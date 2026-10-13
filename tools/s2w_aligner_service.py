#!/usr/bin/env python3
"""s2w_aligner_service: a tiny LOCAL HTTP service that tells the voice recorder where the spoken WORD starts and ends in a recording,
using a speech model you already have (faster-whisper / CTranslate2 models, e.g. the Rootit Voice `vegam-ml` Malayalam Whisper).

It keeps the ML packages out of this repository and out of the Docker container: run it on Windows with the Python that already
has faster-whisper installed (Rootit Voice's backend venv), then start the recorder with --aligner-url.

  "D:\\Apps\\Rootit Voice\\backend\\.venv-py310\\Scripts\\python.exe" tools\\s2w_aligner_service.py --model vegam-ml --device cpu
  (recorder in Docker)   ... python3 tools/s2w_recorder.py ... --aligner-url http://host.docker.internal:8780

API:  POST /align?lang=ml   body = 16-bit mono WAV   ->  {"ok":true,"model":..,"text":..,"start":s,"end":e,"words":[{"t","s","e"}]}
      GET  /health

What it is and is not: Whisper word timestamps locate the WORD (start and end) well enough to trim the silence, but they are
NOT per-sound (K/A/L/A/M) boundaries; the recorder still splits inside the word from spectral change and you correct by hand.
Accuracy on Malayalam is unmeasured here: compare against your hand-corrected take_NNN.segments.json files before trusting it.
Loopback only (127.0.0.1). Audio is processed in memory and never written to disk or sent anywhere.
"""
import argparse
import glob
import io
import json
import os
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BYTES = 20 * 1024 * 1024


def add_cuda_dlls():
    """Make pip-installed nvidia-cublas/cudnn DLLs visible to CTranslate2 (same trick as Rootit Voice)."""
    if sys.platform != "win32":
        return
    for site in {p for p in sys.path if p.endswith("site-packages")}:
        for d in glob.glob(os.path.join(site, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass


def model_path(name, models_dir):
    """A folder path, or a model name looked up in the Rootit Voice models folder, else passed to faster-whisper as is."""
    if os.path.isdir(name):
        return name
    for base in (models_dir, os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "rootit-voice", "models")):
        if base and os.path.isdir(os.path.join(base, name)):
            return os.path.join(base, name)
    return name


def wav_to_16k(data):
    """16-bit mono WAV bytes -> float32 numpy array at 16 kHz (what Whisper expects)."""
    import numpy as np
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("need a 16-bit mono WAV")
        rate, raw = w.getframerate(), w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if rate != 16000:
        try:
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(16000, rate)
            x = resample_poly(x, 16000 // g, rate // g).astype("float32")
        except ImportError:
            t = np.arange(int(len(x) * 16000 / rate)) * rate / 16000.0
            x = np.interp(t, np.arange(len(x)), x).astype("float32")
    return x, len(raw) // 2 / rate


class Aligner:
    def __init__(self, model, device, compute, models_dir):
        self.name, self.device, self.compute = model, device, compute
        self.path = model_path(model, models_dir)
        self.model = None
        self.error = None
        self.lock = threading.Lock()

    def load(self):
        try:
            add_cuda_dlls()
            from faster_whisper import WhisperModel
            compute = self.compute or ("float16" if self.device == "cuda" else "int8")
            self.model = WhisperModel(self.path, device=self.device, compute_type=compute)
        except Exception as e:  # report through /health and /align instead of dying
            self.error = "could not load model %r on %s: %s" % (self.name, self.device, e)
            print("error:", self.error, file=sys.stderr)

    def align(self, data, lang):
        if self.model is None:
            return {"ok": False, "error": self.error or "model is still loading"}
        audio, dur = wav_to_16k(data)
        with self.lock:
            segs, info = self.model.transcribe(audio, language=lang or None, task="transcribe", word_timestamps=True, vad_filter=False,
                                               beam_size=5, condition_on_previous_text=False, temperature=0.0)
            words, text = [], []
            for s in segs:
                text.append(s.text.strip())
                for w in (s.words or []):
                    if w.word.strip():
                        words.append({"t": w.word.strip(), "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
        if not words:
            return {"ok": False, "error": "the model found no speech in this recording", "model": self.name}
        return {"ok": True, "model": self.name, "text": " ".join(text).strip(), "start": min(w["s"] for w in words),
                "end": min(dur, max(w["e"] for w in words)), "words": words, "duration": round(dur, 3)}


def make_handler(al):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def js(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if urlparse(self.path).path == "/health":
                return self.js(200, {"ok": al.model is not None, "model": al.name, "device": al.device, "error": al.error})
            self.js(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/align":
                return self.js(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if not 44 < n <= MAX_BYTES:
                return self.js(413, {"ok": False, "error": "send a WAV of at most 20 MB"})
            data = self.rfile.read(n)
            try:
                self.js(200, al.align(data, (parse_qs(u.query).get("lang") or ["ml"])[0]))
            except Exception as e:
                self.js(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="vegam-ml", help="folder path or a name inside the Rootit Voice models folder (default vegam-ml)")
    ap.add_argument("--models-dir", help="folder holding model sub-folders (default %%APPDATA%%\\rootit-voice\\models)")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    ap.add_argument("--compute-type", help="CTranslate2 compute type (default int8 on cpu, float16 on cuda)")
    ap.add_argument("--port", type=int, default=8780)
    a = ap.parse_args()
    al = Aligner(a.model, a.device, a.compute_type, a.models_dir)
    threading.Thread(target=al.load, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(al))
    print("Sound2Word aligner service at http://127.0.0.1:%d  model=%s device=%s (loading in the background)  - Ctrl+C to stop" % (a.port, a.model, a.device), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
