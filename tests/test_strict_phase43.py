"""Phase 43: Strict Bend Radius -- no fibre bend under r_min anywhere (turns,
joins, the Fermat centre turn, Fill Centre lines, layer climbs), spots that
cannot keep it left empty and reported, the audit failing on any bend under
0.98·r_min; off = today's behaviour."""
import math

import numpy as np
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point, Polygon, box

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.capabilities import field_states, infill_states
from cfrp_core.geometry._common import count_crossings, local_bend_radii
from cfrp_core.geometry.serpentine import centre_fill
from cfrp_core.geometry.strict import dubins_curves, strict_path, tight_clusters
from cfrp_core.layers import auto
from cfrp_core.validation import validate_trajectory
from tests.test_property_fibre import outline

ARCH = "Archimedean (Concentric Spiral)"
FERMAT = "Fermat Spiral (Connected Double Spiral)"
SERP = "Oriented Serpentine"
HATCH = "45° Diagonal Hatch"
CORNER = "Corner-Step Spiral"
AUTO = "Auto (Best Fit)"


def _tight_points(traj, r_min):
    """Bends under 0.98·r_min over the whole trajectory, climbs included
    (as the strict audit judges them)."""
    pts, last = [], None
    for p in traj:
        q = (p["x"], p["y"])
        if last is None or math.dist(q, last) > 0.05:
            pts.append(q)
            last = q
    if len(pts) < 3:
        return 0
    return int((local_bend_radii(np.asarray(pts)) < 0.98 * r_min).sum())


def test_dubins_curves_keep_the_radius_and_reach_the_goal():
    rng = np.random.default_rng(3)
    for _ in range(300):
        p0, p1 = tuple(rng.uniform(-10, 10, 2)), tuple(rng.uniform(-10, 10, 2))
        h0, h1 = rng.uniform(-math.pi, math.pi, 2)
        curves = dubins_curves(p0, h0, p1, h1, 3.0)
        assert curves
        for c in curves:
            assert math.dist(c[-1], p1) < 1e-9
            assert local_bend_radii(np.asarray([p0] + c)).min() >= 2.97
            end = math.atan2(c[-1][1] - c[-2][1], c[-1][0] - c[-2][0])
            assert abs(math.remainder(end - h1, 2 * math.pi)) < 0.1


def test_strict_path_replaces_tight_turns_inside_the_part():
    # A zigzag 6 mm apart joined by sharp corners (a square wave).
    part = box(0, 0, 60, 40)
    pts = []
    for k, y in enumerate(np.arange(5.0, 36.0, 6.0)):
        xs = (5.0, 55.0) if k % 2 == 0 else (55.0, 5.0)
        pts += [(xs[0], y), (xs[1], y)]
    assert tight_clusters(pts, 3.0)
    out = strict_path(pts, 3.0, part.buffer(-0.5), 1.2)
    assert not tight_clusters(out, 3.0)
    assert count_crossings(out) == 0
    assert part.buffer(-0.4).covers(LineString(out))
    assert out.repairs > 0 and out.lost >= 0.0


def test_strict_path_leaves_a_spot_empty_when_no_curve_fits():
    # Lines 1.2 mm apart in a strip too narrow for any loop at r = 3 mm.
    strip = box(0, 0, 60, 4)
    pts = [(2, 1.4), (58, 1.4), (58, 2.6), (2, 2.6)]
    out = strict_path(pts, 3.0, strip, 1.2)
    assert not tight_clusters(out, 3.0)
    assert out.lost > 0.0
    assert any(kind in ("later start", "early end") for kind, *_ in out.events)


def test_strict_audit_names_layer_and_kind():
    cfg = from_legacy_dict(dict(shape="Square/Rectangle", width=60.0, height=40.0, strict_bend_radius=True))
    traj = []
    for x, y in [(0, 0), (10, 0), (20, 0), (20, 1.2), (10, 1.2), (0, 1.2)]:
        traj.append(dict(x=x + 50.0, y=y + 50.0, z=0.2, dist=0.0, v=5.0, e_mult=1.0, layer=2))
    res = validate_trajectory(traj, cfg)
    assert res["status"] == "Fail"
    msg = next(m for m in res["messages"] if m.startswith("Bend radius (strict)"))
    assert "layer 2 (turn)" in msg


def test_strict_off_keeps_the_curvature_warning():
    cfg = dict(shape="Square/Rectangle", width=60.0, height=40.0, infill_type=SERP, layers=2, layup_seq="0, 90")
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    assert any(m.startswith("Curvature:") for m in res.validation["messages"])
    assert not any("strict" in m for m in res.validation["messages"])


@pytest.mark.parametrize("pattern", [ARCH, FERMAT, SERP, HATCH, CORNER, AUTO])
@pytest.mark.parametrize("shape", ["Square/Rectangle", "Circle", "Triangle"])
def test_strict_parts_have_no_tight_bend(pattern, shape):
    cfg = dict(shape=shape, width=60.0, height=40.0, infill_type=pattern, layers=4, layup_seq="0, 90",
               fill_solid=True, strict_bend_radius=True)
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    msgs = res.validation["messages"]
    assert res.validation["status"] != "Fail", msgs[:3]
    assert not any("crosses itself" in m for m in msgs), msgs[:3]
    assert _tight_points(res.trajectory, 3.0) == 0


