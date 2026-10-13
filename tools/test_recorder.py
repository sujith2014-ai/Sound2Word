"""Tests for tools/s2w_recorder.py (stdlib unittest). No microphone is used; uploads are built from the SYNTHETIC fixture."""
import hashlib
import http.client
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import wave

BUILD = os.environ.get("S2W_BUILD", "build")
if not all(os.path.exists(os.path.join(BUILD, n)) for n in ("s2w_make_fixture", "s2w_qa", "s2w_features")):
    print("recorder tests skipped: C tools not built")
    sys.exit(0)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2w_recorder as r  # noqa: E402
import s2w_viewer as v  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "s2w_recorder.py")
WORDS = [r.parse_word("കലം|kalam"), r.parse_word("കാലം|kaalam")]


def make_wav(seconds=1.0, rate=16000, channels=1, amp=8000):
    import math
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(int(amp * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(2, "little", signed=True) * channels for i in range(int(seconds * rate)))
        w.writeframes(frames)
    return buf.getvalue()


def tone_wav(parts, pad=0.2, rate=16000, amp=9000):
    """Concatenated pure tones [(Hz, seconds)] with silence padding: a synthetic 'word' whose sound boundaries are known exactly."""
    import math
    buf = io.BytesIO()
    x = [0] * int(pad * rate)
    for hz, sec in parts:
        x += [int(amp * math.sin(2 * math.pi * hz * i / rate)) for i in range(int(sec * rate))]
    x += [0] * int(pad * rate)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(v.to_bytes(2, "little", signed=True) for v in (a + (1 if i % 2 else -1) * 3 for i, a in enumerate(x))))
    return buf.getvalue()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_csv(path):
    import csv
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_file(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class PureLexicon(unittest.TestCase):
    def test_ml_to_manglish(self):
        for ml, rom in (("കലം", "kalam"), ("കാലം", "kaalam"), ("പട്ടി", "patti"), ("പടി", "pati"), ("മഴ", "mazha"), ("ആന", "aana"), ("ചെവി", "chevi"),
                        ("പുസ്തകം", "pusthakam"), ("വള്ളം", "vallam"), ("കുറ്റി", "kutti"), ("കാട്", "kaatu"), ("അമ്മ", "amma"), ("തേങ്ങ", "thenga"), ("ചക്ക", "chakka"),
                        ("ആകാശം", "aakaasham"), ("വിദ്യാലയം", "vidyaalayam"), ("ഓണം", "onam")):
            self.assertEqual(r.ml_to_manglish(ml), rom, ml)
        self.assertEqual(r.ml_to_manglish("abc ക"), "abcka")      # unknown characters never crash

    def test_ipa_units_keep_length_and_gemination(self):
        U = lambda s_: r.ipa_units(s_.split())
        self.assertEqual(U("k aː l a m"), ["k", "aa", "l", "a", "m"])
        self.assertEqual(U("p a ʈ ʈ i"), ["p", "a", "tt", "i"])
        self.assertEqual(U("p a ʈ i"), ["p", "a", "t", "i"])
        self.assertEqual(U("k oː ʈ i"), ["k", "oo", "t", "i"])
        self.assertEqual(U("k o ʈ i"), ["k", "o", "t", "i"])
        self.assertEqual(U("t̪ eː ŋ ŋ a"), ["th", "ee", "ng", "a"])
        self.assertEqual(U("k u ṯ ṯ i"), ["k", "u", "tt", "i"])
        self.assertEqual(U("ai̯ k j a m"), ["ai", "k", "y", "a", "m"])
        self.assertEqual(U("bʱ a"), ["bh", "a"])
        self.assertIsNone(U("k ? a"))
        self.assertIsNone(r.ipa_units([]))
        self.assertTrue(all(re.match(r"^[a-z]{1,4}$", u) for u in U("ʋ a ɭ ɭ a m")))
        self.assertEqual(U("ʋ a ɭ ɭ a m"), ["v", "a", "llh", "a", "m"])
        self.assertEqual(U("p a t̪ t̪ a"), ["p", "a", "tth", "a"])
        self.assertEqual(U("a m m a"), ["a", "mm", "a"])

    def test_dictionary_layouts_and_search(self):
        d = tempfile.mkdtemp()
        enml = write_file(os.path.join(d, "enml.csv"), "id,english_word,part_of_speech,malayalam_definition\n1,pot,n,\"കലം, കുടം\"\n2,time,n,കാലം\n3,dog,n,പട്ടി; നായ\n4,cooking pot,n,\"കലം (പാചകത്തിന്)\"\n5,say hello,v,ഹലോ പറയുക\n")
        dic = r.Dictionary(enml)
        words = {e[0] for e in dic.entries}
        self.assertTrue({"കലം", "കുടം", "കാലം", "പട്ടി", "നായ"} <= words)
        self.assertEqual({(x["ml"]) for x in dic.search("pot")}, {"കലം", "കുടം"})
        self.assertEqual(dic.search("kalam")[0]["ml"], "കലം")                        # typed Manglish finds the Malayalam word
        self.assertEqual(dic.search("kaal")[0]["ml"], "കാലം")                        # prefix
        self.assertEqual(dic.search("കല")[0]["ml"], "കലം")                            # Malayalam prefix
        self.assertEqual(dic.search("patti")[0]["meaning"], "dog")
        res = [x["ml"] for x in dic.search("a")]
        self.assertEqual(res, sorted(res))                                           # results are listed alphabetically
        self.assertEqual(dic.search(""), [])
        self.assertLessEqual(len(dic.search("a", limit=3)), 3)
        phrase = next(x for x in dic.search("hello") if " " in x["ml"])
        self.assertFalse(phrase["can_add"])                                          # a phrase cannot be added as a word
        self.assertEqual(dic.search("kalam")[0]["manglish"], "kalam")
        tsv = write_file(os.path.join(d, "mlml.tsv"), "കലം\tപാചകത്തിനുള്ള പാത്രം\nകാലം\tസമയം\n")
        d2 = r.Dictionary(tsv, no_header=True)
        self.assertEqual({e[0] for e in d2.entries}, {"കലം", "കാലം"})
        self.assertEqual(d2.search("kaalam")[0]["meaning"], "സമയം")
        one = write_file(os.path.join(d, "one.csv"), "word\nകലം\nമഴ\n")
        self.assertEqual(len(r.Dictionary(one).entries), 2)
        named = write_file(os.path.join(d, "named.csv"), "a,b\nsky,ആകാശം\n")
        self.assertEqual(r.Dictionary(named, ml_col="b", en_col="a").search("sky")[0]["ml"], "ആകാശം")

    def test_dictionary_errors(self):
        d = tempfile.mkdtemp()
        for bad in (os.path.join(d, "nope.csv"), write_file(os.path.join(d, "empty.csv"), ""), write_file(os.path.join(d, "eng.csv"), "a,b\nx,y\n")):
            with self.assertRaises(v.ViewerError):
                r.Dictionary(bad)
        with self.assertRaises(v.ViewerError):
            r.Dictionary(write_file(os.path.join(d, "ok.csv"), "english,malayalam\npot,കലം\n"), ml_col="missing")


class Pure(unittest.TestCase):
    def test_parse_word(self):
        self.assertEqual(r.parse_word("കലം|kalam"), {"id": "kalam", "label": "കലം", "manglish": "kalam", "units": ["k", "a", "l", "a", "m"]})
        self.assertEqual(r.parse_word("കാലം|kaalam|k aa l a m")["units"], ["k", "aa", "l", "a", "m"])
        for bad in ("കലം", "കലം|", "|kalam", "കലം|kaḷam", "കലം|kalam1", "കലം|ka lam", "കലം|kalam|k a l m", "കലം|kalam|ka lam x", "കലം|kalam|k a l a m|extra"):
            with self.assertRaises(v.ViewerError):
                r.parse_word(bad)

    def test_split_units(self):
        for word, units in (("kalam", "k a l a m"), ("kaalam", "k aa l a m"), ("padi", "p a d i"), ("patti", "p a tt i"), ("mazha", "m a zh a"), ("chevi", "ch e v i"),
                            ("pusthakam", "p u s th a k a m"), ("aana", "aa n a"), ("vallam", "v a ll a m"), ("kai", "k ai")):
            self.assertEqual(r.split_units(word), units.split(), word)
            self.assertEqual("".join(r.split_units(word)), word)          # nothing is lost or invented

    def test_base_unit(self):
        self.assertEqual([r.base_unit(u) for u in ("aa", "a", "tt", "t", "ll", "th", "ch", "zh", "ee")], ["a", "a", "t", "t", "l", "th", "ch", "zh", "e"])

    def test_align_units_marks_length_and_different(self):
        rel = lambda a, b: [(i, j, k) for i, j, k in r.align_units(r.split_units(a), r.split_units(b))]
        self.assertEqual([k for _, _, k in rel("kalam", "kaalam")], ["identical", "length", "identical", "identical", "identical"])
        self.assertEqual([k for _, _, k in rel("padi", "patti")], ["identical", "identical", "different", "identical"])
        self.assertEqual([k for _, _, k in rel("pata", "patta")], ["identical", "identical", "length", "identical"])
        rows = rel("kalam", "mala")
        self.assertIn("only_a", [k for _, _, k in rows])
        self.assertEqual(sorted(i for i, _, _ in rows if i is not None), [0, 1, 2, 3, 4])   # every sound of A appears exactly once

    def test_validate_segments(self):
        u = ["k", "a", "m"]
        good = {"units": u, "edges": [0.1, 0.3, 0.5, 0.8], "method": "edited"}
        self.assertEqual(r.validate_segments(good, u, 1.0)["edges"], [0.1, 0.3, 0.5, 0.8])
        for bad in (dict(good, units=["k", "a"]), dict(good, edges=[0.1, 0.3, 0.8]), dict(good, edges=[0.1, 0.3, 0.3, 0.8]), dict(good, edges=[0.1, 0.3, 0.5, 1.5]),
                    dict(good, edges=[-0.5, 0.3, 0.5, 0.8]), dict(good, edges=[0.1, "x", 0.5, 0.8]), dict(good, method="magic"), [], None):
            with self.assertRaises(v.ViewerError):
                r.validate_segments(bad, u, 1.0)

    def test_validate_wav(self):
        self.assertEqual(r.validate_wav(make_wav(1.0)), (16000, 16000))
        for bad, msg in ((b"junk", "valid WAV"), (make_wav(1.0, channels=2), "mono"), (make_wav(0.1), "too short"), (make_wav(21.0, rate=8000), "too long")):
            with self.assertRaises(v.ViewerError) as c:
                r.validate_wav(bad)
            self.assertEqual(c.exception.status, 422)
            self.assertIn(msg, c.exception.message)


class Base(unittest.TestCase):
    WORDS = WORDS

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.dir = os.path.join(cls.tmp, "voice")
        cls.rec = r.Recorder(cls.dir, cls.WORDS, BUILD)
        cls.srv = r.make_server(cls.rec, 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = dict(headers or {})
        if host:
            h["Host"] = host
        c.request(method, path, body=body, headers=h)
        resp = c.getresponse()
        data = resp.read()
        c.close()
        return resp.status, data, dict(resp.getheaders())

    def post(self, path, data, token=True, ctype="audio/wav"):
        h = {"Content-Type": ctype}
        if token:
            h["X-S2W-Token"] = self.rec.token
        return self.req("POST", path, data, h)

    def files(self):
        return sorted(os.path.relpath(os.path.join(d, f), self.dir) for d, _, fs in os.walk(self.dir) for f in fs)

    def accept_tone(self, wid, parts, edges, method="edited", extra=None):
        seg = {"units": self.rec.words[wid]["units"], "edges": edges, "method": method}
        h = {"Content-Type": "audio/wav", "X-S2W-Token": self.rec.token, "X-S2W-Segments": json.dumps(seg)}
        h.update(extra or {})
        return self.req("POST", "/api/accept?word=" + wid, tone_wav(parts), h)


class Server(Base):
    def test_page_has_token_and_words(self):
        st, body, _ = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(self.rec.token.encode(), body)
        self.assertEqual([w["manglish"] for w in json.loads(self.req("GET", "/api/words")[1])], ["kalam", "kaalam"])

    def test_foreign_host_refused(self):
        self.assertEqual(self.req("GET", "/", host="evil.example")[0], 403)
        self.assertEqual(self.post("/api/analyse", make_wav(), token=True)[0], 200)   # control: normal host works

    def test_token_and_content_type_required(self):
        before = self.files()
        self.assertEqual(self.post("/api/accept?word=kalam", make_wav(), token=False)[0], 403)
        self.assertEqual(self.post("/api/accept?word=kalam", make_wav(), ctype="text/plain")[0], 415)
        self.assertEqual(self.files(), before)

    def test_bad_uploads_rejected_and_nothing_written(self):
        before = self.files()
        for data in (b"junk", make_wav(1.0, channels=2), make_wav(0.1)):
            self.assertEqual(self.post("/api/accept?word=kalam", data)[0], 422)
        self.assertEqual(self.post("/api/accept?word=nope", make_wav())[0], 404)
        self.assertEqual(self.post("/api/accept?word=..%2Fx", make_wav())[0], 404)
        self.assertEqual(self.files(), before)

    def test_oversized_upload_refused(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("POST", "/api/analyse")
        c.putheader("Content-Type", "audio/wav")
        c.putheader("X-S2W-Token", self.rec.token)
        c.putheader("Content-Length", str(r.MAX_BYTES + 1))
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()

    def test_analyse_does_not_save_anything(self):
        before = self.files()
        st, body, _ = self.post("/api/analyse", make_wav(1.0))
        d = json.loads(body)
        self.assertEqual(st, 200)
        self.assertIn(d["qa"]["verdict"], ("ok", "marginal", "reject"))
        self.assertEqual(d["spec"]["cols"], 40)
        self.assertAlmostEqual(d["duration"], 1.0, places=2)
        self.assertEqual(self.files(), before)          # Discard = just never calling accept: nothing on disk

    def test_accept_saves_exact_bytes_numbers_takes_and_logs(self):
        a, b = make_wav(1.0), make_wav(1.2, amp=6000)
        st, body, _ = self.post("/api/accept?word=kaalam", a)
        self.assertEqual((st, json.loads(body)["saved"]), (200, "take_001.wav"))
        self.assertEqual(json.loads(self.post("/api/accept?word=kaalam", b)[1])["saved"], "take_002.wav")
        with open(os.path.join(self.dir, "kaalam", "take_001.wav"), "rb") as f:
            self.assertEqual(f.read(), a)
        with open(os.path.join(self.dir, "takes.csv"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("കാലം,kaalam,kaalam/take_001.wav", text)
        self.assertIn("PERSONAL TEST RECORDING", text)
        self.assertTrue(all(p == "takes.csv" or p.startswith(("kalam" + os.sep, "kaalam" + os.sep)) for p in self.files()))
        st, body, h = self.req("GET", "/audio/kaalam/take_002.wav", headers={"Range": "bytes=0-99"})
        self.assertEqual((st, body, h["Content-Range"].split("/")[0]), (206, b[:100], "bytes 0-99"))
        self.assertEqual(self.req("GET", "/audio/kaalam/take_001.wav")[1], a)
        listing = {w["id"]: w for w in json.loads(self.req("GET", "/api/words")[1])}
        self.assertEqual([t["name"] for t in listing["kaalam"]["takes"]], ["take_001.wav", "take_002.wav"])

    def test_audio_path_safety_and_methods(self):
        for p in ("/audio/kalam/..%2F..%2Fetc%2Fpasswd", "/audio/kalam/take_001.wav", "/audio/..%2F/take_001.wav", "/audio/kalam/takes.csv", "/nope"):
            self.assertEqual(self.req("GET", p)[0], 404, p)
        for m in ("PUT", "DELETE", "PATCH"):
            self.assertEqual(self.req(m, "/api/words")[0], 405)


class Seg(Base):
    WORDS = [r.parse_word("കലം|kalam"), r.parse_word("കം|kam"), r.parse_word("കാം|kaam")]

    def test_suggestion_finds_tone_changes_within_30ms(self):
        rec = r.Recorder(os.path.join(self.tmp, "v2"), [r.parse_word("കം|kam")], BUILD)
        out = rec.analyse(tone_wav([(500, 0.15), (1500, 0.12), (900, 0.15)]), "kam")
        sug = out["spec"]["suggest"]
        truth = [0.2, 0.35, 0.47, 0.62]                      # start, k|a, a|m, end of the tone sequence
        self.assertEqual(len(sug), 4)
        self.assertEqual(out["units"], ["k", "a", "m"])
        for got, want in zip(sug, truth):
            self.assertLess(abs(got - want), 0.03, (sug, truth))

    def test_suggestion_never_fails_on_flat_audio(self):
        rec = r.Recorder(os.path.join(self.tmp, "v3"), [r.parse_word("കലം|kalam")], BUILD)
        sug = rec.analyse(tone_wav([(600, 0.8)]), "kalam")["spec"]["suggest"]
        self.assertEqual(len(sug), 6)
        self.assertTrue(all(b > a for a, b in zip(sug, sug[1:])))

    def test_accept_with_segments_validates_and_saves_json(self):
        rec_before = self.files()
        bad = self.accept_tone("kalam", [(500, 0.5)], [0.1, 0.2, 0.3])
        self.assertEqual(bad[0], 422)
        self.assertEqual(self.accept_tone("kalam", [(500, 0.5)], [0.1, 0.2, 0.3, 0.4, 0.5, 9.0])[0], 422)
        self.assertEqual(self.accept_tone("kalam", [(500, 0.5)], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], extra={"X-S2W-Segments": "not json"})[0], 422)
        self.assertEqual(self.accept_tone("kalam", [(500, 0.5)], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], extra={"X-S2W-Segments": "x" * 5000})[0], 422)
        self.assertEqual(self.files(), rec_before)           # nothing is written for rejected segment data
        st, body, _ = self.accept_tone("kalam", [(500, 0.5)], [0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
        name = json.loads(body)["saved"]
        self.assertEqual(st, 200)
        with open(os.path.join(self.dir, "kalam", name[:-4] + ".segments.json"), encoding="utf-8") as f:
            seg = json.load(f)
        self.assertEqual((seg["units"], seg["edges"], seg["method"]), (["k", "a", "l", "a", "m"], [0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "edited"))
        self.assertEqual(json.loads(self.req("GET", "/api/segments/kalam/" + name)[1])["edges"][0], 0.2)
        self.assertEqual(self.req("GET", "/api/segments/kalam/take_999.wav")[0], 404)
        self.assertTrue(next(t for t in json.loads(self.req("GET", "/api/words")[1])[0]["takes"] if t["name"] == name)["segments"])

    def test_compare_finds_the_length_difference_and_same_sounds(self):
        a = self.accept_tone("kam", [(500, 0.15), (1500, 0.12), (900, 0.15)], [0.2, 0.35, 0.47, 0.62])
        b = self.accept_tone("kaam", [(500, 0.15), (1500, 0.30), (900, 0.15)], [0.2, 0.35, 0.65, 0.8])
        ra, rb = json.loads(a[1])["saved"], json.loads(b[1])["saved"]
        st, body, _ = self.req("GET", "/api/compare?a=kam/%s&b=kaam/%s" % (ra, rb))
        self.assertEqual(st, 200, body)
        d = json.loads(body)
        rows = d["rows"]
        self.assertEqual([x["relation"] for x in rows], ["identical", "length", "identical"])
        self.assertAlmostEqual(rows[1]["ratio"], 0.12 / 0.30, places=2)
        self.assertLess(rows[1]["spectral"], 1.5)           # same tone, different length: small (edge frames shift the mean a little)
        self.assertLess(rows[0]["spectral"], 0.5)
        self.assertLess(rows[2]["spectral"], 0.5)
        self.assertAlmostEqual(rows[0]["ratio"], 1.0, places=2)
        self.assertEqual(len(d["a"]["peaks"]), 1200)
        self.assertTrue(d["whole"]["distance"] is not None or d["whole"]["note"])
        # different SOUNDS (different tone) must show a bigger spectral difference than the same sound
        c = self.accept_tone("kam", [(500, 0.15), (2600, 0.12), (900, 0.15)], [0.2, 0.35, 0.47, 0.62])
        rc = json.loads(c[1])["saved"]
        d2 = json.loads(self.req("GET", "/api/compare?a=kam/%s&b=kam/%s" % (ra, rc))[1])
        self.assertGreater(d2["rows"][1]["spectral"], 5 * max(0.05, d2["rows"][0]["spectral"]))
        self.assertLess(d2["rows"][0]["spectral"], 0.5)
        # errors
        self.assertEqual(self.req("GET", "/api/compare?a=kam/take_999.wav&b=kam/%s" % ra)[0], 404)
        self.assertEqual(self.req("GET", "/api/compare?a=..%2Fx&b=kam/" + ra)[0], 404)

    def test_compare_requires_segments_and_page_served(self):
        st, body, _ = self.req("POST", "/api/accept?word=kam", tone_wav([(500, 0.5)]), {"Content-Type": "audio/wav", "X-S2W-Token": self.rec.token})
        name = json.loads(body)["saved"]                        # accepted WITHOUT boundaries
        self.assertEqual(self.req("GET", "/api/compare?a=kam/%s&b=kam/%s" % (name, name))[0], 422)
        st, page, _ = self.req("GET", "/compare")
        self.assertEqual(st, 200)
        self.assertIn(b"Compare two words", page)


class Sidebar(Base):
    WORDS = [r.parse_word("കലം|kalam")]

    @classmethod
    def setUpClass(cls):
        cls.dict_dir = tempfile.mkdtemp()
        cls.dict_path = write_file(os.path.join(cls.dict_dir, "enml.csv"), "english,malayalam\npot,\"കലം, കുടം\"\ntime,കാലം\ndog,പട്ടി\n=cmd|' /C calc'!A0,പടി\n")
        cls.dict_hash = sha_file(cls.dict_path)
        cls.tmp = tempfile.mkdtemp()
        cls.dir = os.path.join(cls.tmp, "voice")
        cls.rec = r.Recorder(cls.dir, cls.WORDS, BUILD, r.Dictionary(cls.dict_path))
        cls.srv = r.make_server(cls.rec, 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    def jpost(self, body, token=True, ctype="application/json", raw=None):
        h = {"Content-Type": ctype}
        if token:
            h["X-S2W-Token"] = self.rec.token
        return self.req("POST", "/api/words", raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8"), h)

    def test_dict_search_endpoint(self):
        d = json.loads(self.req("GET", "/api/dict?q=pot")[1])
        self.assertTrue(d["available"])
        self.assertEqual(d["name"], "enml.csv")
        self.assertEqual({x["ml"] for x in d["results"]}, {"കലം", "കുടം"})
        self.assertEqual(json.loads(self.req("GET", "/api/dict?q=")[1])["results"], [])
        self.assertEqual(json.loads(self.req("GET", "/api/dict?q=" + "x" * 200)[1])["results"], [])

    def test_no_dictionary_is_reported(self):
        plain = r.Recorder(os.path.join(self.tmp, "plain"), WORDS, BUILD)
        self.assertFalse(plain.dict_search("pot")["available"])

    def test_add_word_persists_logs_unverified_and_reloads(self):
        before = self.dict_hash
        st, body, _ = self.jpost({"label": "കാലം", "manglish": "kaalam", "meaning": "time", "source": "dictionary: enml.csv"})
        self.assertEqual((st, json.loads(body)["added"]), (200, "kaalam"))
        self.assertIn("kaalam", [w["id"] for w in json.loads(self.req("GET", "/api/words")[1])])
        saved = read_json(os.path.join(self.dir, "words.json"))
        self.assertEqual((saved[-1]["label"], saved[-1]["units"]), ("കാലം", ["k", "aa", "l", "a", "m"]))
        rows = read_csv(os.path.join(self.dir, "candidates_unverified.csv"))
        self.assertEqual((rows[-1]["word_ml"], rows[-1]["status"], rows[-1]["manglish_suggested"]), ("കാലം", "UNVERIFIED", "kaalam"))
        self.assertEqual(r.Recorder(self.dir, [], BUILD).words["kaalam"]["units"], ["k", "aa", "l", "a", "m"])      # survives a restart
        self.assertEqual(sha_file(self.dict_path), before)                     # dictionary only read

    def test_add_word_rejects_bad_requests(self):
        before = self.rec.words.copy()
        self.assertEqual(self.jpost({"label": "പടി", "manglish": "pati"}, token=False)[0], 403)
        self.assertEqual(self.jpost({"label": "പടി", "manglish": "pati"}, ctype="text/plain")[0], 415)
        self.assertEqual(self.jpost(None, raw=b"not json")[0], 422)
        self.assertEqual(self.jpost(None, raw=b"[1,2]")[0], 422)
        self.assertEqual(self.jpost({"label": "പടി", "manglish": "paḍi"})[0], 422)           # non-ASCII Manglish
        self.assertEqual(self.jpost({"label": "പടി", "manglish": "pa di"})[0], 422)
        self.assertEqual(self.jpost({"label": "", "manglish": "pati"})[0], 422)
        self.assertEqual(self.jpost({"label": "പ|ടി", "manglish": "pati"})[0], 422)
        self.assertEqual(self.jpost({"label": "പടി", "manglish": "pati", "units": "p a t i"})[0], 422)
        self.assertEqual(self.jpost({"label": "പടി", "manglish": "pati", "units": ["p", "a", "t"]})[0], 422)   # units must join up to the spelling
        self.assertEqual(self.jpost({"label": "കലം", "manglish": "kalam"})[0], 409)                          # already there
        self.assertEqual(self.jpost(None, raw=b"x" * 5000)[0], 413)
        self.assertEqual(self.rec.words, before)

    def test_pronunciation_units_allowed_to_differ_from_spelling_only_when_flagged(self):
        ok = self.jpost({"label": "കോടി", "manglish": "kodi", "units": ["k", "oo", "t", "i"], "g2p": True, "source": "mlphon"})
        self.assertEqual(ok[0], 200, ok[1])
        self.assertEqual(self.rec.words["kodi"]["units"], ["k", "oo", "t", "i"])
        self.assertEqual(self.jpost({"label": "കോടി", "manglish": "kodi2", "units": ["k", "oo", "t", "i"]})[0], 422)
        self.assertEqual(r.Recorder(self.dir, [], BUILD).words["kodi"]["units"], ["k", "oo", "t", "i"])

    def test_candidate_csv_is_safe_to_open_in_a_spreadsheet(self):
        self.assertEqual(self.jpost({"label": "പടി", "manglish": "padi", "meaning": "=1+1", "source": "@x"})[0], 200)
        rows = read_csv(os.path.join(self.dir, "candidates_unverified.csv"))
        row = next(x for x in rows if x["manglish_suggested"] == "padi")
        self.assertTrue(row["meaning_en"].startswith("'=") and row["source"].startswith("'@"))

    def test_limit_on_word_list_size(self):
        small = r.Recorder(os.path.join(self.tmp, "small"), WORDS, BUILD)      # starts with 2 words
        small.MAX_WORDS = 3
        small.add_word("മഴ", "mazha")
        with self.assertRaises(v.ViewerError) as c:
            small.add_word("പടി", "pati")
        self.assertEqual(c.exception.status, 422)


class Cli(unittest.TestCase):
    def run_cli(self, *a):
        return subprocess.run([sys.executable, TOOL, "--build", BUILD] + list(a), capture_output=True, text=True, timeout=30)

    def test_dictionary_errors_are_clean_and_words_optional_with_a_dictionary(self):
        d = tempfile.mkdtemp()
        res = self.run_cli("--record-dir", d, "--dictionary", os.path.join(d, "missing.csv"), "--port", str(free_port()))
        self.assertEqual(res.returncode, 2)
        self.assertIn("dictionary file not found", res.stderr)
        self.assertNotIn("Traceback", res.stderr)
        csvp = write_file(os.path.join(d, "d.csv"), "english,malayalam\npot,കലം\n")
        port = free_port()
        p = subprocess.Popen([sys.executable, TOOL, "--build", BUILD, "--record-dir", d, "--dictionary", csvp, "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(50):
                try:
                    page = urllib.request.urlopen("http://127.0.0.1:%d/api/dict?q=pot" % port).read().decode()
                    break
                except OSError:
                    time.sleep(0.1)
            self.assertIn("കലം", json.loads(page)["results"][0]["ml"])
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=10), 0)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdout.close()
            p.stderr.close()

    def test_no_word_or_bad_word_is_a_clean_error(self):
        d = tempfile.mkdtemp()
        for args in (["--record-dir", d], ["--record-dir", d, "--word", "കലം|kaḷam"]):
            res = self.run_cli(*args, "--port", str(free_port()))
            self.assertEqual(res.returncode, 2)
            self.assertIn("error:", res.stderr)
            self.assertNotIn("Traceback", res.stderr)

    def test_serves_and_sigterm_exits_cleanly(self):
        d, port = tempfile.mkdtemp(), free_port()
        p = subprocess.Popen([sys.executable, TOOL, "--build", BUILD, "--record-dir", d, "--word", "കലം|kalam", "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            page = None
            for _ in range(50):
                try:
                    page = urllib.request.urlopen("http://127.0.0.1:%d/" % port).read().decode()
                    break
                except OSError:
                    time.sleep(0.1)
            self.assertIn("Voice Recorder", page or "")
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=10), 0)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdout.close()
            p.stderr.close()


if __name__ == "__main__":
    unittest.main()
