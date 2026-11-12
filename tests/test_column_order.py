"""Track list columns: rearranged by dragging their header, order remembered in the settings."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

from keytool.config import Settings  # noqa: E402
from keytool.db import Database  # noqa: E402
from keytool.library import Library  # noqa: E402


def test_column_order_is_movable_and_remembered(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.gui.main_window import MainWindow
    from keytool.gui.widgets import TrackModel
    s = Settings(write_tags=False)
    # saved by an older version: "key" moved to the front, without the newer "audio_type" column, plus a name
    # that no longer exists
    saved = ["key"] + [c for c in TrackModel.COLUMNS if c not in ("key", "audio_type")] + ["gone_column"]
    s.column_order = saved
    w = MainWindow(Library(Database(str(tmp_path / "lib.sqlite3")), s))
    try:
        order = w.column_order()
        assert sorted(order) == sorted(TrackModel.COLUMNS)
        assert order[0] == "key"
        assert order.index("audio_type") == order.index("album") + 1     # new column behind its default neighbour
        hh = w.table.horizontalHeader()
        assert hh.sectionsMovable()
        hh.moveSection(hh.visualIndex(TrackModel.COLUMNS.index("bpm")), 0)    # what a drag does
        assert w.column_order()[:2] == ["bpm", "key"] and s.column_order[:2] == ["bpm", "key"]
        assert Settings.load().column_order[:2] == ["bpm", "key"]          # saved right away
        w.reset_column_order()
        assert w.column_order() == list(TrackModel.COLUMNS) and s.column_order == []
    finally:
        w._cue_cancel.set()
        w.close()
