# Experiment protocol: does acoustic normalisation help on real speech?

**Status: protocol only.** No real Malayalam speech exists in this repository, nothing in this document has been run, and
no result below is a finding. This protocol uses the repository as it is at commit `58f9360` (no code changes needed except
where a gap is named in section 5). The only existing recognition results are on synthetic tone fixtures and say nothing
about Malayalam.

## 1. Question and scope

Does one established technique at a time (feature normalisation, query-side spectral warp, spectral-subtraction denoising)
improve **cross-speaker matching** of a few real Malayalam words over the current MFCC + DTW baseline, **without destroying
meaningful sound distinctions** (vowel length, gemination)?

This is an **engineering smoke test** with 3 speakers. It can show that a technique plainly helps, plainly hurts, or that the
experiment cannot tell. It cannot support a generalisation claim about speakers, dialects, children, noise or distance.
Anything adopted from it must be confirmed on new speakers (section 9).

## 2. Blockers (owner actions, not software)

1. **Verified words (Gate A).** A native Malayalam speaker verifies 5-10 words and the proposed contrasts. The repository
   marks a word `verified` only with two distinct reviewers (`docs/WORD_REVIEW.md`); with one reviewer the words stay
   `reviewed` and every output is stamped PROVISIONAL by the harness. Proceed with that stamp if a second reviewer is not
   available; do not edit review fields to remove it. `data/drafts/ai_draft_candidates.*` are AI drafts, unverified, and must not
   be used as the word list.
2. **Consenting speakers.** At least 3, under `docs/PILOT_RECRUITMENT_AND_CONSENT.md`; each appears in the output of
   `tools/consent_gate.py eligible` before recording. Four is better (section 9, locked check).

## 2a. Gates for this experiment

These are the experiment's own gates. They are **not** the pilot's Gate A/B/C in `docs/PILOT_DATA_COLLECTION.md` (vocabulary
approval, rehearsal, main pilot); the vocabulary gate is the same check, the other two are specific to this experiment.
Each must pass, in order, before the next step starts.

| Gate | Must be true | Evidence |
|---|---|---|
| **A: vocabulary** | Both reviewers independently approved spelling, meaning, familiarity, pronunciation variants and the intended contrast of every word. A pair either reviewer finds not genuine is replaced **before** any recording. | `review_sheet.py export` succeeded; `wordlist.provenance.json`; `s2w_dataset_check --require-verified` passes |
| **B: recordings** | Every speaker passed `consent_gate.py eligible` before recording. QA-rejected takes were re-recorded at the session where possible. The fourth speaker's audio is held out of development and configuration selection entirely. | consent register output, session logs, `s2w_qa` results, fold manifests that do not contain the held-back speaker |
| **C: experiment integrity** | Configurations, metrics, thresholds and fold assignments are committed before the first run. Every configuration is reported, regressions included, with per-speaker and per-word results. | commit hash and manifest hashes quoted in the report; all 21 run directories accounted for |

## 3. Vocabulary (chosen by the native speaker, not by us)

8 words (range 5-10):

| Group | Count | Purpose |
|---|---|---|
| Minimal pair, vowel length | 2 (1 pair) | the contrast duration handling could damage |
| Minimal pair, gemination | 2 (1 pair) | consonant-duration contrast |
| Distinct control words | 4 | not confusable with each other or the pairs; gives the "easy" ceiling |

Each word needs `word_id`, `label`, `role=vocab`, `pair_id`/`contrast_type` for pair members, and `variants` if the speaker
accepts alternative pronunciations. No `oov` words are needed: rejection stays off (section 6), so the unknown-word
false-accept metric is not part of this experiment.

## 4. Recordings

One device, one quiet room, one placement (`docs/RECORDING_SETUP_GUIDE.md`), clean condition, no device processing (AGC,
noise suppression, echo cancellation off or noted). Operator never says or demonstrates the words
(`docs/RECORDING_SESSION_CHECKLIST.md`). Prompt order is randomised per speaker and generated, not typed.

| Block | Reps per word | Style label | Use |
|---|---|---|---|
| Normal | 5 | `normal` | enrolment and queries (required) |
| Slow | 2 | `slow` | rate stress, queries only (optional) |
| Fast | 2 | `fast` | rate stress, queries only (optional) |

Slow/fast are instructed as a rate only ("a bit slower / faster than usual"), never as a pronunciation. Size: required
block = 8 words x 5 reps x 3 speakers = 120 recordings; with optional blocks 216, about 15-20 minutes per speaker.

Speaker selection: aim to span vocal range and rate (for example lowest and highest voices available, one faster speaker).
Use **measured** F0 (section 5), not self-report, to describe the range actually obtained. Sex, age band and accent are
voluntary (`group_reporting_ok`), and with 3 speakers they are not reported as groups (the harness suppresses groups under 3
speakers anyway).

Quality: run `build/s2w_qa` on every take at the session; `reject` is re-recorded then, `marginal` kept and noted. A take is
excluded only for a reason written in the session log **before** any evaluation (misread word, interruption, device
glitch). Never re-record or exclude after seeing recognition results.

