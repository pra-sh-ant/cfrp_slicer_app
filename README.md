# CFRP Slicer Pro

Slicer for continuous carbon-fibre printing with a UV-cured resin, plus direct
ink writing (DIW) of insulation and conductive ink for structural electronics.
Every fibre part is one unbroken stroke that never bends tighter than the tow's
minimum bend radius.

## Run

Double-click `run.bat`. The first run creates `.venv` (Python 3.12, with `uv`
if installed, otherwise `py -3.12`) and installs `requirements.txt`. After that:

```
.venv\Scripts\python.exe gui_app.py
```

## Develop

```
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe tools\validation_sweep.py --quick
.venv\Scripts\python.exe tools\multilayer_audit.py --quick
.venv\Scripts\python.exe tools\pcb_audit.py 12
```

Build a Windows executable: install `pyinstaller` into `.venv`, then
`.venv\Scripts\python.exe build_exe.py` (add `--onefile` for a single file).

## Layout

| Folder | What |
|---|---|
| `src/cfrp_core/` | Slicing library, no UI code: config, path generators, layers, speed planner, G-code, checks, DIW electronics |
| `gui/` | PySide6 desktop app (settings panel, 3D viewer, structural PCB editor) |
| `tests/` | pytest + hypothesis; `tests/golden` holds reference outputs |
| `tools/` | Audits (also run in CI) and the calibration G-code writer |
| `docs/` | How it works (`DEEP_DIVE.md`, `ARCHITECTURE.md`, `SPEC_current.md`), the PCB editor guide, the upgrade plan |

Rules for changing the code are in `CLAUDE.md`; the history is in `CHANGELOG.md`.
