/* shadow-lightdash-fx: Shadow's light dash draws the see-through after-image trail that Sonic's
 * does, with Shadow's own model, as SA2 Render Fix does (runtime/sa2_tweaks.h: the tweaks and their
 * rules).
 *
 * TWEAK_WRAPS: sub_71e460
 *
 * What is missing, read from the unpacked image. 0x0071E460 (192 bytes, unnamed; Sonic_Afterimage
 * in the Mod Loader's names) draws one after-image of the light dash. Its only caller is Sonic_Main
 * (0x00717840), which Sonic, Shadow, Amy and Metal Sonic all run, from the light dash's action arm
 * (the four calls 0x00718162, 0x00718256, 0x00718277 and 0x00718295, each after `push edi; push
 * edi`: the CharObj2Base twice, as playerwk and as sonicwk; ESI holds the task's work). The body:
 *
 *   0x0071E460  test byte [0x0174B03C],1; jne ret   every other frame (the game timer's parity)
 *   0x0071E472  cmp byte [ebp+1],0; jne ret         EBP = [ESP+4] = playerwk: CharID must be 0,
 *                                                   Sonic's, so Shadow (CharID 1) never draws one
 *   0x0071E47C  mov eax,[0x01DE9714]; test; je ret  plobjects[30].model: Sonic's light-dash model
 *                                                   (plobjects at 0x01DE9620, 8-byte entries, the
 *                                                   model pointer at +4)
 *   0x0071E492  the matrix: push, translate and rotate by the task's position and angles
 *   0x0071E4D8  cmp byte [0x0174AFDE],0; jne        one-player only (TwoPlayerMode)
 *   0x0071E4FE  call 0x00476C20(model, 0, [sonicwk+0x394], 0.0f, playerwk->PlayerNum)
 *
 * 0x00476C20 (PlayerDrawMotionWithAlphaReducing) copies the matrix and keeps the model, the texture
 * list and the player number in a display task of its own (0x00476A60), which draws them later and
 * fades; neither it nor the display task reads CharID or plobjects again. The CharID byte is read
 * once, at 0x0071E472, and plobjects[30] once, at 0x0071E47C.
 *
 * SA2 Render Fix's replacement of this function (rfm_player.c [private: its local checkout])
 * draws plobjects[103] for Shadow, and its help text says Shadow lacks the after-image in every
 * release. Model 103's pointer is at 0x01DE9620 + 103 * 8 + 4 = 0x01DE995C. Its texture list is
 * the caller's own, [sonicwk+0x394], which for Shadow is Shadow's.
 *
 * The wrap, when the tweak is on: it reads playerwk at guest [ESP+4] (the lifted CALL has pushed the
 * return address), its CharID (+1) and CharID2 (+3). For Shadow (both 1) in one-player play with
 * model 103 loaded, it saves the CharID byte and the dword at 0x01DE9714, writes CharID 0 and
 * plobjects[30] = model 103, calls the lifted body, and writes both back before it returns. The
 * body then takes Sonic's path with Shadow's model and texture list. The game is single-threaded,
 * so nothing else runs while the two hold the swapped values; there is one path through the body
 * and the wrap restores both after it, whatever the body did.
 *
 * Left alone, each said once: Metal Sonic (CharID 1, CharID2 12, LoadMetalSonic 0x007176E6 and
 * 0x007176EA), whose model set is not Shadow's; two-player play, where the body draws nothing for
 * Sonic either; model 103 not loaded (its pointer 0); and a guest read that fails. Every other
 * character (Sonic, Amy) is the lifted body alone.
 *
 * Off is the lifted code alone: the wrap tests the tweak's bit first and otherwise only calls
 * `__real_sub_71e460`. With it on nothing changes until Shadow light-dashes. It writes nothing
 * that outlives the call: no save data, no lasting guest state.
 *
 * Risks: visual only. If model 103 were not Shadow's after-image model in some load, another model
 * would draw in the trail; the check covers only a model that is not loaded. The display task the
 * body creates is one more task each drawn frame of a light dash, as for Sonic.
 *
 * The first swap says "sa2 tweaks: shadow lightdash trail: Shadow's light dash draws its
 * after-image with model 103 (0x........)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define LIGHTDASH_MODEL_30   0x01DE9714u   /* plobjects[30].model: Sonic's after-image model */
