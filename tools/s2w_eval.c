/* s2w_eval: reproducible evaluation harness (see docs/EVALUATION.md).
 *
 *   s2w_eval run          --dataset DIR --settings FILE --split calib|test [options]
 *   s2w_eval compare-norm --dataset DIR --settings FILE [options]            (calib split only)
 *   s2w_eval calibrate    --dataset DIR --settings FILE --max-far X --out FILE [--impostors oov|oov+wrong] [--force]
 *   s2w_eval freeze       --settings FILE --out FILE [--force]
 *
 * options: --out-dir DIR (default evaluation/runs)  --run-id ID  --created-utc STAMP  --ledger FILE
 *          --allow-test-reuse  --min-group-speakers N  --min-group-queries N
 * The test split is only evaluated with frozen settings and every test evaluation is ledgered.
 * Exit: 0 ok, 2 error. */
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include "s2w_eval.h"
#include "s2w_sha256.h"

typedef struct {
    const char *dataset, *settings, *out, *split, *out_dir, *run_id, *created, *ledger, *impostors;
    double max_far;
    int have_far, force, allow_reuse, min_spk, min_q;
} opts;

static int die(const char *fmt, const char *a) { fprintf(stderr, "error: "); fprintf(stderr, fmt, a); fputc('\n', stderr); return 2; }

static void shell_line(const char *cmd, char *out, size_t n) {
    out[0] = 0;
    FILE *p = popen(cmd, "r");
    if (!p) return;
    if (fgets(out, (int)n, p)) { size_t l = strlen(out); while (l && (out[l - 1] == '\n' || out[l - 1] == '\r')) out[--l] = 0; }
    pclose(p);
}

static int mkdir_p(const char *path) {
    char tmp[1024];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char *p = tmp + 1; *p; p++)
        if (*p == '/') { *p = 0; if (mkdir(tmp, 0755) != 0 && errno != EEXIST) return -1; *p = '/'; }
    return (mkdir(tmp, 0755) != 0 && errno != EEXIST) ? -1 : 0;
}

static int file_exists(const char *p) { struct stat s; return stat(p, &s) == 0; }

static int print_issues_and_check(const s2w_issues *iss, int failed) {
    for (size_t i = 0; i < iss->n; i++) {
        const s2w_issue *x = &iss->v[i];
        if (!x->is_error && !failed) continue; /* warnings are printed only when the load failed */
        fprintf(stderr, "%s %s:%zu: %s\n", x->is_error ? "ERROR" : "WARN ", x->file, x->line, x->msg);
    }
    return failed;
}

static int load_dataset(const opts *o, s2w_dataset *ds) {
    s2w_issues iss = {0};
    s2w_dataset_options dopt = {0, 0};
    s2w_status st = s2w_dataset_load(o->dataset, &dopt, ds, &iss);
    if (st != S2W_OK) {
        print_issues_and_check(&iss, 1);
        fprintf(stderr, "error: dataset is invalid (run s2w_dataset_check for details); refusing to evaluate\n");
        s2w_issues_free(&iss);
        return 2;
    }
    if (iss.n_warn) fprintf(stderr, "note: dataset has %zu warning(s); run s2w_dataset_check to see them\n", iss.n_warn);
    s2w_issues_free(&iss);
    return 0;
}

static void make_ids(const opts *o, const s2w_dataset *ds, const s2w_settings *st, const char *cmd, const char *split, char *run_id, size_t n, char *created, size_t cn,
                     const char *commit) {
    if (o->created) snprintf(created, cn, "%s", o->created);
    else { time_t t = time(NULL); struct tm g; gmtime_r(&t, &g); strftime(created, cn, "%Y-%m-%dT%H:%M:%SZ", &g); }
    if (o->run_id) { snprintf(run_id, n, "%s", o->run_id); return; }
    char h[65], seed[600], stamp[32];
    s2w_settings_hash(st, h);
    snprintf(seed, sizeof seed, "%s|%s|%s|%s|%s|%s|%s", created, ds->manifest_sha256, h, cmd, split, commit, getenv("USER") ? "u" : "");
    s2w_sha256_buf_hex(seed, strlen(seed), h);
    size_t k = 0;
    for (const char *c = created; *c && k < sizeof stamp - 1; c++) if (*c >= '0' && *c <= '9') stamp[k++] = *c; else if (*c == 'T') stamp[k++] = 'T';
    stamp[k] = 0;
    snprintf(run_id, n, "%sZ-%.8s", stamp, h);
}

