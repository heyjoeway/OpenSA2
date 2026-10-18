/* no-wj-rain: White Jungle's rain is neither moved nor drawn, a saving of per-frame work that matters
 * on a slow device. The drops are still made when the stage loads, so the game's random numbers,
 * and with them the stage, are what they are with the tweak off (runtime/sa2_tweaks.h: the tweaks
 * and their rules).
 *
 * TWEAK_WRAPS: sub_630550, sub_6308d0
 *
 * Where the rain comes from, read from the unpacked image. White Jungle's init (0x0062E9B0, level
 * 4's first routine in the stage table at 0x00EAE5FC, "STG04") calls 0x006303C0 once per player
 * whose MainCharObj1 slot (0x01DEA6C0 + 4 * player) is set: at 0x0062ED93 and 0x0062EDB2 with 3000
 * drops (0xBB8) in one-player play, at 0x0062ED63 and 0x0062EDB2 with 300 (0x12C) when the
 * two-player byte 0x0174AFDE is set; EAX the player, the count and a slot (0x01AEDF34 for player 1,
 * 0x01AEDF38 for player 2) on the stack. 0x006303C0 makes the task "AmeExec" (the string at
 * 0x008C91B0; its source, 0x008C91B8, is ..\src\stg04_FogJungle\ef_fg_rain.c) with LoadObject
 * (0x0046F680), allocates its data (task+0x34, player at +3) and its drop array (task+0x38, 12
 * bytes a drop, rounded down to a multiple of 5), places every drop with 0x005EE060, and sets the
 * task's main (+0x10, 0x00630550), its display (+0x24, 0x006308D0) and its delete (+0x18,
 * 0x00630A60). 0x005EE060 picks a point in a ball by rejection, three calls of rand (0x007A89D8) a
 * try, so the load draws at least 9000 random numbers in one-player play. That creation is why
 * this tweak lets the task be made: without it, every rand after the load would be another.
 *
 * The two routines skipped, each reached only through the task (their one reference each is the
 * store above, and LoadObject's argument for the main; the task runners call [task+off] with the
 * task pushed, pop it themselves, and read no register after the call, as at 0x0046F98E):
 *
 *   0x00630550  AmeExec, the main. If its player's MainCharObj1 slot is empty it deletes its own
 *               task (0x0046F720) and returns. Otherwise it reads the camera and player position
 *               (0x005EDFA0, which only reads 0x01DD92B0 and MainCharObj1 and writes its output),
 *               keeps it in its own data (+0x14..+0x1C), and, unless its data's first byte is set
 *               or the player moved too far (njScalor 0x0077FC30), moves each drop down (not while
 *               TimeStopped, 0x0174AFF7, is set) and wraps it back around the player. It calls no
 *               rand and writes nothing but its own data, its drops and its stack.
 *   0x006308D0  the display. It draws the drops for its own player's screen (0x01DD92A0), turning
 *               the fog off (0x025EFFCC = 0, 0x0042A870) and back on, pushing and popping a matrix
 *               (0x01A557FC), and setting fields of the sprite state at [0x02670544] for its draws.
 *               It leaves the sprite state's +0x0C with its top bits set to 0x94 and +0x20 at
 *               0x00E99210. Skipped, it leaves them as the drawer before it did; in the White
 *               Jungle frames dumped with the tweak on and off, every draw but the rain's (its
 *               triangle strips, one texture) is the off run's, in order, with the same shaders,
 *               texture, viewport and vertices, and the pictures differ only where drops were.
 *
 * Both are cdecl task callbacks ending in a plain `ret`, so each wrap with the tweak on is the RET
 * alone (EIP = [ESP], ESP + 4, every other register as the caller left it). The main's one case
 * with an effect outside the rain, its own delete when its player is gone, is left to the lifted
 * body: the wrap reads the task (guest [ESP + 4]), its data and that slot first, and when the slot
 * is empty calls __real_, which deletes the task as it does with the tweak off. The delete routine
 * is not wrapped, so the stage's end frees the drops as always.
 *
 * Off is the lifted code alone: each wrap's first statement tests the tweak's bit and otherwise
 * calls its __real_ and returns, touching no register and no guest byte.
 *
 * What it does not change: the fog (the stage's fog task, 0x006DFF00, and stg04_fog.bin), the
 * creation and its random numbers, the stage, and the save, which the tweak never writes. The
 * drops stay in memory, frozen where they were placed, and are freed at the stage's end.
 *
 * Risks: if AmeExec or its display had an effect this reading missed, skipping it would hide it;
 * the White Jungle runs on and off (the drops frozen, every rand and the player's path the same,
 * the frames the same but for the rain) are the check. Two-player White Jungle (300 drops a
 * player, a display per screen) was read but not run. When guest memory the wrap needs does not
 * read (the stack, the task's data, the slot), the call goes through, the rain moved or drawn, and
 * the run says so once.
 *
 * Its lines, each said once: "sa2 tweaks: wj rain off: rain not moved (AmeExec 0x00630550 skipped)",
 * "sa2 tweaks: wj rain off: rain not drawn (display 0x006308D0 skipped)", and "sa2 tweaks: wj rain
 * off: player N's rain task deletes itself (its player is gone)" the first time the main is left to
 * do that.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define WJ_RAIN_MAIN_CHAR_OBJ1 0x01DEA6C0u   /* MainCharObj1[2], each an EntityData1 pointer */
