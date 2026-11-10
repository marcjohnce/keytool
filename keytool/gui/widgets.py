"""Reusable widgets: track table model, key filter grid, keyword filter, detail panel."""
from __future__ import annotations

import math
import os
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, QStringListModel
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QComboBox, QCompleter, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget, QButtonGroup,
)

from ..i18n import fmt_datetime, fmt_number, tr
from ..keys import Key, all_keys
from .theme import camelot_color

SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2
DECK_ROLE = Qt.UserRole + 3      # True for the row that is loaded in the deck
FIT_ROLE = Qt.UserRole + 4       # 2 same key, 1 harmonic neighbour, 0 no match, None unknown


def harmonic_fit(code: str | None, deck_code: str | None) -> int | None:
    """How well a key mixes with the deck's key on the Camelot wheel."""
    k, d = Key.from_code(code), Key.from_code(deck_code)
    if not k or not d:
        return None
    if k.code == d.code:
        return 2
    kn, dn = k.camelot_number, d.camelot_number
    if kn == dn:                                    # relative major / minor
        return 1
    if k.minor == d.minor and (kn - dn) % 12 in (1, 11):
        return 1
    return 0


def source_text(r: dict, field: str) -> str:
    """'manual' / 'from file tags' / 'auto · confidence 87 %' for bpm or key."""
    if r.get(f"{field}_manual"):
        return tr("manual")
    if r.get(f"{field}_source") == "tags":
        return tr("from_tags")
    if r.get(f"{field}_source") == "mik":
        return tr("from_mik")
    conf = r.get(f"{field}_confidence")
    return f"{tr('auto')} · {tr('confidence')} {int((conf or 0) * 100)} %"




def fmt_duration(sec) -> str:
    if not sec:
        return ""
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class Translatable:
    """Mixin: register (callable, key) pairs and re-apply them on language change."""

    def _tr_init(self):
        self._tr_items: list[tuple[Callable[[str], None], str]] = []

    def t(self, setter: Callable[[str], None], key: str):
        self._tr_items.append((setter, key))
        setter(tr(key))

    def retranslate(self):
        for setter, key in self._tr_items:
            setter(tr(key))


