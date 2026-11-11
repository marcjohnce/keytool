"""Karaoke-Version's bot protection ("Client Challenge") is reported clearly instead of 'no results'."""
from pathlib import Path

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
        cl.load_details(KVSong("t", "a", None, "https://www.karaoke-version.com/custombackingtrack/x/y.html"))



def test_english_song_page_is_parsed():
    """karaoke-version.com shows the song facts in English; B is B natural there (German B = B flat)."""
    from keytool.research import parse_detail, parse_english_key
    page = ("<div>Tempo: around 128 BPM</div><div>In the same key as the original: B, F#m and Eb</div>"
            "<div>Duration: 04:27</div>")
    d = parse_detail(page)
    assert d.bpm == 128.0 and d.bpm_variable and d.duration == 267
    assert [Key.from_code(c).camelot() for c in d.keys] == ["1B", "11A", "5B"]
    assert parse_english_key("Bb minor").camelot() == "3A" and parse_english_key("H") is None
    assert parse_detail("<p>Tempo: 96 BPM</p><p>Length: 03:05</p>").duration == 185


def test_search_url_uses_the_com_site():
    from keytool.research import search_url
    assert search_url([Key.parse("8A")], 120, 128).startswith("https://www.karaoke-version.com/custombackingtrack/")
