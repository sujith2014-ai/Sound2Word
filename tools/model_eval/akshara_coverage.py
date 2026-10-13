#!/usr/bin/env python3
"""How many akshara (syllable-like written units) cover the dictionary?  usage: akshara_coverage.py [LEXICON.csv]
An akshara here = independent vowel | consonant(+virama+consonant)* with optional vowel sign | chillu, each optionally followed by
anusvara/visarga. Deterministic from Unicode: no model. Prints distinct units by kind and word coverage by the top-N units."""
import csv
import re
import sys
from collections import Counter

csv.field_size_limit(10 ** 7)
path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv"
CONS = "\u0d15-\u0d3a\u0d4e"            # consonants (+ dot reph)
VOW = "\u0d05-\u0d14\u0d60\u0d61"         # independent vowels
SIGN = "\u0d3e-\u0d44\u0d46-\u0d48\u0d4a-\u0d4c\u0d57"   # vowel signs
CHILLU = "\u0d7a-\u0d7f"
VIR = "\u0d4d"
AKS = re.compile("(?:[%s](?:%s[%s])*(?:[%s]|%s)?|[%s]|[%s])[\u0d02\u0d03]?" % (CONS, VIR, CONS, SIGN, VIR, VOW, CHILLU))


def kind(u):
    base = re.sub("[\u0d02\u0d03]", "", u)
    if re.fullmatch("[%s]" % VOW, base):
        return "vowel"
    if re.fullmatch("[%s]" % CHILLU, base):
        return "chillu"
    if re.fullmatch("[%s]" % CONS, base) or re.fullmatch("[%s]%s" % (CONS, VIR), base):
        return "consonant (inherent a, or bare with chandrakkala)"
    if re.fullmatch("[%s][%s]" % (CONS, SIGN), base):
        return "consonant + vowel sign (CV)"
    return "conjunct cluster (C+virama+C ...)"


words = []
with open(path, encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        words.append(r["word_ml"])
units_per_word = []
cnt = Counter()
for w in words:
    segs = AKS.findall(w)
    if "".join(segs) != w:                 # something unexpected (ZWJ etc.): skip from the statistics
        continue
    units_per_word.append(segs)
    cnt.update(segs)
n = len(units_per_word)
print("words analysed: %d of %d   | distinct akshara: %d   | mean units per word: %.2f" %
      (n, len(words), len(cnt), sum(len(s) for s in units_per_word) / n))
by = Counter(kind(u) for u in cnt)
tok = Counter()
for u, c in cnt.items():
    tok[kind(u)] += c
tot = sum(tok.values())
print("\n%-52s %8s %10s" % ("kind", "distinct", "% of tokens"))
for k, v in by.most_common():
    print("%-52s %8d %9.1f%%" % (k, v, 100 * tok[k] / tot))
print("\ncoverage of the dictionary by the N most frequent units (words made ONLY of those units):")
order = [u for u, _ in cnt.most_common()]
rank = {u: i for i, u in enumerate(order)}
need = sorted(max(rank[u] for u in segs) for segs in units_per_word)       # unit rank needed to build each word
for N in (50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000):
    ok = sum(1 for x in need if x < N)
    tk = sum(cnt[u] for u in order[:N]) / tot
    print("  top %5d units: %5.1f%% of words fully buildable   (%5.1f%% of all unit tokens)" % (N, 100 * ok / n, 100 * tk))
for q in (0.90, 0.95, 0.99):
    print("units needed to build %d%% of words: %d" % (round(q * 100), need[int(q * n) - 1] + 1))
print("\nmost frequent 15:", " ".join("%s(%d)" % (u, cnt[u]) for u in order[:15]))
