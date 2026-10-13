#!/usr/bin/env python3
"""M7's synthetic streams: one stream per method no recorded run reaches, replayed in both players.

Subtitle: `source tools/env.sh && .venv/bin/python tools/s5/m7_synthetic.py --out DIR
--players BUILD --receipt OUT.json`

The user's second ruling of 2026-10-04
(`Documentation/decisions/2026-10-04-S5-user-rulings-2.md`): "M7's numerator: a method
counts only when the device answers it, the recorder captures it, and both players handle
it. Methods no recorded run reaches are exercised by a synthetic stream per method, so the
check can fail." `tools/s4/m7_inputs.py` measures that numerator from two receipts: current
recordings of the game with both hosts' replays of each (`s5-m7-production-v1`: Metal Harbor
and the release route, `runs`), and this tool's `s5-m7-synthetic-v1`, for the methods no
recorded run reaches (neither those recordings nor any run of
`Documentation/receipts/m7-recorded-runs.json`).

What it does, per target method (TARGETS, the families of
`tools/s5/tests/rec_harness_m7.c`):

1. builds the fixture once with the pinned zig: `tools/s5/tests/rec_harness_m7.c` (which
   includes `tools/s5/tests/rec_harness.c`'s stand-in runtime) with the real
   `runtime/sa2_d3d9.c`, `runtime/sa2_guest_call.c` and `host/sa2_present_rec.c`, linked
   with the presentation build's `-Wl,-wrap` list (`tools/s5/objcheck.py` WRAPS), its
   shaders assembled by `host/d3d9gpu/test/sm3_asm.py` into `m7_shaders.h`; every file the
   compile read (its depfile) is pinned by sha256;
2. records two streams of the target's family: the positive one, and `--omit`, the same
   scenario without the target's call (the falsifier);
3. reads each stream back with `tools/s5/stream_dump.py` and holds it to the recorder's own
   counts (compare_counts);
4. replays each stream in both players, `sa2_player` (DXVK) and `sa2_player_gpu` (SDL_gpu),
   at stage 3, hidden, with the pixels the harness wrote (`--expect`), through
   `tools/s5/compare_hosts.py` run_player, and runs the frame census the second host's draw
   account is held to;
5. evaluates the shared per-method predicate (`m7_inputs.case_problems`) on both streams;
   a target is held when its positive stream passes and its omit stream fails the same
   predicate, and, for a method with an effect on the picture (`m7_inputs.PICTURE_EFFECT`),
   fails it on both hosts' pixels;
6. writes the receipt: every file each case made, by path and sha256, and every file the
   fixture and the players were built from. No verdict is written: the gate derives it.

A target `m7_inputs.REFUSED_VARIANTS` names (Device Reset) also records each variant stream
(`--variant`, a Reset to other presentation parameters), which both players must refuse by
name: a positive stream whose call needs no host work cannot show the player checks it.

A receipt written under `Documentation/receipts/` pins every case file, so its cases must live
where nothing deletes them: such a receipt is refused unless `--out` is under PERMANENT_OUT
(EVIDENCE_ROOT, private/generated/s5/m7-evidence). A run records into a fresh `--out`: one whose
cases/ already holds a case is refused, so no file an earlier run left there (a report a
crashed player did not rewrite) is pinned as this run's.

A target that a registered recorded run reaches (`m7_inputs.runs_reaching`) is printed as not
held whatever its case shows: the ruling's synthetic stream is for a method no recorded run
reaches, and the gate derives it so.

No case directory may hold a file named counts.json: `m7_inputs.run_dir_named_by` would
register it as a recorded run of the game (the recorder's counts are rec.counts.json), and
this tool refuses to write a receipt over such a directory.

`--families` chooses which families run (default: every one). `ctabset` and `reset` hold
since host/player/player.cpp plans the five constant-table setters as having no effect of
their own (the nested Set*ShaderConstant records carry it) and checks Device Reset against
the creation parameters, refusing a Reset to any other (the other-pp variant). `cube` (F7)
and `volume` (F8) read not held until both players make cube and volume textures (their
creators are not handled in the players this was written against); their falsifier is the
recorder's count, and the harness checks the device's answers (rec_harness_m7.c), exiting 3
on one that is not Direct3D 9's.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s5/m7_synthetic.py --out DIR --players BUILD [--receipt OUT.json]
      [--families indexed,freq,...] [--targets NAME,...] [--evaluate-only]
  .venv/bin/python tools/s5/m7_synthetic.py refresh [--plan] [--record DIR] [--record-release DIR]
      [--regenerate] [--root DIR --production-out PATH --synthetic-out PATH]
  .venv/bin/python tools/s5/m7_synthetic.py record-release --out DIR --trace PATH [--build NAME]

refresh: make M7's three-way evidence again at the tree's sources
------------------------------------------------------------------

Subtitle: `source tools/env.sh && .venv/bin/python tools/s5/m7_synthetic.py refresh`

The three-way block reads measured only while every source its evidence was made from is the
tree's (`m7_inputs.stale_pins`), so a push that moves a pinned source (either player, the second
host, the recorder, the stream format, the runtime, play_report.py, the fixture's sources) makes
the evidence again in the same push. This is that one command (AGENTS.md rule 13). In order:

1. the recordings, one per production run (PRODUCTION_RUNS): Metal Harbor and the release route
   (`record-release` below), each `--record DIR` / `--record-release DIR`, else the
   content-addressed re-recording EVIDENCE_ROOT/record-<key>/<run> when it exists (key: the
   tree's recorder, stream format and runtime digest), else the recording the production
   receipt names for that run. When the recorder, the stream format or the runtime one was
   made from is not the tree's (`m7_inputs.recording_moved`, the same rule the block applies),
   nothing is reused and nothing runs: it prints M7_EVIDENCE_RERECORD, what moved, and the
   exact command that records that run again into EVIDENCE_ROOT/record-<key> (tools/s5/
   present_run.py for Metal Harbor, this tool's record-release for the release route), and
   exits 3. Run those commands, then this one again; it finds the new recordings by their key;
2. the players: both build scripts into EVIDENCE_ROOT/players-<key>, the key a sha256 of every
   file they can read (host/player, host/d3d9gpu, host/sa2_stream.h, host/dxvk_smoke.cpp,
   tools/pins.json) and of the install they link. A directory that already holds a finished
   build of that key is reused when compare_hosts.py's provenance still accepts it; otherwise
   both scripts build into it, and provenance must accept the result;
3. the synthetic cases (the applied families) into a fresh EVIDENCE_ROOT/synthetic-<UTC>-<key>;
4. each recording's stream replayed in both hosts by compare_hosts.py run (r2-draws-full, hidden,
   no controls) into a fresh EVIDENCE_ROOT/replay-<UTC>-<key>/<run>;
5. both evidence receipts written (default Documentation/receipts/s5-m7-production-v1.json and
   s5-m7-synthetic-v1.json), only after every step above finished, and the block they derive
   printed (M7_EVIDENCE measured, held N of M). The production receipt names every run in
   `runs` (production_runs_receipt), each block the single-run receipt
   m7_inputs.production_receipt writes for one recording, the contract of 2026-10-09 with
   tools/s4/m7_inputs.py: a method is held by production when any listed run's recorder counts
   it and both hosts' rows for that run pass the per-host predicate. `--regenerate` then runs
   tools/s4/m7_inputs.py, which the commit needs next (its M7_ROW_MOVED lines name what else the
   commit changes).

`--plan` prints every step and directory and runs nothing. A receipt under
Documentation/receipts/ needs `--root` under EVIDENCE_ROOT (the default); a trial (a unit's)
names all three of `--root`, `--production-out` and `--synthetic-out`, and is refused without
its own `--root`.

record-release: the second production run
------------------------------------------

Subtitle: `source tools/env.sh && .venv/bin/python tools/s5/m7_synthetic.py record-release
--out DIR --trace PATH`

IDirect3DVertexBuffer9::Release is reached by three registered recorded runs and by no Metal
Harbor recording, so no synthetic case may hold it: a production run that reaches it must. This
records one: the boot left at the title screen for 16000 polls with no key (the registered
recorded run RELEASE_REGISTERED's settings), built as present-m7release.<tree tag> with
present_run.py's boot hooks and the recorder, then held by present_run.py's checks that need no
registered replay and by the route check (the recorder counted the Release, every call
returned), and written as DIR/present-run.json with READY beside the stream only when every
check holds. Heavy (a game build and a run of about five minutes, a 12 GB stream): never during
a gate pass.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "host" / "d3d9gpu" / "test"))

import compare_hosts as CH  # noqa: E402
import m7_inputs as M  # noqa: E402
import objcheck as OC  # noqa: E402
import sm3_asm  # noqa: E402
import stream_dump as SD  # noqa: E402

SELF = "tools/s5/m7_synthetic.py"
HARNESS = "tools/s5/tests/rec_harness_m7.c"
ZIG = ROOT / "private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig"
COMPILE_SOURCES = (HARNESS, "runtime/sa2_d3d9.c", "runtime/sa2_guest_call.c", "host/sa2_present_rec.c")
# The recorder references the audio devices' table getters; the fixture has no audio device,
# so it links test_s5_stream.py's inert stand-ins (as host/player/test_synth.sh does).
AUDIO_GETTERS_FROM = "Documentation/scripts/tests/test_s5_stream.py"
# The tools this process runs that make the fixture's generated inputs and its link line:
# the shaders' assembler, the wrap list's owner, and this file.
GENERATORS = M.SYNTHETIC_GENERATORS
# Where the evidence a committed receipt pins lives: the players, replays, re-recordings and
# synthetic cases refresh makes, each in a directory of its own (a unit's scratch is deleted).
EVIDENCE_ROOT = "private/generated/s5/m7-evidence"
PERMANENT_OUT = EVIDENCE_ROOT
SCHEMA = "sa2-recomp-m7-synthetic/v1"
RECEIPT_ID = "s5-m7-synthetic-v1"
COMMAND = "source tools/env.sh && .venv/bin/python tools/s5/m7_synthetic.py"
HOSTS = ("dxvk", "sdlgpu")
APPLIED = ("indexed", "freq", "vsbool", "vsint", "psbool", "psint", "query", "ctabquery", "ctabset", "reset",
           "cube", "volume")
PREPARED = ()
CASE_FILES = ("stream.sa2s", "rec.counts.json", "answers.json", "expect.tsv", "census.json", "harness.exit")
HOST_FILES = {"dxvk": ("report.json", "check.json", "exit", CH.CHECKED_BY),
              "sdlgpu": ("report.json", "check.json", "exit", CH.CHECKED_BY, "d3d9gpu.json")}

# The shaders, in host/d3d9gpu/test/sm3_asm.py's text. VS_COLOR and PS_COLOR pass the
# vertex colour through; VS_INSTANCE adds a per-instance offset (TEXCOORD0, stream 1) to it; the bool and int shaders take their colour from b0 (set: green,
# else red) or from i0 iterations of a quarter of green; the ctab shaders show the one
# register the authored table's constant lives in (s c7, v c11, i i2, b b3, mr c20..c21).
SHADERS = {
    "M7_VS_INSTANCE": """vs_3_0
