"""Left-side controls panel: all slicing parameters as Qt widgets.

Emits `params_changed` whenever any value changes. Callers read
`get_params()` to get the current flat dict (same keys as the legacy
sidebar output) and `get_layup()` for the parsed layup list.

Every setting is one keyed row in a collapsible section. Rows that do not
apply to the current setup are greyed out with the reason as tooltip, and
patterns that do not apply are greyed in the Infill drop-down; the rules
come from `cfrp_core.capabilities`, not from this file.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cfrp_core.capabilities import field_states, first_enabled, infill_states
from cfrp_core.config import REVOLVE_SHAPES, SHAPES, SPECIALTY_SHAPES

from .settings_help import HELP

_FLAT_SPECIALTY = SPECIALTY_SHAPES - REVOLVE_SHAPES  # just Mushroom now

INFILL_OPTIONS = [
    "Archimedean (Concentric Spiral)",
    "Fermat Spiral (Connected Double Spiral)",
    "Oriented Serpentine",
    "45° Diagonal Hatch",
    "Corner-Step Spiral",
]
REVOLVE_INFILL_OPTIONS = [
    "Archimedean (Concentric Spiral)",
    "Fermat Spiral (Connected Double Spiral)",
]


def _spin(min_v, max_v, step, value, decimals=2, suffix=""):
    w = QDoubleSpinBox()
    w.setRange(min_v, max_v)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    w.setValue(value)
    w.setMinimumWidth(86)
    w.setKeyboardTracking(False)
    if suffix:
        w.setSuffix(f" {suffix}")
    return w


def _ispin(min_v, max_v, value, suffix=""):
    w = QSpinBox()
    w.setRange(min_v, max_v)
    w.setValue(value)
    w.setMinimumWidth(86)
    w.setKeyboardTracking(False)
    if suffix:
        w.setSuffix(f" {suffix}")
    return w


def _caution_icon(size=14):
    """Small amber warning triangle for patterns that will raise warnings."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#f5b942"))
    p.setPen(Qt.NoPen)
    p.drawPolygon(QPolygonF([QPointF(size / 2, 1), QPointF(size - 1, size - 1), QPointF(1, size - 1)]))
    p.setPen(QColor("#1b1b28"))
    f = p.font()
    f.setBold(True)
    f.setPixelSize(int(size * 0.7))
    p.setFont(f)
    p.drawText(pm.rect().adjusted(0, 2, 0, 0), Qt.AlignCenter, "!")
    p.end()
    return QIcon(pm)


