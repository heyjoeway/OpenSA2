// sa2_json.cpp: see sa2_json.h.
#include "sa2_json.h"

#include <cstdio>
#include <cctype>
#include <cstdlib>

namespace sa2j {

namespace {

const Value kNull;

struct Parser {
    const std::string& t;
    size_t i = 0;
    std::string err;
    int depth = 0;

    void ws() {
        while (i < t.size() && (t[i] == ' ' || t[i] == '\n' || t[i] == '\r' || t[i] == '\t')) i++;
    }
    bool fail(const std::string& why) {
        if (err.empty()) err = why + " at byte " + std::to_string(i);
        return false;
    }
    static void utf8(std::string& out, unsigned cp) {
        if (cp < 0x80) out += char(cp);
        else if (cp < 0x800) { out += char(0xC0 | (cp >> 6)); out += char(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) {
            out += char(0xE0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 0x3F));
            out += char(0x80 | (cp & 0x3F));
        } else {
            out += char(0xF0 | (cp >> 18)); out += char(0x80 | ((cp >> 12) & 0x3F));
            out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F));
        }
    }
    bool hex4(unsigned& v) {
        if (i + 4 > t.size()) return fail("a short \\u escape");
        v = 0;
        for (int k = 0; k < 4; k++) {
            char c = t[i++];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= unsigned(c - '0');
            else if (c >= 'a' && c <= 'f') v |= unsigned(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= unsigned(c - 'A' + 10);
            else return fail("a bad \\u escape");
        }
        return true;
    }
    bool string(std::string& out) {
        if (t[i] != '"') return fail("no string");
        i++;
        for (;;) {
            if (i >= t.size()) return fail("an unterminated string");
            char c = t[i++];
            if (c == '"') return true;
            if (static_cast<unsigned char>(c) < 0x20) return fail("a control character in a string");
            if (c != '\\') { out += c; continue; }
            if (i >= t.size()) return fail("an unterminated escape");
            char e = t[i++];
            switch (e) {
            case '"': out += '"'; break;
            case '\\': out += '\\'; break;
            case '/': out += '/'; break;
            case 'b': out += '\b'; break;
            case 'f': out += '\f'; break;
            case 'n': out += '\n'; break;
            case 'r': out += '\r'; break;
            case 't': out += '\t'; break;
            case 'u': {
                unsigned v = 0;
                if (!hex4(v)) return false;
                if (v >= 0xD800 && v < 0xDC00 && i + 6 <= t.size() && t[i] == '\\' && t[i + 1] == 'u') {
                    i += 2;
                    unsigned lo = 0;
                    if (!hex4(lo)) return false;
                    v = 0x10000 + ((v - 0xD800) << 10) + (lo - 0xDC00);
                }
                utf8(out, v);
                break;
            }
            default: return fail("a bad escape");
            }
        }
    }
    bool value(Value& v) {
        if (++depth > 256) return fail("nesting too deep");
        ws();
        if (i >= t.size()) return fail("a missing value");
        char c = t[i];
        bool ok = true;
        if (c == '{') {
            v.kind = Value::Object;
            i++;
            ws();
            if (i < t.size() && t[i] == '}') { i++; depth--; return true; }
            for (;;) {
                ws();
                std::string k;
                if (i >= t.size() || !string(k)) return fail("an object key");
                ws();
                if (i >= t.size() || t[i] != ':') return fail("no ':'");
                i++;
                v.o.emplace_back(std::move(k), Value());
                if (!value(v.o.back().second)) return false;
                ws();
                if (i < t.size() && t[i] == ',') { i++; continue; }
                if (i < t.size() && t[i] == '}') { i++; break; }
                return fail("no ',' or '}'");
            }
        } else if (c == '[') {
            v.kind = Value::Array;
            i++;
            ws();
            if (i < t.size() && t[i] == ']') { i++; depth--; return true; }
            for (;;) {
                v.a.emplace_back();
                if (!value(v.a.back())) return false;
                ws();
                if (i < t.size() && t[i] == ',') { i++; continue; }
                if (i < t.size() && t[i] == ']') { i++; break; }
                return fail("no ',' or ']'");
            }
        } else if (c == '"') {
            v.kind = Value::String;
            ok = string(v.s);
        } else if (t.compare(i, 4, "true") == 0) {
            v.kind = Value::Bool; v.b = true; i += 4;
        } else if (t.compare(i, 5, "false") == 0) {
            v.kind = Value::Bool; v.b = false; i += 5;
        } else if (t.compare(i, 4, "null") == 0) {
            v.kind = Value::Null; i += 4;
        } else if (c == '-' || (c >= '0' && c <= '9')) {
            size_t j = i;
            while (j < t.size() && (std::isdigit(static_cast<unsigned char>(t[j])) || t[j] == '-' ||
                                    t[j] == '+' || t[j] == '.' || t[j] == 'e' || t[j] == 'E')) j++;
            v.kind = Value::Number;
            v.s = t.substr(i, j - i);
            char* end = nullptr;
            v.n = std::strtod(v.s.c_str(), &end);
            if (!end || *end) return fail("a bad number");
            i = j;
        } else {
            return fail("an unexpected character");
        }
        depth--;
        return ok;
    }
};

}  // namespace

const Value* Value::get(const std::string& key) const {
    if (kind != Object) return nullptr;
    for (const auto& kv : o)
        if (kv.first == key) return &kv.second;
    return nullptr;
}

const Value& Value::at(const std::string& key) const {
    const Value* v = get(key);
    return v ? *v : kNull;
}

bool parse(const std::string& text, Value& out, std::string* error) {
    Parser p{text, 0, std::string(), 0};
    out = Value();
    bool ok = p.value(out);
    if (ok) {
        p.ws();
        if (p.i != text.size()) ok = p.fail("text after the value");
    }
    if (!ok && error) *error = p.err;
    return ok;
}

std::string quote(const std::string& s) {
    std::string out = "\"";
    for (unsigned char c : s) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) {
                char b[8];
                std::snprintf(b, sizeof b, "\\u%04x", c);
                out += b;
            } else {
                out += char(c);
            }
        }
    }
    return out + "\"";
}

}  // namespace sa2j
