"""Phase 44 (second part): the layer recipe -- edge rings along the outline,
hole wraps at layer changes (a layer spirals into a hole, the next spirals
out of it the same way round), bolt holes first, drill-after holes printed
over."""
import math
import re

import numpy as np
import pytest
from shapely.geometry import LineString, Point, box

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.capabilities import ARCHIMEDEAN, DIAGONAL_45, FERMAT, ORIENTED_SERPENTINE, field_states
from cfrp_core.geometry._common import count_crossings
from cfrp_core.legacy import parse_hole_list

HOLES = ((12.0, 15.0), (38.0, 15.0))       # two 6 mm holes in a 50 x 30 plate, as drawn


def _plate(**kw):
    holes = [[tuple(q) for q in Point(x, y).buffer(3.0, 16).exterior.coords[:-1]] for x, y in HOLES]
    cfg = dict(shape="Custom Outline", custom_outline=[(0, 0), (50, 0), (50, 30), (0, 30)], custom_holes=holes,
               layers=2, layup_seq="0, 90", fill_solid=True, start_x=40.0, start_y=40.0,
               infill_type=ORIENTED_SERPENTINE, strict_bend_radius=True, wrap_holes=True)
    cfg.update(kw)
    return cfg


def _layers(res):
    out = {}
    for q in res.trajectory:
        out.setdefault(q["layer"], []).append((q["x"], q["y"]))
    return out


def _centre(res, k):
    x, y = HOLES[k - 1]
    return x + 40.0 + res.bed_shift[0], y + 40.0 + res.bed_shift[1]


def _sweep(points, centre):
    """Signed degrees turned round `centre` along `points`."""
    a = np.unwrap([math.atan2(y - centre[1], x - centre[0]) for x, y in points])
    return math.degrees(a[-1] - a[0]) if len(a) > 1 else 0.0


def _near_run(points, centre, reach, from_end):
    """The longest run at an end of `points` within `reach` of `centre`."""
    seq = points[::-1] if from_end else points
    run = []
    for p in seq:
        if math.dist(p, centre) > reach:
            break
        run.append(p)
    return run[::-1] if from_end else run


def test_parse_hole_list():
    assert parse_hole_list("1, 3") == (1, 3)
    assert parse_hole_list("2;4 ,") == (2, 4)
    assert parse_hole_list("") == ()
    assert parse_hole_list([2, "3"]) == (2, 3)
    with pytest.raises(ValueError, match="'a'"):
        parse_hole_list("1, a")
    with pytest.raises(ValueError, match="^Bolt Holes: Hole number 'x'"):
        from_legacy_dict(_plate(bolt_holes="x"))
    with pytest.raises(ConfigError, match="Edge Rings"):
        from_legacy_dict(_plate(edge_rings=6))


@pytest.fixture(scope="module")
def wrapped():
    return slice_part(from_legacy_dict(_plate(edge_rings=1, bolt_holes="2")))


def test_bolt_hole_is_wrapped_across_the_layer_change(wrapped):
    res = wrapped
    assert res.validation["status"] == "Pass", res.validation["messages"]
    assert any("hole 2 at layers 1-2" in n for n in res.info), res.info
    layers = _layers(res)
    c = _centre(res, 2)
    reach = 3.0 + 3 * 1.2           # the hole's two rings and half a gap
    end = _near_run(layers[1], c, reach, from_end=True)
    start = _near_run(layers[2], c, reach, from_end=False)
    # full laps round the hole at the end of layer 1 and the start of layer
    # 2, turning the same way round
    into, out = _sweep(end, c), _sweep(start, c)
    assert abs(into) >= 540.0 and abs(out) >= 540.0, (into, out)
    assert into * out > 0
    # hole 1 (not a bolt hole) is not wrapped
    assert not _near_run(layers[1], _centre(res, 1), reach, from_end=True)


def test_recipe_layers_keep_the_bend_radius_and_do_not_cross(wrapped):
    for pts in _layers(wrapped).values():
        assert count_crossings(pts) == 0
    msgs = " ".join(wrapped.validation["messages"])
    assert "tighter than" not in msgs and "crosses itself" not in msgs


