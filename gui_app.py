"""CFRP Slicer Pro -- PySide6 desktop application.

Run with run.bat, or:
    .venv\\Scripts\\python.exe gui_app.py
"""
import sys
from pathlib import Path

# cfrp_core lives in src/; no install needed to run from the source folder.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CFRP Slicer Pro")
    app.setOrganizationName("CFRP Lab")

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
