"""reduce-shake (runtime/tweaks/reduce-shake.c): the camera's shake offsets are scaled to a quarter
for the call of CameraApplyScreenView (0x004EB550) that draws them, and put back after it.

The tweak's file is built with runtime/sa2_tweaks.c and the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted body and the runtime's
accessors. Guest memory is the CameraData array (two cameras of 0x24D8 bytes from 0x01DCFF40) and
FrameCountIngame (0x0174B03C). The stand-in `__real_sub_4eb550` does what the lifted body does with
them: for the camera in ESI, applied (+0x16C) = position (+0x194) + shake offset (+0x154), each
float, and it notes the offsets it read. A call given "w" also writes new offsets inside the body, the case
the wrap must not undo.

Each call prints "<applied x y z> | <offsets after the call> | <offsets the body read> | <pokes>".

The offsets the driver and the header use are checked against the pinned unpacked image [private],
which these tests need (they fail, not skip, without it): the body's three fields, and every place in
.text that addresses the shake offsets by a displacement or an immediate base.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
SOURCE = RUNTIME / "tweaks" / "reduce-shake.c"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
PINS = ROOT / "tools" / "pins.json"

# CameraData: one 0x24D8-byte entry per camera from 0x01DCFF40; the fields 0x004EB550 uses.
CAMS, CAM_SIZE = 0x01DCFF40, 0x24D8
OFFSETS, POSITION, APPLIED = 0x154, 0x194, 0x16C

# argv: calls "esi,x,y,z[,w]"; the position is (100, 200, 300) for every camera.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define CAMS @CAMS@u
#define CAM_SIZE @CAM_SIZE@u
#define OFFSETS @OFFSETS@u
#define POSITION @POSITION@u
#define APPLIED @APPLIED@u
#define FRAME 0x0174B03Cu
static uint8_t cams[2 * CAM_SIZE];
static uint32_t frame = 777, gpr[8], pokes;
static int write_inside;
static float seen[3];

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= CAMS && a + n <= CAMS + sizeof cams) return cams + (a - CAMS);
    if (a == FRAME && n == 4) return (uint8_t *)&frame;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    pokes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static float *field(uint32_t cam, uint32_t off) { return (float *)(cams + CAM_SIZE * cam + off); }

void *__real_sub_4eb550(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t cam = gpr[SA2_ESI];
    if (cam >= 2) { seen[0] = seen[1] = seen[2] = -1.0f; return memory; }
    for (int i = 0; i < 3; i++) {
        seen[i] = field(cam, OFFSETS)[i];
        field(cam, APPLIED)[i] = field(cam, POSITION)[i] + seen[i];
    }
    if (write_inside) for (int i = 0; i < 3; i++) field(cam, OFFSETS)[i] = 9.0f;
    return memory;
}
void *__wrap_sub_4eb550(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        float o[3];
        char w = 0;
        unsigned cam;
        if (sscanf(argv[i], "%u,%f,%f,%f,%c", &cam, &o[0], &o[1], &o[2], &w) < 4) return 2;
        write_inside = w == 'w';
        gpr[SA2_ESI] = cam;
        if (cam < 2) {
            const float pos[3] = {100.0f, 200.0f, 300.0f};
            memcpy(field(cam, POSITION), pos, sizeof pos);
            memcpy(field(cam, OFFSETS), o, sizeof o);
            memset(field(cam, APPLIED), 0, 12);
        }
        pokes = 0;
        int token;
        if (__wrap_sub_4eb550(&token, 0x004EB550u, &token) != &token) return 3;
        if (cam < 2) {
            float *a = field(cam, APPLIED), *s = field(cam, OFFSETS);
            printf("%g %g %g | %g %g %g | %g %g %g | %u\n", a[0], a[1], a[2], s[0], s[1], s[2],
                   seen[0], seen[1], seen[2], pokes);
        } else {
            printf("out | %u\n", pokes);
        }
    }
    return 0;
}
"""


def driver() -> str:
    names = {"CAMS": CAMS, "CAM_SIZE": CAM_SIZE, "OFFSETS": OFFSETS, "POSITION": POSITION,
             "APPLIED": APPLIED}
    text = DRIVER
    for name, value in names.items():
        text = text.replace(f"@{name}@", f"0x{value:X}")
    assert "@" not in text
    return text


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("reduce_shake")
    (d / "driver.c").write_text(driver())
    out = d / "rs"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "reduce-shake.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


