"""Song sections: detection (librosa spectral clustering), storage, queue jobs and the deck display."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from synth import SR, make_track

from keytool.structure import detect_sections, section_at

PLAN = [("intro", 16), ("verse", 32), ("chorus", 32), ("verse", 32), ("chorus", 32), ("bridge", 16),
        ("chorus", 32), ("intro", 16)]


def _part(kind, sec, seed):
    if kind == "intro":
        return 0.4 * make_track(120, 9, True, seconds=sec, drums=False, seed=seed)
    if kind == "verse":
        return 0.6 * make_track(120, 9, True, seconds=sec, drums=True, seed=seed)
    if kind == "chorus":
        return 1.0 * make_track(120, 0, False, seconds=sec, drums=True, seed=seed)
    return 0.5 * make_track(120, 5, False, seconds=sec, drums=False, seed=seed)


@pytest.fixture(scope="module")
def song():
    y = np.concatenate([_part(k, s, i) for i, (k, s) in enumerate(PLAN)]).astype(np.float32)
    return y, detect_sections(y, SR)


def test_boundaries_and_groups(song):
    _, secs = song
    edges = list(np.cumsum([0] + [s for _, s in PLAN]))
    assert len(secs) == len(PLAN)
    for sec, a, b in zip(secs, edges, edges[1:]):
        assert abs(sec["start"] - a) < 2.0 and abs(sec["end"] - b) < 2.0, (sec, a, b)
    groups = [s["group"] for s in secs]
    assert groups[1] == groups[3] and groups[2] == groups[4] == groups[6]        # repeats recognised
    assert len({groups[1], groups[2], groups[5]}) == 3
    assert groups[0] == "A"                                                       # letters by appearance


def test_functional_labels(song):
    _, secs = song
    assert [s["label"] for s in secs] == ["intro", "verse", "chorus", "verse", "chorus", "bridge", "chorus", "outro"]
    assert section_at(secs, 60.0)["label"] == "chorus" and section_at(secs, 9999) is None


def test_short_or_silent_audio_has_no_sections():
    assert detect_sections(np.zeros(SR * 60, dtype=np.float32), SR) == []
    assert detect_sections(make_track(120, 0, False, seconds=20).astype(np.float32), SR) == []


# --------------------------------------------------------------------------- storage and queue
@pytest.fixture
def lib(tmp_path):
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.library import Library
    from test_core import _write_wav
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    p = tmp_path / "a.wav"
    _write_wav(p, make_track(120, 9, True, seconds=4))
    lib.scan([str(p)])
    return lib


def test_set_analysis_stores_sections(lib):
    from keytool.db import Query
    t = lib.search(Query())[0]
    assert t["sections"] is None
    secs = [dict(start=0.0, end=30.0, group="A", label="intro")]
    lib.db.set_analysis(t["id"], dict(sections=secs, duration=30.0))
    assert json.loads(lib.db.get_track(t["id"])["sections"]) == secs
    lib.db.set_analysis(t["id"], dict(bpm=120.0, bpm_confidence=1.0))       # no "sections": stays
    assert json.loads(lib.db.get_track(t["id"])["sections"]) == secs


def test_submit_args_include_sections(lib):
    from keytool.db import Query
    t = lib.search(Query())[0]
    args = lib.submit_args(t)
    assert args[-1] is True and args[3] and args[4]                          # normal analysis: + sections
    args = lib.submit_args(t, sections_only=True)
    assert args[3:5] == (False, False) and args[-1] is True                  # only the sections
    lib.db.update_track(t["id"], bpm=120, bpm_source="tags", key="9:min", key_source="tags", sections="[]")
    assert lib.submit_args(lib.db.get_track(t["id"])) is None                # nothing left to do


def test_worker_reports_sections_only_when_asked(tmp_path):
    from keytool.library import _analyze_worker
    from test_core import _write_wav
    p = tmp_path / "long.wav"
    y = np.concatenate([_part(k, s, i) for i, (k, s) in enumerate(PLAN[:4])]).astype(np.float32)
    _write_wav(p, y)
    res = _analyze_worker(str(p), 70, 180, False, False, "classic", True)
    assert set(res) == {"duration", "sections"} and len(res["sections"]) >= 3
    assert "sections" not in _analyze_worker(str(p), 70, 180, True, False, "classic", False)


# --------------------------------------------------------------------------- deck display
def test_deck_shows_sections_only_when_available(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.config import Settings
    from test_core import _write_wav
    f = tmp_path / "x.wav"
    _write_wav(f, make_track(120, 9, True, seconds=4))
    p = pl.PlayerPanel(Settings())
    secs = [dict(start=0, end=2, group="A", label="intro"), dict(start=2, end=4, group="B", label="chorus")]
    p.set_track(dict(id=1, path=str(f), title="T", artist="A", key=None, bpm=120.0, sections=json.dumps(secs)))
    assert p.wave.sections == secs
    p.wave.duration = 4.0
    p.wave.peaks = np.ones(100, np.float32); p.wave.colors = np.full((100, 3), 128, np.uint8)
    p.wave.resize(600, 90); p.wave.grab()                                    # paints bands and names
    p.set_track(dict(id=1, path=str(f), title="T", artist="A", key=None, bpm=120.0, sections="[]"))
    assert p.wave.sections == []                                            # same file, re-analysed: none
    p.set_track(dict(id=2, path=str(tmp_path / "missing.wav"), title="U", sections=None))
    assert p.wave.sections == []
    assert pl.parse_sections("not json") == [] and pl.parse_sections(None) == []
    p.deleteLater(); app.processEvents()
