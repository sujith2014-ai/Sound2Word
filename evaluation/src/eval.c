#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_eval.h"
#include "s2w_json.h"
#include "s2w_pitch.h"
#include "s2w_sha256.h"

/* ---- collection ---------------------------------------------------------------------------- */

void s2w_run_free(s2w_run *r) { free(r->q); free(r->sp); memset(r, 0, sizeof *r); }

void s2w_eval_decide(const s2w_query *q, const s2w_reject_config *rc, int *decision, int *reason) {
    *decision = S2W_DECISION_REJECT;
    if (!q->usable) *reason = S2W_REASON_EXTRACT_FAIL;
    else if (q->quality & rc->quality_mask) *reason = S2W_REJECT_QUALITY;
    else if (q->n_cand == 0) *reason = S2W_REJECT_NO_CANDIDATE;
    else if (q->cand_dist[0] > rc->max_distance) *reason = S2W_REJECT_DISTANCE;
    else if (isfinite(q->margin) && q->margin < rc->min_margin_ratio) *reason = S2W_REJECT_AMBIGUOUS;
    else { *decision = S2W_DECISION_ACCEPT; *reason = S2W_REJECT_NONE; }
}

void s2w_run_redecide(s2w_run *r, const s2w_reject_config *rc) {
    for (size_t i = 0; i < r->nq; i++) s2w_eval_decide(&r->q[i], rc, &r->q[i].decision, &r->q[i].reason);
}

/* Integrity failures (unreadable file, pin mismatch) abort the run; extraction failures do not. */
static s2w_status load_audio(const s2w_dataset *ds, const s2w_rec *rec, const s2w_feat_config *fc, s2w_audio *a, char *err, size_t en) {
    char path[900], hex[65];
    if (s2w_dataset_audio_path(ds, rec, path, sizeof path) != S2W_OK) { snprintf(err, en, "%s: path too long", rec->id); return S2W_ERR_TOO_LARGE; }
    if (rec->sha256[0]) {
        if (s2w_sha256_file_hex(path, hex) != 0) { snprintf(err, en, "%s: cannot read audio", rec->id); return S2W_ERR_IO; }
        if (strcmp(hex, rec->sha256)) { snprintf(err, en, "%s: audio_sha256 mismatch (dataset content changed)", rec->id); return S2W_ERR_INCOMPATIBLE; }
    }
    s2w_status st = s2w_wav_load(path, a);
    if (st != S2W_OK) { snprintf(err, en, "%s: %s", rec->id, s2w_status_str(st)); return st; }
    if (a->sample_rate != fc->sample_rate) {
        if (fc->sample_rate != S2W_TARGET_RATE) { s2w_audio_free(a); snprintf(err, en, "%s: rate %d != feature rate", rec->id, a->sample_rate); return S2W_ERR_RATE_MISMATCH; }
        s2w_audio r;
        st = s2w_audio_to_16k(a, &r);
        s2w_audio_free(a);
        if (st != S2W_OK) { snprintf(err, en, "%s: resample failed", rec->id); return st; }
        *a = r;
    }
    return S2W_OK;
}

static s2w_speaker_pitch *speaker_slot(s2w_run *r, const char *id, size_t *cap) {
    for (size_t i = 0; i < r->nsp; i++) if (!strcmp(r->sp[i].id, id)) return &r->sp[i];
    if (r->nsp == *cap) {
        *cap = *cap ? *cap * 2 : 16;
        s2w_speaker_pitch *n = realloc(r->sp, *cap * sizeof *n);
        if (!n) return NULL;
        r->sp = n;
    }
    s2w_speaker_pitch *s = &r->sp[r->nsp++];
    memset(s, 0, sizeof *s);
    snprintf(s->id, sizeof s->id, "%s", id);
    s->median_f0 = NAN;
    return s;
}
static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

/* Template pools. Speaker-independent: ONE pool built from every enrolment speaker.
 * Personalised: one pool PER participant, built only from that participant's own enrolment recordings,
 * and each query is matched only against its own participant's pool. */
typedef struct { s2w_matcher **m; char (*spk)[S2W_ID_MAX]; size_t n, cap; int personalised; const s2w_settings *st; s2w_reject_config off; } mpool;

static s2w_matcher *pool_get(mpool *p, const char *speaker) {
    const char *key = p->personalised ? speaker : "";
    for (size_t i = 0; i < p->n; i++) if (!strcmp(p->spk[i], key)) return p->m[i];
    if (p->n == p->cap) {
        size_t nc = p->cap ? p->cap * 2 : 8;
        s2w_matcher **nm = realloc(p->m, nc * sizeof *nm);
        char (*ns)[S2W_ID_MAX] = realloc(p->spk, nc * sizeof *ns);
        if (nm) p->m = nm;
        if (ns) p->spk = ns;
        if (!nm || !ns) return NULL;
        p->cap = nc;
    }
    s2w_matcher *m = s2w_matcher_new(p->st->rep, &p->st->dtw, &p->off);
    if (!m) return NULL;
    snprintf(p->spk[p->n], S2W_ID_MAX, "%s", key);
    p->m[p->n++] = m;
    return m;
}
static s2w_matcher *pool_find(const mpool *p, const char *speaker) {
    const char *key = p->personalised ? speaker : "";
    for (size_t i = 0; i < p->n; i++) if (!strcmp(p->spk[i], key)) return p->m[i];
    return NULL;
}
static void pool_free(mpool *p) {
    for (size_t i = 0; i < p->n; i++) s2w_matcher_free(p->m[i]);
    free(p->m); free(p->spk);
    memset(p, 0, sizeof *p);
}

