#!/usr/bin/env python3
"""Search the dictionary by VOICE. Two methods, reported side by side so neither is trusted blindly:

  composed  build every candidate word out of the alphabet's unit clips (whole written syllables and conjuncts: ങ്ങ is ONE unit), join them with
            tools/s2w_joiner.py, and rank the candidates by DTW distance between their MFCCs and the spoken word's.
  labels    take the text a recogniser heard (any recogniser: indic-core, Whisper ...), cut it into the same units, and rank the dictionary
            by a weighted edit distance in which look-alike letters (ശ ഷ സ, ന ണ, ര റ, ല ള ഴ ...) cost less than unrelated ones.
  fused     reciprocal-rank fusion of the two.
  rescored  composed DTW restricted to the label method's top candidates (recogniser first, templates rescore - Sun & Zhao 2014).
DEFAULT = the plain matcher. Optional (see eval --compare-configs): duration rule on isolated units (cut or feature squeeze) and join smoothing,
after Kahn, Rabiner & Rosenberg 1984 - unproven on real speech. Clips cut out of words are preferred over isolated clips when present.

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
import wave

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


def speaker_key(x):
    """Canonical form of a participant ID: NFC, trimmed, case-folded. 'P1', ' p1 ' and 'p1' are one person; '' means no speaker."""
    return unicodedata.normalize("NFC", str(x if x is not None else "")).strip().casefold()


def file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Bank(dict):
    """{unit text: clip path} plus the provenance of EVERY reference clip listed in the metadata, whether or not it ends up used:
    `.in_word` units cut out of real words, `.speakers` canonical IDs of the people who supplied clips, `.synthetic` generated clips (never
    speakers), `.unattributed` clips with neither a speaker nor a synthetic mark, `.conflicts` clips that are both, `.skipped` listed clips whose
    file is missing or outside the folder, `.audio_hashes` SHA-256 of every reference file that exists."""

    def __init__(self):
        super().__init__()
        self.in_word = set()
        self.dirs = []
        self.speakers = set()
        self.synthetic = 0
        self.unattributed = []
        self.conflicts = []
        self.skipped = []
        self.audio_hashes = set()


def load_bank(unit_dirs, use_in_word=True):
    """One folder or a list. Per unit a clip cut out of a word (meta voice contains 'cut from word') wins over an isolated clip; otherwise the
    earlier folder wins. use_in_word=False leaves the cut-out clips out of the bank (an ablation: isolated clips only) but still records their
    provenance, so the speaker and leakage checks are the same in every configuration."""
    bank = Bank()
    bank.dirs = [unit_dirs] if isinstance(unit_dirs, str) else list(unit_dirs)
    for unit_dir in bank.dirs:
        for it in _meta_items(unit_dir):
            if not it.get("file"):
                continue
            u = norm(it["word_ml"])
            spk = speaker_key(it.get("speaker"))
            if spk:
                bank.speakers.add(spk)                            # registered even if the clip cannot be loaded: the person is a reference speaker
            p = os.path.join(unit_dir, it["file"])
            inside = os.path.realpath(p).startswith(os.path.realpath(unit_dir) + os.sep)
            if not (inside and os.path.exists(p)):
                bank.skipped.append({"unit": u, "file": str(it["file"]), "reason": "outside the folder" if not inside else "file missing"})
                continue
            bank.audio_hashes.add(file_sha256(p))
            if it.get("synthetic"):
                bank.synthetic += 1
            if it.get("synthetic") and spk:
                bank.conflicts.append(u)
            if not it.get("synthetic") and not spk:
                bank.unattributed.append(u)
            cut = "cut from word" in str(it.get("voice", "")) or bool(it.get("in_word"))
            if cut and not use_in_word:
                continue
            if u not in bank or (cut and u not in bank.in_word):
                bank[u] = p
                if cut:
                    bank.in_word.add(u)
                else:
                    bank.in_word.discard(u)
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
    """Builds the template of a word from its unit clips.

    duration  "none"     templates are the joined clips as they are (the default: the plain matcher)
              "cut"      a unit spoken alone (not in bank.in_word), longer than 0.2 s and not last, loses its sustained middle (about half is kept;
                         Kahn et al. 1984). Audio is cut, so spectral transitions inside that part are lost.
              "squeeze"  the same units keep ALL their audio, but their FEATURE frames are time-compressed to the same length (no audio is removed)
    smooth_ms  window in ms over which the template's features are smoothed around each join (0 = off, the default)."""

    def __init__(self, bank, duration_rule=False, smooth_ms=0.0, duration=None):
        self.bank = bank
        self.duration = duration or ("cut" if duration_rule else "none")
        if self.duration not in ("none", "cut", "squeeze"):
            raise ValueError("duration must be none, cut or squeeze")
        if smooth_ms is not None and smooth_ms < 0:
            raise ValueError("smooth_ms must be 0 (off) or a positive window in ms, not %s" % smooth_ms)
        self.smooth_ms = smooth_ms or 0.0
        self.in_word = getattr(bank, "in_word", set())
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

    def _shorten(self, unit, i, n, dur):
        return self.duration != "none" and unit not in self.in_word and i != n - 1 and dur > jn.DEFAULTS["iso_min_s"]

    def est_duration(self, word):
        """Seconds the template will last, without building it; None if a unit has no clip."""
        us = self.units_of(word)
        if not us:
            return None
        total = 0.0
        for i, u in enumerate(us):
            r, x = self.clip(u)
            d = len(x) / float(r)
            if self._shorten(u, i, len(us), d):
                d = max(jn.DEFAULTS["min_keep_s"], d * jn.DEFAULTS["iso_scale"])
            total += d
        return total - jn.DEFAULTS["xfade_ms"] / 1000.0 * 0.5 * (len(us) - 1)

    def template(self, word):
        if word in self._cache:
            return self._cache[word]
        us = self.units_of(word)
        res = None
        if us:
            clips = [self.clip(u) for u in us]
            rate = clips[0][0]
            iso = [self.duration == "cut" and u not in self.in_word for u in us]
            audio, edges = jn.join([jn.resample(c[1], c[0], rate) for c in clips], rate, isolated=iso)
            if len(audio) > 400:
                feat = mfcc(audio, rate)
                if self.duration == "squeeze":
                    feat = self._squeeze(feat, edges, us, [len(c[1]) / float(c[0]) for c in clips])
                if self.smooth_ms > 0:
                    feat = jn.smooth_features(feat, edges, win_ms=self.smooth_ms)
                res = {"units": us, "audio": audio, "rate": rate, "edges": edges, "feat": feat}
        self._cache[word] = res
        return res

    def _squeeze(self, feat, edges, units, clip_secs):
        """Time-compress the feature frames of each isolated, long, non-final unit to about half (linear interpolation of the frames)."""
        out, n = [], len(units)
        for i, u in enumerate(units):
            a, b = int(round(edges[i] * 50)), int(round(edges[i + 1] * 50))
            if i == n - 1:
                b = len(feat)
            seg = feat[a:b]
            if len(seg) == 0:
                continue
            if self._shorten(u, i, n, clip_secs[i]):
                m = max(int(jn.DEFAULTS["min_keep_s"] * 50), int(round(len(seg) * jn.DEFAULTS["iso_scale"])))
                if m < len(seg):
                    idx = np.linspace(0, len(seg) - 1, m)
                    lo = np.floor(idx).astype(int)
                    hi = np.minimum(lo + 1, len(seg) - 1)
                    seg = seg[lo] * (1 - (idx - lo)[:, None]) + seg[hi] * (idx - lo)[:, None]
            out.append(seg)
        return np.concatenate(out) if out else feat


