// sa2_zip.h: the installer's ZIP route. A ZIP of the user's legally obtained game folder is
// verified against the hash table and extracted into the installer's own store (sa2_store.h),
// with no manual rearrangement: the game may sit at the top of the archive, under a
// "Sonic Adventure 2/" folder, or under any deeper folder.
//
// What sa2_zip_install does, in order:
//
//  1. Reads the end-of-central-directory record, ZIP64's when present, and the central
//     directory, which it trusts over the local headers (an archive with data descriptors has
//     no sizes there). An archive that is not a ZIP is named by its format (7z, RAR, gzip, xz,
//     zstd, tar); a split or multi-disk archive is refused.
//  2. Reads each name as UTF-8: Info-ZIP's Unicode Path field (0x7075) when its CRC-32 matches
//     the name's bytes, else the bytes when they are valid UTF-8 (general-purpose bit 11, and
//     macOS, which writes UTF-8 without the bit), else code page 437, which Windows Explorer,
//     7-Zip and Info-ZIP write for a name without bit 11. Folds '\' to '/', ignores directory
//     entries, __MACOSX/, "._*" files and the desktop's own .DS_Store, Thumbs.db and
//     desktop.ini. Two entries whose names differ only in case (the runtime's VFS is
//     case-blind), or not at all, are refused where the route reads one of them or finds the
//     root by it; anywhere else (a Mod Loader's mods/, say) they are skipped with the rest.
//  3. Finds the root: the one folder holding both sonic2app.exe and
//     resource/gd_PC/DLL/Win32/Data_DLL.dll, case-blind. None is "not SA2"; two are listed and
//     refused.
//  4. Matches every table entry (sa2_install.h) to an archive entry case-blind, and applies the
//     user's policy (Documentation/decisions/2026-10-11-S7-user-answers.md, answer 3) through
//     sa2_install_refusals: a required file missing or changed refuses, and is named; a renamed
//     *_orig original whose check passes is stored under the canonical name and recorded as
//     SubstitutedOrig; the Mod Loader's extras are skipped. A size that differs from the table
//     refuses before any byte is written (the executable apart: it is checked by sections).
//
//     The *_orig rule differs from the folder route's on purpose. The folder route leaves the
//     files where they are, so it takes an original only where the runtime's own redirect
//     (runtime/sa2_module.h) would map it: a present .dll naming itself something else, with
//     one sibling naming itself as asked. This route copies the original's verified bytes to the
//     canonical name, so the runtime reads the named file and needs no redirect: any table file
//     may be backed by its *_orig, whether the named file is changed or absent, and the store
//     holds no *_orig file. A SubstitutedOrig here is provenance (the record's "from"), not a
//     substitution the runtime makes.
//  5. Refuses, by name, an entry it would read that is encrypted or compressed with a method
//     other than 0 (stored) and 8 (deflate).
//  6. Names every destination from the table's canonical path, never from the archive. Every
//     other file under the root is classed by the folder route's rule for an extra
//     (sa2_install.h, sa2_install_extra_allowed), so the same tree gets the same verdict by
//     either route. Outside resource/ it is skipped, or listed as a user file (Config/,
//     SAVEDATA/). Under resource/ a save is a user file; user config (*.ini, *.cfg, which
//     install-hashes-v1's rules leave unhashed; the shipped StageSelectConfig.ini) is stored,
//     as the folder route lets the runtime read it; an unused *_orig original of a table file
//     is skipped; anything else refuses, by name, before extraction: the game probes resource/
//     for files a clean install lacks (a mod's forMovie.pak), so it would be read unverified.
//     The stored config names are the only names the archive chooses: they pass
//     sa2_store_safe_relpath and take the table's spelling of every directory they share with
//     it. User files are listed and not written: they belong in the overlay, not in the store
//     of verified files. With Sa2ZipOptions::user_dir set (the overlay), the user
//     files are written there, under the same guard and never over a file already there, once
//     every store file has verified and before the record is written: a refused run writes none.
//  7. Checks free space for what it will write, then extracts each file in one pass: inflate,
//     CRC-32 and SHA-256 together into a temporary file of its own under
//     "<store>.partial/.sa2-tmp/", fsync, close, check, rename. No name the archive chooses is
//     a temporary name. The executable is checked by sa2_exe_check_file (the DRM ruling),
//     every other table file by its size and SHA-256, an extra by its CRC-32 (it has no table
//     hash). A CRC or inflate failure is reported as damage, apart from a hash mismatch.
//  8. Builds the store as "<store>.partial", marked as this installer's from its first write
//     (sa2_store.h), writes its record last, and renames it to "<store>". A run that stops part
//     way, or is refused after extraction began, leaves the partial store; the next run keeps
//     each file in it that verifies again and removes everything else, so a retry converges on
//     the same store. A "<store>.partial" without the marker, and not empty, is refused and
//     left as it is.
//
// It returns true when "<store>" exists and holds a verified game. An existing "<store>" that
// this route wrote (its record says so) is replaced only once its successor is complete: moved
// to "<store>.old", the new one renamed in, the old one removed; a run that stops between the
// renames is repaired by the next. Any other directory there, or at "<store>.old", is never
// written over.
//
// On false, `report.error` says why and `report.failure` is the Sa2InstallFailure: NOT_FOUND
// (not a ZIP, or no game in it), AMBIGUOUS (the game twice), REFUSED (a file the game reads is
// missing or changed, or the archive's form is refused: encrypted, split, an unread method,
// duplicate or unsafe names, a store in the way), IO (a host read or write failed, free space,
// or the archive is damaged or truncated). `report.files` lists every table entry with what
// the archive showed of it before the run stopped; an entry the run stopped before reading is
// Ok only provisionally, with the detail "not read: ...", and Sa2ZipDetail::files_verified is
// false.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "sa2_install.h"

