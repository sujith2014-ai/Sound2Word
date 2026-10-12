#include "s2w_features.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define MAX_SECONDS 600
#define STD_FLOOR 1e-5

void s2w_feat_config_default(s2w_feat_config *c) {
    memset(c, 0, sizeof *c);
    c->sample_rate = S2W_TARGET_RATE;
    c->frame_ms = 25;
    c->hop_ms = 10;
    c->fft_size = 512;
    c->n_mels = 40;
    c->n_mfcc = 13;
    c->f_min_hz = 20.0;
    c->f_max_hz = 0.0;
    c->preemph = 0.97;
    c->norm = S2W_NORM_MEAN;
}

static size_t frame_len(const s2w_feat_config *c) {
    return (size_t)(((long long)c->sample_rate * c->frame_ms + 500) / 1000);
}
static size_t hop_len(const s2w_feat_config *c) {
    return (size_t)(((long long)c->sample_rate * c->hop_ms + 500) / 1000);
}

s2w_status s2w_feat_config_validate(const s2w_feat_config *c) {
    if (!c) return S2W_ERR_BAD_PARAMS;
    if (c->sample_rate < 8000 || c->sample_rate > 48000) return S2W_ERR_BAD_PARAMS;
    if (c->frame_ms < 10 || c->frame_ms > 100) return S2W_ERR_BAD_PARAMS;
    if (c->hop_ms < 5 || c->hop_ms > c->frame_ms) return S2W_ERR_BAD_PARAMS;
    if (c->fft_size < 128 || c->fft_size > 4096 || (c->fft_size & (c->fft_size - 1)))
        return S2W_ERR_BAD_PARAMS;
    if ((size_t)c->fft_size < frame_len(c)) return S2W_ERR_BAD_PARAMS;
    if (c->n_mels < 8 || c->n_mels > 128) return S2W_ERR_BAD_PARAMS;
    int mfcc_max = c->n_mels < 40 ? c->n_mels : 40;
    if (c->n_mfcc < 1 || c->n_mfcc > mfcc_max) return S2W_ERR_BAD_PARAMS;
    double nyq = c->sample_rate / 2.0;
    double fmax = c->f_max_hz == 0.0 ? nyq : c->f_max_hz;
    if (!isfinite(c->f_min_hz) || !isfinite(fmax) || c->f_min_hz < 0 ||
        fmax <= c->f_min_hz || fmax > nyq)
        return S2W_ERR_BAD_PARAMS;
    if (!isfinite(c->preemph) || c->preemph < 0.0 || c->preemph > 0.99) return S2W_ERR_BAD_PARAMS;
    if (c->norm != S2W_NORM_NONE && c->norm != S2W_NORM_MEAN && c->norm != S2W_NORM_MEANVAR)
        return S2W_ERR_BAD_PARAMS;
    return S2W_OK;
}

size_t s2w_feat_num_frames(const s2w_feat_config *c, size_t n) {
    size_t fl = frame_len(c), hl = hop_len(c);
    if (hl == 0 || n < fl) return 0;
    return 1 + (n - fl) / hl;
}

static double hz_to_mel(double f) { return 2595.0 * log10(1.0 + f / 700.0); }
static double mel_to_hz(double m) { return 700.0 * (pow(10.0, m / 2595.0) - 1.0); }

double s2w_feat_mel_center_hz(const s2w_feat_config *c, int band) {
    double fmax = c->f_max_hz == 0.0 ? c->sample_rate / 2.0 : c->f_max_hz;
    double lo = hz_to_mel(c->f_min_hz), hi = hz_to_mel(fmax);
    return mel_to_hz(lo + (hi - lo) * (band + 1) / (c->n_mels + 1));
}

/* In-place iterative radix-2 FFT; tw holds cos/sin tables of size n/2. */
static void fft(double *re, double *im, int n, const double *twc, const double *tws) {
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            double t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }
    for (int len = 2; len <= n; len <<= 1) {
        int half = len >> 1, step = n / len;
        for (int i = 0; i < n; i += len)
            for (int k = 0; k < half; k++) {
                double wr = twc[k * step], wi = -tws[k * step];
                int a = i + k, b = a + half;
                double xr = re[b] * wr - im[b] * wi, xi = re[b] * wi + im[b] * wr;
                re[b] = re[a] - xr; im[b] = im[a] - xi;
                re[a] += xr; im[a] += xi;
            }
    }
}