def rank_composed(query_feat, composer, words, shortlist=150, dur_ratio=(0.4, 2.5), max_templates=20000, track=None):
    """-> (ranking [(word, dist)] best first, n_candidates, n_without_clips, info). Stages: (1) a word with a unit that has no clip is dropped
    ('no_clips'); (2) a length filter ('length_filter'); (3) at most `max_templates` templates are built, the rest are NOT tried and are counted in
    info['templates_truncated'] ('template_limit'); (4) a cheap squashed-feature distance keeps the best `shortlist` ('stage1_shortlist');
    (5) DTW ranks those. info['tracked'] says at which stage the word given as `track` was lost (None = it was ranked)."""
    if shortlist < 1 or max_templates < 1:
        raise ValueError("shortlist and max_templates must be at least 1")
    cands, missing, truncated = [], 0, 0
    lost = {}
    qs = _squash(query_feat)
    q_dur = len(query_feat) * 0.02
    built = 0
    for w in words:
        est = composer.est_duration(w)
        if est is None:
            missing += 1
            lost[w] = "no_clips"
            continue
        if not dur_ratio[0] <= est / max(q_dur, 1e-3) <= dur_ratio[1]:
            lost[w] = "length_filter"                              # cheap length filter BEFORE the expensive template is built
            continue
        if built >= max_templates:
            truncated += 1
            lost[w] = "template_limit"
            continue
        built += 1
        t = composer.template(w)
        if t is None:
            missing += 1
            lost[w] = "no_clips"
            continue
        r = len(t["feat"]) / float(len(query_feat))
        if not dur_ratio[0] <= r <= dur_ratio[1]:
            lost[w] = "length_filter"
            continue
        cands.append((float(np.sqrt(((qs - _squash(t["feat"])) ** 2).sum(axis=1)).mean()), w, t))
    cands.sort(key=lambda x: x[0])
    for _sc, w, _t in cands[shortlist:]:
        lost[w] = "stage1_shortlist"
    scored = sorted(((dtw(query_feat, t["feat"]), w) for _s, w, t in cands[:shortlist]))
    info = {"templates_truncated": truncated, "tracked": lost.get(track) if track is not None else None}
    return [(w, d) for d, w in scored], len(cands), missing, info


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
def search(query_wav, bank, words, heard=None, top=5, shortlist=150, rescore_top=200):
    return search_with(query_wav, Composer(bank), words, heard, top, shortlist, rescore_top)


