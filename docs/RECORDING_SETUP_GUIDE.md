# Recording setup guide (hardware-independent)

This guide states the *properties* a recording setup must have. It does not assume or recommend a particular phone or recorder, and **no hardware purchase is
authorised or needed**: one phone or recorder that can save lossless WAV is enough for the rehearsal and the clean pilot. Live microphone capture inside Sound2Word is out of scope;
recordings come from whatever recorder you use and enter the dataset through `s2w_ingest`.

## 1. Required properties
- **Original format:** a lossless **WAV** file saved by the recorder. `s2w_ingest` accepts WAV PCM 8/16/24/32-bit integer or 32-bit float, 1-8 channels, 1 kHz-384 kHz. Prefer **16-bit or better, 16 kHz or higher** (44.1/48 kHz is fine).
  Compressed or lossy files (MP3, AAC/M4A, OGG, AMR) are **not** accepted; do not convert them to WAV to "make them fit", because the damage is already done.
- **Channels:** mono preferred. Stereo is accepted and averaged to mono when features are computed; do not mix or pan on the device.
- **Rate and conversion policy:** record at the device's native high-quality setting and **do not resample or convert on the device or in an editor.** The pipeline resamples when needed: the evaluator uses its own fixed method, and `s2w_ingest` additionally writes a 16 kHz mono PCM16 copy (windowed-sinc) under `derived16k/` for convenience. The original stays authoritative.
- **No processing:** switch off automatic gain control, noise suppression, echo cancellation, "voice enhancement", beautify/EQ and auto-trim where the device allows it (use an unprocessed/raw audio source if one exists). If a setting cannot be turned off, write that down in the session log.
- **No editing, trimming, normalising or denoising of originals - ever.** Mistakes are fixed by re-recording before ingest, not by editing.

## 2. Quiet baseline procedure (the clean condition `C0`)
1. Choose a quiet room; close doors and windows; silence phones and appliances. If you can measure background level, note it, otherwise note "not measured".
2. Fix the recorder in a stand or on a stable surface about **20 cm** from the mouth, slightly **off-axis** (reduces plosive bursts), same position for the whole session; mark it.
3. Set a recording level that does not clip on a loud word, then **leave it unchanged**. Record the setting.
4. Make a throw-away test recording, run `build/s2w_qa test.wav`, and adjust until the verdict is `ok` (no clipping, speech clearly above the noise floor). Delete the test file; it is not part of the dataset.
5. Record each prompt with about 0.3-0.5 s of silence before and after the word.

## 3. Speaking instructions and operator conduct
Read the shown spelling naturally, in your own pronunciation, at a normal pace; one word at a time with a short pause. **Do not coach** toward a preferred pronunciation, do not say the word aloud, do not play audio examples,
and do not tell the participant which words are related. If a word is misread or interrupted, the participant simply says it again; an uncertain recording is flagged, never "improved".

## 4. Avoiding problems
Clipping (lower the level), truncated onset/offset (start recording before the prompt; keep recording after the word), accidental interruptions (cough, door, notification), device automatic edits (silence trimming, auto-stop) and
level changes between words. `s2w_qa` and `s2w_ingest` check clipping, cut onset/offset, dropouts, low level and SNR; they do not check pronunciation or whether the label is right.

## 5. What to record for every session (private session log)
Participant code, session id, date, operator code, room id and description, recorder **model class**, recording application and audio source setting, gain/level setting, whether processing was confirmed off,
distance and orientation, background level (or "not measured"), condition id, prompt file reference, vocabulary version, anomalies. Template: `data/schema/session_log.template.csv`. No names or contact details in the log.

## 6. Originals, hashes and derived audio
`s2w_ingest` stores the original **byte-for-byte** as `audio/<recording_id>.wav` (read-only, never overwritten), verifies it by SHA-256, pins the hash in `recordings.csv` (`audio_sha256`) and writes the 16 kHz copy separately to `derived16k/`. Derived audio is a convenience; manifests and evaluation use the original.
`s2w_dataset_check` re-verifies every hash and flags identical audio bytes (duplicates / leakage).

## 7. Failed QA and rejected recordings: nothing is silently deleted
A QA **reject** (no speech, heavy clipping, cut onset/offset, SNR too low) is *not* added to the dataset: `s2w_ingest` exits 3 and writes nothing. Re-record while the participant is present. Use `--keep-rejected` to keep the untouched bytes in `quarantine/` as evidence, and
note each rejected take in the session log (count and reason). A **marginal** take is ingested and its sidecar `meta/<id>.json` records why. Quarantined files are reviewed at session reconciliation and then handled under the retention rules; they never enter any manifest.

## 8. `real:` versus `sim:` conditions
`noise_condition` values beginning **`real:`** describe conditions that were actually recorded (see `data/schema/pilot_conditions.csv`). Values beginning **`sim:`** are reserved for audio degraded by software (`s2w_degrade`); the evaluator prints a "SIMULATED CONDITIONS" banner for them.
Never label simulated audio `real:`, never merge the two in one result, and never claim real acoustic conditions were tested when only simulations were run.

## 9. Noise, distance and room conditions
Not required for the clean pilot. **Do not state or promise a noise level** (for example "60 dBA") until suitable measurement equipment and a repeatable, documented procedure exist. Each optional block is marked in the condition spec with what it `requires`
(loudspeaker + SPL meter, tape measure, a documented room); activate it with `pilot_plan.py ... --with` only when the requirement is met. Unavailable conditions stay marked optional/unavailable; they are not substituted by simulations.
