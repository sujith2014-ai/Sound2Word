# Brief for the vocabulary proposer (native Malayalam speaker)

You are drafting candidates; two other speakers will review them blind. Please do not ask software or
anyone else to supply the words for you - the point is native-speaker judgement. Work in the **Master**
sheet of `wordlist_review_template.xlsx`, yellow columns only.

## What to propose
- **30-50 enrolled words (`vocab`, rows W001...)**, familiar everyday words people say naturally in
  isolation. Avoid proper nouns, very rare words, and words whose spelling varies a lot between regions
  (or note the variants).
- **Contrast pairs.** Suggested mix for 40 words (adjust freely): about 5-6 *vowel-length* pairs
  (short vs long vowel), about 5-6 *gemination* pairs (single vs geminate consonant), and the rest
  single words that cover a spread of vowels, consonant places/manners and word lengths. A pair uses one
  shared Pair ID (e.g. P01) on both words and the same Contrast type (`vowel_length`, `gemination`, `other`).
- **Pair quality:** both words must be real, familiar words; they should differ only in the intended feature
  (as far as natural Malayalam allows). A valid word with no good partner is fine as a single word; do not
  force a weak pair.
- **10 unknown-word slots (`oov`, rows U001...)**: familiar-sounding Malayalam words that will **not** be in
  the enrolled vocabulary. Make most clearly different from the vocabulary and a few phonetically close to
  enrolled words, so rejection is tested on easy and hard cases. They never go in a pair.

## For each row
Malayalam spelling (candidate), English meaning/context, optional romanisation, Pair ID + Contrast type (or
leave `none`), and your pseudonymous ID in *Proposed by*. Leave IPA blank unless someone competent in phonetic
transcription has checked it. Type the spelling you intend, including any ZWJ/ZWNJ (chillu / conjunct
forms); reviewers will check it exactly.

## Then
Hand the filled Master to the project owner, who runs `review_sheet.py split` (see `docs/WORD_REVIEW.md`).
Do not review your own proposals unless you are one of the two reviewers; if you are, keep your review
independent of the other reviewer.
