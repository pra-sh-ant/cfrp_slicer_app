"""End-to-end UI wiring tests via Streamlit's AppTest.

These simulate real widget interaction (not just importing the module), so
they catch the class of bug the pipeline-level tests can't: a feature that
works in the pipeline function but is unreachable or misconfigured through
the actual sidebar (e.g. tracks_per_layer hardcoded to 1 for 3D shapes).
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

ALL_SHAPES = [
    "Square/Rectangle", "Triangle", "Hexagon", "Circle", "L-Bracket",
    "Serpentine Infill", "Hollow Cylinder (Z)", "Hollow Square Tube (Z)",
    "Hollow Sphere (Z)", "Dome (Z)", "Tapered Cylinder (Z)", "Torus (Z)",
    "Conical Dome (Z)", "3D Overhang (Mushroom)",
]


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_every_shape_selects_and_slices_without_exception(shape):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    assert not at.exception, f"initial run failed: {at.exception}"

    at.selectbox(key="shape_select").select(shape).run(timeout=60)
    assert not at.exception, f"selecting {shape!r} raised: {at.exception}"


def test_revolve_shape_exposes_tracks_per_layer_and_full_kinematics():
    """Regression: render_layer_strategy hardcoded tracks_per_layer=1 and
    render_kinematics_section zeroed v_min/accel/decel/p_advance/anchor for
    every is_3d shape, making the revolve pipeline's multi-track wall and
    cornering-speed planning unreachable from the actual sidebar."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    at.selectbox(key="shape_select").select("Hollow Sphere (Z)").run(timeout=60)
    assert not at.exception

    labels = {w.label for w in at.number_input}
    assert "Tracks Per Layer (Wall Thickness)" in labels
    assert "Min Bend Speed (mm/s)" in labels
    assert "Acceleration" in labels
    assert "Deceleration" in labels
    assert "Lead-in Anchor Length (mm)" in labels
    # Height is derived (2x radius) for a sphere, not independently settable.
    assert "Height (Z mm)" not in labels
    assert "Wall Thickness (mm)" not in labels


def test_flat_specialty_shape_keeps_simplified_kinematics():
    """Torus/Conical Dome/Mushroom are not part of the revolve pipeline and
    should keep the flat-speed kinematics panel, not the full one."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    at.selectbox(key="shape_select").select("Torus (Z)").run(timeout=60)
    assert not at.exception

    labels = {w.label for w in at.number_input}
    assert "Tracks Per Layer (Wall Thickness)" not in labels
    assert "Min Bend Speed (mm/s)" not in labels


def test_sidebar_only_shows_controls_used_by_the_selected_shape():
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    assert not at.exception
    assert not any("Research Preset" in widget.label for widget in at.selectbox)

    at.selectbox(key="shape_select").select("Serpentine Infill").run(timeout=60)
    labels = {widget.label for widget in at.number_input}
    assert "Tracks Per Layer" not in labels
    assert "Print Outer Boundary" not in {widget.label for widget in at.checkbox}

    at.selectbox(key="shape_select").select("Torus (Z)").run(timeout=60)
    labels = {widget.label for widget in at.number_input}
    assert "Height (Z mm)" in labels
    assert "Layer Transition Angle (deg)" not in labels
    assert "Min Bend Speed (mm/s)" not in labels
    assert "Top Disc Overhang Speed (mm/s)" not in labels

    at.selectbox(key="shape_select").select("3D Overhang (Mushroom)").run(timeout=60)
    labels = {widget.label for widget in at.number_input}
    assert "Top Disc Overhang Speed (mm/s)" in labels
    assert "Top Dwell (sec)" in labels
    assert "Tube Radius (mm)" not in labels


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_every_shape_parameter_can_be_tuned(shape):
    """Change every rendered parameter once, then verify the shape still slices."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    at.selectbox(key="shape_select").select(shape).run(timeout=60)
    assert not at.exception

    for widget in at.number_input:
        value = widget.value
        if isinstance(value, int):
            widget.set_value(value + 1)
        else:
            widget.set_value(float(value) + 0.1)
    for widget in at.checkbox:
        if widget.label != "Auto-Slice":
            widget.set_value(not widget.value)
    for widget in at.text_input:
        widget.set_value("0, 90")
    at.run(timeout=60)
    assert not at.exception, f"tuning {shape!r} raised: {at.exception}"


def test_changing_tracks_per_layer_reslices_without_exception():
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    at.selectbox(key="shape_select").select("Hollow Sphere (Z)").run(timeout=60)
    tracks_widget = next(w for w in at.number_input if w.label == "Tracks Per Layer (Wall Thickness)")
    tracks_widget.set_value(6).run(timeout=60)
    assert not at.exception


def test_default_part_reports_known_track_count_problem():
    """Pins a known issue (docs/ARCHITECTURE.md): the legacy default part
    (135 x 25 mm, 10 tracks, 1.2 mm gap, 3 mm radius) does not fit, so the
    app opens on an error. Update this test when the defaults are changed."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    assert not at.exception
    assert any("only 7 fit" in e.value for e in at.error)
