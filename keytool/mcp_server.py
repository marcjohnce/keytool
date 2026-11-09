"""MCP server for the KeyTool library (full access).

Start with ``python -m keytool --mcp`` (stdio) or ``KeyTool.exe --mcp``.
It uses the same SQLite database as the GUI, so both can run at the same time.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

try:  # MCP Python SDK >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .config import Settings
from .db import Query
from .keys import NOTATIONS, Key, all_keys
from .library import Library

INSTRUCTIONS = """KeyTool manages a local music library with detected BPM and musical key (Camelot, sharps, flats, Open Key)
plus user keywords. Use search_tracks to find music (filters can be combined), compatible_tracks for harmonic
mixing suggestions, analyze_paths to import/analyze files or whole folders (MP3, WAV, FLAC, OGG, M4A), and the
keyword tools to tag tracks. Keys can be given in any notation ("8A", "Am", "A minor", "F#m", "Gbm", "1m")."""

mcp = _Server("keytool", instructions=INSTRUCTIONS)

_lib: Library | None = None
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def lib() -> Library:
    global _lib
    if _lib is None:
        _lib = Library()
    _lib.settings = Settings.load()   # pick up changes made in the GUI
    return _lib


def _track_out(r: dict, notation: str = "camelot", full: bool = False) -> dict:
    k = Key.from_code(r.get("key"))
    out: dict[str, Any] = {
        "id": r["id"], "title": r["title"], "artist": r["artist"], "album": r["album"], "year": r["year"],
        "bpm": r["bpm"], "key": k.format(notation) if k else None,
        "keywords": r.get("keywords", []), "path": r["path"],
    }
    if full:
        out.update(
            genre=r["genre"], duration_s=r["duration"], format=r["ext"], status=r["status"], error=r["error"] or None,
            bpm_confidence=r["bpm_confidence"], key_confidence=r["key_confidence"],
            bpm_manual=bool(r["bpm_manual"]), key_manual=bool(r["key_manual"]),
            bpm_source=r.get("bpm_source") or None, key_source=r.get("key_source") or None,
            key_all_notations={n: k.format(n) for n in NOTATIONS} | {"name": k.long_name()} if k else None,
            tag_bpm=r["tag_bpm"], tag_key=r["tag_key"],
        )
    return out


def _parse_keys(keys: list[str] | None) -> list[str]:
    codes = []
    for t in keys or []:
        k = Key.parse(t)
        if not k:
            raise ValueError(f"Unknown key: {t!r}")
        codes.append(k.code)
    return codes


def _check_notation(notation: str) -> str:
    if notation not in NOTATIONS:
        raise ValueError(f"notation must be one of {NOTATIONS}")
    return notation


# ============================================================================ search & read
@mcp.tool()
def search_tracks(
    text: str = "",
    title: str = "",
    artist: str = "",
    album: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    keys: list[str] | None = None,
    keywords: list[str] | None = None,
    keywords_mode: str = "all",
    status: str | None = None,
    folder: str | None = None,
    order_by: str = "artist",
    descending: bool = False,
    limit: int = 50,
    offset: int = 0,
    notation: str = "camelot",
) -> dict:
    """Search the library. All filters are optional and combined with AND.

    text: free text over title, artist, album, filename, genre, year and keywords.
    title/artist/album: substring match. keys: list of keys in any notation (matches any of them).
    keywords: user keywords; keywords_mode 'all' (track must have every keyword) or 'any'.
    status: 'ok', 'pending' or 'error'. folder: only tracks below this folder.
    order_by: title|artist|album|year|bpm|key|duration|added_at|filename|genre.
    notation for the returned key: camelot|sharps|flats|openkey.
    """
    _check_notation(notation)
    q = Query(text=text, title=title, artist=artist, album=album, year_from=year_from, year_to=year_to,
              bpm_min=bpm_min, bpm_max=bpm_max, keys=_parse_keys(keys), keywords=keywords or [],
              keywords_mode=keywords_mode, status=status, folder=folder, order_by=order_by, descending=descending)
    L = lib()
    total = L.db.count(q)
    q.limit, q.offset = max(1, min(int(limit), 1000)), max(0, int(offset))
    rows = L.search(q)
    return {"total": total, "offset": q.offset, "returned": len(rows), "tracks": [_track_out(r, notation) for r in rows]}


@mcp.tool()
def get_track(track_id: int | None = None, path: str | None = None, notation: str = "camelot") -> dict:
    """Full details of one track by id or file path."""
    _check_notation(notation)
    L = lib()
    r = L.db.get_track(track_id) if track_id is not None else (L.db.get_track_by_path(path) if path else None)
    if not r:
        raise ValueError("Track not found")
    r["keywords"] = L.db.keywords_for([r["id"]])[r["id"]]
    return _track_out(r, notation, full=True)


@mcp.tool()
def compatible_tracks(track_id: int, bpm_tolerance_percent: float = 6.0, limit: int = 30, notation: str = "camelot") -> dict:
    """Tracks that mix harmonically with the given track (same key, Camelot +-1, relative major/minor)
    within a BPM tolerance in percent. Sorted: same key first, then closest BPM."""
    _check_notation(notation)
    L = lib()
    t = L.db.get_track(track_id)
    if not t:
        raise ValueError("Track not found")
    rows = L.compatible_tracks(track_id, bpm_tolerance_percent, limit)
    k = Key.from_code(t["key"])
    return {"reference": _track_out(t, notation), "compatible_keys": [c.format(notation) for c in k.compatible()] if k else [],
            "tracks": [_track_out(r, notation) for r in rows]}


@mcp.tool()
def library_stats(notation: str = "camelot") -> dict:
    """Totals, analysis status, BPM range and key distribution of the library."""
    _check_notation(notation)
    s = lib().db.stats()
    s["key_distribution"] = {Key.from_code(c).format(notation): n for c, n in sorted(
        s["key_distribution"].items(), key=lambda kv: (Key.from_code(kv[0]).camelot_number, not Key.from_code(kv[0]).minor))}
    return s


@mcp.tool()
def convert_key(key: str) -> dict:
    """Convert a key between notations (Camelot, sharps, flats, Open Key) and list compatible keys."""
    k = Key.parse(key)
    if not k:
        raise ValueError(f"Unknown key: {key!r}")
    return {n: k.format(n) for n in NOTATIONS} | {
        "name_en": k.long_name("en"), "name_de": k.long_name("de"),
        "relative": k.relative().camelot(), "compatible_camelot": [c.camelot() for c in k.compatible()],
    }


@mcp.tool()
def list_all_keys() -> list[dict]:
    """All 24 keys in Camelot wheel order with every notation."""
    return [{n: k.format(n) for n in NOTATIONS} | {"name": k.long_name()} for k in all_keys()]


# ============================================================================ keywords
@mcp.tool()
def list_keywords() -> list[dict]:
    """All user keywords with the number of tracks using each."""
    return [{"keyword": k, "count": n} for k, n in lib().db.all_keywords()]


@mcp.tool()
def add_keywords(track_ids: list[int], keywords: list[str]) -> dict:
    """Add keywords to one or more tracks (existing keywords are kept)."""
    lib().db.add_keywords(track_ids, keywords)
    return {"updated": len(track_ids), "keywords": keywords}


@mcp.tool()
def remove_keywords(track_ids: list[int], keywords: list[str]) -> dict:
    """Remove keywords from one or more tracks."""
    lib().db.remove_keywords(track_ids, keywords)
    return {"updated": len(track_ids)}


@mcp.tool()
def set_keywords(track_id: int, keywords: list[str]) -> dict:
    """Replace all keywords of a track."""
    lib().db.set_keywords(track_id, keywords)
    return {"track_id": track_id, "keywords": keywords}


@mcp.tool()
def rename_keyword(old: str, new: str) -> dict:
    """Rename a keyword on every track."""
    lib().db.rename_keyword(old, new)
    return {"renamed": old, "to": new}


# ============================================================================ edits
@mcp.tool()
def set_bpm(track_id: int, bpm: float | None, write_tags: bool | None = None) -> dict:
    """Manually set (or clear with null) the BPM of a track. Manual values survive re-analysis.
    write_tags: write into the file (default: app setting)."""
    lib().set_bpm(track_id, bpm, write_tags)
    return get_track(track_id)


@mcp.tool()
def set_key(track_id: int, key: str | None, write_tags: bool | None = None) -> dict:
    """Manually set (or clear with null) the key of a track, in any notation."""
    code = None
    if key:
        k = Key.parse(key)
        if not k:
            raise ValueError(f"Unknown key: {key!r}")
        code = k.code
    lib().set_key(track_id, code, write_tags)
    return get_track(track_id)


@mcp.tool()
def write_tags(track_ids: list[int]) -> dict:
    """Write the stored BPM and key into the audio files' tags."""
    L = lib()
    errors = {}
    for tid in track_ids:
        e = L._write_tags_safe(tid)
        if e:
            errors[tid] = e
    return {"written": len(track_ids) - len(errors), "errors": errors}


