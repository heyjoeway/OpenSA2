// sdl_mark.h: the startup line that says which SDL3 a player verified, patched or stock.
//
// SDL's zlib licence, clause 2: "Altered source versions must be plainly marked as such". The
// project's patched SDL3 (patches/sdl3, not from the SDL project) carries the mark in its
// revision string, which tools/s5/build_host_deps.sh sets through SDL_VENDOR_INFO:
// "SDL-<revision> (sa2-recomp sdl3-patches <id>)". Both players also say it in words on every
// run, once the loaded library has passed its hash and revision checks and before anything is
// drawn (patches/sdl3/README.md):
//
//   SDL3: <revision>: patched by sa2-recomp (patches/sdl3), not the SDL project's release
//   SDL3: <revision>: stock
#pragma once
#include <string>

inline constexpr char kSa2SdlPatchMark[] = " (sa2-recomp sdl3-patches ";

inline std::string sa2_sdl_mark_line(const std::string& revision) {
    const bool patched = revision.find(kSa2SdlPatchMark) != std::string::npos;
    return "SDL3: " + revision + ": " +
           (patched ? "patched by sa2-recomp (patches/sdl3), not the SDL project's release" : "stock");
}
