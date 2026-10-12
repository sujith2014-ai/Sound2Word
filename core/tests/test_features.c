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

int main(void) {
    test_config(); test_inputs(); test_signals(); test_normalisation();
    test_metadata_and_output(); test_alt_configs();
    puts(failures ? "FAILED" : "features: all tests passed");
    return failures ? 1 : 0;
}
