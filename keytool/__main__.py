"""Entry point.

    python -m keytool              -> GUI
    python -m keytool --mcp        -> MCP server (stdio)
    python -m keytool --mcp-http   -> MCP server (streamable HTTP on http://127.0.0.1:8000/mcp)
    python -m keytool --analyze PATH [PATH ...]  -> console import/analysis
    python -m keytool --compare-key PATH [--limit 25] -> compare classic vs fast key detection
    python -m keytool --set-key-method fast|classic   -> choose the key detection method
    python -m keytool --import-mik [MIKStore.db] [--add-missing] [--fill-only] [--no-keywords]
                                   -> import key/tempo/energy/collections from Mixed In Key
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
            print(f"Allowed: {', '.join(KEY_METHODS)}")
            return 2
        s = Settings.load(); s.key_method = method; s.save()
        print(f"Key detection method: {method}")
        return 0
    if "--import-mik" in args:
        from .library import Library
        from .mik_import import apply_import, default_db_paths, plan_import, read_mik
        files = [a for a in args if not a.startswith("--")]
        db_file = files[0] if files else next(iter(default_db_paths()), None)
        if not db_file:
            print("No Mixed In Key library found (MIKStore.db)")
            return 2
        lib = Library()
        plan = plan_import(lib.db, read_mik(db_file))
        print(f"{db_file}: {len(plan.songs)} songs, {len(plan.matched)} in library "
              f"({plan.by_filename} by file name), {len(plan.missing_existing)} not in library, "
              f"{len(plan.missing_gone)} files gone")
        res = apply_import(lib, plan, add_missing="--add-missing" in args,
                           collections_as_keywords="--no-keywords" not in args, overwrite="--fill-only" not in args)
        print(res)
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
