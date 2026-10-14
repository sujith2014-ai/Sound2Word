"""Tests for tools/gate_status.py. The artifacts here are made up (fixtures); they prove the checks fire, not that any real gate has passed."""
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_status as gs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDS = ["w%d" % i for i in range(8)]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class Args:
    def __init__(self, **kw):
        self.master = self.wordlist = self.approvals = self.participants = self.consent = self.dataset = None
        self.frozen_settings = self.voice_freeze = self.units = self.lexicon = self.queries = None
        self.expect_words = 8
        self.__dict__.update(kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def wordlist(self, n=8, r1="rev-a", r2="rev-b", status="verified"):
        p = os.path.join(self.d, "wordlist.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("#s2w-wordlist v1\n")
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["word_id", "label", "romanisation", "meaning_en", "role", "pair_id", "contrast_type", "review_status", "reviewer_1", "reviewer_2", "variants"])
            for i in range(n):
                w.writerow(["w%d" % i, "ക", "r", "m", "vocab", "", "none", status, r1, r2, ""])
        return p

    def provenance(self, wl, n_vocab=8, r1="rev-a", r2="rev-b", master=None):
        pp = os.path.splitext(wl)[0] + ".provenance.json"
        prov = {"schema": "s2w-wordlist-provenance-v1", "wordlist_sha256": sha(wl), "master_sha256": sha(master) if master else "x", "reviewer_1": r1, "reviewer_2": r2,
                "n_vocab": n_vocab}
        json.dump(prov, open(pp, "w"))
        return pp

    def states(self, checks):
        return {c["check"].split()[0]: c["state"] for c in checks}


class GateA(Base):
    def test_everything_missing_is_reported_as_missing_with_the_needed_flag(self):
        s = self.states(gs.gate_a(Args()))
        self.assertEqual(set(s.values()), {gs.MISSING})
        self.assertIn("--master", gs.gate_a(Args())[0]["detail"])

    def test_a_valid_exported_word_list_passes_a2_and_a3(self):
        wl = self.wordlist()
        self.provenance(wl)
        s = self.states(gs.wordlist_checks(wl, 8))
        self.assertEqual(s, {"A2": gs.PASS, "A3": gs.PASS})

    def test_the_same_reviewer_twice_fails(self):
        wl = self.wordlist(r1="rev-a", r2="REV-A ")
        self.provenance(wl)
        c = gs.wordlist_checks(wl, 8)
        self.assertEqual(c[0]["state"], gs.FAIL)
        self.assertIn("same person", c[0]["detail"])

    def test_wrong_word_count_and_unverified_rows_fail(self):
        wl = self.wordlist(n=7)
        self.provenance(wl, n_vocab=7)
        c = gs.wordlist_checks(wl, 8)
        self.assertEqual((c[0]["state"], c[1]["state"]), (gs.FAIL, gs.FAIL))
        wl = self.wordlist(status="draft")
        self.assertIn("not 'verified'", gs.wordlist_checks(wl, 8)[0]["detail"])

    def test_editing_the_word_list_after_export_is_detected(self):
        wl = self.wordlist()
        self.provenance(wl)
        with open(wl, "a", encoding="utf-8") as f:
            f.write("w9,ക,r,m,oov,,none,verified,rev-a,rev-b,\n")
        c = gs.wordlist_checks(wl, 8)
        self.assertEqual(c[1]["state"], gs.FAIL)
        self.assertIn("edited after export", c[1]["detail"])

    def test_a_changed_master_workbook_is_detected(self):
        wl = self.wordlist()
        master = os.path.join(self.d, "master.xlsx")
        open(master, "wb").write(b"original")
        self.provenance(wl, master=master)
        open(master, "wb").write(b"changed")
        c = gs.wordlist_checks(wl, 8, master)
        self.assertIn("workbook changed after export", c[1]["detail"])

    def test_an_empty_review_workbook_does_not_pass(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("openpyxl not installed")
        m = os.path.join(self.d, "m.xlsx")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "review_sheet.py"), "template", m], check=True, capture_output=True)
        c = gs.master_status(m, 8)
        self.assertEqual(c["state"], gs.FAIL)

    def test_the_draft_workbook_with_pending_words_fails_exactly_as_the_real_one_does(self):
        real = os.path.join(ROOT, "data", "private", "review", "master.xlsx")
        if not os.path.exists(real):
            self.skipTest("no private review workbook in this checkout")
        c = gs.master_status(real, 8)
        self.assertEqual(c["state"], gs.FAIL)
        self.assertIn("Pending review", c["detail"])


