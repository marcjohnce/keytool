"""Worker that kills its own process for files whose name contains 'CRASH' (simulates a native crash)."""
import os


def crashing_worker(path, bpm_min, bpm_max):
    if "CRASH" in os.path.basename(path):
        os._exit(3)
    from keytool.library import _analyze_worker
    return _analyze_worker(path, bpm_min, bpm_max)
