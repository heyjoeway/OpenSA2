/* Code page 1252, the ANSI code page this runtime implements, as the one
 * published table both directions read, and the character data the C
 * runtime builds its tables from for the characters of that code page.
 *
 * Its own unit because two parts of the runtime convert through it: the
 * conversion imports in runtime/sa2_imports.c, and the wsprintf formatter in
 * runtime/sa2_format.c, which turns a wide %C or %S argument into the ANSI code
 * page as USER32 does -- the oracle's user32 writes U+20AC as 0x80, not '?'.
 * A second copy of the table would be a second place for a position in
 * 0x80..0x9F to be wrong.
 */
#ifndef SA2_RUNTIME_CODEPAGE_H
#define SA2_RUNTIME_CODEPAGE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* A 1252 byte as UTF-16. Every byte has a value; the five unassigned positions
 * map to their own code points, as Windows' table does. */
uint16_t sa2_cp1252_to_wide(uint8_t b);

/* UTF-16 to 1252: 1 and the byte where the table has the character, else 0
 * and '?', which is what Windows does with no best-fit table
 * (WC_NO_BEST_FIT_CHARS). */
int sa2_cp1252_from_wide(uint16_t w, uint8_t *out);

/* UTF-16 to 1252 as WideCharToMultiByte converts without WC_NO_BEST_FIT_CHARS:
 * 1 and the byte where the table has the character; 2 and Wine's best-fit
 * byte for a character it lacks (U+0100 is 'A', U+0191 is 0x83, the byte of
 * U+0192, its lower case); else 0 and '?'. The best fit is c_1252.nls's
 * (sa2_chardata.h). The C runtime turns LCMapStringW's upper case back into
 * bytes with it: U+0192 upper-cases to U+0191, which the code page lacks,
 * and native's _mbcasemap holds 0x83 for it. */
int sa2_cp1252_best_fit(uint16_t w, uint8_t *out);

/* The character data GetStringTypeW and LCMapStringW answer with, as Wine
 * answers it, for the characters this runtime has it for: U+0000..U+00FF, the
 * 27 characters code page 1252 has above them, U+0191 (the upper case of
 * U+0192) and U+F8F5 (code page 936's byte 0xFF, which the C runtime
 * classifies in the Chinese-simplified locale). sa2_chardata.h holds it,
 * generated from the oracle's Wine by tools/s4/ctype_check.py emit and held
 * to Wine's files and to the native trace by its `tables` check.
 *
 * sa2_char_ctype1: the CT_CTYPE1 flags, or 0 for a character outside that
 * set, which no character in it has. sa2_char_case: 1 and the upper or lower
 * case for a character in the set; 0 and the character itself outside it. */
uint16_t sa2_char_ctype1(uint16_t w);
int sa2_char_case(uint16_t w, int upper, uint16_t *out);

#ifdef __cplusplus
}
#endif

#endif
