"""Accesos Directos — launcher de archivos y carpetas frecuentes.

Reescritura con PySide6 (Qt) para conseguir un acabado visual moderno
(esquinas redondeadas, sombras, animaciones suaves) que Tkinter no puede
dar de forma nativa. La lógica de datos (app_config.py, github_updates.py,
win_icons.py) no cambia: esta capa solo se encarga de la interfaz.

FASE 1 de la reescritura: ventana principal, tema visual, cuadrícula de
tarjetas con carpetas, añadir/abrir accesos. Selección múltiple, arrastrar
y soltar, categorías agrupadas, papelera, configuración y actualizaciones
llegan en las fases siguientes.
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QDrag, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QRubberBand,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import win_icons
from app_config import (
    DEFAULT_PATH_DISPLAY,
    DEFAULT_SIZE,
    GRID_SNAP_STEP,
    MAX_TILE_HEIGHT,
    MAX_TILE_WIDTH,
    MIN_TILE_HEIGHT,
    MIN_TILE_WIDTH,
    PATH_DISPLAY_MODES,
    SIZE_PRESETS,
    SORT_MODES,
    THEMES,
    USER_DATA_DIR,
    expand_path,
    favorite_items,
    get_app_version,
    get_theme,
    is_startup_enabled,
    load_settings,
    load_shortcuts,
    load_trash,
    most_used_items,
    new_item_id,
    portabilize_path,
    purge_old_trash,
    record_item_opened,
    resolve_lnk_target,
    save_settings,
    save_shortcuts,
    save_trash,
    set_startup_enabled,
    snap_dimension,
    startup_supported,
    TRASH_RETENTION_DAYS,
)
from github_updates import (
    UpdateError,
    UpdateInfo,
    apply_update,
    check_for_updates,
    cleanup_stale_update_files,
    restart_with_update,
)

# Tipo MIME propio para arrastrar tarjetas dentro de la app (reordenar,
# mover a una carpeta). Los archivos arrastrados desde el Explorador usan
# el tipo estándar "text/uri-list", que Qt ya entiende solo.
MIME_ITEM_IDS = "application/x-accesos-directos-ids"


def open_path(path: str) -> None:
    expanded = expand_path(path)
    target = Path(expanded).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"No se encontró: {target}")

    if sys.platform == "win32":
        os.startfile(str(target))  # noqa: S606
    elif sys.platform == "darwin":
        os.system(f'open "{target}"')  # noqa: S605
    else:
        os.system(f'xdg-open "{target}"')  # noqa: S605


def pil_to_pixmap(image) -> QPixmap:
    """Convierte una imagen PIL (RGBA) en un QPixmap, pasando por PNG en
    memoria: es el camino más fiable entre Pillow y Qt, sin depender de
    que PIL.ImageQt detecte bien el binding de Qt instalado."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return pixmap


def color_swatch_icon(hex_color: str, size: int = 12) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 3, 3)
    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# FlowLayout: coloca los widgets hijos en filas, pasando a la siguiente en
# cuanto se acaba el ancho disponible — el equivalente Qt del "wrap" que en
# la versión Tkinter había que recalcular a mano en cada redimensionado.
# Receta estándar de Qt (Qt reajusta solo, sin temporizadores ni parpadeos).
# ---------------------------------------------------------------------------


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, spacing: int = 12) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(width, apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect.width(), apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, width: int, apply: bool) -> int:
        margins = self.contentsMargins()
        x = margins.left()
        y = margins.top()
        line_height = 0
        spacing = self.spacing()
        effective_width = max(width - margins.left() - margins.right(), 1)

        for item in self._items:
            item_size = item.sizeHint()
            next_x = x + item_size.width()
            if next_x - margins.left() > effective_width and line_height > 0:
                x = margins.left()
                y += line_height + spacing
                next_x = x + item_size.width()
                line_height = 0
            if apply:
                item.setGeometry(QRect(x, y, item_size.width(), item_size.height()))
            x = next_x + spacing
            line_height = max(line_height, item_size.height())

        return y + line_height + margins.bottom()


# ---------------------------------------------------------------------------
# Tarjeta de acceso directo o carpeta
# ---------------------------------------------------------------------------