dcl_position v0
dcl_color v1
dcl_texcoord0 v2
dcl_position o0
dcl_color o1
mov r0, v0
add r0.xy, v0.xy, v2.xy
mov o0, r0
mov o1, v1
""",
    "M7_VS_COLOR": """vs_3_0
dcl_position v0
dcl_color v1
dcl_position o0
dcl_color o1
mov o0, v0
mov o1, v1
""",
    "M7_PS_COLOR": """ps_3_0
dcl_color v0
mov oC0, v0
""",
    "M7_VS_BOOL": """vs_3_0
dcl_position v0
dcl_position o0
dcl_color o1
def c1, 0.0, 1.0, 0.0, 1.0
def c2, 1.0, 0.0, 0.0, 1.0
mov o0, v0
if b0
mov r0, c1
else
mov r0, c2
endif
mov o1, r0
""",
    "M7_VS_INT": """vs_3_0
dcl_position v0
dcl_position o0
dcl_color o1
def c3, 0.0, 0.0, 0.0, 0.0
def c4, 0.0, 0.25, 0.0, 0.25
mov o0, v0
mov r0, c3
loop aL, i0
add r0, r0, c4
endloop
mov o1, r0
""",
    "M7_PS_BOOL": """ps_3_0
dcl_color v0
def c1, 0.0, 1.0, 0.0, 1.0
def c2, 1.0, 0.0, 0.0, 1.0
if b0
mov r0, c1
else
mov r0, c2
endif
mov oC0, r0
""",
    "M7_PS_INT": """ps_3_0
dcl_color v0
def c3, 0.0, 0.0, 0.0, 0.0
def c4, 0.0, 0.25, 0.0, 0.25
mov r0, c3
loop aL, i0
add r0, r0, c4
endloop
mov oC0, r0
""",
    "M7_PS_CTAB_FLOAT": """ps_3_0
dcl_color v0
mov r0, c7.x
mov oC0, r0
""",
    "M7_PS_CTAB_VECTOR": """ps_3_0
dcl_color v0
mov oC0, c11
""",
    "M7_PS_CTAB_INT": """ps_3_0
dcl_color v0
def c3, 0.0, 0.0, 0.0, 0.0
def c4, 0.0, 0.25, 0.0, 0.25
mov r0, c3
loop aL, i2
add r0, r0, c4
endloop
mov oC0, r0
""",
    "M7_PS_CTAB_BOOL": """ps_3_0
dcl_color v0
def c1, 0.0, 1.0, 0.0, 1.0
def c2, 1.0, 0.0, 0.0, 1.0
if b3
mov r0, c1
else
mov r0, c2
endif
mov oC0, r0
""",
    "M7_PS_CTAB_MATRIX": """ps_3_0
