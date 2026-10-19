"""3D toolpath viewer (pyqtgraph GLViewWidget + a custom shader item).

Toolpaths are drawn as shaded tubes with the bead's cross-section, built
once into GPU buffers (gui.toolpath_mesh); the layer range slider and the
move scrubber only change which index range is drawn. Layers below the
range can stay visible, dimmed. Colour modes: feature, speed, layer, height,
flow, bend radius (red below r_min), fibre direction, tool.

Mouse: left-drag rotate · middle/shift-drag pan · wheel zoom · click a
toolpath point to inspect it. Keys (view focused): 0 iso, 1 top, 2 bottom,
3 front, 4 rear, 5 left, 6 right, F fit.
"""
from __future__ import annotations

import ctypes
import math

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QMatrix4x4, QPainter, QSurfaceFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    import pyqtgraph.opengl as gl
    from OpenGL import GL
    from pyqtgraph.opengl import shaders as pg_shaders
    from pyqtgraph.opengl.GLGraphicsItem import GLGraphicsItem
    from PySide6.QtOpenGL import QOpenGLBuffer
    _HAS_GL = True
except Exception:          # pragma: no cover - environment without OpenGL
    _HAS_GL = False

from cfrp_core.machine import MachineProfile

from . import toolpath_mesh as tm

MAX_DRAW_POINTS = 400_000

_VERT = """
uniform mat4 u_mvp;
uniform mat4 u_mv;
uniform mat3 u_normal;
attribute vec4 a_position;
attribute vec3 a_normal;
attribute vec4 a_color;
varying vec4 v_color;
varying vec3 v_normal;
varying vec3 v_eye;
void main() {
    v_normal = normalize(u_normal * a_normal);
    v_eye = (u_mv * a_position).xyz;
    v_color = a_color;
    gl_Position = u_mvp * a_position;
}
"""
_FRAG = """
#ifdef GL_ES
precision mediump float;
#endif
varying vec4 v_color;
varying vec3 v_normal;
varying vec3 v_eye;
uniform float u_dim;
uniform float u_alpha;
void main() {
    vec3 n = normalize(v_normal);
    if (!gl_FrontFacing) n = -n;
    vec3 v = normalize(-v_eye);
    vec3 key = normalize(vec3(-0.45, 0.65, 0.62));
    float diff = 0.62 * max(dot(n, key), 0.0) + 0.30 * max(dot(n, v), 0.0);
    vec3 h = normalize(key + v);
    float spec = pow(max(dot(n, h), 0.0), 40.0) * 0.32;
    float sky = 0.5 + 0.5 * n.y;
    vec3 ambient = mix(vec3(0.16, 0.15, 0.18), vec3(0.30, 0.31, 0.36), sky);
    vec3 c = v_color.rgb * (ambient + diff) + vec3(spec);
    float grey = dot(c, vec3(0.30, 0.59, 0.11));
    c = mix(vec3(grey) * 0.55 + vec3(0.05, 0.05, 0.08), c, u_dim);
    gl_FragColor = vec4(pow(c, vec3(0.95)), u_alpha);
}
"""


