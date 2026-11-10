import os
import time
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
    assert len(lib.search(Query(keywords=["peak", "vocal"]))) == 2                     # default: OR
    assert len(lib.search(Query(keywords=["peak", "vocal"], keywords_mode="all"))) == 1
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


# ------------------------------------------------------------------ existing tags
@pytest.mark.parametrize("text,camelot", [
    ("Am", "8A"), ("A minor", "8A"), ("Amin", "8A"), ("08A", "8A"), ("8A - 6", "8A"), ("8A/Am", "8A"),
    ("1m", "8A"), ("Abm", "1A"), ("G#m", "1A"), ("Gb", "2B"), ("F#", "2B"), ("Fis-Moll", None), ("o", None), ("", None),
])
def test_parse_tag_key(text, camelot):
    from keytool.tags import parse_tag_key
    code = parse_tag_key(text)
    assert (Key.from_code(code).camelot() if code else None) == camelot


@pytest.mark.parametrize("text,bpm", [("128", 128), ("128.00", 128), ("127,5", 127.5), ("174 BPM", 174),
                                      ("0", None), ("999", None), ("", None), ("abc", None)])
def test_parse_tag_bpm(text, bpm):
    from keytool.tags import parse_tag_bpm
    assert parse_tag_bpm(text) == bpm


def _encode(src_wav, target):
    ext = os.path.splitext(str(target))[1]
    codec = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
             ".m4a": ["-c:a", "aac"]}[ext]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src_wav), *codec, str(target)], check=True)


def _set_raw_tags(path, bpm_text, key_text):
    """Write tag values verbatim (as other DJ tools would)."""
    import mutagen
    from mutagen.id3 import ID3, TBPM, TKEY, ID3NoHeaderError
    from mutagen.mp4 import MP4, MP4FreeForm
    ext = os.path.splitext(str(path))[1]
    if ext == ".mp3":
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()
        if bpm_text:
            t.add(TBPM(encoding=3, text=bpm_text))
        if key_text:
            t.add(TKEY(encoding=3, text=key_text))
        t.save(path)
    elif ext == ".m4a":
        f = MP4(path)
        if bpm_text:
            f["tmpo"] = [int(float(bpm_text))]
        if key_text:
            f["----:com.apple.iTunes:initialkey"] = [MP4FreeForm(key_text.encode())]
        f.save()
    else:
        f = mutagen.File(path)
        if bpm_text:
            f["BPM"] = bpm_text
        if key_text:
            f["INITIALKEY"] = key_text
        f.save()


