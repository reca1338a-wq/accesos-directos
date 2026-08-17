@echo off
cd /d "%~dp0"

REM Comprueba que las dependencias (PySide6, Pillow...) están instaladas
REM antes de arrancar; si falta alguna, la instala sola. Esto es justo lo
REM que evita el error "ModuleNotFoundError: No module named 'PySide6'"
REM al lanzar la app en un PC donde solo se ha copiado el código.
py -c "import PySide6, PIL" 2>nul
if errorlevel 1 (
    echo Instalando dependencias que faltan...
    py -m pip install -r requirements.txt --quiet || python -m pip install -r requirements.txt --quiet
)

py main.py 2>nul || python main.py
if errorlevel 1 pause
