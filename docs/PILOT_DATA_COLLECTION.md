# Pilot data-collection plan (protocol v2)

**Status: plan and tooling only. Nothing has been recorded, no participant recruited, no consent collected, no vocabulary verified.** Official vocabulary recording
may not start until Gate A (vocabulary approval); the rehearsal needs Gate B; the main pilot needs Gate C (section 3). The only recognition evidence so far is a
*synthetic, simulated* experiment (`docs/EXPERIMENT_NOISE_ENROLMENT.md`); its idea that clean + condition-matched templates may help is a **hypothesis to test on real speech**.

Related documents: split rules `docs/PILOT_SPLIT_DESIGN.md`; recruitment and consent `docs/PILOT_RECRUITMENT_AND_CONSENT.md`; equipment-independent setup `docs/RECORDING_SETUP_GUIDE.md`;
session procedure `docs/RECORDING_SESSION_CHECKLIST.md`; condition spec `data/schema/pilot_conditions.csv`, `pilot_matrix.csv`; planner `tools/pilot_plan.py`; gates `tools/consent_gate.py`.

## 1. What the pilot is for - and not for
Answers: how the unchanged matcher performs on real, reviewed, speaker-disjoint Malayalam recordings; how unknown words are rejected; whether vowel-length and gemination contrasts survive;
and, later and only if the equipment exists, how real noise/distance/room conditions change this. It does **not** establish production accuracy, dialect coverage or robustness to arbitrary speakers.
**Sixteen speakers is a feasibility sample, not strong evidence of general Malayalam recognition accuracy.** With four held-out test speakers, hundreds of utterances do not give hundreds of independent speaker samples.
A system could work for those four voices and fail for others.

## 2. Locked decisions (project owner)
| Decision | Choice |
|---|---|
| Recognition scope | **Speaker-independent is primary.** Personalised is a **separate secondary experiment** with its own settings, thresholds, locked test and reports. |
| Recording devices | Not yet confirmed. **One primary device** for the baseline; no hardware purchase authorised. |
| Controlled noise equipment | **Not required to start.** Noise/distance/room blocks stay optional until equipment and a repeatable procedure exist. |
| Second-device experiment | **Deferred.** Excluded from this pilot (section 12). |
| Recruitment and consent | **Process to be designed and approved** (`docs/PILOT_RECRUITMENT_AND_CONSENT.md`); data-protection review pending. |

## 3. Gates (checked from a private approvals file: `consent_gate.py gate A|B|C --approvals F`)
**Gate A - vocabulary approval.** A competent Malayalam speaker has verified spelling, meaning, familiarity, natural pronunciation, variants and each proposed phonetic contrast, with independent review of the contrasts
(`docs/WORD_REVIEW.md`). A proposed minimal pair that does not differ in the intended feature is rejected or reclassified as ordinary vocabulary. The AI draft stays `UNVERIFIED` and is never imported automatically.
**Gate B - small end-to-end rehearsal** (needs a small approved subset + consent ready: materials, data-protection review, storage, retention, withdrawal procedure). Record a few words with one device; verify participant/session ids and condition labels;
run `s2w_ingest` and `s2w_dataset_check`; confirm original preservation, hashes, QA reporting, manifest integrity and duplicate detection; confirm the rehearsal cannot enter the locked test dataset; confirm reports distinguish simulated from real.
The rehearsal is a workflow check, **not evidence of accuracy**.
**Gate C - main pilot** (needs A + consent items + split-rules review + rehearsal passed). Collect clean baseline speech first. Add controlled real noise, distance, reverberation or device conditions only when the setup can be documented and repeated; keep unavailable conditions marked optional. Synthetic degradation is never substituted for real recordings.

## 4. Vocabulary
30-50 approved enrolled words (default 40) including the reviewed vowel-length and gemination pairs, plus **10 separate unknown words** never enrolled and never in a pair (acoustically near and far). Speakers are not told which words contrast; prompts show the reviewed spelling only, with no audio model.

