#include "s2w_dataset.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_csv.h"
#include "s2w_sha256.h"

const char *const S2W_SPLIT_NAME[3] = {"enrol", "calib", "test"};
const char *const S2W_CONTRAST_NAME[4] = {"none", "vowel_length", "gemination", "other"};
const char *const S2W_REVIEW_NAME[3] = {"draft", "reviewed", "verified"};

/* ---- issues ---- */
void s2w_issues_free(s2w_issues *i) { free(i->v); memset(i, 0, sizeof *i); }

void s2w_issue_add(s2w_issues *i, int is_error, const char *file, size_t line, const char *fmt, ...) {
    if (i->n == i->cap) {
        size_t nc = i->cap ? i->cap * 2 : 32;
        s2w_issue *nv = realloc(i->v, nc * sizeof *nv);
        if (!nv) return;
        i->v = nv; i->cap = nc;
    }
    s2w_issue *x = &i->v[i->n++];
    x->is_error = is_error; x->line = line;
    snprintf(x->file, sizeof x->file, "%s", file);
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(x->msg, sizeof x->msg, fmt, ap);
    va_end(ap);
    if (is_error) i->n_err++; else i->n_warn++;
}
#define ERR(f, l, ...) s2w_issue_add(iss, 1, f, l, __VA_ARGS__)
#define WARN(f, l, ...) s2w_issue_add(iss, 0, f, l, __VA_ARGS__)

/* ---- small validators ---- */
static int valid_id(const char *s) {
    size_t n = strlen(s);
    if (n == 0 || n >= S2W_ID_MAX) return 0;
    for (; *s; s++) if (!isalnum((unsigned char)*s) && *s != '.' && *s != '_' && *s != '-') return 0;
    return 1;
}
static int valid_utf8(const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        if (*p < 0x20 || *p == 0x7f) return 0;
        if (*p < 0x80) { p++; continue; }
        int n = (*p & 0xE0) == 0xC0 ? 1 : (*p & 0xF0) == 0xE0 ? 2 : (*p & 0xF8) == 0xF0 ? 3 : -1;
        if (n < 0 || (*p == 0xC0 || *p == 0xC1)) return 0;
        for (int i = 1; i <= n; i++) if ((p[i] & 0xC0) != 0x80) return 0;
        p += n + 1;
    }
    return 1;
}
static int cpy(char *d, size_t n, const char *s) {
    size_t l = strlen(s);
    if (l >= n) return 0;
    memcpy(d, s, l + 1);
    return 1;
}
static int valid_path(const char *p) {
    size_t n = strlen(p);
    if (n < 5 || n >= 256 || p[0] == '/' || strchr(p, '\\') || (n > 1 && p[1] == ':')) return 0;
    if (strcmp(p + n - 4, ".wav")) return 0;
    for (const char *q = p; *q; ) {
        const char *e = strchr(q, '/');
        size_t l = e ? (size_t)(e - q) : strlen(q);
        if (l == 0 || (l == 1 && q[0] == '.') || (l == 2 && q[0] == '.' && q[1] == '.')) return 0;
        for (size_t k = 0; k < l; k++) if ((unsigned char)q[k] < 0x20) return 0;
        if (!e) break;
        q = e + 1;
    }
    return 1;
}
static int valid_date(const char *s) {
    if (strlen(s) != 10) return 0;
    for (int i = 0; i < 10; i++) {
        if (i == 4 || i == 7) { if (s[i] != '-') return 0; }
        else if (!isdigit((unsigned char)s[i])) return 0;
    }
    int mo = atoi(s + 5), d = atoi(s + 8);
    return mo >= 1 && mo <= 12 && d >= 1 && d <= 31;
}
static int yesno(const char *s, int *out) {
    if (!strcmp(s, "yes")) { *out = 1; return 1; }
    if (!strcmp(s, "no")) { *out = 0; return 1; }
    return 0;
}
static int plain_text(const char *s, size_t max) { return strlen(s) < max && valid_utf8(s); }

static void require_cols(const s2w_csv *c, const char *file, const char *const *req, size_t nreq, s2w_issues *iss, int *ok) {
    int *used = calloc(c->n_cols, sizeof *used);
    for (size_t k = 0; k < nreq; k++) {
        int j = s2w_csv_col(c, req[k]);
        if (j < 0) { ERR(file, 2, "missing column '%s'", req[k]); *ok = 0; }
        else used[j] = 1;
    }
    for (size_t j = 0; j < c->n_cols; j++)
        if (!used[j]) { ERR(file, 2, "unknown column '%s'", s2w_csv_at(c, 0, j)); *ok = 0; }
    free(used);
}

