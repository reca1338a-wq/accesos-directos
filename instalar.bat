@echo off
REM Instalador de Accesos Directos: no necesita Python ni permisos de administrador.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
