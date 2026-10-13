#!/usr/bin/env python3
"""s2w_viewer: local, offline, READ-ONLY Acoustic Viewer for Sound2Word (Python standard library only).

  python3 tools/s2w_viewer.py --dataset DATASET --run RUN_DIR [--review master.xlsx] [--build build] [--port 8765]

Serves http://127.0.0.1:PORT (loopback only). Everything shown is computed from the real WAV samples and from the
existing C tools (s2w_features for the spectrogram, s2w_qa for quality, s2w_match for template distances).
It never writes to the dataset, the run folder or the review workbook, and it cannot approve vocabulary.

Segments are a spectral-change ESTIMATE and are produced only for kind=synthetic (tone) datasets. For real speech no
automatic segmentation is attempted (not implemented; letters of a word are never used as boundaries).
"""
import argparse
import array
import csv
import json
import os
import subprocess
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

try:
    import numpy as np  # optional: only used for the F0 estimate
except ImportError:  # pragma: no cover
    np = None


# ---------------------------------------------------------------- data access
def read_tagged_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        first = f.readline()
        if not first.startswith("#"):
            f.seek(0)
        return list(csv.DictReader(f))


def read_meta(path):
    meta = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if "=" in ln:
                k, v = ln.strip().split("=", 1)
                meta[k] = v
    return meta


def load_wav(path):
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("viewer reads 16-bit mono WAV only")
        n, rate = w.getnframes(), w.getframerate()
        a = array.array("h")
        a.frombytes(w.readframes(n))
        if sys.byteorder == "big":
            a.byteswap()
    return a, rate


def peaks(samples, bins):
    """Per-bin (min, max) in [-1, 1] from the real samples; bins never exceed len(samples)."""
    n = len(samples)
    bins = max(1, min(bins, n))
    out = []
    for b in range(bins):
        lo, hi = b * n // bins, (b + 1) * n // bins
        seg = samples[lo:hi] if hi > lo else samples[lo:lo + 1]
        out.append((min(seg) / 32768.0, max(seg) / 32768.0))
    return out


def parse_matrix(text, name):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        p = ln.split()
        if len(p) == 4 and p[0] == "matrix" and p[1] == name:
            r = int(p[2])
            return [[float(x) for x in lines[i + 1 + k].split()] for k in range(r)]
    raise ValueError("matrix %s not found" % name)


