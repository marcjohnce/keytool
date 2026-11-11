"""Smoke test for the built executables: analyse a synthetic track with KeyTool-MCP.exe and check the result.

    python packaging/smoke_test.py dist/KeyTool

Catches packaging problems (missing modules/DLLs, broken worker processes in the frozen app) that unit tests
running from source cannot see. Uses only the standard library, so it tests the .exe, not the Python environment.
"""
from __future__ import annotations

import math
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import wave

BPM = 120.0
SR = 22050
SECONDS = 20


def write_track(path: str, seconds: int = SECONDS, parts: bool = False) -> None:
    """A minor chord (A C E) plus a click on every beat at 120 BPM. parts: 15 s blocks alternating with an
    F major chord (F A C) and no clicks, so the song-section detection has something to find."""
    freqs = (220.0, 261.63, 329.63)
    other = (174.61, 220.0, 261.63)
    beat = int(SR * 60 / BPM)
    frames = bytearray()
    for n in range(SR * seconds):
        t = n / SR
        b = parts and int(t // 15) % 2 == 1
        fs = other if b else freqs
        v = sum(math.sin(2 * math.pi * f * t) for f in fs) / len(fs) * 0.35
        if b:
            frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32000))
            continue
        k = n % beat
        if k < 600:                                    # short decaying click on the beat
            v += math.sin(2 * math.pi * 1000 * t) * math.exp(-k / 120) * 0.6
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32000))
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(bytes(frames))


def main(dist: str) -> int:
    exe = os.path.join(dist, "KeyTool-MCP.exe" if os.name == "nt" else "KeyTool-MCP")
    gui = os.path.join(dist, "KeyTool.exe" if os.name == "nt" else "KeyTool")
    for f in (exe, gui):
        if not os.path.isfile(f):
            print(f"missing: {f}")
            return 1
    work = tempfile.mkdtemp(prefix="keytool-smoke-")
    track = os.path.join(work, "smoke.wav")
    write_track(track)
    song = os.path.join(work, "sections.wav")
    write_track(song, seconds=60, parts=True)
    env = dict(os.environ, KEYTOOL_DATA_DIR=os.path.join(work, "data"))
    proc = subprocess.run([exe, "--analyze", track, song], env=env, capture_output=True, text=True, timeout=900)
    print(proc.stdout[-3000:])
    if proc.returncode != 0:
        print(proc.stderr[-3000:])
        print(f"KeyTool-MCP exited with {proc.returncode}")
        return 1
    db = os.path.join(work, "data", "library.sqlite3")
    con = sqlite3.connect(db)
    row = con.execute("SELECT status, bpm, key, error FROM tracks WHERE filename='smoke.wav'").fetchone()
    sections = con.execute("SELECT sections FROM tracks WHERE filename='sections.wav'").fetchone()[0]
    print("sections:", sections)
    print("result:", row)
    log = os.path.join(work, "data", "analysis.log")
    if os.path.exists(log):
        print("analysis.log:\n" + open(log, encoding="utf-8", errors="replace").read()[-3000:])
    status, bpm, key, error = row
    if status != "ok" or not bpm or not key:
        print(f"analysis failed: status={status} error={error}")
        return 1
    if abs(bpm - BPM) > 2 and abs(bpm - 2 * BPM) > 2 and abs(bpm - BPM / 2) > 2:
        print(f"unexpected tempo {bpm} (expected about {BPM})")
        return 1
    if key != "9:min":                                 # A minor (pitch class 9) = Camelot 8A
        print(f"unexpected key {key} (expected 9:min = A minor)")
        return 1
    import json
    try:
        n_sections = len(json.loads(sections or "null") or [])
    except ValueError:
        n_sections = 0
    if n_sections < 2:                                 # scipy/librosa parts missing in the frozen app?
        print("song sections were not detected (see analysis.log)")
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join("dist", "KeyTool")))
