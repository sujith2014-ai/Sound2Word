"""Generate SYNTHETIC unit clips with LFM-MALAYALAM-TTS for an inventory.json (see gen_units.py).
usage (Rootit .venv-indic, PYTHONPATH=tts\pkgs;tts\VyvoTTS):  gen_lfm_units.py UNITS_DIR OUT_ROOT"""
import json, os, sys, time
units_dir, out_root = sys.argv[1], sys.argv[2]
inv = json.load(open(os.path.join(units_dir, "inventory.json"), encoding="utf-8"))
out = os.path.join(out_root, "units_raw")
os.makedirs(out, exist_ok=True)
from vyvotts.inference.transformers_inference import VyvoTTSTransformersInference
eng = VyvoTTSTransformersInference(config_path=r"D:\Apps\Sound2Word\data\private\tts\VyvoTTS\vyvotts\configs\inference\lfm2.yaml",
                                   model_name="Praha-Labs/LFM-MALAYALAM-TTS-v0.1", codec_type="snac", device="cuda")
items = []
for u in inv:
    fn = "u%04d.wav" % u["id"]
    audio, _ = eng.generate(u["text"], output_path=os.path.join(out, fn), temperature=0.4, max_new_tokens=300)
    items.append({"i": len(items) + 1, "unit_id": u["id"], "word_ml": u["text"], "manglish": "u%04d" % u["id"], "kind": u["kind"], "uses": u["uses"],
                  "file": fn if audio is not None else "", "audio_s": None if audio is None else round(float(audio.shape[-1]) / 24000, 3),
                  "model": "LFM-MALAYALAM-TTS-v0.1", "voice": "default", "synthetic": True})
json.dump({"items": items}, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", len(items))
