#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_dtw.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

/* ---- raw-matrix DTW ---- */
static float *ramp(size_t n, size_t dim, double a, double b) { /* column 0 climbs a..b */
    float *p = calloc(n * dim, sizeof *p);
    for (size_t i = 0; i < n; i++) p[i * dim] = (float)(a + (b - a) * (n > 1 ? (double)i / (n - 1) : 0));
    return p;
}

static void test_dtw_core(void) {
    s2w_dtw_config c; s2w_dtw_config_default(&c);
    CHECK(s2w_dtw_config_validate(&c) == S2W_OK);
    double d;
    float *a = ramp(50, 3, 0, 5), *b = ramp(50, 3, 0, 5), *s = ramp(70, 3, 0, 5), *o = ramp(50, 3, 20, 25);

    CHECK(s2w_dtw_distance(a, 50, b, 50, 3, &c, &d) == S2W_OK && d == 0.0);
    double d_ab, d_ba, d_as, d_ao;
    s2w_dtw_distance(a, 50, s, 70, 3, &c, &d_ab);
    s2w_dtw_distance(s, 70, a, 50, 3, &c, &d_ba);
    CHECK(fabs(d_ab - d_ba) < 1e-12);                       /* symmetric */
    s2w_dtw_distance(a, 50, o, 50, 3, &c, &d_ao);
    CHECK(d_ab < d_ao);                                       /* stretched same shape beats different shape */
    CHECK(d_ao > 1.0 && isfinite(d_ao));
    d_as = d_ab; CHECK(d_as > 0);                             /* stretching has a (small) cost */

    /* warp penalty increases distance of a time-stretched match, leaves the identical one alone. */
    s2w_dtw_config p = c; p.warp_penalty = 1.0;
    double d_pen, d_id;
    s2w_dtw_distance(a, 50, s, 70, 3, &p, &d_pen);
    s2w_dtw_distance(a, 50, b, 50, 3, &p, &d_id);
    CHECK(d_pen > d_ab && d_id == 0.0);

    /* duration constraint: ratio 50/130 < 0.5 -> infinite, not an error. */
    float *lng = ramp(130, 3, 0, 5);
    CHECK(s2w_dtw_distance(a, 50, lng, 130, 3, &c, &d) == S2W_OK && isinf(d));
    s2w_dtw_config wide = c; wide.min_len_ratio = 0.2; wide.band_ratio = 1.0;
    CHECK(s2w_dtw_distance(a, 50, lng, 130, 3, &wide, &d) == S2W_OK && isfinite(d));

    /* band constraint: a bump at frame 20 (q) vs frame 35 (t) cannot be aligned inside a narrow
       band, so it costs more than with a wide band. */
    float *e1 = calloc(60 * 2, sizeof *e1), *e2 = calloc(60 * 2, sizeof *e2);
    for (int i = 0; i < 60; i++) {
        e1[i * 2] = (float)(3 * exp(-0.5 * (i - 20) * (i - 20) / 9.0));
        e2[i * 2] = (float)(3 * exp(-0.5 * (i - 35) * (i - 35) / 9.0));
    }
    s2w_dtw_config narrow = c; narrow.band_ratio = 0.1; narrow.warp_penalty = 0;
    s2w_dtw_config full = narrow; full.band_ratio = 1.0;
    double dn, df;
    s2w_dtw_distance(e1, 60, e2, 60, 2, &narrow, &dn);
    s2w_dtw_distance(e1, 60, e2, 60, 2, &full, &df);
    CHECK(dn > 2 * df && df > 0);

    /* Single frame vs single frame; 1 x many within ratio bounds is refused by the duration rule. */
    float x = 1, y = 3;
    CHECK(s2w_dtw_distance(&x, 1, &y, 1, 1, &c, &d) == S2W_OK && fabs(d - 2.0) < 1e-12);

    /* Invalid input. */
    CHECK(s2w_dtw_distance(NULL, 1, &y, 1, 1, &c, &d) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_dtw_distance(&x, 0, &y, 1, 1, &c, &d) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_dtw_distance(&x, 1, &y, 1, 0, &c, &d) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_dtw_distance(&x, 1, &y, 1, 1, NULL, &d) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_dtw_distance(&x, S2W_DTW_MAX_FRAMES + 1, &y, 1, 1, &c, &d) == S2W_ERR_BAD_PARAMS);
    float nanv = NAN;
    CHECK(s2w_dtw_distance(&nanv, 1, &y, 1, 1, &c, &d) == S2W_ERR_NONFINITE);
    s2w_dtw_config bad = c;
    bad.band_ratio = 0; CHECK(s2w_dtw_config_validate(&bad) != S2W_OK);
    bad = c; bad.min_len_ratio = 1.5; CHECK(s2w_dtw_config_validate(&bad) != S2W_OK);
    bad = c; bad.max_len_ratio = 0.9; CHECK(s2w_dtw_config_validate(&bad) != S2W_OK);
    bad = c; bad.warp_penalty = -1; CHECK(s2w_dtw_config_validate(&bad) != S2W_OK);
    bad = c; bad.warp_penalty = NAN; CHECK(s2w_dtw_config_validate(&bad) != S2W_OK);

    /* Maximum size still works. */
    float *big1 = ramp(S2W_DTW_MAX_FRAMES, 2, 0, 1), *big2 = ramp(S2W_DTW_MAX_FRAMES, 2, 0, 1);
    CHECK(s2w_dtw_distance(big1, S2W_DTW_MAX_FRAMES, big2, S2W_DTW_MAX_FRAMES, 2, &c, &d) == S2W_OK && d == 0.0);
    free(big1); free(big2); free(a); free(b); free(s); free(o); free(lng); free(e1); free(e2);
}

