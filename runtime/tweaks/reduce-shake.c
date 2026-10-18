/* reduce-shake: camera shake (explosions, bosses, big impacts) moves the view a quarter as far. The
 * shake itself, its timing and the game's random numbers are left as they are; only the picture's
 * offset is scaled, for the one call that draws it (runtime/sa2_tweaks.h: the tweaks and their
 * rules).
 *
 * TWEAK_WRAPS: sub_4eb550
 *
 * What it rests on, read from the unpacked image.
 *
 * The shake offsets. Each camera's CameraData entry is 0x24D8 bytes from 0x01DCFF40; three floats at
 * +0x154 (0x01DD0094, 0x01DD0098 and 0x01DD009C for camera 0) are the shake's x, y and z offset.
 * RunCameraShake (0x004EB6E0, cameraCons_Main's) writes them each frame of a shake from the CRT's
 * rand (0x007A89D8, three calls a frame while a shake runs) and zeroes them when it ends; 0x004EB490
 * zeroes them when a camera is reset; some stage objects write them directly (0x00477627,
 * 0x004DF4D2, 0x005AE2A3, 0x006F006C, 0x006F3F22); and three more write them through a register
 * that holds their address: 0x006F00E2-0x006F00FD, a loop over every camera that starts from
 * 'mov eax,0x1dd0098' and stores at [eax-4], [eax] and, after stepping by 0x24D8, [eax-0x24D4];
 * and 0x006F4C38/0x006F4C3D and 0x006F4D87/0x006F4D8C, each 'add eax,0x1dd0094' followed by stores.
 * Every instruction in .text that addresses them, by displacement or by an immediate base, is one
 * of those writes or one of the three reads in 0x004EB550. RunCameraShake is not touched: skipping
 * it would leave the rand stream short and change the game.
 *
 * 0x004EB550 (245 bytes; the Mod Loader's CameraApplyScreenViewPtr) is the one reader. It takes the
 * camera index in ESI, no stack arguments, and adds the offsets to the camera's position (+0x194,
 * 0x01DD00D4 for camera 0) to make the applied position (+0x16C, 0x01DD00AC), from which 0x0077E0C0
 * builds the view; it ends in a jump to 0x00487060. Its two callers are lifted apart from it and
 * call it directly, so -wrap reaches both: cameraCons_Display's 0x004EB8CA (in 0x004EB8A0) and
 * cameraCons_Main's 0x004EBA28 (in 0x004EB8E0).
 * No instruction inside it, or in anything it reaches by direct calls and jumps, writes the offsets.
 *
 * The wrap reads ESI and that camera's three offsets, writes each times a quarter (exact in binary
 * floating point), calls the lifted body, and then writes the three saved values back, so after the
 * call every byte of guest memory is what it would be with the tweak off except the applied position
 * and what is built from it (the view, the sound listener 0x00487060 sets). Offsets that are all zero
 * (no shake, nearly every frame) are left alone: the body runs on them as it is.
 *
 * Off is the lifted code alone: the wrap tests the tweak's bit first and otherwise only calls
 * `__real_sub_4eb550`; no guest byte is read or written.
 *
 * Checked [private]: Metal Harbor's registered replay, whose rocket climb shakes the camera from
 * about tick 12095 to its end, run with the tweak on in a probe build that let it run under the
 * tick capture and word log. Of the game's memory only these differ from the run with it off: the
 * applied position (+0x16C), the view matrix at 0x01DDA380, the renderer's matrix buffers at
 * 0x01AF1160, 0x01AF14E0 and 0x01934888, and a copy of the camera position at 0x01A519C4 and
 * 0x01A51AD0 (written by 0x00716E00). The player, the goal latch, the stage trace, the input
 * applied and every other word of the 15644 ticks are equal; the frames before the shake are the
 * same image, and those during it are shifted by a few pixels.
 *
 * Risks. The applied position may feed something besides the picture in a stage not checked, an
 * object's visibility or clipping test, say: 0x00716E00's copy is the one such reader seen, and
 * in Metal Harbor nothing read it back into play. If the body ever wrote the offsets itself, the
 * values it wrote are kept, not overwritten by the saved ones, and the run says so once. When
 * the offsets do not read or write (an ESI the game never passes), the camera's shake is drawn
 * in full and the run says so once. Screen flashes (FlashManager 0x006DEDD0 and its loaders) are
 * a separate effect and are unchanged.
 *
 * Its lines, each said once: "sa2 tweaks: camera shake reduced: the first shake (camera 0, offset
 * 1.5 -0.25 2, game frame 1234) drawn a quarter as far" the first time it scales a shake; and the
 * lines for a read, write or restore it cannot make, each naming the camera and the address.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define SHAKE_OFFSETS      0x01DD0094u   /* CameraData 0x01DCFF40 + 0x154: camera 0's x, y, z */
