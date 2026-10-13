"""Round-trip check: transcribe each synthetic WAV with indic-core and compare with the intended word. usage: roundtrip.py DIR"""
import json, os, sys, unicodedata, re
import numpy as np
import soundfile as sf
sys.stdout.reconfigure(encoding="utf-8")
d = sys.argv[1]
meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
base = os.path.join(os.environ["APPDATA"], "rootit-voice", "models", "indic-core")
sys.path.insert(0, base)
from indic_transcribe import IndicTranscribe
asr = IndicTranscribe.from_pretrained(base, device="cuda")
CH = {"ൻ": "ന്", "ൺ": "ണ്", "ൽ": "ല്", "ൾ": "ള്", "ർ": "ര്", "ൿ": "ക്"}


def norm(s):
    s = unicodedata.normalize("NFC", s)
    s = "".join(CH.get(c, c) for c in s).replace("‌", "").replace("‍", "")
    return re.sub(r"[^ഀ-ൿ]", "", s)


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


tot_e = tot_n = exact = 0
for it in meta["items"]:
    if not it["file"]:
        it["heard"], it["cer"] = "", 1.0
        continue
    x, sr = sf.read(os.path.join(d, it["file"]), dtype="float32")
    if sr != 16000:                                   # FFT resampling (no scipy in this environment)
        n_out = int(round(len(x) * 16000 / sr))
        X = np.fft.rfft(x)
        Y = np.zeros(n_out // 2 + 1, dtype=X.dtype)
        m = min(len(X), len(Y))
        Y[:m] = X[:m]
        x = (np.fft.irfft(Y, n_out) * (n_out / len(x))).astype("float32")
    x = np.concatenate([np.zeros(3200, "float32"), x, np.zeros(3200, "float32")])   # a little padding for the recogniser
    heard = str(asr(x, lang="ml")).strip()
    ref, hyp = norm(it["word_ml"]), norm(heard)
    it["heard"], it["cer"] = heard, round(lev(ref, hyp) / max(1, len(ref)), 2)
    it["exact"] = ref == hyp
    tot_e += lev(ref, hyp)
    tot_n += len(ref)
    exact += ref == hyp
    print("%2d %-14s heard %-22s CER %.2f %s" % (it["i"], it["word_ml"], heard, it["cer"], "EXACT" if ref == hyp else ""), flush=True)
meta["summary"] = {"words": len(meta["items"]), "exact": exact, "corpus_cer": round(tot_e / tot_n, 3)}
json.dump(meta, open(os.path.join(d, "meta_scored.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("SUMMARY", meta["summary"])
