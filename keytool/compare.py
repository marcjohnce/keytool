"""Compare the classic and the fast key detection on real files:  python -m keytool --compare-key PATH [--limit N]

For every sampled file both methods run on the same decoded audio. If the file already carries a key
tag (e.g. from Mixed In Key, rekordbox, Serato), each method is also checked against it.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from .keys import Key


def _compare_worker(path: str) -> dict:
    import warnings
    warnings.simplefilter("ignore")
    from .library import _worker_init
    _worker_init()                          # one BLAS thread per process (before numpy is loaded)
    from .analysis import SR, detect_key, load_audio
    from .tags import parse_tag_key, read_metadata
    y, _ = load_audio(path)
    t = time.perf_counter(); classic, cconf = detect_key(y, SR, "classic"); tc = time.perf_counter() - t
    t = time.perf_counter(); fast, fconf = detect_key(y, SR, "fast"); tf = time.perf_counter() - t
    return dict(path=path, classic=classic, fast=fast, t_classic=tc, t_fast=tf,
                tag=parse_tag_key(read_metadata(path).tag_key))


def relation(a: str | None, b: str | None) -> str:
    """How two keys relate on the Camelot wheel (mistakes between neighbours are the common, mild ones)."""
    ka, kb = Key.from_code(a), Key.from_code(b)
    if not ka or not kb:
        return "–"
    if ka == kb:
        return "gleich"
    if ka.camelot_number == kb.camelot_number:
        return "Paralleltonart (relativ)"
    if ka.minor == kb.minor and (ka.camelot_number - kb.camelot_number) % 12 in (1, 11):
        return "Quinte (Camelot ±1)"
    if ka.pc == kb.pc:
        return "gleicher Grundton, Dur/Moll"
    return "andere"


def sample_files(paths: list[str], limit: int) -> list[str]:
    from .library import iter_audio_files
    files = list(iter_audio_files(paths))
    if len(files) <= limit:
        return files
    step = len(files) / limit                       # evenly spread over the folder structure
    return [files[int(i * step)] for i in range(limit)]


def run(paths: list[str], limit: int = 25, workers: int | None = None, out=print) -> dict:
    from .config import Settings
    files = sample_files(paths, limit)
    if not files:
        out("Keine Audiodateien gefunden.")
        return {}
    n_workers = workers or Settings.load().worker_count()
    out(f"Vergleiche {len(files)} Titel mit beiden Verfahren ({n_workers} parallel) …\n")
    out(f"{'Datei':42s} {'Tag':>5s} {'klassisch':>9s} {'schnell':>8s}  {'Zeit klass./schnell':>20s}  Unterschied")
    rows = []
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(n_workers, len(files)), mp_context=ctx) as ex:
        futs = {ex.submit(_compare_worker, f): f for f in files}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:
                out(f"{os.path.basename(futs[fut])[:42]:42s}  Fehler: {e}")
                continue
            rows.append(r)
            cam = lambda c: Key.from_code(c).camelot() if c else "–"
            diff = "" if r["classic"] == r["fast"] else relation(r["classic"], r["fast"])
            out(f"{os.path.basename(r['path'])[:42]:42s} {cam(r['tag']):>5s} {cam(r['classic']):>9s} {cam(r['fast']):>8s}  "
                f"{r['t_classic']:8.1f}s / {r['t_fast']:5.1f}s  {diff}")
    if not rows:
        return {}
    n = len(rows)
    same = sum(r["classic"] == r["fast"] for r in rows)
    tagged = [r for r in rows if r["tag"]]
    c_tag = sum(r["classic"] == r["tag"] for r in tagged)
    f_tag = sum(r["fast"] == r["tag"] for r in tagged)
    tc, tf = sum(r["t_classic"] for r in rows), sum(r["t_fast"] for r in rows)
    out("\n— Zusammenfassung —")
    out(f"Gleiches Ergebnis klassisch/schnell: {same}/{n} ({same / n:.0%})")
    diffs = [relation(r["classic"], r["fast"]) for r in rows if r["classic"] != r["fast"]]
    if diffs:
        from collections import Counter
        out("Art der Abweichungen: " + ", ".join(f"{k}: {v}" for k, v in Counter(diffs).most_common()))
    if tagged:
        out(f"Übereinstimmung mit vorhandenen Tonart-Tags ({len(tagged)} Titel): "
            f"klassisch {c_tag}/{len(tagged)} ({c_tag / len(tagged):.0%}), schnell {f_tag}/{len(tagged)} ({f_tag / len(tagged):.0%})")
    out(f"Rechenzeit Tonart: klassisch {tc / n:.1f} s/Titel, schnell {tf / n:.1f} s/Titel → {tc / max(tf, 1e-9):.1f}× schneller")
    good = (same / n >= 0.9) and (not tagged or f_tag >= c_tag - max(1, round(0.03 * len(tagged))))
    out("")
    if good:
        out("Empfehlung: Das schnelle Verfahren liefert hier praktisch dieselben Ergebnisse. Aktivieren mit")
        out("  Einstellungen → Analyse → Tonart-Verfahren: Schnell   oder   --set-key-method fast")
    else:
        out("Empfehlung: Beim klassischen Verfahren bleiben – die Ergebnisse weichen hier spürbar ab.")
    return dict(n=n, same=same, tagged=len(tagged), classic_tag=c_tag, fast_tag=f_tag,
                t_classic=tc / n, t_fast=tf / n, recommend_fast=good)


def main(argv: list[str]) -> int:
    limit = 25
    paths = []
    it = iter(argv)
    for a in it:
        if a == "--limit":
            limit = int(next(it, "25"))
        elif not a.startswith("--"):
            paths.append(a)
    if not paths:
        print("Aufruf: python -m keytool --compare-key ORDNER_ODER_DATEI [...] [--limit 25]")
        return 2
    run(paths, limit)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
