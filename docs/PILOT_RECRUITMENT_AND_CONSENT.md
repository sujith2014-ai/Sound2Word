# Pilot recruitment and consent protocol (DRAFT - approvals pending)

> **Status: draft for human review.** Nothing here has been approved, no participant has been recruited and no consent has been collected.
> This document does not claim legal or ethical compliance. The **data-protection review is pending** until a designated person has
> approved the arrangements that apply to this project; record that decision in `data/schema/pilot_approvals_template.csv` (private copy).
> Participants may not be recorded until recruitment, consent and eligibility are complete (enforced in tools, section 6).

## A. Recruitment
- **Target:** 16 participants as a *feasibility pilot*, not a definitive accuracy study (planned allocation 8 / 4 / 4; see `docs/PILOT_SPLIT_DESIGN.md`).
  **Minimum to proceed to evaluation:** 12 consented participants with at least 6 enrolment, 3 calibration and 3 test. Below that, collect and validate the
  workflow only; make no split comparisons.
- **Who:** Malayalam speakers who can comfortably read, or naturally pronounce, the approved vocabulary. Screening is about comfort with the task, never about
  how closely a voice, accent or pronunciation matches an expected template. Do not exclude or discourage differing voices, accents or pronunciations.
- **Variation (voluntary only):** seek spread in voice pitch, speaking style/rate, accent, age group and gender where practical. Attributes are volunteered, may be
  "prefer not to say", and are never inferred. Pitch is *measured from audio*, not guessed.
- **Voluntariness:** participation is voluntary; declining or withdrawing has no effect on access to unrelated services. Do **not** recruit through pressure from an
  employer, teacher, family member or other authority, or through anyone who will learn who declined. Record this check (`independent_recruitment_confirmed`).
- **Compensation (if any):** modest, not coercive, decided and approved before recruitment.
- **Pseudonyms:** assign random codes `SPK-001`... from a shuffled list, **not** in order of recruitment, and never derive them from names, numbers or dates.
- **Replacing withdrawals without silently changing the split rules:**
  1. Recruit up to 2 reserve participants beyond the target, each given a pre-assigned slot (split and stratum) by the same seeded allocation, decided *before* any results exist.
  2. A withdrawal before recording frees its slot; the next reserve fills **that same slot**. A participant is never moved to a different split after any result has been seen.
  3. A withdrawal after recording removes that participant's data from all future dataset builds (`consent_gate.py build-eligible`). It is not replaced inside an already-evaluated split;
     the report states the reduced number. Any allocation change is logged as a dated amendment, never a silent edit.

## B. Consent information
Templates: `docs/templates/PILOT_PARTICIPANT_INFORMATION.md` (what participants read) and `docs/templates/PILOT_CONSENT_FORM.md` (what they sign). They must state, in plain language:
- the purpose of Sound2Word and exactly what the participant will be asked to do;
- that voice recordings and derived features are processed for speech-recognition research;
- what is collected: recordings, a pseudonymous participant code, session ids, device details, and only optional, volunteered demographic information;
- who can access the recordings, where they are stored, how long they are kept (a retention date), and that **no external sharing is planned**;
- that use in **future experiments** and any **public release** are separate, optional, explicit choices (public release also needs a separate decision and appropriate consent; default **no**);
- how to withdraw, what can be deleted, and what happens to aggregated results already produced and to anything previously shared;
- how to contact the project owner with questions or concerns;
- that taking part does not mean the system will recognise words accurately, and that results are experimental.
Each consent choice maps one-to-one to a field of the private consent register (section 6).

## C. Data minimisation and security
- Participant codes only in audio filenames, manifests and analysis; **no names, phone numbers or emails** in any dataset file. The identity/contact mapping lives in a separate restricted store; the participant register holds only an opaque `identity_record_ref`.
- Raw audio, derived audio, consent records, registers and per-query results stay **out of Git** (the repository ignores them) and in encrypted storage with named, limited access.
- **No uploading recordings to external AI or transcription services** in this pilot.
- Define before recording: retention period (`retention_until` per participant), storage location and access list, backup and deletion procedure, withdrawal handling.
- Collect sensitive demographic information only if necessary, voluntary and protected; the default is to collect none.
- Voice recordings are personal data and may be biometric; treat them accordingly and have the designated reviewer decide what local law requires.

## D. Withdrawal and deletion procedure (to be approved)
1. Participant asks (to the contact in the information sheet). The owner records `status=withdrawn`, `withdrawal_date`, and a `withdrawal_action` (`exclude_future_runs`, `delete_audio` or `delete_all`) in the consent register.
2. `consent_gate.py withdrawals --consent C --dataset D --out report.csv` lists every affected recording (original, derived, metadata paths).
3. `consent_gate.py stamp-consent` marks the participant `withdrawn` in the dataset's `participants.csv`; the dataset validator and evaluator then **refuse** that dataset until the rows are removed.
4. `consent_gate.py build-eligible` writes a new dataset without them (their audio is not reachable from it) and an exclusions report; bump the dataset version.
5. Apply the stated deletion to stored files and backups; aggregated, already-published results cannot be un-published and the information sheet says so.
6. Check the register for withdrawals **before freezing any dataset**.

## E. Decisions that must be approved before recruitment begins
- [ ] Designated data-protection / consent reviewer named: ______ (approval status: **pending**)
- [ ] Information sheet and consent form adapted to local requirements and approved
- [ ] Retention period and review date; storage location and access list; backup and deletion method
- [ ] Withdrawal and deletion procedure (section D) approved
- [ ] Whether any future-experiment use is offered; confirmation that public release is **not** offered in this pilot unless separately decided
- [ ] Compensation (if any) and non-coercive recruitment channels
- [ ] Target and minimum numbers, reserve procedure (section A), allocation seed recorded
- [ ] Who may see identities (as few people as possible) and who may see audio
Approvals are recorded in the private approvals file and checked with `consent_gate.py gate B` before the rehearsal and `gate C` before the main pilot.

## F. What the tools enforce (and what they cannot)
Implemented and tested with synthetic data: register validation (completed consent required, retention not expired, no contact data in registers, independent-recruitment and
screening recorded); `pilot_plan.py plan/prompts` refuse any participant not consent-eligible; `s2w_ingest --consent-register` refuses unconsented or withdrawn participants and is **required** for any dataset
marked `kind=real`; the dataset validator rejects real datasets without consent status and any dataset containing a withdrawn participant; `build-eligible` removes withdrawn participants.
They cannot confirm that consent was genuinely informed and voluntary, that a form was truly signed, or that the arrangements comply with any law: those remain human responsibilities.
