"""Mixed In Key import (MIKStore.db, SQLite) against a synthetic library with the real MIK 10 schema."""
import os
import shutil
import sqlite3

import pytest

from keytool.config import Settings
from keytool.db import Database, Query
from keytool.keys import Key
from keytool.library import Library
from keytool.mik_import import _path_key, apply_import, plan_import, read_mik

from test_core import _write_wav, make_track

SCHEMA = """
CREATE TABLE Song (Id TEXT PRIMARY KEY, File TEXT, FilePathHash TEXT, ArtistName TEXT, SongName TEXT, Comment TEXT,
    Tempo REAL, OverallVolume REAL, OverallEnergy INTEGER, EnergySegmentsCount INTEGER, StandardPitch REAL,
    KeyResultSummary TEXT, DateAdded TEXT, ClippedPeaksCount INTEGER, Artwork BLOB, LastAnalyzedUtc TEXT,
    Genre TEXT, Album TEXT, Grouping TEXT, Year INTEGER, MainKey TEXT, MainKeyConfidence REAL, SecondKey TEXT,
    SecondKeyConfidence REAL, IsAnalyzed INTEGER, HasPNTag INTEGER, PNTagIsProcessed INTEGER,
    PNTagAppliedClipRepair INTEGER, PNTagVolumeAnalysisVersion INTEGER, PNTagVolumeUnits REAL,
    PNTagOutputVolume REAL, LastModifiedUtc TEXT);
CREATE TABLE Collection (Id TEXT, ThirdPartyId TEXT, Name TEXT, Sequence INTEGER, LibraryTypeId INTEGER, IsLibrary INTEGER);
CREATE TABLE SongCollectionMembership (Id TEXT, SongId TEXT, CollectionId TEXT);
"""


def _mik_db(path, songs, collections=()):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for i, (f, key, bpm, energy) in enumerate(songs):
        con.execute("INSERT INTO Song (Id, File, ArtistName, SongName, Tempo, OverallEnergy, MainKey, "
                    "MainKeyConfidence, IsAnalyzed) VALUES (?,?,?,?,?,?,?,?,1)",
                    (f"s{i}", f, "Artist", f"Song {i}", bpm, energy, key, 0.8))
    con.execute("INSERT INTO Collection VALUES ('root', NULL, 'MIKRoot', 0, 1, 1)")
    for cid, name, members in collections:
        con.execute("INSERT INTO Collection VALUES (?, NULL, ?, 0, 1, 0)", (cid, name))
        for m in members:
            con.execute("INSERT INTO SongCollectionMembership VALUES (?, ?, ?)", (f"{cid}{m}", f"s{m}", cid))
        con.execute("INSERT INTO SongCollectionMembership VALUES (?, ?, 'root')", (f"r{cid}", "s0"))
    con.commit(); con.close()


@pytest.fixture
def setup(tmp_path):
    music = tmp_path / "music"; music.mkdir()
    files = []
    for i in range(4):
        p = music / f"t{i}.wav"
        _write_wav(p, make_track(120, 0, False, seconds=3, seed=i))
        files.append(p)
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False, use_existing_tags=True))
    lib.scan([str(files[0]), str(files[1]), str(files[2])])           # t3 is only known to Mixed In Key
    ids = {r["filename"]: r["id"] for r in lib.search(Query())}
    moved = tmp_path / "old place" / "t2.wav"                         # MIK still has t2 at an old path
    mik = tmp_path / "MIKStore.db"
    _mik_db(mik, [(str(files[0]), "8A", 124.4321, 7), (str(files[1]), "11B", 98.0, 4),
                  (str(moved), "3A", 128.0, 9), (str(files[3]), "5A", 140.0, 6),
                  (str(tmp_path / "gone.wav"), "1A", 100.0, 5)],
            collections=[("c1", "Electro Swing", [0, 1]), ("c2", "Peak Time", [0])])
    return lib, ids, mik, files


def test_read_and_plan(setup):
    lib, ids, mik, files = setup
    songs = read_mik(str(mik))
    assert len(songs) == 5
    s0 = songs[0]
    assert Key.from_code(s0.key).camelot() == "8A" and s0.bpm == 124.43 and s0.energy == 7
    assert s0.collections == ["Electro Swing", "Peak Time"]              # built-in root collection ignored
    plan = plan_import(lib.db, songs)
    assert set(plan.matched) == {ids["t0.wav"], ids["t1.wav"], ids["t2.wav"]}
    assert plan.by_filename == 1                                         # t2 found via its file name
    assert [s.file for s in plan.missing_existing] == [str(files[3])]
    assert len(plan.missing_gone) == 1


def test_apply_overwrite_manual_keywords_and_add(setup):
    lib, ids, mik, files = setup
    lib.set_bpm(ids["t1.wav"], 101.0)                                   # set by hand -> must survive
    plan = plan_import(lib.db, read_mik(str(mik)))
    res = apply_import(lib, plan, add_missing=True, collections_as_keywords=True, overwrite=True)
    assert res.added == 1 and res.updated == 4 and res.skipped_manual == 1
    t0 = lib.db.get_track(ids["t0.wav"])
    assert (Key.from_code(t0["key"]).camelot(), t0["bpm"], t0["energy"]) == ("8A", 124.43, 7)
    assert t0["key_source"] == "mik" and t0["bpm_source"] == "mik" and t0["status"] == "ok"
    t1 = lib.db.get_track(ids["t1.wav"])
    assert t1["bpm"] == 101.0 and Key.from_code(t1["key"]).camelot() == "11B"
    t3 = lib.db.get_track_by_path(str(files[3]))
    assert t3 and Key.from_code(t3["key"]).camelot() == "5A" and t3["status"] == "ok"
    assert {r["filename"] for r in lib.search(Query(keywords=["Electro Swing"]))} == {"t0.wav", "t1.wav"}
    assert {r["filename"] for r in lib.search(Query(keywords=["peak time"]))} == {"t0.wav"}
    # a later analysis of an imported track keeps the MIK values (like values from the file tags) ...
    assert lib.job_parts(t0) == (False, False)
    lib.db.set_analysis(ids["t0.wav"], dict(bpm=90.0, key=Key.parse("1A").code), keep_tag_values=True)
    assert lib.db.get_track(ids["t0.wav"])["bpm"] == 124.43
    # ... but an explicit re-analysis replaces them
    lib.db.set_analysis(ids["t0.wav"], dict(bpm=90.0), keep_tag_values=False)
    assert lib.db.get_track(ids["t0.wav"])["bpm"] == 90.0


def test_fill_only_keeps_existing(setup):
    lib, ids, mik, _ = setup
    lib.db.update_track(ids["t0.wav"], bpm=130.0, bpm_source="analysis", key=None)
    plan = plan_import(lib.db, read_mik(str(mik)))
    apply_import(lib, plan, overwrite=False, collections_as_keywords=False)
    t0 = lib.db.get_track(ids["t0.wav"])
    assert t0["bpm"] == 130.0 and Key.from_code(t0["key"]).camelot() == "8A"   # only the empty key was filled
    assert not lib.db.all_keywords()


def test_windows_paths_match_case_insensitively():
    assert _path_key(r"D:\Music\Song.FLAC") == _path_key("d:/music/song.flac")
    assert _path_key("/home/a/B.wav") != _path_key("/home/a/b.wav")             # POSIX stays case-sensitive


def test_rejects_other_sqlite(tmp_path):
    p = tmp_path / "x.db"
    sqlite3.connect(p).execute("CREATE TABLE t (a)").connection.commit()
    with pytest.raises(ValueError):
        read_mik(str(p))