def search_with(query_wav, comp, words, heard=None, top=5, shortlist=150, rescore_top=200):
    """Like search() but reuses a Composer, so the joined templates are built once and kept (what the recorder does)."""
    if top < 1 or shortlist < 1 or rescore_top < 1:
        raise ValueError("top, shortlist and rescore_top must be at least 1")
    rate, x = jn.read_wav(query_wav)
    qf = mfcc(x, rate)
    wlist = [w for w, _u in words]
    composed, n_cand, missing, info = rank_composed(qf, comp, wlist, shortlist)
    out = {"composed": composed[:top], "candidates_with_clips": n_cand, "words_without_all_unit_clips": missing,
           "labels": [], "fused": [], "rescored": [], "note": "", "templates_truncated": info["templates_truncated"]}
    if heard:
        lab = rank_labels(heard, words)
        out["labels"] = lab[:top]
        out["fused"] = fuse(composed, lab)[:top]
        # rescoring (Sun & Zhao 2014): composed DTW only on the recogniser's / label method's best candidates, not the whole dictionary
        short = [w for w, _d in lab[:rescore_top]]
        resc, _n, _m, _i = rank_composed(qf, comp, short, shortlist)
        out["rescored"] = resc[:top]
        out["rescore_candidates"] = len(short)
    else:
        out["note"] = "no recogniser text given: only the composed-template ranking is shown"
    return out


def _rank_of(r, word):
    for i, (w, _s) in enumerate(r):
        if w == word:
            return i + 1
    return None


RECALL_AT = (10, 50, 100, 200)


class SpeakerLeak(ValueError):
    """A query was spoken by someone whose recording is also a reference clip, or its audio IS a reference clip."""


def metadata_report(bank):
    """What the speaker classification rests on. A reference clip with no speaker and no synthetic mark could be a person: any query from that person
    would be wrongly called unseen. The same holds for a clip that is marked both, and for a listed clip that could not be loaded. `ok` is True only
    when none of that is present; an unseen-speaker claim is valid only then."""
    unattr, conf, skipped = getattr(bank, "unattributed", []), getattr(bank, "conflicts", []), getattr(bank, "skipped", [])
    return {"reference_speakers": sorted(getattr(bank, "speakers", set())), "synthetic_clips": getattr(bank, "synthetic", 0),
            "unattributed_clips": len(unattr), "unattributed_units": sorted(set(unattr))[:20], "conflicting_clips": len(conf),
            "skipped_clips": len(skipped), "skipped": skipped[:20], "reference_files_hashed": len(getattr(bank, "audio_hashes", ())),
            "ok": not unattr and not conf and not skipped}


def query_audio_in_reference(path, bank):
    """True when the query file is byte-identical to a reference clip: a leak whatever the metadata says."""
    try:
        return file_sha256(path) in getattr(bank, "audio_hashes", ())
    except OSError:
        return False


