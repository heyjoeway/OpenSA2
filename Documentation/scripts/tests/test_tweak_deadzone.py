"""The host tweak "deadzone" (host/player/tweaks/deadzone.cpp): each gamepad's left stick, round.

A driver is compiled with the player's own files (host/player/tweaks/deadzone.cpp, the reader
host/player/tweaks.cpp and the sender host/player/input.cpp) and does what the player does: it reads
SA2_TWEAKS as main does, then for each pad set calls sa2tw::deadzone and KeySender::send_pads, as
Player::send_pads does, into a real FIFO, and prints each gamepad record's sticks as the game will
read them. The tests compare them with literal values:

  * on: a resting stick's drift in any direction is the centre; past the deadzone the reach is
    scaled from 0, its direction kept; at and past the outer edge it is full tilt (32767), a
    square gate's corner included, so no direction reaches further than another;
  * the right stick, the triggers and the buttons are sent as they were, and the report line
    says once what the tweak does and the cases it leaves alone (the right stick, the touch
    overlay's stick);
  * off (SA2_TWEAKS unset, another tweak named, or a replay) every record is the raw stick and
    the tweak says nothing.

The touch overlay's stick never reaches the tweak (player.cpp calls it on the physical pads before
slot 0 is shared with the overlay), which the last test holds in player.cpp's send_pads.
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
P = ROOT / "host" / "player"

DRIVER = r"""
#include "input.h"
#include "tweaks.h"
#include "tweaks/deadzone.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

static int i16(const unsigned char* p) { return int16_t(p[0] | p[1] << 8); }

// Each whole pad record waiting: per slot "connected buttons lt rt lx ly rx ry".
static void drain(int fd) {
    unsigned char r[4096];
    const ssize_t n = read(fd, r, sizeof r);
    for (ssize_t off = 0; n > 0 && off + 72 <= n; off += 72) {
        std::printf("record");
        for (int s = 0; s < 4; s++) {
            const unsigned char* p = r + off + 8 + 16 * s;
            std::printf(" | %u %04X %u %u %d %d %d %d", p[0], p[2] | p[3] << 8, p[4], p[5], i16(p + 6),
                        i16(p + 8), i16(p + 10), i16(p + 12));
        }
        std::printf("\n");
    }
}

static sa2_input::PadState pad(int lx, int ly, int rx = 0, int ry = 0, uint16_t buttons = 0, uint8_t lt = 0) {
    sa2_input::PadState s;
    s.connected = true;
    s.buttons = buttons;
    s.lt = lt;
    s.lx = int16_t(lx); s.ly = int16_t(ly); s.rx = int16_t(rx); s.ry = int16_t(ry);
    return s;
}