@pytest.fixture
def tagged_dir(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    raw = tmp_path / "raw.wav"
    # audio is really in C major / 120 BPM - tag values deliberately differ, so we can tell
    # whether they were taken over or analysed
    _write_wav(raw, make_track(120, 0, False, seconds=15))
    d = tmp_path / "music"
    d.mkdir()
    files = {
        "full.mp3": ("128", "Am"),
        "mik.flac": ("128.00", "8A - 6"),
        "itunes.m4a": ("128", "Abm"),
        "openkey.ogg": ("140", "1m"),
        "bpm_only.mp3": ("127.5", None),
        "key_only.flac": (None, "F#m"),
        "none.mp3": (None, None),
    }
    for name, (b, k) in files.items():
        _encode(raw, d / name)
        _set_raw_tags(d / name, b, k)
    return d


def _lib(tmp_path, **kw):
    return Library(Database(tmp_path / "lib.sqlite3"), Settings(workers=2, **kw))


def test_existing_tags_are_used(tmp_path, tagged_dir):
    lib = _lib(tmp_path, write_tags=True, tag_key_notation="camelot")
    res = lib.import_paths([str(tagged_dir)])
    assert res["from_tags"] == 4 and res["total"] == 3 and res["errors"] == 0, res
    rows = {r["filename"]: r for r in lib.search(Query())}
    k = lambda f: Key.from_code(rows[f]["key"]).camelot()
    # full tags: taken over unchanged, never analysed
    for f, bpm, key in (("full.mp3", 128, "8A"), ("mik.flac", 128, "8A"), ("itunes.m4a", 128, "1A"), ("openkey.ogg", 140, "8A")):
        r = rows[f]
        assert (r["bpm"], k(f), r["status"]) == (bpm, key, "ok"), f
        assert r["bpm_source"] == r["key_source"] == "tags" and r["analyzed_at"] is None, f
    # partial tags: the present value is kept, the missing one is analysed
    assert rows["bpm_only.mp3"]["bpm"] == 127.5 and rows["bpm_only.mp3"]["bpm_source"] == "tags"
    assert k("bpm_only.mp3") == "8B" and rows["bpm_only.mp3"]["key_source"] == "analysis"
    assert k("key_only.flac") == "11A" and rows["key_only.flac"]["key_source"] == "tags"
    assert abs(rows["key_only.flac"]["bpm"] - 120) < 1 and rows["key_only.flac"]["bpm_source"] == "analysis"
    assert abs(rows["none.mp3"]["bpm"] - 120) < 1 and k("none.mp3") == "8B"
    # tag values from other tools are not rewritten in the files, only the missing value is added
    md = read_metadata(str(tagged_dir / "bpm_only.mp3"))
    assert md.tag_bpm == "127.5" and md.tag_key == "8B"
    assert read_metadata(str(tagged_dir / "full.mp3")).tag_key == "Am"
    assert read_metadata(str(tagged_dir / "key_only.flac")).tag_key == "F#m"
    # a second import changes nothing
    assert lib.scan([str(tagged_dir)]).unchanged == 7


def test_reanalyze_overrides_tag_values(tmp_path, tagged_dir):
    lib = _lib(tmp_path, write_tags=False)
    lib.import_paths([str(tagged_dir)])
    tid = lib.search(Query(text="full"))[0]["id"]
    lib.analyze([tid], keep_tag_values=False)
    r = lib.db.get_track(tid)
    assert abs(r["bpm"] - 120) < 1 and Key.from_code(r["key"]).camelot() == "8B"
    assert r["bpm_source"] == r["key_source"] == "analysis"


def test_manual_values_beat_tags(tmp_path, tagged_dir):
    lib = _lib(tmp_path, write_tags=False)
    lib.import_paths([str(tagged_dir)])
    tid = lib.search(Query(text="full"))[0]["id"]
    lib.set_bpm(tid, 64.0)
    os.utime(lib.db.get_track(tid)["path"])  # file "changed" -> rescan reads tags again
    lib.scan([str(tagged_dir)])
    r = lib.db.get_track(tid)
    assert r["bpm"] == 64.0 and r["bpm_source"] == "manual" and Key.from_code(r["key"]).camelot() == "8A"


def test_use_existing_tags_off(tmp_path, tagged_dir):
    lib = _lib(tmp_path, write_tags=False, use_existing_tags=False)
    res = lib.import_paths([str(tagged_dir)])
    assert res["from_tags"] == 0 and res["ok"] == 7
    assert all(abs(r["bpm"] - 120) < 1 for r in lib.search(Query()))


def test_db_migration_adds_columns(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite3"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT UNIQUE, folder TEXT, filename TEXT, ext TEXT, "
              "size INTEGER, mtime REAL, title TEXT, artist TEXT, album TEXT, year INTEGER, genre TEXT, duration REAL, "
              "bpm REAL, bpm_confidence REAL, key TEXT, key_confidence REAL, bpm_manual INTEGER, key_manual INTEGER, "
              "tag_bpm TEXT, tag_key TEXT, status TEXT, error TEXT, added_at REAL, analyzed_at REAL)")
    c.commit(); c.close()
    db = Database(p)
    cols = {r[1] for r in db.conn().execute("PRAGMA table_info(tracks)")}
    assert {"bpm_source", "key_source"} <= cols


# ------------------------------------------------------------------ waveform
def test_waveform_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    from keytool.waveform import get_waveform
    y = make_track(120, 0, False, seconds=20)
    y[: SR * 5] = 0                       # 5 s silence at the start
    f = tmp_path / "w.wav"
    _write_wav(f, y)
    wf = get_waveform(str(f))
    assert len(wf.peaks) == len(wf.colors) == 1600 and abs(wf.duration - 20) < 0.1
    assert wf.peaks.max() <= 1.0 and wf.peaks[:300].max() < 0.01 and wf.peaks[500:].mean() > 0.2
    assert wf.colors.dtype == np.uint8 and wf.colors.shape[1] == 3
    cached = list((tmp_path / "data" / "waveforms").glob("*.npz"))
    assert len(cached) == 1
    wf2 = get_waveform(str(f))            # from cache
    assert np.array_equal(wf.peaks, wf2.peaks)


def test_waveform_band_colours(tmp_path, monkeypatch):
    """Bass-heavy audio is drawn bluer than treble-heavy audio."""
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    from keytool.waveform import compute_waveform
    t = np.arange(SR * 6) / SR
    for name, freq in (("low", 60.0), ("high", 6000.0)):
        _write_wav(tmp_path / f"{name}.wav", (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32))
    low = compute_waveform(str(tmp_path / "low.wav")).colors.mean(axis=0)
    high = compute_waveform(str(tmp_path / "high.wav")).colors.mean(axis=0)
    assert low[2] > low[0]                # blue dominates for bass
    assert high[0] > low[0] + 100         # treble is much brighter in red/green


# ------------------------------------------------------------------ Karaoke-Version research
FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fix(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


def test_kv_search_url():
    from urllib.parse import parse_qs, urlparse
    from keytool.research import search_url
    am, c, fsharp, ebm = Key.parse("Am"), Key.parse("C"), Key.parse("F#"), Key.parse("Ebm")
    q = parse_qs(urlparse(search_url([am, c, fsharp, ebm], 122.4, 127.6, page=3)).query)
    assert q["chordkey"] == ["22,1,7,107,116,16"] and q["tempo"] == ["122,128"] and q["page"] == ["3"]
    assert search_url([], 80, 100).endswith("karaoke.html?tempo=80%2C100")
    assert "page" not in search_url([am])


def test_kv_parse_list():
    from keytool.research import parse_list
    total, songs = parse_list(_fix("kv_list.html"))
    assert total == 450 and len(songs) == 3
    s = songs[0]
    assert (s.title, s.artist, s.year, s.price) == ("Losing My Religion", "R.E.M.", 1991, "2,99 €")
    assert s.url == "https://www.karaoke-version.de/custombackingtrack/r-e-m/losing-my-religion.html"
    assert s.song_id == "10592-1" and s.image.startswith("https://")
    assert [x.artist for x in songs[1:]] == ["Andreas Gabalier", "Roland Kaiser"]


@pytest.mark.parametrize("name,bpm,variable,camelot", [
    ("fixed", 126, False, ["8A"]),
    ("variable", 130, True, ["8B", "3A"]),            # 'C, Bm' - German Bm = b-Moll (Bb minor)
    ("notempo", None, False, ["2B", "9B"]),           # 'Fis, G'
    ("many", 119, True, ["5A", "12A", "7A", "2A", "10B", "1A", "3A", "10A", "9A"]),  # Esm and Dism = same key
])
def test_kv_parse_detail(name, bpm, variable, camelot):
    from keytool.research import parse_detail
    d = parse_detail(_fix(f"kv_detail_{name}.html"))
    assert d.bpm == bpm and d.bpm_variable == variable and d.duration == 267
    assert [Key.from_code(c).camelot() for c in d.keys] == camelot


@pytest.mark.parametrize("tok,camelot", [("H", "1B"), ("B", "6B"), ("Hm", "10A"), ("Bm", "3A"), ("Fis", "2B"),
                                         ("Esm", "2A"), ("As", "4B"), ("Cism", "12A"), ("Des", "3B"), ("C#m", "12A")])
def test_german_key_names(tok, camelot):
    from keytool.research import parse_german_key
    assert parse_german_key(tok).camelot() == camelot


def test_kv_client_search_and_details(tmp_path):
    """Per-key searches are merged, keys attached, details parsed and cached, requests counted."""
    from keytool.research import KaraokeVersionClient
    import keytool.research as r
    r.MIN_INTERVAL = 0
    calls = []

    def fetch(url):
        calls.append(url)
        return _fix("kv_detail_variable.html") if "/r-e-m/" in url else _fix("kv_list.html")

    cl = KaraokeVersionClient(cache_file=tmp_path / "c.json", fetcher=fetch)
    page = cl.search([Key.parse("Am"), Key.parse("C")], 122, 128)
    assert len(calls) == 2 and "chordkey=22" in calls[0] and "chordkey=1&" in calls[1]
    assert page.total == 900 and page.has_more and len(page.songs) == 3   # same songs merged
    assert sorted(Key.from_code(k).camelot() for k in page.songs[0].keys) == ["8A", "8B"]
    cl.load_details(page.songs[0])
    assert page.songs[0].bpm == 130 and page.songs[0].bpm_variable and "3A" in [Key.from_code(k).camelot() for k in page.songs[0].keys]
    n = len(calls)
    cl2 = KaraokeVersionClient(cache_file=tmp_path / "c.json", fetcher=fetch)   # cache from disk
    p2 = cl2.search([Key.parse("Am")], 122, 128)
    assert p2.songs[0].details_loaded and p2.songs[0].bpm == 130 and len(calls) == n + 1


def test_kv_library_match():
    from keytool.research import KVSong, in_library, library_index
    idx = library_index([{"title": "Losing My Religion (Remastered)", "artist": "R.E.M."}])
    assert in_library(KVSong("Losing My Religion", "R.E.M.", 1991, "u"), idx)
    assert not in_library(KVSong("Hulapalu", "Andreas Gabalier", 2015, "u"), idx)


# ------------------------------------------------------------------ editable metadata
@pytest.mark.parametrize("ext", ["mp3", "wav", "flac", "ogg", "m4a"])
def test_edit_metadata_roundtrip(tmp_path, ext):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    raw = tmp_path / "raw.wav"
    _write_wav(raw, make_track(120, 0, False, seconds=8))
    f = tmp_path / f"t.{ext}"
    if ext == "wav":
        shutil.copy(raw, f)
    else:
        _encode(raw, f)
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False, workers=1))
    lib.scan([str(f)])
    t = lib.search(Query())[0]
    assert t["bitrate"] and t["bitrate"] > 0 and t["size"] > 0
    errs = lib.update_metadata([t["id"]], dict(title="Neu", artist="Künstlerin", album="Platte", genre="Deep House",
                                               comment="Zeile 1\nZeile 2", energy=7, year=1999))
    assert errs == {}
    md = read_metadata(str(f))
    assert (md.title, md.artist, md.album, md.genre, md.energy) == ("Neu", "Künstlerin", "Platte", "Deep House", 7)
    assert md.year == 1999 and lib.db.get_track(t["id"])["year"] == 1999
    lib.update_metadata([t["id"]], dict(album_artist="Various Artists"))
    assert read_metadata(str(f)).album_artist == "Various Artists"
    assert lib.db.get_track(t["id"])["album_artist"] == "Various Artists"
    assert read_metadata(str(f)).artist == "Künstlerin"              # track artist untouched
    lib.update_metadata([t["id"]], dict(album_artist=""))
    assert read_metadata(str(f)).album_artist == ""
    lib.update_metadata([t["id"]], dict(year="2004"))           # text input from the detail field
    assert read_metadata(str(f)).year == 2004
    lib.update_metadata([t["id"]], dict(year=None))             # empty field removes the year
    assert read_metadata(str(f)).year is None and lib.db.get_track(t["id"])["year"] is None
    assert md.comment.replace("\r", "") == "Zeile 1\nZeile 2"
    r = lib.db.get_track(t["id"])
    assert (r["title"], r["energy"], r["comment"]) == ("Neu", 7, "Zeile 1\nZeile 2")
    # clearing energy/comment, rescan keeps values consistent with the file
    lib.update_metadata([t["id"]], dict(energy=None, comment=""))
    assert read_metadata(str(f)).energy is None and read_metadata(str(f)).comment == ""
    assert lib.scan([str(f)]).unchanged == 0 or lib.db.get_track(t["id"])["title"] == "Neu"


