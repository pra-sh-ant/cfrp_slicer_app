"""Tests for pre-slice config rules (ProcessConfig validation +
resolve_process_constraints) and post-slice validate_trajectory."""
import pytest

from cfrp_core import ConfigError, from_legacy_dict, resolve_process_constraints
from cfrp_core.validation import validate_trajectory
from tests.helpers import base_2d_config


def _resolve(cfg):
    return resolve_process_constraints(from_legacy_dict(cfg))


def test_rejects_zero_track_gap():
    with pytest.raises(ValueError):
        _resolve(base_2d_config(track_gap=0.0))


def test_rejects_negative_min_radius():
    with pytest.raises(ValueError):
        _resolve(base_2d_config(min_radius=-1.0))


def test_rejects_tracks_below_one():
    with pytest.raises(ValueError):
        _resolve(base_2d_config(tracks_per_layer=0))


def test_rejects_zero_print_speed():
    with pytest.raises(ValueError):
        _resolve(base_2d_config(v_max=0.0))


def test_rejects_geometrically_impossible_narrow_part():
    """Width/height must leave room for two bend radii plus one track gap."""
    cfg = base_2d_config(shape="Square/Rectangle", width=100.0, height=4.0, min_radius=3.0, track_gap=2.0)
    with pytest.raises(ValueError):
        _resolve(cfg)


def test_error_messages_are_plain_language():
    with pytest.raises(ConfigError) as info:
        _resolve(base_2d_config(track_gap=0.0))
    assert str(info.value) == "Track Gap must be greater than 0 mm."


def test_min_bend_speed_clamped_to_print_speed():
    resolved = _resolve(base_2d_config(v_max=5.0, v_min=50.0))
    assert resolved.config.kinematics.v_min_mm_s == 5.0


def test_valid_config_resolves_without_error():
    resolved = _resolve(base_2d_config())
    assert resolved.config.track_gap_mm > 0
    assert resolved.config.tracks_per_layer >= 1


def test_empty_trajectory_reports_fail():
    result = validate_trajectory([], from_legacy_dict(base_2d_config()))
    assert result["status"] == "Fail"


@pytest.mark.parametrize("key, value", [
    ("accel", 0.0), ("decel", -1.0), ("layer_height", 0.0), ("e_const", 0.0),
    ("purge_f", 0.0), ("travel_f", 0.0), ("seam_trim_length", -1.0),
    ("anchor_length", -5.0), ("pump_stall_floor", -1.0),
])
def test_rejects_values_that_crash_or_emit_unsafe_gcode(key, value):
    with pytest.raises(ValueError):
        _resolve(base_2d_config(**{key: value}))


def test_flat_specialty_requires_positive_z_pull_speed():
    cfg = base_2d_config(shape="3D Overhang (Mushroom)", v_z=0.0, accel=0.0, decel=0.0)
    with pytest.raises(ValueError, match="Z Pull Speed"):
        _resolve(cfg)


def test_flat_specialty_skips_cornering_planner_checks():
    """Mushroom never runs the speed planner, and the sidebar zeroes its
    fields for it -- that must not be rejected."""
    cfg = base_2d_config(shape="3D Overhang (Mushroom)", v_z=2.0, v_overhang=5.0, dwell=1.5,
                         accel=0.0, decel=0.0, v_min=0.0)
    _resolve(cfg)


# --- Phase 38: fibre over holes, Z drops, ramps over empty space ------------------

def _pt(x, y, z, layer=1, **kw):
    return dict(x=x, y=y, z=z, dist=1.0, v=5.0, e_mult=1.0, layer=layer, **kw)


def _plate_with_hole():
    from shapely.geometry import Point, box
    part = box(0, 0, 40, 30).difference(Point(20, 15).buffer(4))
    return part.buffer(-0.6)          # target shapes are the wall centreline (gap/2 inside)


def test_fibre_over_a_hole_is_an_error():
    from cfrp_core import ProcessConfig
    from cfrp_core.validation import validate_trajectory
    traj = [_pt(x, 15.0, 0.2) for x in range(5, 36)]
    res = validate_trajectory(traj, ProcessConfig(), target_shapes=[(0.2, _plate_with_hole(), 1)])
    assert res["status"] == "Fail" and any("Fibre over a hole" in m for m in res["messages"])
    clear = [_pt(x, 3.0, 0.2) for x in range(5, 36)]
    assert not any("hole" in m for m in validate_trajectory(clear, ProcessConfig(),
                                                             target_shapes=[(0.2, _plate_with_hole(), 1)])["messages"])


def test_z_going_down_is_an_error():
    from cfrp_core import ProcessConfig
    from cfrp_core.validation import validate_trajectory
    traj = [_pt(x, 5.0, 0.4) for x in range(5, 20)] + [_pt(x, 6.0, 0.2) for x in range(20, 30)]
    res = validate_trajectory(traj, ProcessConfig())
    assert any(m.startswith("Z goes down") for m in res["messages"])


def test_ramp_over_a_hole_is_an_error():
    from cfrp_core import ProcessConfig
    from cfrp_core.validation import validate_trajectory
    shape = _plate_with_hole()
    traj = [_pt(x, 3.0, 0.2, 1) for x in range(5, 16)]
    traj += [_pt(16 + k * 0.5, 3.0 + k * 1.5, 0.2 + k * 0.02, 2, is_transition=True) for k in range(1, 11)]
    traj += [_pt(x, 18.0, 0.4, 2) for x in range(21, 36)]
    res = validate_trajectory(traj, ProcessConfig(), target_shapes=[(0.2, shape, 1), (0.4, shape, 2)])
    assert any("Layer climb over empty space" in m for m in res["messages"])
