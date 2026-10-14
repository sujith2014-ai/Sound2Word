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

## Changes taken from the literature (2026-10-14) and what they measured
Sources: Kahn, Rabiner & Rosenberg 1984 (demisyllable duration and smoothing rules); De Wachter et al. 2002 and Sun & Zhao 2014 (template matching, rescoring).

| change | where | note |
|---|---|---|
| duration rule: a unit spoken alone, longer than 0.2 s and not last in the word, is cut to about half (middle removed, onset + offset kept, no resampling) | `s2w_joiner.shorten`, `Composer(duration_rule=True)` | Kahn: about half of the isolated length |
| join smoothing: template features smoothed over 60 ms around each join; audio crossfade 30 ms, never more than a quarter of the shorter unit | `s2w_joiner.smooth_features`, `join` | Kahn: 60-90 ms best, little loss without |
| clips **cut out of real words** win over isolated clips and are never shortened (`voice` contains "cut from word") | `s2w_wordsearch.load_bank` (accepts several folders) | both template papers use real segments with context |
| rescoring: composed DTW only on the label method's top 200 candidates | `search_with(rescore_top)`, result key `rescored`, shown on `/search` | Sun & Zhao: recogniser first, templates rescore |
| the length filter now uses the **shortened** duration estimate | `Composer.est_duration` | otherwise long isolated words would be dropped before DTW |
| `eval --compare-configs` runs all four configurations and reports each | `s2w_wordsearch.compare_configs` | CLAUDE.md rule 5: every configuration, including the worse ones |

**Measured on made-up tones only** (21 words, noise 0.03; top-1 / top-5 of the composed ranking; the tones are stationary, so they cannot show a real duration effect):

| configuration | query units 0.11 s (like in-word syllables) | query units 0.22 s (like isolated clips) |
|---|---|---|
| plain | 0.95 / 0.95 | 0.95 / 1.00 |
| duration rule only | 0.86 / 1.00 | 0.86 / 0.95 |
| smoothing only | 0.95 / 0.95 | 0.95 / 1.00 |
| duration rule + smoothing (default) | 0.95 / 1.00 | 0.81 / 1.00 |

Reading: no gain in top-1 on tones, a small top-5 gain with in-word-length queries, and a loss when the query is as long as the isolated clips (expected: the rule removes that length). **These tones cannot confirm the papers' result; run `eval --compare-configs` on real recordings before keeping or dropping the defaults.** The recorder's own "composed word" playback still joins the clips without the duration rule.

## Limits
* Unit clips spoken alone have the wrong length (about 0.3 s, against 0.08 s inside words), so cut units out of words where you can.
* The earlier experiment (`compose_rank.py`) got 0.7 % / 3.6 % top-1 on synthetic audio and the recogniser + dictionary baseline 41 % top-1. Expect composed matching to be the weaker method until the units are regenerated whole and joined properly; run both and look at the per-word table.
* Everything here is unverified until real, consented recordings exist (CLAUDE.md gates A/B/C).
