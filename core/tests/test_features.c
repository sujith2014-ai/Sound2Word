#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_features.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static s2w_audio mk(size_t n, int rate) {
    s2w_audio a = {0};
    a.samples = calloc(n ? n : 1, sizeof(float));
    a.n_samples = n; a.sample_rate = rate; a.source_channels = 1; a.source_bits = 16;
    return a;
}
static s2w_audio tone(double hz, double amp, double secs) {
    s2w_audio a = mk((size_t)(16000 * secs), 16000);
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] = (float)(amp * sin(2 * M_PI * hz * i / 16000));
    return a;
}
static int finite_all(const float *p, size_t n) {
    for (size_t i = 0; i < n; i++) if (!isfinite(p[i])) return 0;
    return 1;
}
static int argmax_band(const s2w_features *f, size_t row) {
    int b = 0;
    for (size_t j = 1; j < f->n_mels; j++)
        if (f->logmel[row * f->n_mels + j] > f->logmel[row * f->n_mels + b]) b = (int)j;
    return b;
}
static s2w_features extract(const s2w_audio *a, const s2w_feat_config *c, s2w_status *st) {
    s2w_features f;
    *st = s2w_features_extract(a, c, &f);
    return f;
}

static void test_config(void) {
    s2w_feat_config d; s2w_feat_config_default(&d);
    CHECK(s2w_feat_config_validate(&d) == S2W_OK);
    CHECK(d.frame_ms == 25 && d.hop_ms == 10 && d.sample_rate == 16000);
    CHECK(s2w_feat_config_validate(NULL) == S2W_ERR_BAD_PARAMS);
    s2w_feat_config c;
#define BAD(field, val) c = d; c.field = (val); CHECK(s2w_feat_config_validate(&c) == S2W_ERR_BAD_PARAMS)
    BAD(sample_rate, 7999); BAD(sample_rate, 48001);
    BAD(frame_ms, 9); BAD(frame_ms, 101);
    BAD(hop_ms, 4); BAD(hop_ms, 26);
    BAD(fft_size, 500);  /* not a power of two */
    BAD(fft_size, 256);  /* smaller than 400-sample frame */
    BAD(fft_size, 8192); BAD(fft_size, 0);
    BAD(n_mels, 7); BAD(n_mels, 129);
    BAD(n_mfcc, 0); BAD(n_mfcc, 41); BAD(n_mfcc, 14 + 40);
    BAD(f_min_hz, -1.0); BAD(f_min_hz, NAN); BAD(f_max_hz, 8001.0); BAD(f_max_hz, 10.0);
    BAD(preemph, -0.1); BAD(preemph, 1.0); BAD(preemph, NAN);
    BAD(norm, (s2w_norm_mode)7);
    c = d; c.n_mels = 8; c.n_mfcc = 9; CHECK(s2w_feat_config_validate(&c) == S2W_ERR_BAD_PARAMS);
    c = d; c.n_mels = 8; c.n_mfcc = 8; CHECK(s2w_feat_config_validate(&c) == S2W_OK);
    c = d; c.fft_size = 4096; c.n_mels = 128; CHECK(s2w_feat_config_validate(&c) == S2W_OK);

    /* Valid but unusable: Mel bands narrower than an FFT bin are refused at extract time. */
    s2w_audio a = tone(440, 0.3, 0.5); s2w_status st; s2w_features f;
    c = d; c.fft_size = 512; c.n_mels = 128; c.f_min_hz = 0; 
    f = extract(&a, &c, &st);
    CHECK(st == S2W_ERR_BAD_PARAMS && f.logmel == NULL);
    s2w_features_free(&f);
    s2w_audio_free(&a);
}

