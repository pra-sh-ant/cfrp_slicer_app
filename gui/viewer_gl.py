"""3D OpenGL toolpath viewer using pyqtgraph GLViewWidget.

Displays:
  - Print bed (translucent grey mesh)
  - Target boundary outlines (dashed grey)
  - Fiber path coloured by speed (Turbo palette)
  - Start anchor (green) and end nozzle (pink) markers

Layer isolation and playback are handled by passing a pre-filtered
trajectory from the parent widget.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

try:
    import pyqtgraph.opengl as gl
    _HAS_GL = True
except Exception:
    _HAS_GL = False

from cfrp_core.machine import MachineProfile

MAX_VIEWER_POINTS = 8000
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
    """Map speed values to RGBA using a Turbo-like palette."""
    v_min, v_max = v_arr.min(), v_arr.max()
    if v_max > v_min:
        t = (v_arr - v_min) / (v_max - v_min)
    else:
        t = np.zeros_like(v_arr)
    idx = (t * (len(_TURBO) - 1)).astype(int).clip(0, len(_TURBO) - 1)
    rgb = _TURBO[idx]
    alpha = np.ones((len(rgb), 1), dtype=np.float32)
    return np.hstack([rgb, alpha])


def _decimate(arr, max_pts):
    n = len(arr)
    if n <= max_pts:
        return arr
    step = max(1, n // max_pts)
    return arr[::step]


class ViewerGL(QWidget):
    def __init__(self, machine: MachineProfile, parent=None):
        super().__init__(parent)
        self._machine = machine
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        if not _HAS_GL:
            from PySide6.QtWidgets import QLabel
            lbl = QLabel("3D viewer unavailable (pyqtgraph OpenGL not loaded).")
            lbl.setAlignment(Qt.AlignCenter)
            self._layout.addWidget(lbl)
            self._view = None
            return

        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor('#1a1a2e')
        self._view.setCameraPosition(distance=300, elevation=25, azimuth=-60)
        self._layout.addWidget(self._view)

        self._bed_item = None
        self._path_item = None
        self._outline_items: list = []
        self._marker_items: list = []

        self._draw_bed()

    def _draw_bed(self):
        if self._view is None:
            return
        bx = self._machine.bed_x_mm
        by = self._machine.bed_y_mm
        verts = np.array([
            [0, 0, 0], [bx, 0, 0], [bx, by, 0], [0, by, 0]
        ], dtype=np.float32)
        faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        colors = np.array([
            [0.55, 0.55, 0.60, 0.35],
            [0.55, 0.55, 0.60, 0.35],
        ], dtype=np.float32)
        m = gl.GLMeshItem(vertexes=verts, faces=faces, faceColors=colors,
                          smooth=False, drawEdges=False)
        self._view.addItem(m)
        self._bed_item = m

        # Grid lines
        grid = gl.GLGridItem()
        grid.setSize(bx, by)
        grid.setSpacing(20, 20)
        grid.translate(bx / 2, by / 2, 0)
        grid.setColor((80, 80, 100, 60))
        self._view.addItem(grid)

    def update_scene(self, trajectory: list[dict], target_shapes: list,
                     max_z: float):
        if self._view is None:
            return
        self._clear_trajectory()

        if not trajectory:
            return

        # --- fiber path ---
        pts = _decimate(np.array([[p['x'], p['y'], p['z']] for p in trajectory],
                                  dtype=np.float32), MAX_VIEWER_POINTS)
        vs = _decimate(np.array([p['v'] for p in trajectory], dtype=np.float32),
                       MAX_VIEWER_POINTS)
        colors = _speed_to_color(vs)

        path = gl.GLLinePlotItem(pos=pts, color=colors, width=2.5,
                                  antialias=True, mode='line_strip')
        self._view.addItem(path)
        self._path_item = path

        # --- start / end markers ---
        start = np.array([[pts[0, 0], pts[0, 1], pts[0, 2]]], dtype=np.float32)
        end = np.array([[pts[-1, 0], pts[-1, 1], pts[-1, 2]]], dtype=np.float32)
        s_marker = gl.GLScatterPlotItem(pos=start, color=(0.1, 0.9, 0.4, 1.0), size=12)
        e_marker = gl.GLScatterPlotItem(pos=end, color=(0.95, 0.25, 0.55, 1.0), size=12)
        self._view.addItem(s_marker)
        self._view.addItem(e_marker)
        self._marker_items = [s_marker, e_marker]

        # --- target outlines ---
        self._draw_outlines(target_shapes)

        self._view.setCameraPosition(pos=None, distance=max(self._machine.bed_x_mm,
                                                             self._machine.bed_y_mm,
                                                             max_z) * 1.8)

    def _draw_outlines(self, target_shapes):
        shapes = target_shapes[:MAX_VIEWER_OUTLINES]
        for z, geom, _ in shapes:
            if geom is None or geom.is_empty:
                continue
            polys = (list(geom.geoms) if geom.geom_type == 'MultiPolygon'
                     else [geom])
            for poly in polys:
                if poly.geom_type != 'Polygon':
                    continue
                rx, ry = poly.exterior.xy
                pts = np.column_stack([
                    np.array(rx, dtype=np.float32),
                    np.array(ry, dtype=np.float32),
                    np.full(len(rx), float(z), dtype=np.float32)
                ])
                item = gl.GLLinePlotItem(pos=pts, color=(0.6, 0.6, 0.7, 0.5),
                                         width=1.0, antialias=False, mode='line_strip')
                self._view.addItem(item)
                self._outline_items.append(item)

    def _clear_trajectory(self):
        for item in [self._path_item] + self._outline_items + self._marker_items:
            if item is not None:
                self._view.removeItem(item)
        self._path_item = None
        self._outline_items = []
        self._marker_items = []
