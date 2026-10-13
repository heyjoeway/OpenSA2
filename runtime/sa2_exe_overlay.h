/* SA2 recompiled runtime: the shipped executable and its code-section overlay (S7).
 *
 * The user's DRM ruling of 2026-10-11 (Documentation/decisions/2026-10-11-S7-drm-unwrap-
 * ruling.md): no unwrap step ships. The player's shipped `sonic2app.exe` is loaded as-is.
 * SteamStub has encrypted its `.text` and added a `.bind` section; every other section is
 * byte-identical to the unpacked file's at the same VA (Documentation/receipts/s7-drm-unwrap-
 * v1.json). The lifted code never executes guest `.text`, but the runtime reads some of its
 * bytes while the game runs: the jump tables lifted code loads before `__remill_jump`, and the
 * instructions runtime/sa2_emulate.cpp decodes. The release build carries exactly those bytes,
 * taken from the unpacked file at build time as the lifted code is, in a generated table this
 * header describes. `tools/s7/text_readset.py` writes the table under
 * private/generated/s7/exe-overlay/, never tracked (AGENTS.md rule 9: it holds code bytes of the
 * game), and a build links it only when it asks to (tools/s4/link_s4.py `--exe-overlay`).
 *
 * The table installs itself (`sa2_exe_overlay_install`, from a constructor in the generated
 * file) and so becomes guest_memory.c's shipped-executable loader. Whether the file handed to
 * sa2_guest_init is the shipped executable is said by the host, never guessed: see
 * guest_memory.h, "the shipped executable" (sa2_guest_select_shipped, SA2_EXE_KIND=shipped).
 *
 * What the loader does with the shipped file, in this order, writing no guest memory until
 * every check has passed:
 *
 *   1. Parses the header fields the runtime and the guest read -- the MZ and PE signatures,
 *      e_lfanew, the section count, the optional header's size, magic, image base, image size
 *      and header size, the number of data directories, the import and resource directory
 *      entries the runtime reads, and the CLR directory entry the C runtime's start-up reads
 *      (its managed-application check) -- and holds each to the table's value for the shipped
 *      file (`shipped`).
 *   2. Holds each section entry's VA, virtual size, raw size and characteristics to the
 *      table's, in the table's order, and each section's mapped length (its file bytes, at most
 *      its virtual size, as guest_memory.c maps them) to the table's.
 *   3. Hashes the mapped bytes of every section of kind DATA -- every section but `.text` and
 *      the unmapped `.bind` -- and holds each sha256 to the table's.
 *
 *   Then it maps: the header page as-is; every DATA section as-is; not `.bind`. Guest `.text`
 *   is filled with SA2_EXE_TEXT_FILL (int3, the compiler's own padding byte, which no emulated
 *   form starts with and no jump table can hold as an arm), and the overlay's runs are copied
 *   over it. The image's size is the unpacked file's, so `.bind`'s range is free space. The
 *   guest's section list carries the unpacked file's section names (`guest_name`), so every
 *   note the runtime writes from it (sa2_guest_region_of) reads as the unpacked load's.
 *
 *   The header page is mapped as-is but verified only where steps 1 and 2 read it, as the
 *   ruling asks ("the header fields the runtime reads, never the whole file"). The poison tier
 *   keeps a census of the header bytes the guest reads (guest_memory.h, the header census), and
 *   `sa2_exe_overlay_report` names any it read that steps 1 and 2 do not verify
 *   (`header_unverified`).
 *
 * For the poison tier (guest_memory.h) `.text` is a byte-granular range: only the overlay's
 * runs count as written there, and the pages are written exactly as the unpacked file's
 * mapping writes them. A lifted read of any other `.text` byte is then a poison read: trapped
 * under SA2_GUEST_POISON=1, counted under =2 and reported by `sa2_exe_overlay_report`.
 *
 * Every refusal prints one line to stderr that starts `SA2_EXE_OVERLAY_REFUSED <code>:` and
 * returns SA2_E_OVERLAY. A refusal of sa2_guest_init's, whichever file refuses it, is kept for
 * `sa2_exe_overlay_refusal` (guest_memory.h sa2_guest_refusal, which holds it so that a build
 * without this file keeps `switch` and `no-table` too); a refusal of sa2_exe_overlay_verify's is
 * kept for `sa2_exe_overlay_verify_refusal` and leaves the last init's alone. The codes:
 *
 *   switch         SA2_EXE_KIND holds something other than shipped, unpacked or nothing
 *   no-table       the build links no table (guest_memory.c, before anything is mapped)
 *   table          the installed table is not well formed (its runs, extents or version)
 *   not-pe         the file is not a PE32 image the header fields can be read from
 *   header-field   a header field the runtime or the guest reads is not the table's value
 *   section-table  a section entry, or a section's mapped length, is not the table's
 *   section-hash   a DATA section's mapped bytes do not hash to the table's sha256
 *   image          the table's image does not fit the guest layout
 */
#ifndef SA2_RUNTIME_EXE_OVERLAY_H
#define SA2_RUNTIME_EXE_OVERLAY_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The table layout below. tools/s7/text_readset.py writes TABLE_VERSION; a table of another
 * layout is refused (`table`), not read. */
