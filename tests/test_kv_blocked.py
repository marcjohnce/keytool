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
        cl.load_details(KVSong("t", "a", None, "https://www.karaoke-version.de/custombackingtrack/x/y.html"))

