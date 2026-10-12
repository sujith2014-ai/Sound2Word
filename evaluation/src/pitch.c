#include "s2w_pitch.h"

#include <math.h>
#include <stdlib.h>

static int cmp(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

s2w_status s2w_median_f0(const s2w_audio *a, double *f0) {
    if (!a || !a->samples || !f0 || a->sample_rate < 8000) return S2W_ERR_BAD_PARAMS;
    size_t fl = (size_t)a->sample_rate * 40 / 1000, hop = (size_t)a->sample_rate * 10 / 1000;
    if (a->n_samples < fl + 1) return S2W_ERR_EMPTY;
    size_t lag_min = (size_t)(a->sample_rate / 450.0), lag_max = (size_t)(a->sample_rate / 60.0);
    if (lag_max + 1 >= fl) lag_max = fl - 2;
    size_t nf = 1 + (a->n_samples - fl) / hop;
    double *vals = malloc(nf * sizeof *vals);
    if (!vals) return S2W_ERR_NOMEM;
    size_t nv = 0;
    for (size_t f = 0; f < nf; f++) {
        const float *x = a->samples + f * hop;
        double mean = 0, e0 = 0;
        for (size_t i = 0; i < fl; i++) mean += x[i];
        mean /= (double)fl;
        for (size_t i = 0; i < fl; i++) e0 += (x[i] - mean) * (x[i] - mean);
        if (e0 / (double)fl < 1e-6) continue; /* below about -60 dBFS: unvoiced/silent */
        double best = 0; size_t bl = 0;
        for (size_t lag = lag_min; lag <= lag_max; lag++) {
            double c = 0, e1 = 0;
            for (size_t i = 0; i + lag < fl; i++) {
                c += (x[i] - mean) * (x[i + lag] - mean);
                e1 += (x[i + lag] - mean) * (x[i + lag] - mean);
            }
            double r = c / sqrt(e0 * (e1 > 1e-12 ? e1 : 1e-12));
            if (r > best) { best = r; bl = lag; }
        }
        if (best > 0.6 && bl) vals[nv++] = (double)a->sample_rate / (double)bl;
    }
    s2w_status st = S2W_ERR_EMPTY;
    if (nv >= 8) {
        qsort(vals, nv, sizeof *vals, cmp);
        *f0 = vals[nv / 2];
        st = S2W_OK;
    }
    free(vals);
    return st;
}
