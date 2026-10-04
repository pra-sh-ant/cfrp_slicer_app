"""Tests for the flat-speed specialty generators (Torus, Conical Dome, Mushroom)."""
import pytest

from gcode import GCodeCompiler
from specialty import SpecialtyEngine
from tests.helpers import base_revolve_config


def _mushroom(**overrides):
    cfg = base_revolve_config(
        shape="3D Overhang (Mushroom)", base_radius=10.0, disc_radius=15.0,
        stem_height=25.0, v_z=2.0, v_overhang=5.0, dwell=1.5,
    )
    cfg.update(overrides)
    return cfg


def test_mushroom_base_barely_wider_than_bend_radius_does_not_crash():
    """Regression: spiral step count rounded down to 0 -> ZeroDivisionError."""
    traj = SpecialtyEngine.generate_overhang_test(_mushroom(base_radius=2.01, disc_radius=2.01, min_radius=2.0))
    assert traj


def test_mushroom_gcode_emits_cure_dwell():
    cfg = _mushroom(layer_height=0.2, min_radius=2.0)
    traj = SpecialtyEngine.generate_overhang_test(cfg)
    gcode = GCodeCompiler.compile(traj, cfg)
    assert "G4 P1500 ; === UV DWELL TO CURE STEM ===" in gcode


def test_unknown_specialty_shape_is_rejected_not_silently_substituted():
    with pytest.raises(ValueError):
        SpecialtyEngine.generate_specialty_shape(base_revolve_config(shape="Not A Shape"))
