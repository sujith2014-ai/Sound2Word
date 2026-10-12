/* Sound2Word evaluation harness. See docs/EVALUATION.md.
 * Splits: tuning/calibration code may only see enrol+calib; test needs frozen settings. */
#ifndef S2W_EVAL_H
#define S2W_EVAL_H
#include <stdio.h>

#include "s2w_dataset.h"
#include "s2w_dtw.h"

typedef struct {
    s2w_feat_config feat;
    s2w_rep rep;
    s2w_dtw_config dtw;
    s2w_reject_config rej;
    int protocol;                 /* 0 = speaker_independent (default), 1 = personalised; must match the dataset */
    int frozen;
    char calibration_source[256];
    char settings_sha256[65];
} s2w_settings;

void s2w_settings_default(s2w_settings *s);
/* Parse "#s2w-settings v1" key=value file. A frozen file must carry a matching settings_sha256. */
s2w_status s2w_settings_load(const char *path, s2w_settings *s, char *err, size_t errlen);
/* Canonical text; with_freeze also writes frozen/settings_sha256. */
void s2w_settings_write(FILE *f, const s2w_settings *s, int with_freeze);
void s2w_settings_hash(const s2w_settings *s, char hex[65]);   /* excludes frozen + hash fields */
void s2w_settings_freeze(s2w_settings *s);                      /* sets frozen=1 and the hash */

#define S2W_EVAL_K 5
#define S2W_REASON_EXTRACT_FAIL 5 /* extends s2w_reject_reason for unusable queries */

typedef struct {
    size_t rec;                 /* index into ds->r */
    int usable;                 /* features extracted */
    int known;                  /* word role == vocab */
    int true_word;
    size_t n_cand;
    int cand_word[S2W_EVAL_K];
    double cand_dist[S2W_EVAL_K];
    double margin;              /* INFINITY if < 2 candidates */
    unsigned quality;
    int decision;               /* s2w_decision */
    int reason;                 /* s2w_reject_reason or S2W_REASON_EXTRACT_FAIL */
} s2w_query;

typedef struct {
    char id[S2W_ID_MAX];
    double f0[5];
    size_t nf0;
    double median_f0;           /* NaN if undefined */
} s2w_speaker_pitch;

typedef struct {
    int split;
    s2w_query *q; size_t nq;
    s2w_speaker_pitch *sp; size_t nsp;
    size_t n_templates;
} s2w_run;

/* Apply a rejection config to a stored query (the single decision path used everywhere). */
void s2w_eval_decide(const s2w_query *q, const s2w_reject_config *rc, int *decision, int *reason);

/* Builds templates from enrol, evaluates every recording of `split` (enrol is never a query split).
 * Audio of other splits is NOT opened. Pinned audio hashes are verified. */
s2w_status s2w_eval_collect(const s2w_dataset *ds, const s2w_settings *st, int split, s2w_run *run, char *err, size_t errlen);
void s2w_run_free(s2w_run *r);
/* Recompute decisions of all queries under `rc`. */
void s2w_run_redecide(s2w_run *r, const s2w_reject_config *rc);

typedef struct { size_t num, den; } s2w_ratio;
typedef struct {
    size_t n_queries, n_unusable, n_known, n_unknown;
    s2w_ratio top[3];                    /* top-1, top-3, top-5 over known queries */
    s2w_ratio known_accepted_correct, known_accepted_wrong, known_false_reject, known_precision_accepted;
    s2w_ratio unknown_false_accept, unknown_correct_reject;
    size_t known_rej_reason[6], unknown_rej_reason[6];
} s2w_metrics;

/* Metrics over queries where mask[i] != 0 (mask NULL = all). */
void s2w_eval_metrics(const s2w_dataset *ds, const s2w_run *r, const unsigned char *mask, s2w_metrics *m);

typedef struct {
    int min_group_speakers, min_group_queries;
    const char *run_id, *created_utc, *command, *split_name;
    const char *git_commit; int git_dirty; const char *git_describe;
    int test_prev, test_prev_other, test_reuse_ack;   /* test-split ledger info (-1 = not a test run) */
} s2w_report_ctx;

/* Standard run result (metrics, group breakdowns, minimal pairs). */
void s2w_eval_write_json(FILE *f, const s2w_dataset *ds, const s2w_settings *st, const s2w_run *r, const s2w_report_ctx *c);
void s2w_eval_write_summary(FILE *f, const s2w_dataset *ds, const s2w_settings *st, const s2w_run *r, const s2w_report_ctx *c);
void s2w_eval_write_queries_csv(FILE *f, const s2w_dataset *ds, const s2w_run *r);

/* Normalisation comparison (calibration split only, rejection off): runs[i] was collected with norms[i]. */
void s2w_eval_write_compare_json(FILE *f, const s2w_dataset *ds, const s2w_settings *st, const s2w_run *runs,
                                 const s2w_norm_mode *norms, size_t n, const s2w_report_ctx *c);

#define S2W_N_MARGIN_SWEEP 6
typedef struct {
    int include_wrong_known;
    double max_far;
    size_t n_genuine, n_impostor_oov, n_impostor_wrong, n_excluded_unusable;
    s2w_calibration cal;                 /* in-sample FAR/FRR on the calibration data */
    double margin[S2W_N_MARGIN_SWEEP], sweep_frr[S2W_N_MARGIN_SWEEP], sweep_far[S2W_N_MARGIN_SWEEP];
} s2w_calib_report;

/* Chooses max_distance from calibration-split queries (genuine = known & top-1 correct; impostor =
 * unknown [+ known & top-1 wrong]). Needs >= 1 genuine and >= 1 impostor. */
s2w_status s2w_eval_calibrate(const s2w_dataset *ds, const s2w_run *r, double max_far, int include_wrong, s2w_calib_report *out);
void s2w_eval_write_calibration_json(FILE *f, const s2w_dataset *ds, const s2w_settings *st_in, const s2w_settings *st_out,
                                     const s2w_run *r, const s2w_calib_report *cr, const s2w_report_ctx *c);
#endif
