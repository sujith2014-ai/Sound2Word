"""Speed / memory benchmark of one model. usage: bench.py MODEL(large-v3|vegam-ml|indic-core) DEVICE WAV [WAV...]
Prints one JSON line. Accuracy is NOT measured here (no Malayalam ground truth available)."""
import ctypes
import glob
import json
import os
import sys
import time
import wave
from ctypes import wintypes

import numpy as np

model, device, wavs = sys.argv[1], sys.argv[2], sys.argv[3:]
base = os.path.join(os.environ["APPDATA"], "rootit-voice", "models", model)
if model == "large-v3-turbo":
    base = sorted(glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/*")))[-1]


def rss_mb():
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    c = PMC()
    c.cb = ctypes.sizeof(c)
    k=ctypes.WinDLL('kernel32');k.GetCurrentProcess.restype=ctypes.c_void_p
    pp=ctypes.WinDLL('psapi');pp.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD]
    pp.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 2 ** 20


def load_wav(p):
    with wave.open(p) as w:
        sr, raw = w.getframerate(), w.readframes(w.getnframes())
    x = np.frombuffer(raw, "<i2").astype("float32") / 32768
    if sr != 16000:
        t = np.arange(int(len(x) * 16000 / sr)) * sr / 16000
        x = np.interp(t, np.arange(len(x)), x).astype("float32")
    return x


clips = [load_wav(p) for p in wavs]
out = {"model": model, "device": device, "clips_s": [round(len(c) / 16000, 2) for c in clips]}
t0 = time.time()
if model == "indic-core":
    sys.path.insert(0, base)
    import torch
    from indic_transcribe import IndicTranscribe
    asr = IndicTranscribe.from_pretrained(base, device=device)
    out["load_s"] = round(time.time() - t0, 1)

    def run(x):
        return str(asr(x, lang="ml")).strip()
    sync = torch.cuda.synchronize if device == "cuda" else (lambda: None)
else:
    if sys.platform == "win32":
        for site in {p for p in sys.path if p.endswith("site-packages")}:
            for d in glob.glob(os.path.join(site, "nvidia", "*", "bin")):
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    from faster_whisper import WhisperModel
    m = WhisperModel(base, device=device, compute_type="float16" if device == "cuda" else "int8")
    out["load_s"] = round(time.time() - t0, 1)

    def run(x):
        segs, _ = m.transcribe(x, language="ml", task="transcribe", word_timestamps=True, vad_filter=False, beam_size=5,
                               condition_on_previous_text=False, temperature=0.0)
        return " ".join(s.text.strip() for s in segs)
    sync = lambda: None

run(clips[0])                                    # warm-up
sync()
res = []
for x in clips:
    ts = []
    for _ in range(3):
        t = time.time()
        txt = run(x)
        sync()
        ts.append(time.time() - t)
    ts.sort()
    res.append({"audio_s": round(len(x) / 16000, 2), "median_s": round(ts[1], 2), "rtf": round(ts[1] / (len(x) / 16000), 2), "text": txt[:60]})
out["runs"] = res
out["peak_ram_mb"] = round(rss_mb())
if device == "cuda":
    try:
        import torch
        out["torch_vram_peak_mb"] = round(torch.cuda.max_memory_allocated() / 2 ** 20)
    except Exception:
        pass
print("RESULT " + json.dumps(out, ensure_ascii=False))

