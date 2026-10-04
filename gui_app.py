"""CFRP Slicer Pro -- PySide6 desktop application.

Run with:
    .venv312\\Scripts\\python.exe gui_app.py
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    # High-DPI scaling
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("CFRP Slicer Pro")
    app.setOrganizationName("CFRP Lab")

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
