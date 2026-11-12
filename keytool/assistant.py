"""AI chat for KeyTool: a language model that uses the same tools as the MCP server plus tools that drive the app.

Providers (Settings.chat_provider):
  * "anthropic" – Claude through the Anthropic Messages API (https://api.anthropic.com/v1/messages)
  * "openai"    – any OpenAI-compatible Chat Completions endpoint (OpenAI, or local: Ollama, LM Studio ...)
Both are called with plain HTTPS requests (httpx); no vendor SDK needed.

API keys are kept in the operating system's credential store (Windows Credential Manager) through `keyring`,
never in settings.json.

Tools: every MCP tool of mcp_server.py (minus the job tools, the app has its own analysis queue) plus the app
tools the GUI registers (filter, select, deck ...). Tools that change data or files need a confirmation from the
user before they run (CONFIRM); reading and operating the app runs straight away.

This module has no Qt dependency: the GUI passes callbacks for confirmations, events and running app tools on
its own thread, the tests pass fakes.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

KEYRING_SERVICE = "KeyTool"
PROVIDERS = ("anthropic", "openai")
ANTHROPIC_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")
DEFAULT_MODEL = {"anthropic": "claude-sonnet-5-5", "openai": ""}       # OpenAI-compatible: the user names it
DEFAULT_OPENAI_URL = "http://localhost:11434/v1"         # Ollama; OpenAI: https://api.openai.com/v1
MAX_STEPS = 16                       # tool rounds per message
MAX_RESULT_CHARS = 24000             # a tool result longer than this is cut for the model
TIMEOUT = 120.0

# MCP tools the chat does not offer (the app has its own analysis queue; see the app tools add_files/reanalyze)
# and no export of the whole library: that is MCP-only by design (the app exports the selection, Ctrl+E)
SKIP_MCP = {"analyze_paths", "reanalyze_tracks", "get_job_status", "cancel_job", "export_csv"}
# tools that change the library, tags or files: the user confirms them first
CONFIRM = {"import_mixed_in_key", "add_keywords", "remove_keywords", "set_keywords", "rename_keyword", "set_bpm",
           "set_key", "update_metadata", "write_tags", "remove_tracks", "remove_missing_files", "export_csv",
           "add_files", "reanalyze", "add_cue", "delete_cue"}


def model_title(model: str) -> str:
    """"claude-sonnet-5-5" -> "Claude Sonnet 5.5"; other names stay as they are."""
    import re
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?", model or "")
    return f"Claude {m.group(1).capitalize()} {m.group(2)}.{m.group(3)}" if m else (model or "")


class ChatError(Exception):
    """A provider answered with an error (shown in the chat)."""


# ============================================================================ API keys
def get_api_key(provider: str) -> str:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, provider) or ""
    except Exception:
        return ""


def set_api_key(provider: str, key: str) -> None:
    """Store (or with an empty key: delete) the API key in the OS credential store. Raises if there is none."""
    import keyring
    if key:
        keyring.set_password(KEYRING_SERVICE, provider, key)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, provider)
        except Exception:
            pass


# ============================================================================ tools
@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[..., Any]
    confirm: bool = False
    ui: bool = False                  # runs on the GUI thread (app tools)
    changes_library: bool = False     # the list / deck should refresh afterwards

    def needs_confirmation(self, args: dict) -> bool:
        if self.name == "import_mixed_in_key" and args.get("dry_run"):
            return False
        return self.confirm


def _clean_schema(schema: dict) -> dict:
    """MCP (pydantic) schemas -> plain JSON schema both providers accept."""
    s = json.loads(json.dumps(schema or {"type": "object", "properties": {}}))
    s.pop("title", None)
    for p in s.get("properties", {}).values():
        p.pop("title", None)
    s.setdefault("type", "object")
    s.setdefault("properties", {})
    return s


def mcp_tools(library=None) -> list[Tool]:
    """The MCP server's tools, running against `library` (the GUI's library) instead of a separate one."""
    import asyncio

    from . import mcp_server as ms
    if library is not None:
        ms._lib = library
        ms.lib = lambda: library              # the app's own library and settings, not a second instance
    listed = asyncio.run(ms.mcp.list_tools())
    out = []
    for t in listed:
        if t.name in SKIP_MCP:
            continue
        fn = getattr(ms, t.name)
        out.append(Tool(t.name, (t.description or "").strip(), _clean_schema(t.input_schema), fn,
                        confirm=t.name in CONFIRM, changes_library=t.name in CONFIRM))
    return out


def run_tool(tool: Tool, args: dict) -> str:
    """Call a tool and return its result as text (JSON) for the model."""
    try:
        res = tool.fn(**(args or {}))
    except TypeError as e:
        return json.dumps({"error": f"bad arguments: {e}"})
    except Exception as e:                    # the model sees the error and can try again
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
    text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"... [cut, {len(text)} characters in total – ask for less]"
    return text


# ============================================================================ conversation history
# neutral history entries: {"role": "user", "text"} | {"role": "assistant", "text", "calls": [Call]}
#                          | {"role": "tool", "id", "name", "result"}
@dataclass
class Call:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str = ""
    calls: list[Call] = field(default_factory=list)


# ============================================================================ providers
class Provider:
    name = ""

    def __init__(self, model: str, api_key: str = "", base_url: str = "", http=None):
        self.model, self.api_key, self.base_url = model, api_key, base_url
        self._http = http                       # httpx.Client (tests: with a MockTransport)

    def http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=TIMEOUT)
        return self._http

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        try:
            r = self.http().post(url, headers=headers, json=body)
        except Exception as e:
            raise ChatError(f"{type(e).__name__}: {e}") from e
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code >= 400:
            err = data.get("error") if isinstance(data, dict) else None
            msg = (err.get("message") if isinstance(err, dict) else err) or r.text[:300]
            raise ChatError(f"HTTP {r.status_code}: {msg}")
        return data

    def chat(self, system: str, history: list[dict], tools: list[Tool]) -> Reply:
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def messages(self, history: list[dict]) -> list[dict]:
        out: list[dict] = []
        for h in history:
            if h["role"] == "user":
                out.append({"role": "user", "content": h["text"]})
            elif h["role"] == "assistant":
                content = ([{"type": "text", "text": h["text"]}] if h.get("text") else []) + [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in h.get("calls", [])]
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": "…"}]})
            else:                                # tool results of one round go into one user message
                block = {"type": "tool_result", "tool_use_id": h["id"], "content": h["result"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def chat(self, system, history, tools):
        if not self.api_key:
            raise ChatError("no API key")
        body = {"model": self.model, "max_tokens": 4096, "system": system, "messages": self.messages(history),
                "tools": [{"name": t.name, "description": t.description[:1024], "input_schema": t.schema}
                          for t in tools]}
        data = self._post(self.URL, {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                     "content-type": "application/json"}, body)
        reply = Reply()
        for block in data.get("content", []):
            if block.get("type") == "text":
                reply.text += block.get("text", "")
            elif block.get("type") == "tool_use":
                reply.calls.append(Call(block["id"], block["name"], block.get("input") or {}))
        return reply


class OpenAIProvider(Provider):
    name = "openai"

    def messages(self, system: str, history: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for h in history:
            if h["role"] == "user":
                out.append({"role": "user", "content": h["text"]})
            elif h["role"] == "assistant":
                m: dict = {"role": "assistant", "content": h.get("text") or ""}
                if h.get("calls"):
                    m["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                                       for c in h["calls"]]
                out.append(m)
            else:
                out.append({"role": "tool", "tool_call_id": h["id"], "content": h["result"]})
        return out

    def chat(self, system, history, tools):
        url = (self.base_url or DEFAULT_OPENAI_URL).rstrip("/") + "/chat/completions"
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "messages": self.messages(system, history),
                "tools": [{"type": "function", "function": {"name": t.name, "description": t.description[:1024],
                                                            "parameters": t.schema}} for t in tools]}
        data = self._post(url, headers, body)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise ChatError(f"unexpected answer: {str(data)[:300]}") from e
        reply = Reply(text=msg.get("content") or "")
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            reply.calls.append(Call(tc.get("id") or f"call_{i}", fn.get("name", ""), args))
        return reply


def make_provider(settings, http=None) -> Provider:
    p = getattr(settings, "chat_provider", "anthropic")
    model = getattr(settings, "chat_model", "") or DEFAULT_MODEL.get(p, "")
    if p == "openai":
        return OpenAIProvider(model, get_api_key("openai"), getattr(settings, "chat_base_url", ""), http)
    return AnthropicProvider(model, get_api_key("anthropic"), "", http)


# ============================================================================ the conversation
SYSTEM = """You are the assistant inside KeyTool, a Windows app for DJs that manages a local music library with
detected BPM and musical key (notation shown to the user: {notation}), keywords, Serato cue points and song sections.
Use the tools to look things up and to operate the app: filter and select tracks in the list, load them into the
deck, play, transpose, jump to cues or sections. Prefer acting over explaining how to do it by hand.
Track ids come from the tools; never invent them. Tools that change data or files are confirmed by the user in the
app before they run: just call them, and if a call is declined, accept it and do not repeat it unasked.
Keep answers short (a few sentences, short lists); refer to tracks as "Title" (Artist). Answer in {language}.
Current app state: {state}"""

LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish", "fr": "French", "it": "Italian"}


class Conversation:
    """One chat: history plus the loop model -> tools -> model until it answers without tool calls."""

    def __init__(self):
        self.history: list[dict] = []

    def clear(self):
        self.history.clear()

    def run(self, text: str, provider: Provider, tools: list[Tool], *, system: str,
            confirm: Callable[[Tool, dict], bool], run_ui: Callable[[Tool, dict], str] | None = None,
            on_event: Callable[[str, Any], None] = lambda kind, data: None,
            cancel: threading.Event | None = None) -> str:
        """Send `text` and work until the model gives its answer (returned, also as on_event("text", ...)).
        on_event kinds: "action" (Tool, args, result) after a tool ran, "declined" (Tool, args)."""
        by_name = {t.name: t for t in tools}
        self.history.append({"role": "user", "text": text})
        answer = ""
        for _ in range(MAX_STEPS):
            if cancel is not None and cancel.is_set():
                break
            reply = provider.chat(system, self.history, tools)
            self.history.append({"role": "assistant", "text": reply.text, "calls": reply.calls})
            if reply.text:
                answer = reply.text
                on_event("text", reply.text)
            if not reply.calls:
                return answer
            for call in reply.calls:
                tool = by_name.get(call.name)
                if tool is None:
                    result = json.dumps({"error": f"unknown tool {call.name}"})
                elif cancel is not None and cancel.is_set():
                    result = json.dumps({"error": "stopped by the user"})
                elif tool.needs_confirmation(call.args) and not confirm(tool, call.args):
                    result = json.dumps({"declined": "the user did not confirm this action"})
                    on_event("declined", (tool, call.args))
                else:
                    result = run_ui(tool, call.args) if (tool.ui and run_ui) else run_tool(tool, call.args)
                    on_event("action", (tool, call.args, result))
                self.history.append({"role": "tool", "id": call.id, "name": call.name, "result": result})
        return answer
