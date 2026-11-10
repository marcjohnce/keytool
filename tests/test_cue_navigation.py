"""Player buttons 'to previous / next cue point': placed around play/pause, disabled without cue points."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


class FakePlayer:
    """Just enough of QMediaPlayer for seeking."""
    def __init__(self, pl):
        self.pos, self.pl = 0, pl

    def position(self): return self.pos
    def setPosition(self, ms): self.pos = ms
    def duration(self): return 240_000
    def mediaStatus(self): return self.pl.QMediaPlayer.LoadedMedia
    def playbackState(self): return self.pl.QMediaPlayer.StoppedState
    def stop(self): pass
    def setSource(self, _): pass


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    if not pl.HAVE_MULTIMEDIA:
        pytest.skip("Qt Multimedia not available")
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)          # no audio device needed
    from keytool.config import Settings
    p = pl.PlayerPanel(Settings())
    p.player = FakePlayer(pl)
    p.path = str(tmp_path / "x.mp3")
    yield p
    p.player = None
    p.deleteLater()
    QApplication.processEvents()


def _markers(*positions):
    from keytool.serato import Cue, SeratoMarkers
    return SeratoMarkers(cues=[Cue(i, ms, "#cc0000") for i, ms in enumerate(positions)])


def test_buttons_sit_around_play(panel):
    bar = panel.play_btn.parentWidget().layout()
    order = []
    def walk(lay):
        for i in range(lay.count()):
            it = lay.itemAt(i)
            if it.widget():
                order.append(it.widget())
            elif it.layout():
                walk(it.layout())
    walk(bar)
    i = order.index(panel.play_btn)
    assert order[i - 1] is panel.prev_cue_btn and order[i + 1] is panel.next_cue_btn
    assert order.index(panel.prev_btn) < i - 1 and order.index(panel.next_btn) > i + 1


def test_disabled_without_cue_points(panel):
    panel._markers_ready(panel.path, None)
    assert not panel.prev_cue_btn.isEnabled() and not panel.next_cue_btn.isEnabled()
    panel._markers_ready(panel.path, _markers())                 # Serato data, but no cues
    assert not panel.prev_cue_btn.isEnabled()
    panel._markers_ready(panel.path, _markers(30_000))
    assert panel.prev_cue_btn.isEnabled() and panel.next_cue_btn.isEnabled()
    panel.set_track(None)                                         # unloading the deck disables them again
    assert not panel.prev_cue_btn.isEnabled() and not panel.next_cue_btn.isEnabled()


def test_jumps_between_cue_points(panel):
    panel._markers_ready(panel.path, _markers(90_000, 30_000, 150_000))   # any slot order
    panel.player.pos = 0
    panel.next_cue_btn.click(); assert panel.player.pos == 30_000
    panel.next_cue_btn.click(); assert panel.player.pos == 90_000
    panel.next_cue_btn.click(); assert panel.player.pos == 150_000
    panel.next_cue_btn.click(); assert panel.player.pos == 150_000        # no further cue: stays
    panel.player.pos = 150_200                                             # just past a cue (playing on)
    panel.prev_cue_btn.click(); assert panel.player.pos == 90_000         # goes one further back
    panel.player.pos = 120_000
    panel.prev_cue_btn.click(); assert panel.player.pos == 90_000
    panel.player.pos = 10_000
    panel.prev_cue_btn.click(); assert panel.player.pos == 10_000         # none before: stays