static void test_inputs(void) {
    s2w_feat_config c; s2w_feat_config_default(&c);
    s2w_status st; s2w_features f;

    f = extract(NULL, &c, &st); CHECK(st == S2W_ERR_BAD_PARAMS); s2w_features_free(&f);
    s2w_audio a = tone(440, 0.3, 0.5);
    st = s2w_features_extract(&a, NULL, &f); CHECK(st == S2W_ERR_BAD_PARAMS); s2w_features_free(&f);
    CHECK(s2w_features_extract(&a, &c, NULL) == S2W_ERR_BAD_PARAMS);

    a.sample_rate = 8000;
    f = extract(&a, &c, &st); CHECK(st == S2W_ERR_RATE_MISMATCH); s2w_features_free(&f);
    a.sample_rate = 16000;

    a.samples[100] = NAN;
    f = extract(&a, &c, &st); CHECK(st == S2W_ERR_NONFINITE && f.mfcc == NULL); s2w_features_free(&f);
    a.samples[100] = INFINITY;
    f = extract(&a, &c, &st); CHECK(st == S2W_ERR_NONFINITE); s2w_features_free(&f);
    s2w_audio_free(&a);

    /* Frame-count boundaries: frame = 400, hop = 160 samples. */
    CHECK(s2w_feat_num_frames(&c, 399) == 0);
    CHECK(s2w_feat_num_frames(&c, 400) == 1);
    CHECK(s2w_feat_num_frames(&c, 559) == 1);
    CHECK(s2w_feat_num_frames(&c, 560) == 2);
    for (size_t n = 0; n <= 560; n += 1) { /* short audio: error below one frame, ok from 400 */
        a = mk(n, 16000);
        for (size_t i = 0; i < n; i++) a.samples[i] = 0.1f * sinf(0.05f * (float)i);
        f = extract(&a, &c, &st);
        if (n < 400) CHECK(st == S2W_ERR_TOO_SHORT);
        else CHECK(st == S2W_OK && f.n_frames == (n >= 560 ? 2u : 1u));
        s2w_features_free(&f);
        s2w_audio_free(&a);
    }
}

static void test_signals(void) {
    s2w_feat_config c; s2w_feat_config_default(&c);
    s2w_status st; s2w_features f;

    /* Silence: finite, constant, equals the log floor in the Mel domain. */
    s2w_audio a = mk(16000, 16000);
    f = extract(&a, &c, &st);
    CHECK(st == S2W_OK && f.n_frames == 98);
    CHECK(finite_all(f.logmel, f.n_frames * f.n_mels) && finite_all(f.mfcc, f.n_frames * f.n_mfcc));
    CHECK(fabs(f.logmel[0] - log(S2W_LOG_FLOOR)) < 1e-4 && f.logmel[0] == f.logmel[f.n_frames * f.n_mels - 1]);
    CHECK(f.quality.warnings & S2W_WARN_MOSTLY_SILENT);
    s2w_features_free(&f);
    c.norm = S2W_NORM_MEANVAR; /* constant input must not produce NaN/Inf */
    f = extract(&a, &c, &st);
    CHECK(st == S2W_OK && finite_all(f.logmel_norm, f.n_frames * f.n_mels) && finite_all(f.mfcc_norm, f.n_frames * f.n_mfcc));
    s2w_features_free(&f); s2w_audio_free(&a);
    s2w_feat_config_default(&c);

    /* Tones land in the Mel band whose centre is nearest, and order is monotone. */
    int prev = -1;
    double freqs[] = {300, 700, 1000, 2000, 4000};
    for (int t = 0; t < 5; t++) {
        a = tone(freqs[t], 0.5, 0.5);
        f = extract(&a, &c, &st);
        CHECK(st == S2W_OK);
        int b = argmax_band(&f, f.n_frames / 2);
        double fc = s2w_feat_mel_center_hz(&c, b);
        CHECK(fabs(fc - freqs[t]) < 0.25 * freqs[t] + 60);
        CHECK(b > prev); prev = b;
        s2w_features_free(&f); s2w_audio_free(&a);
    }

    /* Impulse: only frames containing it carry energy; others sit at the floor. */
    a = mk(8000, 16000);
    a.samples[4000] = 1.0f;
    c.preemph = 0.0;
    f = extract(&a, &c, &st);
    CHECK(st == S2W_OK);
    int hot = 0;
    for (size_t r = 0; r < f.n_frames; r++) {
        int is_hot = f.logmel[r * f.n_mels] > log(S2W_LOG_FLOOR) + 1.0;
        size_t start = r * 160;
        CHECK(is_hot == (start <= 4000 && 4000 < start + 400 && fabs(0.5 - 0.5 * cos(2 * M_PI * (4000 - start) / 400.0)) > 1e-9));
        hot += is_hot;
    }
    CHECK(hot >= 2);
    s2w_features_free(&f); s2w_audio_free(&a);
    s2w_feat_config_default(&c);

    /* Amplitude scaling shifts log-Mel by ln(g^2) uniformly (no clipping/floor involved). */
    s2w_audio q = tone(1000, 0.4, 0.3), l = tone(1000, 0.2, 0.3);
    s2w_features fq = extract(&q, &c, &st), fl = extract(&l, &c, &st);
    double d = fq.logmel[fq.n_mels * 5 + 10] - fl.logmel[fl.n_mels * 5 + 10];
    CHECK(fabs(d - log(4.0)) < 1e-3);
    s2w_features_free(&fq); s2w_features_free(&fl); s2w_audio_free(&q); s2w_audio_free(&l);
}

