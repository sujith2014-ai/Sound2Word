#!/usr/bin/env python3
"""Train unit models on the clean clips of CLIP_DIR (as unit_consistency.py does) and align given WAV files to their units.
usage: align_demo.py CLIP_DIR  WAV:u1,u2,u3 [WAV:units ...]   (inside the Docker build tree; prints S0 vs re-aligned edges + energy)"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unit_consistency as uc  # noqa: E402

BUILD = os.environ.get("S2W_BUILD", "build")


def prep(wav, units):
    raw = uc.load_clip(wav, BUILD)
    mat = raw - raw.mean(axis=0, keepdims=True)
    return raw, mat @ uc.dct_matrix(mat.shape[1], uc.NC).T


def main():
    clip_dir = sys.argv[1]
    meta = json.load(open(os.path.join(clip_dir, "meta_scored.json"), encoding="utf-8"))["items"]
    items = []
    for it in meta:
        if not it.get("exact") or not it.get("file"):
            continue
        raw, mfcc = prep(os.path.join(clip_dir, it["file"]), it["units"])
        sug = uc.r.suggest_edges(raw.tolist(), len(it["units"]))
        if not sug:
            continue
        fe = [min(len(mfcc), max(0, uc.frame_of(t))) for t in sug]
        if any(b - a < 1 for a, b in zip(fe, fe[1:])):
            continue
        items.append({"units": it["units"], "mfcc": mfcc, "edges": fe})
    for _ in range(6):
        feats = {}
        for it in items:
            for si, u in enumerate(it["units"]):
                feats.setdefault(u, []).append(it["mfcc"][it["edges"][si]:max(it["edges"][si] + 1, it["edges"][si + 1]), 1:])
        allf = np.concatenate([np.concatenate(l) for l in feats.values()])
        gv = allf.var(axis=0) + 1e-6
        means = {u: np.concatenate(l).mean(axis=0) for u, l in feats.items()}
        varis = {u: 0.5 * np.concatenate(l).var(axis=0) + 0.5 * gv + 1e-6 for u, l in feats.items()}
        for it in items:
            if all(u in means for u in it["units"]):
                ed = uc.viterbi_segments(it["mfcc"][:, 1:], means, varis, it["edges"][0], it["edges"][-1], it["units"])
                if ed:
                    it["edges"] = ed
    print("trained on %d clean clips; %d sound models" % (len(items), len(means)))
    for arg in sys.argv[2:]:
        wav, us = arg.split(":")
        units = us.split(",")
        raw, mfcc = prep(wav, units)
        sug = uc.r.suggest_edges(raw.tolist(), len(units))
        s0 = [min(len(mfcc), max(0, uc.frame_of(t))) for t in sug]
        ed = uc.viterbi_segments(mfcc[:, 1:], means, varis, s0[0], s0[-1], units) if all(u in means for u in units) else None
        t = lambda fr: round(fr * uc.HOP + (uc.FRAME - uc.HOP) / 2, 3)
        print("\n%s  units %s  (%d frames)" % (os.path.basename(wav), units, len(mfcc)))
        print("  S0 current   :", [t(f) for f in s0], " durations ms", [int((b - a) * 10) for a, b in zip(s0, s0[1:])])
        su = uc.r.suggest_edges(raw.tolist(), len(units), units=units)
        fu = [min(len(mfcc), max(0, uc.frame_of(x))) for x in su]
        print("  S0u unit-aware:", [t(f) for f in fu], " durations ms", [int((b - a) * 10) for a, b in zip(fu, fu[1:])])
        if ed:
            print("  S2 re-aligned:", [t(f) for f in ed], " durations ms", [int((b - a) * 10) for a, b in zip(ed, ed[1:])])
        e = raw.mean(axis=1)
        print("  energy/10ms  :", " ".join("%d" % round(x) for x in e))


if __name__ == "__main__":
    main()