struct Sa2ZipOptions {
    unsigned threads = 0;  // extraction threads; 0 = the hardware's count, at most 8
    // Bytes free on the store's volume, for the tests; UINT64_MAX asks the volume.
    uint64_t free_bytes_override = UINT64_MAX;
    // Where the archive's user files (Config/, SAVEDATA/) go, at their paths under the game
    // folder: the runtime's overlay. Empty: they are listed and not written. A file already
    // there is kept, never overwritten.
    std::string user_dir;
};

struct Sa2ZipDetail {
    std::string root;                // archive folder holding the game ("" = the top)
    uint64_t archive_bytes = 0;
    size_t entries = 0;              // central-directory entries
    bool zip64 = false;
    bool data_descriptors = false;   // some entry has general-purpose bit 3 set
    std::vector<int> methods;        // methods of the entries read, ascending
    std::vector<std::string> extracted_extras;  // store paths of unhashed resource/ files
    std::vector<std::string> skipped;           // archive names under the root not written
    std::vector<std::string> user_files;        // Config/ and SAVEDATA/ names in the archive
    std::vector<std::string> user_files_written;  // of those, written into the user_dir
    std::vector<std::string> user_files_kept;     // of those, not written: the user_dir has one
    std::vector<std::string> damaged;           // "name: why" for entries that failed to read
    size_t resumed = 0;              // files a previous run left verified in the partial store
    size_t extracted = 0;            // files this run inflated or copied into the store
    uint64_t bytes_written = 0;
    uint64_t bytes_needed = 0, bytes_free = 0;
    std::string store_record;        // path of the store's validation record, on success
    bool files_verified = false;     // every table file the archive holds was read and checked
    size_t names_cp437 = 0;          // entry names read as code page 437
    size_t names_unicode_field = 0;  // entry names taken from the Unicode Path field
    unsigned fs_retries = 0;         // Win32 renames and removals retried (a scanner's hold)
};

bool sa2_zip_install(const std::string& zip, const std::string& store_dir,
                     const Sa2InstallTable& table, Sa2InstallReport& report);

bool sa2_zip_install(const std::string& zip, const std::string& store_dir,
                     const Sa2InstallTable& table, Sa2InstallReport& report,
                     const Sa2ZipOptions& options, Sa2ZipDetail* detail);
