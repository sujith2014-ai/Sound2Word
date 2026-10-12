#!/usr/bin/env python3
"""Tests for pilot_plan.py. Uses only synthetic ids and the synthetic fixture word list (SYNTH_*); no Malayalam
vocabulary, no real participants and no real data. Verifies the spec/plan machinery, consent gating, the two
protocol views, and compatibility with the dataset tools."""
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consent_gate as cg  # noqa: E402
import pilot_plan as pp  # noqa: E402

BUILD = os.environ.get("S2W_BUILD", "build")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SPEC_DIR = os.path.join(ROOT, "data", "schema")
HAVE_C = all(os.path.exists(os.path.join(BUILD, n)) for n in ("s2w_ingest", "s2w_dataset_check", "s2w_make_fixture", "s2w_eval"))
TODAY = "2001-06-01"
NOCONSENT = ["--synthetic-no-consent-check"]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            pp.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                err.write(e.code)
    return code, out.getvalue(), err.getvalue()


def read(path, skip_tag=True):
    with open(path, encoding="utf-8", newline="") as f:
        if skip_tag:
            f.readline()
        return list(csv.DictReader(f))


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.c = os.path.join(self.d, "pilot_conditions.csv")
        self.m = os.path.join(self.d, "pilot_matrix.csv")
        shutil.copy(os.path.join(SPEC_DIR, "pilot_conditions.csv"), self.c)
        shutil.copy(os.path.join(SPEC_DIR, "pilot_matrix.csv"), self.m)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def edit(self, path, find, repl, count=1):
        with open(path, encoding="utf-8") as f:
            t = f.read()
        self.assertIn(find, t)
        with open(path, "w", encoding="utf-8") as f:
            f.write(t.replace(find, repl, count))

    def set_field(self, cond, field, value):
        rows = read(self.c)
        for r in rows:
            if r["condition_id"] == cond:
                r[field] = value
        with open(self.c, "w", encoding="utf-8", newline="") as f:
            f.write(pp.COND_TAG + "\n")
            w = csv.DictWriter(f, fieldnames=pp.COND_COLS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    def drop(self, path, needle):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines(True)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(ln for ln in lines if needle not in ln)

    def errors(self):
        return run("check", "--conditions", self.c, "--matrix", self.m)


class Spec(Base):
    def test_committed_spec_is_current_and_valid(self):
        fresh = os.path.join(self.d, "fresh")
        self.assertEqual(run("write-spec", fresh)[0], 0)
        for n in ("pilot_conditions.csv", "pilot_matrix.csv"):
            with open(os.path.join(fresh, n), encoding="utf-8") as a, open(os.path.join(SPEC_DIR, n), encoding="utf-8") as b:
                self.assertEqual(a.read(), b.read(), "regenerate with: tools/pilot_plan.py write-spec")
        self.assertNotEqual(run("write-spec", fresh)[0], 0)
        code, out, err = self.errors()
        self.assertEqual(code, 0, err)
        self.assertIn("result=VALID", out)

    def test_locked_decisions_are_encoded(self):
        by = {r["condition_id"]: r for r in read(self.c)}
        self.assertEqual((by["C0"]["enabled"], by["C0"]["optional"]), ("yes", "no"))                  # clean baseline is mandatory
        self.assertEqual((by["C0-R"]["enabled"], by["C0-R"]["optional"]), ("yes", "no"))
        self.assertEqual((by["C0-DEVB"]["enabled"], by["C0-DEVB"]["requires"]), ("no", "second_device"))   # second device deferred
        for c in ("N-FAN", "N-STR", "N-BAB"):
            self.assertEqual((by[c]["optional"], by[c]["requires"]), ("yes", "loudspeaker+spl_meter"))      # noise equipment can wait
        for c in ("D-100", "R-HARD"):
            self.assertEqual(by[c]["optional"], "yes")
        self.assertNotIn("dBA at the head 6", by["N-FAN"]["recipe"])
        self.assertIn("Do not specify or claim a level without a measurement", by["N-FAN"]["recipe"])

    def test_matrix_covers_required_categories(self):
        cats = {r["category"] for r in read(self.m)}
        for need in ("baseline", "clean_enrol_loss", "noise_only_matched", "noise_only_mismatched", "mixed_matched", "mixed_mismatched",
                     "noise_only_clean_cost", "mixed_clean_cost", "mixed_all", "session_repeat", "device_mismatch"):
            self.assertIn(need, cats)
        conds = {r["condition_id"] for r in read(self.c)}
        for q in conds - {"C0"}:
            self.assertTrue(any(r["enrol_conditions"] == "C0" and r["query_condition"] == q for r in read(self.m)), q)

    def test_conditions_map_onto_dataset_fields(self):
        for r in read(self.c):
            self.assertTrue(pp.ID_RE.match(r["condition_id"]) and pp.ID_RE.match(r["device_id"]))
            self.assertIn(r["environment"], ("clean", "noisy"))
            self.assertEqual(bool(r["noise_condition"]), r["environment"] == "noisy")
            self.assertFalse(r["noise_condition"].startswith("sim:"))
            if r["environment"] == "noisy":
                self.assertTrue(r["noise_condition"].startswith("real:"))
            self.assertEqual(r["qa_limits"], r["environment"])

    def test_invalid_specs_are_rejected(self):
        field_cases = [
            ("sim prefix", "N-FAN", "noise_condition", "sim:fan-60dBA", "reserved for simulated"),
            ("not real prefix", "N-STR", "noise_condition", "street-65dBA", "must start with 'real:'"),
            ("clean with noise", "C0", "noise_condition", "real:x", "clean conditions must have an empty"),
            ("noisy without noise", "N-FAN", "noise_condition", "", "need a noise_condition"),
            ("bad env", "N-FAN", "environment", "loud", "environment must be"),
            ("qa mismatch", "N-STR", "qa_limits", "clean", "qa_limits"),
            ("bad session", "N-FAN", "session", "S0", "session must be"),
            ("bad reps", "N-FAN", "repetitions", "99", "repetitions must be"),
            ("bad role", "N-FAN", "role", "weird", "role must be"),
            ("bad id", "N-BAB", "condition_id", "N BAB", "invalid id"),
            ("comma in noise", "N-FAN", "noise_condition", "real:fan,60", "noise_condition must be"),
            ("bad enabled", "N-FAN", "enabled", "maybe", "enabled must be"),
            ("bad requires", "N-FAN", "requires", "magic", "requires must be"),
            ("mandatory disabled", "C0-R", "enabled", "no", "cannot be disabled"),
            ("second device enabled", "C0-DEVB", "enabled", "yes", "cannot be enabled until gap G2"),
        ]
        for name, cond, field, value, expect in field_cases:
            self.tearDown(); self.setUp()
            self.set_field(cond, field, value)
            code, out, err = self.errors()
            self.assertEqual(code, 1, name)
            self.assertIn(expect, err, name)
        matrix_cases = [
            ("unknown condition in matrix", "EM-FAN__N-STR,EM-FAN,C0+N-FAN,N-STR", "EM-FAN__N-STR,EM-FAN,C0+N-FAN,N-XXX", "unknown condition"),
            ("wrong category", "E0__N-FAN,E0,C0,N-FAN,clean_enrol_loss", "E0__N-FAN,E0,C0,N-FAN,baseline", "category"),
            ("device as enrolment", "EM-FAN__N-STR,EM-FAN,C0+N-FAN,N-STR", "EM-FAN__N-STR,EM-FAN,C0+C0-DEVB,N-STR", "device-only"),
            ("bad matrix tag", "#s2w-pilot-matrix v1", "#s2w-pilot-matrix v9", "expected first line"),
        ]
        for name, find, repl, expect in matrix_cases:
            self.tearDown(); self.setUp()
            self.edit(self.m, find, repl)
            code, out, err = self.errors()
            self.assertEqual(code, 1, name)
            self.assertIn(expect, err, name)
        self.tearDown(); self.setUp()
        self.edit(self.c, "#s2w-pilot-conditions v1", "#nope")
        self.assertIn("expected first line", self.errors()[2])

    def test_missing_required_cells_are_rejected(self):
        self.drop(self.m, ",baseline")
        self.assertIn("baseline cell", self.errors()[2])
        self.tearDown(); self.setUp()
        self.drop(self.m, "noise_only_mismatched")
        self.assertIn("mismatched pairs and clean-query cost must be measured", self.errors()[2])
        self.tearDown(); self.setUp()
        self.drop(self.m, "mixed_clean_cost")
        self.assertIn("mixed_clean_cost", self.errors()[2])
        self.tearDown(); self.setUp()
        self.drop(self.m, "E0__D-100")
        self.assertIn("no clean-enrolment cell for query condition D-100", self.errors()[2])
        self.tearDown(); self.setUp()
        self.drop(self.c, "C0,clean,")
        self.assertIn("C0 (clean baseline) is required", self.errors()[2])


@unittest.skipUnless(HAVE_C, "C tools not built")
class DatasetToolCompatibility(Base):
    def test_every_condition_is_accepted_by_ingest_and_validator(self):
        fx = os.path.join(self.d, "fx")
        subprocess.run([os.path.join(BUILD, "s2w_make_fixture"), fx], check=True, stdout=subprocess.DEVNULL)
        ds = os.path.join(self.d, "ds")
        os.mkdir(ds)
        for n in ("dataset_meta.txt", "wordlist.csv", "participants.csv"):
            shutil.copy(os.path.join(fx, n), os.path.join(ds, n))
        wavs = sorted(f for f in os.listdir(os.path.join(fx, "audio")) if f.startswith("spkE1_"))
        n = 0
        for r in read(self.c):                                                       # every approved condition label, enabled or not
            n += 1
            src = os.path.join(fx, "audio", wavs[n])
            r_ = subprocess.run([os.path.join(BUILD, "s2w_ingest"), "--in", src, "--dataset-dir", ds, "--recording-id", "rec%02d" % n, "--word-id", "SYNTH_V1",
                                 "--speaker-id", "spkE1", "--session-id", "spkE1-%s" % r["session"], "--repetition", str(n), "--split", "enrol", "--device-id", r["device_id"],
                                 "--environment", r["environment"], "--noise-condition", r["noise_condition"], "--append-manifest",
                                 "--created-utc", "2000-01-01T00:00:00Z"], capture_output=True, text=True)
            self.assertEqual(r_.returncode, 0, "%s: %s" % (r["condition_id"], r_.stderr))
        c = subprocess.run([os.path.join(BUILD, "s2w_dataset_check"), ds], capture_output=True, text=True)
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("result=VALID", c.stdout)


def make_registers(d, codes, eligible=None, tag=""):
    """synthetic participant/consent registers for `codes`; `eligible` (subset) get completed consent, the rest stay pending/screened"""
    eligible = set(codes if eligible is None else eligible)
    prow, crow = [], []
    for c in codes:
        ok = c in eligible
        prow.append(dict(participant_code=c, identity_record_ref="vault-" + c[-3:], recruitment_source="open_call", independent_recruitment_confirmed="yes",
                         reads_vocabulary_comfortably="yes", status="eligible" if ok else "screened", status_date="2000-01-02"))
        crow.append(dict(participant_code=c, consent_version="TEST-v1" if ok else "", information_sheet_version="TEST-v1" if ok else "", consent_date="2000-01-02" if ok else "",
                         recording_and_analysis="yes", retention_until="2002-01-02" if ok else "", future_experiments="no", public_release="no", group_reporting_ok="no",
                         optional_attributes_ok="no", status="consented" if ok else "pending", withdrawal_date="", withdrawal_action=""))
    pp_, cp = os.path.join(d, "participants_reg%s.csv" % tag), os.path.join(d, "consent_reg%s.csv" % tag)
    for path, tag, cols, rows in ((pp_, cg.PART_TAG, cg.PART_COLS, prow), (cp, cg.CONSENT_TAG, cg.CONSENT_COLS, crow)):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(tag + "\n")
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return pp_, cp


class Plan(Base):
    _n = 0

    def speakers(self, n=16, strata=("A", "B")):
        p = os.path.join(self.d, "speakers.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("speaker_id,stratum\n")
            for i in range(n):
                f.write("SPK-%03d,%s\n" % (i + 1, strata[i % len(strata)] if strata else ""))
        return p

    def plan(self, seed=7, n=16, splits="8,4,4", strata=("A", "B"), extra=("--with", "all"), consent=None):
        Plan._n += 1
        out = os.path.join(self.d, "alloc_%d_%s_%d.csv" % (Plan._n, seed, n))
        cons = consent or NOCONSENT
        code, o, e = run("plan", "--speakers", self.speakers(n, strata), "--conditions", self.c, "--matrix", self.m, "--splits", splits, "--seed", str(seed), "--out", out, *extra, *cons)
        self.assertEqual(code, 0, e)
        return out, read(out, skip_tag=False)

    def test_default_plan_is_the_single_device_clean_pilot(self):
        out, rows = self.plan(extra=())
        for r in rows:
            self.assertEqual((r["s1_blocks"], r["s2_blocks"]), ("C0", "C0-R"))               # no noise/distance/room/second device unless switched on
        self.assertEqual(self.plan(extra=("--with", "N-FAN"))[1][0]["s2_blocks"].split("+")[0], "C0-R")
        self.assertIn("N-FAN", self.plan(extra=("--with", "N-FAN"))[1][0]["s2_blocks"])
        self.assertNotIn("D-100", self.plan(extra=("--with", "N-FAN"))[1][0]["s2_blocks"])
        for bad in ("C0-DEVB", "nonsense", "C0"):                                           # disabled / unknown / mandatory conditions cannot be 'switched on'
            self.assertNotEqual(run("plan", "--speakers", self.speakers(), "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "1",
                                    "--out", os.path.join(self.d, "z_%s.csv" % bad), "--with", bad, *NOCONSENT)[0], 0, bad)

    def test_allocation(self):
        out, rows = self.plan()
        by = {}
        for r in rows:
            by.setdefault(r["split"], []).append(r)
        self.assertEqual({k: len(v) for k, v in by.items()}, {"enrol": 8, "calib": 4, "test": 4})
        for split, members in by.items():
            a = sum(1 for r in members if r["stratum"] == "A")
            self.assertLessEqual(abs(a - len(members) / 2), 1, split)
        self.assertEqual(len({r["speaker_id"] for r in rows}), 16)
        _, again = self.plan()
        self.assertEqual(rows, again)
        _, other = self.plan(seed=8)
        self.assertNotEqual([r["split"] for r in rows], [r["split"] for r in other])
        self.assertGreaterEqual(len({r["s2_blocks"] for r in rows}), 5)
        for r in rows:
            blocks = r["s2_blocks"].split("+")
            self.assertEqual(blocks[0], "C0-R")
            self.assertEqual(sorted(blocks[1:]), ["D-100", "N-BAB", "N-FAN", "N-STR", "R-HARD"])
            self.assertEqual(r["s1_blocks"], "C0")
            # personalised track: the locked-test participants are exactly the speaker-independent test speakers
            self.assertEqual(r["personalised_group"], "test_participant" if r["split"] == "test" else "calibration_participant")
            self.assertEqual((r["personalised_enrol_session"], r["personalised_query_session"]), ("S1", "S2"))

    def test_consent_is_required_for_allocation_and_prompts(self):
        sp = self.speakers()
        out = os.path.join(self.d, "needs_consent.csv")
        code, _, err = run("plan", "--speakers", sp, "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "1", "--out", out)
        self.assertNotEqual(code, 0)
        self.assertIn("--consent-register", err)
        self.assertFalse(os.path.exists(out))
        codes = ["SPK-%03d" % (i + 1) for i in range(16)]
        pr, cr = make_registers(self.d, codes)
        ok = ["plan", "--speakers", sp, "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "1", "--consent-register", cr, "--participants-register", pr, "--today", TODAY]
        self.assertEqual(run(*ok, "--out", os.path.join(self.d, "ok.csv"))[0], 0)
        # one participant without completed consent blocks the whole allocation
        pr2, cr2 = make_registers(self.d, codes, eligible=codes[:-1], tag="_partial")
        code, _, err = run("plan", "--speakers", sp, "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "1", "--consent-register", cr2, "--participants-register", pr2,
                           "--today", TODAY, "--out", os.path.join(self.d, "blocked.csv"))
        self.assertNotEqual(code, 0)
        self.assertIn("SPK-016", err)
        self.assertFalse(os.path.exists(os.path.join(self.d, "blocked.csv")))
        # expired consent also blocks
        code, _, err = run(*ok[:-1], "2003-01-01", "--out", os.path.join(self.d, "expired.csv"))
        self.assertNotEqual(code, 0)
        # prompts: the same gate per participant
        alloc = os.path.join(self.d, "ok.csv")
        wl = self.wordlist()
        base = ["prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", alloc, "--speaker", "SPK-016", "--today", TODAY]
        self.assertNotEqual(run(*base, "--out", os.path.join(self.d, "p0.csv"))[0], 0)
        self.assertNotEqual(run(*base, "--consent-register", cr2, "--participants-register", pr2, "--out", os.path.join(self.d, "p1.csv"))[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "p1.csv")))
        self.assertEqual(run(*base, "--consent-register", cr, "--participants-register", pr, "--out", os.path.join(self.d, "p2.csv"))[0], 0)

    def test_allocation_errors(self):
        sp = self.speakers()
        for i, (splits, name) in enumerate((("8,4,3", "sizes"), ("8,4", "three"))):
            self.assertNotEqual(run("plan", "--speakers", sp, "--conditions", self.c, "--splits", splits, "--seed", "1", "--out", os.path.join(self.d, "e%d.csv" % i), *NOCONSENT)[0], 0, name)
        dup = os.path.join(self.d, "dup.csv")
        with open(dup, "w") as f:
            f.write("speaker_id\nSPK-001\nSPK-001\n")
        self.assertNotEqual(run("plan", "--speakers", dup, "--conditions", self.c, "--splits", "1,1,1", "--seed", "1", "--out", os.path.join(self.d, "d.csv"), *NOCONSENT)[0], 0)
        bad = os.path.join(self.d, "bad.csv")
        with open(bad, "w") as f:
            f.write("speaker_id\nname with space\nSPK-002\nSPK-003\n")
        self.assertNotEqual(run("plan", "--speakers", bad, "--conditions", self.c, "--splits", "1,1,1", "--seed", "1", "--out", os.path.join(self.d, "b.csv"), *NOCONSENT)[0], 0)
        out, _ = self.plan()
        self.assertNotEqual(run("plan", "--speakers", sp, "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "7", "--out", out, *NOCONSENT)[0], 0)
        self.edit(self.m, "#s2w-pilot-matrix v1", "#nope")
        self.assertNotEqual(run("plan", "--speakers", sp, "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "9", "--out", os.path.join(self.d, "f.csv"), *NOCONSENT)[0], 0)

    def wordlist(self, verified=True):
        p = os.path.join(self.d, "wordlist.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("#s2w-wordlist v1\nword_id,label,romanisation,meaning_en,role,pair_id,contrast_type,review_status,reviewer_1,reviewer_2,variants\n")
            for i in range(1, 7):
                f.write("SYNTH_V%d,PLACEHOLDER_%d,,m,vocab,,none,%s,a,b,\n" % (i, i, "verified" if verified or i > 1 else "draft"))
            for i in range(1, 3):
                f.write("SYNTH_O%d,PLACEHOLDER_O%d,,m,oov,,none,verified,a,b,\n" % (i, i))
        return p

    def test_prompts(self):
        out, rows = self.plan()
        wl = self.wordlist()
        pr = os.path.join(self.d, "prompts.csv")
        code, o, e = run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SPK-003", "--out", pr, *NOCONSENT)
        self.assertEqual(code, 0, e)
        p = read(pr, skip_tag=False)
        self.assertTrue(all(r["label"] == "" for r in p))
        conds = {r["condition_id"]: r for r in read(self.c)}
        by_block = {}
        for r in p:
            by_block.setdefault((r["session_id_suffix"], r["condition_id"]), []).append(r)
        self.assertEqual(set(k[1] for k in by_block), {"C0", "C0-R", "N-FAN", "N-STR", "N-BAB", "D-100", "R-HARD"})
        for (sess, cid), rs in by_block.items():
            words = 6 + (2 if conds[cid]["include_oov"] == "yes" else 0)
            self.assertEqual(len(rs), words * int(conds[cid]["repetitions"]), cid)
            for rnd in range(1, int(conds[cid]["repetitions"]) + 1):
                self.assertEqual(sorted(r["word_id"] for r in rs if int(r["round"]) == rnd), sorted(["SYNTH_V%d" % i for i in range(1, 7)] + ["SYNTH_O1", "SYNTH_O2"]))
            seq = [r["word_id"] for r in rs]
            self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])))
        keys = [(r["speaker_id"], r["session_id_suffix"], r["word_id"], r["repetition"]) for r in p]
        self.assertEqual(len(keys), len(set(keys)))
        ids = [r["planned_recording_id"] for r in p]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(pp.ID_RE.match(i) for i in ids))
        c0 = [int(r["repetition"]) for r in p if r["condition_id"] == "C0"]
        self.assertEqual((min(c0), max(c0)), (1, 3))
        self.assertEqual(sorted({int(r["repetition"]) for r in p if r["session_id_suffix"] == "S2"}), [1, 2, 3, 4, 5, 6])
        self.assertEqual({r["noise_condition"] for r in p if r["condition_id"] == "N-STR"}, {"real:street-65dBA"})
        pr2 = os.path.join(self.d, "prompts2.csv")
        run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SPK-003", "--out", pr2, "--with-labels", *NOCONSENT)
        p2 = read(pr2, skip_tag=False)
        self.assertEqual([r["word_id"] for r in p], [r["word_id"] for r in p2])
        self.assertTrue(all(r["label"].startswith("PLACEHOLDER") for r in p2))
        pr3 = os.path.join(self.d, "prompts3.csv")
        run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SPK-004", "--out", pr3, *NOCONSENT)
        self.assertNotEqual([r["word_id"] for r in p], [r["word_id"] for r in read(pr3, skip_tag=False)])
        self.assertNotEqual(run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SPK-999", "--out", os.path.join(self.d, "x.csv"), *NOCONSENT)[0], 0)
        self.assertNotEqual(run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SPK-003", "--out", pr, *NOCONSENT)[0], 0)
        notwl = os.path.join(self.d, "nw.csv")
        with open(notwl, "w") as f:
            f.write("nope\n")
        self.assertNotEqual(run("prompts", "--wordlist", notwl, "--conditions", self.c, "--allocation", out, "--speaker", "SPK-003", "--out", os.path.join(self.d, "y.csv"), *NOCONSENT)[0], 0)
        # the clean-only default produces only the clean blocks
        out2, _ = self.plan(extra=())
        pr4 = os.path.join(self.d, "prompts4.csv")
        run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out2, "--speaker", "SPK-003", "--out", pr4, *NOCONSENT)
        self.assertEqual({r["condition_id"] for r in read(pr4, skip_tag=False)}, {"C0", "C0-R"})

    def test_budget_is_derived_from_the_matrix_and_the_approved_wordlist(self):
        wl = self.wordlist()
        code, out, err = run("budget", "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--wordlist", wl)
        self.assertEqual(code, 0, err)
        self.assertIn("Approved words from wordlist.csv: 6 vocab, 2 unknown", out)
        self.assertIn("(pilot guidance is 30-50 vocab words)", out)
        self.assertIn("Utterances per speaker: C0 24, C0-R 8 = 32", out)                       # (6+2)*3 and (6+2)*1: follows the actual words
        self.assertIn("Matrix cells runnable with the active conditions: 2 of 57", out)
        self.assertIn("Not active (optional/disabled)", out)
        code, out, _ = run("budget", "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--wordlist", wl, "--with", "all")
        self.assertIn("Utterances per speaker: C0 24, C0-R 8, N-FAN 8, N-STR 8, N-BAB 8, D-100 8, R-HARD 8 = 72", out)
        self.assertIn("Matrix cells runnable with the active conditions: 56 of 57", out)       # C0-DEVB stays disabled
        # unverified words are excluded and called out
        code, out, _ = run("budget", "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--wordlist", self.wordlist(verified=False))
        self.assertIn("WARNING: 1 word(s) in the word list are not 'verified'", out)
        self.assertIn("5 vocab, 2 unknown", out)
        # hand-typed numbers are accepted but flagged; missing numbers are refused
        code, out, _ = run("budget", "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--vocab-words", "40", "--oov-words", "10")
        self.assertIn("typed in by hand", out)
        self.assertIn("Utterances per speaker: C0 150, C0-R 50 = 200", out)
        self.assertIn("C0       known  480", out)
        self.assertNotEqual(run("budget", "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4")[0], 0)
        self.assertAlmostEqual(pp.wilson_half_width(0.5, 100), 0.0962, places=3)
        # an active condition that no active matrix cell uses is called out as wasted effort
        self.drop(self.m, "D-100")
        code, out, _ = run("budget", "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--wordlist", wl, "--with", "D-100")
        self.assertEqual(code, 1)                                                            # matrix no longer valid without D-100 cells


@unittest.skipUnless(HAVE_C, "C tools not built")
class ProtocolViews(Base):
    """One recording pool -> speaker-independent AND personalised datasets, each validated and evaluated by the real tools."""

    def test_views(self):
        pool = os.path.join(self.d, "pool")
        subprocess.run([os.path.join(BUILD, "s2w_make_fixture"), pool, "personalised"], check=True, stdout=subprocess.DEVNULL)
        alloc = os.path.join(self.d, "alloc.csv")
        with open(alloc, "w") as f:
            f.write("speaker_id,split\npC1,enrol\npC2,enrol\npC3,calib\npC4,calib\npT1,test\npT2,test\n")
        out = os.path.join(self.d, "views")
        code, o, e = run("views", "--pool", pool, "--allocation", alloc, "--out-dir", out)
        self.assertEqual(code, 0, e)
        self.assertNotEqual(run("views", "--pool", pool, "--allocation", alloc, "--out-dir", out)[0], 0)          # no overwrite
        for view, proto in (("si", "speaker_independent"), ("pers", "personalised")):
            r = subprocess.run([os.path.join(BUILD, "s2w_dataset_check"), os.path.join(out, view)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, view + r.stdout)
            self.assertIn("protocol=" + proto, r.stdout)
        si = read(os.path.join(out, "si", "recordings.csv"))
        pe = read(os.path.join(out, "pers", "recordings.csv"))
        split_of = {r["speaker_id"]: r["split"] for r in si}
        self.assertEqual(split_of, {"pC1": "enrol", "pC2": "enrol", "pC3": "calib", "pC4": "calib", "pT1": "test", "pT2": "test"})   # speaker-disjoint by construction
        words = {w["word_id"]: w["role"] for w in read(os.path.join(pool, "wordlist.csv"))}
        self.assertTrue(all(words[r["word_id"]] == "vocab" for r in si if r["split"] == "enrol"))                  # unknown words are never templates
        self.assertTrue(all(r["split"] == "enrol" for r in pe if r["session_id"].endswith("-S1")))
        self.assertTrue(all(words[r["word_id"]] == "vocab" for r in pe if r["split"] == "enrol"))
        for r in pe:
            if r["session_id"].endswith("-S2"):
                self.assertEqual(r["split"], "test" if r["speaker_id"].startswith("pT") else "calib")                # locked-test participants == SI test speakers
        # the same locked people are test speakers in both tracks
        self.assertEqual({r["speaker_id"] for r in si if r["split"] == "test"}, {r["speaker_id"] for r in pe if r["split"] == "test"})
        # each track evaluates only with its own settings
        si_cfg, pe_cfg = os.path.join(self.d, "si.cfg"), os.path.join(self.d, "pe.cfg")
        with open(si_cfg, "w") as f:
            f.write("#s2w-settings v1\nnorm=mean\n")
        with open(pe_cfg, "w") as f:
            f.write("#s2w-settings v1\nprotocol=personalised\nnorm=mean\n")
        ev = lambda ds, cfg, rid: subprocess.run([os.path.join(BUILD, "s2w_eval"), "run", "--dataset", os.path.join(out, ds), "--settings", cfg, "--split", "calib", "--out-dir", os.path.join(self.d, "runs"),
                                                   "--run-id", rid, "--min-group-queries", "1000"], capture_output=True, text=True)  # noqa: E731
        self.assertEqual(ev("si", si_cfg, "a").returncode, 0)
        self.assertEqual(ev("pers", pe_cfg, "b").returncode, 0)
        self.assertNotEqual(ev("si", pe_cfg, "c").returncode, 0)
        self.assertNotEqual(ev("pers", si_cfg, "d").returncode, 0)
        # bad inputs
        short = os.path.join(self.d, "short.csv")
        with open(short, "w") as f:
            f.write("speaker_id,split\npC1,enrol\n")
        self.assertNotEqual(run("views", "--pool", pool, "--allocation", short, "--out-dir", os.path.join(self.d, "v2"))[0], 0)       # pool speaker missing from allocation


if __name__ == "__main__":
    unittest.main(verbosity=1)
