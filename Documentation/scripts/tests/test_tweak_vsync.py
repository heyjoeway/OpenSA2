"""The vsync tweak (host/player/tweaks/vsync.cpp): SA2_TWEAKS=vsync gives a play session the player's
--vsync, PresentationInterval ONE in place of IMMEDIATE.

The tweak's file is compiled with the host's reader (host/player/tweaks.cpp, the system g++ as
host/player/build.sh builds it) and a driver that does what the player's main does: read
SA2_TWEAKS for the player's home, with the caller's reason it is no play session, then ask the tweak
for the run's vsync option, given the --vsync the player had. The driver prints the option; the
tweak's own line is on stderr after the reader's.

The player's main is held to the one call (after the reader, before the run starts), so the option
the tweak returns is the one the device's PresentationInterval, the device line and the report
read. What the presentation does on a display is the live run's to show (the commit's legs).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import tweak_sources as TS  # noqa: E402

PLAYER = ROOT / "host" / "player"
CXX = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_D3D9GPU_PRESENT")
READER = "sa2 host tweaks (player): "
ON = "sa2 host tweaks: vsync on: "

# argv[1]: the caller's reason it is no play session ("-" in play); argv[2]: --vsync given (0 or 1).
DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "tweaks.h"
#include "tweaks/vsync.h"
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    const sa2tw::Reading r = sa2tw::read(std::getenv("SA2_TWEAKS"),
                                         sa2tw::off(std::strcmp(argv[1], "-") ? argv[1] : nullptr), "player");
    sa2tw::g_on = r.on;
    if (!r.line.empty()) std::fprintf(stderr, "%s\n", r.line.c_str());
    std::printf("%d\n", sa2tw::vsync(std::strcmp(argv[2], "1") == 0) ? 1 : 0);
    return 0;
}
"""


def build(d: Path, name: str, *defines: str) -> Path:
    out = d / name
    done = subprocess.run([*CXX, *defines, "-I", str(PLAYER), str(d / "driver.cpp"), str(PLAYER / "tweaks.cpp"),
                           str(PLAYER / "tweaks" / "vsync.cpp"), "-o", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out


@pytest.fixture(scope="module")
def exes(tmp_path_factory) -> dict:
    """The GPU player's build (host/d3d9gpu/build.sh: -DSA2_PLAYER_GPU=1) and the DXVK player's
    (host/player/build.sh: none)."""
    if shutil.which("g++") is None:
        pytest.fail("g++ is not installed, and the host's reader is built with it (host/player/build.sh)")
    d = tmp_path_factory.mktemp("vsync")
    (d / "driver.cpp").write_text(DRIVER)
    return {"gpu": build(d, "vsync-gpu", "-DSA2_PLAYER_GPU=1"), "dxvk": build(d, "vsync-dxvk")}


@pytest.fixture(scope="module", params=["gpu", "dxvk"])
def exe(exes, request) -> Path:
    return exes[request.param]


def run(exe: Path, asked: bool, why: str = "-", **env: str) -> tuple[int, list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), why, "1" if asked else "0"], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return int(p.stdout), p.stderr.splitlines()


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
@pytest.mark.parametrize("asked", [False, True])
def test_off_the_option_is_the_one_given_and_the_tweak_says_nothing(exe, env, asked):
    vsync, err = run(exe, asked, **env)
    assert vsync == int(asked)
    assert not [line for line in err if line.startswith(ON)]


def test_on_in_play_a_run_without_vsync_presents_at_interval_one(exe):
    assert run(exe, False, SA2_TWEAKS="vsync") == (1, [
        READER + "vsync on (SA2_TWEAKS=vsync)",
        ON + "PresentationInterval ONE in place of IMMEDIATE, as --vsync (swapchain FIFO in place of "
             "IMMEDIATE, or of MAILBOX where the window has no IMMEDIATE)"])


def test_on_with_vsync_given_already_says_the_run_is_as_it_was(exe):
    assert run(exe, True, SA2_TWEAKS="no-event-bars, vsync") == (1, [
        READER + "vsync on (SA2_TWEAKS=no-event-bars, vsync)",
        ON + "--vsync is given already, so the run is as it was"])


def test_on_with_no_window_presented_to_says_it_changes_nothing(exes):
    """Host 2 offscreen (SA2_D3D9GPU_PRESENT=none) presents to no window. The DXVK player has no such
    variable, names none of host 2's (host/player/build.sh refuses a binary that holds
    SA2_D3D9GPU), and says what any run without --vsync says."""
    assert run(exes["gpu"], False, SA2_TWEAKS="vsync", SA2_D3D9GPU_PRESENT="none") == (1, [
        READER + "vsync on (SA2_TWEAKS=vsync)",
        ON + "PresentationInterval ONE in place of IMMEDIATE, but SA2_D3D9GPU_PRESENT=none presents to no window, "
             "so it changes nothing"])
    assert run(exes["dxvk"], False, SA2_TWEAKS="vsync", SA2_D3D9GPU_PRESENT="none") == (1, [
        READER + "vsync on (SA2_TWEAKS=vsync)",
        ON + "PresentationInterval ONE in place of IMMEDIATE, as --vsync (swapchain FIFO in place of "
             "IMMEDIATE, or of MAILBOX where the window has no IMMEDIATE)"])
    assert b"SA2_D3D9GPU" not in exes["dxvk"].read_bytes()


@pytest.mark.parametrize("why, env, said", [
    ("outside play (play is --interactive with --input-path)", {},
     "ignored outside play (play is --interactive with --input-path)"),
    ("in a synthetic-input run (--synthetic-keys or --synthetic-input)", {},
     "ignored in a synthetic-input run (--synthetic-keys or --synthetic-input)"),
    ("-", {"SA2_REPLAY_TICKS": "/dev/null"}, "ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)"),
])
def test_out_of_play_and_in_a_replay_a_stream_keeps_its_interval(exe, why, env, said):
    assert run(exe, False, why, SA2_TWEAKS="vsync", **env) == (0, [READER + f"SA2_TWEAKS=vsync {said}"])


def test_the_players_main_asks_the_tweak_once_between_the_reader_and_the_run():
    """The option the tweak returns is the run's: main sets it after the reader and before the
    Player is made, and the device's PresentationInterval is that option's."""
    src = (PLAYER / "player.cpp").read_text()
    assert src.count("sa2tw::vsync(") == 1
    main = src[src.index("\nint main(int argc, char** argv) {"):]
    reader, call, player = (main.index("sa2tw::g_on = tweaks.on;"), main.index("o.vsync = sa2tw::vsync(o.vsync);"),
                            main.index("Player p(o);"))
    assert reader < call < player
    assert re.search(r"pp\.PresentationInterval = o_\.vsync \? D3DPRESENT_INTERVAL_ONE : D3DPRESENT_INTERVAL_IMMEDIATE;",
                     src)


def test_the_summary_promises_no_tearing_only_where_off_would_use_immediate():
    """The summary (the README's line and the Tweaks card's) names where the switch stops tearing:
    off takes MAILBOX, already tear-free, where the window has no IMMEDIATE (Android, Wayland)."""
    [row] = [r for r in TS.every_row(ROOT / "runtime") if r["name"] == "vsync"]
    assert (row["report"], row["summary"], row["home"]) == (
        "vsync on",
        "presents each frame on the display's refresh (vsync); stops tearing only where off would use "
        "IMMEDIATE (X11, Windows), about a frame more delay",
        "player")
