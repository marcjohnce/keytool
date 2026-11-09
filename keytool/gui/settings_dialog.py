from __future__ import annotations

import json
import sys

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from ..config import Settings, db_path
from ..i18n import LANGUAGES, tr
from ..keys import NOTATIONS


def mcp_config_snippet() -> str:
    if getattr(sys, "frozen", False):
        import os
        cmd, args = os.path.join(os.path.dirname(sys.executable), "KeyTool-MCP.exe"), []
    else:
        import os
        exe = sys.executable
        if os.path.basename(exe).lower() == "pythonw.exe":  # MCP over stdio needs the console interpreter
            exe = os.path.join(os.path.dirname(exe), "python.exe")
        cmd, args = exe, ["-m", "keytool", "--mcp"]
    return json.dumps({"mcpServers": {"keytool": {"command": cmd, "args": args,
                                                  "env": {"KEYTOOL_DB": str(db_path())}}}}, indent=2)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle(tr("settings"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)

        g = QGroupBox(tr("s_general"))
        f = QFormLayout(g)
        self.lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(self.lang.findData(settings.language))
        f.addRow(tr("language"), self.lang)
        self.theme = QComboBox()
        for code, key in (("dark", "theme_dark"), ("light", "theme_light"), ("system", "theme_system")):
            self.theme.addItem(tr(key), code)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        f.addRow(tr("s_theme"), self.theme)
        self.notation = QComboBox()
        for n in NOTATIONS:
            self.notation.addItem(tr(f"n_{n}"), n)
        self.notation.setCurrentIndex(self.notation.findData(settings.notation))
        f.addRow(tr("notation"), self.notation)
        lay.addWidget(g)

        g = QGroupBox(tr("s_analysis"))
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.bmin = QDoubleSpinBox(); self.bmin.setRange(30, 300); self.bmin.setValue(settings.bpm_min)
        self.bmax = QDoubleSpinBox(); self.bmax.setRange(40, 400); self.bmax.setValue(settings.bpm_max)
        row.addWidget(self.bmin); row.addWidget(QLabel("–")); row.addWidget(self.bmax)
        lbl = QLabel(tr("s_bpm_range")); lbl.setToolTip(tr("s_bpm_range_tip"))
        f.addRow(lbl, row)
        self.workers = QSpinBox(); self.workers.setRange(0, 64); self.workers.setValue(settings.workers)
        f.addRow(tr("s_workers"), self.workers)
        self.write_tags = QCheckBox(tr("s_write_tags").replace("&", "&&")); self.write_tags.setChecked(settings.write_tags)
        f.addRow(self.write_tags)
        self.tag_notation = QComboBox()
        for n in NOTATIONS:
            self.tag_notation.addItem(tr(f"n_{n}"), n)
        self.tag_notation.setCurrentIndex(self.tag_notation.findData(settings.tag_key_notation))
        f.addRow(tr("s_tag_notation"), self.tag_notation)
        self.reanalyze = QCheckBox(tr("s_reanalyze")); self.reanalyze.setChecked(settings.reanalyze_existing)
        f.addRow(self.reanalyze)
        lay.addWidget(g)

        g = QGroupBox(tr("s_mcp"))
        v = QVBoxLayout(g)
        info = QLabel(tr("s_mcp_info")); info.setWordWrap(True)
        v.addWidget(info)
        self.snippet = QPlainTextEdit(mcp_config_snippet()); self.snippet.setReadOnly(True)
        self.snippet.setMinimumHeight(150)
        v.addWidget(self.snippet)
        cp = QPushButton(tr("copy"))
        cp.clicked.connect(lambda: QApplication.clipboard().setText(self.snippet.toPlainText()))
        v.addWidget(cp)
        lay.addWidget(g)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply(self) -> None:
        s = self.s
        s.language = self.lang.currentData()
        s.theme = self.theme.currentData()
        s.notation = self.notation.currentData()
        s.bpm_min, s.bpm_max = sorted((self.bmin.value(), self.bmax.value()))
        if s.bpm_max < s.bpm_min * 2:  # range must span one octave for folding to work
            s.bpm_max = s.bpm_min * 2
        s.workers = self.workers.value()
        s.write_tags = self.write_tags.isChecked()
        s.tag_key_notation = self.tag_notation.currentData()
        s.reanalyze_existing = self.reanalyze.isChecked()
        s.save()