/* ---- duplicate scan over string keys ---- */
typedef struct { char key[260]; size_t idx; } keyed;
static int cmp_keyed(const void *a, const void *b) {
    int c = strcmp(((const keyed *)a)->key, ((const keyed *)b)->key);
    if (c) return c;
    size_t x = ((const keyed *)a)->idx, y = ((const keyed *)b)->idx;
    return (x > y) - (x < y);
}
/* Calls report(first_idx, dup_idx) for each later duplicate. */
static void dup_scan(keyed *k, size_t n, void (*report)(void *, size_t, size_t), void *ctx) {
    qsort(k, n, sizeof *k, cmp_keyed);
    for (size_t i = 1; i < n; i++)
        if (!strcmp(k[i].key, k[i - 1].key)) {
            size_t first = k[i - 1].idx;
            for (size_t j = i - 1; j > 0 && !strcmp(k[j - 1].key, k[i].key); j--) first = k[j - 1].idx;
            report(ctx, first, k[i].idx);
        }
}

/* ---- loading ---- */
static void cb_dup_word(void *cx, size_t a, size_t b) {
    struct { s2w_dataset *d; s2w_issues *i; } *c = cx;
    (void)a;
    s2w_issue_add(c->i, 1, "wordlist", c->d->w[b].line, "duplicate word_id '%s'", c->d->w[b].id);
}

static char *read_all(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return NULL; }
    char *b = malloc((size_t)n + 1);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); fclose(f); return NULL; }
    fclose(f);
    b[n] = 0; *len = (size_t)n;
    return b;
}

static void parse_meta(const char *dir, s2w_dataset *ds, s2w_issues *iss) {
    char path[600];
    snprintf(path, sizeof path, "%s/dataset_meta.txt", dir);
    size_t len;
    char *txt = read_all(path, &len);
    if (!txt) { ERR("dataset_meta", 0, "cannot read dataset_meta.txt"); return; }
    int seen_schema = 0, seen_kind = 0, seen_id = 0, seen_ver = 0, seen_proto = 0;
    size_t ln = 0;
    for (char *line = strtok(txt, "\n"); line; line = strtok(NULL, "\n")) {
        ln++;
        size_t l = strlen(line);
        if (l && line[l - 1] == '\r') line[--l] = 0;
        if (!l || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) { ERR("dataset_meta", ln, "expected key=value"); continue; }
        *eq = 0;
        const char *k = line, *v = eq + 1;
        if (!strcmp(k, "schema_version")) {
            if (seen_schema++) ERR("dataset_meta", ln, "duplicate key schema_version");
            if (strcmp(v, "1")) ERR("dataset_meta", ln, "unsupported schema_version '%s' (supported: 1)", v);
        } else if (!strcmp(k, "kind")) {
            if (seen_kind++) ERR("dataset_meta", ln, "duplicate key kind");
            if (!strcmp(v, "synthetic")) ds->synthetic = 1;
            else if (strcmp(v, "real")) ERR("dataset_meta", ln, "kind must be 'real' or 'synthetic'");
        } else if (!strcmp(k, "dataset_id")) {
            if (seen_id++) ERR("dataset_meta", ln, "duplicate key dataset_id");
            if (!valid_id(v) || !cpy(ds->dataset_id, sizeof ds->dataset_id, v)) ERR("dataset_meta", ln, "invalid dataset_id");
        } else if (!strcmp(k, "dataset_version")) {
            if (seen_ver++) ERR("dataset_meta", ln, "duplicate key dataset_version");
            if (!valid_id(v) || !cpy(ds->dataset_version, sizeof ds->dataset_version, v)) ERR("dataset_meta", ln, "invalid dataset_version");
        } else if (!strcmp(k, "protocol")) {
            if (seen_proto++) ERR("dataset_meta", ln, "duplicate key protocol");
            if (strcmp(v, "speaker_independent")) ERR("dataset_meta", ln, "unsupported protocol '%s' (supported: speaker_independent)", v);
            else cpy(ds->protocol, sizeof ds->protocol, v);
        } else ERR("dataset_meta", ln, "unknown key '%s'", k);
    }
    if (!seen_schema) ERR("dataset_meta", 0, "missing schema_version");
    if (!seen_kind) ERR("dataset_meta", 0, "missing kind");
    if (!seen_id) ERR("dataset_meta", 0, "missing dataset_id");
    if (!seen_ver) ERR("dataset_meta", 0, "missing dataset_version");
    if (!seen_proto) ERR("dataset_meta", 0, "missing protocol");
    free(txt);
}

