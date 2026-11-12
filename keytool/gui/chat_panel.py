"""Chat sidebar: talk to an AI model that searches the library and operates KeyTool (see keytool/assistant.py).

The model runs in a background thread. App tools (filter, select, deck ...) run on the GUI thread through a
bridge; tools that change data or files show a confirmation card and wait for "Run" / "Cancel".
"""
from __future__ import annotations

import json
import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from .. import assistant as ai
from ..i18n import tr
from ..keys import Key


class _Bridge(QObject):
    """Signals from the chat thread to the GUI thread (queued: the receiver lives in the GUI thread)."""
    ui_call = Signal(object)          # (callable, done Event, holder dict)
    event = Signal(str, object)       # kind, data (see Conversation.run)
    confirm = Signal(object)          # (Tool, args, done Event, holder dict)
    finished = Signal(str)            # "" or an error text


class _Input(QPlainTextEdit):
    """Enter sends, Shift+Enter starts a new line."""
    send = Signal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)


class ChatPanel(QWidget):
    open_settings_requested = Signal()
    library_changed = Signal()        # a tool changed the library: list and deck should refresh

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.win = window             # MainWindow: library, settings, filters, list, deck
        self.conv = ai.Conversation()
        self._tools: list[ai.Tool] | None = None
        self._thread: threading.Thread | None = None
        self._busy = False                # from send() until _finished() ran on the GUI thread
        self._cancel = threading.Event()
        self._pending_confirm: tuple | None = None
        self.bridge = _Bridge()
        self.bridge.ui_call.connect(self._run_ui_call)
        self.bridge.event.connect(self._on_event)
        self.bridge.confirm.connect(self._ask_confirm)
        self.bridge.finished.connect(self._finished)
        self.setObjectName("chatPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(10)
        head = QHBoxLayout()
        self.title = QLabel(); self.title.setObjectName("chatTitle")
        self.model_lbl = QLabel(); self.model_lbl.setObjectName("dim")
        self.new_btn = QPushButton(); self.new_btn.clicked.connect(self.new_conversation)
        names = QVBoxLayout(); names.setSpacing(0)
        names.addWidget(self.title); names.addWidget(self.model_lbl)          # model name under the title
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
        # a few example requests while the conversation is empty
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
        self._update_running(self.running())
        self.refresh_setup()

    def running(self) -> bool:
        return self._busy

    def _update_running(self, on: bool):
        self.send_btn.setText(tr("chat_stop") if on else tr("chat_send"))
        self.busy.setText(tr("chat_thinking")); self.busy.setVisible(on)
        for b in self.suggestions:
            b.setEnabled(not on)

    def provider_ready(self) -> bool:
        s = self.win.s
        if s.chat_provider == "openai":
            return bool(s.chat_model)
        return bool(ai.get_api_key("anthropic"))

    def refresh_setup(self):
        """Model name in the header; a "set up the chat" card while there is no key / model."""
        s = self.win.s
        name = s.chat_model or ai.DEFAULT_MODEL.get(s.chat_provider, "")
        self.model_lbl.setText(ai.model_title(name) if name else "–")
        self.model_lbl.setToolTip(name)
        ready = self.provider_ready()
        if ready and self._setup_card is not None:
            self._setup_card.hide()                  # at once, not only when the deletion happens
            self.mv.removeWidget(self._setup_card)
            self._setup_card.deleteLater(); self._setup_card = None
        elif not ready and self._setup_card is None:
            card = QFrame(); card.setObjectName("chatConfirm")
            cv = QVBoxLayout(card); cv.setContentsMargins(12, 10, 12, 10); cv.setSpacing(6)
            t = QLabel(tr("chat_setup_title")); t.setObjectName("chatCardTitle")
            d = QLabel(tr("chat_setup_text")); d.setObjectName("dim"); d.setWordWrap(True)
            b = QPushButton(tr("chat_setup_btn")); b.setObjectName("primary")
            b.clicked.connect(self.open_settings_requested.emit)
            br = QHBoxLayout(); br.addStretch(1); br.addWidget(b)
            cv.addWidget(t); cv.addWidget(d); cv.addLayout(br)
            self.mv.insertWidget(self.mv.count() - 1, card)
            self._setup_card = card
        self.input.setEnabled(ready)
        self.send_btn.setEnabled(ready or self.running())

    # ------------------------------------------------------------------ messages
    def _add(self, w: QWidget, align=None):
        if align is None:
            self.mv.insertWidget(self.mv.count() - 1, w)
        else:
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
        if self.running():
            self._cancel.set()
        self.conv = ai.Conversation()
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
        if self.running():
            self._cancel.set()
            if self._pending_confirm is not None:
                self._answer_confirm(False)
            return
        self.send()

    def send(self, text: str | None = None):
        text = (text if text is not None else self.input.toPlainText()).strip()
        if not text or self.running() or not self.provider_ready():
            return
        self.input.clear()
        self.add_user(text)
        if self._tools is None:
            self._tools = ai.mcp_tools(self.win.lib) + app_tools(self.win)
        s = self.win.s
        system = ai.SYSTEM.format(notation=s.notation, language=ai.LANGUAGE_NAMES.get(s.language, "English"),
                                  state=json.dumps(app_state(self.win), ensure_ascii=False))
        provider = ai.make_provider(s)
        self._cancel = threading.Event()
        tools = self._tools

        def work():
            err = ""
            try:
                self.conv.run(text, provider, tools, system=system, confirm=self._confirm_blocking,
                              run_ui=self._ui_blocking, on_event=lambda k, d: self.bridge.event.emit(k, d),
                              cancel=self._cancel)
            except ai.ChatError as e:
                err = str(e)
            except Exception as e:                       # never let the chat thread die silently
                err = f"{type(e).__name__}: {e}"
            self.bridge.finished.emit(err)
        self._busy = True
        self._thread = threading.Thread(target=work, name="keytool-chat", daemon=True)
        self._thread.start()
        self._update_running(True)

    def _finished(self, err: str):
        self._thread = None
        self._busy = False
        if err:
            self.add_error(tr("chat_error", e=err))
        self._update_running(False)
        self.refresh_setup()

    # chat thread -> GUI thread, waiting for the result
    def _ui_blocking(self, tool: ai.Tool, args: dict) -> str:
        done, holder = threading.Event(), {}
        self.bridge.ui_call.emit((lambda: ai.run_tool(tool, args), done, holder))
        done.wait()
        return holder.get("result", "")

    def _run_ui_call(self, payload):
        fn, done, holder = payload
        try:
            holder["result"] = fn()
        finally:
            done.set()

    def _confirm_blocking(self, tool: ai.Tool, args: dict) -> bool:
        done, holder = threading.Event(), {}
        self.bridge.confirm.emit((tool, args, done, holder))
        while not done.wait(0.2):
            if self._cancel.is_set():
                return False
        return bool(holder.get("ok"))

    def _ask_confirm(self, payload):
        tool, args, done, holder = payload
        card = QFrame(); card.setObjectName("chatConfirm")
        cv = QVBoxLayout(card); cv.setContentsMargins(12, 10, 12, 10); cv.setSpacing(6)
        t = QLabel(tr("chat_confirm", what=tool_label(tool.name))); t.setObjectName("chatCardTitle"); t.setWordWrap(True)
        d = QLabel(describe(self.win, tool.name, args)); d.setObjectName("dim"); d.setWordWrap(True)
        br = QHBoxLayout(); br.addStretch(1)
        no = QPushButton(tr("cancel")); yes = QPushButton(tr("chat_run")); yes.setObjectName("primary")
        br.addWidget(no); br.addWidget(yes)
        cv.addWidget(t); cv.addWidget(d); cv.addLayout(br)
        self._add(card)
        self._pending_confirm = (card, yes, no, done, holder)
        yes.clicked.connect(lambda: self._answer_confirm(True))
        no.clicked.connect(lambda: self._answer_confirm(False))
        yes.setFocus()

    def _answer_confirm(self, ok: bool):
        if self._pending_confirm is None:
            return
        card, yes, no, done, holder = self._pending_confirm
        self._pending_confirm = None
        holder["ok"] = ok
        yes.setEnabled(False); no.setEnabled(False)
        (yes if ok else no).setText(tr("chat_confirmed") if ok else tr("chat_declined"))
        done.set()

    def _on_event(self, kind: str, data: Any):
        if kind == "text":
            self.add_answer(data)
        elif kind == "action":
            tool, args, result = data
            failed = '"error"' in result[:40]
            if not tool.name.startswith("get_") and tool.name not in READ_ONLY_QUIET:
                self.add_chip(describe(self.win, tool.name, args, short=True), ok=not failed)
            if tool.changes_library and not failed:
                self.library_changed.emit()
        elif kind == "declined":
            tool, args = data
            self.add_chip(tr("chat_not_done", what=tool_label(tool.name)), ok=False)


def _clear_layout(layout):
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            _clear_layout(it.layout())


# ============================================================================ describing tool calls
READ_ONLY_QUIET = {"search_tracks", "compatible_tracks", "library_stats", "convert_key", "list_all_keys",
                   "list_keywords", "suggest_tags", "search_karaoke_version"}


def tool_label(name: str) -> str:
    key = f"chat_t_{name}"
    text = tr(key)
    return name if text == key else text


def _titles(win, ids) -> str:
    ids = [int(i) for i in (ids if isinstance(ids, list) else [ids]) if str(i).lstrip("-").isdigit()]
    rows = {t["id"]: t for t in win.lib.db.get_tracks(ids[:6])}
    names = [f"{rows[i].get('title') or rows[i].get('filename')}" for i in ids[:6] if i in rows]
    more = len(ids) - len(names)
    return ", ".join(names) + (f" {tr('chat_more', n=more)}" if more > 0 else "")


def describe(win, name: str, args: dict, short: bool = False) -> str:
    """Readable summary of a tool call: the label, then the arguments (track ids as titles)."""
    parts = []
    for k, v in (args or {}).items():
        if v in (None, "", [], False) or k in ("notation", "write_tags", "wait_seconds", "with_details"):
            continue
        if k in ("track_ids", "track_id"):
            parts.append(_titles(win, v))
        elif k == "keys" and isinstance(v, list):
            parts.append(tr("key") + " " + " · ".join(_fmt_key(win, x) for x in v))
        elif k in ("bpm_min", "bpm_max"):
            continue
        elif isinstance(v, list):
            parts.append(", ".join(str(x) for x in v))
        else:
            parts.append(f"{k.replace('_', ' ')}: {v}" if not short or k not in ("text", "keywords") else str(v))
    lo, hi = (args or {}).get("bpm_min"), (args or {}).get("bpm_max")
    if lo or hi:
        parts.append(f"{int(lo) if lo else '…'}–{int(hi) if hi else '…'} BPM")
    text = ", ".join(p for p in parts if p)
    return f"{tool_label(name)}: {text}" if text else tool_label(name)


def _fmt_key(win, value) -> str:
    k = Key.parse(str(value))
    return k.format(win.s.notation) if k else str(value)


# ============================================================================ app tools
def app_state(win) -> dict:
    """What the user sees: deck, selection, filters, number of visible tracks (for the system prompt and the
    get_app_state tool)."""
    q = win.build_query()
    deck = win.player.row
    k = Key.from_code(deck.get("key")) if deck else None
    sel = win.selected_ids()
    filters = {f: getattr(q, f) for f in ("text", "title", "artist", "album", "year_from", "year_to", "bpm_min",
                                          "bpm_max", "keywords") if getattr(q, f)}
    if q.keys:
        filters["keys"] = [Key.from_code(c).format(win.s.notation) for c in q.keys if Key.from_code(c)]
    return {
        "deck": {"id": deck["id"], "title": deck.get("title"), "artist": deck.get("artist"),
                 "key": k.format(win.s.notation) if k else None, "bpm": deck.get("bpm"),
                 "playing": win.player.is_playing(), "transposed": getattr(win.player, "semitones", 0)}
        if deck else None,
        "selected_track_ids": sel[:50], "selected_count": len(sel),
        "visible_tracks": win.proxy.rowCount(), "library_tracks": win.lib.db.count(), "filters": filters,
    }


def _visible(win, limit: int = 25) -> list[dict]:
    from .widgets import ID_ROLE
    out = []
    by_id = {r["id"]: r for r in win.model.rows}
    for r in range(min(win.proxy.rowCount(), limit)):
        t = by_id.get(win.proxy.index(r, 0).data(ID_ROLE))
        if t:
            k = Key.from_code(t.get("key"))
            out.append({"id": t["id"], "title": t.get("title"), "artist": t.get("artist"),
                        "key": k.format(win.s.notation) if k else None, "bpm": t.get("bpm")})
    return out


def app_tools(win) -> list[ai.Tool]:
    """Tools that operate the app; they run on the GUI thread."""
    def obj(props: dict, required: list | None = None) -> dict:
        return {"type": "object", "properties": props, **({"required": required} if required else {})}
    ids = {"type": "array", "items": {"type": "integer"}}

    def get_app_state():
        return app_state(win)

    def set_filter(text=None, title=None, artist=None, album=None, year_from=None, year_to=None, bpm_min=None,
                   bpm_max=None, keys=None, keywords=None, keywords_mode=None, keep_other_filters=False):
        if not keep_other_filters:
            win.reset_filters()
        for w, val in ((win.f_text, text), (win.f_title, title), (win.f_artist, artist), (win.f_album, album)):
            if val is not None:
                w.setText(str(val))
        for w, val in ((win.f_year_from, year_from), (win.f_year_to, year_to), (win.f_bpm_from, bpm_min),
                       (win.f_bpm_to, bpm_max)):
            if val is not None:
                w.setValue(float(val))
        unknown = []
        if keys is not None:
            codes = []
            for t in keys:
                k = Key.parse(str(t))
                (codes.append(k.code) if k else unknown.append(t))
            win.f_keys.set_selected(codes)
        if keywords is not None:
            win.f_keywords.set_selected(list(keywords))
        if keywords_mode in ("any", "all") and hasattr(win.f_keywords, "mode"):
            win.f_keywords.mode.setCurrentIndex(max(0, win.f_keywords.mode.findData(keywords_mode)))
        win.refresh()
        return {"visible_tracks": win.proxy.rowCount(), "first": _visible(win), "unknown_keys": unknown or None}

    def clear_filters():
        win.reset_filters()
        return {"visible_tracks": win.proxy.rowCount()}

    def select_tracks(track_ids):
        from PySide6.QtCore import QItemSelection, QItemSelectionModel

        from .widgets import ID_ROLE
        want = {int(i) for i in track_ids}
        sel = QItemSelection()
        found = []
        for r in range(win.proxy.rowCount()):
            idx = win.proxy.index(r, 0)
            if idx.data(ID_ROLE) in want:
                sel.select(idx, idx)
                found.append(idx)
        sm = win.table.selectionModel()
        sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        if found:                                   # current row without touching the selection
            sm.setCurrentIndex(found[0], QItemSelectionModel.NoUpdate); win.table.scrollTo(found[0])
        missing = sorted(want - {i.data(ID_ROLE) for i in found})
        return {"selected": len(found), "not_in_the_filtered_list": missing or None}

    def load_into_deck(track_id, play=False):
        if not win.lib.db.get_track(int(track_id)):
            return {"error": f"no track {track_id}"}
        win.load_into_deck(autoplay=bool(play), track_id=int(track_id))
        return {"deck": app_state(win)["deck"]}

    def playback(action):
        p = win.player
        acts = {"play": p.play, "pause": lambda: p.is_playing() and p.toggle(), "toggle": p.toggle, "stop": p.stop,
                "next": lambda: win.play_adjacent(1, p.is_playing()), "previous": p.previous}
        if action not in acts:
            return {"error": f"action must be one of {list(acts)}"}
        acts[action]()
        return {"ok": True}

    def transpose(semitones):
        st = int(semitones)
        if st not in (-1, 0, 1):
            return {"error": "semitones must be -1, 0 or 1"}
        win.player.set_semitones(st)
        return {"transposed": win.player.semitones}

    def jump_in_deck(cue=None, section=None, seconds=None):
        p = win.player
        if not p.row:
            return {"error": "no track in the deck"}
        if cue is not None:
            c = next((c for c in p.wave.cues if c.index + 1 == int(cue)), None)
            if not c:
                return {"error": f"no cue {cue}", "cues": len(p.wave.cues)}
            p.seek_ms(c.position_ms)
        elif section is not None:
            from ..section_edit import display_name
            secs = p.wave.sections
            hit = None
            if str(section).isdigit() and 0 < int(section) <= len(secs):
                hit = secs[int(section) - 1]
            else:
                low = str(section).lower()
                hit = next((s for s in secs if low in (s["label"], display_name(s).lower(),
                                                       display_name(s, tr).lower())), None)
            if not hit:
                return {"error": f"no section {section}",
                        "sections": [display_name(s) for s in secs]}
            p.seek_ms(int(hit["start"] * 1000))
        elif seconds is not None:
            p.seek_ms(int(float(seconds) * 1000))
        else:
            return {"error": "give cue, section or seconds"}
        return {"ok": True}

    def add_files(paths):
        win.start_import([str(x) for x in paths])
        return {"started": True, "note": "files are scanned and analysed in the background (analysis queue)"}

    def reanalyze(track_ids):
        win.enqueue([int(i) for i in track_ids], keep_tag_values=False)
        return {"queued": len(track_ids)}

    def add_cue(seconds):
        p = win.player
        if not p.row or not p.wave.editable:
            return {"error": "no track in the deck"}
        before = len(p.wave.cues)
        p.wave.add_cue_at(float(seconds))
        return {"cues": len(p.wave.cues), "added": len(p.wave.cues) > before}

    def delete_cue(number):
        p = win.player
        if not p.row or not any(c.index + 1 == int(number) for c in p.wave.cues):
            return {"error": f"no cue {number}"}
        p.wave.delete_cue(int(number) - 1)
        return {"cues": len(p.wave.cues)}

    T = ai.Tool
    return [
        T("get_app_state", "What the user sees now: deck track, selected tracks, active filters, visible count.",
          obj({}), get_app_state, ui=True),
        T("set_filter", "Filter the track list in the app (the user sees the result). keys in any notation "
          "(e.g. 8A, Am); bpm/year ranges; keywords with keywords_mode any|all. Other filters are reset unless "
          "keep_other_filters is true. Returns the number and the first visible tracks.",
          obj({"text": {"type": "string"}, "title": {"type": "string"}, "artist": {"type": "string"},
               "album": {"type": "string"}, "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
               "bpm_min": {"type": "number"}, "bpm_max": {"type": "number"},
               "keys": {"type": "array", "items": {"type": "string"}},
               "keywords": {"type": "array", "items": {"type": "string"}},
               "keywords_mode": {"type": "string", "enum": ["any", "all"]},
               "keep_other_filters": {"type": "boolean"}}), set_filter, ui=True),
        T("clear_filters", "Remove all filters from the track list.", obj({}), clear_filters, ui=True),
        T("select_tracks", "Select tracks in the list (only tracks the current filter shows can be selected).",
          obj({"track_ids": ids}, ["track_ids"]), select_tracks, ui=True),
        T("load_into_deck", "Load a track into the deck, optionally start playing.",
          obj({"track_id": {"type": "integer"}, "play": {"type": "boolean"}}, ["track_id"]), load_into_deck, ui=True),
        T("playback", "Control the deck: play, pause, toggle, stop, next or previous track.",
          obj({"action": {"type": "string", "enum": ["play", "pause", "toggle", "stop", "next", "previous"]}},
              ["action"]), playback, ui=True),
        T("transpose", "Play the deck track 1 semitone lower (-1), higher (1) or in the original key (0).",
          obj({"semitones": {"type": "integer", "enum": [-1, 0, 1]}}, ["semitones"]), transpose, ui=True),
        T("jump_in_deck", "Jump to a cue point (number), a song section (name like chorus/drop or its number) "
          "or a time in seconds in the deck track.",
          obj({"cue": {"type": "integer"}, "section": {"type": "string"}, "seconds": {"type": "number"}}),
          jump_in_deck, ui=True),
        T("add_files", "Import audio files or folders into the library and analyse them.",
          obj({"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"]), add_files,
          confirm=True, ui=True),
        T("reanalyze", "Analyse tracks again (tempo, key, song sections); values from tags are re-detected too.",
          obj({"track_ids": ids}, ["track_ids"]), reanalyze, confirm=True, ui=True),
        T("add_cue", "Set a Serato cue point in the deck track at a time in seconds (written into the file; "
          "cues are numbered by position).", obj({"seconds": {"type": "number"}}, ["seconds"]), add_cue,
          confirm=True, ui=True),
        T("delete_cue", "Delete cue point number N of the deck track (written into the file).",
          obj({"number": {"type": "integer"}}, ["number"]), delete_cue, confirm=True, ui=True),
    ]
