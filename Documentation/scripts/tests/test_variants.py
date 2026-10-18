"""tools/s8/variants/variants.py: a Mod Loader code patch as a variant of the lifted function it
patches, chosen by a code-patch tweak (runtime/sa2_tweaks.h, "Add a code-patch tweak").

Three parts. The checks, on a synthetic image whose modules, edge table, wraps and registered
tweaks are written here, so each refusal is caused by one named defect. The whole path on a
synthetic function (`mov eax, 1; ret`, patched to `mov eax, 2`): remill lifts both, the PC24 pass
and the fold run, the double pass and the compile run as a lifted-object build's, and the objects
are linked with runtime/sa2_tweaks.c, the generated wraps and a driver that stands in for the
runtime's memory and return intrinsics, then run: off, and in a replay, the lifted original
answers 1; with the tweak on, the variant answers 2. The synthetic tweak, `t`, is registered in no
list, as a fixture is; test_tweaks.py runs the reader with a registered code-patch tweak. And the
tracked specs and fixtures, held to the checkout's lifted image and its tweak lists.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s8" / "variants"))
import variants as V  # noqa: E402

sys.path.insert(0, str(ROOT / "tools" / "s4"))
import tweak_sources as TS  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
BASE = 0x00400000
F, A, G, H, K = 0x401000, 0x401020, 0x401080, 0x4010A0, 0x4010C0

# F: test eax,eax; je +6; mov eax,1; ret; call G; jmp 0x401020; int3 padding;
#    0x401020: mov eax,3; ret (also the entry of A, an extra anchored on F)
F_CODE = bytes.fromhex("85c0 7406 b801000000 c3 e871000000 eb0f") + b"\xcc" * 15 + bytes.fromhex("b803000000 c3")
G_CODE = bytes.fromhex("b807000000 c3")


def image(code: dict[int, bytes]) -> bytes:
    img = bytearray(b"\xcc" * 0x3000)
    for va, b in code.items():
        img[va - BASE:va - BASE + len(b)] = b
    return bytes(img)


def world(**over) -> V.World:
    mods = (V.Module(F, F, F + 0x40, "map"), V.Module(A, F, F + 0x40, "extra", F),
            V.Module(G, G, G + 0x10, "map"), V.Module(H, H, H + 0x10, "map"),
            V.Module(K, K, K + 0x10, "map"))
    w = dict(image=image({F: F_CODE, G: G_CODE, H: G_CODE, K: G_CODE}),
             code=((0x401000, 0x402000),), modules=mods, edges={H: (V.EDGE_JMP, 0)},
             taken=frozenset(), builtin=frozenset({"no-event-bars"}),
             registered={"ret-early": "returns early"})
    w.update(over)
    return V.World(**w)


def tweak(*patches, name="t", functions=(F,), report="test tweak", shipped=False) -> V.Tweak:
    return V.Tweak(name=name, report=report, functions=tuple(functions),
                   patches=tuple(V.Patch(va, bytes.fromhex(old), bytes.fromhex(new))
                                 for va, old, new in patches), shipped=shipped)


def refused(w, t, *words):
    with pytest.raises(V.Refused) as e:
        V.plan_tweak(w, t)
    for word in words:
        assert word in str(e.value), str(e.value)
    return str(e.value)


# ---------------------------------------------------------------- the checks

def test_a_ret_in_a_function_is_a_variant_of_that_function_alone():
    p = V.plan_tweak(world(), tweak((F + 4, "b8", "c3")))
    assert [(r["population"], r["entry"], r["variant"]) for r in p["modules"]] == \
        [("map", "0x00401000", "sub_401000__tw_t")]
    assert p["modules"][0]["live_before"] == 8 and p["modules"][0]["live_after"] == 7
    assert p["modules"][0]["edges_added"] == [] and p["changed_bytes"] == 1


def test_a_patch_a_second_entry_runs_makes_a_variant_of_that_module_too():
    p = V.plan_tweak(world(), tweak((A + 1, "03", "05")))
    assert [(r["population"], r["entry"]) for r in p["modules"]] == \
        [("map", "0x00401000"), ("extra", "0x00401020")]
    only_f = V.plan_tweak(world(), tweak((F + 5, "01", "02")))
    assert [r["entry"] for r in only_f["modules"]] == ["0x00401000"]


def test_old_bytes_that_are_not_the_image_are_refused_by_address():
    refused(world(), tweak((F + 4, "b9", "c3")), "tweak t", "0x00401004", "expects the bytes b9",
            "holds b8")


def test_a_data_write_is_a_poke_not_a_variant():
    refused(world(), tweak((0x402000, "cc", "00")), "not in a code section", "guest-memory poke")


def test_a_patch_outside_the_named_functions_is_refused():
    refused(world(), tweak((G, "b8", "c3")), "outside the functions the spec names")


def test_bytes_no_trace_runs_are_refused():
    refused(world(), tweak((F + 0x12, "cc", "90")), "no lifted module's trace runs", "0x00401012")


def test_a_new_tail_jump_to_a_dispatched_entry_is_accepted_and_recorded():
    jmp = "e9" + (H - (F + 4 + 5)).to_bytes(4, "little").hex()
    p = V.plan_tweak(world(), tweak((F + 4, "b801000000", jmp)))
    assert p["modules"][0]["edges_added"] == ["jmp to 0x004010a0"]


def test_a_new_jump_the_edge_table_would_trap_on_is_refused():
    jmp = "e9" + (K - (F + 4 + 5)).to_bytes(4, "little").hex()
    refused(world(), tweak((F + 4, "b801000000", jmp)), "jmp to 0x004010c0", "has no row",
            "reviewed dispatch exception")
    jmp = "e9" + (K + 4 - (F + 4 + 5)).to_bytes(4, "little").hex()
    refused(world(), tweak((F + 4, "b801000000", jmp)), "jmp to 0x004010c4", "no lifted module starts")


def test_a_call_to_no_module_is_refused():
    call = "e8" + (G + 2 - (F + 0xA + 5)).to_bytes(4, "little").hex()
    refused(world(), tweak((F + 0xA, "e871000000", call)), "calls 0x00401082")


def test_bytes_that_decode_to_nothing_and_unliftable_forms_are_refused():
    refused(world(), tweak((F + 4, "b801000000", "0f04909090")), "decode to no instruction at 0x00401004")
    refused(world(), tweak((F + 4, "b801000000", "0fddc19090")), "paddusw at 0x00401004",
            "no lifted module continues at 0x00401007")


def test_a_wrapped_function_and_a_shared_module_are_refused():
    refused(world(taken=frozenset({"sub_401000"})), tweak((F + 4, "b8", "c3")), "PRODUCTION_WRAPS")
    with pytest.raises(V.Refused, match="patched by tweak t too"):
        V.plan_all(world(), [tweak((F + 4, "b8", "c3")), tweak((F + 5, "01", "02"), name="u")])


def test_names_are_checked():
    refused(world(), tweak((F + 4, "b8", "c3"), name="Bad_Name"), "lowercase words")
    refused(world(), tweak((F + 4, "b8", "c3"), name="no-event-bars"), "already has a tweak named",
            "runtime/tweaks/no-event-bars.c")


def test_a_shipped_spec_is_its_registered_tweak_and_a_fixture_none():
    ret = (F + 4, "b8", "c3")
    p = V.plan_tweak(world(), tweak(ret, name="ret-early", report="returns early", shipped=True))
    assert [r["variant"] for r in p["modules"]] == ["sub_401000__tw_ret_early"]
    refused(world(), tweak(ret, name="ret-late", shipped=True), "tweak ret-late", "does not register it",
            "SA2_TWEAK_PATCH_LIST")
    refused(world(), tweak(ret, name="ret-early", report="returns", shipped=True),
            "the report 'returns' is not the one SA2_TWEAK_LIST gives it, 'returns early'")
    refused(world(), tweak(ret, name="ret-early", report="returns early"),
            "registers ret-early as a code-patch tweak", "tools/s8/variants/tweaks/ret-early.json")


def test_the_bits_are_counted_with_the_lists():
    full = world(builtin=frozenset(f"p{i}" for i in range(62)))      # 62 + ret-early: 63 listed
    assert V.plan_all(full, [tweak((F + 4, "b8", "c3"))])
    with pytest.raises(V.Refused, match="the 63 tweaks of runtime/sa2_tweaks.h and 2 unregistered"):
        V.plan_all(full, [tweak((F + 4, "b8", "c3")), tweak((G, "b8", "c3"), name="u", functions=(G,))])


def test_the_tools_limit_is_the_runtimes_mask():
    h = (ROOT / "runtime" / "sa2_tweaks.h").read_text()
    assert f"#define SA2_TWEAK_MAX {V.MAX_TWEAKS}\n" in h
    assert V.MAX_TWEAKS == 64


def test_a_spec_in_the_tweaks_folder_is_a_shipped_one(tmp_path, monkeypatch):
    assert (V.SPECS_DIR, V.FIXTURES_DIR) == (ROOT / TS.SPECS, ROOT / TS.FIXTURES)
    fixture = V.read_spec(ROOT / TS.FIXTURES / "test-speedup-jingle.json")
    assert not fixture.shipped and fixture.spec == f"{TS.FIXTURES}/test-speedup-jingle.json"
    elsewhere = tmp_path / "x.json"
    elsewhere.write_text((ROOT / TS.FIXTURES / "test-speedup-jingle.json").read_text())
    assert not V.read_spec(elsewhere).shipped
    monkeypatch.setattr(V, "SPECS_DIR", tmp_path)
    assert V.read_spec(elsewhere).shipped and V.shipped_specs() == [elsewhere]


# ---------------------------------------------------------------- a Codes.lst

def test_codes_entries_and_writes():
    text = ('Code "Rings"\nwrite16 0174B028 999\nAuthor X\n\n'
            'Patch "Two"\nwritenop 0044CEA6 3\nwrite16 0044CEA7 0xE990\n; a comment\nCategory Cheats\n'
            'Patch "Jump"\nwritejump 0047ADBC 0x47ADCB\nwrite8 0174AFDB 0 x2\n'
            'Patch "Mul"\nmuls16 8A6240 5 x500\n')
    entries = V.codes_entries(text)
    assert [t for t, _ in entries] == ["Two", "Jump", "Mul"]
    assert V.codes_writes(entries[0][1]) == {0x44CEA6: 0x90, 0x44CEA7: 0x90, 0x44CEA8: 0xE9}
    assert V.codes_writes(entries[1][1]) == {0x47ADBC: 0xE9, 0x47ADBD: 0x0A, 0x47ADBE: 0, 0x47ADBF: 0,
                                             0x47ADC0: 0, 0x174AFDB: 0, 0x174AFDC: 0}
    with pytest.raises(ValueError, match="muls16"):
        V.codes_writes(entries[2][1])


# ---------------------------------------------------------------- the whole path

DRIVER = r"""
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "remill/Arch/X86/Runtime/State.h"

