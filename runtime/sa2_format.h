/* USER32's wsprintf format language, over guest memory.
 *
 * `wvsprintfA` is the one printf-family import in the image, and its one caller
 * (0x00851DC0) is a debug-print helper: it formats into a 512-byte stack buffer,
 * hands the result to OutputDebugStringA and never reads the return value. Its
 * callers pass five format strings, all %05d, %08x and %s.
 *
 * The language is wsprintf's, which is not printf's, and the reference is the
 * oracle's own user32 -- Proton's -- observed case by case: harness/
 * format_cases.h through tools/s4/format_oracle.py, whose receipt
 * (Documentation/receipts/s4-wsprintf-oracle-v1.json) the tests hold this to.
 *
 *   %[-][#][0][width][.precision][l|h|w|I|I32|I64]type,  type in c C d i u x X p s S
 *
 * The flags come in that order or not at all. Where the oracle differs from
 * printf, this follows the oracle:
 *
 *   - the width does not count a `#` prefix: %#10x of 0x1f is 12 characters;
 *   - the precision counts the sign: %.3d of -7 is "-07";
 *   - `0` pads to the width even with a precision, and only numbers;
 *   - `h` and `l` do not change an integer's width; only I64 reads 8 bytes;
 *   - %p is %08X of one dword, whatever its size prefix; `#` prefixes 0 as
 *     well ("0x0");
 *   - width and precision are 32-bit and wrap: %4294967297d has a width of 1;
 *   - a precision of 0 on a string is no limit, on a number is 1;
 *   - a zero %c stores its NUL and counts it;
 *   - anything else after `%` -- `%5z`, a trailing `%5`, `%5%` -- drops what was
 *     parsed, pads to the width unless `-`, and reads the character that
 *     ended it as ordinary text, so `%5%d` is five spaces and then a %d;
 *   - output stops at 1023 characters and a terminator, and a call that fills
 *     the buffer returns 1024, not the 1023 it stored.
 *
 * The oracle's user32 has one defect with two faces, and here this follows the
 * documentation: `#` adds two characters to a field's count whether or not it
 * writes a `0x`. On a decimal conversion it then copies two bytes past the
 * digits it formatted (%#d of 5 is "5", NUL, then whatever followed); near the
 * end of the buffer the count passes the limit and the rest of the call writes
 * past 1024 bytes. The receipt names both cases it measured.
 *
 * Wide arguments are converted through the ANSI code page, 1252
 * (runtime/sa2_codepage.c), exactly where 1252 has the character. Where it
 * does not, this writes '?'. Whether the oracle's user32 applies the best fit
 * the runtime's WideCharToMultiByte has carried since 2026-09-26 (U+0100 as
 * 'A') is not measured: Documentation/receipts/s4-wsprintf-oracle-v1.json's
 * only such character, U+4E00, has no best fit. SA2's one caller passes no
 * wide argument.
 *
 * The guest's memory is read and written only through sa2_peek and sa2_poke, so
 * harness/format_probe.c drives this over stubbed memory. It reads everything
 * before it writes, so when both the output and an argument are bad it names
 * the argument where USER32 would fault on the output first; a trap ends the
 * guest either way, and only the address in its note differs.
 */
#ifndef SA2_RUNTIME_FORMAT_H
#define SA2_RUNTIME_FORMAT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SA2_WSPRINTF_MAX 1024u      /* bytes, the terminator included */

/* Format `fmt` (a guest string) with the arguments at guest address `args`
 * into guest memory at `out`. Returns SA2_OK with what wvsprintfA returns in
 * *ret, or SA2_E_RANGE with the guest address it could not read or write in
 * *fault. Natively that is an access violation inside USER32, which the
 * import stub turns into a trap rather than a truncated string. */
int sa2_wvsprintf(uint32_t out, uint32_t fmt, uint32_t args, uint32_t *ret, uint32_t *fault);

#ifdef __cplusplus
}
#endif

#endif
