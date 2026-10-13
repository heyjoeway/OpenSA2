// sa2_install.h: the installer's shared types, the hash table it verifies against, and the
// checks every route (Steam, folder, ZIP) applies to one file.
//
// The table is generated, never typed: tools/s7/install_table.py writes it from
// Documentation/receipts/install-hashes-v1.json into private/generated/s7/install/ (it carries
// the receipt's id and sha256, so a config or a store made under another table is detected).
//
// Entries are "required" (resource/** and the executable: the runtime reads them) or "known"
// (the rest of the receipt: Launcher.exe, steam_api.dll, SteamMetricsSDK.dll, which the runtime
// never reads; they are checked only to report a modified install).
//
// The executable is never checked as a whole file (the user's DRM ruling of 2026-10-11,
// Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md): its entry has `exe` set, its
// sha256 is all zero, and it passes when the header fields the runtime reads and every section
// the runtime reads from the file (all but the code section the release build overlays and the
// section SteamStub adds) match. sa2_install_check_file does the right thing for both kinds.
//
// The user's policy for a modified install (Documentation/decisions/2026-10-11-S7-user-answers.md,
// answer 3: "The game runs only on verified files, under every route"): a required file that is
// missing or fails its check refuses, and is named; a renamed *_orig original whose hash matches
// is used, and the substitution recorded, when the runtime reads that original in place of the
// named file. Extras (files the table does not list) are each classed by
// sa2_install_extra_allowed and listed. Outside resource/ they are skipped: the Mod Loader's
// mods/, Codes.lst and loader DLL, redist/, the launcher's Config/. Under resource/ the game
// probes for files a clean install lacks (the boot run asks for resource/gd_PC/prs/forMovie.pak,
// among others), so an extra there would be read unverified, and it refuses, named, unless it is
// a user save, user config, or an unused *_orig original.
//
// The first six declarations are the contract the ZIP unit codes against; fields are only ever
// added at the end of a struct.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

// One section of the executable as the runtime's loader (runtime/guest_memory.c) maps it.
// `hashed` sections are compared by the SHA-256 of the `hashed_bytes` file bytes the loader
// copies (min(raw size, virtual size) from the section's raw pointer); the others only by their
// section-table entry, which the loader reads.
struct Sa2ExeSection {
    const char* name;            // informational: the loader keys sections by VA, never name
    uint32_t va;                 // RVA, as the section table has it
    uint32_t virtual_size;
    uint32_t raw_size;
    uint32_t characteristics;
    bool hashed;
    uint32_t hashed_bytes;
    uint8_t sha256[32];
    const char* why_not_hashed;  // "" when hashed
};

// The header fields the runtime reads (pe_parse and the section loop, the import binding and
// the resource lookup; tools/s7/drm_unwrap.py's header_fields), and the sections.
struct Sa2ExeCheck {
    uint32_t e_lfanew;
    uint16_t sections_count;
    uint16_t optional_header_size;
    uint16_t magic;
    uint32_t image_base;
    uint32_t size_of_image;
    uint32_t size_of_headers;
    uint32_t import_rva, import_size;
    uint32_t resource_rva, resource_size;
    const Sa2ExeSection* sections;
    size_t count;
    const char* ruling;          // the decision record this check implements
};

struct Sa2InstallEntry {
    const char* path;            // canonical spelling, '/'-separated, relative to the game folder
    uint64_t size;
    uint8_t sha256[32];
    bool required;
    const Sa2ExeCheck* exe;      // non-null for the executable: sha256 is zero, check by sections
};

struct Sa2InstallTable {
    const char* id;              // the receipt's id, "install-hashes-v1"
    const char* receipt_sha256;  // hex SHA-256 of the receipt file the table was generated from
    const Sa2InstallEntry* entries;
    size_t count;
    const char* generated_by;    // "tools/s7/install_table.py"
    size_t required_count;
};

struct Sa2FileResult {
    std::string path;            // the entry's canonical path
    enum Kind { Ok, Missing, Modified, SubstitutedOrig } kind;
    std::string detail;          // why Modified, or the *_orig file that backs SubstitutedOrig
    bool required = true;
    std::string found_as;        // the spelling on disk, when it differs from `path`
    bool io_error = false;       // Missing because the host refused to read it (detail says why)
};

// A file the table does not list, found beside the verified ones.
struct Sa2Extra {
    std::string path;            // as spelled on disk, relative to the game folder
    std::string why;             // why it may stay (sa2_install_extra_allowed), or why it refuses
    bool refused = false;
    bool io = false;             // refused because it cannot be read (an unreadable folder)
};

