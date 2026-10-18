/* modloader-default-fixes: two of the fixes the SA2 Mod Loader turns on by default, made as wraps
 * because OpenSA2 never runs the image's code bytes (runtime/sa2_tweaks.h: the tweaks and their
 * rules). ScreenFadeFix (MainMemory): the white fade every stage's victory screen runs down to
 * nothing now ends at nothing, where the game's own fade stops short and flickers between two
 * faint values for the rest of the pose. EnvMapFix (Exant): a Chao World model
 * drawn through chDrawCnk sets the environment map's matrix first, where the game left it as the
 * last model to set it had. The loader's other default patches are not here: CECarFix and
 * ParticlesFix answer driver faults OpenSA2's renderer does not have, SyncLoad is the loader's own
 * threading, ExtendVertexBuffer is for modded models, and FramerateLimiter and DisableExitPrompt
 * are the player's and the host's (DisableExitPrompt: "Asked for, and already so" in sa2_tweaks.h).
 *
 * TWEAK_WRAPS: sub_478730, sub_56ddd0
 *
 * The reading of the unpacked image, and of SA2ModLoader/patches.cpp, which makes both fixes.
 *
 * ScreenFade (0x00478730, 94 bytes, register arguments): EDX is the alpha to fade towards; the
 * alpha is the byte at 0x0171CDA3, the top byte of ScreenFadeARGB (0x0171CDA0), a screen overlay
 * LoadResultScreenObjects (0x00451450) starts at 0x00FFFFFF, white and clear (0x00451480). The step is ECX = 6, or 5
 * when the byte at 0x0174AFCF is not 2 or CurrentObjectSub (0x01A5A274) is not 0. It returns
 * EAX = 1 when the alpha is the target already, else steps the alpha and returns 0:
 *
 *   0x00478753  cmp eax,edx / jne        ; a == t: return 1
 *   0x0047875E  lea esi,[ecx+edx]        ; a > t + step (jbe, unsigned): a -= step
 *   0x00478770  mov esi,edx / sub esi,ecx; a < t - step (jae, unsigned): a += step
 *   0x00478783  mov eax,edx              ; else a = t
 *
 * Unsigned, t - step wraps when the target is below the step, so every alpha left above 0 and at
 * most the step is stepped up again, and the next call steps it back down: from 255 with step 6 the
 * alpha ends going 3, 9, 3, 9 (step 5: 5, 10) and never reaches 0. The lifted body (ir_00478730.o)
 * compares the same way. Ten calls reach it, each linked against it directly, from the results
 * task's four arms. Six fade to 0xFF or 0x80, which the game gets right: CalcTotalScore (0x0044F7C0)
 * at 0x0044F7E8 and 0x0044F8C8, CalcTotalTime (0x00450780) at 0x004507A8 and 0x0045087A,
 * NotCalcTotalScore (0x00450F90) at 0x00451003 and DispWinnerAndContinue (0x00451050) at
 * 0x0045107A. Four fade to 0 during the victory pose, by return address:
 *
 *   0x0044F885  CalcTotalScore         the pose counted to 0xB4 frames in the task's +4 (0x0044F888)
 *   0x00450837  CalcTotalTime          the same (0x0045083A)
 *   0x00450FD1  NotCalcTotalScore      to 0x168 frames (0x00450FD4)
 *   0x004510DA  DispWinnerAndContinue  to 0x168 frames (0x004510DD)
 *
 * LoadResultScreenObjects picks the results task's arm: DispWinnerAndContinue in two-player play;
 * NotCalcTotalScore after levels 19, 20, 29, 33 and 42 (its table at 0x004516B4) and every level
 * from 60 but 70; CalcTotalTime after level 70 and for missions 2 and 3 (MissionNum 0x0174AFE3,
 * the table at 0x004516CC); CalcTotalScore for missions 1, 4 and 5. None of the four fades to 0
 * reads what ScreenFade returns: each counts its frames, then stores 0 over the whole of
 * ScreenFadeARGB (0x0044F8B0, 0x00450862, 0x00450FEF, 0x004510FD). So the fade's end changes the
 * alpha the pose is drawn under and nothing about when anything happens. ScreenFadeIn (0x00478690)
 * and ScreenFadeOut (0x004786E0), which fade to 0 and 255 alone, compare signed and are left alone,
 * as the loader leaves them.
 *
 * The wrap: when the target (EDX) is below the step, it does what the loader's ScreenFade_ri does,
 * signed, in place of the lifted body: a == t returns 1; a > t steps down, to t when a - step <= t;
 * a < t steps up, to t when a + step >= t; each stores the alpha byte and returns 0. Then it leaves
 * as the body's RET does: EAX the answer, ECX the step as the body leaves it, EDX and ESI as they
 * were, EIP = [ESP], ESP + 4. The four callers read neither EAX, ECX nor the flags: each loads its
 * frame count into EAX, compares it and writes ECX at once (0x0044F885, 0x00450837, 0x00450FD1,
 * 0x004510DA). Every other call, every call with the target at or above the
 * step, runs the lifted body, whose unsigned compares give the signed answer there (both sides of
 * each compare are then 0 to 510, never wrapped); Documentation/scripts/tests/
 * test_tweak_modloader_default_fixes.py holds the two rules equal for every alpha, target and step.
 *
 * chDrawCnk (0x0056DDD0, 374 bytes, cdecl, one stack argument: the model to draw). After setting
 * the model's matrix it reads EDI = [0x01AED2CC] (0x0056DE69), and:
 *
 *   0x0056DE72  test edi,edi / jne 0x0056DE83
 *   0x0056DE76  test byte [0x01A27594],4
 *   0x0056DE7D  je 0x0056DF27            ; 0F 84 A4 00 00 00: the six bytes the loader NOPs
 *   0x0056DE83  the environment map's matrix: 0x00426FF0, then two products (0x00426E40) with
 *               the model's, stored by 0x0041FE30(0x39) into the slot at 0x01AF1620
 *   0x0056DF0D  EDI != 0: draw through 0x0056D9F0; EDI == 0: through 0x0056D7C0 (0x0056DF27)
 *
 * 0x0056D7C0 copies the slot at 0x01AF1620 for an environment-mapped strip (0x0056D945), so with
 * EDI 0 and bit 4 clear, the game's state for most Chao World draws (0x0056D5BF stores 0 to both),
 * it draws with the matrix the last model to build one left there. The loader's six NOPs make the
 * matrix always built. The wrap does the same without touching code: when [0x01AED2CC] is 0 and bit
 * 4 of [0x01A27594] is clear, it sets the bit, calls the lifted body, and stores the saved value
 * back; otherwise it only calls the body. With the bit set the body takes 0x0056DE83, then, EDI
 * being 0, draws through 0x0056D7C0: the NOPed path. The only code that reads or writes 0x01A27594
 * (every instruction in the image whose address field is 0x01A27591 to 0x01A27594) is in nine
 * functions: 0x0053FCF0, 0x0056D2A0, 0x0056D470, 0x0056D540, 0x0056D590, 0x0056D600, 0x0056D9F0,
 * 0x0056DDD0 and 0x0056E0B0. None but chDrawCnk is in the 195 functions the body reaches by direct
 * calls without 0x0056D9F0 (from function-map.json), and none of the other eight has its address
 * stored anywhere in the image, so no indirect call in those 195 can reach one. chDrawCnk's own
 * address is stored, as MotionDrawCallback (0x01D19C0C, at 0x0056E738): a call to it from inside
 * the body would see the bit set and build the matrix, as it does under the loader's NOPs. Seven
 * direct calls reach chDrawCnk (0x0054752A, 0x005477E3, 0x00549A27 in ALO_SeedExecutor_Display,
 * 0x0056D56B, 0x0056DF7D, 0x0056E064, 0x0056E1F4) beside that callback; the wrap takes them all.
 *
 * Off is the lifted code alone: each wrap asks sa2_tweak_on first and otherwise only calls its
 * `__real_` and returns; nothing is read.
 *
 * The save. Both write only screen state: the overlay's alpha, which the pose's end clears, and a
 * draw flag stored back before the wrap returns. Nothing reaches the save.
 *
 * Risks, with the tweak on. The victory pose's fade-out is drawn as the loader draws it: the
 * overlay's last faint 1 to 4 percent of white is gone, nothing else; its timing is the game's
 * (above). EnvMapFix builds one matrix more for each chDrawCnk draw that skipped it, and an
 * environment-mapped part of such a model reflects as its own view gives, not as the model drawn
 * before it did: Chao World looks as it does under the loader. A guest thread that read 0x01A27594
 * while the body runs would see bit 4; only the nine draw functions above read it.
 *
 * By name: the first call at each fade-to-0 site (the first 4 sites) where the signed rule and the
 * game's differ is said, "sa2 tweaks: mod loader fixes: ScreenFade to alpha 0 from
 * NotCalcTotalScore (0x00450FD1): alpha 3 -> 0, where the game's unsigned compare steps it to 9";
 * the first draw the wrap gives the matrix to, "sa2 tweaks: mod loader fixes: chDrawCnk (0x0056DDD0)
 * from 0x0056D570 builds the environment map's matrix it skips (0x0056DE7D)"; a guest read or write
 * that fails, once each, and that call is left to the lifted body; and, once, a flag the body
 * changed while the bit was set, which the reading above says never happens: it is left as the body
 * left it.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define MLF_FADE_ALPHA   0x0171CDA3u   /* ScreenFadeARGB (0x0171CDA0), its alpha byte */
