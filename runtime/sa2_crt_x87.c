/* The CRT's x87 tan, sin and cos bodies, entered with C2 clear.
 *
 * 0x00859ac8, 0x00859648 and 0x00829008 are the x87 bodies of the C runtime's tan, sin and cos:
 * the SSE2 versions (0x0085a0ee, 0x00859c9e, 0x0082c7ee) hand them an argument out of their
 * range, a NaN or an infinity, through each function's plain C entry, whose call-return edge
 * Documentation/dispatch-exceptions.json allows. Each body runs FPTAN, FSIN or FCOS, then
 * `fnstsw ax; sahf; jp`: on the processor those instructions write C2 (clear for an operand
 * within 2^63, set with the operand unchanged beyond it), and the `jp` takes the body's FPREM1
 * reduction only when it is set. remill's FPTAN, FSIN and FCOS do not write C2, so the branch
 * read whatever C2 the guest's earlier code left: after an unordered compare, or FXAM of a
 * normal number, tan returned tan(1.0) with an extra x87 register pushed, sin sin(sin x) and cos
 * cos(cos x) (harness/x87_fallback_probe.c, which sets C2 on entry).
 *
 * Nothing in a body between its entry and that `jp` writes or reads C2 (FNSTCW, FLDCW and the
 * je on __fload_withFB's ZF do not), so clearing it on entry gives the `jp` what the processor
 * gives it for an operand within 2^63. For one beyond 2^63 the body then takes remill's result,
 * which reduces the argument exactly, where the processor would have reduced it by FPREM1's
 * 66-bit pi: the low bits can differ from a native run there, which no replay reaches (these
 * bodies trapped until the exceptions above).
 *
 * Wrapped in every build (tools/s4/link_s4.py PRODUCTION_WRAPS), so the function table's rows,
 * the dispatch and every direct call reach the wrapper. */
#include <stdint.h>
#include <string.h>

void *__real_sub_859ac8(void *state, uint32_t pc, void *memory);
void *__real_sub_859648(void *state, uint32_t pc, void *memory);
void *__real_sub_829008(void *state, uint32_t pc, void *memory);

/* remill's State by field name (runtime/sa2_ninja_math.c), which the harness holds to
 * offsetof(State, ...). */
extern const struct sa2_ninja_field { const char *field; uint32_t offset; } sa2_ninja_layout[];
extern const uint32_t sa2_ninja_layout_count;

static uint32_t c2_offset(void) {
    static uint32_t offset;
    if (!offset)
        for (uint32_t i = 0; i < sa2_ninja_layout_count; i++)
            if (!strcmp(sa2_ninja_layout[i].field, "sw.c2")) offset = sa2_ninja_layout[i].offset;
    return offset;
}

static void clear_c2(void *state) {
    const uint32_t o = c2_offset();
    if (o) ((uint8_t *)state)[o] = 0;
}

__attribute__((used, retain))
void *__wrap_sub_859ac8(void *state, uint32_t pc, void *memory) {
    clear_c2(state);
    return __real_sub_859ac8(state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_859648(void *state, uint32_t pc, void *memory) {
    clear_c2(state);
    return __real_sub_859648(state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_829008(void *state, uint32_t pc, void *memory) {
    clear_c2(state);
    return __real_sub_829008(state, pc, memory);
}
