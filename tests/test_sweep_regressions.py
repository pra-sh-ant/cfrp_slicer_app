"""Regressions found by sweeping every shape x infill x key setting (Phase 24).

Each test pins one root cause that produced a validation error, a false
warning, or a real path defect the old checks hid.
"""
import math

import numpy as np
import pytest
from shapely.geometry import LineString

from cfrp_core import from_legacy_dict, resolve_process_constraints, slice_part
from cfrp_core.geometry import revolve_cross_section
from cfrp_core.geometry._common import _clipper_to_polys, local_bend_radii
from cfrp_core.geometry.serpentine import oriented_serpentine
from cfrp_core.validation import _count_crossings
from tests.helpers import base_2d_config, base_revolve_config, run_slice


def _messages(cfg, layup=(0.0,)):
    return run_slice(cfg, layup)[3]["messages"]


# --- revolve shells ---------------------------------------------------------

def test_square_tube_wall_keeps_full_thickness_through_corners():
    """The hole had sharp corners inside rounded outer ones, so a 1-track
    wall thinned to 0.8 mm at the corners and every layer came out empty."""
    cs = revolve_cross_section(0, 0, 20, 18.8, 'square', min_bend_radius=3, wall_depth=0.6)
    assert cs.exterior.distance(cs.interiors[0]) == pytest.approx(1.2, abs=0.02)
    traj, *_ = run_slice(base_revolve_config("Hollow Square Tube (Z)", tracks_per_layer=1,
                                             base_radius=20.0, min_radius=3.0))
    assert traj


def test_revolve_track_count_is_capped_to_what_fits():
    cfg = from_legacy_dict(base_revolve_config("Hollow Cylinder (Z)", tracks_per_layer=7,
                                               base_radius=10.0, min_radius=3.0))
    resolved = resolve_process_constraints(cfg)
    assert resolved.config.tracks_per_layer == 5
    assert any("reduced from 7 to 5" in n for n in resolved.notes)


def test_revolve_too_small_for_one_track_raises():
    with pytest.raises(ValueError, match="too small for one"):
        resolve_process_constraints(from_legacy_dict(
            base_revolve_config("Hollow Cylinder (Z)", base_radius=4.0, min_radius=3.0)))


def test_square_tube_connector_follows_the_outline():
    """The helical connector lapped a circle, cutting the tube's corners."""
    msgs = _messages(base_revolve_config("Hollow Square Tube (Z)", tracks_per_layer=3,
                                         base_radius=20.0, min_radius=3.0, layer_height=1.0,
                                         stem_height=5.0))
    assert not any("crosses itself" in m for m in msgs)


def test_invalid_clipper_offset_is_repaired():
    """A self-touching offset (micro bow-tie) used to grow back into a spike."""
    bowtie = [[(0, 0), (2000, 2000), (2000, 0), (0, 2000)]]
    (poly,) = _clipper_to_polys(bowtie)
    assert poly.is_valid


# --- flat seams and line patterns -------------------------------------------

def test_fermat_layers_do_not_start_across_the_part():
    """Seams were phase-shifted 180 deg, leaving a chord across every layer."""
    cfg = base_2d_config(shape="Circle", width=60.0, height=60.0, layers=4, tracks_per_layer=15,
                         infill_type="Fermat Spiral (Connected Double Spiral)", min_radius=3.0)
    msgs = _messages(cfg)
    assert not any("crosses itself" in m or "ramp exceeded" in m for m in msgs)


@pytest.mark.parametrize("infill", ["Oriented Serpentine", "45° Diagonal Hatch"])
@pytest.mark.parametrize("shape", ["Hexagon", "Circle", "Triangle"])
def test_zigzag_layers_do_not_cross(shape, infill):
    cfg = base_2d_config(shape=shape, width=60.0, height=60.0, layers=4, infill_type=infill,
                         min_radius=3.0)
    assert not any("crosses itself" in m for m in _messages(cfg, layup=(0.0, 90.0)))


