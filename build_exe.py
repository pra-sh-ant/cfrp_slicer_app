r"""Build script for the CFRP Slicer Pro standalone executable.

Usage:
    .venv\Scripts\python.exe build_exe.py [--onefile]

Flags:
    --onefile   Build a single .exe (slower launch but easier to share)
                Default: one-folder bundle in dist\CFRP_Slicer_Pro\

Output is placed in the dist\ directory.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true",
                    help="Produce a single self-extracting .exe")
    args = ap.parse_args()

    pyinstaller = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller.exists():
        sys.exit("PyInstaller not found. Run:\n"
                 "  uv pip install pyinstaller --python .venv/Scripts/python.exe")

    cmd = [str(pyinstaller), "--clean"]
    if args.onefile:
        # Override spec to single-file mode
        cmd += [
            "--onefile", "--windowed",
            "--name", "CFRP_Slicer_Pro",
            "--paths", str(ROOT / "src"),
            "--add-data", f"{ROOT / 'src' / 'cfrp_core'};cfrp_core",
            "--add-data", f"{ROOT / 'gui'};gui",
            "--hidden-import", "PySide6.QtOpenGL",
            "--hidden-import", "PySide6.QtOpenGLWidgets",
            "--hidden-import", "pyqtgraph.opengl",
            "--hidden-import", "OpenGL",
            "--hidden-import", "OpenGL.GL",
            "--hidden-import", "OpenGL.arrays.numpymodule",
            "--hidden-import", "shapely",
            "--exclude-module", "matplotlib",
            "--exclude-module", "pandas",
            str(ROOT / "gui_app.py"),
        ]
    else:
        cmd += [str(ROOT / "cfrp_slicer.spec")]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f"PyInstaller failed (exit {result.returncode})")

    if args.onefile:
        out = ROOT / "dist" / "CFRP_Slicer_Pro.exe"
    else:
        out = ROOT / "dist" / "CFRP_Slicer_Pro" / "CFRP_Slicer_Pro.exe"

    if out.exists():
        size_mb = out.stat().st_size / 1_048_576
        print(f"\nBuild successful: {out}  ({size_mb:.1f} MB)")
    else:
        print("\nBuild finished but output not found at expected path:", out)


if __name__ == "__main__":
    main()
