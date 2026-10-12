#!/usr/bin/env python3
"""Tests for pilot_plan.py. Uses only synthetic ids and the synthetic fixture word list (SYNTH_*); no Malayalam
vocabulary and no real data. Verifies the spec/plan machinery and its compatibility with the dataset tools."""
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
import pilot_plan as pp  # noqa: E402

BUILD = os.environ.get("S2W_BUILD", "build")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SPEC_DIR = os.path.join(ROOT, "data", "schema")
HAVE_C = all(os.path.exists(os.path.join(BUILD, n)) for n in ("s2w_ingest", "s2w_dataset_check", "s2w_make_fixture"))


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

    def drop(self, path, needle):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines(True)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(ln for ln in lines if needle not in ln)

    def errors(self):
        code, out, err = run("check", "--conditions", self.c, "--matrix", self.m)
        return code, out, err


class Spec(Base):
    def test_committed_spec_is_current_and_valid(self):
        fresh = os.path.join(self.d, "fresh")
        self.assertEqual(run("write-spec", fresh)[0], 0)
        for n in ("pilot_conditions.csv", "pilot_matrix.csv"):
            with open(os.path.join(fresh, n), encoding="utf-8") as a, open(os.path.join(SPEC_DIR, n), encoding="utf-8") as b:
                self.assertEqual(a.read(), b.read(), "regenerate with: tools/pilot_plan.py write-spec")
        self.assertNotEqual(run("write-spec", fresh)[0], 0)                        # never overwrites
        code, out, err = self.errors()
        self.assertEqual(code, 0, err)
        self.assertIn("result=VALID", out)
        self.assertIn("C0-DEVB", out)                                              # the G2 warning is surfaced, not hidden

    def test_matrix_covers_required_categories(self):
        cats = {r["category"] for r in read(self.m)}
        for need in ("baseline", "clean_enrol_loss", "noise_only_matched", "noise_only_mismatched", "mixed_matched", "mixed_mismatched",
                     "noise_only_clean_cost", "mixed_clean_cost", "mixed_all", "session_repeat", "device_mismatch"):
            self.assertIn(need, cats)
        # every query condition is paired with clean enrolment, so the loss can always be measured
        conds = {r["condition_id"] for r in read(self.c)}
        for q in conds - {"C0"}:
            self.assertTrue(any(r["enrol_conditions"] == "C0" and r["query_condition"] == q for r in read(self.m)), q)

    def test_conditions_map_onto_dataset_fields(self):
        for r in read(self.c):
            self.assertTrue(pp.ID_RE.match(r["condition_id"]) and pp.ID_RE.match(r["device_id"]))
            self.assertIn(r["environment"], ("clean", "noisy"))
            self.assertEqual(bool(r["noise_condition"]), r["environment"] == "noisy")
            self.assertFalse(r["noise_condition"].startswith("sim:"))              # 'sim:' is reserved for simulated data
            if r["environment"] == "noisy":
                self.assertTrue(r["noise_condition"].startswith("real:"))
            self.assertEqual(r["qa_limits"], r["environment"])

    def test_invalid_specs_are_rejected(self):
        cases = [
            ("sim prefix", "c", "real:fan-60dBA", "sim:fan-60dBA", "reserved for simulated"),
            ("not real prefix", "c", "real:street-65dBA", "street-65dBA", "must start with 'real:'"),
            ("clean with noise", "c", "C0,clean,,", "C0,clean,real:x,", "clean conditions must have an empty"),
            ("noisy without noise", "c", "N-FAN,noisy,real:fan-60dBA", "N-FAN,noisy,", "need a noise_condition"),
            ("bad env", "c", "N-FAN,noisy", "N-FAN,loud", "environment must be"),
            ("qa mismatch", "c", "N-STR,noisy,real:street-65dBA,DEV-A,noisy", "N-STR,noisy,real:street-65dBA,DEV-A,clean", "qa_limits"),
            ("bad session", "c", ",S2,1,yes,noise", ",S0,1,yes,noise", "session must be"),
            ("bad reps", "c", ",S2,1,yes,noise", ",S2,99,yes,noise", "repetitions must be"),
            ("bad role", "c", ",noise,no", ",weird,no", "role must be"),
            ("bad id", "c", "N-BAB,noisy", "N BAB,noisy", "invalid id"),
            ("comma in noise", "c", "real:fan-60dBA", '"real:fan,60"', "noise_condition must be"),
            ("unknown condition in matrix", "m", "EM-FAN__N-STR,EM-FAN,C0+N-FAN,N-STR", "EM-FAN__N-STR,EM-FAN,C0+N-FAN,N-XXX", "unknown condition"),
            ("wrong category", "m", "E0__N-FAN,E0,C0,N-FAN,clean_enrol_loss", "E0__N-FAN,E0,C0,N-FAN,baseline", "category"),
            ("device as enrolment", "m", "EM-FAN__N-STR,EM-FAN,C0+N-FAN,N-STR", "EM-FAN__N-STR,EM-FAN,C0+C0-DEVB,N-STR", "device-only"),
            ("bad matrix tag", "m", "#s2w-pilot-matrix v1", "#s2w-pilot-matrix v9", "expected first line"),
            ("bad conditions tag", "c", "#s2w-pilot-conditions v1", "#nope", "expected first line"),
        ]
        for name, which, find, repl, expect in cases:
            self.tearDown(); self.setUp()
            self.edit(self.c if which == "c" else self.m, find, repl)
            code, out, err = self.errors()
            self.assertEqual(code, 1, name)
            self.assertIn(expect, err, name)

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
        self.tearDown(); self.setUp()
        self.edit(self.c, "N-FAN,noisy,real:fan-60dBA", "N-FAN,noisy,real:fan-60dBA", 1)
        self.edit(self.c, ",yes,", ",no,", 20)                                      # no condition includes unknown words
        self.assertIn("unknown (oov) words", self.errors()[2])


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
        for r in read(self.c):
            n += 1
            src = os.path.join(fx, "audio", wavs[n])                                # distinct bytes per recording (identical audio is flagged as leakage)
            r_ = subprocess.run([os.path.join(BUILD, "s2w_ingest"), "--in", src, "--dataset-dir", ds, "--recording-id", "rec%02d" % n, "--word-id", "SYNTH_V1",
                                 "--speaker-id", "spkE1", "--session-id", "spkE1-%s" % r["session"], "--repetition", str(n), "--split", "enrol", "--device-id", r["device_id"],
                                 "--environment", r["environment"], "--noise-condition", r["noise_condition"], "--append-manifest",
                                 "--created-utc", "2000-01-01T00:00:00Z"], capture_output=True, text=True)
            self.assertEqual(r_.returncode, 0, "%s: %s" % (r["condition_id"], r_.stderr))
        c = subprocess.run([os.path.join(BUILD, "s2w_dataset_check"), ds], capture_output=True, text=True)
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("result=VALID", c.stdout)
        rows = read(os.path.join(ds, "recordings.csv"))
        self.assertEqual({r["noise_condition"] for r in rows if r["environment"] == "noisy"}, {r["noise_condition"] for r in read(self.c) if r["environment"] == "noisy"})


