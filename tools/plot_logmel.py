#!/usr/bin/env python3
"""Plot a log-Mel matrix from `s2w_features` text output. Visualization only;
not part of the C recognition logic. Needs numpy + matplotlib.
usage: s2w_features x.wav | plot_logmel.py [out.png] [logmel_raw|logmel_norm|mfcc_raw|mfcc_norm]"""
import sys

def parse(lines):
    meta, mats, cur = {}, {}, None
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("matrix "):
            _, name, r, c = ln.split()
            cur = mats[name] = []
        elif cur is not None and ln:
            cur.append([float(x) for x in ln.split()])
        elif "=" in ln:
            k, v = ln.split("=", 1)
            meta[k] = v
    return meta, mats

if __name__ == "__main__":
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt, numpy as np
    out = sys.argv[1] if len(sys.argv) > 1 else "logmel.png"
    name = sys.argv[2] if len(sys.argv) > 2 else "logmel_raw"
    meta, mats = parse(sys.stdin)
    m = np.array(mats[name]).T
    hop = float(meta["hop_ms"]) / 1000
    plt.figure(figsize=(10, 4))
    plt.imshow(m, origin="lower", aspect="auto", extent=[0, m.shape[1] * hop, 0, m.shape[0]])
    plt.xlabel("time (s)"); plt.ylabel("band"); plt.colorbar(label=name); plt.tight_layout()
    plt.savefig(out, dpi=120)
