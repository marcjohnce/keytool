"""The deck must not shift when the tempo or the key name changes length."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from keytool.keys import Key  # noqa: E402


@pytest.mark.parametrize("lang", ["en", "de"])
def test_key_block_stays_put(tmp_path, monkeypatch, lang):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.config import Settings
    from keytool.gui import theme
    from keytool.i18n import set_language
    try:
        set_language(lang)
        theme.apply_theme(app, "dark")
        p = pl.PlayerPanel(Settings())
        p.resize(1400, 230); p.show(); app.processEvents()
        spots = set()
        for bpm, cam, st in ((98.0, "8A", 0), (124.5, "12A", 1), (174.0, "3B", -1), (None, "6B", 0)):
            p.semitones = st
            p.set_track(dict(id=1, path="/x.mp3", title="T", artist="A", key=Key.parse(cam).code, bpm=bpm, energy=10))
            app.processEvents()
            spots.add((p.key_chip.mapTo(p, QPoint(0, 0)).x(), p.tp_buttons[0].mapTo(p, QPoint(0, 0)).x(),
                       p.mute_btn.mapTo(p, QPoint(0, 0)).x()))
        assert len(spots) == 1, spots
        assert p.bpm_lbl.text() in ("–",)                       # last case: no tempo
        p.deleteLater(); app.processEvents()
    finally:
        set_language("de")
