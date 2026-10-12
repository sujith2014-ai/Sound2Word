# Recording session checklist (print one per participant per session)

Participant code: SPK-____  Session: S1 / S2  Date: ______  Operator code: ______  Room id: ______
Vocabulary version pinned: ______ (word list manifest hash: ______)   Allocation file: ______   Prompt file: ______
Protocol tracks this data serves: speaker-independent / personalised (the same recordings feed both; see `docs/PILOT_SPLIT_DESIGN.md`)

**Official vocabulary recording is only allowed after Gate A (vocabulary approved) and, for the main pilot, Gate C.** The rehearsal (Gate B) uses only the small approved subset.

## 1. Consent eligibility
- [ ] Participant appears in the output of `consent_gate.py eligible` (completed consent, screened, retention not expired, **not withdrawn**)
- [ ] Participant code taken from the allocation; operator did **not** invent ids or condition labels

## 2. IDs and prompts (generated, not typed)
- [ ] Session id `SPK-___-S1/S2` and every planned recording id come from `pilot_plan.py prompts` (`--consent-register` / `--participants-register` supplied)
- [ ] Approved vocabulary version pinned (the exported `wordlist.csv`; `s2w_dataset_check --require-verified` passed)
- [ ] Only the conditions marked active in the allocation are run (optional/unavailable ones stay off; second device is **not** used in this pilot)

## 3. Device and setup (see `docs/RECORDING_SETUP_GUIDE.md`)
- [ ] Device model class / app / audio source / gain written in the session log; processing (AGC, noise suppression, echo cancellation) confirmed off or noted
- [ ] Placement ~20 cm, off-axis, marked; quiet room; test recording QA `ok` and discarded
- [ ] Participant information sheet given and consent confirmed again verbally; participant knows they may stop at any time

## 4. Recording (no coaching)
- [ ] Prompts shown as reviewed spelling only; the operator never says the words, never plays examples, never corrects pronunciation
- [ ] Order and repetitions exactly as in the prompt file; breaks offered
- [ ] Each take ingested immediately with `s2w_ingest ... --consent-register <private register> --append-manifest`
- [ ] QA **reject** re-recorded now; **marginal** kept and noted; `--keep-rejected` used so rejected bytes are quarantined, not deleted
- [ ] Failed or uncertain takes (misread word, interruption, device glitch) flagged in the session log with the recording id

## 5. After the session
- [ ] Originals preserved: read-only, hashes pinned in `recordings.csv`; derived 16 kHz copies stored separately
- [ ] `s2w_dataset_check DATASET` passes (hashes, duplicates, split rules); backup made and verified
- [ ] **Reconcile:** number of planned prompts = ingested + quarantined + documented omissions; session log and manifest agree
- [ ] Label verification scheduled: a second listener checks the sample (100% for the first two participants, then 20% random plus every marginal take); failures set `label_verified=no`
- [ ] Private information excluded from every public dataset file (no names, contacts, signed forms, identity mapping); private files stored outside Git
- [ ] Session log signed and stored privately

## 6. Before freezing any dataset or running an evaluation
- [ ] Withdrawal/deletion requests checked: `consent_gate.py withdrawals --consent <register> --dataset <dir> --out <report>` shows nothing outstanding
- [ ] Dataset `participants.csv` stamped from the register (`consent_gate.py stamp-consent`); all `consented`
- [ ] Rehearsal data is **not** in the main dataset or any locked test set

## Stop rules
Stop and fix the setup if the QA reject rate is clearly above what the rehearsal showed, levels drift, the participant is uncomfortable, a device changes its processing mode, or any ID/prompt mix-up occurs. Write it in the log; do not paper over it.