def test_edge_ring_runs_along_the_outline_first(wrapped):
    res = wrapped
    pts = _layers(res)[1]
    sx, sy = 40.0 + res.bed_shift[0], 40.0 + res.bed_shift[1]
    outline = box(sx, sy, sx + 50.0, sy + 30.0).exterior
    # the first 100 mm of fibre (the edge ring) keep about half a gap off the
    # outline (1.5 mm at the corners, rounded to the bend radius)
    run, k = 0.0, 1
    while run < 100.0:
        run += math.dist(pts[k - 1], pts[k])
        k += 1
    d = [outline.distance(Point(p)) for p in pts[:k]]
    assert max(d) < 1.7 and np.median(d) == pytest.approx(0.6, abs=0.15)


def test_drill_after_hole_is_printed_over():
    res = slice_part(from_legacy_dict(_plate(wrap_holes=False, drill_holes="1", layers=1)))
    c = _centre(res, 1)
    laid = LineString(_layers(res)[1])
    assert laid.intersects(Point(c).buffer(2.0)), "fibre runs over the drill-after hole"
    assert not laid.intersects(Point(_centre(res, 2)).buffer(2.0))
    assert any("drill them after curing" in n for n in res.info)


def test_wrong_hole_numbers_are_named():
    res = slice_part(from_legacy_dict(_plate(bolt_holes="7", drill_holes="5", layers=1, wrap_holes=False)))
    assert any(n.startswith("Drill After Curing: no hole 5") for n in res.info), res.info
    assert not any(n.startswith("Bolt Holes:") for n in res.info)    # not used without Wrap Holes


def test_nothing_asked_lays_the_layers_as_before():
    # Wrap Holes on a part without holes, Edge Rings off: no recipe
    cfg = dict(shape="Square/Rectangle", width=40.0, height=25.0, layers=1, infill_type=ORIENTED_SERPENTINE,
               fill_solid=True, strict_bend_radius=True)
    a = slice_part(from_legacy_dict(cfg)).trajectory
    b = slice_part(from_legacy_dict(dict(cfg, wrap_holes=True, hole_rings=4, bolt_holes="1"))).trajectory
    assert [(p["x"], p["y"]) for p in a] == [(p["x"], p["y"]) for p in b]


def test_recipe_settings_greyed_out_where_they_do_nothing():
    flat = dict(shape="Custom Outline", infill_type=ORIENTED_SERPENTINE, layers=2)
    s = field_states(flat)
    assert s["edge_rings"].enabled and s["wrap_holes"].enabled and s["drill_holes"].enabled
    assert not s["hole_rings"].enabled and not s["bolt_holes"].enabled       # Wrap Holes is off
    s = field_states(dict(flat, wrap_holes=True))
    assert s["hole_rings"].enabled and s["bolt_holes"].enabled
    assert not field_states(dict(flat, infill_type=ARCHIMEDEAN))["edge_rings"].enabled
    assert field_states(dict(flat, infill_type=ARCHIMEDEAN, first_layer_infill=FERMAT))["edge_rings"].enabled
    assert not field_states(dict(flat, layers=1))["wrap_holes"].enabled
    rect = field_states(dict(flat, shape="Square/Rectangle", wrap_holes=True))
    assert rect["edge_rings"].enabled and not rect["wrap_holes"].enabled and not rect["drill_holes"].enabled
    assert not rect["bolt_holes"].enabled
    assert field_states(dict(flat, shape="STL Import"))["edge_rings"].enabled        # Phase 45: STL slices too
    # Edge Rings on every layer: Print Outer Boundary adds nothing
    assert not field_states(dict(flat, edge_rings=2))["include_boundary"].enabled
    assert field_states(dict(flat, edge_rings=2, first_layer_infill=ARCHIMEDEAN))["include_boundary"].enabled


@pytest.mark.parametrize("core", [ORIENTED_SERPENTINE, DIAGONAL_45])
def test_every_bolt_hole_is_wrapped_given_two_layers_each(core):
    # the plan's plate: 60 x 40 with four M3 holes, 8 layers, strict on
    centres = ((10.0, 10.0), (50.0, 10.0), (50.0, 30.0), (10.0, 30.0))
    holes = [[tuple(q) for q in Point(x, y).buffer(1.6, 16).exterior.coords[:-1]] for x, y in centres]
    cfg = dict(shape="Custom Outline", custom_outline=[(0, 0), (60, 0), (60, 40), (0, 40)], custom_holes=holes,
               layers=8, layup_seq="0, 90", fill_solid=True, track_gap=1.2, min_radius=3.0, start_x=40.0, start_y=40.0,
               infill_type=core, strict_bend_radius=True, edge_rings=1, wrap_holes=True, hole_rings=2,
               bolt_holes="1, 2, 3, 4")
    res = slice_part(from_legacy_dict(cfg))
    assert res.validation["status"] == "Pass", res.validation["messages"]
    note = next(n for n in res.info if n.startswith("Hole wraps"))
    wraps = {int(h): int(n) for h, n, _ in re.findall(r"hole (\d) at layers (\d)-(\d)", note)}
    assert sorted(wraps) == [1, 2, 3, 4], note
    layers = _layers(res)
    reach = 3.15 + 1.2 + 0.6 + 0.2         # rings at >= 1.05 x r_min round an M3 hole, one gap apart
    for hole, n in wraps.items():
        x, y = centres[hole - 1]
        c = (x + 40.0 + res.bed_shift[0], y + 40.0 + res.bed_shift[1])
        into = _sweep(_near_run(layers[n], c, reach, from_end=True), c)
        out = _sweep(_near_run(layers[n + 1], c, reach, from_end=False), c)
        assert abs(into) >= 540.0 and abs(out) >= 540.0 and into * out > 0, (hole, into, out)