def speaker_leaks(queries, bank):
    """[(word, speaker or 'identical audio')] for every query whose speaker also supplied a reference clip (compared after speaker_key), or whose
    audio file is a copy of a reference clip."""
    ref = getattr(bank, "speakers", set())
    out = []
    for q in queries:
        spk = speaker_key(q[2]) if len(q) > 2 else ""
        if spk and spk in ref:
            out.append((q[0], q[2]))
        elif query_audio_in_reference(q[1], bank):
            out.append((q[0], "identical audio"))
    return out


def _closed(rows):
    return [r for r in rows if r["in_dictionary"]]


def _rate(rs, m, k):
    return round(sum(1 for r in rs if r.get(m) and r[m] <= k) / len(rs), 3)


def evaluate(queries, bank, words, heard=None, shortlist=150, rescore_top=200, composer=None, allow_same_speaker=False, max_templates=20000):
    """queries: [(word, wav path[, speaker])]; heard: {word: recognised text}.
    -> (summary, rows). Every query is a row, failures included. Closed-dictionary figures (top-1 / top-5 / MRR per method, by speaker, by condition,
    shortlist recall) are computed over queries whose word IS in the dictionary only; the others are counted in summary['queries'] and
    ['not_in_dictionary']. A query whose audio cannot be read is a counted miss (never an abort). Conditions: seen_speaker (the speaker, or the exact
    audio, is in the reference bank), unseen_speaker (only when the reference metadata is complete, summary['unseen_speaker_claim_valid']),
    unseen_unverified (the same person-level fact but the reference provenance is incomplete, so no unseen claim is made), speaker_unknown."""
    import time
    if shortlist < 1 or rescore_top < 1 or max_templates < 1:
        raise ValueError("shortlist, rescore_top and max_templates must be at least 1")
    leaks = speaker_leaks(queries, bank)
    if leaks and not allow_same_speaker:
        raise SpeakerLeak("%d query(ies) are spoken by a person who also supplied reference clips, or are copies of them (%s): this is not an "
                          "unseen-speaker test. Remove them, or pass allow_same_speaker / --allow-same-speaker to report them separately as 'seen_speaker'."
                          % (len(leaks), ", ".join(sorted({str(sp) for _w, sp in leaks}))))
    meta = metadata_report(bank)
    comp = composer or Composer(bank)
    words = [(norm(w), u) for w, u in words]
    wlist = [w for w, _u in words]
    in_dict = set(wlist)
    heard_n = {norm(k): v for k, v in heard.items()} if heard is not None else None
    ref_speakers = getattr(bank, "speakers", set())
    leak_set = {(w, sp) for w, sp in leaks}
    rows = []
    for q in queries:
        word, path = norm(q[0]), q[1]
        speaker = q[2] if len(q) > 2 else None
        sk = speaker_key(speaker)
        audio_dup = (q[0], "identical audio") in leak_set
        if (sk and sk in ref_speakers) or audio_dup:
            cond = "seen_speaker"
        elif sk:
            cond = "unseen_speaker" if meta["ok"] else "unseen_unverified"
        else:
            cond = "speaker_unknown"
        row = {"word": word, "speaker": speaker, "in_dictionary": word in in_dict, "condition": cond, "audio_in_reference": audio_dup}
        t0 = time.time()
        try:
            rate, x = jn.read_wav(path)
            qf = mfcc(x, rate)
        except (OSError, ValueError, EOFError, wave.Error) as e:
            row.update({"error": "unusable query audio: %s" % e, "composed": None, "lost_at": "unusable_audio", "ms_composed": 0.0})
            qf = None
        if qf is not None:
            composed, _n, _m, info = rank_composed(qf, comp, wlist, shortlist, max_templates=max_templates, track=word)
            row.update({"composed": _rank_of(composed, word), "ms_composed": round(1000 * (time.time() - t0), 1),
                        "lost_at": None if _rank_of(composed, word) else (info["tracked"] or ("not_in_dictionary" if word not in in_dict else "dtw_not_ranked")),
                        "templates_truncated": info["templates_truncated"]})
        if heard_n is not None and word in heard_n:
            t1 = time.time()
            try:
                lab = rank_labels(heard_n[word], words)
            except ValueError as e:                                # recogniser text that cannot be cut into units: a counted miss, not an abort
                lab = None
                row["heard_error"] = str(e)
            row["ms_labels"] = round(1000 * (time.time() - t1), 1)
            row["labels"] = _rank_of(lab, word) if lab is not None else None
            if lab is None:
                row["fused"] = row["rescored"] = None
            elif qf is not None:
                row["fused"] = _rank_of(fuse(composed, lab), word)
                t2 = time.time()
                resc, _n2, _m2, _i2 = rank_composed(qf, comp, [w for w, _d in lab[:rescore_top]], shortlist, max_templates=max_templates)
                row["ms_rescored"] = round(1000 * (time.time() - t2), 1)
                row["rescored"] = _rank_of(resc, word)
            else:
                row["fused"] = row["rescored"] = None
        rows.append(row)
    closed = _closed(rows)
    summary = {"queries": {"total": len(rows), "in_dictionary": len(closed), "out_of_dictionary": len(rows) - len(closed),
                           "unusable": sum(1 for r in rows if "error" in r)}}
    for m in ("composed", "labels", "fused", "rescored"):
        rs = [r for r in closed if m in r]
        if rs:
            n = len(rs)
            summary[m] = {"n": n, "top1": _rate(rs, m, 1), "top5": _rate(rs, m, 5), "mrr": round(sum(1.0 / r[m] for r in rs if r[m]) / n, 3),
                          "not_found": sum(1 for r in rs if not r[m]), "mean_ms": round(sum(r.get("ms_" + m, 0.0) for r in rs) / n, 1)}
            spk = sorted({r["speaker"] for r in rs if r["speaker"]}, key=str)
            if spk:
                summary[m]["top1_by_speaker"] = {str(sp): round(sum(1 for r in rs if r["speaker"] == sp and r[m] == 1) / max(1, sum(1 for r in rs if r["speaker"] == sp)), 3) for sp in spk}
    cond = {}
    for c in sorted({r["condition"] for r in closed}):
        sub = [r for r in closed if r["condition"] == c]
        cond[c] = {"n": len(sub)}
        for m in ("composed", "labels", "fused", "rescored"):
            rs = [r for r in sub if m in r]
            if rs:
                cond[c][m] = {"top1": _rate(rs, m, 1), "top5": _rate(rs, m, 5)}
    if "unseen_unverified" in cond:
        cond["unseen_unverified"]["note"] = "NOT an unseen-speaker result: reference provenance is incomplete (see reference_metadata)"
    summary["by_condition"] = cond
    summary["unseen_speaker_claim_valid"] = bool(meta["ok"])
    summary["reference_metadata"] = meta
    out_dict = [r["word"] for r in rows if not r["in_dictionary"]]
    if out_dict:
        summary["not_in_dictionary"] = {"n": len(out_dict), "words": sorted(set(out_dict)), "note": "cannot be recognised by construction; excluded from every closed-dictionary figure above"}
    stages = {}
    for r in closed:
        k = "found" if r.get("lost_at") is None and r.get("composed") else (r.get("lost_at") or "dtw_not_ranked")
        stages[k] = stages.get(k, 0) + 1
    summary["composed_stages"] = dict(sorted(stages.items()))
    summary["composed_stages_note"] = "where the composed method lost the correct word; template_limit means candidates were never tried"
    if heard_n is not None:
        summary["missing_heard"] = sum(1 for r in closed if "labels" not in r)
        summary["heard_unusable"] = sum(1 for r in closed if "heard_error" in r)
    lr = [r for r in closed if "labels" in r]
    if lr:
        ks = sorted(set(RECALL_AT) | {rescore_top})
        summary["shortlist_recall"] = {"n": len(lr), **{"top%d" % k: _rate(lr, "labels", k) for k in ks}, "configured_rescore_top": rescore_top}
    return summary, rows


