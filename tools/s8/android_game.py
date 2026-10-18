#!/usr/bin/env python3
"""android_game: build the game for Android (aarch64-linux-android, API 29+), as a PIE executable.

Subtitle: the Linux release's game (tools/s7/release_build.py step_game: tools/s4/link_s4.py with
the shipped-exe overlay, the launch observer and the presentation recorder as hooks) linked for
the triple aarch64-linux-android, from the lifted image cross-compiled the way
tools/s7/cross_build.py compiles it for aarch64-linux-musl (the x87 80-bit legalisation,
tools/s7/f80_legalize.py, and its soft-x87 tools/s7/f80_shims.c), with two differences an
Android executable needs:

* **Position independence.** Android's loader runs only PIE executables, so every object is
  compiled -fPIC and the link is -pie. The lifted IR declares its externals dso_local (every one
  is the runtime's, defined in the executable), so the lifted code is the same PC-relative code
  with or without the flag; the runtime's own references to bionic go through the GOT.
* **The Android ABI.** The triple is aarch64-linux-android, not aarch64-linux-musl: on Android x18
  is the platform register (bionic keeps the shadow call stack there), so code compiled for a
  generic Linux triple, which allocates x18, cannot be called into from bionic. The lifted image is
  recompiled for the Android triple for that reason alone; its codegen is otherwise that of the
  validated aarch64-linux-musl objects.

Two compilers, and why. The lifted IR is LLVM 20 text (`trunc nuw nsw` among others), which the
NDK r28's clang 19 cannot read, so the 24,036 lifted modules are compiled by the pinned zig's
clang 20 (`zig cc -target aarch64-linux-android -fPIC`, the population's own command from
cross_build with the triple swapped), and so is the lifted extras' generated registration
(extra_functions.<tag>.c, one function of 5,642 calls, which clang 19 -O2 did not finish in 13
minutes and zig's clang 20 compiles in 4 s). The rest of the C (the runtime, the function
table, the overlay table, the edge table, the hooks, the driver, the shims) is compiled by the
NDK's clang for aarch64-linux-android29, and the link is the NDK's clang++ (lld, bionic's
crtbegin_dynamic, libc++ linked statically, 16 KB page alignment), so the executable is linked
against the sysroot it runs on. Its only dependencies are bionic's libc, libm and libdl.

The runtime and harness compiles take tools/s7/arm64_link.py's -ffp-contract=off (AArch64 has a
fused multiply-add the x86-64 build lacks; see that tool), and the shims its counting defines
are not used: this is the product build, as the Linux release links the shims-free x86-64 image.
The runtime itself builds for bionic unchanged (no source edit: its aarch64 Linux paths, the
signal context included, are bionic's too).

Variants (`link --variant`):
  game   the deliverable, private/generated/s8/android/game/libsa2game.so: the Linux release's
         game (launch observer + presentation recorder, the overlay table), with game.env (the
         release's share/game.env lines) beside it. Contract D: the APK packages it as
         lib/arm64-v8a/libsa2game.so and the app execs it with the launcher's settings.
  boot   the boot replay's build (the tick probe alone, no overlay), for tick_replay's comparison.

Code-patch tweaks (`link --variants DIR`, not to be confused with `--variant`): DIR is what
tools/s8/variants/variants.py `build --objects <the link's objects>` wrote. Its C is compiled after
the hooks, its variant objects are linked after the extras and its wraps are added, once `linked`
has checked DIR was built for these objects; link.rec names it (`variants`), and the code-patch
tweaks runtime/sa2_tweaks.h registers that the library does not link (`patches_unlinked`, also
said on stderr). `--work DIR` moves the
link's objects and link directory out of the shared private/generated/s8/android/work.

Subcommands:
  compile     the lifted image for aarch64-linux-android -fPIC into --objects (default
              private/generated/s8/android/objects): map, arms and extra populations as
              cross_build.populations() names them, legalised, with build.jsonl, summary.json,
              f80_shims.o and census.json, as cross_build writes them. 9 minutes at 4 jobs.
              --f80 double legalises with tools/s8/f80_double.py instead (the fast mode: the
              x87's registers in binary64, the shims tools/s8/f80_shims_double.c) into
              objects-double; `link --f80 double` links from there.
  link        compile the C with the NDK and link private/generated/s8/android/<variant>/
              libsa2game.so and its link.rec (JSON: every source and object by sha256, the
              flags, the ELF's NEEDED, the fused multiply-add count). Seconds.
  stage       push the installation (default private/install, read only here; adb push --sync
              sends only what differs, 3.1 GB the first time) to /data/local/tmp/sa2/game and the
              built variants to /data/local/tmp/sa2/bin/<variant>/, on the emulator of
              tools/s8/android_emulator.py (or any device adb names emulator-5554).
  run-boot    the boot replay on the device as tools/s7/arm64_replays.py runs it under qemu (its
              argv and settings from tick_replay's tools, the outputs mirrored under
              /data/local/tmp/sa2/root and pulled back), its run.rec with the device's seconds and
              ticks per second, and with --compare tick_replay's comparison against the
              registered native captures and its label. --variant game --exe-kind shipped runs
              the product binary on the shipped executable through the overlay instead.
  live-smoke  the product as host/launch starts it (game.env, SA2_EXE_KIND=shipped, the
              presentation stream into a FIFO drained by `cat | wc -c` in place of the player, the
              keyboard FIFO held open by a silent writer, the launch observer's table), unbounded,
              stopped after --seconds; its smoke.rec has the GameLoop returns and Presents per
              second, the game-mode runs (the title is GameMode 14 at menu 0 held for 60 ticks),
              the peak resident set and the stream's bytes. Needs adbd as root (the shell domain
              may not mkfifo under /data/local/tmp; an app may in its own files directory).

Usage:
  source tools/env.sh
  tools/slot.sh .venv/bin/python tools/s8/android_game.py compile --jobs 4
  tools/slot.sh .venv/bin/python tools/s8/android_game.py compile --f80 double --jobs 4
  tools/slot.sh .venv/bin/python tools/s8/android_game.py link --variant game --f80 double
  tools/slot.sh .venv/bin/python tools/s8/android_game.py link --variant game
  tools/slot.sh .venv/bin/python tools/s8/android_game.py link --variant boot
  .venv/bin/python tools/s8/android_emulator.py setup && .venv/bin/python tools/s8/android_emulator.py start
  .venv/bin/python tools/s8/android_game.py stage
  .venv/bin/python tools/s8/android_game.py run-boot --work private/generated/s8/android/runs/boot-1 --compare
  .venv/bin/python tools/s8/android_game.py live-smoke --name smoke-1 --seconds 330
Outputs live under private/generated/s8/android/ (never tracked: the binary is the lifted game).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "AGENTS.md").is_file() and (p / "tools" / "pins.json").is_file():
            return p
    raise SystemExit(f"no repository above {__file__}")


ROOT = _root()
SELF = "tools/s8/android_game.py"
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "tools" / "s7"))
sys.path.insert(0, str(ROOT / "tools" / "spike"))
sys.path.insert(0, str(ROOT / "tools" / "s8"))

TRIPLE = "aarch64-linux-android"          # zig's spelling (the lifted modules)
API = 29
NDK_TRIPLE = f"aarch64-linux-android{API}"  # the NDK's (everything that needs bionic)
GEN = ROOT / "private" / "generated"
S8 = GEN / "s8" / "android"
OBJECTS = S8 / "objects"
# The f80 modes: "exact", tools/s7/f80_legalize.py's soft x87 (the default, the arm64-linux
# target's), and "double", tools/s8/f80_double.py's fast mode (the x87's registers in binary64;
# tools/s8/perf_f80.py measures it). Each mode's objects have a directory of their own, so neither
# replaces the other's, and each its pass and shims source.
F80_MODES = {"exact": OBJECTS, "double": S8 / "objects-double"}
F80_PASS = {"exact": ROOT / "tools" / "s7" / "f80_legalize.py",
            "double": ROOT / "tools" / "s8" / "f80_double.py"}
F80_SHIMS = {"exact": ROOT / "tools" / "s7" / "f80_shims.c",
             "double": ROOT / "tools" / "s8" / "f80_shims_double.c"}
OUT = S8 / "game"
WORK = S8 / "work"
SDK = ROOT / "private" / "android-sdk"
NDK = SDK / "ndk" / "28.2.13676358"
NDK_BIN = NDK / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
PIC = ("-fPIC",)
FP_FLAGS = ("-ffp-contract=off",)
BINARY = "libsa2game.so"
LINK_REC = "link.rec"
TAG = "next+allarms"
ZIG = ROOT / json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]["zig"]["path"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    path = Path(path)
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def ndk_tool(name: str) -> Path:
    p = NDK_BIN / name
    if not p.exists():
        raise SystemExit(f"the NDK has no {name} at {p}: is {NDK} installed?")
    return p


# ------------------------------------------------------------------ compile (the lifted image)

def android_compile_command(pop: str, triple: str, ir: Path, obj: Path) -> list[str]:
    """cross_build's command for the population, the triple swapped, plus -fPIC after it."""
    import cross_build as XB  # noqa: PLC0415
    cmd = XB._original_compile_command(pop, triple, ir, obj)
    at = cmd.index("-target") + 2
    return cmd[:at] + list(PIC) + cmd[at:]


