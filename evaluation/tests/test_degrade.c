#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_degrade.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static s2w_audio mk(size_t n, int rate) {
    s2w_audio a = {0};
    a.samples = calloc(n ? n : 1, sizeof(float)); a.n_samples = n; a.sample_rate = rate; a.source_channels = 1; a.source_bits = 16;
    return a;
}
static s2w_audio tone(double hz, double amp, double secs, int rate) {
    s2w_audio a = mk((size_t)(secs * rate), rate);
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] = (float)(amp * sin(2 * M_PI * hz * (double)i / rate));
    return a;
}
/* tone burst in the middle of silence */
static s2w_audio burst(void) {
    s2w_audio a = mk(16000, 16000);
    for (size_t i = 6000; i < 10000; i++) a.samples[i] = (float)(0.5 * sin(2 * M_PI * 800.0 * (double)i / 16000.0));
    return a;
}
static double amp_at(const s2w_audio *a, double hz) {
    size_t lo = a->n_samples / 4, hi = a->n_samples * 3 / 4;
    double c = 0, s = 0;
    for (size_t i = lo; i < hi; i++) { double ph = 2 * M_PI * hz * (double)i / a->sample_rate; c += a->samples[i] * cos(ph); s += a->samples[i] * sin(ph); }
    return 2.0 * sqrt(c * c + s * s) / (double)(hi - lo);
}
static double db(double r) { return 20 * log10(r); }
static s2w_audio clone(const s2w_audio *a) {
    s2w_audio c = mk(a->n_samples, a->sample_rate);
    memcpy(c.samples, a->samples, a->n_samples * sizeof(float));
    return c;
}

static void test_basic(void) {
    s2w_audio a = burst();
    CHECK(fabs(s2w_deg_active_power(&a) - 0.125) < 0.02);                     /* 0.5^2/2; two partial edge frames pull it slightly low, silence does not dilute it */
    CHECK(s2w_deg_active_power(&a) > 0.10);
    s2w_audio z = mk(1000, 16000);
    CHECK(s2w_deg_active_power(&z) == 0.0 && s2w_deg_active_power(NULL) == 0.0);
    s2w_audio_free(&z);

    s2w_audio g = clone(&a);
    CHECK(s2w_deg_gain_db(&g, 20 * log10(2.0)) == S2W_OK && fabs(g.samples[8000] / a.samples[8000] - 2.0) < 1e-5);
    CHECK(s2w_deg_gain_db(&g, NAN) == S2W_ERR_BAD_PARAMS && s2w_deg_gain_db(NULL, 1) == S2W_ERR_BAD_PARAMS);
    s2w_deg_gain_db(&g, 20);
    CHECK(s2w_deg_clip(&g, 0.8) == S2W_OK);
    float mx = 0; for (size_t i = 0; i < g.n_samples; i++) if (fabsf(g.samples[i]) > mx) mx = fabsf(g.samples[i]);
    CHECK(mx <= 0.8f + 1e-6f && mx > 0.79f);
    CHECK(s2w_deg_clip(&g, 0.0) == S2W_ERR_BAD_PARAMS && s2w_deg_clip(&g, 1.5) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&g);

    s2w_audio d = clone(&a);
    CHECK(s2w_deg_dc(&d, 0.05) == S2W_OK && fabs(d.samples[0] - 0.05) < 1e-6);
    s2w_audio_free(&d);
    d = clone(&a);
    CHECK(s2w_deg_dropout(&d, 0.4, 20.0) == S2W_OK);
    int zeros = 1;
    for (size_t i = 6400; i < 6400 + 320; i++) zeros &= d.samples[i] == 0.0f;
    CHECK(zeros && d.samples[6400 + 330] != 0.0f);
    CHECK(s2w_deg_dropout(&d, 99.0, 10.0) == S2W_ERR_BAD_PARAMS && s2w_deg_dropout(&d, 0.1, 0.0) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&d);

    d = clone(&a);
    float keep = d.samples[8000];
    CHECK(s2w_deg_truncate_head(&d, 100.0) == S2W_OK && d.n_samples == 16000 - 1600 && d.samples[8000 - 1600] == keep);
    CHECK(s2w_deg_truncate_head(&d, 5000.0) == S2W_ERR_TOO_SHORT && s2w_deg_truncate_head(&d, -1.0) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&d); s2w_audio_free(&a);
}

