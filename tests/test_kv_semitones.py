"""Karaoke-Version search including keys 1 semitone below/above (transposable on the website)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from keytool.keys import Key
from keytool.research import KVSong, semitone_shift, with_semitones


def cam(keys):
    return [k.camelot() for k in keys]


def test_with_semitones():
    assert cam(with_semitones([Key.parse("8A")])) == ["8A", "1A", "3A"]          # Am + G#m + Bbm
    both = with_semitones([Key.parse("8A"), Key.parse("1A")])                    # overlapping neighbours
    assert cam(both) == ["8A", "1A", "3A", "6A"]                                 # shared neighbour only once


def test_semitone_shift():
    target = {Key.parse("8A").code}
    assert semitone_shift([Key.parse("8A").code], target) == 0
    assert semitone_shift([Key.parse("1A").code], target) == 1                   # G#m +1 -> Am
    assert semitone_shift([Key.parse("3A").code], target) == -1                  # Bbm -1 -> Am
    assert semitone_shift([Key.parse("8B").code], target) is None                # other mode: no
    assert semitone_shift([Key.parse("3A").code, Key.parse("8A").code], target) == 0   # key change: 0 wins


def test_dialog_queries_neighbours_and_shows_shift(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.gui.research_dialog import KVModel, ResearchDialog
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings())
    dlg = ResearchDialog(lib.settings, lib, [], None, None)
    seen = []
    monkeypatch.setattr(dlg.worker, "search", lambda keys, lo, hi, page, new: seen.append(cam(keys)))
    dlg.wheel.set_selected([Key.parse("8A").code])
    dlg.new_search()
    assert seen[-1] == ["8A", "1A", "3A"]
    m = dlg.model
    m.append([KVSong("Direct", "A", None, "u1", keys=[Key.parse("8A").code]),
              KVSong("Lower", "B", None, "u2", keys=[Key.parse("1A").code])])
    col = KVModel.COLS.index("shift")
    assert m.data(m.index(0, col)) == "" and m.data(m.index(1, col)) == "+1"
    assert "a-Moll" in m.data(m.index(1, col), Qt.ToolTipRole) or "minor" in m.data(m.index(1, col), Qt.ToolTipRole)
    dlg.semitones.setChecked(False)                                              # option off: chosen keys only
    dlg.new_search()
    assert seen[-1] == ["8A"] and dlg.table.isColumnHidden(col)
    _close(dlg)


def _close(dlg):
    """Close like a user would: stop the worker thread and let Qt delete the dialog before Python exits."""
    from PySide6.QtWidgets import QApplication
    dlg.done(0)
    dlg.worker._t.join(timeout=5)
    dlg.deleteLater()
    QApplication.processEvents()
