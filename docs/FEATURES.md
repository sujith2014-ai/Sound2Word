# Phase 2: feature engine (`core/src/features.c`)

Version: format `s2w-features v1`, library `0.2.0`.

## Pipeline
frame (25 ms / 10 ms hop) -> frame-local pre-emphasis (0.97) -> periodic Hann -> zero-pad to
`fft_size` (512) -> radix-2 FFT -> power `|X|^2 / fft_size` -> 40 triangular HTK-Mel bands
(20 Hz..Nyquist) -> `ln(max(E, 1e-10))` = **log-Mel** -> orthonormal DCT-II, first 13 = **MFCC**
(c0 included).

Log-Mel is the preferred representation for future neural models; MFCC serves the classical DTW
baseline. Neither makes recognition robust to speakers, dialects or noise on its own.

## Configurable, validated limits
sample_rate 8-48 kHz; frame 10-100 ms; hop 5..frame ms; fft_size power of two 128-4096 and >= frame
length; n_mels 8-128; n_mfcc 1..min(n_mels, 40); f_min >= 0; f_max <= Nyquist; preemph 0-0.99.
Configs that give a Mel band narrower than one FFT bin are rejected at extraction. Audio must already
be at the configured rate (`S2W_ERR_RATE_MISMATCH`); the CLI resamples to 16 kHz first.

## Normalisation (Malayalam-aware)
`none`, `mean` (default, per-dimension CMN), `meanvar`. Computed per utterance from the audio only:
no labels, no cross-utterance state. Frames are never trimmed, dropped or warped, so vowel length and
gemination (closure/burst duration) keep their frame counts exactly. `mean` removes only the
long-term average (channel/gain); `meanvar` also rescales dynamics, so use it deliberately and compare
against `mean` in evaluation. Raw and normalised matrices are both stored in every result.

## Metadata
Each `s2w_features` carries format version, library version, resolved config, source sample count and
the Phase 1 `s2w_stats` (including warning bitmask).

## Inspection
`build/s2w_features file.wav [--norm ...] [--out ...]` prints `s2w-features v1` text: header
`key=value` lines, `mel_centers_hz`, then `matrix <name> <rows> <cols>` blocks. `%.6f` formatting,
byte-identical across runs on one platform (libm differences across platforms may change last digits).
`tools/plot_logmel.py` (needs matplotlib) plots it; it is not part of the C recognition code.

## Dependencies
None beyond libc/libm. The FFT is our own ~25-line iterative radix-2 implementation (same license as
the repo), so no third-party FFT license applies.

## Testing
`make test`, `make test-asan` (ASan + UBSan, leaks fatal). Synthetic tests cover silence, tones,
impulse, short audio, frame-count boundaries, invalid parameters, NaN/Inf, determinism, metadata,
normalisation properties and duration preservation. They do **not** establish speech quality:
`make test-real S2W_REAL_WAV_DIR=/path/to/wavs` smoke-tests real Malayalam recordings once available.