# ------------------------------------------------------------------ Gate C: a frozen, verifiable configuration
class FrozenMismatch(ValueError):
    """The inputs or settings no longer match what was frozen."""


class LedgerRefusal(ValueError):
    """A second, different configuration was run on the same locked query set."""


FREEZE_SCHEMA = "s2w-wordsearch-freeze-v1"
DEFAULT_SETTINGS = {"duration": "none", "smooth_ms": 0.0, "shortlist": 150, "rescore_top": 200, "max_templates": 20000, "dur_ratio": [0.4, 2.5]}


def _sha_json(obj):
    import hashlib
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def input_manifests(units, lexicon, queries_dir):
    """Hashes of everything an evaluation depends on: the dictionary file, every reference clip, every query clip with its word and speaker."""
    bank = load_bank(units)
    items = [it for it in _meta_items(queries_dir) if it.get("file")]
    q = sorted([norm(it["word_ml"]), speaker_key(it.get("speaker")), file_sha256(os.path.join(queries_dir, it["file"]))] for it in items)
    return bank, items, {"lexicon_sha256": file_sha256(lexicon), "reference_clips_sha256": _sha_json(sorted(bank.audio_hashes)), "reference_clips": len(bank.audio_hashes),
                         "queries_sha256": _sha_json(q), "queries": len(q), "dictionary_words": len(load_words(lexicon))}


