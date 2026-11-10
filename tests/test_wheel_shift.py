"""Camelot wheel: Shift+click toggles the key with the keys 1 semitone higher/lower; Shift+right-click
additionally with each one's harmonic neighbours. Plain click / right-click / centre click are unchanged."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from keytool.keys import Key  # noqa: E402


@pytest.fixture
def wheel():
    QApplication.instance() or QApplication([])
    from keytool.gui.widgets import KeyFilter
    w = KeyFilter()
    w.resize(300, 300)
    yield w
    w.deleteLater()


def _pos(w, cam):
    """A point inside the segment of Camelot key `cam`."""
    code = Key.parse(cam).code
    for y in range(0, w.height(), 3):
        for x in range(0, w.width(), 3):
            if w._key_at(QPointF(x, y)) == code:
                return QPointF(x, y)
    raise AssertionError(cam)


def _click(w, pos, button=Qt.LeftButton, shift=False):
    mods = Qt.ShiftModifier if shift else Qt.NoModifier
    w.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, pos, w.mapToGlobal(pos.toPoint()),
                                  button, button, mods))


def cams(w):
    return sorted(Key.from_code(c).camelot() for c in w.selected())


def test_shift_click_toggles_key_and_semitone_neighbours(wheel):
    _click(wheel, _pos(wheel, "8A"), shift=True)              # Am + G#m (1A) + Bbm (3A)
    assert cams(wheel) == ["1A", "3A", "8A"]
    _click(wheel, _pos(wheel, "10B"), shift=False)            # plain click still toggles one key
    assert cams(wheel) == ["10B", "1A", "3A", "8A"]
    _click(wheel, _pos(wheel, "8A"), shift=True)              # again: the group goes off, the rest stays
    assert cams(wheel) == ["10B"]


def test_shift_right_click_adds_harmonic_neighbours_of_all_three(wheel):
    _click(wheel, _pos(wheel, "8A"), Qt.RightButton, shift=True)
    expected = sorted({n.camelot() for k in ("8A", "1A", "3A") for n in Key.parse(k).compatible()})
    assert cams(wheel) == expected
    assert {"7A", "9A", "8B", "12A", "2A", "1B", "4A", "3B"} <= set(expected)
    _click(wheel, _pos(wheel, "8A"), Qt.RightButton, shift=True)
    assert cams(wheel) == []


def test_shift_right_click_keeps_other_selection(wheel):
    _click(wheel, _pos(wheel, "5B"))
    _click(wheel, _pos(wheel, "8A"), Qt.RightButton, shift=True)
    assert "5B" in cams(wheel)


def test_plain_right_click_and_centre_unchanged(wheel):
    _click(wheel, _pos(wheel, "5B"))
    _click(wheel, _pos(wheel, "8A"), Qt.RightButton)          # replaces with key + harmonic neighbours
    assert cams(wheel) == sorted(k.camelot() for k in Key.parse("8A").compatible())
    _click(wheel, QPointF(wheel.width() / 2, wheel.height() / 2))
    assert cams(wheel) == []
