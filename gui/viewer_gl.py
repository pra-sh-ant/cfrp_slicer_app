"""3D OpenGL toolpath viewer using pyqtgraph GLViewWidget.

Controls:
  Left-drag   — rotate
  Mid-drag    — pan
  Scroll      — zoom
  Right-drag  — zoom (alternate)

View presets and speed legend are provided by ViewerGL itself.
Layer isolation is handled by the parent ViewerTab.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    import pyqtgraph.opengl as gl
    _HAS_GL = True
except Exception:
    _HAS_GL = False

from cfrp_core.machine import MachineProfile

MAX_VIEWER_POINTS = 12000
MAX_VIEWER_OUTLINES = 150

_TURBO = np.array([
    [0.19, 0.07, 0.23], [0.21, 0.18, 0.47], [0.18, 0.31, 0.66],
    [0.13, 0.46, 0.78], [0.06, 0.60, 0.84], [0.04, 0.72, 0.82],
    [0.15, 0.81, 0.72], [0.35, 0.88, 0.60], [0.56, 0.92, 0.48],
    [0.74, 0.94, 0.37], [0.89, 0.93, 0.25], [0.98, 0.87, 0.14],
    [0.99, 0.76, 0.08], [0.96, 0.62, 0.07], [0.88, 0.47, 0.07],
    [0.76, 0.33, 0.07], [0.60, 0.21, 0.06], [0.42, 0.12, 0.04],
    [0.25, 0.06, 0.02],
], dtype=np.float32)


def _speed_to_color(v_arr: np.ndarray) -> np.ndarray:
    v_min, v_max = v_arr.min(), v_arr.max()
    t = (v_arr - v_min) / (v_max - v_min) if v_max > v_min else np.zeros_like(v_arr)
    idx = (t * (len(_TURBO) - 1)).astype(int).clip(0, len(_TURBO) - 1)
    rgb = _TURBO[idx]
    return np.hstack([rgb, np.ones((len(rgb), 1), dtype=np.float32)])


def _decimate(arr, max_pts):
    n = len(arr)
    if n <= max_pts:
        return arr
    return arr[::max(1, n // max_pts)]


# ---------------------------------------------------------------------------
# Speed legend (colour strip + min/max labels)
# ---------------------------------------------------------------------------
class _SpeedLegend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._v_min = 0.0
        self._v_max = 0.0
        self._visible_legend = False

    def set_range(self, v_min: float, v_max: float):
        self._v_min = v_min
        self._v_max = v_max
        self._visible_legend = True
        self.update()

    def clear(self):
        self._visible_legend = False
        self.update()

    def paintEvent(self, _event):
        if not self._visible_legend:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bar_x, bar_w = 60, w - 120
        if bar_w < 20:
            return

        grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
        for i, (r, g, b) in enumerate(_TURBO):
            t = i / (len(_TURBO) - 1)
            grad.setColorAt(t, QColor(int(r * 255), int(g * 255), int(b * 255)))
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(bar_x, 4, bar_w, h - 8, 3, 3)

        p.setPen(QPen(QColor(180, 180, 200)))
        fm = p.fontMetrics()
        lo = f"{self._v_min:.1f}"
        hi = f"{self._v_max:.1f} mm/s"
        p.drawText(bar_x - fm.horizontalAdvance(lo) - 4,
                   h // 2 + fm.ascent() // 2 - 1, lo)
        p.drawText(bar_x + bar_w + 4,
                   h // 2 + fm.ascent() // 2 - 1, hi)


# ---------------------------------------------------------------------------
# Main viewer widget
# ---------------------------------------------------------------------------
class ViewerGL(QWidget):
    def __init__(self, machine: MachineProfile, parent=None):
        super().__init__(parent)
        self._machine = machine
        self._last_max_z = 50.0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        # ---- toolbar row -----------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        def _vbtn(label, tip, cb):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setFixedHeight(22)
            b.setFixedWidth(52)
            b.setStyleSheet(
                "QPushButton{background:#1e2040;border:1px solid #3a3a60;"
                "border-radius:3px;color:#b0b0d0;font-size:11px;}"
                "QPushButton:hover{background:#2a2a5a;}"
            )
            b.clicked.connect(cb)
            return b

        toolbar.addWidget(_vbtn("⊙ ISO",  "Isometric view",        self.view_iso))
        toolbar.addWidget(_vbtn("⬆ TOP",  "Top-down view (Z axis)", self.view_top))
        toolbar.addWidget(_vbtn("◧ FRONT","Front view (Y=0 plane)", self.view_front))
        toolbar.addWidget(_vbtn("◨ SIDE", "Right side view",        self.view_right))
        toolbar.addWidget(_vbtn("⟳ FIT",  "Reset camera to fit part",self.fit))
        toolbar.addStretch()

        self._hint = QLabel("Drag: rotate  •  Mid/Shift+drag: pan  •  Scroll: zoom")
        self._hint.setStyleSheet("color:#505070;font-size:10px;")
        toolbar.addWidget(self._hint)

        outer.addLayout(toolbar)

        # ---- GL view ---------------------------------------------------
        if not _HAS_GL:
            from PySide6.QtWidgets import QLabel as _L
            lbl = _L("3D viewer unavailable (pyqtgraph OpenGL not loaded).")
            lbl.setAlignment(Qt.AlignCenter)
            outer.addWidget(lbl)
            self._view = None
            self._legend = None
            return

        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor('#12121e')
        self._view.setCameraPosition(distance=300, elevation=30, azimuth=-50)
        outer.addWidget(self._view, stretch=1)

        # ---- legend strip ----------------------------------------------
        self._legend = _SpeedLegend()
        outer.addWidget(self._legend)

        self._path_item = None
        self._trans_item = None
        self._outline_items: list = []
        self._marker_items: list = []
        self._axis_items: list = []

        self._draw_bed()
        self._draw_axes()

    # ------------------------------------------------------------------
    # Bed + axes
    # ------------------------------------------------------------------
    def _draw_bed(self):
        if self._view is None:
            return
        bx, by = self._machine.bed_x_mm, self._machine.bed_y_mm

        verts = np.array([[0,0,0],[bx,0,0],[bx,by,0],[0,by,0]], dtype=np.float32)
        faces = np.array([[0,1,2],[0,2,3]], dtype=np.int32)
        colors = np.full((2, 4), [0.22, 0.22, 0.30, 0.40], dtype=np.float32)
        self._view.addItem(gl.GLMeshItem(vertexes=verts, faces=faces,
                                         faceColors=colors, smooth=False,
                                         drawEdges=False))

        grid = gl.GLGridItem()
        grid.setSize(bx, by)
        grid.setSpacing(20, 20)
        grid.translate(bx / 2, by / 2, 0)
        grid.setColor((70, 70, 100, 50))
        self._view.addItem(grid)

        # Bed border
        border = np.array([[0,0,0],[bx,0,0],[bx,by,0],[0,by,0],[0,0,0]],
                           dtype=np.float32)
        self._view.addItem(gl.GLLinePlotItem(pos=border,
                                              color=(0.5, 0.5, 0.7, 0.6),
                                              width=1.5, antialias=False,
                                              mode='line_strip'))

    def _draw_axes(self):
        if self._view is None:
            return
        L = 30.0
        o = np.array([[0, 0, 0.5]], dtype=np.float32)
        for end, col in [
            (np.array([[L, 0, 0.5]]), (0.9, 0.3, 0.3, 1.0)),   # X red
            (np.array([[0, L, 0.5]]), (0.3, 0.9, 0.3, 1.0)),   # Y green
            (np.array([[0, 0, L]]),   (0.3, 0.5, 1.0, 1.0)),   # Z blue
        ]:
            seg = np.vstack([o, end]).astype(np.float32)
            item = gl.GLLinePlotItem(pos=seg, color=col, width=2.5,
                                      antialias=True, mode='line_strip')
            self._view.addItem(item)
            self._axis_items.append(item)

    # ------------------------------------------------------------------
    # View presets
    # ------------------------------------------------------------------
    def _centre(self):
        bx, by = self._machine.bed_x_mm, self._machine.bed_y_mm
        return (bx / 2, by / 2, self._last_max_z / 2)

    def view_iso(self):
        if self._view is None:
            return
        self._view.setCameraPosition(elevation=30, azimuth=-50)

    def view_top(self):
        if self._view is None:
            return
        self._view.setCameraPosition(elevation=90, azimuth=0)

    def view_front(self):
        if self._view is None:
            return
        self._view.setCameraPosition(elevation=0, azimuth=0)

    def view_right(self):
        if self._view is None:
            return
        self._view.setCameraPosition(elevation=0, azimuth=-90)

    def fit(self):
        if self._view is None:
            return
        bx, by = self._machine.bed_x_mm, self._machine.bed_y_mm
        dist = max(bx, by, self._last_max_z) * 1.8
        self._view.setCameraPosition(distance=dist, elevation=30, azimuth=-50)

    # ------------------------------------------------------------------
    # Scene update
    # ------------------------------------------------------------------
    def update_scene(self, trajectory: list[dict], target_shapes: list,
                     max_z: float):
        if self._view is None:
            return
        self._clear_trajectory()
        if not trajectory:
            if self._legend:
                self._legend.clear()
            return

        self._last_max_z = max(max_z, 1.0)

        all_pts = np.array([[p['x'], p['y'], p['z']] for p in trajectory],
                            dtype=np.float32)
        all_vs  = np.array([p['v'] for p in trajectory], dtype=np.float32)
        is_trans = np.array([bool(p.get('is_transition')) for p in trajectory])

        # --- fiber path (non-transition) ---------------------------------
        fiber_mask = ~is_trans
        if fiber_mask.any():
            f_pts = _decimate(all_pts[fiber_mask], MAX_VIEWER_POINTS)
            f_vs  = _decimate(all_vs[fiber_mask],  MAX_VIEWER_POINTS)
            colors = _speed_to_color(f_vs)
            self._path_item = gl.GLLinePlotItem(pos=f_pts, color=colors,
                                                 width=2.5, antialias=True,
                                                 mode='line_strip')
            self._view.addItem(self._path_item)
            if self._legend:
                self._legend.set_range(float(f_vs.min()), float(f_vs.max()))
        else:
            if self._legend:
                self._legend.clear()

        # --- transition moves (dimmed grey) ------------------------------
        if is_trans.any():
            t_pts = _decimate(all_pts[is_trans], 2000)
            self._trans_item = gl.GLLinePlotItem(
                pos=t_pts,
                color=(0.35, 0.35, 0.45, 0.45),
                width=1.0, antialias=False, mode='line_strip'
            )
            self._view.addItem(self._trans_item)

        # --- start / end markers -----------------------------------------
        s_marker = gl.GLScatterPlotItem(pos=all_pts[:1],
                                         color=(0.15, 0.95, 0.45, 1.0), size=14)
        e_marker = gl.GLScatterPlotItem(pos=all_pts[-1:],
                                         color=(0.95, 0.25, 0.55, 1.0), size=14)
        self._view.addItem(s_marker)
        self._view.addItem(e_marker)
        self._marker_items = [s_marker, e_marker]

        # --- target outlines ---------------------------------------------
        self._draw_outlines(target_shapes)

        # auto-fit camera
        self.fit()

    def _draw_outlines(self, target_shapes):
        for z, geom, _ in target_shapes[:MAX_VIEWER_OUTLINES]:
            if geom is None or geom.is_empty:
                continue
            polys = list(geom.geoms) if geom.geom_type == 'MultiPolygon' else [geom]
            for poly in polys:
                if poly.geom_type != 'Polygon':
                    continue
                rx, ry = poly.exterior.xy
                pts = np.column_stack([
                    np.array(rx, dtype=np.float32),
                    np.array(ry, dtype=np.float32),
                    np.full(len(rx), float(z), dtype=np.float32),
                ])
                item = gl.GLLinePlotItem(pos=pts, color=(0.55, 0.55, 0.75, 0.45),
                                          width=1.0, antialias=False,
                                          mode='line_strip')
                self._view.addItem(item)
                self._outline_items.append(item)

    def _clear_trajectory(self):
        for item in ([self._path_item, self._trans_item]
                     + self._outline_items + self._marker_items):
            if item is not None:
                self._view.removeItem(item)
        self._path_item = None
        self._trans_item = None
        self._outline_items = []
        self._marker_items = []
