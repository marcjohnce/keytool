"""Entry point.

    python -m keytool              -> GUI
    python -m keytool --mcp        -> MCP server (stdio)
    python -m keytool --mcp-http   -> MCP server (streamable HTTP on http://127.0.0.1:8000/mcp)
    python -m keytool --analyze PATH [PATH ...]  -> console import/analysis
    python -m keytool --compare-key PATH [--limit 25] -> compare classic vs fast key detection
    python -m keytool --set-key-method fast|classic   -> choose the key detection method
"""
from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()   # required for the frozen .exe (process pool)
    import os
    from .config import data_dir
    # numba (used by librosa) needs a writable cache dir, esp. inside the frozen .exe
    os.environ.setdefault("NUMBA_CACHE_DIR", str(data_dir() / "numba_cache"))
    args = sys.argv[1:]
    if "--compare-key" in args:
        from .compare import main as compare_main
        return compare_main([a for a in args if a != "--compare-key"])
    if "--set-key-method" in args:
        from .config import Settings
        from .analysis import KEY_METHODS
        i = args.index("--set-key-method")
        method = args[i + 1] if i + 1 < len(args) else ""
        if method not in KEY_METHODS:
            print(f"Erlaubt: {', '.join(KEY_METHODS)}")
            return 2
        s = Settings.load(); s.key_method = method; s.save()
        print(f"Tonart-Verfahren: {method}")
        return 0
    if "--mcp" in args or "--mcp-http" in args:
        from .mcp_server import run
        run(http="--mcp-http" in args)
        return 0
    if "--analyze" in args:
        from .library import Library
        paths = [a for a in args if a != "--analyze"]
        lib = Library()
        def prog(done, total, row):
            if row:
                from .keys import format_code
                print(f"[{done}/{total}] {row.get('bpm') or '-':>6}  {format_code(row.get('key')) or '-':>4}  {row['path']}",
                      flush=True)
        res = lib.import_paths(paths, prog)
        print(res)
        return 0
    from .gui.app import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
