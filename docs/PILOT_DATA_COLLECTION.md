# Pilot data-collection plan (protocol v1 - DRAFT for owner approval)

**Status: plan only.** Nothing has been recorded. No official recording may start until the vocabulary is reviewed
and approved (section 2). The only evidence so far is a *synthetic, simulated* experiment
(`docs/EXPERIMENT_NOISE_ENROLMENT.md`); its finding that multi-condition enrolment may help is a **hypothesis to test
on real speech**, not a result.

This plan defines the protocol, recording structure, condition matrix, leakage rules and quality controls. It invents no
Malayalam vocabulary and needs no recording hardware during development. Machine-readable parts:
`data/schema/pilot_conditions.csv`, `data/schema/pilot_matrix.csv`, `data/schema/session_log.template.csv`, and
`tools/pilot_plan.py` (validate, allocate speakers, generate prompt order, budget). Day-of procedure:
`docs/RECORDING_SESSION_CHECKLIST.md`.

## 1. What the pilot is for - and not for
Answers: how the unchanged matcher performs on real, reviewed, speaker-disjoint Malayalam recordings; how much accuracy
real noise/distance/room conditions cost; whether clean + condition-matched templates ("mixed enrolment") recover that
loss without hurting clean queries; how well unknown words are rejected; whether vowel-length and gemination contrasts
survive each condition.
Does **not** establish production accuracy, dialect coverage, or robustness to arbitrary speakers/devices. 16 speakers is a
pilot: expect to detect large effects only.

### Two intended modes - separate protocols
| | Speaker-independent (**primary**) | Personalised |
|---|---|---|
| Templates from | enrolment speakers only | the same user's own earlier session |
| Queries from | speakers who provided no template | that user, a later session |
| Split unit | speaker (disjoint) | session within speaker (enrol sessions disjoint from query sessions) |
| Difficulty | harder, the stricter claim | easier by construction |
| Tool support today | yes (`protocol=speaker_independent`) | **no** (gap G1) |

Never pool them, and never report personalised numbers as evidence for unfamiliar speakers. Each protocol has its own
frozen settings and its own locked-test ledger. The data design below supports both: every speaker records two sessions,
so session 1 can be enrolment and session 2 queries for the personalised protocol, while the speaker-disjoint splits serve
the independent protocol. A speaker's recordings enter exactly one split in the independent protocol.

## 2. Gate 0 - prerequisites before any official recording
- [ ] Vocabulary approved: `review_sheet.py export` produced `wordlist.csv` (all rows `verified`, two reviewers, contrasts confirmed
      genuine, familiar words) and `s2w_dataset_check --require-verified` passes on the scaffold. The AI draft is never used.
- [ ] Consent form reviewed locally (template in `docs/CONSENT_TEMPLATE.md`), storage, retention and deletion procedures agreed.
- [ ] Analysis plan frozen (sections 8 and 11): hypotheses, non-inferiority margin, exclusion rules. Record the commit hash.
      Later changes are logged as dated amendments, never silent edits.
- [ ] Open decisions answered (section 13); tooling gaps G2 and G7 closed or consciously avoided (section 12).
- [ ] Dry run with 1-2 volunteers on the real equipment. Dry-run audio is not entered in the dataset. It measures time per
      utterance, QA acceptance, noise levels actually reached and ID/prompt handling.
- [ ] Equipment check: devices, SPL meter, loudspeaker, noise files with recorded provenance, storage and backups.

## 3. Vocabulary
30-50 approved enrolled words (default 40) containing the reviewed vowel-length and gemination pairs, plus **10 separate
unknown words** never enrolled and never part of a pair (mix of acoustically near and far). Counts below assume 40 + 10; the
budget tool recomputes any other size. Pair words are not signalled to speakers, who are not told which words contrast
(avoids hyper-articulation); prompts show the reviewed spelling only, with no audio model, so dialect pronunciation is natural.

## 4. Speakers
Target 10-20 consenting adults; **default 16 = 8 enrolment / 4 calibration / 4 test** (`--splits 8,4,4`).
- Pseudonymous ids (`SP001...`); mapping to people stays outside the dataset. Participants file keeps only voluntary attributes.
- Aim for spread across self-reported sex, region/accent, age band and **measured** pitch (median F0 from audio, never inferred),
  and speaking rate. Assign splits only after attributes are known and **before any recognition results exist**; record the
  seed. `pilot_plan.py plan` deals speakers to splits stratified by a coarse `stratum` column you prepare privately.
