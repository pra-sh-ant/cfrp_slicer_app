"""Main application window."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSplitter,
    QStatusBar,
)

from cfrp_core.machine import DEFAULT_MACHINE

from .controls_panel import ControlsPanel
from .results_panel import ResultsPanel
from .slicer_worker import SlicerWorker

_DEBOUNCE_MS = 500   # wait this long after last keystroke before slicing


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CFRP Slicer Pro")
        self.resize(1400, 860)
        self._machine = DEFAULT_MACHINE
        self._worker: SlicerWorker | None = None

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
