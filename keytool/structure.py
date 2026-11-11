"""Song structure: section boundaries, grouping of repeated parts and functional labels.

Method: Laplacian structural segmentation (B. McFee & D. Ellis, "Analyzing song structure with spectral
clustering", ISMIR 2014), the approach shown in librosa's documentation:
  1. beat-synchronous constant-Q spectrogram (harmony/timbre per beat)
  2. recurrence (self-similarity) matrix, smoothed along time lags  -> "which beats sound alike"
  3. MFCC path similarity between neighbouring beats                 -> "where the sound changes"
  4. spectral clustering of the combined graph (Laplacian eigenvectors + k-means)
Beats in the same cluster belong to the same kind of part, so "Chorus 1" and "Chorus 2" share a group.

Functional labels are a heuristic on top of the groups (no model is trained on annotated songs):
  * chorus  = the loudest group that occurs at least twice
  * verse   = the most frequent other repeating group (a repeating group right before a chorus = pre-chorus)
  * intro / outro = a short, quieter first / last section that is not a chorus
  * break   = a quiet section (much below the song's average loudness), bridge = any other one-off part
So the grouping is the reliable part; the names are a good first guess.

MSAF (the Music Structure Analysis Framework) implements the same algorithm ("scluster"), but it is no longer
maintained and pins old librosa versions; using librosa + scipy directly keeps KeyTool installable.
"""
from __future__ import annotations

import string

import numpy as np

LABELS = ("intro", "verse", "prechorus", "chorus", "bridge", "break", "outro")
MIN_SECTION = 8.0          # seconds; shorter detections are merged into a neighbour
MIN_DURATION = 45.0        # shorter files (samples, jingles) get no sections


def _n_clusters(duration: float) -> int:
    return 4 if duration < 200 else 5 if duration < 360 else 6


