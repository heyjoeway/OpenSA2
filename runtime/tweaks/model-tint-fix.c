/* model-tint-fix: rockets, propeller springs, fire skulls and a few other stage objects are drawn
 * without the grey tint the PC version gives them: the diffuse colour of 23 of their
 * materials goes from the default 0xB2B2B2 to white, as SA2 Render Fix's model tint fix
 * (rfg_mdltint.c, RFG_ModelTintInit) sets it (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_43cb50
 *
 * What is wrong, read from the unpacked image. These objects are Ginja models in the executable's
 * .dataa (0x00917000 on). A GJS_OBJECT holds its model at +4, its child at +0x2C and its sibling
 * at +0x30; a GJS_MODEL its opaque and transparent meshset lists at +8 and +0xC and their counts,
 * u16, at +0x10 and +0x12; a meshset is 16 bytes, {materials, count, display list, size}; and a
 * material 8 bytes, {u8 id, 3 bytes of padding, u32 setting}. The draw (DrawSA2BModel 0x004932C0,
 * then 0x0042BED0) hands each meshset's materials to 0x0042BDB0, which calls the handler for each
 * id from the table at 0x0174B334 when its setting changed; id 5 (GJ_MT_DIFFUSE) is 0x0042BA60,
 * which keeps the colour at 0x01934740 and gives it to 0x0041F4E0 as shader constant register 0x42.
 * 0xB2B2B2FF (RGBA) is the default diffuse: 70% grey, opaque. Render Fix sets the first diffuse
 * material of each meshset of these models to 0xFFFFFFFF, by these addresses (the Steam
 * executable's, the one OpenSA2 lifts), at its start. It does it for 5 object trees and 10
 * models; its chunk-model tint fix (RF_CnkTintFix) is never called, and its springs' lines
 * (3SPRING, SPRINGA, SPRINGB) are commented out, so they are not here either.
 *
 *   objects (the tree from each, child and sibling)  models
 *   0x00B46A38  rocket missile (ROCKETMISSILE's      0x00B0D7CC  Knudai (KNUDAI's display
 *               display 0x006CF500)                              0x006E7A30)
 *   0x00B3AA70  rocket (ROCKET's display 0x006D5900) 0x00AEED4C  Metal Harbor's static missiles
 *   0x01176AE8  propeller spring: blades, spring                 (DAMMY 0x006F6D00, [0x00AEFDEC])
 *   0x0116598C  fire skull, Sky Rail's: jaw, box     0x011772D8, 0x01177334, 0x01177BF0  propeller
 *   0x0161AA38  fire skull, Aquatic Mine's and                   spring: base, the two arrows
 *               Pool Quest's: jaw, box               0x011659C4, 0x0160132C  the fire skulls' heads
 *                                                    0x00B3B67C  Nocol skull (0x004D6CF0,
 *                                                                AquaticMine_NOCOLSKULL)
 *                                                    0x01091CE4, 0x01092464  tree trunks ("ST",
 *                                                                "SNB")
 *
 * The names and stages are Render Fix's comments; the functions named are the ones in .text that
 * hold the address (the rest are reached only through .dataa tables). The walk from these 15
 * addresses, done again over the image, visits 12 objects and 55 meshsets; 23 of the meshsets
 * have a diffuse material (each the first of its model's list, ids 0 0 1 0 4 2 5 8 9 10), and all
 * 23 settings read 0xB2B2B2FF. Each material list is the list of that one meshset alone (one
 * pointer to it in the image), and no code holds a setting's address, so the write reaches these
 * models and no other. The 23 dwords, in the walk's order (MODEL_TINT_AT below):
 * 0x00B466DC 0x00B45D94 0x00B45BCC 0x00B3A7F4 0x00B3A1BC 0x00B39FF4 0x01176984 0x011763A4
 * 0x011658A4 0x0116561C 0x0161A974 0x0161A6EC 0x00B110BC 0x00AEF61C 0x00AEFD3C 0x01177134
 * 0x0117774C 0x01177B64 0x01166124 0x0161B20C 0x00B3CD64 0x0109214C 0x01092934.
 * Documentation/scripts/tests/test_tweak_model_tint_fix.py walks the image again and holds the
 * list to it.
 *
 * Which of them shows. Each list's strip flags (id 2, GJD_FST_*, bits 8-15 of the setting) say
 * where the meshset's colour comes from. 19 of the 23 are lit with the register colour (flags
 * 0x00 or 0x02, IS; the propeller spring's two arrows 0x03, IL IS, unlit), so the grey reaches
 * the screen and white brightens them. The other 4, the static missiles' two and the tree trunks'
 * two, are 0x0B (IL IS VM): the colour comes from the vertices, and on the GameCube the register's
 * RGB is not used; written all the same, as Render Fix writes them, they were not seen to change
 * (no run had them in view).
 *
 * Where it writes. LoadLevelManager (0x0043CB50), which GameStateHandler (0x0043A780) and
 * 0x0043C3A0 and 0x0043C420 call when a level starts. On its first call with the tweak on, before
 * the lifted body, the wrap reads the 23 dwords; when every one holds 0xB2B2B2FF it writes
 * 0xFFFFFFFF to each and reads each back. The data is the executable's, which the game reads in
 * place and never writes, so it is done once per process: every later level keeps it.
 *
 * Checked in the Linux release [private: practice-stage runs, host 2's player, SA2_PEEK]: in Sky
 * Rail, off and on, the same keys, the propeller spring's pixels (and nothing else) are brighter
 * with the tweak on, every differing pixel, by a median 1.32 times, and the frames before and
 * after it are the same image; in Pumpkin Hill, at the start, the rim of the stand where the SET
 * file places the Knudai is brighter and nothing else differs; the 23 dwords read 0xFFFFFFFF at
 * the end of an on run and 0xB2B2B2FF at the end of an off run.

 * Why off is identical: with the tweak off the wrap only calls `__real_sub_43cb50`, and the
 * materials keep the executable's bytes.
 *
 * By name, never silently. The first call says one line: "sa2 tweaks: model tint fixed: 23
 * materials (diffuse 0xB2B2B2FF -> 0xFFFFFFFF)". When any dword is not 0xB2B2B2FF (another
 * executable, or a change that wrote the models first), it changes none and names each one that
 * differs with what it holds; when one does not read, or a write does not hold, it names it, puts
 * back any it wrote, and changes none.
 *
 * Risks. A visual change only, on a list chosen by hand: other models with the default grey
 * diffuse keep it. The blanket rule (every 0xB2B2B2 diffuse to white) is not taken, because
 * textured materials keep the default on purpose. The models are executable data in guest memory,
 * never the save, so nothing is saved.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define MODEL_TINT_WAS 0xB2B2B2FFu   /* the default diffuse, 70% grey */
