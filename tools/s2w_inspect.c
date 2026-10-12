/* s2w_inspect: validate a WAV file and print deterministic key=value stats.
 * Exit codes: 0 ok, 1 usable with warnings, 2 error. */
#include <stdio.h>
#include <string.h>

#include "s2w_audio.h"

static const struct { unsigned bit; const char *name; } WARN[] = {
    {S2W_WARN_CLIPPING, "clipping"},       {S2W_WARN_TOO_QUIET, "too_quiet"},
    {S2W_WARN_TOO_SHORT, "too_short"},     {S2W_WARN_TOO_LONG, "too_long"},
    {S2W_WARN_MOSTLY_SILENT, "mostly_silent"}, {S2W_WARN_DC_OFFSET, "dc_offset"},
    {S2W_WARN_LOW_RATE, "low_sample_rate"},
};

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s file.wav\n", argv[0]);
        return 2;
    }
    s2w_audio a;
    s2w_status s = s2w_wav_load(argv[1], &a);
    if (s != S2W_OK) {
        fprintf(stderr, "error: %s\n", s2w_status_str(s));
        return 2;
    }
    s2w_stats st;
    s = s2w_audio_stats(&a, &st);
    if (s != S2W_OK) {
        fprintf(stderr, "error: %s\n", s2w_status_str(s));
        s2w_audio_free(&a);
        return 2;
    }
    printf("sample_rate=%d\nchannels=%d\nbits=%d\nsamples=%zu\n", a.sample_rate,
           a.source_channels, a.source_bits, a.n_samples);
    printf("duration_s=%.3f\npeak=%.4f\nrms_dbfs=%.2f\ndc_offset=%.5f\n", st.duration_s,
           st.peak, st.rms_dbfs, st.dc_offset);
    printf("clipped_samples=%zu\nsilence_ratio=%.3f\nleading_silence_s=%.3f\n"
           "trailing_silence_s=%.3f\nspeech_s=%.3f\n",
           st.clipped_samples, st.silence_ratio, st.leading_silence_s, st.trailing_silence_s,
           st.speech_s);
    printf("warnings=");
    int first = 1;
    for (size_t i = 0; i < sizeof WARN / sizeof *WARN; i++)
        if (st.warnings & WARN[i].bit) { printf("%s%s", first ? "" : ",", WARN[i].name); first = 0; }
    printf("%s\n", first ? "none" : "");
    s2w_audio_free(&a);
    return st.warnings ? 1 : 0;
}
