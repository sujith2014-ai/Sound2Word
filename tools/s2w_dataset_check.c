/* s2w_dataset_check: validate a Sound2Word dataset directory. Never modifies anything.
 * usage: s2w_dataset_check DIR [--require-verified] [--require-participants] [--no-audio]
 *                              [--emit-hashes]   (prints "recording_id,sha256" CSV and exits)
 * Exit: 0 valid (warnings allowed), 1 invalid, 2 usage/IO error. Output is deterministic. */
#include <stdio.h>
#include <string.h>

#include "s2w_dataset.h"

static void print_issues(const s2w_issues *iss) {
    for (size_t i = 0; i < iss->n; i++) {
        const s2w_issue *x = &iss->v[i];
        printf("%s %s", x->is_error ? "ERROR" : "WARN ", x->file);
        if (x->line) printf(":%zu", x->line);
        printf(": %s\n", x->msg);
    }
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s DIR [options]\n", argv[0]); return 2; }
    s2w_dataset_options opt = {0, 0};
    int audio = 1, emit = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--require-verified")) opt.require_verified = 1;
        else if (!strcmp(argv[i], "--require-participants")) opt.require_participants = 1;
        else if (!strcmp(argv[i], "--no-audio")) audio = 0;
        else if (!strcmp(argv[i], "--emit-hashes")) emit = 1;
        else { fprintf(stderr, "error: unknown option %s\n", argv[i]); return 2; }
    }
    s2w_dataset ds;
    s2w_issues iss = {0};
    s2w_status st = s2w_dataset_load(argv[1], &opt, &ds, &iss);
    if (st != S2W_OK) {
        print_issues(&iss);
        printf("result=INVALID errors=%zu warnings=%zu (audio not checked)\n", iss.n_err, iss.n_warn);
        s2w_issues_free(&iss);
        return 1;
    }
    s2w_audio_summary sum = {0};
    if (emit) {
        s2w_issues tmp = {0};
        s2w_dataset_check_audio(&ds, &tmp, &sum, stdout);
        s2w_issues_free(&tmp); s2w_issues_free(&iss); s2w_dataset_free(&ds);
        return 0;
    }
    if (audio) s2w_dataset_check_audio(&ds, &iss, &sum, NULL);
    print_issues(&iss);
    size_t per_split[3] = {0, 0, 0};
    for (size_t i = 0; i < ds.nr; i++) per_split[ds.r[i].split]++;
    printf("dataset_id=%s\ndataset_version=%s\nkind=%s\nmanifest_sha256=%s\n", ds.dataset_id, ds.dataset_version,
           ds.synthetic ? "synthetic" : "real", ds.manifest_sha256);
    printf("words=%zu recordings=%zu (enrol=%zu calib=%zu test=%zu) participants=%zu\n", ds.nw, ds.nr, per_split[0],
           per_split[1], per_split[2], ds.np);
    if (audio)
        printf("audio: checked=%zu decoded=%zu failed=%zu unpinned=%zu hash_mismatch=%zu duplicate=%zu below_16k=%zu\n",
               sum.n_checked, sum.n_decoded, sum.n_decode_fail, sum.n_unpinned, sum.n_hash_mismatch, sum.n_duplicate_audio, sum.n_below_16k);
    else printf("audio: not checked (--no-audio)\n");
    if (ds.synthetic) printf("NOTE: synthetic dataset - software validation only, not Malayalam data\n");
    printf("result=%s errors=%zu warnings=%zu\n", iss.n_err ? "INVALID" : "VALID", iss.n_err, iss.n_warn);
    int rc = iss.n_err ? 1 : 0;
    s2w_issues_free(&iss);
    s2w_dataset_free(&ds);
    return rc;
}
