"""Auto re-tag: suggest title, artist, album, album artist, year and genre for one track.

Sources, most reliable first:
  1. ISRC code from the file tags -> MusicBrainz ISRC lookup (identifies the recording exactly)
  2. MusicBrainz recording search with artist + title (from the tags, or parsed from the file name)
  3. File name and folder heuristics ("03 - Artist - Title [123456]", "Album (2021) [WEB]")

Only empty fields and fields that are obviously broken (e.g. a title that is just the file name) get a
suggestion; sensible values are left alone. Nothing is written here - the caller shows the suggestions and
the user saves or discards them.

MusicBrainz (https://musicbrainz.org/doc/MusicBrainz_API): free, no key, max. one request per second,
requests must identify the application. Responses are JSON (fmt=json).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import __version__

FIELDS = ("title", "artist", "album", "album_artist", "year", "genre")
MB_ROOT = "https://musicbrainz.org/ws/2"
USER_AGENT = f"KeyTool/{__version__} ( https://github.com/marcjohnce/keytool )"
MIN_INTERVAL = 1.1                    # seconds between MusicBrainz requests (their limit: 1/s)
SEARCH_MIN_SCORE = 90                 # MusicBrainz search score a match must reach
DURATION_TOLERANCE = 10.0             # seconds

SOURCE_FILENAME = "filename"
SOURCE_ISRC = "isrc"
SOURCE_SEARCH = "musicbrainz"


@dataclass
class Suggestion:
    field: str
    value: object
    source: str                       # SOURCE_*
    old: object = None


@dataclass
class RetagResult:
    suggestions: list[Suggestion] = field(default_factory=list)
    isrc: str = ""
    online: bool = False              # MusicBrainz was asked
    online_error: str = ""            # e.g. no internet; file-name suggestions are still there

    def by_field(self) -> dict[str, Suggestion]:
        return {s.field: s for s in self.suggestions}


# =========================================================================== text helpers
_ID_BRACKETS = re.compile(r"\s*[\[\(\{]\s*(?:\d{5,}|[0-9a-f]{8,}|web|flac|mp3|320|v0|hi-?res|\d{2,3}\s*k(?:hz|bps)?|"
                          r"\d{2}[- ]?bit|cd|vinyl|deluxe edition rip|promo)\s*[\]\)\}]", re.I)
_TRACKNO = re.compile(r"^\s*(?:cd\s*\d+\s*[-.]?\s*)?(?:\d{1,3}|[a-d]\d{1,2})\s*(?:[-._)]+\s*|\s+(?=\D))", re.I)
_GENERIC_FOLDERS = {"download", "downloads", "music", "musik", "new", "neu", "temp", "tmp", "desktop", "incoming",
                    "audio", "tracks", "songs", "mp3", "flac", "various", "unknown", "misc"}
_FEAT = re.compile(r"\s*[\(\[]?\s*\b(?:feat|ft|featuring)\b\.?\s+", re.I)
_YEAR = re.compile(r"(?:^|[\s\[\(\-_])((?:19|20)\d{2})(?:$|[\s\]\)\-_])")
_SPACES = re.compile(r"\s+")


def clean(text: str) -> str:
    """Remove download IDs / format tags in brackets, underscores and doubled spaces."""
    t = (text or "").replace("_", " ")
    prev = None
    while prev != t:
        prev = t
        t = _ID_BRACKETS.sub("", t)
    return _SPACES.sub(" ", t).strip(" -.")


def norm_feat(text: str) -> str:
    """'ft. X' / 'Feat X' / 'featuring X' -> 'feat. X' (brackets kept as they were)."""
    return _FEAT.sub(lambda m: (" (" if "(" in m.group(0) else " [" if "[" in m.group(0) else " ") + "feat. ", text)


def fold(text: str) -> str:
    """Comparable form: lower case, no accents, no punctuation."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\b(?:feat|ft|featuring)\b.*$", "", t)
    return re.sub(r"[^a-z0-9]+", "", t)


