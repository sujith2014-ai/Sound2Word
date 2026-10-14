#!/usr/bin/env python3
"""Before / after comparison of the voice-search evaluation on IDENTICAL made-up inputs (docs/DEFECT_AUDIT.md).
    git worktree add /tmp/before 3c493bf          # the code before the audit fixes
    python3 tools/audit_before_after.py --before-tools /tmp/before/tools [--out result.json]
Builds six small input sets (tones only: this validates program behaviour, never Malayalam recognition accuracy), runs `s2w_wordsearch.py eval` from
both code trees on each, and prints exit code, closed-dictionary top-1/top-5, per-condition counts and whether an unseen-speaker claim is valid."""
import argparse
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--before-tools", required=True, help="the tools/ folder of the code to compare against")
ap.add_argument("--out", default="audit_before_after.json")
ap.add_argument("--work", default=None, help="folder for the generated inputs (default: a temporary folder)")
ARGS = ap.parse_args()
import tempfile
AFTER, BEFORE = HERE, os.path.abspath(ARGS.before_tools)
import json, csv, shutil, subprocess
sys.path.insert(0, AFTER)
import numpy as np
import test_wordsearch as t, s2w_joiner as jn, s2w_units as su
R = ARGS.work or tempfile.mkdtemp(prefix="s2w_audit_"); shutil.rmtree(R, ignore_errors=True); os.makedirs(R)
SY = t.SYLL
def wav(path, x): open(path, "wb").write(jn.write_wav_bytes(x, 16000))
def units(name, items):
    d = os.path.join(R, name); os.makedirs(d)
    meta = []
    for i, (u, extra) in enumerate(items):
        wav(os.path.join(d, "u%d.wav" % i), t.tone(u, dur=0.22)); meta.append(dict({"word_ml": u, "file": "u%d.wav" % i}, **extra))
    json.dump({"items": meta}, open(os.path.join(d, "meta.json"), "w"), ensure_ascii=False); return d
words = [w for w in ["കമല","നരവ","തപക","ലമന","വരത","പടി","ടിപ","ക്കുമ","മങ്ങ","ശമ","ഷമ","സമ","ലപ","പത","തര","നവ","മരപ","വനത","രനമ","കതര","പവല"] if su.decompose(w) and all(u in SY for u in su.decompose(w))]
lex = os.path.join(R, "lexicon.csv")
with open(lex, "w", newline="") as f:
    w_ = csv.writer(f); w_.writerow(["word_ml"]); [w_.writerow([w]) for w in words]
def queries(name, spec):
    d = os.path.join(R, name); os.makedirs(d); meta = []
    for i, (w, spk, mode) in enumerate(spec):
        fn = "q%d.wav" % i; p = os.path.join(d, fn)
        if mode == "bad": open(p, "wb").write(b"not a wav")
        elif isinstance(mode, str) and mode.startswith("copy:"): shutil.copy(mode[5:], p)
        else:
            parts = [t.tone(u, dur=0.11, noise=0.03, seed=i + k) for k, u in enumerate(su.decompose(w) or [])]
            wav(p, jn.join(parts, 16000)[0])
        it = {"word_ml": w, "file": fn}
        if spk: it["speaker"] = spk
        meta.append(it)
    json.dump({"items": meta}, open(os.path.join(d, "meta.json"), "w"), ensure_ascii=False); return d
synth = [(u, {"synthetic": True, "voice": "made-up tones"}) for u in SY]
U_ok = units("units_ok", synth)
U_person = units("units_person", synth + [("ക", {"speaker": "P1"})])
U_unattr = units("units_unattributed", [(u, {}) for u in SY])
closed = [(w, "S1", None) for w in words]
cases = {
 "C1 closed dictionary, unseen speaker, clean metadata": (U_ok, queries("q1", closed)),
 "C2 same + 3 out-of-dictionary queries": (U_ok, queries("q2", closed + [("ഗുരു", "S1", None), ("അങ്ങല", "S1", None), ("ക്കു", "S1", None)])),
 "C3 query speaker ' p1 ' vs reference speaker 'P1'": (U_person, queries("q3", [(w, " p1 ", None) for w in words[:6]])),
 "C4 reference clips with no speaker and no synthetic mark": (U_unattr, queries("q4", closed)),
 "C5 one unreadable query file in 21": (U_ok, queries("q5", closed[:20] + [("നവ", "S1", "bad")])),
 "C6 query is a byte copy of a reference clip (no speaker metadata on either)": (units("units_nospk", [(u, {}) for u in SY]), None),
}
d6 = cases["C6 query is a byte copy of a reference clip (no speaker metadata on either)"][0]
cases["C6 query is a byte copy of a reference clip (no speaker metadata on either)"] = (d6, queries("q6", [("ക", "S9", "copy:" + os.path.join(d6, "u0.wav"))]))
def run(tools, U, Q):
    p = subprocess.run([sys.executable, os.path.join(tools, "s2w_wordsearch.py"), "eval", "--queries", Q, "--units", U, "--lexicon", lex], capture_output=True, text=True)
    out = {"exit": p.returncode, "stderr": p.stderr.strip()[:160]}
    if p.returncode == 0:
        j = json.loads(p.stdout); s = j["summary"]
        out["composed"] = {k: s["composed"][k] for k in ("n", "top1", "top5", "not_found")}
        out["conditions"] = {k: v["n"] for k, v in s["by_condition"].items()}
        out["unseen_claim_valid"] = s.get("unseen_speaker_claim_valid", "n/a (not reported)")
        out["recall_n"] = s.get("shortlist_recall", {}).get("n")
        out["ms_per_query"] = s["composed"].get("mean_ms")
    return out
res = {}
for name, (U, Q) in cases.items():
    res[name] = {"before": run(BEFORE, U, Q), "after": run(AFTER, U, Q)}
json.dump(res, open(ARGS.out, "w"), ensure_ascii=False, indent=1)
for n, r in res.items():
    print(n); print("  before:", json.dumps(r["before"], ensure_ascii=False)); print("  after: ", json.dumps(r["after"], ensure_ascii=False))
