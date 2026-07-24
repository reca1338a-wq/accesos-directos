"""Accesos Directos — launcher de archivos y carpetas frecuentes."""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app_config import (
    GITHUB_OWNER,
    GITHUB_REPO,
    GITHUB_TOKEN,
    SHORTCUTS_PATH,
    USER_DATA_DIR,
    export_shortcuts,
    get_app_version,
    import_shortcuts,
    load_settings,
    load_shortcuts,
    save_settings,
    save_shortcuts,
)
from github_updates import UpdateError, apply_update, check_for_updates, restart_with_update

COLORS = {
    "bg": "#1e1e2e",
    "surface": "#313244",
    "surface_hover": "#45475a",
    "accent": "#89b4fa",
    "text": "#cdd6f4",
    "text_muted": "#a6adc8",
    "danger": "#f38ba8",
}

# Cada cuánto se comprueba si hay una versión nueva en segundo plano.
# 15 minutos es un buen equilibrio: GitHub permite 60 peticiones/hora sin
# token, así que esto solo gasta 4 y detecta actualizaciones con rapidez.
UPDATE_CHECK_INTERVAL_MS = 15 * 60 * 1000


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


class ShortcutButton(tk.Frame):
    def __init__(self, master: tk.Misc, name: str, path: str, on_remove) -> None:
        super().__init__(master, bg=COLORS["surface"], padx=12, pady=12)
        self.path = path
        self.on_remove = on_remove

        self.label = tk.Label(
            self,
            text=name,
            font=("Segoe UI", 11, "bold"),
            fg=COLORS["text"],
            bg=COLORS["surface"],
            wraplength=180,
            justify="center",
        )
        self.label.pack(fill="x", pady=(0, 6))

        self.path_label = tk.Label(
            self,
            text=self._short_path(path),
            font=("Segoe UI", 8),
            fg=COLORS["text_muted"],
            bg=COLORS["surface"],
            wraplength=180,
            justify="center",
        )
        self.path_label.pack(fill="x")

        for widget in (self, self.label, self.path_label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Button-3>", self._on_right_click)

    @staticmethod
    def _short_path(path: str) -> str:
        home = str(Path.home())
        if path.startswith(home):
            return "~" + path[len(home) :]
        return path

    def _on_enter(self, _event) -> None:
        self.configure(bg=COLORS["surface_hover"])
        self.label.configure(bg=COLORS["surface_hover"])
        self.path_label.configure(bg=COLORS["surface_hover"])

    def _on_leave(self, _event) -> None:
        self.configure(bg=COLORS["surface"])
        self.label.configure(bg=COLORS["surface"])
        self.path_label.configure(bg=COLORS["surface"])

    def _on_click(self, _event) -> None:
        try:
            open_path(self.path)
        except FileNotFoundError as exc:
            messagebox.showerror("Archivo no encontrado", str(exc))
        except OSError as exc:
            messagebox.showerror("Error al abrir", str(exc))

    def _on_right_click(self, event) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Abrir", command=lambda: self._on_click(None))
        menu.add_command(label="Quitar acceso", command=lambda: self.on_remove(self.path))
        menu.tk_popup(event.x_root, event.y_root)


class AccesosDirectosApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Accesos Directos")
        self.root.geometry("720x540")
        self.root.minsize(480, 360)
        self.root.configure(bg=COLORS["bg"])

        self.settings = load_settings()
        self.shortcuts = load_shortcuts()
        self._update_running = False
        self._pending_update = None

        self._build_ui()
        self.refresh_buttons()

        self.root.after(1500, self._maybe_check_updates_on_startup)
        self.root.after(UPDATE_CHECK_INTERVAL_MS, self._auto_check_loop)

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_menu()

        header = tk.Frame(self.root, bg=COLORS["bg"], padx=20, pady=16)
        header.pack(fill="x")

        title_row = tk.Frame(header, bg=COLORS["bg"])
        title_row.pack(fill="x")

        tk.Label(
            title_row,
            text="Accesos Directos",
            font=("Segoe UI", 18, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["bg"],
        ).pack(side="left")

        tk.Label(
            title_row,
            text=f"v{get_app_version()}",
            font=("Segoe UI", 9),
            fg=COLORS["text_muted"],
            bg=COLORS["bg"],
        ).pack(side="left", padx=(10, 0), pady=(6, 0))

        self._build_update_icon(title_row)

        tk.Label(
            header,
            text="Clic para abrir · Clic derecho para más opciones",
            font=("Segoe UI", 9),
            fg=COLORS["text_muted"],
            bg=COLORS["bg"],
        ).pack(anchor="w", pady=(4, 0))

        # Línea separadora sutil bajo la cabecera.
        tk.Frame(self.root, bg=COLORS["surface"], height=1).pack(fill="x", padx=20)

        toolbar = tk.Frame(self.root, bg=COLORS["bg"], padx=20)
        toolbar.pack(fill="x", pady=(14, 10))

        ttk.Style().theme_use("clam")
        style = ttk.Style()
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground=COLORS["bg"],
            font=("Segoe UI", 10, "bold"),
            padding=(14, 9),
            borderwidth=0,
        )
        style.map("Accent.TButton", background=[("active", "#b4befe")])

        ttk.Button(
            toolbar, text="+  Añadir acceso", style="Accent.TButton", command=self.add_shortcut
        ).pack(side="left")

        canvas_frame = tk.Frame(self.root, bg=COLORS["bg"], padx=20, pady=8)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable = tk.Frame(self.canvas, bg=COLORS["bg"])

        self.scrollable.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.create_window((0, 0), window=self.scrollable, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        footer = tk.Frame(self.root, bg=COLORS["bg"], padx=20)
        footer.pack(fill="x", pady=(0, 12))
        tk.Label(
            footer,
            text=f"Datos guardados en: {USER_DATA_DIR}",
            font=("Segoe UI", 8),
            fg=COLORS["text_muted"],
            bg=COLORS["bg"],
            wraplength=680,
            justify="left",
        ).pack(anchor="w")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Añadir acceso...", command=self.add_shortcut)
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

        self.root.config(menu=menubar)

    def _build_update_icon(self, parent: tk.Widget) -> None:
        wrapper = tk.Frame(parent, bg=COLORS["bg"])
        wrapper.pack(side="right")

        self.update_icon = tk.Label(
            wrapper,
            text="🔄",
            font=("Segoe UI Emoji", 14),
            fg=COLORS["text_muted"],
            bg=COLORS["bg"],
            cursor="hand2",
        )
        self.update_icon.pack()
        self.update_icon.bind("<Button-1>", lambda _event: self.check_updates_dialog())
        self._add_tooltip(self.update_icon, "Buscar actualizaciones")

        # Punto rojo que avisa de que hay una actualización pendiente.
        self.update_badge = tk.Canvas(
            wrapper, width=8, height=8, bg=COLORS["bg"], highlightthickness=0
        )
        self.update_badge.create_oval(0, 0, 8, 8, fill=COLORS["danger"], outline="")
        # Oculto por defecto; se muestra con _set_update_available().

    def _add_tooltip(self, widget: tk.Widget, text: str) -> None:
        state = {"win": None}

        def show(_event) -> None:
            win = tk.Toplevel(widget)
            win.overrideredirect(True)
            win.configure(bg=COLORS["surface"])
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            win.geometry(f"+{x}+{y}")
            tk.Label(
                win,
                text=text,
                font=("Segoe UI", 8),
                fg=COLORS["text"],
                bg=COLORS["surface"],
                padx=6,
                pady=3,
            ).pack()
            state["win"] = win

        def hide(_event) -> None:
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ------------------------------------------------------------------
    # Accesos directos
    # ------------------------------------------------------------------

    def refresh_buttons(self) -> None:
        for child in self.scrollable.winfo_children():
            child.destroy()

        if not self.shortcuts:
            tk.Label(
                self.scrollable,
                text="No hay accesos configurados.\nUsa «Archivo → Añadir acceso» para empezar.",
                font=("Segoe UI", 11),
                fg=COLORS["text_muted"],
                bg=COLORS["bg"],
                justify="center",
            ).pack(pady=40)
            return

        columns = 3
        for index, item in enumerate(self.shortcuts):
            row, col = divmod(index, columns)
            button = ShortcutButton(
                self.scrollable,
                name=item["name"],
                path=item["path"],
                on_remove=self.remove_shortcut,
            )
            button.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")

        for col in range(columns):
            self.scrollable.grid_columnconfigure(col, weight=1)

    def add_shortcut(self) -> None:
        choice = messagebox.askyesnocancel(
            "Tipo de acceso",
            "¿Qué quieres añadir?\n\nSí = Archivo\nNo = Carpeta\nCancelar = Salir",
        )
        if choice is None:
            return

        initialdir = str(Path.home())
        if choice:
            path = filedialog.askopenfilename(title="Selecciona un archivo", initialdir=initialdir)
        else:
            path = filedialog.askdirectory(title="Selecciona una carpeta", initialdir=initialdir)
        if not path:
            return

        default_name = Path(path).name
        name = self._prompt_name(default_name)
        if not name:
            return

        self.shortcuts.append({"name": name, "path": path})
        save_shortcuts(self.shortcuts)
        self.refresh_buttons()

    def import_shortcuts_dialog(self) -> None:
        source = filedialog.askopenfilename(
            title="Importar accesos directos",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not source:
            return

        replace = messagebox.askyesno(
            "Modo de importación",
            "¿Reemplazar todos los accesos actuales?\n\n"
            "Sí = Reemplazar todo\n"
            "No = Combinar (solo añade los que no existan)",
        )
        mode = "replace" if replace else "merge"
        try:
            self.shortcuts = import_shortcuts(Path(source), mode)
            self.refresh_buttons()
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

    def _prompt_name(self, default: str) -> str | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Nombre del acceso")
        dialog.configure(bg=COLORS["bg"])
        dialog.geometry("360x140")
        dialog.transient(self.root)
        dialog.grab_set()

        result: dict[str, str | None] = {"value": None}

        tk.Label(
            dialog,
            text="Nombre del botón:",
            font=("Segoe UI", 10),
            fg=COLORS["text"],
            bg=COLORS["bg"],
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

        buttons = tk.Frame(dialog, bg=COLORS["bg"], pady=12)
        buttons.pack(fill="x", padx=16)
        ttk.Button(buttons, text="Guardar", command=confirm).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Cancelar", command=cancel).pack(side="left")
        entry.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: cancel())

        self.root.wait_window(dialog)
        return result["value"]

    def remove_shortcut(self, path: str) -> None:
        if not messagebox.askyesno("Quitar acceso", f"¿Quitar este acceso?\n\n{path}"):
            return
        self.shortcuts = [item for item in self.shortcuts if item["path"] != path]
        save_shortcuts(self.shortcuts)
        self.refresh_buttons()

    def edit_config(self) -> None:
        try:
            open_path(str(SHORTCUTS_PATH))
        except OSError as exc:
            messagebox.showerror("Error", f"No se pudo abrir la configuración:\n{exc}")

    def reload(self) -> None:
        # Vuelve a leer shortcuts.json desde el disco. Útil solo si has
        # editado ese archivo a mano desde «Editar lista (JSON)».
        self.shortcuts = load_shortcuts()
        self.refresh_buttons()

    def show_about(self) -> None:
        messagebox.showinfo(
            "Acerca de",
            f"Accesos Directos\nVersión {get_app_version()}\n\n"
            f"Actualizaciones desde: github.com/{GITHUB_OWNER}/{GITHUB_REPO}",
        )

    # ------------------------------------------------------------------
    # Actualizaciones
    # ------------------------------------------------------------------

    def _toggle_auto_updates(self) -> None:
        self.settings["auto_check_updates"] = self.auto_update_var.get()
        save_settings(self.settings)

    def _set_update_icon_checking(self, checking: bool) -> None:
        if checking:
            self.update_icon.configure(fg=COLORS["accent"])
        else:
            color = COLORS["accent"] if self._pending_update else COLORS["text_muted"]
            self.update_icon.configure(fg=color)

    def _set_update_available(self, update) -> None:
        self._pending_update = update
        self.update_icon.configure(fg=COLORS["accent"])
        self.update_badge.place(in_=self.update_icon, relx=1.0, rely=0.0, anchor="ne", x=2, y=-2)

    def _clear_update_badge(self) -> None:
        self._pending_update = None
        self.update_icon.configure(fg=COLORS["text_muted"])
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
        # Si ya sabíamos de una actualización pendiente, no hace falta
        # volver a preguntar a GitHub: se ofrece instalar directamente.
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
                            "Actualizaciones",
                            f"Ya tienes la versión más reciente (v{get_app_version()}).",
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
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.configure(bg=COLORS["surface"])
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
            toast,
            text="🔄  Actualización disponible",
            font=("Segoe UI", 10, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["surface"],
        ).pack(anchor="w", padx=14, pady=(12, 2))

        tk.Label(
            toast,
            text=f"Versión {update.latest_version} (tienes la {update.current_version})",
            font=("Segoe UI", 9),
            fg=COLORS["text"],
            bg=COLORS["surface"],
        ).pack(anchor="w", padx=14)

        buttons = tk.Frame(toast, bg=COLORS["surface"])
        buttons.pack(anchor="e", padx=10, pady=(10, 10))

        def install_now() -> None:
            toast.destroy()
            self._install_update(update, GITHUB_TOKEN)

        def dismiss() -> None:
            toast.destroy()

        ttk.Button(buttons, text="Actualizar", command=install_now).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Más tarde", command=dismiss).pack(side="left")

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
            "¿Quieres descargar e instalar la actualización?\n\n"
            "Tus accesos directos no se modificarán.",
        )
        if install:
            self._install_update(update, GITHUB_TOKEN)

    def _install_update(self, update, token: str) -> None:
        progress = tk.Toplevel(self.root)
        progress.title("Actualizando...")
        progress.configure(bg=COLORS["bg"])
        progress.geometry("360x120")
        progress.transient(self.root)
        progress.grab_set()
        tk.Label(
            progress,
            text="Descargando e instalando actualización...",
            fg=COLORS["text"],
            bg=COLORS["bg"],
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
