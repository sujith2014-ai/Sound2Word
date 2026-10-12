/* Sound2Word - Phase 3: constrained DTW matcher with top-k ranking and rejection.
 *
 * Distance model
 *   frame cost   d(i,j) = sqrt( sum_k (q[i][k]-t[j][k])^2 / dim )     (RMS difference)
 *   recursion    symmetric2: diagonal step weight 2*d, horizontal/vertical step d + warp_penalty
 *   normalised   D(n,m) / (n+m)   -> comparable across utterance lengths
 *
 * Constraints (hard, applied before any cost is computed):
 *   duration  : n/m must lie in [min_len_ratio, max_len_ratio]; otherwise the pair is
 *               "incompatible" and its distance is +INFINITY (not an error).
 *   warping   : Sakoe-Chiba band of half-width ceil(band_ratio * max(n,m)) frames around the
 *               straight line from (0,0) to (n-1,m-1).
 * Soft duration cue: warp_penalty charges every non-diagonal step, so stretching a short vowel
 * into a long one (or vice versa) costs more than an equally-spectral match, which keeps
 * Malayalam length/gemination contrasts visible.
 *
 * Scores are DISTANCES, not probabilities. Nothing here is a calibrated confidence. Rejection
 * thresholds are inactive until the caller sets them from held-out data
 * (see s2w_calibrate_threshold); results say whether rejection was active.
 *
 * Everything is deterministic: no randomness; ties broken by label then insertion order.
 */
#ifndef S2W_DTW_H
#define S2W_DTW_H

#include "s2w_features.h"

#ifdef __cplusplus
extern "C" {
#endif

#define S2W_DTW_MAX_FRAMES 6000
#define S2W_MAX_TOPK 10
#define S2W_MAX_LABEL_BYTES 63

typedef struct {
    double band_ratio;     /* (0,1], default 0.30 */
    double min_len_ratio;  /* (0,1], default 0.50  (query frames / template frames) */
    double max_len_ratio;  /* [1,inf), default 2.00 */
    double warp_penalty;   /* >= 0, default 0.10, in frame-cost units, per non-diagonal step */
} s2w_dtw_config;

void s2w_dtw_config_default(s2w_dtw_config *c);
s2w_status s2w_dtw_config_validate(const s2w_dtw_config *c);

/* Normalised DTW distance between row-major matrices q (n x dim) and t (m x dim).
 * *dist = +INFINITY when the duration constraint rules the pair out (returns S2W_OK).
 * Errors: BAD_PARAMS (null, dim 0, n or m 0 or > S2W_DTW_MAX_FRAMES, bad config),
 * NONFINITE (NaN/Inf input), NOMEM. */
s2w_status s2w_dtw_distance(const float *q, size_t n, const float *t, size_t m, size_t dim,
                            const s2w_dtw_config *cfg, double *dist);

/* ---- rejection ------------------------------------------------------------------------ */

typedef struct {
    double max_distance;     /* reject if best distance > this; INFINITY = off (default) */
    double min_margin_ratio; /* reject if (d2-d1)/max(d1,1e-9) < this and >=2 labels; 0 = off */
    unsigned quality_mask;   /* reject query whose quality.warnings has any of these bits;
                                default S2W_WARN_TOO_SHORT | S2W_WARN_MOSTLY_SILENT */
} s2w_reject_config;

void s2w_reject_config_default(s2w_reject_config *c);

typedef struct {
    double threshold;  /* accept if distance <= threshold */
    double far;        /* achieved impostor acceptance rate on the supplied data */
    double frr;        /* genuine rejection rate on the supplied data */
} s2w_calibration;

/* Choose the largest threshold whose impostor acceptance rate is <= max_far, from held-out
 * distances (genuine = correct-word best distance, impostor = out-of-vocabulary or wrong-word
 * best distance). Never use evaluation data that was also used to build templates. */
s2w_status s2w_calibrate_threshold(const double *genuine, size_t n_genuine,
                                   const double *impostor, size_t n_impostor,
                                   double max_far, s2w_calibration *out);

/* ---- template library + matcher ---------------------------------------------------------- */

typedef enum { S2W_REP_MFCC = 0, S2W_REP_LOGMEL = 1 } s2w_rep; /* normalised matrices are used */

typedef enum { S2W_DECISION_ACCEPT = 0, S2W_DECISION_REJECT = 1 } s2w_decision;
typedef enum {
    S2W_REJECT_NONE = 0,
    S2W_REJECT_QUALITY,       /* query audio unusable (see quality_mask) */
    S2W_REJECT_NO_CANDIDATE,  /* every template failed the duration constraint, or library empty */
    S2W_REJECT_DISTANCE,      /* best distance above max_distance */
    S2W_REJECT_AMBIGUOUS      /* top two labels too close */
} s2w_reject_reason;

typedef struct {
    const char *label;      /* owned by the matcher; valid until s2w_matcher_free */
    double distance;        /* best normalised distance over this label's templates */
    size_t template_index;  /* insertion index of the best template */
} s2w_candidate;

typedef struct {
    s2w_decision decision;
    s2w_reject_reason reason;
    int rejection_active;   /* 1 if max_distance or min_margin_ratio was set */
    size_t n_candidates;    /* <= requested k; filled even when rejected (closest labels) */
    s2w_candidate cand[S2W_MAX_TOPK];
    double margin_ratio;    /* (d2-d1)/max(d1,1e-9); INFINITY with a single candidate */
} s2w_match_result;

typedef struct s2w_matcher s2w_matcher;

/* Returns NULL on invalid config or OOM. */
s2w_matcher *s2w_matcher_new(s2w_rep rep, const s2w_dtw_config *dtw, const s2w_reject_config *rej);
void s2w_matcher_free(s2w_matcher *m);
size_t s2w_matcher_size(const s2w_matcher *m);

/* Copies the features. The first template fixes the feature config; later ones (and queries)
 * must match it exactly, else S2W_ERR_INCOMPATIBLE. Label: 1..63 bytes, no control chars. */
s2w_status s2w_matcher_add(s2w_matcher *m, const char *label, const s2w_features *f);

/* Rank labels by best-template distance (ascending). k in 1..S2W_MAX_TOPK. */
s2w_status s2w_matcher_match(const s2w_matcher *m, const s2w_features *query, int k,
                             s2w_match_result *res);

#ifdef __cplusplus
}
#endif
#endif