def test_edit_metadata_db_only_and_search(tmp_path):
    raw = tmp_path / "x.wav"
    _write_wav(raw, make_track(120, 0, False, seconds=6))
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_edits=False))
    lib.scan([str(raw)])
    tid = lib.search(Query())[0]["id"]
    before = os.stat(raw).st_mtime
    lib.update_metadata([tid], dict(comment="Opener fürs Set", energy=11))   # 11 is out of range -> cleared
    assert os.stat(raw).st_mtime == before
    assert lib.db.get_track(tid)["energy"] is None
    assert len(lib.search(Query(text="opener"))) == 1


def test_energy_from_mixed_in_key_comment(tmp_path):
    from mutagen.id3 import ID3, COMM
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    raw = tmp_path / "r.wav"
    _write_wav(raw, make_track(120, 0, False, seconds=5))
    f = tmp_path / "mik.mp3"
    _encode(raw, f)
    t = ID3(f); t.add(COMM(encoding=3, lang="eng", desc="", text="8A - Energy 6")); t.save(f)
    assert read_metadata(str(f)).energy == 6


# ------------------------------------------------------------------ cover artwork
def _png_bytes(color=(200, 80, 40)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "PNG")
    return buf.getvalue()


def _embed_cover(path, data):
    import base64
    import mutagen
    from mutagen.flac import Picture
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError
    ext = os.path.splitext(str(path))[1]
    if ext in (".mp3", ".wav"):
        if ext == ".mp3":
            try:
                t = ID3(path)
            except ID3NoHeaderError:
                t = ID3()
            t.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=data)); t.save(path)
        else:
            from mutagen.wave import WAVE
            f = WAVE(path)
            if f.tags is None:
                f.add_tags()
            f.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=data)); f.save()
    elif ext == ".m4a":
        from mutagen.mp4 import MP4, MP4Cover
        f = MP4(path); f["covr"] = [MP4Cover(data, MP4Cover.FORMAT_PNG)]; f.save()
    else:
        pic = Picture(); pic.type = 3; pic.mime = "image/png"; pic.data = data
        f = mutagen.File(path)
        if ext == ".flac":
            f.add_picture(pic)
        else:
            f["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
        f.save()


@pytest.mark.parametrize("ext", ["mp3", "wav", "flac", "ogg", "m4a"])
def test_embedded_cover(tmp_path, ext):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    from keytool.tags import read_cover
    raw = tmp_path / "raw.wav"
    _write_wav(raw, make_track(120, 0, False, seconds=4))
    f = tmp_path / f"t.{ext}"
    shutil.copy(raw, f) if ext == "wav" else _encode(raw, f)
    assert read_cover(str(f)) is None
    data = _png_bytes()
    _embed_cover(f, data)
    assert read_cover(str(f)) == data


def test_folder_cover(tmp_path):
    from keytool.tags import read_cover
    f = tmp_path / "song.wav"
    _write_wav(f, make_track(120, 0, False, seconds=3))
    (tmp_path / "Folder.JPG").write_bytes(b"\xff\xd8fakejpeg")
    assert read_cover(str(f)) == b"\xff\xd8fakejpeg"


# ------------------------------------------------------------------ Serato cue markers
def test_serato_real_bytes():
    """Byte layouts taken from real Serato DJ Pro tags (serato-tags write-up)."""
    import base64
    from keytool.serato import SeratoMarkers, dump_entries, parse_entries, parse_tag_data
    # an analysed file: COLOR #ffffff + BPMLOCK off -> Serato's real base64 starts like this
    real_prefix = "AQFDT0xPUgAAAAAEAP///0JQTUxPQ0"
    assert base64.b64encode(dump_entries(SeratoMarkers(track_color="#ffffff", bpm_locked=False))).decode().startswith(real_prefix)
    m = parse_tag_data(b"\x01\x01" + base64.b64encode(dump_entries(SeratoMarkers(track_color="#ffffff",
                                                                                  bpm_locked=False))) + b"\x00" * 400)
    assert m.track_color == "#ffffff" and m.bpm_locked is False and not m.cues
    # real hot cue 2 at 218456 ms, orange (#cc8800 stored, #f8821a shown by Serato DJ Pro)
    entry = bytes.fromhex("43 55 45 00 00 00 00 0d 00 01 00 03 55 58 00 cc 88 00 00 00 00")
    c = parse_entries(b"\x01\x01" + entry + b"\x00").cues[0]
    assert (c.index, c.position_ms, c.color, c.display_color, c.name) == (1, 218456, "#cc8800", "#f8821a", "")
    named = bytes.fromhex("43 55 45 00 00 00 00 1a 00 00 00 00 00 00 00 cc 00 00 00 00") + "Hello, World".encode() + b"\x00"
    assert parse_entries(b"\x01\x01" + named + b"\x00").cues[0].name == "Hello, World"


def test_serato_lenient_base64_and_linefeeds():
    import base64
    from keytool.serato import Cue, SeratoMarkers, dump_tag_data, parse_tag_data
    m = SeratoMarkers(cues=[Cue(i, 1000 * i, "#cc0000", f"Cue Nummer {i} äöü") for i in range(8)])
    data = dump_tag_data(m)
    assert b"\n" in data and len(data) >= 470                  # linefeeds every 72 chars, NUL padding
    back = parse_tag_data(data)
    assert [c.name for c in back.cues] == [c.name for c in m.cues]
    # Serato quirk: base64 one character too long
    raw = data[2:].split(b"\x00", 1)[0].replace(b"\n", b"")
    if len(raw) % 4 != 1:
        raw = raw.rstrip(b"=")
        raw = raw + b"A" * ((1 - len(raw)) % 4)
    assert parse_tag_data(b"\x01\x01" + raw + b"\x00").cues[0].name == "Cue Nummer 0 äöü"


def _write_serato(path, m):
    import mutagen
    from keytool.serato import dump_tag_data, dump_vorbis_value
    from mutagen.id3 import GEOB, ID3, ID3NoHeaderError
    ext = os.path.splitext(str(path))[1]
    if ext == ".mp3":
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()
        t.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2", data=dump_tag_data(m)))
        t.save(path)
    elif ext == ".wav":
        from mutagen.wave import WAVE
        f = WAVE(path)
        if f.tags is None:
            f.add_tags()
        f.tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2", data=dump_tag_data(m)))
        f.save()
    elif ext == ".m4a":
        from mutagen.mp4 import MP4, MP4FreeForm
        f = MP4(path)
        f["----:com.serato.dj:markersv2"] = [MP4FreeForm(dump_vorbis_value(m).encode())]
        f.save()
    else:
        f = mutagen.File(path)
        f["SERATO_MARKERS_V2"] = dump_vorbis_value(m)
        f.save()


@pytest.mark.parametrize("ext", ["mp3", "wav", "flac", "m4a"])
def test_serato_markers_in_files(tmp_path, ext):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    from keytool.serato import Cue, Loop, SeratoMarkers, read_serato_markers
    raw = tmp_path / "raw.wav"
    _write_wav(raw, make_track(120, 0, False, seconds=6))
    f = tmp_path / f"t.{ext}"
    shutil.copy(raw, f) if ext == "wav" else _encode(raw, f)
    assert not read_serato_markers(str(f))
    m = SeratoMarkers(cues=[Cue(0, 500, "#cc0000", "Intro"), Cue(3, 4200, "#0000cc", "Drop")],
                      loops=[Loop(0, 1000, 3000, "#27aae1", True, "Loop A")], track_color="#99ff99", bpm_locked=True)
    _write_serato(f, m)
    r = read_serato_markers(str(f))
    assert [(c.index, c.position_ms, c.color, c.name) for c in r.cues] == [(0, 500, "#cc0000", "Intro"), (3, 4200, "#0000cc", "Drop")]
    assert [(l.start_ms, l.end_ms, l.color, l.locked, l.name) for l in r.loops] == [(1000, 3000, "#27aae1", True, "Loop A")]
    assert r.track_color == "#99ff99" and r.bpm_locked is True
    # our own tag writing must not destroy Serato's data
    from keytool.tags import write_metadata
    write_metadata(str(f), title="Neu", bpm=120, key_text="8B", energy=5, comment="x")
    assert len(read_serato_markers(str(f)).cues) == 2


def test_serato_garbage_is_ignored(tmp_path):
    from mutagen.id3 import GEOB, ID3
    from keytool.serato import read_serato_markers
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    raw = tmp_path / "raw.wav"
    _write_wav(raw, make_track(120, 0, False, seconds=3))
    f = tmp_path / "g.mp3"
    _encode(raw, f)
    t = ID3(f); t.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2", data=b"\x02\x05junk")); t.save(f)
    assert not read_serato_markers(str(f))


# ------------------------------------------------------------------ analyse only what is missing
def test_analyze_file_parts(tmp_path):
    import time
    from keytool.analysis import analyze_file
    f = tmp_path / "a.wav"
    _write_wav(f, make_track(126, 9, True, seconds=30))
    t0 = time.time(); full = analyze_file(str(f)); t_full = time.time() - t0
    t0 = time.time(); bpm_only = analyze_file(str(f), do_key=False); t_bpm = time.time() - t0
    assert bpm_only.key is None and abs(bpm_only.bpm - 126) < 1 and full.key == Key.parse("Am").code
    assert t_bpm < t_full                         # key detection skipped
    key_only = analyze_file(str(f), do_bpm=False)
    assert key_only.bpm is None and key_only.key == Key.parse("Am").code


def test_import_analyses_only_missing_values(tmp_path, tagged_dir, monkeypatch):
    log = tmp_path / "jobs.log"
    monkeypatch.setenv("KEYTOOL_TEST_LOG", str(log))
    from crashy import recording_worker
    lib = _lib(tmp_path, write_tags=False)
    lib.worker_fn = recording_worker
    lib.import_paths([str(tagged_dir)])
    jobs = {l.split("|")[0]: (l.split("|")[1], l.split("|")[2]) for l in log.read_text().split()}
    assert jobs == {"bpm_only.mp3": ("0", "1"),      # BPM in tags -> key only
                    "key_only.flac": ("1", "0"),     # key in tags -> BPM only
                    "none.mp3": ("1", "1")}          # nothing in tags -> both; fully tagged files: no job
    rows = {r["filename"]: r for r in lib.search(Query())}
    k = rows["key_only.flac"]
    assert Key.from_code(k["key"]).camelot() == "11A" and k["key_source"] == "tags"
    assert abs(k["bpm"] - 120) < 1 and k["bpm_source"] == "analysis" and k["status"] == "ok"
    # explicit re-analysis still analyses everything
    log.write_text("")
    lib.analyze([k["id"]], keep_tag_values=False)
    assert log.read_text().strip() == "key_only.flac|1|1"


# ------------------------------------------------------------------ transposition (+-1 semitone, original tempo)
@pytest.mark.parametrize("st,expected", [(1, "A#m"), (-1, "G#m")])
def test_transpose_render(tmp_path, monkeypatch, st, expected):
    pytest.importorskip("pedalboard")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    from keytool.transpose import cached, render
    from keytool.analysis import analyze_file
    from keytool.tags import write_metadata
    import soundfile as sf
    raw = tmp_path / "raw.wav"
    _write_wav(raw, make_track(124, 9, True, seconds=70))          # A minor, 124 BPM, > 2 chunks
    src = tmp_path / "song.mp3"
    _encode(raw, src)
    out = render(str(src), st)
    r, o = analyze_file(out), analyze_file(str(src))
    assert Key.from_code(r.key).sharps() == expected               # pitch moved by one semitone
    assert abs(r.bpm - o.bpm) < 0.5 and abs(r.duration - o.duration) < 0.05   # tempo and length unchanged
    d, sr = sf.read(out)
    assert d.shape[1] == 2
    jumps = np.abs(np.diff(d[:, 0]))
    b = int(30 * sr)
    assert jumps[b - 300:b + 300].max() <= np.percentile(jumps, 99.95) + 1e-6   # no click at the chunk boundary
    # editing tags does not invalidate the cached version, a different transposition is separate
    write_metadata(str(src), title="Neu", bpm=124, key_text="8A")
    assert cached(str(src), st) == out
    assert cached(str(src), -st) is None


# ------------------------------------------------------------------ fast key detection + comparison tool
@pytest.mark.parametrize("pc,minor,a4,drums", [
    (9, True, 440, True), (2, True, 432, True), (7, False, 446, False), (0, False, 440, False),
    (4, True, 446, True), (10, False, 432, True), (1, True, 440, False), (6, True, 432, False),
])
def test_fast_key_method(pc, minor, a4, drums):
    from keytool.analysis import detect_key
    y = make_track(118, pc, minor, seconds=30, a4=a4, drums=drums, seed=pc)
    assert detect_key(y, method="fast")[0] == Key(pc, minor).code


def test_fast_key_method_long_and_silent():
    from keytool.analysis import detect_key
    y = np.tile(make_track(124, 5, False, seconds=60), 4)          # 4 min -> several 90 s chunks
    code, conf = detect_key(y, method="fast")
    assert code == Key(5, False).code and 0 < conf <= 1
    assert detect_key(np.zeros(SR * 10, dtype=np.float32), method="fast") == (None, 0.0)


def test_key_method_setting_reaches_the_worker(tmp_path, monkeypatch):
    log = tmp_path / "jobs.log"
    monkeypatch.setenv("KEYTOOL_TEST_LOG", str(log))
    from crashy import recording_worker
    f = tmp_path / "x.wav"
    _write_wav(f, make_track(120, 9, True, seconds=12))
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False, workers=1, key_method="fast"))
    lib.worker_fn = recording_worker
    lib.import_paths([str(tmp_path)])
    r = lib.search(Query())[0]
    assert r["key"] == Key(9, True).code and r["status"] == "ok"