- Reviewers/proposers of the word list and project developers should not be test speakers (they know the contrasts).
  Test speakers should be naive to the pairs.
- Subgroups are reported only where there are enough speakers and consent (existing suppression rules).

## 5. Sessions and recording structure
Per speaker, two sessions (session 2 at least a day later where practical, else after a real break with the setup redone).

| Session | Blocks (condition ids) | Utterances (40 vocab + 10 unknown) |
|---|---|---|
| S1 | `C0` clean, 3 repetitions | 150 |
| S2 | `C0-R` clean repeat (1 rep) first, then `N-FAN`, `N-STR`, `N-BAB`, `D-100`, `R-HARD` (1 rep each), order rotated across speakers | 300 |

Total 450 utterances per speaker; 16 speakers = 7,200 utterances. *Planning assumption (to be measured in the dry run):*
about 8 s per utterance including live QA, so roughly 25 min for S1 and 45-55 min for S2 plus setup, breaks and consent.
- Each repetition round is a fresh random permutation of all prompts (unknown words interleaved, indistinguishable in format);
  no word is ever repeated back to back; the order seed per speaker/session is recorded (`prompt_seed_*`).
- Repetition numbers **continue across blocks within a session** (S2: C0-R is rep 1, then rep 2, 3...) because the dataset
  key is (speaker, session, word, repetition) and ignores the condition. `pilot_plan.py prompts` emits this.
- Identifiers: `speaker_id` `SP012`; `session_id` `SP012-S1` / `SP012-S2` (globally unique); `recording_id`
  `SP012-S2-N-FAN-W017-r4` (<= 40 chars). Both are already schema-compliant.
- The speaker reads the prompt as text; the operator never says the word aloud.

## 6. Conditions (real, not simulated)
Defined in `data/schema/pilot_conditions.csv`; each maps onto existing `recordings.csv` fields
(`environment`, `noise_condition`, `device_id`, `speaking_style`).
| Id | environment | noise_condition | What it is |
|---|---|---|---|
| C0 | clean | (empty) | quiet room, primary device ~20 cm, processing off |
| C0-DEVB | clean | (empty) | optional second recorder, same utterances at the same time (gap G2) |
| C0-R | clean | (empty) | clean repeat in session 2 |
| N-FAN | noisy | `real:fan-60dBA` | played fan/air-con noise, 60 dBA at the head |
| N-STR | noisy | `real:street-65dBA` | played street noise, 65 dBA |
| N-BAB | noisy | `real:babble-65dBA` | played multi-talker babble, 65 dBA |
| D-100 | noisy | `real:dist-100cm` | quiet room, device 100 cm away |
| R-HARD | noisy | `real:room-hard` | reverberant hard-surfaced room |

- `real:` marks recorded conditions; `sim:` is reserved for software-degraded audio and triggers the harness's "simulated"
  banner. **Never assume a simulated condition equals its real counterpart**, and never merge them in one result.
- Verify, do not assume: log the playback level at the speaker's head (SPL meter), the noise file id, mic distance and room.
  `s2w_qa` records the achieved SNR and level of every recording; targets are nominal, the measured values are reported.
- Play noise only up to 70 dBA, give breaks, stop on request. The speaker speaks over the noise normally (do not instruct raised voice).
- Devices: primary `DEV-A`; optional `DEV-B` (second phone/recorder) and `DEV-C`. Record model class, OS/app, audio source,
  gain; disable automatic gain control, noise suppression, echo cancellation where the device allows (use the unprocessed source if
  available). Capture lossless WAV >= 16 kHz. Originals are never edited.
- Optional extra block if time permits (not in the default matrix): `speaking_style` fast/slow.

