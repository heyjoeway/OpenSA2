/* action-buttons: the light dash moves to Y, so B and X at a line of light-dash rings somersault
 * and spin-dash as they do anywhere else; and X held starts Sonic's or Shadow's spin dash at once,
 * without the 24-frame charge, while B keeps the usual hold (runtime/sa2_tweaks.h: the tweaks and
 * their rules). The second half is the Mod Loader's Codes.lst code "Press B to
 * Somersault" (Hyper and MainMemory), made as a wrap, since OpenSA2 never runs the guest code bytes
 * that code patches.
 *
 * TWEAK_WRAPS: sub_7230e0, sub_719920
 *
 * Where, read from the unpacked image. The action flags are bytes per player, made each frame by
 * 0x00442390 from Controllers[p] (0x01DEFC00 + 0x34 * p: the held buttons at +8, the press edges at
 * +0x10): Action_Held (0x01DEFBF0 + p) when a held button is B or X (0x402), Action_Pressed
 * (0x01DEFB00 + p) when a press edge is B or X and the action was not held the frame before (A,
 * 0x4, is the jump). Y (0x200) is no action button: in Sonic's code only the masks 0x602 at
 * 0x0071C74E and 0x0071C77E (letting go of a held object) and 0x306FE at 0x0071C148 (mashing) test
 * it, and none of them reaches the light dash.
 *
 *   0x007230E0 (Sonic_CheckActionWindow, 808 bytes). Entered with EAX the EntityData1, ECX the
 *   CharObj2Base, EDX the EntityData2 and one stack argument, the SonicCharObj2 (the same object as
 *   ECX), which the caller pops; a plain `ret`. Its 22 calls are all in Sonic_ChecksForDamage
 *   (0x00719920), one in each action state that offers the action window (0x00719B44 standing,
 *   0x00719CA6, 0x00719E32, ..., 0x0071CE15), each followed by `add esp,4; test eax,eax; jne`. The
 *   window is in the CharObj2Base: the player number at +0, CharID2 at +3, the actions offered as
 *   signed bytes at +4 (count at +0x0C), the one the player picked at +0x0D, the one taken at
 *   +0x0E. With Action_Pressed clear the body returns 0 when B or X is held and otherwise, when the
 *   first action is 60, calls 0x00724070 and returns 1 (0x007230FD-0x0072313B). With it set
 *   (0x0072313C) it takes the picked action when it is in the window, else the first, stores it at
 *   +0x0E and jumps on action - 10 through the tables 0x00723468 (bytes) and 0x00723408. Of the 77
 *   entries, action 59 (the light dash) alone goes to 0x00723189: Sonic_PerformLightDash
 *   (0x00724010; EAX the SonicCharObj2, EDX the CharObj2Base, ECX the EntityData1, registers only,
 *   a plain `ret`; it sets the action byte to 59 and the animation to 0x57 and plays sound 0x2012
 *   through 0x00437260), then EAX = 1 and the `ret`. An action the tables do not list
 *   (0x007233FE) returns 0 after the store.
 *
 *   0x00719920 (Sonic_ChecksForDamage, cdecl: the EntityData1 at [ESP+4], the EntityData2 at
 *   [ESP+8], the CharObj2Base at [ESP+0xC], the SonicCharObj2 at [ESP+0x10]). Its one call is
 *   Sonic_Main's (0x00717913), which Sonic, Shadow, Amy and Metal Sonic all run. In the standing
 *   state, after the action window returns 0, it calls 0x004751D0, 0x00721C00 and
 *   Sonic_Somersault (0x00723880, 0x00719B54-0x00719B80); the other states call the somersault
 *   where they offer it. Sonic_Somersault keeps the hold in the word at SonicCharObj2+0x368: on
 *   Action_Pressed it writes 1 and returns 0 (0x00723DD5-0x00723DF5); while Action_Held it reads
 *   the word (0x00723E00) and, when it is 1 or more, adds 1 and stores it (0x00723E12), and past 24
 *   (`jle 0x00723E3C` at 0x00723E19) calls Sonic_StartSpindash (0x007234C0, action 3) when CharID2
 *   is 0 or 1 (Sonic, Shadow), then clears the word and returns 1. Up to 24 it sets bit 0x400 of
 *   the status and returns 0. A release reads the word at 0x00723E51 (above 2: the somersault at
 *   0x00723EC0). The word's other readers in the image are Sonic_Main (0x007179FA, 0x00717A6B,
 *   after Sonic_ChecksForDamage returns) and Sonic_CheckBlackShield (0x007237D5, at 0x00719B9C after the
 *   somersault returns 0, CharID2 12 alone); its writers are the somersault, the action window's
 *   arms and Sonic_ChecksForDamage itself, each storing 0 (0x007199A5 ... 0x0071B815) or the
 *   somersault's values. No wider access overlaps it in Sonic's code (0x00710000-0x00730000).
 *   Codes.lst's code writes 7E 21 (the `jle`) at 0x00723E19 while Controllers[0] holds B and two
 *   NOPs while it holds X without B, so after an X the first held frame with the word at 1 or more
 *   starts the spin dash. The code's choice outlives the frame, but only a held B or X makes the
 * action held, and it is the held B that chooses the `jle`, so the choice that counts is always the
 * frame's own: X held without B.
 *
 * The wraps. Each is a function of its own, which takes the lifted body to call as `real`, so a
 * later tweak that acts in the same function goes into this file's wrap, as ring-loss-cap acts in
 * no-2p-specials.c's: sa2_action_buttons_window for 0x007230E0 (speedup-1p acts there) and
 * sa2_action_buttons_damage for 0x00719920 (homing-consistent's bounce fix acts there). The wraps
 * call them with `__real_`.
 *
 *   sa2_action_buttons_window, before the body, with p the window's player byte (0 to 3) and the
 *   window's count 1 to 8: when Controllers[p]'s press edges hold Y and 59 is one of the actions
 *   offered, it stores 59 at +0x0E, calls the lifted Sonic_PerformLightDash with the registers
 *   0x00723189 gives it (on the live state, a sentinel return address 0x40 bytes below ESP, every
 *   register put back after, as always-recap.c calls the menu's functions) and returns 1 as the
 *   body does (EAX = 1, EIP = the return address, ESP + 4; the caller pops its argument). When
 *   Action_Pressed is set and the action the body would take is 59, it stores 59 at +0x0E and
 *   returns 0 without the body, as the body returns for an action its tables do not list; the
 *   caller then goes on to the somersault, so B or X there somersaults or starts the hold. Any
 *   other call is the body's. A Y press with 59 in the window light-dashes wherever B or X would
 *   have (the same 22 calls), so the light dash needs what it needs without the tweak: the light
 *   dash rings offering it, and the Light Shoes.
 *
 *   sa2_action_buttons_damage, before the body: when Controllers[p]'s held buttons hold X and not
 *   B (p the player byte of the CharObj2Base), for Sonic and Shadow (CharID2 0 or 1), when
 *   Action_Held is set, Action_Pressed is not, and the word at +0x368 is 1 or more, it writes 24
 *   there when the word is below it, calls the body and, when the word still holds that 24 (the
 *   somersault did not read it this frame), writes the old value back. When the somersault does
 *   read it, its add makes it 25, past 24, and it takes the path the NOPed `jle` takes:
 *   Sonic_StartSpindash, then the clear. 25 is past spin-dash-fast's 10 as well, so the two
 *   tweaks agree; that code-patch tweak's variant of 0x00723880 is untouched (the wrap is its
 *   caller's). With Action_Pressed set the somersault writes 1 and never reads the word, and with
 *   Action_Held clear it reads it at 0x00723E51 instead, where 24 would be a somersault: neither
 *   is changed. The black shield's read is CharID2 12's, which the preset never reaches. Amy and
 *   Metal Sonic (CharID2 above 1) are left alone on purpose: the NOP has no spin dash to start for
 *   them and only clears the hold, which would take Metal Sonic's black shield away.
 *
 * Off is the lifted code alone: each wrap tests the bit first and otherwise only calls `__real_`.
 *
 * The run says what the tweak did, each of the first 16 of a kind in a line of its own and then
 * one line saying later ones are not listed, prefixed "sa2 tweaks: Y light dash, X spin dash: ":
 * "player 0's Y light-dashes (actions offered 59)", "player 0's B or X at a light-dash line goes
 * on to the somersault", "player 0's held X starts the spin dash at once (hold 1)". Once each: a
 * character with no spin dash holding X, a player byte outside 0 to 3, a window count outside 0
 * to 8, and a guest read that fails, which leave the call to the body.
 *
 * Risks. Inputs and the action flow change, so replays keep it off. The prompt on screen still
 * shows the action button at a light-dash line. In 2P each player's own X counts (the code read
 * player 1's pad for both). It does not change what "held" is. The host's hold-toggle latches B
 * alone: with it on, one tap of B holds B for the somersault's charge, a tap of X stays a tap and
 * gives no instant spin dash, and while B is latched the X preset is off, since it needs X held
 * without B. No save data is read or written: the window, the flags and the hold are per-frame
 * state.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"
#include "sa2_tweaks.h"

#define AB_ACTION_PRESSED  0x01DEFB00u   /* byte per player */
#define AB_ACTION_HELD     0x01DEFBF0u   /* byte per player */
#define AB_CONTROLLERS     0x01DEFC00u   /* 0x34 bytes per player */
#define AB_PAD_STRIDE      0x34u
#define AB_PAD_ON          0x08u         /* the held buttons */
#define AB_PAD_PRESS       0x10u         /* the press edges */
#define AB_BUTTON_B        0x002u
#define AB_BUTTON_Y        0x200u
#define AB_BUTTON_X        0x400u
#define AB_LIGHT_DASH      59
#define AB_PERFORM_LIGHT_DASH 0x00724010u
#define AB_WINDOW_TAKEN    0x0Eu         /* CharObj2Base: the action taken */
#define AB_HOLD            0x368u        /* SonicCharObj2: the somersault's hold, a word */
#define AB_HOLD_PAST       24            /* 0x00723E0E's compare */
#define AB_SPIN_DASH       3             /* Sonic_StartSpindash's action */
#define AB_LISTED          16

