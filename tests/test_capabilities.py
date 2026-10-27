"""Option states (cfrp_core.capabilities) checked against the slicer: every
setting reported as not applying must leave the sliced path unchanged, and
every pattern reported as unavailable must slice like the one used instead."""
import math

import numpy as np
import pytest

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.capabilities import (
    ARCHIMEDEAN,
    CORNER_STEP,
    DIAGONAL_45,
    FERMAT,
    INFILL_PATTERNS,
    ORIENTED_SERPENTINE,
    field_states,
    first_enabled,
    infill_states,
)

BASE = {
    "shape": "Square/Rectangle", "width": 60.0, "height": 25.0, "include_boundary": False,
    "stl_path": None, "base_radius": 14.0, "disc_radius": 12.0, "stem_height": 6.0,
    "start_x": 40.0, "start_y": 40.0, "track_gap": 1.2, "z_offset": 0.2,
    "layers": 1, "layup_seq": "0", "infill_type": ARCHIMEDEAN, "tracks_per_layer": 3,
    "layer_height": 0.5, "needle_dia": 0.8, "e_mult": 1.6, "density": 0.00115,
    "weight": 0.55 / (math.pi * 39), "v_max": 10.0, "v_min": 5.0, "accel": 25.0, "decel": 15.0,
    "p_advance": 3.0, "pump_stall_floor": 5.0, "transition_angle": 30.0, "seam_trim_length": 3.0,
    "anchor_length": 15.0, "min_radius": 3.0, "v_z": 2.0, "v_overhang": 5.0, "dwell": 1.5,
    "uv": 225, "purge_e": 15.0, "purge_f": 60.0, "travel_f": 600.0,
}

# A change for every setting a front end can grey out.
PERTURB = {
    "width": lambda p: p["width"] + 6.0,
    "height": lambda p: p["height"] + 6.0,
    "include_boundary": lambda p: not p["include_boundary"],
    "stl_scale": lambda p: 1.1,
    "stl_islands": lambda p: "all",
    "base_radius": lambda p: p["base_radius"] + 2.0,
    "stem_height": lambda p: p["stem_height"] + 2.0,
    "disc_radius": lambda p: p["disc_radius"] + 2.0,
    "layers": lambda p: p["layers"] + 1,
    "tracks_per_layer": lambda p: p["tracks_per_layer"] + 1,
    "fill_solid": lambda p: not p.get("fill_solid", False),
    "layup_seq": lambda p: "30, -30",
    "start_x": lambda p: p["start_x"] + 5.0,
    "start_y": lambda p: p["start_y"] + 5.0,
    "v_min": lambda p: p["v_min"] - 1.0,
    "accel": lambda p: p["accel"] * 2.0,
    "decel": lambda p: p["decel"] * 2.0,
    "p_advance": lambda p: p["p_advance"] + 2.0,
    "pump_stall_floor": lambda p: p["pump_stall_floor"] + 1.0,
    "anchor_length": lambda p: p["anchor_length"] + 5.0,
    "pa_table": lambda p: "5:1.0, 10:4.0",
    "transition_angle": lambda p: 45.0,
    "seam_trim_length": lambda p: 6.0,
    "seam_stagger": lambda p: 40.0,
    "v_z": lambda p: p["v_z"] + 1.0,
    "v_overhang": lambda p: p["v_overhang"] + 1.0,
    "dwell": lambda p: p["dwell"] + 1.0,
    "first_layer_solid": lambda p: not p.get("first_layer_solid", False),
    "first_layer_infill": lambda p: "Corner-Step Spiral",
    "first_layer_min_coverage": lambda p: 0.5,
    "solid_bottom_layers": lambda p: 2,
    "solid_top_layers": lambda p: 2,
    "zigzag_respect_radius": lambda p: not p.get("zigzag_respect_radius", False),
    "fill_centre": lambda p: not p.get("fill_centre", False),
    "strict_bend_radius": lambda p: not p.get("strict_bend_radius", False),
    "fill_pockets": lambda p: not p.get("fill_pockets", False),
    "edge_rings": lambda p: 2,
    "wrap_holes": lambda p: not p.get("wrap_holes", False),
    "hole_rings": lambda p: 3,
    "bolt_holes": lambda p: "1",
    "drill_holes": lambda p: "1",
    "height_ranges": lambda p: "0-1: fermat, solid, layup 45/-45",
    "load_angle": lambda p: 30.0,
    "overhang_span": lambda p: 3.0,
    "uv_irradiance": lambda p: 600.0,
    "uv_spot": lambda p: 3.0,
    "stl_closing_radius": lambda p: 0.3,
    "stl_stitch": lambda p: 1.0,
    "blade_chord": lambda p: 60.0,
    "blade_tip_chord": lambda p: 30.0,
    "blade_thickness": lambda p: 25.0,
    "blade_twist": lambda p: -8.0,
    "blade_pitch": lambda p: 9.0,
    "blade_root_shape": lambda p: "Square" if p.get("blade_root_shape", "Rectangle") != "Square" else "Round",
    "blade_root_length": lambda p: 20.0,
    "blade_root_width": lambda p: 44.0,
    "blade_root_thickness": lambda p: 24.0,
    "hub_blades": lambda p: 4,
    "hub_shaft": lambda p: 10.0,
    "hub_screw": lambda p: 5,
    "hub_thickness": lambda p: 3.0,
    "brim_rings": lambda p: 3,
    "adaptive_layers": lambda p: not p.get("adaptive_layers", False),
    "layer_height_step": lambda p: 8.0,
    "layer_height_limit": lambda p: 0.9,
    "clean_every_m": lambda p: 0.5,
    "clean_every_min": lambda p: 1.0,
    "clean_pause_s": lambda p: 20.0,
}


