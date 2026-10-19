"""Property test for the Phase 42 fibre options: Auto (Best Fit), Fill Centre
and zigzag turns at the bend radius, on random outlines (convex shapes,
plates with holes), must keep the fibre rules."""
import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point, Polygon

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.geometry._common import count_crossings
from cfrp_core.trajectory import check_trajectory
from tests.test_property_fibre import outline

AUTO = "Auto (Best Fit)"
ARCH = "Archimedean (Concentric Spiral)"
CORNER = "Corner-Step Spiral"
PATTERNS = [AUTO, ARCH, CORNER, "Oriented Serpentine", "45° Diagonal Hatch"]


def _plate(w, h, *holes):
    return [(0, 0), (w, 0), (w, h), (0, h)], [[tuple(q) for q in Point(x, y).buffer(d / 2, 16).exterior.coords[:-1]]
                                              for x, y, d in holes]


# Found by a longer run: a widely spaced hatch's turn between two line ends
# on the curve round a hole bulged into it.
@example(shape=_plate(40, 30, (11, 11, 5), (6.5, 18.5, 2.5)), pattern="45° Diagonal Hatch", gap=1.25, r_min=1.5,
         layers=2, centre=False, keep=True)
@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(shape=outline(), pattern=st.sampled_from(PATTERNS), gap=st.floats(0.9, 1.8), r_min=st.floats(1.0, 3.5),
       layers=st.integers(1, 2), centre=st.booleans(), keep=st.booleans())
def test_new_options_keep_the_fibre_rules(shape, pattern, gap, r_min, layers, centre, keep):
    outline_pts, holes = shape
    cfg = dict(shape="Custom Outline", custom_outline=outline_pts, custom_holes=holes, infill_type=pattern,
               track_gap=gap, min_radius=r_min, layers=layers, layup_seq="0, 90", fill_solid=True,
               fill_centre=centre, zigzag_respect_radius=keep, tracks_per_layer=4, start_x=20.0, start_y=20.0)
    try:
        res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    except (ValueError, ConfigError) as exc:
        assert str(exc)
        assume(False)
        return
    traj = res.trajectory
    assert traj and check_trajectory(traj) == []
    zs = [p["z"] for p in traj]
    assert all(b >= a - 1e-9 for a, b in zip(zs, zs[1:], strict=False)), "Z dropped"
    sx, sy = res.config.geometry.start_x_mm + res.bed_shift[0], res.config.geometry.start_y_mm + res.bed_shift[1]
    part = Polygon([(x + sx, y + sy) for x, y in outline_pts], [[(x + sx, y + sy) for x, y in h] for h in holes])
    layers_pts = {}
    for p in traj:
        if not p.get("is_anchor") and not p.get("is_transition") and not p.get("is_bridge"):
            layers_pts.setdefault(p["layer"], []).append((p["x"], p["y"]))
    for pts in layers_pts.values():
        if len(pts) < 2:
            continue
        line = LineString(pts)
        for h in holes:
            assert not line.intersects(Polygon([(x + sx, y + sy) for x, y in h]).buffer(-0.05)), "fibre over a hole"
        if pattern in (ARCH, CORNER):
            assert count_crossings(pts) == 0, "spiral layer crosses itself"
    zone = part.buffer(gap)
    assert all(zone.contains(Point(p["x"], p["y"])) for p in traj if not p.get("is_anchor")), "fibre off the part"
    if pattern == AUTO:
        assert any(n.startswith("Auto (Best Fit):") for n in res.info)


@pytest.mark.parametrize("pattern", PATTERNS)
def test_new_options_on_a_holed_plate(pattern):
    holes = [[tuple(q) for q in Point(x, y).buffer(1.6, 16).exterior.coords[:-1]] for x, y in ((8, 8), (52, 32))]
    cfg = dict(shape="Custom Outline", custom_outline=[(0, 0), (60, 0), (60, 40), (0, 40)], custom_holes=holes,
               infill_type=pattern, layers=2, layup_seq="0, 90", fill_solid=True, fill_centre=True,
               zigzag_respect_radius=True)
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    assert res.validation["status"] != "Fail", res.validation["messages"][:2]
