/* Shared shapes for the S1 fixture harnesses (tools/spike/fixtures).
 *
 * One vector row per call. The same in-word and out-word layouts are used by
 * the remill harness, the RetDec harness and the PC24 reference, so the three
 * produce comparable output files (vectors.h). Every backend runs in a
 * process whose address space holds the unpacked PE image at its own VA
 * (guest.h), so a guest address is a host address.
 *
 * Out rows always start with two words the harness fills: status and exit_pc.
 *   status  0 = the function returned through its own ret
 *           1 = control left the lifted bytes (exit_pc = target)
 *           2 = the process faulted (exit_pc = faulting address)
 *           3 = not runnable in this backend (exit_pc = 0)
 *           4 = the row ran for more than two seconds (looping; exit_pc = block PC)
 *   exit_pc as above, else 0.
 * The reference emits the exit it predicts, so a row compares status, exit_pc
 * and the payload words together.
 */
#ifndef SA2_SPIKE_SHAPES_H
#define SA2_SPIKE_SHAPES_H
#include <stdint.h>

#define FN_CALC_VECTOR   0x004273B0u /* ecx=matrix, eax=vector, edx=out; rotation rows only */
#define FN_CALC_POINT    0x00426CC0u /* eax=matrix, ecx=point, edx=out, [esp+4] byte flag (0 = translate) */
#define FN_OBJECT_TREE   0x0048B9F0u /* cdecl(object, a2, a3); matrix stack, five Ninja helpers, recursion */
#define FN_CLAMP_YAW     0x00446960u /* eax=current, ecx=target, edx=limit -> eax */
#define FN_VECTOR_ANGLE  0x004469E0u /* edi=a, esi=b; head to the first ret: dot <= -1 -> eax 0x8000 */
#define FN_UNIT_VECTOR   0x0077F3A0u /* esi=vector in/out; calls _CIsqrt */
#define FN_LENGTH        0x0077FC30u /* eax=vector -> st0; calls _CIsqrt */
#define FN_ROTATE_ENTITY 0x00468E70u /* esi=vector in/out, edi=entity (angles at +8,+0xc,+0x10) */
#define FN_TRANSFORM_DIR 0x00468FA0u /* eax=direction, [esp+4]=point in/out, [esp+8]=yaw; calls 0x459260 */
#define FN_PUSH_IDENTITY 0x0044B210u /* matrix stack push then identity; calls memset (0x7acde0) */

/* Callees outside the ten that the harnesses provide natively. */
#define CALLEE_CISQRT    0x008290A0u /* CRT _CIsqrt: st0 = sqrt(st0) under the guest control word */
#define CALLEE_MEMSET    0x007ACDE0u /* CRT memset (cdecl) */
#define CALLEE_VEC_ANGLES 0x00459260u /* direction -> two BAM angles via edi/esi; native rows */

/* Game globals used by the matrix-stack functions. */
#define G_MATRIX_STACK_TOP   0x01A557FCu
#define G_MATRIX_STACK_LIMIT 0x02670588u
#define G_MATRIX_STACK_FLOOR 0x0267053Cu

/* Guest arenas outside the image (image ends at 0x2775000). */
#define A_ARGS       0x03000000u
#define A_MATRIX     (A_ARGS + 0x000u)   /* 12 words */
#define A_VECTOR     (A_ARGS + 0x100u)   /* 3 words */
#define A_POINT      (A_ARGS + 0x200u)   /* 3 words */
#define A_OUT        (A_ARGS + 0x300u)   /* 3 words, prefilled 0xDEADBEEF */
#define A_ENTITY     (A_ARGS + 0x400u)   /* entity block for 0x468E70 (angles at +8,+0xc,+0x10) */
#define A_OBJECT     (A_ARGS + 0x500u)   /* NJS_OBJECT for 0x48B9F0 */
#define A_MSTACK     0x03100000u         /* matrix stack frames, 0x30 bytes each */
#define A_MSTACK_FRAMES 16u
#define A_STACK_TOP  0x03F00000u         /* guest esp before the call */
#define GUEST_END    0x04000000u
#define SENTINEL_RET 0xDEAD0000u         /* return address the driver pushes */

typedef struct {
    uint32_t va;
    const char *name;
    uint32_t in_words;
    uint32_t out_words;  /* payload words after status and exit_pc */
} shape_t;

static const shape_t SHAPES[] = {
    {FN_CALC_VECTOR,   "calc_vector",    15, 3},   /* m[12] v[3] -> out[3] */
    {FN_CALC_POINT,    "calc_point",     16, 3},   /* m[12] p[3] flag -> out[3] */
    {FN_OBJECT_TREE,   "object_tree",    21, 12},  /* parent[12] pos[3] rot[3] scale[3] -> top matrix at the polygon builder */
    {FN_CLAMP_YAW,     "clamp_yaw",       3, 1},   /* eax ecx edx -> eax */
    {FN_VECTOR_ANGLE,  "vector_angle",    6, 2},   /* a[3] b[3] -> eax, stored dot word */
    {FN_UNIT_VECTOR,   "unit_vector",     3, 3},   /* v[3] -> v[3] */
    {FN_LENGTH,        "length",          3, 1},   /* v[3] -> float word of st0 */
    {FN_ROTATE_ENTITY, "rotate_entity",   6, 4},   /* v[3] angX angY angZ -> v[3], stack-top word */
    {FN_TRANSFORM_DIR, "transform_dir",   9, 4},   /* dir[3] point[3] yaw angle1 angle2 -> point[3], stack-top word */
    {FN_PUSH_IDENTITY, "push_identity",  14, 14},  /* frame[12] k limit_k -> eax, stack-top word, frame[12] */
};
#define SHAPE_COUNT (sizeof(SHAPES) / sizeof(SHAPES[0]))

static inline const shape_t *shape_for(uint32_t va) {
    for (unsigned i = 0; i < SHAPE_COUNT; i++) if (SHAPES[i].va == va) return &SHAPES[i];
    return 0;
}
#endif