def test_uturns_are_semicircles_between_the_lines():
    from shapely.geometry import box
    path = oriented_serpentine(box(0, 0, 60, 60), 0.0, 6.0, 3.0)
    radii = local_bend_radii(np.asarray(path))
    assert radii.min() == pytest.approx(3.0, rel=0.01)   # lat/2 = 3 mm
    assert LineString(path).is_simple


# --- validator -------------------------------------------------------------

def test_near_parallel_overlap_is_not_a_crossing():
    a = [(0, 0), (10, 0), (10, 1), (0, 1)]
    retrace = [(0, 0.05), (10, -0.05)]   # 0.6 deg X across the first leg
    assert _count_crossings(a + retrace) == 0
    assert _count_crossings([(0, 0), (10, 0), (10, 5), (5, -5)]) == 1


def test_sharp_corner_after_long_straight_is_tight():
    """Three-point circumradius grows with leg length, so this read ~25 mm."""
    pts = np.array([(0.0, 0.0), (50.0, 0.0), (50.0, 0.3)])
    assert local_bend_radii(pts)[0] < 1.0
    for n in (7, 40, 400):   # any sampling density
        arc = np.array([(3 * math.cos(t), 3 * math.sin(t)) for t in np.linspace(0, 1.5, n)])
        assert local_bend_radii(arc)[1:-1] == pytest.approx(3.0, rel=0.01)


def test_coarse_arc_resampled_finely_is_not_tight():
    """A polygonal arc (vertices 0.5 mm apart) resampled at 0.1 mm bunches
    each vertex's turn on one point; per-vertex measures read that as tight."""
    from shapely.geometry import LineString as LS
    coarse = [(3 * math.cos(t), 3 * math.sin(t)) for t in np.arange(0, 3.0, 0.5 / 3)]
    line = LS(coarse)
    fine = np.array([line.interpolate(d).coords[0] for d in np.arange(0, line.length, 0.1)])
    assert local_bend_radii(fine)[10:-10].min() > 2.7


# --- curvature round (Phase 25) ---------------------------------------------

def test_enforce_min_radius_leaves_arcs_at_exactly_r_min_alone():
    """Arcs at r_min read 2.99999... and used to be smoothed into kinks."""
    from shapely.geometry import box

    from cfrp_core.geometry._common import enforce_min_radius
    from cfrp_core.geometry.corner_step import corner_step_path
    path, _ = corner_step_path(box(26, 40, 161, 65), 1.2, 3.0, 7)
    assert np.allclose(enforce_min_radius(path, 3.0), path)


@pytest.mark.parametrize("shape", ["Hexagon", "Circle", "Triangle"])
def test_corner_step_polygons_pass_multilayer(shape):
    """Lane-change steps on one seam, outward layers keep the winding."""
    cfg = base_2d_config(shape=shape, width=60.0, height=60.0, layers=4, tracks_per_layer=7,
                         infill_type="Corner-Step Spiral", min_radius=3.0)
    assert run_slice(cfg)[3]["status"] == "Pass"


def test_boundary_wall_hands_over_with_a_lane_change():
    cfg = base_2d_config(width=135.0, height=25.0, include_boundary=True, tracks_per_layer=7,
                         min_radius=3.0, layers=1)
    assert run_slice(cfg)[3]["status"] == "Pass"


def test_revolve_fermat_uses_archimedean_when_uturn_cannot_fit():
    cfg = from_legacy_dict(base_revolve_config(
        "Hollow Cylinder (Z)", tracks_per_layer=7, base_radius=20.0, min_radius=3.0,
        infill_type="Fermat Spiral (Connected Double Spiral)"))
    resolved = resolve_process_constraints(cfg)
    assert resolved.config.infill_type == "Archimedean (Concentric Spiral)"


# --- multi-layer planning (Phase 26) -----------------------------------------

