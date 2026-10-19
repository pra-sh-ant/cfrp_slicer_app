"""Phase 39 structural-PCB upgrades: crossovers (level-2 wires over
insulating bridges, conformal Z), bead spread, the insulation-edge rule,
current capacity, shorted two-pad parts, ink drying waits."""
import math

import numpy as np
import pytest

from cfrp_core.diw import BoardDesign, DiwConfig, Pad, Trace, drc, example_board, slice_diw
from cfrp_core.diw import edit as ed
from cfrp_core.diw.design import crossovers, footprint
from cfrp_core.diw.slicer import RAMP_SLOPE, conformal_z
from cfrp_core.trajectory import check_trajectory


def _crossing_board():
    """Net A runs left-right on level 1; net B crosses it on level 2."""
    pads = (Pad(x=10, y=20, net="A", w_mm=2, h_mm=2), Pad(x=50, y=20, net="A", w_mm=2, h_mm=2),
            Pad(x=30, y=6, net="B", w_mm=2, h_mm=2), Pad(x=30, y=34, net="B", w_mm=2, h_mm=2))
    traces = (Trace(points=((10, 20), (50, 20)), width_mm=1.0, net="A", name="A wire"),
              Trace(points=((30, 6), (30, 34)), width_mm=1.0, net="B", name="B over", level=2))
    return BoardDesign(outline=ed.rectangle_outline(60, 40, 4), pads=pads, traces=traces)


def test_level_two_wire_crosses_another_net_without_a_short():
    d = _crossing_board()
    res = drc(d, DiwConfig())
    assert res["status"] == "Pass", res["messages"]
    assert any("1 crossover" in n for n in res["notes"])
    assert len(res["nets"]) == 2
    # on one level the same wires are a short
    flat = d.model_copy(update=dict(traces=tuple(t.model_copy(update=dict(level=1)) for t in d.traces)))
    assert any(m.startswith("Short") for m in drc(flat, DiwConfig())["messages"])


def test_crossover_prints_a_bridge_and_climbs_over_it():
    cfg = DiwConfig()
    res = slice_diw(_crossing_board(), cfg)
    assert "crossover" in res.regions and res.regions["crossover"].area > 1.0
    tr = res.trajectory
    assert check_trajectory(tr) == []
    assert any(p["feature"] == "crossover" for p in tr)
    last_layer = max(p["layer"] for p in tr if p["feature"] == "conductor")
    wire = [p for p in tr if p["layer"] == last_layer and p["extruding"]]
    lh = cfg.layer_height_mm
    over = [p["z"] for p in wire if abs(p["y"] - 20) < 0.3]           # on the bridge over A
    beside = [p["z"] for p in wire if abs(p["y"] - 12) < 0.3]         # on the insulation only
    # dielectric 2 + conductor 1 + bridge 2 under it on the crossing; dielectric 2 beside it
    assert max(over) == pytest.approx((2 + 1 + 2 + 1) * lh)
    assert min(beside) == pytest.approx((2 + 1) * lh)
    xy = np.array([(p["x"], p["y"]) for p in wire])
    z = np.array([p["z"] for p in wire])
    steps = np.hypot(*np.diff(xy, axis=0).T)
    climb = np.abs(np.diff(z))[steps > 1e-6] / steps[steps > 1e-6]
    assert climb.max() <= RAMP_SLOPE + 1e-9                          # never steeper than the ramp


def test_conformal_z_ramps_up_before_a_step():
    from shapely.geometry import box
    pts, z = conformal_z([(0, 0), (10, 0)], [(box(5, -1, 10, 1), 2)], 1.0, 0.25, step=0.25)
    z = np.array(z)
    assert z[0] == pytest.approx(1.0) and z[-1] == pytest.approx(1.5)
    x = np.array([p[0] for p in pts])
    assert z[x < 5.0 - 0.5 / RAMP_SLOPE - 0.3].max() == pytest.approx(1.0)    # flat until the ramp
    assert z[(x >= 5.0)].min() == pytest.approx(1.5)                        # on top over the step


def test_level_two_wire_over_a_pad_is_an_error():
    d = _crossing_board()
    d = d.model_copy(update=dict(pads=d.pads + (Pad(x=30, y=27, net="C", w_mm=2, h_mm=2, name="P9"),)))
    msgs = drc(d, DiwConfig())["messages"]
    assert any("crosses pad P9" in m for m in msgs)
    assert [c["under"] for c in crossovers(d)] == ["A wire", "P9"]


