#!/usr/bin/env python3
"""Read-only status of the three mandatory gates in CLAUDE.md, checked against the ACTUAL artifacts (never against what was said earlier).

  Gate A  vocabulary: two distinct reviewers approved all eight words (review workbook, exported word list, provenance, approvals file)
  Gate B  consent and recordings: consent register + eligibility (tools/consent_gate.py), dataset validation (build/s2w_dataset_check),
          real (not synthetic) data, every speaker consented
  Gate C  experiment integrity: frozen + committed settings (build/s2w_eval freeze) and a verifiable voice-search freeze file
          (tools/s2w_wordsearch.py freeze), locked test split untouched

Per CLAUDE.md rule 6 the gates are evaluated in order: if Gate A fails, B and C are reported as NOT EVALUATED. The tool never creates, edits or
approves anything and never invents evidence; a missing artifact is reported as MISSING with the exact path or flag that is needed.

  gate_status.py --master M.xlsx --wordlist W.csv [--expect-words 8] [--approvals F]
                 [--participants P --consent C --dataset DIR]
                 [--frozen-settings S.cfg] [--voice-freeze F.json --units D... --lexicon L --queries Q]
Exit codes: 0 all three gates pass, 1 at least one gate not passed, 2 usage error."""
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS, FAIL, MISSING = "PASS", "FAIL", "MISSING"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, timeout=300):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 2, str(e)


def result(name, state, detail):
    return {"check": name, "state": state, "detail": detail}


def need(path, flag, name):
    if not path:
        return result(name, MISSING, "%s was not given" % flag)
    if not os.path.exists(path):
        return result(name, MISSING, "%s does not exist" % path)
    return None


# ---------------------------------------------------------------- Gate A
def master_status(master, expect):
    r = need(master, "--master", "A1 review workbook: every word agreed by both reviewers")
    if r:
        return r
    name = "A1 review workbook: every word agreed by both reviewers"
    rc, out = run([sys.executable, os.path.join(HERE, "review_sheet.py"), "status", master])
    if rc != 0:
        return result(name, FAIL, "review_sheet.py status failed: %s" % out.strip()[:200])
    counts = {}
    for line in out.splitlines():
        m = re.match(r"^(.*\S)\s+(\d+)\s*$", line)
        if m:
            counts[m.group(1).strip()] = int(m.group(2))
    total = sum(counts.values())
    accepted = sum(n for k, n in counts.items() if k.startswith("Agreed: accept"))
    others = {k: n for k, n in counts.items() if not k.startswith("Agreed: accept") and k != "Empty slot"}
    if total == 0:
        return result(name, FAIL, "the workbook lists no words")
    if accepted == expect and not others:
        return result(name, PASS, "%d words 'Agreed: accept'" % accepted)
    return result(name, FAIL, "need exactly %d words 'Agreed: accept' and nothing else; found accepted=%d, others=%s" % (expect, accepted, others or "none"))


