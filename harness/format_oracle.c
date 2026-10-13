/* Every case in format_cases.h through the oracle's own USER32!wvsprintfA.
 *
 * A 32-bit Windows console program, built with the pinned zig and run under
 * the oracle's Proton by tools/s4/format_oracle.py. The output buffer is
 * prefilled with 0xCC so a write past the terminator shows as `touched`.
 * One JSON line per case: {"case", "name", "ret", "touched", "out"}, `out` the bytes
 * up to `touched` in hex (stdout only; the receipt holds them as text).
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

#include "format_cases.h"

int main(void) {
    static char out[2048];
    for (unsigned i = 0; i < SA2_FORMAT_CASES; i++) {
        const sa2_format_case *c = &sa2_format_cases[i];
        uint32_t argv[8] = {0};
        for (unsigned k = 0; k < c->nargs; k++)
            argv[k] = c->arg[k].kind == 's' ? (uint32_t)(uintptr_t)c->arg[k].s
                    : c->arg[k].kind == 'w' ? (uint32_t)(uintptr_t)c->arg[k].w
                    : c->arg[k].n;
        memset(out, 0xCC, sizeof out);
        int r = wvsprintfA(out, c->fmt, (va_list)argv);
        unsigned touched = sizeof out;
        while (touched && (unsigned char)out[touched - 1] == 0xCC) touched--;
        printf("{\"case\":%u,\"name\":\"%s\",\"ret\":%d,\"touched\":%u,\"out\":\"",
               i, c->name, r, touched);
        for (unsigned k = 0; k < touched; k++) printf("%02x", (unsigned char)out[k]);
        printf("\"}\n");
    }
    fflush(stdout);
    return 0;
}
