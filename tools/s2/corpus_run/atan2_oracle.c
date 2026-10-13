/* Native x87 evidence for the atan2 tail selection (design 7.4 / the fistp
 * oracle's method): run the *same* instruction sequence the CRT's
 * `__trandisp2` runs at 0x0082C064..0x0082C08D on this host's x87, and print
 * the two status words it produces.
 *
 *   echo "<st1 float bits hex> <st0 float bits hex>" | atan2_oracle [MUL]
 *   -> "<sw after fxam of st(0)> <sw after fxch + fxam> <fpatan> <angle2>"
 *
 * The control word is the 0x133F the dispatcher loads at 0x0082C059, so the
 * classification runs under exactly the guest's precision/rounding. Nothing
 * here interprets the bits; `tools/s2/corpus_run/atan2_probe.py` applies the
 * image's own xlat table and descriptor table to them. The status words carry
 * C1, the sign FXAM reports on every class, which __trandisp2 keeps in ecx
 * (`and ecx,404h` at 0x0082C0B4) and atan2's special-case tails take the
 * result's sign from (0x0085A3A5: `or cl,cl` for st(0), `or ch,ch` for st(1)).
 *
 * Two more columns, for the same operands:
 *
 *   fpatan  this host's FPATAN of st(1) over st(0), under the same 0x133F, as
 *           20 hex digits (sign and exponent, then the significand): the x87's
 *           own two-argument arc tangent, whose results on signed zeros and
 *           infinities are specified by the instruction (Intel SDM vol. 2A,
 *           FPATAN), and which is what __trandisp2's tails stand in for when an
 *           operand is not a normal number;
 *   angle2  what 0x00459260 (the vector-to-angles helper the probe drives) does
 *           with an atan2 result, run on that value: 0x00459327 `fstp dword`,
 *           `fld dword`, `fmul qword [0x00905658]` under the control word the
 *           corpus driver runs the helper under (0x007F, sa2_cpu_state_reset),
 *           then _ftol's SSE2 path at 0x008287D2 (`fstp qword`, `cvttsd2si`).
 *           MUL is the double at 0x00905658 as 16 hex digits, read from the
 *           image by the probe; without it the column prints `-`.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    unsigned a, b;
    unsigned short cw = 0x133F, cw_game = 0x007F;
    int have_mul = argc > 1;
    uint64_t mul_bits = have_mul ? strtoull(argv[1], 0, 16) : 0;
    double mul;
    memcpy(&mul, &mul_bits, 8);
    while (scanf("%x %x", &a, &b) == 2) {
        float fa, fb, f32;
        unsigned short sw0 = 0, sw1 = 0, cw_saved = 0;
        unsigned char rb[16] = {0};
        double d;
        int angle2 = 0;
        memcpy(&fa, &a, 4);
        memcpy(&fb, &b, 4);
        __asm__ volatile("fnclex\n\t"   /* each row's words carry its own flags only */
                         "fldcw %4\n\t"
                         "flds %2\n\t"   /* pushed first -> ends up in st(1) */
                         "flds %3\n\t"   /* st(0) */
                         "fxam\n\t"
                         "fnstsw %0\n\t"
                         "fxch %%st(1)\n\t"
                         "fxam\n\t"
                         "fnstsw %1\n\t"
                         "fxch %%st(1)\n\t"
                         "fstp %%st(0)\n\t"
                         "fstp %%st(0)\n\t"
                         : "=m"(sw0), "=m"(sw1)
                         : "m"(fa), "m"(fb), "m"(cw)
                         : "st", "st(1)");
        __asm__ volatile("fnstcw %0" : "=m"(cw_saved));
        __asm__ volatile("fldcw %3\n\t"
                         "flds %1\n\t"   /* st(1) = the first operand */
                         "flds %2\n\t"   /* st(0) = the second */
                         "fpatan\n\t"
                         "fstpt %0\n\t"
                         : "=m"(*(long double *)rb)
                         : "m"(fa), "m"(fb), "m"(cw)
                         : "st", "st(1)");
        printf("%04x %04x %02x%02x%016llx", sw0, sw1, rb[9], rb[8],
               (unsigned long long)*(uint64_t *)rb);
        if (have_mul) {
            __asm__ volatile("fldcw %4\n\t"
                             "fldt %2\n\t"
                             "fstps %1\n\t"      /* 0x00459327 fstp dword [esp+0Ch] */
                             "flds %1\n\t"       /* 0x0045932B fld dword [esp+0Ch]  */
                             "fmull %3\n\t"      /* 0x0045932F fmul qword [905658h] */
                             "fstpl %0\n\t"      /* 0x008287D2 fstp qword [esp]     */
                             : "=m"(d), "=m"(f32)
                             : "m"(*(long double *)rb), "m"(mul), "m"(cw_game)
                             : "st");
            __asm__ volatile("cvttsd2si %1, %0" : "=r"(angle2) : "m"(d));  /* 0x008287D5 */
            printf(" %08x", (unsigned)angle2);
        } else {
            printf(" -");
        }
        __asm__ volatile("fldcw %0" : : "m"(cw_saved));
        printf("\n");
    }
    return 0;
}
