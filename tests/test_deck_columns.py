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