# =========================================================================== file name / folder
def parse_filename(path: str) -> dict:
    """Guess title, artist, album and year from the file name and its folder.

    "03 - Mara Veil - Glass Harbour (Extended Mix) [195023541].flac" -> artist, title
    folder "Mara Veil - Tidal Lines (2021) [WEB]" -> album "Tidal Lines", year 2021 (album artist "Mara Veil")
    """
    out: dict = {}
    stem = os.path.splitext(os.path.basename(path))[0]
    numbered = bool(_TRACKNO.match(stem.replace("_", " ")))
    name = clean(_TRACKNO.sub("", stem.replace("_", " ")))
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", name) if p.strip()]
    if len(parts) >= 2:
        # "Artist - Title" (a leading track number was removed above; "01 - Artist - Title" too)
        out["artist"] = norm_feat(parts[0])
        out["title"] = norm_feat(" - ".join(parts[1:]))
    elif parts:
        out["title"] = norm_feat(parts[0])
    folder = os.path.basename(os.path.dirname(path))
    if re.fullmatch(r"(?:cd|disc|disk)\s*\d+", folder or "", re.I):           # ".../Album/CD1/01 x.mp3"
        folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
    if folder and fold(folder) not in _GENERIC_FOLDERS:
        y = _YEAR.search(folder)
        if y:
            out["year"] = int(y.group(1))
        f = _ID_BRACKETS.sub("", folder)
        f = re.sub(r"[\[\(]\s*(?:19|20)\d{2}\s*[\]\)]", "", f)                # "(2021)"
        f = re.sub(r"^\s*(?:19|20)\d{2}\s*[-–]\s*", "", f)                      # "2021 - Album"
        f = clean(f)
        fparts = [p.strip() for p in re.split(r"\s+[-–—]\s+", f) if p.strip()]
        if len(fparts) >= 2:
            out["album_artist"] = fparts[0]
            out["album"] = " - ".join(fparts[1:])
        elif fparts and numbered and fold(fparts[0]) != fold(out.get("artist", "")):
            out["album"] = fparts[0]                     # plain album folder, only for numbered album tracks
    return out


# =========================================================================== ISRC
_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")


def norm_isrc(text) -> str:
    t = re.sub(r"[\s\-]", "", str(text or "")).upper()
    return t if _ISRC.match(t) else ""


def read_isrc(path: str) -> str:
    """ISRC from the file tags (ID3 TSRC, Vorbis/FLAC ISRC, MP4 ----:com.apple.iTunes:ISRC)."""
    try:
        import mutagen
        f = mutagen.File(path)
    except Exception:
        return ""
    if f is None or f.tags is None:
        return ""
    tags = f.tags
    try:
        if hasattr(tags, "getall"):
            for fr in tags.getall("TSRC"):
                for v in fr.text:
                    if norm_isrc(v):
                        return norm_isrc(v)
            return ""
        for key in ("----:com.apple.iTunes:ISRC", "ISRC", "isrc", "TSRC"):
            try:
                v = tags.get(key)
            except Exception:
                v = None
            if v:
                v = v[0]
                if isinstance(v, (bytes, bytearray)) or type(v).__name__ == "MP4FreeForm":
                    v = bytes(v).decode("utf-8", "ignore")
                if norm_isrc(v):
                    return norm_isrc(v)
    except Exception:
        return ""
    return ""


# =========================================================================== MusicBrainz
class MusicBrainzError(RuntimeError):
    pass


class MusicBrainz:
    """Minimal, throttled MusicBrainz client. `fetcher(url) -> str` can be replaced in tests."""

    _lock = threading.Lock()
    _last = 0.0

    def __init__(self, fetcher=None, timeout: float = 10.0):
        self.fetcher = fetcher or self._fetch
        self.timeout = timeout

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "{}"
            raise MusicBrainzError(f"MusicBrainz: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise MusicBrainzError(f"MusicBrainz not reachable ({getattr(e, 'reason', e)})") from e

    def _get(self, path: str, **params) -> dict:
        params["fmt"] = "json"
        url = f"{MB_ROOT}/{path}?{urllib.parse.urlencode(params)}"
        with MusicBrainz._lock:                     # one request per second across the whole app
            wait = MusicBrainz._last + MIN_INTERVAL - time.monotonic()
            if wait > 0 and self.fetcher == self._fetch:
                time.sleep(wait)
            MusicBrainz._last = time.monotonic()
        text = self.fetcher(url)
        try:
            return json.loads(text or "{}")
        except ValueError as e:
            raise MusicBrainzError("MusicBrainz: unexpected answer") from e

    def by_isrc(self, isrc: str) -> list[dict]:
        return self._get(f"isrc/{isrc}", inc="artist-credits releases genres tags").get("recordings") or []

    def search(self, artist: str, title: str) -> list[dict]:
        q = f'recording:"{_lucene(title)}" AND artist:"{_lucene(artist)}"'
        return self._get("recording", query=q, limit="10").get("recordings") or []


def _lucene(text: str) -> str:
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text)


def _credit(ac) -> str:
    return "".join(f"{c.get('name') or (c.get('artist') or {}).get('name', '')}{c.get('joinphrase', '')}"
                   for c in (ac or [])).strip()


def _year_of(date: str | None) -> int | None:
    m = re.match(r"(\d{4})", date or "")
    return int(m.group(1)) if m else None


