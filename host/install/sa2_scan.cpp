// sa2_scan.cpp: see sa2_scan.h.
#include "sa2_scan.h"

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <set>
#include <thread>

#include "../player/sha256.h"
#include "sa2_platform.h"

namespace {

bool inside(const std::string& real, const std::string& root) {
    return real == root || (real.size() > root.size() && real.compare(0, root.size(), root) == 0 &&
                            (root.back() == '/' || real[root.size()] == '/'));
}

std::string dir_of(const std::string& rel) {
    size_t s = rel.find_last_of('/');
    return s == std::string::npos ? std::string() : rel.substr(0, s);
}

std::string base_of(const std::string& rel) {
    size_t s = rel.find_last_of('/');
    return s == std::string::npos ? rel : rel.substr(s + 1);
}

bool ends_with_ci(const std::string& s, const char* suf) {
    size_t n = std::strlen(suf);
    return s.size() >= n && sa2_install_fold(s.substr(s.size() - n)) == suf;
}

// runtime/sa2_module.c's ends_dll: longer than ".dll" and ending in it, case-blind.
bool ends_dll(const std::string& name) { return name.size() > 4 && ends_with_ci(name, ".dll"); }

bool under_resource(const std::string& rel) { return sa2_install_fold(rel).compare(0, 9, "resource/") == 0; }

}  // namespace

void Sa2TreeIndex::Dir::add(const std::string& n) {
    if (names.insert(n).second) folded[sa2_install_fold(n)].push_back(n);
}

Sa2TreeIndex::Lookup Sa2TreeIndex::resolve(const std::string& rel, std::string& out) const {
    out.clear();
    size_t pos = 0;
    while (pos <= rel.size()) {
        size_t e = rel.find('/', pos);
        if (e == std::string::npos) e = rel.size();
        std::string comp = rel.substr(pos, e - pos);
        pos = e + 1;
        if (comp.empty()) {
            if (e == rel.size()) break;
            continue;
        }
        auto it = kids.find(out);
        if (it == kids.end()) return Absent;
        std::string next;
        if (it->second.names.count(comp)) {
            next = comp;
        } else {
            auto f = it->second.folded.find(sa2_install_fold(comp));
            if (f == it->second.folded.end() || f->second.empty())
                return unreadable.count(out) ? Unreadable : Absent;
            if (f->second.size() > 1) return Ambiguous;
            next = f->second[0];
        }
        out = out.empty() ? next : out + "/" + next;
        if (links_out.count(out)) return Escape;
        if (e == rel.size()) break;
    }
    return Found;
}

Sa2TreeIndex::Lookup Sa2TreeIndex::find(const std::string& rel, size_t& idx) const {
    std::string r;
    Lookup l = resolve(rel, r);
    if (l != Found) return l;
    auto it = exact.find(r);
    if (it == exact.end()) return Absent;  // a folder, or nothing
    idx = it->second;
    return Found;
}

bool Sa2TreeIndex::has_dir(const std::string& rel) const {
    std::string r;
    return resolve(rel, r) == Found && kids.count(r) != 0;
}

bool sa2_tree_index(const std::string& folder, Sa2TreeIndex& out, std::string* error, bool* io) {
    out = Sa2TreeIndex{};
    sa2p::Stat st;
    if (!sa2p::stat_path(folder, st) || !st.is_dir) {
        if (error) *error = "not a folder";
        return false;
    }
    if (!sa2p::real_path(folder, out.root)) {
        if (error) *error = "cannot resolve the folder's path";
        return false;
    }
    std::set<std::string> visited{out.root};
    std::vector<std::string> stack{std::string()};
    while (!stack.empty()) {
        std::string rel = stack.back();
        stack.pop_back();
        std::string host = rel.empty() ? out.root : out.root + "/" + rel;
        sa2p::Stat ds;
        if (sa2p::stat_path(host, ds)) out.dirs.push_back({rel, 0, ds.mtime_ns});
        std::vector<std::string> names;
        if (!sa2p::list_dir(host, names)) {
            std::string why = sa2p::last_error();
            if (rel.empty()) {
                if (error) *error = "cannot read the folder: " + why;
                if (io) *io = true;
                return false;
            }
            out.notes.push_back("unreadable folder: " + rel + " (" + why + ")");
            out.unreadable[rel] = why;
            continue;
        }
        std::sort(names.begin(), names.end());
        for (const std::string& n : names) {
            std::string r = rel.empty() ? n : rel + "/" + n;
            std::string h = out.root + "/" + r;
            sa2p::Stat s;
            if (!sa2p::stat_path(h, s)) {
                out.notes.push_back("dangling or unreadable: " + r);
                continue;
            }
            if (s.is_link) {
                std::string real;
                if (!sa2p::real_path(h, real) || !inside(real, out.root)) {
                    out.notes.push_back("a link out of the folder, not followed: " + r);
                    out.kids[rel].add(n);
                    out.links_out[r] = true;
                    continue;
                }
                if (s.is_dir && !visited.insert(real).second) {
                    out.notes.push_back("a link back into a folder already indexed, not followed: " + r);
                    continue;
                }
            }
            if (s.is_dir) {
                out.kids[rel].add(n);
                out.kids[r];
                stack.push_back(r);
            } else if (s.is_file) {
                out.kids[rel].add(n);
                out.files.push_back({r, s.size, s.mtime_ns});
            }
        }
    }
    auto by_rel = [](const Sa2TreeFile& a, const Sa2TreeFile& b) { return a.rel < b.rel; };
    std::sort(out.files.begin(), out.files.end(), by_rel);
    std::sort(out.dirs.begin(), out.dirs.end(), by_rel);
    out.kids[std::string()];
    for (size_t i = 0; i < out.files.size(); i++) out.exact[out.files[i].rel] = i;
    return true;
}

