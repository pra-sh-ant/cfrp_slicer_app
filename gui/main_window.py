"""Main application window."""
from __future__ import annotations

import json
import math
import pathlib

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
)

from cfrp_core.machine import DEFAULT_MACHINE

from .controls_panel import ControlsPanel
from .results_panel import ResultsPanel
from .slicer_worker import SlicerWorker
from .theme import STYLE

_DEBOUNCE_MS = 500
_MAX_RECENT = 8
_ORG = "CFRPLab"
_APP = "CFRPSlicerPro"

_DEFAULTS = {
    "shape": "Square/Rectangle",
    "width": 135.0, "height": 25.0,
    "include_boundary": False,
    "base_radius": 20.0, "disc_radius": 15.0, "stem_height": 25.0,
    "start_x": 26.0, "start_y": 40.0, "track_gap": 1.2, "z_offset": 0.2,
    "layers": 1, "tracks_per_layer": 7,
    "infill_type": "Archimedean (Concentric Spiral)", "layup_seq": "0",
    "needle_dia": 0.8, "layer_height": 0.2, "e_mult": 1.6,
    "density": 0.00115, "weight": 0.55 / (math.pi * 39),
    "v_max": 10.0, "v_min": 5.0, "accel": 25.0, "decel": 15.0,
    "p_advance": 3.0, "pump_stall_floor": 5.0, "transition_angle": 30.0,
    "seam_trim_length": 3.0, "anchor_length": 15.0, "min_radius": 3.0,
    "v_z": 2.0, "v_overhang": 5.0, "dwell": 1.5, "fill_solid": False,
    "first_layer_solid": True, "first_layer_infill": "Same as other layers", "first_layer_min_coverage": 0.0,
    "solid_bottom_layers": 0, "solid_top_layers": 0,
    "stl_closing_radius": 0.049, "stl_stitch": 5.0,
    "bed_x": 180.0, "bed_y": 220.0, "z_max": 200.0, "bed_margin": 1.0,
    "uv": 225, "purge_e": 15.0, "purge_f": 60.0, "travel_f": 600.0,
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CFRP Slicer Pro")
        self.resize(1400, 860)
        self._machine = DEFAULT_MACHINE
        self._worker: SlicerWorker | None = None
        self._retired: list[SlicerWorker] = []
        self._settings = QSettings(_ORG, _APP)

        # --- central layout -------------------------------------------------
        splitter = QSplitter(Qt.Horizontal)

        self._controls = ControlsPanel()
        self._controls.setMinimumWidth(370)
        self._controls.setMaximumWidth(520)

        self._results = ResultsPanel(self._machine)

        splitter.addWidget(self._controls)
        splitter.addWidget(self._results)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 1100])

        # Top level: the fibre slicer and the structural PCB editor
        from .pcb_editor import PcbEditor
        self._editor = PcbEditor()
        self._editor.slice_requested.connect(self._slice_board)
        self._editor.design_changed.connect(self._board_changed)
        self._controls.open_editor_requested.connect(self._show_editor)
        self._board = None            # (BoardDesign, DiwConfig) while a PCB is being sliced
        self._modes = QTabWidget()
        self._modes.setDocumentMode(True)
        self._modes.addTab(splitter, "Fibre Slicer")
        self._modes.addTab(self._editor, "Structural PCB (DIW)")
        self.setCentralWidget(self._modes)

        # --- menu bar -------------------------------------------------------
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        act_open_proj = file_menu.addAction("Open Project…")
        act_open_proj.setShortcut(QKeySequence("Ctrl+Shift+O"))
        act_open_proj.setToolTip("Settings, PCB design, ink settings and print mode in one .cfrpproj file")
        act_save_proj = file_menu.addAction("Save Project…")
        act_save_proj.setShortcut(QKeySequence("Ctrl+Shift+S"))
        file_menu.addSeparator()
        act_save = file_menu.addAction("Save Parameters…")
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        act_load = file_menu.addAction("Load Parameters…")
        act_load.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addSeparator()
        self._recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self._recent_menu)
        file_menu.addSeparator()
        act_reset = file_menu.addAction("Reset to Defaults")
        act_reset.setShortcut(QKeySequence("Ctrl+Shift+R"))
        file_menu.addSeparator()
        act_slice = file_menu.addAction("Slice Now")
        act_slice.setShortcut(QKeySequence("F5"))
        file_menu.addSeparator()
        act_quit = file_menu.addAction("Quit")
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))

        act_save.triggered.connect(self._save_params)
        act_load.triggered.connect(self._load_params)
        act_open_proj.triggered.connect(self._open_project)
        act_save_proj.triggered.connect(self._save_project)

        edit_menu = mb.addMenu("Edit")
        self._act_undo = edit_menu.addAction("Undo Settings Change\tCtrl+Z")
        self._act_undo.triggered.connect(self._undo)
        self._act_redo = edit_menu.addAction("Redo Settings Change\tCtrl+Y")
        self._act_redo.triggered.connect(self._redo)
        # On the fibre tab only (the PCB editor has its own Ctrl+Z / Ctrl+Y;
        # text boxes keep their own undo while they have the focus).
        for keys, slot in ((QKeySequence.Undo, self._undo), (QKeySequence.Redo, self._redo),
                           (QKeySequence("Ctrl+Y"), self._redo)):
            sc = QShortcut(keys, splitter)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        act_reset.triggered.connect(self._reset_params)
        act_slice.triggered.connect(self._slice)
        act_quit.triggered.connect(self.close)

        tools_menu = mb.addMenu("Tools")
        act_calib = tools_menu.addAction("Generate Calibration G-code…")
        act_calib.triggered.connect(self._generate_calibration)

        help_menu = mb.addMenu("Help")
        act_about = help_menu.addAction("About CFRP Slicer Pro…")
        act_about.triggered.connect(self._show_about)

        # --- toolbar ----------------------------------------------------------
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        self._act_slice = tb.addAction("▶  Slice", self._slice)
        self._act_slice.setToolTip("Slice now (F5)")
        self._act_auto = tb.addAction("Auto-slice")
        self._act_auto.setCheckable(True)
        self._act_auto.setChecked(self._settings.value("autoSlice", True, type=bool))
        self._act_auto.setToolTip("Re-slice automatically whenever a setting changes")
        self._act_auto.toggled.connect(lambda on: self._settings.setValue("autoSlice", on))
        tb.addSeparator()
        tb.addAction("Open STL…", self._controls._browse_stl).setToolTip("Import an STL / OBJ model")
        tb.addAction("Save G-code…", lambda: self._results.gcode_tab.save_gcode()).setToolTip(
            "Save the sliced G-code")
        tb.addAction("Export CSV…", lambda: self._results.gcode_tab.save_csv()).setToolTip(
            "Export the trajectory as CSV")
        tb.addSeparator()
        tb.addAction("PCB Editor", self._show_editor).setToolTip(
            "Draw a structural PCB: board outline, holes, traces, pads (DIW electronics)")
        tb.addAction("Report…", self._save_report).setToolTip(
            "Save a print report (HTML): time, materials, checks, coverage, wire resistances")
        tb.addAction("Calibration…", self._generate_calibration).setToolTip(
            "Write the calibration coupons for these settings")
        self._toolbar = tb

        # Escape cancels a running slice from anywhere in the window
        esc = QShortcut(QKeySequence("Escape"), self)
        esc.activated.connect(self._cancel_slice)

        self._rebuild_recent_menu()

        # --- status bar -----------------------------------------------------
        sb = QStatusBar()
        self._status_lbl = QLabel("Ready.")
        sb.addWidget(self._status_lbl)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedWidth(70)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._cancel_slice)
        self._busy = QProgressBar()
        self._busy.setRange(0, 0)
        self._busy.setFixedWidth(120)
        self._busy.setVisible(False)
        sb.addPermanentWidget(self._busy)
        sb.addPermanentWidget(self._cancel_btn)
        self.setStatusBar(sb)

        # --- theme ----------------------------------------------------------
        self.setStyleSheet(STYLE)

        # --- debounce timer -------------------------------------------------
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._slice)
        self._controls.params_changed.connect(self._schedule_slice)

        # Restore window geometry from last session
        geom = self._settings.value("windowGeometry")
        if isinstance(geom, QByteArray):
            self.restoreGeometry(geom)

        # Initial slice
        QTimer.singleShot(100, self._slice)

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self._retire_worker()
        for w in self._retired:
            w.wait(5000)
        self._settings.setValue("windowGeometry", self.saveGeometry())
        super().closeEvent(event)

    def _schedule_slice(self):
        if self._act_auto.isChecked():
            self._timer.start(_DEBOUNCE_MS)
        else:
            self._status_lbl.setText("Settings changed: press Slice (F5).")

    def _retire_worker(self):
        """Cancel the running worker and keep a reference until its thread
        really ends: dropping the last reference to a running QThread makes Qt
        abort the whole process ("QThread: Destroyed while thread is still
        running")."""
        worker = self._worker
        self._worker = None
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.finished.disconnect()
            worker.failed.disconnect()
            self._retired.append(worker)
        self._retired = [w for w in self._retired if w.isRunning()]

    def _slice(self):
        self._retire_worker()

        params = self._controls.get_params()
        layup = self._controls.get_layup()
        self._status_lbl.setText("Slicing…")
        self._cancel_btn.setVisible(True)
        self._busy.setVisible(True)

        board = self._board if params.get("shape") == "Custom Outline" else None
        machine = self._controls.get_machine()
        if machine != self._machine:
            self._machine = machine
            self._results.viewer.set_machine(machine)
        from .viewer_gl import MAX_DRAW_POINTS
        viewer = self._results.viewer
        view = (MAX_DRAW_POINTS, viewer.wants_core()) if hasattr(viewer, "wants_core") else None
        self._worker = SlicerWorker(params, layup, self._machine, board=board, view=view)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _cancel_slice(self):
        self._retire_worker()
        self._busy.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._status_lbl.setText("Cancelled.")

    def _show_editor(self):
        self._editor.set_min_radius(self._controls.min_r_spin.value())
        self._modes.setCurrentWidget(self._editor)

    def _board_changed(self, design):
        if self._board is not None:
            self._board = (design, self._editor.diw_config(), self._editor.print_mode())

    def _slice_board(self, design, diw_cfg, mode="structural"):
        """Editor -> slicer: the outline becomes the Custom Outline part and
        the ink is sliced on top of it."""
        from cfrp_core.diw import hole_outlines
        self._board = (design, diw_cfg, mode)
        self._controls.set_custom_outline(design.outline, hole_outlines(design), design.name)
        self._modes.setCurrentIndex(0)
        self._slice()

    def _save_report(self):
        """HTML report of the last slice, with a picture of the 3D view."""
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        from cfrp_core.report import html_report
        res = getattr(self, "_last_result", None)
        if res is None:
            QMessageBox.information(self, "Report", "Slice something first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save report", "print_report.html", "HTML (*.html)")
        if not path:
            return
        img = None
        view = getattr(self._results.viewer, "_view", None)
        if view is not None:
            data = QByteArray()
            buf = QBuffer(data)
            buf.open(QIODevice.WriteOnly)
            view.grabFramebuffer().save(buf, "PNG")
            img = bytes(data)
        board = getattr(self, "_last_board", None) if res.config.geometry.shape == "Custom Outline" else None
        html = html_report(fibre=None if board else res, board=board, image_png=img,
                           title=f"Print report: {res.config.geometry.shape}")
        pathlib.Path(path).write_text(html, encoding="utf-8")
        self._status_lbl.setText(f"Report saved: {pathlib.Path(path).name}")

    def _on_finished(self, result):
        self._busy.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._controls.clear_problems()
        ink = 0
        if hasattr(result, "diw"):
            ink = len(result.diw.trajectory) if result.diw is not None else 0
            top = max((p["z"] for p in result.diw.trajectory), default=0.0) if result.diw else 0.0
            result = self._show_board(result)
            top = max(top, result.max_z)
        else:
            self._results.update(result, prepared=getattr(result, "prepared_view", None))
            top = result.max_z
        self._last_result = result
        if not hasattr(result, "diw") and result.config.geometry.shape != "Custom Outline":
            self._last_board = None
        n = len(result.trajectory)
        v = result.validation["status"]
        parts = ([f"{n:,} fibre points"] if n else []) + ([f"{ink:,} ink points"] if ink else [])
        self._status_lbl.setText(f"{' + '.join(parts) or 'nothing'} · {top:.2f} mm tall · {v}")

    def _show_board(self, board):
        """Show a structural PCB result (fibre and/or ink in one view, merged
        checks, one G-code). Returns the result shown as the main one."""
        import dataclasses

        from cfrp_core import from_legacy_dict

        from .toolpath_mesh import from_trajectory
        fibre, diw = board.fibre, board.diw
        if fibre is None:      # electronics only: a fibre-less stand-in for the panels
            from cfrp_core.pipeline import SliceResult
            cfg = from_legacy_dict(self._controls.get_params(), layup=self._controls.get_layup())
            top = max((p["z"] for p in diw.trajectory), default=0.0)
            fibre = SliceResult(config=cfg, trajectory=[], target_shapes=[], gcode=board.gcode,
                                validation={"status": "Pass", "messages": []}, max_z=top,
                                constraint_notes=(), info=("Electronics only: no fibre is printed.",),
                                stats={})
        rank = {"Pass": 0, "Warning": 1, "Fail": 2}
        statuses = [fibre.validation["status"]] + ([diw.validation["status"]] if diw else [])
        status = max(statuses, key=rank.get)
        upper = None
        msgs = [m for m in fibre.validation["messages"] if not m.startswith("✅")]
        if diw is not None:
            msgs += [f"DIW: {m}" for m in diw.validation["messages"] if not m.startswith("DRC passed")]
        upper = getattr(board, "fibre_upper", None)
        if upper is not None:
            msgs += [f"Upper laminate: {m}" for m in upper.validation["messages"] if not m.startswith("✅")]
            statuses.append(upper.validation["status"])
            status = max(statuses, key=rank.get)
        if not msgs:
            msgs = ["✅ All checks passed."]
        merged = dataclasses.replace(fibre, validation={"status": status, "messages": msgs}, gcode=board.gcode)
        rows, extra = [("Print mode", board.mode)], None
        if diw is not None:
            st = diw.stats
            rows += [("─" * 20, ""),
                     ("DIW ink", ", ".join(f"{k} {v / 1000:.2f} m" for k, v in st["ink_length_mm"].items())),
                     ("DIW volume", ", ".join(f"{k} {v:.1f} mm³" for k, v in st["ink_volume_mm3"].items())),
                     ("DIW time", f"{st['print_time_s'] / 60:.1f} min, {st['starts']} ink starts"),
                     ("Board origin", f"X {board.offset[0]:.2f}  Y {board.offset[1]:.2f} mm")]
            rows += [(f"R {t['name']}", f"{t['resistance_ohm'] * 1000:.1f} mΩ  ({t['length_mm']:.1f} mm)")
                     for t in diw.validation["traces"]]
            extra = from_trajectory(diw.trajectory, tool=1)
        if upper is not None:
            from .toolpath_mesh import concat
            c = upper.config
            bead = min(float(c.material.needle_dia_mm), float(c.track_gap_mm))
            extra = concat([extra, from_trajectory(upper.trajectory, bead, float(c.layer_height_mm))])
            rows += [("Upper laminate", f"{c.layers} layer(s), {(upper.stats or {}).get('print_time_s', 0) / 60:.1f} min")]
        self._last_board = board
        self._results.update(merged, extra=extra, extra_rows=rows, extra_notes=tuple(getattr(board, "notes", ())))
        return merged

    def _on_failed(self, msg: str):
        self._busy.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._results.set_error(msg)
        marked = self._controls.mark_problems(msg)
        hint = f" (see the red setting{'s' if len(marked) > 1 else ''})" if marked else ""
        self._status_lbl.setText(f"Failed: {msg[:80]}{hint}")

    def _generate_calibration(self):
        """Write the fibre calibration coupons and the ink flow coupon."""
        from cfrp_core import from_legacy_dict, resolve_process_constraints
        from cfrp_core.calibration import COUPONS, make_coupon

        folder = QFileDialog.getExistingDirectory(self, "Folder for calibration G-code")
        if not folder:
            return
        try:
            cfg = from_legacy_dict(self._controls.get_params(), layup=self._controls.get_layup())
            cfg = resolve_process_constraints(cfg).config
            written = []
            for kind in COUPONS:
                _, gcode = make_coupon(kind, cfg, self._machine)
                path = pathlib.Path(folder) / f"calibration_{kind}.gcode"
                path.write_text(gcode, encoding="utf-8")
                written.append(path.name)
            from cfrp_core.diw.calibration import flow_coupon
            path = pathlib.Path(folder) / "calibration_ink_flow.gcode"
            path.write_text(flow_coupon(self._editor.diw_config()), encoding="utf-8")
            written.append(path.name)
        except Exception as exc:
            QMessageBox.critical(self, "Calibration failed", str(exc))
            return
        QMessageBox.information(
            self, "Calibration G-code written",
            f"Wrote to {folder}:\n  " + "\n  ".join(written)
            + "\n\nEach file's header says how to read the print.",
        )

    # ------------------------------------------------------------------
    # Parameter save / load / reset
    # ------------------------------------------------------------------
    def _save_params(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Parameters", "cfrp_params.json",
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        params = self._controls.get_params()
        params["layup_seq"] = self._controls.layup_edit.text()
        pathlib.Path(path).write_text(
            json.dumps(params, indent=2), encoding="utf-8"
        )
        self._push_recent(path)
        self._status_lbl.setText(f"Saved: {pathlib.Path(path).name}")

    def _load_params(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Parameters", "",
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        self._load_from_path(path)

    def _load_from_path(self, path: str):
        if str(path).lower().endswith(".cfrpproj"):
            self._open_project(path)
            return
        try:
            params = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        ignored = self._controls.load_params(params)
        self._push_recent(path)
        self._status_lbl.setText(f"Loaded: {pathlib.Path(path).name}")
        self._report_ignored(ignored, path)

    def _report_ignored(self, ignored, path):
        """Say which settings of a file this version did not use (it was
        saved by another version), instead of dropping them silently."""
        if not ignored:
            return
        shown = ", ".join(ignored[:12]) + (f" and {len(ignored) - 12} more" if len(ignored) > 12 else "")
        self._status_lbl.setText(f"Loaded {pathlib.Path(path).name}: {len(ignored)} setting(s) ignored")
        if not getattr(self, "_quiet", False):
            QMessageBox.information(
                self, "Some settings were not used",
                f"{len(ignored)} setting(s) in {pathlib.Path(path).name} are not used by this version and "
                f"were ignored:\n\n{shown}")

    # ------------------------------------------------------------------
    # Projects: settings + PCB design + ink settings + print mode
    # ------------------------------------------------------------------
    def _project_params(self) -> dict:
        params = self._controls.get_params()
        params["layup_seq"] = self._controls.layup_edit.text()
        m = self._controls.get_machine()
        params.update(bed_x=m.bed_x_mm, bed_y=m.bed_y_mm, z_max=m.z_max_mm, bed_margin=m.margin_mm)
        return params

    def save_project(self, path: str):
        from cfrp_core.project import SUFFIX, save_project
        if not str(path).lower().endswith(SUFFIX):
            path = str(path) + SUFFIX
        save_project(path, self._project_params(), self._editor.design, self._editor.diw_config(),
                     self._editor.print_mode(), app=self.windowTitle())
        self._push_recent(path)
        self._status_lbl.setText(f"Project saved: {pathlib.Path(path).name}")
        return path

    def _save_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", "part.cfrpproj",
                                              "CFRP projects (*.cfrpproj);;All files (*)")
        if path:
            self.save_project(path)

    def _open_project(self, path=None):
        from cfrp_core.project import load_project
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Project", "",
                                                  "CFRP projects (*.cfrpproj);;All files (*)")
            if not path:
                return
        try:
            proj = load_project(path)
        except ValueError as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        ignored = list(proj["ignored"]) + self._controls.load_params(proj["params"])
        if proj["board"] is not None:
            self._editor.commit(proj["board"])
            self._editor.fit()
        if proj["diw"] is not None:
            self._editor.load_diw_config(proj["diw"])
        self._editor.set_print_mode(proj["print_mode"])
        if proj["params"].get("shape") == "Custom Outline" and proj["board"] is not None:
            self._board = (proj["board"], self._editor.diw_config(), self._editor.print_mode())
        self._push_recent(path)
        self._status_lbl.setText(f"Project opened: {pathlib.Path(path).name}")
        self._report_ignored(ignored, path)

    def _undo(self):
        if self._modes.currentWidget() is self._editor:
            self._editor.undo()
        elif self._controls.undo_settings():
            self._status_lbl.setText("Undid the last settings change.")

    def _redo(self):
        if self._modes.currentWidget() is self._editor:
            self._editor.redo()
        elif self._controls.redo_settings():
            self._status_lbl.setText("Redid the settings change.")

    def _reset_params(self):
        self._controls.load_params(dict(_DEFAULTS))

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------
    def _recent_paths(self) -> list[str]:
        raw = self._settings.value("recentFiles", [])
        return raw if isinstance(raw, list) else [raw] if raw else []

    def _push_recent(self, path: str):
        paths = [p for p in self._recent_paths() if p != path]
        paths.insert(0, path)
        self._settings.setValue("recentFiles", paths[:_MAX_RECENT])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        self._recent_menu.clear()
        paths = self._recent_paths()
        if not paths:
            placeholder = self._recent_menu.addAction("(none)")
            placeholder.setEnabled(False)
            return
        for p in paths:
            label = pathlib.Path(p).name
            act = self._recent_menu.addAction(label)
            act.setToolTip(p)
            act.triggered.connect(lambda checked=False, fp=p: self._load_from_path(fp))
        self._recent_menu.addSeparator()
        clear_act = self._recent_menu.addAction("Clear Recent Files")
        clear_act.triggered.connect(self._clear_recent)

    def _clear_recent(self):
        self._settings.remove("recentFiles")
        self._rebuild_recent_menu()

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------
    def _show_about(self):
        import sys

        import PySide6
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("About CFRP Slicer Pro")
        dlg.setMinimumWidth(380)
        layout = QVBoxLayout(dlg)

        lines = [
            "<b>CFRP Slicer Pro</b>",
            "Continuous Fiber Reinforced Polymer multi-layer slicer",
            "",
            f"Python {sys.version.split()[0]}",
            f"PySide6 {PySide6.__version__}",
            "",
            "Phases completed: 0–21",
            "(safety net · cfrp_core extraction · spiral Z · GUI · "
            "save/load · packaging · Fermat seam fix · recent files · "
            "print stats · cancel · shortcuts · About/CSV · Fermat islands fix · "
            "perimeter+diagonal · seam anchoring · stepped concentric · rect spiral)",
        ]
        for line in lines:
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(lbl)

        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        layout.addWidget(bb)
        dlg.exec()
