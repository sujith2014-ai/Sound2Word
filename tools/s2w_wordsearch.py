#!/usr/bin/env python3
"""Search the dictionary by VOICE. Two methods, reported side by side so neither is trusted blindly:

  composed  build every candidate word out of the alphabet's unit clips (whole written syllables and conjuncts: ങ്ങ is ONE unit), join them with
            tools/s2w_joiner.py, and rank the candidates by DTW distance between their MFCCs and the spoken word's.
  labels    take the text a recogniser heard (any recogniser: indic-core, Whisper ...), cut it into the same units, and rank the dictionary
            by a weighted edit distance in which look-alike letters (ശ ഷ സ, ന ണ, ര റ, ല ള ഴ ...) cost less than unrelated ones.
  fused     reciprocal-rank fusion of the two.

    s2w_wordsearch.py search --query word.wav --units UNIT_DIR --lexicon lexicon.csv [--heard "text"] [--top 5]
    s2w_wordsearch.py eval   --queries QUERY_DIR --units UNIT_DIR --lexicon lexicon.csv [--heard-file meta.json]
QUERY_DIR and UNIT_DIR hold a meta.json / meta_scored.json: items {word_ml, file}. `eval` reports top-1 / top-5 / MRR per method and per word,
including every failure. Numbers from SYNTHETIC audio say nothing about real speakers (see CLAUDE.md)."""
import argparse
import csv
import json
import math
import os
import sys
import unicodedata

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_joiner as jn  # noqa: E402
import s2w_units as su  # noqa: E402

RATE = 16000
NC = 12
NEIGHBOUR_SETS = ["ശഷസ", "നണ", "ങഞ", "രറ", "ലളഴ", "തടഥഠ", "ദഡധഢ", "കഖഗഘ", "ചഛജഝ", "പഫബഭ", "ൻൺ", "ൽൾ"]
VOWEL_SIGNS = "ാിീുൂൃെേൈൊോൗ"


# ------------------------------------------------------------------ features
def _mel_bank(n_fft=512, n_mel=26, lo=100.0, hi=7500.0):
    mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
    inv = lambda m: 700.0 * (10 ** (m / 2595.0) - 1.0)
    pts = inv(np.linspace(mel(lo), mel(hi), n_mel + 2))
    bins = np.floor((n_fft + 1) * pts / RATE).astype(int)
    fb = np.zeros((n_mel, n_fft // 2 + 1))
    for m in range(1, n_mel + 1):
        a, b, c = bins[m - 1], bins[m], bins[m + 1]
        for k in range(a, b):
            fb[m - 1, k] = (k - a) / max(1, b - a)
        for k in range(b, c):
            fb[m - 1, k] = (c - k) / max(1, c - b)
    return fb


_FB = _mel_bank()
_DCT = np.cos(math.pi / 26 * (np.arange(26)[None, :] + 0.5) * np.arange(1, NC + 1)[:, None])


def mfcc(x, rate):
    """c1..c12 per 20 ms hop (25 ms window), cepstral mean AND variance normalised (CMVN). Variance normalisation matters: with mean removal alone,
    loud/short templates such as 'സമ' won half of all synthetic queries (28/63 correct vs 61/63 with CMVN). Self-contained, no C tool needed."""
    x = jn.resample(np.asarray(x, dtype=np.float32), rate, RATE)
    if len(x) < 400:
        x = np.pad(x, (0, 400 - len(x)))
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    win, hop = 400, 320
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx] * np.hamming(win)
    pw = np.abs(np.fft.rfft(fr, 512)) ** 2
    lm = np.log(pw @ _FB.T + 1e-10)
    c = lm @ _DCT.T
    c = c - c.mean(axis=0, keepdims=True)
    return c / (c.std(axis=0, keepdims=True) + 1e-6)


def dtw(a, b, band=0.35):
    """Length-normalised DTW distance, Sakoe-Chiba band."""
    n, m = len(a), len(b)
    if not n or not m:
        return float("inf")
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2))
    INF = 1e18
    prev = np.full(m + 1, INF)
    prev[0] = 0.0
    w = max(3, int(band * max(n, m)), abs(n - m) + 1)
    for i in range(1, n + 1):
        cur = np.full(m + 1, INF)
        lo, hi = max(1, i - w), min(m, i + w)
        di = d[i - 1]
        for j in range(lo, hi + 1):
            cur[j] = di[j - 1] + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return float(prev[m] / (n + m))


def _squash(f, k=20):
    idx = np.linspace(0, len(f) - 1, k)
    lo = np.floor(idx).astype(int)
    hi = np.minimum(lo + 1, len(f) - 1)
    w = (idx - lo)[:, None]
    return f[lo] * (1 - w) + f[hi] * w


# ------------------------------------------------------------------ data
def norm(s):
    return unicodedata.normalize("NFC", s)


