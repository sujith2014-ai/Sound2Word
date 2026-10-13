#!/usr/bin/env python3
"""Fair comparison of two ways to build word templates from sound units, on the SAME query words and the SAME candidates.
 (A) units cut out of other words by the TTS's own durations (mms_cut_units.py); leave-one-word-out: a query never uses a clip cut from itself
 (B) units spoken ALONE by another TTS (gen_units.py, split-all inventory)
Queries = the MMS whole-word clips. Template = unit clips glued in the feature domain, time-normalised to the query, DTW on mean-removed MFCC.
usage (Docker build tree, numpy): mms_rank.py MMS_DIR ISOLATED_UNIT_DIR [N_QUERIES=200] [--build build]"""
import json
import math
import os
import statistics
import sys
import unicodedata

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import s2w_units as su  # noqa: E402
import s2w_viewer as v  # noqa: E402

mms, iso = sys.argv[1], sys.argv[2]
nq = int(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 200
build = sys.argv[sys.argv.index("--build") + 1] if "--build" in sys.argv else "build"
nf = lambda s: unicodedata.normalize("NFC", s)
HOP, OFF = 0.010, 0.0125
NC = 12


def logmel(path):
    r = v.run_tool(os.path.join(build, "s2w_features"), [path, "--out", "logmel"])
    return np.array(v.parse_matrix(r.stdout, "logmel_raw"), dtype=float) if r.returncode == 0 else None


def to_mfcc(lm, D=[None]):
    if D[0] is None:
        n = lm.shape[1]
        D[0] = np.cos(math.pi / n * (np.arange(n)[None, :] + 0.5) * np.arange(1, NC + 1)[:, None])
    return (lm @ D[0].T)


words = json.load(open(os.path.join(mms, "words.json"), encoding="utf-8"))["items"]
W = {}
for it in words:
    lm = logmel(os.path.join(mms, "words", it["file"]))
    if lm is not None and len(lm) > 8:
        W[nf(it["word_ml"])] = {"lm": lm, "units": it["units"], "item": it}
print("words with features:", len(W))


def frames(lm, a, b):
    i0, i1 = int(round((a - OFF) / HOP)), int(round((b - OFF) / HOP))
    return lm[max(0, i0):max(i0 + 1, min(len(lm), i1))]


# (A) in-word instances: per unit, a ranked list of (source word, mfcc-frames); instance features = the frames of the word it was cut from
inst = json.load(open(os.path.join(mms, "instances.json"), encoding="utf-8"))
byu = {}
for i in inst:
    w = nf(i["word"])
    if w not in W:
        continue
    seg = W[w]["units"][i["k"]]
    fr = frames(W[w]["lm"], seg["start"], seg["end"])
    byu.setdefault(nf(i["unit"]), []).append((i["dur"], i["edge"], w, fr))
rankA = {}
for u, lst in byu.items():
    med = statistics.median(x[0] for x in lst)
    rankA[u] = sorted(lst, key=lambda x: abs(x[0] - med) / max(med, 1e-3) + 0.6 * x[1])

# (B) isolated units (split-all), one clip per unit
isoF = {}
for it in json.load(open(os.path.join(iso, "meta_scored.json" if os.path.exists(os.path.join(iso, "meta_scored.json")) else "meta.json"), encoding="utf-8"))["items"]:
    p = os.path.join(iso, it["file"])
    lm = logmel(p) if it.get("file") else None
    if lm is not None:
        isoF[nf(it["word_ml"])] = lm
print("unit instances: in-word %d units / isolated %d units" % (len(rankA), len(isoF)))


def resample(x, n):
    idx = np.linspace(0, len(x) - 1, n)
    lo = np.floor(idx).astype(int)
    hi = np.minimum(lo + 1, len(x) - 1)
    return x[lo] * (1 - (idx - lo)[:, None]) + x[hi] * (idx - lo)[:, None]


def prep(lm):
    return to_mfcc(lm - lm.mean(axis=0, keepdims=True))[::2]


def dtw(a, b, band=0.3):
    n, m = len(a), len(b)
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2))
    INF = 1e18
    prev = [INF] * (m + 1)
    prev[0] = 0.0
    w = max(3, int(band * max(n, m)))
    for i in range(1, n + 1):
        cur = [INF] * (m + 1)
        di = d[i - 1]
        for j in range(max(1, i - w), min(m, i + w) + 1):
            cur[j] = di[j - 1] + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return prev[m] / (n + m)


cand_units = {w: [nf(s["unit"]) for s in W[w]["units"]] for w in W}               # whole-akshara units (A)
cand_split = {w: [nf(u) for u in su.decompose(w, split_all=True) or []] for w in W}  # split-all units (B)
queries = sorted(W)[:nq]
res = {"A in-word cut (leave-one-out)": [], "B spoken alone": []}
for qi, q in enumerate(queries):
    qf = prep(W[q]["lm"])
    for name in res:
        scores = []
        for c in W:
            if name.startswith("A"):
                parts = []
                for u in cand_units[c]:
                    lst = rankA.get(u, [])
                    pick = next((x for x in lst if x[2] != q), None)
                    if pick is None:
                        parts = None
                        break
                    parts.append(pick[3])
            else:
                parts = [isoF[u] for u in cand_split[c]] if cand_split[c] and all(u in isoF for u in cand_split[c]) else None
            if parts is None:
                continue
            t = prep(np.concatenate(parts))
            if len(t) < 3:
                continue
            scores.append((dtw(qf, resample(t, len(qf))), c))
        scores.sort()
        names = [c for _, c in scores]
        if q in names:
            res[name].append((names.index(q) + 1, len(names)))
    if (qi + 1) % 50 == 0:
        print("  ... %d queries" % (qi + 1), flush=True)
print()
for name, rs in res.items():
    n = len(rs)
    if not n:
        print(name, "no results")
        continue
    print("%-32s buildable queries %3d | candidates ~%d | top-1 %5.1f%% | top-5 %5.1f%% | top-10 %5.1f%% | MRR %.3f | median rank %d"
          % (name, n, rs[0][1], 100.0 * sum(r == 1 for r, _ in rs) / n, 100.0 * sum(r <= 5 for r, _ in rs) / n,
             100.0 * sum(r <= 10 for r, _ in rs) / n, sum(1.0 / r for r, _ in rs) / n, sorted(r for r, _ in rs)[n // 2]))
print("chance top-1: %.2f%%" % (100.0 / len(W)))
