"""Auto re-tag: suggest title, artist, album, album artist, year and genre for one track.

Sources, in this order (the first one that has a value for a field wins):
  1. Discogs database search with artist + track, checked against the release's tracklist
     (preferred by the user: Discogs styles such as "Deep House" are precise for DJs)
  2. ISRC code from the file tags -> MusicBrainz ISRC lookup (identifies the recording exactly)
  3. MusicBrainz recording search with artist + title (from the tags, or parsed from the file name)
  4. File name and folder heuristics ("03 - Artist - Title [123456]", "Album (2021) [WEB]")

Only empty fields and fields that are obviously broken (e.g. a title that is just the file name) get a
suggestion; sensible values are left alone. Nothing is written here - the caller shows the suggestions and
the user saves or discards them.

MusicBrainz (https://musicbrainz.org/doc/MusicBrainz_API): free, no key, max. one request per second,
requests must identify the application. Responses are JSON (fmt=json).
Discogs (https://www.discogs.com/developers): used without a token, as decided by the user; anonymous
clients get 25 requests per minute. If Discogs asks for sign-in (401/403), it is skipped and reported.
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
SOURCE_DISCOGS = "discogs"
SOURCE_CASE = "case"                  # only the capitalisation of the current value was fixed
DISCOGS_ROOT = "https://api.discogs.com"
DISCOGS_MIN_INTERVAL = 2.5            # seconds; anonymous Discogs clients: 25 requests per minute


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
    online: bool = False              # online sources were asked
    online_error: str = ""            # details of the failures below
    unreachable: list[str] = field(default_factory=list)   # services that failed ("MusicBrainz", "Discogs")

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


# =========================================================================== capitalisation
TEXT_FIELDS = ("title", "artist", "album", "album_artist", "genre")
_KEEP_UPPER = {"DJ", "MC", "UK", "USA", "US", "EP", "LP", "TV", "NYC", "LA", "OK", "UFO", "RnB", "R&B",
               "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "XIII", "XIV", "XV", "XX", "AC/DC",
               "EDM", "IDM", "EBM", "UKG", "DnB", "D&B", "NRJ"}
_KEEP_LOWER = {"feat.", "ft.", "vs.", "vs", "x"}
_WORD = re.compile(r"[^\W\d_]", re.U)


def bad_case(text) -> bool:
    """All lower case, or ALL UPPER CASE over several words (a single upper-case word may be an acronym or
    a stylised name such as MGMT, so it is left alone)."""
    t = str(text or "")
    letters = _WORD.findall(t)
    if len(letters) < 2:
        return False
    if t == t.lower():
        return True
    return t == t.upper() and len(t.split()) >= 2


def smart_case(text: str) -> str:
    """'midnight avenue (extended mix) feat. kora' -> 'Midnight Avenue (Extended Mix) feat. Kora';
    'DON'T STOP' -> "Don't Stop"; 'hip-hop' -> 'Hip-Hop'; acronyms and roman numerals stay upper case."""
    def word(w: str) -> str:
        lead = re.match(r"^[\(\[\{\"'“‘]*", w).group(0)
        trail = re.search(r"[\)\]\}\"'”’,;:!?]*$", w[len(lead):]).group(0)
        core = w[len(lead):len(w) - len(trail)] if trail else w[len(lead):]
        if not core:
            return w
        low = core.lower()
        if low in _KEEP_LOWER:
            core = low
        elif core.upper() in {k.upper() for k in _KEEP_UPPER}:
            core = next(k for k in _KEEP_UPPER if k.upper() == core.upper())
        else:
            core = "-".join(part[:1].upper() + part[1:].lower() for part in core.split("-"))
        return lead + core + trail
    out = " ".join(word(w) for w in text.split(" "))
    # first word always capitalised, even "feat." / "x" at the start
    return out[:1].upper() + out[1:] if out else out


def _disk_case(path: str) -> str:
    """The path with the letter case it really has on disk (older libraries stored Windows paths in lower
    case; parsing such a file name would suggest lower-case tags)."""
    if not path or not os.path.exists(path):
        return path
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    cur = drive + os.sep
    for part in [p for p in rest.split(os.sep) if p]:
        try:
            names = os.listdir(cur)
        except OSError:
            return path
        match = next((n for n in names if n == part), None) or \
            next((n for n in names if n.casefold() == part.casefold()), part)
        cur = os.path.join(cur, match)
    return cur


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


# =========================================================================== Discogs
class DiscogsError(MusicBrainzError):
    pass


