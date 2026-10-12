/* Sound2Word - Phase 2: feature engine.
 *
 * Pipeline (per frame, all arithmetic in double, output float):
 *   frame (frame_ms) -> optional pre-emphasis (frame-local) -> periodic Hann
 *   -> zero-pad to fft_size -> radix-2 FFT -> power |X|^2 / fft_size
 *   -> triangular HTK-Mel filter bank -> ln(max(E, S2W_LOG_FLOOR))   [log-Mel]
 *   -> orthonormal DCT-II over bands, first n_mfcc coefficients      [MFCC]
 *
 * Log-Mel is the preferred representation for future neural models; MFCC is
 * for the classical DTW baseline. Neither makes recognition robust to speaker,
 * dialect or noise by itself.
 *
 * Design rules (Malayalam: vowel length and gemination are phonemic):
 *  - No frames are ever dropped, trimmed or time-warped: n_frames depends only
 *    on the sample count, so duration cues are preserved exactly.
 *  - Normalisation is per-utterance, computed from the audio alone (no labels,
 *    no cross-utterance state), is conservative (MEAN only subtracts a
 *    per-coefficient average) and never touches the time axis.
 *  - Raw and normalised matrices are always both available.
 *  - Deterministic: no randomness, no threads, no data-dependent allocation
 *    order. Bit-identical on one platform/compiler; across platforms libm
 *    differences may change the last float digits (text output is %.6f).
 */
#ifndef S2W_FEATURES_H
#define S2W_FEATURES_H

#include <stdio.h>

#include "s2w_audio.h"

#ifdef __cplusplus
extern "C" {
#endif

#define S2W_FEAT_FORMAT_VERSION 1
#define S2W_FEAT_LIB_VERSION "0.2.0"
#define S2W_LOG_FLOOR 1e-10

typedef enum {
    S2W_NORM_NONE = 0,     /* normalised == raw copy */
    S2W_NORM_MEAN = 1,     /* subtract per-utterance, per-dimension mean (CMN) */
    S2W_NORM_MEANVAR = 2   /* CMN + divide by per-dimension std (CMVN) */
} s2w_norm_mode;

typedef struct {
    int sample_rate;     /* 8000..48000; audio must already be at this rate */
    int frame_ms;        /* 10..100, default 25 */
    int hop_ms;          /* 5..frame_ms, default 10 */
    int fft_size;        /* power of two, 128..4096, >= frame length; default 512 */
    int n_mels;          /* 8..128, default 40 */
    int n_mfcc;          /* 1..min(n_mels, 40), default 13 (includes c0) */
    double f_min_hz;     /* >= 0, default 20 */
    double f_max_hz;     /* > f_min, <= sample_rate/2; 0 means sample_rate/2 */
    double preemph;      /* 0..0.99, 0 disables; default 0.97 */
    s2w_norm_mode norm;  /* default S2W_NORM_MEAN */
} s2w_feat_config;

void s2w_feat_config_default(s2w_feat_config *c);
/* S2W_OK or S2W_ERR_BAD_PARAMS. f_max_hz == 0 is accepted (resolved later). */
s2w_status s2w_feat_config_validate(const s2w_feat_config *c);

typedef struct {
    uint32_t format_version;      /* S2W_FEAT_FORMAT_VERSION */
    char lib_version[16];         /* S2W_FEAT_LIB_VERSION */
    s2w_feat_config config;       /* resolved (f_max_hz filled in) */
    size_t n_frames, n_mels, n_mfcc;
    size_t source_samples;
    s2w_stats quality;            /* Phase 1 measurements of the source audio */
    float *logmel, *logmel_norm;  /* n_frames x n_mels, row-major */
    float *mfcc, *mfcc_norm;      /* n_frames x n_mfcc, row-major */
} s2w_features;

/* Frames produced for n samples (0 if shorter than one frame). */
size_t s2w_feat_num_frames(const s2w_feat_config *c, size_t n_samples);

/* `out` is zeroed first, so s2w_features_free is always safe, even on error.
 * Errors: BAD_PARAMS, RATE_MISMATCH, TOO_SHORT, TOO_LARGE, NONFINITE, NOMEM. */
s2w_status s2w_features_extract(const s2w_audio *a, const s2w_feat_config *c,
                                s2w_features *out);
void s2w_features_free(s2w_features *f);

/* Center frequency (Hz) of Mel band `band` for this config (for plotting). */
double s2w_feat_mel_center_hz(const s2w_feat_config *c, int band);

/* Deterministic text format "s2w-features v1": header key=value lines, then
 * blocks "matrix <name> <rows> <cols>" followed by one row per line.
 * which: bit0 logmel raw, bit1 logmel norm, bit2 mfcc raw, bit3 mfcc norm. */
#define S2W_OUT_LOGMEL_RAW  1u
#define S2W_OUT_LOGMEL_NORM 2u
#define S2W_OUT_MFCC_RAW    4u
#define S2W_OUT_MFCC_NORM   8u
#define S2W_OUT_ALL 15u
s2w_status s2w_features_write_text(FILE *fp, const s2w_features *f, unsigned which);

#ifdef __cplusplus
}
#endif
#endif