class _StarLabel(QLabel):
    """QLabel clicable: la estrella de favorito de cada tarjeta. Un
    QLabel normal no tiene señal de clic propia, así que se añade aquí
    lo mínimo necesario sin tocar el resto de la lógica de ratón de
    TileWidget (que vive en el propio QFrame, no en sus hijos)."""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class TileWidget(QFrame):
    context_requested = Signal(dict, object)
    clicked_with_modifiers = Signal(dict, object)
    double_clicked = Signal(dict)
    drag_started = Signal(dict)
    drag_hover = Signal(dict, str)  # (target_item, "before"|"after"|"into"|"")
    drop_received = Signal(dict, list, str)  # (target_item, dragged_ids, side)
    files_dropped_here = Signal(dict, list)  # (target_item, local_paths)
    resize_moved = Signal(dict, int, int)  # (item, new_width, new_height) — mientras se arrastra
    resize_finished = Signal(dict, int, int)  # (item, new_width, new_height) — al soltar
    favorite_toggled = Signal(dict)  # (item) — al pulsar la estrella

    def __init__(
        self, item: dict, colors: dict, sibling_items: list[dict],
        categories: dict | None = None, compact: bool = False, subtitle_override: str | None = None,
        path_display_mode: str = DEFAULT_PATH_DISPLAY,
    ) -> None:
        super().__init__()
        self.item = item
        self.colors = colors
        self.compact = compact
        self.subtitle_override = subtitle_override
        self._path_display_mode = (
            path_display_mode if path_display_mode in PATH_DISPLAY_MODES else DEFAULT_PATH_DISPLAY
        )
        self._is_favorite = bool(item.get("favorite", False))
        self._hovering = False
        self._subtitle_label: QLabel | None = None
        self._favorite_label: _StarLabel | None = None
        categories = categories or {}
        preset = SIZE_PRESETS.get(item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])
        # Un tamaño personalizado (arrastrando la esquina, ver _resize_handle_rect)
        # manda sobre el preset pequeño/mediano/grande.
        tile_width = item.get("width") or preset["width"]
        tile_height = item.get("height") or preset["height"]

        self.setObjectName("tile")
        if compact:
            self.setFixedSize(max(preset["width"], 220), 40)
        else:
            self.setFixedSize(tile_width, tile_height)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setAcceptDrops(True)
        self._selected = False
        self._press_pos: QPoint | None = None
        self._dragging = False
        # -- redimensionado a mano (estilo Power BI / Canva), solo en modo
        # tarjeta: se detecta la zona de agarre (esquina inferior derecha)
        # a mano en los eventos de ratón, sin crear un widget hijo aparte,
        # para no interferir con el resto de la lógica de clic/arrastre.
        self._resizable = not compact
        self._resize_grip = 14
        self._resizing = False
        self._resize_start_pos: QPoint | None = None
        self._resize_start_size: QSize | None = None

        is_broken = bool(
            item["type"] == "shortcut"
            and item.get("path")
            and not Path(expand_path(item["path"])).expanduser().exists()
        )
        self._is_broken = is_broken

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        category_color = categories.get(item.get("category") or "")
        if category_color:
            stripe = QFrame()
            stripe.setFixedHeight(4)
            stripe.setStyleSheet(f"background: {category_color}; border: none;")
            outer.addWidget(stripe)

        content = QWidget()
        outer.addWidget(content, stretch=1)
        icon_px = 22 if compact else (40 if preset["width"] >= 200 else (32 if preset["width"] >= 150 else 26))
        pixmap = self._load_icon(sibling_items, icon_px)

        # La sombra se crea siempre (antes solo se creaba en modo tarjeta,
        # y como enterEvent/leaveEvent la usan sin comprobar, pasar el
        # ratón por una fila en modo compacto petaba con AttributeError).
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(10 if compact else 18)
        self._shadow.setOffset(0, 2 if compact else 3)
        self._shadow.setColor(QColor(0, 0, 0, 70 if compact else 90))
        self.setGraphicsEffect(self._shadow)
        self._shadow_anim = QPropertyAnimation(self._shadow, b"blurRadius")
        self._shadow_anim.setDuration(150)
        self._shadow_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_blur = 16 if compact else 28
        self._base_blur = 10 if compact else 18

        if compact:
            layout = QHBoxLayout(content)
            layout.setContentsMargins(10, 4, 10, 4)
            layout.setSpacing(8)

            icon_label = QLabel()
            icon_label.setFixedSize(icon_px + 4, icon_px + 4)
            icon_label.setAlignment(Qt.AlignCenter)
            if pixmap is not None:
                icon_label.setPixmap(pixmap)
            else:
                icon_label.setText("⚠️" if is_broken else ("📁" if item["type"] == "folder" else "📄"))
                icon_label.setFont(QFont("Segoe UI Emoji", 12))
            layout.addWidget(icon_label)

            name_label = QLabel(item["name"])
            name_label.setObjectName("tileName")
            name_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            layout.addWidget(name_label, stretch=1)
            self._build_favorite_star(size=14)
            self._update_favorite_visual()
            return

        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignHCenter)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedHeight(36)
        if pixmap is not None:
            icon_label.setPixmap(pixmap)
        else:
            icon_label.setText("⚠️" if is_broken else ("📁" if item["type"] == "folder" else "📄"))
            icon_label.setFont(QFont("Segoe UI Emoji", 20))
        layout.addWidget(icon_label)

        name_label = QLabel(item["name"])
        name_label.setObjectName("tileName")
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        # Se crea siempre que haga falta poder revelarla más tarde (por
        # ejemplo cuando el modo es "solo si está seleccionado"), aunque
        # empiece vacía/oculta — así no hay que reconstruir la tarjeta al
        # seleccionarla, basta con cambiar el texto de esta misma etiqueta.
        needs_subtitle_widget = (
            self.subtitle_override is not None
            or self.item["type"] == "folder"
            or self._is_broken
            or self._path_display_mode in ("always", "on_select")
        )
        if needs_subtitle_widget:
            subtitle_text = self._subtitle(sibling_items)
            subtitle_label = QLabel(subtitle_text)
            subtitle_label.setObjectName("tileSubtitle")
            subtitle_label.setAlignment(Qt.AlignCenter)
            subtitle_label.setWordWrap(True)
            subtitle_label.setVisible(bool(subtitle_text))
            layout.addWidget(subtitle_label)
            self._subtitle_label = subtitle_label

        layout.addStretch()

        self._grip_label = QLabel(self)
        self._grip_label.setText("⋰")
        self._grip_label.setObjectName("resizeGrip")
        self._grip_label.setAlignment(Qt.AlignCenter)
        self._grip_label.setFixedSize(self._resize_grip, self._resize_grip)
        self._grip_label.setCursor(QCursor(Qt.SizeFDiagCursor))
        self._grip_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._position_grip()

        self._build_favorite_star(size=18)
        self._update_favorite_visual()

    def _position_grip(self) -> None:
        if getattr(self, "_grip_label", None) is None:
            return
        self._grip_label.move(
            self.width() - self._resize_grip - 2, self.height() - self._resize_grip - 2
        )

    def _build_favorite_star(self, size: int) -> None:
        """Estrellita flotante en la esquina superior derecha de la
        tarjeta para marcar/desmarcar favoritos. Es hija directa de la
        tarjeta (no del layout de contenido) para poder posicionarla a
        mano encima de todo, igual que `_grip_label`."""
        self._favorite_size = size
        star = _StarLabel(self)
        star.setObjectName("favoriteStar")
        star.setAlignment(Qt.AlignCenter)
        star.setFixedSize(size, size)
        star.setCursor(QCursor(Qt.PointingHandCursor))
        star.setToolTip("Quitar de favoritos" if self._is_favorite else "Marcar como favorito")
        star.clicked.connect(self._on_favorite_clicked)
        self._favorite_label = star
        self._position_favorite_star()

    def _position_favorite_star(self) -> None:
        if self._favorite_label is None:
            return
        size = self._favorite_size
        self._favorite_label.move(self.width() - size - 4, 4)

    def _on_favorite_clicked(self) -> None:
        self._is_favorite = not self._is_favorite
        self.item["favorite"] = self._is_favorite
        self._update_favorite_visual()
        self.favorite_toggled.emit(self.item)

    def _update_favorite_visual(self) -> None:
        if self._favorite_label is None:
            return
        self._favorite_label.setText("★" if self._is_favorite else "☆")
        self._favorite_label.setToolTip(
            "Quitar de favoritos" if self._is_favorite else "Marcar como favorito"
        )
        self._favorite_label.setProperty("active", "true" if self._is_favorite else "false")
        self._favorite_label.style().unpolish(self._favorite_label)
        self._favorite_label.style().polish(self._favorite_label)
        visible = self._is_favorite or self._hovering or self._selected
        self._favorite_label.setVisible(visible)

    def resizeEvent(self, event) -> None:
        self._position_grip()
        self._position_favorite_star()
        super().resizeEvent(event)

    def _in_resize_zone(self, pos: QPoint) -> bool:
        if not self._resizable:
            return False
        zone = QRect(
            self.width() - self._resize_grip - 4, self.height() - self._resize_grip - 4,
            self._resize_grip + 4, self._resize_grip + 4,
        )
        return zone.contains(pos)

    def _load_icon(self, sibling_items: list[dict], icon_px: int):
        if self.item["type"] == "folder":
            children = [it for it in sibling_items if it["parent_id"] == self.item["id"]]
            preview_paths = [c.get("path") for c in children if c["type"] == "shortcut"][:3]
            if not preview_paths:
                return None
            image = win_icons.compose_folder_icon(preview_paths, size=icon_px)
            return pil_to_pixmap(image) if image is not None else None

        if self._is_broken:
            return None
        image = win_icons.get_icon_image(expand_path(self.item.get("path", "")), icon_px)
        return pil_to_pixmap(image) if image is not None else None

    def _path_text(self) -> str:
        """Ruta original ya acortada (con `~` para la carpeta personal),
        sin tener en cuenta el modo de visualización elegido en ajustes."""
        home = str(Path.home())
        path = self.item.get("path", "")
        return "~" + path[len(home):] if path.startswith(home) else path

    def _subtitle(self, sibling_items: list[dict]) -> str:
        if self.subtitle_override is not None:
            return self.subtitle_override
        if self.item["type"] == "folder":
            count = sum(1 for it in sibling_items if it["parent_id"] == self.item["id"])
            return f"{count} elemento" + ("" if count == 1 else "s")
        if self._is_broken:
            return "⚠ No se encuentra"
        if self._path_display_mode == "never":
            return ""
        if self._path_display_mode == "on_select":
            return self._path_text() if self._selected else ""
        return self._path_text()

    def set_selected(self, value: bool) -> None:
        if self._selected == value:
            return
        self._selected = value
        self.setProperty("selected", "true" if value else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self._update_favorite_visual()
        # Modo "solo si está seleccionado": la ruta se revela/oculta al
        # (des)seleccionar la tarjeta, sin reconstruirla entera.
        if (
            self._subtitle_label is not None
            and self.subtitle_override is None
            and self.item["type"] != "folder"
            and not self._is_broken
            and self._path_display_mode == "on_select"
        ):
            text = self._path_text() if value else ""
            self._subtitle_label.setText(text)
            self._subtitle_label.setVisible(bool(text))

    def set_drop_highlight(self, value: bool) -> None:
        self.setProperty("dropTarget", "true" if value else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event) -> None:
        self._hovering = True
        self._update_favorite_visual()
        self._shadow_anim.stop()
        self._shadow_anim.setStartValue(self._shadow.blurRadius())
        self._shadow_anim.setEndValue(self._hover_blur)
        self._shadow_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovering = False
        self._update_favorite_visual()
        self._shadow_anim.stop()
        self._shadow_anim.setStartValue(self._shadow.blurRadius())
        self._shadow_anim.setEndValue(self._base_blur)
        self._shadow_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if self._in_resize_zone(pos):
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
                event.accept()
                return
            self._press_pos = pos
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing and self._resize_start_pos is not None:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            new_w = max(MIN_TILE_WIDTH, min(MAX_TILE_WIDTH, self._resize_start_size.width() + delta.x()))
            new_h = max(MIN_TILE_HEIGHT, min(MAX_TILE_HEIGHT, self._resize_start_size.height() + delta.y()))
            self.setFixedSize(new_w, new_h)
            self.resize_moved.emit(self.item, new_w, new_h)
            event.accept()
            return
        if (
            self._press_pos is not None
            and (event.buttons() & Qt.LeftButton)
            and not self._dragging
        ):
            moved = (event.position().toPoint() - self._press_pos).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._dragging = True
                self.drag_started.emit(self.item)
                self._press_pos = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            self._resizing = False
            self._resize_start_pos = None
            self.resize_finished.emit(self.item, self.width(), self.height())
            event.accept()
            return
        was_dragging = self._dragging
        self._dragging = False
        inside = self.rect().contains(event.position().toPoint())
        self._press_pos = None
        if event.button() == Qt.LeftButton and inside and not was_dragging:
            self.clicked_with_modifiers.emit(self.item, event.modifiers())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.item)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.item, event.globalPos())

    # -- soltar (reordenar / mover a carpeta / arrastrar desde el Explorador) --

    def _drop_zone(self, x: int) -> str:
        """"before"/"after" (insertar junto a esta tarjeta) o "into" (meter
        dentro, solo si es una carpeta y el cursor está en su zona central:
        el 20% de cada borde sigue sirviendo para reordenar la propia
        carpeta entre sus hermanas, en vez de forzar siempre "meter dentro")."""
        ratio = x / max(self.width(), 1)
        if self.item["type"] == "folder" and 0.2 <= ratio <= 0.8:
            return "into"
        return "before" if ratio < 0.5 else "after"

    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(MIME_ITEM_IDS) or md.hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        md = event.mimeData()
        x = event.position().toPoint().x()
        if md.hasFormat(MIME_ITEM_IDS):
            self.drag_hover.emit(self.item, self._drop_zone(x))
        elif md.hasUrls():
            self.drag_hover.emit(self.item, "into" if self.item["type"] == "folder" else "after")
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.drag_hover.emit(self.item, "")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        self.drag_hover.emit(self.item, "")
        if md.hasFormat(MIME_ITEM_IDS):
            try:
                ids = json.loads(bytes(md.data(MIME_ITEM_IDS)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            if self.item["id"] in ids:
                return
            side = self._drop_zone(event.position().toPoint().x())
            self.drop_received.emit(self.item, ids, side)
            event.acceptProposedAction()
        elif md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self.files_dropped_here.emit(self.item, paths)
                event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contenedor de la cuadrícula: fondo donde se dibuja la selección por
# arrastre (marquee) y donde caen las tarjetas/archivos soltados fuera de
# cualquier tarjeta concreta (es decir, "al final" de la carpeta actual).
# ---------------------------------------------------------------------------


class GridContainer(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self._window = window
        self.setAcceptDrops(True)
        self._rubber_band = QRubberBand(QRubberBand.Rectangle, self)
        self._origin: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.childAt(event.position().toPoint()) is None:
            self._origin = event.position().toPoint()
            self._rubber_band.setGeometry(QRect(self._origin, QSize()))
            self._rubber_band.show()
            self._window.begin_marquee(event.modifiers())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            rect = QRect(self._origin, event.position().toPoint()).normalized()
            self._rubber_band.setGeometry(rect)
            self._window.update_marquee(rect)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._origin is not None:
            self._rubber_band.hide()
            self._origin = None
            self._window.end_marquee()
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        self._window._relayout_sections()
        super().resizeEvent(event)

    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(MIME_ITEM_IDS) or md.hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        self._window.update_drop_indicator(None, "")
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(MIME_ITEM_IDS):
            try:
                ids = json.loads(bytes(md.data(MIME_ITEM_IDS)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            self._window.handle_internal_drop(None, ids, None)
            event.acceptProposedAction()
        elif md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self._window.handle_external_drop(None, paths)
                event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Actualizaciones: la comprobación (HTTP a la API de GitHub) y la descarga
# se hacen en un hilo aparte para no congelar la ventana. Las señales de
# QThread se entregan automáticamente en el hilo principal, así que es
# seguro tocar la interfaz desde los slots que las reciben.
# ---------------------------------------------------------------------------


class UpdateCheckWorker(QThread):
    result_ready = Signal(object)  # UpdateInfo | None
    failed = Signal(str)

    def run(self) -> None:
        try:
            info = check_for_updates()
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de red, mostrarlo igual
            self.failed.emit(str(exc))
        else:
            self.result_ready.emit(info)


class UpdateInstallWorker(QThread):
    progress = Signal(int, int)  # (bytes descargados, bytes totales)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, update: UpdateInfo) -> None:
        super().__init__()
        self.update = update

    def run(self) -> None:
        try:
            apply_update(
                self.update.download_url,
                latest_version=self.update.latest_version,
                progress_callback=lambda done, total: self.progress.emit(done, total),
            )
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit()


class UpdateProgressDialog(QDialog):
    def __init__(self, parent, colors: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Actualizando")
        self.setModal(True)
        self.setFixedSize(360, 130)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        self.status_label = QLabel("Descargando actualización...")
        layout.addWidget(self.status_label)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)

        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet(f"color: {colors['text_muted']}; font-size: 10px;")
        layout.addWidget(self.eta_label)

        self._start_time = time.time()

    def update_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = int(downloaded * 100 / total)
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
            elapsed = time.time() - self._start_time
            speed = downloaded / elapsed if elapsed > 0 else 0
            remaining_bytes = max(total - downloaded, 0)
            eta_seconds = remaining_bytes / speed if speed > 0 else 0
            mb_done = downloaded / 1_048_576
            mb_total = total / 1_048_576
            self.eta_label.setText(
                f"{mb_done:.1f} / {mb_total:.1f} MB — {self._format_eta(eta_seconds)} restante"
            )
        else:
            # El servidor no mandó Content-Length: barra indeterminada.
            self.bar.setRange(0, 0)
            mb_done = downloaded / 1_048_576
            self.eta_label.setText(f"{mb_done:.1f} MB descargados")

    @staticmethod
    def _format_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}m {secs}s"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.colors = get_theme(self.settings.get("theme"))
        self.shortcuts = load_shortcuts()
        self.current_folder_id: str | None = None
        self.breadcrumb: list[tuple[str, str | None]] = [("Inicio", None)]
        self.category_filter: str | None = None  # None=todas, ""=sin categoría, o el nombre
        self.search_query: str = ""

        # Purga la papelera de lo que ya lleve más de la cuenta ahí dentro.
        trash = load_trash()
        trash, changed = purge_old_trash(trash)
        if changed:
            save_trash(trash)

        self.selected_ids: set[str] = set()
        self._last_clicked_id: str | None = None
        self._marquee_base_selection: set[str] | None = None
        self._tiles: list[TileWidget] = []
        self._tile_by_id: dict[str, TileWidget] = {}
        self._flow_sections: list = []
        self._focus_index: int = -1

        # Deshacer/rehacer (Ctrl+Z / Ctrl+Shift+Z): pila de instantáneas de
        # accesos+papelera, en memoria (no persiste entre ejecuciones).
        self._undo_stack: list[tuple[str, list, list]] = []
        self._redo_stack: list[tuple[str, list, list]] = []
        self._UNDO_LIMIT = 30

        # Portapapeles interno (Ctrl+C / Ctrl+V): copia de la selección
        # actual (con sus subcarpetas), lista para duplicarse en Ctrl+V.
        self._clipboard: dict | None = None

        self.setWindowTitle("Accesos Directos")
        self.resize(820, 600)
        self.setMinimumSize(480, 360)
        self.setFocusPolicy(Qt.StrongFocus)
        geometry = self.settings.get("window_geometry", "")
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
            except Exception:
                pass

        self._build_ui()
        self._apply_theme()
        self.refresh()

        # -- actualizaciones --------------------------------------------
        cleanup_stale_update_files()
        self._update_check_thread: UpdateCheckWorker | None = None
        self._update_install_thread: UpdateInstallWorker | None = None
        self._update_progress_dialog: UpdateProgressDialog | None = None
        self._pending_update: UpdateInfo | None = None
        self.update_icon.mousePressEvent = lambda _e: self.check_updates_dialog(manual=True)
        if self.settings.get("auto_check_updates", True):
            QTimer.singleShot(2500, lambda: self.check_updates_dialog(manual=False))
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(lambda: self.check_updates_dialog(manual=False))
        self._update_timer.start(15 * 60 * 1000)

    def closeEvent(self, event) -> None:
        try:
            geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.settings["window_geometry"] = geometry
            save_settings(self.settings)
        except Exception:
            pass
        super().closeEvent(event)

    # -- construcción --------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(24, 20, 24, 16)
        root_layout.setSpacing(4)

        # -- cabecera --
        header_row = QHBoxLayout()
        title_label = QLabel("Accesos Directos")
        title_label.setObjectName("title")
        header_row.addWidget(title_label)

        version_label = QLabel(f"v{get_app_version()}")
        version_label.setObjectName("version")
        header_row.addWidget(version_label)
        header_row.addStretch()

        self.update_icon = QLabel("🔄")
        self.update_icon.setObjectName("updateIcon")
        self.update_icon.setCursor(QCursor(Qt.PointingHandCursor))
        self.update_icon.setToolTip("Buscar actualizaciones")
        header_row.addWidget(self.update_icon)
        root_layout.addLayout(header_row)

        self.breadcrumb_row = QWidget()
        self._breadcrumb_layout = QHBoxLayout(self.breadcrumb_row)
        self._breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        self._breadcrumb_layout.setSpacing(4)
        root_layout.addWidget(self.breadcrumb_row)

        self.search_status_label = QLabel()
        self.search_status_label.setObjectName("breadcrumb")
        self.search_status_label.hide()
        root_layout.addWidget(self.search_status_label)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        root_layout.addWidget(divider)
        root_layout.addSpacing(12)

        # -- barra de herramientas (fila 1: acciones + búsqueda) --
        toolbar_row = QHBoxLayout()
        add_button = QPushButton("+  Añadir")
        add_button.setObjectName("accentButton")
        add_button.setCursor(QCursor(Qt.PointingHandCursor))
        add_button.clicked.connect(self.add_shortcut_dialog)
        toolbar_row.addWidget(add_button)

        back_button = QPushButton("←  Atrás")
        back_button.setObjectName("ghostButton")
        back_button.setCursor(QCursor(Qt.PointingHandCursor))
        back_button.clicked.connect(self.go_back)
        toolbar_row.addWidget(back_button)
        self.back_button = back_button

        toolbar_row.addStretch()

        self.search_box = QLineEdit()
        self.search_box.setObjectName("searchBox")
        self.search_box.setPlaceholderText("🔍  Buscar accesos...  (Ctrl+F)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(240)
        self.search_box.textChanged.connect(self._on_search_changed)
        toolbar_row.addWidget(self.search_box, stretch=1)

        root_layout.addLayout(toolbar_row)
        root_layout.addSpacing(8)

        # -- barra de herramientas (fila 2: orden / vista / categorías...) --
        toolbar_row2 = QHBoxLayout()

        self.sort_button = QPushButton()
        self.sort_button.setObjectName("ghostButton")
        self.sort_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.sort_button.clicked.connect(self.show_sort_menu)
        toolbar_row2.addWidget(self.sort_button)

        self.view_button = QPushButton()
        self.view_button.setObjectName("ghostButton")
        self.view_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.view_button.clicked.connect(self.toggle_view_style)
        toolbar_row2.addWidget(self.view_button)

        self.category_button = QPushButton("🏷  Categorías")
        self.category_button.setObjectName("ghostButton")
        self.category_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.category_button.clicked.connect(self.show_category_menu)
        toolbar_row2.addWidget(self.category_button)

        stats_button = QPushButton("📊  Más usados")
        stats_button.setObjectName("ghostButton")
        stats_button.setCursor(QCursor(Qt.PointingHandCursor))
        stats_button.setToolTip("Estadísticas de uso")
        stats_button.clicked.connect(self.open_usage_stats_dialog)
        toolbar_row2.addWidget(stats_button)

        favorites_button = QPushButton("⭐  Favoritos")
        favorites_button.setObjectName("ghostButton")
        favorites_button.setCursor(QCursor(Qt.PointingHandCursor))
        favorites_button.setToolTip("Accesos y carpetas marcados como favoritos")
        favorites_button.clicked.connect(self.open_favorites_dialog)
        toolbar_row2.addWidget(favorites_button)

        toolbar_row2.addStretch()

        trash_button = QPushButton("🗑")
        trash_button.setObjectName("ghostButton")
        trash_button.setCursor(QCursor(Qt.PointingHandCursor))
        trash_button.setToolTip("Papelera")
        trash_button.clicked.connect(self.open_trash_dialog)
        toolbar_row2.addWidget(trash_button)

        settings_button = QPushButton("⚙")
        settings_button.setObjectName("ghostButton")
        settings_button.setCursor(QCursor(Qt.PointingHandCursor))
        settings_button.setToolTip("Configuración")
        settings_button.clicked.connect(self.open_settings_dialog)
        toolbar_row2.addWidget(settings_button)

        root_layout.addLayout(toolbar_row2)
        root_layout.addSpacing(10)
        self._update_sort_button_text()
        self._update_view_button_text()

        # -- cuadrícula de tarjetas, dentro de un área con scroll --
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("scrollArea")
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.grid_container = GridContainer(self)
        self.grid_container.setObjectName("gridContainer")
        # Un QVBoxLayout de "secciones": normalmente hay una sola sección sin
        # título con todas las tarjetas, pero al agrupar por categoría hay
        # una sección (con encabezado) por categoría, cada una con su propio
        # FlowLayout interno.
        self._sections_layout = QVBoxLayout(self.grid_container)
        self._sections_layout.setContentsMargins(4, 4, 4, 4)
        self._sections_layout.setSpacing(20)

        self.scroll_area.setWidget(self.grid_container)
        root_layout.addWidget(self.scroll_area, stretch=1)

        # Línea de inserción: se muestra/mueve durante el arrastre para
        # dejar claro dónde va a caer la tarjeta, en vez de tener que
        # soltar "a ciegas" para descubrirlo.
        self._drop_indicator = QFrame(self.grid_container)
        self._drop_indicator.setObjectName("dropIndicator")
        self._drop_indicator.hide()
        self._drop_highlight_id: str | None = None

        self.empty_label = QLabel("Esta carpeta está vacía.\nUsa «+ Añadir» para empezar.")
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)

        footer = QLabel(f"Datos guardados en: {USER_DATA_DIR}")
        footer.setObjectName("footer")
        footer.setWordWrap(True)
        root_layout.addWidget(footer)

        # Aviso flotante ("toast") que se desvanece solo, por ejemplo al
        # marcar/desmarcar un favorito. Flota encima de todo lo demás
        # (hijo directo de `central`, no de ningún layout), y se
        # reposiciona a mano cada vez que se muestra.
        self._toast_label = QLabel("", central)
        self._toast_label.setObjectName("toastLabel")
        self._toast_label.setAlignment(Qt.AlignCenter)
        self._toast_label.hide()
        self._toast_opacity = QGraphicsOpacityEffect(self._toast_label)
        self._toast_opacity.setOpacity(1.0)
        self._toast_label.setGraphicsEffect(self._toast_opacity)
        self._toast_anim = QPropertyAnimation(self._toast_opacity, b"opacity")
        self._toast_anim.finished.connect(self._toast_label.hide)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._fade_out_toast)

    def _show_toast(self, text: str) -> None:
        self._toast_timer.stop()
        self._toast_anim.stop()
        self._toast_label.setText(text)
        self._toast_label.adjustSize()
        central = self.centralWidget()
        x = (central.width() - self._toast_label.width()) // 2
        y = central.height() - self._toast_label.height() - 24
        self._toast_label.move(max(0, x), max(0, y))
        self._toast_opacity.setOpacity(1.0)
        self._toast_label.show()
        self._toast_label.raise_()
        self._toast_timer.start(1800)

    def _fade_out_toast(self) -> None:
        self._toast_anim.stop()
        self._toast_anim.setDuration(500)
        self._toast_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._toast_anim.setStartValue(1.0)
        self._toast_anim.setEndValue(0.0)
        self._toast_anim.start()

    def _apply_theme(self) -> None:
        c = self.colors
        self.setStyleSheet(f"""
            QWidget#central {{ background: {c['bg']}; }}
            QScrollArea#scrollArea {{ background: transparent; border: none; }}
            QWidget#gridContainer {{ background: transparent; }}
            QLabel#title {{
                color: {c['accent']}; font-size: 22px; font-weight: 700;
                font-family: 'Segoe UI'; margin-right: 8px;
            }}
            QLabel#version {{ color: {c['text_muted']}; font-size: 11px; }}
            QLabel#updateIcon {{ font-size: 18px; padding: 4px; }}
            QLabel#updateIcon:hover {{ color: {c['accent']}; }}
            QLabel#breadcrumb {{ color: {c['text_muted']}; font-size: 12px; margin-top: 4px; }}
            QLabel#breadcrumbSep {{ color: {c['text_muted']}; font-size: 12px; }}
            QPushButton#breadcrumbLink {{
                color: {c['text_muted']}; font-size: 12px; border: none; background: transparent;
                padding: 0px; text-decoration: underline;
            }}
            QPushButton#breadcrumbLink:hover {{ color: {c['accent']}; }}
            QPushButton#breadcrumbLast {{
                color: {c['text']}; font-size: 12px; font-weight: 600; border: none;
                background: transparent; padding: 0px;
            }}
            QFrame#divider {{ background: {c['surface']}; border: none; }}
            QLabel#emptyLabel {{ color: {c['text_muted']}; font-size: 13px; }}
            QLabel#footer {{ color: {c['text_muted']}; font-size: 10px; margin-top: 6px; }}

            QPushButton#accentButton {{
                background: {c['accent']}; color: {c['bg']}; font-weight: 700;
                border: none; border-radius: 10px; padding: 10px 18px; font-size: 13px;
            }}
            QPushButton#accentButton:pressed {{ padding-top: 11px; padding-bottom: 9px; }}

            QPushButton#ghostButton {{
                background: transparent; color: {c['text']}; border: 1px solid {c['surface_hover']};
                border-radius: 10px; padding: 9px 16px; font-size: 12px; margin-left: 8px;
            }}
            QPushButton#ghostButton:hover {{ background: {c['surface']}; }}
            QPushButton#ghostButton:disabled {{ color: {c['text_muted']}; border-color: {c['surface']}; }}

            QLineEdit#searchBox {{
                background: {c['surface']}; color: {c['text']}; border: 1px solid {c['surface_hover']};
                border-radius: 10px; padding: 8px 10px; font-size: 12px;
            }}
            QLineEdit#searchBox:focus {{ border: 1px solid {c['accent']}; }}

            QFrame#tile {{
                background: {c['surface']}; border-radius: 16px; border: 1px solid {c['surface']};
            }}
            QFrame#tile:hover {{ background: {c['surface_hover']}; border: 1px solid {c['accent']}; }}
            QFrame#tile[selected="true"] {{ border: 2px solid {c['accent']}; background: {c['surface_hover']}; }}
            QFrame#tile[dropTarget="true"] {{ border: 2px solid {c['accent']}; background: {c['surface_hover']}; }}
            QFrame#dropIndicator {{ background: {c['accent']}; border-radius: 2px; }}
            QLabel#resizeGrip {{
                color: {c['text_muted']}; font-size: 13px; background: transparent;
            }}
            QLabel#tileName {{ color: {c['text']}; font-size: 12px; font-weight: 600; font-family: 'Segoe UI'; }}
            QLabel#tileSubtitle {{ color: {c['text_muted']}; font-size: 9px; }}
            QLabel#sectionHeader {{
                color: {c['text_muted']}; font-size: 11px; font-weight: 700; padding-bottom: 2px;
            }}
            QLabel#favoriteStar {{
                color: {c['text_muted']}; font-size: 13px; background: transparent;
            }}
            QLabel#favoriteStar[active="true"] {{ color: #f5c518; }}
            QPushButton#favoriteStarButton {{
                color: #f5c518; background: transparent; border: none;
                font-size: 14px; padding: 4px 8px;
            }}
            QPushButton#favoriteStarButton:hover {{ background: {c['surface_hover']}; border-radius: 6px; }}
            QLabel#toastLabel {{
                background: {c['accent']}; color: {c['bg']}; font-weight: 700;
                font-size: 12px; border-radius: 10px; padding: 8px 16px;
            }}

            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {c['surface_hover']}; border-radius: 5px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    # -- datos / navegación ----------------------------------------------

    def _sort_items(self, items: list[dict]) -> list[dict]:
        mode = self.settings.get("sort_mode", "manual")
        if mode == "name_asc":
            return sorted(items, key=lambda it: it["name"].casefold())
        if mode == "name_desc":
            return sorted(items, key=lambda it: it["name"].casefold(), reverse=True)
        if mode == "folders_first":
            return sorted(
                items, key=lambda it: (0 if it["type"] == "folder" else 1, it.get("order", 0))
            )
        return sorted(items, key=lambda it: it.get("order", 0))

    def _visible_items(self) -> list[dict]:
        items = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        if self.category_filter is not None:
            if self.category_filter == "":
                items = [it for it in items if not it.get("category")]
            else:
                items = [it for it in items if it.get("category") == self.category_filter]
        return self._sort_items(items)

    def _grouped_items(self) -> list[tuple[str, list[dict]]]:
        base = self._sort_items(
            [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        )
        groups: list[tuple[str, list[dict]]] = []
        for name in self.settings.get("categories", {}):
            group = [it for it in base if it.get("category") == name]
            if group:
                groups.append((name, group))
        uncategorized = [it for it in base if not it.get("category")]
        if uncategorized:
            groups.append(("Sin categoría", uncategorized))
        return groups

    def _render_breadcrumb(self) -> None:
        # Ruta de migas de pan clicable en cualquier punto: cada tramo es
        # un botón (menos el último, la carpeta actual) que salta
        # directamente a ese nivel, sin tener que pulsar "Atrás" varias
        # veces uno a uno.
        while self._breadcrumb_layout.count():
            layout_item = self._breadcrumb_layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()

        for i, (name, _folder_id) in enumerate(self.breadcrumb):
            if i > 0:
                sep = QLabel("›")
                sep.setObjectName("breadcrumbSep")
                self._breadcrumb_layout.addWidget(sep)
            is_last = i == len(self.breadcrumb) - 1
            btn = QPushButton(name)
            btn.setObjectName("breadcrumbLast" if is_last else "breadcrumbLink")
            btn.setFlat(True)
            btn.setCursor(QCursor(Qt.ArrowCursor if is_last else Qt.PointingHandCursor))
            btn.setEnabled(not is_last)
            btn.clicked.connect(lambda _checked=False, idx=i: self._jump_to_breadcrumb(idx))
            self._breadcrumb_layout.addWidget(btn)
        self._breadcrumb_layout.addStretch(1)
        self.back_button.setEnabled(self.current_folder_id is not None)

    def _jump_to_breadcrumb(self, index: int) -> None:
        if index >= len(self.breadcrumb) - 1:
            return
        self.breadcrumb = self.breadcrumb[: index + 1]
        self.current_folder_id = self.breadcrumb[-1][1]
        self.selected_ids = set()
        self.category_filter = None
        if self.search_query:
            self.search_query = ""
            self.search_box.blockSignals(True)
            self.search_box.clear()
            self.search_box.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        while self._sections_layout.count():
            layout_item = self._sections_layout.takeAt(0)
            if layout_item.widget() is not None:
                layout_item.widget().hide()
                layout_item.widget().deleteLater()

        self._drop_indicator.hide()
        self._drop_highlight_id = None
        self._tiles = []
        self._tile_by_id = {}
        self._flow_sections = []
        compact = self.settings.get("card_style") == "compact"

        if self.search_query:
            self.breadcrumb_row.hide()
            self.search_status_label.show()
            self.back_button.setEnabled(False)
            results = self._search_results()
            self.search_status_label.setText(
                f"🔍 {len(results)} resultado(s) para «{self.search_query}»"
            )
            if not results:
                self._sections_layout.addWidget(self.empty_label)
            else:
                path_map = {it["id"]: self._path_to_item(it) for it in results}
                self._add_section(None, results, compact, path_map=path_map)
            self._sections_layout.addStretch(1)
            self._relayout_sections()
            return

        self.search_status_label.hide()
        self.breadcrumb_row.show()
        self._render_breadcrumb()

        grouped = bool(self.settings.get("group_by_category")) and self.category_filter is None

        if grouped:
            groups = self._grouped_items()
            all_ids = {it["id"] for _, items in groups for it in items}
            self.selected_ids &= all_ids
            if not groups:
                self._sections_layout.addWidget(self.empty_label)
            else:
                for label, items in groups:
                    self._add_section(label, items, compact)
        else:
            items = self._visible_items()
            self.selected_ids &= {it["id"] for it in items}
            if not items:
                self._sections_layout.addWidget(self.empty_label)
            else:
                self._add_section(None, items, compact)

        self._sections_layout.addStretch(1)
        self._relayout_sections()

    def _add_section(
        self, label: str | None, items: list[dict], compact: bool, path_map: dict[str, str] | None = None,
    ) -> None:
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        if label is not None:
            color = self.settings.get("categories", {}).get(label)
            header = QLabel(f"{label}   ·   {len(items)}")
            header.setObjectName("sectionHeader")
            if color:
                header.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 11px;")
            section_layout.addWidget(header)

        flow_widget = QWidget()
        flow = FlowLayout(flow_widget, margin=0, spacing=14)
        flow_widget.setLayout(flow)
        section_layout.addWidget(flow_widget)

        categories = self.settings.get("categories", {})
        path_display_mode = self.settings.get("path_display", DEFAULT_PATH_DISPLAY)
        for item in items:
            subtitle_override = path_map.get(item["id"]) if path_map else None
            tile = TileWidget(
                item, self.colors, self.shortcuts, categories=categories, compact=compact,
                subtitle_override=subtitle_override, path_display_mode=path_display_mode,
            )
            tile.clicked_with_modifiers.connect(self.handle_tile_click)
            tile.double_clicked.connect(self.open_item)
            tile.context_requested.connect(self.show_context_menu)
            tile.drag_started.connect(self.begin_drag)
            tile.drag_hover.connect(self.update_drop_indicator)
            tile.drop_received.connect(self.handle_internal_drop)
            tile.files_dropped_here.connect(self.handle_external_drop)
            tile.resize_moved.connect(self.handle_tile_resize_moved)
            tile.resize_finished.connect(self.handle_tile_resize_finished)
            tile.favorite_toggled.connect(self.handle_favorite_toggle)
            tile.set_selected(item["id"] in self.selected_ids)
            flow.addWidget(tile)
            self._tiles.append(tile)
            self._tile_by_id[item["id"]] = tile

        self._sections_layout.addWidget(section)
        self._flow_sections.append((flow_widget, flow))

    def _relayout_sections(self) -> None:
        # Qt no propaga bien "altura según ancho" (heightForWidth) a través
        # de varios niveles de layouts anidados (aquí: secciones dentro de
        # _sections_layout, y dentro de cada sección un FlowLayout propio)
        # — es una limitación conocida de Qt, no un descuido. Por eso se
        # calcula la altura de cada sección a mano y se fija explícitamente
        # en vez de confiar en que el sistema de layouts la adivine sola;
        # si no, las secciones acaban solapándose unas con otras.
        width = self.scroll_area.viewport().width()
        if width <= 0:
            return
        for flow_widget, flow in self._flow_sections:
            needed_height = flow._do_layout(width, apply=False)
            flow_widget.setFixedHeight(needed_height)

    # -- acciones ----------------------------------------------------

    def open_item(self, item: dict) -> None:
        if item["type"] == "folder":
            if self.search_query:
                self.breadcrumb = self._breadcrumb_chain_for(item["id"])
                self.search_query = ""
                self.search_box.blockSignals(True)
                self.search_box.clear()
                self.search_box.blockSignals(False)
            else:
                self.breadcrumb.append((item["name"], item["id"]))
            self.current_folder_id = item["id"]
            self.category_filter = None
            self.selected_ids = set()
            self.refresh()
            return
        try:
            open_path(item["path"])
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Archivo no encontrado", str(exc))
        except OSError as exc:
            QMessageBox.warning(self, "Error al abrir", str(exc))
        else:
            record_item_opened(self.shortcuts, item["id"])
            save_shortcuts(self.shortcuts)

    # -- búsqueda ---------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self.search_query = text.strip().lower()
        self.refresh()

    def _search_results(self) -> list[dict]:
        query = self.search_query
        matches = [it for it in self.shortcuts if query in it["name"].lower()]
        return self._sort_items(matches)

    def _breadcrumb_chain_for(self, folder_id: str | None) -> list[tuple[str, str | None]]:
        by_id = {it["id"]: it for it in self.shortcuts}
        chain: list[tuple[str, str | None]] = []
        current = folder_id
        guard = 0
        while current is not None and guard < 200:
            node = by_id.get(current)
            if node is None:
                break
            chain.append((node["name"], node["id"]))
            current = node["parent_id"]
            guard += 1
        chain.reverse()
        return [("Inicio", None)] + chain

    def _path_to_item(self, item: dict) -> str:
        chain = self._breadcrumb_chain_for(item.get("parent_id"))
        return "En: " + " › ".join(name for name, _ in chain)

    # -- selección ------------------------------------------------------

    def handle_tile_click(self, item: dict, modifiers) -> None:
        ordered_ids = [t.item["id"] for t in self._tiles]
        if modifiers & Qt.ControlModifier:
            if item["id"] in self.selected_ids:
                self.selected_ids.discard(item["id"])
            else:
                self.selected_ids.add(item["id"])
            self._last_clicked_id = item["id"]
            self._refresh_selection_visuals()
        elif modifiers & Qt.ShiftModifier and self._last_clicked_id in ordered_ids:
            i0 = ordered_ids.index(self._last_clicked_id)
            i1 = ordered_ids.index(item["id"])
            lo, hi = sorted((i0, i1))
            self.selected_ids = set(ordered_ids[lo:hi + 1])
            self._refresh_selection_visuals()
        else:
            self.selected_ids = {item["id"]}
            self._last_clicked_id = item["id"]
            self._refresh_selection_visuals()
            if self.settings.get("click_mode", "double") == "single":
                self.open_item(item)

    def _refresh_selection_visuals(self) -> None:
        for tile in self._tiles:
            tile.set_selected(tile.item["id"] in self.selected_ids)

    def begin_marquee(self, modifiers) -> None:
        base = set(self.selected_ids) if (modifiers & Qt.ControlModifier) else set()
        self._marquee_base_selection = base
        if not (modifiers & Qt.ControlModifier):
            self.selected_ids = set()
            self._refresh_selection_visuals()

    def update_marquee(self, rect: QRect) -> None:
        # Las tarjetas pueden estar anidadas dentro de una sección (vista
        # agrupada por categoría), así que su `.geometry()` es relativa a
        # ESA sección, no al contenedor general — hay que traducir cada
        # tarjeta a coordenadas de `grid_container` con mapTo() para que
        # el cálculo de intersección sea correcto pase lo que pase.
        hit = set()
        for tile in self._tiles:
            top_left = tile.mapTo(self.grid_container, QPoint(0, 0))
            tile_rect = QRect(top_left, tile.size())
            if tile_rect.intersects(rect):
                hit.add(tile.item["id"])
        base = self._marquee_base_selection or set()
        self.selected_ids = base | hit
        self._refresh_selection_visuals()

    def end_marquee(self) -> None:
        self._marquee_base_selection = None

    def select_all(self) -> None:
        self.selected_ids = {t.item["id"] for t in self._tiles}
        self._refresh_selection_visuals()

    # -- arrastrar y soltar ----------------------------------------------

    def update_drop_indicator(self, target_item: dict | None, mode: str) -> None:
        if not mode:
            self._drop_indicator.hide()
            self._clear_drop_highlight()
            return

        tile = self._tile_by_id.get(target_item["id"]) if target_item else None
        if tile is None:
            self._drop_indicator.hide()
            self._clear_drop_highlight()
            return

        if mode == "into":
            self._drop_indicator.hide()
            if self._drop_highlight_id != target_item["id"]:
                self._clear_drop_highlight()
                tile.set_drop_highlight(True)
                self._drop_highlight_id = target_item["id"]
            return

        self._clear_drop_highlight()
        top_left = tile.mapTo(self.grid_container, QPoint(0, 0))
        x = top_left.x() - 2 if mode == "before" else top_left.x() + tile.width() - 2
        self._drop_indicator.setGeometry(x, top_left.y(), 4, tile.height())
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _clear_drop_highlight(self) -> None:
        if self._drop_highlight_id is not None:
            tile = self._tile_by_id.get(self._drop_highlight_id)
            if tile is not None:
                tile.set_drop_highlight(False)
            self._drop_highlight_id = None

    def begin_drag(self, item: dict) -> None:
        tile = self._tile_by_id.get(item["id"])
        if tile is None:
            return
        ids = list(self.selected_ids) if item["id"] in self.selected_ids and len(self.selected_ids) > 1 else [item["id"]]

        mime = QMimeData()
        mime.setData(MIME_ITEM_IDS, json.dumps(ids).encode("utf-8"))
        drag = QDrag(tile)
        drag.setMimeData(mime)
        pixmap = tile.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.MoveAction)

    def handle_internal_drop(self, target_item: dict | None, ids: list[str], side: str | None) -> None:
        dragged = [it for it in self.shortcuts if it["id"] in ids]
        if not dragged:
            return
        self.push_undo("mover")

        # Evita mover una carpeta dentro de sí misma o de una de sus
        # propias tarjetas (crearía un bucle imposible de navegar).
        dragged_ids = {it["id"] for it in dragged}
        if target_item is not None and target_item["id"] in dragged_ids:
            return

        if target_item is not None and side == "into" and target_item["type"] == "folder":
            for it in dragged:
                it["parent_id"] = target_item["id"]
            parent_id = target_item["id"]
            siblings = [it for it in self.shortcuts if it["parent_id"] == parent_id and it["id"] not in dragged_ids]
            siblings.sort(key=lambda it: it.get("order", 0))
            siblings.extend(dragged)
        else:
            parent_id = target_item["parent_id"] if target_item is not None else self.current_folder_id
            for it in dragged:
                it["parent_id"] = parent_id
            siblings = [it for it in self.shortcuts if it["parent_id"] == parent_id and it["id"] not in dragged_ids]
            siblings.sort(key=lambda it: it.get("order", 0))
            if target_item is not None and side in ("before", "after"):
                idx = next((i for i, it in enumerate(siblings) if it["id"] == target_item["id"]), len(siblings))
                insert_at = idx if side == "before" else idx + 1
            else:
                insert_at = len(siblings)
            for offset, it in enumerate(dragged):
                siblings.insert(insert_at + offset, it)

        for i, it in enumerate(siblings):
            it["order"] = i

        save_shortcuts(self.shortcuts)
        self.refresh()

    def handle_external_drop(self, target_item: dict | None, paths: list[str]) -> None:
        parent_id = target_item["id"] if (target_item is not None and target_item["type"] == "folder") else self.current_folder_id
        existing_paths = {
            it.get("path") for it in self.shortcuts if it["type"] == "shortcut" and it["parent_id"] == parent_id
        }
        siblings = [it for it in self.shortcuts if it["parent_id"] == parent_id]
        added = 0
        new_entries: list[dict] = []
        for raw_path in paths:
            # Accesos directos de Windows (.lnk): en vez de guardar la ruta
            # al propio .lnk, se intenta resolver el destino REAL al que
            # apunta (por ejemplo el .exe de un programa), que es lo que
            # el usuario espera al arrastrar un acceso directo. Si no se
            # puede resolver (algunos .lnk usan formatos que este parser
            # ligero no cubre), se guarda el .lnk tal cual como respaldo.
            display_name = Path(raw_path).stem if raw_path.lower().endswith(".lnk") else Path(raw_path).name
            resolved = resolve_lnk_target(raw_path) if raw_path.lower().endswith(".lnk") else None
            actual_path = resolved or raw_path

            portable = portabilize_path(actual_path)
            if portable in existing_paths:
                continue
            new_entries.append({
                "id": new_item_id(),
                "type": "shortcut",
                "name": display_name,
                "path": portable,
                "parent_id": parent_id,
                "order": len(siblings) + added,
                "color": None,
                "size": DEFAULT_SIZE,
                "width": None,
                "height": None,
                "category": None,
                "open_count": 0,
                "last_opened": 0.0,
            })
            existing_paths.add(portable)
            added += 1
        if added:
            self.push_undo("añadir")
            self.shortcuts.extend(new_entries)
            save_shortcuts(self.shortcuts)
            self.refresh()

    # -- teclado ----------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        ordered = self._tiles
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        if ctrl and key == Qt.Key_F:
            self.search_box.setFocus()
            self.search_box.selectAll()
        elif ctrl and key == Qt.Key_Z and shift:
            self.redo()
        elif ctrl and key == Qt.Key_Z:
            self.undo()
        elif ctrl and key == Qt.Key_Y:
            self.redo()
        elif ctrl and key == Qt.Key_C and self.selected_ids:
            self.copy_selected()
        elif ctrl and key == Qt.Key_V and self._clipboard:
            self.paste_clipboard()
        elif key == Qt.Key_Escape and self.search_query:
            self.search_box.clear()
        elif key == Qt.Key_Escape:
            self.selected_ids = set()
            self._refresh_selection_visuals()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_ids:
            items = [it for it in self.shortcuts if it["id"] in self.selected_ids]
            self._delete_items(items)
        elif ctrl and key == Qt.Key_A and ordered:
            self.select_all()
        elif key == Qt.Key_Return and len(self.selected_ids) == 1:
            item = next((t.item for t in ordered if t.item["id"] in self.selected_ids), None)
            if item is not None:
                self.open_item(item)
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and ordered:
            ids = [t.item["id"] for t in ordered]
            current = next(iter(self.selected_ids), None)
            idx = ids.index(current) if current in ids else -1
            step = 1 if key in (Qt.Key_Right, Qt.Key_Down) else -1
            new_idx = max(0, min(len(ids) - 1, idx + step)) if idx >= 0 else 0
            self.selected_ids = {ids[new_idx]}
            self._last_clicked_id = ids[new_idx]
            self._refresh_selection_visuals()
            self.scroll_area.ensureWidgetVisible(ordered[new_idx])
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def go_back(self) -> None:
        if len(self.breadcrumb) <= 1:
            return
        self.breadcrumb.pop()
        self.current_folder_id = self.breadcrumb[-1][1]
        self.selected_ids = set()
        self.category_filter = None
        self.refresh()

    def show_context_menu(self, item: dict, global_pos) -> None:
        if item["id"] not in self.selected_ids:
            self.selected_ids = {item["id"]}
            self._last_clicked_id = item["id"]
            self._refresh_selection_visuals()

        selected_items = [it for it in self.shortcuts if it["id"] in self.selected_ids]
        menu = QMenu(self)
        if len(selected_items) <= 1:
            menu.addAction("Abrir", lambda: self.open_item(item))
            menu.addSeparator()
            menu.addAction("Renombrar...", lambda: self.rename_item(item))
        menu.addMenu(self._build_category_assignment_menu(selected_items or [item]))
        menu.addMenu(self._build_size_menu(selected_items or [item]))
        menu.addSeparator()
        menu.addAction("Copiar\tCtrl+C", self.copy_selected)
        if self._clipboard:
            menu.addAction("Pegar\tCtrl+V", self.paste_clipboard)
        menu.addSeparator()
        if len(selected_items) <= 1:
            menu.addAction("Quitar", lambda: self._delete_items([item]))
        else:
            menu.addAction(f"Quitar {len(selected_items)} elementos", lambda: self._delete_items(selected_items))
        menu.exec(global_pos)

    def _build_category_assignment_menu(self, items: list[dict]) -> QMenu:
        menu = QMenu("Categoría", self)
        menu.addAction("Sin categoría", lambda: self._assign_category(items, None))
        categories = self.settings.get("categories", {})
        if categories:
            menu.addSeparator()
            for name, color in categories.items():
                menu.addAction(color_swatch_icon(color), name, lambda n=name: self._assign_category(items, n))
        menu.addSeparator()
        menu.addAction("Nueva categoría...", lambda: self._create_category_and_assign(items))
        return menu

    def _build_size_menu(self, items: list[dict]) -> QMenu:
        menu = QMenu("Tamaño", self)
        for key, preset in SIZE_PRESETS.items():
            menu.addAction(preset["label"], lambda k=key: self._assign_size(items, k))
        return menu

    def _assign_category(self, items: list[dict], name: str | None) -> None:
        self.push_undo("categoría")
        for item in items:
            item["category"] = name
        save_shortcuts(self.shortcuts)
        self.refresh()

    def _assign_size(self, items: list[dict], size: str) -> None:
        self.push_undo("tamaño")
        for item in items:
            item["size"] = size
            # Un preset elegido a mano desde el menú anula cualquier
            # tamaño personalizado previo (arrastrando la esquina).
            item["width"] = None
            item["height"] = None
        save_shortcuts(self.shortcuts)
        self.refresh()

    def _create_category_and_assign(self, items: list[dict]) -> None:
        name, ok = QInputDialog.getText(self, "Nueva categoría", "Nombre:")
        name = name.strip()
        if not ok or not name:
            return
        color = QColorDialog.getColor(QColor(self.colors["accent"]), self, "Color de la categoría")
        if not color.isValid():
            return
        categories = dict(self.settings.get("categories", {}))
        categories[name] = color.name()
        self.settings["categories"] = categories
        save_settings(self.settings)
        self._assign_category(items, name)

    def rename_item(self, item: dict) -> None:
        name, ok = QInputDialog.getText(self, "Renombrar", "Nuevo nombre:", text=item["name"])
        if ok and name.strip():
            self.push_undo("renombrar")
            item["name"] = name.strip()
            save_shortcuts(self.shortcuts)
            self.refresh()

    # -- deshacer / rehacer -------------------------------------------------
    #
    # Guarda una instantánea completa de accesos+papelera ANTES de cada
    # operación que los modifica (borrar, mover, redimensionar, pegar,
    # renombrar, cambiar categoría/tamaño...). Es más simple y fiable que
    # llevar la cuenta de "el opuesto de cada acción" a mano, a costa de
    # algo más de memoria — asumible para unas pocas decenas de pasos.

    def push_undo(self, label: str) -> None:
        snapshot = (label, copy.deepcopy(self.shortcuts), copy.deepcopy(load_trash()))
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > self._UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _current_snapshot(self, label: str) -> tuple[str, list, list]:
        return (label, copy.deepcopy(self.shortcuts), copy.deepcopy(load_trash()))

    def undo(self) -> None:
        if not self._undo_stack:
            return
        label, shortcuts_snapshot, trash_snapshot = self._undo_stack.pop()
        self._redo_stack.append(self._current_snapshot(label))
        self.shortcuts = shortcuts_snapshot
        save_shortcuts(self.shortcuts)
        save_trash(trash_snapshot)
        self.selected_ids = set()
        self.refresh()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        label, shortcuts_snapshot, trash_snapshot = self._redo_stack.pop()
        self._undo_stack.append(self._current_snapshot(label))
        self.shortcuts = shortcuts_snapshot
        save_shortcuts(self.shortcuts)
        save_trash(trash_snapshot)
        self.selected_ids = set()
        self.refresh()

    # -- copiar / pegar (Ctrl+C / Ctrl+V) ------------------------------------

    def copy_selected(self) -> None:
        selected = [it for it in self.shortcuts if it["id"] in self.selected_ids]
        if not selected:
            return
        root_ids = {it["id"] for it in selected}
        all_items = list(selected)
        seen_ids = set(root_ids)
        pending = list(selected)
        while pending:
            current = pending.pop()
            if current["type"] != "folder":
                continue
            children = [it for it in self.shortcuts if it.get("parent_id") == current["id"]]
            for child in children:
                if child["id"] not in seen_ids:
                    seen_ids.add(child["id"])
                    all_items.append(child)
                    pending.append(child)
        self._clipboard = {"root_ids": root_ids, "items": copy.deepcopy(all_items)}

    def paste_clipboard(self) -> None:
        clip = self._clipboard
        if not clip or not clip.get("items"):
            return
        self.push_undo("pegar")
        id_map = {it["id"]: new_item_id() for it in clip["items"]}
        siblings = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        next_order = len(siblings)
        new_items = []
        for it in clip["items"]:
            new_it = copy.deepcopy(it)
            new_it["id"] = id_map[it["id"]]
            if it["id"] in clip["root_ids"]:
                new_it["parent_id"] = self.current_folder_id
                new_it["order"] = next_order
                next_order += 1
                new_it["name"] = f"{it['name']} (copia)"
            else:
                new_it["parent_id"] = id_map.get(it["parent_id"], it["parent_id"])
            new_it["open_count"] = 0
            new_it["last_opened"] = 0.0
            new_items.append(new_it)
        self.shortcuts.extend(new_items)
        save_shortcuts(self.shortcuts)
        self.selected_ids = {id_map[i] for i in clip["root_ids"]}
        self.refresh()

    # -- redimensionar a mano (arrastrar esquina) ----------------------------

    def handle_tile_resize_moved(self, item: dict, width: int, height: int) -> None:
        # Solo reajusta el flujo mientras se arrastra; no toca el disco
        # hasta que se suelta (handle_tile_resize_finished), para no
        # generar decenas de escrituras por segundo.
        self._relayout_sections()

    def handle_tile_resize_finished(self, item: dict, width: int, height: int) -> None:
        if self.settings.get("snap_to_grid", True):
            width = snap_dimension(width, GRID_SNAP_STEP)
            height = snap_dimension(height, GRID_SNAP_STEP)
        self.push_undo("redimensionar")
        for it in self.shortcuts:
            if it["id"] == item["id"]:
                it["width"] = width
                it["height"] = height
                break
        save_shortcuts(self.shortcuts)
        self.refresh()

    # -- favoritos --------------------------------------------------------

    def handle_favorite_toggle(self, item: dict) -> None:
        # `item` es la misma referencia que vive dentro de self.shortcuts
        # (las tarjetas se construyen a partir de esa lista sin copiarla),
        # así que la propia TileWidget ya ha actualizado item["favorite"];
        # aquí solo hace falta persistir el cambio y avisar al usuario.
        save_shortcuts(self.shortcuts)
        if item.get("favorite"):
            self._show_toast("★ Añadido a favoritos")
        else:
            self._show_toast("☆ Quitado de favoritos")

    def open_favorites_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Favoritos")
        dialog.resize(380, 420)
        layout = QVBoxLayout(dialog)

        favorites = favorite_items(self.shortcuts)
        if not favorites:
            layout.addWidget(QLabel(
                "Todavía no tienes favoritos.\n"
                "Pulsa la ☆ de cualquier tarjeta (al pasar el ratón o "
                "seleccionarla) para añadirla aquí."
            ))
        else:
            rows_container = QWidget()
            rows_layout = QVBoxLayout(rows_container)
            rows_layout.setContentsMargins(0, 0, 0, 0)

            def build_rows() -> None:
                while rows_layout.count():
                    layout_item = rows_layout.takeAt(0)
                    if layout_item.widget() is not None:
                        layout_item.widget().deleteLater()
                for item in favorite_items(self.shortcuts):
                    row_widget = QWidget()
                    row = QHBoxLayout(row_widget)
                    row.setContentsMargins(0, 2, 0, 2)

                    icon = "📁" if item["type"] == "folder" else "📄"
                    open_button = QPushButton(f"{icon}  {item['name']}")
                    open_button.setObjectName("ghostButton")
                    open_button.setCursor(QCursor(Qt.PointingHandCursor))
                    open_button.clicked.connect(lambda _c=False, it=item: (dialog.accept(), self.open_item(it)))
                    row.addWidget(open_button, stretch=1)

                    unfav_button = QPushButton("★")
                    unfav_button.setObjectName("favoriteStarButton")
                    unfav_button.setToolTip("Quitar de favoritos")
                    unfav_button.setCursor(QCursor(Qt.PointingHandCursor))

                    def unfavorite(_c=False, item_id=item["id"]) -> None:
                        for it in self.shortcuts:
                            if it["id"] == item_id:
                                it["favorite"] = False
                                break
                        save_shortcuts(self.shortcuts)
                        tile = self._tile_by_id.get(item_id)
                        if tile is not None:
                            tile._is_favorite = False
                            tile._update_favorite_visual()
                        build_rows()

                    unfav_button.clicked.connect(unfavorite)
                    row.addWidget(unfav_button)
                    rows_layout.addWidget(row_widget)

            build_rows()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(rows_container)
            layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.exec()

    # -- estadísticas de uso --------------------------------------------------

    def open_usage_stats_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Más usados")
        dialog.resize(380, 420)
        layout = QVBoxLayout(dialog)

        top_items = most_used_items(self.shortcuts, limit=15)
        if not top_items:
            layout.addWidget(QLabel(
                "Todavía no hay estadísticas de uso.\n"
                "Se irán acumulando a medida que abras accesos."
            ))
        else:
            rows_container = QWidget()
            rows_layout = QVBoxLayout(rows_container)
            rows_layout.setContentsMargins(0, 0, 0, 0)
            for rank, item in enumerate(top_items, start=1):
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 2, 0, 2)
                row.addWidget(QLabel(f"{rank}."))
                name_label = QLabel(item["name"])
                row.addWidget(name_label, stretch=1)
                count = int(item.get("open_count", 0) or 0)
                times = "vez" if count == 1 else "veces"
                count_label = QLabel(f"{count} {times}")
                count_label.setStyleSheet(f"color: {self.colors['text_muted']}; font-size: 11px;")
                row.addWidget(count_label)
                rows_layout.addWidget(row_widget)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(rows_container)
            layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.exec()

    def _delete_items(self, items: list[dict]) -> None:
        if not items:
            return
        if len(items) == 1:
            question = f"¿Quitar «{items[0]['name']}»?"
        else:
            question = f"¿Quitar {len(items)} elementos seleccionados?"
        question += "\n\nSe moverán a la papelera, no se borran para siempre."
        confirm = QMessageBox.question(self, "Quitar", question)
        if confirm != QMessageBox.Yes:
            return

        self.push_undo("quitar")
        ids = {it["id"] for it in items}
        # Si se borra una carpeta, sus elementos internos van también a la
        # papelera (si no, quedarían "huérfanos" sin carpeta que los
        # contenga y no aparecerían en ningún sitio).
        to_delete = list(items)
        pending = list(items)
        while pending:
            current = pending.pop()
            children = [it for it in self.shortcuts if it.get("parent_id") == current["id"]]
            for child in children:
                if child["id"] not in ids:
                    ids.add(child["id"])
                    to_delete.append(child)
                    pending.append(child)

        trash = load_trash()
        for it in to_delete:
            entry = dict(it)
            entry["deleted_at"] = time.time()
            trash.append(entry)
        save_trash(trash)

        self.shortcuts = [it for it in self.shortcuts if it["id"] not in ids]
        save_shortcuts(self.shortcuts)
        self.selected_ids -= ids
        self.refresh()

    def add_shortcut_dialog(self) -> None:
        menu = QMenu(self)
        menu.addAction("📄  Acceso a un archivo", self.add_file_shortcut)
        menu.addAction("📁  Acceso a una carpeta del sistema", self.add_folder_shortcut)
        menu.addAction("🗂  Carpeta para organizar", self.add_internal_folder)
        menu.exec(QCursor.pos())

    def add_file_shortcut(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Selecciona un archivo", str(Path.home()))
        if path:
            self._finish_add_shortcut(path)

    def add_folder_shortcut(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Selecciona una carpeta", str(Path.home()))
        if path:
            self._finish_add_shortcut(path)

    def _finish_add_shortcut(self, path: str) -> None:
        default_name = Path(path).name
        name, ok = QInputDialog.getText(self, "Nombre del acceso", "Nombre:", text=default_name)
        if not ok or not name.strip():
            return
        self.push_undo("añadir")
        siblings = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        self.shortcuts.append({
            "id": new_item_id(),
            "type": "shortcut",
            "name": name.strip(),
            "path": portabilize_path(path),
            "parent_id": self.current_folder_id,
            "order": len(siblings),
            "color": None,
            "size": DEFAULT_SIZE,
            "width": None,
            "height": None,
            "category": None,
            "open_count": 0,
            "last_opened": 0.0,
        })
        save_shortcuts(self.shortcuts)
        self.refresh()

    def add_internal_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "Nombre de la carpeta", "Nombre:", text="Nueva carpeta")
        if not ok or not name.strip():
            return
        self.push_undo("añadir")
        siblings = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        self.shortcuts.append({
            "id": new_item_id(),
            "type": "folder",
            "name": name.strip(),
            "parent_id": self.current_folder_id,
            "order": len(siblings),
            "color": None,
            "size": DEFAULT_SIZE,
            "width": None,
            "height": None,
            "category": None,
            "open_count": 0,
            "last_opened": 0.0,
        })
        save_shortcuts(self.shortcuts)
        self.refresh()

    # -- orden / vista / categorías / configuración / papelera ----------

    SORT_LABELS = {
        "manual": "Manual (arrastrar)",
        "name_asc": "Nombre A-Z",
        "name_desc": "Nombre Z-A",
        "folders_first": "Carpetas primero",
    }

    def _update_sort_button_text(self) -> None:
        mode = self.settings.get("sort_mode", "manual")
        self.sort_button.setText("↕  " + self.SORT_LABELS.get(mode, "Orden"))

    def show_sort_menu(self) -> None:
        menu = QMenu(self)
        current = self.settings.get("sort_mode", "manual")
        for key in SORT_MODES:
            action = menu.addAction(self.SORT_LABELS[key], lambda k=key: self._set_sort_mode(k))
            action.setCheckable(True)
            action.setChecked(key == current)
        menu.exec(self.sort_button.mapToGlobal(self.sort_button.rect().bottomLeft()))

    def _set_sort_mode(self, mode: str) -> None:
        self.settings["sort_mode"] = mode
        save_settings(self.settings)
        self._update_sort_button_text()
        self.refresh()

    def _update_view_button_text(self) -> None:
        compact = self.settings.get("card_style") == "compact"
        self.view_button.setText("▦  Tarjetas" if compact else "☰  Compacta")
        self.view_button.setToolTip("Cambiar a vista de tarjetas" if compact else "Cambiar a vista compacta")

    def toggle_view_style(self) -> None:
        compact = self.settings.get("card_style") == "compact"
        self.settings["card_style"] = "cards" if compact else "compact"
        save_settings(self.settings)
        self._update_view_button_text()
        self.refresh()

    def show_category_menu(self) -> None:
        menu = QMenu(self)
        all_action = menu.addAction("Todas las categorías", lambda: self._set_category_filter(None))
        all_action.setCheckable(True)
        all_action.setChecked(self.category_filter is None and not self.settings.get("group_by_category"))

        none_action = menu.addAction("Sin categoría", lambda: self._set_category_filter(""))
        none_action.setCheckable(True)
        none_action.setChecked(self.category_filter == "")

        categories = self.settings.get("categories", {})
        if categories:
            menu.addSeparator()
            for name, color in categories.items():
                action = menu.addAction(
                    color_swatch_icon(color), name, lambda n=name: self._set_category_filter(n)
                )
                action.setCheckable(True)
                action.setChecked(self.category_filter == name)

        menu.addSeparator()
        group_action = menu.addAction("Agrupar por categoría", self._toggle_group_by_category)
        group_action.setCheckable(True)
        group_action.setChecked(bool(self.settings.get("group_by_category")))

        menu.addSeparator()
        menu.addAction("Gestionar categorías...", self.open_manage_categories_dialog)
        menu.exec(self.category_button.mapToGlobal(self.category_button.rect().bottomLeft()))

    def _set_category_filter(self, value: str | None) -> None:
        self.category_filter = value
        if value is not None and self.settings.get("group_by_category"):
            self.settings["group_by_category"] = False
            save_settings(self.settings)
        self.refresh()

    def _toggle_group_by_category(self) -> None:
        self.settings["group_by_category"] = not self.settings.get("group_by_category")
        if self.settings["group_by_category"]:
            self.category_filter = None
        save_settings(self.settings)
        self.refresh()

    def open_manage_categories_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Gestionar categorías")
        dialog.resize(360, 320)
        layout = QVBoxLayout(dialog)

        rows_container = QWidget()
        rows_layout = QVBoxLayout(rows_container)

        def render_rows() -> None:
            while rows_layout.count():
                item = rows_layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
            categories = self.settings.get("categories", {})
            if not categories:
                rows_layout.addWidget(QLabel("Todavía no hay categorías."))
            for name, color in list(categories.items()):
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 0, 0, 0)
                swatch = QLabel()
                swatch.setFixedSize(14, 14)
                swatch.setStyleSheet(f"background: {color}; border-radius: 4px;")
                row.addWidget(swatch)
                row.addWidget(QLabel(name), stretch=1)
                rename_btn = QPushButton("Renombrar")
                rename_btn.clicked.connect(lambda _c=False, n=name: rename_category(n))
                row.addWidget(rename_btn)
                recolor_btn = QPushButton("Color")
                recolor_btn.clicked.connect(lambda _c=False, n=name: recolor_category(n))
                row.addWidget(recolor_btn)
                delete_btn = QPushButton("Borrar")
                delete_btn.clicked.connect(lambda _c=False, n=name: delete_category(n))
                row.addWidget(delete_btn)
                rows_layout.addWidget(row_widget)

        def rename_category(old_name: str) -> None:
            new_name, ok = QInputDialog.getText(dialog, "Renombrar categoría", "Nombre:", text=old_name)
            new_name = new_name.strip()
            if not ok or not new_name or new_name == old_name:
                return
            categories = dict(self.settings.get("categories", {}))
            categories[new_name] = categories.pop(old_name)
            self.settings["categories"] = categories
            for it in self.shortcuts:
                if it.get("category") == old_name:
                    it["category"] = new_name
            save_settings(self.settings)
            save_shortcuts(self.shortcuts)
            render_rows()
            self.refresh()

        def recolor_category(name: str) -> None:
            categories = dict(self.settings.get("categories", {}))
            color = QColorDialog.getColor(QColor(categories.get(name, "#38bdf8")), dialog)
            if not color.isValid():
                return
            categories[name] = color.name()
            self.settings["categories"] = categories
            save_settings(self.settings)
            render_rows()
            self.refresh()

        def delete_category(name: str) -> None:
            confirm = QMessageBox.question(
                dialog, "Borrar categoría",
                f"¿Borrar la categoría «{name}»? Los accesos que la tengan se quedarán sin categoría.",
            )
            if confirm != QMessageBox.Yes:
                return
            categories = dict(self.settings.get("categories", {}))
            categories.pop(name, None)
            self.settings["categories"] = categories
            for it in self.shortcuts:
                if it.get("category") == name:
                    it["category"] = None
            if self.category_filter == name:
                self.category_filter = None
            save_settings(self.settings)
            save_shortcuts(self.shortcuts)
            render_rows()
            self.refresh()

        render_rows()
        layout.addWidget(rows_container)
        layout.addStretch()

        new_row = QHBoxLayout()
        new_button = QPushButton("+ Nueva categoría...")

        def create_new() -> None:
            name, ok = QInputDialog.getText(dialog, "Nueva categoría", "Nombre:")
            name = name.strip()
            if not ok or not name:
                return
            color = QColorDialog.getColor(QColor(self.colors["accent"]), dialog)
            if not color.isValid():
                return
            categories = dict(self.settings.get("categories", {}))
            categories[name] = color.name()
            self.settings["categories"] = categories
            save_settings(self.settings)
            render_rows()

        new_button.clicked.connect(create_new)
        new_row.addWidget(new_button)
        layout.addLayout(new_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.exec()

    def open_settings_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Configuración")
        dialog.resize(340, 380)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("Tema"))
        theme_combo = QComboBox()
        theme_keys = list(THEMES.keys())
        for key in theme_keys:
            theme_combo.addItem(THEMES[key]["label"], key)
        theme_combo.setCurrentIndex(theme_keys.index(self.settings.get("theme", "morado")))
        layout.addWidget(theme_combo)

        layout.addWidget(QLabel("Clics para abrir un acceso"))
        click_group = QButtonGroup(dialog)
        single_radio = QRadioButton("Un clic")
        double_radio = QRadioButton("Doble clic")
        click_group.addButton(single_radio)
        click_group.addButton(double_radio)
        if self.settings.get("click_mode", "double") == "single":
            single_radio.setChecked(True)
        else:
            double_radio.setChecked(True)
        layout.addWidget(single_radio)
        layout.addWidget(double_radio)

        auto_update_check = QCheckBox("Buscar actualizaciones automáticamente")
        auto_update_check.setChecked(bool(self.settings.get("auto_check_updates", True)))
        layout.addWidget(auto_update_check)

        snap_check = QCheckBox("Ajustar a rejilla al redimensionar tarjetas")
        snap_check.setToolTip(
            "Al arrastrar la esquina de una tarjeta para cambiar su tamaño, "
            "el resultado se ajusta a múltiplos de "
            f"{GRID_SNAP_STEP}px para que queden mejor alineadas entre sí."
        )
        snap_check.setChecked(bool(self.settings.get("snap_to_grid", True)))
        layout.addWidget(snap_check)

        layout.addWidget(QLabel("Mostrar la ruta original en cada acceso"))
        path_group = QButtonGroup(dialog)
        path_always_radio = QRadioButton("Mostrar siempre (como ahora)")
        path_select_radio = QRadioButton("Solo en los accesos seleccionados")
        path_never_radio = QRadioButton("No mostrarla nunca")
        for radio in (path_always_radio, path_select_radio, path_never_radio):
            path_group.addButton(radio)
            layout.addWidget(radio)
        current_path_display = self.settings.get("path_display", DEFAULT_PATH_DISPLAY)
        if current_path_display == "never":
            path_never_radio.setChecked(True)
        elif current_path_display == "on_select":
            path_select_radio.setChecked(True)
        else:
            path_always_radio.setChecked(True)

        startup_check = None
        if startup_supported():
            startup_check = QCheckBox("Iniciar con Windows")
            startup_check.setChecked(is_startup_enabled())
            layout.addWidget(startup_check)

        cache_row = QHBoxLayout()
        cache_size = win_icons.disk_cache_size_bytes()
        cache_label = QLabel(f"Caché de iconos en disco: {cache_size / 1024:.0f} KB")
        cache_row.addWidget(cache_label)
        cache_row.addStretch()
        clear_cache_button = QPushButton("Vaciar caché de iconos")

        def clear_icon_cache() -> None:
            size_before = win_icons.disk_cache_size_bytes()
            win_icons.clear_disk_cache()
            if size_before:
                cache_label.setText("Caché de iconos en disco: 0 KB")
                QMessageBox.information(
                    dialog, "Caché de iconos", f"Se han liberado {size_before / 1024:.0f} KB."
                )
            else:
                QMessageBox.information(dialog, "Caché de iconos", "La caché ya estaba vacía.")

        clear_cache_button.clicked.connect(clear_icon_cache)
        cache_row.addWidget(clear_cache_button)
        layout.addLayout(cache_row)

        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        def save_and_close() -> None:
            theme_key = theme_combo.currentData()
            theme_changed = theme_key != self.settings.get("theme")
            self.settings["theme"] = theme_key
            self.settings["click_mode"] = "single" if single_radio.isChecked() else "double"
            self.settings["auto_check_updates"] = auto_update_check.isChecked()
            self.settings["snap_to_grid"] = snap_check.isChecked()
            if path_never_radio.isChecked():
                self.settings["path_display"] = "never"
            elif path_select_radio.isChecked():
                self.settings["path_display"] = "on_select"
            else:
                self.settings["path_display"] = "always"
            if startup_check is not None:
                set_startup_enabled(startup_check.isChecked())
            save_settings(self.settings)
            if theme_changed:
                self.colors = get_theme(theme_key)
                self._apply_theme()
            self.refresh()
            dialog.accept()

        buttons.accepted.connect(save_and_close)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def open_trash_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Papelera")
        dialog.resize(420, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"Los elementos se eliminan definitivamente a los {TRASH_RETENTION_DAYS} días de estar aquí."
        ))

        rows_container = QWidget()
        rows_layout = QVBoxLayout(rows_container)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(rows_container)
        layout.addWidget(scroll, stretch=1)

        def render_rows() -> None:
            while rows_layout.count():
                item = rows_layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
            trash = load_trash()
            if not trash:
                rows_layout.addWidget(QLabel("La papelera está vacía."))
                return
            trash.sort(key=lambda it: it.get("deleted_at", 0), reverse=True)
            for entry in trash:
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 0, 0, 0)
                icon = "📁" if entry["type"] == "folder" else "📄"
                row.addWidget(QLabel(f"{icon} {entry['name']}"), stretch=1)
                restore_btn = QPushButton("Restaurar")
                restore_btn.clicked.connect(lambda _c=False, e=entry: restore_entry(e))
                row.addWidget(restore_btn)
                delete_btn = QPushButton("Eliminar ya")
                delete_btn.clicked.connect(lambda _c=False, e=entry: delete_forever(e))
                row.addWidget(delete_btn)
                rows_layout.addWidget(row_widget)
            rows_layout.addStretch()

        def restore_entry(entry: dict) -> None:
            trash = load_trash()
            trash = [it for it in trash if it["id"] != entry["id"]]
            restored = dict(entry)
            restored.pop("deleted_at", None)
            valid_ids = {it["id"] for it in self.shortcuts}
            if restored.get("parent_id") not in valid_ids:
                restored["parent_id"] = None
            self.shortcuts.append(restored)
            save_shortcuts(self.shortcuts)
            save_trash(trash)
            render_rows()
            self.refresh()

        def delete_forever(entry: dict) -> None:
            confirm = QMessageBox.question(
                dialog, "Eliminar definitivamente",
                f"¿Eliminar «{entry['name']}» para siempre? No se puede deshacer.",
            )
            if confirm != QMessageBox.Yes:
                return
            trash = load_trash()
            trash = [it for it in trash if it["id"] != entry["id"]]
            save_trash(trash)
            render_rows()

        render_rows()

        buttons_row = QHBoxLayout()
        empty_button = QPushButton("Vaciar papelera")

        def empty_trash() -> None:
            trash = load_trash()
            if not trash:
                return
            confirm = QMessageBox.question(
                dialog, "Vaciar papelera",
                f"¿Eliminar definitivamente los {len(trash)} elementos de la papelera?",
            )
            if confirm != QMessageBox.Yes:
                return
            save_trash([])
            render_rows()

        empty_button.clicked.connect(empty_trash)
        buttons_row.addWidget(empty_button)
        buttons_row.addStretch()
        close_button = QPushButton("Cerrar")
        close_button.clicked.connect(dialog.close)
        buttons_row.addWidget(close_button)
        layout.addLayout(buttons_row)

        dialog.exec()

    # -- actualizaciones ---------------------------------------------------

    def check_updates_dialog(self, manual: bool) -> None:
        if self._update_check_thread is not None and self._update_check_thread.isRunning():
            return  # ya hay una comprobación en marcha
        self._manual_check = manual
        self.update_icon.setToolTip("Buscando actualizaciones...")
        self.update_icon.setText("⏳")
        thread = UpdateCheckWorker()
        thread.result_ready.connect(self._on_update_check_result)
        thread.failed.connect(self._on_update_check_failed)
        thread.finished.connect(thread.deleteLater)
        self._update_check_thread = thread
        thread.start()

    def _on_update_check_result(self, update: UpdateInfo | None) -> None:
        self.update_icon.setText("🔄")
        self.update_icon.setToolTip("Buscar actualizaciones")
        self._update_check_thread = None
        if update is None:
            if self._manual_check:
                QMessageBox.information(self, "Actualizaciones", "Ya tienes la última versión instalada.")
            return
        self._pending_update = update
        self.update_icon.setText("🟢")
        self.update_icon.setToolTip(f"Hay una versión nueva disponible: {update.latest_version}")
        self._prompt_install(update)

    def _on_update_check_failed(self, message: str) -> None:
        self.update_icon.setText("🔄")
        self.update_icon.setToolTip("Buscar actualizaciones")
        self._update_check_thread = None
        if self._manual_check:
            QMessageBox.warning(self, "No se pudo comprobar", message)

    def _prompt_install(self, update: UpdateInfo) -> None:
        answer = QMessageBox.question(
            self,
            "Actualización disponible",
            f"Hay una versión nueva: {update.latest_version} (tienes {update.current_version}).\n\n"
            "¿Descargarla e instalarla ahora? La app se cerrará un momento y se abrirá "
            "sola cuando termine.",
        )
        if answer == QMessageBox.Yes:
            self._start_install(update)

    def _start_install(self, update: UpdateInfo) -> None:
        dialog = UpdateProgressDialog(self, self.colors)
        self._update_progress_dialog = dialog

        thread = UpdateInstallWorker(update)
        thread.progress.connect(dialog.update_progress)
        thread.finished_ok.connect(self._on_install_finished)
        thread.failed.connect(self._on_install_failed)
        thread.finished.connect(thread.deleteLater)
        self._update_install_thread = thread
        thread.start()
        dialog.exec()

    def _on_install_finished(self) -> None:
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.status_label.setText("¡Listo! Reiniciando...")
            self._update_progress_dialog.eta_label.setText("")
        QTimer.singleShot(600, self._relaunch_with_update)

    def _relaunch_with_update(self) -> None:
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.accept()
        restart_with_update(self)

    def _on_install_failed(self, message: str) -> None:
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.reject()
            self._update_progress_dialog = None
        QMessageBox.warning(self, "Error al actualizar", message)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
