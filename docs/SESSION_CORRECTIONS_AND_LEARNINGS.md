# Sound2Word: corrections, decisions and learnings from the voice-recorder / letter-sound session (2026-10-13)

Purpose: one place that lists, in order, every correction and requirement the project owner gave, what it changed, and what we learned from
the measurements. Numbers come from experiments recorded in `LOCAL_MODELS_AND_TTS.md` and `MALAYALAM_SOUND_INVENTORY.md`; the scripts are in
`tools/model_eval/`. Everything synthetic here is software validation only, not evidence about real speakers (see `CLAUDE.md` gates).

## Part 1. The owner's corrections and requirements, in the order they were given

| # | What you said | What it changed | Status |
|---|---|---|---|
| 1 | The dictionary words must be listed in the sidebar with a search box; the current word gets an **active** class; choosing a word changes the content area. | Sidebar is now a browsable A-Z list (Dictionary / My words tabs), active row highlighted, click adds and selects. | Done |
| 2 | The chosen word must be in the **URL** so a refresh keeps the page; show the **count** of search results or "no words in the list". | `?word=...&q=...&tab=...` kept with `replaceState`; a count line under the search box. | Done |
| 3 | "The **orange bar** cannot be moved." | It was a stale red playback cursor drawn after playback ended. It now shows only while playing or paused. | Done |
| 4 | The **AI model** should tell where each sound starts and ends; we already have models in Rootit Voice. Use the best one (the 4 GB one). | Aligner service + `--aligner-url`; found `indic-core` (4.9 GB) gives text only, no timestamps. Word span from Whisper-family models. | Done, limits known |
| 5 | Use the open datasets (Olam Datuk, enml, E.K. Kurup) with `mlphon` and combine the missing pieces. | `s2w_lexicon.py` reads the real layouts; 257,684-word `lexicon.csv` with meanings, synonyms, pronunciation, source and licence. | Done |
| 6 | List in **alphabetical** order; clean it. | Words not starting with a full letter, or with digits/symbols, removed; sidebar results sorted A-Z. | Done |
| 7 | Compare the models' **performance** (large-v3, vegam-ml, indic-core). | Benchmarked on a sung track against your lyrics; `indic-core` clearly best at recognition, no timings. | Done |
| 8 | Instead of me speaking, can **AI make the dataset**? I want the best model and a test on the first 10 words. | Tried LFM, Indic Parler-TTS (and MMS); scored by a recogniser round trip. | Done |
| 9 | "Why can't you take the token from that project?" | Declined: I do not pull saved credentials out of another app; you logged in yourself. | Closed |
| 10 | Document **all installed models, paths, how to run, how to uninstall**, for future use. | `LOCAL_MODELS_AND_TTS.md`. | Done |
| 11 | How long to build the voice graph for the **whole dictionary**, and how big with voice labels? | Time and size tables (about 170 h and 6 GB for LFM, 4.35 MB for the unit labels). | Done |
| 12 | "Where is the **graph view of each letter** and the combined audio of the word?" | **▤ Graph** on every AI clip and accepted take: waveform, each sound with its own mini graph and play, the whole word. | Done |
| 13 | "Changed sounds **cannot be saved**." and "add a **revert** option to default." | **Save boundaries** (clips and takes) and **Revert to default**. | Done |
| 14 | "Playing **M** I can hear **Ga**." | Playback stopped on the next animation frame (up to 30 ms late). Now scheduled sample-accurately. | Done |
| 15 | "The last **A** is silent, or we need to split **a** from **G**." and "now G is G, but A is something different." | Measured the clip: g is only about 30 ms, the vowel follows, then a low hum tail. The 50 ms minimum spacing was the cause. | Diagnosed; no automatic fix validated |
| 16 | Parler **Harish** keeps generating **extra words**, so it never matches. | Length cap + full stop + cut at the first pause: 9 of 40 exact became 29 of 40. | Done |
| 17 | "What is your guess: **does our logic work**?" | Honest verdict, see Part 3. | Closed |
| 18 | Before matching letters to words, **check that the same letter's sound looks the same across words.** | `unit_consistency.py`: 24% (current boundaries) vs 52% (re-aligned) vs 2.6% chance. | Done |
| 19 | "We do not need to match every word's letters separately. These **words and sounds** are what we build and combine to make every word." (+ your alphabet text, "don't remove anything") | Unit inventory (whole written syllables); your text kept verbatim in `MALAYALAM_SOUND_INVENTORY.md` Part A. | Done |
| 20 | "My guess is this many letter combinations." | Checked against the dictionary: 522 CV syllables, 1,028 distinct clusters, 3,727 distinct written units. | Done |
| 21 | "Can you build those sounds, can they represent the **whole dictionary**, and what is the **size of the labels**?" | 99.8% of words rebuild exactly; labels 4.35 MB; unit audio 837 clips. | Done |
| 22 | "How can I **test** how the generated letter sounds represent the words? Show it **separately in an additional row**." | "Sounds that build this word" row: per-unit play, composed word, graph, similarity test. | Done |
| 23 | **Use ങ്ങ** (whole), the inventory size does not matter, we want **perfection**. | Stop splitting clusters. Whole written syllables become the units. | Agreed, not yet re-generated |
| 24 | We want the **exact sound of each letter**, not what other models or words generate; but **ശ and ഷ** (both "sha") must sound different. | Units are keyed by the Malayalam letter, never by Manglish. Look-alike letters need extra effort and an acoustic separation check. | Agreed, partly built |
| 25 | When combining letters, **remove the ending gaps and smooth the joins**, otherwise it sounds like different voices. | A proper joiner is required (trim to real edges, level and pitch match, crossfade, end fade). Current join is only 2 ms fades. | **Open** |
| 26 | "You are saying it is **not possible** with AI models? I do not mean I record; **generate it with better AI models**." | Switched to a character-level non-autoregressive TTS (MMS-TTS-mal) and cutting units out of words with its own durations. | Tested, first results |
| 27 | "How do you know it is **കാ** and not **ക**? Did you play it? Why not regenerate until exact?" | I cannot hear audio; I used a recogniser transcript and measured length. A recogniser is a poor judge of lone syllables. | Closed |
| 28 | "Why can't you apply the **same technology to our voice recognition / matching**?" | Neural recogniser + nearest dictionary word beat unit matching about 12 to 1. | Done |
| 29 | "Still **ഗേ**, not **ga**: why?" | Formants show the Parler Anjali clip really contains an "e"; the LFM clip is a true "a". The recorder now chooses per unit among several voices. | Done |
| 30 | **List all my corrections and the learnings as a doc.** | This document. | Done |
| 31 | "Do not use ങ ങ instead of ങ്ങ" (they sound different; the word may still be typed *angane*). | A written conjunct is ONE unit everywhere: `s2w_units.decompose` and `build_unit_inventory.py` no longer split by default (`whole=True`); the alphabet has 163 conjuncts and their modified forms as their own entries; `NeverSplitAConjunct` tests guard it. Splitting only by an explicit `whole=False` / `S2W_SPLIT_ALL=1` for comparison experiments. | Done; unit clips must be re-generated from the whole-unit inventory |
| 32 | "Construct words from the alphabet and search on the words: full voice matching with the alphabet voice labels." | `s2w_joiner.py` (proper joins + measurements), `s2w_wordsearch.py` (composed-template DTW, label edit-distance, fusion, eval), recorder page `/search`. Found and fixed missing variance normalisation. See `VOICE_SEARCH.md`. | Built; tested on synthetic tones only |
| 33 | Three papers supplied (Kahn 1984, De Wachter 2002, Sun & Zhao 2014): apply what helps. Reviewer follow-up: do not make the new settings the default without real evidence; test shortlist recall; ablate each part. | Added as OPTIONS (audio cut, feature squeeze, smoothing, in-word clips, rescoring); **default is the plain matcher**. `eval --compare-configs` runs 6 configurations + an isolated-clips ablation and reports top-1/5, MRR, shortlist recall at 10/50/100/200, ms per query, per-speaker top-1. Synthetic tones: no distinguishable gain. These changes are to the separate voice-search tool, not the experiment's matcher or normalisation settings (CLAUDE.md rule 1). | Options built; unproven, not default |

