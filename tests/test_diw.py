"""DIW structural electronics: design model, DRC, toolpaths, G-code, and the
combined fibre + ink board."""
import math

import pytest
from shapely.geometry import LineString, Point, box

from cfrp_core import ConfigError, ProcessConfig
from cfrp_core.board import slice_structural_board
from cfrp_core.diw import (
    BoardDesign,
    DiwConfig,
    Hole,
    Pad,
    Trace,
    build_diw_config,
    drc,
    example_board,
    footprint,
    interdigitated,
    load_design,
    meander,
    save_design,
    slice_diw,
    spiral_coil,
    trace_resistance,
)
from cfrp_core.diw.slicer import concentric, order_paths, raster


def test_example_board_passes_drc():
    res = drc(example_board(), DiwConfig())
    assert res["status"] == "Pass", res["messages"]
    assert {n["net"] for n in res["nets"]} >= {"VCC", "GND", "LED_A", "SG"}


def test_design_round_trips_through_json(tmp_path):
    d = example_board()
    save_design(d, tmp_path / "b.spcb.json")
    assert load_design(tmp_path / "b.spcb.json") == d


def test_short_between_nets_is_an_error():
    d = BoardDesign(traces=(Trace(points=((10, 10), (30, 10)), width_mm=1.0, net="A"),
                            Trace(points=((20, 5), (20, 15)), width_mm=1.0, net="B")))
    res = drc(d, DiwConfig())
    assert res["status"] == "Fail"
    assert any(m.startswith("Short") and "A" in m and "B" in m for m in res["messages"])


def test_tight_clearance_is_a_warning():
    d = BoardDesign(traces=(Trace(points=((10, 10), (30, 10)), width_mm=1.0, net="A"),
                            Trace(points=((10, 11.3), (30, 11.3)), width_mm=1.0, net="B")))
    res = drc(d, DiwConfig(clearance_mm=0.5))
    assert any(m.startswith("Clearance") for m in res["messages"])


def test_trace_over_a_hole_or_off_the_board_is_an_error():
    d = BoardDesign(holes=(Hole(x=20, y=10, d_mm=3.2),),
                    traces=(Trace(points=((10, 10), (30, 10)), width_mm=1.0, net="A"),
                            Trace(points=((50, 20), (65, 20)), width_mm=1.0, net="B")))
    msgs = drc(d, DiwConfig())["messages"]
    assert any("hole at (20, 10)" in m for m in msgs)
    assert any("board edge" in m for m in msgs)


def test_narrow_trace_and_missing_dielectric_on_carbon_are_errors():
    d = BoardDesign(traces=(Trace(points=((10, 10), (30, 10)), width_mm=0.2),))
    msgs = drc(d, DiwConfig(dielectric=False))["messages"]
    assert any("narrower than" in m for m in msgs)
    assert any("Carbon fibre is conductive" in m for m in msgs)


def test_resistance_follows_length_width_and_thickness():
    cfg = DiwConfig(resistivity_ohm_mm=4e-5, layer_height_mm=0.25, cured_thickness_ratio=0.5)
    t = Trace(points=((0, 0), (100, 0)), width_mm=1.0)
    assert trace_resistance(t, cfg) == pytest.approx(4e-5 * 100 / (1.0 * 0.125))
    assert trace_resistance(t, cfg.model_copy(update=dict(conductor_layers=2))) == pytest.approx(
        trace_resistance(t, cfg) / 2)


def test_parametric_features():
    m = meander(0, 0, 20, 1.5, 6, width=0.5)
    assert m.length() == pytest.approx(6 * 20 + 5 * 1.5)
    combs = interdigitated(0, 0, 10, 8, 1.2)
    assert {t.net for t in combs} == {"A", "B"}
    assert drc(BoardDesign(outline=((-5, -5), (20, -5), (20, 20), (-5, 20)), traces=tuple(combs)),
               DiwConfig(clearance_mm=0.2))["status"] != "Fail"
    coil = spiral_coil(30, 20, 3, 4, 1.5)
    assert coil.length() > 2 * math.pi * 3 * 4
    pads = footprint("SOT-23", 10, 10, 90, "Q1", ("B", "E", "C"))
    assert [p.name for p in pads] == ["Q1.1", "Q1.2", "Q1.3"] and pads[2].net == "C"


