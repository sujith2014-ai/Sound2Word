# Malayalam word-list review (before any recording)

Goal: two Malayalam speakers independently verify every candidate word, spelling and contrast pair
before any recording is treated as ground truth. **This repository contains no verified Malayalam
vocabulary.** The template is empty on purpose; the software never proposes, translates or corrects
Malayalam words.

Template: `data/schema/wordlist_review_template.xlsx` (40 vocabulary + 10 unknown-word slots; regenerate
with `tools/review_sheet.py template OUT.xlsx --vocab-slots N --oov-slots M`). Needs python3 + openpyxl.
Tests: `make test-review` (runs inside `make test`).

Optional starting point (UNVERIFIED, AI-generated, deliberately not importable): `data/drafts/ai_draft_candidates.xlsx`.
It is a prompt for the proposer, never a source of ground truth.

## Roles
- **Proposer** (native speaker): fills the yellow columns: word, meaning, optional romanisation, IPA only
  if checked by someone competent, contrast pair ID and type. May also be one of the reviewers.
- **Reviewer 1 / Reviewer 2**: each judges every word alone, in their own blind copy.
- **Project owner**: runs the tools and records the final decisions after the reviewers settle things.
Use pseudonymous IDs (`rev-a`, `rev-b`) in cells and at export, never names.

## Steps
1. `review_sheet.py template master.xlsx`, then the proposer fills the Master sheet.
2. `review_sheet.py split master.xlsx --out-dir copies/` -> `review_copy_reviewer1.xlsx`,
   `review_copy_reviewer2.xlsx`. Each copy holds only the proposal plus that reviewer's own columns
   (sheet-protected against accidental edits of the proposal). Give each reviewer only their file.
3. Reviewers fill: decision (**Accept / Correct / Reject**), corrected spelling (if Correct), contrast
   genuine? (pair words only), familiar to speakers?, accepted variants, notes.
4. `review_sheet.py merge master.xlsx --r1 ... --r2 ... --out merged.xlsx`. Refused if a copy came from a
   different proposal, was swapped between reviewers, or had a proposal cell edited.
5. Read the status column. Every disagreement is **UNRESOLVED** (never forced):
   one rejects and one does not; spellings differ (including ZWJ/ZWNJ-only differences, so a canonical
   form must be chosen); familiarity differs; contrast verdicts differ. Also **Pending review**,
   **Invalid entry**, and agreed negatives (reject, not familiar, contrast not genuine).
6. Reviewers settle UNRESOLVED rows together; update their decision cells in the Master, note how in
   *Resolution notes*, and run `review_sheet.py status merged.xlsx --write`.
7. The owner fills *Final verified spelling*, *Regional variants (final)* and *Recording approved*
   (Yes/No) for each agreed row. (`merge --prefill-final` can pre-fill spellings of agreed rows; still check them.)
8. `review_sheet.py export merged.xlsx --out data/<dataset>/wordlist.csv --reviewer1-id rev-a --reviewer2-id rev-b`
   writes the `#s2w-wordlist v1` file (all rows `verified`) plus `wordlist.provenance.json` (hash of the
   Master and word list, reviewers, exclusions). **Export refuses** while any row is pending, unresolved or
   invalid, while *Recording approved* is blank, when the final spelling differs from the agreed one,
   when a pair does not have exactly two approved members, or when a final spelling contains non-Malayalam
   characters. Nothing is written on refusal.
9. Only then: consented recordings, `s2w_dataset_check --require-verified`, evaluation.

## Notes
- Malayalam is compared exactly after Unicode NFC normalisation. ZWJ/ZWNJ are never stripped silently.
- Contrast answers are only considered while a word is in a pair. Removing a word from a pair after review
  breaks the pair, which export then reports.
- Changing the proposal after splitting invalidates the copies: re-split and re-review.
- IPA and per-reviewer notes stay in the Master workbook (not part of dataset schema v1).
- The independence rule is procedural: the tool prevents mixing files, not reviewers talking to each other.