#define SHAKE_CAMERA_SIZE  0x24D8u       /* one camera's CameraData entry */
#define SHAKE_FRAME        0x0174B03Cu   /* FrameCountIngame, for the first line only */
#define SHAKE_SCALE        0.25f

static int g_shake_said, g_shake_read_said, g_shake_write_said, g_shake_kept_said;

void *__real_sub_4eb550(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_4eb550(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_REDUCE_SHAKE)) return __real_sub_4eb550(state, pc, memory);
    const uint32_t cam = sa2_rt_get_gpr(state, SA2_ESI);
    const uint32_t at = SHAKE_OFFSETS + SHAKE_CAMERA_SIZE * cam;    /* the guest's own imul and lea */
    uint32_t saved[3];
    if (sa2_peek(at, sizeof saved, saved) != SA2_OK) {
        if (!g_shake_read_said++)
            fprintf(stderr, "sa2 tweaks: camera shake reduced: camera %u's shake offsets at 0x%08X do "
                    "not read, so its shake is drawn in full\n", cam, at);
        return __real_sub_4eb550(state, pc, memory);
    }
    if (((saved[0] | saved[1] | saved[2]) & 0x7FFFFFFFu) == 0)          /* no shake (+0 or -0) */
        return __real_sub_4eb550(state, pc, memory);
    float f[3];
    uint32_t scaled[3];
    memcpy(f, saved, sizeof f);
    for (int i = 0; i < 3; i++) f[i] *= SHAKE_SCALE;
    memcpy(scaled, f, sizeof scaled);
    if (sa2_poke(at, sizeof scaled, scaled) != SA2_OK) {
        if (!g_shake_write_said++)
            fprintf(stderr, "sa2 tweaks: camera shake reduced: camera %u's shake offsets at 0x%08X do "
                    "not write, so its shake is drawn in full\n", cam, at);
        (void)sa2_poke(at, sizeof saved, saved);
        return __real_sub_4eb550(state, pc, memory);
    }
    if (!g_shake_said++) {
        float o[3];
        uint32_t frame = 0;
        memcpy(o, saved, sizeof o);
        if (sa2_peek(SHAKE_FRAME, 4, &frame) == SA2_OK)
            fprintf(stderr, "sa2 tweaks: camera shake reduced: the first shake (camera %u, offset %g "
                    "%g %g, game frame %u) drawn a quarter as far\n", cam, (double)o[0], (double)o[1],
                    (double)o[2], frame);
        else
            fprintf(stderr, "sa2 tweaks: camera shake reduced: the first shake (camera %u, offset %g "
                    "%g %g) drawn a quarter as far\n", cam, (double)o[0], (double)o[1], (double)o[2]);
    }
    void *ret = __real_sub_4eb550(state, pc, memory);
    uint32_t now[3];
    if (sa2_peek(at, sizeof now, now) == SA2_OK && memcmp(now, scaled, sizeof now) != 0) {
        if (!g_shake_kept_said++)
            fprintf(stderr, "sa2 tweaks: camera shake reduced: camera %u's shake offsets at 0x%08X "
                    "changed inside 0x004EB550, so the values it left are kept\n", cam, at);
        return ret;
    }
    if (sa2_poke(at, sizeof saved, saved) != SA2_OK && !g_shake_kept_said++)
        fprintf(stderr, "sa2 tweaks: camera shake reduced: camera %u's saved shake offsets do not "
                "write back at 0x%08X, so the quarter values stay until the game next writes them\n",
                cam, at);
    return ret;
}