s2w_status s2w_eval_collect(const s2w_dataset *ds, const s2w_settings *st, int split, s2w_run *run, char *err, size_t en) {
    memset(run, 0, sizeof *run);
    if (split != S2W_SPLIT_CALIB && split != S2W_SPLIT_TEST) { snprintf(err, en, "query split must be calib or test"); return S2W_ERR_BAD_PARAMS; }
    if (st->protocol != ds->personalised) {
        snprintf(err, en, "settings protocol (%s) does not match the dataset protocol (%s): the two tracks use separate settings and thresholds",
                 st->protocol ? "personalised" : "speaker_independent", ds->personalised ? "personalised" : "speaker_independent");
        return S2W_ERR_INCOMPATIBLE;
    }
    mpool pool;
    memset(&pool, 0, sizeof pool);
    pool.personalised = ds->personalised;
    pool.st = st;
    s2w_reject_config_default(&pool.off);
    s2w_matcher *probe = s2w_matcher_new(st->rep, &st->dtw, &pool.off);
    if (!probe) { snprintf(err, en, "invalid matcher settings"); return S2W_ERR_BAD_PARAMS; }
    s2w_matcher_free(probe);
    s2w_status s;
    /* Personalised: only participants who have queries in THIS split get a template pool, so evaluating (or calibrating on)
     * the calib split never opens the enrolment audio of locked-test participants. */
    char (*qspk)[S2W_ID_MAX] = NULL;
    size_t nqs = 0;
    if (ds->personalised) {
        qspk = calloc(ds->nr ? ds->nr : 1, sizeof *qspk);
        if (!qspk) { snprintf(err, en, "out of memory"); return S2W_ERR_NOMEM; }
        for (size_t i = 0; i < ds->nr; i++) {
            if (ds->r[i].split != split) continue;
            size_t k;
            for (k = 0; k < nqs; k++) if (!strcmp(qspk[k], ds->r[i].speaker)) break;
            if (k == nqs) snprintf(qspk[nqs++], S2W_ID_MAX, "%s", ds->r[i].speaker);
        }
    }
    for (size_t i = 0; i < ds->nr; i++) {
        const s2w_rec *rec = &ds->r[i];
        if (rec->split != S2W_SPLIT_ENROL) continue;
        if (ds->personalised) {
            size_t k;
            for (k = 0; k < nqs; k++) if (!strcmp(qspk[k], rec->speaker)) break;
            if (k == nqs) continue;
        }
        s2w_audio a; s2w_features f;
        s = load_audio(ds, rec, &st->feat, &a, err, en);
        if (s != S2W_OK) { free(qspk); pool_free(&pool); return s; }
        s = s2w_features_extract(&a, &st->feat, &f);
        s2w_audio_free(&a);
        if (s != S2W_OK) { snprintf(err, en, "%s: enrolment feature extraction failed: %s", rec->id, s2w_status_str(s)); free(qspk); pool_free(&pool); return s; }
        s2w_matcher *m = pool_get(&pool, rec->speaker);
        s = m ? s2w_matcher_add(m, ds->w[rec->word].id, &f) : S2W_ERR_NOMEM;
        s2w_features_free(&f);
        if (s != S2W_OK) { snprintf(err, en, "%s: cannot add template: %s", rec->id, s2w_status_str(s)); free(qspk); pool_free(&pool); return s; }
        run->n_templates++;
    }
    free(qspk);
    if (run->n_templates == 0) { snprintf(err, en, "no enrolment recordings"); pool_free(&pool); return S2W_ERR_EMPTY; }

    size_t nq = 0;
    for (size_t i = 0; i < ds->nr; i++) nq += ds->r[i].split == split;
    run->split = split;
    run->q = calloc(nq ? nq : 1, sizeof *run->q);
    if (!run->q) { pool_free(&pool); return S2W_ERR_NOMEM; }
    size_t spcap = 0;
    for (size_t i = 0; i < ds->nr; i++) {
        const s2w_rec *rec = &ds->r[i];
        if (rec->split != split) continue;
        s2w_query *q = &run->q[run->nq++];
        q->rec = i; q->known = ds->w[rec->word].role == S2W_ROLE_VOCAB; q->true_word = rec->word; q->margin = INFINITY; q->warp = 1.0;
        s2w_matcher *m = pool_find(&pool, rec->speaker);
        if (!m) { snprintf(err, en, "%s: no templates available for this query (participant '%s' has no enrolment recordings)", rec->id, rec->speaker); pool_free(&pool); s2w_run_free(run); return S2W_ERR_EMPTY; }
        s2w_audio a; s2w_features f;
        s = load_audio(ds, rec, &st->feat, &a, err, en);
        if (s != S2W_OK) { pool_free(&pool); s2w_run_free(run); return s; }
        s2w_speaker_pitch *sp = speaker_slot(run, rec->speaker, &spcap);
        if (sp && sp->nf0 < 5) {
            double f0;
            if (s2w_median_f0(&a, &f0) == S2W_OK) sp->f0[sp->nf0++] = f0;
        }
        /* Query-side warp search (VTLN-style): try each alpha, keep the one whose best candidate is closest. Templates stay at alpha = 1.
         * With the search off this is exactly the previous behaviour (one extraction, alpha = 1). */
        int ngrid = st->n_warp > 0 ? st->n_warp : 1;
        int have = 0;
        s2w_match_result best_res;
        memset(&best_res, 0, sizeof best_res);
        double best_alpha = 1.0;
        unsigned best_quality = 0;
        for (int g = 0; g < ngrid; g++) {
            s2w_feat_config fc = st->feat;
            if (st->n_warp > 0) fc.warp_alpha = st->warp_grid[g];
            s = s2w_features_extract(&a, &fc, &f);
            if (s != S2W_OK) continue;
            s2w_match_result res;
            s = s2w_matcher_match(m, &f, S2W_EVAL_K, &res);
            unsigned qual = f.quality.warnings;
            s2w_features_free(&f);
            if (s != S2W_OK) { snprintf(err, en, "%s: match failed: %s", rec->id, s2w_status_str(s)); s2w_audio_free(&a); pool_free(&pool); s2w_run_free(run); return s; }
            if (!have || (res.n_candidates > 0 && (best_res.n_candidates == 0 || res.cand[0].distance < best_res.cand[0].distance))) {
                best_res = res; best_alpha = st->n_warp > 0 ? st->warp_grid[g] : 1.0; best_quality = qual; have = 1;
            }
        }
        s2w_audio_free(&a);
        if (!have) { q->usable = 0; continue; }
        q->usable = 1; q->quality = best_quality; q->warp = best_alpha;
        q->n_cand = best_res.n_candidates; q->margin = best_res.margin_ratio;
        for (size_t c = 0; c < best_res.n_candidates; c++) { q->cand_word[c] = s2w_dataset_find_word(ds, best_res.cand[c].label); q->cand_dist[c] = best_res.cand[c].distance; }
    }
    for (size_t i = 0; i < run->nsp; i++) {
        s2w_speaker_pitch *sp = &run->sp[i];
        if (sp->nf0 >= 2) { qsort(sp->f0, sp->nf0, sizeof(double), cmpd); sp->median_f0 = sp->f0[sp->nf0 / 2]; }
    }
    pool_free(&pool);
    s2w_run_redecide(run, &st->rej);
    return S2W_OK;
}