static void parse_words(const char *dir, s2w_dataset *ds, s2w_issues *iss) {
    char path[600], e[200];
    snprintf(path, sizeof path, "%s/wordlist.csv", dir);
    s2w_csv c;
    if (s2w_csv_load(path, "#s2w-wordlist v1", &c, e, sizeof e) != S2W_OK) { ERR("wordlist", 0, "%s", e); return; }
    static const char *req[] = {"word_id", "label", "romanisation", "meaning_en", "role", "pair_id", "contrast_type",
                                "review_status", "reviewer_1", "reviewer_2", "variants"};
    int ok = 1;
    require_cols(&c, "wordlist", req, 11, iss, &ok);
    if (!ok) { s2w_csv_free(&c); return; }
    int col[11];
    for (int k = 0; k < 11; k++) col[k] = s2w_csv_col(&c, req[k]);
    size_t n = c.n_rows - 1;
    ds->w = calloc(n ? n : 1, sizeof *ds->w);
    if (!ds->w) { s2w_csv_free(&c); return; }
    for (size_t r = 1; r < c.n_rows; r++) {
        size_t ln = c.line[r];
        s2w_word *w = &ds->w[ds->nw];
        memset(w, 0, sizeof *w);
        w->line = ln;
        const char *v[11];
        for (int k = 0; k < 11; k++) v[k] = s2w_csv_at(&c, r, col[k]);
        int good = 1;
        if (!valid_id(v[0]) || !cpy(w->id, sizeof w->id, v[0])) { ERR("wordlist", ln, "invalid word_id '%s'", v[0]); good = 0; }
        if (!v[1][0] || !plain_text(v[1], sizeof w->label) || !cpy(w->label, sizeof w->label, v[1])) { ERR("wordlist", ln, "label empty, too long or not valid UTF-8 text"); good = 0; }
        if (!plain_text(v[2], sizeof w->roman) || !plain_text(v[3], sizeof w->meaning) || !plain_text(v[10], sizeof w->variants)) {
            ERR("wordlist", ln, "romanisation/meaning/variants too long or not valid UTF-8 text"); good = 0;
        } else { cpy(w->roman, sizeof w->roman, v[2]); cpy(w->meaning, sizeof w->meaning, v[3]); cpy(w->variants, sizeof w->variants, v[10]); }
        if (!strcmp(v[4], "vocab")) w->role = S2W_ROLE_VOCAB;
        else if (!strcmp(v[4], "oov")) w->role = S2W_ROLE_OOV;
        else { ERR("wordlist", ln, "role must be vocab or oov"); good = 0; }
        if (v[5][0] && (!valid_id(v[5]) || !cpy(w->pair_id, sizeof w->pair_id, v[5]))) { ERR("wordlist", ln, "invalid pair_id"); good = 0; }
        int ct = -1;
        for (int k = 0; k < 4; k++) if (!strcmp(v[6], S2W_CONTRAST_NAME[k])) ct = k;
        if (ct < 0) { ERR("wordlist", ln, "invalid contrast_type '%s'", v[6]); good = 0; } else w->contrast = ct;
        int rv = -1;
        for (int k = 0; k < 3; k++) if (!strcmp(v[7], S2W_REVIEW_NAME[k])) rv = k;
        if (rv < 0) { ERR("wordlist", ln, "invalid review_status '%s'", v[7]); good = 0; } else w->review = rv;
        for (int k = 8; k <= 9; k++)
            if (v[k][0] && !valid_id(v[k])) { ERR("wordlist", ln, "invalid reviewer id"); good = 0; }
        cpy(w->reviewer1, sizeof w->reviewer1, v[8]); cpy(w->reviewer2, sizeof w->reviewer2, v[9]);
        if (good && w->review == S2W_REVIEW_VERIFIED && (!w->reviewer1[0] || !w->reviewer2[0] || !strcmp(w->reviewer1, w->reviewer2)))
            ERR("wordlist", ln, "'verified' needs two distinct reviewers");
        if (good && w->pair_id[0] == 0 && w->contrast != S2W_CT_NONE) ERR("wordlist", ln, "contrast_type set but pair_id empty");
        if (good && w->pair_id[0] && w->contrast == S2W_CT_NONE) ERR("wordlist", ln, "pair_id set but contrast_type is none");
        if (good) ds->nw++;
        else ds->nw++, ds->w[ds->nw - 1].id[0] = 0; /* keep indexing aligned; row is invalid */
    }
    /* uniqueness, pairs */
    keyed *k = malloc((ds->nw ? ds->nw : 1) * sizeof *k);
    size_t nk = 0;
    for (size_t i = 0; i < ds->nw; i++)
        if (ds->w[i].id[0]) { snprintf(k[nk].key, sizeof k[nk].key, "%s", ds->w[i].id); k[nk++].idx = i; }
    struct { s2w_dataset *d; s2w_issues *i; } ctx = {ds, iss};
    dup_scan(k, nk, cb_dup_word, &ctx);
    free(k);
    for (size_t i = 0; i < ds->nw; i++) {
        const s2w_word *a = &ds->w[i];
        if (!a->id[0] || !a->pair_id[0]) continue;
        size_t cnt = 0;
        const s2w_word *partner = NULL;
        for (size_t j = 0; j < ds->nw; j++)
            if (ds->w[j].id[0] && !strcmp(ds->w[j].pair_id, a->pair_id)) { cnt++; if (j != i) partner = &ds->w[j]; }
        if (cnt != 2) { ERR("wordlist", a->line, "pair_id '%s' must link exactly 2 words (found %zu)", a->pair_id, cnt); continue; }
        if (a->role != S2W_ROLE_VOCAB || partner->role != S2W_ROLE_VOCAB) ERR("wordlist", a->line, "pair '%s' must contain only vocab words", a->pair_id);
        if (partner->contrast != a->contrast) ERR("wordlist", a->line, "pair '%s' words disagree on contrast_type", a->pair_id);
    }
    int any_vocab = 0;
    for (size_t i = 0; i < ds->nw; i++) any_vocab |= ds->w[i].id[0] && ds->w[i].role == S2W_ROLE_VOCAB;
    if (!any_vocab) ERR("wordlist", 0, "no vocab words");
    s2w_csv_free(&c);
}