static void test_noise(void) {
    static const s2w_noise_kind K[3] = {S2W_NOISE_WHITE, S2W_NOISE_PINK, S2W_NOISE_HUM};
    double targets[3] = {20, 10, 0};
    for (int k = 0; k < 3; k++)
        for (int t = 0; t < 3; t++) {
            s2w_audio a = burst(), orig = clone(&a);
            double got = 0;
            CHECK(s2w_deg_add_noise(&a, K[k], targets[t], 7, &got) == S2W_OK);
            CHECK(fabs(got - targets[t]) < 0.01);
            double pn = 0;                                                    /* independent measurement: out - in */
            for (size_t i = 0; i < a.n_samples; i++) { double d = (double)a.samples[i] - orig.samples[i]; pn += d * d; }
            pn /= (double)a.n_samples;
            CHECK(fabs(10 * log10(s2w_deg_active_power(&orig) / pn) - targets[t]) < 0.05);
            s2w_audio_free(&a); s2w_audio_free(&orig);
        }
    s2w_audio a = burst(), b = burst(), c = burst();
    s2w_deg_add_noise(&a, S2W_NOISE_WHITE, 10, 5, NULL); s2w_deg_add_noise(&b, S2W_NOISE_WHITE, 10, 5, NULL); s2w_deg_add_noise(&c, S2W_NOISE_WHITE, 10, 6, NULL);
    CHECK(memcmp(a.samples, b.samples, a.n_samples * sizeof(float)) == 0 && memcmp(a.samples, c.samples, a.n_samples * sizeof(float)) != 0);
    s2w_audio_free(&a); s2w_audio_free(&b); s2w_audio_free(&c);

    /* spectral character: white ~ uncorrelated, pink strongly low-frequency, hum peaks at 50 Hz */
    s2w_audio w = tone(1000, 0.3, 3.0, 16000), p = clone(&w), h = clone(&w);
    s2w_deg_add_noise(&w, S2W_NOISE_WHITE, 0, 3, NULL); s2w_deg_add_noise(&p, S2W_NOISE_PINK, 0, 3, NULL); s2w_deg_add_noise(&h, S2W_NOISE_HUM, 0, 3, NULL);
    s2w_audio nz[3] = {clone(&w), clone(&p), clone(&h)};
    s2w_audio base = tone(1000, 0.3, 3.0, 16000);
    for (int i = 0; i < 3; i++) for (size_t k = 0; k < nz[i].n_samples; k++) nz[i].samples[k] -= base.samples[k];
    double r1[2];
    for (int i = 0; i < 2; i++) {
        double c0 = 0, c1 = 0;
        for (size_t k = 1; k < nz[i].n_samples; k++) { c0 += (double)nz[i].samples[k] * nz[i].samples[k]; c1 += (double)nz[i].samples[k] * nz[i].samples[k - 1]; }
        r1[i] = c1 / c0;
    }
    CHECK(fabs(r1[0]) < 0.05 && r1[1] > 0.5);
    CHECK(amp_at(&nz[2], 50) > 20 * amp_at(&nz[2], 61));
    for (int i = 0; i < 3; i++) s2w_audio_free(&nz[i]);
    s2w_audio_free(&w); s2w_audio_free(&p); s2w_audio_free(&h); s2w_audio_free(&base);

    s2w_audio silence = mk(16000, 16000);
    CHECK(s2w_deg_add_noise(&silence, S2W_NOISE_WHITE, 10, 1, NULL) == S2W_ERR_EMPTY);        /* no level to set an SNR against */
    s2w_audio_free(&silence);
    a = burst();
    CHECK(s2w_deg_add_noise(&a, (s2w_noise_kind)9, 10, 1, NULL) == S2W_ERR_BAD_PARAMS && s2w_deg_add_noise(&a, S2W_NOISE_WHITE, NAN, 1, NULL) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&a);
}