static void test_calibration(void) {
    double gen[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
    double imp[] = {6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
    s2w_calibration cal;
    CHECK(s2w_calibrate_threshold(gen, 10, imp, 10, 0.0, &cal) == S2W_OK);
    CHECK(cal.far == 0.0 && cal.threshold < 6 && cal.threshold > 5.99);
    CHECK(fabs(cal.frr - 0.5) < 1e-12);
    CHECK(s2w_calibrate_threshold(gen, 10, imp, 10, 0.2, &cal) == S2W_OK && cal.far <= 0.2 && cal.far > 0.0);
    CHECK(s2w_calibrate_threshold(NULL, 10, imp, 10, 0.1, &cal) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_calibrate_threshold(gen, 0, imp, 10, 0.1, &cal) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_calibrate_threshold(gen, 10, imp, 10, 1.0, &cal) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_calibrate_threshold(gen, 10, imp, 10, -0.1, &cal) == S2W_ERR_BAD_PARAMS);
    double nan_imp[] = {1, NAN};
    CHECK(s2w_calibrate_threshold(gen, 10, nan_imp, 2, 0.1, &cal) == S2W_ERR_NONFINITE);
}

/* ---- synthetic "words": sequences of tone segments, with per-segment duration ---- */
static s2w_audio render(const double *freq, const double *secs, int nseg, double stretch, double amp) {
    size_t total = 0;
    for (int s = 0; s < nseg; s++) total += (size_t)(16000 * secs[s] * stretch);
    s2w_audio a = {0};
    a.samples = calloc(total + 8000, sizeof(float)); /* 0.25 s quiet-ish pad on each side below */
    a.n_samples = total + 8000; a.sample_rate = 16000; a.source_channels = 1; a.source_bits = 16;
    size_t pos = 4000;
    for (int s = 0; s < nseg; s++) {
        size_t n = (size_t)(16000 * secs[s] * stretch);
        for (size_t i = 0; i < n; i++)
            a.samples[pos + i] = (float)(amp * sin(2 * M_PI * freq[s] * (double)(pos + i) / 16000.0) +
                                       0.002 * sin(1.3 * (double)(pos + i))); /* tiny floor noise */
        pos += n;
    }
    return a;
}
static s2w_features feats(const s2w_audio *a, const s2w_feat_config *c) {
    s2w_features f;
    CHECK(s2w_features_extract(a, c, &f) == S2W_OK);
    return f;
}
static s2w_features word(const s2w_feat_config *c, double f1, double f2, double t1, double t2, double stretch, double amp) {
    double fr[2] = {f1, f2}, tm[2] = {t1, t2};
    s2w_audio a = render(fr, tm, 2, stretch, amp);
    s2w_features f = feats(&a, c);
    s2w_audio_free(&a);
    return f;
}

static void test_matcher(void) {
    s2w_feat_config fc; s2w_feat_config_default(&fc);
    s2w_matcher *m = s2w_matcher_new(S2W_REP_MFCC, NULL, NULL);
    CHECK(m != NULL && s2w_matcher_size(m) == 0);
    s2w_match_result r;

    s2w_features q = word(&fc, 500, 1000, 0.25, 0.25, 1.0, 0.4);
    CHECK(s2w_matcher_match(m, &q, 3, &r) == S2W_ERR_EMPTY);

    /* Vocabulary: A=(500,1000), B=(1000,500), C=(2000,3000); two templates for A. */
    struct { const char *l; double f1, f2, st, amp; } T[] = {
        {"A", 500, 1000, 1.0, 0.4}, {"B", 1000, 500, 1.0, 0.4}, {"C", 2000, 3000, 1.0, 0.4}, {"A", 500, 1000, 1.2, 0.3},
    };
    for (int i = 0; i < 4; i++) {
        s2w_features t = word(&fc, T[i].f1, T[i].f2, 0.25, 0.25, T[i].st, T[i].amp);
        CHECK(s2w_matcher_add(m, T[i].l, &t) == S2W_OK);
        s2w_features_free(&t);
    }
    CHECK(s2w_matcher_size(m) == 4);

    /* Query = A spoken faster and louder: ranks A first; labels deduplicated; ordered ascending. */
    s2w_features qa = word(&fc, 500, 1000, 0.25, 0.25, 0.85, 0.6);
    CHECK(s2w_matcher_match(m, &qa, 5, &r) == S2W_OK);
    CHECK(r.n_candidates == 3 && strcmp(r.cand[0].label, "A") == 0);
    CHECK(r.cand[0].distance <= r.cand[1].distance && r.cand[1].distance <= r.cand[2].distance);
    CHECK(r.decision == S2W_DECISION_ACCEPT && !r.rejection_active && r.reason == S2W_REJECT_NONE);
    CHECK(s2w_matcher_match(m, &qa, 1, &r) == S2W_OK && r.n_candidates == 1);
    s2w_match_result r2;
    CHECK(s2w_matcher_match(m, &qa, 1, &r2) == S2W_OK && r2.cand[0].distance == r.cand[0].distance); /* deterministic */

    /* Query = B and C are recognised too (order reversal matters: DTW does not reorder). */
    s2w_features qb = word(&fc, 1000, 500, 0.25, 0.25, 1.1, 0.4), qc = word(&fc, 2000, 3000, 0.25, 0.25, 0.9, 0.4);
    CHECK(s2w_matcher_match(m, &qb, 1, &r) == S2W_OK && strcmp(r.cand[0].label, "B") == 0);
    CHECK(s2w_matcher_match(m, &qc, 1, &r) == S2W_OK && strcmp(r.cand[0].label, "C") == 0);

    /* Held-out calibration -> rejection of an unknown word D=(4000,300). */
    s2w_features qd = word(&fc, 4000, 300, 0.25, 0.25, 1.0, 0.4), qd2 = word(&fc, 3500, 250, 0.25, 0.25, 1.1, 0.4);
    double gen[3], imp[2];
    s2w_features* gq[3] = {&qa, &qb, &qc};
    for (int i = 0; i < 3; i++) { s2w_matcher_match(m, gq[i], 1, &r); gen[i] = r.cand[0].distance; }
    s2w_matcher_match(m, &qd, 1, &r); imp[0] = r.cand[0].distance;
    s2w_matcher_match(m, &qd2, 1, &r); imp[1] = r.cand[0].distance;
    CHECK(imp[0] > gen[0] && imp[0] > gen[1] && imp[0] > gen[2]);  /* unknown is farther than all known */
    s2w_calibration cal;
    CHECK(s2w_calibrate_threshold(gen, 3, imp, 2, 0.0, &cal) == S2W_OK && cal.far == 0.0 && cal.frr == 0.0);

    s2w_reject_config rc; s2w_reject_config_default(&rc);
    rc.max_distance = cal.threshold;
    s2w_matcher *m2 = s2w_matcher_new(S2W_REP_MFCC, NULL, &rc);
    for (int i = 0; i < 4; i++) {
        s2w_features t = word(&fc, T[i].f1, T[i].f2, 0.25, 0.25, T[i].st, T[i].amp);
        s2w_matcher_add(m2, T[i].l, &t); s2w_features_free(&t);
    }
    CHECK(s2w_matcher_match(m2, &qd, 3, &r) == S2W_OK);
    CHECK(r.decision == S2W_DECISION_REJECT && r.reason == S2W_REJECT_DISTANCE && r.rejection_active && r.n_candidates == 3);
    CHECK(s2w_matcher_match(m2, &qa, 3, &r) == S2W_OK && r.decision == S2W_DECISION_ACCEPT);

    /* Ambiguity: two labels with the same template -> margin 0 -> rejected when margin is required. */
    s2w_reject_config ra; s2w_reject_config_default(&ra); ra.min_margin_ratio = 0.2;
    s2w_matcher *m3 = s2w_matcher_new(S2W_REP_MFCC, NULL, &ra);
    s2w_features ta = word(&fc, 500, 1000, 0.25, 0.25, 1.0, 0.4);
    s2w_matcher_add(m3, "X", &ta); s2w_matcher_add(m3, "Y", &ta);
    CHECK(s2w_matcher_match(m3, &ta, 2, &r) == S2W_OK);
    CHECK(r.decision == S2W_DECISION_REJECT && r.reason == S2W_REJECT_AMBIGUOUS && r.margin_ratio == 0.0);
    CHECK(strcmp(r.cand[0].label, "X") == 0 && strcmp(r.cand[1].label, "Y") == 0); /* deterministic tie-break */

    /* Duration constraint: a very long query excludes all templates -> NO_CANDIDATE. */
    s2w_features longq = word(&fc, 500, 1000, 0.25, 0.25, 4.0, 0.4);
    CHECK(s2w_matcher_match(m, &longq, 3, &r) == S2W_OK);
    CHECK(r.decision == S2W_DECISION_REJECT && r.reason == S2W_REJECT_NO_CANDIDATE && r.n_candidates == 0);

    /* Quality gate: near-silent query. */
    s2w_audio sil = {0};
    sil.samples = calloc(16000, sizeof(float)); sil.n_samples = 16000; sil.sample_rate = 16000;
    s2w_features qs = feats(&sil, &fc);
    CHECK(s2w_matcher_match(m, &qs, 3, &r) == S2W_OK && r.reason == S2W_REJECT_QUALITY && r.decision == S2W_DECISION_REJECT);
    s2w_features_free(&qs); s2w_audio_free(&sil);

    /* Incompatible configs and bad arguments. */
    s2w_feat_config other = fc; other.n_mfcc = 20;
    s2w_audio a2 = render((double[]){500, 1000}, (double[]){0.25, 0.25}, 2, 1.0, 0.4);
    s2w_features fo = feats(&a2, &other);
    CHECK(s2w_matcher_add(m, "Z", &fo) == S2W_ERR_INCOMPATIBLE);
    CHECK(s2w_matcher_match(m, &fo, 1, &r) == S2W_ERR_INCOMPATIBLE);
    other = fc; other.norm = S2W_NORM_NONE;
    s2w_features fo2 = feats(&a2, &other);
    CHECK(s2w_matcher_add(m, "Z", &fo2) == S2W_ERR_INCOMPATIBLE);
    CHECK(s2w_matcher_size(m) == 4);
    CHECK(s2w_matcher_add(m, "", &qa) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_matcher_add(m, "bad\nlabel", &qa) == S2W_ERR_BAD_PARAMS);
    char longl[80]; memset(longl, 'a', 79); longl[79] = 0;
    CHECK(s2w_matcher_add(m, longl, &qa) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_matcher_add(m, "മലയാളം", &qa) == S2W_OK); /* UTF-8 Malayalam label: 18 bytes */
    CHECK(s2w_matcher_add(NULL, "A", &qa) == S2W_ERR_BAD_PARAMS);
    s2w_features zero; memset(&zero, 0, sizeof zero);
    CHECK(s2w_matcher_add(m, "A", &zero) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_matcher_match(m, &qa, 0, &r) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_matcher_match(m, &qa, S2W_MAX_TOPK + 1, &r) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_matcher_match(m, &zero, 1, &r) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_matcher_match(m, &qa, 1, NULL) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_matcher_match(NULL, &qa, 1, &r) == S2W_ERR_BAD_PARAMS);

    /* Invalid matcher settings. */
    s2w_dtw_config bad; s2w_dtw_config_default(&bad); bad.band_ratio = 2;
    CHECK(s2w_matcher_new(S2W_REP_MFCC, &bad, NULL) == NULL);
    CHECK(s2w_matcher_new((s2w_rep)9, NULL, NULL) == NULL);
    s2w_reject_config br; s2w_reject_config_default(&br); br.max_distance = NAN;
    CHECK(s2w_matcher_new(S2W_REP_MFCC, NULL, &br) == NULL);
    s2w_matcher_free(NULL);

    /* Log-Mel representation also works. */
    s2w_matcher *ml = s2w_matcher_new(S2W_REP_LOGMEL, NULL, NULL);
    for (int i = 0; i < 3; i++) {
        s2w_features t = word(&fc, T[i].f1, T[i].f2, 0.25, 0.25, 1.0, 0.4);
        s2w_matcher_add(ml, T[i].l, &t); s2w_features_free(&t);
    }
    CHECK(s2w_matcher_match(ml, &qb, 1, &r) == S2W_OK && strcmp(r.cand[0].label, "B") == 0);

    s2w_features_free(&q); s2w_features_free(&qa); s2w_features_free(&qb); s2w_features_free(&qc);
    s2w_features_free(&qd); s2w_features_free(&qd2); s2w_features_free(&ta); s2w_features_free(&longq);
    s2w_features_free(&fo); s2w_features_free(&fo2); s2w_audio_free(&a2);
    s2w_matcher_free(m); s2w_matcher_free(m2); s2w_matcher_free(m3); s2w_matcher_free(ml);
}

/* Malayalam-style length contrast: same spectra, short vs long vowel. With the warp penalty and the
 * duration band, the same-length match must beat the length-contrast match. */
static void test_length_contrast(void) {
    s2w_feat_config fc; s2w_feat_config_default(&fc);
    s2w_matcher *m = s2w_matcher_new(S2W_REP_MFCC, NULL, NULL);
    s2w_features sh = word(&fc, 600, 1500, 0.10, 0.20, 1.0, 0.4); /* short first vowel */
    s2w_features lg = word(&fc, 600, 1500, 0.30, 0.20, 1.0, 0.4); /* long first vowel */
    s2w_matcher_add(m, "short", &sh); s2w_matcher_add(m, "long", &lg);
    s2w_features qs = word(&fc, 600, 1500, 0.11, 0.21, 1.0, 0.5), ql = word(&fc, 600, 1500, 0.29, 0.19, 1.0, 0.5);
    s2w_match_result r;
    CHECK(s2w_matcher_match(m, &qs, 2, &r) == S2W_OK && strcmp(r.cand[0].label, "short") == 0);
    CHECK(s2w_matcher_match(m, &ql, 2, &r) == S2W_OK && strcmp(r.cand[0].label, "long") == 0);
    s2w_features_free(&sh); s2w_features_free(&lg); s2w_features_free(&qs); s2w_features_free(&ql);
    s2w_matcher_free(m);
}

int main(void) {
    test_dtw_core(); test_calibration(); test_matcher(); test_length_contrast();
    puts(failures ? "FAILED" : "dtw: all tests passed");
    return failures ? 1 : 0;
}
