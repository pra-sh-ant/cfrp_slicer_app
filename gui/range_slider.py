"""Vertical dual-handle range slider (layer range, PrusaSlicer-style)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class RangeSlider(QWidget):
    """Integer range [low, high] within [minimum, maximum]; bottom = minimum.
    Drag a handle, drag the band between them to move both, wheel moves the
    upper handle, Up/Down/PageUp/PageDown too. `labels` maps a value to the
    text shown beside a handle (e.g. layer and Z)."""

    rangeChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min, self._max = 1, 1
        self._lo, self._hi = 1, 1
        self._drag = None
        self._drag_off = 0
        self.labels = None
        self.setMinimumWidth(78)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("Layer range: drag the handles (or the band) · wheel / ↑↓ move the top layer")

    # --- model ---------------------------------------------------------------
    def setRange(self, minimum, maximum):
        self._min, self._max = int(minimum), max(int(minimum), int(maximum))
        self.setValues(self._min, self._max)

    def values(self):
        return self._lo, self._hi

    def setValues(self, lo, hi, emit=True):
        lo = max(self._min, min(int(lo), self._max))
        hi = max(self._min, min(int(hi), self._max))
        if lo > hi:
            lo, hi = hi, lo
        changed = (lo, hi) != (self._lo, self._hi)
        self._lo, self._hi = lo, hi
        self.update()
        if changed and emit:
            self.rangeChanged.emit(lo, hi)

    # --- geometry ---------------------------------------------------------------
    def _track(self):
        return QRectF(self.width() - 18, 12, 6, self.height() - 24)

    def _y(self, v):
        t = self._track()
        span = max(1, self._max - self._min)
        return t.bottom() - (v - self._min) / span * t.height()

    def _value_at(self, y):
        t = self._track()
        span = max(1, self._max - self._min)
        return round(self._min + (t.bottom() - y) / max(t.height(), 1) * span)

    # --- painting ------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._track()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2a40"))
        p.drawRoundedRect(t, 3, 3)
        y_lo, y_hi = self._y(self._lo), self._y(self._hi)
        p.setBrush(QColor("#5b8def"))
        p.drawRoundedRect(QRectF(t.left(), y_hi, t.width(), y_lo - y_hi), 3, 3)
        fm = p.fontMetrics()
        for v, y in ((self._hi, y_hi), (self._lo, y_lo)):
            p.setBrush(QColor("#e8ecff"))
            p.setPen(QPen(QColor("#5b8def"), 2))
            p.drawEllipse(QRectF(t.center().x() - 7, y - 7, 14, 14))
            text = self.labels(v) if self.labels else str(v)
            p.setPen(QColor("#c8d0f0"))
            w = fm.horizontalAdvance(text)
            p.drawText(int(t.left() - 8 - w), int(y + fm.ascent() / 2 - 1), text)
        p.end()

    # --- interaction -----------------------------------------------------------
    def mousePressEvent(self, ev):
        y = ev.position().y()
        d_lo, d_hi = abs(y - self._y(self._lo)), abs(y - self._y(self._hi))
        if min(d_lo, d_hi) <= 10:
            self._drag = "hi" if d_hi <= d_lo else "lo"
        elif self._y(self._hi) < y < self._y(self._lo):
            self._drag = "band"
            self._drag_off = self._value_at(y) - self._lo
        else:
            self._drag = "hi" if y < self._y(self._hi) else "lo"
            self.mouseMoveEvent(ev)

    def mouseMoveEvent(self, ev):
        if not self._drag:
            return
        v = self._value_at(ev.position().y())
        if self._drag == "hi":
            self.setValues(self._lo, max(v, self._lo))
        elif self._drag == "lo":
            self.setValues(min(v, self._hi), self._hi)
        else:
            width = self._hi - self._lo
            lo = max(self._min, min(v - self._drag_off, self._max - width))
            self.setValues(lo, lo + width)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def mouseDoubleClickEvent(self, _):
        self.setValues(self._min, self._max)

    def wheelEvent(self, ev):
        step = 1 if ev.angleDelta().y() > 0 else -1
        self.setValues(self._lo, max(self._lo, self._hi + step))

    def keyPressEvent(self, ev):
        k = ev.key()
        step = {Qt.Key_Up: 1, Qt.Key_Down: -1, Qt.Key_PageUp: 10, Qt.Key_PageDown: -10}.get(k)
        if step is None:
            return super().keyPressEvent(ev)
        if ev.modifiers() & Qt.ShiftModifier:
            self.setValues(min(self._hi, self._lo + step), self._hi)
        else:
            self.setValues(self._lo, max(self._lo, self._hi + step))