static void test_filters(void) {
    double f[3] = {100, 1000, 6000};
    double amp[3];
    for (int i = 0; i < 3; i++) {
        s2w_audio a = tone(f[i], 0.5, 1.0, 16000);
        CHECK(s2w_deg_bandlimit(&a, 300, 3400) == S2W_OK);
        amp[i] = amp_at(&a, f[i]);
        s2w_audio_free(&a);
    }
    CHECK(fabs(db(amp[1] / 0.5)) < 1.0);                                          /* passband */
    CHECK(db(amp[0] / 0.5) < -15 && db(amp[2] / 0.5) < -20);                       /* both skirts attenuated */
    s2w_audio a = tone(1000, 0.5, 0.5, 16000);
    CHECK(s2w_deg_bandlimit(&a, 300, 9000) == S2W_ERR_BAD_PARAMS && s2w_deg_bandlimit(&a, 3000, 300) == S2W_ERR_BAD_PARAMS && s2w_deg_bandlimit(&a, 0, 300) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&a);

    /* reverb: an impulse keeps its direct part and gains a tail whose energy follows the DRR */
    for (int k = 0; k < 2; k++) {
        double drr = k == 0 ? 0.0 : 10.0;
        s2w_audio imp = mk(16000, 16000);
        imp.samples[0] = 1.0f;
        CHECK(s2w_deg_reverb(&imp, 0.3, drr, 4) == S2W_OK);
        double tail = 0;
        for (size_t i = 1; i < imp.n_samples; i++) tail += (double)imp.samples[i] * imp.samples[i];
        CHECK(fabs(10 * log10(tail) - (-drr)) < 0.5 && fabs(imp.samples[0] - (1.0 + 0)) < 0.2 + pow(10, -drr / 20) * 3);
        s2w_audio_free(&imp);
    }
    s2w_audio i1 = mk(8000, 16000), i2 = mk(8000, 16000);
    i1.samples[0] = i2.samples[0] = 1;
    s2w_deg_reverb(&i1, 0.2, 3, 9); s2w_deg_reverb(&i2, 0.2, 3, 9);
    CHECK(memcmp(i1.samples, i2.samples, 8000 * sizeof(float)) == 0);
    CHECK(s2w_deg_reverb(&i1, 0.0, 3, 1) == S2W_ERR_BAD_PARAMS && s2w_deg_reverb(&i1, 5.0, 3, 1) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&i1); s2w_audio_free(&i2);

    /* speed: length and frequency scale together (tempo AND pitch) */
    a = tone(1000, 0.5, 1.0, 16000);
    CHECK(s2w_deg_speed(&a, 2.0) == S2W_OK && a.n_samples == 8000 && a.sample_rate == 16000);
    CHECK(amp_at(&a, 2000) > 0.45 && amp_at(&a, 1000) < 0.02);
    s2w_audio_free(&a);
    a = tone(1000, 0.5, 1.0, 16000);
    CHECK(s2w_deg_speed(&a, 0.5) == S2W_OK && a.n_samples == 32000 && amp_at(&a, 500) > 0.45);
    CHECK(s2w_deg_speed(&a, 0.1) == S2W_ERR_BAD_PARAMS && s2w_deg_speed(&a, 9.0) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&a);

    /* rate round trip keeps length, removes content above the low rate's Nyquist */
    a = tone(1000, 0.5, 1.0, 16000);
    s2w_audio hi = tone(6000, 0.5, 1.0, 16000);
    CHECK(s2w_deg_rate_roundtrip(&a, 8000) == S2W_OK && s2w_deg_rate_roundtrip(&hi, 8000) == S2W_OK);
    CHECK(a.n_samples == 16000 && fabs(amp_at(&a, 1000) - 0.5) < 0.01 && db(amp_at(&hi, 6000) / 0.5) < -50);
    CHECK(s2w_deg_rate_roundtrip(&a, 500) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&a); s2w_audio_free(&hi);
}

static void test_scenarios(void) {
    CHECK(S2W_N_SCENARIOS >= 15);
    s2w_audio base = burst();
    for (size_t i = 0; i < 16000; i++) base.samples[i] += (float)(0.001 * sin(0.37 * (double)i));   /* non-silent background */
    for (size_t k = 0; k < S2W_N_SCENARIOS; k++) {
        const char *name = S2W_SCENARIOS[k].name;
        s2w_audio a = clone(&base), b = clone(&base);
        int sim = -1, sim2 = -1;
        CHECK(s2w_scenario_apply(name, &a, 11, &sim) == S2W_OK);
        CHECK(s2w_scenario_apply(name, &b, 11, &sim2) == S2W_OK && sim == sim2);
        CHECK(a.n_samples == b.n_samples && memcmp(a.samples, b.samples, a.n_samples * sizeof(float)) == 0);   /* deterministic */
        int finite = 1;
        for (size_t i = 0; i < a.n_samples; i++) finite &= isfinite(a.samples[i]);
        CHECK(finite && a.sample_rate == 16000 && S2W_SCENARIOS[k].description[0]);
        int same = a.n_samples == base.n_samples && memcmp(a.samples, base.samples, a.n_samples * sizeof(float)) == 0;
        if (!strcmp(name, "identity")) CHECK(sim == 0 && same);
        else CHECK(sim == 1 && !same);
        if (!strcmp(name, "gain_p12_clip")) { float mx = 0; for (size_t i = 0; i < a.n_samples; i++) if (fabsf(a.samples[i]) > mx) mx = fabsf(a.samples[i]); CHECK(mx <= 1.0f); }
        if (!strcmp(name, "head_cut_240ms")) CHECK(a.n_samples == 16000 - 3840);
        s2w_audio_free(&a); s2w_audio_free(&b);
    }
    s2w_audio a = clone(&base);
    CHECK(s2w_scenario_apply("nonsense", &a, 1, NULL) == S2W_ERR_BAD_PARAMS && s2w_scenario_apply(NULL, &a, 1, NULL) == S2W_ERR_BAD_PARAMS);
    s2w_audio b = clone(&base);
    s2w_scenario_apply("snr10_white", &a, 1, NULL); s2w_scenario_apply("snr10_white", &b, 2, NULL);
    CHECK(memcmp(a.samples, b.samples, a.n_samples * sizeof(float)) != 0);                      /* different seed, different noise */
    s2w_audio_free(&a); s2w_audio_free(&b); s2w_audio_free(&base);
}

int main(void) {
    test_basic(); test_noise(); test_filters(); test_scenarios();
    puts(failures ? "FAILED" : "degrade: all tests passed");
    return failures ? 1 : 0;
}
