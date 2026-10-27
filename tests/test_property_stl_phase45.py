"""Property test for Phase 45: the layer recipe on STL slices -- random
extruded outlines with holes (bores through every slice), Edge Rings and
Wrap Holes, Strict Bend Radius on -- must keep the fibre rules: one stroke,
Z never drops, no layer crosses itself, no fibre over a bore or off the
part, no bend under r_min."""
import os

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point, Polygon

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.geometry._common import count_crossings
from cfrp_core.trajectory import check_trajectory
from tests.test_property_fibre import outline
from tests.test_stl_recipe_phase45 import _extruded_stl

CORES = ["Oriented Serpentine", "45° Diagonal Hatch", "Fermat Spiral (Connected Double Spiral)"]
EXAMPLES = int(os.environ.get("STL_EXAMPLES", "6"))      # more for a longer local run


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(shape=outline(), core=st.sampled_from(CORES), gap=st.floats(0.9, 1.6), r_min=st.floats(1.0, 3.0),
       slices=st.integers(2, 6), edge=st.integers(0, 2), wrap=st.booleans())
def test_stl_recipe_keeps_the_fibre_rules(tmp_path_factory, shape, core, gap, r_min, slices, edge, wrap):
    outline_pts, holes = shape
    assume(edge or (wrap and holes))
    poly = Polygon(outline_pts, holes)
    assume(poly.is_valid)
    path = _extruded_stl(tmp_path_factory.mktemp("stl") / "part.stl", poly, slices * 0.25)
    cfg = dict(shape="STL Import", stl_path=path, infill_type=core, layer_height=0.25, track_gap=gap,
               min_radius=r_min, layup_seq="0, 90", fill_solid=True, strict_bend_radius=True, edge_rings=edge,
               wrap_holes=wrap, hole_rings=2)
    try:
        res = slice_part(from_legacy_dict(cfg))
    except (ValueError, ConfigError) as exc:
        assert str(exc)
        assume(False)
        return
    traj = res.trajectory
    assert traj and check_trajectory(traj) == []
    zs = [p["z"] for p in traj]
    assert all(b >= a - 1e-9 for a, b in zip(zs, zs[1:], strict=False)), "Z dropped"
    # the part as placed: the first slice's outline (half a gap in) gives the shift
    bx, by, _bx1, _by1 = res.target_shapes[0][1].bounds
    minx, miny = min(x for x, _y in outline_pts), min(y for _x, y in outline_pts)
    dx, dy = bx - gap / 2.0 - minx, by - gap / 2.0 - miny
    moved = Polygon([(x + dx, y + dy) for x, y in outline_pts], [[(x + dx, y + dy) for x, y in h] for h in holes])
    layers = {}
    for p in traj:
        if not p.get("is_anchor") and not p.get("is_transition") and not p.get("is_bridge"):
            layers.setdefault(p["layer"], []).append((p["x"], p["y"]))
    for pts in layers.values():
        if len(pts) < 2:
            continue
        assert count_crossings(pts) == 0, "a layer crosses itself"
        line = LineString(pts)
        for h in moved.interiors:
            assert not line.intersects(Polygon(h).buffer(-0.3)), "fibre over a bore"
    zone = moved.buffer(gap + 0.3)
    assert all(zone.contains(Point(p["x"], p["y"])) for p in traj if not p.get("is_anchor")), "fibre off the part"
    assert "tighter than" not in " ".join(res.validation["messages"])
