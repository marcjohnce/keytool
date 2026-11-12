"""The details title stays on one line and ends with "…" when it is too long (full text in the tooltip)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from keytool.gui.widgets import DetailPanel, ElidedLabel  # noqa: E402


def test_elided_label():
    QApplication.instance() or QApplication([])
    long = "(God Must Have Spent) A Little More Time On You"
    lab = ElidedLabel(long)
    lab.resize(120, 30); lab.show(); QApplication.processEvents()
    shown = QLabel_text(lab)
    assert shown.endswith("…") and len(shown) < len(long)
    assert lab.text() == long and lab.toolTip() == long
    assert lab.fontMetrics().horizontalAdvance(shown) <= lab.contentsRect().width()
    lab.resize(2000, 30); QApplication.processEvents()
    assert QLabel_text(lab) == long and lab.toolTip() == ""
    assert not lab.wordWrap()


def QLabel_text(lab):
    from PySide6.QtWidgets import QLabel
    return QLabel.text(lab)


def test_details_title_is_one_line(tmp_path):
    QApplication.instance() or QApplication([])
    from keytool.db import Database
    db = Database(str(tmp_path / "lib.sqlite3"))
    tid = db.upsert_file("/m/x.mp3", 1, 1.0, dict(title="(God Must Have Spent) A Little More Time On You",
                                                    artist="*NSYNC", year=2014))
    d = DetailPanel()
    d.resize(220, 800); d.show(); QApplication.processEvents()
    d.set_rows([db.get_track(tid)])
    QApplication.processEvents()
    h = d.heading
    assert isinstance(h, ElidedLabel) and h.text().startswith("(God Must")
    assert h.height() < 2 * h.fontMetrics().height()          # one line
    assert QLabel_text(h).endswith("…")