extern "C" {
static uint8_t guest[0x10000];                 /* guest addresses 0x10000..0x1FFFF */
static uint32_t returned_to;
uint8_t *sa2_guest_fast_base = nullptr;        /* every access takes the slow path */
uint8_t *sa2_guest_read_slow(uint32_t a, uint32_t n) { (void)n; return guest + (a - 0x10000u); }
uint8_t *sa2_guest_write_slow(uint32_t a, uint32_t n) { (void)n; return guest + (a - 0x10000u); }
uint32_t __remill_read_memory_32(void *m, uint32_t a) { (void)m; uint32_t v; std::memcpy(&v, guest + (a - 0x10000u), 4); return v; }
void *__remill_write_memory_32(void *m, uint32_t a, uint32_t v) { std::memcpy(guest + (a - 0x10000u), &v, 4); return m; }
void *__remill_function_return(void *s, uint32_t pc, void *m) { (void)s; returned_to = pc; return m; }
void *__remill_missing_block(void *s, uint32_t pc, void *m) { (void)s; std::printf("missing %x\n", pc); std::exit(3); return m; }
void *__remill_error(void *s, uint32_t pc, void *m) { (void)s; std::printf("error %x\n", pc); std::exit(3); return m; }
void *__sa2_dispatch_stub(void *s, uint32_t pc, void *m) { (void)s; std::printf("dispatch %x\n", pc); std::exit(3); return m; }
void *sub_401000(void *state, uint32_t pc, void *memory);
}

