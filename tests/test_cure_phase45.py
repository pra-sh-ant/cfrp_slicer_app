"""Phase 45: the UV cure speed cap -- no tow faster than the resin's cure dose
allows under the head's LED (irradiance x spot length / dose)."""
import pytest

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.constraints import cure_speed
from cfrp_core.validation import validate_trajectory

CURE = dict(cure_dose=250.0, uv_irradiance=400.0, uv_spot=5.0)      # 400 x 5 / 250 = 8 mm/s


def test_cure_speed_is_irradiance_times_spot_over_dose():
    assert cure_speed(from_legacy_dict(CURE)) == pytest.approx(8.0)
    assert cure_speed(from_legacy_dict({})) is None
    assert cure_speed(from_legacy_dict(dict(CURE, uv_spot=0.0))) is None
    with pytest.raises(ValueError, match="Cure Dose"):
        from_legacy_dict(dict(CURE, cure_dose=-1.0))


@pytest.mark.parametrize("setup", [
    dict(shape="Square/Rectangle", width=40.0, height=25.0, layers=2, infill_type="Oriented Serpentine",
         fill_solid=True, strict_bend_radius=True),          # zigzag + strict + a capped speed (crashed once)
    dict(shape="Hollow Cylinder (Z)", base_radius=12.0, tracks_per_layer=2),
    dict(shape="3D Overhang (Mushroom)", infill_type="N/A", stem_height=8.0),
])
def test_every_point_keeps_under_the_cure_speed(setup):
    res = slice_part(from_legacy_dict(dict(setup, **CURE)))
    assert max(p["v"] for p in res.trajectory) <= 8.0 + 1e-9
    assert any("by the UV cure" in n for n in res.constraint_notes)
    assert not any(m.startswith("UV cure") for m in res.validation["messages"])


def test_a_slow_cure_also_caps_the_bend_speed():
    res = slice_part(from_legacy_dict(dict(CURE, uv_irradiance=200.0)))     # 4 mm/s, under v_min 5
    assert max(p["v"] for p in res.trajectory) <= 4.0 + 1e-9
    assert any(n.startswith("Min Bend Speed limited") for n in res.constraint_notes)


def test_part_of_the_settings_is_noted_not_used():
    res = slice_part(from_legacy_dict(dict(cure_dose=250.0)))
    assert max(p["v"] for p in res.trajectory) == pytest.approx(10.0)
    assert any(n.startswith("UV cure speed cap off") for n in res.constraint_notes)


def test_audit_fails_points_faster_than_the_cure():
    cfg = from_legacy_dict(CURE)
    traj = [dict(x=50.0 + k, y=50.0, z=0.2, v=v, layer=1) for k, v in enumerate((5.0, 8.0, 9.5, 6.0))]
    msgs = validate_trajectory(traj, cfg)["messages"]
    assert any(m.startswith("UV cure: 1 point(s) on layer(s) [1]") for m in msgs), msgs


def test_cure_settings_ride_with_their_profiles():
    from cfrp_core.profiles import KINDS
    assert "cure_dose" in KINDS["material"]
    assert {"uv_irradiance", "uv_spot"} <= set(KINDS["machine"])
