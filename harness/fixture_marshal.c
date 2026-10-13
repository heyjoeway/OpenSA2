/* Row inputs -> guest memory and registers, guest memory -> row outputs.
 *
 * Every guest access goes through the runtime's bounds-checked `sa2_poke` and
 * `sa2_peek` (design 3.5), not through a raw pointer: the harness is the first
 * consumer of that C ABI entry and exercising it here is the point.
 */
#include "fixture_shapes.h"

#include <string.h>

#include "../runtime/guest_memory.h"

static void words_to(uint32_t addr, const uint32_t *w, unsigned n) { sa2_poke(addr, n * 4u, w); }
static void words_from(uint32_t addr, uint32_t *w, unsigned n)     { sa2_peek(addr, n * 4u, w); }

static uint32_t word_at(uint32_t addr) {
    uint32_t v = 0;
    sa2_peek(addr, 4, &v);
    return v;
}

static void put_word(uint32_t addr, uint32_t v) { sa2_poke(addr, 4, &v); }

static void matrix_stack(uint32_t top_frame, uint32_t limit_frame) {
    put_word(G_MATRIX_STACK_TOP,   A_MSTACK + 0x30u * top_frame);
    put_word(G_MATRIX_STACK_LIMIT, A_MSTACK + 0x30u * limit_frame);
    put_word(G_MATRIX_STACK_FLOOR, A_MSTACK);
}

/* The spike's arena reset, expressed against the runtime's guest memory:
 * argument arena, matrix-stack frames and the top of the guest stack are
 * zeroed per row, and the output arena is poisoned so a row that writes
 * nothing is visible. */
static void reset_arenas(void) {
    sa2_guest_fill(A_ARGS, 0, 0x10000u);
    sa2_guest_fill(A_MSTACK, 0, 0x30u * A_MSTACK_FRAMES + 0x100u);
    sa2_guest_fill(A_STACK_TOP - 0x10000u, 0, 0x10000u);
    for (int i = 0; i < 3; i++) put_word(A_OUT + 4u * (uint32_t)i, 0xDEADBEEFu);
}

void sa2_fixture_marshal_in(uint32_t va, const uint32_t *in, sa2_fixture_call *c) {
    memset(c, 0, sizeof *c);
    reset_arenas();
    c->esp0 = A_STACK_TOP - 0x3Cu;   /* esp0 - 4 is 8-aligned, as the game's callers leave it */
    switch (va) {
    case FN_CALC_VECTOR:
        words_to(A_MATRIX, in, 12); words_to(A_VECTOR, in + 12, 3);
        c->ecx = A_MATRIX; c->eax = A_VECTOR; c->edx = A_OUT; break;
    case FN_CALC_POINT:
        words_to(A_MATRIX, in, 12); words_to(A_POINT, in + 12, 3);
        put_word(c->esp0 + 4, in[15]);
        c->eax = A_MATRIX; c->ecx = A_POINT; c->edx = A_OUT; break;
    case FN_CALC_POINT_ACTIVE:
        /* The matrix is the live top of the guest's matrix stack, not an
         * argument: the function loads it from 0x1A557FC. */
        words_to(A_MSTACK, in, 12); words_to(A_POINT, in + 12, 3);
        matrix_stack(0, A_MSTACK_FRAMES - 1);
        c->ecx = A_POINT; c->edx = A_OUT; break;
    case FN_OBJECT_TREE:
        words_to(A_MSTACK, in, 12);
        words_to(A_OBJECT + 8, in + 12, 9);
        matrix_stack(0, A_MSTACK_FRAMES - 1);
        put_word(c->esp0 + 4, A_OBJECT);
        put_word(c->esp0 + 8, 0);
        put_word(c->esp0 + 12, 0);
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
        put_word(A_ENTITY + 0x8, in[3]);
        put_word(A_ENTITY + 0xC, in[4]);
        put_word(A_ENTITY + 0x10, in[5]);
        matrix_stack(2, A_MSTACK_FRAMES - 1);
        c->esi = A_VECTOR; c->edi = A_ENTITY; break;
    case FN_TRANSFORM_DIR:
        words_to(A_VECTOR, in, 3); words_to(A_POINT, in + 3, 3);
        put_word(c->esp0 + 4, A_POINT);
        put_word(c->esp0 + 8, in[6]);
        c->angle1 = in[7]; c->angle2 = in[8];
        memcpy(c->dir, in, 12);
        matrix_stack(2, A_MSTACK_FRAMES - 1);
        c->eax = A_VECTOR; break;
    case FN_PUSH_IDENTITY: {
        uint32_t k = in[12] % A_MSTACK_FRAMES, lim = in[13] % A_MSTACK_FRAMES;
        words_to(A_MSTACK + 0x30u * k, in, 12);
        matrix_stack(k, lim);
        break; }
    default: break;
    }
}

void sa2_fixture_marshal_out(uint32_t va, const sa2_fixture_call *c, uint32_t status,
                             uint32_t eax_after, uint32_t st0_word, uint32_t *out) {
    uint32_t eax = status == 0 ? eax_after : 0;
    switch (va) {
    case FN_CALC_VECTOR: case FN_CALC_POINT: case FN_CALC_POINT_ACTIVE:
        words_from(A_OUT, out, 3); break;
    case FN_OBJECT_TREE:
        words_from(word_at(G_MATRIX_STACK_TOP), out, 12); break;
    case FN_CLAMP_YAW:
        out[0] = eax; break;
    case FN_VECTOR_ANGLE:
        out[0] = eax; out[1] = word_at(c->esp0 - 0x18); break;
    case FN_UNIT_VECTOR:
        words_from(A_VECTOR, out, 3); break;
    case FN_LENGTH:
        out[0] = status == 0 ? st0_word : 0; break;
    case FN_ROTATE_ENTITY:
        words_from(A_VECTOR, out, 3); out[3] = word_at(G_MATRIX_STACK_TOP); break;
    case FN_TRANSFORM_DIR:
        words_from(A_POINT, out, 3); out[3] = word_at(G_MATRIX_STACK_TOP); break;
    case FN_PUSH_IDENTITY:
        out[0] = eax; out[1] = word_at(G_MATRIX_STACK_TOP);
        words_from(word_at(G_MATRIX_STACK_TOP), out + 2, 12); break;
    default: break;
    }
}
