"""Extracción de iconos nativos de Windows para accesos directos.

Obtiene el mismo icono que Windows muestra para un archivo, carpeta o
programa (icono del propio .exe, icono asociado a la extensión, icono de
carpeta, etc.) y lo convierte en un ImageTk.PhotoImage listo para usar en
una tarjeta de Tkinter.

Todo este módulo es "best effort": si falta alguna dependencia (Pillow,
pywin32) o no estamos en Windows, simplemente se desactiva y
`get_icon_photo` devuelve None, para que quien lo use pueda recurrir a un
icono de repuesto (emoji) sin que la app se rompa.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_ICON_SUPPORTED = sys.platform == "win32"

try:
    from PIL import Image, ImageTk
except ImportError:  # Pillow no instalado.
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]
    _ICON_SUPPORTED = False

if _ICON_SUPPORTED:
    try:
        import win32gui  # type: ignore[import]
    except ImportError:  # pywin32 no instalado.
        _ICON_SUPPORTED = False

# Caché de iconos ya extraídos (PhotoImage), para no releer el disco ni
# volver a dibujar el icono cada vez que se redibuja la cuadrícula de
# tarjetas. La clave combina ruta y tamaño en píxeles.
_icon_cache: dict[tuple[str, int], object] = {}

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001
SHGFI_USEFILEATTRIBUTES = 0x000000010
FILE_ATTRIBUTE_NORMAL = 0x80
DI_NORMAL = 0x0003


def icons_available() -> bool:
    """Indica si esta plataforma puede extraer iconos reales de Windows."""
    return _ICON_SUPPORTED


def get_icon_photo(path: str, size: int = 32):
    """Devuelve un ImageTk.PhotoImage con el icono real de `path`, o None
    si no se puede obtener (plataforma no soportada, dependencias
    ausentes, o cualquier error al leerlo)."""
    if not _ICON_SUPPORTED or not path:
        return None

    key = (path, size)
    if key in _icon_cache:
        return _icon_cache[key]

    photo = None
    try:
        image = _extract_icon_image(path, size)
        if image is not None:
            photo = ImageTk.PhotoImage(image)
    except Exception:
        photo = None

    _icon_cache[key] = photo
    return photo


def clear_cache() -> None:
    """Vacía la caché de iconos (por ejemplo al recargar la lista)."""
    _icon_cache.clear()


# ---------------------------------------------------------------------------
# Extracción a bajo nivel usando la API de Windows (GDI / Shell).
# ---------------------------------------------------------------------------


def _extract_icon_image(path: str, size: int):
    target = Path(path)
    flags = SHGFI_ICON | (SHGFI_LARGEICON if size > 20 else SHGFI_SMALLICON)

    if target.exists():
        _ret, info = win32gui.SHGetFileInfo(str(target), 0, flags)
    else:
        # La ruta ya no existe (acceso roto): pedimos el icono genérico
        # según la extensión, sin necesidad de que el archivo exista.
        _ret, info = win32gui.SHGetFileInfo(
            str(target), FILE_ATTRIBUTE_NORMAL, flags | SHGFI_USEFILEATTRIBUTES
        )

    hicon = info[0]
    if not hicon:
        return None

    try:
        return _hicon_to_pil(hicon, size)
    finally:
        win32gui.DestroyIcon(hicon)


class _BITMAPV5HEADER(ctypes.Structure):
    _fields_ = [
        ("bV5Size", ctypes.c_uint32),
        ("bV5Width", ctypes.c_int32),
        ("bV5Height", ctypes.c_int32),
        ("bV5Planes", ctypes.c_uint16),
        ("bV5BitCount", ctypes.c_uint16),
        ("bV5Compression", ctypes.c_uint32),
        ("bV5SizeImage", ctypes.c_uint32),
        ("bV5XPelsPerMeter", ctypes.c_int32),
        ("bV5YPelsPerMeter", ctypes.c_int32),
        ("bV5ClrUsed", ctypes.c_uint32),
        ("bV5ClrImportant", ctypes.c_uint32),
        ("bV5RedMask", ctypes.c_uint32),
        ("bV5GreenMask", ctypes.c_uint32),
        ("bV5BlueMask", ctypes.c_uint32),
        ("bV5AlphaMask", ctypes.c_uint32),
        ("bV5CSType", ctypes.c_uint32),
        ("bV5Endpoints", ctypes.c_byte * 36),
        ("bV5GammaRed", ctypes.c_uint32),
        ("bV5GammaGreen", ctypes.c_uint32),
        ("bV5GammaBlue", ctypes.c_uint32),
        ("bV5Intent", ctypes.c_uint32),
        ("bV5ProfileData", ctypes.c_uint32),
        ("bV5ProfileSize", ctypes.c_uint32),
        ("bV5Reserved", ctypes.c_uint32),
    ]


def _hicon_to_pil(hicon: int, size: int):
    """Convierte un HICON de Windows en una imagen PIL con transparencia
    real, dibujándolo sobre una sección DIB de 32 bits (esto conserva el
    canal alfa de los iconos modernos de Windows, a diferencia de dibujar
    directamente sobre un bitmap normal)."""
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32

    hdc_screen = win32gui.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)

    header = _BITMAPV5HEADER()
    header.bV5Size = ctypes.sizeof(_BITMAPV5HEADER)
    header.bV5Width = size
    header.bV5Height = -size  # Negativo = de arriba a abajo (sin voltear).
    header.bV5Planes = 1
    header.bV5BitCount = 32
    header.bV5Compression = 3  # BI_BITFIELDS
    header.bV5RedMask = 0x00FF0000
    header.bV5GreenMask = 0x0000FF00
    header.bV5BlueMask = 0x000000FF
    header.bV5AlphaMask = 0xFF000000

    ptr_bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        hdc_mem, ctypes.byref(header), 0, ctypes.byref(ptr_bits), None, 0
    )
    if not hbmp:
        win32gui.ReleaseDC(0, hdc_screen)
        return None

    old_bmp = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        user32.DrawIconEx(hdc_mem, 0, 0, hicon, size, size, 0, None, DI_NORMAL)
        buffer_size = size * size * 4
        raw = ctypes.string_at(ptr_bits, buffer_size)
        image = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
        image = image.copy()  # Copia propia antes de liberar el buffer nativo.
    finally:
        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        win32gui.ReleaseDC(0, hdc_screen)

    # Los iconos antiguos (sin canal alfa) quedan con alfa=0 en todos los
    # píxeles tras este proceso; los tratamos como totalmente opacos.
    alpha = image.getchannel("A")
    if alpha.getextrema() == (0, 0):
        image.putalpha(255)

    return image