std::string sa2_tree_digest(const Sa2TreeIndex& idx) {
    Sha256 h;
    for (const Sa2TreeFile& f : idx.files) {
        std::string line = "f\t" + f.rel + '\t' + std::to_string(f.size) + '\t' + std::to_string(f.mtime_ns) + '\n';
        h.update(line.data(), line.size());
    }
    for (const Sa2TreeFile& d : idx.dirs) {
        std::string line = "d\t" + d.rel + '\t' + std::to_string(d.mtime_ns) + '\n';
        h.update(line.data(), line.size());
    }
    return h.hex();
}

bool sa2_tree_is_game(const Sa2TreeIndex& idx) {
    size_t i;
    return idx.find("sonic2app.exe", i) == Sa2TreeIndex::Found &&
           (idx.find("resource/gd_PC/DLL/Win32/Data_DLL.dll", i) == Sa2TreeIndex::Found ||
            idx.find("resource/gd_PC/DLL/Win32/Data_DLL_orig.dll", i) == Sa2TreeIndex::Found);
}

namespace {

// For a changed DLL entry: the *_orig original, when its hash matches the entry and the runtime
// would map it in the named file's place (runtime/sa2_module.c's sa2_module_load: the named file
// loads as a DLL and its export directory names it something else; the runtime then reads every
// other *.dll in the named file's folder, and the one that loads and names itself as asked backs
// the handle; a link out of the folder among them stops the game).
bool try_orig(const Sa2TreeIndex& idx, const Sa2InstallEntry& e, size_t named, Sa2FileResult& r) {
    std::string orig = sa2_install_orig_name(e.path);
    size_t oi;
    if (idx.find(orig, oi) != Sa2TreeIndex::Found) return false;
    const std::string& orel = idx.files[oi].rel;
    std::string why;
    if (sa2_install_check_file(e, idx.host(oi), &why) != Sa2FileResult::Ok) {
        r.detail += "; its original " + orel + " does not match either (" + why + ")";
        return true;
    }
    if (!ends_dll(e.path)) {
        r.detail += "; " + orel + " matches, but the runtime reads the named file, "
                    "not a *_orig original, for anything but a DLL";
        return true;
    }
    const Sa2PeDll nd = sa2_pe_dll(idx.host(named));
    if (!nd.loads) {
        r.detail += "; " + orel + " matches, but the runtime refuses to load the named file (" + nd.why +
                    ") and searches no sibling";
        return true;
    }
    if (nd.self_name.empty() || sa2_same_module_name(nd.self_name, e.path)) {
        r.detail += "; " + orel + " matches, but the runtime maps the named file "
                    "(it is not a DLL naming itself something else)";
        return true;
    }
    const std::string dir = dir_of(idx.files[named].rel);
    for (const auto& l : idx.links_out)
        if (dir_of(l.first) == dir && ends_dll(base_of(l.first))) {
            r.detail += "; " + l.first + " beside it is a link out of the folder, which the runtime "
                        "refuses when it reads the named file's siblings";
            return true;
        }
    int candidates = 0;
    size_t pick = size_t(-1);
    for (size_t i = 0; i < idx.files.size(); i++) {
        if (i == named || dir_of(idx.files[i].rel) != dir || !ends_dll(base_of(idx.files[i].rel))) continue;
        const Sa2PeDll c = sa2_pe_dll(idx.host(i));
        if (c.loads && !c.self_name.empty() && sa2_same_module_name(c.self_name, e.path)) {
            candidates++;
            pick = i;
        }
    }
    if (candidates != 1) {
        r.detail += "; " + orel + " matches, but " + std::to_string(candidates) +
                    " files beside it name themselves " + base_of(e.path) + ", and the runtime "
                    "refuses anything but one";
        return true;
    }
    if (pick != oi) {
        r.detail += "; " + orel + " matches, but the file the runtime would map is " + idx.files[pick].rel;
        return true;
    }
    r.kind = Sa2FileResult::SubstitutedOrig;
    r.detail = orel;
    return true;
}

}  // namespace

