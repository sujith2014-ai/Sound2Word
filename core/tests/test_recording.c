#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_qa.h"
#include "s2w_resample.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static uint32_t rng = 1;
static double urand(void) { rng = rng * 1664525u + 1013904223u; return (rng >> 8) / 16777216.0; }
static double gauss(void) { double s = 0; for (int i = 0; i < 12; i++) s += urand(); return s - 6.0; }

static s2w_audio mk(size_t n, int rate) {
    s2w_audio a = {0};
    a.samples = calloc(n ? n : 1, sizeof(float)); a.n_samples = n; a.sample_rate = rate; a.source_channels = 1; a.source_bits = 16;
    return a;
}
static s2w_audio tone(double hz, double amp, double secs, int rate) {
    s2w_audio a = mk((size_t)(secs * rate), rate);
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] = (float)(amp * sin(2 * M_PI * hz * (double)i / rate));
    return a;
}
/* amplitude of component at hz over the middle half of the signal (least-squares fit) */
static double amp_at(const s2w_audio *a, double hz) {
    size_t lo = a->n_samples / 4, hi = a->n_samples * 3 / 4;
    double c = 0, s = 0;
    for (size_t i = lo; i < hi; i++) {
        double ph = 2 * M_PI * hz * (double)i / a->sample_rate;
        c += a->samples[i] * cos(ph); s += a->samples[i] * sin(ph);
    }
    return 2.0 * sqrt(c * c + s * s) / (double)(hi - lo);
}
static double db(double r) { return 20 * log10(r); }

static void test_resample(void) {
    s2w_audio in, out, legacy;
    in = tone(1000, 0.5, 1.0, 48000);
    CHECK(s2w_audio_resample(&in, 16000, &out) == S2W_OK && out.sample_rate == 16000 && out.n_samples == 16000);
    CHECK(fabs(amp_at(&out, 1000) - 0.5) < 0.003);                              /* passband gain within ~0.05 dB */
    s2w_audio_free(&out); s2w_audio_free(&in);

    in = tone(7000, 0.5, 1.0, 48000);                                           /* below new Nyquist, inside the guard band */
    CHECK(s2w_audio_resample(&in, 16000, &out) == S2W_OK && fabs(amp_at(&out, 7000) - 0.5) < 0.05);
    s2w_audio_free(&out); s2w_audio_free(&in);

    /* 9 kHz is above the 16 kHz Nyquist: it must be removed, not folded to 7 kHz. Compare with the legacy box filter. */
    in = tone(9000, 0.5, 1.0, 48000);
    CHECK(s2w_audio_resample(&in, 16000, &out) == S2W_OK);
    CHECK(s2w_audio_to_16k(&in, &legacy) == S2W_OK);
    double alias_new = amp_at(&out, 7000), alias_old = amp_at(&legacy, 7000);
    CHECK(db(alias_new / 0.5) < -60.0);
    CHECK(db(alias_old / 0.5) > db(alias_new / 0.5) + 20.0);                    /* sinc is far cleaner than the baseline box filter */
    s2w_audio_free(&out); s2w_audio_free(&legacy); s2w_audio_free(&in);

    /* upsampling: no imaging */
    in = tone(1000, 0.5, 1.0, 8000);
    CHECK(s2w_audio_resample(&in, 16000, &out) == S2W_OK && out.n_samples == 16000);
    CHECK(fabs(amp_at(&out, 1000) - 0.5) < 0.005 && db(amp_at(&out, 7000) / 0.5) < -60.0);
    s2w_audio_free(&out); s2w_audio_free(&in);

    /* DC gain is exactly 1 away from the edges, odd ratio */
    in = mk(44100, 44100);
    for (size_t i = 0; i < in.n_samples; i++) in.samples[i] = 0.3f;
    CHECK(s2w_audio_resample(&in, 16000, &out) == S2W_OK && out.n_samples == 16000);
    CHECK(fabs(out.samples[8000] - 0.3) < 1e-5 && fabs(out.samples[100] - 0.3) < 1e-4);
    s2w_audio_free(&out); s2w_audio_free(&in);

    /* round trip 16k -> 48k -> 16k */
    in = tone(1500, 0.4, 1.0, 16000);
    s2w_audio up;
    CHECK(s2w_audio_resample(&in, 48000, &up) == S2W_OK && s2w_audio_resample(&up, 16000, &out) == S2W_OK && out.n_samples == in.n_samples);
    double e = 0;
    for (size_t i = 2000; i < 14000; i++) { double d = out.samples[i] - in.samples[i]; e += d * d; }
    CHECK(sqrt(e / 12000.0) < 1e-3);
    s2w_audio_free(&up); s2w_audio_free(&out);

    /* identity, invalid input, tiny inputs */
    CHECK(s2w_audio_resample(&in, 16000, &out) == S2W_OK && memcmp(out.samples, in.samples, in.n_samples * sizeof(float)) == 0);
    s2w_audio_free(&out);
    CHECK(s2w_audio_resample(NULL, 16000, &out) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_audio_resample(&in, 999, &out) == S2W_ERR_BAD_PARAMS && s2w_audio_resample(&in, 400000, &out) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_audio_resample(&in, 16000, NULL) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&in);
    in = mk(1, 16000);
    CHECK(s2w_audio_resample(&in, 8000, &out) == S2W_ERR_EMPTY);
    CHECK(s2w_audio_resample(&in, 48000, &out) == S2W_OK && out.n_samples == 3 && isfinite(out.samples[0]));
    s2w_audio_free(&out); s2w_audio_free(&in);
}

