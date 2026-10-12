# Sound2Word dataset specification (schema v1)

Purpose: a reproducible, privacy-respecting dataset for measuring isolated-word Malayalam
recognition. **Nothing in this repository is real Malayalam data.** Fixtures are synthetic and carry
`kind=synthetic`; their results are software validation only and are never Malayalam accuracy.

## 1. Word list and verification
`wordlist.csv` (see `data/schema/`). Each word has a `word_id` (ASCII key used everywhere), the
Malayalam `label`, a romanisation, an English meaning, a `role` (`vocab` = enrolled vocabulary,
`oov` = genuinely unseen out-of-vocabulary word used only for rejection tests), and optionally a
minimal-pair link (`pair_id`, `contrast_type`: `vowel_length`, `gemination`, `other`, `none`).

**Review status** (`draft` -> `reviewed` -> `verified`). `verified` requires two independent
reviewers (`reviewer_1`, `reviewer_2`, pseudonymous IDs), each confirming:
1. correct Malayalam spelling and intended meaning;
2. actual pronunciation and acceptable regional variants (`variants` column);
3. that a claimed pair really contrasts the stated feature (vowel length / gemination / other);
4. that the word is familiar enough to be spoken naturally.

Tooling never invents or "fixes" Malayalam. Evaluation results record how many words are verified;
results on unverified lists are marked provisional. OOV words must be reviewed to the same standard
and must never be enrolled or used as templates.

## 2. Recording protocol
- Isolated words, prompted one at a time in randomised order per session (record the order seed
  in `style_notes` if relevant). 3 repetitions per word per speaker per session in the pilot.
- Record original lossless WAV (PCM 16-bit or better, >= 16 kHz, mono preferred). Never trim,
  denoise, normalise or re-encode originals; derived copies live elsewhere.
- Clean condition first (quiet room, close microphone). Noisy conditions are separate recordings
  (`environment=noisy`, `noise_condition` describes it), never mixed into clean test numbers.
- Record `device_id` (microphone/phone model, anonymised class is fine), `speaking_style`
  (`normal`, `fast`, `slow`, ...).
- Discard-and-re-record is allowed for mispronunciation/interruption **before** labelling; log it
  outside the dataset. Once a file is in the manifest it is never edited. `label_verified=yes`
  means a listener confirmed the audio says the labelled word.
- Pilot target: 30-50 words, 10-20 speakers spanning men and women, pitch ranges, regional
  accents, speaking speeds; add separate sessions and noise later.

## 3. Consent, privacy, retention
- Written, informed, voluntary consent before recording (template: `docs/CONSENT_TEMPLATE.md`;
  have it reviewed locally - it is not legal advice). Participants may withdraw; on withdrawal
  delete their audio, rows and derived artefacts and bump the dataset version.
- Collect the minimum. Sex, age band and accent/region are optional and self-reported; `declined`
  or empty is always accepted. A participant's `group_reporting_ok=yes` is required before their
  attributes are used for group breakdowns.
- Pitch range is **measured from audio** (median F0), never inferred from sex or gender.
- Audio, `participants.csv`, consent forms and per-query result files are private: they live
  outside git (`data/private/`, ignored) on encrypted storage with access limited to the project.
  Only schemas, code and synthetic fixtures are public. Share aggregate results only; suppress
  groups too small to avoid identifying individuals (defaults: >= 3 speakers and >= 30 queries).
- Retention: keep audio only while the project needs it; set a review date in the consent form;
  document deletion in the dataset changelog.

## 4. Splits and isolation policy (protocol `speaker_independent`)
| Split | Purpose | Contents |
|-------|---------|----------|
| `enrol` | build templates | in-vocabulary (`vocab`) words only |
| `calib` | tune settings, thresholds | `vocab` + `oov` words |
| `test` | final report, locked until settings frozen | `vocab` + `oov` words |

Rules enforced by `s2w_dataset_check` (violations are errors, never auto-repaired):
1. **Speaker-disjoint:** a `speaker_id` appears in exactly one split.
2. **Session isolation (additional):** a `session_id` belongs to one speaker and one split.
   Session IDs are globally unique (e.g. `spk012-s1`).
3. **No OOV in enrolment**, and OOV words are never `vocab`.
4. Every `vocab` word in calib/test has at least one enrolment recording.
5. No two recordings share a path, a `recording_id`, a (speaker, session, word, repetition)
   key, or identical audio bytes (SHA-256) - this blocks copy leakage across splits.
6. `audio_sha256` (when given) must match the file, pinning dataset content.
The **personalised** protocol (`protocol=personalised`) is a different protocol with its own rules, implemented and tested:
a participant's own S1 recordings are the templates for that participant's later-session queries; calibration and test
participants are disjoint; a query needs the same participant's own template of that word; sessions still belong to one
speaker and one split. Its settings (`protocol=personalised`), thresholds and results are kept separate from the speaker-independent track
(`docs/PILOT_SPLIT_DESIGN.md`).

## 4a. Consent status
`participants.csv` may carry a `consent_status` column (`consented` | `withdrawn`), stamped from the private consent register by
`consent_gate.py stamp-consent`. **Real datasets (`kind=real`) must carry it for every speaker, and every speaker must be `consented`**; a withdrawn participant
makes validation and evaluation fail until their recordings are removed (`consent_gate.py build-eligible`). `s2w_ingest` requires `--consent-register` for real datasets.
See `docs/PILOT_RECRUITMENT_AND_CONSENT.md`.

## 5. Tuning discipline
Tuning code may read `enrol` and `calib` only. The `test` split is evaluated only with a frozen
settings file (`s2w_eval freeze`) and every test evaluation is appended to a ledger; evaluating the
test split again with different settings is refused unless explicitly acknowledged, and then flagged
in the result. See `docs/EVALUATION.md`.

## 6. Versioning
`dataset_meta.txt` carries `dataset_id` and `dataset_version`. The manifest hash (SHA-256 over meta,
word list, recordings, participants files) is stored in every run record. Any change to files,
labels or splits bumps the version.
