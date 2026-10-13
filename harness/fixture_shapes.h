/* Fixture shapes for the runtime v0 harness (design 3.7 level 1, design 5 S2
 * "Harness level 1 (function fixtures) running in CI").
 *
 * One vector row per call, in the vector-file format the S1 spike introduced
 * so the rows this harness produces are directly comparable with the ones
 * `tools/spike/fixtures/run_fixtures.py` produced:
 *
 *   input  "SA2FXV1\0" u32 va, rows, in_words, out_words; rows x in_words u32
 *   output "SA2FXO1\0" u32 va, rows, words;               rows x words u32
 *   words = 2 + out_words: status, exit_pc, payload.
 *
 * Status values are `SA2_TRAP_*` from runtime/sa2_trap.h, which carry the
 * spike's meanings unchanged (0 returned, 1 control left the lifted code,
 * 2 fault, 3 not runnable, 4 timeout).
 *
 * The guest arena addresses are the spike's, because the fixture vectors were
 * generated against them and the matrix-stack functions read a *guest global*
 * that has to point into an arena. They sit inside the runtime's scratch
 * region (runtime/guest_memory.h, SA2_GUEST_SCRATCH_BASE), and the stack top
 * is the runtime's own (SA2_GUEST_STACK_TOP).
 */
#ifndef SA2_HARNESS_FIXTURE_SHAPES_H
#define SA2_HARNESS_FIXTURE_SHAPES_H

#include <stdint.h>

#include "../runtime/guest_memory.h"

/* The ten S1 spike functions, plus the active point transform the S2 entry
 * test lifted (0x00426D50, the one the native corpus traced). */
#define FN_CALC_VECTOR       0x004273B0u /* ecx=matrix, eax=vector, edx=out */
#define FN_CALC_POINT        0x00426CC0u /* eax=matrix, ecx=point, edx=out, [esp+4] flag */
#define FN_CALC_POINT_ACTIVE 0x00426D50u /* ecx=point, edx=out; matrix = *[0x1A557FC] */
#define FN_OBJECT_TREE       0x0048B9F0u /* cdecl(object, a2, a3); matrix stack, recursion */
#define FN_CLAMP_YAW         0x00446960u /* eax=current, ecx=target, edx=limit -> eax */
#define FN_VECTOR_ANGLE      0x004469E0u /* edi=a, esi=b */
#define FN_UNIT_VECTOR       0x0077F3A0u /* esi=vector in/out; calls _CIsqrt */
#define FN_LENGTH            0x0077FC30u /* eax=vector -> st0; calls _CIsqrt */
#define FN_ROTATE_ENTITY     0x00468E70u /* esi=vector in/out, edi=entity */
#define FN_TRANSFORM_DIR     0x00468FA0u /* eax=direction, [esp+4]=point, [esp+8]=yaw */
#define FN_PUSH_IDENTITY     0x0044B210u /* matrix stack push then identity; calls memset */

/* Callees outside the lifted set that the harness answers natively. */
#define CALLEE_CISQRT     0x008290A0u /* CRT _CIsqrt: st0 = sqrt(st0) under the guest cw */
#define CALLEE_MEMSET     0x007ACDE0u /* CRT memset (cdecl) */
#define CALLEE_VEC_ANGLES 0x00459260u /* direction -> two BAM angles; answered from the row */

/* Game globals the matrix-stack functions use. */
#define G_MATRIX_STACK_TOP   0x01A557FCu
#define G_MATRIX_STACK_LIMIT 0x02670588u
#define G_MATRIX_STACK_FLOOR 0x0267053Cu

/* Guest arenas, inside the runtime's scratch region. */
#define A_ARGS          (SA2_GUEST_SCRATCH_BASE)        /* 0x03000000 */
#define A_MATRIX        (A_ARGS + 0x000u)
#define A_VECTOR        (A_ARGS + 0x100u)
#define A_POINT         (A_ARGS + 0x200u)
#define A_OUT           (A_ARGS + 0x300u)
#define A_ENTITY        (A_ARGS + 0x400u)
#define A_OBJECT        (A_ARGS + 0x500u)
#define A_MSTACK        (SA2_GUEST_SCRATCH_BASE + 0x100000u)   /* 0x03100000 */
#define A_MSTACK_FRAMES 16u
#define A_STACK_TOP     (SA2_GUEST_STACK_TOP)           /* 0x03F00000 */

typedef struct {
    uint32_t    va;
    const char *name;
    uint32_t    in_words;
    uint32_t    out_words;
} sa2_fixture_shape;

static const sa2_fixture_shape SA2_FIXTURE_SHAPES[] = {
    {FN_CALC_VECTOR,       "calc_vector",       15,  3},
    {FN_CALC_POINT,        "calc_point",        16,  3},
    {FN_CALC_POINT_ACTIVE, "calc_point_active", 15,  3},
    {FN_OBJECT_TREE,       "object_tree",       21, 12},
    {FN_CLAMP_YAW,         "clamp_yaw",          3,  1},
    {FN_VECTOR_ANGLE,      "vector_angle",       6,  2},
    {FN_UNIT_VECTOR,       "unit_vector",        3,  3},
    {FN_LENGTH,            "length",             3,  1},
    {FN_ROTATE_ENTITY,     "rotate_entity",      6,  4},
    {FN_TRANSFORM_DIR,     "transform_dir",      9,  4},
    {FN_PUSH_IDENTITY,     "push_identity",     14, 14},
};
#define SA2_FIXTURE_SHAPE_COUNT (sizeof(SA2_FIXTURE_SHAPES) / sizeof(SA2_FIXTURE_SHAPES[0]))

static inline const sa2_fixture_shape *sa2_fixture_shape_for(uint32_t va) {
    for (unsigned i = 0; i < SA2_FIXTURE_SHAPE_COUNT; i++)
        if (SA2_FIXTURE_SHAPES[i].va == va) return &SA2_FIXTURE_SHAPES[i];
    return 0;
}

/* Per-row call context: the registers a row starts with, plus the native rows
 * the 0x00459260 stub answers from. */
typedef struct {
    uint32_t eax, ecx, edx, esi, edi;
    uint32_t esp0;              /* where the return address sits at entry */
    uint32_t angle1, angle2;
    uint32_t dir[3];
} sa2_fixture_call;

void sa2_fixture_marshal_in(uint32_t va, const uint32_t *in, sa2_fixture_call *c);
void sa2_fixture_marshal_out(uint32_t va, const sa2_fixture_call *c, uint32_t status,
                             uint32_t eax_after, uint32_t st0_word, uint32_t *out);

/* Native callees, registered into the function table by the driver. */
void sa2_fixture_register_natives(void);
void sa2_fixture_set_current(const sa2_fixture_call *c);

#endif