static void test_normalisation(void) {
    s2w_feat_config c; s2w_feat_config_default(&c);
    s2w_status st;
    /* Two syllable-like bursts: short vowel (0.1 s) vs long vowel (0.3 s), each padded. */
    s2w_audio sh = mk(16000, 16000), lg = mk(16000, 16000);
    for (size_t i = 0; i < 1600; i++) sh.samples[4000 + i] = (float)(0.4 * sin(2 * M_PI * 600.0 * i / 16000));
    for (size_t i = 0; i < 4800; i++) lg.samples[4000 + i] = (float)(0.4 * sin(2 * M_PI * 600.0 * i / 16000));

    s2w_feat_config none = c, mean = c, mv = c;
    none.norm = S2W_NORM_NONE; mean.norm = S2W_NORM_MEAN; mv.norm = S2W_NORM_MEANVAR;
    s2w_features a0 = extract(&sh, &none, &st), a1 = extract(&sh, &mean, &st), a2 = extract(&sh, &mv, &st);
    s2w_features b1 = extract(&lg, &mean, &st);
    size_t n = a0.n_frames * a0.n_mels;

    /* Raw is identical regardless of mode; NONE copies raw into norm. */
    CHECK(memcmp(a0.logmel, a1.logmel, n * sizeof(float)) == 0 && memcmp(a0.logmel, a2.logmel, n * sizeof(float)) == 0);
    CHECK(memcmp(a0.mfcc, a2.mfcc, a0.n_frames * a0.n_mfcc * sizeof(float)) == 0);
    CHECK(memcmp(a0.logmel, a0.logmel_norm, n * sizeof(float)) == 0);

    /* Time axis untouched: frame counts identical for every mode. */
    CHECK(a0.n_frames == a1.n_frames && a1.n_frames == a2.n_frames && a1.n_frames == b1.n_frames);

    /* MEAN: column mean ~ 0; MEANVAR: also std ~ 1 on non-constant columns. */
    for (size_t j = 0; j < a1.n_mfcc; j++) {
        double m = 0, m2 = 0, v2 = 0;
        for (size_t r = 0; r < a1.n_frames; r++) m += a1.mfcc_norm[r * a1.n_mfcc + j];
        CHECK(fabs(m / a1.n_frames) < 1e-4);
        for (size_t r = 0; r < a2.n_frames; r++) m2 += a2.mfcc_norm[r * a2.n_mfcc + j];
        m2 /= a2.n_frames;
        for (size_t r = 0; r < a2.n_frames; r++) { double x = a2.mfcc_norm[r * a2.n_mfcc + j] - m2; v2 += x * x; }
        CHECK(fabs(sqrt(v2 / a2.n_frames) - 1.0) < 1e-3);
    }

    /* Duration cue survives normalisation: long vowel keeps ~3x as many active frames. */
    int bd = argmax_band(&b1, 30);
    double top = b1.logmel[30 * b1.n_mels + bd] - 5.0;
    int act_s = 0, act_l = 0;
    for (size_t r = 0; r < a1.n_frames; r++) {
        act_s += a0.logmel[r * a0.n_mels + bd] > top;
        act_l += b1.logmel[r * b1.n_mels + bd] > top;
    }
    CHECK(act_s > 0 && act_l >= 2 * act_s);
    int act_l_norm = 0, act_s_norm = 0;
    for (size_t r = 0; r < a1.n_frames; r++) {
        act_s_norm += a1.logmel_norm[r * a1.n_mels + bd] > 0.0;
        act_l_norm += b1.logmel_norm[r * b1.n_mels + bd] > 0.0;
    }
    CHECK(act_l_norm >= 2 * act_s_norm && act_s_norm > 0);

    s2w_features_free(&a0); s2w_features_free(&a1); s2w_features_free(&a2); s2w_features_free(&b1);
    s2w_audio_free(&sh); s2w_audio_free(&lg);
}

