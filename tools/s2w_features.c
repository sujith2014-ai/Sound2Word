/* s2w_features: dump feature matrices as deterministic text for external plotting.
 * usage: s2w_features file.wav [--norm none|mean|meanvar] [--out all|logmel|mfcc|logmel-raw|...]
 *        [--frame-ms N] [--hop-ms N] [--fft N] [--mels N] [--mfcc N]
 * Exit codes: 0 ok, 2 error. Audio not at the target rate is resampled to 16 kHz. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_features.h"

static int geti(const char *s, int *ok) {
    char *e; long v = strtol(s, &e, 10);
    if (*s == 0 || *e || v < -1000000 || v > 1000000) *ok = 0;
    return (int)v;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s file.wav [options]\n", argv[0]);
        return 2;
    }
    s2w_feat_config c;
    s2w_feat_config_default(&c);
    unsigned which = S2W_OUT_ALL;
    int ok = 1;
    for (int i = 2; i < argc; i += 2) {
        if (i + 1 >= argc) { ok = 0; break; }
        const char *k = argv[i], *v = argv[i + 1];
        if (!strcmp(k, "--norm")) {
            if (!strcmp(v, "none")) c.norm = S2W_NORM_NONE;
            else if (!strcmp(v, "mean")) c.norm = S2W_NORM_MEAN;
            else if (!strcmp(v, "meanvar")) c.norm = S2W_NORM_MEANVAR;
            else ok = 0;
        } else if (!strcmp(k, "--out")) {
            if (!strcmp(v, "all")) which = S2W_OUT_ALL;
            else if (!strcmp(v, "logmel")) which = S2W_OUT_LOGMEL_RAW | S2W_OUT_LOGMEL_NORM;
            else if (!strcmp(v, "mfcc")) which = S2W_OUT_MFCC_RAW | S2W_OUT_MFCC_NORM;
            else if (!strcmp(v, "logmel-raw")) which = S2W_OUT_LOGMEL_RAW;
            else if (!strcmp(v, "logmel-norm")) which = S2W_OUT_LOGMEL_NORM;
            else if (!strcmp(v, "mfcc-raw")) which = S2W_OUT_MFCC_RAW;
            else if (!strcmp(v, "mfcc-norm")) which = S2W_OUT_MFCC_NORM;
            else ok = 0;
        } else if (!strcmp(k, "--frame-ms")) c.frame_ms = geti(v, &ok);
        else if (!strcmp(k, "--hop-ms")) c.hop_ms = geti(v, &ok);
        else if (!strcmp(k, "--fft")) c.fft_size = geti(v, &ok);
        else if (!strcmp(k, "--mels")) c.n_mels = geti(v, &ok);
        else if (!strcmp(k, "--mfcc")) c.n_mfcc = geti(v, &ok);
        else ok = 0;
    }
    if (!ok) { fprintf(stderr, "error: bad arguments\n"); return 2; }

    s2w_audio a, r;
    s2w_status s = s2w_wav_load(argv[1], &a);
    if (s != S2W_OK) { fprintf(stderr, "error: %s\n", s2w_status_str(s)); return 2; }
    if (a.sample_rate != c.sample_rate) {
        s = s2w_audio_to_16k(&a, &r);
        s2w_audio_free(&a);
        if (s != S2W_OK) { fprintf(stderr, "error: %s\n", s2w_status_str(s)); return 2; }
        a = r;
    }
    s2w_features f;
    s = s2w_features_extract(&a, &c, &f);
    s2w_audio_free(&a);
    if (s != S2W_OK) { fprintf(stderr, "error: %s\n", s2w_status_str(s)); return 2; }
    s = s2w_features_write_text(stdout, &f, which);
    s2w_features_free(&f);
    if (s != S2W_OK) { fprintf(stderr, "error: %s\n", s2w_status_str(s)); return 2; }
    return 0;
}
