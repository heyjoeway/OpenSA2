/* Harness level 2, lifted side: the Mod Loader's test spawn, reproduced.
 *
 * Documentation/decisions/2026-09-23-S4-stage-route.md: a stage replay starts
 * natively through the Mod Loader's `--level <name> --character <name>`
 * (SA2ModLoader/testspawn.cpp, TestSpawnCheckArgs), and the lifted build must
 * get the same effects. The lifted build never runs guest code bytes, so the
 * loader's code patches are reproduced by link-time wraps, and its data writes
 * and calls happen where the loader makes them: in its init, which runs when
 * WinMain's data-DLL loader (0x0077DD10) returns.
 *
 *   sub_77dd10  after the real body: CurrentLevel (int16 at 0x01934B70) and
 *               CurrentCharacter (0x01934B80) set, ChaoSaveIndexThing
 *               (0x0173D06C) cleared, LoadTipsTexs (0x00427EF0, esi = the
 *               TextLanguage byte at 0x0174AFD1) called, and LoadMenuButtonsTex
 *               done as the loader does it -- the same path string built, LoadPNG
 *               (0x0041D490, fastcall, ecx = the path) called, and its result
 *               stored in MenuButtonImage (0x01A55868).
 *   sub_434160  the imm32 at 0x0043459A, `mov [GameMode],15h` in case 13, made 4:
 *               when GameMode was 13 on entry and is 0x15 on exit, 4 is written.
 *               Only 0x00434594 (case 13) and 0x004353EC store 0x15 to GameMode,
 *               and nothing between the store and the return reads it. The runtime
 *               wraps sub_434160 in every build (runtime/tweaks/skip-intro.c, the
 *               skip-intro tweak), so this rule is sa2_harness_gamemode_after, which
 *               that wrap calls after the lifted body with GameMode before and after
 *               it; the runtime's own definition is a weak no-op.
 *   sub_4436a0  `ret` at the entry (the loader's "prevent the game to save").
 *   sub_426760  the same.
 *
 * The two NOP patches (0x0043C983, 0x004395F3) and the LifeIcon hook change
 * nothing for level 10 with Sonic and are not reproduced; the decision record
 * says why.
 *
 * SA2_TEST_SPAWN_LEVEL=<id> turns it on, SA2_TEST_SPAWN_CHARACTER=<id> (default
 * 0) chooses the character. Unset, every wrap passes through.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"

typedef void *(*lifted_fn)(void *state, uint32_t pc, void *memory);

void *__real_sub_77dd10(void *s, uint32_t pc, void *m);
void *__real_sub_4436a0(void *s, uint32_t pc, void *m);
void *__real_sub_426760(void *s, uint32_t pc, void *m);
void *sub_427ef0(void *s, uint32_t pc, void *m);
void *sub_41d490(void *s, uint32_t pc, void *m);

#define CURRENT_LEVEL      0x01934B70u
#define CURRENT_CHARACTER  0x01934B80u
#define CHAO_SAVE_INDEX    0x0173D06Cu
#define TEXT_LANGUAGE      0x0174AFD1u
#define PAD_OR_KEYBOARD    0x0174B5FCu
#define MENU_BUTTON_IMAGE  0x01A55868u
#define GAME_MODE          0x0174B040u

static int spawn_level = -2;           /* -2 unread, -1 off */
static uint32_t spawn_character;

static int level(void) {
    if (spawn_level == -2) {
        const char *l = getenv("SA2_TEST_SPAWN_LEVEL");
        const char *c = getenv("SA2_TEST_SPAWN_CHARACTER");
        spawn_level = l && *l ? (int)strtol(l, NULL, 0) : -1;
        spawn_character = c && *c ? (uint32_t)strtoul(c, NULL, 0) : 0u;
    }
    return spawn_level;
}

static uint32_t peek32(uint32_t va) {
    uint32_t v = 0;
    sa2_peek(va, 4, &v);
    return v;
}

/* Call a lifted function the way `call` would, from inside a wrap: a sentinel
 * return address pushed on the guest stack, the function run to its `ret`, and
 * every general register put back afterwards, so the code the wrap returns to
 * sees the registers it would have seen had the loader's init not run. Both
 * callees end in a plain `ret`, so a return anywhere but the sentinel, or with
 * the stack anywhere but just above it, is not a return and stops the run. */