def _worker_init(f80: str = "exact") -> None:
    """Each compile worker runs cross_build.one with the -fPIC command in place of its own, and
    the f80 mode's pass in place of cross_build's (tools/s7/f80_legalize.py's legalize, or
    tools/s8/f80_double.py's)."""
    import cross_build as XB  # noqa: PLC0415
    import f80_double as D  # noqa: PLC0415
    import f80_legalize as F  # noqa: PLC0415
    if not hasattr(XB, "_original_compile_command"):
        XB._original_compile_command = XB.compile_command
        XB.compile_command = android_compile_command
    if not hasattr(F, "_legalize_exact"):
        F._legalize_exact = F.legalize
    F.legalize = D.legalize if f80 == "double" else F._legalize_exact


def build_shims_ndk(outdir: Path, f80: str = "exact") -> dict:
    """The f80 mode's shims (tools/s7/f80_shims.c, or tools/s8/f80_shims_double.c, which
    includes it) for Android with the NDK's clang (they need bionic's headers), with
    cross_build's optimisation and the PIC flag, no counting defines (the product build)."""
    source = F80_SHIMS[f80]
    obj = outdir / "f80_shims.o"
    cmd = [str(ndk_tool("clang")), f"--target={NDK_TRIPLE}", "-O2", "-w", *PIC, *FP_FLAGS,
           "-c", str(source), "-o", str(obj)]
    if obj.exists():
        obj.unlink()
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"the shims did not compile for {NDK_TRIPLE}:\n{p.stderr[-3000:]}")
    return {"object": rel(obj), "command": cmd[1:], "source": rel(source),
            "source_sha256": sha256(source), "object_sha256": sha256(obj)}


def compile_image(args: argparse.Namespace) -> int:
    _worker_init(args.f80)
    import cross_build as XB  # noqa: PLC0415
    import f80_double as D  # noqa: PLC0415
    import f80_legalize as F  # noqa: PLC0415
    outdir = Path(args.objects or F80_MODES[args.f80]).resolve()
    pops = XB.populations()
    chosen = [p for p in args.populations.split(",") if p]
    for p in chosen:
        if p not in pops:
            raise SystemExit(f"unknown population {p}; known: {sorted(pops)}")
        (outdir / p).mkdir(parents=True, exist_ok=True)
    (outdir / "tmp").mkdir(parents=True, exist_ok=True)
    jobs = []
    for p in chosen:
        mods = pops[p][: args.limit] if args.limit else pops[p]
        jobs += [(p, va, str(src), str(outdir), TRIPLE, True, False) for va, src in mods]
    print(f"target={TRIPLE} pic=yes f80={args.f80} out={outdir} modules={len(jobs)} "
          f"jobs={args.jobs}", flush=True)
    log = outdir / "build.jsonl"
    t0 = time.time()
    n = ok = 0
    ctx = multiprocessing.get_context("fork")
    with log.open("w") as fh, cf.ProcessPoolExecutor(args.jobs, mp_context=ctx,
                                                     initializer=_worker_init,
                                                     initargs=(args.f80,)) as ex:
        for rec in ex.map(XB.one, jobs, chunksize=4):
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
            ok += bool(rec.get("compiled"))
            if n % 1000 == 0:
                print(f"  {n}/{len(jobs)} compiled={ok} {time.time() - t0:.0f}s", flush=True)
    wall = round(time.time() - t0, 1)
    shutil.rmtree(outdir / "tmp", ignore_errors=True)  # empty: workers remove their own IR
    recs = [json.loads(line) for line in log.read_text().splitlines()]
    built = {r["object"] for r in recs}
    stale = [o for p in chosen for o in sorted((outdir / p).glob("ir_*.o"))
             if f"{p}/{o.name}" not in built]
    for o in stale:
        o.unlink()
    summary: dict = {
        "schema": "sa2-s8-android-cross-build/v1", "generated_by": SELF, "target": TRIPLE,
        "pic": True, "legalized": True, "f80_mode": args.f80, "out": rel(outdir),
        "jobs": args.jobs,
        "wall_seconds": wall, "seconds_per_module": round(wall * args.jobs / max(1, len(recs)), 4),
        "commands": {p: android_compile_command(p, TRIPLE, Path("{ir}"), Path("{object}"))
                     for p in chosen},
        "pass": rel(F80_PASS[args.f80]), "pass_sha256": sha256(F80_PASS[args.f80]),
        "stale_objects_removed": len(stale),
        "by_population": {}, "errors": {}, "refused": {}}
    for p in chosen:
        rs = [r for r in recs if r["population"] == p]
        summary["by_population"][p] = {
            "modules": len(rs), "compiled": sum(bool(r.get("compiled")) for r in rs),
            "refused": sum("refused" in r for r in rs)}
    for r in recs:
        if "refused" in r:
            k = r["refused"][:160]
            summary["refused"][k] = summary["refused"].get(k, 0) + 1
        elif not r.get("compiled"):
            k = (r.get("error_head") or "?").splitlines()[0][:200]
            summary["errors"][k] = summary["errors"].get(k, 0) + 1
    summary["modules"] = len(recs)
    summary["compiled"] = sum(bool(r.get("compiled")) for r in recs)
    summary["shims"] = build_shims_ndk(outdir, args.f80)
    objs = [outdir / r["object"] for r in recs if r.get("compiled")]
    names = (D.shim_names if args.f80 == "double" else F.shim_names)(outdir / "f80_shims.o")
    cen = F.census(objs, names, args.jobs)
    (outdir / "census.json").write_text(json.dumps(cen, indent=1) + "\n")
    summary["census"] = {k: cen[k] for k in ("objects", "ok", "classes", "disallowed")}
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("target", "modules", "compiled", "wall_seconds",
                                              "seconds_per_module", "by_population", "errors",
                                              "refused")}, indent=1))
    print(json.dumps(summary["census"], indent=1)[:4000])
    return 0 if summary["compiled"] == summary["modules"] and cen["ok"] else 1