def detect_sections(y: np.ndarray, sr: int, duration: float | None = None, k: int | None = None) -> list[dict]:
    """[{start, end, group, label}] in seconds; [] if the file is too short or has no clear structure."""
    import librosa
    import scipy.linalg
    import scipy.ndimage
    import scipy.sparse.csgraph
    from scipy.cluster.vq import kmeans2

    duration = float(duration if duration is not None else len(y) / sr)
    if duration < MIN_DURATION or not np.any(y):
        return []
    hop = 512
    C = librosa.amplitude_to_db(np.abs(librosa.cqt(y=y, sr=sr, hop_length=hop, bins_per_octave=12,
                                                   n_bins=6 * 12)), ref=np.max)
    _, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop, trim=False)
    beats = np.asarray(beats, dtype=int)
    if len(beats) < 32:                          # no usable beat grid: fall back to 0.5 s frames
        beats = np.arange(0, C.shape[1], max(1, int(0.5 * sr / hop)))
    beats = librosa.util.fix_frames(beats, x_min=0, x_max=C.shape[1])
    Csync = librosa.util.sync(C, beats, aggregate=np.median)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13)
    Msync = librosa.util.sync(mfcc, beats)
    n = Csync.shape[1]
    if n < 16:
        return []

    # recurrence graph (which beats sound alike), smoothed along diagonals
    R = librosa.segment.recurrence_matrix(Csync, width=3, mode="affinity", sym=True)
    Rf = librosa.segment.timelag_filter(scipy.ndimage.median_filter)(R, size=(1, 7))
    # local path graph (continuity between neighbouring beats)
    dist = np.sum(np.diff(Msync, axis=1) ** 2, axis=0)
    sigma = np.median(dist) or 1.0
    sim = np.exp(-dist / sigma)
    Rp = np.diag(sim, k=1) + np.diag(sim, k=-1)
    deg_p, deg_r = Rp.sum(axis=1), Rf.sum(axis=1)
    mu = deg_p.dot(deg_p + deg_r) / max(np.sum((deg_p + deg_r) ** 2), 1e-9)
    A = mu * Rf + (1 - mu) * Rp
    L = scipy.sparse.csgraph.laplacian(A, normed=True)
    _, evecs = scipy.linalg.eigh(L)
    evecs = scipy.ndimage.median_filter(evecs, size=(9, 1))
    k = min(k or _n_clusters(duration), n // 4)
    if k < 2:
        return []
    cnorm = np.cumsum(evecs ** 2, axis=1) ** 0.5
    X = evecs[:, :k] / np.maximum(cnorm[:, k - 1:k], 1e-9)
    _, ids = kmeans2(X, k, seed=0, minit="++")

    times = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    times = np.append(times[:n], duration) if len(times) > n else np.append(times, duration)[: n + 1]
    segs = []
    for i, g in enumerate(ids):
        if segs and segs[-1]["g"] == g:
            segs[-1]["end"] = i + 1
        else:
            segs.append({"g": int(g), "start": i, "end": i + 1})
    # beat index -> seconds
    for s in segs:
        s["t0"], s["t1"] = float(times[s["start"]]), float(times[min(s["end"], len(times) - 1)])
    segs[0]["t0"], segs[-1]["t1"] = 0.0, duration
    segs = _merge_short(segs, Csync)

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rtimes = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    for s in segs:
        m = (rtimes >= s["t0"]) & (rtimes < s["t1"])
        s["loud"] = float(np.mean(rms[m])) if np.any(m) else 0.0
    return _label(segs, duration)


def _merge_short(segs: list[dict], feats: np.ndarray) -> list[dict]:
    """Merge sections shorter than MIN_SECTION into the more similar neighbour."""
    def mean(s):
        return feats[:, s["start"]:s["end"]].mean(axis=1)
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            if s["t1"] - s["t0"] >= MIN_SECTION:
                continue
            if i == 0:
                j = 1
            elif i == len(segs) - 1:
                j = i - 1
            else:
                a, b = segs[i - 1], segs[i + 1]
                j = i - 1 if np.linalg.norm(mean(a) - mean(s)) <= np.linalg.norm(mean(b) - mean(s)) else i + 1
            o = segs[j]
            keep = o if (o["t1"] - o["t0"]) >= (s["t1"] - s["t0"]) else s
            merged = {"g": keep["g"], "start": min(s["start"], o["start"]), "end": max(s["end"], o["end"]),
                      "t0": min(s["t0"], o["t0"]), "t1": max(s["t1"], o["t1"])}
            lo = min(i, j)
            segs[lo:lo + 2] = [merged]
            changed = True
            break
    # neighbours that ended up in the same group become one section
    out: list[dict] = []
    for s in segs:
        if out and out[-1]["g"] == s["g"]:
            out[-1].update(end=s["end"], t1=s["t1"])
        else:
            out.append(dict(s))
    return out


def _label(segs: list[dict], duration: float) -> list[dict]:
    """Group letters in order of appearance (A, B, C ...) and functional labels (see module docstring)."""
    letters = {}
    for s in segs:
        letters.setdefault(s["g"], string.ascii_uppercase[len(letters) % 26])
    count: dict[int, int] = {}
    for s in segs:
        count[s["g"]] = count.get(s["g"], 0) + 1
    loud_of = {g: np.mean([s["loud"] for s in segs if s["g"] == g]) for g in count}
    song_loud = np.average([s["loud"] for s in segs], weights=[s["t1"] - s["t0"] for s in segs]) or 1e-9
    repeating = [g for g, c in count.items() if c >= 2]
    chorus = max(repeating, key=lambda g: loud_of[g]) if repeating else None
    edge = {0, len(segs) - 1}
    # repeating groups in the body of the song (a group heard only as intro and outro does not count)
    others = [g for g in repeating if g != chorus
              and any(i not in edge for i, s in enumerate(segs) if s["g"] == g)]
    # a repeating group that (almost) always comes right before a chorus is a pre-chorus
    pre = None
    for g in (others if len(others) >= 2 else []):       # only with a verse besides it
        idx = [i for i, s in enumerate(segs) if s["g"] == g]
        if chorus is not None and len(idx) >= 2 and all(i + 1 < len(segs) and segs[i + 1]["g"] == chorus for i in idx):
            pre = g
            break
    verse_cands = [g for g in others if g != pre]
    verse = max(verse_cands, key=lambda g: (count[g], -min(i for i, s in enumerate(segs) if s["g"] == g))) \
        if verse_cands else None

    out = []
    for i, s in enumerate(segs):
        g, length = s["g"], s["t1"] - s["t0"]
        quiet = s["loud"] < 0.75 * song_loud
        if g == chorus:
            label = "chorus"
        elif i == 0 and length < 0.25 * duration and (quiet or count[g] == 1):
            label = "intro"
        elif i == len(segs) - 1 and length < 0.25 * duration and (quiet or count[g] == 1):
            label = "outro"
        elif g == pre:
            label = "prechorus"
        elif g == verse:
            label = "verse"
        elif s["loud"] < 0.6 * song_loud:
            label = "break"
        else:
            label = "bridge"
        out.append({"start": round(s["t0"], 2), "end": round(s["t1"], 2), "group": letters[g], "label": label})
    return out


def section_at(sections: list[dict] | None, seconds: float) -> dict | None:
    for s in sections or []:
        if s["start"] <= seconds < s["end"]:
            return s
    return None