static FILE *open_out(const char *dir, const char *name) {
    char p[1100];
    snprintf(p, sizeof p, "%s/%s", dir, name);
    FILE *f = fopen(p, "w");
    if (!f) fprintf(stderr, "error: cannot write %s\n", p);
    return f;
}

typedef struct { char run_id[96], created[40], commit[64], describe[128], dir[1024]; int dirty; } runmeta;

static int begin_run(const opts *o, const s2w_dataset *ds, const s2w_settings *st, const char *cmd, const char *split, runmeta *rm) {
    shell_line("git rev-parse HEAD 2>/dev/null", rm->commit, sizeof rm->commit);
    if (!rm->commit[0]) strcpy(rm->commit, "unknown");
    shell_line("git describe --tags --always 2>/dev/null", rm->describe, sizeof rm->describe);
    if (!rm->describe[0]) strcpy(rm->describe, "unknown");
    char dirty[8];
    shell_line("git status --porcelain 2>/dev/null | head -c 1", dirty, sizeof dirty);
    rm->dirty = dirty[0] != 0;
    make_ids(o, ds, st, cmd, split, rm->run_id, sizeof rm->run_id, rm->created, sizeof rm->created, rm->commit);
    snprintf(rm->dir, sizeof rm->dir, "%s/%s", o->out_dir, rm->run_id);
    if (file_exists(rm->dir)) return die("run directory already exists: %s (run ids are never overwritten)", rm->dir);
    if (mkdir_p(rm->dir) != 0) return die("cannot create %s", rm->dir);
    return 0;
}

static s2w_report_ctx ctx_of(const opts *o, const runmeta *rm, const char *cmd, const char *split) {
    s2w_report_ctx c;
    memset(&c, 0, sizeof c);
    c.min_group_speakers = o->min_spk; c.min_group_queries = o->min_q;
    c.run_id = rm->run_id; c.created_utc = rm->created; c.command = cmd; c.split_name = split;
    c.git_commit = rm->commit; c.git_dirty = rm->dirty; c.git_describe = rm->describe;
    c.test_prev = c.test_prev_other = -1;
    return c;
}

/* ledger: "run_id\tmanifest\tsettings\tcreated\tcommand" */
static void ledger_scan(const char *path, const char *manifest, const char *shash, int *prev, int *other) {
    *prev = *other = 0;
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[1024];
    while (fgets(line, sizeof line, f)) {
        char *t1 = strchr(line, '\t');
        if (!t1) continue;
        char *t2 = strchr(t1 + 1, '\t');
        if (!t2) continue;
        char *t3 = strchr(t2 + 1, '\t');
        if (!t3) continue;
        if ((size_t)(t2 - t1 - 1) == strlen(manifest) && !strncmp(t1 + 1, manifest, strlen(manifest))) {
            (*prev)++;
            if (strncmp(t2 + 1, shash, 64)) (*other)++;
        }
    }
    fclose(f);
}

static void write_run_files(const runmeta *rm, const s2w_dataset *ds, const s2w_settings *st, const s2w_run *r, const s2w_report_ctx *c, int summary_to_stdout) {
    FILE *f = open_out(rm->dir, "result.json");
    if (f) { s2w_eval_write_json(f, ds, st, r, c); fclose(f); }
    f = open_out(rm->dir, "summary.txt");
    if (f) { s2w_eval_write_summary(f, ds, st, r, c); fclose(f); }
    f = open_out(rm->dir, "settings.cfg");
    if (f) { s2w_settings_write(f, st, 1); fclose(f); }
    f = open_out(rm->dir, "queries.csv");
    if (f) { s2w_eval_write_queries_csv(f, ds, r); fclose(f); }
    if (summary_to_stdout) s2w_eval_write_summary(stdout, ds, st, r, c);
    printf("run_dir=%s\n", rm->dir);
}

