"""Editing song sections and Serato cue points: writing cues into files, hand-edited sections, deck editing."""
import json
import os
import shutil
import struct
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from keytool import section_edit as se
from keytool.serato import Cue, Loop, SeratoMarkers, read_serato_markers, replace_cues, write_cues, _split_entries

from test_core import _write_serato, _write_wav, make_track


# --------------------------------------------------------------------------- writing cues
def test_replace_cues_keeps_other_entries_byte_for_byte():
    def entry(name, data):
        return name.encode() + b"\x00" + struct.pack(">I", len(data)) + data
    cue0 = b"\x00\x00" + struct.pack(">I", 1000) + b"\x00\xcc\x00\x00\x00\x00Old\x00"
    payload = (b"\x01\x01" + entry("COLOR", b"\x00\xff\x99\xff") + entry("CUE", cue0)
               + entry("FLIP", b"\x00\x01something-unknown") + entry("LOOP", b"\x00" * 21)
               + entry("BPMLOCK", b"\x01") + b"\x00")
    new = replace_cues(payload, [Cue(1, 2500, "#00cc00", "Drop"), Cue(0, 1200, "#cc0000", "Intro")])
    names = [n for n, _ in _split_entries(new)]
    assert names == ["COLOR", "CUE", "CUE", "FLIP", "LOOP", "BPMLOCK"]          # cues where they were
    old = dict(_split_entries(payload))
    for n, d in _split_entries(new):
        if n != "CUE":
            assert d == old[n]
    cues = [d for n, d in _split_entries(new) if n == "CUE"]
    assert cues[0][1] == 0 and struct.unpack(">I", cues[0][2:6])[0] == 1200 and b"Intro" in cues[0]
    # no tag yet: cues come first
    assert [n for n, _ in _split_entries(replace_cues(None, [Cue(0, 5, "#cc0000")]))] == ["CUE"]


def _make(tmp_path, ext):
    wav = tmp_path / "src.wav"
    _write_wav(wav, make_track(120, 0, False, seconds=2))
    if ext == "wav":
        return wav
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to create test files")
    out = tmp_path / f"t.{ext}"
    codec = {"mp3": ["-c:a", "libmp3lame"], "flac": [], "m4a": ["-c:a", "aac"], "ogg": ["-c:a", "libvorbis"]}[ext]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), *codec, str(out)], check=True)
    return out


@pytest.mark.parametrize("ext", ["wav", "mp3", "flac", "m4a", "ogg"])
def test_write_cues_round_trip(tmp_path, ext):
    p = str(_make(tmp_path, ext))
    if ext != "ogg":                                        # existing tag with a loop and a track colour
        _write_serato(p, SeratoMarkers(cues=[Cue(0, 500, "#cc0000", "A")],
                                       loops=[Loop(0, 100, 900, "#27aae1", True, "L")], track_color="#ff99ff"))
    write_cues(p, [Cue(0, 700, "#cc0000", "Start"), Cue(3, 1500, "#0000cc", "Drop")])
    m = read_serato_markers(p)
    assert [(c.index, c.position_ms, c.name) for c in m.cues] == [(0, 700, "Start"), (3, 1500, "Drop")]
    if ext != "ogg":
        assert m.track_color == "#ff99ff" and [(l.start_ms, l.end_ms, l.name) for l in m.loops] == [(100, 900, "L")]
    write_cues(p, [])                                       # delete all cues
    assert read_serato_markers(p).cues == []


def test_write_cues_keeps_other_tags(tmp_path):
    p = str(_make(tmp_path, "flac"))
    import mutagen
    f = mutagen.File(p); f["TITLE"] = "Song"; f["BPM"] = "128"; f.save()
    write_cues(p, [Cue(0, 1000, "#cc0000")])
    f = mutagen.File(p)
    assert f["TITLE"] == ["Song"] and f["BPM"] == ["128"]


