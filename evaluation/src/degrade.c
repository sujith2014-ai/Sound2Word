#include "s2w_degrade.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_resample.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static uint32_t xs(uint32_t *s) { *s ^= *s << 13; *s ^= *s >> 17; *s ^= *s << 5; return *s; }
static double urand(uint32_t *s) { return (double)(xs(s) >> 8) / 16777216.0; }
static double gauss(uint32_t *s) { double a = 0; for (int i = 0; i < 12; i++) a += urand(s); return a - 6.0; }

static int ok(const s2w_audio *a) { return a && a->samples && a->n_samples > 0 && a->sample_rate >= 1000; }

double s2w_deg_active_power(const s2w_audio *a) {
    if (!ok(a)) return 0.0;
    size_t fl = (size_t)a->sample_rate * 20 / 1000;
    if (fl == 0) fl = 1;
    size_t nf = a->n_samples / fl;
    if (nf == 0) {
        double e = 0;
        for (size_t i = 0; i < a->n_samples; i++) e += (double)a->samples[i] * a->samples[i];
        return e / (double)a->n_samples;
    }
    double *p = malloc(nf * sizeof *p), mx = 0;
    if (!p) return 0.0;
    for (size_t f = 0; f < nf; f++) {
        double e = 0;
        for (size_t k = 0; k < fl; k++) { double v = a->samples[f * fl + k]; e += v * v; }
        p[f] = e / (double)fl;
        if (p[f] > mx) mx = p[f];
    }
    double thr = mx * pow(10.0, -2.5), sum = 0;
    size_t cnt = 0;
    for (size_t f = 0; f < nf; f++) if (p[f] >= thr && p[f] > 0) { sum += p[f]; cnt++; }
    free(p);
    return cnt ? sum / (double)cnt : 0.0;
}

s2w_status s2w_deg_gain_db(s2w_audio *a, double db) {
    if (!ok(a) || !isfinite(db)) return S2W_ERR_BAD_PARAMS;
    double g = pow(10.0, db / 20.0);
    for (size_t i = 0; i < a->n_samples; i++) a->samples[i] = (float)(a->samples[i] * g);
    return S2W_OK;
}

s2w_status s2w_deg_clip(s2w_audio *a, double level) {
    if (!ok(a) || !(level > 0 && level <= 1)) return S2W_ERR_BAD_PARAMS;
    for (size_t i = 0; i < a->n_samples; i++) {
        if (a->samples[i] > level) a->samples[i] = (float)level;
        else if (a->samples[i] < -level) a->samples[i] = (float)-level;
    }
    return S2W_OK;
}

s2w_status s2w_deg_dc(s2w_audio *a, double offset) {
    if (!ok(a) || !isfinite(offset)) return S2W_ERR_BAD_PARAMS;
    for (size_t i = 0; i < a->n_samples; i++) a->samples[i] = (float)(a->samples[i] + offset);
    return S2W_OK;
}