def freeze_problems(bank, query_items, queries_dir):
    """Reasons this setup must NOT be frozen (an invalid or unvalidated setup is never frozen to complete a gate)."""
    probs = []
    if not bank:
        probs.append("the reference bank is empty")
    meta = metadata_report(bank)
    if not meta["ok"]:
        probs.append("reference provenance is incomplete: %d unattributed, %d conflicting, %d skipped clip(s)" % (meta["unattributed_clips"], meta["conflicting_clips"], meta["skipped_clips"]))
    if not query_items:
        probs.append("there are no query clips")
    no_spk = [it.get("file") for it in query_items if not speaker_key(it.get("speaker"))]
    if no_spk:
        probs.append("%d query clip(s) have no speaker ID, so the seen/unseen split cannot be checked (e.g. %s)" % (len(no_spk), no_spk[0]))
    qs = [(norm(it["word_ml"]), os.path.join(queries_dir, it["file"]), it.get("speaker")) for it in query_items]
    leaks = speaker_leaks(qs, bank)
    if leaks:
        probs.append("%d query clip(s) leak from the reference set (same speaker or identical audio): %s" % (len(leaks), ", ".join(sorted({str(sp) for _w, sp in leaks}))))
    return probs


def freeze_config(units, lexicon, queries_dir, settings=None, allow_dirty=False, repo_dir=None):
    """-> config dict with `config_sha256`. Raises ValueError listing every reason when the setup is not valid to freeze."""
    st = dict(DEFAULT_SETTINGS)
    st.update(settings or {})
    if st["duration"] not in ("none", "cut", "squeeze") or st["smooth_ms"] < 0 or min(st["shortlist"], st["rescore_top"], st["max_templates"]) < 1:
        raise ValueError("invalid settings: %s" % st)
    bank, items, man = input_manifests(units, lexicon, queries_dir)
    probs = freeze_problems(bank, items, queries_dir)
    commit, dirty = _git_state(repo_dir)
    if dirty and not allow_dirty:
        probs.append("the code working tree has uncommitted changes (commit first, or pass --allow-dirty and accept that the freeze names a dirty tree)")
    if probs:
        raise ValueError("not frozen: " + "; ".join(probs))
    cfg = {"schema": FREEZE_SCHEMA, "settings": st, "inputs": man, "code": {"git_commit": commit, "dirty": dirty},
           "evidence": ("SYNTHETIC: software validation only, NOT Malayalam recognition accuracy" if bank.synthetic and bank.synthetic == len(bank.audio_hashes)
                        else "reference clips include non-synthetic audio: check consent and verification before quoting results"),
           "reference_speakers": sorted(bank.speakers), "reference_folders": len(bank.dirs)}
    cfg["config_sha256"] = _sha_json(cfg)
    return cfg


def _git_state(repo_dir=None):
    import subprocess
    d = repo_dir or os.path.dirname(os.path.abspath(__file__))
    try:
        c = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True, timeout=20).stdout.strip() or "unknown"
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "tools", "core", "evaluation"], cwd=d, capture_output=True, text=True, timeout=20).stdout.strip())
        return c, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", True


def verify_frozen(cfg, units, lexicon, queries_dir):
    """[] when the frozen file is intact and every input still hashes the same; otherwise one message per difference."""
    probs = []
    body = {k: v for k, v in cfg.items() if k != "config_sha256"}
    if cfg.get("schema") != FREEZE_SCHEMA:
        probs.append("not a %s file" % FREEZE_SCHEMA)
    if _sha_json(body) != cfg.get("config_sha256"):
        probs.append("the frozen file was edited after freezing (its own hash does not match)")
    _bank, _items, man = input_manifests(units, lexicon, queries_dir)
    for k, v in man.items():
        if cfg.get("inputs", {}).get(k) != v:
            probs.append("%s changed since the freeze" % k)
    return probs


