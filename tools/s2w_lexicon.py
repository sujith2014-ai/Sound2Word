#!/usr/bin/env python3
"""s2w_lexicon: merge several open Malayalam dictionary files into ONE local lexicon, adding pronunciation data.

  python3 tools/s2w_lexicon.py --out data/private/dictionary/lexicon.csv \\
      --source datuk.csv=Datuk:ODbL --source kurup.csv=Kurup:ODbL --source olam-enml.csv=Olam-ENML:ODbL

Each --source is FILE[=NAME[:LICENCE]]. Layouts are detected as in the recorder's dictionary sidebar (a mostly-Malayalam column
holds the words, an optional mostly-English column the meaning); override with --ml-col/--en-col for ALL sources, or give
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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_recorder as r  # noqa: E402
import s2w_viewer as v  # noqa: E402

COLUMNS = ["word_ml", "manglish", "sound_units", "ipa", "meaning_en", "meaning_ml", "sources", "licences", "g2p"]


def parse_source(spec):
    path, _, rest = spec.partition("=")
    name, _, lic = rest.partition(":")
    return path, (name or os.path.basename(path)), lic


def build(sources, out_path, ml_col=None, en_col=None, use_g2p=True, max_words=None):
    """sources: [(path, name, licence)] -> writes the merged CSV, returns a stats dict."""
    merged, per_source = {}, {}
    for path, name, lic in sources:
        dic = r.Dictionary(path, ml_col, en_col)
        n_words = 0
        for ml, meaning, tr, _lc, _pu in dic.entries:
            if not tr:                                              # phrases are not single words
                continue
            m = merged.setdefault(ml, {"en": [], "ml": [], "src": [], "lic": []})
            if meaning:
                bucket = m["ml"] if r.MAL.search(meaning) else m["en"]
                if meaning not in bucket:
                    bucket.append(meaning)
            if name not in m["src"]:
                m["src"].append(name)
            if lic and lic not in m["lic"]:
                m["lic"].append(lic)
            n_words += 1
        per_source[name] = n_words
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
        print("  %-20s %d single-word entries" % (n, c))
    if r.g2p("കലം") is None and not a.no_g2p:
        print("note: `mlphon` is not installed, so pronunciation came from the spelling rule (no vowel length). pip install mlphon for better units.")
    print("reminder: derived data - check each source's licence, keep it out of git, and treat every entry as an unverified suggestion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