s2w_status s2w_deg_add_noise(s2w_audio *a, s2w_noise_kind kind, double snr_db, uint32_t seed, double *achieved) {
    if (!ok(a) || !isfinite(snr_db) || kind < S2W_NOISE_WHITE || kind > S2W_NOISE_HUM) return S2W_ERR_BAD_PARAMS;
    double ps = s2w_deg_active_power(a);
    if (ps <= 0) return S2W_ERR_EMPTY;                                  /* silence has no signal level to set an SNR against */
    size_t n = a->n_samples;
    double *nz = malloc(n * sizeof *nz);
    if (!nz) return S2W_ERR_NOMEM;
    uint32_t s = seed ? seed : 1u;
    if (kind == S2W_NOISE_WHITE) {
        for (size_t i = 0; i < n; i++) nz[i] = gauss(&s);
    } else if (kind == S2W_NOISE_PINK) {          /* Paul Kellet's economy pink filter */
        double b0 = 0, b1 = 0, b2 = 0;
        for (size_t i = 0; i < n; i++) {
            double w = gauss(&s);
            b0 = 0.99765 * b0 + w * 0.0990460; b1 = 0.96300 * b1 + w * 0.2965164; b2 = 0.57000 * b2 + w * 1.0526913;
            nz[i] = b0 + b1 + b2 + w * 0.1848;
        }
    } else {                                      /* mains hum: 50 Hz + harmonics, random phases */
        static const double amp[4] = {1.0, 0.5, 0.3, 0.2};
        double ph[4];
        for (int h = 0; h < 4; h++) ph[h] = 2.0 * M_PI * urand(&s);
        for (size_t i = 0; i < n; i++) {
            double v = 0, t = (double)i / (double)a->sample_rate;
            for (int h = 0; h < 4; h++) v += amp[h] * sin(2.0 * M_PI * 50.0 * (h + 1) * t + ph[h]);
            nz[i] = v;
        }
    }
    double pn = 0;
    for (size_t i = 0; i < n; i++) pn += nz[i] * nz[i];
    pn /= (double)n;
    if (pn <= 0) { free(nz); return S2W_ERR_EMPTY; }
    double scale = sqrt(ps / pow(10.0, snr_db / 10.0) / pn), pn_actual = 0;
    for (size_t i = 0; i < n; i++) {
        double v = nz[i] * scale;
        pn_actual += v * v;
        a->samples[i] = (float)(a->samples[i] + v);
    }
    pn_actual /= (double)n;
    if (achieved) *achieved = 10.0 * log10(ps / pn_actual);
    free(nz);
    return S2W_OK;
}

s2w_status s2w_deg_dropout(s2w_audio *a, double start_s, double len_ms) {
    if (!ok(a) || !(start_s >= 0) || !(len_ms > 0) || !isfinite(start_s) || !isfinite(len_ms)) return S2W_ERR_BAD_PARAMS;
    size_t lo = (size_t)(start_s * a->sample_rate), n = (size_t)(len_ms * a->sample_rate / 1000.0);
    if (lo >= a->n_samples) return S2W_ERR_BAD_PARAMS;
    for (size_t i = lo; i < lo + n && i < a->n_samples; i++) a->samples[i] = 0.0f;
    return S2W_OK;
}

s2w_status s2w_deg_truncate_head(s2w_audio *a, double ms) {
    if (!ok(a) || !(ms >= 0) || !isfinite(ms)) return S2W_ERR_BAD_PARAMS;
    size_t cut = (size_t)(ms * a->sample_rate / 1000.0);
    size_t min_keep = (size_t)a->sample_rate / 50;
    if (cut + min_keep > a->n_samples) return S2W_ERR_TOO_SHORT;
    memmove(a->samples, a->samples + cut, (a->n_samples - cut) * sizeof(float));
    a->n_samples -= cut;
    return S2W_OK;
}

typedef struct { double b0, b1, b2, a1, a2, x1, x2, y1, y2; } biquad;

static void biquad_design(biquad *q, int highpass, double fc, double Q, double fs) {
    double w0 = 2.0 * M_PI * fc / fs, c = cos(w0), s = sin(w0), al = s / (2.0 * Q), a0 = 1.0 + al;
    memset(q, 0, sizeof *q);
    if (highpass) { q->b0 = (1 + c) / 2; q->b1 = -(1 + c); q->b2 = (1 + c) / 2; }
    else { q->b0 = (1 - c) / 2; q->b1 = 1 - c; q->b2 = (1 - c) / 2; }
    q->b0 /= a0; q->b1 /= a0; q->b2 /= a0;
    q->a1 = -2.0 * c / a0; q->a2 = (1.0 - al) / a0;
}
static void biquad_run(biquad *q, float *x, size_t n) {
    for (size_t i = 0; i < n; i++) {
        double in = x[i], y = q->b0 * in + q->b1 * q->x1 + q->b2 * q->x2 - q->a1 * q->y1 - q->a2 * q->y2;
        q->x2 = q->x1; q->x1 = in; q->y2 = q->y1; q->y1 = y;
        x[i] = (float)y;
    }
}

