/* s2w_degrade: derive a SIMULATED-condition dataset from an existing dataset (software testing only).
 *
 *   s2w_degrade --list
 *   s2w_degrade --dataset DIR --out-dir OUT --scenario NAME [--seed N]
 *
 * - enrol recordings are copied byte-for-byte (templates stay clean);
 * - calib recordings are degraded with the named scenario and written as PCM16 WAV;
 * - TEST-split recordings are NOT read and NOT copied (simulation never touches the locked test set);
 * - affected rows get environment=noisy, noise_condition="sim:<scenario>", fresh audio_sha256 pins, and the
 *   dataset_id becomes "<id>.sim-<scenario>", so simulated results are identifiable in the data itself.
 * The output dataset keeps kind=synthetic/real from the source: simulated degradation of real speech is
 * still simulated degradation. Simulations do not establish robustness on real speakers or rooms. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "s2w_dataset.h"
#include "s2w_degrade.h"
#include "s2w_resample.h"
#include "s2w_sha256.h"

static int die(const char *fmt, const char *a) { fprintf(stderr, "error: "); fprintf(stderr, fmt, a); fputc('\n', stderr); return 2; }

static int mkdir_p(const char *path) {
    char tmp[1024];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char *p = tmp + 1; *p; p++)
        if (*p == '/') { *p = 0; if (mkdir(tmp, 0755) != 0 && errno != EEXIST) return -1; *p = '/'; }
    return (mkdir(tmp, 0755) != 0 && errno != EEXIST) ? -1 : 0;
}
static int mkparent(const char *file) {
    char tmp[1024];
    if (strlen(file) >= sizeof tmp) return -1;
    memcpy(tmp, file, strlen(file) + 1);
    char *s = strrchr(tmp, '/');
    if (!s) return 0;
    *s = 0;
    return mkdir_p(tmp);
}
static int copy_file(const char *from, const char *to) {
    FILE *a = fopen(from, "rb");
    if (!a) return -1;
    FILE *b = fopen(to, "wb");
    if (!b) { fclose(a); return -1; }
    char buf[65536];
    size_t n;
    int rc = 0;
    while ((n = fread(buf, 1, sizeof buf, a)) > 0) if (fwrite(buf, 1, n, b) != n) { rc = -1; break; }
    if (ferror(a)) rc = -1;
    fclose(a);
    if (fclose(b) != 0) rc = -1;
    return rc;
}
static uint32_t fnv(const char *s, uint32_t seed) {
    uint32_t h = 2166136261u ^ seed;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 16777619u; }
    return h ? h : 1u;
}

int main(int argc, char **argv) {
    const char *ds_dir = NULL, *out = NULL, *scn = NULL;
    uint32_t seed = 1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--list")) {
            for (size_t k = 0; k < S2W_N_SCENARIOS; k++) printf("%-16s %s\n", S2W_SCENARIOS[k].name, S2W_SCENARIOS[k].description);
            return 0;
        }
        if (i + 1 >= argc) return die("option %s needs a value", argv[i]);
        const char *v = argv[i + 1];
        if (!strcmp(argv[i], "--dataset")) ds_dir = v;
        else if (!strcmp(argv[i], "--out-dir")) out = v;
        else if (!strcmp(argv[i], "--scenario")) scn = v;
        else if (!strcmp(argv[i], "--seed")) { char *e; seed = (uint32_t)strtoul(v, &e, 10); if (!*v || *e) return die("bad --seed '%s'", v); }
        else return die("unknown option %s", argv[i]);
        i++;
    }
    if (!ds_dir || !out || !scn) return die("need --dataset, --out-dir, --scenario (or --list)%s", "");
    int known = 0;
    for (size_t k = 0; k < S2W_N_SCENARIOS; k++) known |= !strcmp(S2W_SCENARIOS[k].name, scn);
    if (!known) return die("unknown scenario '%s' (see --list)", scn);
    struct stat st;
    if (stat(out, &st) == 0) return die("output directory already exists: %s (never overwritten)", out);

    s2w_dataset ds;
    s2w_issues iss = {0};
    s2w_dataset_options opt = {0, 0};
    if (s2w_dataset_load(ds_dir, &opt, &ds, &iss) != S2W_OK) {
        for (size_t i = 0; i < iss.n; i++) if (iss.v[i].is_error) fprintf(stderr, "ERROR %s:%zu: %s\n", iss.v[i].file, iss.v[i].line, iss.v[i].msg);
        s2w_issues_free(&iss);
        return die("source dataset is invalid; refusing to derive from it%s", "");
    }
    s2w_issues_free(&iss);
    char new_id[S2W_ID_MAX + 40];
    snprintf(new_id, sizeof new_id, "%s.sim-%s", ds.dataset_id, scn);
    if (strlen(new_id) >= S2W_ID_MAX) { s2w_dataset_free(&ds); return die("derived dataset id too long: %s", new_id); }
    char cond[S2W_ID_MAX + 8];
    snprintf(cond, sizeof cond, "sim:%s", scn);
    if (strlen(cond) >= S2W_ID_MAX) { s2w_dataset_free(&ds); return die("scenario name too long: %s", scn); }
    if (mkdir_p(out) != 0) { s2w_dataset_free(&ds); return die("cannot create %s", out); }

    char path[1100], src[1100];
    snprintf(path, sizeof path, "%s/dataset_meta.txt", out);
    FILE *f = fopen(path, "w");
    if (!f) { s2w_dataset_free(&ds); return die("cannot write %s", path); }
    fprintf(f, "schema_version=1\nkind=%s\ndataset_id=%s\ndataset_version=%s\nprotocol=speaker_independent\n", ds.synthetic ? "synthetic" : "real", new_id, ds.dataset_version);
    fclose(f);
    static const char *copy[] = {"wordlist.csv", "participants.csv"};
    for (int k = 0; k < 2; k++) {
        snprintf(src, sizeof src, "%s/%s", ds_dir, copy[k]);
        snprintf(path, sizeof path, "%s/%s", out, copy[k]);
        FILE *t = fopen(src, "rb");
        if (!t) continue;                       /* participants.csv is optional */
        fclose(t);
        if (copy_file(src, path) != 0) { s2w_dataset_free(&ds); return die("cannot copy %s", src); }
    }
    snprintf(path, sizeof path, "%s/recordings.csv", out);
    FILE *rf = fopen(path, "w");
    if (!rf) { s2w_dataset_free(&ds); return die("cannot write %s", path); }
    fprintf(rf, "#s2w-recordings v1\nrecording_id,path,word_id,speaker_id,session_id,repetition,split,device_id,environment,noise_condition,speaking_style,label_verified,audio_sha256\n");
    size_t n_enrol = 0, n_deg = 0, n_skipped_test = 0;
    for (size_t i = 0; i < ds.nr; i++) {
        const s2w_rec *r = &ds.r[i];
        if (r->split == S2W_SPLIT_TEST) { n_skipped_test++; continue; }
        char from[1100], to[1100], hex[65];
        s2w_dataset_audio_path(&ds, r, from, sizeof from);
        snprintf(to, sizeof to, "%s/%s", out, r->path);
        if (mkparent(to) != 0) { fclose(rf); s2w_dataset_free(&ds); return die("cannot create directory for %s", to); }
        int simulated = 0;
        if (r->split == S2W_SPLIT_ENROL) {
            if (copy_file(from, to) != 0) { fclose(rf); s2w_dataset_free(&ds); return die("cannot copy %s", from); }
            n_enrol++;
        } else {
            s2w_audio a;
            s2w_status s = s2w_wav_load(from, &a);
            if (s != S2W_OK) { fclose(rf); s2w_dataset_free(&ds); return die("cannot read %s", r->id); }
            s = s2w_scenario_apply(scn, &a, fnv(r->id, seed), &simulated);
            if (s == S2W_OK) s = s2w_wav_write_pcm16(to, a.samples, a.n_samples, a.sample_rate);
            s2w_audio_free(&a);
            if (s != S2W_OK) { fclose(rf); s2w_dataset_free(&ds); fprintf(stderr, "error: scenario failed on %s: %s\n", r->id, s2w_status_str(s)); return 2; }
            n_deg++;
        }
        s2w_sha256_file_hex(to, hex);
        int mark = simulated;                    /* identity control keeps the original labels */
        fprintf(rf, "%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s,%s\n", r->id, r->path, ds.w[r->word].id, r->speaker, r->session, r->rep, S2W_SPLIT_NAME[r->split],
                r->device, mark ? "noisy" : (r->noisy ? "noisy" : "clean"), mark ? cond : r->noise_condition, r->style, r->label_verified ? "yes" : "no", hex);
    }
    fclose(rf);
    printf("scenario=%s\nderived_dataset_id=%s\nenrol_copied=%zu\ncalib_degraded=%zu\ntest_recordings_skipped_unread=%zu\n", scn, new_id, n_enrol, n_deg, n_skipped_test);
    printf("SIMULATED conditions: software validation only; not evidence about real speakers, rooms or microphones\n");
    s2w_dataset_free(&ds);
    /* self-check: the derived dataset must pass structural validation */
    s2w_dataset chk;
    s2w_issues ci = {0};
    s2w_status vs = s2w_dataset_load(out, &opt, &chk, &ci);
    if (vs != S2W_OK) {
        for (size_t i = 0; i < ci.n; i++) if (ci.v[i].is_error) fprintf(stderr, "ERROR %s:%zu: %s\n", ci.v[i].file, ci.v[i].line, ci.v[i].msg);
        s2w_issues_free(&ci);
        return die("derived dataset failed validation%s", "");
    }
    s2w_dataset_free(&chk);
    s2w_issues_free(&ci);
    return 0;
}