int main(int argc, char** argv) {
    if (argc >= 2 && !std::strcmp(argv[1], "stick")) {
        for (int i = 2; i + 1 < argc; i += 2) {
            int16_t x = int16_t(std::atoi(argv[i])), y = int16_t(std::atoi(argv[i + 1]));
            sa2tw::deadzone_stick(&x, &y);
            std::printf("%s,%s -> %d,%d\n", argv[i], argv[i + 1], x, y);
        }
        return 0;
    }
    // "records DIR": main's reading (player.cpp), in play, then three pad sets.
    const sa2tw::Reading tweaks = sa2tw::read(std::getenv("SA2_TWEAKS"), sa2tw::off(nullptr), "player");
    sa2tw::g_on = tweaks.on;
    if (!tweaks.line.empty()) std::fprintf(stderr, "%s\n", tweaks.line.c_str());
    const std::string path = std::string(argv[2]) + "/pads.fifo";
    if (mkfifo(path.c_str(), 0600) != 0) return 2;
    const int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK);
    sa2_input::KeySender k(path);
    if (fd < 0 || !k.start()) return 3;
    k.pump();
    sa2_input::PadSet sets[3];
    sets[0].slot[0] = pad(3000, 2000, 0, 0, 0x1000);        // drift, and A held
    sets[0].slot[1] = pad(32767, 32767);                    // a square gate's corner
    sets[0].slot[3] = pad(16384, 0, 3000, -2000, 0, 200);   // half tilt; the right stick drifting
    sets[1] = sets[0];
    sets[1].slot[0] = pad(-2781, -2781, 0, 0, 0x1000);      // just past the deadzone
    sets[2] = sets[1];
    sets[2].slot[0] = pad(12000, -9000, 0, 0, 0x1000);
    sets[2].slot[1] = pad(-32768, 0);
    for (sa2_input::PadSet& set : sets) {
        sa2tw::deadzone(&set);                              // Player::send_pads, in its order
        k.send_pads(set);
        drain(fd);
    }
    close(fd);
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    if GXX is None:
        pytest.fail("g++ is not installed, and the player's files are built with it (host/player/build.sh)")
    if not (SDL_INCLUDE / "SDL3" / "SDL_events.h").is_file():
        pytest.skip(f"no SDL3 headers under {SDL_INCLUDE} (input.h includes them)")
    d = tmp_path_factory.mktemp("deadzone")
    (d / "driver.cpp").write_text(DRIVER)
    exe = d / "driver"
    r = subprocess.run([GXX, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        f"-I{SDL_INCLUDE}", f"-I{P}", str(d / "driver.cpp"), str(P / "tweaks" / "deadzone.cpp"),
                        str(P / "tweaks.cpp"), str(P / "input.cpp"), "-o", str(exe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


def records(exe: Path, tmp_path: Path, **env: str) -> tuple[list[str], list[str]]:
    e = {k: v for k, v in os.environ.items() if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    e.update(env)
    r = subprocess.run([str(exe), "records", str(tmp_path)], capture_output=True, text=True, timeout=30, env=e)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines(), r.stderr.splitlines()


OFF_SLOT = "0 0000 0 0 0 0 0 0"      # a slot not connected: zeroes

RAW = [
    f"record | 1 1000 0 0 3000 2000 0 0 | 1 0000 0 0 32767 32767 0 0 | {OFF_SLOT} | 1 0000 200 0 16384 0 3000 -2000",
    f"record | 1 1000 0 0 -2781 -2781 0 0 | 1 0000 0 0 32767 32767 0 0 | {OFF_SLOT} | 1 0000 200 0 16384 0 3000 -2000",
    f"record | 1 1000 0 0 12000 -9000 0 0 | 1 0000 0 0 -32768 0 0 0 | {OFF_SLOT} | 1 0000 200 0 16384 0 3000 -2000",
]

SAID = ("sa2 host tweaks: round stick deadzone: each gamepad's left stick, centre to 12% of its reach (3932) at "
        "rest, full tilt from 95% (31129), scaled between; the right stick and the touch overlay's stick are left "
        "as they are")


def test_on_the_left_sticks_are_round_and_the_rest_of_each_pad_as_it_was(driver, tmp_path):
    out, err = records(driver, tmp_path, SA2_TWEAKS="deadzone")
    assert out == [
        # drift at rest is the centre; the corner is full tilt on the circle; half tilt scaled
        # from the deadzone's edge; the right stick's drift, the trigger and A as they were
        f"record | 1 1000 0 0 0 0 0 0 | 1 0000 0 0 23170 23170 0 0 | {OFF_SLOT} | 1 0000 200 0 15002 0 3000 -2000",
        # just past the deadzone: a step of 1, no jump
        f"record | 1 1000 0 0 -1 -1 0 0 | 1 0000 0 0 23170 23170 0 0 | {OFF_SLOT} | 1 0000 200 0 15002 0 3000 -2000",
        # the direction kept (4:-3); -32768 is full tilt, as 32767 is
        f"record | 1 1000 0 0 10668 -8001 0 0 | 1 0000 0 0 -32767 0 0 0 | {OFF_SLOT} | 1 0000 200 0 15002 0 3000 -2000",
    ]
    assert err == ["sa2 host tweaks (player): round stick deadzone (SA2_TWEAKS=deadzone)", SAID]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_TWEAKS": "deadzone", "SA2_REPLAY_TICKS": "100"},
                                 {"SA2_TWEAKS": "deadzone", "SA2_REPLAY_WORDS": "x"}])
def test_off_every_record_is_the_raw_stick_and_the_tweak_says_nothing(driver, tmp_path, env):
    out, err = records(driver, tmp_path, **env)
    assert out == RAW
    assert SAID not in err and not any(x.startswith("sa2 host tweaks: ") for x in err)


def test_the_stick_by_its_distance_from_the_centre(driver):
    pairs = ["0", "0", "3932", "0", "0", "-3932", "4000", "0", "16384", "0", "0", "-20000", "1500", "-30000",
             "-7000", "24000", "31128", "0", "31129", "0", "32767", "0", "-32768", "-32768"]
    r = subprocess.run([str(driver), "stick", *pairs], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        "0,0 -> 0,0",
        "3932,0 -> 0,0",               # the deadzone's edge (12% of 32767, 3932.04)
        "0,-3932 -> 0,0",
        "4000,0 -> 82,0",              # past it, scaled from 0
        "16384,0 -> 15002,0",
        "0,-20000 -> 0,-19359",
        "1500,-30000 -> 1571,-31413",  # the direction kept
        "-7000,24000 -> -7107,24368",
        "31128,0 -> 32766,0",          # just short of the outer edge (95%, 31128.65)
        "31129,0 -> 32767,0",          # at it: full tilt
        "32767,0 -> 32767,0",
        "-32768,-32768 -> -23170,-23170",
    ]
    assert r.stderr == ""            # deadzone_stick alone says nothing


def test_the_player_rounds_the_physical_pads_before_the_overlay_shares_slot_0():
    """Player::send_pads calls the tweak after the pads are copied into the set and before the
    touch overlay's stick is combined with slot 0, so the overlay's stick is never changed."""
    text = (P / "player.cpp").read_text()
    body = re.search(r"\nvoid Player::send_pads\(\) \{\n(.*?)\n\}\n", text, re.S)
    assert body, "player.cpp has no Player::send_pads"
    lines = [x.strip() for x in body.group(1).splitlines()]
    copy = lines.index("if (pad_slots_[i].pad) set.slot[i] = pad_slots_[i].state;")
    assert lines[copy + 1].startswith("sa2tw::deadzone(&set);")
    assert lines[copy + 2] == "if (touch_) {"
    assert text.count("sa2tw::deadzone(") == 1