void sa2_verify_tree(const Sa2TreeIndex& idx, const Sa2InstallTable& table, unsigned threads,
                     Sa2InstallReport& report) {
    report = Sa2InstallReport{};
    report.files.resize(table.count);
    std::vector<size_t> found(table.count, size_t(-1));
    std::vector<size_t> jobs;
    for (size_t k = 0; k < table.count; k++) {
        const Sa2InstallEntry& e = table.entries[k];
        Sa2FileResult& r = report.files[k];
        r.path = e.path;
        r.required = e.required;
        r.kind = Sa2FileResult::Missing;
        size_t i;
        Sa2TreeIndex::Lookup l = idx.find(e.path, i);
        if (l == Sa2TreeIndex::Absent) {
            size_t oi;
            if (idx.find(sa2_install_orig_name(e.path), oi) == Sa2TreeIndex::Found)
                r.detail = "only its original " + idx.files[oi].rel + " is present";
            continue;
        }
        if (l == Sa2TreeIndex::Ambiguous) {
            r.kind = Sa2FileResult::Modified;
            r.detail = "two names on its path differ only in case, and the runtime refuses both";
            continue;
        }
        if (l == Sa2TreeIndex::Escape) {
            r.kind = Sa2FileResult::Modified;
            r.detail = "a link out of the folder, which the runtime refuses";
            continue;
        }
        if (l == Sa2TreeIndex::Unreadable) {
            r.io_error = true;
            r.detail = "cannot be read: a folder on its path cannot be listed";
            continue;
        }
        found[k] = i;
        if (idx.files[i].rel != e.path) r.found_as = idx.files[i].rel;
        jobs.push_back(k);
    }
    // Largest first, so the long files do not all land at the end of one thread.
    std::sort(jobs.begin(), jobs.end(), [&](size_t a, size_t b) {
        return idx.files[found[a]].size > idx.files[found[b]].size;
    });
    unsigned n = threads ? threads : sa2p::cpu_count();
    if (n > 16) n = 16;
    if (n > jobs.size()) n = unsigned(jobs.size() ? jobs.size() : 1);
    std::atomic<size_t> next{0};
    auto work = [&]() {
        for (;;) {
            size_t j = next.fetch_add(1);
            if (j >= jobs.size()) return;
            size_t k = jobs[j];
            Sa2FileResult& r = report.files[k];
            bool io = false;
            r.kind = sa2_install_check_file(table.entries[k], idx.host(found[k]), &r.detail, &io);
            r.io_error = io;
        }
    };
    std::vector<std::thread> pool;
    for (unsigned t = 1; t < n; t++) pool.emplace_back(work);
    work();
    for (std::thread& t : pool) t.join();

    std::vector<bool> claimed(idx.files.size(), false);
    for (size_t k = 0; k < table.count; k++) {
        if (found[k] == size_t(-1)) continue;
        claimed[found[k]] = true;
        Sa2FileResult& r = report.files[k];
        if (r.kind == Sa2FileResult::Modified) try_orig(idx, table.entries[k], found[k], r);
        if (r.kind == Sa2FileResult::SubstitutedOrig) {
            size_t oi;
            if (idx.find(r.detail, oi) == Sa2TreeIndex::Found) claimed[oi] = true;
        }
    }
    for (size_t m = 0; m < SA2_MOD_LOADER_MARKER_COUNT; m++) {
        size_t i;
        const char* mk = SA2_MOD_LOADER_MARKERS[m];
        if (idx.find(mk, i) == Sa2TreeIndex::Found || idx.has_dir(mk)) report.markers.push_back(mk);
    }
    report.mod_loader = !report.markers.empty();

    // Every other file is an extra, classed; a link out of the folder or an unreadable folder
    // under resource/ hides what the game would read there.
    std::unordered_set<std::string> table_folded;
    for (size_t k = 0; k < table.count; k++) table_folded.insert(sa2_install_fold(table.entries[k].path));
    for (size_t i = 0; i < idx.files.size(); i++) {
        if (claimed[i]) continue;
        const char* why = sa2_install_extra_allowed(idx.files[i].rel, table);
        report.extra_files.push_back({idx.files[i].rel,
                                      why ? why : "not in the table, under resource/, where the game looks "
                                                  "for files a clean install lacks: it would be read "
                                                  "unverified (remove it, or move it out of resource/)",
                                      !why});
    }
    std::vector<std::string> links;
    for (const auto& l : idx.links_out) links.push_back(l.first);
    std::sort(links.begin(), links.end());
    for (const std::string& l : links) {
        if (table_folded.count(sa2_install_fold(l))) continue;  // refused as its entry
        if (under_resource(l))
            report.extra_files.push_back({l, "a link out of the folder under resource/, which the runtime refuses", true});
        else
            report.extra_files.push_back({l, "a link out of the folder, outside resource/: not followed", false});
    }
    std::vector<std::string> dirs;
    for (const auto& d : idx.unreadable) dirs.push_back(d.first);
    std::sort(dirs.begin(), dirs.end());
    for (const std::string& d : dirs) {
        const std::string& why = idx.unreadable.at(d);
        if (under_resource(d + "/")) {
            report.extra_files.push_back({d + "/", "cannot be read (" + why + "): the game may read files in it", true, true});
            report.failure = SA2_FAIL_IO;
        } else {
            report.extra_files.push_back({d + "/", "an unreadable folder outside resource/ (" + why + "): not indexed", false});
        }
    }
    report.extras = 0;
    for (const Sa2Extra& x : report.extra_files) report.extras += !x.refused;
    for (const Sa2FileResult& f : report.files)
        if (f.required && f.io_error) report.failure = SA2_FAIL_IO;
    sa2_install_recount(report);
}

