#!/usr/bin/env python3
"""s2w_alphabet: the Malayalam alphabet as an inventory, with labels, look-alike groups, audio, measurements and listening verdicts.

  python3 tools/s2w_alphabet.py inventory --out data/private/alphabet [--lexicon data/private/dictionary/lexicon.csv] [--clusters-top 450]
      writes OUT/alphabet.csv (everything below) and OUT/inventory.json (the format tools/model_eval/gen_units.py reads, so the
      existing local TTS scripts can generate one clip per entry), and prints the counts with their DEFINITIONS.
  python3 tools/s2w_alphabet.py unicode      the Unicode Malayalam block, partitioned into named groups (sums to the assigned total)

The recorder serves it as /alphabet:  python3 tools/s2w_recorder.py ... --alphabet-audio-dir DIR [DIR ...] [--alphabet-lexicon FILE]

Three inventories are kept apart on purpose (a Unicode character is not a sound, and a written syllable is not a pronunciation):
  1. BASE      independent vowels, consonant letters, chillu letters (counted from the Unicode chart, nothing hard-coded)
  2. COMBINED  consonant x 16 forms (virama, inherent a, 12 vowel signs, anusvara, visarga), standalone vowel+anusvara/visarga, and
               conjunct clusters TAKEN FROM A DICTIONARY (frequency-ranked) - not every C+virama+C pair is a real Malayalam syllable
  3. SOUND     per entry an IPA reading and a Manglish label. Look-alike labels are flagged: the SAME Manglish for DIFFERENT letters
               (ശ/ഷ "sha", ന/ണ "na", ര/റ "ra", ല/ള "la", ത/ഥ "tha", ദ/ഡ "da" ...) and the acoustic neighbours that are easy to confuse.
Identity is always the Malayalam text / code points, never the Manglish. The IPA, the descriptions and the neighbour sets below are a
curated reference written for this project and are marked as such: a native speaker has to confirm them by LISTENING. Nothing here
can hear: measurements (duration, spectral centroid) only say whether two clips are acoustically different, and the listening
verdicts (ok / wrong / unsure) are a separate, human record."""
import argparse
import csv
import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_units as su  # noqa: E402
import s2w_viewer as v  # noqa: E402

VIR, ANU, VIS = "്", "ം", "ഃ"

# ---------------------------------------------------------------- curated reference (to be confirmed by a native listener)
# letter -> (common typed Manglish, IPA, short description). Duplicated Manglish is deliberate: it is how people type, and the
# collisions are exactly what must be made visible.
CONS = {
    "ക": ("ka", "k", "velar stop"), "ഖ": ("kha", "kʰ", "aspirated velar stop"), "ഗ": ("ga", "ɡ", "voiced velar stop"), "ഘ": ("gha", "ɡʱ", "breathy velar stop"),
    "ങ": ("nga", "ŋ", "velar nasal"),
    "ച": ("cha", "tʃ", "palatal affricate"), "ഛ": ("chha", "tʃʰ", "aspirated palatal affricate"), "ജ": ("ja", "dʒ", "voiced palatal affricate"),
    "ഝ": ("jha", "dʒʱ", "breathy palatal affricate"), "ഞ": ("nja", "ɲ", "palatal nasal"),
    "ട": ("ta", "ʈ", "retroflex stop"), "ഠ": ("tha", "ʈʰ", "aspirated retroflex stop"), "ഡ": ("da", "ɖ", "voiced retroflex stop"),
    "ഢ": ("dha", "ɖʱ", "breathy retroflex stop"), "ണ": ("na", "ɳ", "retroflex nasal"),
    "ത": ("tha", "t̪", "dental stop"), "ഥ": ("tha", "t̪ʰ", "aspirated dental stop"), "ദ": ("da", "d̪", "voiced dental stop"),
    "ധ": ("dha", "d̪ʱ", "breathy dental stop"), "ന": ("na", "n̪", "dental nasal"),
    "പ": ("pa", "p", "labial stop"), "ഫ": ("pha", "pʰ", "aspirated labial stop"), "ബ": ("ba", "b", "voiced labial stop"), "ഭ": ("bha", "bʱ", "breathy labial stop"),
    "മ": ("ma", "m", "labial nasal"),
    "യ": ("ya", "j", "palatal glide"), "ര": ("ra", "ɾ", "tap"), "ല": ("la", "l", "alveolar lateral"), "വ": ("va", "ʋ", "labiodental approximant"),
    "ശ": ("sha", "ɕ", "soft sha: palatal sibilant"), "ഷ": ("sha", "ʂ", "sha: retroflex sibilant"), "സ": ("sa", "s", "sa: alveolar sibilant"),
    "ഹ": ("ha", "ɦ", "glottal fricative"),
    "ള": ("la", "ɭ", "retroflex lateral"), "ഴ": ("zha", "ɻ", "retroflex approximant (zha)"), "റ": ("ra", "r", "trill (and the geminate in റ്റ)"),
}
SIGNS = {  # sign -> (Manglish as in the project owner's inventory, IPA of the vowel, description)
    "": ("a", "a", "inherent a"), "ാ": ("aa", "aː", "long aa"), "ി": ("i", "i", "short i"), "ീ": ("ee", "iː", "long ii"), "ു": ("u", "u", "short u"),
    "ൂ": ("oo", "uː", "long uu"), "ൃ": ("ru", "ɾɨ", "vocalic r"), "െ": ("e", "e", "short e"), "േ": ("ee", "eː", "long ee (ē)"), "ൈ": ("ai", "ai̯", "ai"),
    "ൊ": ("o", "o", "short o"), "ോ": ("oo", "oː", "long oo (ō)"), "ൗ": ("au", "au̯", "au"),
}
FORMS = [(VIR, "virama", "", "consonant alone (chandrakkala)")] + [("", "a", "a", "inherent a")] + \
        [(s, n, "", "") for s, n in (("ാ", "aa"), ("ി", "i"), ("ീ", "ee"), ("ു", "u"), ("ൂ", "oo"), ("ൃ", "ru"), ("െ", "e"), ("േ", "ee"), ("ൈ", "ai"), ("ൊ", "o"), ("ോ", "oo"), ("ൗ", "au"))] + \
        [(ANU, "am", "", "anusvara"), (VIS, "aha", "", "visarga")]