static void test_metadata_and_output(void) {
    s2w_feat_config c; s2w_feat_config_default(&c);
    c.n_mels = 32; c.n_mfcc = 20;
    s2w_status st;
    s2w_audio a = tone(500, 2.0, 0.3); /* clipped on purpose */
    for (size_t i = 0; i < a.n_samples; i++) if (a.samples[i] > 1) a.samples[i] = 1; else if (a.samples[i] < -1) a.samples[i] = -1;
    s2w_features f = extract(&a, &c, &st);
    CHECK(st == S2W_OK);
    CHECK(f.format_version == S2W_FEAT_FORMAT_VERSION && strcmp(f.lib_version, S2W_FEAT_LIB_VERSION) == 0);
    CHECK(f.config.n_mels == 32 && f.config.n_mfcc == 20 && f.config.f_max_hz == 8000.0);
    CHECK(f.n_mels == 32 && f.n_mfcc == 20 && f.source_samples == a.n_samples);
    CHECK(f.quality.warnings & S2W_WARN_CLIPPING && f.quality.clipped_samples > 0);

    /* Deterministic: two extractions and two serialisations are byte-identical. */
    s2w_features g = extract(&a, &c, &st);
    CHECK(memcmp(f.logmel, g.logmel, f.n_frames * f.n_mels * sizeof(float)) == 0);
    CHECK(memcmp(f.mfcc_norm, g.mfcc_norm, f.n_frames * f.n_mfcc * sizeof(float)) == 0);
    FILE *x = tmpfile(), *y = tmpfile();
    CHECK(x && y);
    CHECK(s2w_features_write_text(x, &f, S2W_OUT_ALL) == S2W_OK && s2w_features_write_text(y, &g, S2W_OUT_ALL) == S2W_OK);
    long lx = ftell(x), ly = ftell(y);
    CHECK(lx == ly && lx > 0);
    char *bx = malloc((size_t)lx + 1), *by = malloc((size_t)ly + 1);
    rewind(x); rewind(y);
    CHECK(fread(bx, 1, (size_t)lx, x) == (size_t)lx && fread(by, 1, (size_t)ly, y) == (size_t)ly);
    CHECK(memcmp(bx, by, (size_t)lx) == 0);
    bx[lx] = 0;
    CHECK(strncmp(bx, "s2w-features v1\n", 16) == 0);
    CHECK(strstr(bx, "quality_warnings=") && strstr(bx, "matrix logmel_raw ") && strstr(bx, "matrix mfcc_norm "));
    free(bx); free(by); fclose(x); fclose(y);

    CHECK(s2w_features_write_text(NULL, &f, 1) == S2W_ERR_BAD_PARAMS);
    s2w_features z; memset(&z, 0, sizeof z);
    CHECK(s2w_features_write_text(stdout, &z, 1) == S2W_ERR_BAD_PARAMS);
    s2w_features_free(&z); s2w_features_free(&z); /* double free is a no-op */
    s2w_features_free(&f); s2w_features_free(&g); s2w_audio_free(&a);
}

static void test_alt_configs(void) {
    /* Non-default sample rates and sizes still work. */
    s2w_feat_config c; s2w_feat_config_default(&c);
    c.sample_rate = 8000; c.fft_size = 256; c.n_mels = 20; c.n_mfcc = 10; c.frame_ms = 30; c.hop_ms = 15;
    s2w_audio a = mk(8000, 8000);
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] = (float)(0.3 * sin(2 * M_PI * 500 * i / 8000));
    s2w_status st; s2w_features f = extract(&a, &c, &st);
    CHECK(st == S2W_OK && f.n_frames == 1 + (8000 - 240) / 120);
    CHECK(finite_all(f.logmel, f.n_frames * f.n_mels));
    s2w_features_free(&f); s2w_audio_free(&a);
}

