"""Accesos Directos — launcher de archivos y carpetas frecuentes."""

from __future__ import annotations

import os
import sys

# El .exe se compila como app "windowed" (sin consola, ver main.spec:
# console=False), y en ese modo Windows/PyInstaller dejan sys.stdout y
# sys.stderr en None. Si algo intenta escribir ahí (un print() nuestro, o
# el manejador de errores por defecto de Tkinter, que imprime a stderr
# cualquier excepción no capturada dentro de un callback) salta
# "AttributeError: 'NoneType' object has no attribute 'write'" — una
# excepción *nueva y distinta* de la original, que es la que PyInstaller
# acaba mostrando como "un error" al reiniciar o hacer casi cualquier cosa
# que dispare una excepción de fondo. Redirigirlos a un sumidero nulo evita
# ese crash secundario y dijamos ver el error real si hace falta depurar.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

import win_icons
from app_config import (
    DEFAULT_SIZE,
    GITHUB_OWNER,
    GITHUB_REPO,
    GITHUB_TOKEN,
    SHORTCUTS_PATH,
    SIZE_PRESETS,
    THEMES,
    USER_DATA_DIR,
    export_shortcuts,
    get_app_version,
    get_theme,
    import_shortcuts,
    load_settings,
    load_shortcuts,
    new_item_id,
    save_settings,
    save_shortcuts,
)
from github_updates import (
    WARM_RESTART_FLAG,
    UpdateError,
    apply_update,
    check_for_updates,
    cleanup_stale_update_files,
    restart_app,
    restart_with_update,
)

# Arrastrar archivos desde el Explorador de Windows requiere tkinterdnd2.
# Si no está instalado, la app sigue funcionando con normalidad, solo sin
# esa función (se instala automáticamente con requirements.txt / iniciar.bat).
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

# Tamaño en píxeles del icono real de Windows según el tamaño de tarjeta.
ICON_PIXEL_SIZES = {"small": 28, "medium": 32, "large": 40}

# Teclas que no deben redirigir el foco al buscador aunque produzcan un
# carácter "imprimible" (o vacío) al pulsarlas.
_SEARCH_IGNORED_KEYSYMS = {"Tab", "Return", "Escape", "BackSpace", "Delete"}

# Cada cuánto se comprueba si hay una versión nueva en segundo plano.
UPDATE_CHECK_INTERVAL_MS = 15 * 60 * 1000

# Distancia en píxeles que hay que mover el ratón antes de que un clic se
# considere un arrastre en vez de un clic normal.
DRAG_THRESHOLD = 6
# Ventana de tiempo (ms) para considerar dos clics como un doble clic.
DOUBLE_CLICK_MS = 420
TILE_GAP = 10


def open_path(path: str) -> None:
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"No se encontró: {target}")

    if sys.platform == "win32":
        os.startfile(str(target))  # noqa: S606
    elif sys.platform == "darwin":
        os.system(f'open "{target}"')  # noqa: S605
    else:
        os.system(f'xdg-open "{target}"')  # noqa: S605


def _readable_text_color(hex_color: str) -> str:
    """Devuelve negro o blanco, el que mejor se lea sobre ese color de fondo."""
    try:
        value = hex_color.lstrip("#")
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#1a1a1a" if luminance > 0.6 else "#f5f5f5"
    except (ValueError, IndexError):
        return "#f5f5f5"


# ---------------------------------------------------------------------------
# Tarjeta de acceso directo o carpeta
# ---------------------------------------------------------------------------


