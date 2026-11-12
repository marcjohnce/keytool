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
from ..keys import Key
from ..library import Library
from ..tags import AUDIO_EXTENSIONS
from ..analysis_queue import DONE, ERROR, RUNNING, WAITING, AnalysisQueue
from .player import PlayerPanel
from .queue_panel import QueueBridge, QueuePanel
from .settings_dialog import SettingsDialog
from .theme import apply_theme, resolve_theme
from .widgets import (AudioTypePicker, DetailPanel, GROUP_BY, PANEL_TOP, GROUP_ROLE, HEADER_ROLE, ID_ROLE, KeyFilter,
                      KeywordFilter, SORT_ROLE, TrackDelegate, TrackModel, Translatable)


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
    """Sorting by the clicked column - inside the groups when the list is grouped (groups stay A-Z, the
    header first); tracks of a collapsed group are filtered out."""

    def __init__(self):
        super().__init__()
        self.setSortRole(SORT_ROLE)

    def lessThan(self, left, right):
        m = self.sourceModel()
        if getattr(m, "group_by", "none") != "none":
            gl, gr = left.data(GROUP_ROLE), right.data(GROUP_ROLE)
            asc = self.sortOrder() == Qt.AscendingOrder
            if gl != gr:                                  # Qt swaps the result for descending order
                return (gl < gr) if asc else (gl > gr)
            hl, hr = left.data(HEADER_ROLE), right.data(HEADER_ROLE)
            if hl or hr:
                return bool(hl) if asc else bool(hr)
        return super().lessThan(left, right)

    def refilter(self):
        """Apply a changed set of collapsed groups (Qt >= 6.10: begin/endFilterChange, before: invalidate)."""
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.invalidateRowsFilter()

    def filterAcceptsRow(self, row, parent):
        m = self.sourceModel()
        if getattr(m, "group_by", "none") == "none" or m.is_header(row):
            return True
        return m.group_of(m.rows[row])[0] not in m.collapsed


