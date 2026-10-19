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


def test_worker_prepares_the_view_off_the_ui_thread(app):
    from cfrp_core.machine import DEFAULT_MACHINE
    from gui.main_window import _DEFAULTS
    from gui.slicer_worker import SlicerWorker
    from gui.toolpath_mesh import Prepared
    from gui.viewer_gl import MAX_DRAW_POINTS, ViewerGL
    v = ViewerGL(DEFAULT_MACHINE)
    got = []
    w = SlicerWorker(dict(_DEFAULTS), [0.0], DEFAULT_MACHINE, view=(MAX_DRAW_POINTS, v.wants_core()))
    w.finished.connect(got.append)
    w.run()                                       # synchronously, for the test
    res = got[0]
    pre = res.prepared_view
    assert isinstance(pre, Prepared) and pre.tube is not None and len(pre.data) <= len(res.trajectory)
    v.update_scene(res.trajectory, res.target_shapes, res.max_z, prepared=pre)
    assert v._data is pre.data
    if v._view is not None:
        assert v._mesh is pre.tube                # built by the worker, not again here
        v.mode_cb.setCurrentText("Speed")         # another display mode builds its own
        assert v._mesh is not pre.tube or not pre.core_mode


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


def test_easy_mode_drop_rotate_move_and_wire(editor):
    from PySide6.QtCore import Qt
    assert not editor.advanced and not editor.side_tabs.isTabVisible(2) and editor.side_tabs.isTabVisible(1)
    editor.new_design()
    editor.e_w.setValue(70)
    editor.e_h.setValue(45)
    editor.make_board()
    assert max(x for x, _ in editor.design.outline) == pytest.approx(70)
    editor._drop("resistor", 20, 20)                  # drag-and-drop from the parts list
    assert editor._sel == ("part", "R1")
    editor._key(Qt.Key_R)                              # R rotates 90°
    assert all(p.angle_deg == 90 for p in editor.design.pads)
    editor._press(20, 21, Qt.LeftButton)               # grab the part ...
    editor._move(30, 26, True)                         # ... live preview while dragging
    assert editor._preview is not None
    editor._release(30, 26)
    assert editor._preview is None
    from cfrp_core.diw import edit as ed
    assert ed.centre(editor.design, ("part", "R1")) == pytest.approx((30.0, 25.0))
    editor._drop("led", 50, 25)
    editor.set_tool("trace")
    editor._press(30, 26.5, Qt.LeftButton)
    editor._press(48.5, 25, Qt.LeftButton)
    editor._finish()
    assert editor.design.traces[-1].net == "N1" and editor.design.traces[-1].width_mm == 1.0
    editor.set_advanced(True)
    assert editor.side_tabs.isTabVisible(2)
    assert sum(b.isChecked() for b in editor._tool_btns.buttons()) == 1


def test_editor_multi_select_copy_paste_and_vertices(editor, tmp_path):
    from PySide6.QtCore import Qt

    editor.set_tool("select")
    editor._press(24, 24, Qt.LeftButton)            # empty board: start a selection rectangle
    editor._move(48, 31, True)
    editor._release(48, 31)
    assert ("part", "R1") in editor._sels and ("part", "D1") in editor._sels
    n = len(editor.design.pads)
    editor._key(Qt.Key_C, Qt.ControlModifier)
    editor._key(Qt.Key_V, Qt.ControlModifier)
    assert len(editor.design.pads) == n + 4 and ("part", "R2") in editor._sels
    editor.delete_selected()
    assert len(editor.design.pads) == n
    # drag a wire bend
    editor._sels = [("trace", 0)]
    editor.redraw()
    editor._press(12, 28, Qt.LeftButton)            # the bend handle at (12, 28)
    editor._move(14, 30, True)
    editor._release(14, 30)
    assert editor.design.traces[0].points[1] == (14, 30)
    editor._double(20, 29.2)                         # double-click on the segment adds a bend
    assert len(editor.design.traces[0].points) == 4
    editor._press(20, 29, Qt.RightButton)            # right-click on it removes it again
    assert len(editor.design.traces[0].points) == 3
    # board corner
    editor._press(60, 20, Qt.LeftButton)            # on the board edge
    assert editor._sels == [("outline", None)]
    editor._release(60, 20)
    editor._press(60, 40, Qt.LeftButton)
    editor._move(64, 44, True)
    editor._release(64, 44)
    assert (64.0, 44.0) in editor.design.outline