def wordlist_checks(wordlist, expect, master=None):
    out = []
    n1 = "A2 exported word list: verified, two distinct reviewers"
    r = need(wordlist, "--wordlist", n1)
    if r:
        return [r, result("A3 provenance matches the files", MISSING, "needs the word list")]
    with open(wordlist, encoding="utf-8", newline="") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].strip() != "#s2w-wordlist v1":
        return [result(n1, FAIL, "not a '#s2w-wordlist v1' file")]
    rows = list(csv.DictReader(lines[1:]))
    vocab = [r_ for r_ in rows if r_.get("role") == "vocab"]
    bad = [r_["word_id"] for r_ in rows if r_.get("review_status") != "verified"]
    r1 = {(r_.get("reviewer_1") or "").strip().casefold() for r_ in rows}
    r2 = {(r_.get("reviewer_2") or "").strip().casefold() for r_ in rows}
    problems = []
    if len(vocab) != expect:
        problems.append("%d vocabulary words, expected %d" % (len(vocab), expect))
    if bad:
        problems.append("not 'verified': %s" % ", ".join(bad[:5]))
    if len(r1) != 1 or len(r2) != 1 or "" in r1 or "" in r2:
        problems.append("reviewer ids missing or inconsistent across rows")
    elif r1 == r2:
        problems.append("the two reviewer ids are the same person (%s)" % next(iter(r1)))
    nfc_bad = [r_["word_id"] for r_ in rows if unicodedata.normalize("NFC", r_.get("label", "")) != r_.get("label", "")]
    if nfc_bad:
        problems.append("labels not in NFC form: %s" % ", ".join(nfc_bad[:5]))
    out.append(result(n1, FAIL if problems else PASS, "; ".join(problems) if problems else "%d verified words, reviewers %s and %s" % (len(vocab), next(iter(r1)), next(iter(r2)))))
    n2 = "A3 provenance matches the files"
    pp = os.path.splitext(wordlist)[0] + ".provenance.json"
    if not os.path.exists(pp):
        out.append(result(n2, MISSING, "%s does not exist (written by review_sheet.py export)" % pp))
        return out
    with open(pp, encoding="utf-8") as f:
        prov = json.load(f)
    p = []
    if prov.get("wordlist_sha256") != sha256_file(wordlist):
        p.append("word list hash differs from the provenance (edited after export)")
    if master and os.path.exists(master) and prov.get("master_sha256") != sha256_file(master):
        p.append("master workbook hash differs from the provenance (workbook changed after export)")
    if prov.get("n_vocab") != expect:
        p.append("provenance says n_vocab=%s, expected %d" % (prov.get("n_vocab"), expect))
    if (prov.get("reviewer_1") or "").strip().casefold() == (prov.get("reviewer_2") or "").strip().casefold():
        p.append("provenance reviewers are identical")
    out.append(result(n2, FAIL if p else PASS, "; ".join(p) if p else "hashes and reviewer ids agree"))
    return out


def approvals_check(gate, approvals, label):
    name = "%s approvals file (consent_gate.py gate %s)" % (label, gate)
    r = need(approvals, "--approvals", name)
    if r:
        return r
    rc, out = run([sys.executable, os.path.join(HERE, "consent_gate.py"), "gate", gate, "--approvals", approvals])
    return result(name, PASS if rc == 0 else FAIL, "; ".join(l.strip() for l in out.splitlines() if "[ ]" in l or "result=" in l)[:300])


def gate_a(a):
    checks = [master_status(a.master, a.expect_words)]
    checks += wordlist_checks(a.wordlist, a.expect_words, a.master)
    checks.append(approvals_check("A", a.approvals, "A4"))
    return checks


