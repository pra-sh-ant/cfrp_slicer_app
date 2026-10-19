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
    with pytest.raises(ValueError, match="No STL slice|STL Scale"):
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


# --- real-world import problems (Phase 28) ------------------------------------

def _stl(tmp_path, name, mesh):
    path = tmp_path / f"{name}.stl"
    mesh.export(path)
    return str(path)


def _slice(path, **overrides):
    cfg = base_2d_config(shape="STL Import", stl_path=path, layer_height=0.4, min_radius=3.0)
    cfg.update(overrides)
    return slice_part(from_legacy_dict(cfg), DEFAULT_MACHINE)


def test_mesh_with_a_missing_face_keeps_its_full_height(tmp_path):
    """A hole in the mesh left every slice through it open; those slices were
    dropped and a 30 mm cylinder printed 1.4 mm tall with status Pass."""
    trimesh = pytest.importorskip("trimesh")
    m = trimesh.creation.cylinder(radius=25, height=30, sections=96)
    m.update_faces(np.arange(len(m.faces)) != 5)
    res = _slice(_stl(tmp_path, "holed", m))
    assert max(p['z'] for p in res.trajectory) > 29.0
    assert any("not watertight" in w for w in res.validation["messages"])
    assert any("mesh has holes" in w for w in res.validation["messages"])


def test_overlapping_bodies_are_unioned_not_cancelled():
    a = extrude(box(0, 0, 40, 20), 1.0)
    b = extrude(box(10, -10, 30, 30), 1.0)
    (_, region), *_ = slice_mesh_regions(np.vstack([a, b]), 0.5)
    assert region.area == pytest.approx(box(0, 0, 40, 20).union(box(10, -10, 30, 30)).area, rel=1e-3)


def test_inward_body_is_a_cavity():
    outer = extrude(box(0, 0, 40, 40), 1.0)
    inner = extrude(box(10, 10, 30, 30), 1.0)[:, ::-1]          # reversed winding
    (_, region), *_ = slice_mesh_regions(np.vstack([outer, inner]), 0.5)
    assert region.area == pytest.approx(1600 - 400, rel=1e-3)