if _HAS_GL:
    def _toolpath_shader():
        """The tube shader, registered under its own name: ShaderProgram(None)
        would replace pyqtgraph's default shader, which the bed mesh uses."""
        prog = pg_shaders.ShaderProgram.names.get("cfrp_toolpath")
        if prog is None:
            prog = pg_shaders.ShaderProgram("cfrp_toolpath", [
                pg_shaders.VertexShader(_VERT), pg_shaders.FragmentShader(_FRAG)])
        return prog

    class ToolpathItem(GLGraphicsItem):
        """Tube mesh with per-vertex colour, drawn over point index ranges:
        `ghost` dimmed, `active` in full colour."""

        def __init__(self, alpha=1.0):
            super().__init__()
            self.alpha = float(alpha)
            self.setGLOptions('opaque' if alpha >= 1.0 else 'translucent')
            self._shader = _toolpath_shader()
            self._bufs = {k: None for k in ("pos", "nrm", "col", "idx")}
            self._data = {}
            self._dirty = set()
            self.mesh = None
            self.active = (0, 0)
            self.ghost = (0, 0)
            self.failed = None

        def set_mesh(self, mesh: tm.TubeMesh, colors: np.ndarray):
            self.mesh = mesh
            self._data.update(pos=np.ascontiguousarray(mesh.vertices), nrm=np.ascontiguousarray(mesh.normals),
                              col=np.ascontiguousarray(colors), idx=np.ascontiguousarray(mesh.faces))
            self._dirty |= {"pos", "nrm", "col", "idx"}
            self.update()

        def set_colors(self, colors: np.ndarray):
            self._data["col"] = np.ascontiguousarray(colors)
            self._dirty.add("col")
            self.update()

        def set_ranges(self, active, ghost=(0, 0)):
            self.active, self.ghost = active, ghost
            self.update()

        def _upload(self):
            for key in list(self._dirty):
                arr = self._data[key]
                buf = self._bufs[key]
                if buf is None:
                    kind = QOpenGLBuffer.Type.IndexBuffer if key == "idx" else QOpenGLBuffer.Type.VertexBuffer
                    buf = self._bufs[key] = QOpenGLBuffer(kind)
                if not buf.isCreated():
                    buf.create()
                buf.bind()
                buf.allocate(arr, arr.nbytes)
                buf.release()
            self._dirty.clear()

        def paint(self):
            if self.mesh is None or not len(self.mesh.faces) or self.failed:
                return
            self.setupGLState()
            try:
                self._upload()
                program = self._shader.program()
            except Exception as exc:          # shader/driver problem: fall back to lines
                self.failed = str(exc)
                return
            locs = []
            for name, key, size, typ, norm in (("a_position", "pos", 3, GL.GL_FLOAT, False),
                                               ("a_normal", "nrm", 3, GL.GL_FLOAT, False),
                                               ("a_color", "col", 4, GL.GL_UNSIGNED_BYTE, True)):
                loc = GL.glGetAttribLocation(program, name)
                if loc == -1:
                    continue
                self._bufs[key].bind()
                GL.glVertexAttribPointer(loc, size, typ, norm, 0, None)
                self._bufs[key].release()
                GL.glEnableVertexAttribArray(loc)
                locs.append(loc)
            mvp = np.array(self.mvpMatrix().data(), dtype=np.float32)
            mv = np.array(self.modelViewMatrix().data(), dtype=np.float32)
            nm = np.array(self.modelViewMatrix().normalMatrix().data(), dtype=np.float32)
            with self._shader:
                GL.glUniformMatrix4fv(GL.glGetUniformLocation(program, "u_mvp"), 1, False, mvp)
                GL.glUniformMatrix4fv(GL.glGetUniformLocation(program, "u_mv"), 1, False, mv)
                GL.glUniformMatrix3fv(GL.glGetUniformLocation(program, "u_normal"), 1, False, nm)
                GL.glUniform1f(GL.glGetUniformLocation(program, "u_alpha"), self.alpha)
                u_dim = GL.glGetUniformLocation(program, "u_dim")
                self._bufs["idx"].bind()
                for (a, b), dim in ((self.ghost, 0.32), (self.active, 1.0)):
                    first, count = self.mesh.face_range(a, b)
                    if count <= 0:
                        continue
                    GL.glUniform1f(u_dim, dim)
                    GL.glDrawElements(GL.GL_TRIANGLES, count * 3, GL.GL_UNSIGNED_INT,
                                      ctypes.c_void_p(first * 3 * 4))
                self._bufs["idx"].release()
            for loc in locs:
                GL.glDisableVertexAttribArray(loc)

    class _View(gl.GLViewWidget):
        """GLViewWidget with orthographic projection, view keys and clicks."""

        clicked = Signal(float, float)
        key_view = Signal(str)

        def __init__(self):
            super().__init__()
            self.ortho = False
            self._press = None

        def projectionMatrix(self, region, viewport):
            if not self.ortho:
                return super().projectionMatrix(region, viewport)
            x0, y0, w, h = viewport
            dist = self.opts['distance']
            r = dist * math.tan(0.5 * math.radians(self.opts['fov']))
            t = r * h / w
            left = r * ((region[0] - x0) * (2.0 / w) - 1)
            right = r * ((region[0] + region[2] - x0) * (2.0 / w) - 1)
            bottom = t * ((region[1] - y0) * (2.0 / h) - 1)
            top = t * ((region[1] + region[3] - y0) * (2.0 / h) - 1)
            tr = QMatrix4x4()
            tr.ortho(left, right, bottom, top, dist * 0.001, dist * 1000.0)
            return tr

        def mousePressEvent(self, ev):
            self._press = ev.position()
            super().mousePressEvent(ev)

        def mouseReleaseEvent(self, ev):
            p = ev.position()
            if self._press is not None and (p - self._press).manhattanLength() < 4 \
                    and ev.button() == Qt.LeftButton:
                self.clicked.emit(p.x(), p.y())
            self._press = None
            super().mouseReleaseEvent(ev)

        def keyPressEvent(self, ev):
            name = {Qt.Key_0: "iso", Qt.Key_1: "top", Qt.Key_2: "bottom", Qt.Key_3: "front",
                    Qt.Key_4: "rear", Qt.Key_5: "left", Qt.Key_6: "right", Qt.Key_F: "fit"}.get(ev.key())
            if name:
                self.key_view.emit(name)
            else:
                super().keyPressEvent(ev)

        def screen_points(self, xyz):
            w, h = max(self.width(), 1), max(self.height(), 1)
            proj = self.projectionMatrix((0, 0, w, h), (0, 0, w, h))
            m = np.array((proj * self.viewMatrix()).data(), dtype=np.float64).reshape(4, 4).T
            p = np.c_[xyz, np.ones(len(xyz))] @ m.T
            wcl = np.where(np.abs(p[:, 3]) < 1e-12, 1e-12, p[:, 3])
            ndc = p[:, :3] / wcl[:, None]
            out = np.c_[(ndc[:, 0] + 1) * 0.5 * w, (1 - ndc[:, 1]) * 0.5 * h]
            out[(p[:, 3] <= 0) | (np.abs(ndc[:, 2]) > 1)] = np.nan
            return out


