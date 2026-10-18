/* walker-cannon-fix: when a Chao Walker that has the Mech's cannon upgrade fires it, the shot's
 * cannon object is drawn without reaching for a Mech's model, which a Chao Walker never loads, so
 * the game no longer crashes there (runtime/sa2_tweaks.h: the tweaks and their rules). This is the
 * community Codes.lst fix "write8 75D914 0x3", done as a wrap because OpenSA2 never runs the
 * image's code bytes.
 *
 * TWEAK_WRAPS: sub_75d880
 *
 * The reading of the unpacked image. 0x0075D880 (208 bytes, WalkerBazooka_Disp in the function
 * table) draws the cannon object of an upgraded shot. Its only reference is 0x0075DC86, where its
 * executor (0x0075D9F0) installs it; 0x0075DCA0 creates that object, called only from the walkers'
 * main (0x00741530) at 0x007416A0 and 0x007416EE, when the walker fires with one of two upgrade
 * bits in CharObj2 + 0x24 set (0x200000, 0x80): the reload byte at CharObj2 + 0x360 is no longer
 * above 0 (0x00741617), the player's fire flag 0x01DEFB00[player] is set (0x0074162A), and the
 * same branch launches the projectile (0x0075E1D0 or 0x0075E310), plays 0x00438E70 and sets the
 * reload [edi+0x430] = 0x3000 (Codes.lst: "drawing the upgraded cannon shots"). The display takes
 * p = [[0x01DEA6E0] + 0x40], player 1's CharObj2, and after the object's own matrix calls
 * (0x00427470, 0x004275B0, 0x004274E0) it tests one byte of it:
 *
 *   0x0075D90E  mov ecx,[esp+14h]       ; p, saved at entry
 *   0x0075D912  mov al,[ecx+1]          ; CharID (8A 41 01: the 01 at 0x0075D914 is the fix's byte)
 *   0x0075D915  cmp al,7 / jne          ; 7: [0x01DE9EA4]'s model list
 *   0x0075D924  cmp al,6 / jne          ; 6: [0x01DEA05C]'s model list
 *   0x0075D930  call 0x0042E6C0         ; DrawChunkModel, EAX the list's second entry
 *
 * A Chao Walker plays as a Mech (CharID 6 or 7) with its own CharID2 at p + 3, so the test sends
 * it to a Mech's model list it does not have. The Codes.lst fix makes the test read [ecx+3],
 * CharID2, which is 6 or 7 only for the Mechs themselves. The wrap does the same without touching
 * code: it reads p, and when CharID (p + 1) and CharID2 (p + 3) differ, writes CharID2 into p + 1,
 * calls the lifted body, and writes the saved CharID back. Nothing else the body or any of its
 * callees reaches reads CharID: the body's one read of p is the test above, and a scan of the
 * 135 functions reachable from its four callees finds no read of the player arrays
 * (0x01DEA6E0, 0x01DEFE20), so for the length of the call only that test sees the swap. For
 * Sonic, the Mechs and every other character CharID equals CharID2 and the wrap writes nothing.
 *
 * Off is the lifted code alone: the wrap tests its bit first and only calls __real_sub_75d880.
 * On, nothing outlives the call: CharID is back before the wrap returns, and CharObj2 is play
 * state, not save data, so the player's save is never written.
 *
 * Risks. A player meets this only with a cheat that gives a Chao Walker the Mech's upgrades
 * (Codes.lst "Have All Upgrades"); without it a Chao Walker never fires the upgraded cannon, and
 * the tweak has nothing to do. With it, the game crashes on the walker's first upgraded shot; with
 * the tweak, that shot's cannon object draws no Mech model, which is what the Codes.lst fix
 * shows. The body reads only player 1's CharObj2, whoever fired the cannon, and so does the wrap,
 * as the fix does.
 *
 * By name: the first time the wrap swaps, it says "sa2 tweaks: walker-cannon fix: the cannon's
 * model test read CharID2 <n> in place of CharID <m>"; when player 1's CharObj2 does not read, or
 * CharID does not write, it says so once and calls the lifted body unchanged, and a CharID that
 * does not write back is said every time.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define WALKER_CANNON_PLAYER1   0x01DEA6E0u   /* MainCharObj1[0]: player 1's object */
#define WALKER_CANNON_CHAROBJ2  0x40u         /* its CharObj2 pointer */
#define WALKER_CANNON_CHARID    1u            /* CharObj2 + 1: CharID, what the body tests */
#define WALKER_CANNON_CHARID2   3u            /* CharObj2 + 3: CharID2, what the fix tests */

static int g_walker_cannon_said, g_walker_cannon_unread_said, g_walker_cannon_unwritten_said;

void *__real_sub_75d880(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_75d880(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_WALKER_CANNON_FIX)) return __real_sub_75d880(state, pc, memory);
    uint32_t obj = 0, p = 0;
    uint8_t ids[3];                       /* CharObj2 + 1 .. + 3: CharID, Costume, CharID2 */
    if (sa2_peek(WALKER_CANNON_PLAYER1, 4, &obj) != SA2_OK || obj == 0
        || sa2_peek(obj + WALKER_CANNON_CHAROBJ2, 4, &p) != SA2_OK || p == 0
        || sa2_peek(p + WALKER_CANNON_CHARID, 3, ids) != SA2_OK) {
        if (!g_walker_cannon_unread_said++)
            fprintf(stderr, "sa2 tweaks: walker-cannon fix: player 1's CharObj2 does not read "
                    "(object 0x%08X, CharObj2 0x%08X), so the cannon is drawn as the game does\n",
                    obj, p);
        return __real_sub_75d880(state, pc, memory);
    }
    const uint8_t charid = ids[0], charid2 = ids[2];
    if (charid == charid2) return __real_sub_75d880(state, pc, memory);
    if (sa2_poke(p + WALKER_CANNON_CHARID, 1, &charid2) != SA2_OK) {
        if (!g_walker_cannon_unwritten_said++)
            fprintf(stderr, "sa2 tweaks: walker-cannon fix: CharID at 0x%08X does not write, so "
                    "the cannon is drawn as the game does\n", p + WALKER_CANNON_CHARID);
        return __real_sub_75d880(state, pc, memory);
    }
    void *ret = __real_sub_75d880(state, pc, memory);
    if (sa2_poke(p + WALKER_CANNON_CHARID, 1, &charid) != SA2_OK)
        fprintf(stderr, "sa2 tweaks: walker-cannon fix: CharID at 0x%08X did not write back; "
                "player 1 keeps character %u in its place\n", p + WALKER_CANNON_CHARID, charid2);
    else if (!g_walker_cannon_said++)
        fprintf(stderr, "sa2 tweaks: walker-cannon fix: the cannon's model test read CharID2 %u in "
                "place of CharID %u\n", charid2, charid);
    return ret;
}
