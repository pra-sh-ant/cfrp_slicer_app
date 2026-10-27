"""Printing options: the blade's brim (rings round its first layer, flat on
the bed, in the same stroke), Adaptive Layer Height on Z shells (each layer a
little taller, the resin following), and cleaning pauses at intervals of
path or time (UV off, the head still)."""
import math

import numpy as np
import pytest
import shapely

from cfrp_core import ConfigError, from_legacy_dict, slice_part
from cfrp_core.config import BLADE
from cfrp_core.layers.revolve import shell_progress
from tests.helpers import base_2d_config, base_revolve_config


def _blade(**over):
    cfg = dict(stem_height=24.0, layer_height=1.0, tracks_per_layer=1, min_radius=2.0, blade_chord=50.0,
               blade_tip_chord=24.0, blade_root_length=10.0, blade_root_width=36.0, blade_root_thickness=16.0)
    cfg.update(over)
    return base_revolve_config(BLADE, **cfg)


# --- brim ------------------------------------------------------------------------

def test_brim_rings_lie_flat_round_the_first_layer_in_one_stroke():
    r = slice_part(from_legacy_dict(_blade(brim_rings=3)))
    plain = slice_part(from_legacy_dict(_blade()))
    tr = r.trajectory
    assert r.validation["status"] == "Pass", r.validation["messages"]
    xy = np.array([(p["x"], p["y"]) for p in tr])
    assert np.linalg.norm(np.diff(xy, axis=0), axis=1).max() < 1.2              # one stroke, no jumps
    z0 = tr[0]["z"]
    root = r.target_shapes[0][1]
    idx = [i for i, p in enumerate(tr) if p["layer"] == 1 and not p.get("is_anchor")
           and not root.buffer(0.7).contains(shapely.Point(p["x"], p["y"]))]
    anchor_n = sum(1 for p in tr if p.get("is_anchor"))
    assert idx == list(range(anchor_n, anchor_n + len(idx)))                   # laid first, after the anchor
    assert all(tr[i]["z"] == pytest.approx(z0) for i in idx)                   # flat on the bed
    reach = max(root.exterior.distance(shapely.Point(tr[i]["x"], tr[i]["y"])) for i in idx)
    assert reach == pytest.approx(3 * 1.2 - 0.6, abs=0.1)                      # three rings, a gap apart
    assert len(tr) > len(plain.trajectory)
    assert any("Brim: 3 ring(s)" in n for n in r.info)


def test_brim_rings_are_checked():
    with pytest.raises(ConfigError, match="Brim Rings"):
        from_legacy_dict(_blade(brim_rings=30))


# --- adaptive layer height ----------------------------------------------------------

def test_adaptive_layers_grow_by_the_step_up_to_the_limit():
    cfg = from_legacy_dict(_blade(layer_height=0.2, adaptive_layers=True, layer_height_step=5.0,
                                  layer_height_limit=0.3))
    height = 34.0
    zs = [p * height for p in shell_progress(cfg, height)]
    steps = np.diff(zs)
    assert zs[0] == 0.0 and zs[-1] == pytest.approx(height)
    assert steps[0] == pytest.approx(0.2) and steps[1] == pytest.approx(0.21)
    assert steps.max() <= 0.3 + 1e-9 and steps[:-2].min() >= 0.2 - 1e-9
    assert np.all(np.diff(steps[:-2]) >= -1e-12)                               # never thinner on the way up
    shrink = from_legacy_dict(_blade(layer_height=0.3, adaptive_layers=True, layer_height_step=-10.0,
                                     layer_height_limit=0.15))
    steps = np.diff([p * height for p in shell_progress(shrink, height)])
    assert steps[0] == pytest.approx(0.3) and steps[:-2].min() >= 0.15 - 1e-9 and steps[-3] == pytest.approx(0.15)
    even = from_legacy_dict(_blade(layer_height=0.2))
    assert shell_progress(even, height) == [i / 170 for i in range(171)]       # off: as before


