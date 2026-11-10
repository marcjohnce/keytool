import multiprocessing
import os
import sys


def _route_missing_stdio_to_log():
    """The windowed exe (and every analysis process it spawns) has no console: sys.stdout/stderr are None and
    any error while a worker process starts up would vanish. Send them to analysis.log instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        if os.environ.get("KEYTOOL_DATA_DIR"):
            folder = os.environ["KEYTOOL_DATA_DIR"]
        elif sys.platform == "win32":
            folder = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "KeyTool")
        else:
            folder = os.path.join(os.path.expanduser("~"), ".config", "keytool")
        os.makedirs(folder, exist_ok=True)
        log = open(os.path.join(folder, "analysis.log"), "a", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = log
        if sys.stdout is None:
            sys.stdout = log
    except Exception:
        pass


if __name__ == "__main__":
    _route_missing_stdio_to_log()
    multiprocessing.freeze_support()
    from keytool.__main__ import main
    if len(sys.argv) == 1:          # no arguments: MCP server over stdio (as started by Claude Desktop);
        sys.argv.append("--mcp")    # with arguments the console exe also runs --analyze / --compare-key etc.
    sys.exit(main())
