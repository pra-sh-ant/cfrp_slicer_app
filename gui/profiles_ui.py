"""A row for named profiles (cfrp_core.profiles): pick one to apply it,
save the current settings under a name, delete one."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QMessageBox, QPushButton, QWidget

from cfrp_core.profiles import LABELS, delete_profile, list_profiles, load_profile, save_profile

_PICK = "(choose a saved profile)"


class ProfileRow(QWidget):
    """`values()` -> dict of the current settings; `apply(dict)` sets them.
    `root`: profile folder (tests); default the user's."""

    def __init__(self, kind: str, values, apply, root=None, parent=None):
        super().__init__(parent)
        self.kind, self._values, self._apply, self._root = kind, values, apply, root
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.combo = QComboBox()
        self.combo.setToolTip(f"Saved {LABELS[kind].lower()} profiles: pick one to load its settings.")
        self.combo.activated.connect(self._picked)
        self.save_btn = QPushButton("Save as…")
        self.save_btn.setToolTip(f"Save the current {LABELS[kind].lower()} settings under a name")
        self.save_btn.clicked.connect(self.save_as)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setToolTip("Delete the chosen profile")
        self.del_btn.clicked.connect(self.delete_current)
        lay.addWidget(self.combo, 1)
        lay.addWidget(self.save_btn)
        lay.addWidget(self.del_btn)
        self.refresh()

    def refresh(self, select: str | None = None):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(_PICK)
        names = list_profiles(self.kind, self._root)
        self.combo.addItems(names)
        if select in names:
            self.combo.setCurrentText(select)
        self.combo.blockSignals(False)
        self.del_btn.setEnabled(self.combo.currentIndex() > 0)

    def _picked(self, index):
        self.del_btn.setEnabled(index > 0)
        if index > 0:
            self.load(self.combo.itemText(index))

    def load(self, name: str):
        try:
            self._apply(load_profile(self.kind, name, self._root))
        except ValueError as exc:
            QMessageBox.warning(self, "Profile", str(exc))

    def save(self, name: str):
        save_profile(self.kind, name, self._values(), self._root)
        self.refresh(select=name)

    def save_as(self):
        name, ok = QInputDialog.getText(self, f"Save {LABELS[self.kind].lower()} profile", "Name:")
        if ok and name.strip():
            try:
                self.save(name.strip())
            except ValueError as exc:
                QMessageBox.warning(self, "Profile", str(exc))

    def delete_current(self):
        if self.combo.currentIndex() > 0:
            delete_profile(self.kind, self.combo.currentText(), self._root)
            self.refresh()
