"""Rutas, ajustes y persistencia de accesos directos."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def _get_app_dir() -> Path:
    # Cuando se ejecuta como .exe (PyInstaller), __file__ apunta a una
    # carpeta temporal que se borra al cerrar. Usamos la carpeta donde
    # vive el propio .exe para que VERSION y demás persistan entre
    # ejecuciones y se puedan actualizar.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _get_app_dir()
VERSION_FILE = APP_DIR / "VERSION"
LEGACY_SHORTCUTS_PATH = APP_DIR / "shortcuts.json"

if sys.platform == "win32":
    USER_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "AccesosDirectos"
else:
    USER_DATA_DIR = Path.home() / ".config" / "accesos-directos"

SHORTCUTS_PATH = USER_DATA_DIR / "shortcuts.json"
SETTINGS_PATH = USER_DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "github": {
        "owner": "",
        "repo": "accesos-directos",
        "token": "",
    },
    "check_updates_on_startup": True,
}

DEFAULT_SHORTCUTS = [
    {"name": "Documentos", "path": str(Path.home() / "Documents")},
    {"name": "Escritorio", "path": str(Path.home() / "Desktop")},
    {"name": "Descargas", "path": str(Path.home() / "Downloads")},
]


def get_app_version() -> str:
    if not VERSION_FILE.exists():
        # Primera ejecución del .exe: copiamos el VERSION que va
        # empaquetado dentro del ejecutable (carpeta temporal _MEIPASS).
        bundled = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "VERSION"
        if bundled.exists():
            try:
                shutil.copy2(bundled, VERSION_FILE)
            except OSError:
                pass

    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.0.0"


def ensure_user_data_dir() -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    ensure_user_data_dir()
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS.copy())
        return json.loads(json.dumps(DEFAULT_SETTINGS))

    with SETTINGS_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)

    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    merged["github"].update(data.get("github", {}))
    merged["check_updates_on_startup"] = data.get(
        "check_updates_on_startup", DEFAULT_SETTINGS["check_updates_on_startup"]
    )
    return merged


def save_settings(settings: dict) -> None:
    ensure_user_data_dir()
    with SETTINGS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _normalize_shortcuts(raw: list) -> list[dict[str, str]]:
    shortcuts: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        path = str(item.get("path", "")).strip()
        if not name or not path or path in seen_paths:
            continue
        seen_paths.add(path)
        shortcuts.append({"name": name, "path": path})
    return shortcuts


def migrate_legacy_shortcuts() -> None:
    ensure_user_data_dir()
    if SHORTCUTS_PATH.exists() or not LEGACY_SHORTCUTS_PATH.exists():
        return
    shutil.copy2(LEGACY_SHORTCUTS_PATH, SHORTCUTS_PATH)


def load_shortcuts() -> list[dict[str, str]]:
    ensure_user_data_dir()
    migrate_legacy_shortcuts()

    if not SHORTCUTS_PATH.exists():
        shortcuts = _normalize_shortcuts(DEFAULT_SHORTCUTS)
        save_shortcuts(shortcuts)
        return shortcuts

    with SHORTCUTS_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return _normalize_shortcuts(data.get("shortcuts", []))


def save_shortcuts(shortcuts: list[dict[str, str]]) -> None:
    ensure_user_data_dir()
    normalized = _normalize_shortcuts(shortcuts)
    with SHORTCUTS_PATH.open("w", encoding="utf-8") as handle:
        json.dump({"shortcuts": normalized}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_shortcuts_file(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return _normalize_shortcuts(data)
    return _normalize_shortcuts(data.get("shortcuts", []))


def export_shortcuts(shortcuts: list[dict[str, str]], destination: Path) -> None:
    normalized = _normalize_shortcuts(shortcuts)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump({"shortcuts": normalized}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def import_shortcuts(source: Path, mode: str) -> list[dict[str, str]]:
    imported = parse_shortcuts_file(source)
    if mode == "replace":
        save_shortcuts(imported)
        return imported

    current = load_shortcuts()
    existing_paths = {item["path"] for item in current}
    merged = list(current)
    for item in imported:
        if item["path"] not in existing_paths:
            merged.append(item)
            existing_paths.add(item["path"])
    save_shortcuts(merged)
    return merged
