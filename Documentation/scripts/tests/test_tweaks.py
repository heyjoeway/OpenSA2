"""The play tweaks: runtime/sa2_tweaks.c's reader and report, the list in runtime/sa2_tweaks.h and
the places each tweak is named, and the first tweak, no-event-bars (runtime/tweaks/no-event-bars.c),
on the events' "shown at 4:3?" answer (0x005F8EC0).

The reader and a tweak's file are built with the pinned zig, as tools/s4/link_s4.py compiles a
runtime source (-O2, x86_64-linux-musl), and linked with a driver that stands in for the lifted
answer and the runtime's peek and register accessors. The stand-in `__real_sub_5f8ec0` does what
the lifted body does: EAX = 1 when ECX is in the game's table of 4:3 events, else 0, then the RET
that pops the return address (ESP + 4). Each call puts a caller's return address at guest [ESP]
first, as the lifted CALL does, so a case reads what each caller would see in EAX.

The list's own tests hold every tweak in SA2_TWEAK_LIST to its runtime/tweaks/<name>.c, its line
in the Android settings (GameSettings.TWEAKS) and its line in the release's README, and the
reverse; each tweak file's TWEAK_WRAPS line to the wraps the compiled file defines; and every link
tool's lists to the files (tools/s4/tweak_sources.py), so a tweak added in one place and not the
others fails here by name.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "tools" / "spike"))
import link_s4 as LS  # noqa: E402
import tweak_sources as TS  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
GAME_SETTINGS = ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
EXECUTOR, MOVIE = "0x005FB508", "0x00601948"     # the two callers' return addresses
TABLE = [21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409]
FRAMED_43 = [21, 205, 206, 360, 361]             # the events that keep their bars
IN_ENGINE = [e for e in TABLE if e != 409]       # 409, the one movie, is the movie player's

# The guest stack at 0x20000. argv: pairs of (return address, event ID); each prints
# "<event> <EAX> <ESP moved>".
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
static uint8_t stack[0x100];
static uint32_t gpr[8];

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static const uint32_t table[14] = {21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409};
void *__real_sub_5f8ec0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    gpr[SA2_EAX] = 0;
    for (int i = 0; i < 14; i++) if (gpr[SA2_ECX] == table[i]) gpr[SA2_EAX] = 1;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_5f8ec0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i + 1 < argc; i += 2) {
        const uint32_t ret = (uint32_t)strtoul(argv[i], NULL, 0), event = (uint32_t)strtoul(argv[i + 1], NULL, 0);
        const uint32_t esp = strcmp(argv[i], "unreadable") == 0 ? 0x90000u : STACK + 0x80u;
        if (esp == STACK + 0x80u) memcpy(stack + 0x80, &ret, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_ECX] = event;
        gpr[SA2_EAX] = 0xDEADBEEFu;
        int token;
        if (__wrap_sub_5f8ec0(&token, 0x005F8EC0u, &token) != &token) return 3;
        printf("%u %u %u\n", event, gpr[SA2_EAX], gpr[SA2_ESP] - esp);
    }
    return 0;
}
"""


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def zig() -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    return ZIG


