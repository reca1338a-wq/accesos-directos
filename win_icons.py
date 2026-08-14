"""Extracción de iconos nativos de Windows para accesos directos.

Obtiene el mismo icono que Windows muestra para un archivo, carpeta o
programa (el de la aplicación predeterminada asociada a esa extensión —
por ejemplo HeidiSQL para un .sql, o el navegador por defecto para un
.html — el icono del propio .exe, el de carpeta, etc.) y lo convierte en
un ImageTk.PhotoImage listo para usar en una tarjeta de Tkinter.

Deliberadamente NO usa pywin32: llama directamente a la API de Windows
(shell32/user32/gdi32) con `ctypes`, que forma parte de la librería
estándar de Python. pywin32 necesita sus propias DLL nativas que dan
muchos problemas tanto con pip como al empaquetar con PyInstaller (es la
causa más habitual de que los iconos "no funcionen" en el .exe aunque en
apariencia todo esté instalado). Con ctypes no hay nada que empaquetar
aparte del propio Python: las DLL de Windows (shell32.dll, user32.dll,
gdi32.dll) siempre están presentes en el sistema operativo.

Todo este módulo es "best effort": si falta Pillow, o no estamos en
Windows, o cualquier llamada falla, `get_icon_photo` devuelve None, para
que quien lo use recurra a un icono de repuesto (emoji) sin que la app
se rompa.
"""

from __future__ import annotations

import ctypes
import hashlib
import shutil
import sys
from ctypes import wintypes
from pathlib import Path

_ICON_SUPPORTED = sys.platform == "win32"

try:
    from PIL import Image, ImageTk
except ImportError:  # Pillow no instalado.
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

try:
    from app_config import USER_DATA_DIR
except ImportError:  # por si se importa este módulo suelto, sin el resto de la app.
    USER_DATA_DIR = Path.home() / ".accesos-directos"

# Caché de iconos en disco: extraer un icono de Windows (SHGetFileInfoW +
# dibujarlo en un DIB) no es carísimo, pero sí se nota al abrir la app con
# muchas tarjetas, sobre todo porque la caché en memoria (`_icon_cache`, más
# abajo) se vacía cada vez que se cierra la app. Guardar el resultado como
# PNG hace que la *primera* carga tras abrir la app también sea rápida.
_DISK_CACHE_DIR = USER_DATA_DIR / "icon_cache"

# Extensiones que tratamos como "imagen": en vez de pedirle a Windows el
# icono asociado a esa extensión, mostramos una miniatura real del propio
# archivo. Esto solo necesita Pillow, no hace falta ser Windows.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".ico"}

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
MAX_PATH = 260


def icons_available() -> bool:
    """Indica si esta plataforma puede pedir a Windows el icono de la
    aplicación predeterminada de cada tipo de archivo.

    Las miniaturas de imagen (ver `IMAGE_EXTENSIONS`) funcionan aparte,
    con solo tener Pillow instalado, en cualquier sistema operativo.
    """
    return _ICON_SUPPORTED


def _disk_cache_key(path: str, size: int) -> str:
    # Se incluye la fecha de modificación del archivo (si existe) en la
    # clave para que, si el usuario reemplaza el archivo por otro con el
    # mismo nombre (por ejemplo un .exe distinto), el icono en caché
    # quede obsoleto automáticamente en vez de mostrar el antiguo.
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        mtime = 0
    raw = f"{path}|{size}|{mtime}".encode("utf-8", errors="ignore")
    return hashlib.md5(raw).hexdigest()


def _load_from_disk_cache(path: str, size: int):
    if Image is None:
        return None
    cache_file = _DISK_CACHE_DIR / f"{_disk_cache_key(path, size)}.png"
    if not cache_file.exists():
        return None
    try:
        with Image.open(cache_file) as cached:
            return cached.convert("RGBA")
    except Exception:
        return None


def _save_to_disk_cache(path: str, size: int, image) -> None:
    if image is None:
        return
    try:
        _DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _DISK_CACHE_DIR / f"{_disk_cache_key(path, size)}.png"
        image.save(cache_file, "PNG")
    except Exception:
        pass