# =========================================================================== table model
class TrackModel(QAbstractTableModel):
    COLUMNS = ["fit", "title", "artist", "album", "key", "bpm", "delta", "energy", "duration", "year", "keywords",
               "genre", "comment", "ext", "bitrate", "status", "analyzed_ok_at", "path"]
    HEADER_KEYS = {"ext": "format", "delta": "delta_deck"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.notation = "camelot"
        self.dark = True
        self.deck_id: int | None = None
        self.deck_key: str | None = None      # key the deck currently sounds in (incl. transposition)
        self.deck_bpm: float | None = None

    def set_deck(self, track_id: int | None, key_code: str | None, bpm: float | None):
        if (track_id, key_code, bpm) == (self.deck_id, self.deck_key, self.deck_bpm):
            return
        self.deck_id, self.deck_key, self.deck_bpm = track_id, key_code, bpm
        if self.rows:
            for col in ("fit", "delta", "title"):
                c = self.COLUMNS.index(col)
                self.dataChanged.emit(self.index(0, c), self.index(len(self.rows) - 1, c))

    def _delta(self, r: dict) -> float | None:
        if not self.deck_bpm or not r.get("bpm"):
            return None
        return (r["bpm"] - self.deck_bpm) / self.deck_bpm * 100.0

    # -- data
    def set_rows(self, rows: list[dict]):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def update_row(self, row: dict):
        for i, r in enumerate(self.rows):
            if r["id"] == row["id"]:
                row.setdefault("keywords", r.get("keywords", []))
                self.rows[i] = row
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.COLUMNS) - 1))
                return

    def refresh_all(self):
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(self.COLUMNS) - 1))
        self.headerDataChanged.emit(Qt.Horizontal, 0, len(self.COLUMNS) - 1)

    # -- Qt API
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    # -- drag & drop to other applications (browser upload, audio player, Explorer, DJ software)
    def flags(self, index):
        f = super().flags(index)
        return (f | Qt.ItemIsDragEnabled) if index.isValid() else f

    def mimeTypes(self):
        return ["text/uri-list", "text/plain"]

    def supportedDragActions(self):
        return Qt.CopyAction

    def mimeData(self, indexes):
        from PySide6.QtCore import QMimeData, QUrl
        rows = sorted({i.row() for i in indexes if i.isValid()})
        paths = [self.rows[r]["path"] for r in rows if os.path.exists(self.rows[r]["path"])]
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])     # file list (CF_HDROP on Windows)
        md.setText("\n".join(paths))                          # plain paths for text fields / terminals
        return md

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            col = self.COLUMNS[section]
            return tr(self.HEADER_KEYS.get(col, col))
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        col = self.COLUMNS[index.column()]
        v = r.get(col)
        is_deck = self.deck_id is not None and r["id"] == self.deck_id
        if role == ID_ROLE:
            return r["id"]
        if role == DECK_ROLE:
            return is_deck
        if role == FIT_ROLE:
            return harmonic_fit(r.get("key"), self.deck_key)
        if col == "fit":
            fit = harmonic_fit(r.get("key"), self.deck_key)
            if role == SORT_ROLE:
                return -1 if is_deck else (3 if fit is None else 2 - fit)
            if role == Qt.ToolTipRole and fit is not None:
                return tr("in_deck") if is_deck else tr(("fit_none", "fit_near", "fit_same")[fit])
            if role == Qt.AccessibleTextRole and fit is not None:
                return tr(("fit_none", "fit_near", "fit_same")[fit])
            return None
        if col == "delta":
            d = self._delta(r)
            if role == Qt.DisplayRole:
                if is_deck:
                    return tr("in_deck")
                if d is None:
                    return ""
                return f"{'+' if d >= 0 else '−'}{fmt_number(abs(d))} %"
            if role == SORT_ROLE:
                return -1.0 if is_deck else (abs(d) if d is not None else 1e9)
            if role == Qt.ForegroundRole:
                if is_deck:
                    return QColor("#F0A53A" if self.dark else "#B4650A")
                if d is not None and abs(d) > 6:
                    return QColor("#8D93A4" if self.dark else "#6B7385")
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if role == Qt.ToolTipRole and self.deck_bpm:
                return tr("delta_tip", bpm=f"{self.deck_bpm:.1f}")
            return None
        if role == Qt.DisplayRole:
            if col == "bpm":
                return str(int(v + 0.5)) if v else ""          # rounded half up; exact value in the tooltip
            if col == "key":
                k = Key.from_code(v)
                return k.format(self.notation) if k else ""
            if col == "duration":
                return fmt_duration(v)
            if col == "keywords":
                return ", ".join(v or [])
            if col == "ext":
                return (v or "").upper()
            if col == "status":
                return tr(f"st_{v}") if v else ""
            if col in ("year", "energy", "bitrate"):
                return str(v) if v else ""
            if col == "analyzed_ok_at":
                return fmt_datetime(v)
            if col == "comment":
                return (v or "").replace("\n", " ⏎ ")
            return v or ""
        if role == SORT_ROLE:
            if col == "key":
                k = Key.from_code(v)
                return (k.camelot_number * 2 + (0 if k.minor else 1)) if k else 999
            if col in ("bpm", "year", "duration", "energy", "bitrate", "analyzed_ok_at"):
                return float(v) if v else 1e9
            if col == "keywords":
                return ", ".join(v or []).lower()
            return str(v or "").lower()
        if role == ID_ROLE:
            return r["id"]
        if role == Qt.ForegroundRole:
            if col in ("artist", "album", "duration", "year", "ext", "bitrate", "analyzed_ok_at", "path", "genre"):
                return QColor("#9CA2B3" if self.dark else "#535B6E")
            if col == "status" and v == "error":
                return QColor("#FF6B6B" if self.dark else "#C62828")
        if role == Qt.FontRole and col in ("title", "bpm"):
            from PySide6.QtGui import QFont
            f = QFont(); f.setWeight(QFont.DemiBold)
            return f
        if role == Qt.TextAlignmentRole and col in ("bpm", "duration", "bitrate"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.TextAlignmentRole and col in ("key", "year", "ext", "energy"):
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            if col == "bpm" and r.get("bpm"):
                return f"{r['bpm']:.2f} BPM – {source_text(r, 'bpm')}"
            if col == "key" and r.get("key"):
                k = Key.from_code(r["key"])
                return f"{k.long_name(tr.lang)} · {k.camelot()} · {k.sharps()} / {k.flats()} – {source_text(r, 'key')}"
            if col == "status" and r.get("error"):
                return r["error"]
            if col in ("path", "comment"):
                return v
        return None


# =========================================================================== table delegate
class TrackDelegate(QStyledItemDelegate):
    """Paints the redesigned cells: fit glyph, key chip, energy meter and the deck marker."""

    def __init__(self, model: TrackModel, parent=None):
        super().__init__(parent)
        self.m = model

    def paint(self, painter, option, index):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPen
        from .theme import CHIP_TEXT
        col = self.m.COLUMNS[index.column()]
        if col not in ("fit", "key", "energy"):
            super().paint(painter, option, index)
        else:
            # background (selection) only, content painted below
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            w = opt.widget
            (w.style() if w else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, opt, painter, w)
        pal = option.palette
        text_col = pal.color(QPalette.Text)
        line_col = QColor("#34405A" if self.m.dark else "#CDD2DD")
        r = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if col == "fit":
            fit = index.data(FIT_ROLE)
            c = r.center()
            rad = 6.5
            if fit == 2:
                painter.setPen(Qt.NoPen); painter.setBrush(text_col)
                painter.drawEllipse(c, rad, rad)
            elif fit == 1:
                painter.setPen(QPen(text_col, 1.6)); painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(c, rad - 0.8, rad - 0.8)
                painter.setPen(Qt.NoPen); painter.setBrush(text_col)
                painter.drawPie(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), 90 * 16, 180 * 16)
            elif fit == 0:
                painter.setPen(QPen(QColor("#8D93A4" if self.m.dark else "#6B7385"), 1.3)); painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(c, rad - 0.7, rad - 0.7)
        elif col == "key":
            src = index.model().mapToSource(index) if hasattr(index.model(), "mapToSource") else index
            code = self.m.rows[src.row()].get("key")
            txt = index.data(Qt.DisplayRole) or ""
            bg = camelot_color(code, self.m.dark)
            if bg is not None and txt:
                f = painter.font(); f.setBold(True); painter.setFont(f)
                fm = painter.fontMetrics()
                wch = max(40, fm.horizontalAdvance(txt) + 14)
                chip = QRectF(r.center().x() - wch / 2, r.center().y() - 11, wch, 22)
                painter.setPen(Qt.NoPen); painter.setBrush(bg)
                painter.drawRoundedRect(chip, 6, 6)
                painter.setPen(CHIP_TEXT)
                painter.drawText(chip, Qt.AlignCenter, txt)
        elif col == "energy":
            src = index.model().mapToSource(index) if hasattr(index.model(), "mapToSource") else index
            e = self.m.rows[src.row()].get("energy")
            if e:
                bw, gap, bh = 4.0, 2.0, 13.0
                total = 10 * bw + 9 * gap
                x0 = r.center().x() - total / 2
                y0 = r.center().y() - bh / 2
                painter.setPen(Qt.NoPen)
                for i in range(10):
                    painter.setBrush(text_col if i < int(e) else line_col)
                    painter.drawRoundedRect(QRectF(x0 + i * (bw + gap), y0, bw, bh), 1, 1)
        if index.column() == 0 and index.data(DECK_ROLE):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#F0A53A" if self.m.dark else "#B4650A"))
            painter.drawRect(QRectF(r.left(), r.top(), 3, r.height()))
        painter.restore()


