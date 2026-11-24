"""Firma una release para el flujo de GitHub Actions.

El workflow compila la app y crea el .zip; este script, en ese mismo workflow,
crea el `latest.json` FIRMADO que debe subirse a la release junto al .zip.

    python firmar_release.py --version 2.5.0.3 --zip AccesosDirectos-2.5.0.3.zip

La clave privada se lee de la variable de entorno FIRMA_CLAVE_PRIVADA (el
CONTENIDO del archivo .pem, guardado como «secret» del repositorio en GitHub),
o con --clave RUTA.pem. Si la clave lleva contraseña: FIRMA_CONTRASENA.

Opciones: --source-zip (zip del código fuente, para instalaciones sin
compilar), --notes "texto", --salida latest.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from app_config import UPDATE_PUBLIC_KEY
from github_updates import manifest_signing_payload, verify_manifest_signature

_NAME_OK = re.compile(r"^[\w.\- ()]+$")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_private_key(clave_path: str | None):
    from cryptography.hazmat.primitives import serialization

    pem_text = os.environ.get("FIRMA_CLAVE_PRIVADA", "")
    if pem_text.strip():
        data = pem_text.replace("\\n", "\n").encode("utf-8")
    elif clave_path and Path(clave_path).is_file():
        data = Path(clave_path).read_bytes()
    else:
        sys.exit("No hay clave de firma: define FIRMA_CLAVE_PRIVADA (secret de GitHub) o usa --clave.")
    password = os.environ.get("FIRMA_CONTRASENA")
    try:
        return serialization.load_pem_private_key(data, password=password.encode("utf-8") if password else None)
    except (TypeError, ValueError) as exc:
        sys.exit(f"No se pudo leer la clave privada (¿contraseña? ¿formato?): {exc}")


def asset_entry(path: Path) -> dict:
    if not _NAME_OK.match(path.name):
        sys.exit(f"Nombre de archivo no válido para publicar: {path.name}")
    return {"file": path.name, "sha256": sha256_of(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea el latest.json firmado de una release.")
    parser.add_argument("--version", required=True, help="Versión, p. ej. 2.5.0.3 (o la etiqueta; se quita la «v»).")
    parser.add_argument("--zip", required=True, help="Zip de la app compilada.")
    parser.add_argument("--source-zip", default="", help="Zip del código fuente (opcional).")
    parser.add_argument("--notes", default="", help="Novedades de esta versión.")
    parser.add_argument("--salida", default="latest.json")
    parser.add_argument("--clave", default="", help="Ruta a la clave privada .pem (alternativa al secret).")
    args = parser.parse_args()

    if not UPDATE_PUBLIC_KEY.strip():
        sys.exit("UPDATE_PUBLIC_KEY está vacía en app_config.py: ejecuta generar_claves.py y pega la clave pública.")

    version = args.version.strip().lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        sys.exit(f"Versión no válida: {args.version!r}")
    zip_path = Path(args.zip)
    if not zip_path.is_file():
        sys.exit(f"No existe el zip: {zip_path}")

    manifest: dict = {
        "version": version,
        "notes": args.notes,
        "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "exe": asset_entry(zip_path),
    }
    if args.source_zip:
        source_path = Path(args.source_zip)
        if not source_path.is_file():
            sys.exit(f"No existe el zip de código fuente: {source_path}")
        manifest["source"] = asset_entry(source_path)

    private_key = load_private_key(args.clave)
    manifest["signature"] = base64.b64encode(private_key.sign(manifest_signing_payload(manifest))).decode("ascii")
    try:
        verify_manifest_signature(manifest)  # debe validar con la clave pública de la app
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"La clave privada no corresponde con UPDATE_PUBLIC_KEY de app_config.py: {exc}")

    Path(args.salida).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Manifiesto firmado y verificado -> {args.salida} (versión {version}, {zip_path.name})")


if __name__ == "__main__":
    main()
