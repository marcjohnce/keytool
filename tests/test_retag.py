"""Auto re-tag: file-name heuristics, ISRC, MusicBrainz (saved answers, no network) and the details panel."""
import json
import os
import struct
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from keytool import retag
from keytool.retag import (MusicBrainz, MusicBrainzError, best_search_match, looks_broken, parse_filename,
                           read_isrc, recording_tags, suggest)

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("path, expected", [
    (r"/m/Mara Veil - Tidal Lines (2021) [WEB]/03 - Mara Veil - Glass Harbour (Extended Mix) [195023541].flac",
     dict(artist="Mara Veil", title="Glass Harbour (Extended Mix)", album="Tidal Lines", album_artist="Mara Veil",
          year=2021)),
    ("/m/Downloads/Kora_-_Low_Tide_Signals_ft._Selva.mp3", dict(artist="Kora", title="Low Tide Signals feat. Selva")),
    ("/m/Tidal Lines/07 Glass Harbour.mp3", dict(title="Glass Harbour", album="Tidal Lines")),
    ("/m/2019 - Neon Weather/A1 - The Lanterns - Midnight Avenue.wav",
     dict(artist="The Lanterns", title="Midnight Avenue", album="Neon Weather", year=2019)),
    ("/m/Neon Weather/CD2/01. Paper Planes.mp3", dict(title="Paper Planes", album="Neon Weather")),
    ("/m/Mara Veil/Glass Harbour.mp3", dict(title="Glass Harbour")),
])
def test_parse_filename(path, expected):
    assert parse_filename(path) == expected


def test_looks_broken_leaves_good_values_alone():
    row = dict(path="/m/03 - Mara Veil - Glass Harbour [195023541].flac",
               filename="03 - Mara Veil - Glass Harbour [195023541].flac")
    assert looks_broken("title", "03 - Mara Veil - Glass Harbour [195023541]", row)
    assert looks_broken("title", "", row) and looks_broken("year", None, row) and looks_broken("year", 12, row)
    assert not looks_broken("title", "Glass Harbour", row)
    assert not looks_broken("artist", "Mara Veil", row) and not looks_broken("year", 2021, row)


def _wav(path, isrc=None):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(800)))
    if isrc:
        from mutagen.id3 import TSRC
        from mutagen.wave import WAVE
        f = WAVE(str(path)); f.add_tags(); f.tags.add(TSRC(encoding=3, text=isrc)); f.save()
    return path


def test_read_isrc(tmp_path):
    assert read_isrc(str(_wav(tmp_path / "a.wav", "gb-xyz-21-00417"))) == "GBXYZ2100417"
    assert read_isrc(str(_wav(tmp_path / "b.wav"))) == ""
    assert read_isrc(str(_wav(tmp_path / "c.wav", "not an isrc"))) == ""


def test_recording_tags_picks_the_original_album():
    rec = json.loads((FIX / "mb_isrc.json").read_text())["recordings"][0]
    assert recording_tags(rec) == dict(title="Glass Harbour (Extended Mix)", artist="Mara Veil", album="Tidal Lines",
                                       album_artist="Mara Veil", year=2021, genre="Deep House")


def test_search_match_needs_score_names_and_length():
    recs = json.loads((FIX / "mb_search.json").read_text())["recordings"]
    m = best_search_match(recs, "Kora feat. Selva", "Low Tide Signals", 268.0)
    assert m["id"] == "x2"                                   # x1 has the wrong length (a remix release)
    assert best_search_match(recs, "Kora", "Low Tide", None) is None   # x3: score too low


class Fake:
    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def __call__(self, url):
        self.urls.append(url)
        for key, text in self.answers.items():
            if key in url:
                if isinstance(text, Exception):
                    raise text
                return text
        return "{}"


def test_suggest_via_isrc(tmp_path):
    d = tmp_path / "Mara Veil - Tidal Lines (2021) [WEB]"; d.mkdir()
    f = _wav(d / "03 - Mara Veil - Glass Harbour (Extended Mix) [195023541].wav", "GBXYZ2100417")
    row = dict(id=1, path=str(f), filename=f.name, title=f.stem, artist="", album="", album_artist=None,
               year=None, genre="", duration=247.0)
    fake = Fake({"/isrc/GBXYZ2100417": (FIX / "mb_isrc.json").read_text()})
    res = suggest(row, mb=MusicBrainz(fetcher=fake))
    got = {s.field: (s.value, s.source) for s in res.suggestions}
    assert got == {"title": ("Glass Harbour (Extended Mix)", "isrc"), "artist": ("Mara Veil", "isrc"),
                   "album": ("Tidal Lines", "isrc"), "album_artist": ("Mara Veil", "isrc"),
                   "year": (2021, "isrc"), "genre": ("Deep House", "isrc")}
    assert res.isrc == "GBXYZ2100417" and len(fake.urls) == 1 and "fmt=json" in fake.urls[0]
    assert res.by_field()["title"].old == f.stem