## Part 2. What we learned (measured)

### Recognition, models and timing
* `indic-core` is the best recogniser here: on 60 s of a sung track vs its lyrics, WER 48% / CER 17% (vegam-ml 78% / 43%, large-v3-turbo 100% / 62%).
  It returns text only (80 ms encoder frames), so it cannot place sounds. Whisper-family models give word spans, about 1 s error on song lines.
* `large-v3` was never fully downloaded in Rootit Voice; `large-v3-turbo` and `vegam-ml` are what is installed.
* For **voice to dictionary word**, a neural recogniser plus nearest dictionary word got 41% top-1 / 54% top-5 on 599 candidates, against 0.7%
  (units spoken alone) and 3.6% (units cut out of words) for gluing letter units. The unit inventory is for **making** word audio, not recognising it.

### Boundaries and consistency
* The recorder's spectral first guess is no better than an even split (24% vs 25.5% unit-consistency; chance 2.6%).
* Re-aligning from the data itself roughly doubles consistency (52%) but was wrong on `amga` (it gave the nasal to g). Neither method is validated.
  Ground truth (your hand-corrected boundaries, now savable) is required before choosing a method.
* The cause of "ga inside M" had two parts: a late stop (fixed) and a 50 ms minimum spacing that cannot hold a 30 ms stop (not fixed).

