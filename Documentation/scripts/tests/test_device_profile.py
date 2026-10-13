"""The device profile, read the same way twice and refused the same way.

The user ruled on 2026-09-23 that the host's device set is a recorded property
of each capture, that the lifted replay reads the same profile back, that a
run with no capture uses a plain keyboard-and-mouse default, and that a
capture whose profile the runtime cannot reproduce is refused, not
approximated. runtime/sa2_devprofile.c reads the profile when a lifted run
starts; tools/s4/device_profile.py reads it first, in the oracle runner and in
every replay tool. If the two disagreed on one file, a profile the tools
accepted would stop a lifted run, or one the runtime would take would never be
tried. So this builds the C reader with the pinned zig, as test_wsprintf.py
builds the formatter, and holds every case to both: what they read from a good
file, answer for answer, and the file, line and words of every refusal.

Both readers hold one set of limits, each a consumer module's capacity, and a
size limit derived from them; the test holds the C header's numbers to the
Python module's and to literals, and reads the largest file the limits allow.

Then the checks the tools make with the Python reader: a capture's receipt
against the file beside it, a registered replay against its capture and every
repeat, a lifted run's own record against the profile it was handed, the tick
replay's rule that the reach run it is held to ran on the same profile, and
the rule that no host-device override in a shell reaches a lifted run.

Every device here is made up. A profile names the host's hardware, and host
inventory stays in the private captures.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import device_profile as DP  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"

# Prints every accessor the runtime offers, strings as hex so no byte is lost,
# and turns a trap into one line and exit 3, which the real trap does on
# stderr (runtime/sa2_trap.c) with the note cut to 255 bytes.
DRIVER = r"""
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include "sa2_devprofile.h"
#include "sa2_trap.h"

static char note[8192];
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(note, sizeof note, fmt, ap);
    va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    printf("trap %u %u %s\n", status, exit_pc, note);
    exit(3);
}
static void hex(const void *p, unsigned n) {
    const unsigned char *b = p;
    for (unsigned i = 0; i < n; i++) printf("%02x", b[i]);
}
static void str(const char *s) {
    printf(" ");
    for (; *s; s++) printf("%02x", (unsigned char)*s);
    printf(".");
}
int main(int argc, char **argv) {
    (void)argv;
    if (argc > 1) {
        printf("limits %u %u %u %u %u %u %u %u\n", SA2_DP_MAX_BYTES, SA2_DP_MAX_GAMECTRL,
               SA2_DP_MAX_WMI_PNP, SA2_DP_MAX_DDRAW, SA2_DP_MAX_PROCESSORS,
               SA2_DP_LOCALE_TYPES, SA2_DP_LOCALE_VALUE_MAX, SA2_DP_LOCALE_ACP);
        static const uint32_t T[] = {SA2_DP_LOCALE_LCTYPES};
        for (unsigned i = 0; i < sizeof T / sizeof T[0]; i++) printf("%s%04x", i ? " " : "", T[i]);
        printf("\n");
        printf("env %u %u %u %u\n", SA2_DP_ENV_MAX_STRINGS, SA2_DP_ENV_MAX_UNITS,
               SA2_DP_ENV_VALUE_MAX, SA2_DP_STD_HANDLES);
        return 0;
    }
    sa2_devprofile_config_from_env();
    printf("default %d\nsource", sa2_devprofile_is_default());
    str(sa2_devprofile_source());
    printf("\nsha256 %s.\nsystem %u %08x %08x\nsyskeyboard %08x\nsysmouse %08x\ngamectrl %u\n",
           sa2_devprofile_sha256(), sa2_dp_processors(), sa2_dp_process_mask(),
           sa2_dp_system_mask(), sa2_dp_syskeyboard_hr(), sa2_dp_sysmouse_hr(),
           sa2_dp_gamectrl_count());
    for (uint32_t i = 0; i <= sa2_dp_gamectrl_count(); i++) {
        const sa2_dp_didevice *d = sa2_dp_gamectrl(i);
        if (!d) { printf("gc %u none %08x\n", i, sa2_dp_gamectrl_create_hr(i)); continue; }
        printf("gc %u ", i);
        hex(d->guid_instance, 16); printf(" ");
        hex(d->guid_product, 16); printf(" ");
        hex(d->guid_ffdriver, 16);
        printf(" %08x %04x %04x", d->dev_type, d->usage_page, d->usage);
        str(d->instance_name);
        str(d->product_name);
        printf(" %08x\n", sa2_dp_gamectrl_create_hr(i));
    }
    for (uint32_t s = 0; s <= 4; s++) printf("xinput %u %u\n", s, sa2_dp_xinput_caps_hr(s));
    printf("coinit %08x\nblanket %08x\npnp %u\n", sa2_dp_wmi_coinit_hr(), sa2_dp_wmi_blanket_hr(),
           sa2_dp_wmi_pnp_count());
    for (uint32_t i = 0; i <= sa2_dp_wmi_pnp_count(); i++) {
        const char *d = sa2_dp_wmi_pnp_deviceid(i);
        printf("p %u", i);
        if (d) str(d); else printf(" none");
        printf("\n");
    }
    printf("ddraw %u\n", sa2_dp_ddraw_count());
    for (uint32_t i = 0; i <= sa2_dp_ddraw_count(); i++) {
        const sa2_dp_ddraw *d = sa2_dp_ddraw_device(i);
        if (!d) { printf("dd %u none %d\n", i, sa2_dp_ddraw_monitor_adapter(i)); continue; }
        printf("dd %u %d ", i, d->has_guid);
        hex(d->guid, 16);
        printf(" %d", sa2_dp_ddraw_monitor_adapter(i));
        str(d->description);
        str(d->name);
        printf("\n");
    }
    printf("vidmem %08x %08x\n", sa2_dp_ddraw_vidmem_hr(), sa2_dp_ddraw_vidmem_total());
    printf("xaudio2 %08x %u %u\n", sa2_dp_xaudio2_master_flags(),
           sa2_dp_xaudio2_master_channels(), sa2_dp_xaudio2_master_rate());
    printf("locale %d %08x %08x %u %u\n", sa2_dp_locale_recorded(), sa2_dp_locale_lcid(),
           sa2_dp_locale_system(), sa2_dp_locale_acp(), sa2_dp_locale_oemcp());
    /* Every LCTYPE the section holds, then 0x0002, which it never does. */
    static const uint32_t T[] = {SA2_DP_LOCALE_LCTYPES, 0x0002u};
    for (unsigned i = 0; i < sizeof T / sizeof T[0]; i++) {
        if (!sa2_dp_locale_recorded() && T[i] != 0x0002u) continue;
        const uint16_t *w = NULL;
        uint32_t n = 0, e = 0;
        const int r = sa2_dp_locale_info(T[i], &w, &n, &e);
        printf("li %04x %d %08x ", T[i], r, e);
        for (uint32_t k = 0; r == 1 && k < n; k++) printf("%04x", w[k]);
        printf(".\n");
    }
    uint32_t units = 0;
    const uint16_t *block = sa2_dp_env_block(&units);
    printf("env %d %u %u ", sa2_dp_env_recorded(), sa2_dp_env_strings(), sa2_dp_env_crt());
    for (uint32_t k = 0; k < units; k++) printf("%04x", block[k]);
    printf(".\nstd %d", sa2_dp_std_recorded());
    for (unsigned k = 0; k < 4u; k++) printf(" %08x %u", sa2_dp_std_handle(k), sa2_dp_std_type(k));
    printf("\n");
    return 0;
}
"""


@pytest.fixture(scope="module")
def reader(tmp_path_factory) -> Path:
    """runtime/sa2_devprofile.c under the driver above, for this host."""
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("devprofile")
    (d / "driver.c").write_text(DRIVER)
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=str(ROOT / "private" / "generated" / "zig-cache-tests"))
    exe = d / "driver"
    done = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall",
                           "-Wextra", "-Werror", "-I", str(ROOT / "runtime"), str(d / "driver.c"),
                           str(ROOT / "runtime" / "sa2_devprofile.c"), "-o", str(exe)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return exe


def c_read(exe: Path, path: Path | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != DP.ENV}
    if path is not None:
        env[DP.ENV] = str(path)
    return subprocess.run([str(exe)], env=env, capture_output=True, text=True)


def lines_for(p: DP.Profile, default: bool, source: str, sha: str) -> str:
    """What the driver prints for a runtime that read `p`."""
    def st(b: bytes) -> str:
        return " " + b.hex() + "."
    out = [f"default {int(default)}", "source" + st(source.encode()), f"sha256 {sha}.",
           f"system {p.processors} {p.process_mask:08x} {p.system_mask:08x}",
           f"syskeyboard {p.syskeyboard:08x}", f"sysmouse {p.sysmouse:08x}",
           f"gamectrl {len(p.gamectrl)}"]
    for i, g in enumerate(p.gamectrl):
        out.append(f"gc {i} {g.instance.hex()} {g.product.hex()} {g.ffdriver.hex()} "
                   f"{g.devtype:08x} {g.usage_page:04x} {g.usage:04x}" + st(g.instance_name)
                   + st(g.product_name) + f" {g.create:08x}")
    out.append(f"gc {len(p.gamectrl)} none 00000000")
    out += [f"xinput {s} {p.xinput[s] if s < 4 else 160}" for s in range(5)]
    out += [f"coinit {p.coinit:08x}", f"blanket {p.blanket:08x}", f"pnp {len(p.pnp)}"]
    out += [f"p {i}" + st(d) for i, d in enumerate(p.pnp)] + [f"p {len(p.pnp)} none"]
    out.append(f"ddraw {len(p.ddraw)}")
    for i, d in enumerate(p.ddraw):
        out.append(f"dd {i} {int(d.guid is not None)} {(d.guid or bytes(16)).hex()} {d.monitor}"
                   + st(d.description) + st(d.name))
    out += [f"dd {len(p.ddraw)} none -1", f"vidmem {p.vidmem_hr:08x} {p.vidmem_total:08x}",
            f"xaudio2 {p.xaudio2[0]:08x} {p.xaudio2[1]} {p.xaudio2[2]}"]
    loc = p.locale
    if loc is None:
        out += ["locale 0 00000000 00000000 0 0", "li 0002 -1 00000000 ."]
    else:
        out.append(f"locale 1 {loc.lcid:08x} {loc.system:08x} {loc.acp} {loc.oemcp}")
        for t, (error, value) in zip(DP.LOCALE_LCTYPES, loc.info):
            units = "" if value is None else value.decode("utf-8").encode("utf-16-be").hex()
            out.append(f"li {t:04x} {0 if error else 1} {error:08x} {units}.")
        out.append("li 0002 -1 00000000 .")
    env = p.environment
    if env is None:
        out.append("env 0 0 0 .")
    else:
        block = env.block()[:-2]                      # the reader leaves the final NUL out
        units = "".join(f"{int.from_bytes(block[i:i + 2], 'little'):04x}"
                        for i in range(0, len(block), 2))
        out.append(f"env 1 {len(env.strings)} {env.crt} {units}.")
    std = p.std_handles or ((0, 0),) * 3
    out.append(f"std {int(p.std_handles is not None)}"
               + "".join(f" {h:08x} {t}" for h, t in std) + " 00000000 0")
    return "\n".join(out) + "\n"


def g(text: str) -> bytes:
    return uuid.UUID(text).bytes_le


# A made-up locale: every LCTYPE answered, most with its own name, one
# failing natively (LOCALE_IDEFAULTCODEPAGE, as ERROR_INVALID_FLAGS), one empty,
# LOCALE_IDEFAULTANSICODEPAGE the ANSI code page's number, and values that carry
# every escape and characters code page 1252 has above U+007F: the euro sign
# (U+20AC, byte 0x80), e acute and the florin (U+0192, byte 0x83).
GOOD_INFO = tuple(
    (0x3EC, None) if t == 0x000B
    else (0, b"") if t == 0x0050
    else (0, b"1252") if t == 0x1004
    else (0, "\u20ac \"x\" \\ caf\u00e9 \u0192".encode()) if t == 0x0014
    else (0, b"L%04X" % t)
    for t in DP.LOCALE_LCTYPES)

# A host with six processors of which the process may use four, two
# game-controller-class devices, one of whose CreateDevice failed, forty-five
# Plug and Play entities (batches of 20, 20, 5 and 0), a display adapter on
# adapter 0's monitor, and six speakers. Strings carry every escape.
GOOD = DP.Profile(
    processors=6, process_mask=0x3C, system_mask=0x3F,
    syskeyboard=0, sysmouse=0,
    gamectrl=(
        DP.GameCtrl(g("00000000-0000-0000-0000-000000000000"),
                    g("00000000-0000-0000-0000-000000000000"), bytes(16), 0x0001021C, 1, 6,
                    b'Test "Receiver" \\ \xe9t\xe9', b"Test Receiver", 0),
        DP.GameCtrl(g("00000000-0000-0000-0000-000000000000"),
                    g("00000000-0000-0000-0000-000000000000"),
                    g("00000000-0000-0000-0000-000000000000"), 0x00010214, 1, 4,
                    b"Stick", b"Stick", 0x80004005)),
    coinit=1, blanket=0,
    pnp=tuple(b"TEST\\DEVICE_%02d\\0&%d" % (i, i) for i in range(45)),
    ddraw=(DP.DDrawDevice(None, -1, b"DirectDraw HAL", b"display"),
           DP.DDrawDevice(g("00000000-0000-0000-0000-000000000000"), 0, b"Test Display",
                          b"\\\\.\\DISPLAY1")),
    vidmem_hr=0, vidmem_total=0x20000000, xaudio2=(0, 6, 48000),
    locale=DP.Locale(lcid=0x0000080C, system=0x00000409, acp=1252, oemcp=850, info=GOOD_INFO),
    # Four strings, the last added after start-up (crt 3), with every escape and
    # a character past U+007F; stdin a character device, no stdout, stderr a pipe.
    environment=DP.Environment((b"A=1", b'PATH=C:\\x;"q"', "\u00e9t\u00e9=caf\u00e9".encode(),
                                b"LAST=after start-up"), crt=3),
    std_handles=((0x8, 2), (0, 0), (0x10, 3)))
GOOD_LINES = DP.dump(GOOD).decode("ascii").splitlines()
COUNTS = {"gamectrl": 2, "xinput_connected": 0, "wmi_pnp": 45, "ddraw": 2}


def at(prefix: str, nth: int = 0) -> int:
    """The line of GOOD's nth record that starts with `prefix`, from 1."""
    return [i + 1 for i, line in enumerate(GOOD_LINES) if line.startswith(prefix)][nth]