static void test_wav_write(void) {
    float x[5] = {0.0f, 0.5f, -0.5f, 1.5f, NAN};
    uint8_t *b; size_t l;
    CHECK(s2w_wav_encode_pcm16(x, 5, 16000, &b, &l) == S2W_OK && l == 44 + 10);
    s2w_audio a;
    CHECK(s2w_wav_decode(b, l, &a) == S2W_OK && a.sample_rate == 16000 && a.n_samples == 5 && a.source_bits == 16);
    CHECK(fabs(a.samples[1] - 0.5) < 1.0 / 32768 && fabs(a.samples[2] + 0.5) < 1.0 / 32768);
    CHECK(fabs(a.samples[3] - 32767.0 / 32768.0) < 1e-6);                      /* clamped, not wrapped */
    CHECK(a.samples[4] == 0.0f);                                               /* NaN -> 0, never garbage */
    s2w_audio_free(&a); free(b);
    CHECK(s2w_wav_encode_pcm16(x, 0, 16000, &b, &l) == S2W_ERR_BAD_PARAMS && s2w_wav_encode_pcm16(NULL, 5, 16000, &b, &l) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_wav_write_pcm16("/nonexistent_dir/x.wav", x, 5, 16000) == S2W_ERR_IO);
}

/* three tone bursts (500/1000/1500 Hz, 0.15 s) between `lead` and `trail` seconds of noise */
static s2w_audio speechlike(int rate, double lead, double trail, double amp, double sigma) {
    size_t nl = (size_t)(lead * rate), nt = (size_t)(trail * rate), nb = (size_t)(0.15 * rate);
    s2w_audio a = mk(nl + 3 * nb + nt, rate);
    static const double F[3] = {500, 1000, 1500};
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] = (float)(sigma * gauss());
    for (int b = 0; b < 3; b++)
        for (size_t i = 0; i < nb; i++) {
            double env = i < 160 ? (double)i / 160 : (nb - i < 160 ? (double)(nb - i) / 160 : 1.0);
            a.samples[nl + b * nb + i] += (float)(env * amp * sin(2 * M_PI * F[b] * (double)i / rate));
        }
    return a;
}
static double sigma_for_snr(double amp, double snr_db) { return sqrt(amp * amp / 2.0 / pow(10, snr_db / 10)); }