/* ---- opt-in acoustic-compensation techniques ------------------------------------------------ */
static uint64_t fnv64(uint64_t h, const void *d, size_t n) { const unsigned char *p = d; for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ULL; } return h; }

static void test_baseline_unchanged(void) {
    /* Golden fingerprint of the default-config features for a fixed signal. The same value was produced by a fresh build of the
     * baseline-v0.3.0 source tree (commit de08d46), so the new options cannot silently change default behaviour. */
    s2w_audio a = {0};
    a.n_samples = 16000; a.sample_rate = 16000; a.samples = calloc(a.n_samples, sizeof(float));
    unsigned sd = 12345;
    for (size_t i = 0; i < a.n_samples; i++) {
        double t = (double)i / 16000.0, env = (i > 3000 && i < 12000) ? 1.0 : 0.0;
        sd = sd * 1664525u + 1013904223u;
        a.samples[i] = (float)(env * (0.3 * sin(2 * M_PI * 600 * t) + 0.2 * sin(2 * M_PI * 1700 * t + 1.0) + 0.1 * sin(2 * M_PI * 2900 * t)) + 0.001 * (((sd >> 8) / 16777216.0) - 0.5));
    }
    s2w_feat_config c; s2w_feat_config_default(&c);
    CHECK(c.denoise == S2W_DENOISE_NONE && c.warp_alpha == 1.0);
    s2w_status st; s2w_features f = extract(&a, &c, &st);
    CHECK(st == S2W_OK && f.n_frames == 98);
    uint64_t h = 14695981039346656037ULL;
    h = fnv64(h, f.logmel, f.n_frames * f.n_mels * sizeof(float));
    h = fnv64(h, f.mfcc, f.n_frames * f.n_mfcc * sizeof(float));
    h = fnv64(h, f.logmel_norm, f.n_frames * f.n_mels * sizeof(float));
    h = fnv64(h, f.mfcc_norm, f.n_frames * f.n_mfcc * sizeof(float));
    CHECK(h == 0x45d19694078a4d1fULL);
    /* explicitly setting the options to their "off" values changes nothing */
    s2w_feat_config c2 = c; c2.denoise = S2W_DENOISE_NONE; c2.warp_alpha = 1.0; c2.denoise_oversub = 3.0; c2.denoise_floor = 0.3;
    s2w_features g = extract(&a, &c2, &st);
    CHECK(st == S2W_OK && memcmp(f.mfcc, g.mfcc, f.n_frames * f.n_mfcc * sizeof(float)) == 0 && memcmp(f.logmel, g.logmel, f.n_frames * f.n_mels * sizeof(float)) == 0);
    /* default text output does not mention the new options */
    FILE *x = tmpfile();
    s2w_features_write_text(x, &f, S2W_OUT_MFCC_RAW);
    long l = ftell(x); char *b = malloc((size_t)l + 1); rewind(x); CHECK(fread(b, 1, (size_t)l, x) == (size_t)l); b[l] = 0;
    CHECK(strstr(b, "denoise") == NULL && strstr(b, "warp_alpha") == NULL);
    free(b); fclose(x);
    s2w_features_free(&f); s2w_features_free(&g); s2w_audio_free(&a);
}

static double mean_frame_dist(const float *a, const float *b, size_t rows, size_t cols, size_t lo, size_t hi) {
    double acc = 0;
    for (size_t r = lo; r < hi; r++) {
        double s = 0;
        for (size_t j = 0; j < cols; j++) { double d = (double)a[r * cols + j] - b[r * cols + j]; s += d * d; }
        acc += sqrt(s);
    }
    (void)rows;
    return acc / (double)(hi - lo);
}