enum { AB_SAID_DASH, AB_SAID_PASS, AB_SAID_SPIN, AB_SAID_KINDS };

static unsigned g_ab_said[AB_SAID_KINDS];
static int g_ab_window_read_said, g_ab_damage_read_said, g_ab_player_said, g_ab_count_said;
static int g_ab_char_said;

void *__real_sub_7230e0(void *state, uint32_t pc, void *memory);
void *__real_sub_719920(void *state, uint32_t pc, void *memory);
void *sub_724010(void *state, uint32_t pc, void *memory);
void *sa2_action_buttons_window(void *state, uint32_t pc, void *memory,
                                void *(*real)(void *, uint32_t, void *));
void *sa2_action_buttons_damage(void *state, uint32_t pc, void *memory,
                                void *(*real)(void *, uint32_t, void *));

/* Whether this line of a kind is listed; the 17th says later ones are not. */
static int ab_list(int kind, const char *what) {
    const unsigned n = g_ab_said[kind];
    if (n > AB_LISTED) return 0;
    g_ab_said[kind]++;
    if (n < AB_LISTED) return 1;
    fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: %d %s listed; later ones are not\n",
            AB_LISTED, what);
    return 0;
}

/* The body's `ret`: EIP = the return address at guest [ESP], ESP + 4, EAX the answer. */
static void ab_ret(void *s, uint32_t ret, uint32_t eax) {
    sa2_rt_set_gpr(s, SA2_EAX, eax);
    sa2_rt_set_gpr(s, SA2_EIP, ret);
    sa2_rt_set_gpr(s, SA2_ESP, sa2_rt_get_gpr(s, SA2_ESP) + 4u);
}

