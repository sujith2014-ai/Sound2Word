"""usage: cmp.py MODEL DEVICE WAV OUT.json [LANG]  -> transcript (+ word timestamps for Whisper-family) and timings."""
import glob, json, os, sys, time, wave
import numpy as np

model, device, wav, out, lang = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], (sys.argv[5] if len(sys.argv) > 5 else None)
base = os.path.join(os.environ["APPDATA"], "rootit-voice", "models", model)
if model == "large-v3-turbo":
    base = sorted(glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/*")))[-1]
with wave.open(wav) as w:
    x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype("float32") / 32768
res = {"model": model, "device": device, "audio_s": round(len(x) / 16000, 1), "lang_arg": lang}
t0 = time.time()
if model == "indic-core":
    sys.path.insert(0, base)
    import torch
    from indic_transcribe import IndicTranscribe
    from long_form import transcribe_long
    asr = IndicTranscribe.from_pretrained(base, device=device)
    res["load_s"] = round(time.time() - t0, 1)
    t = time.time()
    text = transcribe_long(asr, x, lang=lang)
    res["transcribe_s"] = round(time.time() - t, 1)
    res["text"] = str(text).strip()
    res["vram_peak_mb"] = round(torch.cuda.max_memory_allocated() / 2 ** 20)
else:
    for site in {p for p in sys.path if p.endswith("site-packages")}:
        for d in glob.glob(os.path.join(site, "nvidia", "*", "bin")):
            os.add_dll_directory(d); os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    from faster_whisper import WhisperModel
    m = WhisperModel(base, device=device, compute_type="float16" if device == "cuda" else "int8")
    res["load_s"] = round(time.time() - t0, 1)
    t = time.time()
    segs, info = m.transcribe(x, language=lang, task="transcribe", word_timestamps=True, vad_filter=False, beam_size=5, condition_on_previous_text=False)
    segs = list(segs)
    res["transcribe_s"] = round(time.time() - t, 1)
    res["language"], res["language_prob"] = info.language, round(info.language_probability, 3)
    res["text"] = " ".join(s.text.strip() for s in segs)
    res["words"] = [{"t": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 2)} for s in segs for w in (s.words or [])]
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", model, lang, res.get("language"), res["load_s"], res["transcribe_s"], len(res["text"]))
