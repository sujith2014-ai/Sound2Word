/* s2w_ingest: add ONE recording to a dataset directory without ever altering the original.
 *
 *   s2w_ingest --in FILE.wav --dataset-dir DIR --recording-id ID --word-id ID --speaker-id ID --session-id ID
 *              --repetition N --split enrol|calib|test --device-id ID --environment clean|noisy
 *              [--noise-condition TEXT] [--style TEXT] [--label-verified yes|no]
 *              [--append-manifest] [--keep-rejected] [--created-utc STAMP]
 *
 * Writes (never overwrites; originals/derived are made read-only):
 *   DIR/audio/<id>.wav      byte-for-byte copy of the input (verified by SHA-256)  <- manifest path
 *   DIR/derived16k/<id>.wav mono PCM16 16 kHz copy (windowed-sinc) for QA/external tools; NOT used by the manifest
 *   DIR/meta/<id>.json      QA results, hashes, format, tool version (no local file paths, no personal data)
 * and prints the recordings.csv row (also appended with --append-manifest, after duplicate checks).
 * A recording whose QA verdict is "reject" is NOT added to the dataset (exit 3); with --keep-rejected
 * the untouched bytes go to DIR/quarantine/ only. QA uses the clean limits for environment=clean and
 * the noisy limits for environment=noisy. Exit: 0 ok, 3 rejected by QA, 2 error. */
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "s2w_json.h"
#include "s2w_qa.h"
#include "s2w_resample.h"
#include "s2w_sha256.h"

#define TOOL_VERSION "1"

static int die(const char *msg, const char *a) { fprintf(stderr, "error: "); fprintf(stderr, msg, a); fputc('\n', stderr); return 2; }

static int valid_id(const char *s) {
    size_t n = strlen(s);
    if (n == 0 || n > 40) return 0;
    for (; *s; s++) if (!isalnum((unsigned char)*s) && *s != '.' && *s != '_' && *s != '-') return 0;
    return 1;
}
static int valid_text(const char *s) {   /* conservative: printable ASCII except comma/quote, <= 40 */
    size_t n = strlen(s);
    if (n > 40) return 0;
    for (; *s; s++) if ((unsigned char)*s < 0x20 || (unsigned char)*s > 0x7e || *s == ',' || *s == '"') return 0;
    return 1;
}
static int exists(const char *p) { struct stat st; return stat(p, &st) == 0; }
static void mkdirs(const char *dir, const char *sub, char *out, size_t n) {
    snprintf(out, n, "%s/%s", dir, sub);
    mkdir(out, 0755);
}

/* Create a new read-only file with exclusive creation; returns 0 on success. */
static int write_new(const char *path, const void *data, size_t len) {
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0444);
    if (fd < 0) return -1;
    const unsigned char *p = data;
    size_t off = 0;
    while (off < len) {
        ssize_t w = write(fd, p + off, len - off);
        if (w <= 0) { close(fd); unlink(path); return -1; }
        off += (size_t)w;
    }
    if (fsync(fd) != 0 || close(fd) != 0) { unlink(path); return -1; }
    return 0;
}

static uint8_t *slurp(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n <= 0 || n > 512L * 1024 * 1024) { fclose(f); return NULL; }
    uint8_t *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); fclose(f); return NULL; }
    fclose(f);
    *len = (size_t)n;
    return b;
}

/* Does recordings.csv already contain this recording id / path / (speaker,session,word,rep)? */
static int manifest_conflict(const char *path, const char *id, const char *rel, const char *key) {
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    char line[4096];
    int conflict = 0;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#') continue;
        char tmp[4096];
        snprintf(tmp, sizeof tmp, "%s", line);
        char *cols[16];
        int nc = 0;
        for (char *t = strtok(tmp, ",\n"); t && nc < 16; t = strtok(NULL, ",\n")) cols[nc++] = t;
        if (nc < 7) continue;
        if (!strcmp(cols[0], id) || !strcmp(cols[1], rel)) conflict = 1;
        char k[400];
        snprintf(k, sizeof k, "%s|%s|%s|%s", cols[3], cols[4], cols[2], cols[5]);
        if (!strcmp(k, key)) conflict = 1;
    }
    fclose(f);
    return conflict;
}

