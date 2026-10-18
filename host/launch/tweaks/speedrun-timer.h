// speedrun-timer.h: the speedrun-timer host tweak (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST, home
// "launcher"): the game's in-game timer and stage state, published on the local machine for a
// speedrun timer (LiveSplit, or any reader of the datagram below).
//
// Read-only by construction. The game is not changed, on or off: no line of the runtime, no
// variable of its environment. The launcher, the game's parent, reads the game process's memory
// (Linux: /proc/<pid>/mem opened read-only; Windows: ReadProcessMemory on the process handle
// CreateProcess gave), and nothing here can write it. The player cannot do this: it replays the
// game's Direct3D 9 stream and has no guest memory (host/player/player.cpp). The guest's 32-bit
// space is one host mapping of SA2_GUEST_END bytes (runtime/guest_memory.h), at an address the
// host chose, so the watch finds it by its image: the PE headers of sonic2app.exe at guest
// 0x00400000 ("MZ", "PE\0\0", optional header magic 0x10B, ImageBase 0x00400000).
//
// The words, read once each poll (every 2 ms) and published when one changes, so once per game
// frame. Each is the reading of StageTimerUpdate (0x0044DC80), which adds 1 to FrameCountIngame
// unless TimeStopped is nonzero, and then advances the stage timer unless TimerStopped is 1 or
// the byte at 0x0174AFF9 is 1: frames wrap at 60, seconds at 60, and the timer holds at 99:59:59.
//   TimerStopped        0x0174AFDA  u8
//   TimerMinutes        0x0174AFDB  u8
//   TimerSeconds        0x0174AFDC  u8
//   TimerFrames         0x0174AFDD  u8    sixtieths of a second
//   TimeStopped         0x0174AFF7  u8    also stops FrameCountIngame
//   (timer stop)        0x0174AFF9  u8
//   goal latch          0x0174B002  u8    set by AwardWin (0x0043E6D0) at 0x0043E781
//                                         (Documentation/decisions/2026-09-23-S4-stage-route.md)
//   FrameCount          0x0174B038  u32
//   FrameCountIngame    0x0174B03C  u32
//   GameMode            0x0174B040  u32   5 is GameMode_Level
//   CurrentLevel        0x01934B70  i16
//   CurrentCharacter    0x01934B80  u8
//   GameState           0x01934BE0  i16   16 is GameStates_Ingame
// A poll reads the 0x0174AFDA block, the 0x01934B70 block and the first block again, and takes
// them when the two first reads agree (a frame's writes not half seen), at most 4 tries.
//
// The datagram: 64 bytes, little-endian, to UDP 127.0.0.1:16838, one per change.
//    0 u32 magic "SA2T"          4 u16 version 1           6 u16 size 64
//    8 u32 seq, 1 for the first datagram of the session, +1 each
//   12 u32 FrameCount           16 u32 FrameCountIngame   20 u32 GameMode
//   24 i16 GameState            26 i16 CurrentLevel       28 u8 CurrentCharacter
//   29 u8 TimerMinutes          30 u8 TimerSeconds        31 u8 TimerFrames
//   32 u8 TimerStopped          33 u8 the 0x0174AFF9 stop 34 u8 TimeStopped     35 u8 goal latch
//   36 u32 stage frames: (minutes * 60 + seconds) * 60 + frames
//   40 u32 game time, frames (below)  44 u32 splits so far  48 u32 1 once the run has started
//   52 u32 milliseconds since the watch began            56 8 bytes, 0
// A datagram is sent when a byte of 12..51 changes: one or two a frame, since the game moves
// FrameCount and the timer at different points of its frame (measured: a poll between the two
// sees the new timer beside the old FrameCount). A reader keys on the timer fields, not on
// datagrams per frame. A frame shorter than a poll (a run faster than real time) can be skipped;
// the timer fields carry the whole count.
//
// Game time, the run, and LiveSplit Server (TCP 127.0.0.1:16834, its default; tried every 2 s, and
// silently absent when it does not run, which is said once). The run starts when a stage's timer
// first leaves 0:00:00 in GameMode 5 ("starttimer", "initgametime", "pausegametime": LiveSplit's
// game time then moves only as it is told). A split is the goal latch rising in GameMode 5
// ("split", after the game time). Game time is the sum of each cleared stage's time, the timer as
// it stood when it next fell (to the next stage's 0:00:00), and the running stage's timer, sent as
// "setgametime" when it changes. Left alone, and said once: a stage left or restarted without its
// goal (an exit or a restart from the pause menu, a race lost) adds nothing, and a checkpoint
// restart is the game's own timer.
// The run's end is LiveSplit's reset, which is the runner's: never sent from here. While connected,
// the watch asks "getcurrenttimerphase" every 250 ms (replies are read without waiting, a line
// each); when LiveSplit's timer comes to NotRunning from any other phase (or is first seen
// NotRunning on a connection) while a run is under way, the run ends, said by name: game time,
// splits and the started field go to 0, and the next stage start begins a new run (a second
// "starttimer"). A reply to a question asked before this run's "starttimer" is not taken. With no
// LiveSplit Server connected nothing can say a run ended, so the datagram's run is the first one
// of the game process, to the game's end (said at the start).
#pragma once

#include <cstdint>
#include <string>

namespace sa2st {

class Watch {
public:
    // Each line the watch says ("sa2 host tweaks: <report>: ..."), with no "sa2recomp: " prefix.
    // Called from the watch's own thread, so it must be safe beside the caller's other threads.
    using Sink = void (*)(const std::string& line);

    // `game`: the game's sa2l::Proc id (its pid; on Windows its process HANDLE). Nothing at all is
    // done unless sa2tw::on(sa2tw::SA2_TWEAK_SPEEDRUN_TIMER); otherwise a thread polls until the
    // game's memory can no longer be read or the Watch is destroyed, and then says what it sent.
    // Every line goes to stderr ("sa2recomp: " and the line) and to `said` (the launcher's record,
    // launcher.log), the last of them, the end summary, before the destructor returns.
    Watch(intptr_t game, Sink said);
    ~Watch();
    Watch(const Watch&) = delete;
    Watch& operator=(const Watch&) = delete;

private:
    struct State;
    State* s_ = nullptr;
};

}  // namespace sa2st
