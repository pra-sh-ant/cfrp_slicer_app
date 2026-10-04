"""Reference configurations for the characterization (golden) tests.

Each case is the exact flat `sys_config` dict the legacy Streamlit sidebar
produced (before `resolve_process_constraints`), plus the layup sequence.
They pin the slicer's numerical behaviour so refactors can prove they change
nothing: see tests/test_golden.py and tests/golden/regenerate.py.

Never edit a case in place. If a case must change, add a new one and
regenerate deliberately (and say so in CHANGELOG.md).
"""
import math

# Materials as the sidebar computed them from presets.DEFAULT_PARAMS:
# e_const = needle_dia * layer_height * e_mult * density / weight
_NEEDLE, _LAYER_H, _E_MULT, _DENSITY = 0.8, 0.2, 1.6, 0.00115
_WEIGHT = 0.55 / (math.pi * 39)


def _e_const(layer_height):
    return (_NEEDLE * layer_height * _E_MULT * _DENSITY) / _WEIGHT


def _flat(shape, infill, **overrides):
    """A 2D laminate config, keyed exactly like the legacy sidebar output."""
    layer_height = overrides.pop("layer_height", _LAYER_H)
    cfg = {
        # geometry
        "shape": shape, "width": 60.0, "height": 30.0, "thickness": 0.0,
        "invert_bends": False, "base_radius": 0.0, "disc_radius": 0.0,
        "stem_height": 0.0, "wall_thickness": 0.0, "include_boundary": False,
        # layering
        "layers": 2, "layup_seq": "0, 90", "infill_type": infill, "tracks_per_layer": 4,
        # placement
        "start_x": 26.0, "start_y": 40.0, "track_gap": 1.2, "z_offset": 0.2,
        # materials
        "layer_height": layer_height, "e_const": _e_const(layer_height),
        "needle_dia": _NEEDLE, "e_mult": _E_MULT, "density": _DENSITY, "weight": _WEIGHT,
        # kinematics
        "v_max": 10.0, "uv": 225, "min_radius": 3.0, "transition_angle": 30.0,
        "seam_trim_length": 3.0, "v_z": 0, "v_overhang": 0, "dwell": 0,
        "v_min": 5.0, "pump_stall_floor": 5.0, "decel": 15.0, "accel": 25.0,
        "p_advance": 3.0, "anchor_length": 15.0,
        "purge_e": 15.0, "purge_f": 60.0, "travel_f": 600.0,
    }
    cfg.update(overrides)
    return cfg


def _revolve(shape, **overrides):
    layer_height = overrides.pop("layer_height", 0.5)
    cfg = _flat(shape, "Archimedean (Concentric Spiral)", layer_height=layer_height)
    cfg.update({
        "width": 0, "height": 0, "thickness": 0, "base_radius": 15.0, "stem_height": 10.0,
        "layers": 1, "layup_seq": "0", "tracks_per_layer": 3,
    })
    cfg.update(overrides)
    return cfg


def _specialty(shape, **overrides):
    cfg = _flat(shape, "N/A")
    cfg.update({
        "width": 0, "height": 0, "thickness": 0, "layers": 1, "layup_seq": "0",
        "tracks_per_layer": 1, "v_z": 2.0, "v_overhang": 0.0, "dwell": 0.0,
        "v_min": 0, "pump_stall_floor": 0, "decel": 0, "accel": 0, "p_advance": 0,
        "anchor_length": 0, "transition_angle": 0.0, "seam_trim_length": 0.0,
    })
    cfg.update(overrides)
    return cfg


ARCH = "Archimedean (Concentric Spiral)"
FERMAT = "Fermat Spiral (Connected Double Spiral)"
SERP = "Oriented Serpentine"

_SHAPES_2D = {
    "rectangle": ("Square/Rectangle", {}),
    "triangle": ("Triangle", {"width": 60.0, "height": 50.0}),
    "hexagon": ("Hexagon", {"width": 50.0, "height": 50.0}),
    "circle": ("Circle", {"width": 40.0, "height": 40.0}),
    "lbracket": ("L-Bracket", {"width": 60.0, "height": 60.0, "thickness": 15.0}),
}
_PATTERNS = {"arch": ARCH, "fermat": FERMAT, "serp": SERP}

CASES = []
for _key, (_shape, _geo) in _SHAPES_2D.items():
    for _pkey, _pattern in _PATTERNS.items():
        CASES.append({
            "name": f"{_key}_{_pkey}",
            "config": _flat(_shape, _pattern, **_geo),
            "layup": [0.0, 90.0],
        })

CASES += [
    {"name": "layup_0_90_45_m45_serp",
     "config": _flat("Square/Rectangle", SERP, layers=4, layup_seq="0, 90, 45, -45"),
     "layup": [0.0, 90.0, 45.0, -45.0]},
    {"name": "rectangle_arch_boundary",
     "config": _flat("Square/Rectangle", ARCH, include_boundary=True, layers=3),
     "layup": [0.0]},
    {"name": "lbracket_serp_boundary",
     "config": _flat("L-Bracket", SERP, width=60.0, height=60.0, thickness=15.0,
                     include_boundary=True, layers=3),
     "layup": [0.0, 90.0]},
    {"name": "serpentine_infill_shape",
     "config": _flat("Serpentine Infill", ARCH, width=60.0, height=30.0, layers=2),
     "layup": [0.0, 90.0]},
    {"name": "serpentine_infill_inverted",
     "config": _flat("Serpentine Infill", ARCH, width=60.0, height=30.0, invert_bends=True),
     "layup": [0.0]},
    {"name": "sphere_arch", "config": _revolve("Hollow Sphere (Z)", base_radius=10.0), "layup": [0.0]},
    {"name": "dome_arch", "config": _revolve("Dome (Z)"), "layup": [0.0]},
    {"name": "taper_arch", "config": _revolve("Tapered Cylinder (Z)"), "layup": [0.0]},
    {"name": "square_tube_arch", "config": _revolve("Hollow Square Tube (Z)"), "layup": [0.0]},
    {"name": "cylinder_fermat", "config": _revolve("Hollow Cylinder (Z)", infill_type=FERMAT), "layup": [0.0]},
    {"name": "mushroom", "config": _specialty(
        "3D Overhang (Mushroom)", base_radius=10.0, disc_radius=15.0, stem_height=25.0,
        v_overhang=5.0, dwell=1.5), "layup": [0.0]},
    {"name": "torus", "config": _specialty(
        "Torus (Z)", base_radius=20.0, wall_thickness=2.5, stem_height=10.0), "layup": [0.0]},
    {"name": "conical_dome", "config": _specialty(
        "Conical Dome (Z)", base_radius=20.0, stem_height=25.0), "layup": [0.0]},
]

CASE_NAMES = [c["name"] for c in CASES]
