"""The host tweak camera-invert: the camera turns the other way.

host/player/tweaks/camera-invert.cpp (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST, home the player)
negates each pad slot's right stick X and swaps its LT and RT in every set of slots the player sends
(player.cpp send_pads), which swaps the game's L and R, the bits its camera turns on; the file's
header has the reading of the image it rests on.

  * the tweak's own code, compiled with the system g++ beside the host's reader
    (host/player/tweaks.cpp) and a driver that reads SA2_TWEAKS as the player's main does: with
    the tweak on, every slot's X is negated (-32768 becomes 32767) and its triggers swapped, and
    nothing else in the set changes, and its one line is said once; with the value unset, another
    tweak named, out of play or in a replay, the set is unchanged and the tweak says nothing;
  * the player, end to end, when SA2_TEST_UINPUT=1: the second host's player (built by
    host/d3d9gpu/build.sh, or SA2_PLAYER_GPU) plays the synthetic stream live in a play session
    (--interactive with --input-path, no synthetic input, so the tweak may be on), with a virtual
    Xbox 360 pad made through /dev/uinput, and the records on the input FIFO carry the pad's right
    stick X and triggers as they are without the tweak, and negated and swapped with it. It
    needs write access to /dev/uinput and a display, and the pad is seen by every program on the
    machine for the few seconds it lives, so it is asked for and not run by default.

  .venv/bin/python -m pytest Documentation/scripts/tests/test_tweak_camera_invert.py
  SA2_TEST_UINPUT=1 .venv/bin/python -m pytest Documentation/scripts/tests/test_tweak_camera_invert.py
"""

from __future__ import annotations

import fcntl
import importlib.util
import os
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLAYER = ROOT / "host" / "player"
CXX = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"]
LINE = ("sa2 host tweaks: camera turn inverted: every pad slot's right stick X negated and LT and RT swapped, "
        "the touch overlay's and SA2_PAD_MAP's included; the right stick's Y is left alone; not limited to "
        "levels (the player does not know the game's mode), so menus and every other screen that reads the "
        "right stick's X or the triggers read them swapped too\n")

