"""Remote control of the running KeyTool app, for Claude (Claude Desktop through the MCP server, or the chat
sidebar, which runs Claude Code with the same MCP server).

The GUI starts a small HTTP server on 127.0.0.1 (random free port) and writes the port and a random token into
the data folder (app_control.json). The MCP server – a separate process started by Claude – reads that file and
sends commands like "set_filter" or "load_into_deck"; the GUI runs them on its own thread and answers with JSON.
Only processes of the same user that can read the data folder know the token; nothing listens on the network.

Commands: see COMMANDS (the GUI registers a handler for each). "confirm" asks the user in the chat sidebar
whether Claude may run a tool that changes data or files (Claude Code's --permission-prompt-tool).
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .config import data_dir

CONTROL_FILE = "app_control.json"
COMMANDS = ("state", "set_filter", "clear_filters", "select_tracks", "load_into_deck", "playback", "transpose",
            "jump_in_deck", "add_files", "reanalyze", "add_cue", "delete_cue", "confirm", "library_changed")


class AppNotRunning(Exception):
    """No KeyTool window is open (or it does not answer)."""


def control_file() -> str:
    return str(data_dir() / CONTROL_FILE)


# ============================================================================ server (inside the GUI)
class ControlServer:
    """HTTP server on 127.0.0.1. `dispatch(command, args) -> dict` is called on the server thread and must hand
    the work to the GUI thread itself (the GUI passes a function that does exactly that)."""

    def __init__(self, dispatch: Callable[[str, dict], Any]):
        self.dispatch = dispatch
        self.token = secrets.token_urlsafe(24)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):            # quiet
                pass

            def do_POST(self):
                if self.headers.get("X-KeyTool-Token") != server.token:
                    self._reply(403, {"error": "forbidden"})
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    cmd, args = body.get("command", ""), body.get("args") or {}
                    if cmd not in COMMANDS:
                        self._reply(400, {"error": f"unknown command {cmd!r}"})
                        return
                    self._reply(200, {"result": server.dispatch(cmd, args)})
                except Exception as e:             # report, never kill the server
                    self._reply(500, {"error": f"{type(e).__name__}: {e}"})

            def _reply(self, status: int, data: dict):
                raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="keytool-control", daemon=True)

    def start(self):
        self.thread.start()
        path = control_file()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, f)
        os.replace(tmp, path)
        return self

    def stop(self):
        try:
            with open(control_file(), encoding="utf-8") as f:
                ours = json.load(f).get("token") == self.token   # only our own file (another window may own it)
            if ours:                                             # closed first: Windows cannot delete open files
                os.remove(control_file())
        except (OSError, ValueError):
            pass
        self.httpd.shutdown()
        self.httpd.server_close()


# ============================================================================ client (MCP server)
def call(command: str, args: dict | None = None, timeout: float = 30.0) -> Any:
    """Send a command to the running app; raises AppNotRunning if there is none."""
    import urllib.error
    import urllib.request
    try:
        with open(control_file(), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        raise AppNotRunning("The KeyTool app is not open. Start KeyTool to control it.") from None
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(info['port'])}/", method="POST",
        data=json.dumps({"command": command, "args": args or {}}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-KeyTool-Token": info.get("token", "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        raise RuntimeError(data.get("error") or f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise AppNotRunning(f"The KeyTool app does not answer ({e}). Is it open?") from None
    return data.get("result")