class KeyChipDelegate(QStyledItemDelegate):
    """Draws the cell text on a rounded chip in the Camelot colour of `code_for(index)` (style sheets
    override plain background roles, so the colour has to be painted)."""

    def __init__(self, code_for, dark_for, parent=None):
        super().__init__(parent)
        self.code_for, self.dark_for = code_for, dark_for

    def paint(self, painter, option, index):
        from PySide6.QtCore import QRectF
        from .theme import CHIP_TEXT
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        w = opt.widget
        (w.style() if w else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, opt, painter, w)
        bg = camelot_color(self.code_for(index), self.dark_for())
        if bg is None or not text:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        f = painter.font(); f.setBold(True); painter.setFont(f)
        r = QRectF(option.rect)
        wch = min(r.width() - 8, max(40, painter.fontMetrics().horizontalAdvance(text) + 14))
        chip = QRectF(r.center().x() - wch / 2, r.center().y() - 10, wch, 20)
        painter.setPen(Qt.NoPen); painter.setBrush(bg)
        painter.drawRoundedRect(chip, 6, 6)
        painter.setPen(CHIP_TEXT)
        painter.drawText(chip, Qt.AlignCenter, text)
        painter.restore()


# =========================================================================== key filter
class KeyFilter(QWidget):
    """Clickable Camelot wheel: outer ring = major (B), inner ring = minor (A), 12B at the top.

    Left click toggles a key, right click selects the key plus its harmonic neighbours,
    a click in the centre clears the selection.
    """
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.notation = "camelot"
        self.dark = True
        self._selected: set[str] = set()
        self._hover: str | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(220, 220)
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    # ------------------------------------------------------------------ public API
    def set_notation(self, notation: str):
        self.notation = notation
        self.update()

    def apply_colors(self, dark: bool):
        self.dark = dark
        self.update()

    def selected(self) -> list[str]:
        return sorted(self._selected)

    def set_selected(self, codes: list[str]):
        self._selected = set(codes)
        self.update()
        self.changed.emit()

    def clear(self):
        self.set_selected([])

    # ------------------------------------------------------------------ geometry
    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return min(w, 340)

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(300, 300)

    def _radii(self):
        side = min(self.width(), self.height()) - 4
        R = side / 2
        return R, R * 0.66, R * 0.33   # outer, ring border, inner hole

    def _center(self):
        from PySide6.QtCore import QPointF
        return QPointF(self.width() / 2, self.height() / 2)

    @staticmethod
    def _angle(n: int) -> float:
        # Qt angles: 0 deg = 3 o'clock, counter-clockwise. 12 at the top, numbers increase clockwise.
        return 90.0 - (n % 12) * 30.0

    def _key_at(self, pos) -> str | None:
        c = self._center()
        dx, dy = pos.x() - c.x(), c.y() - pos.y()
        dist = math.hypot(dx, dy)
        R, mid, hole = self._radii()
        if dist > R or dist < hole:
            return None
        ang = math.degrees(math.atan2(dy, dx))
        n = int(round((90.0 - ang) / 30.0)) % 12
        n = 12 if n == 0 else n
        minor = dist < mid
        for k in all_keys():
            if k.camelot_number == n and k.minor == minor:
                return k.code
        return None

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self._center()
        R, mid, hole = self._radii()
        bg = self.palette().color(self.backgroundRole())
        any_sel = bool(self._selected)
        font = QFont(self.font())
        for k in all_keys():
            r_out, r_in = (mid, hole) if k.minor else (R, mid)
            start = self._angle(k.camelot_number) - 15
            outer = QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out)
            inner = QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in)
            path = QPainterPath()
            path.arcMoveTo(outer, start)
            path.arcTo(outer, start, 30)
            path.arcTo(inner, start + 30, -30)
            path.closeSubpath()
            col = QColor(camelot_color(k.code, self.dark))
            sel = k.code in self._selected
            if any_sel and not sel:
                col.setAlpha(70)
            if k.code == self._hover and not sel:
                col = col.lighter(115)
                col.setAlpha(255 if not any_sel else 150)
            p.setBrush(col)
            p.setPen(QPen(bg, 2))
            p.drawPath(path)
            # label
            a = math.radians(self._angle(k.camelot_number))
            rl = (r_out + r_in) / 2
            pt = QPointF(c.x() + math.cos(a) * rl, c.y() - math.sin(a) * rl)
            text = k.format(self.notation)
            font.setBold(sel)
            font.setPointSizeF(max(7.0, R / (16 if len(text) <= 2 else 19)))
            p.setFont(font)
            txt = QColor("#111111")
            if any_sel and not sel:
                txt = QColor(self.palette().color(self.foregroundRole()))
                txt.setAlpha(150)
            p.setPen(txt)
            box = QRectF(pt.x() - 30, pt.y() - 12, 60, 24)
            p.drawText(box, Qt.AlignCenter, text)
        # centre: selection count / hint
        p.setPen(self.palette().color(self.foregroundRole()))
        font.setBold(True)
        font.setPointSizeF(max(8.0, R / 14))
        p.setFont(font)
        label = str(len(self._selected)) + " ✕" if self._selected else tr("all_keys")
        p.drawText(QRectF(c.x() - hole, c.y() - hole, 2 * hole, 2 * hole), Qt.AlignCenter, label)
        p.end()

    # ------------------------------------------------------------------ interaction
    def mouseMoveEvent(self, e):
        code = self._key_at(e.position())
        if code != self._hover:
            self._hover = code
            self.setCursor(Qt.PointingHandCursor if code or self._in_centre(e.position()) else Qt.ArrowCursor)
            if code:
                k = Key.from_code(code)
                self.setToolTip(f"{k.camelot()} · {k.sharps()} / {k.flats()} · {k.long_name(tr.lang)}")
            else:
                self.setToolTip(tr("reset_filters") if self._selected else "")
            self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def _in_centre(self, pos) -> bool:
        c = self._center()
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) < self._radii()[2]

    def mousePressEvent(self, e):
        pos = e.position()
        code = self._key_at(pos)
        if code is None:
            if self._in_centre(pos) and self._selected:
                self.clear()
            return
        key = Key.from_code(code)
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if e.button() == Qt.RightButton and not shift:
            # key + harmonic neighbours (Camelot +-1, relative major/minor)
            self.set_selected([k.code for k in key.compatible()])
        elif e.button() in (Qt.LeftButton, Qt.RightButton):
            if not shift:
                group = {code}
            else:
                # Shift: the key and the keys 1 semitone higher/lower; with right-click each with its
                # harmonic neighbours. Toggled as a group: on if the clicked key was off, else off.
                keys = [key, key.transpose(1), key.transpose(-1)]
                if e.button() == Qt.RightButton:
                    keys = [n for k in keys for n in k.compatible()]
                group = {k.code for k in keys}
            sel = set(self._selected)
            if code in sel:
                sel -= group
            else:
                sel |= group
            self.set_selected(sorted(sel))


