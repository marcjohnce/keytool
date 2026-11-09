"""Audio analysis: tempo (BPM) and musical key detection.

Decoding uses PyAV (bundled FFmpeg -> MP3, WAV, FLAC, OGG, M4A/AAC/ALAC work
out of the box on Windows) with a librosa/soundfile fallback.

BPM
    1. onset strength envelope (median aggregated spectral flux)
    2. global tempo estimate from the autocorrelation tempogram
    3. beat tracking seeded with that tempo
    4. robust linear regression over beat positions -> sub-frame precision
    5. octave correction into a configurable BPM range

Key
    1. harmonic/percussive separation (drums would smear the chroma)
    2. tuning estimation (tracks that are not tuned to A=440 Hz)
    3. constant-Q chromagram, energy weighted
    4. correlation with an ensemble of key profiles
       (Krumhansl-Kessler, Temperley, Shaath/KeyFinder) for all 24 keys
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, asdict

import numpy as np

from .keys import Key

SR = 22050
HOP = 512
MAX_SECONDS = 600  # analyse at most 10 minutes per track

# ----------------------------------------------------------------- key profiles
_PROFILES = {
    "krumhansl": (
        [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
        [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    ),
    "temperley": (
        [0.748, 0.060, 0.488, 0.082, 0.670, 0.460, 0.096, 0.715, 0.104, 0.366, 0.057, 0.400],
        [0.712, 0.084, 0.474, 0.618, 0.049, 0.460, 0.105, 0.747, 0.404, 0.067, 0.133, 0.330],
    ),
    "shaath": (
        [6.6, 2.0, 3.5, 2.3, 4.6, 4.0, 2.5, 5.2, 2.4, 3.7, 2.3, 3.4],
        [6.5, 2.7, 3.5, 5.4, 2.6, 3.5, 2.5, 5.2, 4.0, 2.7, 4.3, 3.2],
    ),
}


@dataclass
class AnalysisResult:
    bpm: float | None
    bpm_confidence: float
    key: str | None          # canonical code "pc:maj|min"
    key_confidence: float
    duration: float

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------- decoding
def load_audio(path: str, sr: int = SR, max_seconds: float = MAX_SECONDS) -> tuple[np.ndarray, float]:
    """Decode to mono float32 at ``sr``. Returns (samples, full_duration_seconds)."""
    try:
        return _load_pyav(path, sr, max_seconds)
    except Exception:  # pragma: no cover - fallback path
        import librosa

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            y, _ = librosa.load(path, sr=sr, mono=True, duration=max_seconds)
            try:
                dur = float(librosa.get_duration(path=path))
            except Exception:
                dur = len(y) / sr
        return y.astype(np.float32), dur


def _load_pyav(path: str, sr: int, max_seconds: float) -> tuple[np.ndarray, float]:
    import av

    chunks: list[np.ndarray] = []
    total = 0
    limit = int(max_seconds * sr)
    with av.open(path) as container:
        stream = next(s for s in container.streams if s.type == "audio")
        dur = None
        if stream.duration is not None and stream.time_base is not None:
            dur = float(stream.duration * stream.time_base)
        elif container.duration:
            dur = container.duration / 1_000_000
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sr)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                arr = out.to_ndarray().reshape(-1)
                chunks.append(arr)
                total += arr.size
            if total >= limit:
                break
        else:
            for out in resampler.resample(None):
                arr = out.to_ndarray().reshape(-1)
                chunks.append(arr)
                total += arr.size
    if not chunks:
        raise ValueError("no audio decoded")
    y = np.concatenate(chunks)[:limit].astype(np.float32)
    return y, float(dur if dur else len(y) / sr)


# ----------------------------------------------------------------- tempo
def fold_bpm(bpm: float, lo: float, hi: float) -> float:
    if bpm <= 0:
        return bpm
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    return bpm


def _regress_period(beat_times: np.ndarray) -> tuple[float | None, float]:
    """Robust beat period by fitting time = t0 + i*period. Returns (period, inlier ratio)."""
    if beat_times.size < 8:
        return None, 0.0
    diffs = np.diff(beat_times)
    med = float(np.median(diffs))
    if med <= 0:
        return None, 0.0
    # Integer beat index; tolerates skipped beats
    steps = np.maximum(1, np.round(diffs / med)).astype(int)
    idx = np.concatenate([[0], np.cumsum(steps)])
    period, t0 = np.polyfit(idx, beat_times, 1)
    for _ in range(3):
        resid = np.abs(beat_times - (t0 + idx * period))
        inl = resid < 0.08 * period
        if inl.sum() < 8:
            break
        period, t0 = np.polyfit(idx[inl], beat_times[inl], 1)
    resid = np.abs(beat_times - (t0 + idx * period))
    ratio = float(np.mean(resid < 0.08 * period))
    return float(period), ratio


def detect_bpm(y: np.ndarray, sr: int = SR, bpm_min: float = 70, bpm_max: float = 180) -> tuple[float | None, float]:
    import librosa

    if y.size < sr * 5:
        return None, 0.0
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP, aggregate=np.median)
    if not np.any(oenv > 0):
        return None, 0.0
    tempo = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=oenv, sr=sr, hop_length=HOP))[0])
    if tempo <= 0:
        return None, 0.0
    tempo = fold_bpm(tempo, bpm_min, bpm_max)
    _, beats = librosa.beat.beat_track(
        onset_envelope=oenv, sr=sr, hop_length=HOP, bpm=tempo, tightness=120, units="time"
    )
    period, ratio = _regress_period(np.asarray(beats, dtype=float))
    bpm = 60.0 / period if period else tempo
    # Guard against the regression locking onto a wrong metrical level
    if abs(math.log2(bpm / tempo)) > 0.25:
        bpm = tempo
        ratio *= 0.5
    bpm = fold_bpm(bpm, bpm_min, bpm_max)
    # Most produced music has integer BPM - snap when we are very close
    if abs(bpm - round(bpm)) < 0.06:
        bpm = float(round(bpm))
    # Confidence: beat consistency x onset pulse clarity
    pulse = librosa.beat.plp(onset_envelope=oenv, sr=sr, hop_length=HOP)
    clarity = float(np.clip(np.mean(pulse) / (np.max(pulse) + 1e-9) * 2.0, 0, 1))
    conf = float(np.clip(0.75 * ratio + 0.25 * clarity, 0, 1))
    return round(bpm, 2), round(conf, 3)


# ----------------------------------------------------------------- key
def _zscore(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return (v - v.mean()) / (v.std() + 1e-12)


def key_scores(chroma_profile: np.ndarray) -> np.ndarray:
    """Return 24 scores: index pc for major, 12+pc for minor (mean Pearson r over profiles)."""
    c = _zscore(chroma_profile)
    scores = np.zeros(24)
    for major, minor in _PROFILES.values():
        maj, mnr = _zscore(major), _zscore(minor)
        for pc in range(12):
            scores[pc] += np.dot(c, np.roll(maj, pc)) / 12
            scores[12 + pc] += np.dot(c, np.roll(mnr, pc)) / 12
    return scores / len(_PROFILES)


def detect_key(y: np.ndarray, sr: int = SR, chunk_seconds: float = 45.0) -> tuple[str | None, float]:
    import librosa

    if y.size < sr * 5 or float(np.max(np.abs(y))) < 1e-4:
        return None, 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # Tuning from (at most) 90 s in the middle of the track
        mid, half = y.size // 2, int(45 * sr)
        excerpt = y[max(0, mid - half): mid + half]
        # deviation from A=440 Hz in semitones (-0.5 .. 0.5); cqt expects fractions of a 1/36-octave bin
        tuning = float(librosa.estimate_tuning(y=excerpt, sr=sr, bins_per_octave=12))
        # Process in chunks so memory stays bounded (~150 MB) regardless of track length
        acc = np.zeros(12)
        frames = []
        step = int(chunk_seconds * sr)
        for start in range(0, y.size, step):
            seg = y[start:start + step]
            if seg.size < sr * 2:
                continue
            y_h = librosa.effects.harmonic(seg, margin=2.0)
            chroma = librosa.feature.chroma_cqt(
                y=y_h, sr=sr, hop_length=HOP, tuning=tuning * 3, bins_per_octave=36,
                fmin=librosa.note_to_hz("C2"), n_octaves=6,
            )
            rms = librosa.feature.rms(y=y_h, hop_length=HOP)[0]
            n = min(chroma.shape[1], rms.size)
            frames.append((chroma[:, :n], rms[:n]))
    peak = max((float(r.max()) for _, r in frames), default=0.0)
    if peak <= 0:
        return None, 0.0
    for chroma, rms in frames:
        w = rms / peak
        w[w < 0.05] = 0  # ignore near-silence
        acc += (chroma * w).sum(axis=1)
    if acc.sum() == 0:
        return None, 0.0
    profile = np.sqrt(acc)
    scores = key_scores(profile)
    order = np.argsort(scores)[::-1]
    best, second = int(order[0]), int(order[1])
    key = Key(best % 12, best >= 12)
    margin = float(scores[best] - scores[second])
    conf = float(np.clip(scores[best] * 0.6 + margin * 4.0, 0, 1))
    return key.code, round(conf, 3)


# ----------------------------------------------------------------- entry point
def analyze_file(path: str, bpm_min: float = 70, bpm_max: float = 180,
                 do_bpm: bool = True, do_key: bool = True) -> AnalysisResult:
    """Analyse tempo and/or key. A part that is switched off is skipped entirely (the key detection
    is the expensive part, so a BPM-only analysis is several times faster) and returned as None."""
    y, duration = load_audio(path)
    bpm, bconf = detect_bpm(y, SR, bpm_min, bpm_max) if do_bpm else (None, 0.0)
    key, kconf = detect_key(y, SR) if do_key else (None, 0.0)
    return AnalysisResult(bpm=bpm, bpm_confidence=bconf, key=key, key_confidence=kconf, duration=round(duration, 2))
