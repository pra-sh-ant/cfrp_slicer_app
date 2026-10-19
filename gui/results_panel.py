"""Right-side results panel: issue list + tabs for 3D view, dashboard and
G-code.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cfrp_core.hints import fix_hint
from cfrp_core.machine import MachineProfile

from .viewer_gl import ViewerGL

_STATUS_STYLE = {
    "Pass": ("#143d22", "#4ade80"),
    "Warning": ("#3d3014", "#fbbf24"),
    "Fail": ("#3d1414", "#f87171"),
    "Error": ("#3d1414", "#f87171"),
    "": ("#2d2d40", "#9090a0"),
}


def layers_in(message: str) -> list[int]:
    """Layer numbers a message names ('layer 12', 'layer(s) [3, 5]')."""
    m = re.search(r"[Ll]ayer\(s\)\s*\[([\d,\s]+)", message)
    if m:
        return [int(x) for x in m.group(1).split(",") if x.strip()]
    return [int(x) for x in re.findall(r"[Ll]ayer\s+(\d+)", message)]


class IssuesPanel(QWidget):
    """Status pill + summary; expands into the list of warnings, errors and
    notes. Clicking an issue that names a layer emits `layer_requested`."""

    layer_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QHBoxLayout()
        self._pill = QLabel("")
        self._pill.setObjectName("statusPill")
        self._summary = QLabel("No slice yet.")
        self._summary.setWordWrap(True)
        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setArrowType(Qt.DownArrow)
        self._toggle.setToolTip("Show / hide the issue list")
        self._toggle.toggled.connect(self._show_list)
        head.addWidget(self._pill)
        head.addWidget(self._summary, 1)
        head.addWidget(self._toggle)
        lay.addLayout(head)
        self._list = QListWidget()
        self._list.setObjectName("issues")
        self._list.setWordWrap(True)
        self._list.setMaximumHeight(170)
        self._list.itemClicked.connect(self._clicked)
        self._list.setVisible(False)
        lay.addWidget(self._list)
        self._set_pill("")

    def _set_pill(self, status):
        bg, fg = _STATUS_STYLE.get(status, _STATUS_STYLE[""])
        self._pill.setText(f"  {status or '—'}  ")
        self._pill.setStyleSheet(f"background:{bg};color:{fg};border-radius:9px;padding:2px 6px;"
                                 f"font-weight:bold;")

    def _show_list(self, on):
        self._toggle.setArrowType(Qt.UpArrow if on else Qt.DownArrow)
        self._list.setVisible(on)

    def _add(self, kind, text):
        icon = {"error": "✖", "warning": "⚠", "info": "ℹ", "ok": "✔"}[kind]
        color = {"error": "#f87171", "warning": "#fbbf24", "info": "#93c5fd", "ok": "#4ade80"}[kind]
        layers = layers_in(text)
        suffix = "   → click to show layer" if layers else ""
        hint = fix_hint(text) if kind in ("error", "warning") else ""
        item = QListWidgetItem(f"{icon}  {text}{suffix}" + (f"\n      How to fix: {hint}" if hint else ""))
        item.setForeground(QColor(color))
        item.setData(Qt.UserRole, layers[0] if layers else None)
        item.setToolTip(text + (f"\n\nHow to fix: {hint}" if hint else ""))
        self._list.addItem(item)

    def set_result(self, validation, notes=(), info=()):
        self._list.clear()
        status = validation.get("status", "")
        msgs = list(validation.get("messages", []))
        kind = "error" if status == "Fail" else "warning"
        problems = [m for m in msgs if not m.startswith("✅")]
        for m in problems:
            self._add(kind, m)
        for m in list(notes) + list(info):
            self._add("info", m)
        if not problems:
            self._add("ok", msgs[0].lstrip("✅ ") if msgs else "All checks passed.")
        self._set_pill(status)
        n = len(problems)
        first = problems[0] if problems else (msgs[0].lstrip("✅ ") if msgs else "All checks passed.")
        more = f"  (+{n - 1} more)" if n > 1 else ""
        notes_n = len(notes) + len(info)
        self._summary.setText((first[:160] + ("…" if len(first) > 160 else "")) + more
                              + (f"   ·  {notes_n} note(s)" if notes_n else ""))
        self._toggle.setChecked(n > 0)

    def set_error(self, msg):
        self._list.clear()
        self._add("error", msg)
        self._set_pill("Error")
        self._summary.setText(msg.splitlines()[0][:200] if msg else "Error")
        self._toggle.setChecked(True)

    def clear_status(self):
        self._list.clear()
        self._set_pill("")
        self._summary.setText("No slice yet.")

    def _clicked(self, item):
        layer = item.data(Qt.UserRole)
        if layer is not None:
            self.layer_requested.emit(int(layer))


class DashboardTab(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setSpacing(8)
        self._layout.setAlignment(Qt.AlignTop)
        self.setWidget(inner)
        self._labels: list[QWidget] = []

    def update_stats(self, trajectory: list[dict], gcode: str,
                     constraint_notes, info_notes, print_stats: dict | None = None, extra_rows=()):
        for lbl in self._labels:
            lbl.deleteLater()
        self._labels = []

        if not trajectory:
            lbl = QLabel("No fibre in this print." if extra_rows else "No trajectory.")
            self._layout.addWidget(lbl)
            self._labels.append(lbl)
            for key, val in extra_rows:
                if key.startswith("─"):
                    continue
                w = QLabel(f"<b>{key}</b>   {val}")
                self._layout.addWidget(w)
                self._labels.append(w)
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
                ("─" * 20, ""),
                ("Print time", time_str),
                ("Fiber length", f"{fl / 1000:.2f} m  ({fl:.0f} mm)"),
                ("Fiber mass", f"{fm * 1000:.1f} mg  ({fm:.4f} g)"),
            ]
            if "coverage" in print_stats:
                rows += [
                    ("Fiber coverage", f"{print_stats['coverage'] * 100:.1f} %  "
                                       f"(worst L{print_stats['worst_layer']}: "
                                       f"{print_stats['worst_coverage'] * 100:.1f} %)"),
                    ("Tow overlap", f"{print_stats['overlap'] * 100:.1f} %"),
                ]
        rows += list(extra_rows)

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
            k.setMinimumWidth(130)
            v = QLabel(val)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
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


def _stl_triangles(geometry, shift):
    """The imported model as the slicer placed it (scale, bed shift)."""
    try:
        import numpy as np

        from cfrp_core.geometry.mesh_slicer import load_stl
        tris = np.asarray(load_stl(geometry.stl_path).triangles, dtype=np.float64) * float(geometry.stl_scale)
        tris[..., 2] -= tris[..., 2].min()
        tris[..., 0] += shift[0]
        tris[..., 1] += shift[1]
        return tris
    except Exception:
        return None


class ViewerTab(QWidget):
    """The 3D viewer (layer range, scrubber and colour modes live inside it)."""

    def __init__(self, machine: MachineProfile, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._viewer = ViewerGL(machine)
        layout.addWidget(self._viewer, stretch=1)

    @property
    def viewer(self):
        return self._viewer

    def update_view(self, trajectory, target_shapes, max_z, config=None, extra=None, shift=(0.0, 0.0)):
        kw = {}
        if config is not None:
            # The laid bead is about as wide as the needle (never wider than
            # the track spacing); Track Gap is the spacing, not the width.
            bead = min(float(config.material.needle_dia_mm), float(config.track_gap_mm))
            kw = dict(bead_width=bead, bead_height=float(config.layer_height_mm),
                      r_min=float(config.min_radius_mm))
            g = config.geometry
            if g.shape == "STL Import" and g.stl_path:
                kw["part_mesh"] = _stl_triangles(g, shift)
        self._viewer.update_scene(trajectory, target_shapes, max_z, extra=extra, **kw)

    def show_layer(self, layer):
        self._viewer.show_layers(layer)


class GcodeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("Save G-code…")
        self._save_btn.setEnabled(False)
        self._csv_btn = QPushButton("Export CSV…")
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
        self._save_btn.clicked.connect(self.save_gcode)
        self._csv_btn.clicked.connect(self.save_csv)

    def set_gcode(self, gcode: str, layers: int, trajectory: list[dict] | None = None):
        self._gcode = gcode
        self._trajectory = trajectory or []
        self._editor.setPlainText(gcode)
        self._save_btn.setEnabled(bool(gcode))
        self._save_btn.setText(f"Save G-code  ({layers}L)…")
        self._csv_btn.setEnabled(bool(self._trajectory))

    def has_gcode(self):
        return bool(self._gcode)

    def save_gcode(self):
        from PySide6.QtWidgets import QFileDialog
        if not self._gcode:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save G-code", "CFRP_toolpath.gcode",
            "G-code files (*.gcode);;All files (*)"
        )
        if path:
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(self._gcode)

    def save_csv(self):
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

        self.banner = IssuesPanel()
        layout.addWidget(self.banner)

        self._tabs = QTabWidget()
        self._viewer_tab = ViewerTab(machine)
        self._gcode_tab = GcodeTab()
        self._dash_tab = DashboardTab()

        self._tabs.addTab(self._viewer_tab, "3D View")
        self._tabs.addTab(self._dash_tab, "Dashboard")
        self._tabs.addTab(self._gcode_tab, "G-code")
        layout.addWidget(self._tabs, stretch=1)
        self.banner.layer_requested.connect(self._show_layer)

    @property
    def viewer(self):
        return self._viewer_tab.viewer

    @property
    def gcode_tab(self):
        return self._gcode_tab

    def _show_layer(self, layer):
        self._tabs.setCurrentWidget(self._viewer_tab)
        self._viewer_tab.show_layer(layer)

    def update(self, result, extra=None, extra_rows=(), extra_notes=()):
        self.banner.set_result(result.validation, result.constraint_notes,
                               tuple(result.info) + tuple(extra_notes))
        self._viewer_tab.update_view(result.trajectory, result.target_shapes, result.max_z,
                                     config=result.config, extra=extra,
                                     shift=getattr(result, "bed_shift", (0.0, 0.0)))
        self._gcode_tab.set_gcode(result.gcode, result.config.layers, trajectory=result.trajectory)
        self._dash_tab.update_stats(
            result.trajectory, result.gcode,
            result.constraint_notes, result.info,
            print_stats=result.stats, extra_rows=extra_rows,
        )

    def set_error(self, msg: str):
        self.banner.set_error(msg)

    def clear(self):
        self.banner.clear_status()