static void *guest_call(void *s, void *m, lifted_fn fn, uint32_t va, uint32_t esp) {
    uint32_t saved[SA2_GPR_COUNT];
    for (int i = 0; i < SA2_GPR_COUNT; ++i) saved[i] = sa2_rt_get_gpr(s, i);
    const uint32_t sentinel = SA2_CALL_SENTINEL;
    esp -= 4u;
    sa2_poke(esp, 4, &sentinel);
    sa2_rt_set_gpr(s, SA2_ESP, esp);
    sa2_rt_set_gpr(s, SA2_EIP, va);
    m = fn(s, va, m);
    const uint32_t eip_after = sa2_rt_get_gpr(s, SA2_EIP), esp_after = sa2_rt_get_gpr(s, SA2_ESP);
    if (eip_after != sentinel || esp_after != esp + 4u) {
        sa2_trap_note("test spawn: %#010x returned to %#010x with esp %#010x, not to the "
                      "sentinel with esp %#010x", va, eip_after, esp_after, esp + 4u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, eip_after);
    }
    const uint32_t eax = sa2_rt_get_gpr(s, SA2_EAX);
    for (int i = 0; i < SA2_GPR_COUNT; ++i) sa2_rt_set_gpr(s, i, saved[i]);
    sa2_rt_set_gpr(s, SA2_EAX, eax);          /* the callee's result, read by the caller */
    return m;
}

void *__wrap_sub_77dd10(void *s, uint32_t pc, void *m) {
    m = __real_sub_77dd10(s, pc, m);
    if (level() < 0) return m;
    const uint32_t eax = sa2_rt_get_gpr(s, SA2_EAX);  /* the data DLL's result */
    const uint16_t lvl = (uint16_t)spawn_level;
    sa2_poke(CURRENT_LEVEL, 2, &lvl);

    uint8_t lang = 0;
    sa2_peek(TEXT_LANGUAGE, 1, &lang);
    /* LoadTipsTexs(TextLanguage): the language in esi. */
    const uint32_t esi = sa2_rt_get_gpr(s, SA2_ESI);
    sa2_rt_set_gpr(s, SA2_ESI, lang);
    m = guest_call(s, m, sub_427ef0, 0x00427EF0u, sa2_rt_get_gpr(s, SA2_ESP));
    sa2_rt_set_gpr(s, SA2_ESI, esi);

    /* LoadMenuButtonsTex: the loader's sprintf, into a buffer below the stack. */
    char path[40];
    snprintf(path, sizeof path, peek32(PAD_OR_KEYBOARD)
             ? "\\SOC\\menuButton%d\\menu_button_%d.png"
             : "\\SOC\\menuButtonK%d\\menu_button_%d.png", lang, lang);
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    const uint32_t buf = (esp - 0x100u) & ~0xFu;
    sa2_poke(buf, (uint32_t)strlen(path) + 1u, path);
    const uint32_t ecx = sa2_rt_get_gpr(s, SA2_ECX);
    sa2_rt_set_gpr(s, SA2_ECX, buf);
    m = guest_call(s, m, sub_41d490, 0x0041D490u, buf - 0x10u);
    const uint32_t image = sa2_rt_get_gpr(s, SA2_EAX);
    sa2_poke(MENU_BUTTON_IMAGE, 4, &image);
    sa2_rt_set_gpr(s, SA2_ECX, ecx);

    const uint32_t zero = 0;
    sa2_poke(CHAO_SAVE_INDEX, 4, &zero);
    sa2_poke(CURRENT_CHARACTER, 4, &spawn_character);
    sa2_rt_set_gpr(s, SA2_EAX, eax);
    return m;
}

/* GameModeHandler (sub_434160) has run: GameMode was `before` and is `after`. Called by the
 * runtime's wrap of it (runtime/tweaks/skip-intro.c) in every build that links this file. */
void sa2_harness_gamemode_after(uint32_t before, uint32_t after) {
    if (level() >= 0 && before == 13u && after == 0x15u) {
        const uint32_t start_level = 4u;
        sa2_poke(GAME_MODE, 4, &start_level);
    }
}

/* `ret` at the entry: the return address popped into EIP, nothing else. */
static void *bare_ret(void *s, void *m) {
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    sa2_rt_set_gpr(s, SA2_EIP, peek32(esp));
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u);
    return m;
}

void *__wrap_sub_4436a0(void *s, uint32_t pc, void *m) {
    return level() >= 0 ? bare_ret(s, m) : __real_sub_4436a0(s, pc, m);
}

void *__wrap_sub_426760(void *s, uint32_t pc, void *m) {
    return level() >= 0 ? bare_ret(s, m) : __real_sub_426760(s, pc, m);
}