#define MODEL_TINT_NOW 0xFFFFFFFFu   /* white, Render Fix's */

/* The settings of the 23 diffuse materials (the header), in the walk's order. */
static const uint32_t MODEL_TINT_AT[] = {
    0x00B466DCu, 0x00B45D94u, 0x00B45BCCu,                /* rocket missile */
    0x00B3A7F4u, 0x00B3A1BCu, 0x00B39FF4u,                /* rocket */
    0x01176984u, 0x011763A4u,                             /* propeller spring: blades, spring */
    0x011658A4u, 0x0116561Cu,                             /* fire skull (Sky Rail): jaw, box */
    0x0161A974u, 0x0161A6ECu,                             /* fire skull (Aquatic Mine): jaw, box */
    0x00B110BCu,                                          /* Knudai */
    0x00AEF61Cu, 0x00AEFD3Cu,                             /* static missiles */
    0x01177134u, 0x0117774Cu, 0x01177B64u,                /* propeller spring: base, arrows */
    0x01166124u, 0x0161B20Cu,                             /* the fire skulls' heads */
    0x00B3CD64u,                                          /* Nocol skull */
    0x0109214Cu, 0x01092934u,                             /* tree trunks */
};
#define MODEL_TINT_N (sizeof MODEL_TINT_AT / sizeof MODEL_TINT_AT[0])

static int g_model_tint_tried;

/* Puts back the first n written. */
static void model_tint_restore(unsigned n) {
    const uint32_t was = MODEL_TINT_WAS;
    for (unsigned i = 0; i < n; i++) (void)sa2_poke(MODEL_TINT_AT[i], 4, &was);
}

static void model_tint_write(void) {
    char line[MODEL_TINT_N * 32];
    size_t at = 0;
    unsigned differ = 0;
    for (unsigned i = 0; i < MODEL_TINT_N; i++) {
        uint32_t v;
        if (sa2_peek(MODEL_TINT_AT[i], 4, &v) != SA2_OK) {
            fprintf(stderr, "sa2 tweaks: model tint fixed: the material at 0x%08X does not read, so "
                    "none of the %u is changed\n", MODEL_TINT_AT[i], (unsigned)MODEL_TINT_N);
            return;
        }
        if (v != MODEL_TINT_WAS) {
            if (at < sizeof line)
                at += (size_t)snprintf(line + at, sizeof line - at, "%s0x%08X holds 0x%08X",
                                       differ ? ", " : "", MODEL_TINT_AT[i], v);
            differ++;
        }
    }
    if (differ) {
        fprintf(stderr, "sa2 tweaks: model tint fixed: %u of the %u materials %s not the game's "
                "0x%08X (%s), so none is changed\n", differ, (unsigned)MODEL_TINT_N,
                differ == 1 ? "is" : "are", MODEL_TINT_WAS, line);
        return;
    }
    const uint32_t now = MODEL_TINT_NOW;
    for (unsigned i = 0; i < MODEL_TINT_N; i++) {
        uint32_t v = 0;
        if (sa2_poke(MODEL_TINT_AT[i], 4, &now) != SA2_OK
            || sa2_peek(MODEL_TINT_AT[i], 4, &v) != SA2_OK || v != MODEL_TINT_NOW) {
            model_tint_restore(i + 1);
            fprintf(stderr, "sa2 tweaks: model tint fixed: the material at 0x%08X could not be "
                    "written, so the %u before it are put back and none is changed\n",
                    MODEL_TINT_AT[i], i);
            return;
        }
    }
    fprintf(stderr, "sa2 tweaks: model tint fixed: %u materials (diffuse 0x%08X -> 0x%08X)\n",
            (unsigned)MODEL_TINT_N, MODEL_TINT_WAS, MODEL_TINT_NOW);
}

void *__real_sub_43cb50(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_43cb50(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_MODEL_TINT_FIX)) return __real_sub_43cb50(state, pc, memory);
    if (!g_model_tint_tried) {
        g_model_tint_tried = 1;
        model_tint_write();
    }
    return __real_sub_43cb50(state, pc, memory);
}
