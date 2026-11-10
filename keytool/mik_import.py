"""Import key, tempo, energy and collections from a Mixed In Key 10/11 library (MIKStore.db, SQLite).

Mixed In Key keeps its results in %LOCALAPPDATA%\\Mixed In Key\\Mixed In Key\\<version>\\MIKStore.db. Relevant tables:
  Song(Id, File, ArtistName, SongName, Tempo, OverallEnergy, MainKey, MainKeyConfidence, LastAnalyzedUtc, IsAnalyzed)
  Collection(Id, Name, LibraryTypeId, IsLibrary)        user collections; IsLibrary=1 are the built-in roots
  SongCollectionMembership(SongId, CollectionId)
MainKey is already Camelot notation ("8A"). The database is copied before reading, so Mixed In Key may stay open
and is never modified.
"""
from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field

from .db import norm_path
from .keys import Key

SOURCE = "mik"          # value stored in bpm_source / key_source


@dataclass
class MikSong:
    file: str
    artist: str = ""
    title: str = ""
    bpm: float | None = None
    key: str | None = None                 # KeyTool key code ("9:min")
    key_confidence: float | None = None
    energy: int | None = None
    collections: list[str] = field(default_factory=list)


@dataclass
class ImportPlan:
    songs: list[MikSong]
    matched: dict[int, MikSong]            # KeyTool track id -> MIK song
    missing_existing: list[MikSong]        # not in the library, file exists -> can be added
    missing_gone: list[MikSong]            # not in the library, file no longer exists
    by_filename: int = 0                   # how many of `matched` were found via the file name only
    library_gone: list[MikSong] = field(default_factory=list)   # in the library, but the file no longer exists

    @property
    def gone(self) -> int:
        """Tracks skipped because their file does not exist (any more)."""
        return len(self.missing_gone) + len(self.library_gone)


@dataclass
class ImportResult:
    updated: int = 0
    added: int = 0
    keywords: int = 0
    skipped_manual: int = 0
    not_found: int = 0