## 5. Participants
Default 16 = 8 enrolment / 4 calibration / 4 test, allocated once from a recorded seed before any results (`pilot_plan.py plan`, which **refuses any participant who is not consent-eligible**). Codes `SPK-001`...; recruitment, consent, reserves, replacement and minimum numbers are in the recruitment document. Reviewers/proposers of the vocabulary and developers should not be test speakers; test speakers should be naive to the contrasts.

## 6. Sessions and the budget (derived, not typed)
Session S1: `C0` clean, 3 repetitions. Session S2 (at least a day later where practical): `C0-R` clean repeat, then any activated optional blocks (rotated across participants).
Repetition numbers continue across blocks within a session (the dataset key ignores the condition); ids are `SPK-012`, `SPK-012-S2`, `SPK-012-S2-N-FAN-W017-r4`. Each repetition round is a fresh random permutation; unknown words are interleaved and indistinguishable; no immediate repeats.
**The budget is computed by the planner from the condition spec, the matrix and the actual approved word list** (`pilot_plan.py budget --wordlist ... [--with ...]`):
- Default clean-only pilot (conditions `C0`, `C0-R`), with 40 vocabulary + 10 unknown words: **200 utterances per participant (3,200 for 16)**; per query split of 4 speakers: `C0` 480 known + 120 unknown, `C0-R` 160 known + 40 unknown.
  Only **2 of the 57** matrix cells are runnable (baseline and session-repeat): enough for the speaker-independent baseline and the personalised comparison, **not** enough for the noise-enrolment hypotheses.
- With every optional block activated (`--with all`; second device still disabled): 450 per participant (7,200), 56 of 57 cells runnable.
Any other word count changes these numbers; recompute with `--wordlist` rather than trusting this paragraph. Time per utterance is a planning assumption to be measured in the rehearsal.

## 7. Conditions (`data/schema/pilot_conditions.csv`)
Mandatory and enabled: `C0`, `C0-R`. Optional (activate with `--with`, each recording what it `requires`): `N-FAN`, `N-STR`, `N-BAB` (loudspeaker + SPL meter), `D-100` (tape measure), `R-HARD` (a documented room). `C0-DEVB` (second device) is **disabled**.
`real:` marks recorded conditions; `sim:` is reserved for software-degraded audio. Levels such as "60 dBA" are nominal targets only and must not be quoted or claimed without measurement; QA records the achieved SNR and level of every recording.
All approved labels remain compatible with `s2w_ingest` and `s2w_dataset_check` (tested).

## 8. Enrolment-by-query matrix and hypotheses (when optional blocks exist)
Enrolment sets from enrolment speakers only (`E0` clean; noise-only `E-*`; mixed `EM-*`; `EM-ALL`); queries from calibration/test speakers (clean, clean repeat, each noise, `D-100`, `R-HARD`), always including mismatched pairs and clean-query cost. First comparison: the unchanged matcher (MFCC + mean normalisation, default DTW, no tuning).
Pre-registered, reported whichever way they go: **H1** mixed enrolment beats clean-only on each real noise; **H2** clean-query cost within a margin delta (proposed 3 points, confirm before collection); **H3** noise-only enrolment costs clean accuracy and rejection quality; **H4** `EM-ALL` helps unseen conditions.
These cannot be tested until the optional blocks are recorded; the clean-only pilot establishes the baseline first.

## 9. Splits and leakage
Both tracks, their exact allocation, the operating-threshold rule for the personalised track, cross-track rules and the leakage checks with their tests are in `docs/PILOT_SPLIT_DESIGN.md`. Summary: speaker-disjoint templates/calibration/test for Track 1; same-participant, different-session templates and queries for Track 2 with calibration and test participants disjoint;
settings and thresholds chosen on calibration data only; **thresholds are frozen from calibration data before the locked test is opened**; one test evaluation per frozen settings (ledger); simulations never touch real test data.

## 10. Quality controls
Ingest every take immediately (`s2w_ingest`, consent register required for real datasets); QA rejects are re-recorded and kept as quarantined evidence, never silently deleted; labels checked by a second listener (100% of the first two participants, then 20% random + all marginal); exclusions fixed in advance (QA-rejected or label-failed only, never by recognition outcome);
session log per session; `s2w_dataset_check` clean before any evaluation; encrypted storage, backups, deletion on withdrawal.