def text(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("ascii")


def edit(prefix: str, old: str, new: str, nth: int = 0) -> tuple[bytes, int]:
    lines = list(GOOD_LINES)
    n = at(prefix, nth)
    assert old in lines[n - 1], (old, lines[n - 1])
    lines[n - 1] = lines[n - 1].replace(old, new, 1)
    return text(lines), n


# ------------------------------------------------------------ what is read

def test_the_default_is_the_keyboard_the_mouse_and_one_display(reader):
    """Unset, the runtime answers the plain default: nothing but the system
    keyboard and mouse, no pad in any slot, no WMI entity, the default display."""
    out = c_read(reader, None)
    assert out.returncode == 0, out.stdout
    assert out.stdout == (
        "default 1\nsource 64656661756c74.\nsha256 .\nsystem 1 00000001 00000001\n"
        "syskeyboard 00000000\n"
        "sysmouse 00000000\ngamectrl 0\ngc 0 none 00000000\n"
        "xinput 0 1167\nxinput 1 1167\nxinput 2 1167\nxinput 3 1167\nxinput 4 160\n"
        "coinit 00000000\nblanket 00000000\npnp 0\np 0 none\nddraw 1\n"
        "dd 0 0 00000000000000000000000000000000 -1 446972656374447261772048414c. "
        "646973706c6179.\ndd 1 none -1\nvidmem 00000000 10000000\n"
        "xaudio2 00000000 2 48000\nlocale 0 00000000 00000000 0 0\nli 0002 -1 00000000 .\n"
        "env 0 0 0 .\nstd 0 00000000 0 00000000 0 00000000 0 00000000 0\n")
    assert out.stdout == lines_for(DP.default(), True, "default", "")


def test_the_default_written_out_reads_back_as_the_default(reader, tmp_path):
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(DP.default()))
    assert DP.load(path)[0] == DP.default()
    out = c_read(reader, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert out.stdout == lines_for(DP.default(), False, str(path), sha)


def test_a_host_s_profile_reads_the_same_in_both(reader, tmp_path):
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(GOOD))
    profile, sha = DP.load(path)
    assert profile == GOOD
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()
    out = c_read(reader, path)
    assert out.returncode == 0, out.stdout
    assert out.stdout == lines_for(GOOD, False, str(path), sha)
    # Literal spot checks, so a formatter shared by both sides cannot hide a
    # value both read wrong.
    assert "\nsystem 6 0000003c 0000003f\n" in out.stdout
    assert "gc 1 11111111222233334444555555555555 " in out.stdout
    lines = out.stdout.splitlines()
    at_voice = lines.index("xaudio2 00000000 6 48000")
    assert lines[at_voice - 1:at_voice + 2] == ["vidmem 00000000 20000000",
                                                "xaudio2 00000000 6 48000",
                                                "locale 1 0000080c 00000409 1252 850"]
    # The value, unit for unit: the euro, the escapes, e acute, the florin.
    assert ("li 0014 1 00000000 20ac00200022007800220020005c0020006300610066"
            "00e900200192.") in lines
    assert "li 000b 0 000003ec ." in lines and "li 0050 1 00000000 ." in lines
    assert lines[-3] == "li 0002 -1 00000000 ."
    # The block the C runtime saw: its first three strings, each with its NUL,
    # and not the fourth, added after start-up.
    assert lines[-2] == ("env 1 4 3 0041003d00310000"
                         "0050004100540048003d0043003a005c0078003b00220071002200000"
                         "0e9007400e9003d00630061006600e90000.")
    assert lines[-1] == "std 1 00000008 2 00000000 0 00000010 3 00000000 0"

    assert "dd 1 1 7856341" in out.stdout


def test_the_writer_s_file_is_the_only_spelling_of_its_profile():
    data = DP.dump(GOOD)
    assert DP.dump(DP.parse(data)) == data
    assert b'instance_name="Test \\"Receiver\\" \\\\ \\xe9t\\xe9"' in data
    assert b"wmi_next hr=0x00000001 returned=5\n" in data
    assert data.split(b"\n")[1] == b"system processors=6 process_mask=0x0000003c system_mask=0x0000003f"
    assert b"ddraw_create guid={12345678-9ABC-DEF0-1234-56789ABCDEF0} hr=0x00000000\n" in data


def test_answers_the_runtime_passes_through_are_read_whatever_they_were(reader, tmp_path):
    """CreateDevice for the keyboard, the mouse and a controller, CoSetProxyBlanket
    and GetAvailableVidMem are answered from the profile, failures included."""
    odd = dataclasses.replace(GOOD, syskeyboard=0x80070057, sysmouse=0x8007001E,
                              blanket=0x80004001, vidmem_hr=0x88760091, vidmem_total=0)
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(odd))
    profile, sha = DP.load(path)
    assert (profile.sysmouse, profile.blanket, profile.vidmem_hr) == (0x8007001E, 0x80004001,
                                                                      0x88760091)
    assert c_read(reader, path).stdout == lines_for(odd, False, str(path), sha)


def test_the_apartment_coinitialize_met_at_init_is_kept_and_never_refused(reader, tmp_path):
    """RPC_E_CHANGED_MODE is what the probe's CoInitialize meets once the game's
    XAudio2 set-up has entered the multithreaded apartment; it is information,
    read back as recorded, and no reader refuses it (sa2_devprofile.h)."""
    mta = dataclasses.replace(GOOD, coinit=0x80010106)
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(mta))
    profile, sha = DP.load(path)
    assert profile.coinit == 0x80010106
    out = c_read(reader, path)
    assert out.returncode == 0, out.stdout
    assert "\ncoinit 80010106\n" in out.stdout
    assert out.stdout == lines_for(mta, False, str(path), sha)