static void normalise(const float *src, float *dst, size_t rows, size_t cols, s2w_norm_mode m) {
    memcpy(dst, src, rows * cols * sizeof *src);
    if (m == S2W_NORM_NONE) return;
    for (size_t j = 0; j < cols; j++) {
        double mean = 0;
        for (size_t i = 0; i < rows; i++) mean += src[i * cols + j];
        mean /= (double)rows;
        double sd = 1.0;
        if (m == S2W_NORM_MEANVAR) {
            double v = 0;
            for (size_t i = 0; i < rows; i++) {
                double d = src[i * cols + j] - mean;
                v += d * d;
            }
            sd = sqrt(v / (double)rows);
            if (sd < STD_FLOOR) sd = 1.0; /* constant dimension: leave scale alone */
        }
        for (size_t i = 0; i < rows; i++) dst[i * cols + j] = (float)((src[i * cols + j] - mean) / sd);
    }
}

void s2w_features_free(s2w_features *f) {
    if (!f) return;
    free(f->logmel); free(f->logmel_norm); free(f->mfcc); free(f->mfcc_norm);
    memset(f, 0, sizeof *f);
}

s2w_status s2w_features_extract(const s2w_audio *a, const s2w_feat_config *cfg, s2w_features *out) {
    if (!out) return S2W_ERR_BAD_PARAMS;
    memset(out, 0, sizeof *out);
    if (!a || !a->samples || !cfg) return S2W_ERR_BAD_PARAMS;
    s2w_status st = s2w_feat_config_validate(cfg);
    if (st != S2W_OK) return st;
    if (a->sample_rate != cfg->sample_rate) return S2W_ERR_RATE_MISMATCH;
    if (a->n_samples > (size_t)MAX_SECONDS * (size_t)cfg->sample_rate) return S2W_ERR_TOO_LARGE;
    size_t nf = s2w_feat_num_frames(cfg, a->n_samples);
    if (nf == 0) return S2W_ERR_TOO_SHORT;
    for (size_t i = 0; i < a->n_samples; i++)
        if (!isfinite(a->samples[i])) return S2W_ERR_NONFINITE;

    s2w_feat_config c = *cfg;
    if (c.f_max_hz == 0.0) c.f_max_hz = c.sample_rate / 2.0;
    st = s2w_audio_stats(a, &out->quality);
    if (st != S2W_OK) return st;

    size_t fl = frame_len(&c), hl = hop_len(&c);
    int nfft = c.fft_size, nb = nfft / 2 + 1, nm = c.n_mels, nc = c.n_mfcc;

    double *win = malloc(fl * sizeof *win);
    double *twc = malloc((size_t)nfft / 2 * sizeof *twc), *tws = malloc((size_t)nfft / 2 * sizeof *tws);
    double *re = malloc((size_t)nfft * sizeof *re), *im = malloc((size_t)nfft * sizeof *im);
    double *fb = calloc((size_t)nm * nb, sizeof *fb);
    double *dct = malloc((size_t)nc * nm * sizeof *dct);
    double *mel = malloc((size_t)nm * sizeof *mel);
    out->logmel = malloc(nf * nm * sizeof(float));
    out->mfcc = malloc(nf * nc * sizeof(float));
    out->logmel_norm = malloc(nf * nm * sizeof(float));
    out->mfcc_norm = malloc(nf * nc * sizeof(float));
    st = S2W_ERR_NOMEM;
    if (!win || !twc || !tws || !re || !im || !fb || !dct || !mel || !out->logmel || !out->mfcc ||
        !out->logmel_norm || !out->mfcc_norm)
        goto done;

    for (size_t i = 0; i < fl; i++) win[i] = 0.5 - 0.5 * cos(2.0 * M_PI * (double)i / (double)fl);
    for (int i = 0; i < nfft / 2; i++) {
        twc[i] = cos(2.0 * M_PI * i / nfft);
        tws[i] = sin(2.0 * M_PI * i / nfft);
    }
    /* Triangular Mel filters on FFT bin frequencies. */
    {
        double lo = hz_to_mel(c.f_min_hz), hi = hz_to_mel(c.f_max_hz);
        for (int m = 0; m < nm; m++) {
            double fl_hz = mel_to_hz(lo + (hi - lo) * m / (nm + 1));
            double fc_hz = mel_to_hz(lo + (hi - lo) * (m + 1) / (nm + 1));
            double fr_hz = mel_to_hz(lo + (hi - lo) * (m + 2) / (nm + 1));
            double sum = 0;
            for (int k = 0; k < nb; k++) {
                double f = (double)k * c.sample_rate / nfft, w = 0;
                if (f > fl_hz && f <= fc_hz) w = (f - fl_hz) / (fc_hz - fl_hz);
                else if (f > fc_hz && f < fr_hz) w = (fr_hz - f) / (fr_hz - fc_hz);
                fb[(size_t)m * nb + k] = w;
                sum += w;
            }
            if (sum <= 0.0) { st = S2W_ERR_BAD_PARAMS; goto done; } /* band narrower than a bin */
        }
    }
    for (int k = 0; k < nc; k++)
        for (int m = 0; m < nm; m++)
            dct[(size_t)k * nm + m] = sqrt((k == 0 ? 1.0 : 2.0) / nm) * cos(M_PI * k * (m + 0.5) / nm);

    for (size_t f = 0; f < nf; f++) {
        const float *x = a->samples + f * hl;
        memset(re, 0, (size_t)nfft * sizeof *re);
        memset(im, 0, (size_t)nfft * sizeof *im);
        for (size_t i = 0; i < fl; i++) {
            double prev = i ? x[i - 1] : x[0];
            re[i] = (x[i] - c.preemph * prev) * win[i];
        }
        fft(re, im, nfft, twc, tws);
        for (int m = 0; m < nm; m++) {
            double e = 0;
            const double *w = fb + (size_t)m * nb;
            for (int k = 0; k < nb; k++)
                if (w[k] != 0.0) e += w[k] * (re[k] * re[k] + im[k] * im[k]) / nfft;
            mel[m] = log(e > S2W_LOG_FLOOR ? e : S2W_LOG_FLOOR);
            out->logmel[f * nm + m] = (float)mel[m];
        }
        for (int k = 0; k < nc; k++) {
            double acc = 0;
            for (int m = 0; m < nm; m++) acc += dct[(size_t)k * nm + m] * mel[m];
            out->mfcc[f * nc + k] = (float)acc;
        }
    }
    normalise(out->logmel, out->logmel_norm, nf, nm, c.norm);
    normalise(out->mfcc, out->mfcc_norm, nf, nc, c.norm);

    out->format_version = S2W_FEAT_FORMAT_VERSION;
    memcpy(out->lib_version, S2W_FEAT_LIB_VERSION, sizeof S2W_FEAT_LIB_VERSION);
    out->config = c;
    out->n_frames = nf; out->n_mels = (size_t)nm; out->n_mfcc = (size_t)nc;
    out->source_samples = a->n_samples;
    st = S2W_OK;
done:
    free(win); free(twc); free(tws); free(re); free(im); free(fb); free(dct); free(mel);
    if (st != S2W_OK) s2w_features_free(out);
    return st;
}

