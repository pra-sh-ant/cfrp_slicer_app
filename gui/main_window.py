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

        self.setCentralWidget(splitter)

        # --- menu bar -------------------------------------------------------
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
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

        self._worker = SlicerWorker(params, layup, self._machine)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _cancel_slice(self):
        self._retire_worker()
        self._busy.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._status_lbl.setText("Cancelled.")

    def _on_finished(self, result):
        self._busy.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._results.update(result)
        n = len(result.trajectory)
        v = result.validation["status"]
        self._status_lbl.setText(
            f"{n:,} points · {result.max_z:.2f} mm tall · {v}"
        )

    def _on_failed(self, msg: str):
        self._busy.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._results.set_error(msg)
        self._status_lbl.setText(f"Failed: {msg[:80]}")

    def _generate_calibration(self):
        """Write the four calibration coupons for the current settings."""
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
        try:
            params = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        self._controls.load_params(params)
        self._push_recent(path)
        self._status_lbl.setText(f"Loaded: {pathlib.Path(path).name}")

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
