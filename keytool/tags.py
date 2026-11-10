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
    comment: str = ""
    energy: int | None = None          # 1..10 (Mixed-In-Key style energy level)
    bitrate: int | None = None         # kbit/s
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


def _energy(text) -> int | None:
    try:
        v = int(float(str(text).strip()))
    except (TypeError, ValueError):
        return None
    return v if 1 <= v <= 10 else None


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
        br = getattr(getattr(f, "info", None), "bitrate", None)
        if br:
            md.bitrate = int(round(br / 1000))
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
                comms = [c for c in tags.getall("COMM") if not c.desc.lower().startswith("itun")]
                md.comment = _first(comms[0]) if comms else ""
                for fr in tags.getall("TXXX"):
                    if fr.desc.lower() in ("energylevel", "energy", "energy level"):
                        md.energy = _energy(_first(fr))
            elif cls in ("MP4Tags",):
                md.title = _first(tags.get("\xa9nam"))
                md.artist = _first(tags.get("\xa9ART")) or _first(tags.get("aART"))
                md.album = _first(tags.get("\xa9alb"))
                md.year = _year(_first(tags.get("\xa9day")))
                md.genre = _first(tags.get("\xa9gen"))
                md.tag_bpm = _first(tags.get("tmpo"))
                md.tag_key = _first(tags.get("----:com.apple.iTunes:initialkey"))
                md.comment = _first(tags.get("\xa9cmt"))
                md.energy = _energy(_first(tags.get("----:com.apple.iTunes:EnergyLevel"))
                                    or _first(tags.get("----:com.apple.iTunes:ENERGYLEVEL")))
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
                md.comment = g("comment", "description")
                md.energy = _energy(g("energylevel", "energy"))
        if md.energy is None and md.comment:   # Mixed In Key writes e.g. "8A - Energy 6" into the comment
            m = re.search(r"Energy\s*(\d{1,2})", md.comment, re.I)
            md.energy = _energy(m.group(1)) if m else None
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


EDITABLE_FIELDS = ("title", "artist", "album", "year", "genre", "comment", "energy")


def write_bpm_key(path: str, bpm: float | None, key_text: str | None) -> None:
    """Write BPM and key into the file's native tag format."""
    write_metadata(path, bpm=bpm, key_text=key_text)


