#include "s2w_json.h"

#include <math.h>
#include <string.h>

static void pre(s2w_json *j, const char *key) {
    if (!j->first[j->depth]) fputc(',', j->f);
    j->first[j->depth] = 0;
    if (j->depth > 0 || key) {
        fputc('\n', j->f);
        for (int i = 0; i < j->depth; i++) fputs("  ", j->f);
    }
    if (key) {
        fputc('"', j->f); fputs(key, j->f); fputs("\": ", j->f);
    }
}
static void esc(FILE *f, const char *s) {
    fputc('"', f);
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c == '\n') fputs("\\n", f);
        else if (c == '\t') fputs("\\t", f);
        else if (c < 0x20) fprintf(f, "\\u%04x", c);
        else fputc(c, f);
    }
    fputc('"', f);
}
void js_init(s2w_json *j, FILE *f) { memset(j, 0, sizeof *j); j->f = f; j->first[0] = 1; }
static void open(s2w_json *j, const char *key, char c) {
    pre(j, key);
    fputc(c, j->f);
    j->depth++;
    j->first[j->depth] = 1;
}
static void close_(s2w_json *j, char c) {
    int empty = j->first[j->depth];
    j->depth--;
    if (!empty) {
        fputc('\n', j->f);
        for (int i = 0; i < j->depth; i++) fputs("  ", j->f);
    }
    fputc(c, j->f);
}
void js_obj(s2w_json *j, const char *key) { open(j, key, '{'); }
void js_obj_end(s2w_json *j) { close_(j, '}'); }
void js_arr(s2w_json *j, const char *key) { open(j, key, '['); }
void js_arr_end(s2w_json *j) { close_(j, ']'); }
void js_str(s2w_json *j, const char *key, const char *v) { pre(j, key); esc(j->f, v ? v : ""); }
void js_int(s2w_json *j, const char *key, long long v) { pre(j, key); fprintf(j->f, "%lld", v); }
void js_num(s2w_json *j, const char *key, double v) {
    pre(j, key);
    if (isfinite(v)) fprintf(j->f, "%.9g", v); else fputs("null", j->f);
}
void js_bool(s2w_json *j, const char *key, int v) { pre(j, key); fputs(v ? "true" : "false", j->f); }
void js_null(s2w_json *j, const char *key) { pre(j, key); fputs("null", j->f); }
void js_end(s2w_json *j) { fputc('\n', j->f); }
