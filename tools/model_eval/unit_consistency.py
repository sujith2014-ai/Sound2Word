#!/usr/bin/env python3
"""Unit-consistency experiment: does the SAME sound look the same in DIFFERENT words?

Question being tested: when the recorder cuts a word into sounds (K, A, L, A, M ...), does the segment labelled "k" in one word
resemble the segment labelled "k" in other words more than it resembles other sounds? If not, the letter-to-segment matching
cannot be trusted. The script separates the possible causes:

  S0  current recorder boundaries (spectral first guess, tools/s2w_recorder.suggest_edges)
  S1  even split of the same speech region (a control: no boundary intelligence)
  S2  boundaries re-aligned from the data itself (segmental k-means / Viterbi, flat start from S0), several iterations
  shuf  S0 segments with unit labels shuffled (chance level)

Metric: leave-one-WORD-out nearest-centroid accuracy over sounds that occur >= MINCOUNT times, using the mean MFCC of the
central part of each segment. Reading the result:
  S0 ~ shuf          -> segments do not carry the label: boundaries, voice or labels are wrong
  S0 ~ S1 < S2       -> the boundary method adds nothing; re-alignment from data helps (boundaries were the problem)
  S2 ~ S0 ~ low      -> acoustics are not consistent with the labels even after re-alignment (voice or unit labels are the problem)
  S0 >> shuf, S2 > S0-> the idea works, boundaries can be improved

usage (inside the Docker build tree):  unit_consistency.py CLIP_DIR [--build build] [--min 8] [--only-exact]
CLIP_DIR holds meta.json (or meta_scored.json) with items {word_ml, units, file, exact?}. Synthetic data: software validation only.
"""
import argparse
import json
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import s2w_recorder as r  # noqa: E402
import s2w_viewer as v  # noqa: E402

HOP, FRAME = 0.010, 0.025
NC = 13


def dct_matrix(n_in, n_out):
    k = np.arange(n_out)[:, None]
    n = np.arange(n_in)[None, :]
    return np.cos(math.pi / n_in * (n + 0.5) * k) * math.sqrt(2.0 / n_in)


def load_clip(path, build):
    res = v.run_tool(os.path.join(build, "s2w_features"), [path, "--out", "logmel"])
    if res.returncode != 0:
        return None
    return np.array(v.parse_matrix(res.stdout, "logmel_raw"), dtype=float)


def frame_of(t):
    return int(round((t - (FRAME - HOP) / 2) / HOP))


def seg_feature(mfcc, f0, f1):
    """Mean MFCC (c1..c12) over the central 60% of frames f0..f1-1 (at least one frame)."""
    n = max(1, f1 - f0)
    a = f0 + int(n * 0.2)
    b = max(a + 1, f0 + int(math.ceil(n * 0.8)))
    return mfcc[a:min(len(mfcc), b), 1:].mean(axis=0)


def viterbi_segments(frame_feats, means, varis, lo, hi, unit_ids, min_len=2):
    """Monotonic alignment of frames lo..hi-1 to the unit sequence, maximising Gaussian log-likelihood. -> edge frame list."""
    T, N = hi - lo, len(unit_ids)
    x = frame_feats[lo:hi]
    # per-unit frame cost
    cost = np.empty((N, T))
    for n, u in enumerate(unit_ids):
        m, s = means[u], varis[u]
        cost[n] = (0.5 * (((x - m) ** 2) / s + np.log(s))).sum(axis=1)
    cum = np.concatenate([np.zeros((N, 1)), np.cumsum(cost, axis=1)], axis=1)
    INF = 1e18
    dp = np.full((N + 1, T + 1), INF)
    bp = np.zeros((N + 1, T + 1), dtype=int)
    dp[0, 0] = 0.0
    for n in range(1, N + 1):
        for t in range(n * min_len, T + 1):
            best, arg = INF, -1
            for s in range((n - 1) * min_len, t - min_len + 1):
                c = dp[n - 1, s] + cum[n - 1, t] - cum[n - 1, s]
                if c < best:
                    best, arg = c, s
            dp[n, t], bp[n, t] = best, arg
    if dp[N, T] >= INF:
        return None
    edges, t = [T], T
    for n in range(N, 0, -1):
        t = bp[n, t]
        edges.append(t)
    return [lo + e for e in edges[::-1]]


