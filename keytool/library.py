"""Library service: scanning folders, parallel analysis, tag writing.

Used by both the GUI and the MCP server.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Callable, Iterable

from .config import Settings, db_path
from .db import Database, Query, norm_path
from .keys import Key
from .tags import is_audio_file, read_metadata, write_bpm_key

ProgressCB = Callable[[int, int, dict | None], None]   # (done, total, track_row)


CRASH_MESSAGE = ("Analyse-Prozess abgestürzt (Datei beschädigt oder zu wenig Arbeitsspeicher) / "
                 "analysis process crashed (corrupt file or out of memory)")


def _worker_init() -> None:
    # One BLAS/OpenMP/numba thread per worker: the pool already runs one process per core.
    # Oversubscription multiplies memory use and is a common cause of crashed workers.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS"):
        os.environ[var] = "1"


def _analyze_worker(path: str, bpm_min: float, bpm_max: float) -> dict:
    # Imported lazily so the GUI process starts fast and workers are self-contained
    from .analysis import analyze_file

    return analyze_file(path, bpm_min, bpm_max).to_dict()


def _make_pool(workers: int) -> ProcessPoolExecutor:
    ctx = multiprocessing.get_context("spawn")  # same behaviour on every OS; fork + Qt is unsafe
    kw = dict(max_workers=workers, mp_context=ctx, initializer=_worker_init)
    if sys.version_info >= (3, 11):
        kw["max_tasks_per_child"] = 25          # recycle workers -> no memory creep on big libraries
    return ProcessPoolExecutor(**kw)


def iter_audio_files(paths: Iterable[str]) -> Iterable[str]:
    for p in paths:
        p = os.path.abspath(os.path.expanduser(str(p)))
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort(key=str.lower)
                for f in sorted(files, key=str.lower):
                    full = os.path.join(root, f)
                    if is_audio_file(full):
                        yield full
        elif os.path.isfile(p) and is_audio_file(p):
            yield p


@dataclass
class ScanReport:
    found: int = 0
    added: int = 0
    unchanged: int = 0
    to_analyze: list[int] | None = None


class Library:
    worker_fn = staticmethod(_analyze_worker)   # top-level function run in the worker processes

    def __init__(self, db: Database | None = None, settings: Settings | None = None):
        self.db = db or Database(db_path())
        self.settings = settings or Settings.load()
        self._lock = threading.Lock()

    # ----------------------------------------------------------- scanning
    def scan(self, paths: Iterable[str], cancel: threading.Event | None = None,
             progress: Callable[[int, str], None] | None = None) -> ScanReport:
        rep = ScanReport(to_analyze=[])
        for path in iter_audio_files(paths):
            if cancel and cancel.is_set():
                break
            rep.found += 1
            if progress and rep.found % 25 == 0:
                progress(rep.found, path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            existing = self.db.get_track_by_path(path)
            if (existing and existing["size"] == st.st_size and abs((existing["mtime"] or 0) - st.st_mtime) < 1
                    and existing["status"] == "ok" and not self.settings.reanalyze_existing):
                rep.unchanged += 1
                continue
            md = read_metadata(path)
            meta = dict(title=md.title, artist=md.artist, album=md.album, year=md.year, genre=md.genre,
                        duration=md.duration, tag_bpm=md.tag_bpm, tag_key=md.tag_key)
            tid = self.db.upsert_file(path, st.st_size, st.st_mtime, meta)
            if not existing:
                rep.added += 1
            self.db.update_track(tid, status="pending")
            rep.to_analyze.append(tid)
        return rep

    # ----------------------------------------------------------- analysis
    def analyze(self, track_ids: Iterable[int], progress: ProgressCB | None = None,
                cancel: threading.Event | None = None, workers: int | None = None) -> dict:
        """Analyze tracks in a process pool that survives crashing workers.

        If a worker process dies (native crash in a decoder, out of memory, ...), the pool is
        BrokenProcessPool: every unfinished task fails. We then rebuild the pool with half the
        workers and retry the unfinished tracks. With a single worker tasks run strictly in
        submission order, so the first unfinished track is the culprit: it is marked as error
        and the remaining tracks continue with the full worker count again.
        """
        ids = list(track_ids)
        tracks = {t["id"]: t for t in self.db.get_tracks(ids)}
        order = [i for i in ids if i in tracks]
        total = len(order)
        stats = dict(done=0, ok=0, errors=0)
        if not total:
            return dict(total=0, ok=0, errors=0, cancelled=False)
        s = self.settings
        full = max(1, min(workers or s.worker_count(), total))
        n = full
        remaining = order[:]

        handled: set[int] = set()

        def finish(tid: int, res: dict | None, err: str | None):
            handled.add(tid)
            if err is None:
                self.db.set_analysis(tid, res)
                if s.write_tags:
                    self._write_tags_safe(tid)
                stats["ok"] += 1
            else:
                self.db.set_error(tid, err)
                stats["errors"] += 1
            stats["done"] += 1
            if progress:
                progress(stats["done"], total, self.db.get_track(tid))

        while remaining and not (cancel and cancel.is_set()):
            pool = _make_pool(min(n, len(remaining)))
            futs = {}
            broken = False
            try:
                for tid in remaining:
                    futs[pool.submit(self.worker_fn, tracks[tid]["path"], s.bpm_min, s.bpm_max)] = tid
                pending = set(futs)
                while pending:
                    if cancel and cancel.is_set():
                        break
                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    for fut in sorted(done, key=lambda f: order.index(futs[f])):
                        tid = futs[fut]
                        try:
                            finish(tid, fut.result(), None)
                        except BrokenProcessPool:
                            broken = True
                        except Exception as e:  # decoding errors, corrupt files ...
                            finish(tid, None, f"{type(e).__name__}: {e}")
                    if broken:
                        break
            except BrokenProcessPool:     # pool died while we were still submitting
                broken = True
            finally:
                pool.shutdown(wait=not broken, cancel_futures=True)
            remaining = [t for t in remaining if t not in handled]
            if not broken:
                break
            if n == 1 and remaining:
                finish(remaining.pop(0), None, CRASH_MESSAGE)   # culprit found
                n = full
            else:
                n = max(1, n // 2)
        return dict(total=total, ok=stats["ok"], errors=stats["errors"], cancelled=bool(cancel and cancel.is_set()))

    def import_paths(self, paths: Iterable[str], progress: ProgressCB | None = None,
                     cancel: threading.Event | None = None) -> dict:
        rep = self.scan(paths, cancel)
        res = self.analyze(rep.to_analyze or [], progress, cancel)
        return dict(found=rep.found, added=rep.added, unchanged=rep.unchanged, **res)

    # ----------------------------------------------------------- tags
    def _write_tags_safe(self, track_id: int) -> str | None:
        try:
            self.write_tags(track_id)
            return None
        except Exception as e:
            t = self.db.get_track(track_id)
            msg = f"Tag write failed: {e}"
            if t:
                self.db.update_track(track_id, error=msg[:500])
            return msg

    def write_tags(self, track_id: int) -> None:
        t = self.db.get_track(track_id)
        if not t:
            raise KeyError(track_id)
        k = Key.from_code(t["key"])
        key_text = k.format(self.settings.tag_key_notation) if k else None
        write_bpm_key(t["path"], t["bpm"], key_text)
        st = os.stat(t["path"])
        self.db.update_track(track_id, size=st.st_size, mtime=st.st_mtime,
                             tag_bpm=str(int(round(t["bpm"]))) if t["bpm"] else t["tag_bpm"],
                             tag_key=key_text or t["tag_key"])

    # ----------------------------------------------------------- manual edits
    def set_bpm(self, track_id: int, bpm: float | None, write: bool | None = None) -> None:
        self.db.update_track(track_id, bpm=bpm, bpm_manual=1 if bpm is not None else 0, bpm_confidence=1.0 if bpm else None)
        if (self.settings.write_tags if write is None else write) and bpm:
            self._write_tags_safe(track_id)

    def set_key(self, track_id: int, key_code: str | None, write: bool | None = None) -> None:
        self.db.update_track(track_id, key=key_code, key_manual=1 if key_code else 0, key_confidence=1.0 if key_code else None)
        if (self.settings.write_tags if write is None else write) and key_code:
            self._write_tags_safe(track_id)

    def reset_manual(self, track_ids: Iterable[int]) -> None:
        for tid in track_ids:
            self.db.update_track(tid, bpm_manual=0, key_manual=0, status="pending")

    # ----------------------------------------------------------- queries
    def search(self, q: Query) -> list[dict]:
        return self.db.search(q)

    def compatible_tracks(self, track_id: int, bpm_tolerance: float = 6.0, limit: int = 50) -> list[dict]:
        """Tracks that mix harmonically (Camelot neighbours) within a BPM tolerance (percent)."""
        t = self.db.get_track(track_id)
        if not t or not t["key"]:
            return []
        k = Key.from_code(t["key"])
        q = Query(keys=[c.code for c in k.compatible()], order_by="bpm")
        if t["bpm"]:
            q.bpm_min = t["bpm"] * (1 - bpm_tolerance / 100)
            q.bpm_max = t["bpm"] * (1 + bpm_tolerance / 100)
        rows = [r for r in self.db.search(q) if r["id"] != track_id]
        if t["bpm"]:
            rows.sort(key=lambda r: (r["key"] != t["key"], abs((r["bpm"] or 0) - t["bpm"])))
        return rows[:limit]