static void write_matrix(FILE *fp, const char *name, const float *m, size_t r, size_t c) {
    fprintf(fp, "matrix %s %zu %zu\n", name, r, c);
    for (size_t i = 0; i < r; i++) {
        for (size_t j = 0; j < c; j++) fprintf(fp, j ? " %.6f" : "%.6f", (double)m[i * c + j]);
        fputc('\n', fp);
    }
}

s2w_status s2w_features_write_text(FILE *fp, const s2w_features *f, unsigned which) {
    if (!fp || !f || !f->logmel) return S2W_ERR_BAD_PARAMS;
    const s2w_feat_config *c = &f->config;
    static const char *NORM[] = {"none", "mean", "meanvar"};
    fprintf(fp, "s2w-features v%u\nlib_version=%s\n", f->format_version, f->lib_version);
    fprintf(fp, "sample_rate=%d\nframe_ms=%d\nhop_ms=%d\nfft_size=%d\nn_mels=%d\nn_mfcc=%d\n",
            c->sample_rate, c->frame_ms, c->hop_ms, c->fft_size, c->n_mels, c->n_mfcc);
    fprintf(fp, "f_min_hz=%.3f\nf_max_hz=%.3f\npreemph=%.3f\nnorm=%s\n", c->f_min_hz, c->f_max_hz,
            c->preemph, NORM[c->norm]);
    fprintf(fp, "source_samples=%zu\nn_frames=%zu\n", f->source_samples, f->n_frames);
    fprintf(fp, "quality_duration_s=%.3f\nquality_peak=%.4f\nquality_rms_dbfs=%.2f\n"
                "quality_clipped_samples=%zu\nquality_silence_ratio=%.3f\nquality_warnings=0x%02x\n",
            f->quality.duration_s, f->quality.peak, f->quality.rms_dbfs, f->quality.clipped_samples,
            f->quality.silence_ratio, f->quality.warnings);
    fprintf(fp, "mel_centers_hz=");
    for (int b = 0; b < c->n_mels; b++) fprintf(fp, b ? " %.2f" : "%.2f", s2w_feat_mel_center_hz(c, b));
    fputc('\n', fp);
    if (which & S2W_OUT_LOGMEL_RAW) write_matrix(fp, "logmel_raw", f->logmel, f->n_frames, f->n_mels);
    if (which & S2W_OUT_LOGMEL_NORM) write_matrix(fp, "logmel_norm", f->logmel_norm, f->n_frames, f->n_mels);
    if (which & S2W_OUT_MFCC_RAW) write_matrix(fp, "mfcc_raw", f->mfcc, f->n_frames, f->n_mfcc);
    if (which & S2W_OUT_MFCC_NORM) write_matrix(fp, "mfcc_norm", f->mfcc_norm, f->n_frames, f->n_mfcc);
    return ferror(fp) ? S2W_ERR_IO : S2W_OK;
}