## 5. Diagnostics (measured, never used to set configurations)

Computed once per recording after ingest, **before** any technique run. They explain results; they do not drive them.

| Quantity | How | Gap |
|---|---|---|
| Duration, speech duration, leading/trailing silence, SNR estimate, clipping, flags | `build/s2w_qa file.wav` | none |
| Median F0, voiced fraction, F0 range per recording | external Praat/parselmouth, autocorrelation method, fixed settings recorded in the report (floor 75 Hz, ceiling 600 Hz so a high voice is not clipped), run locally on the private audio | **no per-recording F0 tool in the repo.** `s2w_median_f0` is per-speaker, evaluation-only, and limited to 60-450 Hz, and returns "undefined" rather than guessing under 8 voiced frames. Either use Praat as above or approve a small wrapper later. Not built now. |
| Per-speaker, per-word duration ratio of the long/short member of each pair | from `speech_s` above | endpointing is energy-based and untuned; treat as approximate |

Report F0 and duration per speaker code only (no names). Check the sanity of the sample: the speakers' median F0 should
actually differ; if all three are within a few semitones, the pitch question is untested and the report says so.

## 6. Splits for 3 speakers: leave-one-speaker-out

The harness needs speaker-disjoint enrol and query speakers. With 3 speakers there is no room for a calibration split and a
locked test split, so this experiment is **development-only** and uses three folds:

| Fold | Enrolment (templates) | Queries |
|---|---|---|
| F1 | speakers B, C: `normal` block | speaker A: all blocks |
| F2 | speakers A, C: `normal` block | speaker B: all blocks |
| F3 | speakers A, B: `normal` block | speaker C: all blocks |

- Each fold is its own dataset directory with its own `dataset_id`; the only difference between folds is which rows exist and
  their `split` value (enrolment rows `enrol`; held-out speaker rows `calib`, because the `calib` split is the one the
  tuning-side commands may read). Audio and `audio_sha256` are identical across folds (hard links or copies).
- Slow/fast takes of the two enrolment speakers are **omitted** from the fold so the template set is identical for every
  configuration.
- `--split test` is never invoked in this experiment; the test ledger stays untouched. Rejection is off, and `calibrate` and
  `freeze` are not used.
- Validate every fold with `build/s2w_dataset_check FOLD --require-participants` (add `--require-verified` once Gate A is
  complete).
- Query count per fold: 8 x 5 = 40 normal, plus up to 32 optional slow/fast. Pooled normal queries across folds: 120.

## 7. Configurations (fixed now, before any data)

Baseline is the current default: `norm=mean`, no denoise, no warp. Every other configuration is the baseline plus **exactly
one changed line**; confirm with `diff base.cfg cX.cfg`. All use the same words, recordings and folds.

| ID | Settings file content after `#s2w-settings v1` | Technique | Question it answers |
|---|---|---|---|
| C0 | *(empty)* | baseline: per-utterance mean normalisation | reference |
| C1 | `norm=none` | no normalisation (control) | what does mean normalisation buy? |
| C2 | `norm=meanvar` | CMVN | does variance normalisation help or flatten phonetic detail? |
| C3a | `warp_grid=0.9,1.0,1.1` | query-side VTLN-style warp, narrow | does a small vocal-tract-length search help cross-speaker matching? |
| C3b | `warp_grid=0.8,0.9,1.0,1.1,1.2` | same, wide | does a wider search help the extreme speaker or add false matches? |
| C4a | `denoise=specsub` | spectral subtraction, defaults (oversub 1.5, floor 0.05) | does it hurt clean speech? |
| C4b | `denoise=specsub` + `denoise_oversub=2.5` | stronger subtraction | where does damage to weak consonants start? |

Notes:
- The warp is applied to the query only; templates stay at alpha 1. The alpha chosen per query is whichever gives the
  smallest best-candidate distance, so extra search freedom can also lower the distance to a **wrong** word. That is why
  pair confusion is a primary outcome, not a side check.
- C4 on clean speech is a harm check. Little or no gain is the expected result; a noise benefit can only be tested in the
  later stage (section 10).
- No sweeps beyond this table. Combinations (for example best norm + best warp) are run only afterwards, labelled
  **exploratory**, and cannot support a claim.
- Total: 7 configs x 3 folds = 21 runs. Run the baseline first on every fold. Command per run:
  `build/s2w_eval run --dataset FOLD --settings cX.cfg --split calib --run-id <fold>_<config>`.
  Run directories are git-ignored (per-query files can identify speakers). `queries.csv` keeps the per-query top-5 words,
  distances, margin, quality flags and the warp alpha used.

## 8. Metrics

Per configuration, per fold (= per held-out speaker) and pooled. Always show numerator/denominator.

1. **Top-1/3/5 on known words** (normal block), from `summary.txt`/`result.json`.
2. **Control words vs pair words** separately: top-1 for the 4 control words and for the 4 pair words.
3. **Minimal pairs** (`result.json` `minimal_pairs`): top-1 correct, confused with partner, other wrong, per pair and per
   contrast type.
