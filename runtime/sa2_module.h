/* Modules the guest loads with LoadLibrary that are data, mapped and never run.
 *
 * The game keeps its asset tables in a second PE, the data DLL, and asks it for
 * 436 of them by name through GetProcAddress against a handle it holds at
 * 0x01AF0220 (Documentation/receipts/s4-data-dll-v1.json). Design 3.5 classes
 * LoadLibraryA and GetProcAddress as OS services, which S4 stubs, and 1.1 has
 * the assets come from the user's own installation; this is that stub.
 *
 * What it does, and the three rules it will not bend:
 *
 *   * **It executes nothing.** DllMain is not called, TLS callbacks are not run
 *     (a module that has any is refused), and imports are not resolved. A module
 *     is accepted only if every export that lands in an executable section is a
 *     single `ret`, and each of those becomes a host native that performs the
 *     `ret`; any other code export refuses the whole module, loudly. The data
 *     DLL's two code exports, `prolog_motion` and `epilog_motion`, share one RVA
 *     whose byte is 0xC3.
 *   * **The file that backs the handle is the one that names itself so.** A PE's
 *     export directory records the module's own name. The asked file is used
 *     unless it is a valid DLL whose export directory names it something else;
 *     then the one sibling in the same directory that names itself as asked is,
 *     and the substitution is recorded. On a Mod Loader installation the file the
 *     guest names is the loader's shim, which calls itself `SA2ModLoader.dll`,
 *     and the game's data is beside it as `Data_DLL_orig.dll`, which calls itself
 *     `Data_DLL.dll`; on an unmodded one there is no substitution to make. No
 *     file name is hard-coded: zero or two candidates refuse, and a file that is
 *     not a DLL at all is ERROR_BAD_EXE_FORMAT, as Windows says, not a reason to
 *     go looking for another.
 *   * **The image is relocated, not placed.** It is mapped into a zeroed block of
 *     the guest heap arena, 64 KiB aligned, and every base relocation applied; an
 *     image that would need relocating and has no relocations is refused rather
 *     than mapped with its pointers wrong. Its preferred base (0x10000000) is not
 *     chased: under the Mod Loader the shim already holds that address natively,
 *     so the real image is relocated there too, and matching a base the native
 *     run does not have buys nothing.
 *
 * The HMODULE is the mapped base, as on Windows. A module whose count falls to
 * zero is unloaded as far as every lookup can tell, and a later load maps a
 * fresh copy -- the file's bytes, relocated again -- at the same base, which is
 * what a guest that wrote into the old image would see on Windows.
 */
#ifndef SA2_RUNTIME_MODULE_H
#define SA2_RUNTIME_MODULE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SA2_MODULE_MAX        4u
#define SA2_MODULE_PATH_MAX   260u
#define SA2_MODULE_CODE_MAX   8u

typedef struct {
    char     asked[SA2_MODULE_PATH_MAX];      /* the path the guest passed */
    char     backing[SA2_MODULE_PATH_MAX];    /* guest-relative path of the file mapped */
    char     self_name[64];                   /* the export directory's own name */
    int      substituted;                     /* backing is not the asked file */
    uint32_t base, size, image_base;          /* mapped base, SizeOfImage, preferred base */
    uint32_t file_bytes;
    uint32_t relocations;                     /* HIGHLOW fixups applied per mapping */
    uint32_t exports, code_exports;           /* named exports; those that are code */
    uint32_t code_va[SA2_MODULE_CODE_MAX];    /* distinct `ret` natives registered */
    uint32_t code_count;
    uint32_t code_calls;                      /* calls that reached one of them */
    uint32_t imports_unresolved;              /* import descriptors left as the file has them */
    uint32_t entry_rva;                       /* AddressOfEntryPoint, never called */
    uint32_t refs;                            /* LoadLibrary count minus FreeLibrary count */
    uint32_t mappings;                        /* times the file's bytes were laid down */
    uint32_t lookups, misses;                 /* GetProcAddress answered / not answered */
} sa2_module;

/* LoadLibrary for a module that is not an OS module the runtime stands in for.
 * Returns the mapped base, or 0 with `why` saying which rule refused it and
 * `*win32_error` the code Windows would set (ERROR_MOD_NOT_FOUND,
 * ERROR_BAD_EXE_FORMAT, ERROR_NOT_ENOUGH_MEMORY). A path the guest file system
 * refuses, and a module this loader will not map without executing it, trap
 * instead of returning: neither is an answer the guest should route around. */
uint32_t sa2_module_load(const char *guest_path, char *why, size_t why_cap,
                         uint32_t *win32_error);

/* The loaded module mapped at `base`, or NULL (also after its count reached 0). */
const sa2_module *sa2_module_by_base(uint32_t base);

/* The loaded module the guest named this way (path, case and a ".dll" suffix
 * ignored, as GetModuleHandle does), or 0. */
uint32_t sa2_module_by_name(const char *name);

/* GetProcAddress: the address of an export by name, or by ordinal when `name`
 * is NULL. 0 when the module does not export it. */
uint32_t sa2_module_export(uint32_t base, const char *name, uint32_t ordinal);

/* FreeLibrary: lowers the count. At zero every lookup treats the module as
 * unloaded and the next load maps it afresh; the bytes stay reserved so the
 * base can be reused. Returns 0 for a base that is not a loaded module. */
int sa2_module_free(uint32_t base);

uint32_t          sa2_module_count(void);
const sa2_module *sa2_module_at(uint32_t i);

/* The last refusal, for the run's report; "" when nothing was refused. */
const char *sa2_module_last_refusal(void);

#ifdef __cplusplus
}
#endif

#endif