# ------------------------------------------------------------------ link (the executable)

def release_modules():
    """tools/s7/release_build.py, for what the Linux release's game links (its observer, its
    overlay table, its generated inputs and its settings), never retyped here."""
    import release_build as RB  # noqa: PLC0415
    return RB


def plan_for(variant: str, src: Path, work: Path) -> dict:
    """What a variant links beside the runtime, as the Linux tools link it:

    game   the Linux release's game (release_build.step_game): the launch observer (generated
           by release_build.observer_source, the tick probe inside it) and the presentation
           recorder as hooks, the tick probe's and the recorder's wraps, the overlay table;
    boot   the boot replay's build (tick_replay.run_lifted through loop_run): the tick probe
           alone as the hook and its wrap, no overlay (tools/s7/arm64_link.py's boot plan)."""
    RB = release_modules()
    m = RB.source_modules(src)
    tr, pr, rr = m["TR"], m["PR"], m["RR"]
    extra, _objects = RB.link_inputs()
    if variant == "game":
        obs = work / RB.OBSERVER
        obs.parent.mkdir(parents=True, exist_ok=True)
        obs.write_text(RB.observer_source(src, RB.state_words(src)))
        overlay = None
        for cand in (RB.WORK / "overlay" / "sa2_exe_overlay_table.c",
                     GEN / "s7" / "exe-overlay" / "sa2_exe_overlay_table.c"):
            if cand.is_file():
                overlay = cand
                break
        if overlay is None:
            raise SystemExit("no exe overlay table: run tools/s7/release_build.py --only tables "
                             "(or tools/s7/text_readset.py)")
        hooks = [obs, src / pr.RECORDER]
        return {"variant": variant, "main": src / rr.MAIN, "extra": extra + hooks,
                "hooks": [rel(h) for h in hooks], "wraps": [tr.WRAP] + list(pr.WRAPS),
                "overlay": overlay, "env_lines": RB.env_lines(m)}
    if variant == "boot":
        return {"variant": variant, "main": src / rr.MAIN, "extra": extra + [src / tr.HOOK],
                "hooks": [tr.HOOK], "wraps": [tr.WRAP], "overlay": None, "env_lines": None}
    raise SystemExit(f"unknown variant {variant}: game or boot")


def ndk_compile(job: tuple) -> dict:
    name, cmd, obj = job
    t0 = time.time()
    if obj.exists():
        obj.unlink()                    # a failed compile must not leave a stale object
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    rec = {"rc": p.returncode, "seconds": round(time.time() - t0, 2)}
    if p.returncode:
        rec["stderr_tail"] = p.stderr[-3000:]
    else:
        rec["object_sha256"] = sha256(obj)
    return {name: rec}


def lifted_inputs(objects: Path, linkdir: Path) -> dict:
    """The Android cross build's objects as link_s4 takes the x86-64 ones: the map and arm
    objects hard-linked into `linkdir` under the staged link directory's names (linked from it in
    name order), the extras in extra_functions.next+allarms.json's order; nothing failed."""
    import cross_build as XB  # noqa: PLC0415
    log = objects / "build.jsonl"
    recs = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    failed = [r.get("va") for r in recs if r.get("compiled") is not True]
    if failed:
        raise SystemExit(f"{len(failed)} modules of {log} did not compile (first {failed[:3]})")
    whole = {Path(r["object"]).name: objects / r["object"] for r in recs
             if r["population"] in ("map", "arms")}
    staged = {p.name for p in XB.STAGED.glob("ir_*.o")}
    if set(whole) != staged:
        raise SystemExit(f"the Android objects are not {rel(XB.STAGED)}'s: "
                         f"{len(set(whole) - staged)} extra, {len(staged - set(whole))} missing")
    linkdir.mkdir(parents=True, exist_ok=True)
    for t in linkdir.glob("ir_*.o"):
        if t.name not in whole:
            t.unlink()
    for name, obj in whole.items():
        t = linkdir / name
        if t.exists() and not t.samefile(obj):
            t.unlink()
        if not t.exists():
            os.link(obj, t)
    extras_have = {Path(r["object"]).name: objects / r["object"] for r in recs
                   if r["population"] == "extra"}
    order = [Path(o).name for o in json.loads(XB.EXTRA_STATE.read_text())["objects"]]
    missing = [o for o in order if o not in extras_have]
    if missing:
        raise SystemExit(f"{len(missing)} extras the link lists are not in {log}: {missing[:3]}")
    return {"log": rel(log), "log_sha256": sha256(log), "whole": sorted(whole),
            "extras": [extras_have[o] for o in order], "records": len(recs)}