static void test_warp(void) {
    s2w_feat_config c; s2w_feat_config_default(&c);
    CHECK(s2w_feat_warp_hz(&c, 1234.5) == 1234.5);                              /* alpha = 1 is the exact identity */
    for (double alpha = 0.7; alpha <= 1.4001; alpha += 0.05) {                  /* continuous, monotonic, endpoint-preserving */
        c.warp_alpha = alpha;
        double prev = -1;
        for (double f = 0; f <= 8000.0; f += 10.0) {
            double w = s2w_feat_warp_hz(&c, f);
            CHECK(w >= prev - 1e-9);
            prev = w;
        }
        CHECK(fabs(s2w_feat_warp_hz(&c, 8000.0) - 8000.0) < 1e-9 && s2w_feat_warp_hz(&c, 0.0) == 0.0);
        CHECK(fabs(s2w_feat_warp_hz(&c, 500.0) - alpha * 500.0) < 1e-9);          /* plain scaling in the low band */
        double fb = 0.8 * 8000.0 * (alpha > 1.0 ? 1.0 / alpha : 1.0);
        CHECK(fabs(s2w_feat_warp_hz(&c, fb - 1e-6) - s2w_feat_warp_hz(&c, fb + 1e-6)) < 1e-3);   /* continuous at the breakpoint */
    }
    /* validation */
    s2w_feat_config d; s2w_feat_config_default(&d);
    d.warp_alpha = 0.69; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.warp_alpha = 1.41; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.warp_alpha = NAN; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.warp_alpha = 0.7; CHECK(s2w_feat_config_validate(&d) == S2W_OK);

    /* Mechanism: a tone 10% higher in frequency (a "shorter vocal tract" analogue) looks closer to the reference tone once warped by 1/1.1 */
    s2w_feat_config n; s2w_feat_config_default(&n); n.norm = S2W_NORM_NONE;
    s2w_audio ref = tone(1000, 0.3, 0.6), hi = tone(1100, 0.3, 0.6);
    s2w_status st;
    s2w_features fr = extract(&ref, &n, &st), fh = extract(&hi, &n, &st);
    s2w_feat_config w = n; w.warp_alpha = 1.0 / 1.1;
    s2w_features fw = extract(&hi, &w, &st);
    CHECK(st == S2W_OK);
    double before = mean_frame_dist(fr.logmel, fh.logmel, fr.n_frames, fr.n_mels, 5, fr.n_frames - 5);
    double after = mean_frame_dist(fr.logmel, fw.logmel, fr.n_frames, fr.n_mels, 5, fr.n_frames - 5);
    CHECK(after < 0.6 * before);
    /* the wrong direction makes it worse, so the effect is the warp and not an accident */
    w.warp_alpha = 1.1;
    s2w_features fx = extract(&hi, &w, &st);
    CHECK(mean_frame_dist(fr.logmel, fx.logmel, fr.n_frames, fr.n_mels, 5, fr.n_frames - 5) > before);
    /* duration is untouched by warping: same frame count */
    CHECK(fw.n_frames == fh.n_frames && fx.n_frames == fh.n_frames);
    /* text output records the non-default setting */
    FILE *xf = tmpfile();
    s2w_features_write_text(xf, &fw, S2W_OUT_MFCC_RAW);
    long l = ftell(xf); char *b = malloc((size_t)l + 1); rewind(xf); CHECK(fread(b, 1, (size_t)l, xf) == (size_t)l); b[l] = 0;
    CHECK(strstr(b, "warp_alpha=") != NULL);
    free(b); fclose(xf);
    s2w_features_free(&fr); s2w_features_free(&fh); s2w_features_free(&fw); s2w_features_free(&fx); s2w_audio_free(&ref); s2w_audio_free(&hi);
}

