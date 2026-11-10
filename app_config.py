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
ICON_FILE = APP_DIR / "icon.ico"
LEGACY_SHORTCUTS_PATH = APP_DIR / "shortcuts.json"

# ---------------------------------------------------------------------------
# Origen de las actualizaciones. Se configura aquí, en el código (o con el
# archivo opcional `update_config.json` junto al .exe, ver más abajo): el
# usuario final no puede cambiarlo desde la aplicación.
#
#   "folder"  carpeta compartida de la empresa (ruta UNC \\servidor\recurso
#             o unidad de red). Sin servidor web, sin tokens: el acceso lo
#             controlan los permisos NTFS/AD de esa carpeta.
#   "http"    servidor web interno (intranet) que sirve `latest.json` + los
#             .zip. Útil si los PCs no ven la carpeta de red (p. ej. VPN).
#   "github"  GitHub Releases (repositorio público, o privado con token).
#             Es el comportamiento de las versiones anteriores.
# ---------------------------------------------------------------------------
UPDATE_SOURCE = "folder"
UPDATE_FOLDER = r"\\192.168.1.254\AccesosDirectos$"  # UPDATE_SOURCE = "folder"
UPDATE_URL = ""  # UPDATE_SOURCE = "http", ej. "https://intranet.empresa.local/accesos/latest.json"
UPDATE_HTTP_TOKEN = ""  # opcional: se envía como "Authorization: Bearer ..." en modo "http"

# UPDATE_SOURCE = "github" (heredado)
GITHUB_OWNER = "reca1338a-wq"
GITHUB_REPO = "accesos-directos"
GITHUB_TOKEN = ""  # Déjalo vacío si el repositorio es público.

UPDATE_SOURCES = ("folder", "http", "github")
UPDATE_MANIFEST_NAME = "latest.json"
# Archivo OPCIONAL junto al .exe que sobrescribe lo de arriba sin recompilar
# (ej. para que TI cambie la ruta del servidor al desplegar). Claves:
# "source", "folder", "url", "token".
UPDATE_CONFIG_FILE = APP_DIR / "update_config.json"

if sys.platform == "win32":
    USER_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "AccesosDirectos"
else:
    USER_DATA_DIR = Path.home() / ".config" / "accesos-directos"

SHORTCUTS_PATH = USER_DATA_DIR / "shortcuts.json"
SETTINGS_PATH = USER_DATA_DIR / "settings.json"
TRASH_PATH = USER_DATA_DIR / "trash.json"
LOG_FILE = USER_DATA_DIR / "log.txt"
LOG_MAX_BYTES = 1_000_000  # ~1 MB; se rota (se guarda una copia .1) al superarlo.

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

# Cuándo mostrar la ruta original debajo de cada acceso directo:
# "always" (como hasta ahora), "never" (nunca) o "on_select" (solo en los
# accesos seleccionados en cada momento).
PATH_DISPLAY_MODES = ("always", "never", "on_select")
DEFAULT_PATH_DISPLAY = "always"

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
    # Ver PATH_DISPLAY_MODES arriba.
    "path_display": DEFAULT_PATH_DISPLAY,
    "sidebar_visible": False,
    "tray_notice_shown": False,
    # Si se inicia con Windows (ver startup_supported/set_startup_enabled),
    # controla si arranca ya minimizado en la bandeja o mostrando la ventana.
    "start_minimized": False,
    # Avisar con un globo de la bandeja si la comprobación de salud, al
    # arrancar, encuentra accesos rotos.
    "notify_broken_links": True,
}

DEFAULT_SHORTCUTS = [
    {"name": "Documentos", "path": str(Path.home() / "Documents")},
    {"name": "Escritorio", "path": str(Path.home() / "Desktop")},
    {"name": "Descargas", "path": str(Path.home() / "Downloads")},
]