int s2w_dataset_find_word(const s2w_dataset *ds, const char *id) {
    for (size_t i = 0; i < ds->nw; i++) if (ds->w[i].id[0] && !strcmp(ds->w[i].id, id)) return (int)i;
    return -1;
}
const s2w_participant *s2w_dataset_participant(const s2w_dataset *ds, const char *sp) {
    for (size_t i = 0; i < ds->np; i++) if (!strcmp(ds->p[i].id, sp)) return &ds->p[i];
    return NULL;
}

static void parse_participants(const char *dir, s2w_dataset *ds, s2w_issues *iss, const s2w_dataset_options *opt) {
    char path[600], e[200];
    snprintf(path, sizeof path, "%s/participants.csv", dir);
    FILE *f = fopen(path, "rb");
    if (!f) {
        if (opt && opt->require_participants) ERR("participants", 0, "participants.csv missing but required");
        else if (!ds->synthetic) WARN("participants", 0, "participants.csv missing: no consent record, no group breakdowns");
        return;
    }
    fclose(f);
    s2w_csv c;
    if (s2w_csv_load(path, "#s2w-participants v1", &c, e, sizeof e) != S2W_OK) { ERR("participants", 0, "%s", e); return; }
    static const char *req[] = {"speaker_id", "consent_version", "consent_date", "group_reporting_ok", "sex_self_report", "age_band", "accent_region"};
    int ok = 1;
    require_cols(&c, "participants", req, 7, iss, &ok);
    if (!ok) { s2w_csv_free(&c); return; }
    int col[7];
    for (int k = 0; k < 7; k++) col[k] = s2w_csv_col(&c, req[k]);
    ds->has_participants = 1;
    ds->p = calloc(c.n_rows, sizeof *ds->p);
    for (size_t r = 1; r < c.n_rows; r++) {
        size_t ln = c.line[r];
        s2w_participant *p = &ds->p[ds->np];
        memset(p, 0, sizeof *p);
        p->line = ln;
        const char *v[7];
        for (int k = 0; k < 7; k++) v[k] = s2w_csv_at(&c, r, col[k]);
        if (!valid_id(v[0]) || !cpy(p->id, sizeof p->id, v[0])) { ERR("participants", ln, "invalid speaker_id"); continue; }
        if (s2w_dataset_participant(ds, p->id)) { ERR("participants", ln, "duplicate speaker_id '%s'", p->id); continue; }
        if (!v[1][0] || !valid_id(v[1]) || !cpy(p->consent_version, sizeof p->consent_version, v[1])) ERR("participants", ln, "consent_version missing or invalid");
        if (!valid_date(v[2]) || !cpy(p->consent_date, sizeof p->consent_date, v[2])) ERR("participants", ln, "consent_date must be YYYY-MM-DD");
        if (!yesno(v[3], &p->group_ok)) ERR("participants", ln, "group_reporting_ok must be yes or no");
        for (int k = 4; k <= 6; k++) if (!plain_text(v[k], S2W_ID_MAX)) ERR("participants", ln, "attribute too long or invalid text");
        cpy(p->sex, sizeof p->sex, v[4]); cpy(p->age, sizeof p->age, v[5]); cpy(p->accent, sizeof p->accent, v[6]);
        ds->np++;
    }
    s2w_csv_free(&c);
}

