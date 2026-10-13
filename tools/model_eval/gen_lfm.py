"""Generate SYNTHETIC speech for the first N lexicon words with LFM-MALAYALAM-TTS. usage: gen_lfm.py OUT_DIR [N]"""
import csv, json, os, sys, time
csv.field_size_limit(10 ** 7)
out = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
os.makedirs(out, exist_ok=True)
rows = []
with open(r"D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv", encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        rows.append(r)
        if len(rows) >= n:
            break
t0 = time.time()
from vyvotts.inference.transformers_inference import VyvoTTSTransformersInference
eng = VyvoTTSTransformersInference(config_path=r"D:\Apps\Sound2Word\data\private\tts\VyvoTTS\vyvotts\configs\inference\lfm2.yaml",
                                   model_name="Praha-Labs/LFM-MALAYALAM-TTS-v0.1", codec_type="snac", device="cuda")
load_s = time.time() - t0
meta = []
for i, r in enumerate(rows, 1):
    p = os.path.join(out, "w%02d_%s.wav" % (i, r["manglish"]))
    t = time.time()
    audio, timing = eng.generate(r["word_ml"], output_path=p, temperature=0.4, max_new_tokens=600)
    dur = None if audio is None else float(audio.shape[-1]) / 24000
    meta.append({"i": i, "word_ml": r["word_ml"], "manglish": r["manglish"], "file": os.path.basename(p) if audio is not None else "",
                 "audio_s": dur, "gen_s": round(time.time() - t, 2), "model": "Praha-Labs/LFM-MALAYALAM-TTS-v0.1", "codec": "snac_24khz",
                 "voice": "default (single voice)", "synthetic": True})
    print(meta[-1], flush=True)
json.dump({"load_s": round(load_s, 1), "items": meta}, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE")
