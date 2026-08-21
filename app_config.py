"""Rutas, ajustes y persistencia de accesos directos."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
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
TRASH_PATH = USER_DATA_DIR / "trash.json"

# Días que se conserva un elemento en la papelera antes de borrarse
# definitivamente en solitario (al arrancar la app).
TRASH_RETENTION_DAYS = 7

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

SORT_MODES = ("manual", "name_asc", "name_desc", "folders_first")

# Tamaño mínimo/máximo (en píxeles) al redimensionar una tarjeta a mano
# arrastrando su esquina (estilo Power BI / Canva), y el paso de la
# rejilla a la que se ajusta el tamaño cuando "snap_to_grid" está activo.
MIN_TILE_WIDTH, MAX_TILE_WIDTH = 90, 360
MIN_TILE_HEIGHT, MAX_TILE_HEIGHT = 70, 280
GRID_SNAP_STEP = 10

DEFAULT_SETTINGS = {
    "auto_check_updates": True,
    "click_mode": "double",  # "single" o "double"
    "theme": DEFAULT_THEME,
    "sort_mode": "manual",  # "manual" | "name_asc" | "name_desc" | "folders_first"
    "card_style": "cards",  # "cards" | "compact"
    "show_recent": True,
    "categories": {},  # nombre -> color hex, ej. {"Trabajo": "#38bdf8"}
    "group_by_category": False,
    "window_geometry": "",  # "WxH+X+Y", como devuelve root.geometry(); vacío = usar el tamaño por defecto
    # Al redimensionar tarjetas a mano (arrastrando la esquina), ajusta el
    # tamaño resultante a múltiplos de GRID_SNAP_STEP para que tarjetas de
    # tamaños distintos sigan quedando alineadas entre sí de forma pulcra.
    "snap_to_grid": True,
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


def get_theme(name: str) -> dict:
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def new_item_id() -> str:
    return str(_uuid.uuid4())


def snap_dimension(value: int, step: int = GRID_SNAP_STEP) -> int:
    """Redondea `value` al múltiplo de `step` más cercano (mínimo `step`).
    Se usa al redimensionar tarjetas a mano para que el resultado quede
    "en rejilla" en vez de a un píxel exacto arbitrario."""
    if step <= 0:
        return value
    snapped = round(value / step) * step
    return max(step, snapped)


def record_item_opened(items: list[dict], item_id: str) -> None:
    """Actualiza open_count/last_opened del elemento con ese id, en la
    lista `items` (in place). No guarda a disco: quien llame decide
    cuándo hacer save_shortcuts."""
    for it in items:
        if it["id"] == item_id:
            it["open_count"] = int(it.get("open_count", 0) or 0) + 1
            it["last_opened"] = time.time()
            return


def most_used_items(items: list[dict], limit: int = 10) -> list[dict]:
    """Devuelve hasta `limit` accesos (no carpetas) ordenados por número
    de aperturas, de más a menos usado. Ignora los que nunca se han
    abierto."""
    used = [
        it for it in items
        if it["type"] == "shortcut" and int(it.get("open_count", 0) or 0) > 0
    ]
    used.sort(key=lambda it: (-int(it.get("open_count", 0) or 0), -(it.get("last_opened") or 0)))
    return used[:limit]


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
    sort_mode = data.get("sort_mode", DEFAULT_SETTINGS["sort_mode"])
    merged["sort_mode"] = sort_mode if sort_mode in SORT_MODES else "manual"
    card_style = data.get("card_style", DEFAULT_SETTINGS["card_style"])
    merged["card_style"] = card_style if card_style in ("cards", "compact") else "cards"
    merged["show_recent"] = bool(data.get("show_recent", DEFAULT_SETTINGS["show_recent"]))
    merged["group_by_category"] = bool(
        data.get("group_by_category", DEFAULT_SETTINGS["group_by_category"])
    )
    geometry = data.get("window_geometry", "")
    merged["window_geometry"] = str(geometry) if isinstance(geometry, str) else ""
    merged["snap_to_grid"] = bool(data.get("snap_to_grid", DEFAULT_SETTINGS["snap_to_grid"]))
    categories = data.get("categories", {})
    merged["categories"] = (
        {str(k): str(v) for k, v in categories.items()} if isinstance(categories, dict) else {}
    )
    return merged


def save_settings(settings: dict) -> None:
    ensure_user_data_dir()
    with SETTINGS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


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


def expand_path(path: str) -> str:
    """Expande variables de entorno (%APPDATA%, %USERPROFILE%...) en una
    ruta guardada. Si la ruta no las usa, la devuelve tal cual."""
    return os.path.expandvars(path)


def portabilize_path(path: str) -> str:
    """Intenta sustituir el prefijo de una ruta absoluta (típicamente
    dentro de la carpeta del usuario) por la variable de entorno
    equivalente, para que el acceso funcione igual en otro PC/usuario.

    Por ejemplo "C:\\Users\\manuel\\AppData\\Roaming\\X" se convierte en
    "%APPDATA%\\X". Si no coincide con ninguna variable conocida, se deja
    la ruta original sin tocar.
    """
    if sys.platform != "win32":
        return path

    candidates = [
        ("APPDATA", os.environ.get("APPDATA")),
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
        ("USERPROFILE", os.environ.get("USERPROFILE")),
        ("ProgramFiles(x86)", os.environ.get("ProgramFiles(x86)")),
        ("ProgramFiles", os.environ.get("ProgramFiles")),
        ("ProgramData", os.environ.get("ProgramData")),
    ]
    # Ordenamos por longitud de valor, de más específico a más genérico
    # (APPDATA está dentro de USERPROFILE, así que debe probarse antes).
    candidates = [(name, value) for name, value in candidates if value]
    candidates.sort(key=lambda pair: len(pair[1]), reverse=True)

    for name, value in candidates:
        if path.lower().startswith(value.lower()):
            return f"%{name}%" + path[len(value):]
    return path


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

        # Tamaño personalizado (arrastrando la esquina de la tarjeta), en
        # píxeles. Si están presentes, mandan sobre el preset "size" de
        # arriba. None = usar el preset, como hasta ahora.
        width = entry.get("width")
        height = entry.get("height")
        try:
            width = int(width) if width is not None else None
        except (TypeError, ValueError):
            width = None
        try:
            height = int(height) if height is not None else None
        except (TypeError, ValueError):
            height = None
        if width is not None:
            width = max(MIN_TILE_WIDTH, min(MAX_TILE_WIDTH, width))
        if height is not None:
            height = max(MIN_TILE_HEIGHT, min(MAX_TILE_HEIGHT, height))

        color = entry.get("color")
        color = str(color) if color else None

        category = entry.get("category")
        category = str(category) if category else None

        try:
            open_count = int(entry.get("open_count", 0))
        except (TypeError, ValueError):
            open_count = 0
        try:
            last_opened = float(entry.get("last_opened", 0) or 0)
        except (TypeError, ValueError):
            last_opened = 0.0

        item = {
            "id": item_id,
            "type": item_type,
            "name": name,
            "parent_id": parent_id,
            "order": entry.get("order", 0),
            "color": color,
            "size": size,
            "width": width,
            "height": height,
            "category": category,
            "open_count": open_count,
            "last_opened": last_opened,
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


# ---------------------------------------------------------------------------
# Accesos directos de Windows (.lnk): parser binario mínimo, sin pywin32
# (mismo criterio que win_icons.py), para poder arrastrar un .lnk desde
# el Explorador y que la app guarde la ruta REAL a la que apunta (por
# ejemplo el .exe de un programa) en vez de la ruta al propio .lnk.
#
# Formato: "[MS-SHLLINK]: Shell Link (.LNK) Binary File Format", de
# Microsoft. Solo se interpreta lo necesario para sacar la ruta de
# destino (ShellLinkHeader + LinkInfo → LocalBasePath / CommonPathSuffix,
# con preferencia por sus variantes Unicode si existen). Si el .lnk usa
# solo un LinkTargetIDList (por ejemplo accesos a la papelera de
# reciclaje, "Este equipo", o rutas de red exóticas) sin LinkInfo, no se
# puede resolver con este parser ligero y se devuelve None: quien llame
# debe entonces usar el propio .lnk como destino, o descartarlo.
# ---------------------------------------------------------------------------

import struct as _struct

_LNK_MAGIC = bytes.fromhex("4C0000000114020000000000C000000000000046")


def _lnk_read_cstring(data: bytes, offset: int, unicode: bool) -> str | None:
    if offset <= 0 or offset >= len(data):
        return None
    if unicode:
        end = offset
        while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
            end += 2
        try:
            return data[offset:end].decode("utf-16-le", errors="ignore")
        except Exception:
            return None
    end = data.find(b"\x00", offset)
    if end == -1:
        end = len(data)
    try:
        return data[offset:end].decode("mbcs" if sys.platform == "win32" else "latin-1", errors="ignore")
    except Exception:
        return None


def resolve_lnk_target(lnk_path) -> str | None:
    """Intenta extraer la ruta de destino real de un acceso directo .lnk
    de Windows, leyendo su formato binario a mano (sin pywin32/COM).
    Devuelve None si el archivo no es un .lnk válido o no se puede
    resolver (best effort, nunca lanza excepción)."""
    try:
        data = Path(lnk_path).read_bytes()
    except OSError:
        return None

    if len(data) < 76 or data[:20] != _LNK_MAGIC:
        return None

    try:
        link_flags = _struct.unpack_from("<I", data, 20)[0]
        has_id_list = bool(link_flags & 0x1)
        has_link_info = bool(link_flags & 0x2)

        offset = 76
        if has_id_list:
            if offset + 2 > len(data):
                return None
            id_list_size = _struct.unpack_from("<H", data, offset)[0]
            offset += 2 + id_list_size

        target: str | None = None
        if has_link_info and offset + 4 <= len(data):
            link_info_start = offset
            link_info_size = _struct.unpack_from("<I", data, offset)[0]
            header_size = _struct.unpack_from("<I", data, offset + 4)[0]
            info_flags = _struct.unpack_from("<I", data, offset + 8)[0]
            local_base_offset = _struct.unpack_from("<I", data, offset + 16)[0]
            suffix_offset = _struct.unpack_from("<I", data, offset + 20)[0]

            local_base_u = suffix_u = 0
            if header_size >= 0x24 and offset + 0x24 <= len(data):
                local_base_u = _struct.unpack_from("<I", data, offset + 0x1C)[0]
                suffix_u = _struct.unpack_from("<I", data, offset + 0x20)[0]

            has_local_path = bool(info_flags & 0x1)
            if has_local_path:
                base = None
                if local_base_u:
                    base = _lnk_read_cstring(data, link_info_start + local_base_u, unicode=True)
                if not base and local_base_offset:
                    base = _lnk_read_cstring(data, link_info_start + local_base_offset, unicode=False)
                suffix = None
                if suffix_u:
                    suffix = _lnk_read_cstring(data, link_info_start + suffix_u, unicode=True)
                if suffix is None and suffix_offset:
                    suffix = _lnk_read_cstring(data, link_info_start + suffix_offset, unicode=False)
                if base:
                    target = base + (suffix or "")
            offset = link_info_start + link_info_size

        if target:
            return target
    except (_struct.error, IndexError):
        return None
    return None


# ---------------------------------------------------------------------------
# Papelera: los elementos borrados se guardan aquí durante
# TRASH_RETENTION_DAYS días antes de eliminarse definitivamente, por si se
# borran sin querer. No pasan por `_normalize_items` (que asigna un
# "order" recalculado): se guardan tal cual estaban, para poder
# restaurarlos con sus mismos datos.
# ---------------------------------------------------------------------------


def load_trash() -> list[dict]:
    ensure_user_data_dir()
    if not TRASH_PATH.exists():
        return []
    try:
        with TRASH_PATH.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items", []) if isinstance(data, dict) else []
    return [it for it in items if isinstance(it, dict) and "id" in it]


def save_trash(items: list[dict]) -> None:
    ensure_user_data_dir()
    with TRASH_PATH.open("w", encoding="utf-8") as handle:
        json.dump({"items": items}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def purge_old_trash(items: list[dict], days: int = TRASH_RETENTION_DAYS) -> tuple[list[dict], bool]:
    """Quita de la lista los elementos borrados hace más de `days` días.
    Devuelve (lista_filtrada, se_quitó_algo)."""
    cutoff = time.time() - days * 86400
    kept = [it for it in items if float(it.get("deleted_at", 0) or 0) >= cutoff]
    return kept, len(kept) != len(items)


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
