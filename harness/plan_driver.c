/* The corpus driver: execute a plan file (harness/plan_ops.h) against runtime v0.
 *
 *   usage: plan_driver <sonic2app-unpacked.exe> <plan.bin> <out.bin> [--trace N]
 *
 * One process per fixture. Every row is an op stream: pokes that lay the
 * fixture's live-in state into guest memory, register assignments, one or more
 * `sa2_call`s, and peeks that read back the words the fixture compares. The
 * driver knows nothing about any fixture; `tools/s2/corpus_run/plans.py`
 * knows, and the plan file is the contract.
 *
 * `__sa2_dispatch_stub`, the landing pad the IR rewriter
 * (tools/s2/corpus_run/internalize.py) folds remill's per-module missing-block
 * stubs into, used to be defined here and nowhere else, which made this driver
 * the only program that could link folded IR. It now lives in
 * `runtime/function_table_impl.c` beside the dispatch it calls; this driver gets
 * it from there, and that this driver still links is the evidence that it does.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "plan_ops.h"

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_trap.h"

typedef struct {
    uint32_t  version, entry_va, rows, out_words, global_bytes;
    uint8_t  *blob;         /* the whole file */
    size_t    size;
    uint8_t  *global;       /* global op stream */
    uint8_t  *rows_at;      /* first row record */
} plan_t;

static int plan_read(const char *path, plan_t *p) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return -1; }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    p->blob = (uint8_t *)malloc((size_t)n);
    if (!p->blob || fread(p->blob, 1, (size_t)n, f) != (size_t)n) { fclose(f); return -1; }
    fclose(f);
    p->size = (size_t)n;
    if (n < 28 || memcmp(p->blob, SA2_PLAN_MAGIC, 8) != 0) {
        fprintf(stderr, "%s: not a plan file\n", path);
        return -1;
    }
    uint32_t h[5];
    memcpy(h, p->blob + 8, sizeof h);
    p->version = h[0]; p->entry_va = h[1]; p->rows = h[2]; p->out_words = h[3]; p->global_bytes = h[4];
    p->global = p->blob + 28;
    p->rows_at = p->global + p->global_bytes;
    return 0;
}

typedef struct {
    sa2_cpu_state_t   st;
    sa2_call_result_t res;
    uint32_t          status, exit_pc;
    uint32_t         *out;
    uint32_t          emitted, capacity;
    uint32_t          entry_va;
    uint32_t          calls;
    int               unsupplied;      /* OP_ST_UNSUPPLIED fired on this row */
    uint32_t          unsupplied_reg;
} ctx_t;

/* `SA2_X87_ENTRY` puts a value other than 0.0 in every entry x87 register.
 *
 * Every row enters through `sa2_cpu_state_reset`, which zeroes `st[]`, and until OP_ST
 * the plan vocabulary had no way to set them -- so a window that reads a caller x87
 * register read 0.0, and nothing measured whether that mattered. Setting this to a
 * value the native never had turns "the zeros are never read" into a measurement:
 * a fixture whose verdict does not move is one whose entry x87 stack is dead.
 *
 *   SA2_X87_ENTRY=1.5 private/generated/.../corpus_driver_pc24 ...
 */
static int   g_x87_entry_set;
static double g_x87_entry;

static void reset_state(ctx_t *c) {
    sa2_cpu_state_reset(&c->st);
    if (g_x87_entry_set)
        for (int i = 0; i < 8; i++) c->st.st80[i] = sa2_f80_from_f64(g_x87_entry);
}

static void emit(ctx_t *c, uint32_t w) {
    if (c->emitted < c->capacity) c->out[c->emitted] = w;
    c->emitted++;
}

/* An x87 register as the float `fst m32` would store: the x87 unit's conversion
 * (cpu_state.h), so the word is the same on a host without one. */
static uint32_t float_word(sa2_f80 v) {
    float f = sa2_f80_to_f32(v);
    uint32_t w;
    memcpy(&w, &f, 4);
    return w;
}

