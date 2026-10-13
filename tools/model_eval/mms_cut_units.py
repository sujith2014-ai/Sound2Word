#!/usr/bin/env python3
"""Cut sound units out of WORDS spoken by a character-level, non-autoregressive TTS (facebook/mms-tts-mal, VITS) using the model's own
per-character durations, so every akshara is taken in its natural word context with exact boundaries (no recogniser involved).
usage (venv_parler):  mms_cut_units.py OUT_DIR [N_WORDS=600] [SEED=0]
Writes OUT_DIR/words/*.wav (+ words.json), OUT_DIR/clips/*.wav + instances.json (every akshara instance), and OUT_DIR/best/ (one clip per
unit: the instance with a typical length and quiet edges) with meta.json in the same format as the other unit folders.
Whole written syllables are kept whole (a conjunct such as ങ്ങ is ONE unit)."""
import csv
import json
import os
import random
import statistics
import sys
import unicodedata
import wave

import numpy as np
import torch
from transformers import AutoTokenizer, VitsModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import s2w_units as su  # noqa: E402

out = sys.argv[1]
n_words = int(sys.argv[2]) if len(sys.argv) > 2 else 600
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 0
M = "facebook/mms-tts-mal"
tok = AutoTokenizer.from_pretrained(M)
model = VitsModel.from_pretrained(M).eval()
vocab = tok.get_vocab()
SR, HOP = model.config.sampling_rate, 256
for d in ("words", "clips", "best"):
    os.makedirs(os.path.join(out, d), exist_ok=True)

csv.field_size_limit(10 ** 7)
pool = []
with open(r"D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv", encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        w = r["word_ml"]
        n = su.normalise(w)
        if r["meaning_en"] and " + " in r["sources"] and su.decompose(n, split_all=False) and 2 <= len(su.AKS.findall(n)) <= 7 and all(c in vocab for c in n):
            pool.append((w, r["manglish"]))
random.Random(seed).shuffle(pool)
pool = pool[:n_words]
cap = {}
model.duration_predictor.register_forward_hook(lambda m, i, o: cap.__setitem__("log_dur", o.detach()))


def write_wav(path, x):
    y = np.clip(x, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype("<i2").tobytes())


insts, words_meta = [], []
for wi, (w, rom) in enumerate(pool):
    n = su.normalise(w)
    segs = su.AKS.findall(n)
    ids = tok(n, return_tensors="pt")
    if ids["input_ids"].shape[1] != 2 * len(n) + 1:
        continue
    torch.manual_seed(wi)
    with torch.no_grad():
        wav = model(**ids).waveform[0].numpy()
    dur = torch.ceil(torch.exp(cap["log_dur"][0, 0]) / model.config.speaking_rate).long().numpy()
    if int(dur.sum()) * HOP != len(wav):
        continue
    cum = np.concatenate([[0], np.cumsum(dur)])                    # frame at which token t starts
    nch = len(n)
    b = [int(cum[1])] + [int(cum[2 * i] + dur[2 * i] // 2) for i in range(1, nch)] + [int(cum[2 * nch])]      # boundary between chars, in frames
    pos, units = 0, []
    wfn = "w%04d_%s.wav" % (wi, rom)
    write_wav(os.path.join(out, "words", wfn), wav)
    for k, s in enumerate(segs):
        a, z = b[pos], b[pos + len(s)]
        pos += len(s)
        clip = wav[a * HOP:z * HOP]
        cfn = "c%04d_%02d.wav" % (wi, k)
        write_wav(os.path.join(out, "clips", cfn), clip)
        peak = float(np.abs(clip).max()) or 1.0
        e = int(0.008 * SR)
        edge = float((np.sqrt((clip[:e] ** 2).mean()) + np.sqrt((clip[-e:] ** 2).mean())) / 2 / peak) if len(clip) > 2 * e else 1.0
        insts.append({"unit": s, "word": w, "k": k, "file": cfn, "dur": round((z - a) * HOP / SR, 3), "edge": round(edge, 3)})
        units.append({"unit": s, "start": round(a * HOP / SR, 4), "end": round(z * HOP / SR, 4)})
    words_meta.append({"word_ml": w, "manglish": rom, "file": wfn, "units": units, "audio_s": round(len(wav) / SR, 3)})
json.dump(insts, open(os.path.join(out, "instances.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"items": words_meta}, open(os.path.join(out, "words.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

by = {}
for i in insts:
    by.setdefault(unicodedata.normalize("NFC", i["unit"]), []).append(i)
items = []
for u, lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
    med = statistics.median(x["dur"] for x in lst)
    best = min(lst, key=lambda x: abs(x["dur"] - med) / max(med, 1e-3) + 0.6 * x["edge"])
    src = os.path.join(out, "clips", best["file"])
    dst = os.path.join(out, "best", "u_%s" % best["file"])
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        fo.write(fi.read())
    items.append({"i": len(items) + 1, "word_ml": u, "file": os.path.basename(dst), "kind": su.kind(u), "uses": len(lst), "audio_s": best["dur"],
                  "model": M, "voice": "MMS-mal (VITS), cut from word '%s'" % best["word"], "synthetic": True, "instances": len(lst)})
json.dump({"items": items}, open(os.path.join(out, "best", "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("words used %d of %d | instances %d | distinct units %d | units with >=3 instances %d | mean unit length %.0f ms" %
      (len(words_meta), len(pool), len(insts), len(items), sum(1 for x in items if x["instances"] >= 3),
       1000 * statistics.mean(x["dur"] for x in insts)))
print("top units:", " ".join("%s(%d)" % (x["word_ml"], x["instances"]) for x in items[:25]))
