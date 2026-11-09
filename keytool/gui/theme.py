"""Light and dark themes (Fusion palette + a small stylesheet) and Camelot key colors."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..keys import Key

THEMES = {
    # Redesign: neutral ink-blue surfaces, warm off-white text, amber only for playback/primary actions.
    # Colour belongs to the Camelot keys; everything else stays quiet.
    "dark": dict(
        window="#161B2A", deck="#1C2234", panel="#191F2F", base="#1F2638", alt="#1F2638", text="#ECE6D9",
        dim="#9CA2B3", far="#8D93A4", button="#1F2638", border="#34405A", accent="#F0A53A", accent_text="#1A1206",
        hover="#28304A", select="#2C3550", danger="#FF6B6B", tooltip="#28304A", invert_bg="#ECE6D9",
        invert_text="#161B2A",
    ),
    "light": dict(
        window="#EEF0F4", deck="#E4E7EE", panel="#F6F7FA", base="#FFFFFF", alt="#FFFFFF", text="#192033",
        dim="#535B6E", far="#6B7385", button="#FFFFFF", border="#CDD2DD", accent="#B4650A", accent_text="#FFFFFF",
        hover="#E2E6EE", select="#DCE2EE", danger="#C62828", tooltip="#FFFFFF", invert_bg="#192033",
        invert_text="#FFFFFF",
    ),
}

CUE_COLOR = "#D9443E"          # Serato-style red for all cue points
FONT_FAMILY = "Archivo"
_font_loaded: bool | None = None


def load_fonts() -> str:
    """Register the bundled Archivo variable font (wdth 62-125, wght 100-900). Returns the family to use."""
    global _font_loaded
    if _font_loaded is None:
        _font_loaded = False
        try:
            from importlib.resources import files
            from PySide6.QtGui import QFontDatabase
            path = files("keytool").joinpath("resources/fonts/Archivo.ttf")
            if path.is_file():
                _font_loaded = QFontDatabase.addApplicationFont(str(path)) >= 0
        except Exception:
            _font_loaded = False
    return FONT_FAMILY if _font_loaded else ""


def styled_font(base, *, size: float | None = None, weight: int | None = None, width: float | None = None,
                tabular: bool = True):
    """Copy of `base` with point size, weight and variable width ('wdth' axis, 100 = normal)."""
    from PySide6.QtGui import QFont
    f = QFont(base)
    if size:
        f.setPointSizeF(size)
    if weight:
        f.setWeight(QFont.Weight(weight))
    if width and width != 100:
        try:
            f.setVariableAxis(QFont.Tag("wdth"), float(width))
        except Exception:
            f.setStretch(int(width))
    if tabular:
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except Exception:
            pass
    return f


def colors(name: str) -> dict:
    return THEMES[resolve_theme(name)]


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
    family = load_fonts()
    if family:
        f = styled_font(app.font(), size=10, weight=400)
        f.setFamily(family)
        app.setFont(f)
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
    p.setColor(QPalette.Highlight, QColor(c["select"]))
    p.setColor(QPalette.HighlightedText, QColor(c["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["dim"]))
    p.setColor(QPalette.Link, QColor(c["accent"]))
    p.setColor(QPalette.Mid, QColor(c["border"]))
    p.setColor(QPalette.Accent, QColor(c["accent"])) if hasattr(QPalette, "Accent") else None
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["dim"]))
    app.setPalette(p)
    app.setStyleSheet(f"""
        QWidget {{ font-size: 10pt; }}
        QMainWindow, QDialog {{ background: {c['window']}; }}
        QToolBar {{ background: {c['window']}; border: none; border-bottom: 1px solid {c['border']}; padding: 8px 12px; spacing: 6px; }}
        QToolBar QLabel#brand {{ font-size: 14pt; font-weight: 800; padding-right: 14px; }}
        QToolButton {{ padding: 6px 10px; border-radius: 8px; border: 1px solid transparent; background: transparent; }}
        QToolButton:hover {{ background: {c['hover']}; border-color: {c['border']}; }}
        QToolButton::menu-indicator {{ image: none; width: 0; }}
        QToolBar QToolButton#tbPrimary {{ border-color: {c['border']}; background: {c['button']}; }}
        QToolButton#seg, QToolButton#segFirst, QToolButton#segLast {{ border: 1px solid {c['border']}; background: transparent; }}
        QToolButton:checked {{ background: {c['invert_bg']}; color: {c['invert_text']}; border-color: {c['invert_bg']}; font-weight: 700; }}
        QToolButton#seg {{ border-radius: 0; border-left: none; }}
        QToolButton#segFirst {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
        QToolButton#segLast {{ border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; }}
        QToolButton#seg:checked, QToolButton#segFirst:checked, QToolButton#segLast:checked {{
            background: {c['invert_bg']}; color: {c['invert_text']}; border-color: {c['invert_bg']}; }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
            background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 9px; min-height: 20px;
            selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
        QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow {{ width: 0px; height: 0px; }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border-color: {c['accent']}; }}
        QPushButton {{ background: {c['button']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 7px 14px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:focus {{ border-color: {c['accent']}; }}
        QPushButton:checked {{ background: {c['invert_bg']}; color: {c['invert_text']}; border-color: {c['invert_bg']}; font-weight: 700; }}
        QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 700; }}
        QPushButton#primary:hover {{ background: {QColor(c['accent']).lighter(110).name()}; }}
        QPushButton#link {{ background: transparent; border: none; color: {c['accent']}; padding: 4px 0; }}
        QPushButton:disabled, QPushButton#primary:disabled {{ background: {c['window']}; color: {c['dim']};
            border-color: {c['border']}; }}
        QGroupBox:disabled {{ color: {c['dim']}; }}
        QTableView {{ background: {c['window']}; alternate-background-color: {c['window']}; border: none;
            gridline-color: transparent; selection-background-color: {c['select']}; selection-color: {c['text']};
            outline: 0; }}
        QTableView::item {{ border-top: 1px solid {c['border']}; padding: 0 6px; }}
        QTableView::item:selected {{ background: {c['select']}; color: {c['text']}; }}
        QHeaderView {{ background: {c['window']}; }}
        QHeaderView::section {{ background: {c['window']}; color: {c['dim']}; border: none; padding: 8px 6px;
            font-size: 9pt; font-weight: 600; }}
        QListWidget::indicator, QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['dim']}; border-radius: 4px; background: {c['base']}; }}
        QListWidget::indicator:checked, QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: none; }}
        QListWidget::item {{ padding: 4px; }}
        QListWidget {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px; }}
        QGroupBox {{ border: none; border-top: 1px solid {c['border']}; margin-top: 18px; padding-top: 10px; font-weight: 700; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 0px; padding: 0; color: {c['text']}; }}
        QLabel#dim {{ color: {c['dim']}; }}
        QLabel#heading {{ font-size: 13pt; font-weight: 700; }}
        QLabel#dropHint {{ color: {c['dim']}; font-size: 12pt; border: 2px dashed {c['border']}; border-radius: 12px; padding: 40px; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollArea#rail {{ border-right: 1px solid {c['border']}; }}
        QScrollArea#inspector {{ border-left: 1px solid {c['border']}; background: {c['panel']}; }}
        QWidget#inspectorBody {{ background: {c['panel']}; }}
        QStatusBar {{ background: {c['deck']}; border-top: 1px solid {c['border']}; padding: 2px 12px; }}
        QStatusBar QLabel {{ padding: 2px 6px; }}
        QProgressBar {{ border: none; background: {c['border']}; border-radius: 2px; max-height: 4px; min-height: 4px; color: transparent; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 2px; }}
        QSplitter::handle {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; margin: 2px; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
        QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; margin: 2px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QMenu {{ background: {c['tooltip']}; border: 1px solid {c['border']}; padding: 4px; }}
        QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
        QMenu::item:selected {{ background: {c['select']}; }}
        QToolTip {{ background: {c['tooltip']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
        QDockWidget::title {{ background: {c['deck']}; padding: 6px 12px; }}
        /* ---- deck (player): the reference everything else is compared to */
        #playerPanel {{ background: {c['deck']}; border: none; border-bottom: 1px solid {c['border']}; }}
        #playerPanel QLabel#deckTitle {{ font-size: 21pt; font-weight: 800; }}
        #playerPanel QLabel#deckBpm {{ font-size: 36pt; font-weight: 300; }}
        #playerPanel QLabel#deckCaption {{ color: {c['dim']}; font-size: 9pt; }}
        #playerPanel QLabel#deckArtist {{ color: {c['dim']}; font-size: 11pt; }}
        #playerPanel QLabel#deckTime {{ color: {c['dim']}; font-size: 10.5pt; }}
        #playerPanel QLabel#deckSmall {{ color: {c['dim']}; font-size: 9pt; }}
        #playerPanel QPushButton {{ min-height: 34px; }}
        #playerPanel QPushButton#transpose {{ min-width: 58px; padding: 6px 8px; }}
        #playerPanel QToolButton {{ border: none; background: transparent; padding: 4px; }}
        #playerPanel QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
        #playerPanel QSlider::sub-page:horizontal {{ background: {c['text']}; border-radius: 2px; }}
        #playerPanel QSlider::handle:horizontal {{ width: 14px; margin: -5px 0; border-radius: 7px; background: {c['text']}; }}
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
    # outer ring (major) lighter than the inner ring (minor), like the Camelot wheel;
    # text on a key chip is always near-black, so every hue stays readable
    if dark:
        return QColor.fromHsl(hue, 158, 148 if k.minor else 173)
    return QColor.fromHsl(hue, 178, 158 if k.minor else 184)


CHIP_TEXT = QColor("#13131A")