def test_bead_spread_widens_spacing_and_checks():
    cfg = DiwConfig(line_width_mm=0.5, spread_factor=1.4)
    assert cfg.bead_mm == pytest.approx(0.7)
    d = BoardDesign(outline=ed.rectangle_outline(40, 30, 3),
                    traces=(Trace(points=((5, 15), (35, 15)), width_mm=0.6, net="A"),))
    msgs = drc(d, cfg)["messages"]
    assert any("narrower than the 0.7 mm printed bead (a 0.5 mm line spreads to 0.7 mm)" in m for m in msgs)
    res = slice_diw(example_board(), cfg)
    assert all(p["width"] == pytest.approx(0.7) for p in res.trajectory)
    plain = slice_diw(example_board(), DiwConfig(line_width_mm=0.5))
    # fewer, wider insulation lines
    assert res.stats["ink_length_mm"]["dielectric"] < 0.85 * plain.stats["ink_length_mm"]["dielectric"]
    with pytest.raises(ValueError):
        DiwConfig(spread_factor=0.8)


def test_ink_near_the_insulation_edge_is_reported():
    assert not any("inside the insulation" in m for m in drc(example_board(), DiwConfig())["messages"])
    thin = DiwConfig(dielectric_margin_mm=0.3)
    assert any("less than 0.5 mm inside the insulation" in m for m in drc(example_board(), thin)["messages"])


def test_current_capacity_of_each_wire():
    cfg = DiwConfig()
    d = example_board()
    rows = drc(d, cfg)["traces"]
    film = cfg.conductor_layers * cfg.layer_height_mm * cfg.cured_thickness_ratio
    assert rows[0]["capacity_a"] == pytest.approx(cfg.max_current_density_a_mm2 * 1.0 * film)
    hot = d.model_copy(update=dict(traces=(d.traces[0].model_copy(update=dict(current_a=10.0)),) + d.traces[1:]))
    res = drc(hot, cfg)
    msg = next(m for m in res["messages"] if "must carry 10 A" in m)
    assert "Widen it to" in msg
    assert res["traces"][0]["drop_v"] == pytest.approx(10.0 * res["traces"][0]["resistance_ohm"])


def test_two_pad_part_on_one_net_is_flagged():
    d = BoardDesign(pads=tuple(footprint("LED / resistor 1206", 30, 20, 0, "R7", ("VCC", "VCC"))))
    assert any(m.startswith("R7: both pads are on net VCC") for m in drc(d, DiwConfig())["messages"])
    assert not any("both pads" in m for m in drc(example_board(), DiwConfig())["messages"])


def test_drying_wait_between_ink_layers():
    cfg = DiwConfig(layer_wait_s=20.0)
    res = slice_diw(example_board(), cfg)
    waits = [line for line in res.gcode.splitlines() if "let the ink layer dry" in line]
    layers = len({p["layer"] for p in res.trajectory if p["extruding"]})
    assert len(waits) == layers - 1 and waits[0].startswith("G4 P20000")
    base = slice_diw(example_board(), DiwConfig())
    assert res.stats["print_time_s"] == pytest.approx(base.stats["print_time_s"] + 20.0 * (layers - 1))


def test_router_uses_a_crossover_only_when_needed():
    from cfrp_core.diw.router import auto_route
    d = BoardDesign(outline=ed.rectangle_outline(60, 40, 4),
                    pads=(Pad(x=10, y=20, net="A"), Pad(x=50, y=20, net="A")),
                    traces=(Trace(points=((30, 1.5), (30, 38.5)), width_mm=2.0, net="B"),))
    routed, rep = auto_route(d)
    assert rep["crossovers"] == 1
    new = [t for t in routed.traces if t.net == "A"]
    assert len(new) == 1 and new[0].level == 2 and "crossover" in new[0].name
    res = slice_diw(routed, DiwConfig())
    assert res.regions["crossover"].area > 0
    assert math.isclose(max(p["z"] for p in res.trajectory if p["extruding"] and p["feature"] == "conductor"),
                        (2 + 1 + 2 + 1) * DiwConfig().layer_height_mm)
