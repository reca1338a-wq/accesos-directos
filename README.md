# Accesos Directos

App de escritorio (Tkinter) para organizar accesos directos a archivos y
carpetas en tarjetas, con carpetas internas, temas de color, arrastrar y
soltar, y auto-actualización desde GitHub Releases.

## Archivos

- `main.py` — la aplicación (interfaz, tarjetas, carpetas, drag&drop, configuración).
- `app_config.py` — rutas, ajustes, y el modelo de datos de accesos/carpetas.
- `github_updates.py` — comprobación e instalación de actualizaciones desde GitHub.
- `win_icons.py` — iconos reales de Windows (vía `ctypes`, sin `pywin32`) y miniaturas de imagen (vía Pillow). No hace nada en Linux/Mac, la app sigue funcionando con emojis de repuesto.
- `main.spec` — receta de PyInstaller para compilar el `.exe` de Windows.
- `requirements.txt` — dependencias opcionales (Pillow, tkinterdnd2).
- `iniciar.bat` / `iniciar.sh` — lanzadores para Windows / Linux-Mac.
- `VERSION` — número de versión actual, léelo/edítalo antes de publicar un Release.

## Desarrollo en Linux (tu situación actual)

Puedes seguir programando y probando la app en Linux Mint con normalidad:
tkinter funciona igual, y `win_icons.py` se desactiva solo (usa `ctypes.windll`,
que es específico de Windows) sin romper nada — verás emojis en vez de
iconos reales mientras pruebas aquí, y volverán al compilar en Windows.

```
pip install -r requirements.txt   # opcional, para probar iconos/drag&drop
python3 main.py
```

## Compilar el `.exe` de Windows

`main.spec` genera un `.exe` de Windows con PyInstaller — esto **necesita
ejecutarse en Windows** (o con Wine). Como ya no tienes una VM de Windows a
mano, la opción más cómoda ahora es dejar que GitHub Actions lo compile en
la nube automáticamente cada vez que publiques una versión, sin necesitar
Windows en tu equipo. Si quieres, puedo montarte ese flujo.

## Publicar una actualización

1. Sube el número de versión en `VERSION` (ej. `1.3.0`).
2. Compila el `.exe`.
3. Crea un Release en GitHub con un tag igual a esa versión y sube el `.exe` como adjunto.

La app detecta automáticamente la versión nueva (cada 15 min o al pulsar
el icono 🔄) y se actualiza sola.
