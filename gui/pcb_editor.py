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
    QApplication,
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
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shapely.geometry import Point, Polygon

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
from cfrp_core.diw.design import crossovers, pad_shape, trace_shape
from cfrp_core.diw.presets import DIELECTRIC_PRESETS, INK_PRESETS, apply_preset
from cfrp_core.diw.router import ratsnest as ed_ratsnest

from .settings_help import DIW_HELP

MODES = (("Structural: fibre + electronics", "structural"), ("Fibre board only", "fibre"),
         ("Electronics only (ink)", "electronics"),
         ("Embedded: electronics inside the board", "embedded"))
_MIME = "application/x-spcb-part"

TOOLS = (
    ("select", "Select / move", "Click something to select it, drag to move it, R to rotate, Delete to remove"),
    ("rect", "Square board", "Board outline: drag a rectangle"),
    ("rrect", "Rounded board", "Board outline: drag a rectangle with rounded corners (best for fibre)"),
    ("polygon", "Free-shape board", "Board outline: click the corners, double-click to finish"),
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
    "trace2": QColor("#e7a7ff"), "bridge": QColor(76, 200, 150, 150), "pin1": QColor("#ff7a59"),
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
    box = QGroupBox((f"{n}  {title}" if n != "" else title).replace("&", "&&"))
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
        self._sels = []
        self._clipboard = None
        self._vdrag = None
        self._band = None
        self._cursor = (0.0, 0.0)
        self._min_radius = 3.0
        self.path = None
        self.last_drc = {"status": "Pass", "messages": [], "nets": [], "traces": []}

        self.scene = QGraphicsScene(self)
        self.canvas = _Canvas(self.scene)
        self.canvas.pressed.connect(self._press)
        self.canvas.moved.connect(self._move)
        self.canvas.released.connect(self._release)
        self.canvas.double.connect(self._double)
        self.canvas.key.connect(self._key)
        self.canvas.dropped.connect(self._drop)

        # ---- mode switch + top bar ----------------------------------------------
        top = QHBoxLayout()
        self._mode_btns = QButtonGroup(self)
        for label, adv in (("Easy", False), ("Advanced", True)):
            b = QToolButton()
            b.setObjectName("segLeft" if not adv else "segRight")
            b.setText(label)
            b.setCheckable(True)
            b.setMinimumHeight(28)
            b.setMinimumWidth(84)
            b.setChecked(not adv)
            b.setToolTip("Easy: step by step with simple choices.  Advanced: every tool and setting."
                         if not adv else "Every drawing tool, net names and the full DIW process settings.")
            b.clicked.connect(lambda _=False, a=adv: self.set_advanced(a))
            self._mode_btns.addButton(b)
            top.addWidget(b)
        top.setSpacing(4)
        top.addSpacing(16)
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
        imp = QPushButton("Import / export ▾")
        imp.setToolTip("Bring in a board outline (DXF) or a netlist, or export the design as DXF")
        menu = QMenu(imp)
        menu.addAction("Board outline from DXF…", self.import_dxf_dialog)
        menu.addAction("Netlist (which pads connect)…", self.import_netlist_dialog)
        menu.addSeparator()
        menu.addAction("Export DXF…", self.export_dxf_dialog)
        imp.setMenu(menu)
        top.addWidget(imp)
        self.show_links = QCheckBox("Show connections")
        self.show_links.setChecked(True)
        self.show_links.setToolTip("Dashed lines between pads that should be connected (same net) but are "
                                   "not wired yet")
        self.show_links.toggled.connect(lambda _: self.redraw(live=True))
        top.addWidget(self.show_links)
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
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setWidget(self.left)
        left_scroll.setMinimumWidth(270)

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

        # ---- right: selected item on top, then tabs (checks / ink / how to) -----
        self.prop_box = QGroupBox("Selected")
        self.prop_form = QFormLayout(self.prop_box)
        checks = QWidget()
        dl = QVBoxLayout(checks)
        dl.setContentsMargins(4, 4, 4, 4)
        self.drc_status = QLabel("")
        self.drc_status.setWordWrap(True)
        dl.addWidget(self.drc_status)
        self.drc_list = QListWidget()
        self.drc_list.setWordWrap(True)
        self.drc_list.setObjectName("issues")
        dl.addWidget(self.drc_list, 1)
        self.guide = QLabel(GUIDE)
        self.guide.setWordWrap(True)
        self.guide.setTextFormat(Qt.RichText)
        self.guide.setAlignment(Qt.AlignTop)
        self.guide_box = QScrollArea()
        self.guide_box.setWidgetResizable(True)
        self.guide_box.setWidget(self.guide)
        self.diw_box = QScrollArea()
        self.diw_box.setWidgetResizable(True)
        self.diw_box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.diw_box.setWidget(self._build_diw_box())
        self.side_tabs = QTabWidget()
        self.side_tabs.addTab(checks, "Checks")
        self.side_tabs.addTab(self.guide_box, "How to")
        self.side_tabs.addTab(self.diw_box, "Ink settings")
        right_w = QWidget()
        right = QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.prop_box)
        right.addWidget(self.side_tabs, 1)
        right_w.setMinimumWidth(300)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(left_scroll)
        split.addWidget(mid)
        split.addWidget(right_w)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([300, 900, 360])
        self.split = split
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(split)

        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.Redo, self, self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
        # The Easy and Advanced print-mode boxes are one setting.
        self.mode_cb.currentIndexChanged.connect(lambda i: self.adv_mode_cb.setCurrentIndex(i))
        self.adv_mode_cb.currentIndexChanged.connect(lambda i: self.mode_cb.setCurrentIndex(i))
        self.mode_cb.currentIndexChanged.connect(self._diw_states)
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
        b = QPushButton("Auto-route the dashed connections")
        b.setToolTip("Draws a wire for every dashed line (pads with the same net), keeping clear of other "
                     "wires, holes and the edge")
        b.clicked.connect(self.auto_route)
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
        self.e_ink = QComboBox()
        self.e_ink.addItems(list(INK_PRESETS))
        self.e_ink.setToolTip("Which conductive ink you print with (sets line width, speed and resistance)")
        self.e_ink.currentTextChanged.connect(lambda name: self.apply_ink_preset(name))
        lay.addWidget(self.e_ink)
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
        v = QVBoxLayout(w)
        v.setSpacing(6)
        box, lay = _step("", "Tools")
        grid = QGridLayout()
        grid.setSpacing(4)
        self._tool_btns = QButtonGroup(self)
        self._tool_btns.setExclusive(True)
        for k, (key, label, tip) in enumerate(TOOLS):
            b = QToolButton()
            b.setObjectName("tool")
            b.setText(label)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setMinimumHeight(30)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setChecked(key == "select")
            b.clicked.connect(lambda _=False, kk=key: self.set_tool(kk))
            self._tool_btns.addButton(b)
            if key == "select":
                grid.addWidget(b, 0, 0, 1, 2)
            else:
                grid.addWidget(b, 1 + (k - 1) // 2, (k - 1) % 2)
        lay.addLayout(grid)
        b = QPushButton("Auto-route connections")
        b.setToolTip("Draw a wire for every dashed connection (same-net pads)")
        b.clicked.connect(self.auto_route)
        lay.addWidget(b)
        v.addWidget(box)

        box, lay = _step("", "New item settings")
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.grid_spin = _spin(0.1, 10, 1.0, 0.1, 2)
        self.grid_spin.setToolTip("Everything snaps to this grid")
        self.grid_spin.valueChanged.connect(self._set_grid)
        form.addRow("Grid", self.grid_spin)
        self.width_spin = _spin(0.2, 20, 0.8)
        self.width_spin.setToolTip("Width of new wires")
        form.addRow("Wire width", self.width_spin)
        self.level2_chk = QCheckBox("Crossover (level 2)")
        self.level2_chk.setToolTip("New wires go on level 2: they may cross other nets' wires, which get a "
                                   "printed insulating bridge where they cross. They join their own net where "
                                   "they touch it. Never over another net's pad.")
        form.addRow("", self.level2_chk)
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
        lay.addLayout(form)
        v.addWidget(box)

        box, lay = _step("", "Sensors")
        grid = QGridLayout()
        for k, (label, cb, tip) in enumerate((
                ("Strain gauge…", self._add_meander, "Zigzag wire whose resistance changes when the board bends"),
                ("Electrodes…", self._add_ide, "Interlocking combs: touch / humidity / strain sensing"),
                ("Coil…", self._add_coil, "Flat spiral: NFC antenna, inductor or heater"))):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            grid.addWidget(b, k // 2, k % 2)
        lay.addLayout(grid)
        v.addWidget(box)

        box, lay = _step("", "Selection")
        row = QHBoxLayout()
        for label, cb, tip in (("⟲ 90°", lambda: self.rotate_selected(90), "Rotate (R)"),
                               ("⟳ 90°", lambda: self.rotate_selected(-90), "Rotate back (Shift+R)"),
                               ("Delete", self.delete_selected, "Remove (Delete)")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            row.addWidget(b)
        lay.addLayout(row)
        row = QHBoxLayout()
        for label, cb, tip in (("Copy", self.copy_selected, "Ctrl+C"), ("Paste", self.paste, "Ctrl+V"),
                               ("Duplicate", lambda: (self.copy_selected(), self.paste()), "Ctrl+D")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            row.addWidget(b)
        lay.addLayout(row)
        tip = QLabel("Shift+click adds to the selection · drag on empty board to select an area · "
                     "select a wire or the board edge to drag its corners")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        v.addWidget(box)

        box, lay = _step("", "Print")
        self.adv_mode_cb = QComboBox()
        for label, key in MODES:
            self.adv_mode_cb.addItem(label, key)
        self.adv_mode_cb.setToolTip(DIW_HELP["print_mode"])
        lay.addWidget(self.adv_mode_cb)
        b = QPushButton("Slice board  ▶")
        b.setObjectName("primary")
        b.setMinimumHeight(32)
        b.clicked.connect(self._request_slice)
        lay.addWidget(b)
        v.addWidget(box)
        v.addStretch()
        return w

    def set_advanced(self, on: bool):
        self.advanced = bool(on)
        self.left.setCurrentIndex(1 if on else 0)
        self.side_tabs.setTabVisible(2, on)          # Ink settings: advanced only
        self.side_tabs.setTabVisible(1, True)
        # (the tab shows / hides its page: forcing the page visible drew it
        # over whichever tab was open)
        if not on and self.side_tabs.currentIndex() == 2:
            self.side_tabs.setCurrentIndex(0)
        for b, adv in zip(self._mode_btns.buttons(), (False, True), strict=False):
            b.setChecked(adv == self.advanced)
        self._diw_states()

    # ------------------------------------------------------------------
    # DIW settings (advanced)
    # ------------------------------------------------------------------
    def _build_diw_box(self):
        box = QWidget()
        f = QFormLayout(box)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
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
        self.d_early = _spin(0, 10, d.stop_early_mm, 0.1, 2)
        self.d_prime = _spin(0, 10, d.prime_e, 0.01, 3, "")
        self.d_retract = _spin(0, 10, d.retract_e, 0.01, 3, "")
        self.d_uv = QCheckBox("UV cure between ink layers")
        self.d_uv_s = _spin(0, 120, d.uv_cure_s, 0.5, 1, " s")
        self.d_uv_on = QLineEdit(d.uv_cure_on)
        self.d_uv_off = QLineEdit(d.uv_cure_off)
        self.d_embed = QSpinBox()
        self.d_embed.setRange(0, 500)
        self.d_embed.setSpecialValueText("middle")
        self.d_spread = _spin(1.0, 3.0, d.spread_factor, 0.05, 2, " x")
        self.d_overlap = _spin(0, 5, d.min_insulation_overlap_mm, 0.05, 2)
        self.d_jmax = _spin(0.1, 500, d.max_current_density_a_mm2, 1, 1, " A/mm²")
        self.d_wait = _spin(0, 600, d.layer_wait_s, 1, 0, " s")
        self.d_bridge_n = QSpinBox()
        self.d_bridge_n.setRange(1, 10)
        self.d_bridge_n.setValue(d.bridge_layers)
        self.d_bridge_m = _spin(0, 5, d.bridge_margin_mm, 0.05, 2)
        self.d_ink_preset = QComboBox()
        self.d_ink_preset.addItems(["(keep)"] + list(INK_PRESETS))
        self.d_diel_preset = QComboBox()
        self.d_diel_preset.addItems(["(keep)"] + list(DIELECTRIC_PRESETS))
        self.d_ink_preset.currentTextChanged.connect(
            lambda n: n != "(keep)" and self.apply_ink_preset(n))
        self.d_diel_preset.currentTextChanged.connect(
            lambda n: n != "(keep)" and self.apply_ink_preset(None, n))
        from .profiles_ui import ProfileRow
        self.ink_profile = ProfileRow("ink", lambda: self.diw_config().model_dump(),
                                      lambda v: self.load_diw_config(DiwConfig.model_validate(v)))
        f.addRow(QLabel("Ink profile"), self.ink_profile)
        rows = (("Ink preset", self.d_ink_preset, "ink_preset"),
                ("Insulation preset", self.d_diel_preset, "diel_preset"),("Needle", self.d_nozzle, "nozzle"), ("Line width", self.d_lw, "line_width"),
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
                ("Surface height", self.d_surface, "surface_z"),
                ("Close valve early", self.d_early, "stop_early"), ("Prime at start", self.d_prime, "prime_e"),
                ("Retract at end", self.d_retract, "retract_e"), ("", self.d_uv, "uv_cure"),
                ("UV cure time", self.d_uv_s, "uv_cure_s"), ("UV on command", self.d_uv_on, "uv_on"),
                ("UV off command", self.d_uv_off, "uv_off"), ("Embed after layer", self.d_embed, "embed_after"),
                ("Bead spread", self.d_spread, "spread_factor"),
                ("Ink inside insulation", self.d_overlap, "min_insulation_overlap"),
                ("Max current density", self.d_jmax, "max_current_density"),
                ("Dry between layers", self.d_wait, "layer_wait"),
                ("Crossover layers", self.d_bridge_n, "bridge_layers"),
                ("Crossover overlap", self.d_bridge_m, "bridge_margin"))
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
        for sig in (self.d_mode.currentTextChanged, self.d_diel_on.toggled, self.d_enc.toggled, self.d_uv.toggled):
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
                  "Only for Electronics only: otherwise the ink goes on the fibre board's top."),
                 (self.d_early, pneu, "Only for pneumatic (valve) dispensing."),
                 (self.d_prime, not pneu, "Only for volumetric dispensing."),
                 (self.d_retract, not pneu, "Only for volumetric dispensing."),
                 (self.d_uv_s, self.d_uv.isChecked(), "UV cure between layers is off."),
                 (self.d_uv_on, self.d_uv.isChecked(), "UV cure between layers is off."),
                 (self.d_uv_off, self.d_uv.isChecked(), "UV cure between layers is off."),
                 (self.d_embed, self.print_mode() == "embedded", "Only for the Embedded print mode."),
                 (self.d_overlap, self.d_diel_on.isChecked(), "Insulation is off."),
                 (self.d_bridge_n, any(t.level == 2 for t in self.design.traces),
                  "No crossover wires (level 2) on this board."),
                 (self.d_bridge_m, any(t.level == 2 for t in self.design.traces),
                  "No crossover wires (level 2) on this board."))
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
            surface_z_mm=self.d_surface.value(), stop_early_mm=self.d_early.value(),
            prime_e=self.d_prime.value(), retract_e=self.d_retract.value(),
            uv_cure_between_layers=self.d_uv.isChecked(), uv_cure_s=self.d_uv_s.value(),
            uv_cure_on=self.d_uv_on.text(), uv_cure_off=self.d_uv_off.text(),
            embed_after_layer=self.d_embed.value(), spread_factor=self.d_spread.value(),
            min_insulation_overlap_mm=self.d_overlap.value(), max_current_density_a_mm2=self.d_jmax.value(),
            layer_wait_s=self.d_wait.value(), bridge_layers=self.d_bridge_n.value(),
            bridge_margin_mm=self.d_bridge_m.value())

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
        self._diw_states()                          # e.g. crossover settings follow level-2 wires
        self.design_changed.emit(design)

    def undo(self):
        if self._undo:
            self._redo.append(self.design)
            self.design = self._undo.pop()
            self._sel = None
            self.redraw()
            self._show_props()
            self._diw_states()
            self.design_changed.emit(self.design)

    def redo(self):
        if self._redo:
            self._undo.append(self.design)
            self.design = self._redo.pop()
            self._sel = None
            self.redraw()
            self._show_props()
            self._diw_states()
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

    # --- selection (several items; the first one is shown in "Selected") -------
    @property
    def _sel(self):
        return self._sels[0] if getattr(self, "_sels", None) else None

    @_sel.setter
    def _sel(self, value):
        self._sels = [value] if value else []

    def _items(self):
        """The selected items that can be moved / rotated / deleted."""
        return [s for s in self._sels if s[0] != "outline"]

    def _vertex_target(self):
        """The outline / wire / zone whose corners have handles, if exactly
        that one thing is selected."""
        if len(self._sels) == 1 and self._sels[0][0] in ("outline", "trace", "zone"):
            return self._sels[0]
        return None

    def _handle_tol(self):
        return max(0.4, 6.0 / max(abs(self.canvas.transform().m11()), 1e-6))

    def _press(self, x, y, button):
        rx, ry = x, y
        x, y = self.snap(x, y)
        d = self.design
        t = self._tool
        mods = QApplication.keyboardModifiers()
        if t == "select":
            vt = self._vertex_target()
            if vt is not None:
                hit = ed.find_vertex(d, rx, ry, self._handle_tol(), [vt])
                if hit is not None:
                    if button == Qt.RightButton:
                        self.commit(ed.delete_vertex(d, *hit))
                        return
                    self._vdrag = hit
                    return
        if button == Qt.RightButton:
            self._finish()
            return
        if t == "select":
            got = ed.pick(d, x, y)
            if got is None and Polygon(d.outline).exterior.distance(Point(rx, ry)) <= self._handle_tol():
                got = ("outline", None)
            if mods & Qt.ShiftModifier:
                if got in self._sels:
                    self._sels.remove(got)
                elif got is not None:
                    self._sels.append(got)
            elif got is None:
                self._sels = []
                self._band = (x, y)
            elif got not in self._sels:
                self._sels = [got]
            self._drag_from = (x, y) if got is not None and got[0] != "outline" else None
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
        if held and getattr(self, "_vdrag", None):
            self._preview = ed.move_vertex(self.design, *self._vdrag, sx, sy)
            self.redraw(live=True)
        elif held and getattr(self, "_band", None):
            self.redraw(live=True)
        elif held and self._tool == "select" and self._items() and self._drag_from:
            dx, dy = sx - self._drag_from[0], sy - self._drag_from[1]
            self._preview = ed.transform_many(self.design, self._items(), dx, dy) if (dx or dy) else None
            self.redraw(live=True)
        elif self._draft or (held and self._tool in ("rect", "rrect")):
            self.redraw(live=True)

    def _release(self, x, y):
        x, y = self.snap(x, y)
        d = self.design
        if getattr(self, "_vdrag", None):
            target, k = self._vdrag
            self._vdrag = None
            self._preview = None
            self.commit(ed.move_vertex(d, target, k, x, y))
            return
        if getattr(self, "_band", None):
            x0, y0 = self._band
            self._band = None
            if abs(x - x0) > 0.5 and abs(y - y0) > 0.5:
                self._sels = ed.select_in_rect(d, x0, y0, x, y)
            self.redraw()
            self._show_props()
            return
        if self._tool in ("rect", "rrect") and self._drag_from:
            x0, y0 = self._drag_from
            self._drag_from = None
            if abs(x - x0) > 1 and abs(y - y0) > 1:
                r = 2.0 * self._min_radius if self._tool == "rrect" else 0.0
                outline = ed.rectangle_outline(abs(x - x0), abs(y - y0), r)
                ox, oy = min(x0, x), min(y0, y)
                self.commit(d.model_copy(update=dict(outline=tuple((px + ox, py + oy) for px, py in outline))))
            self.redraw()
        elif self._tool == "select" and self._items() and self._drag_from:
            dx, dy = x - self._drag_from[0], y - self._drag_from[1]
            self._drag_from = None
            self._preview = None
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                self.commit(ed.transform_many(d, self._items(), dx, dy))
            else:
                self.redraw()
            self._show_props()

    def _double(self, x, y):
        """Double-click: finish a polyline, or add a bend to the selected
        wire / zone / outline where its edge was clicked."""
        if self._draft:
            self._finish()
            return
        vt = self._vertex_target()
        if self._tool == "select" and vt is not None:
            seg = ed.find_segment(self.design, x, y, self._handle_tol(), [vt])
            if seg is not None:
                sx, sy = self.snap(x, y)
                self.commit(ed.insert_vertex(self.design, seg[0], seg[1], sx, sy))

    def _key(self, key, mods=None):
        ctrl = mods is not None and bool(mods & Qt.ControlModifier)
        shift = mods is not None and bool(mods & Qt.ShiftModifier)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        elif key == Qt.Key_Escape:
            self._draft = []
            self._sels = []
            self.set_tool("select")
            self._show_props()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif key == Qt.Key_R and not ctrl:
            self.rotate_selected(-90 if shift else 90)
        elif ctrl and key == Qt.Key_C:
            self.copy_selected()
        elif ctrl and key == Qt.Key_V:
            self.paste()
        elif ctrl and key == Qt.Key_D:
            self.copy_selected()
            self.paste()
        elif ctrl and key == Qt.Key_A:
            b = Polygon(self.design.outline).bounds
            self._sels = ed.select_in_rect(self.design, b[0] - 1, b[1] - 1, b[2] + 1, b[3] + 1)
            self.redraw()
            self._show_props()

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
            level = 2 if getattr(self, "level2_chk", None) is not None and self.level2_chk.isChecked() else 1
            tr = Trace(points=tuple(pts), width_mm=self._wire_width(), net=net, level=level)
            self.commit(d.model_copy(update=dict(traces=d.traces + (tr,))))
        elif self._tool == "zone" and len(pts) >= 3:
            z = Zone(points=tuple(pts), kind=self.zone_kind.currentText(), net=self.net_edit.text().strip())
            self.commit(d.model_copy(update=dict(zones=d.zones + (z,))))
        self.redraw()

    def pick(self, x, y, tol=0.6):
        return ed.pick(self.design, x, y, tol)

    def delete_selected(self):
        items = self._items()
        if not items:
            return
        self._sels = []
        self.commit(ed.delete_many(self.design, items))
        self._show_props()

    def rotate_selected(self, angle):
        items = self._items()
        if not items or all(s[0] == "hole" for s in items):
            return
        self.commit(ed.transform_many(self.design, items, angle_deg=angle))
        self._show_props()

    def copy_selected(self):
        if self._items():
            self._clipboard = ed.copy_items(self.design, self._items())
            self.hint.setText(f"Copied {len(self._items())} item(s) · Ctrl+V pastes")

    def paste(self):
        clip = getattr(self, "_clipboard", None)
        if not clip:
            return
        d, sels = ed.paste_items(self.design, clip)
        self._sels = sels
        self.commit(d)
        self._show_props()

    def auto_route(self):
        """Draw every missing connection (pads with the same net)."""
        from cfrp_core.diw.router import auto_route
        before = len(self.design.traces)
        d, rep = auto_route(self.design, self.diw_config(), width=self._wire_width())
        self.commit(d)
        msg = f"Auto-route: {len(d.traces) - before} wire(s) drawn."
        if rep.get("crossovers"):
            msg += (f" {rep['crossovers']} of them cross another net on level 2 (an insulating bridge is "
                    f"printed under the crossing).")
        if rep["failed"]:
            msg += f" Could not reach: {', '.join(rep['failed'])} (blocked; move parts or route by hand)."
        elif not ed_ratsnest(d):
            msg += " Everything is connected."
        self.hint.setText(msg)
        return rep

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
        if kind == "outline":
            self.prop_box.setTitle("Selected: board outline")
            lbl = QLabel("Drag a white square to move a corner · double-click an edge to add a corner · "
                         "right-click a corner to remove it")
            lbl.setWordWrap(True)
            self.prop_form.addRow(lbl)
            self.prop_form.addRow("Corners", QLabel(str(len(d.outline))))
            return
        if len(self._items()) > 1:
            self.prop_box.setTitle(f"Selected: {len(self._items())} items")
            row = QHBoxLayout()
            for label, cb in (("⟲ 90°", lambda: self.rotate_selected(90)), ("⟳ 90°", lambda: self.rotate_selected(-90)),
                              ("Copy", self.copy_selected), ("Delete", self.delete_selected)):
                b = QPushButton(label)
                b.clicked.connect(cb)
                row.addWidget(b)
            holder = QWidget()
            holder.setLayout(row)
            self.prop_form.addRow(holder)
            lbl = QLabel("Drag any of them to move them all together.")
            lbl.setObjectName("hint")
            self.prop_form.addRow(lbl)
            return
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
            self.prop_form.addRow("Carries up to", QLabel(f"about {r['capacity_a']:.2g} A"))
            if self.advanced:
                lvl = QComboBox()
                lvl.addItems(["1 (on the insulation)", "2 (crossover)"])
                lvl.setCurrentIndex(item.level - 1)
                lvl.setToolTip("Level 2 may cross other nets' wires over a printed insulating bridge.")
                lvl.currentIndexChanged.connect(lambda i: setter("level", int)(i + 1))
                self.prop_form.addRow("Level", lvl)
                cur = _spin(0, 100, float(item.current_a), 0.1, 2, " A")
                cur.setSpecialValueText("not checked")
                cur.setToolTip("Current this wire must carry: the check warns if it would overheat.")
                cur.setKeyboardTracking(False)
                cur.valueChanged.connect(setter("current_a"))
                self.prop_form.addRow("Current", cur)
                if item.current_a > 0:
                    self.prop_form.addRow("Voltage drop", QLabel(f"{r['drop_v'] * 1000:.0f} mV, "
                                                                 f"{r['power_w'] * 1000:.0f} mW"))
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
            if t.level == 1:
                self._add_geom(trace_shape(t), _COL["trace"])
        for c in crossovers(d):
            self._add_geom(c["geom"], _COL["bridge"])
        for t in d.traces:
            if t.level == 2:
                self._add_geom(trace_shape(t), _COL["trace2"], QPen(_COL["trace2"].darker(140), 0.08))
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
            first = next((p for p in pads if p.name.endswith(".1")), None)
            if first is not None and len(pads) > 1:
                # pin 1: a dot just outside the pad, away from the part's middle
                mx, my = sum(p.x for p in pads) / len(pads), sum(p.y for p in pads) / len(pads)
                vx, vy = first.x - mx, first.y - my
                n = math.hypot(vx, vy) or 1.0
                r = 0.35
                off = max(first.w_mm, first.h_mm) / 2 + 0.6
                cx, cy = first.x + vx / n * off, first.y + vy / n * off
                self.scene.addEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r), QPen(Qt.NoPen), QBrush(_COL["pin1"]))
            x = max(p.x for p in pads)
            y = max(p.y for p in pads)
            t = self.scene.addSimpleText(ref)
            t.setBrush(QBrush(_COL["label"]))
            t.setScale(0.12)
            t.setTransform(t.transform().scale(1, -1))
            t.setPos(x + 1.0, y + 2.0)
        keep = []
        for sel in self._sels:
            try:
                g = (Polygon(d.outline).exterior.buffer(0.35) if sel[0] == "outline"
                     else ed.geometry(d, sel).buffer(0.25))
            except (IndexError, KeyError):
                continue
            keep.append(sel)
            self._add_geom(g, None, QPen(_COL["sel"], 0.15))
        self._sels = keep
        vt = self._vertex_target()
        if vt is not None:
            r = self._handle_tol() * 0.6
            pts, _ = ed._points(d, vt)
            for px, py in pts:
                self.scene.addRect(QRectF(px - r, py - r, 2 * r, 2 * r), QPen(_COL["sel"], 0.08),
                                   QBrush(QColor("#e8ecff")))
        if self.show_links.isChecked():
            pen_l = QPen(QColor(245, 185, 66, 200), 0.12, Qt.DashLine)
            for link in ed_ratsnest(d):
                self.scene.addLine(link["a"][0], link["a"][1], link["b"][0], link["b"][1], pen_l)
        if getattr(self, "_band", None):
            x0, y0 = self._band
            x1, y1 = self._cursor
            self.scene.addRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)),
                               QPen(_COL["sel"], 0.1, Qt.DashLine), QBrush(QColor(91, 141, 239, 30)))
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
        for n in res.get("notes", ()):
            it = QListWidgetItem("ℹ " + n)
            it.setForeground(QColor("#93c5fd"))
            self.drc_list.addItem(it)
        for t in res["traces"]:
            it = QListWidgetItem(f"ℹ {t['name']}: {t['length_mm']:.1f} mm long, about {_ohms(t['resistance_ohm'])}, "
                                 f"up to {t['capacity_a']:.2g} A" + (" (level 2)" if t.get("level") == 2 else ""))
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

    def apply_ink_preset(self, ink=None, dielectric=None):
        """Put an ink / insulation preset's values into the ink settings."""
        cfg = apply_preset(self.diw_config(), ink, dielectric)
        setters = ((self.d_nozzle, cfg.nozzle_mm), (self.d_lw, cfg.line_width_mm), (self.d_lh, cfg.layer_height_mm),
                   (self.d_cond, cfg.conductor_layers), (self.d_vc, cfg.speed_conductor_mm_s),
                   (self.d_vd, cfg.speed_dielectric_mm_s), (self.d_cure, cfg.cured_thickness_ratio),
                   (self.d_enc_n, cfg.encapsulation_layers), (self.d_uv_s, cfg.uv_cure_s))
        for w, v in setters:
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.d_rho.setText(f"{cfg.resistivity_ohm_mm:g}")
        self.d_enc.setChecked(cfg.encapsulate)
        self.d_uv.setChecked(cfg.uv_cure_between_layers)
        self._diw_states()
        self._run_drc()

    def import_dxf_dialog(self, path=None):
        from cfrp_core.diw.io import import_dxf
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Board outline from DXF", "", "DXF (*.dxf *.DXF)")
        if not path:
            return
        try:
            d = import_dxf(path, self.design)
        except Exception as exc:
            QMessageBox.critical(self, "DXF import failed", str(exc))
            return
        self.commit(d)
        self.fit()
        self.hint.setText(f"Imported the board outline and {len(d.holes)} hole(s) from {path}.")

    def import_netlist_dialog(self, path=None):
        from pathlib import Path

        from cfrp_core.diw.io import apply_netlist
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Netlist", "", "Netlist (*.csv *.txt *.net);;All (*)")
        if not path:
            return
        d, rep = apply_netlist(self.design, Path(path).read_text(encoding="utf-8", errors="replace"))
        self.commit(d)
        msg = f"Netlist: {rep['assigned']} pad(s) got their net."
        if rep["missing"]:
            msg += f" Not on the board: {', '.join(rep['missing'][:8])}" + (" …" if len(rep["missing"]) > 8 else "")
        self.hint.setText(msg + " Dashed lines show what still needs wiring; Auto-route draws it.")

    def export_dxf_dialog(self, path=None):
        from cfrp_core.diw.io import export_dxf
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Export DXF", "board.dxf", "DXF (*.dxf)")
        if path:
            export_dxf(self.design, path)

    def print_mode(self) -> str:
        cb = self.adv_mode_cb if getattr(self, "advanced", False) and hasattr(self, "adv_mode_cb") else self.mode_cb
        return cb.currentData()

    def set_print_mode(self, mode: str) -> None:
        i = self.mode_cb.findData(mode)
        if i >= 0:
            self.mode_cb.setCurrentIndex(i)           # the Advanced box follows

    def load_diw_config(self, cfg: DiwConfig) -> None:
        """Show `cfg` in the Ink settings (the inverse of diw_config)."""
        def put(w, value):
            w.blockSignals(True)
            try:
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(value))
                elif isinstance(w, QComboBox):
                    w.setCurrentText(str(value))
                elif isinstance(w, QLineEdit):
                    w.setText(str(value))
                else:
                    w.setValue(value)
            finally:
                w.blockSignals(False)
        ox, oy, oz = cfg.tool_offset
        for w, value in ((self.d_nozzle, cfg.nozzle_mm), (self.d_lw, cfg.line_width_mm),
                         (self.d_lh, cfg.layer_height_mm), (self.d_cond, cfg.conductor_layers),
                         (self.d_diel_on, cfg.dielectric), (self.d_diel, cfg.dielectric_layers),
                         (self.d_margin, cfg.dielectric_margin_mm), (self.d_enc, cfg.encapsulate),
                         (self.d_enc_n, cfg.encapsulation_layers), (self.d_clear, cfg.clearance_mm),
                         (self.d_edge, cfg.edge_clearance_mm), (self.d_vc, cfg.speed_conductor_mm_s),
                         (self.d_vd, cfg.speed_dielectric_mm_s), (self.d_vt, cfg.travel_speed_mm_s),
                         (self.d_hop, cfg.z_hop_mm), (self.d_mode, cfg.dispense), (self.d_on, cfg.dispense_on),
                         (self.d_off, cfg.dispense_off), (self.d_on_ms, cfg.on_delay_ms),
                         (self.d_off_ms, cfg.off_delay_ms), (self.d_epm, cfg.e_per_mm3),
                         (self.d_tool, cfg.tool_select), (self.d_ox, ox), (self.d_oy, oy), (self.d_oz, oz),
                         (self.d_pause, cfg.pause_before), (self.d_rho, f"{cfg.resistivity_ohm_mm:g}"),
                         (self.d_cure, cfg.cured_thickness_ratio), (self.d_carbon, cfg.substrate_conductive),
                         (self.d_surface, cfg.surface_z_mm), (self.d_early, cfg.stop_early_mm),
                         (self.d_prime, cfg.prime_e), (self.d_retract, cfg.retract_e),
                         (self.d_uv, cfg.uv_cure_between_layers), (self.d_uv_s, cfg.uv_cure_s),
                         (self.d_uv_on, cfg.uv_cure_on), (self.d_uv_off, cfg.uv_cure_off),
                         (self.d_embed, cfg.embed_after_layer), (self.d_spread, cfg.spread_factor),
                         (self.d_overlap, cfg.min_insulation_overlap_mm), (self.d_jmax, cfg.max_current_density_a_mm2),
                         (self.d_wait, cfg.layer_wait_s), (self.d_bridge_n, cfg.bridge_layers),
                         (self.d_bridge_m, cfg.bridge_margin_mm)):
            put(w, value)
        self._diw_states()
        self._run_drc()

    def _request_slice(self):
        self.slice_requested.emit(self.design, self.diw_config(), self.print_mode())


def _next_ref(ref: str) -> str:
    head = ref.rstrip("0123456789")
    tail = ref[len(head):]
    return f"{head}{int(tail) + 1}" if tail else f"{ref}2"


def _ohms(r):
    return f"{r * 1000:.1f} mΩ" if r < 1 else f"{r:.2f} Ω" if r < 1000 else f"{r / 1000:.2f} kΩ"
