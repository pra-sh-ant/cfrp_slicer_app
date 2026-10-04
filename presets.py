"""Legacy Streamlit UI defaults (widget initial values). Shape classes come
from the core so the UI and the slicer can never disagree."""
import math

from cfrp_core.config import FLAT_SPECIALTY_SHAPES as Z_FLAT_SPECIALTY_SHAPES
from cfrp_core.config import REVOLVE_SHAPES as Z_REVOLVE_SHAPES
from cfrp_core.config import SHAPES as _SHAPES
from cfrp_core.config import SPECIALTY_SHAPES as Z_SPECIALTY_SHAPES

SHAPES = list(_SHAPES)

__all__ = ["DEFAULT_PARAMS", "SHAPES", "Z_FLAT_SPECIALTY_SHAPES", "Z_REVOLVE_SHAPES", "Z_SPECIALTY_SHAPES"]

DEFAULT_PARAMS = {
    "shape": "Square/Rectangle", "w": 135.0, "h": 25.0, "gap": 1.2, "rad": 3.0,
    "start_x": 26.0, "start_y": 40.0, "z_off": 0.20, "thick": 10.0, "invert": False,
    "base_r": 10.0, "top_r": 15.0, "stem_h": 25.0, "wall_t": 2.5,
    "uv": 225, "vmin": 5.0, "accel": 25.0,
    "decel": 15.0, "p_adv": 3.0, "vmax": 10.0, "layers": 1, "layup": "0", "anchor": 15.0,
    "tracks_per_layer": 10, "infill_type": "Archimedean (Concentric Spiral)",
    "include_boundary": False,
    # Materials -- previously hardcoded straight into the widgets, now part
    # of the saveable parameter set.
    "needle_dia": 0.8, "layer_height": 0.2, "e_mult": 1.6, "density": 0.00115,
    "weight": 0.55 / (math.pi * 39),
    # Extra kinematics/process knobs, same reason.
    "transition_angle": 30.0, "seam_trim": 3.0, "v_z": 2.0, "v_overhang": 5.0,
    "dwell": 1.5, "pump_stall_floor": 5.0, "purge_e": 15.0, "purge_f": 60.0,
    "travel_f": 600.0,
}

