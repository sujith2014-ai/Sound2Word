#include "s2w_dtw.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

void s2w_dtw_config_default(s2w_dtw_config *c) {
    c->band_ratio = 0.30;
    c->min_len_ratio = 0.50;
    c->max_len_ratio = 2.00;
    c->warp_penalty = 0.10;
}

s2w_status s2w_dtw_config_validate(const s2w_dtw_config *c) {
    if (!c) return S2W_ERR_BAD_PARAMS;
    if (!isfinite(c->band_ratio) || c->band_ratio <= 0 || c->band_ratio > 1) return S2W_ERR_BAD_PARAMS;
    if (!isfinite(c->min_len_ratio) || c->min_len_ratio <= 0 || c->min_len_ratio > 1) return S2W_ERR_BAD_PARAMS;
    if (!isfinite(c->max_len_ratio) || c->max_len_ratio < 1) return S2W_ERR_BAD_PARAMS;
    if (!isfinite(c->warp_penalty) || c->warp_penalty < 0) return S2W_ERR_BAD_PARAMS;
    return S2W_OK;
}

void s2w_reject_config_default(s2w_reject_config *c) {
    c->max_distance = INFINITY;
    c->min_margin_ratio = 0.0;
    c->quality_mask = S2W_WARN_TOO_SHORT | S2W_WARN_MOSTLY_SILENT;
}

static double frame_cost(const float *a, const float *b, size_t dim) {
    double s = 0;
    for (size_t k = 0; k < dim; k++) {
        double d = (double)a[k] - (double)b[k];
        s += d * d;
    }
    return sqrt(s / (double)dim);
}

s2w_status s2w_dtw_distance(const float *q, size_t n, const float *t, size_t m, size_t dim,
                            const s2w_dtw_config *cfg, double *dist) {
    if (!q || !t || !dist || !cfg || dim == 0) return S2W_ERR_BAD_PARAMS;
    if (n == 0 || m == 0 || n > S2W_DTW_MAX_FRAMES || m > S2W_DTW_MAX_FRAMES) return S2W_ERR_BAD_PARAMS;
    s2w_status st = s2w_dtw_config_validate(cfg);
    if (st != S2W_OK) return st;
    for (size_t i = 0; i < n * dim; i++) if (!isfinite(q[i])) return S2W_ERR_NONFINITE;
    for (size_t i = 0; i < m * dim; i++) if (!isfinite(t[i])) return S2W_ERR_NONFINITE;

    double ratio = (double)n / (double)m;
    if (ratio < cfg->min_len_ratio || ratio > cfg->max_len_ratio) {
        *dist = INFINITY;
        return S2W_OK;
    }

    size_t mx = n > m ? n : m;
    double w = ceil(cfg->band_ratio * (double)mx);
    double *prev = malloc(m * sizeof *prev), *cur = malloc(m * sizeof *cur);
    if (!prev || !cur) { free(prev); free(cur); return S2W_ERR_NOMEM; }
    for (size_t j = 0; j < m; j++) prev[j] = cur[j] = INFINITY;

    double pen = cfg->warp_penalty;
    for (size_t i = 0; i < n; i++) {
        double centre = n > 1 ? (double)i * (double)(m - 1) / (double)(n - 1) : 0.0;
        double lo_d = ceil(centre - w), hi_d = floor(centre + w);
        size_t lo = lo_d < 0 ? 0 : (size_t)lo_d;
        size_t hi = hi_d >= (double)(m - 1) ? m - 1 : (size_t)hi_d;
        for (size_t j = 0; j < m; j++) cur[j] = INFINITY;
        for (size_t j = lo; j <= hi; j++) {
            double d = frame_cost(q + i * dim, t + j * dim, dim), best;
            if (i == 0 && j == 0) {
                best = 2.0 * d;
            } else {
                best = INFINITY;
                if (i > 0 && j > 0 && prev[j - 1] + 2.0 * d < best) best = prev[j - 1] + 2.0 * d;
                if (i > 0 && prev[j] + d + pen < best) best = prev[j] + d + pen;
                if (j > 0 && cur[j - 1] + d + pen < best) best = cur[j - 1] + d + pen;
            }
            cur[j] = best;
        }
        double *tmp = prev; prev = cur; cur = tmp;
    }
    double total = prev[m - 1];
    free(prev); free(cur);
    *dist = isfinite(total) ? total / (double)(n + m) : INFINITY;
    return S2W_OK;
}

static int cmp_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

