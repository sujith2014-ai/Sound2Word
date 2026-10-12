#include "s2w_resample.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define LOBES 16
#define CUTOFF_GUARD 0.97
#define MAX_OUT_SAMPLES ((size_t)600 * 384000)

s2w_status s2w_audio_resample(const s2w_audio *in, int target_rate, s2w_audio *out) {
    if (!out) return S2W_ERR_BAD_PARAMS;
    memset(out, 0, sizeof *out);
    if (!in || !in->samples || in->n_samples == 0 || in->sample_rate < 1000 || in->sample_rate > 384000 ||
        target_rate < 1000 || target_rate > 384000)
        return S2W_ERR_BAD_PARAMS;
    size_t n_out = (size_t)((double)in->n_samples * (double)target_rate / (double)in->sample_rate);
    if (n_out == 0) return S2W_ERR_EMPTY;
    if (n_out > MAX_OUT_SAMPLES) return S2W_ERR_TOO_LARGE;
    float *o = malloc(n_out * sizeof *o);
    if (!o) return S2W_ERR_NOMEM;
    if (target_rate == in->sample_rate) {
        memcpy(o, in->samples, n_out * sizeof *o);
    } else {
        double ratio = (double)in->sample_rate / (double)target_rate;      /* input samples per output sample */
        double low = (target_rate < in->sample_rate ? (double)target_rate / (double)in->sample_rate : 1.0) * CUTOFF_GUARD;
        double fc = 0.5 * low;                                             /* cycles per input sample */
        double half = (double)LOBES / (2.0 * fc);                          /* kernel half-width in input samples */
        for (size_t i = 0; i < n_out; i++) {
            double t = (double)i * ratio;
            long j0 = (long)ceil(t - half), j1 = (long)floor(t + half);
            double acc = 0, wsum = 0;
            for (long j = j0; j <= j1; j++) {
                double d = t - (double)j, a = 2.0 * fc * d;
                double sinc = fabs(a) < 1e-12 ? 1.0 : sin(M_PI * a) / (M_PI * a);
                double u = d / half;                                       /* -1..1 */
                double w = 0.42 + 0.5 * cos(M_PI * u) + 0.08 * cos(2.0 * M_PI * u);
                double h = sinc * w;
                wsum += h;
                if (j >= 0 && (size_t)j < in->n_samples) acc += h * (double)in->samples[j];
            }
            o[i] = (float)(wsum != 0.0 ? acc / wsum : 0.0);
        }
    }
    out->samples = o;
    out->n_samples = n_out;
    out->sample_rate = target_rate;
    out->source_channels = in->source_channels;
    out->source_bits = in->source_bits;
    return S2W_OK;
}

static void le16(uint8_t *p, unsigned v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void le32(uint8_t *p, uint32_t v) { le16(p, v & 0xFFFF); le16(p + 2, v >> 16); }

s2w_status s2w_wav_encode_pcm16(const float *x, size_t n, int rate, uint8_t **buf, size_t *len) {
    if (!x || !buf || !len || n == 0 || rate < 1000 || rate > 384000 || n > (size_t)0x7FFFFFFF / 2 - 64) return S2W_ERR_BAD_PARAMS;
    size_t dl = n * 2;
    uint8_t *b = malloc(44 + dl);
    if (!b) return S2W_ERR_NOMEM;
    memcpy(b, "RIFF", 4); le32(b + 4, (uint32_t)(36 + dl)); memcpy(b + 8, "WAVEfmt ", 8);
    le32(b + 16, 16); le16(b + 20, 1); le16(b + 22, 1); le32(b + 24, (uint32_t)rate);
    le32(b + 28, (uint32_t)rate * 2); le16(b + 32, 2); le16(b + 34, 16);
    memcpy(b + 36, "data", 4); le32(b + 40, (uint32_t)dl);
    for (size_t i = 0; i < n; i++) {
        double v = isfinite(x[i]) ? (double)x[i] * 32768.0 : 0.0;
        long q = lround(v);
        if (q > 32767) q = 32767;
        if (q < -32768) q = -32768;
        le16(b + 44 + 2 * i, (unsigned)(q & 0xFFFF));
    }
    *buf = b; *len = 44 + dl;
    return S2W_OK;
}

s2w_status s2w_wav_write_pcm16(const char *path, const float *x, size_t n, int rate) {
    uint8_t *b; size_t l;
    s2w_status st = s2w_wav_encode_pcm16(x, n, rate, &b, &l);
    if (st != S2W_OK) return st;
    FILE *f = fopen(path, "wb");
    if (!f) { free(b); return S2W_ERR_IO; }
    size_t w = fwrite(b, 1, l, f);
    int bad = fclose(f);
    free(b);
    return (w == l && !bad) ? S2W_OK : S2W_ERR_IO;
}
