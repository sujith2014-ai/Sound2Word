/* Strict CSV reader for dataset manifests. Never repairs input: BOMs, NUL bytes, ragged rows and
 * unterminated quotes are errors with a line number. */
#ifndef S2W_CSV_H
#define S2W_CSV_H
#include <stddef.h>

#include "s2w_audio.h"

#define S2W_CSV_MAX_COLS 32

typedef struct {
    char *buf;
    char **cell;          /* n_rows * n_cols; row 0 is the header */
    size_t *line;         /* source line of each row */
    size_t n_rows, n_cols;
} s2w_csv;

/* `tag` is the required first line, e.g. "#s2w-recordings v1". On error `err` receives a message. */
s2w_status s2w_csv_load(const char *path, const char *tag, s2w_csv *out, char *err, size_t errlen);
void s2w_csv_free(s2w_csv *c);
/* Column index for a header name, or -1. */
int s2w_csv_col(const s2w_csv *c, const char *name);
static inline const char *s2w_csv_at(const s2w_csv *c, size_t row, size_t col) { return c->cell[row * c->n_cols + col]; }
#endif