#define SA2_EXE_OVERLAY_TABLE_VERSION 2u
#define SA2_EXE_TEXT_FILL             0xCCu
#define SA2_EXE_OVERLAY_MAX_SECTIONS  32u

/* What the loader does with a section of the shipped file. */
enum { SA2_EXE_SEC_TEXT = 1,       /* filled, then overlaid; its bytes are not read */
       SA2_EXE_SEC_DATA = 2,       /* verified by hash, then mapped as-is */
       SA2_EXE_SEC_UNMAPPED = 3 }; /* not mapped and not verified: SteamStub's `.bind` */

/* One run of guest `.text` the overlay holds: [va, va + len), whose bytes are
 * `bytes[offset .. offset + len)` of the table. Runs are sorted, disjoint and not adjacent. */
typedef struct {
    uint32_t va, len, offset;
} sa2_exe_overlay_run_t;

typedef struct {
    char        name[9];            /* the shipped file's name for it, as it stands */
    char        guest_name[9];      /* the unpacked file's name for it (TEXT, DATA): the name
                                       the guest's section list carries */
    uint32_t    va;                 /* guest VA: image base plus the entry's RVA */
    uint32_t    virtual_size, raw_size, characteristics;
    uint32_t    mapped;             /* the file bytes the loader maps: raw size, at most vsize */
    int         kind;               /* SA2_EXE_SEC_* */
    const char *sha256;             /* of the `mapped` bytes, kind DATA; "" otherwise */
} sa2_exe_overlay_section_t;

/* The header fields the runtime reads (guest_memory.c pe_parse, sa2_imports_bind and the
 * dialog resource lookup) and the guest reads (the C runtime's start-up: the data directory
 * count and the CLR entry), each as one file holds it. */
typedef struct {
    uint32_t e_lfanew, sections_count, optional_header_size, magic;
    uint32_t image_base, size_of_image, size_of_headers;
    uint32_t rva_count;                                    /* NumberOfRvaAndSizes */
    uint32_t import_directory[2], resource_directory[2];   /* RVA, size */
    uint32_t clr_directory[2];                             /* DataDirectory[14]: RVA, size */
} sa2_exe_overlay_header_t;

typedef struct {
    uint32_t    version;            /* SA2_EXE_OVERLAY_TABLE_VERSION */
    const char *generated_by;       /* "tools/s7/text_readset.py" */
    const char *unpacked_sha256;    /* the file the runs' bytes were taken from */
    const char *shipped_sha256;     /* the file the hashes and header values were taken from;
                                       a record of provenance, never checked: the loader
                                       verifies what it reads, never the whole file */
    const char *readset_sha256;     /* of the run list (text_readset.py `readset_digest`) */
    const char *bytes_sha256;       /* of `bytes`, the runs' bytes in order */
    const char *omitted;            /* "" for a release table; what a control table leaves out */
    sa2_exe_overlay_header_t shipped, unpacked;
    const sa2_exe_overlay_section_t *sections;   /* the shipped file's, in its header's order */
    uint32_t    section_count;
    uint32_t    text_va, text_mapped;            /* the `.text` the unpacked file's loader maps */
    const sa2_exe_overlay_run_t *runs;
    uint32_t    run_count;
    const uint8_t *bytes;
    uint32_t    byte_count;
} sa2_exe_overlay_table_t;

/* Make `t` the shipped-executable loader's table (guest_memory.h sa2_guest_shipped_loader).
 * The generated table calls this from a constructor; a host may call it itself. A table that
 * is not well formed is still installed and makes every load refuse (`table`), so a bad table
 * is named rather than read as "no table". SA2_OK, or SA2_E_OVERLAY for a bad table. */
int sa2_exe_overlay_install(const sa2_exe_overlay_table_t *t);
const sa2_exe_overlay_table_t *sa2_exe_overlay_table(void);   /* NULL when none is installed */

/* Steps 1 to 3 above, and the guest layout check, on the whole file `file`/`n`, without
 * mapping anything: what the loader would refuse, refused the same way and by the same line.
 * For a host that verifies the shipped executable before running it (the installer), so the
 * file it accepts is exactly the file the runtime accepts. SA2_OK or SA2_E_OVERLAY. */
int sa2_exe_overlay_verify(const uint8_t *file, size_t n, const char *path);

/* 1 when the last sa2_guest_init loaded the shipped executable with the overlay. Like the
 * refusal, it outlasts sa2_guest_shutdown and the next init resets it. */
int sa2_exe_overlay_loaded(void);
/* The last sa2_guest_init's refusal line, without its newline; "" when it refused nothing. */
const char *sa2_exe_overlay_refusal(void);
/* The last sa2_exe_overlay_verify's refusal line; "" when it passed or has not run. */
const char *sa2_exe_overlay_verify_refusal(void);

/* One JSON object describing the table and the last load: the table's digests and counts,
 * what it omits, the file loaded, the poison tier's byte-granular count for `.text`
 * (guest_memory.h sa2_guest_fine): the reads of a `.text` byte the overlay does not hold, and
 * its header census (sa2_guest_head_read): the header bytes the guest read, as file offsets,
 * and those of them the loader does not verify (`header_unverified`, empty when it verifies
 * every one). Both counts are of the memory intrinsics' tests, so only under the poison tier. */
void sa2_exe_overlay_report(FILE *out);

#ifdef __cplusplus
}
#endif
#endif