static int cmd_run(const opts *o) {
    if (!o->dataset || !o->settings || !o->split) return die("run needs --dataset, --settings, --split%s", "");
    int split = !strcmp(o->split, "calib") ? S2W_SPLIT_CALIB : !strcmp(o->split, "test") ? S2W_SPLIT_TEST : -1;
    if (split < 0) return die("--split must be calib or test (got '%s')", o->split);
    s2w_settings st;
    char err[300];
    if (s2w_settings_load(o->settings, &st, err, sizeof err) != S2W_OK) return die("%s", err);
    if (split == S2W_SPLIT_TEST) {
        if (!st.frozen) return die("test split is LOCKED: settings are not frozen (use `s2w_eval freeze` after tuning on calib)%s", "");
        if ((isfinite(st.rej.max_distance) || st.rej.min_margin_ratio > 0) && !st.calibration_source[0])
            return die("frozen settings use rejection thresholds but record no calibration_source%s", "");
    }
    s2w_dataset ds;
    if (load_dataset(o, &ds)) return 2;
    char ledger[1100], sh[65];
    snprintf(ledger, sizeof ledger, "%s", o->ledger ? o->ledger : "evaluation/test_ledger.txt");
    int prev = 0, other = 0;
    s2w_settings_hash(&st, sh);
    if (split == S2W_SPLIT_TEST) {
        ledger_scan(ledger, ds.manifest_sha256, sh, &prev, &other);
        if (other > 0 && !o->allow_reuse) {
            s2w_dataset_free(&ds);
            return die("test split already evaluated with %s different settings: refusing (test-set tuning). Use --allow-test-reuse to override; it will be flagged", other == 1 ? "1" : "other");
        }
    }
    s2w_run run;
    s2w_status s = s2w_eval_collect(&ds, &st, split, &run, err, sizeof err);
    if (s != S2W_OK) { s2w_dataset_free(&ds); return die("%s", err); }
    runmeta rm;
    if (begin_run(o, &ds, &st, "run", o->split, &rm)) { s2w_run_free(&run); s2w_dataset_free(&ds); return 2; }
    s2w_report_ctx c = ctx_of(o, &rm, "run", o->split);
    if (split == S2W_SPLIT_TEST) { c.test_prev = prev; c.test_prev_other = other; c.test_reuse_ack = other > 0 && o->allow_reuse; }
    write_run_files(&rm, &ds, &st, &run, &c, 1);
    if (split == S2W_SPLIT_TEST) {
        char *slash = strrchr(ledger, '/');
        if (slash) { *slash = 0; mkdir_p(ledger); *slash = '/'; }
        FILE *lf = fopen(ledger, "a");
        if (lf) { fprintf(lf, "%s\t%s\t%s\t%s\trun\n", rm.run_id, ds.manifest_sha256, sh, rm.created); fclose(lf); }
        else fprintf(stderr, "warning: could not append to test ledger %s\n", ledger);
    }
    s2w_run_free(&run);
    s2w_dataset_free(&ds);
    return 0;
}

static int cmd_compare(const opts *o) {
    if (!o->dataset || !o->settings) return die("compare-norm needs --dataset and --settings%s", "");
    s2w_settings st;
    char err[300];
    if (s2w_settings_load(o->settings, &st, err, sizeof err) != S2W_OK) return die("%s", err);
    s2w_dataset ds;
    if (load_dataset(o, &ds)) return 2;
    s2w_norm_mode norms[3] = {S2W_NORM_NONE, S2W_NORM_MEAN, S2W_NORM_MEANVAR};
    s2w_run runs[3];
    memset(runs, 0, sizeof runs);
    for (int i = 0; i < 3; i++) {
        s2w_settings v = st;
        v.feat.norm = norms[i];
        v.rej.max_distance = INFINITY; v.rej.min_margin_ratio = 0;
        s2w_status s = s2w_eval_collect(&ds, &v, S2W_SPLIT_CALIB, &runs[i], err, sizeof err);
        if (s != S2W_OK) { for (int k = 0; k < i; k++) s2w_run_free(&runs[k]); s2w_dataset_free(&ds); return die("%s", err); }
    }
    runmeta rm;
    if (begin_run(o, &ds, &st, "compare-norm", "calib", &rm)) { for (int k = 0; k < 3; k++) s2w_run_free(&runs[k]); s2w_dataset_free(&ds); return 2; }
    s2w_report_ctx c = ctx_of(o, &rm, "compare-norm", "calib");
    FILE *f = open_out(rm.dir, "result.json");
    if (f) { s2w_eval_write_compare_json(f, &ds, &st, runs, norms, 3, &c); fclose(f); }
    f = open_out(rm.dir, "settings.cfg");
    if (f) { s2w_settings_write(f, &st, 1); fclose(f); }
    for (int i = 0; i < 3; i++) {
        s2w_metrics m;
        s2w_eval_metrics(&ds, &runs[i], NULL, &m);
        printf("norm=%s top1=%zu/%zu top3=%zu/%zu top5=%zu/%zu\n", (const char *[]){"none", "mean", "meanvar"}[norms[i]], m.top[0].num, m.top[0].den,
               m.top[1].num, m.top[1].den, m.top[2].num, m.top[2].den);
    }
    if (ds.synthetic) printf("*** SYNTHETIC FIXTURE RUN: software validation only. NOT Malayalam recognition accuracy. ***\n");
    printf("run_dir=%s\n", rm.dir);
    for (int k = 0; k < 3; k++) s2w_run_free(&runs[k]);
    s2w_dataset_free(&ds);
    return 0;
}

