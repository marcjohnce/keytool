"""Reading metadata from and writing BPM/key into audio file tags (mutagen)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac"}


@dataclass
class Metadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    year: int | None = None
    genre: str = ""
    tag_bpm: str = ""
    tag_key: str = ""
    duration: float | None = None
    extra: dict = field(default_factory=dict)


def parse_tag_bpm(text: str | None) -> float | None:
    """BPM from a tag value: '128', '128.00', '128,5', '128 BPM'. Plausible range 30-300."""
    if not text:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(text))
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", "."))
    except ValueError:
        return None
    return round(v, 2) if 30 <= v <= 300 else None


def parse_tag_key(text: str | None) -> str | None:
    """Canonical key code from a tag value in any common notation.

    Handles 'Am', 'A minor', 'Amin', '8A', '08A', '1m' (Open Key), 'Abm', 'G#m' and
    Mixed-In-Key style values like '8A - 6' or '8A/Am'. Off-key markers ('o') give None.
    """
    from .keys import Key

    if not text:
        return None
    t = str(text).strip()
    k = Key.parse(t)
    if k:
        return k.code
    for part in re.split(r"\s*[-/|,;]\s*|\s+", t):
        k = Key.parse(part)
        if k:
            return k.code
    return None


def is_audio_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


def _first(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if hasattr(v, "text"):
        v = v.text[0] if v.text else ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).strip()


def _year(text: str) -> int | None:
    m = re.search(r"(1[89]\d\d|20\d\d)", text or "")
    return int(m.group(1)) if m else None


def read_metadata(path: str) -> Metadata:
    import mutagen

    md = Metadata()
    try:
        f = mutagen.File(path)
    except Exception:
        f = None
    if f is not None:
        if getattr(f, "info", None) is not None and getattr(f.info, "length", None):
            md.duration = float(f.info.length)
        tags = f.tags
        if tags is not None:
            cls = type(tags).__name__
            if cls == "ID3" or hasattr(tags, "getall"):
                md.title = _first(tags.get("TIT2"))
                md.artist = _first(tags.get("TPE1")) or _first(tags.get("TPE2"))
                md.album = _first(tags.get("TALB"))
                md.year = _year(_first(tags.get("TDRC")) or _first(tags.get("TYER")) or _first(tags.get("TDOR")))
                md.genre = _first(tags.get("TCON"))
                md.tag_bpm = _first(tags.get("TBPM"))
                md.tag_key = _first(tags.get("TKEY"))
            elif cls in ("MP4Tags",):
                md.title = _first(tags.get("\xa9nam"))
                md.artist = _first(tags.get("\xa9ART")) or _first(tags.get("aART"))
                md.album = _first(tags.get("\xa9alb"))
                md.year = _year(_first(tags.get("\xa9day")))
                md.genre = _first(tags.get("\xa9gen"))
                md.tag_bpm = _first(tags.get("tmpo"))
                md.tag_key = _first(tags.get("----:com.apple.iTunes:initialkey"))
            else:  # Vorbis comments (FLAC, OGG, Opus)
                def g(*names):
                    for n in names:
                        try:
                            v = tags.get(n) or tags.get(n.upper())
                        except Exception:
                            v = None
                        if v:
                            return _first(v)
                    return ""
                md.title = g("title")
                md.artist = g("artist", "albumartist")
                md.album = g("album")
                md.year = _year(g("date", "year", "originaldate"))
                md.genre = g("genre")
                md.tag_bpm = g("bpm", "tbpm")
                md.tag_key = g("initialkey", "key")
    if not (md.title and md.artist and md.album and md.year):
        _fill_from_container(path, md)
    if not md.title:
        base = os.path.splitext(os.path.basename(path))[0]
        # "Artist - Title" filename convention
        if " - " in base and not md.artist:
            a, t = base.split(" - ", 1)
            md.artist, md.title = a.strip(), t.strip()
        else:
            md.title = base
    return md


def _fill_from_container(path: str, md: Metadata) -> None:
    """Fallback via FFmpeg/PyAV metadata - covers e.g. RIFF INFO chunks in WAV files."""
    try:
        import av

        with av.open(path) as c:
            meta = {k.lower(): v for k, v in (c.metadata or {}).items()}
            for s in c.streams:
                for k, v in (s.metadata or {}).items():
                    meta.setdefault(k.lower(), v)
            if not md.duration and c.duration:
                md.duration = c.duration / 1_000_000
    except Exception:
        return
    md.title = md.title or meta.get("title", "").strip()
    md.artist = md.artist or (meta.get("artist") or meta.get("album_artist") or "").strip()
    md.album = md.album or meta.get("album", "").strip()
    md.year = md.year or _year(meta.get("date") or meta.get("year") or "")
    md.genre = md.genre or meta.get("genre", "").strip()


def write_bpm_key(path: str, bpm: float | None, key_text: str | None) -> None:
    """Write BPM and key into the file's native tag format."""
    import mutagen
    from mutagen.id3 import ID3, TBPM, TKEY, ID3NoHeaderError

    ext = os.path.splitext(path)[1].lower()
    bpm_txt = str(int(round(bpm))) if bpm else None

    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        if bpm_txt:
            tags.setall("TBPM", [TBPM(encoding=3, text=bpm_txt)])
        if key_text:
            tags.setall("TKEY", [TKEY(encoding=3, text=key_text)])
        tags.save(path)
        return

    if ext == ".wav":
        from mutagen.wave import WAVE

        f = WAVE(path)
        if f.tags is None:
            f.add_tags()
        if bpm_txt:
            f.tags.setall("TBPM", [TBPM(encoding=3, text=bpm_txt)])
        if key_text:
            f.tags.setall("TKEY", [TKEY(encoding=3, text=key_text)])
        f.save()
        return

    if ext in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4, MP4FreeForm

        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        if bpm_txt:
            f.tags["tmpo"] = [int(bpm_txt)]
        if key_text:
            f.tags["----:com.apple.iTunes:initialkey"] = [MP4FreeForm(key_text.encode("utf-8"))]
        f.save()
        return

    # FLAC / OGG Vorbis / Opus: Vorbis comments
    f = mutagen.File(path)
    if f is None:
        raise ValueError(f"unsupported file: {path}")
    if f.tags is None:
        f.add_tags()
    if bpm_txt:
        f.tags["BPM"] = bpm_txt
    if key_text:
        f.tags["INITIALKEY"] = key_text
    f.save()
