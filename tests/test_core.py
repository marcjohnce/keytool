import os
import shutil
import subprocess
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from synth import make_track, SR  # noqa: E402
from keytool.keys import Key, all_keys  # noqa: E402
from keytool.db import Database, Query  # noqa: E402
from keytool.config import Settings  # noqa: E402
from keytool.library import Library  # noqa: E402
from keytool.tags import read_metadata  # noqa: E402


# ------------------------------------------------------------------ keys
def test_notation_roundtrip():
    assert len(all_keys()) == 24
    for k in all_keys():
        for n in ("camelot", "sharps", "flats", "openkey"):
            assert Key.parse(k.format(n)) == k, (k, n)


@pytest.mark.parametrize("text,camelot", [
    ("Am", "8A"), ("A minor", "8A"), ("C", "8B"), ("F#m", "11A"), ("Gbm", "11A"), ("Bb", "6B"),
    ("Ebm", "2A"), ("8a", "8A"), ("12B", "12B"), ("1m", "8A"), ("1d", "8B"), ("a-moll", "8A"), ("Db", "3B"),
])
def test_parse(text, camelot):
    assert Key.parse(text).camelot() == camelot


def test_compatible():
    k = Key.parse("8A")
    assert sorted(x.camelot() for x in k.compatible()) == sorted(["8A", "7A", "9A", "8B"])
    assert {x.camelot() for x in Key.parse("12B").compatible()} == {"12B", "11B", "1B", "12A"}


# ------------------------------------------------------------------ end to end
def _write_wav(path, y):
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


CASES = [  # (ext, bpm, pc, minor, artist, title, year)
    ("mp3", 128, 9, True, "Alpha", "Night Drive", 2019),
    ("wav", 100, 10, False, "Beta", "Sunny", 2001),
    ("flac", 174, 2, True, "Gamma", "Rush", 2022),
    ("ogg", 95, 7, False, "Delta", "Lazy", 1998),
    ("m4a", 122, 4, True, "Alpha", "Deep", 2019),
]


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to create test files")
    root = tmp_path_factory.mktemp("music")
    sub = root / "Sub Folder" / "Deeper"
    sub.mkdir(parents=True)
    for i, (ext, bpm, pc, mn, artist, title, year) in enumerate(CASES):
        raw = root / f"raw{i}.wav"
        _write_wav(raw, make_track(bpm, pc, mn, seconds=35, seed=i))
        target = (sub if i % 2 else root) / f"{artist} - {title}.{ext}"
        if ext == "wav":
            shutil.move(raw, target)
            continue
        codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "192k"], "flac": ["-c:a", "flac"],
                 "ogg": ["-c:a", "libvorbis"], "m4a": ["-c:a", "aac", "-b:a", "192k"]}[ext]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), *codec,
                        "-metadata", f"title={title}", "-metadata", f"artist={artist}",
                        "-metadata", f"album={artist} Album", "-metadata", f"date={year}", str(target)], check=True)
        raw.unlink()
    db = Database(root / "lib.sqlite3")
    s = Settings(write_tags=True, tag_key_notation="camelot", workers=2)
    lib = Library(db, s)
    res = lib.import_paths([str(root)])
    return lib, res, root


def test_import(library):
    lib, res, _ = library
    assert res["found"] == 5 and res["ok"] == 5 and res["errors"] == 0


def test_analysis_results(library):
    lib, _, _ = library
    rows = {r["title"]: r for r in lib.search(Query())}
    for ext, bpm, pc, mn, artist, title, year in CASES:
        r = rows[title]
        assert abs(r["bpm"] - bpm) < 1.0, (title, r["bpm"])
        assert r["key"] == Key(pc, mn).code, (title, r["key"])
        assert r["artist"] == artist
        if ext != "wav":
            assert r["year"] == year


def test_tags_written(library):
    lib, _, _ = library
    for r in lib.search(Query()):
        md = read_metadata(r["path"])
        assert md.tag_key == Key.from_code(r["key"]).camelot(), r["path"]
        assert md.tag_bpm.split(".")[0] == str(int(round(r["bpm"]))), (r["path"], md.tag_bpm)


def test_rescan_unchanged(library):
    lib, _, root = library
    rep = lib.scan([str(root)])
    assert rep.unchanged == 5 and not rep.to_analyze


def test_search_and_keywords(library):
    lib, _, root = library
    db = lib.db
    assert len(lib.search(Query(artist="alpha"))) == 2
    assert len(lib.search(Query(year_from=2010))) == 3
    assert len(lib.search(Query(bpm_min=120, bpm_max=130))) == 2
    assert len(lib.search(Query(keys=[Key.parse("8A").code]))) == 1
    assert len(lib.search(Query(text="night"))) == 1
    assert len(lib.search(Query(folder=str(root / "Sub Folder")))) == 2
    ids = [r["id"] for r in lib.search(Query(artist="alpha"))]
    db.add_keywords(ids, ["Warmup", "Peak"])
    db.add_keywords(ids[:1], ["Vocal"])
    assert len(lib.search(Query(keywords=["warmup"]))) == 2
    assert len(lib.search(Query(keywords=["peak", "vocal"]))) == 1
    assert len(lib.search(Query(keywords=["peak", "vocal"], keywords_mode="any"))) == 2
    assert len(lib.search(Query(text="vocal"))) == 1
    db.remove_keywords(ids[:1], ["vocal"])
    assert len(lib.search(Query(keywords=["vocal"]))) == 0
    assert dict(db.all_keywords())["Peak"] == 2


def test_compatible_tracks(library):
    lib, _, _ = library
    t = lib.search(Query(title="Night Drive"))[0]
    comp = lib.compatible_tracks(t["id"], bpm_tolerance=10)
    assert all(r["id"] != t["id"] for r in comp)


@pytest.mark.parametrize("a4", [432.0, 436.0, 443.0, 446.0, 449.0])
def test_detuned_key(a4):
    from keytool.analysis import detect_key, detect_bpm
    for pc, mn in ((9, True), (5, False), (1, True)):
        y = make_track(118, pc, mn, seconds=30, a4=a4)
        assert detect_key(y)[0] == Key(pc, mn).code
    assert abs(detect_bpm(make_track(118, 0, False, seconds=30, a4=a4))[0] - 118) < 1


def test_no_drums_and_silence():
    from keytool.analysis import detect_key, detect_bpm
    y = make_track(100, 7, True, seconds=30, drums=False)
    assert detect_key(y)[0] == Key(7, True).code
    assert detect_key(np.zeros(SR * 10, dtype=np.float32)) == (None, 0.0)
    assert detect_bpm(np.zeros(SR * 10, dtype=np.float32))[0] is None


def test_crashing_worker_is_isolated(tmp_path):
    """A worker process that dies must not fail the whole batch (BrokenProcessPool)."""
    from crashy import crashing_worker
    names = ["a", "b CRASH", "c", "d", "e CRASH", "f", "g"]
    for i, n in enumerate(names):
        _write_wav(tmp_path / f"{n}.wav", make_track(110 + i, i, False, seconds=12, seed=i))
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False, workers=3))
    lib.worker_fn = crashing_worker
    res = lib.import_paths([str(tmp_path)])
    assert res["ok"] == 5 and res["errors"] == 2, res
    rows = {r["filename"]: r for r in lib.search(Query())}
    assert all(rows[f"{n}.wav"]["status"] == ("error" if "CRASH" in n else "ok") for n in names)
    assert "crash" in rows["b CRASH.wav"]["error"].lower()