## 7. Enrolment-by-query matrix (matcher fixed)
Enrolment sets built from **enrolment-split speakers only**; queries from calibration/test speakers.
- Enrolment: `E0` clean (C0); `E-FAN/STR/BAB` noise-only; `EM-FAN/STR/BAB` mixed (C0 + that noise); `EM-ALL` (C0 + all three noises).
- Queries: C0, C0-R, N-FAN, N-STR, N-BAB, D-100, R-HARD, and C0-DEVB against E0 only. D-100 and R-HARD are *unseen* conditions (no matched enrolment): they test generalisation.
- 57 cells, each labelled (`baseline`, `clean_enrol_loss`, `noise_only_matched`, `noise_only_mismatched`, `mixed_matched`,
  `mixed_mismatched`, `mixed_all`, `*_clean_cost`, `session_repeat`, `device_mismatch`). The checker refuses a matrix that lacks mismatched
  pairs or clean-query-cost cells, so matched noise cannot be flattered by similar distortions.
- First comparison: baseline matcher, MFCC + mean normalisation, default DTW settings, no tuning. The `baseline-v0.3.0` commit is recorded in every run.

### Pre-registered hypotheses (each reported whatever its direction)
- **H1** For each real noise query X: mixed enrolment (clean + X) beats clean-only enrolment in top-1.
- **H2** Clean-query cost: top-1 on clean queries with mixed enrolment is within a margin delta of clean-only (proposed delta = 3 points; confirm before collection).
- **H3** Noise-only enrolment costs clean-query accuracy and rejection quality relative to mixed enrolment (as in the synthetic run).
- **H4** EM-ALL beats E0 on the unseen conditions D-100 and R-HARD.
- Exploratory: confusion on each vowel-length / gemination pair by condition; unknown-word separability (AUC) and calibrated false-accept/false-reject; device mismatch.
- Metrics: top-1/3/5 over known words (denominators shown), unknown-word false accept and known-word false reject at thresholds
  calibrated on **calibration speakers only**, AUC, per-pair confusion, per-condition and per-device tables.

### What the pilot can resolve
Budget (`pilot_plan.py budget`, 8/4/4 split, 40 + 10 words): per query split (4 speakers), C0: 480 known + 120 unknown;
C0-R and each noise/distance/room condition: 160 known + 40 unknown; templates per word from the 8 enrolment speakers: 24 clean (C0), 8 per other condition.
Recording-level 95% half-widths are about +-3.6 points (n=480) and +-6.2 points (n=160) at 80% accuracy, **optimistic**,
because queries cluster within 4 speakers. Report speaker-level (paired) differences and a speaker-cluster bootstrap; with 4 test
speakers only large effects are detectable. This is why a calibration-split exploration and one locked test run are both
reported with their uncertainty, and why the pilot cannot justify fine claims.

## 8. Splits, isolation and leakage prevention
1. **Speaker-disjoint** splits for the independent protocol (enforced by `s2w_dataset_check`); **session isolation** (a session belongs to one speaker and split).
2. Unknown words appear only in calib/test, never in enrolment; every known word in calib/test has enrolment.
3. Simultaneous multi-device recordings of the same utterance must stay together in one split and one role; until gap G2 is closed, do not rely on them (record the second device in a separate session, or skip it).
4. The same utterance never serves as both template and query (the independent protocol makes this structural; the personalised protocol uses disjoint sessions, G1).
5. Tuning, threshold calibration and condition exploration use **calib** only. Test is evaluated once with frozen settings (`s2w_eval freeze`); the ledger refuses a second evaluation with different settings. Real-data work never reads test audio for degradation/simulation.
6. Simulated augmentation (if later used) is clearly labelled `sim:` and applied only within the training/enrolment or calib roles, never to test.
7. QA thresholds are set from calibration/dry-run data, not from test recordings.

## 9. Quality controls
- **At the session:** ingest every take immediately with `s2w_ingest`. QA **reject** (clipping, cut onset/offset, no speech, SNR too low) is re-recorded while the speaker is present. Marginal takes are kept and flagged. Originals are byte-identical and read-only; hashes pinned.
- **Labels:** the audio must say the labelled word. A second listener checks 100% of the first two speakers, then a 20% random sample plus every marginal take; failures set `label_verified=no`. Mispronunciations/other words are ingested as they were said or re-recorded; never "fixed" afterwards.
- **Exclusions (fixed in advance):** only QA-rejected or label-failed recordings, reported with counts. Never exclude by recognition outcome.
- **Session log** for every session and condition block (`session_log.template.csv`): room, devices/settings, noise file, measured playback level, distance, operator, anomalies. Private.
- **Integrity:** `s2w_dataset_check` clean (including audio hashes) before any evaluation; encrypted storage, 3-2-1 backups; deletion on withdrawal bumps the dataset version.
- **Privacy:** voice is personal data. Consent per `docs/DATASET.md`; audio, participants, logs and per-query results stay out of git.

