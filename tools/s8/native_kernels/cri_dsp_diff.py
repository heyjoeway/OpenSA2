#!/usr/bin/env python3
"""cri_dsp_diff: build and run the differential harness of runtime/sa2_cri_dsp.c.

Subtitle: compiles tools/s8/native_kernels/cri_dsp_diff.c and state_layout.cpp with the pinned zig
and runtime/sa2_cri_dsp.c with tools/s4/link_s4.py's own runtime command, links them with the
double-mode lifted objects of the native kernels (tools/s8/perf_f80.py's output,
private/generated/s8/pc-double/linux-obj) as `__real_`, wrapped as the production link wraps them
(link_s4.PRODUCTION_WRAPS), and runs each kernel on CASES random and edge-case States and guest
memories plus every guard's cases; prints the harness's JSON.

    source tools/env.sh
    .venv/bin/python tools/s8/native_kernels/cri_dsp_diff.py [--cases 1000000] [--seed 1]
        [--kernels biquad,comb,mix] [--variant double|nomarker] [--json OUT] [--out DIR]

`--variant nomarker` links without the double mode's marker (`sa2_f80_state_double`), as the
exact build is: every call must then take the lifted body. `--arch aarch64` builds the harness
for aarch64-linux-musl from the Android game's double-mode lifted objects
(private/generated/s8/android/objects-double, tools/s8/android_game.py `compile --f80 double`:
the phone's own code), compiles the runtime file with the Android link's -ffp-contract=off and
the double mode's shims from their source, and runs it under the pinned qemu-aarch64. Builds land
under private/generated/s8/cri-dsp-native/harness/<arch>-<variant>/ (`--out DIR`: DIR/<arch>-<variant>/,
and the Windows runs' Wine prefix beside DIR).

The lifted objects must be what this checkout's double mode makes of this checkout's IR, or the
harness would hold the native bodies to an older double mode (`lifted_objects`). Each kernel's
record in the build's build.jsonl names its IR and the IR's sha256, which must still be the
file's; the build's summary.json must name the double mode's pass (tools/s8/f80_double.py) and
shims (tools/s8/f80_shims_double.c) at this checkout's sha256, and the shims object the harness
links (the build's f80_shims.o, on x86_64 and Windows) at the summary's; and every input the
summary's `deps` records (tools/s8/perf_f80.py `deps`: what the pass imports, what the shims
include) must still hash as recorded. A build whose summary has no `deps` (perf_f80.py and
android_game.py builds made before perf_f80.py recorded them) is held, for each input perf_f80.py
`deps` names now beyond the pass and the shims (tools/s7/f80_legalize.py, tools/s7/f80_shims.c),
to the file's modification time: before the build started (summary.json's time less its
`wall_seconds`), or the build is refused. A refusal names every problem and the command that
rebuilds the build (tools/s7/release_build.py's `f80_command` for a perf_f80.py build,
tools/s8/android_game.py `compile --f80 double` for the Android one).

`stats(exe, mask)` runs the harness's `calls` (one realistic call of each kernel) with
SA2_CRI_DSP=mask (None: unset) and SA2_CRI_DSP_STATS=1, and reads the counters the runtime prints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in ("tools/s3/whole_image", "tools/spike", "tools/s4"):
    sys.path.insert(0, str(ROOT / p))
import link_s4 as L4  # noqa: E402  read-only: the runtime's compile and the production wraps

GEN = ROOT / "private" / "generated"
LIFTED = GEN / "s8" / "pc-double" / "linux-obj"
ARCHES = {
    # arch: (zig target, the double-mode lifted objects, the shims object or None to compile
    #        tools/s8/f80_shims_double.c, extra runtime flags, the runner prefix)
    "x86_64": ("x86_64-linux-musl", LIFTED, LIFTED / "f80_shims.o", [], []),
    "aarch64": ("aarch64-linux-musl", GEN / "s8" / "android" / "objects-double", None,
                ["-ffp-contract=off"],
                [str(ROOT / "private/predecessor-tools/qemu/root/usr/bin/qemu-aarch64")]),
    # The Windows release's code: tools/s7/win_link.py's triple and WIN_FLAGS, its double-mode
    # lifted objects, run under the Proton wine tools/s7/wine_run.py runs, in a prefix of its own.
    "x86_64-windows": ("x86_64-windows-gnu", GEN / "s8" / "pc-double" / "windows-obj",
                       GEN / "s8" / "pc-double" / "windows-obj" / "f80_shims.o",
                       ["-mno-ms-bitfields"],
                       [str(Path.home() / ".local/share/Steam/steamapps/common/"
                            "Proton - Experimental/files/bin/wine")]),
}
# The double mode's pass and shims, as a double build's summary must name them.
DOUBLE_PASS = "tools/s8/f80_double.py"
DOUBLE_SHIMS = "tools/s8/f80_shims_double.c"
SHIMS_SRC = ROOT / DOUBLE_SHIMS
ANDROID_COMPILE = "tools/s8/android_game.py compile --f80 double --jobs 4"
OUT = GEN / "s8" / "cri-dsp-native" / "harness"
RUNTIME_FILE = "sa2_cri_dsp.c"
# The kernels' guest functions: the harness's `__real_` objects and the -wrap the link passes.
KERNELS = {"biquad": 0x00842500, "comb": 0x00843400, "net": 0x00843460, "mix": 0x008418B0}


def wine_prefix() -> Path:
    """The Windows runs' Wine prefix, beside the harness builds."""
    return OUT.parent / "wine-prefix"


