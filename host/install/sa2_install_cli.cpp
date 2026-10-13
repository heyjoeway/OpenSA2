// sa2_install_cli.cpp: sa2-install, the installer's command-line front end. It never prompts.
//
//   sa2-install --source steam [--library N] [--yes] [--record R.runrec]
//   sa2-install --source folder PATH [--yes] [--record R.runrec]
//   sa2-install --source zip FILE [--data-dir D] --yes [--record R.runrec]
//   sa2-install --verify [--record R.runrec]
//   sa2-install --read-vdf FILE      (diagnostics: the libraries a libraryfolders.vdf lists)
//   options: --threads N (0 = every CPU), --data-dir D (default <data home>/sa2recomp)
//
// Without --yes nothing is written but the record: the source is verified and the verdict
// printed (a dry run); the ZIP route, which extracts, refuses to start. With --yes an accepted
// source gets its manifest (<data dir>/manifest.tsv) and the config (sa2_config.h). Neither may
// lie inside the game folder, which is never written. --verify reads the config, re-checks the
// manifest's file sizes and mtimes and folder mtimes, and re-verifies in full on any difference.
//
// Exit codes (Sa2InstallFailure): 0 accepted, 2 usage, 3 refused (a file the game reads is
// missing or changed, or an extra under resource/ or a file in the overlay would be read
// unverified; each named), 4 not found, 5 I/O error (the host will not read a file or folder the
// game reads, and nothing else refuses; or a write failed; or the source changed during the
// run), 6 ambiguous (more than one install: they are listed, and --library N picks one).
//
// An accepted install (and --verify, on an install made before this rule) gets the defaults of
// Config/UserConfig.cfg and Config/Keyboard.cfg in the overlay, each when neither the game folder
// nor the overlay holds it (sa2_config.h, sa2_user_defaults_ensure): SA2's own Launcher.exe writes
// them on its first run, which may never have happened, and the game cannot be played without
// them. The game folder is still never written.
//
// The ZIP route checks the overlay before it extracts anything, records the archive's sha256,
// size and mtime before and after as its source evidence and the store's digest apart from it,
// and classes the store's files beyond the table as the folder routes class extras.
//
// The record (--record, a .runrec file; never .json, which the M7 census would count as a game
// run) holds what a receipt needs and nothing personal: paths under $HOME are written "~/..."
// (compared case-blind on Windows), a path component elsewhere that is the login name is written
// "<user>", no field of a Steam manifest is copied but installdir, StateFlags and buildid, and no
// file content is copied.
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "../player/sha256.h"
#include "sa2_config.h"
#include "sa2_install.h"
#include "sa2_platform.h"
#include "sa2_scan.h"
#include "sa2_steam.h"
#include "sa2_zip.h"