static void test_denoise(void) {
    s2w_feat_config d; s2w_feat_config_default(&d);
    d.denoise = (s2w_denoise_mode)7; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.denoise = S2W_DENOISE_SPECSUB; d.denoise_oversub = 0.9; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.denoise_oversub = 4.1; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.denoise_oversub = 1.5; d.denoise_floor = -0.1; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.denoise_floor = 0.51; CHECK(s2w_feat_config_validate(&d) == S2W_ERR_BAD_PARAMS);
    d.denoise_floor = 0.05; CHECK(s2w_feat_config_validate(&d) == S2W_OK);

    /* tone bursts in a silent file, then the same file with stationary white noise (about 10 dB SNR) */
    s2w_audio clean = mk(16000, 16000);
    for (size_t i = 5000; i < 11000; i++) clean.samples[i] = (float)(0.3 * sin(2 * M_PI * 900.0 * (double)i / 16000.0));
    s2w_audio noisy = mk(16000, 16000);
    unsigned sd = 99;
    for (size_t i = 0; i < noisy.n_samples; i++) {
        double u = 0; for (int k = 0; k < 12; k++) { sd = sd * 1664525u + 1013904223u; u += (sd >> 8) / 16777216.0; }
        noisy.samples[i] = clean.samples[i] + (float)(0.1 * (u - 6.0));
    }
    for (size_t i = 0; i < clean.n_samples; i++) clean.samples[i] += 1e-5f * (float)sin(0.77 * (double)i);   /* avoid exact digital silence in the reference */
    s2w_feat_config off; s2w_feat_config_default(&off); off.norm = S2W_NORM_NONE;
    s2w_feat_config on = off; on.denoise = S2W_DENOISE_SPECSUB;
    s2w_status st;
    s2w_features rc = extract(&clean, &off, &st), rn = extract(&noisy, &off, &st), dn = extract(&noisy, &on, &st);
    CHECK(st == S2W_OK && dn.n_frames == rn.n_frames && finite_all(dn.logmel, dn.n_frames * dn.n_mels) && finite_all(dn.mfcc, dn.n_frames * dn.n_mfcc));
    /* What spectral subtraction can and cannot do: it lowers the noise floor by at most ~13 dB (floor 0.05), so noisy features never
     * become the clean ones; it should (a) push noise-only frames down and (b) raise the speech-vs-noise contrast in the speech band. */
    size_t speech_row = 5500 / 160 + 2, noise_row = 2000 / 160;
    size_t band = 0;
    for (size_t j = 1; j < rc.n_mels; j++) if (rc.logmel[speech_row * rc.n_mels + j] > rc.logmel[speech_row * rc.n_mels + band]) band = j;   /* tone's band in the clean reference */
    double floor_raw = 0, floor_dn = 0;
    for (size_t r = 2; r < 25; r++)
        for (size_t j = 0; j < dn.n_mels; j++) { floor_raw += rn.logmel[r * rn.n_mels + j]; floor_dn += dn.logmel[r * dn.n_mels + j]; }
    floor_raw /= 23.0 * (double)dn.n_mels; floor_dn /= 23.0 * (double)dn.n_mels;
    CHECK(floor_dn < floor_raw - 1.0);                                                               /* noise floor lowered by > 1 nat on average */
    double contrast_raw = rn.logmel[speech_row * rn.n_mels + band] - rn.logmel[noise_row * rn.n_mels + band];
    double contrast_dn = dn.logmel[speech_row * dn.n_mels + band] - dn.logmel[noise_row * dn.n_mels + band];
    CHECK(contrast_dn > contrast_raw + 1.0);                                                         /* tone stands out more against the noise floor */
    CHECK(contrast_dn > 0 && contrast_raw > 0);
    /* duration untouched; pure silence and a deterministic rerun are safe */
    s2w_audio z = mk(16000, 16000);
    s2w_features fz = extract(&z, &on, &st);
    CHECK(st == S2W_OK && finite_all(fz.logmel, fz.n_frames * fz.n_mels));
    s2w_features dn2 = extract(&noisy, &on, &st);
    CHECK(memcmp(dn.logmel, dn2.logmel, dn.n_frames * dn.n_mels * sizeof(float)) == 0);
    FILE *xf = tmpfile();
    s2w_features_write_text(xf, &dn, S2W_OUT_LOGMEL_RAW);
    long l = ftell(xf); char *b = malloc((size_t)l + 1); rewind(xf); CHECK(fread(b, 1, (size_t)l, xf) == (size_t)l); b[l] = 0;
    CHECK(strstr(b, "denoise=specsub") != NULL);
    free(b); fclose(xf);
    s2w_features_free(&rc); s2w_features_free(&rn); s2w_features_free(&dn); s2w_features_free(&dn2); s2w_features_free(&fz);
    s2w_audio_free(&clean); s2w_audio_free(&noisy); s2w_audio_free(&z);
}

int main(void) {
    test_config(); test_inputs(); test_signals(); test_normalisation();
    test_metadata_and_output(); test_alt_configs(); test_baseline_unchanged(); test_warp(); test_denoise();
    puts(failures ? "FAILED" : "features: all tests passed");
    return failures ? 1 : 0;
}