#define WJ_RAIN_TASK_DATA      0x34u         /* task+0x34: the rain's data; its player at +3 */
#define WJ_RAIN_DATA_PLAYER    0x03u

static int g_wj_rain_exec_said, g_wj_rain_display_said, g_wj_rain_gone_said;
static int g_wj_rain_stack_said, g_wj_rain_task_said;

/* The return address the lifted CALL pushed, at guest [ESP]; 0 (and said once) when it does not
 * read, so the call goes through. */
static uint32_t wj_rain_site(void *state, const char *what) {
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) == SA2_OK) return site;
    if (!g_wj_rain_stack_said++)
        fprintf(stderr, "sa2 tweaks: wj rain off: the guest stack at 0x%08X does not read, so %s "
                "goes through\n", esp, what);
    return 0;
}

/* The RET alone: EIP = the return address, ESP + 4, every other register as the caller left it. */
static void *wj_rain_skip(void *state, uint32_t site, void *memory) {
    sa2_rt_set_gpr(state, SA2_ESP, sa2_rt_get_gpr(state, SA2_ESP) + 4u);
    sa2_rt_set_gpr(state, SA2_EIP, site);
    return memory;
}

void *__real_sub_630550(void *state, uint32_t pc, void *memory);
void *__real_sub_6308d0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_630550(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_WJ_RAIN)) return __real_sub_630550(state, pc, memory);
    const uint32_t site = wj_rain_site(state, "the rain's main");
    if (!site) return __real_sub_630550(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t task = 0, data = 0, slot = 0;
    uint8_t player = 0;
    if (sa2_peek(esp + 4u, 4, &task) != SA2_OK
        || sa2_peek(task + WJ_RAIN_TASK_DATA, 4, &data) != SA2_OK
        || sa2_peek(data + WJ_RAIN_DATA_PLAYER, 1, &player) != SA2_OK
        || sa2_peek(WJ_RAIN_MAIN_CHAR_OBJ1 + 4u * player, 4, &slot) != SA2_OK) {
        if (!g_wj_rain_task_said++)
            fprintf(stderr, "sa2 tweaks: wj rain off: the rain task 0x%08X, its data or its "
                    "player's slot does not read, so the rain's main goes through\n", task);
        return __real_sub_630550(state, pc, memory);
    }
    if (!slot) {
        if (!g_wj_rain_gone_said++)
            fprintf(stderr, "sa2 tweaks: wj rain off: player %u's rain task deletes itself (its "
                    "player is gone)\n", player + 1u);
        return __real_sub_630550(state, pc, memory);
    }
    if (!g_wj_rain_exec_said++)
        fprintf(stderr, "sa2 tweaks: wj rain off: rain not moved (AmeExec 0x00630550 skipped)\n");
    return wj_rain_skip(state, site, memory);
}

__attribute__((used, retain))
void *__wrap_sub_6308d0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_WJ_RAIN)) return __real_sub_6308d0(state, pc, memory);
    const uint32_t site = wj_rain_site(state, "the rain's display");
    if (!site) return __real_sub_6308d0(state, pc, memory);
    if (!g_wj_rain_display_said++)
        fprintf(stderr, "sa2 tweaks: wj rain off: rain not drawn (display 0x006308D0 skipped)\n");
    return wj_rain_skip(state, site, memory);
}