class Plan(Base):
    def speakers(self, n=16, strata=("A", "B")):
        p = os.path.join(self.d, "speakers.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("speaker_id,stratum\n")
            for i in range(n):
                f.write("SP%03d,%s\n" % (i + 1, strata[i % len(strata)] if strata else ""))
        return p

    _n = 0

    def plan(self, seed=7, n=16, splits="8,4,4", strata=("A", "B")):
        Plan._n += 1
        out = os.path.join(self.d, "alloc_%d_%s_%d.csv" % (Plan._n, seed, n))
        code, o, e = run("plan", "--speakers", self.speakers(n, strata), "--conditions", self.c, "--matrix", self.m, "--splits", splits, "--seed", str(seed), "--out", out)
        self.assertEqual(code, 0, e)
        return out, read(out, skip_tag=False)

    def test_allocation(self):
        out, rows = self.plan()
        self.assertEqual(len(rows), 16)
        by = {}
        for r in rows:
            by.setdefault(r["split"], []).append(r)
        self.assertEqual({k: len(v) for k, v in by.items()}, {"enrol": 8, "calib": 4, "test": 4})
        for split, members in by.items():                                          # strata balanced across splits
            a = sum(1 for r in members if r["stratum"] == "A")
            self.assertLessEqual(abs(a - len(members) / 2), 1, split)
        self.assertEqual(len({r["speaker_id"] for r in rows}), 16)                 # each speaker in exactly one split (speaker-disjoint)
        _, again = self.plan()
        self.assertEqual(rows, again)                                              # deterministic
        _, other = self.plan(seed=8)
        self.assertNotEqual([r["split"] for r in rows], [r["split"] for r in other])
        orders = {r["s2_blocks"] for r in rows}
        self.assertGreaterEqual(len(orders), 5)                                    # noisy-block order rotates (counterbalanced)
        for r in rows:
            blocks = r["s2_blocks"].split("+")
            self.assertEqual(blocks[0], "C0-R")                                    # clean repeat always first in session 2
            self.assertEqual(sorted(blocks[1:]), ["D-100", "N-BAB", "N-FAN", "N-STR", "R-HARD"])
            self.assertEqual(r["s1_blocks"], "C0")                                 # simultaneous second device is not a separate block
            self.assertEqual((r["personalised_enrol_session"], r["personalised_query_session"]), ("S1", "S2"))
        pos = {}
        for r in rows:
            for i, b in enumerate(r["s2_blocks"].split("+")[1:]):
                pos.setdefault(b, set()).add(i)
        self.assertTrue(all(len(v) >= 3 for v in pos.values()))                    # each block appears at several positions

    def test_allocation_without_strata_and_errors(self):
        _, rows = self.plan(strata=None)
        self.assertEqual(sum(1 for r in rows if r["split"] == "test"), 4)
        sp = self.speakers()
        self.assertNotEqual(run("plan", "--speakers", sp, "--conditions", self.c, "--splits", "8,4,3", "--seed", "1", "--out", os.path.join(self.d, "b.csv"))[0], 0)      # sizes mismatch
        self.assertNotEqual(run("plan", "--speakers", sp, "--conditions", self.c, "--splits", "8,4", "--seed", "1", "--out", os.path.join(self.d, "c.csv"))[0], 0)
        dup = os.path.join(self.d, "dup.csv")
        with open(dup, "w") as f:
            f.write("speaker_id\nSP001\nSP001\n")
        self.assertNotEqual(run("plan", "--speakers", dup, "--conditions", self.c, "--splits", "1,1,1", "--seed", "1", "--out", os.path.join(self.d, "d.csv"))[0], 0)
        bad = os.path.join(self.d, "bad.csv")
        with open(bad, "w") as f:
            f.write("speaker_id\nname with space\nSP2\nSP3\n")
        self.assertNotEqual(run("plan", "--speakers", bad, "--conditions", self.c, "--splits", "1,1,1", "--seed", "1", "--out", os.path.join(self.d, "e.csv"))[0], 0)
        out, _ = self.plan()
        self.assertNotEqual(run("plan", "--speakers", sp, "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "7", "--out", out)[0], 0)   # no overwrite
        self.edit(self.m, "#s2w-pilot-matrix v1", "#nope")
        self.assertNotEqual(run("plan", "--speakers", sp, "--conditions", self.c, "--matrix", self.m, "--splits", "8,4,4", "--seed", "9", "--out", os.path.join(self.d, "f.csv"))[0], 0)

    def wordlist(self):
        p = os.path.join(self.d, "wordlist.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("#s2w-wordlist v1\nword_id,label,romanisation,meaning_en,role,pair_id,contrast_type,review_status,reviewer_1,reviewer_2,variants\n")
            for i in range(1, 7):
                f.write("SYNTH_V%d,PLACEHOLDER_%d,,m,vocab,,none,verified,a,b,\n" % (i, i))
            for i in range(1, 3):
                f.write("SYNTH_O%d,PLACEHOLDER_O%d,,m,oov,,none,verified,a,b,\n" % (i, i))
        return p

    def test_prompts(self):
        out, rows = self.plan()
        wl = self.wordlist()
        pr = os.path.join(self.d, "prompts.csv")
        code, o, e = run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SP003", "--out", pr)
        self.assertEqual(code, 0, e)
        p = read(pr, skip_tag=False)
        self.assertTrue(all(r["label"] == "" for r in p))                          # labels only on request
        conds = {r["condition_id"]: r for r in read(self.c)}
        by_block = {}
        for r in p:
            by_block.setdefault((r["session_id_suffix"], r["condition_id"]), []).append(r)
        self.assertEqual(set(k[1] for k in by_block), {"C0", "C0-R", "N-FAN", "N-STR", "N-BAB", "D-100", "R-HARD"})
        for (sess, cid), rs in by_block.items():
            words = 6 + (2 if conds[cid]["include_oov"] == "yes" else 0)
            self.assertEqual(len(rs), words * int(conds[cid]["repetitions"]), cid)
            for rnd in range(1, int(conds[cid]["repetitions"]) + 1):                # each word exactly once per round
                self.assertEqual(sorted(r["word_id"] for r in rs if int(r["round"]) == rnd), sorted(["SYNTH_V%d" % i for i in range(1, 7)] + ["SYNTH_O1", "SYNTH_O2"]))
            seq = [r["word_id"] for r in rs]
            self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])))             # never the same word twice in a row, also across rounds
        keys = [(r["speaker_id"], r["session_id_suffix"], r["word_id"], r["repetition"]) for r in p]
        self.assertEqual(len(keys), len(set(keys)))                                # dataset key never collides across blocks
        ids = [r["planned_recording_id"] for r in p]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(pp.ID_RE.match(i) for i in ids))
        c0 = [int(r["repetition"]) for r in p if r["condition_id"] == "C0"]
        self.assertEqual((min(c0), max(c0)), (1, 3))
        s2 = sorted({int(r["repetition"]) for r in p if r["session_id_suffix"] == "S2"})
        self.assertEqual(s2, [1, 2, 3, 4, 5, 6])                                   # one rep per block, numbered in block order
        self.assertEqual({r["noise_condition"] for r in p if r["condition_id"] == "N-STR"}, {"real:street-65dBA"})
        # deterministic and label option
        pr2 = os.path.join(self.d, "prompts2.csv")
        run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SP003", "--out", pr2, "--with-labels")
        p2 = read(pr2, skip_tag=False)
        self.assertEqual([r["word_id"] for r in p], [r["word_id"] for r in p2])
        self.assertTrue(all(r["label"].startswith("PLACEHOLDER") for r in p2))
        # different speakers get different orders; errors
        pr3 = os.path.join(self.d, "prompts3.csv")
        run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SP004", "--out", pr3)
        self.assertNotEqual([r["word_id"] for r in p], [r["word_id"] for r in read(pr3, skip_tag=False)])
        self.assertNotEqual(run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SP999", "--out", os.path.join(self.d, "x.csv"))[0], 0)
        self.assertNotEqual(run("prompts", "--wordlist", wl, "--conditions", self.c, "--allocation", out, "--speaker", "SP003", "--out", pr)[0], 0)
        notwl = os.path.join(self.d, "nw.csv")
        with open(notwl, "w") as f:
            f.write("nope\n")
        self.assertNotEqual(run("prompts", "--wordlist", notwl, "--conditions", self.c, "--allocation", out, "--speaker", "SP003", "--out", os.path.join(self.d, "y.csv"))[0], 0)

    def test_budget(self):
        code, out, _ = run("budget", "--conditions", self.c, "--splits", "8,4,4", "--vocab-words", "40", "--oov-words", "10")
        self.assertEqual(code, 0)
        self.assertIn("Utterances per speaker: C0 150, C0-R 50, N-FAN 50, N-STR 50, N-BAB 50, D-100 50, R-HARD 50 = 450", out)
        self.assertIn("C0       known  480", out)
        self.assertIn("N-FAN    known  160", out)
        self.assertIn("large effects only", out)
        self.assertAlmostEqual(pp.wilson_half_width(0.5, 100), 0.0962, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=1)
