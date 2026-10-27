"""The Wind Turbine Blade (Z) shape -- a straight root (rectangle or round)
for the hub to clamp, then airfoil sections set at pitch + twist, tapering to
the tip, laid as one helical stroke -- and hold points: stops on the stroke
(a dwell, or a pause to cut the fibre)."""
import math
import re

import numpy as np
import pytest
import shapely

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.config import BLADE
from cfrp_core.geometry.blade import (
    SHOULDER,
    blade_cross_section,
    blade_section,
    blade_trailing_edge,
    root_corner,
)
from cfrp_core.legacy import parse_hold_points
from cfrp_core.trajectory import check_trajectory
from tests.helpers import base_2d_config, base_revolve_config

CX, CY = 90.0, 110.0
TE = blade_trailing_edge(2.0, 1.2)


def _blade(**over):
    cfg = dict(base_radius=12.0, stem_height=24.0, layer_height=1.0, tracks_per_layer=3, min_radius=2.0,
               blade_chord=50.0, blade_tip_chord=24.0, blade_thickness=21.0, blade_twist=12.0, blade_pitch=4.0,
               blade_root_length=10.0, blade_root_width=36.0, blade_root_thickness=16.0)
    cfg.update(over)
    return base_revolve_config(BLADE, **cfg)


def _geom(**over):
    return from_legacy_dict(_blade(**over)).geometry


def _slice(cfg):
    return slice_part(from_legacy_dict(cfg))


