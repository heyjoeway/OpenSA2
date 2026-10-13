// sa2_scan.h: a game folder, scanned and verified under the user's policy.
//
// The folder is indexed once (every file below it, with size and mtime), case-blind the way the
// runtime resolves names (runtime/sa2_files.h, decision 4: the exact spelling first, else the one
// ASCII-case-folded match; two names differing only in case are ambiguous and refuse). A
// symbolic link inside the folder that leads out of it is not followed, as the runtime refuses
// one. Every table entry is then checked in parallel (sa2_install_check_file), and the policy of
// sa2_install.h applied: a changed or missing required file refuses, named; a file the host
// will not read is an I/O failure, not a refusal; every extra is classed and listed
// (sa2_install_extra_allowed: under resource/ only saves, user config and unused originals may
// stay); a *_orig original is accepted for a changed file when its hash matches and the runtime
// reads it in the named file's place (a DLL: the named file loads, names itself something else,
// and the one sibling that loads and names itself as asked is that original;
// runtime/sa2_module.c, sa2_pe_dll).
//
// Nothing here writes into the folder. sa2_tree_digest over the index before and after a run is
// the evidence that it did not (tools/s4/install_guard.py's method: names, sizes and mtimes).
#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "sa2_install.h"

struct Sa2TreeFile {
    std::string rel;         // '/'-separated, as spelled on disk
    uint64_t size = 0;
    int64_t mtime_ns = 0;
};

struct Sa2TreeIndex {
    std::string root;                                   // realpath of the folder
    std::vector<Sa2TreeFile> files;                     // sorted by rel
    std::vector<Sa2TreeFile> dirs;                      // every folder indexed ("" = the root), sorted
    std::unordered_map<std::string, size_t> exact;      // rel -> files index
    struct Dir {
        std::unordered_set<std::string> names;                           // as spelled
        std::unordered_map<std::string, std::vector<std::string>> folded; // folded name -> spellings
        void add(const std::string& n);
    };
    std::unordered_map<std::string, Dir> kids;          // dir rel ("" = root) -> its names
    std::unordered_map<std::string, bool> links_out;    // rel of a link leading out of the folder
    std::unordered_map<std::string, std::string> unreadable;  // rel of a folder that cannot be
                                                        // listed -> the host's reason
    std::vector<std::string> notes;                     // links not followed, unreadable dirs

    enum Lookup { Found, Absent, Ambiguous, Escape, Unreadable };
    // Resolves `rel` one component at a time, as runtime/sa2_files.c's resolve_under does: the
    // exact spelling first, else the one ASCII-case-folded match among the directory's names
    // (two are Ambiguous); a link out of the folder is Escape; a name sought in a folder that
    // could not be listed is Unreadable (it may be there). `idx` is set when Found.
    Lookup find(const std::string& rel, size_t& idx) const;
    bool has_dir(const std::string& rel) const;
    std::string host(size_t idx) const { return root + "/" + files[idx].rel; }

private:
    Lookup resolve(const std::string& rel, std::string& out) const;
};

// Indexes `folder`. False (with `error`) when it is not a directory, or (with `io` set) when it
// cannot be listed.
bool sa2_tree_index(const std::string& folder, Sa2TreeIndex& out, std::string* error, bool* io = nullptr);

// A digest of every file's name, size and mtime and every folder's name and mtime.
std::string sa2_tree_digest(const Sa2TreeIndex& idx);

// The folder holds a game: sonic2app.exe, and the data DLL (or its *_orig original).
bool sa2_tree_is_game(const Sa2TreeIndex& idx);

// Verifies an indexed folder against `table` and applies the policy: every table entry checked,
// every other file classed as an extra (sa2_install_extra_allowed), a link out of the folder or
// an unreadable folder under resource/ refused (the latter as an I/O failure, report.failure).
// `threads` 0 = all CPUs.
void sa2_verify_tree(const Sa2TreeIndex& idx, const Sa2InstallTable& table, unsigned threads,
                     Sa2InstallReport& report);

// What in the overlay the runtime would read in place of a verified file, or unverified: the
// runtime reads the overlay first (runtime/sa2_files.h, decision 3), for every name it resolves,
// a substituted DLL's siblings included (runtime/sa2_module.c). Refused: a copy of any table file
// or of any path in `also` (the manifest's rows: the files on disk that were verified, a
// substitution's backing among them); any file under resource/ but a user's own
// (sa2_install_user_file), since the game probes there for files the install lacks; an overlay
// or a folder in it that cannot be listed. An absent overlay holds none.
std::vector<std::string> sa2_overlay_shadows(const std::string& overlay, const Sa2InstallTable& table,
                                             const std::vector<std::string>& also);

// ---- the quick manifest: size and mtime of every verified file, for the check at launch ----

struct Sa2ManifestRow {
    std::string rel;         // the file on disk that was verified (the *_orig file when substituted)
    uint64_t size = 0;
    int64_t mtime_ns = 0;
};

struct Sa2Manifest {
    std::string table_id, receipt_sha256, root, verified_at;
    std::vector<Sa2ManifestRow> rows;
    std::vector<Sa2ManifestRow> dirs;   // every folder's mtime: a name added, removed or renamed
                                        // (a new spelling that would shadow a verified one) moves it
};

Sa2Manifest sa2_manifest_from(const Sa2TreeIndex& idx, const Sa2InstallTable& table,
                              const Sa2InstallReport& report, const std::string& verified_at);
std::string sa2_manifest_text(const Sa2Manifest& m);
bool sa2_manifest_parse(const std::string& text, Sa2Manifest& m, std::string* error);
// Every row whose file is gone or whose size or mtime moved, every folder whose mtime moved,
// and a table mismatch; empty = clean.
std::vector<std::string> sa2_manifest_quick_check(const Sa2Manifest& m, const Sa2InstallTable& table);