def test_a_single_processor_host_and_a_full_mask_are_read(reader, tmp_path):
    """The two ends: one processor, and thirty-two, whose mask is every bit."""
    for cpus, mask in ((1, 0x1), (32, 0xFFFFFFFF)):
        p = dataclasses.replace(GOOD, processors=cpus, process_mask=mask, system_mask=mask)
        path = tmp_path / f"{cpus}.txt"
        path.write_bytes(DP.dump(p))
        profile, sha = DP.load(path)
        assert (profile.processors, profile.process_mask) == (cpus, mask)
        assert c_read(reader, path).stdout == lines_for(p, False, str(path), sha)


def test_the_longest_strings_the_runtime_holds_are_read_whole(reader, tmp_path):
    long = dataclasses.replace(
        GOOD, gamectrl=(dataclasses.replace(GOOD.gamectrl[0], product_name=b"p" * 259),),
        pnp=(b"d" * 511,))
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(long))
    profile, sha = DP.load(path)
    assert profile.pnp == (b"d" * 511,)
    assert c_read(reader, path).stdout == lines_for(long, False, str(path), sha)


@pytest.mark.parametrize("pad", range(0, 70, 7))
def test_the_sha256_the_runtime_reports_is_the_file_s(reader, tmp_path, pad):
    """Every length of the last block, since the padding is where a hand-written
    SHA-256 goes wrong."""
    p = dataclasses.replace(GOOD, pnp=GOOD.pnp[:3] + (b"x" * pad,))
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(p))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert f"sha256 {sha}.\n" in c_read(reader, path).stdout


def test_the_limits_are_one_set_in_both_readers(reader):
    """Each a consumer's capacity (runtime/sa2_dinput.h 16, sa2_wbem.h 253,
    sa2_ddraw.h 6) and 32 processors, and the size limit derived from them."""
    out = subprocess.run([str(reader), "limits"], capture_output=True, text=True)
    first, types, env = out.stdout.splitlines()
    assert first == "limits 828477 16 253 6 32 74 127 1252"
    assert (DP.MAX_BYTES, DP.MAX_GAMECTRL, DP.MAX_WMI_PNP, DP.MAX_DDRAW,
            DP.MAX_PROCESSORS) == (828477, 16, 253, 6, 32)
    assert env == "env 256 16384 4095 3"
    assert (DP.ENV_MAX_STRINGS, DP.ENV_MAX_UNITS, DP.ENV_VALUE_MAX, DP.STD_HANDLES) == \
        (256, 16384, 4095, 3)
    assert (len(DP.LOCALE_LCTYPES), DP.LOCALE_VALUE_MAX, DP.LOCALE_ACP) == (74, 127, 1252)
    assert types == " ".join(f"{t:04x}" for t in DP.LOCALE_LCTYPES)
    assert list(DP.LOCALE_LCTYPES) == sorted(set(DP.LOCALE_LCTYPES))


def test_the_locale_s_code_page_table_is_the_runtime_s_in_both_readers():
    """GetLocaleInfoA answers a recorded value through code page 1252
    (runtime/sa2_codepage.c), so both readers refuse a value with a character
    that table has no byte for; each holds its own copy of the table's upper
    half, and all three must be one table."""
    import re

    def table(path: str, name: str) -> tuple[int, ...]:
        text = (ROOT / path).read_text()
        body = re.search(name + r"\[32\] = \{(.*?)\};", text, re.S).group(1)
        return tuple(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{4})", body))

    runtime = table("runtime/sa2_codepage.c", "k_cp1252_high")
    assert len(runtime) == 32 and runtime[0] == 0x20AC and runtime[31] == 0x0178
    assert table("runtime/sa2_devprofile.c", "high") == runtime == DP.CP1252_HIGH


def test_the_probe_asks_every_lctype_the_readers_hold_it_to():
    """harness/replay/native_device_profile.c asks each LCTYPE in its own list and
    the readers require SA2_DP_LOCALE_LCTYPES in order, so the two lists are one."""
    import re
    text = (ROOT / "harness" / "replay" / "native_device_profile.c").read_text()
    body = re.search(r"static const uint32_t LOCALE_LCTYPES\[\] = \{(.*?)\};", text, re.S).group(1)
    assert tuple(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)u", body)) == DP.LOCALE_LCTYPES


def maximal() -> DP.Profile:
    """The largest profile the limits allow: every count at its limit, every
    string at its longest and spelled at four characters a byte, every dec at
    its most digits. \\x01 is valid UTF-8, so the DeviceIDs are too."""
    name = b"\x01" * (DP.NAME_MAX - 1)
    ctrl = tuple(DP.GameCtrl(g(f"{i + 1:08x}-0000-0000-0000-000000000000"), g("1" * 32),
                             g("2" * 32), 0xFFFFFFFF, 0xFFFF, 0xFFFF, name, name, 0xFFFFFFFF)
                 for i in range(DP.MAX_GAMECTRL))
    dd = tuple(DP.DDrawDevice(g(f"{i + 1:08x}-1111-1111-1111-111111111111"), 0, name, name)
               for i in range(DP.MAX_DDRAW))
    info = tuple((0, b"1252") if t == 0x1004 else (0, b"\x01" * DP.LOCALE_VALUE_MAX)
                 for t in DP.LOCALE_LCTYPES)
    # Every string at three UTF-8 bytes a unit, as many units as the block holds
    # beside the strings' NULs and its own: 16127, 63 in each string but the last.
    room = DP.ENV_MAX_UNITS - DP.ENV_MAX_STRINGS - 1
    wide = [63] * (DP.ENV_MAX_STRINGS - 1) + [room - 63 * (DP.ENV_MAX_STRINGS - 1)]
    env = DP.Environment(tuple(("\u0800" * n).encode() for n in wide), DP.ENV_MAX_STRINGS)
    return DP.Profile(processors=32, process_mask=0xFFFFFFFF, system_mask=0xFFFFFFFF,
                      syskeyboard=0xFFFFFFFF, sysmouse=0xFFFFFFFF, gamectrl=ctrl,
                      coinit=0xFFFFFFFF, blanket=0xFFFFFFFF,
                      pnp=(b"\x01" * (DP.DEVICEID_MAX - 1),) * DP.MAX_WMI_PNP, ddraw=dd,
                      vidmem_hr=0xFFFFFFFF, vidmem_total=0xFFFFFFFF,
                      xaudio2=(0xFFFFFFFF, 4294967295, 4294967295),
                      locale=DP.Locale(0xFFFFFFFF, 0xFFFFFFFF, 1252, 4294967295, info),
                      environment=env,
                      std_handles=((0xFFFFFFF0, 3), (0xFFFFFFF4, 3), (0xFFFFFFF8, 3)))


def test_the_largest_file_the_limits_allow_is_read_by_both(reader, tmp_path):
    """The size limit is derived from each record kind's longest line, so the
    largest file the grammar accepts fits it: short of it only by the index
    digits below their most -- controllers 0-9 one digit each (10 bytes), the
    run-ending empty batch's `returned=0` (1), entities 0-9 two and 10-99 one
    (110) -- 121 bytes in all; and the locale's ANSI code page, which a reader
    accepts only as 1252, four digits of the ten (6), and the one value that
    must spell it, LOCALE_IDEFAULTANSICODEPAGE's "1252" where the others are 127
    bytes at four characters each (504): 631 in all. The environment: the
    block's units less the strings' NULs and its own, 257 units at twelve
    characters each (3084), and the strings' index digits, 0-9 two short and
    10-99 one (110): 3194 more."""
    big = maximal()
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(big))
    assert path.stat().st_size == DP.MAX_BYTES - 631 - 3194
    profile, sha = DP.load(path)
    assert profile == big
    out = c_read(reader, path)
    assert out.returncode == 0, out.stdout[-300:]
    assert out.stdout == lines_for(big, False, str(path), sha)


def test_the_size_limit_is_inclusive_and_refused_at_line_0_past_it(reader, tmp_path):
    """MAX_BYTES bytes are read and then refused for what they hold, at line 1;
    one more byte is refused unread, at line 0, by both readers."""
    at_limit, past = tmp_path / "at.txt", tmp_path / "past.txt"
    at_limit.write_bytes(b"x" * DP.MAX_BYTES)
    past.write_bytes(b"x" * (DP.MAX_BYTES + 1))
    for path, where in ((at_limit, "1: the last line does not end in LF"),
                        (past, "0: the device profile is larger than 828477 bytes")):
        with pytest.raises(DP.ProfileError) as e:
            DP.load(path)
        assert str(e.value) == f"{path}:{where}"
        assert c_read(reader, path).stdout == f"trap 2 0 {path}:{where}\n"


@pytest.mark.skipif(not Path("/dev/zero").exists(), reason="no /dev/zero on this host")
def test_a_file_that_never_ends_is_refused_unread(reader):
    """/dev/zero has no end, so a reader that held the whole file before
    checking its size would never return; both read one byte past the limit and
    stop there (the 2026-09-23 review found the Python side reading it all)."""
    words = "/dev/zero:0: the device profile is larger than 828477 bytes"
    with pytest.raises(DP.ProfileError) as e:
        DP.load(Path("/dev/zero"))
    assert str(e.value) == words
    assert c_read(reader, Path("/dev/zero")).stdout == f"trap 2 0 {words}\n"


def test_the_counts_a_receipt_records(tmp_path):
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(GOOD))
    assert DP.summary(path) == {"file": "sa2-device-profile.txt",
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "counts": COUNTS}
    assert DP.counts(DP.default()) == {"gamectrl": 0, "xinput_connected": 0, "wmi_pnp": 0,
                                       "ddraw": 1}


# --------------------------------------------------------------- refusals