def _genre(rec: dict) -> str:
    items = rec.get("genres") or rec.get("tags") or []
    items = [g for g in items if g.get("name") and (g.get("count") or 0) >= 0]
    if not items:
        return ""
    best = max(items, key=lambda g: (g.get("count") or 0))
    return " ".join(w if w.isupper() else w.capitalize() for w in best["name"].split())


def _pick_release(rec: dict) -> dict | None:
    """The original album: official, not a compilation by other artists, earliest date."""
    rels = rec.get("releases") or []
    if not rels:
        return None
    artist = fold(_credit(rec.get("artist-credit")))

    def rank(r):
        rg = r.get("release-group") or {}
        secondary = [s.lower() for s in (rg.get("secondary-types") or [])]
        ra = fold(_credit(r.get("artist-credit"))) if r.get("artist-credit") else artist
        return (
            0 if (r.get("status") or "Official") == "Official" else 1,
            0 if "compilation" not in secondary and ra == artist else 1,
            0 if (rg.get("primary-type") or "Album") in ("Album", "EP") else 1,
            r.get("date") or "9999",
        )
    return sorted(rels, key=rank)[0]


def recording_tags(rec: dict) -> dict:
    """title / artist / album / album_artist / year / genre from a MusicBrainz recording."""
    out = {"title": rec.get("title") or "", "artist": _credit(rec.get("artist-credit"))}
    rel = _pick_release(rec)
    if rel:
        out["album"] = rel.get("title") or ""
        out["album_artist"] = _credit(rel.get("artist-credit")) or out["artist"]
        out["year"] = _year_of(rel.get("date")) or _year_of(rec.get("first-release-date"))
    else:
        out["year"] = _year_of(rec.get("first-release-date"))
    out["genre"] = _genre(rec)
    return {k: v for k, v in out.items() if v}


def best_search_match(recs: list[dict], artist: str, title: str, duration: float | None) -> dict | None:
    """A search hit counts only if score, artist, title and (if known) length agree."""
    for r in recs:
        if int(r.get("score") or 0) < SEARCH_MIN_SCORE:
            continue
        if fold(r.get("title")) != fold(title) or fold(_credit(r.get("artist-credit"))) != fold(artist):
            continue
        if duration and r.get("length") and abs(r["length"] / 1000 - duration) > DURATION_TOLERANCE:
            continue
        return r
    return None


# =========================================================================== suggestions
def looks_broken(fld: str, value, row: dict) -> bool:
    """Empty, or clearly not a real tag value (title = file name, track number / download ID in it)."""
    if value in (None, "", 0):
        return True
    if fld in ("year",):
        return not (1900 <= int(value) <= 2100)
    v = str(value)
    stem = os.path.splitext(row.get("filename") or os.path.basename(row.get("path") or ""))[0]
    if fld == "title":
        return fold(v) == fold(stem) or bool(_TRACKNO.match(v) and " - " in v) or bool(_ID_BRACKETS.search(v))
    return bool(_ID_BRACKETS.search(v)) or "_" in v


def suggest(row: dict, online: bool = True, mb: MusicBrainz | None = None) -> RetagResult:
    """Suggestions for one library row (dict with path, filename, title, artist, album, ... , duration)."""
    res = RetagResult()
    path = row.get("path") or ""
    guessed = parse_filename(path)
    candidates: dict[str, tuple[object, str]] = {}          # field -> (value, source), best source first

    if online:
        mb = mb or MusicBrainz()
        res.online = True
        res.isrc = read_isrc(path) if os.path.exists(path) else ""
        try:
            rec = None
            src = SOURCE_SEARCH
            if res.isrc:
                recs = mb.by_isrc(res.isrc)
                if len(recs) >= 1:
                    rec, src = recs[0], SOURCE_ISRC
            if rec is None:
                artist = row.get("artist") if not looks_broken("artist", row.get("artist"), row) else guessed.get("artist")
                title = row.get("title") if not looks_broken("title", row.get("title"), row) else guessed.get("title")
                if artist and title:
                    rec = best_search_match(mb.search(artist, title), artist, title, row.get("duration"))
            if rec is not None:
                for k, v in recording_tags(rec).items():
                    candidates.setdefault(k, (v, src))
        except MusicBrainzError as e:
            res.online_error = str(e)
    for k, v in guessed.items():
        candidates.setdefault(k, (v, SOURCE_FILENAME))

    for fld in FIELDS:
        if fld not in candidates:
            continue
        value, src = candidates[fld]
        cur = row.get(fld)
        if not looks_broken(fld, cur, row):
            continue
        if cur not in (None, "") and str(cur) == str(value):
            continue
        res.suggestions.append(Suggestion(fld, value, src, cur))
    return res
