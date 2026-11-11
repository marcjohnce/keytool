"""Deck behaviour in the main window: only double-click/Enter load a title, the deck follows re-analyses,
context-menu filters."""
import math
import os
import struct
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)        # no audio device needed
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.gui.main_window import MainWindow
    from keytool.keys import Key
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    for i, (t, a, al, k, b) in enumerate([("A1", "Alpha", "One", "8A", 124.0), ("A2", "Alpha", "", "9A", 122.0),
                                          ("B1", "Beta", "Two", "8A", 128.0), ("B2", "Beta", "Two", None, 100.0)]):
        p = tmp_path / f"{i}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(n / 9))) for n in range(8000)))
        tid = lib.db.upsert_file(str(p), 1, 0, dict(title=t, artist=a, album=al))
        lib.db.update_track(tid, key=Key.parse(k).code if k else None, bpm=b, status="ok")
    w = MainWindow(lib)
    w.show()
    w.table.sortByColumn(1, Qt.AscendingOrder)
    app.processEvents()
    yield w
    w.player.shutdown()
    w.queue.shutdown()
    w.deleteLater()


def _row_of(win, title):
    return next(r for r in range(win.proxy.rowCount()) if win.proxy.index(r, 1).data() == title)


def _click(win, title, double=False):
    vp = win.table.viewport()
    pos = win.table.visualRect(win.proxy.index(_row_of(win, title), 1)).center()
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, pos)
    if double:
        QTest.mouseDClick(vp, Qt.LeftButton, Qt.NoModifier, pos)


def test_only_double_click_loads_the_deck(win):
    _click(win, "A2")
    assert win.player.row is None and [r["title"] for r in win.details.rows] == ["A2"]
    _click(win, "B1", double=True)
    assert win.player.row["title"] == "B1"
    _click(win, "A1")                                   # selecting another title leaves the deck alone
    assert win.player.row["title"] == "B1"
    win.table.setFocus(); win.table.selectRow(_row_of(win, "A2"))
    QTest.keyClick(win.table, Qt.Key_Return)
    assert win.player.row["title"] == "A2"
    win.play_adjacent(1, False)                          # next relative to the deck, not the selection
    assert win.player.row["title"] == "B1"


def test_deck_follows_reanalysis(win):
    from keytool.keys import Key
    _click(win, "A1", double=True)
    tid = win.player.row["id"]
    win.lib.db.update_track(tid, key=Key.parse("2A").code, bpm=99.0)

    class Item:
        track_id = tid
    win.on_queue_event("finished", Item())
    assert win.player.key_chip.text == "2A" and win.player.bpm_lbl.text().startswith("99")
    assert Key.from_code(win.model.deck_key).camelot() == "2A" and win.model.deck_bpm == 99.0


def test_context_menu_filters(win):
    win.table.selectRow(_row_of(win, "B1"))
    r = win._menu_row()
    win.filter_by_artist(r); win.refresh()
    assert sorted(win.proxy.index(i, 1).data() for i in range(win.proxy.rowCount())) == ["B1", "B2"]
    win.reset_filters(); win.table.selectRow(_row_of(win, "B1"))
    win.filter_by_album(win._menu_row()); win.refresh()
    assert win.f_album.text() == "Two" and win.proxy.rowCount() == 2
    win.reset_filters(); win.table.selectRow(_row_of(win, "A1"))
    win.filter_by_key(win._menu_row()); win.refresh()
    assert sorted(win.proxy.index(i, 1).data() for i in range(win.proxy.rowCount())) == ["A1", "B1"]


def test_queue_opens_only_on_request(win):
    import time
    from keytool.i18n import tr
    btn = win.toolbar.widgetForAction(win.a_queue)
    win.enqueue([r["id"] for r in win.model.rows], keep_tag_values=False)
    assert not win.queue_dock.isVisible()                      # no automatic opening
    assert btn.property("busy") is True and win.a_queue.text().split()[-1].isdigit()   # e.g. "Warteschlange  4"
    deadline = time.time() + 120
    while win.queue.is_busy() and time.time() < deadline:
        QApplication.processEvents(); time.sleep(0.05)
    for _ in range(20):
        QApplication.processEvents(); time.sleep(0.02)
    assert not win.queue.is_busy()
    assert btn.property("busy") is False and win.a_queue.text() == tr("queue")
    win.a_queue.setChecked(True)                               # opening by hand still works
    assert win.queue_dock.isVisible()


def test_toolbar_buttons_share_font_and_height(win):
    """Regression: widgets built before the theme kept the system font, so the re-styled queue button
    (Archivo) sat 1 px off its neighbours."""
    from PySide6.QtWidgets import QWidget
    from keytool.gui.theme import load_fonts
    if load_fonts():
        assert {w.font().family() for w in win.findChildren(QWidget)} == {"Archivo"}
    btns = [win.toolbar.widgetForAction(a) for a in (win.a_details, win.a_queue, win.a_settings)]
    btn = btns[1]
    btn.setProperty("busy", True); btn.style().unpolish(btn); btn.style().polish(btn)
    QApplication.processEvents()
    assert len({b.sizeHint().height() for b in btns}) == 1


def test_context_menu_order(win):
    from keytool.i18n import tr
    _click(win, "A1")
    m = win.build_track_menu()
    items = [("-" if a.isSeparator() else a.text()) for a in m.actions()]
    assert items == ["▶  " + tr("play"), "-", tr("filter_by"), "-", "⚡  " + tr("analyze_now"), tr("reanalyze"),
                     tr("write_tags"), tr("reset_manual"), tr("sec_delete_selected"), tr("add_keyword"), "-",
                     tr("remove")]
    sub = m.actions()[2].menu()
    assert [a.text() for a in sub.actions()] == [tr("artist"), tr("album"), tr("key"), tr("filter_key_semitones")]
    sub.actions()[3].trigger()                                    # A1 is 8A -> 8A, 1A (−1), 3A (+1)
    from keytool.keys import Key
    assert sorted(Key.from_code(c).camelot() for c in win.f_keys.selected()) == ["1A", "3A", "8A"]
