# Sound2Word: project structure (as of 2026-10-14, branch `main-asw1kr`)

Offline-first, Malayalam-first word recogniser: a C core (features + DTW) with C evaluation tools, plus local Python tools for
recording, the alphabet/voice labels, the dictionary and an experimental voice-to-word search.

**Status: prototype.** The tools run and are tested on synthetic or made-up data. No claim about Malayalam recognition accuracy is made;
that needs verified words, consenting speakers and real recordings (gates A/B/C in `CLAUDE.md`).

## Layout
```
CLAUDE.md                 project rules: experiment gates A/B/C, "never split a conjunct"
README.md  Makefile       build / test entry points (make all | test | test-review | test-asan | test-real)
core/                     C engine
  include/ src/           audio (WAV), resample, features (log-Mel/MFCC), DTW, QA
  tests/                  C unit tests
evaluation/               C evaluation harness
  src/ include/ tests/    dataset loader/checker, degrade (noise/clipping/etc.), eval, ingest rules, json/csv/sha256, settings, pitch
data/
  schema/                 tracked templates: wordlist, recordings, consent/participant registers, pilot matrix/conditions
  drafts/                 AI-drafted candidate words (UNVERIFIED)
  alphabet/               generated label set: alphabet.csv, inventory.json (5,596 entries)
  private/                git-ignored: audio, lexicon, units, review workbooks, recordings (never committed)
docs/                     protocols, experiment designs, learnings (index below)
models/MODEL_MANIFEST.md  which local models exist and where (weights are not in the repo)
tools/                    Python and C command-line tools (below)
```

## Tools (`tools/`)
| file | role |
|---|---|
| `s2w_features.c`, `s2w_match.c`, `s2w_qa.c`, `s2w_ingest.c`, `s2w_eval.c`, `s2w_degrade.c`, `s2w_dataset_check.c`, `s2w_inspect.c`, `s2w_make_fixture.c` | C command-line tools built into `build/` |
| `s2w_viewer.py` | read-only Acoustic Viewer (waveform, log-Mel, QA) |
| `s2w_recorder.py` | local recorder web app (loopback only, per-run token): live recording, sound-by-sound segment editor, compare page, dictionary sidebar, `/alphabet`, `/search` (voice search) |
| `s2w_alphabet.py` | Malayalam alphabet inventory: Unicode block partition (118), consonant x 16 forms, 143+ conjuncts x 13 forms, modified forms, chillu; unique `sound_id`, voice labels, look-alike detection; CLI `inventory`, `markdown`, `unicode`, `lookalikes`, `labels` |
| `s2w_units.py` | cut a word into written sound units; **a conjunct is one unit** (`whole=True` default) |
| `s2w_lexicon.py` | merge open dictionaries (Olam Datuk/enml, E.K. Kurup, mlphon) into `lexicon.csv` |
| `s2w_joiner.py` | glue unit clips: trim, level match, equal-power crossfade, end fade, measured joins |
| `s2w_wordsearch.py` | voice -> dictionary word: composed unit templates (DTW), recogniser-text label matching, fusion, `eval` |
| `s2w_aligner_service.py` | optional forced-alignment HTTP service the recorder can call |
| `consent_gate.py`, `pilot_plan.py`, `review_sheet.py`, `make_ai_draft.py`, `run_enrolment_experiment.py`, `run_robustness.py` | pilot planning, consent gate (Gate B), word-review workbook (Gate A), synthetic experiments |
| `model_eval/` | experiments with local models on a PC/GPU: TTS generation (`gen_*`: LFM, Parler, MMS), `build_unit_inventory.py`, `unit_consistency.py`, `compose_rank.py`, `mms_rank.py`, `roundtrip.py`, `asr_match.py` ... |
| `run_recorder_container.sh` | start the recorder in Docker with bind-mounted data |
| `test_*.py`, `test_*_browser.js` | Python unit tests and Playwright browser checks (all use made-up data) |

## How the pieces connect
```
lexicon.csv (dictionary) --> s2w_units (whole units) --> s2w_alphabet (labels, inventory.json)
inventory.json --> model_eval/gen_units.py (TTS clips) --> recorder /alphabet (listen, verdicts)
unit clips + lexicon --> s2w_joiner --> s2w_wordsearch (composed) --+
recogniser text -------> s2w_wordsearch (labels) -------------------+--> fused ranking --> recorder /search
recordings --> recorder (QA, segments) --> evaluation/ (C) once Gates A/B/C pass
```

## Tests
`make test` (C), `make test-review` (C tools plus all Python suites: viewer, recorder, lexicon, alphabet, wordsearch, pilot/consent/review),
`node tools/test_*_browser.js` (needs Playwright + Chromium). Current: 56 alphabet, 36 recorder, 23 wordsearch, 25 viewer, 15 lexicon tests;
browser checks 48 (recorder), 35 (sidebar), 31 (alphabet), 32 (viewer).

## Documents (`docs/`)
- Rules and experiment: `EXPERIMENT_NORMALISATION_REAL_SPEECH.md`, `EXPERIMENT_NOISE_ENROLMENT.md`, `EVALUATION.md`, `BASELINE.md`
- Pilot and recording: `PILOT_*`, `RECORDING*.md`, `CONSENT_TEMPLATE.md`, `WORD_REVIEW.md`, `DATASET.md`, `templates/`
- Engine: `FEATURES.md`, `DTW.md`, `ROADMAP.md`, `PROPOSER_BRIEF.md`
- Malayalam sounds and models: `MALAYALAM_SOUND_INVENTORY.md`, `MALAYALAM_ALPHABET_LABELS.md` (generated; a test fails if stale), `LOCAL_MODELS_AND_TTS.md`, `VOICE_SEARCH.md`
- History: `SESSION_CORRECTIONS_AND_LEARNINGS.md` (every correction, measurements, before/after), this file

## What is and is not done
| area | state |
|---|---|
| C core, evaluation harness, QA, consent gate | built and tested (synthetic fixtures) |
| Recorder, compare, dictionary, alphabet, search pages | built, tested; run on your PC with Docker |
| Alphabet labels (5,596 entries) | generated; **curated text unverified** (needs a native listener) |
| Unit audio for the alphabet | **not generated** for the whole-unit inventory (old clips used split units) |
| Voice search accuracy | **unmeasured on real speech** (synthetic tones only) |
| Gate A (8 words verified by two reviewers) | not passed |
| Gate B (consented recordings) | not started |
| Gate C (frozen config, locked test split) | not started |

## Private data (never committed; `.gitignore`)
`data/private/` (audio, lexicon, units, recordings, review workbooks), `*.wav`, consent and participant registers, session logs.
