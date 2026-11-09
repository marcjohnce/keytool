from __future__ import annotations

import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QSortFilterProxyModel, Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QDoubleSpinBox, QSplitter, QStackedWidget, QTableView, QToolBar, QVBoxLayout, QWidget,
)

from ..config import Settings
from ..db import Query
from ..i18n import set_language, tr
from ..keys import NOTATIONS, Key
from ..library import Library
from ..tags import AUDIO_EXTENSIONS
from ..analysis_queue import DONE, ERROR, RUNNING, WAITING, AnalysisQueue
from .player import PlayerPanel
from .queue_panel import QueueBridge, QueuePanel
from .settings_dialog import SettingsDialog
from .theme import apply_theme, resolve_theme
from .widgets import DetailPanel, ID_ROLE, KeyFilter, KeywordFilter, SORT_ROLE, TrackModel, Translatable


# =========================================================================== worker
class Worker(QObject):
    """Scans files/folders (reads tags, finds new/changed files). The analysis itself runs in the queue."""
    scan_progress = Signal(int)
    finished = Signal(dict)

    def __init__(self, library: Library, paths: list[str]):
        super().__init__()
        self.lib, self.paths = library, paths
        self.cancel = threading.Event()

    def run(self):
        try:
            rep = self.lib.scan(self.paths, self.cancel, lambda n, _p: self.scan_progress.emit(n))
            res = dict(found=rep.found, added=rep.added, unchanged=rep.unchanged, from_tags=rep.from_tags,
                       to_analyze=rep.to_analyze or [], cancelled=self.cancel.is_set())
        except Exception as e:  # never let the thread die silently
            res = dict(found=0, added=0, unchanged=0, from_tags=0, to_analyze=[], error=str(e))
        finally:
            self.lib.db.close()
        self.finished.emit(res)


class SortProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setSortRole(SORT_ROLE)


