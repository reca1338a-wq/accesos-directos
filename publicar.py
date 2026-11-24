"""Publica una nueva versión en la carpeta de actualizaciones de la empresa.

Uso (desde la carpeta del proyecto, en el PC de quien publica):

    py publicar.py --build          compila con PyInstaller y publica
    py publicar.py                  publica lo que ya haya en dist/main
    py publicar.py --notes "Qué cambia en esta versión"
    py publicar.py --dest "\\\\servidor\\software\\AccesosDirectos"

Qué hace:
  1. Lee la versión de VERSION.
  2. Crea AccesosDirectos-<versión>.zip (app compilada, contenido de dist/main)
     y AccesosDirectos-<versión>-source.zip (los .py, para instalaciones sin
     compilar).
  3. Calcula el SHA-256 de cada zip (la app lo verifica antes de instalar).
  4. FIRMA el manifiesto con tu clave privada (ver generar_claves.py): la app
     solo instala actualizaciones firmadas, aunque alguien consiga escribir en
     la carpeta o publicar en GitHub.
  5. Copia los zip al destino y, POR ÚLTIMO, escribe latest.json de forma
     atómica: los clientes nunca ven una versión anunciada a medio subir.

El destino es la carpeta (o carpeta servida por el servidor web interno) que
los usuarios tienen configurada como UPDATE_FOLDER / UPDATE_URL. Solo quien
publica debe tener permiso de ESCRITURA en ella; los usuarios, solo lectura.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from app_config import UPDATE_MANIFEST_NAME, UPDATE_PUBLIC_KEY, get_update_config
from github_updates import (
    UPDATE_FILES,
    manifest_signing_payload,
    parse_version,
    verify_manifest_signature,
)

PROJECT_DIR = Path(__file__).resolve().parent
DIST_DIR = PROJECT_DIR / "dist" / "main"
PACKAGE_PREFIX = "AccesosDirectos-"
DEFAULT_KEY_PATH = Path.home() / ".accesos-directos-firma" / "clave_privada.pem"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_exe() -> None:
    if sys.platform != "win32":
        sys.exit("--build solo funciona en Windows (PyInstaller genera ejecutables del sistema donde se ejecuta).")
    print("Compilando con PyInstaller...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "main.spec", "--noconfirm", "--clean"],
        cwd=PROJECT_DIR,
        check=True,
    )


def load_private_key(path: Path):
    """Carga la clave privada de firma (pide la contraseña si la tiene)."""
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        sys.exit("Falta la librería «cryptography». Instálala con:  py -m pip install cryptography")
    if not path.is_file():
        sys.exit(
            f"No encuentro la clave privada de firma en {path}.\n"
            "Créala una sola vez con:  py generar_claves.py  (o indica otra con --clave)."
        )
    data = path.read_bytes()
    password = os.environ.get("ACCESOS_FIRMA_CONTRASENA")
    try:
        return serialization.load_pem_private_key(data, password=password.encode() if password else None)
    except TypeError:
        pass  # está protegida con contraseña
    except ValueError as exc:
        sys.exit(f"No se pudo leer la clave privada: {exc}")
    try:
        return serialization.load_pem_private_key(
            data, password=getpass.getpass("Contraseña de la clave de firma: ").encode("utf-8")
        )
    except ValueError:
        sys.exit("Contraseña incorrecta (o clave dañada). No se ha publicado nada.")


def sign_manifest(manifest: dict, private_key) -> None:
    signature = private_key.sign(manifest_signing_payload(manifest))
    manifest["signature"] = base64.b64encode(signature).decode("ascii")


def zip_directory(directory: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())


def zip_source(destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in UPDATE_FILES:
            source = PROJECT_DIR / name
            if source.exists():
                archive.write(source, name)


def copy_atomic(source: Path, destination: Path) -> None:
    """Copia a un .tmp y lo renombra al terminar, para que nadie lea un
    archivo a medio copiar."""
    tmp = destination.with_name(destination.name + ".tmp")
    shutil.copy2(source, tmp)
    os.replace(tmp, destination)


def prune_old_packages(dest: Path, keep: int, current_version: str) -> None:
    """Conserva los zip de las `keep` versiones más recientes (para poder
    volver atrás manualmente) y borra el resto."""
    versions: dict[tuple[int, ...], list[Path]] = {}
    for path in dest.glob(f"{PACKAGE_PREFIX}*.zip"):
        match = re.match(rf"{re.escape(PACKAGE_PREFIX)}(.+?)(-source)?\.zip$", path.name)
        if match:
            versions.setdefault(parse_version(match.group(1)), []).append(path)
    newest = sorted(versions, reverse=True)[: max(keep, 1)]
    keep_set = set(newest) | {parse_version(current_version)}
    for version, paths in versions.items():
        if version not in keep_set:
            for path in paths:
                try:
                    path.unlink()
                    print(f"  Borrado paquete antiguo: {path.name}")
                except OSError:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Publica una versión de Accesos Directos.")
    parser.add_argument("--dest", help="Carpeta de destino (por defecto, la de update_config / UPDATE_FOLDER).")
    parser.add_argument("--build", action="store_true", help="Compilar con PyInstaller antes (solo Windows).")
    parser.add_argument("--notes", default="", help="Texto breve con las novedades de esta versión.")
    parser.add_argument("--keep", type=int, default=3, help="Cuántas versiones conservar en el destino (defecto 3).")
    parser.add_argument("--force", action="store_true", help="Publicar aunque la versión no sea mayor que la ya publicada.")
    parser.add_argument("--clave", default=os.environ.get("ACCESOS_CLAVE_PRIVADA", str(DEFAULT_KEY_PATH)),
                        help="Clave privada de firma (por defecto ~/.accesos-directos-firma/clave_privada.pem).")
    parser.add_argument("--sin-firmar", action="store_true",
                        help="Publicar SIN firma (solo para apps compiladas sin UPDATE_PUBLIC_KEY; no recomendado).")
    args = parser.parse_args()

    private_key = None
    if args.sin_firmar:
        if UPDATE_PUBLIC_KEY.strip():
            sys.exit("UPDATE_PUBLIC_KEY está configurada: las apps rechazarían una actualización sin firma.")
        print("AVISO: se publica SIN firma. Cualquiera con acceso de escritura al destino podría suplantarla.")
    else:
        if not UPDATE_PUBLIC_KEY.strip():
            sys.exit(
                "Aún no hay clave de firma configurada.\n"
                "  1) Ejecuta una vez:  py generar_claves.py\n"
                "  2) Pega la línea UPDATE_PUBLIC_KEY que muestra en app_config.py y recompila.\n"
                "(O usa --sin-firmar si de momento no quieres firmar.)"
            )
        private_key = load_private_key(Path(args.clave))

    version = (PROJECT_DIR / "VERSION").read_text(encoding="utf-8").strip()
    dest_text = args.dest or get_update_config()["folder"]
    if not dest_text:
        sys.exit("Indica el destino con --dest o configura UPDATE_FOLDER en app_config.py.")
    dest = Path(dest_text)
    if not dest.is_dir():
        sys.exit(f"La carpeta de destino no existe o no es accesible: {dest}")

    manifest_path = dest / UPDATE_MANIFEST_NAME
    if manifest_path.exists() and not args.force:
        try:
            published = json.loads(manifest_path.read_text(encoding="utf-8")).get("version", "")
        except (OSError, ValueError):
            published = ""
        if published and parse_version(version) <= parse_version(published):
            sys.exit(
                f"La versión publicada ({published}) ya es igual o mayor que VERSION ({version}). "
                "Sube el número en VERSION (o usa --force)."
            )

    if args.build:
        build_exe()

    work = PROJECT_DIR / "build" / "publicar"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    manifest: dict = {
        "version": version,
        "notes": args.notes,
        "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    packages: list[Path] = []

    if DIST_DIR.is_dir():
        exe_zip = work / f"{PACKAGE_PREFIX}{version}.zip"
        print(f"Empaquetando app compilada -> {exe_zip.name}")
        zip_directory(DIST_DIR, exe_zip)
        manifest["exe"] = {"file": exe_zip.name, "sha256": sha256_of(exe_zip)}
        packages.append(exe_zip)
    else:
        print("AVISO: no existe dist/main; solo se publica el paquete de código fuente (usa --build).")

    src_zip = work / f"{PACKAGE_PREFIX}{version}-source.zip"
    print(f"Empaquetando código fuente -> {src_zip.name}")
    zip_source(src_zip)
    manifest["source"] = {"file": src_zip.name, "sha256": sha256_of(src_zip)}
    packages.append(src_zip)

    for package in packages:
        print(f"Copiando {package.name} a {dest} ...")
        copy_atomic(package, dest / package.name)

    # Instalador para usuarios (sin Python): se deja junto a los paquetes.
    for helper in ("instalar.bat", "instalar.ps1"):
        helper_path = PROJECT_DIR / helper
        if helper_path.exists():
            copy_atomic(helper_path, dest / helper)

    if private_key is not None:
        sign_manifest(manifest, private_key)
        # Comprobación de seguridad: la firma debe validar con la clave pública
        # que lleva la app; si no, esta clave privada no corresponde a esa app.
        try:
            verify_manifest_signature(manifest)
        except Exception as exc:  # noqa: BLE001
            sys.exit(
                "La clave privada no corresponde con UPDATE_PUBLIC_KEY de app_config.py "
                f"({exc}). No se ha publicado nada."
            )
        print("Manifiesto firmado y verificado.")

    manifest_tmp = work / UPDATE_MANIFEST_NAME
    manifest_tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    copy_atomic(manifest_tmp, manifest_path)  # lo último: ahora sí se anuncia la versión

    prune_old_packages(dest, args.keep, version)
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nPublicada la versión {version} en {dest}")


if __name__ == "__main__":
    main()
