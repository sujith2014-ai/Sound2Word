/* s2w_qa: recording quality assessment (does not modify the file).
 * usage: s2w_qa file.wav [--condition clean|noisy]
 * Exit: 0 ok, 1 marginal, 3 reject (re-record), 2 error. Deterministic key=value output.
 * Thresholds are untuned engineering defaults (no real Malayalam speech used yet). */
#include <stdio.h>
#include <string.h>

#include "s2w_qa.h"

int main(int argc, char **argv) {
    if (argc < 2 || (argc != 2 && argc != 4)) { fprintf(stderr, "usage: %s file.wav [--condition clean|noisy]\n", argv[0]); return 2; }
    s2w_qa_limits lim;
    s2w_qa_limits_clean(&lim);
    if (argc == 4) {
        if (strcmp(argv[2], "--condition")) { fprintf(stderr, "error: bad arguments\n"); return 2; }
        if (!strcmp(argv[3], "noisy")) s2w_qa_limits_noisy(&lim);
        else if (strcmp(argv[3], "clean")) { fprintf(stderr, "error: condition must be clean or noisy\n"); return 2; }
    }
    s2w_audio a;
    s2w_status s = s2w_wav_load(argv[1], &a);
    if (s != S2W_OK) { fprintf(stderr, "error: %s\n", s2w_status_str(s)); return 2; }
    s2w_qa q;
    s = s2w_qa_analyse(&a, &lim, &q);
    if (s != S2W_OK) { fprintf(stderr, "error: %s\n", s2w_status_str(s)); s2w_audio_free(&a); return 2; }
    char names[200];
    s2w_qa_flag_names(q.flags, names, sizeof names);
    printf("sample_rate=%d\nchannels=%d\nbits=%d\nduration_s=%.3f\n", a.sample_rate, a.source_channels, a.source_bits, (double)a.n_samples / a.sample_rate);
    printf("noise_floor_dbfs=%.1f\nspeech_level_dbfs=%.1f\nsnr_db_estimate=%.1f\n", q.noise_floor_dbfs, q.speech_level_dbfs, q.snr_db);
    printf("leading_s=%.3f\ntrailing_s=%.3f\nspeech_s=%.3f\n", q.leading_s, q.trailing_s, q.speech_s);
    printf("clipped_run_samples=%zu\nmax_clip_run=%zu\ndropouts=%zu\ndc_offset=%.5f\n", q.clipped_run_samples, q.max_clip_run, q.n_dropouts, q.dc_offset);
    printf("flags=%s\nverdict=%s\n", names, s2w_qa_verdict_name(q.verdict));
    s2w_audio_free(&a);
    return q.verdict == S2W_QA_OK ? 0 : q.verdict == S2W_QA_MARGINAL ? 1 : 3;
}
