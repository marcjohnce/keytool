"""Deck waveform zoom: minimum 100 %, zoom around a point, scrolling, following the playhead, deck buttons."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from keytool.serato import Cue


@pytest.fixture
def wave(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    w = pl.WaveformView()
    w.resize(608, 84 + w.STRIP)                      # 600 px
    w.duration, w.bpm = 120.0, 120.0
    w.peaks = np.linspace(0, 1, 12000).astype(np.float32); w.colors = np.full((12000, 3), 128, np.uint8)
    w.cues = [Cue(0, 30000, "#cc0000")]
    w.set_sections([dict(start=0, end=60, group="A", label="verse", conf=1.0),
                    dict(start=60, end=120, group="B", label="chorus", conf=1.0)])
    yield w
    w.deleteLater(); app.processEvents()


def test_zoom_limits_and_mapping(wave):
    assert wave.zoom == 1.0 and wave._x_of(60) == pytest.approx(304)
    wave.set_zoom(0.5)
    assert wave.zoom == 1.0                                                  # never below 100 %
    wave.set_zoom(1000)
    assert wave.zoom == 30.0                                                 # 120 s / 4 s at the most
    wave.set_zoom(1.0)
    wave.set_zoom(4.0, anchor=60.0)                                          # 60 s stays in the middle
    assert wave.view0 == pytest.approx(45.0) and wave._x_of(60) == pytest.approx(304)
    assert wave._frac(4) == pytest.approx(45 / 120) and wave._t_of(604) == pytest.approx(75)
    assert wave._section_x(wave.sections[1])[0] == pytest.approx(304)
    wave.set_zoom(4.0, anchor=1.0)                                           # near the start: view stays inside
    wave.set_zoom(8.0, anchor=0.0)
    assert wave.view0 == 0.0
    wave.scroll_by(-10)
    assert wave.view0 == 0.0
    wave.scroll_by(1000)
    assert wave.view0 == pytest.approx(120 - 15)                             # last 15 s at 8x
    wave.grab()                                                              # paints the zoomed view


def test_wheel_zooms_at_the_pointer(wave):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    ev = QWheelEvent(QPointF(154, 40), QPointF(154, 40), QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    wave.wheelEvent(ev)
    assert wave.zoom == pytest.approx(1.25) and wave._x_of(30.0) == pytest.approx(154)   # 30 s stays put
    ev = QWheelEvent(QPointF(154, 40), QPointF(154, 40), QPoint(), QPoint(0, -120), Qt.NoButton, Qt.ShiftModifier,
                     Qt.NoScrollPhase, False)
    v0 = wave.view0
    wave.wheelEvent(ev)
    assert wave.view0 > v0 and wave.zoom == pytest.approx(1.25)              # Shift: scroll, no zoom


def test_view_follows_the_playhead(wave):
    wave.set_zoom(4.0, anchor=0.0)                                           # 0..30 s in view
    wave.set_position(20 / 120)
    assert wave.view0 == 0.0
    wave.set_position(28 / 120)                                              # past 90 %: page on
    assert wave.view0 == pytest.approx(28 - 3)
    wave.scroll_by(60)                                                       # looked elsewhere: no following
    v0 = wave.view0
    wave.set_position(29 / 120)
    assert wave.view0 == v0


def test_click_and_cue_hit_in_zoomed_view(wave):
    from PySide6.QtCore import QPointF
    got = []
    wave.seek_requested.connect(got.append)
    wave.set_zoom(4.0, anchor=30.0)                                          # 30 s keeps its place: 22.5..52.5 s
    wave.grab()
    assert wave._cue_at(QPointF(155, 40)).position_ms == 30000 and wave._cue_at(QPointF(305, 40)) is None

    class Ev:
        def position(self):
            return QPointF(4 + 300, 40)                                      # middle of the view = 37.5 s

        def button(self):
            from PySide6.QtCore import Qt
            return Qt.LeftButton
    wave.mousePressEvent(Ev())
    wave.mouseReleaseEvent(None)
    assert got[-1] == pytest.approx(37.5 / 120)


def test_deck_zoom_buttons(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.config import Settings
    p = pl.PlayerPanel(Settings())
    p.wave.duration = 120.0
    p.wave.peaks = np.ones(1000, np.float32); p.wave.colors = np.full((1000, 3), 128, np.uint8)
    p.wave.set_zoom(1.0)                                                     # as after loading a waveform
    assert p.zoom_lbl.text() == "100 %" and not p.zoom_out_btn.isEnabled() and p.zoom_in_btn.isEnabled()
    p.zoom_in_btn.click()
    assert p.wave.zoom == 1.5 and p.zoom_lbl.text() == "150 %" and p.zoom_out_btn.isEnabled()
    p.zoom_lbl.click()
    assert p.wave.zoom == 1.0
    p.deleteLater(); app.processEvents()
