"""cfrp_core must import and slice with no UI libraries available."""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

SCRIPT = r"""
import importlib, pkgutil, sys

class _BlockUI:
    BLOCKED = ("streamlit", "plotly", "PySide6", "pyvista", "pyvistaqt", "vtk")
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BLOCKED:
            raise ImportError(f"UI library {name!r} imported by the core")
        return None

sys.meta_path.insert(0, _BlockUI())

import cfrp_core
for mod in pkgutil.walk_packages(cfrp_core.__path__, "cfrp_core."):
    importlib.import_module(mod.name)

from cfrp_core import ProcessConfig, slice_part
# tracks_per_layer=4: the legacy defaults (10 tracks on a 25 mm-wide part)
# don't fit -- see docs/ARCHITECTURE.md, known issues.
result = slice_part(ProcessConfig(tracks_per_layer=4))
assert result.trajectory and result.gcode.startswith(";")
print("OK", len(result.trajectory))
"""


def test_core_imports_and_slices_without_ui_libraries():
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True, text=True, timeout=300,
        env={"PYTHONPATH": str(SRC), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK")
