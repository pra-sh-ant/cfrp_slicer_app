@echo off
cd /d "%~dp0"

:: Auto-install dependencies if missing
".venv312\Scripts\python.exe" -c "import trimesh" 2>nul
if errorlevel 1 (
    echo Installing trimesh...
    ".venv312\Scripts\python.exe" -m pip install trimesh >nul 2>&1
)

".venv312\Scripts\python.exe" -c "import pyclipper" 2>nul
if errorlevel 1 (
    echo Installing pyclipper...
    ".venv312\Scripts\python.exe" -m pip install pyclipper >nul 2>&1
)

".venv312\Scripts\python.exe" -c "from stl import mesh" 2>nul
if errorlevel 1 (
    echo Installing numpy-stl...
    ".venv312\Scripts\python.exe" -m pip install numpy-stl >nul 2>&1
)

".venv312\Scripts\python.exe" gui_app.py
