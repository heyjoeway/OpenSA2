// sa2_config.cpp: see sa2_config.h.
#include "sa2_config.h"

#include <cstdlib>

#include "sa2_platform.h"

const Sa2UserDefaultFile SA2_USER_DEFAULTS[] = {
    {"Config/UserConfig.cfg",
     "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
     "<Configs FullScreen=\"0\" Display=\"0\" Res=\"0\" Width=\"1280\" Height=\"720\" RefreshRate=\"60\" "
     "Language=\"0\" Analytics=\"0\" />\n"},
    {"Config/Keyboard.cfg",
     "<?xml version=\"1.0\" ?>\n"
     "<Setup Layout=\"0\" />\n"
     "<Players>\n"
     "    <Player InputWay=\"0\" Vibrate=\"0\">\n"
     "        <key index=\"22\" />\n"
     "        <key index=\"18\" />\n"
     "        <key index=\"0\" />\n"
     "        <key index=\"3\" />\n"
     "        <key index=\"8\" />\n"
     "        <key index=\"9\" />\n"
     "        <key index=\"20\" />\n"
     "        <key index=\"10\" />\n"
     "        <key index=\"25\" />\n"
     "        <key index=\"16\" />\n"
     "        <key index=\"37\" />\n"
     "        <key index=\"23\" />\n"
     "        <key index=\"4\" />\n"
     "        <key index=\"37\" />\n"
     "        <key index=\"27\" />\n"
     "        <key index=\"67\" />\n"
     "    </Player>\n"
     "    <Player InputWay=\"0\" Vibrate=\"0\">\n"
     "        <key index=\"83\" />\n"
     "        <key index=\"65\" />\n"
     "        <key index=\"73\" />\n"
     "        <key index=\"79\" />\n"
     "        <key index=\"102\" />\n"
     "        <key index=\"98\" />\n"
     "        <key index=\"101\" />\n"
     "        <key index=\"99\" />\n"
     "        <key index=\"66\" />\n"
     "        <key index=\"104\" />\n"
     "        <key index=\"37\" />\n"
     "        <key index=\"76\" />\n"
     "        <key index=\"105\" />\n"
     "        <key index=\"37\" />\n"
     "        <key index=\"28\" />\n"
     "        <key index=\"111\" />\n"
     "    </Player>\n"
     "</Players>\n"},
};
const size_t SA2_USER_DEFAULT_COUNT = sizeof SA2_USER_DEFAULTS / sizeof SA2_USER_DEFAULTS[0];

namespace {

// ASCII case fold, the runtime's rule (sa2_install_fold's; the launcher links this file without
// sa2_verify.cpp).
std::string fold(std::string s) {
    for (char& c : s)
        if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    return s;
}

// The entry of `dir` named `name`: the exact spelling first, else the first case-folded match, as
// runtime/sa2_files.c resolves one component. "" when there is none or `dir` cannot be listed.
std::string entry_folded(const std::string& dir, const std::string& name) {
    std::vector<std::string> names;
    if (!sa2p::list_dir(dir, names)) return {};
    std::string hit;
    for (const std::string& n : names) {
        if (n == name) return n;
        if (hit.empty() && fold(n) == fold(name)) hit = n;
    }
    return hit;
}

// `root`/`dir`/`name` is a file, each component resolved case-blind.
bool holds(const std::string& root, const std::string& dir, const std::string& name) {
    const std::string d = entry_folded(root, dir);
    if (d.empty()) return false;
    const std::string f = entry_folded(root + "/" + d, name);
    sa2p::Stat st;
    return !f.empty() && sa2p::stat_path(root + "/" + d + "/" + f, st) && st.is_file;
}

}  // namespace

bool sa2_user_defaults_ensure(const std::string& read_root, const std::string& overlay,
                              std::vector<Sa2UserDefault>& out, std::string* error) {
    out.clear();
    for (size_t i = 0; i < SA2_USER_DEFAULT_COUNT; i++) {
        const std::string rel = SA2_USER_DEFAULTS[i].path;
        const size_t slash = rel.find('/');
        const std::string dir = rel.substr(0, slash), name = rel.substr(slash + 1);
        Sa2UserDefault d;
        d.path = rel;
        d.in_read_root = holds(read_root, dir, name);
        d.in_overlay = holds(overlay, dir, name);
        if (!d.in_read_root && !d.in_overlay) {
            const std::string have = entry_folded(overlay, dir);
            const std::string folder = overlay + "/" + (have.empty() ? dir : have);
            const std::string path = folder + "/" + name;
            if (!sa2p::make_dirs(folder) || !sa2p::write_file_atomic(path, SA2_USER_DEFAULTS[i].text)) {
                if (error) *error = "cannot write the default " + rel + " at " + path;
                out.push_back(d);
                return false;
            }
            d.written = true;
            d.written_at = path;
        }
        out.push_back(d);
    }
    return true;
}

