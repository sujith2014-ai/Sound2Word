#!/usr/bin/env python3
"""Tests for s2w_ingest / s2w_qa. All audio is SYNTHETIC (tone bursts + noise): these tests validate
software behaviour (byte preservation, QA gating, manifest rows), not speech quality."""
import hashlib
import json
import math
import os
import random
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

BUILD = os.environ.get("S2W_BUILD", "build")
INGEST, QA, CHECK, EVAL, FIXTURE = (os.path.join(BUILD, n) for n in ("s2w_ingest", "s2w_qa", "s2w_dataset_check", "s2w_eval", "s2w_make_fixture"))
if not all(os.path.exists(p) for p in (INGEST, QA, CHECK, EVAL, FIXTURE)):
    print("ingest tests skipped: C tools not built")
    sys.exit(0)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def speechlike(path, rate=16000, channels=1, lead=0.3, trail=0.3, amp=0.4, sigma=1e-4, seed=1, clip=False, bits16=True):
    """three tone bursts between lead/trail seconds of Gaussian noise, written as 16-bit PCM WAV"""
    rnd = random.Random(seed)
    nl, nt, nb = int(lead * rate), int(trail * rate), int(0.15 * rate)
    n = nl + 3 * nb + nt
    x = [rnd.gauss(0, sigma) for _ in range(n)]
    for b, f in enumerate((500, 1000, 1500)):
        for i in range(nb):
            env = min(1.0, i / 160.0, (nb - i) / 160.0)
            x[nl + b * nb + i] += env * amp * math.sin(2 * math.pi * f * i / rate)
    if clip:
        x = [max(-1.0, min(1.0, v)) for v in x]
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for v in x:
            q = max(-32768, min(32767, int(round(v * 32768))))
            frames += struct.pack("<h", q) * channels
        w.writeframes(bytes(frames))
    return n


