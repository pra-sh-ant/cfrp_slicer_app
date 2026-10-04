"""Tests for resolve_process_constraints and ValidationEngine."""
import pytest

from tests.helpers import base_2d_config
from validation import ValidationEngine, resolve_process_constraints


def test_rejects_zero_track_gap():
    with pytest.raises(ValueError):
        resolve_process_constraints(base_2d_config(track_gap=0.0))


def test_rejects_negative_min_radius():
    with pytest.raises(ValueError):
        resolve_process_constraints(base_2d_config(min_radius=-1.0))


def test_rejects_tracks_below_one():
    with pytest.raises(ValueError):
        resolve_process_constraints(base_2d_config(tracks_per_layer=0))


def test_rejects_zero_print_speed():
    with pytest.raises(ValueError):
        resolve_process_constraints(base_2d_config(v_max=0.0))


def test_rejects_geometrically_impossible_narrow_part():
    """Width/height must leave room for two bend radii plus one track gap."""
    cfg = base_2d_config(shape="Square/Rectangle", width=100.0, height=4.0, min_radius=3.0, track_gap=2.0)
    with pytest.raises(ValueError):
        resolve_process_constraints(cfg)


def test_serpentine_gap_auto_raised_to_fit_uturn():
    cfg = base_2d_config(shape="Serpentine Infill", width=100.0, height=25.0, track_gap=1.0, min_radius=3.0)
    resolved = resolve_process_constraints(cfg)
    assert resolved["track_gap"] >= 2.0 * 3.0
    assert resolved["constraint_notes"]


def test_min_bend_speed_clamped_to_print_speed():
    cfg = base_2d_config(v_max=5.0, v_min=50.0)
    resolved = resolve_process_constraints(cfg)
    assert resolved["v_min"] == 5.0


def test_valid_config_resolves_without_error():
    resolved = resolve_process_constraints(base_2d_config())
    assert resolved["track_gap"] > 0
    assert resolved["tracks_per_layer"] >= 1


def test_empty_trajectory_reports_fail():
    result = ValidationEngine.validate_trajectory([], base_2d_config())
    assert result["status"] == "Fail"


@pytest.mark.parametrize("key, value", [
    ("accel", 0.0), ("decel", -1.0), ("layer_height", 0.0), ("e_const", 0.0),
    ("purge_f", 0.0), ("travel_f", 0.0), ("seam_trim_length", -1.0),
    ("anchor_length", -5.0), ("pump_stall_floor", -1.0),
])
def test_rejects_values_that_crash_or_emit_unsafe_gcode(key, value):
    with pytest.raises(ValueError):
        resolve_process_constraints(base_2d_config(**{key: value}))


def test_flat_specialty_requires_positive_z_pull_speed():
    cfg = base_2d_config(shape="Torus (Z)", v_z=0.0, accel=0.0, decel=0.0)
    with pytest.raises(ValueError, match="Z Pull Speed"):
        resolve_process_constraints(cfg)


def test_flat_specialty_skips_cornering_planner_checks():
    """Torus/Conical Dome/Mushroom never run the speed planner, and the
    sidebar zeroes its fields for them -- that must not be rejected."""
    cfg = base_2d_config(shape="Conical Dome (Z)", v_z=2.0, accel=0.0, decel=0.0, v_min=0.0)
    resolve_process_constraints(cfg)
