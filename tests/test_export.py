"""CSV export: columns, delimiters, file names, the GUI export of the selection and the MCP export of everything."""
import csv
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from keytool import export as ex
from keytool.keys import Key

from test_core import _write_wav, make_track

T = dict(id=1, title="Glass Harbour", artist="Mara Veil", album="Tides", year=2021, genre="House", comment="",
         bpm=124.46, key=Key.parse("8A").code, energy=7, duration=245.4, cue_count=3, keywords=["warm-up", "vocal"],
         filename="glass.flac", folder="/m", path="/m/glass.flac", ext=".flac", bitrate=900, size=31_500_000,
         sections=json.dumps([dict(start=0, end=16, group="A", label="intro", conf=1),
                              dict(start=16, end=60, group="B", label="custom", name="Solo", conf=1)]),
         added_at=0, analyzed_ok_at=None)


def _read(path, delimiter):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f, delimiter=delimiter))


def test_values_and_headers(tmp_path):
    cols = ["artist", "title", "bpm", "bpm_exact", "key", "camelot", "openkey", "key_name", "duration", "cues",
            "keywords", "format", "size_mb", "sections"]
    p = tmp_path / "a.csv"
    assert ex.write_csv(str(p), [T], cols, ",", notation="sharps") == 1
    head, row = _read(p, ",")
    assert head[:4] == ["Artist", "Title", "BPM", "BPM (exact)"]
    got = dict(zip(cols, row))
    assert got["bpm"] == "124" and got["bpm_exact"] == "124.46" and got["key"] == "Am" and got["camelot"] == "8A"
    assert got["openkey"] == "1m" and got["key_name"] == "A minor" and got["duration"] == "4:05" and got["cues"] == "3"
    assert got["keywords"] == "warm-up, vocal" and got["format"] == "FLAC" and got["size_mb"] == "31.5"
    assert got["sections"] == "Intro 0:00, Solo 0:16"
    assert open(p, "rb").read(3) == b"\xef\xbb\xbf"                         # BOM: Excel reads UTF-8


def test_semicolon_and_decimal_comma(tmp_path):
    p = tmp_path / "b.csv"
    d = ex.resolve_delimiter("auto", "de")
    assert d == ";" and ex.resolve_delimiter("auto", "en") == "," and ex.resolve_delimiter("tab", "de") == "\t"
    ex.write_csv(str(p), [dict(T, artist="A; B")], ["artist", "bpm_exact"], d)
    rows = _read(p, ";")
    assert rows[1] == ["A; B", "124,46"]                                      # quoted where needed


def test_columns_and_file_names():
    assert ex.valid_columns(["bpm", "nope", "bpm", "title"]) == ["bpm", "title"]
    assert ex.valid_columns([]) == ex.DEFAULT_COLUMNS
    assert ex.default_filename("Set {count}", 12) == "Set 12.csv"
    assert ex.default_filename("a/b:{date}.csv", 1).startswith("a_b_20")
    assert ex.default_filename("{oops}", 1) == "{oops}.csv"


@pytest.fixture
def lib(tmp_path):
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False, language="en"))
    for n in ("b", "a", "c"):
        p = tmp_path / f"{n}.wav"
        _write_wav(p, make_track(120, 9, True, seconds=2))
    lib.scan([str(tmp_path / f"{n}.wav") for n in "bac"])
    for t in lib.search(__import__("keytool.db", fromlist=["Query"]).Query()):
        lib.db.update_track(t["id"], artist=t["filename"][0].upper(), title="T" + t["filename"][0])
    return lib


def test_library_export_selection_order_and_all(lib, tmp_path):
    from keytool.db import Query
    ids = {t["filename"]: t["id"] for t in lib.search(Query())}
    lib.db.add_keywords([ids["a.wav"]], ["peak"])
    lib.settings.export_columns = ["artist", "keywords"]
    p = tmp_path / "sel.csv"
    assert lib.export_csv(str(p), [ids["c.wav"], ids["a.wav"]]) == 2
    assert _read(p, ",")[1:] == [["C", ""], ["A", "peak"]]                     # order of the list
    assert lib.export_csv(str(p)) == 3                                          # None: everything
    assert [r[0] for r in _read(p, ",")[1:]] == ["A", "B", "C"]


def test_mcp_export_all_and_overwrite(lib, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import keytool.mcp_server as ms
    monkeypatch.setattr(ms, "lib", lambda: lib)
    p = str(tmp_path / "all.csv")
    res = ms.export_csv(p, columns=["title", "artist"])
    assert res["written"] and res["tracks"] == 3 and _read(p, ",")[0] == ["Title", "Artist"]
    again = ms.export_csv(p)
    assert not again["written"] and "overwrite" in again["reason"]           # existing file: not replaced
    assert ms.export_csv(p, overwrite=True, delimiter="semicolon")["written"]
    assert _read(p, ";")[0][0] == "Artist"
    with pytest.raises(ValueError):
        ms.export_csv(p, columns=["nope"], overwrite=True)
    with pytest.raises(ValueError):
        ms.export_csv(str(tmp_path / "x.txt"))


def _window(lib, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.gui.main_window import MainWindow
    return MainWindow(lib)


def _close(w):
    w._cue_cancel.set()
    w.player.shutdown(); w.queue.shutdown(); w.deleteLater()


def test_gui_exports_selection_to_fixed_target_and_asks_before_replacing(lib, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w = _window(lib, tmp_path, monkeypatch)
    try:
        w.s.export_ask, w.s.export_folder, w.s.export_filename = False, str(tmp_path / "out"), "Set-{count}"
        w.s.export_columns = ["artist"]
        w.export_selected()
        assert not (tmp_path / "out").exists()                                 # nothing selected
        w.table.selectAll()
        w.export_selected()
        target = tmp_path / "out" / "Set-3.csv"
        assert len(_read(target, ",")) == 4 and "3" in w.msg_lbl.text()
        target.write_text("keep me")
        asked = []
        monkeypatch.setattr(QMessageBox, "exec", lambda self: asked.append(self.text()) or 0)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)  # dialog closed: cancel
        w.export_selected()
        assert asked and target.read_text() == "keep me"                      # not replaced without a yes
    finally:
        _close(w)


def test_gui_asks_with_save_dialog(lib, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    w = _window(lib, tmp_path, monkeypatch)
    try:
        w.s.export_ask, w.s.export_folder = True, str(tmp_path)
        seen = {}

        def fake(parent, title, start, filt):
            seen["start"] = start
            return str(tmp_path / "picked" / "mine"), filt
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake))
        w.table.selectAll()
        w.export_selected()
        assert seen["start"].startswith(str(tmp_path)) and seen["start"].endswith(".csv")
        assert (tmp_path / "picked" / "mine.csv").exists()
        assert w.s.export_folder == str(tmp_path / "picked")                  # remembered for next time
    finally:
        _close(w)


def test_settings_page_columns(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.gui.settings_dialog import SettingsDialog
    s = Settings(export_columns=["bpm", "artist"])
    d = SettingsDialog(s)
    assert d.export_columns() == ["bpm", "artist"]                             # ticked, in file order
    d.export_cols.item(2).setCheckState(Qt.Checked)
    d.export_delim.setCurrentIndex(d.export_delim.findData("tab"))
    d.export_ask.setChecked(False)
    d.apply()
    assert s.export_columns[:2] == ["bpm", "artist"] and len(s.export_columns) == 3
    assert s.export_delimiter == "tab" and s.export_ask is False
    d.deleteLater()
