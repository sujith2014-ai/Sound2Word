# Reference-driven defect audit and before/after verification (2026-10-14)

Scope: every code-verifiable finding in the project's reference documents (`CLAUDE.md`, `VOICE_SEARCH.md`, `EVALUATION.md`, `BASELINE.md`,
`SESSION_CORRECTIONS_AND_LEARNINGS.md`, `PROJECT_STRUCTURE.md`, `WORD_REVIEW.md`, the recording / consent / pilot protocols). Code is judged by what it
does, not by whether a test or a document says it does. **All audio in this audit is made-up tones: it validates program behaviour and says nothing about
Malayalam recognition accuracy.** The default matching configuration is unchanged (plain matcher; a test asserts it).

Commit that contains the fixes and this audit: `06f6e08` (branch `main-asw1kr`).

## 1. Before-state (code before any change in this task)
| item | value |
|---|---|
| commit | `3c493bfe538649ba3ed92925576c66ff41b82654` (working tree clean) |
| `make test` (C) | exit 0 |
| `make test-review` (Python suites) | exit 0; 250 tests: 17 + 12 + 7 + 10 + 14 + 12 + 25 + 36 + 15 + 56 + 46 |
| browser checks | recorder 48/48, sidebar 35/35, alphabet 31/31, viewer 32/32 |
Measured in a clean `git worktree` at that commit, so later edits cannot contaminate it.

## 2. Requirement-to-code audit
Status: **FIXED** (code changed, regression test added, before/after shown), **NOT FIXED**, **BLOCKED** (needs a person, real speech or hardware this session does not have).

| # | reference finding | expected behaviour | where | worked before? | status |
|---|---|---|---|---|---|
| D1 | VOICE_SEARCH "Reference metadata must be trustworthy"; review: a person must not look unseen | speaker IDs `P1`, ` p1 `, `p1` are one person | `speaker_key`, `Bank.speakers`, `speaker_leaks` | **No**: `' p1 '` was not detected (0 leaks) | **FIXED** |
| D2 | VOICE_SEARCH "never silently dropped from a denominator; quote both numbers" | closed-dictionary top-1/top-5 exclude out-of-dictionary queries; counts shown beside | `evaluate` → `summary.queries`, per-method `n` | **No**: top-level `n` included them (diluted top-1 24 vs 21 queries) | **FIXED** |
| D3 | same | dictionary membership decided after Unicode NFC | `evaluate` | **No**: an NFD query word was called out-of-dictionary | **FIXED** |
| D4 | review: shortlist recall at the configured sizes, separate from ranking | recall over in-dictionary queries only, includes the configured `rescore_top`, plus where the composed method lost the word | `shortlist_recall`, `composed_stages`, row `lost_at` | **No**: recall `n` counted out-of-dictionary queries; configured size missing; no stage diagnostics | **FIXED** |
| D5 | VOICE_SEARCH "failures never skipped" | a candidate limit must be reported | `rank_composed` → `info.templates_truncated` | **No**: `max_templates` silently stopped trying candidates | **FIXED** |
| D6 | VOICE_SEARCH / EVALUATION "unusable queries count as misses" | one unreadable query is a counted miss, the run continues | `evaluate` row `error`, `summary.queries.unusable` | **No**: Python traceback, whole run aborted (exit 1) | **FIXED** |
| D7 | CLAUDE rule 5 + review: no unjustified unseen-speaker claim | with unattributed / conflicting / skipped reference clips the result is `unseen_unverified` and `unseen_speaker_claim_valid=false` | `evaluate`, `metadata_report` | **No**: still labelled `unseen_speaker` (warning on stderr only) | **FIXED** |
| D8 | review: classification must be identical in every configuration | the isolated-clips ablation sees the same reference speakers | `load_bank` records provenance of every listed clip | **No**: the ablation bank dropped speakers of cut-out clips | **FIXED** |
| D9 | review: missing / conflicting metadata | a listed clip whose file is missing or outside its folder is reported; its speaker still counts | `Bank.skipped`, `metadata_report.skipped_clips` | **No**: silently ignored | **FIXED** |
| D10 | VOICE_SEARCH eval | queries without recogniser text are counted; keys compared after NFC | `summary.missing_heard` | **No**: label/fused `n` shrank silently | **FIXED** |
| D14 | VOICE_SEARCH "failures never skipped" | recogniser text that is not Malayalam is a counted miss (`heard_error`, `summary.heard_unusable`), not an abort | `evaluate` | **No**: `ValueError`, whole run aborted | **FIXED** |
| D13 | review: duration modes, smoothing | invalid settings are refused, not ignored | `Composer`, `evaluate`, `search_with` | **No**: `smooth_ms=-5`, `rescore_top=0`, `shortlist=0` accepted | **FIXED** |
| D20 | review + CLAUDE rule 4: no near-duplicate leakage | a query that is a byte copy of a reference clip is refused whatever the metadata says | `file_sha256`, `query_audio_in_reference` | **No**: not detected | **FIXED** |
| F | CLAUDE Gate C "freeze and commit configurations, metrics, thresholds, folds"; "do not freeze an invalid setup" | a verifiable freeze of voice-search settings and every input hash; refuses invalid setups; detects later change; second configuration on the same query set is test reuse | `s2w_wordsearch.py freeze`, `eval --frozen`, ledger | no such feature | **FIXED** (tool); Gate C itself **BLOCKED** |
| G | CLAUDE rules 2/3/4/6/7 "inspect the actual files"; stop at the failing gate | one read-only report per gate from the real artifacts, stopping at the first failing gate | `tools/gate_status.py` | no such tool | **FIXED** (tool) |
| B4 | EVALUATION "Limits": validator enforces only the enrolment part of OOV words | every OOV word is queried by calib/test speakers and never enrolled; splits are speaker-separated | `gate_status.dataset_design_check` | not checked | **FIXED** at the gate layer; the C validator is unchanged (**NOT FIXED** there) |
| R | review: default stays plain | the default configuration is unchanged | `Composer` defaults, test `test_the_default_is_the_plain_matcher` | yes | verified, unchanged |
| Gate A | CLAUDE Gate A | two distinct reviewers approved all eight words | `data/private/review/master.xlsx` | all 8 words **Pending review** | **BLOCKED** (reviewers) |
| Gate B | CLAUDE Gate B | consent records, real consented recordings, QA-clean dataset | none in the repository | no real data | **BLOCKED** (consent, speakers) |
| Gate C | CLAUDE Gate C | frozen + committed settings, fourth speaker locked out | needs real data | not possible | **BLOCKED** |
| T | BASELINE.md: tag `baseline-v0.3.0` | tag exists on the remote | git proxy refused the tag push | no | **BLOCKED** (tag-push rights) |
| U | SESSION Part 5: regenerate whole-unit clips; native listening; hand-corrected boundaries; one voice for composed words | done on the owner's PC / by people | `gen_units.py`, recorder | no | **BLOCKED** |
| P | SESSION Part 5.5: word-frequency prior | optional prior for near ties | `rank_labels(prior=)` exists, never wired | n/a | **NOT FIXED**: a feature, not a defect; CLAUDE rule 8 needs evidence first |
| L | VOICE_SEARCH Limits: recorder playback of the composed word has no duration rule | listening aid only | `Recorder.unit_join` | by design | **NOT FIXED** (documented) |

