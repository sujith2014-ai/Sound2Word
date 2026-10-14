"""Glue sound-unit clips into one word: trim each clip to its real edges, match the levels, equal-power crossfade at every join, one fade
at the end, and MEASURE every join (level, spectrum and pitch jump) so a bad join is a number, not an impression.

Pure numpy + the standard library (16-bit mono WAV). It cannot match pitch (that needs a vocoder); it reports the pitch jump instead.

    audio, edges = join([x1, x2, x3], rate)                 # x = float arrays in -1..1
    report = measure_joins(audio, edges, rate)               # [{rms_db, spectral, f0_jump_hz, ok}, ...]
"""
import io
import wave

import numpy as np

# xfade: audio crossfade (capped at a quarter of the shorter clip, so a short unit is never swallowed). smooth_ms: the window over which the
# FEATURES of a template are smoothed at each join; Kahn et al. (1984) found 60-90 ms best for demisyllable joins, and little loss without it.
# iso_min_s / iso_scale: duration rule for units spoken ALONE (Kahn: about half their isolated length in non-final position).
DEFAULTS = {"trim_db": -40.0, "pad_ms": 6.0, "xfade_ms": 30.0, "end_fade_ms": 12.0, "max_gain": 4.0, "smooth_ms": 60.0, "iso_min_s": 0.2, "iso_scale": 0.5, "min_keep_s": 0.1}
# a join is flagged when the level jumps by more than this many dB, or the spectral distance exceeds this (see measure_joins)
OK_RMS_DB = 6.0
OK_SPECTRAL = 2.5
OK_F0_HZ = 40.0


