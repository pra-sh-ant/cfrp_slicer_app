"""Structural PCB editor: draw a board, add holes, parts and wires, check
them and send the board to the slicer (fibre board + DIW electronics).

Two modes:
  Easy      six numbered steps (board size, holes, drag parts on, draw
            wires, move/rotate, check & print) with plain words; the DIW
            process uses sensible defaults.
  Advanced  every drawing tool, any net names, the full DIW process.

Canvas: wheel zoom · middle-drag (or Space+drag) pan · drag a part from
the list onto the board · click to select, drag to move, R / Shift+R
rotate 90°, Delete removes · Enter / double-click / right-click finishes a
wire or outline · Esc cancels · Ctrl+Z / Ctrl+Y undo / redo.

The editor edits an immutable `cfrp_core.diw.BoardDesign`; every change
makes a new design (cfrp_core.diw.edit) and pushes the old one on the undo
stack; the scene is redrawn from the design.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF, QShortcut
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
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shapely.geometry import Polygon

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
from cfrp_core.diw import edit as ed
from cfrp_core.diw.design import pad_shape, trace_shape

from .settings_help import DIW_HELP

MODES = (("Structural: fibre + electronics", "structural"), ("Fibre board only", "fibre"),
         ("Electronics only (ink)", "electronics"))
_MIME = "application/x-spcb-part"

TOOLS = (
    ("select", "Select / move", "Click something to select it, drag to move it, R to rotate, Delete to remove"),
    ("rect", "Board ▭", "Board outline: drag a rectangle"),
    ("rrect", "Board ▢", "Board outline: drag a rounded rectangle"),
    ("polygon", "Board ⬠", "Board outline: click the corners, double-click to finish"),
    ("hole", "Hole", "Mounting hole: click where it goes"),
    ("trace", "Wire", "Wire: click from pad to pad, double-click to finish"),
    ("pad", "Pad", "Single pad: click where it goes"),
    ("zone", "Zone", "Area: click its corners (filled ink, extra insulation, or keep ink out)"),
    ("footprint", "Part", "Place the part chosen below"),
)

HINTS = {
    "select": "Click a part, wire or hole to select it · drag to move · R rotates 90° (Shift+R the other way) · "
              "Delete removes · drag a part from the list onto the board to add it",
    "rect": "Drag a rectangle for the board outline",
    "rrect": "Drag a rectangle; its corners are rounded so the fibre can follow them",
    "polygon": "Click the corners of your board, double-click (or Enter) to finish · Esc cancels",
    "hole": "Click where a mounting hole goes",
    "trace": "Click on a pad to start the wire, click to add bends, double-click on the other pad to finish · "
             "Esc cancels",
    "pad": "Click where the pad goes",
    "zone": "Click the corners of the area, double-click to finish",
    "footprint": "Click where the part goes",
    "place": "Click on the board to put the part there (or drag it from the list)",
}

GUIDE = """<b>Design a structural PCB in 6 steps</b><ol>
<li><b>Board</b>: type the size and press <i>Make board</i> (or draw your own shape in Advanced).</li>
<li><b>Holes</b>: <i>Add 4 corner holes</i> for screws. The fibre will steer round them.</li>
<li><b>Parts</b>: drag LEDs, resistors, connectors or sensors from the list onto the board.</li>
<li><b>Wires</b>: press <i>Draw wire</i>, click a pad, click where it should bend, double-click on the
other pad. Wires take their name from the pad you start on.</li>
<li><b>Tidy</b>: click to select, drag to move, <b>R</b> rotates, <b>Delete</b> removes, Ctrl+Z undoes.</li>
<li><b>Check &amp; print</b>: fix anything red in <i>Checks</i>, choose what to print, press
<i>Slice board</i>.</li></ol>
Carbon fibre conducts electricity: the slicer puts an insulating layer under every wire for you."""

_COL = {
    "board": QColor("#2b2f38"), "board_edge": QColor("#8d96b8"), "hole": QColor("#0d0d12"),
    "trace": QColor("#c9cdd8"), "pad": QColor("#e2c26a"), "zone_conductor": QColor(201, 205, 216, 90),
    "zone_dielectric": QColor(76, 200, 150, 70), "zone_keepout": QColor(240, 90, 90, 60),
    "sel": QColor("#5b8def"), "draft": QColor("#f5b942"), "grid": QColor(255, 255, 255, 18),
    "grid10": QColor(255, 255, 255, 38), "label": QColor("#9aa3c8"),
}

WIDTHS = (("Thin (0.6 mm)", 0.6), ("Normal (1.0 mm)", 1.0), ("Wide - power (2.0 mm)", 2.0))


class _Canvas(QGraphicsView):
    """mm canvas, Y up. Emits clicks/drags in mm, accepts dropped parts."""

    pressed = Signal(float, float, object)
    moved = Signal(float, float, bool)
    released = Signal(float, float)
    double = Signal(float, float)
    key = Signal(int, object)
    dropped = Signal(str, float, float)

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.scale(8, -8)
        self.grid = 1.0
        self._pan = None
        self._space = False
        self.setBackgroundBrush(QColor("#15151f"))

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        step = self.grid
        while step * abs(self.transform().m11()) < 6:
            step *= 5
        for mult, col in ((1, _COL["grid"]), (10, _COL["grid10"])):
            s = step * mult
            painter.setPen(QPen(col, 0))
            x = math.floor(rect.left() / s) * s
            while x <= rect.right():
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += s
            y = math.floor(rect.top() / s) * s
            while y <= rect.bottom():
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                y += s

    def wheelEvent(self, ev):
        f = 1.2 if ev.angleDelta().y() > 0 else 1 / 1.2
        self.scale(f, f)

    def _mm(self, pos):
        p = self.mapToScene(pos.toPoint())
        return p.x(), p.y()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MiddleButton or (self._space and ev.button() == Qt.LeftButton):
            self._pan = ev.position()
            return
        self.pressed.emit(*self._mm(ev.position()), ev.button())

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            d = ev.position() - self._pan
            self._pan = ev.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        self.moved.emit(*self._mm(ev.position()), bool(ev.buttons() & Qt.LeftButton))

    def mouseReleaseEvent(self, ev):
        if self._pan is not None:
            self._pan = None
            return
        self.released.emit(*self._mm(ev.position()))

    def mouseDoubleClickEvent(self, ev):
        self.double.emit(*self._mm(ev.position()))

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = True
        self.key.emit(ev.key(), ev.modifiers())
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = False
        super().keyReleaseEvent(ev)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(_MIME):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat(_MIME):
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        key = bytes(ev.mimeData().data(_MIME)).decode()
        self.dropped.emit(key, *self._mm(ev.position()))
        ev.acceptProposedAction()


class _Palette(QListWidget):
    """Parts list: drag an item onto the board, or click it then click."""

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setObjectName("issues")
        for key, (label, _, _, desc) in ed.PALETTE.items():
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            it.setToolTip(desc + "\nDrag it onto the board (or click it, then click the board).")
            self.addItem(it)

    def startDrag(self, actions):
        it = self.currentItem()
        if it is None:
            return
        mime = QMimeData()
        mime.setData(_MIME, it.data(Qt.UserRole).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


def _spin(lo, hi, val, step=0.1, dec=2, suffix=" mm"):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(val)
    if suffix:
        w.setSuffix(suffix)
    return w


def _step(n, title):
    box = QGroupBox(f"{n}  {title}".replace("&", "&&"))
    box.setObjectName("step")
    lay = QVBoxLayout(box)
    lay.setSpacing(4)
    return box, lay


class PcbEditor(QWidget):
    """Board editor. `slice_requested(design, diw_config, mode)` asks the
    main window to slice; `design_changed` fires on every edit."""

    slice_requested = Signal(object, object, str)
    design_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.design = example_board()
        self._undo: list[BoardDesign] = []
        self._redo: list[BoardDesign] = []
        self._tool = "select"
        self._place_key = None
        self._draft: list[tuple[float, float]] = []
        self._drag_from = None
        self._preview = None
        self._sel = None
        self._cursor = (0.0, 0.0)
        self._min_radius = 3.0
        self.path = None
        self.last_drc = {"status": "Pass", "messages": [], "nets": [], "traces": []}

        self.scene = QGraphicsScene(self)
        self.canvas = _Canvas(self.scene)
        self.canvas.pressed.connect(self._press)
        self.canvas.moved.connect(self._move)
        self.canvas.released.connect(self._release)
        self.canvas.double.connect(lambda x, y: self._finish())
        self.canvas.key.connect(self._key)
        self.canvas.dropped.connect(self._drop)

        # ---- mode switch + top bar ----------------------------------------------
        top = QHBoxLayout()
        self._mode_btns = QButtonGroup(self)
        for label, adv in (("Easy", False), ("Advanced", True)):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setChecked(not adv)
            b.setToolTip("Easy: step by step with simple choices.  Advanced: every tool and setting."
                         if not adv else "Every drawing tool, net names and the full DIW process settings.")
            b.clicked.connect(lambda _=False, a=adv: self.set_advanced(a))
            self._mode_btns.addButton(b)
            top.addWidget(b)
        top.addSpacing(12)
        for label, cb, tip in (("New", self.new_design, "Start an empty board"),
                               ("Example", self.load_example, "Load an example board (LED + strain gauge)"),
                               ("Open…", self.open_design, "Open a saved board (.spcb.json)"),
                               ("Save…", self.save_design_as, "Save this board"),
                               ("Undo", self.undo, "Undo (Ctrl+Z)"), ("Redo", self.redo, "Redo (Ctrl+Y)"),
                               ("Fit", self.fit, "Show the whole board")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            top.addWidget(b)
        top.addStretch()
        self.coord_lbl = QLabel("")
        self.coord_lbl.setObjectName("viewInfo")
        top.addWidget(self.coord_lbl)

        # ---- left: easy steps / advanced tools ----------------------------------------
        self.left = QStackedWidget()
        self.left.addWidget(self._build_easy())
        self.left.addWidget(self._build_advanced())
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(self.left)
        left_scroll.setMinimumWidth(250)
        left_scroll.setMaximumWidth(300)

        self.hint = QLabel(HINTS["select"])
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        centre = QVBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.addLayout(top)
        centre.addWidget(self.canvas, 1)
        centre.addWidget(self.hint)
        mid = QWidget()
        mid.setLayout(centre)

        # ---- right: selection, checks, guide / DIW settings ----------------------------
        right = QVBoxLayout()
        self.prop_box = QGroupBox("Selected")
        self.prop_form = QFormLayout(self.prop_box)
        right.addWidget(self.prop_box)
        drc_box = QGroupBox("Checks")
        dl = QVBoxLayout(drc_box)
        self.drc_status = QLabel("")
        self.drc_status.setWordWrap(True)
        dl.addWidget(self.drc_status)
        self.drc_list = QListWidget()
        self.drc_list.setWordWrap(True)
        self.drc_list.setObjectName("issues")
        dl.addWidget(self.drc_list)
        right.addWidget(drc_box, 1)
        self.guide = QLabel(GUIDE)
        self.guide.setWordWrap(True)
        self.guide.setTextFormat(Qt.RichText)
        self.guide_box = QGroupBox("How to")
        QVBoxLayout(self.guide_box).addWidget(self.guide)
        right.addWidget(self.guide_box)
        self.diw_box = self._build_diw_box()
        right.addWidget(self.diw_box)
        rw = QWidget()
        rw.setLayout(right)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(rw)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(390)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left_scroll)
        split.addWidget(mid)
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(split)

        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.Redo, self, self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
        self.mode_cb.currentIndexChanged.connect(self._diw_states)
        self.adv_mode_cb.currentIndexChanged.connect(self._diw_states)
        self.set_advanced(False)
        self.redraw()
        self._show_props()
        self.fit()

    # ------------------------------------------------------------------
    # Easy mode
    # ------------------------------------------------------------------
    def _build_easy(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(6)
        box, lay = _step(1, "Board")
        form = QFormLayout()
        self.e_w = _spin(10, 1000, 60, 1, 0)
        self.e_h = _spin(10, 1000, 40, 1, 0)
        self.e_w.setToolTip("Board width (left to right)")
        self.e_h.setToolTip("Board height (front to back)")
        form.addRow("Width", self.e_w)
        form.addRow("Height", self.e_h)
        lay.addLayout(form)
        self.e_round = QCheckBox("Rounded corners (better for fibre)")
        self.e_round.setChecked(True)
        lay.addWidget(self.e_round)
        b = QPushButton("Make board")
        b.setToolTip("Replace the board outline with this size (parts and wires stay)")
        b.clicked.connect(self.make_board)
        lay.addWidget(b)
        v.addWidget(box)

        box, lay = _step(2, "Holes")
        b = QPushButton("Add 4 corner holes (M3)")
        b.setToolTip("Screw holes 5 mm in from each corner")
        b.clicked.connect(lambda: self.commit(ed.corner_holes(self.design)))
        lay.addWidget(b)
        b = QPushButton("Place a hole…")
        b.setToolTip("Then click on the board where it goes")
        b.clicked.connect(lambda: self.set_tool("hole"))
        lay.addWidget(b)
        v.addWidget(box)

        box, lay = _step(3, "Parts")
        lbl = QLabel("Drag onto the board:")
        lbl.setObjectName("hint")
        lay.addWidget(lbl)
        self.palette = _Palette()
        self.palette.setMinimumHeight(230)
        self.palette.itemClicked.connect(lambda it: self.start_place(it.data(Qt.UserRole)))
        lay.addWidget(self.palette)
        v.addWidget(box)

        box, lay = _step(4, "Wires")
        self.e_width = QComboBox()
        for label, val in WIDTHS:
            self.e_width.addItem(label, val)
        self.e_width.setCurrentIndex(1)
        self.e_width.setToolTip("How wide the wire is printed. Wide wires carry more current.")
        lay.addWidget(self.e_width)
        b = QPushButton("Draw wire")
        b.setToolTip("Click on a pad, click to bend, double-click on the other pad")
        b.clicked.connect(lambda: self.set_tool("trace"))
        lay.addWidget(b)
        v.addWidget(box)

        box, lay = _step(5, "Move & rotate")
        row = QHBoxLayout()
        for label, cb, tip in (("Select", lambda: self.set_tool("select"), "Click things to select, drag to move"),
                               ("⟲ 90°", lambda: self.rotate_selected(90), "Rotate the selection (R)"),
                               ("⟳ 90°", lambda: self.rotate_selected(-90), "Rotate the other way (Shift+R)"),
                               ("Delete", self.delete_selected, "Remove the selection (Delete)")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            row.addWidget(b)
        lay.addLayout(row)
        v.addWidget(box)

        box, lay = _step(6, "Check & print")
        self.e_check = QLabel("")
        self.e_check.setWordWrap(True)
        lay.addWidget(self.e_check)
        self.mode_cb = QComboBox()
        for label, key in MODES:
            self.mode_cb.addItem(label, key)
        self.mode_cb.setToolTip(DIW_HELP["print_mode"])
        lay.addWidget(self.mode_cb)
        self.slice_btn = QPushButton("Slice board  ▶")
        self.slice_btn.setObjectName("primary")
        self.slice_btn.setToolTip("Make the toolpaths and open them in the 3D view")
        self.slice_btn.clicked.connect(self._request_slice)
        lay.addWidget(self.slice_btn)
        v.addWidget(box)
        v.addStretch()
        return w

    def make_board(self):
        r = 2.0 * self._min_radius if self.e_round.isChecked() else 0.0
        outline = ed.rectangle_outline(self.e_w.value(), self.e_h.value(), r)
        self.commit(self.design.model_copy(update=dict(outline=outline)))
        self.fit()

    def start_place(self, key):
        self._place_key = key
        self._tool = "place"
        self.hint.setText(f"{ed.PALETTE[key][0]}: " + HINTS["place"])

    def _drop(self, key, x, y):
        x, y = self.snap(x, y)
        d, sel = ed.place(self.design, key, x, y)
        self._sel = sel
        self.commit(d)
        self.set_tool("select")
        self._show_props()

    # ------------------------------------------------------------------
    # Advanced mode
    # ------------------------------------------------------------------
    def _build_advanced(self):
        w = QWidget()
        tools = QVBoxLayout(w)
        tools.setSpacing(3)
        self._tool_btns = QButtonGroup(self)
        self._tool_btns.setExclusive(True)
        for key, label, tip in TOOLS:
            b = QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setMinimumWidth(110)
            b.setChecked(key == "select")
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            self._tool_btns.addButton(b)
            tools.addWidget(b)
        tools.addSpacing(8)
        form = QFormLayout()
        self.grid_spin = _spin(0.1, 10, 1.0, 0.1, 2)
        self.grid_spin.setToolTip("Everything snaps to this grid")
        self.grid_spin.valueChanged.connect(self._set_grid)
        form.addRow("Grid", self.grid_spin)
        self.width_spin = _spin(0.2, 20, 0.8)
        self.width_spin.setToolTip("Width of new wires")
        form.addRow("Wire width", self.width_spin)
        self.net_edit = QLineEdit()
        self.net_edit.setPlaceholderText("auto")
        self.net_edit.setToolTip("Signal name for new wires and pads. Empty = take it from the pad the wire "
                                 "starts on. Different names that touch are reported as a short.")
        form.addRow("Net", self.net_edit)
        self.hole_spin = _spin(0.5, 30, 3.2)
        self.hole_spin.setToolTip("Diameter of new holes (3.2 mm fits an M3 screw)")
        form.addRow("Hole Ø", self.hole_spin)
        self.pad_w = _spin(0.2, 30, 1.6)
        self.pad_h = _spin(0.2, 30, 1.6)
        form.addRow("Pad width", self.pad_w)
        form.addRow("Pad height", self.pad_h)
        self.pad_shape = QComboBox()
        self.pad_shape.addItems(["rect", "circle"])
        form.addRow("Pad shape", self.pad_shape)
        self.zone_kind = QComboBox()
        self.zone_kind.addItems(["conductor", "dielectric", "keepout"])
        self.zone_kind.setToolTip("conductor = filled with ink; dielectric = extra insulation; "
                                  "keepout = no ink allowed here")
        form.addRow("Zone kind", self.zone_kind)
        self.fp_combo = QComboBox()
        self.fp_combo.addItems(list(FOOTPRINTS))
        form.addRow("Part", self.fp_combo)
        self.fp_angle = _spin(-360, 360, 0, 90, 0, "°")
        form.addRow("Part angle", self.fp_angle)
        self.fp_ref = QLineEdit("U1")
        self.fp_ref.setToolTip("Name of the next part (counts up)")
        form.addRow("Part name", self.fp_ref)
        tools.addLayout(form)
        tools.addSpacing(6)
        for label, cb in (("Strain gauge…", self._add_meander), ("Interdigitated…", self._add_ide),
                          ("Spiral coil…", self._add_coil)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            tools.addWidget(b)
        row = QHBoxLayout()
        for label, cb in (("⟲ 90°", lambda: self.rotate_selected(90)), ("⟳ 90°", lambda: self.rotate_selected(-90)),
                          ("Delete", self.delete_selected)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            row.addWidget(b)
        tools.addLayout(row)
        self.adv_mode_cb = QComboBox()
        for label, key in MODES:
            self.adv_mode_cb.addItem(label, key)
        self.adv_mode_cb.setToolTip(DIW_HELP["print_mode"])
        tools.addWidget(self.adv_mode_cb)
        b = QPushButton("Slice board  ▶")
        b.setObjectName("primary")
        b.clicked.connect(self._request_slice)
        tools.addWidget(b)
        tools.addStretch()
        return w

    def set_advanced(self, on: bool):
        self.advanced = bool(on)
        self.left.setCurrentIndex(1 if on else 0)
        self.diw_box.setVisible(on)
        self.guide_box.setVisible(not on)
        for b, adv in zip(self._mode_btns.buttons(), (False, True), strict=False):
            b.setChecked(adv == self.advanced)
        if hasattr(self, "adv_mode_cb"):
            src, dst = (self.mode_cb, self.adv_mode_cb) if on else (self.adv_mode_cb, self.mode_cb)
            dst.setCurrentIndex(src.currentIndex())
        self._diw_states()

    # ------------------------------------------------------------------
    # DIW settings (advanced)
    # ------------------------------------------------------------------
    def _build_diw_box(self):
        box = QGroupBox("DIW process (ink)")
        f = QFormLayout(box)
        d = DiwConfig()
        self.d_nozzle = _spin(0.05, 3, d.nozzle_mm, 0.01, 2)
        self.d_lw = _spin(0.05, 5, d.line_width_mm, 0.05, 2)
        self.d_lh = _spin(0.02, 3, d.layer_height_mm, 0.05, 2)
        self.d_cond = QSpinBox()
        self.d_cond.setRange(1, 10)
        self.d_cond.setValue(d.conductor_layers)
        self.d_diel_on = QCheckBox("Insulate under the ink")
        self.d_diel_on.setChecked(d.dielectric)
        self.d_diel = QSpinBox()
        self.d_diel.setRange(0, 10)
        self.d_diel.setValue(d.dielectric_layers)
        self.d_margin = _spin(0, 10, d.dielectric_margin_mm)
        self.d_enc = QCheckBox("Cover the wires (encapsulate)")
        self.d_enc_n = QSpinBox()
        self.d_enc_n.setRange(1, 10)
        self.d_enc_n.setValue(d.encapsulation_layers)
        self.d_clear = _spin(0, 5, d.clearance_mm, 0.05)
        self.d_edge = _spin(0, 10, d.edge_clearance_mm)
        self.d_vc = _spin(0.1, 100, d.speed_conductor_mm_s, 0.5, 1, " mm/s")
        self.d_vd = _spin(0.1, 100, d.speed_dielectric_mm_s, 0.5, 1, " mm/s")
        self.d_vt = _spin(1, 300, d.travel_speed_mm_s, 1, 0, " mm/s")
        self.d_hop = _spin(0, 20, d.z_hop_mm, 0.1, 2)
        self.d_mode = QComboBox()
        self.d_mode.addItems(["pneumatic", "volumetric"])
        self.d_on = QLineEdit(d.dispense_on)
        self.d_off = QLineEdit(d.dispense_off)
        self.d_on_ms = QSpinBox()
        self.d_on_ms.setRange(0, 5000)
        self.d_on_ms.setSuffix(" ms")
        self.d_on_ms.setValue(d.on_delay_ms)
        self.d_off_ms = QSpinBox()
        self.d_off_ms.setRange(0, 5000)
        self.d_off_ms.setSuffix(" ms")
        self.d_off_ms.setValue(d.off_delay_ms)
        self.d_epm = _spin(0.0001, 1000, d.e_per_mm3, 0.01, 4, "")
        self.d_tool = QLineEdit(d.tool_select)
        self.d_ox = _spin(-500, 500, 0.0)
        self.d_oy = _spin(-500, 500, 0.0)
        self.d_oz = _spin(-100, 100, 0.0)
        self.d_pause = QCheckBox("Pause before ink (cut fibre, swap head)")
        self.d_pause.setChecked(d.pause_before)
        self.d_rho = QLineEdit(f"{d.resistivity_ohm_mm:g}")
        self.d_cure = _spin(0.05, 1.0, d.cured_thickness_ratio, 0.05, 2, "")
        self.d_carbon = QCheckBox("Board is carbon fibre (conducts)")
        self.d_carbon.setChecked(d.substrate_conductive)
        self.d_surface = _spin(0, 500, d.surface_z_mm, 0.1, 2)
        rows = (("Needle", self.d_nozzle, "nozzle"), ("Line width", self.d_lw, "line_width"),
                ("Layer height", self.d_lh, "layer_height"), ("Ink layers", self.d_cond, "conductor_layers"),
                ("", self.d_diel_on, "dielectric"), ("Insulation layers", self.d_diel, "dielectric_layers"),
                ("Insulation margin", self.d_margin, "dielectric_margin"), ("", self.d_enc, "encapsulate"),
                ("Cover layers", self.d_enc_n, "encapsulation_layers"),
                ("Wire spacing", self.d_clear, "clearance"), ("Edge spacing", self.d_edge, "edge_clearance"),
                ("Ink speed", self.d_vc, "speed_conductor"), ("Insulation speed", self.d_vd, "speed_dielectric"),
                ("Travel speed", self.d_vt, "travel_speed"), ("Lift between lines", self.d_hop, "z_hop"),
                ("Dispense", self.d_mode, "dispense"), ("Valve on", self.d_on, "dispense_on"),
                ("Valve off", self.d_off, "dispense_off"), ("Open delay", self.d_on_ms, "on_delay"),
                ("Close delay", self.d_off_ms, "off_delay"), ("E per mm³", self.d_epm, "e_per_mm3"),
                ("Tool command", self.d_tool, "tool_select"), ("Head offset X", self.d_ox, "tool_offset"),
                ("Head offset Y", self.d_oy, "tool_offset"), ("Head offset Z", self.d_oz, "tool_offset"),
                ("", self.d_pause, "pause_before"), ("Resistivity Ω·mm", self.d_rho, "resistivity"),
                ("Cure shrink", self.d_cure, "cured_ratio"), ("", self.d_carbon, "carbon"),
                ("Surface height", self.d_surface, "surface_z"))
        self._diw_tips = {}
        for label, w, key in rows:
            tip = DIW_HELP.get(key, "")
            self._diw_tips[w] = tip
            w.setToolTip(tip)
            lbl = QLabel(label)
            lbl.setToolTip(tip)
            f.addRow(lbl, w)
            for sig in ("valueChanged", "toggled", "currentTextChanged", "editingFinished"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self._run_drc)
                    break
        for sig in (self.d_mode.currentTextChanged, self.d_diel_on.toggled, self.d_enc.toggled):
            sig.connect(self._diw_states)
        return box

    def _diw_states(self, *_):
        """Grey out DIW settings that do not apply (with the reason)."""
        if not hasattr(self, "d_mode") or not hasattr(self, "mode_cb"):
            return
        pneu = self.d_mode.currentText() == "pneumatic"
        rules = ((self.d_on, pneu, "Volumetric dispensing uses the E axis, not valve commands."),
                 (self.d_off, pneu, "Volumetric dispensing uses the E axis, not valve commands."),
                 (self.d_on_ms, pneu, "Only for pneumatic (valve) dispensing."),
                 (self.d_off_ms, pneu, "Only for pneumatic (valve) dispensing."),
                 (self.d_epm, not pneu, "Only for volumetric dispensing."),
                 (self.d_diel, self.d_diel_on.isChecked(), "Insulation is off."),
                 (self.d_margin, self.d_diel_on.isChecked(), "Insulation is off."),
                 (self.d_enc_n, self.d_enc.isChecked(), "Covering is off."),
                 (self.d_surface, self.print_mode() == "electronics",
                  "Only for Electronics only: otherwise the ink goes on the fibre board's top."))
        for w, on, why in rules:
            w.setEnabled(on)
            tip = self._diw_tips.get(w, "")
            w.setToolTip(tip if on else f"<b>Not used here.</b> {why}<br><br>{tip}")

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
            substrate_conductive=self.d_carbon.isChecked(),
            encapsulation_layers=self.d_enc_n.value(), travel_speed_mm_s=self.d_vt.value(),
            z_hop_mm=self.d_hop.value(), on_delay_ms=self.d_on_ms.value(), off_delay_ms=self.d_off_ms.value(),
            e_per_mm3=self.d_epm.value(), tool_select=self.d_tool.text().strip(),
            tool_offset=(self.d_ox.value(), self.d_oy.value(), self.d_oz.value()),
            pause_before=self.d_pause.isChecked(), cured_thickness_ratio=self.d_cure.value(),
            surface_z_mm=self.d_surface.value())

    # ------------------------------------------------------------------
    # Editing
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
        self._preview = None
        self.redraw()
        self.design_changed.emit(design)

    def undo(self):
        if self._undo:
            self._redo.append(self.design)
            self.design = self._undo.pop()
            self._sel = None
            self.redraw()
            self._show_props()
            self.design_changed.emit(self.design)

    def redo(self):
        if self._redo:
            self._undo.append(self.design)
            self.design = self._redo.pop()
            self._sel = None
            self.redraw()
            self._show_props()
            self.design_changed.emit(self.design)

    def set_tool(self, key):
        self._tool = key
        self._draft = []
        for b, (k, _, _) in zip(self._tool_btns.buttons(), TOOLS, strict=False):
            b.setChecked(k == key)
        self.hint.setText(HINTS.get(key, ""))
        self.redraw()

    def _set_grid(self, v):
        self.canvas.grid = v
        self.canvas.viewport().update()

    def snap(self, x, y):
        g = self.grid_spin.value()
        return round(x / g) * g, round(y / g) * g

    def _wire_width(self):
        return self.width_spin.value() if self.advanced else self.e_width.currentData()

    def _press(self, x, y, button):
        x, y = self.snap(x, y)
        d = self.design
        t = self._tool
        if button == Qt.RightButton:
            self._finish()
            return
        if t == "select":
            self._sel = ed.pick(d, x, y)
            self._drag_from = (x, y) if self._sel else None
            self.redraw()
            self._show_props()
        elif t == "place" and self._place_key:
            self._drop(self._place_key, x, y)
        elif t in ("rect", "rrect"):
            self._drag_from = (x, y)
        elif t in ("polygon", "trace", "zone"):
            if self._draft and math.dist(self._draft[-1], (x, y)) < 1e-9:
                return
            self._draft.append((x, y))
            self.redraw(live=True)
        elif t == "hole":
            self.commit(d.model_copy(update=dict(holes=d.holes + (Hole(x=x, y=y, d_mm=self.hole_spin.value()),))))
        elif t == "pad":
            pad = Pad(x=x, y=y, w_mm=self.pad_w.value(), h_mm=self.pad_h.value(), shape=self.pad_shape.currentText(),
                      net=self.net_edit.text().strip())
            self.commit(d.model_copy(update=dict(pads=d.pads + (pad,))))
        elif t == "footprint":
            ref = self.fp_ref.text().strip() or ed.next_ref(d, "U")
            pads = footprint(self.fp_combo.currentText(), x, y, self.fp_angle.value(), ref)
            self.commit(d.model_copy(update=dict(pads=d.pads + tuple(pads))))
            self.fp_ref.setText(_next_ref(ref))

    def _move(self, x, y, held):
        sx, sy = self.snap(x, y)
        self._cursor = (sx, sy)
        self.coord_lbl.setText(f"X {sx:.2f}  Y {sy:.2f} mm")
        if held and self._tool == "select" and self._sel and self._drag_from:
            dx, dy = sx - self._drag_from[0], sy - self._drag_from[1]
            self._preview = ed.transform(self.design, self._sel, dx, dy) if (dx or dy) else None
            self.redraw(live=True)
        elif self._draft or (held and self._tool in ("rect", "rrect")):
            self.redraw(live=True)

    def _release(self, x, y):
        x, y = self.snap(x, y)
        d = self.design
        if self._tool in ("rect", "rrect") and self._drag_from:
            x0, y0 = self._drag_from
            self._drag_from = None
            if abs(x - x0) > 1 and abs(y - y0) > 1:
                r = 2.0 * self._min_radius if self._tool == "rrect" else 0.0
                outline = ed.rectangle_outline(abs(x - x0), abs(y - y0), r)
                ox, oy = min(x0, x), min(y0, y)
                self.commit(d.model_copy(update=dict(outline=tuple((px + ox, py + oy) for px, py in outline))))
            self.redraw()
        elif self._tool == "select" and self._sel and self._drag_from:
            dx, dy = x - self._drag_from[0], y - self._drag_from[1]
            self._drag_from = None
            self._preview = None
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                self.commit(ed.transform(d, self._sel, dx, dy))
            else:
                self.redraw()
            self._show_props()

    def _key(self, key, mods=None):
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        elif key == Qt.Key_Escape:
            self._draft = []
            self._sel = None
            self.set_tool("select")
            self._show_props()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif key == Qt.Key_R:
            shift = mods is not None and bool(mods & Qt.ShiftModifier)
            self.rotate_selected(-90 if shift else 90)

    def _finish(self):
        d, pts = self.design, self._draft
        self._draft = []
        if self._tool == "polygon" and len(pts) >= 3:
            if not Polygon(pts).exterior.is_ccw:
                pts = pts[::-1]
            self.commit(d.model_copy(update=dict(outline=tuple(pts))))
        elif self._tool == "trace" and len(pts) >= 2:
            net = self.net_edit.text().strip()
            if not net:
                d, net = ed.auto_net(d, pts)
            tr = Trace(points=tuple(pts), width_mm=self._wire_width(), net=net)
            self.commit(d.model_copy(update=dict(traces=d.traces + (tr,))))
        elif self._tool == "zone" and len(pts) >= 3:
            z = Zone(points=tuple(pts), kind=self.zone_kind.currentText(), net=self.net_edit.text().strip())
            self.commit(d.model_copy(update=dict(zones=d.zones + (z,))))
        self.redraw()

    def pick(self, x, y, tol=0.6):
        return ed.pick(self.design, x, y, tol)

    def delete_selected(self):
        if not self._sel:
            return
        sel, self._sel = self._sel, None
        self.commit(ed.delete(self.design, sel))
        self._show_props()

    def rotate_selected(self, angle):
        if not self._sel or self._sel[0] == "hole":
            return
        self.commit(ed.transform(self.design, self._sel, angle_deg=angle))
        self._show_props()

    # ------------------------------------------------------------------
    # Properties of the selection
    # ------------------------------------------------------------------
    def _show_props(self):
        while self.prop_form.rowCount():
            self.prop_form.removeRow(0)
        if not self._sel:
            self.prop_box.setTitle("Selected")
            lbl = QLabel("Nothing selected. Click a part, wire or hole on the board.")
            lbl.setObjectName("hint")
            lbl.setWordWrap(True)
            self.prop_form.addRow(lbl)
            return
        kind, key = self._sel
        d = self.design
        names = {"part": "Part", "trace": "Wire", "pad": "Pad", "hole": "Hole", "zone": "Area"}
        self.prop_box.setTitle(f"Selected: {names[kind]} {key if kind == 'part' else key + 1}")
        cx, cy = ed.centre(d, self._sel)
        self.prop_form.addRow("Position", QLabel(f"X {cx:.1f}  Y {cy:.1f} mm"))
        row = QHBoxLayout()
        for label, ang in (("⟲ 90°", 90), ("⟳ 90°", -90)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, a=ang: self.rotate_selected(a))
            b.setEnabled(kind != "hole")
            row.addWidget(b)
        b = QPushButton("Delete")
        b.clicked.connect(self.delete_selected)
        row.addWidget(b)
        holder = QWidget()
        holder.setLayout(row)
        self.prop_form.addRow(holder)
        if kind == "part":
            pads = [d.pads[i] for i in ed.part_pads(d, key)]
            self.prop_form.addRow("Pads", QLabel(", ".join(f"{p.name} ({p.net or 'no net'})" for p in pads)))
            return
        field = {"trace": "traces", "pad": "pads", "hole": "holes", "zone": "zones"}[kind]
        item = getattr(d, field)[key]

        def setter(attr, cast=float):
            def apply(value):
                cur = list(getattr(self.design, field))
                try:
                    cur[key] = cur[key].model_copy(update={attr: cast(value)})
                except Exception:
                    return
                self.commit(self.design.model_copy(update={field: tuple(cur)}))
            return apply

        def num(attr, label, lo=0.05, hi=500.0):
            w = _spin(lo, hi, float(getattr(item, attr)))
            w.setKeyboardTracking(False)
            w.valueChanged.connect(setter(attr))
            self.prop_form.addRow(label, w)

        if kind == "trace":
            num("width_mm", "Width", 0.05, 50)
            r = drc(self.design, self.diw_config())["traces"][key]
            self.prop_form.addRow("Length", QLabel(f"{r['length_mm']:.1f} mm"))
            self.prop_form.addRow("Resistance", QLabel(f"about {_ohms(r['resistance_ohm'])}"))
        elif kind == "pad":
            num("w_mm", "Width", 0.05, 50)
            num("h_mm", "Height", 0.05, 50)
        elif kind == "hole":
            num("d_mm", "Diameter", 0.1, 100)
        if hasattr(item, "net") and self.advanced:
            e = QLineEdit(item.net)
            e.editingFinished.connect(lambda: setter("net", str)(e.text().strip()))
            self.prop_form.addRow("Net", e)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def redraw(self, live=False):
        self.scene.clear()
        d = self._preview or self.design
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
        refs = {}
        for p in d.pads:
            ref = ed.part_ref(p.name)
            if ref:
                refs.setdefault(ref, []).append(p)
        for ref, pads in refs.items():
            x = max(p.x for p in pads)
            y = max(p.y for p in pads)
            t = self.scene.addSimpleText(ref)
            t.setBrush(QBrush(_COL["label"]))
            t.setScale(0.12)
            t.setTransform(t.transform().scale(1, -1))
            t.setPos(x + 1.0, y + 2.0)
        if self._sel:
            try:
                self._add_geom(ed.geometry(d, self._sel).buffer(0.25), None, QPen(_COL["sel"], 0.15))
            except (IndexError, KeyError):
                self._sel = None
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
        if not hasattr(self, "drc_list"):
            return
        res = drc(self.design, self.diw_config())
        self.last_drc = res
        self.drc_list.clear()
        color = {"Pass": "#4ade80", "Warning": "#fbbf24", "Fail": "#f87171"}[res["status"]]
        plain = {"Pass": "All good: nothing touches, everything fits on the board.",
                 "Warning": "Printable, but check the yellow notes below.",
                 "Fail": "Fix the red problems below before printing."}[res["status"]]
        self.drc_status.setText(f"<b style='color:{color}'>{plain}</b><br>{len(self.design.pads)} pads, "
                                f"{len(self.design.traces)} wires, {len(self.design.holes)} holes, "
                                f"{len(res['nets'])} connection(s)")
        if hasattr(self, "e_check"):
            self.e_check.setText(f"<b style='color:{color}'>{plain}</b>")
        for m in res["messages"]:
            if res["status"] == "Pass":
                continue
            it = QListWidgetItem("• " + m)
            it.setForeground(QColor(color))
            self.drc_list.addItem(it)
        for t in res["traces"]:
            it = QListWidgetItem(f"ℹ {t['name']}: {t['length_mm']:.1f} mm long, about {_ohms(t['resistance_ohm'])}")
            it.setForeground(QColor("#93c5fd"))
            self.drc_list.addItem(it)

    # ------------------------------------------------------------------
    # Files, generators, slicing
    # ------------------------------------------------------------------
    def new_design(self):
        self._sel = None
        self.commit(BoardDesign())
        self._show_props()
        self.fit()

    def load_example(self):
        self._sel = None
        self.commit(example_board())
        self._show_props()
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
        self._sel = None
        self.commit(d)
        self._show_props()
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
            ("turns", "Legs", 6), ("width", "Wire width", 0.6), ("net", "Net", "SG")])
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
            ("pitch", "Pitch", 1.6), ("width", "Wire width", 0.6), ("net", "Net", "COIL")])
        if v:
            tr = spiral_coil(v["x"], v["y"], v["r"], v["turns"], v["pitch"], v["width"], v["net"])
            self.commit(self.design.model_copy(update=dict(traces=self.design.traces + (tr,))))

    def print_mode(self) -> str:
        cb = self.adv_mode_cb if getattr(self, "advanced", False) and hasattr(self, "adv_mode_cb") else self.mode_cb
        return cb.currentData()

    def _request_slice(self):
        self.slice_requested.emit(self.design, self.diw_config(), self.print_mode())


def _next_ref(ref: str) -> str:
    head = ref.rstrip("0123456789")
    tail = ref[len(head):]
    return f"{head}{int(tail) + 1}" if tail else f"{ref}2"


def _ohms(r):
    return f"{r * 1000:.1f} mΩ" if r < 1 else f"{r:.2f} Ω" if r < 1000 else f"{r / 1000:.2f} kΩ"