def test_auto_weighs_the_recipe_with_edge_rings(monkeypatch):
    from cfrp_core.layers import recipe as recipe_mod
    weighed = []
    real = recipe_mod.Recipe.layer

    def spy(self, *args, **kw):
        weighed.append(kw.get("record", True))
        return real(self, *args, **kw)
    monkeypatch.setattr(recipe_mod.Recipe, "layer", spy)
    cfg = _plate(infill_type="Auto (Best Fit)", edge_rings=2, wrap_holes=True, layers=2)
    res = slice_part(from_legacy_dict(cfg))
    assert res.validation["status"] == "Pass", res.validation["messages"]
    assert weighed and not any(weighed)                    # candidates only: nothing noted, no wrap
    assert any(n.startswith("Auto (Best Fit):") for n in res.info)
    assert any(n.startswith("Wrap Holes: not used with Auto") for n in res.info)
    assert not any(n.startswith("Layer recipe:") or n.startswith("Hole wraps") for n in res.info)
    s = field_states(dict(shape="Custom Outline", infill_type="Auto (Best Fit)", layers=2))
    assert s["edge_rings"].enabled and not s["wrap_holes"].enabled and not s["hole_rings"].enabled


def test_auto_names_the_edge_rings_it_lays():
    from cfrp_core.layers import auto
    cands = auto.candidates_for(box(0, 0, 60, 40), 0.0, edges=(2,))
    assert (ORIENTED_SERPENTINE, 0.0, 2) in cands and (FERMAT, 0.0, 2) in cands
    assert all(e == 0 for _, _, e in auto.candidates_for(box(0, 0, 60, 40), 0.0))
    choice = auto.Choice(ORIENTED_SERPENTINE, 90.0, 0.5, 0.0, 0, 0.5, 0.0, 2)
    assert "zigzag at 90° in 2 edge ring(s)" in auto.summary([(1, choice)])


def test_a_hole_can_have_its_own_ring_count():
    # hole 2 gets 4 laps, hole 1 the part's Hole Rings (2)
    res = slice_part(from_legacy_dict(_plate(edge_rings=1, bolt_holes="2", hole_ring_counts="0, 4")))
    assert res.validation["status"] == "Pass", res.validation["messages"]
    assert any(n.startswith("Hole wraps (4 ring(s)") for n in res.info), res.info
    layers = _layers(res)
    c = _centre(res, 2)
    reach = 3.15 + 3 * 1.2 + 0.8              # four rings round a 6 mm hole, the first pushed to 1.05 x r_min
    into = _sweep(_near_run(layers[1], c, reach, from_end=True), c)
    assert abs(into) >= 4 * 360.0, into       # a lap of the outer ring, three blended in, a lap of the inner
    with pytest.raises(ConfigError):
        from_legacy_dict(_plate(hole_ring_counts="0, 6"))


def test_hole_marks_from_the_board():
    from cfrp_core.diw import BoardDesign, Hole, hole_marks
    d = BoardDesign(outline=((0, 0), (50, 0), (50, 30), (0, 30)),
                    holes=(Hole(x=8, y=8), Hole(x=42, y=8, fibre="bolt", rings=3), Hole(x=25, y=22, fibre="drill")))
    assert hole_marks(d) == {"bolt_holes": (2,), "drill_holes": (3,), "hole_ring_counts": (0, 3, 0)}
    assert hole_marks(d.model_copy(update=dict(holes=(Hole(x=8, y=8),)))) == {
        "bolt_holes": (), "drill_holes": (), "hole_ring_counts": ()}
