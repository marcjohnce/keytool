"""Audio type: detection from title / file name / folders, the AUDIO_TYPE tag, manual values, search filter,
and the grouped track list (headers, sorting inside groups, collapsing)."""
import os
import sys
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from keytool import audio_type as at  # noqa: E402
from keytool.config import Settings  # noqa: E402
from keytool.db import AT_DETECTED, AT_FROM_TAG, AT_MANUAL, Database, Query  # noqa: E402
from keytool.library import Library  # noqa: E402
from keytool.tags import read_metadata, write_metadata  # noqa: E402


@pytest.mark.parametrize("title,path,album,genre,expected", [
    ("Daylight", None, None, None, ["song"]),
    ("Daylight (Instrumental)", None, None, None, ["instrumental"]),
    ("Daylight (Instrumental Mix)", None, None, None, ["instrumental"]),
    ("Daylight (Acapella)", None, None, None, ["acapella"]),
    ("Daylight (Vocals)", None, None, None, ["acapella"]),
    ("Daylight (Kim Remix)", None, None, None, ["remix"]),
    ("Daylight (Club Remix Instrumental)", None, None, None, ["instrumental", "remix"]),
    ("Daylight - Kim Remix", None, None, None, ["remix"]),
    ("Daylight (Vocal Mix)", None, None, None, ["remix"]),
    ("Daylight (Original Mix)", None, None, None, ["song"]),
    ("Daylight - Extended Mix", None, None, None, ["song"]),
    ("Daylight (Radio Edit)", None, None, None, ["song"]),
    ("Daylight (Remastered 2011)", None, None, None, ["song"]),
    ("Daylight (feat. Kim)", None, None, None, ["song"]),
    ("Daylight [Drums Stem]", None, None, None, ["stem"]),
    ("Track 01", "C:/Musik/Hörbücher/Der Hobbit/01.mp3", None, None, ["audiobook"]),
    ("Folge 1", "D:/Hörspiele/Fall 1/01.mp3", None, None, ["radioplay"]),
    ("Folge 12: Neue Wege", None, None, None, ["podcast"]),
    ("Gespräch", None, None, "Podcast", ["podcast"]),
    ("Interview mit Kim", None, None, None, ["interview"]),
    ("Main Theme", None, "Inception (Original Motion Picture Soundtrack)", None, ["soundtrack"]),
    ("Final Score", None, None, None, ["song"]),
    ("Werbespot Autohaus", None, None, None, ["commercial"]),
    ("kick 01", "D:/Samples/Kicks/kick 01.wav", None, None, ["sfx"]),
    ("Thunder", None, None, None, ["song"]),
    ("Thunder", "x/Geräusche/thunder.wav", None, None, ["sfx"]),
    ("Samples of Love", None, None, None, ["song"]),
    ("Neue Aufnahme 12", None, None, None, ["note"]),
])
def test_detect(title, path, album, genre, expected):
    assert at.detect(title, path, album, genre) == expected


def test_file_name_counts_too():
    assert at.detect("Bad Guy", "x/Billie - Bad Guy (Instrumental).mp3") == ["instrumental"]


def test_tag_text_roundtrip():
    assert at.to_tag(["remix", "instrumental"]) == "Instrumental; Remix"
    assert at.from_tag("Instrumental; Remix") == ["instrumental", "remix"]
    assert at.from_tag("remix,Sound Effect, Hörbuch, nonsense") == ["remix", "audiobook", "sfx"]
    assert at.from_tag("") == []
    assert at.parse(["Instrumental", "podcast"]) == ["instrumental", "podcast"]
    assert at.decode(at.encode(["note", "song"])) == ["song", "note"]