typedef struct { char id[S2W_ID_MAX]; char owner[S2W_ID_MAX]; int split; size_t line; } seen_t;

static void cb_dup_id(void *cx, size_t a, size_t b) {
    struct { s2w_dataset *d; s2w_issues *i; const char *what; } *c = cx;
    s2w_issue_add(c->i, 1, "recordings", c->d->r[b].line, "duplicate %s (first at line %zu)", c->what, c->d->r[a].line);
}

static void parse_recordings(const char *dir, s2w_dataset *ds, s2w_issues *iss, const s2w_dataset_options *opt) {
    char path[600], e[200];
    snprintf(path, sizeof path, "%s/recordings.csv", dir);
    s2w_csv c;
    if (s2w_csv_load(path, "#s2w-recordings v1", &c, e, sizeof e) != S2W_OK) { ERR("recordings", 0, "%s", e); return; }
    static const char *req[] = {"recording_id", "path", "word_id", "speaker_id", "session_id", "repetition", "split", "device_id",
                                "environment", "noise_condition", "speaking_style", "label_verified", "audio_sha256"};
    int ok = 1;
    require_cols(&c, "recordings", req, 13, iss, &ok);
    if (!ok) { s2w_csv_free(&c); return; }
    int col[13];
    for (int k = 0; k < 13; k++) col[k] = s2w_csv_col(&c, req[k]);
    ds->r = calloc(c.n_rows, sizeof *ds->r);
    for (size_t r = 1; r < c.n_rows; r++) {
        size_t ln = c.line[r];
        s2w_rec *x = &ds->r[ds->nr];
        memset(x, 0, sizeof *x);
        x->line = ln; x->word = -1;
        const char *v[13];
        for (int k = 0; k < 13; k++) v[k] = s2w_csv_at(&c, r, col[k]);
        int good = 1;
        if (!valid_id(v[0]) || !cpy(x->id, sizeof x->id, v[0])) { ERR("recordings", ln, "invalid recording_id"); good = 0; }
        if (!valid_path(v[1]) || !cpy(x->path, sizeof x->path, v[1])) { ERR("recordings", ln, "invalid path '%s' (relative, '/', no '..', must end in .wav)", v[1]); good = 0; }
        x->word = s2w_dataset_find_word(ds, v[2]);
        if (x->word < 0) { ERR("recordings", ln, "unknown word_id '%s'", v[2]); good = 0; }
        if (!valid_id(v[3]) || !cpy(x->speaker, sizeof x->speaker, v[3])) { ERR("recordings", ln, "invalid speaker_id"); good = 0; }
        if (!valid_id(v[4]) || !cpy(x->session, sizeof x->session, v[4])) { ERR("recordings", ln, "invalid session_id"); good = 0; }
        char *end;
        long rep = strtol(v[5], &end, 10);
        if (!v[5][0] || *end || rep < 1 || rep > 1000) { ERR("recordings", ln, "repetition must be an integer 1..1000"); good = 0; } else x->rep = (int)rep;
        int sp = -1;
        for (int k = 0; k < 3; k++) if (!strcmp(v[6], S2W_SPLIT_NAME[k])) sp = k;
        if (sp < 0) { ERR("recordings", ln, "invalid split '%s'", v[6]); good = 0; } else x->split = sp;
        if (!valid_id(v[7]) || !cpy(x->device, sizeof x->device, v[7])) { ERR("recordings", ln, "invalid device_id"); good = 0; }
        if (!strcmp(v[8], "clean")) x->noisy = 0; else if (!strcmp(v[8], "noisy")) x->noisy = 1;
        else { ERR("recordings", ln, "environment must be clean or noisy"); good = 0; }
        if (!plain_text(v[9], S2W_ID_MAX) || !plain_text(v[10], S2W_ID_MAX)) { ERR("recordings", ln, "noise_condition/speaking_style invalid"); good = 0; }
        else { cpy(x->noise_condition, sizeof x->noise_condition, v[9]); cpy(x->style, sizeof x->style, v[10]); }
        if (good && x->noisy && (!x->noise_condition[0] || !strcmp(x->noise_condition, "none"))) ERR("recordings", ln, "noisy recording needs a noise_condition");
        if (good && !x->noisy && x->noise_condition[0] && strcmp(x->noise_condition, "none")) WARN("recordings", ln, "clean recording has noise_condition '%s'", x->noise_condition);
        if (!yesno(v[11], &x->label_verified)) { ERR("recordings", ln, "label_verified must be yes or no"); good = 0; }
        if (v[12][0]) {
            int hex = strlen(v[12]) == 64;
            for (const char *q = v[12]; hex && *q; q++) hex = isdigit((unsigned char)*q) || (*q >= 'a' && *q <= 'f');
            if (!hex) { ERR("recordings", ln, "audio_sha256 must be 64 lowercase hex chars"); good = 0; } else cpy(x->sha256, sizeof x->sha256, v[12]);
        }
        if (!good) x->id[0] = 0, x->word = -1;
        ds->nr++;
    }
    size_t n = ds->nr;
    if (n == 0) ERR("recordings", 0, "no recordings");
    keyed *k = malloc((n ? n : 1) * sizeof *k);
    struct { s2w_dataset *d; s2w_issues *i; const char *what; } cx = {ds, iss, ""};
    for (int pass = 0; pass < 3; pass++) {
        size_t nk = 0;
        for (size_t i = 0; i < n; i++) {
            const s2w_rec *x = &ds->r[i];
            if (!x->id[0]) continue;
            if (pass == 0) snprintf(k[nk].key, sizeof k[nk].key, "%s", x->id);
            else if (pass == 1) snprintf(k[nk].key, sizeof k[nk].key, "%s", x->path);
            else snprintf(k[nk].key, sizeof k[nk].key, "%s|%s|%s|%d", x->speaker, x->session, ds->w[x->word].id, x->rep);
            k[nk++].idx = i;
        }
        cx.what = pass == 0 ? "recording_id" : pass == 1 ? "path" : "(speaker,session,word,repetition)";
        dup_scan(k, nk, cb_dup_id, &cx);
    }
    free(k);

    /* split isolation */
    seen_t *spk = calloc(n ? n : 1, sizeof *spk), *ses = calloc(n ? n : 1, sizeof *ses);
    size_t nspk = 0, nses = 0;
    for (size_t i = 0; i < n; i++) {
        const s2w_rec *x = &ds->r[i];
        if (!x->id[0]) continue;
        size_t j;
        for (j = 0; j < nspk; j++) if (!strcmp(spk[j].id, x->speaker)) break;
        if (j == nspk) { snprintf(spk[nspk].id, S2W_ID_MAX, "%s", x->speaker); spk[nspk].split = x->split; spk[nspk++].line = x->line; }
        else if (spk[j].split != x->split)
            ERR("recordings", x->line, "LEAKAGE: speaker '%s' appears in splits '%s' and '%s' (first at line %zu)", x->speaker, S2W_SPLIT_NAME[spk[j].split], S2W_SPLIT_NAME[x->split], spk[j].line);
        for (j = 0; j < nses; j++) if (!strcmp(ses[j].id, x->session)) break;
        if (j == nses) { snprintf(ses[nses].id, S2W_ID_MAX, "%s", x->session); snprintf(ses[nses].owner, S2W_ID_MAX, "%s", x->speaker); ses[nses].split = x->split; ses[nses++].line = x->line; }
        else {
            if (strcmp(ses[j].owner, x->speaker)) ERR("recordings", x->line, "LEAKAGE: session '%s' used by speakers '%s' and '%s' (first at line %zu)", x->session, ses[j].owner, x->speaker, ses[j].line);
            if (ses[j].split != x->split) ERR("recordings", x->line, "LEAKAGE: session '%s' appears in splits '%s' and '%s'", x->session, S2W_SPLIT_NAME[ses[j].split], S2W_SPLIT_NAME[x->split]);
        }
        const s2w_word *w = &ds->w[x->word];
        if (x->split == S2W_SPLIT_ENROL && w->role == S2W_ROLE_OOV) ERR("recordings", x->line, "OOV word '%s' must not appear in the enrol split", w->id);
    }
    free(spk); free(ses);

    /* every vocab word tested/calibrated needs enrolment */
    for (size_t wi = 0; wi < ds->nw; wi++) {
        if (!ds->w[wi].id[0] || ds->w[wi].role != S2W_ROLE_VOCAB) continue;
        int enrol = 0, query = 0;
        size_t first_query_line = 0;
        for (size_t i = 0; i < n; i++)
            if (ds->r[i].id[0] && ds->r[i].word == (int)wi) {
                if (ds->r[i].split == S2W_SPLIT_ENROL) enrol = 1;
                else if (!query) { query = 1; first_query_line = ds->r[i].line; }
            }
        if (query && !enrol) ERR("recordings", first_query_line, "vocab word '%s' has calib/test recordings but no enrolment recording", ds->w[wi].id);
        if (!query && !enrol) WARN("wordlist", ds->w[wi].line, "vocab word '%s' has no recordings", ds->w[wi].id);
    }

    /* participants cross-check */
    if (ds->has_participants) {
        for (size_t i = 0; i < n; i++) {
            const s2w_rec *x = &ds->r[i];
            if (x->id[0] && !s2w_dataset_participant(ds, x->speaker)) { ERR("recordings", x->line, "speaker '%s' has no row in participants.csv", x->speaker); break; }
        }
        for (size_t p = 0; p < ds->np; p++) {
            int used = 0;
            for (size_t i = 0; i < n && !used; i++) used = !strcmp(ds->r[i].speaker, ds->p[p].id);
            if (!used) WARN("participants", ds->p[p].line, "participant '%s' has no recordings", ds->p[p].id);
        }
    }

    /* verification state */
    size_t unverified_words = 0, unverified_labels = 0;
    unsigned char *seen_w = calloc(ds->nw ? ds->nw : 1, 1);
    for (size_t i = 0; i < n; i++) {
        const s2w_rec *x = &ds->r[i];
        if (!x->id[0]) continue;
        if (!x->label_verified) unverified_labels++;
        if (!seen_w[x->word] && ds->w[x->word].review != S2W_REVIEW_VERIFIED) { seen_w[x->word] = 1; unverified_words++; }
    }
    free(seen_w);
    if (unverified_words || unverified_labels) {
        int as_err = opt && opt->require_verified;
        s2w_issue_add(iss, as_err, "recordings", 0, "%zu used word(s) not 'verified' and %zu recording(s) with label_verified=no: results are PROVISIONAL, not ground truth", unverified_words, unverified_labels);
    }
    s2w_csv_free(&c);
}

