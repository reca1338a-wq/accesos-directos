@echo off
cd /d "%~dp0"
py main.py 2>nul || python main.py
if errorlevel 1 pause
