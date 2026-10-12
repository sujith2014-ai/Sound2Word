#include "s2w_audio.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_FILE_BYTES (512u * 1024u * 1024u)
#define FRAME_MS 20
#define SILENCE_DBFS (-50.0)
#define CLIP_LEVEL 0.9995

const char *s2w_status_str(s2w_status s) {
    switch (s) {
    case S2W_OK: return "ok";
    case S2W_ERR_IO: return "cannot read file";
    case S2W_ERR_NOT_RIFF: return "not a RIFF/WAVE file";
    case S2W_ERR_TRUNCATED: return "file truncated or chunk sizes invalid";
    case S2W_ERR_NO_FMT: return "missing fmt chunk";
    case S2W_ERR_NO_DATA: return "missing data chunk";
    case S2W_ERR_UNSUPPORTED_FORMAT: return "unsupported WAV encoding";
    case S2W_ERR_BAD_PARAMS: return "invalid audio parameters";
    case S2W_ERR_EMPTY: return "no audio samples";
    case S2W_ERR_NOMEM: return "out of memory";
    case S2W_ERR_TOO_LARGE: return "file too large";
    case S2W_ERR_TOO_SHORT: return "audio shorter than one analysis frame";
    case S2W_ERR_RATE_MISMATCH: return "audio sample rate does not match feature config";
    case S2W_ERR_NONFINITE: return "non-finite value encountered";
    }
    return "unknown error";
}

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

void s2w_audio_free(s2w_audio *a) {
    if (!a) return;
    free(a->samples);
    memset(a, 0, sizeof *a);
}

s2w_status s2w_wav_decode(const uint8_t *buf, size_t len, s2w_audio *out) {
    if (!buf || !out) return S2W_ERR_BAD_PARAMS;
    memset(out, 0, sizeof *out);
    if (len < 12 || memcmp(buf, "RIFF", 4) || memcmp(buf + 8, "WAVE", 4))
        return S2W_ERR_NOT_RIFF;

    int have_fmt = 0;
    unsigned fmt_tag = 0, channels = 0, rate = 0, bits = 0;
    const uint8_t *data = NULL;
    size_t data_len = 0;

    size_t pos = 12;
    while (pos + 8 <= len) {
        const uint8_t *ck = buf + pos;
        size_t sz = rd32(ck + 4);
        size_t body = pos + 8;
        if (!memcmp(ck, "data", 4)) {
            /* Streaming writers may leave a bogus size; clamp to file. */
            if (sz > len - body) sz = len - body;
            data = buf + body;
            data_len = sz;
            if (have_fmt) break;
        } else if (!memcmp(ck, "fmt ", 4)) {
            if (sz < 16 || sz > len - body) return S2W_ERR_TRUNCATED;
            fmt_tag = rd16(ck + 8);
            channels = rd16(ck + 10);
            rate = rd32(ck + 12);
            bits = rd16(ck + 22);
            if (fmt_tag == 0xFFFE && sz >= 40) fmt_tag = rd16(ck + 8 + 24); /* extensible subformat */
            have_fmt = 1;
            if (data) break;
        }
        if (sz > len - body) break;
        pos = body + sz + (sz & 1u);
    }
    if (!have_fmt) return S2W_ERR_NO_FMT;
    if (!data) return S2W_ERR_NO_DATA;

    if (channels < 1 || channels > 8 || rate < 1000 || rate > 384000)
        return S2W_ERR_BAD_PARAMS;
    int is_float = (fmt_tag == 3);
    if (fmt_tag != 1 && fmt_tag != 3) return S2W_ERR_UNSUPPORTED_FORMAT;
    if (is_float ? bits != 32 : (bits != 8 && bits != 16 && bits != 24 && bits != 32))
        return S2W_ERR_UNSUPPORTED_FORMAT;

    size_t bps = bits / 8, frame = bps * channels;
    size_t n = data_len / frame;
    if (n == 0) return S2W_ERR_EMPTY;

    float *s = malloc(n * sizeof *s);
    if (!s) return S2W_ERR_NOMEM;
    for (size_t i = 0; i < n; i++) {
        double acc = 0;
        for (unsigned c = 0; c < channels; c++) {
            const uint8_t *p = data + i * frame + c * bps;
            double v;
            if (is_float) {
                uint32_t u = rd32(p);
                float f;
                memcpy(&f, &u, 4);
                v = isfinite(f) ? f : 0.0;
            } else if (bits == 8) {
                v = ((int)p[0] - 128) / 128.0;
            } else if (bits == 16) {
                v = (int16_t)rd16(p) / 32768.0;
            } else if (bits == 24) {
                int32_t x = (int32_t)(((uint32_t)p[0] << 8) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 24));
                v = (x >> 8) / 8388608.0;
            } else {
                v = (int32_t)rd32(p) / 2147483648.0;
            }
            acc += v;
        }
        double m = acc / channels;
        s[i] = (float)(m > 1.0 ? 1.0 : (m < -1.0 ? -1.0 : m));
    }
    out->samples = s;
    out->n_samples = n;
    out->sample_rate = (int)rate;
    out->source_channels = (int)channels;
    out->source_bits = (int)bits;
    return S2W_OK;
}