/* ---- metrics -------------------------------------------------------------------------------- */

static void ratio_add(s2w_ratio *r, int hit) { r->den++; r->num += hit != 0; }

void s2w_eval_metrics(const s2w_dataset *ds, const s2w_run *r, const unsigned char *mask, s2w_metrics *m) {
    (void)ds;
    memset(m, 0, sizeof *m);
    size_t accepted_known = 0, accepted_known_correct = 0;
    for (size_t i = 0; i < r->nq; i++) {
        if (mask && !mask[i]) continue;
        const s2w_query *q = &r->q[i];
        m->n_queries++;
        if (!q->usable) m->n_unusable++;
        int accept = q->decision == S2W_DECISION_ACCEPT;
        if (q->known) {
            m->n_known++;
            static const size_t K[3] = {1, 3, 5};
            for (int t = 0; t < 3; t++) {
                int hit = 0;
                for (size_t c = 0; c < q->n_cand && c < K[t]; c++) hit |= q->cand_word[c] == q->true_word;
                ratio_add(&m->top[t], hit);
            }
            int correct = q->n_cand > 0 && q->cand_word[0] == q->true_word;
            ratio_add(&m->known_accepted_correct, accept && correct);
            ratio_add(&m->known_accepted_wrong, accept && !correct);
            ratio_add(&m->known_false_reject, !accept);
            if (accept) { accepted_known++; accepted_known_correct += correct; } else m->known_rej_reason[q->reason]++;
        } else {
            m->n_unknown++;
            ratio_add(&m->unknown_false_accept, accept);
            ratio_add(&m->unknown_correct_reject, !accept);
            if (!accept) m->unknown_rej_reason[q->reason]++;
        }
    }
    m->known_precision_accepted.num = accepted_known_correct;
    m->known_precision_accepted.den = accepted_known;
}

/* ---- calibration ---------------------------------------------------------------------------- */

s2w_status s2w_eval_calibrate(const s2w_dataset *ds, const s2w_run *r, double max_far, int include_wrong, s2w_calib_report *out) {
    (void)ds;
    memset(out, 0, sizeof *out);
    out->max_far = max_far; out->include_wrong_known = include_wrong;
    double *gen = malloc((r->nq ? r->nq : 1) * sizeof *gen), *imp = malloc((r->nq ? r->nq : 1) * sizeof *imp);
    if (!gen || !imp) { free(gen); free(imp); return S2W_ERR_NOMEM; }
    size_t ng = 0, ni = 0;
    for (size_t i = 0; i < r->nq; i++) {
        const s2w_query *q = &r->q[i];
        if (!q->usable || q->n_cand == 0 || (q->quality & S2W_WARN_TOO_SHORT) || (q->quality & S2W_WARN_MOSTLY_SILENT)) { out->n_excluded_unusable++; continue; }
        int correct = q->cand_word[0] == q->true_word;
        if (q->known && correct) gen[ng++] = q->cand_dist[0];
        else if (!q->known) { imp[ni++] = q->cand_dist[0]; out->n_impostor_oov++; }
        else if (include_wrong) { imp[ni++] = q->cand_dist[0]; out->n_impostor_wrong++; }
    }
    out->n_genuine = ng;
    s2w_status st = (ng && ni) ? s2w_calibrate_threshold(gen, ng, imp, ni, max_far, &out->cal) : S2W_ERR_EMPTY;
    free(gen); free(imp);
    if (st != S2W_OK) return st;
    static const double M[S2W_N_MARGIN_SWEEP] = {0.0, 0.05, 0.1, 0.2, 0.3, 0.5};
    s2w_run tmp = *r;
    tmp.q = malloc((r->nq ? r->nq : 1) * sizeof *tmp.q);
    if (!tmp.q) return S2W_ERR_NOMEM;
    memcpy(tmp.q, r->q, r->nq * sizeof *tmp.q);
    for (int k = 0; k < S2W_N_MARGIN_SWEEP; k++) {
        s2w_reject_config rc;
        s2w_reject_config_default(&rc);
        rc.max_distance = out->cal.threshold; rc.min_margin_ratio = M[k];
        s2w_run_redecide(&tmp, &rc);
        s2w_metrics mm;
        s2w_eval_metrics(ds, &tmp, NULL, &mm);
        out->margin[k] = M[k];
        out->sweep_frr[k] = mm.known_false_reject.den ? (double)mm.known_false_reject.num / (double)mm.known_false_reject.den : NAN;
        out->sweep_far[k] = mm.unknown_false_accept.den ? (double)mm.unknown_false_accept.num / (double)mm.unknown_false_accept.den : NAN;
    }
    free(tmp.q);
    return S2W_OK;
}