def _box_stl(path, w=40.0, d=30.0, h=2.0):
    v = np.array([[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0],
                  [0, 0, h], [w, 0, h], [w, d, h], [0, d, h]], dtype=float)
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "w") as f:
        f.write("solid box\n")
        for a, b, c in faces:
            f.write("facet normal 0 0 0\nouter loop\n")
            for k in (a, b, c):
                f.write(f"vertex {v[k][0]} {v[k][1]} {v[k][2]}\n")
            f.write("endloop\nendfacet\n")
        f.write("endsolid box\n")
    return str(path)


def _path(p):
    """The sliced path, rounded (G-code headers echo settings, so compare
    the motion itself)."""
    r = slice_part(from_legacy_dict(p))
    return [(round(q["x"], 6), round(q["y"], 6), round(q["z"], 6), round(q["v"], 6),
             round(q.get("e_mult", 1.0), 6)) for q in r.trajectory]


SETUPS = {
    "rect_arch_1layer": {},
    "rect_corner_2layers": {"infill_type": CORNER_STEP, "layers": 2},
    "rect_serp_2layers": {"infill_type": ORIENTED_SERPENTINE, "layers": 2, "layup_seq": "0, 90"},
    "rect_hatch_2layers": {"infill_type": DIAGONAL_45, "layers": 2},
    "rect_fermat_fill": {"infill_type": FERMAT, "fill_solid": True},
    "rect_auto_2layers": {"infill_type": "Auto (Best Fit)", "layers": 2, "layup_seq": "0, 90"},
    "rect_serp_edge_rings": {"infill_type": ORIENTED_SERPENTINE, "layers": 2, "layup_seq": "0, 90",
                             "edge_rings": 1, "fill_solid": True},
    "circle_arch": {"shape": "Circle", "width": 40.0, "height": 40.0},
    "rect_pa_table": {"pa_table": "5:2.0, 10:3.0"},
    "cylinder": {"shape": "Hollow Cylinder (Z)", "tracks_per_layer": 2},
    "square_tube": {"shape": "Hollow Square Tube (Z)", "tracks_per_layer": 2},
    "sphere": {"shape": "Hollow Sphere (Z)", "tracks_per_layer": 2, "base_radius": 12.0},
    "blade": {"shape": "Wind Turbine Blade (Z)", "tracks_per_layer": 2, "stem_height": 10.0,
              "blade_chord": 50.0, "blade_tip_chord": 25.0, "blade_root_length": 5.0},
    "blade_round": {"shape": "Wind Turbine Blade (Z)", "tracks_per_layer": 2, "stem_height": 10.0,
                    "blade_chord": 50.0, "blade_tip_chord": 25.0, "blade_root_length": 5.0,
                    "blade_root_shape": "Round"},
    "hub": {"shape": "Wind Turbine Hub", "hub_thickness": 1.0},
    "hub_square": {"shape": "Wind Turbine Hub", "hub_thickness": 1.0, "blade_root_shape": "Square",
                   "blade_root_width": 30.0},
    "mushroom": {"shape": "3D Overhang (Mushroom)", "infill_type": "N/A", "stem_height": 8.0},
    "stl_box": {"shape": "STL Import"},
}


