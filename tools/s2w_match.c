/* s2w_match: rank a query WAV against a template manifest with constrained DTW.
 * usage: s2w_match manifest.txt query.wav [--k N] [--rep mfcc|logmel] [--norm none|mean|meanvar]
 *                  [--max-distance X] [--min-margin X]
 * manifest: one "label path" pair per line (label has no spaces); '#' starts a comment.
 * Output is deterministic key=value text. Scores are distances, not probabilities.
 * Exit: 0 accepted, 1 rejected (unknown/ambiguous/unusable), 2 error. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_dtw.h"

static s2w_status load_features(const char *path, const s2w_feat_config *c, s2w_features *f) {
    s2w_audio a, r;
    s2w_status s = s2w_wav_load(path, &a);
    if (s != S2W_OK) return s;
    if (a.sample_rate != c->sample_rate) {
        s = s2w_audio_to_16k(&a, &r);
        s2w_audio_free(&a);
        if (s != S2W_OK) return s;
        a = r;
    }
    s = s2w_features_extract(&a, c, f);
    s2w_audio_free(&a);
    return s;
}

static const char *REASON[] = {"none", "quality", "no_candidate", "distance", "ambiguous"};

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s manifest.txt query.wav [options]\n", argv[0]); return 2; }
    s2w_feat_config fc; s2w_feat_config_default(&fc);
    s2w_reject_config rc; s2w_reject_config_default(&rc);
    s2w_rep rep = S2W_REP_MFCC;
    int k = 3, ok = 1;
    for (int i = 3; i < argc; i += 2) {
        if (i + 1 >= argc) { ok = 0; break; }
        const char *o = argv[i], *v = argv[i + 1];
        char *e;
        if (!strcmp(o, "--k")) { k = (int)strtol(v, &e, 10); ok &= *v && !*e; }
        else if (!strcmp(o, "--max-distance")) { rc.max_distance = strtod(v, &e); ok &= *v && !*e; }
        else if (!strcmp(o, "--min-margin")) { rc.min_margin_ratio = strtod(v, &e); ok &= *v && !*e; }
        else if (!strcmp(o, "--rep")) {
            if (!strcmp(v, "mfcc")) rep = S2W_REP_MFCC; else if (!strcmp(v, "logmel")) rep = S2W_REP_LOGMEL; else ok = 0;
        } else if (!strcmp(o, "--norm")) {
            if (!strcmp(v, "none")) fc.norm = S2W_NORM_NONE;
            else if (!strcmp(v, "mean")) fc.norm = S2W_NORM_MEAN;
            else if (!strcmp(v, "meanvar")) fc.norm = S2W_NORM_MEANVAR;
            else ok = 0;
        } else ok = 0;
    }
    if (!ok) { fprintf(stderr, "error: bad arguments\n"); return 2; }

    s2w_matcher *m = s2w_matcher_new(rep, NULL, &rc);
    if (!m) { fprintf(stderr, "error: invalid matcher settings\n"); return 2; }
    FILE *fp = fopen(argv[1], "r");
    if (!fp) { fprintf(stderr, "error: cannot open manifest\n"); s2w_matcher_free(m); return 2; }
    char line[4096];
    int lineno = 0, rcode = 2;
    while (fgets(line, sizeof line, fp)) {
        lineno++;
        char *hash = strchr(line, '#');
        if (hash) *hash = 0;
        char label[S2W_MAX_LABEL_BYTES + 2], path[4000];
        if (sscanf(line, "%64s %3999[^\n]", label, path) != 2) continue;
        size_t pl = strlen(path);
        while (pl && (path[pl - 1] == ' ' || path[pl - 1] == '\r' || path[pl - 1] == '\t')) path[--pl] = 0;
        s2w_features f;
        s2w_status s = load_features(path, &fc, &f);
        if (s == S2W_OK) { s = s2w_matcher_add(m, label, &f); s2w_features_free(&f); }
        if (s != S2W_OK) {
            fprintf(stderr, "error: manifest line %d (%s): %s\n", lineno, label, s2w_status_str(s));
            goto out;
        }
    }
    {
        s2w_features q;
        s2w_status s = load_features(argv[2], &fc, &q);
        if (s != S2W_OK) { fprintf(stderr, "error: query: %s\n", s2w_status_str(s)); goto out; }
        s2w_match_result r;
        s = s2w_matcher_match(m, &q, k, &r);
        if (s != S2W_OK) { fprintf(stderr, "error: match: %s\n", s2w_status_str(s)); s2w_features_free(&q); goto out; }
        printf("templates=%zu\ndecision=%s\nreason=%s\nrejection_active=%d\n", s2w_matcher_size(m),
               r.decision == S2W_DECISION_ACCEPT ? "accept" : "reject", REASON[r.reason], r.rejection_active);
        if (!r.rejection_active) printf("note=no rejection thresholds set; distances are uncalibrated scores\n");
        printf("query_quality_warnings=0x%02x\nmargin_ratio=", q.quality.warnings);
        if (isfinite(r.margin_ratio)) printf("%.4f\n", r.margin_ratio); else printf("inf\n");
        for (size_t i = 0; i < r.n_candidates; i++)
            printf("rank%zu=%s %.5f\n", i + 1, r.cand[i].label, r.cand[i].distance);
        s2w_features_free(&q);
        rcode = r.decision == S2W_DECISION_ACCEPT ? 0 : 1;
    }
out:
    fclose(fp);
    s2w_matcher_free(m);
    return rcode;
}
