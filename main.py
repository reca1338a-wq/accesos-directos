"""Accesos Directos — launcher de archivos y carpetas frecuentes."""

from __future__ import annotations

import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

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
    UpdateError,
    apply_update,
    check_for_updates,
    cleanup_stale_update_files,
    restart_with_update,
)

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

        super().__init__(master, bg=bg, highlightthickness=2, highlightbackground=bg)
        self.app = app
        self.item = item
        self.base_bg = bg
        self.preset = preset
        self._press_pos: tuple[int, int] | None = None
        self._dragging = False

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
        self.configure(highlightbackground=color)

    # -- interacción -----------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        self._press_pos = (event.x_root, event.y_root)
        self._dragging = False

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

        self.settings = load_settings()
        self.colors = get_theme(self.settings.get("theme"))
        self.shortcuts = load_shortcuts()

        self.current_folder_id: str | None = None
        self.breadcrumb: list[tuple[str, str | None]] = [("Inicio", None)]

        self._update_running = False
        self._pending_update = None
        self._resize_after_id: str | None = None
        self._drag_source: Tile | None = None
        self._drag_target: Tile | None = None
        self._last_click_time = 0.0
        self._last_click_id: str | None = None

        self.root = tk.Tk()
        self.root.title("Accesos Directos")
        self.root.geometry("760x560")
        self.root.minsize(420, 320)
        self.root.configure(bg=self.colors["bg"])

        self._build_ui()
        self.root.update_idletasks()
        self._layout_tiles()

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

        tk.Label(
            toolbar,
            text="Arrastra una tarjeta sobre una carpeta para moverla dentro",
            font=("Segoe UI", 8),
            fg=colors["text_muted"],
            bg=colors["bg"],
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
    # Navegación de carpetas
    # ------------------------------------------------------------------

    def _render_breadcrumb(self) -> None:
        colors = self.colors
        for child in self.breadcrumb_frame.winfo_children():
            child.destroy()

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
                tk.Label(
                    self.breadcrumb_frame, text="  ›  ", font=("Segoe UI", 9),
                    fg=colors["text_muted"], bg=colors["bg"],
                ).pack(side="left")

    def _go_to_breadcrumb(self, index: int) -> None:
        self.breadcrumb = self.breadcrumb[: index + 1]
        self.current_folder_id = self.breadcrumb[-1][1]
        self._render_breadcrumb()
        self._layout_tiles()

    def navigate_into(self, folder_item: dict) -> None:
        self.breadcrumb.append((folder_item["name"], folder_item["id"]))
        self.current_folder_id = folder_item["id"]
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

    def _visible_items(self) -> list[dict]:
        items = [it for it in self.shortcuts if it["parent_id"] == self.current_folder_id]
        items.sort(key=lambda it: it.get("order", 0))
        return items

    def _layout_tiles(self) -> None:
        self._resize_after_id = None
        for child in self.scrollable.winfo_children():
            child.destroy()

        items = self._visible_items()
        canvas_width = max(self.canvas.winfo_width(), 240)

        if not items:
            tk.Label(
                self.scrollable,
                text="Esta carpeta está vacía.\nUsa «+ Añadir» para empezar.",
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
        for item in items:
            preset = SIZE_PRESETS.get(item.get("size", DEFAULT_SIZE), SIZE_PRESETS[DEFAULT_SIZE])
            width, height = preset["width"], preset["height"]
            if x + width + TILE_GAP > canvas_width and x > TILE_GAP:
                x = TILE_GAP
                y += row_height + TILE_GAP
                row_height = 0

            tile = Tile(self.scrollable, self, item)
            tile.place(x=x, y=y, width=width, height=height)

            x += width + TILE_GAP
            row_height = max(row_height, height)

        total_height = y + row_height + TILE_GAP
        self.scrollable.configure(width=canvas_width, height=total_height)
        self.canvas.configure(scrollregion=(0, 0, canvas_width, total_height))

    # ------------------------------------------------------------------
    # Clic / doble clic (según preferencia) y arrastre
    # ------------------------------------------------------------------

    def handle_tile_click(self, tile: Tile) -> None:
        mode = self.settings.get("click_mode", "double")
        if mode == "single":
            self.open_item(tile.item)
            return

        now = time.monotonic() * 1000
        if self._last_click_id == tile.item["id"] and now - self._last_click_time <= DOUBLE_CLICK_MS:
            self._last_click_time = 0
            self._last_click_id = None
            self.open_item(tile.item)
        else:
            self._last_click_time = now
            self._last_click_id = tile.item["id"]

    def open_item(self, item: dict) -> None:
        if item["type"] == "folder":
            self.navigate_into(item)
            return
        try:
            open_path(item["path"])
        except FileNotFoundError as exc:
            messagebox.showerror("Archivo no encontrado", str(exc))
        except OSError as exc:
            messagebox.showerror("Error al abrir", str(exc))

    def begin_drag(self, tile: Tile) -> None:
        self._drag_source = tile
        tile.configure(cursor="fleur")

    def update_drag(self, x_root: int, y_root: int) -> None:
        widget = self.root.winfo_containing(x_root, y_root)
        target = self._find_tile_ancestor(widget)
        if target is self._drag_source:
            target = None
        if target is not self._drag_target:
            if self._drag_target is not None:
                self._drag_target.set_drop_highlight(False)
            self._drag_target = target
            if target is not None:
                target.set_drop_highlight(True)

    def end_drag(self, x_root: int, y_root: int) -> None:
        self.update_drag(x_root, y_root)
        source = self._drag_source
        target = self._drag_target

        if target is not None:
            target.set_drop_highlight(False)
        if source is not None:
            source.configure(cursor="hand2")

        self._drag_source = None
        self._drag_target = None

        if source is None or target is None:
            return
        self._apply_drop(source.item, target.item)

    def _find_tile_ancestor(self, widget) -> Tile | None:
        while widget is not None:
            if isinstance(widget, Tile):
                return widget
            widget = getattr(widget, "master", None)
        return None

    def _apply_drop(self, dragged: dict, target: dict) -> None:
        if target["type"] == "folder" and target["id"] != dragged["id"]:
            dragged["parent_id"] = target["id"]
            siblings = [
                it for it in self.shortcuts if it["parent_id"] == target["id"] and it["id"] != dragged["id"]
            ]
            dragged["order"] = len(siblings)
        else:
            dragged["parent_id"] = target["parent_id"]
            siblings = [
                it
                for it in self.shortcuts
                if it["parent_id"] == dragged["parent_id"] and it["id"] != dragged["id"]
            ]
            target_index = next(
                (idx for idx, it in enumerate(siblings) if it["id"] == target["id"]), len(siblings)
            )
            siblings.insert(target_index, dragged)
            for idx, it in enumerate(siblings):
                it["order"] = idx

        save_shortcuts(self.shortcuts)
        self._layout_tiles()

    # ------------------------------------------------------------------
    # Menú contextual de cada tarjeta
    # ------------------------------------------------------------------

    def show_tile_menu(self, tile: Tile, event: tk.Event) -> None:
        item = tile.item
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Abrir", command=lambda: self.open_item(item))

        size_menu = tk.Menu(menu, tearoff=0)
        for key, preset in SIZE_PRESETS.items():
            size_menu.add_command(
                label=preset["label"], command=lambda k=key: self._set_item_size(item, k)
            )
        menu.add_cascade(label="Tamaño", menu=size_menu)

        menu.add_command(label="Cambiar color...", command=lambda: self._set_item_color(item))
        if item.get("color"):
            menu.add_command(label="Quitar color personalizado", command=lambda: self._clear_item_color(item))

        menu.add_command(label="Renombrar...", command=lambda: self._rename_item(item))
        menu.add_separator()

        if item["type"] == "folder":
            menu.add_command(label="Eliminar carpeta...", command=lambda: self._delete_folder(item))
        else:
            menu.add_command(label="Quitar acceso", command=lambda: self._delete_shortcut(item))

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
                os.execv(sys.executable, [sys.executable, *sys.argv])

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
                    restart_with_update()

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    AccesosDirectosApp().run()


if __name__ == "__main__":
    main()