# =========================================================================== keyword filter
class KeywordFilter(QWidget, Translatable):
    changed = Signal()
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tr_init()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("", "any")        # default: keywords are combined with OR
        self.mode.addItem("", "all")
        self.mode.currentIndexChanged.connect(lambda _: self.changed.emit())
        row.addWidget(self.mode)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        self.list.itemChanged.connect(lambda _: self.changed.emit())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list)
        self.empty = QLabel()
        self.empty.setObjectName("dim")
        lay.addWidget(self.empty)
        self.t(self.empty.setText, "no_keywords")
        self.retranslate()

    def retranslate(self):
        super().retranslate()
        self.mode.setItemText(0, tr("match_any"))
        self.mode.setItemText(1, tr("match_all"))

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu

        it = self.list.itemAt(pos)
        if not it:
            return
        kw = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction(tr("rename"), lambda: self.rename_requested.emit(kw))
        m.addAction(tr("delete_everywhere"), lambda: self.delete_requested.emit(kw))
        m.exec(self.list.mapToGlobal(pos))

    def set_keywords(self, items: list[tuple[str, int]]):
        checked = set(k.lower() for k in self.selected())
        self.list.blockSignals(True)
        self.list.clear()
        for kw, n in items:
            it = QListWidgetItem(f"{kw}  ({n})")
            it.setData(Qt.UserRole, kw)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if kw.lower() in checked else Qt.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.empty.setVisible(not items)
        self.list.setVisible(bool(items))

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def set_selected(self, kws: list[str]):
        low = {k.lower() for k in kws}
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole).lower() in low else Qt.Unchecked)
        self.list.blockSignals(False)
        self.changed.emit()

    def match_mode(self) -> str:
        return self.mode.currentData()


