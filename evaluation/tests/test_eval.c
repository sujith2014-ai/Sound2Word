#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_eval.h"
#include "s2w_pitch.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static char BUILD[200], FX[220], TMP[220];

static int sh(const char *fmt, ...) {
    char cmd[3000];
    va_list ap; va_start(ap, fmt); vsnprintf(cmd, sizeof cmd, fmt, ap); va_end(ap);
    return system(cmd);
}
static char *slurp(const char *path) {
    FILE *f = fopen(path, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *b = malloc((size_t)n + 1); if (fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); fclose(f); return NULL; }
    b[n] = 0; fclose(f); return b;
}
static void spit(const char *path, const char *s) { FILE *f = fopen(path, "w"); fputs(s, f); fclose(f); }

static void test_settings(void) {
    s2w_settings s, t;
    char err[300], path[300], h1[65], h2[65];
    s2w_settings_default(&s);
    s2w_settings_hash(&s, h1);
    s2w_settings_hash(&s, h2);
    CHECK(!strcmp(h1, h2));
    t = s; t.dtw.band_ratio = 0.31; s2w_settings_hash(&t, h2); CHECK(strcmp(h1, h2) != 0);
    t = s; t.rej.max_distance = 3.0; s2w_settings_hash(&t, h2); CHECK(strcmp(h1, h2) != 0);

    snprintf(path, sizeof path, "%s_s.cfg", TMP);
    FILE *f = fopen(path, "w"); s2w_settings_write(f, &s, 1); fclose(f);
    CHECK(s2w_settings_load(path, &t, err, sizeof err) == S2W_OK && !t.frozen && !isfinite(t.rej.max_distance));
    CHECK(t.feat.norm == s.feat.norm && t.dtw.warp_penalty == s.dtw.warp_penalty);

    /* freeze, round trip, then tamper */
    t.rej.max_distance = 2.5; snprintf(t.calibration_source, sizeof t.calibration_source, "calib:test");
    s2w_settings_freeze(&t);
    CHECK(t.frozen && strlen(t.settings_sha256) == 64);
    f = fopen(path, "w"); s2w_settings_write(f, &t, 1); fclose(f);
    CHECK(s2w_settings_load(path, &s, err, sizeof err) == S2W_OK && s.frozen && s.rej.max_distance == 2.5);
    char *txt = slurp(path);
    char *at = strstr(txt, "max_distance=2.5"); at[15] = '9';           /* 2.5 -> 2.9 */
    spit(path, txt); free(txt);
    CHECK(s2w_settings_load(path, &s, err, sizeof err) == S2W_ERR_INCOMPATIBLE && strstr(err, "edited after freezing"));
    /* frozen=yes without hash, hash without frozen */
    spit(path, "#s2w-settings v1\nfrozen=yes\n");
    CHECK(s2w_settings_load(path, &s, err, sizeof err) != S2W_OK);
    spit(path, "#s2w-settings v1\nsettings_sha256=0000000000000000000000000000000000000000000000000000000000000000\n");
    CHECK(s2w_settings_load(path, &s, err, sizeof err) != S2W_OK);

    struct { const char *txt, *expect; } bad[] = {
        {"nope\n", "expected '#s2w-settings v1'"}, {"#s2w-settings v1\nbogus=1\n", "unknown key"},
        {"#s2w-settings v1\nnorm=mean\nnorm=none\n", "duplicate key"}, {"#s2w-settings v1\nnorm=loud\n", "invalid value"},
        {"#s2w-settings v1\nband_ratio=abc\n", "invalid value"}, {"#s2w-settings v1\nband_ratio=2\n", "invalid DTW"},
        {"#s2w-settings v1\nfft_size=500\n", "invalid feature"}, {"#s2w-settings v1\nmax_distance=-1\n", "invalid rejection"},
        {"#s2w-settings v1\nline without equals\n", "key=value"},
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) {
        spit(path, bad[i].txt);
        err[0] = 0;
        CHECK(s2w_settings_load(path, &s, err, sizeof err) != S2W_OK && strstr(err, bad[i].expect));
    }
    CHECK(s2w_settings_load("/nonexistent.cfg", &s, err, sizeof err) == S2W_ERR_IO);
}

