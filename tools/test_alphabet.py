"""Tests for tools/s2w_alphabet.py and the recorder's /alphabet page. MADE-UP clips and lexicon only (tones and band-limited noise):
no real speech, no real TTS output. They check the definitions, the look-alike detection, the measurements and the human-verdict
store; they cannot check that any clip sounds like its label (only a listener can)."""
import csv
import hashlib
import http.client
import io
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import unicodedata
import unittest
import wave

BUILD = os.environ.get("S2W_BUILD", "build")
if not all(os.path.exists(os.path.join(BUILD, n)) for n in ("s2w_features", "s2w_qa", "s2w_match")):
    print("alphabet tests skipped: C tools not built")
    sys.exit(0)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_alphabet as al  # noqa: E402
import s2w_recorder as r  # noqa: E402
import s2w_viewer as v  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "s2w_alphabet.py")


def noise_wav(lo, hi, seconds=0.4, rate=16000, seed=1, amp=6000):
    """Band-limited noise made of random-phase tones between lo and hi Hz (a stand-in for a fricative of that brightness)."""
    rng = random.Random(seed)
    tones = [(rng.uniform(lo, hi), rng.uniform(0, 6.28)) for _ in range(60)]
    n = int(seconds * rate)
    x = [sum(math.sin(2 * math.pi * f * i / rate + ph) for f, ph in tones) for i in range(n)]
    peak = max(abs(a) for a in x) or 1
    env = [min(1.0, i / 800, (n - i) / 800) for i in range(n)]
    pcm = [int(amp * 1.6 * a / peak * e) for a, e in zip(x, env)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(p.to_bytes(2, "little", signed=True) for p in pcm))
    return buf.getvalue()


