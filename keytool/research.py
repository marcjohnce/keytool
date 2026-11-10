"""External research on Karaoke-Version (karaoke-version.de): search by key(s) and BPM range and parse
the results.

Site facts (verified Nov 2026):
* Search  /custombackingtrack/karaoke.html?chordkey=<codes>&tempo=<min>,<max>&page=<n>
          20 results per page; several keys and any BPM range can be combined.
* Results (.songlist__item) carry title, artist, year, price, image and the song page link,
          but NOT key or BPM. KeyTool therefore searches once per key (so every hit's key is known)
          and reads tempo / all keys / duration from the song page on demand.
* Song page "Tempo: 126 BPM" | "Tempo: variabel (ca. 130 BPM)" | no tempo line,
          "In der gleichen Tonart wie das Original: C, Bm" (German note names: H = B, B = Bb,
          Fis/Cis/... = sharps, Es/As/... = flats; several keys = key changes within the song),
          "Dauer: 04:27".
* Terms: Recisio's AGB only reserve text & data mining for AI/ML training; robots.txt allows the
          search pages for normal user agents. KeyTool only fetches what the user asks for, identifies
          itself, throttles requests and caches song pages.
"""
from __future__ import annotations

import html as html_lib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from urllib.parse import urlencode, urljoin

from . import __version__
from .keys import Key

BASE = "https://www.karaoke-version.de"
SEARCH_PATH = "/custombackingtrack/karaoke.html"
PER_PAGE = 20
USER_AGENT = f"KeyTool/{__version__} (desktop music library; key/BPM research)"
MIN_INTERVAL = 0.6          # seconds between two requests (politeness)
DETAIL_TTL = 14 * 86400     # cache song pages for two weeks

# "chordkey" codes per tonic pitch class (German labels: Des=102, Es=4, Fis=7/107, As=109, B(=Bb)=11,
# H=12; Dism=16/116, Bm(=bb minor)=23, Hm=24)
_KV_MAJOR = {0: [1], 1: [102], 2: [3], 3: [4], 4: [5], 5: [6], 6: [7, 107], 7: [8], 8: [109], 9: [10],
             10: [11], 11: [12]}
_KV_MINOR = {0: [13], 1: [14], 2: [15], 3: [116, 16], 4: [17], 5: [18], 6: [19], 7: [20], 8: [21],
             9: [22], 10: [23], 11: [24]}

# German note names -> pitch class (B = Bb, H = B natural)
_GERMAN_PC = {
    "c": 0, "cis": 1, "des": 1, "d": 2, "dis": 3, "es": 3, "e": 4, "eis": 5, "fes": 4, "f": 5, "fis": 6,
    "ges": 6, "g": 7, "gis": 8, "as": 8, "a": 9, "ais": 10, "b": 10, "h": 11, "his": 0, "ces": 11,
}


# =========================================================================== data
@dataclass
class KVSong:
    title: str
    artist: str
    year: int | None
    url: str
    image: str = ""
    price: str = ""
    song_id: str = ""
    keys: list[str] = field(default_factory=list)      # canonical key codes known so far
    bpm: float | None = None
    bpm_variable: bool = False
    duration: int | None = None                        # seconds
    details_loaded: bool = False

    def to_dict(self, notation: str = "camelot") -> dict:
        d = asdict(self)
        d["keys"] = [Key.from_code(c).format(notation) for c in self.keys]
        return d


@dataclass
class KVDetails:
    bpm: float | None
    bpm_variable: bool
    keys: list[str]
    duration: int | None


@dataclass
class KVPage:
    total: int                     # total hits (sum over the per-key searches)
    songs: list[KVSong]
    page: int
    has_more: bool
    url: str                       # the combined search on the website (for "open in browser")


# =========================================================================== semitone neighbours
def with_semitones(keys: list[Key], semitones: int = 1) -> list[Key]:
    """The keys plus the keys up to `semitones` below/above (same mode): songs in those keys can be
    transposed on Karaoke-Version to fit. Order: the chosen keys first, then the neighbours."""
    out = list(dict.fromkeys(keys))
    for k in keys:
        for st in range(1, semitones + 1):
            for d in (-st, st):
                t = k.transpose(d)
                if t not in out:
                    out.append(t)
    return out


def semitone_shift(song_keys: list[str], targets: set[str], semitones: int = 1) -> int | None:
    """By how many semitones a song has to be transposed so that one of its keys becomes a target key:
    0 = fits as is; +1 = one semitone higher; -1 = lower; None = does not fit within +-semitones."""
    best = None
    for code in song_keys:
        k = Key.from_code(code)
        if not k:
            continue
        for st in sorted(range(-semitones, semitones + 1), key=abs):   # 0 first, then +-1 ...
            if k.transpose(st).code in targets:
                if best is None or abs(st) < abs(best):
                    best = st
                break
    return best


