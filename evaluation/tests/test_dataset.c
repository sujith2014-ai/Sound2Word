#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_csv.h"
#include "s2w_dataset.h"
#include "s2w_sha256.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static char FX[200], FXP[200], WORK[220];
static const char *SRC;

static char *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); long l = ftell(f); fseek(f, 0, SEEK_SET);
    char *b = malloc((size_t)l + 1);
    if (fread(b, 1, (size_t)l, f) != (size_t)l) { free(b); fclose(f); return NULL; }
    b[l] = 0; fclose(f); *n = (size_t)l;
    return b;
}
static void spit(const char *path, const char *data, size_t n) {
    FILE *f = fopen(path, "wb");
    fwrite(data, 1, n, f);
    fclose(f);
}
/* Fresh working copy of the fixture: CSVs copied, audio symlinked. */
static void fresh(const char *name) {
    char cmd[2048];
    snprintf(WORK, sizeof WORK, "%s_%s", SRC ? SRC : FX, name);
    const char *src = SRC ? SRC : FX;
    snprintf(cmd, sizeof cmd, "rm -rf '%s' && mkdir '%s' && cp '%s'/*.csv '%s'/dataset_meta.txt '%s'/ && ln -s \"$(cd '%s' && pwd)/audio\" '%s/audio'", WORK, WORK, src, src, WORK, src, WORK);
    if (system(cmd)) { puts("fixture copy failed"); exit(2); }
}
/* Replace first occurrence of find in WORK/file. */
static void edit(const char *file, const char *find, const char *repl) {
    char p[700]; size_t n;
    snprintf(p, sizeof p, "%s/%s", WORK, file);
    char *b = slurp(p, &n);
    char *at = b ? strstr(b, find) : NULL;
    if (!at) { printf("edit: '%s' not found in %s\n", find, file); failures++; free(b); return; }
    FILE *f = fopen(p, "wb");
    fwrite(b, 1, (size_t)(at - b), f); fputs(repl, f); fputs(at + strlen(find), f);
    fclose(f); free(b);
}
static void drop_lines(const char *file, const char *needle) {
    char p[700]; size_t n;
    snprintf(p, sizeof p, "%s/%s", WORK, file);
    char *b = slurp(p, &n);
    FILE *f = fopen(p, "wb");
    for (char *l = b, *nx; *l; l = nx) {
        nx = strchr(l, '\n'); nx = nx ? nx + 1 : l + strlen(l);
        if (!memmem(l, (size_t)(nx - l), needle, strlen(needle))) fwrite(l, 1, (size_t)(nx - l), f);
    }
    fclose(f); free(b);
}
/* Load the WORK dir; return status and whether any error message contains `needle`. */
static int load_has(const char *needle, const s2w_dataset_options *opt, s2w_status *st_out) {
    s2w_dataset ds; s2w_issues iss = {0};
    s2w_status st = s2w_dataset_load(WORK, opt, &ds, &iss);
    int hit = 0;
    for (size_t i = 0; i < iss.n; i++) if (iss.v[i].is_error && strstr(iss.v[i].msg, needle)) hit = 1;
    if (st == S2W_OK) s2w_dataset_free(&ds);
    s2w_issues_free(&iss);
    if (st_out) *st_out = st;
    return hit;
}
#define EXPECT_ERR(name, needle) do { s2w_status st_; int h_ = load_has(needle, NULL, &st_); \
    if (!h_ || st_ == S2W_OK) { printf("FAIL %s:%d: case '%s' did not report '%s'\n", __FILE__, __LINE__, name, needle); failures++; } } while (0)

