"""Dialog: import key, tempo, energy and collections from Mixed In Key (MIKStore.db)."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..i18n import tr
from ..mik_import import ImportPlan, default_db_paths, plan_import, read_mik
from .settings_dialog import ToggleSwitch


class MikBridge(QObject):
    """Delivers the result of the background import (ImportResult, error) into the GUI thread."""
    done = Signal(object)


class MikImportDialog(QDialog):
    """Pick the Mixed In Key library, preview what matches, choose options. The caller runs the import."""

    def __init__(self, lib, parent=None, db_file: str | None = None):
        super().__init__(parent)
        self.lib = lib
        self.plan: ImportPlan | None = None
        self.db_file = db_file or next(iter(default_db_paths()), "")
        self.setWindowTitle(tr("mik_title"))
        self.setObjectName("settingsDialog")
        self.setMinimumWidth(720)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        page = QWidget(); page.setObjectName("settingsPage")
        v = QVBoxLayout(page); v.setContentsMargins(32, 26, 32, 22); v.setSpacing(0)
        h = QLabel(tr("mik_title")); h.setObjectName("settingsTitle")
        intro = QLabel(tr("mik_intro")); intro.setObjectName("dim"); intro.setWordWrap(True)
        v.addWidget(h); v.addSpacing(4); v.addWidget(intro); v.addSpacing(16)

        # library file
        self.file_lbl = QLabel(); self.file_lbl.setObjectName("dim"); self.file_lbl.setWordWrap(True)
        self.file_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        browse = QPushButton(tr("mik_browse")); browse.clicked.connect(self._browse)
        self._row(v, tr("mik_file"), self.file_lbl, browse)

        # preview
        self.summary = QLabel(); self.summary.setObjectName("rowTitle"); self.summary.setWordWrap(True)
        self.details = QLabel(); self.details.setObjectName("dim"); self.details.setWordWrap(True)
        box = QWidget(); box.setObjectName("settingsRow")
        bl = QVBoxLayout(box); bl.setContentsMargins(0, 14, 0, 14); bl.setSpacing(4)
        bl.addWidget(self.summary); bl.addWidget(self.details)
        v.addWidget(box)

        # options
        self.overwrite = ToggleSwitch(); self.overwrite.setChecked(True)
        self._row(v, tr("mik_overwrite"), self._dim(tr("mik_overwrite_desc")), self.overwrite)
        self.collections = ToggleSwitch(); self.collections.setChecked(True)
        self.coll_desc = self._dim("")
        self._row(v, tr("mik_collections"), self.coll_desc, self.collections)
        self.add_missing = ToggleSwitch(); self.add_missing.setChecked(False)
        self.add_desc = self._dim("")
        self.add_row = self._row(v, tr("mik_add_missing"), self.add_desc, self.add_missing)
        v.addStretch(1)
        root.addWidget(page, 1)

        foot = QWidget(); foot.setObjectName("settingsFooter")
        fl = QHBoxLayout(foot); fl.setContentsMargins(24, 12, 24, 12); fl.addStretch(1)
        cancel = QPushButton(tr("cancel")); cancel.clicked.connect(self.reject)
        self.start = QPushButton(tr("mik_start")); self.start.setObjectName("primary"); self.start.setDefault(True)
        self.start.clicked.connect(self.accept)
        fl.addWidget(cancel); fl.addWidget(self.start)
        root.addWidget(foot)
        self._load()

    # ------------------------------------------------------------------ layout helpers
    @staticmethod
    def _dim(text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("dim"); lbl.setWordWrap(True)
        return lbl

    @staticmethod
    def _row(v, title: str, desc: QLabel, control) -> QWidget:
        r = QWidget(); r.setObjectName("settingsRow")
        hl = QHBoxLayout(r); hl.setContentsMargins(0, 14, 0, 14); hl.setSpacing(28)
        txt = QVBoxLayout(); txt.setSpacing(3)
        t = QLabel(title); t.setObjectName("rowTitle")
        txt.addWidget(t); txt.addWidget(desc)
        hl.addLayout(txt, 1)
        hl.addWidget(control, 0, Qt.AlignVCenter)
        if isinstance(control, ToggleSwitch):
            t.mousePressEvent = lambda _e, c=control: c.toggle()
            control.setAccessibleName(title)
        v.addWidget(r)
        return r

    # ------------------------------------------------------------------ data
    def _browse(self):
        start = os.path.dirname(self.db_file) if self.db_file else os.path.expanduser("~")
        f, _ = QFileDialog.getOpenFileName(self, tr("mik_title"), start, tr("mik_filter") + " (*.db);;* (*)")
        if f:
            self.db_file = f
            self._load()

    def _load(self):
        self.plan = None
        self.file_lbl.setText(self.db_file or tr("mik_not_found"))
        if not self.db_file or not os.path.exists(self.db_file):
            self._show_error(tr("mik_not_found"))
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            songs = read_mik(self.db_file)
            self.plan = plan_import(self.lib.db, songs)
        except Exception as e:
            self._show_error(tr("mik_error", e=f"{type(e).__name__}: {e}"))
            return
        finally:
            QApplication.restoreOverrideCursor()
        p = self.plan
        self.summary.setText(tr("mik_summary", total=len(p.songs), matched=len(p.matched)))
        lines = []
        if p.by_filename:
            lines.append(tr("mik_by_name", n=p.by_filename))
        if p.missing_existing:
            lines.append(tr("mik_missing", n=len(p.missing_existing)))
        if p.gone:
            lines.append(tr("mik_gone", n=p.gone))
        self.details.setText("\n".join(lines))
        self.details.setVisible(bool(lines))
        names = sorted({c for s in p.songs for c in s.collections}, key=str.lower)
        self.coll_desc.setText(tr("mik_collections_desc", names=", ".join(names)) if names
                               else tr("mik_collections_none"))
        self.collections.setEnabled(bool(names))
        self.collections.setChecked(bool(names))
        self.add_desc.setText(tr("mik_add_missing_desc", n=len(p.missing_existing)))
        self.add_row.setVisible(bool(p.missing_existing))
        self.start.setEnabled(bool(p.matched or p.missing_existing))

    def _show_error(self, text: str):
        self.summary.setText(text)
        self.details.setVisible(False)
        self.start.setEnabled(False)

    def options(self) -> dict:
        return dict(add_missing=self.add_missing.isChecked() and self.add_row.isVisible(),
                    collections_as_keywords=self.collections.isChecked(),
                    overwrite=self.overwrite.isChecked())