/* ---- reporting ------------------------------------------------------------------------------ */

static const char *REASON_NAME[6] = {"none", "quality", "no_candidate", "distance", "ambiguous", "extraction_failed"};

static void j_ratio(s2w_json *j, const char *key, const s2w_ratio *r, const char *undef) {
    js_obj(j, key);
    js_int(j, "num", (long long)r->num);
    js_int(j, "den", (long long)r->den);
    if (r->den) js_num(j, "value", (double)r->num / (double)r->den);
    else { js_null(j, "value"); js_str(j, "undefined_because", undef); }
    js_obj_end(j);
}

static void j_metrics(s2w_json *j, const char *key, const s2w_metrics *m, int active) {
    js_obj(j, key);
    js_int(j, "n_queries", (long long)m->n_queries);
    js_int(j, "n_unusable_queries", (long long)m->n_unusable);
    js_bool(j, "rejection_active", active);
    js_obj(j, "known_words");
    js_int(j, "n", (long long)m->n_known);
    j_ratio(j, "top1_accuracy", &m->top[0], "no known-word queries");
    j_ratio(j, "top3_accuracy", &m->top[1], "no known-word queries");
    j_ratio(j, "top5_accuracy", &m->top[2], "no known-word queries");
    j_ratio(j, "accepted_and_top1_correct", &m->known_accepted_correct, "no known-word queries");
    j_ratio(j, "accepted_but_wrong_word", &m->known_accepted_wrong, "no known-word queries");
    j_ratio(j, "false_reject_rate", &m->known_false_reject, "no known-word queries");
    j_ratio(j, "precision_among_accepted", &m->known_precision_accepted, "no known-word query was accepted");
    js_obj(j, "rejected_by_reason");
    for (int k = 1; k < 6; k++) js_int(j, REASON_NAME[k], (long long)m->known_rej_reason[k]);
    js_obj_end(j);
    js_obj_end(j);
    js_obj(j, "unknown_words");
    js_int(j, "n", (long long)m->n_unknown);
    j_ratio(j, "false_accept_rate", &m->unknown_false_accept, "no unknown-word (OOV) queries");
    j_ratio(j, "correct_rejection_rate", &m->unknown_correct_reject, "no unknown-word (OOV) queries");
    js_obj(j, "rejected_by_reason");
    for (int k = 1; k < 6; k++) js_int(j, REASON_NAME[k], (long long)m->unknown_rej_reason[k]);
    js_obj_end(j);
    js_obj_end(j);
    js_obj_end(j);
}

static void count_unverified(const s2w_dataset *ds, size_t *words, size_t *labels) {
    unsigned char *seen = calloc(ds->nw ? ds->nw : 1, 1);
    *words = *labels = 0;
    for (size_t i = 0; i < ds->nr; i++) {
        *labels += !ds->r[i].label_verified;
        if (!seen[ds->r[i].word] && ds->w[ds->r[i].word].review != S2W_REVIEW_VERIFIED) { seen[ds->r[i].word] = 1; (*words)++; }
    }
    free(seen);
}

static size_t count_simulated(const s2w_dataset *ds) {
    size_t n = 0;
    for (size_t i = 0; i < ds->nr; i++) n += !strncmp(ds->r[i].noise_condition, "sim:", 4);
    return n;
}

static const char *protocol_note(const s2w_dataset *ds) {
    return ds->personalised ? "PERSONALISED protocol: templates come from the same participant's earlier session. This says nothing about recognising unfamiliar speakers."
                            : "Speaker-independent protocol: templates come only from enrolment speakers disjoint from the query speakers.";
}

static const char *scope_statement(const s2w_dataset *ds, int verified) {
    if (count_simulated(ds)) return "SIMULATED CONDITIONS: some recordings were artificially degraded by software. Software validation only; not evidence about real speakers, rooms or microphones.";
    if (ds->synthetic) return "SYNTHETIC FIXTURE: software validation only. These numbers say nothing about Malayalam recognition accuracy.";
    if (!verified) return "PROVISIONAL: word list or recording labels are not fully verified; do not treat as ground-truth accuracy.";
    return "Real dataset with verified word list and labels per its metadata (verification is attested by the dataset, not checked by this tool).";
}