static int cmd_calibrate(const opts *o) {
    if (!o->dataset || !o->settings || !o->out || !o->have_far) return die("calibrate needs --dataset, --settings, --max-far, --out%s", "");
    if (file_exists(o->out) && !o->force) return die("output settings file exists: %s (use --force to replace)", o->out);
    s2w_settings st;
    char err[300];
    if (s2w_settings_load(o->settings, &st, err, sizeof err) != S2W_OK) return die("%s", err);
    if (st.frozen) return die("input settings are already frozen; calibration produces new unfrozen settings from an unfrozen file%s", "");
    int wrong = o->impostors && !strcmp(o->impostors, "oov+wrong");
    if (o->impostors && !wrong && strcmp(o->impostors, "oov")) return die("--impostors must be oov or oov+wrong (got '%s')", o->impostors);
    s2w_dataset ds;
    if (load_dataset(o, &ds)) return 2;
    s2w_settings off = st;
    off.rej.max_distance = INFINITY; off.rej.min_margin_ratio = 0;
    s2w_run run;
    s2w_status s = s2w_eval_collect(&ds, &off, S2W_SPLIT_CALIB, &run, err, sizeof err);
    if (s != S2W_OK) { s2w_dataset_free(&ds); return die("%s", err); }
    s2w_calib_report cr;
    s = s2w_eval_calibrate(&ds, &run, o->max_far, wrong, &cr);
    if (s != S2W_OK) {
        s2w_run_free(&run); s2w_dataset_free(&ds);
        return die(s == S2W_ERR_EMPTY ? "calibration needs usable genuine AND impostor queries in the calib split (unknown/OOV words required)%s" : "calibration failed%s", "");
    }
    s2w_settings out = st;
    out.rej.max_distance = cr.cal.threshold;
    out.frozen = 0; out.settings_sha256[0] = 0;
    snprintf(out.calibration_source, sizeof out.calibration_source, "calib:%s@%s:%.12s:far<=%g:%s:n_imp=%zu", ds.dataset_id, ds.dataset_version, ds.manifest_sha256,
             o->max_far, wrong ? "oov+wrong" : "oov", cr.n_impostor_oov + cr.n_impostor_wrong);
    runmeta rm;
    if (begin_run(o, &ds, &st, "calibrate", "calib", &rm)) { s2w_run_free(&run); s2w_dataset_free(&ds); return 2; }
    s2w_report_ctx c = ctx_of(o, &rm, "calibrate", "calib");
    FILE *f = open_out(rm.dir, "result.json");
    if (f) { s2w_eval_write_calibration_json(f, &ds, &st, &out, &run, &cr, &c); fclose(f); }
    f = fopen(o->out, "w");
    if (!f) { s2w_run_free(&run); s2w_dataset_free(&ds); return die("cannot write %s", o->out); }
    s2w_settings_write(f, &out, 1);
    fclose(f);
    f = open_out(rm.dir, "settings_out.cfg");
    if (f) { s2w_settings_write(f, &out, 1); fclose(f); }
    printf("max_distance=%.6f\nin_sample_far=%.4f (n_impostor=%zu)\nin_sample_frr=%.4f (n_genuine=%zu)\nsettings_written=%s (NOT frozen)\nrun_dir=%s\n", cr.cal.threshold,
           cr.cal.far, cr.n_impostor_oov + cr.n_impostor_wrong, cr.cal.frr, cr.n_genuine, o->out, rm.dir);
    if (ds.synthetic) printf("*** SYNTHETIC FIXTURE RUN: software validation only. NOT Malayalam recognition accuracy. ***\n");
    s2w_run_free(&run);
    s2w_dataset_free(&ds);
    return 0;
}

