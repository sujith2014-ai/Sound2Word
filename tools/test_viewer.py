"""Tests for tools/s2w_viewer.py (stdlib unittest). Uses the SYNTHETIC fixture only."""
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave

BUILD = os.environ.get("S2W_BUILD", "build")
NEEDED = ("s2w_make_fixture", "s2w_eval", "s2w_features", "s2w_qa", "s2w_match")
if not all(os.path.exists(os.path.join(BUILD, n)) for n in NEEDED):
    print("viewer tests skipped: C tools not built")
    sys.exit(0)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_viewer as v  # noqa: E402


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree_hash(root):
    h = hashlib.sha256()
    for d, _, fs in sorted(os.walk(root)):
        for n in sorted(fs):
            h.update(n.encode())
            h.update(sha(os.path.join(d, n)).encode())
    return h.hexdigest()


class Pure(unittest.TestCase):
    def test_peaks_match_brute_force(self):
        s = [(-1) ** i * (i * 37 % 30000) for i in range(1000)]
        p = v.peaks(s, 10)
        self.assertEqual(len(p), 10)
        for b, (lo, hi) in enumerate(p):
            seg = s[b * 100:(b + 1) * 100]
            self.assertAlmostEqual(lo, min(seg) / 32768.0)
            self.assertAlmostEqual(hi, max(seg) / 32768.0)

    def test_peaks_short_input(self):
        self.assertEqual(len(v.peaks([5, -5, 7], 1200)), 3)

    def test_f0_of_150hz_tone(self):
        if v.np is None:
            self.skipTest("numpy not installed")
        s = [int(8000 * math.sin(2 * math.pi * 150 * i / 16000)) for i in range(16000)]
        f = [x for x in v.f0_track(s, 16000) if x]
        self.assertTrue(len(f) > 50)
        self.assertTrue(all(abs(x - 150) < 6 for x in f))

    def test_segments_of_three_tone_signal(self):
        # mel-like matrix: strongest bin 5, then 15, then 25, with silence around
        mat = [[-20.0] * 40 for _ in range(10)]
        for b, n in ((5, 20), (15, 20), (25, 20)):
            for _ in range(n):
                r = [-20.0] * 40
                r[b] = 5.0
                mat.append(r)
        mat += [[-20.0] * 40 for _ in range(10)]
        seg = v.estimate_segments(mat)
        self.assertEqual(len(seg), 3)
        self.assertAlmostEqual(seg[0]["start"], 0.10)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ds = os.path.join(cls.tmp, "ds")
        subprocess.run([os.path.join(BUILD, "s2w_make_fixture"), cls.ds], check=True, capture_output=True)
        subprocess.run([os.path.join(BUILD, "s2w_dataset_check"), cls.ds, "--emit-hashes"], capture_output=True)
        cfg = os.path.join(cls.tmp, "base.cfg")
        with open(cfg, "w") as f:
            f.write("#s2w-settings v1\n")
        subprocess.run([os.path.join(BUILD, "s2w_eval"), "run", "--dataset", cls.ds, "--settings", cfg, "--split", "calib", "--out-dir", os.path.join(cls.tmp, "runs"),
                        "--run-id", "t"], check=True, capture_output=True)
        cls.run_dir = os.path.join(cls.tmp, "runs", "t")
        cls.app = v.App(cls.ds, cls.run_dir, BUILD)
        cls.srv = v.make_server(cls.app, 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.before = (tree_hash(cls.ds), tree_hash(cls.run_dir))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as r:
            return r.read()

    def err(self, path, method="GET"):
        try:
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method, data=b"x" if method != "GET" else None))
        except urllib.error.HTTPError as e:
            return e.code
        return 200

    def test_index_counts_and_synthetic_banner_data(self):
        idx = json.loads(self.get("/api/index"))
        self.assertEqual(idx["kind"], "synthetic")
        self.assertEqual(len(idx["queries"]), len(self.app.queries))

    def test_recording_is_real_audio_analysis(self):
        rid = self.app.queries[0]["recording_id"]
        d = json.loads(self.get("/api/recording/" + rid))
        with wave.open(self.app.wav_path(rid)) as w:
            self.assertEqual(d["n_samples"], w.getnframes())
            self.assertAlmostEqual(d["duration"], w.getnframes() / w.getframerate())
        self.assertEqual(d["spec"]["cols"], 40)
        self.assertEqual(len(d["segments"]), 3)               # SYNTH_V1 has three tones
        self.assertIsNone(d["f0"])                            # F0 hidden for synthetic tones
        self.assertEqual(d["qa"]["verdict"], "ok")

    def test_audio_endpoint_returns_identical_bytes(self):
        rid = self.app.queries[0]["recording_id"]
        with open(self.app.wav_path(rid), "rb") as f:
            self.assertEqual(self.get("/audio/" + rid), f.read())

    def test_unknown_ids_and_traversal_refused(self):
        for p in ("/audio/nope", "/audio/..%2F..%2Fetc%2Fpasswd", "/api/recording/..%2Frecordings.csv", "/api/match/nope"):
            self.assertEqual(self.err(p), 404, p)

    def test_match_ranks_same_word_first_and_agrees_with_engine(self):
        q = next(x for x in self.app.queries if x["word_id"] == "SYNTH_V1")
        m = json.loads(self.get("/api/match/" + q["recording_id"]))
        self.assertEqual(m["ranks"][0]["word"], "SYNTH_V1")
        self.assertEqual(self.app.label_to_word[q["top1"]], m["ranks"][0]["word"])
        self.assertTrue(all(m["ranks"][i]["dist"] <= m["ranks"][i + 1]["dist"] for i in range(len(m["ranks"]) - 1)))

    def test_read_only(self):
        for method in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.err("/api/index", method), 405)
        self.get("/api/recording/" + self.app.queries[0]["recording_id"])
        self.get("/api/match/" + self.app.queries[0]["recording_id"])
        self.assertEqual((tree_hash(self.ds), tree_hash(self.run_dir)), self.before)

    def test_verification_cannot_approve(self):
        v_ = json.loads(self.get("/api/verification"))
        self.assertFalse(v_["available"])                     # no workbook supplied
        self.assertIn("cannot approve", v_["note"])


if __name__ == "__main__":
    unittest.main()
