# Sound2Word: project instructions

## Real-Speech Experiment: Mandatory Gates

These rules apply to the real-speech normalisation experiment defined in
`docs/EXPERIMENT_NORMALISATION_REAL_SPEECH.md`. They override any impulse to keep building while evidence is missing.

1. **No premature implementation.** Do not build tools, add features, change matching algorithms, alter normalisation
   settings, or run the real-speech experiment while waiting for Gate A vocabulary artifacts.

2. **Gate A: vocabulary.** Require the actual review sheets and evidence that two distinct reviewers independently approved
   all eight words, their spelling, meaning, familiarity, pronunciation variants, and both intended phonetic contrasts. If a
   pair is not genuine or any required approval is missing, stop.

3. **Gate B: consent and recordings.** Before accepting recordings for the experiment, check the required consent records and
   eligibility using the repository's existing `tools/consent_gate.py` workflow. Validate the actual recording metadata and
   dataset. Do not bypass consent, silently accept QA failures, or work around separation checks.

4. **Gate C: experiment integrity.** Freeze and commit the configurations, metrics, thresholds, and folds before the first
   run. Keep the fourth confirmation speaker entirely outside development and configuration selection. Never access the
   locked `--split test` data during development.

5. **Evidence-based decisions.** Report all planned configurations, including failures and regressions, with per-word and
   per-speaker results. Do not claim that normalisation improves Malayalam recognition without real-speech evidence.

6. **Stop on failure.** If any gate fails, stop at that gate, explain the exact missing or failed evidence, and do not
   continue to later steps.

7. **Artifact-first workflow.** When the user supplies artifacts, inspect the actual files and repository instructions before
   acting. Do not assume gates passed because of previous conversation messages.

8. **Minimal changes.** Do not add new tooling or expand scope unless the user explicitly requests it and the experiment
   evidence justifies it.

## Sound units: never split a conjunct

A written conjunct is ONE sound unit: ങ്ങ is never ങ + ങ, ക്കു is never ക് + കു, ന്ത is never ന് + ത. This holds in every tool's default
(`tools/s2w_units.py`, `tools/model_eval/build_unit_inventory.py`, the recorder, the alphabet). How a word is *typed* in Manglish (angane,
thenga) is a separate convention and must not change the units. Splitting is allowed only as an explicit, labelled comparison experiment
(`whole=False`, `S2W_SPLIT_ALL=1`). The tests in `tools/test_alphabet.py::NeverSplitAConjunct` must keep passing.
