"""The host tweak hold-toggle (host/player/tweaks/hold-toggle.cpp): B as a toggle.

The tweak's file is compiled with the host's reader (host/player/tweaks.cpp) and the player's real
key sender (host/player/input.cpp), and a driver calls them as Player::send_pads and
Player::service_pads do: each set of slots goes through sa2tw::hold_toggle::apply and then
KeySender::send_pads into a real FIFO, which writes a record only for a change (input.h). The
records the game would read are compared with literal lines:

  * on (SA2_TWEAKS=hold-toggle read in play): a press of B gives a record with B held, its release
    none, the next press a record with B let go; START, the window losing the input focus (owed()
    asks for the send with no pad event) and the slot emptying let a latch go; other buttons and
    other slots pass as they are, each slot its own latch; and the stderr lines say each change by
    name, after the one line that says the case left alone;
  * off (no SA2_TWEAKS, and SA2_TWEAKS=hold-toggle in a replay): the same presses give the records
    the player sends today, B held exactly while it is pressed, and the tweak says nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SDL_INCLUDE = ROOT / "private" / "s5-tools" / "install" / "include"
GXX = shutil.which(os.environ.get("CXX", "g++"))
PLAYER = ROOT / "host" / "player"

DRIVER = r"""
#include "input.h"
#include "tweaks.h"
#include "tweaks/hold-toggle.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

static int rd = -1;
static sa2_input::KeySender* k = nullptr;

// Every whole pad record waiting: "<step> <seq> <slot>:<connected>:<buttons> ..." for the slots
// connected or holding a button.
static void drain(const char* tag) {
    unsigned char r[4096];
    const ssize_t n = read(rd, r, sizeof r);
    for (ssize_t off = 0; n > 0 && off + 72 <= n; off += 72) {
        const unsigned seq = r[off + 4] | r[off + 5] << 8 | r[off + 6] << 16 | (unsigned)r[off + 7] << 24;
        std::printf("%s %u", tag, seq);
        for (int s = 0; s < 4; s++) {
            const unsigned char* p = r + off + 8 + 16 * s;
            const unsigned buttons = p[2] | p[3] << 8;
            if (p[0] || buttons) std::printf(" %d:%d:%04X", s, p[0], buttons);
        }
        std::printf("\n");
    }
}

// Player::send_pads: the tweak's step, then the sender.
static void send(const char* tag, sa2_input::PadSet set, bool focused = true) {
    sa2tw::hold_toggle::apply(&set, focused);
    k->send_pads(set);
    std::fflush(stderr);
    drain(tag);
}

