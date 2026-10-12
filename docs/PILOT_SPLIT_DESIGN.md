# Pilot split design: two independent tracks

Status: **implemented and tested with synthetic fixtures only.** No real participants exist. Both tracks are evaluated from the
same pool of consented recordings, but as separate datasets with separate settings, thresholds, locked test sets and reports.
A split designed for one track does not satisfy the other; the tools enforce each track's rules separately.

## 1. Participants (default 16) and the pool
`tools/pilot_plan.py plan` allocates participant codes (`SPK-001`...) once, from a recorded seed, before any recognition result
exists: **8 enrolment (E), 4 calibration (C), 4 test (T)**. Every participant records session S1 (clean, 3 repetitions) and session
S2 (clean repeat, plus optional condition blocks). Session ids are `SPK-001-S1` / `SPK-001-S2`.
`tools/pilot_plan.py views --pool POOL --allocation allocation.csv --out-dir OUT` derives the two track datasets (`OUT/si`, `OUT/pers`).

## 2. Track 1 - speaker-independent (primary)
`dataset_meta.txt: protocol=speaker_independent`
| Role | Who | Recordings used |
|---|---|---|
| Templates (`enrol` split) | E speakers only | all their recordings of approved vocabulary words (S1 and S2) |
| Calibration queries (`calib`) | C speakers | all their recordings (vocabulary + unknown words) |
| Locked test queries (`test`) | T speakers | all their recordings |
Rules (validator, `s2w_dataset_check` / evaluator refuse violations): a speaker belongs to exactly one split; a session belongs to one
speaker and one split; unknown words never appear in `enrol` (so unknown-word recordings of E speakers are dropped from this view);
every queried vocabulary word has templates. Settings and thresholds: chosen on **calib only**; frozen with `s2w_eval freeze`;
test evaluated once (ledger refuses a second evaluation with different settings).

## 3. Track 2 - personalised (secondary)
`dataset_meta.txt: protocol=personalised`; evaluation settings must carry `protocol=personalised` (a mismatch is refused in both directions).
| Role | Who | Recordings used |
|---|---|---|
| Own templates (`enrol`) | every participant | their **S1** vocabulary recordings (clean C0); S1 unknown-word recordings are dropped |
| Calibration queries (`calib`) | the 12 E + C participants | their **S2** recordings |
| Locked test queries (`test`) | the 4 T participants | their **S2** recordings |
Rules enforced: enrolment and query recordings come from **different sessions** of the **same** participant; each query is matched only against that
participant's own templates (per-participant template pools); a participant has queries in calib **or** test, never both (calibration and
test participants disjoint); a queried word needs that participant's own enrolment recording; unknown words never enrolled.
**Operating threshold.** One global threshold is calibrated on the pooled calib-participant S2 queries (impostors = their unknown words), frozen,
then applied unchanged to test participants. No per-user threshold is derived from a test participant's data in v1 (future work, would need its own design).
Calibration-split runs build template pools only for participants who have queries in that split: **locked-test participants' audio (templates
included) is never opened**, and a test deletes it all to prove it.

## 4. Cross-track rules
- The same four T participants are locked in **both** tracks and are never used for tuning in either; E and C participants may be used for tuning in either.
- E participants are templates in Track 1 and calibration participants in Track 2: separate datasets, never merged; results are never combined or compared as one number.
- Separate settings files (`protocol=` is part of the settings hash), separate frozen records, separate ledger entries (the manifest hash differs by protocol), separate reports.
- Personalised numbers are never presented as evidence for unfamiliar speakers.
- Gaps (not built): per-user threshold adaptation, personalised use of noisy enrolment sessions, personalised cluster-aware intervals.

## 5. Leakage checks and the tests that prove them
| Forbidden situation | Where it is rejected | Test |
|---|---|---|
| Speaker in two splits (Track 1) | dataset loader | `test_dataset.c`: structure_errors a, personalised_rules s2 |
| Session shared by two speakers or two splits | dataset loader | structure_errors b; personalised p4 |
| Participant has both calib and test queries (Track 2) | dataset loader | personalised p1 |
| Query participant without own templates / word | dataset loader | p2, p3 |
| Unknown word enrolled | dataset loader | structure_errors c; p5 |
| Track-2 layout under Track-1 rules and vice versa | dataset loader | p6, s1 |
| Unknown protocol value | dataset loader | p7 |
| Settings from one track used on the other | evaluator | `test_eval.c` personalised_protocol |
| Query matched against other participants' templates (Track 2) | evaluator | label-swap test in `test_eval.c` |
| Calibration reads locked-test audio | evaluator | `test_eval.c` (blind calibration with all test-participant audio deleted) |
| Test split before settings are frozen / second test run with other settings | evaluator + ledger | `test_eval.c` test_cli |
| Withdrawn / unconsented participant in a dataset | dataset loader, ingest | `test_dataset.c` consent_status; `test_ingest.py` Consent; `test_consent_gate.py` |
