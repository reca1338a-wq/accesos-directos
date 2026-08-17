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
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPixmap
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
    new_item_id,
    portabilize_path,
    save_shortcuts,
)


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
    opened = Signal(dict)
    context_requested = Signal(dict, object)

    def __init__(self, item: dict, colors: dict, sibling_items: list[dict]) -> None:
        super().__init__()
        self.item = item
        self.colors = colors
        preset = SIZE_PRESETS.get(item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])

        self.setObjectName("tile")
        self.setFixedSize(preset["width"], preset["height"])
        self.setCursor(QCursor(Qt.PointingHandCursor))

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

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.opened.emit(self.item)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.item, event.globalPos())


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.colors = get_theme(self.settings.get("theme"))
        self.shortcuts = load_shortcuts()
        self.current_folder_id: str | None = None
        self.breadcrumb: list[tuple[str, str | None]] = [("Inicio", None)]

        self.setWindowTitle("Accesos Directos")
        self.resize(820, 600)
        self.setMinimumSize(480, 360)

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

        self.grid_container = QWidget()
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

        if not items:
            self.flow_layout.addWidget(self.empty_label)
            return

        for item in items:
            tile = TileWidget(item, self.colors, self.shortcuts)
            tile.opened.connect(self.open_item)
            tile.context_requested.connect(self.show_context_menu)
            self.flow_layout.addWidget(tile)

    # -- acciones ----------------------------------------------------

    def open_item(self, item: dict) -> None:
        if item["type"] == "folder":
            self.breadcrumb.append((item["name"], item["id"]))
            self.current_folder_id = item["id"]
            self.refresh()
            return
        try:
            open_path(item["path"])
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Archivo no encontrado", str(exc))
        except OSError as exc:
            QMessageBox.warning(self, "Error al abrir", str(exc))

    def go_back(self) -> None:
        if len(self.breadcrumb) <= 1:
            return
        self.breadcrumb.pop()
        self.current_folder_id = self.breadcrumb[-1][1]
        self.refresh()

    def show_context_menu(self, item: dict, global_pos) -> None:
        menu = QMenu(self)
        menu.addAction("Abrir", lambda: self.open_item(item))
        menu.addSeparator()
        menu.addAction("Renombrar...", lambda: self.rename_item(item))
        menu.addAction("Quitar", lambda: self.delete_item(item))
        menu.exec(global_pos)

    def rename_item(self, item: dict) -> None:
        name, ok = QInputDialog.getText(self, "Renombrar", "Nuevo nombre:", text=item["name"])
        if ok and name.strip():
            item["name"] = name.strip()
            save_shortcuts(self.shortcuts)
            self.refresh()

    def delete_item(self, item: dict) -> None:
        confirm = QMessageBox.question(self, "Quitar acceso", f"¿Quitar «{item['name']}»?")
        if confirm != QMessageBox.Yes:
            return
        self.shortcuts = [it for it in self.shortcuts if it["id"] != item["id"]]
        save_shortcuts(self.shortcuts)
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