def test_compare_tool(tmp_path):
    from keytool.compare import relation, run
    assert relation(Key.parse("8A").code, Key.parse("8B").code).startswith("relative")
    assert relation(Key.parse("8A").code, Key.parse("9A").code).startswith("fifth")
    assert relation(Key.parse("Am").code, Key.parse("A").code).startswith("same tonic")
    for i, (pc, mn) in enumerate(((9, True), (0, False), (7, True))):
        _write_wav(tmp_path / f"t{i}.wav", make_track(120, pc, mn, seconds=20, seed=i))
    lines = []
    res = run([str(tmp_path)], limit=2, workers=2, out=lines.append)
    assert res["n"] == 2 and res["same"] == 2 and res["recommend_fast"]
    assert any("faster" in l for l in lines)


def test_set_key_method_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "keytool", "--set-key-method", "fast"], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(__file__)), env={**os.environ, "KEYTOOL_DATA_DIR": str(tmp_path)})
    assert r.returncode == 0 and Settings.load().key_method == "fast"
    r = subprocess.run([sys.executable, "-m", "keytool", "--set-key-method", "turbo"], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(__file__)), env={**os.environ, "KEYTOOL_DATA_DIR": str(tmp_path)})
    assert r.returncode == 2


# ------------------------------------------------------------------ drag tracks to other applications
def test_track_list_drag_mime(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from keytool.gui.widgets import TrackModel
    app = QApplication.instance() or QApplication([])
    files = []
    for n in ("a b.wav", "c.wav"):
        f = tmp_path / n
        _write_wav(f, make_track(120, 0, False, seconds=1))
        files.append(str(f))
    m = TrackModel()
    m.set_rows([{"id": i, "path": p} for i, p in enumerate(files)] + [{"id": 9, "path": str(tmp_path / "gone.mp3")}])
    assert m.flags(m.index(0, 0)) & Qt.ItemIsDragEnabled
    md = m.mimeData([m.index(r, c) for r in range(3) for c in (0, 1)])   # several columns per row
    # one entry per row, missing file skipped (Qt returns "/" separators on Windows too)
    assert [os.path.normpath(u.toLocalFile()) for u in md.urls()] == [os.path.normpath(f) for f in files]
    assert md.text().splitlines() == files
    assert m.supportedDragActions() == Qt.CopyAction


# ------------------------------------------------------------------ analysis queue
def _queue_lib(tmp_path, names, workers=1):
    from keytool.library import Library
    for i, n in enumerate(names):
        _write_wav(tmp_path / f"{n}.wav", make_track(110 + i, i % 12, False, seconds=6, seed=i))
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False, workers=workers))
    lib.scan([str(tmp_path)])
    ids = {r["filename"][:-4]: r["id"] for r in lib.search(Query())}
    return lib, ids