static void j_common(s2w_json *j, const s2w_dataset *ds, const s2w_settings *st, const s2w_report_ctx *c) {
    size_t uw, ul;
    count_unverified(ds, &uw, &ul);
    int verified = !uw && !ul;
    js_str(j, "schema", "s2w-eval-result");
    js_int(j, "schema_version", 1);
    js_str(j, "run_id", c->run_id);
    js_str(j, "created_utc", c->created_utc);
    js_str(j, "command", c->command);
    js_str(j, "split", c->split_name);
    js_obj(j, "evidence");
    js_str(j, "dataset_kind", ds->synthetic ? "synthetic" : "real");
    js_bool(j, "ground_truth_verified", !ds->synthetic && verified);
    js_int(j, "unverified_words_used", (long long)uw);
    js_int(j, "unverified_labels", (long long)ul);
    js_int(j, "simulated_recordings", (long long)count_simulated(ds));
    js_str(j, "scope_statement", scope_statement(ds, verified));
    js_str(j, "protocol_note", protocol_note(ds));
    js_obj_end(j);
    js_obj(j, "code");
    js_str(j, "git_commit", c->git_commit);
    js_bool(j, "git_dirty", c->git_dirty);
    js_str(j, "git_describe", c->git_describe);
    js_str(j, "baseline_reference", "baseline-v0.3.0 = de08d4691e7b6c02969371f8c8c99f451eca156e");
    js_int(j, "feature_format_version", S2W_FEAT_FORMAT_VERSION);
    js_str(j, "feature_lib_version", S2W_FEAT_LIB_VERSION);
    js_obj_end(j);
    js_obj(j, "dataset");
    js_str(j, "dataset_id", ds->dataset_id);
    js_str(j, "dataset_version", ds->dataset_version);
    js_str(j, "manifest_sha256", ds->manifest_sha256);
    js_str(j, "protocol", ds->personalised ? "personalised" : "speaker_independent");
    size_t cnt[3] = {0, 0, 0};
    for (size_t i = 0; i < ds->nr; i++) cnt[ds->r[i].split]++;
    js_obj(j, "recordings_per_split");
    for (int k = 0; k < 3; k++) js_int(j, S2W_SPLIT_NAME[k], (long long)cnt[k]);
    js_obj_end(j);
    js_obj_end(j);
    static const char *NORM[] = {"none", "mean", "meanvar"};
    js_obj(j, "settings");
    js_str(j, "rep", st->rep == S2W_REP_MFCC ? "mfcc" : "logmel");
    js_obj(j, "features");
    js_str(j, "norm", NORM[st->feat.norm]);
    js_int(j, "sample_rate", st->feat.sample_rate); js_int(j, "frame_ms", st->feat.frame_ms); js_int(j, "hop_ms", st->feat.hop_ms);
    js_int(j, "fft_size", st->feat.fft_size); js_int(j, "n_mels", st->feat.n_mels); js_int(j, "n_mfcc", st->feat.n_mfcc);
    js_num(j, "f_min_hz", st->feat.f_min_hz); js_num(j, "f_max_hz", st->feat.f_max_hz); js_num(j, "preemph", st->feat.preemph);
    js_obj_end(j);
    js_obj(j, "techniques");
    js_str(j, "denoise", st->feat.denoise == S2W_DENOISE_SPECSUB ? "specsub" : "none");
    js_arr(j, "warp_grid");
    for (int i = 0; i < st->n_warp; i++) js_num(j, NULL, st->warp_grid[i]);
    js_arr_end(j);
    js_obj_end(j);
    js_obj(j, "dtw");
    js_num(j, "band_ratio", st->dtw.band_ratio); js_num(j, "min_len_ratio", st->dtw.min_len_ratio);
    js_num(j, "max_len_ratio", st->dtw.max_len_ratio); js_num(j, "warp_penalty", st->dtw.warp_penalty);
    js_obj_end(j);
    js_obj(j, "rejection");
    js_num(j, "max_distance", st->rej.max_distance);   /* null = off */
    js_num(j, "min_margin_ratio", st->rej.min_margin_ratio);
    js_int(j, "quality_mask", st->rej.quality_mask);
    js_str(j, "threshold_calibration_source", st->calibration_source[0] ? st->calibration_source : "none");
    js_obj_end(j);
    js_bool(j, "frozen", st->frozen);
    char h[65];
    s2w_settings_hash(st, h);
    js_str(j, "settings_sha256", h);
    js_obj_end(j);
    js_obj(j, "test_access");
    if (c->test_prev < 0) js_str(j, "status", "not a test-split run");
    else {
        js_str(j, "status", "test split evaluated with frozen settings");
        js_int(j, "previous_test_evaluations_of_this_dataset", c->test_prev);
        js_int(j, "previous_with_different_settings", c->test_prev_other);
        js_bool(j, "reuse_with_changed_settings_acknowledged", c->test_reuse_ack);
    }
    js_obj_end(j);
}

/* group helpers */
static int pitch_group(const s2w_run *r, const char *speaker, char *out, size_t n, double *lo, double *hi) {
    double v[256]; size_t nv = 0;
    const s2w_speaker_pitch *me = NULL;
    for (size_t i = 0; i < r->nsp && nv < 256; i++) {
        if (isfinite(r->sp[i].median_f0)) v[nv++] = r->sp[i].median_f0;
        if (!strcmp(r->sp[i].id, speaker)) me = &r->sp[i];
    }
    if (nv < 6 || !me || !isfinite(me->median_f0)) return 0;
    qsort(v, nv, sizeof *v, cmpd);
    double t1 = v[nv / 3], t2 = v[(2 * nv) / 3];
    *lo = v[0]; *hi = v[nv - 1];
    snprintf(out, n, "%s", me->median_f0 < t1 ? "low" : me->median_f0 < t2 ? "mid" : "high");
    return 1;
}

typedef enum { D_DEVICE, D_ENV, D_STYLE, D_PITCH, D_SEX, D_AGE, D_ACCENT, D_COUNT } dim_t;
static const char *DIM_NAME[D_COUNT] = {"device", "environment", "speaking_style", "measured_pitch_tercile", "sex_self_report", "age_band", "accent_region"};

static int group_of(const s2w_dataset *ds, const s2w_run *r, const s2w_query *q, dim_t d, char *out, size_t n) {
    const s2w_rec *rec = &ds->r[q->rec];
    const s2w_participant *p = s2w_dataset_participant(ds, rec->speaker);
    const char *v = NULL;
    switch (d) {
    case D_DEVICE: v = rec->device; break;
    case D_ENV: if (rec->noisy) { snprintf(out, n, "noisy:%s", rec->noise_condition); return 1; } v = "clean"; break;
    case D_STYLE: v = rec->style[0] ? rec->style : "unspecified"; break;
    case D_PITCH: { double lo, hi; return pitch_group(r, rec->speaker, out, n, &lo, &hi); }
    case D_SEX: if (p && p->group_ok) v = p->sex; break;
    case D_AGE: if (p && p->group_ok) v = p->age; break;
    case D_ACCENT: if (p && p->group_ok) v = p->accent; break;
    default: break;
    }
    if (!v || !v[0] || !strcmp(v, "declined")) return 0;
    snprintf(out, n, "%s", v);
    return 1;
}