VOWEL_LETTERS = {"അ": ("a", "a", "a"), "ആ": ("aa", "aː", "long aa"), "ഇ": ("i", "i", "short i"), "ഈ": ("ee", "iː", "long ii"), "ഉ": ("u", "u", "short u"),
                 "ഊ": ("oo", "uː", "long uu"), "ഋ": ("ru", "ɾɨ", "vocalic r"), "ഌ": ("lru", "lɨ", "vocalic l (archaic)"), "എ": ("e", "e", "short e"),
                 "ഏ": ("e", "eː", "long e (ē)"), "ഐ": ("ai", "ai̯", "ai"), "ഒ": ("o", "o", "short o"), "ഓ": ("o", "oː", "long o (ō)"), "ഔ": ("au", "au̯", "au"),
                 "ൠ": ("rruu", "ɾɨː", "long vocalic r (archaic)"), "ൡ": ("lruu", "lɨː", "long vocalic l (archaic)")}
CHILLU = {"ൺ": ("n", "ɳ", "chillu retroflex n"), "ൻ": ("n", "n̪", "chillu dental n"), "ർ": ("r", "r", "chillu r"), "ൽ": ("l", "l", "chillu l"),
          "ൾ": ("l", "ɭ", "chillu retroflex l"), "ൿ": ("k", "k", "chillu k (rare)")}
# letters whose label is the SAME for different sounds are found automatically; this adds a short disambiguating name for them
DISAMBIG = {"ശ": "soft sha", "ഷ": "hard sha", "ന": "dental na", "ണ": "retroflex na", "ര": "tap ra", "റ": "trill ra", "ല": "plain la", "ള": "retroflex la", "ത": "dental tha",
            "ഥ": "aspirated tha", "ട": "retroflex ta", "ദ": "dental da", "ഡ": "retroflex da", "ധ": "dental dha", "ഢ": "retroflex dha", "ഠ": "retroflex tha",
            "എ": "short e", "ഏ": "long e", "ഒ": "short o", "ഓ": "long o", "ൺ": "retroflex n", "ൻ": "dental n", "ൽ": "plain l", "ൾ": "retroflex l"}
SIGN_DISAMBIG = {"ീ": "long ii", "േ": "long ee", "ൂ": "long uu", "ോ": "long oo"}
# the names exactly as the project owner supplied them (docs/MALAYALAM_SOUND_INVENTORY.md, Part A): a second convention to compare
OWNER_CONS = dict(zip("കഖഗഘങചഛജഝഞടഠഡഢണതഥദധനപഫബഭമയരലവശഷസഹളഴറ",
                      "ka kha ga gha nga ca cha ja jha nja ta tha da dha na ta tha da dha na pa pha ba bha ma ya ra la va sha sha sa ha la zha ra".split()))
OWNER_VOWELS = {"അ": "a", "ആ": "aa", "ഇ": "i", "ഈ": "ee", "ഉ": "u", "ഊ": "oo", "ഋ": "ri", "ഌ": "lri", "എ": "e", "ഏ": "e", "ഐ": "ai", "ഒ": "o", "ഓ": "o", "ഔ": "au",
                "ൠ": "rri", "ൡ": "lrri"}
OWNER_SIGNS = {"ാ": "aa", "ി": "i", "ീ": "ee", "ു": "u", "ൂ": "oo", "ൃ": "ri", "െ": "e", "േ": "ee", "ൈ": "ai", "ൊ": "o", "ോ": "oo", "ൗ": "au"}
OWNER_CHILLU = {"ൺ": "n", "ൻ": "n", "ർ": "r", "ൽ": "l", "ൾ": "la", "ൿ": "k"}
# acoustic neighbours (easy to confuse even when the Manglish differs)
NEIGHBOURS = [("sibilants: ശ ഷ സ", "ശഷസ"), ("nasals: ന ണ ങ ഞ", "നണങഞ"), ("rhotics: ര റ", "രറ"), ("laterals: ല ള ഴ", "ലളഴ"), ("t: ത ട ഥ ഠ", "തടഥഠ"), ("d: ദ ഡ ധ ഢ", "ദഡധഢ")]


def nfc(s):
    return unicodedata.normalize("NFC", s)


def codes(text):
    return " ".join("U+%04X" % ord(c) for c in text)


def ipa_of(cons, sign):
    c, a = CONS[cons][1], SIGNS.get(sign)
    if sign == VIR:
        return c
    if sign == ANU:
        return c + "am"
    if sign == VIS:
        return c + "aha"
    return c + a[1]


def manglish_of(cons, sign):
    c = CONS[cons][0]
    if sign == VIR:
        return c[:-1] if c.endswith("a") else c
    if sign == ANU:
        return c + "m"
    if sign == VIS:
        return c + "h"
    base = c[:-1] if c.endswith("a") else c
    return base + SIGNS[sign][0]


