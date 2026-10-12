/* Dataset manifest loading and validation (schema v1). See docs/DATASET.md.
 * Nothing here modifies, repairs or re-labels data: problems are reported as issues. */
#ifndef S2W_DATASET_H
#define S2W_DATASET_H
#include <stddef.h>
#include <stdio.h>

#include "s2w_audio.h"

#define S2W_ID_MAX 41

typedef enum { S2W_ROLE_VOCAB = 0, S2W_ROLE_OOV = 1 } s2w_role;
typedef enum { S2W_CT_NONE = 0, S2W_CT_VOWEL_LENGTH, S2W_CT_GEMINATION, S2W_CT_OTHER } s2w_contrast;
typedef enum { S2W_REVIEW_DRAFT = 0, S2W_REVIEW_REVIEWED, S2W_REVIEW_VERIFIED } s2w_review;
typedef enum { S2W_SPLIT_ENROL = 0, S2W_SPLIT_CALIB, S2W_SPLIT_TEST } s2w_split;

extern const char *const S2W_SPLIT_NAME[3];
extern const char *const S2W_CONTRAST_NAME[4];
extern const char *const S2W_REVIEW_NAME[3];

typedef struct {
    char id[S2W_ID_MAX], label[160], roman[96], meaning[96], pair_id[S2W_ID_MAX];
    char reviewer1[S2W_ID_MAX], reviewer2[S2W_ID_MAX], variants[96];
    int role, contrast, review;
    size_t line;
} s2w_word;

typedef struct {
    char id[S2W_ID_MAX], path[256], speaker[S2W_ID_MAX], session[S2W_ID_MAX], device[S2W_ID_MAX];
    char noise_condition[S2W_ID_MAX], style[S2W_ID_MAX], sha256[65];
    int word;          /* index into words */
    int rep, split, noisy, label_verified;
    size_t line;
} s2w_rec;

typedef struct {
    char id[S2W_ID_MAX], consent_version[S2W_ID_MAX], consent_date[16];
    char sex[S2W_ID_MAX], age[S2W_ID_MAX], accent[S2W_ID_MAX];
    int group_ok;
    int consent_status;   /* 0 = column absent, 1 = consented, 2 = withdrawn (withdrawn always fails validation) */
    size_t line;
} s2w_participant;

typedef struct {
    char dir[512];
    char dataset_id[S2W_ID_MAX], dataset_version[S2W_ID_MAX], protocol[S2W_ID_MAX];
    int synthetic;     /* kind=synthetic */
    int personalised;  /* protocol=personalised (templates come from the same participant's own earlier session); else speaker_independent */
    s2w_word *w; size_t nw;
    s2w_rec *r; size_t nr;
    s2w_participant *p; size_t np;
    int has_participants;
    char manifest_sha256[65];
} s2w_dataset;

typedef struct { int is_error; char file[24]; size_t line; char msg[220]; } s2w_issue;
typedef struct { s2w_issue *v; size_t n, cap, n_err, n_warn; } s2w_issues;

void s2w_issues_free(s2w_issues *i);
void s2w_issue_add(s2w_issues *i, int is_error, const char *file, size_t line, const char *fmt, ...);

typedef struct {
    int require_verified;      /* error if any word used by a recording is not `verified` */
    int require_participants;  /* error if participants.csv is missing */
} s2w_dataset_options;

/* Parses and structurally validates (no audio is opened). Returns S2W_OK only if `iss` has no
 * errors; otherwise a failure status and `ds` is empty. Warnings do not fail. */
s2w_status s2w_dataset_load(const char *dir, const s2w_dataset_options *opt, s2w_dataset *ds, s2w_issues *iss);
void s2w_dataset_free(s2w_dataset *ds);

int s2w_dataset_find_word(const s2w_dataset *ds, const char *id);
const s2w_participant *s2w_dataset_participant(const s2w_dataset *ds, const char *speaker);
s2w_status s2w_dataset_audio_path(const s2w_dataset *ds, const s2w_rec *r, char *out, size_t n);

typedef struct {
    size_t n_checked, n_decoded, n_decode_fail, n_unpinned, n_hash_mismatch, n_duplicate_audio;
    size_t n_below_16k;
    size_t warn_count[8];  /* indexed by bit position of s2w_warning */
} s2w_audio_summary;

/* Opens every recording: decodes, hashes, checks pinned hashes, reports quality warnings
 * (too_short / mostly_silent are errors, the rest warnings) and duplicate audio bytes.
 * If emit_hashes is non-NULL, writes "recording_id,sha256" lines for every readable file. */
s2w_status s2w_dataset_check_audio(const s2w_dataset *ds, s2w_issues *iss, s2w_audio_summary *sum, FILE *emit_hashes);
#endif
