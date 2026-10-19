import sys
from pathlib import Path

# The Streamlit app imports its siblings (presets, ui, viewer) and cfrp_core.
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))
