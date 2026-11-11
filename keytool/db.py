"""SQLite library storage. Safe to use from the GUI and the MCP server at the same time (WAL)."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT NOT NULL UNIQUE,
    path_key        TEXT,
    folder          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    ext             TEXT NOT NULL,
    size            INTEGER,
    mtime           REAL,
    title           TEXT DEFAULT '',
    artist          TEXT DEFAULT '',
    album           TEXT DEFAULT '',
    album_artist    TEXT,
    year            INTEGER,
    genre           TEXT DEFAULT '',
    duration        REAL,
    bpm             REAL,
    bpm_confidence  REAL,
    key             TEXT,
    key_confidence  REAL,
    bpm_manual      INTEGER DEFAULT 0,
    key_manual      INTEGER DEFAULT 0,
    tag_bpm         TEXT DEFAULT '',
    tag_key         TEXT DEFAULT '',
    bpm_source      TEXT DEFAULT '',
    key_source      TEXT DEFAULT '',
    comment         TEXT DEFAULT '',
    energy          INTEGER,
    bitrate         INTEGER,
    cue_count       INTEGER,            -- Serato cue points in the file; NULL = not read yet
    sections        TEXT,               -- song sections as JSON (structure.py); NULL = not analysed
    status          TEXT DEFAULT 'pending',
    error           TEXT DEFAULT '',
    added_at        REAL,
    analyzed_at     REAL,
    analyzed_ok_at  REAL
);
CREATE INDEX IF NOT EXISTS ix_tracks_bpm ON tracks(bpm);
CREATE INDEX IF NOT EXISTS ix_tracks_key ON tracks(key);
CREATE INDEX IF NOT EXISTS ix_tracks_artist ON tracks(artist COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_tracks_folder ON tracks(folder);

CREATE TABLE IF NOT EXISTS keywords (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    keyword  TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (track_id, keyword)
);
CREATE INDEX IF NOT EXISTS ix_keywords_kw ON keywords(keyword COLLATE NOCASE);
"""

TRACK_COLUMNS = [
    "id", "path", "folder", "filename", "ext", "size", "mtime", "title", "artist", "album", "album_artist", "year",
    "genre", "duration", "bpm", "bpm_confidence", "key", "key_confidence", "bpm_manual", "key_manual",
    "tag_bpm", "tag_key", "bpm_source", "key_source", "comment", "energy", "bitrate", "cue_count", "sections", "status", "error", "added_at", "analyzed_at", "analyzed_ok_at",
]

SORTABLE = {"title", "artist", "album", "year", "bpm", "key", "duration", "added_at", "filename", "genre", "energy",
            "comment", "bitrate", "size", "analyzed_ok_at", "cue_count"}


def norm_path(p: str | os.PathLike) -> str:
    """Absolute, normalised path with its original letter case (what is stored and shown)."""
    return os.path.normpath(os.path.abspath(str(p)))


CASE_INSENSITIVE_PATHS = os.name == "nt"      # Windows file systems ignore letter case


def path_key(p: str | os.PathLike) -> str:
    """Form used to compare paths: case-insensitive on Windows (all Unicode letters, not just A–Z),
    unchanged elsewhere. Stored in tracks.path_key with a unique index."""
    n = norm_path(p)
    return n.casefold() if CASE_INSENSITIVE_PATHS else n


@dataclass
class Query:
    text: str = ""                    # free text: title/artist/album/filename/genre/keywords
    title: str = ""
    artist: str = ""
    album: str = ""
    year_from: int | None = None
    year_to: int | None = None
    bpm_min: float | None = None
    bpm_max: float | None = None
    keys: list[str] = field(default_factory=list)       # canonical key codes
    keywords: list[str] = field(default_factory=list)
    keywords_mode: str = "any"        # any (OR, default) | all (AND)
    status: str | None = None         # pending | ok | error
    folder: str | None = None         # restrict to folder (recursive)
    order_by: str = "artist"
    descending: bool = False
    limit: int | None = None
    offset: int = 0