def default_db_paths() -> list[str]:
    """MIKStore.db files of installed Mixed In Key versions, newest version first."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    found = glob.glob(os.path.join(base, "Mixed In Key", "Mixed In Key", "*", "MIKStore.db"))

    def version(p: str):
        try:
            return tuple(int(x) for x in os.path.basename(os.path.dirname(p)).split("."))
        except ValueError:
            return (0,)
    return sorted(found, key=version, reverse=True)


def _path_key(p: str) -> str:
    """Comparable form of a Windows or POSIX path (Windows paths are case-insensitive)."""
    p = str(p)
    if len(p) > 2 and p[1] == ":" or "\\" in p:
        return p.replace("/", "\\").lower()
    return p


def read_mik(db_file: str) -> list[MikSong]:
    """Read all analysed songs (with their user collections) from a MIKStore.db."""
    tmp = tempfile.mkdtemp(prefix="keytool-mik-")
    copy = os.path.join(tmp, "MIKStore.db")
    try:
        shutil.copyfile(db_file, copy)                     # never touch the live file
        for ext in ("-wal", "-shm"):
            if os.path.exists(db_file + ext):
                shutil.copyfile(db_file + ext, copy + ext)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "Song" not in tables:
                raise ValueError("not a Mixed In Key library (table 'Song' missing)")
            cols = {r[1] for r in con.execute("PRAGMA table_info(Song)")}
            analysed = "WHERE IsAnalyzed = 1" if "IsAnalyzed" in cols else ""
            collections: dict[str, list[str]] = {}
            if {"Collection", "SongCollectionMembership"} <= tables:
                for song_id, name in con.execute(
                        "SELECT m.SongId, c.Name FROM SongCollectionMembership m "
                        "JOIN Collection c ON c.Id = m.CollectionId WHERE IFNULL(c.IsLibrary, 0) = 0"):
                    if name and name.strip():
                        collections.setdefault(song_id, []).append(name.strip())
            out = []
            for sid, f, artist, title, tempo, energy, mkey, conf in con.execute(
                    f"SELECT Id, File, ArtistName, SongName, Tempo, OverallEnergy, MainKey, MainKeyConfidence "
                    f"FROM Song {analysed}"):
                if not f:
                    continue
                k = Key.parse(mkey) if mkey else None
                out.append(MikSong(
                    file=f, artist=artist or "", title=title or "",
                    bpm=round(float(tempo), 2) if tempo and 30 <= float(tempo) <= 300 else None,
                    key=k.code if k else None,
                    key_confidence=float(conf) if conf is not None else None,
                    energy=int(energy) if energy and 1 <= int(energy) <= 10 else None,
                    collections=sorted(set(collections.get(sid, []))),
                ))
            return out
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def plan_import(db, songs: list[MikSong]) -> ImportPlan:
    """Match MIK songs to library tracks: exact path first, then a unique file name."""
    rows = db.conn().execute("SELECT id, path, filename FROM tracks").fetchall()
    by_path = {_path_key(r["path"]): r["id"] for r in rows}
    path_of = {r["id"]: r["path"] for r in rows}
    names: dict[str, list[int]] = {}
    for r in rows:
        names.setdefault((r["filename"] or "").lower(), []).append(r["id"])
    mik_names: dict[str, int] = {}
    for s in songs:
        n = _file_name(s.file).lower()
        mik_names[n] = mik_names.get(n, 0) + 1

    plan = ImportPlan(songs=songs, matched={}, missing_existing=[], missing_gone=[])
    for s in songs:
        tid = by_path.get(_path_key(s.file)) or by_path.get(_path_key(norm_path(s.file)))
        if tid is None:
            n = _file_name(s.file).lower()
            cands = names.get(n, [])
            if len(cands) == 1 and mik_names.get(n) == 1:   # unambiguous on both sides
                tid = cands[0]
                plan.by_filename += 1
        if tid is not None:
            if os.path.exists(path_of[tid]):
                plan.matched[tid] = s
            else:
                plan.library_gone.append(s)                 # library entry whose file is gone: skip
        elif os.path.exists(s.file):
            plan.missing_existing.append(s)
        else:
            plan.missing_gone.append(s)
    return plan


def _file_name(p: str) -> str:
    return p.replace("\\", "/").rsplit("/", 1)[-1]


def apply_import(lib, plan: ImportPlan, add_missing: bool = False, collections_as_keywords: bool = True,
                 overwrite: bool = True, cancel=None) -> ImportResult:
    """Store MIK values in the library.

    overwrite=True replaces KeyTool's own analysis and tag values, False only fills empty fields. Values set by
    hand are never replaced. Collections become keywords. With add_missing, files only known to Mixed In Key are
    added to the library first (without queueing an analysis - MIK already has the values). Tracks whose file
    does not exist are ignored, both MIK-only songs and library entries; this is checked again here."""
    res = ImportResult(not_found=plan.gone + (0 if add_missing else len(plan.missing_existing)))
    matched = dict(plan.matched)
    # files can disappear between the preview and the import: check again, skip what is gone
    to_add = []
    if add_missing:
        to_add = [s for s in plan.missing_existing if os.path.exists(s.file)]
        res.not_found += len(plan.missing_existing) - len(to_add)
    if to_add:
        lib.scan([s.file for s in to_add], cancel=cancel)
        for s in to_add:
            t = lib.db.get_track_by_path(norm_path(s.file))
            if t:
                matched[t["id"]] = s
                res.added += 1
    db = lib.db
    keyword_map: dict[str, list[int]] = {}
    con = db.conn()
    con.execute("BEGIN")                                    # one transaction: fast for 10 000+ tracks
    try:
        for tid, s in matched.items():
            if cancel is not None and cancel.is_set():
                break
            t = db.get_track(tid)
            if not t:
                continue
            if not os.path.exists(t["path"]):
                res.not_found += 1
                continue
            upd: dict = {}
            if s.bpm and not t["bpm_manual"] and (overwrite or not t["bpm"]):
                upd.update(bpm=s.bpm, bpm_confidence=None, bpm_source=SOURCE)
            elif s.bpm and t["bpm_manual"]:
                res.skipped_manual += 1
            if s.key and not t["key_manual"] and (overwrite or not t["key"]):
                upd.update(key=s.key, key_confidence=s.key_confidence, key_source=SOURCE)
            elif s.key and t["key_manual"]:
                res.skipped_manual += 1
            if s.energy and (overwrite or not t["energy"]):
                upd["energy"] = s.energy
            have_bpm = upd.get("bpm") or t["bpm"]
            have_key = upd.get("key") or t["key"]
            if have_bpm and have_key and t["status"] != "ok":
                upd.update(status="ok", error="")           # nothing left to analyse
            if upd:
                db.update_track(tid, **upd)
                res.updated += 1
            if collections_as_keywords:
                for name in s.collections:
                    keyword_map.setdefault(name, []).append(tid)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    for name, ids in keyword_map.items():
        db.add_keywords(ids, [name])
        res.keywords += len(ids)
    return res