class Tile(tk.Frame):
    """Una tarjeta arrastrable que representa un acceso directo o una carpeta."""

    def __init__(self, master: tk.Misc, app: "AccesosDirectosApp", item: dict) -> None:
        colors = app.colors
        preset = SIZE_PRESETS.get(item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])
        custom_color = item.get("color")
        bg = custom_color or colors["surface"]
        text_color = _readable_text_color(custom_color) if custom_color else colors["text"]
        muted_color = text_color if custom_color else colors["text_muted"]

        self.selected = item["id"] in app.selected_ids
        border_color = colors["accent"] if self.selected else bg
        border_thickness = 3 if self.selected else 2

        super().__init__(master, bg=bg, highlightthickness=border_thickness, highlightbackground=border_color)
        self.app = app
        self.item = item
        self.base_bg = border_color
        self.preset = preset
        self._press_pos: tuple[int, int] | None = None
        self._dragging = False
        self._press_ctrl = False
        self._press_shift = False

        self._icon_photo = None  # referencia viva: evita que Tk la recolecte
        icon_photo = None
        if item["type"] == "shortcut":
            icon_size = ICON_PIXEL_SIZES.get(item.get("size", DEFAULT_SIZE), 32)
            icon_photo = win_icons.get_icon_photo(item.get("path", ""), icon_size)

        if icon_photo is not None:
            self._icon_photo = icon_photo
            tk.Label(self, image=icon_photo, bg=bg).pack(pady=(14, 2))
        else:
            icon = "📁" if item["type"] == "folder" else "📄"
            tk.Label(
                self, text=icon, font=("Segoe UI Emoji", 18), fg=text_color, bg=bg
            ).pack(pady=(14, 2))

        tk.Label(
            self,
            text=item["name"],
            font=("Segoe UI", 10, "bold"),
            fg=text_color,
            bg=bg,
            wraplength=preset["width"] - 16,
            justify="center",
        ).pack(fill="x", padx=6)

        subtitle = self._subtitle(colors)
        if subtitle:
            tk.Label(
                self,
                text=subtitle,
                font=("Segoe UI", 8),
                fg=muted_color,
                bg=bg,
                wraplength=preset["width"] - 16,
                justify="center",
            ).pack(fill="x", padx=6, pady=(2, 0))

        for widget in self._all_children():
            widget.bind("<ButtonPress-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_motion)
            widget.bind("<ButtonRelease-1>", self._on_release)
            widget.bind("<Button-3>", self._on_right_click)
            widget.configure(cursor="hand2")

    def _all_children(self) -> list[tk.Widget]:
        widgets: list[tk.Widget] = [self]
        widgets.extend(self.winfo_children())
        return widgets

    def _subtitle(self, colors: dict) -> str:
        if self.item["type"] == "folder":
            count = sum(
                1 for it in self.app.shortcuts if it["parent_id"] == self.item["id"]
            )
            return f"{count} elemento" + ("" if count == 1 else "s")
        return self.app.short_path(self.item.get("path", ""))

    def set_drop_highlight(self, on: bool) -> None:
        color = self.app.colors["accent"] if on else self.base_bg
        self.configure(highlightbackground=color, highlightthickness=3 if (on or self.selected) else 2)

    def set_dragging(self, on: bool) -> None:
        # Marca claramente cuál tarjeta se está moviendo: borde grueso de
        # color de acento, para que no haya duda de cuál es.
        color = self.app.colors["accent"] if on else self.base_bg
        self.configure(highlightbackground=color, highlightthickness=3 if (on or self.selected) else 2)

    # -- interacción -----------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        self._press_pos = None if self.app._is_searching() else (event.x_root, event.y_root)
        self._dragging = False
        self._press_ctrl = bool(event.state & 0x0004)
        self._press_shift = bool(event.state & 0x0001)

    def _on_motion(self, event: tk.Event) -> None:
        if self._press_pos is None:
            return
        dx = event.x_root - self._press_pos[0]
        dy = event.y_root - self._press_pos[1]
        if not self._dragging and (abs(dx) > DRAG_THRESHOLD or abs(dy) > DRAG_THRESHOLD):
            self._dragging = True
            self.app.begin_drag(self)
        if self._dragging:
            self.app.update_drag(event.x_root, event.y_root)

    def _on_release(self, event: tk.Event) -> None:
        if self._dragging:
            self.app.end_drag(event.x_root, event.y_root)
        elif self._press_ctrl:
            self.app.toggle_tile_selection(self)
        elif self._press_shift:
            self.app.extend_tile_selection(self)
        else:
            self.app.handle_tile_click(self)
        self._press_pos = None
        self._dragging = False

    def _on_right_click(self, event: tk.Event) -> None:
        self.app.show_tile_menu(self, event)


# ---------------------------------------------------------------------------
# Aplicación principal
# ---------------------------------------------------------------------------


class AccesosDirectosApp:
    def __init__(self) -> None:
        cleanup_stale_update_files()

        if WARM_RESTART_FLAG in sys.argv:
            # Venimos de un reinicio rápido (tras cambiar el tema o tras
            # actualizar): si pedimos los iconos reales de Windows
            # demasiado pronto tras arrancar el proceso, la caché de
            # iconos de Shell a veces todavía no está "caliente" y
            # devuelve el icono genérico en vez del real (por eso cerrar
            # y volver a abrir la app a mano —que tarda más— no tiene
            # este problema). Una pequeña espera aquí es suficiente.
            time.sleep(0.6)

        self.settings = load_settings()
        self.colors = get_theme(self.settings.get("theme"))
        self.shortcuts = load_shortcuts()

        self.current_folder_id: str | None = None
        self.breadcrumb: list[tuple[str, str | None]] = [("Inicio", None)]

        self.selected_ids: set[str] = set()
        self._selection_anchor_id: str | None = None

        self._update_running = False
        self._pending_update = None
        self._resize_after_id: str | None = None
        self._tile_by_id: dict[str, Tile] = {}
        self._drag_source: Tile | None = None
        self._drag_sources: list[dict] = []
        self._drag_target: Tile | None = None
        self._drag_mode: str | None = None  # "into" o "insert"
        self._drag_side: str | None = None  # "before" o "after"
        self._drag_target_kind: str | None = None  # "tile" o "breadcrumb"
        self._drag_breadcrumb_folder_id: str | None = None
        self._drag_breadcrumb_widget: tk.Label | None = None
        self._breadcrumb_targets: list[tuple[tk.Label, str | None]] = []
        self._drag_ghost: tk.Toplevel | None = None
        self._drop_indicator: tk.Frame | None = None
        self._last_click_time = 0.0
        self._last_click_id: str | None = None

        self.root = TkinterDnD.Tk() if _DND_AVAILABLE else tk.Tk()
        self.root.title("Accesos Directos")
        self.root.geometry("760x560")
        self.root.minsize(420, 320)
        self.root.configure(bg=self.colors["bg"])

        self._build_ui()
        self.root.update_idletasks()
        self._layout_tiles()

        self.root.bind_all("<KeyPress>", self._on_global_keypress, add="+")

        # Navegación con flechas por la cuadrícula de tarjetas (ver
        # _build_search_box para cómo se entra en este modo desde el
        # buscador). Se atan a la ventana principal, así que solo actúan
        # cuando el foco está en ella (no mientras se escribe en el
        # buscador ni dentro de un diálogo).
        self.root.bind("<Down>", self._on_grid_down)
        self.root.bind("<Up>", self._on_grid_up)
        self.root.bind("<Left>", self._on_grid_left)
        self.root.bind("<Right>", self._on_grid_right)
        self.root.bind("<Return>", self._on_grid_return)

        self.root.after(1500, self._maybe_check_updates_on_startup)
        self.root.after(UPDATE_CHECK_INTERVAL_MS, self._auto_check_loop)

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        colors = self.colors
        self._build_menu()

        header = tk.Frame(self.root, bg=colors["bg"], padx=20, pady=16)
        header.pack(fill="x")

        title_row = tk.Frame(header, bg=colors["bg"])
        title_row.pack(fill="x")

        tk.Label(
            title_row,
            text="Accesos Directos",
            font=("Segoe UI", 18, "bold"),
            fg=colors["accent"],
            bg=colors["bg"],
        ).pack(side="left")

        tk.Label(
            title_row,
            text=f"v{get_app_version()}",
            font=("Segoe UI", 9),
            fg=colors["text_muted"],
            bg=colors["bg"],
        ).pack(side="left", padx=(10, 0), pady=(6, 0))

        self._build_update_icon(title_row)

        self.breadcrumb_frame = tk.Frame(header, bg=colors["bg"])
        self.breadcrumb_frame.pack(fill="x", pady=(6, 0))
        self._render_breadcrumb()

        tk.Frame(self.root, bg=colors["surface"], height=1).pack(fill="x", padx=20)

        toolbar = tk.Frame(self.root, bg=colors["bg"], padx=20)
        toolbar.pack(fill="x", pady=(14, 10))

        ttk.Style().theme_use("clam")
        style = ttk.Style()
        style.configure(
            "Accent.TButton",
            background=colors["accent"],
            foreground=colors["bg"],
            font=("Segoe UI", 10, "bold"),
            padding=(14, 9),
            borderwidth=0,
        )
        style.map("Accent.TButton", background=[("active", colors["accent"])])

        ttk.Button(
            toolbar, text="+  Añadir", style="Accent.TButton", command=self.open_add_dialog
        ).pack(side="left")

        self._build_search_box(toolbar)

        hint_text = (
            "Arrastra sobre una carpeta o sobre la ruta de arriba para mover. "
            "Clic para seleccionar, Mayús + clic para un rango, Ctrl + clic para añadir/quitar uno."
        )
        if _DND_AVAILABLE:
            hint_text += " Arrastra un archivo desde el Explorador para crear su acceso."
        tk.Label(
            toolbar,
            text=hint_text,
            font=("Segoe UI", 8),
            fg=colors["text_muted"],
            bg=colors["bg"],
            wraplength=380,
            justify="left",
        ).pack(side="left", padx=(14, 0))

        canvas_frame = tk.Frame(self.root, bg=colors["bg"], padx=20, pady=8)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg=colors["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable = tk.Frame(self.canvas, bg=colors["bg"])

        self.canvas.create_window((0, 0), window=self.scrollable, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-1>", self._on_background_click, add="+")
        self.scrollable.bind("<Button-1>", self._on_background_click, add="+")
        self.root.bind("<Escape>", lambda _e: self._on_escape())

        if _DND_AVAILABLE:
            for target in (self.canvas, self.scrollable):
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                target.dnd_bind("<<DropLeave>>", self._on_drop_leave)
                target.dnd_bind("<<Drop>>", self._on_files_dropped)

        footer = tk.Frame(self.root, bg=colors["bg"], padx=20)
        footer.pack(fill="x", pady=(0, 12))
        tk.Label(
            footer,
            text=f"Datos guardados en: {USER_DATA_DIR}",
            font=("Segoe UI", 8),
            fg=colors["text_muted"],
            bg=colors["bg"],
            wraplength=720,
            justify="left",
        ).pack(anchor="w")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Añadir...", command=self.open_add_dialog)
        file_menu.add_command(label="Nueva carpeta...", command=self.create_folder_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Importar...", command=self.import_shortcuts_dialog)
        file_menu.add_command(label="Exportar...", command=self.export_shortcuts_dialog)
        file_menu.add_command(label="Editar lista (JSON)...", command=self.edit_config)
        file_menu.add_command(label="Recargar lista", command=self.reload)
        file_menu.add_separator()
        file_menu.add_command(label="Salir", command=self.root.quit)
        menubar.add_cascade(label="Archivo", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Buscar actualizaciones ahora", command=self.check_updates_dialog)
        self.auto_update_var = tk.BooleanVar(value=self.settings.get("auto_check_updates", True))
        help_menu.add_checkbutton(
            label="Buscar actualizaciones automáticamente",
            variable=self.auto_update_var,
            command=self._toggle_auto_updates,
        )
        help_menu.add_separator()
        help_menu.add_command(label="Acerca de...", command=self.show_about)
        menubar.add_cascade(label="Ayuda", menu=help_menu)

        menubar.add_command(label="⚙ Configuración", command=self.open_settings_dialog)

        self.root.config(menu=menubar)

    def _build_update_icon(self, parent: tk.Widget) -> None:
        colors = self.colors
        wrapper = tk.Frame(parent, bg=colors["bg"])
        wrapper.pack(side="right")

        self.update_icon = tk.Label(
            wrapper,
            text="🔄",
            font=("Segoe UI Emoji", 14),
            fg=colors["text_muted"],
            bg=colors["bg"],
            cursor="hand2",
        )
        self.update_icon.pack()
        self.update_icon.bind("<Button-1>", lambda _e: self.check_updates_dialog())
        self._add_tooltip(self.update_icon, "Buscar actualizaciones")

        self.update_badge = tk.Canvas(
            wrapper, width=8, height=8, bg=colors["bg"], highlightthickness=0
        )
        self.update_badge.create_oval(0, 0, 8, 8, fill=colors["danger"], outline="")

    def _build_search_box(self, parent: tk.Widget) -> None:
        colors = self.colors
        wrap = tk.Frame(parent, bg=colors["surface"])
        wrap.pack(side="right")

        tk.Label(
            wrap, text="🔍", font=("Segoe UI", 10), fg=colors["text_muted"], bg=colors["surface"],
        ).pack(side="left", padx=(10, 2))

        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            wrap,
            textvariable=self.search_var,
            font=("Segoe UI", 10),
            fg=colors["text"],
            bg=colors["surface"],
            insertbackground=colors["text"],
            relief="flat",
            width=22,
            highlightthickness=0,
        )
        self.search_entry.pack(side="left", ipady=5)
        self.search_var.trace_add("write", lambda *_a: self._on_search_changed())
        self.search_entry.bind("<Return>", self._on_search_enter)
        self.search_entry.bind("<Down>", self._on_search_down)

        self.search_clear_btn = tk.Label(
            wrap, text="✕", font=("Segoe UI", 9), fg=colors["text_muted"], bg=colors["surface"],
            cursor="hand2",
        )
        self.search_clear_btn.pack(side="left", padx=(4, 10))
        self.search_clear_btn.bind("<Button-1>", lambda _e: self._clear_search())

        self._add_tooltip(
            self.search_entry, "Buscar accesos directos (o simplemente empieza a escribir)"
        )

    def _add_tooltip(self, widget: tk.Widget, text: str) -> None:
        colors = self.colors
        state = {"win": None}

        def show(_event) -> None:
            win = tk.Toplevel(widget)
            win.overrideredirect(True)
            win.configure(bg=colors["surface"])
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            win.geometry(f"+{x}+{y}")
            tk.Label(
                win, text=text, font=("Segoe UI", 8), fg=colors["text"], bg=colors["surface"],
                padx=6, pady=3,
            ).pack()
            state["win"] = win

        def hide(_event) -> None:
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def short_path(self, path: str) -> str:
        home = str(Path.home())
        if path.startswith(home):
            return "~" + path[len(home) :]
        return path

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------

    def _on_search_changed(self) -> None:
        self._render_breadcrumb()
        # Al escribir en el buscador, resaltamos siempre el primer
        # resultado como "elegido por defecto": así Intro abre ese acceso
        # sin tener que tocar el ratón ni las flechas.
        if self._is_searching():
            items = self._visible_items()
            if items:
                self.selected_ids = {items[0]["id"]}
                self._selection_anchor_id = items[0]["id"]
            else:
                self.selected_ids.clear()
                self._selection_anchor_id = None
        else:
            self.selected_ids.clear()
            self._selection_anchor_id = None
        self._layout_tiles()

    def _clear_search(self) -> None:
        self.search_var.set("")
        self.root.focus_set()

    # ------------------------------------------------------------------
    # Navegación por teclado desde el buscador y por la cuadrícula
    # ------------------------------------------------------------------

    def _selected_visible_item(self) -> dict | None:
        """El único elemento actualmente resaltado, si sigue siendo
        visible con el filtro/carpeta actual (None si no hay ninguno o
        hay varios seleccionados)."""
        if len(self.selected_ids) != 1:
            return None
        item_id = next(iter(self.selected_ids))
        return next((it for it in self._visible_items() if it["id"] == item_id), None)

    def _select_item_id(self, item_id: str, *, scroll: bool = True) -> None:
        self.selected_ids = {item_id}
        self._selection_anchor_id = item_id
        self._layout_tiles()
        if scroll:
            self._scroll_tile_into_view(item_id)

    def _scroll_tile_into_view(self, item_id: str) -> None:
        tile = self._tile_by_id.get(item_id)
        if tile is None:
            return
        self.canvas.update_idletasks()
        canvas_height = self.canvas.winfo_height()
        scroll_height = max(self.scrollable.winfo_height(), 1)
        tile_top = tile.winfo_y()
        tile_bottom = tile_top + tile.winfo_height()
        top, bottom = self.canvas.yview()
        visible_top = top * scroll_height
        visible_bottom = bottom * scroll_height
        if tile_top < visible_top:
            self.canvas.yview_moveto(max(0, tile_top - TILE_GAP) / scroll_height)
        elif tile_bottom > visible_bottom:
            self.canvas.yview_moveto(
                max(0, tile_bottom + TILE_GAP - canvas_height) / scroll_height
            )

    def _on_search_enter(self, _event=None) -> str:
        """Intro en el buscador: abre el resultado resaltado (el primero
        por defecto) sin necesidad de tocar el ratón."""
        items = self._visible_items()
        if not items:
            return "break"
        target = self._selected_visible_item() or items[0]
        self.open_item(target)
        return "break"

    def _on_search_down(self, _event=None) -> str:
        """Flecha abajo en el buscador: baja el foco a la cuadrícula de
        resultados, donde ya se puede navegar con las flechas."""
        items = self._visible_items()
        if not items:
            return "break"
        current = self._selected_visible_item()
        if current is None:
            self._select_item_id(items[0]["id"])
        self.root.focus_set()
        return "break"

    def _tile_rows(self) -> list[list[dict]]:
        """Agrupa los elementos visibles en filas, en el mismo orden en
        que _layout_tiles los coloca, para poder mover la selección hacia
        arriba/abajo de forma coherente con lo que se ve en pantalla."""
        items = self._visible_items()
        canvas_width = max(self.canvas.winfo_width(), 240)
        rows: list[list[dict]] = []
        current_row: list[dict] = []
        x = TILE_GAP
        for item in items:
            preset = SIZE_PRESETS.get(item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])
            width = preset["width"]
            if x + width + TILE_GAP > canvas_width and x > TILE_GAP:
                rows.append(current_row)
                current_row = []
                x = TILE_GAP
            current_row.append(item)
            x += width + TILE_GAP
        if current_row:
            rows.append(current_row)
        return rows

    def _focus_is_text_entry(self) -> bool:
        focus = self.root.focus_get()
        return isinstance(focus, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox))

    def _on_grid_left(self, _event=None) -> str | None:
        if self._focus_is_text_entry():
            return None
        items = self._visible_items()
        if not items:
            return "break"
        ids = [it["id"] for it in items]
        current = self._selected_visible_item()
        idx = ids.index(current["id"]) if current else 0
        self._select_item_id(ids[max(0, idx - 1)])
        return "break"

    def _on_grid_right(self, _event=None) -> str | None:
        if self._focus_is_text_entry():
            return None
        items = self._visible_items()
        if not items:
            return "break"
        ids = [it["id"] for it in items]
        current = self._selected_visible_item()
        idx = ids.index(current["id"]) if current else -1
        self._select_item_id(ids[min(len(ids) - 1, idx + 1)])
        return "break"

    def _on_grid_down(self, _event=None) -> str | None:
        if self._focus_is_text_entry():
            return None
        rows = self._tile_rows()
        if not rows:
            return "break"
        current = self._selected_visible_item()
        if current is None:
            self._select_item_id(rows[0][0]["id"])
            return "break"
        for row_idx, row in enumerate(rows):
            for col_idx, item in enumerate(row):
                if item["id"] == current["id"]:
                    if row_idx + 1 < len(rows):
                        next_row = rows[row_idx + 1]
                        target = next_row[min(col_idx, len(next_row) - 1)]
                        self._select_item_id(target["id"])
                    return "break"
        self._select_item_id(rows[0][0]["id"])
        return "break"

    def _on_grid_up(self, _event=None) -> str | None:
        if self._focus_is_text_entry():
            return None
        rows = self._tile_rows()
        if not rows:
            return "break"
        current = self._selected_visible_item()
        if current is None:
            self._select_item_id(rows[0][0]["id"])
            return "break"
        for row_idx, row in enumerate(rows):
            for col_idx, item in enumerate(row):
                if item["id"] == current["id"]:
                    if row_idx == 0:
                        # Ya estamos en la primera fila: volvemos el foco
                        # al buscador, como haría cualquier lista con
                        # cabecera de búsqueda.
                        self.search_entry.focus_set()
                        self.search_entry.icursor("end")
                    else:
                        prev_row = rows[row_idx - 1]
                        target = prev_row[min(col_idx, len(prev_row) - 1)]
                        self._select_item_id(target["id"])
                    return "break"
        self._select_item_id(rows[0][0]["id"])
        return "break"

    def _on_grid_return(self, _event=None) -> str | None:
        if self._focus_is_text_entry():
            return None
        items = self._visible_items()
        if not items:
            return "break"
        target = self._selected_visible_item() or items[0]
        self.open_item(target)
        return "break"

    def _on_escape(self) -> None:
        if self.search_var.get():
            self._clear_search()
            return
        self._clear_selection()

    def _on_global_keypress(self, event: tk.Event) -> None:
        # Si hay un diálogo abierto (renombrar, crear carpeta/acceso,
        # ajustes, elegir color...) no interceptamos nada: que la persona
        # pueda escribir con normalidad en ese diálogo.
        widget = event.widget
        try:
            toplevel = widget.winfo_toplevel()
        except Exception:
            toplevel = None
        if toplevel is not None and toplevel is not self.root:
            return

        # Si ya se está escribiendo en un campo de texto (incluido el
        # propio buscador), dejamos que Tk lo gestione de forma normal.
        focus = self.root.focus_get()
        if isinstance(focus, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox)):
            return

        char = event.char
        if not char or not char.isprintable():
            return  # Teclas como Ctrl, Alt, Mayús o flechas no producen esto.
        if event.keysym in _SEARCH_IGNORED_KEYSYMS:
            return

        self.search_entry.focus_set()
        self.search_var.set(self.search_var.get() + char)
        self.search_entry.icursor("end")

    # ------------------------------------------------------------------
    # Arrastrar archivos desde el Explorador de Windows
    # ------------------------------------------------------------------

    def _on_drop_enter(self, _event) -> None:
        self.canvas.configure(highlightthickness=2, highlightbackground=self.colors["accent"])

    def _on_drop_leave(self, _event) -> None:
        self.canvas.configure(highlightthickness=0)

    def _on_files_dropped(self, event) -> None:
        self._on_drop_leave(event)
        if self._is_searching():
            self.search_var.set("")

        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]

        siblings = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        next_order = len(siblings)
        added = 0

        for raw_path in paths:
            candidate = Path(raw_path)
            if not candidate.exists():
                continue
            self.shortcuts.append(
                {
                    "id": new_item_id(),
                    "type": "shortcut",
                    "name": candidate.name or str(candidate),
                    "path": str(candidate),
                    "parent_id": self.current_folder_id,
                    "order": next_order,
                    "color": None,
                    "size": DEFAULT_SIZE,
                }
            )
            next_order += 1
            added += 1

        if added:
            save_shortcuts(self.shortcuts)
            self._layout_tiles()

    # ------------------------------------------------------------------
    # Navegación de carpetas
    # ------------------------------------------------------------------

    def _render_breadcrumb(self) -> None:
        colors = self.colors
        for child in self.breadcrumb_frame.winfo_children():
            child.destroy()
        self._breadcrumb_targets = []

        query = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        if query:
            tk.Label(
                self.breadcrumb_frame,
                text=f"🔍  Resultados para «{query}»",
                font=("Segoe UI", 9, "bold"),
                fg=colors["accent"],
                bg=colors["bg"],
            ).pack(side="left")
            return

        for index, (name, folder_id) in enumerate(self.breadcrumb):
            is_last = index == len(self.breadcrumb) - 1
            label = tk.Label(
                self.breadcrumb_frame,
                text=name,
                font=("Segoe UI", 9, "bold" if is_last else "normal"),
                fg=colors["text"] if is_last else colors["text_muted"],
                bg=colors["bg"],
                cursor="hand2" if not is_last else "arrow",
            )
            label.pack(side="left")
            if not is_last:
                label.bind("<Button-1>", lambda _e, i=index: self._go_to_breadcrumb(i))
                self._breadcrumb_targets.append((label, folder_id))
                tk.Label(
                    self.breadcrumb_frame, text="  ›  ", font=("Segoe UI", 9),
                    fg=colors["text_muted"], bg=colors["bg"],
                ).pack(side="left")

    def _go_to_breadcrumb(self, index: int) -> None:
        self.breadcrumb = self.breadcrumb[: index + 1]
        self.current_folder_id = self.breadcrumb[-1][1]
        self.selected_ids.clear()
        self._selection_anchor_id = None
        self._render_breadcrumb()
        self._layout_tiles()

    def navigate_into(self, folder_item: dict) -> None:
        self.search_var.set("")
        self.breadcrumb.append((folder_item["name"], folder_item["id"]))
        self.current_folder_id = folder_item["id"]
        self.selected_ids.clear()
        self._selection_anchor_id = None
        self._render_breadcrumb()
        self._layout_tiles()

    def _jump_to_folder(self, folder_item: dict) -> None:
        """Navega directamente a una carpeta encontrada por búsqueda,
        reconstruyendo la ruta completa de migas de pan hasta ella."""
        chain = [folder_item]
        parent_id = folder_item["parent_id"]
        while parent_id is not None:
            parent = next((it for it in self.shortcuts if it["id"] == parent_id), None)
            if parent is None:
                break
            chain.append(parent)
            parent_id = parent["parent_id"]
        chain.reverse()

        self.breadcrumb = [("Inicio", None)] + [(it["name"], it["id"]) for it in chain]
        self.current_folder_id = folder_item["id"]
        self.selected_ids.clear()
        self._selection_anchor_id = None
        self._render_breadcrumb()
        self._layout_tiles()

    # ------------------------------------------------------------------
    # Diseño responsivo tipo "tarjetas" (estilo Power BI)
    # ------------------------------------------------------------------

    def _on_canvas_configure(self, _event: tk.Event) -> None:
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        # Pequeño margen de espera para no recalcular en cada píxel mientras
        # se arrastra el borde de la ventana: mantiene la app fluida.
        self._resize_after_id = self.root.after(80, self._layout_tiles)

    def _on_background_click(self, event: tk.Event) -> None:
        # Solo cuenta como "vacío" si el clic no cayó sobre una tarjeta
        # (las tarjetas están colocadas encima con .place, pero un clic en
        # el hueco entre ellas llega hasta el frame de fondo).
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if self._find_tile_ancestor(widget) is not None:
            return
        if self.selected_ids:
            self._clear_selection()

    def _visible_items(self) -> list[dict]:
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        if query:
            matches = [it for it in self.shortcuts if query in it["name"].lower()]
            matches.sort(key=lambda it: it["name"].lower())
            return matches

        items = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        items.sort(key=lambda it: it.get("order", 0))
        return items

    def _is_searching(self) -> bool:
        return bool(self.search_var.get().strip()) if hasattr(self, "search_var") else False

    def _layout_tiles(self) -> None:
        self._resize_after_id = None
        for child in self.scrollable.winfo_children():
            child.destroy()

        items = self._visible_items()
        canvas_width = max(self.canvas.winfo_width(), 240)

        if not items:
            self._tile_by_id = {}
            empty_text = (
                "No se encontraron accesos directos."
                if self._is_searching()
                else "Esta carpeta está vacía.\nUsa «+ Añadir» para empezar."
            )
            tk.Label(
                self.scrollable,
                text=empty_text,
                font=("Segoe UI", 11),
                fg=self.colors["text_muted"],
                bg=self.colors["bg"],
                justify="center",
            ).place(x=20, y=30)
            self.scrollable.configure(width=canvas_width, height=140)
            self.canvas.configure(scrollregion=(0, 0, canvas_width, 140))
            return

        x = TILE_GAP
        y = TILE_GAP
        row_height = 0
        self._tile_by_id = {}
        for item in items:
            preset = SIZE_PRESETS.get(item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])
            width, height = preset["width"], preset["height"]
            if x + width + TILE_GAP > canvas_width and x > TILE_GAP:
                x = TILE_GAP
                y += row_height + TILE_GAP
                row_height = 0

            tile = Tile(self.scrollable, self, item)
            tile.place(x=x, y=y, width=width, height=height)
            self._tile_by_id[item["id"]] = tile

            x += width + TILE_GAP
            row_height = max(row_height, height)

        total_height = y + row_height + TILE_GAP
        self.scrollable.configure(width=canvas_width, height=total_height)
        self.canvas.configure(scrollregion=(0, 0, canvas_width, total_height))

    # ------------------------------------------------------------------
    # Selección de tarjetas (varias a la vez)
    # ------------------------------------------------------------------

    def toggle_tile_selection(self, tile: Tile) -> None:
        item_id = tile.item["id"]
        if item_id in self.selected_ids:
            self.selected_ids.discard(item_id)
        else:
            self.selected_ids.add(item_id)
        self._selection_anchor_id = item_id
        self._layout_tiles()

    def extend_tile_selection(self, tile: Tile) -> None:
        ids_order = [it["id"] for it in self._visible_items()]
        anchor_id = self._selection_anchor_id
        if anchor_id not in ids_order:
            anchor_id = tile.item["id"]
        start = ids_order.index(anchor_id)
        end = ids_order.index(tile.item["id"])
        if start > end:
            start, end = end, start
        self.selected_ids = set(ids_order[start : end + 1])
        self._layout_tiles()

    def _clear_selection(self) -> None:
        if not self.selected_ids and self._selection_anchor_id is None:
            return
        self.selected_ids.clear()
        self._selection_anchor_id = None
        self._layout_tiles()

    # ------------------------------------------------------------------
    # Clic / doble clic (según preferencia) y arrastre
    # ------------------------------------------------------------------

    def handle_tile_click(self, tile: Tile) -> None:
        item_id = tile.item["id"]
        self.selected_ids = {item_id}
        self._selection_anchor_id = item_id
        self._layout_tiles()

        mode = self.settings.get("click_mode", "double")
        if mode == "single":
            self.open_item(tile.item)
            return

        now = time.monotonic() * 1000
        if self._last_click_id == item_id and now - self._last_click_time <= DOUBLE_CLICK_MS:
            self._last_click_time = 0
            self._last_click_id = None
            self.open_item(tile.item)
        else:
            self._last_click_time = now
            self._last_click_id = item_id

    def open_item(self, item: dict) -> None:
        if item["type"] == "folder":
            if self._is_searching():
                self._jump_to_folder(item)
            else:
                self.navigate_into(item)
            return
        try:
            open_path(item["path"])
        except FileNotFoundError as exc:
            messagebox.showerror("Archivo no encontrado", str(exc))
        except OSError as exc:
            messagebox.showerror("Error al abrir", str(exc))

    def begin_drag(self, tile: Tile) -> None:
        if self._is_searching():
            return
        self._drag_source = tile
        if tile.item["id"] in self.selected_ids and len(self.selected_ids) > 1:
            self._drag_sources = [
                it for it in self.shortcuts if it["id"] in self.selected_ids
            ]
        else:
            self._drag_sources = [tile.item]

        dragged_ids = {it["id"] for it in self._drag_sources}
        for item_id in dragged_ids:
            source_tile = self._tile_by_id.get(item_id)
            if source_tile is not None:
                source_tile.configure(cursor="fleur")
                source_tile.set_dragging(True)

        self._create_drag_ghost(self._drag_sources)
        self._drop_indicator = tk.Frame(self.scrollable, bg=self.colors["accent"])

    def _create_drag_ghost(self, items: list[dict]) -> None:
        colors = self.colors
        ghost = tk.Toplevel(self.root)
        ghost.overrideredirect(True)
        try:
            ghost.attributes("-topmost", True)
        except tk.TclError:
            pass
        ghost.configure(bg=colors["accent"])
        if len(items) == 1:
            icon = "📁" if items[0]["type"] == "folder" else "📄"
            text = f"{icon}  {items[0]['name']}"
        else:
            text = f"🗃  {len(items)} elementos"
        tk.Label(
            ghost,
            text=text,
            font=("Segoe UI", 9, "bold"),
            fg=colors["bg"],
            bg=colors["accent"],
            padx=10,
            pady=6,
        ).pack()
        self._drag_ghost = ghost

    def _match_breadcrumb(self, widget) -> tuple[bool, str | None]:
        for label, folder_id in self._breadcrumb_targets:
            if widget is label:
                return True, folder_id
        return False, None

    def _set_breadcrumb_highlight(self, widget: tk.Label | None) -> None:
        if self._drag_breadcrumb_widget is not None and self._drag_breadcrumb_widget is not widget:
            self._drag_breadcrumb_widget.configure(
                fg=self.colors["text_muted"], font=("Segoe UI", 9, "normal")
            )
        if widget is not None:
            widget.configure(fg=self.colors["accent"], font=("Segoe UI", 9, "bold"))
        self._drag_breadcrumb_widget = widget

    def update_drag(self, x_root: int, y_root: int) -> None:
        # La tarjeta fantasma sigue al cursor con un pequeño desplazamiento
        # para no taparlo, dejando claro qué se está moviendo.
        if self._drag_ghost is not None:
            self._drag_ghost.geometry(f"+{x_root + 16}+{y_root + 12}")

        widget = self.root.winfo_containing(x_root, y_root)
        dragged_ids = {it["id"] for it in self._drag_sources}

        is_breadcrumb, breadcrumb_folder_id = self._match_breadcrumb(widget)
        if is_breadcrumb:
            if self._drag_target is not None:
                self._drag_target.set_drop_highlight(False)
                self._drag_target = None
            self._drag_mode = None
            self._drag_side = None
            self._drag_target_kind = "breadcrumb"
            self._drag_breadcrumb_folder_id = breadcrumb_folder_id
            self._set_breadcrumb_highlight(widget)
            if self._drop_indicator is not None:
                self._drop_indicator.place_forget()
            return

        self._set_breadcrumb_highlight(None)
        self._drag_target_kind = "tile"

        target = self._find_tile_ancestor(widget)
        if target is not None and target.item["id"] in dragged_ids:
            target = None

        mode: str | None = None
        side: str | None = None
        if target is not None:
            local_x = x_root - target.winfo_rootx()
            ratio = local_x / max(target.winfo_width(), 1)
            if target.item["type"] == "folder" and 0.22 <= ratio <= 0.78:
                mode = "into"
            else:
                mode = "insert"
                side = "before" if ratio < 0.5 else "after"

        if self._drag_target is not None and self._drag_target is not target:
            self._drag_target.set_drop_highlight(False)
        if target is not None:
            target.set_drop_highlight(mode == "into")

        self._drag_target = target
        self._drag_mode = mode
        self._drag_side = side

        if self._drop_indicator is None:
            return
        if target is not None and mode == "insert":
            x = (
                target.winfo_x() - 2
                if side == "before"
                else target.winfo_x() + target.winfo_width() - 2
            )
            self._drop_indicator.place(x=x, y=target.winfo_y(), width=4, height=target.winfo_height())
            self._drop_indicator.lift()
        else:
            self._drop_indicator.place_forget()

    def end_drag(self, x_root: int, y_root: int) -> None:
        self.update_drag(x_root, y_root)
        sources = self._drag_sources
        target = self._drag_target
        mode = self._drag_mode
        side = self._drag_side
        target_kind = self._drag_target_kind
        breadcrumb_folder_id = self._drag_breadcrumb_folder_id

        if target is not None:
            target.set_drop_highlight(False)
        self._set_breadcrumb_highlight(None)
        if self._drop_indicator is not None:
            self._drop_indicator.destroy()
            self._drop_indicator = None
        if self._drag_ghost is not None:
            self._drag_ghost.destroy()
            self._drag_ghost = None

        for item in sources:
            source_tile = self._tile_by_id.get(item["id"])
            if source_tile is not None:
                source_tile.configure(cursor="hand2")
                source_tile.set_dragging(False)

        self._drag_source = None
        self._drag_sources = []
        self._drag_target = None
        self._drag_mode = None
        self._drag_side = None
        self._drag_target_kind = None
        self._drag_breadcrumb_folder_id = None

        if not sources:
            return

        if target_kind == "breadcrumb":
            self._move_items_to_folder(sources, breadcrumb_folder_id)
            return

        if target is None:
            # Soltado en un hueco vacío: se colocan al final de esta carpeta.
            self._move_items_to_folder(sources, self.current_folder_id)
            return

        if mode == "into":
            self._move_items_to_folder(sources, target.item["id"])
        else:
            self._insert_items(sources, target.item, side)

    def _find_tile_ancestor(self, widget) -> Tile | None:
        while widget is not None:
            if isinstance(widget, Tile):
                return widget
            widget = getattr(widget, "master", None)
        return None

    def _move_items_to_folder(self, items: list[dict], folder_id: str | None) -> None:
        moved_ids = {it["id"] for it in items}
        items_sorted = sorted(items, key=lambda it: it.get("order", 0))
        existing = [
            it for it in self.shortcuts if it["parent_id"] == folder_id and it["id"] not in moved_ids
        ]
        start = len(existing)
        for offset, it in enumerate(items_sorted):
            it["parent_id"] = folder_id
            it["order"] = start + offset

        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def _insert_items(self, items: list[dict], target: dict, side: str | None) -> None:
        parent_id = target["parent_id"]
        moved_ids = {it["id"] for it in items}
        siblings = [
            it for it in self.shortcuts if it["parent_id"] == parent_id and it["id"] not in moved_ids
        ]
        siblings.sort(key=lambda it: it.get("order", 0))
        target_index = next(
            (idx for idx, it in enumerate(siblings) if it["id"] == target["id"]), len(siblings)
        )
        insert_at = target_index if side == "before" else target_index + 1

        items_sorted = sorted(items, key=lambda it: it.get("order", 0))
        for it in items_sorted:
            it["parent_id"] = parent_id
        siblings[insert_at:insert_at] = items_sorted
        for idx, it in enumerate(siblings):
            it["order"] = idx

        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    # ------------------------------------------------------------------
    # Menú contextual de cada tarjeta
    # ------------------------------------------------------------------

    def show_tile_menu(self, tile: Tile, event: tk.Event) -> None:
        item = tile.item

        if item["id"] in self.selected_ids and len(self.selected_ids) > 1:
            self._show_multi_tile_menu(event)
            return

        if self.selected_ids:
            self.selected_ids.clear()
            self._selection_anchor_id = None
            self._layout_tiles()

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Abrir", command=lambda: self.open_item(item))

        size_menu = tk.Menu(menu, tearoff=0)
        for key, preset in SIZE_PRESETS.items():
            size_menu.add_command(
                label=preset["label"], command=lambda k=key: self._set_item_size(item, k)
            )
        menu.add_cascade(label="Tamaño", menu=size_menu)

        menu.add_command(label="Cambiar color...", command=lambda: self._set_item_color(item))
        menu.add_command(
            label="Restablecer color del tema",
            command=lambda: self._clear_item_color(item),
            state="normal" if item.get("color") else "disabled",
        )

        menu.add_command(label="Renombrar...", command=lambda: self._rename_item(item))

        if self.current_folder_id is not None:
            menu.add_command(
                label="Mover a la carpeta superior",
                command=lambda: self._move_item_to_parent(item),
            )

        menu.add_separator()

        if item["type"] == "folder":
            menu.add_command(label="Eliminar carpeta...", command=lambda: self._delete_folder(item))
        else:
            menu.add_command(label="Quitar acceso", command=lambda: self._delete_shortcut(item))

        menu.tk_popup(event.x_root, event.y_root)

    def _show_multi_tile_menu(self, event: tk.Event) -> None:
        ids = set(self.selected_ids)
        items = [it for it in self.shortcuts if it["id"] in ids]
        if not items:
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"{len(items)} elementos seleccionados", state="disabled")
        menu.add_separator()

        if self.current_folder_id is not None:
            menu.add_command(
                label="Mover a la carpeta superior",
                command=lambda: self._move_items_to_parent(items),
            )

        menu.add_command(
            label="Restablecer color del tema",
            command=lambda: self._clear_items_color(items),
        )
        menu.add_separator()
        menu.add_command(
            label=f"Eliminar {len(items)} elementos...",
            command=lambda: self._delete_items(items),
        )
        menu.add_separator()
        menu.add_command(label="Deseleccionar todo", command=self._clear_selection)

        menu.tk_popup(event.x_root, event.y_root)

    def _set_item_size(self, item: dict, size: str) -> None:
        item["size"] = size
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def _set_item_color(self, item: dict) -> None:
        initial = item.get("color") or self.colors["surface"]
        _rgb, hex_color = colorchooser.askcolor(color=initial, title="Elige un color")
        if hex_color:
            item["color"] = hex_color
            save_shortcuts(self.shortcuts)
            self._layout_tiles()

    def _clear_item_color(self, item: dict) -> None:
        item["color"] = None
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def _move_item_to_parent(self, item: dict) -> None:
        self._move_items_to_parent([item])

    def _move_items_to_parent(self, items: list[dict]) -> None:
        # Solo tiene sentido dentro de una carpeta: sube los elementos al
        # nivel que contiene a la carpeta actual (su "carpeta madre").
        if self.current_folder_id is None:
            return
        current_folder = next(
            (it for it in self.shortcuts if it["id"] == self.current_folder_id), None
        )
        grandparent_id = current_folder["parent_id"] if current_folder else None
        self._move_items_to_folder(items, grandparent_id)

    def _clear_items_color(self, items: list[dict]) -> None:
        for item in items:
            item["color"] = None
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def _rename_item(self, item: dict) -> None:
        new_name = self._prompt_name(item["name"], title="Renombrar")
        if new_name:
            item["name"] = new_name
            save_shortcuts(self.shortcuts)
            self._layout_tiles()

    def _delete_shortcut(self, item: dict) -> None:
        if not messagebox.askyesno("Quitar acceso", f"¿Quitar «{item['name']}»?"):
            return
        self.shortcuts = [it for it in self.shortcuts if it["id"] != item["id"]]
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def _delete_folder(self, item: dict) -> None:
        contents = [it for it in self.shortcuts if it["parent_id"] == item["id"]]
        if contents:
            proceed = messagebox.askyesno(
                "Eliminar carpeta",
                f"«{item['name']}» contiene {len(contents)} elemento(s).\n\n"
                "Se moverán fuera de la carpeta (no se borrará su contenido). "
                "¿Continuar?",
            )
            if not proceed:
                return
            for child in contents:
                child["parent_id"] = item["parent_id"]
        else:
            if not messagebox.askyesno("Eliminar carpeta", f"¿Eliminar la carpeta «{item['name']}»?"):
                return

        self.shortcuts = [it for it in self.shortcuts if it["id"] != item["id"]]
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def _delete_items(self, items: list[dict]) -> None:
        names_preview = ", ".join(it["name"] for it in items[:5])
        if len(items) > 5:
            names_preview += "…"
        if not messagebox.askyesno(
            "Eliminar elementos",
            f"¿Eliminar {len(items)} elementos?\n\n{names_preview}\n\n"
            "El contenido de las carpetas eliminadas se moverá fuera de ellas "
            "(no se borrará).",
        ):
            return

        ids = {it["id"] for it in items}
        for item in items:
            if item["type"] == "folder":
                children = [c for c in self.shortcuts if c["parent_id"] == item["id"]]
                for child in children:
                    child["parent_id"] = item["parent_id"]

        self.shortcuts = [it for it in self.shortcuts if it["id"] not in ids]
        self.selected_ids.clear()
        self._selection_anchor_id = None
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    # ------------------------------------------------------------------
    # Añadir accesos y carpetas
    # ------------------------------------------------------------------

    def open_add_dialog(self) -> None:
        colors = self.colors
        dialog = tk.Toplevel(self.root)
        dialog.title("Añadir")
        dialog.configure(bg=colors["bg"])
        dialog.geometry("400x330")
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog, text="¿Qué quieres añadir?", font=("Segoe UI", 12, "bold"),
            fg=colors["text"], bg=colors["bg"],
        ).pack(anchor="w", padx=18, pady=(18, 10))

        def option_card(icon: str, title: str, subtitle: str, command) -> None:
            card = tk.Frame(dialog, bg=colors["surface"], cursor="hand2")
            card.pack(fill="x", padx=18, pady=6)
            inner = tk.Frame(card, bg=colors["surface"])
            inner.pack(fill="x", padx=12, pady=10)
            tk.Label(
                inner, text=icon, font=("Segoe UI Emoji", 16), fg=colors["text"], bg=colors["surface"],
            ).pack(side="left", padx=(0, 10))
            text_col = tk.Frame(inner, bg=colors["surface"])
            text_col.pack(side="left", fill="x", expand=True)
            tk.Label(
                text_col, text=title, font=("Segoe UI", 10, "bold"), fg=colors["text"],
                bg=colors["surface"], anchor="w",
            ).pack(fill="x")
            tk.Label(
                text_col, text=subtitle, font=("Segoe UI", 8), fg=colors["text_muted"],
                bg=colors["surface"], anchor="w", wraplength=300, justify="left",
            ).pack(fill="x")

            def trigger(_event=None) -> None:
                dialog.destroy()
                command()

            for widget in (card, inner, text_col, *text_col.winfo_children()):
                widget.bind("<Button-1>", trigger)

        option_card(
            "📄", "Acceso a un archivo", "Crea una tarjeta que abre un archivo concreto.",
            self._add_file_shortcut,
        )
        option_card(
            "📁", "Acceso a una carpeta del sistema",
            "Crea una tarjeta que abre una carpeta de Windows (ej. Documentos).",
            self._add_folder_shortcut,
        )
        option_card(
            "🗂", "Carpeta para organizar",
            "Agrupa varias tarjetas dentro, como una carpeta interna de la app.",
            self.create_folder_dialog,
        )

        ttk.Button(dialog, text="Cancelar", command=dialog.destroy).pack(pady=(6, 14))

    def _add_file_shortcut(self) -> None:
        path = filedialog.askopenfilename(title="Selecciona un archivo", initialdir=str(Path.home()))
        if not path:
            return
        self._finish_add_shortcut(path)

    def _add_folder_shortcut(self) -> None:
        path = filedialog.askdirectory(title="Selecciona una carpeta", initialdir=str(Path.home()))
        if not path:
            return
        self._finish_add_shortcut(path)

    def _finish_add_shortcut(self, path: str) -> None:
        name = self._prompt_name(Path(path).name, title="Nombre del acceso")
        if not name:
            return
        siblings = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        self.shortcuts.append(
            {
                "id": new_item_id(),
                "type": "shortcut",
                "name": name,
                "path": path,
                "parent_id": self.current_folder_id,
                "order": len(siblings),
                "color": None,
                "size": DEFAULT_SIZE,
            }
        )
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def create_folder_dialog(self) -> None:
        name = self._prompt_name("Nueva carpeta", title="Nombre de la carpeta")
        if not name:
            return
        siblings = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        self.shortcuts.append(
            {
                "id": new_item_id(),
                "type": "folder",
                "name": name,
                "parent_id": self.current_folder_id,
                "order": len(siblings),
                "color": None,
                "size": DEFAULT_SIZE,
            }
        )
        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    def _prompt_name(self, default: str, title: str = "Nombre") -> str | None:
        colors = self.colors
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=colors["bg"])
        dialog.geometry("360x140")
        dialog.transient(self.root)
        dialog.grab_set()

        result: dict[str, str | None] = {"value": None}

        tk.Label(
            dialog, text=f"{title}:", font=("Segoe UI", 10), fg=colors["text"], bg=colors["bg"],
        ).pack(anchor="w", padx=16, pady=(16, 4))

        entry = ttk.Entry(dialog, width=40)
        entry.insert(0, default)
        entry.pack(padx=16, fill="x")
        entry.focus_set()
        entry.select_range(0, "end")

        def confirm() -> None:
            result["value"] = entry.get().strip() or default
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=colors["bg"], pady=12)
        buttons.pack(fill="x", padx=16)
        ttk.Button(buttons, text="Guardar", command=confirm).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Cancelar", command=cancel).pack(side="left")
        entry.bind("<Return>", lambda _e: confirm())
        dialog.bind("<Escape>", lambda _e: cancel())

        self.root.wait_window(dialog)
        return result["value"]

    # ------------------------------------------------------------------
    # Importar / exportar / editar / recargar
    # ------------------------------------------------------------------

    def import_shortcuts_dialog(self) -> None:
        source = filedialog.askopenfilename(
            title="Importar accesos directos", filetypes=[("JSON", "*.json"), ("Todos", "*.*")]
        )
        if not source:
            return
        replace = messagebox.askyesno(
            "Modo de importación",
            "¿Reemplazar todos los accesos actuales?\n\n"
            "Sí = Reemplazar todo\nNo = Combinar (solo añade los que no existan)",
        )
        mode = "replace" if replace else "merge"
        try:
            self.shortcuts = import_shortcuts(Path(source), mode)
            self._layout_tiles()
            messagebox.showinfo("Importación completada", f"Se importaron accesos desde:\n{source}")
        except Exception as exc:
            messagebox.showerror("Error al importar", str(exc))

    def export_shortcuts_dialog(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="Exportar accesos directos",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="mis-accesos-directos.json",
        )
        if not destination:
            return
        try:
            export_shortcuts(self.shortcuts, Path(destination))
            messagebox.showinfo("Exportación completada", f"Accesos guardados en:\n{destination}")
        except Exception as exc:
            messagebox.showerror("Error al exportar", str(exc))

    def edit_config(self) -> None:
        try:
            open_path(str(SHORTCUTS_PATH))
        except OSError as exc:
            messagebox.showerror("Error", f"No se pudo abrir la configuración:\n{exc}")

    def reload(self) -> None:
        self.shortcuts = load_shortcuts()
        win_icons.clear_cache()
        self._layout_tiles()

    def show_about(self) -> None:
        messagebox.showinfo(
            "Acerca de",
            f"Accesos Directos\nVersión {get_app_version()}\n\n"
            f"Actualizaciones desde: github.com/{GITHUB_OWNER}/{GITHUB_REPO}",
        )

    # ------------------------------------------------------------------
    # Configuración (clic y tema)
    # ------------------------------------------------------------------

    def open_settings_dialog(self) -> None:
        colors = self.colors
        dialog = tk.Toplevel(self.root)
        dialog.title("Configuración")
        dialog.configure(bg=colors["bg"])
        dialog.geometry("360x300")
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog, text="Clics para abrir un acceso", font=("Segoe UI", 10, "bold"),
            fg=colors["text"], bg=colors["bg"],
        ).pack(anchor="w", padx=18, pady=(18, 4))

        click_var = tk.StringVar(value=self.settings.get("click_mode", "double"))
        for value, label in (("single", "Un clic"), ("double", "Doble clic")):
            tk.Radiobutton(
                dialog, text=label, value=value, variable=click_var,
                fg=colors["text"], bg=colors["bg"], selectcolor=colors["surface"],
                activebackground=colors["bg"], activeforeground=colors["text"],
                highlightthickness=0,
            ).pack(anchor="w", padx=26)

        tk.Label(
            dialog, text="Tema de la aplicación", font=("Segoe UI", 10, "bold"),
            fg=colors["text"], bg=colors["bg"],
        ).pack(anchor="w", padx=18, pady=(16, 4))

        theme_var = tk.StringVar(value=self.settings.get("theme", "morado"))
        for key, theme in THEMES.items():
            tk.Radiobutton(
                dialog, text=theme["label"], value=key, variable=theme_var,
                fg=colors["text"], bg=colors["bg"], selectcolor=colors["surface"],
                activebackground=colors["bg"], activeforeground=colors["text"],
                highlightthickness=0,
            ).pack(anchor="w", padx=26)

        tk.Label(
            dialog, text="El tema se aplica al reiniciar la app.", font=("Segoe UI", 8),
            fg=colors["text_muted"], bg=colors["bg"],
        ).pack(anchor="w", padx=18, pady=(6, 0))

        def save_and_close() -> None:
            self.settings["click_mode"] = click_var.get()
            theme_changed = theme_var.get() != self.settings.get("theme")
            self.settings["theme"] = theme_var.get()
            save_settings(self.settings)
            dialog.destroy()
            if theme_changed and messagebox.askyesno(
                "Reiniciar", "El tema ha cambiado. ¿Reiniciar ahora para aplicarlo?"
            ):
                restart_app(self.root)

        buttons = tk.Frame(dialog, bg=colors["bg"], pady=14)
        buttons.pack(fill="x", padx=18)
        ttk.Button(buttons, text="Guardar", command=save_and_close).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="left")

    # ------------------------------------------------------------------
    # Actualizaciones
    # ------------------------------------------------------------------

    def _toggle_auto_updates(self) -> None:
        self.settings["auto_check_updates"] = self.auto_update_var.get()
        save_settings(self.settings)

    def _set_update_icon_checking(self, checking: bool) -> None:
        if checking:
            self.update_icon.configure(fg=self.colors["accent"])
        else:
            color = self.colors["accent"] if self._pending_update else self.colors["text_muted"]
            self.update_icon.configure(fg=color)

    def _set_update_available(self, update) -> None:
        self._pending_update = update
        self.update_icon.configure(fg=self.colors["accent"])
        self.update_badge.place(in_=self.update_icon, relx=1.0, rely=0.0, anchor="ne", x=2, y=-2)

    def _clear_update_badge(self) -> None:
        self._pending_update = None
        self.update_icon.configure(fg=self.colors["text_muted"])
        self.update_badge.place_forget()

    def _maybe_check_updates_on_startup(self) -> None:
        if not self.auto_update_var.get():
            return
        self._run_update_check(silent_if_updated=True, show_toast=True)

    def _auto_check_loop(self) -> None:
        if self.auto_update_var.get():
            self._run_update_check(silent_if_updated=True, show_toast=True)
        self.root.after(UPDATE_CHECK_INTERVAL_MS, self._auto_check_loop)

    def check_updates_dialog(self) -> None:
        if self._pending_update is not None:
            self._prompt_install(self._pending_update)
            return
        self._run_update_check(silent_if_updated=False, show_toast=False)

    def _run_update_check(self, silent_if_updated: bool, show_toast: bool) -> None:
        if self._update_running:
            return
        self._update_running = True
        self.root.after(0, lambda: self._set_update_icon_checking(True))

        def worker() -> None:
            try:
                update = check_for_updates()
            except UpdateError as exc:
                self._update_running = False
                self.root.after(0, lambda: self._set_update_icon_checking(False))
                if not silent_if_updated:
                    self.root.after(0, lambda: messagebox.showerror("Actualizaciones", str(exc)))
                return

            self._update_running = False

            if update is None:
                self.root.after(0, self._clear_update_badge)
                if not silent_if_updated:
                    self.root.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Actualizaciones", f"Ya tienes la versión más reciente (v{get_app_version()})."
                        ),
                    )
                return

            def on_found() -> None:
                self._set_update_available(update)
                if show_toast:
                    self._show_update_toast(update)
                else:
                    self._prompt_install(update)

            self.root.after(0, on_found)

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_toast(self, update) -> None:
        colors = self.colors
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.configure(bg=colors["surface"])
        try:
            toast.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.root.update_idletasks()
        width, height = 300, 96
        x = self.root.winfo_rootx() + self.root.winfo_width() - width - 20
        y = self.root.winfo_rooty() + self.root.winfo_height() - height - 20
        toast.geometry(f"{width}x{height}+{x}+{y}")

        tk.Label(
            toast, text="🔄  Actualización disponible", font=("Segoe UI", 10, "bold"),
            fg=colors["accent"], bg=colors["surface"],
        ).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(
            toast, text=f"Versión {update.latest_version} (tienes la {update.current_version})",
            font=("Segoe UI", 9), fg=colors["text"], bg=colors["surface"],
        ).pack(anchor="w", padx=14)

        buttons = tk.Frame(toast, bg=colors["surface"])
        buttons.pack(anchor="e", padx=10, pady=(10, 10))

        def install_now() -> None:
            toast.destroy()
            self._install_update(update, GITHUB_TOKEN)

        def dismiss() -> None:
            toast.destroy()

        tk.Button(
            buttons, text="Actualizar", command=install_now, font=("Segoe UI", 9, "bold"),
            fg=colors["bg"], bg=colors["accent"], activeforeground=colors["bg"],
            activebackground=colors["accent"], relief="flat", padx=10, pady=4,
            cursor="hand2", borderwidth=0,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            buttons, text="Más tarde", command=dismiss, font=("Segoe UI", 9),
            fg=colors["text"], bg=colors["surface_hover"], activeforeground=colors["text"],
            activebackground=colors["surface_hover"], relief="flat", padx=10, pady=4,
            cursor="hand2", borderwidth=0,
        ).pack(side="left")

        def auto_close() -> None:
            try:
                toast.destroy()
            except tk.TclError:
                pass

        toast.after(10000, auto_close)

    def _prompt_install(self, update) -> None:
        install = messagebox.askyesno(
            "Actualización disponible",
            f"Hay una nueva versión: {update.latest_version}\n"
            f"Versión actual: {update.current_version}\n\n"
            "¿Quieres descargar e instalar la actualización?\n\nTus accesos directos no se modificarán.",
        )
        if install:
            self._install_update(update, GITHUB_TOKEN)

    def _install_update(self, update, token: str) -> None:
        colors = self.colors
        progress = tk.Toplevel(self.root)
        progress.title("Actualizando...")
        progress.configure(bg=colors["bg"])
        progress.geometry("360x120")
        progress.transient(self.root)
        progress.grab_set()
        tk.Label(
            progress, text="Descargando e instalando actualización...", fg=colors["text"], bg=colors["bg"],
        ).pack(pady=24)

        def worker() -> None:
            try:
                apply_update(update.download_url, token, update.latest_version)
            except UpdateError as exc:
                self.root.after(0, lambda: progress.destroy())
                self.root.after(0, lambda: messagebox.showerror("Error de actualización", str(exc)))
                return

            def done() -> None:
                progress.destroy()
                self._clear_update_badge()
                restart = messagebox.askyesno(
                    "Actualización instalada",
                    "La aplicación se ha actualizado correctamente.\n"
                    "Tus accesos directos se mantienen intactos.\n\n"
                    "¿Reiniciar ahora para aplicar los cambios?",
                )
                if restart:
                    restart_with_update(self.root)

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


