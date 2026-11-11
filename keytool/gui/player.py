"""Waveform view + audio player for the selected track (system default output device)."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QToolButton, QVBoxLayout, QWidget

from ..i18n import fmt_number, tr
from ..keys import Key
from .. import section_edit as se
from .widgets import Translatable, fmt_duration

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
    HAVE_MULTIMEDIA = True
except Exception:  # pragma: no cover - Qt built without multimedia
    HAVE_MULTIMEDIA = False


def tinted(icon, color: QColor, size: int = 20):
    """Recolour a (monochrome) standard icon so it is visible in light and dark mode."""
    from PySide6.QtGui import QIcon, QPixmap
    src = icon.pixmap(size, size)
    pm = QPixmap(src.size())
    pm.setDevicePixelRatio(src.devicePixelRatio())
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), color)
    p.end()
    return QIcon(pm)


BTN = 40                      # diameter of all round control buttons
from .theme import CUE_COLOR, CUE_TEXT_COLOR   # all cue points are shown in one colour

UNSURE = 0.5            # song sections below this confidence get a "?" and a fainter name
CUE_FLAG_GROW = 2             # px added to the width and height of the cue flags


def parse_sections(value) -> list[dict]:
    """tracks.sections (JSON text) -> list; [] for not analysed / nothing found / unreadable."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        import json
        data = json.loads(value)
        return [d for d in data if {"start", "end", "group", "label"} <= set(d)] if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def fmt_time(ms: int | float | None) -> str:
    if not ms or ms < 0:
        return "0:00"
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# =========================================================================== background loading
def _safe_emit(signal, *args) -> None:
    """Emit from a worker thread; the receiver may already be gone if KeyTool was closed meanwhile."""
    try:
        signal.emit(*args)
    except RuntimeError:
        pass

class _Emitter(QObject):
    done = Signal(str, object, str)   # path, Waveform | None, error


class _WaveformJob(QRunnable):
    def __init__(self, path: str, emitter: _Emitter):
        super().__init__()
        self.path, self.emitter = path, emitter

    def run(self):
        try:
            from ..waveform import get_waveform
            wf = get_waveform(self.path)
        except Exception as e:
            _safe_emit(self.emitter.done, self.path, None, f"{type(e).__name__}: {e}")
            return
        _safe_emit(self.emitter.done, self.path, wf, "")


class _CoverEmitter(QObject):
    done = Signal(str, object)        # path, QImage | None
    markers = Signal(str, object)     # path, SeratoMarkers


class _CoverJob(QRunnable):
    def __init__(self, path: str, emitter: _CoverEmitter):
        super().__init__()
        self.path, self.emitter = path, emitter

    def run(self):
        from PySide6.QtGui import QImage
        img = None
        try:
            from ..tags import read_cover
            data = read_cover(self.path)
            if data:
                q = QImage.fromData(data)
                if not q.isNull():
                    # pre-scale in the worker thread; enough for the player and the enlarged view
                    img = q.scaled(600, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation) if q.width() > 600 else q
        except Exception:
            img = None
        _safe_emit(self.emitter.done, self.path, img)
        try:
            from ..serato import read_serato_markers
            markers = read_serato_markers(self.path)
        except Exception:
            return
        _safe_emit(self.emitter.markers, self.path, markers)