def test_strict_holed_plate_keeps_holes_clear_and_reports_empty_spots():
    holes = [[tuple(q) for q in Point(x, y).buffer(1.6, 16).exterior.coords[:-1]]
             for x, y in ((8, 8), (52, 8), (8, 32), (52, 32))]
    cfg = dict(shape="Custom Outline", custom_outline=[(0, 0), (60, 0), (60, 40), (0, 40)], custom_holes=holes,
               infill_type=SERP, layers=4, layup_seq="0, 90", fill_solid=True, strict_bend_radius=True)
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    msgs = res.validation["messages"]
    assert res.validation["status"] != "Fail", msgs[:3]
    assert _tight_points(res.trajectory, 3.0) == 0
    assert any("fill the area once" in n for n in res.constraint_notes)


def test_strict_fill_centre_lays_one_line_that_moves_between_layers():
    region = box(0, 0, 40, 20)
    ring = list(box(1, 1, 39, 19).exterior.coords)
    lines = [centre_fill(ring, region, 1.2, at_end=True, line=k) for k in range(3)]
    assert all(len(p) == 2 for p in lines)                       # one straight line, no turns
    ys = {round((p[0][1] + p[1][1]) / 2.0, 3) for p in lines}
    assert len(ys) == 3                                           # a different line each layer


def test_auto_strict_rejects_tight_candidates():
    part = box(0, 0, 60, 40)
    tight = [(5, 5), (55, 5), (55, 6.2), (5, 6.2)]                # a 0.6 mm turn
    smooth = list(Point(30, 20).buffer(10, 32).exterior.coords)

    def generate(pattern, angle):
        return tight if pattern == SERP else smooth
    choice, _ = auto.choose(generate, [(SERP, 0.0), (ARCH, 0.0)], part, [], 1.2, 3.0, strict=True)
    assert choice.pattern == ARCH
    choice, _ = auto.choose(generate, [(SERP, 0.0)], part, [], 1.2, 3.0, strict=True)
    assert choice is None


def test_strict_settings_states():
    p = dict(shape="Square/Rectangle", infill_type=SERP, strict_bend_radius=True)
    assert not field_states(p)["zigzag_respect_radius"].enabled
    assert field_states(p)["strict_bend_radius"].enabled
    assert "Strict Bend Radius" in infill_states(p)[SERP].reason
    assert "loop" in infill_states(p)[FERMAT].reason
    for shape in ("Hollow Cylinder (Z)", "3D Overhang (Mushroom)"):
        assert not field_states(dict(shape=shape))["strict_bend_radius"].enabled


def test_strict_is_off_by_default_in_the_core():
    assert from_legacy_dict({}).strict_bend_radius is False


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(shape=outline(), pattern=st.sampled_from([ARCH, CORNER, FERMAT, SERP, HATCH, AUTO]),
       gap=st.floats(0.9, 1.8), r_min=st.floats(1.0, 3.5), layers=st.integers(1, 3), centre=st.booleans())
def test_strict_random_outlines_keep_the_radius(shape, pattern, gap, r_min, layers, centre):
    outline_pts, holes = shape
    cfg = dict(shape="Custom Outline", custom_outline=outline_pts, custom_holes=holes, infill_type=pattern,
               track_gap=gap, min_radius=r_min, layers=layers, layup_seq="0, 90", fill_solid=True,
               fill_centre=centre, tracks_per_layer=4, start_x=20.0, start_y=20.0, strict_bend_radius=True)
    try:
        res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    except (ValueError, ConfigError) as exc:
        assert str(exc)
        assume(False)
        return
    traj = res.trajectory
    assert _tight_points(traj, r_min) == 0
    assert not any(m.startswith("Bend radius (strict)") for m in res.validation["messages"])
    sx, sy = res.config.geometry.start_x_mm + res.bed_shift[0], res.config.geometry.start_y_mm + res.bed_shift[1]
    part = Polygon([(x + sx, y + sy) for x, y in outline_pts], [[(x + sx, y + sy) for x, y in h] for h in holes])
    zone = part.buffer(gap)
    assert all(zone.contains(Point(p["x"], p["y"])) for p in traj if not p.get("is_anchor")), "fibre off the part"
    for layer in {p["layer"] for p in traj}:
        pts = [(p["x"], p["y"]) for p in traj if p["layer"] == layer and not p.get("is_transition")
               and not p.get("is_anchor") and not p.get("is_bridge")]
        if len(pts) > 1:
            for h in holes:
                hole = Polygon([(x + sx, y + sy) for x, y in h]).buffer(-0.05)
                assert not LineString(pts).intersects(hole), "fibre over a hole"