def link_game(args: argparse.Namespace) -> int:
    import common as C  # noqa: PLC0415  tools/s3/whole_image/common.py
    src = Path(args.src).resolve()
    objects = Path(args.objects or F80_MODES[args.f80]).resolve()
    out = Path(args.out).resolve() if args.out else S8 / args.variant
    work = Path(os.path.abspath(args.work)) if getattr(args, "work", None) else WORK / args.variant
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    plan = plan_for(args.variant, src, work)
    patches = None
    if getattr(args, "variants", None):
        # Code-patch tweaks (tools/s8/variants/variants.py `build --objects <these objects>`):
        # their C after the hooks, their objects after the extras, their wraps after the plan's.
        RB = release_modules()
        patches = RB.load(src, "tools/s8/variants/variants.py", "variants").linked(Path(args.variants), objects)
        import link_s4 as L4v  # noqa: PLC0415
        clash = sorted(set(patches["wraps"]) & set(list(L4v.PRODUCTION_WRAPS) + list(plan["wraps"])))
        if clash:
            raise SystemExit(f"--variants wraps {clash}, which this link already wraps")
        plan = dict(plan, extra=list(plan["extra"]) + [patches["c"]],
                    wraps=list(plan["wraps"]) + patches["wraps"])
    # The code-patch tweaks runtime/sa2_tweaks.h registers that this library does not link: the
    # settings screen still offers each, and the game names it as one it was built without.
    unlinked = release_modules().unlinked_patches(src, {"variants": patches["note"]} if patches else {})
    if unlinked:
        print(f"code-patch tweaks this library does not link: {', '.join(unlinked)}", file=sys.stderr)
    import link_s4 as L4  # noqa: PLC0415
    summary = json.loads((objects / "summary.json").read_text())
    if not summary.get("pic") or summary.get("target") != TRIPLE:
        raise SystemExit(f"{objects} is not a -fPIC {TRIPLE} build of this tool")
    if summary.get("f80_mode", "exact") != args.f80:
        raise SystemExit(f"{objects} holds the f80 mode {summary.get('f80_mode', 'exact')!r} "
                         f"objects; this link asks for --f80 {args.f80}")
    shims = objects / "f80_shims.o"
    lifted = lifted_inputs(objects, work / "linkdir")
    clang, clangxx = str(ndk_tool("clang")), str(ndk_tool("clang++"))
    common = [L4.OPT, "-w", *PIC, *FP_FLAGS, "-I", str(src / "runtime"), "-I", str(C.REMILL_INC)]
    cflags = [clang, f"--target={NDK_TRIPLE}"] + common + ["-c"]
    cxxflags = [clangxx, f"--target={NDK_TRIPLE}"] + common + ["-std=c++17", "-c"]
    table = GEN / "s3" / "link" / TAG / "function_table_whole.c"
    sources = [(s, src / "runtime" / s, cflags) for s in L4.RUNTIME_C]
    sources += [(s, src / "runtime" / s, cxxflags) for s in L4.RUNTIME_CXX]
    sources.append(("function_table_whole.c", table, cflags))
    if plan["overlay"] is not None:
        sources.append((L4.exe_overlay_object(sha256(plan["overlay"])), plan["overlay"], cflags))
    # The lifted extras' registration (one function of 5,642 sa2_ft_add calls) is compiled by
    # the pinned zig's clang 20, as the lifted modules are: the NDK's clang 19 at -O2 did not
    # finish it in 13 minutes (2026-10-11; zig's -O2 takes 4 s). It includes only stdint.h.
    zflags = [str(ZIG), "cc", "-target", TRIPLE] + common + ["-c"]
    sources += [(f"extra{k}_{x.stem}.c", x,
                 zflags if x.name.startswith("extra_functions.") else cflags)
                for k, x in enumerate(plan["extra"])]
    sources.append(("main.c", plan["main"], cflags))
    objdir = work / "obj"
    objdir.mkdir(parents=True, exist_ok=True)
    jobs, log = [], {}
    for name, path, flags in sources:
        if not Path(path).is_file():
            raise SystemExit(f"{path} is not there ({name})")
        obj = objdir / (Path(name).stem + ".o")
        jobs.append((name, flags + [str(path), "-o", str(obj)], obj))
        log[name] = {"source": rel(path), "source_sha256": sha256(path)}
    t0 = time.time()
    with cf.ThreadPoolExecutor(max(1, args.jobs)) as ex:
        for done in ex.map(ndk_compile, jobs):
            for k, v in done.items():
                log[k].update(v)
    bad = {k: v for k, v in log.items() if v.get("rc")}
    if bad:
        raise SystemExit("compile failed: " + "; ".join(
            f"{k}: {v.get('stderr_tail', '')[-800:]}" for k, v in bad.items()))
    compile_seconds = round(time.time() - t0, 1)
    wrapped = list(L4.PRODUCTION_WRAPS) + list(plan["wraps"])
    binary = out / BINARY
    tmp = out / (BINARY + ".tmp")
    if tmp.exists():
        tmp.unlink()
    # A response file: the extras' absolute paths and the map's names are ~1 MB of arguments.
    rsp = work / "link.rsp"
    words = ([f"--target={NDK_TRIPLE}", L4.OPT, "-pie", "-static-libstdc++",
              "-Wl,--build-id=sha1", "-Wl,-z,max-page-size=16384"]
             + [f"-Wl,-wrap={w}" for w in wrapped]
             + [str(o) for _, _, o in jobs] + [str(x) for x in lifted["extras"]]
             + [str(x) for x in (patches["objects"] if patches else [])]
             + [str(shims)] + lifted["whole"] + ["-o", str(tmp)])
    rsp.write_text("\n".join(words) + "\n")
    t1 = time.time()
    p = subprocess.run([clangxx, f"@{rsp}"], cwd=str(work / "linkdir"), capture_output=True,
                       text=True, timeout=7200)
    rec = {"schema": "sa2-s8-android-link/v1", "generated_by": SELF, "variant": args.variant,
           "triple": NDK_TRIPLE, "src": rel(src), "ndk": rel(NDK),
           "ndk_clang": subprocess.run([clang, "--version"], capture_output=True,
                                       text=True).stdout.splitlines()[0],
           "hooks": plan["hooks"], "wraps": plan["wraps"], "wrapped": wrapped,
           "variants": patches["note"] if patches else None, "patches_unlinked": unlinked,
           "f80_mode": args.f80,
           "main": rel(plan["main"]),
           "exe_overlay": ({"table": rel(plan["overlay"]), "sha256": sha256(plan["overlay"])}
                           if plan["overlay"] else None),
           "link_s4": {"RUNTIME_C": L4.RUNTIME_C, "RUNTIME_CXX": L4.RUNTIME_CXX, "OPT": L4.OPT,
                       "PRODUCTION_WRAPS": L4.PRODUCTION_WRAPS},
           "flags_c": cflags[1:], "flags_cxx": cxxflags[1:], "flags_zig": zflags[1:],
           "compile": log,
           "compile_seconds": compile_seconds,
           "shims": {"object": rel(shims), "sha256": sha256(shims)},
           "lifted": {"objects": rel(objects), "log": lifted["log"],
                      "log_sha256": lifted["log_sha256"], "records": lifted["records"],
                      "whole_image": len(lifted["whole"]), "extras": len(lifted["extras"])},
           "link_flags": words[:6], "returncode": p.returncode,
           "link_seconds": round(time.time() - t1, 1), "stderr_tail": p.stderr[-3000:]}
    if p.returncode == 0 and tmp.is_file():
        os.replace(tmp, binary)     # by rename: a run using the old file keeps the one it mapped
        rec["binary"] = rel(binary)
        rec["binary_sha256"] = sha256(binary)
        rec["binary_bytes"] = binary.stat().st_size
        import arm64_link as AL  # noqa: PLC0415
        rec["fma_words"] = {"binary": AL.fma_words(binary),
                            "runtime_and_harness": sum(AL.fma_words(o) for _, _, o in jobs)}
        rd = subprocess.run([str(ndk_tool("llvm-readelf")), "-h", "-d", "-l", str(binary)],
                            capture_output=True, text=True).stdout
        rec["elf"] = {"type_dyn": "DYN (Shared object file)" in rd or "DYN (" in rd,
                      "pie": "PIE" in rd or "FLAGS_1" in rd,
                      "needed": [l.split("[")[1].rstrip("]") for l in rd.splitlines()
                                 if "(NEEDED)" in l],
                      "textrel": "TEXTREL" in rd}
        if plan["env_lines"]:
            (out / "game.env").write_text("\n".join(plan["env_lines"]) + "\n")
            rec["game_env"] = plan["env_lines"]
    (out / LINK_REC).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: rec.get(k) for k in ("variant", "returncode", "compile_seconds",
                                               "link_seconds", "binary", "binary_sha256",
                                               "binary_bytes", "elf", "fma_words")}, indent=1))
    if p.returncode:
        print(p.stderr[-4000:], file=sys.stderr)
    return p.returncode