def _meta_items(d):
    for name in ("meta_scored.json", "meta.json"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)["items"]
    raise FileNotFoundError("no meta.json in %s" % d)


def load_bank(unit_dir):
    """{unit text: clip path}; the first clip wins (put the preferred voice's folder first / name it meta_scored.json)."""
    bank = {}
    for it in _meta_items(unit_dir):
        if it.get("file"):
            p = os.path.join(unit_dir, it["file"])
            if os.path.realpath(p).startswith(os.path.realpath(unit_dir) + os.sep) and os.path.exists(p):
                bank.setdefault(norm(it["word_ml"]), p)
    return bank


def load_words(lexicon_csv, limit=None):
    """[(word, uses)] from a lexicon.csv (column word_ml, or the first column). `uses`/`sources` count if present, else 0."""
    csv.field_size_limit(10 ** 7)
    out, seen = [], set()
    with open(lexicon_csv, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        col = "word_ml" if "word_ml" in (rd.fieldnames or []) else (rd.fieldnames or ["word"])[0]
        for r in rd:
            w = norm(r.get(col, "").strip())
            if not w or w in seen:
                continue
            seen.add(w)
            try:
                uses = float(r.get("uses") or 0)
            except ValueError:
                uses = 0.0
            out.append((w, uses))
            if limit and len(out) >= limit:
                break
    return out


# ------------------------------------------------------------------ method 1: composed templates
class Composer:
    def __init__(self, bank):
        self.bank = bank
        self._wav = {}
        self._cache = {}

    def clip(self, unit):
        p = self.bank.get(norm(unit))
        if p is None:
            return None
        if p not in self._wav:
            self._wav[p] = jn.read_wav(p)
        return self._wav[p]

    def units_of(self, word):
        us = su.decompose(word)                                   # whole written units: a conjunct is never split
        return us if us and all(norm(u) in self.bank for u in us) else None

    def est_duration(self, word):
        """Seconds the joined word will last (clip lengths minus the crossfades), without building it; None if a unit has no clip."""
        us = self.units_of(word)
        if not us:
            return None
        clips = [self.clip(u) for u in us]
        return sum(len(c[1]) / float(c[0]) for c in clips) - 0.012 * (len(clips) - 1)

    def template(self, word):
        if word in self._cache:
            return self._cache[word]
        us = self.units_of(word)
        res = None
        if us:
            clips = [self.clip(u) for u in us]
            rate = clips[0][0]
            audio, edges = jn.join([jn.resample(c[1], c[0], rate) for c in clips], rate)
            if len(audio) > 400:
                res = {"units": us, "audio": audio, "rate": rate, "edges": edges, "feat": mfcc(audio, rate)}
        self._cache[word] = res
        return res


def rank_composed(query_feat, composer, words, shortlist=150, dur_ratio=(0.4, 2.5), max_templates=20000):
    """-> ([(word, dist)] best first, n_candidates, n_without_clips). Two stages: a cheap squashed-feature distance on every candidate whose
    length is plausible, then DTW on the best `shortlist`."""
    cands, missing = [], 0
    qs = _squash(query_feat)
    q_dur = len(query_feat) * 0.02
    built = 0
    for w in words:
        est = composer.est_duration(w)
        if est is None:
            missing += 1
            continue
        if not dur_ratio[0] <= est / max(q_dur, 1e-3) <= dur_ratio[1]:
            continue                                               # cheap length filter BEFORE the expensive template is built
        if built >= max_templates:
            break
        built += 1
        t = composer.template(w)
        if t is None:
            missing += 1
            continue
        r = len(t["feat"]) / float(len(query_feat))
        if not dur_ratio[0] <= r <= dur_ratio[1]:
            continue
        cands.append((float(np.sqrt(((qs - _squash(t["feat"])) ** 2).sum(axis=1)).mean()), w, t))
    cands.sort(key=lambda x: x[0])
    scored = sorted(((dtw(query_feat, t["feat"]), w) for _s, w, t in cands[:shortlist]))
    return [(w, d) for d, w in scored], len(cands), missing


# ------------------------------------------------------------------ method 2: label sequence
def _sub_cost(a, b):
    if a == b:
        return 0.0
    ca, cb = a[:1], b[:1]
    va, vb = a[1:], b[1:]
    same_set = any(ca in s and cb in s for s in NEIGHBOUR_SETS)
    if ca == cb:                                    # same consonant, other vowel / form (ക vs കാ)
        return 0.35
    if same_set and va == vb:
        return 0.4                                  # look-alike letter, same vowel (ശ vs ഷ)
    if same_set:
        return 0.7
    return 1.0


def unit_distance(p, q):
    """Weighted Levenshtein distance between two unit lists."""
    n, m = len(p), len(q)
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = float(i)
    for j in range(1, m + 1):
        d[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1.0, d[i][j - 1] + 1.0, d[i - 1][j - 1] + _sub_cost(p[i - 1], q[j - 1]))
    return d[n][m]


def rank_labels(heard, words, prior=0.0):
    """Rank `words` by the distance between the units of the recogniser's text and each word's units. `prior` (0..1) subtracts a small
    bonus for frequent words (uses), only to break near ties."""
    q = su.decompose(heard)
    if not q:
        raise ValueError("the recognised text must be Malayalam script (it could not be cut into units)")
    mx = max([u for _w, u in words] + [1.0])
    scored = []
    for w, uses in words:
        us = su.decompose(w)
        if not us:
            continue
        scored.append((unit_distance(q, us) / max(len(q), len(us)) - prior * 0.05 * (uses / mx), w))
    scored.sort()
    return [(w, d) for d, w in scored]


def fuse(*rankings, k=10, weights=None):
    """Reciprocal-rank fusion of several [(word, score)] lists -> [(word, fused)] best first."""
    weights = weights or [1.0] * len(rankings)
    acc = {}
    for wt, r in zip(weights, rankings):
        for i, (w, _s) in enumerate(r):
            acc[w] = acc.get(w, 0.0) + wt / (k + i + 1)
    return sorted(acc.items(), key=lambda x: -x[1])


# ------------------------------------------------------------------ top level
def search(query_wav, bank, words, heard=None, top=5, shortlist=150):
    return search_with(query_wav, Composer(bank), words, heard, top, shortlist)


def search_with(query_wav, comp, words, heard=None, top=5, shortlist=150):
    """Like search() but reuses a Composer, so the joined templates are built once and kept (what the recorder does)."""
    rate, x = jn.read_wav(query_wav)
    qf = mfcc(x, rate)
    wlist = [w for w, _u in words]
    composed, n_cand, missing = rank_composed(qf, comp, wlist, shortlist)
    out = {"composed": composed[:top], "candidates_with_clips": n_cand, "words_without_all_unit_clips": missing,
           "labels": [], "fused": [], "note": ""}
    if heard:
        lab = rank_labels(heard, words)
        out["labels"] = lab[:top]
        out["fused"] = fuse(composed, lab)[:top]
    else:
        out["note"] = "no recogniser text given: only the composed-template ranking is shown"
    return out


def _rank_of(r, word):
    for i, (w, _s) in enumerate(r):
        if w == word:
            return i + 1
    return None


def evaluate(queries, bank, words, heard=None, shortlist=150):
    """queries: [(word, wav path)]; heard: {word: recognised text}. -> {method: {top1, top5, mrr, n}}, per-query rows (failures included)."""
    comp = Composer(bank)
    wlist = [w for w, _u in words]
    rows = []
    for word, path in queries:
        rate, x = jn.read_wav(path)
        composed, _n, _m = rank_composed(mfcc(x, rate), comp, wlist, shortlist)
        row = {"word": word, "composed": _rank_of(composed, word)}
        if heard and word in heard:
            lab = rank_labels(heard[word], words)
            row["labels"] = _rank_of(lab, word)
            row["fused"] = _rank_of(fuse(composed, lab), word)
        rows.append(row)
    summary = {}
    for m in ("composed", "labels", "fused"):
        rs = [r for r in rows if m in r]
        if rs:
            n = len(rs)
            summary[m] = {"n": n, "top1": round(sum(1 for r in rs if r[m] == 1) / n, 3), "top5": round(sum(1 for r in rs if r[m] and r[m] <= 5) / n, 3),
                          "mrr": round(sum(1.0 / r[m] for r in rs if r[m]) / n, 3), "not_found": sum(1 for r in rs if not r[m])}
    return summary, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--units", required=True)
    s.add_argument("--lexicon", required=True)
    s.add_argument("--heard", help="what a recogniser heard, in Malayalam script")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--limit", type=int, help="only use the first N lexicon words")
    e = sub.add_parser("eval")
    e.add_argument("--queries", required=True)
    e.add_argument("--units", required=True)
    e.add_argument("--lexicon", required=True)
    e.add_argument("--heard-file", help="JSON {word: recognised text}")
    e.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    try:
        bank = load_bank(a.units)
        words = load_words(a.lexicon, a.limit)
        if a.cmd == "search":
            r = search(a.query, bank, words, a.heard, a.top)
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0
        heard = json.load(open(a.heard_file, encoding="utf-8")) if a.heard_file else None
        qs = [(norm(it["word_ml"]), os.path.join(a.queries, it["file"])) for it in _meta_items(a.queries) if it.get("file")]
        summary, rows = evaluate(qs, bank, words, heard)
        print(json.dumps({"summary": summary, "per_word": rows, "chance_top1": round(1.0 / max(1, len(words)), 5), "words": len(words),
                          "units_with_clips": len(bank)}, ensure_ascii=False, indent=1))
        return 0
    except (OSError, ValueError, KeyError) as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
