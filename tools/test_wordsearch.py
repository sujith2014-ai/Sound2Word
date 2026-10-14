"""Tests for the joiner and the voice -> word search. ALL audio here is made-up tones, so the numbers prove the software works and nothing about
real speech or real speakers."""
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
import zlib

try:
    import numpy as np
except ImportError:                                               # the recorder itself is stdlib-only; the search and joiner need numpy
    print("wordsearch tests skipped: numpy is not installed")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_joiner as jn  # noqa: E402
import s2w_units as su  # noqa: E402
import s2w_wordsearch as ws  # noqa: E402

RATE = 16000
SYLL = ["ക", "ല", "മ", "ന", "ര", "വ", "ത", "പ", "ടി", "ഗു", "ശ", "ഷ", "സ", "ങ്ങ", "ക്കു"]


def tone(unit, dur=0.22, rate=RATE, shift=1.0, noise=0.0, seed=0):
    """A made-up 'sound' for a unit: a pitch plus two unit-specific formant-like partials, with soft edges."""
    h = zlib.crc32(unit.encode("utf-8"))
    f1, f2, f0 = 300 + (h % 700), 1200 + ((h >> 8) % 1800), 110 + ((h >> 20) % 3) * 4
    t = np.arange(int(dur * rate)) / float(rate)
    x = sum(np.sin(2 * np.pi * f0 * k * shift * t) / k for k in range(1, 6)) * 0.2
    x = x + 0.35 * np.sin(2 * np.pi * f1 * t) + 0.3 * np.sin(2 * np.pi * f2 * t)
    env = np.minimum(1.0, np.minimum(t, dur - t) / 0.02)
    x = x * env * 0.4
    if noise:
        x = x + np.random.RandomState(seed).randn(len(x)) * noise
    return x.astype(np.float32)


def write_wav(path, x, rate=RATE):
    with open(path, "wb") as f:
        f.write(jn.write_wav_bytes(x, rate))


class Joiner(unittest.TestCase):
    def test_trim_removes_silence_but_keeps_the_sound(self):
        x = np.concatenate([np.zeros(4000), tone("ക"), np.zeros(6000)]).astype(np.float32)
        y = jn.trim(x, RATE)
        self.assertLess(len(y), len(x) - 8000)
        self.assertGreater(len(y), len(tone("ക")) * 0.9)

    def test_levels_are_matched(self):
        a, b = tone("ക") * 0.05, tone("ല") * 0.5
        m = jn.match_levels([a, b])
        ra, rb = jn.voiced_rms(m[0]), jn.voiced_rms(m[1])
        self.assertLess(max(ra, rb) / min(ra, rb), 2.0)
        self.assertGreater(max(jn.voiced_rms(a), jn.voiced_rms(b)) / min(jn.voiced_rms(a), jn.voiced_rms(b)), 5.0)

    def test_crossfade_removes_the_click_a_plain_concatenation_has(self):
        a, b = tone("ക", dur=0.2), tone("ല", dur=0.2)
        a2, b2 = a.copy(), b.copy()
        a2[-1], b2[0] = 0.4, -0.4                                     # a hard step at the join
        plain = np.concatenate([a2, b2])
        joined, edges = jn.join([a2, b2], RATE, do_trim=False, do_levels=False)
        step = lambda x, i: float(np.abs(np.diff(x[i - 3:i + 3])).max())
        self.assertGreater(step(plain, len(a2)), 0.5)
        c = int(edges[1] * RATE)
        self.assertLess(step(joined, c), 0.35)
        self.assertAlmostEqual(joined[-1], 0.0, places=2)             # one fade at the very end

    def test_edges_are_in_order_and_inside_the_word(self):
        audio, edges = jn.join([tone("ക"), tone("ല"), tone("മ")], RATE)
        self.assertEqual(len(edges), 4)
        self.assertEqual(edges, sorted(edges))
        self.assertAlmostEqual(edges[-1], len(audio) / float(RATE), places=3)

    def test_join_report_flags_a_big_level_step(self):
        loud, quiet = tone("ക"), tone("ക") * 0.02
        audio, edges = jn.join([loud, quiet], RATE, do_levels=False)
        rep = jn.measure_joins(audio, edges, RATE)
        self.assertEqual(len(rep), 1)
        self.assertFalse(rep[0]["ok"])
        self.assertGreater(abs(rep[0]["rms_db"]), jn.OK_RMS_DB)
        audio2, edges2 = jn.join([tone("ക"), tone("ക")], RATE)
        self.assertTrue(jn.measure_joins(audio2, edges2, RATE)[0]["ok"])

    def test_wav_roundtrip_and_refusal_of_stereo(self):
        x = tone("ക")
        rate, y = jn.read_wav(jn.write_wav_bytes(x, RATE))
        self.assertEqual(rate, RATE)
        self.assertLess(float(np.abs(x - y).max()), 1e-3)
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"\0\0" * 200)
        with self.assertRaises(ValueError):
            jn.read_wav(buf.getvalue())

    def test_empty_input_does_not_crash(self):
        audio, edges = jn.join([], RATE)
        self.assertEqual((len(audio), edges), (0, [0.0]))


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.units = os.path.join(cls.d, "units")
        os.makedirs(cls.units)
        items = []
        for i, u in enumerate(SYLL):
            fn = "u%d.wav" % i
            write_wav(os.path.join(cls.units, fn), tone(u))
            items.append({"word_ml": u, "file": fn})
        with open(os.path.join(cls.units, "meta.json"), "w", encoding="utf-8") as mf:
            json.dump({"items": items}, mf, ensure_ascii=False)
        cls.words = ["കലം", "കമല", "നരവ", "തപക", "ലമന", "വരത", "പടി", "ടിപ", "ഗുരു", "അങ്ങല", "ക്കുമ", "മങ്ങ", "ശമ", "ഷമ", "സമ", "ലപ", "പത", "തര", "നവ", "മരപ", "വനത", "രനമ", "കതര", "പവല"]
        cls.words = [w for w in cls.words if su.decompose(w) and all(u in SYLL for u in su.decompose(w))]
        cls.lex = os.path.join(cls.d, "lexicon.csv")
        with open(cls.lex, "w", encoding="utf-8", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["word_ml", "meaning_en"])
            for w in cls.words:
                wr.writerow([w, ""])
            for w in ("കാല", "മോര"):                                      # words whose units have no clip: must be counted, not crash
                wr.writerow([w, ""])
        cls.bank = ws.load_bank(cls.units)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def query(self, word, seed=1, noise=0.01, shift=1.0, stretch=1.0, unit_dur=0.11):
        """A 'spoken' word: each unit about 0.11 s (units inside words are much shorter than the 0.22 s clips spoken alone in the bank)."""
        parts = [tone(u, dur=unit_dur * stretch, shift=shift, noise=noise, seed=seed + k) for k, u in enumerate(su.decompose(word))]
        audio, _e = jn.join(parts, RATE)
        path = os.path.join(self.d, "q_%s_%d.wav" % (zlib.crc32(word.encode()), seed))
        write_wav(path, audio)
        return path


