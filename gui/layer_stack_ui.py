"""The structural PCB's ink layers as an editable list, bottom first: each
row says what the layer is and what it covers (or its wire level); the
selected layer's fill, direction, height, speed and UV cure are set below
the list. Off "Set each layer myself", it shows the automatic stack from the
layer counts, greyed."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from cfrp_core.diw import DiwLayerSpec

KINDS = (("Insulation", "dielectric"), ("Ink", "conductor"), ("Cover", "encapsulation"))
COVERS = (("under the wires", "wires"), ("whole board", "board"))
LEVELS = (("level 1", 1), ("level 2 (crossover)", 2))
FILLS = (("lines (raster)", "raster"), ("rings (concentric)", "concentric"))
COLUMNS = ("On", "Layer", "Covers / level")
TIPS = {
    "On": "Untick to skip this layer.",
    "Layer": "Insulation keeps the ink off the carbon fibre; Ink is the conductive wires and pads; "
             "Cover protects the wires afterwards (pads stay open).",
    "Covers / level": "Insulation and cover: only under / over the wires (plus the margin), or the whole top "
                      "of the board. Ink: wire level 1, or level 2 for crossover wires.",
    "fill": "Insulation and cover: straight lines at the angle, or rings following the edge. Ink always "
            "follows the wires as drawn.",
    "angle": "Direction of the lines (raster fill).",
    "height": "This layer's height (0 = the Layer height setting).",
    "speed": "This layer's print speed (0 = the usual speed for its kind).",
    "cure": "Switch the UV light on once this layer is printed (also without 'UV cure between layers').",
}


def _combo(items, value):
    cb = QComboBox()
    for label, key in items:
        cb.addItem(label, key)
    cb.setCurrentIndex(max(cb.findData(value), 0))
    return cb


def _num(lo, hi, step, dec, special="", suffix=""):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setKeyboardTracking(False)
    if special:
        w.setSpecialValueText(special)
    if suffix:
        w.setSuffix(suffix)
    return w


class LayerStackTable(QWidget):
    """`stack()` -> tuple[DiwLayerSpec]; `custom()` -> bool; emits
    `changed` on every edit."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._specs: list[DiwLayerSpec] = []
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.custom_chk = QCheckBox("Set each layer myself")
        self.custom_chk.setToolTip("Off: the layers follow the counts in Ink settings (shown greyed here). "
                                   "On: add, remove, reorder and set every layer.")
        self.custom_chk.toggled.connect(self._custom_toggled)
        lay.addWidget(self.custom_chk)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        for c, name in enumerate(COLUMNS):
            self.table.horizontalHeaderItem(c).setToolTip(TIPS[name])
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.currentCellChanged.connect(lambda *_: self._show_detail())
        lay.addWidget(self.table, 1)

        grid = QGridLayout()
        grid.setSpacing(4)
        self.buttons = []
        for k, (label, cb, tip) in enumerate((
                ("+ Insulation", lambda: self.add_row("dielectric"), "Add an insulation layer after the selected one"),
                ("+ Ink", lambda: self.add_row("conductor"), "Add an ink (wire) layer after the selected one"),
                ("+ Cover", lambda: self.add_row("encapsulation"), "Add a cover layer after the selected one"),
                ("Remove", self.remove_row, "Remove the selected layer"),
                ("▲ Earlier", lambda: self.move_row(-1), "Print the selected layer earlier"),
                ("▼ Later", lambda: self.move_row(1), "Print the selected layer later"))):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            grid.addWidget(b, k // 3, k % 3)
            self.buttons.append(b)
        lay.addLayout(grid)

        self.detail = QWidget()
        form = QFormLayout(self.detail)
        form.setContentsMargins(0, 4, 0, 0)
        self.fill_cb = _combo(FILLS, "raster")
        self.angle_spin = _num(-180, 180, 15, 0, suffix=" °")
        self.height_spin = _num(0, 5, 0.05, 2, "usual", " mm")
        self.speed_spin = _num(0, 200, 0.5, 1, "usual", " mm/s")
        self.cure_chk = QCheckBox("UV cure after this layer")
        for label, w, tip in (("Fill", self.fill_cb, "fill"), ("Angle", self.angle_spin, "angle"),
                              ("Height", self.height_spin, "height"), ("Speed", self.speed_spin, "speed"),
                              ("", self.cure_chk, "cure")):
            w.setToolTip(TIPS[tip])
            form.addRow(label, w)
        self.fill_cb.currentIndexChanged.connect(self._detail_changed)
        for w in (self.angle_spin, self.height_spin, self.speed_spin):
            w.valueChanged.connect(self._detail_changed)
        self.cure_chk.toggled.connect(self._detail_changed)
        lay.addWidget(self.detail)
        hint = QLabel("Top row is printed first. Crossover insulation is added by itself before the first "
                      "level-2 ink layer.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._apply_enabled()

    # ------------------------------------------------------------------
    def custom(self) -> bool:
        return self.custom_chk.isChecked()

    def set_stack(self, specs, custom: bool):
        """Show `specs`; `custom` = the user's own stack (editable)."""
        row = max(self.table.currentRow(), 0)
        self._loading = True
        try:
            self.custom_chk.blockSignals(True)
            self.custom_chk.setChecked(bool(custom))
            self.custom_chk.blockSignals(False)
            self._specs = list(specs)
            self._rebuild()
            if self._specs:
                self.table.setCurrentCell(min(row, len(self._specs) - 1), 1)
        finally:
            self._loading = False
        self._show_detail()
        self._apply_enabled()

    def stack(self) -> tuple[DiwLayerSpec, ...]:
        return tuple(self._specs)

    def add_row(self, kind):
        r = self.table.currentRow()
        at = r + 1 if r >= 0 else len(self._specs)
        self._specs.insert(at, DiwLayerSpec(kind=kind, angle_deg=45.0 if kind == "encapsulation" else 0.0))
        self._rebuild()
        self.table.setCurrentCell(at, 1)
        self._emit()

    def remove_row(self):
        r = self.table.currentRow()
        if 0 <= r < len(self._specs):
            del self._specs[r]
            self._rebuild()
            if self._specs:
                self.table.setCurrentCell(min(r, len(self._specs) - 1), 1)
            self._emit()

    def move_row(self, step):
        r = self.table.currentRow()
        j = r + step
        if r < 0 or not 0 <= j < len(self._specs):
            return
        self._specs[r], self._specs[j] = self._specs[j], self._specs[r]
        self._rebuild()
        self.table.setCurrentCell(j, 1)
        self._emit()

    # ------------------------------------------------------------------
    def _rebuild(self):
        loading, self._loading = self._loading, True
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for r, spec in enumerate(self._specs):
                self.table.insertRow(r)
                on = QCheckBox()
                on.setChecked(spec.enabled)
                kind = _combo(KINDS, spec.kind)
                second = QComboBox()
                for c, w in enumerate((on, kind, second)):
                    w.setToolTip(TIPS[COLUMNS[c]])
                    self.table.setCellWidget(r, c, w)
                self._fill_second(second, spec)
                on.toggled.connect(lambda v, w=on: self._row_changed(w))
                kind.currentIndexChanged.connect(lambda _=0, w=kind: self._row_changed(w, kind_changed=True))
                second.currentIndexChanged.connect(lambda _=0, w=second: self._row_changed(w))
        finally:
            self.table.blockSignals(False)
            self._loading = loading
        self._apply_enabled()

    @staticmethod
    def _fill_second(second, spec):
        second.blockSignals(True)
        second.clear()
        items = LEVELS if spec.kind == "conductor" else COVERS
        for label, key in items:
            second.addItem(label, key)
        second.setCurrentIndex(max(second.findData(spec.level if spec.kind == "conductor" else spec.covers), 0))
        second.blockSignals(False)

    def _row_of(self, widget):
        for r in range(self.table.rowCount()):
            if any(self.table.cellWidget(r, c) is widget for c in range(len(COLUMNS))):
                return r
        return -1

    def _row_changed(self, widget, kind_changed=False):
        if self._loading:
            return
        r = self._row_of(widget)
        if r < 0:
            return
        on, kind, second = (self.table.cellWidget(r, c) for c in range(len(COLUMNS)))
        spec = self._specs[r]
        new_kind = kind.currentData()
        update = dict(enabled=on.isChecked(), kind=new_kind)
        if kind_changed:
            update.update(covers="wires", level=1)
            spec = spec.model_copy(update=update)
            self._fill_second(second, spec)
        elif new_kind == "conductor":
            update["level"] = second.currentData()
        else:
            update["covers"] = second.currentData()
        self._specs[r] = spec.model_copy(update=update)
        self._show_detail()
        self._emit()

    def _show_detail(self):
        r = self.table.currentRow()
        spec = self._specs[r] if 0 <= r < len(self._specs) else None
        widgets = (self.fill_cb, self.angle_spin, self.height_spin, self.speed_spin, self.cure_chk)
        for w in widgets:
            w.blockSignals(True)
        try:
            if spec is not None:
                self.fill_cb.setCurrentIndex(max(self.fill_cb.findData(spec.fill), 0))
                self.angle_spin.setValue(spec.angle_deg)
                self.height_spin.setValue(spec.height_mm)
                self.speed_spin.setValue(spec.speed_mm_s)
                self.cure_chk.setChecked(spec.cure)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._apply_enabled()

    def _detail_changed(self, *_):
        r = self.table.currentRow()
        if self._loading or not 0 <= r < len(self._specs):
            return
        self._specs[r] = self._specs[r].model_copy(update=dict(
            fill=self.fill_cb.currentData(), angle_deg=self.angle_spin.value(), height_mm=self.height_spin.value(),
            speed_mm_s=self.speed_spin.value(), cure=self.cure_chk.isChecked()))
        self._emit()

    def _custom_toggled(self, _on):
        self._apply_enabled()
        self._emit()

    def _apply_enabled(self):
        on = self.custom()
        for b in self.buttons:
            b.setEnabled(on)
        for r in range(self.table.rowCount()):
            for c in range(len(COLUMNS)):
                w = self.table.cellWidget(r, c)
                if w is not None:
                    w.setEnabled(on)
        r = self.table.currentRow()
        spec = self._specs[r] if 0 <= r < len(self._specs) else None
        ink = spec is not None and spec.kind == "conductor"
        lines = spec is not None and spec.fill == "raster"
        self.detail.setEnabled(on and spec is not None)
        self.fill_cb.setEnabled(on and spec is not None and not ink)
        self.angle_spin.setEnabled(on and spec is not None and not ink and lines)

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()


__all__ = ["LayerStackTable"]
