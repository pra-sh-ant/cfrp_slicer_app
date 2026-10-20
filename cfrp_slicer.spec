# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for CFRP Slicer Pro desktop application.

Build with:
    .venv\Scripts\pyinstaller.exe cfrp_slicer.spec

Output: dist\CFRP_Slicer_Pro\CFRP_Slicer_Pro.exe  (one-folder bundle)
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 — injected by PyInstaller

a = Analysis(
    [str(ROOT / 'gui_app.py')],
    pathex=[str(ROOT / 'src')],
    binaries=[],
    datas=[
        # cfrp_core package with its data files (machine profiles)
        (str(ROOT / 'src' / 'cfrp_core'), 'cfrp_core'),
        # GUI package
        (str(ROOT / 'gui'), 'gui'),
    ],
    hiddenimports=[
        # PySide6 plugins that PyInstaller sometimes misses
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtPrintSupport',
        # pyqtgraph OpenGL backend
        'pyqtgraph.opengl',
        'OpenGL',
        'OpenGL.GL',
        'OpenGL.arrays.numpymodule',
        # shapely / numpy internals
        'shapely',
        'shapely.geometry',
        'numpy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'IPython', 'notebook'],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CFRP_Slicer_Pro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # replace with path to a .ico to add an icon
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CFRP_Slicer_Pro',
)
