"""Settings dialog redesign: every control still maps onto Settings when saving."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


def test_settings_dialog_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.gui.settings_dialog import SettingsDialog
    s = Settings()
    d = SettingsDialog(s)
    assert d.nav.count() == d.pages.count() == 5                     # display, analysis, files & tags, CSV export, MCP
    d.lang.setCurrentIndex(d.lang.findData("en"))
    d.theme.setCurrentIndex(d.theme.findData("light"))
    d.key_method.setCurrentIndex(d.key_method.findData("fast"))
    d.bmin.setValue(90); d.bmax.setValue(100)            # must span an octave -> corrected to 180
    d.workers.setValue(3)
    d.use_tags.setChecked(False); d.reanalyze.setChecked(True)
    d.write_tags.setChecked(False)
    assert not d.tag_notation.isEnabled()                # notation only matters when tags are written
    d.write_edits.setChecked(False)
    d.apply()
    s2 = Settings.load()
    assert (s2.language, s2.theme, s2.key_method, s2.workers) == ("en", "light", "fast", 3)
    assert (s2.bpm_min, s2.bpm_max) == (90, 180)
    assert not s2.use_existing_tags and s2.reanalyze_existing and not s2.write_tags and not s2.write_edits


def test_settings_dialog_retag_patterns(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.gui.settings_dialog import SettingsDialog
    s = Settings()
    d = SettingsDialog(s)
    assert "Mara Veil" in d.pattern_result.text()                            # sample matches the default
    d.patterns.setPlainText("(?<artist>.+\n" + r"^(?<title>.+)\.(?<ext>mp3)$")
    assert not d.patterns_err.isHidden() and "1" in d.patterns_err.text()
    d.pattern_sample.setText("Some Song.mp3")
    assert "Some Song" in d.pattern_result.text()
    d.apply()
    assert Settings.load().retag_patterns == ["(?<artist>.+", r"^(?<title>.+)\.(?<ext>mp3)$"]
