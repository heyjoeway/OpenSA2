#include "marshal.h"
#include "guest.h"
#include "shapes.h"
#include <string.h>

static void words_to(uint32_t addr, const uint32_t *w, unsigned n) { memcpy(gp32(addr), w, n * 4); }
static void words_from(uint32_t addr, uint32_t *w, unsigned n) { memcpy(w, gp32(addr), n * 4); }

static void matrix_stack(uint32_t top_frame, uint32_t limit_frame) {
    *gp32(G_MATRIX_STACK_TOP) = A_MSTACK + 0x30u * top_frame;
    *gp32(G_MATRIX_STACK_LIMIT) = A_MSTACK + 0x30u * limit_frame;
    *gp32(G_MATRIX_STACK_FLOOR) = A_MSTACK;
}

void marshal_in(uint32_t va, const uint32_t *in, call_t *c) {
    memset(c, 0, sizeof *c);
    guest_reset_arenas();
    c->esp0 = A_STACK_TOP - 0x3Cu;   /* esp0 - 4 is 8-aligned, as the game's callers leave it */
    switch (va) {
    case FN_CALC_VECTOR:
        words_to(A_MATRIX, in, 12); words_to(A_VECTOR, in + 12, 3);
        c->ecx = A_MATRIX; c->eax = A_VECTOR; c->edx = A_OUT; break;
    case FN_CALC_POINT:
        words_to(A_MATRIX, in, 12); words_to(A_POINT, in + 12, 3);
        *gp32(c->esp0 + 4) = in[15];
        c->eax = A_MATRIX; c->ecx = A_POINT; c->edx = A_OUT; break;
    case FN_OBJECT_TREE:
        words_to(A_MSTACK, in, 12);                 /* frame 0 = parent matrix, the current top */
        words_to(A_OBJECT + 8, in + 12, 9);          /* pos, rot, scale at +8..+0x28 */
        matrix_stack(0, A_MSTACK_FRAMES - 1);
        *gp32(c->esp0 + 4) = A_OBJECT; *gp32(c->esp0 + 8) = 0; *gp32(c->esp0 + 12) = 0;
        break;
    case FN_CLAMP_YAW:
        c->eax = in[0]; c->ecx = in[1]; c->edx = in[2]; break;
    case FN_VECTOR_ANGLE:
        words_to(A_VECTOR, in, 3); words_to(A_POINT, in + 3, 3);
        c->edi = A_VECTOR; c->esi = A_POINT; break;
    case FN_UNIT_VECTOR:
        words_to(A_VECTOR, in, 3); c->esi = A_VECTOR; break;
    case FN_LENGTH:
        words_to(A_VECTOR, in, 3); c->eax = A_VECTOR; break;
    case FN_ROTATE_ENTITY:
        words_to(A_VECTOR, in, 3);
        *gp32(A_ENTITY + 0x8) = in[3]; *gp32(A_ENTITY + 0xC) = in[4]; *gp32(A_ENTITY + 0x10) = in[5];
        matrix_stack(2, A_MSTACK_FRAMES - 1);
        c->esi = A_VECTOR; c->edi = A_ENTITY; break;
    case FN_TRANSFORM_DIR:
        words_to(A_VECTOR, in, 3); words_to(A_POINT, in + 3, 3);
        *gp32(c->esp0 + 4) = A_POINT; *gp32(c->esp0 + 8) = in[6];
        c->angle1 = in[7]; c->angle2 = in[8]; memcpy(c->dir, in, 12);
        matrix_stack(2, A_MSTACK_FRAMES - 1);
        c->eax = A_VECTOR; break;
    case FN_PUSH_IDENTITY: {
        uint32_t k = in[12] % A_MSTACK_FRAMES, lim = in[13] % A_MSTACK_FRAMES;
        words_to(A_MSTACK + 0x30u * k, in, 12);
        matrix_stack(k, lim);
        break; }
    }
}

void marshal_out(uint32_t va, const call_t *c, uint32_t status, uint32_t eax_after,
                 uint32_t st0_word, uint32_t *out) {
    uint32_t eax = status == 0 ? eax_after : 0;
    switch (va) {
    case FN_CALC_VECTOR: case FN_CALC_POINT:
        words_from(A_OUT, out, 3); break;
    case FN_OBJECT_TREE:
        words_from(*gp32(G_MATRIX_STACK_TOP), out, 12); break;
    case FN_CLAMP_YAW:
        out[0] = eax; break;
    case FN_VECTOR_ANGLE:
        out[0] = eax; out[1] = *gp32(c->esp0 - 0x18); break;
    case FN_UNIT_VECTOR:
        words_from(A_VECTOR, out, 3); break;
    case FN_LENGTH:
        out[0] = status == 0 ? st0_word : 0; break;
    case FN_ROTATE_ENTITY:
        words_from(A_VECTOR, out, 3); out[3] = *gp32(G_MATRIX_STACK_TOP); break;
    case FN_TRANSFORM_DIR:
        words_from(A_POINT, out, 3); out[3] = *gp32(G_MATRIX_STACK_TOP); break;
    case FN_PUSH_IDENTITY:
        out[0] = eax; out[1] = *gp32(G_MATRIX_STACK_TOP);
        words_from(*gp32(G_MATRIX_STACK_TOP), out + 2, 12); break;
    }
}