## 3. Before / after evidence
### 3.1 Reproduction tests (`tools/test_wordsearch_audit.py`, class `Audit`)
Written first and run against `3c493bf`: **15 tests (13 defects, two of them with two tests), 0 pass** (`audit_before` output: e.g. `' p1 '` → 0 leaks found; closed `n` 6 ≠ 5; `unseen_speaker` ≠ `unseen_unverified`; `ValueError not raised`; missing keys). After the fixes: **15 / 15 pass**. The seven `FrozenConfiguration` tests (F1–F7) cover the new freeze feature and have no "before".

### 3.2 Identical inputs, both code trees (`tools/audit_before_after.py`)
Same reference banks, query clips, dictionary (21 made-up words) and scoring rules for both columns. Closed-dictionary composed ranking.

| case | before (3c493bf) | after |
|---|---|---|
| C1 clean closed dictionary, 21 queries (regression check) | top-1 0.952, top-5 0.952, n 21, unseen 21 | **identical**; `unseen_claim_valid=true` |
| C2 the same + 3 out-of-dictionary queries | n 24, top-1 **0.833**, 3 "not found" mixed in | n **21**, top-1 **0.952** (same 20 correct queries); `queries.out_of_dictionary=3` reported beside |
| C3 reference speaker `P1`, queries by `' p1 '` | exit 0, 6 queries called **unseen**, top-1 1.0 (an inflated unseen result) | exit **3** "not an unseen-speaker test" |
| C4 reference clips with no speaker, not marked synthetic | 21 queries called **unseen_speaker** (warning only) | 21 called **unseen_unverified**, `unseen_speaker_claim_valid=false` |
| C5 one unreadable query file among 21 | **traceback, exit 1, no result** | exit 0, top-1 0.905, 1 counted miss (`unusable=1`) |
| C6 query is a byte copy of a reference clip | exit 0, evaluated | exit **3** refused |
| processing time (mean ms per query, runs that completed) | 4.4–29 ms | 4.7–8.2 ms (same order; refused or crashed cases have no timing) |

