"""The chao-stat-x5 tweak (runtime/tweaks/chao-stat-x5.c) on the Chao item-stat apply (0x00535F00)
and its two stat tables at 0x008A6240.

The reader and the tweak's file are built with the pinned zig, as tools/s4/link_s4.py compiles a
runtime source (-O2, x86_64-linux-musl), and linked with a driver that stands in for the lifted
body and the runtime's guest memory. The fake guest memory holds the 1000 bytes at 0x008A6240 from
a file the test writes; the stand-in `__real_sub_535f00` reads a row as the lifted body does (kind
2: table A, row index * 20, any other kind: table B at 0x008A6448; eight int16 at row + 4) and
prints them, so each case reads what a Chao would be given.

The tables' real bytes come from the unpacked image (private/exe/sonic2app-unpacked.exe). Without
it, the cases that need them are skipped by name; the cases with other bytes still run.

`give` models what 0x00535F00 does with one stat's value (0x00535F50-0x00535FD6), so the values the
tweak writes can be checked against the points byte they land in: the gain (kind 3: a quarter,
plus the remainder when the call's second word is set), the sign test on the whole sum, the byte
store that keeps the low 8 bits, then the one level-up a call (a byte above 99 loses 100, or is
set to 99 at level 99).
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "tweaks" / "chao-stat-x5.c"
IMAGE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
TABLE, SIZE = 0x008A6240, 1000
REPORT = "sa2 tweaks: chao stats x5 (SA2_TWEAKS=chao-stat-x5)"
APPLIED = ("sa2 tweaks: chao stats x5: the stat tables at 0x008A6240 multiplied by 5, at most 100 a "
           "stat for an animal or a drive and 388 for a fruit")

# argv: the table file ("-" for a table that does not read, "ro:<file>" for one that does not
# write), then "<kind>:<index>" calls. Each call prints "<kind> <index>: <8 values>"; the end prints
# "peeks <n> pokes <n> reals <n>" and the 500 values as the guest holds them.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "guest_memory.h"

#define TABLE 0x008A6240u
static uint8_t mem[1000];
static int readable = 1, writable = 1, peeks, pokes, reals;
static uint16_t kind;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    peeks++;
    if (!readable || a < TABLE || a + n > TABLE + sizeof mem) return 1;
    memcpy(out, mem + (a - TABLE), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    pokes++;
    if (!writable || a < TABLE || a + n > TABLE + sizeof mem) return 1;
    memcpy(mem + (a - TABLE), in, n);
    return SA2_OK;
}

static uint32_t index_dx;
void *__real_sub_535f00(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reals++;
    const uint32_t row = (kind == 2 ? 0x008A6240u : 0x008A6448u) + index_dx * 20 + 4;
    printf("%u %u:", kind, index_dx);
    for (int i = 0; i < 8; i++) {
        const uint32_t at = row + 2u * (uint32_t)i - TABLE;
        printf(" %d", (int16_t)(mem[at] | mem[at + 1] << 8));
    }
    printf("\n");
    return memory;
}
void *__wrap_sub_535f00(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    const char *file = argv[1];
    if (strcmp(file, "-") == 0) readable = 0;
    else {
        if (strncmp(file, "ro:", 3) == 0) writable = 0, file += 3;
        FILE *f = fopen(file, "rb");
        if (!f || fread(mem, 1, sizeof mem, f) != sizeof mem) return 2;
        fclose(f);
    }
    for (int i = 2; i < argc; i++) {
        kind = (uint16_t)strtoul(argv[i], NULL, 0);
        index_dx = (uint32_t)strtoul(strchr(argv[i], ':') + 1, NULL, 0);
        int token;
        if (__wrap_sub_535f00(&token, 0x00535F00u, &token) != &token) return 3;
    }
    printf("peeks %d pokes %d reals %d\n", peeks, pokes, reals);
    for (unsigned i = 0; i < 500; i++) printf("%d%c", (int16_t)(mem[2 * i] | mem[2 * i + 1] << 8), i == 499 ? '\n' : ' ');
    return 0;
}
"""


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("chao_stat_x5")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(SOURCE), "-o", str(out)], capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def image_table() -> bytes:
    """The 1000 bytes at 0x008A6240 in the unpacked image (its .rdata, file-backed)."""
    if not IMAGE.is_file():
        pytest.skip(f"the unpacked image is not present at {IMAGE}, so the tables' bytes are not available")
    import pefile
    pe = pefile.PE(str(IMAGE), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        lo = base + s.VirtualAddress
        if lo <= TABLE and TABLE + SIZE <= lo + s.SizeOfRawData:
            assert s.Name.rstrip(b"\0") == b".rdata"
            off = s.PointerToRawData + (TABLE - lo)
            return pe.__data__[off:off + SIZE]
    pytest.fail("0x008A6240 is in no section's file-backed bytes")


@pytest.fixture
def real(tmp_path) -> Path:
    p = tmp_path / "table.bin"
    p.write_bytes(image_table())
    return p


def run(exe: Path, table: str, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), table, *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def values(raw: bytes) -> list[int]:
    return list(struct.unpack("<500h", raw))


def x5(v: int) -> int:
    """Codes.lst's muls16: five times, kept to 16 bits."""
    return struct.unpack("<h", struct.pack("<H", (v * 5) & 0xFFFF))[0]


def tweaked(orig: list[int]) -> list[int]:
    """The 500 values the tweak writes: the eight stat values of each row (columns 2-9) five times,
    a positive one at most 100 in table A (rows 0-24) and 388 in table B (rows 26-49); columns 0-1
    and row 25 left as they are."""
    out = list(orig)
    for r in range(50):
        if r == 25:
            continue
        for c in range(2, 10):
            out[r * 10 + c] = min(orig[r * 10 + c] * 5, 100 if r < 25 else 388)
    return out


def give(points: int, level: int, v: int, kind: int = 2, last: bool = False) -> tuple[int, int]:
    """One stat of one call of 0x00535F00: the value v added to the points byte, then the level-up."""
    if kind == 3:
        g = int(v / 4)                       # cdq; and edx,3; add eax,edx; sar eax,2
        if last:
            g += v - 4 * int(v / 4)          # the signed remainder (and ecx,0x80000003 ...)
    else:
        g = v
    points = 0 if points + g < 0 else (points + g) & 0xFF   # movzx; add; jns / add cl,al
    if points > 99:
        if level < 99:
            points, level = points - 100, level + 1
        else:
            points = 99
    return points, level


# Row 0 of table A (an animal), row 21 (the first Chaos Drive), row 0 of table B (a fruit, kind 3).
CALLS = ("2:0", "2:21", "3:0")
OFF = ["2 0: 36 12 8 -24 0 20 20 0", "2 21: 24 0 0 0 0 40 20 0", "3 0: 0 0 0 0 40 0 0 0"]
ON = ["2 0: 100 60 40 -120 0 100 100 0", "2 21: 100 0 0 0 0 100 100 0", "3 0: 0 0 0 0 200 0 0 0"]


def test_the_recorded_sha256_is_the_images():
    m = re.search(r'CHAO_STAT_SHA256\[\] =\s*"([0-9a-f]{64})"', SOURCE.read_text())
    assert m and m.group(1) == hashlib.sha256(image_table()).hexdigest()


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_alone(exe, real, env):
    out, err = run(exe, str(real), *CALLS, **env)
    assert out[:-1] == OFF + ["peeks 0 pokes 0 reals 3"]
    assert values(real.read_bytes()) == [int(v) for v in out[-1].split()]
    assert "chao stats x5" not in err


def test_on_scales_both_tables_once_before_the_first_read(exe, real):
    orig = values(real.read_bytes())
    out, err = run(exe, str(real), *CALLS, "2:0", SA2_TWEAKS="chao-stat-x5")
    assert out[:-1] == ON + [ON[0], "peeks 1 pokes 1 reals 4"]
    assert [int(v) for v in out[-1].split()] == tweaked(orig)
    assert err.splitlines() == [REPORT, APPLIED]


def held_on(exe: Path, real: Path) -> list[int]:
    out, _ = run(exe, str(real), "2:0", SA2_TWEAKS="chao-stat-x5")
    return [int(v) for v in out[-1].split()]


def test_rows_are_capped_and_the_odd_row_is_left(exe, real):
    held = held_on(exe, real)
    assert held[0:10] == [1, 0, 100, 60, 40, -120, 0, 100, 100, 0]        # row 0: 180 capped
    assert held[110:120] == [1, 0, 100, 100, -120, 80, 0, 100, 100, 0]    # row 11: 60 x5 = 300 capped
    assert held[210:220] == [1, 0, 100, 0, 0, 0, 0, 100, 100, 0]          # row 21, a Chaos Drive
    assert held[250:260] == [14, 14, 7, 7, 0, 0, 256, 256, 6, 0]          # row 25, 0x008A6434
    assert held[260:270] == [1, 2, 0, 0, 0, 0, 200, 0, 0, 0]              # table B row 0, a fruit
    assert held[270:280] == [1, 2, 0, 0, 0, 0, 388, 0, 0, 0]              # table B row 1: 99 x5 capped


def test_three_drives_on_a_byte_at_99_lose_no_points(exe, real):
    """The finding: with Codes.lst's values, a Chaos Drive's 120 thrice on a byte at 99 wraps the
    third time and loses 136 points; with the tweak's 100 each drive is one level and 99 stays."""
    def thrice(v: int) -> list[tuple[int, int]]:
        steps, p, lv = [], 99, 0
        for _ in range(3):
            p, lv = give(p, lv, v)
            steps.append((p, lv))
        return steps
    orig = values(real.read_bytes())
    assert thrice(x5(orig[212])) == [(119, 1), (139, 2), (3, 2)]          # Codes.lst: 120
    assert thrice(held_on(exe, real)[212]) == [(99, 1), (99, 2), (99, 3)]
    assert give(56, 0, x5(orig[217])) == (0, 0)                           # Codes.lst: 56 + 200 wraps
    assert give(56, 0, held_on(exe, real)[217]) == (56, 1)


def test_no_item_lowers_a_stat_or_strands_points(exe, real):
    """Every animal and drive (kind 2, table A rows 0-24) and every fruit (kind 3, table B, with and
    without the remainder), on every stat, from every byte 0-99 and several levels, given five
    times: the byte never wraps and ends at 99 or less, and a positive value never leaves the stat
    (level * 100 + points) lower than with the tweak off."""
    orig, held = values(real.read_bytes()), held_on(exe, real)
    rows = [(r, 2, False) for r in range(25)] + [(r, 3, last) for r in range(26, 50) for last in (False, True)]
    for r, kind, last in rows:
        for c in range(2, 10):
            off_v, on_v = orig[r * 10 + c], held[r * 10 + c]
            for start in range(100):
                for level in (0, 50, 97, 99):
                    off, on = (start, level), (start, level)
                    for _ in range(5):
                        g = on_v if kind == 2 else int(on_v / 4) + (on_v - 4 * int(on_v / 4) if last else 0)
                        assert on[0] + g < 256, (r, c, start, level)
                        off, on = give(*off, off_v, kind, last), give(*on, on_v, kind, last)
                        assert on[0] <= 99, (r, c, start, level)
                        if off_v > 0:
                            assert on[1] * 100 + on[0] >= off[1] * 100 + off[0], (r, c, start, level)


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, real, replay):
    out, _ = run(exe, str(real), *CALLS, SA2_TWEAKS="chao-stat-x5", **{replay: "/dev/null"})
    assert out[:-1] == OFF + ["peeks 0 pokes 0 reals 3"]


