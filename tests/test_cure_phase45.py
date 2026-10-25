"""Phase 45: curing in the path -- the tow must pass the head's UV LED slowly
enough for the resin to get its dose, so every speed is capped at
irradiance x spot length / dose."""
import pytest

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.capabilities import field_states

PLATE = dict(shape="Square/Rectangle", width=60.0, height=30.0, layers=2, layup_seq="0, 90", v_max=10.0, v_min=5.0)


def test_speeds_are_capped_for_the_dose():
    res = slice_part(from_legacy_dict(dict(PLATE, uv_dose=300.0, uv_irradiance=600.0, uv_spot=3.0)))
    cap = 600.0 * 3.0 / 300.0                    # 6 mm/s
    assert max(p["v"] for p in res.trajectory) <= cap + 1e-9
    note = next(n for n in res.constraint_notes if n.startswith("UV cure:"))
    assert "Print Speed (Max)" in note and "6.00 mm/s" in note and "300 mJ/cm²" in note
    # the minimum speeds are capped too when they would exceed it
    res = slice_part(from_legacy_dict(dict(PLATE, uv_dose=1000.0, uv_irradiance=600.0, uv_spot=3.0)))
    assert max(p["v"] for p in res.trajectory) <= 1.8 + 1e-9
    assert "Min Bend Speed" in next(n for n in res.constraint_notes if n.startswith("UV cure:"))


def test_no_cap_without_the_led():
    plain = slice_part(from_legacy_dict(PLATE))
    res = slice_part(from_legacy_dict(dict(PLATE, uv_dose=300.0)))
    assert [p["v"] for p in res.trajectory] == [p["v"] for p in plain.trajectory]
    assert any(n.startswith("Cure Dose is set but") for n in res.constraint_notes)
    # a cap above every speed changes nothing
    fast = slice_part(from_legacy_dict(dict(PLATE, uv_dose=10.0, uv_irradiance=600.0, uv_spot=3.0)))
    assert [p["v"] for p in fast.trajectory] == [p["v"] for p in plain.trajectory]
    assert not any(n.startswith("UV cure:") for n in fast.constraint_notes)


def test_cure_settings():
    with pytest.raises(Exception, match="Cure Dose"):
        from_legacy_dict(dict(PLATE, uv_dose=-1.0))
    s = field_states(PLATE)
    assert s["uv_dose"].enabled and not s["uv_irradiance"].enabled and not s["uv_spot"].enabled
    s = field_states(dict(PLATE, uv_dose=300.0))
    assert s["uv_irradiance"].enabled and s["uv_spot"].enabled
