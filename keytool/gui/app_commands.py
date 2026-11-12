"""What Claude can do in the open app (see app_control.py): the handlers behind the MCP tools app_*.

Every handler runs on the GUI thread (AppControl hands the call over) and returns a JSON-able dict.
"""
from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..keys import Key


def app_state(win) -> dict:
    """What the user sees: deck, selection, filters, number of visible tracks."""
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
        "notation": win.s.notation,
    }


def _visible(win, limit: int = 25) -> list[dict]:
    from .widgets import ID_ROLE
    by_id = {r["id"]: r for r in win.model.rows}
    out = []
    for r in range(min(win.proxy.rowCount(), limit)):
        t = by_id.get(win.proxy.index(r, 0).data(ID_ROLE))
        if t:
            k = Key.from_code(t.get("key"))
            out.append({"id": t["id"], "title": t.get("title"), "artist": t.get("artist"),
                        "key": k.format(win.s.notation) if k else None, "bpm": t.get("bpm")})
    return out


def handlers(win) -> dict:
    def state():
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
        sel, found = QItemSelection(), []
        for r in range(win.proxy.rowCount()):
            idx = win.proxy.index(r, 0)
            if idx.data(ID_ROLE) in want:
                sel.select(idx, idx)
                found.append(idx)
        sm = win.table.selectionModel()
        sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        if found:                                   # current row without touching the selection
            sm.setCurrentIndex(found[0], QItemSelectionModel.NoUpdate)
            win.table.scrollTo(found[0])
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
        from ..section_edit import display_name
        from ..i18n import tr
        p = win.player
        if not p.row:
            return {"error": "no track in the deck"}
        if cue is not None:
            c = next((c for c in p.wave.cues if c.index + 1 == int(cue)), None)
            if not c:
                return {"error": f"no cue {cue}", "cues": len(p.wave.cues)}
            p.seek_ms(c.position_ms)
        elif section is not None:
            secs = p.wave.sections
            if str(section).isdigit() and 0 < int(section) <= len(secs):
                hit = secs[int(section) - 1]
            else:
                low = str(section).lower()
                hit = next((s for s in secs if low in (s["label"], display_name(s).lower(),
                                                       display_name(s, tr).lower())), None)
            if not hit:
                return {"error": f"no section {section}", "sections": [display_name(s) for s in secs]}
            p.seek_ms(int(hit["start"] * 1000))
        elif seconds is not None:
            p.seek_ms(int(float(seconds) * 1000))
        else:
            return {"error": "give cue, section or seconds"}
        return {"ok": True}

    def add_files(paths):
        win.start_import([str(x) for x in paths])
        return {"started": True, "note": "files are scanned and analysed in the app's analysis queue"}

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

    def library_changed():
        win.refresh()
        win.sync_deck()
        return {"ok": True}

    return {"state": state, "set_filter": set_filter, "clear_filters": clear_filters, "select_tracks": select_tracks,
            "load_into_deck": load_into_deck, "playback": playback, "transpose": transpose,
            "jump_in_deck": jump_in_deck, "add_files": add_files, "reanalyze": reanalyze, "add_cue": add_cue,
            "delete_cue": delete_cue, "library_changed": library_changed}


class AppControl(QObject):
    """Runs control commands on the GUI thread; "confirm" shows a card in the chat sidebar and waits there."""
    _run = Signal(object)          # (callable, done Event, holder)
    _confirm = Signal(object)      # (tool name, input, done Event, holder)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.handlers = handlers(win)
        self._run.connect(self._on_run)
        self._confirm.connect(self._on_confirm)
        self.server = None

    def start(self):
        from ..app_control import ControlServer
        try:
            self.server = ControlServer(self.dispatch).start()
        except OSError:
            self.server = None                      # no local socket: Claude cannot control the app
        return self

    def stop(self):
        if self.server is not None:
            self.server.stop()
            self.server = None

    # called on the server thread
    def dispatch(self, command: str, args: dict) -> Any:
        done, holder = threading.Event(), {}
        if command == "confirm":
            self._confirm.emit((args.get("tool_name", ""), args.get("input") or {}, done, holder))
            done.wait(3600)
            return {"allow": bool(holder.get("ok"))}
        fn = self.handlers[command]
        self._run.emit((lambda: fn(**args), done, holder))
        if not done.wait(60):
            return {"error": "the app is busy"}
        if "error" in holder:
            raise RuntimeError(holder["error"])
        return holder.get("result")

    def _on_run(self, payload):
        fn, done, holder = payload
        try:
            holder["result"] = fn()
        except TypeError as e:
            holder["error"] = f"bad arguments: {e}"
        except Exception as e:
            holder["error"] = f"{type(e).__name__}: {e}"
        finally:
            done.set()

    def _on_confirm(self, payload):
        name, args, done, holder = payload
        chat = getattr(self.win, "chat", None)
        if chat is None:
            holder["ok"] = False
            done.set()
            return
        if not chat.isVisible():
            self.win.a_chat.setChecked(True)
        chat.ask_confirm(name, args, lambda ok: (holder.__setitem__("ok", ok), done.set()))
