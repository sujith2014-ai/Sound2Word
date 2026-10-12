#!/usr/bin/env python3
"""Tests for the enrolment experiment tooling. SYNTHETIC/SIMULATED only. These tests check wiring, labelling and
arithmetic; they deliberately do NOT assert that noise-matched enrolment helps (that is what the experiment measures)."""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BUILD = os.environ.get("S2W_BUILD", "build")
BIN = {n: os.path.join(BUILD, n) for n in ("s2w_degrade", "s2w_make_fixture", "s2w_eval", "s2w_dataset_check")}
if not all(os.path.exists(p) for p in BIN.values()):
    print("enrolment experiment tests skipped: C tools not built")
    sys.exit(0)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_enrolment_experiment as ex  # noqa: E402


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(*a):
    r = subprocess.run(list(a), capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def rows(ds):
    with open(os.path.join(ds, "recordings.csv"), encoding="utf-8") as f:
        f.readline()
        lines = f.read().splitlines()
    hdr = lines[0].split(",")
    return [dict(zip(hdr, ln.split(","))) for ln in lines[1:]]


class Helpers(unittest.TestCase):
    def test_wilson(self):
        lo, hi = ex.wilson(50, 100)
        self.assertTrue(0.40 < lo < 0.41 and 0.59 < hi < 0.60)
        self.assertEqual(ex.wilson(0, 0), (None, None))
        lo, hi = ex.wilson(72, 72)
        self.assertTrue(lo > 0.94 and hi == 1.0)
        lo, hi = ex.wilson(0, 10)
        self.assertEqual(lo, 0.0)

    def test_auc(self):
        self.assertEqual(ex.auc_lower_is_genuine([1, 2], [3, 4]), 1.0)
        self.assertEqual(ex.auc_lower_is_genuine([3, 4], [1, 2]), 0.0)
        self.assertEqual(ex.auc_lower_is_genuine([1, 1], [1, 1]), 0.5)
        self.assertAlmostEqual(ex.auc_lower_is_genuine([1, 3], [2]), 0.5)
        self.assertIsNone(ex.auc_lower_is_genuine([], [1]))
        self.assertIsNone(ex.auc_lower_is_genuine([1], []))

    def test_frr_at_far(self):
        gen = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        imp = [6.5 + i for i in range(20)]                        # 20 impostors, 5% = 1 may pass
        # threshold sits just below the 2nd smallest impostor (7.5): genuine values 8,9,10 are rejected
        self.assertEqual(ex.frr_at_far(gen, imp), 0.3)
        self.assertEqual(ex.frr_at_far(gen, [100.0, 101.0]), 0.0)
        self.assertIsNone(ex.frr_at_far([], imp))
        self.assertIsNone(ex.frr_at_far(gen, []))

    def test_recovery(self):
        self.assertAlmostEqual(ex.recovery_fraction(0.5, 0.9, 1.0), 0.8)
        self.assertEqual(ex.recovery_fraction(0.5, 0.5, 1.0), 0.0)
        self.assertLess(ex.recovery_fraction(0.5, 0.4, 1.0), 0.0)                   # can be negative: worse than clean enrolment
        self.assertIsNone(ex.recovery_fraction(0.98, 1.0, 1.0))                     # no loss to recover
        self.assertIsNone(ex.recovery_fraction(None, 1.0, 1.0))

    def test_specs(self):
        self.assertEqual(ex.parse_enrol("clean"), (None, None))
        self.assertEqual(ex.parse_enrol("snr10_white"), ("snr10_white", "replace"))
        self.assertEqual(ex.parse_enrol("mix:far_mic"), ("far_mic", "mix"))
        self.assertEqual(ex.query_label("identity"), "clean")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.fx = os.path.join(cls.d, "fx")
        subprocess.run([BIN["s2w_make_fixture"], cls.fx], check=True, stdout=subprocess.DEVNULL)
        cls.cfg = os.path.join(cls.d, "s.cfg")
        with open(cls.cfg, "w") as f:
            f.write("#s2w-settings v1\nnorm=mean\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)


class EnrolmentDegrade(Base):
    def test_replace_mode(self):
        out = os.path.join(self.d, "rep")
        code, stdout, err = run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", out, "--scenario", "identity", "--enrol-scenario", "snr10_white", "--seed", "2")
        self.assertEqual(code, 0, err)
        self.assertIn("enrol_mode=replace", stdout)
        rs = rows(out)
        enrol = [r for r in rs if r["split"] == "enrol"]
        calib = [r for r in rs if r["split"] == "calib"]
        self.assertEqual((len(enrol), len(calib)), (96, 99))
        for r in enrol:
            self.assertEqual((r["environment"], r["noise_condition"]), ("noisy", "sim:snr10_white"))
            self.assertNotEqual(sha(os.path.join(out, r["path"])), sha(os.path.join(self.fx, r["path"])))        # degraded
            self.assertEqual(sha(os.path.join(out, r["path"])), r["audio_sha256"])
        for r in calib:                                                                  # identity query condition: untouched labels
            self.assertEqual((r["environment"], r["noise_condition"]), ("clean", ""))
        self.assertEqual(run(BIN["s2w_dataset_check"], out)[0], 0)

    def test_mix_mode_keeps_clean_and_adds_degraded(self):
        out = os.path.join(self.d, "mix")
        code, stdout, err = run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", out, "--scenario", "snr10_pink", "--enrol-scenario", "snr10_white", "--enrol-mode", "mix")
        self.assertEqual(code, 0, err)
        enrol = [r for r in rows(out) if r["split"] == "enrol"]
        self.assertEqual(len(enrol), 192)
        clean = [r for r in enrol if not r["recording_id"].endswith("_sim")]
        sim = [r for r in enrol if r["recording_id"].endswith("_sim")]
        self.assertEqual((len(clean), len(sim)), (96, 96))
        for r in clean:                                                                  # originals byte-identical, labels unchanged
            self.assertEqual(sha(os.path.join(out, r["path"])), sha(os.path.join(self.fx, r["path"])))
            self.assertEqual(r["environment"], "clean")
        for r in sim:
            self.assertEqual((r["environment"], r["noise_condition"]), ("noisy", "sim:snr10_white"))
            self.assertTrue(r["session_id"].endswith("-sim") and r["path"].endswith(".sim.wav"))
        spk_split = {}
        for r in rows(out):
            spk_split.setdefault(r["speaker_id"], set()).add(r["split"])
        self.assertTrue(all(len(v) == 1 for v in spk_split.values()))                    # speaker isolation preserved
        code, o, _ = run(BIN["s2w_dataset_check"], out)
        self.assertEqual(code, 0, o)
        self.assertIn("duplicate=0", o)

    def test_enrol_option_errors_and_test_split_unread(self):
        o = os.path.join(self.d, "e1")
        for extra in (["--enrol-scenario", "nonsense"], ["--enrol-scenario", "snr10_white", "--enrol-mode", "bogus"],
                      ["--enrol-scenario", "identity", "--enrol-mode", "mix"]):
            self.assertEqual(run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", o, "--scenario", "identity", *extra)[0], 2, str(extra))
            self.assertFalse(os.path.exists(o))
        src = os.path.join(self.d, "no_test")
        shutil.copytree(self.fx, src)
        for f in os.listdir(os.path.join(src, "audio")):
            if f.startswith("spkT"):
                os.remove(os.path.join(src, "audio", f))
        code, stdout, err = run(BIN["s2w_degrade"], "--dataset", src, "--out-dir", os.path.join(self.d, "e2"), "--scenario", "snr10_white", "--enrol-scenario", "snr10_pink", "--enrol-mode", "mix")
        self.assertEqual(code, 0, err)                                                     # test audio never needed
        self.assertIn("test_recordings_skipped_unread=99", stdout)

    def test_determinism(self):
        outs = [os.path.join(self.d, n) for n in ("da", "db")]
        for o in outs:
            self.assertEqual(run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", o, "--scenario", "snr10_white", "--enrol-scenario", "snr10_pink", "--seed", "4")[0], 0)
        self.assertEqual([r["audio_sha256"] for r in rows(outs[0])], [r["audio_sha256"] for r in rows(outs[1])])


class Matrix(Base):
    def test_small_matrix_wiring(self):
        work = os.path.join(self.d, "exp")
        code = ex.main(["--dataset", self.fx, "--settings", self.cfg, "--work-dir", work, "--enrol", "clean,mix:snr10_white", "--queries", "identity,snr10_white", "--build", BUILD])
        self.assertEqual(code, 0)
        with open(os.path.join(work, "experiment.json"), encoding="utf-8") as f:
            res = json.load(f)
        self.assertIn("SIMULATED", res["meta"]["banner"])
        self.assertEqual(res["meta"]["split"], "calib only; test split never read")
        self.assertEqual(res["meta"]["matcher"], "unchanged (baseline matcher, no tuning)")
        self.assertEqual(res["meta"]["cell_errors"], 0)
        self.assertEqual(len(res["meta"]["source_manifest_sha256"]), 64)
        self.assertEqual(sorted(res["top1"]), ["clean | clean", "clean | snr10_white", "mix:snr10_white | clean", "mix:snr10_white | snr10_white"])
        cells = res["cells"]["1"]
        self.assertEqual(cells["clean | clean"]["templates"], 96)
        self.assertEqual(cells["mix:snr10_white | clean"]["templates"], 192)                  # mix really doubled the templates
        self.assertEqual(cells["clean | clean"]["simulated_recordings"], 0)
        self.assertEqual(cells["clean | snr10_white"]["simulated_recordings"], 99)
        self.assertEqual(cells["mix:snr10_white | snr10_white"]["simulated_recordings"], 99 + 96)
        # the clean/clean cell is the unmodified baseline
        plain = os.path.join(self.d, "plainrun")
        code, _, err = run(BIN["s2w_eval"], "run", "--dataset", self.fx, "--settings", self.cfg, "--split", "calib", "--out-dir", plain, "--run-id", "p", "--min-group-queries", "1000000")
        self.assertEqual(code, 0, err)
        with open(os.path.join(plain, "p", "result.json"), encoding="utf-8") as f:
            base = json.load(f)["metrics"]["known_words"]
        self.assertEqual(res["top1"]["clean | clean"]["num"], base["top1_accuracy"]["num"])
        self.assertEqual(res["top1"]["clean | clean"]["den"], base["top1_accuracy"]["den"])
        for k in ("top1", "top3", "top5"):
            for cell in res[k].values():
                self.assertEqual(cell["den"], 72)
                self.assertLessEqual(cell["ci95"][0], cell["rate"])
                self.assertGreaterEqual(cell["ci95"][1], cell["rate"])
        for v in res["auc"].values():
            self.assertTrue(0.0 <= v["auc"] <= 1.0)
        with open(os.path.join(work, "report.md"), encoding="utf-8") as f:
            md = f.read()
        for must in ("SIMULATED CONDITIONS", "Cost on clean queries", "mismatched", "Wilson", "cannot say whether real Malayalam speech"):
            self.assertIn(must, md)
        self.assertEqual(len(res["comparisons"]), 1)
        # guards
        with self.assertRaises(SystemExit):
            ex.main(["--dataset", self.fx, "--settings", self.cfg, "--work-dir", work, "--build", BUILD])                       # non-empty work dir
        with self.assertRaises(SystemExit):
            ex.main(["--dataset", self.fx, "--settings", self.cfg, "--work-dir", os.path.join(self.d, "x"), "--enrol", "snr10_white", "--build", BUILD])   # no clean baseline
        with self.assertRaises(SystemExit):
            ex.main(["--dataset", self.fx, "--settings", self.cfg, "--work-dir", os.path.join(self.d, "y"), "--queries", "snr10_white", "--build", BUILD])    # no clean query


if __name__ == "__main__":
    unittest.main(verbosity=1)
