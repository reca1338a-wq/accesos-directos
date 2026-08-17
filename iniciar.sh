#!/bin/bash
# Lanzador para Linux/Mac, equivalente a iniciar.bat en Windows.
cd "$(dirname "$0")"

PYTHON=python3
if [ -x "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
fi

if ! "$PYTHON" -c "import PySide6, PIL" 2>/dev/null; then
    echo "Instalando dependencias que faltan..."
    "$PYTHON" -m pip install -r requirements.txt --quiet
fi

"$PYTHON" main.py
