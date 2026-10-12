/* Sound2Word - windowed-sinc resampler and PCM16 WAV writer (additive; the Phase 1 box/linear
 * s2w_audio_to_16k is unchanged so the frozen baseline stays reproducible).
 *
 * s2w_audio_resample: Blackman-windowed sinc, 16 lobes per side, cutoff 0.97 x the lower Nyquist,
 * per-sample gain normalisation (DC gain exactly 1). Stopband roughly -70 dB or better. Direct
 * convolution; O(n x taps). Adopting it inside feature extraction changes features and must be
 * evaluated on real recordings first - it is NOT used by the matcher today.
 */
#ifndef S2W_RESAMPLE_H
#define S2W_RESAMPLE_H
#include "s2w_audio.h"

#ifdef __cplusplus
extern "C" {
#endif

/* target_rate 1000..384000. Same rate -> exact copy. Output length floor(n * target / in_rate). */
s2w_status s2w_audio_resample(const s2w_audio *in, int target_rate, s2w_audio *out);

/* Mono PCM16 WAV (44-byte header). Values are rounded and clamped to [-32768, 32767]; no dither. */
s2w_status s2w_wav_write_pcm16(const char *path, const float *x, size_t n, int rate);
/* Same, into memory (malloc'd; caller frees). */
s2w_status s2w_wav_encode_pcm16(const float *x, size_t n, int rate, uint8_t **buf, size_t *len);

#ifdef __cplusplus
}
#endif
#endif
