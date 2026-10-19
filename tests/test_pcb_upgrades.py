"""Phase 37 upgrades: auto-router, imports, ink presets, flow compensation,
UV cure, embedded electronics, print report, multi-item and vertex editing."""
import pytest
from shapely.geometry import LineString, Point

from cfrp_core import ProcessConfig, slice_part
from cfrp_core.board import slice_structural_board
from cfrp_core.diw import BoardDesign, DiwConfig, Hole, Pad, drc, example_board, slice_diw
from cfrp_core.diw import edit as ed
from cfrp_core.diw.design import trace_shape
from cfrp_core.diw.io import apply_netlist, export_dxf, import_dxf, parse_netlist
from cfrp_core.diw.presets import DIELECTRIC_PRESETS, INK_PRESETS, apply_preset
from cfrp_core.diw.router import auto_route, ratsnest
from cfrp_core.report import html_report


def _pads_board():
    d = BoardDesign(outline=ed.rectangle_outline(70, 50, 6), holes=(Hole(x=35, y=25, d_mm=8),))
    for x, y, n in ((10, 25, "A"), (60, 25, "A"), (35, 8, "B"), (35, 42, "B")):
        d = d.model_copy(update=dict(pads=d.pads + (Pad(x=x, y=y, net=n, shape="circle", w_mm=2, h_mm=2),)))
    return d


def test_ratsnest_lists_unconnected_pads_of_a_net():
    links = ratsnest(_pads_board())
    assert sorted(link["net"] for link in links) == ["A", "B"]
    assert not ratsnest(example_board())               # the example is fully wired


def test_auto_route_connects_everything_cleanly():
    d, rep = auto_route(_pads_board())
    assert rep == {"routed": 2, "crossovers": 0, "failed": []}
    assert not ratsnest(d)
    assert drc(d, DiwConfig())["status"] == "Pass"
    hole = Point(35, 25).buffer(4)
    assert not any(trace_shape(t).intersects(hole) for t in d.traces)


def test_auto_route_reports_what_cannot_be_reached():
    d = BoardDesign(pads=(Pad(x=10, y=20, net="A"), Pad(x=50, y=20, net="A")),
                    traces=(ed.meander(30, 1, 0.1, 0.0, 1, width=0.6, net="B"),))
    wall = ed.Trace(points=((30, 1.5), (30, 38.5)), width_mm=2.0, net="B")
    d = d.model_copy(update=dict(traces=(wall,)))
    _, rep = auto_route(d, crossovers_allowed=False)
    assert rep["failed"] == ["A"]
    # with crossovers (the default) A goes over B's wall on a bridge
    routed, rep = auto_route(d)
    assert rep == {"routed": 1, "crossovers": 1, "failed": []}
    res = drc(routed, DiwConfig())
    assert not any(m.startswith("Short") for m in res["messages"])
    assert any("crossover" in n for n in res["notes"])


def test_dxf_round_trip_and_netlist(tmp_path):
    d = example_board()
    export_dxf(d, tmp_path / "b.dxf")
    back = import_dxf(tmp_path / "b.dxf")
    assert len(back.holes) == 4 and back.holes[0].d_mm == pytest.approx(3.2)
    assert parse_netlist("ref,pin,net\nR1,1,VCC") == {"R1.1": "VCC"}
    assert parse_netlist("GND: J1.1 D1-2") == {"J1.1": "GND", "D1.2": "GND"}
    d2, rep = apply_netlist(d, "R1,1,NEW\nQ7,1,X")
    assert rep == {"assigned": 1, "missing": ["Q7.1"]}
    assert next(p for p in d2.pads if p.name == "R1.1").net == "NEW"


def test_dxf_lines_and_inch_units(tmp_path):
    lines = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "1", "0", "ENDSEC",
             "0", "SECTION", "2", "ENTITIES"]
    for (x0, y0), (x1, y1) in (((0, 0), (2, 0)), ((2, 0), (2, 1)), ((2, 1), (0, 1)), ((0, 1), (0, 0))):
        lines += ["0", "LINE", "8", "0", "10", str(x0), "20", str(y0), "11", str(x1), "21", str(y1)]
    lines += ["0", "CIRCLE", "8", "0", "10", "0.25", "20", "0.5", "40", "0.0625", "0", "ENDSEC", "0", "EOF"]
    (tmp_path / "in.dxf").write_text("\n".join(lines))
    d = import_dxf(tmp_path / "in.dxf")
    xs = [x for x, _ in d.outline]
    assert max(xs) == pytest.approx(50.8) and d.holes[0].d_mm == pytest.approx(3.175)


def test_presets_are_valid_configs():
    for ink in INK_PRESETS:
        for diel in DIELECTRIC_PRESETS:
            cfg = apply_preset(DiwConfig(), ink, diel)
            assert isinstance(cfg, DiwConfig)
    assert apply_preset(DiwConfig(), "Liquid metal (EGaIn)").encapsulate


