"""Worker that kills its own process for files whose name contains 'CRASH' (simulates a native crash)."""
import os


def crashing_worker(path, bpm_min, bpm_max, *args):
    if "CRASH" in os.path.basename(path):
        os._exit(3)
    from keytool.library import _analyze_worker
    return _analyze_worker(path, bpm_min, bpm_max, *args)


def _append_locked(log: str, line: str) -> None:
    """Append one line from several worker processes without losing any: appends to the same file are
    not atomic on Windows, so take a lock first (creating a directory is atomic on every platform)."""
    import time
    lock = log + ".lock"
    deadline = time.time() + 10
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            if time.time() > deadline:
                raise
            time.sleep(0.005)
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(line)
    finally:
        os.rmdir(lock)


def recording_worker(path, bpm_min, bpm_max, do_bpm=True, do_key=True, key_method="classic", do_sections=False):
    """Logs which parts were requested (file given by KEYTOOL_TEST_LOG), then analyses normally."""
    log = os.environ.get("KEYTOOL_TEST_LOG")
    if log:
        _append_locked(log, f"{os.path.basename(path)}|{int(do_bpm)}|{int(do_key)}\n")
    from keytool.library import _analyze_worker
    return _analyze_worker(path, bpm_min, bpm_max, do_bpm, do_key, key_method, do_sections)


def slow_recording_worker(path, *args):
    """Like recording_worker but takes a moment, so the queue can be rearranged while it runs."""
    import time
    time.sleep(0.4)
    return recording_worker(path, *args)


def crashing_recording_worker(path, *args):
    if "CRASH" in os.path.basename(path):
        os._exit(3)
    return recording_worker(path, *args)


def segfault_worker(path, *args):
    """Native crash (segmentation fault / access violation) for files named *SEGV*."""
    if "SEGV" in os.path.basename(path):
        # a real native crash on every platform (ctypes.string_at(0) is caught by ctypes on Windows and
        # only raises OSError); _sigsegv is CPython's helper for exactly this kind of test
        import faulthandler
        faulthandler._sigsegv()
    from keytool.library import _analyze_worker
    return _analyze_worker(path, *args)


def raising_worker(path, *args):
    """Goes through the real worker, which must log the Python traceback, for files named *BADFILE*."""
    from keytool.library import _analyze_worker
    if "BADFILE" not in os.path.basename(path):
        return _analyze_worker(path, *args)
    import keytool.analysis as an
    original = an.analyze_file
    an.analyze_file = lambda *a, **k: (_ for _ in ()).throw(ValueError("simulated decode error"))
    try:
        return _analyze_worker(path, *args)
    finally:
        an.analyze_file = original
