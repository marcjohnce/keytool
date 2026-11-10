"""Number fields look exactly like line edits: same height, same text inset, same frame."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QLineEdit, QSpinBox  # noqa: E402


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_number_fields_match_line_edits(theme):
    app = QApplication.instance() or QApplication([])
    from keytool.gui.theme import SPIN_TEXT_INSET, apply_theme
    apply_theme(app, theme)
    le, sp, dsp = QLineEdit("2021"), QSpinBox(), QDoubleSpinBox()
    for w in (le, sp, dsp):
        w.resize(160, w.sizeHint().height()); w.show()
    app.processEvents()
    assert sp.height() == le.height() == dsp.height()
    for s in (sp, dsp):
        assert s.findChild(QLineEdit).textMargins().left() == SPIN_TEXT_INSET
