"""Research dialog: build a Karaoke-Version search by key(s) and BPM range and open it in the browser.

Karaoke-Version answers automated requests with a bot challenge, so KeyTool no longer fetches results
itself; it only composes the search URL (all keys, optionally +-1 semitone, and the BPM range in one search).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..i18n import fmt_number, tr
from ..keys import Key
from ..research import search_url, with_semitones
from .widgets import KeyFilter

WHEEL = 300      # px; fixed so the dialog stays compact and the wheel never overlaps the buttons


class ResearchDialog(QDialog):
    def __init__(self, settings, library, keys: list[str], bpm_min: float | None, bpm_max: float | None,
                 dark: bool = True, notation: str = "camelot", parent=None):
        super().__init__(parent)
        self.s = settings
        self.lib = library
        self.notation = notation
        self.setWindowTitle(tr("research_title"))
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        kl = QLabel(tr("key")); kl.setObjectName("dim")          # same labels as the wheel in the main window
        lay.addWidget(kl)
        holder = QWidget()                 # the wheel's height-for-width confuses QVBoxLayout: give it a fixed box
        holder.setFixedSize(WHEEL, WHEEL)
        self.wheel = KeyFilter(holder)
        self.wheel.set_notation(notation)
        self.wheel.apply_colors(dark)
        self.wheel.set_selected(keys)
        self.wheel.setGeometry(0, 0, WHEEL, WHEEL)
        lay.addWidget(holder, 0, Qt.AlignHCenter)
        wh = QLabel(tr("wheel_hint")); wh.setObjectName("dim"); wh.setWordWrap(True)
        lay.addWidget(wh)

        ext = QPushButton(tr("research_extend")); ext.clicked.connect(self.extend_harmonic)
        ext.setAutoDefault(False)
        lay.addWidget(ext)

        brow = QHBoxLayout()
        brow.addWidget(QLabel(tr("bpm")))
        self.bmin, self.bmax = QDoubleSpinBox(), QDoubleSpinBox()
        for w, v in ((self.bmin, bpm_min), (self.bmax, bpm_max)):
            w.setRange(0, 300); w.setDecimals(1); w.setSpecialValueText("–"); w.setValue(v or 0)
            w.valueChanged.connect(self._update)
        brow.addWidget(self.bmin, 1); brow.addWidget(QLabel("–")); brow.addWidget(self.bmax, 1)
        lay.addLayout(brow)

        self.semitones = QCheckBox(tr("kv_semitones"))
        self.semitones.setChecked(getattr(settings, "research_semitones", True))
        self.semitones.setToolTip(tr("kv_semitones_tip"))
        self.semitones.toggled.connect(self._update)
        lay.addWidget(self.semitones)

        self.preview = QLabel(); self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.preview)

        btns = QHBoxLayout()
        btns.addStretch()
        close = QPushButton(tr("close")); close.setAutoDefault(False); close.clicked.connect(self.accept)
        self.search_btn = QPushButton(tr("kv_search")); self.search_btn.setObjectName("primary")
        self.search_btn.setDefault(True)
        self.search_btn.clicked.connect(self.open_search)
        btns.addWidget(close); btns.addWidget(self.search_btn)
        lay.addLayout(btns)

        self.wheel.changed.connect(self._update)
        self.setFixedWidth(WHEEL + 100)
        self._update()

    # ------------------------------------------------------------------ criteria
    def keys(self) -> list[Key]:
        ks = [Key.from_code(c) for c in self.wheel.selected()]
        return sorted(ks, key=lambda k: (k.camelot_number, not k.minor))

    def bpm_range(self):
        lo, hi = self.bmin.value() or None, self.bmax.value() or None
        if lo and hi and lo > hi:
            lo, hi = hi, lo
        return lo, hi

    def search_keys(self) -> list[Key]:
        """The chosen keys, plus the keys 1 semitone below/above when that option is on."""
        keys = self.keys()
        return with_semitones(keys) if keys and self.semitones.isChecked() else keys

    def url(self) -> str:
        keys, (lo, hi) = self.search_keys(), self.bpm_range()
        return search_url(keys, lo, hi) if keys or lo or hi else ""

    def extend_harmonic(self):
        codes = set(self.wheel.selected())
        for c in list(codes):
            codes.update(k.code for k in Key.from_code(c).compatible())
        self.wheel.set_selected(sorted(codes))

    # ------------------------------------------------------------------ preview / open
    def _update(self, *_):
        chosen, (lo, hi) = self.keys(), self.bpm_range()
        ok = bool(chosen or lo or hi)
        self.search_btn.setEnabled(ok)
        if not ok:
            self.preview.setText(tr("research_none"))
            self._fit_height()
            return
        fmt = lambda ks: ", ".join(k.format(self.notation) for k in ks)
        parts = [fmt(chosen) if chosen else tr("kv_any_key")]
        extra = sorted((k for k in self.search_keys() if k not in chosen),
                       key=lambda k: (k.camelot_number, not k.minor))
        if extra:
            parts.append(tr("kv_neighbours", keys=fmt(extra)))
        if lo or hi:
            num = lambda v: fmt_number(v, 0 if v == int(v) else 1) if v else "…"
            parts.append(f"{num(lo)}–{num(hi)} {tr('bpm')}")
        self.preview.setText(tr("kv_preview", what=" · ".join(parts)))
        self.preview.setToolTip(self.url())
        self._fit_height()

    def _fit_height(self):
        """Word-wrapped labels under-report their height; size the dialog for its fixed width."""
        h = self.layout().totalHeightForWidth(self.width())
        if h > 0:
            self.setFixedHeight(h)

    def open_search(self):
        url = self.url()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def done(self, r):
        self.s.research_semitones = self.semitones.isChecked()
        self.s.save()
        super().done(r)