static void j_groups(s2w_json *j, const s2w_dataset *ds, const s2w_run *r, const s2w_report_ctx *c, int active) {
    js_obj(j, "groups");
    js_int(j, "min_group_speakers", c->min_group_speakers);
    js_int(j, "min_group_queries", c->min_group_queries);
    js_str(j, "policy", "only voluntary attributes (group_reporting_ok=yes) and audio-measured pitch; small groups suppressed and not named");
    for (int d = 0; d < D_COUNT; d++) {
        js_obj(j, DIM_NAME[d]);
        char (*vals)[S2W_ID_MAX + 8] = malloc((r->nq ? r->nq : 1) * sizeof *vals);
        size_t nvals = 0, covered = 0;
        for (size_t i = 0; i < r->nq; i++) {
            char g[S2W_ID_MAX + 8];
            if (!group_of(ds, r, &r->q[i], d, g, sizeof g)) continue;
            covered++;
            size_t k;
            for (k = 0; k < nvals; k++) if (!strcmp(vals[k], g)) break;
            if (k == nvals) strcpy(vals[nvals++], g);
        }
        js_int(j, "queries_with_value", (long long)covered);
        if (nvals == 0) js_str(j, "status", "undefined: no values available for this dimension");
        else {
            /* sort values for deterministic output */
            for (size_t a = 0; a < nvals; a++) for (size_t b = a + 1; b < nvals; b++)
                if (strcmp(vals[b], vals[a]) < 0) { char t[S2W_ID_MAX + 8]; strcpy(t, vals[a]); strcpy(vals[a], vals[b]); strcpy(vals[b], t); }
            size_t suppressed = 0;
            js_arr(j, "groups");
            unsigned char *mask = malloc(r->nq ? r->nq : 1);
            for (size_t k = 0; k < nvals; k++) {
                char spk[64][S2W_ID_MAX]; size_t nspk = 0;
                memset(mask, 0, r->nq);
                for (size_t i = 0; i < r->nq; i++) {
                    char g[S2W_ID_MAX + 8];
                    if (!group_of(ds, r, &r->q[i], d, g, sizeof g) || strcmp(g, vals[k])) continue;
                    mask[i] = 1;
                    const char *sp = ds->r[r->q[i].rec].speaker;
                    size_t s;
                    for (s = 0; s < nspk; s++) if (!strcmp(spk[s], sp)) break;
                    if (s == nspk && nspk < 64) snprintf(spk[nspk++], S2W_ID_MAX, "%s", sp);
                }
                s2w_metrics m;
                s2w_eval_metrics(ds, r, mask, &m);
                if ((int)nspk < c->min_group_speakers || (int)m.n_queries < c->min_group_queries) { suppressed++; continue; }
                js_obj(j, NULL);
                js_str(j, "value", vals[k]);
                js_int(j, "n_speakers", (long long)nspk);
                j_metrics(j, "metrics", &m, active);
                js_obj_end(j);
            }
            free(mask);
            js_arr_end(j);
            js_int(j, "suppressed_groups", (long long)suppressed);
            if (d == D_PITCH) {
                double lo = 0, hi = 0; char g[16];
                for (size_t i = 0; i < r->nq; i++) if (pitch_group(r, ds->r[r->q[i].rec].speaker, g, sizeof g, &lo, &hi)) break;
                js_num(j, "speaker_median_f0_min_hz", lo); js_num(j, "speaker_median_f0_max_hz", hi);
            }
        }
        free(vals);
        js_obj_end(j);
    }
    js_obj_end(j);
}

static void j_pairs(s2w_json *j, const s2w_dataset *ds, const s2w_run *r) {
    js_obj(j, "minimal_pairs");
    js_str(j, "note", "ranking-level (top-1 candidate), independent of rejection; isolates acoustic confusability");
    js_arr(j, "pairs");
    size_t tot_n[4] = {0}, tot_ok[4] = {0}, tot_conf[4] = {0};
    for (size_t a = 0; a < ds->nw; a++) {
        const s2w_word *wa = &ds->w[a];
        if (!wa->id[0] || !wa->pair_id[0]) continue;
        int b = -1;
        for (size_t k = a + 1; k < ds->nw; k++) if (ds->w[k].id[0] && !strcmp(ds->w[k].pair_id, wa->pair_id)) b = (int)k;
        if (b < 0) continue;
        size_t n[2] = {0, 0}, ok[2] = {0, 0}, conf[2] = {0, 0}, none[2] = {0, 0};
        for (size_t i = 0; i < r->nq; i++) {
            const s2w_query *q = &r->q[i];
            if (!q->known) continue;
            int side = q->true_word == (int)a ? 0 : q->true_word == b ? 1 : -1;
            if (side < 0) continue;
            n[side]++;
            if (!q->usable || q->n_cand == 0) { none[side]++; continue; }
            if (q->cand_word[0] == q->true_word) ok[side]++;
            else if (q->cand_word[0] == (side == 0 ? b : (int)a)) conf[side]++;
        }
        js_obj(j, NULL);
        js_str(j, "pair_id", wa->pair_id);
        js_str(j, "contrast_type", S2W_CONTRAST_NAME[wa->contrast]);
        for (int s = 0; s < 2; s++) {
            js_obj(j, s == 0 ? "word_a" : "word_b");
            js_str(j, "word_id", s == 0 ? wa->id : ds->w[b].id);
            js_int(j, "queries", (long long)n[s]);
            js_int(j, "top1_correct", (long long)ok[s]);
            js_int(j, "confused_with_partner", (long long)conf[s]);
            js_int(j, "other_wrong", (long long)(n[s] - ok[s] - conf[s] - none[s]));
            js_int(j, "no_candidate_or_unusable", (long long)none[s]);
            js_obj_end(j);
            tot_n[wa->contrast] += n[s]; tot_ok[wa->contrast] += ok[s]; tot_conf[wa->contrast] += conf[s];
        }
        js_obj_end(j);
    }
    js_arr_end(j);
    js_obj(j, "by_contrast_type");
    for (int ct = 1; ct < 4; ct++) {
        js_obj(j, S2W_CONTRAST_NAME[ct]);
        js_int(j, "queries", (long long)tot_n[ct]);
        s2w_ratio a = {tot_ok[ct], tot_n[ct]}, b = {tot_conf[ct], tot_n[ct]};
        j_ratio(j, "top1_correct", &a, "no pairs of this contrast type with queries");
        j_ratio(j, "confused_with_partner", &b, "no pairs of this contrast type with queries");
        js_obj_end(j);
    }
    js_obj_end(j);
    js_obj_end(j);
}

