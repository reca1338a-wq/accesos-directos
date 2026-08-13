# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

# tkinterdnd2 necesita sus archivos Tcl empaquetados aparte (no son código
# Python), o el arrastrar-y-soltar desde el Explorador no funcionará en el
# .exe aunque funcione al ejecutar desde el código fuente.
datas = [('VERSION', '.')]
try:
    datas += collect_data_files('tkinterdnd2')
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# COLLECT (en vez de meter a.binaries/a.datas en el EXE de arriba) es lo
# que convierte esto en modo "onedir": todo queda reunido de una vez en
# dist/main/, y el .exe lee sus archivos directamente de ahí en cada
# arranque, sin descomprimirse a una carpeta temporal nueva cada vez.
# Esto es justo lo que elimina la condición de carrera que causaba los
# errores "Can't find a usable init.tcl" / "Failed to load Python DLL"
# al reiniciar rápido (cambiar tema, actualizar): ya no hay nada que
# extraer ni que se pueda borrar a medio leer.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='main',
)
