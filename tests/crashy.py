"""Worker that kills its own process for files whose name contains 'CRASH' (simulates a native crash)."""
import os


def crashing_worker(path, bpm_min, bpm_max, *args):
    if "CRASH" in os.path.basename(path):
        os._exit(3)
    from keytool.library import _analyze_worker
    return _analyze_worker(path, bpm_min, bpm_max, *args)


def recording_worker(path, bpm_min, bpm_max, do_bpm=True, do_key=True, key_method="classic"):
    """Logs which parts were requested (file given by KEYTOOL_TEST_LOG), then analyses normally."""
    log = os.environ.get("KEYTOOL_TEST_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{os.path.basename(path)}|{int(do_bpm)}|{int(do_key)}\n")
    from keytool.library import _analyze_worker
    return _analyze_worker(path, bpm_min, bpm_max, do_bpm, do_key, key_method)


def slow_recording_worker(path, *args):
    """Like recording_worker but takes a moment, so the queue can be rearranged while it runs."""
    import time
    time.sleep(0.4)
    return recording_worker(path, *args)


def crashing_recording_worker(path, *args):
    if "CRASH" in os.path.basename(path):
        os._exit(3)
    return recording_worker(path, *args)
