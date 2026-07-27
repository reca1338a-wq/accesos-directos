@echo off
cd /d "%~dp0"

py -c "import win32gui, PIL, tkinterdnd2" 2>nul
if errorlevel 1 (
    echo Instalando dependencias necesarias ^(solo la primera vez^)...
    py -m pip install -r requirements.txt --quiet --disable-pip-version-check
)

py main.py 2>nul || python main.py
if errorlevel 1 pause
