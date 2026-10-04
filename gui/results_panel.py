"""Right-side results panel: validation banner + tabs for 3D view, G-code,
and a statistics dashboard.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cfrp_core.machine import MachineProfile

from .viewer_gl import ViewerGL


class ValidationBanner(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setMinimumHeight(36)
        self.setStyleSheet("padding: 6px 10px; border-radius: 4px; font-size: 12px;")
        self.clear_status()

    def clear_status(self):
        self.setText("No slice yet.")
        self.setStyleSheet("padding: 6px 10px; border-radius: 4px; font-size: 12px;"
                           "background: #2d2d40; color: #9090a0;")

    def set_status(self, val: dict):
        status = val["status"]
        msgs = val.get("messages", [])
        text = " · ".join(msgs) if msgs else status
        if status == "Pass":
            style = "background: #143d22; color: #4ade80;"
        elif status == "Warning":
            style = "background: #3d3014; color: #fbbf24;"
        else:
            style = "background: #3d1414; color: #f87171;"
        self.setText(f"[{status}]  {text}")
        self.setStyleSheet(f"padding: 6px 10px; border-radius: 4px; font-size: 12px; {style}")

    def set_error(self, msg: str):
        self.setText(f"[Error]  {msg}")
        self.setStyleSheet("padding: 6px 10px; border-radius: 4px; font-size: 12px;"
                           "background: #3d1414; color: #f87171;")


class DashboardTab(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setSpacing(8)
        self._layout.setAlignment(Qt.AlignTop)
        self.setWidget(inner)
        self._labels: list[QLabel] = []

    def update_stats(self, trajectory: list[dict], gcode: str,
                     constraint_notes, info_notes, print_stats: dict | None = None):
        for lbl in self._labels:
            lbl.deleteLater()
        self._labels = []

        if not trajectory:
            lbl = QLabel("No trajectory.")
            self._layout.addWidget(lbl)
            self._labels.append(lbl)
            return

        xs = [p['x'] for p in trajectory]
        ys = [p['y'] for p in trajectory]
        zs = [p['z'] for p in trajectory]
        vs = [p['v'] for p in trajectory]
        layers = max(p.get('layer', 1) for p in trajectory)

        rows = [
            ("Points", f"{len(trajectory):,}"),
            ("Layers", str(layers)),
            ("X span", f"{min(xs):.1f} – {max(xs):.1f} mm"),
            ("Y span", f"{min(ys):.1f} – {max(ys):.1f} mm"),
            ("Z span", f"{min(zs):.2f} – {max(zs):.2f} mm"),
            ("Speed range", f"{min(vs):.1f} – {max(vs):.1f} mm/s"),
            ("G-code lines", f"{gcode.count(chr(10)):,}"),
        ]
        if print_stats:
            t = print_stats.get("print_time_s", 0.0)
            fl = print_stats.get("fiber_length_mm", 0.0)
            fm = print_stats.get("fiber_mass_g", 0.0)
            mins, secs = divmod(int(t), 60)
            hours, mins = divmod(mins, 60)
            if hours:
                time_str = f"{hours}h {mins:02d}m {secs:02d}s"
            elif mins:
                time_str = f"{mins}m {secs:02d}s"
            else:
                time_str = f"{secs}s"
            rows += [
                ("─" * 20, ""),          # separator
                ("Print time", time_str),
                ("Fiber length", f"{fl / 1000:.2f} m  ({fl:.0f} mm)"),
                ("Fiber mass", f"{fm * 1000:.1f} mg  ({fm:.4f} g)"),
            ]

        for key, val in rows:
            if key.startswith("─"):
                sep = QLabel()
                sep.setFixedHeight(1)
                sep.setStyleSheet("background: #2e2e44; margin: 4px 0;")
                self._layout.addWidget(sep)
                self._labels.append(sep)
                continue
            row = QHBoxLayout()
            k = QLabel(f"<b>{key}</b>")
            k.setMinimumWidth(120)
            v = QLabel(val)
            row.addWidget(k)
            row.addWidget(v)
            row.addStretch()
            w = QWidget()
            w.setLayout(row)
            self._layout.addWidget(w)
            self._labels.append(w)

        for note in list(constraint_notes) + list(info_notes):
            lbl = QLabel(f"ℹ  {note}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #93c5fd; padding: 4px;")
            self._layout.addWidget(lbl)
            self._labels.append(lbl)


class ViewerTab(QWidget):
    def __init__(self, machine: MachineProfile, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Layer slider
        ctrl_row = QHBoxLayout()
        self._layer_lbl = QLabel("Layer: all")
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.setValue(0)
        self._slider.setToolTip("0 = show all; >0 = isolate that layer")
        ctrl_row.addWidget(QLabel("Isolate layer:"))
        ctrl_row.addWidget(self._slider)
        ctrl_row.addWidget(self._layer_lbl)
        layout.addLayout(ctrl_row)

        self._viewer = ViewerGL(machine)
        layout.addWidget(self._viewer, stretch=1)

        self._full_traj = []
        self._full_shapes = []
        self._max_z = 1.0
        self._max_layer = 1

        self._slider.valueChanged.connect(self._on_layer_changed)

    def update_view(self, trajectory, target_shapes, max_z):
        self._full_traj = trajectory
        self._full_shapes = target_shapes
        self._max_z = max_z
        self._max_layer = max(p.get('layer', 1) for p in trajectory) if trajectory else 1
        self._slider.blockSignals(True)
        self._slider.setMaximum(self._max_layer)
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._layer_lbl.setText("all")
        self._viewer.update_scene(trajectory, target_shapes, max_z)

    def _on_layer_changed(self, val):
        if val == 0:
            self._layer_lbl.setText("all")
            self._viewer.update_scene(self._full_traj, self._full_shapes, self._max_z)
        else:
            self._layer_lbl.setText(str(val))
            traj = [p for p in self._full_traj if p.get('layer', 1) == val]
            shapes = [s for s in self._full_shapes if len(s) > 2 and s[2] == val]
            self._viewer.update_scene(traj, shapes, self._max_z)


class GcodeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("💾  Save G-code…")
        self._save_btn.setEnabled(False)
        self._csv_btn = QPushButton("📄  Export CSV…")
        self._csv_btn.setEnabled(False)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._csv_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setFont(QFont("Consolas", 9))
        self._editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self._editor, stretch=1)

        self._gcode = ""
        self._trajectory: list[dict] = []
        self._save_btn.clicked.connect(self._save_gcode)
        self._csv_btn.clicked.connect(self._save_csv)

    def set_gcode(self, gcode: str, layers: int, trajectory: list[dict] | None = None):
        self._gcode = gcode
        self._trajectory = trajectory or []
        self._editor.setPlainText(gcode)
        self._save_btn.setEnabled(bool(gcode))
        self._save_btn.setText(f"💾  Save G-code  ({layers}L)")
        self._csv_btn.setEnabled(bool(self._trajectory))

    def _save_gcode(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Save G-code", "CFRP_toolpath.gcode",
            "G-code files (*.gcode);;All files (*)"
        )
        if path:
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(self._gcode)

    def _save_csv(self):
        import csv

        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Trajectory CSV", "CFRP_trajectory.csv",
            "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        fields = ["idx", "x", "y", "z", "v", "dist", "e_mult",
                  "layer", "is_transition", "layer_start"]
        with open(path, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            w.writeheader()
            for i, pt in enumerate(self._trajectory):
                w.writerow({
                    "idx": i,
                    "x": round(pt.get("x", 0.0), 4),
                    "y": round(pt.get("y", 0.0), 4),
                    "z": round(pt.get("z", 0.0), 4),
                    "v": round(pt.get("v", 0.0), 4),
                    "dist": round(pt.get("dist", 0.0), 4),
                    "e_mult": round(pt.get("e_mult", 1.0), 4),
                    "layer": pt.get("layer", 1),
                    "is_transition": int(bool(pt.get("is_transition", False))),
                    "layer_start": int(bool(pt.get("layer_start", False))),
                })


class ResultsPanel(QWidget):
    def __init__(self, machine: MachineProfile, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.banner = ValidationBanner()
        layout.addWidget(self.banner)

        self._tabs = QTabWidget()
        self._viewer_tab = ViewerTab(machine)
        self._gcode_tab = GcodeTab()
        self._dash_tab = DashboardTab()

        self._tabs.addTab(self._viewer_tab, "3D View")
        self._tabs.addTab(self._dash_tab, "Dashboard")
        self._tabs.addTab(self._gcode_tab, "G-code")
        layout.addWidget(self._tabs, stretch=1)

    def update(self, result):
        self.banner.set_status(result.validation)
        self._viewer_tab.update_view(
            result.trajectory, result.target_shapes, result.max_z
        )
        self._gcode_tab.set_gcode(
            result.gcode, result.config.layers, trajectory=result.trajectory
        )
        self._dash_tab.update_stats(
            result.trajectory, result.gcode,
            result.constraint_notes, result.info,
            print_stats=result.stats,
        )

    def set_error(self, msg: str):
        self.banner.set_error(msg)

    def clear(self):
        self.banner.clear_status()