# =========================================================================== main window
class MainWindow(QMainWindow, Translatable):
    def __init__(self, library: Library):
        super().__init__()
        self._tr_init()
        self.lib = library
        self.s: Settings = library.settings
        set_language(self.s.language)
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self._scan_backlog: list[list[str]] = []
        self._batch_done = 0
        self.queue_bridge = QueueBridge()
        self.queue_bridge.changed.connect(self.on_queue_event)
        self.queue = AnalysisQueue(library, on_change=self.queue_bridge.changed.emit)
        self.harmonic_ids: list[int] | None = None
        self.setAcceptDrops(True)
        self.resize(1480, 880)

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._build_queue_dock()
        self.search_timer = QTimer(self, singleShot=True, interval=180, timeout=self.refresh)
        QShortcut(QKeySequence.Find, self, activated=lambda: (self.f_text.setFocus(), self.f_text.selectAll()))
        QShortcut(QKeySequence.Delete, self.table, activated=self.remove_selected)

        self.apply_theme()
        self.set_notation(self.s.notation)
        self.retranslate()
        self.refresh_keywords()
        self.refresh()

    # ------------------------------------------------------------------ UI build
    def _act(self, key: str, slot, icon_text: str = "") -> QAction:
        a = QAction(self)
        self.t(lambda s, a=a, i=icon_text: a.setText(f"{i}  {s}" if i else s), key)
        a.triggered.connect(slot)
        return a

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        self.a_files = self._act("add_files", self.add_files, "＋")
        self.a_folder = self._act("add_folder", self.add_folder, "📁")
        self.a_pending = self._act("analyze_pending", self.analyze_pending, "▶")
        self.a_reanalyze = self._act("reanalyze", self.reanalyze_selected, "↻")
        self.a_tags = self._act("write_tags", self.write_tags_selected, "🏷")
        self.a_research = self._act("research", lambda: self.open_research(False), "🔎")
        self.a_queue = QAction(self)
        self.a_queue.setCheckable(True)
        self.a_queue.toggled.connect(lambda on: self.queue_dock.setVisible(on))
        self.a_settings = self._act("settings", self.open_settings, "⚙")
        self.a_files.setShortcut(QKeySequence.Open)
        for a in (self.a_files, self.a_folder, None, self.a_pending, self.a_reanalyze, self.a_tags, self.a_queue, None,
                  self.a_research):
            tb.addSeparator() if a is None else tb.addAction(a)
        from PySide6.QtWidgets import QSizePolicy
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.notation_lbl = QLabel()
        self.t(lambda s: self.notation_lbl.setText(s + ": "), "notation")
        tb.addWidget(self.notation_lbl)
        self.notation_box = QComboBox()
        for n in NOTATIONS:
            self.notation_box.addItem("", n)
        self.notation_box.currentIndexChanged.connect(lambda _: self.set_notation(self.notation_box.currentData()))
        tb.addWidget(self.notation_box)
        self.lang_box = QComboBox()
        self.lang_box.addItem("DE", "de")
        self.lang_box.addItem("EN", "en")
        self.lang_box.setCurrentIndex(self.lang_box.findData(self.s.language))
        self.lang_box.currentIndexChanged.connect(lambda _: self.set_language(self.lang_box.currentData()))
        tb.addWidget(self.lang_box)
        self.theme_btn = QPushButton()
        self.theme_btn.setFixedWidth(40)
        self.theme_btn.clicked.connect(self.toggle_theme)
        tb.addWidget(self.theme_btn)
        tb.addAction(self.a_settings)

    def _build_central(self):
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # ---------------- left: filters
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        h = QLabel(); h.setObjectName("heading"); self.t(h.setText, "filters"); lv.addWidget(h)
        self.f_text = QLineEdit(); self.f_text.setClearButtonEnabled(True)
        self.t(self.f_text.setPlaceholderText, "search_all")
        lv.addWidget(self.f_text)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.f_title, self.f_artist, self.f_album = QLineEdit(), QLineEdit(), QLineEdit()
        for key, w in (("title", self.f_title), ("artist", self.f_artist), ("album", self.f_album)):
            w.setClearButtonEnabled(True)
            lbl = QLabel(); self.t(lbl.setText, key)
            form.addRow(lbl, w)
        self.f_year_from, self.f_year_to = QSpinBox(), QSpinBox()
        for w in (self.f_year_from, self.f_year_to):
            w.setRange(0, 2100); w.setSpecialValueText("–"); w.setValue(0)
        yr = QHBoxLayout(); yr.addWidget(self.f_year_from, 1); yr.addWidget(QLabel("–")); yr.addWidget(self.f_year_to, 1)
        lbl = QLabel(); self.t(lbl.setText, "year"); form.addRow(lbl, yr)
        self.f_bpm_from, self.f_bpm_to = QDoubleSpinBox(), QDoubleSpinBox()
        for w in (self.f_bpm_from, self.f_bpm_to):
            w.setRange(0, 400); w.setDecimals(1); w.setSpecialValueText("–"); w.setValue(0)
        br = QHBoxLayout(); br.addWidget(self.f_bpm_from, 1); br.addWidget(QLabel("–")); br.addWidget(self.f_bpm_to, 1)
        lbl = QLabel(); self.t(lbl.setText, "bpm"); form.addRow(lbl, br)
        lv.addLayout(form)
        kl = QLabel(); kl.setObjectName("dim"); self.t(kl.setText, "key"); lv.addWidget(kl)
        self.f_keys = KeyFilter()
        lv.addWidget(self.f_keys)
        wh = QLabel(); wh.setObjectName("dim"); wh.setWordWrap(True); self.t(wh.setText, "wheel_hint")
        lv.addWidget(wh)
        self.harmonic_btn = QPushButton(); self.harmonic_btn.setCheckable(True)
        self.t(self.harmonic_btn.setText, "harmonic"); self.t(self.harmonic_btn.setToolTip, "harmonic_tip")
        self.harmonic_btn.toggled.connect(self.toggle_harmonic)
        lv.addWidget(self.harmonic_btn)
        kwl = QLabel(); kwl.setObjectName("dim"); self.t(kwl.setText, "keywords"); lv.addWidget(kwl)
        self.f_keywords = KeywordFilter()
        self.f_keywords.rename_requested.connect(self.rename_keyword)
        self.f_keywords.delete_requested.connect(self.delete_keyword)
        lv.addWidget(self.f_keywords, 1)
        reset = QPushButton(); self.t(reset.setText, "reset_filters"); reset.clicked.connect(self.reset_filters)
        lv.addWidget(reset)
        for w in (self.f_text, self.f_title, self.f_artist, self.f_album):
            w.textChanged.connect(self.schedule_refresh)
        for w in (self.f_year_from, self.f_year_to, self.f_bpm_from, self.f_bpm_to):
            w.valueChanged.connect(self.schedule_refresh)
        self.f_keys.changed.connect(self.schedule_refresh)
        self.f_keywords.changed.connect(self.schedule_refresh)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(left)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(320)
        split.addWidget(scroll)

        # ---------------- center: table (or drop hint when library is empty)
        self.model = TrackModel(self)
        self.proxy = SortProxy(); self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.AscendingOrder)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # drag selected tracks as files into other applications
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragOnly)
        self.table.setDefaultDropAction(Qt.CopyAction)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for col, w in zip(range(len(TrackModel.COLUMNS)),
                          (240, 170, 170, 56, 64, 64, 56, 60, 150, 100, 160, 56, 64, 90, 120, 300)):
            self.table.setColumnWidth(col, w)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_menu)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.drop_hint = QLabel(); self.drop_hint.setObjectName("dropHint"); self.drop_hint.setAlignment(Qt.AlignCenter)
        self.t(self.drop_hint.setText, "drop_hint")
        self.center = QStackedWidget()
        self.center.addWidget(self.table)
        hint_wrap = QWidget(); hv = QVBoxLayout(hint_wrap); hv.addStretch(); hv.addWidget(self.drop_hint); hv.addStretch()
        self.center.addWidget(hint_wrap)
        self.player = PlayerPanel(self.s)
        self.table.doubleClicked.connect(lambda _: self.player.play())
        self.player.prev_requested.connect(lambda playing: self.play_adjacent(-1, playing))
        self.player.next_requested.connect(lambda playing: self.play_adjacent(1, playing))
        for seq, slot in ((QKeySequence("Ctrl+Right"), lambda: self.play_adjacent(1, self.player.is_playing())),
                          (QKeySequence("Ctrl+Left"), self.player.previous),
                          (QKeySequence(Qt.Key_MediaNext), lambda: self.play_adjacent(1, self.player.is_playing())),
                          (QKeySequence(Qt.Key_MediaPrevious), self.player.previous),
                          (QKeySequence(Qt.Key_MediaTogglePlayPause), self.player.toggle),
                          (QKeySequence(Qt.Key_MediaPlay), self.player.toggle),
                          (QKeySequence(Qt.Key_MediaStop), self.player.stop)):
            QShortcut(seq, self, activated=slot)
        for i in range(1, 9):   # Alt+1..8: jump to Serato hot cue 1..8
            QShortcut(QKeySequence(f"Alt+{i}"), self, activated=lambda i=i: self.player.jump_to_cue(i))
        QShortcut(QKeySequence(Qt.Key_Space), self.table, activated=self.player.toggle,
                  context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence(Qt.Key_Space), self.player, activated=self.player.toggle,
                  context=Qt.WidgetWithChildrenShortcut)
        cw = QWidget(); cv = QVBoxLayout(cw); cv.setContentsMargins(4, 8, 4, 8)
        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self.player)
        vsplit.addWidget(self.center)
        vsplit.setStretchFactor(0, 0); vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([150, 700])
        cv.addWidget(vsplit)
        split.addWidget(cw)

        # ---------------- right: details
        self.details = DetailPanel()
        self.details.save_requested.connect(self.save_details)
        self.details.reveal_requested.connect(self.reveal)
        self.details.keywords_added.connect(self.add_keywords)
        self.details.keyword_removed.connect(self.remove_keyword)
        self.details.keyword_clicked.connect(lambda kw: self.f_keywords.set_selected([kw]))
        dscroll = QScrollArea(); dscroll.setWidgetResizable(True); dscroll.setWidget(self.details)
        dscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dscroll.setMinimumWidth(360)
        split.addWidget(dscroll)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setStretchFactor(2, 0)
        split.setSizes([330, 800, 370])
        self.setCentralWidget(split)

    def _build_queue_dock(self):
        from PySide6.QtWidgets import QDockWidget
        self.queue_panel = QueuePanel(self.queue)
        self.queue_panel.select_track.connect(self.select_track_by_id)
        self.queue_dock = QDockWidget(self)
        self.queue_dock.setObjectName("queueDock")
        self.queue_dock.setWidget(self.queue_panel)
        self.queue_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.queue_dock)
        self.queue_dock.visibilityChanged.connect(lambda v: (self.a_queue.blockSignals(True), self.a_queue.setChecked(v),
                                                             self.a_queue.blockSignals(False)))
        self.queue_dock.hide()

    def _build_statusbar(self):
        sb = self.statusBar()
        self.count_lbl = QLabel()
        sb.addWidget(self.count_lbl)
        self.progress = QProgressBar(); self.progress.setFixedWidth(260); self.progress.hide()
        self.cancel_btn = QPushButton(); self.t(self.cancel_btn.setText, "cancel")
        self.cancel_btn.clicked.connect(self.cancel_work); self.cancel_btn.hide()
        self.msg_lbl = QLabel(); self.msg_lbl.setObjectName("dim")
        sb.addPermanentWidget(self.msg_lbl, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)

    # ------------------------------------------------------------------ appearance & language
    def apply_theme(self):
        apply_theme(QApplication.instance(), self.s.theme)
        dark = resolve_theme(self.s.theme) == "dark"
        self.model.dark = dark
        self.f_keys.apply_colors(dark)
        if hasattr(self, "player"):
            self.player.set_dark(dark)
        self.theme_btn.setText("☀" if dark else "☾")
        self.theme_btn.setToolTip(tr("theme_light") if dark else tr("theme_dark"))
        self.model.refresh_all()

    def toggle_theme(self):
        self.s.theme = "light" if resolve_theme(self.s.theme) == "dark" else "dark"
        self.s.save()
        self.apply_theme()

    def set_notation(self, notation: str):
        self.s.notation = notation
        self.s.save()
        self.notation_box.blockSignals(True)
        self.notation_box.setCurrentIndex(self.notation_box.findData(notation))
        self.notation_box.blockSignals(False)
        self.model.notation = notation
        self.f_keys.set_notation(notation)
        self.details.set_notation(notation)
        self.player.set_notation(notation)
        self.model.refresh_all()

    def set_language(self, lang: str):
        self.s.language = lang
        self.s.save()
        set_language(lang)
        self.retranslate()

    def retranslate(self):
        super().retranslate()
        self.setWindowTitle(tr("app_title"))
        for i in range(self.notation_box.count()):
            self.notation_box.setItemText(i, tr(f"n_{self.notation_box.itemData(i)}"))
        self.lang_box.blockSignals(True)
        self.lang_box.setCurrentIndex(self.lang_box.findData(tr.lang))
        self.lang_box.blockSignals(False)
        dark = resolve_theme(self.s.theme) == "dark"
        self.theme_btn.setToolTip(tr("theme_light") if dark else tr("theme_dark"))
        self.f_keys.set_notation(self.s.notation)
        self.f_keywords.retranslate()
        self.details.retranslate()
        self.player.retranslate()
        if hasattr(self, "queue_panel"):
            self.queue_panel.retranslate()
            self.queue_dock.setWindowTitle(tr("queue_title"))
            self.update_progress()
        self.model.refresh_all()
        self.update_count()

    # ------------------------------------------------------------------ data
    def build_query(self) -> Query:
        q = Query(
            text=self.f_text.text().strip(), title=self.f_title.text().strip(), artist=self.f_artist.text().strip(),
            album=self.f_album.text().strip(),
            year_from=self.f_year_from.value() or None, year_to=self.f_year_to.value() or None,
            bpm_min=self.f_bpm_from.value() or None, bpm_max=self.f_bpm_to.value() or None,
            keys=self.f_keys.selected(), keywords=self.f_keywords.selected(),
            keywords_mode=self.f_keywords.match_mode(),
        )
        return q

    def schedule_refresh(self, *_):
        self.search_timer.start()

    def refresh(self):
        selected = set(self.selected_ids())
        cur = self.table.currentIndex() if hasattr(self, "table") else None
        current_id = cur.data(ID_ROLE) if cur is not None and cur.isValid() else None
        if self.harmonic_ids is not None:
            rows = [r for r in self.lib.search(self.build_query()) if r["id"] in set(self.harmonic_ids)]
        else:
            rows = self.lib.search(self.build_query())
        self.model.set_rows(rows)
        self.center.setCurrentIndex(0 if self.lib.db.count() else 1)
        if selected:
            sm = self.table.selectionModel()
            from PySide6.QtCore import QItemSelection, QItemSelectionModel
            sel = QItemSelection()
            for row in range(self.proxy.rowCount()):
                idx = self.proxy.index(row, 0)
                if idx.data(ID_ROLE) in selected:
                    sel.select(idx, self.proxy.index(row, self.proxy.columnCount() - 1))
            sm.select(sel, QItemSelectionModel.ClearAndSelect)
            # keep the current row too (needed for previous/next track and keyboard navigation)
            for row in range(self.proxy.rowCount()):
                if self.proxy.index(row, 0).data(ID_ROLE) == current_id:
                    sm.setCurrentIndex(self.proxy.index(row, 0), QItemSelectionModel.NoUpdate)
                    break
        self.update_count()
        self.selection_changed()

    def refresh_keywords(self):
        self.details.set_known_genres(self.lib.db.distinct_values("genre"))
        kws = self.lib.db.all_keywords()
        self.f_keywords.set_keywords(kws)
        self.details.set_known_keywords([k for k, _ in kws])

    def update_count(self):
        self.count_lbl.setText(tr("tracks_count", shown=self.proxy.rowCount(), total=self.lib.db.count()))

    def reset_filters(self):
        self.harmonic_btn.setChecked(False)
        for w in (self.f_text, self.f_title, self.f_artist, self.f_album):
            w.blockSignals(True); w.clear(); w.blockSignals(False)
        for w in (self.f_year_from, self.f_year_to, self.f_bpm_from, self.f_bpm_to):
            w.blockSignals(True); w.setValue(0); w.blockSignals(False)
        self.f_keys.blockSignals(True); self.f_keys.clear(); self.f_keys.blockSignals(False); self.f_keys.update()
        self.f_keywords.blockSignals(True); self.f_keywords.set_selected([]); self.f_keywords.blockSignals(False)
        self.refresh()

    # ------------------------------------------------------------------ selection
    def selected_ids(self) -> list[int]:
        if not hasattr(self, "table"):
            return []
        return [i.data(ID_ROLE) for i in self.table.selectionModel().selectedRows()]

    def selected_rows(self) -> list[dict]:
        ids = set(self.selected_ids())
        return [r for r in self.model.rows if r["id"] in ids]

    def selection_changed(self, *_):
        rows = self.selected_rows()
        old_ids = [r["id"] for r in self.details.rows]
        if self.details.has_unsaved() and old_ids != [r["id"] for r in rows]:
            ch = self.details.changes()
            self.details.dirty.clear()          # prevents asking again from the refresh below
            if QMessageBox.question(self, tr("app_title"), tr("ask_save_changes"),
                                    QMessageBox.Save | QMessageBox.Discard, QMessageBox.Save) == QMessageBox.Save:
                self._apply_detail_changes(old_ids, ch)
                rows = self.selected_rows()
        self.details.set_rows(rows, keep_edits=True)
        if not hasattr(self, "player"):
            return
        cur = self.table.currentIndex()
        cur_id = cur.data(ID_ROLE) if cur.isValid() else None
        row = next((r for r in rows if r["id"] == cur_id), rows[0] if rows else None)
        self.player.set_track(row)

    def play_adjacent(self, delta: int, keep_playing: bool):
        """Select the previous/next track in the list's current order (filter + sorting) and load it;
        keeps playing if the player was playing. Stops at the end of the list."""
        n = self.proxy.rowCount()
        if not n:
            return
        cur = self.table.currentIndex()
        row = cur.row() if cur.isValid() else -1
        target = row + delta
        if target < 0 or target >= n:
            if delta > 0 and keep_playing:
                self.player.stop()      # end of the list
            return
        self.table.selectRow(target)
        self.table.scrollTo(self.proxy.index(target, 0))
        if keep_playing:
            self.player.play()

    def _released(self, ids, fn):
        """Run fn while the player has released the affected file (Windows file locking)."""
        paths = {r["path"] for r in self.lib.db.get_tracks(ids)}
        restores = [self.player.release_file(p) for p in paths]
        try:
            return fn()
        finally:
            for r in restores:
                r()

    def toggle_harmonic(self, on: bool):
        if on:
            rows = self.selected_rows()
            if not rows or not rows[0].get("key"):
                self.harmonic_btn.blockSignals(True); self.harmonic_btn.setChecked(False); self.harmonic_btn.blockSignals(False)
                return
            comp = self.lib.compatible_tracks(rows[0]["id"], bpm_tolerance=6, limit=10_000)
            self.harmonic_ids = [rows[0]["id"]] + [r["id"] for r in comp]
        else:
            self.harmonic_ids = None
        self.refresh()

    # ------------------------------------------------------------------ import / analysis
    def add_files(self):
        pattern = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, tr("select_files"), "", f"{tr('audio_files')} ({pattern})")
        if files:
            self.start_import(files)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, tr("select_folder"))
        if d:
            self.start_import([d])

    def analyze_pending(self):
        ids = self.lib.db.pending_ids() + [r["id"] for r in self.lib.search(Query(status="error"))]
        self.enqueue(ids, keep_tag_values=True)

    def reanalyze_selected(self):
        # explicit re-analysis: really analyse, even if the values came from tags -> end of the queue
        self.enqueue(self.selected_ids(), keep_tag_values=False)

    def analyze_now_selected(self):
        # "analyse now": to the top of the queue
        self.enqueue(self.selected_ids(), keep_tag_values=False, front=True)

    def enqueue(self, ids: list[int], keep_tag_values: bool, front: bool = False):
        if not ids:
            return
        if not self.queue.is_busy():
            self._batch_done = 0
        self.queue.add(ids, keep_tag_values=keep_tag_values, front=front)
        self.queue_dock.show()
        self.refresh()

    def select_track_by_id(self, track_id: int):
        for row in range(self.proxy.rowCount()):
            if self.proxy.index(row, 0).data(ID_ROLE) == track_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.proxy.index(row, 0))
                return

    def start_import(self, paths: list[str]):
        if self.thread is not None:                     # a scan is running -> do this one afterwards
            self._scan_backlog.append(paths)
            return
        worker = Worker(self.lib, paths)
        self.worker = worker
        self.thread = QThread(self)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.scan_progress.connect(lambda n: self.msg_lbl.setText(tr("scanning", n=n)))
        worker.finished.connect(self.on_scan_finished)
        self.progress.setRange(0, 0); self.progress.show(); self.cancel_btn.show()
        self.msg_lbl.setText(tr("scanning", n=0))
        self.thread.start()

    def on_scan_finished(self, res: dict):
        self.thread.quit(); self.thread.wait()
        self.thread = None; self.worker = None
        if res.get("to_analyze"):
            self.enqueue(res["to_analyze"], keep_tag_values=True)
        self.msg_lbl.setText(tr("scan_done", found=res.get("found", 0), from_tags=res.get("from_tags", 0),
                                unchanged=res.get("unchanged", 0), queued=len(res.get("to_analyze", [])))
                             + (f" – {res['error']}" if res.get("error") else ""))
        self.refresh()
        if self._scan_backlog:
            self.start_import(self._scan_backlog.pop(0))
        else:
            self.update_progress()

    def on_queue_event(self, event: str, item):
        if event == "finished" and item is not None:
            self._batch_done += 1
            row = self.lib.db.get_track(item.track_id)
            if row:
                row["keywords"] = self.lib.db.keywords_for([row["id"]])[row["id"]]
                self.model.update_row(row)
                if item.track_id in self.selected_ids():
                    self.selection_changed()
        self.queue_panel.schedule()
        self.update_progress()
        if not self.queue.is_busy() and event == "finished":
            c = self.queue.counts()
            self.msg_lbl.setText(tr("queue_all_done", done=c[DONE], errors=c[ERROR]))
            self.refresh()

    def update_progress(self):
        c = self.queue.counts()
        busy = c[WAITING] + c[RUNNING]
        self.a_queue.setText(f"☰  {tr('queue')}" + (f" ({busy})" if busy else ""))
        if self.thread is not None:                     # scanning shows its own (indeterminate) progress
            return
        if busy:
            total = self._batch_done + busy
            self.progress.setRange(0, total); self.progress.setValue(self._batch_done)
            self.progress.show(); self.cancel_btn.show()
            running = [i for i in self.queue.items() if i.state == RUNNING]
            if running:
                self.msg_lbl.setText(tr("analyzing", done=self._batch_done + 1, total=total, name=running[0].title))
        else:
            self.progress.hide(); self.cancel_btn.hide()

    def cancel_work(self):
        """Stop scanning and drop all waiting tracks (running analyses finish)."""
        if self.worker:
            self.worker.cancel.set()
        self._scan_backlog.clear()
        n = self.queue.clear_waiting()
        self.msg_lbl.setText(tr("cancelled") + (f" ({n})" if n else ""))
        self.update_progress()

    # ------------------------------------------------------------------ edits
    def save_details(self, changes: dict):
        self._apply_detail_changes([r["id"] for r in self.details.rows], changes)
        self.details.set_rows(self.selected_rows())

    def _apply_detail_changes(self, ids: list[int], changes: dict):
        if not ids or not changes:
            return
        meta = {k: v for k, v in changes.items() if k not in ("bpm", "key")}

        def work():
            errs = self.lib.update_metadata(ids, meta) if meta else {}
            for t in ids:
                if "bpm" in changes:
                    self.lib.set_bpm(t, changes["bpm"])
                if "key" in changes:
                    self.lib.set_key(t, changes["key"])
            return errs

        errs = self._released(ids, work)
        msg = tr("edits_saved", n=len(ids))
        if errs:
            msg += " · " + tr("tag_errors", n=len(errs))
        self.msg_lbl.setText(msg)
        if "genre" in changes:
            self.details.set_known_genres(self.lib.db.distinct_values("genre"))
        self.refresh()

    def set_bpm(self, value: float):
        ids = self.selected_ids()
        self._released(ids, lambda: [self.lib.set_bpm(t, round(value, 2) if value > 0 else None) for t in ids])
        self.refresh()

    def set_key(self, code: str):
        ids = self.selected_ids()
        self._released(ids, lambda: [self.lib.set_key(t, code or None) for t in ids])
        self.refresh()

    def add_keywords(self, kws: list[str]):
        self.lib.db.add_keywords(self.selected_ids(), kws)
        self.refresh_keywords(); self.refresh()

    def remove_keyword(self, kw: str):
        self.lib.db.remove_keywords(self.selected_ids(), [kw])
        self.refresh_keywords(); self.refresh()

    def rename_keyword(self, kw: str):
        new, ok = QInputDialog.getText(self, tr("keyword_rename"), tr("keyword_new_name"), text=kw)
        if ok and new.strip() and new.strip() != kw:
            self.lib.db.rename_keyword(kw, new.strip())
            self.refresh_keywords(); self.refresh()

    def delete_keyword(self, kw: str):
        ids = [r["id"] for r in self.lib.search(Query(keywords=[kw]))]
        self.lib.db.remove_keywords(ids, [kw])
        self.refresh_keywords(); self.refresh()

    def write_tags_selected(self):
        ids = self.selected_ids()
        results = self._released(ids, lambda: [self.lib._write_tags_safe(t) for t in ids])
        ok = sum(1 for r in results if r is None)
        err = len(results) - ok
        msg = tr("tags_written", n=ok)
        if err:
            msg += " · " + tr("tag_errors", n=err)
        self.msg_lbl.setText(msg)
        self.refresh()

    def remove_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, tr("app_title"), tr("confirm_remove", n=len(ids))) == QMessageBox.Yes:
            self.lib.db.delete_tracks(ids)
            self.refresh_keywords(); self.refresh()

    def remove_missing(self):
        n = self.lib.db.remove_missing()
        self.msg_lbl.setText(tr("removed_missing", n=n))
        self.refresh_keywords(); self.refresh()

    # ------------------------------------------------------------------ context menu
    def table_menu(self, pos):
        rows = self.selected_rows()
        if not rows:
            return
        m = QMenu(self)
        m.addAction("▶  " + tr("play"), self.player.play)
        m.addSeparator()
        m.addAction("⚡  " + tr("analyze_now"), self.analyze_now_selected)
        m.addAction(tr("reanalyze"), self.reanalyze_selected)
        m.addAction(tr("write_tags"), self.write_tags_selected)
        m.addAction(tr("reset_manual"), lambda: (self.lib.reset_manual(self.selected_ids()), self.reanalyze_selected()))
        m.addAction("🔎  " + tr("research_similar"), lambda: self.open_research(True))
        m.addSeparator()
        m.addAction(tr("add_keyword"), self._prompt_keywords)
        m.addSeparator()
        if len(rows) == 1:
            m.addAction(tr("open_folder"), lambda: self.reveal(rows[0]["path"]))
            m.addAction(tr("copy_path"), lambda: QApplication.clipboard().setText(rows[0]["path"]))
            m.addSeparator()
        m.addAction(tr("cleanup"), self.remove_missing)
        m.addAction(tr("remove"), self.remove_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _prompt_keywords(self):
        text, ok = QInputDialog.getText(self, tr("keywords"), tr("add_keyword"))
        if ok and text.strip():
            self.add_keywords([p.strip() for p in text.split(",") if p.strip()])

    @staticmethod
    def reveal(path: str):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ------------------------------------------------------------------ external research
    def open_research(self, from_track: bool = False):
        """Pre-fill from the key wheel / BPM filter, or from the selected track (key + BPM ±tolerance)."""
        from .research_dialog import ResearchDialog

        keys = self.f_keys.selected()
        lo, hi = self.f_bpm_from.value() or None, self.f_bpm_to.value() or None
        rows = self.selected_rows()
        if (from_track or not (keys or lo or hi)) and rows:
            r = rows[0]
            keys = [r["key"]] if r.get("key") else []
            if r.get("bpm"):
                tol = self.s.research_bpm_tolerance / 100
                lo, hi = round(r["bpm"] * (1 - tol), 1), round(r["bpm"] * (1 + tol), 1)
        dlg = ResearchDialog(self.s, self.lib, keys, lo, hi, dark=resolve_theme(self.s.theme) == "dark",
                             notation=self.s.notation, parent=self)
        dlg.exec()

    # ------------------------------------------------------------------ settings
    def open_settings(self):
        dlg = SettingsDialog(self.s, self)
        if dlg.exec():
            dlg.apply()
            set_language(self.s.language)
            self.apply_theme()
            self.set_notation(self.s.notation)
            self.retranslate()

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        # e.source() is set for drags started inside KeyTool (the track list) -> not an import
        if e.mimeData().hasUrls() and e.source() is None:
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is None:
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.source() is not None:
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.start_import(paths)

    def closeEvent(self, e):
        if self.details.has_unsaved():
            if QMessageBox.question(self, tr("app_title"), tr("ask_save_changes"),
                                    QMessageBox.Save | QMessageBox.Discard, QMessageBox.Save) == QMessageBox.Save:
                self.details.save()
        self.player.shutdown()
        self.queue.shutdown()
        self.s.save()
        if self.worker:
            self.worker.cancel.set()
        if self.thread:
            self.thread.quit(); self.thread.wait(15000)
        super().closeEvent(e)
