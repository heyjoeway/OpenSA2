// mini_json.h: the smallest JSON reader the state check needs (the device's keys files, the
// check's parameter files). Numbers keep their text, so integers above 2^53 read exactly.
#pragma once

#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace mini_json {

struct Value {
    enum Kind { Null, Bool, Number, String, Array, Object } kind = Null;
    bool b = false;
    std::string text;                                   // a number's text, or a string
    std::vector<Value> items;
    std::vector<std::pair<std::string, Value>> fields;

    const Value& operator[](const std::string& k) const {
        for (const auto& [n, v] : fields)
            if (n == k) return v;
        throw std::runtime_error("JSON: no field " + k);
    }
    const Value& operator[](size_t i) const {
        if (i >= items.size()) throw std::runtime_error("JSON: index past the array");
        return items[i];
    }
    bool has(const std::string& k) const {
        for (const auto& [n, v] : fields)
            if (n == k) return true;
        return false;
    }
    size_t size() const { return kind == Array ? items.size() : fields.size(); }
    uint64_t u64() const {
        if (kind == Bool) return b;
        if (kind != Number) throw std::runtime_error("JSON: not a number");
        return std::strtoull(text.c_str(), nullptr, 10);
    }
    int64_t i64() const {
        if (kind != Number) throw std::runtime_error("JSON: not a number");
        return std::strtoll(text.c_str(), nullptr, 10);
    }
    uint32_t u32() const { return uint32_t(u64()); }
    double num() const {
        if (kind != Number) throw std::runtime_error("JSON: not a number");
        return std::strtod(text.c_str(), nullptr);
    }
    bool boolean() const {
        if (kind != Bool) throw std::runtime_error("JSON: not a boolean");
        return b;
    }
    const std::string& str() const {
        if (kind != String) throw std::runtime_error("JSON: not a string");
        return text;
    }
};

class Parser {
public:
    explicit Parser(const std::string& s) : s_(s) {}
    Value parse() {
        Value v = value();
        ws();
        if (i_ != s_.size()) fail("trailing text");
        return v;
    }

private:
    const std::string& s_;
    size_t i_ = 0;
    [[noreturn]] void fail(const char* what) { throw std::runtime_error(std::string("JSON: ") + what + " at " + std::to_string(i_)); }
    void ws() { while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\n' || s_[i_] == '\r' || s_[i_] == '\t')) i_++; }
    bool lit(const char* l) {
        size_t n = std::char_traits<char>::length(l);
        if (s_.compare(i_, n, l) == 0) { i_ += n; return true; }
        return false;
    }
    std::string string() {
        if (s_[i_] != '"') fail("expected a string");
        i_++;
        std::string o;
        while (i_ < s_.size() && s_[i_] != '"') {
            char c = s_[i_++];
            if (c == '\\') {
                if (i_ >= s_.size()) fail("bad escape");
                char e = s_[i_++];
                switch (e) {
                case 'n': o += '\n'; break;
                case 't': o += '\t'; break;
                case 'r': o += '\r'; break;
                case 'b': o += '\b'; break;
                case 'f': o += '\f'; break;
                case 'u': {
                    unsigned v = std::stoul(s_.substr(i_, 4), nullptr, 16);
                    i_ += 4;
                    if (v < 0x80) o += char(v);
                    else if (v < 0x800) { o += char(0xC0 | (v >> 6)); o += char(0x80 | (v & 0x3F)); }
                    else { o += char(0xE0 | (v >> 12)); o += char(0x80 | ((v >> 6) & 0x3F)); o += char(0x80 | (v & 0x3F)); }
                    break;
                }
                default: o += e;
                }
            } else {
                o += c;
            }
        }
        if (i_ >= s_.size()) fail("unterminated string");
        i_++;
        return o;
    }
    Value value() {
        ws();
        if (i_ >= s_.size()) fail("unexpected end");
        Value v;
        char c = s_[i_];
        if (c == '{') {
            v.kind = Value::Object;
            i_++;
            ws();
            if (s_[i_] == '}') { i_++; return v; }
            for (;;) {
                ws();
                std::string k = string();
                ws();
                if (s_[i_++] != ':') fail("expected ':'");
                v.fields.emplace_back(k, value());
                ws();
                if (s_[i_] == ',') { i_++; continue; }
                if (s_[i_] == '}') { i_++; return v; }
                fail("expected ',' or '}'");
            }
        }
        if (c == '[') {
            v.kind = Value::Array;
            i_++;
            ws();
            if (s_[i_] == ']') { i_++; return v; }
            for (;;) {
                v.items.push_back(value());
                ws();
                if (s_[i_] == ',') { i_++; continue; }
                if (s_[i_] == ']') { i_++; return v; }
                fail("expected ',' or ']'");
            }
        }
        if (c == '"') { v.kind = Value::String; v.text = string(); return v; }
        if (lit("true")) { v.kind = Value::Bool; v.b = true; return v; }
        if (lit("false")) { v.kind = Value::Bool; v.b = false; return v; }
        if (lit("null")) { v.kind = Value::Null; return v; }
        size_t st = i_;
        while (i_ < s_.size() && (std::isdigit((unsigned char)s_[i_]) || s_[i_] == '-' || s_[i_] == '+' || s_[i_] == '.' ||
                                  s_[i_] == 'e' || s_[i_] == 'E'))
            i_++;
        if (st == i_) fail("unexpected character");
        v.kind = Value::Number;
        v.text = s_.substr(st, i_ - st);
        return v;
    }
};

inline Value parse(const std::string& s) { return Parser(s).parse(); }

}  // namespace mini_json