dcl_color v0
mov oC0, c21
""",
}


def sha256(path: Path) -> str:
    return M.sha256_file(path)


def rel(p: Path) -> str:
    p = Path(p).resolve()
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def shaders_header() -> str:
    out = ["/* Generated by tools/s5/m7_synthetic.py from its SHADERS with host/d3d9gpu/test/sm3_asm.py. */",
           "#include <stdint.h>"]
    for name, text in SHADERS.items():
        words = sm3_asm.assemble(text)
        n = len(words) // 4
        vals = [int.from_bytes(words[4 * i:4 * i + 4], "little") for i in range(n)]
        body = ", ".join(f"0x{v:08X}u" for v in vals)
        out.append(f"static const uint32_t {name}[{n}] = {{{body}}};")
    return "\n".join(out) + "\n"


def audio_getters(src: Path = ROOT) -> str:
    import ast
    tree = ast.parse((src / AUDIO_GETTERS_FROM).read_text())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "AUDIO_GETTERS_C" for t in n.targets))


def depfile_sources(text: str, build: Path, src: Path) -> tuple[list[str], list[str]]:
    """(every file under the source checkout `src` a zig cc depfile names, by its path there;
    every other file it names outside the build directory and the compiler's own tree)."""
    body = text.replace("\\\n", " ")
    _, _, deps = body.partition(":")
    inside, outside = set(), set()
    for token in shlex.split(deps):
        p = Path(token)
        if not p.is_absolute():
            p = (build / p)
        p = p.resolve()
        if p.is_relative_to(build.resolve()) or p.is_relative_to(ZIG.parent.resolve()):
            continue
        if p.is_relative_to(src.resolve()):
            inside.add(p.relative_to(src.resolve()).as_posix())
        else:
            outside.add(str(p))
    return sorted(inside), sorted(outside)


def build(out: Path, src: Path = ROOT) -> dict:
    """The fixture, built once into out/build from the C sources of the checkout `src` (this
    tree unless named: a `git archive` of a commit when the tree is mid-edit). Returns its
    build record: every file under `src` a compile read, by its path there and sha256, and
    the tools this process ran that made its generated inputs, by their path here."""
    if not ZIG.is_file():
        raise SystemExit(f"m7_synthetic: the pinned zig is not at {ZIG}")
    src = Path(src).resolve()
    b = out / "build"
    for d in (b, b / "zig-local", b / "zig-global", b / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    (b / "m7_shaders.h").write_text(shaders_header())
    (b / "audio_getters.c").write_text(audio_getters(src))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    env.update(ZIG_LOCAL_CACHE_DIR=str(b / "zig-local"), ZIG_GLOBAL_CACHE_DIR=str(b / "zig-global"),
               TMPDIR=str(b / "tmp"))
    binary = b / "rec_harness_m7"
    flags = ["-target", "x86_64-linux-musl", "-O1", "-Wall", "-Wextra", "-Werror",
             "-I" + str(src / "runtime"), "-I" + str(src / "host"), "-I" + str(b)]
    units = [src / s for s in COMPILE_SOURCES] + [b / "audio_getters.c"]
    before = {s: sha256(src / s) for s in COMPILE_SOURCES}
    log, objects, argvs = [], [], []
    for i, unit in enumerate(units):
        obj = b / f"unit{i}.o"
        argv = [str(ZIG), "cc", *flags, "-MD", "-MF", str(b / f"unit{i}.d"), "-c", str(unit), "-o", str(obj)]
        argvs.append(argv)
        p = subprocess.run(argv, env=env, capture_output=True, text=True)
        log.append(p.stdout + p.stderr)
        if p.returncode != 0:
            (b / "build.log").write_text("\n".join(log))
            raise SystemExit(f"m7_synthetic: {unit} did not compile:\n{p.stderr[-2000:]}")
        objects.append(str(obj))
    argv = [str(ZIG), "cc", "-target", "x86_64-linux-musl", *objects, *["-Wl,-wrap=" + w for w in OC.WRAPS],
            "-o", str(binary)]
    argvs.append(argv)
    p = subprocess.run(argv, env=env, capture_output=True, text=True)
    log.append(p.stdout + p.stderr)
    (b / "build.log").write_text("\n".join(log))
    if p.returncode != 0:
        raise SystemExit(f"m7_synthetic: the fixture did not link:\n{p.stderr[-2000:]}")
    if {s: sha256(src / s) for s in COMPILE_SOURCES} != before:
        raise SystemExit("m7_synthetic: a source changed while the fixture built; build again")
    # Every file under the checkout a compile read (its depfile), the named sources whatever
    # they say, and the file the audio stand-ins came from.
    deps, outside = set(COMPILE_SOURCES) | {AUDIO_GETTERS_FROM}, set()
    for i in range(len(units)):
        inside, other = depfile_sources((b / f"unit{i}.d").read_text(), b, src)
        deps |= set(inside)
        outside |= set(other)
    unread = sorted(set(M.SYNTHETIC_SOURCES) - deps)
    if unread:
        # m7_inputs reads the evidence as not measured unless these are pinned; a build that
        # does not read one of them is not the fixture this tool and the gate describe.
        raise SystemExit(f"m7_synthetic: the fixture's compile read none of {unread}")
    record = {
        "binary": {"path": rel(binary), "sha256": sha256(binary)},
        "source_root": rel(src),
        "argv": [[a.replace(str(src) + "/", "").replace(str(ROOT) + "/", "") for a in v] for v in argvs],
        "compiler": {"path": ZIG.relative_to(ROOT).as_posix(), "version": "0.15.2", "sha256": sha256(ZIG)},
        "sources": {s: sha256(src / s) for s in sorted(deps)},
        "external": {s: sha256(Path(s)) for s in sorted(outside)},
        "tools": {t: sha256(ROOT / t) for t in GENERATORS},
        "generated": {"m7_shaders.h": sha256(b / "m7_shaders.h"), "audio_getters.c": sha256(b / "audio_getters.c")},
    }
    (b / "harness.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def harness_targets(binary: Path) -> list[tuple[str, str, bool]]:
    p = subprocess.run([str(binary), "--list"], capture_output=True, text=True, check=True)
    return [(n, f, bool(prep)) for n, f, prep in json.loads(p.stdout)]


def record_case(binary: Path, target: str, mode: str, case: Path, variant: str = "") -> dict:
    """One stream of `target` (mode positive or omit) into `case`."""
    case.mkdir(parents=True, exist_ok=True)
    if (case / "counts.json").exists():
        raise SystemExit(f"m7_synthetic: {case / 'counts.json'} exists: a case directory holds no counts.json")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    env.update(SA2_REC_TRACE=str(case / "stream.sa2s"), SA2_REC_COUNTS=str(case / "rec.counts.json"),
               SA2_REC_META=json.dumps({"build": "rec_harness_m7", "target": target, "mode": mode,
                                        "variant": variant}, sort_keys=True))
    argv = [str(binary), "--target", target, "--out", str(case)] + (["--omit"] if mode == "omit" else []) \
        + (["--variant", variant] if variant else [])
    p = subprocess.run(argv, env=env, capture_output=True, text=True)
    (case / "harness.out").write_text(p.stdout + p.stderr)
    (case / "harness.exit").write_text(f"{p.returncode}\n")
    return {"exit": p.returncode}


def scan_case(case: Path) -> dict:
    """The stream read back and held to the recorder's own counts."""
    out: dict = {"problems": []}
    try:
        summary = SD.scan(case / "stream.sa2s")
    except (SD.StreamError, OSError) as e:
        return {"problems": [f"the stream does not read: {e}"]}
    counts = json.loads((case / "rec.counts.json").read_text())
    out["problems"] = list(summary.get("problems") or []) + SD.compare_counts(summary, counts)
    return out


def replay_case(players: Path, case: Path) -> None:
    for host in HOSTS:
        CH.run_player(players, case / "stream.sa2s", 3, case / host, sdlgpu=host == "sdlgpu", hidden=True,
                      expect=case / "expect.tsv")
    CH.run_census(players, case / "stream.sa2s", 3, case)


def case_files(case: Path) -> dict:
    files = {name: sha256(case / name) for name in CASE_FILES if (case / name).is_file()}
    hosts = {host: {"dir": rel(case / host),
                    "files": {name: sha256(case / host / name) for name in HOST_FILES[host]
                              if (case / host / name).is_file()}}
             for host in HOSTS}
    return {"dir": rel(case), "files": files, "hosts": hosts}


def players_record(players: Path) -> dict:
    out = {}
    for name in ("sa2_player.json", "sa2_player_gpu.json"):
        doc = json.loads((players / name).read_text())
        out[name] = {"path": rel(players / name), "sha256": sha256(players / name),
                     "binaries": doc["binaries"], "sources": doc["sources"]}
    return out


def counts_json_dirs(out: Path) -> list[str]:
    return sorted(rel(p.parent) for p in out.rglob("counts.json"))


def pipeline() -> dict:
    files = (SELF, HARNESS, "tools/s5/tests/rec_harness.c", "tools/s4/m7_inputs.py", "tools/s5/compare_hosts.py",
             "tools/s5/play_report.py", "tools/s5/stream_dump.py", "host/d3d9gpu/test/sm3_asm.py",
             "tools/s5/objcheck.py")
    return {f: sha256(ROOT / f) for f in files}


def receipt(out: Path, players: Path, harness: dict, cases: list[dict]) -> dict:
    return {
        "schema": SCHEMA,
        "id": RECEIPT_ID,
        "generated_by": SELF,
        "command": COMMAND + f" --out {rel(out)} --players {rel(players)} --source-root {harness.get('source_root')}",
        "written": now(),
        "note": ("M7's synthetic streams (the user's ruling of 2026-10-04: \"Methods no recorded run reaches are "
                 "exercised by a synthetic stream per method, so the check can fail.\"). One case per target "
                 "method: a positive stream and an omit stream of the same scenario without the target's call, "
                 "each recorded by the real recording device and recorder in tools/s5/tests/rec_harness_m7.c and "
                 "replayed in both players at stage 3 with the pixels the harness predicts. Pins only: "
                 "tools/s4/m7_inputs.py derives each target's verdict from these files with the predicate it "
                 "shares with the production evidence, and Documentation/scripts/check_m7_inputs.py holds the "
                 "derivation at every push. No case directory holds a counts.json, so no case is a recorded run."),
        "inputs": {"pipeline": pipeline()},
        "harness": harness,
        "players": {"build": rel(players), "records": players_record(players)},
        "cases": cases,
    }


def run_cases(out: Path, players: Path, *, families: list[str], targets: list[str] = (),
              evaluate_only: bool = False, source_root: Path = ROOT, receipt_path: Path | None = None,
              registry: dict | None = None) -> int:
    """Record, read back, replay and evaluate the chosen targets' cases into `out` (step 1-5 of
    the module's list), and write the receipt to `receipt_path` when given. 0 when done, 1 when
    a case directory holds a counts.json (no receipt is written then), 2 when `out` already holds
    cases and this is not --evaluate-only. `registry` is M7's registry of recorded runs (default:
    the tree's), whose `reached` makes a target one no synthetic case holds."""
    if not evaluate_only and (out / "cases").is_dir() and any((out / "cases").iterdir()):
        print(f"refused: {rel(out)}/cases already holds cases; a run records into a fresh --out, so no file an "
              f"earlier run left there is pinned as this run's", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    harness = json.loads((out / "build" / "harness.json").read_text()) if evaluate_only \
        else build(out, Path(source_root))
    binary = ROOT / harness["binary"]["path"]
    chosen = [t for t in harness_targets(binary) if t[1] in families and (not targets or t[0] in targets)]
    registry = registry if registry is not None else (M.cached_json(ROOT / M.REGISTRY) or {})
    cases, verdicts, reached = [], {}, {}
    for target, family, prepared in chosen:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", target).strip("-")
        entry = {"target": target, "family": family, "prepared": prepared}
        for mode in ("positive", "omit"):
            case = out / "cases" / slug / mode
            if not evaluate_only:
                record_case(binary, target, mode, case)
                (case / "scan.json").write_text(json.dumps(scan_case(case), indent=1) + "\n")
                replay_case(players, case)
            entry[mode] = case_files(case)
        for variant in sorted(M.REFUSED_VARIANTS.get(target, {})):
            case = out / "cases" / slug / variant
            if not evaluate_only:
                record_case(binary, target, "positive", case, variant=variant)
                (case / "scan.json").write_text(json.dumps(scan_case(case), indent=1) + "\n")
                replay_case(players, case)
            if case.is_dir():
                entry.setdefault("variants", {})[variant] = case_files(case)
        cases.append(entry)
        loaded = M.load_case(ROOT, entry, checker=sha256(ROOT / M.PLAY_REPORT))
        verdicts[target] = v = M.case_verdict(loaded, target)
        reached[target] = n = M.runs_reaching(registry, target)
        if n:
            print(f"{target}: not held -- {n} registered recorded runs reach it ({M.REGISTRY}), so no synthetic case "
                  f"holds it (its case {'passes' if v['held'] else 'does not pass either'})")
        else:
            print(f"{target}: {'HELD' if v['held'] else 'not held'}" + ("" if v["held"] else f" -- {v['why'][:200]}"))
    bad = counts_json_dirs(out)
    if bad:
        print(f"m7_synthetic: refused: these case directories hold a counts.json: {bad}", file=sys.stderr)
        return 1
    (out / "verdicts.json").write_text(json.dumps(verdicts, indent=1, sort_keys=True) + "\n")
    if receipt_path is not None:
        doc = receipt(out, players, harness, cases)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"-> {receipt_path}")
    held = sorted(t for t, v in verdicts.items() if v["held"] and not reached[t])
    print(f"M7_SYNTHETIC held={len(held)} of {len(verdicts)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["refresh"]:
        return refresh_main(argv[1:])
    if argv[:1] == ["record-release"]:
        return record_release_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="the cases' directory (under private/), fresh")
    ap.add_argument("--players", required=True, help="the build holding sa2_player, sa2_player_gpu and their records")
    ap.add_argument("--receipt", help="write the receipt here")
    ap.add_argument("--families", default=",".join(APPLIED))
    ap.add_argument("--targets", default="", help="only these targets (comma-separated M7 names)")
    ap.add_argument("--evaluate-only", action="store_true", help="evaluate cases already on disk")
    ap.add_argument("--source-root", default=str(ROOT),
                    help="the checkout whose C sources the fixture is built from (default: this tree; a "
                         "`git archive` of a commit when the tree is mid-edit)")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    players = Path(args.players).resolve()
    if (why := CH.refuse(out / "x")):
        print(f"refused: {why}", file=sys.stderr)
        return 2
    if args.receipt and Path(args.receipt).resolve().is_relative_to((ROOT / M.RECEIPTS_DIR).resolve()) \
            and not out.is_relative_to((ROOT / PERMANENT_OUT).resolve()):
        print(f"refused: a receipt under {M.RECEIPTS_DIR}/ pins every case file, so its cases go under "
              f"{PERMANENT_OUT}, not {rel(out)} (a unit's scratch directory is deleted)", file=sys.stderr)
        return 2
    families = [f for f in args.families.split(",") if f]
    unknown = sorted(set(families) - set(APPLIED) - set(PREPARED))
    if unknown:
        print(f"m7_synthetic: unknown families {unknown}", file=sys.stderr)
        return 2
    return run_cases(out, players, families=families, targets=[t for t in args.targets.split(",") if t],
                     evaluate_only=args.evaluate_only, source_root=Path(args.source_root),
                     receipt_path=Path(args.receipt) if args.receipt else None)


# ------------------------------------------------------------------ refresh

REFRESH = M.REFRESH
PRESENT_RUN = "tools/s5/present_run.py"
REPLAY = "metalharbor"
# The second production run (`release`): a recording of a route that reaches
# IDirect3DVertexBuffer9::Release, which Metal Harbor never calls and three registered recorded
# runs do, so no synthetic case may hold it (the user's ruling of 2026-10-04 and wave 1b's
# rule); record_release below makes it.
RELEASE = "release"
# The production runs refresh replays, in the order the production receipt lists them, each with
# what the plan and the re-recording messages call it.
PRODUCTION_RUNS = ((REPLAY, "the Metal Harbor recording"), (RELEASE, "the release route's recording"))
# What the two player builds can read in the tree (their build scripts' start sets).
PLAYER_TREES = ("host/player", "host/d3d9gpu")
PLAYER_FILES = ("host/sa2_stream.h", "host/dxvk_smoke.cpp", "tools/pins.json")
BUILD_SCRIPTS = ("host/player/build.sh", "host/d3d9gpu/build.sh")
BUILD_MARK = "m7-evidence-build.json"


class Stop(Exception):
    """Refresh cannot go on as the tree stands; the message says why and what to run."""


def install_dir() -> Path:
    return Path(os.environ.get("SA2_S5_INSTALL") or ROOT / "private/s5-tools/install")


def players_inputs(root: Path, install: Path) -> dict:
    """Every file the two player builds can read in the tree `root`, by sha256, and the install
    they link, by its physical path and the sha256 of every file under its lib/."""
    files = [root / f for f in PLAYER_FILES]
    for d in PLAYER_TREES:
        files += [q for q in (root / d).rglob("*") if q.is_file() and "__pycache__" not in q.parts]
    real = install.resolve()
    lib = real / "lib"
    return {"sources": {q.relative_to(root).as_posix(): sha256(q) for q in sorted(files) if q.is_file()},
            "install": {"path": str(real),
                        "lib": {q.relative_to(real).as_posix(): sha256(q)
                                for q in sorted(lib.rglob("*")) if q.is_file()} if lib.is_dir() else {}}}


def content_key(doc: dict) -> str:
    return M.sha256_bytes(M.canonical(doc).encode())[:16]


def recording_key(root: Path, runtime_digest: str | None) -> str:
    """The re-recording's directory key: the tree's recorder, stream format and runtime digest,
    the sources a recording's currency is held to (m7_inputs.recording_moved)."""
    return content_key({"recorder": M.cached_sha256(root / M.RECORDER),
                        "stream_h": M.cached_sha256(root / "host/sa2_stream.h"), "runtime": runtime_digest})


def rerecord_command(evidence: Path, key: str, name: str = REPLAY) -> str:
    """The command that makes production run `name` again into EVIDENCE_ROOT/record-<key>: Metal
    Harbor by tools/s5/present_run.py, the release route by this tool's record-release."""
    base = evidence / f"record-{key}"
    if name == RELEASE:
        return (f"{COMMAND} record-release --out {rel(base / RELEASE)} "
                f"--trace {rel(base / 'trace' / (RELEASE + '.sa2s'))}")
    return (f"source tools/env.sh && .venv/bin/python {PRESENT_RUN} --replay {REPLAY} "
            f"--out {rel(base / REPLAY)} --trace {rel(base / 'trace' / (REPLAY + '.sa2s'))}")


def receipt_run_dir(doc: dict, name: str) -> str | None:
    """The recording directory a production receipt names for production run `name`: its
    `runs` entry of that name, or, in the single-run form a receipt had before the release
    route, its one `recording` (Metal Harbor's)."""
    if isinstance(doc.get("runs"), list):
        return next(((r.get("recording") or {}).get("dir") for r in doc["runs"]
                     if isinstance(r, dict) and r.get("name") == name), None)
    return (doc.get("recording") or {}).get("dir") if name == REPLAY else None


def choose_recording(record: str | None, evidence: Path, key: str, production: Path,
                     name: str = REPLAY) -> tuple[Path | None, str]:
    """(production run `name`'s recording directory, where it came from): the one named on the
    command line, else the content-addressed re-recording when it exists, else the recording
    the production receipt names for it."""
    if record:
        return (ROOT / record).resolve(), ("--record" if name == REPLAY else f"--record-{name}")
    mine = evidence / f"record-{key}" / name
    if (mine / "present-run.json").is_file():
        return mine, f"the re-recording of this tree's recorder and runtime ({rel(mine)})"
    d = receipt_run_dir(M.cached_json(production) or {}, name)
    if d:
        return ROOT / d, f"the recording {rel(production)} names"
    return None, f"no recording: {rel(production)} names none and {rel(mine)} does not exist"


def run_identity(name: str | None) -> dict:
    """What a production run's present-run.json says it is: Metal Harbor's is present_run.py's
    receipt of that replay, the release route's this tool's record-release receipt."""
    return {REPLAY: {"schema": "sa2-present-run/v3", "replay": REPLAY},
            RELEASE: {"schema": RELEASE_SCHEMA, "route": RELEASE}}.get(name) or {}


def recording_problems(record: Path, digest: str | None,
                       name: str | None = None) -> tuple[list[str], Path | None]:
    """(why the recording cannot be replayed as the tree's, its stream): a run that is not whole,
    a stream that is gone, any source it was made from that the tree no longer holds, or (given
    the production run's name) a recording of another run filed under that one."""
    present = M.cached_json(record / "present-run.json")
    if not isinstance(present, dict):
        return [f"{rel(record)}/present-run.json is absent or not JSON"], None
    why = []
    for key, want in run_identity(name).items():
        if present.get(key) != want:
            why.append(f"{rel(record)}/present-run.json reads {key} {present.get(key)!r}, not "
                       f"{want!r}: it is not a recording of the {name} run")
    if present.get("ok") is not True:
        why.append(f"{rel(record)}/present-run.json reads ok {present.get('ok')!r}: the recording is not whole")
    if not isinstance((present.get("ready") or {}).get("stream_sha256"), str):
        why.append(f"{rel(record)}/present-run.json names no READY stream")
    stream = (present.get("ready") or {}).get("stream")
    stream_p = (ROOT / stream) if isinstance(stream, str) else None
    if stream_p is None or not stream_p.is_file():
        why.append(f"its stream {stream} is absent")
    for what, was, now_ in M.recording_moved(ROOT, present.get("current") or {}, digest):
        why.append(f"moved: {what}: the recording was made at {was}, the tree's is {now_}")
    return why, stream_p


def build_players(players: Path, install: Path, inputs: dict, key: str) -> str:
    """Both players in `players`: reused when it holds a finished build of `key` that
    compare_hosts.py's provenance accepts, else built into it by both scripts. Returns what it
    did; raises Stop when the build fails or provenance refuses it."""
    mark = M.cached_json(players / BUILD_MARK) or {}
    if mark.get("key") == key:
        prov = CH.provenance(players, CH.pipeline())
        if prov["pass"]:
            return f"reused {rel(players)} (a finished build of {key}, provenance accepts it)"
    players.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("SA2_REPO", "SA2_STREAM_H_DIR", "OUT", "SA2_S5_INSTALL")}
    env.update(OUT=str(players), SA2_REPO=str(ROOT), SA2_S5_INSTALL=str(install))
    for script in BUILD_SCRIPTS:
        log = players / f"{Path(script).parent.name}-build.log"
        with log.open("w") as fh:
            p = subprocess.run(["bash", str(ROOT / script)], env=env, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            raise Stop(f"{script} exited {p.returncode} (its log: {rel(log)})")
    prov = CH.provenance(players, CH.pipeline())
    if not prov["pass"]:
        raise Stop(f"compare_hosts.py's provenance refuses the build in {rel(players)}: " + "; ".join(prov["failures"][:6]))
    if content_key(players_inputs(ROOT, install)) != key:
        raise Stop("a player source or the install changed while the players built; run refresh again")
    (players / BUILD_MARK).write_text(json.dumps({"key": key, "inputs": inputs, "written": now(),
                                                  "by": REFRESH}, indent=1, sort_keys=True) + "\n")
    return f"built {rel(players)}"


def replay_recording(players: Path, stream: Path, out_dir: Path) -> Path:
    """The stream replayed in both hosts by compare_hosts.py run into `out_dir` (new). Its
    verdict is the R2 comparison's and is printed; what M7 holds is derived from the two runs."""
    log = out_dir.with_name(out_dir.name + ".log")
    argv = [sys.executable, str(ROOT / "tools/s5/compare_hosts.py"), "run", "--stream", str(stream), "--stage", "3",
            "--scope", "r2-draws-full", "--build", str(players), "--hidden", "--out", str(out_dir)]
    with log.open("w") as fh:
        p = subprocess.run(argv, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    missing = [n for n in ("census.json", "dxvk/report.json", "sdlgpu/report.json") if not (out_dir / n).is_file()]
    if p.returncode not in (0, 1) or missing:
        raise Stop(f"compare_hosts.py run exited {p.returncode}" + (f" and left no {', '.join(missing)}" if missing else "")
                   + f" (its log: {rel(log)})")
    verdict = (M.cached_json(out_dir / "compare.json") or {}).get("verdict")
    print(f"  compare_hosts verdict {verdict} (the R2 comparison's; M7 derives its own from the two runs)")
    return out_dir


PRODUCTION_NOTE = (
    "The three-way numerator's production evidence (the user's ruling of 2026-10-04: \"a method counts only when "
    "the device answers it, the recorder captures it, and both players handle it\"): each run a current recording "
    "of the game and both hosts' replays of its stream, by path and sha256 -- Metal Harbor, and the release route, "
    "which reaches IDirect3DVertexBuffer9::Release. A method is held by production when any listed run's recorder "
    "counts it and both hosts' rows for that run pass the per-host predicate. Pins only: tools/s4/m7_inputs.py "
    "derives which methods it holds, and Documentation/scripts/check_m7_inputs.py holds that derivation at every "
    "push; it reads not measured once any source a recording or either player was made from is not the tree's.")


def production_runs_receipt(replayed: list[tuple[str, Path, Path]], out: str) -> dict:
    """The production receipt for several production runs (the contract of 2026-10-09 between
    this tool and tools/s4/m7_inputs.py): `runs`, each block shaped as the single-run receipt
    m7_inputs.production_receipt writes for one recording (its `recording` with the recorder's
    counts.json and present-run.json, `census`, both `hosts`' reports, `players`), with the
    run's `name` and the command that pins it; the rest of the receipt is that function's
    (schema, id, pipeline). (`replayed`: (name, its recording's directory, its compare_hosts.py
    run directory) per run, in PRODUCTION_RUNS order.) Pins only."""
    if not replayed:
        raise Stop("no production run to write")
    blocks = []
    for name, record, replay in replayed:
        one = M.production_receipt(ROOT, rel(record), rel(replay / "dxvk"), rel(replay / "sdlgpu"),
                                   rel(replay / "census.json"), out=out)
        blocks.append({"name": name, "command": one["command"],
                       **{k: one[k] for k in ("recording", "census", "hosts", "players")}})
        head = {k: v for k, v in one.items() if k not in ("recording", "census", "hosts", "players", "command")}
    builds = {b["players"]["build"] for b in blocks}
    if len(builds) != 1:
        raise Stop(f"the production runs were replayed by players from {sorted(builds)}, not one build")
    return {**head, "command": REFRESH, "assembled_by": SELF, "note": PRODUCTION_NOTE, "runs": blocks}


def outputs_problem(evidence: Path, production: Path, synthetic: Path, explicit: bool, root_named: bool) -> str | None:
    """Why refresh refuses these outputs: one receipt tracked and the other not; a tracked receipt
    pinning files outside EVIDENCE_ROOT; a trial (receipts elsewhere) that names no --root, whose
    evidence would land beside the tracked receipts' own; an output git would hold."""
    receipts = (ROOT / M.RECEIPTS_DIR).resolve()
    tracked = [p for p in (production, synthetic) if p.is_relative_to(receipts)]
    if explicit and len(tracked) == 1:
        return "refused: write both receipts to Documentation/receipts/ or neither, so the two always describe one refresh"
    if not tracked and not root_named:
        return (f"refused: a trial (receipts outside {M.RECEIPTS_DIR}/) names its own --root, so its evidence never "
                f"lands under {EVIDENCE_ROOT}, which only the tracked receipts' evidence holds")
    if tracked and not evidence.is_relative_to((ROOT / EVIDENCE_ROOT).resolve()):
        return (f"refused: {', '.join(rel(p) for p in tracked)} would pin files under {rel(evidence)}; a receipt under "
                f"{M.RECEIPTS_DIR}/ pins files under {EVIDENCE_ROOT} only (a unit's scratch directory is deleted)")
    for p in (evidence / "x", production, synthetic):
        if not p.is_relative_to(receipts) and (why := CH.refuse(p)):
            return f"refused: {why}"
    return None


def refresh(args: argparse.Namespace) -> int:
    evidence = Path(args.root).resolve() if args.root else (ROOT / EVIDENCE_ROOT).resolve()
    production = (ROOT / args.production_out).resolve() if args.production_out else (ROOT / M.PRODUCTION).resolve()
    synthetic = (ROOT / args.synthetic_out).resolve() if args.synthetic_out else (ROOT / M.SYNTHETIC).resolve()
    if (why := outputs_problem(evidence, production, synthetic, bool(args.production_out or args.synthetic_out),
                               bool(args.root))):
        print(why, file=sys.stderr)
        return 2
    digest = M.tree_runtime_digest(ROOT)
    rkey = recording_key(ROOT, digest)
    chosen = {REPLAY: args.record, RELEASE: args.record_release}
    runs: list[tuple[str, Path, Path]] = []
    sources: dict[str, str] = {}
    stale = False
    for name, what in PRODUCTION_RUNS:
        record, source = choose_recording(chosen[name], evidence, rkey, ROOT / M.PRODUCTION, name)
        sources[name] = source
        why, stream = recording_problems(record, digest, name) if record is not None else ([source], None)
        if not why:
            runs.append((name, record, stream))
            continue
        stale = True
        if record is None:
            print(f"M7_EVIDENCE_RERECORD {what}: {source}.")
        else:
            print(f"M7_EVIDENCE_RERECORD {what} {rel(record)} ({source}) is not one this tree can replay as its "
                  f"own, so it is not replayed:")
            for w in why:
                print(f"  {w}")
        print(f"It would record {'Metal Harbor' if name == REPLAY else 'the release route'} again with this "
              f"tree's recorder and runtime:")
        print(f"  {rerecord_command(evidence, rkey, name)}")
        print(f"and then make the evidence from that recording with this command again (it finds "
              f"{rel(evidence / f'record-{rkey}' / name)} by its key).")
    if stale:
        return 3
    install = install_dir()
    inputs = players_inputs(ROOT, install)
    key = content_key(inputs)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    players = evidence / f"players-{key}"
    syn_dir = evidence / f"synthetic-{stamp}-{key}"
    replay_dir = evidence / f"replay-{stamp}-{key}"
    print(f"M7_EVIDENCE plan (tree runtime digest {digest}):")
    for name, record, stream in runs:
        print(f"  recording  {rel(record)} ({name}: {sources[name]}), stream {rel(stream)}")
    mark = M.cached_json(players / BUILD_MARK) or {}
    print(f"  players    {rel(players)} ({'a finished build of this key; reused if provenance accepts it' if mark.get('key') == key else 'built by ' + ' and '.join(BUILD_SCRIPTS)})")
    print(f"  synthetic  {rel(syn_dir)} (families {','.join(APPLIED)})")
    print(f"  replay     {rel(replay_dir)}/<run> for {', '.join(n for n, _, _ in runs)} "
          f"(compare_hosts.py run --scope r2-draws-full --hidden)")
    print(f"  receipts   {rel(production)}, {rel(synthetic)}")
    if args.plan:
        return 0
    try:
        print("  " + build_players(players, install, inputs, key))
        syn_receipt = syn_dir / "s5-m7-synthetic.json"
        rc = run_cases(syn_dir, players, families=list(APPLIED), receipt_path=syn_receipt)
        if rc != 0:
            raise Stop(f"the synthetic cases did not finish (m7_synthetic exited {rc})")
        replay_dir.mkdir(parents=True, exist_ok=True)
        replayed = [(name, record, replay_recording(players, stream, replay_dir / name))
                    for name, record, stream in runs]
        prod_doc = production_runs_receipt(replayed, out=rel(production))
    except (Stop, M.Refused, SystemExit, subprocess.SubprocessError, RuntimeError, OSError) as e:
        # SystemExit: the fixture's build (build()) refuses that way; RuntimeError: a census that
        # failed (compare_hosts.run_census); SubprocessError: a player past its timeout.
        print(f"M7_EVIDENCE_STOPPED {e}; no receipt was written", file=sys.stderr)
        return 1
    production.parent.mkdir(parents=True, exist_ok=True)
    M.write_json(production, prod_doc)
    synthetic.parent.mkdir(parents=True, exist_ok=True)
    synthetic.write_bytes(syn_receipt.read_bytes())
    print(f"-> {rel(production)}, {rel(synthetic)}")
    try:
        inp, _ = M.load_inputs(ROOT, evidence=(rel(production), rel(synthetic)))
        block = M.three_way_numerator(inp)
        called = M.called_and_implemented(inp)[0]
    except (M.Refused, OSError, ValueError, KeyError) as e:
        print(f"M7_EVIDENCE written; the block could not be derived here ({e})")
        return 0
    if block.get("measured") is True:
        print(f"M7_EVIDENCE measured, held {len(block['held_methods'])} of {len(called)} "
              f"({len(block['held_by']['production'])} by the recording, {len(block['held_by']['synthetic'])} synthetic), "
              f"against M7's inputs as they stand; tools/s4/m7_inputs.py's regeneration is the measure")
    else:
        print(f"M7_EVIDENCE not measured: {str(block.get('why_not_measured'))[:600]}")
    if args.regenerate:
        return subprocess.run([sys.executable, str(ROOT / "tools/s4/m7_inputs.py")], cwd=ROOT).returncode
    print(f"next: {M.COMMAND}")
    return 0


# ------------------------------------------------------------------ record-release

# The release route: the boot left at the title screen for RELEASE_POLLS polls with no key,
# which plays on into the attract demo and back, and so releases vertex buffers: the
# registered recorded run RELEASE_REGISTERED (the NEW FILE investigation's idle run, made by
# private/generated/s5/newfile/threads/run.py with the tracked tick hook and recorder and a
# thread-census hook of its own) counted 1701 IDirect3DVertexBuffer9::Release calls, and Metal
# Harbor none. Its settings are that run's: the reach configuration (the boot replay's
# environment, tools/s4/reach_receipt.py ENV) with the poll, import and virtual-time budgets the
# runner derives from 16000 polls, the registered boot capture's device profile, the pinned
# saves reset, a keyboard script that holds no key, and the probe's 2400 s alarm; the census
# hook is left out (it observes the scheduler and changes nothing the guest sees).
RELEASE_REGISTERED = "private/generated/s5/ship3/threads-runs/idle-16000"
RELEASE_POLLS = 16000
RELEASE_ALARM_S = 2400
RELEASE_LOOP_TIMEOUT_S = 3600
RELEASE_BUILD = "present-m7release"
RELEASE_SCHEMA = "sa2-m7-release-route/v1"
RELEASE_REACHES = "IDirect3DVertexBuffer9::Release"
RELEASE_SCRIPT = "# control: the host keyboard is the script source, and the script holds no key\n"


def release_paths(out: Path, trace: Path) -> dict:
    """present_run.py's paths for a run into `out` with its stream at `trace` (the partial
    stream beside it until the run holds), and the input script and log the route adds."""
    import present_run as PR
    p = PR.paths(out, trace)
    p.update(script=out / "script.txt", input_log=out / "input.log")
    return p


def release_command(build: str, p: dict, profile_env: dict, meta: dict) -> tuple[list[str], str]:
    """loop_run's command for the release route and its --run spec: present_run.py's boot
    command (the tick hook, the recorder and its wraps, --dump-imports) with the route's
    settings and alarm."""
    import present_run as PR
    TR, RR = PR.TR, PR.RR
    env = dict(RR.ENV)
    env.update({"SA2_PUMP_POLLS": str(RELEASE_POLLS),
                "SA2_IMPORT_BUDGET": str(max(8_000_000, RELEASE_POLLS * 5000)),
                "SA2_VIRTUAL_BUDGET_MS": str(max(30000, RELEASE_POLLS * 20))})
    env.update(profile_env)
    env.update({"SA2_VFS_WRITE_ROOT": rel(p["overlay"]), "SA2_REPLAY_TICKS": rel(p["ticks"]),
                "SA2_STAGE_TRACE": rel(p["stage_trace"]), "SA2_REPLAY_WORDS": rel(p["words"]),
                "SA2_REC_TRACE": rel(p["partial"]), "SA2_REC_COUNTS": rel(p["counts"]),
                "SA2_REC_META": json.dumps(meta, separators=(",", ":"), sort_keys=True),
                "SA2_INPUT_DI_SCRIPT": rel(p["script"]), "SA2_INPUT_DI_LOG": rel(p["input_log"])})
    items = [RELEASE] + [f"{k}={v}" for k, v in env.items()]
    for it in items[1:]:
        if "|" in it:
            raise SystemExit(f"a run setting carries `|`, loop_run's separator: {it!r}")
    spec = "|".join(items)
    cmd = [str(TR.PY), str(ROOT / "tools/s4/loop_run.py"), "--tag", RR.TAG, "--main", RR.MAIN, "--name", build,
           "--hook", TR.HOOK, "--wrap", TR.WRAP, "--hook", PR.RECORDER]
    for w in PR.WRAPS:
        cmd += ["--wrap", w]
    cmd += ["--out-prefix", build, "--timeout", str(RELEASE_LOOP_TIMEOUT_S), "--run", spec]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--timeout", f"--probe-arg={RELEASE_ALARM_S}",
            "--probe-arg=--dump-imports", f"--probe-arg={rel(p['imports'])}"]
    return cmd, spec


def release_route_check(counts: dict | None, registered: dict | None) -> dict:
    """The run reached what the route is for, and how its recorder's counts compare with the
    registered run it re-records, per method: the route holds when the recorder counted
    calls of RELEASE_REACHES, every one returned. The differences from the registered run,
    whose runtime was an earlier tree's, are kept beside it and do not decide it."""
    rows = M.recorder_rows(counts)
    row = rows.get(RELEASE_REACHES) or {}
    calls = row.get("calls") if isinstance(row.get("calls"), int) else 0
    theirs = M.recorder_rows(registered)
    differs = sorted(f"{n}: {(rows.get(n) or {}).get('calls')} vs {(theirs.get(n) or {}).get('calls')}"
                     for n in set(rows) | set(theirs)
                     if (rows.get(n) or {}).get("calls") != (theirs.get(n) or {}).get("calls"))
    return {"reaches": RELEASE_REACHES, "calls": calls, "entered": row.get("entered"),
            "registered_run": RELEASE_REGISTERED, "registered_counts_read": registered is not None,
            "differs_from_registered": differs,
            "ok": bool(calls and calls > 0 and row.get("entered") == calls)}


# What the run's record must show of its keyboard for the route's one host source, the script
# that holds no key: it answered nothing, held nothing and wrote nothing (harness/crt_probe.c's
# dinput.host_input block). present_run.py's configuration check refuses any host source, which
# a registered replay never has; this route's registered run had this one.
RELEASE_INERT_INPUT = {"source": "script", "answers_from_host": 0, "polls_held": 0, "keys_written": 0,
                       "script_lines": 0, "records": 0}


def release_configuration(run_rec: dict, profile, profile_env: dict) -> dict:
    """present_run.py's configuration check (the device profile the run read, every device
    switch on, no host keyboard), with the one host source the route sets accepted only when the
    record shows it inert (RELEASE_INERT_INPUT)."""
    import present_run as PR
    out = PR.check_configuration(run_rec, profile, profile_env)
    host = (run_rec.get("dinput") or {}).get("host_input") if isinstance(run_rec.get("dinput"), dict) else None
    inert = isinstance(host, dict) and all(host.get(k) == v for k, v in RELEASE_INERT_INPUT.items())
    kept = [x for x in out["problems"] if not (inert and x.startswith("the run's record reads dinput.host_input"))]
    return {"problems": kept, "host_input": host, "host_input_inert": inert, "ok": bool(run_rec) and not kept}


def record_release(out: Path, trace: Path, build: str, keep_failed: bool = False) -> dict:
    """The release route recorded into `out`, its stream at `trace`: build and run (the saves
    reset first), the checks, and present-run.json, the recording's own record, which
    tools/s4/m7_inputs.py reads as it reads present_run.py's (ok, the counts and stream checks,
    the READY stream's sha256, and the recorder, stream format and runtime it was made with).
    present_run.py's checks hold it where they do not need a registered replay: the build
    (fresh hooks, the recorder object the tree's), the run's configuration (the device profile,
    every switch on, the keyboard script that holds no key inert: release_configuration), the
    recorder's counts against the run's own record and import table, the
    stream whole and true to those counts and to the run, and READY written only when every
    check holds. A registered replay's own equalities (its tick capture, reach, record, word
    log) have nothing to hold this run to; the route check holds what it is for."""
    import present_run as PR
    TR, RR, SS = PR.TR, PR.RR, PR.SS
    why = PR.build_name_problem(build)
    if why:
        raise SystemExit(why)
    p = release_paths(out, trace)
    profile = TR.native_device_profile(PR.registered("boot")["entry"])
    profile_env = RR.profile_env(profile)
    for k in ("ticks", "words", "words_zst", "counts", "counts_status", "imports", "run", "matrix", "log",
              "receipt", "partial", "partial_status", "stage_trace", "input_log"):
        if p.get(k) is not None:
            Path(p[k]).unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    trace.parent.mkdir(parents=True, exist_ok=True)
    p["overlay"].mkdir(parents=True, exist_ok=True)
    p["script"].write_text(RELEASE_SCRIPT)
    forgotten = PR.forget_hook_objects(build)
    def made_from() -> dict:
        return {"commit": PR.git_head(), "recorder_sha256": sha256(ROOT / PR.RECORDER),
                "stream_h_sha256": sha256(ROOT / PR.STREAM_H), "runner_sha256": sha256(ROOT / SELF),
                "runtime_sha256": RR.runtime_digest(), "tick_hook_sha256": sha256(ROOT / TR.HOOK)}
    current = made_from()
    run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}-{os.urandom(4).hex()}"
    meta = {"build": build, "route": RELEASE, "run": run_id, **current}
    cmd, spec = release_command(build, p, profile_env, meta)
    saves = SS.reset_lifted(cmd, ROOT)
    SS.require_empty(saves)
    t0 = dt.datetime.now(dt.timezone.utc)
    try:
        done = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              timeout=RELEASE_LOOP_TIMEOUT_S + 900, env=PR.clean_env())
    except subprocess.TimeoutExpired as e:
        # A run past its timeout is a failed recording: its receipt is written and its partial
        # stream settled (deleted, or kept under --keep-failed) like any other failed run's.
        def text(b) -> str:
            return b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")
        done = subprocess.CompletedProcess(cmd, None, text(e.stdout),
                                           text(e.stderr) + f"\nrecord-release: the run passed its "
                                           f"{RELEASE_LOOP_TIMEOUT_S + 900} s timeout and was killed\n")
    seconds = round((dt.datetime.now(dt.timezone.utc) - t0).total_seconds(), 2)
    p["log"].write_text(f"$ {' '.join(cmd)}\n--- stdout\n{done.stdout}\n--- stderr\n{done.stderr}")
    # loop_run names the run's record by the build and the --run's name (release_command's spec).
    for src, dst in ((PR.RUN_DIR / f"{build}.{RELEASE}.json", p["run"]), (PR.RUN_DIR / f"{build}.matrix.json", p["matrix"])):
        if src.is_file():
            os.replace(src, dst)
    run_rec = M.cached_json(p["run"]) or {}
    receipt: dict = {
        "schema": RELEASE_SCHEMA, "generated_by": SELF, "route": RELEASE, "build": build,
        "command": f"{COMMAND} record-release --out {rel(out)} --trace {rel(trace)}",
        "written": now(), "current": current,
        "run": {"command": cmd, "spec": spec, "saves": saves, "rc": done.returncode, "seconds": seconds,
                "meta": meta, "hook_objects_deleted": forgotten,
                "profile": {"path": profile.get("path"), "sha256": profile.get("sha256")}
                if isinstance(profile, dict) else profile},
        "build_check": PR.check_build(build, p["matrix"]),
    }
    rows = (M.cached_json(p["matrix"]) or {}).get("rows") or []
    receipt["checks"] = {
        "exit": {"rc": done.returncode, "matrix_row_ok": rows[0].get("ok") if rows else None,
                 "ok": done.returncode == 0 and bool(rows) and rows[0].get("ok") is True},
        "configuration": release_configuration(run_rec, profile, profile_env),
        # present_run's counts check, its registered-run clause held to the run itself: the
        # route's registered run is compared in `route`.
        "counts": PR.check_counts(p, run_rec, {"run": run_rec}),
        "stream": PR.check_stream(p, p["partial"], run_rec, meta),
        "route": release_route_check(M.cached_json(p["counts"]),
                                     M.cached_json(ROOT / RELEASE_REGISTERED / "counts.json")),
    }
    # The sources `current` names, read again after the build and the run: a tree edited while
    # the run was built would leave a record naming sources that did not make it.
    after = made_from()
    receipt["checks"]["sources"] = {"moved": sorted(k for k in current if k != "commit" and after[k] != current[k]),
                                    "ok": all(after[k] == current[k] for k in current if k != "commit")}
    receipt["ok"] = bool(receipt["build_check"].get("ok") and all(c.get("ok") for c in receipt["checks"].values()))
    if p["words"].is_file():
        z = subprocess.run(["zstd", "-3", "-q", "-f", "--rm", str(p["words"]), "-o", str(p["words_zst"])],
                           capture_output=True, text=True)
        receipt["words_kept"] = {"path": rel(p["words_zst"]), "rc": z.returncode}
    PR.settle(receipt, p, trace, p["partial"], keep_failed)
    p["receipt"].write_text(json.dumps(receipt, indent=1, default=str) + "\n")
    return receipt


def record_release_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="m7_synthetic.py record-release",
                                 description="record the release route (M7's second production run)")
    ap.add_argument("--out", required=True, help="the recording's directory (present-run.json goes here)")
    ap.add_argument("--trace", required=True, help="the stream's path")
    ap.add_argument("--build", default=None, help=f"default {RELEASE_BUILD}.<tree tag>")
    ap.add_argument("--keep-failed", action="store_true",
                    help="keep a failed run's stream as <trace>.<tag>.failed (present_run.py's rule)")
    args = ap.parse_args(argv)
    import present_run as PR
    out, trace = Path(args.out).resolve(), Path(args.trace).resolve()
    for path in (out / "x", trace):
        if (why := CH.refuse(path)):
            print(f"refused: {why}", file=sys.stderr)
            return 2
    receipt = record_release(out, trace, args.build or f"{RELEASE_BUILD}.{PR.tree_tag()}", args.keep_failed)
    for k, c in [("build", receipt["build_check"]), *receipt["checks"].items()]:
        body = {x: y for x, y in c.items() if x in ("rc", "ok", "why", "problems", "tied", "counts_mismatch",
                                                    "per_method_vs_run", "calls", "differs_from_registered",
                                                    "held_to_run", "end", "frames", "records")}
        print(f"{'ok  ' if c.get('ok') else 'FAIL'} {k}: {json.dumps(body, default=str)[:900]}")
    print(f"receipt {rel(out / 'present-run.json')}; READY {receipt.get('ready')}; "
          f"{'OK' if receipt['ok'] else 'NOT OK'}")
    return 0 if receipt["ok"] else 1


def refresh_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="m7_synthetic.py refresh",
                                 description="make M7's three-way evidence again at the tree's sources")
    ap.add_argument("--plan", action="store_true", help="print every step and directory; run nothing")
    ap.add_argument("--record", help="the Metal Harbor recording to replay (its directory, holding present-run.json)")
    ap.add_argument("--record-release", help="the release route's recording to replay (record-release's --out)")
    ap.add_argument("--root", help=f"where the evidence goes (default {EVIDENCE_ROOT})")
    ap.add_argument("--production-out", help=f"the production receipt (default {M.PRODUCTION})")
    ap.add_argument("--synthetic-out", help=f"the synthetic receipt (default {M.SYNTHETIC})")
    ap.add_argument("--regenerate", action="store_true",
                    help="then run tools/s4/m7_inputs.py (only with the receipts at their tracked paths)")
    args = ap.parse_args(argv)
    if args.regenerate and (args.production_out or args.synthetic_out):
        ap.error("--regenerate reads the tracked receipts, so it takes no --production-out or --synthetic-out")
    return refresh(args)


if __name__ == "__main__":
    raise SystemExit(main())
