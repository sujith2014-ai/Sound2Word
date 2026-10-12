# Recording pipeline, QA and simulated-condition testing

Everything here is tested with **synthetic** audio only. Passing tests show the software handles those
signals correctly. They do not show that Malayalam recognition works, or is robust, for real speakers.

## 1. Capture (not built here)
This session cannot test microphone capture, so no capture code is claimed. Recommended settings for whatever
recorder is used (phone app, desk recorder): lossless WAV PCM 16-bit or better, >= 16 kHz (44.1/48 kHz is fine),
mono preferred, automatic gain control / noise suppression / echo cancellation OFF where the device allows it,
about 0.3-0.5 s of silence before and after each word, a fixed mouth-to-microphone distance written down in the
session notes, and one device per session. Never trim, normalise, denoise or re-encode the original.

## 2. Ingest (`s2w_ingest`)
Adds one recording to a dataset directory and never alters the original:
- `audio/<id>.wav` byte-for-byte copy (verified by SHA-256, mode 0444, never overwritten) - this is the manifest path;
- `derived16k/<id>.wav` mono PCM16 16 kHz copy (windowed-sinc) for QA / external tools only;
- `meta/<id>.json` format, hashes, QA numbers, labels (no local paths, no personal data);
- the `recordings.csv` row with `audio_sha256` pinned (`--append-manifest` adds it after duplicate checks).
A take whose QA verdict is **reject** is not added (exit 3); `--keep-rejected` stores the untouched bytes under
`quarantine/` only. Marginal takes are added and the sidecar records why.

## 3. Quality assessment (`s2w_qa`, `core/src/qa.c`)
Reports noise floor, speech level, SNR estimate, leading/trailing margin, clipped runs, dropouts (exact-zero runs
inside speech), DC offset. Reject: no speech, heavy clipping, onset or offset cut (< 50 ms margin), SNR below the
reject limit. Marginal: SNR below the ok limit, low level, dropout, DC, minor clipping. Clean limits: 20/10 dB;
noisy limits: 5/0 dB. **All limits are untuned engineering defaults.** The SNR is a percentile estimate and is
under-estimated when speech fills the whole file. QA says nothing about pronunciation or whether the label is right.
Exit codes: 0 ok, 1 marginal, 3 reject, 2 error. Phase 1 `s2w_inspect` and its exit codes are unchanged.

## 4. Resampling
`s2w_audio_resample` (Blackman-windowed sinc) leaves a 9 kHz tone ~60+ dB down after 48 -> 16 kHz and keeps passband
gain within ~0.05 dB; the frozen baseline's `s2w_audio_to_16k` aliases it ~20+ dB worse. The matcher and evaluator
still use the baseline resampler so results stay comparable to `baseline-v0.3.0`. Switching is a feature change and
must be evaluated on real recordings first.

## 5. Simulated conditions (`s2w_degrade`, `tools/run_robustness.py`)
`s2w_degrade --list` shows the scenarios: level changes, white/pink/hum noise at set SNR, hard clipping, telephone
band-limit, synthetic reverberation + distance, speed change, low-rate round trips, dropout, head cut, DC offset.
- Enrolment audio is copied untouched (clean templates); only the **calib** split is degraded; the **test split is
  never read**. The derived dataset id ends `.sim-<scenario>` and affected rows carry
  `noise_condition=sim:<scenario>`, so the evaluator prints "SIMULATED CONDITIONS ... NOT evidence about real
  speakers, rooms or microphones" and `result.json` records `simulated_recordings`.
- `run_robustness.py --dataset D --settings S --work-dir W` runs every scenario against the UNCHANGED matcher and
  prints a table plus `robustness_summary.json`. It tunes nothing.
- Speed changes scale tempo and pitch together; reverb is a random exponential tail; none of this is a model of real
  speakers or rooms. `s2w_make_fixture OUT wide` widens the synthetic pitch/tempo offsets of calib/test "speakers"
  to exercise mismatch handling; tone-word "speakers" are not voices.

## 6. What the simulations showed (synthetic tone-word fixture, baseline matcher, calib split)
Identity control reproduces the clean run (top-1 72/72). Pure gain changes are harmless (mean normalisation).
Clean templates against noisy queries degrade sharply (top-1 79% at 20 dB white noise, 21% at 0 dB), as do
heavy reverb + distance and large pitch/tempo mismatch. These are software observations on a toy fixture. They
suggest the pipeline would need noise-matched enrolment or noise-robust features, but only real Malayalam
recordings can say what helps - and denoising/normalisation can erase real distinctions (vowel length,
gemination), so each preprocessing step must be judged on real data with the locked-test discipline.

## 7. Still needs real data
Real capture hardware, QA threshold calibration, real noise/rooms/microphones/speakers, and every claim about
Malayalam recognition.
