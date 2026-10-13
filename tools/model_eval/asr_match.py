#!/usr/bin/env python3
"""Voice -> dictionary word with a neural recogniser: transcribe the clip, then pick the nearest dictionary word by edit distance.
The SAME queries and candidates as mms_rank.py (the composed-unit DTW test), so the numbers are directly comparable.
usage (Rootit .venv-indic):  asr_match.py MMS_DIR [N_QUERIES=150]"""
import json
import os
import re
import sys
import unicodedata

import numpy as np
import soundfile as sf

sys.stdout.reconfigure(encoding="utf-8")
mms = sys.argv[1]
nq = int(sys.argv[2]) if len(sys.argv) > 2 else 150
base = os.path.join(os.environ["APPDATA"], "rootit-voice", "models", "indic-core")
sys.path.insert(0, base)
from indic_transcribe import IndicTranscribe  # noqa: E402

CH = {"ൻ": "ന്", "ൺ": "ണ്", "ൽ": "ല്", "ൾ": "ള്", "ർ": "ര്", "ൿ": "ക്"}


def norm(s):
    s = unicodedata.normalize("NFC", s)
    s = "".join(CH.get(c, c) for c in s).replace("‌", "").replace("‍", "")
    return re.sub(r"[^ഀ-ൿ]", "", s)


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


items = json.load(open(os.path.join(mms, "words.json"), encoding="utf-8"))["items"]
byw = {unicodedata.normalize("NFC", it["word_ml"]): it for it in items}
words = sorted(byw)                                           # same order as mms_rank.py
cands = {w: norm(w) for w in words}
asr = IndicTranscribe.from_pretrained(base, device="cuda")
top1 = top5 = exact = 0
rows = []
for q in words[:nq]:
    x, sr = sf.read(os.path.join(mms, "words", byw[q]["file"]), dtype="float32")
    x = np.concatenate([np.zeros(3200, "float32"), x, np.zeros(3200, "float32")])
    heard = norm(str(asr(x, lang="ml")))
    ranked = sorted(words, key=lambda c: (lev(heard, cands[c]) / max(len(heard), len(cands[c]), 1), c))
    r = ranked.index(q) + 1
    top1 += r == 1
    top5 += r <= 5
    exact += heard == cands[q]
    rows.append((r, q, heard))
n = len(rows)
print("queries %d | candidates %d | recogniser text exactly right %d (%.1f%%) | nearest dictionary word is the right one: top-1 %.1f%%, top-5 %.1f%%"
      % (n, len(words), exact, 100.0 * exact / n, 100.0 * top1 / n, 100.0 * top5 / n))
print("median rank", sorted(r for r, _, _ in rows)[n // 2])
print("misses:", [(q, h, r) for r, q, h in rows if r > 1][:8])
