# Experiment: does noise-matched enrolment help the existing matcher?

> **SIMULATED CONDITIONS, SYNTHETIC DATA.** Tone-sequence "words", software-generated noise, one matcher.
> This is software/engineering evidence only. It is **not** evidence about real speakers, rooms, microphones or
> Malayalam recognition, and no accuracy claim should be drawn from it.

## Question
With the matcher, features, vocabulary and split policy fixed, do templates enrolled under noisy conditions recover the
accuracy that clean templates lose on noisy queries? The aim is to separate a *template/enrolment mismatch* problem
(fixable by what we enrol) from a *feature* problem (the representation cannot carry the information under noise).

## Protocol (fixed in advance)
- Matcher unchanged (`baseline-v0.3.0` code, MFCC + mean normalisation, default DTW settings, no tuning).
- Synthetic fixture dataset (`s2w_make_fixture`); **calib split only; the test split is never read.**
- Full **enrolment x query matrix**, including mismatched pairs, so matched noise cannot look good merely because the
  distortions are alike. Enrolment: `clean`, `snr10_white`, `snr10_pink`, `far_mic` (every template degraded) and
  `mix:snr10_white` (clean templates kept + degraded copies). Queries: clean, 20/10/0 dB white, 10 dB pink, far_mic,
  telephone band-limit, 10 dB hum. 3 noise seeds, 72 known + 27 unknown queries per cell per seed.
- Reported: top-1/3/5, rejection separability (threshold-free AUC of correct-known vs unknown best distance), clean-query
  cost of each enrolment, per-seed spread, Wilson 95% intervals. Nothing was tuned between runs.
- Reproduce: `make all && tools/run_enrolment_experiment.py --dataset <fixture> --settings <base.cfg> --work-dir W --seeds 1,2,3`
  (`<fixture>` from `build/s2w_make_fixture OUT`, base settings `#s2w-settings v1` + `norm=mean`).
  Full output: `docs/experiments/noise_enrolment_synthetic/{report.md,experiment.json}`.

## Results (top-1 %, pooled over 3 seeds)
| enrolment \ query | clean | 20 dB white | 10 dB white | 0 dB white | 10 dB pink | far_mic | telephone | 10 dB hum |
|---|---|---|---|---|---|---|---|---|
| clean | 100.0 | 77.3 | 48.6 | 23.6 | 40.3 | 59.7 | 100.0 | 100.0 |
| snr10_white | 75.5 | 94.0 | 96.3 | 86.1 | 97.7 | 92.6 | 76.4 | 75.9 |
| snr10_pink | 79.6 | 94.4 | 96.8 | 81.5 | 96.8 | 93.5 | 79.6 | 79.2 |
| far_mic | 79.6 | 88.9 | 86.1 | 79.2 | 88.9 | 94.9 | 79.6 | 79.6 |
| mix:snr10_white | 100.0 | 94.0 | 96.3 | 86.1 | 97.7 | 92.6 | 100.0 | 100.0 |

Top-3 is 100% for every noisy-enrolment row and top-5 is 100% almost everywhere; clean templates against noisy
queries reach top-3 of 94% (10 dB white) and 51% (0 dB). Uncertainty: each cell has 216 pooled known-word
queries; 95% intervals are roughly +-5 points (e.g. clean|10 dB white 48.6% [42, 55]; 10 dB-white matched 96.3% [93, 98]).

Rejection separability (AUC; 1.0 perfect, 0.5 none, below 0.5 unknown words look closer than known): clean templates
keep 0.80-1.00; noisy-only templates reach 0.93-1.00 on white/pink-noise queries but only **0.60-0.92 on clean and
telephone queries and 0.18-0.34 on hum queries**; the mixed enrolment stays 0.99-1.00 everywhere.

## What this does and does not show (synthetic only)
1. **Clean templates fail systematically under noise.** Top-1 on 10 dB white noise is 48.6% in every seed even though
   the noise audio differs per seed (all 99 query distances differ, no top-1 label does). It is a condition effect,
   not noise-draw luck. The right word is usually still in the shortlist (top-3 94%), so ranking is degraded, not destroyed.
2. **Most of the loss is recoverable by templates alone.** Matched noisy enrolment recovers about 87-95% of the gap to the
   clean ceiling, and the benefit largely **survives mismatched noise types**: noisy templates of a different type
   score 91-93% on 10 dB queries against 40-60% for clean templates, so matched beats mismatched by only ~2-5 points.
   On this fixture the dominant failure looks like a template/enrolment mismatch problem more than an inability of MFCCs
   to carry the information.
3. **Noise-only enrolment is not free.** Replacing clean templates with noisy ones drops clean-query top-1 to 75-80% and
   telephone/hum queries to ~76-80%, and damages rejection separability on those conditions. This is the larger risk.
4. **Multi-condition enrolment (clean + noisy copies) was the best design tested here:** no cost on clean, telephone
   or hum queries, 94-98% at 20/10 dB white and 10 dB pink noise (86% at 0 dB, 93% far_mic), and rejection
   separability stayed ~1.0. It doubles the templates (and DTW compute).
5. Residual gaps remain: at 0 dB white noise even noisy templates reach only 79-86% (vs 100% clean/clean), and a far-field
   condition is only 93% with templates from other conditions, so representation limits probably still exist.

## Why not to over-read this
- The fixture is three clean tone segments per "word", with very high spectral contrast. Real Malayalam speech has weaker,
  more confusable structure; noise masks consonant, length and geminate cues that tones do not have.
- Noise was synthetic, enrolled and queried with the same generators; real noise/rooms/microphones will differ. The
  mismatched columns reduce, but do not remove, that concern.
- Four speakers enrolled, three in calib: no speaker-variation conclusions.
- Noise-matched templates need real noisy enrolment recordings (or realistic simulation); simulated enrolment noise
  may not transfer to real rooms. This must be re-tested on real recordings, which is the actual open question.
- Rejection numbers are threshold-free separability, not calibrated false-accept / false-reject rates.

## Implications for the plan (hypotheses to test on real data, not conclusions)
- Prefer **multi-condition enrolment** over noise-only enrolment, and measure clean-query cost explicitly.
- Do not add noise-robust features yet; first see how much condition-diverse enrolment recovers on real speech.
- When real data exists, run this same matrix on the calibration split with real noisy recordings, keep the locked test set unread
  until settings are frozen, and treat any improvement as provisional until it holds on the untouched test split.
- Stop expanding synthetic experiments unless they answer a specific engineering question.