class CoverView(QWidget):
    """Square cover artwork; a vinyl-record placeholder (label in the track's key colour) if there is none.
    Click to see the cover enlarged."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # height follows the player panel (controls + waveform), width is kept equal -> always square
        self.setFixedSize(116, 116)
        self.image = None
        self.key_code: str | None = None
        self.dark = True
        self.loading = False

    def set_image(self, img):
        self.image = img
        self.loading = False
        self.setCursor(Qt.PointingHandCursor if img is not None else Qt.ArrowCursor)
        self.setToolTip("" if img is not None else tr("no_cover"))
        self.update()

    def set_key(self, code: str | None):
        self.key_code = code
        self.update()

    def paintEvent(self, _):
        from PySide6.QtGui import QPainterPath, QRadialGradient
        from .theme import camelot_color

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 6, 6)
        p.setClipPath(clip)
        pal = self.palette()
        if self.image is not None:
            from PySide6.QtGui import QPixmap
            pm = QPixmap.fromImage(self.image).scaled(self.size() * self.devicePixelRatioF(),
                                                      Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            x = (self.width() - pm.width() / pm.devicePixelRatio()) / 2
            y = (self.height() - pm.height() / pm.devicePixelRatio()) / 2
            p.drawPixmap(QPointF(x, y), pm)
        else:
            # placeholder: vinyl record on a soft background
            p.fillRect(r, pal.color(pal.ColorRole.Base))
            c = r.center()
            rad = r.width() * 0.42
            g = QRadialGradient(c, rad)
            g.setColorAt(0.0, QColor("#2a2a2e"))
            g.setColorAt(1.0, QColor("#0d0d10"))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, rad, rad)
            groove = QColor(255, 255, 255, 22)
            p.setPen(QPen(groove, 1))
            p.setBrush(Qt.NoBrush)
            for f in (0.92, 0.82, 0.72, 0.62, 0.52):
                p.drawEllipse(c, rad * f, rad * f)
            label = camelot_color(self.key_code, self.dark) if self.key_code else pal.color(pal.ColorRole.Link)
            p.setPen(Qt.NoPen)
            p.setBrush(label)
            p.drawEllipse(c, rad * 0.36, rad * 0.36)
            p.setBrush(QColor("#0d0d10"))
            p.drawEllipse(c, rad * 0.05, rad * 0.05)
            # light reflection
            shine = QColor(255, 255, 255, 30)
            p.setPen(QPen(shine, 2))
            p.drawArc(QRectF(c.x() - rad * 0.8, c.y() - rad * 0.8, rad * 1.6, rad * 1.6), 100 * 16, 50 * 16)
            if self.loading:
                p.setPen(pal.color(pal.ColorRole.PlaceholderText))
                p.drawText(r.adjusted(0, 0, 0, -4), Qt.AlignHCenter | Qt.AlignBottom, "…")
        p.setClipping(False)
        p.setPen(QPen(pal.color(pal.ColorRole.Mid), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 6, 6)

    def mousePressEvent(self, e):
        if self.image is None or e.button() != Qt.LeftButton:
            return
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self.window())
        dlg.setWindowTitle(tr("cover"))
        lay = QVBoxLayout(dlg)
        lbl = QLabel()
        pm = QPixmap.fromImage(self.image)
        if pm.width() > 600:
            pm = pm.scaled(600, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lbl.setPixmap(pm)
        lay.addWidget(lbl)
        dlg.exec()


class _TransposeEmitter(QObject):
    progress = Signal(str, int, float)        # path, semitones, fraction
    done = Signal(str, int, str, str)         # path, semitones, output file, error


class _TransposeJob(QRunnable):
    def __init__(self, path: str, semitones: int, emitter: _TransposeEmitter):
        super().__init__()
        self.path, self.semitones, self.emitter = path, semitones, emitter

    def run(self):
        try:
            from ..transpose import render
            out = render(self.path, self.semitones,
                         progress=lambda f: _safe_emit(self.emitter.progress, self.path, self.semitones, f))
        except Exception as e:
            _safe_emit(self.emitter.done, self.path, self.semitones, "", f"{type(e).__name__}: {e}")
            return
        _safe_emit(self.emitter.done, self.path, self.semitones, out, "")


# =========================================================================== waveform view
class WaveformView(QWidget):
    seek_requested = Signal(float)    # fraction 0..1
    cue_clicked = Signal(int)         # exact cue position in ms
    section_clicked = Signal(int)     # start of the clicked song section in ms
    sections_edited = Signal(list)    # song sections changed by hand (complete new list)
    cues_edited = Signal(list)        # cue points changed by hand (complete new list of serato.Cue)
    redetect_requested = Signal()     # "detect the sections again" for the loaded track
    STRIP = 18                        # height of the section-name strip below the waveform

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setMouseTracking(True)
        self.peaks: np.ndarray | None = None
        self.colors: np.ndarray | None = None
        self.duration = 0.0
        self.position = 0.0           # fraction 0..1
        self.hover: float | None = None
        self.message = ""
        self.dark = True
        self.bpm: float | None = None
        self._drag = False
        self.cues = []                # serato.Cue
        self.loops = []               # serato.Loop
        self.sections: list[dict] = []   # song sections (structure.py); empty = not analysed / none found
        self._flag_rects: list = []   # (QRectF, cue) for click / hover
        self.editable = False         # a library track is loaded: cues and sections can be edited
        self._bdrag: int | None = None    # section whose start is being dragged
        self._cdrag: list | None = None   # [cue, press x, moved?] while a cue flag is held
        self._edit_before: list = []

    # one muted hue per group (A, B, C ...): same part = same colour; the Camelot colours stay the loud ones
    SECTION_HUES = (205, 30, 150, 275, 95, 340, 180, 55)

    def set_sections(self, sections):
        self.sections = list(sections or [])
        self.update()

    def _section_color(self, group: str, alpha: int, conf: float = 1.0) -> QColor:
        """Muted colour per group; unsure sections (low "conf") are drawn fainter."""
        hue = self.SECTION_HUES[(ord(group[:1] or "A") - 65) % len(self.SECTION_HUES)]
        c = QColor.fromHsl(hue, 80, 105 if self.dark else 185)
        c.setAlpha(int(alpha * (0.45 + 0.55 * min(max(conf, 0.0), 1.0))))
        return c

    def _wave_height(self) -> int:
        """Height of the waveform itself: the strip below is reserved for section names when there are any."""
        return self.height() - self.STRIP if self.sections and self.duration else self.height()

    def _section_x(self, sec) -> tuple[float, float]:
        inner = self.width() - 8
        return (4 + sec["start"] / self.duration * inner, 4 + min(sec["end"], self.duration) / self.duration * inner)

    def section_in_strip(self, pos) -> dict | None:
        """The section whose name strip (below the waveform) is under the mouse."""
        if not (self.sections and self.duration) or pos.y() < self._wave_height():
            return None
        for sec in self.sections:
            x0, x1 = self._section_x(sec)
            if x0 <= pos.x() < x1:
                return sec
        return None

    def set_waveform(self, wf):
        self.peaks, self.colors, self.duration = wf.peaks, wf.colors, wf.duration
        self.message = ""
        self.update()

    def set_markers(self, markers):
        from ..serato import number_by_position
        self.cues = number_by_position(markers.cues) if markers else []      # numbered by position
        self.loops = list(markers.loops) if markers else []
        self.update()

    def clear(self, message: str = ""):
        self.editable = False
        self._bdrag = self._cdrag = None
        self.cues, self.loops = [], []
        self.sections = []
        self.peaks = self.colors = None
        self.duration = 0.0
        self.position = 0.0
        self.message = message
        self.update()

    def set_position(self, frac: float):
        self.position = min(max(frac, 0.0), 1.0)
        self.update()

    # -- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        pal = self.palette()
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(pal.color(pal.ColorRole.Window).darker(108) if self.dark else pal.color(pal.ColorRole.Base))
        p.drawRoundedRect(QRectF(r), 6, 6)
        w, full = self.width(), self.height()
        h = self._wave_height() if self.peaks is not None else full     # waveform area (strip below it)
        mid = h / 2
        if self.peaks is None or not len(self.peaks):
            p.setPen(pal.color(pal.ColorRole.PlaceholderText))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        n = len(self.peaks)
        inner = w - 8
        dur_ms = self.duration * 1000.0
        # saved loops: translucent band behind the waveform
        if dur_ms > 0:
            for lp in self.loops:
                x0 = 4 + lp.start_ms / dur_ms * inner
                x1 = 4 + lp.end_ms / dur_ms * inner
                c = QColor(lp.color)
                band = QColor(c); band.setAlpha(55)
                p.fillRect(QRectF(x0, 1, max(2.0, x1 - x0), h - 2), band)
                c.setAlpha(200)
                p.fillRect(QRectF(x0, h - 6, max(2.0, x1 - x0), 4), c)       # at the waveform's bottom
        # song sections: tinted bands behind the waveform that reach down into the name strip below it
        # (the one playing now a little stronger, the strip stronger still: it is the clickable part)
        now = self.position * self.duration
        if self.sections and self.duration:
            for sec in self.sections:
                x0, x1 = self._section_x(sec)
                conf = float(sec.get("conf", 1.0))
                playing = sec["start"] <= now < sec["end"]
                a = (95 if playing else 60) if self.dark else (120 if playing else 80)
                p.fillRect(QRectF(x0, 1, max(1.0, x1 - x0), h - 1), self._section_color(sec["group"], a, conf))
                strip_a = (150 if playing else 105) if self.dark else (175 if playing else 130)
                p.fillRect(QRectF(x0, h, max(1.0, x1 - x0), full - h - 1),
                           self._section_color(sec["group"], strip_a, conf))
                if sec["start"] > 0:
                    edge = QColor(pal.color(pal.ColorRole.Text)); edge.setAlpha(45)
                    p.setPen(edge)
                    p.drawLine(QPointF(x0, 2), QPointF(x0, full - 2))
                    p.setPen(Qt.NoPen)
        # resample bins to pixel columns (max of peaks, colour of the loudest bin)
        cols = max(1, int(inner))
        edges = np.linspace(0, n, cols + 1).astype(int)
        played_x = 4 + self.position * inner
        amp = (h - 14) / 2
        for x in range(cols):
            a, b = edges[x], max(edges[x + 1], edges[x] + 1)
            seg = self.peaks[a:b]
            i = a + int(np.argmax(seg))
            level = float(seg.max())
            c = QColor(*self.colors[i])
            if not self.dark:
                c = c.darker(150)
            px = 4 + x
            if px > played_x:
                c.setAlpha(105 if self.dark else 150)
            p.setPen(c)
            hh = max(1.0, level * amp)
            p.drawLine(QPointF(px, mid - hh), QPointF(px, mid + hh))
        # section names in the strip below the waveform ("?" = unsure)
        if self.sections and self.duration:
            from PySide6.QtGui import QFont
            f = QFont(self.font()); f.setPointSizeF(max(7.0, f.pointSizeF() * 0.8)); f.setWeight(QFont.DemiBold)
            p.setFont(f)
            fm = p.fontMetrics()
            for sec in self.sections:
                x0, x1 = self._section_x(sec)
                if x1 - x0 < 24:
                    continue
                playing = sec["start"] <= now < sec["end"]
                unsure = float(sec.get("conf", 1.0)) < UNSURE
                col = QColor(pal.color(pal.ColorRole.Text))
                col.setAlpha((235 if playing else 175) - (60 if unsure else 0))
                p.setPen(col)
                label = se.display_name(sec, tr) + ("?" if unsure else "")
                name = fm.elidedText(label, Qt.ElideRight, int(x1 - x0 - 8))
                p.drawText(QRectF(x0 + 4, h, x1 - x0 - 6, full - h), Qt.AlignLeft | Qt.AlignVCenter, name)
            p.setFont(self.font())
        # beat ticks (from BPM, without phase) every bar as a subtle orientation aid
        if self.bpm and self.duration:
            bar = 4 * 60.0 / self.bpm
            if bar * inner / self.duration >= 6:
                tick = QColor(pal.color(pal.ColorRole.Text)); tick.setAlpha(28)
                p.setPen(tick)
                t = 0.0
                while t < self.duration:
                    x = 4 + t / self.duration * inner
                    p.drawLine(QPointF(x, 3), QPointF(x, 8))
                    t += bar
        # Serato hot cues: coloured line + flag with the slot number (or name)
        self._flag_rects = []
        if dur_ms > 0 and self.cues:
            from PySide6.QtGui import QFont, QFontMetrics
            f = QFont(self.font()); f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85)); f.setBold(True)
            p.setFont(f)
            fm = QFontMetrics(f)
            for cue in self.cues:
                x = 4 + cue.position_ms / dur_ms * inner
                col = QColor(CUE_COLOR)
                line = QColor(col); line.setAlpha(230)
                p.setPen(QPen(line, 2))
                p.drawLine(QPointF(x, 2), QPointF(x, h - 2))
                label = str(cue.index + 1)
                fw = max(16, fm.horizontalAdvance(label) + 8) + CUE_FLAG_GROW
                rect = QRectF(x - 1, 2, fw, fm.height() + 2 + CUE_FLAG_GROW)
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.setRenderHint(QPainter.Antialiasing, True)
                p.drawRoundedRect(rect, 3, 3)
                p.setRenderHint(QPainter.Antialiasing, False)
                p.setPen(QColor(CUE_TEXT_COLOR))
                p.drawText(rect, Qt.AlignCenter, label)
                self._flag_rects.append((rect, cue))
            p.setFont(self.font())
        # hover + playhead
        if self.hover is not None:
            hc = QColor(pal.color(pal.ColorRole.Text)); hc.setAlpha(90)
            p.setPen(QPen(hc, 1))
            hx = 4 + self.hover * inner
            p.drawLine(QPointF(hx, 2), QPointF(hx, full - 2))
            p.drawText(QRectF(hx + 4, 2, 70, 16), Qt.AlignLeft, fmt_time(self.hover * self.duration * 1000))
        p.setPen(QPen(pal.color(pal.ColorRole.Link), 2))     # amber playhead
        p.drawLine(QPointF(played_x, 1), QPointF(played_x, full - 1))

    # -- interaction
    def _frac(self, x: float) -> float:
        return min(max((x - 4) / max(1, self.width() - 8), 0.0), 1.0)

    def _x_of(self, seconds: float) -> float:
        return 4 + seconds / self.duration * (self.width() - 8) if self.duration else 4.0

    def _cue_at(self, pos):
        """Cue under the mouse: its flag, or anywhere within 5 px of its line (nearest one wins)."""
        for rect, cue in self._flag_rects:
            if rect.adjusted(-2, -2, 2, 2).contains(pos):
                return cue
        best, dist = None, 6.0
        for rect, cue in self._flag_rects:
            d = abs(pos.x() - rect.left())
            if d < dist:
                best, dist = cue, d
        return best

    def _boundary_at(self, pos) -> int | None:
        """Index of the section whose start line (not the first) is within 4 px, in the name strip."""
        if not (self.editable and self.sections and self.duration) or pos.y() < self._wave_height():
            return None
        for i, sec in enumerate(self.sections[1:], 1):
            if abs(pos.x() - self._x_of(sec["start"])) <= 4:
                return i
        return None

    def _grid_anchor(self) -> float:
        """A time that lies on a bar line: a detected section boundary, else the first cue, else 0."""
        starts = [s["start"] for s in self.sections if s["start"] > 0]
        if starts:
            return starts[0]
        return self.cues[0].position_ms / 1000.0 if self.cues else 0.0

    def snap(self, seconds: float, beats: int, free: bool = False) -> float:
        """Round to the beat grid (beats=4: bars) from the tempo; free (Alt held) or no tempo: unchanged."""
        if free or not self.bpm or not self.duration:
            return min(max(seconds, 0.0), self.duration)
        step = beats * 60.0 / float(self.bpm)
        a = self._grid_anchor()
        t = a + round((seconds - a) / step) * step
        if t > self.duration:
            t -= step
        if t < 0:
            t += step
        return min(max(t, 0.0), self.duration)

    @staticmethod
    def _free(e) -> bool:
        return bool(e.modifiers() & Qt.AltModifier) if hasattr(e, "modifiers") else False

    # editing: every change goes out as the complete new list (sections_edited / cues_edited)
    def edit_sections(self, sections: list[dict]):
        self.sections = list(sections)
        self.update()
        self.sections_edited.emit(list(sections))

    def edit_cues(self, cues: list):
        from ..serato import number_by_position
        self.cues = number_by_position(cues)          # numbers follow the position in the track
        self.update()
        self.cues_edited.emit(list(self.cues))

    def add_cue_at(self, seconds: float):
        from ..serato import FREE_COLORS, Cue, next_free_slot
        slot = next_free_slot(self.cues)
        if slot is not None:
            self.edit_cues(self.cues + [Cue(slot, int(round(seconds * 1000)), FREE_COLORS[slot])])

    def move_cue(self, index: int, ms: int):
        from dataclasses import replace
        self.edit_cues([replace(c, position_ms=int(ms)) if c.index == index else c for c in self.cues])

    def delete_cue(self, index: int):
        self.edit_cues([c for c in self.cues if c.index != index])

    def _ask_section_name(self, i: int):
        from PySide6.QtWidgets import QInputDialog
        from ..structure import LABELS
        names = [tr(f"sec_{l}") for l in LABELS]
        cur = se.display_name(self.sections[i], tr)
        text, ok = QInputDialog.getItem(self, tr("sec_rename"), tr("sec_name_prompt"), names,
                                        names.index(cur) if cur in names else 0, True)
        if ok and text.strip():
            text = text.strip()
            if text in names:
                self.edit_sections(se.rename(self.sections, i, label=LABELS[names.index(text)]))
            else:
                self.edit_sections(se.rename(self.sections, i, name=text))

    def _confirm(self, text: str) -> bool:
        from PySide6.QtWidgets import QMessageBox
        return QMessageBox.question(self, tr("app_title"), text) == QMessageBox.Yes

    def build_menu(self, pos, free: bool = False):
        """Right-click menu for the position under the mouse (cue points and song sections)."""
        from PySide6.QtWidgets import QMenu
        from ..serato import next_free_slot
        from ..structure import LABELS
        m = QMenu(self)
        t = self._frac(pos.x()) * self.duration
        cue = self._cue_at(pos)
        if cue is not None:
            n = cue.index + 1
            m.addAction(tr("cue_delete_n", n=n), lambda: self.delete_cue(cue.index))
        else:
            full = next_free_slot(self.cues) is None
            a = m.addAction(tr("cue_add_full") if full else tr("cue_add_here"),
                            lambda: self.add_cue_at(self.snap(t, 1, free)))
            a.setEnabled(not full)                              # Serato has 8 hot cue slots
        if self.cues:
            m.addAction(tr("cue_delete_all"),
                        lambda: self._confirm(tr("cue_delete_all_ask")) and self.edit_cues([]))
        m.addSeparator()
        i = se.index_at(self.sections, t) if self.sections else None
        if i is not None:
            sec = self.sections[i]
            rn = m.addMenu(tr("sec_rename_as", name=se.display_name(sec, tr)))
            for lab in LABELS:
                act = rn.addAction(tr(f"sec_{lab}"), lambda lab=lab, i=i: self.edit_sections(se.rename(self.sections, i, label=lab)))
                act.setCheckable(True)
                act.setChecked(sec["label"] == lab and not sec.get("name"))
            rn.addSeparator()
            rn.addAction(tr("sec_custom_name"), lambda i=i: self._ask_section_name(i))
            bt = self.snap(t, 4, free)
            a = m.addAction(tr("sec_split_here"), lambda: self.edit_sections(se.split(self.sections, bt)))
            a.setEnabled(se.split(self.sections, bt) != self.sections)
            m.addAction(tr("sec_delete_one", name=se.display_name(sec, tr)),
                        lambda i=i: self.edit_sections(se.delete(self.sections, i)))
            m.addAction(tr("sec_delete_all"),
                        lambda: self._confirm(tr("sec_delete_all_ask")) and self.edit_sections([]))
        else:
            bt = self.snap(t, 4, free)
            m.addAction(tr("sec_create"), lambda: self.edit_sections(
                se.split([], bt, self.duration) if 1.0 <= bt <= self.duration - 1.0 else se.create(self.duration)))
        m.addAction(tr("sec_redetect"), self.redetect_requested.emit)
        return m

    def contextMenuEvent(self, e):
        if not (self.editable and self.peaks is not None and self.duration):
            return
        from PySide6.QtGui import QGuiApplication
        free = bool(QGuiApplication.keyboardModifiers() & Qt.AltModifier)
        self.build_menu(QPointF(e.pos()), free).exec(e.globalPos())

    def mouseDoubleClickEvent(self, e):
        if not (self.editable and self.peaks is not None) or e.button() != Qt.LeftButton:
            return
        sec = self.section_in_strip(e.position())
        if sec is not None:
            self._ask_section_name(self.sections.index(sec))

    def mousePressEvent(self, e):
        if self.peaks is None or e.button() != Qt.LeftButton:
            return
        b = self._boundary_at(e.position())
        if b is not None:                                       # drag a section boundary
            self._bdrag = b
            self._edit_before = self.sections
            return
        sec = self.section_in_strip(e.position())
        if sec is not None:                                     # name strip: jump to the section's start
            self.set_position(sec["start"] / self.duration)
            self.section_clicked.emit(int(round(sec["start"] * 1000)))
            return
        cue = self._cue_at(e.position())
        if cue is not None:                                     # cue: jump on release, drag to move
            self._cdrag = [cue, e.position().x(), False]
            return
        self._drag = True
        f = self._frac(e.position().x())
        self.set_position(f)
        self.seek_requested.emit(f)

    def mouseMoveEvent(self, e):
        if self.peaks is None:
            return
        pos = e.position()
        self.hover = self._frac(pos.x())
        free = self._free(e)
        if self._bdrag is not None:
            self.sections = se.move_boundary(self._edit_before, self._bdrag,
                                             self.snap(self.hover * self.duration, 4, free))
            self.update()
            return
        if self._cdrag is not None:
            cue, x0, moved = self._cdrag
            if self.editable and (moved or abs(pos.x() - x0) > 3):
                from dataclasses import replace
                ms = int(round(self.snap(self.hover * self.duration, 1, free) * 1000))
                self._cdrag[2] = True
                self.cues = [replace(c, position_ms=ms) if c.index == cue.index else c for c in self.cues]
                self.update()
            return
        b = None if self._drag else self._boundary_at(pos)
        sec = None if (self._drag or b is not None) else self.section_in_strip(pos)
        cue = None if (sec is not None or b is not None) else self._cue_at(pos)
        if b is not None:
            self.setToolTip(tr("sec_drag_tip"))
            self.setCursor(Qt.SizeHorCursor)
        elif sec is not None:
            self.setToolTip(tr("sec_tip").format(name=se.display_name(sec, tr), group=sec["group"],
                                                 start=fmt_time(sec["start"] * 1000), end=fmt_time(sec["end"] * 1000),
                                                 conf=int(round(100 * float(sec.get("conf", 1.0))))))
            self.setCursor(Qt.PointingHandCursor)
        elif cue is not None:
            name = f" – {cue.name}" if cue.name else ""
            self.setToolTip(f"Cue {cue.index + 1}{name} · {fmt_time(cue.position_ms)}"
                            + ("\n" + tr("cue_drag_tip") if self.editable else ""))
            self.setCursor(Qt.PointingHandCursor)
        else:
            loop = next((l for l in self.loops if self.duration and
                         l.start_ms <= self.hover * self.duration * 1000 <= l.end_ms), None)
            self.setToolTip(f"Loop {loop.index + 1}{' – ' + loop.name if loop.name else ''} · "
                            f"{fmt_time(loop.start_ms)}–{fmt_time(loop.end_ms)}{' 🔒' if loop.locked else ''}"
                            if loop else "")
            self.unsetCursor()
        if self._drag:
            self.set_position(self.hover)
            self.seek_requested.emit(self.hover)
        self.update()

    def mouseReleaseEvent(self, _):
        self._drag = False
        if self._bdrag is not None:
            before, self._bdrag = self._edit_before, None
            if self.sections != before:
                self.edit_sections(self.sections)
            return
        if self._cdrag is not None:
            cue, _, moved = self._cdrag
            self._cdrag = None
            if moved:
                self.edit_cues(self.cues)
            elif self.duration:
                self.set_position(cue.position_ms / (self.duration * 1000.0))
                self.cue_clicked.emit(cue.position_ms)

    def leaveEvent(self, _):
        self.hover = None
        self.update()


class CircleButton(QPushButton):
    """Round media button: outline ring with a painted glyph (play, pause, stop, prev, next, vol, mute).
    The primary button (play) is filled with the accent colour. Glyphs are drawn, not icon files,
    so they stay sharp at any DPI and follow light/dark mode."""

    def __init__(self, glyph: str, diameter: int = 40, primary: bool = False, parent=None):
        super().__init__(parent)
        self.glyph = glyph
        self.primary = primary
        self.setObjectName("circle")          # exempt from the panel's QPushButton min-height/padding
        self.diameter = diameter
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(diameter, diameter)

    # style sheets may reset min/max sizes on polish; the size hint keeps the button a true circle
    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(self.diameter, self.diameter)

    def minimumSizeHint(self):
        return self.sizeHint()

    def event(self, e):
        from PySide6.QtCore import QEvent
        r = super().event(e)
        if e.type() == QEvent.Polish:
            self.setFixedSize(self.diameter, self.diameter)
        return r
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover, True)

    def set_glyph(self, glyph: str):
        if glyph != self.glyph:
            self.glyph = glyph
            self.update()

    def paintEvent(self, _):
        from PySide6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        ink = pal.color(pal.ColorRole.ButtonText)
        accent = pal.color(pal.ColorRole.Link)
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        down = enabled and self.isDown()
        d = min(self.width(), self.height())
        ring = 2.0
        ox, oy = (self.width() - d) / 2, (self.height() - d) / 2
        r = QRectF(ox + ring / 2 + 0.5, oy + ring / 2 + 0.5, d - ring - 1, d - ring - 1)
        if self.primary and enabled:
            fill = accent.lighter(112) if hover else accent
            if down:
                fill = accent.darker(112)
            p.setPen(Qt.NoPen); p.setBrush(fill)
            p.drawEllipse(r)
            fg = QColor("#1A1206") if accent.lightness() > 140 else QColor("#FFFFFF")
        else:
            fg = QColor(ink)
            if not enabled:
                fg.setAlpha(90)
            bg = Qt.NoBrush
            if down:
                bg = QColor(ink); bg.setAlpha(46)
            elif hover:
                bg = QColor(ink); bg.setAlpha(22)
            p.setPen(QPen(fg, ring)); p.setBrush(bg)
            p.drawEllipse(r)
        if self.hasFocus():
            p.setPen(QPen(accent, 2)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(-1, -1, 1, 1))
        # glyph, scaled to the diameter
        c = r.center()
        u = d / 40.0                     # 1 unit at a 40 px button
        p.setPen(Qt.NoPen); p.setBrush(fg)
        g = self.glyph

        def tri(x0, y0, x1, y1, x2, y2):
            path = QPainterPath(QPointF(x0, y0)); path.lineTo(x1, y1); path.lineTo(x2, y2); path.closeSubpath()
            p.drawPath(path)

        cx, cy = c.x(), c.y()
        if g == "play":
            tri(cx - 4.5 * u, cy - 7 * u, cx - 4.5 * u, cy + 7 * u, cx + 7.5 * u, cy)
        elif g == "pause":
            p.drawRoundedRect(QRectF(cx - 6 * u, cy - 7 * u, 4.2 * u, 14 * u), 1, 1)
            p.drawRoundedRect(QRectF(cx + 1.8 * u, cy - 7 * u, 4.2 * u, 14 * u), 1, 1)
        elif g == "stop":
            p.drawRoundedRect(QRectF(cx - 6 * u, cy - 6 * u, 12 * u, 12 * u), 1.5, 1.5)
        elif g == "prev":
            p.drawRect(QRectF(cx - 7 * u, cy - 6.5 * u, 2.6 * u, 13 * u))
            tri(cx + 6.5 * u, cy - 6.5 * u, cx + 6.5 * u, cy + 6.5 * u, cx - 4 * u, cy)
        elif g == "next":
            p.drawRect(QRectF(cx + 4.4 * u, cy - 6.5 * u, 2.6 * u, 13 * u))
            tri(cx - 6.5 * u, cy - 6.5 * u, cx - 6.5 * u, cy + 6.5 * u, cx + 4 * u, cy)
        elif g in ("cue_prev", "cue_next"):
            # measured from the user's reference (54 px circle): triangle 18 px high, tip 13 px from the centre,
            # base 2 px past it, bar 4.5 px wide after a 3.5 px gap; the glyph sits slightly towards the tip
            m = -1 if g == "cue_prev" else 1                  # direction the triangle points
            tip, base, bar_in, bar_out, half = 9.63 * u, 1.48 * u, 4.0 * u, 7.41 * u, 6.67 * u
            tri(cx + m * tip, cy, cx - m * base, cy - half, cx - m * base, cy + half)
            x0, x1 = sorted((cx - m * bar_in, cx - m * bar_out))
            p.drawRect(QRectF(x0, cy - half, x1 - x0, 2 * half))
        elif g in ("vol", "mute"):
            body = QPainterPath(QPointF(cx - 8 * u, cy - 3 * u))
            body.lineTo(cx - 4.5 * u, cy - 3 * u); body.lineTo(cx + 0.5 * u, cy - 7.5 * u)
            body.lineTo(cx + 0.5 * u, cy + 7.5 * u); body.lineTo(cx - 4.5 * u, cy + 3 * u)
            body.lineTo(cx - 8 * u, cy + 3 * u); body.closeSubpath()
            p.drawPath(body)
            pen = QPen(fg, 1.8 * u); pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            if g == "vol":
                p.drawArc(QRectF(cx - 2 * u, cy - 4 * u, 8 * u, 8 * u), -55 * 16, 110 * 16)
                p.drawArc(QRectF(cx - 3.5 * u, cy - 7.5 * u, 15 * u, 15 * u), -55 * 16, 110 * 16)
            else:
                p.drawLine(QPointF(cx + 3.5 * u, cy - 3.5 * u), QPointF(cx + 8.5 * u, cy + 3.5 * u))
                p.drawLine(QPointF(cx + 8.5 * u, cy - 3.5 * u), QPointF(cx + 3.5 * u, cy + 3.5 * u))


class KeyChip(QWidget):
    """Large key label on its Camelot colour (the deck's memorable element)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from .theme import styled_font
        self.code: str | None = None
        self.text = "–"
        self.dark = True
        self.big = styled_font(self.font(), size=27, weight=800, width=125)   # painted, not styled by the QSS
        self.setFixedHeight(56)
        self.fit_to(["12A"])

    def fit_to(self, texts):
        """One width for every key: as wide as the widest label of the notation in use."""
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.big)
        self.setFixedWidth(max(70, max(fm.horizontalAdvance(t) for t in list(texts) + ["–"]) + 30))

    def set_key(self, code: str | None, text: str):
        self.code, self.text = code, text or "–"
        self.update()

    def paintEvent(self, _):
        from .theme import CHIP_TEXT, camelot_color
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = camelot_color(self.code, self.dark) if self.code else None
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if col is not None:
            p.setPen(Qt.NoPen); p.setBrush(col)
        else:
            p.setPen(QPen(self.palette().color(self.palette().ColorRole.Mid), 1)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 10, 10)
        p.setFont(self.big)
        p.setPen(CHIP_TEXT if col is not None else self.palette().color(self.palette().ColorRole.PlaceholderText))
        p.drawText(r, Qt.AlignCenter, self.text)