# ---------------------------------------------------------------------------
# Legend
# ---------------------------------------------------------------------------
class Legend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._legend = {"kind": "none"}

    def set_legend(self, legend):
        self._legend = legend or {"kind": "none"}
        self.update()

    def paintEvent(self, _):
        lg = self._legend
        if lg.get("kind") == "none":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = p.fontMetrics()
        h = self.height()
        if lg["kind"] == "gradient":
            lo, hi = f"{lg['lo']:.3g}", f"{lg['hi']:.3g} {lg['unit']}"
            x0 = 8 + fm.horizontalAdvance(lo) + 6
            x1 = self.width() - fm.horizontalAdvance(hi) - 14
            if lg.get("note"):
                x1 -= fm.horizontalAdvance(lg["note"]) + 16
            if x1 - x0 < 40:
                return
            grad = QLinearGradient(x0, 0, x1, 0)
            table = lg["table"]
            for i, (r, g, b) in enumerate(table):
                grad.setColorAt(i / (len(table) - 1), QColor.fromRgbF(float(r), float(g), float(b)))
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(x0, 5, x1 - x0, 11), 3, 3)
            p.setPen(QColor("#b8bcd8"))
            p.drawText(8, 15, lo)
            p.drawText(int(x1 + 6), 15, hi)
            for t, text in lg.get("ticks", []):
                x = x0 + t * (x1 - x0)
                p.drawLine(int(x), 16, int(x), 19)
                p.drawText(int(x - fm.horizontalAdvance(text) / 2), h - 1, text)
            if lg.get("note"):
                p.setPen(QColor("#f5b942") if "below" in lg["note"] else QColor("#6fd39a"))
                p.drawText(int(self.width() - fm.horizontalAdvance(lg["note"]) - 8), 15, lg["note"])
        else:
            x = 8
            for label, rgb in lg["items"]:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor.fromRgbF(*map(float, rgb)))
                p.drawRoundedRect(QRectF(x, 8, 12, 12), 2, 2)
                p.setPen(QColor("#c0c4dc"))
                p.drawText(x + 17, 18, label)
                x += 17 + fm.horizontalAdvance(label) + 16
                if x > self.width() - 40:
                    break
        p.end()