/* Execute one op stream. Returns 0 on a clean walk. */
static int run_ops(ctx_t *c, const uint8_t *ops, size_t len, int trace) {
    const uint32_t *w = (const uint32_t *)ops;
    const uint32_t *end = (const uint32_t *)(ops + len);
    while (w < end) {
        uint32_t op = *w++;
        if (op == OP_END) break;
        switch (op) {
        case OP_POKE: {
            uint32_t addr = w[0], nbytes = w[1];
            w += 2;
            if (sa2_poke(addr, nbytes, w) != SA2_OK) {
                fprintf(stderr, "poke %#010x +%u out of range\n", addr, nbytes);
                return -1;
            }
            w += (nbytes + 3) / 4;
            break;
        }
        case OP_FILL: {
            uint32_t addr = w[0], nbytes = w[1], val = w[2];
            w += 3;
            if (sa2_guest_fill(addr, (int)val, nbytes) != SA2_OK) {
                fprintf(stderr, "fill %#010x +%u out of range\n", addr, nbytes);
                return -1;
            }
            break;
        }
        case OP_REG:
            c->st.gpr[w[0]] = w[1];
            w += 2;
            break;
        case OP_RESET:
            reset_state(c);
            break;
        case OP_SENTINEL:
            sa2_call_place_return(&c->st, SA2_CALL_SENTINEL);
            break;
        case OP_CALL: {
            uint32_t va = w[0] ? w[0] : c->entry_va, timeout = w[1];
            w += 2;
            sa2_call(va, &c->st, timeout, &c->res);
            c->calls++;
            if (c->res.status != SA2_TRAP_NONE && c->status == SA2_TRAP_NONE) {
                c->status = c->res.status;
                c->exit_pc = c->res.exit_pc;
                if (trace) fprintf(stderr, "  trap status %u at %#010x: %s\n",
                                   c->res.status, c->res.exit_pc, c->res.note);
            }
            break;
        }
        case OP_PEEK: {
            uint32_t addr = w[0], nbytes = w[1];
            w += 2;
            for (uint32_t off = 0; off < nbytes; off += 4) {
                uint32_t v = 0;
                if (sa2_peek(addr + off, 4, &v) != SA2_OK) v = 0xDEADBEEFu;
                emit(c, v);
            }
            break;
        }
        case OP_PEEK_PTR: {
            uint32_t ptr_at = w[0], nbytes = w[1];
            w += 2;
            uint32_t addr = 0;
            sa2_peek(ptr_at, 4, &addr);
            for (uint32_t off = 0; off < nbytes; off += 4) {
                uint32_t v = 0;
                if (sa2_peek(addr + off, 4, &v) != SA2_OK) v = 0xDEADBEEFu;
                emit(c, v);
            }
            break;
        }
        case OP_EMIT_REG:
            emit(c, c->st.gpr[*w++]);
            break;
        case OP_EMIT_ST0:
            emit(c, float_word(c->st.st80[0]));
            break;
        case OP_EMIT_ST0_F64: {
            double d = sa2_f80_to_f64(c->st.st80[0]);
            uint32_t two[2];
            memcpy(two, &d, 8);
            emit(c, two[0]);
            emit(c, two[1]);
            break;
        }
        case OP_EMIT_WORD:
            emit(c, *w++);
            break;
        case OP_CW:
            /* The row's x87 control word, rounding-control field and all. It reaches
             * the host `fenv` through sa2_rt_state_load, which is the only thing
             * remill's arithmetic reads the rounding mode from. */
            c->st.x87_cw = (uint16_t)*w++;
            break;
        case OP_EMIT_CW:
            emit(c, c->st.x87_cw);
            break;
        case OP_ST: {
            uint32_t i = w[0];
            double d;
            memcpy(&d, w + 1, 8);
            w += 3;
            if (i < 8) c->st.st80[i] = sa2_f80_from_f64(d);
            break;
        }
        case OP_ST_F80: {
            /* the ten bytes as the fixture recorded them, moved and never converted */
            uint32_t i = w[0];
            sa2_f80 v;
            memset(&v, 0, sizeof v);
            memcpy(v.b, w + 1, 10);
            w += 4;                       /* index + ceil(10/4) = 3 payload words */
            if (i < 8) c->st.st80[i] = v;
            break;
        }
        case OP_ST_UNSUPPLIED: {
            uint32_t i = *w++;
            /* The row still runs, so its shape and its trap address are observable; it
             * just cannot be counted as a match. Passing on a zero the driver invented
             * is the failure mode this op exists to make impossible. */
            if (!c->unsupplied) { c->unsupplied = 1; c->unsupplied_reg = i; }
            break;
        }
        case OP_EMIT_ST: {
            uint32_t i = *w++;
            emit(c, i < 8 ? float_word(c->st.st80[i]) : 0u);
            break;
        }
        default:
            fprintf(stderr, "unknown op %u\n", op);
            return -1;
        }
    }
    return 0;
}

