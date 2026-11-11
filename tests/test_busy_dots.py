"""Running dots in the "fits" column while the analysis queue works on a track."""
import math
import os
import struct
import types
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.gui.main_window import MainWindow
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False))
    for i in range(3):
        p = tmp_path / f"{i}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(n / 9))) for n in range(800)))
        lib.db.upsert_file(str(p), 1, 0, dict(title=f"T{i}"))
    w = MainWindow(lib)
    w.show(); app.processEvents()
    yield w
    w._cue_cancel.set()
    w.player.shutdown(); w.queue.shutdown(); w.deleteLater()


def test_dots_follow_the_running_queue_items(win, monkeypatch):
    from keytool.analysis_queue import RUNNING, WAITING
    from keytool.gui.widgets import TrackModel
    ids = [r["id"] for r in win.model.rows]
    items = [types.SimpleNamespace(track_id=ids[0], state=RUNNING), types.SimpleNamespace(track_id=ids[1], state=WAITING)]
    monkeypatch.setattr(win.queue, "items", lambda: items)
    win._update_busy()
    assert win.model.busy_ids == {ids[0]} and win._busy_timer.isActive()
    col = TrackModel.COLUMNS.index("fit")
    row = next(i for i, r in enumerate(win.model.rows) if r["id"] == ids[0])
    from keytool.i18n import tr
    assert win.model.data(win.model.index(row, col), Qt.ToolTipRole) == tr("analyzing_now")
    a0 = win.model.busy_phase
    win._busy_tick(); win._busy_tick()
    assert win.model.busy_phase != a0                                  # running
    win.table.viewport().grab()                                        # painting the dots works
    items[0].state = "done"
    win._update_busy()
    assert win.model.busy_ids == set() and not win._busy_timer.isActive()


def test_dots_light_up_one_after_the_other():
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage, QPainter
    from keytool.gui.widgets import paint_busy_dots

    def alphas(phase):
        img = QImage(30, 10, QImage.Format_ARGB32); img.fill(0)
        p = QPainter(img); paint_busy_dots(p, QPointF(15, 5), phase, QColor("#000000")); p.end()
        return [img.pixelColor(x, 5).alpha() for x in (9, 15, 21)]
    a, b = alphas(0.0), alphas(1 / 3)
    assert a[0] > a[1] and a[0] > a[2]                                   # first dot brightest ...
    assert b[1] > b[0] and b[1] > b[2]                                   # ... then the second
