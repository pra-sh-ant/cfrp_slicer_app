"""The Wind Turbine Hub -- a disc, printed twice, that clamps the blades'
roots between it, its screw holes drilled after curing -- and the kit that
writes every printed part's settings to fit each other."""
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import shapely

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.config import BLADE, HUB
from cfrp_core.geometry.hub import hub_layout, hub_template_svg
from cfrp_core.project import load_project
from cfrp_core.trajectory import check_trajectory
from cfrp_core.turbine import assembly_notes, kit_parts, write_kit
from tests.helpers import base_revolve_config


def _hub(**over):
    cfg = dict(layer_height=0.5, hub_thickness=2.0, min_radius=3.0)
    cfg.update(over)
    return base_revolve_config(HUB, **cfg)


@pytest.mark.parametrize("blades,shape", [(2, "Rectangle"), (3, "Rectangle"), (4, "Rectangle"), (6, "Rectangle"),
                                          (3, "Square"), (3, "Round")])
def test_hub_layout_keeps_screws_apart_beside_their_roots_and_on_the_disc(blades, shape):
    lay = hub_layout(from_legacy_dict(_hub(hub_blades=blades, blade_root_shape=shape, base_radius=20.0)).geometry)
    d = lay.screw
    seats = [lay.seat(k) for k in range(blades)]
    assert len(lay.screws) == 2 * blades
    for i, p in enumerate(lay.screws):
        assert math.hypot(*p) + 3.0 * d <= lay.radius + 1e-9                     # edge distance
        for q in lay.screws[i + 1:]:
            assert math.dist(p, q) >= 2.5 * d - 1e-9
        own = seats[i // 2]
        assert shapely.Point(p).distance(own) == pytest.approx(d / 2.0 + 1.5, abs=0.01)  # right beside its root
        assert all(shapely.Point(p).distance(s) >= d / 2.0 + 1.0 for k, s in enumerate(seats) if k != i // 2)
    for k, s in enumerate(seats):
        assert max(math.hypot(x, y) for x, y in s.exterior.coords) <= lay.radius + 1e-6   # every seat on the disc
        assert all(not s.intersects(t) for t in seats[k + 1:])
        assert s.distance(shapely.Point(0, 0)) >= lay.shaft / 2.0 + 8.0 - 1e-9   # clear of the coupler's screws


def test_hub_disc_is_one_stroke_filled_round_its_shaft_hole():
    r = slice_part(from_legacy_dict(_hub()))
    tr = r.trajectory
    assert r.validation["status"] == "Pass", r.validation["messages"]
    assert not check_trajectory(tr)
    assert r.stats["coverage"] > 0.95
    xyz = np.array([(p["x"], p["y"], p["z"]) for p in tr if not p.get("is_anchor")])
    assert np.diff(xyz[:, 2]).min() > -1e-9
    assert r.max_z == pytest.approx(0.2 + 2.0)
    lay = hub_layout(r.config.geometry)
    centre = (r.config.geometry.start_x_mm + r.bed_shift[0], r.config.geometry.start_y_mm + r.bed_shift[1])
    dist = np.hypot(xyz[:, 0] - centre[0], xyz[:, 1] - centre[1])
    assert dist.min() >= lay.shaft / 2.0 + 0.5                                 # never over the shaft hole
    assert dist.max() <= lay.radius


def test_hub_notes_say_what_to_drill_and_which_screws():
    r = slice_part(from_legacy_dict(_hub()))
    lay = hub_layout(r.config.geometry)
    drill = next(n for n in r.info if "drill 6 screw holes" in n)
    assert "4.5 mm (M4)" in drill and f"M4 x {lay.screw_length(2.0)} mm" in drill
    for x, y in lay.screws:
        assert f"({x:.1f}, {y:.1f})" in drill


def test_hub_drilling_template_is_to_scale():
    lay = hub_layout(from_legacy_dict(_hub(hub_blades=4)).geometry)
    root = ET.fromstring(hub_template_svg(lay))
    assert root.get("width").endswith("mm")
    w = float(root.get("width")[:-2])
    assert w == pytest.approx(float(root.get("viewBox").split()[2]))           # 1 unit = 1 mm
    ns = "{http://www.w3.org/2000/svg}"
    radii = sorted(float(c.get("r")) for c in root.iter(f"{ns}circle"))
    assert sum(abs(r - lay.drill / 2.0) < 0.01 for r in radii) == 8            # a drill circle per screw
    assert any(abs(r - lay.radius) < 0.01 for r in radii) and any(abs(r - lay.shaft / 2.0) < 0.01 for r in radii)


def test_fermat_on_a_hub_lays_rings():
    r = slice_part(from_legacy_dict(_hub(infill_type="Fermat Spiral (Connected Double Spiral)", track_gap=6.5)))
    assert any("Fermat spiral does not apply" in n for n in r.constraint_notes)


def test_kit_parts_fit_each_other(tmp_path):
    params = dict(_hub(), track_gap=1.1, stem_height=40.0, hold_points="10: 5 s")
    parts = kit_parts(params)
    assert [p["shape"] for p in parts.values()] == [BLADE, HUB, "Conical Dome (Z)"]
    assert all(p["track_gap"] == 1.1 for p in parts.values())                  # one material for all
    assert parts["blade"]["hold_points"] == "10: 5 s" and "hold_points" not in parts["hub_disc"]
    lay = hub_layout(from_legacy_dict(parts["hub_disc"]).geometry)
    cone = parts["nose_cone"]["base_radius"]
    assert max(math.hypot(*p) for p in lay.screws) + 1.125 * lay.screw <= cone <= lay.radius  # covers the washers
    written = write_kit(tmp_path / "kit", params)
    assert sorted(p.name for p in written) == ["assembly.txt", "blade.cfrpproj", "hub_disc.cfrpproj",
                                               "hub_drilling_template.svg", "nose_cone.cfrpproj"]
    blade = load_project(tmp_path / "kit" / "blade.cfrpproj")["params"]
    hub = load_project(tmp_path / "kit" / "hub_disc.cfrpproj")["params"]
    for key in ("blade_root_shape", "blade_root_length", "blade_root_width", "blade_root_thickness"):
        assert blade.get(key) == hub.get(key)                                  # the seats fit the roots
    notes = assembly_notes(params)
    assert "x3" in notes and "6 x M4" in notes and "anticlockwise" in notes
    r = slice_part(from_legacy_dict(dict(parts["nose_cone"], layer_height=1.0)))
    assert r.validation["status"] == "Pass", r.validation["messages"]
