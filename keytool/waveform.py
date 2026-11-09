"""Waveform overview: per-bin peak level plus a colour from the energy in three frequency bands.

low (< 200 Hz) -> blue, mid (200 Hz - 2 kHz) -> amber, high (> 2 kHz) -> white, similar to
the 3-band waveforms in DJ software. Results are cached on disk (a few KB per track).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import numpy as np

from .config import data_dir

WF_SR = 11025
DEFAULT_BINS = 1600
_CACHE_VERSION = 2

_BAND_COLORS = np.array([
    [0.22, 0.48, 1.00],   # low
    [1.00, 0.66, 0.16],   # mid
    [0.93, 0.93, 1.00],   # high
])


_BAND_GAINS = np.array([1.0, 2.2, 5.0])


@dataclass
class Waveform:
    peaks: np.ndarray      # float32 [bins], 0..1
    colors: np.ndarray     # uint8   [bins, 3]
    duration: float        # seconds


def _cache_file(path: str) -> str:
    h = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()
    d = data_dir() / "waveforms"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{h}.npz")


def compute_waveform(path: str, bins: int = DEFAULT_BINS) -> Waveform:
    from .analysis import load_audio

    y, duration = load_audio(path, sr=WF_SR, max_seconds=3 * 3600)
    n = y.size
    if n == 0:
        return Waveform(np.zeros(0, np.float32), np.zeros((0, 3), np.uint8), duration)
    bins = max(1, min(bins, n // 64))
    per = n // bins
    frames = y[: per * bins].reshape(bins, per)
    peaks = np.abs(frames).max(axis=1)
    top = float(np.percentile(peaks, 99.5)) or float(peaks.max()) or 1.0
    peaks = np.clip(peaks / top, 0, 1).astype(np.float32)

    # band energies, computed in row chunks so long DJ mixes stay small in memory
    freqs = np.fft.rfftfreq(per, 1.0 / WF_SR)
    masks = [freqs < 200, (freqs >= 200) & (freqs < 2000), freqs >= 2000]
    window = np.hanning(per).astype(np.float32)
    energy = np.zeros((bins, 3), np.float64)
    step = max(1, 4_000_000 // max(per, 1))
    for i in range(0, bins, step):
        spec = np.abs(np.fft.rfft(frames[i:i + step] * window, axis=1)) ** 2
        for b, m in enumerate(masks):
            energy[i:i + step, b] = spec[:, m].sum(axis=1)
    # fixed per-band gains compensate the natural bass-heavy spectrum of music (no per-track
    # normalisation: a track without highs must not get its leakage boosted into white)
    energy = np.sqrt(energy) * _BAND_GAINS
    w = energy / np.maximum(energy.sum(axis=1, keepdims=True), 1e-12)
    rgb = w @ _BAND_COLORS
    colors = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    return Waveform(peaks, colors, float(duration))


def get_waveform(path: str, bins: int = DEFAULT_BINS) -> Waveform:
    """Cached waveform; recomputed if the file changed size or modification time."""
    st = os.stat(path)
    cf = _cache_file(path)
    sig = np.array([_CACHE_VERSION, bins, st.st_size, st.st_mtime], dtype=np.float64)
    try:
        with np.load(cf) as z:
            if np.array_equal(z["sig"], sig):
                return Waveform(z["peaks"], z["colors"], float(z["duration"]))
    except Exception:
        pass
    wf = compute_waveform(path, bins)
    try:
        tmp = cf + ".tmp.npz"
        np.savez_compressed(tmp, sig=sig, peaks=wf.peaks, colors=wf.colors, duration=wf.duration)
        os.replace(tmp, cf)
    except OSError:
        pass
    return wf
