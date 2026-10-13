/* Vector files for the S1 fixture harnesses.
 *   input  "SA2FXV1\0" u32 va, u32 rows, u32 in_words, u32 out_words; rows x in_words u32
 *   output "SA2FXO1\0" u32 va, u32 rows, u32 words;                    rows x words u32
 * where words = 2 + out_words: status, exit_pc, payload (shapes.h). */
#ifndef SA2_SPIKE_VECTORS_H
#define SA2_SPIKE_VECTORS_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    uint32_t va, rows, in_words, out_words;
    uint32_t *in;   /* rows * in_words */
    uint32_t *out;  /* rows * (2 + out_words) */
} vectors_t;
int vectors_read(const char *path, vectors_t *v);   /* 0 on success; allocates in/out */
int vectors_write(const char *path, const vectors_t *v);
#ifdef __cplusplus
}
#endif
#endif
