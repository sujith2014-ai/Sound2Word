/* s2w_make_fixture: write a SYNTHETIC dataset (tone "words") for software tests only.
 * usage: s2w_make_fixture OUTDIR
 * Labels are placeholders (SYNTH_*), not Malayalam; kind=synthetic. Deterministic. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "s2w_audio.h"
#include "s2w_sha256.h"

static uint32_t rng = 12345;
static double urand(void) { rng = rng * 1664525u + 1013904223u; return (rng >> 8) / 16777216.0; }

static void w16(FILE *f, unsigned v) { fputc(v & 255, f); fputc((v >> 8) & 255, f); }
static void w32(FILE *f, unsigned v) { w16(f, v & 0xFFFF); w16(f, v >> 16); }

static int write_wav(const char *path, const float *x, size_t n) {
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    fwrite("RIFF", 1, 4, f); w32(f, (unsigned)(36 + n * 2)); fwrite("WAVEfmt ", 1, 8, f);
    w32(f, 16); w16(f, 1); w16(f, 1); w32(f, 16000); w32(f, 32000); w16(f, 2); w16(f, 16);
    fwrite("data", 1, 4, f); w32(f, (unsigned)(n * 2));
    for (size_t i = 0; i < n; i++) {
        double v = x[i] * 32767.0;
        if (v > 32767) v = 32767;
        if (v < -32768) v = -32768;
        w16(f, (unsigned)((int)lrint(v) & 0xFFFF));
    }
    return fclose(f);
}

typedef struct { const char *id, *label; int oov; const char *pair, *contrast; double f[3], t[3]; } W;
/* three-segment tone words; V3/V4 and V5/V6 are synthetic "minimal pairs" differing only in a segment length. */
static const W WORDS[] = {
    {"SYNTH_V1", "SYNTH_V1", 0, "", "none", {500, 1000, 1500}, {.20, .20, .20}},
    {"SYNTH_V2", "SYNTH_V2", 0, "", "none", {1500, 800, 400}, {.20, .20, .20}},
    {"SYNTH_V3", "SYNTH_V3", 0, "PAIR_A", "vowel_length", {700, 1800, 700}, {.12, .20, .20}},
    {"SYNTH_V4", "SYNTH_V4", 0, "PAIR_A", "vowel_length", {700, 1800, 700}, {.30, .20, .20}},
    {"SYNTH_V5", "SYNTH_V5", 0, "PAIR_B", "gemination", {900, 2500, 1200}, {.15, .06, .20}},
    {"SYNTH_V6", "SYNTH_V6", 0, "PAIR_B", "gemination", {900, 2500, 1200}, {.15, .18, .20}},
    {"SYNTH_V7", "SYNTH_V7", 0, "", "none", {2200, 2800, 3300}, {.20, .20, .20}},
    {"SYNTH_V8", "SYNTH_V8", 0, "", "none", {350, 350, 2600}, {.25, .15, .20}},
    {"SYNTH_O1", "SYNTH_O1", 1, "", "none", {3800, 300, 3000}, {.20, .20, .20}},
    {"SYNTH_O2", "SYNTH_O2", 1, "", "none", {250, 3600, 250}, {.20, .20, .20}},
    {"SYNTH_O3", "SYNTH_O3", 1, "", "none", {4200, 4200, 150}, {.20, .20, .20}},
};
#define NW ((int)(sizeof WORDS / sizeof *WORDS))

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s OUTDIR\n", argv[0]); return 2; }
    char p[1024];
    snprintf(p, sizeof p, "%s", argv[1]); mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/audio", argv[1]); mkdir(p, 0755);

    snprintf(p, sizeof p, "%s/dataset_meta.txt", argv[1]);
    FILE *f = fopen(p, "w");
    if (!f) { perror(p); return 2; }
    fprintf(f, "schema_version=1\nkind=synthetic\ndataset_id=synthetic-fixture\ndataset_version=1\nprotocol=speaker_independent\n");
    fclose(f);

    snprintf(p, sizeof p, "%s/wordlist.csv", argv[1]);
    f = fopen(p, "w");
    fprintf(f, "#s2w-wordlist v1\nword_id,label,romanisation,meaning_en,role,pair_id,contrast_type,review_status,reviewer_1,reviewer_2,variants\n");
    for (int i = 0; i < NW; i++)
        fprintf(f, "%s,%s,%s,synthetic tone sequence,%s,%s,%s,verified,rev-a,rev-b,\n", WORDS[i].id, WORDS[i].label, WORDS[i].id,
                WORDS[i].oov ? "oov" : "vocab", WORDS[i].pair, WORDS[i].contrast);
    fclose(f);

    /* speakers: 4 enrol, 3 calib, 3 test; pitch factor and tempo vary per speaker */
    struct { const char *id; int split; double pitch, tempo; const char *dev; const char *sex; } S[] = {
        {"spkE1", 0, 1.00, 1.00, "dev-a", "f"}, {"spkE2", 0, 0.90, 1.10, "dev-b", "m"},
        {"spkE3", 0, 1.10, 0.95, "dev-a", "f"}, {"spkE4", 0, 0.95, 1.05, "dev-b", "m"},
        {"spkC1", 1, 1.03, 1.05, "dev-a", "f"}, {"spkC2", 1, 0.92, 0.95, "dev-b", "m"}, {"spkC3", 1, 1.08, 1.00, "dev-a", "f"},
        {"spkT1", 2, 0.97, 1.02, "dev-a", "m"}, {"spkT2", 2, 1.06, 0.97, "dev-b", "f"}, {"spkT3", 2, 0.93, 1.08, "dev-a", "m"},
    };
    snprintf(p, sizeof p, "%s/recordings.csv", argv[1]);
    FILE *rf = fopen(p, "w");
    fprintf(rf, "#s2w-recordings v1\nrecording_id,path,word_id,speaker_id,session_id,repetition,split,device_id,environment,noise_condition,speaking_style,label_verified,audio_sha256\n");
    snprintf(p, sizeof p, "%s/participants.csv", argv[1]);
    FILE *pf = fopen(p, "w");
    fprintf(pf, "#s2w-participants v1\nspeaker_id,consent_version,consent_date,group_reporting_ok,sex_self_report,age_band,accent_region\n");
    static const char *SPLIT[] = {"enrol", "calib", "test"};
    int count = 0;
    for (int s = 0; s < 10; s++) {
        fprintf(pf, "%s,synthetic-v1,2000-01-01,yes,%s,declined,declined\n", S[s].id, S[s].sex);
        for (int w = 0; w < NW; w++) {
            if (S[s].split == 0 && WORDS[w].oov) continue;
            for (int rep = 1; rep <= 3; rep++) {
                int nseg = 3;
                double jitter = 0.97 + 0.06 * urand();
                size_t tot = 0, seglen[3];
                for (int g = 0; g < nseg; g++) { seglen[g] = (size_t)(16000 * WORDS[w].t[g] * S[s].tempo * jitter); tot += seglen[g]; }
                size_t pad = 4000, n = tot + 2 * pad;
                float *x = calloc(n, sizeof *x);
                size_t pos = pad;
                double amp = 0.3 + 0.2 * urand();
                for (int g = 0; g < nseg; g++) {
                    double fr = WORDS[w].f[g] * S[s].pitch * (0.98 + 0.04 * urand());
                    for (size_t i = 0; i < seglen[g]; i++) {
                        double env = 1.0;
                        if (i < 160) env = (double)i / 160.0; else if (seglen[g] - i < 160) env = (double)(seglen[g] - i) / 160.0;
                        x[pos + i] = (float)(env * amp * sin(2 * M_PI * fr * (double)(pos + i) / 16000.0));
                    }
                    pos += seglen[g];
                }
                for (size_t i = 0; i < n; i++) x[i] += (float)(0.002 * (urand() - 0.5));
                char rel[200], full[1200], hex[65];
                snprintf(rel, sizeof rel, "audio/%s_%s_%d.wav", S[s].id, WORDS[w].id, rep);
                snprintf(full, sizeof full, "%s/%s", argv[1], rel);
                if (write_wav(full, x, n) != 0) { perror(full); return 2; }
                free(x);
                s2w_sha256_file_hex(full, hex);
                fprintf(rf, "rec%04d,%s,%s,%s,%s-s1,%d,%s,%s,clean,,normal,yes,%s\n", ++count, rel, WORDS[w].id, S[s].id, S[s].id, rep,
                        SPLIT[S[s].split], S[s].dev, hex);
            }
        }
    }
    fclose(rf); fclose(pf);
    printf("synthetic fixture written: %d recordings\n", count);
    return 0;
}
