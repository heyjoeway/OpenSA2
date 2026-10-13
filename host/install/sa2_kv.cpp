// sa2_kv.cpp: see sa2_kv.h.
#include "sa2_kv.h"

#include <cstddef>

namespace {

bool ieq(const std::string& a, const std::string& b) {
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); i++) {
        char x = a[i], y = b[i];
        if (x >= 'A' && x <= 'Z') x = char(x - 'A' + 'a');
        if (y >= 'A' && y <= 'Z') y = char(y - 'A' + 'a');
        if (x != y) return false;
    }
    return true;
}

struct Lexer {
    const std::string& s;
    size_t i = 0;
    int line = 1;
    std::string err;

    enum Tok { End, Open, Close, Str, Cond, Bad };

    explicit Lexer(const std::string& text) : s(text) {
        if (s.size() >= 3 && (unsigned char)s[0] == 0xEF && (unsigned char)s[1] == 0xBB &&
            (unsigned char)s[2] == 0xBF)
            i = 3;
    }

    void skip() {
        while (i < s.size()) {
            char c = s[i];
            if (c == '\n') { line++; i++; }
            else if (c == ' ' || c == '\t' || c == '\r' || c == '\f' || c == '\v') i++;
            else if (c == '/' && i + 1 < s.size() && s[i + 1] == '/') {
                while (i < s.size() && s[i] != '\n') i++;
            } else break;
        }
    }

    Tok next(std::string& out) {
        out.clear();
        skip();
        if (i >= s.size()) return End;
        char c = s[i];
        if (c == '{') { i++; return Open; }
        if (c == '}') { i++; return Close; }
        if (c == '[') {
            size_t j = s.find(']', i);
            if (j == std::string::npos) { err = "unterminated [condition]"; return Bad; }
            out = s.substr(i, j + 1 - i);
            i = j + 1;
            return Cond;
        }
        if (c == '"') {
            i++;
            while (i < s.size() && s[i] != '"') {
                char d = s[i];
                if (d == '\\' && i + 1 < s.size()) {
                    char e = s[i + 1];
                    if (e == '\\') { out += '\\'; i += 2; continue; }
                    if (e == '"') { out += '"'; i += 2; continue; }
                    if (e == 'n') { out += '\n'; i += 2; continue; }
                    if (e == 't') { out += '\t'; i += 2; continue; }
                    out += d;
                    i++;
                    continue;
                }
                if (d == '\n') line++;
                out += d;
                i++;
            }
            if (i >= s.size()) { err = "unterminated quoted string"; return Bad; }
            i++;
            return Str;
        }
        while (i < s.size()) {
            char d = s[i];
            if (d == ' ' || d == '\t' || d == '\r' || d == '\n' || d == '{' || d == '}' || d == '"')
                break;
            out += d;
            i++;
        }
        return Str;
    }
};

bool parse_block(Lexer& lx, Sa2Kv& into, bool top, std::string* error, int depth) {
    if (depth > 64) {
        if (error) *error = "nesting deeper than 64";
        return false;
    }
    std::string tok;
    for (;;) {
        Lexer::Tok t = lx.next(tok);
        if (t == Lexer::Cond) continue;
        if (t == Lexer::End) {
            if (top) return true;
            if (error) *error = "line " + std::to_string(lx.line) + ": a block never closes";
            return false;
        }
        if (t == Lexer::Close) {
            if (!top) return true;
            if (error) *error = "line " + std::to_string(lx.line) + ": '}' with no open block";
            return false;
        }
        if (t == Lexer::Bad) {
            if (error) *error = "line " + std::to_string(lx.line) + ": " + lx.err;
            return false;
        }
        if (t == Lexer::Open) {
            if (error) *error = "line " + std::to_string(lx.line) + ": '{' where a key belongs";
            return false;
        }
        Sa2Kv kv;
        kv.key = tok;
        Lexer::Tok v;
        do v = lx.next(tok); while (v == Lexer::Cond);
        if (v == Lexer::Open) {
            kv.block = true;
            if (!parse_block(lx, kv, false, error, depth + 1)) return false;
        } else if (v == Lexer::Str) {
            kv.value = tok;
        } else if (v == Lexer::Bad) {
            if (error) *error = "line " + std::to_string(lx.line) + ": " + lx.err;
            return false;
        } else {
            if (error)
                *error = "line " + std::to_string(lx.line) + ": key \"" + kv.key + "\" has no value";
            return false;
        }
        into.children.push_back(std::move(kv));
    }
}

}  // namespace

const Sa2Kv* Sa2Kv::find(const std::string& k) const {
    for (const Sa2Kv& c : children)
        if (ieq(c.key, k)) return &c;
    return nullptr;
}

std::string Sa2Kv::get(const std::string& k, const std::string& fallback) const {
    const Sa2Kv* c = find(k);
    return (c && !c->block) ? c->value : fallback;
}

bool sa2_kv_parse(const std::string& text, Sa2Kv& root, std::string* error) {
    root = Sa2Kv{};
    root.block = true;
    Lexer lx(text);
    return parse_block(lx, root, true, error, 0);
}
