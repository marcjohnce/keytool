"""Dialog: research songs in external databases by key and BPM range (opens filtered searches in the browser)."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from ..i18n import tr
from ..keys import Key
from ..research import MAX_AUDIOKEYCHAIN_KEYS, SOURCES, build_links, describe_criteria
from .widgets import KeyFilter


class ResearchDialog(QDialog):
    def __init__(self, settings, keys: list[str], bpm_min: float | None, bpm_max: float | None,
                 dark: bool = True, notation: str = "camelot", parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle(tr("research_title"))
        self.setMinimumSize(760, 640)
        lay = QHBoxLayout(self)

        # ---------------- left: criteria
        left = QVBoxLayout()
        intro = QLabel(tr("research_intro")); intro.setWordWrap(True); intro.setObjectName("dim")
        left.addWidget(intro)
        kl = QLabel(tr("research_keys")); kl.setWordWrap(True)
        left.addWidget(kl)
        self.wheel = KeyFilter()
        self.wheel.set_notation(notation)
        self.wheel.apply_colors(dark)
        self.wheel.set_selected(keys)
        self.wheel.changed.connect(self.update_links)
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
            w.valueChanged.connect(self.update_links)
        brow.addWidget(self.bmin, 1); brow.addWidget(QLabel("–")); brow.addWidget(self.bmax, 1)
        left.addLayout(brow)
        lay.addLayout(left, 1)

        # ---------------- right: sources + resulting searches
        right = QVBoxLayout()
        g = QGroupBox(tr("research_sources"))
        gv = QVBoxLayout(g)
        chosen = settings.research_sources or list(SOURCES)
        self.src_boxes: dict[str, QCheckBox] = {}
        for src in SOURCES:
            name, _, desc = tr(f"src_{src}").partition(" – ")
            cb = QCheckBox(name)
            cb.setChecked(src in chosen)
            cb.toggled.connect(self.update_links)
            self.src_boxes[src] = cb
            gv.addWidget(cb)
            d = QLabel(desc); d.setWordWrap(True); d.setObjectName("dim"); d.setContentsMargins(24, 0, 0, 6)
            gv.addWidget(d)
        right.addWidget(g)
        right.addWidget(QLabel(tr("research_links")))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda it: self.open_links([it.data(Qt.UserRole)]))
        right.addWidget(self.list, 1)
        self.info = QLabel(); self.info.setObjectName("dim"); self.info.setWordWrap(True)
        right.addWidget(self.info)
        btns = QHBoxLayout()
        self.open_btn = QPushButton(tr("research_open")); self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(lambda: self.open_links(self.links))
        copy = QPushButton(tr("research_copy")); copy.clicked.connect(self.copy_links)
        close = QPushButton(tr("close")); close.clicked.connect(self.accept)
        btns.addWidget(self.open_btn); btns.addWidget(copy); btns.addStretch(); btns.addWidget(close)
        right.addLayout(btns)
        lay.addLayout(right, 1)
        self.links = []
        self.update_links()

    # ------------------------------------------------------------------ logic
    def keys(self) -> list[Key]:
        ks = [Key.from_code(c) for c in self.wheel.selected()]
        return sorted(ks, key=lambda k: (k.camelot_number, not k.minor))

    def sources(self) -> list[str]:
        return [s for s, cb in self.src_boxes.items() if cb.isChecked()]

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

    def update_links(self, *_):
        keys, (lo, hi), srcs = self.keys(), self.bpm_range(), self.sources()
        self.list.clear()
        if not srcs or (not keys and not lo and not hi):
            self.links = []
            self.info.setText(tr("research_none"))
            self.open_btn.setEnabled(False)
            return
        self.links = build_links(keys, lo, hi, srcs)
        for l in self.links:
            notes = []
            if "range" in l.note:
                notes.append(tr("note_range", lo=math.floor(lo or 40), hi=math.ceil(hi or 180)))
            if "too_many_keys" in l.note:
                notes.append(tr("note_too_many", n=MAX_AUDIOKEYCHAIN_KEYS))
            if "manual" in l.note:
                notes.append(tr("note_manual"))
            it = QListWidgetItem(l.label + (f"\n    ↳ {' · '.join(notes)}" if notes else ""))
            it.setData(Qt.UserRole, l)
            it.setToolTip(l.url)
            self.list.addItem(it)
        self.info.setText(describe_criteria(keys, lo, hi))
        self.open_btn.setEnabled(True)
        self.s.research_sources = srcs

    def open_links(self, links):
        if any(l.source == "tunebat" for l in links):
            crit = describe_criteria(self.keys(), *self.bpm_range())
            QApplication.clipboard().setText(crit)
        for l in links:
            QDesktopServices.openUrl(QUrl(l.url))
        msg = tr("research_opened", n=len(links))
        if any(l.source == "tunebat" for l in links):
            msg += "\n" + tr("tunebat_clip", crit=describe_criteria(self.keys(), *self.bpm_range()))
        self.info.setText(msg)
        self.s.save()

    def copy_links(self):
        QApplication.clipboard().setText("\n".join(f"{l.label}\n{l.url}" for l in self.links))
        self.info.setText(tr("research_copied"))