## 11. What every report must show
Dataset id/version/manifest hash, **protocol track**, split sizes in speakers and recordings, settings hash and commit, calibration source. Then, **never only a pooled average**:
- results **for each test speaker**, and per-speaker spread;
- known-word accuracy (top-1/3/5 with denominators) and unknown-word **false acceptance**, plus known-word false rejection, reported **separately**;
- results by **real recording condition** (and by device when a second device exists);
- uncertainty: recording-level intervals are optimistic; **speaker-level (cluster) uncertainty is required once the tooling exists (gap G6)** and until then results are labelled as lacking it;
- minimal-pair confusion by contrast type; QA exclusions with counts;
- explicit statement of whether the system **generalises to unfamiliar voices** (Track 1) or only works after enrolment (Track 2). Never mix tracks;
- thresholds frozen on calibration data, never chosen from the locked test set. Synthetic, simulated and real results are never combined; negative and null results are reported.

## 12. Deferred: paired-device capture
Not built and not used in this pilot. Do not treat separate recordings as paired utterances. A future experiment needs a shared **utterance id** linking recordings of the same spoken event, and **before it is enabled** (the spec refuses `C0-DEVB: enabled=yes`) tests that detect:
missing pairs (an utterance recorded on one device only), duplicate device entries for one utterance, mismatched speaker or session ids within a pair, and incorrectly paired utterances (different words or repetitions paired together); plus a rule that both members of a pair stay in the same split and role.

## 13. Tooling status
| Item | Status |
|---|---|
| Two-protocol dataset rules, per-participant templates, settings/protocol guard, `views` | **implemented + tested** (synthetic) |
| Consent registers, eligibility, withdrawal report, eligible-dataset rebuild, ingest consent gate, gates A/B/C | **implemented + tested** (synthetic) |
| Matrix-derived budget, clean-only default, optional-condition activation | **implemented + tested** |
| Paired-device utterance ids (G2) | deferred |
| Label-verification workflow tool (G7) | not built (procedure in checklist) |
| Speaker-cluster intervals (G6), real-recording matrix builder (G3), QA-by-condition summary (G4), QA threshold calibration (G5) | not built |
| Live microphone capture | out of scope |

## 14. Owner actions before the first real recording
1. A Malayalam-speaking vocabulary reviewer (ideally a second, independent) verifies the proposed words and contrasts -> `wordlist.csv` (Gate A, rehearsal subset first).
2. Name the person responsible for consent and data protection and obtain their review of `docs/PILOT_RECRUITMENT_AND_CONSENT.md` and the two templates; adapt them locally (data-protection review stays **pending** until then).
3. Check which recording device you already own; one phone suffices for the rehearsal. Buy nothing yet.
4. Keep all real recordings, registers and signed forms private and out of Git.

## 15. Commands (all runnable without real data)
```
tools/pilot_plan.py check   --conditions data/schema/pilot_conditions.csv --matrix data/schema/pilot_matrix.csv
tools/pilot_plan.py budget  --conditions data/schema/pilot_conditions.csv --matrix data/schema/pilot_matrix.csv --splits 8,4,4 --wordlist wordlist.csv [--with all|ID,ID]
tools/pilot_plan.py plan    --speakers speakers.csv --conditions ... --matrix ... --splits 8,4,4 --seed <N> --out allocation.csv --consent-register C --participants-register P
tools/pilot_plan.py prompts --wordlist wordlist.csv --conditions ... --allocation allocation.csv --speaker SPK-001 --out prompts.csv --consent-register C --participants-register P
tools/pilot_plan.py views   --pool POOL --allocation allocation.csv --out-dir OUT          # OUT/si and OUT/pers
tools/consent_gate.py check|eligible|stamp-consent|withdrawals|build-eligible|gate ...
build/s2w_ingest ... --consent-register <private register> --append-manifest
build/s2w_dataset_check DATASET --require-verified
```