static void test_decide(void) {
    s2w_query q; memset(&q, 0, sizeof q);
    q.usable = 1; q.n_cand = 2; q.cand_dist[0] = 1.0; q.cand_dist[1] = 1.5; q.margin = 0.5;
    s2w_reject_config rc; s2w_reject_config_default(&rc);
    int d, r;
    s2w_eval_decide(&q, &rc, &d, &r); CHECK(d == S2W_DECISION_ACCEPT && r == S2W_REJECT_NONE);
    rc.max_distance = 0.9; s2w_eval_decide(&q, &rc, &d, &r); CHECK(d == S2W_DECISION_REJECT && r == S2W_REJECT_DISTANCE);
    rc.max_distance = 1.0; s2w_eval_decide(&q, &rc, &d, &r); CHECK(d == S2W_DECISION_ACCEPT);              /* boundary: <= accepts */
    rc.min_margin_ratio = 0.6; s2w_eval_decide(&q, &rc, &d, &r); CHECK(r == S2W_REJECT_AMBIGUOUS);
    q.margin = INFINITY; s2w_eval_decide(&q, &rc, &d, &r); CHECK(d == S2W_DECISION_ACCEPT);                  /* single label */
    q.quality = S2W_WARN_TOO_SHORT; s2w_eval_decide(&q, &rc, &d, &r); CHECK(r == S2W_REJECT_QUALITY);
    q.quality = 0; q.n_cand = 0; s2w_eval_decide(&q, &rc, &d, &r); CHECK(r == S2W_REJECT_NO_CANDIDATE);
    q.usable = 0; s2w_eval_decide(&q, &rc, &d, &r); CHECK(r == S2W_REASON_EXTRACT_FAIL && d == S2W_DECISION_REJECT);
}

static void test_pitch(void) {
    double f0;
    s2w_audio a = {0};
    a.n_samples = 16000; a.sample_rate = 16000; a.samples = calloc(a.n_samples, sizeof(float));
    for (double target = 100; target <= 300; target += 100) {
        for (size_t i = 0; i < a.n_samples; i++) {
            double t = (double)i / 16000.0, v = 0;
            for (int h = 1; h <= 5; h++) v += sin(2 * M_PI * target * h * t) / h;
            a.samples[i] = (float)(0.2 * v);
        }
        CHECK(s2w_median_f0(&a, &f0) == S2W_OK && fabs(f0 - target) < 0.05 * target);
    }
    memset(a.samples, 0, a.n_samples * sizeof(float));
    CHECK(s2w_median_f0(&a, &f0) == S2W_ERR_EMPTY);                    /* silence: undefined, not guessed */
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] = (float)(0.1 * ((double)rand() / RAND_MAX - 0.5));
    CHECK(s2w_median_f0(&a, &f0) == S2W_ERR_EMPTY);                    /* noise: no pitch */
    CHECK(s2w_median_f0(NULL, &f0) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&a);
}