def _log_order(log):
    return [l.split("|")[0][:-4] for l in log.read_text().split()] if log.exists() else []


def test_queue_order_and_analyse_now(tmp_path, monkeypatch):
    from crashy import slow_recording_worker
    from keytool.analysis_queue import AnalysisQueue, DONE
    log = tmp_path / "order.log"
    monkeypatch.setenv("KEYTOOL_TEST_LOG", str(log))
    names = ["a", "b", "c", "d", "e"]
    lib, ids = _queue_lib(tmp_path, names, workers=1)
    lib.worker_fn = slow_recording_worker
    q = AnalysisQueue(lib)
    q.add([ids[n] for n in names], keep_tag_values=False)
    time.sleep(0.2)                                   # 'a' is running now
    q.move_to_front([ids["e"], ids["d"]])             # "analyse now": e, then d go to the top
    q.remove([ids["b"]])
    assert q.wait_idle(120)
    assert _log_order(log) == ["a", "e", "d", "c"]    # strictly top to bottom, b removed
    st = {i.track_id: i.state for i in q.items()}
    assert all(st[ids[n]] == DONE for n in "aecd") and ids["b"] not in st
    assert lib.db.get_track(ids["c"])["status"] == "ok" and lib.db.get_track(ids["c"])["bpm"]
    # re-adding a finished track queues it again; adding a waiting one twice does not duplicate it
    assert q.add([ids["a"], ids["a"]], keep_tag_values=False) == 1
    assert q.wait_idle(60) and _log_order(log)[-1] == "a"
    q.shutdown()