SHAKES = ("0,4,-8,2", "1,0.5,0,-1", "0,0,0,0", "0,-0,0,-0")
FULL = ["104 192 302 | 4 -8 2 | 4 -8 2 | 0",
        "100.5 200 299 | 0.5 0 -1 | 0.5 0 -1 | 0",
        "100 200 300 | 0 0 0 | 0 0 0 | 0",
        "100 200 300 | -0 0 -0 | -0 0 -0 | 0"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_alone(exe, env):
    out, err = run(exe, *SHAKES, **env)
    assert out == FULL
    assert err == ([] if not env.get("SA2_TWEAKS") else
                   ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"])


def test_on_draws_a_quarter_of_the_shake_and_puts_the_offsets_back(exe):
    out, err = run(exe, *SHAKES, "0,4,-8,2", SA2_TWEAKS="reduce-shake")
    # Applied = position + offset / 4; the offsets after the call are the game's own, bit for bit;
    # the body read the quarter; two pokes (the quarter, then the saved values). No shake: no poke.
    assert out == ["101 198 300.5 | 4 -8 2 | 1 -2 0.5 | 2",
                   "100.125 200 299.75 | 0.5 0 -1 | 0.125 0 -0.25 | 2",
                   "100 200 300 | 0 0 0 | 0 0 0 | 0",
                   "100 200 300 | -0 0 -0 | -0 0 -0 | 0",
                   "101 198 300.5 | 4 -8 2 | 1 -2 0.5 | 2"]
    assert err == ["sa2 tweaks: camera shake reduced (SA2_TWEAKS=reduce-shake)",
                   "sa2 tweaks: camera shake reduced: the first shake (camera 0, offset 4 -8 2, game "
                   "frame 777) drawn a quarter as far"]


def test_ignored_in_a_replay(exe):
    out, err = run(exe, *SHAKES, SA2_TWEAKS="reduce-shake", SA2_REPLAY_TICKS="/dev/null")
    assert out == FULL
    assert err == ["sa2 tweaks: SA2_TWEAKS=reduce-shake ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_with_the_other_tweak_both_are_reported(exe):
    out, err = run(exe, "0,4,-8,2", SA2_TWEAKS="reduce-shake,no-event-bars")
    assert out == ["101 198 300.5 | 4 -8 2 | 1 -2 0.5 | 2"]
    assert err[0] == ("sa2 tweaks: event-bars off, camera shake reduced "
                      "(SA2_TWEAKS=reduce-shake,no-event-bars)")


def test_offsets_the_body_writes_are_kept_and_said_once(exe):
    out, err = run(exe, "0,4,-8,2,w", "0,4,-8,2,w", SA2_TWEAKS="reduce-shake")
    assert out == ["101 198 300.5 | 9 9 9 | 1 -2 0.5 | 1"] * 2
    assert err.count("sa2 tweaks: camera shake reduced: camera 0's shake offsets at 0x01DD0094 "
                     "changed inside 0x004EB550, so the values it left are kept") == 1


def test_a_camera_whose_offsets_do_not_read_is_drawn_in_full_and_said_once(exe):
    # Camera 2 lies outside the driver's memory: the body runs as with the tweak off.
    out, err = run(exe, "2,4,-8,2", "2,4,-8,2", "0,4,-8,2", SA2_TWEAKS="reduce-shake")
    assert out == ["out | 0", "out | 0", "101 198 300.5 | 4 -8 2 | 1 -2 0.5 | 2"]
    assert err.count("sa2 tweaks: camera shake reduced: camera 2's shake offsets at 0x01DD4A44 do "
                     "not read, so its shake is drawn in full") == 1


# ------------------------------------------------------------------ the header against the image

# Each place the header names that addresses the shake offsets, and where in .text each 32-bit value
# from 0x01DD0094 to 0x01DD009F (camera 0's three offsets) sits inside it.
SITES = {
    0x00477627: (0x00477629, 0x0047762F, 0x0047763B),
    0x004DF4D2: (0x004DF4D4, 0x004DF4E2, 0x004DF4E7),
    0x004EB490: (0x004EB4A8, 0x004EB4B3, 0x004EB4BF),             # a camera's reset
    0x004EB550: (0x004EB56B, 0x004EB579, 0x004EB58B),             # the three reads
    0x004EB6E0: (0x004EB77D, 0x004EB78F, 0x004EB7A7, 0x004EB7C5,  # RunCameraShake
                 0x004EB7CB, 0x004EB7D1, 0x004EB886, 0x004EB88C, 0x004EB892),
    0x005AE2A3: (0x005AE2A5, 0x005AE2B3, 0x005AE2B8),
    0x006F006C: (0x006F006D, 0x006F0076, 0x006F007E),
    0x006F00E2: (0x006F00E3,),                                    # mov eax,0x1dd0098
    0x006F3F22: (0x006F3F24, 0x006F3F2A, 0x006F3F30),
    0x006F4C38: (0x006F4C39,),                                    # add eax,0x1dd0094
    0x006F4D87: (0x006F4D88,),                                    # add eax,0x1dd0094
}


@pytest.fixture(scope="module")
def image() -> dict:
    assert EXE.is_file(), f"{EXE} is absent: the header's addresses are checked against it"
    data = EXE.read_bytes()
    pin = json.loads(PINS.read_text())["executables"][EXE.name]["sha256"]
    assert hashlib.sha256(data).hexdigest() == pin, f"{EXE} is not the pinned image"
    nt = struct.unpack_from("<I", data, 0x3C)[0]
    count, opt_size = struct.unpack_from("<H", data, nt + 6)[0], struct.unpack_from("<H", data, nt + 20)[0]
    base = struct.unpack_from("<I", data, nt + 24 + 28)[0]
    for i in range(count):
        name, _, va, raw_size, raw = struct.unpack_from("<8sIIII", data, nt + 24 + opt_size + 40 * i)
        if name.rstrip(b"\0") == b".text":
            return {"start": base + va, "bytes": data[raw:raw + raw_size]}
    pytest.fail(f"{EXE} has no .text section")


def code(image: dict, va: int, n: int) -> bytes:
    return image["bytes"][va - image["start"]:va - image["start"] + n]


def le(v: int) -> bytes:
    return struct.pack("<I", v)


def test_the_body_reads_the_offsets_and_position_and_writes_the_applied_position(image):
    # lea ebx,[ecx+applied]; fld [ecx+offsets]; fadd [ecx+position]; fstp [ebx]: camera 0's x.
    assert code(image, 0x004EB55D, 6) == bytes.fromhex("69c9d8240000")    # imul ecx,ecx,0x24d8
    assert code(image, 0x004EB563, 6) == b"\x8d\x99" + le(CAMS + APPLIED)
    assert code(image, 0x004EB569, 6) == b"\xd9\x81" + le(CAMS + OFFSETS)
    assert code(image, 0x004EB56F, 6) == b"\xd8\x81" + le(CAMS + POSITION)
    assert (CAMS + OFFSETS, CAMS + POSITION, CAMS + APPLIED) == (0x01DD0094, 0x01DD00D4, 0x01DD00AC)
    assert "#define SHAKE_OFFSETS      0x01DD0094u" in SOURCE.read_text()


def test_the_pointer_writers_form_the_offsets_address_from_an_immediate(image):
    assert code(image, 0x006F00E2, 5) == b"\xb8" + le(0x01DD0098)          # mov eax,0x1dd0098
    assert code(image, 0x006F00E7, 3) == bytes.fromhex("d950fc")           # fst [eax-4]
    assert code(image, 0x006F00F7, 6) == bytes.fromhex("d9902cdbffff")     # fst [eax-0x24d4]
    assert code(image, 0x006F4C38, 7) == b"\x05" + le(0x01DD0094) + b"\x89\x08"   # mov [eax],ecx
    assert code(image, 0x006F4D87, 7) == b"\x05" + le(0x01DD0094) + b"\x89\x10"   # mov [eax],edx


def test_every_place_in_text_that_addresses_the_offsets_is_named_in_the_header(image):
    text = image["bytes"]
    found = [image["start"] + i for i in range(len(text) - 3)
             if 0x01DD0094 <= struct.unpack_from("<I", text, i)[0] < 0x01DD00A0]
    assert found == sorted(a for hits in SITES.values() for a in hits)
    header = SOURCE.read_text().split("*/", 1)[0]
    assert [f"0x{site:08X}" for site in SITES if f"0x{site:08X}" not in header] == []
    assert "(+0x194," in header and "(+0x16C," in header and "+0xAC" not in header