# --------------------------------------------------------------------------- section editing (pure)
SECS = [dict(start=0.0, end=10.0, group="A", label="intro", conf=0.8),
        dict(start=10.0, end=40.0, group="B", label="verse", conf=0.7),
        dict(start=40.0, end=60.0, group="C", label="chorus", conf=0.9)]


def _tiles(secs, duration=60.0):
    return secs[0]["start"] == 0 and secs[-1]["end"] == duration and \
        all(a["end"] == b["start"] for a, b in zip(secs, secs[1:]))


def test_split_move_delete_rename():
    s = se.split(SECS, 25.0)
    assert [x["start"] for x in s] == [0, 10, 25, 40] and _tiles(s) and s[2]["group"] == "D"
    assert SECS[1]["end"] == 40.0                                              # argument untouched
    assert se.split(SECS, 10.4) == SECS                                         # would be shorter than 1 s
    m = se.move_boundary(SECS, 1, 12.5)
    assert m[0]["end"] == m[1]["start"] == 12.5 and m[0]["conf"] == 1.0 and _tiles(m)
    assert se.move_boundary(SECS, 1, 50)[1]["start"] == 39.0                    # stays inside its neighbours
    assert se.move_boundary(SECS, 0, 5) == SECS                                 # the start of the track is fixed
    d = se.delete(SECS, 1)
    assert [(x["start"], x["end"]) for x in d] == [(0, 40), (40, 60)]           # time goes to the previous one
    d = se.delete(SECS, 0)
    assert d[0]["start"] == 0 and d[0]["label"] == "verse" and _tiles(d)
    assert se.delete([SECS[0]], 0) == []
    r = se.rename(se.split(SECS, 50.0), 3, label="verse")
    assert r[3]["label"] == "verse" and r[3]["group"] == "B"                    # same name -> same colour
    r = se.rename(SECS, 2, name="Solo")
    assert r[2]["label"] == "custom" and r[2]["name"] == "Solo" and se.display_name(r[2]) == "Solo"
    assert se.rename(SECS, 1, label="nonsense") == SECS
    assert se.create(200.0) == [dict(start=0.0, end=200.0, group="A", label="verse", conf=1.0)]
    assert len(se.split([], 30.0, duration=60.0)) == 2                          # no sections yet


# --------------------------------------------------------------------------- library
@pytest.fixture
def lib(tmp_path):
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    p = tmp_path / "a.wav"
    _write_wav(p, make_track(120, 9, True, seconds=3))
    lib.scan([str(p)])
    return lib


def _track(lib):
    from keytool.db import Query
    return lib.search(Query())[0]


def test_hand_edited_sections_survive_analysis(lib):
    t = _track(lib)
    lib.set_sections(t["id"], SECS)
    t = lib.db.get_track(t["id"])
    assert t["sections_manual"] == 1 and json.loads(t["sections"]) == SECS
    lib.db.set_analysis(t["id"], dict(sections=[dict(start=0, end=60, group="A", label="verse", conf=0.5)]))
    assert json.loads(lib.db.get_track(t["id"])["sections"]) == SECS              # analysis keeps them
    assert lib.submit_args(lib.db.get_track(t["id"]), sections_only=True) is None  # nothing to detect
    assert t["id"] not in lib.db.ids_needing_sections()
    lib.reset_manual([t["id"]])
    t = lib.db.get_track(t["id"])
    assert t["sections_manual"] == 0
    lib.db.set_analysis(t["id"], dict(sections=[]))
    assert lib.db.get_track(t["id"])["sections"] == "[]"


def test_clear_and_redetect_sections(lib):
    t = _track(lib)
    lib.db.update_track(t["id"], sections=json.dumps(SECS), status="ok")
    lib.clear_sections([t["id"]])
    t = lib.db.get_track(t["id"])
    assert t["sections"] == "[]" and t["sections_manual"] == 1
    lib.allow_section_detection([t["id"]])
    assert lib.submit_args(lib.db.get_track(t["id"]), sections_only=True)[-1] is True


