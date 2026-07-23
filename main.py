"""Accesos Directos — launcher de archivos y carpetas frecuentes."""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app_config import (
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
from github_updates import UpdateError, apply_update, check_for_updates

COLORS = {
    "bg": "#1e1e2e",
    "surface": "#313244",
    "surface_hover": "#45475a",
    "accent": "#89b4fa",
    "text": "#cdd6f4",
    "text_muted": "#a6adc8",
    "danger": "#f38ba8",
}


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
        self.root.geometry("720x520")
        self.root.minsize(480, 360)
        self.root.configure(bg=COLORS["bg"])

        self.settings = load_settings()
        self.shortcuts = load_shortcuts()
        self._update_running = False

        self._build_ui()
        self.refresh_buttons()
        self.root.after(800, self._maybe_check_updates_on_startup)

    def _build_ui(self) -> None:
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

        tk.Label(
            header,
            text="Clic para abrir · Clic derecho para más opciones",
            font=("Segoe UI", 9),
            fg=COLORS["text_muted"],
            bg=COLORS["bg"],
        ).pack(anchor="w", pady=(4, 0))

        toolbar = tk.Frame(self.root, bg=COLORS["bg"], padx=20)
        toolbar.pack(fill="x", pady=(0, 4))

        ttk.Style().theme_use("clam")
        style = ttk.Style()
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground=COLORS["bg"],
            font=("Segoe UI", 10, "bold"),
            padding=(12, 8),
        )
        style.map("Accent.TButton", background=[("active", "#b4befe")])

        ttk.Button(toolbar, text="+ Añadir", style="Accent.TButton", command=self.add_shortcut).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Importar", command=self.import_shortcuts_dialog).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Exportar", command=self.export_shortcuts_dialog).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Editar lista", command=self.edit_config).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Recargar", command=self.reload).pack(side="left")

        toolbar2 = tk.Frame(self.root, bg=COLORS["bg"], padx=20)
        toolbar2.pack(fill="x", pady=(0, 8))

        ttk.Button(toolbar2, text="Buscar actualizaciones", command=self.check_updates_dialog).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar2, text="Configuración", command=self.open_settings_dialog).pack(side="left")

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

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def refresh_buttons(self) -> None:
        for child in self.scrollable.winfo_children():
            child.destroy()

        if not self.shortcuts:
            tk.Label(
                self.scrollable,
                text="No hay accesos configurados.\nPulsa «+ Añadir» para empezar.",
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
        self.shortcuts = load_shortcuts()
        self.refresh_buttons()

    def open_settings_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Configuración")
        dialog.configure(bg=COLORS["bg"])
        dialog.geometry("520x360")
        dialog.transient(self.root)
        dialog.grab_set()

        github = self.settings.get("github", {})

        tk.Label(
            dialog,
            text="Repositorio de GitHub para actualizaciones",
            font=("Segoe UI", 11, "bold"),
            fg=COLORS["text"],
            bg=COLORS["bg"],
        ).pack(anchor="w", padx=16, pady=(16, 8))

        tk.Label(
            dialog,
            text="Funciona con repos privados si indicas un token con permiso «repo».",
            font=("Segoe UI", 9),
            fg=COLORS["text_muted"],
            bg=COLORS["bg"],
            wraplength=480,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 12))

        form = tk.Frame(dialog, bg=COLORS["bg"], padx=16)
        form.pack(fill="x")

        owner_entry = ttk.Entry(form, width=48)
        repo_entry = ttk.Entry(form, width=48)
        token_entry = ttk.Entry(form, width=48, show="*")
        startup_var = tk.BooleanVar(value=self.settings.get("check_updates_on_startup", True))

        labels = ("Propietario (usuario u organización):", "Repositorio:", "Token de GitHub (opcional):")
        entries = (owner_entry, repo_entry, token_entry)
        for row, (label, entry) in enumerate(zip(labels, entries)):
            tk.Label(form, text=label, fg=COLORS["text"], bg=COLORS["bg"]).grid(
                row=row, column=0, sticky="w", pady=(0, 4)
            )
            entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 10))

        owner_entry.insert(0, github.get("owner", ""))
        repo_entry.insert(0, github.get("repo", "accesos-directos"))
        token_entry.insert(0, github.get("token", ""))

        ttk.Checkbutton(
            dialog,
            text="Buscar actualizaciones al iniciar",
            variable=startup_var,
        ).pack(anchor="w", padx=16, pady=(8, 0))

        def save_and_close() -> None:
            self.settings["github"] = {
                "owner": owner_entry.get().strip(),
                "repo": repo_entry.get().strip() or "accesos-directos",
                "token": token_entry.get().strip(),
            }
            self.settings["check_updates_on_startup"] = startup_var.get()
            save_settings(self.settings)
            dialog.destroy()
            messagebox.showinfo("Configuración guardada", "Los ajustes se han guardado correctamente.")

        buttons = tk.Frame(dialog, bg=COLORS["bg"], padx=16, pady=16)
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="Guardar", command=save_and_close).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="left")

    def _github_settings(self) -> tuple[str, str, str]:
        github = self.settings.get("github", {})
        return (
            github.get("owner", ""),
            github.get("repo", "accesos-directos"),
            github.get("token", ""),
        )

    def _maybe_check_updates_on_startup(self) -> None:
        if not self.settings.get("check_updates_on_startup", True):
            return
        owner, repo, _token = self._github_settings()
        if not owner or not repo:
            return
        self._run_update_check(silent_if_updated=True)

    def check_updates_dialog(self) -> None:
        owner, repo, _token = self._github_settings()
        if not owner or not repo:
            messagebox.showinfo(
                "Configuración necesaria",
                "Indica el repositorio de GitHub en Configuración antes de buscar actualizaciones.",
            )
            self.open_settings_dialog()
            return
        self._run_update_check(silent_if_updated=False)

    def _run_update_check(self, silent_if_updated: bool) -> None:
        if self._update_running:
            return

        self._update_running = True
        owner, repo, token = self._github_settings()

        def worker() -> None:
            try:
                update = check_for_updates(owner, repo, token)
            except UpdateError as exc:
                self.root.after(0, lambda: messagebox.showerror("Actualizaciones", str(exc)))
                return
            finally:
                self._update_running = False

            if update is None:
                if not silent_if_updated:
                    self.root.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Actualizaciones",
                            f"Ya tienes la versión más reciente (v{get_app_version()}).",
                        ),
                    )
                return

            def prompt_install() -> None:
                install = messagebox.askyesno(
                    "Actualización disponible",
                    f"Hay una nueva versión: {update.latest_version}\n"
                    f"Versión actual: {update.current_version}\n\n"
                    "¿Quieres descargar e instalar la actualización?\n\n"
                    "Tus accesos directos no se modificarán.",
                )
                if install:
                    self._install_update(update.download_url, token)

            self.root.after(0, prompt_install)

        threading.Thread(target=worker, daemon=True).start()

    def _install_update(self, download_url: str, token: str) -> None:
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
                apply_update(download_url, token)
            except UpdateError as exc:
                self.root.after(0, lambda: progress.destroy())
                self.root.after(0, lambda: messagebox.showerror("Error de actualización", str(exc)))
                return

            def done() -> None:
                progress.destroy()
                restart = messagebox.askyesno(
                    "Actualización instalada",
                    "La aplicación se ha actualizado correctamente.\n"
                    "Tus accesos directos se mantienen intactos.\n\n"
                    "¿Reiniciar ahora para aplicar los cambios?",
                )
                if restart:
                    os.execv(sys.executable, [sys.executable, *sys.argv])

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    AccesosDirectosApp().run()


if __name__ == "__main__":
    main()
