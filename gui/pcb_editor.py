"""Structural PCB editor: draw the board outline, holes, traces, pads and
zones on a mm grid, check them (live DRC) and send the board to the slicer
(fibre laminate of the outline + DIW electronics on top).

The editor edits an immutable `cfrp_core.diw.BoardDesign`; every change
makes a new design and pushes the old one on the undo stack, and the scene
is redrawn from the design (simple and always consistent).

Canvas: wheel zoom · middle-drag (or Space+drag) pan · Esc cancels ·
Enter / double-click / right-click finishes a polyline · Delete removes the
selection · Ctrl+Z / Ctrl+Y undo / redo.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shapely.geometry import LineString, Point, Polygon

from cfrp_core.diw import (
    FOOTPRINTS,
    BoardDesign,
    DiwConfig,
    Hole,
    Pad,
    Trace,
    Zone,
    drc,
    example_board,
    footprint,
    interdigitated,
    load_design,
    meander,
    save_design,
    spiral_coil,
)
from cfrp_core.diw.design import pad_shape, trace_shape

TOOLS = (
    ("select", "Select", "Select / move (drag) · Delete removes"),
    ("rect", "Board ▭", "Board outline: drag a rectangle"),
    ("rrect", "Board ▢", "Board outline: drag a rounded rectangle (corner radius = Min Bend Radius x 2)"),
    ("polygon", "Board ⬠", "Board outline: click the corners, Enter / double-click to finish"),
    ("hole", "Hole", "Mounting hole / via: click (diameter below)"),
    ("trace", "Trace", "Conductive trace: click points, Enter / double-click / right-click to finish"),
    ("pad", "Pad", "Pad: click (size and shape below)"),
    ("zone", "Zone", "Zone: click corners (conductor fill, extra dielectric, or keepout)"),
    ("footprint", "Part", "Place the footprint chosen below"),
)

_COL = {
    "board": QColor("#2b2f38"), "board_edge": QColor("#8d96b8"), "hole": QColor("#0d0d12"),
    "trace": QColor("#c9cdd8"), "pad": QColor("#e2c26a"), "zone_conductor": QColor(201, 205, 216, 90),
    "zone_dielectric": QColor(76, 200, 150, 70), "zone_keepout": QColor(240, 90, 90, 60),
    "sel": QColor("#5b8def"), "draft": QColor("#f5b942"), "grid": QColor(255, 255, 255, 18),
    "grid10": QColor(255, 255, 255, 38), "err": QColor("#f87171"),
}


class _Canvas(QGraphicsView):
    """mm canvas, Y up. Emits clicks/drags in mm; draws the grid itself."""

    pressed = Signal(float, float, object)        # x, y, Qt.MouseButton
    moved = Signal(float, float, bool)            # x, y, button held
    released = Signal(float, float)
    double = Signal(float, float)
    key = Signal(int)

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.scale(8, -8)
        self.grid = 1.0
        self._pan = None
        self._space = False
        self.setBackgroundBrush(QColor("#15151f"))

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        step = self.grid
        px = step * abs(self.transform().m11())
        while px < 6:
            step *= 5
            px = step * abs(self.transform().m11())
        x0 = math.floor(rect.left() / step) * step
        y0 = math.floor(rect.top() / step) * step
        for i, col in ((1, _COL["grid"]), (10, _COL["grid10"])):
            s = step * i
            pen = QPen(col, 0)
            painter.setPen(pen)
            x = math.floor(rect.left() / s) * s
            while x <= rect.right():
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += s
            y = math.floor(rect.top() / s) * s
            while y <= rect.bottom():
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                y += s
        del x0, y0
        painter.setPen(QPen(QColor(230, 80, 80, 160), 0))
        painter.drawLine(QPointF(0, 0), QPointF(10, 0))
        painter.setPen(QPen(QColor(80, 220, 110, 160), 0))
        painter.drawLine(QPointF(0, 0), QPointF(0, 10))

    def wheelEvent(self, ev):
        f = 1.2 if ev.angleDelta().y() > 0 else 1 / 1.2
        self.scale(f, f)

    def _mm(self, ev):
        p = self.mapToScene(ev.position().toPoint())
        return p.x(), p.y()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MiddleButton or (self._space and ev.button() == Qt.LeftButton):
            self._pan = ev.position()
            return
        x, y = self._mm(ev)
        self.pressed.emit(x, y, ev.button())

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            d = ev.position() - self._pan
            self._pan = ev.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        x, y = self._mm(ev)
        self.moved.emit(x, y, bool(ev.buttons() & Qt.LeftButton))

    def mouseReleaseEvent(self, ev):
        if self._pan is not None:
            self._pan = None
            return
        x, y = self._mm(ev)
        self.released.emit(x, y)

    def mouseDoubleClickEvent(self, ev):
        x, y = self._mm(ev)
        self.double.emit(x, y)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = True
        self.key.emit(ev.key())
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = False
        super().keyReleaseEvent(ev)


def _spin(lo, hi, val, step=0.1, dec=2, suffix=" mm"):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(val)
    if suffix:
        w.setSuffix(suffix)
    return w


class PcbEditor(QWidget):
    """Board editor. `slice_requested(design, diw_config)` asks the main
    window to slice fibre + ink; `design_changed` fires on every edit."""

    slice_requested = Signal(object, object)
    design_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.design = example_board()
        self._undo: list[BoardDesign] = []
        self._redo: list[BoardDesign] = []
        self._tool = "select"
        self._draft: list[tuple[float, float]] = []
        self._drag_from = None
        self._sel = None              # ("trace"|"pad"|"hole"|"zone", index)
        self._cursor = (0.0, 0.0)
        self._min_radius = 3.0
        self.path = None

        self.scene = QGraphicsScene(self)
        self.canvas = _Canvas(self.scene)
        self.canvas.pressed.connect(self._press)
        self.canvas.moved.connect(self._move)
        self.canvas.released.connect(self._release)
        self.canvas.double.connect(lambda x, y: self._finish())
        self.canvas.key.connect(self._key)

        # ---- left: tools ------------------------------------------------------
        tools = QVBoxLayout()
        tools.setSpacing(3)
        self._tool_btns = QButtonGroup(self)
        self._tool_btns.setExclusive(True)
        for key, label, tip in TOOLS:
            b = QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setMinimumWidth(92)
            b.setChecked(key == "select")
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            self._tool_btns.addButton(b)
            tools.addWidget(b)
        tools.addSpacing(8)
        form = QFormLayout()
        self.grid_spin = _spin(0.1, 10, 1.0, 0.1, 2)
        self.grid_spin.valueChanged.connect(self._set_grid)
        form.addRow("Grid", self.grid_spin)
        self.width_spin = _spin(0.2, 20, 0.8)
        form.addRow("Trace W", self.width_spin)
        self.net_edit = QLineEdit()
        self.net_edit.setPlaceholderText("net name")
        form.addRow("Net", self.net_edit)
        self.hole_spin = _spin(0.5, 30, 3.2)
        form.addRow("Hole Ø", self.hole_spin)
        self.pad_w = _spin(0.2, 30, 1.6)
        self.pad_h = _spin(0.2, 30, 1.6)
        form.addRow("Pad W", self.pad_w)
        form.addRow("Pad H", self.pad_h)
        self.pad_shape = QComboBox()
        self.pad_shape.addItems(["rect", "circle"])
        form.addRow("Pad", self.pad_shape)
        self.zone_kind = QComboBox()
        self.zone_kind.addItems(["conductor", "dielectric", "keepout"])
        form.addRow("Zone", self.zone_kind)
        self.fp_combo = QComboBox()
        self.fp_combo.addItems(list(FOOTPRINTS))
        form.addRow("Part", self.fp_combo)
        self.fp_angle = _spin(-360, 360, 0, 90, 0, "°")
        form.addRow("Rotate", self.fp_angle)
        self.fp_ref = QLineEdit("U1")
        form.addRow("Ref", self.fp_ref)
        tools.addLayout(form)
        tools.addSpacing(6)
        for label, cb in (("Strain gauge…", self._add_meander), ("Interdigitated…", self._add_ide),
                          ("Spiral coil…", self._add_coil)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            tools.addWidget(b)
        tools.addStretch()
        left = QWidget()
        left.setLayout(tools)
        left.setMaximumWidth(230)

        # ---- top bar ------------------------------------------------------------
        top = QHBoxLayout()
        for label, cb, tip in (("New", self.new_design, "Empty 60 x 40 board"),
                               ("Example", self.load_example, "Example structural panel"),
                               ("Open…", self.open_design, "Open a .spcb.json design"),
                               ("Save…", self.save_design_as, "Save the design"),
                               ("Undo", self.undo, "Ctrl+Z"), ("Redo", self.redo, "Ctrl+Y"),
                               ("Delete", self.delete_selected, "Delete the selection"),
                               ("Fit", self.fit, "Fit the board in view")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            top.addWidget(b)
        top.addStretch()
        self.coord_lbl = QLabel("")
        self.coord_lbl.setObjectName("viewInfo")
        top.addWidget(self.coord_lbl)
        self.slice_btn = QPushButton("Slice board  ▶")
        self.slice_btn.setObjectName("primary")
        self.slice_btn.setToolTip("Fibre laminate of this outline + DIW electronics on top")
        self.slice_btn.clicked.connect(self._request_slice)
        top.addWidget(self.slice_btn)

        centre = QVBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.addLayout(top)
        centre.addWidget(self.canvas, 1)
        mid = QWidget()
        mid.setLayout(centre)

        # ---- right: properties, DRC, DIW settings ---------------------------------
        right = QVBoxLayout()
        self.prop_box = QGroupBox("Selection")
        self.prop_form = QFormLayout(self.prop_box)
        right.addWidget(self.prop_box)
        drc_box = QGroupBox("Design rules")
        dl = QVBoxLayout(drc_box)
        self.drc_status = QLabel("")
        dl.addWidget(self.drc_status)
        self.drc_list = QListWidget()
        self.drc_list.setWordWrap(True)
        self.drc_list.setObjectName("issues")
        dl.addWidget(self.drc_list)
        right.addWidget(drc_box, 1)
        right.addWidget(self._build_diw_box())
        rw = QWidget()
        rw.setLayout(right)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(rw)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(380)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(split)

        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.Redo, self, self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
        self.redraw()
        self.fit()

    # ------------------------------------------------------------------
    # DIW settings
    # ------------------------------------------------------------------
    def _build_diw_box(self):
        box = QGroupBox("DIW process")
        f = QFormLayout(box)
        d = DiwConfig()
        self.d_nozzle = _spin(0.05, 3, d.nozzle_mm, 0.01, 2)
        self.d_lw = _spin(0.05, 5, d.line_width_mm, 0.05, 2)
        self.d_lh = _spin(0.02, 3, d.layer_height_mm, 0.05, 2)
        self.d_cond = QSpinBox()
        self.d_cond.setRange(1, 10)
        self.d_cond.setValue(d.conductor_layers)
        self.d_diel_on = QCheckBox("Dielectric under ink")
        self.d_diel_on.setChecked(d.dielectric)
        self.d_diel = QSpinBox()
        self.d_diel.setRange(0, 10)
        self.d_diel.setValue(d.dielectric_layers)
        self.d_margin = _spin(0, 10, d.dielectric_margin_mm)
        self.d_enc = QCheckBox("Encapsulate traces")
        self.d_clear = _spin(0, 5, d.clearance_mm, 0.05)
        self.d_edge = _spin(0, 10, d.edge_clearance_mm)
        self.d_vc = _spin(0.1, 100, d.speed_conductor_mm_s, 0.5, 1, " mm/s")
        self.d_vd = _spin(0.1, 100, d.speed_dielectric_mm_s, 0.5, 1, " mm/s")
        self.d_mode = QComboBox()
        self.d_mode.addItems(["pneumatic", "volumetric"])
        self.d_on = QLineEdit(d.dispense_on)
        self.d_off = QLineEdit(d.dispense_off)
        self.d_rho = QLineEdit(f"{d.resistivity_ohm_mm:g}")
        self.d_rho.setToolTip("Cured ink resistivity in Ω·mm (silver flake ~4e-5, carbon ~0.1, EGaIn 3e-4)")
        self.d_carbon = QCheckBox("Laminate is carbon (conductive)")
        self.d_carbon.setChecked(d.substrate_conductive)
        for label, w in (("Nozzle", self.d_nozzle), ("Bead width", self.d_lw), ("Layer height", self.d_lh),
                         ("Conductor layers", self.d_cond), ("", self.d_diel_on), ("Dielectric layers", self.d_diel),
                         ("Dielectric margin", self.d_margin), ("", self.d_enc), ("Net clearance", self.d_clear),
                         ("Edge clearance", self.d_edge), ("Ink speed", self.d_vc), ("Dielectric speed", self.d_vd),
                         ("Dispense", self.d_mode), ("Valve on", self.d_on), ("Valve off", self.d_off),
                         ("Resistivity Ω·mm", self.d_rho), ("", self.d_carbon)):
            f.addRow(label, w)
            for sig in ("valueChanged", "toggled", "currentTextChanged", "editingFinished"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self._run_drc)
                    break
        self.d_mode.currentTextChanged.connect(lambda m: (self.d_on.setEnabled(m == "pneumatic"),
                                                          self.d_off.setEnabled(m == "pneumatic")))
        return box

    def diw_config(self) -> DiwConfig:
        try:
            rho = float(self.d_rho.text())
        except ValueError:
            rho = DiwConfig().resistivity_ohm_mm
        return DiwConfig(
            nozzle_mm=self.d_nozzle.value(), line_width_mm=self.d_lw.value(), layer_height_mm=self.d_lh.value(),
            conductor_layers=self.d_cond.value(), dielectric=self.d_diel_on.isChecked(),
            dielectric_layers=self.d_diel.value(), dielectric_margin_mm=self.d_margin.value(),
            encapsulate=self.d_enc.isChecked(), clearance_mm=self.d_clear.value(),
            edge_clearance_mm=self.d_edge.value(), speed_conductor_mm_s=self.d_vc.value(),
            speed_dielectric_mm_s=self.d_vd.value(), dispense=self.d_mode.currentText(),
            dispense_on=self.d_on.text(), dispense_off=self.d_off.text(),
            resistivity_ohm_mm=rho if rho > 0 else DiwConfig().resistivity_ohm_mm,
            substrate_conductive=self.d_carbon.isChecked())

    # ------------------------------------------------------------------
    # Design editing
    # ------------------------------------------------------------------
    def set_min_radius(self, r):
        self._min_radius = float(r)

    def commit(self, design: BoardDesign):
        if design == self.design:
            return
        self._undo.append(self.design)
        self._undo = self._undo[-200:]
        self._redo.clear()
        self.design = design
        self.redraw()
        self.design_changed.emit(design)

    def undo(self):
        if self._undo:
            self._redo.append(self.design)
            self.design = self._undo.pop()
            self._sel = None
            self.redraw()
            self.design_changed.emit(self.design)

    def redo(self):
        if self._redo:
            self._undo.append(self.design)
            self.design = self._redo.pop()
            self._sel = None
            self.redraw()
            self.design_changed.emit(self.design)

    def set_tool(self, key):
        self._tool = key
        self._draft = []
        for b, (k, _, _) in zip(self._tool_btns.buttons(), TOOLS, strict=False):
            b.setChecked(k == key)
        self.redraw()

    def _set_grid(self, v):
        self.canvas.grid = v
        self.canvas.viewport().update()

    def snap(self, x, y):
        g = self.grid_spin.value()
        return round(x / g) * g, round(y / g) * g

    def _press(self, x, y, button):
        x, y = self.snap(x, y)
        d = self.design
        t = self._tool
        if button == Qt.RightButton:
            self._finish()
            return
        if t == "select":
            self._sel = self.pick(x, y)
            self._drag_from = (x, y) if self._sel else None
            self.redraw()
            self._show_props()
        elif t in ("rect", "rrect"):
            self._drag_from = (x, y)
        elif t in ("polygon", "trace", "zone"):
            if self._draft and math.dist(self._draft[-1], (x, y)) < 1e-9:
                return
            self._draft.append((x, y))
            self.redraw()
        elif t == "hole":
            self.commit(d.model_copy(update=dict(holes=d.holes + (Hole(x=x, y=y, d_mm=self.hole_spin.value()),))))
        elif t == "pad":
            pad = Pad(x=x, y=y, w_mm=self.pad_w.value(), h_mm=self.pad_h.value(), shape=self.pad_shape.currentText(),
                      net=self.net_edit.text().strip())
            self.commit(d.model_copy(update=dict(pads=d.pads + (pad,))))
        elif t == "footprint":
            pads = footprint(self.fp_combo.currentText(), x, y, self.fp_angle.value(), self.fp_ref.text() or "U")
            self.commit(d.model_copy(update=dict(pads=d.pads + tuple(pads))))
            self.fp_ref.setText(_next_ref(self.fp_ref.text()))

    def _move(self, x, y, held):
        sx, sy = self.snap(x, y)
        self._cursor = (sx, sy)
        self.coord_lbl.setText(f"X {sx:.2f}  Y {sy:.2f} mm")
        if held and self._tool == "select" and self._sel and self._drag_from:
            self._preview_move = (sx - self._drag_from[0], sy - self._drag_from[1])
        if self._draft or (held and self._tool in ("rect", "rrect")):
            self.redraw(live=True)

    def _release(self, x, y):
        x, y = self.snap(x, y)
        d = self.design
        if self._tool in ("rect", "rrect") and self._drag_from:
            x0, y0 = self._drag_from
            self._drag_from = None
            if abs(x - x0) > 1 and abs(y - y0) > 1:
                rect = Polygon([(x0, y0), (x, y0), (x, y), (x0, y)])
                if self._tool == "rrect":
                    r = min(2.0 * self._min_radius, abs(x - x0) / 3, abs(y - y0) / 3)
                    rect = rect.buffer(-r, join_style=2).buffer(r, quad_segs=8)
                pts = list(rect.exterior.coords)[:-1]
                if not Polygon(pts).exterior.is_ccw:
                    pts = pts[::-1]
                self.commit(d.model_copy(update=dict(outline=tuple(pts))))
            self.redraw()
        elif self._tool == "select" and self._sel and self._drag_from:
            dx, dy = x - self._drag_from[0], y - self._drag_from[1]
            self._drag_from = None
            self._preview_move = None
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                self.commit(_moved(d, self._sel, dx, dy))
                self._show_props()

    def _key(self, key):
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        elif key == Qt.Key_Escape:
            self._draft = []
            self._sel = None
            self.redraw()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()

    def _finish(self):
        d, pts = self.design, self._draft
        self._draft = []
        if self._tool == "polygon" and len(pts) >= 3:
            if not Polygon(pts).exterior.is_ccw:
                pts = pts[::-1]
            self.commit(d.model_copy(update=dict(outline=tuple(pts))))
        elif self._tool == "trace" and len(pts) >= 2:
            tr = Trace(points=tuple(pts), width_mm=self.width_spin.value(), net=self.net_edit.text().strip())
            self.commit(d.model_copy(update=dict(traces=d.traces + (tr,))))
        elif self._tool == "zone" and len(pts) >= 3:
            z = Zone(points=tuple(pts), kind=self.zone_kind.currentText(), net=self.net_edit.text().strip())
            self.commit(d.model_copy(update=dict(zones=d.zones + (z,))))
        self.redraw()

    def pick(self, x, y, tol=0.6):
        p = Point(x, y)
        d = self.design
        for i, pad in enumerate(d.pads):
            if pad_shape(pad).buffer(tol * 0.5).contains(p):
                return ("pad", i)
        for i, h in enumerate(d.holes):
            if math.dist((h.x, h.y), (x, y)) <= h.d_mm / 2 + tol * 0.5:
                return ("hole", i)
        best = None
        for i, t in enumerate(d.traces):
            dist = LineString(t.points).distance(p)
            if dist <= t.width_mm / 2 + tol and (best is None or dist < best[0]):
                best = (dist, ("trace", i))
        if best:
            return best[1]
        for i, z in enumerate(d.zones):
            if Polygon(z.points).buffer(0).contains(p):
                return ("zone", i)
        return None

    def delete_selected(self):
        if not self._sel:
            return
        kind, i = self._sel
        d = self.design
        field = {"trace": "traces", "pad": "pads", "hole": "holes", "zone": "zones"}[kind]
        items = list(getattr(d, field))
        items.pop(i)
        self._sel = None
        self.commit(d.model_copy(update={field: tuple(items)}))
        self._show_props()

    # ------------------------------------------------------------------
    # Properties of the selection
    # ------------------------------------------------------------------
    def _show_props(self):
        while self.prop_form.rowCount():
            self.prop_form.removeRow(0)
        if not self._sel:
            self.prop_box.setTitle("Selection")
            lbl = QLabel("Click an item (Select tool) to edit it.")
            lbl.setObjectName("hint")
            self.prop_form.addRow(lbl)
            return
        kind, i = self._sel
        d = self.design
        item = {"trace": d.traces, "pad": d.pads, "hole": d.holes, "zone": d.zones}[kind][i]
        self.prop_box.setTitle(f"Selection: {kind} {i + 1}")
        field = {"trace": "traces", "pad": "pads", "hole": "holes", "zone": "zones"}[kind]

        def setter(attr, cast=float):
            def apply(value):
                cur = list(getattr(self.design, field))
                try:
                    cur[i] = cur[i].model_copy(update={attr: cast(value)})
                except Exception:
                    return
                self.commit(self.design.model_copy(update={field: tuple(cur)}))
            return apply

        def num(attr, lo=0.05, hi=500.0):
            w = _spin(lo, hi, float(getattr(item, attr)))
            w.valueChanged.connect(setter(attr))
            self.prop_form.addRow(attr.replace("_mm", " (mm)").replace("_", " "), w)

        if kind == "trace":
            num("width_mm", 0.05, 50)
            r = drc(self.design, self.diw_config())["traces"][i]
            self.prop_form.addRow("length", QLabel(f"{r['length_mm']:.1f} mm"))
            self.prop_form.addRow("resistance", QLabel(_ohms(r["resistance_ohm"])))
        elif kind == "pad":
            num("x", -1000, 1000)
            num("y", -1000, 1000)
            num("w_mm", 0.05, 50)
            num("h_mm", 0.05, 50)
        elif kind == "hole":
            num("x", -1000, 1000)
            num("y", -1000, 1000)
            num("d_mm", 0.1, 100)
        if hasattr(item, "net"):
            e = QLineEdit(item.net)
            e.editingFinished.connect(lambda: setter("net", str)(e.text().strip()))
            self.prop_form.addRow("net", e)
        if hasattr(item, "name"):
            e2 = QLineEdit(item.name)
            e2.editingFinished.connect(lambda: setter("name", str)(e2.text().strip()))
            self.prop_form.addRow("name", e2)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def redraw(self, live=False):
        self.scene.clear()
        d = self.design
        board = QPainterPath()
        board.addPolygon(QPolygonF([QPointF(x, y) for x, y in d.outline]))
        board.closeSubpath()
        self.scene.addPath(board, QPen(_COL["board_edge"], 0), QBrush(_COL["board"]))
        for z in d.zones:
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in z.points]))
            path.closeSubpath()
            self.scene.addPath(path, QPen(_COL[f"zone_{z.kind}"].lighter(150), 0), QBrush(_COL[f"zone_{z.kind}"]))
        for t in d.traces:
            self._add_geom(trace_shape(t), _COL["trace"])
        for p in d.pads:
            self._add_geom(pad_shape(p), _COL["pad"])
        for h in d.holes:
            r = h.d_mm / 2
            self.scene.addEllipse(QRectF(h.x - r, h.y - r, 2 * r, 2 * r), QPen(_COL["board_edge"], 0),
                                  QBrush(_COL["hole"]))
        if self._sel:
            kind, i = self._sel
            geom = {"trace": lambda: trace_shape(d.traces[i]), "pad": lambda: pad_shape(d.pads[i]),
                    "hole": lambda: Point(d.holes[i].x, d.holes[i].y).buffer(d.holes[i].d_mm / 2),
                    "zone": lambda: Polygon(d.zones[i].points).buffer(0)}[kind]()
            self._add_geom(geom.buffer(0.15), None, QPen(_COL["sel"], 0.12))
        # draft (polyline / rectangle being drawn)
        pen = QPen(_COL["draft"], 0.12, Qt.DashLine)
        if self._draft:
            pts = self._draft + [self._cursor]
            for a, b in zip(pts[:-1], pts[1:], strict=False):
                self.scene.addLine(a[0], a[1], b[0], b[1], pen)
        if self._tool in ("rect", "rrect") and self._drag_from:
            x0, y0 = self._drag_from
            x1, y1 = self._cursor
            self.scene.addRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)), pen)
        if not live:
            self._run_drc()
        b = Polygon(d.outline).bounds
        self.scene.setSceneRect(QRectF(b[0] - 40, b[1] - 40, b[2] - b[0] + 80, b[3] - b[1] + 80))

    def _add_geom(self, geom, color, pen=None):
        for g in getattr(geom, 'geoms', [geom]):
            if g.geom_type != 'Polygon' or g.is_empty:
                continue
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in g.exterior.coords]))
            for ring in g.interiors:
                path.addPolygon(QPolygonF([QPointF(x, y) for x, y in ring.coords]))
            path.setFillRule(Qt.OddEvenFill)
            self.scene.addPath(path, pen or QPen(Qt.NoPen), QBrush(color) if color is not None else QBrush())

    def fit(self):
        b = Polygon(self.design.outline).bounds
        self.canvas.fitInView(QRectF(b[0] - 5, b[1] - 5, b[2] - b[0] + 10, b[3] - b[1] + 10), Qt.KeepAspectRatio)

    def _run_drc(self, *_):
        res = drc(self.design, self.diw_config())
        self.last_drc = res
        self.drc_list.clear()
        color = {"Pass": "#4ade80", "Warning": "#fbbf24", "Fail": "#f87171"}[res["status"]]
        self.drc_status.setText(f"<b style='color:{color}'>{res['status']}</b> · {len(res['nets'])} net(s), "
                                f"{len(self.design.traces)} trace(s), {len(self.design.pads)} pad(s), "
                                f"{len(self.design.holes)} hole(s)")
        for m in res["messages"]:
            it = QListWidgetItem(("✔ " if res["status"] == "Pass" else "• ") + m)
            it.setForeground(QColor(color))
            self.drc_list.addItem(it)
        for t in res["traces"]:
            it = QListWidgetItem(f"ℹ {t['name']}: {t['length_mm']:.1f} mm, {t['width_mm']:g} mm wide, "
                                 f"R ≈ {_ohms(t['resistance_ohm'])}")
            it.setForeground(QColor("#93c5fd"))
            self.drc_list.addItem(it)

    # ------------------------------------------------------------------
    # Files, generators, slicing
    # ------------------------------------------------------------------
    def new_design(self):
        self.commit(BoardDesign())
        self.fit()

    def load_example(self):
        self.commit(example_board())
        self.fit()

    def open_design(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open board", "", "Structural PCB (*.spcb.json *.json)")
        if not path:
            return
        try:
            d = load_design(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self.path = path
        self.commit(d)
        self.fit()

    def save_design_as(self, path=None):
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save board", self.path or "board.spcb.json",
                                                  "Structural PCB (*.spcb.json)")
        if path:
            save_design(self.design, path)
            self.path = path

    def _params_dialog(self, title, fields):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)
        widgets = {}
        for key, label, val in fields:
            if isinstance(val, str):
                w = QLineEdit(val)
            elif isinstance(val, int) and not isinstance(val, bool):
                w = QSpinBox()
                w.setRange(1, 500)
                w.setValue(val)
            else:
                w = _spin(-1000, 1000, float(val))
            widgets[key] = w
            form.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return None
        return {k: (w.text() if isinstance(w, QLineEdit) else w.value()) for k, w in widgets.items()}

    def _add_meander(self):
        x, y = self._cursor
        v = self._params_dialog("Strain gauge / heater meander", [
            ("x", "Start X", x), ("y", "Start Y", y), ("length", "Leg length", 15.0), ("pitch", "Pitch", 1.6),
            ("turns", "Legs", 6), ("width", "Trace width", 0.6), ("net", "Net", "SG")])
        if v:
            tr = meander(v["x"], v["y"], v["length"], v["pitch"], v["turns"], v["width"], v["net"], "meander")
            self.commit(self.design.model_copy(update=dict(traces=self.design.traces + (tr,))))

    def _add_ide(self):
        x, y = self._cursor
        v = self._params_dialog("Interdigitated electrodes", [
            ("x", "Origin X", x), ("y", "Origin Y", y), ("finger", "Finger length", 8.0), ("n", "Fingers", 8),
            ("pitch", "Pitch", 1.2), ("width", "Finger width", 0.5), ("a", "Net A", "IDE_A"), ("b", "Net B", "IDE_B")])
        if v:
            trs = interdigitated(v["x"], v["y"], v["finger"], v["n"], v["pitch"], v["width"], (v["a"], v["b"]))
            self.commit(self.design.model_copy(update=dict(traces=self.design.traces + tuple(trs))))

    def _add_coil(self):
        x, y = self._cursor
        v = self._params_dialog("Spiral coil (antenna / inductor / heater)", [
            ("x", "Centre X", x), ("y", "Centre Y", y), ("r", "Inner radius", 3.0), ("turns", "Turns", 4),
            ("pitch", "Pitch", 1.6), ("width", "Trace width", 0.6), ("net", "Net", "COIL")])
        if v:
            tr = spiral_coil(v["x"], v["y"], v["r"], v["turns"], v["pitch"], v["width"], v["net"])
            self.commit(self.design.model_copy(update=dict(traces=self.design.traces + (tr,))))

    def _request_slice(self):
        self.slice_requested.emit(self.design, self.diw_config())


def _moved(d: BoardDesign, sel, dx, dy) -> BoardDesign:
    kind, i = sel
    if kind == "trace":
        items = list(d.traces)
        items[i] = items[i].model_copy(update=dict(points=tuple((x + dx, y + dy) for x, y in items[i].points)))
        return d.model_copy(update=dict(traces=tuple(items)))
    if kind == "pad":
        items = list(d.pads)
        items[i] = items[i].model_copy(update=dict(x=items[i].x + dx, y=items[i].y + dy))
        return d.model_copy(update=dict(pads=tuple(items)))
    if kind == "hole":
        items = list(d.holes)
        items[i] = items[i].model_copy(update=dict(x=items[i].x + dx, y=items[i].y + dy))
        return d.model_copy(update=dict(holes=tuple(items)))
    items = list(d.zones)
    items[i] = items[i].model_copy(update=dict(points=tuple((x + dx, y + dy) for x, y in items[i].points)))
    return d.model_copy(update=dict(zones=tuple(items)))


def _next_ref(ref: str) -> str:
    head = ref.rstrip("0123456789")
    tail = ref[len(head):]
    return f"{head}{int(tail) + 1}" if tail else f"{ref}2"


def _ohms(r):
    return f"{r * 1000:.1f} mΩ" if r < 1 else f"{r:.2f} Ω" if r < 1000 else f"{r / 1000:.2f} kΩ"
