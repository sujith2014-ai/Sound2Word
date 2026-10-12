#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "s2w_audio.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static void w16(uint8_t *p, unsigned v) { p[0] = v & 255; p[1] = (v >> 8) & 255; }
static void w32(uint8_t *p, unsigned v) { w16(p, v & 0xFFFF); w16(p + 2, v >> 16); }

/* Build a PCM16 mono WAV of a sine; returns malloc'd buffer. */
static uint8_t *make_wav(int rate, int ch, double freq, double amp, double secs, size_t *len) {
    size_t n = (size_t)(rate * secs), dl = n * ch * 2;
    uint8_t *b = calloc(44 + dl, 1);
    memcpy(b, "RIFF", 4); w32(b + 4, (unsigned)(36 + dl)); memcpy(b + 8, "WAVEfmt ", 8);
    w32(b + 16, 16); w16(b + 20, 1); w16(b + 22, ch); w32(b + 24, rate);
    w32(b + 28, rate * ch * 2); w16(b + 32, ch * 2); w16(b + 34, 16);
    memcpy(b + 36, "data", 4); w32(b + 40, (unsigned)dl);
    for (size_t i = 0; i < n; i++) {
        double v = amp * sin(2 * M_PI * freq * i / rate);
        int s = (int)lrint(v * 32767.0);
        if (s > 32767) s = 32767;
        if (s < -32768) s = -32768;
        for (int c = 0; c < ch; c++) w16(b + 44 + (i * ch + c) * 2, (unsigned)(s & 0xFFFF));
    }
    *len = 44 + dl;
    return b;
}

int main(void) {
    size_t len; s2w_audio a; s2w_stats st;

    /* Clean 1 s, 440 Hz sine at half scale. */
    uint8_t *b = make_wav(16000, 1, 440, 0.5, 1.0, &len);
    CHECK(s2w_wav_decode(b, len, &a) == S2W_OK);
    CHECK(a.sample_rate == 16000 && a.n_samples == 16000 && a.source_bits == 16);
    CHECK(s2w_audio_stats(&a, &st) == S2W_OK);
    CHECK(fabs(st.duration_s - 1.0) < 1e-9);
    CHECK(fabs(st.peak - 0.5) < 0.01);
    CHECK(fabs(st.rms - 0.5 / sqrt(2.0)) < 0.01);
    CHECK(st.clipped_samples == 0 && st.warnings == 0);
    s2w_audio_free(&a);

    /* Clipping detected. */
    uint8_t *c = make_wav(16000, 1, 440, 2.0, 1.0, &len);
    CHECK(s2w_wav_decode(c, len, &a) == S2W_OK);
    s2w_audio_stats(&a, &st);
    CHECK(st.clipped_samples > 0 && (st.warnings & S2W_WARN_CLIPPING));
    s2w_audio_free(&a); free(c);

    /* Silence flagged. */
    uint8_t *q = make_wav(16000, 1, 440, 0.0, 1.0, &len);
    CHECK(s2w_wav_decode(q, len, &a) == S2W_OK);
    s2w_audio_stats(&a, &st);
    CHECK((st.warnings & S2W_WARN_MOSTLY_SILENT) && (st.warnings & S2W_WARN_TOO_SHORT));
    s2w_audio_free(&a); free(q);

    /* Stereo downmix + 44.1k -> 16k resample keeps duration. */
    uint8_t *s = make_wav(44100, 2, 440, 0.5, 1.0, &len);
    s2w_audio r;
    CHECK(s2w_wav_decode(s, len, &a) == S2W_OK && a.source_channels == 2);
    CHECK(s2w_audio_to_16k(&a, &r) == S2W_OK);
    CHECK(r.sample_rate == 16000 && labs((long)r.n_samples - 16000) <= 1);
    s2w_audio_free(&a); s2w_audio_free(&r); free(s);

    /* Malformed inputs never crash. */
    CHECK(s2w_wav_decode(b, 5, &a) == S2W_ERR_NOT_RIFF);
    CHECK(s2w_wav_decode(b, 40, &a) != S2W_OK);
    for (size_t cut = 0; cut < 60; cut++) { /* every truncation point */
        s2w_status st2 = s2w_wav_decode(b, cut, &a);
        if (st2 == S2W_OK) s2w_audio_free(&a);
    }
    uint8_t bad[44]; memcpy(bad, b, 44); w16(bad + 34, 12); /* 12-bit */
    CHECK(s2w_wav_decode(bad, 44, &a) == S2W_ERR_UNSUPPORTED_FORMAT);
    memcpy(bad, b, 44); w16(bad + 22, 0); /* 0 channels */
    CHECK(s2w_wav_decode(bad, 44, &a) == S2W_ERR_BAD_PARAMS);
    CHECK(s2w_wav_load("/nonexistent/x.wav", &a) == S2W_ERR_IO);
    free(b);

    /* Determinism: same input, same stats. */
    b = make_wav(16000, 1, 300, 0.3, 0.5, &len);
    s2w_stats s1, s2;
    s2w_wav_decode(b, len, &a); s2w_audio_stats(&a, &s1); s2w_audio_stats(&a, &s2);
    CHECK(memcmp(&s1, &s2, sizeof s1) == 0);
    s2w_audio_free(&a); free(b);

    puts(failures ? "FAILED" : "all tests passed");
    return failures ? 1 : 0;
}