def wrap_symbol(va: int) -> str:
    return f"sub_{va:x}"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def release_build():
    """tools/s7/release_build.py, read-only: its f80_command and f80_deps."""
    sys.path.insert(0, str(ROOT / "tools" / "s7"))
    import release_build as RB  # noqa: PLC0415
    return RB


def rebuild_command(arch: str, root: Path) -> str:
    """The command that rebuilds `root` as the double-mode build of `arch`."""
    if arch == "aarch64":
        objects = "" if root.resolve() == ARCHES["aarch64"][1].resolve() else f" --objects {root}"
        return f"source tools/env.sh; tools/slot.sh .venv/bin/python {ANDROID_COMPILE}{objects}"
    return release_build().f80_command(root, ARCHES[arch][0], "double")


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def build_problems(root: Path, summary: dict, arch: str) -> list[str]:
    """What makes the double build at `root` not this checkout's double mode (module docstring)."""
    problems = []
    shims = summary.get("shims") or {}
    for what, path, want, expected in (
            ("pass", summary.get("pass"), summary.get("pass_sha256"), DOUBLE_PASS),
            ("shims source", shims.get("source"), shims.get("source_sha256"), DOUBLE_SHIMS)):
        if path != expected:
            problems.append(f"its {what} is {path}, not {expected}")
        elif sha256(ROOT / path) != want:
            problems.append(f"its {what} {path} was {want} and is {sha256(ROOT / path)} here")
    if ARCHES[arch][2] is not None:
        obj = root / "f80_shims.o"
        if not obj.is_file() or sha256(obj) != shims.get("object_sha256"):
            problems.append(f"its {obj.name} is not the object its summary names "
                            f"({shims.get('object_sha256')})")
    deps = summary.get("deps")
    if deps is not None:
        for path, want in sorted(deps.items()):
            have = sha256(ROOT / path) if (ROOT / path).is_file() else None
            if have != want:
                problems.append(f"its input {path} was {want} and is {have} here")
        return problems
    start = (root / "summary.json").stat().st_mtime - float(summary.get("wall_seconds") or 0)
    for path in sorted(set(release_build().f80_deps("double")) - {DOUBLE_PASS, DOUBLE_SHIMS}):
        p = ROOT / path
        if not p.is_file():
            problems.append(f"its input {path} is gone from this checkout")
        elif p.stat().st_mtime >= start:
            problems.append(f"its input {path} was modified at {stamp(p.stat().st_mtime)}, after "
                            f"the build started ({stamp(start)}), and the build records no deps "
                            f"to tell whether it changed")
    return problems