def test_fills_cover_and_stay_inside():
    region = box(0, 0, 6, 3)
    rings = concentric(region, 0.5)
    lines = LineString([q for c, _ in rings for q in c])
    assert lines.buffer(0.25).intersection(region).area / region.area > 0.9
    ras = raster(Point(0, 0).buffer(5), 0.5, 30.0)
    assert len(ras) == 1                                 # one zigzag
    assert LineString(ras[0][0]).difference(Point(0, 0).buffer(5.01)).length < 1e-6


def test_order_paths_reverses_to_shorten_travel():
    a = ([(0, 0), (10, 0)], False)
    b = ([(20, 0), (11, 0)], False)
    out = order_paths([b, a], start=(0, 0))
    assert out[0][0] == (0, 0) and out[1][0] == (11, 0)


def test_diw_slice_stack_and_gcode():
    d = example_board()
    cfg = DiwConfig(encapsulate=True)
    r = slice_diw(d, cfg, z_base=1.0)
    feats = [layer.feature for layer in r.layers]
    assert feats == ["dielectric", "dielectric", "conductor", "encapsulation"]
    zs = sorted({round(p["z"], 3) for p in r.trajectory if p["extruding"]})
    assert zs == [1.25, 1.5, 1.75, 2.0]
    assert r.gcode.count("ink on") == r.stats["starts"] == r.gcode.count("ink off")
    assert "M0 " in r.gcode and "T1" in r.gcode
    vol = slice_diw(d, cfg.model_copy(update=dict(dispense="volumetric")), z_base=1.0).gcode
    assert " E0." in vol and "ink on" not in vol


def test_diw_config_errors_are_plain():
    with pytest.raises(ConfigError, match="line width"):
        build_diw_config({"line_width_mm": 0})


def test_structural_board_puts_ink_on_the_laminate():
    d = example_board()
    proc = ProcessConfig(layers=2, layup_deg=(0.0, 90.0), infill_type="Corner-Step Spiral", fill_solid=True)
    r = slice_structural_board(d, proc, DiwConfig())
    assert r.fibre.validation["status"] == "Pass"
    assert r.diw.validation["status"] == "Pass"
    fibre_top = max(p["z"] for p in r.fibre.trajectory)
    assert min(p["z"] for p in r.diw.trajectory if p["extruding"]) > fibre_top
    # ink sits on the board: the laminate outline (shifted) contains every inked point
    ox, oy = r.offset
    board = box(ox, oy, ox + 60, oy + 40)
    inked = [(p["x"], p["y"]) for p in r.diw.trajectory if p["extruding"]]
    assert all(board.buffer(1e-6).contains(Point(q)) for q in inked)
    # one program: fibre first, then the DIW head, ending with the motors off once
    g = r.gcode
    assert g.index("UV OFF") < g.index("DIW STRUCTURAL ELECTRONICS") < g.rindex("M84")
    assert g.count("M84") == 1
    assert max(p["layer"] for p in r.diw.trajectory) > max(p["layer"] for p in r.fibre.trajectory)


def test_pad_smaller_than_a_bead_still_prints():
    d = BoardDesign(pads=(Pad(x=30, y=20, w_mm=0.4, h_mm=0.4),))
    r = slice_diw(d, DiwConfig())
    assert any(p["extruding"] and p["feature"] == "conductor" for p in r.trajectory)
    assert any("smaller than one" in m for m in r.validation["messages"])


@pytest.mark.parametrize("mode", ["structural", "fibre", "electronics"])
def test_board_print_modes(mode):
    r = slice_structural_board(example_board(), ProcessConfig(), DiwConfig(surface_z_mm=2.0), mode=mode)
    assert (r.fibre is not None) == (mode != "electronics")
    assert (r.diw is not None) == (mode != "fibre")
    if mode == "electronics":
        assert min(p["z"] for p in r.diw.trajectory if p["extruding"]) == pytest.approx(2.25)
        assert "M0 " in r.gcode and "UV ON" not in r.gcode
    if mode == "fibre":
        assert "DIW" not in r.gcode
    with pytest.raises(ValueError, match="Print mode"):
        slice_structural_board(example_board(), ProcessConfig(), DiwConfig(), mode="both")