def _wav(path):
    y = (np.sin(np.linspace(0, 2000, 22050)) * 3000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(y.tobytes())


def _mp3_or_flac(tmp_path, ext):
    """A tiny file in another format (needs FFmpeg/PyAV); skipped without it."""
    av = pytest.importorskip("av")
    src = str(tmp_path / "src.wav"); _wav(src)
    dst = str(tmp_path / f"t.{ext}")
    with av.open(src) as i, av.open(dst, "w") as o:
        s = o.add_stream("mp3" if ext == "mp3" else "flac", rate=22050)
        for f in i.decode(audio=0):
            for p in s.encode(f):
                o.mux(p)
        for p in s.encode(None):
            o.mux(p)
    return dst


@pytest.mark.parametrize("ext", ["wav", "mp3", "flac"])
def test_tag_written_and_read(tmp_path, ext):
    if ext == "wav":
        p = str(tmp_path / "t.wav"); _wav(p)
    else:
        try:
            p = _mp3_or_flac(tmp_path, ext)
        except Exception as e:                       # encoder missing in this PyAV build
            pytest.skip(str(e))
    write_metadata(p, audio_type="Instrumental; Remix")
    assert read_metadata(p).audio_type == "Instrumental; Remix"
    write_metadata(p, audio_type="")
    assert read_metadata(p).audio_type == ""


def test_library_detect_manual_tag_and_filter(tmp_path):
    music = tmp_path / "music"; music.mkdir()
    plain, inst, tagged = (str(music / n) for n in ("Kim - Daylight.wav", "Kim - Daylight (Instrumental).wav",
                                                     "Kim - Nightfall.wav"))
    for p in (plain, inst, tagged):
        _wav(p)
    write_metadata(tagged, audio_type="Acapella")
    lib = Library(Database(str(tmp_path / "lib.sqlite3")), Settings(write_tags=False, write_edits=True))
    lib.scan([plain, inst, tagged])
    t = {os.path.basename(p): lib.db.get_track_by_path(p) for p in (plain, inst, tagged)}
    assert t["Kim - Daylight.wav"]["audio_type"] == "song"
    assert t["Kim - Daylight (Instrumental).wav"]["audio_type"] == "instrumental"
    assert t["Kim - Nightfall.wav"]["audio_type"] == "acapella"                 # the tag wins
    assert t["Kim - Nightfall.wav"]["audio_type_manual"] == AT_FROM_TAG

    # set by hand: stored, written into the file, kept by an analysis and by a rescan
    tid = t["Kim - Daylight.wav"]["id"]
    assert lib.update_metadata([tid], {"audio_type": ["remix", "stem"]}) == {}
    row = lib.db.get_track(tid)
    assert (row["audio_type"], row["audio_type_manual"]) == ("remix,stem", AT_MANUAL)
    assert read_metadata(plain).audio_type == "Remix; Stem"
    lib.db.set_analysis(tid, {"bpm": 120.0})
    assert lib.db.get_track(tid)["audio_type"] == "remix,stem"
    # nothing ticked: back to the detected type
    lib.update_metadata([tid], {"audio_type": []})
    row = lib.db.get_track(tid)
    assert (row["audio_type"], row["audio_type_manual"]) == ("song", AT_DETECTED)

    # a new title is detected again (not set by hand)
    lib.update_metadata([tid], {"title": "Daylight (Kim Remix)"})
    assert lib.db.get_track(tid)["audio_type"] == "remix"

    ids = lambda q: {r["id"] for r in lib.search(q)}  # noqa: E731
    assert ids(Query(audio_types=["remix"])) == {tid}
    assert ids(Query(audio_types=["instrumental", "acapella"])) == {t["Kim - Daylight (Instrumental).wav"]["id"],
                                                                    t["Kim - Nightfall.wav"]["id"]}
    assert len(ids(Query())) == 3


def test_old_library_gets_types(tmp_path):
    import sqlite3
    path = str(tmp_path / "lib.sqlite3")
    db = Database(path)
    db.upsert_file("/m/Kim - Daylight (Acapella).wav", 1, 1.0, dict(title="Daylight (Acapella)"))
    c = sqlite3.connect(path); c.execute("UPDATE tracks SET audio_type=NULL"); c.commit(); c.close()
    db2 = Database(path)
    assert db2.search(Query())[0]["audio_type"] == "acapella"


def test_mcp_types():
    pytest.importorskip("mcp")
    from keytool import mcp_server
    assert mcp_server._parse_types(["Remix", "sfx", "Audio Book"]) == ["remix", "audiobook", "sfx"]
    with pytest.raises(ValueError):
        mcp_server._parse_types(["polka"])
    out = mcp_server._track_out(dict(id=1, title="t", artist="a", album="", year=None, bpm=None, key=None,
                                     path="p", audio_type="song,remix"))
    assert out["audio_type"] == ["song", "remix"]


# ------------------------------------------------------------------ grouped list
def test_grouping_sorts_inside_groups_and_collapses():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from keytool.gui.main_window import SortProxy
    from keytool.gui.widgets import GROUP_ROLE, HEADER_ROLE, ID_ROLE, TrackModel
    m = TrackModel()
    m.set_rows([dict(id=1, title="B", artist="Zed", album="X", path="1"),
                dict(id=2, title="A", artist="Zed", album="X", path="2"),
                dict(id=3, title="C", artist="Amy", album="Y", path="3"),
                dict(id=4, title="D", artist="", album="", path="4")])
    p = SortProxy(); p.setSourceModel(m)
    m.set_group_by("artist"); p.invalidate()
    title = m.COLUMNS.index("title")

    def listing():
        out = []
        for r in range(p.rowCount()):
            i = p.index(r, 0)
            out.append(("H", i.data(GROUP_ROLE)) if i.data(HEADER_ROLE) else i.data(ID_ROLE))
        return out

    p.sort(title, Qt.AscendingOrder)
    assert listing() == [("H", "amy"), 3, ("H", "zed"), 2, 1, ("H", "\uffff"), 4]
    p.sort(title, Qt.DescendingOrder)          # tracks reverse, groups and headers stay in place
    assert listing() == [("H", "amy"), 3, ("H", "zed"), 1, 2, ("H", "\uffff"), 4]
    m.collapsed.add("zed"); p.refilter()
    assert listing() == [("H", "amy"), 3, ("H", "zed"), ("H", "\uffff"), 4]
    # headers: not selectable, no id, label + count for screen readers
    hdr = p.index(2, 0)
    assert not (hdr.flags() & Qt.ItemIsSelectable) and hdr.data(ID_ROLE) is None
    assert "Zed" in hdr.data(Qt.AccessibleTextRole)
    m.set_group_by("none"); p.invalidate()
    assert len(listing()) == 4 and all(isinstance(x, int) for x in listing())

