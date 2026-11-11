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


def test_confidence_per_section(song):
    _, secs = song
    assert all(0.0 <= s["conf"] <= 1.0 for s in secs)
    assert min(s["conf"] for s in secs if s["label"] == "chorus") >= 0.8         # clear repeats: sure


# EDM-style track that starts 0.9 s late (lead-in): 16 bars intro, 8 bars build-up (rising), 16 bars drop,
# 16 bars break, build-up, drop, outro (= intro material). Boundaries must land on the bar grid.
BAR = 4 * 60 / 128
EDM = [("intro", 16), ("build", 8), ("drop", 16), ("break", 16), ("build", 8), ("drop", 16), ("intro", 16)]
LEAD_IN = 0.9


def _edm_part(kind, bars, seed):
    sec = bars * BAR
    if kind == "intro":
        return 0.4 * make_track(128, 9, True, seconds=sec, drums=False, seed=seed)
    if kind == "build":
        x = make_track(128, 9, True, seconds=sec, drums=True, seed=seed)
        return x * np.linspace(0.12, 0.9, len(x))
    if kind == "drop":
        return make_track(128, 0, False, seconds=sec, drums=True, seed=seed)
    return 0.25 * make_track(128, 5, False, seconds=sec, drums=False, seed=seed)


@pytest.fixture(scope="module")
def edm():
    y = np.concatenate([np.zeros(int(LEAD_IN * SR))] + [_edm_part(k, b, i) for i, (k, b) in enumerate(EDM)])
    return detect_sections(y.astype(np.float32), SR, bpm=128.0)


def test_edm_labels_drop_and_buildup(edm):
    assert [s["label"] for s in edm] == ["intro", "buildup", "drop", "break", "buildup", "drop", "outro"]
    groups = [s["group"] for s in edm]
    assert groups[1] == groups[4] and groups[2] == groups[5] and groups[0] == groups[6]


def test_edm_boundaries_on_the_bar_grid(edm):
    edges = LEAD_IN + np.cumsum([0] + [b for _, b in EDM]) * BAR
    starts = [s["start"] for s in edm[1:]]
    for t in starts:
        bars = (t - LEAD_IN) / BAR
        assert abs(bars - round(bars)) * BAR < 0.15, t                         # on a bar line
    hits = sum(min(abs(t - e) for e in edges[1:-1]) < 0.15 for t in starts)
    assert hits >= len(starts) - 1                                              # at most one is a bar off


def test_beat_grid_needs_a_steady_pulse():
    from keytool.structure import _beat_grid
    rng = np.random.default_rng(1)
    fps = SR / 512
    n = int(60 * fps)
    pulse = np.zeros(n)
    pulse[(np.arange(0.25, 60, 0.5) * fps).astype(int)] = 1.0                 # kick every 0.5 s from 0.25 s
    bt = np.arange(0.25, 60, 0.5)
    grid = _beat_grid(bt, pulse + 0.01 * rng.random(n), SR, 512, 120.0, 60.0)
    assert grid is not None and abs(grid[1] - grid[0] - 0.5) < 0.01
    d = (grid[0] - (0.25 - 0.08)) % 0.5                  # phase = kick minus the onset lag
    assert min(d, 0.5 - d) < 0.03
    assert _beat_grid(bt, rng.random(n), SR, 512, 120.0, 60.0) is None         # no pulse: no grid


def test_snap_prefers_phrase_lines():
    from keytool.structure import _snap_to_phrases
    beats = np.arange(0, 200, 0.5)                                              # 120 BPM, bars of 2 s
    sharp = {16.0: 1.0, 48.0: 1.0, 82.0: 0.7, 80.0: 0.5, 30.0: 0.6}

    def nov_at(t):
        return sharp.get(round(t, 1), 0.1)
    out = _snap_to_phrases([16.2, 47.9, 82.1, 30.1], beats, nov_at, np.ones(len(beats)))
    assert out == [16.0, 30.0, 48.0, 80.0]      # 82 -> phrase line 80 (sharp enough); 30 has none nearby


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


def test_ids_needing_sections_includes_old_detections(lib):
    from keytool.db import Query
    t = lib.search(Query())[0]
    lib.db.update_track(t["id"], status="ok")
    assert lib.db.ids_needing_sections() == [t["id"]]                          # none yet
    old = [dict(start=0.0, end=30.0, group="A", label="intro")]
    lib.db.update_track(t["id"], sections=json.dumps(old))
    assert lib.db.ids_needing_sections() == [t["id"]]                          # first detection: again
    lib.db.update_track(t["id"], sections=json.dumps([dict(old[0], conf=0.8)]))
    assert lib.db.ids_needing_sections() == []
    lib.db.update_track(t["id"], sections="[]")
    assert lib.db.ids_needing_sections() == []                                  # nothing found: stays


def test_section_strip_click_jumps_to_section_start(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    w = pl.WaveformView()
    w.resize(408, 84 + w.STRIP)                                                # 400 px for 100 s
    w.duration = 100.0
    w.peaks = np.ones(100, np.float32); w.colors = np.full((100, 3), 128, np.uint8)
    assert w._wave_height() == w.height()                                       # no sections: no strip
    w.set_sections([dict(start=0, end=40, group="A", label="verse", conf=0.9),
                    dict(start=40, end=100, group="B", label="chorus", conf=0.3)])
    assert w._wave_height() == 84
    w.grab()                                                                    # paints bands, strip, "?"
    got = []
    w.section_clicked.connect(got.append)
    y_strip = 84 + w.STRIP / 2
    assert w.section_in_strip(QPointF(250, 40)) is None                         # waveform: normal seeking
    sec = w.section_in_strip(QPointF(250, y_strip))
    assert sec["label"] == "chorus"

    class Ev:                                                                   # minimal mouse event
        def __init__(self, x, y):
            self._p = QPointF(x, y)

        def position(self):
            return self._p

        def button(self):
            from PySide6.QtCore import Qt
            return Qt.LeftButton
    w.mousePressEvent(Ev(250, y_strip))
    assert got == [40000] and abs(w.position - 0.4) < 1e-9
    w.mouseMoveEvent(Ev(100, y_strip))
    assert "100" not in w.toolTip() and "90" in w.toolTip()                     # verse, 90 % sure
    w.deleteLater(); app.processEvents()