### Generating sounds
* **Autoregressive TTS keeps talking** after the word. Length cap + full stop + cut at the first 200 ms pause took Parler from 9 to 29 exact of 40.
* **A lone letter is not a reliable prompt.** Across three voices the same units fail the same way (ക→കാ, അ→ആ, കു→കൂ, തി→തീ, നി→നീ): isolated
  syllables come out long, 0.28-0.35 s against about 0.08 s inside a word.
* **Recogniser agreement is the wrong judge of a unit.** It answers "റെഡ്" for ര, and returns nonsense for natural 64 ms in-word cuts.
  Use measurements (length, F1/F2) and a native ear instead.
* **F1/F2 check (no recogniser):** Parler Anjali's ഗ vowel is F1 505 / F2 2582 Hz (front "e", like ഏ 401 / 2887); LFM's ഗ is 642 / 1223 Hz
  (open "a", like ആ 903 / 1324). So Anjali really says "ge" and LFM says "ga".
* **Cutting units out of words** (character-level VITS, per-character durations) gives natural lengths (median 176 ms) and exact boundaries, but
  needs several instances per unit; 467 distinct units from 600 words, 200 with 3 or more instances.

### The inventory
* The dictionary has 3,727 distinct written units: 522 consonant+vowel, 3,054 conjunct forms, 123 plain, 21 vowels, 7 chillu.
* Splitting clusters into hold + syllable gives 837 units and rebuilds 99.8% of words exactly; **we decided not to split** (point 23).
* Words average 5.1 written units. Labels for the whole dictionary: 4.35 MB binary, 13.5 MB text.
* The vowel ഔ was missing from the supplied list; names collide (ta, na, la, ra, sha); identify units by the script letter.

## Part 3. The position we ended on

1. **What we are building** is a concatenative letter-sound voice: Malayalam letter in, the exact sound of that letter out, joined into a word.
2. **Identity** is the Malayalam letter; Manglish is only a label (ശ / ഷ / സ stay separate units even though Manglish shows the same text).
3. **Whole written syllables** are the units (ങ്ങ is one unit). Inventory size is not a constraint, correctness is.
4. **Judge units by measurement and ear**, not by a recogniser: duration, F1/F2, separation between look-alike letters, then a listening pass.
5. **Obtain sounds in word context** (cut out of generated words with exact durations) rather than speaking letters alone; pick one voice per
   word so joins do not sound like different people.