#define MLF_FADE_MODE    0x0174AFCFu   /* byte: the step is 6 only when it is 2 */
#define MLF_FADE_OBJSUB  0x01A5A274u   /* CurrentObjectSub: the step is 6 only when it is 0 */
#define MLF_ENV_DRAWER   0x01AED2CCu   /* dword: chDrawCnk's EDI, 0 for the 0x0056D7C0 draw */
#define MLF_ENV_MODE     0x01A27594u   /* dword: bit 4 builds the matrix with EDI 0 */
#define MLF_ENV_BIT      4u

/* ScreenFade's callers that fade to 0, by return address. */
static const struct {
    uint32_t ret;
    const char *name;
} MLF_FADE_SITES[] = {
    {0x0044F885u, "CalcTotalScore"},
    {0x00450837u, "CalcTotalTime"},
    {0x00450FD1u, "NotCalcTotalScore"},
    {0x004510DAu, "DispWinnerAndContinue"},
};
#define MLF_FADE_NSITES (sizeof MLF_FADE_SITES / sizeof MLF_FADE_SITES[0])

static uint32_t g_mlf_fade_said[4];
static unsigned g_mlf_fade_nsaid;
static int g_mlf_fade_read_said, g_mlf_fade_write_said;
static int g_mlf_env_said, g_mlf_env_read_said, g_mlf_env_write_said, g_mlf_env_changed_said;

