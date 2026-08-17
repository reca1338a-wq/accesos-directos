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

import io
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QMimeData, QPoint, QPropertyAnimation, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QDrag, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import win_icons
from app_config import (
    DEFAULT_SIZE,
    SIZE_PRESETS,
    USER_DATA_DIR,
    expand_path,
    get_app_version,
    get_theme,
    load_settings,
    load_shortcuts,
    load_trash,
    new_item_id,
    portabilize_path,
    save_shortcuts,
    save_trash,
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


class TileWidget(QFrame):
    context_requested = Signal(dict, object)
    clicked_with_modifiers = Signal(dict, object)
    drag_started = Signal(dict)
    drop_received = Signal(dict, list, str)  # (target_item, dragged_ids, side)
    files_dropped_here = Signal(dict, list)  # (target_item, local_paths)

    def __init__(self, item: dict, colors: dict, sibling_items: list[dict]) -> None:
        super().__init__()
        self.item = item
        self.colors = colors
        preset = SIZE_PRESETS.get(item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])

        self.setObjectName("tile")
        self.setFixedSize(preset["width"], preset["height"])
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setAcceptDrops(True)
        self._selected = False
        self._press_pos: QPoint | None = None
        self._dragging = False

        is_broken = bool(
            item["type"] == "shortcut"
            and item.get("path")
            and not Path(expand_path(item["path"])).expanduser().exists()
        )
        self._is_broken = is_broken

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignHCenter)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedHeight(36)
        pixmap = self._load_icon(sibling_items)
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

        subtitle = self._subtitle(sibling_items)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("tileSubtitle")
            subtitle_label.setAlignment(Qt.AlignCenter)
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

        layout.addStretch()

        # Sombra suave: esto es exactamente lo que en Tkinter no se podía
        # hacer sin recurrir a trucos con imágenes — aquí es una línea.
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(18)
        self._shadow.setOffset(0, 3)
        self._shadow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(self._shadow)

        # Animación sutil de "elevación" al pasar el ratón por encima.
        self._shadow_anim = QPropertyAnimation(self._shadow, b"blurRadius")
        self._shadow_anim.setDuration(150)
        self._shadow_anim.setEasingCurve(QEasingCurve.OutCubic)

    def _load_icon(self, sibling_items: list[dict]):
        preset = SIZE_PRESETS.get(self.item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])
        icon_px = 40 if preset["width"] >= 200 else (32 if preset["width"] >= 150 else 26)

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

    def _subtitle(self, sibling_items: list[dict]) -> str:
        if self.item["type"] == "folder":
            count = sum(1 for it in sibling_items if it["parent_id"] == self.item["id"])
            return f"{count} elemento" + ("" if count == 1 else "s")
        if self._is_broken:
            return "⚠ No se encuentra"
        home = str(Path.home())
        path = self.item.get("path", "")
        return "~" + path[len(home):] if path.startswith(home) else path

    def set_selected(self, value: bool) -> None:
        if self._selected == value:
            return
        self._selected = value
        self.setProperty("selected", "true" if value else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event) -> None:
        self._shadow_anim.stop()
        self._shadow_anim.setStartValue(self._shadow.blurRadius())
        self._shadow_anim.setEndValue(28)
        self._shadow_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._shadow_anim.stop()
        self._shadow_anim.setStartValue(self._shadow.blurRadius())
        self._shadow_anim.setEndValue(18)
        self._shadow_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
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
        was_dragging = self._dragging
        self._dragging = False
        inside = self.rect().contains(event.position().toPoint())
        self._press_pos = None
        if event.button() == Qt.LeftButton and inside and not was_dragging:
            self.clicked_with_modifiers.emit(self.item, event.modifiers())
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.item, event.globalPos())

    # -- soltar (reordenar / mover a carpeta / arrastrar desde el Explorador) --

    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(MIME_ITEM_IDS) or md.hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(MIME_ITEM_IDS):
            try:
                ids = json.loads(bytes(md.data(MIME_ITEM_IDS)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            if self.item["id"] in ids:
                return
            x = event.position().toPoint().x()
            if self.item["type"] == "folder":
                side = "into"
            else:
                side = "before" if x < self.width() / 2 else "after"
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

    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(MIME_ITEM_IDS) or md.hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.colors = get_theme(self.settings.get("theme"))
        self.shortcuts = load_shortcuts()
        self.current_folder_id: str | None = None
        self.breadcrumb: list[tuple[str, str | None]] = [("Inicio", None)]

        self.selected_ids: set[str] = set()
        self._last_clicked_id: str | None = None
        self._marquee_base_selection: set[str] | None = None
        self._tiles: list[TileWidget] = []
        self._tile_by_id: dict[str, TileWidget] = {}
        self._focus_index: int = -1

        self.setWindowTitle("Accesos Directos")
        self.resize(820, 600)
        self.setMinimumSize(480, 360)
        self.setFocusPolicy(Qt.StrongFocus)

        self._build_ui()
        self._apply_theme()
        self.refresh()

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
        self.update_icon.setToolTip("Buscar actualizaciones (próximamente)")
        header_row.addWidget(self.update_icon)
        root_layout.addLayout(header_row)

        self.breadcrumb_label = QLabel()
        self.breadcrumb_label.setObjectName("breadcrumb")
        root_layout.addWidget(self.breadcrumb_label)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        root_layout.addWidget(divider)
        root_layout.addSpacing(12)

        # -- barra de herramientas --
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
        root_layout.addLayout(toolbar_row)
        root_layout.addSpacing(10)

        # -- cuadrícula de tarjetas, dentro de un área con scroll --
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("scrollArea")
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.grid_container = GridContainer(self)
        self.grid_container.setObjectName("gridContainer")
        self.flow_layout = FlowLayout(self.grid_container, margin=4, spacing=14)
        self.grid_container.setLayout(self.flow_layout)

        self.scroll_area.setWidget(self.grid_container)
        root_layout.addWidget(self.scroll_area, stretch=1)

        self.empty_label = QLabel("Esta carpeta está vacía.\nUsa «+ Añadir» para empezar.")
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)

        footer = QLabel(f"Datos guardados en: {USER_DATA_DIR}")
        footer.setObjectName("footer")
        footer.setWordWrap(True)
        root_layout.addWidget(footer)

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

            QFrame#tile {{
                background: {c['surface']}; border-radius: 16px; border: 1px solid {c['surface']};
            }}
            QFrame#tile:hover {{ background: {c['surface_hover']}; border: 1px solid {c['accent']}; }}
            QFrame#tile[selected="true"] {{ border: 2px solid {c['accent']}; background: {c['surface_hover']}; }}
            QLabel#tileName {{ color: {c['text']}; font-size: 12px; font-weight: 600; font-family: 'Segoe UI'; }}
            QLabel#tileSubtitle {{ color: {c['text_muted']}; font-size: 9px; }}

            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {c['surface_hover']}; border-radius: 5px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    # -- datos / navegación ----------------------------------------------

    def _visible_items(self) -> list[dict]:
        items = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        return sorted(items, key=lambda it: it.get("order", 0))

    def _render_breadcrumb(self) -> None:
        self.breadcrumb_label.setText("  ›  ".join(name for name, _ in self.breadcrumb))
        self.back_button.setEnabled(self.current_folder_id is not None)

    def refresh(self) -> None:
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        items = self._visible_items()
        self._render_breadcrumb()
        self.selected_ids &= {it["id"] for it in items}
        self._tiles = []
        self._tile_by_id = {}

        if not items:
            self.flow_layout.addWidget(self.empty_label)
            return

        for item in items:
            tile = TileWidget(item, self.colors, self.shortcuts)
            tile.clicked_with_modifiers.connect(self.handle_tile_click)
            tile.context_requested.connect(self.show_context_menu)
            tile.drag_started.connect(self.begin_drag)
            tile.drop_received.connect(self.handle_internal_drop)
            tile.files_dropped_here.connect(self.handle_external_drop)
            tile.set_selected(item["id"] in self.selected_ids)
            self.flow_layout.addWidget(tile)
            self._tiles.append(tile)
            self._tile_by_id[item["id"]] = tile

    # -- acciones ----------------------------------------------------

    def open_item(self, item: dict) -> None:
        if item["type"] == "folder":
            self.breadcrumb.append((item["name"], item["id"]))
            self.current_folder_id = item["id"]
            self.selected_ids = set()
            self.refresh()
            return
        try:
            open_path(item["path"])
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Archivo no encontrado", str(exc))
        except OSError as exc:
            QMessageBox.warning(self, "Error al abrir", str(exc))

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
            self.selected_ids = set()
            self._last_clicked_id = item["id"]
            self._refresh_selection_visuals()
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
        hit = {t.item["id"] for t in self._tiles if t.geometry().intersects(rect)}
        base = self._marquee_base_selection or set()
        self.selected_ids = base | hit
        self._refresh_selection_visuals()

    def end_marquee(self) -> None:
        self._marquee_base_selection = None

    def select_all(self) -> None:
        self.selected_ids = {t.item["id"] for t in self._tiles}
        self._refresh_selection_visuals()

    # -- arrastrar y soltar ----------------------------------------------

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
        for raw_path in paths:
            portable = portabilize_path(raw_path)
            if portable in existing_paths:
                continue
            self.shortcuts.append({
                "id": new_item_id(),
                "type": "shortcut",
                "name": Path(raw_path).name,
                "path": portable,
                "parent_id": parent_id,
                "order": len(siblings) + added,
                "color": None,
                "size": DEFAULT_SIZE,
                "category": None,
                "open_count": 0,
                "last_opened": 0.0,
            })
            existing_paths.add(portable)
            added += 1
        if added:
            save_shortcuts(self.shortcuts)
            self.refresh()

    # -- teclado ----------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        ordered = self._tiles
        if key == Qt.Key_Escape:
            self.selected_ids = set()
            self._refresh_selection_visuals()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_ids:
            items = [it for it in self.shortcuts if it["id"] in self.selected_ids]
            self._delete_items(items)
        elif event.modifiers() & Qt.ControlModifier and key == Qt.Key_A and ordered:
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
            menu.addAction("Quitar", lambda: self._delete_items([item]))
        else:
            menu.addAction(f"Quitar {len(selected_items)} elementos", lambda: self._delete_items(selected_items))
        menu.exec(global_pos)

    def rename_item(self, item: dict) -> None:
        name, ok = QInputDialog.getText(self, "Renombrar", "Nuevo nombre:", text=item["name"])
        if ok and name.strip():
            item["name"] = name.strip()
            save_shortcuts(self.shortcuts)
            self.refresh()

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
        siblings = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        self.shortcuts.append({
            "id": new_item_id(),
            "type": "folder",
            "name": name.strip(),
            "parent_id": self.current_folder_id,
            "order": len(siblings),
            "color": None,
            "size": DEFAULT_SIZE,
            "category": None,
            "open_count": 0,
            "last_opened": 0.0,
        })
        save_shortcuts(self.shortcuts)
        self.refresh()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