def build_inventory(lexicon_path=None, clusters_top=450):
    """-> list of dicts, one per entry. Counts are computed, never hard-coded."""
    items = []

    def add(text, group, **kw):
        t = nfc(text)
        d = {"text": t, "group": group, "codes": codes(t), "consonant": "", "form": "", "manglish": "", "ipa": "", "desc": "", "rare": False, "uses": 0}
        d.update(kw)
        items.append(d)

    for ch in sorted(VOWEL_LETTERS):
        m, ipa, desc = VOWEL_LETTERS[ch]
        add(ch, "vowel", manglish=m, ipa=ipa, desc=desc, rare=ch in "ഌൠൡ")
    for ch, tail, m, ipa in (("അ", ANU, "am", "am"), ("അ", VIS, "aha", "aha")):
        add(ch + tail, "vowel+sign", manglish=m, ipa=ipa, desc="vowel with " + ("anusvara" if tail == ANU else "visarga"))
    for c in CONS:
        for sign, fname, _x, fdesc in FORMS:
            add(c + sign, "cv", consonant=c, form=sign, manglish=manglish_of(c, sign), ipa=ipa_of(c, sign),
                desc="%s + %s" % (CONS[c][2], fdesc or SIGNS.get(sign, ("", "", ""))[2]))
    for ch in sorted(CHILLU):
        m, ipa, desc = CHILLU[ch]
        add(ch, "chillu", manglish=m, ipa=ipa, desc=desc, rare=ch == "ൿ")
    if lexicon_path:
        known = {i["text"] for i in items}
        for text, n in clusters_from_lexicon(lexicon_path, clusters_top):
            if text not in known:
                add(text, "cluster", manglish=su_manglish(text), desc="conjunct cluster from the dictionary", uses=n)
    # collisions and labels
    by_m = {}
    for it in items:
        by_m.setdefault(it["manglish"], []).append(it)
    for it in items:
        same = [x["text"] for x in by_m[it["manglish"]] if x["text"] != it["text"]]
        it["same_manglish_as"] = same
        it["lookalike"] = bool(same)
        label = it["manglish"]
        if same:
            tag = DISAMBIG.get(it["consonant"] or it["text"]) if it["group"] != "cv" or not it["consonant"] else DISAMBIG.get(it["consonant"])
            sg = SIGN_DISAMBIG.get(it["form"]) if it["group"] == "cv" else None
            tags = [t for t in (tag, sg) if t]
            label = "%s (%s)" % (it["manglish"], ", ".join(tags)) if tags else it["manglish"]
        it["label"] = label
        it["neighbours"] = [n for n, letters in NEIGHBOURS if (it["consonant"] or it["text"][:1]) in letters]
    return items


def su_manglish(text):
    """Best-effort Manglish for a conjunct cluster (consonant letters from the curated table, virama joins, vowel sign last)."""
    out = []
    for ch in text:
        if ch in CONS:
            out.append(CONS[ch][0][:-1] if CONS[ch][0].endswith("a") else CONS[ch][0])
        elif ch in SIGNS:
            out.append(SIGNS[ch][0])
        elif ch == ANU:
            out.append("m")
        elif ch == VIS:
            out.append("h")
    s = "".join(out)
    return s + ("a" if text and text[-1] in CONS else "")


def clusters_from_lexicon(path, top):
    """[(akshara, count)] for written units made of two or more consonants joined by virama, most frequent first."""
    from collections import Counter
    csv.field_size_limit(10 ** 7)
    cnt = Counter()
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        col = "word_ml" if "word_ml" in (rd.fieldnames or []) else (rd.fieldnames or ["word"])[0]
        for r in rd:
            n = su.normalise(r.get(col, ""))
            for a in su.AKS.findall(n):
                if su.kind(a) == "cluster":
                    cnt[a] += 1
    return cnt.most_common(top)


def lookalike_report(convention="common"):
    """Groups of DIFFERENT letters that share one Manglish, per kind of letter, under a convention:
    'common' (the table above), 'owner' (Part A of docs/MALAYALAM_SOUND_INVENTORY.md) or 'rule' (ml_to_manglish, the recorder's spelling rule).
    -> {kind: {manglish: [letters]}} (only groups of two or more). 'cross' adds the groups that mix a chillu with consonant+virama forms."""
    import s2w_recorder as rec
    tabs = {"common": (lambda c: CONS[c][0], lambda c: VOWEL_LETTERS[c][0], lambda sg: SIGNS[sg][0], lambda c: CHILLU[c][0]),
            "owner": (OWNER_CONS.get, OWNER_VOWELS.get, OWNER_SIGNS.get, OWNER_CHILLU.get),
            "rule": (rec.ml_to_manglish, rec.ml_to_manglish, lambda sg: rec.ml_to_manglish("ക" + sg), rec.ml_to_manglish)}
    if convention not in tabs:
        raise v.ViewerError(2, "convention must be one of: " + ", ".join(tabs))
    fc, fv, fs, fh = tabs[convention]

    def groups(letters, f):
        g = {}
        for t in letters:
            g.setdefault(f(t), []).append(t)
        return {k: x for k, x in sorted(g.items()) if len(x) > 1}
    out = {"consonant letters": groups(CONS, fc), "vowel letters": groups(VOWEL_LETTERS, fv), "vowel signs": groups([x for x in SIGNS if x], fs), "chillu letters": groups(CHILLU, fh)}
    cross = {}
    for c in CONS:                                              # consonant + virama written as a bare consonant sound
        base = fc(c)[:-1] if fc(c).endswith("a") else fc(c)
        cross.setdefault(base, []).append(c + VIR)
    for c in CHILLU:
        cross.setdefault(fh(c), []).append(c)
    out["chillu vs consonant+virama"] = {k: x for k, x in sorted(cross.items()) if len(x) > 1 and any(t in CHILLU for t in x) and any(t.endswith(VIR) for t in x)}
    return out


# ---------------------------------------------------------------- Unicode block map
def unicode_block():
    """The assigned code points of U+0D00..0D7F partitioned into named groups; the groups sum to the assigned total."""
    groups = {}
    order = []

    def put(name, cp):
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(cp)
    for cp in range(0x0D00, 0x0D80):
        if not unicodedata.name(chr(cp), None):
            continue
        cat = unicodedata.category(chr(cp))
        if 0x0D05 <= cp <= 0x0D14 or cp in (0x0D60, 0x0D61):
            put("independent vowel letters", cp)
        elif 0x0D15 <= cp <= 0x0D39 and cp != 0x0D29:
            put("consonant letters (modern)", cp)
        elif cp in (0x0D29, 0x0D3A):
            put("consonant letters (historical)", cp)
        elif 0x0D7A <= cp <= 0x0D7F:
            put("chillu letters", cp)
        elif cat == "Lo":
            put("other letters (avagraha, dot reph, archaic/chillu-like)", cp)
        elif cat in ("Mc", "Mn") and (0x0D3E <= cp <= 0x0D4C or cp in (0x0D57, 0x0D62, 0x0D63)):
            put("dependent vowel signs", cp)
        elif cat in ("Mc", "Mn", "Me"):
            put("signs (candrabindu, anusvara, visarga, virama variants)", cp)
        elif cat == "Nd":
            put("digits", cp)
        elif cat == "No":
            put("numbers (fractions, quarters)", cp)
        else:
            put("symbols", cp)
    return {"unicode_version": unicodedata.unidata_version, "assigned": sum(len(x) for x in groups.values()),
            "groups": [{"name": n, "count": len(groups[n]), "codes": ["U+%04X" % c for c in groups[n]]} for n in order]}


