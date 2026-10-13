// sa2_kv.h: Valve's text KeyValues, the format of Steam's libraryfolders.vdf and
// appmanifest_<appid>.acf.
//
// What it accepts: quoted tokens with the escapes \\ \" \n \t (any other backslash is kept as
// written, which is how a Windows path written without escaping survives); unquoted tokens
// ending at whitespace, a brace or a quote; // comments to the end of the line; { } blocks;
// a [$CONDITION] tag after a key or value, skipped; a UTF-8 byte-order mark. Keys keep their
// spelling and are looked up ASCII-case-blind, as Steam does ("LibraryFolders" before 2021,
// "libraryfolders" since). Duplicate keys are kept in order. A block that never closes, a
// value missing at the end of input or a stray '}' is an error, with its line.
#pragma once

#include <string>
#include <vector>

struct Sa2Kv {
    std::string key;
    std::string value;           // empty for a block
    bool block = false;
    std::vector<Sa2Kv> children;

    // The first child whose key matches, ASCII-case-blind; nullptr when none.
    const Sa2Kv* find(const std::string& key) const;
    // The first child's value, or `fallback` when absent or a block.
    std::string get(const std::string& key, const std::string& fallback = std::string()) const;
};

// Parses `text` into `root`, a block whose children are the top-level pairs.
bool sa2_kv_parse(const std::string& text, Sa2Kv& root, std::string* error);