static void test_collect_and_metrics(void) {
    s2w_dataset ds; s2w_issues iss = {0};
    CHECK(s2w_dataset_load(FX, NULL, &ds, &iss) == S2W_OK);
    s2w_settings st; s2w_settings_default(&st);
    s2w_run run; char err[300];

    CHECK(s2w_eval_collect(&ds, &st, S2W_SPLIT_ENROL, &run, err, sizeof err) == S2W_ERR_BAD_PARAMS); /* enrol is never a query split */
    CHECK(s2w_eval_collect(&ds, &st, S2W_SPLIT_CALIB, &run, err, sizeof err) == S2W_OK);
    CHECK(run.n_templates == 96 && run.nq == 99 && run.split == S2W_SPLIT_CALIB);
    s2w_metrics m;
    s2w_eval_metrics(&ds, &run, NULL, &m);
    CHECK(m.n_queries == 99 && m.n_known == 72 && m.n_unknown == 27 && m.n_unusable == 0);
    CHECK(m.top[0].den == 72 && m.top[0].num <= m.top[1].num && m.top[1].num <= m.top[2].num && m.top[2].num <= 72);
    CHECK(m.top[0].num >= 60);                          /* sanity: separable tone words */
    /* rejection off: every known accepted, every unknown falsely accepted */
    CHECK(m.known_false_reject.num == 0 && m.unknown_false_accept.num == 27 && m.unknown_correct_reject.num == 0);
    CHECK(m.known_accepted_correct.num + m.known_accepted_wrong.num == 72);
    CHECK(m.known_precision_accepted.den == 72);

    /* masks: metrics over a subset have their own denominators */
    unsigned char *mask = calloc(run.nq, 1);
    for (size_t i = 0; i < run.nq; i++) mask[i] = run.q[i].known;
    s2w_metrics mk; s2w_eval_metrics(&ds, &run, mask, &mk);
    CHECK(mk.n_queries == 72 && mk.n_unknown == 0 && mk.unknown_false_accept.den == 0);   /* undefined, not 0% or 100% */
    memset(mask, 0, run.nq);
    s2w_metrics mz; s2w_eval_metrics(&ds, &run, mask, &mz);
    CHECK(mz.n_queries == 0 && mz.top[0].den == 0 && mz.known_false_reject.den == 0);
    free(mask);

    /* redecide with a tight threshold flips decisions, never rankings */
    size_t top1_before = m.top[0].num;
    s2w_reject_config rc; s2w_reject_config_default(&rc);
    rc.max_distance = 0.0;
    s2w_run_redecide(&run, &rc);
    s2w_eval_metrics(&ds, &run, NULL, &m);
    CHECK(m.top[0].num == top1_before && m.known_false_reject.num == 72 && m.unknown_false_accept.num == 0);
    CHECK(m.known_rej_reason[S2W_REJECT_DISTANCE] == 72 && m.unknown_rej_reason[S2W_REJECT_DISTANCE] == 27);

    /* calibration */
    s2w_run_redecide(&run, &st.rej);
    s2w_calib_report cr;
    CHECK(s2w_eval_calibrate(&ds, &run, 0.0, 0, &cr) == S2W_OK);
    CHECK(cr.n_genuine > 0 && cr.n_impostor_oov == 27 && cr.n_impostor_wrong == 0 && cr.cal.far == 0.0);
    CHECK(s2w_eval_calibrate(&ds, &run, 1.0, 0, &cr) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_eval_calibrate(&ds, &run, 0.5, 1, &cr) == S2W_OK && cr.n_impostor_wrong + cr.n_impostor_oov >= 27);
    s2w_run_free(&run);

    /* the matcher's own decision logic and s2w_eval_decide agree */
    s2w_settings s2 = st;
    s2.rej.max_distance = 2.0; s2.rej.min_margin_ratio = 0.3;
    CHECK(s2w_eval_collect(&ds, &s2, S2W_SPLIT_TEST, &run, err, sizeof err) == S2W_OK);
    s2w_matcher *mt = s2w_matcher_new(s2.rep, &s2.dtw, &s2.rej);
    for (size_t i = 0; i < ds.nr; i++) {
        if (ds.r[i].split != S2W_SPLIT_ENROL) continue;
        char path[900]; s2w_audio a; s2w_features f;
        s2w_dataset_audio_path(&ds, &ds.r[i], path, sizeof path);
        CHECK(s2w_wav_load(path, &a) == S2W_OK && s2w_features_extract(&a, &s2.feat, &f) == S2W_OK);
        s2w_matcher_add(mt, ds.w[ds.r[i].word].id, &f);
        s2w_features_free(&f); s2w_audio_free(&a);
    }
    int agree = 0, total = 0;
    for (size_t i = 0; i < run.nq; i += 7) {
        char path[900]; s2w_audio a; s2w_features f; s2w_match_result res;
        s2w_dataset_audio_path(&ds, &ds.r[run.q[i].rec], path, sizeof path);
        s2w_wav_load(path, &a); s2w_features_extract(&a, &s2.feat, &f);
        s2w_matcher_match(mt, &f, 5, &res);
        total++;
        agree += (int)res.decision == run.q[i].decision && (int)res.reason == run.q[i].reason;
        s2w_features_free(&f); s2w_audio_free(&a);
    }
    CHECK(total > 5 && agree == total);
    s2w_matcher_free(mt);
    s2w_run_free(&run);
    s2w_dataset_free(&ds); s2w_issues_free(&iss);
}