def get_update_config() -> dict:
    """Configuración efectiva del origen de actualizaciones: las constantes
    de arriba, sobrescritas por `update_config.json` si existe."""
    cfg = {
        "source": UPDATE_SOURCE,
        "folder": UPDATE_FOLDER,
        "url": UPDATE_URL,
        "token": UPDATE_HTTP_TOKEN,
    }
    try:
        data = json.loads(UPDATE_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in cfg:
                if isinstance(data.get(key), str):
                    cfg[key] = data[key].strip()
    except (OSError, ValueError):
        pass
    if cfg["source"] not in UPDATE_SOURCES:
        cfg["source"] = "folder"
    return cfg


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


def disk_folder_target(path: str, allow_slow: bool = True) -> str | None:
    """Si `path` (ruta guardada en un acceso) es una CARPETA del disco, o un
    .lnk cuyo destino es una carpeta, devuelve la ruta real de esa carpeta;
    si no (archivo, programa, ruta rota...), None. Nunca lanza excepción."""
    if not path:
        return None
    try:
        # Rutas pegadas a mano con comillas o espacios de más.
        candidate = expand_path(str(path).strip().strip('"').strip())
        if candidate.lower().endswith(".lnk"):
            # `allow_slow` permite el método lento (PowerShell): sí al abrir
            # UN acceso, no al escanear muchos (p. ej. al buscar).
            resolved = resolve_lnk_target(candidate, allow_slow=allow_slow)
            if not resolved:
                return None
            candidate = expand_path(resolved.strip().strip('"'))
        return candidate if Path(candidate).expanduser().is_dir() else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Importar una carpeta del disco como árbol de carpetas internas + accesos.
# ---------------------------------------------------------------------------

# Límites de seguridad: soltar por error "C:\\" no debe congelar la app ni
# crear cientos de miles de tarjetas.
FOLDER_IMPORT_MAX_ITEMS = 10000
FOLDER_IMPORT_MAX_DEPTH = 25
# A partir de cuántos elementos se pide confirmación antes de importar.
FOLDER_IMPORT_CONFIRM_THRESHOLD = 200
_IMPORT_SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "$recycle.bin", "system volume information"}
_WIN_ATTR_HIDDEN_SYSTEM = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def _new_entry(item_type: str, name: str, parent_id: str | None, order: int) -> dict:
    return {
        "id": new_item_id(),
        "type": item_type,
        "name": name,
        "parent_id": parent_id,
        "order": order,
        "color": None,
        "size": DEFAULT_SIZE,
        "width": None,
        "height": None,
        "category": None,
        "open_count": 0,
        "last_opened": 0.0,
        "favorite": False,
    }


def _is_hidden_entry(entry: "os.DirEntry") -> bool:
    if entry.name.startswith(".") or entry.name.lower() in _IMPORT_SKIP_NAMES:
        return True
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & _WIN_ATTR_HIDDEN_SYSTEM)


def build_folder_tree_items(
    root_path,
    parent_id: str | None = None,
    order: int = 0,
    max_items: int = FOLDER_IMPORT_MAX_ITEMS,
    max_depth: int = FOLDER_IMPORT_MAX_DEPTH,
) -> tuple[list[dict], dict]:
    """Recorre `root_path` y devuelve (entradas, estadísticas) que reproducen
    su árbol: una carpeta interna por cada carpeta del disco y un acceso
    directo por cada archivo, con los `parent_id` ya enlazados. La carpeta
    raíz cuelga de `parent_id` con el `order` indicado.

    - Dentro de cada carpeta van primero las subcarpetas y luego los
      archivos, ordenados por nombre.
    - Se omiten archivos/carpetas ocultos o de sistema, y los enlaces
      simbólicos a carpetas (evita ciclos y salirse del árbol).
    - Los .lnk se guardan apuntando a su destino real cuando se puede.
    - Se detiene al llegar a `max_items` (stats["truncated"] = True).

    stats = {"folders": n, "files": n, "skipped": n, "truncated": bool}
    Solo lee el disco; no modifica nada."""
    root = Path(root_path)
    stats = {"folders": 0, "files": 0, "skipped": 0, "truncated": False}
    entries: list[dict] = []
    visited: set[str] = set()

    def total() -> int:
        return len(entries)

    def walk(directory: Path, folder_entry: dict, depth: int) -> None:
        try:
            real = os.path.realpath(directory)
        except OSError:
            stats["skipped"] += 1
            return
        if real in visited or depth > max_depth:
            stats["skipped"] += 1
            return
        visited.add(real)

        try:
            with os.scandir(directory) as it:
                children = list(it)
        except OSError:  # sin permiso, ruta desaparecida...
            stats["skipped"] += 1
            return

        subdirs, files = [], []
        for child in children:
            if _is_hidden_entry(child):
                stats["skipped"] += 1
                continue
            try:
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError:
                stats["skipped"] += 1
                continue
            (subdirs if is_dir else files).append(child)
            # Un symlink a carpeta no es is_dir(follow_symlinks=False): cae
            # en `files`; se descarta abajo si apunta a una carpeta.

        subdirs.sort(key=lambda e: e.name.casefold())
        files.sort(key=lambda e: e.name.casefold())

        position = 0
        for sub in subdirs:
            if total() >= max_items:
                stats["truncated"] = True
                return
            entry = _new_entry("folder", sub.name, folder_entry["id"], position)
            position += 1
            entries.append(entry)
            stats["folders"] += 1
            walk(Path(sub.path), entry, depth + 1)

        for f in files:
            if total() >= max_items:
                stats["truncated"] = True
                return
            try:
                if f.is_symlink() and f.is_dir():
                    stats["skipped"] += 1
                    continue
            except OSError:
                pass
            is_lnk = f.name.lower().endswith(".lnk")
            target = (resolve_lnk_target(f.path) if is_lnk else None) or f.path
            entry = _new_entry("shortcut", Path(f.name).stem if is_lnk else f.name, folder_entry["id"], position)
            entry["path"] = portabilize_path(target)
            position += 1
            entries.append(entry)
            stats["files"] += 1

    root_entry = _new_entry("folder", root.name or str(root), parent_id, order)
    root_entry["source_path"] = portabilize_path(str(root))
    entries.append(root_entry)
    stats["folders"] += 1
    walk(root, root_entry, 0)
    return entries, stats


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
        if it["type"] in ("shortcut", "url") and int(it.get("open_count", 0) or 0) > 0
    ]
    used.sort(key=lambda it: (-int(it.get("open_count", 0) or 0), -(it.get("last_opened") or 0)))
    return used[:limit]


