# Sound2Word roadmap

Offline-first, Malayalam-first, C core + Kotlin Android.

| Milestone | Scope | Status |
|-----------|-------|--------|
| M1 | Phase 1: audio inspector (WAV read/validate, stats, warnings) | done |
| M2 | Phase 2: feature engine (log-Mel / MFCC, normalisation) | done (docs/FEATURES.md) |
| M3 | Phase 3: constrained DTW matcher, top-k, rejection | done, synthetic-tested only (docs/DTW.md) |
| M4-M9 | Later phases per the program model | planned |

## Rules carried from the blueprint
- No raw-waveform matching; match on features.
- Preserve duration cues; do not over-trim.
- No fake probabilities: report scores as scores, with an explicit reject path.
- Keep train and evaluation data strictly separate.
- Offline by default; no network use in the core.

## Phase 3.1 - evaluation foundation (done, synthetic-tested)
Dataset spec/consent (docs/DATASET.md), schemas (data/schema), validator `s2w_dataset_check`, harness `s2w_eval` (docs/EVALUATION.md). No real Malayalam data or accuracy numbers exist yet.

## Next
1. Native-speaker review of the word list (two reviewers) and minimal pairs, using `docs/WORD_REVIEW.md` and `data/schema/wordlist_review_template.xlsx` (tooling done; the review itself is not).
2. Collect pilot recordings (30-50 words, 10-20 speakers, 3 reps) with consent; validate with `s2w_dataset_check`.
3. Run the untouched baseline on calib, record it; then compare normalisations, tune on calib only, freeze, evaluate test once.
4. Phase 4 (better matcher or pretrained Malayalam acoustic model) only after the measured baseline.

## Phase 1 CLI
`make test && build/s2w_inspect file.wav` prints deterministic key=value lines.
Exit code: 0 clean, 1 usable with warnings, 2 error.
