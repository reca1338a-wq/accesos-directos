"""Comprobación e instalación de actualizaciones desde GitHub."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app_config import APP_DIR, GITHUB_OWNER, GITHUB_REPO, GITHUB_TOKEN, VERSION_FILE, get_app_version

UPDATE_FILES = ("main.py", "iniciar.bat", "VERSION", "app_config.py", "github_updates.py")


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    download_url: str


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


def check_for_updates(owner: str = "", repo: str = "", token: str = "") -> UpdateInfo | None:
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
    wanted_suffix = ".exe" if frozen else ".zip"
    download_url = ""
    for asset in release.get("assets", []):
        if asset.get("name", "").lower().endswith(wanted_suffix):
            download_url = asset.get("browser_download_url", "")
            break

    if not download_url and not frozen:
        download_url = release.get("zipball_url", "")

    if not download_url:
        raise UpdateError(
            "No se encontró en la release un archivo compatible "
            f"({wanted_suffix}). Revisa que hayas subido ese tipo de archivo al Release."
        )

    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        release_url=release.get("html_url", ""),
        download_url=download_url,
    )


def cleanup_stale_update_files() -> None:
    """Borra restos de una actualización anterior (por si algo quedó a medias).

    Se llama al arrancar la app: si estamos corriendo como el .exe actual,
    cualquier *_nuevo.exe o _actualizar.bat que siga junto a él es basura
    de una actualización ya aplicada (o interrumpida) y se puede eliminar
    con seguridad.
    """
    if not getattr(sys, "frozen", False):
        return

    current_exe = Path(sys.executable).resolve()
    candidates = (
        current_exe.with_name(current_exe.stem + "_nuevo.exe"),
        current_exe.with_name("_actualizar.bat"),
    )
    for stale in candidates:
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass


def apply_update(download_url: str, token: str = "", latest_version: str = "") -> None:
    """Descarga e instala la actualización. Se adapta según si es .exe o .py sueltos."""
    if getattr(sys, "frozen", False):
        _apply_update_exe(download_url, token, latest_version)
    else:
        _apply_update_source(download_url, token)


# ---------------------------------------------------------------------------
# Modo .exe (aplicación empaquetada con PyInstaller)
# ---------------------------------------------------------------------------

def _apply_update_exe(download_url: str, token: str = "", latest_version: str = "") -> None:
    """Descarga el nuevo .exe junto al actual, listo para sustituirlo al reiniciar."""
    headers = {"User-Agent": "AccesosDirectos-Updater"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    request = urllib.request.Request(download_url, headers=headers)
    current_exe = Path(sys.executable).resolve()
    new_exe = current_exe.with_name(current_exe.stem + "_nuevo.exe")

    try:
        with urllib.request.urlopen(request, timeout=60) as response, new_exe.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except Exception as exc:
        new_exe.unlink(missing_ok=True)
        raise UpdateError(f"No se pudo descargar la actualización: {exc}") from exc

    # Actualizamos ya el número de versión mostrado, aunque el .exe se
    # sustituya realmente al reiniciar.
    if latest_version:
        try:
            VERSION_FILE.write_text(latest_version.lstrip("vV") + "\n", encoding="utf-8")
        except OSError:
            pass


def restart_with_update() -> None:
    """Sustituye el .exe actual por el descargado (si lo hay) y reinicia la app."""
    if not getattr(sys, "frozen", False):
        os.execv(sys.executable, [sys.executable, *sys.argv])
        return

    current_exe = Path(sys.executable).resolve()
    new_exe = current_exe.with_name(current_exe.stem + "_nuevo.exe")

    if not new_exe.exists():
        # No hay actualización pendiente, reinicio normal.
        os.execv(sys.executable, [sys.executable, *sys.argv])
        return

    # En Windows no se puede sobrescribir un .exe mientras se está ejecutando,
    # así que dejamos un script que espera a que cerremos, hace el cambio y
    # vuelve a abrir la app.
    #
    # Reintenta el borrado y el movido en bucle en vez de esperar un tiempo
    # fijo: el proceso anterior o el antivirus (que escanea el .exe recién
    # descargado) pueden tardar un instante variable en soltar el archivo.
    # Confirmar cada paso antes de continuar evita el error
    # "Failed to load Python DLL" al abrir el ejecutable a medio mover, y
    # el "if exist %NEW% del" final evita que quede un *_nuevo.exe huérfano
    # si algo se retrasa más de lo normal.
    updater = current_exe.with_name("_actualizar.bat")
    updater.write_text(
        "@echo off\n"
        "setlocal\n"
        f'set "CURRENT={current_exe}"\n'
        f'set "NEW={new_exe}"\n'
        "timeout /t 2 /nobreak >nul\n"
        ":esperar_liberacion\n"
        'del "%CURRENT%" 2>nul\n'
        'if exist "%CURRENT%" (\n'
        "  timeout /t 1 /nobreak >nul\n"
        "  goto esperar_liberacion\n"
        ")\n"
        ":mover\n"
        'move /y "%NEW%" "%CURRENT%" >nul 2>nul\n'
        'if not exist "%CURRENT%" (\n'
        "  timeout /t 1 /nobreak >nul\n"
        "  goto mover\n"
        ")\n"
        'if exist "%NEW%" del /f /q "%NEW%"\n'
        "timeout /t 1 /nobreak >nul\n"
        'start "" "%CURRENT%"\n'
        'del "%~f0"\n',
        encoding="utf-8",
    )
    subprocess.Popen(["cmd", "/c", str(updater)], creationflags=subprocess.CREATE_NO_WINDOW)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Modo fuente (ejecutando con iniciar.bat / python main.py, sin compilar)
# ---------------------------------------------------------------------------

def _download_release_zip(download_url: str, token: str = "") -> Path:
    headers = {"User-Agent": "AccesosDirectos-Updater"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    request = urllib.request.Request(download_url, headers=headers)
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        with urllib.request.urlopen(request, timeout=60) as response, temp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
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


def _apply_update_source(download_url: str, token: str = "") -> None:
    zip_path = _download_release_zip(download_url, token)
    temp_dir = Path(tempfile.mkdtemp())

    try:
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