def _pnp(i: int) -> str:
    return f'wmi_pnp index={i} get=0x00000000 vt=0x00000008 deviceid="E{i}"'


def _short_batch_then_more() -> tuple[bytes, int]:
    head = GOOD_LINES[:at("wmi_next") - 1]
    lines = head + ["wmi_next hr=0x00000001 returned=2", _pnp(0), _pnp(1),
                    "wmi_next hr=0x00000001 returned=1", _pnp(2), "end"]
    return text(lines), len(head) + 4


def _many(**kw) -> bytes:
    return DP.dump(dataclasses.replace(GOOD, **kw))


def _line_of(data: bytes, prefix: str) -> int:
    return [i + 1 for i, line in enumerate(data.decode().splitlines())
            if line.startswith(prefix)][0]


MALFORMED, UNREPRODUCIBLE, UNMEASURED = DP.MALFORMED, DP.UNREPRODUCIBLE, DP.UNMEASURED

# (id, file bytes, line, the runtime's words, kind)
CASES: list[tuple[str, bytes, int, str, str]] = []


def case(name: str, made: tuple[bytes, int] | bytes, message: str, kind: str = MALFORMED,
         line: int | None = None) -> None:
    data, n = made if isinstance(made, tuple) else (made, line)
    CASES.append((name, data, n, message, kind))


_all = text(GOOD_LINES)
case("empty", b"", "the file ends before `end`", line=1)
case("another version", edit("sa2-device-profile", "1", "2"),
     "the first line is not `sa2-device-profile 1`")
case("crlf", _all.replace(b"\n", b"\r\n"), "a byte outside 0x20-0x7e (0x0d)", line=1)
case("a tab", edit("dinput8_enum", " count", "\tcount"), "a byte outside 0x20-0x7e (0x09)")
case("a raw high byte", _all.replace(b"Stick", b"St\xe9ck", 1),
     "a byte outside 0x20-0x7e (0xe9)", line=at("dinput8_gamectrl", 1))
case("no final LF", _all[:-1], "the last line does not end in LF", line=len(GOOD_LINES))
case("an empty line", text(GOOD_LINES[:3] + [""] + GOOD_LINES[3:]), "an empty line", line=4)
case("torn", text(GOOD_LINES[:40]), "the file ends before `end`", line=41)
case("after end", _all + b"more\n", "text after `end`", line=len(GOOD_LINES) + 1)
case("unknown kind", edit("dinput8_syskeyboard", "dinput8_syskeyboard", "dinput8_keyboard"),
     "expected `dinput8_syskeyboard`, found `dinput8_keyboard`")
case("the wrong record", edit("dinput8_enum", "dinput8_enum", "ddraw_enum"),
     "expected `dinput8_gamectrl` or `dinput8_enum`, found `ddraw_enum`")
case("a field missing", edit("dinput8_enum", " count=2", ""),
     "record `dinput8_enum`: expected ` count=` at column 27")
case("fields out of order", edit("dinput8_enum", "hr=0x00000000 count=2", "count=2 hr=0x00000000"),
     "record `dinput8_enum`: expected ` hr=` at column 13")
case("two spaces", edit("dinput8_enum", " hr=", "  hr="),
     "record `dinput8_enum`: expected ` hr=` at column 13")
case("a field added", edit("dinput8_enum", "count=2", "count=2 more=1"),
     "record `dinput8_enum`: text after its last field")
case("upper-case hex", edit("dinput8_create", "0x00000000", "0x0000000A"),
     "field `hr`: not 0x and eight lower-case hex digits")
case("short hex", edit("dinput8_create", "0x00000000", "0x0"),
     "field `hr`: not 0x and eight lower-case hex digits")
case("a leading zero", edit("dinput8_enum", "count=2", "count=02"),
     "field `count`: not a decimal number")
case("past 32 bits", edit("dinput8_enum", "count=2", "count=4294967296"),
     "field `count`: not a decimal number")
case("a lower-case GUID", edit("dinput8_gamectrl", "{0A1B2C3D", "{0a1b2c3d"),
     "field `instance`: not a GUID in registry form")
case("a GUID without braces", edit("ddraw_create", "{12345678-9ABC-DEF0-1234-56789ABCDEF0}",
                                   "12345678-9ABC-DEF0-1234-56789ABCDEF0"),
     "field `guid`: not a GUID in registry form")
case("unquoted", edit("dinput8_gamectrl", 'product_name="Stick"', "product_name=Stick", 1),
     "field `product_name`: not a quoted string")
case("unterminated", text(GOOD_LINES[:at("dinput8_gamectrl") - 1]
                          + ['dinput8_gamectrl index=0 instance={0A1B2C3D-4E5F-6071-8293-'
                             'A4B5C6D7E8F9} product={5678ABCD-0000-0000-0000-504944564944} '
                             'ffdriver={00000000-0000-0000-0000-000000000000} '
                             'devtype=0x0001021c usage_page=0x00000001 usage=0x00000006 '
                             'instance_name="abc']),
     "field `instance_name`: the string does not end", line=at("dinput8_gamectrl"))
case("a newline escape", edit("dinput8_gamectrl", "Stick", "St\\nck", 1),
     "field `instance_name`: an escape other than \\\", \\\\ or \\xhh")
case("upper-case \\x", edit("dinput8_gamectrl", "\\xe9t", "\\xE9t"),
     "field `instance_name`: \\x is not followed by two lower-case hex digits")
case("\\x00", edit("dinput8_gamectrl", "\\xe9t", "\\x00t"),
     "field `instance_name`: \\x00 in a string")
case("a printable byte escaped", edit("dinput8_gamectrl", "Stick", "\\x53tick", 1),
     "field `instance_name`: \\x53 is not that byte's spelling")
case("a quote escaped the long way", edit("dinput8_gamectrl", '\\"Receiver\\"',
                                          '\\x22Receiver\\"'),
     "field `instance_name`: \\x22 is not that byte's spelling")
case("text after a string", edit("dinput8_gamectrl", '"Stick" product', '"Stick"x product', 1),
     "field `instance_name`: text after the closing quote")
case("no system record", text(GOOD_LINES[:1] + GOOD_LINES[2:]),
     "expected `system`, found `dinput8_create`", line=2)
case("no processor", edit("system", "processors=6", "processors=0"),
     "field `processors`: 0, and a process runs on at least one processor")
case("thirty-three processors", edit("system", "processors=6", "processors=33"),
     "GetSystemInfo reported 33 processors natively, and the runtime's affinity masks, a "
     "32-bit process's, hold 32", UNREPRODUCIBLE)
case("an empty process mask", edit("system", "process_mask=0x0000003c",
                                   "process_mask=0x00000000"),
     "field `process_mask`: 0x00000000 names no processor")
case("an empty system mask", edit("system", "system_mask=0x0000003f", "system_mask=0x00000000"),
     "field `system_mask`: 0x00000000 names no processor")
case("a process mask past the processors", edit("system", "process_mask=0x0000003c",
                                                "process_mask=0x00000040"),
     "field `process_mask`: 0x00000040 names a processor past the 6 GetSystemInfo reported")
case("a system mask past the processors", edit("system", "system_mask=0x0000003f",
                                               "system_mask=0x0000007f"),
     "field `system_mask`: 0x0000007f names a processor past the 6 GetSystemInfo reported")
case("a process mask outside the system mask", edit("system", "system_mask=0x0000003f",
                                                    "system_mask=0x0000001f"),
     "field `process_mask`: 0x0000003c names a processor the system mask 0x0000001f does "
     "not, which Windows never answers")
case("a name past MAX_PATH", _many(gamectrl=(dataclasses.replace(
    GOOD.gamectrl[0], instance_name=b"n" * 260),)),
     "field `instance_name`: 260 bytes, more than the 259 the runtime holds",
     line=at("dinput8_gamectrl"))
case("a DeviceID past the runtime's", _many(pnp=(b"d" * 512,)),
     "field `deviceid`: 512 bytes, more than the 511 the runtime holds",
     line=_line_of(_many(pnp=(b"d" * 512,)), "wmi_pnp"))
case("an index skipped", edit("dinput8_gamectrl", "index=1", "index=2", 1),
     "index 2 where 1 was next")
case("a count that is not the records'", edit("dinput8_enum", "count=2", "count=3"),
     "count 3 where 2 records came before it")
case("a usage page past 16 bits", edit("dinput8_gamectrl", "usage_page=0x00000001",
                                       "usage_page=0x00010001"),
     "field `usage_page`: 0x00010001 does not fit in 16 bits")
case("seventeen controllers", _many(gamectrl=tuple(
    dataclasses.replace(GOOD.gamectrl[0], instance=g(f"{i + 1:08x}-0000-0000-0000-000000000000"))
    for i in range(17))),
     "more than 16 `dinput8_gamectrl` records, the most the runtime holds", UNREPRODUCIBLE,
     line=at("dinput8_gamectrl") + 16)
case("a controller with the keyboard's GUID",
     edit("dinput8_gamectrl", "{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9}",
          "{6F1D2B61-D5A0-11CF-BFC7-444553540000}"),
     "controller 0 has GUID_SysKeyboard as its instance GUID, and the runtime's CreateDevice "
     "tells devices apart by that GUID", UNREPRODUCIBLE)
case("a controller with the mouse's GUID",
     edit("dinput8_gamectrl", "{00000000-0000-0000-0000-000000000000}",
          "{6F1D2B60-D5A0-11CF-BFC7-444553540000}", 1),
     "controller 1 has GUID_SysMouse as its instance GUID, and the runtime's CreateDevice "
     "tells devices apart by that GUID", UNREPRODUCIBLE)
case("two controllers with one GUID",
     edit("dinput8_gamectrl", "{00000000-0000-0000-0000-000000000000}",
          "{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9}", 1),
     "controller 1 has controller 0's instance GUID, and the runtime's CreateDevice tells "
     "devices apart by that GUID", UNREPRODUCIBLE)
