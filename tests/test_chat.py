"""AI chat: providers (request/response format), the tool loop with confirmations, MCP + app tools, the sidebar."""
import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

httpx = pytest.importorskip("httpx")

from keytool import assistant as ai  # noqa: E402

from test_core import _write_wav, make_track  # noqa: E402


# --------------------------------------------------------------------------- keyring stand-in
class MemoryKeyring:
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture
def keys(monkeypatch):
    kr = pytest.importorskip("keyring")
    mem = MemoryKeyring()
    monkeypatch.setattr(kr, "get_password", mem.get_password)
    monkeypatch.setattr(kr, "set_password", mem.set_password)
    monkeypatch.setattr(kr, "delete_password", mem.delete_password)
    return mem


def test_api_keys_go_to_the_credential_store(keys, tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    ai.set_api_key("anthropic", "sk-test")
    assert ai.get_api_key("anthropic") == "sk-test" and keys.store[("KeyTool", "anthropic")] == "sk-test"
    from keytool.config import Settings
    Settings().save()
    assert "sk-test" not in (tmp_path / "settings.json").read_text()
    ai.set_api_key("anthropic", "")
    assert ai.get_api_key("anthropic") == ""


# --------------------------------------------------------------------------- providers
def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


TOOL = ai.Tool("search_tracks", "Search", {"type": "object", "properties": {"text": {"type": "string"}}}, lambda **k: k)
HISTORY = [{"role": "user", "text": "find house"},
           {"role": "assistant", "text": "Looking.", "calls": [ai.Call("t1", "search_tracks", {"text": "house"}),
                                                              ai.Call("t2", "search_tracks", {"text": "deep"})]},
           {"role": "tool", "id": "t1", "name": "search_tracks", "result": "[1]"},
           {"role": "tool", "id": "t2", "name": "search_tracks", "result": "[2]"}]


def test_anthropic_request_and_reply():
    seen = {}

    def handler(req):
        seen["headers"], seen["body"] = req.headers, json.loads(req.content)
        return httpx.Response(200, json={"content": [
            {"type": "text", "text": "Filtering now."},
            {"type": "tool_use", "id": "t3", "name": "set_filter", "input": {"keys": ["8A"]}}]})
    p = ai.AnthropicProvider("claude-sonnet-5-5", "sk-x", http=_client(handler))
    reply = p.chat("SYS", HISTORY, [TOOL])
    assert reply.text == "Filtering now." and reply.calls[0].name == "set_filter" and reply.calls[0].args == {"keys": ["8A"]}
    assert seen["headers"]["x-api-key"] == "sk-x" and seen["headers"]["anthropic-version"]
    b = seen["body"]
    assert b["model"] == "claude-sonnet-5-5" and b["system"] == "SYS" and b["tools"][0]["input_schema"]["type"] == "object"
    msgs = b["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [c["type"] for c in msgs[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [c["tool_use_id"] for c in msgs[2]["content"]] == ["t1", "t2"]             # one message per round


def test_anthropic_errors():
    p = ai.AnthropicProvider("m", "sk", http=_client(lambda r: httpx.Response(
        401, json={"error": {"type": "authentication_error", "message": "invalid x-api-key"}})))
    with pytest.raises(ai.ChatError, match="401.*invalid x-api-key"):
        p.chat("s", [{"role": "user", "text": "hi"}], [])
    with pytest.raises(ai.ChatError, match="no API key"):
        ai.AnthropicProvider("m", "").chat("s", [], [])


def test_openai_compatible_request_and_reply():
    seen = {}

    def handler(req):
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "playback", "arguments": '{"action": "play"}'}}]}}]})
    p = ai.OpenAIProvider("llama3.1", "", "http://localhost:11434/v1/", http=_client(handler))
    reply = p.chat("SYS", HISTORY, [TOOL])
    assert reply.calls[0].name == "playback" and reply.calls[0].args == {"action": "play"} and reply.text == ""
    assert seen["url"] == "http://localhost:11434/v1/chat/completions" and "authorization" not in seen["headers"]
    msgs = seen["body"]["messages"]
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"text": "house"}'
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "[1]"}
    assert seen["body"]["tools"][0]["function"]["parameters"]["type"] == "object"


