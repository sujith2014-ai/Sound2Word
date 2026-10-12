#!/usr/bin/env python3
"""Tests for s2w_degrade and run_robustness.py. Everything here is SIMULATED/synthetic: it validates that
the software handles the signal changes and labels them honestly, NOT that Malayalam recognition is robust."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BUILD = os.environ.get("S2W_BUILD", "build")
BIN = {n: os.path.join(BUILD, n) for n in ("s2w_degrade", "s2w_make_fixture", "s2w_eval", "s2w_dataset_check")}
if not all(os.path.exists(p) for p in BIN.values()):
    print("robustness tests skipped: C tools not built")
    sys.exit(0)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_robustness as rr  # noqa: E402


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

    def eval_calib(self, ds, name):
        out = os.path.join(self.d, "runs_" + name)
        code, stdout, err = run(BIN["s2w_eval"], "run", "--dataset", ds, "--settings", self.cfg, "--split", "calib", "--out-dir", out, "--run-id", name,
                                "--created-utc", "1970-01-01T00:00:00Z", "--min-group-queries", "1000000")
        self.assertEqual(code, 0, err)
        with open(os.path.join(out, name, "result.json"), encoding="utf-8") as f:
            return json.load(f), stdout


class Degrade(Base):
    def test_list_and_errors(self):
        code, out, _ = run(BIN["s2w_degrade"], "--list")
        self.assertEqual(code, 0)
        names = [ln.split()[0] for ln in out.splitlines()]
        for must in ("identity", "snr10_white", "far_mic", "telephone", "speed_085", "rate_8k", "dropout_mid", "head_cut_240ms"):
            self.assertIn(must, names)
        o = os.path.join(self.d, "o1")
        self.assertEqual(run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", o, "--scenario", "nonsense")[0], 2)
        self.assertFalse(os.path.exists(o))
        self.assertEqual(run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", o)[0], 2)
        self.assertEqual(run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", o, "--scenario", "identity", "--seed", "x")[0], 2)
        self.assertEqual(run(BIN["s2w_degrade"], "--dataset", "/nonexistent", "--out-dir", o, "--scenario", "identity")[0], 2)

    def test_derived_dataset_properties_and_unread_test_split(self):
        src = os.path.join(self.d, "src_no_test")
        shutil.copytree(self.fx, src)
        for f in os.listdir(os.path.join(src, "audio")):
            if f.startswith("spkT"):
                os.remove(os.path.join(src, "audio", f))        # test audio is gone: degrade must still work (never reads it)
        # recordings.csv still lists test rows, so the *validator* would complain, but s2w_degrade only needs structure
        out = os.path.join(self.d, "o2")
        code, stdout, err = run(BIN["s2w_degrade"], "--dataset", src, "--out-dir", out, "--scenario", "snr10_white", "--seed", "3")
        self.assertEqual(code, 0, err)
        self.assertIn("test_recordings_skipped_unread=99", stdout)
        self.assertIn("SIMULATED", stdout)
        rs = rows(out)
        self.assertEqual({r["split"] for r in rs}, {"enrol", "calib"})
        for r in rs:
            p = os.path.join(out, r["path"])
            self.assertEqual(sha(p), r["audio_sha256"])                    # pins match
            if r["split"] == "enrol":
                self.assertEqual(sha(p), sha(os.path.join(self.fx, r["path"])))        # enrolment audio byte-identical
                self.assertEqual((r["environment"], r["noise_condition"]), ("clean", ""))
            else:
                self.assertNotEqual(sha(p), sha(os.path.join(self.fx, r["path"])))     # degraded
                self.assertEqual((r["environment"], r["noise_condition"]), ("noisy", "sim:snr10_white"))
        with open(os.path.join(out, "dataset_meta.txt")) as f:
            self.assertIn("dataset_id=synthetic-fixture.sim-snr10_white", f.read())
        code, stdout, _ = run(BIN["s2w_dataset_check"], out)
        self.assertEqual(code, 0, stdout)
        self.assertEqual(run(BIN["s2w_degrade"], "--dataset", src, "--out-dir", out, "--scenario", "snr10_white")[0], 2)   # never overwrites

    def test_determinism_and_seed(self):
        a, b, c = (os.path.join(self.d, n) for n in ("da", "db", "dc"))
        for out, seed in ((a, "5"), (b, "5"), (c, "6")):
            self.assertEqual(run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", out, "--scenario", "snr10_pink", "--seed", seed)[0], 0)
        ra, rb, rc = rows(a), rows(b), rows(c)
        self.assertEqual([r["audio_sha256"] for r in ra], [r["audio_sha256"] for r in rb])
        calib = [i for i, r in enumerate(ra) if r["split"] == "calib"]
        self.assertTrue(all(ra[i]["audio_sha256"] != rc[i]["audio_sha256"] for i in calib))

    def test_identity_is_labelled_unsimulated(self):
        out = os.path.join(self.d, "oid")
        self.assertEqual(run(BIN["s2w_degrade"], "--dataset", self.fx, "--out-dir", out, "--scenario", "identity")[0], 0)
        for r in rows(out):
            self.assertEqual((r["environment"], r["noise_condition"]), ("clean", ""))
        res, stdout = self.eval_calib(out, "idrun")
        self.assertEqual(res["evidence"]["simulated_recordings"], 0)
        self.assertNotIn("SIMULATED CONDITIONS", stdout)

    def test_invalid_source_refused(self):
        bad = os.path.join(self.d, "badsrc")
        shutil.copytree(self.fx, bad)
        p = os.path.join(bad, "recordings.csv")
        with open(p, encoding="utf-8") as f:
            t = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(t.replace(",spkC1-s1,", ",spkE1-s1,", 1))
        out = os.path.join(self.d, "obad")
        code, _, err = run(BIN["s2w_degrade"], "--dataset", bad, "--out-dir", out, "--scenario", "identity")
        self.assertEqual(code, 2)
        self.assertIn("invalid", err)
        self.assertFalse(os.path.exists(os.path.join(out, "recordings.csv")))


class Runner(Base):
    def test_summary_banner_and_expected_direction(self):
        work = os.path.join(self.d, "rob")
        code = rr.main(["--dataset", self.fx, "--settings", self.cfg, "--work-dir", work, "--scenarios", "identity,snr20_white,snr0_white,gain_m20", "--build", BUILD])
        self.assertEqual(code, 0)
        with open(os.path.join(work, "robustness_summary.json"), encoding="utf-8") as f:
            s = json.load(f)
        self.assertIn("SIMULATED", s["banner"])
        self.assertIn("test split never read", s["split"])
        by = {r["scenario"]: r for r in s["rows"]}
        base, _ = self.eval_calib(self.fx, "plain")
        self.assertEqual(by["identity"]["top1"], base["metrics"]["known_words"]["top1_accuracy"]["value"])   # control reproduces the clean run
        self.assertLess(by["snr0_white"]["top1"], by["identity"]["top1"])                                      # heavy noise hurts (synthetic)
        self.assertLess(by["snr0_white"]["top1"], by["snr20_white"]["top1"] + 1e-9)
        self.assertEqual(by["gain_m20"]["top1"], by["identity"]["top1"])                                       # mean normalisation removes pure gain
        self.assertEqual(by["identity"]["simulated_recordings"], 0)
        self.assertEqual(by["snr0_white"]["simulated_recordings"], 99)
        self.assertIn("SIMULATED CONDITIONS", by["snr0_white"]["scope"])
        self.assertFalse(by["snr0_white"]["rejection_active"])
        with self.assertRaises(SystemExit):
            rr.main(["--dataset", self.fx, "--settings", self.cfg, "--work-dir", work, "--build", BUILD])        # non-empty work dir is refused
        bad = os.path.join(self.d, "rob2")
        self.assertEqual(rr.main(["--dataset", self.fx, "--settings", self.cfg, "--work-dir", bad, "--scenarios", "nonsense", "--build", BUILD]), 1)

    def test_wide_speaker_variation_fixture(self):
        wide = os.path.join(self.d, "fxw")
        subprocess.run([BIN["s2w_make_fixture"], wide, "wide"], check=True, stdout=subprocess.DEVNULL)
        code, out, _ = run(BIN["s2w_dataset_check"], wide)
        self.assertEqual(code, 0, out)
        self.assertIn("dataset_id=synthetic-fixture-wide", out)
        narrow, _ = self.eval_calib(self.fx, "narrow")
        wider, stdout = self.eval_calib(wide, "wider")
        self.assertIn("SYNTHETIC FIXTURE RUN", stdout)
        self.assertLess(wider["metrics"]["known_words"]["top1_accuracy"]["value"], narrow["metrics"]["known_words"]["top1_accuracy"]["value"])
        self.assertEqual(run(BIN["s2w_make_fixture"], os.path.join(self.d, "x"), "bogus")[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
