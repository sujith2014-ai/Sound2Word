# Recording session checklist (print one per speaker per session)

Speaker id: ______  Session: S1 / S2  Date: ______  Operator id: ______  Room id: ______
Primary device: DEV-A  model class / app / audio source / gain: ____________________  Second device: DEV-B / none

## Before the day
- [ ] Gate 0 complete: approved `wordlist.csv`, consent materials, frozen analysis plan, dry run done
- [ ] Allocation row and prompt files for this speaker/session printed or loaded (`pilot_plan.py prompts`); prompts show reviewed spelling only
- [ ] Devices charged; storage free; processing off (automatic gain control, noise suppression, echo cancellation); lossless WAV >= 16 kHz
- [ ] Loudspeaker, SPL meter, noise files (ids recorded), measuring tape ready (S2); noise level cap 70 dBA
- [ ] `s2w_ingest` target dataset directory writable; clock/time correct

## Before the speaker arrives
- [ ] Room quiet; background level noted (dBA if measurable): ______ ; door closed; phones silenced
- [ ] Device at 20 cm, slightly off-axis (reduces plosive bursts); position marked
- [ ] Test recording of the operator: QA verdict ok (`s2w_qa file.wav`), no clipping, levels sensible; this test file is discarded

## Consent and introduction
- [ ] Participant information read; written consent signed (consent version ______); optional attributes asked, "prefer not to say" accepted
- [ ] Pseudonymous id assigned; the name/id key is stored apart from the data
- [ ] Neutral instructions given: read each word naturally, in your own pronunciation, at a normal pace; do not exaggerate; operator never says the words; tell the speaker nothing about which words are related
- [ ] Right to stop or withdraw at any time explained

## For each block (condition id: ______ )
- [ ] Condition set up exactly as in the conditions file; for noise blocks: file id ______, level at head position ______ dBA (SPL meter), loudspeaker position noted
- [ ] For D-100: distance measured 100 cm ; for R-HARD: room described ______
- [ ] Prompts run in the planned order; no back-to-back repeats; speaker pauses between words
- [ ] Each take ingested immediately (`s2w_ingest ... --append-manifest`); QA reject -> re-record now; marginal -> keep and note
- [ ] Repetition numbers follow the plan (they continue across blocks in a session)
- [ ] Break offered between blocks; speaker comfortable with the noise level (stop if not)
- [ ] Anomalies (cough, interruption, device glitch, misread word) written in the session log - audio is never edited

## After the session
- [ ] Every file ingested; originals untouched (read-only), hashes pinned in `recordings.csv`
- [ ] `s2w_dataset_check DATASET` clean (including audio hashes); backup copy made and verified
- [ ] Session log filled and signed (`data/schema/session_log.template.csv`); stored privately
- [ ] Label verification scheduled: second listener checks the planned sample (100% for the first two speakers, then 20% random + every marginal take)
- [ ] Any withdrawal request handled: delete audio, rows and derived artefacts and bump the dataset version

## Stop rules
Stop and fix the setup if: QA reject rate in a block is clearly above what the dry run showed; levels drift; the speaker is uncomfortable; the device changes processing mode; or any ID/prompt mix-up occurs. Note it in the log; do not paper over it.