def get_icon_photo(path: str, size: int = 32):
    """Devuelve un ImageTk.PhotoImage para `path`, o None si no se puede.

    Para archivos de imagen (png, jpg...) se usa una miniatura real del
    propio archivo. Para el resto, se pide a Windows el icono de la
    aplicación predeterminada asociada a esa extensión (el mismo que se
    ve en el Explorador). Si nada de eso está disponible (falta Pillow,
    no es Windows, o falla la llamada), devuelve None para que el que
    llama use un icono de repuesto (emoji)."""
    if not path:
        return None

    key = (path, size)
    if key in _icon_cache:
        return _icon_cache[key]

    photo = None
    try:
        image = _load_from_disk_cache(path, size)
        came_from_disk = image is not None
        if image is None:
            image = _extract_image_thumbnail(path, size)
            if image is None and _ICON_SUPPORTED:
                image = _extract_icon_image(path, size)
        if image is not None:
            if not came_from_disk:
                _save_to_disk_cache(path, size, image)
            photo = ImageTk.PhotoImage(image)
    except Exception:
        photo = None

    _icon_cache[key] = photo
    return photo


def clear_cache() -> None:
    """Vacía la caché de iconos en memoria (por ejemplo al recargar la lista)."""
    _icon_cache.clear()


def clear_disk_cache() -> None:
    """Borra también la caché de iconos guardada en disco (los PNG de
    icon_cache/). Con el tiempo, si se cambian muchos accesos, se pueden
    acumular bastantes archivos ahí — esto la deja limpia; los iconos se
    volverán a generar (y guardar) la próxima vez que se necesiten."""
    clear_cache()
    try:
        if _DISK_CACHE_DIR.exists():
            shutil.rmtree(_DISK_CACHE_DIR, ignore_errors=True)
    except Exception:
        pass


def disk_cache_size_bytes() -> int:
    """Tamaño actual de la caché de iconos en disco, en bytes (0 si no
    existe o no se puede leer)."""
    if not _DISK_CACHE_DIR.exists():
        return 0
    total = 0
    try:
        for entry in _DISK_CACHE_DIR.glob("*.png"):
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _extract_image_thumbnail(path: str, size: int):
    """Miniatura real de un archivo de imagen, centrada en un lienzo
    cuadrado con transparencia. Solo necesita Pillow."""
    if Image is None:
        return None

    target = Path(path)
    if target.suffix.lower() not in IMAGE_EXTENSIONS or not target.exists():
        return None

    try:
        with Image.open(target) as source:
            source = source.convert("RGBA")
            source.thumbnail((size, size), Image.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            offset = ((size - source.width) // 2, (size - source.height) // 2)
            canvas.paste(source, offset, source)
            return canvas
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Extracción a bajo nivel usando la API de Windows (Shell / GDI) vía ctypes.
# Sin pywin32: solo shell32.dll, user32.dll y gdi32.dll (siempre presentes
# en cualquier Windows).
# ---------------------------------------------------------------------------


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * MAX_PATH),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


def _sh_get_file_icon(path: Path, use_file_attributes: bool, flags: int):
    """Pide a Windows el icono asociado a `path` (aplicación predeterminada
    de esa extensión, icono de carpeta, icono del propio .exe...)."""
    shell32 = ctypes.windll.shell32
    info = _SHFILEINFOW()

    attrs = FILE_ATTRIBUTE_NORMAL if use_file_attributes else 0
    if use_file_attributes:
        flags |= SHGFI_USEFILEATTRIBUTES

    shell32.SHGetFileInfoW(
        ctypes.c_wchar_p(str(path)),
        attrs,
        ctypes.byref(info),
        ctypes.sizeof(info),
        flags,
    )
    return info.hIcon


def _extract_icon_image(path: str, size: int):
    target = Path(path)
    flags = SHGFI_ICON | (SHGFI_LARGEICON if size > 20 else SHGFI_SMALLICON)

    if target.exists():
        hicon = _sh_get_file_icon(target, use_file_attributes=False, flags=flags)
    else:
        # La ruta ya no existe (acceso roto): pedimos el icono genérico
        # según la extensión, sin necesidad de que el archivo exista.
        hicon = _sh_get_file_icon(target, use_file_attributes=True, flags=flags)

    if not hicon:
        return None

    try:
        return _hicon_to_pil(hicon, size)
    finally:
        ctypes.windll.user32.DestroyIcon(hicon)


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

    hdc_screen = user32.GetDC(None)
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
        user32.ReleaseDC(None, hdc_screen)
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
        user32.ReleaseDC(None, hdc_screen)

    # Los iconos antiguos (sin canal alfa) quedan con alfa=0 en todos los
    # píxeles tras este proceso; los tratamos como totalmente opacos.
    alpha = image.getchannel("A")
    if alpha.getextrema() == (0, 0):
        image.putalpha(255)

    return image