# --- editing operations (easy editor) ----------------------------------------------

def test_part_moves_and_rotates_as_one():
    from cfrp_core.diw import edit as ed
    d = BoardDesign()
    d, sel = ed.place(d, "led", 30, 20)
    assert sel == ("part", "D1") and len(ed.part_pads(d, "D1")) == 2
    r = ed.transform(d, sel, angle_deg=90)
    xs = sorted(round(p.x, 6) for p in r.pads)
    ys = sorted(round(p.y, 6) for p in r.pads)
    assert xs == [30.0, 30.0] and ys == [18.5, 21.5]          # pads now stacked vertically
    assert all(p.angle_deg == 90 for p in r.pads)
    m = ed.transform(r, sel, dx=5, dy=-2)
    assert ed.centre(m, sel) == pytest.approx((35.0, 18.0))
    assert ed.delete(m, sel).pads == ()
    assert ed.pick(m, 35, 19.5) == sel


def test_wire_takes_the_net_of_the_pad_it_starts_on():
    from cfrp_core.diw import edit as ed
    d = BoardDesign(pads=(Pad(x=10, y=10, net="VCC"), Pad(x=30, y=10)))
    d2, net = ed.auto_net(d, [(10, 10), (30, 10)])
    assert net == "VCC" and d2.pads[1].net == "VCC"
    d3, net2 = ed.auto_net(BoardDesign(pads=(Pad(x=10, y=10), Pad(x=30, y=10))), [(10, 10), (30, 10)])
    assert net2 == "N1" and {p.net for p in d3.pads} == {"N1"}


def test_every_palette_part_places_cleanly():
    from cfrp_core.diw import edit as ed
    d = BoardDesign(outline=((0, 0), (120, 0), (120, 90), (0, 90)))
    for k, key in enumerate(ed.PALETTE):
        d, _ = ed.place(d, key, 15 + (k % 4) * 28, 15 + (k // 4) * 28)
    assert drc(d, DiwConfig())["status"] == "Pass"
    assert ed.next_ref(d, "D") == "D2"


def test_easy_board_and_corner_holes():
    from cfrp_core.diw import edit as ed
    d = BoardDesign(outline=ed.rectangle_outline(80, 50, 6.0))
    d = ed.corner_holes(ed.corner_holes(d))          # twice: no duplicates
    assert len(d.holes) == 4 and (d.holes[0].x, d.holes[0].y) == (5.0, 5.0)
    from shapely.geometry import Polygon
    assert Polygon(d.outline).area < 80 * 50 and Polygon(d.outline).exterior.is_ccw


def test_ink_never_leaves_the_board_and_always_sits_on_insulation():
    """A wire run past the edge and across a hole: the DRC fails it, but the
    slicer still must not print ink in the air, and every conductor point
    must lie on the dielectric (audit: 174 points off the board, 288 off the
    insulation)."""
    from shapely.geometry import Point, Polygon
    d = BoardDesign(holes=(Hole(x=30, y=20, d_mm=4.0),),
                    traces=(Trace(points=((10, 20), (70, 20)), width_mm=1.0, net="A"),
                            Trace(points=((5, 2), (55, 2)), width_mm=1.0, net="B")))
    r = slice_diw(d, DiwConfig())
    board = Polygon(d.outline).buffer(1e-6)
    hole = Point(30, 20).buffer(2.0)
    diel = r.regions["dielectric"].buffer(0.05)
    ink = [Point(p["x"], p["y"]) for p in r.trajectory if p["extruding"] and p["feature"] == "conductor"]
    assert ink and all(board.contains(q) and not hole.contains(q) and diel.contains(q) for q in ink)
    assert any("was left out" in m for m in r.validation["messages"])
