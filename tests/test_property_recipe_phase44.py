"""Property test for the Phase 44 layer recipe: Edge Rings, Wrap Holes, Bolt
Holes and Drill After Curing on random holed outlines, with Strict Bend Radius
on, must keep the fibre rules: one stroke, Z never drops, no layer crosses
itself, no fibre over a hole that is not drilled, no fibre off the part."""
import os

from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point, Polygon

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.geometry._common import count_crossings
from cfrp_core.trajectory import check_trajectory
from tests.test_property_fibre import _plate, outline

CORES = ["Oriented Serpentine", "45° Diagonal Hatch", "Fermat Spiral (Connected Double Spiral)"]

EXAMPLES = int(os.environ.get("RECIPE_EXAMPLES", "10"))      # more for a longer local run


# Found by longer runs: an outward zigzag core boxed its own end in with the
# route to its first line, so the join out to the edge ring crossed it.
@example(shape=_plate(89, 48, (30, 17, 4), (43, 13, 6), (24, 8, 3)), core=CORES[1], gap=1.5, r_min=1.0, layers=2,
         edge=1, wrap=False, rings=1, bolt=False, drill=False)
# Widely spaced lines cut back at both ends by turns on opposite sides: the
# next turn stretched the top line past its end, 1.5 mm off the plate (and
# across another line on a 3-layer part).
@example(shape=_plate(40, 30, (7, 7, 3), (25, 14.5, 3)), core=CORES[1], gap=1.21875, r_min=3.0, layers=2,
         edge=2, wrap=True, rings=1, bolt=False, drill=True)
@example(shape=_plate(40, 30, (7, 7, 3), (27, 13.5, 3)), core=CORES[1], gap=1.21875, r_min=3.0, layers=3,
         edge=0, wrap=True, rings=3, bolt=False, drill=True)
# A Fermat core whose centre turn crossed: the bend repair then started the
# layer 339 mm late, with a straight hop across a bolt hole.
@example(shape=_plate(57.18201367565506, 50.561748280306034, (6.5, 6.5, 2.5),
                      (40.502589724560174, 12.709049520606847, 2.5), (30.234450700561545, 21.115946461784205, 6.427269795858166)),
         core=CORES[2], gap=1.4668513451175387, r_min=2.8912686229556686, layers=3, edge=2, wrap=False, rings=1,
         bolt=False, drill=False)
# A climb landing before a 90-degree corner the layer's repair had left to
# it (within its first mm); a bend repair whose curve looped across a line
# that the replaced corner only touched.
@example(shape=_plate(40, 30, (13, 19, 3)), core=CORES[0], gap=1.125, r_min=2.5, layers=2, edge=1, wrap=False,
         rings=1, bolt=False, drill=False)
@example(shape=_plate(40, 30, (10, 16, 3)), core=CORES[1], gap=1.125, r_min=2.0, layers=2, edge=1, wrap=False,
         rings=1, bolt=False, drill=False)
# Runs cut where level turns are impossible, by default: a one-line run
# left between two holes and its connectors crossed (the whole run, turns
# level, stayed on the part).
@example(shape=_plate(40, 44, (7, 7, 3), (11, 19, 3)), core=CORES[0], gap=1.3125, r_min=2.0, layers=1, edge=0,
         wrap=True, rings=1, bolt=False, drill=False)
# A one-line run between two holes that only a connector over a hole
# reached; a strict Fermat layer whose corner the radius smoothing nudged
# across the next arm.
@example(shape=_plate(71, 36, (7, 7, 3), (7, 20, 3)), core=CORES[1], gap=1.0, r_min=2.0, layers=2, edge=2, wrap=True,
         rings=1, bolt=False, drill=False)
@example(shape=_plate(71, 34, (7, 9, 3), (7, 18, 3)), core=CORES[2], gap=1.0, r_min=1.375, layers=3, edge=2,
         wrap=True, rings=1, bolt=False, drill=False)
# A bend repair's loop across an edge ring that the replaced join ran along.
@example(shape=_plate(40, 30, (6.5, 7, 2.5)), core=CORES[0], gap=1.59375, r_min=2.5, layers=1, edge=2, wrap=False,
         rings=1, bolt=False, drill=False)
@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(shape=outline(), core=st.sampled_from(CORES), gap=st.floats(0.9, 1.6), r_min=st.floats(1.0, 3.0),
       layers=st.integers(1, 3), edge=st.integers(0, 2), wrap=st.booleans(), rings=st.integers(1, 3),
       bolt=st.booleans(), drill=st.booleans())
def test_layer_recipe_keeps_the_fibre_rules(shape, core, gap, r_min, layers, edge, wrap, rings, bolt, drill):
    outline_pts, holes = shape
    assume(edge or (wrap and holes))
    drilled = {1} if drill and holes else set()
    cfg = dict(shape="Custom Outline", custom_outline=outline_pts, custom_holes=holes, infill_type=core,
               track_gap=gap, min_radius=r_min, layers=layers, layup_seq="0, 90", fill_solid=True,
               strict_bend_radius=True, edge_rings=edge, wrap_holes=wrap, hole_rings=rings,
               bolt_holes=str(len(holes)) if bolt and holes else "", drill_holes=",".join(map(str, drilled)),
               start_x=20.0, start_y=20.0)
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
    moved = [[(x + sx, y + sy) for x, y in h] for h in holes]
    part = Polygon([(x + sx, y + sy) for x, y in outline_pts], moved)
    layers_pts = {}
    for p in traj:
        if not p.get("is_anchor") and not p.get("is_transition") and not p.get("is_bridge"):
            layers_pts.setdefault(p["layer"], []).append((p["x"], p["y"]))
    for pts in layers_pts.values():
        if len(pts) < 2:
            continue
        assert count_crossings(pts) == 0, "recipe layer crosses itself"
        line = LineString(pts)
        for k, h in enumerate(moved, 1):
            if k not in drilled:
                assert not line.intersects(Polygon(h).buffer(-0.05)), f"fibre over hole {k}"
    zone = part.buffer(gap)
    for k in drilled:
        zone = zone.union(Polygon(moved[k - 1]))
    assert all(zone.contains(Point(p["x"], p["y"])) for p in traj if not p.get("is_anchor")), "fibre off the part"
    assert "tighter than" not in " ".join(res.validation["messages"])