# --------------------------------------------------------------------------- the loop
class Scripted(ai.Provider):
    """Replies one after another; records what it was sent."""

    def __init__(self, replies):
        super().__init__("fake")
        self.replies, self.sent = list(replies), []

    def chat(self, system, history, tools):
        self.sent.append([dict(h) for h in history])
        return self.replies.pop(0) if self.replies else ai.Reply("done")


def test_loop_runs_tools_and_asks_before_changes():
    done = []
    read = ai.Tool("search_tracks", "", {}, lambda text="": [{"id": 1, "title": text}])
    write = ai.Tool("add_keywords", "", {}, lambda track_ids, keywords: done.append((track_ids, keywords)) or {"ok": 1},
                    confirm=True, changes_library=True)
    prov = Scripted([
        ai.Reply("", [ai.Call("a", "search_tracks", {"text": "x"})]),
        ai.Reply("Adding.", [ai.Call("b", "add_keywords", {"track_ids": [1], "keywords": ["warm"]})]),
        ai.Reply("", [ai.Call("c", "add_keywords", {"track_ids": [1], "keywords": ["peak"]}),
                      ai.Call("d", "nope", {})]),
        ai.Reply("All done."),
    ])
    answers = iter([True, False])
    events = []
    conv = ai.Conversation()
    out = conv.run("tag it", prov, [read, write], system="S", confirm=lambda t, a: next(answers),
                   on_event=lambda k, d: events.append(k))
    assert out == "All done." and done == [([1], ["warm"])]                         # second one declined
    results = [h for h in conv.history if h["role"] == "tool"]
    assert json.loads(results[0]["result"])[0]["title"] == "x"
    assert "declined" in results[2]["result"] and "unknown tool" in results[3]["result"]
    assert events == ["action", "text", "action", "declined", "text"]


def test_loop_stops_and_cuts_long_results():
    import threading
    big = ai.Tool("search_tracks", "", {}, lambda: "x" * (ai.MAX_RESULT_CHARS + 50))
    assert ai.run_tool(big, {}).endswith("ask for less]")
    assert "bad arguments" in ai.run_tool(big, {"wrong": 1})
    stop = threading.Event(); stop.set()
    prov = Scripted([ai.Reply("", [ai.Call("a", "search_tracks", {})])])
    assert ai.Conversation().run("x", prov, [big], system="", confirm=lambda *a: True, cancel=stop) == ""
    assert prov.sent == []                                                          # stopped before asking


# --------------------------------------------------------------------------- MCP tools against the app library
@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path / "data"))
    from keytool.config import Settings
    from keytool.db import Database, Query
    from keytool.keys import Key
    from keytool.library import Library
    lib = Library(Database(tmp_path / "lib.sqlite3"), Settings(write_tags=False, language="en"))
    for n, cam, bpm in (("a", "8A", 124.0), ("b", "9A", 122.0), ("c", "3B", 128.0)):
        p = tmp_path / f"{n}.wav"
        _write_wav(p, make_track(120, 9, True, seconds=2))
    lib.scan([str(tmp_path / f"{n}.wav") for n in "abc"])
    for t, (cam, bpm) in zip(sorted(lib.search(Query()), key=lambda t: t["filename"]),
                             (("8A", 124.0), ("9A", 122.0), ("3B", 128.0))):
        lib.db.update_track(t["id"], key=Key.parse(cam).code, bpm=bpm, status="ok", title=t["filename"][0].upper())
    return lib


def test_mcp_tools_use_the_app_library(lib):
    pytest.importorskip("mcp")
    tools = {t.name: t for t in ai.mcp_tools(lib)}
    assert "search_tracks" in tools and "add_keywords" in tools
    assert not {"export_csv", "analyze_paths", "reanalyze_tracks"} & set(tools)        # whole-library export: MCP only
    assert tools["add_keywords"].confirm and not tools["search_tracks"].confirm
    assert not tools["import_mixed_in_key"].needs_confirmation({"dry_run": True})
    res = json.loads(ai.run_tool(tools["search_tracks"], {"keys": ["8A"]}))
    assert [t["title"] for t in res["tracks"]] == ["A"]