def ledger_check(ledger_path, cfg, allow_reuse=False):
    """Append-only record of evaluations on a frozen query set. The same config may be re-run (reproduction); a DIFFERENT config on the same query
    set is refused unless allow_reuse, and then it is recorded as a reuse. Returns True when this run is a reuse."""
    entries = []
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
    q = cfg["inputs"]["queries_sha256"]
    other = [e for e in entries if e["queries_sha256"] == q and e["config_sha256"] != cfg["config_sha256"]]
    if other and not allow_reuse:
        raise LedgerRefusal("this query set was already evaluated with a different frozen configuration (%s...); a second configuration on the same locked "
                            "queries is test reuse. Pass --allow-test-reuse to record it as such." % other[0]["config_sha256"][:12])
    import time
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"config_sha256": cfg["config_sha256"], "queries_sha256": q, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "reuse": bool(other)}, sort_keys=True) + "\n")
    return bool(other)


CONFIGS = {"plain (default: no duration rule, no smoothing)": dict(duration="none", smooth_ms=0),
           "audio cut (duration rule) only": dict(duration="cut", smooth_ms=0),
           "feature squeeze (no audio removed) only": dict(duration="squeeze", smooth_ms=0),
           "smoothing 60 ms only": dict(duration="none", smooth_ms=60),
           "audio cut + smoothing 60 ms": dict(duration="cut", smooth_ms=60),
           "feature squeeze + smoothing 60 ms": dict(duration="squeeze", smooth_ms=60)}