std::vector<std::string> sa2_overlay_shadows(const std::string& overlay, const Sa2InstallTable& table,
                                             const std::vector<std::string>& also) {
    std::vector<std::string> out;
    sa2p::Stat st;
    if (!sa2p::stat_path(overlay, st)) return out;
    Sa2TreeIndex idx;
    std::string err;
    if (!sa2_tree_index(overlay, idx, &err)) {
        out.push_back(overlay + ": the overlay cannot be read (" + err + ")");
        return out;
    }
    std::set<std::string> named;  // folded paths already refused
    auto shadow = [&](const std::string& rel) {
        size_t i;
        if (idx.find(rel, i) == Sa2TreeIndex::Absent || !named.insert(sa2_install_fold(rel)).second) return;
        out.push_back(rel + ": the overlay holds a copy, which the runtime would read first");
    };
    for (size_t k = 0; k < table.count; k++) shadow(table.entries[k].path);
    for (const std::string& rel : also) shadow(rel);
    for (const Sa2TreeFile& f : idx.files)
        if (!sa2_install_user_file(f.rel) && named.insert(sa2_install_fold(f.rel)).second)
            out.push_back(f.rel + ": the overlay holds it under resource/, where the game looks for files the "
                          "install lacks: it would be read unverified");
    for (const auto& d : idx.unreadable)
        out.push_back(d.first + "/: a folder in the overlay that cannot be read (" + d.second + ")");
    return out;
}

Sa2Manifest sa2_manifest_from(const Sa2TreeIndex& idx, const Sa2InstallTable& table,
                              const Sa2InstallReport& report, const std::string& verified_at) {
    Sa2Manifest m;
    m.table_id = table.id;
    m.receipt_sha256 = table.receipt_sha256;
    m.root = idx.root;
    m.verified_at = verified_at;
    std::set<std::string> rels;
    for (const Sa2FileResult& r : report.files) {
        size_t i;
        if (r.kind == Sa2FileResult::Missing) continue;
        if (idx.find(r.path, i) == Sa2TreeIndex::Found) rels.insert(idx.files[i].rel);
        if (r.kind == Sa2FileResult::SubstitutedOrig && idx.find(r.detail, i) == Sa2TreeIndex::Found)
            rels.insert(idx.files[i].rel);
    }
    for (const std::string& rel : rels) {
        const Sa2TreeFile& f = idx.files[idx.exact.at(rel)];
        m.rows.push_back({rel, f.size, f.mtime_ns});
    }
    for (const Sa2TreeFile& d : idx.dirs) m.dirs.push_back({d.rel, 0, d.mtime_ns});
    return m;
}