def write_metadata(path: str, title: str | None = None, artist: str | None = None, album: str | None = None,
                   genre: str | None = None, comment: str | None = None, energy: int | None | str = None,
                   bpm: float | None = None, key_text: str | None = None, year: int | None | str = None) -> None:
    """Write the given fields into the file's native tags (ID3 / Vorbis comments / MP4 atoms).

    None = leave the field untouched, "" = remove it (energy / year: 0 or "" removes it).
    """
    import mutagen
    from mutagen.id3 import ID3, COMM, TALB, TBPM, TCON, TDRC, TIT2, TKEY, TPE1, TXXX, ID3NoHeaderError

    ext = os.path.splitext(path)[1].lower()
    bpm_txt = str(int(round(bpm))) if bpm else None
    energy_txt = None if energy is None else (str(int(energy)) if energy not in ("", 0) else "")
    year_txt = None if year is None else (str(int(year)) if year not in ("", 0) else "")

    def id3_apply(tags):
        for frame_id, cls, val in (("TIT2", TIT2, title), ("TPE1", TPE1, artist), ("TALB", TALB, album),
                                   ("TCON", TCON, genre), ("TBPM", TBPM, bpm_txt), ("TKEY", TKEY, key_text)):
            if val is None:
                continue
            tags.delall(frame_id)
            if val != "":
                tags.add(cls(encoding=3, text=val))
        if year_txt is not None:
            # ID3v2.4 recording time; drop the v2.3 year frame so players don't show a stale value
            tags.delall("TDRC"); tags.delall("TYER")
            if year_txt:
                tags.add(TDRC(encoding=3, text=year_txt))
        if comment is not None:
            for c in [c for c in tags.getall("COMM") if not c.desc.lower().startswith("itun")]:
                tags.delall(c.HashKey)
            if comment != "":
                tags.add(COMM(encoding=3, lang="eng", desc="", text=comment))
        if energy_txt is not None:
            for fr in list(tags.getall("TXXX")):
                if fr.desc.lower() in ("energylevel", "energy", "energy level"):
                    tags.delall(fr.HashKey)
            if energy_txt:
                tags.add(TXXX(encoding=3, desc="EnergyLevel", text=energy_txt))

    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        id3_apply(tags)
        tags.save(path)
        return

    if ext == ".wav":
        from mutagen.wave import WAVE

        f = WAVE(path)
        if f.tags is None:
            f.add_tags()
        id3_apply(f.tags)
        f.save()
        return

    if ext in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4, MP4FreeForm

        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        t = f.tags

        def put(atom, val, free=False):
            if val is None:
                return
            if val == "":
                t.pop(atom, None)
            else:
                t[atom] = [MP4FreeForm(val.encode("utf-8"))] if free else [val]

        put("\xa9nam", title); put("\xa9ART", artist); put("\xa9alb", album)
        put("\xa9gen", genre); put("\xa9cmt", comment); put("\xa9day", year_txt)
        put("----:com.apple.iTunes:initialkey", key_text, free=True)
        put("----:com.apple.iTunes:EnergyLevel", energy_txt, free=True)
        if bpm_txt:
            t["tmpo"] = [int(bpm_txt)]
        f.save()
        return

    # FLAC / OGG Vorbis / Opus: Vorbis comments
    f = mutagen.File(path)
    if f is None:
        raise ValueError(f"unsupported file: {path}")
    if f.tags is None:
        f.add_tags()
    if year_txt is not None:
        for k in [k for k in f.tags.keys() if k.upper() == "YEAR"]:
            del f.tags[k]
    for name, val in (("TITLE", title), ("ARTIST", artist), ("ALBUM", album), ("GENRE", genre), ("DATE", year_txt),
                      ("COMMENT", comment), ("ENERGYLEVEL", energy_txt), ("BPM", bpm_txt), ("INITIALKEY", key_text)):
        if val is None:
            continue
        for k in [k for k in f.tags.keys() if k.upper() == name]:
            del f.tags[k]
        if val != "":
            f.tags[name] = val
    f.save()


# ------------------------------------------------------------------ cover artwork
_FOLDER_IMAGES = ("cover", "folder", "front", "album", "albumart", "albumartsmall")


def read_cover(path: str) -> bytes | None:
    """Embedded front cover (ID3 APIC, FLAC/Vorbis picture, MP4 covr) or an image in the same folder
    (cover.jpg, folder.jpg, front.png ...). Returns the raw image bytes or None."""
    data = _embedded_cover(path)
    if data:
        return data
    folder = os.path.dirname(path)
    try:
        files = {f.lower(): f for f in os.listdir(folder)}
    except OSError:
        return None
    for name in _FOLDER_IMAGES:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            f = files.get(name + ext)
            if f:
                try:
                    with open(os.path.join(folder, f), "rb") as fh:
                        return fh.read()
                except OSError:
                    pass
    return None


def _embedded_cover(path: str) -> bytes | None:
    import base64
    import mutagen

    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    # FLAC pictures
    pics = getattr(f, "pictures", None)
    if pics:
        front = [p for p in pics if p.type == 3] or list(pics)
        return front[0].data
    tags = f.tags
    if tags is None:
        return None
    if hasattr(tags, "getall"):                     # ID3 (MP3, WAV)
        apics = tags.getall("APIC")
        if apics:
            front = [a for a in apics if a.type == 3] or apics
            return front[0].data
        return None
    if type(tags).__name__ == "MP4Tags":
        covr = tags.get("covr")
        return bytes(covr[0]) if covr else None
    try:                                            # Vorbis comments (OGG/Opus)
        from mutagen.flac import Picture

        for b64 in tags.get("metadata_block_picture", []) or tags.get("METADATA_BLOCK_PICTURE", []) or []:
            pic = Picture(base64.b64decode(b64))
            if pic.data:
                return pic.data
    except Exception:
        pass
    return None
