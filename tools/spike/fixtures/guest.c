#include "guest.h"
#include "shapes.h"
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>

#define IMAGE_BASE 0x400000u

int guest_map(const char *image_path) {
    void *want = (void *)(uintptr_t)IMAGE_BASE;
    size_t len = GUEST_END - IMAGE_BASE;
    void *got = mmap(want, len, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (got != want) {
        fprintf(stderr, "guest_map: cannot map %#x..%#x (got %p)\n", IMAGE_BASE, GUEST_END, got);
        return -1;
    }
    FILE *f = fopen(image_path, "rb");
    if (!f) { perror(image_path); return -1; }
    size_t n = fread(want, 1, len, f);
    fclose(f);
    if (n == 0) { fprintf(stderr, "guest_map: empty image %s\n", image_path); return -1; }
    return 0;
}

void guest_reset_arenas(void) {
    memset(gp8(A_ARGS), 0, 0x10000);
    memset(gp8(A_MSTACK), 0, 0x30 * A_MSTACK_FRAMES + 0x100);
    memset(gp8(A_STACK_TOP - 0x10000), 0, 0x10000 + 0x100);
    for (int i = 0; i < 3; i++) gp32(A_OUT)[i] = 0xDEADBEEFu;
}