# ------------------------------------------------------------------ on the device

# The device's layout (tools/s8/android_emulator.py's emulator, or any adb device):
#   DEV/game                    a copy of the installation (SA2_INSTALL's files), pushed by `stage`
#   DEV/bin/<variant>/          the executable
#   DEV/root                    a run's working directory, mirroring the checkout: private/install
#                               and private/exe/* are links to DEV/game, every other path a run
#                               names relative to the checkout is the same path under DEV/root,
#                               so tick_replay's settings are used as they are made.
DEV = "/data/local/tmp/sa2"
DEV_ROOT = f"{DEV}/root"
ALARM_SCALE = 30          # the probe's alarm on translated arm64, as arm64_replays' qemu takes it
RUNS = S8 / "runs"


def emu():
    sys.path.insert(0, str(ROOT / "tools" / "s8"))
    import android_emulator as E  # noqa: PLC0415
    return E


def dev_shell(script: str, timeout: float = 600) -> subprocess.CompletedProcess:
    return emu().adb("shell", script, timeout=timeout)


def stage(args: argparse.Namespace) -> int:
    """Push the installation (adb push --sync: only what differs), the executables and the
    variant's binary, and lay out DEV/root's links."""
    E = emu()
    st = E.status_dict()
    if not st.get("booted"):
        raise SystemExit("the emulator is not booted: tools/s8/android_emulator.py start")
    install = Path(args.install).resolve()
    if not install.is_dir():
        raise SystemExit(f"no installation at {install}")
    t0 = time.time()
    dev_shell(f"mkdir -p {DEV}/game {DEV}/bin {DEV_ROOT}/private/exe", timeout=60)
    entries = sorted(install.iterdir())
    for e in entries:
        p = E.adb("push", "--sync", str(e), f"{DEV}/game/", timeout=3600)
        if p.returncode:
            raise SystemExit(f"adb push {e} failed: {p.stderr[-800:]}")
    pushed = {"install": str(install), "entries": len(entries),
              "seconds": round(time.time() - t0, 1)}
    for variant in args.variant:
        b = S8 / variant / BINARY
        if not b.is_file():
            raise SystemExit(f"{b} is not built: android_game.py link --variant {variant}")
        dev_shell(f"mkdir -p {DEV}/bin/{variant}", timeout=60)
        p = E.adb("push", str(b), f"{DEV}/bin/{variant}/{BINARY}", timeout=600)
        if p.returncode:
            raise SystemExit(f"adb push {b} failed: {p.stderr[-800:]}")
        dev_shell(f"chmod 755 {DEV}/bin/{variant}/{BINARY}", timeout=60)
        pushed[variant] = sha256(b)
    # The checkout's private/install and private/exe/<exe> as a run sees them: links to the
    # pushed copy (the copy holds both executables, as the installation does).
    dev_shell(f"cd {DEV_ROOT}/private && rm -f install && ln -s {DEV}/game install && "
              f"cd exe && for x in sonic2app.exe sonic2app-unpacked.exe; do rm -f $x; "
              f"ln -s {DEV}/game/$x $x; done", timeout=60)
    exe = install / "sonic2app-unpacked.exe"
    mine = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
    if exe.is_file() and mine.is_file() and sha256(exe) != sha256(mine):
        raise SystemExit(f"{exe} is not {rel(mine)}: the device's private/exe would differ")
    print(json.dumps(pushed, indent=1))
    return 0


def dev_path(value: str) -> str:
    """A setting's value as the device names it: a path under the checkout moved under
    DEV_ROOT; anything else as it is."""
    return DEV_ROOT + value[len(str(ROOT)):] if value.startswith(str(ROOT) + "/") else value