class Search(Fixture):
    def test_there_are_enough_words_and_units(self):
        self.assertGreaterEqual(len(self.words), 18)
        self.assertEqual(len(self.bank), len(SYLL))

    def test_conjunct_units_stay_whole_in_the_search(self):
        self.assertEqual(su.decompose("അങ്ങല"), ["അ", "ങ്ങ", "ല"])
        c = ws.Composer(self.bank)
        self.assertEqual(c.units_of("മങ്ങ"), ["മ", "ങ്ങ"])
        self.assertIsNone(c.units_of("കാല"))                           # no clip for കാ

    def test_composed_search_finds_the_spoken_word_with_noise_and_a_different_speed(self):
        words = ws.load_words(self.lex)
        hits = 0
        for k, w in enumerate(self.words):
            r = ws.search(self.query(w, seed=k, noise=0.02, stretch=1.15), self.bank, words)
            self.assertEqual(r["words_without_all_unit_clips"], 2)
            if r["composed"] and r["composed"][0][0] == w:
                hits += 1
        self.assertGreaterEqual(hits / float(len(self.words)), 0.8)

    def test_look_alike_words_are_separable_in_the_synthetic_set(self):
        words = ws.load_words(self.lex)
        r = ws.search(self.query("ശമ"), self.bank, words)
        self.assertEqual(r["composed"][0][0], "ശമ")
        self.assertEqual(ws.search(self.query("ഷമ"), self.bank, words)["composed"][0][0], "ഷമ")

    def test_evaluate_reports_every_failure_and_chance(self):
        words = ws.load_words(self.lex)
        qs = [(w, self.query(w, seed=3)) for w in self.words[:6]] + [("കാല", self.query("കമല", seed=9))]
        summary, rows = ws.evaluate(qs, self.bank, words)
        self.assertEqual(summary["composed"]["n"], 7)
        self.assertEqual(summary["composed"]["not_found"], 1)           # കാല cannot be built: counted as a failure, never skipped
        self.assertEqual(len(rows), 7)

    def test_cli_search_and_eval(self):
        import contextlib
        import io
        q = self.query("കമല")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ws.main(["search", "--query", q, "--units", self.units, "--lexicon", self.lex, "--heard", "കമല"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["composed"][0][0], "കമല")
        self.assertEqual(out["fused"][0][0], "കമല")
        self.assertEqual(ws.main(["search", "--query", q, "--units", self.units + "_nope", "--lexicon", self.lex]), 2)


class DurationAndSmoothing(Fixture):
    def test_shorten_cuts_the_middle_and_keeps_a_minimum(self):
        x = tone("ക", dur=0.4)
        y = jn.shorten(x, RATE)
        self.assertLess(abs(len(y) / float(len(x)) - 0.5), 0.05)
        self.assertGreaterEqual(len(jn.shorten(tone("ക", dur=0.15), RATE)), int(RATE * jn.DEFAULTS["min_keep_s"]) - 2)
        self.assertEqual(len(jn.shorten(x, RATE, scale=1.0)), len(x))

    def test_join_shortens_only_isolated_non_final_long_units(self):
        a, b, c = tone("ക", dur=0.3), tone("ല", dur=0.3), tone("മ", dur=0.3)
        plain, _ = jn.join([a, b, c], RATE, do_levels=False)
        rule, _ = jn.join([a, b, c], RATE, do_levels=False, isolated=[True, True, True])
        in_word, _ = jn.join([a, b, c], RATE, do_levels=False, isolated=[False, False, False])
        self.assertLess(len(rule), len(plain) * 0.75)
        self.assertEqual(len(in_word), len(plain))                           # clips cut out of words are left alone
        short = [tone("ക", dur=0.12), tone("ല", dur=0.12)]
        self.assertEqual(len(jn.join(short, RATE, isolated=[True, True])[0]), len(jn.join(short, RATE)[0]))   # short clips are not shortened again

    def test_crossfade_never_swallows_a_short_unit(self):
        a, b = tone("ക", dur=0.06), tone("ല", dur=0.06)
        audio, edges = jn.join([a, b], RATE, xfade_ms=60.0, do_trim=False, do_levels=False)
        self.assertGreater(len(audio), int(RATE * 0.06) * 1.5)

    def test_smooth_features_only_touches_frames_near_the_joins(self):
        f = np.zeros((40, 3))
        f[20:] = 1.0
        g = jn.smooth_features(f, [0.0, 0.4, 0.8], win_ms=60)
        self.assertTrue(np.allclose(g[:15], f[:15]) and np.allclose(g[26:], f[26:]))
        self.assertTrue(0.0 < g[20, 0] < 1.0 and 0.0 < g[19, 0] < 1.0)

    def test_estimated_duration_follows_the_rule(self):
        on = ws.Composer(self.bank, duration="cut").est_duration("കമല")
        off = ws.Composer(self.bank, duration="none").est_duration("കമല")
        self.assertLess(on, off)
        t = ws.Composer(self.bank, duration="cut").template("കമല")
        self.assertLess(abs(on - len(t["audio"]) / float(t["rate"])), 0.05)

    def test_clips_cut_from_words_win_and_are_not_shortened(self):
        d2 = os.path.join(self.d, "units_cut")
        os.makedirs(d2)
        write_wav(os.path.join(d2, "k.wav"), tone("ക", dur=0.09))
        with open(os.path.join(d2, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"items": [{"word_ml": "ക", "file": "k.wav", "voice": "MMS-mal (VITS), cut from word 'കല'"}]}, f, ensure_ascii=False)
        bank = ws.load_bank([self.units, d2])
        self.assertTrue(bank["ക"].endswith("k.wav"))
        self.assertEqual(bank.in_word, {"ക"})
        self.assertEqual(len(bank), len(SYLL))
        bank2 = ws.load_bank([d2, self.units])                                # order does not matter: a cut-out clip beats an isolated one
        self.assertTrue(bank2["ക"].endswith("k.wav"))

    def test_rescoring_runs_on_the_label_shortlist(self):
        words = ws.load_words(self.lex)
        r = ws.search(self.query("കമല", seed=2), self.bank, words, heard="കമല", rescore_top=5)
        self.assertEqual(r["rescore_candidates"], 5)
        self.assertEqual(r["rescored"][0][0], "കമല")
        self.assertLessEqual(len({w for w, _d in r["rescored"]}), 5)

    def test_compare_configs_reports_every_configuration(self):
        words = ws.load_words(self.lex)
        qs = [(w, self.query(w, seed=k)) for k, w in enumerate(self.words[:4])]
        res = ws.compare_configs(qs, self.bank, words, {w: w for w, _p in qs})
        self.assertEqual(len(res), len(ws.CONFIGS))                           # no in-word clips in this bank: no ablation run
        self.assertTrue(next(iter(res)).startswith("plain"))                  # the plain matcher is always the first, and the default
        for name, r in res.items():
            self.assertEqual(r["summary"]["composed"]["n"], 4, name)
            self.assertIn("rescored", r["summary"])
            self.assertIn("shortlist_recall", r["summary"])

    def test_the_default_is_the_plain_matcher(self):
        c = ws.Composer(self.bank)
        self.assertEqual((c.duration, c.smooth_ms), ("none", 0.0))
        plain = c.template("കമല")
        self.assertAlmostEqual(len(plain["audio"]) / float(plain["rate"]), c.est_duration("കമല"), delta=0.05)
        with self.assertRaises(ValueError):
            ws.Composer(self.bank, duration="stretch")

    def test_squeeze_keeps_all_audio_but_shortens_the_feature_frames(self):
        plain = ws.Composer(self.bank).template("കമല")
        sq = ws.Composer(self.bank, duration="squeeze").template("കമല")
        cut = ws.Composer(self.bank, duration="cut").template("കമല")
        self.assertEqual(len(sq["audio"]), len(plain["audio"]))               # nothing removed from the audio
        self.assertLess(len(sq["feat"]), len(plain["feat"]) * 0.8)
        self.assertLess(len(cut["audio"]), len(plain["audio"]) * 0.8)         # the cut variant does remove audio

    def test_shortlist_recall_timing_and_speakers_are_reported(self):
        words = ws.load_words(self.lex)
        qs = [(w, self.query(w, seed=k), "S%d" % (k % 2)) for k, w in enumerate(self.words[:6])]
        heard = {w: w for w, _p, _s in qs}
        heard[self.words[0]] = "ശമ"                                           # a recogniser error: the right word may fall out of the shortlist
        summary, rows = ws.evaluate(qs, self.bank, words, heard)
        rec = summary["shortlist_recall"]
        self.assertEqual(rec["n"], 6)
        self.assertTrue(all(0.0 <= rec["top%d" % k] <= 1.0 for k in ws.RECALL_AT))
        self.assertLessEqual(rec["top10"], rec["top50"])
        self.assertLessEqual(rec["top100"], rec["top200"])
        self.assertEqual(set(summary["composed"]["top1_by_speaker"]), {"S0", "S1"})
        self.assertGreaterEqual(summary["composed"]["mean_ms"], 0.0)
        self.assertTrue(all("ms_composed" in r for r in rows))

    def test_rescoring_cannot_recover_a_word_outside_its_shortlist(self):
        words = ws.load_words(self.lex)
        q = self.query("കമല", seed=2)
        r = ws.search(q, self.bank, words, heard="ശമ", rescore_top=2)         # the heard text points at other words
        self.assertNotIn("കമല", [w for w, _d in r["rescored"]])
        self.assertEqual(r["composed"][0][0], "കമല")                          # the plain composed ranking still finds it

    def test_ablation_without_in_word_clips_is_run_when_the_bank_has_them(self):
        d2 = os.path.join(self.d, "units_cut2")
        os.makedirs(d2)
        write_wav(os.path.join(d2, "k.wav"), tone("ക", dur=0.09))
        with open(os.path.join(d2, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"items": [{"word_ml": "ക", "file": "k.wav", "voice": "MMS-mal (VITS), cut from word 'കല'"}]}, f, ensure_ascii=False)
        bank = ws.load_bank([self.units, d2])
        words = ws.load_words(self.lex)
        res = ws.compare_configs([("കമല", self.query("കമല", seed=1))], bank, words)
        self.assertEqual(len(res), len(ws.CONFIGS) + 1)
        self.assertTrue(any(k.startswith("ablation") for k in res))
        self.assertEqual(ws.load_bank([self.units, d2], use_in_word=False).in_word, set())


class RecorderIntegration(Fixture):
    def test_voice_search_through_the_recorder_and_its_http_api(self):
        import s2w_recorder as r
        import threading
        import urllib.request
        rdir = os.path.join(self.d, "rec")
        words = [r.parse_word("കമല|kamala"), r.parse_word("നരവ|narava")]
        rec = r.Recorder(rdir, words, "build", None, None, None, self.units, None, self.lex)
        os.makedirs(os.path.join(rdir, "kamala"))
        q = self.query("കമല", seed=4)
        shutil.copy(q, os.path.join(rdir, "kamala", "take_001.wav"))
        res = rec.voice_search("kamala", "take_001.wav", "കമല")
        self.assertEqual(res["composed"][0][0], "കമല")
        self.assertEqual(res["fused"][0][0], "കമല")
        self.assertEqual(res["query"]["word"], "kamala")
        self.assertIn("Unverified", res["note"])
        with self.assertRaises(r.v.ViewerError):
            rec.voice_search("kamala", "take_999.wav")
        with self.assertRaises(r.v.ViewerError):
            rec.voice_search("kamala", "take_001.wav", "hello")                 # not Malayalam script
        plain = r.Recorder(os.path.join(self.d, "rec2"), words, "build")
        with self.assertRaises(r.v.ViewerError):
            plain.voice_search("kamala", "take_001.wav")                        # no unit clips
        self.assertTrue(rec.unit_join("kamala")[0][:4] == b"RIFF")
        self.assertEqual(len(rec.last_join_report), 2)

        srv = r.make_server(rec)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            page = urllib.request.urlopen(base + "/search").read().decode()
            self.assertIn("Search the dictionary by voice", page)
            with urllib.request.urlopen(base + "/api/search?word=kamala&take=take_001.wav") as resp:
                j = json.loads(resp.read())
            self.assertEqual(j["composed"][0][0], "കമല")
            self.assertEqual(j["labels"], [])
        finally:
            srv.shutdown()
            srv.server_close()


class Labels(unittest.TestCase):
    W = [("ശരി", 5.0), ("ഷരി", 1.0), ("സരി", 1.0), ("നാവ്", 3.0), ("ണാവ്", 0.0), ("അങ്ങനെ", 4.0), ("അനങ്ങനെ", 0.0), ("കണ്ണ്", 2.0), ("കണ്ടു", 2.0)]

    def test_exact_text_ranks_first(self):
        for w, _u in self.W:
            self.assertEqual(ws.rank_labels(w, self.W)[0][0], w)

    def test_look_alike_letters_cost_less_than_unrelated_ones(self):
        self.assertLess(ws.unit_distance(["ശ", "രി"], ["ഷ", "രി"]), ws.unit_distance(["ശ", "രി"], ["മ", "രി"]))
        self.assertLess(ws.unit_distance(["ന", "ാ"], ["ണ", "ാ"]), 1.0)
        self.assertEqual(ws.unit_distance(["ങ്ങ"], ["ങ്ങ"]), 0.0)

    def test_conjunct_is_one_unit_never_two_letters(self):
        self.assertEqual(su.decompose("അങ്ങനെ"), ["അ", "ങ്ങ", "നെ"])
        # a recogniser text with ങ ങ (two letters) is farther from അങ്ങനെ than the exact conjunct
        near = ws.rank_labels("അങ്ങനെ", self.W)
        self.assertEqual(near[0][0], "അങ്ങനെ")
        self.assertGreater(dict(near)["അനങ്ങനെ"], near[0][1])

    def test_a_heard_look_alike_still_finds_the_word_in_the_top_three(self):
        r = [w for w, _d in ws.rank_labels("ഷരി", self.W)]
        self.assertIn("ശരി", r[:3])

    def test_non_malayalam_text_is_refused(self):
        with self.assertRaises(ValueError):
            ws.rank_labels("hello", self.W)

    def test_fusion_prefers_a_word_both_methods_like(self):
        a = [("x", 1), ("y", 2), ("z", 3)]
        b = [("y", 1), ("x", 2), ("w", 3)]
        self.assertEqual(ws.fuse(a, b)[0][0], "x")
        self.assertEqual({w for w, _s in ws.fuse(a, b)}, {"x", "y", "z", "w"})


class Features(unittest.TestCase):
    def test_mfcc_shape_mean_and_variance_normalised(self):
        f = ws.mfcc(tone("ക", dur=0.4), RATE)
        self.assertEqual(f.shape[1], ws.NC)
        self.assertLess(float(np.abs(f.mean(axis=0)).max()), 1e-6)
        self.assertLess(float(np.abs(f.std(axis=0) - 1.0).max()), 0.05)

    def test_other_sample_rates_are_resampled(self):
        a = ws.mfcc(tone("ക", dur=0.4), RATE)
        b = ws.mfcc(jn.resample(tone("ക", dur=0.4), RATE, 22050), 22050)
        self.assertLess(abs(len(a) - len(b)), 3)

    def test_dtw_is_zero_for_equal_and_grows_with_difference(self):
        a = ws.mfcc(tone("ക", dur=0.4), RATE)
        b = ws.mfcc(tone("ല", dur=0.4), RATE)
        self.assertAlmostEqual(ws.dtw(a, a), 0.0, places=6)
        self.assertGreater(ws.dtw(a, b), 0.5)


if __name__ == "__main__":
    unittest.main()