namespace {

constexpr int EXIT_OK = 0, EXIT_USAGE = 2;

// ---- a small JSON writer for the record ----

std::string jstr(const std::string& s) {
    std::string o = "\"";
    for (unsigned char c : s) {
        if (c == '"') o += "\\\"";
        else if (c == '\\') o += "\\\\";
        else if (c == '\n') o += "\\n";
        else if (c == '\t') o += "\\t";
        else if (c < 0x20) {
            char b[8];
            std::snprintf(b, sizeof b, "\\u%04x", c);
            o += b;
        } else o += char(c);
    }
    return o + "\"";
}

struct J {
    std::string s;
    bool first = true;
    void open(char c) { s += c; first = true; }
    void close(char c) { s += c; first = false; }
    void sep() { if (!first) s += ","; first = false; }
    void key(const std::string& k) { sep(); s += jstr(k) + ":"; first = true; }
    void str(const std::string& k, const std::string& v) { key(k); s += jstr(v); first = false; }
    void num(const std::string& k, double v) {
        key(k);
        char b[40];
        std::snprintf(b, sizeof b, "%.15g", v);
        s += b;
        first = false;
    }
    void boolean(const std::string& k, bool v) { key(k); s += v ? "true" : "false"; first = false; }
    void item(const std::string& v) { sep(); s += jstr(v); }
    void obj(const std::string& k) { key(k); open('{'); }
    void arr(const std::string& k) { key(k); open('['); }
    void obj_item() { sep(); open('{'); }
};

// `p` with every mention of $HOME (as given, and as its realpath) written "~", and every other
// path component that is the login name written "<user>" (/media/<user>/SteamLibrary). Paths
// compare case-blind where the host's do (Windows).
std::string home_rel(const std::string& p) {
    const bool ci = sa2p::case_blind_paths();
    auto fold = [&](const std::string& x) { return ci ? sa2_install_fold(x) : x; };
    std::string h = sa2p::home_dir();
    while (h.size() > 1 && h.back() == '/') h.pop_back();
    std::string real_h;
    if (!sa2p::real_path(h, real_h)) real_h.clear();
    // Segments of `p`: (text, is the home directory).
    std::vector<std::pair<std::string, bool>> seg{{p, false}};
    for (const std::string& base : {h, real_h}) {
        if (base.size() < 2) continue;
        const std::string fb = fold(base);
        std::vector<std::pair<std::string, bool>> next;
        for (const auto& sg : seg) {
            if (sg.second) {
                next.push_back(sg);
                continue;
            }
            const std::string& t = sg.first;
            const std::string ft = fold(t);
            size_t i = 0;  // start of the text not yet pushed
            for (size_t f = ft.find(fb); f != std::string::npos; f = ft.find(fb, f + 1)) {
                size_t e = f + base.size();
                bool ends = e == t.size() || !(std::isalnum((unsigned char)t[e]) || t[e] == '_' ||
                                              t[e] == '-' || t[e] == '.');
                if (!ends || f < i) continue;
                next.push_back({t.substr(i, f - i), false});
                next.push_back({"~", true});
                i = e;
            }
            next.push_back({t.substr(i), false});
        }
        seg = next;
    }
    const std::string user = sa2p::user_name();
    std::string out;
    for (const auto& sg : seg) {
        if (sg.second || user.size() < 2) {
            out += sg.first;
            continue;
        }
        const std::string& t = sg.first;
        size_t a = 0;
        for (size_t b = 0; b <= t.size(); b++) {
            if (b < t.size() && t[b] != '/' && t[b] != '\\') continue;
            std::string comp = t.substr(a, b - a);
            out += fold(comp) == fold(user) ? std::string("<user>") : comp;
            if (b < t.size()) out += t[b];
            a = b + 1;
        }
    }
    return out;
}

// `path` (which need not exist yet) lies inside `root` (a realpath): its deepest existing
// ancestor is resolved, the rest appended.
bool under(const std::string& path, const std::string& root) {
    std::string head = path, tail;
    std::string real;
    while (!head.empty() && !sa2p::real_path(head, real)) {
        size_t s = head.find_last_of('/');
        if (s == std::string::npos) return false;
        tail = head.substr(s) + tail;
        head = head.substr(0, s);
    }
    if (head.empty()) return false;
    std::string full = real + tail;
    return full == root || (full.size() > root.size() && full.compare(0, root.size(), root) == 0 &&
                            full[root.size()] == '/');
}

std::string abs_path(const std::string& p) {
    std::string r;
    if (sa2p::real_path(p, r)) return r;
    return p;
}

std::string file_sha256(const std::string& path) {
    uint8_t d[32];
    return sa2_sha256_file(path, d) ? sa2_hex(d, 32) : std::string();
}

void usage() {
    std::fprintf(stderr,
                 "usage: sa2-install --source steam [--library N] | --source folder PATH | "
                 "--source zip FILE [--data-dir D]\n"
                 "                   [--yes] [--record R.runrec] [--threads N]\n"
                 "       sa2-install --verify [--record R.runrec]\n");
}

struct Run {
    std::vector<std::string> argv;
    std::string route, kind, source_path, read_root, data_dir, record;
    std::string source_method = "sha256 of every file's name, size and mtime and every folder's name "
                                "and mtime under the read root, before and after the run";
    size_t source_files = 0;
    std::string store_path, store_digest;   // the ZIP route's extracted store
    size_t store_files = 0;
    bool zip_ran = false;                   // the ZIP route's archive detail (sa2_zip.h)
    Sa2ZipDetail zip;
    bool yes = false, verify = false, data_dir_given = false;
    long library = -1;
    unsigned threads = 0;
    std::string started;
    double t0 = 0;
    Sa2SteamScan steam;
    bool steam_ran = false;
    long chosen = -1;
    Sa2InstallReport report;
    bool hash_checked = false, installed = false;
    std::string digest_before, digest_after;
    std::vector<std::string> refusals, tree_notes, quick;
    std::string config_path, config_sha, manifest_path, verified_at, verify_mode;
    bool user_defaults_checked = false;     // the Config/ defaults' check and writes
    std::vector<Sa2UserDefault> user_defaults;
    int code = 0;
    std::string message;
};

const char* code_name(int c) {
    switch (c) {
    case 0: return "accepted";
    case EXIT_USAGE: return "usage";
    case SA2_FAIL_REFUSED: return "refused";
    case SA2_FAIL_NOT_FOUND: return "not found";
    case SA2_FAIL_IO: return "I/O error";
    case SA2_FAIL_AMBIGUOUS: return "ambiguous";
    }
    return "unknown";
}

const char* kind_name(Sa2FileResult::Kind k) {
    switch (k) {
    case Sa2FileResult::Ok: return "ok";
    case Sa2FileResult::Missing: return "missing";
    case Sa2FileResult::Modified: return "modified";
    case Sa2FileResult::SubstitutedOrig: return "substituted-orig";
    }
    return "?";
}

bool write_record(const Run& r) {
    if (r.record.empty()) return true;
    const Sa2InstallTable& t = sa2_install_table();
    J j;
    j.open('{');
    j.str("schema", "sa2-s7-install-run/v1");
    j.obj("tool");
    j.str("name", "sa2-install");
    std::string self = sa2p::self_exe_path();
    j.str("sha256", self.empty() ? std::string() : file_sha256(self));
    j.boolean("sha256_hardware", Sha256::hardware());
    j.close('}');
    j.obj("table");
    j.str("id", t.id);
    j.str("receipt_sha256", t.receipt_sha256);
    j.str("generated_by", t.generated_by ? t.generated_by : "");
    j.num("entries", double(t.count));
    j.num("required", double(t.required_count));
    // Which executable check this binary carries (the DRM ruling's per-section check, or none).
    const Sa2ExeCheck* exe = nullptr;
    for (size_t k = 0; k < t.count; k++)
        if (t.entries[k].exe) exe = t.entries[k].exe;
    j.obj("exe_check");
    j.str("kind", exe ? "per-section" : "none");
    j.str("ruling", exe && exe->ruling ? exe->ruling : "");
    j.arr("hashed");
    for (size_t i = 0; exe && i < exe->count; i++)
        if (exe->sections[i].hashed) j.item(exe->sections[i].name);
    j.close(']');
    j.arr("not_hashed");
    for (size_t i = 0; exe && i < exe->count; i++)
        if (!exe->sections[i].hashed) j.item(exe->sections[i].name);
    j.close(']');
    j.close('}');
    j.close('}');
    j.str("target", sa2p::arch_name() + "-" + sa2p::os_name());
    j.arr("argv");
    for (const std::string& a : r.argv) j.item(home_rel(a));
    j.close(']');
    j.str("route", r.route);
    j.obj("source");
    j.str("kind", r.kind);
    j.str("path", home_rel(r.source_path));
    j.str("read_root", home_rel(r.read_root));
    if (r.steam_ran) {
        j.obj("steam");
        j.arr("roots_tried");
        for (const std::string& x : r.steam.roots_tried) j.item(home_rel(x));
        j.close(']');
        j.arr("roots");
        for (const std::string& x : r.steam.roots) j.item(home_rel(x));
        j.close(']');
        j.arr("libraries");
        for (const std::string& x : r.steam.libraries) j.item(home_rel(x));
        j.close(']');
        j.arr("installs");
        for (const Sa2SteamInstall& in : r.steam.installs) {
            j.obj_item();
            j.str("game_path", home_rel(in.game_path));
            j.str("library", home_rel(in.library));
            j.str("installdir", in.installdir);
            j.str("state_flags", in.state_flags);
            j.str("buildid", in.buildid);
            j.close('}');
        }
        j.close(']');
        j.num("chosen", double(r.chosen));
        j.arr("notes");
        for (const std::string& x : r.steam.notes) j.item(home_rel(x));
        j.close(']');
        j.close('}');
    }
    j.close('}');
    j.str("policy", SA2_POLICY);
    j.obj("counts");
    j.num("entries", double(r.report.files.size()));
    j.num("ok", double(r.report.ok));
    j.num("missing", double(r.report.missing));
    j.num("modified", double(r.report.modified));
    j.num("substituted", double(r.report.substituted));
    size_t refused_extras = 0;
    for (const Sa2Extra& x : r.report.extra_files) refused_extras += x.refused;
    j.num("extras_skipped", double(r.report.extras));
    j.num("extras_refused", double(refused_extras));
    j.num("known_missing", double(r.report.known_missing));
    j.num("known_modified", double(r.report.known_modified));
    j.close('}');
    j.boolean("mod_loader", r.report.mod_loader);
    j.arr("markers");
    for (const std::string& m : r.report.markers) j.item(m);
    j.close(']');
    j.arr("substitutions");
    for (const Sa2FileResult& f : r.report.files)
        if (f.kind == Sa2FileResult::SubstitutedOrig) {
            j.obj_item();
            j.str("path", f.path);
            j.str("backing", f.detail);
            j.close('}');
        }
    j.close(']');
    j.arr("extras");
    for (const Sa2Extra& x : r.report.extra_files) {
        j.obj_item();
        j.str("path", home_rel(x.path));
        j.boolean("refused", x.refused);
        j.str("why", home_rel(x.why));
        j.close('}');
    }
    j.close(']');
    j.arr("not_ok");
    for (const Sa2FileResult& f : r.report.files)
        if (f.kind == Sa2FileResult::Missing || f.kind == Sa2FileResult::Modified) {
            j.obj_item();
            j.str("path", f.path);
            j.str("kind", kind_name(f.kind));
            j.boolean("required", f.required);
            j.boolean("io_error", f.io_error);
            j.str("detail", f.detail);
            j.close('}');
        }
    j.close(']');
    j.num("found_in_another_case", [&] {
        size_t n = 0;
        for (const Sa2FileResult& f : r.report.files) n += !f.found_as.empty();
        return double(n);
    }());
    j.arr("refusals");
    for (const std::string& x : r.refusals) j.item(home_rel(x));
    j.close(']');
    j.arr("quick_check_differences");
    for (const std::string& x : r.quick) j.item(home_rel(x));
    j.close(']');
    j.obj("source_manifest");
    j.str("method", r.source_method);
    j.num("files", double(r.source_files));
    j.str("before", r.digest_before);
    j.str("after", r.digest_after);
    j.boolean("untouched", !r.digest_before.empty() && r.digest_before == r.digest_after);
    j.arr("notes");
    for (const std::string& x : r.tree_notes) j.item(home_rel(x));
    j.close(']');
    j.close('}');
    if (r.zip_ran) {
        // The archive as the ZIP route read it: its layout, and the user files (Config/,
        // SAVEDATA/) it held, written into the overlay or kept there.
        j.obj("zip");
        j.str("root", r.zip.root);
        j.num("archive_bytes", double(r.zip.archive_bytes));
        j.num("entries", double(r.zip.entries));
        j.boolean("zip64", r.zip.zip64);
        j.boolean("data_descriptors", r.zip.data_descriptors);
        j.arr("methods");
        for (int m : r.zip.methods) { j.sep(); j.s += std::to_string(m); }
        j.close(']');
        j.boolean("files_verified", r.zip.files_verified);
        j.num("extracted", double(r.zip.extracted));
        j.num("resumed", double(r.zip.resumed));
        j.arr("extracted_extras");
        for (const std::string& x : r.zip.extracted_extras) j.item(x);
        j.close(']');
        j.arr("user_files");
        for (const std::string& x : r.zip.user_files) j.item(x);
        j.close(']');
        j.arr("user_files_written");
        for (const std::string& x : r.zip.user_files_written) j.item(x);
        j.close(']');
        j.arr("user_files_kept");
        for (const std::string& x : r.zip.user_files_kept) j.item(x);
        j.close(']');
        j.num("skipped", double(r.zip.skipped.size()));
        j.close('}');
    }
    if (!r.store_path.empty()) {
        j.obj("store");
        j.str("path", home_rel(r.store_path));
        j.num("files", double(r.store_files));
        j.str("digest", r.store_digest);
        j.str("method", "sha256 of every file's name, size and mtime and every folder's name and mtime in the store");
        j.close('}');
    }
    j.boolean("hash_checked", r.hash_checked);
    j.boolean("installed", r.installed);
    j.num("manual_steps", 0);
    j.boolean("launched", false);
    j.str("verified_mode", r.verify_mode);
    if (r.user_defaults_checked) {
        j.arr("user_defaults");
        for (size_t i = 0; i < r.user_defaults.size(); i++) {
            const Sa2UserDefault& d = r.user_defaults[i];
            j.obj_item();
            j.str("path", d.path);
            j.boolean("in_read_root", d.in_read_root);
            j.boolean("in_overlay", d.in_overlay);
            j.boolean("default_written", d.written);
            j.str("written_at", home_rel(d.written_at));
            Sha256 h;
            h.update(SA2_USER_DEFAULTS[i].text, std::strlen(SA2_USER_DEFAULTS[i].text));
            j.str("default_sha256", h.hex());
            j.close('}');
        }
        j.close(']');
    }
    j.obj("config");
    j.str("path", home_rel(r.config_path));
    j.str("sha256", r.config_sha);
    j.str("manifest", home_rel(r.manifest_path));
    j.close('}');
    j.str("started", r.started);
    j.str("finished", sa2p::now_utc_iso8601());
    j.num("seconds", sa2p::monotonic_seconds() - r.t0);
    j.obj("exit");
    j.num("code", r.code);
    j.str("name", code_name(r.code));
    j.str("message", home_rel(r.message));
    j.close('}');
    j.close('}');
    j.s += "\n";
    if (!sa2p::write_file_atomic(r.record, j.s)) {
        std::fprintf(stderr, "sa2-install: cannot write the record %s\n", r.record.c_str());
        return false;
    }
    return true;
}

int finish(Run& r, int code, const std::string& message) {
    r.code = code;
    r.message = message;
    const char* tag = code == 0 ? "SA2_INSTALL_OK" : "SA2_INSTALL_FAIL";
    std::fprintf(code == 0 ? stdout : stderr, "%s %s: %s\n", tag, code_name(code), message.c_str());
    if (!write_record(r) && code == EXIT_OK) return SA2_FAIL_IO;  // a run with no record is not evidence
    return code;
}

void print_report(const Sa2InstallReport& rep) {
    std::printf("verified %zu entries: %zu ok, %zu substituted, %zu missing, %zu changed; "
                "%zu extra files skipped%s\n",
                rep.files.size(), rep.ok, rep.substituted, rep.missing, rep.modified, rep.extras,
                rep.mod_loader ? " (SA2 Mod Loader present)" : "");
    for (const Sa2FileResult& f : rep.files)
        if (f.kind == Sa2FileResult::SubstitutedOrig)
            std::printf("  using %s for %s (the Mod Loader's renamed original)\n", f.detail.c_str(), f.path.c_str());
    for (const Sa2FileResult& f : rep.files)
        if (!f.required && (f.kind == Sa2FileResult::Missing || f.kind == Sa2FileResult::Modified))
            std::printf("  not read by the game, %s: %s\n", kind_name(f.kind), f.path.c_str());
}

// The exit code of a refused report: I/O when every reason is that the host refused a read,
// else refused (a file missing, changed or extra, or an overlay shadow: `other` reasons).
int refusal_code(const Sa2InstallReport& rep, bool other) {
    bool content = other;
    for (const Sa2FileResult& f : rep.files)
        if (f.required && ((f.kind == Sa2FileResult::Missing && !f.io_error) || f.kind == Sa2FileResult::Modified))
            content = true;
    for (const Sa2Extra& x : rep.extra_files) content |= x.refused && !x.io;
    bool io = rep.failure == SA2_FAIL_IO;
    for (const Sa2FileResult& f : rep.files) io |= f.required && f.io_error;
    return io && !content ? SA2_FAIL_IO : SA2_FAIL_REFUSED;
}

std::string refusal_message(int code, size_t n) {
    if (code == SA2_FAIL_IO)
        return std::to_string(n) + " file(s) or folder(s) the game reads cannot be read; check their "
               "permissions and run again";
    return std::to_string(n) + " refusal(s), each named above: Steam's \"Verify integrity of game files\" "
           "restores a missing or changed file; an extra under resource/ has to be removed";
}

// The Config/ defaults into the overlay, each when neither `read_root` nor the overlay holds it
// (sa2_config.h). Each written is said on stdout.
bool ensure_user_defaults(Run& r, const std::string& read_root, const std::string& overlay, std::string* err) {
    r.user_defaults_checked = true;
    const bool ok = sa2_user_defaults_ensure(read_root, overlay, r.user_defaults, err);
    for (const Sa2UserDefault& d : r.user_defaults)
        if (d.written)
            std::printf("wrote the default %s into the overlay (%s): the game folder has none, and SA2's "
                        "launcher, which writes it, has not run there\n", d.path.c_str(), d.written_at.c_str());
    return ok;
}

// Index, verify and (with --yes) accept a game folder: the Steam and folder routes.
int accept_folder(Run& r) {
    Sa2TreeIndex idx;
    std::string err;
    bool io = false;
    if (!sa2_tree_index(r.read_root, idx, &err, &io))
        return finish(r, io ? SA2_FAIL_IO : SA2_FAIL_NOT_FOUND, r.read_root + ": " + err);
    r.read_root = idx.root;
    r.digest_before = sa2_tree_digest(idx);
    r.source_files = idx.files.size();
    r.tree_notes = idx.notes;
    if (!sa2_tree_is_game(idx))
        return finish(r, SA2_FAIL_NOT_FOUND, idx.root + " is not an SA2 game folder (no sonic2app.exe and "
                                             "resource/gd_PC/DLL/Win32/Data_DLL.dll)");
    const Sa2InstallTable& t = sa2_install_table();
    std::printf("verifying %s against %s (%zu files)\n", idx.root.c_str(), t.id, t.count);
    sa2_verify_tree(idx, t, r.threads, r.report);
    r.hash_checked = true;
    r.verify_mode = "full";
    r.verified_at = sa2p::now_utc_iso8601();
    print_report(r.report);
    r.refusals = sa2_install_refusals(r.report);
    Sa2Manifest m = sa2_manifest_from(idx, t, r.report, r.verified_at);
    std::vector<std::string> rows;
    for (const Sa2ManifestRow& row : m.rows) rows.push_back(row.rel);
    const std::vector<std::string> shadows = sa2_overlay_shadows(r.data_dir + "/overlay", t, rows);
    r.refusals.insert(r.refusals.end(), shadows.begin(), shadows.end());
    auto after = [&]() {
        Sa2TreeIndex again;
        if (sa2_tree_index(idx.root, again, nullptr)) r.digest_after = sa2_tree_digest(again);
    };
    if (!r.refusals.empty()) {
        for (const std::string& x : r.refusals) std::fprintf(stderr, "  refused: %s\n", x.c_str());
        after();
        int code = refusal_code(r.report, !shadows.empty());
        return finish(r, code, refusal_message(code, r.refusals.size()));
    }
    after();
    if (r.digest_after != r.digest_before)
        return finish(r, SA2_FAIL_IO, "the source changed while it was verified; run again");
    if (!r.yes) return finish(r, EXIT_OK, "verified; nothing written (dry run: pass --yes to write the config)");
    r.config_path = sa2_config_path();
    if (r.config_path.empty()) return finish(r, SA2_FAIL_IO, "no config directory (HOME is unset)");
    for (const std::string& w : {r.data_dir, r.config_path})
        if (under(w, idx.root))
            return finish(r, EXIT_USAGE, w + " is inside the game folder, which the installer never writes");
    size_t exe_i;
    idx.find("sonic2app.exe", exe_i);
    Sa2Config c;
    c.table_id = t.id;
    c.receipt_sha256 = t.receipt_sha256;
    c.source_kind = r.kind;
    c.source_path = r.source_path;
    c.read_root = idx.root;
    c.overlay = r.data_dir + "/overlay";
    c.exe = idx.host(exe_i);
    c.verified_at = r.verified_at;
    c.verified_mode = "full";
    c.policy = SA2_POLICY;
    c.mod_loader = r.report.mod_loader;
    c.extras = r.report.extras;
    for (const Sa2FileResult& f : r.report.files)
        if (f.kind == Sa2FileResult::SubstitutedOrig) c.substitutions.emplace_back(f.path, f.detail);
    r.manifest_path = r.data_dir + "/manifest.tsv";
    c.manifest = r.manifest_path;
    if (!sa2p::make_dirs(r.data_dir) || !sa2p::make_dirs(c.overlay) ||
        !sa2p::write_file_atomic(r.manifest_path, sa2_manifest_text(m)))
        return finish(r, SA2_FAIL_IO, "cannot write under " + r.data_dir);
    if (!ensure_user_defaults(r, idx.root, c.overlay, &err)) return finish(r, SA2_FAIL_IO, err);
    if (!sa2_config_write(r.config_path, c, &err)) return finish(r, SA2_FAIL_IO, err);
    r.config_sha = file_sha256(r.config_path);
    r.installed = true;
    after();
    if (r.digest_after != r.digest_before)
        return finish(r, SA2_FAIL_IO, "the source changed while the config was written; run again");
    return finish(r, EXIT_OK, "accepted " + idx.root + "; config " + r.config_path);
}

int run_steam(Run& r) {
    std::vector<std::string> notes;
    std::vector<std::string> roots = sa2p::steam_roots(&notes);
    r.steam = sa2_steam_find(roots);
    r.steam_ran = true;
    for (const std::string& n : notes) r.steam.notes.push_back(n);
    std::string reg;
    if (sa2p::sega_registry_install_path(reg)) {
        std::string real;
        bool dup = false;
        if (sa2p::real_path(reg, real)) {
            for (const Sa2SteamInstall& in : r.steam.installs) dup |= in.game_path == real;
            if (!dup) {
                Sa2SteamInstall in;
                in.game_path = real;
                in.installdir = "(HKLM\\Software\\Sega\\SonicAd2)";
                in.fully_installed = true;
                r.steam.installs.push_back(in);
            }
        }
    }
    const auto& ins = r.steam.installs;
    if (ins.empty()) {
        for (const std::string& n : r.steam.notes) std::fprintf(stderr, "  %s\n", n.c_str());
        return finish(r, SA2_FAIL_NOT_FOUND, "no Steam installation of app " + std::string(SA2_STEAM_APPID) +
                                             " found under " + std::to_string(r.steam.roots.size()) +
                                             " Steam root(s); use --source folder PATH");
    }
    if (r.library >= 0) {
        if (r.library >= long(ins.size()))
            return finish(r, EXIT_USAGE, "--library " + std::to_string(r.library) + ": there are " +
                                         std::to_string(ins.size()) + " installs (0-based)");
        r.chosen = r.library;
    } else if (ins.size() > 1) {
        std::fprintf(stderr, "more than one Steam installation of SA2:\n");
        for (size_t i = 0; i < ins.size(); i++)
            std::fprintf(stderr, "  --library %zu  %s\n", i, ins[i].game_path.c_str());
        return finish(r, SA2_FAIL_AMBIGUOUS, std::to_string(ins.size()) + " installs found; pick one with --library N");
    } else {
        r.chosen = 0;
    }
    r.source_path = ins[size_t(r.chosen)].game_path;
    r.read_root = r.source_path;
    std::printf("found %s (library %s)\n", r.source_path.c_str(), ins[size_t(r.chosen)].library.c_str());
    return accept_folder(r);
}

// The archive's evidence of being left alone: "sha256=... bytes=... mtime_ns=...", "" when unreadable.
std::string archive_evidence(const std::string& path) {
    sa2p::Stat st;
    uint8_t d[32];
    if (!sa2p::stat_path(path, st) || !st.is_file || !sa2_sha256_file(path, d)) return {};
    return "sha256=" + sa2_hex(d, 32) + " bytes=" + std::to_string(st.size) + " mtime_ns=" + std::to_string(st.mtime_ns);
}

int run_zip(Run& r) {
    sa2p::Stat st;
    if (!sa2p::stat_path(r.source_path, st) || !st.is_file)
        return finish(r, SA2_FAIL_NOT_FOUND, r.source_path + ": no such file");
    r.source_path = abs_path(r.source_path);
    if (!r.yes) return finish(r, EXIT_USAGE, "the ZIP route extracts the game files; pass --yes");
    const Sa2InstallTable& t = sa2_install_table();
    r.source_method = "the archive's sha256, size and mtime, before and after the run";
    r.source_files = 1;
    // The overlay first: what it holds would be read before the store, so a shadow refuses
    // before anything is extracted.
    const std::string overlay = r.data_dir + "/overlay";
    r.refusals = sa2_overlay_shadows(overlay, t, {});
    if (!r.refusals.empty()) {
        for (const std::string& x : r.refusals) std::fprintf(stderr, "  refused: %s\n", x.c_str());
        return finish(r, SA2_FAIL_REFUSED, "the overlay " + overlay + " holds files the game would read "
                                           "before the verified ones; nothing was extracted");
    }
    r.digest_before = archive_evidence(r.source_path);
    if (r.digest_before.empty())
        return finish(r, SA2_FAIL_IO, r.source_path + ": cannot read the archive (" + sa2p::last_error() + ")");
    std::string store = r.data_dir + "/game";
    if (!sa2p::make_dirs(r.data_dir)) return finish(r, SA2_FAIL_IO, "cannot create " + r.data_dir);
    // The archive's user files (Config/*.cfg, saves) go to the overlay the runtime writes, never
    // over a file already there (sa2_zip.h, Sa2ZipOptions::user_dir).
    Sa2ZipOptions zopt;
    zopt.threads = r.threads;
    zopt.user_dir = overlay;
    if (!sa2p::make_dirs(overlay)) return finish(r, SA2_FAIL_IO, "cannot create " + overlay);
    r.zip_ran = true;
    bool ok = sa2_zip_install(r.source_path, store, t, r.report, zopt, &r.zip);
    r.digest_after = archive_evidence(r.source_path);
    // Required or known is the table's word, whatever the route filled in.
    std::unordered_map<std::string, bool> required;
    for (size_t k = 0; k < t.count; k++) required[t.entries[k].path] = t.entries[k].required;
    for (Sa2FileResult& f : r.report.files) {
        auto it = required.find(f.path);
        if (it != required.end()) f.required = it->second;
    }
    sa2_install_recount(r.report);
    // Hash-checked only when every table file the archive holds was read and checked: a run that
    // stopped early marks the files it never read Ok provisionally ("not read: ..."; sa2_zip.h).
    r.hash_checked = !r.report.files.empty() && r.zip.files_verified;
    r.verify_mode = "full";
    r.verified_at = sa2p::now_utc_iso8601();
    print_report(r.report);
    r.refusals = sa2_install_refusals(r.report);
    if (!ok || !r.refusals.empty()) {
        int code = r.report.failure;
        if (!code) {
            bool named = false;
            for (const Sa2FileResult& f : r.report.files)
                named |= f.required && (f.kind == Sa2FileResult::Missing || f.kind == Sa2FileResult::Modified);
            code = named ? SA2_FAIL_REFUSED : SA2_FAIL_IO;
        }
        for (const std::string& x : r.refusals) std::fprintf(stderr, "  refused: %s\n", x.c_str());
        return finish(r, code, r.report.error.empty() ? std::string("the archive was refused") : r.report.error);
    }
    r.read_root = abs_path(store);
    Sa2TreeIndex idx;
    std::string err;
    if (!sa2_tree_index(r.read_root, idx, &err) || !sa2_tree_is_game(idx))
        return finish(r, SA2_FAIL_IO, "the store " + r.read_root + " is not a game folder after extraction");
    r.store_path = idx.root;
    r.store_files = idx.files.size();
    r.store_digest = sa2_tree_digest(idx);
    // The store's files beyond the table, classed as the folder routes class extras: one under
    // resource/ would be read unverified.
    std::unordered_set<std::string> table_rels;
    for (size_t k = 0; k < t.count; k++) {
        size_t i;
        if (idx.find(t.entries[k].path, i) == Sa2TreeIndex::Found) table_rels.insert(idx.files[i].rel);
    }
    for (const Sa2TreeFile& f : idx.files) {
        if (table_rels.count(f.rel)) continue;
        const char* why = sa2_install_extra_allowed(f.rel, t);
        r.report.extra_files.push_back({f.rel, why ? why : "extracted into the store under resource/, where the "
                                                           "game looks for files a clean install lacks: it would "
                                                           "be read unverified", !why});
        if (!why) r.refusals.push_back(f.rel + ": " + r.report.extra_files.back().why);
    }
    if (!r.refusals.empty()) {
        for (const std::string& x : r.refusals) std::fprintf(stderr, "  refused: %s\n", x.c_str());
        return finish(r, SA2_FAIL_REFUSED, refusal_message(SA2_FAIL_REFUSED, r.refusals.size()));
    }
    if (r.digest_after != r.digest_before)
        return finish(r, SA2_FAIL_IO, "the archive changed while it was read; run again");
    size_t exe_i;
    idx.find("sonic2app.exe", exe_i);
    Sa2Config c;
    c.table_id = t.id;
    c.receipt_sha256 = t.receipt_sha256;
    c.source_kind = "zip";
    c.source_path = r.source_path;
    c.read_root = idx.root;
    c.overlay = overlay;
    c.exe = idx.host(exe_i);
    c.verified_at = r.verified_at;
    c.verified_mode = "full";
    c.policy = SA2_POLICY;
    c.mod_loader = r.report.mod_loader;
    c.extras = r.report.extras;
    // No substitution line: this route copied a matching *_orig original's verified bytes to the
    // canonical name, so the store holds no *_orig file for the runtime to map (sa2_zip.h, step
    // 4). The record keeps the substitution as provenance.
    r.manifest_path = r.data_dir + "/manifest.tsv";
    c.manifest = r.manifest_path;
    Sa2Manifest m = sa2_manifest_from(idx, t, r.report, r.verified_at);
    if (!sa2p::make_dirs(c.overlay) || !sa2p::write_file_atomic(r.manifest_path, sa2_manifest_text(m)))
        return finish(r, SA2_FAIL_IO, "cannot write under " + r.data_dir);
    // The store holds no Config/ (the archive's user files went to the overlay, above).
    if (!ensure_user_defaults(r, idx.root, c.overlay, &err)) return finish(r, SA2_FAIL_IO, err);
    r.config_path = sa2_config_path();
    if (r.config_path.empty() || !sa2_config_write(r.config_path, c, &err))
        return finish(r, SA2_FAIL_IO, err.empty() ? std::string("no config directory") : err);
    r.config_sha = file_sha256(r.config_path);
    r.installed = true;
    return finish(r, EXIT_OK, "installed from " + r.source_path + " into " + r.read_root);
}

int run_verify(Run& r) {
    r.route = "verify";
    r.config_path = sa2_config_path();
    std::string text, err;
    Sa2Config c;
    if (r.config_path.empty() || !sa2p::read_file(r.config_path, text) || !sa2_config_parse(text, c, &err))
        return finish(r, SA2_FAIL_NOT_FOUND, "no usable config at " + r.config_path + (err.empty() ? "" : ": " + err));
    r.kind = c.source_kind;
    r.source_path = c.source_path;
    r.read_root = c.read_root;
    r.manifest_path = c.manifest;
    const Sa2InstallTable& t = sa2_install_table();
    Sa2Manifest m;
    std::string mtext;
    if (!sa2p::read_file(c.manifest, mtext) || !sa2_manifest_parse(mtext, m, &err))
        r.quick.push_back("no usable manifest at " + c.manifest);
    else {
        r.quick = sa2_manifest_quick_check(m, t);
        if (m.root != c.read_root) r.quick.push_back("the manifest names another folder");
    }
    if (c.table_id != t.id || c.receipt_sha256 != t.receipt_sha256)
        r.quick.push_back("the config was written under table " + c.table_id);
    std::vector<std::string> rows;
    for (const Sa2ManifestRow& row : m.rows) rows.push_back(row.rel);
    r.refusals = sa2_overlay_shadows(c.overlay, t, rows);
    if (!r.refusals.empty()) {
        for (const std::string& x : r.refusals) std::fprintf(stderr, "  refused: %s\n", x.c_str());
        return finish(r, SA2_FAIL_REFUSED, "the overlay " + c.overlay + " shadows files the game reads");
    }
    if (r.quick.empty()) {
        // The read root's names, sizes and mtimes before and after, as the full check records them.
        Sa2TreeIndex before, after;
        if (sa2_tree_index(c.read_root, before, nullptr)) {
            r.digest_before = sa2_tree_digest(before);
            r.source_files = before.files.size();
        }
        r.verify_mode = "quick";
        r.config_sha = file_sha256(r.config_path);
        if (!ensure_user_defaults(r, c.read_root, c.overlay, &err)) return finish(r, SA2_FAIL_IO, err);
        if (sa2_tree_index(c.read_root, after, nullptr)) r.digest_after = sa2_tree_digest(after);
        return finish(r, EXIT_OK, "quick check clean: " + std::to_string(m.rows.size()) + " files unchanged since " + c.verified_at);
    }
    for (const std::string& q : r.quick) std::printf("  quick check: %s\n", q.c_str());
    // The full re-verify writes the manifest and config where the install put them.
    size_t slash = c.manifest.find_last_of('/');
    if (!r.data_dir_given && slash != std::string::npos) r.data_dir = c.manifest.substr(0, slash);
    std::printf("re-verifying %s in full\n", c.source_kind == "zip" ? "the extracted store" : "the folder");
    r.yes = true;
    return accept_folder(r);
}

}  // namespace

