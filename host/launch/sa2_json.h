// sa2_json.h: a small JSON reader and writer for the launcher's records: it reads the game's run
// record (harness/crt_probe.c's) and the player's report, and writes the launcher's own .runrec.
// Numbers are kept as doubles and as their text; strings are UTF-8 with \uXXXX decoded.
#pragma once

#include <map>
#include <memory>
#include <string>
#include <vector>

namespace sa2j {

struct Value {
    enum Kind { Null, Bool, Number, String, Array, Object } kind = Null;
    bool b = false;
    double n = 0;
    std::string s;                                  // a string, or a number's text
    std::vector<Value> a;
    std::vector<std::pair<std::string, Value>> o;   // in document order

    const Value* get(const std::string& key) const;          // nullptr when absent
    const Value& at(const std::string& key) const;           // a Null when absent
    bool is_true() const { return kind == Bool && b; }
    std::string str() const { return kind == String ? s : ""; }
    double num(double otherwise = -1) const { return kind == Number ? n : otherwise; }
};

// False with `error` (and the byte offset) on anything that is not one JSON value.
bool parse(const std::string& text, Value& out, std::string* error);

// Writing: a quoted, escaped JSON string.
std::string quote(const std::string& s);

}  // namespace sa2j