# =========================================================================== player panel
class PlayerPanel(QWidget, Translatable):
    """Cover + transport controls + waveform. Plays through the system's default audio output."""
    prev_requested = Signal(bool)     # keep playing?
    next_requested = Signal(bool)
    deck_changed = Signal()           # loaded track, its key/bpm or the transposition changed
    cues_read = Signal(str, int)      # path, number of cue points found in the loaded file
    sections_edited = Signal(int, list)   # track id, song sections changed by hand in the waveform
    cues_edited = Signal(int, list)       # track id, cue points changed by hand (to be written into the file)
    redetect_sections = Signal(int)       # track id: detect the song sections again

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._tr_init()
        self.s = settings
        self.row: dict | None = None
        self.path: str | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.emitter = _Emitter()
        self.emitter.done.connect(self._waveform_ready)
        self.cover_emitter = _CoverEmitter()
        self.cover_emitter.done.connect(self._cover_ready)
        self.cover_emitter.markers.connect(self._markers_ready)
        self.markers = None
        self.notation = "camelot"

        # the deck: the loaded track is the reference the library list is compared to
        self.setObjectName("playerPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        from PySide6.QtWidgets import QButtonGroup, QGridLayout, QSizePolicy
        from .. import transpose as _tp
        from .theme import styled_font
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(18)
        root.addLayout(top)
        self.cover = CoverView()
        top.addWidget(self.cover, 0, Qt.AlignTop)

        # title block + transport
        info = QVBoxLayout(); info.setSpacing(2); info.setContentsMargins(0, 0, 0, 0)
        self.caption = QLabel(); self.caption.setObjectName("deckCaption")
        self.title = QLabel(); self.title.setObjectName("deckTitle")
        self.title.setFont(styled_font(self.font(), size=21, weight=800, width=125))
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.artist_lbl = QLabel(); self.artist_lbl.setObjectName("deckArtist")
        self.artist_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        info.addWidget(self.caption); info.addWidget(self.title); info.addWidget(self.artist_lbl)
        info.addSpacing(8)
        bar = QHBoxLayout(); bar.setSpacing(6)
        self.play_btn = CircleButton("play", BTN, primary=True)
        self.play_btn.clicked.connect(self.toggle)
        self.prev_btn = CircleButton("prev", BTN)
        self.prev_btn.clicked.connect(self.previous)
        self.next_btn = CircleButton("next", BTN)
        self.next_btn.clicked.connect(lambda: self.next_requested.emit(self.is_playing()))
        self.prev_cue_btn = CircleButton("cue_prev", BTN)
        self.prev_cue_btn.clicked.connect(self.previous_cue)
        self.next_cue_btn = CircleButton("cue_next", BTN)
        self.next_cue_btn.clicked.connect(self.next_cue)
        self.stop_btn = CircleButton("stop", BTN)
        self.stop_btn.clicked.connect(self.stop)
        self.time = QLabel("0:00"); self.time.setObjectName("deckTime")
        self.time.setFont(styled_font(self.font(), size=10.5))
        self.mute_btn = CircleButton("vol", BTN)
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self._mute)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(100)
        self.volume.setValue(int(round(getattr(settings, "volume", 0.8) * 100)))
        self.volume.valueChanged.connect(self._volume)
        bar.setSpacing(10)
        for wdg in (self.prev_btn, self.prev_cue_btn, self.play_btn, self.next_cue_btn, self.next_btn, self.stop_btn):
            bar.addWidget(wdg, 0, Qt.AlignVCenter)
        bar.addSpacing(8); bar.addWidget(self.time, 0, Qt.AlignVCenter); bar.addStretch(1)
        bar.addWidget(self.mute_btn, 0, Qt.AlignVCenter); bar.addWidget(self.volume, 0, Qt.AlignVCenter)
        info.addLayout(bar)
        info.addStretch(1)
        top.addLayout(info, 1)

        # key block: big key chip + transposition with target keys
        self.semitones = 0
        self.source_file: str | None = None       # file currently loaded in the player (original or transposed)
        self._rendering: set[tuple[str, int]] = set()
        self._want_play = False
        self._tp_available = _tp.available()
        self.tp_emitter = _TransposeEmitter()
        self.tp_emitter.progress.connect(self._transpose_progress)
        self.tp_emitter.done.connect(self._transpose_done)
        keyb = QGridLayout(); keyb.setHorizontalSpacing(4); keyb.setVerticalSpacing(6)
        self.key_caption = QLabel(); self.key_caption.setObjectName("deckCaption")
        self.key_chip = KeyChip()
        self.key_name = QLabel(); self.key_name.setObjectName("deckArtist")
        chip_row = QHBoxLayout(); chip_row.setSpacing(10)
        chip_row.addWidget(self.key_chip); chip_row.addWidget(self.key_name, 0, Qt.AlignBottom); chip_row.addStretch(1)
        keyb.addWidget(self.key_caption, 0, 0, 1, 3)
        keyb.addLayout(chip_row, 1, 0, 1, 3)
        self.tp_group = QButtonGroup(self)
        self.tp_group.setExclusive(True)
        self.tp_buttons: dict[int, QPushButton] = {}
        for i, st in enumerate((-1, 0, 1)):
            b = QPushButton()
            b.setCheckable(True)
            b.setChecked(st == 0)
            b.setObjectName("transpose")
            b.clicked.connect(lambda _=False, st=st: self.set_semitones(st))
            self.tp_group.addButton(b)
            self.tp_buttons[st] = b
            keyb.addWidget(b, 2, i)
        self._tp_label = self.key_caption
        top.addLayout(keyb, 0)
        top.addSpacing(10)

        # tempo block
        tempo = QVBoxLayout(); tempo.setSpacing(2)
        self.tempo_caption = QLabel(); self.tempo_caption.setObjectName("deckCaption")
        self.bpm_lbl = QLabel("–"); self.bpm_lbl.setObjectName("deckBpm")
        self.bpm_lbl.setFont(styled_font(self.font(), size=38, weight=300, width=125))
        self.bpm_sub = QLabel(); self.bpm_sub.setObjectName("deckSmall")
        tempo.addWidget(self.tempo_caption); tempo.addWidget(self.bpm_lbl); tempo.addWidget(self.bpm_sub)
        tempo.addStretch(1)
        # fixed width, right-aligned: a two-digit tempo must not move the key block (see _fix_widths)
        for lbl in (self.tempo_caption, self.bpm_lbl, self.bpm_sub):
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addLayout(tempo, 0)

        self.wave = WaveformView()
        self.wave.setFixedHeight(84 + WaveformView.STRIP)    # one height: the deck does not jump with sections
        self.wave.seek_requested.connect(self.seek_fraction)
        self.wave.cue_clicked.connect(self.seek_ms)
        self.wave.section_clicked.connect(self.seek_ms)
        self.wave.sections_edited.connect(self._sections_edited)
        self.wave.cues_edited.connect(self._cues_edited)
        self.wave.redetect_requested.connect(lambda: self.row and self.redetect_sections.emit(self.row["id"]))
        root.addWidget(self.wave)

        self.player = None
        self._pending: tuple[int, bool] | None = None   # (position ms, play) applied once media is loaded
        if HAVE_MULTIMEDIA:
            self.devices = QMediaDevices(self)
            self.audio = QAudioOutput(QMediaDevices.defaultAudioOutput(), self)
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(self.audio)
            # always follow the system default output device (e.g. headphones plugged in)
            self.devices.audioOutputsChanged.connect(
                lambda: self.audio.setDevice(QMediaDevices.defaultAudioOutput()))
            self.player.playbackStateChanged.connect(self._state_changed)
            self.player.durationChanged.connect(lambda _: self._tick())
            self.player.errorOccurred.connect(self._error)
            self.player.mediaStatusChanged.connect(self._media_status)
            self._volume(self.volume.value())
        self.timer = QTimer(self, interval=40, timeout=self._tick)
        self._make_icons()
        self.retranslate()
        self.set_track(None)

    # -- appearance
    def retranslate(self):
        super().retranslate()
        self.play_btn.setToolTip(tr("play_pause") + " (Space)")
        self.stop_btn.setToolTip(tr("stop"))
        for st, b in self.tp_buttons.items():
            b.setToolTip(tr("transpose_tip", st=f"{st:+d}" if st else "0") if self._tp_available
                         else tr("transpose_unavailable"))
            b.setEnabled(self._tp_available or st == 0)
        self.key_caption.setText(tr("key"))
        self.key_caption.setToolTip(tr("transpose"))
        self.tempo_caption.setText(tr("tempo_short"))
        self._fix_widths()
        self.caption.setText(tr("deck_caption"))
        self.prev_btn.setToolTip(tr("prev_track") + " (Ctrl+←)")
        self.next_btn.setToolTip(tr("next_track") + " (Ctrl+→)")
        self.prev_cue_btn.setToolTip(tr("prev_cue"))
        self.next_cue_btn.setToolTip(tr("next_cue"))
        self.volume.setToolTip(tr("volume"))
        self._update_mute_icon()
        if self.row is None:
            self.wave.clear(tr("no_selection"))
        elif self.wave.peaks is None and not self.wave.message.startswith("⚠"):
            self.wave.message = tr("waveform_loading")
        self._update_title()

    def _make_icons(self):
        # round buttons paint their own glyphs in the palette colours
        self.ic_vol = True
        self.play_btn.set_glyph("pause" if self.is_playing() else "play")
        for b in (self.play_btn, self.prev_btn, self.next_btn, self.stop_btn, self.mute_btn,
                  self.prev_cue_btn, self.next_cue_btn):
            b.update()
        self._update_mute_icon()

    # -- cover flush with the bottom of the control row
    def _fit_cover(self):
        """Square cover whose bottom edge lines up with the bottom of the round control buttons."""
        top = self.cover.mapTo(self, self.cover.rect().topLeft()).y()
        bottom = self.play_btn.mapTo(self, self.play_btn.rect().bottomLeft()).y()
        size = max(60, bottom - top + 1)
        if size != self.cover.height():
            self.cover.setFixedSize(size, size)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._fit_cover)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._fit_cover)
        QTimer.singleShot(0, self._refit)

    def _refit(self):
        self._fix_widths()
        self._update_title()

    def changeEvent(self, e):
        super().changeEvent(e)
        from PySide6.QtCore import QEvent
        if e.type() in (QEvent.FontChange, QEvent.StyleChange):
            QTimer.singleShot(0, self._fit_cover)
            QTimer.singleShot(0, self._refit)

    KEY_NAME_MAX = 170          # px; longer key names are shortened with "…" (full name in the tooltip)

    def _fix_widths(self):
        """Give the tempo column and the key name a fixed width for the current language and fonts, so the
        deck does not shift when the tempo has two instead of three digits or the key name changes length."""
        for lbl in (self.bpm_lbl, self.bpm_sub, self.tempo_caption, self.key_name):
            lbl.ensurePolished()
        num = self.bpm_lbl.fontMetrics()
        small = self.bpm_sub.fontMetrics()
        w = max(num.horizontalAdvance(fmt_number(888.8)), num.horizontalAdvance("–"),
                small.horizontalAdvance(tr("bpm_energy", e=10)),
                self.tempo_caption.fontMetrics().horizontalAdvance(self.tempo_caption.text())) + 4
        for lbl in (self.tempo_caption, self.bpm_lbl, self.bpm_sub):
            lbl.setFixedWidth(w)
        fm = self.key_name.fontMetrics()
        longest = max(fm.horizontalAdvance(f"{Key(pc, minor).long_name(tr.lang)}, +1")
                      for pc in range(12) for minor in (False, True))
        self.key_name.setFixedWidth(min(self.KEY_NAME_MAX, longest + 2))
        self._fit_chip()

    def _set_key_name(self, text: str):
        fm = self.key_name.fontMetrics()
        self.key_name.setText(fm.elidedText(text, Qt.ElideRight, self.key_name.width()))
        self.key_name.setToolTip(text if self.key_name.text() != text else "")

    def set_dark(self, dark: bool):
        self.wave.dark = dark
        self.cover.dark = dark
        self.key_chip.dark = dark
        self.key_chip.update()
        self.cover.update()
        self._make_icons()
        self.wave.update()

    def set_notation(self, notation: str):
        self.notation = notation
        self._fit_chip()
        self._update_title()

    def _fit_chip(self):
        from ..keys import all_keys
        self.key_chip.fit_to([k.format(self.notation) for k in all_keys()])

    def _update_title(self):
        r = self.row
        st = getattr(self, "semitones", 0)
        k = Key.from_code(r.get("key")) if r else None
        for s_, b in self.tp_buttons.items():
            # original key without a sign ("8A"), the neighbours with it ("−1  1A", "+1  3A")
            sign = "" if s_ == 0 else ("+1" if s_ > 0 else "−1")
            name = k.transpose(s_).format(self.notation) if k else ""
            b.setText(f"{sign}  {name}".strip() if (sign or name) else "–")
        if not r:
            self.title.setText(tr("no_selection"))
            self.artist_lbl.setText("")
            self.key_chip.set_key(None, "")
            self.key_name.setText("")
            self.bpm_lbl.setText("–")
            self.bpm_sub.setText("")
            return
        self.title.setText(r.get("title") or r.get("filename") or "")
        bits = [x for x in (r.get("artist"), r.get("album"), str(r["year"]) if r.get("year") else "") if x]
        mk = getattr(self, "markers", None)
        if mk and mk.cues:
            bits.append(f"{len(mk.cues)} Cues" + (f", {len(mk.loops)} Loops" if mk.loops else ""))
        self.artist_lbl.setText(", ".join(bits))
        self.title.setToolTip(self.title.text())
        self.artist_lbl.setToolTip(self.artist_lbl.text())
        playing_key = k.transpose(st) if (k and st) else k
        if playing_key:
            self.key_chip.set_key(playing_key.code, playing_key.format(self.notation))
            name = playing_key.long_name(tr.lang)
            self._set_key_name(name if not st else f"{name}, {st:+d}")
        else:
            self.key_chip.set_key(None, "–")
            self.key_name.setText("")
        bpm = r.get("bpm")
        self.bpm_lbl.setText(fmt_number(bpm) if bpm else "–")
        e = r.get("energy")
        self.bpm_sub.setText(tr("bpm_energy", e=e) if e else "BPM")

    # -- track handling
    def set_track(self, row: dict | None, autoplay: bool = False):
        """Load the selected track (paused unless autoplay). Called on every selection change."""
        same = row is not None and self.row is not None and row.get("path") == self.row.get("path")
        self.row = row
        self._update_title()
        self.deck_changed.emit()
        self.cover.set_key(row.get("key") if row else None)
        if same:
            self.wave.bpm = row.get("bpm")
            self.wave.set_sections(parse_sections(row.get("sections")))   # e.g. after a re-analysis
            if autoplay:
                self.play()
            return
        enabled = row is not None and os.path.exists(row.get("path", ""))
        for wdg in (self.play_btn, self.stop_btn, self.prev_btn, self.next_btn):
            wdg.setEnabled(enabled and self.player is not None)
        self.cover.set_image(None)
        self.markers = None
        self._update_cue_buttons()
        if enabled:
            self.cover.loading = True
            self.pool.start(_CoverJob(row["path"], self.cover_emitter))
        if self.player is not None:
            self._pending = None
            self.player.stop()
            self.player.setSource(QUrl())
        self.path = row["path"] if row else None
        self.wave.bpm = row.get("bpm") if row else None
        if not row:
            self.wave.clear(tr("no_selection"))
            self._tick()
            return
        if not enabled:
            self.wave.clear("⚠ " + tr("file_missing"))
            return
        self.wave.clear(tr("waveform_loading"))
        self.wave.editable = row.get("id") is not None                   # library track: cues/sections editable
        self.wave.set_sections(parse_sections(row.get("sections")))      # nothing shown if not analysed
        self.pool.start(_WaveformJob(self.path, self.emitter))
        self.source_file = None
        self._want_play = autoplay
        if self.player is not None:
            self._load_version(keep_position=False)
        self._tick()

    # -- transposition
    def set_semitones(self, st: int):
        if st != 0 and not self._tp_available:
            st = 0
        self.semitones = st
        self.tp_buttons[st].setChecked(True)
        self._update_title()
        self.deck_changed.emit()
        if self.path and self.player is not None:
            self._load_version(keep_position=True)

    def _load_version(self, keep_position: bool):
        """Play the original (0) or the transposed version of the current track. If the transposed
        version still has to be rendered, the current audio keeps playing and is replaced seamlessly."""
        from ..transpose import cached
        target = self.path if self.semitones == 0 else cached(self.path, self.semitones)
        if target:
            self._switch_source(target, keep_position)
            self._status("")
            return
        key = (self.path, self.semitones)
        if key not in self._rendering:
            self._rendering.add(key)
            self.pool.start(_TransposeJob(self.path, self.semitones, self.tp_emitter))
        self._status(tr("transposing", st=f"{self.semitones:+d}", pct=0))

    def _switch_source(self, file: str, keep_position: bool):
        if self.player is None or file == self.source_file:
            if self._want_play and not self.is_playing():
                self._want_play = False
                self.player.play()
            return
        if keep_position and self.source_file:
            pos, playing = self.player.position(), self.is_playing() or self._want_play
        else:
            pos, playing = 0, self._want_play
        self._want_play = False
        self.player.stop()
        self.source_file = file
        self._pending = (pos, playing) if (pos or playing) else None
        self.player.setSource(QUrl.fromLocalFile(file))
        if not self._pending and playing:
            self.player.play()

    def _transpose_progress(self, path: str, st: int, frac: float):
        if path == self.path and st == self.semitones:
            self._status(tr("transposing", st=f"{st:+d}", pct=int(frac * 100)))

    def _transpose_done(self, path: str, st: int, out: str, err: str):
        self._rendering.discard((path, st))
        if path != self.path or st != self.semitones:
            return
        if err:
            self._status("⚠ " + err)
            return
        self._status("")
        self._switch_source(out, keep_position=True)

    def _status(self, text: str):
        self._status_text = text
        self._tick()

    def _cover_ready(self, path: str, img):
        if path == self.path:
            self.cover.set_image(img)

    def _markers_ready(self, path: str, markers):
        if path == self.path:
            if markers:
                from ..serato import number_by_position
                markers.cues = number_by_position(markers.cues)     # cue n = n-th cue in the track
            self.markers = markers
            self.wave.set_markers(markers)
            self._update_cue_buttons()
            self.cues_read.emit(path, len(markers.cues) if markers else 0)
            self._update_title()

    def seek_ms(self, ms: int):
        """Set the player to an exact position in ms (also while the track is still loading)."""
        if self.player is None or not self.path:
            return
        if self.player.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                                         QMediaPlayer.BufferingMedia, QMediaPlayer.EndOfMedia):
            self.player.setPosition(int(ms))
        else:
            self._pending = (int(ms), self.is_playing())
        dur = self.player.duration() or int(self.wave.duration * 1000)
        if dur:
            self.wave.set_position(ms / dur)
        if self._pending is None:
            self._tick()
        else:
            self.time.setText(f"{fmt_time(ms)} / {fmt_time(dur)}")

    def jump_to_cue(self, slot: int):
        """Jump to Serato hot cue `slot` (1-based)."""
        if not self.markers or self.player is None:
            return
        cue = next((c for c in self.markers.cues if c.index + 1 == slot), None)
        if cue is not None:
            self.seek_ms(cue.position_ms)

    # -- cue navigation
    CUE_BACK_GRACE_MS = 500        # "previous cue" right after passing a cue goes one further back

    def _cue_positions(self) -> list[int]:
        return sorted({c.position_ms for c in self.markers.cues}) if self.markers else []

    # -- editing in the waveform
    def _sections_edited(self, sections: list):
        if self.row and self.row.get("id") is not None:
            import json
            self.row = dict(self.row, sections=json.dumps(sections, separators=(",", ":")), sections_manual=1)
            self.sections_edited.emit(self.row["id"], sections)

    def _cues_edited(self, cues: list):
        from ..serato import SeratoMarkers
        if self.markers is None:
            self.markers = SeratoMarkers()
        self.markers.cues = list(cues)
        self._update_cue_buttons()
        self._update_title()
        if self.row and self.row.get("id") is not None:
            self.cues_edited.emit(self.row["id"], list(cues))

    def run_on_file(self, path: str, fn):
        """Run fn (e.g. writing tags) on a file; if the player holds the file open (Windows locks it), let go of
        it for the write and continue at the same position afterwards."""
        try:
            return fn()
        except OSError:
            if self.player is None or self.source_file is None or \
                    os.path.normcase(self.source_file) != os.path.normcase(path):
                raise
        pos, playing = self._position_ms(), self.is_playing()
        self.player.stop()
        self.player.setSource(QUrl())
        self.source_file = None
        try:
            return fn()
        finally:
            self._want_play = False
            self._switch_source(path, keep_position=False)
            self._pending = (pos, playing) if (pos or playing) else None

    def reload_markers(self):
        """Read the cue points of the loaded file again (e.g. after a failed write)."""
        if self.path:
            from ..serato import read_serato_markers
            self._markers_ready(self.path, read_serato_markers(self.path))

    def _update_cue_buttons(self):
        """Cue buttons work only when the loaded track has cue points."""
        on = bool(self._cue_positions()) and self.player is not None and bool(getattr(self, "path", None))
        self.prev_cue_btn.setEnabled(on)
        self.next_cue_btn.setEnabled(on)

    def _position_ms(self) -> int:
        if self._pending is not None:
            return self._pending[0]
        return self.player.position() if self.player is not None else 0

    def previous_cue(self):
        """Jump to the nearest cue point before the playhead (none before it: stay)."""
        pos = self._position_ms()
        before = [c for c in self._cue_positions() if c < pos - self.CUE_BACK_GRACE_MS]
        if before:
            self.seek_ms(before[-1])

    def next_cue(self):
        """Jump to the nearest cue point after the playhead (none after it: stay)."""
        pos = self._position_ms()
        after = [c for c in self._cue_positions() if c > pos + 1]
        if after:
            self.seek_ms(after[0])

    def previous(self):
        """Like every player: restart the track if it is more than 3 s in, otherwise go to the previous one."""
        if self.player is not None and self.player.position() > 3000:
            self.player.setPosition(0)
            self._tick()
        else:
            self.prev_requested.emit(self.is_playing())

    def _waveform_ready(self, path: str, wf, error: str):
        if path != self.path:
            return          # selection changed meanwhile
        if wf is None:
            self.wave.clear("⚠ " + error)
        else:
            self.wave.set_waveform(wf)
            if self.markers is not None:
                self.wave.set_markers(self.markers)
            self._tick()

    def release_file(self, path: str):
        """Let go of a file (e.g. before writing tags). Returns a restore callback."""
        if self.player is None or path != self.path or self.source_file != path:
            return lambda: None          # not loaded, or a transposed copy is playing -> nothing to release
        pos, playing = self.player.position(), self.is_playing()
        self.player.stop()
        self.player.setSource(QUrl())
        self.source_file = None

        def restore():
            if self.path == path:
                self._pending = (pos, playing)
                self.source_file = path
                self.player.setSource(QUrl.fromLocalFile(path))
                self.wave.set_position(pos / max(1, int(self.wave.duration * 1000)))
        return restore

    # -- transport
    def is_playing(self) -> bool:
        return self.player is not None and self.player.playbackState() == QMediaPlayer.PlayingState

    def toggle(self):
        if self.player is None or not self.path:
            return
        if self.source_file is None:            # transposed version still rendering -> play when ready
            self._want_play = not self._want_play
            return
        if self.is_playing():
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def play(self):
        if self.player is not None and self.path and not self.is_playing():
            self.toggle()

    def stop(self):
        if self.player is not None:
            self.player.stop()
            self.player.setPosition(0)
        self.wave.set_position(0)
        self._tick()

    def seek_fraction(self, frac: float):
        if self.player is None:
            return
        dur = self.player.duration() or int(self.wave.duration * 1000)
        if dur:
            self.player.setPosition(int(frac * dur))
            self._tick()

    def _tick(self):
        if self.player is None:
            return
        pos = self.player.position()
        dur = self.player.duration() or int(self.wave.duration * 1000)
        if dur and not self.wave._drag:
            self.wave.set_position(pos / dur)
        status = getattr(self, "_status_text", "")
        self.time.setText((status + "   " if status else "") + tr("time_of", a=fmt_time(pos), b=fmt_time(dur)))

    def _state_changed(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.set_glyph("pause" if playing else "play")
        (self.timer.start if playing else self.timer.stop)()
        self._tick()

    def _media_status(self, status):
        if status == QMediaPlayer.EndOfMedia and self.path:
            QTimer.singleShot(0, lambda: self.next_requested.emit(True))   # continue with the next track
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._pending:
            pos, playing = self._pending
            self._pending = None
            self.player.setPosition(pos)
            if playing:
                self.player.play()
            self._tick()
        if status == QMediaPlayer.EndOfMedia:
            self.wave.set_position(1.0)

    def _error(self, _err, msg):
        if msg:
            self.wave.message = "⚠ " + msg
            if self.wave.peaks is None:
                self.wave.update()

    def _volume(self, v: int):
        if self.player is not None:
            # perceptual (logarithmic) volume curve
            self.audio.setVolume((v / 100.0) ** 2)
        self.s.volume = v / 100.0
        self._update_mute_icon()

    def _mute(self, on: bool):
        if self.player is not None:
            self.audio.setMuted(on)
        self._update_mute_icon()

    def _update_mute_icon(self):
        if not hasattr(self, "ic_vol"):
            return
        v = self.volume.value()
        self.mute_btn.set_glyph("mute" if self.mute_btn.isChecked() or v == 0 else "vol")
        self.mute_btn.setToolTip(tr("mute"))

    def shutdown(self):
        if self.player is not None:
            self.player.stop()
        self.pool.clear()
        self.pool.waitForDone(3000)
