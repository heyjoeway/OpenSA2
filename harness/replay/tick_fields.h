/* The words a boot-stretch tick replay compares, one X-macro row each:
 *
 *     TP_FIELD(name, guest virtual address, width in bytes)
 *
 * This file is included by both sides of harness level 2 -- the Mod Loader
 * hook the live oracle runs (native_tick_probe.c) and the lifted build's
 * GameLoop wrapper (wrap_tick_probe.c) -- so the two captures name the same
 * fields at the same addresses by construction rather than by agreement.
 *
 * Provenance: the addresses are the data references the analyzer's function
 * map records for GameLoop (0x00433EE0), the routine whose every exit is the
 * Mod Loader's OnFrame, minus 0x0044C2B0, which is in .text. Names are from
 * the Mod Loader headers where they declare the address (community-guided),
 * and `g_<address>` where they do not. Widths are the header's declared type
 * where there is one, else 4.
 */
TP_FIELD(frame_count,         0x0174B038u, 4)   /* FrameCount, int */
TP_FIELD(game_mode,           0x0174B040u, 4)   /* GameMode, int */
TP_FIELD(g_0174aff9,          0x0174AFF9u, 1)   /* the byte GameLoop's default case tests */
TP_FIELD(current_level,       0x01934B70u, 2)   /* CurrentLevel, __int16 */
TP_FIELD(current_character,   0x01934B80u, 4)   /* CurrentCharacter, int */
TP_FIELD(current_character_2p,0x01934BE4u, 4)   /* CurrentCharacter2P, int */
TP_FIELD(frame_increment,     0x01DEB50Cu, 4)   /* FrameIncrement, int */
TP_FIELD(g_0173a128,          0x0173A128u, 4)
TP_FIELD(g_01934118,          0x01934118u, 4)
TP_FIELD(g_01a5578c,          0x01A5578Cu, 4)
TP_FIELD(g_01a55794,          0x01A55794u, 4)
TP_FIELD(g_01a557c0,          0x01A557C0u, 4)
TP_FIELD(g_01a55864,          0x01A55864u, 4)
TP_FIELD(g_01a5586c,          0x01A5586Cu, 4)
TP_FIELD(g_01a55870,          0x01A55870u, 4)
TP_FIELD(g_01a5a230,          0x01A5A230u, 4)
TP_FIELD(g_01af19f0,          0x01AF19F0u, 4)
TP_FIELD(g_01deb694,          0x01DEB694u, 4)
TP_FIELD(g_01deb698,          0x01DEB698u, 4)
/* Not GameLoop's own references: three words the Mod Loader writes on the
 * native side, sampled so that a difference it causes shows up in the capture
 * instead of being assumed away (2026-09-22 review). VoiceLanguage is set from
 * the loader's profile at init; WindowActive is driven by the loader's window
 * procedure, and WinMain skips GameLoop while it is 0 (0x00401DE0 calls
 * 0x00425DA0). Names and widths from the Mod Loader's SA2Variables.h. */
TP_FIELD(text_language,       0x0174AFD1u, 1)   /* TextLanguage, char */
TP_FIELD(voice_language,      0x0174AFD2u, 1)   /* VoiceLanguage, char */
TP_FIELD(window_active,       0x0174F5ACu, 4)   /* WindowActive, int */
