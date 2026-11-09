"""Research dialog: search Karaoke-Version by key(s) and BPM range and show the parsed results."""
from __future__ import annotations

import collections
import threading

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QPushButton, QTableView, QVBoxLayout,
)

from ..db import Query
from ..i18n import tr
from ..keys import Key
from ..research import KaraokeVersionClient, KVSong, in_library, library_index, search_url
from .theme import camelot_color
from .widgets import KeyFilter, fmt_duration

SORT_ROLE = Qt.UserRole + 1


# =========================================================================== background worker
class _Bridge(QObject):
    page_loaded = Signal(int, object)        # generation, KVPage
    song_updated = Signal(int, str)          # generation, url
    failed = Signal(int, str)
    busy = Signal(bool)


class ResearchWorker:
    """One background thread: searches first, then song-page details (throttled by the client)."""

    def __init__(self, client: KaraokeVersionClient):
        self.client = client
        self.bridge = _Bridge()
        self.gen = 0
        self._cv = threading.Condition()
        self._search: tuple | None = None
        self._details: collections.deque[KVSong] = collections.deque()
        self._cancel = threading.Event()
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def search(self, keys, lo, hi, page: int, new: bool) -> int:
        with self._cv:
            if new:
                self.gen += 1
                self._cancel.set()
                self._details.clear()
            self._search = (self.gen, keys, lo, hi, page)
            self._cv.notify()
        return self.gen

    def details(self, songs: list[KVSong], front: bool = False):
        with self._cv:
            for s in (reversed(songs) if front else songs):
                if s.details_loaded:
                    continue
                if s in self._details:
                    self._details.remove(s)
                (self._details.appendleft if front else self._details.append)(s)
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._stop = True
            self._cancel.set()
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while not self._stop and self._search is None and not self._details:
                    self.bridge.busy.emit(False)
                    self._cv.wait()
                if self._stop:
                    return
                self.bridge.busy.emit(True)
                job, self._search = self._search, None
                song = None if job else self._details.popleft()
                gen = self.gen
                if job:
                    self._cancel.clear()
            try:
                if job:
                    g, keys, lo, hi, page = job
                    res = self.client.search(keys, lo, hi, page, self._cancel)
                    if g == self.gen:
                        self.bridge.page_loaded.emit(g, res)
                else:
                    self.client.load_details(song)
                    self.bridge.song_updated.emit(gen, song.url)
            except Exception as e:
                self.bridge.failed.emit(gen, str(e))


# =========================================================================== results model
class KVModel(QAbstractTableModel):
    COLS = ["in_lib", "title", "artist", "year", "key", "bpm", "duration", "price"]

    def __init__(self):
        super().__init__()
        self.songs: list[KVSong] = []
        self.lib_index: set = set()
        self.notation = "camelot"
        self.dark = True

    def reset(self, songs=()):
        self.beginResetModel()
        self.songs = list(songs)
        self.endResetModel()

    def append(self, songs):
        known = {s.url for s in self.songs}
        new = [s for s in songs if s.url not in known]
        if not new:
            return
        self.beginInsertRows(QModelIndex(), len(self.songs), len(self.songs) + len(new) - 1)
        self.songs += new
        self.endInsertRows()

    def changed(self, url: str):
        for i, s in enumerate(self.songs):
            if s.url == url:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.COLS) - 1))

    def rowCount(self, p=QModelIndex()):
        return 0 if p.isValid() else len(self.songs)

    def columnCount(self, p=QModelIndex()):
        return 0 if p.isValid() else len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            c = self.COLS[section]
            return "✓" if c == "in_lib" else tr({"price": "price", "key": "key", "bpm": "bpm"}.get(c, c))
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole and self.COLS[section] == "in_lib":
            return tr("in_library")
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        s = self.songs[idx.row()]
        c = self.COLS[idx.column()]
        keys = [Key.from_code(k) for k in s.keys]
        if role == Qt.DisplayRole:
            if c == "in_lib":
                return "✓" if in_library(s, self.lib_index) else ""
            if c == "key":
                return ", ".join(k.format(self.notation) for k in keys)
            if c == "bpm":
                if not s.details_loaded:
                    return "…"
                return ("~" if s.bpm_variable else "") + (f"{s.bpm:g}" if s.bpm else "–")
            if c == "duration":
                return fmt_duration(s.duration) if s.duration else ("…" if not s.details_loaded else "")
            if c == "year":
                return str(s.year or "")
            return getattr(s, c) or ""
        if role == SORT_ROLE:
            if c == "key":
                return (keys[0].camelot_number * 2 + (0 if keys[0].minor else 1)) if keys else 999
            if c in ("bpm", "year", "duration"):
                v = getattr(s, c)
                return float(v) if v else 1e9
            if c == "in_lib":
                return 0 if in_library(s, self.lib_index) else 1
            return str(getattr(s, c) or "").lower()
        if role == Qt.BackgroundRole and c == "key" and keys:
            return camelot_color(keys[0].code, self.dark)
        if role == Qt.ForegroundRole and c == "key" and keys:
            return QColor("#111111")
        if role == Qt.TextAlignmentRole and c in ("in_lib", "year", "key", "bpm", "duration", "price"):
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            if c == "key" and keys:
                extra = "\n" + tr("kv_key_change") if len(keys) > 1 else ""
                return " · ".join(f"{k.long_name(tr.lang)} ({k.camelot()})" for k in keys) + extra
            if c == "bpm" and s.bpm_variable:
                return tr("kv_variable_tempo")
            return s.url
        return None


