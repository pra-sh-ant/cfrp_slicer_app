import sys
from pathlib import Path

# Flat module layout at the project root (app.py, geometry.py, ...) with no
# package/__init__.py there -- make it importable regardless of where pytest
# is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