static void test_sha256(void) {
    char h[65];
    s2w_sha256_buf_hex("abc", 3, h);
    CHECK(!strcmp(h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    s2w_sha256_buf_hex("", 0, h);
    CHECK(!strcmp(h, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    const char *two = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    s2w_sha256_buf_hex(two, strlen(two), h);
    CHECK(!strcmp(h, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"));
    char *mil = malloc(1000000); memset(mil, 'a', 1000000);
    s2w_sha256 c; s2w_sha256_init(&c);
    for (int i = 0; i < 1000000; i += 777) s2w_sha256_update(&c, mil + i, (i + 777 > 1000000) ? 1000000 - i : 777);
    s2w_sha256_hex(&c, h); free(mil);
    CHECK(!strcmp(h, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"));
    CHECK(s2w_sha256_file_hex("/nonexistent/file", h) == -1);
}

static void test_csv(void) {
    char p[600], err[200];
    snprintf(p, sizeof p, "%s_csv.csv", FX);
    s2w_csv c;
    const char *ok = "#t v1\r\na,b,c\r\n1,\"x,y\",\"say \"\"hi\"\"\"\r\n\r\n4,5,\r\n";
    spit(p, ok, strlen(ok));
    CHECK(s2w_csv_load(p, "#t v1", &c, err, sizeof err) == S2W_OK);
    CHECK(c.n_rows == 3 && c.n_cols == 3 && s2w_csv_col(&c, "b") == 1 && s2w_csv_col(&c, "z") == -1);
    CHECK(!strcmp(s2w_csv_at(&c, 1, 1), "x,y") && !strcmp(s2w_csv_at(&c, 1, 2), "say \"hi\"") && !strcmp(s2w_csv_at(&c, 2, 2), ""));
    CHECK(c.line[1] == 3 && c.line[2] == 5);
    s2w_csv_free(&c);
    const char *utf = "#t v1\nlabel,n\nമലയാളം,1\n";
    spit(p, utf, strlen(utf));
    CHECK(s2w_csv_load(p, "#t v1", &c, err, sizeof err) == S2W_OK && !strcmp(s2w_csv_at(&c, 1, 0), "മലയാളം"));
    s2w_csv_free(&c);
    struct { const char *txt; const char *expect; } bad[] = {
        {"#t v2\na\n1\n", "expected first line"},
        {"\xEF\xBB\xBF#t v1\na\n1\n", "BOM"},
        {"#t v1\na,b\n1\n", "wrong number of columns"},
        {"#t v1\na,b\n1,\"oops\n", "unterminated quote"},
        {"#t v1\na,b\n1,\"x\"y\n", "after closing quote"},
        {"#t v1\na,b\n1,x\"y\n", "stray quote"},
        {"#t v1\n", "missing header"},
        {"", "expected first line"},
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) {
        spit(p, bad[i].txt, strlen(bad[i].txt));
        err[0] = 0;
        CHECK(s2w_csv_load(p, "#t v1", &c, err, sizeof err) != S2W_OK && strstr(err, bad[i].expect));
    }
    spit(p, "#t v1\na\n1\0002\n", 12); /* embedded NUL */
    CHECK(s2w_csv_load(p, "#t v1", &c, err, sizeof err) != S2W_OK);
    CHECK(s2w_csv_load("/nonexistent.csv", "#t v1", &c, err, sizeof err) == S2W_ERR_IO);
}

static void test_valid(void) {
    fresh("valid");
    s2w_dataset ds; s2w_issues iss = {0};
    CHECK(s2w_dataset_load(WORK, NULL, &ds, &iss) == S2W_OK && iss.n_err == 0);
    CHECK(ds.synthetic && ds.nw == 11 && ds.nr == 294 && ds.np == 10 && ds.has_participants);
    CHECK(strlen(ds.manifest_sha256) == 64);
    char h1[65]; strcpy(h1, ds.manifest_sha256);
    CHECK(s2w_dataset_find_word(&ds, "SYNTH_V3") >= 0 && s2w_dataset_find_word(&ds, "nope") < 0);
    CHECK(s2w_dataset_participant(&ds, "spkE1") && !s2w_dataset_participant(&ds, "zzz"));
    s2w_dataset_free(&ds); s2w_issues_free(&iss);
    s2w_dataset_load(WORK, NULL, &ds, &iss);
    CHECK(!strcmp(h1, ds.manifest_sha256)); /* stable */
    s2w_dataset_free(&ds); s2w_issues_free(&iss);
    edit("wordlist.csv", "synthetic tone sequence", "synthetic tone sequenCe");
    s2w_dataset_load(WORK, NULL, &ds, &iss);
    CHECK(strcmp(h1, ds.manifest_sha256) != 0); /* changes with content */
    s2w_dataset_free(&ds); s2w_issues_free(&iss);
}

static void test_structure_errors(void) {
    fresh("a"); edit("recordings.csv", ",spkC1,spkC1-s1,1,calib", ",spkE1,spkC1-s1,1,calib"); EXPECT_ERR("speaker in two splits", "LEAKAGE: speaker 'spkE1'");
    fresh("b"); edit("recordings.csv", "spkE2,spkE2-s1", "spkE2,spkE1-s1"); EXPECT_ERR("session shared by speakers", "LEAKAGE: session 'spkE1-s1' used by speakers");
    fresh("c"); edit("recordings.csv", ",SYNTH_V1,spkE1,spkE1-s1,1,enrol", ",SYNTH_O1,spkE1,spkE1-s1,1,enrol"); EXPECT_ERR("OOV enrolled", "must not appear in the enrol split");
    fresh("d"); edit("recordings.csv", "rec0002,", "rec0001,"); EXPECT_ERR("dup recording id", "duplicate recording_id");
    fresh("e"); edit("recordings.csv", "audio/spkE1_SYNTH_V1_2.wav", "audio/spkE1_SYNTH_V1_1.wav"); EXPECT_ERR("dup path", "duplicate path");
    fresh("f"); edit("recordings.csv", "spkE1,spkE1-s1,2,", "spkE1,spkE1-s1,1,"); EXPECT_ERR("dup key", "duplicate (speaker,session,word,repetition)");
    fresh("g"); edit("recordings.csv", "audio/spkE1_SYNTH_V1_1.wav", "../escape.wav"); EXPECT_ERR("path traversal", "invalid path");
    fresh("h"); edit("recordings.csv", "audio/spkE1_SYNTH_V1_1.wav", "/etc/passwd.wav"); EXPECT_ERR("absolute path", "invalid path");
    fresh("i"); edit("recordings.csv", ",SYNTH_V1,spkE1,", ",NOPE,spkE1,"); EXPECT_ERR("unknown word", "unknown word_id");
    fresh("j"); edit("recordings.csv", ",enrol,dev-a", ",train,dev-a"); EXPECT_ERR("bad split", "invalid split");
    fresh("k"); edit("recordings.csv", ",clean,", ",quiet,"); EXPECT_ERR("bad environment", "environment must be");
    fresh("l"); edit("recordings.csv", "spkE1-s1,1,enrol", "spkE1-s1,0,enrol"); EXPECT_ERR("bad repetition", "repetition must be");
    fresh("m"); edit("recordings.csv", ",yes,", ",maybe,"); EXPECT_ERR("bad label_verified", "label_verified must be");
    fresh("n"); edit("recordings.csv", "enrol,dev-a,clean,,normal,yes,", "enrol,dev-a,clean,,normal,yes,zz"); EXPECT_ERR("bad sha", "audio_sha256 must be");
    fresh("o"); drop_lines("recordings.csv", ",SYNTH_V1,spkE"); EXPECT_ERR("vocab without enrolment", "no enrolment recording");
    fresh("p"); drop_lines("participants.csv", "spkT3"); EXPECT_ERR("speaker not in participants", "no row in participants.csv");
    fresh("q"); edit("recordings.csv", ",clean,,normal", ",noisy,,normal"); EXPECT_ERR("noisy needs condition", "needs a noise_condition");
    fresh("r"); edit("recordings.csv", "environment,", "env,"); EXPECT_ERR("missing column", "missing column 'environment'");
    fresh("s"); edit("recordings.csv", "audio_sha256", "audio_sha256,extra"); EXPECT_ERR("unknown/ragged", "wrong number of columns");
    fresh("t"); edit("recordings.csv", "#s2w-recordings v1", "#s2w-recordings v2"); EXPECT_ERR("wrong tag", "expected first line");
}

static void test_wordlist_errors(void) {
    fresh("w1"); edit("wordlist.csv", "SYNTH_V2,", "SYNTH_V1,"); EXPECT_ERR("dup word id", "duplicate word_id");
    fresh("w2"); edit("wordlist.csv", "verified,rev-a,rev-b", "verified,rev-a,rev-a"); EXPECT_ERR("same reviewer", "two distinct reviewers");
    fresh("w3"); edit("wordlist.csv", "verified,rev-a,rev-b", "verified,rev-a,"); EXPECT_ERR("one reviewer", "two distinct reviewers");
    fresh("w4"); edit("wordlist.csv", "SYNTH_V1,SYNTH_V1,SYNTH_V1,synthetic tone sequence,vocab,,none", "SYNTH_V1,SYNTH_V1,SYNTH_V1,synthetic tone sequence,vocab,PAIR_A,vowel_length"); EXPECT_ERR("pair of three", "must link exactly 2 words");
    fresh("w5"); edit("wordlist.csv", "vocab,,none", "vocab,,gemination"); EXPECT_ERR("contrast without pair", "pair_id empty");
    fresh("w6"); edit("wordlist.csv", "vocab,PAIR_A,vowel_length", "vocab,PAIR_A,none"); EXPECT_ERR("pair none contrast", "contrast_type is none");
    fresh("w7"); edit("wordlist.csv", "oov,,none", "mystery,,none"); EXPECT_ERR("bad role", "role must be");
    fresh("w8"); edit("wordlist.csv", "SYNTH_V7,SYNTH_V7", "SYNTH_V7,SYNTH\x01V7"); EXPECT_ERR("control char label", "not valid UTF-8");
    fresh("w9"); edit("wordlist.csv", "SYNTH_V8,SYNTH_V8", "SYNTH_V8,\xff\xfe"); EXPECT_ERR("invalid utf8", "not valid UTF-8");
    fresh("w10"); edit("wordlist.csv", "verified", "approved"); EXPECT_ERR("bad review status", "invalid review_status");
    fresh("w11"); edit("wordlist.csv", "SYNTH_O1,SYNTH_O1,SYNTH_O1,synthetic tone sequence,oov,", "SYNTH_O1,SYNTH_O1,SYNTH_O1,synthetic tone sequence,oov,PAIR_B"); EXPECT_ERR("pair with oov", "must link exactly 2 words");
    fresh("w12"); edit("wordlist.csv", "#s2w-wordlist v1", "#s2w-wordlist v9"); EXPECT_ERR("wordlist tag", "expected first line");
    fresh("mx"); edit("dataset_meta.txt", "schema_version=1", "schema_version=2"); EXPECT_ERR("schema version", "unsupported schema_version");
    fresh("my"); edit("dataset_meta.txt", "kind=synthetic", "kind=fake"); EXPECT_ERR("kind", "kind must be");
    fresh("mz"); edit("dataset_meta.txt", "protocol=speaker_independent", "color=blue"); EXPECT_ERR("unknown meta key", "unknown key");
    fresh("pc"); edit("participants.csv", "2000-01-01", "01/01/2000"); EXPECT_ERR("bad date", "consent_date");
    fresh("pd"); edit("participants.csv", "spkE2,", "spkE1,"); EXPECT_ERR("dup participant", "duplicate speaker_id");
    fresh("pe"); edit("participants.csv", ",yes,", ",sure,"); EXPECT_ERR("bad group flag", "group_reporting_ok");
}

static void test_options(void) {
    fresh("opt");
    edit("recordings.csv", ",normal,yes,", ",normal,no,"); /* one unverified label */
    s2w_dataset ds; s2w_issues iss = {0};
    CHECK(s2w_dataset_load(WORK, NULL, &ds, &iss) == S2W_OK); /* warning only */
    int prov = 0;
    for (size_t i = 0; i < iss.n; i++) prov |= !iss.v[i].is_error && strstr(iss.v[i].msg, "PROVISIONAL") != NULL;
    CHECK(prov && iss.n_warn >= 1);
    s2w_dataset_free(&ds); s2w_issues_free(&iss);
    s2w_dataset_options o = {1, 0};
    s2w_status st;
    CHECK(load_has("PROVISIONAL", &o, &st) && st != S2W_OK);
    fresh("opt2");
    char p[700]; snprintf(p, sizeof p, "%s/participants.csv", WORK); remove(p);
    s2w_dataset_options o2 = {0, 1};
    CHECK(load_has("participants.csv missing", &o2, &st) && st != S2W_OK);
    CHECK(load_has("zzz", NULL, &st) == 0 && st == S2W_OK); /* synthetic: no warning-as-error */
}


static void test_personalised_rules(void) {
    SRC = FXP;
    fresh("pvalid");
    s2w_dataset ds; s2w_issues iss = {0};
    CHECK(s2w_dataset_load(WORK, NULL, &ds, &iss) == S2W_OK && ds.personalised && ds.nr == 276 && !strcmp(ds.protocol, "personalised"));
    s2w_dataset_free(&ds); s2w_issues_free(&iss);

    fresh("p1"); edit("recordings.csv", ",pT1,pT1-S2,1,test,", ",pT1,pT1-S2,1,calib,"); EXPECT_ERR("participant both calib and test", "has both calib and test query recordings");
    fresh("p2"); drop_lines("recordings.csv", "pC4_S1"); EXPECT_ERR("query participant without own enrolment", "no enrolment recordings of their own");
    fresh("p3"); drop_lines("recordings.csv", "pC1_S1_SYNTH_V3"); EXPECT_ERR("queried word without own template", "no enrolment recording of it");
    fresh("p4"); { char cmd[600]; snprintf(cmd, sizeof cmd, "sed -i 's/,pC2,pC2-S2,/,pC2,pC2-S1,/' '%s/recordings.csv'", WORK); CHECK(system(cmd) == 0); }
    EXPECT_ERR("query session equals enrolment session", "LEAKAGE: session 'pC2-S1' appears in splits");
    fresh("p5"); edit("recordings.csv", ",SYNTH_V1,pC1,pC1-S1,1,enrol", ",SYNTH_O1,pC1,pC1-S1,1,enrol"); EXPECT_ERR("OOV enrolled", "must not appear in the enrol split");
    fresh("p6"); edit("dataset_meta.txt", "protocol=personalised", "protocol=speaker_independent"); EXPECT_ERR("personalised pattern under SI rules", "LEAKAGE: speaker");
    fresh("p7"); edit("dataset_meta.txt", "protocol=personalised", "protocol=hybrid"); EXPECT_ERR("unknown protocol", "unsupported protocol");

    /* the other direction: a speaker-independent layout is not a valid personalised dataset */
    SRC = FX;
    fresh("s1"); edit("dataset_meta.txt", "protocol=speaker_independent", "protocol=personalised"); EXPECT_ERR("SI layout under personalised rules", "no enrolment recordings of their own");
    /* and the speaker-independent leakage rules still hold */
    fresh("s2"); edit("recordings.csv", ",spkC1,spkC1-s1,1,calib", ",spkE1,spkC1-s1,1,calib"); EXPECT_ERR("SI speaker in two splits", "LEAKAGE: speaker 'spkE1'");
    SRC = NULL;
}

static void test_consent_status(void) {
    SRC = FX;
    s2w_status st;
    fresh("c1"); edit("participants.csv", "spkE1,synthetic-v1,2000-01-01,yes,f,declined,declined,consented", "spkE1,synthetic-v1,2000-01-01,yes,f,declined,declined,withdrawn");
    CHECK(load_has("WITHDRAWN consent", NULL, &st) && st != S2W_OK);
    fresh("c2"); edit("participants.csv", "declined,declined,consented", "declined,declined,pending");
    CHECK(load_has("consent_status must be", NULL, &st) && st != S2W_OK);
    /* synthetic fixtures may omit the column; real datasets may not */
    fresh("c3"); { char cmd[600]; snprintf(cmd, sizeof cmd, "sed -i 's/,consent_status//; s/,consented$//' '%s/participants.csv'", WORK); CHECK(system(cmd) == 0); }
    CHECK(load_has("zzz", NULL, &st) == 0 && st == S2W_OK);
    edit("dataset_meta.txt", "kind=synthetic", "kind=real");
    CHECK(load_has("missing column 'consent_status'", NULL, &st) && st != S2W_OK);
    fresh("c4"); edit("dataset_meta.txt", "kind=synthetic", "kind=real");
    CHECK(load_has("zzz", NULL, &st) == 0 && st == S2W_OK);                              /* real + all consented: structurally fine (provisional warnings only) */
    { char p[700]; snprintf(p, sizeof p, "%s/participants.csv", WORK); remove(p); }
    CHECK(load_has("must carry a consent record", NULL, &st) && st != S2W_OK);          /* real dataset without participants file */
    SRC = NULL;
}

static void test_audio(void) {
    char p[800];
    fresh("au1");
    s2w_dataset ds; s2w_issues iss = {0}; s2w_audio_summary sum;
    CHECK(s2w_dataset_load(WORK, NULL, &ds, &iss) == S2W_OK);
    CHECK(s2w_dataset_check_audio(&ds, &iss, &sum, NULL) == S2W_OK);
    CHECK(sum.n_checked == 294 && sum.n_decoded == 294 && sum.n_hash_mismatch == 0 && sum.n_duplicate_audio == 0);
    FILE *tmp = tmpfile();
    s2w_issues i2 = {0};
    s2w_dataset_check_audio(&ds, &i2, &sum, tmp);
    CHECK(ftell(tmp) > 294 * 60);
    fclose(tmp);
    s2w_issues_free(&i2); s2w_dataset_free(&ds); s2w_issues_free(&iss);

    /* Audio edits happen on private copies of the wav, never on the shared fixture. */
    char cmd[2048];
    fresh("au2");
    snprintf(cmd, sizeof cmd, "rm '%s/audio' && mkdir '%s/audio' && cp '%s'/audio/spkE1_SYNTH_V1_*.wav '%s'/audio/spkE1_SYNTH_V2_1.wav '%s/audio/'", WORK, WORK, FX, FX, WORK);
    CHECK(system(cmd) == 0);
    /* keep only rows whose files exist */
    {
        char rp[700]; size_t n; snprintf(rp, sizeof rp, "%s/recordings.csv", WORK);
        char *b = slurp(rp, &n); FILE *f = fopen(rp, "wb");
        for (char *l = b, *nx; *l; l = nx) {
            nx = strchr(l, '\n'); nx = nx ? nx + 1 : l + strlen(l);
            if (l == b || l == strchr(b, '\n') + 1 || memmem(l, (size_t)(nx - l), "spkE1_SYNTH_V1_", 15) || memmem(l, (size_t)(nx - l), "spkE1_SYNTH_V2_1", 16))
                fwrite(l, 1, (size_t)(nx - l), f);
        }
        fclose(f); free(b);
        /* participants / meta unchanged; vocab words other than V1/V2 now unrecorded -> warnings only */
    }
    /* 1) hash mismatch: flip a byte in audio data */
    snprintf(p, sizeof p, "%s/audio/spkE1_SYNTH_V1_1.wav", WORK);
    size_t n; char *w = slurp(p, &n); w[1000] ^= 0x55; spit(p, w, n); free(w);
    /* 2) truncated file (decode failure by hash mismatch too) */
    snprintf(p, sizeof p, "%s/audio/spkE1_SYNTH_V1_2.wav", WORK);
    w = slurp(p, &n); spit(p, w, 30); free(w);
    /* 3) silent file */
    snprintf(p, sizeof p, "%s/audio/spkE1_SYNTH_V1_3.wav", WORK);
    w = slurp(p, &n); memset(w + 44, 0, n - 44); spit(p, w, n); free(w);
    /* 4) identical bytes in two files */
    snprintf(cmd, sizeof cmd, "cp '%s/audio/spkE1_SYNTH_V2_1.wav' '%s/audio/dup.wav'", WORK, WORK);
    CHECK(system(cmd) == 0);
    {
        char rp[700]; snprintf(rp, sizeof rp, "%s/recordings.csv", WORK);
        FILE *f = fopen(rp, "ab");
        fprintf(f, "recdup,audio/dup.wav,SYNTH_V2,spkE1,spkE1-s1,9,enrol,dev-a,clean,,normal,yes,\n");
        fclose(f);
    }
    iss = (s2w_issues){0};
    CHECK(s2w_dataset_load(WORK, NULL, &ds, &iss) == S2W_OK);
    CHECK(s2w_dataset_check_audio(&ds, &iss, &sum, NULL) != S2W_OK);
    int mism = 0, trunc = 0, silent = 0, dup = 0, unpinned = 0;
    for (size_t i = 0; i < iss.n; i++) {
        const char *m = iss.v[i].msg;
        mism += !!strstr(m, "audio_sha256 mismatch");
        trunc += iss.v[i].is_error && (!!strstr(m, "truncated") || !!strstr(m, "missing") || !!strstr(m, "not a RIFF"));
        silent += !!strstr(m, "mostly silent");
        dup += !!strstr(m, "LEAKAGE/DUPLICATE");
        unpinned += !iss.v[i].is_error && !!strstr(m, "no audio_sha256");
    }
    CHECK(mism >= 2 && trunc >= 1 && silent >= 1 && dup == 1 && unpinned == 1);
    CHECK(sum.n_hash_mismatch >= 2 && sum.n_decode_fail >= 1 && sum.n_duplicate_audio == 1);
    /* the originals on disk were not touched or repaired by the checker */
    char *w2 = slurp(p, &n); CHECK(w2 && n == 44 + (n - 44)); free(w2);
    s2w_dataset_free(&ds); s2w_issues_free(&iss);
}

int main(int argc, char **argv) {
    const char *build = argc > 1 ? argv[1] : "build";
    snprintf(FX, sizeof FX, "%s/fx_dataset", build);
    snprintf(FXP, sizeof FXP, "%s/fx_dataset_pers", build);
    char cmd[1024];
    snprintf(cmd, sizeof cmd, "rm -rf '%s' && '%s/s2w_make_fixture' '%s' >/dev/null", FX, build, FX);
    if (system(cmd)) { puts("cannot generate fixture"); return 2; }
    snprintf(cmd, sizeof cmd, "rm -rf '%s' && '%s/s2w_make_fixture' '%s' personalised >/dev/null", FXP, build, FXP);
    if (system(cmd)) { puts("cannot generate personalised fixture"); return 2; }
    test_sha256(); test_csv(); test_valid(); test_structure_errors(); test_wordlist_errors(); test_options(); test_personalised_rules(); test_consent_status(); test_audio();
    puts(failures ? "FAILED" : "dataset: all tests passed");
    return failures ? 1 : 0;
}
