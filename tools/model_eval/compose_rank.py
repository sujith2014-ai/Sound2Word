#!/usr/bin/env python3
"""Word recognition from COMPOSED templates: can glued sound-unit clips pick out the right word?
For every query clip (a whole spoken word) the script builds a template for each candidate word by gluing the unit clips of its
units (tools/s2w_units.py), time-normalises the template to the query length and ranks the candidates by DTW distance (mean-removed
MFCC c1..c12). Reports top-1 / top-5 / mean reciprocal rank. Inside the Docker build tree (needs numpy + the C feature tool).
usage: compose_rank.py QUERY_DIR UNIT_DIR [--extra QUERY_DIR2 ...] [--build build] [--only-exact]
QUERY_DIR/meta(_scored).json: items {word_ml, file, exact?}; UNIT_DIR/meta(_scored).json: items {word_ml (the unit text), file}."""
import argparse
import io
import json
import math
import os
import struct
import sys
import tempfile
import unicodedata
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import s2w_units as su  # noqa: E402
import s2w_viewer as v  # noqa: E402

NC = 12


def load_meta(d):
    p = os.path.join(d, "meta_scored.json" if os.path.exists(os.path.join(d, "meta_scored.json")) else "meta.json")
    return json.load(open(p, encoding="utf-8"))["items"]


def dct(n_in):
    k = np.arange(1, NC + 1)[:, None]
    n = np.arange(n_in)[None, :]
    return np.cos(math.pi / n_in * (n + 0.5) * k)


def feats(path, build, cache, D=[None]):
    if path in cache:
        return cache[path]
    res = v.run_tool(os.path.join(build, "s2w_features"), [path, "--out", "logmel"])
    if res.returncode != 0:
        return None
    m = np.array(v.parse_matrix(res.stdout, "logmel_raw"), dtype=float)
    if D[0] is None:
        D[0] = dct(m.shape[1])
    m = m - m.mean(axis=0, keepdims=True)
    f = (m @ D[0].T)[::2]                          # 20 ms hop
    cache[path] = f
    return f


def resample(x, n):
    idx = np.linspace(0, len(x) - 1, n)
    lo = np.floor(idx).astype(int)
    hi = np.minimum(lo + 1, len(x) - 1)
    w = (idx - lo)[:, None]
    return x[lo] * (1 - w) + x[hi] * w


def dtw(a, b, band=0.3):
    n, m = len(a), len(b)
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2))
    INF = 1e18
    prev = [INF] * (m + 1)
    prev[0] = 0.0
    w = max(3, int(band * max(n, m)))
    for i in range(1, n + 1):
        cur = [INF] * (m + 1)
        lo, hi = max(1, i - w), min(m, i + w)
        di = d[i - 1]
        for j in range(lo, hi + 1):
            cur[j] = di[j - 1] + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return prev[m] / (n + m)


def compose(units, clips, td, tag):
    rate, frames = None, []
    for u in units:
        c = clips.get(unicodedata.normalize("NFC", u))
        if c is None:
            return None
        with wave.open(c, "rb") as w:
            if rate is None:
                rate = w.getframerate()
            frames.append(w.readframes(w.getnframes()))
    p = os.path.join(td, "c_%s.wav" % tag)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query_dir")
    ap.add_argument("unit_dir")
    ap.add_argument("--extra", nargs="*", default=[], help="more query folders (their words are added as candidates)")
    ap.add_argument("--build", default="build")
    ap.add_argument("--only-exact", action="store_true")
    a = ap.parse_args()
    clips = {}
    for it in load_meta(a.unit_dir):
        if it.get("file"):
            clips[unicodedata.normalize("NFC", it["word_ml"])] = os.path.join(a.unit_dir, it["file"])
    cache = {}
    queries, cands = [], {}
    for qd in [a.query_dir] + a.extra:
        for it in load_meta(qd):
            if not it.get("file") or (a.only_exact and not it.get("exact") and qd == a.query_dir):
                continue
            w = unicodedata.normalize("NFC", it["word_ml"])
            us = su.decompose(w)
            if us is None or any(unicodedata.normalize("NFC", u) not in clips for u in us):
                continue
            queries.append((qd, w, os.path.join(qd, it["file"])))
            cands[w] = us
    td = tempfile.mkdtemp()
    tmpl = {}
    for i, (w, us) in enumerate(cands.items()):
        p = compose(us, clips, td, str(i))
        f = feats(p, a.build, cache) if p else None
        if f is not None and len(f) > 3:
            tmpl[w] = f
    print("candidates: %d composed templates (words with every unit available)" % len(tmpl))
    res = {}
    for qd, w, path in queries:
        if w not in tmpl:
            continue
        q = feats(path, a.build, cache)
        if q is None or len(q) < 4:
            continue
        scores = []
        for cw, tf in tmpl.items():
            scores.append((dtw(q, resample(tf, len(q))), cw))
        scores.sort()
        rank = [c for _, c in scores].index(w) + 1
        res.setdefault(qd, []).append((rank, w, scores[0][1]))
    for qd, rs in res.items():
        n = len(rs)
        top1 = sum(1 for r in rs if r[0] == 1)
        top5 = sum(1 for r in rs if r[0] <= 5)
        mrr = sum(1.0 / r[0] for r in rs) / n
        med = sorted(r[0] for r in rs)[n // 2]
        print("\nqueries from %-34s n=%3d  top-1 %5.1f%%  top-5 %5.1f%%  MRR %.3f  median rank %d of %d  (chance top-1 %.1f%%)"
              % (os.path.basename(os.path.normpath(qd)), n, 100.0 * top1 / n, 100.0 * top5 / n, mrr, med, len(tmpl), 100.0 / len(tmpl)))
        worst = sorted(rs, key=lambda r: -r[0])[:5]
        print("   worst:", [(w, r, "picked " + p) for r, w, p in worst])


if __name__ == "__main__":
    main()
