# Phase 3: constrained DTW matcher (`core/src/dtw.c`)

## Distance
Frame cost = RMS difference between feature vectors (`sqrt(sum (q-t)^2 / dim)`), recursion
symmetric2 (diagonal `2*d`, horizontal/vertical `d + warp_penalty`), normalised by `n + m`.
Scores are **distances, not probabilities**. Default representation is normalised MFCC (classical
baseline); `S2W_REP_LOGMEL` is supported for comparison and future neural work.

## Constraints
- **Duration (hard):** `n/m` must lie in `[min_len_ratio, max_len_ratio]` (default 0.5-2.0); a
  violating pair has distance `+inf` and is skipped, not an error.
- **Warping (hard):** Sakoe-Chiba band, half-width `ceil(band_ratio * max(n,m))` (default 0.30)
  around the straight start-to-end line.
- **Duration cue (soft):** `warp_penalty` (default 0.10) is charged on every non-diagonal step, so
  matching a short vowel to a long one costs more than the spectra alone imply. This keeps
  Malayalam length / gemination contrasts visible. Defaults are untuned starting values.

## Ranking and rejection
`s2w_matcher_match` takes the best template per label, sorts ascending (ties: label, then insertion
order), returns top-k (<= 10). Rejection reasons, checked in order:
1. `QUALITY` - query has a Phase 1 warning in `quality_mask` (default too-short, mostly-silent).
2. `NO_CANDIDATE` - every template excluded by the duration constraint.
3. `DISTANCE` - best distance above `max_distance`.
4. `AMBIGUOUS` - top-two margin `(d2-d1)/d1` below `min_margin_ratio`.

Thresholds are **off by default** and results carry `rejection_active`. Without held-out data no
default can be honest. Use `s2w_calibrate_threshold(genuine, impostor, max_far)` on recordings that
were not used as templates; it returns the threshold plus the FAR/FRR actually achieved on that data.
Genuine/impostor sets must be speaker- and session-disjoint from template enrolment to be meaningful.

## CLI
`build/s2w_match manifest.txt query.wav [--k N] [--rep mfcc|logmel] [--max-distance X] [--min-margin X]`
Manifest lines: `label path` (`#` comments). Exit 0 accepted, 1 rejected, 2 error.

## Limits and honesty
- Tests use synthetic tone "words": they check mechanics (constraints, ranking, rejection,
  calibration, determinism), not Malayalam recognition accuracy. Real, multi-speaker recordings are
  required before any accuracy claim; `make test-real` currently only smoke-tests inputs.
- Template features and queries must share one feature config (`S2W_ERR_INCOMPATIBLE` otherwise).
- Single-word isolated matching only; no continuous speech.
