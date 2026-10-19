"""Custom Outline shape (structural PCB boards) and rings that bend round holes."""
import re

import pytest
from shapely.affinity import translate
from shapely.geometry import LineString, Point

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.geometry._common import notched_rings

OUTLINE = [(0, 0), (60, 0), (60, 40), (0, 40)]
HOLES = [Point(x, y).buffer(1.6, 16) for x, y in ((5, 5), (55, 5), (55, 35), (5, 35), (30, 20))]


def _board(**over):
    cfg = dict(shape="Custom Outline", custom_outline=OUTLINE,
               custom_holes=[list(h.exterior.coords)[:-1] for h in HOLES],
               start_x=20.0, start_y=20.0, layers=2, layup_seq="0, 90", tracks_per_layer=7)
    cfg.update(over)
    return slice_part(from_legacy_dict(cfg))


def _offset(res):
    sx, sy = 20.0, 20.0
    for m in res.info:
        if "auto-positioned" in m:
            a = re.findall(r"([+-]\d+\.\d+)", m)
            sx, sy = sx + float(a[0]), sy + float(a[1])
    return sx, sy


@pytest.mark.parametrize("infill", ["Archimedean (Concentric Spiral)", "Corner-Step Spiral"])
def test_spiral_rings_bend_round_bolt_holes(infill):
    """Archimedean stopped at the first hole (17 % coverage); the analytic
    Corner-Step rectangle ran straight over the holes (13 mm of fibre)."""
    res = _board(infill_type=infill, fill_solid=True)
    sx, sy = _offset(res)
    layer = LineString([(p["x"], p["y"]) for p in res.trajectory if p["layer"] == 1 and not p.get("is_anchor")])
    assert sum(layer.intersection(translate(h, sx, sy)).length for h in HOLES) == 0.0
    assert res.stats["coverage"] > 0.8
    assert res.validation["status"] == "Pass"


def test_track_cap_counts_rings_that_bend_round_holes():
    res = _board(infill_type="Archimedean (Concentric Spiral)")
    assert not any("reduced from 7 to 1" in n for n in res.constraint_notes)
    assert res.stats["coverage"] > 0.5


def test_notched_rings_stay_off_holes_and_a_gap_apart():
    from shapely.geometry import Polygon
    board = Polygon(OUTLINE, [list(h.exterior.coords) for h in HOLES])
    rings = notched_rings(board, 1.2, 3.0)
    assert len(rings) > 8
    for ring in rings:
        assert all(ring.exterior.distance(h) >= 0.6 - 0.02 for h in HOLES)   # polygonised arcs
    for a, b in zip(rings, rings[1:], strict=False):
        assert a.exterior.distance(b.exterior) >= 1.2 * 0.9


def test_custom_outline_needs_a_real_outline():
    with pytest.raises(ConfigError, match="at least 3 points"):
        from_legacy_dict(dict(shape="Custom Outline", custom_outline=[(0, 0), (1, 1)]))
    with pytest.raises(ConfigError, match="too narrow"):
        from_legacy_dict(dict(shape="Custom Outline", custom_outline=[(0, 0), (50, 0), (50, 5), (0, 5)]))


def test_zigzag_on_a_board_never_crosses_a_hole():
    res = _board(infill_type="Oriented Serpentine")
    sx, sy = _offset(res)
    pts = [(p["x"], p["y"]) for p in res.trajectory if not p.get("is_anchor") and not p.get("is_transition")]
    line = LineString(pts)
    assert sum(line.intersection(translate(h, sx, sy)).length for h in HOLES) == 0.0


def test_holes_that_pinch_the_board_do_not_crash():
    """Two holes almost meeting split the wall centreline in two; the fillet
    took only a single polygon (AttributeError, found by tools/pcb_audit.py)."""
    from shapely.geometry import Polygon

    from cfrp_core.geometry import printable_boundary
    holes = [list(Point(30, y).buffer(9.4, 16).exterior.coords)[:-1] for y in (10, 30)]
    poly = Polygon(OUTLINE, holes)
    assert not printable_boundary(poly, 1.2, 3.0).is_empty
    res = _board(custom_holes=holes, infill_type="Archimedean (Concentric Spiral)", include_boundary=True)
    assert res.trajectory