def test_queue_parallel_starts_in_order(tmp_path, monkeypatch):
    from crashy import slow_recording_worker
    from keytool.analysis_queue import AnalysisQueue
    log = tmp_path / "order.log"
    monkeypatch.setenv("KEYTOOL_TEST_LOG", str(log))
    names = ["p1", "p2", "p3", "p4"]
    lib, ids = _queue_lib(tmp_path, names, workers=2)
    lib.worker_fn = slow_recording_worker
    q = AnalysisQueue(lib)
    q.add([ids[n] for n in names])
    assert q.wait_idle(120)
    assert sorted(_log_order(log)) == names                         # all four analysed
    # the queue hands tracks to the pool top-down: p1, p2 first, p3/p4 only when a slot frees up.
    # Compare when the queue started them – the order in which workers *finish* depends on process
    # start-up times (slow on Windows) and says nothing about the queue.
    started = {i.track_id: i.started for i in q.items()}
    first, later = [started[ids[n]] for n in ("p1", "p2")], [started[ids[n]] for n in ("p3", "p4")]
    assert max(first) <= min(later)
    q.shutdown()


def test_queue_survives_crashing_worker(tmp_path, monkeypatch):
    from crashy import crashing_recording_worker
    from keytool.analysis_queue import AnalysisQueue, DONE, ERROR
    monkeypatch.setenv("KEYTOOL_TEST_LOG", str(tmp_path / "log"))
    names = ["ok1", "x CRASH", "ok2", "ok3"]
    lib, ids = _queue_lib(tmp_path, names, workers=2)
    lib.worker_fn = crashing_recording_worker
    events = []
    q = AnalysisQueue(lib, on_change=lambda ev, it: events.append(ev))
    q.add([ids[n] for n in names], keep_tag_values=False)
    assert q.wait_idle(180)
    st = {i.track_id: i for i in q.items()}
    assert st[ids["x CRASH"]].state == ERROR and "crash" in st[ids["x CRASH"]].error.lower()
    assert all(st[ids[n]].state == DONE for n in ("ok1", "ok2", "ok3"))
    assert lib.db.get_track(ids["x CRASH"])["status"] == "error"
    assert "started" in events and "finished" in events
    q.shutdown()


