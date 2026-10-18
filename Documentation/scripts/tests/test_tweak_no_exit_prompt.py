"""no-exit-prompt, the change asked for as a tweak that this port already makes (runtime/sa2_tweaks.h,
"Asked for, and already so"): closing the game asks no question.

The game asks from its window procedure's WM_CLOSE arm (0x0040184A, then 0x004015F0, 0x004026E0 and
0x00404A00, which creates the dialog at 0x00404CBA). The question never shows because the guest is
never sent WM_CLOSE. A close of the player's window is the player's end request
(host/player/player.cpp, Player::pump_events; test_s5_input_latency_player.py's close cases), which
the launcher turns into the game's SIGTERM (test_installer_launch.py,
test_play_ends_when_the_window_closes) and the runtime into WM_QUIT; the guest then calls
ExitProcess(0) itself.

The arm is not a clean exit on its own. Sent WM_CLOSE at the title, the guest stops on MulDiv, an
import the runtime does not provide, from the dialog's sizer at 0x004029FD, before it reaches the
dialog. Past that, CreateDialogIndirectParamW answers NULL (runtime/sa2_dialog.c), and the guest
takes its no-dialog branch at 0x00404CC2. This file holds that answer: runtime/sa2_dialog.c is
built with the pinned zig, as tools/s4/link_s4.py compiles a runtime source, beside a driver that
calls the entry point with the guest's own arguments and stands in for the guest memory the file
reads (sa2_guest and sa2_peek, for LoadStringW, which no case here reaches).

It also holds the name to what it is: in neither tweak list, with no home file and no switch on the
Android settings screen, since a switch whose on and off are the same would be a switch that lies.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import tweak_sources as TS  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
NAME = "no-exit-prompt"

# The guest's call: 0x00404A00 pushes five arguments (0x00404CA3..0x00404CB9) and calls at
# 0x00404CBA; the call returns to 0x00404CC0, where `test edi,edi / je` at 0x00404CC2 takes the
# no-dialog branch on NULL. The DLGPROC is 0x00403BD0. The template and parent are stand-ins.
RETURN, DLGPROC = 0x00404CC0, 0x00403BD0

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "guest_memory.h"
#include "sa2_dialog.h"

sa2_guest_t sa2_guest;   /* never ready: no image is mapped */
int sa2_peek(uint32_t a, uint32_t n, void *out) { (void)a; (void)n; (void)out; return 1; }

int main(int argc, char **argv) {
    (void)argc;
    sa2_dialog_config_from_env();
    sa2_dialog_reset();
    uint32_t out = 0xDEADBEEFu;
    int ok = sa2_dialog_create_dialog_indirect_param(
        (uint32_t)strtoul(argv[1], NULL, 0), 0x00400000u, 0x01000000u, 0x7E000010u,
        (uint32_t)strtoul(argv[2], NULL, 0), 0, &out);
    sa2_dlg_stats st;
    sa2_dialog_stats(&st);
    const sa2_dlg_event *e = sa2_dialog_log_count() ? sa2_dialog_log_at(0) : NULL;
    printf("ok=%d out=0x%08x strict=%d refused=%u measured=%u noop=%u invented=%u dialogs_refused=%u"
           " log=%u what=%s caller=0x%08x dlgproc=0x%08x\n",
           ok, out, sa2_dialog_strict(), st.refused, st.measured, st.noop_success, st.invented,
           st.dialogs_refused, sa2_dialog_log_count(), e ? e->what : "-", e ? e->caller : 0u,
           e ? e->a[3] : 0u);
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("no_exit_prompt")
    (d / "driver.c").write_text(DRIVER)
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_dialog.c"),
                           "-o", str(d / "driver")], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return d / "driver"


def answer(exe: Path, strict: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "SA2_DIALOG_STRICT"}
    if strict is not None:
        env["SA2_DIALOG_STRICT"] = strict
    p = subprocess.run([str(exe), hex(RETURN), hex(DLGPROC)], capture_output=True, text=True,
                       timeout=30, env=env)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.mark.parametrize("strict,shown", [(None, 0), ("1", 1)])
def test_the_quit_dialog_is_never_created(exe, strict, shown):
    """NULL, counted as a measured answer and recorded with the guest's return address and DLGPROC,
    so the guest takes its no-dialog branch. SA2_DIALOG_STRICT, which turns every
    fabricated success into a refusal, leaves it as it is: NULL is no fabricated success."""
    assert answer(exe, strict) == (
        f"ok=1 out=0x00000000 strict={shown} refused=0 measured=1 noop=0 invented=0 dialogs_refused=1"
        " log=1 what=CreateDialogIndirectParamW caller=0x00404cc0 dlgproc=0x00403bd0")


def test_it_is_recorded_and_not_registered():
    """In neither list, with no file in any home and no Android switch; the tweaks' notes name it."""
    assert NAME not in [r["name"] for r in TS.every_row(RUNTIME)]
    for home in TS.HOMES:
        assert not (ROOT / TS.host_file({"name": NAME, "home": home})).exists(), home
    settings = (ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java").read_text()
    assert f'new Tweak("{NAME}"' not in settings
    notes = (RUNTIME / TS.HEADER).read_text()
    assert f" *   {NAME} (the Mod Loader's DisableExitPrompt" in notes
