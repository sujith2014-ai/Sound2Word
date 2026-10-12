/* Sound2Word - Phase 1: audio foundation.
 * WAV reading, validation and quality statistics. Pure C99, no I/O beyond
 * the file read, no network, deterministic output. */
#ifndef S2W_AUDIO_H
#define S2W_AUDIO_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define S2W_TARGET_RATE 16000

typedef enum {
    S2W_OK = 0,
    S2W_ERR_IO,
    S2W_ERR_NOT_RIFF,
    S2W_ERR_TRUNCATED,
    S2W_ERR_NO_FMT,
    S2W_ERR_NO_DATA,
    S2W_ERR_UNSUPPORTED_FORMAT,
    S2W_ERR_BAD_PARAMS,
    S2W_ERR_EMPTY,
    S2W_ERR_NOMEM,
    S2W_ERR_TOO_LARGE,
    S2W_ERR_TOO_SHORT,       /* Phase 2: shorter than one analysis frame */
    S2W_ERR_RATE_MISMATCH,   /* Phase 2: audio rate != configured rate */
    S2W_ERR_NONFINITE        /* Phase 2: NaN/Inf in samples or features */
} s2w_status;

const char *s2w_status_str(s2w_status s);

/* Decoded audio: mono float samples in [-1, 1]. */
typedef struct {
    float *samples;
    size_t n_samples;
    int sample_rate;
    int source_channels;
    int source_bits;
} s2w_audio;

typedef enum {
    S2W_WARN_NONE        = 0,
    S2W_WARN_CLIPPING    = 1 << 0,
    S2W_WARN_TOO_QUIET   = 1 << 1,
    S2W_WARN_TOO_SHORT   = 1 << 2,
    S2W_WARN_TOO_LONG    = 1 << 3,
    S2W_WARN_MOSTLY_SILENT = 1 << 4,
    S2W_WARN_DC_OFFSET   = 1 << 5,
    S2W_WARN_LOW_RATE    = 1 << 6
} s2w_warning;

typedef struct {
    double duration_s;
    double peak;             /* linear, 0..1 */
    double rms;              /* linear */
    double rms_dbfs;
    double dc_offset;
    size_t clipped_samples;
    double silence_ratio;    /* fraction of 20 ms frames below threshold */
    double leading_silence_s;
    double trailing_silence_s;
    double speech_s;         /* duration between leading/trailing silence */
    unsigned warnings;       /* bitmask of s2w_warning */
} s2w_stats;

/* Decode a WAV held in memory (PCM 8/16/24/32-bit int or 32-bit float;
 * any channel count, downmixed to mono by averaging). Never reads out of
 * bounds; malformed input returns an error status. */
s2w_status s2w_wav_decode(const uint8_t *buf, size_t len, s2w_audio *out);
s2w_status s2w_wav_load(const char *path, s2w_audio *out);
void s2w_audio_free(s2w_audio *a);

/* Resample to 16 kHz (linear interpolation with a box pre-filter when
 * downsampling). Returns a new buffer in `out`; `in` is untouched. */
s2w_status s2w_audio_to_16k(const s2w_audio *in, s2w_audio *out);

/* Compute quality statistics. */
s2w_status s2w_audio_stats(const s2w_audio *a, s2w_stats *st);

#ifdef __cplusplus
}
#endif
#endif