/* The loader's ScreenFade_ri: the alpha after one call, and the answer. */
static uint32_t mlf_fade_signed(int a, int t, int step, int *out) {
    if (a == t) {
        *out = a;
        return 1;
    }
    if (a > t) *out = a - step <= t ? t : a - step;
    else *out = a + step >= t ? t : a + step;
    return 0;
}

/* The lifted body's rule, 0x00478753-0x00478785, to say what the game would have done. */
static uint32_t mlf_fade_game(uint32_t a, uint32_t t, uint32_t step) {
    if (a == t) return a;
    if (a > t + step) return (a - step) & 0xFFu;
    if (a < t - step) return (a + step) & 0xFFu;
    return t & 0xFFu;
}

static const char *mlf_fade_site_name(uint32_t ret) {
    for (unsigned i = 0; i < MLF_FADE_NSITES; i++)
        if (MLF_FADE_SITES[i].ret == ret) return MLF_FADE_SITES[i].name;
    return "an unlisted caller";
}

void *__real_sub_478730(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_478730(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_MODLOADER_DEFAULT_FIXES)) return __real_sub_478730(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    const uint32_t target = sa2_rt_get_gpr(state, SA2_EDX);
    uint32_t ret = 0, objsub = 0;
    uint8_t mode = 0, alpha = 0;
    if (sa2_peek(esp, 4, &ret) != SA2_OK || sa2_peek(MLF_FADE_MODE, 1, &mode) != SA2_OK ||
        sa2_peek(MLF_FADE_OBJSUB, 4, &objsub) != SA2_OK || sa2_peek(MLF_FADE_ALPHA, 1, &alpha) != SA2_OK) {
        if (!g_mlf_fade_read_said++)
            fprintf(stderr, "sa2 tweaks: mod loader fixes: ScreenFade's state (the stack at 0x%08X, "
                    "0x%08X, 0x%08X, 0x%08X) does not read, so the game's fade runs\n", esp,
                    MLF_FADE_MODE, MLF_FADE_OBJSUB, MLF_FADE_ALPHA);
        return __real_sub_478730(state, pc, memory);
    }
    const uint32_t step = mode != 2 || objsub != 0 ? 5u : 6u;
    if (target >= step) return __real_sub_478730(state, pc, memory);   /* the game's is right */
    int now = 0;
    const uint32_t answer = mlf_fade_signed(alpha, (int)target, (int)step, &now);
    if (!answer) {
        const uint8_t b = (uint8_t)now;
        if (sa2_poke(MLF_FADE_ALPHA, 1, &b) != SA2_OK) {
            if (!g_mlf_fade_write_said++)
                fprintf(stderr, "sa2 tweaks: mod loader fixes: ScreenFade's alpha at 0x%08X does not "
                        "write, so the game's fade runs\n", MLF_FADE_ALPHA);
            return __real_sub_478730(state, pc, memory);
        }
        const uint32_t game = mlf_fade_game(alpha, target, step);
        unsigned i = 0;
        while (i < g_mlf_fade_nsaid && g_mlf_fade_said[i] != ret) i++;
        if (game != (uint32_t)now && i == g_mlf_fade_nsaid &&
            i < sizeof g_mlf_fade_said / sizeof g_mlf_fade_said[0]) {
            g_mlf_fade_said[g_mlf_fade_nsaid++] = ret;
            fprintf(stderr, "sa2 tweaks: mod loader fixes: ScreenFade to alpha %u from %s (0x%08X): "
                    "alpha %u -> %d, where the game's unsigned compare steps it to %u\n", target,
                    mlf_fade_site_name(ret), ret, alpha, now, game);
        }
    }
    sa2_rt_set_gpr(state, SA2_EAX, answer);
    sa2_rt_set_gpr(state, SA2_ECX, step);
    sa2_rt_set_gpr(state, SA2_EIP, ret);   /* the body's RET */
    sa2_rt_set_gpr(state, SA2_ESP, esp + 4u);
    return memory;
}

