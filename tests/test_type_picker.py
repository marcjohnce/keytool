"""Audio type drop-down: opening it must not toggle the entry under the mouse; a click in the list toggles."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from keytool.gui.widgets import AudioTypePicker  # noqa: E402


def _mouse(kind, pos):
    return QMouseEvent(kind, QPointF(pos), QPointF(pos), Qt.LeftButton,
                       Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton, Qt.NoModifier)


def test_opening_click_does_not_toggle():
    QApplication.instance() or QApplication([])
    p = AudioTypePicker()
    p.show(); p.showPopup()
    vp = p.view().viewport()
    first = p.view().visualRect(p.model().index(0, 0)).center()
    # release of the click that opened the list, over the first entry
    p.eventFilter(vp, _mouse(QEvent.MouseButtonRelease, first))
    assert p.selected() == []
    # a real click on the first entry toggles it, a second one toggles it back
    for expected in (1, 0):
        p.eventFilter(vp, _mouse(QEvent.MouseButtonPress, first))
        p.eventFilter(vp, _mouse(QEvent.MouseButtonRelease, first))
        assert len(p.selected()) == expected
    # reopening forgets a press from before
    p.eventFilter(vp, _mouse(QEvent.MouseButtonPress, first))
    p.hidePopup(); p.showPopup()
    p.eventFilter(vp, _mouse(QEvent.MouseButtonRelease, first))
    assert p.selected() == []
    p.hidePopup()


def test_list_closes_at_the_first_attempt_after_ticking():
    QApplication.instance() or QApplication([])
    p = AudioTypePicker()
    p.show(); p.showPopup()
    vp = p.view().viewport()
    first = p.view().visualRect(p.model().index(0, 0)).center()
    p.eventFilter(vp, _mouse(QEvent.MouseButtonPress, first))
    p.eventFilter(vp, _mouse(QEvent.MouseButtonRelease, first))
    assert p.view().isVisible()
    p.hidePopup()                      # Escape / click outside
    assert not p.view().isVisible()