def _seam_cosines(traj):
    """cos of the angle between each layer's end and the next layer's start."""
    by = {}
    for p in traj:
        if not p.get('is_transition'):
            by.setdefault(p['layer'], []).append((p['x'], p['y']))
    out = []
    nums = sorted(by)
    for a, b in zip(nums[:-1], nums[1:], strict=False):
        A, B = np.asarray(by[a]), np.asarray(by[b])
        ia = next(i for i in range(len(A) - 2, -1, -1) if np.hypot(*(A[-1] - A[i])) >= 1.0)
        ib = next(i for i in range(1, len(B)) if np.hypot(*(B[i] - B[0])) >= 1.0)
        da, db = A[-1] - A[ia], B[ib] - B[0]
        out.append(float(da @ db / np.linalg.norm(da) / np.linalg.norm(db)))
    return out


@pytest.mark.parametrize("w,h", [(135.0, 25.0), (60.0, 60.0)])
def test_corner_step_rectangle_layers_continue_without_reversal(w, h):
    cfg = base_2d_config(width=w, height=h, layers=4, tracks_per_layer=3, min_radius=3.0,
                         infill_type="Corner-Step Spiral")
    traj, _, _, val, _ = run_slice(cfg)
    assert val["status"] == "Pass"
    assert min(_seam_cosines(traj)) > -0.1


def test_tail_trim_ends_exactly_at_the_cut():
    """The vertex past the cut used to be dropped, ending the tail a whole
    segment early (113 mm on a Corner-Step edge)."""
    from cfrp_core.layers import trim_trajectory_tail
    traj = [{'x': 0.0, 'y': 0.0, 'z': 0.2, 'layer': 1, 'v': 9.0},
            {'x': 100.0, 'y': 0.0, 'z': 0.2, 'layer': 1, 'v': 9.0}]
    cut = trim_trajectory_tail(traj, 3.0)
    assert (traj[-1]['x'], traj[-1]['y']) == pytest.approx((cut['x'], cut['y']))
    assert traj[-1]['x'] == pytest.approx(97.0)
    assert traj[-1]['v'] == 9.0


def test_flat_track_count_is_capped_with_a_note():
    cfg = from_legacy_dict(base_2d_config(width=135.0, height=25.0, tracks_per_layer=15,
                                          min_radius=3.0))
    resolved = resolve_process_constraints(cfg)
    assert resolved.config.tracks_per_layer == 8
    assert any("reduced from 15 to 8" in n for n in resolved.notes)


@pytest.mark.parametrize("layup", [(0.0,), (0.0, 45.0, 90.0, -45.0)])
def test_layer_ramps_stay_within_the_transition_angle(layup):
    cfg = base_2d_config(shape="Triangle", width=135.0, height=25.0, layers=6, min_radius=3.0,
                         infill_type="Oriented Serpentine")
    msgs = run_slice(cfg, layup=layup)[3]["messages"]
    assert not any("ramp exceeded" in m for m in msgs)


# --- Phase 34: first layer -----------------------------------------------------------

@pytest.mark.parametrize("infill", ["Oriented Serpentine", "45° Diagonal Hatch", "Archimedean (Concentric Spiral)"])
@pytest.mark.parametrize("first", [None, "Archimedean (Concentric Spiral)", "Corner-Step Spiral"])
def test_first_layer_solid_base(infill, first):
    """A solid first layer (optionally in another pattern) fills the base and
    hands over to the next layer without crossings: a spiral base under
    zigzags runs inside-out with its lead-in on the innermost ring."""
    from cfrp_core.coverage import layer_coverage
    cfg = dict(shape="Square/Rectangle", width=60.0, height=60.0, infill_type=infill, layers=3,
               tracks_per_layer=3, layup_seq="0, 90", first_layer_solid=True, first_layer_infill=first)
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    cov = layer_coverage(res.trajectory, res.target_shapes, res.config.track_gap_mm)
    assert cov[1]["coverage"] > (0.9 if first or infill.startswith("Archimedean") else 0.8)
    assert not any("crosses itself" in m or "Disjointed" in m for m in res.validation["messages"])


def test_first_layer_target_warns():
    cfg = dict(shape="Square/Rectangle", width=135.0, height=25.0, first_layer_min_coverage=0.95)
    res = slice_part(from_legacy_dict(cfg))
    assert any("first layer is only" in m for m in res.validation["messages"])