def test_other_bytes_are_left_alone_and_named(exe, tmp_path):
    other = tmp_path / "other.bin"
    other.write_bytes(bytes(range(256)) * 3 + bytes(SIZE - 768))
    out, err = run(exe, str(other), "2:0", "2:0", SA2_TWEAKS="chao-stat-x5")
    assert out[-2] == "peeks 1 pokes 0 reals 2"
    assert [int(v) for v in out[-1].split()] == values(other.read_bytes())
    sha = hashlib.sha256(other.read_bytes()).hexdigest()
    assert err.splitlines() == [REPORT, "sa2 tweaks: chao stats x5: the table at 0x008A6240 is not "
                                f"the expected bytes (sha256 {sha}); not applied"]


@pytest.mark.parametrize("scale", [tweaked, lambda orig: [x5(v) for v in orig]], ids=["tweak", "codes-lst"])
def test_a_table_that_was_scaled_already_is_not_scaled_again(exe, tmp_path, real, scale):
    once = tmp_path / "once.bin"
    once.write_bytes(struct.pack("<500h", *scale(values(real.read_bytes()))))
    out, err = run(exe, str(once), "2:0", SA2_TWEAKS="chao-stat-x5")
    assert out[1] == "peeks 1 pokes 0 reals 1"
    assert [int(v) for v in out[-1].split()] == values(once.read_bytes())
    assert "is not the expected bytes" in err.splitlines()[1]


def test_a_table_that_does_not_read_or_write_is_named(exe, real):
    out, err = run(exe, "-", "2:0", SA2_TWEAKS="chao-stat-x5")
    assert out[1] == "peeks 1 pokes 0 reals 1"
    assert err.splitlines()[1] == "sa2 tweaks: chao stats x5: the table at 0x008A6240 does not read; not applied"
    out, err = run(exe, f"ro:{real}", "2:0", "2:0", SA2_TWEAKS="chao-stat-x5")
    assert out[:3] == [OFF[0], OFF[0], "peeks 1 pokes 1 reals 2"]
    assert err.splitlines()[1:] == ["sa2 tweaks: chao stats x5: the table at 0x008A6240 does not write; not applied"]
