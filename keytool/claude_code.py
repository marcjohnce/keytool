"""The chat sidebar's engine: Claude Code (the `claude` program) in non-interactive mode, signed in with the
user's Claude plan (Pro/Max) – no API key.

Each message runs `claude -p` once, continuing the conversation with --resume. Claude Code gets only KeyTool's
MCP server (--mcp-config/--strict-mcp-config) and none of its own tools (files, shell, web). Read-only tools and
the app tools that operate the window run without asking (--allowedTools); every other KeyTool tool goes through
--permission-prompt-tool, which shows a confirmation card in the sidebar (app_control "confirm").
ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are removed from the environment so the plan is used, not the API.

The output is read as stream-json: system/init (session id), assistant messages (text, tool calls), user
messages (tool results) and the final result.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Callable

from .config import data_dir, db_path

SERVER = "keytool"
PREFIX = f"mcp__{SERVER}__"
# run without asking: reading and operating the app
ALLOWED = ("search_tracks", "get_track", "compatible_tracks", "library_stats", "convert_key", "list_all_keys",
           "list_keywords", "suggest_tags", "search_karaoke_version", "app_state", "app_set_filter",
           "app_clear_filters", "app_select_tracks", "app_load_into_deck", "app_playback", "app_transpose",
           "app_jump_in_deck")
# never in the chat: whole-library export stays an MCP-only feature, the job tools are replaced by the app's queue
BLOCKED_MCP = ("export_csv", "analyze_paths", "reanalyze_tracks", "get_job_status", "cancel_job",
               "app_confirm_permission")
# Claude Code's own tools: not needed for KeyTool and not wanted (files, shell, web)
BLOCKED_BUILTIN = ("Bash", "BashOutput", "KillShell", "Edit", "MultiEdit", "Write", "NotebookEdit", "Read", "Glob",
                   "Grep", "LS", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "ExitPlanMode", "Skill",
                   "SlashCommand")
MODELS = ("", "sonnet", "opus", "haiku")      # "" = Claude Code's default for the plan
SYSTEM = """You are the assistant inside KeyTool, a Windows app for DJs: a music library with detected BPM and key
(notation the user sees: {notation}), keywords, Serato cue points and song sections. Use only the KeyTool tools.
Operate the open app with the app_* tools (filter, select, load into the deck, play, transpose, jump) instead of
explaining how to do it by hand. Track ids come from the tools; never invent them. Tools that change data or files
are confirmed by the user in the app; if a call is declined, accept it and do not repeat it unasked.
Answer briefly in {language}; refer to tracks as "Title" (Artist). App state now: {state}"""
LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish", "fr": "French", "it": "Italian"}


class ChatError(Exception):
    pass


def find_claude(configured: str = "") -> str | None:
    """Path of the claude program: the setting, then PATH, then the usual install places on Windows."""
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    for p in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"),
              os.path.join(home, ".claude", "local", "claude.exe")):
        if p and os.path.isfile(p):
            return p
    return None


def mcp_launch() -> tuple[str, list[str]]:
    """How to start KeyTool's MCP server (the frozen KeyTool-MCP.exe, else this Python with -m keytool --mcp)."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "KeyTool-MCP.exe"), []
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        exe = os.path.join(os.path.dirname(exe), "python.exe")
    return exe, ["-m", "keytool", "--mcp"]


def write_mcp_config() -> str:
    cmd, args = mcp_launch()
    env = {"KEYTOOL_DB": str(db_path())}
    if os.environ.get("KEYTOOL_DATA_DIR"):
        env["KEYTOOL_DATA_DIR"] = os.environ["KEYTOOL_DATA_DIR"]
    folder = data_dir() / "chat"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {SERVER: {"command": cmd, "args": args, "env": env}}}, indent=2),
                    encoding="utf-8")
    return str(path)


def short_name(tool: str) -> str:
    """"mcp__keytool__app_set_filter" -> "set_filter"."""
    name = tool[len(PREFIX):] if tool.startswith(PREFIX) else tool
    return name[4:] if name.startswith("app_") else name


def plan_env() -> dict:
    """Environment without API credentials: Claude Code then uses the signed-in Claude plan."""
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    return env


