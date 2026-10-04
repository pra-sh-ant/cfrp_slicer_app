"""Main application window."""
from __future__ import annotations

import json
import math
import pathlib

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStatusBar,
)

from cfrp_core.machine import DEFAULT_MACHINE

from .controls_panel import ControlsPanel
from .results_panel import ResultsPanel
from .slicer_worker import SlicerWorker

_DEBOUNCE_MS = 500
_MAX_RECENT = 8
_ORG = "CFRPLab"
_APP = "CFRPSlicerPro"

_DEFAULTS = {
    "shape": "Square/Rectangle",
    "width": 135.0, "height": 25.0, "thickness": 10.0,
    "include_boundary": False, "invert_bends": False,
    "base_radius": 20.0, "disc_radius": 15.0, "stem_height": 25.0,
    "start_x": 26.0, "start_y": 40.0, "track_gap": 1.2, "z_offset": 0.2,
    "layers": 1, "tracks_per_layer": 7,
    "infill_type": "Archimedean (Concentric Spiral)", "layup_seq": "0",
    "needle_dia": 0.8, "layer_height": 0.2, "e_mult": 1.6,
    "density": 0.00115, "weight": 0.55 / (math.pi * 39),
    "v_max": 10.0, "v_min": 5.0, "accel": 25.0, "decel": 15.0,
    "p_advance": 3.0, "pump_stall_floor": 5.0, "transition_angle": 30.0,
    "seam_trim_length": 3.0, "anchor_length": 15.0, "min_radius": 3.0,
    "v_z": 2.0, "v_overhang": 5.0, "dwell": 1.5,
    "uv": 225, "purge_e": 15.0, "purge_f": 60.0, "travel_f": 600.0,
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CFRP Slicer Pro")
        self.resize(1400, 860)
        self._machine = DEFAULT_MACHINE
        self._worker: SlicerWorker | None = None
        self._settings = QSettings(_ORG, _APP)

        # --- central layout -------------------------------------------------
        splitter = QSplitter(Qt.Horizontal)

        self._controls = ControlsPanel()
        self._controls.setMinimumWidth(320)
        self._controls.setMaximumWidth(420)

        self._results = ResultsPanel(self._machine)

        splitter.addWidget(self._controls)
        splitter.addWidget(self._results)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1020])

        self.setCentralWidget(splitter)

        # --- menu bar -------------------------------------------------------
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        act_save = file_menu.addAction("Save Parameters…")
        act_load = file_menu.addAction("Load Parameters…")
        file_menu.addSeparator()
        self._recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self._recent_menu)
        file_menu.addSeparator()
        act_reset = file_menu.addAction("Reset to Defaults")
        file_menu.addSeparator()
        act_quit = file_menu.addAction("Quit")

        act_save.triggered.connect(self._save_params)
        act_load.triggered.connect(self._load_params)
        act_reset.triggered.connect(self._reset_params)
        act_quit.triggered.connect(self.close)

        self._rebuild_recent_menu()

        # --- status bar -----------------------------------------------------
        sb = QStatusBar()
        self._status_lbl = QLabel("Ready.")
        sb.addWidget(self._status_lbl)
        self.setStatusBar(sb)

        # --- dark theme -----------------------------------------------------
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #12121e; color: #d0d0e0; }
            QGroupBox { border: 1px solid #2e2e44; border-radius: 4px;
                         margin-top: 8px; font-weight: bold; color: #9898b8; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; top: 4px; }
            QScrollArea { border: none; }
            QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit {
                background: #1e1e30; border: 1px solid #3a3a54;
                border-radius: 3px; color: #d0d0e0; padding: 2px 4px; }
            QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus {
                border-color: #5b6ee1; }
            QPushButton { background: #2a2a44; border: 1px solid #4a4a6a;
                           border-radius: 4px; padding: 4px 12px; color: #c8c8e8; }
            QPushButton:hover { background: #3a3a5a; }
            QPushButton:disabled { color: #555570; }
            QTabBar::tab { background: #1e1e30; border: 1px solid #3a3a54;
                            padding: 4px 12px; color: #9898b8; }
            QTabBar::tab:selected { background: #2a2a44; color: #d0d0f0;
                                    border-bottom: 2px solid #5b6ee1; }
            QSplitter::handle { background: #2a2a44; width: 2px; }
            QLabel { color: #c0c0d8; }
            QPlainTextEdit { background: #0e0e1a; color: #a8f0a8; font-family: Consolas; }
            QSlider::groove:horizontal { background: #2a2a40; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #5b6ee1; width: 12px; height: 12px;
                                          border-radius: 6px; margin: -4px 0; }
            QStatusBar { background: #0e0e1a; color: #7070a0; font-size: 11px; }
            QMenuBar { background: #0e0e1a; color: #c0c0d8; }
            QMenuBar::item:selected { background: #2a2a44; }
            QMenu { background: #1a1a2e; color: #c0c0d8; border: 1px solid #3a3a54; }
            QMenu::item:selected { background: #2a2a44; }
        """)

        # --- debounce timer -------------------------------------------------
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._slice)
        self._controls.params_changed.connect(self._schedule_slice)

        # Initial slice
        QTimer.singleShot(100, self._slice)

    # ------------------------------------------------------------------
    def _schedule_slice(self):
        self._timer.start(_DEBOUNCE_MS)

    def _slice(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.finished.disconnect()
            self._worker.failed.disconnect()
            self._worker.quit()

        params = self._controls.get_params()
        layup = self._controls.get_layup()
        self._status_lbl.setText("Slicing…")

        self._worker = SlicerWorker(params, layup, self._machine)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_finished(self, result):
        self._results.update(result)
        n = len(result.trajectory)
        v = result.validation["status"]
        self._status_lbl.setText(
            f"{n:,} points · {result.max_z:.2f} mm tall · {v}"
        )

    def _on_failed(self, msg: str):
        self._results.set_error(msg)
        self._status_lbl.setText(f"Failed: {msg[:80]}")

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