#define LIGHTDASH_MODEL_103  0x01DE995Cu   /* plobjects[103].model: Shadow's */
#define LIGHTDASH_TWO_PLAYER 0x0174AFDEu   /* TwoPlayerMode */
#define LIGHTDASH_SHADOW     1u            /* CharID and CharID2 of Shadow */

static int g_lightdash_said_swap, g_lightdash_said_metal, g_lightdash_said_2p, g_lightdash_said_null,
    g_lightdash_said_read, g_lightdash_said_write;

void *__real_sub_71e460(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_71e460(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_SHADOW_LIGHTDASH_FX)) return __real_sub_71e460(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t pwp = 0, model = 0, was_model = 0;
    uint8_t ids[3] = {0}, two_player = 0;
    if (sa2_peek(esp + 4, 4, &pwp) != SA2_OK || sa2_peek(pwp + 1, 3, ids) != SA2_OK) {
        if (!g_lightdash_said_read++)
            fprintf(stderr, "sa2 tweaks: shadow lightdash trail: the player at [0x%08X] does not "
                    "read, so this after-image is the game's\n", esp + 4);
        return __real_sub_71e460(state, pc, memory);
    }
    if (ids[0] != LIGHTDASH_SHADOW) return __real_sub_71e460(state, pc, memory);
    if (ids[2] != LIGHTDASH_SHADOW) {
        if (!g_lightdash_said_metal++)
            fprintf(stderr, "sa2 tweaks: shadow lightdash trail: character %u on Shadow's code is "
                    "left alone (model 103 is Shadow's)\n", ids[2]);
        return __real_sub_71e460(state, pc, memory);
    }
    if (sa2_peek(LIGHTDASH_TWO_PLAYER, 1, &two_player) != SA2_OK
        || sa2_peek(LIGHTDASH_MODEL_103, 4, &model) != SA2_OK
        || sa2_peek(LIGHTDASH_MODEL_30, 4, &was_model) != SA2_OK) {
        if (!g_lightdash_said_read++)
            fprintf(stderr, "sa2 tweaks: shadow lightdash trail: the character models at 0x%08X do "
                    "not read, so this after-image is the game's\n", LIGHTDASH_MODEL_30);
        return __real_sub_71e460(state, pc, memory);
    }
    if (two_player) {
        if (!g_lightdash_said_2p++)
            fprintf(stderr, "sa2 tweaks: shadow lightdash trail: two-player play is left alone "
                    "(the game draws no after-image there for Sonic either)\n");
        return __real_sub_71e460(state, pc, memory);
    }
    if (!model) {
        if (!g_lightdash_said_null++)
            fprintf(stderr, "sa2 tweaks: shadow lightdash trail: model 103 (0x%08X) is not loaded, "
                    "so Shadow's light dash draws no after-image\n", LIGHTDASH_MODEL_103);
        return __real_sub_71e460(state, pc, memory);
    }
    const uint8_t sonic = 0, shadow = ids[0];
    if (sa2_poke(pwp + 1, 1, &sonic) != SA2_OK) {
        if (!g_lightdash_said_write++)
            fprintf(stderr, "sa2 tweaks: shadow lightdash trail: Shadow's CharID at 0x%08X could not "
                    "be written, so this after-image is the game's\n", pwp + 1);
        return __real_sub_71e460(state, pc, memory);
    }
    if (sa2_poke(LIGHTDASH_MODEL_30, 4, &model) != SA2_OK) {
        sa2_poke(pwp + 1, 1, &shadow);
        if (!g_lightdash_said_write++)
            fprintf(stderr, "sa2 tweaks: shadow lightdash trail: plobjects[30] at 0x%08X could not "
                    "be written, so this after-image is the game's\n", LIGHTDASH_MODEL_30);
        return __real_sub_71e460(state, pc, memory);
    }
    if (!g_lightdash_said_swap++)
        fprintf(stderr, "sa2 tweaks: shadow lightdash trail: Shadow's light dash draws its "
                "after-image with model 103 (0x%08X)\n", model);
    void *ret = __real_sub_71e460(state, pc, memory);
    const int back = sa2_poke(LIGHTDASH_MODEL_30, 4, &was_model) == SA2_OK
                     && sa2_poke(pwp + 1, 1, &shadow) == SA2_OK;
    if (!back)
        fprintf(stderr, "sa2 tweaks: shadow lightdash trail: Shadow's CharID (0x%08X) or plobjects[30] "
                "(0x%08X) could not be written back\n", pwp + 1, LIGHTDASH_MODEL_30);
    return ret;
}