void s2w_eval_write_json(FILE *f, const s2w_dataset *ds, const s2w_settings *st, const s2w_run *r, const s2w_report_ctx *c) {
    s2w_json j; js_init(&j, f);
    js_obj(&j, NULL);
    j_common(&j, ds, st, c);
    s2w_metrics m;
    s2w_eval_metrics(ds, r, NULL, &m);
    int active = isfinite(st->rej.max_distance) || st->rej.min_margin_ratio > 0;
    j_metrics(&j, "metrics", &m, active);
    j_groups(&j, ds, r, c, active);
    j_pairs(&j, ds, r);
    js_obj_end(&j);
    js_end(&j);
}

void s2w_eval_write_compare_json(FILE *f, const s2w_dataset *ds, const s2w_settings *st, const s2w_run *runs, const s2w_norm_mode *norms,
                                 size_t n, const s2w_report_ctx *c) {
    static const char *NORM[] = {"none", "mean", "meanvar"};
    s2w_json j; js_init(&j, f);
    js_obj(&j, NULL);
    j_common(&j, ds, st, c);
    js_str(&j, "note", "rejection disabled for all variants: distance scales differ per normalisation, so thresholds must be re-calibrated for the chosen one");
    js_arr(&j, "comparisons");
    for (size_t i = 0; i < n; i++) {
        s2w_metrics m;
        s2w_eval_metrics(ds, &runs[i], NULL, &m);
        js_obj(&j, NULL);
        js_str(&j, "norm", NORM[norms[i]]);
        js_int(&j, "n_known", (long long)m.n_known);
        j_ratio(&j, "top1_accuracy", &m.top[0], "no known-word queries");
        j_ratio(&j, "top3_accuracy", &m.top[1], "no known-word queries");
        j_ratio(&j, "top5_accuracy", &m.top[2], "no known-word queries");
        /* distance separation of known vs unknown best distances (medians) */
        double *kd = malloc((runs[i].nq + 1) * sizeof *kd), *ud = malloc((runs[i].nq + 1) * sizeof *ud);
        size_t nk = 0, nu = 0;
        for (size_t q = 0; q < runs[i].nq; q++) {
            const s2w_query *x = &runs[i].q[q];
            if (!x->usable || !x->n_cand) continue;
            if (x->known && x->cand_word[0] == x->true_word) kd[nk++] = x->cand_dist[0];
            else if (!x->known) ud[nu++] = x->cand_dist[0];
        }
        qsort(kd, nk, sizeof *kd, cmpd); qsort(ud, nu, sizeof *ud, cmpd);
        js_num(&j, "median_best_distance_correct_known", nk ? kd[nk / 2] : NAN);
        js_num(&j, "median_best_distance_unknown", nu ? ud[nu / 2] : NAN);
        js_int(&j, "n_correct_known_with_distance", (long long)nk);
        js_int(&j, "n_unknown_with_distance", (long long)nu);
        free(kd); free(ud);
        j_pairs(&j, ds, &runs[i]);
        js_obj_end(&j);
    }
    js_arr_end(&j);
    js_obj_end(&j);
    js_end(&j);
}

void s2w_eval_write_calibration_json(FILE *f, const s2w_dataset *ds, const s2w_settings *st_in, const s2w_settings *st_out,
                                     const s2w_run *r, const s2w_calib_report *cr, const s2w_report_ctx *c) {
    s2w_json j; js_init(&j, f);
    js_obj(&j, NULL);
    j_common(&j, ds, st_in, c);
    js_obj(&j, "calibration");
    js_str(&j, "data", "calib split only (enrol templates, calib queries)");
    js_num(&j, "max_far_target", cr->max_far);
    js_str(&j, "impostor_set", cr->include_wrong_known ? "unknown words + known words ranked wrong" : "unknown words (OOV)");
    js_int(&j, "n_genuine", (long long)cr->n_genuine);
    js_int(&j, "n_impostor_oov", (long long)cr->n_impostor_oov);
    js_int(&j, "n_impostor_wrong_known", (long long)cr->n_impostor_wrong);
    js_int(&j, "n_excluded_unusable", (long long)cr->n_excluded_unusable);
    js_num(&j, "far_resolution", (cr->n_impostor_oov + cr->n_impostor_wrong) ? 1.0 / (double)(cr->n_impostor_oov + cr->n_impostor_wrong) : NAN);
    js_num(&j, "chosen_max_distance", cr->cal.threshold);
    js_num(&j, "in_sample_far", cr->cal.far);
    js_num(&j, "in_sample_frr", cr->cal.frr);
    js_str(&j, "caveat", "in-sample estimates on calibration data are optimistic; only the untouched test split gives an honest estimate");
    js_arr(&j, "margin_sweep");
    for (int k = 0; k < S2W_N_MARGIN_SWEEP; k++) {
        js_obj(&j, NULL);
        js_num(&j, "min_margin_ratio", cr->margin[k]);
        js_num(&j, "known_false_reject_rate", cr->sweep_frr[k]);
        js_num(&j, "unknown_false_accept_rate", cr->sweep_far[k]);
        js_obj_end(&j);
    }
    js_arr_end(&j);
    js_str(&j, "output_calibration_source", st_out->calibration_source);
    js_obj_end(&j);
    (void)r;
    js_obj_end(&j);
    js_end(&j);
}

