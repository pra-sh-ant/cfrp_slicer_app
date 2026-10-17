"""Regressions found by sweeping every shape x infill x key setting (Phase 24).

Each test pins one root cause that produced a validation error, a false
warning, or a real path defect the old checks hid.
"""
import math

import numpy as np
import pytest
from shapely.geometry import LineString

from cfrp_core import from_legacy_dict, resolve_process_constraints
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
    assert local_bend_radii(pts)[0] < 0.5
    arc = np.array([(3 * math.cos(t), 3 * math.sin(t)) for t in np.linspace(0, 1.5, 7)])
    assert local_bend_radii(arc) == pytest.approx(3.0, rel=1e-6)
