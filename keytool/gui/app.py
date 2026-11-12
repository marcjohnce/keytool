from __future__ import annotations

import sys


def run_gui(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from ..library import Library
    from .main_window import MainWindow

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("KeyTool")
    app.setOrganizationName("KeyTool")
    try:
        from importlib.resources import files
        icon = files("keytool").joinpath("resources/icon.png")
        if icon.is_file():
            app.setWindowIcon(QIcon(str(icon)))
    except Exception:
        pass
    lib = Library()
    win = MainWindow(lib)
    win.show()
    # Paths given on the command line (e.g. "Open with KeyTool") are imported right away
    paths = [a for a in (argv or sys.argv)[1:] if not a.startswith("-")]
    if paths:
        win.start_import(paths)
    return app.exec()
