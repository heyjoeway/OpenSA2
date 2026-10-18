/* chao-stat-x5: Chao gain up to five times the stat points from a Chaos Drive, a small animal or a
 * fruit, after the Mod Loader's Codes.lst "Chao Stat Multiplier" but capped so that no gain wraps a
 * stat's points byte (runtime/sa2_tweaks.h: the tweaks and their rules). The points are the game's
 * own, so they are kept in the Chao save.
 *
 * TWEAK_WRAPS: sub_535f00
 *
 * Where the points come from, read from the unpacked image. 0x00535F00 (unnamed) adds an item's
 * stat points to a Chao: EAX the Chao's object, DX the item's index, its first stack word the kind.
 * Kind 2 takes row DX of table A at 0x008A6240, any other kind row DX of table B at 0x008A6448
 * (`lea esi,[idx*20+base]`), 20 bytes a row. It reads the row's eight signed 16-bit values at
 * row + 4, one for each of the Chao's eight stats (kind 3 takes a quarter of each, plus the
 * remainder when its second stack word is set), and adds each to the Chao's byte of points at
 * [[EAX+0x34]+0x5C] + 0x20 + i (0x00535F81-0x00535F93: `movzx edx,cl; add edx,eax; jns` clamps a
 * negative sum at 0, and `add cl,al` stores the low 8 bits of a positive one). Then, once a call
 * (0x00535FA1-0x00535FD6), a byte above 99 becomes a level and loses 100, or is set to 99 at level
 * 99. Its two callers, the only calls to 0x00535F00 in .text:
 * 0x005674C4, kind 2 (table A: the 21 small animals, then the four Chaos Drives, the rows Codes.lst
 * describes as "each Chaos Drive and Minimal"), and 0x00545848, kind 3 (table B, the fruit). The
 * two LEAs in 0x00535F00 are the only references to the tables in the image (every dword of .text,
 * .rdata and .dataa from 0x008A6200 to 0x008A6680 was read; 0x007B4C27 is the middle of an
 * instruction), and 0x005523D0 refers to 0x008A6628, where table B ends.
 *
 * The Codes.lst line, `muls16 8A6240 5 x500`, a "Patch" (applied once at the start), multiplies
 * the 500 int16 values at 0x008A6240-0x008A6627 (both tables) by 5, each kept to 16 bits. The
 * tables are in .rdata (0x00873000-0x00916105), and the lifted body reads them through the guest
 * memory (__remill_read_memory_16), so a write there is what the game reads. The wrap writes the
 * tables once, the first time a Chao gets points with the tweak on, before the lifted body reads
 * the row: it reads the 1000 bytes, checks their sha256 against the one recorded from the unpacked
 * image (the shipped executable's are the same), scales the stat values and writes them back.
 *
 * Where it departs from Codes.lst. Five times a value is often more than a byte holds: a Chaos
 * Drive's 24 becomes 120, its 40 becomes 200, and an animal's 60 becomes 300, which adds 44. With
 * Codes.lst a byte at 99 given a drive's 120 three times is 219 (a level, 119 kept), 239 (a level,
 * 139 kept), then 259, stored as 3 with no level: the Chao loses 136 points, and the save keeps the
 * loss. So each positive stat value is five times its own but at most what one call can add
 * without that: 100 in table A, whose value a call adds whole (a byte of at most 99 plus at most
 * 100 is at most 199, which the one level-up brings back to at most 99), and 388 in table B, of
 * which a call adds a quarter, plus a remainder of at most 3 (97 + 3). A negative value is five
 * times its own, as with Codes.lst; the sign test clamps the sum at 0. A gain is then never less
 * than with the tweak off, and a byte never wraps or keeps more than 99. Row 0 of table A,
 * (1, 0, 36, 12, 8, -24, 0, 20, 20, 0), becomes (1, 0, 100, 60, 40, -120, 0, 100, 100, 0); a drive
 * gives 100 to its stat and to stats 5 and 6, one level of each. The two values before
 * row + 4 of each row, which nothing in the image reads, and row 25 of table A at 0x008A6434
 * (14, 14, 7, 7, 0, 0, 256, 256, 6, 0), past the 25 items the kind-2 caller passes and of no known
 * use, are left as they are.
 *
 * Off is identical: the first line of the wrap asks sa2_tweak_on and, when the tweak is off (or a
 * replay is set), only calls `__real_` and returns; the tables are never read or written.
 *
 * Risks. The extra points are written to the Chao save, as Codes.lst's are: a Chao raised with the
 * tweak keeps its stats with the tweak off, and races and karate become uneven. Bytes that are not
 * the recorded ones (another executable, or something that wrote the table first, such as
 * Codes.lst's own line) are left alone, and so is a table that does not read or write; each case
 * is said once by name and the tweak does nothing.
 *
 * The first time it acts, the run says what it did once: "sa2 tweaks: chao stats x5: the stat
 * tables at 0x008A6240 multiplied by 5, at most 100 a stat for an animal or a drive and 388 for a
 * fruit", or "sa2 tweaks: chao stats x5: the table at 0x008A6240 is not the expected bytes (sha256
 * ...); not applied".
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "guest_memory.h"
#include "sa2_tweaks.h"

#define CHAO_STAT_TABLE  0x008A6240u   /* table A; table B follows at 0x008A6448 */
#define CHAO_STAT_VALUES 500u          /* int16 values: Codes.lst's x500, 10 a row */
#define CHAO_STAT_ROWS_A 25u           /* table A rows 0-24: 21 animals, 4 drives; row 25 left */
#define CHAO_STAT_ROW_B  26u           /* table B (the fruit): rows 26-49 of the 500 values */
#define CHAO_STAT_FACTOR 5
#define CHAO_STAT_CAP_A  100           /* a call adds the value whole */
#define CHAO_STAT_CAP_B  388           /* a call adds a quarter (97), plus a remainder of at most 3 */