# ---------------------------------------------------------------- measurements (no ears: only what the signal says)
def measure_features(feat_stdout):
    """From `s2w_features --out logmel` text -> brightness numbers of the loudest frames (a fricative's centre of gravity, a vowel's
    spectral tilt). Untuned engineering measurements, not a phonetic verdict."""
    centres = None
    mat = None
    lines = feat_stdout.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("mel_centers_hz="):
            centres = [float(x) for x in ln.split("=", 1)[1].split()]
        p = ln.split()
        if len(p) == 4 and p[0] == "matrix" and p[1] == "logmel_raw":
            mat = [[float(x) for x in lines[i + 1 + k].split()] for k in range(int(p[2]))]
    if not centres or not mat:
        return None
    lin = [[math.exp(max(-30.0, min(30.0, x))) for x in row] for row in mat]
    tot = [sum(r) for r in lin]
    thr = sorted(tot)[int(0.7 * (len(tot) - 1))]
    loud = [r for r, t in zip(lin, tot) if t >= thr and t > 0] or lin
    cent = hi = 0.0
    for r in loud:
        s = sum(r)
        cent += sum(c * x for c, x in zip(centres, r)) / s
        hi += sum(x for c, x in zip(centres, r) if c >= 4000) / s
    n = len(loud)
    return {"centroid_hz": round(cent / n), "high_ratio": round(hi / n, 3), "speech_frames": sum(1 for t in tot if t >= 0.2 * max(tot)) if tot else 0}


# untuned thresholds: two clips of look-alike letters count as "acoustically similar" when BOTH numbers are this close
SIM_CENTROID_HZ, SIM_HIGH_RATIO = 400, 0.08


def acoustically_similar(a, b):
    return abs(a["centroid_hz"] - b["centroid_hz"]) < SIM_CENTROID_HZ and abs(a["high_ratio"] - b["high_ratio"]) < SIM_HIGH_RATIO


# ---------------------------------------------------------------- the service used by the recorder
VERDICTS = ("ok", "wrong", "unsure")


