"""Redesign: 'fits' and 'vs. deck' columns relative to the track in the deck."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from keytool.gui.widgets import DECK_ROLE, ID_ROLE, SORT_ROLE, TrackModel, harmonic_fit  # noqa: E402
from keytool.keys import Key  # noqa: E402


def code(c):
    return Key.parse(c).code


def test_harmonic_fit():
    assert harmonic_fit(code("8A"), code("8A")) == 2
    assert harmonic_fit(code("9A"), code("8A")) == 1
    assert harmonic_fit(code("7A"), code("8A")) == 1
    assert harmonic_fit(code("8B"), code("8A")) == 1      # relative major
    assert harmonic_fit(code("1A"), code("12A")) == 1     # wraps around the wheel
    assert harmonic_fit(code("9B"), code("8A")) == 0
    assert harmonic_fit(code("3A"), code("8A")) == 0
    assert harmonic_fit(None, code("8A")) is None
    assert harmonic_fit(code("8A"), None) is None


def test_model_deck_columns():
    QApplication.instance() or QApplication([])
    m = TrackModel()
    m.set_rows([dict(id=1, title="Deck", key=code("8A"), bpm=124.5, path="a"),
                dict(id=2, title="Near", key=code("9A"), bpm=125.0, path="b"),
                dict(id=3, title="Far", key=code("3A"), bpm=174.0, path="c")])
    m.set_deck(1, code("8A"), 124.5)
    fit, delta = m.COLUMNS.index("fit"), m.COLUMNS.index("delta")
    # the id must be available on every column, the first one in particular (selection uses column 0)
    for c in range(m.columnCount()):
        assert m.index(1, c).data(ID_ROLE) == 2
    assert m.index(0, fit).data(DECK_ROLE) is True
    assert m.index(0, fit).data(SORT_ROLE) == -1
    assert m.index(1, fit).data(SORT_ROLE) == 1
    assert m.index(2, fit).data(SORT_ROLE) == 2
    assert m.index(1, delta).data(Qt.DisplayRole).startswith("+0")
    assert m.index(2, delta).data(SORT_ROLE) == pytest.approx(39.76, abs=0.01)
    m.set_deck(None, None, None)
    assert m.index(1, delta).data(Qt.DisplayRole) == ""


def test_bpm_shown_rounded_with_exact_tooltip():
    QApplication.instance() or QApplication([])
    m = TrackModel()
    m.set_rows([dict(id=1, title="a", bpm=124.5, path="a"), dict(id=2, title="b", bpm=127.49, path="b"),
                dict(id=3, title="c", bpm=None, path="c")])
    col = m.COLUMNS.index("bpm")
    assert [m.data(m.index(i, col)) for i in range(3)] == ["125", "127", ""]
    assert m.data(m.index(1, col), Qt.ToolTipRole).startswith("127.49 BPM")
    assert m.data(m.index(1, col), SORT_ROLE) == 127.49                     # sorting keeps the exact value


def test_deck_row_text_uses_the_accent_text_colour():
    QApplication.instance() or QApplication([])
    from keytool.gui.theme import THEMES
    m = TrackModel()
    m.set_rows([dict(id=1, title="Deck", artist="A", key=code("8A"), bpm=124.0, path="a"),
                dict(id=2, title="Other", artist="B", key=code("9A"), bpm=125.0, path="b")])
    m.set_deck(1, code("8A"), 124.0)
    for dark in (True, False):
        m.dark = dark
        want = THEMES["dark" if dark else "light"]["accent_text"].lower()
        for col in ("title", "artist", "bpm", "delta"):
            c = m.COLUMNS.index(col)
            assert m.data(m.index(0, c), Qt.ForegroundRole).name() == want, col
        assert m.data(m.index(1, m.COLUMNS.index("title")), Qt.ForegroundRole) is None


def test_deck_row_is_painted_in_accent(tmp_path):
    from PySide6.QtWidgets import QTableView
    from keytool.gui.theme import THEMES
    from keytool.gui.widgets import TrackDelegate
    QApplication.instance() or QApplication([])
    m = TrackModel()
    m.set_rows([dict(id=1, title="Deck", key=code("8A"), bpm=124.0, path="a"),
                dict(id=2, title="Other", key=code("9A"), bpm=125.0, path="b")])
    m.set_deck(1, code("8A"), 124.0)
    v = QTableView(); v.setModel(m); v.setItemDelegate(TrackDelegate(m, v)); v.resize(900, 200); v.show()
    QApplication.processEvents()
    img = v.viewport().grab().toImage()
    col = m.COLUMNS.index("duration")
    r = v.visualRect(m.index(0, col))
    assert img.pixelColor(r.center()).name() == THEMES["dark"]["accent"].lower()
    r2 = v.visualRect(m.index(1, col))
    assert img.pixelColor(r2.center()).name() != THEMES["dark"]["accent"].lower()