# ---------------------------------------------------------------------------
# Viewer widget
# ---------------------------------------------------------------------------
class ViewerGL(QWidget):
    """Toolbar + GL view + layer range slider + legend + move scrubber."""

    point_picked = Signal(int)

    def __init__(self, machine: MachineProfile, parent=None):
        super().__init__(parent)
        self._machine = machine
        self._data: tm.ToolpathData | None = None
        self._mesh = None
        self._layers: dict[int, tuple[int, int]] = {}
        self._layer_z: dict[int, float] = {}
        self._outlines = []
        self._r_min = 3.0
        self._fit_box = None
        self._cum_t = None
        self._cursor = 0
        self._playing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        # ---- toolbar ------------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(4)

        def _btn(label, tip, cb, w=48):
            b = QPushButton(label)
            b.setObjectName("viewBtn")
            b.setToolTip(tip)
            b.setFixedHeight(24)
            b.setMinimumWidth(w)
            b.clicked.connect(cb)
            bar.addWidget(b)
            return b

        _btn("Iso", "Isometric view (0)", lambda: self.set_view("iso"))
        _btn("Top", "Top view (1)", lambda: self.set_view("top"))
        _btn("Front", "Front view (3)", lambda: self.set_view("front"))
        _btn("Side", "Right view (6)", lambda: self.set_view("right"))
        _btn("Fit", "Fit the part in view (F)", self.fit)
        self.ortho_chk = QCheckBox("Ortho")
        self.ortho_chk.setToolTip("Orthographic projection (no perspective)")
        self.ortho_chk.toggled.connect(self._set_ortho)
        bar.addWidget(self.ortho_chk)
        bar.addSpacing(10)
        bar.addWidget(QLabel("Colour:"))
        self.mode_cb = QComboBox()
        self.mode_cb.addItems(list(tm.MODES))
        self.mode_cb.setToolTip("What the toolpath colour shows")
        self.mode_cb.currentTextChanged.connect(self._recolor)
        bar.addWidget(self.mode_cb)
        self.ghost_chk = QCheckBox("Lower layers")
        self.ghost_chk.setChecked(True)
        self.ghost_chk.setToolTip("Show layers below the range, dimmed")
        self.ghost_chk.toggled.connect(self._apply_range)
        bar.addWidget(self.ghost_chk)
        self.outline_chk = QCheckBox("Outlines")
        self.outline_chk.setChecked(True)
        self.outline_chk.setToolTip("Show each layer's part outline")
        self.outline_chk.toggled.connect(self._apply_range)
        bar.addWidget(self.outline_chk)
        self.resin_chk = QCheckBox("Resin")
        self.resin_chk.setChecked(True)
        self.resin_chk.setToolTip("Feature colours: draw the resin coat (amber, see-through) round the "
                                  "carbon fibre core")
        self.resin_chk.toggled.connect(self._recolor)
        bar.addWidget(self.resin_chk)
        self.travel_chk = QCheckBox("Travel")
        self.travel_chk.setChecked(True)
        self.travel_chk.setToolTip("Show DIW travel moves (no ink)")
        self.travel_chk.toggled.connect(self._apply_range)
        bar.addWidget(self.travel_chk)
        bar.addStretch()
        _btn("Save image…", "Save the view as PNG", self.save_screenshot, 90)
        outer.addLayout(bar)

        # ---- GL view + range slider ------------------------------------------
        mid = QHBoxLayout()
        mid.setSpacing(2)
        if not _HAS_GL:
            lbl = QLabel("3D viewer unavailable (pyqtgraph OpenGL not loaded).")
            lbl.setAlignment(Qt.AlignCenter)
            mid.addWidget(lbl, 1)
            self._view = None
        else:
            self._view = _View()
            fmt = QSurfaceFormat()
            fmt.setSamples(4)
            fmt.setDepthBufferSize(24)
            self._view.setFormat(fmt)
            self._view.setBackgroundColor('#15151f')
            self._view.setCameraPosition(distance=300, elevation=30, azimuth=-50)
            self._view.clicked.connect(self._pick)
            self._view.key_view.connect(lambda n: self.fit() if n == "fit" else self.set_view(n))
            mid.addWidget(self._view, 1)
        from .range_slider import RangeSlider
        self.range = RangeSlider()
        self.range.labels = self._layer_label
        self.range.rangeChanged.connect(lambda lo, hi: self._apply_range())
        mid.addWidget(self.range)
        outer.addLayout(mid, 1)

        # ---- legend ------------------------------------------------------------
        self.legend = Legend()
        outer.addWidget(self.legend)

        # ---- scrubber ------------------------------------------------------------
        bot = QHBoxLayout()
        bot.setSpacing(6)
        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("viewBtn")
        self.play_btn.setFixedSize(30, 24)
        self.play_btn.setToolTip("Play the print (moves through the layers in time)")
        self.play_btn.clicked.connect(self._toggle_play)
        bot.addWidget(self.play_btn)
        self.speed_cb = QComboBox()
        self.speed_cb.addItems(["1x", "5x", "20x", "100x", "500x"])
        self.speed_cb.setCurrentText("20x")
        self.speed_cb.setToolTip("Playback speed (real print time)")
        bot.addWidget(self.speed_cb)
        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setToolTip("Moves within the top visible layer")
        self.scrub.valueChanged.connect(self._on_scrub)
        bot.addWidget(self.scrub, 1)
        self.info = QLabel("")
        self.info.setObjectName("viewInfo")
        self.info.setMinimumWidth(360)
        bot.addWidget(self.info)
        outer.addLayout(bot)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        self._tube = None
        self._resin = None
        self._end = None
        self._lines = None
        self._travel = None
        self._outline_item = None
        self._nozzle = None
        self._start = None
        if self._view is not None:
            self._draw_bed()

    # ------------------------------------------------------------------
    # Static scene
    # ------------------------------------------------------------------
    def set_machine(self, machine):
        """Redraw the bed for another machine profile."""
        self._machine = machine
        if self._view is None:
            return
        for item in getattr(self, "_bed_items", []):
            self._view.removeItem(item)
        self._draw_bed(markers=False)

    def _draw_bed(self, markers=True):
        bx, by = self._machine.bed_x_mm, self._machine.bed_y_mm
        verts = np.array([[0, 0, -0.02], [bx, 0, -0.02], [bx, by, -0.02], [0, by, -0.02]], dtype=np.float32)
        faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        colors = np.full((2, 4), [0.17, 0.17, 0.23, 1.0], dtype=np.float32)
        self._bed_items = [gl.GLMeshItem(vertexes=verts, faces=faces, faceColors=colors,
                                         smooth=False, drawEdges=False)]
        self._view.addItem(self._bed_items[0])
        grid = gl.GLGridItem()
        grid.setSize(bx, by)
        grid.setSpacing(10, 10)
        grid.translate(bx / 2, by / 2, 0)
        grid.setColor((80, 80, 110, 60))
        self._view.addItem(grid)
        self._bed_items.append(grid)
        border = np.array([[0, 0, 0], [bx, 0, 0], [bx, by, 0], [0, by, 0], [0, 0, 0]], dtype=np.float32)
        edge = gl.GLLinePlotItem(pos=border, color=(0.45, 0.47, 0.65, 0.8), width=1.5,
                                 antialias=True, mode='line_strip')
        self._view.addItem(edge)
        self._bed_items.append(edge)
        if not markers:
            return
        for end, col in (((25, 0, 0.05), (0.9, 0.3, 0.3, 1)), ((0, 25, 0.05), (0.3, 0.85, 0.35, 1)),
                         ((0, 0, 25), (0.35, 0.55, 1.0, 1))):
            self._view.addItem(gl.GLLinePlotItem(pos=np.array([(0, 0, 0.05), end], dtype=np.float32),
                                                 color=col, width=2.5, antialias=True))
        md = gl.MeshData.cylinder(rows=1, cols=16, radius=[0.0, 1.6], length=4.0)
        self._nozzle = gl.GLMeshItem(meshdata=md, smooth=True, shader='shaded',
                                     color=(0.92, 0.94, 1.0, 1.0), glOptions='opaque')
        self._nozzle.setVisible(False)
        self._view.addItem(self._nozzle)
        self._start = gl.GLScatterPlotItem(pos=np.zeros((1, 3)), color=(0.2, 0.95, 0.5, 1), size=12)
        self._start.setVisible(False)
        self._view.addItem(self._start)
        self._end = gl.GLScatterPlotItem(pos=np.zeros((1, 3)), color=(0.98, 0.2, 0.3, 1), size=12)
        self._end.setVisible(False)
        self._view.addItem(self._end)

    # ------------------------------------------------------------------
    # Scene update
    # ------------------------------------------------------------------
    def update_scene(self, trajectory, target_shapes, max_z, bead_width=1.0, bead_height=0.2,
                     r_min=3.0, extra=None):
        """Show a slice result. `extra`: further ToolpathData (e.g. DIW)
        drawn after the fibre."""
        self._r_min = float(r_min)
        fibre = tm.from_trajectory(trajectory or [], bead_width, bead_height)
        data = tm.concat([fibre, extra]) if extra is not None else fibre
        self._outlines = list(target_shapes or [])
        if not len(data):
            self.clear()
            return
        keep = tm.simplify(data)
        tol = 1.0
        while len(keep) > MAX_DRAW_POINTS and tol < 16:
            tol *= 2
            keep = tm.simplify(data, angle_deg=tol, max_len=1.5 * tol)
        self._data = data = data.take(keep)
        self._layers = data.layer_ranges()
        self._layer_z = {lay: float(np.median(data.xyz[a:b, 2])) for lay, (a, b) in self._layers.items()}
        dist = np.r_[0.0, np.linalg.norm(np.diff(data.xyz, axis=0), axis=1)]
        speed = np.maximum(data.v.astype(np.float64), 0.5)
        dt = np.where(data.breaks, dist / 20.0, dist / speed)
        self._cum_t = np.cumsum(dt)
        lays = sorted(self._layers)
        self.range.blockSignals(True)
        self.range.setRange(lays[0], lays[-1])
        self.range.blockSignals(False)
        if self._view is None:
            return
        self._build_meshes()
        box = (data.xyz.min(axis=0), data.xyz.max(axis=0))
        # Re-frame only when the part itself moved or resized (>8 %), so a
        # re-slice after a speed tweak keeps the user's zoom.
        size = max(1.0, float(np.ptp(data.xyz[:, :2], axis=0).max()))
        if self._fit_box is None or not np.allclose(np.r_[box[0], box[1]], np.r_[self._fit_box[0], self._fit_box[1]],
                                                    atol=0.08 * size):
            self._fit_box = box
            self.fit()
        self._start.setData(pos=data.xyz[:1])
        self._start.setVisible(True)
        fib_idx = np.nonzero(data.tool == 0)[0]
        if len(fib_idx):
            self._end.setData(pos=data.xyz[fib_idx[-1]:fib_idx[-1] + 1])
            self._end.setVisible(True)
        self._apply_range()

    def clear(self):
        self._data = None
        self._layers = {}
        for item in (self._tube, self._resin, self._lines, self._travel, self._outline_item):
            if item is not None and self._view is not None:
                self._view.removeItem(item)
        self._tube = self._resin = self._lines = self._travel = self._outline_item = None
        self.legend.set_legend(None)
        self.info.setText("")
        if self._nozzle is not None:
            self._nozzle.setVisible(False)
            self._start.setVisible(False)
            self._end.setVisible(False)

    def _build_meshes(self):
        data = self._data
        for item in (self._tube, self._resin, self._lines, self._travel):
            if item is not None:
                self._view.removeItem(item)
        self._tube = self._resin = self._lines = self._travel = None
        sides = tm.sides_for(len(data))
        # With the resin coat on, the coloured tube is the fibre tow inside
        # the resin bead; otherwise it is the whole bead.
        self._core_mode = self._resin_wanted()
        if self._core_mode:
            self._mesh = tm.tube_mesh(data, sides=sides, width_scale=tm.FIBRE_CORE[0],
                                      height_scale=tm.FIBRE_CORE[1])
        else:
            self._mesh = tm.tube_mesh(data, sides=sides, width_scale=0.95, height_scale=0.95)
        rgb, legend = tm.mode_colors(self.mode_cb.currentText(), data, self._r_min)
        self.legend.set_legend(legend)
        self._tube = ToolpathItem()
        self._tube.set_mesh(self._mesh, tm.vertex_colors(rgb, sides))
        self._view.addItem(self._tube)
        # Resin coat: a slightly larger see-through tube round the fibre.
        if (data.tool == 0).any():
            import dataclasses
            fibre_only = dataclasses.replace(data, breaks=data.breaks | (data.tool != 0))
            resin_mesh = tm.tube_mesh(fibre_only, sides=sides, width_scale=1.0, height_scale=1.0)
            col = np.tile(np.array(tm.RESIN, dtype=np.float32), (len(data), 1))
            col[data.tool != 0] = 0.0
            self._resin = ToolpathItem(alpha=0.33)
            self._resin.setDepthValue(10)
            self._resin.set_mesh(resin_mesh, tm.vertex_colors(col, sides))
            self._view.addItem(self._resin)
            self._resin.setVisible(self._resin_wanted())
        # DIW travel moves as thin lines
        tr = np.nonzero(data.breaks[1:])[0]
        if len(tr):
            seg = np.empty((2 * len(tr), 3), dtype=np.float32)
            seg[0::2], seg[1::2] = data.xyz[tr], data.xyz[tr + 1]
            self._travel = gl.GLLinePlotItem(pos=seg, color=(0.55, 0.56, 0.7, 0.55), width=1.0,
                                             antialias=True, mode='lines')
            self._travel.travel_index = tr
            self._view.addItem(self._travel)

    def _resin_wanted(self):
        return self.resin_chk.isChecked() and self.mode_cb.currentText() == "Feature" \
            and self._data is not None and not (self._data.tool != 0).any()

    def _recolor(self, *_):
        if self._data is not None and self._view is not None and                 getattr(self, "_core_mode", None) is not None and self._core_mode != self._resin_wanted():
            self._build_meshes()            # core <-> full-bead tube
            self._apply_range()
            return
        if self._resin is not None:
            self._resin.setVisible(self._resin_wanted())
        if self._data is None or self._tube is None:
            return
        rgb, legend = tm.mode_colors(self.mode_cb.currentText(), self._data, self._r_min)
        self.legend.set_legend(legend)
        self._tube.set_colors(tm.vertex_colors(rgb, self._mesh.sides))
        if self._lines is not None:
            self._view.removeItem(self._lines)
            self._lines = None
        self._apply_range()

    def _layer_label(self, layer):
        z = self._layer_z.get(layer)
        return f"{layer}" if z is None else f"{layer}  {z:.2f}"

    # ------------------------------------------------------------------
    # Ranges, scrubber, playback
    # ------------------------------------------------------------------
    def _point_range(self):
        lo, hi = self.range.values()
        if not self._layers:
            return 0, 0, 0
        lays = [k for k in self._layers if lo <= k <= hi]
        if not lays:
            return 0, 0, 0
        a = min(self._layers[k][0] for k in lays)
        b = max(self._layers[k][1] for k in lays)
        top_a = self._layers[max(lays)][0]
        return a, b, top_a

    def _apply_range(self, *_):
        if self._data is None:
            return
        a, b, top_a = self._point_range()
        self.scrub.blockSignals(True)
        self.scrub.setRange(top_a + 1, b)
        if not self._playing or not (top_a < self._cursor <= b):
            self._cursor = b
        self.scrub.setValue(self._cursor)
        self.scrub.blockSignals(False)
        self._draw_to(self._cursor)

    def _draw_to(self, end):
        a, b, _ = self._point_range()
        end = max(a + 1, min(int(end), b))
        self._cursor = end
        ghost = (0, a + 1) if self.ghost_chk.isChecked() and a > 0 else (0, 0)
        if self._tube is not None:
            self._tube.set_ranges((a, end), ghost)
            if self._resin is not None:
                self._resin.set_ranges((a, end), ghost)
            if self._tube.failed and self._lines is None:
                self._fallback_lines()
        if self._lines is not None:
            self._lines.setData(pos=self._data.xyz[a:end])
        if self._travel is not None:
            idx = self._travel.travel_index
            vis = (idx >= a) & (idx + 1 < end)
            self._travel.setVisible(self.travel_chk.isChecked() and vis.any())
        self._draw_outlines()
        if self._nozzle is not None and end > 0:
            x, y, z = self._data.xyz[end - 1].tolist()
            self._nozzle.resetTransform()
            self._nozzle.translate(x, y, z + 0.05)
            self._nozzle.setVisible(end < b or self._playing)
        self._show_info(end - 1)

    def _fallback_lines(self):
        rgb, _ = tm.mode_colors(self.mode_cb.currentText(), self._data, self._r_min)
        col = np.c_[rgb, np.ones(len(rgb), dtype=np.float32)]
        self._lines = gl.GLLinePlotItem(pos=self._data.xyz, color=col, width=2.0, antialias=True,
                                        mode='line_strip')
        self._view.addItem(self._lines)

    def _draw_outlines(self):
        if self._outline_item is not None:
            self._view.removeItem(self._outline_item)
            self._outline_item = None
        if not self.outline_chk.isChecked() or not self._outlines:
            return
        lo, hi = self.range.values()
        segs = []
        shapes = [s for s in self._outlines if len(s) > 2 and lo <= s[2] <= hi]
        step = max(1, len(shapes) // 150)
        for z, geom, _ in shapes[::step]:
            if geom is None or geom.is_empty:
                continue
            for poly in getattr(geom, 'geoms', [geom]):
                if poly.geom_type != 'Polygon':
                    continue
                for ring in [poly.exterior, *poly.interiors]:
                    c = np.asarray(ring.coords, dtype=np.float32)[:, :2]
                    pts = np.c_[c, np.full(len(c), float(z), dtype=np.float32)]
                    segs.append(np.repeat(pts, 2, axis=0)[1:-1])
        if segs:
            self._outline_item = gl.GLLinePlotItem(pos=np.vstack(segs), color=(0.55, 0.58, 0.8, 0.45),
                                                   width=1.0, antialias=True, mode='lines')
            self._view.addItem(self._outline_item)

    def _on_scrub(self, value):
        self._draw_to(value)

    def _toggle_play(self):
        self._playing = not self._playing
        self.play_btn.setText("❚❚" if self._playing else "▶")
        if self._playing:
            a, b, _ = self._point_range()
            if self._cursor >= b:
                lo = self.range.values()[0]
                self.range.setValues(lo, lo)
                self._cursor = self._point_range()[0] + 1
            self._timer.start()
        else:
            self._timer.stop()
            self._draw_to(self._cursor)

    def _tick(self):
        if self._data is None:
            return self._toggle_play()
        factor = float(self.speed_cb.currentText().rstrip("x"))
        t_now = self._cum_t[self._cursor - 1] + self._timer.interval() / 1000.0 * factor
        nxt = int(np.searchsorted(self._cum_t, t_now)) + 1
        a, b, _ = self._point_range()
        if nxt > b:
            lo, hi = self.range.values()
            lays = sorted(self._layers)
            if hi >= lays[-1]:
                self._cursor = b
                return self._toggle_play()
            self.range.blockSignals(True)
            self.range.setValues(lo, hi + 1, emit=False)
            self.range.blockSignals(False)
            a, b, top_a = self._point_range()
            self.scrub.blockSignals(True)
            self.scrub.setRange(top_a + 1, b)
            self.scrub.blockSignals(False)
        self.scrub.blockSignals(True)
        self.scrub.setValue(min(nxt, b))
        self.scrub.blockSignals(False)
        self._draw_to(min(nxt, b))

    def _show_info(self, i):
        d = self._data
        if d is None or not (0 <= i < len(d)):
            self.info.setText("")
            return
        x, y, z = d.xyz[i].tolist()
        r = d.bend_radii()[i]
        bend = "straight" if r >= 9000 else f"R {r:.1f} mm"
        feat = tm.FEATURES[int(d.feature[i])][1]
        self.info.setText(f"L{int(d.layer[i])} · {feat} · X {x:.2f} Y {y:.2f} Z {z:.2f} · "
                          f"{float(d.v[i]):.1f} mm/s · {bend}")

    def _pick(self, px, py):
        if self._data is None or self._view is None:
            return
        a, _, _ = self._point_range()
        end = self._cursor
        lo = 0 if self.ghost_chk.isChecked() else a
        cand = np.arange(lo, end)
        if not len(cand):
            return
        scr = self._view.screen_points(self._data.xyz[cand].astype(np.float64))
        ok = ~np.isnan(scr[:, 0])
        k = tm.nearest_point(scr[ok], (px, py), candidates=cand[ok])
        if k is not None:
            self._show_info(k)
            self.point_picked.emit(int(self._data.source[k]))

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------
    def show_layers(self, lo, hi=None):
        self.range.setValues(lo, lo if hi is None else hi)

    def set_view(self, name):
        if self._view is None:
            return
        elev, azim = {"iso": (30, -50), "top": (90, -90), "bottom": (-90, -90), "front": (0, -90),
                      "rear": (0, 90), "left": (0, 180), "right": (0, 0)}[name]
        self._view.setCameraPosition(elevation=elev, azimuth=azim)

    def view_iso(self):
        self.set_view("iso")

    def view_top(self):
        self.set_view("top")

    def fit(self):
        if self._view is None:
            return
        if self._fit_box is None:
            bx, by = self._machine.bed_x_mm, self._machine.bed_y_mm
            lo, hi = np.array([0, 0, 0.0]), np.array([bx, by, 10.0])
        else:
            lo, hi = self._fit_box
        centre = (np.asarray(lo) + np.asarray(hi)) / 2.0
        size = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)))
        from pyqtgraph import Vector
        self._view.opts['center'] = Vector(*centre.tolist())
        fov = math.radians(self._view.opts['fov'])
        self._view.setCameraPosition(distance=max(size, 10.0) * 0.85 / math.tan(fov / 2))

    def _set_ortho(self, on):
        if self._view is not None:
            self._view.ortho = on
            self._view.update()

    def save_screenshot(self, path=None):
        if self._view is None:
            return None
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save view", "toolpath.png", "PNG (*.png)")
        if path:
            self._view.grabFramebuffer().save(path)
        return path

