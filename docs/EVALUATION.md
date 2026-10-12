# Evaluation harness (`s2w_eval`)

Code validation vs recognition validation: the harness is tested only on **synthetic fixtures**
(`build/s2w_make_fixture`, `kind=synthetic`). Every artefact from a synthetic dataset carries the
banner "SYNTHETIC FIXTURE RUN: software validation only. NOT Malayalam recognition accuracy." Results
on real data are marked PROVISIONAL unless every used word is `verified` and every recording has
`label_verified=yes`.

## Workflow
```
make all
build/s2w_dataset_check DATASET [--require-verified] [--require-participants]   # validate first
build/s2w_dataset_check DATASET --emit-hashes > hashes.csv                      # pin audio into recordings.csv
build/s2w_eval run          --dataset DATASET --settings base.cfg --split calib  # baseline BEFORE any tuning
build/s2w_eval compare-norm --dataset DATASET --settings base.cfg                # none / mean / meanvar (calib only)
build/s2w_eval calibrate    --dataset DATASET --settings base.cfg --max-far 0.02 --out cal.cfg
build/s2w_eval freeze       --settings cal.cfg --out frozen.cfg
build/s2w_eval run          --dataset DATASET --settings frozen.cfg --split test # once
```
Settings files: `#s2w-settings v1` then `key=value` (see `evaluation/src/settings.c`; any omitted key
takes the baseline default). Record the untouched-baseline calib run first; change settings only
afterwards, one decision at a time, using calib results only.

## Safeguards
- **Test lock:** `--split test` needs frozen settings. Frozen files carry a SHA-256 of their content;
  editing a frozen file is detected and refused. Rejection thresholds must name a `calibration_source`.
- **Tuning never reads test audio:** `calibrate`, `compare-norm` and calib runs open only enrol and
  calib audio (tested by deleting the test audio).
- **Ledger:** each test evaluation is appended to a ledger (`evaluation/test_ledger.txt`, ignored by
  git). A second test evaluation with *different* settings on the same dataset is refused unless
  `--allow-test-reuse`, and the result then says so. Re-running identical frozen settings is
  allowed (reproduction). The ledger is a process control, not tamper-proof.
- **Pinned data:** the manifest hash (meta + word list + recordings + participants) is in every
  result, and per-recording `audio_sha256` is verified at evaluation time.
- Runs never overwrite a previous run directory.

## What is reported
Each run writes `evaluation/runs/<run_id>/` (git-ignored; per-query files can identify speakers):
`result.json` (machine-readable), `summary.txt`, `settings.cfg`, `queries.csv`.

`result.json`: schema/run id/time; **evidence** (synthetic vs real, verification counts, scope
statement); **code** (git commit, dirty flag, describe, baseline reference, feature version);
**dataset** (id, version, manifest hash, split counts); **settings** (features, DTW, rejection,
calibration source, frozen flag, settings hash); **test_access**; **metrics**; **groups**;
**minimal_pairs**.

Every rate is `{num, den, value}`; when `den == 0` the value is `null` with `undefined_because`.
- Known words: Top-1/3/5 (ranking, independent of rejection; unusable queries count as misses),
  accepted-and-correct, accepted-but-wrong-word, **false reject rate**, precision among accepted,
  rejections by reason.
- Unknown (OOV) words, reported separately: **false accept rate**, correct rejection rate, by reason.
  With no thresholds set, unknown false accepts are 100% by construction and the report says so.
- Groups: device, environment/noise condition, speaking style, **measured** pitch tercile (median
  F0 from audio; needs >= 6 speakers with a measurable F0), and voluntary sex/age/accent
  (only participants with `group_reporting_ok=yes`, never `declined`). Groups under
  `--min-group-speakers` (3) or `--min-group-queries` (30) are suppressed and not named.
- Minimal pairs: per pair and per contrast type (`vowel_length`, `gemination`, `other`): top-1
  correct, confused with partner, other wrong, no candidate.
- `compare-norm`: Top-k, minimal-pair confusion and median distances per normalisation, rejection off
  (distance scales differ per normalisation; re-calibrate thresholds for whichever is chosen).
- `calibrate`: chooses `max_distance` so that the achieved impostor acceptance on the calib split is
  <= `--max-far` (impostors = OOV words; `--impostors oov+wrong` also includes known words ranked
  wrong). Reports FAR resolution (1/N), in-sample FAR/FRR (optimistic) and a margin sweep. It does not
  pick `min_margin_ratio`; choose it from the sweep and write it into the settings file yourself.

## Limits
- Isolated-word, speaker-independent protocol only (schema v1).
- Small pilots give wide uncertainty; the harness reports counts, not confidence intervals - read
  denominators before comparing numbers.
- Genuine unknown-word evaluation needs OOV words that were never enrolled and are spoken by test
  speakers; the validator enforces the enrolment part only.
