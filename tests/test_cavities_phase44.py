"""Phase 44 (first part): the cavity check -- spots of the part that no
layer fills, a void through the laminate -- and Fill Pockets, which lets
Auto (Best Fit) choose each layer's pattern partly by covering them."""
import pytest
from shapely.geometry import Point, box

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.coverage import cavity_map, cavity_note


def _layer(points, layer, z):
    return [dict(x=x, y=y, z=z, layer=layer) for x, y in points]


def _zigzag(x0, x1, y0, y1, step):
    pts, y, k = [], y0, 0
    while y <= y1 + 1e-9:
        pts += [(x0, y), (x1, y)] if k % 2 == 0 else [(x1, y), (x0, y)]
        y += step
        k += 1
    return pts


def test_cavity_map_finds_spots_empty_in_every_layer():
    region = box(0.6, 0.6, 39.4, 19.4)                 # printable outline (gap/2 inside a 40 x 20 part)
    shapes = [(0.2, region, 1), (0.4, region, 2)]
    left = _zigzag(0.0, 20.0, 0.6, 20.0, 1.2)          # both layers cover only the left half
    traj = _layer(left, 1, 0.2) + _layer(left, 2, 0.4)
    m = cavity_map(traj, shapes, 1.2)
    assert m["layers"] == 2
    assert 0.4 < m["share"] < 0.55
    assert m["spots"] and m["spots"][0]["x"] > 20.0
    assert "empty in all 2 layers" in cavity_note(m)
    # the second layer covers the right half: nothing is empty in both
    right = _zigzag(20.0, 40.0, 0.6, 20.0, 1.2)
    m = cavity_map(_layer(left, 1, 0.2) + _layer(right, 2, 0.4), shapes, 1.2)
    assert m["share"] < 0.02
    # ... but each half is empty in one layer: a run of 1 layer finds them
    assert cavity_map(_layer(left, 1, 0.2) + _layer(right, 2, 0.4), shapes, 1.2, run=1)["share"] > 0.4


def test_slice_reports_cavities_with_holes_numbered_as_drawn():
    holes = [[tuple(q) for q in Point(x, y).buffer(1.6, 16).exterior.coords[:-1]]
             for x, y in ((8, 8), (52, 8), (8, 32), (52, 32))]
    cfg = dict(shape="Custom Outline", custom_outline=[(0, 0), (60, 0), (60, 40), (0, 40)], custom_holes=holes,
               layers=2, layup_seq="0, 90", fill_solid=True, start_x=40.0, start_y=40.0)
    res = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    note = next(n for n in res.info if n.startswith("Cavities:"))
    assert "empty in all 2 layers" in note
    cav = [m for m in res.markers if m[3] == "cavity"]
    assert cav and 0.0 < res.stats["cavity_share"] < 0.2
    # a spot named "next to hole k" lies nearest the k-th hole as drawn
    sx, sy = 40.0 + res.bed_shift[0], 40.0 + res.bed_shift[1]
    m = cavity_map(res.trajectory, res.target_shapes, 1.2,
                   holes=[box(x + sx - 1.6, y + sy - 1.6, x + sx + 1.6, y + sy + 1.6)
                          for x, y in ((8, 8), (52, 8), (8, 32), (52, 32))])
    for spot in m["spots"]:
        if spot["where"].startswith("next to hole"):
            k = int(spot["where"].split()[-1]) - 1
            cx, cy = ((8, 8), (52, 8), (8, 32), (52, 32))[k]
            others = [((x - spot["x"] + sx) ** 2 + (y - spot["y"] + sy) ** 2) for x, y in ((8, 8), (52, 8), (8, 32), (52, 32))]
            assert others.index(min(others)) == k


def test_single_layer_has_no_cavity_note():
    res = slice_part(from_legacy_dict(dict(shape="Square/Rectangle", width=60.0, height=40.0)))
    assert not any(n.startswith("Cavities:") for n in res.info)


@pytest.mark.parametrize("strict", [False, True])
def test_fill_pockets_lets_layers_take_turns(strict):
    cfg = dict(shape="Triangle", width=60.0, height=60.0, infill_type="Auto (Best Fit)", layers=4,
               layup_seq="0, 90", fill_centre=True, strict_bend_radius=strict)
    off = slice_part(from_legacy_dict(cfg, layup=[0.0, 90.0]))
    on = slice_part(from_legacy_dict(dict(cfg, fill_pockets=True), layup=[0.0, 90.0]))
    assert on.stats["cavity_share"] < 0.7 * off.stats["cavity_share"]
    assert on.stats["coverage"] > off.stats["coverage"] - 0.02
    assert on.validation["status"] != "Fail"