# --------------------------------------------------------------------------- the sidebar, end to end
def _window(lib, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import keytool.gui.player as pl
    monkeypatch.setattr(pl, "HAVE_MULTIMEDIA", False)
    from keytool.gui.main_window import MainWindow
    return MainWindow(lib)


def _close(w):
    w._cue_cancel.set()
    w.player.shutdown(); w.queue.shutdown(); w.deleteLater()


def _wait(panel, until, seconds=15):
    from PySide6.QtWidgets import QApplication
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if until():
            return True
        time.sleep(0.02)
    return False


def test_sidebar_filters_selects_and_confirms(lib, keys, monkeypatch):
    pytest.importorskip("mcp")
    from keytool.db import Query
    ids = {t["title"]: t["id"] for t in lib.search(Query())}
    w = _window(lib, monkeypatch)
    try:
        ai.set_api_key("anthropic", "sk-test")
        w.a_chat.setChecked(True)
        assert w.chat.isVisibleTo(w) and w.chat.provider_ready() and w.chat._setup_card is None
        prov = Scripted([
            ai.Reply("", [ai.Call("1", "set_filter", {"keys": ["8A", "9A"], "bpm_min": 120, "bpm_max": 126})]),
            ai.Reply("", [ai.Call("3", "load_into_deck", {"track_id": ids["B"]}),
                          ai.Call("2", "select_tracks", {"track_ids": [ids["A"], ids["B"]]})]),
            ai.Reply("", [ai.Call("4", "add_keywords", {"track_ids": [ids["A"], ids["B"]], "keywords": ["Warm-up"]})]),
            ai.Reply("Tagged both."),
        ])
        monkeypatch.setattr(ai, "make_provider", lambda s, http=None: prov)
        w.chat.send("tag what fits")
        assert _wait(w.chat, lambda: w.chat._pending_confirm is not None)
        assert w.proxy.rowCount() == 2 and sorted(w.selected_ids()) == sorted([ids["A"], ids["B"]])
        assert w.player.row["id"] == ids["B"]
        assert not lib.db.keywords_for([ids["A"]]).get(ids["A"])                     # not before the yes
        w.chat._answer_confirm(True)
        assert _wait(w.chat, lambda: not w.chat.running())
        assert lib.db.keywords_for([ids["A"]])[ids["A"]] == ["Warm-up"]
        texts = [lb.text() for lb in w.chat.msgs.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
        assert any("Filter" in t and "8A" in t for t in texts) and any("Tagged both." in t for t in texts)
        system = prov.sent[0][0]
        assert system["text"] == "tag what fits"
        w.chat.new_conversation()
        assert w.chat.conv.history == []
    finally:
        _close(w)


def test_sidebar_without_key_shows_setup(lib, keys, monkeypatch):
    w = _window(lib, monkeypatch)
    try:
        w.s.chat_provider = "anthropic"
        w.chat.refresh_setup()
        assert w.chat._setup_card is not None and not w.chat.input.isEnabled()
        w.s.chat_provider, w.s.chat_model = "openai", "llama3.1"                       # local: no key needed
        w.chat.refresh_setup()
        assert w.chat._setup_card is None and w.chat.input.isEnabled()
    finally:
        _close(w)


def test_settings_page_stores_key_in_credential_store(tmp_path, keys, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("KEYTOOL_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from keytool.config import Settings
    from keytool.gui.settings_dialog import SettingsDialog
    s = Settings()
    d = SettingsDialog(s)
    d.show_page("chat")
    assert d.pages.currentIndex() == d.page_index["sp_chat"]
    assert d.chat_model.currentText() == "claude-sonnet-5-5" and not d.chat_url.isEnabled()
    d.chat_key.setText("sk-new")
    d.apply()
    assert ai.get_api_key("anthropic") == "sk-new" and "sk-new" not in json.dumps(s.__dict__)
    d2 = SettingsDialog(s)
    d2.chat_provider.setCurrentIndex(d2.chat_provider.findData("openai"))
    assert d2.chat_url.isEnabled() and d2.chat_model.currentText() == ""
    d2.chat_model.setEditText("llama3.1"); d2.chat_url.setText("http://localhost:1234/v1")
    d2.apply()
    assert (s.chat_provider, s.chat_model, s.chat_base_url) == ("openai", "llama3.1", "http://localhost:1234/v1")
    d3 = SettingsDialog(s)
    d3.chat_provider.setCurrentIndex(d3.chat_provider.findData("anthropic"))
    assert d3.chat_key_del.isEnabled()
    d3.chat_key_del.click()
    d3.apply()
    assert ai.get_api_key("anthropic") == ""
    for x in (d, d2, d3):
        x.deleteLater()