case("a slot out of order", edit("xinput_caps", "slot=1", "slot=2", 1), "slot 2 where 1 was next")
case("a batch larger than asked", edit("wmi_next", "returned=20", "returned=21"),
     "Next returned 21 objects of the 20 asked for")
case("an entity out of order", edit("wmi_pnp", "index=4", "index=5", 4), "index 5 where 4 was next")
case("a vt past 16 bits", edit("wmi_pnp", "vt=0x00000008", "vt=0x00010008"),
     "field `vt`: 0x00010008 does not fit in 16 bits")
case("entities miscounted", edit("wmi_entities", "count=45", "count=44"),
     "count 44 where 45 records came before it")
case("254 entities", _many(pnp=tuple(b"E%d" % i for i in range(254))),
     "more than 253 `wmi_pnp` records, the most the runtime holds", UNREPRODUCIBLE,
     line=_line_of(_many(pnp=tuple(b"E%d" % i for i in range(254))), "wmi_pnp index=253"))
case("a DeviceID that is not UTF-8",
     edit("wmi_pnp", 'deviceid="TEST\\\\DEVICE_07\\\\0&7"', 'deviceid="TEST\\xc3("', 7),
     "entity 7's DeviceID is not valid UTF-8")
case("a monitor that is not a word", edit("ddraw_device", "monitor=adapter0", "monitor=adapter", 1),
     "field `monitor`: not null, other or adapter<n>")
case("a monitor past the adapters", edit("ddraw_device", "monitor=adapter0", "monitor=adapter1", 1),
     "monitor adapter1, and Direct3D reported 1 adapters")
case("seven displays", _many(ddraw=GOOD.ddraw[:1] * 7),
     "more than 6 `ddraw_device` records, the most the runtime holds", UNREPRODUCIBLE,
     line=_line_of(_many(ddraw=GOOD.ddraw[:1] * 7), "ddraw_device index=6"))
case("displays miscounted", edit("ddraw_enum", "count=2", "count=1"),
     "count 1 where 2 records came before it")
case("another GUID created", edit("ddraw_create", "{12345678", "{12345679"),
     "DirectDrawCreate's GUID is not the one the game's callback 0x00865c20 keeps from the "
     "devices above")
case("a probe error", edit("dinput8_create", "dinput8_create hr=0x00000000",
                           'error step="LoadLibraryA(dinput8.dll)" code=0x0000007e'),
     'the probe could not measure "LoadLibraryA(dinput8.dll)" (code 0x0000007e)', UNMEASURED)
# What the runtime cannot reproduce.
case("DirectInput8Create failed", edit("dinput8_create", "0x00000000", "0x80004005"),
     "DirectInput8Create answered 0x80004005 natively, and the runtime's answers S_OK",
     UNREPRODUCIBLE)
case("EnumDevices failed", edit("dinput8_enum", "hr=0x00000000", "hr=0x80004005"),
     "EnumDevices answered 0x80004005 natively, and the runtime's answers DI_OK", UNREPRODUCIBLE)
case("a pad", edit("xinput_caps", "code=0x0000048f", "code=0x00000000"),
     "XInput slot 0 answered 0 natively, and the runtime answers only 1167 (not connected): a "
     "pad's input is part of no recording", UNREPRODUCIBLE)
case("XInput failing otherwise", edit("xinput_caps", "code=0x0000048f", "code=0x0000001f", 3),
     "XInput slot 3 answered 31 natively, and the runtime answers only 1167 (not connected): a "
     "pad's input is part of no recording", UNREPRODUCIBLE)
case("no WMI", edit("wmi_locator", "0x00000000", "0x80040154"),
     "CoCreateInstance(CLSID_WbemLocator) answered 0x80040154 natively, and the runtime's "
     "answers S_OK", UNREPRODUCIBLE)
case("ConnectServer failed", edit("wmi_connect", "0x00000000", "0x8004100e"),
     "ConnectServer answered 0x8004100e natively, and the runtime's answers S_OK", UNREPRODUCIBLE)
case("CreateInstanceEnum failed", edit("wmi_enum", "0x00000000", "0x80041010"),
     "CreateInstanceEnum answered 0x80041010 natively, and the runtime's answers S_OK",
     UNREPRODUCIBLE)
case("a full batch called short", edit("wmi_next", "hr=0x00000000", "hr=0x00000001"),
     "Next answered 0x00000001 with 20 objects, and the runtime's answers 0x00000000",
     UNREPRODUCIBLE)
case("a batch that failed", edit("wmi_next", "hr=0x00000000", "hr=0x80041032", 1),
     "Next answered 0x80041032 with 20 objects, and the runtime's answers 0x00000000",
     UNREPRODUCIBLE)
case("more after a short batch", _short_batch_then_more(),
     "Next returned 1 objects after a short batch, and the runtime's returns none",
     UNREPRODUCIBLE)
case("Get failed", edit("wmi_pnp", "get=0x00000000", "get=0x80041002"),
     "Get(DeviceID) on entity 0 answered 0x80041002 natively, and the runtime's answers S_OK",
     UNREPRODUCIBLE)
case("a DeviceID that is not a BSTR",
     edit("wmi_pnp", 'vt=0x00000008 deviceid="TEST\\\\DEVICE_07\\\\0&7"',
          "vt=0x00000001 deviceid=null", 7),
     "entity 7's DeviceID has vt 0x0001 natively, and the runtime's is a BSTR (8)",
     UNREPRODUCIBLE)
case("a null BSTR", edit("wmi_pnp", 'deviceid="TEST\\\\DEVICE_07\\\\0&7"', "deviceid=null", 7),
     "entity 7's DeviceID is a null BSTR natively, and the runtime's is a string", UNREPRODUCIBLE)
case("two adapters", edit("d3d9_adapters", "count=1", "count=2"),
     "Direct3D reported 2 adapters natively, and the runtime's IDirect3D9 reports 1",
     UNREPRODUCIBLE)
case("a monitor no adapter has", edit("ddraw_device", "monitor=adapter0", "monitor=other", 1),
     "ddraw device 1's monitor is no Direct3D adapter's natively, and the runtime's monitors are "
     "its adapters'", UNREPRODUCIBLE)
case("no GUID on adapter 0", edit("ddraw_device", "guid={12345678-9ABC-DEF0-1234-56789ABCDEF0}",
                                  "guid=null", 1),
     "ddraw device 1 is on adapter 0's monitor with no GUID, which the game's callback "
     "0x00865c20 would read through", UNREPRODUCIBLE)
case("the enumeration failed", edit("ddraw_enum", "hr=0x00000000", "hr=0x80004001"),
     "DirectDrawEnumerateExA answered 0x80004001 natively, and the runtime's answers DD_OK",
     UNREPRODUCIBLE)
case("DirectDrawCreate failed", edit("ddraw_create", "hr=0x00000000", "hr=0x88760091"),
     "DirectDrawCreate answered 0x88760091 natively, and the runtime's answers DD_OK",
     UNREPRODUCIBLE)
case("no IDirectDraw7", edit("ddraw_query", "0x00000000", "0x80004002"),
     "QueryInterface(IID_IDirectDraw7) answered 0x80004002 natively, and the runtime's answers "
     "S_OK", UNREPRODUCIBLE)
case("a voice that is not a flag", edit("xaudio2_master", "voice=1", "voice=2"),
     "field `voice`: 2 is neither 0 nor 1")
case("no mastering voice", edit("xaudio2_master", "voice=1", "voice=0"),
     "the game had no XAudio2 mastering voice natively (0x01af0edc was null), and the "
     "runtime's engine makes one", UNREPRODUCIBLE)
# The locale section (runtime/sa2_devprofile.h, "The locale").
_user = at("locale_user")
case("a locale without its user record", text(GOOD_LINES[:_user - 1] + GOOD_LINES[_user:]),
     "expected `locale_user`, `environment`, `std_handle` or `end`, found `locale_info`",
     line=_user)
case("a locale that is not a locale", edit("locale_user", "lcid=0x0000080c", "lcid=0x00000000"),
     "field `lcid`: 0 names no locale")
case("a validity that is not a flag", edit("locale_user", "valid=1", "valid=2"),
     "field `valid`: 2 is neither 0 nor 1")
case("an LCTYPE out of order", edit("locale_info", "lctype=0x00000003", "lctype=0x00000002", 1),
     "LCTYPE 0x00000002 where 0x00000003 was next")
case("an LCTYPE missing", text(GOOD_LINES[:_user + 1] + GOOD_LINES[_user + 2:]),
     "LCTYPE 0x00000004 where 0x00000003 was next", line=_user + 2)
case("the section cut short", text(GOOD_LINES[:_user + 3] + ["end"]),
     "expected `locale_info`, found `end`", line=_user + 4)
case("a value beside an error", edit("locale_info", "error=0x000003ec value=null",
                                     'error=0x000003ec value="x"', 8),
     "LCTYPE 0x0000000b: a value is null exactly when its error is not 0")
case("an error with no code", edit("locale_info", 'value="L0001"', "value=null"),
     "LCTYPE 0x00000001: a value is null exactly when its error is not 0")
case("a value that is not UTF-8", edit("locale_info", 'value="L0001"', 'value="L\\xff"'),
     "LCTYPE 0x00000001: not valid UTF-8")
case("a value past the runtime's", edit("locale_info", 'value="L0001"',
                                        'value="' + "v" * 128 + '"'),
     "field `value`: 128 bytes, more than the 127 the runtime holds")
case("a locale the probe could not measure",
     edit("locale_info", 'locale_info lctype=0x00000001 error=0x00000000 value="L0001"',
          'error step="a locale answer that does not convert to UTF-8" code=0x00000001'),
     'the probe could not measure "a locale answer that does not convert to UTF-8" '
     "(code 0x00000001)", UNMEASURED)
case("an invalid user locale", edit("locale_user", "valid=1", "valid=0"),
     "IsValidLocale answered 0 for the user's locale 0x0000080c natively, and the runtime's "
     "answers 1", UNREPRODUCIBLE)
