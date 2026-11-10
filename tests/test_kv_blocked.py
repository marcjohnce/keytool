"""Karaoke-Version's bot protection ("Client Challenge") is reported clearly instead of 'no results'."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import keytool.research as r
from keytool.keys import Key
from keytool.research import KaraokeVersionClient, KVBlocked, KVSong, is_challenge

FIX = Path(__file__).parent / "fixtures"


def _page(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_challenge_detection():
    assert is_challenge(_page("kv_challenge.html"))
    assert not is_challenge(_page("kv_list.html"))


def test_client_raises_blocked_with_browser_url(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "MIN_INTERVAL", 0)
    cl = KaraokeVersionClient(cache_file=tmp_path / "c.json", fetcher=lambda url: _page("kv_challenge.html"))
    keys = [Key.parse("8A"), Key.parse("1A")]
    with pytest.raises(KVBlocked) as e:
        cl.search(keys, 120, 128)
    assert "chordkey=22%2C21" in e.value.url and "tempo=120%2C128" in e.value.url   # all keys in one search
    with pytest.raises(KVBlocked):
        cl.load_details(KVSong("t", "a", None, "https://www.karaoke-version.de/custombackingtrack/x/y.html"))


def test_dialog_explains_and_offers_browser(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.gui.research_dialog import ResearchDialog
    from keytool.i18n import tr
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings())
    dlg = ResearchDialog(lib.settings, lib, [], None, None)
    gen = dlg.worker.gen
    dlg._blocked(gen, "https://www.karaoke-version.de/custombackingtrack/karaoke.html?chordkey=22")
    assert tr("kv_blocked") in dlg.status.text() and tr("kv_no_results") not in dlg.status.text()
    assert dlg.web_url.endswith("chordkey=22") and dlg.web_btn.isEnabled()
    assert dlg.web_btn.objectName() == "primary"
    _close(dlg)


def _close(dlg):
    """Close like a user would: stop the worker thread and let Qt delete the dialog before Python exits."""
    from PySide6.QtWidgets import QApplication
    dlg.done(0)
    dlg.worker._t.join(timeout=5)
    dlg.deleteLater()
    QApplication.processEvents()