def lifted_objects(arch: str = "x86_64", root: Path | None = None) -> list[Path]:
    """The kernels' lifted objects from `arch`'s double-mode build (`root`, default ARCHES'),
    checked to be this checkout's double mode (module docstring)."""
    root = ARCHES[arch][1] if root is None else root
    if not (root / "build.jsonl").is_file() or not (root / "summary.json").is_file():
        raise SystemExit(f"no double-mode lifted build at {root}; build it: "
                         f"{rebuild_command(arch, root)}")
    summary = json.loads((root / "summary.json").read_text())
    if summary.get("f80_mode") != "double":
        raise SystemExit(f"{root} is not a double-mode build: {summary.get('f80_mode')}; build "
                         f"it: {rebuild_command(arch, root)}")
    want = {f"0x{va:08x}" for va in KERNELS.values()}
    recs = {}
    for line in (root / "build.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("va") in want:
            recs[r["va"]] = r
    out, problems = [], []
    for va in sorted(want):
        r = recs.get(va)
        if not r or not r.get("compiled"):
            problems.append(f"its build.jsonl has no compiled object for {va}")
            continue
        ir = ROOT / r["ir"]
        if not ir.is_file() or sha256(ir) != r["ir_sha256"]:
            problems.append(f"{r['object']} was compiled from an older {r['ir']}")
        out.append(root / r["object"])
    problems += build_problems(root, summary, arch)
    if problems:
        raise SystemExit(f"the double-mode build {root} is stale: " + "; ".join(problems)
                         + f"; rebuild it: {rebuild_command(arch, root)}")
    return out


def run(cmd: list[str], cwd: Path | None = None) -> None:
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(OUT / "zig-global"),
               ZIG_LOCAL_CACHE_DIR=str(OUT / "zig-local"))
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)
    if p.returncode:
        raise SystemExit(f"failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-4000:]}")


def build(variant: str = "double", arch: str = "x86_64") -> Path:
    """The harness binary for `variant` ("double", or "nomarker": no sa2_f80_state_double) on
    `arch` (ARCHES)."""
    if variant not in ("double", "nomarker") or arch not in ARCHES:
        raise SystemExit(f"unknown variant {variant} or arch {arch}")
    triple, root, shims_obj, rt_extra, _ = ARCHES[arch]
    target = ["-target", triple]
    wraps = [wrap_symbol(va) for va in KERNELS.values()]
    missing = [w for w in wraps if w not in L4.PRODUCTION_WRAPS]
    if missing or RUNTIME_FILE not in L4.RUNTIME_C:
        raise SystemExit(f"tools/s4/link_s4.py does not link the native kernels: wraps missing "
                         f"{missing}, {RUNTIME_FILE} in RUNTIME_C: {RUNTIME_FILE in L4.RUNTIME_C}")
    d = OUT / f"{arch}-{variant}"
    d.mkdir(parents=True, exist_ok=True)
    zig = str(L4.S.ZIG)
    # The runtime file as link_s4.py's compile phase compiles a RUNTIME_C source (with the
    # Android link's -ffp-contract=off for aarch64).
    rt_flags = target + [L4.OPT, "-w", *rt_extra, "-c", "-I", str(L4.RUNTIME),
                         "-I", str(L4.C.REMILL_INC)]
    objs = [d / "sa2_cri_dsp.o", d / "cri_dsp_diff.o", d / "state_layout.o"]
    run([zig, "cc", *rt_flags, str(L4.RUNTIME / RUNTIME_FILE), "-o", str(objs[0])])
    hflags = target + rt_extra + ["-O2", "-Wall", "-Wextra", "-Werror"]
    run([zig, "cc", *hflags, "-c", str(HERE / "cri_dsp_diff.c"), "-o", str(objs[1])])
    run([zig, "c++", *target, *rt_extra, "-O2", "-std=c++17", "-w", "-I", str(L4.C.REMILL_INC), "-c",
         str(HERE / "state_layout.cpp"), "-o", str(objs[2])])
    lifted = lifted_objects(arch)
    if variant == "nomarker":
        # The double mode's shims with the marker renamed away: the lifted objects still reach
        # sa2_df80_pc_round_slow, and the runtime's weak reference to sa2_f80_state_double
        # stays NULL, as in the exact build.
        shims_obj = d / "f80_shims_nomarker.o"
        run([zig, "cc", *target, "-O2", "-w", "-ffp-contract=off",
             "-Dsa2_f80_state_double=sa2_f80_state_double_renamed_away", "-c", str(SHIMS_SRC),
             "-o", str(shims_obj)])
    elif shims_obj is None:
        shims_obj = d / "f80_shims.o"
        run([zig, "cc", *target, "-O2", "-w", "-ffp-contract=off", "-c", str(SHIMS_SRC),
             "-o", str(shims_obj)])
    shims = [shims_obj]
    if "windows" in triple:
        # tools/s7/win_link.py's own link: zig's lld-link line for its probe, with `-wrap:` for
        # each wrap and these objects in the probe's place (zig's -Wl,-wrap= does not reach
        # lld-link's COFF driver).
        sys.path.insert(0, str(ROOT / "tools" / "s7"))
        import win_link as WL  # noqa: PLC0415  read-only
        exe = d / "cri_dsp_diff.exe"
        probe = WL.probe_line(d / "probe")
        line = WL.link_line(probe, d / "probe" / "probe.o", objs + lifted + shims, exe, wraps)
        rsp = d / "link.rsp"
        rsp.write_text(WL.response_file(line))
        run([zig, "lld-link", f"@{rsp}"], cwd=d)
        return exe
    exe = d / "cri_dsp_diff"
    run([zig, "c++", *target, "-O2", "-static", *[f"-Wl,-wrap={w}" for w in wraps],
         *map(str, objs + lifted + shims), "-o", str(exe)])
    return exe


