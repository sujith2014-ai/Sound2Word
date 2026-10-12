#include "s2w_csv.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_BYTES (64u * 1024u * 1024u)

static void seterr(char *err, size_t n, const char *fmt, size_t line, const char *a) {
    if (err && n) snprintf(err, n, fmt, line, a ? a : "");
}

void s2w_csv_free(s2w_csv *c) {
    if (!c) return;
    free(c->buf); free(c->cell); free(c->line);
    memset(c, 0, sizeof *c);
}

int s2w_csv_col(const s2w_csv *c, const char *name) {
    for (size_t j = 0; j < c->n_cols; j++) if (!strcmp(s2w_csv_at(c, 0, j), name)) return (int)j;
    return -1;
}

s2w_status s2w_csv_load(const char *path, const char *tag, s2w_csv *out, char *err, size_t errlen) {
    memset(out, 0, sizeof *out);
    FILE *f = fopen(path, "rb");
    if (!f) { seterr(err, errlen, "line %zu: cannot open file %s", 0, path); return S2W_ERR_IO; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0 || (unsigned long)sz > MAX_BYTES) { fclose(f); seterr(err, errlen, "line %zu: file size invalid%s", 0, ""); return S2W_ERR_TOO_LARGE; }
    char *buf = malloc((size_t)sz + 2);
    if (!buf) { fclose(f); return S2W_ERR_NOMEM; }
    size_t got = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    if (got != (size_t)sz) { free(buf); seterr(err, errlen, "line %zu: read error%s", 0, ""); return S2W_ERR_IO; }
    buf[sz] = '\n'; buf[sz + 1] = 0; /* sentinel so the last line terminates */
    if (memchr(buf, 0, (size_t)sz)) { free(buf); seterr(err, errlen, "line %zu: NUL byte in file%s", 0, ""); return S2W_ERR_BAD_PARAMS; }
    if (sz >= 3 && !memcmp(buf, "\xEF\xBB\xBF", 3)) {
        free(buf); seterr(err, errlen, "line %zu: UTF-8 BOM present; save as UTF-8 without BOM%s", 1, ""); return S2W_ERR_BAD_PARAMS;
    }

    /* tag line */
    char *p = buf;
    char *eol = strchr(p, '\n');
    size_t tl = (size_t)(eol - p);
    if (tl && p[tl - 1] == '\r') tl--;
    if (tl != strlen(tag) || strncmp(p, tag, tl)) {
        free(buf); seterr(err, errlen, "line %zu: expected first line '%s'", 1, tag); return S2W_ERR_BAD_PARAMS;
    }
    p = eol + 1;

    size_t cap = 256, ncell = 0, nrows = 0, ncols = 0, lcap = 64;
    char **cell = malloc(cap * sizeof *cell);
    size_t *line = malloc(lcap * sizeof *line);
    if (!cell || !line) { free(buf); free(cell); free(line); return S2W_ERR_NOMEM; }
    size_t lineno = 1;
    char *end = buf + sz + 1;
    s2w_status st = S2W_OK;
    while (p < end) {
        lineno++;
        size_t row_cells = 0, row_start_line = lineno;
        int in_row = 1;
        if (*p == '\n' || (*p == '\r' && p[1] == '\n')) { p += (*p == '\r') + 1; continue; } /* blank line */
        while (in_row) {
            char *w = p, *start = p;
            int quoted = (*p == '"');
            if (quoted) {
                p++; w = start;
                for (;;) {
                    if (p >= end) { seterr(err, errlen, "line %zu: unterminated quote%s", row_start_line, ""); st = S2W_ERR_BAD_PARAMS; goto fail; }
                    if (*p == '"') {
                        if (p[1] == '"') { *w++ = '"'; p += 2; continue; }
                        p++; break;
                    }
                    if (*p == '\n') lineno++;
                    *w++ = *p++;
                }
                if (*p != ',' && *p != '\n' && *p != '\r') { seterr(err, errlen, "line %zu: text after closing quote%s", lineno, ""); st = S2W_ERR_BAD_PARAMS; goto fail; }
            } else {
                while (*p != ',' && *p != '\n') {
                    if (*p == '"') { seterr(err, errlen, "line %zu: stray quote in unquoted field%s", lineno, ""); st = S2W_ERR_BAD_PARAMS; goto fail; }
                    p++;
                }
                w = p;
                if (w > start && w[-1] == '\r' && *p == '\n') w--;
            }
            int last = (*p != ',');
            char *next = p + 1;
            if (*p == '\r' && p[1] == '\n') next = p + 2;
            *w = 0;
            if (ncell == cap) {
                cap *= 2;
                char **nc = realloc(cell, cap * sizeof *cell);
                if (!nc) { st = S2W_ERR_NOMEM; goto fail; }
                cell = nc;
            }
            cell[ncell++] = start;
            row_cells++;
            if (row_cells > S2W_CSV_MAX_COLS) { seterr(err, errlen, "line %zu: too many columns%s", row_start_line, ""); st = S2W_ERR_BAD_PARAMS; goto fail; }
            p = next;
            if (last) in_row = 0;
        }
        if (nrows == 0) ncols = row_cells;
        else if (row_cells != ncols) { seterr(err, errlen, "line %zu: wrong number of columns%s", row_start_line, ""); st = S2W_ERR_BAD_PARAMS; goto fail; }
        if (nrows == lcap) {
            lcap *= 2;
            size_t *nl = realloc(line, lcap * sizeof *line);
            if (!nl) { st = S2W_ERR_NOMEM; goto fail; }
            line = nl;
        }
        line[nrows++] = row_start_line;
    }
    if (nrows == 0) { seterr(err, errlen, "line %zu: missing header row%s", 2, ""); st = S2W_ERR_BAD_PARAMS; goto fail; }
    out->buf = buf; out->cell = cell; out->line = line; out->n_rows = nrows; out->n_cols = ncols;
    return S2W_OK;
fail:
    free(buf); free(cell); free(line);
    return st;
}
