"""CSV export of library tracks (GUI: the selected tracks; MCP: the whole library).

The file opens directly in Excel / Numbers / LibreOffice: UTF-8 with BOM, one header row (English column names,
stable for scripts), the delimiter from the settings ("auto": a semicolon and decimal commas for languages that
write 124,5 – what Excel expects there – else a comma). Which columns are written, and in which order, is a
setting (Settings.export_columns); COLUMNS lists everything that can be exported.
"""
from __future__ import annotations

import csv
import json
import os
import time
from typing import Callable

from .keys import Key

# id -> (header in the file, i18n key for the settings list)
COLUMNS: dict[str, tuple[str, str]] = {
    "artist": ("Artist", "artist"),
    "title": ("Title", "title"),
    "album": ("Album", "album"),
    "album_artist": ("Album artist", "album_artist"),
    "year": ("Year", "year"),
    "genre": ("Genre", "genre"),
    "comment": ("Comment", "comment"),
    "bpm": ("BPM", "x_bpm"),
    "bpm_exact": ("BPM (exact)", "x_bpm_exact"),
    "key": ("Key", "x_key"),
    "camelot": ("Camelot", "n_camelot"),
    "openkey": ("Open Key", "n_openkey"),
    "key_name": ("Key name", "x_key_name"),
    "energy": ("Energy", "x_energy"),
    "duration": ("Duration", "x_duration"),
    "cues": ("Cue points", "x_cues"),
    "sections": ("Song sections", "x_sections"),
    "keywords": ("Keywords", "keywords"),
    "filename": ("File name", "x_filename"),
    "folder": ("Folder", "x_folder"),
    "path": ("Path", "x_path"),
    "format": ("Format", "x_format"),
    "bitrate": ("Bitrate (kbps)", "x_bitrate"),
    "size_mb": ("Size (MB)", "x_size"),
    "bpm_source": ("BPM source", "x_bpm_source"),
    "key_source": ("Key source", "x_key_source"),
    "added": ("Added", "x_added"),
    "analyzed": ("Analysed", "x_analyzed"),
}
DEFAULT_COLUMNS = ["artist", "title", "album", "year", "genre", "bpm", "key", "camelot", "energy", "duration",
                   "keywords", "path"]
DELIMITERS = {"auto": None, "comma": ",", "semicolon": ";", "tab": "\t"}
DEFAULT_FILENAME = "KeyTool-{date}.csv"
DECIMAL_COMMA = {"de", "es", "fr", "it"}


def resolve_delimiter(setting: str, lang: str) -> str:
    d = DELIMITERS.get(setting)
    if d:
        return d
    return ";" if lang in DECIMAL_COMMA else ","


def valid_columns(columns: list[str] | None) -> list[str]:
    """Known column ids in the given order, without duplicates; the defaults if nothing is left."""
    out = []
    for c in columns or []:
        if c in COLUMNS and c not in out:
            out.append(c)
    return out or list(DEFAULT_COLUMNS)


def default_filename(pattern: str | None, count: int) -> str:
    """File name from the pattern: {date} = 2026-11-12, {time} = 1405, {count} = number of tracks."""
    name = (pattern or DEFAULT_FILENAME).strip() or DEFAULT_FILENAME
    try:
        name = name.format(date=time.strftime("%Y-%m-%d"), time=time.strftime("%H%M"), count=count)
    except (KeyError, IndexError, ValueError):
        pass                                       # unknown placeholder: keep the text as it is
    name = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in name)
    return name if name.lower().endswith(".csv") else name + ".csv"


def _mmss(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _sections_text(value) -> str:
    try:
        secs = json.loads(value) if isinstance(value, str) else (value or [])
    except (TypeError, ValueError):
        return ""
    from .i18n import _T

    def name(s):                    # own name, else the English label ("Pre-chorus")
        return s.get("name") or _T.get(f"sec_{s.get('label')}", ("", s.get("label") or ""))[1]
    return ", ".join(f"{name(s)} {_mmss(s.get('start')) or '0:00'}" for s in secs)


def _date(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def row_values(t: dict, columns: list[str], notation: str = "camelot", decimal_comma: bool = False) -> list[str]:
    k = Key.from_code(t.get("key"))

    def num(v, digits):
        if v is None or v == "":
            return ""
        s = f"{float(v):.{digits}f}"
        return s.replace(".", ",") if decimal_comma else s

    getters: dict[str, Callable[[], object]] = {
        "artist": lambda: t.get("artist") or "",
        "title": lambda: t.get("title") or t.get("filename") or "",
        "album": lambda: t.get("album") or "",
        "album_artist": lambda: t.get("album_artist") or "",
        "year": lambda: t.get("year") or "",
        "genre": lambda: t.get("genre") or "",
        "comment": lambda: t.get("comment") or "",
        "bpm": lambda: str(int(round(t["bpm"]))) if t.get("bpm") else "",
        "bpm_exact": lambda: num(t.get("bpm"), 2),
        "key": lambda: k.format(notation) if k else "",
        "camelot": lambda: k.format("camelot") if k else "",
        "openkey": lambda: k.format("openkey") if k else "",
        "key_name": lambda: k.long_name("en") if k else "",
        "energy": lambda: t.get("energy") or "",
        "duration": lambda: _mmss(t.get("duration")),
        "cues": lambda: "" if t.get("cue_count") is None else t["cue_count"],
        "sections": lambda: _sections_text(t.get("sections")),
        "keywords": lambda: ", ".join(t.get("keywords") or []),
        "filename": lambda: t.get("filename") or os.path.basename(t.get("path") or ""),
        "folder": lambda: t.get("folder") or os.path.dirname(t.get("path") or ""),
        "path": lambda: t.get("path") or "",
        "format": lambda: (t.get("ext") or "").lstrip(".").upper(),
        "bitrate": lambda: t.get("bitrate") or "",
        "size_mb": lambda: num(t["size"] / 1_000_000, 1) if t.get("size") else "",
        "bpm_source": lambda: t.get("bpm_source") or "",
        "key_source": lambda: t.get("key_source") or "",
        "added": lambda: _date(t.get("added_at")),
        "analyzed": lambda: _date(t.get("analyzed_ok_at")),
    }
    return [str(getters[c]()) for c in columns]


def write_csv(path: str, tracks: list[dict], columns: list[str] | None = None, delimiter: str = ",",
              notation: str = "camelot", decimal_comma: bool | None = None) -> int:
    """Write the tracks to `path` (replaced as a whole: written to a temporary file first). Returns the number
    of tracks. Asking before replacing an existing file is the caller's job (the GUI asks, MCP needs overwrite)."""
    columns = valid_columns(columns)
    if decimal_comma is None:
        decimal_comma = delimiter != ","
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
            w.writerow([COLUMNS[c][0] for c in columns])
            for t in tracks:
                w.writerow(row_values(t, columns, notation, decimal_comma))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return len(tracks)
