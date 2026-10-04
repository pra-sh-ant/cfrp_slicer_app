"""Tests for the flat-speed specialty generators (Torus, Conical Dome, Mushroom)."""
import pytest

from cfrp_core import ConfigError, from_legacy_dict
from cfrp_core.gcode import compile_gcode
from cfrp_core.specialty import overhang_test
from tests.helpers import base_revolve_config


def _mushroom(**overrides):
    cfg = base_revolve_config(
        shape="3D Overhang (Mushroom)", base_radius=10.0, disc_radius=15.0,
        stem_height=25.0, v_z=2.0, v_overhang=5.0, dwell=1.5,
    )
    cfg.update(overrides)
    return from_legacy_dict(cfg)


def test_mushroom_base_barely_wider_than_bend_radius_does_not_crash():
    """Regression: spiral step count rounded down to 0 -> ZeroDivisionError."""
    traj = overhang_test(_mushroom(base_radius=2.01, disc_radius=2.01, min_radius=2.0))
    assert traj


def test_mushroom_gcode_emits_cure_dwell():
    cfg = _mushroom(layer_height=0.2, min_radius=2.0)
    traj = overhang_test(cfg)
    gcode = compile_gcode(traj, cfg)
    assert "G4 P1500 ; === UV DWELL TO CURE STEM ===" in gcode


def test_unknown_shape_is_rejected_not_silently_substituted():
    """An unknown shape can't even be configured, so it can never fall
    through to another generator."""
    with pytest.raises(ConfigError):
        from_legacy_dict(base_revolve_config(shape="Not A Shape"))