# ------------------------------------------------------------------ diagnostics (windowed exe has no console)
def test_analysis_log_records_crashes_and_errors(tmp_path, monkeypatch):
    """A native crash and a Python error in a worker both end up in analysis.log with their cause."""
    from crashy import raising_worker, segfault_worker
    from keytool.analysis_queue import AnalysisQueue, ERROR
    data = tmp_path / "data"
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(data))          # inherited by the spawned workers
    lib, ids = _queue_lib(tmp_path, ["ok1", "SEGV", "BADFILE"], workers=1)
    log = data / "analysis.log"

    lib.worker_fn = segfault_worker
    q = AnalysisQueue(lib)
    q.add([ids["SEGV"]], keep_tag_values=False)
    assert q.wait_idle(120)
    q.shutdown()
    text = log.read_text(encoding="utf-8")
    # faulthandler dump: "Fatal Python error: Segmentation fault" (POSIX) or
    # "Windows fatal exception: access violation" (Windows)
    assert any(s in text for s in ("Fatal Python error", "Segmentation fault", "Windows fatal exception",
                                   "access violation"))
    assert "analysis process died; exit codes:" in text and "SEGV.wav" in text
    assert "analysis.log" in lib.db.get_track(ids["SEGV"])["error"]

    lib.worker_fn = raising_worker
    q = AnalysisQueue(lib)
    q.add([ids["BADFILE"], ids["ok1"]], keep_tag_values=False)
    assert q.wait_idle(120)
    st = {i.track_id: i.state for i in q.items()}
    q.shutdown()
    assert st[ids["BADFILE"]] == ERROR and lib.db.get_track(ids["ok1"])["status"] == "ok"
    text = log.read_text(encoding="utf-8")
    assert "analysis failed:" in text and "BADFILE.wav" in text and "simulated decode error" in text


