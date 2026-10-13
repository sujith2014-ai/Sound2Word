"""Generate SYNTHETIC speech with ai4bharat/indic-parler-tts. usage: gen_parler.py OUT_ROOT [VARIANT]
Sets: first10 (first 10 lexicon words, same as the LFM test) and common10 (everyday words). Voices: Anjali, Harish (recommended Malayalam)."""
import csv, json, os, sys, time
import soundfile as sf
import torch
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer

root = sys.argv[1]
VARIANT = sys.argv[2] if len(sys.argv) > 2 else "cap_dot"   # base | cap | cap_dot | cap_dot_cold. cap_dot is the default: without a length cap and a full stop
# the model keeps talking after the word (24/40 exact vs 9/40 for base on 20 words x 2 voices, indic-core round trip, 2026-10-13)
csv.field_size_limit(10 ** 7)
first10 = []
with open(r"D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv", encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        first10.append((r["word_ml"], r["manglish"]))
        if len(first10) >= 10:
            break
common10 = [("കലം", "kalam"), ("കാലം", "kaalam"), ("മഴ", "mazha"), ("പട്ടി", "patti"), ("വീട്", "veedu"),
            ("പുഴ", "puzha"), ("ആന", "aana"), ("പൂവ്", "poovu"), ("കണ്ണ്", "kannu"), ("ചായ", "chaaya")]
SETS = {"first10": first10, "common10": common10}
units_of = {}
with open(r"D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv", encoding="utf-8", newline="") as f2:
    want = {w for w, _ in first10 + common10}
    for r2 in csv.DictReader(f2):
        if r2["word_ml"] in want:
            units_of[r2["word_ml"]] = r2["sound_units"].split()
VOICES = ["Anjali", "Harish"]
dev = "cuda:0" if torch.cuda.is_available() else "cpu"
t0 = time.time()
M = "ai4bharat/indic-parler-tts"
model = ParlerTTSForConditionalGeneration.from_pretrained(M).to(dev)
tok = AutoTokenizer.from_pretrained(M)
dtok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
print("loaded in %.1fs on %s" % (time.time() - t0, dev), flush=True)
for sname, words in SETS.items():
    for v in VOICES:
        out = os.path.join(root, "%s_%s" % (sname, v.lower()))
        os.makedirs(out, exist_ok=True)
        desc = ("%s speaks in a clear, neutral tone at a slow pace, pronouncing the word carefully and distinctly. "
                "The recording is very high quality with no background noise and no reverberation." % v)
        items = []
        for i, (ml, rom) in enumerate(words, 1):
            t = time.time()
            d = dtok(desc, return_tensors="pt").to(dev)
            text = ml + "." if "dot" in VARIANT else ml
            p = tok(text, return_tensors="pt").to(dev)
            kw = {}
            if "cap" in VARIANT:                                   # a word is short: allow ~0.5 s + 0.22 s per sound, 86 frames/s + codebook delay
                n_units = max(1, len(units_of.get(ml, [1, 1, 1])))
                kw["max_new_tokens"] = int(86 * max(0.8, 0.22 * n_units + 0.5)) + 10
            if "cold" in VARIANT:
                kw["temperature"] = 0.3
            torch.manual_seed(0)
            with torch.no_grad():
                g = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask, prompt_input_ids=p.input_ids,
                                   prompt_attention_mask=p.attention_mask, **kw)
            x = g.cpu().numpy().squeeze()
            fn = "w%02d_%s.wav" % (i, rom)
            sf.write(os.path.join(out, fn), x, model.config.sampling_rate)
            items.append({"i": i, "word_ml": ml, "manglish": rom, "file": fn, "audio_s": round(len(x) / model.config.sampling_rate, 2),
                          "gen_s": round(time.time() - t, 2), "model": M, "voice": v, "caption": desc, "synthetic": True})
            print(sname, v, items[-1]["i"], ml, items[-1]["audio_s"], items[-1]["gen_s"], flush=True)
        json.dump({"items": items}, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE")