def median(v):
    s = sorted(v)
    return s[len(s) // 2]


def estimate_segments(mat, hop=0.010, frame=0.025, min_frames=4, jump_bins=3):
    """Spectral-change segments for TONE-like synthetic audio: runs of frames whose strongest mel bin stays put.
    Returns [{start,end,bin,confidence}] in seconds. An estimate; says nothing about phonetic segments of speech."""
    if not mat:
        return []
    energy = [max(r) for r in mat]
    lo, hi = min(energy), max(energy)
    thr = lo + 0.35 * (hi - lo)
    act = [i for i, e in enumerate(energy) if e > thr]
    if not act:
        return []
    bins = [max(range(len(r)), key=lambda j: r[j]) for r in mat]
    runs, cur = [], []
    for i in range(act[0], act[-1] + 1):
        if cur and abs(bins[i] - median([bins[j] for j in cur])) >= jump_bins:
            runs.append(cur)
            cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    merged = []
    for r in runs:                               # fold runs shorter than min_frames into the previous run
        if merged and len(r) < min_frames:
            merged[-1].extend(r)
        else:
            merged.append(list(r))
    out = []
    for r in merged:
        b = median([bins[j] for j in r])
        agree = sum(1 for j in r if abs(bins[j] - b) < jump_bins) / len(r)
        out.append({"start": r[0] * hop, "end": r[-1] * hop + frame, "bin": b, "confidence": round(agree, 2)})
    return out


def f0_track(samples, rate, lo=60.0, hi=450.0):
    """Normalised-autocorrelation F0 estimate (40 ms frames, 10 ms step). None where unvoiced or numpy is missing."""
    if np is None:
        return None
    x = np.asarray(samples, dtype=np.float64) / 32768.0
    fl, step = int(0.04 * rate), int(0.01 * rate)
    lmin, lmax = int(rate / hi), int(rate / lo)
    out = []
    for s in range(0, max(0, len(x) - fl), step):
        f = x[s:s + fl] - x[s:s + fl].mean()
        e = float(f @ f)
        if e < 1e-4 * fl:
            out.append(None)
            continue
        ac = np.correlate(f, f, "full")[fl - 1:]
        lag = lmin + int(np.argmax(ac[lmin:lmax]))
        out.append(round(rate / lag, 1) if ac[lag] / ac[0] > 0.6 else None)
    return out


# ---------------------------------------------------------------- application
class App:
    def __init__(self, dataset, run, build="build", review=None):
        self.dataset, self.run, self.build, self.review = dataset, run, build, review
        self.meta = read_meta(os.path.join(dataset, "dataset_meta.txt"))
        self.recs = {r["recording_id"]: r for r in read_tagged_csv(os.path.join(dataset, "recordings.csv"))}
        self.words = {w["word_id"]: w for w in read_tagged_csv(os.path.join(dataset, "wordlist.csv"))}
        self.label_to_word = {w["label"]: w["word_id"] for w in self.words.values()}
        with open(os.path.join(run, "queries.csv"), encoding="utf-8", newline="") as f:
            self.queries = list(csv.DictReader(f))
        with open(os.path.join(run, "settings.cfg"), encoding="utf-8") as f:
            self.settings = f.read()
        self.synthetic = self.meta.get("kind") == "synthetic"

    def tool(self, name):
        return os.path.join(self.build, name)

    def wav_path(self, rid):
        r = self.recs.get(rid)
        return None if r is None else os.path.join(self.dataset, r["path"])

    def index(self):
        qs = []
        for q in self.queries:
            w = self.words.get(q["word_id"], {})
            qs.append({"id": q["recording_id"], "word": q["word_id"], "label": w.get("label", q["word_id"]), "speaker": q["speaker_id"],
                       "role": q["role"], "decision": q["decision"], "usable": q["usable"], "margin": q["margin_ratio"], "warp": q.get("warp_alpha", ""),
                       "quality": q["quality_warnings"],
                       "cands": [{"label": q["top%d" % k], "word": self.label_to_word.get(q["top%d" % k], ""), "dist": q["top%d_distance" % k]}
                                 for k in (1, 2, 3) if q.get("top%d" % k)],
                       "correct": bool(q["top1"]) and self.label_to_word.get(q["top1"]) == q["word_id"]})
        return {"dataset_id": self.meta.get("dataset_id", ""), "kind": self.meta.get("kind", ""), "protocol": self.meta.get("protocol", ""),
                "run": os.path.basename(os.path.normpath(self.run)), "settings": self.settings, "queries": qs}

    def recording(self, rid, bins=1200):
        path = self.wav_path(rid)
        if path is None:
            return None
        samples, rate = load_wav(path)
        r = self.recs[rid]
        out = {"id": rid, "speaker": r["speaker_id"], "split": r["split"], "word": r["word_id"], "label": self.words.get(r["word_id"], {}).get("label", ""),
               "sample_rate": rate, "duration": len(samples) / rate, "n_samples": len(samples), "peaks": peaks(samples, bins), "qa": self.qa(path),
               "f0": None if self.synthetic else f0_track(samples, rate), "f0_note": "F0 is not meaningful for synthetic tone signals and is not shown." if self.synthetic else "", "segments": [], "segment_note": "No automatic segmentation for real speech (not implemented)."}
        feat = subprocess.run([self.tool("s2w_features"), path, "--out", "logmel"], capture_output=True, text=True)
        if feat.returncode == 0:
            mat = parse_matrix(feat.stdout, "logmel_raw")
            out["spec"] = {"rows": len(mat), "cols": len(mat[0]), "hop": 0.010, "frame": 0.025, "data": [[round(v, 2) for v in row] for row in mat]}
            if self.synthetic:
                out["segments"] = estimate_segments(mat)
                out["segment_note"] = "ESTIMATE from spectral changes of synthetic tone audio; not phonetic labels."
        return out

    def qa(self, path):
        p = subprocess.run([self.tool("s2w_qa"), path], capture_output=True, text=True)
        return dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln)

    def match(self, rid, k=5):
        path = self.wav_path(rid)
        if path is None:
            return None
        enrol = [r for r in self.recs.values() if r["split"] == "enrol"]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as m:
            for r in enrol:                              # one label per template -> per-template ranking from the real engine
                m.write("%s %s\n" % (r["recording_id"], os.path.join(self.dataset, r["path"])))
        try:
            p = subprocess.run([self.tool("s2w_match"), m.name, path, "--k", str(k)], capture_output=True, text=True)
        finally:
            os.unlink(m.name)
        res = dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln)
        ranks = []
        for i in range(1, k + 1):
            if "rank%d" % i in res:
                t, d = res["rank%d" % i].split()
                rr = self.recs[t]
                ranks.append({"template": t, "dist": float(d), "word": rr["word_id"], "label": self.words.get(rr["word_id"], {}).get("label", ""), "speaker": rr["speaker_id"]})
        note = "Per-template distances from s2w_match with default matcher settings."
        if any(s in self.settings for s in ("denoise=specsub", "warp_grid=")):
            note += " This run used denoise/warp, which this lookup does NOT apply."
        return {"ranks": ranks, "margin_ratio": res.get("margin_ratio"), "note": note}

    def verification(self):
        out = {"available": False, "words": [], "note": "Approval happens in the review workbook by two human reviewers. This viewer is read-only and cannot approve."}
        if not self.review or not os.path.exists(self.review):
            return out
        try:
            import openpyxl
        except ImportError:
            out["note"] += " (openpyxl missing: workbook not read)"
            return out
        wb = openpyxl.load_workbook(self.review, read_only=True, data_only=True)
        ws = wb["Master"]
        rows = list(ws.iter_rows(values_only=True))
        h = {v: i for i, v in enumerate(rows[0])}
        def col(prefix):
            return next(i for v, i in h.items() if str(v).startswith(prefix))
        c = {"id": col("Word ID"), "word": col("Malayalam word"), "roman": col("Romanisation"), "meaning": col("English meaning"), "pair": col("Contrast pair ID"),
             "ctype": col("Contrast type"), "proposer": col("Proposed by"), "status": col("Agreement status")}
        for r in rows[1:]:
            if r[c["id"]]:
                w = {k: ("" if r[i] is None else str(r[i])) for k, i in c.items()}
                if w["status"] == "Empty slot" and w["word"]:
                    w["status"] = "Pending review (status not recomputed; run review_sheet.py status --write)"
                out["words"].append(w)
        out["available"] = True
        return out