def compare_configs(queries, bank, words, heard=None, shortlist=150, rescore_top=200, allow_same_speaker=False):
    """Run EVERY planned configuration on the same queries and report all of them, including the ones that do worse (CLAUDE.md rule 5).
    When the bank has clips cut out of words, one more run uses isolated clips only, to isolate the effect of the in-word clips. Rescoring is
    reported as its own method in every summary (next to the plain composed ranking and the shortlist recall), so its effect is also separate."""
    out = {}
    for name, kw in CONFIGS.items():
        summary, rows = evaluate(queries, bank, words, heard, shortlist, rescore_top, composer=Composer(bank, **kw), allow_same_speaker=allow_same_speaker)
        out[name] = {"summary": summary, "per_word": rows}
    if getattr(bank, "in_word", None) and bank.dirs:
        iso = load_bank(bank.dirs, use_in_word=False)
        summary, rows = evaluate(queries, iso, words, heard, shortlist, rescore_top, composer=Composer(iso), allow_same_speaker=allow_same_speaker)
        out["ablation: plain, isolated clips only (clips cut from words ignored)"] = {"summary": summary, "per_word": rows}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--units", required=True, nargs="+", help="one or more unit folders; the first folder wins unless a later one has a clip cut out of a word")
    s.add_argument("--lexicon", required=True)
    s.add_argument("--heard", help="what a recogniser heard, in Malayalam script")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--limit", type=int, help="only use the first N lexicon words")
    e = sub.add_parser("eval")
    e.add_argument("--queries", required=True)
    e.add_argument("--units", required=True, nargs="+")
    e.add_argument("--lexicon", required=True)
    e.add_argument("--heard-file", help="JSON {word: recognised text}")
    e.add_argument("--limit", type=int)
    e.add_argument("--allow-same-speaker", action="store_true", help="evaluate queries spoken by a person who also supplied reference clips; they are reported separately as seen_speaker, never mixed into the unseen-speaker result")
    e.add_argument("--strict-metadata", action="store_true", help="refuse when reference provenance is incomplete (exit 4)")
    e.add_argument("--compare-configs", action="store_true", help="run every duration-rule / smoothing configuration and report each")
    e.add_argument("--frozen", help="a freeze file: settings come from it, and the evaluation is refused (exit 5) if any input hash changed")
    e.add_argument("--ledger", help="evaluation ledger for the frozen query set (default: test_ledger.jsonl inside the queries folder)")
    e.add_argument("--allow-test-reuse", action="store_true", help="with --frozen: record a second, different configuration on the same query set as a reuse (exit 6 otherwise)")
    f = sub.add_parser("freeze", help="write a verifiable freeze file (settings + hashes of dictionary, reference clips and queries); refuses an invalid setup (exit 7)")
    f.add_argument("--queries", required=True)
    f.add_argument("--units", required=True, nargs="+")
    f.add_argument("--lexicon", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--allow-dirty", action="store_true")
    for q in (s, e, f):
        q.add_argument("--duration", choices=("none", "cut", "squeeze"), default="none", help="duration rule for isolated non-final units (default none)")
        q.add_argument("--smooth-ms", type=float, default=0.0, help="join smoothing window in ms (default 0 = off)")
        q.add_argument("--rescore-top", type=int, default=200)
    for q in (e, f):
        q.add_argument("--shortlist", type=int, default=150, help="composed-DTW shortlist size")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "freeze":
            try:
                cfg = freeze_config(a.units, a.lexicon, a.queries, {"duration": a.duration, "smooth_ms": a.smooth_ms, "shortlist": a.shortlist, "rescore_top": a.rescore_top}, a.allow_dirty)
            except ValueError as ex:
                print("refused: %s" % ex, file=sys.stderr)
                return 7
            with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=1, sort_keys=True)
                fh.write("\n")
            print("froze %s (config_sha256 %s...; %d reference clips, %d queries)" % (a.out, cfg["config_sha256"][:12], cfg["inputs"]["reference_clips"], cfg["inputs"]["queries"]))
            return 0
        bank = load_bank(a.units)
        words = load_words(a.lexicon, a.limit)
        if a.cmd == "search":
            r = search_with(a.query, Composer(bank, smooth_ms=a.smooth_ms, duration=a.duration), words, a.heard, a.top, rescore_top=a.rescore_top)
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0
        settings = {"duration": a.duration, "smooth_ms": a.smooth_ms, "shortlist": a.shortlist, "rescore_top": a.rescore_top, "max_templates": 20000}
        cfg = None
        if a.frozen:
            if a.compare_configs or a.limit:
                raise ValueError("--frozen cannot be combined with --compare-configs or --limit: the frozen file fixes the settings and the dictionary")
            with open(a.frozen, encoding="utf-8") as fh:
                cfg = json.load(fh)
            diffs = verify_frozen(cfg, a.units, a.lexicon, a.queries)
            if diffs:
                print("refused: the frozen setup does not match: %s" % "; ".join(diffs), file=sys.stderr)
                return 5
            settings.update(cfg["settings"])
            try:
                reuse = ledger_check(a.ledger or os.path.join(a.queries, "test_ledger.jsonl"), cfg, a.allow_test_reuse)
            except LedgerRefusal as ex:
                print("refused: %s" % ex, file=sys.stderr)
                return 6
        meta = metadata_report(bank)
        if not meta["ok"]:
            msg = "reference provenance is incomplete: %d unattributed (no speaker, not marked synthetic), %d conflicting, %d skipped clip(s); no unseen-speaker claim is made" % (meta["unattributed_clips"], meta["conflicting_clips"], meta["skipped_clips"])
            if a.strict_metadata:
                print("refused: " + msg, file=sys.stderr)
                return 4
            print("warning: " + msg, file=sys.stderr)
        heard = None
        if a.heard_file:
            with open(a.heard_file, encoding="utf-8") as fh:
                heard = json.load(fh)
        qs = [(norm(it["word_ml"]), os.path.join(a.queries, it["file"]), it.get("speaker")) for it in _meta_items(a.queries) if it.get("file")]
        if a.compare_configs:
            res = compare_configs(qs, bank, words, heard, rescore_top=a.rescore_top, allow_same_speaker=a.allow_same_speaker)
            print(json.dumps({"configurations": res, "chance_top1": round(1.0 / max(1, len(words)), 5), "words": len(words), "units_with_clips": len(bank),
                              "units_cut_from_words": len(getattr(bank, "in_word", ()))}, ensure_ascii=False, indent=1))
            return 0
        comp = Composer(bank, smooth_ms=settings["smooth_ms"], duration=settings["duration"])
        summary, rows = evaluate(qs, bank, words, heard, shortlist=settings["shortlist"], rescore_top=settings["rescore_top"], composer=comp,
                                 allow_same_speaker=a.allow_same_speaker, max_templates=settings["max_templates"])
        out = {"summary": summary, "per_word": rows, "chance_top1": round(1.0 / max(1, len(words)), 5), "words": len(words), "units_with_clips": len(bank), "settings": settings}
        if cfg:
            out["frozen"] = {"config_sha256": cfg["config_sha256"], "evidence": cfg["evidence"], "test_reuse": reuse}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    except SpeakerLeak as ex:
        print("refused: %s" % ex, file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError) as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
