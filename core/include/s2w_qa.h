/* Sound2Word - recording quality assessment for isolated-word recordings.
 *
 * Complements the Phase 1 stats (unchanged). All thresholds are initial engineering defaults chosen
 * WITHOUT real Malayalam speech; they are configurable and must be revisited with real recordings.
 * The SNR is a crude percentile estimate from 20 ms frame powers: it assumes the recording contains
 * some non-speech frames. If speech fills the whole file the noise floor is over-estimated and SNR
 * under-estimated.
 */
#ifndef S2W_QA_H
#define S2W_QA_H
#include "s2w_audio.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    S2W_QA_NO_SPEECH   = 1 << 0,  /* reject: no frames clearly above the noise floor */
    S2W_QA_HARD_CLIP   = 1 << 1,  /* reject: clipped runs exceed limit */
    S2W_QA_ONSET_CUT   = 1 << 2,  /* reject: speech starts inside the first min_edge_s */
    S2W_QA_OFFSET_CUT  = 1 << 3,  /* reject: speech still active inside the last min_edge_s */
    S2W_QA_LOW_SNR     = 1 << 4,  /* marginal or reject depending on limits */
    S2W_QA_LOW_LEVEL   = 1 << 5,  /* marginal */
    S2W_QA_DROPOUT     = 1 << 6,  /* marginal: exact-zero run inside the speech span */
    S2W_QA_DC_OFFSET   = 1 << 7,  /* marginal */
    S2W_QA_CLIP_RUNS   = 1 << 8   /* marginal: some clipped runs, below the hard-clip limit */
} s2w_qa_flag;

typedef enum { S2W_QA_OK = 0, S2W_QA_MARGINAL = 1, S2W_QA_REJECT = 2 } s2w_qa_verdict;

typedef struct {
    double snr_ok_db, snr_reject_db;  /* below ok: marginal; below reject: reject */
    double min_level_dbfs;            /* speech level below this: marginal */
    double min_edge_s;                /* required non-speech margin at each end */
    double hard_clip_fraction;        /* clipped-run samples / n above this: reject */
    double dropout_ms;                /* exact-zero run length that counts as a dropout */
    double max_dc;                    /* |mean| above this: marginal */
} s2w_qa_limits;

void s2w_qa_limits_clean(s2w_qa_limits *l);   /* quiet-room recordings */
void s2w_qa_limits_noisy(s2w_qa_limits *l);   /* deliberately noisy conditions */

typedef struct {
    double noise_floor_dbfs, speech_level_dbfs, snr_db;   /* snr capped at 99 */
    double leading_s, trailing_s, speech_s;
    size_t clipped_run_samples, max_clip_run, n_dropouts;
    double longest_dropout_ms, dc_offset;
    unsigned flags;                                       /* s2w_qa_flag bits */
    s2w_qa_verdict verdict;
} s2w_qa;

/* Errors: BAD_PARAMS, TOO_SHORT (< 5 frames of 20 ms). Never modifies the audio. */
s2w_status s2w_qa_analyse(const s2w_audio *a, const s2w_qa_limits *lim, s2w_qa *out);
/* Comma-separated flag names into buf ("none" if no flags). */
void s2w_qa_flag_names(unsigned flags, char *buf, size_t n);
const char *s2w_qa_verdict_name(s2w_qa_verdict v);

#ifdef __cplusplus
}
#endif
#endif
