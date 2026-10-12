# Sound2Word

Offline-first, Malayalam-first speech-to-word recognition engine (C core + native Android Kotlin).

See the technical program model for the phased implementation plan.

## Build
`make test` builds the C core and runs tests; `build/s2w_inspect file.wav` inspects a WAV. See `docs/ROADMAP.md`.

Dataset and evaluation: `docs/DATASET.md`, `docs/EVALUATION.md`. Frozen baseline: `docs/BASELINE.md`. Fixtures are synthetic; no accuracy claim about Malayalam is made yet.
Recording, QA and simulated-condition testing: `docs/RECORDING.md`.