def test_flow_compensation_and_uv_cure_in_gcode():
    d = example_board()
    g = slice_diw(d, DiwConfig(stop_early_mm=1.0, uv_cure_between_layers=True)).gcode
    assert "ink off (early)" in g
    assert g.count("ink on") == g.count("ink off")          # each run opened and closed once
    assert g.count("UV cure") == 3                           # after each of the 3 layers
    v = slice_diw(d, DiwConfig(dispense="volumetric", prime_e=0.02, retract_e=0.03)).gcode
    assert "prime" in v and "retract" in v


def test_embedded_electronics_sit_between_two_laminates():
    r = slice_structural_board(example_board(), ProcessConfig(layers=4), DiwConfig(embed_after_layer=2),
                               mode="embedded")
    lower_top = max(p["z"] for p in r.fibre.trajectory)
    ink = [p["z"] for p in r.diw.trajectory if p["extruding"]]
    upper_bottom = min(p["z"] for p in r.fibre_upper.trajectory)
    assert lower_top < min(ink) and max(ink) < upper_bottom
    assert r.diw.layers[-1].feature == "encapsulation"       # insulated from the upper carbon
    assert r.gcode.count("G28") == 1 and r.gcode.count("M84") == 1 and r.gcode.count("M0 ") == 2
    # the upper laminate leaves the pads open
    for p in example_board().pads:
        c = Point(p.x + r.offset[0], p.y + r.offset[1])
        assert all(LineString([(a["x"], a["y"]), (b["x"], b["y"])]).distance(c) > 0.5
                   for a, b in zip(r.fibre_upper.trajectory, r.fibre_upper.trajectory[1:], strict=False))
    with pytest.raises(ValueError, match="at least 2 fibre layers"):
        slice_structural_board(example_board(), ProcessConfig(layers=1), DiwConfig(), mode="embedded")


def test_report_contains_every_section():
    r = slice_structural_board(example_board(), ProcessConfig(layers=4), DiwConfig(), mode="embedded")
    h = html_report(board=r, image_png=b"\x89PNG")
    for text in ("Fibre laminate", "Upper laminate", "Electronics (DIW)", "Wires", "Coverage per layer",
                 "data:image/png;base64"):
        assert text in h
    assert "Coverage per layer" in html_report(fibre=slice_part(ProcessConfig(layers=2)))


def test_multi_select_copy_paste_and_vertex_editing():
    d = example_board()
    sels = ed.select_in_rect(d, 25, 25, 50, 31)
    assert ("part", "R1") in sels and ("part", "D1") in sels
    moved = ed.transform_many(d, sels, dx=0, dy=-3)
    assert ed.centre(moved, ("part", "R1"))[1] == pytest.approx(ed.centre(d, ("part", "R1"))[1] - 3)
    clip = ed.copy_items(d, [("part", "R1")])
    d2, new = ed.paste_items(d, clip)
    assert new == [("part", "R2")] and len(d2.pads) == len(d.pads) + 2
    assert len(ed.delete_many(d2, new + [("hole", 0)]).holes) == 3
    t, k = ed.find_vertex(d, 12, 28, tol=0.5)
    assert t == ("trace", 0) and k == 1
    d3 = ed.move_vertex(d, t, k, 14, 30)
    assert d3.traces[0].points[1] == (14, 30)
    seg = ed.find_segment(d, 20, 28, tol=0.5)
    d4 = ed.insert_vertex(d, seg[0], seg[1], 20, 29)
    assert len(d4.traces[0].points) == 4
    assert len(ed.delete_vertex(d4, seg[0], 2).traces[0].points) == 3
    corner = ed.find_vertex(d, 60, 40, tol=0.5)
    assert corner == (("outline", None), 2)
    assert len(ed.delete_vertex(BoardDesign(), ("outline", None), 0).outline) == 3
    assert ed.delete_vertex(ed.delete_vertex(BoardDesign(), ("outline", None), 0), ("outline", None), 0).outline \
        == ed.delete_vertex(BoardDesign(), ("outline", None), 0).outline   # a shape keeps 3 corners


def test_valve_closes_exactly_stop_early_before_each_line_end():
    import re

    from cfrp_core.diw.calibration import flow_coupon
    g = flow_coupon(DiwConfig())
    assert g.count("ink off (early)") == 5                 # every line but the 0 mm one
    lines = g.splitlines()
    gaps = []
    for k, ln in enumerate(lines):
        if "ink off (early)" in ln:
            x_close = float(re.search(r"X([\d.]+)", lines[k - 1]).group(1))
            x_end = [float(re.search(r"X([\d.]+)", m).group(1)) for m in lines[k + 1:k + 4] if m.startswith("G1")][-1]
            gaps.append(round(45.0 - x_close, 3) if x_end == 45.0 else None)
    assert gaps == [0.3, 0.6, 1.0, 1.5, 2.0]
    assert flow_coupon(DiwConfig(dispense="volumetric")).count("retract") >= 5
