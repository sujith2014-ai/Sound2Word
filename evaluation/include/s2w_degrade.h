/* Deterministic SIMULATED degradations for software testing (evaluation-only; not used by recognition).
 * These model gain, noise, clipping, band limits, reverberation, rate changes and dropouts on a
 * waveform. They do NOT model real rooms, real microphones or real speakers. Results obtained with them
 * are software validation, never evidence of robustness on real Malayalam speech.
 *
 * SNR definition: ratio of the signal's "active power" to the added noise's mean power, where active
 * power = mean power of 20 ms frames within 25 dB of the loudest frame (so long silences do not
 * dilute the speech-level reference).
 */
#ifndef S2W_DEGRADE_H
#define S2W_DEGRADE_H
#include <stdint.h>

#include "s2w_audio.h"

typedef enum { S2W_NOISE_WHITE = 0, S2W_NOISE_PINK = 1, S2W_NOISE_HUM = 2 } s2w_noise_kind;

double s2w_deg_active_power(const s2w_audio *a);                       /* 0 for silence */
s2w_status s2w_deg_gain_db(s2w_audio *a, double db);                   /* no clipping applied */
s2w_status s2w_deg_clip(s2w_audio *a, double level);                   /* hard clip to +-level, 0 < level <= 1 */
s2w_status s2w_deg_dc(s2w_audio *a, double offset);
s2w_status s2w_deg_add_noise(s2w_audio *a, s2w_noise_kind kind, double snr_db, uint32_t seed, double *achieved_snr_db);
s2w_status s2w_deg_dropout(s2w_audio *a, double start_s, double len_ms);   /* exact zeros */
s2w_status s2w_deg_truncate_head(s2w_audio *a, double ms);             /* removes the first ms (shortens); at least 1 frame must remain */
s2w_status s2w_deg_bandlimit(s2w_audio *a, double lo_hz, double hi_hz);    /* 4th-order Butterworth HP + LP */
s2w_status s2w_deg_reverb(s2w_audio *a, double rt60_s, double drr_db, uint32_t seed);   /* synthetic exponential-decay tail */
s2w_status s2w_deg_speed(s2w_audio *a, double factor);                 /* plays factor x faster: tempo AND pitch scale together */
s2w_status s2w_deg_rate_roundtrip(s2w_audio *a, int rate);             /* resample to `rate` and back (band-limits to rate/2) */

typedef struct { const char *name; const char *description; } s2w_scenario_info;
extern const s2w_scenario_info S2W_SCENARIOS[];
extern const size_t S2W_N_SCENARIOS;
/* Applies a named scenario in place. S2W_ERR_BAD_PARAMS for an unknown name. `*simulated` is set to 0
 * only for the "identity" control. */
s2w_status s2w_scenario_apply(const char *name, s2w_audio *a, uint32_t seed, int *simulated);
#endif