def run(*args):
    r = subprocess.run(list(args), capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def ingest(src, ds, rid="rec0001", word="SYNTH_V1", spk="spkE1", ses="spkE1-s1", rep=1, split="enrol", env="clean", extra=()):
    return run(INGEST, "--in", src, "--dataset-dir", ds, "--recording-id", rid, "--word-id", word, "--speaker-id", spk, "--session-id", ses,
               "--repetition", str(rep), "--split", split, "--device-id", "dev-a", "--environment", env, "--created-utc", "2000-01-01T00:00:00Z", *extra)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ds = os.path.join(self.d, "ds")
        os.mkdir(self.ds)
        self.src = os.path.join(self.d, "in.wav")
        speechlike(self.src)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def listing(self):
        out = {}
        for root, _, files in os.walk(self.ds):
            for f in files:
                out[os.path.relpath(os.path.join(root, f), self.ds)] = sha(os.path.join(root, f))
        return out


class QaCli(Base):
    def test_clean_ok_and_modes(self):
        code, out, err = run(QA, self.src)
        self.assertEqual(code, 0, err)
        self.assertIn("verdict=ok", out)
        self.assertIn("flags=none", out)
        before = sha(self.src)
        run(QA, self.src, "--condition", "noisy")
        self.assertEqual(sha(self.src), before)                              # never modifies the file
        self.assertEqual(run(QA, self.src, "--condition", "loud")[0], 2)
        self.assertEqual(run(QA, "/nonexistent.wav")[0], 2)
        self.assertEqual(run(QA)[0], 2)

    def test_exit_codes(self):
        p = os.path.join(self.d, "cut.wav")
        speechlike(p, lead=0.005)
        code, out, _ = run(QA, p)
        self.assertEqual(code, 3)
        self.assertIn("onset_cut", out)
        p2 = os.path.join(self.d, "snr15.wav")
        speechlike(p2, sigma=math.sqrt(0.4 ** 2 / 2 / 10 ** 1.5))
        code, out, _ = run(QA, p2)
        self.assertEqual(code, 1)
        self.assertIn("verdict=marginal", out)
        self.assertEqual(run(QA, p2, "--condition", "noisy")[0], 0)


class Ingest(Base):
    def test_original_preserved_and_derived(self):
        code, out, err = ingest(self.src, self.ds)
        self.assertEqual(code, 0, err)
        orig = os.path.join(self.ds, "audio", "rec0001.wav")
        self.assertEqual(sha(orig), sha(self.src))                           # byte-for-byte
        with open(orig, "rb") as a, open(self.src, "rb") as b:
            self.assertEqual(a.read(), b.read())
        for p in (orig, os.path.join(self.ds, "derived16k", "rec0001.wav"), os.path.join(self.ds, "meta", "rec0001.json")):
            self.assertEqual(os.stat(p).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH), 0, p)   # read-only
        with wave.open(os.path.join(self.ds, "derived16k", "rec0001.wav")) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
        with open(os.path.join(self.ds, "meta", "rec0001.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["original"]["sha256"], sha(self.src))
        self.assertEqual(meta["derived"]["sha256"], sha(os.path.join(self.ds, "derived16k", "rec0001.wav")))
        self.assertEqual(meta["qa"]["verdict"], "ok")
        self.assertEqual(meta["labels"]["split"], "enrol")
        self.assertEqual(meta["ingested_utc"], "2000-01-01T00:00:00Z")
        self.assertNotIn(self.d, json.dumps(meta))                           # no local paths leak into metadata
        cols = out.strip().split(",")
        self.assertEqual(len(cols), 13)
        self.assertEqual((cols[0], cols[1], cols[-1]), ("rec0001", "audio/rec0001.wav", sha(self.src)))

    def test_never_overwrites(self):
        self.assertEqual(ingest(self.src, self.ds)[0], 0)
        before = self.listing()
        other = os.path.join(self.d, "other.wav")
        speechlike(other, seed=9)
        code, _, err = ingest(other, self.ds)                                # same recording id
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(self.listing(), before)
        # manifest conflicts: same id/path or same (speaker,session,word,repetition)
        self.assertEqual(ingest(self.src, self.ds, rid="a1", extra=("--append-manifest",))[0], 0)
        self.assertEqual(ingest(other, self.ds, rid="a2", extra=("--append-manifest",))[0], 2)    # same key as a1
        self.assertEqual(ingest(other, self.ds, rid="a2", rep=2, extra=("--append-manifest",))[0], 0)
        with open(os.path.join(self.ds, "recordings.csv"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "#s2w-recordings v1")
        self.assertEqual(len(lines), 4)
        self.assertEqual(ingest(other, self.ds, rid="a2", rep=2, extra=("--append-manifest",))[0], 2)   # a2 already ingested: refused, not duplicated

    def test_qa_rejects_stay_out_of_dataset(self):
        bad = {}
        bad["silent"] = os.path.join(self.d, "silent.wav")
        with wave.open(bad["silent"], "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)
        bad["clipped"] = os.path.join(self.d, "clipped.wav")
        speechlike(bad["clipped"], amp=3.0, clip=True)
        bad["onset"] = os.path.join(self.d, "onset.wav")
        speechlike(bad["onset"], lead=0.005)
        for name, p in bad.items():
            code, _, err = ingest(p, self.ds, rid="r_" + name, extra=("--append-manifest",))
            self.assertEqual(code, 3, name + err)
            self.assertIn("re-record", err)
        self.assertEqual(self.listing(), {})                                  # nothing at all was written
        code, _, err = ingest(bad["silent"], self.ds, rid="kept", extra=("--keep-rejected", "--append-manifest"))
        self.assertEqual(code, 3)
        listing = self.listing()
        self.assertEqual(list(listing), [os.path.join("quarantine", "kept.wav")])   # quarantine only, never audio/ or the manifest
        self.assertEqual(listing[os.path.join("quarantine", "kept.wav")], sha(bad["silent"]))

    def test_stereo_44k_input(self):
        p = os.path.join(self.d, "st.wav")
        n = speechlike(p, rate=44100, channels=2)
        self.assertEqual(ingest(p, self.ds)[0], 0)
        self.assertEqual(sha(os.path.join(self.ds, "audio", "rec0001.wav")), sha(p))     # original stays 44.1 kHz stereo
        with wave.open(os.path.join(self.ds, "audio", "rec0001.wav")) as w:
            self.assertEqual((w.getframerate(), w.getnchannels()), (44100, 2))
        with wave.open(os.path.join(self.ds, "derived16k", "rec0001.wav")) as w:
            self.assertEqual((w.getframerate(), w.getnchannels()), (16000, 1))
            self.assertAlmostEqual(w.getnframes(), int(n * 16000 / 44100), delta=1)

    def test_noisy_environment_uses_noisy_limits(self):
        def sigma(snr):
            return math.sqrt(0.4 ** 2 / 2 / 10 ** (snr / 10))
        p15, p5 = os.path.join(self.d, "snr15.wav"), os.path.join(self.d, "snr5.wav")
        speechlike(p15, sigma=sigma(15))
        speechlike(p5, sigma=sigma(5), seed=5)
        # about 15 dB: marginal under clean limits -> still ingested, but the sidecar records why
        code, _, err = ingest(p15, self.ds, rid="m1")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.ds, "meta", "m1.json"), encoding="utf-8") as f:
            qa = json.load(f)["qa"]
        self.assertEqual((qa["verdict"], qa["limits"]), ("marginal", "clean"))
        self.assertIn("low_snr", qa["flags"])
        # about 5 dB: rejected under clean limits (nothing written), accepted under the noisy limits
        before = self.listing()
        self.assertEqual(ingest(p5, self.ds, rid="r5")[0], 3)
        self.assertEqual(self.listing(), before)
        code, out, err = ingest(p5, self.ds, rid="n1", env="noisy", extra=("--noise-condition", "sim-white-5dB"))
        self.assertEqual(code, 0, err)
        self.assertIn(",noisy,sim-white-5dB,", out)
        with open(os.path.join(self.ds, "meta", "n1.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["qa"]["limits"], "noisy")
        self.assertEqual(ingest(p5, self.ds, rid="n2", rep=2, env="noisy")[0], 2)               # noisy needs a condition

    def test_bad_arguments(self):
        self.assertEqual(ingest(self.src, os.path.join(self.d, "missing"))[0], 2)
        self.assertEqual(ingest("/nonexistent.wav", self.ds)[0], 2)
        notwav = os.path.join(self.d, "x.wav")
        with open(notwav, "wb") as f:
            f.write(b"not a wav file at all, just text")
        self.assertEqual(ingest(notwav, self.ds)[0], 2)
        for kw in (dict(rid="bad id"), dict(word="a,b"), dict(rep=0), dict(split="dev"), dict(env="loud"), dict(spk="../x")):
            self.assertEqual(ingest(self.src, self.ds, **kw)[0], 2, str(kw))
        self.assertEqual(run(INGEST)[0], 2)
        self.assertEqual(self.listing(), {})



REG_HEADER = ("#s2w-consent-register v1\nparticipant_code,consent_version,information_sheet_version,consent_date,recording_and_analysis,retention_until,"
              "future_experiments,public_release,group_reporting_ok,optional_attributes_ok,status,withdrawal_date,withdrawal_action\n")


def register(path, rows):
    """rows: (code, status, recording_and_analysis)"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(REG_HEADER)
        for code, status, rec in rows:
            f.write("%s,TEST-v1,TEST-v1,2000-01-01,%s,2001-01-01,no,no,no,no,%s,,\n" % (code, rec, status))


class Consent(Base):
    """Nobody can enter the dataset without a verified consent record. Synthetic ids only; no real consent data."""

    def setUp(self):
        super().setUp()
        self.reg = os.path.join(self.d, "consent.csv")
        register(self.reg, [("spkOK", "consented", "yes"), ("spkPend", "pending", "yes"), ("spkWd", "withdrawn", "yes"), ("spkNoRec", "consented", "no")])

    def go(self, spk, rid="r1", reg=None, ds=None):
        return ingest(self.src, ds or self.ds, rid=rid, spk=spk, ses=spk + "-S1", extra=("--consent-register", reg or self.reg))

    def test_only_consented_participants_are_ingested(self):
        code, _, err = self.go("spkOK")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(self.ds, "audio", "r1.wav")))
        before = self.listing()
        for spk, expect in (("spkPend", "pending"), ("spkWd", "withdrawn"), ("spkNoRec", "recording_and_analysis"), ("spkMissing", "not in the consent register")):
            code, _, err = self.go(spk, rid="x_" + spk)
            self.assertEqual(code, 2, spk)
            self.assertIn(expect, err)
        self.assertEqual(self.listing(), before)                               # refusals write nothing at all

    def test_register_problems_refuse(self):
        self.assertEqual(self.go("spkOK", reg="/nonexistent.csv")[0], 2)
        bad = os.path.join(self.d, "bad.csv")
        with open(bad, "w") as f:
            f.write("participant_code,status\nspkOK,consented\n")            # missing tag line
        self.assertEqual(self.go("spkOK", reg=bad)[0], 2)
        nocols = os.path.join(self.d, "nocols.csv")
        with open(nocols, "w") as f:
            f.write("#s2w-consent-register v1\nparticipant_code,whatever\nspkOK,yes\n")
        self.assertEqual(self.go("spkOK", reg=nocols)[0], 2)
        self.assertEqual(self.listing(), {})

    def test_real_dataset_requires_the_register(self):
        with open(os.path.join(self.ds, "dataset_meta.txt"), "w") as f:
            f.write("schema_version=1\nkind=real\ndataset_id=x\ndataset_version=1\nprotocol=speaker_independent\n")
        code, _, err = ingest(self.src, self.ds, spk="spkOK", ses="spkOK-S1")
        self.assertEqual(code, 2)
        self.assertIn("--consent-register is required", err)
        self.assertEqual([k for k in self.listing() if k.startswith("audio")], [])
        self.assertEqual(self.go("spkOK")[0], 0)                              # with a valid register it proceeds
        self.assertEqual(self.go("spkWd", rid="r2")[0], 2)


class Pipeline(Base):
    """ingest -> dataset validator -> evaluator, on the synthetic fixture's audio"""

    def test_ingested_dataset_validates_and_evaluates(self):
        fx = os.path.join(self.d, "fx")
        subprocess.run([FIXTURE, fx], check=True, stdout=subprocess.DEVNULL)
        for name in ("dataset_meta.txt", "wordlist.csv", "participants.csv"):
            shutil.copy(os.path.join(fx, name), os.path.join(self.ds, name))
        plan = [("e1", "spkE1", "spkE1-s1", "enrol", "SYNTH_V1"), ("e2", "spkE1", "spkE1-s1", "enrol", "SYNTH_V2"),
                ("c1", "spkC1", "spkC1-s1", "calib", "SYNTH_V1"), ("c2", "spkC1", "spkC1-s1", "calib", "SYNTH_V2"),
                ("c3", "spkC1", "spkC1-s1", "calib", "SYNTH_O1")]
        for rid, spk, ses, split, word in plan:
            src = os.path.join(fx, "audio", "%s_%s_1.wav" % (spk, word))
            code, _, err = ingest(src, self.ds, rid=rid, word=word, spk=spk, ses=ses, split=split, extra=("--append-manifest",))
            self.assertEqual(code, 0, err)
        r = subprocess.run([CHECK, self.ds], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("result=VALID", r.stdout)
        self.assertIn("unpinned=0", r.stdout)                                  # ingest pinned every audio hash
        cfg = os.path.join(self.d, "s.cfg")
        with open(cfg, "w") as f:
            f.write("#s2w-settings v1\nnorm=mean\n")
        r = subprocess.run([EVAL, "run", "--dataset", self.ds, "--settings", cfg, "--split", "calib", "--out-dir", os.path.join(self.d, "runs"),
                            "--run-id", "r1", "--min-group-queries", "1", "--min-group-speakers", "1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SYNTHETIC FIXTURE RUN", r.stdout)
        # tamper with a stored original (after making it writable): the validator and evaluator both notice
        orig = os.path.join(self.ds, "audio", "c1.wav")
        os.chmod(orig, 0o644)
        with open(orig, "r+b") as f:
            f.seek(1000)
            f.write(b"\x55")
        self.assertEqual(subprocess.run([CHECK, self.ds], capture_output=True, text=True).returncode, 1)
        r = subprocess.run([EVAL, "run", "--dataset", self.ds, "--settings", cfg, "--split", "calib", "--out-dir", os.path.join(self.d, "runs"),
                            "--run-id", "r2"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
