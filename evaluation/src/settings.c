#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_eval.h"
#include "s2w_sha256.h"

void s2w_settings_default(s2w_settings *s) {
    memset(s, 0, sizeof *s);
    s2w_feat_config_default(&s->feat);
    s->rep = S2W_REP_MFCC;
    s2w_dtw_config_default(&s->dtw);
    s2w_reject_config_default(&s->rej);
}

static const char *NORM[] = {"none", "mean", "meanvar"};
static const char *REP[] = {"mfcc", "logmel"};

static void canon(char *buf, size_t n, const s2w_settings *s) {
    size_t o = 0;
#define P(...) o += (size_t)snprintf(buf + o, n - o, __VA_ARGS__)
    if (s->protocol) P("protocol=personalised\n");      /* omitted for the default so older frozen settings keep their hash */
    P("norm=%s\nrep=%s\nsample_rate=%d\nframe_ms=%d\nhop_ms=%d\nfft_size=%d\nn_mels=%d\nn_mfcc=%d\n", NORM[s->feat.norm],
      REP[s->rep], s->feat.sample_rate, s->feat.frame_ms, s->feat.hop_ms, s->feat.fft_size, s->feat.n_mels, s->feat.n_mfcc);
    P("f_min_hz=%.9g\nf_max_hz=%.9g\npreemph=%.9g\n", s->feat.f_min_hz, s->feat.f_max_hz, s->feat.preemph);
    P("band_ratio=%.9g\nmin_len_ratio=%.9g\nmax_len_ratio=%.9g\nwarp_penalty=%.9g\n", s->dtw.band_ratio, s->dtw.min_len_ratio,
      s->dtw.max_len_ratio, s->dtw.warp_penalty);
    if (isfinite(s->rej.max_distance)) P("max_distance=%.9g\n", s->rej.max_distance); else P("max_distance=off\n");
    P("min_margin_ratio=%.9g\nquality_mask=%u\ncalibration_source=%s\n", s->rej.min_margin_ratio, s->rej.quality_mask, s->calibration_source);
#undef P
}

void s2w_settings_hash(const s2w_settings *s, char hex[65]) {
    char buf[1024];
    canon(buf, sizeof buf, s);
    s2w_sha256_buf_hex(buf, strlen(buf), hex);
}

void s2w_settings_write(FILE *f, const s2w_settings *s, int with_freeze) {
    char buf[1024];
    canon(buf, sizeof buf, s);
    fputs("#s2w-settings v1\n", f);
    fputs(buf, f);
    if (with_freeze) {
        fprintf(f, "frozen=%s\n", s->frozen ? "yes" : "no");
        if (s->frozen) fprintf(f, "settings_sha256=%s\n", s->settings_sha256);
    }
}

void s2w_settings_freeze(s2w_settings *s) {
    s->frozen = 1;
    s2w_settings_hash(s, s->settings_sha256);
}

static int parse_double(const char *v, double *out) {
    char *e;
    *out = strtod(v, &e);
    return *v && !*e && isfinite(*out);
}
static int parse_int(const char *v, int *out) {
    char *e;
    long x = strtol(v, &e, 10);
    *out = (int)x;
    return *v && !*e && x >= -1000000 && x <= 1000000;
}

