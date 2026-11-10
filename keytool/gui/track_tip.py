"""Rich tooltip for the track list: cover artwork, title, artist, album and year.

Shown when the mouse rests on the title, artist or album column. Cover art is read in a worker thread
and kept in a small cache, so moving over the list never blocks the UI."""
from __future__ import annotations

import collections

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .player import CoverView
from .theme import THEMES, styled_font

COVER = 112            # px, cover edge
WIDTH = 340            # px, popup width
RADIUS = 10
CACHE_SIZE = 300       # covers kept in memory (scaled to 2x COVER)
COLUMNS = ("title", "artist", "album")

_NO_COVER = object()   # cache marker: file has no artwork


class _Emitter(QObject):
    done = Signal(str, object)        # path, QImage | None


class _CoverJob(QRunnable):
    def __init__(self, path: str, emitter: _Emitter):
        super().__init__()
        self.path, self.emitter = path, emitter

    def run(self):
        img = None
        try:
            from ..tags import read_cover
            data = read_cover(self.path)
            if data:
                q = QImage.fromData(data)
                if not q.isNull():
                    img = q.scaled(COVER * 2, COVER * 2, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        except Exception:
            img = None
        try:
            self.emitter.done.emit(self.path, img)
        except RuntimeError:            # popup already deleted (app closing)
            pass


class TrackTip(QWidget):
    """Frameless popup card. Call show_for(row, global_pos) / hide()."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.dark = True
        self.path: str | None = None
        self._cache: collections.OrderedDict[str, object] = collections.OrderedDict()
        self._loading: set[str] = set()
        self.emitter = _Emitter()
        self.emitter.done.connect(self._cover_ready)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 16, 10)
        lay.setSpacing(14)
        self.cover = CoverView()
        self.cover.setFixedSize(COVER, COVER)
        self.cover.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.cover, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addStretch(1)
        self.title = QLabel()
        self.artist = QLabel()
        self.album = QLabel()
        self.year = QLabel()
        for w in (self.title, self.artist, self.album, self.year):
            w.setWordWrap(True)
            w.setTextFormat(Qt.PlainText)
        self.title.setFont(styled_font(self.font(), size=14, weight=750, width=110))
        self.artist.setFont(styled_font(self.font(), size=11.5, weight=500))
        self.album.setFont(styled_font(self.font(), size=10.5, weight=400))
        self.year.setFont(styled_font(self.font(), size=10.5, weight=400))
        col.addWidget(self.title)
        col.addWidget(self.artist)
        self.gap = QWidget(); self.gap.setFixedHeight(4)
        col.addWidget(self.gap)
        col.addWidget(self.album)
        col.addWidget(self.year)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.setFixedWidth(WIDTH)
        self.set_dark(True)

    # ------------------------------------------------------------------ look
    def set_dark(self, dark: bool):
        self.dark = dark
        self.cover.dark = dark
        c = THEMES["dark" if dark else "light"]
        self._bg, self._border = QColor(c["tooltip"]), QColor(c["border"])
        self.title.setStyleSheet(f"color: {c['text']}; background: transparent;")
        self.artist.setStyleSheet(f"color: {c['text']}; background: transparent;")
        for w in (self.album, self.year):
            w.setStyleSheet(f"color: {c['dim']}; background: transparent;")
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self._border, 1))
        p.setBrush(self._bg)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

    # ------------------------------------------------------------------ content
    def show_for(self, row: dict, global_pos: QPoint):
        self.path = row.get("path")
        self.title.setText(row.get("title") or row.get("filename") or "")
        self.artist.setText(row.get("artist") or "")
        self.artist.setVisible(bool(row.get("artist")))
        album, year = row.get("album") or "", row.get("year")
        self.album.setText(album)
        self.album.setVisible(bool(album))
        self.year.setText(str(year) if year else "")
        self.year.setVisible(bool(year))
        self.gap.setVisible(bool(album or year))
        self.cover.set_key(row.get("key"))
        self._apply_cover()
        h = self.layout().totalHeightForWidth(WIDTH)
        self.setFixedHeight(max(h, COVER + 20))
        self.move(self._place(global_pos))
        self.show()
        self.raise_()

    def _apply_cover(self):
        cached = self._cache.get(self.path) if self.path else None
        if cached is not None:
            self._cache.move_to_end(self.path)
            self.cover.set_image(None if cached is _NO_COVER else cached)
            return
        self.cover.set_image(None)
        if not self.path:
            return
        self.cover.loading = True
        if self.path not in self._loading:
            self._loading.add(self.path)
            QThreadPool.globalInstance().start(_CoverJob(self.path, self.emitter))

    def _cover_ready(self, path: str, img):
        self._loading.discard(path)
        self._cache[path] = img if img is not None else _NO_COVER
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        if path == self.path:
            self.cover.set_image(img)

    def _place(self, pos: QPoint) -> QPoint:
        """Below-right of the cursor; flipped above/left where the screen ends."""
        x, y = pos.x() + 16, pos.y() + 20
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            if x + self.width() > g.right():
                x = max(g.left(), pos.x() - self.width() - 8)
            if y + self.height() > g.bottom():
                y = max(g.top(), pos.y() - self.height() - 8)
        return QPoint(x, y)


class TrackTipFilter(QObject):
    """Event filter for a track table's viewport: shows TrackTip on the title/artist/album columns and
    hides it as soon as the mouse moves to another cell, leaves, scrolls or clicks."""

    def __init__(self, table, row_for_index, column_name, parent=None):
        super().__init__(parent or table)
        self.table = table
        self.row_for_index = row_for_index        # proxy index -> row dict | None
        self.column_name = column_name            # proxy index -> column key
        self.tip = TrackTip(table)            # owned by the table, still a separate tooltip window
        self._cell = None
        table.viewport().installEventFilter(self)
        table.viewport().setMouseTracking(True)    # needed to see the mouse leave the cell without a button
        table.verticalScrollBar().valueChanged.connect(lambda _: self.hide())
        table.horizontalScrollBar().valueChanged.connect(lambda _: self.hide())

    def hide(self):
        self._cell = None
        self.tip.hide()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.ToolTip:
            idx = self.table.indexAt(ev.pos())
            if idx.isValid() and self.column_name(idx) in COLUMNS:
                row = self.row_for_index(idx)
                if row:
                    from PySide6.QtWidgets import QToolTip
                    QToolTip.hideText()
                    self._cell = (idx.row(), idx.column())
                    self.tip.show_for(row, ev.globalPos())
                    return True
            self.hide()
        elif t == QEvent.MouseMove and self._cell is not None:
            idx = self.table.indexAt(ev.position().toPoint())
            if not idx.isValid() or (idx.row(), idx.column()) != self._cell:
                self.hide()
        elif t in (QEvent.Leave, QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
                   QEvent.KeyPress, QEvent.Hide):
            self.hide()
        return False
