# Sound2Word roadmap

Offline-first, Malayalam-first, C core + Kotlin Android.

| Milestone | Scope | Status |
|-----------|-------|--------|
| M1 | Phase 1: audio inspector (WAV read/validate, stats, warnings) | done |
| M2 | Phase 2: feature engine (log-Mel / MFCC, normalisation) | next |
| M3 | Phase 3: constrained DTW matcher with rejection | planned |
| M4-M9 | Later phases per the program model | planned |

## Rules carried from the blueprint
- No raw-waveform matching; match on features.
- Preserve duration cues; do not over-trim.
- No fake probabilities: report scores as scores, with an explicit reject path.
- Keep train and evaluation data strictly separate.
- Offline by default; no network use in the core.

## Phase 1 CLI
`make test && build/s2w_inspect file.wav` prints deterministic key=value lines.
Exit code: 0 clean, 1 usable with warnings, 2 error.