class Sequencing(Base):
    def test_a_failing_gate_a_blocks_b_and_c(self):
        gates = gs.evaluate_gates(Args())
        self.assertEqual([g["state"] for g in gates], ["NOT PASSED", "NOT EVALUATED", "NOT EVALUATED"])
        self.assertIn("blocked by Gate A", gates[1]["reason"])

    def test_exit_code_and_report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = gs.main([])
        self.assertEqual(rc, 1)
        self.assertIn("NOT EVALUATED", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gs.main(["--expect-words", "0"]), 2)

    def test_all_gates_passing_gives_exit_0_and_runs_in_order(self):
        order = []
        ok = lambda g: (lambda a: order.append(g) or [gs.result(g + "1 x", gs.PASS, "ok")])
        saved = gs.gate_a, gs.gate_b, gs.gate_c
        try:
            gs.gate_a, gs.gate_b, gs.gate_c = ok("A"), ok("B"), ok("C")
            gates = gs.evaluate_gates(Args())
        finally:
            gs.gate_a, gs.gate_b, gs.gate_c = saved
        self.assertEqual(order, ["A", "B", "C"])
        self.assertTrue(all(g["state"] == gs.PASS for g in gates))

    def test_gate_b_rejects_a_synthetic_dataset_even_when_it_is_valid(self):
        tool = os.path.join(ROOT, "build", "s2w_make_fixture")
        if not os.path.exists(tool):
            self.skipTest("C tools not built")
        ds = os.path.join(self.d, "ds")
        subprocess.run([tool, ds], check=True, capture_output=True)
        c = [x for x in gs.gate_b(Args(dataset=ds)) if x["check"].startswith("B2")][0]
        self.assertEqual(c["state"], gs.FAIL)
        self.assertIn("SYNTHETIC", c["detail"])


class DatasetDesign(Base):
    def dataset(self, recs, words):
        ds = os.path.join(self.d, "ds")
        os.makedirs(ds, exist_ok=True)
        with open(os.path.join(ds, "wordlist.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("#s2w-wordlist v1\nword_id,label,role\n" + "".join("%s,x,%s\n" % w for w in words))
        with open(os.path.join(ds, "recordings.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("#s2w-recordings v1\nrecording_id,word_id,speaker_id,split\n" + "".join("r%d,%s,%s,%s\n" % (i, w, s_, sp) for i, (w, s_, sp) in enumerate(recs)))
        return ds

    GOOD = [("v1", "e1", "enrol"), ("v1", "c1", "calib"), ("v1", "t1", "test"), ("o1", "c1", "calib"), ("o1", "t1", "test")]
    WORDS = [("v1", "vocab"), ("o1", "oov")]

    def test_a_well_designed_dataset_passes(self):
        self.assertEqual(gs.dataset_design_check(self.dataset(self.GOOD, self.WORDS))["state"], gs.PASS)

    def test_each_design_flaw_is_named(self):
        cases = [(self.GOOD + [("v1", "e1", "test")], "more than one split"), (self.GOOD + [("o1", "e2", "enrol")], "OOV words enrolled"),
                 ([r for r in self.GOOD if r[0] != "o1"], "never spoken in calib/test"), ([r for r in self.GOOD if r[2] != "enrol"], "no enrolment recording"),
                 ([r for r in self.GOOD if r[2] == "enrol"] + [("o1", "c1", "calib")], "never queried")]
        for recs, text in cases:
            c = gs.dataset_design_check(self.dataset(recs, self.WORDS))
            self.assertEqual(c["state"], gs.FAIL, text)
            self.assertIn(text, c["detail"])
        c = gs.dataset_design_check(self.dataset(self.GOOD, [("v1", "vocab")]))
        self.assertIn("no OOV words", c["detail"])

    def test_missing_files_are_reported(self):
        self.assertEqual(gs.dataset_design_check(self.d)["state"], gs.MISSING)


class GateC(Base):
    def frozen(self, tamper=False, frozen=True):
        body = "norm=mean\nrep=mfcc\n"
        p = os.path.join(self.d, "frozen.cfg")
        with open(p, "w") as f:
            f.write("#s2w-settings v1\n" + body)
            if frozen:
                f.write("frozen=yes\nsettings_sha256=%s\n" % hashlib.sha256((body + ("x" if tamper else "")).encode()).hexdigest())
            else:
                f.write("frozen=no\n")
        return p

    def test_unfrozen_tampered_or_uncommitted_settings_fail(self):
        self.assertIn("not frozen", gs.frozen_settings_check(self.frozen(frozen=False))["detail"])
        self.assertIn("edited after freezing", gs.frozen_settings_check(self.frozen(tamper=True))["detail"])
        c = gs.frozen_settings_check(self.frozen())                         # a valid hash, but the file is not in the repository
        self.assertEqual(c["state"], gs.FAIL)
        self.assertIn("not committed", c["detail"])

    def test_missing_settings_are_reported(self):
        self.assertEqual(gs.frozen_settings_check(None)["state"], gs.MISSING)

    def test_the_voice_freeze_needs_all_inputs_and_synthetic_freezes_cannot_complete_the_gate(self):
        self.assertEqual(gs.voice_freeze_check(Args())["state"], gs.MISSING)
        cfgp = os.path.join(self.d, "f.json")
        open(cfgp, "w").write("{}")
        self.assertEqual(gs.voice_freeze_check(Args(voice_freeze=cfgp))["state"], gs.MISSING)


if __name__ == "__main__":
    unittest.main()
