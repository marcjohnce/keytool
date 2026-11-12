"""Rich tooltip on the title/artist/album columns: cover, title, artist, album, year."""
import math
import os
import struct
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QEvent, QPointF, Qt, QThreadPool  # noqa: E402
from PySide6.QtGui import QColor, QHelpEvent, QImage, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.gui.main_window import MainWindow
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    for i, (t, a, al, y) in enumerate([("Alpha", "Ann", "First Album", 2019), ("Beta", "Bob", "", None)]):
        p = tmp_path / f"{i}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(n / 9))) for n in range(800)))
        tid = lib.db.upsert_file(str(p), 1, 0, dict(title=t, artist=a, album=al))
        lib.db.update_track(tid, year=y, status="ok")
    w = MainWindow(lib)
    w.show()
    w.table.sortByColumn(1, Qt.AscendingOrder)
    app.processEvents()
    yield w
    w.track_tip.hide()
    w.player.shutdown()
    w.queue.shutdown()
    w.deleteLater()


def _cell(win, row, col_name):
    from keytool.gui.widgets import TrackModel
    idx = win.proxy.index(row, TrackModel.COLUMNS.index(col_name))
    return win.table.visualRect(idx).center()


def _hover(win, row, col_name):
    pos = _cell(win, row, col_name)
    vp = win.table.viewport()
    QApplication.sendEvent(vp, QHelpEvent(QEvent.ToolTip, pos, vp.mapToGlobal(pos)))
    QApplication.processEvents()


def _move(win, row, col_name):
    pos = QPointF(_cell(win, row, col_name))
    vp = win.table.viewport()
    QApplication.sendEvent(vp, QMouseEvent(QEvent.MouseMove, pos, vp.mapToGlobal(pos.toPoint()),
                                           Qt.NoButton, Qt.NoButton, Qt.NoModifier))


def test_tip_shows_title_artist_album_year(win):
    tip = win.track_tip.tip
    _hover(win, 0, "title")
    assert tip.isVisible()
    assert (tip.title.text(), tip.artist.text(), tip.album.text(), tip.year.text()) == \
        ("Alpha", "Ann", "First Album", "2019")
    assert not tip.album.isHidden() and not tip.year.isHidden()
    _hover(win, 1, "album")                                     # no album, no year: lines hidden
    assert tip.title.text() == "Beta" and tip.album.isHidden() and tip.year.isHidden()


def test_only_on_title_artist_album(win):
    tip = win.track_tip.tip
    _hover(win, 0, "artist")
    assert tip.isVisible()
    _hover(win, 0, "bpm")
    assert not tip.isVisible()


def test_hides_when_mouse_moves_to_another_cell(win):
    tip = win.track_tip.tip
    _hover(win, 0, "title")
    _move(win, 0, "title")                                      # same cell: stays
    assert tip.isVisible()
    _move(win, 1, "title")
    assert not tip.isVisible()


def test_cover_loaded_in_background_and_cached(win, monkeypatch):
    img = QImage(300, 300, QImage.Format_RGB32); img.fill(QColor("#336699"))
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QBuffer.WriteOnly); img.save(buf, "PNG")
    calls = []
    import keytool.tags as tags
    monkeypatch.setattr(tags, "read_cover", lambda p: calls.append(p) or bytes(ba))
    tip = win.track_tip.tip
    _hover(win, 0, "title")
    QThreadPool.globalInstance().waitForDone(5000)
    QApplication.processEvents()
    assert tip.cover.image is not None and tip.cover.image.width() == 224
    _hover(win, 0, "artist")                                    # same track again: from the cache
    QThreadPool.globalInstance().waitForDone(5000)
    QApplication.processEvents()
    assert len(calls) == 1 and tip.cover.image is not None


def test_group_header_has_plain_tooltip_not_the_track_card(win):
    from keytool.gui.widgets import HEADER_ROLE, TrackModel
    from keytool.i18n import set_language
    win.group_combo.setCurrentIndex(1)                  # artist
    QApplication.processEvents()
    assert win.proxy.index(0, 0).data(HEADER_ROLE)
    _hover(win, 0, "artist")
    assert not win.track_tip.tip.isVisible()            # no empty cover card over the header
    tip = win.proxy.index(0, TrackModel.COLUMNS.index("title")).data(Qt.ToolTipRole)
    assert tip == "Grouped by artist: Ann (1 track)"
    set_language("de")
    try:
        assert win.proxy.index(0, 0).data(Qt.ToolTipRole) == "Gruppiert nach Interpret: Ann (1 Titel)"
    finally:
        set_language("en")