def runner(exe: Path) -> tuple[list[str], dict]:
    """The command prefix and environment that run a harness binary: qemu for an aarch64 one,
    wine (no display, no debug channels, its own prefix) for a Windows one."""
    arch = exe.parent.name.rsplit("-", 1)[0]
    env = dict(os.environ)
    if arch == "x86_64-windows":
        wine_prefix().mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in env.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d",
                   WINEPREFIX=str(wine_prefix()))
    return (ARCHES[arch][4] if arch in ARCHES else []), env


def run_harness(exe: Path, *args: str, timeout: float = 900) -> dict:
    prefix, env = runner(exe)
    p = subprocess.run([*prefix, str(exe), *args], capture_output=True, text=True,
                       timeout=timeout, env=env)
    if p.returncode:
        raise SystemExit(f"{exe} {' '.join(args)} failed ({p.returncode}): {p.stderr[-3000:]}")
    return json.loads(p.stdout)


STATS_LINE = re.compile(r"sa2_cri_dsp: (\w+) 0x[0-9A-Fa-f]{8} native (\d+) lifted (\d+) \(off (\d+) "
                        r"memory (\d+) overlap (\d+) value (\d+) size (\d+)\)")
STATS_NAMES = {"biquad": "biquad", "comb": "comb", "network": "net", "mix": "mix"}


def stats(exe: Path, mask: str | None) -> dict:
    """The harness's `calls` with SA2_CRI_DSP=`mask` (None: unset) and SA2_CRI_DSP_STATS=1: per
    kernel, the counters the runtime prints at exit ({"native", "lifted", "off", ...}), and the
    harness's own reading under "harness"."""
    prefix, env = runner(exe)
    env = {k: v for k, v in env.items() if k != "SA2_CRI_DSP"}
    env["SA2_CRI_DSP_STATS"] = "1"
    if mask is not None:
        env["SA2_CRI_DSP"] = mask
    p = subprocess.run([*prefix, str(exe), "calls"], capture_output=True, text=True, timeout=300,
                       env=env)
    if p.returncode:
        raise SystemExit(f"{exe} calls failed ({p.returncode}): {p.stderr[-3000:]}")
    out = {"harness": json.loads(p.stdout)}
    for m in STATS_LINE.finditer(p.stderr):
        n = [int(x) for x in m.groups()[1:]]
        out[STATS_NAMES[m.group(1)]] = dict(zip(
            ("native", "lifted", "off", "memory", "overlap", "value", "size"), n))
    if set(out) != {"harness", *STATS_NAMES.values()}:
        raise SystemExit(f"{exe} calls printed no SA2_CRI_DSP_STATS lines: {p.stderr[-2000:]}")
    return out


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kernels", default=",".join(KERNELS))
    ap.add_argument("--variant", default="double", choices=("double", "nomarker"))
    ap.add_argument("--arch", default="x86_64", choices=sorted(ARCHES))
    ap.add_argument("--json", type=Path)
    ap.add_argument("--out", type=Path, help="the harness builds' directory (default %(default)s)",
                    default=OUT)
    a = ap.parse_args()
    OUT = a.out.resolve()
    exe = build(a.variant, a.arch)
    res = {"variant": a.variant, "arch": a.arch, "binary": str(exe),
           "layout": run_harness(exe, "layout"), "kernels": {}}
    for k in [k for k in a.kernels.split(",") if k]:
        res["kernels"][k] = run_harness(exe, k, str(a.cases), str(a.seed))
        print(json.dumps(res["kernels"][k]), flush=True)
    ok = res["layout"]["ok"] and all(r["mismatches"] == 0 for r in res["kernels"].values())
    res["ok"] = ok
    if a.json:
        a.json.write_text(json.dumps(res, indent=1) + "\n")
    print("CRI_DSP_DIFF_OK" if ok else "CRI_DSP_DIFF_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