6. **Joins must be engineered**: trim to real edges, match level and pitch, equal-power crossfade, one fade at the end, longer hold for doubled
   consonants; measure the jump at each join.
7. **Recognition** (voice to word) uses the neural recogniser plus the dictionary, with rescoring and a frequency prior still to do.

## Part 4. Things I got wrong, so they are not repeated

* I split clusters to shrink the inventory when you asked for fidelity.
* I used a speech recogniser as the judge of a letter sound, and reported ✗ without being able to hear it.
* I presented an isolated-letter prompt as if a TTS could be forced to say an exact letter.
* I stated "the TTS says it long" as fact; it was an inference (a transcript plus a measured length). I cannot play audio.
* I joined units with 2 ms fades and called the result a word.
* I let tools split a conjunct into letter + letter by default (ങ്ങ -> ങ് + ങ), and later "fixed" it by changing how words are *typed*; the rule is: the unit is the conjunct, the typed spelling is a separate convention. See correction 31 and the `NeverSplitAConjunct` tests.

## Part 5. Open items

1. Re-generate the unit set **with whole clusters** and with the in-word cutting approach for all 3,727 units (needs more words per unit).
2. Build the **joiner** and its join measurements (spectral, pitch and level jump at each join).
3. Look-alike pairs: carrier words, an acoustic separation check, and a native listening pass for ശ/ഷ/സ, ത/ട, ദ/ഡ, ന/ണ, ര/റ, ല/ള/ഴ and chillu.
4. Hand-correct about 25 words with **Save boundaries** to get ground truth, then choose the boundary method by error in milliseconds.
5. Recognition: rescore the recogniser's top candidates with its own likelihood, add a word-frequency prior, and test on real voices.
6. Choose one voice for composed words (mixing voices in one word is audible).

## Part 6. Label audit and voice-search build (2026-10-14): findings and measured before/after

All numbers are software checks on made-up data or the example words; none is recognition accuracy on real speech.

### Label audit against real words (86 example words)
| Finding | Before | After / fix |
|---|---|---|
| Example words fully covered by unit labels | 76 / 86 | 86 / 86: added modified forms (sign + anusvara, conjunct + chandrakkala / anusvara / visarga) and 20 more conjuncts |
| Alphabet entries | 600 | 5,596 (143+ standard conjuncts x 13 forms, plus 2,877 modified forms) |
| Joined labels spell the word like the recorder rule | n/a | 66 / 86 (the 20 others: final chandrakkala "u", long vowel signs, nasal voicing; unverified) |
| Duplicate human labels | 2 entries shared "k" (ക് and ൿ) | 0 |
| ങ്ങ pronounced as /ŋɡ/ ("finger") | wrong (my error) | /ŋː/, a long nasal; Manglish label stays "ngga" |
| അഃ labelled "aha" but കഃ "kah" | inconsistent | "ah" |
| `decompose()` split every conjunct by default | ങ്ങ -> ങ് + ങ | `whole=True` default; guard tests `NeverSplitAConjunct` (correction 31) |
| I changed how words are *typed* (angane -> anggane) | overreach | reverted; typed spelling and sound unit are separate |

### Voice search (docs/VOICE_SEARCH.md)
| Measurement (synthetic tones, 63 queries: 21 words x 3 noise/speed conditions) | Correct top-1 |
|---|---|
| MFCC with mean removal only (before) | 28 / 63 (44 %) |
| MFCC with mean and variance normalisation (after) | 61 / 63 (97 %) |
| plus a length penalty | 30 / 63 and 61 / 63 (no real gain) |

* The old recorder join (2 ms fades) is replaced by trim + level match + equal-power crossfade; each join is measured (level, spectral, pitch step). Pitch is measured, not matched.
* **Not measured:** anything on real speech. The earlier 0.7 % / 3.6 % (glued split units) and 41 % (recogniser + dictionary) were different pipelines on synthetic audio and are not comparable with the table above.
