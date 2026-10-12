#include "s2w_qa.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FRAME_MS 20
#define CLIP_LEVEL 0.9995
#define SNR_CAP 99.0

void s2w_qa_limits_clean(s2w_qa_limits *l) {
    l->snr_ok_db = 20.0; l->snr_reject_db = 10.0; l->min_level_dbfs = -45.0; l->min_edge_s = 0.05;
    l->hard_clip_fraction = 0.001; l->dropout_ms = 5.0; l->max_dc = 0.02;
}
void s2w_qa_limits_noisy(s2w_qa_limits *l) {
    s2w_qa_limits_clean(l);
    l->snr_ok_db = 5.0; l->snr_reject_db = 0.0;
}

static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

static double db(double p) { return 10.0 * log10(p > 1e-12 ? p : 1e-12); }

s2w_status s2w_qa_analyse(const s2w_audio *a, const s2w_qa_limits *lim, s2w_qa *o) {
    if (!o) return S2W_ERR_BAD_PARAMS;
    memset(o, 0, sizeof *o);
    if (!a || !a->samples || !lim || a->sample_rate < 1000) return S2W_ERR_BAD_PARAMS;
    size_t fl = (size_t)a->sample_rate * FRAME_MS / 1000;
    if (fl == 0) fl = 1;
    size_t nf = a->n_samples / fl;
    if (nf < 5) return S2W_ERR_TOO_SHORT;
    for (size_t i = 0; i < a->n_samples; i++) if (!isfinite(a->samples[i])) return S2W_ERR_NONFINITE;

    double *pw = malloc(nf * sizeof *pw), *sorted = malloc(nf * sizeof *sorted);
    if (!pw || !sorted) { free(pw); free(sorted); return S2W_ERR_NOMEM; }
    double sum = 0;
    for (size_t i = 0; i < a->n_samples; i++) sum += a->samples[i];
    o->dc_offset = sum / (double)a->n_samples;
    for (size_t f = 0; f < nf; f++) {       /* power after removing the global mean, so DC does not masquerade as noise */
        double e = 0;
        for (size_t k = 0; k < fl; k++) { double v = (double)a->samples[f * fl + k] - o->dc_offset; e += v * v; }
        pw[f] = e / (double)fl;
    }
    memcpy(sorted, pw, nf * sizeof *sorted);
    qsort(sorted, nf, sizeof *sorted, cmpd);
    size_t nlow = nf / 10 ? nf / 10 : 1;
    double pn = 0;
    for (size_t i = 0; i < nlow; i++) pn += sorted[i];
    pn /= (double)nlow;
    double ps = sorted[(nf * 9) / 10 < nf ? (nf * 9) / 10 : nf - 1];
    o->noise_floor_dbfs = db(pn);
    o->speech_level_dbfs = db(ps);
    double sig = ps - pn;
    o->snr_db = sig > 1e-12 && pn > 1e-12 ? 10.0 * log10(sig / pn) : (sig > 1e-12 ? SNR_CAP : 0.0);
    if (o->snr_db > SNR_CAP) o->snr_db = SNR_CAP;

    double dur = (double)nf * (double)fl / (double)a->sample_rate;
    double fdur = (double)fl / (double)a->sample_rate;
    size_t first = nf, last = 0;
    if (o->speech_level_dbfs - o->noise_floor_dbfs < 3.0) {
        o->flags |= S2W_QA_NO_SPEECH;
    } else {
        double thr_db = o->noise_floor_dbfs + fmin(10.0, 0.5 * (o->speech_level_dbfs - o->noise_floor_dbfs));
        for (size_t f = 0; f < nf; f++)
            if (db(pw[f]) > thr_db) { if (f < first) first = f; last = f; }
        if (first >= nf) o->flags |= S2W_QA_NO_SPEECH;
    }
    if (!(o->flags & S2W_QA_NO_SPEECH)) {
        o->leading_s = (double)first * fdur;
        o->trailing_s = (double)(nf - 1 - last) * fdur + (double)(a->n_samples - nf * fl) / (double)a->sample_rate;
        o->speech_s = (double)(last - first + 1) * fdur;
        if (o->leading_s < lim->min_edge_s) o->flags |= S2W_QA_ONSET_CUT;
        if (o->trailing_s < lim->min_edge_s) o->flags |= S2W_QA_OFFSET_CUT;
    } else {
        o->leading_s = dur;
    }

    /* clipped runs (>= 3 consecutive samples at full scale) */
    size_t run = 0;
    for (size_t i = 0; i <= a->n_samples; i++) {
        int c = i < a->n_samples && fabs((double)a->samples[i]) >= CLIP_LEVEL;
        if (c) run++;
        else {
            if (run >= 3) { o->clipped_run_samples += run; if (run > o->max_clip_run) o->max_clip_run = run; }
            run = 0;
        }
    }
    if (o->clipped_run_samples) {
        if ((double)o->clipped_run_samples / (double)a->n_samples > lim->hard_clip_fraction) o->flags |= S2W_QA_HARD_CLIP;
        else o->flags |= S2W_QA_CLIP_RUNS;
    }

    /* exact-zero runs inside the speech span */
    if (!(o->flags & S2W_QA_NO_SPEECH)) {
        size_t lo = first * fl, hi = (last + 1) * fl, zr = 0;
        size_t min_run = (size_t)(lim->dropout_ms * a->sample_rate / 1000.0);
        if (min_run < 2) min_run = 2;
        for (size_t i = lo; i <= hi && i <= a->n_samples; i++) {
            int z = i < hi && i < a->n_samples && a->samples[i] == 0.0f;
            if (z) zr++;
            else {
                if (zr >= min_run) {
                    o->n_dropouts++;
                    double ms = 1000.0 * (double)zr / (double)a->sample_rate;
                    if (ms > o->longest_dropout_ms) o->longest_dropout_ms = ms;
                }
                zr = 0;
            }
        }
        if (o->n_dropouts) o->flags |= S2W_QA_DROPOUT;
    }

    if (o->snr_db < lim->snr_ok_db) o->flags |= S2W_QA_LOW_SNR;
    if (o->speech_level_dbfs < lim->min_level_dbfs) o->flags |= S2W_QA_LOW_LEVEL;
    if (fabs(o->dc_offset) > lim->max_dc) o->flags |= S2W_QA_DC_OFFSET;

    unsigned reject = S2W_QA_NO_SPEECH | S2W_QA_HARD_CLIP | S2W_QA_ONSET_CUT | S2W_QA_OFFSET_CUT;
    if (o->flags & reject || o->snr_db < lim->snr_reject_db) o->verdict = S2W_QA_REJECT;
    else if (o->flags) o->verdict = S2W_QA_MARGINAL;
    else o->verdict = S2W_QA_OK;
    free(pw); free(sorted);
    return S2W_OK;
}

void s2w_qa_flag_names(unsigned flags, char *buf, size_t n) {
    static const struct { unsigned bit; const char *name; } T[] = {
        {S2W_QA_NO_SPEECH, "no_speech"}, {S2W_QA_HARD_CLIP, "hard_clip"}, {S2W_QA_ONSET_CUT, "onset_cut"},
        {S2W_QA_OFFSET_CUT, "offset_cut"}, {S2W_QA_LOW_SNR, "low_snr"}, {S2W_QA_LOW_LEVEL, "low_level"},
        {S2W_QA_DROPOUT, "dropout"}, {S2W_QA_DC_OFFSET, "dc_offset"}, {S2W_QA_CLIP_RUNS, "clip_runs"}};
    size_t o = 0;
    buf[0] = 0;
    for (size_t i = 0; i < sizeof T / sizeof *T; i++)
        if ((flags & T[i].bit) && o < n) o += (size_t)snprintf(buf + o, n - o, "%s%s", o ? "," : "", T[i].name);
    if (!o) snprintf(buf, n, "none");
}

const char *s2w_qa_verdict_name(s2w_qa_verdict v) { return v == S2W_QA_OK ? "ok" : v == S2W_QA_MARGINAL ? "marginal" : "reject"; }
