/* drives-to-minimals: an enemy that would leave a Chaos Drive when destroyed leaves one small
 * animal (a minimal) in its place, as the Codes.lst code "Replace Chaos Drives with Minimals" does
 * (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_47abb0
 *
 * Where the drive comes from, read from the unpacked image. 0x0047ABB0 (EnemySetDead, 581 bytes)
 * runs when an enemy is destroyed: EAX is the enemy's task, and its one stack argument, p (guest
 * [ESP + 4] on entry, read into EBP at 0x0047ABB5), points to the enemy's data, whose byte
 * [p + 0x4C] holds its drop flags. 18 lifted callers (0x004F85B5 ... 0x007A1950) link against it
 * directly and no data in the image points at it, so the wrap sees every call. The drops, at the
 * body's end:
 *
 *   0x0047ADAD  cmp byte [0x0174AFDE], 0; jne 0x0047ADED   TwoPlayerMode: a 2P game drops nothing
 *   0x0047ADB6  test byte [p+0x4C], 0x10; je 0x0047ADC5
 *   0x0047ADC0  call 0x0048F8E0   ChaosDrive_Load (EBX = the enemy's position)
 *   0x0047ADC5  test byte [p+0x4C], 0x08; je 0x0047ADED
 *   0x0047ADE5  call 0x0048ADA0   one animal at the position (cdecl x, y, z)
 *
 * Codes.lst's patch writes a jump at 0x0047ADBC to 0x0047ADCB: with flag 0x10 the drive's call is
 * skipped and the animal's is made, once, whatever flag 0x08 says. OpenSA2 never runs the guest's
 * code bytes, so the wrap gets the same result through the flags: when [p + 0x4C] has 0x10, it
 * writes (flags & ~0x10) | 0x08 there, calls the lifted body, and then puts back the original 0x10
 * and 0x08 bits alone. The body tests those bits in one other place, `test [p+0x4C], 0x1C` at
 * 0x0047AC98 (whether anything drops at all), whose answer the rewrite keeps (0x10 set before,
 * 0x08 set during); it writes the byte in one place, `and word [p+0x4C], 0xFFDF` at 0x0047AC8A,
 * clearing bit 0x20, which the restore leaves as the body left it. The calls it makes in between
 * do not read p's flags; 0x006B80B0 (called with p when flag 0x04 asks for an effect) reads only
 * [p], [p+4] and [p+8]. 0x0048F8E0 and 0x0048ADA0 are called from nowhere else in the image. In a
 * 2P game the body skips both calls, so the rewrite changes nothing there; the title screen's demo
 * runs with TwoPlayerMode set too (seen in runs left at the title), and drops nothing either.
 *
 * Off is the lifted body alone: the wrap tests its bit first and only calls __real_sub_47abb0.
 * On, an enemy without flag 0x10 is also left to the lifted body untouched.
 *
 * The risks. Objects spawned and the game's random numbers change (a drive draws one random number
 * at 0x0048F92F, an animal its own), so the tweak is play only, as every tweak is. A player farming
 * Chaos Drives for their Chao gets animals instead; the save holds whatever the player then gives
 * their Chao, and the tweak writes nothing to it. If the enemy's data does not read, or its flags
 * cannot be written, the enemy keeps its drive and the run says so once.
 *
 * The first time an enemy's drive becomes an animal, the run says so: "sa2 tweaks: drives become
 * minimals: an enemy left an animal in place of its Chaos Drive (flags 0x10, made 0x08 for the
 * call)", and once, the first time it meets an enemy with flag 0x10 while TwoPlayerMode is set,
 * that nothing drops there.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define DRIVES_FLAGS_AT     0x4Cu         /* the drop flags' byte in the enemy's data */
#define DRIVES_DRIVE        0x10u         /* drops a Chaos Drive (ChaosDrive_Load, 0x0048F8E0) */
#define DRIVES_ANIMAL       0x08u         /* drops an animal (0x0048ADA0) */
#define DRIVES_TWO_PLAYER   0x0174AFDEu   /* TwoPlayerMode, read by the body at 0x0047ADAD */

static int g_drives_said, g_drives_two_player_said, g_drives_fail_said;

static void drives_fail(const char *what, uint32_t va, const char *verb) {
    if (g_drives_fail_said++) return;
    fprintf(stderr, "sa2 tweaks: drives become minimals: %s at 0x%08X does not %s, so the enemy "
            "keeps its Chaos Drive\n", what, va, verb);
}

void *__real_sub_47abb0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_47abb0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_DRIVES_TO_MINIMALS)) return __real_sub_47abb0(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t data = 0;
    if (sa2_peek(esp + 4u, 4, &data) != SA2_OK) {
        drives_fail("the guest stack", esp + 4u, "read");
        return __real_sub_47abb0(state, pc, memory);
    }
    const uint32_t at = data + DRIVES_FLAGS_AT;
    uint8_t flags = 0;
    if (sa2_peek(at, 1, &flags) != SA2_OK) {
        drives_fail("the enemy's drop flags", at, "read");
        return __real_sub_47abb0(state, pc, memory);
    }
    if (!(flags & DRIVES_DRIVE)) return __real_sub_47abb0(state, pc, memory);
    const uint8_t during = (uint8_t)((flags & ~DRIVES_DRIVE) | DRIVES_ANIMAL);
    if (sa2_poke(at, 1, &during) != SA2_OK) {
        drives_fail("the enemy's drop flags", at, "take a write");
        return __real_sub_47abb0(state, pc, memory);
    }
    void *ret = __real_sub_47abb0(state, pc, memory);
    const uint8_t bits = DRIVES_DRIVE | DRIVES_ANIMAL;
    uint8_t after = 0;
    int restored = sa2_peek(at, 1, &after) == SA2_OK;
    after = (uint8_t)((after & ~bits) | (flags & bits));
    if (restored) restored = sa2_poke(at, 1, &after) == SA2_OK;
    if (!restored)
        fprintf(stderr, "sa2 tweaks: drives become minimals: the enemy's drop flags at 0x%08X could "
                "not be put back to 0x%02X after the call\n", at, flags);
    uint8_t two_player = 0;
    if (sa2_peek(DRIVES_TWO_PLAYER, 1, &two_player) == SA2_OK && two_player) {
        if (!g_drives_two_player_said++)
            fprintf(stderr, "sa2 tweaks: drives become minimals: TwoPlayerMode is %u (a 2P game, or "
                    "the title screen's demo), where no enemy drops a drive or an animal, so there is "
                    "nothing to change\n", two_player);
    } else if (!g_drives_said++) {
        fprintf(stderr, "sa2 tweaks: drives become minimals: an enemy left an animal in place of its "
                "Chaos Drive (flags 0x%02X, made 0x%02X for the call)\n", flags, during);
    }
    return ret;
}