/* Sonic_PerformLightDash on the live state, as 0x00723189 calls it, with a sentinel return address
 * 0x40 bytes below ESP and every register put back after. */
static void *ab_light_dash(void *s, void *m, uint32_t sonic, uint32_t co2, uint32_t data1) {
    uint32_t saved[SA2_GPR_COUNT];
    for (int i = 0; i < SA2_GPR_COUNT; ++i) saved[i] = sa2_rt_get_gpr(s, i);
    const uint32_t sentinel = SA2_CALL_SENTINEL, base = saved[SA2_ESP] - 0x40u;
    if (sa2_poke(base, 4, &sentinel) != SA2_OK) {
        sa2_trap_note("sa2 tweaks: Y light dash, X spin dash: the guest stack at %#010x does not "
                      "take the return address for %#010x", base, AB_PERFORM_LIGHT_DASH);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, AB_PERFORM_LIGHT_DASH);
    }
    sa2_rt_set_gpr(s, SA2_EAX, sonic);
    sa2_rt_set_gpr(s, SA2_EDX, co2);
    sa2_rt_set_gpr(s, SA2_ECX, data1);
    sa2_rt_set_gpr(s, SA2_ESP, base);
    sa2_rt_set_gpr(s, SA2_EIP, AB_PERFORM_LIGHT_DASH);
    m = sub_724010(s, AB_PERFORM_LIGHT_DASH, m);
    const uint32_t eip = sa2_rt_get_gpr(s, SA2_EIP), esp = sa2_rt_get_gpr(s, SA2_ESP);
    if (eip != sentinel || esp != base + 4u) {
        sa2_trap_note("sa2 tweaks: Y light dash, X spin dash: %#010x returned to %#010x with esp "
                      "%#010x, not to the sentinel with esp %#010x", AB_PERFORM_LIGHT_DASH, eip,
                      esp, base + 4u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, eip);
    }
    for (int i = 0; i < SA2_GPR_COUNT; ++i) sa2_rt_set_gpr(s, i, saved[i]);
    return m;
}