/* The missing-block census. No record before the G2 fold counted the addresses the corpus
 * dispatches through, which is the path 1 203 243 of its rows depend on. Two outputs: a
 * JSON file beside the row output, and one stderr line per address -- printed last, so it
 * lands in the `driver_stderr` tail `tools/s2/corpus_run/run.py` copies into the run
 * record without that file having to change. */
static void write_census(const char *out_path, uint32_t entry_va) {
    uint32_t n = sa2_mb_census_count();
    char path[1024];
    snprintf(path, sizeof path, "%s.census.json", out_path);
    FILE *f = fopen(path, "w");
    if (f) {
        fprintf(f, "{\n \"entry_va\": \"%#010x\",\n \"policy\": \"%s\",\n"
                   " \"edges_registered\": %u,\n \"dispatched_addresses\": [\n",
                entry_va, sa2_mb_policy_name(sa2_missing_block_dispatch), sa2_mb_edge_count());
        for (uint32_t i = 0; i < n; i++) {
            const sa2_mb_census_entry *e = sa2_mb_census_at(i);
            fprintf(f, "  {\"target\": \"%#010x\", \"reached_by\": \"%s\", "
                       "\"source\": \"%#010x\", \"named_exception\": %s, "
                       "\"hits\": %u, \"dispatches\": %u, \"traps\": %u}%s\n",
                    e->target, sa2_mb_edge_name(e->kind), e->source,
                    e->allow ? "true" : "false", e->hits, e->dispatches,
                    e->traps, i + 1 < n ? "," : "");
        }
        fprintf(f, " ]\n}\n");
        fclose(f);
    }
    if (!n) {
        fprintf(stderr, "  dispatch census: no missing block reached\n");
        return;
    }
    fprintf(stderr, "  dispatch census (%s):", sa2_mb_policy_name(sa2_missing_block_dispatch));
    for (uint32_t i = 0; i < n; i++) {
        const sa2_mb_census_entry *e = sa2_mb_census_at(i);
        fprintf(stderr, " %#010x<-%s@%#010x x%u%s%s", e->target, sa2_mb_edge_name(e->kind),
                e->source, e->hits, e->allow ? " [named exception]" : "",
                e->dispatches ? "" : " [trapped]");
    }
    fprintf(stderr, "\n");
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s <image.exe> <plan.bin> <out.bin> [--trace N]\n", argv[0]);
        return 2;
    }
    int trace = 0;
    for (int i = 4; i < argc - 1; i++)
        if (strcmp(argv[i], "--trace") == 0) trace = atoi(argv[i + 1]);

    plan_t p;
    if (plan_read(argv[2], &p) != 0) return 1;
    /* Whole-closure linking makes an unresolved tail call dispatchable, but only a `jmp`
     * edge is one: see runtime/remill_runtime.cpp. `SA2_MISSING_BLOCK_DISPATCH` in the
     * environment overrides this, which is how the A/B against the withdrawn
     * target-membership rule (=1) and against no dispatch at all (=0) is run.
     * fixture_driver keeps the trapping default. */
    sa2_missing_block_dispatch = SA2_MB_REACHED;
    sa2_mb_policy_from_env();
    sa2_mb_register_generated();
    sa2_guest_poison_from_env();
    {
        const char *x = getenv("SA2_X87_ENTRY");
        if (x && *x) { g_x87_entry_set = 1; g_x87_entry = strtod(x, NULL); }
    }
    if (sa2_runtime_init(argv[1], 8) != SA2_OK) return 1;

    ctx_t c;
    memset(&c, 0, sizeof c);
    c.entry_va = p.entry_va;
    c.capacity = p.out_words;
    reset_state(&c);
    if (p.global_bytes && run_ops(&c, p.global, p.global_bytes, trace) != 0) return 1;

    uint32_t words = 2 + p.out_words;
    uint32_t *out = (uint32_t *)calloc((size_t)p.rows * words + 1, 4);
    uint8_t *cur = p.rows_at;
    uint32_t traps = 0, shape_bad = 0, unsupplied_rows = 0;
    char first_note[SA2_TRAP_NOTE_MAX] = {0};
    uint32_t first_trap_row = 0;
    sa2_mb_census_reset();

    for (uint32_t r = 0; r < p.rows; r++) {
        uint32_t len;
        memcpy(&len, cur, 4);
        cur += 4;
        uint32_t *row = out + (size_t)r * words;
        c.out = row + 2;
        c.emitted = 0;
        c.status = SA2_TRAP_NONE;
        c.exit_pc = 0;
        c.res.status = SA2_TRAP_NONE;
        c.unsupplied = 0;
        c.unsupplied_reg = 0;
        reset_state(&c);
        if (run_ops(&c, cur, len, trace && (int)r < trace) != 0) return 1;
        cur += len;
        /* An unsupplied live-in outranks a clean return: the row ran, but on a value the
         * driver invented rather than one the fixture recorded, so it is not a match. */
        if (c.unsupplied && c.status == SA2_TRAP_NONE) {
            c.status = SA2_TRAP_UNSUPPLIED;
            c.exit_pc = c.unsupplied_reg;
            unsupplied_rows++;
        }
        row[0] = c.status;
        row[1] = c.exit_pc;
        if (c.emitted != p.out_words) shape_bad++;
        if (c.status != SA2_TRAP_NONE) {
            if (!traps) { first_trap_row = r; snprintf(first_note, sizeof first_note, "%s", c.res.note); }
            traps++;
        }
    }

    FILE *f = fopen(argv[3], "wb");
    if (!f) { perror(argv[3]); return 1; }
    uint32_t hdr[3] = {p.entry_va, p.rows, words};
    fwrite("SA2FXO1\0", 1, 8, f);
    fwrite(hdr, 4, 3, f);
    fwrite(out, 4, (size_t)p.rows * words, f);
    fclose(f);

    if (traps) fprintf(stderr, "  first trap at row %u: %s\n", first_trap_row, first_note);
    fprintf(stderr, "%#010x: %u rows, %u trapped, %u unsupplied, %u calls, table %u/%u, misses %u%s\n",
            p.entry_va, p.rows, traps, unsupplied_rows, c.calls, sa2_ft_count(), sa2_ft_capacity(),
            sa2_ft_misses(), shape_bad ? " [SHAPE MISMATCH]" : "");
    if (sa2_guest_poison != SA2_POISON_OFF)
        fprintf(stderr, "  poison tier (%d): %llu read(s) from a page nothing wrote, first at %#010x\n",
                sa2_guest_poison, (unsigned long long)sa2_guest_poison_reads, sa2_guest_poison_first);
    write_census(argv[3], p.entry_va);
    return 0;
}