def test_set_cues_writes_file_and_count(lib):
    t = _track(lib)
    n = lib.set_cues(t["id"], [Cue(0, 500, "#cc0000", "In"), Cue(1, 1500, "#cc8800")])
    t = lib.db.get_track(t["id"])
    assert n == 2 and t["cue_count"] == 2 and t["mtime"] == os.stat(t["path"]).st_mtime
    assert [c.name for c in read_serato_markers(t["path"]).cues] == ["In", ""]


def test_migration_adds_sections_manual(tmp_path):
    import sqlite3
    from keytool.db import Database
    p = tmp_path / "old.sqlite3"
    Database(p).upsert_file("/x/a.wav", 1, 0, {})
    con = sqlite3.connect(p)
    con.execute("ALTER TABLE tracks DROP COLUMN sections_manual")
    con.commit(); con.close()
    assert Database(p).get_track(1)["sections_manual"] == 0


# --------------------------------------------------------------------------- deck waveform editing
class Ev:
    """Minimal mouse event for calling the handlers directly."""
    def __init__(self, x, y, alt=False):
        from PySide6.QtCore import QPointF
        self._p, self.alt = QPointF(x, y), alt

    def position(self):
        return self._p

    def button(self):
        from PySide6.QtCore import Qt
        return Qt.LeftButton

    def modifiers(self):
        from PySide6.QtCore import Qt
        return Qt.AltModifier if self.alt else Qt.NoModifier