def run_boot(args: argparse.Namespace) -> int:
    """The boot replay on the device, as tools/s7/arm64_replays.py runs it under qemu: its
    argv and settings from tick_replay's own tools (loop_command), the outputs mirrored under
    DEV_ROOT and pulled back, the same checks of the run record, a run.rec beside them, and with
    --compare tick_replay's comparison against the registered native captures."""
    import arm64_replays as AR  # noqa: PLC0415
    E = emu()
    name = "boot"
    work = Path(args.work).resolve()
    t = AR.tools(work)
    T, LR, RR, SS, DP = t["T"], t["LR"], t["RR"], t["SS"], t["DP"]
    entry = AR.entries()[name]
    out = work / name
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: give each run a directory of its own")
    out.mkdir(parents=True, exist_ok=True)
    linkrec = json.loads((S8 / args.variant / LINK_REC).read_text())
    binary = ROOT / linkrec["binary"]
    if sha256(binary) != linkrec["binary_sha256"]:
        raise SystemExit(f"{binary} is not the binary its link record names")
    cmd, read = AR.loop_command(t, entry, out)
    plan = AR.parse_loop_cmd(cmd)
    vfs = out / "vfsroot"
    vfs.mkdir()
    spec_env = dict(plan["env"], SA2_VFS_WRITE_ROOT=rel(vfs))
    saves = SS.reset_lifted(["--run", "|".join([plan["label"]] + [f"{k}={v}" for k, v in
                                                                   spec_env.items()])], ROOT)
    SS.require_empty(saves)
    env = {k: v for k, v in RR.clean_environ().items() if k != LR.EXE_KIND_VAR}
    env.update(LR.BASE_ENV)
    env.update(spec_env)
    env = {k: dev_path(v) for k, v in env.items()
           if k.startswith("SA2_") and k not in ("SA2_F80_STATS", "SA2_WORDLOG_BUILD")}
    exe = T.C.EXE
    if args.exe_kind == "shipped":
        # The product's image: the shipped executable through the build's overlay table
        # (loop_run's own spelling of the switch), which only the game variant links.
        exe = LR.SHIPPED_EXE
        env[LR.EXE_KIND_VAR] = "shipped"
    probe_args, alarm = AR.scaled_probe_args(plan["probe_args"], args.alarm_scale)
    runrec = out / "probe.runrec"
    dev_bin = f"{DEV}/bin/{args.variant}/{BINARY}"
    argv = [dev_bin, dev_path(str(exe)), "--json", rel(runrec)] + probe_args
    # Every file a setting names that the run reads (the device profile) goes to the same
    # path under DEV_ROOT; every output's directory is made there.
    inputs = [v for k, v in env.items() if not v.startswith("/") and (ROOT / v).is_file()]
    outdirs = sorted({str(Path(rel(out)))} | {str(Path(v).parent) for v in env.values()
                                                if v.startswith("private/generated/")})
    script = [f"mkdir -p {DEV_ROOT}/{d}" for d in outdirs]
    script.append(f"rm -rf {DEV_ROOT}/{rel(out)} && mkdir -p {DEV_ROOT}/{rel(vfs)}")
    r = dev_shell(" && ".join(script), timeout=60)
    if r.returncode:
        raise SystemExit(f"cannot lay out {DEV_ROOT}: {r.stderr[-500:]}")
    for f in inputs:
        dev_shell(f"mkdir -p {DEV_ROOT}/{Path(f).parent}", timeout=60)
        p = E.adb("push", str(ROOT / f), f"{DEV_ROOT}/{f}", timeout=120)
        if p.returncode:
            raise SystemExit(f"adb push {f}: {p.stderr[-500:]}")
    bound = int(max(plan["timeout"], alarm["ran"]) + 900)
    lines = ["#!/system/bin/sh", f"cd {DEV_ROOT} || exit 97"]
    lines += [f"export {k}='{v}'" for k, v in sorted(env.items())]
    lines += [f"t0=$(date +%s%N)",
              f"timeout -s KILL {bound} " + " ".join(f"'{a}'" for a in argv)
              + f" > {rel(out)}/stdout.txt 2> {rel(out)}/stderr.txt",
              "rc=$?", "t1=$(date +%s%N)",
              f"echo \"$rc $t0 $t1\" > {rel(out)}/device.rc"]
    local_script = out / "run.sh"
    local_script.write_text("\n".join(lines) + "\n")
    E.adb("push", str(local_script), f"{DEV_ROOT}/{rel(out)}/run.sh", timeout=60)
    started = time.time()
    p = E.adb("shell", f"sh {DEV_ROOT}/{rel(out)}/run.sh", timeout=bound + 120)
    seconds = round(time.time() - started, 2)
    pulled = {}
    for f in ("probe.runrec", "lifted.csv", "lifted-words.bin", "stdout.txt", "stderr.txt",
              "device.rc"):
        q = E.adb("pull", f"{DEV_ROOT}/{rel(out)}/{f}", str(out / f), timeout=600)
        pulled[f] = q.returncode == 0
    rc_line = (out / "device.rc").read_text().split() if (out / "device.rc").is_file() else []
    rc = int(rc_line[0]) if rc_line else -1
    device_seconds = (round((int(rc_line[2]) - int(rc_line[1])) / 1e9, 3)
                      if len(rc_line) == 3 else None)
    if rc or not runrec.is_file():
        raise SystemExit(f"{name}: the run did not complete on the device (rc {rc}); see "
                         f"{out / 'stderr.txt'}: "
                         f"{(out / 'stderr.txt').read_text(errors='replace')[-1500:] if (out / 'stderr.txt').is_file() else p.stderr[-800:]}")
    d = json.loads(runrec.read_text())
    stop = T.stop_note(d)
    if AR.ALARM_NOTE in stop:
        raise SystemExit(f"{name}: the run ended at the probe's alarm ({stop[:200]}): raise "
                         "--alarm-scale")
    try:
        device = DP.check_run(d, read["profile"])
    except DP.Refusal as e:
        raise SystemExit(f"{name}: the run is not the one asked for: {e}") from None
    off = RR.switch_problems(d, {**RR.ENV, **RR.profile_env(read["profile"])})
    if off:
        raise SystemExit(f"{name}: its devices are not its configuration's: {'; '.join(off)}")
    capture = out / "lifted.csv"
    if not capture.is_file():
        raise SystemExit(f"{name}: the run wrote no tick capture")
    rows = [l for l in capture.read_text().splitlines() if l and not l.startswith("#")]
    ticks = max(0, len(rows) - 1)
    lifted = {"device_profile": device, "guest_paths": DP.run_guest(d), "saves": saves,
              "capture": rel(capture), "sha256": sha256(capture),
              "binary_sha256": linkrec["binary_sha256"], "run_json": rel(runrec),
              "reach": T.reach_of(d), "poll_handoff": T.poll_counters(d),
              "stop": {"note": stop, "exit_pc": (d.get("trap") or {}).get("exit_pc"),
                       "esp": (d.get("trap") or {}).get("esp"),
                       "stack": (d.get("trap") or {}).get("stack", [])}}
    if "word_policy" in entry:
        lifted["words"] = T.keep_lifted_words(capture, out / "lifted-words.bin")
    props = {k: E.getprop(k) for k in ("ro.product.cpu.abilist", "ro.dalvik.vm.native.bridge",
                                       "ro.build.version.sdk", "ro.product.model",
                                       "ro.hardware")}
    rec = {"schema": "sa2-s8-android-run/v1", "generated_by": SELF, "replay": name,
           "runner": {"kind": "android-adb", "serial": E.SERIAL, "props": props,
                      "translated": "native.bridge" in json.dumps(props)
                      and props.get("ro.dalvik.vm.native.bridge") not in ("", "0")},
           "link": {"path": rel(S8 / args.variant / LINK_REC),
                    "sha256": sha256(S8 / args.variant / LINK_REC)},
           "binary": {"path": rel(binary), "sha256": linkrec["binary_sha256"]},
           "triple": linkrec["triple"], "variant": args.variant,
           "argv": argv, "env": dict(sorted(env.items())),
           "loop_run": {k: plan[k] for k in ("label", "main", "hooks", "wraps", "timeout")},
           "probe_alarm": alarm, "returncode": rc, "seconds": seconds,
           "device_seconds": device_seconds, "ticks": ticks,
           "ticks_per_second": round(ticks / device_seconds, 2) if device_seconds else None,
           "pulled": pulled, "lifted": lifted}
    (out / AR.RUN_REC).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"replay": name, "seconds": seconds, "device_seconds": device_seconds,
                      "ticks": ticks, "ticks_per_second": rec["ticks_per_second"], "rc": rc,
                      "stop": stop[:200], "capture": lifted["sha256"]}, indent=1), flush=True)
    if args.compare:
        recs = AR.compare_one(t, name, [work])
        result = recs[0]["result"]
        m5 = T.m5_record({"name": name, "result": result})
        try:
            ident = T.identity_problems({"replays": [{"name": name, "result": result}]},
                                        json.loads((ROOT / AR.REGISTRY).read_text()), ROOT)
        except SystemExit as e:
            ident = [f"the identity rule cannot be checked: {e}"]
        lab = AR.label(m5, ident)
        print(json.dumps({"label": lab[0], "residues": lab[1], "first_divergent_tick": lab[2],
                          "m5": m5}, indent=1))
    return 0


