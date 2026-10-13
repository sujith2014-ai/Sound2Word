#!/usr/bin/env python3
"""s2w_recorder: local live-recording page for PERSONAL SCREEN TESTS (Python standard library only).

  python3 tools/s2w_recorder.py --record-dir data/private/voice_demo --word "കലം|kalam" [--word "കാലം|kaalam|k aa l a m"] [--build build] [--port 8765]

Open http://127.0.0.1:PORT, pick a word, press Record, speak, press Stop. You see the live graph, then the recorded
waveform and spectrogram with a Play button. The word is split into its sounds (K A L A M); boundaries are a FIRST GUESS
from spectral changes that you drag to where you hear each sound start; every sound can be played on its own. Accept saves
the take with your boundaries; Discard throws it away and nothing is written. /compare lines two takes up sound by sound
(durations and spectral difference per sound), e.g. KALAM against KAALAM.

Optional dictionary sidebar: --dictionary FILE.csv (e.g. an Olam open dataset you downloaded; it is only READ) lets you search by
Malayalam, English or Manglish and add a word to your recording list with one click. Dictionary words are only SUGGESTIONS: the
Manglish spelling is an automatic transliteration (check it), and every added word is logged as UNVERIFIED in
candidates_unverified.csv. Native-speaker review (docs/WORD_REVIEW.md) is still what makes a word verified.

Sound units come from the Manglish spelling by simple text rules (aa = one long vowel, tt = one geminate consonant, th/ch/zh
= one consonant); give them explicitly as a third field ("k aa l a m") when the rule is wrong. Boundaries are estimates or
human-placed, never ground truth: neighbouring sounds overlap in real speech.

Safety: loopback only; writes ONLY take_NNN.wav + takes.csv inside --record-dir; a per-run token and a Host check
stop other web pages from posting audio; uploads are validated (16-bit mono WAV, 0.3-20 s, <= 12 MB).
The recordings are NOT experiment data (no consent record, unverified words) and say nothing about recognition accuracy.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import signal
import sys
import tempfile
import wave
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_viewer as v  # noqa: E402

MAX_BYTES = 12 * 1024 * 1024
MIN_S, MAX_S = 0.3, 20.0
TAKE_RE = re.compile(r"^take_(\d{3})\.wav$")
MANGLISH_RE = re.compile(r"^[A-Za-z]{1,30}$")


VOWELS = ["aa", "ee", "ii", "oo", "uu", "ai", "au", "ou", "a", "e", "i", "o", "u"]
CONS2 = ["chh", "kh", "gh", "ch", "jh", "sh", "zh", "th", "dh", "bh", "ph", "ng", "nj"]
LONG_VOWELS = {"aa", "ee", "ii", "oo", "uu"}


def split_units(manglish):
    """Manglish -> sound units by text rules: long vowels (aa), diphthongs (ai), consonant digraphs (ch zh th) and doubled
    consonants (tt, ll) are ONE unit each. A display/segmentation aid only; override with an explicit unit list when wrong."""
    s, i, out = manglish.lower(), 0, []
    while i < len(s):
        for vw in VOWELS:
            if s.startswith(vw, i):
                out.append(vw)
                i += len(vw)
                break
        else:
            c = next((d for d in CONS2 if s.startswith(d, i)), s[i])
            if s.startswith(c + c, i):
                out.append(c + c)
                i += 2 * len(c)
            else:
                out.append(c)
                i += len(c)
    return out


def base_unit(u):
    """The unit without its length: aa -> a, tt -> t, tth -> th (used to spot length contrasts)."""
    if u in LONG_VOWELS:
        return u[0]
    h = len(u) // 2
    return u[:h] if len(u) >= 2 and len(u) % 2 == 0 and u[:h] == u[h:] else u


def parse_word(spec, strict=True):
    parts = [x.strip() for x in spec.split("|")]
    ml, rom = parts[0], (parts[1] if len(parts) > 1 else "")
    if len(parts) not in (2, 3) or not ml or not MANGLISH_RE.match(rom):
        raise v.ViewerError(2, "bad --word %r: use 'MALAYALAM|manglish' or 'MALAYALAM|manglish|k a l a m' with ASCII letters A-Z only" % spec)
    units = parts[2].lower().split() if len(parts) == 3 else split_units(rom)
    if not units or len(units) > 14 or any(not re.match(r"^[a-z]{1,4}$", u) for u in units) or (strict and "".join(units) != rom.lower()):
        raise v.ViewerError(2, "bad sound units for %r: they must be ASCII letters and join up to the Manglish spelling" % rom)
    return {"id": rom.lower(), "label": ml, "manglish": rom, "units": units}


def align_units(ua, ub):
    """Needleman-Wunsch over sound units. -> [(i|None, j|None, relation)], relation in identical/length/different/only_a/only_b."""
    def sc(a, b):
        return 2.0 if a == b else 1.2 if base_unit(a) == base_unit(b) else -1.0
    n, m, gap = len(ua), len(ub), -1.0
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = i * gap
    for j in range(1, m + 1):
        d[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = max(d[i - 1][j - 1] + sc(ua[i - 1], ub[j - 1]), d[i - 1][j] + gap, d[i][j - 1] + gap)
    i, j, rows = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and abs(d[i][j] - (d[i - 1][j - 1] + sc(ua[i - 1], ub[j - 1]))) < 1e-9:
            a, b = ua[i - 1], ub[j - 1]
            rows.append((i - 1, j - 1, "identical" if a == b else "length" if base_unit(a) == base_unit(b) else "different"))
            i, j = i - 1, j - 1
        elif i > 0 and abs(d[i][j] - (d[i - 1][j] + gap)) < 1e-9:
            rows.append((i - 1, None, "only_a"))
            i -= 1
        else:
            rows.append((None, j - 1, "only_b"))
            j -= 1
    return rows[::-1]


def suggest_edges(mat, n, hop=0.010, frame=0.025, min_frames=4):
    """First-guess unit edges [start, b1..b(n-1), end] (seconds) from spectral change: the speech region is where the mean
    log-mel energy is high; boundaries go at the n-1 strongest spectral-flux peaks at least 50 ms apart (widest gaps are
    split if too few peaks). An ESTIMATE of unknown accuracy on real speech; meant to be corrected by hand."""
    if not mat or n < 1:
        return None
    nf = len(mat)
    e = [sum(r) / len(r) for r in mat]
    lo, hi = min(e), max(e)
    act = [i for i, x in enumerate(e) if x > lo + 0.30 * (hi - lo)] or [0, nf - 1]
    first, last = act[0], act[-1]
    flux = [0.0] + [math.sqrt(sum((a - b) ** 2 for a, b in zip(mat[i], mat[i - 1])) / len(mat[i])) for i in range(1, nf)]
    sm = [sum(flux[max(0, i - 1):i + 2]) / len(flux[max(0, i - 1):i + 2]) for i in range(nf)]
    cands = [i for i in range(max(first + min_frames, 1), min(last - min_frames, nf - 2) + 1) if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1]]
    chosen = []
    for i in sorted(cands, key=lambda k: -sm[k]):
        if len(chosen) < n - 1 and all(abs(i - c) >= 5 for c in chosen):
            chosen.append(i)
    cuts = sorted(chosen)
    while len(cuts) < n - 1:                                   # not enough peaks: split the widest gap
        pts = [first] + cuts + [last + 1]
        k = max(range(len(pts) - 1), key=lambda t: pts[t + 1] - pts[t])
        cuts = sorted(cuts + [(pts[k] + pts[k + 1]) // 2])
    t = lambda i: round((i - 0.5) * hop + frame / 2, 4)        # boundary between frame i-1 and frame i
    return [round(first * hop + (frame - hop) / 2, 4)] + [t(c) for c in cuts] + [round(last * hop + (frame + hop) / 2, 4)]


def call_aligner(url, data, duration, timeout=120):
    """Ask the optional local aligner service (tools/s2w_aligner_service.py) where the spoken word starts and ends.
    -> {"ok": True, "start", "end", "text", "model", "words"} or {"ok": False, "error"}. Never raises: the recorder works without it."""
    import urllib.error
    import urllib.request
    pu = urlparse(url)
    if pu.scheme != "http" or pu.hostname not in ("127.0.0.1", "localhost", "host.docker.internal"):
        return {"ok": False, "error": "the aligner URL must be http://127.0.0.1:PORT, http://localhost:PORT or http://host.docker.internal:PORT"}
    req = urllib.request.Request(url.rstrip("/") + "/align?lang=ml", data=data, headers={"Content-Type": "audio/wav"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read(1 << 20).decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as e:
        return {"ok": False, "error": "aligner service not reachable: %s" % e}
    if not isinstance(d, dict) or not d.get("ok"):
        return {"ok": False, "error": str(d.get("error", "aligner failed"))[:200] if isinstance(d, dict) else "bad aligner response"}
    st, en = d.get("start"), d.get("end")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (st, en)) or not (0 <= st < en <= duration + 0.05) or en - st < 0.05:
        return {"ok": False, "error": "the aligner returned an unusable word span"}
    return {"ok": True, "start": float(st), "end": min(float(en), duration), "text": str(d.get("text", ""))[:80], "model": str(d.get("model", ""))[:40]}


def aligned_edges(mat, n_units, a0, a1, hop=0.010):
    """Unit edges inside the model's word span [a0, a1]: outer edges are the span, inner ones come from spectral change computed
    only within the span (even split when that fails). -> (edges, "spectral" | "even")."""
    inner = None
    i0, i1 = int(a0 / hop), int(a1 / hop) + 1
    if n_units > 1 and i1 - i0 > 5 * n_units:
        sub = suggest_edges(mat[i0:i1], n_units)
        if sub:
            inner = [round(i0 * hop + x, 4) for x in sub[1:-1]]
    if inner is not None:
        edges = [round(a0, 4)] + inner + [round(a1, 4)]
        if all(b - a >= 0.012 for a, b in zip(edges, edges[1:])):
            return edges, "spectral"
    return [round(a0 + k * (a1 - a0) / n_units, 4) for k in range(n_units + 1)], "even"


def validate_segments(seg, units, duration):
    if not isinstance(seg, dict) or seg.get("units") != units:
        raise v.ViewerError(422, "segment units do not match the word")
    ed = seg.get("edges")
    if not isinstance(ed, list) or len(ed) != len(units) + 1 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in ed):
        raise v.ViewerError(422, "segments need %d numeric edges" % (len(units) + 1))
    if ed[0] < -1e-6 or ed[-1] > duration + 1e-3 or any(b - a < 0.01 for a, b in zip(ed, ed[1:])):
        raise v.ViewerError(422, "segment edges must increase (each sound at least 10 ms) and lie inside the recording")
    if seg.get("method") not in ("suggested", "edited", "default-split", "model-aligned"):
        raise v.ViewerError(422, "unknown segment method")
    return {"units": units, "edges": [round(float(x), 4) for x in ed], "method": seg["method"]}


MAL = re.compile("[\u0d00-\u0d7f]")
_CONS = {"ക": "k", "ഖ": "kh", "ഗ": "g", "ഘ": "gh", "ങ": "ng", "ച": "ch", "ഛ": "chh", "ജ": "j", "ഝ": "jh", "ഞ": "nj", "ട": "t", "ഠ": "th", "ഡ": "d", "ഢ": "dh",
         "ണ": "n", "ത": "th", "ഥ": "th", "ദ": "d", "ധ": "dh", "ന": "n", "പ": "p", "ഫ": "ph", "ബ": "b", "ഭ": "bh", "മ": "m", "യ": "y", "ര": "r", "ല": "l",
         "വ": "v", "ശ": "sh", "ഷ": "sh", "സ": "s", "ഹ": "h", "ള": "l", "ഴ": "zh", "റ": "r"}
_VOW = {"അ": "a", "ആ": "aa", "ഇ": "i", "ഈ": "ee", "ഉ": "u", "ഊ": "oo", "ഋ": "ru", "എ": "e", "ഏ": "e", "ഐ": "ai", "ഒ": "o", "ഓ": "o", "ഔ": "au"}
_SIGN = {"ാ": "aa", "ി": "i", "ീ": "ee", "ു": "u", "ൂ": "oo", "ൃ": "ru", "െ": "e", "േ": "e", "ൈ": "ai", "ൊ": "o", "ോ": "o", "ൌ": "au", "ൗ": "au"}
_CHILLU = {"ൺ": "n", "ൻ": "n", "ർ": "r", "ൽ": "l", "ൾ": "l", "ൿ": "k"}
VIRAMA = "\u0d4d"


def ml_to_manglish(word):
    """Malayalam script -> a SUGGESTED ASCII Manglish spelling (consonant + inherent 'a', vowel signs, virama, chillu, final
    virama -> 'u'; the geminate RRa 'റ്റ' -> tt). It cannot know voicing (പടി -> pati, not padi) or o/oo length: a reviewer must
    check it. Characters it does not know are dropped."""
    w = word.replace("\u200c", "").replace("\u200d", "").replace("റ്റ", "ട്ട").replace("ങ്ങ", "ങ")
    out, i, n = [], 0, len(w)
    while i < n:
        c = w[i]
        if c in _CONS:
            out.append(_CONS[c])
            nxt = w[i + 1] if i + 1 < n else ""
            if nxt in _SIGN:
                out.append(_SIGN[nxt])
                i += 2
            elif nxt == VIRAMA:
                if i + 2 >= n or not MAL.match(w[i + 2]):
                    out.append("u")                       # word-final virama (samvruthokaram)
                i += 2
            else:
                out.append("a")
                i += 1
        elif c in _VOW:
            out.append(_VOW[c])
            i += 1
        elif c in _CHILLU:
            out.append(_CHILLU[c])
            i += 1
        elif c == "ം":
            out.append("m")
            i += 1
        elif c == "ഃ":
            out.append("h")
            i += 1
        else:
            if c.isascii() and c.isalpha():
                out.append(c.lower())
            i += 1
    return "".join(out)


_IPA = {"a": "a", "aː": "aa", "i": "i", "iː": "ii", "u": "u", "uː": "uu", "e": "e", "eː": "ee", "o": "o", "oː": "oo", "ə": "u", "ai̯": "ai", "au̯": "au",
        "k": "k", "ɡ": "g", "ŋ": "ng", "t͡ʃ": "ch", "ɟ": "j", "ɲ": "nj", "ʈ": "t", "ɖ": "d", "ɳ": "n", "t̪": "th", "d": "d", "d̪": "d", "n": "n", "n̪": "n",
        "p": "p", "b": "b", "m": "m", "j": "y", "ɾ": "r", "r": "rr", "l": "l", "ɭ": "lh", "ɻ": "zh", "ʋ": "v", "ʃ": "sh", "ʂ": "sh", "s": "s", "ɦ": "h",
        "h": "h", "f": "f", "ṯ": "t"}


def ipa_units(phonemes):
    """mlphon phoneme list -> our ASCII sound units (long vowels aa/ii/uu/ee/oo; doubled consonants merged into one geminate unit,
    e.g. ʈ ʈ -> tt). None if a symbol is not mapped (then the spelling rule is used instead)."""
    out = []
    for ph in phonemes:
        if ph.strip() == "":
            continue
        if ph in _IPA:
            u = _IPA[ph]
        elif ph[-1:] in ("ʰ", "ʱ") and ph[:-1] in _IPA and _IPA[ph[:-1]][-1] not in "aeiou":
            u = _IPA[ph[:-1]] + "h"
        else:
            return None
        if out and out[-1][0] == u and u == "ng" and not out[-1][1]:
            out[-1] = ("ng", True)                        # ങ്ങ: kept as one 'ng' unit (a 4-letter ngng is not a Manglish habit)
        elif out and out[-1][0] == u and u[-1] not in "aeiou" and len(u) <= 2 and not out[-1][1]:
            out[-1] = ((u[0] + u) if u[-1] == "h" else (u + u), True)      # geminate: tt, mm; for aspirate-like units tth, llh, ssh
        else:
            out.append((u, False))
    return [u for u, _ in out] or None


_MLPHON = []


def g2p(ml):
    """(units, ipa string) from the optional `mlphon` package (pip install mlphon), or None when it is missing or cannot analyse the
    word. Pronunciation data is far better than a spelling rule: it keeps vowel length (കോടി vs കൊടി) and gemination."""
    if not _MLPHON:
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from mlphon import PhoneticAnalyser, split_as_phonemes
                _MLPHON.append((PhoneticAnalyser(), split_as_phonemes))
        except Exception:
            _MLPHON.append(None)
    if _MLPHON[0] is None:
        return None
    try:
        an, sp = _MLPHON[0]
        res = an.analyse(ml)
        ph = sp(res[0]) if res else None
        if isinstance(ph, str):                           # split_as_phonemes returns a space-separated string
            ph = ph.split()
    except Exception:
        return None
    units = ipa_units(ph) if ph else None
    return (units, " ".join(x for x in ph if x.strip())) if units else None


def _sanitise_cell(x):
    x = str(x).replace("\r", " ").replace("\n", " ")
    return "'" + x if x[:1] in ("=", "+", "-", "@", "\t") else x


class Dictionary:
    """Read-only search over a local dictionary CSV/TSV (e.g. an Olam open dataset). The layout is detected from the data:
    a column that is mostly Malayalam script holds the Malayalam words (several per cell are split at , ; / |), an optional
    mostly-English column holds the meaning. Override with ml_col / en_col (column name or 0-based index)."""

    def __init__(self, path, ml_col=None, en_col=None, no_header=False, max_entries=2000000):
        import csv as _csv
        _csv.field_size_limit(10 ** 7)
        if not os.path.isfile(path):
            raise v.ViewerError(2, "dictionary file not found: %s" % path)
        self.name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                head = f.read(8192)
                f.seek(0)
                delim = "\t" if head.count("\t") > head.count(",") else ","
                rows = [r for r in _csv.reader(f, delimiter=delim) if r]
        except (OSError, UnicodeDecodeError, _csv.Error) as e:
            raise v.ViewerError(2, "cannot read dictionary %s: %s" % (self.name, e))
        if not rows:
            raise v.ViewerError(2, "dictionary %s is empty" % self.name)
        header = (not no_header and not any(MAL.search(c) for c in rows[0]) and len(rows) > 1 and any(MAL.search(c) for c in rows[1])
                  and all(len(c) <= 40 for c in rows[0]))
        names = [c.strip().lower() for c in rows[0]] if header else []
        data = rows[1:] if header else rows
        ncol = max(len(r) for r in data[:500]) if data else 0
        sample = data[:500]

        def frac(ci, pred):
            cells = [r[ci] for r in sample if ci < len(r) and r[ci].strip()]
            return sum(1 for c in cells if pred(c)) / len(cells) if cells else 0.0
        isml = lambda c: bool(MAL.search(c))
        isen = lambda c: bool(re.search("[A-Za-z]", c)) and not MAL.search(c)

        def resolve(spec):
            if spec is None:
                return None
            if str(spec).isdigit() and int(spec) < ncol:
                return int(spec)
            if str(spec).lower() in names:
                return names.index(str(spec).lower())
            raise v.ViewerError(2, "dictionary column %r not found (columns: %s)" % (spec, ", ".join(names) or "0.." + str(ncol - 1)))
        mlc, enc = resolve(ml_col), resolve(en_col)
        if mlc is None and "word_ml" in names:                  # a lexicon built by tools/s2w_lexicon.py
            mlc = names.index("word_ml")
        if enc is None and "meaning_en" in names:
            enc = names.index("meaning_en")
        pre = {k: names.index(k) for k in ("manglish", "sound_units", "ipa", "meaning_ml", "g2p") if k in names}
        if mlc is None:
            cands = [ci for ci in range(ncol) if frac(ci, isml) >= 0.5]
            if not cands:
                raise v.ViewerError(2, "no Malayalam-script column found in %s (use --dict-ml-col)" % self.name)
            pref = [ci for ci in cands if names and any(k in names[ci] for k in ("malayalam", "word", "head", "ml"))]
            mlc = (pref or cands)[0]
        if enc is None:
            cands = [ci for ci in range(ncol) if ci != mlc and frac(ci, isen) >= 0.5 and sum(len(r[ci]) for r in sample if ci < len(r)) / max(1, len(sample)) < 120]
            pref = [ci for ci in cands if names and any(k in names[ci] for k in ("english", "meaning", "en", "word"))]
            enc = (pref or cands or [None])[0]
        other_ml = [ci for ci in range(ncol) if ci not in (mlc, enc) and frac(ci, isml) >= 0.5]
        self.entries, seen = [], set()
        for r in data:
            if mlc >= len(r):
                continue
            meaning = r[enc].strip() if enc is not None and enc < len(r) else (r[other_ml[0]].strip() if other_ml and other_ml[0] < len(r) else "")
            if not meaning and "meaning_ml" in pre and pre["meaning_ml"] < len(r):
                meaning = r[pre["meaning_ml"]].strip()
            cellv = lambda k: r[pre[k]].strip() if k in pre and pre[k] < len(r) else ""
            cell = re.sub(r"\([^)]*\)", " ", r[mlc])
            for tok in re.split(r"[;,/|\n]+", cell):
                t = tok.strip(" .:-\u2013\u2014\"'")
                if not t or not MAL.search(t) or len(t) > 60 or (t, meaning) in seen:
                    continue
                seen.add((t, meaning))
                single = " " not in t and all(MAL.match(ch) or ch in "\u200c\u200d" for ch in t)
                tr = (cellv("manglish") or ml_to_manglish(t)) if single else ""
                su = cellv("sound_units").split()
                pre_units = (su, cellv("ipa"), cellv("g2p") == "yes") if single and su and all(re.match(r"^[a-z]{1,4}$", x) for x in su) else None
                self.entries.append((t, meaning[:240], tr, meaning[:240].lower(), pre_units))
                if len(self.entries) >= max_entries:
                    break
            if len(self.entries) >= max_entries:
                break
        if not self.entries:
            raise v.ViewerError(2, "dictionary %s contained no usable Malayalam entries" % self.name)
        self.entries.sort(key=lambda e: e[0])                                         # the sidebar lists the dictionary A-Z
        self.columns = {"malayalam": names[mlc] if names else mlc, "meaning": (names[enc] if names else enc) if enc is not None else None}

    def search(self, q, limit=40):
        q = q.strip().lower()
        if not q or len(q) > 60:
            return []
        scored = []
        if MAL.search(q):
            for i, (ml, mean, tr, ml_lc, _pu) in enumerate(self.entries):
                rank = 0 if ml == q else 1 if ml.startswith(q) else 2 if q in ml else None
                if rank is not None:
                    scored.append((rank, len(ml), i))
        else:
            for i, (ml, mean, tr, mean_lc, _pu) in enumerate(self.entries):
                if tr == q:
                    rank = 0
                elif tr and tr.startswith(q):
                    rank = 1
                elif mean_lc.startswith(q):
                    rank = 2
                elif (" " + q) in (" " + mean_lc):
                    rank = 3
                elif tr and q in tr:
                    rank = 4
                else:
                    continue
                scored.append((rank, len(ml), i))
        scored.sort()
        top = sorted(scored[:limit], key=lambda t: (self.entries[t[2]][0], t[0], t[2]))      # best `limit` matches, listed alphabetically
        return [self._row(i) for _, _, i in top]

    def browse(self, offset=0, limit=100):
        """A page of the dictionary in file order (the lexicon builder writes it alphabetically)."""
        offset = max(0, int(offset))
        return [self._row(i) for i in range(offset, min(len(self.entries), offset + max(1, int(limit))))]

    def _row(self, i):
        ml, mean, tr, _lc, pu = self.entries[i]
        g = g2p(ml) if tr and not pu else None
        if pu:
            return {"ml": ml, "meaning": mean, "manglish": tr, "units": pu[0], "ipa": pu[1], "g2p": pu[2], "can_add": True}
        if g:
            return {"ml": ml, "meaning": mean, "manglish": "".join(g[0]), "units": g[0], "ipa": g[1], "g2p": True, "can_add": True}
        return {"ml": ml, "meaning": mean, "manglish": tr, "units": split_units(tr) if tr else [], "ipa": "", "g2p": False, "can_add": bool(tr)}


def validate_wav(data):
    """-> (sample_rate, n_frames). Raises ViewerError(422) for anything that is not a usable 16-bit mono WAV."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise v.ViewerError(422, "recording must be 16-bit mono WAV")
            rate, n = w.getframerate(), w.getnframes()
            if not 8000 <= rate <= 96000:
                raise v.ViewerError(422, "unsupported sample rate %d" % rate)
            if len(w.readframes(n)) != 2 * n:
                raise v.ViewerError(422, "WAV data is truncated")
    except (wave.Error, EOFError):
        raise v.ViewerError(422, "not a valid WAV file")
    d = n / rate
    if d < MIN_S:
        raise v.ViewerError(422, "recording is too short (%.2f s; minimum %.1f s)" % (d, MIN_S))
    if d > MAX_S:
        raise v.ViewerError(422, "recording is too long (%.1f s; maximum %d s)" % (d, MAX_S))
    return rate, n


