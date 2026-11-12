"""Chat with Claude through Claude Code (no API key) and the remote control of the running app.

A fake `claude` program (a small Python script) stands in for Claude Code: it prints stream-json like the real one
and records its arguments, so the chat can be tested without an account or network.
"""
import json
import os
import stat
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from keytool import app_control
from keytool import claude_code as cc

from test_core import _write_wav, make_track


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    os.makedirs(tmp_path / "data", exist_ok=True)
    return tmp_path


# --------------------------------------------------------------------------- control server / client
def test_control_roundtrip_and_security(data):
    seen = []
    srv = app_control.ControlServer(lambda cmd, args: seen.append((cmd, args)) or {"ok": cmd}).start()
    try:
        with open(app_control.control_file()) as f:
            info = json.load(f)
        assert info["port"] == srv.port and len(info["token"]) > 20
        assert app_control.call("state") == {"ok": "state"}
        assert app_control.call("set_filter", {"keys": ["8A"]}) == {"ok": "set_filter"}
        assert seen[-1] == ("set_filter", {"keys": ["8A"]})
        with pytest.raises(RuntimeError, match="unknown command"):
            app_control.call("format_disk")
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{srv.port}/", data=b"{}", method="POST",
                                     headers={"X-KeyTool-Token": "wrong"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 403
    finally:
        srv.stop()
    assert not os.path.exists(app_control.control_file())
    with pytest.raises(app_control.AppNotRunning):
        app_control.call("state")


def test_mcp_app_tools_forward_to_the_app(data):
    pytest.importorskip("mcp")
    import keytool.mcp_server as ms
    assert "not open" in ms.app_state()["error"]                                  # no window: a clear error
    seen = []

    def dispatch(cmd, args):
        seen.append((cmd, args))
        return {"allow": args["tool_name"].endswith("add_keywords")} if cmd == "confirm" else {"done": cmd}
    srv = app_control.ControlServer(dispatch).start()
    try:
        assert ms.app_set_filter(keys=["8A"], bpm_min=120) == {"done": "set_filter"}
        assert seen[-1] == ("set_filter", {"keys": ["8A"], "bpm_min": 120, "keep_other_filters": False})
        assert ms.app_load_into_deck(7, play=True) == {"done": "load_into_deck"}
        ok = json.loads(ms.app_confirm_permission("mcp__keytool__add_keywords", {"track_ids": [1]}))
        assert ok == {"behavior": "allow", "updatedInput": {"track_ids": [1]}}
        no = json.loads(ms.app_confirm_permission("mcp__keytool__remove_tracks", {"track_ids": [1]}))
        assert no["behavior"] == "deny"
    finally:
        srv.stop()


def test_changing_tools_tell_the_app_to_refresh(data):
    pytest.importorskip("mcp")
    import keytool.mcp_server as ms
    from keytool.config import Settings
    from keytool.db import Database
    from keytool.library import Library
    lib = Library(Database(data / "lib.sqlite3"), Settings(write_tags=False))
    p = data / "a.wav"
    _write_wav(p, make_track(120, 9, True, seconds=2))
    lib.scan([str(p)])
    ms._lib = lib
    orig = ms.lib
    ms.lib = lambda: lib
    seen = []
    srv = app_control.ControlServer(lambda c, a: seen.append(c) or {"ok": True}).start()
    try:
        ms.add_keywords([1], ["Warm-up"])
        assert seen == ["library_changed"]
        ms.search_tracks(text="a")                                                 # reading: no refresh
        assert seen == ["library_changed"]
    finally:
        srv.stop()
        ms.lib = orig


# --------------------------------------------------------------------------- Claude Code runner
FAKE = r'''
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_CLAUDE_LOG")
prompt = sys.stdin.read()
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "prompt": prompt, "api_key": os.environ.get("ANTHROPIC_API_KEY")}) + "\n")
if "--version" in args:
    print("2.9.0 (Claude Code)"); sys.exit(0)
if "json" in args and "stream-json" not in args:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "OK"})); sys.exit(0)
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
out = lambda d: print(json.dumps(d), flush=True)
out({"type": "system", "subtype": "init", "session_id": "sess-1"})
if mode == "error":
    out({"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login"})
    sys.exit(1)
out({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Filtering."},
    {"type": "tool_use", "id": "t1", "name": "mcp__keytool__app_set_filter", "input": {"keys": ["8A"], "bpm_min": 120, "bpm_max": 126}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
     "content": [{"type": "text", "text": "{\"visible_tracks\": 2}"}]}]}})
out({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t2", "name": "mcp__keytool__add_keywords", "input": {"track_ids": [1], "keywords": ["x"]}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True,
     "content": "The user did not confirm this action."}]}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done: 2 tracks fit."}]}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: 2 tracks fit.", "session_id": "sess-1"})
'''


@pytest.fixture
def fake_claude(data, monkeypatch):
    script = data / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")
    if sys.platform == "win32":
        exe = data / "claude.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = data / "claude"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = data / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    return str(exe), log


def _log(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]


def test_session_runs_claude_code_on_the_plan(fake_claude):
    exe, log = fake_claude
    assert cc.find_claude(exe) == exe
    s = cc.Session(exe, "sonnet")
    events = []
    answer = s.run("what fits?", "SYSTEM", lambda k, d: events.append((k, d)))
    assert answer == "Done: 2 tracks fit." and s.session_id == "sess-1"
    assert events[0] == ("text", "Filtering.")
    assert events[1][0] == "action" and events[1][1][0] == "mcp__keytool__app_set_filter"
    assert events[1][1][2] == '{"visible_tracks": 2}' and events[1][1][3] is False
    assert events[2][1][3] is True and "did not confirm" in events[2][1][2]
    call = _log(log)[0]
    a = call["args"]
    assert call["prompt"] == "what fits?" and call["api_key"] is None                # the plan, not the API
    assert a[a.index("--permission-prompt-tool") + 1] == "mcp__keytool__app_confirm_permission"
    allowed = a[a.index("--allowedTools") + 1].split(",")
    assert "mcp__keytool__app_set_filter" in allowed and "mcp__keytool__add_keywords" not in allowed
    blocked = a[a.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Write", "mcp__keytool__export_csv"} <= set(blocked)
    assert "--strict-mcp-config" in a and a[a.index("--model") + 1] == "sonnet" and "--resume" not in a
    with open(a[a.index("--mcp-config") + 1]) as f:
        cfg = json.load(f)
    assert "keytool" in cfg["mcpServers"] and cfg["mcpServers"]["keytool"]["env"]["KEYTOOL_DB"]
    s.run("and now?", "SYSTEM", lambda *_: None)
    a2 = _log(log)[1]["args"]
    assert a2[a2.index("--resume") + 1] == "sess-1"                                   # same conversation


def test_session_errors_and_check(fake_claude, monkeypatch):
    exe, _ = fake_claude
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    with pytest.raises(cc.ChatError, match="login"):
        cc.Session(exe).run("hi", "S", lambda *_: None)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    assert cc.check(exe).startswith("2.9.0")
    assert cc.short_name("mcp__keytool__app_set_filter") == "set_filter"
    assert cc.short_name("mcp__keytool__add_keywords") == "add_keywords"
    assert "ANTHROPIC_API_KEY" not in cc.plan_env()


# --------------------------------------------------------------------------- the window
@pytest.fixture
def win(data, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.config import Settings
    from keytool.db import Database, Query
    from keytool.gui.main_window import MainWindow
    from keytool.keys import Key
    from keytool.library import Library
    lib = Library(Database(data / "lib.sqlite3"), Settings(write_tags=False, language="en"))
    for n in "abc":
        _write_wav(data / f"{n}.wav", make_track(120, 9, True, seconds=2))
    lib.scan([str(data / f"{n}.wav") for n in "abc"])
    for t, (cam, bpm) in zip(sorted(lib.search(Query()), key=lambda t: t["filename"]),
                             (("8A", 124.0), ("9A", 122.0), ("3B", 128.0))):
        lib.db.update_track(t["id"], key=Key.parse(cam).code, bpm=bpm, status="ok", title=t["filename"][0].upper())
    w = MainWindow(lib)
    w.start_app_control()
    yield w
    w.app_control.stop()
    w._cue_cancel.set()
    w.player.shutdown(); w.queue.shutdown(); w.deleteLater()


def _in_thread(fn):
    """Call fn in a thread while the GUI keeps processing events (the app answers on the GUI thread)."""
    from PySide6.QtWidgets import QApplication
    box = {}
    t = threading.Thread(target=lambda: box.__setitem__("r", fn()), daemon=True)
    t.start()
    end = time.time() + 20
    while t.is_alive() and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return box.get("r")


def _wait(until, seconds=20):
    from PySide6.QtWidgets import QApplication
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return False


def test_claude_operates_the_open_window(win):
    pytest.importorskip("mcp")
    import keytool.mcp_server as ms
    from keytool.db import Query
    ids = {t["title"]: t["id"] for t in win.lib.search(Query())}
    res = _in_thread(lambda: ms.app_set_filter(keys=["8A", "9A"], bpm_min=120, bpm_max=126))
    assert res["visible_tracks"] == 2 and win.proxy.rowCount() == 2
    assert {t["title"] for t in res["first"]} == {"A", "B"}
    assert _in_thread(lambda: ms.app_select_tracks([ids["A"], ids["B"]]))["selected"] == 2
    assert sorted(win.selected_ids()) == sorted([ids["A"], ids["B"]])
    _in_thread(lambda: ms.app_load_into_deck(ids["B"]))
    assert win.player.row["id"] == ids["B"]
    state = _in_thread(ms.app_state)
    assert state["deck"]["title"] == "B" and sorted(state["filters"]["keys"]) == ["8A", "9A"]
    assert _in_thread(ms.app_clear_filters)["visible_tracks"] == 3
    assert "error" in _in_thread(lambda: ms.app_playback("dance"))


def test_confirmation_card_answers_claude_code(win):
    pytest.importorskip("mcp")
    import keytool.mcp_server as ms
    box = {}
    t = threading.Thread(target=lambda: box.__setitem__(
        "r", ms.app_confirm_permission("mcp__keytool__add_keywords", {"track_ids": [1], "keywords": ["Peak"]})),
        daemon=True)
    t.start()
    assert _wait(lambda: win.chat._pending_confirm is not None)
    assert win.chat.isVisibleTo(win)                                                  # the sidebar opens
    win.chat._answer_confirm(True)
    assert _wait(lambda: "r" in box)
    assert json.loads(box["r"])["behavior"] == "allow"


def test_sidebar_chat_with_fake_claude(win, fake_claude):
    exe, log = fake_claude
    win.s.chat_claude_path = exe
    win.a_chat.setChecked(True)
    win.chat.refresh_setup()
    assert win.chat._setup_card is None and win.chat.input.isEnabled()
    win.chat.send("what fits?")
    assert _wait(lambda: not win.chat.running())
    from PySide6.QtWidgets import QLabel
    texts = [lb.text() for lb in win.chat.msgs.findChildren(QLabel)]
    assert any("Done: 2 tracks fit." in t for t in texts)
    assert any("Filter" in t and "8A" in t and "120–126 BPM" in t for t in texts)        # note for the filter
    assert any("Not done" in t for t in texts)                                       # declined change
    assert "KeyTool" in _log(log)[0]["args"][_log(log)[0]["args"].index("--append-system-prompt") + 1]
    win.chat.new_conversation()
    assert win.chat.session is None


def test_sidebar_without_claude_code_shows_setup(win, monkeypatch):
    monkeypatch.setattr(cc, "find_claude", lambda configured="": None)
    win.chat.refresh_setup()
    assert win.chat._setup_card is not None and not win.chat.input.isEnabled()


def test_settings_page(data, fake_claude):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.gui.settings_dialog import SettingsDialog
    exe, _ = fake_claude
    s = Settings()
    d = SettingsDialog(s)
    d.show_page("chat")
    assert d.pages.currentIndex() == d.page_index["sp_chat"]
    d.chat_path.setText(exe)
    d.chat_model.setCurrentIndex(d.chat_model.findData("opus"))
    d._test_chat()
    assert "2.9.0" in d.chat_test_result.text()
    d.apply()
    assert s.chat_claude_path == exe and s.chat_model == "opus"
    d.deleteLater()
