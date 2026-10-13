// sa2_store.h: the installer's own storage for the ZIP route, and the few file-system calls the
// route needs, for POSIX and Win32 alike. Paths are UTF-8 on both: Win32 calls take them through
// MultiByteToWideChar, never the ANSI code page.
//
// A store is a directory laid out as the game folder: every table file under its canonical path
// (sa2_install.h), the unhashed shipped files under resource/, and the validation record,
// SA2_STORE_RECORD, written last. It is built as `<store>.partial` and renamed to `<store>`
// only once that record is durable, so a store that exists is complete, and a run that stopped
// part way leaves only the partial one, which the next run resumes from: every file in it was
// verified before it was renamed into place, and is verified again before it is kept.
//
// A partial store carries SA2_STORE_PARTIAL_MARKER from its first write until just before its
// rename, so a run never empties a "<store>.partial" it did not make. Files are written under
// SA2_STORE_TMP_DIR, one temporary name per file, and renamed to their destination: no name an
// archive can choose is ever a temporary name.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

// ------------------------------------------------------------------------- files

class Sa2File {
public:
    Sa2File() = default;
    Sa2File(const Sa2File&) = delete;
    Sa2File& operator=(const Sa2File&) = delete;
    ~Sa2File();

    bool open_read(const std::string& path, std::string* err);
    bool create(const std::string& path, std::string* err);  // write-only, truncating
    bool is_open() const;
    uint64_t size() const { return size_; }

    // Exactly `n` bytes at `off`; false, with `err`, on an error or a short read.
    // Safe to call from several threads on one handle.
    bool read_at(uint64_t off, void* buf, size_t n, std::string* err) const;
    bool write(const void* p, size_t n, std::string* err);
    bool sync(std::string* err);   // fsync / FlushFileBuffers
    bool close(std::string* err);  // reports the close's own error

private:
#ifdef _WIN32
    void* h_ = nullptr;
#else
    int fd_ = -1;
#endif
    uint64_t size_ = 0;
    std::string path_;
};

enum class Sa2FsKind { None, File, Dir, Other };

Sa2FsKind sa2_fs_kind(const std::string& path);           // never follows a final symlink
bool sa2_fs_mkdirs(const std::string& path, std::string* err);
// Rename, replacing an existing file at `to`. A directory is renamed only onto a free name.
// On Win32 both retry, a bounded number of times with a short backoff, a failure an antivirus
// scanner or indexer holding a just-closed file causes (access denied, a sharing or lock
// violation); sa2_fs_retry_count says how many retries this process made.
bool sa2_fs_rename(const std::string& from, const std::string& to, std::string* err);
bool sa2_fs_remove_file(const std::string& path, std::string* err);
unsigned sa2_fs_retry_count();
// Rename a file onto a name that is free, never replacing what is there: false with `*existed`
// set when something already is (MoveFileExW without replace on Win32; link and unlink on POSIX,
// or a check then a rename on a file system without hard links).
bool sa2_fs_rename_noreplace(const std::string& from, const std::string& to, bool* existed,
                             std::string* err);
bool sa2_fs_sync_dir(const std::string& path, std::string* err);  // a no-op on Win32
// Bytes free to this user on the volume holding `path`, or its nearest existing ancestor.
bool sa2_fs_free_bytes(const std::string& path, uint64_t* out, std::string* err);
// Every entry under `dir`, '/'-separated and relative to it; directories are not followed
// through symlinks, and a symlink is listed among `files`.
bool sa2_fs_walk(const std::string& dir, std::vector<std::string>* files,
                 std::vector<std::string>* dirs, std::string* err);
// Remove `dir` and everything under it, never following a symlink out of it.
bool sa2_fs_remove_tree(const std::string& dir, std::string* err);

// `a/b` for ("a", "b"), with '/' on both systems (Win32 takes either separator).
std::string sa2_path_join(const std::string& a, const std::string& b);
std::string sa2_path_parent(const std::string& p);

// ------------------------------------------------------------------------- the store

extern const char* const SA2_STORE_RECORD;    // "sa2recomp-store.json"
extern const char* const SA2_STORE_SCHEMA;    // "sa2-store/v1"
extern const char* const SA2_STORE_PART_SUFFIX;  // ".part", for the record being written
extern const char* const SA2_STORE_TMP_DIR;   // ".sa2-tmp": a partial store's temporary files
extern const char* const SA2_STORE_PARTIAL_MARKER;  // "sa2recomp-partial"

std::string sa2_store_partial_dir(const std::string& store_dir);  // "<store>.partial"

// Whether `name`, a top-level name in a store, is one the store itself uses (the record and its
// temporary, the marker, the temporary directory), compared case-blind.
bool sa2_store_reserved_name(const std::string& name);

// The partial store's marker: written (and synced, with its directory) as a run's first write
// into "<store>.partial"; present only in a partial store this installer made.
bool sa2_store_write_partial_marker(const std::string& partial_dir, std::string* err);
bool sa2_store_has_partial_marker(const std::string& partial_dir);

// The lexical guard for a name the archive chose (sa2_vfs_normalise's rules,
// runtime/sa2_files.c): '/'-separated, relative, no empty, "." or ".." component, no byte
// below 0x20 and none of : * ? " < > | \, no component ending in a dot or a space, no DOS
// device name, components of at most 255 bytes and 1024 in all. False, with `why`, otherwise.
bool sa2_store_safe_relpath(const std::string& rel, std::string* why);

struct Sa2StoreFile {
    std::string path;        // in the store
    uint64_t size = 0;
    std::string sha256;      // hex, of the bytes written
    std::string kind;        // "ok", "substituted-orig" or "extra"
    std::string from;        // the archive name it was read from, relative to the game root
};

struct Sa2StoreRecord {
    std::string table_id, receipt_sha256;
    std::string source_kind;    // "zip"
    uint64_t source_bytes = 0;  // the archive's size
    std::string source_root;    // the folder in the archive the game was found under ("" = top)
    std::vector<Sa2StoreFile> files;  // table files, in table order, then extras by name
    std::vector<std::string> missing;   // known (not required) table files the source lacked
    std::vector<std::string> modified;  // known table files that failed their check, not stored
    std::vector<std::string> markers;   // the Mod Loader's, as found
};

// Writes SA2_STORE_RECORD into `dir` through a .part file, fsync and rename. The text is a
// function of the record alone (no clock, no host path), so a retry writes the same bytes.
bool sa2_store_write_record(const std::string& dir, const Sa2StoreRecord& rec, std::string* err);
std::string sa2_store_record_json(const Sa2StoreRecord& rec);

// The table id and receipt hash a store's record names; false when there is no readable record.
bool sa2_store_read_identity(const std::string& store_dir, std::string* table_id,
                             std::string* receipt_sha256);

// JSON string body (no quotes) for UTF-8 or other bytes: a byte that is not part of valid UTF-8
// is written as \u00XX, so the output is always valid JSON.
std::string sa2_json_escape(const std::string& s);
