"""Per-layer plan: solid bottom/top skins, and spiral directions that hand
over cleanly between spiral and zigzag layers."""
import numpy as np
import pytest
from shapely.geometry import Point, box

from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part
from cfrp_core.layers import plan_layers, skin_summary, stl_skins
from tests.helpers import base_2d_config

ARCH = "Archimedean (Concentric Spiral)"
FERMAT = "Fermat Spiral (Connected Double Spiral)"
SERP = "Oriented Serpentine"
CORNER = "Corner-Step Spiral"


def _plan(n, **over):
    cfg = from_legacy_dict(base_2d_config(layers=n, **over))
    return plan_layers(cfg, n)


@pytest.mark.parametrize("infill", [ARCH, FERMAT, SERP, CORNER])
def test_without_skins_the_plan_is_the_layer_order(infill):
    plans = _plan(5, infill_type=infill)
    assert [p.index for p in plans] == list(range(5))
    assert {p.pattern for p in plans} == {infill}
    assert not any(p.solid or p.skin or p.lead_in for p in plans)


def test_spiral_first_layer_under_zigzags_runs_inside_out_with_a_ring_lead_in():
    plans = _plan(4, infill_type=SERP, first_layer_infill=ARCH, anchor_length=5.0)
    assert plans[0].pattern == ARCH and plans[0].index == 1 and plans[0].lead_in == 5.0
    assert [p.index for p in plans[1:]] == [1, 2, 3]


def test_bottom_and_top_skins_are_solid_with_the_skin_pattern():
    plans = _plan(8, infill_type=SERP, first_layer_infill=ARCH, solid_bottom_layers=3, solid_top_layers=2)
    assert [p.skin for p in plans] == [True] * 3 + [False] * 3 + [True] * 2
    assert [p.pattern for p in plans] == [ARCH] * 3 + [SERP] * 3 + [ARCH] * 2
    assert all(p.solid for p in plans if p.skin)
    # the bottom skins end on the perimeter where the zigzag starts ...
    assert plans[2].index % 2 == 1
    # ... alternating below that, so the first layer is inside-out too
    assert [p.index % 2 for p in plans[:3]] == [1, 0, 1]
    # the top skins start outside-in from the perimeter where the zigzag ended
    assert plans[6].index % 2 == 0 and plans[7].index % 2 == 1
    assert skin_summary(plans) == f"Solid skins: layer(s) 1-3, 7-8 are filled solid ({ARCH})."


def test_first_layer_solid_is_one_bottom_skin():
    plans = _plan(3, first_layer_solid=True)
    assert [p.solid for p in plans] == [True, False, False]
    assert skin_summary(plans) == ""


def test_odd_spiral_run_between_zigzag_skins_ends_with_a_fermat_layer():
    plans = _plan(5, infill_type=ARCH, first_layer_infill=SERP, solid_bottom_layers=1, solid_top_layers=1)
    assert [p.pattern for p in plans] == [SERP, ARCH, ARCH, FERMAT, SERP]
    assert "Layer 4" in plans[3].note and "Fermat" in plans[3].note
    assert plans[1].index % 2 == 0               # starts on the perimeter after the zigzag


def test_even_spiral_run_between_zigzag_skins_needs_no_fermat_layer():
    plans = _plan(4, infill_type=ARCH, first_layer_infill=SERP, solid_bottom_layers=1, solid_top_layers=1)
    assert [p.pattern for p in plans] == [SERP, ARCH, ARCH, SERP]
    assert [p.index % 2 for p in plans[1:3]] == [0, 1]
    assert not any(p.note for p in plans)


def test_fermat_layers_keep_their_order():
    plans = _plan(4, infill_type=FERMAT, first_layer_infill=SERP)
    assert [p.index for p in plans] == [0, 1, 2, 3]


def test_flat_part_skins_are_filled_and_reported():
    cfg = base_2d_config(width=60.0, height=40.0, layers=6, tracks_per_layer=2, solid_bottom_layers=2,
                         solid_top_layers=2, layup_seq="0")
    res = slice_part(from_legacy_dict(cfg), DEFAULT_MACHINE)
    length = {}
    for a, b in zip(res.trajectory, res.trajectory[1:], strict=False):
        if not b.get("is_transition") and not b.get("is_anchor"):
            length[b["layer"]] = length.get(b["layer"], 0.0) + np.hypot(b["x"] - a["x"], b["y"] - a["y"])
    assert min(length[k] for k in (1, 2, 5, 6)) > 2.5 * max(length[k] for k in (3, 4))
    assert "Solid skins: layer(s) 1-2, 5-6 are filled solid" in " ".join(res.info)
    assert res.validation["status"] != "Fail"


