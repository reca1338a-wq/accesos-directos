"""Rutas, ajustes y persistencia de accesos directos."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid as _uuid
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

# Repositorio de GitHub usado para comprobar actualizaciones.
# Se configura aquí, en el código: el usuario final no puede cambiarlo
# desde la aplicación.
GITHUB_OWNER = "reca1338a-wq"
GITHUB_REPO = "accesos-directos"
GITHUB_TOKEN = ""  # Déjalo vacío si el repositorio es público.

if sys.platform == "win32":
    USER_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "AccesosDirectos"
else:
    USER_DATA_DIR = Path.home() / ".config" / "accesos-directos"

SHORTCUTS_PATH = USER_DATA_DIR / "shortcuts.json"
SETTINGS_PATH = USER_DATA_DIR / "settings.json"

# ---------------------------------------------------------------------------
# Temas de color. Añade aquí nuevos temas si quieres más opciones futuras.
# ---------------------------------------------------------------------------
THEMES = {
    "morado": {
        "label": "Morado (oscuro)",
        "bg": "#1e1e2e",
        "surface": "#313244",
        "surface_hover": "#45475a",
        "accent": "#89b4fa",
        "text": "#cdd6f4",
        "text_muted": "#a6adc8",
        "danger": "#f38ba8",
    },
    "azul": {
        "label": "Azul (oscuro)",
        "bg": "#0f172a",
        "surface": "#1e293b",
        "surface_hover": "#334155",
        "accent": "#38bdf8",
        "text": "#e2e8f0",
        "text_muted": "#94a3b8",
        "danger": "#f87171",
    },
    "claro": {
        "label": "Claro",
        "bg": "#f4f4f5",
        "surface": "#ffffff",
        "surface_hover": "#e4e4e7",
        "accent": "#2563eb",
        "text": "#18181b",
        "text_muted": "#71717a",
        "danger": "#dc2626",
    },
}
DEFAULT_THEME = "morado"

# ---------------------------------------------------------------------------
# Tamaños disponibles para cada acceso o carpeta (estilo "tarjetas" de Power BI).
# ---------------------------------------------------------------------------
SIZE_PRESETS = {
    "small": {"label": "Pequeño", "width": 120, "height": 92},
    "medium": {"label": "Mediano", "width": 160, "height": 112},
    "large": {"label": "Grande", "width": 220, "height": 144},
}
DEFAULT_SIZE = "medium"

DEFAULT_SETTINGS = {
    "auto_check_updates": True,
    "click_mode": "double",  # "single" o "double"
    "theme": DEFAULT_THEME,
}

# ---------------------------------------------------------------------------
# Arranque automático con Windows (pestaña "Aplicaciones de inicio" del
# Administrador de tareas). Se guarda en el registro, no en settings.json,
# para que sea siempre el propio Windows quien tenga la última palabra
# sobre si está activo (por ejemplo si el usuario lo desactiva a mano
# desde el Administrador de tareas, la app lo detecta correctamente).
# ---------------------------------------------------------------------------

STARTUP_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "AccesosDirectos"


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}"'
    # Modo fuente (sin compilar): usa pythonw.exe para que no abra una
    # consola al arrancar con Windows.
    python_dir = Path(sys.executable).resolve().parent
    pythonw = python_dir / "pythonw.exe"
    interpreter = pythonw if pythonw.exists() else Path(sys.executable).resolve()
    script = (APP_DIR / "main.py").resolve()
    return f'"{interpreter}" "{script}"'


def startup_supported() -> bool:
    return sys.platform == "win32"


def is_startup_enabled() -> bool:
    if not startup_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_startup_enabled(enabled: bool) -> None:
    if not startup_supported():
        return
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass

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


def get_theme(name: str) -> dict:
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def new_item_id() -> str:
    return str(_uuid.uuid4())


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
    merged["auto_check_updates"] = bool(
        data.get("auto_check_updates", DEFAULT_SETTINGS["auto_check_updates"])
    )
    click_mode = data.get("click_mode", DEFAULT_SETTINGS["click_mode"])
    merged["click_mode"] = click_mode if click_mode in ("single", "double") else "double"
    theme = data.get("theme", DEFAULT_SETTINGS["theme"])
    merged["theme"] = theme if theme in THEMES else DEFAULT_THEME
    return merged


def save_settings(settings: dict) -> None:
    ensure_user_data_dir()
    with SETTINGS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Accesos directos y carpetas.
#
# Cada elemento es un diccionario con:
#   id         identificador único
#   type       "shortcut" o "folder"
#   name       nombre mostrado
#   path       solo para "shortcut": ruta del archivo o carpeta del sistema
#   parent_id  id de la carpeta interna que lo contiene, o None si está en la raíz
#   order      posición dentro de su carpeta (para el orden manual)
#   color      color personalizado en hex, o None para usar el color del tema
#   size       "small" | "medium" | "large"
# ---------------------------------------------------------------------------


def _normalize_items(raw: list) -> list[dict]:
    items: list[dict] = []
    seen_ids: set[str] = set()

    for entry in raw:
        if not isinstance(entry, dict):
            continue

        item_type = entry.get("type", "shortcut")
        if item_type not in ("shortcut", "folder"):
            continue

        name = str(entry.get("name", "")).strip()
        if not name:
            continue

        item_id = str(entry.get("id") or new_item_id())
        if item_id in seen_ids:
            item_id = new_item_id()
        seen_ids.add(item_id)

        parent_id = entry.get("parent_id")
        parent_id = str(parent_id) if parent_id else None

        size = entry.get("size")
        size = size if size in SIZE_PRESETS else DEFAULT_SIZE

        color = entry.get("color")
        color = str(color) if color else None

        item = {
            "id": item_id,
            "type": item_type,
            "name": name,
            "parent_id": parent_id,
            "order": entry.get("order", 0),
            "color": color,
            "size": size,
        }

        if item_type == "shortcut":
            path = str(entry.get("path", "")).strip()
            if not path:
                continue
            item["path"] = path

        items.append(item)

    # Evita que una carpeta acabe siendo su propia antecesora si el JSON
    # se ha editado a mano de forma incorrecta.
    valid_ids = {item["id"] for item in items}
    for item in items:
        if item["parent_id"] not in valid_ids:
            item["parent_id"] = None

    # Reordena de forma estable agrupando por carpeta contenedora.
    items.sort(key=lambda it: (it["parent_id"] or "", it.get("order", 0)))
    counters: dict[str | None, int] = {}
    for item in items:
        key = item["parent_id"]
        item["order"] = counters.get(key, 0)
        counters[key] = item["order"] + 1

    return items


def migrate_legacy_shortcuts() -> None:
    ensure_user_data_dir()
    if SHORTCUTS_PATH.exists() or not LEGACY_SHORTCUTS_PATH.exists():
        return
    shutil.copy2(LEGACY_SHORTCUTS_PATH, SHORTCUTS_PATH)


def _extract_raw_items(data) -> list:
    if isinstance(data, list):
        return data
    raw = data.get("items")
    if raw is not None:
        return raw
    # Formato antiguo (versiones previas): lista plana sin carpetas.
    return [{"type": "shortcut", **entry} for entry in data.get("shortcuts", [])]


def load_shortcuts() -> list[dict]:
    ensure_user_data_dir()
    migrate_legacy_shortcuts()

    if not SHORTCUTS_PATH.exists():
        items = _normalize_items([{"type": "shortcut", **entry} for entry in DEFAULT_SHORTCUTS])
        save_shortcuts(items)
        return items

    with SHORTCUTS_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)

    return _normalize_items(_extract_raw_items(data))


def save_shortcuts(items: list[dict]) -> None:
    ensure_user_data_dir()
    normalized = _normalize_items(items)
    with SHORTCUTS_PATH.open("w", encoding="utf-8") as handle:
        json.dump({"items": normalized}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_shortcuts_file(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return _normalize_items(_extract_raw_items(data))


def export_shortcuts(items: list[dict], destination: Path) -> None:
    normalized = _normalize_items(items)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump({"items": normalized}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def import_shortcuts(source: Path, mode: str) -> list[dict]:
    imported = parse_shortcuts_file(source)
    if mode == "replace":
        save_shortcuts(imported)
        return imported

    current = load_shortcuts()
    existing_paths = {item["path"] for item in current if item["type"] == "shortcut"}
    merged = list(current)
    next_order = sum(1 for item in current if item["parent_id"] is None)

    for entry in imported:
        if entry["type"] == "shortcut" and entry["path"] in existing_paths:
            continue
        new_entry = dict(entry)
        new_entry["id"] = new_item_id()
        new_entry["parent_id"] = None  # Los importados se colocan en la raíz.
        new_entry["order"] = next_order
        next_order += 1
        merged.append(new_entry)
        if new_entry["type"] == "shortcut":
            existing_paths.add(new_entry["path"])

    save_shortcuts(merged)
    return merged