def recent_items(items: list[dict], limit: int = 15) -> list[dict]:
    """Devuelve hasta `limit` accesos (no carpetas) ordenados por la
    última vez que se abrieron, del más reciente al más antiguo. A
    diferencia de most_used_items (frecuencia), esto es un historial real
    por orden cronológico. Ignora los que nunca se han abierto."""
    used = [
        it for it in items
        if it["type"] in ("shortcut", "url") and (it.get("last_opened") or 0) > 0
    ]
    used.sort(key=lambda it: -(it.get("last_opened") or 0))
    return used[:limit]


def known_urls(items: list[dict]) -> list[str]:
    """Direcciones ya usadas en otros accesos de tipo url, sin repetir,
    para sugerirlas con autocompletado al añadir una nueva."""
    seen: list[str] = []
    for it in items:
        if it["type"] == "url" and it.get("url") and it["url"] not in seen:
            seen.append(it["url"])
    return seen


def favorite_items(items: list[dict]) -> list[dict]:
    """Devuelve los accesos y carpetas marcados como favoritos, ordenados
    alfabéticamente por nombre."""
    favorites = [it for it in items if it.get("favorite")]
    favorites.sort(key=lambda it: it["name"].casefold())
    return favorites


def ensure_user_data_dir() -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


# Validadores por ajuste: cuando un valor guardado no es válido (ej. un
# tema que ya no existe), se usa el valor por defecto en su lugar. Los
# ajustes que no aparecen aquí se cargan tal cual, solo forzando el mismo
# tipo (bool/str/...) que su valor por defecto.
_SETTINGS_VALIDATORS = {
    "click_mode": lambda v: v if v in ("single", "double") else DEFAULT_SETTINGS["click_mode"],
    "theme": lambda v: v if v in THEMES else DEFAULT_THEME,
    "sort_mode": lambda v: v if v in SORT_MODES else "manual",
    "card_style": lambda v: v if v in ("cards", "compact") else "cards",
    "path_display": lambda v: v if v in PATH_DISPLAY_MODES else DEFAULT_PATH_DISPLAY,
    "categories": lambda v: {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {},
    "window_geometry": lambda v: str(v) if isinstance(v, str) else "",
}


def load_settings() -> dict:
    ensure_user_data_dir()
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS.copy())
        return json.loads(json.dumps(DEFAULT_SETTINGS))

    with SETTINGS_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)

    # Recorremos DEFAULT_SETTINGS (no `data`) para que cualquier ajuste
    # nuevo que se añada en el futuro se cargue solo en cuanto se declare
    # ahí arriba, sin tener que acordarse de añadir también una línea aquí
    # (así es como sidebar_visible se quedó "guardándose pero sin
    # recuperarse nunca" la primera vez).
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key, default_value in DEFAULT_SETTINGS.items():
        if key not in data:
            continue
        value = data[key]
        validator = _SETTINGS_VALIDATORS.get(key)
        if validator is not None:
            merged[key] = validator(value)
        elif isinstance(default_value, bool):
            merged[key] = bool(value)
        else:
            merged[key] = value
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

# Se añade al comando de arranque con Windows cuando el usuario elige
# "Iniciar minimizado" — main.py comprueba sys.argv al arrancar y, si
# está presente, se salta el window.show() inicial (la app abre
# directamente en la bandeja, sin mostrar la ventana).
START_MINIMIZED_FLAG = "--start-minimized"