def test_editor_auto_route_import_and_presets(editor, tmp_path):
    from cfrp_core.diw.io import export_dxf
    from cfrp_core.diw.router import ratsnest
    editor.commit(editor.design.model_copy(update=dict(traces=())))
    assert ratsnest(editor.design)
    rep = editor.auto_route()
    assert rep["failed"] == [] and not ratsnest(editor.design)
    export_dxf(editor.design, tmp_path / "b.dxf")
    editor.new_design()
    editor.import_dxf_dialog(str(tmp_path / "b.dxf"))
    assert len(editor.design.holes) == 4
    (tmp_path / "n.csv").write_text("ref,pin,net\nJ1,1,GND\n")
    editor.load_example()
    editor.import_netlist_dialog(str(tmp_path / "n.csv"))
    assert "1 pad(s)" in editor.hint.text()
    editor.set_advanced(True)
    editor.d_ink_preset.setCurrentText("Liquid metal (EGaIn)")
    cfg = editor.diw_config()
    assert cfg.encapsulate and cfg.resistivity_ohm_mm == pytest.approx(2.94e-4)
    editor.mode_cb.setCurrentIndex(3)
    editor.set_advanced(False)
    assert editor.print_mode() == "embedded"


def test_editor_crossover_wire_level_current_and_pin1(editor):
    from PySide6.QtCore import Qt
    editor.set_advanced(True)
    editor.set_tool("trace")
    editor.net_edit.setText("OTHER")
    editor.level2_chk.setChecked(True)
    editor._press(36, 24, Qt.LeftButton)
    editor._press(36, 34, Qt.LeftButton)          # crosses the LED wire, now on level 2
    editor._finish()
    assert editor.design.traces[-1].level == 2
    assert not any(m.startswith("Short") for m in editor.last_drc["messages"])
    assert any("crossover" in n for n in editor.last_drc["notes"])
    assert editor.d_bridge_n.isEnabled()
    # the wire's properties: level and a current to check
    editor._sel = ("trace", len(editor.design.traces) - 1)
    editor._show_props()
    labels = [editor.prop_form.itemAt(i, editor.prop_form.ItemRole.LabelRole) for i in range(editor.prop_form.rowCount())]
    names = {w.widget().text() for w in labels if w is not None and w.widget() is not None}
    assert {"Level", "Current", "Carries up to"} <= names
    cfg = editor.diw_config()
    assert cfg.bridge_layers == editor.d_bridge_n.value() and cfg.spread_factor == editor.d_spread.value()
    editor.level2_chk.setChecked(False)


def test_failed_slice_marks_the_setting_it_names(panel):
    keys = panel.mark_problems("Layer Height must be greater than 0 mm (got 0).")
    assert keys == ["layer_height"]                       # not "height": the longer label wins
    lbl, w, sec = panel._rows["layer_height"]
    assert "f87171" in lbl.styleSheet() and "Problem:" in w.toolTip() and sec.is_open()
    assert panel.mark_problems("Track Gap must be greater than 0 mm.\nMin Bend Radius cannot be negative.") == \
        ["min_radius", "track_gap"]
    assert "f87171" not in panel._rows["layer_height"][0].styleSheet()      # the earlier marks are cleared
    assert panel.mark_problems("geometry.width_mm: Input should be a valid number") == ["width"]
    panel.clear_problems()
    assert "f87171" not in panel._rows["width"][1].styleSheet()
    assert panel.mark_problems("Something else went wrong.") == []


# --- Phase 40: settings undo, ignored keys, projects, profiles ------------------------