class Discogs:
    """Anonymous Discogs client (no token): database search + release lookup, throttled."""

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
            if e.code in (401, 403):
                raise DiscogsError("Discogs requires signing in for searches") from e
            raise DiscogsError(f"Discogs: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise DiscogsError(f"Discogs not reachable ({getattr(e, 'reason', e)})") from e

    def _get(self, path: str, **params) -> dict:
        url = f"{DISCOGS_ROOT}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        with Discogs._lock:
            wait = Discogs._last + DISCOGS_MIN_INTERVAL - time.monotonic()
            if wait > 0 and self.fetcher == self._fetch:
                time.sleep(wait)
            Discogs._last = time.monotonic()
        text = self.fetcher(url)
        try:
            data = json.loads(text or "{}")
        except ValueError as e:
            raise DiscogsError("Discogs: unexpected answer") from e
        if isinstance(data, dict) and data.get("message") and not data.get("results") and not data.get("id"):
            raise DiscogsError(f"Discogs: {data['message']}")   # e.g. "You must authenticate to access this resource."
        return data

    def search(self, artist: str, track: str) -> list[dict]:
        return self._get("database/search", artist=artist, track=track, type="release", per_page="10").get("results") or []

    def release(self, release_id) -> dict:
        return self._get(f"releases/{release_id}")


def _dc_name(name: str) -> str:
    """Discogs disambiguates artists as 'Name (2)' and marks the main name with '*'."""
    return re.sub(r"\s*\(\d+\)$", "", (name or "").strip()).rstrip("*").strip()


def _dc_artists(artists) -> str:
    """'Kora', join 'Feat.', 'Selva' -> 'Kora Feat. Selva' (join ',' -> 'A, B')."""
    out = ""
    for a in artists or []:
        out += a.get("anv") or _dc_name(a.get("name"))
        j = (a.get("join") or "").strip()
        out += ", " if j == "," else f" {j} " if j else ""
    return re.sub(r"\s+", " ", out).strip(" ,")


def discogs_tags(dc: Discogs, artist: str, title: str, duration: float | None) -> dict:
    """Tags from the first Discogs release (not a compilation) whose tracklist has this track."""
    tried = 0
    for hit in dc.search(artist, title):
        if tried >= 3:
            break
        rel_artist = (hit.get("title") or "").split(" - ", 1)[0]
        if fold(rel_artist) in ("various", "variousartists"):
            continue                                                  # prefer the artist's own release
        tried += 1
        rel = dc.release(hit.get("id"))
        for tr_ in rel.get("tracklist") or []:
            if tr_.get("type_", "track") != "track" or fold(tr_.get("title")) != fold(title):
                continue
            if duration and tr_.get("duration"):
                m = re.match(r"(\d+):(\d{2})$", tr_["duration"])
                if m and abs(int(m.group(1)) * 60 + int(m.group(2)) - duration) > DURATION_TOLERANCE:
                    continue
            album_artist = _dc_artists(rel.get("artists"))
            track_artist = _dc_artists(tr_.get("artists")) or album_artist
            if fold(track_artist) != fold(artist) and fold(album_artist) != fold(artist):
                continue
            styles, genres = rel.get("styles") or hit.get("style") or [], rel.get("genres") or hit.get("genre") or []
            out = {"title": tr_.get("title"), "artist": track_artist, "album": rel.get("title"),
                   "album_artist": album_artist, "year": int(rel["year"]) if str(rel.get("year") or "").isdigit()
                   and int(rel["year"]) > 0 else None, "genre": (styles or genres or [""])[0]}
            return {k: v for k, v in out.items() if v}
    return {}


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


def suggest(row: dict, online: bool = True, mb: MusicBrainz | None = None,
            discogs: Discogs | None = None) -> RetagResult:
    """Suggestions for one library row (dict with path, filename, title, artist, album, ... , duration)."""
    res = RetagResult()
    path = row.get("path") or ""
    guessed = parse_filename(_disk_case(path))
    candidates: dict[str, tuple[object, str]] = {}          # field -> (value, source), best source first
    artist = row.get("artist") if not looks_broken("artist", row.get("artist"), row) else guessed.get("artist")
    title = row.get("title") if not looks_broken("title", row.get("title"), row) else guessed.get("title")
    errors: list[str] = []

    if online and artist and title:
        res.online = True
        try:
            for k, v in discogs_tags(discogs or Discogs(), artist, title, row.get("duration")).items():
                candidates.setdefault(k, (v, SOURCE_DISCOGS))
        except DiscogsError as e:
            res.unreachable.append("Discogs")
            errors.append(str(e))
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
                if artist and title:
                    rec = best_search_match(mb.search(artist, title), artist, title, row.get("duration"))
            if rec is not None:
                for k, v in recording_tags(rec).items():
                    candidates.setdefault(k, (v, src))
        except MusicBrainzError as e:
            res.unreachable.append("MusicBrainz")
            errors.append(str(e))
    res.online_error = "; ".join(errors)
    for k, v in guessed.items():
        candidates.setdefault(k, (v, SOURCE_FILENAME))

    for fld in FIELDS:
        cur = row.get(fld)
        value, src = candidates.get(fld, (None, ""))
        text_field = fld in TEXT_FIELDS
        if value is not None and text_field and bad_case(value) and src == SOURCE_FILENAME:
            value = smart_case(str(value))                  # file names are often all lower case
        if not looks_broken(fld, cur, row):
            if not text_field:
                continue
            cur_s = str(cur)
            if value is not None and str(value) == cur_s and src != SOURCE_FILENAME:
                continue                                    # Discogs/MusicBrainz spell it exactly so (deadmau5)
            if value is not None and str(value).lower() == cur_s.lower() and str(value) != cur_s \
                    and not bad_case(value):
                res.suggestions.append(Suggestion(fld, value, src, cur))       # same text, proper case
            elif bad_case(cur_s):
                fixed = smart_case(cur_s)
                if fixed != cur_s:
                    res.suggestions.append(Suggestion(fld, fixed, SOURCE_CASE, cur))
            continue
        if value is None or (cur not in (None, "") and str(cur) == str(value)):
            continue
        res.suggestions.append(Suggestion(fld, value, src, cur))
    return res
