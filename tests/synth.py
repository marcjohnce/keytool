"""Synthetic test tracks: drum loop at a given BPM + chord progression in a given key."""
from __future__ import annotations

import numpy as np

SR = 22050
MAJOR_STEPS = [0, 2, 4, 5, 7, 9, 11]
MINOR_STEPS = [0, 2, 3, 5, 7, 8, 10]


def _tone(freq, dur, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    env = np.minimum(1, t * 50) * np.exp(-t * 0.8)
    sig = sum((0.6 ** h) * np.sin(2 * np.pi * freq * (h + 1) * t) for h in range(5))
    return sig * env


def make_track(bpm=128.0, tonic=9, minor=True, seconds=40, sr=SR, drums=True, seed=0, a4=440.0):
    ref = a4 / 440.0
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    y = np.zeros(n)
    beat = 60.0 / bpm
    steps = MINOR_STEPS if minor else MAJOR_STEPS
    # i - VI - III - VII (minor) / I - V - vi - IV (major) in scale degrees
    prog = [0, 3, 5, 4] if minor else [0, 4, 5, 3]
    bar = 4 * beat
    t = 0.0
    i = 0
    while t < seconds:
        deg = prog[i % len(prog)]
        chord = [steps[(deg + k) % 7] + 12 * ((deg + k) // 7) for k in (0, 2, 4)]
        if minor and deg == 4:  # dominant with raised leading tone (harmonic minor)
            chord = [c + 1 if c % 12 == 10 else c for c in chord]
        start = int(t * sr)
        for semis in chord + [chord[0] - 12]:
            f = ref * 220.0 * 2 ** ((tonic - 9 + semis) / 12)
            tone = _tone(f, bar)
            end = min(n, start + tone.size)
            y[start:end] += 0.2 * tone[: end - start]
        # bass on tonic of chord
        f = ref * 55.0 * 2 ** ((tonic - 9 + chord[0]) / 12)
        tone = _tone(f, bar)
        end = min(n, start + tone.size)
        y[start:end] += 0.3 * tone[: end - start]
        t += bar
        i += 1
    if drums:
        k = 0
        while k * beat < seconds:
            s = int(k * beat * sr)
            L = int(0.12 * sr)
            tt = np.arange(L) / sr
            kick = np.sin(2 * np.pi * (50 + 100 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 25)
            e = min(n, s + L)
            y[s:e] += 0.9 * kick[: e - s]
            # off-beat hihat
            s2 = int((k + 0.5) * beat * sr)
            L2 = int(0.04 * sr)
            hat = rng.standard_normal(L2) * np.exp(-np.arange(L2) / sr * 80)
            e2 = min(n, s2 + L2)
            if s2 < n:
                y[s2:e2] += 0.15 * hat[: e2 - s2]
            k += 1
    y /= np.max(np.abs(y)) + 1e-9
    return (0.8 * y).astype(np.float32)