def read_wav(path_or_bytes):
    """-> (rate, float32 array in -1..1). 16-bit mono only (anything else is refused, never guessed)."""
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    with wave.open(src, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("clip is not 16-bit mono")
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return rate, pcm


def write_wav_bytes(x, rate):
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def resample(x, rate, to_rate):
    if rate == to_rate or len(x) < 2:
        return x
    n = max(2, int(round(len(x) * to_rate / float(rate))))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def trim(x, rate, trim_db=None, pad_ms=None):
    """Cut leading and trailing silence: keep from the first to the last 5 ms frame whose RMS is within `trim_db` of the loudest frame."""
    trim_db = DEFAULTS["trim_db"] if trim_db is None else trim_db
    pad = int(rate * (DEFAULTS["pad_ms"] if pad_ms is None else pad_ms) / 1000.0)
    hop = max(1, int(rate * 0.005))
    n = len(x) // hop
    if n == 0:
        return x
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    peak = rms.max()
    if peak <= 0:
        return x[:0]
    live = np.where(rms >= peak * 10 ** (trim_db / 20.0))[0]
    a = max(0, live[0] * hop - pad)
    b = min(len(x), (live[-1] + 1) * hop + pad)
    return x[a:b]


def voiced_rms(x):
    """RMS of the loud part of a clip (frames above half the peak RMS), so a quiet tail does not set the gain."""
    if len(x) == 0:
        return 0.0
    hop = max(1, len(x) // 40)
    n = len(x) // hop
    r = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) if n else np.array([np.sqrt((x ** 2).mean())])
    keep = r[r >= 0.5 * r.max()]
    return float(np.sqrt((keep ** 2).mean())) if len(keep) else float(r.max())


def match_levels(clips, max_gain=None):
    """Scale every clip towards the median voiced RMS of the set (gain limited, so noise is not blown up)."""
    mg = DEFAULTS["max_gain"] if max_gain is None else max_gain
    lv = [voiced_rms(c) for c in clips]
    live = [v for v in lv if v > 0]
    if not live:
        return clips
    target = float(np.median(live))
    return [c * min(mg, max(1.0 / mg, target / v)) if v > 0 else c for c, v in zip(clips, lv)]


def shorten(x, rate, scale=None, min_keep_s=None, xfade_ms=10.0):
    """Duration rule for a unit spoken alone: cut the sustained middle so that about `scale` of its length remains (never below `min_keep_s`),
    keeping the onset and the offset and crossfading the cut. Resampling is NOT used: it would move the formants."""
    scale = DEFAULTS["iso_scale"] if scale is None else scale
    keep = max(int(rate * (DEFAULTS["min_keep_s"] if min_keep_s is None else min_keep_s)), int(len(x) * scale))
    if keep >= len(x) - 8:
        return x
    k = int(rate * xfade_ms / 1000.0)
    keep = min(len(x) - 1, keep + k)                          # the crossfade overlaps k samples, so add them back: the result is `keep` long
    head = int(keep * 0.45)
    tail = keep - head
    k = min(k, head // 2, tail // 2)
    a, b = x[:head], x[len(x) - tail:]
    if k <= 0:
        return np.concatenate([a, b]).astype(np.float32)
    t = (np.arange(k) + 0.5) / k
    mix = a[-k:] * np.cos(t * np.pi / 2) + b[:k] * np.sin(t * np.pi / 2)
    return np.concatenate([a[:-k], mix, b[k:]]).astype(np.float32)


def join(clips, rate, xfade_ms=None, end_fade_ms=None, trim_db=None, do_trim=True, do_levels=True, isolated=None):
    """-> (audio float32, edges in seconds). edges[0] = 0, edges[i] = the middle of the crossfade between unit i-1 and i, edges[-1] = end.
    `isolated` = a list of booleans (one per clip): clips spoken alone longer than DEFAULTS["iso_min_s"] are shortened (see shorten) unless they are
    the LAST unit of the word; clips cut out of real words are left alone."""
    xf_ms = DEFAULTS["xfade_ms"] if xfade_ms is None else xfade_ms
    ef_ms = DEFAULTS["end_fade_ms"] if end_fade_ms is None else end_fade_ms
    parts = [trim(c, rate, trim_db) if do_trim else c for c in clips]
    if isolated is not None:
        last = max([i for i, p in enumerate(parts) if len(p)] or [0])
        parts = [shorten(p, rate) if (isolated[i] and i != last and len(p) > rate * DEFAULTS["iso_min_s"]) else p for i, p in enumerate(parts)]
    parts = [p for p in parts if len(p)]
    if not parts:
        return np.zeros(0, dtype=np.float32), [0.0]
    if do_levels:
        parts = match_levels(parts)
    out = parts[0].astype(np.float32)
    edges = [0.0]
    prev_len = len(parts[0])
    for p in parts[1:]:
        k = min(int(rate * xf_ms / 1000.0), min(prev_len, len(p)) // 4)          # never more than a quarter of the shorter unit
        if k > 0:
            t = (np.arange(k) + 0.5) / k
            fo, fi = np.cos(t * np.pi / 2), np.sin(t * np.pi / 2)            # equal power
            mix = out[-k:] * fo + p[:k] * fi
            edges.append((len(out) - k / 2.0) / rate)
            out = np.concatenate([out[:-k], mix, p[k:]])
        else:
            edges.append(len(out) / float(rate))
            out = np.concatenate([out, p])
        prev_len = len(p)
    f = min(len(out) // 2, int(rate * ef_ms / 1000.0))
    if f:
        out[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)
    edges.append(len(out) / float(rate))
    return out.astype(np.float32), [round(e, 4) for e in edges]


def f0(x, rate, lo=70.0, hi=400.0):
    """Autocorrelation pitch of one short window, or None when it is not clearly voiced."""
    if len(x) < int(rate / lo) * 2:
        return None
    x = x - x.mean()
    if float(np.sqrt((x ** 2).mean())) < 1e-4:
        return None
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    a, b = int(rate / hi), int(rate / lo)
    if b >= len(ac) or ac[0] <= 0:
        return None
    lag = a + int(np.argmax(ac[a:b]))
    return rate / float(lag) if ac[lag] / ac[0] > 0.3 else None


def _band_logs(x, rate, bands=8):
    if len(x) < 16:
        return np.zeros(bands)
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    edges = np.unique(np.geomspace(1, len(s) - 1, bands + 1).astype(int))
    v = np.array([np.log10(s[a:b].sum() + 1e-9) for a, b in zip(edges[:-1], edges[1:])])
    return np.pad(v, (0, bands - len(v)), mode="edge") if len(v) < bands else v


def measure_joins(audio, edges, rate, win_ms=25.0):
    """One dict per inner join: the level step in dB, the spectral distance (mean abs difference of 8 log-power bands / 10), the pitch step in Hz
    (None when either side is unvoiced) and ok = all three within the limits at the top of this file."""
    w = int(rate * win_ms / 1000.0)
    out = []
    for e in edges[1:-1]:
        c = int(round(e * rate))
        a, b = audio[max(0, c - w):c], audio[c:c + w]
        if len(a) < 8 or len(b) < 8:
            out.append({"at": e, "rms_db": None, "spectral": None, "f0_jump_hz": None, "ok": False})
            continue
        ra, rb = float(np.sqrt((a ** 2).mean())) + 1e-9, float(np.sqrt((b ** 2).mean())) + 1e-9
        db = 20.0 * np.log10(rb / ra)
        sp = float(np.abs(_band_logs(a, rate) - _band_logs(b, rate)).mean())
        fa, fb = f0(a, rate), f0(b, rate)
        jump = abs(fa - fb) if fa and fb else None
        out.append({"at": e, "rms_db": round(float(db), 2), "spectral": round(sp, 3), "f0_jump_hz": None if jump is None else round(float(jump), 1),
                    "ok": abs(db) <= OK_RMS_DB and sp <= OK_SPECTRAL and (jump is None or jump <= OK_F0_HZ)})
    return out


def smooth_features(feat, edges, rate_hz=50.0, win_ms=None):
    """Smooth a template's feature frames (rows, one per 20 ms) around each inner join with a triangular window of `win_ms` (default 60 ms).
    Frames far from a join are untouched."""
    win_ms = DEFAULTS["smooth_ms"] if win_ms is None else win_ms
    half = max(1, int(round(win_ms / 1000.0 * rate_hz / 2)))
    out = feat.copy()
    for e in edges[1:-1]:
        c = int(round(e * rate_hz))
        for i in range(max(0, c - half), min(len(feat), c + half + 1)):
            w = 1.0 - abs(i - c) / float(half + 1)
            lo, hi = max(0, i - half), min(len(feat), i + half + 1)
            out[i] = (1 - w) * feat[i] + w * feat[lo:hi].mean(axis=0)
    return out
