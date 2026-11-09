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


def fmt_time(ms: int | float | None) -> str:
    if not ms or ms < 0:
        return "0:00"
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# =========================================================================== background loading
class _Emitter(QObject):
    done = Signal(str, object, str)   # path, Waveform | None, error


class _WaveformJob(QRunnable):
    def __init__(self, path: str, emitter: _Emitter):
        super().__init__()
        self.path, self.emitter = path, emitter

    def run(self):
        try:
            from ..waveform import get_waveform
            self.emitter.done.emit(self.path, get_waveform(self.path), "")
        except Exception as e:
            self.emitter.done.emit(self.path, None, f"{type(e).__name__}: {e}")


# =========================================================================== waveform view
class WaveformView(QWidget):
    seek_requested = Signal(float)    # fraction 0..1

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

    def set_waveform(self, wf):
        self.peaks, self.colors, self.duration = wf.peaks, wf.colors, wf.duration
        self.message = ""
        self.update()

    def clear(self, message: str = ""):
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

    def mousePressEvent(self, e):
        if self.peaks is not None and e.button() == Qt.LeftButton:
            self._drag = True
            f = self._frac(e.position().x())
            self.set_position(f)
            self.seek_requested.emit(f)

    def mouseMoveEvent(self, e):
        if self.peaks is None:
            return
        self.hover = self._frac(e.position().x())
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
    """Transport controls + waveform. Plays through the system's default audio output."""

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
        self.notation = "camelot"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        bar = QHBoxLayout()
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("primary")
        self.play_btn.setFixedWidth(44)
        self.play_btn.clicked.connect(self.toggle)
        self.stop_btn = QPushButton()
        self.stop_btn.setFixedWidth(44)
        self.stop_btn.clicked.connect(self.stop)
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
        for wdg, stretch in ((self.play_btn, 0), (self.stop_btn, 0), (self.title, 1), (self.time, 0),
                             (self.mute_btn, 0), (self.volume, 0)):
            bar.addWidget(wdg, stretch)
        lay.addLayout(bar)
        self.wave = WaveformView()
        self.wave.seek_requested.connect(self.seek_fraction)
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

    # -- appearance
    def retranslate(self):
        super().retranslate()
        self.play_btn.setToolTip(tr("play_pause") + " (Space)")
        self.stop_btn.setToolTip(tr("stop"))
        self.volume.setToolTip(tr("volume"))
        self._update_mute_icon()
        if self.row is None:
            self.wave.clear(tr("no_selection"))
        elif self.wave.peaks is None and not self.wave.message.startswith("⚠"):
            self.wave.message = tr("waveform_loading")
        self._update_title()

    def _make_icons(self):
        st, pal = self.style(), self.palette()
        fg = pal.color(pal.ColorRole.Text)
        on_accent = pal.color(pal.ColorRole.HighlightedText)
        self.ic_play = tinted(st.standardIcon(QStyle.SP_MediaPlay), on_accent)
        self.ic_pause = tinted(st.standardIcon(QStyle.SP_MediaPause), on_accent)
        self.ic_vol = tinted(st.standardIcon(QStyle.SP_MediaVolume), fg)
        self.ic_mute = tinted(st.standardIcon(QStyle.SP_MediaVolumeMuted), fg)
        self.stop_btn.setIcon(tinted(st.standardIcon(QStyle.SP_MediaStop), fg))
        self.play_btn.setIcon(self.ic_pause if self.is_playing() else self.ic_play)
        self._update_mute_icon()

    def set_dark(self, dark: bool):
        self.wave.dark = dark
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
        if k:
            bits.append(k.format(self.notation))
        title = (r.get("title") or r.get("filename") or "").replace("<", "&lt;")
        artist = (r.get("artist") or "").replace("<", "&lt;")
        extra = " · ".join(bits)
        self.title.setText(f"<b>{title}</b>" + (f" — {artist}" if artist else "") +
                           (f"&nbsp;&nbsp;<span style='opacity:.7'>{extra}</span>" if extra else ""))

    # -- track handling
    def set_track(self, row: dict | None):
        """Load the selected track (paused). Called on every selection change."""
        same = row is not None and self.row is not None and row.get("path") == self.row.get("path")
        self.row = row
        self._update_title()
        if same:
            self.wave.bpm = row.get("bpm")
            return
        enabled = row is not None and os.path.exists(row.get("path", ""))
        for wdg in (self.play_btn, self.stop_btn):
            wdg.setEnabled(enabled and self.player is not None)
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
        if self.player is not None:
            self.player.setSource(QUrl.fromLocalFile(self.path))
        self._tick()

    def _waveform_ready(self, path: str, wf, error: str):
        if path != self.path:
            return          # selection changed meanwhile
        if wf is None:
            self.wave.clear("⚠ " + error)
        else:
            self.wave.set_waveform(wf)
            self._tick()

    def release_file(self, path: str):
        """Let go of a file (e.g. before writing tags). Returns a restore callback."""
        if self.player is None or path != self.path:
            return lambda: None
        pos, playing = self.player.position(), self.is_playing()
        self.player.stop()
        self.player.setSource(QUrl())

        def restore():
            if self.path == path:
                self._pending = (pos, playing)
                self.player.setSource(QUrl.fromLocalFile(path))
                self.wave.set_position(pos / max(1, int(self.wave.duration * 1000)))
        return restore

    # -- transport
    def is_playing(self) -> bool:
        return self.player is not None and self.player.playbackState() == QMediaPlayer.PlayingState

    def toggle(self):
        if self.player is None or not self.path:
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
        self.time.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _state_changed(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(self.ic_pause if playing else self.ic_play)
        (self.timer.start if playing else self.timer.stop)()
        self._tick()

    def _media_status(self, status):
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