static int cmd_freeze(const opts *o) {
    if (!o->settings || !o->out) return die("freeze needs --settings and --out%s", "");
    if (file_exists(o->out) && !o->force) return die("output exists: %s (use --force)", o->out);
    s2w_settings st;
    char err[300];
    if (s2w_settings_load(o->settings, &st, err, sizeof err) != S2W_OK) return die("%s", err);
    if (st.frozen) return die("settings are already frozen%s", "");
    if ((isfinite(st.rej.max_distance) || st.rej.min_margin_ratio > 0) && !st.calibration_source[0])
        return die("rejection thresholds are set but calibration_source is empty: calibrate (or document the source) before freezing%s", "");
    s2w_settings_freeze(&st);
    FILE *f = fopen(o->out, "w");
    if (!f) return die("cannot write %s", o->out);
    s2w_settings_write(f, &st, 1);
    fclose(f);
    printf("frozen settings written to %s\nsettings_sha256=%s\n", o->out, st.settings_sha256);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s run|compare-norm|calibrate|freeze [options]\n", argv[0]); return 2; }
    opts o;
    memset(&o, 0, sizeof o);
    o.out_dir = "evaluation/runs"; o.min_spk = 3; o.min_q = 30;
    for (int i = 2; i < argc; i++) {
        const char *k = argv[i];
        if (!strcmp(k, "--force")) { o.force = 1; continue; }
        if (!strcmp(k, "--allow-test-reuse")) { o.allow_reuse = 1; continue; }
        if (i + 1 >= argc) return die("option %s needs a value", k);
        const char *v = argv[++i];
        char *e;
        if (!strcmp(k, "--dataset")) o.dataset = v;
        else if (!strcmp(k, "--settings")) o.settings = v;
        else if (!strcmp(k, "--out")) o.out = v;
        else if (!strcmp(k, "--split")) o.split = v;
        else if (!strcmp(k, "--out-dir")) o.out_dir = v;
        else if (!strcmp(k, "--run-id")) o.run_id = v;
        else if (!strcmp(k, "--created-utc")) o.created = v;
        else if (!strcmp(k, "--ledger")) o.ledger = v;
        else if (!strcmp(k, "--impostors")) o.impostors = v;
        else if (!strcmp(k, "--max-far")) { o.max_far = strtod(v, &e); if (!*v || *e) return die("bad --max-far '%s'", v); o.have_far = 1; }
        else if (!strcmp(k, "--min-group-speakers")) { o.min_spk = (int)strtol(v, &e, 10); if (!*v || *e || o.min_spk < 1) return die("bad value '%s'", v); }
        else if (!strcmp(k, "--min-group-queries")) { o.min_q = (int)strtol(v, &e, 10); if (!*v || *e || o.min_q < 1) return die("bad value '%s'", v); }
        else return die("unknown option %s", k);
    }
    if (o.run_id) {
        for (const char *c = o.run_id; *c; c++)
            if (!((*c >= '0' && *c <= '9') || (*c >= 'A' && *c <= 'Z') || (*c >= 'a' && *c <= 'z') || *c == '-' || *c == '_')) return die("invalid --run-id '%s'", o.run_id);
    }
    if (!strcmp(argv[1], "run")) return cmd_run(&o);
    if (!strcmp(argv[1], "compare-norm")) return cmd_compare(&o);
    if (!strcmp(argv[1], "calibrate")) return cmd_calibrate(&o);
    if (!strcmp(argv[1], "freeze")) return cmd_freeze(&o);
    return die("unknown command '%s'", argv[1]);
}