/* Tune mode must not touch test audio: delete it and the calib run must still work and agree. */
static void test_tuning_never_reads_test(void) {
    char cmd[3000];
    snprintf(TMP, sizeof TMP, "%s/ev_tmp", BUILD);
    sh("rm -rf '%s_nt' && mkdir '%s_nt' && cp '%s'/*.csv '%s'/dataset_meta.txt '%s_nt/' && mkdir '%s_nt/audio'", TMP, TMP, FX, FX, TMP, TMP);
    snprintf(cmd, sizeof cmd, "cd '%s' && for f in *.wav; do case $f in spkT*) ;; *) ln -s \"$(pwd)/$f\" '../ev_tmp_nt/audio/'$f;; esac; done", "x");
    (void)cmd;
    sh("cd '%s/audio' && for f in *.wav; do case $f in spkT*) ;; *) ln -s \"$(pwd)/$f\" '../../ev_tmp_nt/audio/'$f;; esac; done", FX);
    s2w_dataset ds; s2w_issues iss = {0};
    CHECK(s2w_dataset_load(TMP[0] ? "/dev/null/none" : "", NULL, &ds, &iss) != S2W_OK); /* sanity: bad dir fails */
    s2w_issues_free(&iss); iss = (s2w_issues){0};
    char dir[260]; snprintf(dir, sizeof dir, "%s_nt", TMP);
    CHECK(s2w_dataset_load(dir, NULL, &ds, &iss) == S2W_OK);   /* structure only: no audio opened */
    s2w_settings st; s2w_settings_default(&st);
    s2w_run run; char err[300];
    CHECK(s2w_eval_collect(&ds, &st, S2W_SPLIT_CALIB, &run, err, sizeof err) == S2W_OK && run.nq == 99);
    s2w_run_free(&run);
    CHECK(s2w_eval_collect(&ds, &st, S2W_SPLIT_TEST, &run, err, sizeof err) != S2W_OK);     /* missing test audio => hard error, not silent skip */
    s2w_dataset_free(&ds); s2w_issues_free(&iss);
}