std::string sa2_config_path() {
    std::string h = sa2p::config_home();
    return h.empty() ? std::string() : h + "/sa2recomp/sa2recomp.cfg";
}

std::string sa2_config_text(const Sa2Config& c) {
    std::string s = "# sa2recomp configuration, written by sa2-install. The game folder named by\n"
                    "# read_root is never written; the game's own writes go to overlay.\n";
    auto kv = [&](const char* k, const std::string& v) { s += std::string(k) + "=" + v + "\n"; };
    kv("format", "1");
    kv("table.id", c.table_id);
    kv("table.receipt_sha256", c.receipt_sha256);
    kv("source.kind", c.source_kind);
    kv("source.path", c.source_path);
    kv("read_root", c.read_root);
    kv("overlay", c.overlay);
    kv("exe", c.exe);
    kv("verified.at", c.verified_at);
    kv("verified.mode", c.verified_mode);
    kv("manifest", c.manifest);
    kv("policy", c.policy);
    kv("mod_loader", c.mod_loader ? "1" : "0");
    kv("extras", std::to_string(c.extras));
    for (const auto& p : c.substitutions) kv("substitution", p.first + " <- " + p.second);
    return s;
}

bool sa2_config_parse(const std::string& text, Sa2Config& c, std::string* error) {
    c = Sa2Config{};
    size_t pos = 0;
    int line = 0;
    bool format = false;
    while (pos < text.size()) {
        size_t e = text.find('\n', pos);
        if (e == std::string::npos) e = text.size();
        std::string l = text.substr(pos, e - pos);
        pos = e + 1;
        line++;
        if (!l.empty() && l.back() == '\r') l.pop_back();
        if (l.empty() || l[0] == '#') continue;
        size_t eq = l.find('=');
        if (eq == std::string::npos) {
            if (error) *error = "line " + std::to_string(line) + " has no '='";
            return false;
        }
        std::string k = l.substr(0, eq), v = l.substr(eq + 1);
        if (k == "format") {
            if (v != "1") {
                if (error) *error = "format " + v + " is not 1";
                return false;
            }
            format = true;
        } else if (k == "table.id") c.table_id = v;
        else if (k == "table.receipt_sha256") c.receipt_sha256 = v;
        else if (k == "source.kind") c.source_kind = v;
        else if (k == "source.path") c.source_path = v;
        else if (k == "read_root") c.read_root = v;
        else if (k == "overlay") c.overlay = v;
        else if (k == "exe") c.exe = v;
        else if (k == "verified.at") c.verified_at = v;
        else if (k == "verified.mode") c.verified_mode = v;
        else if (k == "manifest") c.manifest = v;
        else if (k == "policy") c.policy = v;
        else if (k == "mod_loader") c.mod_loader = v == "1";
        else if (k == "extras") {
            char* end = nullptr;
            unsigned long long n = std::strtoull(v.c_str(), &end, 10);
            if (v.empty() || *end) {
                if (error) *error = "line " + std::to_string(line) + ": extras is not a number";
                return false;
            }
            c.extras = size_t(n);
        }
        else if (k == "substitution") {
            size_t a = v.find(" <- ");
            if (a == std::string::npos) {
                if (error) *error = "line " + std::to_string(line) + ": a substitution without ' <- '";
                return false;
            }
            c.substitutions.emplace_back(v.substr(0, a), v.substr(a + 4));
        }
        // An unknown key is kept for a later format's reader: ignored here.
    }
    if (!format) {
        if (error) *error = "no format line";
        return false;
    }
    return true;
}

bool sa2_config_write(const std::string& path, const Sa2Config& c, std::string* error) {
    std::string text = sa2_config_text(c);
    size_t lines = 0;
    for (char ch : text) lines += ch == '\n';
    size_t expected = 16 + c.substitutions.size();  // 2 comment lines + 14 keys + substitutions
    if (lines != expected) {
        if (error) *error = "a value holds a line break";
        return false;
    }
    size_t slash = path.find_last_of('/');
    if (slash != std::string::npos && !sa2p::make_dirs(path.substr(0, slash))) {
        if (error) *error = "cannot create " + path.substr(0, slash);
        return false;
    }
    if (!sa2p::write_file_atomic(path, text)) {
        if (error) *error = "cannot write " + path;
        return false;
    }
    return true;
}
