"""Track list columns: rearranged by dragging their header; order, widths and sort column remembered."""
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
    from PySide6.QtCore import Qt
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
        # widths set by the user are remembered (the last column fills the window and is not)
        bpm = TrackModel.COLUMNS.index("bpm")
        w.table.setColumnWidth(bpm, 131)
        assert s.column_widths == {"bpm": 131}
        w._widths_timer.timeout.emit()
        assert Settings.load().column_widths == {"bpm": 131}
        w.retranslate()                                   # header-based minimum widths keep the user's width
        assert w.table.columnWidth(bpm) == 131
        # the sort column and direction
        w.table.sortByColumn(TrackModel.COLUMNS.index("year"), Qt.DescendingOrder)
        assert (s.sort_column, s.sort_descending) == ("year", True)
        assert Settings.load().sort_column == "year"
        w.reset_columns()
        assert w.column_order() == list(TrackModel.COLUMNS) and s.column_order == [] and s.column_widths == {}
        assert w.table.columnWidth(bpm) != 131
    finally:
        w._cue_cancel.set()
        w.close()
    # a new window starts with the saved widths and sort column
    s2 = Settings.load()
    s2.column_widths, s2.sort_column, s2.sort_descending = {"bpm": 99}, "year", True
    w2 = MainWindow(Library(Database(str(tmp_path / "lib2.sqlite3")), s2))
    try:
        hh = w2.table.horizontalHeader()
        assert w2.table.columnWidth(TrackModel.COLUMNS.index("bpm")) == 99
        assert hh.sortIndicatorSection() == TrackModel.COLUMNS.index("year")
        assert hh.sortIndicatorOrder() == Qt.DescendingOrder
    finally:
        w2._cue_cancel.set()
        w2.close()
