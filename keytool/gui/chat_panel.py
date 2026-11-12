"""Chat sidebar: talk to Claude through Claude Code with the user's Claude plan (see keytool/claude_code.py).

Claude Code runs in a background thread with KeyTool's MCP server; the app_* tools operate this window through
app_control. Tools that change data or files arrive here as confirmation cards (AppControl calls ask_confirm)
and wait for "Run" / "Cancel".
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from .. import claude_code as cc
from ..i18n import tr
from ..keys import Key


class _Bridge(QObject):
    event = Signal(str, object)       # kind, data (see claude_code.Session.run)
    finished = Signal(str)            # "" or an error text


class _Input(QPlainTextEdit):
    """Enter sends, Shift+Enter starts a new line."""
    send = Signal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)


# tools whose use is not worth a note in the chat (looking things up)
QUIET = {"search_tracks", "get_track", "compatible_tracks", "library_stats", "convert_key", "list_all_keys",
         "list_keywords", "suggest_tags", "search_karaoke_version", "state"}


class ChatPanel(QWidget):
    open_settings_requested = Signal()

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.win = window             # MainWindow: settings, library, deck, list
        self.session: cc.Session | None = None
        self._busy = False
        self._cancel = threading.Event()
        self._pending_confirm: tuple | None = None
        self.bridge = _Bridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.finished.connect(self._finished)
        self.setObjectName("chatPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(10)
        head = QHBoxLayout()
        self.title = QLabel(); self.title.setObjectName("chatTitle")
        self.model_lbl = QLabel(); self.model_lbl.setObjectName("dim")
        self.new_btn = QPushButton(); self.new_btn.clicked.connect(self.new_conversation)
        names = QVBoxLayout(); names.setSpacing(0)
        names.addWidget(self.title); names.addWidget(self.model_lbl)
        head.addLayout(names); head.addStretch(1); head.addWidget(self.new_btn, 0, Qt.AlignTop)
        v.addLayout(head)

        self.msgs = QWidget(); self.msgs.setObjectName("chatMessages")
        self.mv = QVBoxLayout(self.msgs); self.mv.setContentsMargins(0, 0, 4, 0); self.mv.setSpacing(12)
        self.mv.addStretch(1)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(self.msgs)
        self.scroll.setFrameShape(QFrame.NoFrame); self.scroll.setObjectName("chatScroll")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            lambda _a, b: self.scroll.verticalScrollBar().setValue(b))       # follow new messages
        v.addWidget(self.scroll, 1)

        self.busy = QLabel(); self.busy.setObjectName("dim"); self.busy.hide()
        v.addWidget(self.busy)
        self.sugg_box = QWidget(); sv = QVBoxLayout(self.sugg_box); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(6)
        self.suggestions: list[QPushButton] = []
        for _ in range(3):
            b = QPushButton(); b.setObjectName("chatSuggestion"); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, b=b: self.send(b.text()))
            self.suggestions.append(b)
            r = QHBoxLayout(); r.addWidget(b); r.addStretch(1); sv.addLayout(r)
        v.addWidget(self.sugg_box)
        row = QHBoxLayout(); row.setSpacing(8)
        self.input = _Input(); self.input.setObjectName("chatInput"); self.input.setFixedHeight(64)
        self.input.send.connect(lambda: self.send())
        self.send_btn = QPushButton(); self.send_btn.setObjectName("primary"); self.send_btn.setFixedHeight(64)
        self.send_btn.clicked.connect(self._send_or_stop)
        row.addWidget(self.input, 1); row.addWidget(self.send_btn)
        v.addLayout(row)
        self.hint = QLabel(); self.hint.setObjectName("chatHint"); self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        self._setup_card: QWidget | None = None
        self.retranslate()

    # ------------------------------------------------------------------ texts / state
    def retranslate(self):
        self.title.setText(tr("chat"))
        self.new_btn.setText(tr("chat_new")); self.new_btn.setToolTip(tr("chat_new_tip"))
        for b, key in zip(self.suggestions, ("chat_s_fit", "chat_s_nokey", "chat_s_peak")):
            b.setText(tr(key))
        self.input.setPlaceholderText(tr("chat_placeholder"))
        self.hint.setText(tr("chat_hint"))
        self._update_running(self._busy)
        self.refresh_setup()

    def running(self) -> bool:
        return self._busy

    def _update_running(self, on: bool):
        self.send_btn.setText(tr("chat_stop") if on else tr("chat_send"))
        self.busy.setText(tr("chat_thinking")); self.busy.setVisible(on)
        for b in self.suggestions:
            b.setEnabled(not on)

    def claude_exe(self) -> str | None:
        return cc.find_claude(getattr(self.win.s, "chat_claude_path", ""))

    def refresh_setup(self):
        """Model in the header; a "set up the chat" card while Claude Code is not found."""
        model = getattr(self.win.s, "chat_model", "")
        self.model_lbl.setText("Claude Code · " + (tr(f"chat_model_{model}") if model else tr("chat_model_default")))
        ready = self.claude_exe() is not None
        if ready and self._setup_card is not None:
            self._setup_card.hide()
            self.mv.removeWidget(self._setup_card)
            self._setup_card.deleteLater(); self._setup_card = None
        elif not ready and self._setup_card is None:
            card = QFrame(); card.setObjectName("chatConfirm")
            cv = QVBoxLayout(card); cv.setContentsMargins(12, 10, 12, 10); cv.setSpacing(6)
            t = QLabel(tr("chat_setup_title")); t.setObjectName("chatCardTitle")
            d = QLabel(tr("chat_setup_text")); d.setObjectName("dim"); d.setWordWrap(True)
            d.setTextFormat(Qt.RichText); d.setOpenExternalLinks(True)
            b = QPushButton(tr("chat_setup_btn")); b.setObjectName("primary")
            b.clicked.connect(self.open_settings_requested.emit)
            br = QHBoxLayout(); br.addStretch(1); br.addWidget(b)
            cv.addWidget(t); cv.addWidget(d); cv.addLayout(br)
            self.mv.insertWidget(self.mv.count() - 1, card)
            self._setup_card = card
        self.input.setEnabled(ready)
        self.send_btn.setEnabled(ready or self._busy)

    # ------------------------------------------------------------------ messages
    def _add(self, w: QWidget, align=None):
        if align is None:
            self.mv.insertWidget(self.mv.count() - 1, w)
            return
        row = QHBoxLayout()
        if align == Qt.AlignRight:
            row.addStretch(1)
        row.addWidget(w)
        if align == Qt.AlignLeft:
            row.addStretch(1)
        self.mv.insertLayout(self.mv.count() - 1, row)

    def add_user(self, text: str):
        self.sugg_box.hide()
        lb = QLabel(text); lb.setObjectName("chatUser"); lb.setWordWrap(True); lb.setMaximumWidth(300)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._add(lb, Qt.AlignRight)

    def add_answer(self, text: str):
        lb = QLabel(); lb.setObjectName("chatAnswer"); lb.setWordWrap(True)
        lb.setTextFormat(Qt.MarkdownText); lb.setText(text)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        lb.setOpenExternalLinks(True)
        self._add(lb)

    def add_chip(self, text: str, ok: bool = True):
        lb = QLabel(("✓  " if ok else "✕  ") + text); lb.setObjectName("chatChip"); lb.setWordWrap(True)
        self._add(lb, Qt.AlignLeft)

    def add_error(self, text: str):
        lb = QLabel(text); lb.setObjectName("chatError"); lb.setWordWrap(True)
        self._add(lb)

    def new_conversation(self):
        if self._busy:
            self._stop()
        self.session = None
        while self.mv.count() > 1:
            it = self.mv.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                _clear_layout(it.layout())
        self._setup_card = None
        self.sugg_box.show()
        self.refresh_setup()

    # ------------------------------------------------------------------ sending
    def _send_or_stop(self):
        if self._busy:
            self._stop()
            return
        self.send()

    def _stop(self):
        self._cancel.set()
        if self.session is not None:
            self.session.stop()
        if self._pending_confirm is not None:
            self._answer_confirm(False)

    def make_session(self, exe: str) -> cc.Session:
        return cc.Session(exe, getattr(self.win.s, "chat_model", ""))

    def send(self, text: str | None = None):
        text = (text if text is not None else self.input.toPlainText()).strip()
        exe = self.claude_exe()
        if not text or self._busy or not exe:
            return
        self.input.clear()
        self.add_user(text)
        if self.session is None or self.session.exe != exe:
            self.session = self.make_session(exe)
        from .app_commands import app_state
        s = self.win.s
        system = cc.SYSTEM.format(notation=s.notation, language=cc.LANGUAGE_NAMES.get(s.language, "English"),
                                  state=json.dumps(app_state(self.win), ensure_ascii=False))
        self._cancel = threading.Event()
        session, cancel = self.session, self._cancel

        def work():
            err = ""
            try:
                session.run(text, system, lambda k, d: self.bridge.event.emit(k, d), cancel)
            except cc.ChatError as e:
                err = str(e)
            except Exception as e:                       # never let the chat thread die silently
                err = f"{type(e).__name__}: {e}"
            self.bridge.finished.emit(err)
        self._busy = True
        threading.Thread(target=work, name="keytool-chat", daemon=True).start()
        self._update_running(True)

    def _finished(self, err: str):
        self._busy = False
        if err and not self._cancel.is_set():
            self.add_error(tr("chat_error", e=err))
        self._update_running(False)
        self.refresh_setup()

    def _on_event(self, kind: str, data: Any):
        if kind == "text":
            self.add_answer(data)
        elif kind == "action":
            name, args, result, is_error = data
            short = cc.short_name(name)
            if short in QUIET:
                return
            declined = "did not confirm" in result or "denied" in result.lower()
            if declined:
                self.add_chip(tr("chat_not_done", what=tool_label(short)), ok=False)
            else:
                self.add_chip(describe(self.win, short, args, short=True), ok=not is_error and '"error"' not in result[:40])

    # ------------------------------------------------------------------ confirmations (from AppControl)
    def ask_confirm(self, tool_name: str, args: dict, answer: Callable[[bool], None]):
        short = cc.short_name(tool_name)
        card = QFrame(); card.setObjectName("chatConfirm")
        cv = QVBoxLayout(card); cv.setContentsMargins(12, 10, 12, 10); cv.setSpacing(6)
        t = QLabel(tr("chat_confirm", what=tool_label(short))); t.setObjectName("chatCardTitle"); t.setWordWrap(True)
        d = QLabel(describe(self.win, short, args)); d.setObjectName("dim"); d.setWordWrap(True)
        br = QHBoxLayout(); br.addStretch(1)
        no = QPushButton(tr("cancel")); yes = QPushButton(tr("chat_run")); yes.setObjectName("primary")
        br.addWidget(no); br.addWidget(yes)
        cv.addWidget(t); cv.addWidget(d); cv.addLayout(br)
        self._add(card)
        if self._pending_confirm is not None:        # only one open question: the older one is declined
            self._answer_confirm(False)
        self._pending_confirm = (card, yes, no, answer)
        yes.clicked.connect(lambda: self._answer_confirm(True))
        no.clicked.connect(lambda: self._answer_confirm(False))
        yes.setFocus()

    def _answer_confirm(self, ok: bool):
        if self._pending_confirm is None:
            return
        card, yes, no, answer = self._pending_confirm
        self._pending_confirm = None
        yes.setEnabled(False); no.setEnabled(False)
        (yes if ok else no).setText(tr("chat_confirmed") if ok else tr("chat_declined"))
        answer(ok)


def _clear_layout(layout):
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            _clear_layout(it.layout())


# ============================================================================ describing tool calls
def tool_label(name: str) -> str:
    key = f"chat_t_{name}"
    text = tr(key)
    return name.replace("_", " ") if text == key else text


def _titles(win, ids) -> str:
    ids = [int(i) for i in (ids if isinstance(ids, list) else [ids]) if str(i).lstrip("-").isdigit()]
    rows = {t["id"]: t for t in win.lib.db.get_tracks(ids[:6])}
    names = [f"{rows[i].get('title') or rows[i].get('filename')}" for i in ids[:6] if i in rows]
    more = len(ids) - len(names)
    return ", ".join(names) + (f" {tr('chat_more', n=more)}" if more > 0 else "")


def _fmt_key(win, value) -> str:
    k = Key.parse(str(value))
    return k.format(win.s.notation) if k else str(value)


def describe(win, name: str, args: dict, short: bool = False) -> str:
    """Readable summary of a tool call: the label, then the arguments (track ids as titles)."""
    parts = []
    for k, v in (args or {}).items():
        if v in (None, "", [], False) or k in ("notation", "write_tags", "wait_seconds", "with_details",
                                                "keep_other_filters"):
            continue
        if k in ("track_ids", "track_id"):
            parts.append(_titles(win, v))
        elif k == "keys" and isinstance(v, list):
            parts.append(tr("key") + " " + " · ".join(_fmt_key(win, x) for x in v))
        elif k in ("bpm_min", "bpm_max"):
            continue
        elif isinstance(v, list):
            parts.append(", ".join(str(x) for x in v))
        elif isinstance(v, dict):
            continue
        else:
            parts.append(str(v) if short and k in ("text", "action", "section") else f"{k.replace('_', ' ')}: {v}")
    lo, hi = (args or {}).get("bpm_min"), (args or {}).get("bpm_max")
    if lo or hi:
        parts.append(f"{int(lo) if lo else '…'}–{int(hi) if hi else '…'} BPM")
    text = ", ".join(p for p in parts if p)
    return f"{tool_label(name)}: {text}" if text else tool_label(name)