@pytest.fixture
def win(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    import gui.main_window as mw
    monkeypatch.setattr(mw, "QSettings", lambda *a: QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    monkeypatch.setenv("CFRP_PROFILE_DIR", str(tmp_path / "profiles"))
    w = mw.MainWindow()
    w._quiet = True
    w._act_auto.setChecked(False)
    yield w
    w._timer.stop()
    w._retire_worker()
    for r in w._retired:
        r.wait(5000)


def test_settings_undo_and_redo(panel):
    gap0 = panel.gap_spin.value()
    panel.gap_spin.setValue(gap0 + 0.3)
    panel.layers_spin.setValue(4)
    assert panel.undo_settings()                       # the pending change is recorded first
    assert panel.gap_spin.value() == pytest.approx(gap0) and panel.layers_spin.value() == 1
    assert panel.redo_settings()
    assert panel.gap_spin.value() == pytest.approx(gap0 + 0.3) and panel.layers_spin.value() == 4
    assert not panel.redo_settings()
    assert panel.undo_settings() and not panel.undo_settings()


def test_loading_reports_settings_this_version_does_not_use(panel):
    ignored = panel.load_params({"track_gap": 1.4, "layers": 2, "warp_drive": True, "old_knob": 3})
    assert ignored == ["old_knob", "warp_drive"]
    assert panel.gap_spin.value() == pytest.approx(1.4)
    assert panel.load_params(panel.get_params()) == []                   # what we write, we read


def test_project_saves_and_opens_everything(win, tmp_path):
    from cfrp_core.diw import DiwConfig, Trace
    win._controls.gap_spin.setValue(1.5)
    win._controls.bed_x_spin.setValue(300.0)
    d = win._editor.design
    win._editor.commit(d.model_copy(update=dict(traces=d.traces + (
        Trace(points=((36, 22), (36, 34)), width_mm=1.0, net="X", level=2),))))
    win._editor.load_diw_config(DiwConfig(layer_wait_s=9.0, spread_factor=1.2))
    win._editor.set_print_mode("embedded")
    path = win.save_project(str(tmp_path / "job"))
    assert path.endswith(".cfrpproj")
    design = win._editor.design
    win._reset_params()
    win._editor.new_design()
    win._editor.load_diw_config(DiwConfig())
    win._editor.set_print_mode("structural")
    win._open_project(path)
    assert win._controls.gap_spin.value() == pytest.approx(1.5)
    assert win._controls.bed_x_spin.value() == pytest.approx(300.0)
    assert win._editor.design == design
    assert win._editor.diw_config() == DiwConfig(layer_wait_s=9.0, spread_factor=1.2)
    assert win._editor.print_mode() == "embedded"
    assert win._recent_paths()[0] == path


def test_profiles_from_the_panel(win, tmp_path):
    c = win._controls
    c.bed_x_spin.setValue(250.0)
    c.machine_profile.save("Big bed")
    c.bed_x_spin.setValue(180.0)
    c.gap_spin.setValue(1.3)
    assert c.machine_profile.combo.findText("Big bed") > 0
    c.machine_profile.load("Big bed")
    assert c.bed_x_spin.value() == pytest.approx(250.0)
    assert c.gap_spin.value() == pytest.approx(1.3)                       # a machine profile leaves the part alone
    c.material_profile.save("CF 1k")
    assert (tmp_path / "profiles" / "material" / "CF 1k.json").is_file()
    ed = win._editor
    from cfrp_core.diw import DiwConfig
    ed.load_diw_config(DiwConfig(layer_wait_s=4.0))
    ed.ink_profile.save("solvent silver")
    ed.load_diw_config(DiwConfig())
    ed.ink_profile.load("solvent silver")
    assert ed.diw_config().layer_wait_s == 4.0


def test_undo_from_the_window_follows_the_tab(win):
    c = win._controls
    gap0 = c.gap_spin.value()
    c.gap_spin.setValue(gap0 + 0.2)
    win._undo()
    assert c.gap_spin.value() == pytest.approx(gap0)
    win._modes.setCurrentWidget(win._editor)
    n = len(win._editor.design.traces)
    win._editor.new_design()
    win._undo()                                          # the editor's own undo
    assert len(win._editor.design.traces) == n