def _missing_optional_dependencies() -> list[str]:
    """Comprueba (sin instalar nada) qué paquetes opcionales faltan.

    Solo tiene sentido en modo fuente: el .exe compilado ya debería
    llevarlos empaquetados dentro (ver main.spec), así que si faltan ahí
    es un problema de cómo se compiló, no algo que arreglar en tiempo de
    ejecución en el equipo de un usuario final.
    """
    missing = []
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow")
    try:
        import tkinterdnd2  # noqa: F401
    except ImportError:
        missing.append("tkinterdnd2")
    return missing


def _offer_dependency_install(missing: list[str]) -> None:
    """Pregunta si se instalan las dependencias que faltan y, si se
    acepta, las instala con pip y reinicia la app. Nunca se llama en el
    .exe compilado (ver _missing_optional_dependencies)."""
    import subprocess

    prompt_root = tk.Tk()
    prompt_root.withdraw()

    install = messagebox.askyesno(
        "Funciones adicionales disponibles",
        "Accesos Directos puede mostrar los iconos reales de Windows y "
        "permitir arrastrar archivos desde el Explorador, pero necesita "
        "instalar estas librerías de Python (una sola vez):\n\n"
        f"  •  {', '.join(missing)}\n\n"
        "Ocupan aproximadamente 10-15 MB en total (el tamaño exacto "
        "depende de tu versión de Windows y Python) y se descargan desde "
        "pypi.org, el repositorio oficial de paquetes de Python.\n\n"
        "¿Instalarlas ahora? Si dices que no, la app funciona igual, "
        "pero con iconos genéricos y sin arrastrar-soltar.",
        parent=prompt_root,
    )
    if not install:
        prompt_root.destroy()
        return

    progress = tk.Toplevel(prompt_root)
    progress.title("Instalando...")
    progress.geometry("360x110")
    tk.Label(progress, text="Instalando dependencias, un momento...", pady=24).pack()
    progress.update()

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
            check=True,
        )
    except Exception as exc:
        progress.destroy()
        messagebox.showerror(
            "Error al instalar",
            f"No se pudieron instalar las dependencias:\n{exc}\n\n"
            "La app se abrirá igualmente, con funciones limitadas.",
            parent=prompt_root,
        )
        prompt_root.destroy()
        return

    progress.destroy()
    messagebox.showinfo(
        "Instalación completada",
        "Listo. La aplicación se va a reiniciar para activarlo.",
        parent=prompt_root,
    )
    prompt_root.destroy()
    restart_app()


def main() -> None:
    # El .exe compilado ya lleva sus dependencias empaquetadas dentro
    # (ver main.spec); esta comprobación solo aplica cuando se ejecuta
    # el código fuente directamente.
    if not getattr(sys, "frozen", False):
        missing = _missing_optional_dependencies()
        if missing:
            _offer_dependency_install(missing)

    AccesosDirectosApp().run()


if __name__ == "__main__":
    main()