# ---------------------------------------------------------------- Gate B
def dataset_design_check(dataset):
    """Independent re-check of what EVALUATION.md says the C validator enforces only in part: every unknown (OOV) word must be spoken by a calib or test
    speaker and never enrolled, every vocabulary word must have enrolment and query recordings, and speakers never cross the enrol / calib / test splits."""
    n = "B4 dataset design: speaker-separated splits, OOV words queried but never enrolled"
    wl, rc = os.path.join(dataset, "wordlist.csv"), os.path.join(dataset, "recordings.csv")
    if not (os.path.exists(wl) and os.path.exists(rc)):
        return result(n, MISSING, "%s needs wordlist.csv and recordings.csv" % dataset)

    def rows(path):
        with open(path, encoding="utf-8", newline="") as f:
            lines = [l for l in f.read().splitlines() if not l.startswith("#")]
        return list(csv.DictReader(lines))
    words, recs = rows(wl), rows(rc)
    role = {w["word_id"]: w.get("role", "") for w in words}
    p = []
    by_split = {}
    for r in recs:
        by_split.setdefault(r["split"], []).append(r)
    spk_split = {}
    for r in recs:
        spk_split.setdefault(r["speaker_id"], set()).add(r["split"])
    crossing = sorted(s_ for s_, sp in spk_split.items() if len(sp) > 1)
    if crossing:
        p.append("speakers in more than one split: %s" % ", ".join(crossing[:5]))
    enrolled_oov = sorted({r["word_id"] for r in by_split.get("enrol", []) if role.get(r["word_id"]) == "oov"})
    if enrolled_oov:
        p.append("OOV words enrolled: %s" % ", ".join(enrolled_oov[:5]))
    queried = {r["word_id"] for sp in ("calib", "test") for r in by_split.get(sp, [])}
    never_queried_oov = sorted(w for w, ro in role.items() if ro == "oov" and w not in queried)
    if never_queried_oov:
        p.append("OOV words never spoken in calib/test: %s" % ", ".join(never_queried_oov[:5]))
    no_enrol = sorted(w for w, ro in role.items() if ro == "vocab" and w not in {r["word_id"] for r in by_split.get("enrol", [])})
    if no_enrol:
        p.append("vocabulary words with no enrolment recording: %s" % ", ".join(no_enrol[:5]))
    no_query = sorted(w for w, ro in role.items() if ro == "vocab" and w not in queried)
    if no_query:
        p.append("vocabulary words never queried in calib/test: %s" % ", ".join(no_query[:5]))
    if not any(ro == "oov" for ro in role.values()):
        p.append("the word list has no OOV words, so false-accept rate cannot be measured")
    return result(n, FAIL if p else PASS, "; ".join(p) if p else "splits are speaker-separated; every OOV word is queried and never enrolled")


def gate_b(a):
    checks = []
    n = "B1 consent register and eligibility (consent_gate.py check)"
    r = need(a.participants, "--participants", n) or need(a.consent, "--consent", n)
    if r:
        checks.append(r)
    else:
        rc, out = run([sys.executable, os.path.join(HERE, "consent_gate.py"), "check", "--participants", a.participants, "--consent", a.consent])
        checks.append(result(n, PASS if rc == 0 else FAIL, out.strip().splitlines()[-1][:300] if out.strip() else "no output"))
    n = "B2 dataset validation (s2w_dataset_check --require-verified --require-participants)"
    r = need(a.dataset, "--dataset", n)
    if r:
        checks.append(r)
    else:
        tool = os.path.join(ROOT, "build", "s2w_dataset_check")
        if not os.path.exists(tool):
            checks.append(result(n, MISSING, "%s is not built (run make all)" % tool))
        else:
            rc, out = run([tool, a.dataset, "--require-verified", "--require-participants"])
            kind = re.search(r"^kind=(\S+)", out, re.M)
            if rc != 0:
                checks.append(result(n, FAIL, "; ".join(l for l in out.splitlines() if "ERROR" in l or "result=" in l)[:300]))
            elif kind and kind.group(1) == "synthetic":
                checks.append(result(n, FAIL, "the dataset is SYNTHETIC (kind=synthetic): Gate B needs real consented recordings"))
            else:
                checks.append(result(n, PASS, [l for l in out.splitlines() if l.startswith("result=")][0]))
    checks.append(approvals_check("B", a.approvals, "B3"))
    checks.append(dataset_design_check(a.dataset) if a.dataset and os.path.isdir(a.dataset) else result("B4 dataset design: speaker-separated splits, OOV words queried but never enrolled", MISSING, "--dataset was not given"))
    return checks


