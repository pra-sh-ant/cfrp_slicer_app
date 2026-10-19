"""Application style sheet (dark theme). Disabled settings are visibly greyed."""

ACCENT = "#5b8def"

_TEMPLATE = """
QMainWindow, QWidget { background: #13131d; color: #d4d6e6; }
QToolTip { background: #232336; color: #e4e6f4; border: 1px solid #44446a; padding: 5px; }
QScrollArea { border: none; }
QSplitter::handle { background: #262638; width: 3px; }
QLabel { color: #c4c6dc; }
QLabel:disabled, QCheckBox:disabled { color: #55576e; }
QLabel#hint { color: #8e92b4; }
QLabel#caution { color: #f5b942; font-size: 11px; padding: 2px 0 4px 0; }
QLabel#viewInfo { color: #9ea6d0; font-family: Consolas; font-size: 11px; }

QToolButton#sectionHeader {
    background: #1c1c2c; border: none; border-top: 1px solid #2a2a40;
    color: #a9b0e0; font-weight: bold; text-align: left; padding: 6px 6px; }
QToolButton#sectionHeader:hover { background: #23233a; }
QWidget#sectionBody { background: #15151f; }

QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit {
    background: #1d1d2e; border: 1px solid #36364f; border-radius: 4px;
    color: #dcdef0; padding: 3px 5px; selection-background-color: #3d5bb5; }
QDoubleSpinBox:hover, QSpinBox:hover, QComboBox:hover, QLineEdit:hover { border-color: #4a4a6c; }
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus { border-color: %(accent)s; }
QDoubleSpinBox:disabled, QSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {
    background: #161622; color: #4f5168; border: 1px dashed #2c2c40; }
QComboBox QAbstractItemView { background: #1d1d2e; color: #dcdef0; selection-background-color: #2f3f73; }
QComboBox QAbstractItemView::item:disabled { color: #55576e; }
QLineEdit#settingsSearch { padding: 5px 8px; border-radius: 12px; margin: 4px; }

QCheckBox { spacing: 6px; }
QPushButton { background: #262640; border: 1px solid #40405e; border-radius: 4px;
              padding: 4px 12px; color: #cdd0ee; }
QPushButton:hover { background: #31315a; }
QPushButton:pressed { background: #3a3a6a; }
QPushButton:disabled { color: #55576e; border-color: #2c2c40; }
QPushButton#viewBtn { padding: 2px 8px; font-size: 11px; background: #1e2038; }
QPushButton#primary { background: %(accent)s; border-color: %(accent)s; color: white; font-weight: bold; }
QPushButton#primary:hover { background: #6f9cf5; }

QTabWidget::pane { border: 1px solid #2a2a40; border-radius: 4px; }
QTabBar::tab { background: #1c1c2c; border: 1px solid #2e2e46; padding: 5px 14px; color: #9a9ec0;
               border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #26263e; color: #e4e6ff; border-bottom: 2px solid %(accent)s; }

QGroupBox#step { border: 1px solid #2e3350; border-radius: 6px; margin-top: 14px; padding: 6px 6px 6px 6px;
                 font-weight: bold; color: #b9c2f0; }
QGroupBox#step::title { subcontrol-origin: margin; left: 8px; top: 2px; }
QListWidget#issues { background: #171724; border: 1px solid #2a2a40; border-radius: 4px; }
QListWidget#issues::item { padding: 3px 4px; }
QListWidget#issues::item:hover { background: #20203a; }

QPlainTextEdit { background: #0f0f18; color: #a8f0a8; font-family: Consolas; }
QSlider::groove:horizontal { background: #2a2a40; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: %(accent)s; width: 14px; height: 14px;
                             border-radius: 7px; margin: -5px 0; }
QStatusBar { background: #0f0f18; color: #7a7ea8; font-size: 11px; }
QToolBar { background: #171724; border: none; border-bottom: 1px solid #262638; spacing: 4px; padding: 3px; }
QToolBar QToolButton { color: #cdd0ee; padding: 4px 9px; border-radius: 4px; }
QToolBar QToolButton:hover { background: #2a2a46; }
QToolBar QToolButton:checked { background: #2c3a66; }
QMenuBar { background: #0f0f18; color: #c4c6dc; }
QMenuBar::item:selected { background: #26263e; }
QMenu { background: #1b1b2b; color: #c4c6dc; border: 1px solid #36364f; }
QMenu::item:selected { background: #2c3a66; }
QMenu::item:disabled { color: #55576e; }
QProgressBar { background: #1d1d2e; border: 1px solid #36364f; border-radius: 4px; max-height: 10px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 4px; }
"""
STYLE = _TEMPLATE.replace("%(accent)s", ACCENT)
