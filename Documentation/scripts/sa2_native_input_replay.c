/* Deterministic input to a private owned SA2 PC game copy for differential
 * movement analysis. Writes only game-process input projection; it never sends
 * desktop key events or changes the Steam installation.
 * Sequence: idle, forward, release, full reversal, circle, release.
 */
#include <stdint.h>

#define EXPORT __declspec(dllexport)

typedef struct {
    int32_t Version;
    void *Init, *Patches;
    int32_t PatchCount;
    void *Jumps;
    int32_t JumpCount;
    void *Calls;
    int32_t CallCount;
    void *Pointers;
    int32_t PointerCount;
} ModInfo;

EXPORT const ModInfo SA2ModInfo = {1, 0, 0, 0, 0, 0, 0, 0, 0, 0};

static unsigned long ready_tick;

EXPORT void __cdecl OnFrame(void) {
    const unsigned char *entity = ((const unsigned char *const *)0x01DEA6C0)[0];
    const unsigned char *player = ((const unsigned char *const *)0x01DE9600)[0];
    if (entity && player) ++ready_tick;
}

EXPORT void __cdecl OnControl(void) {
    const unsigned char *entity = ((const unsigned char *const *)0x01DEA6C0)[0];
    const unsigned char *player = ((const unsigned char *const *)0x01DE9600)[0];
    volatile int32_t *projected_yaw = (volatile int32_t *)0x01DEFBA0;
    volatile float *projected_strength = (volatile float *)0x01DEFBA4;
    if (!entity || !player) return;
    uint16_t facing_yaw = *(const uint32_t *)(entity + 0xc) & 0xffff;
    unsigned long phase = ready_tick;
    float strength = 0;
    uint16_t direction = facing_yaw;
    if (phase >= 120 && phase < 240) {
        strength = 1;
    } else if (phase >= 300 && phase < 390) {
        strength = 1;
        direction = facing_yaw + 0x8000;
    } else if (phase >= 390 && phase < 510) {
        strength = 1;
        direction = facing_yaw + (uint16_t)((phase - 390) * 0x0400);
    }
    *projected_yaw = direction;
    *projected_strength = strength;
}
