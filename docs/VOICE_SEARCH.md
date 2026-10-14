# Search the dictionary by voice

Status: **built and tested on made-up tones only. No accuracy on real speech is known.** Do not quote any figure from the synthetic tests.

## What it does
A spoken word is matched against dictionary words that are *constructed from the alphabet's unit clips* (whole written syllables and conjuncts; ങ്ങ is one unit).

| method | needs | how |
|---|---|---|
| composed | unit clips (`gen_units.py` output), `lexicon.csv` | join the unit clips of each candidate word with `tools/s2w_joiner.py` (trim, level match, equal-power crossfade, end fade), rank by DTW of CMVN-normalised MFCCs |
| labels | text from any recogniser (Malayalam script) | cut the text into the same units, weighted edit distance (look-alike letters ശ ഷ സ, ന ണ, ര റ, ല ള ഴ ... cost less) |
| fused | both | reciprocal-rank fusion |

## Run it (on your PC, in the Docker build tree)
```
python3 tools/s2w_wordsearch.py search --query take.wav --units data/private/units_audio --lexicon data/private/dictionary/lexicon.csv --heard "അങ്ങനെ"
python3 tools/s2w_wordsearch.py eval --queries data/private/queries --units data/private/units_audio --lexicon data/private/dictionary/lexicon.csv --heard-file heard.json
```
`eval` prints top-1 / top-5 / MRR per method (closed-dictionary queries only; out-of-dictionary and unusable queries are counted beside them), the chance level, and every query including failures (a word whose units have no clip counts as not found, never skipped).
In the recorder: start with `--unit-audio-dir ... --dictionary ...` and open **Search by voice** (`/search`): pick a recorded take, optionally paste what a recogniser heard.

## Bugs found while building it
* **Variance normalisation was missing**: with mean-removed MFCCs alone, short loud templates (e.g. `സമ`) won about half of the synthetic queries (28/63 correct). With CMVN 61/63. Kept as a test (`test_composed_search_finds_the_spoken_word...`).
* The recorder joined units with 2 ms fades. It now trims, matches levels and crossfades, and reports the level / spectral / pitch step at every join (`/api/unitjoin?meta=1` → `joins`). Pitch is **not** matched, only measured.
* Candidate words without a clip for every unit are counted and reported, not silently dropped.

## Before / after (synthetic tones only)
| | Correct top-1 |
|---|---|
| mean-removed MFCC (before) | 28 / 63 (44 %) |
| mean + variance normalised MFCC (after) | 61 / 63 (97 %) |

63 queries = 21 made-up words x 3 conditions (2 % noise + 15 % slower, 5 % noise, 2 % noise + 15 % faster). This shows the software fix, not accuracy on speech.

## Optional changes taken from the literature: NOT the default
Sources: Kahn, Rabiner & Rosenberg 1984 (demisyllable duration and smoothing rules); De Wachter et al. 2002 and Sun & Zhao 2014 (template matching, rescoring).
**The default is the plain matcher.** The synthetic results below did not show a top-1 gain and the rule is sensitive to query duration, so none of these is accepted until real, held-out speech shows a reproducible benefit (all of it is switchable and compared by `eval --compare-configs`).

| option | where | how to switch on |
|---|---|---|
| audio cut: a unit spoken alone, longer than 0.2 s, not last, loses its sustained middle (about half kept, no resampling). Loses any spectral transition inside the removed part | `s2w_joiner.shorten` | `--duration cut` |
| feature squeeze: same units keep all their audio, only their feature frames are time-compressed (duration-aware, nothing cut) | `Composer._squeeze` | `--duration squeeze` |
| join smoothing of template features (audio crossfade is separate: 30 ms, at most a quarter of the shorter unit) | `s2w_joiner.smooth_features` | `--smooth-ms 60` |
| clips cut out of real words win over isolated clips and are never shortened | `load_bank` (several folders; `use_in_word=False` for the ablation) | automatic when the meta voice says "cut from word" |
| rescoring: composed DTW only on the label method's top N | `search_with(rescore_top)`, own column `rescored` | `--rescore-top 200` |
| length filter uses the shortened estimate | `Composer.est_duration` | follows `--duration` |

### What `eval --compare-configs` reports (so each effect is separate)
Six configurations (plain, audio cut, feature squeeze, smoothing, cut + smoothing, squeeze + smoothing), plus one **ablation with isolated clips only** whenever the bank has cut-out clips. Per method: top-1, top-5, MRR, not-found, **mean ms per query**, top-1 **per speaker** (if the query meta has `speaker`), and every query as a row. `shortlist_recall` gives how often the right word is inside the label method's top 10 / 50 / 100 / 200: rescoring can never recover a word outside its shortlist, so read `rescored` against that number.

### Two evaluation conditions: known word, unseen speaker
`eval` labels every query: **unseen_speaker** (the speaker did not supply any reference clip), **seen_speaker** (they did), or **speaker_unknown**. Reference clips carry a speaker when their meta item has a `speaker` field (TTS voices are not people). A query whose speaker also supplied a reference clip is **refused** (exit code 3, "not an unseen-speaker test") unless `--allow-same-speaker` is given; then it is reported under `by_condition.seen_speaker` and never mixed into the unseen-speaker figures. Queries whose word is not in the dictionary are counted separately (`not_in_dictionary`) because they cannot be recognised by construction. `--units` accepts several folders. With only eight words any figure is preliminary: expand the vocabulary and the speakers before broader claims.

