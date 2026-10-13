#!/usr/bin/env python3
"""Keep only the FIRST spoken word of each synthetic clip: cut at the first pause of >= GAP_MS after speech starts.
TTS models that keep talking after the word ("അംഗകം ഒരു ...") otherwise never match the intended word.
usage: trim_clips.py SRC_ROOT DST_ROOT [GAP_MS=150] [THRESH=0.04]     (SRC_ROOT holds <set>/meta.json + wavs; needs numpy + soundfile)
Writes DST_ROOT/<set>/ with trimmed WAVs and a fresh meta.json (heard/exact are dropped: score again with roundtrip.py)."""
import json
import os
import shutil
import sys

import numpy as np
import soundfile as sf


def trim(x, sr, gap_ms=150, thresh=0.04, pad_ms=30):
    hop = max(1, int(sr * 0.010))
    n = len(x) // hop
    if n < 3:
        return x
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    peak = rms.max()
    if peak <= 0:
        return x
    sp = rms > thresh * peak
    idx = np.flatnonzero(sp)
    if len(idx) == 0:
        return x
    s0 = int(idx[0])
    gap = max(1, int(gap_ms / 10))
    end = n
    run = 0
    for i in range(s0, n):
        if sp[i]:
            run = 0
        else:
            run += 1
            if run >= gap:
                end = i - run + 1
                break
    pad = int(pad_ms / 10)
    a, b = max(0, s0 - pad) * hop, min(len(x), (end + pad) * hop)
    return x[a:b]


def main():
    src, dst = sys.argv[1], sys.argv[2]
    gap = float(sys.argv[3]) if len(sys.argv) > 3 else 150.0
    th = float(sys.argv[4]) if len(sys.argv) > 4 else 0.04
    for name in sorted(os.listdir(src)):
        d = os.path.join(src, name)
        mp = os.path.join(d, "meta.json")
        if not os.path.isfile(mp):
            continue
        out = os.path.join(dst, name)
        os.makedirs(out, exist_ok=True)
        meta = json.load(open(mp, encoding="utf-8"))
        items = []
        for it in meta["items"]:
            if not it.get("file"):
                items.append(it)
                continue
            x, sr = sf.read(os.path.join(d, it["file"]), dtype="float32")
            y = trim(x, sr, gap, th)
            sf.write(os.path.join(out, it["file"]), y, sr)
            new = {k: v for k, v in it.items() if k not in ("heard", "cer", "exact")}
            new.update(audio_s=round(len(y) / sr, 3), trimmed_from_s=round(len(x) / sr, 3), trim_gap_ms=gap)
            items.append(new)
        json.dump({"items": items}, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(name, len(items), "clips")


if __name__ == "__main__":
    main()
