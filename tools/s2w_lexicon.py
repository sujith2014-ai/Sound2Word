#!/usr/bin/env python3
"""s2w_lexicon: merge several open Malayalam dictionary files into ONE local lexicon, adding pronunciation data.

  python3 tools/s2w_lexicon.py --out data/private/dictionary/lexicon.csv \\
      --source datuk.csv=Datuk:ODbL --source kurup.csv=Kurup:ODbL --source olam-enml.csv=Olam-ENML:ODbL

Each --source is FILE[=NAME[:LICENCE]]. Known Olam layouts are read directly: Datuk and enml (from_content/types/to_content TSV)
and E.K. Kurup (.yml synsets). Other CSV/TSV files use the recorder's detection (a mostly-Malayalam column holds the words, an
optional mostly-English column the meaning); override with --ml-col/--en-col for ALL sources, or give
each source its own layout by running the tool with one source and merging the outputs.

What it combines, per Malayalam word (the pieces each source is missing):
  meaning_en   English meanings from the English-Malayalam files            (joined with " ; ")
  meaning_ml   Malayalam definitions from the Malayalam-Malayalam file
  manglish     suggested ASCII spelling   |  sound_units  the sounds (K A L A M; aa/oo = long, tt = geminate)
  ipa          pronunciation from the optional `mlphon` package (pip install mlphon); otherwise a spelling rule is used
  sources, licences   where every row came from
The output is a DERIVED database: respect each source's licence (ODbL asks for attribution and share-alike if you redistribute
it). Keep it in a git-ignored folder (data/private/); it is not part of this repository. All entries are SUGGESTIONS: they say
nothing about whether a word is natural or familiar to speakers and are never "verified" until the two-reviewer review.
Load the result in the recorder with:  python3 tools/s2w_recorder.py --dictionary data/private/dictionary/lexicon.csv ...
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_recorder as r  # noqa: E402
import s2w_viewer as v  # noqa: E402

COLUMNS = ["word_ml", "manglish", "sound_units", "ipa", "meaning_en", "meaning_ml", "synonyms_ml", "pos", "sources", "licences", "g2p"]


_WORD = re.compile("[\u0d05-\u0d39][\u0d00-\u0d65\u0d70-\u0d7f\u200c\u200d]*")        # starts with a full letter; no digits, brackets or symbols


def clean_word(w):
    """True for a plain Malayalam word: not a fragment starting with a vowel sign/chillu/virama, and with no digits or stray symbols."""
    return bool(_WORD.fullmatch(w))


def _tokens(cell):
    """Single Malayalam words from a cell (several per cell are split at , ; / |; parenthesised notes and phrases are dropped)."""
    out = []
    for tok in re.split(r"[;,/|\n]+", re.sub(r"\([^)]*\)", " ", cell)):
        t = tok.strip(" .:-\u2013\u2014\"'")
        if t and r.MAL.search(t) and len(t) <= 60 and not re.search(r"\s", t):
            out.append(t)
    return out


def _flow_lists(text):
    """Parse a YAML flow sequence of sequences ([[a, b], ['c d']]) into a list of lists of strings (quotes honoured)."""
    groups, cur, buf, q, depth = [], None, [], None, 0
    for ch in text:
        if q:
            if ch == q:
                q = None
            else:
                buf.append(ch)
        elif ch in "'\"":
            q = ch
        elif ch == "[":
            depth += 1
            if depth == 2:
                cur = []
        elif ch == "]":
            if buf and cur is not None:
                cur.append("".join(buf).strip())
            buf = []
            if depth == 2 and cur is not None:
                groups.append([x for x in cur if x])
                cur = None
            depth -= 1
        elif ch == "," and depth == 2:
            cur.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    return groups


def _kurup_rows(text, head, pos):
    for g in _flow_lists(text):
        for w in g:
            for t in _tokens(w):
                yield t, head, pos, [x for x in g if x != w]


def read_kurup(path):
    """E.K. Kurup YAML corpus -> (ml_word, english_head, pos, malayalam_synonyms). The English head word is each Malayalam word's
    English meaning; the other Malayalam words in the same synset are its synonyms."""
    head, pos, collecting = None, "", None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if collecting is not None:
                collecting += " " + line.strip()
                if collecting.count("[") <= collecting.count("]"):
                    yield from _kurup_rows(collecting, head, pos)
                    collecting = None
                continue
            m = re.match(r"^- head:\s*(.*)$", line)
            if m:
                head = m.group(1).strip().strip("'\"")
                continue
            m = re.match(r"^\s+- pos:\s*(.*)$", line)
            if m:
                pos = m.group(1).strip()
                continue
            m = re.match(r"^\s+ml:\s*(\[.*)$", line)
            if m:
                collecting = m.group(1)
                if collecting.count("[") <= collecting.count("]"):
                    yield from _kurup_rows(collecting, head, pos)
                    collecting = None


def read_olam_tsv(path):
    """Olam Datuk (Malayalam, POS, Malayalam definition) and enml (English, POS, Malayalam meaning) dumps, tab separated with a
    `from_content  types  to_content` header. -> (ml_word, meaning, pos, synonyms); the meaning is Malayalam (Datuk) or English (enml)."""
    csv.field_size_limit(10 ** 7)
    with open(path, encoding="utf-8-sig", newline="") as f:
        rdr = csv.reader(f, delimiter="\t")
        first = next(rdr, None)
        if first is None or [c.strip().lower() for c in first[:3]] != ["from_content", "types", "to_content"]:
            raise v.ViewerError(2, "%s is not an Olam from_content/types/to_content dump" % os.path.basename(path))
        for row in rdr:
            if len(row) < 3:
                continue
            frm, pos, to = row[0].strip(), row[1].strip().strip("{}"), row[2].strip()
            if r.MAL.search(frm):                           # Datuk: Malayalam word -> Malayalam definition
                if to:
                    for t in _tokens(frm):
                        yield t, to, pos, []
            else:                                           # enml: English word -> Malayalam word(s)
                for t in _tokens(to):
                    yield t, frm, pos, []


def read_source(path, ml_col=None, en_col=None):
    """Yield (ml_word, meaning, pos, synonyms) from a known Olam layout, or from any CSV/TSV via the recorder's detection."""
    if os.path.splitext(path)[1].lower() in (".yml", ".yaml"):
        yield from read_kurup(path)
        return
    try:
        with open(path, encoding="utf-8-sig") as f:
            is_olam = f.readline().rstrip("\r\n").lower().replace(" ", "") == "from_content\ttypes\tto_content"
    except OSError as e:
        raise v.ViewerError(2, "cannot read %s: %s" % (path, e))
    if is_olam and ml_col is None and en_col is None:
        yield from read_olam_tsv(path)
        return
    for ml, meaning, tr, _lc, _pu in r.Dictionary(path, ml_col, en_col).entries:
        if tr:
            yield ml, meaning, "", []


