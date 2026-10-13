#!/usr/bin/env python3
"""Decompose EVERY lexicon word into a small inventory of sound units and verify the decomposition is lossless.
usage: build_unit_inventory.py [LEXICON.csv] [OUT_DIR]

Units (all written in Malayalam script, so they are unambiguous):
  * a syllable: consonant + vowel sign (ക, കാ, കി ...), a standalone vowel (അ, ആ ...), a chillu (ൻ, ർ ...)
  * a HOLD: the first consonant of a doubled / nasal+stop cluster written with the chandrakkala (ക്, ന്, ത് ...)
  * an ONSET CLUSTER kept whole when the second consonant is ya/ra/la/va (പ്ര, ത്ര, സ്വ, വ്യ ...) with its vowel sign
  * anusvara / visarga stay attached to the syllable they follow (കം, കഃ)
Outputs (in OUT_DIR): inventory.json (id, text, kind, uses), labels.csv (word_ml,unit_ids), labels.bin (uint16 ids, uint32 offsets),
stats.json. Every word is rebuilt from its unit ids and compared with the original text."""
import csv
import json
import os
import re
import struct
import sys
import unicodedata
from collections import Counter

csv.field_size_limit(10 ** 7)
LEX = sys.argv[1] if len(sys.argv) > 1 else r"D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"D:\Apps\Sound2Word\data\private\units"
CONS = "\u0d15-\u0d3a\u0d4e"
VOW = "\u0d05-\u0d14\u0d60\u0d61"
SIGN = "\u0d3e-\u0d44\u0d46-\u0d48\u0d4a-\u0d4c\u0d57"
CHILLU = "\u0d7a-\u0d7f"
VIR = "\u0d4d"
ZWJ, ZWNJ = "\u200d", "\u200c"
AKS = re.compile("(?:[%s](?:%s[%s])*(?:[%s]|%s)?|[%s]|[%s])[\u0d02\u0d03]?" % (CONS, VIR, CONS, SIGN, VIR, VOW, CHILLU))
OLD_CHILLU = {"ണ" + VIR + ZWJ: "ൺ", "ന" + VIR + ZWJ: "ൻ", "ര" + VIR + ZWJ: "ർ", "ല" + VIR + ZWJ: "ൽ", "ള" + VIR + ZWJ: "ൾ", "ക" + VIR + ZWJ: "ൿ"}
LIQ = set("യരലവ") if os.environ.get("S2W_SPLIT_ALL") != "1" else set()      # S2W_SPLIT_ALL=1: split EVERY cluster into hold + syllable


def normalise(w):
    w = unicodedata.normalize("NFC", w)
    for k, v in OLD_CHILLU.items():
        w = w.replace(k, v)
    w = w.replace(ZWJ, "").replace(ZWNJ, "")
    # old two-part spellings of one vowel sign: e+e = ai, e+aa = o, E+aa = oo, e+au-length = au
    for a, b in (("െെ", "ൈ"), ("ൊ", "ൊ"), ("ോ", "ോ"), ("ൌ", "ൌ")):
        w = w.replace(a, b)
    return w


def split_akshara(u):
    m = re.fullmatch("([%s](?:%s[%s])+)([%s])?([\u0d02\u0d03])?" % (CONS, VIR, CONS, SIGN), u)
    if not m:
        return [u]
    cons = [p for p in m.group(1).split(VIR) if p]
    tail = (m.group(2) or "") + (m.group(3) or "")
    if len(cons) == 2 and cons[1] in LIQ:
        return [u]                                       # onset cluster: pra, tra, sva, vya ...
    return [c + VIR for c in cons[:-1]] + [cons[-1] + tail]


def kind(u):
    if re.fullmatch("[%s][\u0d02\u0d03]?" % CHILLU, u):
        return "chillu"
    if re.fullmatch("[%s][\u0d02\u0d03]?" % VOW, u):
        return "vowel"
    if re.fullmatch("[%s]%s" % (CONS, VIR), u):
        return "hold"
    if re.fullmatch("[%s](?:[%s])?[\u0d02\u0d03]?" % (CONS, SIGN), u):
        return "syllable"
    return "onset_cluster"


def main():
    words, bad = [], []
    with open(LEX, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            w = r["word_ml"]
            n = normalise(w)
            segs = AKS.findall(n)
            if "".join(segs) != n:
                bad.append(w)
                continue
            words.append((w, [x for s in segs for x in split_akshara(s)]))
    cnt = Counter(u for _, us in words for u in us)
    order = [u for u, _ in cnt.most_common()]                       # id 0 = most frequent unit
    uid = {u: i for i, u in enumerate(order)}
    os.makedirs(OUT, exist_ok=True)
    inv = [{"id": i, "text": u, "kind": kind(u), "uses": cnt[u]} for i, u in enumerate(order)]
    json.dump(inv, open(os.path.join(OUT, "inventory.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    exact = 0
    with open(os.path.join(OUT, "labels.csv"), "w", encoding="utf-8", newline="") as fc, open(os.path.join(OUT, "labels.bin"), "wb") as fb:
        wr = csv.writer(fc)
        wr.writerow(["word_ml", "unit_ids"])
        offs, ids = [0], []
        for w, us in words:
            row = [uid[u] for u in us]
            if "".join(order[i] for i in row) == normalise(w):        # lossless: units spell the word again
                exact += 1
            wr.writerow([w, " ".join(map(str, row))])
            ids.extend(row)
            offs.append(len(ids))
        fb.write(struct.pack("<II", len(words), len(order)))
        fb.write(struct.pack("<%dI" % len(offs), *offs))
        fb.write(struct.pack("<%dH" % len(ids), *ids))
    kinds = Counter(i["kind"] for i in inv)
    tok = Counter()
    for i in inv:
        tok[i["kind"]] += i["uses"]
    total = sum(tok.values())
    order_rank = {u: i for i, u in enumerate(order)}
    need = sorted(max(order_rank[u] for u in us) for _, us in words)
    stats = {"words_total": len(words) + len(bad), "words_decomposed": len(words), "words_not_decomposed": len(bad), "lossless": exact,
             "distinct_units": len(order), "units_by_kind": dict(kinds), "unit_tokens": total,
             "mean_units_per_word": round(total / len(words), 2), "unit_tokens_by_kind_pct": {k: round(100 * v / total, 1) for k, v in tok.items()},
             "units_for_90pct_words": need[int(0.90 * len(need)) - 1] + 1, "units_for_95pct_words": need[int(0.95 * len(need)) - 1] + 1,
             "units_for_99pct_words": need[int(0.99 * len(need)) - 1] + 1,
             "labels_bin_bytes": os.path.getsize(os.path.join(OUT, "labels.bin")), "labels_csv_bytes": os.path.getsize(os.path.join(OUT, "labels.csv")),
             "inventory_json_bytes": os.path.getsize(os.path.join(OUT, "inventory.json")), "not_decomposed_examples": bad[:20]}
    json.dump(stats, open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
