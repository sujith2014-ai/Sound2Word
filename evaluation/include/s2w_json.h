/* Tiny deterministic JSON writer (2-space indent, fixed key order = call order). */
#ifndef S2W_JSON_H
#define S2W_JSON_H
#include <stdio.h>

typedef struct { FILE *f; int depth; int first[32]; } s2w_json;

void js_init(s2w_json *j, FILE *f);
void js_obj(s2w_json *j, const char *key);   /* key NULL inside arrays / at root */
void js_obj_end(s2w_json *j);
void js_arr(s2w_json *j, const char *key);
void js_arr_end(s2w_json *j);
void js_str(s2w_json *j, const char *key, const char *v);
void js_int(s2w_json *j, const char *key, long long v);
void js_num(s2w_json *j, const char *key, double v);   /* non-finite -> null */
void js_bool(s2w_json *j, const char *key, int v);
void js_null(s2w_json *j, const char *key);
void js_end(s2w_json *j);                    /* trailing newline */
#endif