void s2w_dataset_free(s2w_dataset *ds) {
    free(ds->w); free(ds->r); free(ds->p);
    memset(ds, 0, sizeof *ds);
}

static void hash_file(s2w_sha256 *h, const char *dir, const char *name) {
    char path[600];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    size_t len = 0;
    char *b = read_all(path, &len);
    char hdr[160];
    snprintf(hdr, sizeof hdr, "FILE:%s:%zu\n", b ? name : "ABSENT", len);
    if (!b) snprintf(hdr, sizeof hdr, "FILE:%s:ABSENT\n", name);
    s2w_sha256_update(h, hdr, strlen(hdr));
    if (b) s2w_sha256_update(h, b, len);
    free(b);
}

s2w_status s2w_dataset_load(const char *dir, const s2w_dataset_options *opt, s2w_dataset *ds, s2w_issues *iss) {
    memset(ds, 0, sizeof *ds);
    if (!dir || strlen(dir) >= sizeof ds->dir) return S2W_ERR_BAD_PARAMS;
    snprintf(ds->dir, sizeof ds->dir, "%s", dir);
    size_t before = iss->n_err;
    parse_meta(dir, ds, iss);
    parse_words(dir, ds, iss);
    parse_participants(dir, ds, iss, opt);
    if (ds->nw && ds->w) parse_recordings(dir, ds, iss, opt);
    else ERR("recordings", 0, "not checked: word list invalid");
    s2w_sha256 h;
    s2w_sha256_init(&h);
    hash_file(&h, dir, "dataset_meta.txt"); hash_file(&h, dir, "wordlist.csv");
    hash_file(&h, dir, "recordings.csv"); hash_file(&h, dir, "participants.csv");
    s2w_sha256_hex(&h, ds->manifest_sha256);
    if (iss->n_err != before) {
        s2w_dataset_free(ds);
        return S2W_ERR_BAD_PARAMS;
    }
    return S2W_OK;
}