int main(int argc, char **argv) {
    const char *in = NULL, *dir = NULL, *id = NULL, *word = NULL, *spk = NULL, *ses = NULL, *split = NULL, *dev = NULL, *env = NULL;
    const char *noise = "", *style = "normal", *lv = "yes", *created = NULL;
    int rep = 0, append = 0, keep_rejected = 0;
    for (int i = 1; i < argc; i++) {
        const char *k = argv[i];
        if (!strcmp(k, "--append-manifest")) { append = 1; continue; }
        if (!strcmp(k, "--keep-rejected")) { keep_rejected = 1; continue; }
        if (i + 1 >= argc) return die("option %s needs a value", k);
        const char *v = argv[++i];
        if (!strcmp(k, "--in")) in = v; else if (!strcmp(k, "--dataset-dir")) dir = v; else if (!strcmp(k, "--recording-id")) id = v;
        else if (!strcmp(k, "--word-id")) word = v; else if (!strcmp(k, "--speaker-id")) spk = v; else if (!strcmp(k, "--session-id")) ses = v;
        else if (!strcmp(k, "--split")) split = v; else if (!strcmp(k, "--device-id")) dev = v; else if (!strcmp(k, "--environment")) env = v;
        else if (!strcmp(k, "--noise-condition")) noise = v; else if (!strcmp(k, "--style")) style = v; else if (!strcmp(k, "--label-verified")) lv = v;
        else if (!strcmp(k, "--created-utc")) created = v;
        else if (!strcmp(k, "--repetition")) { char *e; rep = (int)strtol(v, &e, 10); if (!*v || *e) rep = 0; }
        else return die("unknown option %s", k);
    }
    if (!in || !dir || !id || !word || !spk || !ses || !split || !dev || !env) return die("missing required option (see usage in source header)%s", "");
    if (!valid_id(id) || !valid_id(word) || !valid_id(spk) || !valid_id(ses) || !valid_id(dev)) return die("ids must match [A-Za-z0-9._-]{1,40}%s", "");
    if (rep < 1 || rep > 1000) return die("--repetition must be 1..1000%s", "");
    if (strcmp(split, "enrol") && strcmp(split, "calib") && strcmp(split, "test")) return die("--split must be enrol, calib or test%s", "");
    int noisy = !strcmp(env, "noisy");
    if (!noisy && strcmp(env, "clean")) return die("--environment must be clean or noisy%s", "");
    if (!valid_text(noise) || !valid_text(style)) return die("noise-condition/style: printable ASCII, no commas/quotes, <= 40 chars%s", "");
    if (noisy && (!noise[0] || !strcmp(noise, "none"))) return die("noisy recordings need --noise-condition%s", "");
    if (strcmp(lv, "yes") && strcmp(lv, "no")) return die("--label-verified must be yes or no%s", "");

    struct stat sd;
    if (stat(dir, &sd) != 0 || !S_ISDIR(sd.st_mode)) return die("dataset directory does not exist: %s", dir);
    char audio_dir[700], derived_dir[700], meta_dir[700], quar_dir[700];
    mkdirs(dir, "audio", audio_dir, sizeof audio_dir); mkdirs(dir, "derived16k", derived_dir, sizeof derived_dir);
    mkdirs(dir, "meta", meta_dir, sizeof meta_dir);
    char orig[800], deriv[800], meta[800], rel[200], manifest[800];
    snprintf(rel, sizeof rel, "audio/%s.wav", id);
    snprintf(orig, sizeof orig, "%s/%s.wav", audio_dir, id);
    snprintf(deriv, sizeof deriv, "%s/%s.wav", derived_dir, id);
    snprintf(meta, sizeof meta, "%s/%s.json", meta_dir, id);
    snprintf(manifest, sizeof manifest, "%s/recordings.csv", dir);
    if (exists(orig) || exists(deriv) || exists(meta)) return die("recording id already exists in dataset: %s (originals are never overwritten)", id);
    char key[400];
    snprintf(key, sizeof key, "%s|%s|%s|%d", spk, ses, word, rep);
    if (manifest_conflict(manifest, id, rel, key)) return die("recordings.csv already has this recording id, path or (speaker,session,word,repetition): %s", id);

    size_t len;
    uint8_t *bytes = slurp(in, &len);
    if (!bytes) return die("cannot read input file (or empty/too large): %s", in);
    char sha[65];
    s2w_sha256_buf_hex(bytes, len, sha);
    s2w_audio a;
    s2w_status st = s2w_wav_decode(bytes, len, &a);
    if (st != S2W_OK) { free(bytes); return die("input is not a usable WAV: %s", s2w_status_str(st)); }
    s2w_qa_limits lim;
    if (noisy) s2w_qa_limits_noisy(&lim); else s2w_qa_limits_clean(&lim);
    s2w_qa qa;
    st = s2w_qa_analyse(&a, &lim, &qa);
    if (st != S2W_OK) { free(bytes); s2w_audio_free(&a); return die("QA could not analyse the recording: %s", s2w_status_str(st)); }
    char flags[200];
    s2w_qa_flag_names(qa.flags, flags, sizeof flags);

    if (qa.verdict == S2W_QA_REJECT) {
        fprintf(stderr, "rejected by QA (flags: %s): re-record; nothing was added to the dataset\n", flags);
        if (keep_rejected) {
            mkdirs(dir, "quarantine", quar_dir, sizeof quar_dir);
            char qp[800];
            snprintf(qp, sizeof qp, "%s/%s.wav", quar_dir, id);
            if (write_new(qp, bytes, len) == 0) fprintf(stderr, "quarantined (untouched bytes): quarantine/%s.wav\n", id);
        }
        free(bytes); s2w_audio_free(&a);
        return 3;
    }

    /* 1. original, byte for byte, verified */
    if (write_new(orig, bytes, len) != 0) { free(bytes); s2w_audio_free(&a); return die("cannot create %s (exists or not writable)", orig); }
    char chk[65];
    if (s2w_sha256_file_hex(orig, chk) != 0 || strcmp(chk, sha)) { unlink(orig); free(bytes); s2w_audio_free(&a); return die("verification of the stored original failed%s", ""); }
    free(bytes);

    /* 2. derived 16 kHz mono PCM16 */
    s2w_audio d;
    st = s2w_audio_resample(&a, S2W_TARGET_RATE, &d);
    uint8_t *db = NULL; size_t dl = 0;
    char dsha[65] = "";
    if (st == S2W_OK) st = s2w_wav_encode_pcm16(d.samples, d.n_samples, S2W_TARGET_RATE, &db, &dl);
    if (st != S2W_OK || write_new(deriv, db, dl) != 0) {
        unlink(orig); free(db); s2w_audio_free(&d); s2w_audio_free(&a);
        return die("could not create the 16 kHz derived copy%s", "");
    }
    s2w_sha256_buf_hex(db, dl, dsha);
    free(db); s2w_audio_free(&d);

    /* 3. sidecar */
    char stamp[40];
    if (created) snprintf(stamp, sizeof stamp, "%s", created);
    else { time_t t = time(NULL); struct tm g; gmtime_r(&t, &g); strftime(stamp, sizeof stamp, "%Y-%m-%dT%H:%M:%SZ", &g); }
    FILE *mf = fopen(meta, "wx");
    if (!mf) { unlink(orig); unlink(deriv); s2w_audio_free(&a); return die("cannot create %s", meta); }
    s2w_json j;
    js_init(&j, mf);
    js_obj(&j, NULL);
    js_str(&j, "schema", "s2w-recording-meta"); js_int(&j, "schema_version", 1);
    js_str(&j, "tool", "s2w_ingest " TOOL_VERSION); js_str(&j, "ingested_utc", stamp);
    js_str(&j, "recording_id", id);
    js_obj(&j, "original");
    js_str(&j, "path", rel); js_str(&j, "sha256", sha); js_int(&j, "bytes", (long long)len);
    js_int(&j, "sample_rate", a.sample_rate); js_int(&j, "channels", a.source_channels); js_int(&j, "bits", a.source_bits);
    js_int(&j, "samples_per_channel", (long long)a.n_samples);
    js_str(&j, "note", "byte-for-byte copy of the input; never modified");
    js_obj_end(&j);
    js_obj(&j, "derived");
    js_str(&j, "path", "derived16k/<recording_id>.wav"); js_str(&j, "sha256", dsha);
    js_str(&j, "format", "mono PCM16 16000 Hz");
    js_str(&j, "resampler", "windowed-sinc (Blackman, 16 lobes, cutoff 0.97 x Nyquist), channels averaged to mono");
    js_str(&j, "note", "convenience copy only; the manifest and evaluation use the original");
    js_obj_end(&j);
    js_obj(&j, "qa");
    js_str(&j, "limits", noisy ? "noisy" : "clean"); js_str(&j, "verdict", s2w_qa_verdict_name(qa.verdict)); js_str(&j, "flags", flags);
    js_num(&j, "snr_db_estimate", qa.snr_db); js_num(&j, "noise_floor_dbfs", qa.noise_floor_dbfs); js_num(&j, "speech_level_dbfs", qa.speech_level_dbfs);
    js_num(&j, "leading_s", qa.leading_s); js_num(&j, "trailing_s", qa.trailing_s); js_num(&j, "speech_s", qa.speech_s);
    js_int(&j, "clipped_run_samples", (long long)qa.clipped_run_samples); js_int(&j, "dropouts", (long long)qa.n_dropouts); js_num(&j, "dc_offset", qa.dc_offset);
    js_str(&j, "caveat", "thresholds are untuned defaults; QA is not evidence about pronunciation or labelling");
    js_obj_end(&j);
    js_obj(&j, "labels");
    js_str(&j, "word_id", word); js_str(&j, "speaker_id", spk); js_str(&j, "session_id", ses); js_int(&j, "repetition", rep);
    js_str(&j, "split", split); js_str(&j, "device_id", dev); js_str(&j, "environment", env); js_str(&j, "noise_condition", noise);
    js_str(&j, "speaking_style", style); js_str(&j, "label_verified", lv);
    js_obj_end(&j);
    js_obj_end(&j);
    js_end(&j);
    fclose(mf);
    chmod(meta, 0444);
    s2w_audio_free(&a);

    char row[1200];
    snprintf(row, sizeof row, "%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s,%s", id, rel, word, spk, ses, rep, split, dev, env, noise, style, lv, sha);
    if (append) {
        int fresh = !exists(manifest);
        FILE *m = fopen(manifest, "a");
        if (!m) return die("cannot append to %s", manifest);
        if (fresh) fputs("#s2w-recordings v1\nrecording_id,path,word_id,speaker_id,session_id,repetition,split,device_id,environment,noise_condition,speaking_style,label_verified,audio_sha256\n", m);
        fprintf(m, "%s\n", row);
        fclose(m);
    }
    printf("%s\n", row);
    fprintf(stderr, "ingested %s (qa: %s, flags: %s)\n", id, s2w_qa_verdict_name(qa.verdict), flags);
    return 0;
}
