"""Calibration coupons, measured pressure-advance table, seam stagger.

All three are opt-in: with defaults, part output is unchanged (the golden
tests pin that); these tests pin what each does when used.
"""
import math

import pytest

from cfrp_core import ProcessConfig, from_legacy_dict, slice_part
from cfrp_core.calibration import COUPONS, make_coupon
from cfrp_core.config import KinematicsConfig
from cfrp_core.legacy import parse_pa_table
from cfrp_core.validation import validate_trajectory
from tests.helpers import base_revolve_config, run_slice

SETTINGS = [ProcessConfig(), ProcessConfig(min_radius_mm=1.5, track_gap_mm=0.9),
            ProcessConfig(min_radius_mm=5.0, track_gap_mm=2.0)]


@pytest.mark.parametrize("cfg", SETTINGS, ids=["default", "fine", "coarse"])
@pytest.mark.parametrize("kind", COUPONS)
def test_coupon_is_one_labelled_continuous_stroke(kind, cfg):
    traj, gcode = make_coupon(kind, cfg)
    steps = [math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(traj[:-1], traj[1:], strict=False)]
    assert max(steps) < 1.0                       # no travel / jump
    assert len({p['z'] for p in traj}) == 1       # one layer
    assert gcode.startswith(f"; ===== CALIBRATION COUPON: {kind.replace('_', ' ').upper()}")
    assert gcode.count(" TEST ") >= 7             # every band labelled
    status = validate_trajectory(traj, cfg)["status"]
    # Only the bend-radius coupon may bend below r_min: that is its test.
    assert status == ("Warning" if kind == "bend_radius" else "Pass")


def test_pump_floor_coupon_flow_follows_speed():
    cfg = ProcessConfig()
    traj, _ = make_coupon("pump_floor", cfg)
    for p in traj[1:]:
        assert p['e_mult'] == pytest.approx(p['v'] / cfg.kinematics.v_max_mm_s)


def test_pa_table_parsing():
    assert parse_pa_table("5:1.0, 10:3") == ((5.0, 1.0), (10.0, 3.0))
    assert parse_pa_table("") == ()
    with pytest.raises(ValueError, match="speed:advance"):
        parse_pa_table("5-1")


def test_constant_pa_table_equals_fixed_advance():
    fixed = slice_part(ProcessConfig(layers=2))
    table = slice_part(ProcessConfig(layers=2, kinematics=KinematicsConfig(
        pressure_advance_table=((5.0, 3.0), (10.0, 3.0)))))
    assert fixed.gcode == table.gcode


def test_speed_dependent_pa_table_changes_flow_only():
    fixed = slice_part(ProcessConfig(layers=2))
    table = slice_part(ProcessConfig(layers=2, kinematics=KinematicsConfig(
        pressure_advance_table=((5.0, 1.0), (10.0, 6.0)))))
    xy = [(p['x'], p['y'], p['v']) for p in fixed.trajectory]
    assert xy == [(p['x'], p['y'], p['v']) for p in table.trajectory]   # same path & speed
    assert [p['e_mult'] for p in fixed.trajectory] != [p['e_mult'] for p in table.trajectory]


def _seam_stack(traj):
    starts = [(p['x'], p['y']) for p in traj if p.get('layer_start')]
    return max(sum(1 for q in starts if math.dist(p, q) < 2.0) for p in starts)


def test_seam_stagger_spreads_revolve_seams():
    base = base_revolve_config("Hollow Cylinder (Z)", tracks_per_layer=3, layer_height=1.0,
                               stem_height=10.0, base_radius=20.0, min_radius=3.0)
    aligned = run_slice(base)
    staggered = run_slice(dict(base, seam_stagger=137.5))
    assert _seam_stack(aligned[0]) >= 5     # 11 layers: two seam lines (inner/outer ring)
    assert _seam_stack(staggered[0]) <= 2
    assert staggered[3]["status"] == "Pass"


def test_seam_stagger_default_is_aligned():
    assert from_legacy_dict(base_revolve_config()).seam_stagger_deg == 0.0