@pytest.fixture
def wave(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    w = pl.WaveformView()
    w.resize(608, 84 + w.STRIP)                      # 600 px for 60 s: 10 px per second
    w.duration, w.bpm = 60.0, 120.0                  # bars of 2 s
    w.peaks = np.ones(100, np.float32); w.colors = np.full((100, 3), 128, np.uint8)
    w.editable = True
    w.set_sections(SECS)
    w.cues = [Cue(0, 10000, "#cc0000", "A")]
    w.grab()
    yield w
    w.deleteLater(); app.processEvents()


def x(t):
    return 4 + t * 10


def test_drag_section_boundary_snaps_to_bars(wave):
    got = []
    wave.sections_edited.connect(got.append)
    y = 84 + wave.STRIP / 2
    assert wave._boundary_at(Ev(x(40) + 2, y).position()) == 2
    assert wave._boundary_at(Ev(x(40) + 2, 40).position()) is None              # only in the name strip
    wave.mousePressEvent(Ev(x(40), y))
    wave.mouseMoveEvent(Ev(x(45.3), y))
    wave.mouseReleaseEvent(None)
    assert got and got[-1][2]["start"] == 46.0 and got[-1][1]["end"] == 46.0    # 10 + n*2 s (anchor 10)
    wave.mousePressEvent(Ev(x(46), y))
    wave.mouseMoveEvent(Ev(x(43.3), y, alt=True))                              # Alt: free
    wave.mouseReleaseEvent(None)
    assert got[-1][2]["start"] == 43.3


def test_drag_cue_moves_it_click_jumps(wave):
    moved, jumped = [], []
    wave.cues_edited.connect(moved.append)
    wave.cue_clicked.connect(jumped.append)
    wave.mousePressEvent(Ev(x(10), 20))
    wave.mouseReleaseEvent(None)
    assert jumped == [10000] and moved == []                                    # click: jump only
    wave.mousePressEvent(Ev(x(10), 20))
    wave.mouseMoveEvent(Ev(x(13.2), 20))
    wave.mouseReleaseEvent(None)
    assert moved[-1][0].position_ms == 13000                                    # snapped to the beat (0.5 s)
    assert jumped == [10000]


def test_menu_actions(wave):
    from PySide6.QtCore import QPointF
    texts = lambda m: [a.text() for a in m.actions()]
    from keytool.i18n import tr
    m = wave.build_menu(QPointF(x(30), 40))
    assert tr("cue_add_here") in texts(m) and tr("sec_split_here") in texts(m) and tr("sec_redetect") in texts(m)
    got = []
    wave.sections_edited.connect(got.append)
    next(a for a in m.actions() if a.text() == tr("sec_split_here")).trigger()
    assert [s["start"] for s in got[-1]] == [0, 10, 30, 40]
    m = wave.build_menu(QPointF(x(10), 10))                                     # on the cue flag
    assert tr("cue_delete_n", n=1) in texts(m)
    cues = []
    wave.cues_edited.connect(cues.append)
    next(a for a in m.actions() if a.text() == tr("cue_delete_n", n=1)).trigger()
    assert cues[-1] == []
    wave.add_cue_at(20.0)
    assert [(c.index, c.position_ms) for c in cues[-1]] == [(0, 20000)]         # first free slot
    sub = next(a.menu() for a in wave.build_menu(QPointF(x(50), 40)).actions() if a.menu())
    next(a for a in sub.actions() if a.text() == tr("sec_verse")).trigger()
    assert got[-1][-1]["label"] == "verse" and got[-1][-1]["group"] == got[-1][1]["group"]
    wave.set_sections([])
    m = wave.build_menu(QPointF(x(30), 40))
    next(a for a in m.actions() if a.text() == tr("sec_create")).trigger()
    assert [s["start"] for s in got[-1]] == [0, 30]


def test_main_window_stores_edits(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.config import Settings
    from keytool.db import Database, Query
    from keytool.gui.main_window import MainWindow
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    p = tmp_path / "a.wav"
    _write_wav(p, make_track(120, 9, True, seconds=3))
    lib.scan([str(p)])
    win = MainWindow(lib)
    tid = lib.search(Query())[0]["id"]
    win._deck_sections_edited(tid, SECS)
    assert lib.db.get_track(tid)["sections_manual"] == 1
    win._deck_cues_edited(tid, [Cue(0, 1200, "#cc0000", "In")])
    assert [c.position_ms for c in read_serato_markers(str(p)).cues] == [1200]
    assert lib.db.get_track(tid)["cue_count"] == 1
    row = next(r for r in win.model.rows if r["id"] == tid)
    assert row["cue_count"] == 1 and row["sections_manual"] == 1
    win.table.selectRow(0)
    win.clear_sections_selected()
    assert lib.db.get_track(tid)["sections"] == "[]"
    win.redetect_sections(tid)
    assert lib.db.get_track(tid)["sections_manual"] == 0
    win._cue_cancel.set()
    win.player.shutdown(); win.queue.shutdown(); win.deleteLater(); app.processEvents()


def test_cues_are_numbered_by_position(wave):
    from keytool.serato import number_by_position
    got = number_by_position([Cue(5, 30000, "#cc0000", "late"), Cue(0, 20000, "#00cc00"), Cue(2, 5000, "#0000cc")])
    assert [(c.index, c.position_ms) for c in got] == [(0, 5000), (1, 20000), (2, 30000)]
    assert got[2].name == "late" and got[2].color == "#cc0000"               # name and colour stay with the cue
    assert len(number_by_position([Cue(i, i * 100, "#cc0000") for i in range(10)])) == 8   # Serato: 8 slots
    wave.set_markers(SeratoMarkers(cues=[Cue(3, 30000, "#cc0000"), Cue(1, 10000, "#cc0000")]))
    assert [(c.index, c.position_ms) for c in wave.cues] == [(0, 10000), (1, 30000)]
    moved = []
    wave.cues_edited.connect(moved.append)
    wave.grab()
    wave.mousePressEvent(Ev(x(10), 20))                                     # cue 1 dragged behind cue 2
    wave.mouseMoveEvent(Ev(x(40), 20))
    wave.mouseReleaseEvent(None)
    assert [(c.index, c.position_ms) for c in moved[-1]] == [(0, 30000), (1, 40000)]
    wave.add_cue_at(5.0)                                                     # new first cue: all move up
    assert [(c.index, c.position_ms) for c in moved[-1]] == [(0, 5000), (1, 30000), (2, 40000)]
    from PySide6.QtCore import QPointF
    from keytool.i18n import tr
    assert not any("…" in a.text() and "Cue 1" in a.text()
                   for a in wave.build_menu(QPointF(x(5), 10)).actions())     # no rename entry any more
