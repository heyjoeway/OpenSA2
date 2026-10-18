/* analog-fix: the analog stick's stroke, the push the player's physics reads, grows across the
 * gamepad stick's throw up to two thirds of a push instead of reaching full before half of one, so a
 * half push walks and a full push still runs (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_441ef0
 *
 * Where the curve comes from, read from the unpacked image. WriteAnalogs, 0x00441EF0 (693 bytes),
 * called by Control (0x00441D21, which every menu and stage calls each frame) and by the last boss's
 * player manager (0x0049E580), works out each player's stick angle and stroke. For players 0 and 1
 * it reads x1 and y1, int16 at Controllers 0x01DEFC00 + 0x34 * i + 0x1C and + 0x1E; subtracts a
 * square deadzone of 14 from each axis (the doubles 14.0 at 0x009052E0 and -14.0 at 0x00905798);
 * then, with r the length of what is left, stroke = r^3 / 125000.0 (r^2 at 0x0044209D, sqrt
 * 0x008290A0, the multiply at 0x004420D3 and `fdiv qword [0x00905790]` at 0x004420D7), held to at
 * most 1.0 (0x004420E1). The stroke goes to AnalogThings 0x01DEFBA0 + 8 * i + 4 (the angle at + 0)
 * and its copy at 0x01DEFAC0 + 8 * i + 4.
 *
 * 125000 is 50^3, so the stroke is full at r = 50. What r a stick reaches was measured in the lifted
 * game [private: a Metal Harbor play run, the test spawn, with gamepad records on the live input
 * source, SA2_GUEST_CMDLINE=-r, and Controllers[0] read after each Control call]: the game's pad
 * fill (0x00425700: its input layer's axis value, after that layer's own deadzone and scaling
 * around 0x00862260, times 127.0 at 0x009060E8, truncated by _ftol2 0x008287C0) gives x1 = 224 at a
 * full push right (XInput 32767), -225 left (-32768), 138 on each axis at a full diagonal (23170,
 * 23170) and 76 at half a push (16384). So a pad reaches r = 210 straight and 175 on a diagonal, and the vanilla
 * curve is full from x1 = 64, a bit under half a push (0.46 of the throw). The keyboard is not a
 * stick: the fill writes x1 = 127 or -128 for a held direction (0x00425997-0x004259CC), r = 113.
 *
 * The tweak changes the divisor: on its first call with the tweak on, the wrap reads the 8 bytes at
 * 0x00905790 and, when they are exactly 125000.0, writes 1404928.0 (112^3) there, says so once, and
 * calls the lifted body. The stroke is then r^3 / 1404928, full at r = 112: one under the keyboard's
 * 113, so a held key is still a full stroke, which is as far as the curve can stretch. On a
 * pad the stroke is full from about two thirds of a push straight and four fifths on a diagonal
 * (vanilla: under half), half a push gives 0.17 (vanilla 1.0), a walk, and the curve between is the
 * game's cube. The per-axis deadzone and the clamp are the game's too, so a resting stick's drift
 * stays inside the deadzone as before. One divisor cannot do more: a pad's full push is r = 175 to
 * 210, and a divisor sized for it would leave a held key at a quarter stroke.
 *
 * 0x00905790 is in .rdata (0x00873000-0x00916105; .text ends at 0x00870591), and the only 4-byte
 * reference to it in the image is that fdiv's; the lifted object of 0x00441EF0 reads it from guest
 * memory on each call, in the f80 build, the plain one and the arm64 one alike (none folds 125000.0
 * into a constant). The 14.0 doubles are shared with dozens of other functions and are left alone.
 *
 * A value other than 125000.0 there (another build, or a mod that changed it), or a read or write
 * that fails, is said once in its own line and the divisor is left as it is: "sa2 tweaks: analog
 * full throw: 0x00905790 holds 1000000, not 125000.0, so the stick's curve is left alone".
 *
 * Off is the lifted code alone: the wrap tests the tweak's bit first and only calls the lifted body,
 * and nothing else reads or writes 0x00905790.
 *
 * Risks. The stroke is what the physics reads, so this is a gameplay change: the off-in-replays rule
 * keeps every replay and receipt on the vanilla curve, and a speedrun that leans on half-push running
 * plays differently. The divisor is one value for both players and for the last boss's player
 * manager. AnalogThrust (0x01DEFDA0, set when the stroke crosses 0.5, the float at 0x0165DFD4) reads
 * the new stroke, so that "pushed" test now crosses at r = 89 instead of 40. A stick that cannot
 * reach its rim still runs, since full stroke comes before it. Nothing is saved.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "guest_memory.h"
#include "sa2_tweaks.h"

#define ANALOG_FIX_DIVISOR 0x00905790u   /* .rdata double, read by WriteAnalogs's fdiv at 0x004420D7 */
static const double ANALOG_FIX_VANILLA = 125000.0;    /* 50^3: full stroke at r = 50 */
static const double ANALOG_FIX_FULL = 1404928.0;      /* 112^3: full stroke at r = 112 */

static int g_analog_fix_done;

static void analog_fix_apply(void) {
    double was = 0.0;
    int rc = sa2_peek(ANALOG_FIX_DIVISOR, 8, &was);
    if (rc != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: analog full throw: 0x%08X does not read (%d), so the stick's "
                "curve is left alone\n", ANALOG_FIX_DIVISOR, rc);
        return;
    }
    if (memcmp(&was, &ANALOG_FIX_VANILLA, sizeof was) != 0) {
        fprintf(stderr, "sa2 tweaks: analog full throw: 0x%08X holds %.17g, not 125000.0, so the "
                "stick's curve is left alone\n", ANALOG_FIX_DIVISOR, was);
        return;
    }
    rc = sa2_poke(ANALOG_FIX_DIVISOR, 8, &ANALOG_FIX_FULL);
    if (rc != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: analog full throw: 0x%08X does not write (%d), so the stick's "
                "curve is left alone\n", ANALOG_FIX_DIVISOR, rc);
        return;
    }
    fprintf(stderr, "sa2 tweaks: analog full throw: the stroke is full at 112 past the deadzone, "
            "not 50 (0x%08X: 125000.0 -> 1404928.0)\n", ANALOG_FIX_DIVISOR);
}

void *__real_sub_441ef0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_441ef0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ANALOG_FIX)) return __real_sub_441ef0(state, pc, memory);
    if (!g_analog_fix_done) {
        g_analog_fix_done = 1;
        analog_fix_apply();
    }
    return __real_sub_441ef0(state, pc, memory);
}