/* The player byte, as an index of the per-player flags, or -1 (said once). */
static int ab_player(int8_t p) {
    if (p >= 0 && p <= 3) return p;
    if (!g_ab_player_said++)
        fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: a character's player byte is %d, "
                "not 0 to 3, so its buttons are left to the game\n", p);
    return -1;
}

/* 0x007230E0, Sonic_CheckActionWindow: Y light-dashes, B and X do not. */
void *sa2_action_buttons_window(void *state, uint32_t pc, void *memory,
                                void *(*real)(void *, uint32_t, void *)) {
    if (!sa2_tweak_on(SA2_TWEAK_ACTION_BUTTONS)) return real(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    const uint32_t data1 = sa2_rt_get_gpr(state, SA2_EAX), co2 = sa2_rt_get_gpr(state, SA2_ECX);
    uint32_t ret = 0, sonic = 0, press = 0;
    int8_t w[16];
    uint8_t pressed = 0;
    if (sa2_peek(esp, 4, &ret) != SA2_OK || sa2_peek(esp + 4u, 4, &sonic) != SA2_OK ||
        sa2_peek(co2, sizeof w, w) != SA2_OK) {
        if (!g_ab_window_read_said++)
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: the guest stack at 0x%08X or the "
                    "action window at 0x%08X does not read, so the action window is left to the "
                    "game\n", esp, co2);
        return real(state, pc, memory);
    }
    const int p = ab_player(w[0]);
    if (p < 0) return real(state, pc, memory);
    const int count = w[0x0C];
    if (count < 0 || count > 8) {
        if (!g_ab_count_said++)
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: an action window holds %d "
                    "actions, not 0 to 8, so it is left to the game\n", count);
        return real(state, pc, memory);
    }
    const uint32_t pad = AB_CONTROLLERS + AB_PAD_STRIDE * (uint32_t)p;
    if (sa2_peek(pad + AB_PAD_PRESS, 4, &press) != SA2_OK ||
        sa2_peek(AB_ACTION_PRESSED + (uint32_t)p, 1, &pressed) != SA2_OK) {
        if (!g_ab_window_read_said++)
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: player %d's buttons do not read, "
                    "so the action window is left to the game\n", p);
        return real(state, pc, memory);
    }
    int offered = 0, picked = 0;
    for (int i = 0; i < count; i++) {
        if (w[4 + i] == AB_LIGHT_DASH) offered = 1;
        if (w[4 + i] == w[0x0D]) picked = 1;
    }
    const int8_t taken = AB_LIGHT_DASH;
    if ((press & AB_BUTTON_Y) && offered) {
        sa2_poke(co2 + AB_WINDOW_TAKEN, 1, &taken);
        memory = ab_light_dash(state, memory, sonic, co2, data1);
        ab_ret(state, ret, 1);
        if (ab_list(AB_SAID_DASH, "light dashes")) {
            char list[8 * 5 + 1];
            int n = 0;
            for (int i = 0; i < count; i++)
                n += snprintf(list + n, sizeof list - (size_t)n, "%s%d", i ? "," : "", w[4 + i]);
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: player %d's Y light-dashes "
                    "(actions offered %s)\n", p, list);
        }
        return memory;
    }
    if (pressed && count > 0 && (picked ? w[0x0D] : w[4]) == AB_LIGHT_DASH) {
        sa2_poke(co2 + AB_WINDOW_TAKEN, 1, &taken);
        ab_ret(state, ret, 0);
        if (ab_list(AB_SAID_PASS, "presses at a light-dash line"))
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: player %d's B or X at a "
                    "light-dash line goes on to the somersault\n", p);
        return memory;
    }
    return real(state, pc, memory);
}