def test_model_in_inches_gets_a_scale_hint(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    path = _stl(tmp_path, "inch", trimesh.creation.box((2.0, 1.0, 0.5)))
    with pytest.raises(ValueError, match="STL Scale to 25.4"):
        _slice(path)
    res = _slice(path, stl_scale=25.4)
    assert max(p['z'] for p in res.trajectory) > 10.0


def test_stl_gets_no_flat_width_note(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    res = _slice(_stl(tmp_path, "cube", trimesh.creation.box((40, 40, 4))))
    assert not any("narrow dimension" in n for n in res.constraint_notes)


def test_printable_boundary_keeps_holes():
    from cfrp_core.geometry import printable_boundary
    ring = Point(0, 0).buffer(25, 64).difference(Point(0, 0).buffer(20, 64))
    assert len(printable_boundary(ring, 1.2, 3.0).interiors) == 1


@pytest.mark.parametrize("angle", [0.0, 45.0, 90.0])
def test_serpentine_stays_on_a_part_with_a_hole(angle):
    """Keeping only the longest segment per scan line put consecutive lines on
    opposite sides of the hole, and the U-turns crossed it (320 mm of an 880 mm
    path in mid-air on a tube); rotating back about the path's own centroid
    also shifted angled layers off the part."""
    from cfrp_core.geometry.serpentine import oriented_serpentine
    ring = Point(0, 0).buffer(40, 96).difference(Point(0, 0).buffer(36, 96))
    path = oriented_serpentine(ring, angle, 1.2, 3.0)
    assert LineString(path).difference(ring.buffer(0.6)).length < 1.0
    assert LineString(path).buffer(0.6).intersection(ring).area / ring.area > 0.75


def test_walls_thinner_than_a_track_are_reported(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    vase = trimesh.creation.revolve(np.array([(20, 0), (20.6, 0), (20.6, 20), (20, 20), (20, 0)], float),
                                    sections=96)
    with pytest.raises(ValueError, match="thinner than one"):
        _slice(_stl(tmp_path, "vase", vase))


# --- Phase 29: robust STL slicing ---------------------------------------------

def test_repaired_offset_ring_keeps_counter_clockwise_winding():
    """Clipper's invalid-ring repair (buffer(0)) returned the ring clockwise;
    the spiral blended it into its counter-clockwise neighbours as a teardrop
    loop across the other rings (2 of 75 layers of a sliced cylinder)."""
    from cfrp_core.geometry._common import _clipper_to_polys
    s = 1000
    bow_tie = [(0, 0), (40 * s, 0), (40 * s, 40 * s), (0, 40 * s), (0, 20 * s),
               (300, 20 * s + 300), (300, 20 * s - 300), (0, 20 * s + 10)]
    (ring,) = _clipper_to_polys([bow_tie])
    assert ring.is_valid and ring.exterior.is_ccw


def test_sliced_cylinder_spiral_never_crosses_itself(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    res = _slice(_stl(tmp_path, "cyl", trimesh.creation.cylinder(radius=25, height=30, sections=96)),
                 tracks_per_layer=7)
    assert not any("crosses itself" in m for m in res.validation["messages"])
    assert not any(m.startswith("Curvature") for m in res.validation["messages"])


BOLTED_PLATE = box(0, 0, 60, 30).difference(Point(15, 15).buffer(3.5, 32)).difference(
    Point(45, 15).buffer(3.5, 32))


@pytest.mark.parametrize("angle", [0.0, 45.0, -45.0, 90.0])
def test_zigzag_around_two_bolt_holes_has_no_crossings(angle):
    """Runs were chained greedily and connectors only checked against laid
    fibre, so they cut through the run they led into (14 crossings at 45
    deg); one long run could also wrap round a hole and box others in."""
    from cfrp_core.geometry._common import count_crossings
    from cfrp_core.geometry.serpentine import oriented_serpentine
    path = oriented_serpentine(BOLTED_PLATE, angle, 1.2, 3.0)
    assert count_crossings(path) == 0
    line = LineString(path)
    assert line.difference(BOLTED_PLATE.buffer(0.6)).length < 1e-6     # never over a hole
    # (line ends stay r_min inside the outline and the holes: the 3 mm band
    # alone is ~30 % of this plate, so 66-75 % is all a zigzag covers here)
    assert line.buffer(0.6).intersection(BOLTED_PLATE).area / BOLTED_PLATE.area > 0.6


def test_thin_tube_walls_get_rings_instead_of_zigzag_stubs(tmp_path):
    """A 5 mm wall cannot hold a zigzag's U-turns at r_min 3: it became stubs
    across the wall (45k curvature points, ~550 crossings)."""
    trimesh = pytest.importorskip("trimesh")
    tube = trimesh.creation.annulus(r_min=20, r_max=25, height=6, sections=96)
    res = _slice(_stl(tmp_path, "tube", tube), infill_type="Oriented Serpentine")
    assert any("too narrow for the zigzag" in n for n in res.info)
    assert not any("crosses itself" in m for m in res.validation["messages"])


def test_fermat_on_single_track_walls_falls_back_to_rings(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    vase = trimesh.creation.annulus(r_min=20, r_max=22, height=4, sections=96)
    res = _slice(_stl(tmp_path, "vase", vase), infill_type="Fermat Spiral (Connected Double Spiral)")
    assert any("Fermat spiral's two tracks" in n for n in res.info)
    assert res.trajectory


def test_fill_solid_fills_a_solid_slice(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    path = _stl(tmp_path, "puck", trimesh.creation.cylinder(radius=20, height=2, sections=96))
    capped = _slice(path, tracks_per_layer=3)
    filled = _slice(path, tracks_per_layer=3, fill_solid=True)
    assert filled.stats["coverage"] > capped.stats["coverage"] + 0.3
    assert filled.stats["coverage"] > 0.85
    assert any("Fill Solid" in m for m in capped.validation["messages"])     # the hint


@pytest.mark.parametrize("infill", ["Archimedean (Concentric Spiral)", "Corner-Step Spiral"])
def test_fill_solid_on_a_flat_part_lays_every_ring_that_fits(infill):
    from cfrp_core.geometry.laminate import max_track_count
    from cfrp_core.geometry.outlines import base_polygon
    cfg = base_2d_config(width=60.0, height=30.0, tracks_per_layer=2, infill_type=infill, layers=1)
    few = slice_part(from_legacy_dict(cfg))
    full = slice_part(from_legacy_dict(dict(cfg, fill_solid=True)))
    fit = max_track_count(base_polygon(full.config.geometry, 1.2, 2.0), 1.2, 2.0, infill, False)
    assert fit > 2
    assert full.stats["fiber_length_mm"] > few.stats["fiber_length_mm"] * 1.5
    assert not any("reduced" in n for n in full.constraint_notes)
