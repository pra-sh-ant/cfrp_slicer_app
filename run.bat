@echo off
rem CFRP Slicer Pro: starts the desktop app. The first run creates .venv
rem (Python 3.12) and installs requirements.txt.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run
echo First run: creating .venv and installing the requirements...
where uv >nul 2>&1
if errorlevel 1 goto pip
uv venv .venv --python 3.12 || goto fail
uv pip install --python .venv\Scripts\python.exe -r requirements.txt || goto fail
goto run

:pip
py -3.12 -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail

:run
".venv\Scripts\python.exe" gui_app.py
exit /b %errorlevel%

:fail
echo Setup failed. Install Python 3.12 (or uv) and run this again.
pause
exit /b 1