@pytest.mark.parametrize("name", sorted(SETUPS))
def test_disabled_settings_do_not_change_the_path(name, tmp_path):
    p = dict(BASE, **SETUPS[name])
    if p["shape"] == "STL Import":
        p["stl_path"] = _box_stl(tmp_path / "box.stl")
    base = _path(p)
    disabled = [k for k, st in field_states(p).items() if not st.enabled and k in PERTURB]
    assert disabled, "every setup has something greyed out"
    for key in disabled:
        if key == "stem_height" and p["shape"] == "3D Overhang (Mushroom)":
            continue    # the GUI's Z-shell Height field; the Mushroom's stem top is "stem_top"
        q = dict(p)
        q[key] = PERTURB[key](p)
        got = _path(q)
        if p["shape"] == "Circle" and key == "height":
            # Legacy placement quirk: the circle is centred at y = Height/2,
            # so Height only moves it (sized by Width alone).
            a, b = np.array(got), np.array(base)
            assert a.shape == b.shape
            a[:, 1] -= a[0, 1] - b[0, 1]
            # (offsetting at other coordinates differs in the last digits)
            assert np.allclose(a, b, atol=1e-2), f"{name}: Height changed more than the position"
            continue
        assert got == base, f"{name}: {key} is greyed out but changed the path"


def test_enabled_settings_are_live():
    """The reverse spot-check: settings reported as applying do change it."""
    p = dict(BASE, infill_type=ORIENTED_SERPENTINE, layers=2, layup_seq="0, 90")
    st = field_states(p)
    for key in ("layup_seq", "seam_trim_length", "start_x", "width"):
        assert st[key].enabled
        q = dict(p)
        q[key] = PERTURB[key](p)
        assert _path(q) != _path(p), key


@pytest.mark.parametrize("shape", ["Hollow Cylinder (Z)", "Hollow Square Tube (Z)", "Wind Turbine Blade (Z)"])
def test_unavailable_revolve_patterns_slice_as_archimedean(shape):
    p = dict(BASE, shape=shape, tracks_per_layer=2)
    states = infill_states(p)
    assert states[ARCHIMEDEAN].enabled
    ref = _path(dict(p, infill_type=ARCHIMEDEAN))
    for name, st in states.items():
        if not st.enabled:
            assert _path(dict(p, infill_type=name)) == ref, name


def test_fermat_on_shells_only_available_when_the_gap_allows_its_u_turn():
    p = dict(BASE, shape="Hollow Cylinder (Z)")
    assert not infill_states(p)[FERMAT].enabled          # 1.2 mm gap < 2 x 3 mm
    assert infill_states(dict(p, track_gap=6.5))[FERMAT].enabled


def test_flat_u_turn_patterns_carry_a_caution_not_a_block():
    st = infill_states(BASE)
    assert all(s.enabled for s in st.values())
    assert st[ORIENTED_SERPENTINE].caution and st[FERMAT].caution
    assert not st[ARCHIMEDEAN].caution
    assert not infill_states(dict(BASE, track_gap=6.0, min_radius=3.0))[FERMAT].caution


def test_mushroom_has_no_pattern_choice():
    st = infill_states(dict(BASE, shape="3D Overhang (Mushroom)"))
    assert set(st) == set(INFILL_PATTERNS)
    assert not any(s.enabled for s in st.values())
    assert all(s.reason for s in st.values())


def test_reasons_given_for_everything_disabled():
    for setup in SETUPS.values():
        p = dict(BASE, **setup)
        for key, st in {**field_states(p), **infill_states(p)}.items():
            assert st.enabled or st.reason, key


def test_first_enabled_keeps_a_valid_choice():
    st = infill_states(dict(BASE, shape="Hollow Cylinder (Z)"))
    assert first_enabled(st, ORIENTED_SERPENTINE) == ARCHIMEDEAN
    assert first_enabled(st, ARCHIMEDEAN) == ARCHIMEDEAN