case("another ANSI code page", edit("locale_user", "acp=1252", "acp=932"),
     "GetACP answered 932 natively, and the runtime's code-page tables are 1252's",
     UNREPRODUCIBLE)
case("a character past U+FFFF", edit("locale_info", 'value="L0001"',
                                     'value="L\\xf0\\x9f\\x98\\x80"'),
     "LCTYPE 0x00000001: a character past U+FFFF natively, and the runtime's answers hold none",
     UNREPRODUCIBLE)
case("a character code page 1252 lacks", edit("locale_info", 'value="L0001"',
                                              'value="L\\xe4\\xb8\\x80"'),
     "LCTYPE 0x00000001: U+4E00 has no byte of its own in code page 1252, and a reader "
     "takes no value GetLocaleInfoA would answer through the best fit", UNREPRODUCIBLE)
case("another ANSI code page named", edit("locale_info", 'value="1252"', 'value="1250"',
                                          DP.LOCALE_LCTYPES.index(0x1004)),
     'LOCALE_IDEFAULTANSICODEPAGE is "1250" and GetACP answered 1252, and the runtime\'s C '
     "runtime would install a code page its conversions refuse", UNREPRODUCIBLE)


# The environment and standard-handle sections (runtime/sa2_devprofile.h, "The
# environment" and "The standard handles").
_env = at("environment")
_std = at("std_handle")
case("no strings", edit("environment", "strings=4 crt=3", "strings=0 crt=0"),
     "field `strings`: 0, and a process's block holds at least one")
case("more strings than the runtime holds", edit("environment", "strings=4", "strings=257"),
     "257 strings in the environment block, more than the 256 the runtime holds",
     UNREPRODUCIBLE)
case("no string the C runtime kept", edit("environment", "crt=3", "crt=0"),
     "field `crt`: 0, and the C runtime keeps every string of a block it is handed")
case("more kept than the block holds", edit("environment", "crt=3", "crt=5"),
     "field `crt`: 5 strings the C runtime kept, more than the block's 4")
case("a string out of order", edit("env ", "index=1", "index=2", 1),
     "index 2 where 1 was next")
case("an empty string", edit("env ", 'value="PATH=C:\\\\x;\\"q\\""', 'value=""', 1),
     "string 1: empty, and an empty string ends the block")
case("a string that is not UTF-8", edit("env ", 'value="A=1"', 'value="A=\\xff"'),
     "string 0: not valid UTF-8")
case("a string past U+FFFF", edit("env ", 'value="A=1"', 'value="A=\\xf0\\x9f\\x98\\x80"'),
     "string 0: a character past U+FFFF natively, and the runtime's block holds none",
     UNREPRODUCIBLE)
case("a string longer than the runtime holds",
     edit("env ", 'value="A=1"', 'value="A=' + "x" * 4094 + '"'),
     "field `value`: 4096 bytes, more than the 4095 the runtime holds")
_huge = text(GOOD_LINES[:_env - 1] + ["environment strings=5 crt=5"]
             + [f'env index={i} value="{chr(65 + i)}=' + "x" * 3998 + '"' for i in range(5)]
             + GOOD_LINES[_std - 1:])
case("a block longer than the runtime holds", _huge,
     "string 4: the block passes the 16384 units the runtime holds", UNREPRODUCIBLE,
     line=_env + 5)
case("the environment cut short", text(GOOD_LINES[:_env + 1] + GOOD_LINES[_std - 1:]),
     "expected `env`, found `std_handle`", line=_env + 2)
case("the sections out of order",
     text(GOOD_LINES[:_user - 1] + GOOD_LINES[_env - 1:_std - 1] + GOOD_LINES[_user - 1:_env - 1]
          + GOOD_LINES[_std - 1:]),
     "expected `std_handle` or `end`, found `locale_user`", line=_user + _std - _env)
case("a standard handle out of order", edit("std_handle", "which=1", "which=2", 1),
     "standard handle 2 where 1 was next")
case("a type for no handle", edit("std_handle", "handle=0x00000000 type=0",
                                  "handle=0x00000000 type=2", 1),
     "stdout: type 2 for no handle, which GetFileType is not asked for")
case("a standard handle that is a file", edit("std_handle", "type=2", "type=1"),
     "stdin was a disk file (FILE_TYPE_DISK) natively, and the runtime's standard handles "
     "hold no file", UNREPRODUCIBLE)
case("a type that is none", edit("std_handle", "type=3", "type=4", 2),
     "stderr: type 4 is not FILE_TYPE_UNKNOWN, FILE_TYPE_CHAR or FILE_TYPE_PIPE")
case("the standard handles cut short", text(GOOD_LINES[:_std + 1] + ["end"]),
     "expected `std_handle`, found `end`", line=_std + 2)


@pytest.mark.parametrize("name,data,line,message,kind", CASES, ids=[c[0] for c in CASES])
def test_every_refusal_is_the_runtime_s_word_for_word(reader, tmp_path, name, data, line,
                                                      message, kind):
    path = tmp_path / DP.FILE
    path.write_bytes(data)
    with pytest.raises(DP.ProfileError) as e:
        DP.load(path)
    assert (e.value.line, e.value.message, e.value.kind) == (line, message, kind)
    assert str(e.value) == f"{path}:{line}: {message}"
    out = c_read(reader, path)
    assert (out.returncode, out.stdout) == (3, f"trap 2 0 {path}:{line}: {message}\n")


@pytest.mark.parametrize("deviceid,valid", [
    (b"caf\xc3\xa9", True),                     # two bytes
    (b"\xe2\x82\xac", True),                    # three
    (b"\xf0\x9f\x98\x80", True),                # four
    (b"\xef\xbf\xbf\xf4\x8f\xbf\xbf", True),    # U+FFFF and U+10FFFF, the ends
    (b"\x01\x7f", True),                        # control bytes are UTF-8 too
    (b"\x80", False),                           # a continuation byte alone
    (b"\xc0\x80", False),                       # an overlong NUL
    (b"\xc1\xbf", False),                       # overlong, two bytes
    (b"\xe0\x9f\xbf", False),                   # overlong, three
    (b"\xf0\x8f\xbf\xbf", False),               # overlong, four
    (b"\xed\xa0\x80", False),                   # a surrogate
    (b"\xf4\x90\x80\x80", False),               # past U+10FFFF
    (b"\xf5\x80\x80\x80", False),               # a lead byte no sequence has
    (b"\xe2\x82", False),                       # cut short at the end
    (b"\xe2\x82(", False),                      # cut short inside
    (b"\xff", False),
])
def test_both_readers_draw_utf8_s_line_in_the_same_place(reader, tmp_path, deviceid, valid):
    """runtime/sa2_wbem.c hands each DeviceID back as UTF-16 and refuses one that
    is not UTF-8, so the readers refuse it first, and on the same bytes: the C
    reader's Table 3-7 and Python's strict decoder."""
    p = dataclasses.replace(GOOD, pnp=GOOD.pnp[:3] + (deviceid,) + GOOD.pnp[4:])
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(p))
    out = c_read(reader, path)
    if valid:
        profile, sha = DP.load(path)
        assert profile.pnp[3] == deviceid
        assert out.stdout == lines_for(p, False, str(path), sha)
        return
    line = _line_of(DP.dump(p), "wmi_pnp index=3 ")
    with pytest.raises(DP.ProfileError) as e:
        DP.load(path)
    assert (e.value.line, e.value.message, e.value.kind) == (
        line, "entity 3's DeviceID is not valid UTF-8", MALFORMED)
    assert (out.returncode, out.stdout) == (
        3, f"trap 2 0 {path}:{line}: entity 3's DeviceID is not valid UTF-8\n")


def test_a_file_that_cannot_be_read_or_is_oversized_is_refused_at_line_0(reader, tmp_path):
    """A directory is the case that parted the two readers' words: the C
    reader's fopen takes it and its read fails, Python's open fails first. One
    message now covers every I/O failure in both (the 2026-09-23 review)."""
    (tmp_path / "a-directory").mkdir()
    (tmp_path / "big.txt").write_bytes(DP.dump(GOOD) + b"x" * DP.MAX_BYTES)
    for name, message in (("absent.txt", "the device profile cannot be read"),
                          ("a-directory", "the device profile cannot be read"),
                          ("big.txt", "the device profile is larger than 828477 bytes")):
        path = tmp_path / name
        with pytest.raises(DP.ProfileError) as e:
            DP.load(path)
        assert str(e.value) == f"{path}:0: {message}"
        assert c_read(reader, path).stdout == f"trap 2 0 {path}:0: {message}\n"


def test_an_empty_setting_is_refused_rather_than_read_as_the_default(reader, tmp_path):
    env = dict(os.environ, **{DP.ENV: ""})
    out = subprocess.run([str(reader)], env=env, capture_output=True, text=True)
    assert (out.returncode, out.stdout) == (
        3, "trap 2 0 SA2_DEVICE_PROFILE is set and empty; leave it unset for the default "
           "profile\n")


# ------------------------------------------------------- captures and runs

PROBE_TEXT = b"/* the profile probe, as the tree holds it */\n"


# Where the fixture's runner launched the game, as tools/oracle/run_oracle_probe.py
# records it (`cwd`, and `command` ending `proton runinprefix ./sonic2app-unpacked.exe`),
# and what the game saw, as Wine names it (DP.guest_paths).
CAPTURE_CWD = "/home/user/x/oracle/game"
LAUNCH = ("timeout --signal=TERM --kill-after=5s 200s env STEAM_COMPAT_DATA_PATH=/home/user/p "
          "/home/user/tools/proton runinprefix ./sonic2app-unpacked.exe")
GUEST = {"cwd": "Z:\\home\\user\\x\\oracle\\game",
         "exe_path": "Z:\\home\\user\\x\\oracle\\game\\sonic2app-unpacked.exe"}