### 3.3 Six configurations, same inputs, with recogniser text (`eval --compare-configs`, 21 closed + 3 out-of-dictionary queries)
| configuration | before top-1 / top-5 (n) | after top-1 / top-5 (n) | correct queries before → after |
|---|---|---|---|
| plain (default) | 0.833 / 0.833 (24) | 0.952 / 0.952 (21) | 20 → 20 |
| audio cut | 0.750 / 0.875 (24) | 0.857 / 1.000 (21) | 18 → 18 |
| feature squeeze | 0.833 / 0.833 (24) | 0.952 / 0.952 (21) | 20 → 20 |
| smoothing 60 ms | 0.833 / 0.833 (24) | 0.952 / 0.952 (21) | 20 → 20 |
| audio cut + smoothing | 0.833 / 0.875 (24) | 0.952 / 1.000 (21) | 20 → 20 |
| squeeze + smoothing | 0.875 / 0.875 (24) | 1.000 / 1.000 (21) | 21 → 21 |
Only the denominator changed: the number of correct queries per configuration is identical, so no configuration regressed. Shortlist recall at 10 / 50 / 100 / 200: 1.0 at all sizes here, `n` 24 → 21 (a fixture with a 21-word dictionary and exact recogniser text; **it says nothing about a real recogniser**). Regressions in the configurations themselves are as reported before: audio cut loses 2 queries in this fixture. None of this is evidence about real speech.

### 3.4 Full test suites
| suite | before (3c493bf, clean worktree) | after |
|---|---|---|
| `make test` (C) | exit 0 | exit 0 |
| `make test-review` (Python) | exit 0, 250 tests | exit 0, **290 tests** (+22 audit, +18 gate-status) |
| browser checks (recorder / sidebar / alphabet / viewer) | 48 / 35 / 31 / 32, all pass | 48 / 35 / 31 / 32, all pass |
| new failures | n/a | **none** |
Two existing tests encoded the old behaviour and were updated on purpose: the made-up unit clips are now marked `"synthetic": true` (they are tones; without the mark the new provenance rule correctly refuses an unseen claim), and speaker IDs are compared as canonical keys.

## 4. What was built (all read-only or evaluation-side; the experiment's matcher and normalisation settings are untouched)
* `tools/s2w_wordsearch.py`: items D1–D14, D20; `freeze` / `eval --frozen` / `--ledger` / `--allow-test-reuse`; exit codes 3 (leak), 4 (provenance), 5 (frozen mismatch), 6 (test reuse), 7 (invalid setup, not frozen).
* `tools/gate_status.py`: Gate A, B, C status from the real artifacts; B and C are "NOT EVALUATED" while A fails (CLAUDE rule 6). Run on the repository's real draft workbook it reports `A1 FAIL: found accepted=0, others={'Pending review': 8}`.
* `tools/audit_before_after.py`: reproduces §3.2.

## 5. Remaining blockers (genuinely need people or real speech)
1. **Gate A**: two independent reviewers must judge the eight words (`review_sheet.py split/merge/status/export`; see `WORD_REVIEW.md`). Nothing here fabricates their decisions.
2. **Gate B**: consent register, participant IDs, real consented recordings (several speakers, repeats); `gate_status.py` then checks them.
3. **Gate C**: only after A and B: `s2w_eval freeze` and `s2w_wordsearch.py freeze` on the real bank, query set and dictionary; commit the files; keep the locked speaker out of development.
4. Regenerating whole-unit clips, native listening, boundary hand-correction, tag push.

## 6. Reproduce
```
git worktree add /tmp/before 3c493bf
make all && make test && make test-review
python3 -m unittest discover -s tools -p "test_wordsearch_audit.py"      # 22 tests, all pass now
cp tools/test_wordsearch_audit.py /tmp/before/tools/ && (cd /tmp/before && python3 -m unittest discover -s tools -p test_wordsearch_audit.py)   # before: the 15 D-tests fail
python3 tools/audit_before_after.py --before-tools /tmp/before/tools
python3 tools/gate_status.py --master data/private/review/master.xlsx          # Gate A NOT PASSED, B and C NOT EVALUATED
for j in test_recorder_browser test_recorder_sidebar_browser test_alphabet_browser test_viewer_browser; do node tools/$j.js; done
```

## 7. Where every finding is recorded
| finding set | document |
|---|---|
| label audit against real words (units covered 76 -> 86 of 86, 600 -> 5,596 entries, ngga, duplicate label, never split a conjunct) | `SESSION_CORRECTIONS_AND_LEARNINGS.md` Part 6, corrections 31, `CLAUDE.md` |
| voice-search build, CMVN fix (28/63 -> 61/63), joiner, optional duration / smoothing / squeeze / rescoring (not default) | `VOICE_SEARCH.md`, corrections 32-33 |
| review findings: seen / unseen speakers, reference metadata, shortlist recall, stop adding framework and finish the gates | `SESSION_CORRECTIONS_AND_LEARNINGS.md` Part 7, corrections 34-35 |
| this audit: 13 defects, freeze / ledger, gate_status, before / after | this file, `VOICE_SEARCH.md` (Gate C and summary keys), `SESSION_CORRECTIONS_AND_LEARNINGS.md` Part 8, correction 36 |
| project layout, tool table, test counts, done / not done | `PROJECT_STRUCTURE.md` |
