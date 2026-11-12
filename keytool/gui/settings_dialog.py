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


class ToggleSwitch(QCheckBox):
    """On/off switch: amber track with the knob on the right when on, outlined track when off."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import Qt
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(46, 26)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor, QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        on = self.isChecked()
        accent = pal.color(pal.ColorRole.Link)
        ink = pal.color(pal.ColorRole.Text)
        line = pal.color(pal.ColorRole.Mid)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        if on:
            p.setPen(Qt.NoPen); p.setBrush(accent if self.isEnabled() else line)
        else:
            p.setPen(QPen(line, 1.5)); p.setBrush(pal.color(pal.ColorRole.Base))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - 3 - d if on else r.left() + 3
        knob = QColor("#1A1206") if on and accent.lightness() > 140 else (QColor("#FFFFFF") if on else ink)
        if not self.isEnabled():
            knob.setAlpha(110)
        p.setPen(Qt.NoPen); p.setBrush(knob)
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
        if self.hasFocus():
            p.setPen(QPen(accent, 2)); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), r.height() / 2, r.height() / 2)


class SettingsDialog(QDialog):
    """Settings with a section list on the left and described rows on the right:
    every setting shows what it does instead of hiding it in a tooltip."""

    CONTROL_W = 260

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidget, QScrollArea, QStackedWidget, QWidget
        self.s = settings
        self.setWindowTitle(tr("settings"))
        self.setObjectName("settingsDialog")
        self.setMinimumSize(900, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        root.addLayout(body, 1)

        self.nav = QListWidget(); self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(210)
        self.nav.setFocusPolicy(Qt.StrongFocus)
        self.pages = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)

        def page(title_key: str, intro_key: str):
            w = QWidget(); w.setObjectName("settingsPage")
            v = QVBoxLayout(w); v.setContentsMargins(32, 26, 32, 26); v.setSpacing(0)
            h = QLabel(tr(title_key)); h.setObjectName("settingsTitle")
            i = QLabel(tr(intro_key)); i.setObjectName("dim"); i.setWordWrap(True)
            v.addWidget(h); v.addSpacing(4); v.addWidget(i); v.addSpacing(18)
            sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(w); sc.setFrameShape(QScrollArea.NoFrame)
            self.pages.addWidget(sc)
            self.nav.addItem(tr(title_key))
            return v

        def row(v, title: str, desc: str, control, align_top: bool = False):
            r = QWidget(); r.setObjectName("settingsRow")
            hl = QHBoxLayout(r); hl.setContentsMargins(0, 14, 0, 14); hl.setSpacing(28)
            txt = QVBoxLayout(); txt.setSpacing(3)
            t = QLabel(title); t.setObjectName("rowTitle"); t.setWordWrap(True)
            txt.addWidget(t)
            if desc:
                d = QLabel(desc); d.setObjectName("dim"); d.setWordWrap(True)
                txt.addWidget(d)
            txt.addStretch(1)
            hl.addLayout(txt, 1)
            box = QHBoxLayout(); box.setSpacing(8)
            if isinstance(control, (list, tuple)):
                for c in control:
                    box.addWidget(c) if not isinstance(c, str) else box.addWidget(QLabel(c))
            else:
                box.addWidget(control)
            if isinstance(control, QCheckBox):
                box.addStretch(1)                       # switches sit left-aligned with the other controls
            holder = QWidget(); holder.setFixedWidth(self.CONTROL_W); holder.setLayout(box)
            box.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(holder, 0, Qt.AlignTop if align_top else Qt.AlignVCenter)
            if isinstance(control, QCheckBox):           # clicking the text toggles the switch too
                t.mousePressEvent = lambda _e, c=control: c.toggle()
                control.setAccessibleName(title)
            v.addWidget(r)
            return r

        def check(value: bool) -> QCheckBox:
            c = ToggleSwitch(); c.setChecked(value)
            return c

        # ---------------- display
        v = page("sp_display", "sp_display_intro")
        self.lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(self.lang.findData(settings.language))
        row(v, tr("language"), tr("sd_language"), self.lang)
        self.theme = QComboBox()
        for code, key in (("dark", "theme_dark"), ("light", "theme_light"), ("system", "theme_system")):
            self.theme.addItem(tr(key), code)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        row(v, tr("s_color_scheme"), tr("sd_theme"), self.theme)
        self.notation = QComboBox()
        for n in NOTATIONS:
            self.notation.addItem(tr(f"n_{n}"), n)
        self.notation.setCurrentIndex(self.notation.findData(settings.notation))
        row(v, tr("notation"), tr("sd_notation"), self.notation)
        v.addStretch(1)

        # ---------------- analysis
        v = page("s_analysis", "sp_analysis_intro")
        self.bmin = QDoubleSpinBox(); self.bmin.setRange(30, 300); self.bmin.setDecimals(0); self.bmin.setValue(settings.bpm_min)
        self.bmax = QDoubleSpinBox(); self.bmax.setRange(40, 400); self.bmax.setDecimals(0); self.bmax.setValue(settings.bpm_max)
        row(v, tr("s_bpm_range_short"), tr("s_bpm_range_tip"), [self.bmin, tr("to"), self.bmax, "BPM"])
        self.key_method = QComboBox()
        self.key_method.addItem(tr("key_method_classic"), "classic")
        self.key_method.addItem(tr("key_method_fast"), "fast")
        self.key_method.setCurrentIndex(max(0, self.key_method.findData(settings.key_method)))
        row(v, tr("s_key_method"), tr("sd_key_method"), self.key_method)
        self.workers = QSpinBox(); self.workers.setRange(0, 64); self.workers.setValue(settings.workers)
        self.workers.setSpecialValueText(tr("auto"))
        auto_n = Settings(workers=0).worker_count()
        row(v, tr("s_workers_short"), tr("sd_workers", n=auto_n), self.workers)
        self.use_tags = check(settings.use_existing_tags)
        row(v, tr("s_use_tags_short"), tr("s_use_tags_tip"), self.use_tags)
        self.reanalyze = check(settings.reanalyze_existing)
        row(v, tr("s_reanalyze_short"), tr("sd_reanalyze"), self.reanalyze)
        self.detect_sections = check(settings.detect_sections)
        row(v, tr("s_sections_short"), tr("sd_sections"), self.detect_sections)
        v.addStretch(1)

        # ---------------- files & tags
        v = page("sp_files", "sp_files_intro")
        self.write_tags = check(settings.write_tags)
        row(v, tr("s_write_tags_short"), tr("sd_write_tags"), self.write_tags)
        self.tag_notation = QComboBox()
        for n in NOTATIONS:
            self.tag_notation.addItem(tr(f"n_{n}"), n)
        self.tag_notation.setCurrentIndex(self.tag_notation.findData(settings.tag_key_notation))
        row(v, tr("s_tag_notation"), tr("sd_tag_notation"), self.tag_notation)
        self.write_tags.toggled.connect(self.tag_notation.setEnabled)
        self.tag_notation.setEnabled(settings.write_tags)
        self.write_edits = check(settings.write_edits)
        row(v, tr("s_write_edits_short"), tr("sd_write_edits"), self.write_edits)
        # Auto re-tag: file-name patterns (full width: regular expressions need room)
        from PySide6.QtWidgets import QLineEdit
        prow = QWidget(); prow.setObjectName("settingsRow")
        pv = QVBoxLayout(prow); pv.setContentsMargins(0, 14, 0, 14); pv.setSpacing(6)
        pt = QLabel(tr("s_retag_patterns")); pt.setObjectName("rowTitle")
        pd = QLabel(tr("sd_retag_patterns")); pd.setObjectName("dim"); pd.setWordWrap(True)
        self.patterns = QPlainTextEdit("\n".join(settings.retag_patterns)); self.patterns.setObjectName("codeBlock")
        self.patterns.setLineWrapMode(QPlainTextEdit.NoWrap); self.patterns.setMinimumHeight(96)
        self.patterns_err = QLabel(); self.patterns_err.setObjectName("patternError"); self.patterns_err.setWordWrap(True)
        trow = QHBoxLayout(); trow.setSpacing(8)
        self.pattern_sample = QLineEdit("07 - Mara Veil - Glass Harbour (Extended Mix).flac")
        self.pattern_sample.setPlaceholderText(tr("s_retag_sample"))
        trow.addWidget(QLabel(tr("s_retag_test"))); trow.addWidget(self.pattern_sample, 1)
        self.pattern_result = QLabel(); self.pattern_result.setObjectName("dim"); self.pattern_result.setWordWrap(True)
        self.pattern_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for w_ in (pt, pd, self.patterns, self.patterns_err):
            pv.addWidget(w_)
        pv.addLayout(trow); pv.addWidget(self.pattern_result)
        v.addWidget(prow)
        self.patterns.textChanged.connect(self._check_patterns)
        self.pattern_sample.textChanged.connect(self._check_patterns)
        self._check_patterns()
        v.addStretch(1)

        # ---------------- CSV export
        from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem
        from ..export import COLUMNS, DEFAULT_COLUMNS, valid_columns
        v = page("sp_export", "sp_export_intro")
        crow = QWidget(); crow.setObjectName("settingsRow")
        cv = QVBoxLayout(crow); cv.setContentsMargins(0, 14, 0, 14); cv.setSpacing(6)
        ct = QLabel(tr("s_export_columns")); ct.setObjectName("rowTitle")
        cd = QLabel(tr("sd_export_columns")); cd.setObjectName("dim"); cd.setWordWrap(True)
        self.export_cols = QListWidget(); self.export_cols.setObjectName("exportColumns")
        self.export_cols.setDragDropMode(QAbstractItemView.InternalMove)   # drag to change the order
        self.export_cols.setMinimumHeight(260)

        def fill_columns(chosen):
            self.export_cols.clear()
            for cid in chosen + [c for c in COLUMNS if c not in chosen]:
                it = QListWidgetItem(tr(COLUMNS[cid][1]))
                it.setData(Qt.UserRole, cid)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                it.setCheckState(Qt.Checked if cid in chosen else Qt.Unchecked)
                self.export_cols.addItem(it)
        fill_columns(valid_columns(settings.export_columns))
        reset = QPushButton(tr("s_export_default")); reset.clicked.connect(lambda: fill_columns(list(DEFAULT_COLUMNS)))
        rrow = QHBoxLayout(); rrow.addStretch(1); rrow.addWidget(reset)
        for w_ in (ct, cd, self.export_cols):
            cv.addWidget(w_)
        cv.addLayout(rrow)
        v.addWidget(crow)
        self.export_delim = QComboBox()
        for code, key in (("auto", "s_delim_auto"), ("semicolon", "s_delim_semicolon"), ("comma", "s_delim_comma"),
                          ("tab", "s_delim_tab")):
            self.export_delim.addItem(tr(key), code)
        self.export_delim.setCurrentIndex(max(0, self.export_delim.findData(settings.export_delimiter)))
        row(v, tr("s_export_delimiter"), tr("sd_export_delimiter"), self.export_delim)
        self.export_ask = check(settings.export_ask)
        row(v, tr("s_export_ask"), tr("sd_export_ask"), self.export_ask)
        from PySide6.QtWidgets import QFileDialog, QLineEdit
        self.export_folder = QLineEdit(settings.export_folder); self.export_folder.setPlaceholderText(tr("s_export_documents"))
        pick = QPushButton(tr("s_export_choose"))
        pick.clicked.connect(lambda: (lambda d: d and self.export_folder.setText(d))(
            QFileDialog.getExistingDirectory(self, tr("s_export_folder"), self.export_folder.text())))
        row(v, tr("s_export_folder"), tr("sd_export_folder"), [self.export_folder, pick])
        self.export_name = QLineEdit(settings.export_filename); self.export_name.setPlaceholderText("KeyTool-{date}.csv")
        row(v, tr("s_export_filename"), tr("sd_export_filename"), self.export_name)
        v.addStretch(1)

        # ---------------- MCP
        v = page("s_mcp", "sp_mcp_intro")
        cp = QPushButton(tr("s_copy_config"))
        row(v, tr("s_mcp_stdio"), tr("s_mcp_info"), cp)
        self.snippet = QPlainTextEdit(mcp_config_snippet()); self.snippet.setReadOnly(True)
        self.snippet.setObjectName("codeBlock")
        self.snippet.setMinimumHeight(180)
        v.addWidget(self.snippet)
        cp.clicked.connect(lambda: self._copy(self.snippet.toPlainText(), cp))
        from PySide6.QtWidgets import QLineEdit
        url = QLineEdit("http://127.0.0.1:8000/mcp"); url.setReadOnly(True)
        row(v, tr("s_mcp_http"), tr("sd_mcp_http"), url)
        dbp = str(db_path())
        db_btn = QPushButton(tr("s_open_folder"))
        db_btn.clicked.connect(lambda: self._open_folder(dbp))
        r = row(v, tr("s_database"), tr("sd_database") + "\n" + dbp, db_btn)
        r.findChildren(QLabel)[-1].setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addStretch(1)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # ---------------- footer
        foot = QWidget(); foot.setObjectName("settingsFooter")
        fl = QHBoxLayout(foot); fl.setContentsMargins(24, 12, 24, 12)
        fl.addStretch(1)
        cancel = QPushButton(tr("cancel")); cancel.clicked.connect(self.reject)
        save = QPushButton(tr("save_settings")); save.setObjectName("primary"); save.setDefault(True)
        save.clicked.connect(self.accept)
        fl.addWidget(cancel); fl.addWidget(save)
        root.addWidget(foot)

    def _copy(self, text: str, btn) -> None:
        from PySide6.QtCore import QTimer
        QApplication.clipboard().setText(text)
        old = btn.text()
        btn.setText(tr("copied"))
        QTimer.singleShot(1500, lambda: btn.setText(old))

    @staticmethod
    def _open_folder(path: str) -> None:
        import os
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def export_columns(self) -> list[str]:
        """Ticked columns in the order of the list (dragged into the file order)."""
        from PySide6.QtCore import Qt
        out = [self.export_cols.item(i).data(Qt.UserRole) for i in range(self.export_cols.count())
               if self.export_cols.item(i).checkState() == Qt.Checked]
        return out

    def pattern_lines(self) -> list[str]:
        return [ln for ln in self.patterns.toPlainText().splitlines() if ln.strip()]

    def _check_patterns(self):
        """Show invalid patterns and what the first matching pattern makes of the sample file name."""
        from ..retag import check_patterns, match_patterns
        lines = self.pattern_lines()
        errs = check_patterns(lines)
        self.patterns_err.setText("\n".join(tr("s_retag_bad_line", n=n, e=e) for n, e in errs))
        self.patterns_err.setVisible(bool(errs))
        got = match_patterns(self.pattern_sample.text().strip(), lines) if self.pattern_sample.text().strip() else {}
        names = {"artist": tr("artist"), "title": tr("title"), "album": tr("album"),
                 "album_artist": tr("album_artist"), "year": tr("year"), "genre": tr("genre")}
        self.pattern_result.setText(" · ".join(f"{names[k]}: {v}" for k, v in got.items()) if got
                                    else tr("s_retag_no_match"))

    def apply(self) -> None:
        s = self.s
        s.language = self.lang.currentData()
        s.theme = self.theme.currentData()
        s.notation = self.notation.currentData()
        s.bpm_min, s.bpm_max = sorted((self.bmin.value(), self.bmax.value()))
        if s.bpm_max < s.bpm_min * 2:  # range must span one octave for folding to work
            s.bpm_max = s.bpm_min * 2
        s.workers = self.workers.value()
        s.key_method = self.key_method.currentData()
        s.write_tags = self.write_tags.isChecked()
        s.use_existing_tags = self.use_tags.isChecked()
        s.write_edits = self.write_edits.isChecked()
        s.tag_key_notation = self.tag_notation.currentData()
        s.reanalyze_existing = self.reanalyze.isChecked()
        s.detect_sections = self.detect_sections.isChecked()
        s.retag_patterns = self.pattern_lines()
        s.export_columns = self.export_columns()
        s.export_delimiter = self.export_delim.currentData()
        s.export_ask = self.export_ask.isChecked()
        s.export_folder = self.export_folder.text().strip()
        s.export_filename = self.export_name.text().strip() or "KeyTool-{date}.csv"
        s.save()