@mcp.tool()
def remove_tracks(track_ids: list[int]) -> dict:
    """Remove tracks from the library database. Audio files are NOT deleted."""
    return {"removed": lib().db.delete_tracks(track_ids)}


@mcp.tool()
def remove_missing_files() -> dict:
    """Remove library entries whose files no longer exist on disk."""
    return {"removed": lib().db.remove_missing()}


# ============================================================================ analysis jobs
def _run_job(job_id: str, paths: list[str] | None, ids: list[int] | None):
    job = _jobs[job_id]
    L = Library(settings=Settings.load())

    def prog(done, total, row):
        job.update(done=done, total=total, current=row.get("path") if row else None)

    try:
        if paths is not None:
            job["phase"] = "scanning"
            rep = L.scan(paths, job["cancel"])
            job.update(phase="analyzing", found=rep.found, added=rep.added, unchanged=rep.unchanged,
                       from_tags=rep.from_tags, total=len(rep.to_analyze or []))
            res = L.analyze(rep.to_analyze or [], prog, job["cancel"])
        else:
            job.update(phase="analyzing", total=len(ids or []))
            res = L.analyze(ids or [], prog, job["cancel"], keep_tag_values=False)
        job.update(result=res, phase="cancelled" if res.get("cancelled") else "finished")
    except Exception as e:
        job.update(phase="failed", error=str(e))
    finally:
        job["finished_at"] = time.time()
        L.db.close()