def _chord_deg(poly):
    """Direction of a section's chord line, from the nose (the middle point:
    the outline runs trailing edge, upper surface, nose, lower surface) to
    the middle of the blunt trailing edge (the first and last points)."""
    pts = np.asarray(poly.exterior.coords)[:-1]
    d = 0.5 * (pts[0] + pts[-1]) - pts[len(pts) // 2]
    return math.degrees(math.atan2(d[1], d[0]))


# --- the shape ---------------------------------------------------------------

def test_blade_root_is_a_straight_rectangle_square_to_the_hub():
    g = _geom()
    corner = root_corner(g, 1.2, 2.0)
    bottom, top = blade_section(g, 0.0, TE, corner), blade_section(g, 9.9, TE, corner)
    assert bottom.symmetric_difference(top).area < 1e-6               # straight up to the blade
    minx, miny, maxx, maxy = bottom.bounds
    assert (maxx - minx, maxy - miny) == pytest.approx((36.0, 16.0), abs=0.05)   # not turned by the pitch
    assert bottom.area == pytest.approx(36.0 * 16.0 - (4.0 - math.pi) * corner ** 2, rel=0.01)
    assert bottom.centroid.distance(shapely.Point(CX, CY)) < 0.05
    assert corner >= 1.05 * 2.0 + 0.6 - 1e-9                          # the outer ring keeps its bend


def test_square_root_is_root_width_on_every_side():
    g = _geom(blade_root_shape="Square", blade_root_width=30.0)
    corner = root_corner(g, 1.2, 2.0)
    root = blade_section(g, 0.0, TE, corner)
    minx, miny, maxx, maxy = root.bounds
    assert (maxx - minx, maxy - miny) == pytest.approx((30.0, 30.0), abs=0.05)
    assert root.area == pytest.approx(30.0 * 30.0 - (4.0 - math.pi) * corner ** 2, rel=0.01)
    r = _slice(_blade(blade_root_shape="Square", blade_root_width=30.0, blade_root_thickness=5.0))
    assert r.validation["status"] == "Pass", r.validation["messages"]       # (its thickness is not used)
    assert any("a 30 mm square, 10 mm long" in n for n in r.info)


def test_round_root_is_a_circle():
    g = _geom(blade_root_shape="Round", base_radius=15.0)
    root = blade_section(g, 0.0, TE, root_corner(g, 1.2, 2.0))
    assert root.area == pytest.approx(math.pi * 15.0 ** 2, rel=0.01)


def test_blade_airfoil_is_set_at_pitch_plus_twist_and_tapers():
    g = _geom(blade_chord=80.0, blade_tip_chord=30.0, stem_height=100.0)
    corner = root_corner(g, 1.2, 2.0)
    shoulder = blade_section(g, 10.0 + SHOULDER * 100.0, TE, corner)
    tip = blade_section(g, 110.0, TE, corner)
    for poly, chord, angle in ((shoulder, 80.0, 16.0), (tip, 30.0, 4.0)):
        assert poly.is_valid and poly.exterior.is_ccw
        assert poly.contains(shapely.Point(CX, CY))                 # turned about the pitch axis
        assert _chord_deg(poly) == pytest.approx(angle, abs=0.5)
        span = np.asarray(poly.exterior.coords) @ (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
        assert span.max() - span.min() == pytest.approx(chord, rel=0.02)
    te = np.asarray(tip.exterior.coords)[:-1]                         # the trailing edge is cut blunt
    assert np.linalg.norm(te[0] - te[-1]) == pytest.approx(TE, rel=0.01)


def test_blade_hollow_keeps_the_wall_or_closes():
    g = _geom()
    hollow = blade_cross_section(g, 0.0, TE, wall=3.6, min_radius=2.0, gap=1.2)
    assert len(hollow.interiors) == 1
    assert hollow.area == pytest.approx(36.0 * 16.0 - (36.0 - 7.2) * (16.0 - 7.2), rel=0.04)
    solid = blade_cross_section(g, 34.0, TE, wall=20.0, min_radius=2.0, gap=1.2)
    assert not len(solid.interiors)


def test_blade_is_one_stroke_from_root_to_tip():
    r = _slice(_blade())
    tr = r.trajectory
    assert r.validation["status"] == "Pass", r.validation["messages"]
    assert not check_trajectory(tr)
    xyz = np.array([(p["x"], p["y"], p["z"]) for p in tr])
    assert np.diff(xyz[:, 2]).min() > -1e-9                      # Z never comes down
    assert np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1).max() < 1.2   # no jump: one tow
    layers = sorted({p["layer"] for p in tr})
    assert layers == list(range(1, 36))                           # every layer, root to tip
    assert xyz[-1, 2] == pytest.approx(r.max_z)


def test_blade_fibre_stays_on_the_blade():
    r = _slice(_blade(blade_twist=-20.0))
    shapes = {layer: poly for _z, poly, layer in r.target_shapes}
    gap = 1.2
    for p in r.trajectory:
        if p.get("is_anchor"):
            continue
        zone = shapely.union_all([shapes[k] for k in (p["layer"], p["layer"] + 1) if k in shapes])
        assert zone.buffer(gap).contains(shapely.Point(p["x"], p["y"])), p


@pytest.mark.parametrize("tracks,r_min", [(1, 3.0), (7, 1.0)])
def test_blade_hollow_and_solid_walls_keep_the_bend_radius(tracks, r_min):
    # (a 48 mm blade: on a 24 mm one at 1 mm layers the root transition
    # changes so much per layer that consecutive inner rings cross)
    r = _slice(_blade(tracks_per_layer=tracks, min_radius=r_min, stem_height=48.0))
    assert r.validation["status"] == "Pass", r.validation["messages"]


def test_blade_notes_name_the_root_the_blunt_edge_and_a_steep_root():
    r = _slice(_blade(blade_chord=80.0))
    assert any("36 x 16 mm rectangle, 10 mm long" in n and "34 mm printed" in n for n in r.info)
    assert any(f"{TE:.1f} mm thick" in n for n in r.info)
    assert any("degrees from upright" in n for n in r.info)
    assert r.max_z == pytest.approx(0.2 + 10.0 + 24.0)


def test_blade_root_must_fit_a_ring():
    with pytest.raises(ValueError, match="too small for one"):
        _slice(_blade(blade_root_shape="Round", base_radius=3.0))
    with pytest.raises(ConfigError, match="root is too thin"):
        from_legacy_dict(_blade(blade_root_thickness=5.0))


def test_blade_settings_are_checked():
    with pytest.raises(ConfigError, match="Airfoil Thickness"):
        from_legacy_dict(_blade(blade_thickness=80.0))
    with pytest.raises(ConfigError, match="Twist"):
        from_legacy_dict(_blade(blade_twist=120.0))
    with pytest.raises(ConfigError, match="Tip Chord"):
        from_legacy_dict(_blade(blade_tip_chord=0.0))
    with pytest.raises(ConfigError, match="Pitch"):
        from_legacy_dict(_blade(blade_pitch=95.0))


def test_fermat_on_a_blade_lays_its_rings():
    fermat = _slice(_blade(infill_type="Fermat Spiral (Connected Double Spiral)", track_gap=4.5))
    rings = _slice(_blade(track_gap=4.5))
    assert [(p["x"], p["y"], p["z"]) for p in fermat.trajectory] == [(p["x"], p["y"], p["z"]) for p in rings.trajectory]
    assert any("Fermat spiral does not apply" in n for n in fermat.constraint_notes)


# --- hold points ---------------------------------------------------------------

def test_parse_hold_points():
    assert parse_hold_points("40: 20 s; 80: 30s, cut; 120: cut") == (
        {"at_mm": 40.0, "seconds": 20.0}, {"at_mm": 80.0, "seconds": 30.0, "cut": True},
        {"at_mm": 120.0, "cut": True})
    assert parse_hold_points("") == ()
    for bad in ("x: 5", "40: soon"):
        with pytest.raises(ValueError):
            parse_hold_points(bad)
    with pytest.raises(ConfigError, match="hold time"):
        from_legacy_dict(base_2d_config(hold_points="40"))       # neither a time nor a cut


def test_holds_dwell_and_pause_in_the_gcode():
    base = base_2d_config(layers=4)
    plain = _slice(base)
    held = _slice(dict(base, hold_points="0.2: 5 s; 0.6: 2 s, cut"))
    assert [(p["x"], p["y"], p["z"]) for p in held.trajectory] == [(p["x"], p["y"], p["z"]) for p in plain.trajectory]
    lines = held.gcode.splitlines()
    dwell = next(i for i, ln in enumerate(lines) if ln.startswith("G4 P5000 ; HOLD"))
    assert lines[dwell - 1].startswith("G1 ")                     # right after its own move
    z = float(re.search(r"Z(-?[\d.]+)", lines[dwell - 1]).group(1))
    assert z == pytest.approx(0.2 + 0.2, abs=1e-6)                # first layer + 0.2 mm
    pause = next(i for i, ln in enumerate(lines) if ln.startswith("M0 "))
    assert lines[pause - 2].startswith("G4 P2000 ; HOLD")
    assert lines[pause - 1] == "M1995 P47 S0 ; UV OFF for the pause"
    assert lines[pause + 1].startswith("M1995 P47 S225") and lines[pause + 2].startswith("G4 P500")
    assert held.stats["print_time_s"] == pytest.approx(plain.stats["print_time_s"] + 7.0)
    assert sum("Hold at" in n for n in held.info) == 2
    assert held.gcode.replace("\n".join(lines[pause - 2:pause + 3]) + "\n", "").replace(lines[dwell] + "\n", "") \
        == plain.gcode


def test_hold_above_the_top_is_left_out():
    r = _slice(base_2d_config(layers=2, hold_points="50: 5 s"))
    assert "HOLD" not in r.gcode
    assert any("above the part's top" in n for n in r.info)


def test_hold_on_a_shell_is_never_swallowed_by_an_arc():
    r = _slice(base_revolve_config("Hollow Cylinder (Z)", tracks_per_layer=2, hold_points="7.3: 3 s"))
    lines = r.gcode.splitlines()
    k = next(i for i, ln in enumerate(lines) if "HOLD 3 s" in ln)
    assert lines[k - 1].startswith("G1 ")
    held = [p for p in r.trajectory if p.get("hold_s")]
    assert len(held) == 1 and held[0]["z"] >= 0.2 + 7.3 - 1e-6


def test_hold_on_the_blade():
    r = _slice(_blade(hold_points="12: 30 s, cut"))
    held = [p for p in r.trajectory if p.get("hold_cut")]
    assert len(held) == 1 and held[0]["hold_s"] == 30.0
    assert held[0]["z"] == pytest.approx(0.2 + 12.0, abs=0.2)
    assert r.gcode.count("M0 ") == 1