# ---------------------------------------------------------------- HTTP
def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def js(self, obj, code=200):
            self.send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            p = unquote(urlparse(self.path).path)
            try:
                if p == "/":
                    return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
                if p == "/api/index":
                    return self.js(app.index())
                if p == "/api/verification":
                    return self.js(app.verification())
                for pre, fn in (("/api/recording/", app.recording), ("/api/match/", app.match)):
                    if p.startswith(pre):
                        r = fn(p[len(pre):])
                        return self.js(r) if r is not None else self.js({"error": "unknown recording"}, 404)
                if p.startswith("/audio/"):
                    path = app.wav_path(p[len("/audio/"):])      # id lookup only: no path is taken from the URL
                    if path is None:
                        return self.js({"error": "unknown recording"}, 404)
                    with open(path, "rb") as f:
                        return self.send(200, f.read(), "audio/wav")
                return self.js({"error": "not found"}, 404)
            except Exception as e:  # report, never crash the server
                return self.js({"error": str(e)}, 500)

        def do_POST(self):
            self.js({"error": "read-only viewer"}, 405)
        do_PUT = do_DELETE = do_PATCH = do_POST
    return H


def make_server(app, port=0):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sound2Word Acoustic Viewer</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2230;--mut:#6b7385;--line:#e3e6ec;--blue:#4285d8;--orange:#e8a43c;--teal:#2d9d78;--red:#c0392b}
@media(prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--ink:#e8ebf1;--mut:#8f98aa;--line:#2b323f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}
header{padding:14px 20px;background:var(--card);border-bottom:1px solid var(--line)}header h1{margin:0;font-size:18px}
.banner{margin-top:8px;padding:8px 12px;border-radius:8px;background:#fff3cd;color:#664d03;font-size:13px}
main{display:grid;grid-template-columns:300px 1fr;gap:16px;padding:16px 20px}@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}
.card h2{margin:0 0 4px;font-size:15px}.sub{color:var(--mut);font-size:12px;margin-bottom:10px}
#list{max-height:70vh;overflow:auto}.q{padding:7px 8px;border-radius:8px;cursor:pointer;display:flex;justify-content:space-between;gap:8px;border:1px solid transparent}
.q:hover{background:var(--bg)}.q.sel{border-color:var(--blue);background:var(--bg)}.ok{color:var(--teal)}.bad{color:var(--red)}
.seg{display:grid;grid-template-columns:44px 1fr 90px;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)}
.badge{width:40px;height:40px;border-radius:10px;color:#fff;font-weight:700;display:grid;place-items:center}
canvas{width:100%;display:block;border-radius:8px;background:var(--bg)}button{font:inherit;padding:5px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--blue)}.bar{display:flex;height:34px;border-radius:8px;overflow:hidden;margin-top:6px}.bar div{display:grid;place-items:center;color:#fff;font-weight:700;font-size:12px}
table{border-collapse:collapse;width:100%}td,th{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px}
.pend{background:#fff3cd;color:#664d03}.meas{background:#d6efe6;color:#0b5a3f}.est{background:#e4e8fb;color:#2b3a8f}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
small{color:var(--mut)}
</style></head><body>
<header><h1>Sound2Word &mdash; Acoustic Viewer</h1><div id="meta" class="sub"></div><div id="banner"></div></header>
<main><div><div class="card"><h2>Queries</h2><div class="sub" id="runinfo"></div><div id="list"></div></div></div>
<div id="view"><div class="card">Select a query on the left.</div></div></main>
<script>
const $=id=>document.getElementById(id),COL=['#4285d8','#e8a43c','#2d9d78','#9b59b6','#d35400'];
let IDX,CUR=null,audio=new Audio(),playEnd=null,sel=null,zoom=1,scroll=0;
const j=u=>fetch(u).then(r=>r.json()),esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function init(){IDX=await j('/api/index');
 $('meta').textContent=`dataset ${IDX.dataset_id} · protocol ${IDX.protocol} · run ${IDX.run} · read-only`;
 if(IDX.kind==='synthetic')$('banner').innerHTML='<div class="banner"><b>SYNTHETIC DEMO DATA</b> &mdash; tone signals with placeholder labels (SYNTH_*). Software demonstration only. Not Malayalam speech and not recognition accuracy.</div>';
 const ok=IDX.queries.filter(q=>q.correct).length;$('runinfo').textContent=`${ok}/${IDX.queries.length} top-1 correct (this run)`;
 $('list').innerHTML=IDX.queries.map(q=>`<div class="q" data-id="${q.id}"><span>${esc(q.id)} · ${esc(q.label)}<br><small>${esc(q.speaker)}</small></span><span class="${q.correct?'ok':'bad'}">${q.correct?'✓':'✗'}</span></div>`).join('');
 document.querySelectorAll('.q').forEach(e=>e.onclick=()=>open(e.dataset.id));
 if(location.hash.length>1)open(location.hash.slice(1));}
async function open(id){document.querySelectorAll('.q').forEach(e=>e.classList.toggle('sel',e.dataset.id===id));
 $('view').innerHTML='<div class="card">Loading…</div>';audio.pause();sel=null;zoom=1;scroll=0;
 const [rec,mat,ver]=await Promise.all([j('/api/recording/'+id),j('/api/match/'+id),j('/api/verification')]);
 const q=IDX.queries.find(x=>x.id===id);CUR={rec,mat,q,ver};audio.src='/audio/'+id;render();}
function fmt(t){return t.toFixed(3)+' s'}
function render(){const {rec,mat,q,ver}=CUR;
 const segs=rec.segments.map((s,i)=>`<div class="seg"><div class="badge" style="background:${COL[i%5]}">S${i+1}</div><div><b>Spectral segment ${i+1}</b> <span class="tag est">estimate</span><br><small>${fmt(s.start)} – ${fmt(s.end)} · confidence ${s.confidence}</small><canvas class="mini" data-i="${i}" height="40"></canvas></div><div><button data-p="${i}">▶ play</button></div></div>`).join('')||`<small>${esc(rec.segment_note)}</small>`;
 const cand=q.cands.map(c=>`<tr><td>${esc(c.label)}</td><td>${(+c.dist).toFixed(4)}</td></tr>`).join('');
 const tm=(mat.ranks||[]).map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.template)}</td><td>${esc(r.label)}</td><td>${esc(r.speaker)}</td><td>${r.dist.toFixed(4)}</td><td><button data-t="${r.template}">▶</button></td></tr>`).join('');
 const qa=rec.qa;
 const vw=ver.available?`<table><tr><th>ID</th><th>Malayalam</th><th>Manglish</th><th>Meaning</th><th>Pair</th><th>Status</th></tr>${ver.words.map(w=>`<tr><td>${esc(w.id)}</td><td>${esc(w.word)}</td><td>${esc(w.roman)}</td><td>${esc(w.meaning)}</td><td>${esc(w.pair)} ${esc(w.ctype)}</td><td><span class="tag pend">${esc(w.status)}</span></td></tr>`).join('')}</table>`:'<small>No review workbook given (--review).</small>';
 const here=IDX.kind==='synthetic'?'This recording is a synthetic placeholder word (<b>'+esc(rec.label)+'</b>): no human verification applies to it.':'';
 $('view').innerHTML=`
 <div class="card"><h2>1. Individual sound segments</h2><div class="sub">${esc(rec.segment_note)} Regions are cut from the actual recording; letters are never used as boundaries.</div>${segs}</div>
 <div class="card"><h2>2. Combined word &mdash; ${esc(rec.label)} <small>(${esc(rec.id)}, speaker ${esc(rec.speaker)})</small></h2>
  <canvas id="wave" height="150"></canvas><canvas id="spec" height="150" style="margin-top:6px"></canvas><canvas id="f0c" height="60" style="margin-top:6px"></canvas>
  <canvas id="segbar" height="30" style="margin-top:6px"></canvas>
  <div class="row"><button id="pall">▶ Play whole word</button><button id="psel">▶ Play selection</button><button id="stop">■ Stop</button>
   <label>zoom <input type="range" id="zoom" min="1" max="8" step="0.5" value="1"></label><label>scroll <input type="range" id="scr" min="0" max="100" value="0"></label></div>
  <div class="row"><span class="tag meas">measured</span> waveform, spectrogram, quality &nbsp;<span class="tag est">estimate</span> segments, F0 &nbsp;<small id="selinfo">drag on the waveform to select a region</small></div></div>
 <div class="card"><h2>3. What the matcher says</h2><div class="sub">Decision <b>${esc(q.decision)}</b> · margin ratio ${esc(q.margin)} · warp α ${esc(q.warp)} · quality flags ${esc(q.quality)} · true word <b>${esc(q.label)}</b></div>
  <table><tr><th>Ranked word (run output)</th><th>Distance</th></tr>${cand}</table>
  <div class="sub" style="margin-top:10px">Nearest enrolment templates. ${esc(mat.note||'')}</div><table><tr><th>#</th><th>Template</th><th>Word</th><th>Speaker</th><th>Distance</th><th></th></tr>${tm}</table>
  <small>Distances are uncalibrated scores, not probabilities. A similar-looking waveform does not prove the same word.</small></div>
 <div class="card"><h2>4. Signal quality <span class="tag meas">measured</span></h2><table>
  <tr><td>Duration</td><td>${(+qa.duration_s).toFixed(3)} s</td><td>SNR estimate</td><td>${qa.snr_db_estimate} dB</td></tr>
  <tr><td>Speech level</td><td>${qa.speech_level_dbfs} dBFS</td><td>Noise floor</td><td>${qa.noise_floor_dbfs} dBFS</td></tr>
  <tr><td>Clipping</td><td>${qa.clipped_run_samples} samples</td><td>QA verdict</td><td>${esc(qa.verdict)} (${esc(qa.flags)})</td></tr></table></div>
 <div class="card"><h2>5. Vocabulary verification status</h2><div class="sub">${esc(ver.note)}</div>${here}${vw}
  <div class="sub" style="margin-top:8px">Pending until two independent human reviewers approve. For the gemination pair, reviewers must state whether the difference is duration only or also voicing.</div></div>`;
 wire();draw();}
function wire(){const {rec}=CUR;
 $('pall').onclick=()=>play(0,rec.duration);$('psel').onclick=()=>sel?play(sel[0],sel[1]):0;$('stop').onclick=()=>{audio.pause();playEnd=null};
 document.querySelectorAll('[data-p]').forEach(b=>b.onclick=()=>{const s=rec.segments[+b.dataset.p];play(s.start,s.end)});
 document.querySelectorAll('[data-t]').forEach(b=>b.onclick=()=>{const a=new Audio('/audio/'+b.dataset.t);a.play()});
 $('zoom').oninput=e=>{zoom=+e.target.value;draw()};$('scr').oninput=e=>{scroll=+e.target.value/100;draw()};
 const w=$('wave');let drag=null;const t0=x=>{const r=w.getBoundingClientRect(),v=view();return v[0]+(x-r.left)/r.width*(v[1]-v[0])};
 w.onmousedown=e=>{drag=t0(e.clientX);sel=[drag,drag];draw()};w.onmousemove=e=>{if(drag!==null){const t=t0(e.clientX);sel=[Math.min(drag,t),Math.max(drag,t)];draw()}};
 window.onmouseup=()=>{drag=null};}
function view(){const d=CUR.rec.duration,span=d/zoom,s=scroll*(d-span);return[s,s+span]}
function play(a,b){audio.currentTime=a;playEnd=b;audio.play();tick()}
function tick(){if(!audio.paused&&playEnd!==null&&audio.currentTime>=playEnd){audio.pause();playEnd=null}draw();if(!audio.paused)requestAnimationFrame(tick)}
function sized(c){const r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.width=r.width*d;c.height=(+c.getAttribute('height'))*d;const g=c.getContext('2d');g.scale(d,d);return[g,r.width,+c.getAttribute('height')]}
function drawPeaks(c,p,t0,t1,dur,col){const [g,W,H]=sized(c);g.clearRect(0,0,W,H);g.strokeStyle=col||'#4285d8';const n=p.length;
 for(let x=0;x<W;x++){const t=t0+(x/W)*(t1-t0),i=Math.min(n-1,Math.max(0,Math.floor(t/dur*n)));const[lo,hi]=p[i];g.beginPath();g.moveTo(x,H/2-hi*H/2);g.lineTo(x,H/2-lo*H/2);g.stroke()}return[g,W,H]}
function draw(){if(!CUR)return;const {rec}=CUR,[t0,t1]=view(),X=t=>(t-t0)/(t1-t0);
 let[g,W,H]=drawPeaks($('wave'),rec.peaks,t0,t1,rec.duration);
 rec.segments.forEach((s,i)=>{g.fillStyle=COL[i%5]+'33';g.fillRect(X(s.start)*W,0,(X(s.end)-X(s.start))*W,H);g.fillStyle=COL[i%5];g.fillRect(X(s.start)*W,0,1,H)});
 if(sel){g.fillStyle='#00000030';g.fillRect(X(sel[0])*W,0,(X(sel[1])-X(sel[0]))*W,H);$('selinfo').textContent=`selection ${sel[0].toFixed(3)} – ${sel[1].toFixed(3)} s`}
 g.strokeStyle='#c0392b';const cx=X(audio.currentTime)*W;g.beginPath();g.moveTo(cx,0);g.lineTo(cx,H);g.stroke();
 const sp=rec.spec;[g,W,H]=sized($('spec'));if(sp){const lo=Math.min(...sp.data.map(r=>Math.min(...r))),hi=Math.max(...sp.data.map(r=>Math.max(...r)));
  for(let x=0;x<W;x++){const t=t0+(x/W)*(t1-t0),f=Math.min(sp.rows-1,Math.max(0,Math.floor(t/sp.hop)));for(let m=0;m<sp.cols;m++){const v=(sp.data[f][m]-lo)/(hi-lo||1);
   g.fillStyle=`hsl(${240-240*v},80%,${15+45*v}%)`;g.fillRect(x,H-(m+1)*H/sp.cols,1.5,H/sp.cols+1)}}
  g.fillStyle='#fff';g.font='11px sans-serif';g.fillText('log-mel (from the matcher\'s own feature code), low → high frequency',6,12)}
 [g,W,H]=sized($('segbar'));rec.segments.forEach((s,i)=>{g.fillStyle=COL[i%5];const a=Math.max(0,X(s.start)*W),b=Math.min(W,X(s.end)*W);if(b>a){g.fillRect(a,0,b-a-1,H);g.fillStyle='#fff';g.font='bold 12px sans-serif';g.fillText('S'+(i+1),a+6,19)}});
 const f=rec.f0;[g,W,H]=sized($('f0c'));g.fillStyle='#8f98aa';g.font='11px sans-serif';
 if(!f){g.fillText(rec.f0_note||'F0 not available (numpy not installed)',6,14)}else{const vals=f.filter(v=>v);g.fillText(vals.length?'F0 estimate (autocorrelation, 60–450 Hz)':'No voiced F0 found in 60–450 Hz (expected for the synthetic tones)',6,14);
  g.fillStyle='#e8a43c';f.forEach((v,i)=>{if(v){const t=i*0.01+0.02;g.fillRect(X(t)*W,H-(v-60)/390*(H-18),2,2)}})}
 document.querySelectorAll('canvas.mini').forEach(c=>{const s=rec.segments[+c.dataset.i];drawPeaks(c,rec.peaks,s.start,s.end,rec.duration,COL[+c.dataset.i%5])})}
addEventListener('resize',draw);init();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--review", help="review workbook (read-only status panel)")
    ap.add_argument("--build", default=os.environ.get("S2W_BUILD", "build"))
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    app = App(a.dataset, a.run, a.build, a.review)
    srv = make_server(app, a.port)
    print("Sound2Word Acoustic Viewer (read-only) at http://127.0.0.1:%d  - Ctrl+C to stop" % srv.server_address[1])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