static void test_qa(void) {
    s2w_qa_limits clean, noisy;
    s2w_qa_limits_clean(&clean); s2w_qa_limits_noisy(&noisy);
    CHECK(noisy.snr_ok_db < clean.snr_ok_db && noisy.snr_reject_db < clean.snr_reject_db && clean.min_edge_s == noisy.min_edge_s);
    s2w_qa q; char names[200];

    s2w_audio a = speechlike(16000, 0.3, 0.3, 0.4, 1e-4);
    float *copy = malloc(a.n_samples * sizeof(float)); memcpy(copy, a.samples, a.n_samples * sizeof(float));
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK);
    CHECK(q.verdict == S2W_QA_OK && q.flags == 0 && q.snr_db > 40 && q.noise_floor_dbfs < -70);
    CHECK(fabs(q.leading_s - 0.3) < 0.05 && fabs(q.trailing_s - 0.3) < 0.05 && fabs(q.speech_s - 0.45) < 0.06);
    CHECK(memcmp(copy, a.samples, a.n_samples * sizeof(float)) == 0);           /* analysis never modifies audio */
    s2w_qa_flag_names(q.flags, names, sizeof names); CHECK(!strcmp(names, "none"));
    s2w_audio_free(&a); free(copy);

    /* SNR estimate tracks the injected SNR */
    double want[3] = {30, 20, 10};
    for (int i = 0; i < 3; i++) {
        a = speechlike(16000, 0.3, 0.3, 0.4, sigma_for_snr(0.4, want[i]));
        CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && fabs(q.snr_db - want[i]) < 3.0);
        s2w_audio_free(&a);
    }
    a = speechlike(16000, 0.3, 0.3, 0.4, sigma_for_snr(0.4, 15));
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && q.verdict == S2W_QA_MARGINAL && (q.flags & S2W_QA_LOW_SNR));
    s2w_qa_flag_names(q.flags, names, sizeof names); CHECK(!strcmp(names, "low_snr"));
    CHECK(s2w_qa_analyse(&a, &noisy, &q) == S2W_OK && q.verdict == S2W_QA_OK);           /* same audio is fine for a noisy condition */
    s2w_audio_free(&a);
    a = speechlike(16000, 0.3, 0.3, 0.4, sigma_for_snr(0.4, 5));
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && q.verdict == S2W_QA_REJECT);
    s2w_audio_free(&a);

    /* truncated onset / offset */
    a = speechlike(16000, 0.005, 0.3, 0.4, 1e-4);
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_ONSET_CUT) && q.verdict == S2W_QA_REJECT);
    s2w_audio_free(&a);
    a = speechlike(16000, 0.3, 0.005, 0.4, 1e-4);
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_OFFSET_CUT) && q.verdict == S2W_QA_REJECT);
    s2w_audio_free(&a);

    /* silence and noise-only: no speech */
    a = mk(16000, 16000);
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_NO_SPEECH) && q.verdict == S2W_QA_REJECT);
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] = (float)(0.01 * gauss());
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_NO_SPEECH) && q.verdict == S2W_QA_REJECT);
    s2w_audio_free(&a);

    /* clipping: heavy = reject, a few samples = marginal */
    a = speechlike(16000, 0.3, 0.3, 3.0, 1e-4);
    for (size_t i = 0; i < a.n_samples; i++) { if (a.samples[i] > 1) a.samples[i] = 1; if (a.samples[i] < -1) a.samples[i] = -1; }
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_HARD_CLIP) && q.verdict == S2W_QA_REJECT && q.max_clip_run >= 3);
    s2w_audio_free(&a);
    a = speechlike(16000, 0.3, 0.3, 0.4, 1e-4);
    for (int i = 0; i < 4; i++) a.samples[6000 + i] = 1.0f;
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_CLIP_RUNS) && !(q.flags & S2W_QA_HARD_CLIP) && q.verdict == S2W_QA_MARGINAL);
    s2w_audio_free(&a);

    /* dropout inside the speech span is flagged; exact zeros in the leading silence are not */
    a = speechlike(16000, 0.3, 0.3, 0.4, 1e-4);
    for (int i = 0; i < 128; i++) a.samples[5500 + i] = 0.0f;                   /* 8 ms inside burst 1/2 */
    for (int i = 0; i < 800; i++) a.samples[500 + i] = 0.0f;                    /* 50 ms in leading noise */
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_DROPOUT) && q.n_dropouts == 1 && q.longest_dropout_ms > 7.0 && q.verdict == S2W_QA_MARGINAL);
    s2w_audio_free(&a);
    a = speechlike(16000, 0.3, 0.3, 0.4, 1e-4);
    for (int i = 0; i < 800; i++) a.samples[500 + i] = 0.0f;
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && !(q.flags & S2W_QA_DROPOUT));
    s2w_audio_free(&a);

    /* DC offset is flagged but does not wreck the SNR estimate */
    a = speechlike(16000, 0.3, 0.3, 0.4, 1e-4);
    for (size_t i = 0; i < a.n_samples; i++) a.samples[i] += 0.05f;
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_DC_OFFSET) && q.snr_db > 30 && q.verdict == S2W_QA_MARGINAL);
    s2w_audio_free(&a);

    /* very quiet but clean */
    a = speechlike(16000, 0.3, 0.3, 0.002, 1e-6);
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && (q.flags & S2W_QA_LOW_LEVEL) && q.verdict == S2W_QA_MARGINAL);
    s2w_audio_free(&a);

    /* other sample rates scale frame sizes correctly */
    a = speechlike(48000, 0.3, 0.3, 0.4, 1e-4);
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_OK && q.verdict == S2W_QA_OK && fabs(q.leading_s - 0.3) < 0.05);
    s2w_audio_free(&a);

    /* errors */
    a = mk(1500, 16000);                                  /* 4 frames of 20 ms */
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_ERR_TOO_SHORT);
    s2w_audio_free(&a);
    a = speechlike(16000, 0.3, 0.3, 0.4, 1e-4);
    a.samples[100] = NAN;
    CHECK(s2w_qa_analyse(&a, &clean, &q) == S2W_ERR_NONFINITE);
    s2w_audio_free(&a);
    CHECK(s2w_qa_analyse(NULL, &clean, &q) == S2W_ERR_BAD_PARAMS);
    a = mk(16000, 16000);
    CHECK(s2w_qa_analyse(&a, NULL, &q) == S2W_ERR_BAD_PARAMS && s2w_qa_analyse(&a, &clean, NULL) == S2W_ERR_BAD_PARAMS);
    s2w_audio_free(&a);
    s2w_qa_flag_names(S2W_QA_HARD_CLIP | S2W_QA_DROPOUT, names, sizeof names); CHECK(!strcmp(names, "hard_clip,dropout"));
    CHECK(!strcmp(s2w_qa_verdict_name(S2W_QA_REJECT), "reject"));
}

int main(void) {
    test_resample(); test_wav_write(); test_qa();
    puts(failures ? "FAILED" : "recording: all tests passed");
    return failures ? 1 : 0;
}
