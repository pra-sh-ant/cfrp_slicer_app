"""Property tests: random outlines (convex shapes, plates with holes) and
random settings must keep the fibre rules -- never over a hole, on the part,
Z never dropping, spiral layers without crossings, a well-formed trajectory.
Impossible geometry may be refused, but only with a plain ValueError."""
import math

import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point, Polygon

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.geometry._common import count_crossings
from cfrp_core.trajectory import check_trajectory

PATTERNS = ["Archimedean (Concentric Spiral)", "Fermat Spiral (Connected Double Spiral)",
            "Oriented Serpentine", "45° Diagonal Hatch", "Corner-Step Spiral"]
SPIRALS_NO_CROSS = {"Archimedean (Concentric Spiral)", "Corner-Step Spiral"}


@st.composite
def outline(draw):
    kind = draw(st.sampled_from(["convex", "plate"]))
    if kind == "convex":
        n = draw(st.integers(5, 9))
        r = draw(st.floats(18, 40))
        jitter = [draw(st.floats(0.8, 1.0)) for _ in range(n)]
        pts = [(50 + r * j * math.cos(2 * math.pi * k / n), 50 + r * j * math.sin(2 * math.pi * k / n))
               for k, j in enumerate(jitter)]
        poly = Polygon(pts).convex_hull
        return [tuple(c) for c in poly.exterior.coords[:-1]], []
    w, h = draw(st.floats(40, 90)), draw(st.floats(30, 60))
    holes = []
    for _ in range(draw(st.integers(0, 3))):
        d = draw(st.floats(2.5, 7.0))
        x, y = draw(st.floats(d + 4, w - d - 4)), draw(st.floats(d + 4, h - d - 4))
        c = Point(x, y).buffer(d / 2, 16)
        if all(c.distance(Polygon(hh)) > 3.0 for hh in holes):
            holes.append([tuple(q) for q in c.exterior.coords[:-1]])
    return [(0, 0), (w, 0), (w, h), (0, h)], holes


def _plate(w, h, *holes):
    return [(0, 0), (w, 0), (w, h), (0, h)], [[tuple(q) for q in Point(x, y).buffer(d / 2, 16).exterior.coords[:-1]]
                                              for x, y, d in holes]


# Found by longer runs: the ring seam under a hole (fibre over it), the seam
# by a hole crossing the next ring, a zigzag connector straight over a hole.
@example(shape=_plate(40, 30, (7, 7, 3), (18, 7, 3)), pattern=PATTERNS[0], gap=1.0, r_min=3.25, layers=1,
         fill=False, first_solid=True)
@example(shape=_plate(40, 30, (7, 7, 3), (20, 7, 3)), pattern=PATTERNS[0], gap=1.0, r_min=2.25, layers=1,
         fill=True, first_solid=False)
@example(shape=_plate(40, 30, (6.5, 8, 2.5)), pattern=PATTERNS[3], gap=1.0, r_min=1.0, layers=1,
         fill=False, first_solid=False)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(shape=outline(), pattern=st.sampled_from(PATTERNS), gap=st.floats(0.8, 2.0), r_min=st.floats(1.0, 4.0),
       layers=st.integers(1, 3), fill=st.booleans(), first_solid=st.booleans())
def test_fibre_rules_hold_on_random_parts(shape, pattern, gap, r_min, layers, fill, first_solid):
    outline_pts, holes = shape
    cfg = dict(shape="Custom Outline", custom_outline=outline_pts, custom_holes=holes, infill_type=pattern,
               track_gap=gap, min_radius=r_min, layers=layers, layup_seq="0, 90", fill_solid=fill,
               first_layer_solid=first_solid, tracks_per_layer=4, start_x=20.0, start_y=20.0)
    try:
        res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    except (ValueError, ConfigError) as exc:
        assert str(exc)                   # refused in plain words, not a crash
        assume(False)
        return
    traj = res.trajectory
    assert traj
    assert check_trajectory(traj) == []
    zs = [p["z"] for p in traj]
    assert all(b >= a - 1e-9 for a, b in zip(zs, zs[1:], strict=False)), "Z dropped"
    sx, sy = res.config.geometry.start_x_mm + res.bed_shift[0], res.config.geometry.start_y_mm + res.bed_shift[1]
    part = Polygon([(x + sx, y + sy) for x, y in outline_pts], [[(x + sx, y + sy) for x, y in h] for h in holes])
    body = [p for p in traj if not p.get("is_anchor")]
    runs, run = [], []
    for p in body:
        if p.get("is_transition"):
            if len(run) > 1:
                runs.append(run)
            run = []
            continue
        run.append((p["x"], p["y"]))
    if len(run) > 1:
        runs.append(run)
    for hole in holes:
        hp = Polygon([(x + sx, y + sy) for x, y in hole])
        assert not any(LineString(r).intersects(hp.buffer(-0.05)) for r in runs), "fibre over a hole"
    zone = part.buffer(gap)
    assert all(zone.contains(Point(p["x"], p["y"])) for p in body), "fibre off the part"
    if pattern in SPIRALS_NO_CROSS and not first_solid:
        for lay in sorted({p["layer"] for p in body}):
            pts = [(p["x"], p["y"]) for p in body if p["layer"] == lay and not p.get("is_transition")]
            assert count_crossings(pts) == 0, f"layer {lay} crosses itself"


@pytest.mark.parametrize("seed_shape", [
    ([(0, 0), (60, 0), (60, 40), (0, 40)], [[(30 + 3 * math.cos(a / 8 * math.pi), 20 + 3 * math.sin(a / 8 * math.pi))
                                             for a in range(16)]]),
])
def test_example_holed_plate_all_patterns(seed_shape):
    outline_pts, holes = seed_shape
    for pattern in PATTERNS:
        res = slice_part(from_legacy_dict(dict(shape="Custom Outline", custom_outline=outline_pts,
                                                custom_holes=holes, infill_type=pattern, layers=2,
                                                layup_seq="0, 90"), layup=[0.0, 90.0]))
        assert res.validation["status"] != "Fail", (pattern, res.validation["messages"][:2])
