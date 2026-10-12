*** SIMULATED CONDITIONS, SYNTHETIC DATA: software validation only. NOT evidence about real speakers, rooms, microphones or Malayalam recognition. ***

# Noise-matched enrolment experiment

Dataset kind: synthetic | seeds: [1, 2, 3] | calib split only | matcher unchanged | commit baseline-v0.3.0-10-g08fb058

Known-word queries per cell per seed: 72. Percentages are top-N accuracy on those queries, pooled over seeds.

## Top-1 accuracy (%)
| enrolment \ query | clean | snr20_white | snr10_white | snr0_white | snr10_pink | far_mic | telephone | hum_snr10 |
|---|---|---|---|---|---|---|---|---|
| clean | 100.0 | 77.3 | 48.6 | 23.6 | 40.3 | 59.7 | 100.0 | 100.0 |
| snr10_white | 75.5 | 94.0 | 96.3 | 86.1 | 97.7 | 92.6 | 76.4 | 75.9 |
| snr10_pink | 79.6 | 94.4 | 96.8 | 81.5 | 96.8 | 93.5 | 79.6 | 79.2 |
| far_mic | 79.6 | 88.9 | 86.1 | 79.2 | 88.9 | 94.9 | 79.6 | 79.6 |
| mix:snr10_white | 100.0 | 94.0 | 96.3 | 86.1 | 97.7 | 92.6 | 100.0 | 100.0 |

## Top-3 accuracy (%)
| enrolment \ query | clean | snr20_white | snr10_white | snr0_white | snr10_pink | far_mic | telephone | hum_snr10 |
|---|---|---|---|---|---|---|---|---|
| clean | 100.0 | 100.0 | 94.4 | 50.9 | 89.8 | 99.1 | 100.0 | 100.0 |
| snr10_white | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| snr10_pink | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| far_mic | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| mix:snr10_white | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |

## Top-5 accuracy (%)
| enrolment \ query | clean | snr20_white | snr10_white | snr0_white | snr10_pink | far_mic | telephone | hum_snr10 |
|---|---|---|---|---|---|---|---|---|
| clean | 100.0 | 100.0 | 100.0 | 84.7 | 100.0 | 100.0 | 100.0 | 100.0 |
| snr10_white | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| snr10_pink | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| far_mic | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| mix:snr10_white | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |

## Rejection separability: AUC of correct-known vs unknown best distance (1.0 = perfectly separable, 0.5 = none)
| enrolment \ query | clean | snr20_white | snr10_white | snr0_white | snr10_pink | far_mic | telephone | hum_snr10 |
|---|---|---|---|---|---|---|---|---|
| clean | 1.000 | 1.000 | 0.843 | 0.952 | 0.903 | 0.800 | 1.000 | 1.000 |
| snr10_white | 0.811 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.603 | 0.176 |
| snr10_pink | 0.796 | 1.000 | 1.000 | 0.964 | 1.000 | 1.000 | 0.609 | 0.228 |
| far_mic | 0.916 | 1.000 | 1.000 | 0.929 | 1.000 | 1.000 | 0.748 | 0.337 |
| mix:snr10_white | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.994 |

## Matched vs clean vs mismatched enrolment (top-1, per noisy query condition)

| query | clean enrol | matched | mix (clean+noisy) | mismatched-noisy mean (n) | matched - clean | matched - mismatched | recovery(matched) | recovery(mix) |
|---|---|---|---|---|---|---|---|---|
| snr20_white | 77.3 | n/a | n/a | 92.4 (3) | n/a | n/a | n/a | n/a |
| snr10_white | 48.6 | 96.3 | 96.3 | 91.4 (2) | 47.7 | 4.9 | 93% | 93% |
| snr0_white | 23.6 | n/a | n/a | 82.3 (3) | n/a | n/a | n/a | n/a |
| snr10_pink | 40.3 | 96.8 | n/a | 93.3 (2) | 56.5 | 3.5 | 95% | n/a |
| far_mic | 59.7 | 94.9 | n/a | 93.1 (2) | 35.2 | 1.9 | 87% | n/a |
| telephone | 100.0 | n/a | n/a | 78.5 (3) | n/a | n/a | n/a | n/a |
| hum_snr10 | 100.0 | n/a | n/a | 78.2 (3) | n/a | n/a | n/a | n/a |

recovery = (alternative - clean enrol) / (clean-enrol/clean-query ceiling - clean enrol); n/a when there is little loss to recover.

## Cost on clean queries (top-1 %, by enrolment condition)

| enrolment | top-1 on clean queries |
|---|---|
| clean | 100.0 |
| snr10_white | 75.5 |
| snr10_pink | 79.6 |
| far_mic | 79.6 |
| mix:snr10_white | 100.0 |

## Reading these numbers

- Matched-noise cells can look good because template and query distortions are alike; compare with the mismatched columns and with clean-query cost.
- Per-seed spread and Wilson 95% intervals (pooled counts, approximate because the same recordings recur across seeds) are in experiment.json; small differences are noise.
- Distance scales differ by enrolment condition, so rejection thresholds calibrated for one condition do not transfer. AUC is threshold-free.
- A fixture of tone sequences, simulated noise, one matcher. This cannot say whether real Malayalam speech behaves the same way.