class Session:
    """One conversation (Claude Code session, continued with --resume)."""

    def __init__(self, exe: str, model: str = ""):
        self.exe, self.model = exe, model
        self.session_id: str | None = None
        self.proc: subprocess.Popen | None = None

    def command(self, system: str, mcp_config: str) -> list[str]:
        if self.exe.lower().endswith((".cmd", ".bat")):
            # npm installs claude.cmd, which runs through cmd.exe: keep the argument on one line, no double quotes
            system = " ".join(system.split()).replace('"', "'")
        cmd = [self.exe, "-p", "--output-format", "stream-json", "--verbose",
               "--mcp-config", mcp_config, "--strict-mcp-config",
               "--allowedTools", ",".join(PREFIX + t for t in ALLOWED),
               "--disallowedTools", ",".join(list(BLOCKED_BUILTIN) + [PREFIX + t for t in BLOCKED_MCP]),
               "--permission-prompt-tool", PREFIX + "app_confirm_permission",
               "--append-system-prompt", system]
        if self.model:
            cmd += ["--model", self.model]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def run(self, prompt: str, system: str, on_event: Callable[[str, object], None],
            cancel: threading.Event | None = None) -> str:
        """Send one message; on_event("text", str) for answers, ("action", (name, input, result, is_error)) after
        each tool. Returns the final answer; raises ChatError if Claude Code reports an error."""
        cwd = data_dir() / "chat"                    # neutral folder: no project files / CLAUDE.md around
        cwd.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(self.command(system, write_mcp_config()), cwd=str(cwd), env=plan_env(),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, encoding="utf-8", errors="replace", creationflags=flags)
        except OSError as e:
            raise ChatError(f"Claude Code could not be started: {e}") from e
        stderr: list[str] = []
        threading.Thread(target=lambda: stderr.extend(self.proc.stderr), daemon=True).start()
        self.proc.stdin.write(prompt)
        self.proc.stdin.close()
        calls: dict[str, tuple[str, dict]] = {}
        answer, error = "", None
        for line in self.proc.stdout:
            if cancel is not None and cancel.is_set():
                self.stop()
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if ev.get("session_id"):
                self.session_id = ev["session_id"]
            if kind == "assistant":
                for block in (ev.get("message") or {}).get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        answer = block["text"]
                        on_event("text", block["text"])
                    elif block.get("type") == "tool_use":
                        calls[block.get("id", "")] = (block.get("name", ""), block.get("input") or {})
            elif kind == "user":
                content = (ev.get("message") or {}).get("content", [])
                for block in content if isinstance(content, list) else []:
                    if block.get("type") == "tool_result":
                        name, args = calls.get(block.get("tool_use_id", ""), ("", {}))
                        result = block.get("content")
                        if isinstance(result, list):
                            result = "".join(c.get("text", "") for c in result if isinstance(c, dict))
                        on_event("action", (name, args, str(result or ""), bool(block.get("is_error"))))
            elif kind == "result":
                if ev.get("is_error") or ev.get("subtype", "success") != "success":
                    error = ev.get("result") or ev.get("subtype") or "error"
                elif ev.get("result") and not answer:
                    answer = ev["result"]
                    on_event("text", answer)
        code = self.proc.wait()
        self.proc = None
        if cancel is not None and cancel.is_set():
            return answer
        if error:
            raise ChatError(str(error))
        if code != 0 and not answer:
            msg = "".join(stderr).strip().splitlines()
            raise ChatError(msg[-1] if msg else f"Claude Code ended with code {code}")
        return answer

    def stop(self):
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


def check(exe: str, model: str = "") -> str:
    """Version and a one-word test request (signed in?). Returns a short status text, raises ChatError."""
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, env=plan_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ChatError(str(e)) from e
    version = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else "?"
    cmd = [exe, "-p", "--output-format", "json", "--disallowedTools", ",".join(BLOCKED_BUILTIN)]
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, input="Reply with the single word OK.", capture_output=True, text=True, timeout=120,
                           env=plan_env(), cwd=str(data_dir()))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ChatError(str(e)) from e
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise ChatError((r.stderr or r.stdout).strip()[-300:] or f"exit code {r.returncode}") from None
    if data.get("is_error"):
        raise ChatError(str(data.get("result") or data.get("subtype")))
    return version