s2w_status s2w_deg_bandlimit(s2w_audio *a, double lo_hz, double hi_hz) {
    if (!ok(a) || !(lo_hz > 0) || !(hi_hz > lo_hz) || hi_hz >= a->sample_rate / 2.0) return S2W_ERR_BAD_PARAMS;
    static const double Q4[2] = {0.5411961, 1.3065630};      /* 4th-order Butterworth section Qs */
    for (int k = 0; k < 2; k++) {
        biquad h, l;
        biquad_design(&h, 1, lo_hz, Q4[k], a->sample_rate);
        biquad_design(&l, 0, hi_hz, Q4[k], a->sample_rate);
        biquad_run(&h, a->samples, a->n_samples);
        biquad_run(&l, a->samples, a->n_samples);
    }
    return S2W_OK;
}

s2w_status s2w_deg_reverb(s2w_audio *a, double rt60_s, double drr_db, uint32_t seed) {
    if (!ok(a) || !(rt60_s > 0 && rt60_s <= 2.0) || !isfinite(drr_db)) return S2W_ERR_BAD_PARAMS;
    size_t L = (size_t)(rt60_s * a->sample_rate);
    if (L < 2) return S2W_ERR_BAD_PARAMS;
    double *h = malloc(L * sizeof *h), e = 0;
    float *out = malloc(a->n_samples * sizeof *out);
    if (!h || !out) { free(h); free(out); return S2W_ERR_NOMEM; }
    uint32_t s = seed ? seed : 1u;
    for (size_t k = 0; k < L; k++) {
        double env = exp(-6.907755 * (double)k / (double)L);      /* -60 dB at rt60 */
        h[k] = gauss(&s) * env;
        e += h[k] * h[k];
    }
    double norm = e > 0 ? 1.0 / sqrt(e) : 0.0, wet = pow(10.0, -drr_db / 20.0);
    for (size_t i = 0; i < a->n_samples; i++) {
        double acc = 0;
        size_t kmax = i + 1 < L ? i + 1 : L;
        for (size_t k = 0; k < kmax; k++) acc += (double)a->samples[i - k] * h[k];
        out[i] = (float)((double)a->samples[i] + wet * norm * acc);
    }
    memcpy(a->samples, out, a->n_samples * sizeof *out);
    free(h); free(out);
    return S2W_OK;
}

s2w_status s2w_deg_speed(s2w_audio *a, double factor) {
    if (!ok(a) || !(factor >= 0.25 && factor <= 4.0)) return S2W_ERR_BAD_PARAMS;
    int rate = a->sample_rate;
    s2w_audio in = *a, out;
    in.sample_rate = (int)lround((double)rate * factor);
    s2w_status st = s2w_audio_resample(&in, rate, &out);
    if (st != S2W_OK) return st;
    free(a->samples);
    a->samples = out.samples; a->n_samples = out.n_samples; a->sample_rate = rate;
    return S2W_OK;
}

s2w_status s2w_deg_rate_roundtrip(s2w_audio *a, int rate) {
    if (!ok(a) || rate < 1000 || rate > 384000) return S2W_ERR_BAD_PARAMS;
    int orig = a->sample_rate;
    s2w_audio lo, back;
    s2w_status st = s2w_audio_resample(a, rate, &lo);
    if (st != S2W_OK) return st;
    st = s2w_audio_resample(&lo, orig, &back);
    s2w_audio_free(&lo);
    if (st != S2W_OK) return st;
    free(a->samples);
    a->samples = back.samples; a->n_samples = back.n_samples;
    return S2W_OK;
}