void *__real_sub_56ddd0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_56ddd0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_MODLOADER_DEFAULT_FIXES)) return __real_sub_56ddd0(state, pc, memory);
    uint32_t drawer = 0, mode = 0;
    if (sa2_peek(MLF_ENV_DRAWER, 4, &drawer) != SA2_OK || sa2_peek(MLF_ENV_MODE, 4, &mode) != SA2_OK) {
        if (!g_mlf_env_read_said++)
            fprintf(stderr, "sa2 tweaks: mod loader fixes: chDrawCnk's flags (0x%08X, 0x%08X) do not "
                    "read, so the game's draw runs\n", MLF_ENV_DRAWER, MLF_ENV_MODE);
        return __real_sub_56ddd0(state, pc, memory);
    }
    if (drawer != 0 || (mode & MLF_ENV_BIT)) return __real_sub_56ddd0(state, pc, memory);   /* built */
    const uint32_t set = mode | MLF_ENV_BIT;
    if (sa2_poke(MLF_ENV_MODE, 4, &set) != SA2_OK) {
        if (!g_mlf_env_write_said++)
            fprintf(stderr, "sa2 tweaks: mod loader fixes: chDrawCnk's flag at 0x%08X does not write, "
                    "so the game's draw runs\n", MLF_ENV_MODE);
        return __real_sub_56ddd0(state, pc, memory);
    }
    if (!g_mlf_env_said++) {
        uint32_t ret = 0;
        if (sa2_peek(sa2_rt_get_gpr(state, SA2_ESP), 4, &ret) == SA2_OK)
            fprintf(stderr, "sa2 tweaks: mod loader fixes: chDrawCnk (0x0056DDD0) from 0x%08X builds "
                    "the environment map's matrix it skips (0x0056DE7D)\n", ret);
        else
            fprintf(stderr, "sa2 tweaks: mod loader fixes: chDrawCnk (0x0056DDD0) builds the "
                    "environment map's matrix it skips (0x0056DE7D)\n");
    }
    void *out = __real_sub_56ddd0(state, pc, memory);
    uint32_t after = 0;
    if (sa2_peek(MLF_ENV_MODE, 4, &after) == SA2_OK && after == set) {
        if (sa2_poke(MLF_ENV_MODE, 4, &mode) != SA2_OK && !g_mlf_env_write_said++)
            fprintf(stderr, "sa2 tweaks: mod loader fixes: chDrawCnk's flag at 0x%08X does not write "
                    "back, so bit 4 stays set\n", MLF_ENV_MODE);
    } else if (!g_mlf_env_changed_said++) {
        fprintf(stderr, "sa2 tweaks: mod loader fixes: chDrawCnk's flag at 0x%08X was 0x%08X after "
                "the draw, not the 0x%08X the wrap set, so it is left as the draw left it\n",
                MLF_ENV_MODE, after, set);
    }
    return out;
}