def put_clips(root, voice, table):
    """root/voice/meta.json + wavs; table = {text: wav bytes}. Mirrors tools/model_eval/gen_units.py output."""
    d = os.path.join(root, voice)
    os.makedirs(d, exist_ok=True)
    items = []
    for i, (text, data) in enumerate(table.items()):
        fn = "u%04d.wav" % i
        with open(os.path.join(d, fn), "wb") as f:
            f.write(data)
        items.append({"unit_id": i, "word_ml": text, "file": fn, "voice": voice, "model": "made-up-" + voice, "synthetic": True})
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False)


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Inventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = al.build_inventory()
        cls.by = {i["text"]: i for i in cls.items}

    def test_unicode_block_partitions_exactly(self):
        u = al.unicode_block()
        assigned = [c for c in range(0x0D00, 0x0D80) if unicodedata.name(chr(c), None)]
        self.assertEqual(u["assigned"], len(assigned))
        self.assertEqual(sum(g["count"] for g in u["groups"]), len(assigned))
        seen = [c for g in u["groups"] for c in g["codes"]]
        self.assertEqual(len(seen), len(set(seen)))                       # every code point in exactly one group
        self.assertEqual(sorted(int(c[2:], 16) for c in seen), assigned)
        names = {g["name"] for g in u["groups"]}
        self.assertTrue({"independent vowel letters", "consonant letters (modern)", "chillu letters"} <= names)

    def test_definitions_are_computed_from_unicode_not_typed_in(self):
        modern = [c for c in range(0x0D15, 0x0D3A) if c != 0x0D29]
        self.assertEqual(set(al.CONS), {chr(c) for c in modern})          # the curated table covers exactly the modern consonant letters
        self.assertEqual(len(al.CONS), len(modern))
        self.assertTrue(all(unicodedata.name(c).startswith("MALAYALAM LETTER") for c in al.CONS))
        self.assertEqual({c for c in al.CHILLU}, {chr(c) for c in range(0x0D7A, 0x0D80)})
        cv = [i for i in self.items if i["group"] == "cv"]
        self.assertEqual(len(cv), len(al.CONS) * len(al.FORMS))
        self.assertEqual(len(al.FORMS), 16)
        for c in al.CONS:
            self.assertEqual(sum(1 for i in cv if i["consonant"] == c), 16)

    def test_every_entry_is_nfc_unique_and_has_codepoints(self):
        texts = [i["text"] for i in self.items]
        self.assertEqual(len(texts), len(set(texts)))
        for i in self.items:
            self.assertEqual(i["text"], unicodedata.normalize("NFC", i["text"]))
            self.assertEqual(i["codes"], " ".join("U+%04X" % ord(c) for c in i["text"]))
            self.assertTrue(i["manglish"].isascii() and i["manglish"].isalpha(), i["text"])
            self.assertTrue(i["ipa"], i["text"])

    def test_sha_pair_is_flagged_and_told_apart(self):
        sha_soft, sha_hard, sa = self.by["ശ"], self.by["ഷ"], self.by["സ"]
        self.assertEqual((sha_soft["codes"], sha_hard["codes"]), ("U+0D36", "U+0D37"))
        self.assertEqual(sha_soft["manglish"], sha_hard["manglish"])        # same typed Manglish ...
        self.assertTrue(sha_soft["lookalike"] and sha_hard["lookalike"])
        self.assertIn("ഷ", sha_soft["same_manglish_as"])
        self.assertNotEqual(sha_soft["label"], sha_hard["label"])           # ... different labels
        self.assertIn("soft", sha_soft["label"])
        self.assertNotEqual(sha_soft["ipa"], sha_hard["ipa"])
        self.assertNotEqual(sha_soft["manglish"], sa["manglish"])
        for x in (sha_soft, sha_hard, sa):                                  # and they are acoustic neighbours with സ
            self.assertTrue(any("sibilants" in n for n in x["neighbours"]))
        self.assertEqual((self.by["ശാ"]["manglish"], self.by["ഷാ"]["manglish"]), ("shaa", "shaa"))     # it holds for every form

    def test_other_lookalikes_the_owner_listed(self):
        for a, b in (("ന", "ണ"), ("ര", "റ"), ("ല", "ള"), ("ത", "ഥ"), ("എ", "ഏ"), ("ഒ", "ഓ"), ("കീ", "കേ"), ("കൂ", "കോ")):
            self.assertEqual(self.by[a]["manglish"], self.by[b]["manglish"], a + b)
            self.assertIn(b, self.by[a]["same_manglish_as"])
            self.assertNotEqual(self.by[a]["label"], self.by[b]["label"], a + b)
        self.assertFalse(self.by["ഴ"]["lookalike"])                          # zha is unique
        self.assertNotEqual(self.by["ക"]["manglish"], self.by["ഖ"]["manglish"])

    def test_vowel_length_is_kept_in_the_ipa(self):
        self.assertEqual((self.by["കാ"]["ipa"], self.by["കി"]["ipa"], self.by["കീ"]["ipa"]), ("kaː", "ki", "kiː"))
        self.assertEqual((self.by["കെ"]["ipa"], self.by["കേ"]["ipa"], self.by["കൊ"]["ipa"], self.by["കോ"]["ipa"]), ("ke", "keː", "ko", "koː"))
        self.assertEqual(self.by["ക്"]["ipa"], "k")

    def test_conjunct_clusters_come_from_a_dictionary_in_frequency_order(self):
        d = tempfile.mkdtemp()
        lex = os.path.join(d, "lexicon.csv")
        with open(lex, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["word_ml", "manglish"])
            for word, n in (("അക്കം", 5), ("അന്തം", 3), ("കണ്ണ്", 1), ("കാലം", 9)):
                for _ in range(n):
                    w.writerow([word, ""])
        items = al.build_inventory(lex, 10)
        cl = [i for i in items if i["group"] == "cluster"]
        self.assertTrue(cl and cl[0]["text"].startswith("ക്ക") and cl[0]["uses"] == 5)
        self.assertEqual([i["uses"] for i in cl], sorted((i["uses"] for i in cl), reverse=True))
        self.assertFalse(any(i["text"] in {x["text"] for x in al.build_inventory() if x["group"] != "cluster"} for i in cl))   # no duplicates with the base inventory
        self.assertEqual(len(al.build_inventory(lex, 1)) - len(al.build_inventory()), 1)

    def test_write_inventory_matches_the_format_the_generators_read(self):
        d = tempfile.mkdtemp()
        n = al.write_inventory(d, self.items)
        with open(os.path.join(d, "inventory.json"), encoding="utf-8") as f:
            inv = json.load(f)
        self.assertEqual((n, len(inv)), (len(self.items), len(self.items)))
        self.assertEqual([x["id"] for x in inv], list(range(len(inv))))
        self.assertTrue(all({"id", "text", "kind", "uses"} <= set(x) for x in inv))
        with open(os.path.join(d, "alphabet.csv"), encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(self.items))
        self.assertEqual(next(x for x in rows if x["text"] == "ശ")["label"], self.by["ശ"]["label"])

    def test_cli(self):
        d = tempfile.mkdtemp()
        p = subprocess.run([sys.executable, TOOL, "inventory", "--out", os.path.join(d, "a")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("share their Manglish", p.stdout)
        self.assertIn("consonant x 16 forms", p.stdout)
        u = subprocess.run([sys.executable, TOOL, "unicode"], capture_output=True, text=True)
        self.assertIn("assigned code points", u.stdout)
        bad = subprocess.run([sys.executable, TOOL, "inventory", "--out", os.path.join(d, "a"), "--lexicon", os.path.join(d, "nope.csv")], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertNotIn("Traceback", bad.stderr)


class LabelDocument(unittest.TestCase):
    def test_committed_markdown_is_up_to_date_and_complete(self):
        doc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "MALAYALAM_ALPHABET_LABELS.md")
        items = al.build_inventory()
        text = al.to_markdown(items)
        with open(doc, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), text, "regenerate: python3 tools/s2w_alphabet.py markdown --out docs/MALAYALAM_ALPHABET_LABELS.md")
        for it in items:
            self.assertIn(it["sound_id"], text)
            self.assertTrue(it["voice_label"] and it["say_as"])


class VoiceLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = al.build_inventory()
        cls.by = {i["text"]: i for i in cls.items}

    def test_sound_ids_are_unique_ascii_and_reversible(self):
        ids = [i["sound_id"] for i in self.items]
        self.assertEqual(len(ids), len(set(ids)))
        for i in self.items:
            self.assertRegex(i["sound_id"], r"^ml(-[0-9a-f]{4})+(-[a-z0-9]+)+$")
            cps = []
            for part in i["sound_id"].split("-")[1:]:
                if re.fullmatch(r"[0-9a-f]{4}", part) and len(cps) < len(i["text"]):
                    cps.append(chr(int(part, 16)))
                else:
                    break
            self.assertEqual("".join(cps), i["text"], i["sound_id"])                 # the id gives back the exact letters

    def test_every_lookalike_letter_has_an_exact_label_say_as_and_example(self):
        rep = al.lookalike_report("common")
        for kind in ("consonant letters", "vowel letters", "chillu letters"):
            for k, letters in rep[kind].items():
                tags, says = [], []
                for t in letters:
                    it = self.by[t]
                    self.assertTrue(it["say_as"] and it["example"], t)
                    self.assertIn(t, it["example"], (t, it["example"]))               # the example word really contains the letter
                    self.assertEqual(it["example"], unicodedata.normalize("NFC", it["example"]))
                    tags.append(it["sound_id"])
                    says.append(it["say_as"])
                self.assertEqual(len(set(tags)), len(letters), k)
                self.assertEqual(len(set(says)), len(letters), k)                      # no two look-alikes are described the same way
        for sg, (_tag, _say, ex, _gloss) in al.SIGN_NOTES.items():
            self.assertIn(sg, ex)                                                      # each sign example really contains the sign

    def test_the_sha_pair_exactly(self):
        a, b = self.by["ശ"], self.by["ഷ"]
        self.assertEqual((a["sound_id"], b["sound_id"]), ("ml-0d36-sha-soft", "ml-0d37-sha-hard"))
        self.assertNotEqual(a["say_as"], b["say_as"])
        self.assertIn("palatal", a["say_as"])
        self.assertIn("retroflex", b["say_as"])
        self.assertEqual((a["example"], b["example"]), ("ശരി", "കഷ്ടം"))
        for form in ("ാ", "ി", "ു"):                                                   # every form keeps the distinction
            x, y = self.by["ശ" + form], self.by["ഷ" + form]
            self.assertEqual(x["manglish"], y["manglish"])
            self.assertNotEqual(x["sound_id"], y["sound_id"])
            self.assertTrue(x["sound_id"].endswith("-soft") and y["sound_id"].endswith("-hard"))

    def test_sign_collisions_are_told_apart_in_the_id(self):
        ids = {t: self.by[t]["sound_id"] for t in ("കീ", "കേ", "കൂ", "കോ")}
        self.assertEqual(len(set(ids.values())), 4)
        self.assertTrue(ids["കീ"].endswith("long-i") and ids["കേ"].endswith("long-e") and ids["കൂ"].endswith("long-u") and ids["കോ"].endswith("long-o"))

    def test_sign_examples_only_on_the_consonant_that_appears_in_them(self):
        self.assertEqual(self.by["കീ"]["example"], "കീഴ്")
        self.assertEqual(self.by["ഖീ"]["example"], "")                                  # കീഴ് does not contain ഖീ: no misleading example
        self.assertTrue(self.by["ഖീ"]["say_as"])

    def test_voice_label_contains_glyph_label_ipa_and_example(self):
        vl = self.by["ഷ"]["voice_label"]
        for part in ("ഷ", "sha (hard sha)", "/ʂa/", "retroflex", "കഷ്ടം"):
            self.assertIn(part, vl)

    def test_labels_are_in_the_generator_inventory_and_csv(self):
        d = tempfile.mkdtemp()
        al.write_inventory(d, self.items)
        with open(os.path.join(d, "inventory.json"), encoding="utf-8") as f:
            inv = json.load(f)
        self.assertEqual(next(x for x in inv if x["text"] == "ഷ")["sound_id"], "ml-0d37-sha-hard")
        with open(os.path.join(d, "alphabet.csv"), encoding="utf-8", newline="") as f:
            rows = {r_["text"]: r_ for r_ in csv.DictReader(f)}
        self.assertEqual((rows["ശ"]["sound_id"], rows["ശ"]["example"]), ("ml-0d36-sha-soft", "ശരി"))
        self.assertTrue(rows["ശ"]["say_as"])

    def test_carrier_words_come_from_the_lexicon_and_contain_the_unit(self):
        d = tempfile.mkdtemp()
        lex = os.path.join(d, "lexicon.csv")
        with open(lex, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["word_ml", "sources"])
            for word, src in (("ശരി", "A"), ("ശരിയാണ്", "A + B"), ("ആകാശം", "A"), ("ഷർട്ട്", "A + B + C"), ("കടൽത്തീരത്തുള്ളത്", "A")):
                w.writerow([word, src])
        items = {i["text"]: i for i in al.build_inventory(lex, 5)}
        self.assertIn("ശ", items["ശ"]["carrier_word"])
        self.assertEqual(su_split(items["ശ"]["carrier_word"])[items["ശ"]["carrier_index"]], "ശ")
        self.assertEqual(items["ഷ"]["carrier_word"], "ഷർട്ട്")
        self.assertEqual(items["ഖ"]["carrier_word"], "")                              # no dictionary word contains it
        self.assertNotIn("കടൽത്തീരത്തുള്ളത്", {i["carrier_word"] for i in items.values()})   # too long to be a good carrier

    def test_labels_cli_lists_the_sha_pair(self):
        p = subprocess.run([sys.executable, TOOL, "labels"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ml-0d36-sha-soft", p.stdout)
        self.assertIn("ml-0d37-sha-hard", p.stdout)
        self.assertIn("ml-0d23-na-retroflex", p.stdout)


def su_split(word):
    import s2w_units as su
    return su.AKS.findall(su.normalise(word))


class Lookalikes(unittest.TestCase):
    def test_common_convention_finds_every_group(self):
        rep = al.lookalike_report("common")
        flat = {k: "".join(sorted(x)) for k, x in rep["consonant letters"].items()}
        self.assertEqual(flat, {"da": "".join(sorted("ഡദ")), "dha": "".join(sorted("ഢധ")), "la": "".join(sorted("ലള")), "na": "".join(sorted("ണന")),
                                "ra": "".join(sorted("രറ")), "sha": "".join(sorted("ശഷ")), "tha": "".join(sorted("ഠതഥ"))})
        self.assertEqual({k: sorted(x) for k, x in rep["vowel letters"].items()}, {"e": sorted("എഏ"), "o": sorted("ഒഓ")})
        self.assertEqual({k: sorted(x) for k, x in rep["vowel signs"].items()}, {"ee": sorted("ീേ"), "oo": sorted("ൂോ")})
        self.assertEqual({k: sorted(x) for k, x in rep["chillu letters"].items()}, {"l": sorted("ൽൾ"), "n": sorted("ൺൻ")})
        cross = rep["chillu vs consonant+virama"]
        self.assertEqual(set(cross), {"k", "l", "n", "r"})
        self.assertIn("ൿ", cross["k"])
        self.assertIn("ൻ", cross["n"])

    def test_owner_convention_differs_where_the_owner_wrote_ta_for_two_letters(self):
        own = al.lookalike_report("owner")["consonant letters"]
        self.assertEqual(sorted(own["ta"]), sorted("ടത"))
        self.assertEqual(sorted(own["tha"]), sorted("ഠഥ"))
        self.assertEqual(sorted(own["sha"]), sorted("ശഷ"))
        self.assertNotIn("ടത", "".join(al.lookalike_report("common")["consonant letters"].get("ta", [])))

    def test_rule_convention_merges_more_vowel_signs(self):
        rule = al.lookalike_report("rule")
        self.assertEqual(sorted(rule["vowel signs"]["ke"]), sorted("െേ"))                    # the spelling rule drops ē/ō length
        self.assertEqual(sorted(rule["vowel signs"]["ko"]), sorted("ൊോ"))

    def test_every_group_is_really_different_letters_and_labelled_apart(self):
        by = {i["text"]: i for i in al.build_inventory()}
        for kind, gs in al.lookalike_report("common").items():
            for k, x in gs.items():
                self.assertEqual(len(x), len(set(x)))
                if kind in ("consonant letters", "vowel letters"):
                    for t in x:
                        self.assertTrue(by[t]["lookalike"], t)
                    self.assertEqual(len({by[t]["label"] for t in x}), len(x), (kind, k))      # distinguishing labels are all different

    def test_unknown_convention_and_cli(self):
        with self.assertRaises(v.ViewerError):
            al.lookalike_report("nope")
        p = subprocess.run([sys.executable, TOOL, "lookalikes"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("sha", p.stdout)
        self.assertIn("ശ ഷ", p.stdout)
        self.assertIn("sibilants", p.stdout)
        o = subprocess.run([sys.executable, TOOL, "lookalikes", "--convention", "owner"], capture_output=True, text=True)
        self.assertIn("ടത", o.stdout.replace(" ", ""))


class Measure(unittest.TestCase):
    def feat(self, wav):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.wav")
        with open(p, "wb") as f:
            f.write(wav)
        out = subprocess.run([os.path.join(BUILD, "s2w_features"), p, "--out", "logmel"], capture_output=True, text=True).stdout
        return al.measure_features(out)

    def test_brighter_noise_has_higher_centroid_and_more_high_band_energy(self):
        bright, dull, mid = self.feat(noise_wav(5000, 7500)), self.feat(noise_wav(800, 2500)), self.feat(noise_wav(2500, 4500))
        self.assertGreater(bright["centroid_hz"], mid["centroid_hz"])
        self.assertGreater(mid["centroid_hz"], dull["centroid_hz"])
        self.assertGreater(bright["high_ratio"], dull["high_ratio"] + 0.3)
        self.assertGreater(bright["centroid_hz"] - dull["centroid_hz"], 1500)

    def test_similar_and_different_clips(self):
        a1, a2 = self.feat(noise_wav(5000, 7500, seed=1)), self.feat(noise_wav(5000, 7500, seed=7))
        d = self.feat(noise_wav(800, 2500))
        self.assertTrue(al.acoustically_similar(a1, a2))
        self.assertFalse(al.acoustically_similar(a1, d))

    def test_missing_features_return_none(self):
        self.assertIsNone(al.measure_features("nothing useful"))


class Service(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.audio = os.path.join(cls.tmp, "audio")
        bright, mid, dull = noise_wav(5000, 7500), noise_wav(2500, 4500), noise_wav(800, 2500)
        put_clips(cls.audio, "voiceA", {"ശ": mid, "ഷ": dull, "സ": bright, "ക": dull})
        put_clips(cls.audio, "voiceB", {"ശ": noise_wav(2500, 4500, seed=3), "ഷ": noise_wav(2500, 4500, seed=4)})
        cls.rec_dir = os.path.join(cls.tmp, "voice")
        cls.a = al.Alphabet(cls.rec_dir, [cls.audio], None, BUILD)

    def test_all_voices_are_kept_and_keys_are_unique(self):
        cl = self.a.clips["ശ"]
        self.assertEqual({c["voice"] for c in cl}, {"voiceA", "voiceB"})
        self.assertEqual(len({c["key"] for c in cl}), len(cl))

    def test_overview_counts_and_coverage(self):
        o = self.a.overview()
        self.assertEqual(o["total"], len(self.a.items))
        self.assertEqual(o["with_audio"], 4)
        self.assertEqual(next(x for x in o["items"] if x["text"] == "ശ")["clips"][0]["voice"], "voiceA")

    def test_clip_loader_ignores_traversal_and_non_wav(self):
        d = tempfile.mkdtemp()
        root = os.path.join(d, "root")
        os.makedirs(os.path.join(root, "v"))
        with open(os.path.join(d, "secret.wav"), "wb") as f:               # OUTSIDE the root the recorder was given
            f.write(noise_wav(800, 900))
        with open(os.path.join(root, "v", "ok.wav"), "wb") as f:
            f.write(noise_wav(800, 900))
        with open(os.path.join(root, "v", "x.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(root, "v", "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"items": [{"word_ml": "ക", "file": "../../secret.wav"}, {"word_ml": "ഖ", "file": "x.txt"}, {"word_ml": "ഗ", "file": "ok.wav"},
                                 {"word_ml": "ഘ", "file": "missing.wav"}, {"word_ml": "ങ", "file": "/etc/passwd"}]}, f)
        got = al.load_clip_lists([root])
        self.assertEqual(set(got), {"ഗ"})

    def test_detail_has_peaks_and_measurements_and_is_cached(self):
        d = self.a.detail("ശ", 0)
        self.assertEqual(len(d["peaks"]), 240)
        self.assertTrue(0.35 < d["duration"] < 0.45)
        self.assertTrue(d["measure"]["centroid_hz"] > 500)
        self.assertEqual(self.a.detail("ശ", 0)["peaks"], d["peaks"])
        with self.assertRaises(v.ViewerError):
            self.a.detail("ശ", 9)
        with self.assertRaises(v.ViewerError):
            self.a.detail("ഖ", 0)                                           # no audio for this entry

    def test_group_report_flags_acoustically_similar_lookalikes(self):
        g = self.a.group_report("ശ")
        by = {p["text"]: p for p in g["partners"]}
        self.assertIn("ഷ", by)
        self.assertTrue(by["ഷ"]["same_manglish"])
        self.assertIn("സ", by)                                              # acoustic neighbour although the Manglish differs
        self.assertFalse(by["സ"]["same_manglish"])
        self.assertFalse(by["ഷ"]["similar"])                                # mid-band noise vs dull noise: clearly different
        self.assertFalse(by["സ"]["similar"])
        # voiceB: both clips are the same kind of noise, so the report must say they are alike
        b = al.Alphabet(self.rec_dir, [os.path.join(self.audio, "voiceB")], None, BUILD)
        gb = {p["text"]: p for p in b.group_report("ശ")["partners"]}
        self.assertTrue(gb["ഷ"]["similar"])
        self.assertIsNone(gb["സ"]["similar"] if gb.get("സ") else None)     # no audio for സ in this voice -> unknown, not "different"

    def test_review_is_validated_saved_and_reloaded(self):
        key = self.a.clips["ശ"][0]["key"]
        saved = self.a.set_review("ശ", key, "wrong", "sounds like sa", "native-1")
        self.assertEqual((saved["verdict"], saved["note"], saved["reviewer"]), ("wrong", "sounds like sa", "native-1"))
        again = al.Alphabet(self.rec_dir, [self.audio], None, BUILD)
        self.assertEqual(again.review["ശ"][key]["verdict"], "wrong")      # survives a restart
        self.assertEqual(next(c for c in again.overview()["items"] if c["text"] == "ശ")["clips"][0]["verdict"], "wrong")
        for bad in (("ശ", key, "perfect"), ("ശ", "nope|x.wav", "ok"), ("ഖ", key, "ok"), ("ശ", key, "ok", "x" * 201), ("ശ", key, "ok", "a\nb"), ("ശ", key, "ok", "", "bad<name>")):
            with self.assertRaises(v.ViewerError):
                self.a.set_review(*bad)
        self.assertEqual(self.a.review["ശ"][key]["verdict"], "wrong")      # refused requests changed nothing
        self.a.set_review("ശ", key, "")                                     # clearing removes it
        self.assertNotIn("ശ", self.a.review)
        with open(self.a.review_file, "rb") as f:
            self.assertNotIn(b"native-1", f.read())

    def test_measurements_never_write_verdicts(self):
        before = dict(self.a.review)
        self.a.detail("ഷ", 0)
        self.a.group_report("ഷ")
        self.assertEqual(self.a.review, before)

    def test_export_csv_has_labels_and_verdict_counts(self):
        key = self.a.clips["സ"][0]["key"]
        self.a.set_review("സ", key, "ok")
        rows = list(csv.DictReader(io.StringIO(self.a.export_csv())))
        row = next(x for x in rows if x["text"] == "സ")
        self.assertEqual((row["verdict_ok"], row["clips"], row["codepoints"]), ("1", "1", "U+0D38"))
        sha_row = next(x for x in rows if x["text"] == "ശ")
        self.assertIn("ഷ", sha_row["same_manglish_as"])
        self.a.set_review("സ", key, "")

    def test_audio_files_are_never_modified(self):
        paths = [c["path"] for cl in self.a.clips.values() for c in cl]
        before = [sha(p) for p in paths]
        self.a.detail("ശ", 0)
        self.a.group_report("ശ")
        self.assertEqual(before, [sha(p) for p in paths])


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.audio = os.path.join(cls.tmp, "audio")
        put_clips(cls.audio, "voiceA", {"ശ": noise_wav(2500, 4500), "ഷ": noise_wav(800, 2500)})
        cls.rec = r.Recorder(os.path.join(cls.tmp, "voice"), [r.parse_word("കലം|kalam")], BUILD, alphabet_audio_dir=[cls.audio])
        cls.srv = r.make_server(cls.rec, 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request(method, path, body=body, headers=headers or {})
        resp = c.getresponse()
        data = resp.read()
        c.close()
        return resp.status, data, dict(resp.getheaders())

    def post_review(self, body, token=True, ctype="application/json", raw=None):
        h = {"Content-Type": ctype}
        if token:
            h["X-S2W-Token"] = self.rec.token
        return self.req("POST", "/api/alphabet/review", raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8"), h)

    def test_page_and_nav(self):
        st, body, _ = self.req("GET", "/alphabet")
        self.assertEqual(st, 200)
        self.assertIn(self.rec.token.encode(), body)
        self.assertIn("Malayalam alphabet".encode(), body)
        self.assertIn(b'href="/alphabet"', self.req("GET", "/")[1])
        self.assertIn(b'href="/alphabet"', self.req("GET", "/compare")[1])

    def test_overview_unicode_group_and_export(self):
        o = json.loads(self.req("GET", "/api/alphabet")[1])
        self.assertEqual(o["with_audio"], 2)
        self.assertEqual(json.loads(self.req("GET", "/api/alphabet/unicode")[1])["assigned"], 118)
        import urllib.parse as up
        g = json.loads(self.req("GET", "/api/alphabet/group?text=" + up.quote("ശ"))[1])
        self.assertTrue(any(p["text"] == "ഷ" for p in g["partners"]))
        st, body, h = self.req("GET", "/api/alphabet/export.csv")
        self.assertEqual(st, 200)
        self.assertIn("text/csv", h["Content-Type"])
        self.assertIn("ശ".encode(), body)

    def test_clip_audio_with_range_and_peaks(self):
        import urllib.parse as up
        q = "/api/alphabet/clip?text=" + up.quote("ശ") + "&k=0"
        st, full, h = self.req("GET", q)
        self.assertEqual((st, h.get("Accept-Ranges")), (200, "bytes"))
        with open(self.rec.alphabet.clips["ശ"][0]["path"], "rb") as f:
            self.assertEqual(full, f.read())
        st, part, h = self.req("GET", q, headers={"Range": "bytes=0-99"})
        self.assertEqual((st, part), (206, full[:100]))
        self.assertEqual(self.req("GET", q, headers={"Range": "bytes=999999999-"})[0], 416)
        pk = json.loads(self.req("GET", "/api/alphabet/peaks?text=" + up.quote("ശ") + "&k=0")[1])
        self.assertEqual(len(pk["peaks"]), 64)
        dt = json.loads(self.req("GET", "/api/alphabet/detail?text=" + up.quote("ശ") + "&k=0")[1])
        self.assertIn("centroid_hz", dt["measure"])

    def test_bad_requests_are_404_not_crashes_or_leaks(self):
        import urllib.parse as up
        for q in ("/api/alphabet/clip?text=" + up.quote("ഖ") + "&k=0", "/api/alphabet/clip?text=x&k=0", "/api/alphabet/clip?text=" + up.quote("ശ") + "&k=7",
                  "/api/alphabet/clip?text=" + up.quote("ശ") + "&k=-1", "/api/alphabet/clip?text=..%2F..%2Fetc%2Fpasswd&k=0", "/api/alphabet/detail?text=" + up.quote("ഖ") + "&k=0",
                  "/api/alphabet/group?text=zzz", "/api/alphabet/nothing"):
            st, body, _ = self.req("GET", q)
            self.assertEqual(st, 404, q)
            self.assertNotIn(self.tmp.encode(), body)

    def test_review_endpoint_security_and_effect(self):
        import urllib.parse as up
        key = self.rec.alphabet.clips["ഷ"][0]["key"]
        good = {"text": "ഷ", "key": key, "verdict": "ok", "note": "clear", "reviewer": "me"}
        self.assertEqual(self.post_review(good, token=False)[0], 403)
        self.assertEqual(self.post_review(good, ctype="text/plain")[0], 415)
        self.assertEqual(self.post_review(None, raw=b"not json")[0], 422)
        self.assertEqual(self.post_review({"text": "ഷ"})[0], 422)
        self.assertEqual(self.post_review(dict(good, verdict="great"))[0], 422)
        self.assertEqual(self.post_review(None, raw=b"x" * 5000)[0], 413)
        self.assertEqual(self.rec.alphabet.review, {})                       # nothing happened for any refused request
        st, body, _ = self.post_review(good)
        self.assertEqual((st, json.loads(body)["saved"]["verdict"]), (200, "ok"))
        o = json.loads(self.req("GET", "/api/alphabet")[1])
        self.assertEqual(next(x for x in o["items"] if x["text"] == "ഷ")["clips"][0]["verdict"], "ok")
        self.post_review(dict(good, verdict=""))

    def test_existing_endpoints_still_work(self):
        self.assertEqual(self.req("GET", "/api/words")[0], 200)
        self.assertEqual(self.req("GET", "/")[0], 200)


if __name__ == "__main__":
    unittest.main()
