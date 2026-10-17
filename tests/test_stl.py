"""STL slicing (holes, islands, world coordinates) and multi-island strokes."""
import math

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Point, box

from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part
from cfrp_core.coverage import coverage_summary, layer_coverage
from cfrp_core.geometry.mesh_slicer import islands, segments_to_region, slice_mesh, slice_mesh_regions
from cfrp_core.validation import validate_trajectory
from tests.helpers import base_2d_config


def extrude(poly, height):
    """(N, 3, 3) closed triangle soup of `poly` extruded from z=0 to height."""
    tris = []
    for t in shapely.constrained_delaunay_triangles(poly).geoms:
        c = np.asarray(t.exterior.coords)[:3]
        tris.append([[*c[0], 0], [*c[2], 0], [*c[1], 0]])
        tris.append([[*c[0], height], [*c[1], height], [*c[2], height]])
    for p in (poly.geoms if poly.geom_type == 'MultiPolygon' else [poly]):
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)
            for a, b in zip(c[:-1], c[1:], strict=False):
                tris.append([[*a, 0], [*b, 0], [*b, height]])
                tris.append([[*a, 0], [*b, height], [*a, height]])
    return np.asarray(tris, dtype=np.float64)


def write_stl(path, tris):
    """Minimal binary STL writer (normals left zero)."""
    rec = np.zeros(len(tris), dtype=[('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    rec['v'] = tris
    with open(path, 'wb') as f:
        f.write(b'\0' * 80)
        f.write(np.uint32(len(tris)).tobytes())
        f.write(rec.tobytes())


PLATE = box(10, 20, 90, 60).difference(Point(30, 40).buffer(4, 64))
PADS = box(10, 20, 40, 50).union(box(55, 20, 85, 50))


# --- slicer ------------------------------------------------------------------

def test_slice_keeps_world_xy_and_holes():
    (z, region), *_ = slice_mesh_regions(extrude(PLATE, 1.2), 0.4)
    assert region.geom_type == 'Polygon'
    assert len(region.interiors) == 1
    assert region.bounds == pytest.approx((10, 20, 90, 60), abs=1e-6)
    assert region.area == pytest.approx(PLATE.area, rel=1e-3)
    assert z == pytest.approx(0.0)


def test_slice_keeps_every_island():
    slices = slice_mesh_regions(extrude(PADS, 1.2), 0.4)
    assert len(slices) == 3
    found = islands(slices[0][1])
    assert len(found) == 2
    assert sum(p.area for p in found) == pytest.approx(PADS.area, rel=1e-3)


def test_legacy_slice_mesh_returns_largest_island():
    slices = slice_mesh(extrude(box(0, 0, 30, 30).union(box(40, 0, 50, 10)), 1.0), 0.5)
    assert all(poly.geom_type == 'Polygon' for _, poly in slices)
    assert slices[0][1].area == pytest.approx(900.0, rel=1e-3)


def test_island_inside_hole_survives_even_odd():
    ring = box(0, 0, 60, 60).difference(box(10, 10, 50, 50))
    nested = ring.union(box(20, 20, 40, 40))
    (_, region), *_ = slice_mesh_regions(extrude(nested, 1.0), 0.5)
    assert region.area == pytest.approx(nested.area, rel=1e-3)
    assert len(islands(region)) == 2


def test_closing_radius_joins_a_leaky_contour():
    square = [(0, 0), (20, 0), (20, 20), (0, 20)]
    segs = [[square[i], square[(i + 1) % 4]] for i in range(4)]
    segs[2][1] = (0.03, 20.0)   # 0.03 mm gap left by a bad mesh
    region = segments_to_region(np.asarray(segs, dtype=np.float64), closing_radius=0.049)
    assert region.area == pytest.approx(400.0, rel=1e-2)


# --- multi-island stroke -------------------------------------------------------

@pytest.fixture(scope="module")
def pads_stl(tmp_path_factory):
    pytest.importorskip("trimesh")
    path = tmp_path_factory.mktemp("stl") / "pads.stl"
    write_stl(path, extrude(PADS, 1.2))
    return str(path)


def _stl_cfg(path, **overrides):
    cfg = base_2d_config(shape="STL Import", stl_path=path, layer_height=0.4,
                         tracks_per_layer=40, min_radius=3.0)
    cfg.update(overrides)
    return from_legacy_dict(cfg)


@pytest.mark.parametrize("infill", [
    "Archimedean (Concentric Spiral)", "Fermat Spiral (Connected Double Spiral)", "Oriented Serpentine",
])
def test_all_islands_printed_in_one_stroke(pads_stl, infill):
    res = slice_part(_stl_cfg(pads_stl, stl_islands="all", infill_type=infill), DEFAULT_MACHINE)
    traj = res.trajectory
    # Both islands receive fibre on every layer.
    for _z, shape, layer in res.target_shapes:
        pts = [(p['x'], p['y']) for p in traj if p['layer'] == layer and not p.get('is_bridge')]
        assert len(islands(shape)) == 2
        for isl in islands(shape):
            assert shapely.contains_xy(isl.buffer(1.0), *np.asarray(pts).T).sum() > 20
    # Exactly the 15 mm gap is bridged on each layer (minus the edge tolerance).
    per_layer = {}
    for p in traj:
        if p.get('is_bridge'):
            per_layer[p['layer']] = per_layer.get(p['layer'], 0.0) + p['dist']
    assert len(per_layer) == len(res.target_shapes)
    for length in per_layer.values():
        assert 10.0 < length < 16.0
    assert any("Island bridges" in m for m in res.validation["messages"])


def test_island_connector_does_not_cross_its_own_track(pads_stl):
    res = slice_part(_stl_cfg(pads_stl, stl_islands="all"), DEFAULT_MACHINE)
    layer1 = [p for p in res.trajectory if p['layer'] == res.target_shapes[0][2]]
    bridge = [(p['x'], p['y']) for p in layer1 if p.get('is_bridge')]
    assert len(bridge) >= 2
    # The bridge is a straight run between the facing edges of the two pads.
    line = LineString(bridge)
    assert line.length > 10.0
    assert max(y for _, y in bridge) - min(y for _, y in bridge) < 1.0


def test_largest_mode_unchanged_single_island(pads_stl):
    res = slice_part(_stl_cfg(pads_stl), DEFAULT_MACHINE)
    assert not any(p.get('is_bridge') for p in res.trajectory)
    assert all(len(islands(s)) == 1 for _, s, _ in res.target_shapes)


def test_corner_step_falls_back_when_track_count_does_not_fit(pads_stl):
    res = slice_part(_stl_cfg(pads_stl, infill_type="Corner-Step Spiral"), DEFAULT_MACHINE)
    assert len({p['layer'] for p in res.trajectory}) == 3


def test_unfillable_stl_raises(tmp_path):
    pytest.importorskip("trimesh")
    path = tmp_path / "tiny.stl"
    write_stl(path, extrude(box(0, 0, 2, 2), 1.0))
    with pytest.raises(ValueError, match="No STL slice"):
        slice_part(_stl_cfg(str(path)), DEFAULT_MACHINE)


# --- validation and coverage ---------------------------------------------------

def test_validation_reports_missing_layers():
    cfg = from_legacy_dict(base_2d_config())
    traj = []
    for n, layer in enumerate((1, 3)):
        for k in range(10):   # continuous in XY, so no seam-jump error masks warnings
            traj.append({'x': 30.0 + 10 * n + k, 'y': 50.0, 'z': 0.2 * layer, 'dist': 1.0,
                         'v': 5.0, 'layer': layer, 'layer_start': k == 0})
    msgs = validate_trajectory(traj, cfg)["messages"]
    assert any("No toolpath on layer(s) [2]" in m for m in msgs)


def test_coverage_of_a_full_concentric_fill_is_high():
    res = slice_part(from_legacy_dict(base_2d_config(width=60.0, height=30.0, tracks_per_layer=10,
                                                     layers=1)), DEFAULT_MACHINE)
    per_layer = layer_coverage(res.trajectory, res.target_shapes, res.config.track_gap_mm)
    summary = coverage_summary(per_layer)
    assert 0.8 < summary["coverage"] <= 1.0
    assert 0.0 <= summary["overlap"] < 0.2
    assert not math.isnan(summary["worst_coverage"])