static void test_personalised_protocol(void) {
    char fxp[230], work[260], out[260], si_cfg[260], pers_cfg[260];
    snprintf(fxp, sizeof fxp, "%s/fx_eval_pers", BUILD);
    snprintf(work, sizeof work, "%s_swap", fxp);
    snprintf(out, sizeof out, "%s_runs", fxp);
    snprintf(si_cfg, sizeof si_cfg, "%s_si.cfg", fxp);
    snprintf(pers_cfg, sizeof pers_cfg, "%s_pers.cfg", fxp);
    sh("rm -rf '%s' '%s' '%s_runs' && '%s/s2w_make_fixture' '%s' personalised >/dev/null", fxp, work, fxp, BUILD, fxp);
    spit(si_cfg, "#s2w-settings v1\nnorm=mean\n");
    spit(pers_cfg, "#s2w-settings v1\nprotocol=personalised\nnorm=mean\n");
    char eval[300]; snprintf(eval, sizeof eval, "'%s/s2w_eval'", BUILD);

    /* the two tracks use separate settings: a protocol mismatch is refused in both directions, before any audio is read */
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id a >/dev/null 2>&1", eval, fxp, si_cfg, out) != 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id b >/dev/null 2>&1", eval, FX, pers_cfg, out) != 0);
    CHECK(sh("test ! -d '%s/a' && test ! -d '%s/b'", out, out) == 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id ok --min-group-queries 1 --min-group-speakers 1 >/dev/null", eval, fxp, pers_cfg, out) == 0);
    char q[400]; snprintf(q, sizeof q, "%s/ok/result.json", out);
    char *j = slurp(q);
    CHECK(j && strstr(j, "\"protocol\": \"personalised\"") && strstr(j, "says nothing about recognising unfamiliar speakers"));
    free(j);
    snprintf(q, sizeof q, "%s/ok/summary.txt", out);
    j = slurp(q); CHECK(j && strstr(j, "PERSONALISED protocol")); free(j);

    /* separate settings hash per protocol (so thresholds/frozen files cannot be swapped) */
    s2w_settings a, b; char err[200];
    CHECK(s2w_settings_load(si_cfg, &a, err, sizeof err) == S2W_OK && s2w_settings_load(pers_cfg, &b, err, sizeof err) == S2W_OK && a.protocol == 0 && b.protocol == 1);
    char ha[65], hb[65]; s2w_settings_hash(&a, ha); s2w_settings_hash(&b, hb);
    CHECK(strcmp(ha, hb) != 0);
    spit(si_cfg, "#s2w-settings v1\nprotocol=speaker_independent\nnorm=mean\n");
    CHECK(s2w_settings_load(si_cfg, &a, err, sizeof err) == S2W_OK && a.protocol == 0);
    spit(si_cfg, "#s2w-settings v1\nprotocol=hybrid\n");
    CHECK(s2w_settings_load(si_cfg, &a, err, sizeof err) != S2W_OK);

    /* Locked-test participants' audio is never opened by calibration-split work: delete ALL of it (templates and queries) and calibrate */
    {
        char blind[260];
        snprintf(blind, sizeof blind, "%s_blind", fxp);
        sh("rm -rf '%s' && cp -r '%s' '%s' && rm -f '%s'/audio/pT*", blind, fxp, blind, blind);
        CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id blind --min-group-queries 1 --min-group-speakers 1 >/dev/null 2>&1", eval, blind, pers_cfg, out) == 0);
        CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --run-id blindtest >/dev/null 2>&1", eval, blind, pers_cfg, out) != 0);   /* and test is locked without frozen settings anyway */
        char qb[400]; snprintf(qb, sizeof qb, "%s/blind/queries.csv", out);
        CHECK(sh("! grep -q ',test,' '%s'", qb) == 0);
    }

    /* Each participant is matched ONLY against their own templates: swap the labels of pC1's own V1/V2 enrolment;
     * pC1's V1 queries must now be answered V2 (own templates), although every other participant enrolled V1 correctly. */
    sh("cp -r '%s' '%s' && sed -i '/,pC1,pC1-S1,/ { s/,SYNTH_V1,pC1/,SYNTH_TMP,pC1/; s/,SYNTH_V2,pC1/,SYNTH_V1,pC1/; s/,SYNTH_TMP,pC1/,SYNTH_V2,pC1/ }' '%s/recordings.csv'", fxp, work, work);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id swap --min-group-queries 1 --min-group-speakers 1 >/dev/null", eval, work, pers_cfg, out) == 0);
    snprintf(q, sizeof q, "%s/swap/queries.csv", out);
    CHECK(sh("test \"$(awk -F, '$3==\"SYNTH_V1\" && $5==\"pC1\" && $11==\"SYNTH_V2\"' '%s' | wc -l)\" -eq 2", q) == 0);
    CHECK(sh("test \"$(awk -F, '$3==\"SYNTH_V1\" && $5==\"pC1\" && $11==\"SYNTH_V1\"' '%s' | wc -l)\" -eq 0", q) == 0);
    CHECK(sh("test \"$(awk -F, '$3==\"SYNTH_V1\" && $5==\"pC2\" && $11==\"SYNTH_V1\"' '%s' | wc -l)\" -eq 2", q) == 0);      /* others unaffected */
}

