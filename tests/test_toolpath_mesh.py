"""Viewer geometry (numpy only): decimation, tube mesh layout, colour modes."""
import math

import numpy as np
import pytest

from cfrp_core import ProcessConfig, slice_part
from gui.toolpath_mesh import (
    FEATURE_INDEX,
    MODES,
    concat,
    from_trajectory,
    mode_colors,
    simplify,
    tube_mesh,
    vertex_colors,
)


@pytest.fixture(scope="module")
def traj():
    from cfrp_core.config import GeometryConfig
    cfg = ProcessConfig(layers=3, geometry=GeometryConfig(width_mm=60, height_mm=25),
                        layup_deg=(0.0, 90.0), infill_type="Oriented Serpentine")
    return slice_part(cfg).trajectory


def test_features_and_layers(traj):
    d = from_trajectory(traj, 1.2, 0.2)
    assert len(d) == len(traj)
    assert (d.feature == FEATURE_INDEX["anchor"]).sum() > 0
    assert (d.feature == FEATURE_INDEX["ramp"]).sum() > 0
    ranges = d.layer_ranges()
    assert sorted(ranges) == [1, 2, 3]
    assert ranges[1][0] == 0 and ranges[3][1] == len(d)


def test_simplify_keeps_shape_and_boundaries(traj):
    d = from_trajectory(traj)
    keep = simplify(d)
    assert len(keep) < len(d)
    s = d.take(keep)
    assert sorted(s.layer_ranges()) == [1, 2, 3]
    # every dropped point lies on the simplified polyline (no corner cut)
    from shapely.geometry import LineString, Point
    line = LineString(s.xyz[:, :2])
    worst = max(line.distance(Point(p)) for p in d.xyz[::7, :2])
    assert worst < 0.05


def test_tube_mesh_layout():
    pts = [{"x": math.cos(t) * 10, "y": math.sin(t) * 10, "z": 0.2, "v": 5, "layer": 1}
           for t in np.linspace(0, 3, 40)]
    d = from_trajectory(pts, 1.2, 0.2)
    m = tube_mesh(d, sides=8)
    assert m.vertices.shape == (40 * 8, 3)
    assert m.faces.shape == (39 * 16, 3)
    assert np.allclose(np.linalg.norm(m.normals, axis=1), 1.0, atol=1e-4)
    first, count = m.face_range(10, 20)
    assert first == 10 * 16 and count == 9 * 16
    # bead centre half a bead height below the nozzle point
    assert m.vertices[:, 2].max() == pytest.approx(0.2, abs=1e-5)


def test_travel_segments_have_no_bead():
    pts = [{"x": float(i), "y": 0.0, "z": 0.2, "layer": 1, "feature": "conductor", "extruding": i != 5}
           for i in range(10)]
    m = tube_mesh(from_trajectory(pts), sides=6)
    seg4 = m.faces[4 * 12:5 * 12]       # segment 4 -> 5 is travel
    assert (seg4 == seg4[:, :1]).all()
    seg3 = m.faces[3 * 12:4 * 12]
    assert not (seg3 == seg3[:, :1]).all()


@pytest.mark.parametrize("mode", MODES)
def test_every_colour_mode(traj, mode):
    d = from_trajectory(traj)
    rgb, legend = mode_colors(mode, d, 3.0)
    assert rgb.shape == (len(d), 3) and rgb.dtype == np.float32
    assert (rgb >= 0).all() and (rgb <= 1).all()
    assert legend["kind"] in ("gradient", "categories")
    assert vertex_colors(rgb, 6).shape == (len(d) * 6, 4)


def test_bend_radius_mode_flags_tight_turns(traj):
    d = from_trajectory(traj)
    _, legend = mode_colors("Bend radius", d, 3.0)
    assert "below r_min" in legend["note"]       # zigzag U-turns at gap/2


def test_concat_fibre_and_diw(traj):
    fib = from_trajectory(traj)
    diw = from_trajectory([{"x": 0, "y": 0, "z": 5, "layer": 1, "feature": "dielectric"},
                           {"x": 9, "y": 0, "z": 5, "layer": 1, "feature": "dielectric"}], tool=1,
                          layer_offset=10)
    both = concat([fib, diw])
    assert len(both) == len(fib) + 2 and both.tool[-1] == 1 and both.breaks[len(fib)]
