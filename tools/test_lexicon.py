"""Tests for tools/s2w_lexicon.py and the recorder's dictionary loading of its output. MADE-UP sample dictionaries only:
the real files are not committed; RealLayouts mirrors the layouts of the Olam datuk / enml TSV dumps and the E.K. Kurup YAML as
published on olam.in/p/open (checked against the downloaded files), using made-up rows."""
import csv
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BUILD = os.environ.get("S2W_BUILD", "build")
import s2w_lexicon as lx  # noqa: E402
import s2w_recorder as r  # noqa: E402
import s2w_viewer as v  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "s2w_lexicon.py")


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Lexicon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.datuk = write(os.path.join(cls.d, "datuk.tsv"), "കലം\tപാചകത്തിനുള്ള പാത്രം\nമഴ\tആകാശത്തുനിന്നു പെയ്യുന്ന വെള്ളം\n")                      # Malayalam -> Malayalam
        cls.kurup = write(os.path.join(cls.d, "kurup.csv"), "english,malayalam\npot,\"കലം, കുടം\"\ntime,കാലം\nrain,മഴ\n")                                  # English -> Malayalam
        cls.olam = write(os.path.join(cls.d, "olam.csv"), "id,english_word,part_of_speech,malayalam_definition\n1,pot,n,കലം\n2,dog,n,പട്ടി\n3,say hi,v,ഹായ് പറയുക\n")
        cls.out = os.path.join(cls.d, "out", "lexicon.csv")
        cls.stats = lx.build([(cls.datuk, "Datuk", "ODbL"), (cls.kurup, "Kurup", "ODbL"), (cls.olam, "Olam", "ODbL")], cls.out)
        cls.rows = {x["word_ml"]: x for x in rows(cls.out)}

    def test_combines_the_pieces_each_source_is_missing(self):
        kalam = self.rows["കലം"]
        self.assertEqual(kalam["meaning_en"], "pot")                                      # same English meaning from two files, kept once
        self.assertEqual(kalam["meaning_ml"], "പാചകത്തിനുള്ള പാത്രം")                      # Malayalam definition from the Malayalam-Malayalam file
        self.assertEqual(kalam["sources"], "Datuk + Kurup + Olam")
        self.assertEqual(kalam["licences"], "ODbL")
        self.assertEqual(self.rows["കുടം"]["meaning_en"], "pot")
        self.assertEqual(self.rows["മഴ"]["meaning_en"], "rain")
        self.assertEqual(self.rows["മഴ"]["sources"], "Datuk + Kurup")
        self.assertEqual(set(self.rows), {"കലം", "കുടം", "കാലം", "മഴ", "പട്ടി"})
        self.assertEqual((self.stats["words"], self.stats["multi_source"], self.stats["with_en_and_ml"]), (5, 2, 2))

    def test_phrases_are_not_words(self):
        self.assertFalse(any(" " in w for w in self.rows))

    def test_every_row_has_valid_sound_units_and_manglish(self):
        for w, x in self.rows.items():
            units = x["sound_units"].split()
            self.assertTrue(units and all(len(u) <= 4 and u.isalpha() and u.isascii() and u.islower() for u in units), w)
            self.assertTrue(x["manglish"].isascii() and x["manglish"].isalpha(), w)
            self.assertIn(x["g2p"], ("yes", "no"))
        self.assertEqual(self.rows["കാലം"]["sound_units"].split()[:2], ["k", "aa"])         # long vowel kept, with or without mlphon

    def test_recorder_dictionary_reads_the_lexicon_and_uses_its_units(self):
        dic = r.Dictionary(self.out)
        res = {x["ml"]: x for x in dic.search("pot")}
        self.assertEqual(set(res), {"കലം", "കുടം"})
        self.assertEqual(res["കലം"]["units"], self.rows["കലം"]["sound_units"].split())
        self.assertEqual(res["കലം"]["meaning"], "pot")
        self.assertEqual(dic.search("kaalam")[0]["units"][:2], ["k", "aa"])
        self.assertEqual(dic.columns["malayalam"], "word_ml")

    def test_output_cells_are_safe_in_a_spreadsheet(self):
        d = tempfile.mkdtemp()
        src = write(os.path.join(d, "x.csv"), "english,malayalam\n=cmd|' /C calc'!A0,കലം\n")
        out = os.path.join(d, "o.csv")
        lx.build([(src, "X", "")], out)
        self.assertTrue(rows(out)[0]["meaning_en"].startswith("'="))

    def test_errors_and_no_partial_output(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "o.csv")
        with self.assertRaises(v.ViewerError):
            lx.build([(os.path.join(d, "missing.csv"), "M", "")], out)
        english_only = write(os.path.join(d, "e.csv"), "a,b\nx,y\n")
        with self.assertRaises(v.ViewerError):
            lx.build([(english_only, "E", "")], out)
        self.assertFalse(os.path.exists(out) or os.path.exists(out + ".tmp"))

    def test_max_words(self):
        out = os.path.join(self.d, "small.csv")
        self.assertEqual(lx.build([(self.kurup, "Kurup", "")], out, max_words=2)["words"], 2)

    def test_parse_source(self):
        self.assertEqual(lx.parse_source("a/b.csv=Datuk:ODbL"), ("a/b.csv", "Datuk", "ODbL"))
        self.assertEqual(lx.parse_source("a/b.csv"), ("a/b.csv", "b.csv", ""))
        self.assertEqual(lx.parse_source("a.csv=Name"), ("a.csv", "Name", ""))

    def test_inputs_are_only_read(self):
        before = [os.stat(p).st_mtime_ns for p in (self.datuk, self.kurup, self.olam)]
        lx.build([(self.datuk, "D", ""), (self.kurup, "K", ""), (self.olam, "O", "")], os.path.join(self.d, "again.csv"))
        self.assertEqual(before, [os.stat(p).st_mtime_ns for p in (self.datuk, self.kurup, self.olam)])

    def test_cli(self):
        out = os.path.join(self.d, "cli.csv")
        p = subprocess.run([sys.executable, TOOL, "--source", self.kurup + "=Kurup:ODbL", "--source", self.datuk + "=Datuk:ODbL", "--out", out, "--no-g2p"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("wrote", p.stdout)
        self.assertIn("licence", p.stdout)
        bad = subprocess.run([sys.executable, TOOL, "--source", os.path.join(self.d, "nope.csv"), "--out", out], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertNotIn("Traceback", bad.stderr)

    def test_scales_to_a_large_dictionary(self):
        letters = "കഗചജടതദനപബമയരലവസഹ"
        signs = ["", "ാ", "ി", "ീ", "ു", "ൂ", "െ", "ൊ"]
        words = []
        for a in letters:
            for sa in signs:
                for b in letters:
                    for sb in signs[:4]:
                        for c in letters[:6]:
                            words.append(a + sa + b + sb + c + "ം")
        words = words[:60000]
        big = write(os.path.join(self.d, "big.csv"), "english,malayalam\n" + "\n".join("w%d,%s" % (i, w) for i, w in enumerate(words)) + "\n")
        t0 = time.time()
        dic = r.Dictionary(big)
        load = time.time() - t0
        t0 = time.time()
        hits = dic.search("kalak")
        q = time.time() - t0
        self.assertGreater(len(dic.entries), 50000)
        self.assertLess(load, 15, "load took %.1fs" % load)
        self.assertLess(q, 1.5, "search took %.2fs" % q)
        self.assertTrue(hits)


class CleanWords(unittest.TestCase):
    def test_clean_word(self):
        for ok in ("കലം", "കാലം", "പട്ടി", "അ", "ക്രിക്കറ്റ്"):
            self.assertTrue(lx.clean_word(ok), ok)
        for bad in ("(ഉദാ", "100ഗ്രാം", "ദരം2", "ൽ", "ർത്ത്", "◦വിവേകരഹിതം", "ാ", "", "kalam", "ക ലം"):
            self.assertFalse(lx.clean_word(bad), bad)


class RealLayouts(unittest.TestCase):
    """Layouts as published by Olam: `from_content<TAB>types<TAB>to_content` TSV (datuk, enml) and the Kurup YAML synsets."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.datuk = write(os.path.join(cls.d, "datuk"), "from_content\ttypes\tto_content\nകലം\t{നാ.}\tപാചകത്തിനുള്ള പാത്രം\nകലം\t{നാ.}\tകുടം\nഅ1\t{}\t\n")
        cls.enml = write(os.path.join(cls.d, "enml"), "from_content\ttypes\tto_content\nPot\t{n}\tകലം, കുടം\n10 Crores\t{n}\tപത്തുകോടി\nSay hi\t{v}\tഹായ് പറയുക\n")
        cls.kurup = write(os.path.join(cls.d, "ekkurup.yml"),
                          "- head: pot\n  senses:\n    - pos: noun\n      en: [[pot, vessel]]\n      ml: [[കലം, 'കുടം', \"ഭാജനം\"]]\n"
                          "- head: take someone aback\n  senses:\n    - pos: idiom\n      en: [[astonish]]\n      ml: [[അമ്പരപ്പിക്കുക,\n        ഞെട്ടിക്കുക]]\n")
        cls.out = os.path.join(cls.d, "lexicon.csv")
        cls.stats = lx.build([(cls.datuk, "Datuk", "ODbL"), (cls.enml, "Olam-ENML", "ODbL"), (cls.kurup, "EK-Kurup", "CC-BY-SA-4.0")], cls.out, use_g2p=False)
        cls.rows = {x["word_ml"]: x for x in rows(cls.out)}

    def test_olam_tsv_and_kurup_yaml_are_combined(self):
        kalam = self.rows["കലം"]
        self.assertEqual(kalam["sources"], "Datuk + Olam-ENML + EK-Kurup")
        self.assertEqual(kalam["meaning_en"], "Pot ; pot")
        self.assertEqual(kalam["meaning_ml"], "പാചകത്തിനുള്ള പാത്രം ; കുടം")
        self.assertEqual(kalam["synonyms_ml"], "കുടം ; ഭാജനം")
        self.assertEqual(kalam["pos"], "നാ. n noun")
        self.assertEqual(kalam["licences"], "ODbL + CC-BY-SA-4.0")

    def test_quoted_and_wrapped_yaml_lists_and_phrases(self):
        self.assertIn("ഭാജനം", self.rows)
        self.assertIn("ഞെട്ടിക്കുക", self.rows)                      # list wrapped over two lines
        self.assertEqual(self.rows["ഞെട്ടിക്കുക"]["meaning_en"], "take someone aback")
        self.assertFalse(any(" " in w for w in self.rows))           # "ഹായ് പറയുക" is a phrase
        self.assertNotIn("അ1", self.rows)                            # headword with a digit and no definition

    def test_non_olam_tsv_is_rejected_by_the_strict_reader(self):
        bad = write(os.path.join(self.d, "bad.tsv"), "a\tb\tc\nകലം\tx\ty\n")
        with self.assertRaises(v.ViewerError):
            list(lx.read_olam_tsv(bad))


if __name__ == "__main__":
    unittest.main()