def capture(root: Path, name: str, profile: bytes | None, *, receipt_sha: str | None = None,
            counts: dict | None = None, probe_sha: str | None = None,
            profile_first: bool = False, cwd: str | None = CAPTURE_CWD,
            command: str | None = LAUNCH) -> Path:
    """A capture directory as tools/oracle/run_oracle_probe.py --device-profile leaves one."""
    probe = root / DP.PROBE
    probe.parent.mkdir(parents=True, exist_ok=True)
    if not probe.is_file():
        probe.write_bytes(PROBE_TEXT)
    d = root / "private" / "captures" / name
    d.mkdir(parents=True)
    record = None
    if profile is not None:
        (d / DP.FILE).write_bytes(profile)
        record = {"file": DP.FILE, "sha256": receipt_sha or hashlib.sha256(profile).hexdigest(),
                  "counts": counts if counts is not None else COUNTS}
    probes = [{"mod_id": "tick_probe", "source_sha256": "0" * 64,
               "source": str(ROOT / "harness" / "replay" / "native_tick_probe.c")},
              {"mod_id": "device_profile", "source": str(probe),
               "source_sha256": probe_sha or hashlib.sha256(PROBE_TEXT).hexdigest()}]
    if profile_first:
        probes.reverse()
    doc = {"session": {"device_profile": record},
           "inputs": {"built_probes": probes, "enabled_mods": [b["mod_id"] for b in probes]}}
    if cwd is not None:
        doc["cwd"] = cwd
    if command is not None:
        doc["command"] = command
    (d / "receipt.json").write_text(json.dumps(doc))
    return d


GOOD_SHA = hashlib.sha256(DP.dump(GOOD)).hexdigest()


def test_a_profile_probe_not_loaded_last_is_refused(tmp_path):
    d = capture(tmp_path, "c", DP.dump(GOOD), profile_first=True)
    with pytest.raises(DP.Refusal, match="somewhere other than last"):
        DP.capture_profile(d, tmp_path)


def test_a_capture_s_profile_is_its_receipt_s(tmp_path):
    d = capture(tmp_path, "c", DP.dump(GOOD))
    assert DP.capture_profile(d, tmp_path) == {
        "path": "private/captures/c/sa2-device-profile.txt", "sha256": GOOD_SHA, "counts": COUNTS,
        "guest": GUEST}


def test_a_capture_with_no_recorded_profile_is_refused_not_run_on_the_default(tmp_path):
    d = capture(tmp_path, "c", None)
    with pytest.raises(DP.Refusal) as e:
        DP.capture_profile(d, tmp_path)
    assert str(e.value) == ("private/captures/c's receipt records no device profile: re-capture "
                            "it with the profile probe (tools/oracle/run_oracle_probe.py "
                            "--device-profile)")


def test_a_profile_edited_after_its_receipt_is_refused(tmp_path):
    d = capture(tmp_path, "c", DP.dump(GOOD))
    (d / DP.FILE).write_bytes(DP.dump(GOOD) + b"\n")
    with pytest.raises(DP.Refusal, match=r"sa2-device-profile.txt hashes to [0-9a-f]{12} and "
                                         r"its receipt records " + GOOD_SHA[:12]):
        DP.capture_profile(d, tmp_path)


def test_receipt_counts_the_profile_does_not_hold_are_refused(tmp_path):
    d = capture(tmp_path, "c", DP.dump(GOOD), counts=dict(COUNTS, wmi_pnp=44))
    with pytest.raises(DP.Refusal, match="receipt counts"):
        DP.capture_profile(d, tmp_path)


def test_a_capture_taken_with_another_profile_probe_is_refused(tmp_path):
    d = capture(tmp_path, "c", DP.dump(GOOD), probe_sha="1" * 64)
    with pytest.raises(DP.Refusal, match="not the one in the tree .*; re-capture it"):
        DP.capture_profile(d, tmp_path)


def test_a_capture_with_a_pad_attached_is_refused_with_the_runtime_s_words(tmp_path):
    pad, _ = edit("xinput_caps", "code=0x0000048f", "code=0x00000000")
    d = capture(tmp_path, "c", pad)
    with pytest.raises(DP.ProfileError) as e:
        DP.capture_profile(d, tmp_path)
    assert e.value.kind == UNREPRODUCIBLE
    assert str(e.value).startswith("private/captures/c/sa2-device-profile.txt:")


def entry(profile: dict | None, repeats: list[str]) -> dict:
    native = {"capture": "private/captures/c/sa2-tick-capture.csv",
              "receipt": "private/captures/c/receipt.json",
              "repeats": [{"capture": f"private/captures/{r}/sa2-tick-capture.csv",
                           "receipt": f"private/captures/{r}/receipt.json"} for r in repeats]}
    if profile is not None:
        native["device_profile"] = profile
    return {"name": "boot", "native": native}


REGISTERED = {"path": "private/captures/c/sa2-device-profile.txt", "sha256": GOOD_SHA}
# A capture's profile as the tools hand it to a run: checked by capture_profile,
# with the directory and executable its capture's game saw.
CHECKED = dict(REGISTERED, counts=COUNTS, guest=GUEST)


def test_a_registered_replay_s_profile_is_its_capture_s_and_every_repeat_s(tmp_path):
    capture(tmp_path, "c", DP.dump(GOOD))
    capture(tmp_path, "r", DP.dump(GOOD))
    assert DP.replay_profile(entry(REGISTERED, ["r"]), tmp_path) == CHECKED


def test_a_registered_replay_without_a_profile_is_refused(tmp_path):
    capture(tmp_path, "c", DP.dump(GOOD))
    with pytest.raises(DP.Refusal) as e:
        DP.replay_profile(entry(None, []), tmp_path)
    assert str(e.value) == (
        "boot: the registry records no device profile for "
        "private/captures/c/sa2-tick-capture.csv; re-capture it with the profile probe "
        "(tools/oracle/run_oracle_probe.py --device-profile) and register its "
        "native.device_profile")


def test_a_registry_profile_that_is_not_the_capture_s_is_refused(tmp_path):
    capture(tmp_path, "c", DP.dump(GOOD))
    with pytest.raises(DP.Refusal, match="is not the one its capture's receipt records"):
        DP.replay_profile(entry(dict(REGISTERED, sha256="0" * 64), []), tmp_path)


def test_a_repeat_on_another_device_set_is_refused(tmp_path):
    capture(tmp_path, "c", DP.dump(GOOD))
    capture(tmp_path, "r", DP.dump(dataclasses.replace(GOOD, pnp=GOOD.pnp[:44])),
            counts=dict(COUNTS, wmi_pnp=44))
    with pytest.raises(DP.Refusal, match="a repeat of another device set shows nothing"):
        DP.replay_profile(entry(REGISTERED, ["r"]), tmp_path)


def test_a_repeat_with_no_profile_is_refused(tmp_path):
    capture(tmp_path, "c", DP.dump(GOOD))
    capture(tmp_path, "r", None)
    with pytest.raises(DP.Refusal, match="private/captures/r's receipt records no device profile"):
        DP.replay_profile(entry(REGISTERED, ["r"]), tmp_path)


PLACEHOLDERS = {"cwd": "C:\\SA2", "exe_path": "C:\\SA2\\sonic2app.exe"}


def test_a_run_must_say_it_read_the_profile_it_was_handed():
    handed = CHECKED
    run = {"device_profile": {"source": REGISTERED["path"], "default": False,
                              "sha256": GOOD_SHA}, "virtual": dict(GUEST, sleep_ms=0)}
    assert DP.check_run(run, handed) == REGISTERED
    assert DP.check_run({"device_profile": {"source": "default", "default": True, "sha256": ""},
                         "virtual": PLACEHOLDERS}, DP.DEFAULT) == DP.DEFAULT
    with pytest.raises(DP.Refusal) as e:
        DP.check_run({"device_profile": {"source": "default", "default": True, "sha256": ""},
                      "virtual": PLACEHOLDERS}, handed)
    assert str(e.value) == ("the run read device profile default and was handed "
                            f"private/captures/c/sa2-device-profile.txt ({GOOD_SHA[:12]})")
    with pytest.raises(DP.Refusal, match="no device_profile block"):
        DP.check_run({}, DP.DEFAULT)


def test_the_environment_a_run_gets(monkeypatch):
    assert DP.run_env(DP.DEFAULT) == {}
    assert DP.run_env(CHECKED) == {"SA2_DEVICE_PROFILE": REGISTERED["path"],
                                   "SA2_GUEST_CWD": GUEST["cwd"],
                                   "SA2_GUEST_EXE_PATH": GUEST["exe_path"]}
    with pytest.raises(DP.Refusal, match="cannot travel in a run spec"):
        DP.run_env({"path": "a|b", "sha256": GOOD_SHA})
    monkeypatch.setenv(DP.ENV, "left/in/a/shell.txt")
    monkeypatch.setenv("SA2_XINPUT_PAD", "1")
    monkeypatch.setenv("SA2_GUEST_CPUS", "16")
    monkeypatch.setenv("SA2_GUEST_CWD", "D:\\left\\in\\a\\shell")
    monkeypatch.setenv("SA2_GUEST_EXE_PATH", "D:\\left\\in\\a\\shell\\x.exe")
    monkeypatch.setenv("SA2_PUMP_POLLS", "7")
    monkeypatch.setenv("SA2_GUEST_LCID", "0x0411")
    monkeypatch.setenv("SA2_GUEST_ACP", "932")
    clean = DP.clean_environ()
    assert [k for k in ("SA2_DEVICE_PROFILE", "SA2_XINPUT_PAD", "SA2_GUEST_CPUS",
                        "SA2_GUEST_CWD", "SA2_GUEST_EXE_PATH", "SA2_GUEST_LCID",
                        "SA2_GUEST_ACP") if k in clean] == []
    assert clean["SA2_PUMP_POLLS"] == "7"            # only the device set is stripped


def test_two_records_are_one_device_set_only_by_content():
    assert DP.same(DP.DEFAULT, DP.DEFAULT)
    assert DP.same(REGISTERED, {"path": "elsewhere.txt", "sha256": GOOD_SHA})
    assert not DP.same(DP.DEFAULT, REGISTERED)
    assert not DP.same(REGISTERED, dict(REGISTERED, sha256="0" * 64))
    assert not DP.same(None, None)