struct Sa2InstallReport {
    std::vector<Sa2FileResult> files;
    size_t ok = 0, missing = 0, modified = 0, extras = 0;
    bool mod_loader = false;
    std::string error;
    size_t substituted = 0;
    std::vector<std::string> markers;        // the Mod Loader markers found
    size_t known_missing = 0, known_modified = 0;  // of the above, entries that are not required
    int failure = 0;             // a Sa2InstallFailure when the route failed; 0 otherwise
    std::vector<Sa2Extra> extra_files;       // every extra, classed (the folder routes fill it);
                                             // `extras` counts those not refused
};

// Why a route failed. The values are sa2-install's exit codes.
enum Sa2InstallFailure {
    SA2_FAIL_NONE = 0,
    SA2_FAIL_REFUSED = 3,        // a file the game reads is missing or changed (named in `files`),
                                 // or an extra or overlay file it would read unverified
    SA2_FAIL_NOT_FOUND = 4,      // no game here: no Steam install, not a game folder, not SA2's ZIP
    SA2_FAIL_IO = 5,             // the host refused a read or a write, or the archive is damaged
    SA2_FAIL_AMBIGUOUS = 6,      // more than one candidate (installs, or game roots in one ZIP)
};

const Sa2InstallTable& sa2_install_table();
bool sa2_sha256_file(const std::string& path, uint8_t out[32]);

// ---- shared checks (sa2_verify.cpp) ----

// ASCII case fold, the runtime's rule (runtime/sa2_files.h, decision 4).
std::string sa2_install_fold(const std::string& s);

// Check one file on disk against one entry: size and whole-file SHA-256, or, for the
// executable, sa2_exe_check_file. Ok or Modified (with `detail`), or Missing when it cannot be
// read; then `io_error` is set and `detail` carries the host's reason.
Sa2FileResult::Kind sa2_install_check_file(const Sa2InstallEntry& e, const std::string& host_path,
                                           std::string* detail, bool* io_error = nullptr);

// The executable's check of the DRM ruling. False, with `why`, on the first field or section
// that does not match.
bool sa2_exe_check_file(const Sa2ExeCheck& c, const std::string& host_path, std::string* why);
bool sa2_exe_check_bytes(const Sa2ExeCheck& c, const uint8_t* data, size_t n, std::string* why);

// The Mod Loader's markers, as tools/hash_install.py lists them.
extern const char* const SA2_MOD_LOADER_MARKERS[];
extern const size_t SA2_MOD_LOADER_MARKER_COUNT;

// "dir/Name_orig.ext" for "dir/Name.ext": the Mod Loader's renamed original.
std::string sa2_install_orig_name(const std::string& path);

// What runtime/sa2_module.c makes of a file it is asked to load as a DLL. `loads` is pe_parse's
// verdict (an i386 PE32 image with IMAGE_FILE_DLL, its optional header, data directories and
// section table inside the file, a power-of-two section alignment); a file that fails it is
// refused with ERROR_BAD_EXE_FORMAT and no sibling is searched. `self_name` is self_name's: the
// export directory's own name, when the directory has a size, its 40 bytes and the name are
// backed by the file, and the name is under 64 bytes; "" otherwise, and the file is mapped as
// asked.
struct Sa2PeDll {
    bool loads = false;
    std::string why;             // why it does not load
    std::string self_name;
};
Sa2PeDll sa2_pe_dll(const std::string& host_path);
Sa2PeDll sa2_pe_dll_bytes(const uint8_t* data, size_t n);

// runtime/sa2_module.c's same_module_name: path, case and a trailing ".dll" ignored.
bool sa2_same_module_name(const std::string& a, const std::string& b);

// Whether an extra (a file the table does not list, `rel` as spelled under the game folder) may
// stay beside the verified files: the reason it may, or nullptr when it refuses. Outside
// resource/ every extra may ("outside resource/"). Under resource/ only a file in a SAVEDATA
// folder ("a user save"), a *.ini or *.cfg ("user config"; tools/hash_install.py's exclusions,
// which install-hashes-v1 records in its rules), and a *_orig original of a table file that
// backs nothing ("an unused original": the runtime opens *_orig names only as a DLL's sibling,
// which the substitution check covers). A substitution's backing is not an extra.
const char* sa2_install_extra_allowed(const std::string& rel, const Sa2InstallTable& table);
// The first three of those: a file of the user's own (outside resource/, a save, user config),
// the only kinds an overlay may hold under resource/. nullptr otherwise.
const char* sa2_install_user_file(const std::string& rel);

// Recount ok/missing/modified/substituted/known_* from `files`.
void sa2_install_recount(Sa2InstallReport& r);

// The policy: every reason this report is refused, each naming a file ("" list = accepted).
std::vector<std::string> sa2_install_refusals(const Sa2InstallReport& r);

// Hex <-> bytes.
std::string sa2_hex(const uint8_t* p, size_t n);
