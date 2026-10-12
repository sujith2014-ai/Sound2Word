# Sound2Word

Offline-first, Malayalam-first speech-to-word recognition engine (C core + native Android Kotlin).

See the technical program model for the phased implementation plan.

## Build
`make test` builds the C core and runs tests; `build/s2w_inspect file.wav` inspects a WAV. See `docs/ROADMAP.md`.

Dataset and evaluation: `docs/DATASET.md`, `docs/EVALUATION.md`. Frozen baseline: `docs/BASELINE.md`. Fixtures are synthetic; no accuracy claim about Malayalam is made yet.
Recording, QA and simulated-condition testing: `docs/RECORDING.md`.
Noise-matched enrolment experiment (synthetic only): `docs/EXPERIMENT_NOISE_ENROLMENT.md`.
Pilot data-collection plan (protocol, conditions, matrix; no real data yet): `docs/PILOT_DATA_COLLECTION.md`.
Pilot readiness: `docs/PILOT_DATA_COLLECTION.md`, `docs/PILOT_SPLIT_DESIGN.md`, `docs/PILOT_RECRUITMENT_AND_CONSENT.md`, `docs/RECORDING_SETUP_GUIDE.md`.