int main(int argc, char** argv) {
    Run r;
    r.t0 = sa2p::monotonic_seconds();
    r.started = sa2p::now_utc_iso8601();
    r.argv = sa2p::utf8_args(argc, argv);
    argc = int(r.argv.size());
    std::string source, data_dir;
    bool data_dir_given = false;
    for (int i = 1; i < argc; i++) {
        std::string a = r.argv[size_t(i)];
        auto need = [&](const char* what) -> std::string {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "sa2-install: %s needs %s\n", a.c_str(), what);
                std::exit(EXIT_USAGE);
            }
            return r.argv[size_t(++i)];
        };
        if (a == "--source") {
            source = need("steam, folder or zip");
            if (source == "folder" || source == "zip") r.source_path = need("a path");
            else if (source != "steam") {
                usage();
                return EXIT_USAGE;
            }
        } else if (a == "--library") {
            std::string v = need("a number");
            char* end = nullptr;
            r.library = std::strtol(v.c_str(), &end, 10);
            if (!end || *end || r.library < 0) {
                std::fprintf(stderr, "sa2-install: --library takes a number from the listing\n");
                return EXIT_USAGE;
            }
        } else if (a == "--read-vdf") {
            // Diagnostics: the library paths a libraryfolders.vdf lists, one per line.
            std::string path = need("a libraryfolders.vdf"), text, err;
            Sa2Kv kv;
            if (!sa2p::read_file(path, text, 4u << 20)) {
                std::fprintf(stderr, "sa2-install: cannot read %s\n", path.c_str());
                return SA2_FAIL_IO;
            }
            if (!sa2_kv_parse(text, kv, &err)) {
                std::fprintf(stderr, "sa2-install: %s: %s\n", path.c_str(), err.c_str());
                return SA2_FAIL_IO;
            }
            for (const std::string& lib : sa2_steam_library_paths(kv)) std::printf("library %s\n", lib.c_str());
            return EXIT_OK;
        } else if (a == "--data-dir") {
            data_dir = need("a directory");
            data_dir_given = true;
        }
        else if (a == "--yes") r.yes = true;
        else if (a == "--verify") r.verify = true;
        else if (a == "--record") r.record = need("a .runrec path");
        else if (a == "--threads") r.threads = unsigned(std::strtoul(need("a number").c_str(), nullptr, 10));
        else if (a == "--help" || a == "-h") {
            usage();
            return EXIT_OK;
        } else {
            std::fprintf(stderr, "sa2-install: unknown argument %s\n", a.c_str());
            usage();
            return EXIT_USAGE;
        }
    }
    if (!r.record.empty()) {
        const std::string suf = ".runrec";
        if (r.record.size() <= suf.size() || r.record.compare(r.record.size() - suf.size(), suf.size(), suf) != 0) {
            std::fprintf(stderr, "sa2-install: --record takes a .runrec path (a .json record would be "
                                 "counted as a game run)\n");
            return EXIT_USAGE;
        }
    }
    if (r.verify == !source.empty()) {
        usage();
        return EXIT_USAGE;
    }
    if (r.library >= 0 && source != "steam") {
        std::fprintf(stderr, "sa2-install: --library goes with --source steam\n");
        return EXIT_USAGE;
    }
    if (data_dir.empty()) {
        std::string d = sa2p::data_home();
        if (d.empty()) {
            std::fprintf(stderr, "sa2-install: no data directory (HOME is unset); pass --data-dir\n");
            return EXIT_USAGE;
        }
        data_dir = d + "/sa2recomp";
    }
    r.data_dir = data_dir;
    r.data_dir_given = data_dir_given;
    if (r.verify) return run_verify(r);
    r.kind = source;
    if (source == "steam") {
        r.route = "steam-autodiscovery";
        return run_steam(r);
    }
    if (source == "folder") {
        r.route = "folder";
        sa2p::Stat st;
        if (!sa2p::stat_path(r.source_path, st) || !st.is_dir)
            return finish(r, SA2_FAIL_NOT_FOUND, r.source_path + ": no such folder");
        r.source_path = abs_path(r.source_path);
        r.read_root = r.source_path;
        return accept_folder(r);
    }
    r.route = "zip";
    return run_zip(r);
}