static int json_valid(const char *path) {
    return sh("python3 -I -c \"import json,sys; json.load(open(sys.argv[1]))\" '%s' 2>/dev/null", path) == 0;
}

static void test_cli(void) {
    char out[260], base[260], cal[260], frozen[260], ledger[260];
    snprintf(out, sizeof out, "%s_runs", TMP);
    snprintf(base, sizeof base, "%s_base.cfg", TMP); snprintf(cal, sizeof cal, "%s_cal.cfg", TMP);
    snprintf(frozen, sizeof frozen, "%s_frozen.cfg", TMP); snprintf(ledger, sizeof ledger, "%s_ledger.txt", TMP);
    sh("rm -rf '%s' '%s' '%s' '%s' '%s'", out, cal, frozen, ledger, base);
    spit(base, "#s2w-settings v1\nnorm=mean\n");
    const char *E = BUILD;
    char common[600]; snprintf(common, sizeof common, "'%s/s2w_eval'", E);

    /* test split locked before freezing */
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --ledger '%s' 2>/dev/null", common, FX, base, out, ledger) != 0);
    CHECK(sh("test ! -e '%s'", ledger) == 0 && sh("test ! -d '%s'", out) == 0);

    /* calib run: fixed run id => reproducible files */
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id r1 --created-utc 2000-01-01T00:00:00Z >/dev/null", common, FX, base, out) == 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id r2 --created-utc 2000-01-01T00:00:00Z >/dev/null", common, FX, base, out) == 0);
    char p1[400], p2[400];
    snprintf(p1, sizeof p1, "%s/r1/result.json", out); snprintf(p2, sizeof p2, "%s/r2/result.json", out);
    CHECK(json_valid(p1));
    char *a = slurp(p1), *b = slurp(p2);
    CHECK(a && b && strlen(a) > 1000);
    /* identical except run_id */
    for (char *x = a; (x = strstr(x, "\"r1\"")); ) x[2] = '2';
    CHECK(a && b && !strcmp(a, b));
    CHECK(strstr(b, "\"manifest_sha256\"") && strstr(b, "\"git_commit\"") && strstr(b, "\"settings_sha256\"") && strstr(b, "\"scope_statement\"") &&
          strstr(b, "SYNTHETIC FIXTURE") && strstr(b, "\"den\"") && strstr(b, "\"threshold_calibration_source\""));
    CHECK(strstr(b, "undefined_because"));                 /* pitch/age etc. and empty denominators say why */
    free(a); free(b);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id r1 >/dev/null 2>&1", common, FX, base, out) != 0);   /* never overwrites a run */
    char q[400]; snprintf(q, sizeof q, "%s/r1/summary.txt", out);
    b = slurp(q); CHECK(b && strstr(b, "SYNTHETIC FIXTURE RUN") && strstr(b, "rejection_active=NO")); free(b);

    /* compare-norm */
    CHECK(sh("%s compare-norm --dataset '%s' --settings '%s' --out-dir '%s' --run-id cmp >/dev/null", common, FX, base, out) == 0);
    snprintf(q, sizeof q, "%s/cmp/result.json", out);
    CHECK(json_valid(q));
    b = slurp(q); CHECK(b && strstr(b, "\"norm\": \"none\"") && strstr(b, "\"norm\": \"mean\"") && strstr(b, "\"norm\": \"meanvar\"")); free(b);

    /* calibrate -> freeze -> test */
    CHECK(sh("%s calibrate --dataset '%s' --settings '%s' --max-far 0.0 --out '%s' --out-dir '%s' --run-id cal >/dev/null", common, FX, base, cal, out) == 0);
    CHECK(sh("%s calibrate --dataset '%s' --settings '%s' --max-far 0.0 --out '%s' --out-dir '%s' --run-id cal2 >/dev/null 2>&1", common, FX, base, cal, out) != 0);  /* no silent overwrite */
    snprintf(q, sizeof q, "%s/cal/result.json", out);
    CHECK(json_valid(q));
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --ledger '%s' >/dev/null 2>&1", common, FX, cal, out, ledger) != 0);  /* calibrated but not frozen */
    CHECK(sh("%s freeze --settings '%s' --out '%s' >/dev/null", common, cal, frozen) == 0);
    CHECK(sh("%s freeze --settings '%s' --out '%s' >/dev/null 2>&1", common, cal, frozen) != 0);
    CHECK(sh("%s freeze --settings '%s' --out '%s.x' >/dev/null 2>&1", common, frozen, frozen) != 0);          /* already frozen */
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --ledger '%s' --run-id t1 >/dev/null", common, FX, frozen, out, ledger) == 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --ledger '%s' --run-id t2 >/dev/null", common, FX, frozen, out, ledger) == 0);  /* same settings: reproduction */
    snprintf(q, sizeof q, "%s/t2/result.json", out);
    b = slurp(q); CHECK(b && json_valid(q) && strstr(b, "\"previous_test_evaluations_of_this_dataset\": 1") && strstr(b, "\"previous_with_different_settings\": 0")); free(b);

    /* different frozen settings on the same test set: refused, then flagged when acknowledged */
    char other[300], other_frozen[300];
    snprintf(other, sizeof other, "%s_other.cfg", TMP); snprintf(other_frozen, sizeof other_frozen, "%s_other_frozen.cfg", TMP);
    sh("rm -f '%s' '%s'", other, other_frozen);
    spit(other, "#s2w-settings v1\nnorm=meanvar\n");
    CHECK(sh("%s freeze --settings '%s' --out '%s' >/dev/null", common, other, other_frozen) == 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --ledger '%s' --run-id t3 >/dev/null 2>&1", common, FX, other_frozen, out, ledger) != 0);
    CHECK(sh("test ! -d '%s/t3'", out) == 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --ledger '%s' --run-id t4 --allow-test-reuse >/dev/null", common, FX, other_frozen, out, ledger) == 0);
    snprintf(q, sizeof q, "%s/t4/result.json", out);
    b = slurp(q); CHECK(b && strstr(b, "\"reuse_with_changed_settings_acknowledged\": true") && strstr(b, "\"previous_with_different_settings\": 2")); free(b);

    /* tampering with a frozen file is detected */
    sh("sed -i 's/^warp_penalty=.*/warp_penalty=0.9/' '%s'", frozen);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split test --out-dir '%s' --ledger '%s' --run-id t5 >/dev/null 2>&1", common, FX, frozen, out, ledger) != 0);

    /* invalid dataset refuses to evaluate */
    char bad[300]; snprintf(bad, sizeof bad, "%s_bad", TMP);
    sh("rm -rf '%s' && cp -r '%s_nt' '%s' && sed -i 's/,spkC1-s1,/,spkE1-s1,/' '%s/recordings.csv'", bad, TMP, bad, bad);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id bad >/dev/null 2>&1", common, bad, base, out) != 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split dev --out-dir '%s' >/dev/null 2>&1", common, FX, base, out) != 0);
    CHECK(sh("%s bogus >/dev/null 2>&1", common) != 0);
    CHECK(sh("%s run --dataset '%s' --settings '%s' --split calib --out-dir '%s' --run-id '../x' >/dev/null 2>&1", common, FX, base, out) != 0);
}

int main(int argc, char **argv) {
    snprintf(BUILD, sizeof BUILD, "%s", argc > 1 ? argv[1] : "build");
    snprintf(FX, sizeof FX, "%s/fx_eval", BUILD);
    snprintf(TMP, sizeof TMP, "%s/ev_tmp", BUILD);
    sh("rm -rf '%s' && '%s/s2w_make_fixture' '%s' >/dev/null", FX, BUILD, FX);
    test_settings(); test_decide(); test_pitch(); test_collect_and_metrics(); test_tuning_never_reads_test(); test_cli(); test_personalised_protocol();
    puts(failures ? "FAILED" : "eval: all tests passed");
    return failures ? 1 : 0;
}
