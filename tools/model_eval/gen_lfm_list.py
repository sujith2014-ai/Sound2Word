"""Generate SYNTHETIC speech with LFM-MALAYALAM-TTS for a sample of lexicon words (for the unit-consistency experiment).
usage (Rootit .venv-indic, PYTHONPATH=tts\\pkgs;tts\\VyvoTTS):  gen_lfm_list.py OUT_DIR N [SEED]
Picks N random lexicon words that have pronunciation data, 3-6 sound units and an English meaning, seen in 2+ sources."""
import csv, json, os, random, sys, time
csv.field_size_limit(10 ** 7)
out, n = sys.argv[1], int(sys.argv[2])
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 0
os.makedirs(out, exist_ok=True)
pool = []
with open(r"D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv", encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        u = r["sound_units"].split()
        if r["g2p"] == "yes" and 3 <= len(u) <= 6 and r["meaning_en"] and " + " in r["sources"] and r["word_ml"].isalpha():
            pool.append((r["word_ml"], r["manglish"], u))
random.Random(seed).shuffle(pool)
pool = pool[:n]
t0 = time.time()
from vyvotts.inference.transformers_inference import VyvoTTSTransformersInference
eng = VyvoTTSTransformersInference(config_path=r"D:\Apps\Sound2Word\data\private\tts\VyvoTTS\vyvotts\configs\inference\lfm2.yaml",
                                   model_name="Praha-Labs/LFM-MALAYALAM-TTS-v0.1", codec_type="snac", device="cuda")
print("loaded %.0fs, %d words" % (time.time() - t0, len(pool)), flush=True)
items = []
for i, (ml, rom, units) in enumerate(pool, 1):
    fn = "w%03d_%s.wav" % (i, rom)
    audio, _ = eng.generate(ml, output_path=os.path.join(out, fn), temperature=0.4, max_new_tokens=600)
    items.append({"i": i, "word_ml": ml, "manglish": rom, "units": units, "file": fn if audio is not None else "",
                  "audio_s": None if audio is None else round(float(audio.shape[-1]) / 24000, 3),
                  "model": "Praha-Labs/LFM-MALAYALAM-TTS-v0.1", "voice": "default (single voice)", "synthetic": True})
    if i % 20 == 0:
        print(i, round(time.time() - t0), flush=True)
        json.dump({"items": items}, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump({"items": items}, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", round(time.time() - t0))
