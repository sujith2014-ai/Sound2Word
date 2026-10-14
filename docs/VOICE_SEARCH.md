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
`eval` prints top-1 / top-5 / MRR per method, the chance level, and every query including failures (a word whose units have no clip counts as not found, never skipped).
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

## Limits
* Unit clips spoken alone have the wrong length (about 0.3 s, against 0.08 s inside words), so cut units out of words where you can.
* The earlier experiment (`compose_rank.py`) got 0.7 % / 3.6 % top-1 on synthetic audio and the recogniser + dictionary baseline 41 % top-1. Expect composed matching to be the weaker method until the units are regenerated whole and joined properly; run both and look at the per-word table.
* Everything here is unverified until real, consented recordings exist (CLAUDE.md gates A/B/C).
