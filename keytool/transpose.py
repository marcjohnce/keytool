"""Transpose a track by +-1 semitone at the original tempo (pitch shift with time correction).

Uses Rubber Band via Spotify's pedalboard (GPLv3). The track is decoded at its native sample rate,
processed in overlapping chunks on all CPU cores, stitched together with short crossfades and written
to a cache file that the player plays instead of the original. Length and timing stay identical to the
original, so positions, the waveform and cue points remain valid.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from .config import data_dir

CHUNK_SECONDS = 30.0
OVERLAP_SECONDS = 1.0
CROSSFADE_SECONDS = 0.05
CACHE_LIMIT_FILES = 12
_lock = threading.Lock()


def available() -> bool:
    try:
        import pedalboard  # noqa: F401
        return True
    except Exception:
        return False


def cache_dir():
    d = data_dir() / "transpose_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audio_signature(path: str) -> str:
    """Identifies the audio content but not the tags: editing BPM/key/title must not invalidate the cache."""
    try:
        import mutagen
        info = mutagen.File(path).info
        return f"{round(info.length * 1000)}|{getattr(info, 'sample_rate', 0)}|{getattr(info, 'channels', 0)}"
    except Exception:
        st = os.stat(path)
        return f"{st.st_size}|{int(st.st_mtime)}"


def cache_path(path: str, semitones: int) -> str:
    h = hashlib.sha1(f"{os.path.abspath(path)}|{_audio_signature(path)}".encode("utf-8")).hexdigest()[:20]
    return str(cache_dir() / f"{h}_{semitones:+d}.wav")


def cached(path: str, semitones: int) -> str | None:
    try:
        p = cache_path(path, semitones)
    except OSError:
        return None
    return p if os.path.exists(p) and os.path.getsize(p) > 44 else None


def _decode_stereo(path: str) -> tuple[np.ndarray, int]:
    import av

    with av.open(path) as c:
        stream = next(s for s in c.streams if s.type == "audio")
        sr = stream.codec_context.sample_rate or 44100
        res = av.AudioResampler(format="fltp", layout="stereo", rate=sr)
        chunks = []
        for frame in c.decode(stream):
            for out in res.resample(frame):
                chunks.append(out.to_ndarray())
        for out in res.resample(None):
            chunks.append(out.to_ndarray())
    if not chunks:
        raise ValueError("no audio")
    return np.concatenate(chunks, axis=1).astype(np.float32), int(sr)


def shift(audio: np.ndarray, sr: int, semitones: int, workers: int | None = None,
          progress: Callable[[float], None] | None = None) -> np.ndarray:
    """Pitch-shift `audio` (channels x samples) keeping its length, using all CPU cores."""
    from pedalboard import time_stretch

    n = audio.shape[1]
    step = int(CHUNK_SECONDS * sr)
    ov = int(OVERLAP_SECONDS * sr)
    bounds = [(s, min(n, s + step)) for s in range(0, n, step)] or [(0, n)]
    done = [0]

    def job(i):
        s, e = bounds[i]
        a, b = max(0, s - ov), min(n, e + ov)
        out = time_stretch(audio[:, a:b], sr, stretch_factor=1.0, pitch_shift_in_semitones=float(semitones),
                           high_quality=True)
        if out.shape[1] < b - a:                       # keep exact length
            out = np.pad(out, ((0, 0), (0, b - a - out.shape[1])))
        done[0] += 1
        if progress:
            progress(done[0] / len(bounds))
        return a, out[:, : b - a]

    workers = workers or max(1, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=min(workers, len(bounds))) as ex:   # pedalboard releases the GIL
        parts = list(ex.map(job, range(len(bounds))))

    result = np.zeros_like(audio)
    cf = max(1, int(CROSSFADE_SECONDS * sr))
    for i, ((s, e), (a, out)) in enumerate(zip(bounds, parts)):
        seg = out[:, s - a: e - a]
        result[:, s:e] = seg
        if i > 0:                                      # crossfade across the chunk boundary
            pa, pout = parts[i - 1]
            lo, hi = max(s - cf, 0), min(s + cf, n)
            prev = pout[:, lo - pa: hi - pa]
            cur = out[:, lo - a: hi - a]
            m = min(prev.shape[1], cur.shape[1])
            if m > 1:
                w = np.linspace(0.0, 1.0, m, dtype=np.float32)
                result[:, lo:lo + m] = prev[:, :m] * (1 - w) + cur[:, :m] * w
    return result


def render(path: str, semitones: int, progress: Callable[[float], None] | None = None,
           workers: int | None = None) -> str:
    """Create (or reuse) the transposed version of `path`. Returns the cache file path."""
    import soundfile as sf

    if semitones == 0:
        return path
    hit = cached(path, semitones)
    if hit:
        return hit
    audio, sr = _decode_stereo(path)
    if progress:
        progress(0.02)
    out = shift(audio, sr, semitones, workers, (lambda f: progress(0.02 + 0.93 * f)) if progress else None)
    np.clip(out, -1.0, 1.0, out=out)
    target = cache_path(path, semitones)
    tmp = target + ".part"
    sf.write(tmp, out.T, sr, subtype="PCM_16", format="WAV")
    os.replace(tmp, target)
    _trim_cache()
    if progress:
        progress(1.0)
    return target


def _trim_cache() -> None:
    with _lock:
        try:
            files = sorted((f for f in cache_dir().glob("*.wav")), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[CACHE_LIMIT_FILES:]:
                f.unlink(missing_ok=True)
            for f in cache_dir().glob("*.part"):
                if time.time() - f.stat().st_mtime > 3600:
                    f.unlink(missing_ok=True)
        except OSError:
            pass