s2w_status s2w_calibrate_threshold(const double *gen, size_t ng, const double *imp, size_t ni,
                                   double max_far, s2w_calibration *out) {
    if (!gen || !imp || !out || ng == 0 || ni == 0) return S2W_ERR_BAD_PARAMS;
    if (!isfinite(max_far) || max_far < 0 || max_far >= 1) return S2W_ERR_BAD_PARAMS;
    for (size_t i = 0; i < ng; i++) if (!isfinite(gen[i])) return S2W_ERR_NONFINITE;
    for (size_t i = 0; i < ni; i++) if (!isfinite(imp[i])) return S2W_ERR_NONFINITE;
    double *s = malloc(ni * sizeof *s);
    if (!s) return S2W_ERR_NOMEM;
    memcpy(s, imp, ni * sizeof *s);
    qsort(s, ni, sizeof *s, cmp_double);
    size_t allowed = (size_t)floor(max_far * (double)ni);
    if (allowed >= ni) allowed = ni - 1;
    double t = nextafter(s[allowed], -INFINITY); /* at most `allowed` impostors fall at or below t */
    size_t fa = 0, fr = 0;
    for (size_t i = 0; i < ni; i++) fa += imp[i] <= t;
    for (size_t i = 0; i < ng; i++) fr += gen[i] > t;
    free(s);
    out->threshold = t;
    out->far = (double)fa / (double)ni;
    out->frr = (double)fr / (double)ng;
    return S2W_OK;
}

/* ---- matcher ----------------------------------------------------------------------------- */

typedef struct {
    char *label;
    float *data;
    size_t n_frames;
} tmpl;

struct s2w_matcher {
    s2w_rep rep;
    s2w_dtw_config dtw;
    s2w_reject_config rej;
    s2w_feat_config fcfg;
    size_t dim;
    int locked;
    tmpl *t;
    size_t n, cap;
};

s2w_matcher *s2w_matcher_new(s2w_rep rep, const s2w_dtw_config *dtw, const s2w_reject_config *rej) {
    if (rep != S2W_REP_MFCC && rep != S2W_REP_LOGMEL) return NULL;
    s2w_matcher *m = calloc(1, sizeof *m);
    if (!m) return NULL;
    m->rep = rep;
    if (dtw) m->dtw = *dtw; else s2w_dtw_config_default(&m->dtw);
    if (rej) m->rej = *rej; else s2w_reject_config_default(&m->rej);
    if (s2w_dtw_config_validate(&m->dtw) != S2W_OK || isnan(m->rej.max_distance) ||
        m->rej.max_distance < 0 || !isfinite(m->rej.min_margin_ratio) || m->rej.min_margin_ratio < 0) {
        free(m);
        return NULL;
    }
    return m;
}

void s2w_matcher_free(s2w_matcher *m) {
    if (!m) return;
    for (size_t i = 0; i < m->n; i++) { free(m->t[i].label); free(m->t[i].data); }
    free(m->t);
    free(m);
}

size_t s2w_matcher_size(const s2w_matcher *m) { return m ? m->n : 0; }

static int cfg_equal(const s2w_feat_config *a, const s2w_feat_config *b) {
    return a->sample_rate == b->sample_rate && a->frame_ms == b->frame_ms && a->hop_ms == b->hop_ms &&
           a->fft_size == b->fft_size && a->n_mels == b->n_mels && a->n_mfcc == b->n_mfcc &&
           a->f_min_hz == b->f_min_hz && a->f_max_hz == b->f_max_hz && a->preemph == b->preemph &&
           a->norm == b->norm;
}

static const float *rep_data(const s2w_matcher *m, const s2w_features *f, size_t *dim) {
    if (m->rep == S2W_REP_MFCC) { *dim = f->n_mfcc; return f->mfcc_norm; }
    *dim = f->n_mels;
    return f->logmel_norm;
}

static int features_usable(const s2w_features *f) {
    return f && f->format_version == S2W_FEAT_FORMAT_VERSION && f->n_frames > 0 &&
           f->n_frames <= S2W_DTW_MAX_FRAMES && f->logmel_norm && f->mfcc_norm;
}