# ---------------------------------------------------------------- Gate C
def frozen_settings_check(path):
    n = "C1 frozen, committed evaluation settings (s2w_eval freeze)"
    r = need(path, "--frozen-settings", n)
    if r:
        return r
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if "frozen=yes" not in lines:
        return result(n, FAIL, "the settings file is not frozen (no 'frozen=yes')")
    sha = [l.split("=", 1)[1] for l in lines if l.startswith("settings_sha256=")]
    if not sha:
        return result(n, FAIL, "no settings_sha256 line")
    body = "\n".join(lines[1:lines.index("frozen=yes")]) + "\n"
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != sha[0]:
        return result(n, FAIL, "settings_sha256 does not match the settings (file edited after freezing)")
    rc1, _o = run(["git", "ls-files", "--error-unmatch", os.path.abspath(path)])
    rc2, out2 = run(["git", "status", "--porcelain", "--", os.path.abspath(path)])
    if rc1 != 0:
        return result(n, FAIL, "the frozen settings are not committed to git (CLAUDE.md: freeze and COMMIT before the first run)")
    if out2.strip():
        return result(n, FAIL, "the frozen settings have uncommitted changes")
    return result(n, PASS, "frozen, hash verified, committed")


def voice_freeze_check(a):
    n = "C2 voice-search freeze file (verifiable inputs and settings)"
    r = need(a.voice_freeze, "--voice-freeze", n)
    if r:
        return r
    if not (a.units and a.lexicon and a.queries):
        return result(n, MISSING, "--units, --lexicon and --queries are needed to re-hash the inputs")
    import s2w_wordsearch as ws
    with open(a.voice_freeze, encoding="utf-8") as f:
        cfg = json.load(f)
    diffs = ws.verify_frozen(cfg, a.units, a.lexicon, a.queries)
    if diffs:
        return result(n, FAIL, "; ".join(diffs))
    if str(cfg.get("evidence", "")).startswith("SYNTHETIC"):
        return result(n, FAIL, "the freeze describes SYNTHETIC data only: it cannot complete Gate C for the real-speech experiment")
    return result(n, PASS, "intact; config_sha256 %s..." % cfg["config_sha256"][:12])


def gate_c(a):
    return [frozen_settings_check(a.frozen_settings), voice_freeze_check(a), approvals_check("C", a.approvals, "C3")]


# ---------------------------------------------------------------- driver
def evaluate_gates(a):
    gates = []
    blocked_by = None
    for key, title, fn in (("A", "vocabulary", gate_a), ("B", "consent and recordings", gate_b), ("C", "experiment integrity", gate_c)):
        if blocked_by:
            gates.append({"gate": key, "title": title, "state": "NOT EVALUATED", "reason": "blocked by Gate %s (CLAUDE.md rule 6: stop at the failing gate)" % blocked_by, "checks": []})
            continue
        checks = fn(a)
        ok = all(c["state"] == PASS for c in checks)
        gates.append({"gate": key, "title": title, "state": PASS if ok else "NOT PASSED", "checks": checks})
        if not ok:
            blocked_by = key
    return gates


def render(gates):
    lines = []
    for g in gates:
        lines.append("Gate %s - %s: %s" % (g["gate"], g["title"], g["state"]))
        if g.get("reason"):
            lines.append("  " + g["reason"])
        for c in g["checks"]:
            lines.append("  [%s] %s - %s" % ("x" if c["state"] == PASS else " ", c["check"], "%s: %s" % (c["state"], c["detail"])))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master")
    ap.add_argument("--wordlist")
    ap.add_argument("--expect-words", type=int, default=8)
    ap.add_argument("--approvals")
    ap.add_argument("--participants")
    ap.add_argument("--consent")
    ap.add_argument("--dataset")
    ap.add_argument("--frozen-settings")
    ap.add_argument("--voice-freeze")
    ap.add_argument("--units", nargs="+")
    ap.add_argument("--lexicon")
    ap.add_argument("--queries")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.expect_words < 1:
        print("error: --expect-words must be at least 1", file=sys.stderr)
        return 2
    gates = evaluate_gates(a)
    print(json.dumps(gates, ensure_ascii=False, indent=1) if a.json else render(gates))
    return 0 if all(g["state"] == PASS for g in gates) else 1


if __name__ == "__main__":
    sys.exit(main())