@pytest.fixture(scope="module")
def exe(zig, tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("tweaks")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(zig), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "no-event-bars.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def names() -> list[str]:
    return [r["name"] for r in TS.rows(RUNTIME)]


# ------------------------------------------------------------------ the reader and the report

# The intro (361) and a stage's (21) in-engine events, both framed for 4:3, a Dark story event
# (100) that is not, an event outside the table (50), and the one movie in the table (409, at the
# movie player's call).
CALLS = (EXECUTOR, "361", EXECUTOR, "21", EXECUTOR, "100", EXECUTOR, "50", MOVIE, "409",
         EXECUTOR, "361", EXECUTOR, "100")
GAME = ["361 1 4", "21 1 4", "100 1 4", "50 0 4", "409 1 4", "361 1 4", "100 1 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}])
def test_off_is_the_lifted_answer_and_says_nothing(exe, env):
    assert run(exe, *CALLS, **env) == (GAME, "")


def test_names_are_trimmed_and_repeats_are_one(exe):
    out, err = run(exe, EXECUTOR, "100", SA2_TWEAKS=" no-event-bars ,no-event-bars,")
    assert out == ["100 0 4"]
    assert err.splitlines()[0] == "sa2 tweaks: event-bars off (SA2_TWEAKS= no-event-bars ,no-event-bars,)"


@pytest.mark.parametrize("value, on", [("event-bars", False), ("No-Event-Bars", False),
                                       ("no-event-bars,widescreen-fmv", True)])
def test_a_name_that_is_no_tweak_is_named_and_ignored(exe, value, on):
    out, err = run(exe, EXECUTOR, "100", SA2_TWEAKS=value)
    assert out == (["100 0 4"] if on else ["100 1 4"])
    unknown = value.split(",")[-1]
    assert err.splitlines()[0] == (
        f"sa2 tweaks: {'event-bars off' if on else 'none on'} (SA2_TWEAKS={value}; no tweak is named "
        f"\"{unknown}\", so it is ignored; the tweaks: {', '.join(names())})")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-event-bars", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=no-event-bars ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")
    assert run(exe, *CALLS, **{replay: "/dev/null"}) == (GAME, "")


# ------------------------------------------------------------------ no-event-bars

def test_no_event_bars_answers_0_to_the_executor_but_for_the_events_framed_for_4_3(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-event-bars")
    assert out == ["361 1 4", "21 1 4", "100 0 4", "50 0 4", "409 1 4", "361 1 4", "100 0 4"]
    assert err.splitlines() == [
        "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)",
        "sa2 tweaks: event-bars off: event 361 keeps its side bars (its scene is framed for 4:3)",
        "sa2 tweaks: event-bars off: event 21 keeps its side bars (its scene is framed for 4:3)",
        "sa2 tweaks: event-bars off: event 100's side bars not drawn"]


def test_each_in_engine_event_in_the_table(exe):
    """The five framed for 4:3 keep their bars (the answer stays 1); the other eight lose them."""
    calls = [x for e in IN_ENGINE for x in (EXECUTOR, str(e))]
    out, _ = run(exe, *calls, SA2_TWEAKS="no-event-bars")
    assert out == [f"{e} {int(e in FRAMED_43)} 4" for e in IN_ENGINE]
    assert [e for e in IN_ENGINE if e not in FRAMED_43] == [24, 25, 100, 101, 126, 128, 129, 203]
    assert run(exe, *calls)[0] == [f"{e} 1 4" for e in IN_ENGINE]


def test_the_movie_keeps_its_bars(exe):
    out, err = run(exe, MOVIE, "409", MOVIE, "100", SA2_TWEAKS="no-event-bars")
    assert out == ["409 1 4", "100 1 4"]
    assert err == "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)\n"


def test_an_unreadable_stack_keeps_the_bars_and_says_so(exe):
    out, err = run(exe, "unreadable", "100", EXECUTOR, "100", SA2_TWEAKS="no-event-bars")
    assert out == ["100 1 4", "100 0 4"]
    assert ("sa2 tweaks: event-bars off: the guest stack at 0x00090000 does not read, so the caller "
            "is unknown and the event keeps its bars") in err.splitlines()


# ------------------------------------------------------------------ the list and its places

def android_tweaks() -> list[str]:
    """GameSettings.TWEAKS's names, in its order."""
    text = GAME_SETTINGS.read_text()
    m = re.search(r"static final Tweak\[\] TWEAKS = \{(.*?)\n    \};", text, re.S)
    assert m, f"{GAME_SETTINGS.name} has no TWEAKS array"
    return re.findall(r'new Tweak\(\s*"([^"]+)"', m.group(1))


def test_the_list_is_in_name_order_and_each_name_is_one_spelling():
    rows = TS.rows(RUNTIME)
    assert rows and names() == sorted(names()) and len(set(names())) == len(rows)
    for r in rows:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", r["name"]), r
        assert r["bit"] == "SA2_TWEAK_" + r["name"].upper().replace("-", "_"), r
        assert r["report"] and r["summary"], r


def test_the_compiled_reader_holds_the_headers_list(exe):
    _, err = run(exe, SA2_TWEAKS="?")
    assert err.rstrip("\n").endswith(f"; the tweaks: {', '.join(names())})")


def test_every_tweak_has_its_file_and_every_file_its_tweak():
    assert [Path(s).stem for s in TS.sources(RUNTIME)] == names()


def test_every_tweak_has_its_android_switch_and_every_switch_its_tweak():
    assert android_tweaks() == names()


def test_every_tweak_is_in_the_release_readme():
    spec = importlib.util.spec_from_file_location("release_build_tweaks_under_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    readme = rb.player_readme("x86_64-linux")
    listed = re.findall(r"^    SA2_TWEAKS=(\S+) +(.+)$", readme, re.M)
    assert listed == [(r["name"], r["summary"]) for r in TS.rows(RUNTIME)]


@pytest.mark.parametrize("source", TS.sources(RUNTIME))
def test_each_tweak_file_defines_the_wraps_its_line_names(zig, tmp_path, source):
    """Compiled alone with -Wall -Wextra -Werror: the `__wrap_` it defines and the `__real_` it
    calls are the TWEAK_WRAPS line's, and it asks sa2_tweak_on whether its tweak is on."""
    obj = tmp_path / "t.o"
    done = subprocess.run([str(zig), "cc", *CC, "-c", str(RUNTIME / source), "-o", str(obj)],
                          capture_output=True, text=True, env=zig_env(tmp_path))
    assert done.returncode == 0, done.stderr
    symbols = subprocess.run(["nm", str(obj)], capture_output=True, text=True, check=True).stdout
    defined = sorted(re.findall(r"^\S+ [Tt] __wrap_(\w+)$", symbols, re.M))
    called = sorted(re.findall(r"^ +U __real_(\w+)$", symbols, re.M))
    declared = sorted(dict(TS.files(RUNTIME))[source])
    assert defined == declared and called == declared
    assert re.search(r"^ +U sa2_tweak_on$", symbols, re.M)


# ------------------------------------------------------------------ the links

def test_every_link_compiles_the_tweaks_and_wraps_their_functions():
    assert "sa2_tweaks.c" in LS.RUNTIME_C
    tweaks = TS.sources(RUNTIME)
    assert "tweaks/no-event-bars.c" in tweaks
    assert LS.RUNTIME_C[-len(tweaks):] == tweaks
    wraps = TS.wraps(RUNTIME, [])
    assert "sub_5f8ec0" in wraps and LS.PRODUCTION_WRAPS[-len(wraps):] == wraps
    assert len(set(LS.PRODUCTION_WRAPS)) == len(LS.PRODUCTION_WRAPS)
    import device_profile as DP
    assert "SA2_TWEAKS" in DP.PLAY_ENV and "SA2_TWEAKS" in DP.OWNED


def test_the_tools_that_read_link_s4s_literals_add_the_same_tweaks():
    """arm64_link.py reads link_s4.py's literals with ast.literal_eval, build_cost.py with a
    regular expression; each then adds the tweaks as link_s4.py's import does."""
    sys.path.insert(0, str(ROOT / "tools" / "s7"))
    import arm64_link as AL
    import build_cost as BC
    lists = AL.link_s4_lists(ROOT)
    assert lists["RUNTIME_C"] == LS.RUNTIME_C and lists["PRODUCTION_WRAPS"] == LS.PRODUCTION_WRAPS
    assert BC.S3_RUNTIME_C + BC.S4_ADDED_C == LS.RUNTIME_C
    assert BC.PRODUCTION_WRAPS == LS.PRODUCTION_WRAPS


def test_the_runtime_digests_and_the_release_sources_count_the_tweak_files():
    """A change to a tweak file makes every receipt that digests the runtime stale, and the
    release manifest names each tweak file it was built from."""
    import reach_receipt as RR
    import tick_replay as TR
    spec = importlib.util.spec_from_file_location("release_build_globs_under_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    for globs in (TR.RUNTIME_GLOBS, RR.RUNTIME_GLOBS, rb.SOURCE_GLOBS):
        assert "runtime/tweaks/*.c" in globs
    assert "tools/s4/tweak_sources.py" in rb.SOURCE_GLOBS
    assert {f"runtime/{s}" for s in TS.sources(RUNTIME)} <= set(rb.sources(ROOT))


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    rt = tmp_path / "runtime"
    (rt / "tweaks").mkdir(parents=True)
    (rt / "sa2_call.c").write_text("")
    for name, text in files.items():
        (rt / name).write_text(text)
    return rt


def test_tweak_files_are_read_in_name_order(tmp_path):
    rt = tree(tmp_path, {"tweaks/b-two.c": "/* b\n * TWEAK_WRAPS: sub_401000, sub_402000\n */\n",
                         "tweaks/a-one.c": "/* a\n *\n * TWEAK_WRAPS: sub_403000\n */\n"})
    assert TS.sources(rt) == ["tweaks/a-one.c", "tweaks/b-two.c"]
    assert TS.wraps(rt, ["sub_427ee0"]) == ["sub_403000", "sub_401000", "sub_402000"]


@pytest.mark.parametrize("files, refusal", [
    ({"tweaks/a.c": "/* a tweak with no line */\n"}, r"tweaks/a\.c: 0 'TWEAK_WRAPS:' lines"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: sub_401000\n * TWEAK_WRAPS: sub_402000 */\n"},
     r"tweaks/a\.c: 2 'TWEAK_WRAPS:' lines"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: ReadConfig */\n"}, r"tweaks/a\.c: TWEAK_WRAPS names \['ReadConfig'\]"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: */\n"}, r"tweaks/a\.c: TWEAK_WRAPS names nothing"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: sub_401000 */\n", "tweaks/b.c": "/* TWEAK_WRAPS: sub_401000 */\n"},
     r"tweaks/b\.c and tweaks/a\.c both wrap sub_401000"),
    ({"tweaks/sa2_call.c": "/* TWEAK_WRAPS: sub_401000 */\n"}, r"its object would be sa2_call\.o"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: sub_427ee0 */\n"}, r"tweaks/a\.c wraps \['sub_427ee0'\], which the runtime"),
])
def test_a_tweak_file_the_links_cannot_take_is_refused_by_name(tmp_path, files, refusal):
    rt = tree(tmp_path, files)
    with pytest.raises(SystemExit, match=refusal):
        TS.wraps(rt, ["sub_427ee0"])
