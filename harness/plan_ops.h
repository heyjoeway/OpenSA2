/* The plan format the corpus driver executes (design 3.7 level 1, the generic
 * form of harness/fixture_shapes.h).
 *
 * `harness/fixture_driver.c` knows one C shape per lifted function, which is
 * right for eleven functions and wrong for a corpus of fifty-four fixtures over
 * fourteen functions with per-fixture memory layouts. The plan file moves the
 * shape out of C and into data: a per-row op stream that pokes guest memory,
 * sets registers, calls a guest address and peeks the words the fixture
 * compares. The C side executes ops; the Python side (tools/s2/corpus_run)
 * knows what a fixture means.
 *
 *   header  "SA2PLN1\0" u32 version, entry_va, rows, out_words, global_bytes
 *   global  op stream executed once, after runtime init (the fixture's tables)
 *   rows    rows x (u32 byte length, then that many bytes of op stream)
 *
 * An op is a u32 opcode followed by its fixed arguments and, for OP_POKE, the
 * payload words. Every op stream ends with OP_END.
 *
 * Output is the S1 vector-file result format so a row this driver produces is
 * directly comparable with one `fixture_driver` or the S1 spike produced:
 *   "SA2FXO1\0" u32 va, rows, words; rows x words u32 = status, exit_pc, payload.
 */
#ifndef SA2_HARNESS_PLAN_OPS_H
#define SA2_HARNESS_PLAN_OPS_H

#define SA2_PLAN_MAGIC "SA2PLN1\0"

enum {
    OP_END       = 0,
    OP_POKE      = 1,  /* addr, nbytes, ceil(nbytes/4) payload words */
    OP_FILL      = 2,  /* addr, nbytes, byte value */
    OP_REG       = 3,  /* register index (cpu_state.h), value */
    OP_CALL      = 4,  /* guest va (0 = the plan's entry_va), timeout seconds */
    OP_PEEK      = 5,  /* addr, nbytes -- emits nbytes/4 output words */
    OP_EMIT_REG  = 6,  /* register index -- emits its value */
    OP_EMIT_ST0  = 7,  /* emits (float)st[0] as a u32 word */
    OP_EMIT_WORD = 8,  /* literal word, for provenance columns */
    OP_RESET     = 9,  /* reset the CPU state (x87 cw 0x007F, all GPRs zero) */
    OP_SENTINEL  = 10, /* place the sentinel return address at ESP */
    OP_EMIT_ST0_F64 = 11, /* emits (double)st[0] as two u32 words */
    OP_PEEK_PTR  = 12, /* pointer addr, nbytes -- peeks at the word stored at that address */
    OP_CW        = 13, /* x87 control word for this row (OP_RESET's default is 0x007F) */
    OP_EMIT_CW   = 14, /* emits the control word the call left behind */
    /* The entry x87 stack (G2 finding 2). Until these, the op stream could set general
     * registers (OP_REG) and the control word (OP_CW) and nothing else, so every row
     * entered through `sa2_cpu_state_reset`'s zeroed `st[]`. Four shipping windows read
     * caller x87 registers -- 0x004624C0 (ST0), 0x00462F83 (ST0 and ST1, whose `fcompp`
     * steers control flow), 0x00462526 (ST0) and 0x0048DA51 (ST0) -- so those reads were
     * answered with 0.0 where the native had whatever the prologue pushed. */
    OP_ST        = 15, /* i (0..7), f64 low word, f64 high word: st[i] = that double */
    OP_ST_F80    = 16, /* i, then 10 bytes (3 words, the last half used) of an x87 m80 */
    OP_ST_UNSUPPLIED = 17, /* i: the window reads st(i) and no fixture records it. The row
                            * is driven but reported with status SA2_TRAP_UNSUPPLIED, so it
                            * cannot pass on a zero the driver invented. */
    OP_EMIT_ST   = 18, /* i -- emits (float)st[i] as a u32 word */
};

#endif