class Database:
    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        c = self.conn()
        c.executescript(SCHEMA)
        # migrations for libraries created by older versions
        cols = {r[1] for r in c.execute("PRAGMA table_info(tracks)")}
        for col, decl in (("bpm_source", "TEXT DEFAULT ''"), ("key_source", "TEXT DEFAULT ''"),
                          ("comment", "TEXT DEFAULT ''"), ("energy", "INTEGER"), ("bitrate", "INTEGER"),
                          ("analyzed_ok_at", "REAL"),
                          ("album_artist", "TEXT"),       # NULL = not read yet (filled lazily from the file)
                          ("cue_count", "INTEGER"),       # NULL = not read yet (filled in the background)
                          ("sections", "TEXT")):          # NULL = not analysed yet
            if col not in cols:
                c.execute(f"ALTER TABLE tracks ADD COLUMN {col} {decl}")
        # path_key: case-insensitive lookup key on Windows. Older libraries stored the path itself in lower
        # case (os.path.normcase); their rows get a key now and their original casing back on the next scan.
        if "path_key" not in cols:
            c.execute("ALTER TABLE tracks ADD COLUMN path_key TEXT")
        missing = c.execute("SELECT id, path FROM tracks WHERE path_key IS NULL").fetchall()
        if missing:
            c.execute("BEGIN")
            c.executemany("UPDATE tracks SET path_key=? WHERE id=?", [(path_key(r["path"]), r["id"]) for r in missing])
            c.execute("COMMIT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_tracks_path_key ON tracks(path_key)")

    # -------------------------------------------------------------- connection
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    # -------------------------------------------------------------- tracks
    def get_track_by_path(self, path: str) -> dict | None:
        r = self.conn().execute("SELECT * FROM tracks WHERE path_key=?", (path_key(path),)).fetchone()
        return self._row(r) if r else None

    def get_track(self, track_id: int) -> dict | None:
        r = self.conn().execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        return self._row(r) if r else None

    def get_tracks(self, ids: Iterable[int]) -> list[dict]:
        ids = list(ids)
        if not ids:
            return []
        out = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT * FROM tracks WHERE id IN ({','.join('?' * len(chunk))})"
            out += [self._row(r) for r in self.conn().execute(q, chunk)]
        return out

    def upsert_file(self, path: str, size: int, mtime: float, meta: dict) -> int:
        p = norm_path(path)
        c = self.conn()
        row = c.execute("SELECT id FROM tracks WHERE path_key=?", (path_key(p),)).fetchone()
        vals = dict(
            path=p, path_key=path_key(p),            # path keeps the original casing (fixes old lower-case rows)
            folder=os.path.dirname(p), filename=os.path.basename(p), ext=os.path.splitext(p)[1].lower().lstrip("."),
            size=size, mtime=mtime, **meta,
        )
        if row:
            sets = ", ".join(f"{k}=?" for k in vals)
            c.execute(f"UPDATE tracks SET {sets} WHERE id=?", (*vals.values(), row["id"]))
            return int(row["id"])
        vals.update(added_at=time.time(), status="pending")
        cols = ", ".join(vals)
        cur = c.execute(f"INSERT INTO tracks ({cols}) VALUES ({','.join('?' * len(vals))})", tuple(vals.values()))
        return int(cur.lastrowid)

    def ids_without_cue_count(self, limit: int = 200) -> list[tuple[int, str]]:
        """(id, path) of tracks whose cue points were not counted yet."""
        return [(r[0], r[1]) for r in self.conn().execute(
            "SELECT id, path FROM tracks WHERE cue_count IS NULL ORDER BY id LIMIT ?", (limit,))]

    def update_track(self, track_id: int, **fields_: Any) -> None:
        fields_ = {k: v for k, v in fields_.items() if k in TRACK_COLUMNS and k != "id"}
        if not fields_:
            return
        sets = ", ".join(f"{k}=?" for k in fields_)
        self.conn().execute(f"UPDATE tracks SET {sets} WHERE id=?", (*fields_.values(), track_id))

    def set_analysis(self, track_id: int, result: dict, keep_tag_values: bool = True) -> None:
        """Store an analysis result. Manual values are never overwritten; values read from the
        file's tags are kept unless keep_tag_values is False (explicit re-analysis)."""
        t = self.get_track(track_id)
        if not t:
            return
        now = time.time()
        upd: dict[str, Any] = dict(status="ok", error="", analyzed_at=now, analyzed_ok_at=now)  # last successful run
        # fields missing from the result were not analysed (e.g. BPM-only analysis) and stay untouched
        kept = ("tags", "mik")      # from the file tags / imported from Mixed In Key
        if "bpm" in result and not t["bpm_manual"] and not (keep_tag_values and t["bpm_source"] in kept and t["bpm"]):
            upd.update(bpm=result.get("bpm"), bpm_confidence=result.get("bpm_confidence"), bpm_source="analysis")
        if "key" in result and not t["key_manual"] and not (keep_tag_values and t["key_source"] in kept and t["key"]):
            upd.update(key=result.get("key"), key_confidence=result.get("key_confidence"), key_source="analysis")
        if result.get("duration") and not t["duration"]:
            upd["duration"] = result["duration"]
        if result.get("sections") is not None:
            import json
            upd["sections"] = json.dumps(result["sections"], separators=(",", ":"))
        self.update_track(track_id, **upd)

    def set_error(self, track_id: int, message: str) -> None:
        self.update_track(track_id, status="error", error=message[:500], analyzed_at=time.time())

    def delete_tracks(self, ids: Iterable[int]) -> int:
        ids = list(ids)
        c = self.conn()
        n = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            n += c.execute(f"DELETE FROM tracks WHERE id IN ({','.join('?' * len(chunk))})", chunk).rowcount
        return n

    def remove_missing(self) -> int:
        gone = [r["id"] for r in self.conn().execute("SELECT id, path FROM tracks") if not os.path.exists(r["path"])]
        return self.delete_tracks(gone)

    def pending_ids(self) -> list[int]:
        return [r[0] for r in self.conn().execute("SELECT id FROM tracks WHERE status='pending' ORDER BY id")]

    # -------------------------------------------------------------- keywords
    def keywords_for(self, track_ids: Iterable[int]) -> dict[int, list[str]]:
        ids = list(track_ids)
        out: dict[int, list[str]] = {i: [] for i in ids}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT track_id, keyword FROM keywords WHERE track_id IN ({','.join('?' * len(chunk))}) ORDER BY keyword"
            for r in self.conn().execute(q, chunk):
                out[r[0]].append(r[1])
        return out

    def all_keywords(self) -> list[tuple[str, int]]:
        q = "SELECT keyword, COUNT(*) FROM keywords GROUP BY keyword COLLATE NOCASE ORDER BY keyword COLLATE NOCASE"
        return [(r[0], r[1]) for r in self.conn().execute(q)]

    def add_keywords(self, track_ids: Iterable[int], keywords: Iterable[str]) -> None:
        kws = [k.strip() for k in keywords if k and k.strip()]
        c = self.conn()
        c.execute("BEGIN")
        try:
            for tid in track_ids:
                for k in kws:
                    c.execute("INSERT OR IGNORE INTO keywords(track_id, keyword) VALUES (?, ?)", (tid, k))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    def remove_keywords(self, track_ids: Iterable[int], keywords: Iterable[str]) -> None:
        c = self.conn()
        for tid in track_ids:
            for k in keywords:
                c.execute("DELETE FROM keywords WHERE track_id=? AND keyword=? COLLATE NOCASE", (tid, k.strip()))

    def set_keywords(self, track_id: int, keywords: Iterable[str]) -> None:
        c = self.conn()
        c.execute("DELETE FROM keywords WHERE track_id=?", (track_id,))
        self.add_keywords([track_id], keywords)

    def rename_keyword(self, old: str, new: str) -> None:
        c = self.conn()
        c.execute("BEGIN")
        c.execute(
            "INSERT OR IGNORE INTO keywords(track_id, keyword) SELECT track_id, ? FROM keywords WHERE keyword=? COLLATE NOCASE",
            (new.strip(), old),
        )
        c.execute("DELETE FROM keywords WHERE keyword=? COLLATE NOCASE AND keyword<>?", (old, new.strip()))
        c.execute("COMMIT")

    # -------------------------------------------------------------- search
    def search(self, q: Query) -> list[dict]:
        where, params = self._where(q)
        order = q.order_by if q.order_by in SORTABLE else "artist"
        collate = " COLLATE NOCASE" if order in ("title", "artist", "album", "filename", "genre") else ""
        direction = "DESC" if q.descending else "ASC"
        sql = f"SELECT * FROM tracks t {where} ORDER BY t.{order} IS NULL, t.{order}{collate} {direction}, t.title COLLATE NOCASE"
        if q.limit:
            sql += f" LIMIT {int(q.limit)} OFFSET {int(q.offset)}"
        rows = [self._row(r) for r in self.conn().execute(sql, params)]
        kw = self.keywords_for([r["id"] for r in rows])
        for r in rows:
            r["keywords"] = kw.get(r["id"], [])
        return rows

    def count(self, q: Query | None = None) -> int:
        where, params = self._where(q or Query())
        return int(self.conn().execute(f"SELECT COUNT(*) FROM tracks t {where}", params).fetchone()[0])

    def _where(self, q: Query) -> tuple[str, list]:
        cl: list[str] = []
        p: list[Any] = []

        def like(col: str, val: str):
            cl.append(f"t.{col} LIKE ? ESCAPE '\\'")
            p.append("%" + val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")

        if q.text:
            for word in q.text.split():
                pat = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                cl.append(
                    "(t.title LIKE ? ESCAPE '\\' OR t.artist LIKE ? ESCAPE '\\' OR t.album LIKE ? ESCAPE '\\' "
                    "OR t.filename LIKE ? ESCAPE '\\' OR t.genre LIKE ? ESCAPE '\\' OR t.comment LIKE ? ESCAPE '\\' "
                    "OR CAST(t.year AS TEXT)=? "
                    "OR EXISTS (SELECT 1 FROM keywords k WHERE k.track_id=t.id AND k.keyword LIKE ? ESCAPE '\\'))"
                )
                p += [pat] * 6 + [word, pat]
        if q.title:
            like("title", q.title)
        if q.artist:
            like("artist", q.artist)
        if q.album:
            like("album", q.album)
        if q.year_from is not None:
            cl.append("t.year >= ?"); p.append(q.year_from)
        if q.year_to is not None:
            cl.append("t.year <= ?"); p.append(q.year_to)
        if q.bpm_min is not None:
            cl.append("t.bpm >= ?"); p.append(q.bpm_min)
        if q.bpm_max is not None:
            cl.append("t.bpm <= ?"); p.append(q.bpm_max)
        if q.keys:
            cl.append(f"t.key IN ({','.join('?' * len(q.keys))})"); p += list(q.keys)
        if q.keywords:
            kws = [k.strip() for k in q.keywords if k.strip()]
            if kws:
                ph = ",".join("?" * len(kws))
                if q.keywords_mode == "any":
                    cl.append(f"EXISTS (SELECT 1 FROM keywords k WHERE k.track_id=t.id AND k.keyword IN ({ph}))")
                    p += kws
                else:
                    cl.append(
                        f"(SELECT COUNT(DISTINCT k.keyword COLLATE NOCASE) FROM keywords k WHERE k.track_id=t.id "
                        f"AND k.keyword IN ({ph})) = ?"
                    )
                    p += kws + [len({k.lower() for k in kws})]
        if q.status:
            cl.append("t.status = ?"); p.append(q.status)
        if q.folder:
            k = path_key(q.folder).rstrip(os.sep) + os.sep      # everything below this folder
            cl.append("substr(t.path_key, 1, ?) = ?")
            p += [len(k), k]
        return ("WHERE " + " AND ".join(cl)) if cl else "", p

    def distinct_values(self, column: str) -> list[str]:
        if column not in ("genre", "artist", "album"):
            raise ValueError(column)
        q = f"SELECT DISTINCT {column} FROM tracks WHERE {column}<>'' ORDER BY {column} COLLATE NOCASE"
        return [r[0] for r in self.conn().execute(q)]

    # -------------------------------------------------------------- stats
    def stats(self) -> dict:
        c = self.conn()
        r = c.execute(
            "SELECT COUNT(*), SUM(status='ok'), SUM(status='pending'), SUM(status='error'), "
            "MIN(bpm), MAX(bpm), AVG(bpm), SUM(duration) FROM tracks"
        ).fetchone()
        keys = {row[0]: row[1] for row in c.execute("SELECT key, COUNT(*) FROM tracks WHERE key IS NOT NULL GROUP BY key")}
        return dict(
            total=r[0] or 0, analyzed=r[1] or 0, pending=r[2] or 0, errors=r[3] or 0,
            bpm_min=r[4], bpm_max=r[5], bpm_avg=round(r[6], 2) if r[6] else None,
            total_duration_s=round(r[7] or 0), key_distribution=keys,
            artists=c.execute("SELECT COUNT(DISTINCT artist COLLATE NOCASE) FROM tracks WHERE artist<>''").fetchone()[0],
            albums=c.execute("SELECT COUNT(DISTINCT album COLLATE NOCASE) FROM tracks WHERE album<>''").fetchone()[0],
            keywords=c.execute("SELECT COUNT(DISTINCT keyword COLLATE NOCASE) FROM keywords").fetchone()[0],
        )

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        return {k: r[k] for k in r.keys()}
