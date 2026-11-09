"""Reusable widgets: track table model, key filter grid, keyword filter, detail panel."""
from __future__ import annotations

import math
import os
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, QStringListModel
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget, QButtonGroup,
)

from ..i18n import tr
from ..keys import Key, all_keys
from .theme import camelot_color

SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2


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
    COLUMNS = ["title", "artist", "album", "year", "bpm", "key", "duration", "keywords", "genre", "ext", "status", "path"]
    HEADER_KEYS = {"ext": "format"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.notation = "camelot"
        self.dark = True

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
        if role == Qt.DisplayRole:
            if col == "bpm":
                return f"{v:.1f}" if v else ""
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
            if col == "year":
                return str(v) if v else ""
            return v or ""
        if role == SORT_ROLE:
            if col == "key":
                k = Key.from_code(v)
                return (k.camelot_number * 2 + (0 if k.minor else 1)) if k else 999
            if col in ("bpm", "year", "duration"):
                return float(v) if v else 1e9
            if col == "keywords":
                return ", ".join(v or []).lower()
            return str(v or "").lower()
        if role == ID_ROLE:
            return r["id"]
        if role == Qt.BackgroundRole and col == "key":
            return camelot_color(v, self.dark)
        if role == Qt.ForegroundRole:
            if col == "key" and v:
                return QColor("#111111")
            if col == "status" and v == "error":
                return QColor("#ff6b6b")
        if role == Qt.TextAlignmentRole and col in ("bpm", "key", "year", "duration", "ext"):
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            if col == "bpm" and r.get("bpm"):
                src = tr("manual") if r.get("bpm_manual") else f"{tr('confidence')}: {int((r.get('bpm_confidence') or 0) * 100)} %"
                return f"{r['bpm']:.2f} BPM – {src}"
            if col == "key" and r.get("key"):
                k = Key.from_code(r["key"])
                src = tr("manual") if r.get("key_manual") else f"{tr('confidence')}: {int((r.get('key_confidence') or 0) * 100)} %"
                return f"{k.long_name(tr.lang)} · {k.camelot()} · {k.sharps()} / {k.flats()} – {src}"
            if col == "status" and r.get("error"):
                return r["error"]
            if col == "path":
                return v
        return None


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
        # selection outlines on top
        for code in self._selected:
            k = Key.from_code(code)
            r_out, r_in = (mid, hole) if k.minor else (R, mid)
            start = self._angle(k.camelot_number) - 15
            outer = QRectF(c.x() - r_out + 2, c.y() - r_out + 2, 2 * r_out - 4, 2 * r_out - 4)
            inner = QRectF(c.x() - r_in - 2, c.y() - r_in - 2, 2 * r_in + 4, 2 * r_in + 4)
            path = QPainterPath()
            path.arcMoveTo(outer, start + 1)
            path.arcTo(outer, start + 1, 28)
            path.arcTo(inner, start + 29, -28)
            path.closeSubpath()
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(self.palette().color(self.foregroundRole()), 2.5))
            p.drawPath(path)
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
        if e.button() == Qt.RightButton:
            # key + harmonic neighbours (Camelot +-1, relative major/minor)
            self.set_selected([k.code for k in Key.from_code(code).compatible()])
        elif e.button() == Qt.LeftButton:
            sel = set(self._selected)
            sel.symmetric_difference_update({code})
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
        self.mode.addItem("", "all")
        self.mode.addItem("", "any")
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
        self.mode.setItemText(0, tr("match_all"))
        self.mode.setItemText(1, tr("match_any"))

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
class DetailPanel(QWidget, Translatable):
    bpm_changed = Signal(float)
    key_changed = Signal(str)
    keywords_added = Signal(list)
    keyword_removed = Signal(str)
    keyword_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tr_init()
        self.notation = "camelot"
        self.rows: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.heading = QLabel()
        self.heading.setObjectName("heading")
        self.heading.setWordWrap(True)
        lay.addWidget(self.heading)
        self.sub = QLabel()
        self.sub.setObjectName("dim")
        self.sub.setWordWrap(True)
        self.sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.sub)

        self.form_w = QWidget()
        form = QFormLayout(self.form_w)
        form.setContentsMargins(0, 8, 0, 0)
        # BPM
        bpm_row = QHBoxLayout()
        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(0, 400)
        self.bpm.setDecimals(1)
        self.bpm.setSpecialValueText("–")
        self.bpm_apply = QPushButton()
        self.t(self.bpm_apply.setText, "apply")
        self.bpm_apply.clicked.connect(lambda: self.bpm_changed.emit(self.bpm.value()))
        self.bpm.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.half = QPushButton("½")
        self.half.setFixedWidth(40)
        self.half.setToolTip("BPM / 2")
        self.half.clicked.connect(lambda: self.bpm.setValue(self.bpm.value() / 2))
        self.double = QPushButton("x2")
        self.double.setFixedWidth(40)
        self.double.setToolTip("BPM x 2")
        self.double.clicked.connect(lambda: self.bpm.setValue(self.bpm.value() * 2))
        for w in (self.bpm, self.half, self.double, self.bpm_apply):
            bpm_row.addWidget(w, 1 if w is self.bpm else 0)
        self.bpm_label = QLabel()
        self.t(self.bpm_label.setText, "bpm")
        form.addRow(self.bpm_label, bpm_row)
        self.bpm_info = QLabel()
        self.bpm_info.setObjectName("dim")
        form.addRow("", self.bpm_info)
        # Key
        key_row = QHBoxLayout()
        self.key = QComboBox()
        self.key.addItem("–", "")
        for k in all_keys():
            self.key.addItem("", k.code)
        self.key_apply = QPushButton()
        self.t(self.key_apply.setText, "apply")
        self.key_apply.clicked.connect(lambda: self.key_changed.emit(self.key.currentData() or ""))
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.key_apply)
        self.key_label = QLabel()
        self.t(self.key_label.setText, "key")
        form.addRow(self.key_label, key_row)
        self.key_info = QLabel()
        self.key_info.setObjectName("dim")
        self.key_info.setWordWrap(True)
        form.addRow("", self.key_info)
        self.tag_info = QLabel()
        self.tag_info.setObjectName("dim")
        self.tag_label = QLabel()
        self.t(self.tag_label.setText, "tag_values")
        form.addRow(self.tag_label, self.tag_info)
        lay.addWidget(self.form_w)

        # Keywords
        self.kw_label = QLabel()
        self.kw_label.setObjectName("heading")
        self.t(self.kw_label.setText, "keywords")
        lay.addSpacing(8)
        lay.addWidget(self.kw_label)
        kw_row = QHBoxLayout()
        self.kw_edit = QLineEdit()
        self.t(self.kw_edit.setPlaceholderText, "add_keyword")
        self.kw_completer_model = QStringListModel()
        comp = QCompleter(self.kw_completer_model, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.kw_edit.setCompleter(comp)
        self.kw_edit.returnPressed.connect(self._add_kw)
        self.kw_add = QPushButton()
        self.kw_add.setObjectName("primary")
        self.t(self.kw_add.setText, "add")
        self.kw_add.clicked.connect(self._add_kw)
        kw_row.addWidget(self.kw_edit, 1)
        kw_row.addWidget(self.kw_add)
        lay.addLayout(kw_row)
        self.kw_hint = QLabel()
        self.kw_hint.setObjectName("dim")
        lay.addWidget(self.kw_hint)
        self.kw_list = QListWidget()
        self.kw_list.itemDoubleClicked.connect(lambda it: self.keyword_clicked.emit(it.data(Qt.UserRole)))
        lay.addWidget(self.kw_list, 1)
        self.kw_remove = QPushButton()
        self.t(self.kw_remove.setText, "remove_kw")
        self.kw_remove.clicked.connect(self._remove_kw)
        lay.addWidget(self.kw_remove)
        self.retranslate()

    def retranslate(self):
        super().retranslate()
        self.kw_hint.setText("Tipp: mehrere mit Komma trennen · Doppelklick filtert" if tr.lang == "de"
                             else "Tip: separate several with commas · double-click filters")
        self.set_notation(self.notation)

    def set_notation(self, notation: str):
        self.notation = notation
        for i in range(1, self.key.count()):
            k = Key.from_code(self.key.itemData(i))
            self.key.setItemText(i, f"{k.format(notation)}   ({k.long_name(tr.lang)})")
        self.set_rows(self.rows)

    def set_known_keywords(self, kws: list[str]):
        self.kw_completer_model.setStringList(kws)

    def _add_kw(self):
        parts = [p.strip() for p in self.kw_edit.text().split(",") if p.strip()]
        if parts and self.rows:
            self.keywords_added.emit(parts)
        self.kw_edit.clear()

    def _remove_kw(self):
        for it in self.kw_list.selectedItems():
            self.keyword_removed.emit(it.data(Qt.UserRole))

    def set_rows(self, rows: list[dict]):
        self.rows = rows
        has = bool(rows)
        self.form_w.setEnabled(has)
        for w in (self.kw_edit, self.kw_add, self.kw_list, self.kw_remove):
            w.setEnabled(has)
        self.kw_list.clear()
        if not rows:
            self.heading.setText(tr("no_selection"))
            self.sub.setText("")
            self.bpm.setValue(0)
            self.key.setCurrentIndex(0)
            self.bpm_info.setText("")
            self.key_info.setText("")
            self.tag_info.setText("")
            return
        if len(rows) == 1:
            r = rows[0]
            self.heading.setText(r["title"] or r["filename"])
            bits = [r["artist"], r["album"], str(r["year"] or ""), fmt_duration(r["duration"]), (r["ext"] or "").upper()]
            self.sub.setText(" · ".join(b for b in bits if b) + f"\n{r['path']}")
            if r.get("error"):
                self.sub.setText(self.sub.text() + f"\n⚠ {r['error']}")
            self.bpm.setValue(r["bpm"] or 0)
            idx = self.key.findData(r["key"] or "")
            self.key.setCurrentIndex(max(0, idx))
            self.bpm_info.setText(
                tr("manual") if r["bpm_manual"] else
                (f"{tr('auto')} · {tr('confidence')} {int((r['bpm_confidence'] or 0) * 100)} %" if r["bpm"] else ""))
            k = Key.from_code(r["key"])
            if k:
                comp = ", ".join(c.format(self.notation) for c in k.compatible()[1:])
                src = tr("manual") if r["key_manual"] else f"{tr('auto')} · {tr('confidence')} {int((r['key_confidence'] or 0) * 100)} %"
                self.key_info.setText(f"{k.long_name(tr.lang)} · {src}\n↔ {comp}")
            else:
                self.key_info.setText("")
            self.tag_info.setText(f"BPM {r['tag_bpm'] or '–'} · {tr('key')} {r['tag_key'] or '–'}")
            kws = r.get("keywords", [])
        else:
            self.heading.setText(tr("n_selected", n=len(rows)))
            self.sub.setText("")
            self.bpm.setValue(0)
            self.key.setCurrentIndex(0)
            self.bpm_info.setText("")
            self.key_info.setText("")
            self.tag_info.setText("")
            # keywords common to the selection are listed, others shown dimmed with count
            counts: dict[str, int] = {}
            for r in rows:
                for k in r.get("keywords", []):
                    counts[k] = counts.get(k, 0) + 1
            kws = sorted(counts, key=str.lower)
        for kw in kws:
            label = kw
            if len(rows) > 1:
                n = sum(1 for r in rows if kw in r.get("keywords", []))
                if n < len(rows):
                    label = f"{kw}  ({n}/{len(rows)})"
            it = QListWidgetItem("🏷  " + label)
            it.setData(Qt.UserRole, kw)
            self.kw_list.addItem(it)