TITLE_MODE, TITLE_MENU, TITLE_HOLD = 14, 0, 60   # GameMode_Advertise at Menus_TitleScreen,
#                                               held as host/launch's selftest holds it


def ticks_summary(path: Path) -> dict:
    """The launch observer's table read back: (game_mode, current_menu) runs in order, with the
    tick each began at, and the first tick of a title held for TITLE_HOLD ticks."""
    runs: list[list] = []
    title = None
    for line in path.read_text().splitlines():
        f = line.split("\t")
        if f[0] != "T" or len(f) < 5 or not f[1].isdigit():
            continue
        tick, mode, menu = int(f[1]), int(f[3]), int(f[4])
        if runs and runs[-1][0] == mode and runs[-1][1] == menu:
            runs[-1][2] += 1
        else:
            runs.append([mode, menu, 1, tick])
        if title is None and mode == TITLE_MODE and menu == TITLE_MENU \
                and runs[-1][2] >= TITLE_HOLD:
            title = runs[-1][3]
    return {"runs": [{"game_mode": m, "current_menu": c, "ticks": n, "from_tick": t}
                     for m, c, n, t in runs], "title_from_tick": title}


def rate_phases(path: Path) -> list[dict]:
    """rate.txt (device ns, then the last T row) as ticks per second between samples, joined
    into runs of the same game mode."""
    pts = []
    for line in path.read_text().splitlines():
        f = line.split()
        if len(f) >= 6 and f[1] == "T" and f[2].isdigit():
            pts.append((int(f[0]) / 1e9, int(f[2]), int(f[4])))
    out: list[dict] = []
    for (t0, k0, _m0), (t1, k1, m1) in zip(pts, pts[1:]):
        if t1 <= t0:
            continue
        if out and out[-1]["game_mode"] == m1:
            out[-1]["seconds"] += t1 - t0
            out[-1]["ticks"] += k1 - k0
        else:
            out.append({"game_mode": m1, "seconds": t1 - t0, "ticks": k1 - k0})
    for r in out:
        r["seconds"] = round(r["seconds"], 1)
        r["ticks_per_second"] = round(r["ticks"] / r["seconds"], 2) if r["seconds"] else None
    return out