/* 0x00719920, Sonic_ChecksForDamage: the hold preset while X alone is held. */
void *sa2_action_buttons_damage(void *state, uint32_t pc, void *memory,
                                void *(*real)(void *, uint32_t, void *)) {
    if (!sa2_tweak_on(SA2_TWEAK_ACTION_BUTTONS)) return real(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t args[4] = {0}, on = 0;
    uint8_t co2_head[4] = {0}, held = 0, pressed = 0;
    int16_t hold = 0;
    if (sa2_peek(esp + 4u, sizeof args, args) != SA2_OK ||
        sa2_peek(args[2], sizeof co2_head, co2_head) != SA2_OK) {
        if (!g_ab_damage_read_said++)
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: the guest stack at 0x%08X or "
                    "the character does not read, so its spin dash is left to the game\n", esp);
        return real(state, pc, memory);
    }
    const int p = ab_player((int8_t)co2_head[0]);
    if (p < 0) return real(state, pc, memory);
    const uint32_t data1 = args[0], sonic = args[3];
    if (sa2_peek(AB_CONTROLLERS + AB_PAD_STRIDE * (uint32_t)p + AB_PAD_ON, 4, &on) != SA2_OK ||
        sa2_peek(AB_ACTION_HELD + (uint32_t)p, 1, &held) != SA2_OK ||
        sa2_peek(AB_ACTION_PRESSED + (uint32_t)p, 1, &pressed) != SA2_OK ||
        sa2_peek(sonic + AB_HOLD, 2, &hold) != SA2_OK) {
        if (!g_ab_damage_read_said++)
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: player %d's buttons or hold do "
                    "not read, so the spin dash is left to the game\n", p);
        return real(state, pc, memory);
    }
    if (!(on & AB_BUTTON_X) || (on & AB_BUTTON_B) || !held || pressed || hold < 1)
        return real(state, pc, memory);
    if (co2_head[3] > 1) {
        if (!g_ab_char_said++)
            fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: character %u (CharID2) has no "
                    "spin dash, so its held X is left to the game\n", co2_head[3]);
        return real(state, pc, memory);
    }
    const int16_t preset = hold < AB_HOLD_PAST ? AB_HOLD_PAST : hold;
    if (preset != hold) sa2_poke(sonic + AB_HOLD, 2, &preset);
    memory = real(state, pc, memory);
    int16_t after = 0;
    if (sa2_peek(sonic + AB_HOLD, 2, &after) == SA2_OK && after == preset) {
        if (preset != hold) sa2_poke(sonic + AB_HOLD, 2, &hold);
        return memory;
    }
    uint8_t action = 0;
    if (sa2_peek(data1, 1, &action) == SA2_OK && action == AB_SPIN_DASH &&
        ab_list(AB_SAID_SPIN, "instant spin dashes"))
        fprintf(stderr, "sa2 tweaks: Y light dash, X spin dash: player %d's held X starts the "
                "spin dash at once (hold %d)\n", p, hold);
    return memory;
}

__attribute__((used, retain))
void *__wrap_sub_7230e0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ACTION_BUTTONS)) return __real_sub_7230e0(state, pc, memory);
    return sa2_action_buttons_window(state, pc, memory, __real_sub_7230e0);
}

__attribute__((used, retain))
void *__wrap_sub_719920(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ACTION_BUTTONS)) return __real_sub_719920(state, pc, memory);
    return sa2_action_buttons_damage(state, pc, memory, __real_sub_719920);
}
