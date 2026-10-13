// sha256.h: SHA-256 (FIPS 180-4) for the player's report, which pins the
// stream, the player, DXVK, SDL3 and dxvk.conf by content. The x86 SHA
// extensions are used when the CPU has them (the boot's 965 MB stream is hashed
// while it is read), with a portable path otherwise; test_stream.cpp holds both
// to the FIPS 180-4 examples and to each other.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

class Sha256 {
public:
    Sha256();
    void update(const void* data, size_t n);
    std::string hex();                       // finishes; call once
    static bool hardware();                  // the SHA extensions are in use
    static void force_portable(bool on);     // for the tests

private:
    void blocks(const uint8_t* p, size_t nblocks);
    uint32_t h_[8];
    uint8_t buf_[64];
    size_t fill_ = 0;
    uint64_t total_ = 0;
};

// The sha256 of a whole file, or "" when it cannot be read.
std::string sha256_file(const std::string& path);