s2w_status s2w_matcher_add(s2w_matcher *m, const char *label, const s2w_features *f) {
    if (!m || !label || !features_usable(f)) return S2W_ERR_BAD_PARAMS;
    size_t len = strlen(label);
    if (len == 0 || len > S2W_MAX_LABEL_BYTES) return S2W_ERR_BAD_PARAMS;
    for (size_t i = 0; i < len; i++) if ((unsigned char)label[i] < 0x20 || label[i] == 0x7f) return S2W_ERR_BAD_PARAMS;
    size_t dim;
    const float *src = rep_data(m, f, &dim);
    if (m->locked && (!cfg_equal(&m->fcfg, &f->config) || dim != m->dim)) return S2W_ERR_INCOMPATIBLE;

    if (m->n == m->cap) {
        size_t nc = m->cap ? m->cap * 2 : 8;
        tmpl *nt = realloc(m->t, nc * sizeof *nt);
        if (!nt) return S2W_ERR_NOMEM;
        m->t = nt; m->cap = nc;
    }
    char *l = malloc(len + 1);
    float *d = malloc(f->n_frames * dim * sizeof *d);
    if (!l || !d) { free(l); free(d); return S2W_ERR_NOMEM; }
    memcpy(l, label, len + 1);
    memcpy(d, src, f->n_frames * dim * sizeof *d);
    m->t[m->n].label = l; m->t[m->n].data = d; m->t[m->n].n_frames = f->n_frames;
    m->n++;
    if (!m->locked) { m->fcfg = f->config; m->dim = dim; m->locked = 1; }
    return S2W_OK;
}

typedef struct { const char *label; double dist; size_t idx; } lab_best;

static int cmp_lab(const void *a, const void *b) {
    const lab_best *x = a, *y = b;
    if (x->dist < y->dist) return -1;
    if (x->dist > y->dist) return 1;
    int c = strcmp(x->label, y->label);
    if (c) return c;
    return (x->idx > y->idx) - (x->idx < y->idx);
}

s2w_status s2w_matcher_match(const s2w_matcher *m, const s2w_features *q, int k, s2w_match_result *res) {
    if (!res) return S2W_ERR_BAD_PARAMS;
    memset(res, 0, sizeof *res);
    res->decision = S2W_DECISION_REJECT;
    res->margin_ratio = INFINITY;
    if (!m || !features_usable(q) || k < 1 || k > S2W_MAX_TOPK) return S2W_ERR_BAD_PARAMS;
    if (!m->locked) return S2W_ERR_EMPTY;
    size_t dim;
    const float *qd = rep_data(m, q, &dim);
    if (dim != m->dim || !cfg_equal(&m->fcfg, &q->config)) return S2W_ERR_INCOMPATIBLE;
    res->rejection_active = isfinite(m->rej.max_distance) || m->rej.min_margin_ratio > 0;

    /* best template per distinct label (first-seen order, then sorted deterministically) */
    lab_best *lb = malloc(m->n * sizeof *lb);
    if (!lb) return S2W_ERR_NOMEM;
    size_t nl = 0;
    for (size_t i = 0; i < m->n; i++) {
        double d;
        s2w_status st = s2w_dtw_distance(qd, q->n_frames, m->t[i].data, m->t[i].n_frames, dim, &m->dtw, &d);
        if (st != S2W_OK) { free(lb); return st; }
        if (!isfinite(d)) continue; /* ruled out by duration constraint */
        size_t j;
        for (j = 0; j < nl; j++) if (!strcmp(lb[j].label, m->t[i].label)) break;
        if (j == nl) { lb[nl].label = m->t[i].label; lb[nl].dist = d; lb[nl].idx = i; nl++; }
        else if (d < lb[j].dist) { lb[j].dist = d; lb[j].idx = i; }
    }
    qsort(lb, nl, sizeof *lb, cmp_lab);
    res->n_candidates = nl < (size_t)k ? nl : (size_t)k;
    for (size_t i = 0; i < res->n_candidates; i++) {
        res->cand[i].label = lb[i].label;
        res->cand[i].distance = lb[i].dist;
        res->cand[i].template_index = lb[i].idx;
    }
    if (nl >= 2) res->margin_ratio = (lb[1].dist - lb[0].dist) / (lb[0].dist > 1e-9 ? lb[0].dist : 1e-9);
    double best = nl ? lb[0].dist : INFINITY;
    free(lb);

    if (q->quality.warnings & m->rej.quality_mask) res->reason = S2W_REJECT_QUALITY;
    else if (nl == 0) res->reason = S2W_REJECT_NO_CANDIDATE;
    else if (best > m->rej.max_distance) res->reason = S2W_REJECT_DISTANCE;
    else if (nl >= 2 && res->margin_ratio < m->rej.min_margin_ratio) res->reason = S2W_REJECT_AMBIGUOUS;
    else { res->reason = S2W_REJECT_NONE; res->decision = S2W_DECISION_ACCEPT; }
    return S2W_OK;
}
