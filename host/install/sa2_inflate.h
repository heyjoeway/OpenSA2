// sa2_inflate.h: the installer's own raw-deflate decoder (RFC 1951) and CRC-32, for the ZIP
// route (sa2_zip.h). No zlib and no miniz: the release binary carries no third-party inflate.
//
// The decoder pulls its input through Sa2InflateIo::read and pushes its output through
// Sa2InflateIo::write, so it never needs to suspend in the middle of a symbol. It keeps the
// 32 KiB window deflate needs plus a 1 MiB output chunk, decodes Huffman codes through a
// 10-bit lookup table with a canonical slow path for longer codes, and copies stored blocks
// with memcpy.
//
// Its acceptance rules are zlib's (inflate.c and inftrees.c): an over-subscribed code is bad,
// an incomplete code is bad unless it is a single one-bit code for literals/lengths or
// distances, a block must code end-of-block, and a distance may not reach before the first
// byte written. Every rejection is a return value, never an out-of-bounds read or write:
// truncated input is reported as Truncated, and the sink can stop the decoder at any byte.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

// CRC-32 (the ISO-HDLC polynomial ZIP and zlib use). Start with crc = 0; chain calls.
uint32_t sa2_crc32(uint32_t crc, const void* data, size_t n);

struct Sa2InflateIo {
    // Up to `cap` bytes of compressed input; 0 means the input has ended.
    virtual size_t read(uint8_t* buf, size_t cap) = 0;
    // `n` decoded bytes. False stops the decoder (OutputStopped).
    virtual bool write(const uint8_t* data, size_t n) = 0;
    virtual ~Sa2InflateIo() = default;
};

enum class Sa2InflateStatus {
    Ok,             // the final block ended; everything was written
    Truncated,      // the input ended before the final block did
    BadData,        // the stream breaks a rule of RFC 1951 (`why` says which)
    OutputStopped,  // the sink returned false
};

struct Sa2InflateResult {
    Sa2InflateStatus status = Sa2InflateStatus::BadData;
    uint64_t in_bytes = 0;   // compressed bytes the stream used, to the end of its final block
    uint64_t out_bytes = 0;  // bytes passed to the sink
    std::string why;         // empty on Ok
};

// Decode one raw deflate stream (no zlib or gzip wrapper) from `io`.
Sa2InflateResult sa2_inflate(Sa2InflateIo& io);

const char* sa2_inflate_status_name(Sa2InflateStatus s);
