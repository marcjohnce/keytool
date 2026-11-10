"""Analysis queue: tracks are analysed in queue order (top to bottom).

* add(ids)                  -> append to the end (already waiting tracks keep their place)
* add(ids, front=True) /
  move_to_front(ids)        -> "analyse now": put them at the top, in the given order
* remove(ids)               -> drop waiting tracks
A single background thread feeds a process pool: whenever a worker is free it takes the topmost
waiting track, so with N parallel analyses the N topmost tracks run and the order of starting is
always the queue order (with 1 parallel analysis the queue is processed strictly one after another).

Crash safety: if a worker process dies (BrokenProcessPool), all running tracks go back to the top and
are re-run one at a time; a track that crashes on its own is marked as an error.
"""
from __future__ import annotations

import itertools
import threading
import time
from concurrent.futures import FIRST_COMPLETED, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Callable

from .library import CRASH_MESSAGE, Library, _make_pool, write_log

WAITING, RUNNING, DONE, ERROR = "waiting", "running", "done", "error"
_seq = itertools.count(1)


@dataclass
class QueueItem:
    track_id: int
    keep_tag_values: bool = True
    state: str = WAITING
    error: str = ""
    title: str = ""
    artist: str = ""
    path: str = ""
    crashes: int = 0
    prev_status: str = ""           # status before the track was queued (restored when it is removed)
    prev_error: str = ""
    seq: int = field(default_factory=lambda: next(_seq))
    started: float | None = None
    finished: float | None = None


