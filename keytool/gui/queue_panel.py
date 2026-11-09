"""Panel showing the analysis queue (running and waiting tracks in queue order, then finished ones)."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..analysis_queue import DONE, ERROR, RUNNING, WAITING, AnalysisQueue
from ..i18n import tr
from .widgets import Translatable


class QueueBridge(QObject):
    """Carries queue events from the worker thread into the GUI thread."""
    changed = Signal(str, object)


def fmt_datetime(ts) -> str:
    if not ts:
        return ""
    return time.strftime("%d.%m.%Y %H:%M" if tr.lang == "de" else "%Y-%m-%d %H:%M", time.localtime(ts))


class QueuePanel(QWidget, Translatable):
    select_track = Signal(int)

    COLS = ("#", "title", "artist", "status", "info")

    def __init__(self, queue: AnalysisQueue, parent=None):
        super().__init__(parent)
        self._tr_init()
        self.queue = queue
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        self.summary = QLabel(); self.summary.setObjectName("dim")
        head.addWidget(self.summary, 1)
        self.now_btn = QPushButton(); self.t(self.now_btn.setText, "analyze_now")
        self.now_btn.clicked.connect(self.analyse_now)
        self.remove_btn = QPushButton(); self.t(self.remove_btn.setText, "queue_remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_wait_btn = QPushButton(); self.t(self.clear_wait_btn.setText, "queue_clear_waiting")
        self.clear_wait_btn.clicked.connect(self.queue.clear_waiting)
        self.clear_done_btn = QPushButton(); self.t(self.clear_done_btn.setText, "queue_clear_finished")
        self.clear_done_btn.clicked.connect(self.queue.clear_finished)
        for b in (self.now_btn, self.remove_btn, self.clear_wait_btn, self.clear_done_btn):
            head.addWidget(b)
        lay.addLayout(head)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        for i, w in enumerate((44, 260, 180, 130, 200)):
            self.table.setColumnWidth(i, w)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._emit_select(r))
        lay.addWidget(self.table)
        # throttle redraws: the queue can report many events per second
        self._timer = QTimer(self, singleShot=True, interval=150, timeout=self.rebuild)
        self.retranslate()
        self.rebuild()

    # ------------------------------------------------------------------ view
    def retranslate(self):
        super().retranslate()
        self.table.setHorizontalHeaderLabels(["#", tr("title"), tr("artist"), tr("status"), tr("queue_info")])
        self.now_btn.setToolTip(tr("analyze_now_tip"))
        self.rebuild()

    def schedule(self):
        if not self._timer.isActive():
            self._timer.start()

    def rebuild(self):
        items = self.queue.items()
        selected = set(self.selected_ids())
        self.table.setRowCount(len(items))
        pal = self.palette()
        dim = pal.color(pal.ColorRole.PlaceholderText)
        n_wait = 0
        for r, it in enumerate(items):
            if it.state == WAITING:
                n_wait += 1
            pos = "▶" if it.state == RUNNING else (str(n_wait) if it.state == WAITING else "")
            if it.state == RUNNING:
                info = tr("queue_running_since", s=int(time.time() - (it.started or time.time())))
            elif it.state == ERROR:
                info = it.error
            elif it.state == DONE:
                info = fmt_datetime(it.finished)
            else:
                info = tr("queue_reanalyse") if not it.keep_tag_values else tr("queue_import")
                if it.crashes:
                    info += " · " + tr("queue_retry")
            vals = (pos, it.title, it.artist, tr(f"queue_{it.state}"), info)
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setData(Qt.UserRole, it.track_id)
                if c in (0, 3):
                    cell.setTextAlignment(Qt.AlignCenter)
                if it.state == DONE:
                    cell.setForeground(dim)
                elif it.state == ERROR:
                    cell.setForeground(QColor("#ff6b6b"))
                elif it.state == RUNNING:
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                if c == 4 and it.state == ERROR:
                    cell.setToolTip(it.error)
                self.table.setItem(r, c, cell)
            if it.track_id in selected:
                self.table.selectRow(r)
        c = self.queue.counts()
        self.summary.setText(tr("queue_summary", running=c[RUNNING], waiting=c[WAITING], done=c[DONE], errors=c[ERROR]))
        self._update_buttons()

    def _update_buttons(self):
        items = {i.track_id: i for i in self.queue.items()}
        sel = [items[t] for t in self.selected_ids() if t in items]
        self.now_btn.setEnabled(any(i.state == WAITING for i in sel))
        self.remove_btn.setEnabled(any(i.state != RUNNING for i in sel))
        c = self.queue.counts()
        self.clear_wait_btn.setEnabled(bool(c[WAITING]))
        self.clear_done_btn.setEnabled(bool(c[DONE] or c[ERROR]))

    # ------------------------------------------------------------------ actions
    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        out = []
        for r in rows:
            cell = self.table.item(r, 0)
            if cell is not None:
                out.append(cell.data(Qt.UserRole))
        return out

    def analyse_now(self):
        ids = self.selected_ids()
        if ids:
            self.queue.move_to_front(ids)

    def remove_selected(self):
        ids = self.selected_ids()
        if ids:
            self.queue.remove(ids)

    def _emit_select(self, row: int):
        cell = self.table.item(row, 0)
        if cell is not None:
            self.select_track.emit(cell.data(Qt.UserRole))

    def _menu(self, pos):
        if not self.selected_ids():
            return
        m = QMenu(self)
        a = m.addAction(tr("analyze_now"), self.analyse_now); a.setEnabled(self.now_btn.isEnabled())
        a = m.addAction(tr("queue_remove"), self.remove_selected); a.setEnabled(self.remove_btn.isEnabled())
        m.addSeparator()
        m.addAction(tr("queue_show_in_list"), lambda: self.select_track.emit(self.selected_ids()[0]))
        m.exec(self.table.viewport().mapToGlobal(pos))