class _Section(QWidget):
    """Collapsible settings section: a header button and a form body."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self._header = QToolButton()
        self._header.setObjectName("sectionHeader")
        self._header.setCheckable(True)
        self._header.setChecked(True)
        self._header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.DownArrow)
        self._header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._header.toggled.connect(self._toggle)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.form = QFormLayout(self.body)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.setContentsMargins(8, 4, 8, 8)
        self.form.setHorizontalSpacing(10)
        self.form.setVerticalSpacing(5)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._header)
        lay.addWidget(self.body)
        self.set_unused(0)

    def _toggle(self, open_):
        self._header.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
        self.body.setVisible(open_)

    def set_open(self, open_):
        self._header.setChecked(open_)

    def is_open(self):
        return self._header.isChecked()

    def set_unused(self, n):
        self._header.setText(self.title.replace("&", "&&") + (f"   ·  {n} not used here" if n else ""))


class ControlsPanel(QWidget):
    params_changed = Signal()
    open_editor_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, tuple[QLabel, QWidget, _Section]] = {}
        self._sections: list[_Section] = []
        self._custom: dict = {}
        self._updating = False
        self._caution = _caution_icon()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search settings…")
        self._search.setClearButtonEnabled(True)
        self._search.setObjectName("settingsSearch")
        self._search.textChanged.connect(self._filter)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._vbox = QVBoxLayout(inner)
        self._vbox.setSpacing(6)
        self._vbox.setContentsMargins(4, 4, 4, 4)

        self._build_geometry()
        self._build_layering()
        self._build_placement()
        self._build_materials()
        self._build_kinematics()

        self._vbox.addStretch()
        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        outer.addWidget(self._search)
        outer.addWidget(scroll)

        self._update_shape_visibility()

    # ------------------------------------------------------------------
    # Row registry
    # ------------------------------------------------------------------
    def _section(self, title):
        sec = _Section(title)
        self._sections.append(sec)
        self._vbox.addWidget(sec)
        return sec

    def _add(self, sec, key, label, widget):
        lbl = QLabel(label)
        lbl.setObjectName("settingLabel")
        sec.form.addRow(lbl, widget)
        self._rows[key] = (lbl, widget, sec)
        help_text = HELP.get(key, "")
        lbl.setToolTip(help_text)
        widget.setToolTip(help_text)
        return widget

    # ------------------------------------------------------------------
    # Geometry section
    # ------------------------------------------------------------------
    def _build_geometry(self):
        sec = self._section("Part")

        self.shape_cb = QComboBox()
        self.shape_cb.addItems(list(SHAPES))
        self.shape_cb.setCurrentText("Square/Rectangle")
        self._add(sec, "shape", "Shape", self.shape_cb)

        # Flat shape fields
        self.w_spin = self._add(sec, "width", "Width", _spin(1, 500, 1, 135.0, 1, "mm"))
        self.h_spin = self._add(sec, "height", "Height", _spin(1, 500, 1, 25.0, 1, "mm"))
        self.boundary_chk = self._add(sec, "include_boundary", "", QCheckBox("Print Outer Boundary"))

        # Custom outline (Structural PCB editor)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.custom_lbl = QLabel("no outline yet")
        self.custom_lbl.setObjectName("hint")
        self.custom_lbl.setWordWrap(True)
        self.custom_lbl.setMinimumWidth(60)
        self.custom_btn = QPushButton("Edit…")
        self.custom_btn.setFixedWidth(64)
        self.custom_btn.clicked.connect(self.open_editor_requested.emit)
        row.addWidget(self.custom_lbl, 1)
        row.addWidget(self.custom_btn)
        box = QWidget()
        box.setLayout(row)
        self._add(sec, "custom_outline", "Outline", box)

        # STL Import fields
        stl_row = QHBoxLayout()
        self.stl_path_edit = QLineEdit()
        self.stl_path_edit.setPlaceholderText("Select an STL file…")
        self.stl_path_edit.setReadOnly(True)
        self.stl_path_edit.setMinimumWidth(60)
        self.stl_browse_btn = QPushButton("Browse…")
        self.stl_browse_btn.setFixedWidth(70)
        self.stl_browse_btn.clicked.connect(self._browse_stl)
        stl_row.addWidget(self.stl_path_edit)
        stl_row.addWidget(self.stl_browse_btn)
        stl_container = QWidget()
        stl_container.setLayout(stl_row)
        stl_row.setContentsMargins(0, 0, 0, 0)
        self._add(sec, "stl_path", "STL File", stl_container)
        self.stl_scale_spin = self._add(sec, "stl_scale", "STL Scale", _spin(0.001, 1000, 1, 1.0, 3, "x"))
        self.stl_islands_chk = self._add(sec, "stl_islands", "", QCheckBox("Print All Islands (fibre-bridged)"))
        self.stl_close_spin = self._add(sec, "stl_closing_radius", "Close Cracks", _spin(0, 2, 0.01, 0.049, 3, "mm"))
        self.stl_stitch_spin = self._add(sec, "stl_stitch", "Stitch Holes", _spin(0, 50, 0.5, 5.0, 1, "mm"))

        # Revolve / specialty fields
        self.base_r_spin = self._add(sec, "base_radius", "Outer Radius", _spin(1, 200, 1, 20.0, 1, "mm"))
        self.stem_h_spin = self._add(sec, "stem_height", "Height (Z)", _spin(0.1, 500, 1, 25.0, 1, "mm"))
        self.disc_r_spin = self._add(sec, "disc_radius", "Top Disc Radius", _spin(0.1, 200, 1, 15.0, 1, "mm"))
        self.stem_top_spin = self._add(sec, "stem_top", "Stem Top (abs. Z)", _spin(0.1, 500, 1, 25.0, 1, "mm"))

        for w in (self.w_spin, self.h_spin, self.base_r_spin, self.stem_h_spin,
                  self.disc_r_spin, self.stem_top_spin, self.stl_scale_spin, self.stl_close_spin,
                  self.stl_stitch_spin):
            w.valueChanged.connect(self._emit)
        self.boundary_chk.toggled.connect(self._emit)
        self.stl_islands_chk.toggled.connect(self._emit)
        self.shape_cb.currentTextChanged.connect(self._on_shape_changed)

    # ------------------------------------------------------------------
    # Layering section
    # ------------------------------------------------------------------
    def _build_layering(self):
        sec = self._section("Pattern & Layers")

        self.infill_cb = QComboBox()
        self.infill_cb.addItems(INFILL_OPTIONS)
        self._add(sec, "infill_type", "Infill Pattern", self.infill_cb)
        self.infill_hint = QLabel("")
        self.infill_hint.setObjectName("caution")
        self.infill_hint.setWordWrap(True)
        self.infill_hint.setMinimumWidth(60)
        self.infill_hint.setVisible(False)
        sec.form.addRow("", self.infill_hint)

        self.tracks_spin = self._add(sec, "tracks_per_layer", "Tracks Per Layer", _ispin(1, 200, 7))
        self.fill_chk = self._add(sec, "fill_solid", "", QCheckBox("Fill Solid (as many tracks as fit)"))
        self.layers_spin = self._add(sec, "layers", "Total Layers", _ispin(1, 500, 1))
        self.first_solid_chk = self._add(sec, "first_layer_solid", "",
                                         QCheckBox("First Layer Solid (fully filled base)"))
        self.first_solid_chk.setChecked(True)
        self.first_infill_cb = QComboBox()
        self.first_infill_cb.addItems(["Same as other layers"] + INFILL_OPTIONS)
        self._add(sec, "first_layer_infill", "First Layer / Skin Pattern", self.first_infill_cb)
        self.first_cov_spin = self._add(sec, "first_layer_min_coverage", "First Layer Target",
                                        _spin(0, 100, 5, 0, 0, "%"))
        self.first_cov_spin.setSpecialValueText("no check")
        self.skin_bottom_spin = self._add(sec, "solid_bottom_layers", "Solid Bottom Layers", _ispin(0, 100, 0))
        self.skin_top_spin = self._add(sec, "solid_top_layers", "Solid Top Layers", _ispin(0, 100, 0))
        for w in (self.skin_bottom_spin, self.skin_top_spin):
            w.setSpecialValueText("off")
        self.layup_edit = QLineEdit("0")
        self.layup_edit.setPlaceholderText("e.g. 0, 90, 45, -45")
        self._add(sec, "layup_seq", "Layup Angles (°)", self.layup_edit)

        for w in (self.layers_spin, self.tracks_spin):
            w.valueChanged.connect(self._emit)
        self.fill_chk.toggled.connect(self._emit)
        self.first_solid_chk.toggled.connect(self._emit)
        self.first_infill_cb.currentTextChanged.connect(self._emit)
        self.first_cov_spin.valueChanged.connect(self._emit)
        self.skin_bottom_spin.valueChanged.connect(self._emit)
        self.skin_top_spin.valueChanged.connect(self._emit)
        self.infill_cb.currentTextChanged.connect(self._emit)
        self.layup_edit.textChanged.connect(self._emit)

    # ------------------------------------------------------------------
    # Placement section
    # ------------------------------------------------------------------
    def _build_placement(self):
        sec = self._section("Placement")
        self.x_spin = self._add(sec, "start_x", "Start X", _spin(0, 500, 1, 26.0, 1, "mm"))
        self.y_spin = self._add(sec, "start_y", "Start Y", _spin(0, 500, 1, 40.0, 1, "mm"))
        self.gap_spin = self._add(sec, "track_gap", "Track Gap", _spin(0.05, 20, 0.1, 1.2, 2, "mm"))
        self.z_off_spin = self._add(sec, "z_offset", "Z-Offset / SOD", _spin(0, 50, 0.05, 0.2, 2, "mm"))
        for w in (self.x_spin, self.y_spin, self.gap_spin, self.z_off_spin):
            w.valueChanged.connect(self._emit)

    # ------------------------------------------------------------------
    # Materials section
    # ------------------------------------------------------------------
    def _build_materials(self):
        sec = self._section("Material")
        self.needle_spin = self._add(sec, "needle_dia", "Needle Dia", _spin(0.01, 5, 0.1, 0.8, 2, "mm"))
        self.lh_spin = self._add(sec, "layer_height", "Layer Height", _spin(0.01, 5, 0.05, 0.2, 2, "mm"))
        self.emult_spin = self._add(sec, "e_mult", "E-Multiplier", _spin(0.01, 10, 0.1, 1.6))
        self.density_spin = self._add(sec, "density", "Liquid Density",
                                      _spin(1e-5, 1, 0.0001, 0.00115, 5, "g/mm³"))
        self.weight_spin = self._add(sec, "weight", "Fiber Weight",
                                     _spin(1e-5, 1, 0.00001, round(0.55 / (math.pi * 39), 6), 6, "g/mm"))
        for w in (self.needle_spin, self.lh_spin, self.emult_spin, self.density_spin, self.weight_spin):
            w.valueChanged.connect(self._emit)

    # ------------------------------------------------------------------
    # Kinematics sections
    # ------------------------------------------------------------------
    def _build_kinematics(self):
        sec = self._section("Speed & Flow")
        self.vmax_spin = self._add(sec, "v_max", "Print Speed Max", _spin(0.1, 100, 0.5, 10.0, 1, "mm/s"))
        self.vmin_spin = self._add(sec, "v_min", "Min Bend Speed", _spin(0, 100, 0.5, 5.0, 1, "mm/s"))
        self.accel_spin = self._add(sec, "accel", "Acceleration", _spin(0.1, 500, 1, 25.0, 1, "mm/s²"))
        self.decel_spin = self._add(sec, "decel", "Deceleration", _spin(0.1, 500, 1, 15.0, 1, "mm/s²"))
        self.p_adv_spin = self._add(sec, "p_advance", "Pressure Advance", _spin(0, 50, 0.5, 3.0, 1, "mm"))
        self.pa_table_edit = QLineEdit()
        self.pa_table_edit.setPlaceholderText("empty = fixed advance; e.g. 5:1.0, 10:3.0")
        self._add(sec, "pa_table", "PA Table", self.pa_table_edit)
        self.pump_floor_spin = self._add(sec, "pump_stall_floor", "Pump-Stall Floor",
                                         _spin(0, 100, 0.5, 5.0, 1, "mm/s"))
        self.min_r_spin = self._add(sec, "min_radius", "Min Bend Radius", _spin(0, 50, 0.5, 3.0, 1, "mm"))

        sec = self._section("Seams & Transitions")
        self.anchor_spin = self._add(sec, "anchor_length", "Anchor Length", _spin(0, 50, 1, 15.0, 1, "mm"))
        self.trans_angle_spin = self._add(sec, "transition_angle", "Transition Angle", _spin(5, 85, 5, 30.0, 0, "°"))
        self.seam_trim_spin = self._add(sec, "seam_trim_length", "Seam Trim", _spin(0, 20, 0.5, 3.0, 1, "mm"))
        self.seam_stagger_spin = self._add(sec, "seam_stagger", "Seam Stagger", _spin(0, 360, 5, 0.0, 1, "°"))

        sec = self._section("Mushroom Test")
        self.vz_spin = self._add(sec, "v_z", "Z Pull Speed", _spin(0.1, 50, 0.5, 2.0, 1, "mm/s"))
        self.v_overhang_spin = self._add(sec, "v_overhang", "Overhang Speed", _spin(0.1, 50, 0.5, 5.0, 1, "mm/s"))
        self.dwell_spin = self._add(sec, "dwell", "Top Dwell", _spin(0, 30, 0.5, 1.5, 1, "s"))

        sec = self._section("G-code Start")
        self.uv_spin = self._add(sec, "uv", "UV Intensity (PWM)", _ispin(0, 255, 225))
        self.purge_e_spin = self._add(sec, "purge_e", "Purge Amount (E)", _spin(0, 100, 1, 15.0, 1))
        self.purge_f_spin = self._add(sec, "purge_f", "Purge Speed", _spin(1, 3000, 10, 60.0, 0, "mm/min"))
        self.travel_f_spin = self._add(sec, "travel_f", "Approach Speed", _spin(1, 3000, 10, 600.0, 0, "mm/min"))
        sec.set_open(False)

        sec = self._section("Machine")
        self.bed_x_spin = self._add(sec, "bed_x", "Bed Width (X)", _spin(10, 2000, 10, 180.0, 0, "mm"))
        self.bed_y_spin = self._add(sec, "bed_y", "Bed Depth (Y)", _spin(10, 2000, 10, 220.0, 0, "mm"))
        self.z_max_spin = self._add(sec, "z_max", "Max Height (Z)", _spin(1, 2000, 10, 200.0, 0, "mm"))
        self.margin_spin = self._add(sec, "bed_margin", "Edge Margin", _spin(0, 50, 0.5, 1.0, 1, "mm"))
        sec.set_open(False)
        for w in (self.bed_x_spin, self.bed_y_spin, self.z_max_spin, self.margin_spin):
            w.valueChanged.connect(self._emit)

        for w in (self.vmax_spin, self.vmin_spin, self.accel_spin, self.decel_spin,
                  self.p_adv_spin, self.pump_floor_spin, self.trans_angle_spin,
                  self.seam_trim_spin, self.anchor_spin, self.min_r_spin,
                  self.seam_stagger_spin, self.vz_spin, self.v_overhang_spin, self.dwell_spin,
                  self.purge_e_spin, self.purge_f_spin, self.travel_f_spin):
            w.valueChanged.connect(self._emit)
        self.uv_spin.valueChanged.connect(self._emit)
        self.pa_table_edit.editingFinished.connect(self._emit)

    # ------------------------------------------------------------------
    # Option states (grey-out) and search
    # ------------------------------------------------------------------
    def _on_shape_changed(self, shape):
        self._emit()

    def _browse_stl(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select STL File", "", "Meshes (*.stl *.STL *.obj *.OBJ);;All Files (*)"
        )
        if path:
            self.set_stl_path(path)

    def set_stl_path(self, path: str):
        self.stl_path_edit.setText(path)
        self.stl_path_edit.setToolTip(path)
        if self.shape_cb.currentText() != "STL Import":
            self.shape_cb.setCurrentText("STL Import")   # emits via _on_shape_changed
        else:
            self._emit()

    def _update_shape_visibility(self):
        """Grey out every row and pattern that does not apply (kept for the
        old name; nothing is hidden any more)."""
        self._apply_states()

    def _apply_states(self):
        params = self._raw_params()
        f_states = field_states(params)
        unused = {sec.title: 0 for sec in self._sections}
        for key, (lbl, widget, sec) in self._rows.items():
            st = f_states.get(key)
            enabled = st is None or st.enabled
            lbl.setEnabled(enabled)
            widget.setEnabled(enabled)
            help_text = HELP.get(key, "")
            tip = help_text if enabled else f"<b>Not used here.</b> {st.reason}<br><br>{help_text}"
            lbl.setToolTip(tip)
            widget.setToolTip(tip)
            if not enabled:
                unused[sec.title] += 1
        for sec in self._sections:
            sec.set_unused(unused[sec.title])

        # Infill patterns: grey out what the slicer would not use.
        states = infill_states(params)
        model = self.infill_cb.model()
        current = self.infill_cb.currentText()
        for i in range(self.infill_cb.count()):
            name = self.infill_cb.itemText(i)
            st = states.get(name)
            item = model.item(i)
            if st is None:
                continue
            item.setEnabled(st.enabled)
            self.infill_cb.setItemData(i, st.reason or None, Qt.ToolTipRole)
            self.infill_cb.setItemIcon(i, self._caution if st.caution else QIcon())
        choice = first_enabled(states, current)
        if choice != current:
            self.infill_cb.blockSignals(True)
            self.infill_cb.setCurrentText(choice)
            self.infill_cb.blockSignals(False)
        st = states.get(self.infill_cb.currentText())
        hint = st.reason if st is not None and st.caution else ""
        self.infill_hint.setText(f"⚠ {hint}" if hint else "")
        self.infill_hint.setVisible(bool(hint))

    def _filter(self, text):
        text = text.strip().lower()
        for key, (lbl, widget, sec) in self._rows.items():
            label = lbl.text() or (widget.text() if isinstance(widget, QCheckBox) else "")
            hit = not text or text in label.lower() or text in HELP.get(key, "").lower() or text in key
            sec.form.setRowVisible(lbl, hit)
        for sec in self._sections:
            any_hit = any(sec.form.isRowVisible(lbl) for lbl, _, s in self._rows.values() if s is sec)
            sec.setVisible(any_hit)
            if text and any_hit:
                sec.set_open(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_custom_outline(self, outline, holes=(), name: str = ""):
        """Outline (and holes) for the Custom Outline shape, in mm."""
        self._custom = {"custom_outline": [tuple(map(float, p)) for p in outline],
                        "custom_holes": [[tuple(map(float, p)) for p in h] for h in holes]}
        n = len(self._custom["custom_holes"])
        xs = [p[0] for p in self._custom["custom_outline"]] or [0.0]
        ys = [p[1] for p in self._custom["custom_outline"]] or [0.0]
        self.custom_lbl.setText(f"{name or 'outline'}: {max(xs) - min(xs):.0f} x {max(ys) - min(ys):.0f} mm"
                                + (f", {n} hole(s)" if n else ""))
        if "Custom Outline" in SHAPES and self.shape_cb.currentText() != "Custom Outline":
            self.shape_cb.setCurrentText("Custom Outline")
        else:
            self._emit()

    def _raw_params(self) -> dict:
        """Current widget values (no shape-specific zeroing): input to the
        option-state rules."""
        return {
            "shape": self.shape_cb.currentText(),
            "infill_type": self.infill_cb.currentText(),
            "layers": self.layers_spin.value(),
            "fill_solid": self.fill_chk.isChecked(),
            "first_layer_solid": self.first_solid_chk.isChecked(),
            "first_layer_infill": self.first_infill_cb.currentText(),
            "pa_table": self.pa_table_edit.text(),
            "track_gap": self.gap_spin.value(),
            "min_radius": self.min_r_spin.value(),
            "include_boundary": self.boundary_chk.isChecked(),
        }

    def get_params(self) -> dict:
        shape = self.shape_cb.currentText()
        is_mushroom = shape == "3D Overhang (Mushroom)"
        is_flat_specialty = shape in _FLAT_SPECIALTY
        is_sphere = shape == "Hollow Sphere (Z)"

        stem_h = (2.0 * self.base_r_spin.value() if is_sphere
                  else (self.stem_top_spin.value() if is_mushroom
                        else self.stem_h_spin.value()))

        # kinematics defaults for specialty shapes
        if is_flat_specialty:
            vmin = accel = decel = p_adv = pump_floor = 0.0
            trans_angle = seam_trim = anchor = 0.0
            vz = self.vz_spin.value()
            v_overhang = self.v_overhang_spin.value() if is_mushroom else 0.0
            dwell = self.dwell_spin.value() if is_mushroom else 0.0
        else:
            vmin = self.vmin_spin.value()
            accel = self.accel_spin.value()
            decel = self.decel_spin.value()
            p_adv = self.p_adv_spin.value()
            pump_floor = self.pump_floor_spin.value()
            trans_angle = self.trans_angle_spin.value()
            seam_trim = self.seam_trim_spin.value()
            anchor = self.anchor_spin.value()
            vz = v_overhang = dwell = 0.0

        infill = (self.infill_cb.currentText()
                  if not is_flat_specialty else "N/A")

        stl_path = self.stl_path_edit.text() or None

        params = {
            "shape": shape,
            "width": self.w_spin.value(),
            "height": self.h_spin.value(),
            "include_boundary": self.boundary_chk.isChecked(),
            "stl_path": stl_path,
            "stl_islands": "all" if self.stl_islands_chk.isChecked() else "largest",
            "stl_scale": self.stl_scale_spin.value(),
            "base_radius": self.base_r_spin.value(),
            "disc_radius": self.disc_r_spin.value(),
            "stem_height": stem_h,
            "wall_thickness": 0.0,
            "start_x": self.x_spin.value(),
            "start_y": self.y_spin.value(),
            "track_gap": self.gap_spin.value(),
            "z_offset": self.z_off_spin.value(),
            "layers": self.layers_spin.value(),
            "layup_seq": self.layup_edit.text(),
            "infill_type": infill,
            "tracks_per_layer": self.tracks_spin.value(),
            "fill_solid": self.fill_chk.isChecked(),
            "first_layer_solid": self.first_solid_chk.isChecked() and not is_flat_specialty
            and shape not in REVOLVE_SHAPES,
            "first_layer_infill": (None if self.first_infill_cb.currentIndex() <= 0
                                   or is_flat_specialty or shape in REVOLVE_SHAPES
                                   else self.first_infill_cb.currentText()),
            "first_layer_min_coverage": self.first_cov_spin.value() / 100.0,
            "solid_bottom_layers": (0 if is_flat_specialty or shape in REVOLVE_SHAPES
                                    else self.skin_bottom_spin.value()),
            "solid_top_layers": (0 if is_flat_specialty or shape in REVOLVE_SHAPES
                                 else self.skin_top_spin.value()),
            "stl_closing_radius": self.stl_close_spin.value(),
            "stl_stitch": self.stl_stitch_spin.value(),
            "layer_height": self.lh_spin.value(),
            "needle_dia": self.needle_spin.value(),
            "e_mult": self.emult_spin.value(),
            "density": self.density_spin.value(),
            "weight": self.weight_spin.value(),
            "v_max": self.vmax_spin.value(),
            "v_min": vmin,
            "accel": accel,
            "decel": decel,
            "p_advance": p_adv,
            "pump_stall_floor": pump_floor,
            "transition_angle": trans_angle,
            "seam_trim_length": seam_trim,
            "seam_stagger": self.seam_stagger_spin.value(),
            "pa_table": self.pa_table_edit.text(),
            "anchor_length": anchor,
            "min_radius": self.min_r_spin.value(),
            "v_z": vz,
            "v_overhang": v_overhang,
            "dwell": dwell,
            "uv": self.uv_spin.value(),
            "purge_e": self.purge_e_spin.value(),
            "purge_f": self.purge_f_spin.value(),
            "travel_f": self.travel_f_spin.value(),
        }
        if shape == "Custom Outline":
            params.update(self._custom)
        return params

    def get_machine(self):
        """Machine profile from the Machine section."""
        from cfrp_core.machine import MachineProfile
        return MachineProfile(name="custom", bed_x_mm=self.bed_x_spin.value(), bed_y_mm=self.bed_y_spin.value(),
                              z_max_mm=self.z_max_spin.value(), margin_mm=self.margin_spin.value())

    def get_layup(self) -> list[float]:
        try:
            seq = [float(x.strip()) for x in self.layup_edit.text().split(',') if x.strip()]
            return seq if seq else [0.0]
        except ValueError:
            return [0.0]

    def load_params(self, p: dict) -> None:
        """Populate all widgets from a params dict (same format as get_params)."""
        def _set(widget, key, cast=float):
            if key in p:
                widget.blockSignals(True)
                try:
                    if isinstance(widget, QCheckBox):
                        widget.setChecked(bool(p[key]))
                    elif isinstance(widget, (QSpinBox,)):
                        widget.setValue(int(p[key]))
                    elif isinstance(widget, QComboBox):
                        widget.setCurrentText(str(p[key]))
                    elif isinstance(widget, QLineEdit):
                        widget.setText(str(p[key]))
                    else:
                        widget.setValue(cast(p[key]))
                finally:
                    widget.blockSignals(False)

        _set(self.shape_cb, "shape")

        _set(self.w_spin, "width")
        _set(self.h_spin, "height")
        _set(self.boundary_chk, "include_boundary")
        _set(self.base_r_spin, "base_radius")
        _set(self.disc_r_spin, "disc_radius")
        # stem_height maps to stem_h_spin or stem_top_spin depending on shape
        if "stem_height" in p:
            shape = self.shape_cb.currentText()
            if shape == "3D Overhang (Mushroom)":
                self.stem_top_spin.blockSignals(True)
                self.stem_top_spin.setValue(float(p["stem_height"]))
                self.stem_top_spin.blockSignals(False)
            else:
                self.stem_h_spin.blockSignals(True)
                self.stem_h_spin.setValue(float(p["stem_height"]))
                self.stem_h_spin.blockSignals(False)

        if "stl_path" in p and p["stl_path"]:
            self.stl_path_edit.setText(str(p["stl_path"]))
        _set(self.stl_scale_spin, "stl_scale")
        if "stl_islands" in p:
            self.stl_islands_chk.blockSignals(True)
            self.stl_islands_chk.setChecked(p["stl_islands"] == "all")
            self.stl_islands_chk.blockSignals(False)
        if p.get("custom_outline"):
            self._custom = {"custom_outline": [tuple(q) for q in p["custom_outline"]],
                            "custom_holes": [[tuple(q) for q in h] for h in p.get("custom_holes", [])]}
            self.custom_lbl.setText(f"outline: {len(self._custom['custom_outline'])} points, "
                                    f"{len(self._custom['custom_holes'])} hole(s)")

        _set(self.x_spin, "start_x")
        _set(self.y_spin, "start_y")
        _set(self.gap_spin, "track_gap")
        _set(self.z_off_spin, "z_offset")
        _set(self.layers_spin, "layers", int)
        _set(self.tracks_spin, "tracks_per_layer", int)
        _set(self.fill_chk, "fill_solid")
        _set(self.first_solid_chk, "first_layer_solid")
        if "first_layer_infill" in p:
            self.first_infill_cb.blockSignals(True)
            self.first_infill_cb.setCurrentText(p["first_layer_infill"] or "Same as other layers")
            self.first_infill_cb.blockSignals(False)
        if "first_layer_min_coverage" in p:
            self.first_cov_spin.blockSignals(True)
            self.first_cov_spin.setValue(float(p["first_layer_min_coverage"]) * 100.0)
            self.first_cov_spin.blockSignals(False)
        _set(self.skin_bottom_spin, "solid_bottom_layers", int)
        _set(self.skin_top_spin, "solid_top_layers", int)
        _set(self.stl_close_spin, "stl_closing_radius")
        _set(self.stl_stitch_spin, "stl_stitch")
        for key, w in (("bed_x", self.bed_x_spin), ("bed_y", self.bed_y_spin), ("z_max", self.z_max_spin),
                       ("bed_margin", self.margin_spin)):
            _set(w, key)
        _set(self.infill_cb, "infill_type")
        _set(self.layup_edit, "layup_seq")

        _set(self.needle_spin, "needle_dia")
        _set(self.lh_spin, "layer_height")
        _set(self.emult_spin, "e_mult")
        _set(self.density_spin, "density")
        _set(self.weight_spin, "weight")

        _set(self.vmax_spin, "v_max")
        _set(self.vmin_spin, "v_min")
        _set(self.accel_spin, "accel")
        _set(self.decel_spin, "decel")
        _set(self.p_adv_spin, "p_advance")
        _set(self.pump_floor_spin, "pump_stall_floor")
        _set(self.trans_angle_spin, "transition_angle")
        _set(self.seam_trim_spin, "seam_trim_length")
        _set(self.seam_stagger_spin, "seam_stagger")
        _set(self.pa_table_edit, "pa_table")
        _set(self.anchor_spin, "anchor_length")
        _set(self.min_r_spin, "min_radius")
        _set(self.vz_spin, "v_z")
        _set(self.v_overhang_spin, "v_overhang")
        _set(self.dwell_spin, "dwell")
        _set(self.uv_spin, "uv", int)
        _set(self.purge_e_spin, "purge_e")
        _set(self.purge_f_spin, "purge_f")
        _set(self.travel_f_spin, "travel_f")

        self._emit()

    def _emit(self):
        self._apply_states()
        self.params_changed.emit()