# =========================================================================== URLs
def chordkey_codes(keys: list[Key]) -> list[int]:
    codes: list[int] = []
    for k in keys:
        for c in (_KV_MINOR if k.minor else _KV_MAJOR)[k.pc]:
            if c not in codes:
                codes.append(c)
    return codes


def search_url(keys: list[Key], bpm_min: float | None = None, bpm_max: float | None = None,
               page: int = 1, base: str = BASE) -> str:
    params: dict[str, str] = {}
    if keys:
        params["chordkey"] = ",".join(map(str, chordkey_codes(keys)))
    if bpm_min or bpm_max:
        lo = int(math.floor(bpm_min)) if bpm_min else 30
        hi = int(math.ceil(bpm_max)) if bpm_max else 300
        params["tempo"] = f"{lo},{hi}"
    if page > 1:
        params["page"] = str(page)
    return base + SEARCH_PATH + ("?" + urlencode(params) if params else "")


# =========================================================================== parsing
def _text(fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(t)).strip()


def parse_total(page_html: str) -> int | None:
    m = re.search(r'data-pagination-ajax[^>]*data-total="(\d+)"', page_html)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d[\d.]*)\s+Titel verfügbar", page_html)
    return int(m.group(1).replace(".", "")) if m else None


def parse_list(page_html: str, base: str = BASE) -> tuple[int | None, list[KVSong]]:
    """Parse a result page. Returns (total hits or None, songs on this page)."""
    songs = []
    chunks = page_html.split('<div class="songlist__item"')[1:]
    for ch in chunks:
        m = re.search(r'class="song__name[^"]*"[^>]*>\s*<a\b([^>]*)>(.*?)</a>', ch, re.S)
        if not m:
            continue
        attrs, inner = m.group(1), m.group(2)
        href = re.search(r'href="([^"]+)"', attrs)
        sid = re.search(r'data-prodsongid="([^"]+)"', attrs)
        am = re.search(r'class="song__artist[^"]*"[^>]*>(.*?)</a>', ch, re.S)
        ym = re.search(r'class="song__year"[^>]*>\s*(\d{4})', ch)
        im = re.search(r'<img[^>]*src="([^"]+)"', ch)
        pm = re.search(r'class="song__price".*?<p>(.*?)</p>', ch, re.S)
        if not href:
            continue
        songs.append(KVSong(
            title=_text(inner), artist=_text(am.group(1)) if am else "",
            year=int(ym.group(1)) if ym else None, url=urljoin(base, html_lib.unescape(href.group(1))),
            image=html_lib.unescape(im.group(1)) if im else "", price=_text(pm.group(1)) if pm else "",
            song_id=sid.group(1) if sid else "",
        ))
    return parse_total(page_html), songs


def parse_german_key(token: str) -> Key | None:
    """'Am', 'Fis', 'Dism', 'Esm', 'Hm', 'B' (= Bb major), 'Bm' (= bb minor), 'C#m', 'B♭' ..."""
    t = token.strip().replace("♯", "#").replace("♭", "b")
    m = re.fullmatch(r"([A-Ha-h])(is|es|s)?\s*(m|moll|-moll|dur|-dur)?", t, re.I)
    if m:
        name = (m.group(1) + (m.group(2) or "")).lower()
        if name in ("ass",):
            name = "as"
        if name in _GERMAN_PC:
            mode = (m.group(3) or "").lower().lstrip("-")
            return Key(_GERMAN_PC[name], mode in ("m", "moll"))
    if "#" in t or re.fullmatch(r"[A-G]b\s*m?", t):
        return Key.parse(t)   # English spelling with accidentals
    return None


def parse_detail(page_html: str) -> KVDetails:
    text = _text(page_html)
    bpm, variable = None, False
    m = re.search(r"Tempo:\s*(variabel|variable)?\s*\(?\s*(?:ca\.|ungefähr|around)?\s*(\d+(?:[.,]\d+)?)\s*BPM",
                  text, re.I)
    if m:
        variable = bool(m.group(1))
        bpm = float(m.group(2).replace(",", "."))
    keys: list[str] = []
    m = (re.search(r"Tonart wie das Original:\s*(.+?)(?:\s+Dauer:|$)", text)
         or re.search(r"(?:Originaltonart|Tonart|Tonlage)\s*:\s*(.+?)(?:\s+Dauer:|$)", text))
    if m:
        for tok in re.split(r"\s*,\s*|\s+und\s+|\s*/\s*", m.group(1)):
            tok = tok.strip()
            if not tok:
                continue
            k = parse_german_key(tok)
            if k is None:
                break           # end of the key list
            if k.code not in keys:
                keys.append(k.code)
    dur = None
    m = re.search(r"Dauer:\s*(\d+):(\d{2})", text)
    if m:
        dur = int(m.group(1)) * 60 + int(m.group(2))
    return KVDetails(bpm, variable, keys, dur)