s2w_status s2w_settings_load(const char *path, s2w_settings *s, char *err, size_t errlen) {
    s2w_settings_default(s);
    FILE *f = fopen(path, "r");
    if (!f) { snprintf(err, errlen, "cannot open settings file %s", path); return S2W_ERR_IO; }
    char line[512];
    size_t ln = 0;
    int seen_frozen = 0, seen_hash = 0;
    char keys[64][32];
    size_t nkeys = 0;
    if (!fgets(line, sizeof line, f) || strcmp(line, "#s2w-settings v1\n")) {
        fclose(f); snprintf(err, errlen, "line 1: expected '#s2w-settings v1'"); return S2W_ERR_BAD_PARAMS;
    }
    ln = 1;
    while (fgets(line, sizeof line, f)) {
        ln++;
        size_t l = strlen(line);
        if (l && line[l - 1] == '\n') line[--l] = 0;
        if (l && line[l - 1] == '\r') line[--l] = 0;
        if (!l || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) { fclose(f); snprintf(err, errlen, "line %zu: expected key=value", ln); return S2W_ERR_BAD_PARAMS; }
        *eq = 0;
        const char *k = line, *v = eq + 1;
        for (size_t i = 0; i < nkeys; i++)
            if (!strcmp(keys[i], k)) { fclose(f); snprintf(err, errlen, "line %zu: duplicate key '%s'", ln, k); return S2W_ERR_BAD_PARAMS; }
        if (nkeys < 64 && strlen(k) < 32) strcpy(keys[nkeys++], k);
        int ok = 1, iv; double dv;
        if (!strcmp(k, "norm")) { ok = 0; for (int i = 0; i < 3; i++) if (!strcmp(v, NORM[i])) { s->feat.norm = (s2w_norm_mode)i; ok = 1; } }
        else if (!strcmp(k, "rep")) { ok = 0; for (int i = 0; i < 2; i++) if (!strcmp(v, REP[i])) { s->rep = (s2w_rep)i; ok = 1; } }
        else if (!strcmp(k, "sample_rate")) { ok = parse_int(v, &iv); s->feat.sample_rate = iv; }
        else if (!strcmp(k, "frame_ms")) { ok = parse_int(v, &iv); s->feat.frame_ms = iv; }
        else if (!strcmp(k, "hop_ms")) { ok = parse_int(v, &iv); s->feat.hop_ms = iv; }
        else if (!strcmp(k, "fft_size")) { ok = parse_int(v, &iv); s->feat.fft_size = iv; }
        else if (!strcmp(k, "n_mels")) { ok = parse_int(v, &iv); s->feat.n_mels = iv; }
        else if (!strcmp(k, "n_mfcc")) { ok = parse_int(v, &iv); s->feat.n_mfcc = iv; }
        else if (!strcmp(k, "f_min_hz")) { ok = parse_double(v, &dv); s->feat.f_min_hz = dv; }
        else if (!strcmp(k, "f_max_hz")) { ok = parse_double(v, &dv); s->feat.f_max_hz = dv; }
        else if (!strcmp(k, "preemph")) { ok = parse_double(v, &dv); s->feat.preemph = dv; }
        else if (!strcmp(k, "band_ratio")) { ok = parse_double(v, &dv); s->dtw.band_ratio = dv; }
        else if (!strcmp(k, "min_len_ratio")) { ok = parse_double(v, &dv); s->dtw.min_len_ratio = dv; }
        else if (!strcmp(k, "max_len_ratio")) { ok = parse_double(v, &dv); s->dtw.max_len_ratio = dv; }
        else if (!strcmp(k, "warp_penalty")) { ok = parse_double(v, &dv); s->dtw.warp_penalty = dv; }
        else if (!strcmp(k, "max_distance")) { if (!strcmp(v, "off")) s->rej.max_distance = INFINITY; else { ok = parse_double(v, &dv); s->rej.max_distance = dv; } }
        else if (!strcmp(k, "min_margin_ratio")) { ok = parse_double(v, &dv); s->rej.min_margin_ratio = dv; }
        else if (!strcmp(k, "quality_mask")) { ok = parse_int(v, &iv) && iv >= 0; s->rej.quality_mask = (unsigned)iv; }
        else if (!strcmp(k, "protocol")) { ok = 0; if (!strcmp(v, "speaker_independent")) { s->protocol = 0; ok = 1; } else if (!strcmp(v, "personalised")) { s->protocol = 1; ok = 1; } }
        else if (!strcmp(k, "calibration_source")) { ok = strlen(v) < sizeof s->calibration_source; if (ok) strcpy(s->calibration_source, v); }
        else if (!strcmp(k, "frozen")) { seen_frozen = 1; if (!strcmp(v, "yes")) s->frozen = 1; else if (strcmp(v, "no")) ok = 0; }
        else if (!strcmp(k, "settings_sha256")) { seen_hash = 1; ok = strlen(v) == 64; if (ok) strcpy(s->settings_sha256, v); }
        else { fclose(f); snprintf(err, errlen, "line %zu: unknown key '%s'", ln, k); return S2W_ERR_BAD_PARAMS; }
        if (!ok) { fclose(f); snprintf(err, errlen, "line %zu: invalid value for '%s'", ln, k); return S2W_ERR_BAD_PARAMS; }
    }
    fclose(f);
    if (s2w_feat_config_validate(&s->feat) != S2W_OK) { snprintf(err, errlen, "invalid feature settings"); return S2W_ERR_BAD_PARAMS; }
    if (s2w_dtw_config_validate(&s->dtw) != S2W_OK) { snprintf(err, errlen, "invalid DTW settings"); return S2W_ERR_BAD_PARAMS; }
    if (isnan(s->rej.max_distance) || s->rej.max_distance < 0 || s->rej.min_margin_ratio < 0) { snprintf(err, errlen, "invalid rejection settings"); return S2W_ERR_BAD_PARAMS; }
    (void)seen_frozen;
    if (s->frozen) {
        char h[65];
        s2w_settings_hash(s, h);
        if (!seen_hash || strcmp(h, s->settings_sha256)) {
            snprintf(err, errlen, "frozen settings do not match their recorded hash (file edited after freezing)");
            return S2W_ERR_INCOMPATIBLE;
        }
    } else if (seen_hash) {
        snprintf(err, errlen, "settings_sha256 present but frozen is not 'yes'");
        return S2W_ERR_BAD_PARAMS;
    }
    return S2W_OK;
}
