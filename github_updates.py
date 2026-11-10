"""Comprobación e instalación de actualizaciones.

Origen configurable (ver UPDATE_SOURCE en app_config.py):
  - "folder": carpeta compartida de la empresa (UNC / unidad de red).
  - "http":   servidor web interno.
  - "github": GitHub Releases (comportamiento original).

Para "folder" y "http" el origen publica un `latest.json` (lo genera
`publicar.py`) con la versión y el .zip + su SHA-256. El nombre del módulo
se mantiene (github_updates) para no romper las instalaciones existentes,
cuyo auto-actualizador solo conoce los archivos listados en UPDATE_FILES.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app_config import (
    APP_DIR,
    GITHUB_OWNER,
    GITHUB_REPO,
    GITHUB_TOKEN,
    UPDATE_MANIFEST_NAME,
    VERSION_FILE,
    get_app_version,
    get_logger,
    get_update_config,
)

# Se añade a los argumentos de la nueva instancia cuando se relanza la app
# muy rápido (tras cambiar el tema o tras actualizar), para que sepa que
# debe darle un momento a la caché de iconos de Windows antes de pintar
# las tarjetas (ver AccesosDirectosApp.__init__ en main.py).
WARM_RESTART_FLAG = "--warm-restart"

# Variables de entorno que el bootloader de PyInstaller usa internamente
# para el .exe empaquetado en modo "onefile" (apuntan a la carpeta
# temporal _MEIxxxxxx donde se descomprimió Python/Tcl/Tk para ESTA
# ejecución). Si se heredan al lanzar una segunda instancia del propio
# .exe, la nueva copia intenta reutilizar esa carpeta temporal en vez de
# crear la suya propia — y en cuanto la primera instancia termina y
# PyInstaller la borra, la segunda falla con errores como "Can't find a
# usable init.tcl" o "Failed to load Tcl/Tk". Hay que quitarlas del
# entorno antes de lanzar la nueva instancia (es el propio PyInstaller
# quien documenta este problema).
_PYINSTALLER_LEAK_VARS = ("_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY")


def _relaunch_env() -> dict:
    env = os.environ.copy()
    for name in _PYINSTALLER_LEAK_VARS:
        env.pop(name, None)
    return env


UPDATE_FILES = (
    "main.py",
    "iniciar.bat",
    "VERSION",
    "app_config.py",
    "github_updates.py",
    "win_icons.py",
    "requirements.txt",
)


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    download_url: str  # URL http(s) o ruta de archivo (carpeta de red)
    sha256: str = ""  # vacío solo en modo "github" (ahí no hay manifiesto)
    token: str = ""  # para descargar (modo "http" con token / GitHub privado)
    notes: str = ""


class UpdateError(Exception):
    pass


def parse_version(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value.lstrip("vV"))
    if not numbers:
        return (0,)
    return tuple(int(part) for part in numbers)


def is_newer_version(current: str, latest: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _github_request(url: str, token: str = "") -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AccesosDirectos-Updater",
    }
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No se encontró el repositorio o no hay releases publicados.") from exc
        if exc.code in (401, 403):
            raise UpdateError(
                "No se pudo acceder al repositorio privado. "
                "Comprueba el token de GitHub en Configuración."
            ) from exc
        raise UpdateError(f"GitHub respondió con error {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"No se pudo conectar con GitHub: {exc.reason}") from exc


# --- Orígenes privados (carpeta de red / servidor interno) ----------------

_SAFE_FILE_NAME = re.compile(r"^[\w.\- ()]+$")


def _manifest_asset(manifest: dict) -> tuple[str, str]:
    """(nombre_de_archivo, sha256) del paquete que corresponde a esta
    instalación: "exe" si es la app compilada, "source" si son .py."""
    key = "exe" if getattr(sys, "frozen", False) else "source"
    asset = manifest.get(key)
    if not isinstance(asset, dict) or not asset.get("file"):
        raise UpdateError(
            f"La versión publicada no incluye el paquete «{key}» para este tipo de instalación."
        )
    name = str(asset["file"])
    sha = str(asset.get("sha256", "")).strip().lower()
    if not _SAFE_FILE_NAME.match(name) or name in (".", ".."):
        raise UpdateError("El manifiesto de actualización tiene un nombre de archivo no válido.")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise UpdateError("El manifiesto de actualización no incluye una suma SHA-256 válida.")
    return name, sha


def _info_from_manifest(manifest: dict, resolve_download, release_url: str, token: str) -> UpdateInfo | None:
    latest = str(manifest.get("version", "")).strip()
    if not latest:
        raise UpdateError("El manifiesto de actualización no indica la versión.")
    current = get_app_version()
    get_logger().info("Actualizaciones: versión instalada %s, versión publicada %s", current, latest)
    if not is_newer_version(current, latest):
        return None
    name, sha = _manifest_asset(manifest)
    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        release_url=release_url,
        download_url=resolve_download(name),
        sha256=sha,
        token=token,
        notes=str(manifest.get("notes", "")),
    )


def _check_folder(folder: str) -> UpdateInfo | None:
    if not folder:
        raise UpdateError("Configura UPDATE_FOLDER en app_config.py (o update_config.json).")
    base = Path(folder)
    manifest_path = base / UPDATE_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UpdateError(
            f"No se puede acceder a la carpeta de actualizaciones ({folder}). "
            "¿Estás conectado a la red de la empresa?"
        ) from exc
    except ValueError as exc:
        raise UpdateError(f"El archivo {UPDATE_MANIFEST_NAME} no es un JSON válido.") from exc
    if not isinstance(manifest, dict):
        raise UpdateError(f"El archivo {UPDATE_MANIFEST_NAME} no tiene el formato esperado.")
    return _info_from_manifest(manifest, lambda name: str(base / name), "", "")


def _http_json(url: str, token: str = "") -> dict:
    headers = {"Accept": "application/json", "User-Agent": "AccesosDirectos-Updater"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise UpdateError("El servidor de actualizaciones rechazó el acceso.") from exc
        if exc.code == 404:
            raise UpdateError("No se encontró el manifiesto de actualizaciones en el servidor.") from exc
        raise UpdateError(f"El servidor de actualizaciones respondió con error {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(
            f"No se pudo conectar con el servidor de actualizaciones: {exc.reason}"
        ) from exc
    except ValueError as exc:
        raise UpdateError("El manifiesto del servidor no es un JSON válido.") from exc


def _check_http(url: str, token: str) -> UpdateInfo | None:
    if not url.lower().startswith(("https://", "http://")):
        raise UpdateError("Configura UPDATE_URL (dirección de latest.json) en app_config.py.")
    manifest = _http_json(url, token)
    if not isinstance(manifest, dict):
        raise UpdateError("El manifiesto del servidor no tiene el formato esperado.")
    return _info_from_manifest(
        manifest, lambda name: urllib.parse.urljoin(url, name), url, token
    )


def check_for_updates(owner: str = "", repo: str = "", token: str = "") -> UpdateInfo | None:
    cfg = get_update_config()
    if cfg["source"] == "folder":
        return _check_folder(cfg["folder"])
    if cfg["source"] == "http":
        return _check_http(cfg["url"], cfg["token"])
    return _check_github(owner, repo, token)


def _check_github(owner: str = "", repo: str = "", token: str = "") -> UpdateInfo | None:
    owner = (owner or GITHUB_OWNER).strip()
    repo = (repo or GITHUB_REPO).strip()
    token = token or GITHUB_TOKEN
    if not owner or not repo:
        raise UpdateError("Configura GITHUB_OWNER y GITHUB_REPO en app_config.py.")

    current = get_app_version()
    release = _github_request(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", token)
    latest = str(release.get("tag_name", "")).strip()
    if not latest:
        raise UpdateError("La release más reciente no tiene etiqueta de versión.")

    if not is_newer_version(current, latest):
        return None

    frozen = getattr(sys, "frozen", False)
    download_url = ""

    if frozen:
        # La app compilada (onedir) se publica como un .zip de la carpeta
        # entera, no un único .exe: ver el paso "Empaquetar" del workflow
        # de GitHub Actions.
        for asset in release.get("assets", []):
            if asset.get("name", "").lower().endswith(".zip"):
                download_url = asset.get("browser_download_url", "")
                break
        if not download_url:
            raise UpdateError(
                "No se encontró en la release un .zip de la app compilada. "
                "Revisa que el workflow de GitHub Actions haya subido ese archivo."
            )
    else:
        # En modo fuente usamos siempre el zip automático del código
        # fuente que genera GitHub para cada release/tag.
        download_url = release.get("zipball_url", "")
        if not download_url:
            raise UpdateError("La release no tiene un zip de código fuente disponible.")

    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        release_url=release.get("html_url", ""),
        download_url=download_url,
        token=token,
    )


def cleanup_stale_update_files() -> None:
    """Borra restos de una actualización anterior (por si algo quedó a medias).

    Se llama al arrancar la app: si estamos corriendo con normalidad,
    cualquier carpeta de preparación o script de actualización que siga
    por ahí es basura de una actualización ya aplicada (o interrumpida) y
    se puede eliminar con seguridad.
    """
    if not getattr(sys, "frozen", False):
        return

    app_dir = Path(sys.executable).resolve().parent
    staging_dir = app_dir.parent / f"{app_dir.name}_actualizacion"
    updater_bat = app_dir.parent / "_actualizar.bat"

    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    if updater_bat.exists():
        try:
            updater_bat.unlink()
        except OSError:
            pass


def _download_with_progress(request, destination: Path, progress_callback=None) -> None:
    """Descarga `request` a `destination` en trozos, avisando del avance.

    `progress_callback(downloaded_bytes, total_bytes)` se llama tras cada
    trozo leído; `total_bytes` es 0 si el servidor no manda
    Content-Length (raro, pero posible)."""
    chunk_size = 262144  # 256 KB
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback(downloaded, total)


def _copy_with_progress(source: Path, destination: Path, progress_callback=None) -> None:
    """Copia un archivo local/de red a `destination` avisando del avance
    (equivalente a _download_with_progress para el origen "folder")."""
    chunk_size = 1024 * 1024
    try:
        total = source.stat().st_size
        copied = 0
        with source.open("rb") as reader, destination.open("wb") as writer:
            while True:
                chunk = reader.read(chunk_size)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
                if progress_callback is not None:
                    progress_callback(copied, total)
    except OSError as exc:
        raise UpdateError(f"No se pudo leer el paquete de actualización: {exc}") from exc


def _fetch_to_file(location: str, destination: Path, token: str = "", progress_callback=None) -> None:
    """Deja el paquete en `destination`, venga de una URL http(s) o de una
    ruta de archivo (carpeta compartida)."""
    if location.lower().startswith(("http://", "https://")):
        headers = {"User-Agent": "AccesosDirectos-Updater"}
        if token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        request = urllib.request.Request(location, headers=headers)
        _download_with_progress(request, destination, progress_callback)
    else:
        _copy_with_progress(Path(location), destination, progress_callback)


def _verify_sha256(path: Path, expected: str) -> None:
    """Comprueba la integridad del paquete descargado antes de instalarlo.
    `expected` vacío = sin comprobación (solo modo GitHub heredado)."""
    if not expected:
        return
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().lower() != expected.strip().lower():
        raise UpdateError(
            "El paquete descargado no coincide con la suma SHA-256 publicada "
            "(archivo dañado o modificado). No se ha instalado nada."
        )


def apply_update(
    download_url: str, token: str = "", latest_version: str = "", progress_callback=None,
    sha256: str = "",
) -> None:
    """Descarga e instala la actualización. Se adapta según si es .exe o .py sueltos."""
    if getattr(sys, "frozen", False):
        _apply_update_exe(download_url, token, latest_version, progress_callback, sha256)
    else:
        _apply_update_source(download_url, token, progress_callback, sha256)


# ---------------------------------------------------------------------------
# Modo .exe (aplicación empaquetada con PyInstaller)
# ---------------------------------------------------------------------------

def _apply_update_exe(
    download_url: str, token: str = "", latest_version: str = "", progress_callback=None,
    sha256: str = "",
) -> None:
    """Descarga el .zip de la nueva versión (app completa, modo onedir) y
    lo deja ya descomprimido en una carpeta de preparación, lista para
    aplicarse al reiniciar (ver `restart_with_update`)."""
    app_dir = Path(sys.executable).resolve().parent
    staging_dir = app_dir.parent / f"{app_dir.name}_actualizacion"
    zip_path = Path(tempfile.gettempdir()) / "accesos-directos-actualizacion.zip"

    try:
        _fetch_to_file(download_url, zip_path, token, progress_callback)
        _verify_sha256(zip_path, sha256)

        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(staging_dir)
    except UpdateError:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise UpdateError(f"No se pudo descargar la actualización: {exc}") from exc
    finally:
        zip_path.unlink(missing_ok=True)

    # Si el zip trae todo metido dentro de una única carpeta contenedora
    # (según cómo se haya comprimido), usamos su contenido directamente.
    children = list(staging_dir.iterdir())
    if len(children) == 1 and children[0].is_dir():
        inner = children[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(staging_dir / item.name))
        inner.rmdir()

    # Actualizamos ya el número de versión mostrado, aunque el cambio
    # real de archivos ocurra al reiniciar.
    if latest_version:
        version_text = latest_version.lstrip("vV") + "\n"
        for target in (staging_dir / "VERSION", VERSION_FILE):
            try:
                target.write_text(version_text, encoding="utf-8")
            except OSError:
                pass


def _close_window(root) -> None:
    """Cierra la ventana principal de Tk de forma limpia antes de salir.

    Si el proceso termina (sys.exit / os.execv) mientras aún hay callbacks
    pendientes (un after() programado, un hilo en segundo plano
    comprobando actualizaciones...) que luego intentan tocar la ventana,
    Tkinter puede lanzar una excepción al intentar reportarla por
    sys.stderr — y en el .exe (app "windowed", sin consola) eso se
    traduce en la ventana de error que PyInstaller muestra al detectar una
    excepción no capturada. Destruir la ventana primero, de forma
    explícita, evita esa condición de carrera en la mayoría de los casos.
    """
    if root is None:
        return
    try:
        root.destroy()
    except Exception:
        pass


def restart_app(root=None) -> None:
    """Reinicia la aplicación de forma segura.

    En modo fuente (python main.py) usamos os.execv, que funciona bien.
    En el .exe empaquetado con PyInstaller, os.execv NO es seguro: rompe
    las variables de entorno que el intérprete embebido necesita, dando
    errores como "Failed to import encodings module". En su lugar,
    lanzamos una copia nueva del propio .exe y cerramos esta con
    normalidad, dejando que el bootloader limpie su carpeta temporal
    correctamente.

    `root`, si se indica, es la ventana principal de Tk: se cierra de
    forma explícita antes de terminar el proceso actual (ver
    `_close_window`).
    """
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable, WARM_RESTART_FLAG], env=_relaunch_env())
        _close_window(root)
        sys.exit(0)
    else:
        _close_window(root)
        os.execv(sys.executable, [sys.executable, *sys.argv])


def restart_with_update(root=None) -> None:
    """Aplica la actualización ya descargada (ver `_apply_update_exe`) y
    reinicia la app."""
    if not getattr(sys, "frozen", False):
        restart_app(root)
        return

    current_exe = Path(sys.executable).resolve()
    app_dir = current_exe.parent
    staging_dir = app_dir.parent / f"{app_dir.name}_actualizacion"

    if not staging_dir.exists():
        # No hay actualización pendiente, reinicio normal.
        restart_app(root)
        return

    # Sincronizamos la carpeta de preparación sobre la carpeta real de la
    # app con robocopy (incluido en Windows) en vez de un bucle de
    # borrado/movido hecho a mano: robocopy ya trae reintentos
    # incorporados pensados exactamente para esto (archivos que un
    # antivirus o el propio proceso anterior aún tienen bloqueados un
    # instante), así que es mucho más fiable.
    updater = app_dir.parent / "_actualizar.bat"
    updater.write_text(
        "@echo off\n"
        "setlocal\n"
        f'set "APPDIR={app_dir}"\n'
        f'set "STAGING={staging_dir}"\n'
        f'set "EXE={current_exe}"\n'
        # Quitamos las variables que el bootloader de PyInstaller pueda
        # dejar en el entorno (ver _PYINSTALLER_LEAK_VARS más arriba).
        "set \"_MEIPASS2=\"\n"
        "set \"TCL_LIBRARY=\"\n"
        "set \"TK_LIBRARY=\"\n"
        "timeout /t 2 /nobreak >nul\n"
        # /E copia subcarpetas (incluidas vacías); /IS/IT también
        # sobrescribe archivos idénticos o "solo con la fecha distinta"
        # (por si acaso); /R:20 /W:1 reintenta hasta 20 veces esperando
        # 1s si algo sigue bloqueado, en vez de rendirse a la primera.
        'robocopy "%STAGING%" "%APPDIR%" /E /IS /IT /R:20 /W:1 /NFL /NDL /NJH /NJS\n'
        'rmdir /s /q "%STAGING%" 2>nul\n'
        "timeout /t 1 /nobreak >nul\n"
        f'start "" "%EXE%" {WARM_RESTART_FLAG}\n'
        'del "%~f0"\n',
        encoding="utf-8",
    )
    subprocess.Popen(["cmd", "/c", str(updater)], creationflags=subprocess.CREATE_NO_WINDOW)
    _close_window(root)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Modo fuente (ejecutando con iniciar.bat / python main.py, sin compilar)
# ---------------------------------------------------------------------------

def _download_release_zip(download_url: str, token: str = "", progress_callback=None) -> Path:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        _fetch_to_file(download_url, temp_path, token, progress_callback)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return temp_path


def _find_repo_root(extracted_dir: Path) -> Path:
    if not extracted_dir.exists():
        raise UpdateError("No se pudo descomprimir la actualización.")
    children = [path for path in extracted_dir.iterdir() if path.is_dir()]
    if len(children) == 1:
        return children[0]
    return extracted_dir


def _apply_update_source(
    download_url: str, token: str = "", progress_callback=None, sha256: str = ""
) -> None:
    zip_path = _download_release_zip(download_url, token, progress_callback)
    temp_dir = Path(tempfile.mkdtemp())

    try:
        _verify_sha256(zip_path, sha256)
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(temp_dir)

        repo_root = _find_repo_root(temp_dir)
        updated_files: list[str] = []

        for filename in UPDATE_FILES:
            source = repo_root / filename
            if not source.exists():
                continue
            destination = APP_DIR / filename
            shutil.copy2(source, destination)
            updated_files.append(filename)

        if not updated_files:
            raise UpdateError("La release no contiene archivos de la aplicación para actualizar.")
    finally:
        zip_path.unlink(missing_ok=True)
        shutil.rmtree(temp_dir, ignore_errors=True)