4. **Word-by-word confusion matrix** from `queries.csv` (`word_id` vs `top1`), built in a spreadsheet; no new tool.
5. **Paired comparison against C0**: join on `recording_id`, count queries *fixed* (wrong in C0, right in Cx) and *broken*
   (right in C0, wrong in Cx). Report these two counts, not a p-value.
6. **Partner-distance ratio** for pair-word queries: when the partner is in the top-5, `partner_distance / correct_distance`
   from `queries.csv`. If the partner is not in the top-5 only a lower bound exists; record it as such.
7. **Rate stress** (optional blocks): top-1 for `slow` and `fast` queries vs `normal`, per config.
8. **Warp diagnostics** (C3a/C3b): distribution of chosen alpha per held-out speaker next to that speaker's measured F0.
9. **Median distances** are shown for context only; their scale differs across normalisation/denoise settings, so they are
   not compared between configurations. Compare ranks, margins and counts.

With 40 normal queries per fold, one query is 2.5 points; differences of a query or two are noise. Add Wilson 95% intervals
to pooled rates and say so.

## 9. Decision rules and leakage rules

**Per technique, per variation, one of four outcomes**, decided from the counts in section 8 under these thresholds. They are
engineering heuristics for a smoke test, not significance tests, and are fixed now:

- **Helps**: pooled fixed minus broken >= 3, no fold with more broken than fixed by 2 or more, and partner confusion not
  increased.
- **Harms**: partner confusion up by >= 2 queries on either pair, or pair top-1 down on a vowel-length or gemination pair
  even when overall top-1 rises.
- **Neutral**: within +/-2 and no pair change.
- **Inconclusive**: baseline already at ceiling (>= 95% top-1 on normal queries, so no room to improve) or at floor
  (< 50%, which points at features/endpointing, to be diagnosed with section 5 before any technique is judged). Ceiling does
  not show techniques are unnecessary; it means the next stage needs harder conditions or more confusable words.

Interpretation table (what to look at, and what counts as a failure):

| Variation | Evidence | Technique expected to address it | Failure looks like |
|---|---|---|---|
| Pitch/vocal-tract difference | accuracy of the lowest- and highest-F0 held-out speakers; chosen alpha vs F0 | C2, C3 | no gain for the extreme speaker, or alpha picks are random |
| Speaking rate | slow/fast vs normal | DTW itself (all configs); none of C1-C4 targets it | slow/fast drop and no technique recovers it; pair confusion rises when rate differs |
| Noise | not tested on clean speech | C4 | C4 lowers pair accuracy on clean speech (harm check only) |
| Loudness/channel | C1 vs C0/C2 | normalisation | C1 equal to C0 means the room/device was too uniform to test it |
| Accent/pronunciation | per-word errors by speaker, listening to failures | none here; needs pronunciation variants or an acoustic model | errors concentrated on one word for one speaker |

**Leakage and discipline rules**

1. Words, recording list, fold assignments, configurations (section 7), metrics and thresholds (this section) are committed
   **before the first run**. The commit hash and dataset manifest hashes are quoted in the report.
2. A fold's held-out speaker is never in that fold's enrolment; enrolment speakers are never scored in that fold.
3. No threshold, parameter or word is changed after seeing outputs. Anything added later is labelled post-hoc and excluded
   from conclusions.
4. Diagnostics (F0, duration) are not used to choose a per-speaker warp or per-speaker settings. Warp alpha is chosen per
   query by distance only.
5. Listening to failed queries is allowed to explain errors; it is not a reason to drop, re-record or relabel recordings.
6. The held-out audio is never used for anything but queries in its own fold; no fold's results are used to set another
   fold's configuration.
7. No test split exists here. Nothing from this experiment becomes a default until it is confirmed on **new** speakers who
   were not in this experiment, with that speaker group's results read once under frozen settings (the existing
   calib/test/freeze/ledger process). Recording a fourth speaker now and keeping their audio out of every run here is the
   cheapest way to have such a check.
8. All 21 runs are reported, including worse ones. Real-data results while words are not `verified` stay PROVISIONAL.
9. Report aggregates by speaker code only; per-query files stay private.

## 10. After the clean-speech experiment

Only when the baseline works reliably on clean speech and the section 9 outcomes are decided, extend to real fan/traffic
noise and distance using `docs/PILOT_DATA_COLLECTION.md`. Simulated degradations (`sim:` conditions) remain separate and are
never substituted for real recordings. Spectral subtraction is judged there, because clean speech cannot show its benefit.
Two candidate follow-ups, each needing approval first and neither built now: a per-recording F0 diagnostic wrapper, and a
forced-alignment/duration measurement for the vowel-length and gemination pairs.

## 11. Known limits of this design

- 3 speakers, 1 device, 1 room: no claim about speakers, children, accents, devices or noise.
- Query-side warp is the only vocal-tract technique tested; Kaldi-style VTLN with speaker adaptation, fMLLR, RNNoise,
  forced alignment and self-supervised models are not available in this repository and are not evaluated.
- Top-5 export means pair distance ratios are sometimes only lower bounds.
- Endpointing, QA thresholds and the F0 estimator are untuned on real speech.
