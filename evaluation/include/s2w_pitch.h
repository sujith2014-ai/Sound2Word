/* Measured pitch (median F0) for pitch-range grouping. Evaluation-only; not used by recognition. */
#ifndef S2W_PITCH_H
#define S2W_PITCH_H
#include "s2w_audio.h"

/* Median F0 (Hz) over voiced 40 ms frames (autocorrelation, 60-450 Hz). Returns S2W_OK, or
 * S2W_ERR_EMPTY when fewer than 8 voiced frames are found (F0 undefined: do not guess). */
s2w_status s2w_median_f0(const s2w_audio *a, double *f0_hz);
#endif
