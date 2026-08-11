#!/bin/bash
# Lanzador para Linux/Mac, equivalente a iniciar.bat en Windows.
cd "$(dirname "$0")"

if [ -x "venv/bin/python" ]; then
    venv/bin/python main.py
else
    python3 main.py
fi