# =========================================================================== dialog
class ResearchDialog(QDialog):
    def __init__(self, settings, library, keys: list[str], bpm_min: float | None, bpm_max: float | None,
                 dark: bool = True, notation: str = "camelot", parent=None):
        super().__init__(parent)
        self.s = settings
        self.lib = library
        self.page = 0
        self.has_more = False
        self.total = 0
        self.web_url = ""
        self.setWindowTitle(tr("research_title"))
        self.resize(1250, 720)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QHBoxLayout(self)

        # ---------------- left: criteria
        left = QVBoxLayout()
        kl = QLabel(tr("research_keys")); kl.setWordWrap(True)
        left.addWidget(kl)
        self.wheel = KeyFilter()
        self.wheel.set_notation(notation)
        self.wheel.apply_colors(dark)
        self.wheel.set_selected(keys)
        self.wheel.setMinimumSize(260, 260)
        left.addWidget(self.wheel, 1)
        row = QHBoxLayout()
        ext = QPushButton(tr("research_extend")); ext.clicked.connect(self.extend_harmonic)
        clr = QPushButton(tr("research_clear")); clr.clicked.connect(self.wheel.clear)
        row.addWidget(ext); row.addWidget(clr)
        left.addLayout(row)
        brow = QHBoxLayout()
        brow.addWidget(QLabel(tr("bpm")))
        self.bmin, self.bmax = QDoubleSpinBox(), QDoubleSpinBox()
        for w, v in ((self.bmin, bpm_min), (self.bmax, bpm_max)):
            w.setRange(0, 300); w.setDecimals(1); w.setSpecialValueText("–"); w.setValue(v or 0)
        brow.addWidget(self.bmin, 1); brow.addWidget(QLabel("–")); brow.addWidget(self.bmax, 1)
        left.addLayout(brow)
        self.auto_details = QCheckBox(tr("kv_auto_details"))
        self.auto_details.setChecked(getattr(settings, "research_auto_details", True))
        self.auto_details.setToolTip(tr("kv_auto_details_tip"))
        left.addWidget(self.auto_details)
        self.search_btn = QPushButton("🔎  " + tr("kv_search")); self.search_btn.setObjectName("primary")
        self.search_btn.clicked.connect(self.new_search)
        left.addWidget(self.search_btn)
        src = QLabel(tr("kv_source_note")); src.setWordWrap(True); src.setObjectName("dim")
        left.addWidget(src)
        lay.addLayout(left, 0)

        # ---------------- right: results
        right = QVBoxLayout()
        self.status = QLabel(tr("kv_start_hint")); self.status.setWordWrap(True)
        right.addWidget(self.status)
        self.model = KVModel()
        self.model.notation, self.model.dark = notation, dark
        self.model.lib_index = library_index(self.lib.db.search(Query()))
        self.proxy = QSortFilterProxyModel(); self.proxy.setSourceModel(self.model); self.proxy.setSortRole(SORT_ROLE)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        for i, w in enumerate((32, 260, 190, 56, 120, 64, 60, 70)):
            self.table.setColumnWidth(i, w)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda _: self.open_selected())
        self.table.selectionModel().currentRowChanged.connect(self._row_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        right.addWidget(self.table, 1)
        btns = QHBoxLayout()
        self.more_btn = QPushButton(tr("kv_more")); self.more_btn.clicked.connect(self.load_more)
        self.open_btn = QPushButton(tr("kv_open_song")); self.open_btn.clicked.connect(self.open_selected)
        self.web_btn = QPushButton(tr("kv_open_search")); self.web_btn.clicked.connect(self.open_search)
        close = QPushButton(tr("close")); close.clicked.connect(self.accept)
        for b in (self.more_btn, self.open_btn, self.web_btn):
            btns.addWidget(b)
        btns.addStretch(); btns.addWidget(close)
        right.addLayout(btns)
        lay.addLayout(right, 1)

        self.worker = ResearchWorker(KaraokeVersionClient())
        self.worker.bridge.page_loaded.connect(self._page_loaded)
        self.worker.bridge.song_updated.connect(self._song_updated)
        self.worker.bridge.failed.connect(self._failed)
        self.worker.bridge.busy.connect(self._busy)
        self._update_buttons()
        if keys or bpm_min or bpm_max:
            self.new_search()

    # ------------------------------------------------------------------ criteria
    def keys(self) -> list[Key]:
        ks = [Key.from_code(c) for c in self.wheel.selected()]
        return sorted(ks, key=lambda k: (k.camelot_number, not k.minor))

    def bpm_range(self):
        lo, hi = self.bmin.value() or None, self.bmax.value() or None
        if lo and hi and lo > hi:
            lo, hi = hi, lo
        return lo, hi

    def extend_harmonic(self):
        codes = set(self.wheel.selected())
        for c in list(codes):
            codes.update(k.code for k in Key.from_code(c).compatible())
        self.wheel.set_selected(sorted(codes))

    # ------------------------------------------------------------------ search
    def new_search(self):
        keys, (lo, hi) = self.keys(), self.bpm_range()
        if not keys and not lo and not hi:
            self.status.setText(tr("research_none"))
            return
        self.s.research_auto_details = self.auto_details.isChecked()
        self.page, self.total, self.has_more = 1, 0, False
        self.model.reset()
        self.web_url = search_url(keys, lo, hi)
        self._criteria = (keys, lo, hi)
        self.status.setText(tr("kv_searching", n=max(1, len(keys))))
        self.worker.search(keys, lo, hi, 1, new=True)
        self._update_buttons()

    def load_more(self):
        if not self.has_more:
            return
        self.page += 1
        keys, lo, hi = self._criteria
        self.status.setText(tr("kv_loading_more", page=self.page))
        self.worker.search(keys, lo, hi, self.page, new=False)
        self.more_btn.setEnabled(False)

    def _page_loaded(self, gen, res):
        if gen != self.worker.gen:
            return
        self.total = res.total if self.page == 1 else self.total
        self.has_more = res.has_more
        self.model.append(res.songs)
        if self.auto_details.isChecked():
            self.worker.details([s for s in res.songs if not s.details_loaded])
        self._update_status()
        self._update_buttons()

    def _song_updated(self, gen, url):
        if gen == self.worker.gen:
            self.model.changed(url)
            self._update_status()

    def _failed(self, gen, msg):
        if gen == self.worker.gen:
            self.status.setText("⚠ " + msg)
            self._update_buttons()

    def _busy(self, busy):
        self.search_btn.setText(("⏳  " if busy else "🔎  ") + tr("kv_search"))

    def _update_status(self):
        n = len(self.model.songs)
        loaded = sum(1 for s in self.model.songs if s.details_loaded)
        lib = sum(1 for s in self.model.songs if in_library(s, self.model.lib_index))
        txt = tr("kv_status", total=self.total, shown=n)
        if loaded < n and self.auto_details.isChecked():
            txt += " · " + tr("kv_details_progress", done=loaded, n=n)
        if lib:
            txt += " · " + tr("kv_in_lib_count", n=lib)
        if not n:
            txt = tr("kv_no_results")
        self.status.setText(txt)

    def _update_buttons(self):
        self.more_btn.setEnabled(self.has_more)
        self.web_btn.setEnabled(bool(self.web_url))
        self.open_btn.setEnabled(bool(self.model.songs))

    # ------------------------------------------------------------------ selection / open
    def _current_song(self) -> KVSong | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        return self.model.songs[self.proxy.mapToSource(idx).row()]

    def _row_selected(self, *_):
        s = self._current_song()
        if s and not s.details_loaded:
            self.worker.details([s], front=True)   # load the clicked song first

    def open_selected(self):
        s = self._current_song()
        if s:
            QDesktopServices.openUrl(QUrl(s.url))

    def open_search(self):
        if self.web_url:
            QDesktopServices.openUrl(QUrl(self.web_url))

    def _menu(self, pos):
        s = self._current_song()
        if not s:
            return
        m = QMenu(self)
        m.addAction(tr("kv_open_song"), self.open_selected)
        m.addAction(tr("copy_link"), lambda: QApplication.clipboard().setText(s.url))
        m.addAction(tr("copy_title"), lambda: QApplication.clipboard().setText(f"{s.artist} - {s.title}"))
        if not s.details_loaded:
            m.addAction(tr("kv_load_details"), lambda: self.worker.details([s], front=True))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def done(self, r):
        self.worker.stop()
        self.s.research_auto_details = self.auto_details.isChecked()
        self.s.save()
        super().done(r)
