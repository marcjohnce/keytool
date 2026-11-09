"""Light and dark themes (Fusion palette + a small stylesheet) and Camelot key colors."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..keys import Key

THEMES = {
    "dark": dict(
        window="#16181d", base="#1d2027", alt="#22262e", text="#e6e8ee", dim="#9aa1ae", button="#262a33",
        border="#323743", accent="#5b8cff", accent_text="#ffffff", hover="#2b303a", danger="#ff6b6b",
        tooltip="#2b303a",
    ),
    "light": dict(
        window="#f4f5f8", base="#ffffff", alt="#f7f8fa", text="#1c1f26", dim="#626a78", button="#ffffff",
        border="#d7dbe3", accent="#3366ff", accent_text="#ffffff", hover="#eceff5", danger="#d63c3c",
        tooltip="#ffffff",
    ),
}


def resolve_theme(name: str) -> str:
    if name in THEMES:
        return name
    # "system": follow the OS colour scheme where Qt can tell us
    try:
        from PySide6.QtCore import Qt

        scheme = QApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    except Exception:
        return "dark"


def apply_theme(app: QApplication, name: str) -> dict:
    c = THEMES[resolve_theme(name)]
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(c["window"]))
    p.setColor(QPalette.WindowText, QColor(c["text"]))
    p.setColor(QPalette.Base, QColor(c["base"]))
    p.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    p.setColor(QPalette.Text, QColor(c["text"]))
    p.setColor(QPalette.Button, QColor(c["button"]))
    p.setColor(QPalette.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.ToolTipBase, QColor(c["tooltip"]))
    p.setColor(QPalette.ToolTipText, QColor(c["text"]))
    p.setColor(QPalette.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(c["accent_text"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["dim"]))
    p.setColor(QPalette.Link, QColor(c["accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["dim"]))
    app.setPalette(p)
    app.setStyleSheet(f"""
        QWidget {{ font-size: 10pt; }}
        QToolBar {{ background: {c['window']}; border: none; border-bottom: 1px solid {c['border']}; padding: 4px; spacing: 4px; }}
        QToolButton {{ padding: 5px 9px; border-radius: 6px; border: 1px solid transparent; }}
        QToolButton:hover {{ background: {c['hover']}; border-color: {c['border']}; }}
        QToolButton:checked {{ background: {c['accent']}; color: {c['accent_text']}; }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background: {c['base']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 6px; min-height: 18px; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
        QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow {{ width: 0px; height: 0px; }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
        QPushButton {{ background: {c['button']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 12px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:checked {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
        QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
        QTableView {{ background: {c['base']}; alternate-background-color: {c['alt']}; border: 1px solid {c['border']};
            border-radius: 8px; gridline-color: transparent; selection-background-color: {c['accent']}; }}
        QHeaderView::section {{ background: {c['window']}; color: {c['dim']}; border: none;
            border-bottom: 1px solid {c['border']}; padding: 6px; font-weight: 600; }}
        QListWidget::indicator, QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c['dim']}; border-radius: 4px; background: {c['base']}; }}
        QListWidget::indicator:checked, QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: none; }}
        QListWidget::item {{ padding: 3px; }}
        QListWidget {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 6px; }}
        QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 14px; padding-top: 6px; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['dim']}; }}
        QLabel#dim {{ color: {c['dim']}; }}
        QLabel#heading {{ font-size: 13pt; font-weight: 700; }}
        QLabel#dropHint {{ color: {c['dim']}; font-size: 12pt; border: 2px dashed {c['border']}; border-radius: 12px; padding: 40px; }}
        QScrollArea {{ border: none; }}
        QStatusBar {{ border-top: 1px solid {c['border']}; }}
        QProgressBar {{ border: 1px solid {c['border']}; border-radius: 5px; text-align: center; max-height: 14px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
        QSplitter::handle {{ background: transparent; }}
    """)
    return c


def camelot_hue(number: int) -> int:
    """Hue of a Camelot number as on the classic wheel: 3 yellow-green, 6 red, 9 violet, 12 cyan."""
    return int((90 - (number - 3) * 30) % 360)


def camelot_color(code: str | None, dark: bool) -> QColor | None:
    k = Key.from_code(code)
    if not k:
        return None
    hue = camelot_hue(k.camelot_number)
    # outer ring (major) a little lighter than the inner ring (minor), like the Camelot wheel
    if dark:
        return QColor.fromHsl(hue, 170, 150 if k.minor else 175)
    return QColor.fromHsl(hue, 200, 170 if k.minor else 195)