# =========================================================================== main window
class MainWindow(QMainWindow, Translatable):
    # default widths of the compact columns (widened if the header needs more room in the UI language)
    RAIL_TOP = PANEL_TOP   # top margin of the panel titles (filters, track list, details) - keeps them aligned
    DEFAULT_WIDTHS = (44, 210, 140, 140, 150, 62, 60, 78, 80, 56, 52, 52, 150, 100, 160, 56, 64, 90, 120, 300)
    NARROW_COLUMNS = {"fit": 44, "key": 62, "bpm": 60, "delta": 78, "energy": 80, "duration": 56, "cues": 52,
                      "year": 52}

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
        from .mik_dialog import MikBridge
        self.mik_bridge = MikBridge()
        self.mik_bridge.done.connect(self._mik_done)
        self.queue_bridge.changed.connect(self.on_queue_event)
        self.queue = AnalysisQueue(library, on_change=self.queue_bridge.changed.emit)
        self.harmonic_ids: list[int] | None = None
        self.setAcceptDrops(True)
        self.resize(1480, 880)

        # palette, style sheet and the bundled font must be active before any widget exists, otherwise
        # widgets created earlier keep the system font (style-sheet fonts are resolved at creation time)
        apply_theme(QApplication.instance(), self.s.theme)
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._build_queue_dock()
        self.search_timer = QTimer(self, singleShot=True, interval=180, timeout=self.refresh)
        QShortcut(QKeySequence.Find, self, activated=lambda: (self.f_text.setFocus(), self.f_text.selectAll()))
        QShortcut(QKeySequence.Delete, self.table, activated=self.remove_selected)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.export_selected)

        self.apply_theme()
        self.set_notation(self.s.notation)
        self.retranslate()
        self.refresh_keywords()
        self.refresh()
        self._start_cue_counter()

    # ------------------------------------------------------------------ UI build
    def _act(self, key: str, slot, icon_text: str = "") -> QAction:
        a = QAction(self)
        self.t(lambda s, a=a, i=icon_text: a.setText(f"{i}  {s}" if i else s), key)
        a.triggered.connect(slot)
        return a

    def createPopupMenu(self):
        """No right-click menu on the toolbar (Qt's default only offers to hide the toolbar itself)."""
        return None

    def _build_toolbar(self):
        tb = QToolBar()
        self.toolbar = tb
        tb.setMovable(False)
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        tb.toggleViewAction().setEnabled(False)          # the toolbar cannot be hidden by accident
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        brand = QLabel("KeyTool"); brand.setObjectName("brand")
        from .theme import styled_font
        brand.setFont(styled_font(brand.font(), size=14, weight=800, width=125))
        tb.addWidget(brand)
        self.a_files = self._act("add_files", self.add_files)
        self.a_folder = self._act("add_folder", self.add_folder)
        self.a_pending = self._act("analyze_pending", self.analyze_pending)
        self.a_reanalyze = self._act("reanalyze", self.reanalyze_selected)
        self.a_tags = self._act("write_tags", self.write_tags_selected)
        self.a_research = self._act("research", lambda: self.open_research(False))
        self.a_queue = QAction(self)
        self.a_queue.setCheckable(True)
        self.a_queue.toggled.connect(lambda on: self.queue_dock.setVisible(on))
        self.a_settings = self._act("settings", self.open_settings)
        self.a_files.setShortcut(QKeySequence.Open)
        def gap(w=12):
            sp = QWidget(); sp.setFixedWidth(w); tb.addWidget(sp)
        from PySide6.QtWidgets import QMenu, QToolButton as _TB
        for a in (self.a_files, self.a_folder):
            tb.addAction(a)
        gap()
        # analysis actions grouped in one menu button
        self.analyze_btn = _TB(); self.analyze_btn.setPopupMode(_TB.InstantPopup)
        am = QMenu(self.analyze_btn); am.setToolTipsVisible(True)
        self.a_sections = self._act("analyze_sections", self.analyze_sections)
        self.t(self.a_sections.setToolTip, "analyze_sections_tip")
        for a in (self.a_pending, self.a_reanalyze, self.a_sections, self.a_tags):
            am.addAction(a)
        am.addSeparator()
        self.a_mik = self._act("mik_import", self.import_mik)
        am.addAction(self.a_mik)
        self.analyze_btn.setMenu(am)
        self.t(lambda s: self.analyze_btn.setText(s + "  ▾"), "analysis_menu")
        tb.addWidget(self.analyze_btn)
        tb.addAction(self.a_research)
        for a in (self.a_files, self.a_folder):
            tb.widgetForAction(a).setObjectName("tbPrimary")
        from PySide6.QtWidgets import QSizePolicy
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        # language, colour scheme and notation live in the settings dialog
        # right sidebar (metadata etc.) on/off
        self.a_details = QAction(self)
        self.a_details.setCheckable(True)
        self.a_details.setChecked(bool(getattr(self.s, "show_details", True)))
        self.a_details.setShortcut(QKeySequence("Ctrl+I"))
        self.t(self.a_details.setText, "details_toggle")
        self.t(self.a_details.setToolTip, "details_toggle_tip")
        self.a_details.toggled.connect(self.set_details_visible)
        tb.addAction(self.a_details)
        tb.addAction(self.a_queue)              # analysis queue panel on/off, right of "Details"
        tb.addAction(self.a_settings)

    def _build_central(self):
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # ---------------- left: filters
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(20, self.RAIL_TOP, 16, 16)
        lv.setSpacing(10)
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
        self.f_types = AudioTypePicker("all_types")
        self.f_types.changed.connect(self.schedule_refresh)
        lbl = QLabel(); self.t(lbl.setText, "audio_type"); form.addRow(lbl, self.f_types)
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
        scroll = QScrollArea(); scroll.setObjectName("rail"); scroll.setWidgetResizable(True); scroll.setWidget(left)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(290)
        split.addWidget(scroll)

        # ---------------- center: table (or drop hint when library is empty)
        self.model = TrackModel(self)
        self.proxy = SortProxy(); self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        sort_col = self.s.sort_column if self.s.sort_column in TrackModel.COLUMNS else "title"
        self.table.sortByColumn(TrackModel.COLUMNS.index(sort_col),
                                Qt.DescendingOrder if self.s.sort_descending else Qt.AscendingOrder)
        self.table.setAlternatingRowColors(False)
        self.table.setItemDelegate(TrackDelegate(self.model, self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # drag selected tracks as files into other applications
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragOnly)
        self.table.setDefaultDropAction(Qt.CopyAction)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for col, w in zip(range(len(TrackModel.COLUMNS)), self.DEFAULT_WIDTHS):
            self.table.setColumnWidth(col, w)
        # columns can be rearranged by dragging their header; the order is remembered
        hh.setSectionsMovable(True)
        hh.setFirstSectionMovable(True)
        self.apply_column_order(getattr(self.s, "column_order", None) or [])
        self.apply_column_widths(self.s.column_widths or {})
        hh.sectionMoved.connect(self._column_moved)
        # widths and the sort column are remembered too (widths saved shortly after the last drag)
        self._widths_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save_column_widths)
        hh.sectionResized.connect(self._column_resized)
        hh.sortIndicatorChanged.connect(self._sort_changed)
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_menu)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        # rich tooltip (cover, title, artist, album, year) on the title/artist/album columns
        from .track_tip import TrackTipFilter
        self.track_tip = TrackTipFilter(
            self.table,
            lambda idx: self.model.rows[self.proxy.mapToSource(idx).row()],
            lambda idx: TrackModel.COLUMNS[idx.column()], self)
        self.track_tip.tip.set_dark(resolve_theme(self.s.theme) == "dark")
        self.drop_hint = QLabel(); self.drop_hint.setObjectName("dropHint"); self.drop_hint.setAlignment(Qt.AlignCenter)
        self.t(self.drop_hint.setText, "drop_hint")
        # header panel above the list: title "Tracks" on the left, grouping (none / artist / album) on the right
        self.group_combo = QComboBox(); self.group_combo.setObjectName("groupBy")
        for mode in GROUP_BY:
            self.group_combo.addItem("", mode)
        gl = QLabel(); gl.setObjectName("dim"); self.t(gl.setText, "group_by")
        self.list_title = QLabel(); self.list_title.setObjectName("heading"); self.t(self.list_title.setText, "tracks_heading")
        list_page = QWidget(); lpv = QVBoxLayout(list_page); lpv.setContentsMargins(0, 0, 0, 0); lpv.setSpacing(0)
        # number of tracks next to the title ("14 of 13,063 tracks"), baseline-aligned with it
        self.count_lbl = QLabel(); self.count_lbl.setObjectName("dim")
        # the title is top-aligned with "Search & filter" (same top margin as the filter rail)
        gbar = QWidget(); gbar.setObjectName("listHeader"); gbl = QHBoxLayout(gbar)
        gbl.setContentsMargins(6, self.RAIL_TOP, 6, 8); gbl.setSpacing(8)
        title_w = QWidget(); title_box = QHBoxLayout(title_w)
        title_box.setContentsMargins(0, 0, 0, 0); title_box.setSpacing(10)
        title_box.addWidget(self.list_title, 0, Qt.AlignBaseline); title_box.addWidget(self.count_lbl, 0, Qt.AlignBaseline)
        group_w = QWidget(); group_box = QHBoxLayout(group_w); group_box.setContentsMargins(0, 0, 0, 0)
        group_box.addWidget(gl); group_box.addWidget(self.group_combo)
        gbl.addWidget(title_w, 0, Qt.AlignVCenter)
        gbl.addStretch(1)
        gbl.addWidget(group_w, 0, Qt.AlignVCenter)
        # the drop-down is taller than the title: start the row higher by half the difference, so the title's
        # top lines up with "Search & filter" and the drop-down sits centred on the title line
        lift = max(0, (group_w.sizeHint().height() - title_w.sizeHint().height()) // 2)
        gbl.setContentsMargins(6, max(0, self.RAIL_TOP - lift), 6, 8)
        gl.setBuddy(self.group_combo)
        lpv.addWidget(gbar); lpv.addWidget(self.table, 1)
        mode = getattr(self.s, "group_by", "none")
        self.group_combo.setCurrentIndex(max(0, GROUP_BY.index(mode) if mode in GROUP_BY else 0))
        self.model.set_group_by(self.group_combo.currentData())
        self.group_combo.currentIndexChanged.connect(self.set_group_by)
        self.table.clicked.connect(self._header_clicked)
        for sig in (self.proxy.layoutChanged, self.proxy.modelReset, self.proxy.rowsInserted, self.proxy.rowsRemoved):
            sig.connect(self._update_spans)
        self.center = QStackedWidget()
        self.center.addWidget(list_page)
        hint_wrap = QWidget(); hv = QVBoxLayout(hint_wrap); hv.addStretch(); hv.addWidget(self.drop_hint); hv.addStretch()
        self.center.addWidget(hint_wrap)
        self.player = PlayerPanel(self.s)
        self.player.deck_changed.connect(self.update_deck)
        self.player.cues_read.connect(self._deck_cues_read)
        self.player.sections_edited.connect(self._deck_sections_edited)
        self.player.cues_edited.connect(self._deck_cues_edited)
        self.player.redetect_sections.connect(self.redetect_sections)
        self.table.doubleClicked.connect(lambda idx: idx.data(HEADER_ROLE) or
                                         self.load_into_deck(autoplay=True, track_id=idx.data(ID_ROLE)))
        for k in (Qt.Key_Return, Qt.Key_Enter):
            QShortcut(QKeySequence(k), self.table, activated=self._enter_pressed, context=Qt.WidgetShortcut)
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
        cw = QWidget(); cv = QVBoxLayout(cw); cv.setContentsMargins(12, 0, 12, 0)   # top: RAIL_TOP in the header
        cv.addWidget(self.center)
        split.addWidget(cw)

        # ---------------- right: details
        self.details = DetailPanel()
        self.details.retag_patterns = self.s.retag_patterns
        self.details.save_requested.connect(self.save_details)
        self.details.reveal_requested.connect(self.reveal)
        self.details.keywords_added.connect(self.add_keywords)
        self.details.keyword_removed.connect(self.remove_keyword)
        self.details.keyword_clicked.connect(lambda kw: self.f_keywords.set_selected([kw]))
        self.details.setObjectName("inspectorBody")
        self.details.setAttribute(Qt.WA_StyledBackground, True)
        dscroll = QScrollArea(); dscroll.setObjectName("inspector"); dscroll.setWidgetResizable(True)
        self.details_scroll = dscroll
        dscroll.setWidget(self.details)
        dscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dscroll.setMinimumWidth(330)
        split.addWidget(dscroll)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setStretchFactor(2, 0)
        split.setSizes([300, 840, 340])
        # the deck spans the full width above filter rail, list and inspector
        root = QWidget(); rv = QVBoxLayout(root); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.player, 0)
        rv.addWidget(split, 1)
        self.setCentralWidget(root)
        self.details_scroll.setVisible(self.a_details.isChecked())

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
        if hasattr(self, "track_tip"):
            self.track_tip.tip.set_dark(dark)
        self.model.refresh_all()

    def set_notation(self, notation: str):
        self.s.notation = notation
        self.s.save()
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
        self.f_keys.set_notation(self.s.notation)
        self.f_keywords.retranslate()
        self.f_types.retranslate()
        self.details.retranslate()
        self.player.retranslate()
        if hasattr(self, "queue_panel"):
            self.queue_panel.retranslate()
            self.queue_dock.setWindowTitle(tr("queue_title"))
            self.update_progress()
        for i, mode in enumerate(GROUP_BY):
            self.group_combo.setItemText(i, tr(f"group_{mode}"))
        self.model.refresh_all()
        self.update_count()
        self._fit_narrow_columns()

    def _fit_narrow_columns(self):
        """Narrow columns are never narrower than their header in this language (e.g. "Tonalidad", "Energía");
        +26 px leaves room for the padding and the sort arrow. Widths the user has set are kept."""
        fm = self.table.horizontalHeader().fontMetrics()
        saved = self.s.column_widths or {}
        self._sizing_columns = True
        try:
            for col, w in self.NARROW_COLUMNS.items():
                if col in saved:
                    continue
                i = TrackModel.COLUMNS.index(col)
                header = self.model.headerData(i, Qt.Horizontal) or ""
                self.table.setColumnWidth(i, max(w, fm.horizontalAdvance(header) + 26))
        finally:
            self._sizing_columns = False

    # ------------------------------------------------------------------ data
    def build_query(self) -> Query:
        q = Query(
            text=self.f_text.text().strip(), title=self.f_title.text().strip(), artist=self.f_artist.text().strip(),
            album=self.f_album.text().strip(),
            year_from=self.f_year_from.value() or None, year_to=self.f_year_to.value() or None,
            bpm_min=self.f_bpm_from.value() or None, bpm_max=self.f_bpm_to.value() or None,
            keys=self.f_keys.selected(), keywords=self.f_keywords.selected(), audio_types=self.f_types.selected(),
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
        if hasattr(self, "player"):
            self.sync_deck()

    def refresh_keywords(self):
        self.details.set_known_genres(self.lib.db.distinct_values("genre"))
        kws = self.lib.db.all_keywords()
        self.f_keywords.set_keywords(kws)
        self.details.set_known_keywords([k for k, _ in kws])

    # ------------------------------------------------------------------ grouping
    def set_group_by(self, _=None):
        mode = self.group_combo.currentData() or "none"
        self.s.group_by = mode
        self.model.set_group_by(mode)
        self.proxy.invalidate()
        self._update_spans()

    def _update_spans(self, *_):
        """Group headers are painted across the row by every cell (TrackDelegate._paint_header) - no table spans,
        which do not work with columns moved by the user. Only a repaint is needed."""
        self.table.viewport().update()

    # ------------------------------------------------------------------ column order
    def column_order(self) -> list[str]:
        """Column names from left to right as shown."""
        hh = self.table.horizontalHeader()
        return [TrackModel.COLUMNS[hh.logicalIndex(v)] for v in range(hh.count())]

    def apply_column_order(self, order: list[str]):
        """Arrange the columns like `order` (names); unknown names are ignored, columns missing there (e.g. new in
        this version) keep their place next to the column they follow by default."""
        cols = TrackModel.COLUMNS
        known = [c for c in dict.fromkeys(order) if c in cols]
        for c in cols:                         # insert missing columns after their default predecessor
            if c not in known:
                i = cols.index(c)
                prev = next((cols[j] for j in range(i - 1, -1, -1) if cols[j] in known), None)
                known.insert(known.index(prev) + 1 if prev else 0, c)
        hh = self.table.horizontalHeader()
        hh.blockSignals(True)
        for target, name in enumerate(known):
            hh.moveSection(hh.visualIndex(cols.index(name)), target)
        hh.blockSignals(False)
        self._update_spans()

    def apply_column_widths(self, widths: dict):
        for name, w in widths.items():
            if name in TrackModel.COLUMNS and isinstance(w, (int, float)) and 20 <= w <= 2000:
                self.table.setColumnWidth(TrackModel.COLUMNS.index(name), int(w))

    def _column_resized(self, logical, _old, new):
        hh = self.table.horizontalHeader()
        if getattr(self, "_sizing_columns", False) or hh.visualIndex(logical) == hh.count() - 1:
            return            # set by the program, or the last column (it fills the rest of the window)
        self.s.column_widths = {**(self.s.column_widths or {}), TrackModel.COLUMNS[logical]: int(new)}
        self._widths_timer.start()          # written to disk after the drag (and anyway when the app closes)

    def _save_column_widths(self):
        self._save_settings_quietly()

    def _sort_changed(self, logical, order):
        if not 0 <= logical < len(TrackModel.COLUMNS):
            return
        self.s.sort_column = TrackModel.COLUMNS[logical]
        self.s.sort_descending = order == Qt.DescendingOrder
        self._save_settings_quietly()

    def _save_settings_quietly(self):
        try:
            self.s.save()
        except OSError:
            pass

    def _column_moved(self, *_):
        self.s.column_order = self.column_order()
        self._update_spans()
        self._save_settings_quietly()

    def reset_columns(self):
        """Default order and widths of the columns (the sort column stays)."""
        self._sizing_columns = True
        try:
            self.apply_column_order(list(TrackModel.COLUMNS))
            for col, w in zip(range(len(TrackModel.COLUMNS)), self.DEFAULT_WIDTHS):
                self.table.setColumnWidth(col, w)
            self.s.column_order, self.s.column_widths = [], {}
            self._fit_narrow_columns()
        finally:
            self._sizing_columns = False
        self._save_settings_quietly()

    def _header_menu(self, pos):
        m = QMenu(self)
        a = m.addAction(tr("reset_columns"), self.reset_columns)
        a.setEnabled(self.column_order() != list(TrackModel.COLUMNS) or bool(self.s.column_widths))
        m.exec(self.table.horizontalHeader().viewport().mapToGlobal(pos))

    def _enter_pressed(self):
        cur = self.table.currentIndex()
        if cur.isValid() and cur.data(HEADER_ROLE):            # Enter on a group header opens / closes it
            self.toggle_group(cur.data(GROUP_ROLE))
        else:
            self.load_into_deck(autoplay=True)

    def _header_clicked(self, idx):
        if not idx.isValid() or not idx.data(HEADER_ROLE):
            return
        self.toggle_group(idx.data(GROUP_ROLE))

    def toggle_group(self, key: str, collapse: bool | None = None):
        c = self.model.collapsed
        if collapse is None:
            collapse = key not in c
        (c.add if collapse else c.discard)(key)
        self.proxy.refilter()
        self._update_spans()

    def set_all_groups(self, collapse: bool):
        self.model.collapsed = set(self.model.group_keys()) if collapse else set()
        self.proxy.refilter()
        self._update_spans()

    def update_count(self):
        self.count_lbl.setText(tr("tracks_count", shown=len(self.model.tracks), total=self.lib.db.count()))

    def reset_filters(self):
        self.harmonic_btn.setChecked(False)
        for w in (self.f_text, self.f_title, self.f_artist, self.f_album):
            w.blockSignals(True); w.clear(); w.blockSignals(False)
        for w in (self.f_year_from, self.f_year_to, self.f_bpm_from, self.f_bpm_to):
            w.blockSignals(True); w.setValue(0); w.blockSignals(False)
        self.f_keys.blockSignals(True); self.f_keys.clear(); self.f_keys.blockSignals(False); self.f_keys.update()
        self.f_keywords.blockSignals(True); self.f_keywords.set_selected([]); self.f_keywords.blockSignals(False)
        self.f_types.set_selected([])
        self.refresh()

    # ------------------------------------------------------------------ selection
    def selected_ids(self) -> list[int]:
        if not hasattr(self, "table"):
            return []
        return [i.data(ID_ROLE) for i in self.table.selectionModel().selectedRows() if i.data(ID_ROLE) is not None]

    def selected_rows(self) -> list[dict]:
        ids = set(self.selected_ids())
        return [r for r in self.model.rows if r["id"] in ids]

    def selection_changed(self, *_):
        rows = self.selected_rows()
        if rows and len(rows) <= 200:
            self.lib.fill_album_artist(rows)           # older libraries: read the album artist once
        old_ids = [r["id"] for r in self.details.rows]
        if self.details.has_unsaved() and old_ids != [r["id"] for r in rows]:
            ch = self.details.changes()
            self.details.dirty.clear()          # prevents asking again from the refresh below
            if QMessageBox.question(self, tr("app_title"), tr("ask_save_changes"),
                                    QMessageBox.Save | QMessageBox.Discard, QMessageBox.Save) == QMessageBox.Save:
                self._apply_detail_changes(old_ids, ch)
                rows = self.selected_rows()
        self.details.set_rows(rows, keep_edits=True)
        # selecting only shows details; a title goes into the deck by double-click (or Enter / context menu)

    def _menu_row(self) -> dict | None:
        """The title the user is acting on: the current row if it is selected, else the first selected one."""
        rows = self.selected_rows()
        cur = self.table.currentIndex()
        cur_id = cur.data(ID_ROLE) if cur.isValid() else None
        return next((r for r in rows if r["id"] == cur_id), rows[0] if rows else None)

    def load_into_deck(self, autoplay: bool = False, track_id: int | None = None):
        """Load a title into the deck (double-click, Enter, context menu), optionally playing it."""
        if track_id is not None:
            row = next((r for r in self.model.rows if r["id"] == track_id), None)
        else:
            row = self._menu_row()
        if row:
            self.player.set_track(row, autoplay=autoplay)

    def sync_deck(self):
        """Keep the deck's key/tempo/title in step with the library after an analysis or an edit.
        Same file -> the player only refreshes its display, playback is not interrupted."""
        cur = self.player.row
        if not cur:
            return
        row = next((r for r in self.model.rows if r["id"] == cur["id"]), None) or self.lib.db.get_track(cur["id"])
        if row and any(row.get(k) != cur.get(k)
                       for k in ("key", "bpm", "energy", "title", "artist", "album", "year", "sections")):
            self.player.set_track(row)

    # -- context menu filters
    def filter_by_artist(self, row: dict):
        self.f_artist.setText(row.get("artist") or "")

    def filter_by_album(self, row: dict):
        self.f_album.setText(row.get("album") or "")

    def filter_by_key(self, row: dict, semitones: bool = False):
        """Key filter = the track's key; with semitones also the keys 1 semitone lower and higher."""
        if row.get("key"):
            codes = [row["key"]]
            if semitones:
                from ..keys import Key
                k = Key.from_code(row["key"])
                codes += [k.transpose(-1).code, k.transpose(1).code]
            self.f_keys.set_selected(codes)

    def set_details_visible(self, on: bool):
        """Show/hide the right sidebar; the track list takes the freed space. Remembered across starts."""
        if hasattr(self, "details_scroll"):
            self.details_scroll.setVisible(on)
        if self.s.show_details != on:
            self.s.show_details = on
            self.s.save()

    # ------------------------------------------------------------------ Mixed In Key import
    def import_mik(self):
        from .mik_dialog import MikImportDialog
        dlg = MikImportDialog(self.lib, self)
        if not dlg.exec() or dlg.plan is None:
            return
        plan, opts = dlg.plan, dlg.options()
        import threading
        from ..mik_import import apply_import
        self.msg_lbl.setText(tr("mik_running"))
        self.progress.setRange(0, 0); self.progress.show()
        self.a_mik.setEnabled(False)

        def work():
            try:
                res, err = apply_import(self.lib, plan, **opts), None
            except Exception as e:                      # report instead of losing it in a thread
                res, err = None, f"{type(e).__name__}: {e}"
            self.mik_bridge.done.emit((res, err))
        threading.Thread(target=work, daemon=True, name="mik-import").start()

    def _mik_done(self, payload):
        res, err = payload
        self.progress.hide(); self.progress.setRange(0, 1)
        self.a_mik.setEnabled(True)
        if err:
            self.msg_lbl.setText(tr("mik_error", e=err))
            QMessageBox.warning(self, tr("mik_title"), tr("mik_error", e=err))
            return
        self.msg_lbl.setText(tr("mik_done", updated=res.updated, added=res.added, keywords=res.keywords))
        self.refresh_keywords()
        self.refresh()

    def update_deck(self):
        """The loaded track is the reference for the 'fits' and 'vs. deck' columns."""
        row = self.player.row
        if not row:
            self.model.set_deck(None, None, None)
            return
        k = Key.from_code(row.get("key"))
        if k and self.player.semitones:
            k = k.transpose(self.player.semitones)
        self.model.set_deck(row["id"], k.code if k else None, row.get("bpm"))

    def play_adjacent(self, delta: int, keep_playing: bool):
        """Load the previous/next title relative to the one in the deck, in the list's current order
        (filter + sorting); keeps playing if the player was playing. Stops at the end of the list."""
        n = self.proxy.rowCount()
        if not n:
            return
        deck_id = self.player.row["id"] if self.player.row else None
        pos = next((r for r in range(n) if self.proxy.index(r, 0).data(ID_ROLE) == deck_id), None)
        if pos is None:                       # deck title filtered out / empty deck: start from the selection
            cur = self.table.currentIndex()
            pos = cur.row() if cur.isValid() else -1
        target = pos + delta
        while 0 <= target < n and self.proxy.index(target, 0).data(HEADER_ROLE):   # skip group headers
            target += delta
        if target < 0 or target >= n:
            if delta > 0 and keep_playing:
                self.player.stop()      # end of the list
            return
        self.table.selectRow(target)
        self.table.scrollTo(self.proxy.index(target, 0))
        self.load_into_deck(autoplay=keep_playing, track_id=self.proxy.index(target, 0).data(ID_ROLE))

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

    def analyze_sections(self):
        """Song sections for the selected tracks, or for every analysed track that has none yet (or only
        sections from the first detection, which had no confidence values)."""
        ids = self.selected_ids()
        if ids:                     # explicitly chosen: also tracks whose sections were edited by hand
            manual = [t for t in self.lib.db.get_tracks(ids) if t.get("sections_manual") and t.get("sections") != "[]"]
            if manual and QMessageBox.question(self, tr("app_title"), tr("sec_redetect_manual_ask", n=len(manual))) \
                    != QMessageBox.Yes:
                ids = [i for i in ids if i not in {t["id"] for t in manual}]
            self.lib.allow_section_detection(ids)
        else:
            ids = self.lib.db.ids_needing_sections()
        self.enqueue(ids, keep_tag_values=True, sections_only=True)

    def analyze_now_selected(self):
        # "analyse now": to the top of the queue
        self.enqueue(self.selected_ids(), keep_tag_values=False, front=True)

    def enqueue(self, ids: list[int], keep_tag_values: bool, front: bool = False, sections_only: bool = False):
        if not ids:
            return
        if not self.queue.is_busy():
            self._batch_done = 0
        self.queue.add(ids, keep_tag_values=keep_tag_values, front=front, sections_only=sections_only)
        # the queue panel only opens on request; the toolbar button shows that work is pending
        self.update_progress()
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

    # ------------------------------------------------------------------ busy dots for running analyses
    BUSY_FPS = 24
    BUSY_CYCLE = 1.2                        # seconds for the light to run through the three dots

    def _update_busy(self):
        from ..analysis_queue import RUNNING
        busy = {i.track_id for i in self.queue.items() if i.state == RUNNING}
        changed = busy ^ self.model.busy_ids
        self.model.busy_ids = busy
        if changed:
            self._repaint_fit(changed)
        if not hasattr(self, "_busy_timer"):
            self._busy_timer = QTimer(self, interval=1000 // self.BUSY_FPS, timeout=self._busy_tick)
        if busy and not self._busy_timer.isActive():
            self._busy_timer.start()
        elif not busy:
            self._busy_timer.stop()

    def _busy_tick(self):
        self.model.busy_phase = (self.model.busy_phase + 1 / (self.BUSY_FPS * self.BUSY_CYCLE)) % 1.0
        self._repaint_fit(self.model.busy_ids)

    def _repaint_fit(self, ids):
        col = TrackModel.COLUMNS.index("fit")
        for i, r in enumerate(self.model.rows):
            if r["id"] in ids:
                idx = self.model.index(i, col)
                self.model.dataChanged.emit(idx, idx, [Qt.DisplayRole])

    def on_queue_event(self, event: str, item):
        if event == "finished" and item is not None:
            self._batch_done += 1
            row = self.lib.db.get_track(item.track_id)
            if row:
                row["keywords"] = self.lib.db.keywords_for([row["id"]])[row["id"]]
                self.model.update_row(row)
                if item.track_id in self.selected_ids():
                    self.selection_changed()
                if self.player.row and self.player.row["id"] == item.track_id:
                    self.sync_deck()
        self._update_busy()
        self.queue_panel.schedule()
        self.update_progress()
        if not self.queue.is_busy() and event == "finished":
            c = self.queue.counts()
            self.msg_lbl.setText(tr("queue_all_done", done=c[DONE], errors=c[ERROR]))
            self.refresh()

    def update_progress(self):
        c = self.queue.counts()
        busy = c[WAITING] + c[RUNNING]
        self.a_queue.setText(tr('queue') + (f"  {busy}" if busy else ""))
        self.a_queue.setToolTip(tr("queue_busy_tip", running=c[RUNNING], waiting=c[WAITING]) if busy
                                else tr("queue_idle_tip"))
        btn = self.toolbar.widgetForAction(self.a_queue)
        if btn is not None and btn.property("busy") != bool(busy):
            btn.setProperty("busy", bool(busy))       # amber highlight while analyses run or wait
            btn.style().unpolish(btn); btn.style().polish(btn)
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
        idx = self.table.indexAt(pos)
        if idx.isValid() and idx.data(HEADER_ROLE):            # group header: open / close groups
            menu = self.build_group_menu(idx.data(GROUP_ROLE))
            menu.exec(self.table.viewport().mapToGlobal(pos))
            return
        menu = self.build_track_menu()
        if menu is not None:
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def build_group_menu(self, key: str) -> QMenu:
        m = QMenu(self)
        closed = key in self.model.collapsed
        m.addAction(tr("expand") if closed else tr("collapse"), lambda: self.toggle_group(key))
        m.addSeparator()
        m.addAction(tr("expand_all"), lambda: self.set_all_groups(False))
        m.addAction(tr("collapse_all"), lambda: self.set_all_groups(True))
        return m

    def build_track_menu(self) -> QMenu | None:
        """Context menu of the track list:
        Play | Filter by > Artist, Album, Key, Key ±1 semitone | Analyse now, Re-analyse, Write tags,
        Reset manual values, Add keyword | Remove from library."""
        rows = self.selected_rows()
        if not rows:
            return None
        r0 = self._menu_row()
        m = QMenu(self)
        m.addAction("▶  " + tr("play"), lambda: self.load_into_deck(autoplay=True))
        m.addSeparator()
        fm = m.addMenu(tr("filter_by"))
        a = fm.addAction(tr("artist"), lambda: self.filter_by_artist(r0))
        a.setEnabled(bool(r0 and r0.get("artist")))
        a = fm.addAction(tr("album"), lambda: self.filter_by_album(r0))
        a.setEnabled(bool(r0 and r0.get("album")))
        a = fm.addAction(tr("key"), lambda: self.filter_by_key(r0))
        a.setEnabled(bool(r0 and r0.get("key")))
        a = fm.addAction(tr("filter_key_semitones"), lambda: self.filter_by_key(r0, semitones=True))
        a.setEnabled(bool(r0 and r0.get("key")))
        m.addSeparator()
        m.addAction("⚡  " + tr("analyze_now"), self.analyze_now_selected)
        m.addAction(tr("reanalyze"), self.reanalyze_selected)
        m.addAction(tr("write_tags"), self.write_tags_selected)
        m.addAction(tr("reset_manual"), lambda: (self.lib.reset_manual(self.selected_ids()), self.reanalyze_selected()))
        m.addAction(tr("sec_delete_selected"), self.clear_sections_selected)
        m.addAction(tr("add_keyword"), self._prompt_keywords)
        m.addAction(tr("export_csv") + "\tCtrl+E", self.export_selected)
        m.addSeparator()
        m.addAction(tr("remove"), self.remove_selected)
        return m

    def _prompt_keywords(self):
        text, ok = QInputDialog.getText(self, tr("keywords"), tr("add_keyword"))
        if ok and text.strip():
            self.add_keywords([p.strip() for p in text.split(",") if p.strip()])

    # ------------------------------------------------------------------ CSV export
    def export_target(self, count: int) -> str | None:
        """Where the export goes: asked with a save dialog (which confirms replacing a file), or the folder and
        file name from the settings (an existing file is only replaced after asking). None = cancelled."""
        from PySide6.QtCore import QStandardPaths
        from ..export import default_filename
        folder = self.s.export_folder or QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        name = default_filename(self.s.export_filename, count)
        path = os.path.join(folder, name)
        if not self.s.export_ask:
            if not os.path.exists(path):
                return path
            box = QMessageBox(QMessageBox.Question, tr("app_title"), tr("export_exists", file=path), parent=self)
            replace = box.addButton(tr("export_replace"), QMessageBox.DestructiveRole)
            other = box.addButton(tr("export_other"), QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(other)
            box.exec()
            if box.clickedButton() is replace:
                return path
            if box.clickedButton() is not other:
                return None
        got, _ = QFileDialog.getSaveFileName(self, tr("export_csv_title", n=count), path, "CSV (*.csv)")
        if not got:
            return None
        if not got.lower().endswith(".csv"):
            got += ".csv"
            if os.path.exists(got) and QMessageBox.question(self, tr("app_title"), tr("export_exists", file=got)) \
                    != QMessageBox.Yes:
                return None
        if self.s.export_ask:                         # next time the dialog starts in the same folder
            self.s.export_folder = os.path.dirname(got)
            self.s.save()
        return got

    def export_selected(self):
        """CSV export of the tracks selected in the list (in list order); the whole library: MCP export_csv."""
        sel = {i.row(): i.data(ID_ROLE) for i in self.table.selectionModel().selectedRows()} if hasattr(self, "table") else {}
        ids = [sel[r] for r in sorted(sel)]
        if not ids:
            self.msg_lbl.setText(tr("export_none"))
            return
        path = self.export_target(len(ids))
        if not path:
            return
        try:
            n = self.lib.export_csv(path, ids)
        except OSError as e:
            QMessageBox.warning(self, tr("app_title"), tr("export_failed", file=path, error=str(e)))
            return
        self.msg_lbl.setText(tr("export_done", n=n, file=path))

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
            self.details.retag_patterns = self.s.retag_patterns
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

    # ------------------------------------------------------------------ cue counts
    def _start_cue_counter(self):
        """Count the Serato cue points of tracks that have none stored yet (libraries from before the cue
        column), in the background and in small batches, updating the list as results come in."""
        import threading
        from PySide6.QtCore import QObject, Signal
        from ..library import write_log

        class _Bridge(QObject):
            batch = Signal(object)          # [(track_id, count)]

        self._cue_cancel = threading.Event()
        self._cue_bridge = _Bridge()
        self._cue_bridge.batch.connect(self._cue_counts_ready)
        cancel, bridge, lib = self._cue_cancel, self._cue_bridge, self.lib

        def work():
            while not cancel.is_set():
                try:
                    done = lib.fill_cue_counts(batch=100, cancel=cancel)
                except Exception as e:                  # never let this break the app
                    write_log(f"cue count: {type(e).__name__}: {e}")
                    return
                if not done:
                    return
                try:
                    bridge.batch.emit(done)
                except RuntimeError:                    # window already gone
                    return
        self._cue_thread = threading.Thread(target=work, daemon=True, name="cue-count")
        self._cue_thread.start()

    def _cue_counts_ready(self, done):
        counts = dict(done)
        col = TrackModel.COLUMNS.index("cues")
        for i, r in enumerate(self.model.rows):
            n = counts.get(r["id"])
            if n is not None and r.get("cue_count") != n:
                r["cue_count"] = n
                idx = self.model.index(i, col)
                self.model.dataChanged.emit(idx, idx)

    def _update_rows(self, track_id: int, **values):
        """Change a few values of one list row in place (no full refresh)."""
        for i, r in enumerate(self.model.rows):
            if r["id"] == track_id:
                r.update(values)
                self.model.dataChanged.emit(self.model.index(i, 0), self.model.index(i, self.model.columnCount() - 1))

    def _deck_sections_edited(self, track_id: int, sections: list):
        """Song sections edited in the deck waveform: stored as hand-edited (an analysis keeps them)."""
        import json
        self.lib.set_sections(track_id, sections)
        self._update_rows(track_id, sections=json.dumps(sections, separators=(",", ":")), sections_manual=1)

    def _deck_cues_edited(self, track_id: int, cues: list):
        """Cue points edited in the deck waveform: written into the file's Serato tag."""
        t = self.lib.db.get_track(track_id)
        if not t:
            return
        try:
            n = self.player.run_on_file(t["path"], lambda: self.lib.set_cues(track_id, cues))
        except Exception as e:
            QMessageBox.warning(self, tr("app_title"), tr("cue_write_failed", file=t["filename"], error=str(e)))
            self.player.reload_markers()          # show what is really in the file
            return
        self._update_rows(track_id, cue_count=n)

    def redetect_sections(self, track_id: int):
        """Detect the song sections of a track again (also after editing or deleting them by hand)."""
        self.lib.allow_section_detection([track_id])
        self.enqueue([track_id], keep_tag_values=True, sections_only=True)

    def clear_sections_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        self.lib.clear_sections(ids)
        for tid in ids:
            self._update_rows(tid, sections="[]", sections_manual=1)
        self.sync_deck()

    def _deck_cues_read(self, path: str, n: int):
        """The deck read the cue points of its file anyway: keep the stored count up to date."""
        for i, r in enumerate(self.model.rows):
            if r.get("path") == path and r.get("cue_count") != n:
                self.lib.db.update_track(r["id"], cue_count=n)
                r["cue_count"] = n
                idx = self.model.index(i, TrackModel.COLUMNS.index("cues"))
                self.model.dataChanged.emit(idx, idx)

    def closeEvent(self, e):
        if self.details.has_unsaved():
            if QMessageBox.question(self, tr("app_title"), tr("ask_save_changes"),
                                    QMessageBox.Save | QMessageBox.Discard, QMessageBox.Save) == QMessageBox.Save:
                self.details.save()
        self.player.shutdown()
        self.queue.shutdown()
        self._cue_cancel.set()
        self.s.save()
        if self.worker:
            self.worker.cancel.set()
        if self.thread:
            self.thread.quit(); self.thread.wait(15000)
        super().closeEvent(e)