def test_skins_default_off_changes_nothing():
    cfg = base_2d_config(width=60.0, height=40.0, layers=3)
    a = slice_part(from_legacy_dict(cfg), DEFAULT_MACHINE)
    b = slice_part(from_legacy_dict(dict(cfg, solid_bottom_layers=0, solid_top_layers=0)), DEFAULT_MACHINE)
    assert a.gcode == b.gcode


def test_negative_skin_count_is_refused():
    from cfrp_core import ConfigError
    with pytest.raises(ConfigError):
        from_legacy_dict(base_2d_config(solid_top_layers=-1))


def test_stl_skins_find_the_first_last_and_exposed_slices():
    disc, ring = Point(0, 0).buffer(20, 64), Point(0, 0).buffer(20, 64).difference(Point(0, 0).buffer(17, 64))
    regions = [disc] * 3 + [ring] * 6          # a cup: solid base, thin wall
    flags = stl_skins(regions, bottom=2, top=2)
    assert flags == [True, True, True, False, False, False, False, True, True]
    assert stl_skins(regions, 0, 0) == [False] * 9
    assert stl_skins([None, disc, disc], 1, 0) == [False, True, False]


def test_stl_cup_gets_a_solid_floor_top(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    base = trimesh.creation.cylinder(radius=20, height=1.2, sections=96)
    base.apply_translation((0, 0, 0.6))
    wall = trimesh.creation.annulus(r_min=14, r_max=20, height=4.0, sections=96)
    wall.apply_translation((0, 0, 1.2 + 2.0))
    cup = trimesh.util.concatenate([base, wall])
    path = tmp_path / "cup.stl"
    cup.export(path)
    cfg = base_2d_config(shape="STL Import", stl_path=str(path), layer_height=0.4, min_radius=3.0,
                         tracks_per_layer=2)
    plain = slice_part(from_legacy_dict(cfg), DEFAULT_MACHINE)
    skinned = slice_part(from_legacy_dict(dict(cfg, solid_bottom_layers=1, solid_top_layers=1)), DEFAULT_MACHINE)
    assert skinned.stats["fiber_length_mm"] > plain.stats["fiber_length_mm"] * 1.2
    assert any(n.startswith("Solid skins:") for n in skinned.info)
    assert not any(n.startswith("Solid skins:") for n in plain.info)


def test_stl_skins_ignore_empty_neighbours():
    sq = box(0, 0, 10, 10)
    assert stl_skins([sq, None, sq, sq], 1, 1) == [True, False, False, True]


@pytest.mark.parametrize("shape,w,h", [("Square/Rectangle", 60.0, 40.0), ("Hexagon", 60.0, 60.0)])
def test_seam_stagger_spreads_flat_spiral_seams_without_crossings(shape, w, h):
    import math

    from cfrp_core.capabilities import field_states
    from cfrp_core.geometry._common import count_crossings

    def starts(stagger):
        cfg = base_2d_config(shape=shape, width=w, height=h, layers=6, tracks_per_layer=7, min_radius=3.0,
                             seam_stagger=stagger)
        res = slice_part(from_legacy_dict(cfg), DEFAULT_MACHINE)
        out = []
        for lay in sorted({p["layer"] for p in res.trajectory}):
            pts = [p for p in res.trajectory if p["layer"] == lay and not p.get("is_anchor")]
            assert count_crossings([(p["x"], p["y"]) for p in pts], 5.0) == 0
            body = [p for p in pts if not p.get("is_transition")]
            out.append((body[0]["x"], body[0]["y"]))
        return min(math.dist(a, b) for i, a in enumerate(out) for b in out[i + 1:])

    assert starts(0.0) < 1.0                    # every seam in one spot
    assert starts(137.5) > 5.0                  # spread round the part
    assert field_states(dict(shape=shape, infill_type=ARCH, layers=6))["seam_stagger"].enabled
    assert not field_states(dict(shape=shape, infill_type=ARCH, layers=1))["seam_stagger"].enabled
    assert not field_states(dict(shape=shape, infill_type=SERP, layers=6))["seam_stagger"].enabled