# =========================================================================== client
class KVError(RuntimeError):
    pass


class KaraokeVersionClient:
    """Throttled HTTP client with a small on-disk cache for song pages. Thread-safe."""

    def __init__(self, base: str = BASE, cache_file=None, fetcher=None):
        self.base = base
        self._lock = threading.Lock()
        self._last = 0.0
        self._fetcher = fetcher or self._http_get
        if cache_file is None:
            from .config import data_dir
            cache_file = data_dir() / "karaoke_version_cache.json"
        self.cache_file = cache_file
        self._cache: dict[str, dict] = {}
        try:
            self._cache = json.loads(open(cache_file, encoding="utf-8").read())
        except Exception:
            self._cache = {}

    # -- http
    def _http_get(self, url: str) -> str:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "de-DE,de;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                charset = r.headers.get_content_charset() or "utf-8"
                return r.read().decode(charset, "replace")
        except urllib.error.HTTPError as e:
            raise KVError(f"Karaoke-Version: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise KVError(f"Karaoke-Version nicht erreichbar / not reachable: {getattr(e, 'reason', e)}") from e

    def get(self, url: str) -> str:
        with self._lock:                       # one request at a time, spaced out
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                return self._fetcher(url)
            finally:
                self._last = time.monotonic()

    # -- search
    def search(self, keys: list[Key], bpm_min: float | None = None, bpm_max: float | None = None,
               page: int = 1, cancel: threading.Event | None = None) -> KVPage:
        """One request per key (so each hit's key is known), results merged by song URL."""
        keys = list(dict.fromkeys(keys))
        if bpm_min and bpm_max and bpm_min > bpm_max:
            bpm_min, bpm_max = bpm_max, bpm_min
        queries = [[k] for k in keys] or [[]]
        merged: dict[str, KVSong] = {}
        total, more = 0, False
        for q in queries:
            if cancel and cancel.is_set():
                break
            n, songs = parse_list(self.get(search_url(q, bpm_min, bpm_max, page, self.base)), self.base)
            n = n if n is not None else len(songs)
            total += n
            more = more or n > page * PER_PAGE
            for s in songs:
                hit = merged.setdefault(s.url, s)
                for k in q:
                    if k.code not in hit.keys:
                        hit.keys.append(k.code)
                cached = self._cached_details(s.url)
                if cached:
                    self._apply(hit, cached)
        return KVPage(total=total, songs=list(merged.values()), page=page, has_more=more,
                      url=search_url(keys, bpm_min, bpm_max, 1, self.base))

    # -- details
    def _cached_details(self, url: str) -> KVDetails | None:
        c = self._cache.get(url)
        if c and time.time() - c.get("t", 0) < DETAIL_TTL:
            return KVDetails(c.get("bpm"), c.get("var", False), c.get("keys", []), c.get("dur"))
        return None

    @staticmethod
    def _apply(song: KVSong, d: KVDetails) -> None:
        song.bpm, song.bpm_variable, song.duration = d.bpm, d.bpm_variable, d.duration
        for c in d.keys:
            if c not in song.keys:
                song.keys.append(c)
        song.details_loaded = True

    def load_details(self, song: KVSong) -> KVSong:
        d = self._cached_details(song.url)
        if d is None:
            d = parse_detail(self.get(song.url))
            self._cache[song.url] = dict(t=time.time(), bpm=d.bpm, var=d.bpm_variable, keys=d.keys, dur=d.duration)
            self._save_cache()
        self._apply(song, d)
        return song

    def _save_cache(self) -> None:
        try:
            now = time.time()
            self._cache = {u: c for u, c in self._cache.items() if now - c.get("t", 0) < DETAIL_TTL}
            tmp = str(self.cache_file) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
            import os
            os.replace(tmp, self.cache_file)
        except OSError:
            pass


# =========================================================================== library matching
def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]|feat\..*|ft\..*", " ", s)
    return re.sub(r"[^0-9a-zäöüß]+", "", s)


def library_index(rows: list[dict]) -> set[tuple[str, str]]:
    return {(_norm(r.get("title", "")), _norm(r.get("artist", ""))) for r in rows if r.get("title")}


def in_library(song: KVSong, index: set[tuple[str, str]]) -> bool:
    return (_norm(song.title), _norm(song.artist)) in index