def load_clip_lists(roots):
    """{NFC text: [clip, ...]} from every meta_scored.json / meta.json under the roots: ALL voices are kept (unlike the recorder's
    one-best-clip unit index). A clip's key is its folder + file so a verdict stays attached to one specific recording."""
    out = {}
    for root in ([roots] if isinstance(roots, str) else (roots or [])):
        if not root or not os.path.isdir(root):
            continue
        real = os.path.realpath(root)
        for dp, _dn, fn in os.walk(root):
            name = "meta_scored.json" if "meta_scored.json" in fn else "meta.json" if "meta.json" in fn else None
            if not name:
                continue
            try:
                with open(os.path.join(dp, name), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            for it in (meta.get("items") if isinstance(meta, dict) else None) or []:
                try:
                    path = os.path.realpath(os.path.join(dp, str(it["file"])))
                    if not path.startswith(real + os.sep) or not path.lower().endswith(".wav") or not os.path.isfile(path):
                        continue
                    key = nfc(str(it["word_ml"]))
                    folder = os.path.relpath(dp, root).replace(os.sep, "/")
                    out.setdefault(key, []).append({"path": path, "key": "%s|%s" % (os.path.basename(os.path.normpath(root)) + ("/" + folder if folder != "." else ""), it["file"]),
                                                    "voice": str(it.get("voice", ""))[:30], "model": str(it.get("model", ""))[:60], "synthetic": bool(it.get("synthetic", True)),
                                                    "heard": str(it["heard"])[:60] if it.get("heard") is not None else None, "exact": bool(it.get("exact"))})
                except (KeyError, TypeError):
                    continue
    return out


class Alphabet:
    def __init__(self, record_dir, audio_roots=None, lexicon_path=None, build="build", clusters_top=450):
        self.dir, self.build = record_dir, build
        self.items = build_inventory(lexicon_path, clusters_top)
        self.by_text = {i["text"]: i for i in self.items}
        self.clips = load_clip_lists(audio_roots)
        self.cache = {}
        self.review_file = os.path.join(record_dir, "alphabet_review.json")
        self.review = self._load_review()

    def _load_review(self):
        try:
            with open(self.review_file, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def overview(self):
        out = []
        for it in self.items:
            cl = self.clips.get(it["text"], [])
            rv = self.review.get(it["text"], {})
            out.append(dict(it, clips=[{"k": k, "voice": c["voice"], "model": c["model"], "heard": c["heard"], "exact": c["exact"], "synthetic": c["synthetic"],
                                       "verdict": (rv.get(c["key"]) or {}).get("verdict", "")} for k, c in enumerate(cl)]))
        n_audio = sum(1 for x in out if x["clips"])
        counts = {}
        for x in out:
            counts[x["group"]] = counts.get(x["group"], 0) + 1
        return {"items": out, "counts": counts, "total": len(out), "with_audio": n_audio, "neighbours": [n for n, _ in NEIGHBOURS],
                "note": "Audio is machine-generated and unverified. Only a listener can confirm a sound; measurements only compare clips."}

    def clip(self, text, k):
        cl = self.clips.get(nfc(text), [])
        if not (0 <= k < len(cl)):
            raise v.ViewerError(404, "no such clip")
        return cl[k]

    def detail(self, text, k):
        c = self.clip(text, k)
        mt = os.stat(c["path"]).st_mtime_ns
        ck = (c["path"], mt)
        if ck not in self.cache:
            samples, rate = v.load_wav(c["path"])
            m = None
            feat = v.run_tool(os.path.join(self.build, "s2w_features"), [c["path"], "--out", "logmel"])
            if feat.returncode == 0:
                m = measure_features(feat.stdout)
            self.cache[ck] = {"duration": round(len(samples) / rate, 3), "rate": rate, "peaks": v.peaks(samples, 240), "measure": m}
        d = dict(self.cache[ck])
        d["clip"] = {kk: c[kk] for kk in ("voice", "model", "heard", "exact", "synthetic", "key")}
        d["review"] = self.review.get(nfc(text), {}).get(c["key"])
        return d

    def detail_fast(self, text, k):
        """Waveform peaks only (no feature extraction): used for the small graphs in the grid."""
        c = self.clip(text, k)
        samples, rate = v.load_wav(c["path"])
        return {"duration": round(len(samples) / rate, 3), "peaks": v.peaks(samples, 64)}

    def group_report(self, text):
        """For one entry: its look-alike partners and, where both have audio, whether the first clips look acoustically alike."""
        it = self.by_text.get(nfc(text))
        if not it:
            raise v.ViewerError(404, "unknown entry")
        partners = list(it["same_manglish_as"])
        if it["consonant"]:
            partners += [x["text"] for x in self.items if x["group"] == "cv" and x["form"] == it["form"] and x["consonant"] != it["consonant"] and
                         any(it["consonant"] in letters and x["consonant"] in letters for _n, letters in NEIGHBOURS)]
        out = []
        mine = self.detail(it["text"], 0)["measure"] if self.clips.get(it["text"]) else None
        for p in dict.fromkeys(partners):
            pm = self.detail(p, 0)["measure"] if self.clips.get(p) else None
            out.append({"text": p, "label": self.by_text[p]["label"] if p in self.by_text else "", "measure": pm,
                        "similar": bool(mine and pm and acoustically_similar(mine, pm)) if mine and pm else None,
                        "same_manglish": p in it["same_manglish_as"]})
        return {"text": it["text"], "measure": mine, "partners": out, "thresholds": {"centroid_hz": SIM_CENTROID_HZ, "high_ratio": SIM_HIGH_RATIO, "note": "untuned"}}

    def set_review(self, text, key, verdict, note="", reviewer=""):
        t = nfc(text)
        if t not in self.by_text:
            raise v.ViewerError(404, "unknown entry")
        if verdict not in VERDICTS + ("",):
            raise v.ViewerError(422, "verdict must be ok, wrong or unsure")
        if not any(c["key"] == key for c in self.clips.get(t, [])):
            raise v.ViewerError(404, "unknown clip")
        if not isinstance(note, str) or len(note) > 200 or any(ord(c) < 32 for c in note):
            raise v.ViewerError(422, "note: up to 200 characters, no control characters")
        if not isinstance(reviewer, str) or not re.fullmatch(r"[A-Za-z0-9 _.\-]{0,40}", reviewer):
            raise v.ViewerError(422, "reviewer: up to 40 letters, digits, space . _ -")
        rv = self.review.setdefault(t, {})
        if verdict == "":
            rv.pop(key, None)
            if not rv:
                self.review.pop(t, None)
        else:
            rv[key] = {"verdict": verdict, "note": note, "reviewer": reviewer.strip(), "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.review_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.review, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.review_file)
        return self.review.get(t, {}).get(key)

    def export_csv(self):
        """alphabet.csv text: every entry with its labels and the number of human verdicts per kind."""
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["text", "codepoints", "group", "manglish", "label", "ipa", "description", "same_manglish_as", "clips", "verdict_ok", "verdict_wrong", "verdict_unsure"])
        for it in self.items:
            rv = self.review.get(it["text"], {})
            cnt = {k: sum(1 for x in rv.values() if x["verdict"] == k) for k in VERDICTS}
            w.writerow([it["text"], it["codes"], it["group"], it["manglish"], it["label"], it["ipa"], it["desc"], " ".join(it["same_manglish_as"]), len(self.clips.get(it["text"], [])),
                        cnt["ok"], cnt["wrong"], cnt["unsure"]])
        return buf.getvalue()


# ---------------------------------------------------------------- the page
PAGE = r"""<!doctype html><html lang="ml"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sound2Word Alphabet</title><style>__STYLE__
.al{display:grid;grid-template-columns:minmax(0,1fr) 400px;gap:16px;max-width:1500px;margin:0 auto;padding:16px 20px}@media(max-width:1000px){.al{grid-template-columns:1fr}}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.tab{padding:6px 14px;border:1px solid var(--line);border-radius:99px;cursor:pointer;background:var(--card)}.tab.sel{border-color:var(--blue);font-weight:600}
.grid{display:grid;grid-template-columns:90px repeat(16,minmax(64px,1fr));gap:4px;overflow-x:auto}.cell{border:1px solid var(--line);border-radius:8px;padding:4px;text-align:center;cursor:pointer;background:var(--card);min-width:64px;position:relative}
.cell:hover{border-color:var(--blue)}.cell.sel{outline:2px solid var(--blue)}.cell .g{font-size:22px;line-height:1.2}.cell .m{font-size:11px;color:var(--mut)}.cell canvas{height:18px;margin-top:2px;background:transparent}
.cell.look{background:#fff7e0}.cell.noaudio .g{opacity:.9}.dot{position:absolute;top:3px;right:4px;font-size:11px}.ok{color:#1e8e3e}.bad{color:#d93025}.unsure{color:#b06000}
.rowh{font-size:12px;color:var(--mut);display:grid;place-items:center}.list{display:flex;flex-wrap:wrap;gap:6px}.list .cell{width:78px}
#detail{position:sticky;top:8px;align-self:start;max-height:calc(100vh - 16px);overflow:auto}.big{font-size:64px;line-height:1.1}.chips{display:flex;gap:6px;flex-wrap:wrap}.chip2{padding:2px 10px;border:1px solid var(--line);border-radius:99px;cursor:pointer;background:var(--card)}
.clip{border-top:1px solid var(--line);padding:8px 0}.vb button{padding:4px 10px;font-size:13px;margin-right:4px}.vb .on{border-color:var(--blue);font-weight:700}.warn{color:#b06000}table.m td,table.m th{padding:3px 6px;font-size:13px}
input.s{font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);min-width:220px}
</style></head><body>
<header><h1>Sound2Word &mdash; Malayalam alphabet</h1><nav><a href="/">Record</a> &middot; <a href="/compare">Compare two words</a> &middot; <a href="/alphabet">Alphabet</a></nav>
<div class="banner"><b>Machine-generated audio, unverified.</b> Measurements only compare clips with each other. Whether a clip really sounds like its label can be confirmed only by a person listening: use &#10003; / &#10007; / ? on each clip. Not experiment data.</div></header>
<div class="al"><main style="padding:0;max-width:none;margin:0;min-width:0">
 <div class="card"><div id="summary" class="mut">Loading&hellip;</div>
  <div class="row"><input class="s" id="q" placeholder="search: ശ, sha, U+0D36, retroflex&hellip;" aria-label="Search"><label><input type="checkbox" id="onlyLook"> look-alikes only</label>
   <label><input type="checkbox" id="onlyNoAudio"> without audio only</label><a href="/api/alphabet/export.csv" download="alphabet_labels.csv">export labels + verdicts (CSV)</a></div>
  <div class="tabs" id="tabs"></div></div>
 <div class="card" id="body"></div>
 <details class="card"><summary><b>What the numbers mean</b> (Unicode check)</summary><div id="uni" class="mut"></div></details>
</main>
<aside class="card" id="detail"><div class="mut">Select a letter to see its sound, graph, measurements and look-alikes.</div></aside></div>
<script>
const TOKEN='__TOKEN__',$=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let D=null,tab='cv',cur=null,curK=0,audio=new Audio(),peakCache={},rv='';
try{rv=localStorage.getItem('s2w_reviewer')||''}catch(e){}
async function api(u,o){const r=await fetch(u,o);const d=await r.json().catch(()=>({error:'bad response'}));if(!r.ok||d.error)throw new Error(d.error||('HTTP '+r.status));return d}
const TABS=[['cv','Consonant × 16 forms'],['vowel','Vowels'],['chillu','Chillu'],['cluster','Conjuncts'],['look','Look-alikes (same Manglish)']];
async function init(){D=await api('/api/alphabet');const u=await api('/api/alphabet/unicode');
 const c=D.counts;$('summary').innerHTML=`<b>${D.total}</b> entries: ${Object.entries(c).map(([k,v])=>v+' '+({cv:'consonant forms',vowel:'vowel letters','vowel+sign':'vowel+sign',chillu:'chillu',cluster:'conjuncts'}[k]||k)).join(' · ')}. Audio for <b>${D.with_audio}</b>. Look-alikes: <b>${D.items.filter(i=>i.lookalike).length}</b>.`;
 $('uni').innerHTML=`<p>Unicode ${esc(u.unicode_version)}: <b>${u.assigned}</b> assigned code points in U+0D00&ndash;0D7F, split into groups that add up exactly. A code point is not a letter, and a written syllable is not a sound: this page counts <b>entries</b> by the definitions below.</p><table class="m">${u.groups.map(g=>`<tr><td>${g.count}</td><td>${esc(g.name)}</td></tr>`).join('')}</table>
 <p>Entries here: independent vowels, <b>consonant × 16 forms</b> (virama, inherent a, 12 vowel signs, anusvara, visarga), chillu letters, and conjunct clusters taken from a dictionary (not every consonant+virama+consonant pair is a real syllable). Look-alike = the same Manglish for a different letter.</p>`;
 $('tabs').innerHTML=TABS.map(([k,t])=>`<span class="tab${k===tab?' sel':''}" data-t="${k}">${t}</span>`).join('');
 document.querySelectorAll('.tab').forEach(e=>e.onclick=()=>{tab=e.dataset.t;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('sel',x.dataset.t===tab));render()});
 ['q','onlyLook','onlyNoAudio'].forEach(id=>$(id).oninput=render);
 const h=location.hash.slice(1);render();if(h)select(decodeURIComponent(h))}
function match(i){const q=$('q').value.trim().toLowerCase();if($('onlyLook').checked&&!i.lookalike)return false;if($('onlyNoAudio').checked&&i.clips.length)return false;
 if(!q)return true;return i.text.includes(q)||i.manglish.toLowerCase().includes(q)||i.label.toLowerCase().includes(q)||i.codes.toLowerCase().includes(q)||(i.desc||'').toLowerCase().includes(q)||i.ipa.includes(q)}
function dotOf(i){const v=i.clips.map(c=>c.verdict);return v.includes('wrong')?'<span class="dot bad">&#10007;</span>':v.includes('unsure')?'<span class="dot unsure">?</span>':v.includes('ok')?'<span class="dot ok">&#10003;</span>':''}
function cell(i){return `<div class="cell${i.lookalike?' look':''}${i.clips.length?'':' noaudio'}${i.text===cur?' sel':''}" data-x="${esc(i.text)}" title="${esc(i.label)} /${esc(i.ipa)}/ ${esc(i.codes)}">${dotOf(i)}<div class="g">${esc(i.text)}</div><div class="m">${esc(i.manglish)}</div>${i.clips.length?`<canvas data-p="${esc(i.text)}" width="64" height="18"></canvas>`:'<div class="m">no audio</div>'}</div>`}
function render(){const items=D.items.filter(match);let h='';
 if(tab==='cv'){const cons=[...new Set(items.filter(i=>i.group==='cv').map(i=>i.consonant))];
  h=`<div class="grid">${cons.map(c=>{const row=D.items.filter(i=>i.group==='cv'&&i.consonant===c);const first=row[1]||row[0];return `<div class="rowh"><div class="g" style="font-size:24px">${esc(c)}</div><div>${esc(first.label.split(' (')[0].replace(/a$/,''))}</div></div>`+row.map(i=>match(i)?cell(i):'<div></div>').join('')}).join('')}</div>`}
 else if(tab==='look'){const byM={};items.filter(i=>i.lookalike&&(i.group!=='cv'||i.form===''||'ീേൂോ'.includes(i.form))).forEach(i=>(byM[i.manglish]=byM[i.manglish]||[]).push(i));
  h=Object.entries(byM).map(([m,l])=>`<div style="margin-bottom:10px"><b>${esc(m)}</b> &mdash; ${l.length} different letters, same Manglish<div class="list">${l.map(cell).join('')}</div></div>`).join('')||'<div class="mut">None match.</div>'}
 else{const l=items.filter(i=>tab==='vowel'?(i.group==='vowel'||i.group==='vowel+sign'):i.group===tab);h=`<div class="list">${l.map(cell).join('')||'<div class="mut">'+(tab==='cluster'?'No conjunct list: start the recorder with --alphabet-lexicon FILE (a dictionary) to rank the real clusters.':'None match.')+'</div>'}</div>`}
 $('body').innerHTML=h;document.querySelectorAll('.cell').forEach(e=>e.onclick=()=>select(e.dataset.x));
 const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){io.unobserve(e.target);drawMini(e.target)}}),{rootMargin:'200px'});document.querySelectorAll('canvas[data-p]').forEach(c=>io.observe(c))}
async function peaksOf(t,k){const key=t+'|'+k;if(!peakCache[key])peakCache[key]=api('/api/alphabet/peaks?text='+encodeURIComponent(t)+'&k='+k).catch(()=>null);return peakCache[key]}
async function drawMini(c){const p=await peaksOf(c.dataset.p,0);if(!p)return;const g=c.getContext('2d');g.strokeStyle='#4285d8';const n=p.peaks.length;for(let x=0;x<c.width;x++){const[lo,hi]=p.peaks[Math.min(n-1,Math.floor(x/c.width*n))];g.beginPath();g.moveTo(x+.5,9-hi*9);g.lineTo(x+.5,9-lo*9);g.stroke()}}
function drawBig(c,p){const r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.width=r.width*d;c.height=70*d;const g=c.getContext('2d');g.scale(d,d);g.strokeStyle='#4285d8';const W=r.width,n=p.peaks.length;for(let x=0;x<W;x++){const[lo,hi]=p.peaks[Math.min(n-1,Math.floor(x/W*n))];g.beginPath();g.moveTo(x,35-hi*34);g.lineTo(x,35-lo*34);g.stroke()}}
function play(t,k,then){audio.pause();audio=new Audio('/api/alphabet/clip?text='+encodeURIComponent(t)+'&k='+k);if(then)audio.onended=then;audio.play()}
async function select(t){cur=t;history.replaceState(null,'','#'+encodeURIComponent(t));document.querySelectorAll('.cell').forEach(e=>e.classList.toggle('sel',e.dataset.x===t));
 const i=D.items.find(x=>x.text===t);if(!i){$('detail').textContent='Unknown entry';return}
 const same=i.same_manglish_as.map(x=>`<span class="chip2" data-go="${esc(x)}">${esc(x)}</span>`).join('');
 $('detail').innerHTML=`<div class="big">${esc(i.text)}</div><div><b>${esc(i.label)}</b> <span class="mut">/${esc(i.ipa)}/ · ${esc(i.codes)}</span></div><div class="mut">${esc(i.desc)}${i.rare?' (rare)':''}</div>
  ${same?`<p><b>Same Manglish, different letter:</b> <span class="chips">${same}</span></p>`:''}${i.neighbours.length?`<div class="mut">Easy to confuse: ${esc(i.neighbours.join('; '))}</div>`:''}
  <div id="clips">${i.clips.length?'':'<p class="mut">No audio for this entry yet. Generate it with the local TTS scripts on inventory.json (see tools/s2w_alphabet.py), then restart with --alphabet-audio-dir.</p>'}</div><div id="grp"></div>`;
 document.querySelectorAll('[data-go]').forEach(e=>e.onclick=()=>select(e.dataset.go));
 for(const c of i.clips){const d=await api('/api/alphabet/detail?text='+encodeURIComponent(t)+'&k='+c.k).catch(e=>null);if(cur!==t)return;
  const m=d&&d.measure,rvw=d&&d.review;
  $('clips').insertAdjacentHTML('beforeend',`<div class="clip" data-k="${c.k}"><div><button data-pl="${c.k}">&#9654; play</button> <b>${esc(c.voice||'voice')}</b> <span class="mut">${esc(c.model)}${c.synthetic?' · machine-generated':''}</span></div>
   <canvas class="wv" data-k="${c.k}" style="margin-top:4px" height="70"></canvas>
   <div class="mut">${d?d.duration+' s':''}${m?` · spectral centre ${m.centroid_hz} Hz · energy above 4 kHz ${(m.high_ratio*100).toFixed(0)}%`:''}${c.heard!=null?` · a recogniser heard &ldquo;${esc(c.heard)}&rdquo; ${c.exact?'(matches)':'(differs)'}`:''}</div>
   <div class="vb" data-v="${c.k}"><span class="mut">Does it sound like <b>${esc(i.label)}</b>? </span>${['ok','wrong','unsure'].map(v=>`<button data-vd="${v}" class="${rvw&&rvw.verdict===v?'on':''}">${{ok:'✓ yes',wrong:'✗ no',unsure:'? unsure'}[v]}</button>`).join('')}
   <input class="s" style="min-width:90px;width:90px" placeholder="you" data-rv="${c.k}" value="${esc(rvw?rvw.reviewer:rv)}" maxlength="40"> <input class="s" style="min-width:120px;width:150px" placeholder="note" data-nt="${c.k}" value="${esc(rvw?rvw.note:'')}" maxlength="200"></div></div>`);
  const cv=document.querySelector(`canvas.wv[data-k="${c.k}"]`);if(d)drawBig(cv,d);
  const row=document.querySelector(`.clip[data-k="${c.k}"]`);row.querySelector('[data-pl]').onclick=()=>play(t,c.k);
  row.querySelectorAll('[data-vd]').forEach(b=>b.onclick=async()=>{const verdict=b.classList.contains('on')?'':b.dataset.vd,who=row.querySelector('[data-rv]').value,note=row.querySelector('[data-nt]').value;
   try{await api('/api/alphabet/review',{method:'POST',headers:{'X-S2W-Token':TOKEN,'Content-Type':'application/json'},body:JSON.stringify({text:t,key:d.clip.key,verdict,note,reviewer:who})});try{localStorage.setItem('s2w_reviewer',who)}catch(e){}rv=who;
    row.querySelectorAll('[data-vd]').forEach(x=>x.classList.toggle('on',x.dataset.vd===verdict&&verdict!==''));D.items.find(x=>x.text===t).clips.find(x=>x.k===c.k).verdict=verdict;const ce=document.querySelector(`.cell[data-x="${CSS.escape(t)}"]`);if(ce){ce.querySelector('.dot')?.remove();ce.insertAdjacentHTML('afterbegin',dotOf(D.items.find(x=>x.text===t)))}}
   catch(e){alert('Could not save: '+e.message)}})}
 if(i.same_manglish_as.length||i.neighbours.length){const g=await api('/api/alphabet/group?text='+encodeURIComponent(t)).catch(e=>null);if(g&&cur===t&&g.partners.length){
  $('grp').innerHTML=`<h3 style="margin:10px 0 4px;font-size:14px">Look-alikes and neighbours</h3><table class="m"><tr><th></th><th>label</th><th>centre</th><th>&gt;4 kHz</th><th>vs this</th><th></th></tr>
   <tr><td><b>${esc(t)}</b></td><td>${esc(i.label)}</td><td>${g.measure?g.measure.centroid_hz:'–'}</td><td>${g.measure?(g.measure.high_ratio*100).toFixed(0)+'%':'–'}</td><td></td><td></td></tr>
   ${g.partners.map(p=>`<tr><td><span class="chip2" data-go="${esc(p.text)}">${esc(p.text)}</span></td><td>${esc(p.label)}${p.same_manglish?' <span class="mut">(same Manglish)</span>':''}</td><td>${p.measure?p.measure.centroid_hz:'–'}</td><td>${p.measure?(p.measure.high_ratio*100).toFixed(0)+'%':'–'}</td>
   <td>${p.similar===null?'<span class="mut">no audio</span>':p.similar?'<span class="warn">&#9888; acoustically similar</span>':'differs'}</td><td>${p.measure?`<button data-ab="${esc(p.text)}">A/B</button>`:''}</td></tr>`).join('')}</table>
   <div class="mut">&ldquo;Similar&rdquo; uses untuned limits (${g.thresholds.centroid_hz} Hz, ${(g.thresholds.high_ratio*100).toFixed(0)} points). It says the two clips are alike as signals, not what they say: a listener decides.</div>`;
  document.querySelectorAll('#grp [data-go]').forEach(e=>e.onclick=()=>select(e.dataset.go));document.querySelectorAll('[data-ab]').forEach(b=>b.onclick=()=>play(t,0,()=>setTimeout(()=>play(b.dataset.ab,0),350)))}}}
init().catch(e=>{$('summary').textContent='Cannot load: '+e.message});
</script></body></html>"""

# ---------------------------------------------------------------- CLI
def write_inventory(out_dir, items):
    os.makedirs(out_dir, exist_ok=True)
    kinds = {"vowel": "vowel", "vowel+sign": "vowel", "chillu": "chillu", "cluster": "cluster"}
    inv = [{"id": i, "text": it["text"], "kind": (su.kind(it["text"]) if it["group"] == "cv" else kinds.get(it["group"], "syllable")), "uses": it["uses"],
            "manglish": it["manglish"], "label": it["label"], "group": it["group"]} for i, it in enumerate(items)]
    with open(os.path.join(out_dir, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump(inv, f, ensure_ascii=False, indent=0)
    with open(os.path.join(out_dir, "alphabet.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "codepoints", "group", "manglish", "label", "ipa", "description", "same_manglish_as", "neighbour_sets", "rare", "uses"])
        for i, it in enumerate(items):
            w.writerow([i, it["text"], it["codes"], it["group"], it["manglish"], it["label"], it["ipa"], it["desc"], " ".join(it["same_manglish_as"]), "; ".join(it["neighbours"]),
                        "yes" if it["rare"] else "", it["uses"]])
    return len(inv)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("inventory", help="write alphabet.csv + inventory.json")
    a.add_argument("--out", required=True)
    a.add_argument("--lexicon")
    a.add_argument("--clusters-top", type=int, default=450)
    sub.add_parser("unicode", help="print the Unicode Malayalam block partition")
    lk = sub.add_parser("lookalikes", help="list the different letters that share one Manglish")
    lk.add_argument("--convention", choices=("common", "owner", "rule"), default="common")
    args = ap.parse_args()
    if args.cmd == "unicode":
        u = unicode_block()
        print("Unicode %s: %d assigned code points in U+0D00..U+0D7F" % (u["unicode_version"], u["assigned"]))
        for g in u["groups"]:
            print("  %3d  %s" % (g["count"], g["name"]))
        return 0
    if args.cmd == "lookalikes":
        rep = lookalike_report(args.convention)
        print("Different letters with the SAME Manglish (convention: %s)" % args.convention)
        for kind, gs in rep.items():
            print("\n%s: %d group(s)" % (kind, len(gs)))
            for k, x in gs.items():
                print("  %-6s %s" % (k, " ".join(x)))
        print("\nEvery consonant+vowel form of a group collides too (e.g. ശാ/ഷാ). Letters with DIFFERENT Manglish can still sound alike (ഷ/സ, ത/ട, ന/ങ):")
        for n, letters in NEIGHBOURS:
            print("  " + n)
        return 0
    try:
        items = build_inventory(args.lexicon, args.clusters_top)
        n = write_inventory(args.out, items)
    except (OSError, v.ViewerError) as e:
        print("error: %s" % (getattr(e, "message", None) or e), file=sys.stderr)
        return 2
    cnt = {}
    for it in items:
        cnt[it["group"]] = cnt.get(it["group"], 0) + 1
    look = sum(1 for it in items if it["lookalike"])
    print("wrote %d entries to %s" % (n, args.out))
    for g, c in cnt.items():
        print("  %4d  %s" % (c, {"vowel": "independent vowel letters", "vowel+sign": "vowel + anusvara/visarga", "cv": "consonant x 16 forms (virama, inherent a, 12 vowel signs, anusvara, visarga)",
                                  "chillu": "chillu letters", "cluster": "conjunct clusters from the dictionary (frequency-ranked)"}.get(g, g)))
    print("%d entries share their Manglish with a different letter (look-alikes); see the label column" % look)
    if not args.lexicon:
        print("note: no --lexicon, so no conjunct clusters (only a dictionary tells which clusters are real).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