def _startup_command(minimized: bool = False) -> str:
    suffix = f" {START_MINIMIZED_FLAG}" if minimized else ""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}"{suffix}'
    # Modo fuente (sin compilar): usa pythonw.exe para que no abra una
    # consola al arrancar con Windows.
    python_dir = Path(sys.executable).resolve().parent
    pythonw = python_dir / "pythonw.exe"
    interpreter = pythonw if pythonw.exists() else Path(sys.executable).resolve()
    script = (APP_DIR / "main.py").resolve()
    return f'"{interpreter}" "{script}"{suffix}'


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


def set_startup_enabled(enabled: bool, minimized: bool = False) -> None:
    if not startup_supported():
        return
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, _startup_command(minimized))
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
        if item_type not in ("shortcut", "folder", "url"):
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
            "favorite": bool(entry.get("favorite", False)),
        }

        if item_type == "shortcut":
            path = str(entry.get("path", "")).strip()
            if not path:
                continue
            item["path"] = path
        elif item_type == "folder":
            # Carpeta importada desde el disco: se recuerda su origen para
            # poder "Abrir en el Explorador" y "Actualizar desde disco".
            source_path = str(entry.get("source_path", "") or "").strip()
            if source_path:
                item["source_path"] = source_path
        elif item_type == "url":
            url = str(entry.get("url", "")).strip()
            if not url:
                continue
            if "://" not in url:
                url = "https://" + url
            item["url"] = url

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
    # Con muchos elementos, JSON compacto (indentar 15000 entradas tarda
    # y triplica el tamaño). Escritura atómica: a un .tmp y se renombra,
    # así un corte a mitad de guardado no deja el archivo a medias.
    tmp_path = SHORTCUTS_PATH.with_name(SHORTCUTS_PATH.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        if len(normalized) <= 300:
            json.dump({"items": normalized}, handle, indent=2, ensure_ascii=False)
        else:
            json.dump({"items": normalized}, handle, separators=(",", ":"), ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp_path, SHORTCUTS_PATH)


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


def _resolve_lnk_target_linkinfo(lnk_path) -> str | None:
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


def _resolve_lnk_target_idlist(lnk_path) -> str | None:
    """Segundo intento para los .lnk que NO traen LinkInfo (típico de los
    accesos a carpetas de red, unidades asignadas o carpetas movidas): se
    le pasa al propio Windows el LinkTargetIDList del .lnk mediante
    SHGetPathFromIDListW (shell32, vía ctypes, sin COM ni pywin32). Solo
    funciona en Windows; en otros sistemas o si falla devuelve None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        data = Path(lnk_path).read_bytes()
        if len(data) < 78 or data[:20] != _LNK_MAGIC:
            return None
        link_flags = _struct.unpack_from("<I", data, 20)[0]
        if not link_flags & 0x1:  # sin LinkTargetIDList
            return None
        size = _struct.unpack_from("<H", data, 76)[0]
        idlist = data[78:78 + size]
        if size < 2 or len(idlist) != size:
            return None

        pidl = ctypes.create_string_buffer(idlist + b"\0\0")  # terminador extra por seguridad
        shell32 = ctypes.WinDLL("shell32")
        func = shell32.SHGetPathFromIDListW
        func.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        func.restype = ctypes.c_int
        out = ctypes.create_unicode_buffer(260)  # MAX_PATH
        if func(ctypes.addressof(pidl), out):
            return out.value or None
    except Exception:
        return None
    return None


def _path_exists_safe(path: str) -> bool:
    try:
        return os.path.exists(expand_path(path))
    except (OSError, ValueError):
        return False


_lnk_slow_cache: dict[tuple[str, float], str | None] = {}


def _resolve_lnk_target_powershell(lnk_path) -> str | None:
    """Último recurso (solo Windows): le pregunta a Windows el destino del
    .lnk con WScript.Shell, que entiende TODOS los tipos de acceso directo
    (los que solo traen IDList, carpetas de red, OneDrive...). Tarda unas
    décimas de segundo, por eso solo se usa cuando lo demás falla y al abrir
    un único acceso. Nunca lanza excepción."""
    if sys.platform != "win32":
        return None
    try:
        mtime = Path(lnk_path).stat().st_mtime
    except OSError:
        return None
    key = (str(lnk_path), mtime)
    if key in _lnk_slow_cache:
        return _lnk_slow_cache[key]

    result = None
    try:
        import subprocess

        env = dict(os.environ, LNK_PATH=str(lnk_path))
        command = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK_PATH).TargetPath"
        )
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=10, env=env,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: sin ventana negra
        )
        text = done.stdout.decode("utf-8", errors="ignore").strip()
        result = text or None
    except Exception:
        result = None
    _lnk_slow_cache[key] = result
    return result


def resolve_lnk_target(lnk_path, allow_slow: bool = False) -> str | None:
    """Destino real de un acceso directo .lnk de Windows, o None.

    Prueba primero la ruta guardada en LinkInfo y, si no existe o el .lnk
    no la trae, la que resuelve Windows a partir del LinkTargetIDList. Se
    devuelve el primer destino que EXISTE; si ninguno existe, el primero
    que se haya podido leer (para que quien llame pueda mostrar un acceso
    roto en vez de nada). Nunca lanza excepción."""
    primary = _resolve_lnk_target_linkinfo(lnk_path)
    if primary and _path_exists_safe(primary):
        return primary
    alternative = _resolve_lnk_target_idlist(lnk_path)
    if alternative and _path_exists_safe(alternative):
        return alternative
    if allow_slow:
        slow = _resolve_lnk_target_powershell(lnk_path)
        if slow and _path_exists_safe(slow):
            return slow
        primary = primary or alternative or slow
        return primary
    return primary or alternative


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


def export_shortcuts(items: list[dict], destination: Path, categories: dict | None = None) -> None:
    normalized = _normalize_items(items)
    payload = {"items": normalized}
    # Las categorías (nombre -> color) viven en settings.json, no en cada
    # elemento, así que si no se incluyen aquí aparte, un acceso exportado
    # con categoría "Trabajo" llegaría a otro PC sin saber de qué color
    # pintar esa franja.
    if categories:
        payload["categories"] = dict(categories)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def import_shortcuts(
    source: Path, mode: str, existing_categories: dict | None = None
) -> tuple[list[dict], dict]:
    """Devuelve (accesos_resultantes, categorías_resultantes). Las
    categorías del archivo importado se fusionan con las que ya había:
    los nombres nuevos se añaden con su color; los que ya existían
    localmente conservan el color que ya tenías (no se sobrescribe)."""
    with source.open(encoding="utf-8") as handle:
        raw_data = json.load(handle)
    imported_categories = raw_data.get("categories", {}) if isinstance(raw_data, dict) else {}
    imported_categories = (
        {str(k): str(v) for k, v in imported_categories.items()}
        if isinstance(imported_categories, dict)
        else {}
    )

    merged_categories = dict(existing_categories or {})
    for name, color in imported_categories.items():
        merged_categories.setdefault(name, color)

    imported = parse_shortcuts_file(source)
    if mode == "replace":
        save_shortcuts(imported)
        return imported, merged_categories

    current = load_shortcuts()
    existing_paths = {item["path"] for item in current if item["type"] == "shortcut"}
    existing_urls = {item["url"] for item in current if item["type"] == "url"}
    merged = list(current)
    next_order = sum(1 for item in current if item["parent_id"] is None)

    for entry in imported:
        if entry["type"] == "shortcut" and entry["path"] in existing_paths:
            continue
        if entry["type"] == "url" and entry.get("url") in existing_urls:
            continue
        new_entry = dict(entry)
        new_entry["id"] = new_item_id()
        new_entry["parent_id"] = None  # Los importados se colocan en la raíz.
        new_entry["order"] = next_order
        next_order += 1
        merged.append(new_entry)
        if new_entry["type"] == "shortcut":
            existing_paths.add(new_entry["path"])
        elif new_entry["type"] == "url":
            existing_urls.add(new_entry["url"])

    save_shortcuts(merged)
    return merged, merged_categories


# ---------------------------------------------------------------------------
# Registro de errores en disco (log.txt), para poder diagnosticar fallos
# sin depender de que alguien capture pantallazos del error. Con
# --windowed (sin consola) es la única forma práctica de ver qué pasó.
# ---------------------------------------------------------------------------


def setup_logging():
    """Configura el registro en archivo. Es seguro llamarla varias veces
    (solo se configura de verdad la primera). Devuelve el logger."""
    import logging
    from logging.handlers import RotatingFileHandler

    logger = logging.getLogger("accesos_directos")
    if logger.handlers:
        return logger

    ensure_user_data_dir()
    logger.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    except OSError:
        # Si por lo que sea no se puede escribir el archivo (permisos,
        # disco lleno...), seguimos sin registro en vez de romper la app.
        logger.addHandler(logging.NullHandler())
    return logger


def get_logger():
    import logging

    return logging.getLogger("accesos_directos")
