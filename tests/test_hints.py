"""Plain-language fix hints for slicer and board-check messages."""
from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part
from cfrp_core.hints import fix_hint
from cfrp_core.report import html_report
from tests.helpers import base_2d_config


def test_common_problems_get_a_fix():
    assert "Min Bend Radius" in fix_hint("Curvature: 27 point(s) are tighter than the 2.2 mm min bend radius")
    assert "pattern" in fix_hint("Layer 2 path crosses itself (3 internal intersections).").lower()
    assert fix_hint("Fibre over a hole: 0.4 mm of tow crosses a hole of the part on layer(s) [1].")
    assert fix_hint("Short: nets GND, VCC touch (T1, T2).")
    assert fix_hint("Something nobody wrote a hint for.") == ""


def test_report_shows_the_fix_under_the_problem():
    # zigzag U-turns (half a gap) are far tighter than a 3 mm bend radius
    res = slice_part(from_legacy_dict(base_2d_config(width=40.0, height=20.0, min_radius=3.0,
                                                     infill_type="Oriented Serpentine")), DEFAULT_MACHINE)
    page = html_report(res)
    hinted = [m for m in res.validation["messages"] if fix_hint(m)]
    assert hinted, res.validation["messages"]
    assert page.count("How to fix:") == len(hinted)
