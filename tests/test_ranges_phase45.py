"""Phase 45: by loads -- the Load Direction the layup is measured from, and
Height Ranges that give a part's layers a different pattern, layup, Edge
Rings or Fill Solid from one height to another."""
import math

import numpy as np
import pytest

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.capabilities import field_states

RECT = dict(shape="Square/Rectangle", width=60.0, height=30.0, layer_height=0.2, track_gap=1.2, min_radius=3.0,
            infill_type="Oriented Serpentine", fill_solid=True)


def _layers(res):
    out = {}
    for q in res.trajectory:
        if not q.get("is_transition") and not q.get("is_anchor"):
            out.setdefault(q["layer"], []).append((q["x"], q["y"]))
    return out


def _line_angle(points, min_len=8.0):
    """Length-weighted direction (mod 180, degrees) of the layer's long
    straight segments."""
    p = np.asarray(points)
    d = np.diff(p, axis=0)
    length = np.hypot(d[:, 0], d[:, 1])
    keep = length > min_len
    if not keep.any():
        return None
    a = 2.0 * np.arctan2(d[keep, 1], d[keep, 0])
    c, s = (length[keep] * np.cos(a)).sum(), (length[keep] * np.sin(a)).sum()
    return (math.degrees(math.atan2(s, c)) / 2.0) % 180.0


def _near(a, b, tol=6.0):
    return a is not None and min(abs(a - b) % 180.0, 180.0 - abs(a - b) % 180.0) <= tol


def test_load_direction_turns_the_layup():
    res = slice_part(from_legacy_dict(dict(RECT, layers=2, layup_seq="0, 90", load_angle=30.0)))
    layers = _layers(res)
    assert _near(_line_angle(layers[1]), 30.0) and _near(_line_angle(layers[2]), 120.0)
    assert any(n.startswith("Load Direction 30°") for n in res.info)


def test_height_ranges_on_a_flat_part():
    cfg = dict(RECT, layers=6, layup_seq="0, 90", strict_bend_radius=True,
               height_ranges="0-0.4: fermat, solid; 0.4-2: layup 45/-45")
    res = slice_part(from_legacy_dict(cfg))
    assert res.validation["status"] != "Fail", res.validation["messages"]
    note = next(n for n in res.info if n.startswith("Height ranges:"))
    assert "layers 1-2 (0-0.4 mm): Fermat Spiral (Connected Double Spiral), solid" in note
    assert "layers 3-6 (0.4-2 mm): layup 45/-45" in note
    layers = _layers(res)
    for k, want in ((3, 45.0), (4, 135.0), (5, 45.0), (6, 135.0)):
        assert _near(_line_angle(layers[k]), want), (k, _line_angle(layers[k]))


def _stack_stl(path, boxes):
    """An ASCII STL of boxes (x0, y0, z0, x1, y1, z1); the slicer merges
    bodies that touch."""
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "w") as f:
        f.write("solid stack\n")
        for x0, y0, z0, x1, y1, z1 in boxes:
            v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                 (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
            for a, b, c in faces:
                f.write("facet normal 0 0 0\nouter loop\n")
                for k in (a, b, c):
                    f.write(f"vertex {v[k][0]} {v[k][1]} {v[k][2]}\n")
                f.write("endloop\nendfacet\n")
        f.write("endsolid stack\n")
    return str(path)


def test_a_stiff_flange_at_the_bottom_of_an_stl_part(tmp_path):
    # a 60 x 60 x 1 mm flange under a 30 x 30 x 2 mm block
    path = _stack_stl(tmp_path / "flange.stl", [(0, 0, 0, 60, 60, 1), (15, 15, 1, 45, 45, 3)])
    cfg = dict(shape="STL Import", stl_path=path, layer_height=0.25, track_gap=1.2, min_radius=3.0,
               infill_type="Oriented Serpentine", layup_seq="0, 90", fill_solid=True, strict_bend_radius=True,
               height_ranges="0-1: edge 2; 1-10: layup 45/-45")
    res = slice_part(from_legacy_dict(cfg))
    assert res.validation["status"] != "Fail", res.validation["messages"]
    note = next(n for n in res.info if n.startswith("Height ranges:"))
    assert "layers 1-4 (0-1 mm): 2 edge ring(s)" in note and "(1-10 mm): layup 45/-45" in note
    assert any(n.startswith("Layer recipe on 4 slice(s)") for n in res.info), res.info
    layers = _layers(res)
    assert _near(_line_angle(layers[5]), 45.0) and _near(_line_angle(layers[6]), 135.0)


def test_height_range_text_is_checked():
    with pytest.raises(ValueError, match="^Height Ranges: '0-a: solid'"):
        from_legacy_dict(dict(RECT, height_ranges="0-a: solid"))
    with pytest.raises(ValueError, match="'shiny' is not a pattern"):
        from_legacy_dict(dict(RECT, height_ranges="0-1: shiny"))
    with pytest.raises((ValueError, ConfigError), match="must run upwards"):
        from_legacy_dict(dict(RECT, height_ranges="2-1: solid"))


def test_by_loads_settings():
    assert field_states(dict(RECT))["height_ranges"].enabled
    assert field_states(dict(RECT))["load_angle"].enabled
    tube = field_states(dict(shape="Hollow Cylinder (Z)"))
    assert not tube["height_ranges"].enabled and not tube["load_angle"].enabled