class AnalysisQueue:
    MAX_FINISHED = 500          # finished entries kept for display

    def __init__(self, library: Library, on_change: Callable[[str, QueueItem | None], None] | None = None):
        self.lib = library
        self.on_change = on_change or (lambda ev, item: None)
        self._items: list[QueueItem] = []           # display order: running/waiting in queue order, then finished
        self._cv = threading.Condition()
        self._stop = False
        self._suspects: set[int] = set()            # tracks involved in a crash -> run them one at a time
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ public API (any thread)
    def add(self, track_ids, keep_tag_values: bool = True, front: bool = False) -> int:
        """Queue tracks. Returns the number of tracks newly added."""
        ids = list(dict.fromkeys(track_ids))
        rows = {t["id"]: t for t in self.lib.db.get_tracks(ids)}
        added = 0
        with self._cv:
            new_items = []
            for tid in ids:
                if tid not in rows:
                    continue
                cur = self._find(tid, (WAITING, RUNNING))
                if cur is not None:
                    if cur.state == WAITING:
                        cur.keep_tag_values = cur.keep_tag_values and keep_tag_values
                        if front:
                            new_items.append(cur)
                    continue
                self._drop_finished(tid)
                r = rows[tid]
                item = QueueItem(tid, keep_tag_values, title=r["title"] or r["filename"], artist=r["artist"] or "",
                                 path=r["path"], prev_status=r["status"] or "", prev_error=r["error"] or "")
                self.lib.db.update_track(tid, status="pending")
                new_items.append(item)
                added += 1
            if front:
                for it in new_items:
                    if it in self._items:
                        self._items.remove(it)
                pos = self._first_waiting_index()
                self._items[pos:pos] = new_items
            else:
                pos = self._first_finished_index()
                self._items[pos:pos] = [i for i in new_items if i not in self._items]
            self._ensure_thread()
            self._cv.notify_all()
        self.on_change("changed", None)
        return added

    def move_to_front(self, track_ids) -> None:
        self.add(track_ids, front=True)

    def remove(self, track_ids) -> int:
        ids = set(track_ids)
        with self._cv:
            gone = [i for i in self._items if i.track_id in ids and i.state != RUNNING]
            for i in gone:
                self._items.remove(i)
                if i.state == WAITING:      # restore the status the track had before it was queued
                    self.lib.db.update_track(i.track_id, status=i.prev_status or "pending", error=i.prev_error)
        self.on_change("changed", None)
        return len(gone)

    def clear_waiting(self) -> int:
        with self._cv:
            ids = [i.track_id for i in self._items if i.state == WAITING]
        return self.remove(ids)

    def clear_finished(self) -> None:
        with self._cv:
            self._items = [i for i in self._items if i.state in (WAITING, RUNNING)]
        self.on_change("changed", None)

    def items(self) -> list[QueueItem]:
        with self._cv:
            return list(self._items)

    def counts(self) -> dict:
        with self._cv:
            c = {WAITING: 0, RUNNING: 0, DONE: 0, ERROR: 0}
            for i in self._items:
                c[i.state] += 1
            return c

    def is_busy(self) -> bool:
        c = self.counts()
        return bool(c[WAITING] or c[RUNNING])

    def wait_idle(self, timeout: float = 600) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if not self.is_busy():
                return True
            time.sleep(0.05)
        return False

    def shutdown(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=10)

    # ------------------------------------------------------------------ internals (lock held)
    def _find(self, tid: int, states) -> QueueItem | None:
        return next((i for i in self._items if i.track_id == tid and i.state in states), None)

    def _drop_finished(self, tid: int) -> None:
        self._items = [i for i in self._items if not (i.track_id == tid and i.state in (DONE, ERROR))]

    def _first_waiting_index(self) -> int:
        return next((n for n, i in enumerate(self._items) if i.state == WAITING),
                    self._first_finished_index())

    def _first_finished_index(self) -> int:
        return next((n for n, i in enumerate(self._items) if i.state in (DONE, ERROR)), len(self._items))

    def _next_waiting(self) -> QueueItem | None:
        return next((i for i in self._items if i.state == WAITING), None)

    def _finish(self, item: QueueItem, state: str, error: str = "") -> None:
        item.state, item.error, item.finished = state, error, time.time()
        self._items.remove(item)
        pos = self._first_finished_index()
        self._items.insert(pos, item)                      # newest finished first
        finished = [i for i in self._items if i.state in (DONE, ERROR)]
        for old in finished[self.MAX_FINISHED:]:
            self._items.remove(old)

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop = False
            self._thread = threading.Thread(target=self._run, name="analysis-queue", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------------ worker thread
    def _run(self) -> None:
        pool = None
        running: dict = {}                                 # future -> item
        try:
            while True:
                with self._cv:
                    while not self._stop and not running and self._next_waiting() is None:
                        if pool is not None:               # idle: free the worker processes
                            pool.shutdown(wait=False, cancel_futures=True)
                            pool = None
                        self._cv.wait(timeout=1.0)
                    if self._stop:
                        break
                    full = max(1, self.lib.settings.worker_count())
                    limit = 1 if self._suspects else full
                    started = []
                    while len(running) < limit:
                        item = self._next_waiting()
                        if item is None:
                            break
                        t = self.lib.db.get_track(item.track_id)
                        args = self.lib.submit_args(t, item.keep_tag_values) if t else None
                        if args is None:                   # nothing to analyse (or track deleted)
                            if t:
                                self.lib.db.update_track(item.track_id, status="ok", error="")
                            self._finish(item, DONE)
                            started.append(("finished", item))
                            continue
                        try:
                            if pool is None:
                                pool = _make_pool(full)
                            fut = pool.submit(self.lib.worker_fn, *args)
                        except Exception as e:     # pool could not start a process: report, don't hang
                            import traceback
                            write_log("could not start analysis process\n" + traceback.format_exc())
                            err = f"{type(e).__name__}: {e}"
                            self.lib.db.set_error(item.track_id, err)
                            self._finish(item, ERROR, err)
                            started.append(("finished", item))
                            if pool is not None:
                                pool.shutdown(wait=False, cancel_futures=True)
                                pool = None
                            continue
                        item.state, item.started = RUNNING, time.time()
                        running[fut] = item
                        started.append(("started", item))
                for ev, item in started:
                    self.on_change(ev, item)
                if not running:
                    continue
                done, _ = wait(list(running), timeout=0.3, return_when=FIRST_COMPLETED)
                broken = False
                for fut in done:
                    item = running.pop(fut)
                    try:
                        res = fut.result()
                        err = None
                    except BrokenProcessPool:
                        broken = True
                        running[fut] = item                # handled below together with the others
                        continue
                    except Exception as e:
                        res, err = None, f"{type(e).__name__}: {e}"
                    self.lib.store_result(item.track_id, res, err, item.keep_tag_values)
                    with self._cv:
                        self._suspects.discard(item.track_id)
                        self._finish(item, DONE if err is None else ERROR, err or "")
                    self.on_change("finished", item)
                if broken:
                    crashed = list(running.values())
                    running.clear()
                    codes = []
                    for p in list(getattr(pool, "_processes", {}).values()):
                        try:
                            p.join(timeout=0.5)
                            if p.exitcode not in (None, 0):
                                c = p.exitcode
                                codes.append(f"{c} (0x{c & 0xFFFFFFFF:08X})")
                        except Exception:
                            pass
                    write_log("analysis process died; exit codes: " + (", ".join(codes) or "unknown") +
                              "; files: " + "; ".join(self.lib.db.get_track(i.track_id)["path"]
                                                      for i in crashed if self.lib.db.get_track(i.track_id)))
                    pool.shutdown(wait=False, cancel_futures=True)
                    pool = None
                    with self._cv:
                        if len(crashed) == 1 and crashed[0].track_id in self._suspects:
                            item = crashed[0]              # crashed on its own -> this file is the culprit
                            self._suspects.discard(item.track_id)
                            self.lib.store_result(item.track_id, None, CRASH_MESSAGE, item.keep_tag_values)
                            self._finish(item, ERROR, CRASH_MESSAGE)
                            events = [("finished", item)]
                        else:                              # back to the top, re-run one at a time
                            for item in crashed:
                                item.state, item.crashes = WAITING, item.crashes + 1
                                self._suspects.add(item.track_id)
                                self._items.remove(item)
                            pos = self._first_waiting_index()
                            crashed.sort(key=lambda i: i.started or 0)
                            self._items[pos:pos] = crashed
                            events = [("changed", None)]
                    for ev, item in events:
                        self.on_change(ev, item)
        finally:
            if pool is not None:
                # terminate running analyses quickly on shutdown
                for p in list(getattr(pool, "_processes", {}).values()):
                    try:
                        p.terminate()
                    except Exception:
                        pass
                pool.shutdown(wait=False, cancel_futures=True)