## 10. Staging and go / no-go gates
| Gate | Evidence | Proceed if (proposals, confirm before starting) |
|---|---|---|
| G0 | section 2 complete | all boxes ticked |
| G1 clean data | S1 from first 2-3 speakers | QA reject rate low enough to be practical; label check passes; dataset validates |
| G2 baseline | clean/clean on calib, recorded **before any tuning** | numbers + denominators written down |
| G3 conditions | S2 blocks recorded for all speakers | achieved SNR/levels logged and plausible |
| G4 comparison | matrix on calib with fixed matcher | results reported for every pre-registered hypothesis |
| G5 lock | settings frozen, test run once | ledger shows one test evaluation |
If the clean/clean baseline is poor, fix that before spending effort on the noise comparison.

## 11. Reporting template
Every report states: dataset id/version and manifest hash, protocol, split sizes in speakers and recordings, matcher/settings hash and
commit, calibration source, then per cell: counts with denominators, top-1/3/5, unknown-word false accept / known-word false reject, AUC,
per-pair confusion by contrast type, QA exclusions, per-speaker spread and uncertainty. Synthetic and real results are never mixed; negative
and null results are reported.

## 12. Tooling gaps (not built; this plan does not depend on them for design)
| Gap | What | Needed before |
|---|---|---|
| G1 | personalised protocol support: schema `protocol=personalized`, within-speaker session-disjoint rule, per-protocol ledger | any personalised evaluation |
| G2 | utterance grouping for simultaneous devices (shared id), else record device B separately | collection, if second device used simultaneously |
| G3 | `s2w_select`: build an evaluation dataset for a matrix cell (enrol sets incl. mixed, one query condition) from real recordings; real-data analogue of `s2w_degrade` | the matrix comparison |
| G4 | per-condition QA summary vs nominal targets (SNR/level) | G3 gate |
| G5 | calibrate QA thresholds on dry-run/clean-session data | collection (QA gating) |
| G6 | speaker-cluster bootstrap intervals in the harness | reporting |
| G7 | label-verification sheet/sampling list and import into `label_verified` | collection |

## 13. Open decisions for the project owner
1. Primary mode: speaker-independent (recommended) - and is personalised recognition also in scope for this pilot?
2. Which recorders/microphones are available (>= 2 distinct devices?), and can a loudspeaker + SPL meter be used?
3. Recruitment and consent route, local ethics/data-protection review, compensation.
4. Number of speakers (10-20) and the split sizes; who prepares the private `stratum` column.
5. Rooms for C0 / R-HARD; noise files and their licences/provenance.
6. Who verifies labels; who operates sessions; where data is stored.
7. Non-inferiority margin delta for H2; whether to add a fast/slow speaking block.

## 14. Commands (all work without real data)
```
tools/pilot_plan.py check   --conditions data/schema/pilot_conditions.csv --matrix data/schema/pilot_matrix.csv
tools/pilot_plan.py budget  --conditions data/schema/pilot_conditions.csv --splits 8,4,4 --vocab-words 40 --oov-words 10
tools/pilot_plan.py plan    --speakers speakers.csv --conditions data/schema/pilot_conditions.csv --matrix data/schema/pilot_matrix.csv --splits 8,4,4 --seed <N> --out allocation.csv
tools/pilot_plan.py prompts --wordlist wordlist.csv --conditions data/schema/pilot_conditions.csv --allocation allocation.csv --speaker SP001 --out prompts_SP001.csv [--with-labels]
build/s2w_ingest ... --recording-id SP001-S2-N-FAN-W017-r4 --session-id SP001-S2 --environment noisy --noise-condition real:fan-60dBA --device-id DEV-A --append-manifest
build/s2w_dataset_check DATASET --require-verified
```
