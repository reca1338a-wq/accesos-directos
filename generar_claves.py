"""Genera el par de claves con el que se firman las actualizaciones.

Se ejecuta UNA SOLA VEZ, en el PC desde el que publicas:

    py generar_claves.py

Crea:
  - la clave PRIVADA, en  ~/.accesos-directos-firma/clave_privada.pem
    (en Windows: C:\\Users\\TU_USUARIO\\.accesos-directos-firma\\clave_privada.pem).
    Es la que firma cada versión. NO la subas a GitHub, NO la dejes en la
    carpeta de red y haz una copia de seguridad (pendrive, gestor de
    contraseñas). Si la pierdes, las apps ya instaladas no aceptarán más
    actualizaciones y habría que reinstalarlas a mano.
  - la clave PÚBLICA, que se muestra por pantalla: se pega en app_config.py
    (UPDATE_PUBLIC_KEY = "...") y se vuelve a compilar la app.

Es gratis: no hace falta ningún certificado de pago.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import sys
from pathlib import Path

DEFAULT_KEY_PATH = Path.home() / ".accesos-directos-firma" / "clave_privada.pem"


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera las claves de firma de actualizaciones.")
    parser.add_argument("--salida", default=str(DEFAULT_KEY_PATH), help="Dónde guardar la clave privada.")
    args = parser.parse_args()

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        sys.exit("Falta la librería «cryptography». Instálala con:  py -m pip install cryptography")

    target = Path(args.salida)
    if target.exists():
        sys.exit(
            f"Ya existe una clave privada en {target}.\n"
            "No la sobrescribo: si generaras otra, las apps ya instaladas dejarían de aceptar tus "
            "actualizaciones. Si de verdad quieres cambiarla, muévela antes a otro sitio."
        )

    print("Puedes proteger la clave privada con una contraseña (recomendado).")
    print("Se te pedirá cada vez que publiques. Déjala vacía para no usar contraseña.")
    password = getpass.getpass("Contraseña: ")
    if password and getpass.getpass("Repite la contraseña: ") != password:
        sys.exit("Las contraseñas no coinciden. No se ha creado nada.")

    private_key = Ed25519PrivateKey.generate()
    encryption = (
        serialization.BestAvailableEncryption(password.encode("utf-8"))
        if password
        else serialization.NoEncryption()
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pem)
    try:
        os.chmod(target, 0o600)  # solo el propietario (en Windows no hace nada)
    except OSError:
        pass

    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    public_b64 = base64.b64encode(public_raw).decode("ascii")

    print(f"\nClave privada guardada en: {target}")
    print("  -> Haz una copia de seguridad y NO la compartas con nadie.\n")
    print("Pega esta línea en app_config.py (sustituye la que hay) y vuelve a compilar la app:\n")
    print(f'UPDATE_PUBLIC_KEY = "{public_b64}"\n')


if __name__ == "__main__":
    main()
