"""Song structure: section boundaries, grouping of repeated parts, functional labels and a confidence value.

Method: Laplacian structural segmentation (B. McFee & D. Ellis, "Analyzing song structure with spectral
clustering", ISMIR 2014), the approach shown in librosa's documentation, plus a few steps for DJ music:
  1. beat-synchronous features: constant-Q spectrogram (harmony), MFCC (timbre), loudness (energy)
  2. recurrence (self-similarity) matrix of the CQT, smoothed along time lags   -> "which beats sound alike"
  3. path similarity between neighbouring beats (MFCC + loudness)              -> "where the sound changes"
  4. spectral clustering of the combined graph (Laplacian eigenvectors + k-means). The number of kinds
     of part (k) is chosen per song from the eigengap of the Laplacian, no longer fixed by the duration.
  5. novelty curve (Foote checkerboard kernel over harmony + timbre + energy): every boundary moves to the
     sharpest change nearby, and strong energy jumps that the clustering missed (drops, breaks) are added
  6. boundaries snap to bars, preferably to 4- and 8-bar phrases (downbeat phase and phrase offset are
     estimated from the boundaries themselves), as long as the music changes there as well
  7. a group whose occurrences differ a lot in loudness is split (a quiet and a loud take of the same
     chords are different parts, e.g. a break and a drop)
Beats in the same cluster belong to the same kind of part, so "Chorus 1" and "Chorus 2" share a group.

Functional labels are a heuristic on top of the groups (no model is trained on annotated songs):
  * chorus  = the loudest group that occurs at least twice; drop = such a chorus that mostly starts with a
              big energy jump out of a quieter part
  * buildup = a part right before a chorus/drop whose loudness rises clearly
  * verse   = the most frequent other repeating group (a repeating group right before a chorus = pre-chorus)
  * intro / outro = a short, quieter first / last section that is not a chorus
  * break   = a quiet section (much below the song's average loudness), bridge = any other one-off part
"conf" (0..1) combines how sharp the start boundary is, how uniform the section's beats are and how sure
the label rule is; the deck draws unsure sections fainter.

MSAF (the Music Structure Analysis Framework) implements the same clustering ("scluster"), but it is no
longer maintained and pins old librosa versions; using librosa + scipy directly keeps KeyTool installable.
"""
from __future__ import annotations

import string

import numpy as np

LABELS = ("intro", "verse", "prechorus", "buildup", "chorus", "drop", "bridge", "break", "outro")
MIN_SECTION = 8.0          # seconds; shorter detections are merged into a neighbour
MIN_DURATION = 45.0        # shorter files (samples, jingles) get no sections
K_MIN = 3
NOVELTY_WIDTH = 16         # beats on each side of the checkerboard kernel (4 bars)
SPLIT_DB = 5.0             # a group whose takes differ by this much loudness is two parts
EXTRA_JUMP_DB = 4.0        # an energy jump this big becomes a boundary even if the clustering missed it
DROP_JUMP_DB = 5.0
ONSET_LAG = 0.08
GRID_CONTRAST = 1.15       # kick pulse on the best beat phase vs. a typical phase (steady tempo test)
EXTRA_PROMINENCE = 0.2     # a novelty peak this prominent splits a section even inside one cluster
EDGE_MIN = 2.0             # a short intro/outro is kept if its boundary is sharp
SAME_PART = 0.35           # recurrence links between two sections (0..1) to count as a repeat of one part
BUILDUP_RISE_DB = 3.0
PHRASE_BONUS = ((8, 1, 0.45), (8, 2, 0.3), (4, 1, 0.15))   # (every n bars, reach in bars, bonus)
LABEL_SURE = {"intro": 0.85, "outro": 0.85, "chorus": 0.9, "drop": 0.85, "verse": 0.8, "buildup": 0.75,
              "prechorus": 0.7, "break": 0.7, "bridge": 0.55}     # how reliable each label rule is