int main(int argc, char** argv) {
    const std::string mode = argv[2];
    if (mode == "replay") setenv("SA2_REPLAY_TICKS", "1", 1);
    const sa2tw::Reading r = sa2tw::read(mode == "unset" ? nullptr : "hold-toggle", sa2tw::off(nullptr), "player");
    sa2tw::g_on = r.on;
    std::fprintf(stderr, "%s\n", r.line.c_str());
    const std::string path = std::string(argv[1]) + "/pads.fifo";
    mkfifo(path.c_str(), 0600);
    rd = open(path.c_str(), O_RDONLY | O_NONBLOCK);
    sa2_input::KeySender sender(path);
    k = &sender;
    std::printf("start %d\n", sender.start());
    sa2_input::PadSet p;
    p.slot[0].connected = true;
    send("connect", p);
    p.slot[0].buttons = 0x2000; send("press-b", p);
    p.slot[0].buttons = 0; send("release-b", p);
    p.slot[0].buttons = 0x2000; send("press-b-again", p);
    p.slot[0].buttons = 0; send("release-b-again", p);
    p.slot[0].buttons = 0x1000; send("press-a", p);
    p.slot[0].buttons = 0; send("release-a", p);
    p.slot[0].buttons = 0x2000; send("press-b", p);
    p.slot[0].buttons = 0; send("release-b", p);
    p.slot[0].buttons = 0x1000; send("jump-while-held", p);
    p.slot[0].buttons = 0; send("land", p);
    p.slot[0].buttons = 0x0010; send("press-start", p);
    p.slot[0].buttons = 0; send("release-start", p);
    p.slot[0].buttons = 0x2000; send("press-b", p);
    p.slot[0].buttons = 0; send("release-b", p);
    std::printf("owed-focused %d\n", sa2tw::hold_toggle::owed(true));
    std::printf("owed-unfocused %d\n", sa2tw::hold_toggle::owed(false));
    send("focus-lost", p, false);                // Player::service_pads: owed, so a send
    std::printf("owed-after %d\n", sa2tw::hold_toggle::owed(false));
    p.slot[0].buttons = 0x2000; send("press-b-focused", p);
    p.slot[0].buttons = 0; send("release-b", p);
    p.slot[1].connected = true; send("connect-1", p);
    p.slot[1].buttons = 0x2000; send("press-b-1", p);
    p.slot[1].buttons = 0; send("release-b-1", p);
    p.slot[0].buttons = 0x2000; send("press-b-0", p);
    p.slot[0].buttons = 0; send("release-b-0", p);
    p.slot[1] = sa2_input::PadState{}; send("remove-1", p);
    p.slot[1].connected = true; send("reconnect-1", p);
    sender.close();
    drain("close");
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    if GXX is None or not (SDL_INCLUDE / "SDL3" / "SDL_events.h").is_file():
        pytest.fail(f"no C++ compiler, or no SDL3 headers under {SDL_INCLUDE}")
    d = tmp_path_factory.mktemp("hold_toggle")
    (d / "driver.cpp").write_text(DRIVER)
    exe = d / "driver"
    r = subprocess.run([GXX, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        f"-I{SDL_INCLUDE}", f"-I{PLAYER}", str(d / "driver.cpp"), str(PLAYER / "input.cpp"),
                        str(PLAYER / "tweaks.cpp"), str(PLAYER / "tweaks" / "hold-toggle.cpp"), "-o", str(exe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


def run(driver: Path, tmp_path: Path, mode: str) -> tuple[list[str], list[str]]:
    r = subprocess.run([str(driver), str(tmp_path), mode], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines(), r.stderr.splitlines()


def test_on_a_press_holds_b_and_the_next_lets_it_go(driver, tmp_path):
    out, err = run(driver, tmp_path, "on")
    assert out == [
        "start 1",
        "connect 0 0:1:0000",
        "press-b 1 0:1:2000",              # held
        # release-b: no record, B stays held
        "press-b-again 2 0:1:0000",        # let go
        # release-b-again: nothing changed
        "press-a 3 0:1:1000",              # A as it is
        "release-a 4 0:1:0000",
        "press-b 5 0:1:2000",
        "jump-while-held 6 0:1:3000",      # A with B still held
        "land 7 0:1:2000",
        "press-start 8 0:1:0010",          # START lets B go
        "release-start 9 0:1:0000",
        "press-b 10 0:1:2000",
        "owed-focused 0",
        "owed-unfocused 1",
        "focus-lost 11 0:1:0000",          # losing the focus lets it go
        "owed-after 0",
        "press-b-focused 12 0:1:2000",
        "connect-1 13 0:1:2000 1:1:0000",
        "press-b-1 14 0:1:2000 1:1:2000",  # each slot its own latch
        "press-b-0 15 0:1:0000 1:1:2000",
        "remove-1 16 0:1:0000",            # the slot emptied: its latch let go
        "reconnect-1 17 0:1:0000 1:1:0000",
        "close 18",                        # close(): every slot let go
    ]
    assert err == [
        "sa2 host tweaks (player): B toggles (SA2_TWEAKS=hold-toggle)",
        "sa2 host tweaks: B toggles: a press of B holds it and the next lets it go, on every pad and the touch "
        "controls; START, losing the input focus and a pad's removal let it go; left alone: the game's menus, which "
        "the player cannot tell from play (a B latched in one stays held until the next press of B or START)",
        "sa2 host tweaks: B toggles: slot 0 B held",
        "sa2 host tweaks: B toggles: slot 0 B let go (pressed again)",
        "sa2 host tweaks: B toggles: slot 0 B held",
        "sa2 host tweaks: B toggles: slot 0 B let go (START)",
        "sa2 host tweaks: B toggles: slot 0 B held",
        "sa2 host tweaks: B toggles: slot 0 B let go (the window lost the input focus)",
        "sa2 host tweaks: B toggles: slot 0 B held",
        "sa2 host tweaks: B toggles: slot 1 B held",
        "sa2 host tweaks: B toggles: slot 0 B let go (pressed again)",
        "sa2 host tweaks: B toggles: slot 1 B let go (the slot emptied)",
    ]


# Off, every press of B is sent while it is pressed: the player's records as they were.
OFF = [
    "start 1",
    "connect 0 0:1:0000",
    "press-b 1 0:1:2000",
    "release-b 2 0:1:0000",
    "press-b-again 3 0:1:2000",
    "release-b-again 4 0:1:0000",
    "press-a 5 0:1:1000",
    "release-a 6 0:1:0000",
    "press-b 7 0:1:2000",
    "release-b 8 0:1:0000",
    "jump-while-held 9 0:1:1000",
    "land 10 0:1:0000",
    "press-start 11 0:1:0010",
    "release-start 12 0:1:0000",
    "press-b 13 0:1:2000",
    "release-b 14 0:1:0000",
    "owed-focused 0",
    "owed-unfocused 0",
    "owed-after 0",
    "press-b-focused 15 0:1:2000",
    "release-b 16 0:1:0000",
    "connect-1 17 0:1:0000 1:1:0000",
    "press-b-1 18 0:1:0000 1:1:2000",
    "release-b-1 19 0:1:0000 1:1:0000",
    "press-b-0 20 0:1:2000 1:1:0000",
    "release-b-0 21 0:1:0000 1:1:0000",
    "remove-1 22 0:1:0000",
    "reconnect-1 23 0:1:0000 1:1:0000",
    "close 24",
]


@pytest.mark.parametrize("mode, line", [
    ("unset", ""),
    ("replay", "sa2 host tweaks (player): SA2_TWEAKS=hold-toggle ignored in a replay (SA2_REPLAY_TICKS or "
               "SA2_REPLAY_WORDS is set)"),
])
def test_off_the_records_are_the_players_own(driver, tmp_path, mode, line):
    out, err = run(driver, tmp_path, mode)
    assert out == OFF
    assert err == [line]


def test_the_player_calls_it_where_it_sends_and_services_the_pads():
    """player.cpp's lines: the step just before the sender in send_pads, and the focus check just
    before service_pads's send; nothing else of the tweak's."""
    player = (PLAYER / "player.cpp").read_text()
    focus = "win_ && (SDL_GetWindowFlags(win_) & SDL_WINDOW_INPUT_FOCUS)"
    assert f"    sa2tw::hold_toggle::apply(&set, {focus});\n    ++pad_sets_;\n    input_->send_pads(set);" in player
    assert (f"    if (sa2tw::hold_toggle::owed({focus})) pads_dirty_ = true;\n"
            "    if (pads_dirty_) send_pads();\n}") in player
    assert len(re.findall(r"hold_toggle|hold-toggle", player)) == 3   # those two and the include
