"""Generate SYNTHETIC audio for every sound unit in data/private/units/inventory.json with indic-parler-tts (voice Anjali).
usage (venv_parler):  gen_units.py UNITS_DIR OUT_RAW_DIR [VOICE=Anjali]
Writes OUT_RAW_DIR/units_raw/<uNNNN>.wav + meta.json (items: word_ml = the unit text, units = [id]). Next: trim_clips.py, then roundtrip.py.
Each unit is spoken alone with a length cap (about 0.8 s) and a full stop, as in gen_parler.py (the variant that stops after the word)."""
import json
import os
import sys
import time

import soundfile as sf
import torch
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer

units_dir, out_root = sys.argv[1], sys.argv[2]
voice = sys.argv[3] if len(sys.argv) > 3 else "Anjali"
inv = json.load(open(os.path.join(units_dir, "inventory.json"), encoding="utf-8"))
out = os.path.join(out_root, "units_raw")
os.makedirs(out, exist_ok=True)
dev = "cuda:0" if torch.cuda.is_available() else "cpu"
M = "ai4bharat/indic-parler-tts"
model = ParlerTTSForConditionalGeneration.from_pretrained(M).to(dev)
tok = AutoTokenizer.from_pretrained(M)
dtok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
desc = ("%s speaks in a clear, neutral tone at a slow pace, pronouncing the sound carefully and distinctly. "
        "The recording is very high quality with no background noise and no reverberation." % voice)
d = dtok(desc, return_tensors="pt").to(dev)
items, t0 = [], time.time()
meta_path = os.path.join(out, "meta.json")
done = {}
if os.path.isfile(meta_path):                                      # resume
    for it in json.load(open(meta_path, encoding="utf-8"))["items"]:
        done[it["unit_id"]] = it
for k, u in enumerate(inv):
    if u["id"] in done and os.path.isfile(os.path.join(out, done[u["id"]]["file"])):
        items.append(done[u["id"]])
        continue
    p = tok(u["text"] + ".", return_tensors="pt").to(dev)
    torch.manual_seed(0)
    with torch.no_grad():
        g = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask, prompt_input_ids=p.input_ids,
                           prompt_attention_mask=p.attention_mask, max_new_tokens=int(86 * 0.8) + 10)
    x = g.cpu().numpy().squeeze()
    fn = "u%04d.wav" % u["id"]
    sf.write(os.path.join(out, fn), x, model.config.sampling_rate)
    items.append({"i": len(items) + 1, "unit_id": u["id"], "word_ml": u["text"], "manglish": "u%04d" % u["id"], "kind": u["kind"], "uses": u["uses"],
                  "file": fn, "audio_s": round(len(x) / model.config.sampling_rate, 3), "model": M, "voice": voice, "synthetic": True})
    if len(items) % 25 == 0:
        json.dump({"items": items}, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(len(items), "/", len(inv), round(time.time() - t0), "s", flush=True)
json.dump({"items": items}, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", len(items), round(time.time() - t0), "s", flush=True)
