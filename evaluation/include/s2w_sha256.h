/* Minimal SHA-256 (FIPS 180-4) for manifest/audio pinning. No dependencies. */
#ifndef S2W_SHA256_H
#define S2W_SHA256_H
#include <stddef.h>
#include <stdint.h>

typedef struct { uint32_t h[8]; uint64_t len; uint8_t buf[64]; size_t n; } s2w_sha256;

void s2w_sha256_init(s2w_sha256 *c);
void s2w_sha256_update(s2w_sha256 *c, const void *data, size_t len);
void s2w_sha256_hex(s2w_sha256 *c, char out[65]); /* finalises */
void s2w_sha256_buf_hex(const void *data, size_t len, char out[65]);
/* Returns 0 on success, -1 if the file cannot be read. */
int s2w_sha256_file_hex(const char *path, char out[65]);
#endif