def _n_clusters(duration: float) -> int:
    """Typical number of kinds of part for a song of this length (prior for the eigengap choice)."""
    return 4 if duration < 200 else 5 if duration < 360 else 6


def _choose_k(evals: np.ndarray, duration: float, n: int) -> int:
    """Number of clusters from the eigengap of the normalised Laplacian, nudged towards the typical value."""
    kmax = int(min(max(4, duration // 20), 10, n // 4, len(evals) - 1))
    if kmax < K_MIN:
        return kmax
    prior = _n_clusters(duration)
    best, score = K_MIN, -1.0
    for k in range(K_MIN, kmax + 1):
        gap = float(evals[k] - evals[k - 1])
        s = gap * (1.0 - 0.06 * abs(k - prior))
        if s > score:
            best, score = k, s
    return best


def _zscore(F: np.ndarray) -> np.ndarray:
    F = np.atleast_2d(F).astype(float)
    sd = F.std(axis=1, keepdims=True)
    return (F - F.mean(axis=1, keepdims=True)) / np.where(sd > 1e-9, sd, 1.0)


def _stack(blocks: list[np.ndarray]) -> np.ndarray:
    """Harmony, timbre and energy per beat, each block standardised and weighted equally."""
    return np.vstack([_zscore(b) / np.sqrt(max(1, np.atleast_2d(b).shape[0])) for b in blocks])


def _novelty(blocks: list[np.ndarray], width: int = NOVELTY_WIDTH) -> np.ndarray:
    """Foote novelty per beat, 0..1: how strongly the music before beat i differs from the music after it."""
    F = _stack(blocks)
    F = F / np.maximum(np.linalg.norm(F, axis=0, keepdims=True), 1e-9)
    S = F.T @ F
    n = S.shape[0]
    w = max(2, min(width, n // 4))
    ax = np.arange(-w, w) + 0.5
    g = np.exp(-(ax / (0.5 * w)) ** 2)
    K = np.outer(g, g) * np.outer(np.sign(ax), np.sign(ax))      # + on the diagonal blocks, - off them
    P = np.pad(S, w, mode="edge")
    nov = np.array([np.sum(K * P[i:i + 2 * w, i:i + 2 * w]) for i in range(n)])
    nov = np.maximum(nov, 0)
    return nov / nov.max() if nov.max() > 0 else nov


def detect_sections(y: np.ndarray, sr: int, duration: float | None = None, k: int | None = None,
                    bpm: float | None = None) -> list[dict]:
    """[{start, end, group, label, conf}] in seconds; [] if the file is too short or has no clear structure.
    bpm (optional, e.g. from the tempo analysis) steadies the beat grid used for bar/phrase snapping."""
    import librosa
    import scipy.linalg
    import scipy.ndimage
    import scipy.signal
    import scipy.sparse.csgraph
    from scipy.cluster.vq import kmeans2

    duration = float(duration if duration is not None else len(y) / sr)
    if duration < MIN_DURATION or not np.any(y):
        return []
    hop = 512
    C = librosa.amplitude_to_db(np.abs(librosa.cqt(y=y, sr=sr, hop_length=hop, bins_per_octave=12,
                                                   n_bins=6 * 12)), ref=np.max)
    nframes = C.shape[1]
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    kw = {"bpm": float(bpm)} if bpm else {}
    _, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop, trim=False, **kw)
    beats = np.asarray(beats, dtype=int)
    # kick drum onsets (below ~150 Hz) give the phase of a steady beat grid (see _beat_grid)
    low = librosa.onset.onset_strength(S=librosa.power_to_db(librosa.feature.melspectrogram(
        y=y, sr=sr, hop_length=hop, n_mels=64, fmax=sr / 2)[:6]), sr=sr, hop_length=hop)
    steady = _beat_grid(librosa.frames_to_time(beats, sr=sr, hop_length=hop), low, sr, hop, bpm, duration) \
        if len(beats) >= 32 else None
    if steady is not None:                       # steady tempo: features per beat of the ideal grid
        beats = np.unique(np.round(steady * sr / hop).astype(int))
        beats = beats[(beats >= 0) & (beats < nframes)]
    elif len(beats) < 32:                        # no usable beat grid: fall back to 0.5 s frames
        beats = np.arange(0, nframes, max(1, int(0.5 * sr / hop)))
    frames = librosa.util.fix_frames(beats, x_min=0, x_max=nframes)
    Csync = librosa.util.sync(C, frames, aggregate=np.median)
    Msync = librosa.util.sync(librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13), frames)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0][:nframes]
    loud_f = 20 * np.log10(rms + 1e-5)
    Lsync = librosa.util.sync(loud_f[None, :], frames)[0]
    n = Csync.shape[1]
    if n < 16:
        return []
    times = librosa.frames_to_time(frames, sr=sr, hop_length=hop)[: n + 1].astype(float)
    times[0], times[-1] = 0.0, duration

    # recurrence graph (which beats sound alike), smoothed along diagonals
    R = librosa.segment.recurrence_matrix(Csync, width=3, mode="affinity", sym=True)
    Rf = librosa.segment.timelag_filter(scipy.ndimage.median_filter)(R, size=(1, 7))
    # local path graph: continuity between neighbouring beats (timbre and energy)
    dm = np.sum(np.diff(Msync, axis=1) ** 2, axis=0)
    dm = dm / (np.median(dm) or 1.0)
    zl = _zscore(Lsync)[0]
    sim = np.exp(-(dm + 2.0 * np.diff(zl) ** 2))
    Rp = np.diag(sim, k=1) + np.diag(sim, k=-1)
    deg_p, deg_r = Rp.sum(axis=1), Rf.sum(axis=1)
    mu = deg_p.dot(deg_p + deg_r) / max(np.sum((deg_p + deg_r) ** 2), 1e-9)
    A = mu * Rf + (1 - mu) * Rp
    L = scipy.sparse.csgraph.laplacian(A, normed=True)
    evals, evecs = scipy.linalg.eigh(L)
    evecs = scipy.ndimage.median_filter(evecs, size=(9, 1))
    k = min(k or _choose_k(evals, duration, n), n // 4)
    if k < 2:
        return []
    cnorm = np.cumsum(evecs ** 2, axis=1) ** 0.5
    X = evecs[:, :k] / np.maximum(cnorm[:, k - 1:k], 1e-9)
    _, ids = kmeans2(X, k, seed=0, minit="++")
    ids = np.asarray(ids, dtype=int)

    # boundaries: cluster changes, moved to the sharpest change within a bar, plus missed energy jumps
    nov = _novelty([Csync, Msync, Lsync[None, :]])
    raw = [i for i in range(1, n) if ids[i] != ids[i - 1]]
    bounds = set()
    for b in raw:                    # within two bars; a little preference for staying close
        lo, hi = max(1, b - 8), min(n - 1, b + 8)
        cand = np.arange(lo, hi + 1)
        bounds.add(int(cand[np.argmax(nov[cand] - 0.01 * np.abs(cand - b))]))
    peaks, props = scipy.signal.find_peaks(nov, distance=8, prominence=0.05)
    for pk, prom in zip(peaks, props["prominences"]):
        if pk < 2 or pk > n - 2 or any(abs(pk - b) < 8 for b in bounds):
            continue
        jump = abs(np.mean(Lsync[max(0, pk - 8):pk]) - np.mean(Lsync[pk:pk + 8]))
        if prom >= EXTRA_PROMINENCE or (nov[pk] >= 0.5 and jump >= EXTRA_JUMP_DB):
            bounds.add(int(pk))
    btimes = sorted(float(times[b]) for b in bounds)

    def nov_at(t: float) -> float:
        i = int(np.clip(np.searchsorted(times, t, side="right") - 1, 0, n - 1))
        j = min(n - 1, i + 1)        # a boundary between two beats: take the sharper side
        return float(max(nov[i], nov[j] if abs(times[j] - t) < 0.05 else 0.0))

    def nov_near(t: float) -> float:      # the novelty peak is a beat or so wide: look one beat around
        return max(nov_at(t - 0.4 * period), nov_at(t), nov_at(t + 0.4 * period))

    period = float(np.median(np.diff(times[1:-1]))) if n > 3 else 0.5
    if steady is not None:
        onset = low[np.clip(np.round(steady * sr / hop).astype(int), 0, len(low) - 1)]
        btimes = _snap_to_phrases(btimes, steady, nov_near, onset)
    edges = [0.0] + [t for t in btimes if 0.5 < t < duration - 0.5] + [duration]
    edges = sorted(set(round(t, 3) for t in edges))

    # sections: group = majority cluster of their beats
    mids = (times[:-1] + times[1:]) / 2
    segs = []
    for t0, t1 in zip(edges, edges[1:]):
        m = (mids >= t0) & (mids < t1)
        if not np.any(m):
            m = np.zeros(n, bool); m[int(np.clip(np.searchsorted(mids, t0), 0, n - 1))] = True
        vals, cnt = np.unique(ids[m], return_counts=True)
        segs.append({"g": int(vals[np.argmax(cnt)]), "t0": t0, "t1": t1,
                     "s": int(np.argmax(m)), "e": int(len(m) - np.argmax(m[::-1]))})
    feats = _stack([Csync, Msync, Lsync[None, :]])
    segs = _split_groups(segs, Rf)
    segs = _join_same(segs)
    segs = _merge_short(segs, feats, [nov_near(sg["t0"]) for sg in segs])

    rtimes = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)

    def db(t0, t1):
        m = (rtimes >= t0) & (rtimes < t1)
        return float(20 * np.log10(np.mean(rms[m]) + 1e-5)) if np.any(m) else -100.0

    for s in segs:
        s["loud"] = db(s["t0"], s["t1"])
    segs = _split_by_energy(segs)
    segs = _join_same(segs)
    for i, s in enumerate(segs):
        s["loud"] = db(s["t0"], s["t1"])
        third = (s["t1"] - s["t0"]) / 3
        s["rise"] = db(s["t1"] - third, s["t1"]) - db(s["t0"], s["t0"] + third)
        s["jump"] = db(s["t0"], s["t0"] + 4) - db(segs[i - 1]["t1"] - 4, segs[i - 1]["t1"]) if i else 0.0
        m = (mids >= s["t0"]) & (mids < s["t1"])
        s["purity"] = float(np.mean(ids[m] == np.bincount(ids[m]).argmax())) if np.any(m) else 0.5
        s["edge"] = 1.0 if i == 0 else min(1.0, nov_at(s["t0"]) / max(np.percentile(nov, 95), 1e-9))
    return _label(segs, duration)


def _beat_grid(beat_times: np.ndarray, low: np.ndarray, sr: int, hop: int, bpm: float | None,
               duration: float) -> np.ndarray | None:
    """Steady beat grid for bar snapping: tempo from the analysis (or the tracked beats), phase from the
    kick drum (onsets below ~150 Hz). None if the tracked beats do not follow a steady tempo (live music)."""
    import scipy.ndimage
    bt = np.asarray(beat_times, dtype=float)
    if len(bt) < 16:
        return None
    period = float(np.median(np.diff(bt)))
    if bpm and abs(np.log2(period * bpm / 60.0)) < 0.1:      # same metrical level: trust the analysis
        period = 60.0 / bpm
    else:
        period = float(np.polyfit(np.arange(len(bt)), bt, 1)[0])
    peak = scipy.ndimage.maximum_filter1d(low, size=5)     # onset peaks come a frame or two late
    fps = sr / hop

    def strengths(per):
        grid = np.arange(0, duration, per)
        offs = np.linspace(0, per, 48, endpoint=False)
        f = np.round((offs[:, None] + grid[None, :]) * fps).astype(int)
        return offs, peak[np.clip(f, 0, len(peak) - 1)].mean(axis=1)
    # fine-tune the tempo (a 0.5 % error already drifts by half a beat over a song) and find the phase
    best = None
    for per in period * (1 + np.linspace(-0.012, 0.012, 25)):
        offs, st = strengths(per)
        contrast = st.max() / max(np.median(st), 1e-9)
        if best is None or contrast > best[0]:
            top = offs[st >= st.max() - 1e-9]           # the max filter makes a plateau: take its middle
            best = (contrast, per, float(top[len(top) // 2]))
    contrast, period, off = best
    # steady tempo = a clear pulse on the ideal grid; with a drifting tempo every phase looks alike
    if contrast < GRID_CONTRAST:
        return None
    # onset peaks lag the attack by ~80 ms (analysis window + difference); measured on drum loops and cues
    off = (off - ONSET_LAG) % period
    return np.arange(off, duration, period)


def _snap_to_phrases(btimes: list[float], beat_times: np.ndarray, nov_at, beat_onset: np.ndarray) -> list[float]:
    """Move boundaries onto bar lines, preferably 4-/8-bar phrase lines where the music changes too."""
    beat_times = np.asarray(beat_times, dtype=float)
    if len(beat_times) < 8 or not btimes:
        return btimes
    near = [int(np.argmin(np.abs(beat_times - t))) for t in btimes]
    w = [nov_at(t) + 0.1 for t in btimes]
    # downbeat phase: the bar grid on which most (sharp) boundaries already lie; then the louder beats
    def phase_cost(ph):
        d = [min((j - ph) % 4, 4 - (j - ph) % 4) for j in near]
        return float(np.dot(w, d)), -float(np.mean(beat_onset[ph::4]))
    phase = min(range(4), key=phase_cost)
    bars = beat_times[phase::4]
    if len(bars) < 4:
        return btimes
    bar_of = [int(np.argmin(np.abs(bars - t))) for t in btimes]

    def phrase_score(o):
        return sum(wi * (1.0 if (m - o) % 8 == 0 else 0.5 if (m - o) % 4 == 0 else 0.0)
                   for wi, m in zip(w, bar_of))
    offset = max(range(8), key=phrase_score)
    out = []
    for t, m in zip(btimes, bar_of):
        cands = {m: 0.0}
        here = nov_at(bars[m])
        # phrase lines win over a slightly sharper change one bar away (the novelty peak is a bar wide)
        for line, reach, bonus in PHRASE_BONUS:
            for c in range(m - reach, m + reach + 1):
                # ... but only where the music clearly changes as well (at least half as sharply)
                if 0 <= c < len(bars) and (c - offset) % line == 0 and nov_at(bars[c]) >= 0.5 * here:
                    cands[c] = max(cands.get(c, 0.0), bonus)
        best = max(cands, key=lambda c: nov_at(bars[c]) + cands[c] - 0.05 * abs(c - m))
        out.append(float(bars[best]))
    return sorted(set(out))


def _join_same(segs: list[dict]) -> list[dict]:
    """Neighbours in the same group become one section."""
    out: list[dict] = []
    for s in segs:
        if out and out[-1]["g"] == s["g"]:
            out[-1].update(e=s["e"], t1=s["t1"])
        else:
            out.append(dict(s))
    return out


def _mean(feats: np.ndarray, s: dict) -> np.ndarray:
    return feats[:, s["s"]:max(s["e"], s["s"] + 1)].mean(axis=1)


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-9))


def _links(R: np.ndarray, a: dict, b: dict) -> float:
    """Repetition between two sections: how strongly the beats of each find a recurring beat in the other."""
    blk = R[a["s"]:max(a["e"], a["s"] + 1), b["s"]:max(b["e"], b["s"] + 1)]
    return float((blk.max(axis=1).mean() + blk.max(axis=0).mean()) / 2) if blk.size else 0.0


def _split_groups(segs: list[dict], R: np.ndarray) -> list[dict]:
    """Sections of one cluster that do not repeat each other (e.g. verse and pre-chorus, which the clustering
    often lumps together, or an intro and a bridge with the same sound) become separate groups; real repeats
    share one. Long sections are the references; short pieces join the closest reference of their cluster."""
    R = R / max(float(R.max()), 1e-9)
    refs: dict[int, list[tuple[int, dict]]] = {}
    next_id = max(s["g"] for s in segs) + 1
    for s in sorted(segs, key=lambda s: -(s["t1"] - s["t0"])):
        mine = refs.setdefault(s["g"], [])
        best = max(mine, key=lambda r: _links(R, r[1], s), default=None)
        if best is None:
            mine.append((s["g"], s))
        elif _links(R, best[1], s) >= SAME_PART or s["t1"] - s["t0"] < MIN_SECTION:
            s["g"] = best[0]
        else:
            mine.append((next_id, s))
            s["g"] = next_id
            next_id += 1
    return segs


def _merge_short(segs: list[dict], feats: np.ndarray, edge_sharp: list[float] | None = None) -> list[dict]:
    """Merge sections shorter than MIN_SECTION into the more similar neighbour (a short intro or outro with a
    sharp boundary stays)."""
    def mean(s):
        return _mean(feats, s)
    sharp = {round(s["t0"], 3): v for s, v in zip(segs, edge_sharp or [])}
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            length = s["t1"] - s["t0"]
            if length >= MIN_SECTION:
                continue
            if length >= EDGE_MIN and ((i == 0 and sharp.get(round(segs[1]["t0"], 3), 0) >= 0.4) or
                                       (i == len(segs) - 1 and sharp.get(round(s["t0"], 3), 0) >= 0.4)):
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
            merged = {"g": keep["g"], "s": min(s["s"], o["s"]), "e": max(s["e"], o["e"]),
                      "t0": min(s["t0"], o["t0"]), "t1": max(s["t1"], o["t1"])}
            lo = min(i, j)
            segs[lo:lo + 2] = [merged]
            changed = True
            break
    return _join_same(segs)


def _split_by_energy(segs: list[dict]) -> list[dict]:
    """A group whose takes fall into a quiet and a loud cluster (gap >= SPLIT_DB) becomes two groups."""
    next_id = max(s["g"] for s in segs) + 1
    for g in sorted({s["g"] for s in segs}):
        mine = [s for s in segs if s["g"] == g]
        if len(mine) < 2:
            continue
        levels = sorted(s["loud"] for s in mine)
        gaps = np.diff(levels)
        i = int(np.argmax(gaps))
        if gaps[i] >= SPLIT_DB:
            cut = (levels[i] + levels[i + 1]) / 2
            for s in mine:
                if s["loud"] > cut:
                    s["g"] = next_id
            next_id += 1
    return segs


def _label(segs: list[dict], duration: float) -> list[dict]:
    """Group letters in order of appearance (A, B, C ...), functional labels and confidence (see docstring)."""
    letters = {}
    for s in segs:
        letters.setdefault(s["g"], string.ascii_uppercase[len(letters) % 26])
    count: dict[int, int] = {}
    for s in segs:
        count[s["g"]] = count.get(s["g"], 0) + 1
    loud_of = {g: np.mean([s["loud"] for s in segs if s["g"] == g]) for g in count}
    song_loud = float(np.average([s["loud"] for s in segs], weights=[s["t1"] - s["t0"] for s in segs]))
    last = len(segs) - 1

    def takes(g):
        return [i for i, s in enumerate(segs) if s["g"] == g]
    repeating = [g for g, c in count.items() if c >= 2 and not set(takes(g)) <= {0, last}]
    chorus = max(repeating, key=lambda g: loud_of[g]) if repeating else None

    def is_chorus(i):
        return chorus is not None and 0 <= i <= last and segs[i]["g"] == chorus
    labels: list[str | None] = [None] * len(segs)
    for i in takes(chorus) if chorus is not None else []:
        labels[i] = "chorus"
    # intro / outro: short first / last part that is quiet or heard nowhere else
    for i in (0, last):
        s = segs[i]
        only_edges = set(takes(s["g"])) <= {0, last}
        if labels[i] is None and s["t1"] - s["t0"] < 0.25 * duration and \
                (s["loud"] < song_loud - 2.5 or only_edges):
            labels[i] = "intro" if i == 0 else "outro"

    # drop: the chorus mostly comes in with a big energy jump, or out of a quieter part that builds up
    def drop_entry(i):
        prev = segs[i - 1]
        gain = segs[i]["loud"] - prev["loud"]
        return gain >= DROP_JUMP_DB or (prev["rise"] >= BUILDUP_RISE_DB and gain >= BUILDUP_RISE_DB)
    entries = [i for i in takes(chorus) if i > 0] if chorus is not None else []
    drop = bool(entries) and sum(drop_entry(i) for i in entries) * 2 > len(entries)
    if drop:
        for i in entries:
            labels[i] = "drop"
            if labels[i - 1] is None and segs[i - 1]["rise"] >= BUILDUP_RISE_DB \
                    and segs[i - 1]["t1"] - segs[i - 1]["t0"] <= 0.2 * duration:
                labels[i - 1] = "buildup"
    # pre-chorus: a repeating group that always sits between another part (the verse) and a chorus
    pre = None
    for g in repeating:
        idx = takes(g)
        if g != chorus and all(labels[i] is None and is_chorus(i + 1) and i > 0 and not is_chorus(i - 1)
                               and labels[i - 1] not in ("intro", "buildup") for i in idx):
            pre = g
            break
    for i in takes(pre) if pre is not None else []:
        labels[i] = "prechorus"
    # verse: the part that leads into the pre-chorus (or straight into the chorus)
    target = "prechorus" if pre is not None else "chorus"
    slots = [i for i in range(len(segs) - 1) if labels[i] is None and labels[i + 1] in (target, "drop")]
    if pre is None and slots:
        rep = [i for i in slots if count[segs[i]["g"]] >= 2]
        if rep:                     # the repeating part in front of the choruses
            g = max({segs[i]["g"] for i in rep}, key=lambda g: (sum(segs[i]["g"] == g for i in rep), -min(takes(g))))
            slots = takes(g)
        else:                       # all one-offs: the parts before the first two choruses
            firsts = sorted(i for i in range(len(segs)) if labels[i] in ("chorus", "drop"))[:2]
            slots = [i for i in slots if firsts and i < firsts[-1]]
    verse_groups = {segs[i]["g"] for i in slots}
    for i, s in enumerate(segs):
        if labels[i] is None and s["g"] in verse_groups:
            labels[i] = "verse"
    # the rest: quiet = break, otherwise bridge
    for i, s in enumerate(segs):
        if labels[i] is None:
            labels[i] = "break" if s["loud"] < song_loud - 4.4 else "bridge"

    out = []
    for s, label in zip(segs, labels):
        conf = 0.4 * s["edge"] + 0.35 * s["purity"] + 0.25 * LABEL_SURE[label]
        out.append({"start": round(s["t0"], 2), "end": round(s["t1"], 2), "group": letters[s["g"]],
                    "label": label, "conf": round(float(np.clip(conf, 0, 1)), 2)})
    return out


def section_at(sections: list[dict] | None, seconds: float) -> dict | None:
    for s in sections or []:
        if s["start"] <= seconds < s["end"]:
            return s
    return None
