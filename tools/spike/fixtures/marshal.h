/* In-word -> guest arenas/registers and guest state -> out-word marshalling,
 * shared by the remill and RetDec harnesses (shapes.h documents each layout). */
#ifndef SA2_SPIKE_MARSHAL_H
#define SA2_SPIKE_MARSHAL_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    uint32_t eax, ecx, edx, esi, edi;
    uint32_t esp0;              /* where the return address sits at entry */
    uint32_t angle1, angle2;    /* native rows for the 0x459260 stub (transform_dir) */
    uint32_t dir[3];
} call_t;
/* Writes the row's inputs into the guest arenas and globals; fills registers. */
void marshal_in(uint32_t va, const uint32_t *in, call_t *c);
/* Collects the row's outputs. eax_after/st0_word are what the backend observed
 * (ignored for rows that did not return). */
void marshal_out(uint32_t va, const call_t *c, uint32_t status, uint32_t eax_after,
                 uint32_t st0_word, uint32_t *out);
#ifdef __cplusplus
}
#endif
#endif