s2w_status s2w_wav_load(const char *path, s2w_audio *out) {
    if (!path || !out) return S2W_ERR_BAD_PARAMS;
    FILE *f = fopen(path, "rb");
    if (!f) return S2W_ERR_IO;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return S2W_ERR_IO; }
    long sz = ftell(f);
    if (sz < 0 || fseek(f, 0, SEEK_SET) != 0) { fclose(f); return S2W_ERR_IO; }
    if ((unsigned long)sz > MAX_FILE_BYTES) { fclose(f); return S2W_ERR_TOO_LARGE; }
    uint8_t *buf = malloc((size_t)sz ? (size_t)sz : 1);
    if (!buf) { fclose(f); return S2W_ERR_NOMEM; }
    size_t got = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    s2w_status st = got == (size_t)sz ? s2w_wav_decode(buf, got, out) : S2W_ERR_IO;
    free(buf);
    return st;
}

s2w_status s2w_audio_to_16k(const s2w_audio *in, s2w_audio *out) {
    if (!in || !out || !in->samples || in->sample_rate <= 0) return S2W_ERR_BAD_PARAMS;
    memset(out, 0, sizeof *out);
    double ratio = (double)in->sample_rate / S2W_TARGET_RATE;
    size_t n = (size_t)((double)in->n_samples / ratio);
    if (n == 0) return S2W_ERR_EMPTY;
    float *o = malloc(n * sizeof *o);
    if (!o) return S2W_ERR_NOMEM;
    for (size_t i = 0; i < n; i++) {
        double t = i * ratio;
        if (ratio > 1.0) { /* box-average over the source span to limit aliasing */
            size_t a = (size_t)t, b = (size_t)(t + ratio);
            if (b > in->n_samples) b = in->n_samples;
            if (b <= a) b = a + 1;
            double acc = 0;
            for (size_t k = a; k < b && k < in->n_samples; k++) acc += in->samples[k];
            o[i] = (float)(acc / (double)(b - a));
        } else {
            size_t a = (size_t)t;
            double fr = t - (double)a;
            size_t b = a + 1 < in->n_samples ? a + 1 : a;
            o[i] = (float)(in->samples[a] * (1.0 - fr) + in->samples[b] * fr);
        }
    }
    out->samples = o;
    out->n_samples = n;
    out->sample_rate = S2W_TARGET_RATE;
    out->source_channels = in->source_channels;
    out->source_bits = in->source_bits;
    return S2W_OK;
}

s2w_status s2w_audio_stats(const s2w_audio *a, s2w_stats *st) {
    if (!a || !st || !a->samples || a->sample_rate <= 0) return S2W_ERR_BAD_PARAMS;
    if (a->n_samples == 0) return S2W_ERR_EMPTY;
    memset(st, 0, sizeof *st);

    double sum = 0, sumsq = 0, peak = 0;
    size_t clipped = 0;
    for (size_t i = 0; i < a->n_samples; i++) {
        double v = a->samples[i], av = fabs(v);
        sum += v;
        sumsq += v * v;
        if (av > peak) peak = av;
        if (av >= CLIP_LEVEL) clipped++;
    }
    size_t n = a->n_samples;
    st->duration_s = (double)n / a->sample_rate;
    st->peak = peak;
    st->rms = sqrt(sumsq / n);
    st->rms_dbfs = st->rms > 1e-9 ? 20.0 * log10(st->rms) : -180.0;
    st->dc_offset = sum / n;
    st->clipped_samples = clipped;

    size_t fl = (size_t)a->sample_rate * FRAME_MS / 1000;
    if (fl == 0) fl = 1;
    size_t nf = n / fl;
    size_t silent = 0, first = nf, last = 0;
    for (size_t f = 0; f < nf; f++) {
        double e = 0;
        for (size_t k = 0; k < fl; k++) {
            double v = a->samples[f * fl + k];
            e += v * v;
        }
        double db = e > 0 ? 10.0 * log10(e / fl) : -180.0;
        if (db < SILENCE_DBFS) silent++;
        else { if (f < first) first = f; last = f; }
    }
    if (nf > 0) st->silence_ratio = (double)silent / nf;
    double fdur = (double)fl / a->sample_rate;
    if (first < nf) {
        st->leading_silence_s = first * fdur;
        st->trailing_silence_s = (nf - 1 - last) * fdur;
        st->speech_s = (last - first + 1) * fdur;
    } else {
        st->leading_silence_s = st->duration_s;
    }

    unsigned w = 0;
    if (clipped >= 3 || (double)clipped / n > 0.0005) w |= S2W_WARN_CLIPPING;
    if (st->rms_dbfs < -40.0) w |= S2W_WARN_TOO_QUIET;
    if (st->speech_s < 0.15) w |= S2W_WARN_TOO_SHORT;
    if (st->duration_s > 30.0) w |= S2W_WARN_TOO_LONG;
    if (st->silence_ratio > 0.9) w |= S2W_WARN_MOSTLY_SILENT;
    if (fabs(st->dc_offset) > 0.02) w |= S2W_WARN_DC_OFFSET;
    if (a->sample_rate < S2W_TARGET_RATE) w |= S2W_WARN_LOW_RATE;
    st->warnings = w;
    return S2W_OK;
}