int main() {
    alignas(16) static unsigned char raw[sizeof(State)];   /* State has no default constructor */
    State &st = *reinterpret_cast<State *>(raw);
    st.gpr.rsp.dword = 0x18000u;
    const uint32_t ret = 0x00404242u;
    std::memcpy(guest + 0x8000, &ret, 4);
    int token;
    if (sub_401000(&st, 0x00401000u, &token) != &token) return 4;
    std::printf("eax %u esp %x ret %x\n", st.gpr.rax.dword, st.gpr.rsp.dword, returned_to);
    return 0;
}
"""


@pytest.fixture(scope="module")
def lifted(tmp_path_factory):
    """The synthetic function lifted, folded, legalised and compiled, original and variant,
    linked with sa2_tweaks.c, the generated wraps and the driver. And `wide`, the same link with
    sa2_tweaks.c built against a copy of runtime/sa2_tweaks.h whose list has WIDE tweaks, so the
    fixture `t` takes bit WIDE, past a 32-bit mask."""
    import common as C
    if not ZIG.is_file() or not (C.REMILL_PREFIX / "bin" / "remill-lift-14").is_file():
        pytest.fail(f"the pinned zig or remill is not present ({ZIG}, {C.REMILL_PREFIX})")
    d = tmp_path_factory.mktemp("variants")
    code = bytes.fromhex("b801000000 c3")
    w = world(image=image({F: code + b"\xcc" * 10}), modules=(V.Module(F, F, F + 0x10, "map"),),
              edges={})
    t = tweak((F + 1, "01", "02"))
    plan = V.plan_tweak(w, t)
    row = plan["modules"][0]
    # A lifted-object build with no modules: its summary names the double pass, so compile is
    # the pass and the build's own command for the map population.
    objdir = d / "objects"
    objdir.mkdir()
    (objdir / "summary.json").write_text(json.dumps({
        "target": "x86_64-linux-musl", "legalized": True, "f80_mode": "double",
        "pass": "tools/s8/f80_double.py", "pass_sha256": V.sha256(ROOT / "tools/s8/f80_double.py")}))
    (objdir / "build.jsonl").write_text("")
    objs = V.Objects(objdir)
    m = w.modules[0]
    text0, markers0 = V.lift_folded(w.image, m, d / "orig")
    text1, markers1 = V.lift_folded(V.apply(w.image, BASE, t.patches), m, d / "patched")
    text1 = V.rename(text1, m, m.variant(t))
    objs.compile(text0, "map", d / "obj" / "orig.o", "orig")
    objs.compile(text1, "map", d / "obj" / "variant.o", "variant")
    c = d / V.C_FILE
    c.write_text(V.c_source([t], [plan], {"dir": "test", "target": "x86_64-linux-musl",
                                         "f80_mode": "double"}))
    none = d / "none.c"                  # a build of no spec: names no tweak, wraps nothing
    none.write_text(V.c_source([], [], {"dir": "test", "target": "x86_64-linux-musl", "f80_mode": "double"}))
    (d / "driver.cpp").write_text(DRIVER)
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"), ZIG_GLOBAL_CACHE_DIR=os.environ.get(
        "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    inc = ["-I", str(ROOT / "runtime"), "-I", str(C.REMILL_INC)]
    objs_c = []
    for src in (ROOT / "runtime" / "sa2_tweaks.c", c, none):
        o = d / "obj" / (src.stem + ".c.o")
        done = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra",
                               "-Werror", *inc, "-c", str(src), "-o", str(o)],
                              capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
        objs_c.append(o)
    none_o = objs_c.pop()
    assert subprocess.run(["nm", "-g", "--defined-only", str(none_o)], capture_output=True, text=True,
                          check=True).stdout.split()[-1:] == ["sa2_tweak_patches_linked"]
    wide = d / "wide-rt"                 # sa2_tweaks.c includes its own directory's header
    wide.mkdir()
    (wide / "sa2_tweaks.c").write_text((ROOT / "runtime" / "sa2_tweaks.c").read_text())
    (wide / "sa2_tweaks.h").write_text(wide_header())
    wide_o = d / "obj" / "sa2_tweaks.wide.c.o"
    done = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror",
                           "-I", str(wide), "-c", str(wide / "sa2_tweaks.c"), "-o", str(wide_o)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    exes = {}
    for name, tweaks_o in (("run", objs_c[0]), ("wide", wide_o)):
        exes[name] = d / name
        done = subprocess.run([str(ZIG), "c++", "-target", "x86_64-linux-musl", "-O2", "-std=c++17", *inc,
                               "-Wl,-wrap=sub_401000", str(d / "driver.cpp"), str(tweaks_o), *map(str, objs_c[1:]),
                               str(d / "obj" / "orig.o"), str(d / "obj" / "variant.o"), "-o", str(exes[name])],
                              capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
    return {"exe": exes["run"], "wide": exes["wide"], "row": row, "markers": (markers0, markers1),
            "text": (text0, text1), "variant": d / "obj" / "variant.o"}


WIDE = 60                                # the list's tweaks in the wide build; `t` takes bit 60


def wide_names() -> list[str]:
    """The wide build's list: the checkout's, then wide-NN up to WIDE tweaks."""
    names = [r["name"] for r in TS.rows(ROOT / "runtime")]
    assert len(names) < WIDE, f"the checkout's list has {len(names)} tweaks, the wide build's {WIDE}"
    return names + [f"wide-{i:02d}" for i in range(len(names), WIDE)]


def wide_header() -> str:
    h = (ROOT / "runtime" / "sa2_tweaks.h").read_text()
    end = "    /* the end of the list */"
    at = h.index(end, h.index("#define SA2_TWEAK_LIST(X)"))
    pad = "".join(f'    X(SA2_TWEAK_WIDE_{n[5:]}, "{n}", "{n} on", "a padding tweak") \\\n'
                  for n in wide_names() if n.startswith("wide-"))
    return h[:at] + pad + h[at:]


def run(exe: Path, **env: str) -> tuple[str, str]:
    clean = {k: v for k, v in os.environ.items()
             if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout, p.stderr


def test_remill_follows_the_patched_bytes_as_the_checks_walked_them(lifted):
    assert lifted["markers"] == (3, 3) == (lifted["markers"][0], lifted["row"]["pc_markers_expected"])
    assert "@sub_401000(" not in lifted["text"][1] and "@sub_401000__tw_t(" in lifted["text"][1]
    assert V.defined_globals(lifted["variant"]) == ["sub_401000__tw_t"]


def test_off_runs_the_lifted_original_and_says_nothing(lifted):
    assert run(lifted["exe"]) == ("eax 1 esp 18004 ret 404242\n", "")


def test_on_runs_the_variant_and_says_so_once(lifted):
    out, err = run(lifted["exe"], SA2_TWEAKS="t")
    assert out == "eax 2 esp 18004 ret 404242\n"
    assert err.splitlines() == ["sa2 tweaks: test tweak (SA2_TWEAKS=t)",
                                "sa2 tweaks: test tweak: the patched lift of 0x00401000 runs"]


def test_a_variant_tweak_is_listed_after_the_lists_tweaks_and_ignored_in_a_replay(lifted):
    out, err = run(lifted["exe"], SA2_TWEAKS="no-event-bars,u")
    assert out == "eax 1 esp 18004 ret 404242\n"
    plain = [r["name"] for r in TS.rows(ROOT / "runtime")
             if r["name"] not in {p["name"] for p in TS.patches(ROOT / "runtime")}]
    hosts = [r["name"] for r in TS.host_rows(ROOT / "runtime")]
    assert err == ('sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars,u; no tweak is named "u", so it '
                   f"is ignored; the tweaks: {', '.join(plain + ['t'])}"
                   + (f"; the host tweaks: {', '.join(hosts)}" if hosts else "") + ")\n")
    out, err = run(lifted["exe"], SA2_TWEAKS="t", SA2_REPLAY_TICKS="/dev/null")
    assert out == "eax 1 esp 18004 ret 404242\n"
    assert err.startswith("sa2 tweaks: SA2_TWEAKS=t ignored in a replay")


def test_a_fixture_past_bit_32_runs_its_variant_and_no_other_bit_does(lifted):
    names = wide_names()
    assert run(lifted["wide"]) == ("eax 1 esp 18004 ret 404242\n", "")
    out, err = run(lifted["wide"], SA2_TWEAKS="t")
    assert out == "eax 2 esp 18004 ret 404242\n"
    assert err.splitlines() == ["sa2 tweaks: test tweak (SA2_TWEAKS=t)",
                                "sa2 tweaks: test tweak: the patched lift of 0x00401000 runs"]
    # Bit WIDE cut to 32 bits is bit WIDE - 32; bits 32 and 33 are the first a 32-bit mask lacks.
    # None of them, nor every other tweak at once, runs the variant. Once the checkout's list has
    # more than 32 tweaks those bits are real ones: a code-patch tweak, which this build does not
    # link, is one it was built without, so with it alone none is on; any other is on with its list
    # report.
    unlinked = {r["name"] for r in TS.patches(ROOT / "runtime")}
    said = {r["name"]: r["report"] for r in TS.rows(ROOT / "runtime")}
    for on in (names[WIDE - 32], names[32], names[33], ",".join(names)):
        out, err = run(lifted["wide"], SA2_TWEAKS=on)
        assert out == "eax 1 esp 18004 ret 404242\n", on
        assert "the patched lift" not in err and ("none on" in err) == (on in unlinked), (on, err)
    # The last padding tweak, which is past the checkout's list, reports "<name> on".
    pad = names[WIDE - 1]
    _, err = run(lifted["wide"], SA2_TWEAKS=f"{names[33]},{pad},t")
    first = [] if names[33] in unlinked else [said.get(names[33], names[33] + " on")]
    absent = (f'; this game was built without the code-patch tweak "{names[33]}", so it is ignored'
              if names[33] in unlinked else "")
    assert err.splitlines()[0] == (f"sa2 tweaks: {', '.join(first + [pad + ' on', 'test tweak'])} "
                                   f"(SA2_TWEAKS={names[33]},{pad},t{absent})")
    _, err = run(lifted["wide"], SA2_TWEAKS="?")
    hosts = [r["name"] for r in TS.host_rows(ROOT / "runtime")]
    assert err.rstrip("\n").endswith(f"; the tweaks: {', '.join([n for n in names if n not in unlinked] + ['t'])}"
                                      + (f"; the host tweaks: {', '.join(hosts)}" if hosts else "") + ")")
    assert run(lifted["wide"], SA2_TWEAKS="t", SA2_REPLAY_TICKS="/dev/null")[0] == "eax 1 esp 18004 ret 404242\n"


# ---------------------------------------------------------------- the tracked specs

def test_every_tracked_spec_and_fixture_passes_the_checks_on_the_lifted_image():
    """The shipped specs as one build, each fixture as its own."""
    w = V.load_world()
    assert (w.builtin, w.registered) == V.registrations()
    shipped = V.plan_all(w, [V.read_spec(p) for p in V.shipped_specs()])
    assert [p["tweak"] for p in shipped] == [r["name"] for r in TS.patches(ROOT / "runtime")]
    fixtures = {p["tweak"]: [r["entry"] for r in p["modules"]]
                for f in V.fixture_specs() for p in V.plan_all(w, [V.read_spec(f)])}
    assert fixtures == {"test-speedup-jingle": ["0x0046e000"]}


def test_linked_refuses_variants_built_for_other_objects(tmp_path):
    out, a, b = tmp_path / "out", tmp_path / "a", tmp_path / "b"
    for d, tag in ((a, "a"), (b, "b")):
        d.mkdir()
        (d / "summary.json").write_text(tag)
        (d / "build.jsonl").write_text(tag)
    out.mkdir()
    (out / V.C_FILE).write_text("/* c */\n")

    def record(tweaks, schema=V.SCHEMA):
        (out / V.RECORD).write_text(json.dumps({
            "schema": schema, "objects": {"dir": "a", "summary_sha256": V.sha256(a / "summary.json"),
                                          "build_jsonl_sha256": V.sha256(a / "build.jsonl")},
            "c": {"path": V.C_FILE, "sha256": V.sha256(out / V.C_FILE)}, "tweaks": tweaks, "wraps": [],
            "link_objects": []}))
    record([])
    assert V.linked(out, a)["wraps"] == []
    with pytest.raises(SystemExit, match="a different build"):
        V.linked(out, b)
    fixture = {"name": "test-speedup-jingle", "report": "r", "spec": f"{TS.FIXTURES}/test-speedup-jingle.json",
               "spec_sha256": "", "shipped": False, "modules": []}
    record([fixture])
    assert [(t["name"], t["shipped"]) for t in V.linked(out, a)["note"]["tweaks"]] == [("test-speedup-jingle", False)]
    record([dict(fixture, name="no-event-bars")])
    with pytest.raises(SystemExit, match="runtime/sa2_tweaks.h has a tweak of that name"):
        V.linked(out, a)
    record([dict(fixture, name="gone", spec=f"{TS.SPECS}/gone.json", shipped=True)])
    with pytest.raises(SystemExit, match="no longer registers it"):
        V.linked(out, a)
    record([], schema="sa2-s8-variants/v1")
    with pytest.raises(SystemExit, match="not sa2-s8-variants/v2"):
        V.linked(out, a)
    record([])
    (out / V.C_FILE).write_text("/* moved */\n")
    with pytest.raises(SystemExit, match="is not the file"):
        V.linked(out, a)
