"""Comprobación e instalación de actualizaciones desde GitHub."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app_config import APP_DIR, get_app_version

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


def check_for_updates(owner: str, repo: str, token: str = "") -> UpdateInfo | None:
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        raise UpdateError("Configura el propietario y nombre del repositorio en Configuración.")

    current = get_app_version()
    release = _github_request(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", token)
    latest = str(release.get("tag_name", "")).strip()
    if not latest:
        raise UpdateError("La release más reciente no tiene etiqueta de versión.")

    if not is_newer_version(current, latest):
        return None

    download_url = ""
    for asset in release.get("assets", []):
        if asset.get("name", "").lower().endswith(".zip"):
            download_url = asset.get("browser_download_url", "")
            break

    if not download_url:
        download_url = release.get("zipball_url", "")

    if not download_url:
        raise UpdateError("No se encontró un archivo ZIP en la release más reciente.")

    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        release_url=release.get("html_url", ""),
        download_url=download_url,
    )


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


def apply_update(download_url: str, token: str = "") -> None:
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