/* sha256 of the 1000 bytes at 0x008A6240 in the unpacked image (and the shipped one). */
static const char CHAO_STAT_SHA256[] =
    "76814b328e66a73366b22ff2548ed2cfd29fbe32e6fce7ed6b041fa9669e1725";

enum { CHAO_STAT_UNTRIED, CHAO_STAT_APPLIED, CHAO_STAT_LEFT };
static int g_chao_stat;

static uint32_t chao_ror(uint32_t x, unsigned r) { return (x >> r) | (x << (32u - r)); }

static void chao_sha256_hex(const uint8_t *p, size_t n, char out[65]) {
    static const uint32_t K[64] = {
        0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u,
        0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu,
        0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu,
        0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau, 0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
        0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu,
        0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
        0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u, 0x19a4c116u,
        0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
        0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u,
        0xc67178f2u,
    };
    uint32_t h[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                     0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
    const size_t blocks = (n + 9 + 63) / 64;
    for (size_t b = 0; b < blocks; b++) {
        uint8_t blk[64];
        for (size_t i = 0; i < 64; i++) {
            const size_t at = b * 64 + i;
            blk[i] = at < n ? p[at] : at == n ? 0x80 : 0;
        }
        if (b == blocks - 1)
            for (int i = 0; i < 8; i++) blk[63 - i] = (uint8_t)((uint64_t)n * 8 >> (8 * i));
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t)blk[4 * i] << 24 | (uint32_t)blk[4 * i + 1] << 16 |
                   (uint32_t)blk[4 * i + 2] << 8 | blk[4 * i + 3];
        for (int i = 16; i < 64; i++)
            w[i] = w[i - 16] + (chao_ror(w[i - 15], 7) ^ chao_ror(w[i - 15], 18) ^ (w[i - 15] >> 3)) +
                   w[i - 7] + (chao_ror(w[i - 2], 17) ^ chao_ror(w[i - 2], 19) ^ (w[i - 2] >> 10));
        uint32_t a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
        for (int i = 0; i < 64; i++) {
            const uint32_t t1 = hh + (chao_ror(e, 6) ^ chao_ror(e, 11) ^ chao_ror(e, 25)) +
                                ((e & f) ^ (~e & g)) + K[i] + w[i];
            const uint32_t t2 = (chao_ror(a, 2) ^ chao_ror(a, 13) ^ chao_ror(a, 22)) +
                                ((a & bb) ^ (a & c) ^ (bb & c));
            hh = g, g = f, f = e, e = d + t1, d = c, c = bb, bb = a, a = t1 + t2;
        }
        h[0] += a, h[1] += bb, h[2] += c, h[3] += d, h[4] += e, h[5] += f, h[6] += g, h[7] += hh;
    }
    for (int i = 0; i < 8; i++) snprintf(out + 8 * i, 9, "%08x", h[i]);
}

/* Read, check, scale and write the tables once: CHAO_STAT_APPLIED, or CHAO_STAT_LEFT after saying
 * why. */
static int chao_stat_apply(void) {
    uint8_t t[CHAO_STAT_VALUES * 2];
    if (sa2_peek(CHAO_STAT_TABLE, sizeof t, t) != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: chao stats x5: the table at 0x%08X does not read; not applied\n",
                CHAO_STAT_TABLE);
        return CHAO_STAT_LEFT;
    }
    char hex[65];
    chao_sha256_hex(t, sizeof t, hex);
    if (strcmp(hex, CHAO_STAT_SHA256) != 0) {
        fprintf(stderr, "sa2 tweaks: chao stats x5: the table at 0x%08X is not the expected bytes "
                "(sha256 %s); not applied\n", CHAO_STAT_TABLE, hex);
        return CHAO_STAT_LEFT;
    }
    for (unsigned i = 0; i < CHAO_STAT_VALUES; i++) {
        const unsigned row = i / 10, col = i % 10;
        if (col < 2 || (row >= CHAO_STAT_ROWS_A && row < CHAO_STAT_ROW_B)) continue;
        const int32_t cap = row < CHAO_STAT_ROWS_A ? CHAO_STAT_CAP_A : CHAO_STAT_CAP_B;
        int32_t x = (int16_t)(t[2 * i] | t[2 * i + 1] << 8) * CHAO_STAT_FACTOR;
        if (x > cap) x = cap;
        t[2 * i] = (uint8_t)x, t[2 * i + 1] = (uint8_t)((uint32_t)x >> 8);
    }
    if (sa2_poke(CHAO_STAT_TABLE, sizeof t, t) != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: chao stats x5: the table at 0x%08X does not write; not applied\n",
                CHAO_STAT_TABLE);
        return CHAO_STAT_LEFT;
    }
    fprintf(stderr, "sa2 tweaks: chao stats x5: the stat tables at 0x%08X multiplied by %d, at most %d a "
            "stat for an animal or a drive and %d for a fruit\n", CHAO_STAT_TABLE, CHAO_STAT_FACTOR,
            CHAO_STAT_CAP_A, CHAO_STAT_CAP_B);
    return CHAO_STAT_APPLIED;
}

void *__real_sub_535f00(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_535f00(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_CHAO_STAT_X5)) return __real_sub_535f00(state, pc, memory);
    if (g_chao_stat == CHAO_STAT_UNTRIED) g_chao_stat = chao_stat_apply();
    return __real_sub_535f00(state, pc, memory);
}
