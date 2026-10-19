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


# --- structural PCB editor (Phase 33) --------------------------------------------

@pytest.fixture
def editor(app):
    from gui.pcb_editor import PcbEditor
    return PcbEditor()


def test_editor_starts_with_a_clean_example(editor):
    assert editor.last_drc["status"] == "Pass"
    assert editor.design.traces and editor.design.holes


def test_draw_trace_undo_redo(editor):
    from PySide6.QtCore import Qt
    n = len(editor.design.traces)
    editor.set_tool("trace")
    editor.net_edit.setText("SIG")
    for x, y in ((25, 33), (35, 33), (35, 36)):
        editor._press(x, y, Qt.LeftButton)
    editor._finish()
    assert len(editor.design.traces) == n + 1
    assert editor.design.traces[-1].net == "SIG" and len(editor.design.traces[-1].points) == 3
    editor.undo()
    assert len(editor.design.traces) == n
    editor.redo()
    assert len(editor.design.traces) == n + 1


def test_outline_hole_pad_footprint_and_delete(editor):
    from PySide6.QtCore import Qt
    editor.new_design()
    editor.set_tool("rect")
    editor._press(0, 0, Qt.LeftButton)
    editor._release(80, 50)
    assert editor.design.outline[0] == (0.0, 0.0) and len(editor.design.outline) == 4
    editor.set_tool("hole")
    editor._press(6, 6, Qt.LeftButton)
    editor.set_tool("pad")
    editor._press(40, 25, Qt.LeftButton)
    editor.set_tool("footprint")
    editor.fp_combo.setCurrentText("SOT-23")
    editor._press(60, 25, Qt.LeftButton)
    assert len(editor.design.holes) == 1 and len(editor.design.pads) == 4
    editor.set_tool("select")
    editor._press(40, 25, Qt.LeftButton)
    assert editor._sel == ("pad", 0)
    editor._release(45, 30)                       # drag moves it
    assert (editor.design.pads[0].x, editor.design.pads[0].y) == (45.0, 30.0)
    editor.delete_selected()
    assert len(editor.design.pads) == 3


def test_editor_drc_flags_a_short(editor):
    from PySide6.QtCore import Qt
    editor.set_tool("trace")
    editor.net_edit.setText("OTHER")
    editor._press(36, 24, Qt.LeftButton)
    editor._press(36, 34, Qt.LeftButton)          # crosses the LED trace at y=28
    editor._finish()
    assert editor.last_drc["status"] == "Fail"
    assert any(m.startswith("Short") for m in editor.last_drc["messages"])


def test_editor_save_load_and_slice_request(editor, tmp_path):
    path = tmp_path / "b.spcb.json"
    editor.save_design_as(str(path))
    design = editor.design
    editor.new_design()
    editor.open_design(str(path))
    assert editor.design == design
    got = []
    editor.slice_requested.connect(lambda d, c, m: got.append((d, c, m)))
    editor._request_slice()
    assert got and got[0][0] == design and got[0][1].line_width_mm == editor.d_lw.value()
    assert got[0][2] == "structural"


def test_editor_greys_out_diw_settings_that_do_not_apply(editor):
    assert not editor.d_epm.isEnabled() and editor.d_on.isEnabled()        # pneumatic
    editor.d_mode.setCurrentText("volumetric")
    assert editor.d_epm.isEnabled() and not editor.d_on.isEnabled()
    assert "Not used here" in editor.d_on.toolTip()
    assert not editor.d_surface.isEnabled()
    editor.mode_cb.setCurrentIndex(2)                                      # electronics only
    assert editor.d_surface.isEnabled() and editor.print_mode() == "electronics"
    cfg = editor.diw_config()
    assert cfg.dispense == "volumetric" and cfg.tool_select == "T1"


def test_every_setting_has_plain_help(panel):
    from gui.settings_help import DIW_HELP, HELP
    for key in panel._rows:
        assert len(HELP.get(key, "")) > 20, key
    assert all(len(v) > 20 for v in DIW_HELP.values())
