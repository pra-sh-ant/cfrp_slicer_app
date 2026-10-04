"""Left-side controls panel: all slicing parameters as Qt widgets.

Emits `params_changed` whenever any value changes. Callers read
`get_params()` to get the current flat dict (same keys as the legacy
sidebar output) and `get_layup()` for the parsed layup list.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cfrp_core.config import REVOLVE_SHAPES, SHAPES, SPECIALTY_SHAPES

_FLAT_SPECIALTY = SPECIALTY_SHAPES - REVOLVE_SHAPES  # just Mushroom now

INFILL_OPTIONS = [
    "Archimedean (Concentric Spiral)",
    "Fermat Spiral (Connected Double Spiral)",
    "Oriented Serpentine",
    "45° Diagonal Hatch",
]
REVOLVE_INFILL_OPTIONS = [
    "Archimedean (Concentric Spiral)",
    "Fermat Spiral (Connected Double Spiral)",
]


def _spin(min_v, max_v, step, value, decimals=2):
    w = QDoubleSpinBox()
    w.setRange(min_v, max_v)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    w.setValue(value)
    w.setFixedWidth(100)
    return w


def _ispin(min_v, max_v, value):
    w = QSpinBox()
    w.setRange(min_v, max_v)
    w.setValue(value)
    w.setFixedWidth(100)
    return w


def _group(title, layout):
    box = QGroupBox(title)
    box.setCheckable(False)
    box.setLayout(layout)
    return box


class ControlsPanel(QWidget):
    params_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
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
        outer.addWidget(scroll)

        self._update_shape_visibility()

    # ------------------------------------------------------------------
    # Geometry section
    # ------------------------------------------------------------------
    def _build_geometry(self):
        fl = QFormLayout()
        fl.setLabelAlignment(Qt.AlignRight)

        self.shape_cb = QComboBox()
        self.shape_cb.addItems(list(SHAPES))
        self.shape_cb.setCurrentText("Square/Rectangle")
        fl.addRow("Shape", self.shape_cb)

        # Flat shape fields
        self.w_spin = _spin(1, 500, 1, 135.0, 1)
        self.h_spin = _spin(1, 500, 1, 25.0, 1)
        self._w_row = fl.addRow("Width (mm)", self.w_spin)
        self._h_row = fl.addRow("Height (mm)", self.h_spin)

        self.boundary_chk = QCheckBox("Print Outer Boundary")
        self._boundary_row = fl.addRow("", self.boundary_chk)

        self.invert_chk = QCheckBox("Invert Bends")
        self._invert_row = fl.addRow("", self.invert_chk)

        # Revolve / specialty fields
        self.base_r_spin = _spin(1, 200, 1, 20.0, 1)
        self._base_r_row = fl.addRow("Outer Radius (mm)", self.base_r_spin)

        self.stem_h_spin = _spin(0.1, 500, 1, 25.0, 1)
        self._stem_h_row = fl.addRow("Height (Z mm)", self.stem_h_spin)

        # Mushroom-only
        self.disc_r_spin = _spin(0.1, 200, 1, 15.0, 1)
        self._disc_r_row = fl.addRow("Top Disc Radius (mm)", self.disc_r_spin)

        self.stem_top_spin = _spin(0.1, 500, 1, 25.0, 1)
        self._stem_top_row = fl.addRow("Stem Top Height (absolute Z, mm)", self.stem_top_spin)

        self._vbox.addWidget(_group("Target Geometry", fl))

        # wire signals
        for w in (self.w_spin, self.h_spin, self.base_r_spin,
                  self.stem_h_spin, self.disc_r_spin, self.stem_top_spin):
            w.valueChanged.connect(self._emit)
        for w in (self.boundary_chk, self.invert_chk):
            w.toggled.connect(self._emit)
        self.shape_cb.currentTextChanged.connect(self._on_shape_changed)

    # ------------------------------------------------------------------
    # Layering section
    # ------------------------------------------------------------------
    def _build_layering(self):
        fl = QFormLayout()
        fl.setLabelAlignment(Qt.AlignRight)

        self.layers_spin = _ispin(1, 50, 1)
        self._layers_row = fl.addRow("Total Layers", self.layers_spin)

        self.tracks_spin = _ispin(1, 50, 7)
        fl.addRow("Tracks Per Layer", self.tracks_spin)

        self.infill_cb = QComboBox()
        self.infill_cb.addItems(INFILL_OPTIONS)
        self._infill_row = fl.addRow("Infill Pattern", self.infill_cb)

        self.layup_edit = QLineEdit("0")
        self.layup_edit.setPlaceholderText("e.g. 0, 90, 45, -45")
        self._layup_row = fl.addRow("Layup Angles (°)", self.layup_edit)

        self._vbox.addWidget(_group("Layering & Layup", fl))

        for w in (self.layers_spin, self.tracks_spin):
            w.valueChanged.connect(self._emit)
        self.infill_cb.currentTextChanged.connect(self._emit)
        self.layup_edit.textChanged.connect(self._emit)

    # ------------------------------------------------------------------
    # Placement section
    # ------------------------------------------------------------------
    def _build_placement(self):
        fl = QFormLayout()
        fl.setLabelAlignment(Qt.AlignRight)

        self.x_spin = _spin(0, 220, 1, 26.0, 1)
        self.y_spin = _spin(0, 220, 1, 40.0, 1)
        self.gap_spin = _spin(0.05, 20, 0.1, 1.2)
        self.z_off_spin = _spin(0, 50, 0.05, 0.2)

        fl.addRow("Start X (mm)", self.x_spin)
        fl.addRow("Start Y (mm)", self.y_spin)
        fl.addRow("Track Gap (mm)", self.gap_spin)
        fl.addRow("Z-Offset / SOD (mm)", self.z_off_spin)

        self._vbox.addWidget(_group("Placement & Offsets", fl))

        for w in (self.x_spin, self.y_spin, self.gap_spin, self.z_off_spin):
            w.valueChanged.connect(self._emit)

    # ------------------------------------------------------------------
    # Materials section
    # ------------------------------------------------------------------
    def _build_materials(self):
        fl = QFormLayout()
        fl.setLabelAlignment(Qt.AlignRight)

        self.needle_spin = _spin(0.01, 5, 0.1, 0.8)
        self.lh_spin = _spin(0.01, 5, 0.05, 0.2)
        self.emult_spin = _spin(0.01, 10, 0.1, 1.6)
        self.density_spin = _spin(1e-5, 1, 0.0001, 0.00115, 5)
        self.weight_spin = _spin(1e-5, 1, 0.00001, round(0.55 / (math.pi * 39), 6), 6)

        fl.addRow("Needle Dia (mm)", self.needle_spin)
        fl.addRow("Layer Height (mm)", self.lh_spin)
        fl.addRow("E-Multiplier", self.emult_spin)
        fl.addRow("Liquid Density (g/mm³)", self.density_spin)
        fl.addRow("Fiber Weight (g/mm)", self.weight_spin)

        self._vbox.addWidget(_group("Materials", fl))

        for w in (self.needle_spin, self.lh_spin, self.emult_spin,
                  self.density_spin, self.weight_spin):
            w.valueChanged.connect(self._emit)

    # ------------------------------------------------------------------
    # Kinematics section
    # ------------------------------------------------------------------
    def _build_kinematics(self):
        fl = QFormLayout()
        fl.setLabelAlignment(Qt.AlignRight)

        self.vmax_spin = _spin(0.1, 100, 0.5, 10.0, 1)
        self.vmin_spin = _spin(0, 100, 0.5, 5.0, 1)
        self.accel_spin = _spin(0.1, 500, 1, 25.0, 1)
        self.decel_spin = _spin(0.1, 500, 1, 15.0, 1)
        self.p_adv_spin = _spin(0, 50, 0.5, 3.0, 1)
        self.pump_floor_spin = _spin(0, 100, 0.5, 5.0, 1)
        self.trans_angle_spin = _spin(5, 85, 5, 30.0, 0)
        self.seam_trim_spin = _spin(0, 20, 0.5, 3.0, 1)
        self.anchor_spin = _spin(0, 50, 1, 15.0, 1)
        self.min_r_spin = _spin(0, 50, 0.5, 3.0, 1)

        # Specialty-only
        self.vz_spin = _spin(0.1, 50, 0.5, 2.0, 1)
        self.v_overhang_spin = _spin(0.1, 50, 0.5, 5.0, 1)
        self.dwell_spin = _spin(0, 30, 0.5, 1.5, 1)

        # G-code
        self.uv_spin = _ispin(0, 255, 225)
        self.purge_e_spin = _spin(0, 100, 1, 15.0, 1)
        self.purge_f_spin = _spin(1, 3000, 10, 60.0, 0)
        self.travel_f_spin = _spin(1, 3000, 10, 600.0, 0)

        fl.addRow("Print Speed Max (mm/s)", self.vmax_spin)
        fl.addRow("Min Bend Speed (mm/s)", self.vmin_spin)
        fl.addRow("Acceleration (mm/s²)", self.accel_spin)
        fl.addRow("Deceleration (mm/s²)", self.decel_spin)
        fl.addRow("Pressure Advance (mm)", self.p_adv_spin)
        fl.addRow("Pump-Stall Floor (mm/s)", self.pump_floor_spin)
        fl.addRow("Transition Angle (°)", self.trans_angle_spin)
        fl.addRow("Seam Trim (mm)", self.seam_trim_spin)
        fl.addRow("Anchor Length (mm)", self.anchor_spin)
        fl.addRow("Min Bend Radius (mm)", self.min_r_spin)

        self._vz_label = QLabel("Z Pull Speed (mm/s)")
        fl.addRow(self._vz_label, self.vz_spin)
        self._vo_label = QLabel("Overhang Speed (mm/s)")
        fl.addRow(self._vo_label, self.v_overhang_spin)
        self._dwell_label = QLabel("Top Dwell (s)")
        fl.addRow(self._dwell_label, self.dwell_spin)

        fl.addRow("UV Intensity (PWM)", self.uv_spin)
        fl.addRow("Purge Amount (E)", self.purge_e_spin)
        fl.addRow("Purge Speed (mm/min)", self.purge_f_spin)
        fl.addRow("Approach Speed (mm/min)", self.travel_f_spin)

        self._kin_form = fl
        self._vbox.addWidget(_group("Kinematics & G-code", fl))

        for w in (self.vmax_spin, self.vmin_spin, self.accel_spin, self.decel_spin,
                  self.p_adv_spin, self.pump_floor_spin, self.trans_angle_spin,
                  self.seam_trim_spin, self.anchor_spin, self.min_r_spin,
                  self.vz_spin, self.v_overhang_spin, self.dwell_spin,
                  self.purge_e_spin, self.purge_f_spin, self.travel_f_spin):
            w.valueChanged.connect(self._emit)
        self.uv_spin.valueChanged.connect(self._emit)

    # ------------------------------------------------------------------
    # Shape-dependent visibility
    # ------------------------------------------------------------------
    def _on_shape_changed(self, shape):
        self._update_shape_visibility()
        self._emit()

    def _update_shape_visibility(self):
        shape = self.shape_cb.currentText()
        is_revolve = shape in REVOLVE_SHAPES
        is_mushroom = shape == "3D Overhang (Mushroom)"
        is_flat_specialty = shape in _FLAT_SPECIALTY
        is_specialty = is_revolve or is_flat_specialty
        is_flat = not is_specialty
        is_serpentine = shape == "Serpentine Infill"
        is_circle_hex = shape in {"Hexagon", "Circle"}
        is_sphere = shape == "Hollow Sphere (Z)"

        # Flat shape geometry
        self.w_spin.setVisible(is_flat)
        self.h_spin.setVisible(is_flat and not is_circle_hex)
        self.boundary_chk.setVisible(is_flat and not is_serpentine)
        self.invert_chk.setVisible(is_serpentine)

        # Revolve/specialty geometry
        self.base_r_spin.setVisible(is_revolve or is_mushroom)
        self.stem_h_spin.setVisible(is_revolve and not is_sphere)
        # Sphere: show computed height label, not editable spin
        self.disc_r_spin.setVisible(is_mushroom)
        self.stem_top_spin.setVisible(is_mushroom)

        # Layering
        self.layers_spin.setVisible(is_flat)
        self.infill_cb.setVisible(not is_flat_specialty)
        if is_revolve:
            # Limit infill options to archimedean/fermat
            current = self.infill_cb.currentText()
            self.infill_cb.blockSignals(True)
            self.infill_cb.clear()
            self.infill_cb.addItems(REVOLVE_INFILL_OPTIONS)
            if current in REVOLVE_INFILL_OPTIONS:
                self.infill_cb.setCurrentText(current)
            self.infill_cb.blockSignals(False)
        elif is_flat:
            current = self.infill_cb.currentText()
            self.infill_cb.blockSignals(True)
            self.infill_cb.clear()
            self.infill_cb.addItems(INFILL_OPTIONS)
            if current in INFILL_OPTIONS:
                self.infill_cb.setCurrentText(current)
            self.infill_cb.blockSignals(False)
        self.tracks_spin.setVisible(not is_flat_specialty)
        self.layup_edit.setVisible(is_flat)

        # Kinematics: full planner for flat + revolve; specialty only Z speed
        show_planner = not is_flat_specialty
        show_specialty = is_flat_specialty
        show_mushroom = is_mushroom

        for w in (self.vmin_spin, self.accel_spin, self.decel_spin,
                  self.p_adv_spin, self.pump_floor_spin, self.trans_angle_spin,
                  self.seam_trim_spin, self.anchor_spin):
            w.setVisible(show_planner)
        self.vz_spin.setVisible(show_specialty)
        self._vz_label.setVisible(show_specialty)
        self.v_overhang_spin.setVisible(show_mushroom)
        self._vo_label.setVisible(show_mushroom)
        self.dwell_spin.setVisible(show_mushroom)
        self._dwell_label.setVisible(show_mushroom)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
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

        return {
            "shape": shape,
            "width": self.w_spin.value(),
            "height": self.h_spin.value(),
            "invert_bends": self.invert_chk.isChecked(),
            "include_boundary": self.boundary_chk.isChecked(),
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
        self._update_shape_visibility()

        _set(self.w_spin, "width")
        _set(self.h_spin, "height")
        _set(self.boundary_chk, "include_boundary")
        _set(self.invert_chk, "invert_bends")
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

        _set(self.x_spin, "start_x")
        _set(self.y_spin, "start_y")
        _set(self.gap_spin, "track_gap")
        _set(self.z_off_spin, "z_offset")
        _set(self.layers_spin, "layers", int)
        _set(self.tracks_spin, "tracks_per_layer", int)
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
        self.params_changed.emit()