class Recorder:
    MAX_WORDS = 200

    def __init__(self, record_dir, words, build="build", dictionary=None, aligner_url=None):
        self.dir, self.build, self.dictionary, self.aligner_url = record_dir, build, dictionary, aligner_url
        self.words = {w["id"]: w for w in words}
        os.makedirs(self.dir, exist_ok=True)
        self.token = secrets.token_urlsafe(16)
        self.load_saved_words()
        if not self.words and dictionary is None:
            raise v.ViewerError(2, "give at least one --word 'MALAYALAM|manglish' (or run with --dictionary and add words in the sidebar)")

    def words_file(self):
        return os.path.join(self.dir, "words.json")

    def load_saved_words(self):
        try:
            with open(self.words_file(), encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        for e in saved if isinstance(saved, list) else []:
            try:
                w = parse_word("%s|%s|%s" % (e["label"], e["manglish"], " ".join(e["units"])), strict=not e.get("g2p"))
                w["g2p"] = bool(e.get("g2p"))
            except (v.ViewerError, KeyError, TypeError):
                continue
            if w["id"] not in self.words and len(self.words) < self.MAX_WORDS:
                w["meaning"], w["source"] = str(e.get("meaning", ""))[:240], str(e.get("source", ""))[:80]
                self.words[w["id"]] = w

    def add_word(self, label, manglish, units=None, meaning="", source="", g2p_units=False):
        """Add a word to the recording list (persisted in words.json) and log it as an UNVERIFIED candidate."""
        if not isinstance(label, str) or not isinstance(manglish, str) or not label.strip() or len(label) > 60 or any(c in label for c in "|\n\r\t"):
            raise v.ViewerError(422, "give the Malayalam word (up to 60 characters, no | or line breaks)")
        try:
            w = parse_word("%s|%s%s" % (label.strip(), manglish.strip(), "|" + " ".join(units) if units else ""), strict=not (g2p_units and units))
        except v.ViewerError as e:
            raise v.ViewerError(422, "The Manglish spelling must use the letters A-Z only (no accents or spaces)" if "bad --word" in e.message else
                                "The sound units must be letters A-Z and join up to the Manglish spelling")
        w["g2p"] = bool(g2p_units and units)
        if w["id"] in self.words:
            raise v.ViewerError(409, "'%s' is already in your word list" % w["manglish"])
        if len(self.words) >= self.MAX_WORDS:
            raise v.ViewerError(422, "word list is full (%d words)" % self.MAX_WORDS)
        w["meaning"], w["source"] = str(meaning)[:240], str(source)[:80]
        self.words[w["id"]] = w
        saved = []
        try:
            with open(self.words_file(), encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            pass
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        saved.append({"label": w["label"], "manglish": w["manglish"], "units": w["units"], "g2p": w["g2p"], "meaning": w["meaning"], "source": w["source"], "added_utc": now})
        tmp = self.words_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(saved, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.words_file())
        cand = os.path.join(self.dir, "candidates_unverified.csv")
        new = not os.path.exists(cand)
        with open(cand, "a", newline="", encoding="utf-8") as f:
            cw = csv.writer(f)
            if new:
                cw.writerow(["word_ml", "manglish_suggested", "sound_units", "meaning_en", "source", "added_utc", "status", "note"])
            cw.writerow([_sanitise_cell(w["label"]), w["manglish"], " ".join(w["units"]), _sanitise_cell(w["meaning"]), _sanitise_cell(w["source"]), now, "UNVERIFIED",
                         "dictionary/typed suggestion: spelling, meaning, familiarity and pronunciation still need the two-reviewer review"])
        return w

    def tool(self, name):
        return os.path.join(self.build, name)

    def word_dir(self, wid):
        return os.path.join(self.dir, wid)

    def takes(self, wid):
        d = self.word_dir(wid)
        names = sorted(n for n in (os.listdir(d) if os.path.isdir(d) else []) if TAKE_RE.match(n))
        out = []
        for n in names:
            try:
                with wave.open(os.path.join(d, n), "rb") as w:
                    out.append({"name": n, "duration": round(w.getnframes() / w.getframerate(), 3), "segments": os.path.isfile(os.path.join(d, n[:-4] + ".segments.json"))})
            except (wave.Error, EOFError, OSError):
                out.append({"name": n, "duration": None, "segments": False})
        return out

    def listing(self):
        return [dict(w, takes=self.takes(w["id"])) for w in self.words.values()]

    def dict_search(self, q, limit=40, offset=None):
        """offset=None: search; an integer offset: a page of the whole dictionary (the sidebar's default alphabetical list)."""
        if self.dictionary is None:
            return {"available": False, "results": [], "note": "No dictionary loaded. Start the recorder with --dictionary FILE (see --help)."}
        limit = max(1, min(int(limit), 200))
        res = self.dictionary.search(q, limit) if offset is None else self.dictionary.browse(offset, limit)
        return {"available": True, "name": self.dictionary.name, "count": len(self.dictionary.entries), "results": res}

    def segments(self, wid, name):
        p = self.take_path(wid, name)
        sp = p[:-4] + ".segments.json" if p else None
        if not sp or not os.path.isfile(sp):
            return None
        with open(sp, encoding="utf-8") as f:
            return json.load(f)

    def side(self, ref):
        wid, _, name = ref.partition("/")
        path = self.take_path(wid, name)
        if path is None:
            raise v.ViewerError(404, "unknown take: %s" % ref)
        seg = self.segments(wid, name)
        if seg is None:
            raise v.ViewerError(422, "%s has no sound boundaries (accept takes with the segment editor)" % ref)
        samples, rate = v.load_wav(path)
        feat = v.run_tool(self.tool("s2w_features"), [path, "--out", "mfcc"])
        if feat.returncode != 0:
            raise v.ViewerError(422, "feature extraction failed for %s" % ref)
        # un-normalised MFCCs: per-utterance mean normalisation would shift every sound whenever the REST of the word differs,
        # making identical sounds look different. Both takes come from the same microphone, so channel effects are shared.
        mfcc = v.parse_matrix(feat.stdout, "mfcc_raw")
        means = []
        for k in range(len(seg["units"])):
            a, b = seg["edges"][k], seg["edges"][k + 1]
            fr = [r_[1:13] for i, r_ in enumerate(mfcc) if a <= i * 0.010 + 0.0125 < b] or [mfcc[min(len(mfcc) - 1, max(0, int((a + b) / 2 / 0.010)))][1:13]]
            means.append([sum(c) / len(fr) for c in zip(*fr)])
        return {"ref": ref, "word": self.words[wid], "path": path, "duration": len(samples) / rate, "peaks": v.peaks(samples, 1200), "segments": seg, "means": means}

    def compare(self, a, b):
        A, B = self.side(a), self.side(b)
        rows = []
        for ia, ib, rel in align_units(A["segments"]["units"], B["segments"]["units"]):
            row = {"relation": rel, "ia": ia, "ib": ib, "ua": A["segments"]["units"][ia] if ia is not None else None, "ub": B["segments"]["units"][ib] if ib is not None else None}
            if ia is not None:
                row["dur_a"] = round(A["segments"]["edges"][ia + 1] - A["segments"]["edges"][ia], 4)
            if ib is not None:
                row["dur_b"] = round(B["segments"]["edges"][ib + 1] - B["segments"]["edges"][ib], 4)
            if ia is not None and ib is not None:
                row["ratio"] = round(row["dur_a"] / row["dur_b"], 3)
                row["spectral"] = round(math.sqrt(sum((x - y) ** 2 for x, y in zip(A["means"][ia], B["means"][ib])) / 12), 4)
            rows.append(row)
        whole = {"distance": None, "note": ""}
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as m:
            m.write("B %s\n" % B["path"])
        try:
            p = v.run_tool(self.tool("s2w_match"), [m.name, A["path"], "--k", "1"])
        finally:
            os.unlink(m.name)
        res = dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln)
        if "rank1" in res and p.returncode in (0, 1):
            dist = float(res["rank1"].split()[1])
            if math.isfinite(dist):
                whole["distance"] = round(dist, 4)
            else:
                whole["note"] = "not comparable: the matcher refuses pairs whose lengths differ by more than 2x"
        else:
            whole["note"] = "whole-word distance unavailable"
        side = lambda S: {"ref": S["ref"], "label": S["word"]["label"], "manglish": S["word"]["manglish"], "duration": S["duration"], "peaks": S["peaks"], "units": S["segments"]["units"],
                          "edges": S["segments"]["edges"], "method": S["segments"]["method"]}
        return {"a": side(A), "b": side(B), "rows": rows, "whole": whole}

    def take_path(self, wid, name):
        if wid not in self.words or not TAKE_RE.match(name):
            return None
        p = os.path.join(self.word_dir(wid), name)
        return p if os.path.isfile(p) else None

    def analyse(self, data, wid=None):
        rate, n = validate_wav(data)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.wav")
            with open(p, "wb") as f:
                f.write(data)
            qa = v.App.qa(self, p)
            al = None
            feat = v.run_tool(self.tool("s2w_features"), [p, "--out", "logmel"])
            spec = None
            if feat.returncode == 0:
                mat = v.parse_matrix(feat.stdout, "logmel_raw")
                spec = {"rows": len(mat), "cols": len(mat[0]), "hop": 0.010, "data": [[round(x, 2) for x in r] for r in mat]}
                if wid in self.words:
                    nu = len(self.words[wid]["units"])
                    sug = suggest_edges(mat, nu)
                    if sug and sug[-1] > n / rate:
                        sug[-1] = round(n / rate, 4)
                    if self.aligner_url:
                        al = call_aligner(self.aligner_url, data, n / rate)
                        if al["ok"]:
                            sug, al["split"] = aligned_edges(mat, nu, al["start"], al["end"])
                    spec["suggest"] = sug
        return {"qa": qa, "spec": spec, "duration": n / rate, "rate": rate, "units": self.words[wid]["units"] if wid in self.words else None, "aligner": al}

    def accept(self, wid, data, segments=None):
        if wid not in self.words:
            raise v.ViewerError(404, "unknown word")
        rate, n = validate_wav(data)
        seg = validate_segments(segments, self.words[wid]["units"], n / rate) if segments is not None else None
        d = self.word_dir(wid)
        os.makedirs(d, exist_ok=True)
        existing = [int(TAKE_RE.match(x).group(1)) for x in os.listdir(d) if TAKE_RE.match(x)]
        k = max(existing, default=0) + 1
        name = "take_%03d.wav" % k
        with open(os.path.join(d, name), "xb") as f:      # exclusive create: never overwrites a take
            f.write(data)
        if seg is not None:
            with open(os.path.join(d, name[:-4] + ".segments.json"), "x", encoding="utf-8") as f:
                json.dump(dict(seg, note="sound boundaries: suggested from spectral change or placed by hand; estimates, not ground truth"), f, ensure_ascii=False, indent=1)
        qa = {}
        try:
            qa = self.analyse(data)["qa"]
        except v.ViewerError:
            pass
        new = not os.path.exists(os.path.join(self.dir, "takes.csv"))
        with open(os.path.join(self.dir, "takes.csv"), "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["word_id", "label", "manglish", "file", "sample_rate", "duration_s", "sha256", "saved_utc", "qa_verdict", "segments", "source", "note"])
            wd = self.words[wid]
            w.writerow([wid, wd["label"], wd["manglish"], "%s/%s" % (wid, name), rate, "%.3f" % (n / rate), hashlib.sha256(data).hexdigest(),
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), qa.get("verdict", ""), seg["method"] if seg else "none", "browser_microphone",
                        "PERSONAL TEST RECORDING - not experiment data"])
        return {"saved": name, "takes": len(existing) + 1}


def make_handler(rec):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, val in (extra or {}).items():
                self.send_header(k, val)
            self.end_headers()
            self.wfile.write(body)

        def js(self, obj, code=200):
            self.send(code, json.dumps(obj).encode(), "application/json")

        def host_ok(self):
            return (self.headers.get("Host") or "") in self.server.allowed_hosts

        def body(self):
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise v.ViewerError(411, "Content-Length required")
            if n > MAX_BYTES:
                raise v.ViewerError(413, "recording is too large")
            return self.rfile.read(n)

        def do_GET(self):
            try:
                if not self.host_ok():
                    return self.js({"error": "forbidden host"}, 403)
                p = unquote(urlparse(self.path).path)
                if p == "/":
                    return self.send(200, PAGE.replace("__STYLE__", STYLE).replace("__TOKEN__", rec.token).encode(), "text/html; charset=utf-8")
                if p == "/favicon.ico":
                    return self.send(204, b"", "image/x-icon")
                if p == "/compare":
                    return self.send(200, COMPARE.replace("__STYLE__", STYLE).replace("__TOKEN__", rec.token).encode(), "text/html; charset=utf-8")
                if p == "/api/words":
                    return self.js(rec.listing())
                if p == "/api/dict":
                    qs = parse_qs(urlparse(self.path).query)
                    num = lambda k, d: int((qs.get(k) or [d])[0]) if re.fullmatch(r"\d{1,7}", (qs.get(k) or [str(d)])[0]) else d
                    return self.js(rec.dict_search((qs.get("q") or [""])[0], num("limit", 40), num("offset", 0) if "browse" in qs else None))
                if p == "/api/compare":
                    q = parse_qs(urlparse(self.path).query)
                    return self.js(rec.compare((q.get("a") or [""])[0], (q.get("b") or [""])[0]))
                m = re.match(r"^/api/segments/([a-z]+)/(take_\d{3}\.wav)$", p)
                if m:
                    seg = rec.segments(m.group(1), m.group(2))
                    return self.js(seg) if seg is not None else self.js({"error": "no segments for this take"}, 404)
                m = re.match(r"^/audio/([a-z]+)/(take_\d{3}\.wav)$", p)
                if m:
                    path = rec.take_path(m.group(1), m.group(2))
                    if path is None:
                        return self.js({"error": "unknown take"}, 404)
                    with open(path, "rb") as f:
                        data = f.read()
                    r = v.parse_range(self.headers.get("Range"), len(data))
                    if r == "invalid":
                        return self.send(416, b"", "audio/wav", {"Content-Range": "bytes */%d" % len(data), "Accept-Ranges": "bytes"})
                    if r:
                        return self.send(206, data[r[0]:r[1] + 1], "audio/wav", {"Content-Range": "bytes %d-%d/%d" % (r[0], r[1], len(data)), "Accept-Ranges": "bytes"})
                    return self.send(200, data, "audio/wav", {"Accept-Ranges": "bytes"})
                return self.js({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                return
            except v.ViewerError as e:
                return self.js({"error": e.message}, e.status if e.status >= 400 else 422)
            except Exception as e:
                sys.stderr.write("recorder internal error on %s: %r\n" % (self.path, e))
                return self.js({"error": "internal recorder error (see the server log)"}, 500)

        def do_POST(self):
            try:
                if not self.host_ok():
                    return self.js({"error": "forbidden host"}, 403)
                if self.headers.get("X-S2W-Token") != rec.token:
                    return self.js({"error": "missing or wrong token (reload the page)"}, 403)
                u = urlparse(self.path)
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if u.path == "/api/words":
                    if ctype != "application/json":
                        return self.js({"error": "Content-Type must be application/json"}, 415)
                    try:
                        n = int(self.headers.get("Content-Length", ""))
                    except ValueError:
                        raise v.ViewerError(411, "Content-Length required")
                    if n > 4096:
                        raise v.ViewerError(413, "request too large")
                    try:
                        d = json.loads(self.rfile.read(n).decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise v.ViewerError(422, "request is not valid JSON")
                    if not isinstance(d, dict):
                        raise v.ViewerError(422, "request must be a JSON object")
                    units = d.get("units")
                    if units is not None and not (isinstance(units, list) and all(isinstance(x, str) for x in units)):
                        raise v.ViewerError(422, "units must be a list of strings")
                    w = rec.add_word(d.get("label"), d.get("manglish"), units, d.get("meaning", ""), d.get("source", ""), d.get("g2p") is True)
                    return self.js({"added": w["id"], "words": len(rec.words)})
                if ctype != "audio/wav":
                    return self.js({"error": "Content-Type must be audio/wav"}, 415)
                data = self.body()
                q = parse_qs(u.query)
                wid = (q.get("word") or [""])[0]
                if u.path == "/api/analyse":
                    return self.js(rec.analyse(data, wid))
                if u.path == "/api/accept":
                    raw = self.headers.get("X-S2W-Segments")
                    seg = None
                    if raw is not None:
                        if len(raw) > 4000:
                            raise v.ViewerError(422, "segment data too large")
                        try:
                            seg = json.loads(raw)
                        except ValueError:
                            raise v.ViewerError(422, "segment data is not valid JSON")
                    return self.js(rec.accept(wid, data, seg))
                return self.js({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                return
            except v.ViewerError as e:
                return self.js({"error": e.message}, e.status if e.status >= 400 else 422)
            except Exception as e:
                sys.stderr.write("recorder internal error on %s: %r\n" % (self.path, e))
                return self.js({"error": "internal recorder error (see the server log)"}, 500)

        def do_PUT(self):
            self.js({"error": "method not allowed"}, 405)
        do_DELETE = do_PATCH = do_PUT
    return H


def make_server(rec, port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(rec))
    p = srv.server_address[1]
    srv.allowed_hosts = {"127.0.0.1:%d" % p, "localhost:%d" % p}
    return srv


STYLE = r"""
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2230;--mut:#6b7385;--line:#e3e6ec;--blue:#4285d8;--red:#d93025;--green:#1e8e3e}
@media(prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--ink:#e8ebf1;--mut:#8f98aa;--line:#2b323f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}
header{padding:14px 20px;background:var(--card);border-bottom:1px solid var(--line)}h1{margin:0;font-size:18px}nav{margin-top:6px;font-size:14px}nav a{color:var(--blue);text-decoration:none}
.layout{display:grid;grid-template-columns:330px minmax(0,1fr);gap:16px;max-width:1440px;margin:0 auto;padding:16px 20px}.layout>main{padding:0;max-width:none;margin:0;min-width:0}@media(max-width:900px){.layout{grid-template-columns:1fr}}
.side input[type=text],.side input:not([type]){font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;width:100%;background:var(--card);color:var(--ink)}.side button{padding:5px 10px;font-size:13px}.tabs{display:flex;gap:6px;margin:8px 0}.tabs .tab{flex:1;border-radius:8px}.tabs .tab.on{border-color:var(--blue);background:var(--bg);font-weight:600}
#dres{max-height:56vh;overflow:auto;margin-top:8px;border:1px solid var(--line);border-radius:8px}.dr{padding:7px 10px;border-bottom:1px solid var(--line);border-left:4px solid transparent;cursor:pointer;display:grid;grid-template-columns:1fr auto;gap:0 8px}
.dr:hover{background:var(--bg)}.dr.active{border-left-color:var(--blue);background:var(--bg);box-shadow:inset 0 0 0 1px var(--blue)}.mlw{font-size:20px}.dg{color:var(--mut);font-size:13px;text-align:right;align-self:center}
.dmean{grid-column:1/-1;color:var(--mut);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.dt{color:var(--green);font-size:12px;grid-column:1/-1}.dt:empty{display:none}
.banner{margin-top:8px;padding:8px 12px;border-radius:8px;background:#fff3cd;color:#664d03;font-size:13px}
main{max-width:1000px;margin:0 auto;padding:16px 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.chips{display:flex;gap:8px;flex-wrap:wrap}.chip{padding:6px 14px;border:1px solid var(--line);border-radius:99px;cursor:pointer;background:var(--card);color:var(--ink)}.chip.sel{border-color:var(--blue);background:var(--bg);font-weight:600}
.word{text-align:center;padding:10px 0}.ml{font-size:72px;line-height:1.15}.rom{font-size:28px;color:var(--mut);letter-spacing:.08em;text-transform:uppercase}.units{font-size:18px;color:var(--mut);letter-spacing:.12em}
canvas{width:100%;display:block;border-radius:8px;background:var(--bg)}button{font:inherit;padding:9px 18px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);cursor:pointer}
button:hover:not(:disabled){border-color:var(--blue)}button:disabled{opacity:.45;cursor:default}select{font:inherit;padding:7px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);max-width:100%}
.rec{background:var(--red);color:#fff;border-color:var(--red);font-weight:600}.ok{background:var(--green);color:#fff;border-color:var(--green);font-weight:600}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}
#status{min-height:1.5em;font-weight:600}.bad{color:var(--red)}.good{color:var(--green)}.mut{color:var(--mut);font-size:13px}.tag{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;background:#e4e8fb;color:#2b3a8f;margin-right:6px}
.take{display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)}
.seg{display:grid;grid-template-columns:48px 1fr 56px;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)}.badge{min-width:44px;height:44px;border-radius:10px;color:#fff;font-weight:700;display:grid;place-items:center;padding:0 4px}
#wave,#segbar{touch-action:none;cursor:ew-resize;user-select:none}.nudge button{padding:0 7px;margin:0 2px;font-size:11px;border-radius:6px}table{border-collapse:collapse;width:100%}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}
.rel{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px}.same{background:#d6efe6;color:#0b5a3f}.len{background:#fde7c7;color:#7a4300}.diff{background:#fbd9d5;color:#8a1c12}.only{background:#e4e8fb;color:#2b3a8f}
.dbar{height:10px;border-radius:5px;background:#4285d8;min-width:2px}
"""

PAGE = r"""<!doctype html><html lang="ml"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sound2Word Voice Recorder</title><style>__STYLE__</style></head><body>
<header><h1>Sound2Word &mdash; Voice Recorder</h1><nav><a href="/">Record</a> &middot; <a href="/compare">Compare two words</a></nav>
<div class="banner"><b>PERSONAL TEST RECORDINGS</b> &mdash; for trying the screen and playback only. Not experiment data, not a verified vocabulary, and not evidence of recognition accuracy. Audio stays on this computer.</div></header>
<div class="layout"><aside class="side card" id="side"><h2 style="margin:0 0 6px;font-size:16px">Word search</h2>
 <div class="mut" id="dinfo">Search a dictionary by Malayalam, English or Manglish.</div>
 <div class="tabs"><button type="button" class="tab on" id="tdict">Dictionary</button><button type="button" class="tab" id="tmine">My words (<span id="nmine">0</span>)</button></div>
 <input type="text" id="dq" placeholder="Search: pot, kalam, കലം" autocomplete="off" aria-label="Search the word list"><div class="mut" id="dcount" role="status" style="margin-top:6px"></div><div id="dres" role="listbox" aria-label="Words"></div><div class="mut" id="dmsg" style="margin-top:6px"></div>
 <h3 style="margin:14px 0 4px;font-size:14px">Add a word by hand</h3>
 <input type="text" id="addml" placeholder="Malayalam, e.g. കലം" style="margin-bottom:6px"><input type="text" id="addrom" placeholder="Manglish, letters A-Z only, e.g. kalam" style="margin-bottom:6px">
 <button type="button" id="addgo">＋ Add to my words</button><div class="mut" id="addmsg" style="margin-top:6px"></div>
 <div class="mut" style="margin-top:10px">Dictionary words are only suggestions. The Manglish spelling is automatic: check it. Added words are listed as UNVERIFIED in <code>candidates_unverified.csv</code>; they are not part of the verified word list until native speakers review them.</div></aside>
<main>
<div class="card"><div class="chips" id="chips"></div>
 <div class="word"><div class="ml" id="ml"></div><div class="rom" id="rom"></div><div class="units" id="units"></div><div class="mut" id="meaning"></div></div>
 <div class="row" style="justify-content:center"><button id="rec" class="rec">&#9679; Record</button><button id="stop" disabled>&#9632; Stop</button><span id="timer" class="mut">0.0 s</span></div>
 <canvas id="live" height="130"></canvas><div id="status" role="status"></div></div>
<div class="card" id="review" hidden><h2 style="margin:0 0 4px;font-size:16px">Your recording</h2>
 <div class="mut" style="margin-bottom:8px">Drag the dark handles on the graph to where you hear each sound begin and end. The first positions are only a guess from spectral changes. Sounds overlap in real speech, so boundaries are approximate.</div>
 <div class="row" style="margin:0 0 6px"><button id="vz" type="button">Show whole recording</button><span class="mut" id="vinfo"></span></div>
 <canvas id="wave" height="150"></canvas><canvas id="segbar" height="36" style="margin-top:6px"></canvas><div class="mut" style="margin-top:4px">Combined word: <b id="comb"></b> &middot; you can also drag on the coloured bar, or use the &#9664; &#9654; buttons on each sound (Shift = 50 ms).</div>
 <h3 style="margin:14px 0 2px;font-size:15px">Each sound on its own</h3><div id="cards"></div>
 <canvas id="spec" height="120" style="margin-top:10px"></canvas>
 <div id="qa" class="mut" style="margin-top:8px"></div>
 <div class="row"><button id="play">&#9654; Play whole word</button><button id="accept" class="ok">&#10003; Accept</button><button id="discard">&#10007; Discard</button><span class="mut">Discard throws this recording away (nothing is saved) and you can record again.</span></div></div>
<div class="card"><h2 style="margin:0 0 6px;font-size:16px">Accepted takes for this word</h2><div id="takes" class="mut">None yet.</div></div>
</main></div>
<script>
const TOKEN='__TOKEN__',$=id=>document.getElementById(id),COL=['#4285d8','#e8a43c','#2d9d78','#9b59b6','#d35400','#16a085','#c0392b','#7f8c8d'];
const QS=new URLSearchParams(location.search);let urlWord=QS.get('word');
let words=[],cur=0,state='idle',ctx,stream,node,chunks=[],rate=0,start=0,live=[],raf=0,pcm=null,wav=null,rev=null,analysis=null,units=[],edges=[],method='suggested',stopAt=null,drag=-1,pausedMid=false,view=[0,1],zoomed=true;
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function say(t,cls){const s=$('status');s.textContent=t;s.className=cls||''}
async function api(u,opt){const r=await fetch(u,opt);const d=await r.json().catch(()=>({error:'bad response'}));if(!r.ok||d.error)throw new Error(d.error||('HTTP '+r.status));return d}
async function loadWords(){words=await api('/api/words');if(urlWord!==null){const k=words.findIndex(w=>w.id===urlWord);if(k>=0)cur=k;urlWord=null}if(cur>=words.length)cur=0;if(window.syncUrl)syncUrl();
 $('chips').innerHTML=words.map((w,i)=>`<span class="chip${i===cur?' sel':''}" data-i="${i}">${esc(w.label)} · ${esc(w.manglish)} (${w.takes.length})</span>`).join('');
 document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{if(state==='idle'){cur=+c.dataset.i;clearReview();loadWords()}});
 $('nmine').textContent=words.length;if(!words.length&&window.afterWords)afterWords();
 if(!words.length){$('ml').textContent='';$('rom').textContent='';$('units').textContent='';$('meaning').textContent='No words yet: search the dictionary on the left and press ＋ Add, or add a word by hand.';$('takes').textContent='None yet.';if(state==='idle')$('rec').disabled=true;return}
 if(state==='idle'&&$('rec').disabled&&!pcm)$('rec').disabled=false;
 if(window.afterWords)afterWords();
 const w=words[cur];$('ml').textContent=w.label;$('rom').textContent=w.manglish;$('units').textContent=w.units.map(u=>u.toUpperCase()).join(' · ');$('meaning').textContent=w.meaning?w.meaning+(w.source?' ('+w.source+', unverified)':''):'';
 $('takes').innerHTML=w.takes.length?w.takes.map(t=>`<div class="take"><button data-t="${t.name}">&#9654;</button><span>${t.name}</span><span class="mut">${t.duration==null?'':t.duration.toFixed(2)+' s'} · ${t.segments?'sounds marked':'no sound boundaries'}</span></div>`).join(''):'None yet.';
 document.querySelectorAll('[data-t]').forEach(b=>b.onclick=()=>new Audio('/audio/'+w.id+'/'+b.dataset.t).play())}
function sized(c){const r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.width=r.width*d;c.height=(+c.getAttribute('height'))*d;const g=c.getContext('2d');g.scale(d,d);return[g,r.width,+c.getAttribute('height')]}
function drawLive(){const[g,W,H]=sized($('live'));g.clearRect(0,0,W,H);g.strokeStyle='#4285d8';const N=Math.floor(W/2),sl=live.slice(-N);
 sl.forEach(([lo,hi],i)=>{g.beginPath();g.moveTo(i*2,H/2-hi*H/2);g.lineTo(i*2,H/2-lo*H/2);g.stroke()});
 const peak=sl.length?Math.max(...sl.slice(-10).map(([a,b])=>Math.max(-a,b))):0;g.fillStyle=peak>.98?'#d93025':'#1e8e3e';g.fillRect(0,H-5,peak*W,5);
 $('timer').textContent=((performance.now()-start)/1000).toFixed(1)+' s'}
function tick(){if(state==='recording'){drawLive();raf=requestAnimationFrame(tick)}}
async function startRec(){if(state!=='idle')return;clearReview();
 if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){say('This browser cannot record here. Open the page at http://127.0.0.1:PORT (not a network address).','bad');return}
 try{stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false}})}catch(e){say('Microphone unavailable: '+e.message+' (allow microphone access for this page)','bad');return}
 ctx=new AudioContext();await ctx.resume();rate=ctx.sampleRate;chunks=[];live=[];
 const code="class R extends AudioWorkletProcessor{process(i){const c=i[0]&&i[0][0];if(c)this.port.postMessage(c.slice(0));return true}}registerProcessor('s2w-rec',R)";
 const url=URL.createObjectURL(new Blob([code],{type:'application/javascript'}));await ctx.audioWorklet.addModule(url);URL.revokeObjectURL(url);
 node=new AudioWorkletNode(ctx,'s2w-rec');const mute=ctx.createGain();mute.gain.value=0;
 let acc=[];const B=Math.round(rate*0.01);
 node.port.onmessage=e=>{const c=e.data;chunks.push(c);for(let i=0;i<c.length;i++){acc.push(c[i]);if(acc.length>=B){let lo=1,hi=-1;for(const y of acc){if(y<lo)lo=y;if(y>hi)hi=y}live.push([lo,hi]);acc=[]}}if(chunks.length*128/rate>20)stopRec()};
 ctx.createMediaStreamSource(stream).connect(node);node.connect(mute).connect(ctx.destination);
 state='recording';start=performance.now();$('rec').disabled=true;$('stop').disabled=false;say('Recording… say the word naturally, then press Stop.');tick()}
function toWav(f32,sr){const n=f32.length,b=new ArrayBuffer(44+2*n),v=new DataView(b),w=(o,s)=>{for(let i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i))};
 w(0,'RIFF');v.setUint32(4,36+2*n,true);w(8,'WAVEfmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,sr,true);v.setUint32(28,sr*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);w(36,'data');v.setUint32(40,2*n,true);
 for(let i=0;i<n;i++){const s=Math.max(-1,Math.min(1,f32[i]));v.setInt16(44+2*i,s<0?s*32768:s*32767,true)}return new Blob([b],{type:'audio/wav'})}
async function stopRec(){if(state!=='recording')return;state='analysing';cancelAnimationFrame(raf);
 node.disconnect();stream.getTracks().forEach(t=>t.stop());const sr=ctx.sampleRate;await ctx.close();
 let n=0;for(const c of chunks)n+=c.length;pcm=new Float32Array(n);let o=0;for(const c of chunks){pcm.set(c,o);o+=c.length}
 $('rec').disabled=true;$('stop').disabled=true;
 if(n/sr<0.3){say('Too short — nothing was recorded. Press Record and try again.','bad');state='idle';$('rec').disabled=false;return}
 wav=toWav(pcm,sr);rate=sr;say('Analysing…');
 try{analysis=await api('/api/analyse?word='+encodeURIComponent(words[cur].id),{method:'POST',headers:{'X-S2W-Token':TOKEN,'Content-Type':'audio/wav'},body:wav})}catch(e){analysis=null;say('Could not analyse this recording: '+e.message+' (you can still mark the sounds by hand, play, accept or discard it)','bad')}
 state='review';showReview();if(analysis)say('Listen to it, check the sound boundaries, then Accept or Discard.')}
const dur=()=>pcm.length/rate;
function fixEdges(){const d=dur(),N=units.length;let ok=edges.length===N+1;for(let k=1;ok&&k<=N;k++){if(!(edges[k]-edges[k-1]>=0.012))edges[k]=edges[k-1]+0.012}
 if(!ok||edges[N]>d||edges[0]<0){edges=units.map((_,k)=>k*d/N).concat([d]);method='default-split'}}
function initSegments(){units=words[cur].units;const sug=analysis&&analysis.spec&&analysis.spec.suggest;
 if(sug&&sug.length===units.length+1){edges=sug.slice();method=analysis.aligner&&analysis.aligner.ok?'model-aligned':'suggested'}else{edges=[];method='default-split'}fixEdges();zoomed=true;setView()}
function setView(){const d=dur(),N=units.length;view=zoomed?[Math.max(0,edges[0]-0.15),Math.min(d,edges[N]+0.15)]:[0,d];if(view[1]-view[0]<0.2)view=[0,d];$('vz').textContent=zoomed?'Show whole recording':'Zoom to the word';$('vinfo').textContent=zoomed?'zoomed to the word':'whole recording';$('comb').textContent=units.join('').toUpperCase()}
function ensureVisible(){if(zoomed){const N=units.length;view=[Math.min(view[0],Math.max(0,edges[0]-0.05)),Math.max(view[1],Math.min(dur(),edges[N]+0.05))]}}
function peaksOf(a,b,W){const out=[],n=pcm.length,s0=Math.floor(a*rate),s1=Math.floor(b*rate);for(let x=0;x<W;x++){let lo=1,hi=-1;const i0=s0+Math.floor(x*(s1-s0)/W),i1=Math.max(i0+1,s0+Math.floor((x+1)*(s1-s0)/W));for(let i=Math.max(0,i0);i<i1&&i<n;i++){const y=pcm[i];if(y<lo)lo=y;if(y>hi)hi=y}out.push([lo,hi])}return out}
function showReview(){$('review').hidden=false;rev=new Audio(URL.createObjectURL(wav));rev.onended=()=>{$('play').textContent='▶ Play whole word';stopAt=null;pausedMid=false;drawReview()};initSegments();
 if(analysis){const q=analysis.qa;$('qa').innerHTML=`<span class="tag">measured</span>duration ${analysis.duration.toFixed(2)} s · ${analysis.rate} Hz · SNR ${q.snr_db_estimate} dB · level ${q.speech_level_dbfs} dBFS · clipping ${q.clipped_run_samples} · QA verdict <b>${esc(q.verdict)}</b> (${esc(q.flags)})`;const al=analysis.aligner;if(al)$('qa').innerHTML+='<br>'+(al.ok?`<span class="tag">speech model</span>${esc(al.model)} places the word at ${al.start.toFixed(2)}–${al.end.toFixed(2)} s and heard “${esc(al.text)}”; the split inside the word is spectral ('${esc(al.split)}')`:`<span class="tag">speech model unavailable</span>${esc(al.error)} (using the spectral first guess)`)}else $('qa').textContent='';
 renderCards();drawReview();$('play').disabled=false;$('accept').disabled=false;$('discard').disabled=false}
function tagText(){return method==='edited'?'placed by you':method==='model-aligned'?'word span from the speech model':method==='suggested'?'first guess':'even split (no guess available)'}
function renderCards(){$('cards').innerHTML=units.map((u,k)=>`<div class="seg"><div class="badge" style="background:${COL[k%8]}">${esc(u.toUpperCase())}</div><div><b>Sound ${k+1}: ${esc(u.toUpperCase())}</b> <span class="tag">${tagText()}</span><br><span class="mut">${edges[k].toFixed(3)} – ${edges[k+1].toFixed(3)} s · ${Math.round((edges[k+1]-edges[k])*1000)} ms</span>
 <span class="nudge mut"> start <button type="button" data-n="${k}:-1">&#9664;</button><button type="button" data-n="${k}:1">&#9654;</button> end <button type="button" data-n="${k+1}:-1">&#9664;</button><button type="button" data-n="${k+1}:1">&#9654;</button></span><canvas class="mini" data-k="${k}" height="40"></canvas></div><button data-u="${k}" title="play this sound">&#9654;</button></div>`).join('');
 document.querySelectorAll('[data-n]').forEach(b=>b.onclick=e=>{const[i,dir]=b.dataset.n.split(':').map(Number),N=units.length,step=(e.shiftKey?0.05:0.01)*dir,lo=i>0?edges[i-1]+0.012:0,hi=i<N?edges[i+1]-0.012:dur();edges[i]=Math.max(lo,Math.min(hi,edges[i]+step));method='edited';ensureVisible();renderCards();drawReview()});
 document.querySelectorAll('[data-u]').forEach(b=>b.onclick=()=>playRegion(edges[+b.dataset.u],edges[+b.dataset.u+1]));
 document.querySelectorAll('canvas.mini').forEach(c=>{const k=+c.dataset.k,[g,W,H]=sized(c);g.strokeStyle=COL[k%8];peaksOf(edges[k],edges[k+1],Math.floor(W)).forEach(([lo,hi],x)=>{g.beginPath();g.moveTo(x,H/2-hi*H/2);g.lineTo(x,H/2-lo*H/2);g.stroke()})})}
function drawReview(){if(!pcm)return;const[v0,v1]=view;let[g,W,H]=sized($('wave')),X=t=>(t-v0)/(v1-v0)*W;
 units.forEach((u,k)=>{g.fillStyle=COL[k%8]+'33';g.fillRect(X(edges[k]),0,X(edges[k+1])-X(edges[k]),H)});
 g.strokeStyle='#4285d8';peaksOf(v0,v1,Math.floor(W)).forEach(([lo,hi],x)=>{g.beginPath();g.moveTo(x,H/2-hi*H/2);g.lineTo(x,H/2-lo*H/2);g.stroke()});
 edges.forEach((t,k)=>{const outer=k===0||k===units.length;g.fillStyle=outer?'#8f98aa':'#1c2230';g.fillRect(X(t)-1,0,2,H);g.fillRect(X(t)-8,0,16,20);g.fillStyle='#fff';g.font='bold 11px sans-serif';g.fillText('◂▸',X(t)-7,14)});
 if(rev&&rev.duration&&(!rev.paused||pausedMid)){g.strokeStyle='#d93025';g.lineWidth=2;const x=X(rev.currentTime);g.beginPath();g.moveTo(x,0);g.lineTo(x,H);g.stroke()}
 [g,W,H]=sized($('segbar'));units.forEach((u,k)=>{const x0=X(edges[k]),x1=X(edges[k+1]);g.fillStyle=COL[k%8];g.fillRect(x0,0,Math.max(1,x1-x0-1),H);if(x1-x0>16){g.fillStyle='#fff';g.font='bold 13px sans-serif';g.fillText(u.toUpperCase(),x0+5,22)}});
 edges.forEach(t=>{g.fillStyle='#1c2230';g.fillRect(X(t)-1,0,2,H)});
 [g,W,H]=sized($('spec'));const sp=analysis&&analysis.spec;if(!sp){g.fillStyle='#8f98aa';g.fillText('spectrogram unavailable',8,16);return}
 const lo=Math.min(...sp.data.map(r=>Math.min(...r))),hi=Math.max(...sp.data.map(r=>Math.max(...r)));
 for(let x=0;x<W;x++){const t=v0+x/W*(v1-v0),f=Math.min(sp.rows-1,Math.max(0,Math.floor(t/sp.hop)));for(let m=0;m<sp.cols;m++){const q=(sp.data[f][m]-lo)/(hi-lo||1);g.fillStyle=`hsl(${240-240*q},80%,${15+45*q}%)`;g.fillRect(x,H-(m+1)*H/sp.cols,1.5,H/sp.cols+1)}}
 edges.forEach(t=>{g.fillStyle='#ffffffaa';g.fillRect(X(t),0,1,H)})}
function tAt(e,c){const r=c.getBoundingClientRect();return Math.max(view[0],Math.min(view[1],view[0]+(e.clientX-r.left)/r.width*(view[1]-view[0])))}
function bindDrag(c){
 c.addEventListener('pointerdown',e=>{if(!pcm)return;e.preventDefault();const t=tAt(e,c),px=c.getBoundingClientRect().width/(view[1]-view[0]);let best=-1,bd=1e9;
  edges.forEach((x,k)=>{const dd=Math.abs(x-t)*px;if(dd<bd||(dd===bd&&k>best&&t>x)){bd=dd;best=k}});
  if(bd<=20){drag=best;c.setPointerCapture(e.pointerId)}});
 c.addEventListener('pointermove',e=>{if(drag<0)return;e.preventDefault();const t=tAt(e,c),N=units.length,lo=drag>0?edges[drag-1]+0.012:0,hi=drag<N?edges[drag+1]-0.012:dur();edges[drag]=Math.max(lo,Math.min(hi,t));method='edited';renderCards();drawReview()});
 c.addEventListener('pointerup',()=>{drag=-1});c.addEventListener('pointercancel',()=>{drag=-1})}
bindDrag($('wave'));bindDrag($('segbar'));
$('vz').onclick=()=>{zoomed=!zoomed;setView();drawReview()};
function playRegion(a,b){if(!rev)return;pausedMid=false;rev.currentTime=a;stopAt=b;rev.play();$('play').textContent='❚❚ Pause';rafPlay()}
function rafPlay(){drawReview();if(rev&&!rev.paused){if(stopAt!=null&&rev.currentTime>=stopAt){rev.pause();stopAt=null;pausedMid=false;$('play').textContent='▶ Play whole word';drawReview();return}requestAnimationFrame(rafPlay)}}
function clearReview(){if(rev){rev.pause();rev=null}$('review').hidden=true;pcm=null;wav=null;analysis=null;stopAt=null;pausedMid=false;$('play').textContent='▶ Play whole word'}
function discard(){clearReview();state='idle';$('rec').disabled=false;$('stop').disabled=true;$('timer').textContent='0.0 s';say('Discarded — nothing was saved. Ready for a new recording.')}
async function accept(){if(state!=='review')return;$('accept').disabled=true;
 try{const seg=JSON.stringify({units,edges:edges.map(x=>+x.toFixed(4)),method});
  const r=await api('/api/accept?word='+encodeURIComponent(words[cur].id),{method:'POST',headers:{'X-S2W-Token':TOKEN,'X-S2W-Segments':seg,'Content-Type':'audio/wav'},body:wav});
  clearReview();state='idle';$('rec').disabled=false;$('stop').disabled=true;await loadWords();say('Saved '+r.saved+' with its sound boundaries. Record another, pick the next word, or open Compare.','good')}
 catch(e){$('accept').disabled=false;say('Could not save: '+e.message,'bad')}}
$('rec').onclick=startRec;$('stop').onclick=stopRec;$('discard').onclick=discard;$('accept').onclick=accept;
$('play').onclick=()=>{if(!rev)return;if(rev.paused){if(!pausedMid)rev.currentTime=0;pausedMid=false;stopAt=null;rev.play();$('play').textContent='❚❚ Pause';rafPlay()}else{rev.pause();pausedMid=stopAt==null;stopAt=null;$('play').textContent=pausedMid?'▶ Resume':'▶ Play whole word'}};
addEventListener('resize',()=>{if(state==='review'){renderCards();drawReview()}});
async function addWord(body,msgEl){if(state!=='idle'){msgEl.textContent='Finish or discard the current recording first.';msgEl.className='bad';return false}
 try{const r=await api('/api/words',{method:'POST',headers:{'X-S2W-Token':TOKEN,'Content-Type':'application/json'},body:JSON.stringify(body)});
  cur=0;await loadWords();cur=words.findIndex(w=>w.id===r.added);if(cur<0)cur=0;await loadWords();msgEl.textContent='Added. It is selected: press Record.';msgEl.className='good';return true}
 catch(e){msgEl.textContent=e.message;msgEl.className='bad';return false}}
$('addgo').onclick=async()=>{if(await addWord({label:$('addml').value,manglish:$('addrom').value,source:'typed by hand'},$('addmsg'))){$('addml').value='';$('addrom').value=''}};
let dtimer=null,dseq=0,dmode=QS.get('tab')==='mine'?'mine':'dict',dq=(QS.get('q')||'').slice(0,60),doff=0,dmore=false,dbusy=false,dname='',dok=true,ditems=[];const DLIM=100;
$('dq').value=dq;$('tdict').classList.toggle('on',dmode==='dict');$('tmine').classList.toggle('on',dmode==='mine');
window.syncUrl=()=>{try{const u=new URL(location.href);if(words[cur])u.searchParams.set('word',words[cur].id);else u.searchParams.delete('word');
 if(dq)u.searchParams.set('q',dq);else u.searchParams.delete('q');if(dmode==='mine')u.searchParams.set('tab','mine');else u.searchParams.delete('tab');history.replaceState(null,'',u)}catch(e){}};
const setCount=t=>{$('dcount').textContent=t};
function rowHtml(r,i){return `<div class="dr" role="option" tabindex="0" data-i="${i}" data-ml="${esc(r.ml)}"><span class="mlw">${esc(r.ml)}</span><span class="dg">${esc(r.manglish||'')}</span><span class="dt"></span><span class="dmean">${esc(r.meaning||'')}</span></div>`}
function markActive(){const a=words[cur]?words[cur].label:null,mine=new Map(words.map(w=>[w.label,w.takes.length]));let act=null;
 document.querySelectorAll('#dres .dr').forEach(e=>{const m=e.dataset.ml,on=m===a;if(on)act=e;e.classList.toggle('active',on);e.classList.toggle('mine',mine.has(m));e.setAttribute('aria-selected',on?'true':'false');
  const t=e.querySelector('.dt'),n=mine.get(m);t.textContent=mine.has(m)?'✓ in my words · '+n+' take'+(n===1?'':'s'):''});
 if(act){const el=$('dres'),top=act.offsetTop-el.offsetTop;if(top<el.scrollTop||top+act.offsetHeight>el.scrollTop+el.clientHeight)el.scrollTop=Math.max(0,top-el.clientHeight/2)}}
window.afterWords=()=>{if(dmode==='mine')dload(true);else markActive()};
function dmsg(t,cls){$('dmsg').textContent=t||'';$('dmsg').className='mut '+(cls||'')}
async function pick(i){const r=ditems[i];if(!r)return;
 if(state!=='idle'){dmsg('Finish or discard the current recording first.','bad');return}
 const k=words.findIndex(w=>w.label===r.ml);
 if(k>=0){cur=k;clearReview();dmsg('');await loadWords();return}
 if(!r.can_add){dmsg('That entry is a phrase: only single words can be recorded.','bad');return}
 clearReview();
 await addWord(Object.assign({label:r.ml,manglish:r.manglish,meaning:r.meaning,source:'dictionary: '+dname+(r.g2p?' + mlphon pronunciation':'')},r.g2p?{units:r.units,g2p:true}:{}),$('dmsg'))}
async function dload(reset){
 if(dmode==='mine'){const q=dq.toLowerCase();
  ditems=words.map(w=>({ml:w.label,manglish:w.manglish,meaning:w.meaning||'',can_add:true})).filter(r=>!q||r.ml.includes(dq)||r.manglish.includes(q)||r.meaning.toLowerCase().includes(q));
  $('dres').innerHTML=ditems.length?ditems.map(rowHtml).join(''):'<div class="mut" style="padding:10px">'+(words.length?'No match.':'No words yet. Pick one from the Dictionary tab.')+'</div>';
  $('dinfo').textContent='Words you have added to record';
  setCount(!words.length?'No words in your list yet.':ditems.length?(dq?ditems.length+' of '+words.length+' words match “'+dq+'”':words.length+' word'+(words.length===1?'':'s')+' in your list'):'No words match “'+dq+'”.');
  markActive();return}
 if(!dok)return;
 if(dbusy&&!reset)return;
 const my=reset?++dseq:dseq;dbusy=true;
 try{const d=await api(dq?'/api/dict?q='+encodeURIComponent(dq)+'&limit=100':'/api/dict?browse=1&offset='+(reset?0:doff)+'&limit='+DLIM);
  if(my!==dseq)return;
  if(!d.available){dok=false;$('dinfo').textContent=d.note;$('tdict').disabled=true;dmode='mine';$('tdict').classList.remove('on');$('tmine').classList.add('on');dload(true);return}
  dname=d.name;
  if(reset){ditems=[];doff=0;$('dres').innerHTML='';$('dres').scrollTop=0}
  const base=ditems.length;ditems=ditems.concat(d.results);doff+=d.results.length;dmore=!dq&&d.results.length===DLIM;
  $('dinfo').textContent='Dictionary: '+d.name+' ('+d.count.toLocaleString()+' entries), listed A–Z';
  const n=ditems.length;setCount(!n?(dq?'No words match “'+dq+'”.':'No words in the dictionary.'):dq?(n>=100?'Showing the best 100 matches: type more letters to narrow it':n+' match'+(n===1?'':'es')):'Showing '+n.toLocaleString()+' of '+d.count.toLocaleString()+(dmore?' · scroll for more':''));
  if(!n)$('dres').innerHTML='';
  else $('dres').insertAdjacentHTML('beforeend',d.results.map((r,i)=>rowHtml(r,base+i)).join(''));
  markActive()}
 catch(e){if(my===dseq)$('dres').textContent=e.message}
 finally{if(my===dseq)dbusy=false}}
function setMode(m){if(m==='dict'&&!dok)return;dmode=m;syncUrl();$('tdict').classList.toggle('on',m==='dict');$('tmine').classList.toggle('on',m==='mine');dload(true)}
$('tdict').onclick=()=>setMode('dict');$('tmine').onclick=()=>setMode('mine');
$('dq').oninput=()=>{clearTimeout(dtimer);dtimer=setTimeout(()=>{dq=$('dq').value.trim();syncUrl();dload(true)},180)};
$('dres').onclick=e=>{const r=e.target.closest('.dr');if(r)pick(+r.dataset.i)};
$('dres').onkeydown=e=>{const r=e.target.closest&&e.target.closest('.dr');if(!r)return;
 if(e.key==='Enter'||e.key===' '){e.preventDefault();pick(+r.dataset.i)}else if(e.key==='ArrowDown'&&r.nextElementSibling){e.preventDefault();r.nextElementSibling.focus()}else if(e.key==='ArrowUp'&&r.previousElementSibling){e.preventDefault();r.previousElementSibling.focus()}};
$('dres').onscroll=()=>{const el=$('dres');if(dmode==='dict'&&!dq&&dmore&&!dbusy&&el.scrollTop+el.clientHeight>el.scrollHeight-240)dload(false)};
loadWords().then(()=>dload(true)).catch(e=>say('Cannot load words: '+e.message,'bad'));
</script></body></html>"""

COMPARE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sound2Word Compare Words</title><style>__STYLE__</style></head><body>
<header><h1>Sound2Word &mdash; Compare two words, sound by sound</h1><nav><a href="/">Record</a> &middot; <a href="/compare">Compare two words</a></nav>
<div class="banner"><b>PERSONAL TEST RECORDINGS</b> &mdash; boundaries are estimates or placed by hand; differences shown are uncalibrated measurements, not recognition accuracy.</div></header>
<main>
<div class="card"><div class="row"><label>Word A <select id="a"></select></label><label>Word B <select id="b"></select></label><button id="go" class="ok">Compare</button></div><div id="msg" class="mut">Only takes accepted with sound boundaries can be compared.</div></div>
<div id="out"></div></main>
<script>
const $=id=>document.getElementById(id),COL=['#4285d8','#e8a43c','#2d9d78','#9b59b6','#d35400','#16a085','#c0392b','#7f8c8d'];
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let cur=null,timer=null,R=null;
async function api(u){const r=await fetch(u);const d=await r.json().catch(()=>({error:'bad response'}));if(!r.ok||d.error)throw new Error(d.error||('HTTP '+r.status));return d}
async function init(){const ws=await api('/api/words'),opts=[];ws.forEach(w=>w.takes.filter(t=>t.segments).forEach(t=>opts.push(`<option value="${w.id}/${t.name}">${esc(w.label)} · ${esc(w.manglish)} / ${t.name} (${t.duration.toFixed(2)} s)</option>`)));
 $('a').innerHTML=$('b').innerHTML=opts.join('');if(opts.length>1)$('b').selectedIndex=1;
 if(!opts.length)$('msg').textContent='No takes with sound boundaries yet. Record and Accept some first.';
 const q=new URLSearchParams(location.search);if(q.get('a')){$('a').value=q.get('a');$('b').value=q.get('b');compare()}}
function sized(c){const r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.width=r.width*d;c.height=(+c.getAttribute('height'))*d;const g=c.getContext('2d');g.scale(d,d);return[g,r.width,+c.getAttribute('height')]}
function playSeg(ref,a,b){if(cur){cur.pause();clearInterval(timer)}cur=new Audio('/audio/'+ref);const au=cur;au.addEventListener('loadedmetadata',()=>{au.currentTime=a;au.play();timer=setInterval(()=>{if(au.currentTime>=b||au.paused){au.pause();clearInterval(timer)}},15)})}
function drawSide(c,S,mx,colOf){const[g,W,H]=sized(c),n=S.peaks.length,X=t=>t/mx*W;g.strokeStyle='#4285d8';
 for(let x=0;x<W;x++){const t=x/W*mx;if(t>S.duration)break;const[lo,hi]=S.peaks[Math.min(n-1,Math.floor(t/S.duration*n))];g.beginPath();g.moveTo(x,H*0.4-hi*H*0.4);g.lineTo(x,H*0.4-lo*H*0.4);g.stroke()}
 S.units.forEach((u,k)=>{const x0=X(S.edges[k]),x1=X(S.edges[k+1]),col=COL[colOf[k]%8];g.fillStyle=col+'2a';g.fillRect(x0,0,x1-x0,H*0.8);g.fillStyle=col;g.fillRect(x0,H*0.8,Math.max(1,x1-x0-1),H*0.2);g.fillStyle='#fff';g.font='bold 11px sans-serif';g.fillText(u.toUpperCase(),x0+3,H*0.8+H*0.15)})}
function chip(r){return r==='identical'?'<span class="rel same">same unit</span>':r==='length'?'<span class="rel len">same unit, different length</span>':r==='different'?'<span class="rel diff">different units</span>':r==='only_a'?'<span class="rel only">only in A</span>':'<span class="rel only">only in B</span>'}
async function compare(){$('msg').textContent='Comparing…';$('out').innerHTML='';
 try{R=await api('/api/compare?a='+encodeURIComponent($('a').value)+'&b='+encodeURIComponent($('b').value))}catch(e){$('msg').textContent='Cannot compare: '+e.message;$('msg').className='bad';return}
 $('msg').textContent='';$('msg').className='mut';const A=R.a,B=R.b,mx=Math.max(A.duration,B.duration),rows=R.rows;
 const colA=[],colB=[];rows.forEach((r,i)=>{if(r.ia!=null)colA[r.ia]=i;if(r.ib!=null)colB[r.ib]=i});
 const maxS=Math.max(1e-9,...rows.filter(r=>r.spectral!=null).map(r=>r.spectral));
 const notes=rows.map(r=>{if(r.relation==='length'&&r.ratio)return `<b>${r.ua.toUpperCase()}</b> vs <b>${r.ub.toUpperCase()}</b>: same unit, length differs: A is ${r.ratio>=1?r.ratio.toFixed(2)+'× longer':(1/r.ratio).toFixed(2)+'× shorter'} than B.`;
  if(r.relation==='different')return `<b>${(r.ua||'').toUpperCase()}</b> vs <b>${(r.ub||'').toUpperCase()}</b>: different units.`;if(r.relation==='only_a')return `<b>${r.ua.toUpperCase()}</b> exists only in A.`;if(r.relation==='only_b')return `<b>${r.ub.toUpperCase()}</b> exists only in B.`;
  if(r.ratio&&(r.ratio>=1.4||r.ratio<=1/1.4))return `<b>${r.ua.toUpperCase()}</b>: same unit but A is ${r.ratio>=1?r.ratio.toFixed(2)+'× longer':(1/r.ratio).toFixed(2)+'× shorter'}.`;return null}).filter(Boolean);
 const whole=R.whole.distance!=null?`Whole-word distance from the matcher: <b>${R.whole.distance}</b> <span class="mut">(uncalibrated; smaller = more alike)</span>`:`<span class="mut">${esc(R.whole.note)}</span>`;
 $('out').innerHTML=`<div class="card"><h2 style="margin:0 0 6px;font-size:16px">A: ${esc(A.label)} · ${esc(A.manglish)} &nbsp; B: ${esc(B.label)} · ${esc(B.manglish)}</h2>
  <div class="mut">Both words are drawn on the same time scale, so a longer sound looks longer. Matching sounds share a colour. <b>Relation</b> comes from the spelling&rsquo;s sound units (K, A, L&hellip;); the <b>spectral difference</b> bar shows how alike the two recordings of that unit really are.</div>
  <div class="row"><b>A</b><button id="pa">&#9654; ${esc(A.manglish)}</button></div><canvas id="ca" height="110"></canvas>
  <div class="row"><b>B</b><button id="pb">&#9654; ${esc(B.manglish)}</button></div><canvas id="cb" height="110"></canvas>
  <p>${whole}</p></div>
  <div class="card"><h2 style="margin:0 0 6px;font-size:16px">What is the same and what differs</h2>${notes.length?'<ul>'+notes.map(n=>'<li>'+n+'</li>').join('')+'</ul>':'<p>No clear differences found.</p>'}
  <table><tr><th>Sound in A</th><th>Sound in B</th><th>Relation</th><th>Length A</th><th>Length B</th><th>A ÷ B</th><th>Spectral difference</th><th></th></tr>
  ${rows.map((r,i)=>`<tr><td><b style="color:${COL[i%8]}">${esc((r.ua||'—').toUpperCase())}</b></td><td><b style="color:${COL[i%8]}">${esc((r.ub||'—').toUpperCase())}</b></td><td>${chip(r.relation)}</td>
   <td>${r.dur_a!=null?Math.round(r.dur_a*1000)+' ms':''}</td><td>${r.dur_b!=null?Math.round(r.dur_b*1000)+' ms':''}</td><td>${r.ratio!=null?'×'+r.ratio.toFixed(2):''}</td>
   <td>${r.spectral!=null?`<div class="dbar" style="width:${Math.max(2,r.spectral/maxS*100)}%"></div><span class="mut">${r.spectral.toFixed(2)}</span>`:''}</td>
   <td>${r.ia!=null?`<button data-s="a" data-i="${r.ia}">▶A</button>`:''} ${r.ib!=null?`<button data-s="b" data-i="${r.ib}">▶B</button>`:''}</td></tr>`).join('')}</table>
  <p class="mut">Boundaries are estimates or placed by hand, and neighbouring sounds overlap in real speech. Spectral difference is the average (un-normalised) MFCC difference inside each sound (uncalibrated): compare rows of this table with each other, not across different comparisons. This says nothing about recognition accuracy.</p></div>`;
 drawSide($('ca'),A,mx,colA);drawSide($('cb'),B,mx,colB);
 $('pa').onclick=()=>playSeg(A.ref,0,A.duration);$('pb').onclick=()=>playSeg(B.ref,0,B.duration);
 document.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{const S=b.dataset.s==='a'?A:B,i=+b.dataset.i;playSeg(S.ref,S.edges[i],S.edges[i+1])})}
$('go').onclick=compare;init().catch(e=>{$('msg').textContent='Cannot load: '+e.message});
addEventListener('resize',()=>{if(R)compare()});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record-dir", required=True, help="where accepted takes are saved (use a git-ignored folder)")
    ap.add_argument("--word", action="append", default=[], help="'MALAYALAM|manglish' (repeatable); Manglish is ASCII letters only")
    ap.add_argument("--dictionary", help="optional dictionary CSV/TSV for the search sidebar (read-only), e.g. an Olam open dataset")
    ap.add_argument("--dict-ml-col", help="dictionary column (name or 0-based index) holding the Malayalam words")
    ap.add_argument("--dict-en-col", help="dictionary column holding the English meaning")
    ap.add_argument("--dict-no-header", action="store_true", help="the dictionary file has no header row")
    ap.add_argument("--aligner-url", help="optional local aligner service (tools/s2w_aligner_service.py), e.g. http://host.docker.internal:8780: "
                    "a speech model places the word start/end for the first guess")
    ap.add_argument("--build", default=os.environ.get("S2W_BUILD", "build"))
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    try:
        dic = Dictionary(a.dictionary, a.dict_ml_col, a.dict_en_col, a.dict_no_header) if a.dictionary else None
        rec = Recorder(a.record_dir, [parse_word(w) for w in a.word], a.build, dic, a.aligner_url)
        srv = make_server(rec, a.port)
    except v.ViewerError as e:
        print("error: %s" % e.message, file=sys.stderr)
        return 2
    except OSError as e:
        print("error: %s" % (e.strerror or e), file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print("Sound2Word Voice Recorder at http://127.0.0.1:%d  - takes are saved in %s - Ctrl+C to stop" % (srv.server_address[1], os.path.abspath(a.record_dir)), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