def test_suggest_via_search_keeps_good_values(tmp_path):
    f = _wav(tmp_path / "Kora - Low Tide Signals.wav")
    row = dict(id=2, path=str(f), filename=f.name, title="Low Tide Signals", artist="Kora feat. Selva",
               album="", album_artist=None, year=None, genre="My Genre", duration=268.0)
    fake = Fake({"/recording?": (FIX / "mb_search.json").read_text()})
    res = suggest(row, mb=MusicBrainz(fetcher=fake))
    got = {s.field: (s.value, s.source) for s in res.suggestions}
    assert got == {"album": ("Dawn Patrol", "musicbrainz"), "album_artist": ("Kora", "musicbrainz"),
                   "year": (2022, "musicbrainz")}            # title, artist and genre were fine already
    assert "recording%3A%22Low+Tide+Signals%22" in fake.urls[0]


def test_offline_falls_back_to_file_name(tmp_path):
    f = _wav(tmp_path / "Kora_-_Low_Tide_Signals.wav")
    row = dict(id=3, path=str(f), filename=f.name, title="", artist="", album="", year=None, genre="")
    res = suggest(row, mb=MusicBrainz(fetcher=Fake({"musicbrainz": MusicBrainzError("no internet")})))
    assert {s.field: s.value for s in res.suggestions} == {"title": "Low Tide Signals", "artist": "Kora"}
    assert res.online_error and all(s.source == "filename" for s in res.suggestions)
    res = suggest(row, online=False)
    assert not res.online and len(res.suggestions) == 2


# --------------------------------------------------------------------------- details panel
@pytest.fixture
def panel():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from keytool.gui.widgets import DetailPanel
    p = DetailPanel()
    p.set_rows([dict(id=7, path="/m/x/03 - Mara Veil - Glass Harbour [195023541].flac",
                     filename="03 - Mara Veil - Glass Harbour [195023541].flac", ext="flac",
                     title="03 - Mara Veil - Glass Harbour [195023541]", artist="", album="", album_artist="",
                     year=None, genre="", comment="", key=None, bpm=None, energy=None, keywords=[],
                     tag_bpm="", tag_key="", status="ok")])
    p.show()
    yield p
    p.deleteLater()


def _result(*items, isrc=""):
    return retag.RetagResult(suggestions=[retag.Suggestion(f, v, src, old) for f, v, src, old in items], isrc=isrc)


def test_panel_shows_suggestions_and_saves_them(panel):
    saved = []
    panel.save_requested.connect(saved.append)
    panel.apply_suggestions(_result(("title", "Glass Harbour", "filename", "03 - Mara Veil - Glass Harbour [195023541]"),
                                    ("artist", "Mara Veil", "isrc", ""), ("year", 2021, "isrc", None),
                                    ("genre", "Deep House", "isrc", ""), isrc="GBXYZ2100417"))
    assert panel.edits["title"].text() == "Glass Harbour" and panel.year.text() == "2021"
    assert panel.genre.currentText() == "Deep House"
    assert panel.edits["title"].property("suggested") is True
    assert panel._meta_form.isRowVisible(panel.retag_hints["artist"])
    assert not panel._meta_form.isRowVisible(panel.retag_hints["album"])
    assert "GBXYZ2100417" in panel.retag_banner.text() and panel.save_btn.isEnabled()
    panel.save()
    assert saved == [dict(title="Glass Harbour", artist="Mara Veil", year=2021, genre="Deep House")]


def test_panel_undo_and_discard(panel):
    panel.apply_suggestions(_result(("title", "Glass Harbour", "filename", "03 - Mara Veil - Glass Harbour [195023541]"),
                                    ("artist", "Mara Veil", "filename", "")))
    panel.retag_hints["artist"].undo.click()                 # one field back
    assert panel.edits["artist"].text() == "" and "artist" not in panel.dirty
    assert not panel._meta_form.isRowVisible(panel.retag_hints["artist"])
    assert panel.changes() == {"title": "Glass Harbour"}
    panel.revert_btn.click()                                 # Discard: everything back
    assert panel.edits["title"].text() == "03 - Mara Veil - Glass Harbour [195023541]"
    assert not panel.dirty and panel.retag_banner.isHidden()
    assert panel.edits["title"].property("suggested") is False


def test_panel_runs_in_background(panel, monkeypatch):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(retag, "suggest", lambda row, online=True: _result(("artist", "Mara Veil", "filename", "")))
    panel.retag_btn.click()
    assert not panel.retag_btn.isEnabled()
    for _ in range(200):
        QApplication.processEvents()
        if panel.retag_btn.isEnabled():
            break
        import time; time.sleep(0.01)
    assert panel.edits["artist"].text() == "Mara Veil" and panel.retag_btn.isEnabled()


def test_button_only_for_one_track(panel):
    panel.set_rows(panel.rows + [dict(panel.rows[0], id=8)])
    assert not panel.retag_btn.isEnabled()
