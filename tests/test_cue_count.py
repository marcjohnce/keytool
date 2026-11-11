"""Number of Serato cue points per track: stored in the library, shown in the list."""
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from keytool.config import Settings
from keytool.db import Database, Query
from keytool.library import Library
from keytool.serato import Cue, SeratoMarkers, count_cues

from test_core import _write_serato, _write_wav, make_track


def _wav(path, cues):
    _write_wav(path, make_track(120, 0, False, seconds=2))
    if cues:
        _write_serato(path, SeratoMarkers(cues=[Cue(i, 300 * (i + 1), "#cc0000") for i in range(cues)]))
    return path


@pytest.fixture
def lib(tmp_path):
    return Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))


def test_scan_stores_cue_count(tmp_path, lib):
    a, b = _wav(tmp_path / "a.wav", 3), _wav(tmp_path / "b.wav", 0)
    assert count_cues(str(a)) == 3 and count_cues(str(b)) == 0
    lib.scan([str(a), str(b)])
    got = {r["filename"]: r["cue_count"] for r in lib.search(Query())}
    assert got == {"a.wav": 3, "b.wav": 0}


def test_old_library_is_counted_later(tmp_path, lib):
    a = _wav(tmp_path / "a.wav", 2)
    lib.scan([str(a)])
    tid = lib.search(Query())[0]["id"]
    lib.db.update_track(tid, cue_count=None)                 # as in a library from before the column
    assert lib.fill_cue_counts() == [(tid, 2)]
    assert lib.db.get_track(tid)["cue_count"] == 2
    assert lib.fill_cue_counts() == []                       # nothing left


def test_migration_adds_column(tmp_path):
    p = tmp_path / "old.sqlite3"
    Database(p).upsert_file("/x/a.wav", 1, 0, {})
    con = sqlite3.connect(p)
    con.execute("ALTER TABLE tracks DROP COLUMN cue_count")          # library from before the column
    con.commit(); con.close()
    db = Database(p)
    assert [path for _, path in db.ids_without_cue_count()] == [os.path.normpath(os.path.abspath("/x/a.wav"))]


def test_list_column(lib):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from keytool.gui.widgets import SORT_ROLE, TrackModel
    m = TrackModel()
    m.set_rows([dict(id=1, title="a", cue_count=4, path="a"), dict(id=2, title="b", cue_count=0, path="b"),
                dict(id=3, title="c", cue_count=None, path="c")])
    col = m.COLUMNS.index("cues")
    assert [m.data(m.index(i, col)) for i in range(3)] == ["4", "0", ""]
    assert m.data(m.index(0, col), SORT_ROLE) < m.data(m.index(2, col), SORT_ROLE)
    assert m.data(m.index(0, col), Qt.TextAlignmentRole) == int(Qt.AlignCenter)


def test_deck_updates_the_count(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.gui.main_window import MainWindow
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    a = _wav(tmp_path / "a.wav", 1)
    lib.scan([str(a)])
    w = MainWindow(lib)
    try:
        row = w.model.rows[0]
        w.player.cues_read.emit(row["path"], 5)               # e.g. cues added in Serato meanwhile
        assert row["cue_count"] == 5 and lib.db.get_track(row["id"])["cue_count"] == 5
    finally:
        w._cue_cancel.set()
        w.player.shutdown(); w.queue.shutdown(); w.deleteLater()