static const char *pct(const s2w_ratio *r, char *buf, size_t n) {
    if (!r->den) snprintf(buf, n, "undefined (0/0)");
    else snprintf(buf, n, "%zu/%zu = %.1f%%", r->num, r->den, 100.0 * (double)r->num / (double)r->den);
    return buf;
}

void s2w_eval_write_summary(FILE *f, const s2w_dataset *ds, const s2w_settings *st, const s2w_run *r, const s2w_report_ctx *c) {
    size_t uw, ul;
    count_unverified(ds, &uw, &ul);
    fprintf(f, "Sound2Word evaluation  run_id=%s  split=%s  command=%s\n", c->run_id, c->split_name, c->command);
    if (count_simulated(ds)) fprintf(f, "*** SIMULATED CONDITIONS (%zu recordings degraded by software): software validation only. NOT evidence about real speakers, rooms or microphones. ***\n", count_simulated(ds));
    if (ds->synthetic) fprintf(f, "*** SYNTHETIC FIXTURE RUN: software validation only. NOT Malayalam recognition accuracy. ***\n");
    else if (uw || ul) fprintf(f, "*** PROVISIONAL: %zu used word(s) not verified, %zu recording label(s) not verified. ***\n", uw, ul);
    fprintf(f, "protocol: %s\n", protocol_note(ds));
    fprintf(f, "code: %s%s (%s)\ndataset: %s v%s  manifest_sha256=%.16s...\n", c->git_commit, c->git_dirty ? " (dirty tree)" : "", c->git_describe,
            ds->dataset_id, ds->dataset_version, ds->manifest_sha256);
    char h[65];
    s2w_settings_hash(st, h);
    fprintf(f, "settings: rep=%s norm=%s frozen=%s sha256=%.16s... calibration_source=%s\n", st->rep == S2W_REP_MFCC ? "mfcc" : "logmel",
            (const char *[]){"none", "mean", "meanvar"}[st->feat.norm], st->frozen ? "yes" : "no", h,
            st->calibration_source[0] ? st->calibration_source : "none");
    s2w_metrics m;
    s2w_eval_metrics(ds, r, NULL, &m);
    int active = isfinite(st->rej.max_distance) || st->rej.min_margin_ratio > 0;
    fprintf(f, "templates=%zu queries=%zu (known=%zu unknown=%zu unusable=%zu) rejection_active=%s\n", r->n_templates, m.n_queries, m.n_known, m.n_unknown, m.n_unusable, active ? "yes" : "NO");
    char b[64];
    fprintf(f, "known words:   top1 %s | top3 %s | top5 %s\n", pct(&m.top[0], b, sizeof b), pct(&m.top[1], (char[64]){0}, 64), pct(&m.top[2], (char[64]){0}, 64));
    fprintf(f, "               accepted&correct %s\n", pct(&m.known_accepted_correct, b, sizeof b));
    fprintf(f, "               accepted but wrong word %s\n", pct(&m.known_accepted_wrong, b, sizeof b));
    fprintf(f, "               false reject %s\n", pct(&m.known_false_reject, b, sizeof b));
    fprintf(f, "unknown words: false accept %s\n", pct(&m.unknown_false_accept, b, sizeof b));
    fprintf(f, "               correct reject %s\n", pct(&m.unknown_correct_reject, b, sizeof b));
    if (!active) fprintf(f, "NOTE: no rejection thresholds set, so every unknown word is accepted by construction.\n");
    if (c->test_prev >= 0) fprintf(f, "test split: %d previous evaluation(s) of this dataset, %d with different settings%s\n", c->test_prev, c->test_prev_other, c->test_reuse_ack ? " (reuse acknowledged: test set no longer untouched)" : "");
    fprintf(f, "See result.json for denominators, groups (small groups suppressed) and minimal-pair confusion.\n");
}

void s2w_eval_write_queries_csv(FILE *f, const s2w_dataset *ds, const s2w_run *r) {
    fprintf(f, "recording_id,split,word_id,role,speaker_id,device_id,environment,usable,decision,reason,top1,top1_distance,top2,top2_distance,top3,top3_distance,margin_ratio,quality_warnings,warp_alpha\n");
    for (size_t i = 0; i < r->nq; i++) {
        const s2w_query *q = &r->q[i];
        const s2w_rec *rec = &ds->r[q->rec];
        fprintf(f, "%s,%s,%s,%s,%s,%s,%s,%d,%s,%s", rec->id, S2W_SPLIT_NAME[rec->split], ds->w[rec->word].id, q->known ? "vocab" : "oov",
                rec->speaker, rec->device, rec->noisy ? "noisy" : "clean", q->usable, q->decision == S2W_DECISION_ACCEPT ? "accept" : "reject", REASON_NAME[q->reason]);
        for (size_t c = 0; c < 3; c++) {
            if (c < q->n_cand) fprintf(f, ",%s,%.6f", ds->w[q->cand_word[c]].id, q->cand_dist[c]);
            else fprintf(f, ",,");
        }
        if (isfinite(q->margin)) fprintf(f, ",%.6f", q->margin); else fprintf(f, ",");
        fprintf(f, ",0x%02x,%.4f\n", q->quality, q->warp);
    }
}