def parse_source(spec):
    path, _, rest = spec.partition("=")
    name, _, lic = rest.partition(":")
    return path, (name or os.path.basename(path)), lic


def build(sources, out_path, ml_col=None, en_col=None, use_g2p=True, max_words=None):
    """sources: [(path, name, licence)] -> writes the merged CSV, returns a stats dict."""
    merged, per_source = {}, {}
    for path, name, lic in sources:
        n_words = 0
        for ml, meaning, pos, syn in read_source(path, ml_col, en_col):
            m = merged.setdefault(ml, {"en": [], "ml": [], "syn": [], "pos": [], "src": [], "lic": []})
            if meaning:
                bucket = m["ml"] if r.MAL.search(meaning) else m["en"]
                if meaning not in bucket and len(bucket) < 30:
                    bucket.append(meaning)
            for x in syn:
                if x != ml and x not in m["syn"] and len(m["syn"]) < 12:
                    m["syn"].append(x)
            if pos and pos != "-" and pos not in m["pos"]:
                m["pos"].append(pos)
            if name not in m["src"]:
                m["src"].append(name)
            if lic and lic not in m["lic"]:
                m["lic"].append(lic)
            n_words += 1
        per_source[name] = n_words
    merged = {w: m for w, m in merged.items() if clean_word(w)}
    if not merged:
        raise v.ViewerError(2, "no single-word Malayalam entries found in the given sources")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    stats = {"words": 0, "g2p": 0, "per_source": per_source, "multi_source": 0, "with_en_and_ml": 0}
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for ml in sorted(merged):
            if max_words and stats["words"] >= max_words:
                break
            m = merged[ml]
            g = r.g2p(ml) if use_g2p else None
            tr = r.ml_to_manglish(ml)
            if g:
                units, ipa, manglish = g[0], g[1], "".join(g[0])
            else:
                units, ipa, manglish = r.split_units(tr), "", tr
            if not manglish or not units:
                continue
            w.writerow([r._sanitise_cell(ml), manglish, " ".join(units), ipa, r._sanitise_cell(" ; ".join(m["en"])[:300]), r._sanitise_cell(" ; ".join(m["ml"])[:300]),
                        r._sanitise_cell(" ; ".join(m["syn"])[:300]), r._sanitise_cell(" ".join(m["pos"])[:60]),
                        " + ".join(m["src"]), " + ".join(m["lic"]), "yes" if g else "no"])
            stats["words"] += 1
            stats["g2p"] += 1 if g else 0
            stats["multi_source"] += 1 if len(m["src"]) > 1 else 0
            stats["with_en_and_ml"] += 1 if (m["en"] and m["ml"]) else 0
    os.replace(tmp, out_path)
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", required=True, help="FILE[=NAME[:LICENCE]] (repeatable)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ml-col")
    ap.add_argument("--en-col")
    ap.add_argument("--no-g2p", action="store_true", help="do not use mlphon even if installed (spelling rule only; faster, loses vowel length)")
    ap.add_argument("--max-words", type=int)
    a = ap.parse_args()
    try:
        st = build([parse_source(s) for s in a.source], a.out, a.ml_col, a.en_col, not a.no_g2p, a.max_words)
    except v.ViewerError as e:
        print("error: %s" % e.message, file=sys.stderr)
        return 2
    print("wrote %s: %d words (%d with pronunciation from mlphon, %d from more than one source, %d with both English and Malayalam meanings)"
          % (a.out, st["words"], st["g2p"], st["multi_source"], st["with_en_and_ml"]))
    for n, c in st["per_source"].items():
        print("  %-20s %d word-meaning rows" % (n, c))
    if r.g2p("കലം") is None and not a.no_g2p:
        print("note: `mlphon` is not installed, so pronunciation came from the spelling rule (no vowel length). pip install mlphon for better units.")
    print("reminder: derived data - check each source's licence, keep it out of git, and treat every entry as an unverified suggestion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