# =========================================================================== detail panel
# =========================================================================== detail panel
def fmt_size(n) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{fmt_number(n)} {unit}"
        n /= 1024


MIXED = object()


def _common(rows: list[dict], field: str):
    vals = {r.get(field) for r in rows}
    return vals.pop() if len(vals) == 1 else MIXED


class DetailPanel(QWidget, Translatable):
    """Editable track details: metadata, key/energy/tempo; read-only file info; keywords.

    Edits are collected and applied with "Save" (Ctrl+S). With several tracks selected, only the
    fields that were actually changed are applied to all of them."""
    save_requested = Signal(dict)
    reveal_requested = Signal(str)
    keywords_added = Signal(list)
    keyword_removed = Signal(str)
    keyword_clicked = Signal(str)

    TEXT_FIELDS = ("title", "artist", "album", "album_artist")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tr_init()
        self.notation = "camelot"
        self.rows: list[dict] = []
        self._loading = False
        self.dirty: set[str] = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        self.heading = QLabel(); self.heading.setObjectName("heading"); self.heading.setWordWrap(True)
        self.sub = QLabel(); self.sub.setObjectName("dim"); self.sub.setWordWrap(True)
        lay.addWidget(self.heading)
        lay.addWidget(self.sub)

        # ---------------- metadata
        from PySide6.QtWidgets import QGroupBox, QPlainTextEdit, QSpinBox
        self.g_meta = QGroupBox(); self.t(self.g_meta.setTitle, "g_metadata")
        fm = QFormLayout(self.g_meta)
        fm.setLabelAlignment(Qt.AlignLeft)
        self.edits: dict[str, QLineEdit] = {}
        for f in self.TEXT_FIELDS:
            e = QLineEdit()
            e.textEdited.connect(lambda _=None, f=f: self._mark(f))
            self.edits[f] = e
            lbl = QLabel(); self.t(lbl.setText, f)
            fm.addRow(lbl, e)
        from PySide6.QtGui import QIntValidator
        self.year = QLineEdit(); self.year.setValidator(QIntValidator(0, 9999, self.year)); self.year.setMaxLength(4)
        self.year.setFixedWidth(90)
        self.year.textEdited.connect(lambda _: self._mark("year"))
        lbl = QLabel(); self.t(lbl.setText, "year"); fm.addRow(lbl, self.year)
        self.genre = QComboBox(); self.genre.setEditable(True); self.genre.setInsertPolicy(QComboBox.NoInsert)
        self.genre.lineEdit().textEdited.connect(lambda _: self._mark("genre"))
        self.genre.activated.connect(lambda _: self._mark("genre"))
        lbl = QLabel(); self.t(lbl.setText, "genre"); fm.addRow(lbl, self.genre)
        self.comment = QPlainTextEdit(); self.comment.setFixedHeight(58); self.comment.setTabChangesFocus(True)
        self.comment.textChanged.connect(lambda: self._mark("comment"))
        lbl = QLabel(); self.t(lbl.setText, "comment"); fm.addRow(lbl, self.comment)
        lay.addWidget(self.g_meta)

        # ---------------- key / energy / tempo
        self.g_music = QGroupBox(); self.t(self.g_music.setTitle, "g_music")
        fk = QFormLayout(self.g_music)
        self.key = QComboBox()
        self.key.addItem("–", "")
        for k in all_keys():
            self.key.addItem("", k.code)
        self.key.activated.connect(lambda _: self._mark("key"))
        lbl = QLabel(); self.t(lbl.setText, "key"); fk.addRow(lbl, self.key)
        self.key_info = QLabel(); self.key_info.setObjectName("dim"); self.key_info.setWordWrap(True)
        fk.addRow("", self.key_info)
        erow = QHBoxLayout()
        self.energy = QSpinBox(); self.energy.setRange(0, 10); self.energy.setSpecialValueText("–")
        self.energy.valueChanged.connect(lambda _: (self._mark("energy"), self._energy_bar()))
        self.energy_bar = QLabel(); self.energy_bar.setObjectName("dim")
        erow.addWidget(self.energy); erow.addWidget(self.energy_bar, 1)
        lbl = QLabel(); self.t(lbl.setText, "energy"); fk.addRow(lbl, erow)
        brow = QHBoxLayout()
        self.bpm = QDoubleSpinBox(); self.bpm.setRange(0, 400); self.bpm.setDecimals(1); self.bpm.setSpecialValueText("–")
        self.bpm.valueChanged.connect(lambda _: self._mark("bpm"))
        self.half = QPushButton("½"); self.half.setFixedWidth(40); self.half.setToolTip("BPM / 2")
        self.half.clicked.connect(lambda: self.bpm.setValue(self.bpm.value() / 2))
        self.double = QPushButton("x2"); self.double.setFixedWidth(40); self.double.setToolTip("BPM x 2")
        self.double.clicked.connect(lambda: self.bpm.setValue(self.bpm.value() * 2))
        brow.addWidget(self.bpm, 1); brow.addWidget(self.half); brow.addWidget(self.double)
        lbl = QLabel(); self.t(lbl.setText, "tempo"); fk.addRow(lbl, brow)
        self.bpm_info = QLabel(); self.bpm_info.setObjectName("dim")
        fk.addRow("", self.bpm_info)
        lay.addWidget(self.g_music)

        # ---------------- save / revert
        srow = QHBoxLayout()
        self.save_btn = QPushButton(); self.save_btn.setObjectName("primary"); self.t(self.save_btn.setText, "save")
        self.save_btn.clicked.connect(self.save)
        self.revert_btn = QPushButton(); self.t(self.revert_btn.setText, "revert")
        self.revert_btn.clicked.connect(lambda: self.set_rows(self.rows))
        srow.addWidget(self.save_btn, 1); srow.addWidget(self.revert_btn)
        lay.addLayout(srow)
        self.save_hint = QLabel(); self.save_hint.setObjectName("dim"); self.save_hint.setWordWrap(True)
        lay.addWidget(self.save_hint)
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence.Save, self, activated=self.save, context=Qt.WidgetWithChildrenShortcut)

        # ---------------- file info (read only)
        self.g_file = QGroupBox(); self.t(self.g_file.setTitle, "g_file")
        ff = QFormLayout(self.g_file)
        self.f_type, self.f_bitrate, self.f_size, self.f_duration, self.f_tags, self.f_analyzed = (QLabel() for _ in range(6))
        self.f_path = QLabel(); self.f_path.setWordWrap(True); self.f_path.setObjectName("dim")
        for w in (self.f_type, self.f_bitrate, self.f_size, self.f_duration, self.f_tags, self.f_analyzed, self.f_path):
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for key, w in (("filetype", self.f_type), ("bitrate", self.f_bitrate), ("filesize", self.f_size),
                       ("duration", self.f_duration), ("tag_values", self.f_tags), ("analyzed_ok_at", self.f_analyzed),
                       ("path", self.f_path)):
            lbl = QLabel(); self.t(lbl.setText, key); ff.addRow(lbl, w)
        self.reveal_btn = QPushButton(); self.t(self.reveal_btn.setText, "open_folder")
        self.reveal_btn.clicked.connect(lambda: self.rows and self.reveal_requested.emit(self.rows[0]["path"]))
        ff.addRow("", self.reveal_btn)
        lay.addWidget(self.g_file)

        # ---------------- keywords
        self.g_kw = QGroupBox(); self.t(self.g_kw.setTitle, "keywords")
        kv = QVBoxLayout(self.g_kw)
        kw_row = QHBoxLayout()
        self.kw_edit = QLineEdit()
        self.t(self.kw_edit.setPlaceholderText, "add_keyword")
        self.kw_completer_model = QStringListModel()
        comp = QCompleter(self.kw_completer_model, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.kw_edit.setCompleter(comp)
        self.kw_edit.returnPressed.connect(self._add_kw)
        self.kw_add = QPushButton(); self.t(self.kw_add.setText, "add")
        self.kw_add.clicked.connect(self._add_kw)
        kw_row.addWidget(self.kw_edit, 1); kw_row.addWidget(self.kw_add)
        kv.addLayout(kw_row)
        self.kw_hint = QLabel(); self.kw_hint.setObjectName("dim"); self.kw_hint.setWordWrap(True)
        kv.addWidget(self.kw_hint)
        self.kw_list = QListWidget(); self.kw_list.setMinimumHeight(90)
        self.kw_list.itemDoubleClicked.connect(lambda it: self.keyword_clicked.emit(it.data(Qt.UserRole)))
        kv.addWidget(self.kw_list)
        self.kw_remove = QPushButton(); self.t(self.kw_remove.setText, "remove_kw")
        self.kw_remove.clicked.connect(self._remove_kw)
        kv.addWidget(self.kw_remove)
        lay.addWidget(self.g_kw)
        lay.addStretch(1)
        # long labels (e.g. "Artiste de l'album") wrap onto two lines instead of pushing the fields out
        for form in (fm, fk, ff):
            for i in range(form.rowCount()):
                item = form.itemAt(i, QFormLayout.LabelRole)
                if item is not None and isinstance(item.widget(), QLabel):
                    item.widget().setWordWrap(True)
                    item.widget().setMaximumWidth(118)
        self.retranslate()

    # ------------------------------------------------------------------ texts
    def retranslate(self):
        super().retranslate()
        self.kw_hint.setText(tr("kw_hint"))
        self.set_notation(self.notation)

    def set_notation(self, notation: str):
        self.notation = notation
        for i in range(1, self.key.count()):
            k = Key.from_code(self.key.itemData(i))
            self.key.setItemText(i, f"{k.format(notation)}   ({k.long_name(tr.lang)})")
        dirty = set(self.dirty)
        self.set_rows(self.rows, keep_edits=bool(dirty))

    def set_known_keywords(self, kws: list[str]):
        self.kw_completer_model.setStringList(kws)

    def set_known_genres(self, genres: list[str]):
        cur = self.genre.currentText()
        self._loading = True
        self.genre.clear()
        self.genre.addItems(genres)
        self.genre.setEditText(cur)
        self._loading = False

    # ------------------------------------------------------------------ editing state
    def _mark(self, field: str):
        if self._loading or not self.rows:
            return
        self.dirty.add(field)
        self._update_buttons()

    def _update_buttons(self):
        on = bool(self.dirty) and bool(self.rows)
        self.save_btn.setEnabled(on)
        self.revert_btn.setEnabled(on)
        if len(self.rows) > 1:
            self.save_hint.setText(tr("multi_edit_hint", n=len(self.rows)))
        else:
            self.save_hint.setText(tr("unsaved_changes") if on else "")

    def _energy_bar(self):
        v = self.energy.value()
        self.energy_bar.setText("▮" * v + "▯" * (10 - v) if v else "")

    def changes(self) -> dict:
        out: dict = {}
        for f in self.dirty:
            if f in self.TEXT_FIELDS:
                out[f] = self.edits[f].text().strip()
            elif f == "year":
                t = self.year.text().strip()
                out[f] = int(t) if t.isdigit() and int(t) > 0 else None
            elif f == "genre":
                out[f] = self.genre.currentText().strip()
            elif f == "comment":
                out[f] = self.comment.toPlainText().strip()
            elif f == "energy":
                out[f] = self.energy.value() or None
            elif f == "bpm":
                out[f] = round(self.bpm.value(), 2) if self.bpm.value() > 0 else None
            elif f == "key":
                out[f] = self.key.currentData() or None
        return out

    def save(self):
        ch = self.changes()
        if ch and self.rows:
            self.dirty.clear()
            self._update_buttons()
            self.save_requested.emit(ch)

    def has_unsaved(self) -> bool:
        return bool(self.dirty)

    # ------------------------------------------------------------------ keywords
    def _add_kw(self):
        parts = [p.strip() for p in self.kw_edit.text().split(",") if p.strip()]
        if parts and self.rows:
            self.keywords_added.emit(parts)
        self.kw_edit.clear()

    def _remove_kw(self):
        for it in self.kw_list.selectedItems():
            self.keyword_removed.emit(it.data(Qt.UserRole))

    # ------------------------------------------------------------------ fill
    def set_rows(self, rows: list[dict], keep_edits: bool = False):
        same = [r["id"] for r in rows] == [r["id"] for r in self.rows]
        if keep_edits and same and self.dirty:
            self.rows = rows
            self._fill_readonly()
            return
        self.rows = rows
        self.dirty.clear()
        self._loading = True
        try:
            self._fill()
        finally:
            self._loading = False
        self._update_buttons()

    def _set_text(self, edit: QLineEdit, val):
        if val is MIXED:
            edit.setText("")
            edit.setPlaceholderText(tr("mixed_values"))
        else:
            edit.setText(val or "")
            edit.setPlaceholderText("")

    def _fill(self):
        rows = self.rows
        has = bool(rows)
        for w in (self.g_meta, self.g_music, self.g_kw, self.reveal_btn):
            w.setEnabled(has)
        self.reveal_btn.setVisible(len(rows) == 1)
        if not rows:
            self.heading.setText(tr("no_selection"))
            self.sub.setText("")
            for e in self.edits.values():
                self._set_text(e, "")
            self.genre.setEditText(""); self.comment.setPlainText(""); self._set_text(self.year, "")
            self.key.setCurrentIndex(0); self.energy.setValue(0); self.bpm.setValue(0)
            self.key_info.setText(""); self.bpm_info.setText("")
            self.kw_list.clear()
            self._fill_readonly()
            self._energy_bar()
            return
        one = len(rows) == 1
        r0 = rows[0]
        if one:
            self.heading.setText(r0["title"] or r0["filename"])
            self.sub.setText(" · ".join(b for b in (r0["artist"], str(r0["year"] or "")) if b))
        else:
            self.heading.setText(tr("n_selected", n=len(rows)))
            self.sub.setText("")
        for f, e in self.edits.items():
            self._set_text(e, _common(rows, f))
        y = _common(rows, "year")
        self._set_text(self.year, y if y is MIXED else (str(y) if y else ""))
        g = _common(rows, "genre")
        self.genre.setEditText("" if g is MIXED else (g or ""))
        self.genre.lineEdit().setPlaceholderText(tr("mixed_values") if g is MIXED else "")
        c = _common(rows, "comment")
        self.comment.setPlainText("" if c is MIXED else (c or ""))
        self.comment.setPlaceholderText(tr("mixed_values") if c is MIXED else "")
        k = _common(rows, "key")
        if k is MIXED:
            self.key.setCurrentIndex(-1)
            self.key.setPlaceholderText(tr("mixed_values"))
        else:
            self.key.setCurrentIndex(max(0, self.key.findData(k or "")))
        e = _common(rows, "energy")
        self.energy.setValue(0 if e is MIXED else (e or 0))
        self.energy.setToolTip(tr("mixed_values") if e is MIXED else tr("energy_tip"))
        b = _common(rows, "bpm")
        self.bpm.setValue(0 if b is MIXED else (b or 0))
        self.bpm.setToolTip(tr("mixed_values") if b is MIXED else "")
        self._energy_bar()
        # info lines (single selection)
        if one and r0["bpm"]:
            self.bpm_info.setText(source_text(r0, "bpm"))
        else:
            self.bpm_info.setText("")
        kk = Key.from_code(r0["key"]) if one else None
        if kk:
            comp = ", ".join(c.format(self.notation) for c in kk.compatible()[1:])
            self.key_info.setText(f"{source_text(r0, 'key')}\n↔ {comp}")
        else:
            self.key_info.setText("")
        self._fill_readonly()
        self._fill_keywords()

    def _fill_readonly(self):
        rows = self.rows
        if len(rows) == 1:
            r = rows[0]
            self.f_type.setText((r["ext"] or "").upper())
            self.f_bitrate.setText(f"{r['bitrate']} kbit/s" if r.get("bitrate") else "–")
            self.f_size.setText(fmt_size(r.get("size")) or "–")
            self.f_duration.setText(fmt_duration(r.get("duration")) or "–")
            self.f_tags.setText(f"BPM {r['tag_bpm'] or '–'} · {tr('key')} {r['tag_key'] or '–'}")
            self.f_analyzed.setText(fmt_datetime(r.get("analyzed_ok_at")) or tr("never"))
            self.f_path.setText(r["path"] + (f"\n⚠ {r['error']}" if r.get("error") else ""))
        elif rows:
            types = sorted({(r["ext"] or "").upper() for r in rows})
            self.f_type.setText(", ".join(types))
            brs = [r["bitrate"] for r in rows if r.get("bitrate")]
            self.f_bitrate.setText(f"{min(brs)}–{max(brs)} kbit/s" if brs and min(brs) != max(brs)
                                   else (f"{brs[0]} kbit/s" if brs else "–"))
            self.f_size.setText(fmt_size(sum(r.get("size") or 0 for r in rows)) + f"  ({tr('total')})")
            self.f_duration.setText(fmt_duration(sum(r.get("duration") or 0 for r in rows)))
            self.f_tags.setText("")
            self.f_analyzed.setText("")
            self.f_path.setText("")
        else:
            for w in (self.f_type, self.f_bitrate, self.f_size, self.f_duration, self.f_tags, self.f_analyzed, self.f_path):
                w.setText("")

    def _fill_keywords(self):
        rows = self.rows
        self.kw_list.clear()
        counts: dict[str, int] = {}
        for r in rows:
            for k in r.get("keywords", []):
                counts[k] = counts.get(k, 0) + 1
        for kw in sorted(counts, key=str.lower):
            label = kw if counts[kw] == len(rows) or len(rows) == 1 else f"{kw}  ({counts[kw]}/{len(rows)})"
            it = QListWidgetItem("🏷  " + label)
            it.setData(Qt.UserRole, kw)
            self.kw_list.addItem(it)