def live_smoke(args: argparse.Namespace) -> int:
    """The game as the product starts it (host/launch/sa2_launch.cpp step 4, contract D's
    settings): share/game.env's settings (the link's game.env), the shipped executable under
    SA2_EXE_KIND=shipped, the presentation stream into a FIFO (drained by `cat | wc -c` in place
    of the player), the keyboard FIFO held open by a writer that sends nothing, the input log and
    the launch observer's table; argv {game, exe, --json, record, --timeout, 0}, its working
    directory the session. It runs unbounded, so it is stopped after --seconds (SIGTERM); what is
    read back is the observer's table: GameLoop returns (T rows) and Presents over the run."""
    E = emu()
    if not E.ensure_root():
        raise SystemExit("adbd is not root: the shell domain may not mkfifo under /data/local/tmp")
    env_file = S8 / "game" / "game.env"
    if not env_file.is_file():
        raise SystemExit(f"no {rel(env_file)}: android_game.py link --variant game")
    settings = [l for l in env_file.read_text().splitlines() if l and not l.startswith("#")]
    sess = f"{DEV}/live/{args.name}"
    meta = ('{"host":"android-smoke","interactive":false,"runner":"tools/s8/android_game.py",'
            '"scene":"boot","selftest":false}')
    contract = {"SA2_INSTALL": f"{DEV}/game", "SA2_VFS_WRITE_ROOT": f"{sess}/overlay",
                "SA2_EXE_KIND": "shipped", "SA2_REC_TRACE": f"{sess}/game.fifo",
                "SA2_REC_META": meta, "SA2_REC_COUNTS": f"{sess}/counts.runrec",
                "SA2_INPUT_DI_SOURCE": f"{sess}/input.fifo",
                "SA2_INPUT_DI_LOG": f"{sess}/input.log", "SA2_LAUNCH_TICKS": f"{sess}/ticks.tsv"}
    lines = ["#!/system/bin/sh", f"rm -rf {sess}", f"mkdir -p {sess}/overlay", f"cd {sess}",
             "mkfifo game.fifo input.fifo",
             "(cat game.fifo | wc -c > trace.bytes) &",
             f"(sleep {args.seconds + 60} > input.fifo) &", "holder=$!"]
    lines += [f"export {l.split('=', 1)[0]}='{l.split('=', 1)[1]}'" for l in settings]
    lines += [f"export {k}='{v}'" for k, v in contract.items()]
    lines += ["t0=$(date +%s%N)",
              f"{DEV}/bin/game/{BINARY} {DEV}/game/sonic2app.exe "
              f"--json {sess}/game.json --timeout 0 > game.out 2> game.log &", "gp=$!",
              # The observer's last T row every 5 s, with the device's clock: ticks per second
              # by phase (a movie's ticks cost more than the title's).
              "(while kill -0 $gp 2>/dev/null; do echo \"$(date +%s%N) "
              "$(grep '^T' ticks.tsv 2>/dev/null | tail -1)\"; sleep 5; done > rate.txt) &",
              f"sleep {args.seconds}",
              # The peak resident set so far (the translator's own memory included when the
              # device translates), read before the game is stopped.
              "grep -E '^(VmHWM|VmRSS|Threads)' /proc/$gp/status > mem.txt",
              "kill -TERM $gp", "wait $gp", "rc=$?", "t1=$(date +%s%N)",
              'echo "$rc $t0 $t1" > device.rc', "kill $holder 2>/dev/null", "wait"]
    local = S8 / "live" / args.name
    local.mkdir(parents=True, exist_ok=True)
    (local / "run.sh").write_text("\n".join(lines) + "\n")
    dev_shell(f"mkdir -p {DEV}/live", timeout=60)
    E.adb("push", str(local / "run.sh"), f"{DEV}/live/{args.name}.sh", timeout=60)
    t0 = time.time()
    E.adb("shell", f"sh {DEV}/live/{args.name}.sh", timeout=args.seconds + 300)
    for f in ("ticks.tsv", "game.log", "game.out", "trace.bytes", "input.log", "device.rc",
              "game.json", "mem.txt", "rate.txt"):
        E.adb("pull", f"{sess}/{f}", str(local / f), timeout=600)
    rc_line = (local / "device.rc").read_text().split() if (local / "device.rc").is_file() else []
    secs = (int(rc_line[2]) - int(rc_line[1])) / 1e9 if len(rc_line) == 3 else None
    rows = [l.split("\t") for l in (local / "ticks.tsv").read_text().splitlines()
            if l.startswith("T\t")] if (local / "ticks.tsv").is_file() else []
    k_rows = sum(1 for l in (local / "ticks.tsv").read_text().splitlines()
                 if l.startswith("K\t")) if (local / "ticks.tsv").is_file() else 0
    last = rows[-1] if rows else None
    rec = {"schema": "sa2-s8-android-live-smoke/v1", "generated_by": SELF, "name": args.name,
           "binary_sha256": sha256(S8 / "game" / BINARY), "settings": settings,
           "contract": contract, "returncode": int(rc_line[0]) if rc_line else None,
           "device_seconds": round(secs, 2) if secs else None,
           "wall_seconds": round(time.time() - t0, 1),
           "gameloop_returns": int(last[1]) if last else 0,
           "presents": int(last[2]) if last else 0,
           "game_mode": last[3] if last else None, "current_menu": last[4] if last else None,
           "keyboard_rows": k_rows,
           "modes": ticks_summary(local / "ticks.tsv") if (local / "ticks.tsv").is_file() else None,
           "rate_by_mode": rate_phases(local / "rate.txt") if (local / "rate.txt").is_file() else None,
           "memory": {l.split(":")[0]: l.split(":")[1].strip()
                      for l in (local / "mem.txt").read_text().splitlines() if ":" in l}
           if (local / "mem.txt").is_file() else None,
           "ticks_per_second": round(int(last[1]) / secs, 2) if last and secs else None,
           "presents_per_second": round(int(last[2]) / secs, 2) if last and secs else None,
           "trace_bytes": int((local / "trace.bytes").read_text().split()[0])
           if (local / "trace.bytes").is_file() and (local / "trace.bytes").read_text().split()
           else None,
           "game_log_tail": (local / "game.log").read_text(errors="replace")[-1500:]
           if (local / "game.log").is_file() else None,
           "props": {k: E.getprop(k) for k in ("ro.product.cpu.abilist",
                                               "ro.dalvik.vm.native.bridge")}}
    (local / "smoke.rec").write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k not in ("settings", "contract")},
                     indent=1))
    return 0 if rec["gameloop_returns"] else 1


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="the lifted image for aarch64-linux-android -fPIC")
    c.add_argument("--objects", default=None,
                   help="default private/generated/s8/android/objects (--f80 exact) or "
                        "objects-double (--f80 double)")
    c.add_argument("--f80", choices=sorted(F80_MODES), default="exact",
                   help="exact (tools/s7/f80_legalize.py's soft x87, the default) or double "
                        "(tools/s8/f80_double.py: the x87's registers in binary64)")
    c.add_argument("--populations", default="map,arms,extra")
    c.add_argument("--jobs", type=int, default=4)
    c.add_argument("--limit", type=int, default=0, help="first N modules of each population")
    lk = sub.add_parser("link", help="the executable: runtime + hooks + lifted, NDK-linked PIE")
    lk.add_argument("--variant", default="game", choices=["game", "boot"])
    lk.add_argument("--src", default=str(ROOT), help="the tree (or a snapshot) to compile")
    lk.add_argument("--objects", default=None,
                    help="default: the --f80 mode's objects directory")
    lk.add_argument("--f80", choices=sorted(F80_MODES), default="exact",
                    help="the objects' f80 mode, which the link checks against their summary")
    lk.add_argument("--out", default=None,
                    help="default private/generated/s8/android/<variant> (the game variant's is "
                         "the deliverable, private/generated/s8/android/game/libsa2game.so)")
    lk.add_argument("--jobs", type=int, default=4)
    lk.add_argument("--work", default=None,
                    help="where the objects and the link directory go (default "
                         "private/generated/s8/android/work/<variant>)")
    lk.add_argument("--variants", default=None,
                    help="a directory tools/s8/variants/variants.py built for --objects: its "
                         "code-patch tweaks are linked in (their C, variant objects and wraps)")
    sg = sub.add_parser("stage", help="push the installation and the binaries to the device")
    sg.add_argument("--install", default=str(ROOT / "private" / "install"),
                    help="the installation to copy (read only; default private/install, the "
                         "boot replay's)")
    sg.add_argument("--variant", action="append", default=[],
                    help="game and/or boot (default both that are built)")
    rn = sub.add_parser("run-boot", help="the boot replay on the device, compared")
    rn.add_argument("--variant", default="boot", choices=["game", "boot"])
    rn.add_argument("--work", default=str(RUNS / "boot-1"))
    rn.add_argument("--alarm-scale", type=int, default=ALARM_SCALE)
    rn.add_argument("--compare", action="store_true")
    rn.add_argument("--exe-kind", default="unpacked", choices=["unpacked", "shipped"],
                    help="shipped: the shipped executable under SA2_EXE_KIND=shipped (the game "
                         "variant, which links the overlay table)")
    lv = sub.add_parser("live-smoke", help="the product's settings on the device, for --seconds")
    lv.add_argument("--name", default="smoke-1")
    lv.add_argument("--seconds", type=int, default=180)
    args = ap.parse_args(argv)
    if args.cmd == "live-smoke":
        return live_smoke(args)
    if args.cmd == "stage":
        if not args.variant:
            args.variant = [v for v in ("game", "boot") if (S8 / v / BINARY).is_file()]
        return stage(args)
    if args.cmd == "run-boot":
        return run_boot(args)
    if args.cmd == "compile":
        return compile_image(args)
    if args.cmd == "link":
        return link_game(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