std::string sa2_manifest_text(const Sa2Manifest& m) {
    std::string s = "sa2recomp-manifest 1\n";
    s += "table\t" + m.table_id + "\t" + m.receipt_sha256 + "\n";
    s += "root\t" + m.root + "\n";
    s += "verified\t" + m.verified_at + "\n";
    for (const Sa2ManifestRow& r : m.rows)
        s += "f\t" + std::to_string(r.size) + "\t" + std::to_string(r.mtime_ns) + "\t" + r.rel + "\n";
    for (const Sa2ManifestRow& r : m.dirs)
        s += "d\t0\t" + std::to_string(r.mtime_ns) + "\t" + r.rel + "\n";
    return s;
}

bool sa2_manifest_parse(const std::string& text, Sa2Manifest& m, std::string* error) {
    m = Sa2Manifest{};
    size_t pos = 0;
    int line = 0;
    bool head = false;
    while (pos < text.size()) {
        size_t e = text.find('\n', pos);
        if (e == std::string::npos) e = text.size();
        std::string l = text.substr(pos, e - pos);
        pos = e + 1;
        line++;
        if (l.empty()) continue;
        std::vector<std::string> f;
        size_t a = 0;
        for (int k = 0; k < 3; k++) {  // the path is the last field and may hold anything but '\n'
            size_t t = l.find('\t', a);
            if (t == std::string::npos) break;
            f.push_back(l.substr(a, t - a));
            a = t + 1;
        }
        f.push_back(l.substr(a));
        if (line == 1) {
            head = l == "sa2recomp-manifest 1";
            if (!head) break;
        } else if (f[0] == "table" && f.size() >= 3) {
            m.table_id = f[1];
            m.receipt_sha256 = f[2];
        } else if (f[0] == "root" && f.size() >= 2) {
            m.root = l.substr(5);
        } else if (f[0] == "verified" && f.size() >= 2) {
            m.verified_at = f[1];
        } else if ((f[0] == "f" || f[0] == "d") && f.size() == 4) {
            char* e1 = nullptr;
            char* e2 = nullptr;
            unsigned long long size = std::strtoull(f[1].c_str(), &e1, 10);
            long long mtime = std::strtoll(f[2].c_str(), &e2, 10);
            if (f[1].empty() || f[2].empty() || *e1 || *e2) {
                if (error) *error = "line " + std::to_string(line) + " has a bad number";
                return false;
            }
            (f[0] == "f" ? m.rows : m.dirs).push_back({f[3], uint64_t(size), int64_t(mtime)});
        } else {
            if (error) *error = "line " + std::to_string(line) + " is not a manifest row";
            return false;
        }
    }
    if (!head) {
        if (error) *error = "not a manifest";
        return false;
    }
    return true;
}

std::vector<std::string> sa2_manifest_quick_check(const Sa2Manifest& m, const Sa2InstallTable& table) {
    std::vector<std::string> out;
    if (m.table_id != table.id || m.receipt_sha256 != table.receipt_sha256)
        out.push_back("verified under table " + m.table_id + ", this build carries " + table.id);
    sa2p::Stat st;
    if (!sa2p::stat_path(m.root, st) || !st.is_dir) {
        out.push_back("the folder " + m.root + " is gone");
        return out;
    }
    for (const Sa2ManifestRow& r : m.rows) {
        if (!sa2p::stat_path(m.root + "/" + r.rel, st) || !st.is_file)
            out.push_back(r.rel + ": gone");
        else if (st.size != r.size || st.mtime_ns != r.mtime_ns)
            out.push_back(r.rel + ": size or time changed");
    }
    for (const Sa2ManifestRow& d : m.dirs) {
        std::string host = d.rel.empty() ? m.root : m.root + "/" + d.rel;
        if (!sa2p::stat_path(host, st) || !st.is_dir)
            out.push_back((d.rel.empty() ? std::string(".") : d.rel) + "/: gone");
        else if (st.mtime_ns != d.mtime_ns)
            out.push_back((d.rel.empty() ? std::string(".") : d.rel) + "/: a name in it was added, removed or renamed");
    }
    return out;
}
