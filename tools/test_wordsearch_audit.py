"""Regression tests for the defects found by the reference-driven audit (docs/DEFECT_AUDIT.md). Each test is named D<number> after the audit entry
and was written to FAIL on the code before the fix. All audio is made-up tones: this validates program behaviour, never recognition accuracy."""
import contextlib
import io
import json
import os
import shutil
import sys
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_wordsearch as base  # noqa: E402

ws, jn, tone, write_wav, SYLL = base.ws, base.jn, base.tone, base.write_wav, base.SYLL


class Audit(base.Fixture):
    def person_folder(self, name, speaker, units=("ക",), extra=None):
        d2 = os.path.join(self.d, name)
        os.makedirs(d2, exist_ok=True)
        items = []
        for i, u in enumerate(units):
            write_wav(os.path.join(d2, "p%d.wav" % i), tone(u, dur=0.2))
            it = {"word_ml": u, "file": "p%d.wav" % i}
            if speaker is not None:
                it["speaker"] = speaker
            it.update(extra or {})
            items.append(it)
        with open(os.path.join(d2, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False)
        return d2

    def q(self, word, seed=1, **kw):
        return self.query(word, seed=seed, **kw)

    # D1 speaker IDs must be compared after normalisation
    def test_D1_speaker_ids_differing_only_in_case_or_spaces_are_the_same_person(self):
        bank = ws.load_bank([self.units, self.person_folder("d1", "P1")])
        words = ws.load_words(self.lex)
        for variant in (" p1 ", "p1", "P1 "):
            qs = [("കമല", self.q("കമല"), variant)]
            self.assertEqual(len(ws.speaker_leaks(qs, bank)), 1, repr(variant))
            with self.assertRaises(ws.SpeakerLeak):
                ws.evaluate(qs, bank, words)

    # D2 / D3 out-of-dictionary and normalisation
    def test_D2_out_of_dictionary_queries_do_not_lower_the_closed_dictionary_score(self):
        words = ws.load_words(self.lex)
        good = [(w, self.q(w, seed=k), "S1") for k, w in enumerate(self.words[:5])]
        ood = [("ഗുരു", self.q("ഗുരു", seed=40), "S1")]
        s_good, _ = ws.evaluate(good, self.bank, words)
        s_mix, _ = ws.evaluate(good + ood, self.bank, words)
        self.assertEqual(s_mix["composed"]["n"], s_good["composed"]["n"])
        self.assertEqual(s_mix["composed"]["top1"], s_good["composed"]["top1"])
        self.assertEqual(s_mix["queries"], {"total": 6, "in_dictionary": 5, "out_of_dictionary": 1, "unusable": 0})

    def test_D3_dictionary_membership_is_decided_after_unicode_normalisation(self):
        w = "കൊല"                                                       # the vowel sign ൊ has a decomposed form (U+0D46 U+0D3E)
        nfd = unicodedata.normalize("NFD", w)
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        _s, rows = ws.evaluate([(nfd, self.q("കമല"), "S1")], self.bank, [(unicodedata.normalize("NFC", w), 0.0)])
        self.assertTrue(rows[0]["in_dictionary"])

    # D4 shortlist recall: in-dictionary only, at the configured size, plus where the composed method loses the word
    def test_D4_shortlist_recall_ignores_out_of_dictionary_queries_and_reports_the_configured_size(self):
        words = ws.load_words(self.lex)
        qs = [("കമല", self.q("കമല"), "S1"), ("ഗുരു", self.q("ഗുരു", seed=9), "S1")]
        summary, _rows = ws.evaluate(qs, self.bank, words, heard={"കമല": "കമല", "ഗുരു": "ഗുരു"}, rescore_top=7)
        rec = summary["shortlist_recall"]
        self.assertEqual(rec["n"], 1)
        self.assertIn("top7", rec)
        self.assertEqual(rec["top7"], 1.0)

    def test_D4_the_report_says_where_the_composed_method_lost_the_word(self):
        words = ws.load_words(self.lex)
        qs = [("കമല", self.q("കമല"), "S1"), ("കാല", self.q("കമല", seed=3), "S1")]       # കാല: no clip for കാ
        summary, rows = ws.evaluate(qs, self.bank, words)
        st = summary["composed_stages"]
        self.assertEqual(st["found"], 1)
        self.assertEqual(rows[0]["lost_at"], None)
        self.assertEqual(rows[1]["lost_at"], "no_clips")                # in the dictionary, but no clip exists for കാ

    # D5 silent truncation of the candidate list
    def test_D5_template_limit_is_reported_not_silent(self):
        words = [w for w, _u in ws.load_words(self.lex)]
        comp = ws.Composer(self.bank)
        qf = ws.mfcc(jn.read_wav(self.q("കമല"))[1], base.RATE)
        out = ws.rank_composed(qf, comp, words, max_templates=3)
        self.assertEqual(len(out), 4)                                  # (ranking, candidates, missing, info)
        self.assertGreater(out[3]["templates_truncated"], 0)
        full = ws.rank_composed(qf, comp, words)
        self.assertEqual(full[3]["templates_truncated"], 0)

    # D6 one unusable query must not abort the whole evaluation, and it counts as a miss
    def test_D6_an_unusable_query_is_a_counted_miss_not_a_crash(self):
        words = ws.load_words(self.lex)
        bad = os.path.join(self.d, "bad.wav")
        with open(bad, "wb") as f:
            f.write(b"not a wav file")
        qs = [("കമല", self.q("കമല"), "S1"), ("നവ", bad, "S1")]
        summary, rows = ws.evaluate(qs, self.bank, words)
        self.assertEqual(summary["queries"]["unusable"], 1)
        self.assertEqual(summary["composed"]["n"], 2)                  # the unusable query stays in the denominator
        self.assertEqual(summary["composed"]["not_found"], 1)
        self.assertIn("error", rows[1])

    # D7 no unjustified unseen-speaker claim
    def test_D7_unseen_speaker_is_not_claimed_when_reference_provenance_is_unknown(self):
        d2 = self.person_folder("d7", None)                            # a reference clip with neither a speaker nor a synthetic mark
        bank = ws.load_bank([d2])
        words = ws.load_words(self.lex)
        summary, rows = ws.evaluate([("കമല", self.q("കമല"), "P9")], bank, words)
        self.assertEqual(rows[0]["condition"], "unseen_unverified")
        self.assertNotIn("unseen_speaker", summary["by_condition"])
        self.assertFalse(summary["unseen_speaker_claim_valid"])

    def test_D7_clean_metadata_still_gives_a_valid_unseen_claim(self):
        clean = ws.load_bank([self.person_folder("d7c", "P1")])
        words = ws.load_words(self.lex)
        summary, rows = ws.evaluate([("കമല", self.q("കമല"), "P9")], clean, words)
        self.assertEqual(rows[0]["condition"], "unseen_speaker")
        self.assertTrue(summary["unseen_speaker_claim_valid"])

    # D8 the isolated-clips ablation must classify speakers exactly like the full bank
    def test_D8_ablation_bank_keeps_every_reference_speaker(self):
        d_cut = self.person_folder("d8", "P1", extra={"voice": "MMS cut from word 'കല'"})
        bank = ws.load_bank([self.units, d_cut])
        self.assertIn(ws.speaker_key("P1"), bank.speakers)
        words = ws.load_words(self.lex)
        qs = [("കമല", self.q("കമല"), "P1")]
        with self.assertRaises(ws.SpeakerLeak):
            ws.compare_configs(qs, bank, words)                        # every configuration refuses, including the isolated-only ablation
        res = ws.compare_configs(qs, bank, words, allow_same_speaker=True)
        conds = {name: {r["condition"] for r in v["per_word"]} for name, v in res.items()}
        self.assertTrue(any(k.startswith("ablation") for k in conds))
        self.assertEqual({tuple(sorted(c)) for c in conds.values()}, {("seen_speaker",)})

    # D9 reference clips that cannot be loaded are reported, and their speaker still counts
    def test_D9_missing_reference_files_are_reported_and_the_speaker_is_still_registered(self):
        d2 = self.person_folder("d9", "P1")
        os.remove(os.path.join(d2, "p0.wav"))
        bank = ws.load_bank([self.units, d2])
        rep = ws.metadata_report(bank)
        self.assertEqual(rep["skipped_clips"], 1)
        self.assertFalse(rep["ok"])
        self.assertEqual(len(ws.speaker_leaks([("കമല", "x.wav", "P1")], bank)), 1)

    # D10 heard-text bookkeeping
    def test_D10_queries_without_recogniser_text_are_counted(self):
        words = ws.load_words(self.lex)
        qs = [("കമല", self.q("കമല"), "S1"), ("നവ", self.q("നവ", seed=2), "S1")]
        summary, _rows = ws.evaluate(qs, self.bank, words, heard={"കമല": "കമല"})
        self.assertEqual(summary["missing_heard"], 1)
        nfd = unicodedata.normalize("NFD", "കമല")
        s2, _ = ws.evaluate(qs[:1], self.bank, words, heard={nfd: "കമല"})
        self.assertEqual(s2["missing_heard"], 0)

    # D14 unusable recogniser text
    def test_D14_recogniser_text_that_is_not_malayalam_is_a_counted_miss_not_an_abort(self):
        words = ws.load_words(self.lex)
        qs = [("കമല", self.q("കമല"), "S1"), ("നവ", self.q("നവ", seed=2), "S1")]
        summary, rows = ws.evaluate(qs, self.bank, words, heard={"കമല": "hello world", "നവ": "നവ"})
        self.assertEqual(summary["heard_unusable"], 1)
        self.assertIn("heard_error", rows[0])
        self.assertIsNone(rows[0]["labels"])
        self.assertEqual(summary["labels"]["n"], 2)                      # stays in the denominator as a miss
        self.assertEqual(summary["labels"]["not_found"], 1)
        self.assertEqual(rows[1]["labels"], 1)

    # D13 invalid settings are refused, not ignored
    def test_D13_negative_smoothing_and_non_positive_sizes_are_refused(self):
        with self.assertRaises(ValueError):
            ws.Composer(self.bank, smooth_ms=-5)
        words = ws.load_words(self.lex)
        qs = [("കമല", self.q("കമല"), "S1")]
        with self.assertRaises(ValueError):
            ws.evaluate(qs, self.bank, words, heard={"കമല": "കമല"}, rescore_top=0)
        with self.assertRaises(ValueError):
            ws.evaluate(qs, self.bank, words, shortlist=0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws.main(["search", "--query", self.q("കമല"), "--units", self.units, "--lexicon", self.lex, "--smooth-ms", "-5"])
        self.assertEqual(rc, 2)

    # D20 identical audio is a leak whatever the metadata says
    def test_D20_a_query_that_is_a_copy_of_a_reference_clip_is_refused(self):
        d2 = self.person_folder("d20", None)                           # no speaker metadata at all
        bank = ws.load_bank([d2])
        words = ws.load_words(self.lex)
        copy = os.path.join(self.d, "copy_of_reference.wav")
        shutil.copy(os.path.join(d2, "p0.wav"), copy)
        with self.assertRaises(ws.SpeakerLeak):
            ws.evaluate([("കമല", copy, "P9")], bank, words)
        _s, rows = ws.evaluate([("കമല", copy, "P9")], bank, words, allow_same_speaker=True)
        self.assertEqual(rows[0]["condition"], "seen_speaker")
        self.assertTrue(rows[0]["audio_in_reference"])


class FrozenConfiguration(base.Fixture):
    """Gate C preparation: a freeze file that pins settings and every input by hash, refuses an invalid setup, and detects any later change."""

    def queries(self, name, items):
        qd = os.path.join(self.d, name)
        os.makedirs(qd, exist_ok=True)
        meta = []
        for i, (w, spk) in enumerate(items):
            fn = "q%d.wav" % i
            shutil.copy(self.query(w, seed=100 + i), os.path.join(qd, fn))
            it = {"word_ml": w, "file": fn}
            if spk:
                it["speaker"] = spk
            meta.append(it)
        with open(os.path.join(qd, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"items": meta}, f, ensure_ascii=False)
        return qd

    def cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ws.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def freeze(self, qd, cfg, *extra):
        return self.cli(["freeze", "--queries", qd, "--units", self.units, "--lexicon", self.lex, "--out", cfg, "--allow-dirty", *extra])

    def test_F1_a_valid_setup_freezes_and_verifies(self):
        qd = self.queries("f1q", [("കമല", "S1"), ("നവ", "S2")])
        cfg = os.path.join(self.d, "f1.json")
        rc, out, err = self.freeze(qd, cfg)
        self.assertEqual(rc, 0, err)
        c = json.load(open(cfg, encoding="utf-8"))
        self.assertEqual(c["schema"], ws.FREEZE_SCHEMA)
        self.assertTrue(c["evidence"].startswith("SYNTHETIC"))
        self.assertEqual(ws.verify_frozen(c, [self.units], self.lex, qd), [])

    def test_F2_an_invalid_setup_is_never_frozen(self):
        cfg = os.path.join(self.d, "f2.json")
        rc, _o, err = self.freeze(self.queries("f2a", [("കമല", None)]), cfg)                         # a query without a speaker ID
        self.assertEqual(rc, 7)
        self.assertIn("no speaker ID", err)
        self.assertFalse(os.path.exists(cfg))
        d2 = os.path.join(self.d, "f2_ref")
        os.makedirs(d2)
        write_wav(os.path.join(d2, "a.wav"), tone("ക", dur=0.2))
        with open(os.path.join(d2, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"items": [{"word_ml": "ക", "file": "a.wav", "speaker": "S1"}]}, f, ensure_ascii=False)
        rc, _o, err = self.cli(["freeze", "--queries", self.queries("f2b", [("കമല", "S1")]), "--units", self.units, d2, "--lexicon", self.lex, "--out", cfg, "--allow-dirty"])
        self.assertEqual(rc, 7)
        self.assertIn("leak", err)
        rc, _o, err = self.cli(["freeze", "--queries", self.queries("f2c", []), "--units", self.units, "--lexicon", self.lex, "--out", cfg, "--allow-dirty"])
        self.assertEqual(rc, 7)
        self.assertIn("no query clips", err)

    def test_F3_incomplete_reference_provenance_blocks_the_freeze(self):
        d2 = os.path.join(self.d, "f3_ref")
        os.makedirs(d2)
        write_wav(os.path.join(d2, "a.wav"), tone("ക", dur=0.2))
        with open(os.path.join(d2, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"items": [{"word_ml": "ക", "file": "a.wav"}]}, f, ensure_ascii=False)          # no speaker, not synthetic
        rc, _o, err = self.cli(["freeze", "--queries", self.queries("f3q", [("കമല", "S9")]), "--units", self.units, d2, "--lexicon", self.lex,
                                "--out", os.path.join(self.d, "f3.json"), "--allow-dirty"])
        self.assertEqual(rc, 7)
        self.assertIn("provenance is incomplete", err)

    def test_F4_any_change_after_the_freeze_is_refused(self):
        qd = self.queries("f4q", [("കമല", "S1"), ("നവ", "S2")])
        cfg = os.path.join(self.d, "f4.json")
        self.assertEqual(self.freeze(qd, cfg)[0], 0)
        args = ["eval", "--queries", qd, "--units", self.units, "--lexicon", self.lex, "--frozen", cfg]
        rc, out, err = self.cli(args)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["frozen"]["config_sha256"], json.load(open(cfg, encoding="utf-8"))["config_sha256"])
        shutil.copy(self.query("തര", seed=77), os.path.join(qd, "q1.wav"))                            # a query clip replaced
        rc, _o, err = self.cli(args)
        self.assertEqual((rc, "queries_sha256 changed" in err), (5, True))
        qd2 = self.queries("f4q2", [("കമല", "S1"), ("നവ", "S2")])
        cfg2 = os.path.join(self.d, "f4b.json")
        self.assertEqual(self.freeze(qd2, cfg2)[0], 0)
        c = json.load(open(cfg2, encoding="utf-8"))
        c["settings"]["duration"] = "cut"                                                            # the settings edited by hand
        json.dump(c, open(cfg2, "w", encoding="utf-8"))
        rc, _o, err = self.cli(["eval", "--queries", qd2, "--units", self.units, "--lexicon", self.lex, "--frozen", cfg2])
        self.assertEqual((rc, "edited after freezing" in err), (5, True))
        with open(self.lex, "a", encoding="utf-8") as f:                                             # the dictionary changed
            f.write("പവ,\n")
        rc, _o, err = self.cli(args[:-1] + [cfg])
        self.assertEqual(rc, 5)
        self.assertIn("lexicon_sha256 changed", err)

    def test_F5_a_second_configuration_on_the_same_queries_is_test_reuse(self):
        qd = self.queries("f5q", [("കമല", "S1"), ("നവ", "S2")])
        a, b = os.path.join(self.d, "f5a.json"), os.path.join(self.d, "f5b.json")
        self.assertEqual(self.freeze(qd, a)[0], 0)
        self.assertEqual(self.freeze(qd, b, "--duration", "squeeze")[0], 0)
        ev = lambda c, *x: self.cli(["eval", "--queries", qd, "--units", self.units, "--lexicon", self.lex, "--frozen", c, *x])
        self.assertEqual(ev(a)[0], 0)
        self.assertEqual(ev(a)[0], 0)                                                                # the identical configuration may be re-run
        rc, _o, err = ev(b)
        self.assertEqual((rc, "test reuse" in err), (6, True))
        rc, out, _e = ev(b, "--allow-test-reuse")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["frozen"]["test_reuse"])

    def test_F7_gate_c_rejects_a_synthetic_freeze_and_a_changed_input(self):
        import gate_status as gs
        qd = self.queries("f7q", [("കമല", "S1")])
        cfg = os.path.join(self.d, "f7.json")
        self.assertEqual(self.freeze(qd, cfg)[0], 0)
        a = type("A", (), {"voice_freeze": cfg, "units": [self.units], "lexicon": self.lex, "queries": qd})()
        c = gs.voice_freeze_check(a)
        self.assertEqual(c["state"], gs.FAIL)
        self.assertIn("SYNTHETIC", c["detail"])                                                       # intact, but synthetic data cannot complete Gate C
        shutil.copy(self.query("തര", seed=5), os.path.join(qd, "q0.wav"))
        self.assertIn("queries_sha256 changed", gs.voice_freeze_check(a)["detail"])

    def test_F6_frozen_settings_cannot_be_combined_with_a_comparison_run(self):
        qd = self.queries("f6q", [("കമല", "S1")])
        cfg = os.path.join(self.d, "f6.json")
        self.assertEqual(self.freeze(qd, cfg)[0], 0)
        rc, _o, err = self.cli(["eval", "--queries", qd, "--units", self.units, "--lexicon", self.lex, "--frozen", cfg, "--compare-configs"])
        self.assertEqual(rc, 2)
        self.assertIn("--frozen cannot be combined", err)


if __name__ == "__main__":
    unittest.main()