def _start_job(paths: list[str] | None, ids: list[int] | None, wait_seconds: float) -> dict:
    with _jobs_lock:
        running = [j for j in _jobs.values() if j["phase"] in ("queued", "scanning", "analyzing")]
        if running:
            raise RuntimeError(f"Job {running[0]['id']} is still running; check get_job_status")
        jid = uuid.uuid4().hex[:8]
        _jobs[jid] = dict(id=jid, phase="queued", done=0, total=0, started_at=time.time(), cancel=threading.Event())
    threading.Thread(target=_run_job, args=(jid, paths, ids), daemon=True).start()
    deadline = time.time() + max(0.0, wait_seconds)
    while time.time() < deadline and _jobs[jid]["phase"] in ("queued", "scanning", "analyzing"):
        time.sleep(0.5)
    return get_job_status(jid)


@mcp.tool()
def analyze_paths(paths: list[str], wait_seconds: float = 60) -> dict:
    """Import and analyze audio files and/or folders (recursively). Supported: MP3, WAV, FLAC, OGG, M4A.
    Unchanged, already analyzed files are skipped. Files that already carry BPM and key tags are not
    analyzed; the tag values are taken over (setting 'use_existing_tags'). Runs in the background; waits up to wait_seconds and
    returns the job status. Poll get_job_status for long imports."""
    return _start_job(list(paths), None, wait_seconds)


@mcp.tool()
def reanalyze_tracks(track_ids: list[int] | None = None, pending_and_errors: bool = False, wait_seconds: float = 60) -> dict:
    """Re-run the analysis for the given track ids, or for all pending/failed tracks if pending_and_errors=true.
    This always analyzes the audio, also for values that were taken from file tags (manual values are kept)."""
    L = lib()
    ids = list(track_ids or [])
    if pending_and_errors:
        ids += L.db.pending_ids() + [r["id"] for r in L.search(Query(status="error"))]
    if not ids:
        raise ValueError("No tracks given")
    return _start_job(None, sorted(set(ids)), wait_seconds)


@mcp.tool()
def get_job_status(job_id: str | None = None) -> dict:
    """Progress of an analysis job (latest job if job_id is omitted)."""
    if not _jobs:
        return {"phase": "none"}
    j = _jobs[job_id] if job_id else max(_jobs.values(), key=lambda x: x["started_at"])
    return {k: v for k, v in j.items() if k != "cancel"}


@mcp.tool()
def cancel_job(job_id: str | None = None) -> dict:
    """Cancel the running analysis job."""
    for j in _jobs.values():
        if (job_id is None or j["id"] == job_id) and j["phase"] in ("queued", "scanning", "analyzing"):
            j["cancel"].set()
            return {"cancelling": j["id"]}
    return {"cancelling": None}


def run(http: bool = False) -> None:
    if http:
        mcp.run("streamable-http")
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    run()