# ------------------------------------------------------------ the tools

def test_the_runner_records_the_contract_s_fields_and_fails_a_refused_profile(tmp_path):
    import run_oracle_probe as R
    good = tmp_path / "good" / DP.FILE
    good.parent.mkdir()
    good.write_bytes(DP.dump(GOOD))
    record, row = R.profile_artifact(good)
    assert record == {"file": "sa2-device-profile.txt", "sha256": GOOD_SHA, "counts": COUNTS}
    assert row["check"]["ok"] is True and row["present"] is True
    pad = tmp_path / "pad" / DP.FILE
    pad.parent.mkdir()
    pad.write_bytes(edit("xinput_caps", "code=0x0000048f", "code=0x00000000")[0])
    record, row = R.profile_artifact(pad)
    assert record["counts"] is None
    assert record["refused"] == {
        "line": at("xinput_caps"), "kind": "unreproducible",
        "message": "XInput slot 0 answered 0 natively, and the runtime answers only 1167 (not "
                   "connected): a pad's input is part of no recording"}
    assert row["check"]["ok"] is False


def test_the_runner_applies_the_readers_size_limit_and_hashes_the_whole_file(tmp_path):
    """The runner reads through DP.load, as the replay tools and the runtime do,
    so an oversized profile fails the capture at line 0 unread; the receipt still
    records the whole file's sha256 and size."""
    import run_oracle_probe as R
    big = tmp_path / DP.FILE
    data = DP.dump(GOOD) + b"x" * DP.MAX_BYTES
    big.write_bytes(data)
    record, row = R.profile_artifact(big)
    assert record == {"file": DP.FILE, "sha256": hashlib.sha256(data).hexdigest(),
                      "counts": None,
                      "refused": {"line": 0, "kind": "malformed",
                                  "message": "the device profile is larger than 828477 bytes"}}
    assert (row["check"]["ok"], row["check"]["bytes"]) == (False, len(data))


def test_the_recording_s_replays_must_have_seen_its_devices():
    import record_stage as RS
    rec = {"file": DP.FILE, "sha256": GOOD_SHA, "counts": COUNTS}
    same = {"device_profile": dict(rec)}
    other = {"device_profile": dict(rec, sha256="0" * 64)}
    assert RS.same_devices(rec, [same, same]) is True
    assert RS.same_devices(rec, [same, other]) is False
    assert RS.same_devices(rec, [same, {"device_profile": None}]) is False
    assert RS.same_devices(None, [same]) is False


# ------------------------------------------------------------ the capture's directory
#
# The game's current directory and its own file name are answers of the
# capture's machine, as the processor count is: CRI's file table holds the
# opening movie's full path, built from the current directory, and the boot
# replay's tick-380 divergence was that path (agents-journals/s4/2026-09-24-tick-380-xeci.md).
# A run that stands for a capture answers the capture's; one that stands for
# none keeps C:\SA2.

# What the oracle's receipts record (tools/oracle/run_oracle_probe.py), and the
# game's own words for the same directory: the opening movie's path in CRI's
# file table at 0x01755018, read out of the native boot snapshots
# (private/generated/s4/burst/w1/static-tick380), and the image Wine's +loaddll
# trace names in native-outcomes-v1a's log.
ORACLE_CWD = "/workspace/OpenSA2/private/generated/oracle/game"
NATIVE_XECI_PATH = ("Z:\\home\\user\\OpenSA2\\private\\generated\\oracle\\game"
                    "\\resource\\gd_pc\\op360us.sfd")
NATIVE_IMAGE = ("Z:\\home\\user\\OpenSA2\\private\\generated\\oracle\\game"
                "\\sonic2app-unpacked.exe")


def test_the_oracle_s_directory_is_the_one_the_native_game_saw():
    got = DP.guest_paths({"cwd": ORACLE_CWD, "command": LAUNCH})
    assert got["cwd"] + "\\resource\\gd_pc\\op360us.sfd" == NATIVE_XECI_PATH
    assert got["exe_path"] == NATIVE_IMAGE


def test_a_capture_s_directory_and_executable_are_its_launch_s():
    assert DP.guest_paths({"cwd": CAPTURE_CWD, "command": LAUNCH}) == GUEST
    # game arguments after the program, and a program named by an absolute path
    args = LAUNCH + " --level metalharbor --character sonic"
    assert DP.guest_paths({"cwd": CAPTURE_CWD, "command": args}) == GUEST
    absolute = LAUNCH.replace("./sonic2app-unpacked.exe", "/srv/g/../game/a.exe")
    assert DP.guest_paths({"cwd": CAPTURE_CWD, "command": absolute})["exe_path"] == \
        "Z:\\srv\\game\\a.exe"
    assert DP.wine_path("/") == "Z:\\"


@pytest.mark.parametrize("receipt, message", [
    ({"command": LAUNCH}, "records no cwd"),
    ({"cwd": "home/user/x", "command": LAUNCH}, "is not an absolute Unix path"),
    ({"cwd": CAPTURE_CWD}, "records no launch command"),
    ({"cwd": CAPTURE_CWD, "command": "wine ./sonic2app-unpacked.exe"},
     "names no program after 'runinprefix'"),
    ({"cwd": CAPTURE_CWD, "command": "proton runinprefix"}, "names no program after"),
    ({"cwd": CAPTURE_CWD, "command": "proton runinprefix a runinprefix b"},
     "names no program after"),
    ({"cwd": CAPTURE_CWD, "command": "proton runinprefix 'a"}, "does not parse"),
    ({"cwd": "/home/user/a\\b", "command": LAUNCH}, "holds a backslash"),
    ({"cwd": "/home/user/p/pfx/drive_c/game", "command": LAUNCH}, "inside a prefix's drive_c"),
])
def test_a_directory_wine_would_name_otherwise_or_none_is_refused(receipt, message):
    with pytest.raises(DP.Refusal, match=message):
        DP.guest_paths(receipt, "c")


def test_a_capture_whose_receipt_names_no_directory_is_refused(tmp_path):
    d = capture(tmp_path, "c", DP.dump(GOOD), cwd=None)
    with pytest.raises(DP.Refusal, match="private/captures/c: the receipt records no cwd"):
        DP.capture_profile(d, tmp_path)


def test_a_repeat_run_in_another_directory_is_refused(tmp_path):
    capture(tmp_path, "c", DP.dump(GOOD))
    capture(tmp_path, "r", DP.dump(GOOD), cwd="/home/user/elsewhere")
    with pytest.raises(DP.Refusal, match="a repeat in another directory shows nothing"):
        DP.replay_profile(entry(REGISTERED, ["r"]), tmp_path)


def test_a_capture_s_profile_without_its_directory_is_never_run():
    with pytest.raises(DP.Refusal, match="carries no guest directory"):
        DP.run_env(REGISTERED)
    with pytest.raises(DP.Refusal, match="carries no guest directory"):
        DP.run_env(dict(CHECKED, guest={"cwd": GUEST["cwd"]}))
    with pytest.raises(DP.Refusal, match="cannot travel in a run spec"):
        DP.run_env(dict(CHECKED, guest=dict(GUEST, cwd="Z:\\a=b")))


def test_what_a_run_on_either_profile_must_show_it_answered():
    assert DP.guest_record(DP.DEFAULT) == PLACEHOLDERS
    assert DP.guest_record(CHECKED) == GUEST
    assert DP.run_guest({"virtual": dict(GUEST, sleep_ms=3, last_error=0)}) == GUEST
    for bad in ({}, {"virtual": {"cwd": GUEST["cwd"]}}, {"virtual": None}):
        with pytest.raises(DP.Refusal, match="no virtual cwd and exe_path"):
            DP.run_guest(bad)


def test_a_run_that_answered_another_directory_is_refused():
    block = {"source": REGISTERED["path"], "default": False, "sha256": GOOD_SHA}
    default_block = {"source": "default", "default": True, "sha256": ""}
    # the capture's profile read, in the placeholder directory: the control run
    # of 2026-09-24 before the profile carried the directory
    with pytest.raises(DP.Refusal) as e:
        DP.check_run({"device_profile": block, "virtual": PLACEHOLDERS}, CHECKED)
    assert str(e.value) == (
        "the run answered the guest directory 'C:\\\\SA2' and executable "
        "'C:\\\\SA2\\\\sonic2app.exe', and its device profile's capture ran in "
        "'Z:\\\\home\\\\user\\\\x\\\\oracle\\\\game' as "
        "'Z:\\\\home\\\\user\\\\x\\\\oracle\\\\game\\\\sonic2app-unpacked.exe'")
    # the right directory, the wrong executable
    with pytest.raises(DP.Refusal, match="the run answered the guest directory"):
        DP.check_run({"device_profile": block,
                      "virtual": dict(GUEST, exe_path=GUEST["cwd"] + "\\sonic2app.exe")}, CHECKED)
    # a run on the default that answered a capture's directory
    with pytest.raises(DP.Refusal, match="the run answered the guest directory"):
        DP.check_run({"device_profile": default_block, "virtual": GUEST}, DP.DEFAULT)
    with pytest.raises(DP.Refusal, match="no virtual cwd and exe_path"):
        DP.check_run({"device_profile": block}, CHECKED)


def test_a_capture_directory_through_a_symlink_is_refused(tmp_path):
    """The working-directory review's nit 5: Wine names the current directory with
    symlinks resolved, so a directory reached through one is refused rather than fed
    to the guest; the executable is named as launched, so its symlink is not."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "game.exe").write_text("")
    (real / "linked.exe").symlink_to(real / "game.exe")
    link = tmp_path / "link"
    link.symlink_to(real)
    cmd = "wine-env runinprefix ./linked.exe"
    got = DP.guest_paths({"cwd": str(real), "command": cmd})
    assert got["cwd"] == "Z:" + str(real).replace("/", "\\")
    with pytest.raises(DP.Refusal, match="runs through a symlink"):
        DP.guest_paths({"cwd": str(link), "command": cmd})