def test_adaptive_layers_slice_with_the_resin_following_each_layer():
    base = base_revolve_config("Hollow Cylinder (Z)", tracks_per_layer=2, stem_height=10.0, layer_height=0.2)
    r = slice_part(from_legacy_dict(dict(base, adaptive_layers=True, layer_height_step=10.0, layer_height_limit=0.4)))
    assert r.validation["status"] == "Pass", r.validation["messages"]
    tr = r.trajectory
    assert np.diff([p["z"] for p in tr]).min() > -1e-9
    assert r.max_z == pytest.approx(0.2 + 10.0)
    layers = sorted({p["layer"] for p in tr})
    assert len(layers) < 51                                                    # fewer than at an even 0.2 mm
    mean = {k: np.mean([p["e_mult"] for p in tr if p["layer"] == k]) for k in (layers[1], layers[-3])}
    assert mean[layers[-3]] / mean[layers[1]] == pytest.approx(0.4 / 0.2, rel=0.02)   # layer 2: 0.2 above layer 1
    assert any(n.startswith("Adaptive layer height: 0.2 mm to 0.4 mm") for n in r.info)


def test_adaptive_layers_only_on_z_shells():
    flat = slice_part(from_legacy_dict(base_2d_config(adaptive_layers=True, layer_height_step=10.0)))
    plain = slice_part(from_legacy_dict(base_2d_config()))
    assert [(p["x"], p["y"], p["z"]) for p in flat.trajectory] == [(p["x"], p["y"], p["z"]) for p in plain.trajectory]


# --- cleaning pauses ---------------------------------------------------------------------

def _pauses(gcode):
    lines = gcode.splitlines()
    return [k for k, ln in enumerate(lines) if "CLEANING PAUSE" in ln], lines


def test_pause_by_distance_turns_the_uv_off_and_keeps_the_head_still():
    cfg = base_revolve_config("Hollow Cylinder (Z)", tracks_per_layer=2, stem_height=10.0, layer_height=0.5)
    plain = slice_part(from_legacy_dict(cfg))
    r = slice_part(from_legacy_dict(dict(cfg, clean_by_distance=True, clean_every_m=2.0, clean_pause_s=10.0)))
    assert [(p["x"], p["y"], p["z"]) for p in r.trajectory] == [(p["x"], p["y"], p["z"]) for p in plain.trajectory]
    path_m = sum(math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
                 for a, b in zip(r.trajectory, r.trajectory[1:], strict=False)) / 1000.0
    k, lines = _pauses(r.gcode)
    assert len(k) == sum(1 for n in range(1, 100) if n * 2.0 < path_m - 1.0)    # none in the last half interval
    for i in k:
        assert lines[i - 1] == "M1995 P47 S0 ; UV OFF: clean the nozzle"
        assert lines[i].startswith("G4 P10000 ;")
        assert lines[i + 1].startswith("M1995 P47 S225") and lines[i + 2].startswith("G4 P500")
        assert lines[i - 2].startswith("G1 ")                                  # the head stops on its last move
    assert r.stats["print_time_s"] == pytest.approx(plain.stats["print_time_s"] + 10.5 * len(k))
    assert any(f"Cleaning pauses: {len(k)}" in n for n in r.info)


def test_pause_by_time_and_both():
    cfg = base_2d_config(layers=3)
    t_min = slice_part(from_legacy_dict(cfg)).stats["print_time_s"] / 60.0
    every = t_min / 3.5
    by_time = slice_part(from_legacy_dict(dict(cfg, clean_by_time=True, clean_every_min=every)))
    assert len(_pauses(by_time.gcode)[0]) == 3
    both = slice_part(from_legacy_dict(dict(cfg, clean_by_time=True, clean_every_min=every,
                                            clean_by_distance=True, clean_every_m=1000.0)))
    assert len(_pauses(both.gcode)[0]) == 3                                    # (the path is far shorter than 1 km)
    off = slice_part(from_legacy_dict(dict(cfg, clean_every_min=every)))      # the switches decide
    assert not _pauses(off.gcode)[0]