def evaluate(items, seg_edges, min_count, label_key="units", shuffle_seed=None):
    """Leave-one-word-out nearest-centroid accuracy. items[k]['feat'][s] is built from seg_edges[k]."""
    X, Y, W = [], [], []
    for k, it in enumerate(items):
        e = seg_edges[k]
        for si, u in enumerate(it["units"]):
            X.append(seg_feature(it["mfcc"], e[si], e[si + 1]))
            Y.append(u)
            W.append(k)
    X, Y, W = np.array(X), np.array(Y), np.array(W)
    if shuffle_seed is not None:
        Y = Y.copy()
        np.random.RandomState(shuffle_seed).shuffle(Y)
    cnt = {u: int((Y == u).sum()) for u in set(Y)}
    keep = np.array([cnt[u] >= min_count for u in Y])
    X, Y, W = X[keep], Y[keep], W[keep]
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = (X - mu) / sd
    units = sorted(set(Y))
    sums = {u: Z[Y == u].sum(axis=0) for u in units}
    counts = {u: int((Y == u).sum()) for u in units}
    ok = 0
    per = {u: [0, 0] for u in units}
    conf = {}
    for i in range(len(Z)):
        best, bu = 1e18, None
        for u in units:
            s, c = sums[u], counts[u]
            if Y[i] == u:
                s, c = s - Z[i], c - 1
            # (words repeat a unit within one word: also remove that word's other segments of u)
            same = (W == W[i]) & (Y == u)
            same[i] = False
            if same.any():
                s, c = s - Z[same].sum(axis=0), c - int(same.sum())
            if c <= 0:
                continue
            d = float(((Z[i] - s / c) ** 2).sum())
            if d < best:
                best, bu = d, u
        per[Y[i]][1] += 1
        if bu == Y[i]:
            ok += 1
            per[Y[i]][0] += 1
        else:
            conf[(Y[i], bu)] = conf.get((Y[i], bu), 0) + 1
    maj = max(counts.values()) / sum(counts.values())
    # Fisher-like separation: mean within-unit distance / mean between-unit-centroid distance
    cent = np.array([Z[Y == u].mean(axis=0) for u in units])
    within = np.mean([np.sqrt(((Z[Y == u] - cent[j]) ** 2).sum(axis=1)).mean() for j, u in enumerate(units)])
    between = np.mean([np.sqrt(((cent[a] - cent[b]) ** 2).sum()) for a in range(len(units)) for b in range(a + 1, len(units))])
    bal = float(np.mean([per[u][0] / max(1, per[u][1]) for u in units]))
    return {"acc": ok / max(1, len(Z)), "balanced_acc": bal, "n": int(len(Z)), "units": len(units), "majority": maj, "per_unit": per,
            "confusions": sorted(conf.items(), key=lambda kv: -kv[1])[:8], "within_over_between": float(within / between)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip_dir")
    ap.add_argument("--build", default="build")
    ap.add_argument("--min", type=int, default=8)
    ap.add_argument("--only-exact", action="store_true", help="use only clips the recogniser heard as exactly the intended word")
    ap.add_argument("--iters", type=int, default=6)
    ap.add_argument("--out")
    a = ap.parse_args()
    meta_path = os.path.join(a.clip_dir, "meta_scored.json" if os.path.exists(os.path.join(a.clip_dir, "meta_scored.json")) else "meta.json")
    meta = json.load(open(meta_path, encoding="utf-8"))["items"]
    dctm = None
    items = []
    for it in meta:
        if not it.get("file") or "units" not in it or (a.only_exact and not it.get("exact")):
            continue
        mat = load_clip(os.path.join(a.clip_dir, it["file"]), a.build)
        if mat is None or len(mat) < 12:
            continue
        raw = mat
        mat = mat - mat.mean(axis=0, keepdims=True)                      # per-word cepstral-mean style normalisation for the features
        if dctm is None:
            dctm = dct_matrix(mat.shape[1], NC)
        mfcc = mat @ dctm.T
        sug = r.suggest_edges(raw.tolist(), len(it["units"]))             # exactly what the recorder does (un-normalised log-mel)
        if not sug:
            continue
        fe = [min(len(mfcc), max(0, frame_of(t))) for t in sug]
        if any(b - a_ < 1 for a_, b in zip(fe, fe[1:])):
            continue
        sugu = r.suggest_edges(raw.tolist(), len(it["units"]), units=it["units"])      # unit-aware version (stops may be short)
        fu = [min(len(mfcc), max(0, frame_of(t))) for t in sugu] if sugu else fe
        if any(b - a_ < 1 for a_, b in zip(fu, fu[1:])):
            fu = fe
        items.append({"word": it["word_ml"], "units": it["units"], "mfcc": mfcc, "s0": fe, "s0u": fu})
    print("clips used: %d of %d%s" % (len(items), len(meta), " (exact only)" if a.only_exact else ""))
    if len(items) < 30:
        print("too few clips for a meaningful test")
        return 1
    res = {}
    s0 = [it["s0"] for it in items]
    res["S0 current boundaries"] = evaluate(items, s0, a.min)
    res["S0u unit-aware first guess (new)"] = evaluate(items, [it["s0u"] for it in items], a.min)
    s1 = []
    for it in items:
        lo, hi, n = it["s0"][0], it["s0"][-1], len(it["units"])
        s1.append([int(round(lo + k * (hi - lo) / n)) for k in range(n + 1)])
    res["S1 even split of the same region"] = evaluate(items, s1, a.min)
    sh = [evaluate(items, s0, a.min, shuffle_seed=s)["acc"] for s in range(10)]
    res["shuffled labels (chance)"] = {"acc": float(np.mean(sh)), "sd": float(np.std(sh))}
    # S2: segmental k-means re-alignment, flat start from S0
    cur = [list(e) for e in s0]
    for itn in range(1, a.iters + 1):
        feats = {}
        for it, e in zip(items, cur):
            for si, u in enumerate(it["units"]):
                feats.setdefault(u, []).append(it["mfcc"][e[si]:max(e[si] + 1, e[si + 1]), 1:])
        means, varis = {}, {}
        allf = np.concatenate([np.concatenate(v_) for v_ in feats.values()])
        gv = allf.var(axis=0) + 1e-6
        for u, lst in feats.items():
            f = np.concatenate(lst)
            means[u] = f.mean(axis=0)
            varis[u] = 0.5 * f.var(axis=0) + 0.5 * gv + 1e-6
        new, changed = [], 0
        for it, e in zip(items, cur):
            ff = it["mfcc"][:, 1:]
            ed = viterbi_segments(ff, means, varis, e[0], e[-1], it["units"])
            ed = ed or e
            changed += sum(abs(x - y) for x, y in zip(ed, e))
            new.append(ed)
        cur = new
        res["S2 re-aligned, iteration %d" % itn] = evaluate(items, cur, a.min)
        res["S2 re-aligned, iteration %d" % itn]["boundary_shift_frames_total"] = int(changed)
    # S2x: the same re-alignment, but each word is aligned with models trained ONLY on the other folds (no self-fitting)
    K = 5
    fold = [i % K for i in range(len(items))]
    random.Random(0).shuffle(fold)
    curx = [list(e) for e in s0]
    for itn in range(1, a.iters + 1):
        feats = []
        for it, e in zip(items, curx):
            d = {}
            for si, u in enumerate(it["units"]):
                d.setdefault(u, []).append(it["mfcc"][e[si]:max(e[si] + 1, e[si + 1]), 1:])
            feats.append(d)
        newx = list(curx)
        for k in range(K):
            pool = {}
            for i, d in enumerate(feats):
                if fold[i] != k:
                    for u, lst in d.items():
                        pool.setdefault(u, []).extend(lst)
            allf = np.concatenate([np.concatenate(l) for l in pool.values()])
            gv = allf.var(axis=0) + 1e-6
            means = {u: np.concatenate(l).mean(axis=0) for u, l in pool.items()}
            varis = {u: 0.5 * np.concatenate(l).var(axis=0) + 0.5 * gv + 1e-6 for u, l in pool.items()}
            for i, it in enumerate(items):
                if fold[i] == k and all(u in means for u in it["units"]):
                    ed = viterbi_segments(it["mfcc"][:, 1:], means, varis, curx[i][0], curx[i][-1], it["units"])
                    if ed:
                        newx[i] = ed
        curx = newx
    res["S2x re-aligned, cross-fitted (5 folds)"] = evaluate(items, curx, a.min)
    # report
    print("\nunits with >= %d samples: %d  | samples: %d  | majority-class baseline %.0f%%\n" %
          (a.min, res["S0 current boundaries"]["units"], res["S0 current boundaries"]["n"], 100 * res["S0 current boundaries"]["majority"]))
    for k, d in res.items():
        extra = "" if "within_over_between" not in d else "  within/between %.2f" % d["within_over_between"]
        sd = "" if "sd" not in d else " +/- %.1f" % (100 * d["sd"])
        bal = "" if "balanced_acc" not in d else "  balanced %5.1f%%" % (100 * d["balanced_acc"])
        print("%-40s accuracy %5.1f%%%s%s%s" % (k, 100 * d["acc"], bal, sd, extra))
    last = res["S2 re-aligned, iteration %d" % a.iters]
    print("\nper-sound accuracy, current boundaries (S0) -> after re-alignment (S2):")
    for u in sorted(res["S0 current boundaries"]["per_unit"], key=lambda u: -res["S0 current boundaries"]["per_unit"][u][1]):
        a0, n0 = res["S0 current boundaries"]["per_unit"][u]
        a2, n2 = last["per_unit"].get(u, [0, 0])
        print("  %-4s n=%3d   S0 %3.0f%%   S2 %3.0f%%" % (u, n0, 100 * a0 / max(1, n0), 100 * a2 / max(1, n2)))
    print("\nmost common confusions after re-alignment:", [("%s->%s" % k, c) for k, c in last["confusions"]])
    # durations
    for name, edges in (("S0", s0), ("S2", cur)):
        d = {}
        for it, e in zip(items, edges):
            for si, u in enumerate(it["units"]):
                d.setdefault(u, []).append((e[si + 1] - e[si]) * HOP * 1000)
        print("mean duration ms (%s): %s" % (name, ", ".join("%s %.0f" % (u, np.mean(d[u])) for u in sorted(d, key=lambda u: -len(d[u]))[:10])))
    if a.out:
        json.dump({k: {kk: vv for kk, vv in d.items() if kk != "per_unit"} for k, d in res.items()}, open(a.out, "w"), indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
