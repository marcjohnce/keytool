"""The Karaoke-Version dialog only composes the search and opens it in the browser (the site blocks bots)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from keytool.keys import Key

pytest.importorskip("PySide6")


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.gui import research_dialog
    from keytool.library import Library
    opened = []
    monkeypatch.setattr(research_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings())
    dlg = research_dialog.ResearchDialog(lib.settings, lib, [Key.parse("8A").code], 120, 128)
    dlg.opened = opened
    yield dlg
    dlg.done(0)
    dlg.deleteLater()
    QApplication.processEvents()


def test_no_result_list_any_more(dialog):
    from PySide6.QtWidgets import QTableView
    assert not dialog.findChildren(QTableView)
    assert not hasattr(dialog, "worker")


def test_search_opens_browser_with_neighbours_and_bpm(dialog):
    dialog.semitones.setChecked(True)
    dialog.search_btn.click()
    url = dialog.opened[-1]
    assert "chordkey=22%2C21%2C23" in url and "tempo=120%2C128" in url     # Am + G#m + Bbm, one search
    assert "1A" in dialog.preview.text() and "3A" in dialog.preview.text()


def test_semitones_off_and_empty_criteria(dialog):
    dialog.semitones.setChecked(False)
    dialog.search_btn.click()
    assert "chordkey=22&" in dialog.opened[-1] + "&" and "1A" not in dialog.preview.text()
    dialog.wheel.clear(); dialog.bmin.setValue(0); dialog.bmax.setValue(0)
    assert not dialog.search_btn.isEnabled() and dialog.url() == ""
    dialog.bmin.setValue(90)                                                  # BPM alone is enough
    assert dialog.search_btn.isEnabled() and "tempo=90%2C300" in dialog.url()


def test_semitone_choice_is_remembered(dialog):
    dialog.semitones.setChecked(False)
    dialog.done(0)
    assert dialog.s.research_semitones is False


def test_wheel_behaves_like_main_window(dialog):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QPushButton
    from keytool.i18n import tr
    assert tr("research_extend") in [b.text() for b in dialog.findChildren(QPushButton)]
    assert len(dialog.findChildren(QPushButton)) == 3                         # extend, close, search – no "clear"
    w = dialog.wheel
    assert w.selected()
    c = QPointF(w.width() / 2, w.height() / 2)
    ev = QMouseEvent(QMouseEvent.MouseButtonPress, c, w.mapToGlobal(QPoint(int(c.x()), int(c.y()))),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    w.mousePressEvent(ev)                                                      # click in the centre: all keys off
    assert w.selected() == [] and "chordkey" not in dialog.url()