def test_album_artist_lazy_fill_for_old_libraries(tmp_path):
    """Tracks scanned before album-artist support (NULL) get it from the file on first view, without rescan."""
    from mutagen.id3 import ID3, TPE2
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    raw = tmp_path / "r.wav"
    _write_wav(raw, make_track(120, 0, False, seconds=5))
    f = tmp_path / "comp.mp3"
    _encode(raw, f)
    t = ID3(f); t.add(TPE2(encoding=3, text="Various Artists")); t.save(f)
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    lib.scan([str(f)])
    tid = lib.search(Query())[0]["id"]
    assert lib.db.get_track(tid)["album_artist"] == "Various Artists"      # new scans read it directly
    lib.db.update_track(tid, album_artist=None)                            # simulate an old library
    rows = [lib.db.get_track(tid)]
    assert lib.fill_album_artist(rows) and rows[0]["album_artist"] == "Various Artists"
    assert lib.db.get_track(tid)["album_artist"] == "Various Artists"
    assert not lib.fill_album_artist(rows)                                 # only once


# ------------------------------------------------------------------ path letter case (Windows)
def test_paths_keep_case_but_match_case_insensitively(tmp_path, monkeypatch):
    """Windows: the library shows paths and file names with their real letter case, but finds a track
    whatever the case of the lookup path (incl. non-ASCII letters). Old libraries that stored lower-case
    paths get their casing back on the next scan, without re-analysis."""
    import sqlite3
    import keytool.db as dbmod
    monkeypatch.setattr(dbmod, "CASE_INSENSITIVE_PATHS", True)
    folder = tmp_path / "Ärzte Live"
    folder.mkdir()
    f = folder / "Westerland CRASH.wav"
    _write_wav(f, make_track(120, 0, False, seconds=3))
    db = Database(tmp_path / "lib.sqlite3")
    lib = Library(db, Settings(write_tags=False))
    lib.scan([str(f)])
    t = lib.search(Query())[0]
    assert t["filename"] == "Westerland CRASH.wav" and t["path"] == str(f)          # original case kept
    assert db.get_track_by_path(str(f).replace("Ärzte", "äRZTE").upper())["id"] == t["id"]
    assert [r["id"] for r in lib.search(Query(folder=str(folder).lower()))] == [t["id"]]
    assert db.upsert_file(str(f).lower(), 1, 0, {}) == t["id"]                      # no duplicate row
    assert len(lib.search(Query())) == 1

    # library written by an older version: path stored in lower case, no path_key column yet
    old = tmp_path / "old.sqlite3"
    con = sqlite3.connect(old)
    con.executescript(dbmod.SCHEMA.replace("    path_key        TEXT,\n", ""))
    con.execute("INSERT INTO tracks (path, folder, filename, ext, size, mtime, status, bpm, key) "
                "VALUES (?, ?, ?, 'wav', ?, ?, 'ok', 120, '9:min')",
                (str(f).casefold(), str(folder).casefold(), f.name.casefold(), os.stat(f).st_size, os.stat(f).st_mtime))
    con.commit(); con.close()
    lib2 = Library(Database(old), Settings(write_tags=False))                        # migration adds path_key
    rep = lib2.scan([str(f)])
    assert rep.unchanged == 1 and not rep.to_analyze                                  # nothing re-analysed
    t2 = lib2.search(Query())[0]
    assert t2["path"] == str(f) and t2["filename"] == "Westerland CRASH.wav"          # casing restored