# The driver: SA2_TWEAKS read and its line said as the player's main does (argv[1] the reason it is
# no play session, "-" in play), then the slots of argv[2..] (8 numbers a slot: connected buttons lt rt lx
# ly rx ry) through the tweak twice, each result printed as one line.
DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "tweaks.h"
#include "tweaks/camera-invert.h"
static void print(const sa2_input::PadSet& s) {
    for (const sa2_input::PadState& p : s.slot)
        std::printf("%d %u %u %u %d %d %d %d ", int(p.connected), unsigned(p.buttons), unsigned(p.lt), unsigned(p.rt),
                    int(p.lx), int(p.ly), int(p.rx), int(p.ry));
    std::printf("\n");
}
int main(int argc, char** argv) {
    if (argc != 2 + 8 * int(sa2_input::kPadSlots)) return 2;
    const sa2tw::Reading r = sa2tw::read(std::getenv("SA2_TWEAKS"), sa2tw::off(std::strcmp(argv[1], "-") ? argv[1] : nullptr),
                                         "player");
    sa2tw::g_on = r.on;
    if (!r.line.empty()) std::fprintf(stderr, "%s\n", r.line.c_str());   // as the player's main
    sa2_input::PadSet set;
    for (size_t i = 0; i < sa2_input::kPadSlots; i++) {
        long v[8];
        for (int k = 0; k < 8; k++) v[k] = std::strtol(argv[2 + 8 * i + k], nullptr, 0);
        set.slot[i] = {v[0] != 0, uint16_t(v[1]), uint8_t(v[2]), uint8_t(v[3]), int16_t(v[4]), int16_t(v[5]),
                       int16_t(v[6]), int16_t(v[7])};
    }
    sa2_input::PadSet once = set;
    sa2tw::camera_invert(&once);
    print(once);
    sa2tw::camera_invert(&set);
    sa2tw::camera_invert(&set);
    print(set);
    return 0;
}
"""

# Four slots: every field set, the stick's two ends, a slot not connected.
SLOTS = [(1, 0x1010, 255, 0, 1000, -2000, 20000, 3000),
         (1, 0x0001, 10, 200, -32768, 32767, -32768, -32768),
         (1, 0, 0, 0, 0, 0, 32767, 0),
         (0, 0, 0, 0, 0, 0, 0, 0)]
INVERTED = [(1, 0x1010, 0, 255, 1000, -2000, -20000, 3000),
            (1, 0x0001, 200, 10, -32768, 32767, 32767, -32768),
            (1, 0, 0, 0, 0, 0, -32767, 0),
            (0, 0, 0, 0, 0, 0, 0, 0)]
# Twice: the set it was, but the stick's far left, which once made the far right, is one short.
TWICE = [SLOTS[0], (1, 0x0001, 10, 200, -32768, 32767, -32767, -32768), SLOTS[2], SLOTS[3]]


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    if shutil.which("g++") is None:
        pytest.fail("g++ is not installed, and the player's tweaks are built with it (host/player/build.sh)")
    install = Path(os.environ.get("SA2_S5_INSTALL") or ROOT / "private/s5-tools/install").resolve()
    if not (install / "include" / "SDL3" / "SDL_events.h").is_file():
        pytest.fail(f"no SDL3 headers under {install}/include (host/player/input.h includes them): "
                    "private/s5-tools/build.sh installs them, or SA2_S5_INSTALL names an install")
    d = tmp_path_factory.mktemp("camera_invert")
    (d / "driver.cpp").write_text(DRIVER)
    exe = d / "driver"
    done = subprocess.run([*CXX, "-I", str(PLAYER), "-I", str(install / "include"), str(d / "driver.cpp"),
                           str(PLAYER / "tweaks.cpp"), str(PLAYER / "tweaks" / "camera-invert.cpp"), "-o", str(exe)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return exe


def through(exe: Path, why: str = "-", **env: str) -> tuple[list[list[tuple[int, ...]]], str]:
    """The slots after the tweak once and after it twice, and the driver's stderr."""
    e = {k: v for k, v in os.environ.items() if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    done = subprocess.run([str(exe), why, *(str(v) for s in SLOTS for v in s)], capture_output=True, text=True,
                          env=dict(e, **env))
    assert done.returncode == 0, done.stderr
    rows = []
    for line in done.stdout.splitlines():
        v = [int(x) for x in line.split()]
        rows.append([tuple(v[i:i + 8]) for i in range(0, len(v), 8)])
    return rows, done.stderr


def test_on_negates_each_slots_right_stick_x_and_swaps_its_triggers(driver):
    rows, err = through(driver, SA2_TWEAKS="camera-invert")
    assert rows == [INVERTED, TWICE]
    assert err == "sa2 host tweaks (player): camera turn inverted (SA2_TWEAKS=camera-invert)\n" + LINE


def test_on_beside_other_tweaks(driver):
    rows, err = through(driver, SA2_TWEAKS="no-event-bars, camera-invert,bogus")
    assert rows == [INVERTED, TWICE]
    assert err.endswith(LINE) and err.count(LINE) == 1


@pytest.mark.parametrize("why, env, said", [
    ("-", {}, ""),
    ("-", {"SA2_TWEAKS": ""}, ""),
    ("-", {"SA2_TWEAKS": "no-event-bars"},
     "sa2 host tweaks (player): none on (SA2_TWEAKS=no-event-bars)\n"),
    ("-", {"SA2_TWEAKS": "Camera-Invert"},
     "sa2 host tweaks (player): none on (SA2_TWEAKS=Camera-Invert)\n"),
    ("outside play (play is --interactive with --input-path)", {"SA2_TWEAKS": "camera-invert"},
     "sa2 host tweaks (player): SA2_TWEAKS=camera-invert ignored outside play (play is --interactive with "
     "--input-path)\n"),
    ("in a synthetic-input run (--synthetic-keys or --synthetic-input)", {"SA2_TWEAKS": "camera-invert"},
     "sa2 host tweaks (player): SA2_TWEAKS=camera-invert ignored in a synthetic-input run (--synthetic-keys or "
     "--synthetic-input)\n"),
    ("-", {"SA2_TWEAKS": "camera-invert", "SA2_REPLAY_TICKS": "/dev/null"},
     "sa2 host tweaks (player): SA2_TWEAKS=camera-invert ignored in a replay (SA2_REPLAY_TICKS or "
     "SA2_REPLAY_WORDS is set)\n"),
])
def test_off_leaves_every_slot_as_it_is_and_says_nothing_of_its_own(driver, why, env, said):
    rows, err = through(driver, why, **env)
    assert rows == [SLOTS, SLOTS]
    assert err == said


# ------------------------------------------------------------------ the player, end to end

# linux/input-event-codes.h and linux/uinput.h
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR = 0x130, 0x131, 0x133, 0x134, 0x136, 0x137
BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x13A, 0x13B, 0x13C, 0x13D, 0x13E
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 0x10, 0x11
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
ABS_CNT = 64
STICK_X = 20000            # the right stick pushed right, the run's value
TRIGGER = 255              # LT all the way (a trigger's 0..255 is XInput's as it is)


class UinputPad:
    """A virtual Xbox 360 pad (USB 045e:028e, xpad's axes and buttons), through /dev/uinput's
    legacy uinput_user_dev setup, which every kernel since 2.6 takes."""

    def __init__(self) -> None:
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        for b in (BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, b)
        lo, hi = [0] * ABS_CNT, [0] * ABS_CNT
        for a in (ABS_X, ABS_Y, ABS_RX, ABS_RY):
            lo[a], hi[a] = -32768, 32767
        for a in (ABS_Z, ABS_RZ):
            lo[a], hi[a] = 0, 255
        for a in (ABS_HAT0X, ABS_HAT0Y):
            lo[a], hi[a] = -1, 1
        for a in (ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, a)
        name = b"camera-invert test pad".ljust(80, b"\0")
        dev = name + struct.pack("<HHHHI", 3, 0x045E, 0x028E, 0x0114, 0)     # BUS_USB, id, ff_effects_max
        dev += struct.pack(f"<{ABS_CNT}i", *hi) + struct.pack(f"<{ABS_CNT}i", *lo)
        dev += struct.pack(f"<{ABS_CNT}i", *([0] * ABS_CNT)) * 2                # absfuzz, absflat
        assert os.write(self.fd, dev) == len(dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def axis(self, code: int, value: int) -> None:
        ev = struct.pack("<qqHHi", 0, 0, EV_ABS, code, value) + struct.pack("<qqHHi", 0, 0, EV_SYN, 0, 0)
        os.write(self.fd, ev)

    def close(self) -> None:
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)


@pytest.fixture(scope="module")
def uinput_runs(tmp_path_factory):
    if os.environ.get("SA2_TEST_UINPUT") != "1":
        pytest.skip("the end-to-end run makes a virtual pad every program on the machine sees: "
                    "SA2_TEST_UINPUT=1 asks for it")
    if not os.access("/dev/uinput", os.W_OK):
        pytest.fail("SA2_TEST_UINPUT=1, and /dev/uinput is not writable by this user")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: the interactive mode opens a window")
    spec = importlib.util.spec_from_file_location("pads_touch", Path(__file__).with_name("test_player_pads_touch.py"))
    pt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pt)
    t = tmp_path_factory.mktemp("camera_invert_e2e")
    player = pt.build_player(t / "bin")
    stream = pt.build_synth(t)
    out = {}
    for name, value in (("off", None), ("on", "camera-invert")):
        pad = UinputPad()
        try:
            time.sleep(1.0)                   # udev gives the node its access before SDL opens it
            moves = {"stop": False}

            def drive() -> None:
                # the pad's right stick right and LT held, then let go, in a loop while the player runs
                while not moves["stop"]:
                    pad.axis(ABS_RX, STICK_X)
                    pad.axis(ABS_Z, TRIGGER)
                    time.sleep(0.3)
                    pad.axis(ABS_RX, 0)
                    pad.axis(ABS_Z, 0)
                    time.sleep(0.3)

            th = threading.Thread(target=drive)
            th.start()
            # the window need not have focus; SA2_TWEAKS empty is no value
            settings = {"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS": "1", "SA2_TWEAKS": value or ""}
            try:
                out[name] = pt.play(player, stream, t / name, None, settings)
            finally:
                moves["stop"] = True
                th.join()
        finally:
            pad.close()
            time.sleep(0.5)
        out[name]["log"] = (t / name / "player.out").read_text(errors="replace")
    return out


def pushed(records: list) -> set[tuple[int, int, int]]:
    """(rx, lt, rt) of every connected slot in the records that has the stick or a trigger moved."""
    return {(s[6], s[2], s[3]) for _, slots in records for s in slots if s[0] and (s[6] or s[2] or s[3])}


def test_the_player_sends_the_pad_as_it_is_with_the_tweak_off(uinput_runs):
    r = uinput_runs["off"]
    assert r["exit"] == 0, r["log"][-2000:]
    assert "sa2 host tweaks" not in r["log"]
    assert (STICK_X, TRIGGER, 0) in pushed(r["records"]), r["records"]
    assert all(rx >= 0 and rt == 0 for rx, lt, rt in pushed(r["records"]))


def test_the_player_inverts_the_pad_with_the_tweak_on(uinput_runs):
    r = uinput_runs["on"]
    assert r["exit"] == 0, r["log"][-2000:]
    assert "sa2 host tweaks (player): camera turn inverted (SA2_TWEAKS=camera-invert)\n" in r["log"]
    assert r["log"].count(LINE) == 1
    assert (-STICK_X, 0, TRIGGER) in pushed(r["records"]), r["records"]
    assert all(rx <= 0 and lt == 0 for rx, lt, rt in pushed(r["records"]))
