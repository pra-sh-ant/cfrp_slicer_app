"""Qt front end, run offscreen: option greying, settings search, params."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def panel(app):
    from gui.controls_panel import ControlsPanel
    return ControlsPanel()


def _enabled(panel, key):
    lbl, widget, _ = panel._rows[key]
    return widget.isEnabled()


def test_flat_rectangle_greys_out_shell_and_mushroom_settings(panel):
    assert _enabled(panel, "width") and _enabled(panel, "height")
    for key in ("base_radius", "disc_radius", "stl_scale", "v_z", "seam_stagger"):
        assert not _enabled(panel, key), key
    # Archimedean has no fibre angle; one layer has no transitions.
    assert not _enabled(panel, "layup_seq")
    assert not _enabled(panel, "transition_angle")
    lbl, widget, _ = panel._rows["layup_seq"]
    assert "Not used here" in widget.toolTip()


def test_revolve_greys_out_zigzags_in_the_pattern_list(panel):
    panel.shape_cb.setCurrentText("Hollow Cylinder (Z)")
    model = panel.infill_cb.model()
    enabled = {panel.infill_cb.itemText(i): model.item(i).isEnabled() for i in range(panel.infill_cb.count())}
    assert enabled["Archimedean (Concentric Spiral)"]
    assert not enabled["Oriented Serpentine"] and not enabled["Corner-Step Spiral"]
    assert not _enabled(panel, "layers") and _enabled(panel, "tracks_per_layer")
    assert _enabled(panel, "seam_stagger")


def test_unavailable_current_pattern_is_replaced(panel):
    panel.infill_cb.setCurrentText("Oriented Serpentine")
    panel.shape_cb.setCurrentText("Hollow Cylinder (Z)")
    assert panel.infill_cb.currentText() == "Archimedean (Concentric Spiral)"


def test_zigzag_greys_out_tracks_and_shows_a_caution(panel):
    panel.infill_cb.setCurrentText("Oriented Serpentine")
    assert not _enabled(panel, "tracks_per_layer")
    assert _enabled(panel, "layup_seq")
    assert not panel.infill_hint.isHidden() and "U-turns" in panel.infill_hint.text()


def test_fill_solid_greys_out_track_count(panel):
    panel.fill_chk.setChecked(True)
    assert not _enabled(panel, "tracks_per_layer")
    assert panel.get_params()["fill_solid"] is True


def test_mushroom_keeps_only_its_own_speeds(panel):
    panel.shape_cb.setCurrentText("3D Overhang (Mushroom)")
    for key in ("v_z", "v_overhang", "dwell", "disc_radius", "stem_top", "v_max"):
        assert _enabled(panel, key), key
    for key in ("accel", "infill_type", "tracks_per_layer", "layers"):
        assert not _enabled(panel, key), key
    assert panel.get_params()["infill_type"] == "N/A"


def test_section_headers_count_unused_settings(panel):
    titles = {sec.title: sec._header.text() for sec in panel._sections}
    assert "not used here" in titles["Part"]


def test_search_filters_rows(panel):
    panel._search.setText("radius")
    form_rows = {key: sec.form.isRowVisible(lbl) for key, (lbl, _, sec) in panel._rows.items()}
    assert form_rows["min_radius"] and form_rows["base_radius"]
    assert not form_rows["purge_f"]
    panel._search.setText("")
    assert all(sec.form.isRowVisible(lbl) for lbl, _, sec in panel._rows.values())


def test_params_round_trip(panel):
    p = panel.get_params()
    p.update(shape="Triangle", width=80.0, layers=3, fill_solid=True, layup_seq="0, 90")
    panel.load_params(p)
    q = panel.get_params()
    for key in ("shape", "width", "layers", "fill_solid", "layup_seq"):
        assert q[key] == p[key], key


# --- results panel / viewer (Phase 30-31) ---------------------------------------

def test_issue_messages_name_their_layers():
    from gui.results_panel import layers_in
    assert layers_in("Layer 21 path crosses itself (7 internal intersections).") == [21]
    assert layers_in("Curvature: 5 point(s) ... on layer(s) [2, 4, 6]; these") == [2, 4, 6]
    assert layers_in("All good") == []


def test_issue_click_requests_its_layer(app):
    from gui.results_panel import IssuesPanel
    panel = IssuesPanel()
    got = []
    panel.layer_requested.connect(got.append)
    panel.set_result({"status": "Warning", "messages": ["Layer 7 path crosses itself."]}, ("a note",))
    panel._clicked(panel._list.item(0))
    assert got == [7]
    assert panel._list.count() == 2


def test_viewer_builds_ranges_and_modes(app):
    from cfrp_core import ProcessConfig, slice_part
    from cfrp_core.config import GeometryConfig
    from cfrp_core.machine import DEFAULT_MACHINE
    from gui.toolpath_mesh import MODES
    from gui.viewer_gl import ViewerGL
    r = slice_part(ProcessConfig(layers=3, geometry=GeometryConfig(width_mm=50, height_mm=25)))
    v = ViewerGL(DEFAULT_MACHINE)
    v.update_scene(r.trajectory, r.target_shapes, r.max_z, 1.2, 0.2, 3.0)
    assert v.range.values() == (1, 3)
    v.show_layers(2)
    a, b, top = v._point_range()
    assert 0 < a < b and top == a
    for mode in MODES:
        v.mode_cb.setCurrentText(mode)
    v._draw_to(a + 5)
    assert v.info.text().startswith("L2")
    v.clear()
