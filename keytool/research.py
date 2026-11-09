"""External research: build filtered search links (key + BPM range) for external song databases.

KeyTool never downloads these pages itself; it only opens the prepared links in the user's
browser. That keeps it within the sites' terms (Tunebat forbids any automated access) and
independent of HTML changes.

Verified URL schemes (Nov 2026):
* Karaoke-Version   /custombackingtrack/karaoke.html?chordkey=<codes>&tempo=<min>,<max>
                    several keys and any BPM range can be combined in one link
* AudioKeychain     /database?key=<flats, lowercase>&bpm=<value>&s=bpm&d=ASC
                    only ONE key per search and only a single BPM value (no range)
* Tunebat           /Advanced is a JavaScript app without URL parameters -> page is opened and
                    the criteria are copied to the clipboard
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlencode

from .keys import Key

SOURCES = ("karaoke_version", "audiokeychain", "tunebat")
SOURCE_NAMES = {"karaoke_version": "Karaoke-Version", "audiokeychain": "AudioKeychain", "tunebat": "Tunebat"}
MAX_AUDIOKEYCHAIN_KEYS = 6   # one browser tab per key

# Karaoke-Version "chordkey" codes per tonic pitch class (German labels on the site:
# Des=102, Es=4, Fis=7/107, As=109, B(=Bb)=11, H=12; Dism=16/116, Bm(=bb minor)=23, Hm=24)
_KV_MAJOR = {0: [1], 1: [102], 2: [3], 3: [4], 4: [5], 5: [6], 6: [7, 107], 7: [8], 8: [109], 9: [10],
             10: [11], 11: [12]}
_KV_MINOR = {0: [13], 1: [14], 2: [15], 3: [116, 16], 4: [17], 5: [18], 6: [19], 7: [20], 8: [21],
             9: [22], 10: [23], 11: [24]}


@dataclass
class ResearchLink:
    source: str            # one of SOURCES
    label: str             # human readable description
    url: str
    note: str = ""         # hint shown to the user (limitations of the source)


def describe_criteria(keys: list[Key], bpm_min: float | None, bpm_max: float | None) -> str:
    parts = []
    if keys:
        def name(k: Key) -> str:
            n = k.sharps() if k.sharps() == k.flats() else f"{k.sharps()}/{k.flats()}"
            return f"{n} ({k.camelot()})"
        parts.append("Key: " + ", ".join(name(k) for k in keys))
    if bpm_min or bpm_max:
        parts.append(f"BPM: {_fmt(bpm_min) or '…'} – {_fmt(bpm_max) or '…'}")
    return " · ".join(parts)


def _fmt(v: float | None) -> str:
    if not v:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def karaoke_version_url(keys: list[Key], bpm_min: float | None, bpm_max: float | None,
                        base: str = "https://www.karaoke-version.de/custombackingtrack/karaoke.html") -> str:
    params = {}
    if keys:
        codes: list[int] = []
        for k in keys:
            for c in (_KV_MINOR if k.minor else _KV_MAJOR)[k.pc]:
                if c not in codes:
                    codes.append(c)
        params["chordkey"] = ",".join(map(str, codes))
    if bpm_min or bpm_max:
        lo = int(math.floor(bpm_min)) if bpm_min else 30
        hi = int(math.ceil(bpm_max)) if bpm_max else 300
        params["tempo"] = f"{lo},{hi}"
    return base + ("?" + urlencode(params) if params else "")


def audiokeychain_key_value(k: Key) -> str:
    return k.flats().lower()      # site uses flats: abm, b, ebm, gb, ... (1A..12B)


def audiokeychain_urls(keys: list[Key], bpm_min: float | None, bpm_max: float | None) -> list[ResearchLink]:
    base = "https://www.audiokeychain.com/database"
    note = ""
    params: dict[str, str] = {"search": ""}
    if bpm_min and bpm_max and bpm_max - bpm_min <= 2:
        params["bpm"] = str(int(round((bpm_min + bpm_max) / 2)))     # exact tempo search (±1 BPM)
    elif bpm_min or bpm_max:
        note = "range"     # AudioKeychain cannot filter a BPM range -> results sorted by BPM instead
    params.update(s="bpm", d="ASC")
    out = []
    for k in (keys or [None])[:MAX_AUDIOKEYCHAIN_KEYS]:
        p = dict(params)
        p["key"] = audiokeychain_key_value(k) if k else ""
        label = f"{SOURCE_NAMES['audiokeychain']}: {k.camelot()} / {k.flats()}" if k else SOURCE_NAMES["audiokeychain"]
        out.append(ResearchLink("audiokeychain", label, base + "?" + urlencode(p), note))
    if keys and len(keys) > MAX_AUDIOKEYCHAIN_KEYS:
        out[-1].note = (out[-1].note + " too_many_keys").strip()
    return out


def build_links(keys: list[Key], bpm_min: float | None = None, bpm_max: float | None = None,
                sources: tuple[str, ...] | list[str] = SOURCES) -> list[ResearchLink]:
    keys = list(dict.fromkeys(keys))          # unique, keep order
    if bpm_min and bpm_max and bpm_min > bpm_max:
        bpm_min, bpm_max = bpm_max, bpm_min
    crit = describe_criteria(keys, bpm_min, bpm_max)
    links: list[ResearchLink] = []
    if "karaoke_version" in sources:
        links.append(ResearchLink("karaoke_version", f"{SOURCE_NAMES['karaoke_version']}: {crit or '–'}",
                                  karaoke_version_url(keys, bpm_min, bpm_max)))
    if "audiokeychain" in sources:
        links += audiokeychain_urls(keys, bpm_min, bpm_max)
    if "tunebat" in sources:
        links.append(ResearchLink("tunebat", SOURCE_NAMES["tunebat"], "https://tunebat.com/Advanced", "manual"))
    return links
