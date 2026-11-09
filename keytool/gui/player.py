"""Waveform view + audio player for the selected track (system default output device)."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QToolButton, QVBoxLayout, QWidget

from ..i18n import tr
from ..keys import Key
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


CUE_COLOR = "#c02626"     # all cue points are shown in red (Serato DJ Pro's cue red)


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
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Ignored)
        self.setMinimumHeight(40)
        self.setFixedWidth(124)
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
        clip.addRoundedRect(r, 10, 10)
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
            label = camelot_color(self.key_code, self.dark) if self.key_code else pal.color(pal.ColorRole.Highlight)
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
        p.drawRoundedRect(r, 10, 10)

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
        self._flag_rects: list = []   # (QRectF, cue) for click / hover

    def set_waveform(self, wf):
        self.peaks, self.colors, self.duration = wf.peaks, wf.colors, wf.duration
        self.message = ""
        self.update()

    def set_markers(self, markers):
        self.cues = list(markers.cues) if markers else []
        self.loops = list(markers.loops) if markers else []
        self.update()

    def clear(self, message: str = ""):
        self.cues, self.loops = [], []
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
        p.setPen(QPen(pal.color(pal.ColorRole.Mid), 1))
        p.setBrush(pal.color(pal.ColorRole.Base))
        p.drawRoundedRect(QRectF(r), 8, 8)
        w, h = self.width(), self.height()
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
                p.fillRect(QRectF(x0, h - 6, max(2.0, x1 - x0), 4), c)
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
                c.setAlpha(140)
            p.setPen(c)
            hh = max(1.0, level * amp)
            p.drawLine(QPointF(px, mid - hh), QPointF(px, mid + hh))
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
                fw = max(16, fm.horizontalAdvance(label) + 8)
                rect = QRectF(x, 2, fw, fm.height() + 2)
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawRect(rect)
                p.setPen(QColor("#ffffff") if col.lightness() < 150 else QColor("#111111"))
                p.drawText(rect, Qt.AlignCenter, label)
                self._flag_rects.append((rect, cue))
            p.setFont(self.font())
        # hover + playhead
        if self.hover is not None:
            hc = QColor(pal.color(pal.ColorRole.Text)); hc.setAlpha(90)
            p.setPen(QPen(hc, 1))
            hx = 4 + self.hover * inner
            p.drawLine(QPointF(hx, 2), QPointF(hx, h - 2))
            p.drawText(QRectF(hx + 4, 2, 70, 16), Qt.AlignLeft, fmt_time(self.hover * self.duration * 1000))
        p.setPen(QPen(pal.color(pal.ColorRole.Highlight), 2))
        p.drawLine(QPointF(played_x, 1), QPointF(played_x, h - 1))

    # -- interaction
    def _frac(self, x: float) -> float:
        return min(max((x - 4) / max(1, self.width() - 8), 0.0), 1.0)

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

    def mousePressEvent(self, e):
        cue = self._cue_at(e.position()) if self.peaks is not None else None
        if cue is not None and e.button() == Qt.LeftButton:
            if self.duration:
                self.set_position(cue.position_ms / (self.duration * 1000.0))
            self.cue_clicked.emit(cue.position_ms)
            return
        if self.peaks is not None and e.button() == Qt.LeftButton:
            self._drag = True
            f = self._frac(e.position().x())
            self.set_position(f)
            self.seek_requested.emit(f)

    def mouseMoveEvent(self, e):
        if self.peaks is None:
            return
        self.hover = self._frac(e.position().x())
        cue = self._cue_at(e.position())
        if cue is not None:
            name = f" – {cue.name}" if cue.name else ""
            self.setToolTip(f"Cue {cue.index + 1}{name} · {fmt_time(cue.position_ms)}")
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

    def leaveEvent(self, _):
        self.hover = None
        self.update()


# =========================================================================== player panel
class PlayerPanel(QWidget, Translatable):
    """Cover + transport controls + waveform. Plays through the system's default audio output."""
    prev_requested = Signal(bool)     # keep playing?
    next_requested = Signal(bool)

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

        # coloured panel (list-selection blue, see theme.py "#playerPanel")
        self.setObjectName("playerPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        self.cover = CoverView()
        outer.addWidget(self.cover, 0)
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        outer.addLayout(lay, 1)
        bar = QHBoxLayout()
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("primary")
        self.play_btn.setFixedWidth(44)
        self.play_btn.clicked.connect(self.toggle)
        self.prev_btn = QPushButton()
        self.prev_btn.setFixedWidth(40)
        self.prev_btn.clicked.connect(self.previous)
        self.next_btn = QPushButton()
        self.next_btn.setFixedWidth(40)
        self.next_btn.clicked.connect(lambda: self.next_requested.emit(self.is_playing()))
        self.stop_btn = QPushButton()
        self.stop_btn.setFixedWidth(44)
        self.stop_btn.clicked.connect(self.stop)
        # transposition: -1 / 0 / +1 semitone at the original tempo
        from PySide6.QtWidgets import QButtonGroup
        from .. import transpose as _tp
        self.semitones = 0
        self.source_file: str | None = None       # file currently loaded in the player (original or transposed)
        self._rendering: set[tuple[str, int]] = set()
        self._want_play = False
        self._tp_available = _tp.available()
        self.tp_emitter = _TransposeEmitter()
        self.tp_emitter.progress.connect(self._transpose_progress)
        self.tp_emitter.done.connect(self._transpose_done)
        self.tp_group = QButtonGroup(self)
        self.tp_group.setExclusive(True)
        self.tp_buttons: dict[int, QPushButton] = {}
        for st, label in ((-1, "−1"), (0, "0"), (1, "+1")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedSize(38, 30)
            b.setChecked(st == 0)
            b.setObjectName("transpose")
            b.clicked.connect(lambda _=False, st=st: self.set_semitones(st))
            self.tp_group.addButton(b)
            self.tp_buttons[st] = b
        self.title = QLabel()
        self.title.setTextFormat(Qt.RichText)
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("dim")
        self.time.setMinimumWidth(110)
        self.time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.mute_btn = QToolButton()
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self._mute)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(110)
        self.volume.setValue(int(round(getattr(settings, "volume", 0.8) * 100)))
        self.volume.valueChanged.connect(self._volume)
        tp_lbl = QLabel("♯♭"); tp_lbl.setObjectName("tpLabel")
        self._tp_label = tp_lbl
        for wdg, stretch in ((self.prev_btn, 0), (self.play_btn, 0), (self.next_btn, 0), (self.stop_btn, 0),
                             (tp_lbl, 0), (self.tp_buttons[-1], 0), (self.tp_buttons[0], 0), (self.tp_buttons[1], 0),
                             (self.title, 1), (self.time, 0),
                             (self.mute_btn, 0), (self.volume, 0)):
            bar.addWidget(wdg, stretch)
        lay.addLayout(bar)
        self.wave = WaveformView()
        self.wave.seek_requested.connect(self.seek_fraction)
        self.wave.cue_clicked.connect(self.seek_ms)
        lay.addWidget(self.wave)

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

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # square cover, flush with the top of the controls and the bottom of the waveform
        m = self.layout().contentsMargins()
        self.cover.setFixedWidth(max(40, self.height() - m.top() - m.bottom()))

    # -- appearance
    def retranslate(self):
        super().retranslate()
        self.play_btn.setToolTip(tr("play_pause") + " (Space)")
        self.stop_btn.setToolTip(tr("stop"))
        for st, b in self.tp_buttons.items():
            b.setToolTip(tr("transpose_tip", st=f"{st:+d}" if st else "0") if self._tp_available
                         else tr("transpose_unavailable"))
            b.setEnabled(self._tp_available or st == 0)
        self._tp_label.setToolTip(tr("transpose"))
        self.prev_btn.setToolTip(tr("prev_track") + " (Ctrl+←)")
        self.next_btn.setToolTip(tr("next_track") + " (Ctrl+→)")
        self.volume.setToolTip(tr("volume"))
        self._update_mute_icon()
        if self.row is None:
            self.wave.clear(tr("no_selection"))
        elif self.wave.peaks is None and not self.wave.message.startswith("⚠"):
            self.wave.message = tr("waveform_loading")
        self._update_title()

    def _make_icons(self):
        # buttons are white on the blue panel -> black icons everywhere
        st = self.style()
        fg = on_accent = QColor("#000000")
        self.ic_play = tinted(st.standardIcon(QStyle.SP_MediaPlay), on_accent)
        self.ic_pause = tinted(st.standardIcon(QStyle.SP_MediaPause), on_accent)
        self.ic_vol = tinted(st.standardIcon(QStyle.SP_MediaVolume), fg)
        self.ic_mute = tinted(st.standardIcon(QStyle.SP_MediaVolumeMuted), fg)
        self.stop_btn.setIcon(tinted(st.standardIcon(QStyle.SP_MediaStop), fg))
        self.prev_btn.setIcon(tinted(st.standardIcon(QStyle.SP_MediaSkipBackward), fg))
        self.next_btn.setIcon(tinted(st.standardIcon(QStyle.SP_MediaSkipForward), fg))
        self.play_btn.setIcon(self.ic_pause if self.is_playing() else self.ic_play)
        self._update_mute_icon()

    def set_dark(self, dark: bool):
        self.wave.dark = dark
        self.cover.dark = dark
        self.cover.update()
        self._make_icons()
        self.wave.update()

    def set_notation(self, notation: str):
        self.notation = notation
        self._update_title()

    def _update_title(self):
        r = self.row
        if not r:
            self.title.setText("")
            return
        bits = []
        if r.get("bpm"):
            bits.append(f"{r['bpm']:.1f} BPM")
        k = Key.from_code(r.get("key"))
        st = getattr(self, "semitones", 0)
        if k and st:
            bits.append(f"{k.format(self.notation)} → <b>{k.transpose(st).format(self.notation)}</b> ({st:+d})")
        elif k:
            bits.append(k.format(self.notation))
        elif st:
            bits.append(f"{st:+d} HT" if tr.lang == "de" else f"{st:+d} st")
        title = (r.get("title") or r.get("filename") or "").replace("<", "&lt;")
        artist = (r.get("artist") or "").replace("<", "&lt;")
        mk = getattr(self, "markers", None)
        if mk:
            bits.append(f"{len(mk.cues)} Cues" + (f" · {len(mk.loops)} Loops" if mk.loops else ""))
        extra = " · ".join(bits)
        self.title.setText(f"<b>{title}</b>" + (f" — {artist}" if artist else "") +
                           (f"&nbsp;&nbsp;<span style='opacity:.7'>{extra}</span>" if extra else ""))

    # -- track handling
    def set_track(self, row: dict | None, autoplay: bool = False):
        """Load the selected track (paused unless autoplay). Called on every selection change."""
        same = row is not None and self.row is not None and row.get("path") == self.row.get("path")
        self.row = row
        self._update_title()
        self.cover.set_key(row.get("key") if row else None)
        if same:
            self.wave.bpm = row.get("bpm")
            if autoplay:
                self.play()
            return
        enabled = row is not None and os.path.exists(row.get("path", ""))
        for wdg in (self.play_btn, self.stop_btn, self.prev_btn, self.next_btn):
            wdg.setEnabled(enabled and self.player is not None)
        self.cover.set_image(None)
        self.markers = None
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
            self.markers = markers
            self.wave.set_markers(markers)
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
        self.time.setText((status + "   ") if status else "")
        self.time.setText(self.time.text() + f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _state_changed(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(self.ic_pause if playing else self.ic_play)
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
        self.mute_btn.setIcon(self.ic_mute if self.mute_btn.isChecked() or v == 0 else self.ic_vol)
        self.mute_btn.setToolTip(tr("mute"))

    def shutdown(self):
        if self.player is not None:
            self.player.stop()
        self.pool.clear()
        self.pool.waitForDone(3000)