/* ---- named scenarios ------------------------------------------------------------------------- */
const s2w_scenario_info S2W_SCENARIOS[] = {
    {"identity", "control: no change (pipeline must reproduce the clean result)"},
    {"gain_m20", "level -20 dB (quiet speaker / distant mic level only)"},
    {"gain_m40", "level -40 dB (very quiet)"},
    {"gain_p12_clip", "level +12 dB then hard clip at full scale (overdriven input)"},
    {"snr20_white", "white noise at 20 dB SNR"},
    {"snr10_white", "white noise at 10 dB SNR"},
    {"snr0_white", "white noise at 0 dB SNR"},
    {"snr10_pink", "pink noise at 10 dB SNR"},
    {"hum_snr10", "50 Hz mains hum + harmonics at 10 dB SNR"},
    {"telephone", "band limit 300-3400 Hz"},
    {"far_mic", "synthetic reverberant tail (rt60 0.3 s, 0 dB direct-to-reverb), -18 dB level, pink noise 15 dB SNR"},
    {"speed_085", "played 0.85x speed: slower tempo AND lower pitch together (not a speaker model)"},
    {"speed_115", "played 1.15x speed: faster tempo AND higher pitch together (not a speaker model)"},
    {"rate_8k", "round trip through 8 kHz sampling (4 kHz bandwidth)"},
    {"rate_22k", "round trip through 22.05 kHz sampling"},
    {"dropout_mid", "20 ms of exact zeros in the middle of the file"},
    {"head_cut_240ms", "first 240 ms removed (can cut the word onset)"},
    {"dc_offset", "DC offset +0.05"},
};
const size_t S2W_N_SCENARIOS = sizeof S2W_SCENARIOS / sizeof *S2W_SCENARIOS;

s2w_status s2w_scenario_apply(const char *name, s2w_audio *a, uint32_t seed, int *simulated) {
    if (!name || !ok(a)) return S2W_ERR_BAD_PARAMS;
    if (simulated) *simulated = 1;
    if (!strcmp(name, "identity")) { if (simulated) *simulated = 0; return S2W_OK; }
    if (!strcmp(name, "gain_m20")) return s2w_deg_gain_db(a, -20);
    if (!strcmp(name, "gain_m40")) return s2w_deg_gain_db(a, -40);
    if (!strcmp(name, "gain_p12_clip")) { s2w_status s = s2w_deg_gain_db(a, 12); return s != S2W_OK ? s : s2w_deg_clip(a, 1.0); }
    if (!strcmp(name, "snr20_white")) return s2w_deg_add_noise(a, S2W_NOISE_WHITE, 20, seed, NULL);
    if (!strcmp(name, "snr10_white")) return s2w_deg_add_noise(a, S2W_NOISE_WHITE, 10, seed, NULL);
    if (!strcmp(name, "snr0_white")) return s2w_deg_add_noise(a, S2W_NOISE_WHITE, 0, seed, NULL);
    if (!strcmp(name, "snr10_pink")) return s2w_deg_add_noise(a, S2W_NOISE_PINK, 10, seed, NULL);
    if (!strcmp(name, "hum_snr10")) return s2w_deg_add_noise(a, S2W_NOISE_HUM, 10, seed, NULL);
    if (!strcmp(name, "telephone")) return s2w_deg_bandlimit(a, 300, 3400);
    if (!strcmp(name, "far_mic")) {
        s2w_status s = s2w_deg_reverb(a, 0.3, 0.0, seed);
        if (s == S2W_OK) s = s2w_deg_gain_db(a, -18);
        return s == S2W_OK ? s2w_deg_add_noise(a, S2W_NOISE_PINK, 15, seed + 1, NULL) : s;
    }
    if (!strcmp(name, "speed_085")) return s2w_deg_speed(a, 0.85);
    if (!strcmp(name, "speed_115")) return s2w_deg_speed(a, 1.15);
    if (!strcmp(name, "rate_8k")) return s2w_deg_rate_roundtrip(a, 8000);
    if (!strcmp(name, "rate_22k")) return s2w_deg_rate_roundtrip(a, 22050);
    if (!strcmp(name, "dropout_mid")) return s2w_deg_dropout(a, 0.5 * (double)a->n_samples / a->sample_rate, 20.0);
    if (!strcmp(name, "head_cut_240ms")) return s2w_deg_truncate_head(a, 240.0);
    if (!strcmp(name, "dc_offset")) return s2w_deg_dc(a, 0.05);
    return S2W_ERR_BAD_PARAMS;
}