s2w_status s2w_dataset_audio_path(const s2w_dataset *ds, const s2w_rec *r, char *out, size_t n) {
    int k = snprintf(out, n, "%s/%s", ds->dir, r->path);
    return (k > 0 && (size_t)k < n) ? S2W_OK : S2W_ERR_TOO_LARGE;
}

s2w_status s2w_dataset_check_audio(const s2w_dataset *ds, s2w_issues *iss, s2w_audio_summary *sum, FILE *emit) {
    memset(sum, 0, sizeof *sum);
    keyed *hk = malloc((ds->nr ? ds->nr : 1) * sizeof *hk);
    size_t nh = 0;
    for (size_t i = 0; i < ds->nr; i++) {
        const s2w_rec *x = &ds->r[i];
        char path[900], hex[65];
        if (s2w_dataset_audio_path(ds, x, path, sizeof path) != S2W_OK) { ERR("audio", x->line, "path too long"); continue; }
        sum->n_checked++;
        if (s2w_sha256_file_hex(path, hex) != 0) { ERR("audio", x->line, "%s: cannot read file '%s'", x->id, x->path); sum->n_decode_fail++; continue; }
        if (emit) fprintf(emit, "%s,%s\n", x->id, hex);
        if (!x->sha256[0]) { sum->n_unpinned++; }
        else if (strcmp(x->sha256, hex)) { sum->n_hash_mismatch++; ERR("audio", x->line, "%s: audio_sha256 mismatch (file changed since pinning)", x->id); }
        snprintf(hk[nh].key, sizeof hk[nh].key, "%s", hex); hk[nh++].idx = i;
        s2w_audio a;
        s2w_status st = s2w_wav_load(path, &a);
        if (st != S2W_OK) { ERR("audio", x->line, "%s: %s", x->id, s2w_status_str(st)); sum->n_decode_fail++; continue; }
        s2w_stats stt;
        st = s2w_audio_stats(&a, &stt);
        if (st != S2W_OK) { ERR("audio", x->line, "%s: %s", x->id, s2w_status_str(st)); sum->n_decode_fail++; s2w_audio_free(&a); continue; }
        sum->n_decoded++;
        if (a.sample_rate < S2W_TARGET_RATE) sum->n_below_16k++;
        for (int b = 0; b < 8; b++) if (stt.warnings & (1u << b)) sum->warn_count[b]++;
        if (stt.warnings & S2W_WARN_TOO_SHORT) ERR("audio", x->line, "%s: unusable, speech span under 0.15 s", x->id);
        if (stt.warnings & S2W_WARN_MOSTLY_SILENT) ERR("audio", x->line, "%s: unusable, mostly silent", x->id);
        unsigned soft = stt.warnings & ~(unsigned)(S2W_WARN_TOO_SHORT | S2W_WARN_MOSTLY_SILENT);
        if (soft) WARN("audio", x->line, "%s: quality warnings 0x%02x (clip=1 quiet=2 long=8 dc=32 lowrate=64)", x->id, soft);
        s2w_audio_free(&a);
    }
    qsort(hk, nh, sizeof *hk, cmp_keyed);
    for (size_t i = 1; i < nh; i++)
        if (!strcmp(hk[i].key, hk[i - 1].key)) {
            sum->n_duplicate_audio++;
            ERR("audio", ds->r[hk[i].idx].line, "LEAKAGE/DUPLICATE: identical audio bytes as %s (line %zu)", ds->r[hk[i - 1].idx].id, ds->r[hk[i - 1].idx].line);
        }
    free(hk);
    if (sum->n_unpinned) WARN("audio", 0, "%zu recording(s) have no audio_sha256: dataset content not pinned (use --emit-hashes)", sum->n_unpinned);
    return iss->n_err ? S2W_ERR_BAD_PARAMS : S2W_OK;
}