### Reference metadata must be trustworthy
The seen / unseen classification is only as good as the metadata on the reference clips. Each reference clip must be either **a person** (`speaker`: a stable, anonymised participant ID) or **generated** (`"synthetic": true`, with the voice named in `voice`). `eval` reports `reference_metadata` in its summary (speakers, synthetic clips, unattributed clips, conflicting clips) and:
* **unattributed** (no speaker, not synthetic): a person's clip could hide here, so their queries would be wrongly called unseen. `eval` prints a warning; `--strict-metadata` refuses (exit 4).
* **conflicting** (marked synthetic *and* given a speaker): same handling.
* Generated voices never become speakers, so a query labelled with a TTS voice name is not a leak.
* The check covers every folder passed to `--units`; pass **all** human reference folders together, or a speaker whose clips are in a folder you left out will look unseen.
* A word missing from the dictionary is reported with its count (`not_in_dictionary`) and never silently dropped from a denominator; `by_condition` figures cover in-dictionary words only, so quote both numbers.

### Gate C: freeze and verify (`freeze`, `eval --frozen`)
```
python3 tools/s2w_wordsearch.py freeze --queries Q --units REF_DIR... --lexicon L --out freeze.json [--duration none|cut|squeeze] [--smooth-ms N] [--shortlist N] [--rescore-top N]
python3 tools/s2w_wordsearch.py eval   --queries Q --units REF_DIR... --lexicon L --frozen freeze.json [--allow-test-reuse]
```
`freeze` refuses (exit 7) an invalid setup: empty bank, incomplete reference provenance, a query without a speaker ID, a query that leaks from the reference set (same speaker or identical audio), no queries, or a dirty code tree (`--allow-dirty` to override and have it recorded). The file pins the settings, the SHA-256 of the dictionary, of every reference clip and of every query clip (with its word and speaker), the git commit, and its own hash. `eval --frozen` takes its settings from the file and refuses (exit 5) if the file or any input changed; it cannot be combined with `--compare-configs`. A ledger (`test_ledger.jsonl` in the queries folder) records each evaluation: the identical configuration may be re-run, a *different* configuration on the same query set is refused (exit 6) unless `--allow-test-reuse`, and the result then says `test_reuse`. A freeze whose reference clips are all synthetic is labelled SYNTHETIC and cannot complete Gate C (`tools/gate_status.py`).

### What the summary reports (after the audit, `docs/DEFECT_AUDIT.md`)
`queries` (total / in_dictionary / out_of_dictionary / unusable); per method closed-dictionary `n`, `top1`, `top5`, `mrr`, `not_found`, `mean_ms`, `top1_by_speaker`; `by_condition` (`seen_speaker`, `unseen_speaker`, `unseen_unverified`, `speaker_unknown`); `unseen_speaker_claim_valid`; `reference_metadata` (speakers, synthetic, unattributed, conflicting, skipped clips, files hashed); `not_in_dictionary`; `composed_stages` (found / no_clips / length_filter / template_limit / stage1_shortlist / unusable_audio); `missing_heard` and `heard_unusable` (recogniser text that is not Malayalam is a counted miss with `heard_error` in its row, never an abort); `shortlist_recall` at 10 / 50 / 100 / 200 and at the configured `rescore_top`. Speaker IDs are compared after NFC + trim + case-fold; a query whose audio file is a byte copy of a reference clip counts as seen. Exit codes: 3 leak, 4 strict provenance, 5 frozen mismatch, 6 test reuse, 7 not frozen.

### Measured on made-up tones only (21 words, noise 0.03, top-1 / top-5 of the composed ranking)
| configuration | query units 0.11 s (like in-word syllables) | query units 0.22 s (like isolated clips) |
|---|---|---|
| plain (default) | 0.95 / 0.95 | 0.95 / 1.00 |
| audio cut | 0.86 / 1.00 | 0.86 / 0.95 |
| feature squeeze | 0.95 / 0.95 | 0.86 / 1.00 |
| smoothing 60 ms | 0.95 / 0.95 | 0.95 / 1.00 |
| audio cut + smoothing | 0.95 / 1.00 | 0.81 / 1.00 |
| feature squeeze + smoothing | 1.00 / 1.00 | 0.86 / 1.00 |

Reading: differences are one or two queries out of 21, so none of them is distinguishable from noise; the only clear pattern is that shortening hurts when the query is as long as an isolated clip (expected). The synthetic tones are steady sounds, so they cannot show a real duration effect or the loss of transitions. The shortlist recall in this fixture is 1.0 at every N only because the made-up recogniser text is exact and the dictionary has 23 words: it says nothing about a real recogniser. **Needed before any default changes:** verified words, several consenting speakers, a held-out test split never used for tuning (CLAUDE.md gates A/B/C), reported per word and per speaker, with failures.
The recorder's own "composed word" playback joins clips without the duration rule.

## Limits
* Unit clips spoken alone have the wrong length (about 0.3 s, against 0.08 s inside words), so cut units out of words where you can.
* The earlier experiment (`compose_rank.py`) got 0.7 % / 3.6 % top-1 on synthetic audio and the recogniser + dictionary baseline 41 % top-1. Expect composed matching to be the weaker method until the units are regenerated whole and joined properly; run both and look at the per-word table.
* Everything here is unverified until real, consented recordings exist (CLAUDE.md gates A/B/C).
