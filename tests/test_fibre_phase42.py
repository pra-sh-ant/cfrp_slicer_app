"""Phase 42 fibre options: zigzag turns that keep the bend radius, a filled
centre for spirals, and the Auto (best fit) pattern."""
import numpy as np
import pytest
from shapely.geometry import LineString
from shapely.ops import unary_union

from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part
from cfrp_core.geometry._common import count_crossings, local_bend_radii
from tests.helpers import base_2d_config

HATCH = "45° Diagonal Hatch"
SERP = "Oriented Serpentine"


def _layers(res):
    out = {}
    for p in res.trajectory:
        if not p.get("is_anchor") and not p.get("is_transition"):
            out.setdefault(p["layer"], []).append((p["x"], p["y"]))
    return out


@pytest.mark.parametrize("pattern", [HATCH, SERP])
def test_zigzag_turns_can_keep_the_bend_radius(pattern):
    cfg = base_2d_config(width=60.0, height=40.0, layers=10, infill_type=pattern, layup_seq="0, 90",
                         track_gap=1.2, min_radius=3.0)
    tight = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]), DEFAULT_MACHINE)
    keep = slice_part(from_legacy_dict(dict(cfg, zigzag_respect_radius=True), layup=[0.0, 90.0]), DEFAULT_MACHINE)
    part = keep.target_shapes[0][1].buffer(0.6)

    def tight_points(res):
        return sum(int((local_bend_radii(np.asarray(pts)) < 3.0 * 0.98).sum()) for pts in _layers(res).values())

    assert tight_points(tight) > 1000
    assert tight_points(keep) < 0.005 * len(keep.trajectory)      # only where layers join
    layers = _layers(keep)
    for pts in layers.values():
        assert count_crossings(pts, 5.0) == 0
    # each layer lays every 5th line; the 5 layers at one angle cover what one tight layer did
    one = LineString(_layers(tight)[1]).buffer(0.6).intersection(part).area
    stack = unary_union([LineString(layers[k]).buffer(0.6) for k in (1, 3, 5, 7, 9)]).intersection(part).area
    assert LineString(layers[1]).buffer(0.6).intersection(part).area < 0.3 * one
    assert stack > 0.9 * one
    assert any("turns kept at the 3.0 mm min bend radius" in n for n in keep.constraint_notes)


@pytest.mark.parametrize("pattern", ["Archimedean (Concentric Spiral)", "Corner-Step Spiral"])
@pytest.mark.parametrize("size", [(60.0, 40.0), (135.0, 25.0)])
def test_fill_centre_closes_the_hollow_middle_without_crossings(pattern, size):
    w, h = size
    cfg = base_2d_config(width=w, height=h, layers=4, infill_type=pattern, fill_solid=True, min_radius=3.0,
                         track_gap=1.2)
    open_ = slice_part(from_legacy_dict(cfg), DEFAULT_MACHINE)
    filled = slice_part(from_legacy_dict(dict(cfg, fill_centre=True)), DEFAULT_MACHINE)
    assert filled.stats["coverage"] > open_.stats["coverage"] + 0.03
    for pts in _layers(filled).values():
        assert count_crossings(pts, 5.0) == 0
    # the middle of the part is covered on every layer
    sx = filled.config.geometry.start_x_mm + filled.bed_shift[0]
    sy = filled.config.geometry.start_y_mm + filled.bed_shift[1]
    from shapely.geometry import Point
    centre = Point(sx + w / 2.0, sy + h / 2.0).buffer(1.0)
    for pts in _layers(filled).values():
        assert LineString(pts).buffer(0.6).intersection(centre).area > 0.5 * centre.area


AUTO = "Auto (Best Fit)"


@pytest.mark.parametrize("shape,w,h", [("Square/Rectangle", 60.0, 40.0), ("Square/Rectangle", 135.0, 25.0),
                                       ("Circle", 50.0, 50.0)])
def test_auto_picks_a_rule_keeping_pattern_per_layer(shape, w, h):
    cfg = base_2d_config(shape=shape, width=w, height=h, layers=4, infill_type=AUTO, layup_seq="0, 90",
                         min_radius=3.0, track_gap=1.2)
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]), DEFAULT_MACHINE)
    note = next(n for n in res.info if n.startswith("Auto (Best Fit): layer(s) 1"))
    assert "covered" in note
    for pts in _layers(res).values():
        assert count_crossings(pts, 5.0) == 0
    # at least as good as filling every layer with the plain Archimedean spiral
    arch = slice_part(from_legacy_dict(dict(cfg, infill_type="Archimedean (Concentric Spiral)", fill_solid=True),
                                       layup=[0.0, 90.0]), DEFAULT_MACHINE)
    assert res.stats["coverage"] >= arch.stats["coverage"] - 0.02
    assert res.validation["status"] != "Fail"


def test_auto_keeps_fibre_off_holes():
    from shapely.geometry import Point, Polygon
    holes = [[tuple(q) for q in Point(x, y).buffer(1.6, 16).exterior.coords[:-1]]
             for x, y in ((5, 5), (55, 5), (55, 35), (5, 35), (30, 20))]
    cfg = dict(shape="Custom Outline", custom_outline=[(0, 0), (60, 0), (60, 40), (0, 40)], custom_holes=holes,
               infill_type=AUTO, layers=2, layup_seq="0, 90", track_gap=1.2, min_radius=3.0)
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    sx, sy = res.config.geometry.start_x_mm + res.bed_shift[0], res.config.geometry.start_y_mm + res.bed_shift[1]
    for pts in _layers(res).values():
        assert count_crossings(pts, 5.0) == 0
        for h in holes:
            assert not LineString(pts).intersects(Polygon([(x + sx, y + sy) for x, y in h]).buffer(-0.1))
    assert res.stats["coverage"] > 0.7
