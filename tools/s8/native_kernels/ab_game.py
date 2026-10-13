#!/usr/bin/env python3
"""ab_game: Metal Harbor's replay with the runtime's native kernels against the same build without.

Subtitle: tools/s8/perf_game.py's build and run, for an A/B of runtime/sa2_cri_dsp.c. `build`
links the replay as perf_game does, with the runtime as the tree has it (`--kernels on`) or with
sa2_cri_dsp.c and its three wraps left out of tools/s4/link_s4.py's lists (`--kernels off`), so
the off build is the lifted code alone. `run` runs one build through perf_game's run (under
tools/slot.sh), with the recorder's stream (--rec) read by stream_audio_hash.c into per-kind and
per-audio-op digests, and optionally the per-thread /proc monitor of the S8 serial study.
`compare` holds two runs' stream digests to each other.

    source tools/env.sh; export SDL_AUDIO_DRIVER=dummy
    .venv/bin/python tools/s8/native_kernels/ab_game.py build --kernels on|off --variant plain|rec \\
        --name NAME [--lifted private/generated/s8/pc-double/linux-obj]
    .venv/bin/python tools/s8/native_kernels/ab_game.py run --build NAME --label LABEL [--rec]
        [--instrument] [--procmon] [--env VAR=VALUE ...]
    .venv/bin/python tools/s8/native_kernels/ab_game.py compare LABEL_A LABEL_B

Builds land where perf_game puts them (private/generated/s8/perf-game/); runs under
private/generated/s8/cri-dsp-native/runs/<LABEL>/ with <LABEL>.stream.json and
<LABEL>.procmon.jsonl beside it (`--out DIR`, before the command: DIR/runs/ instead, and DIR's
recorder/ and bin/). A recorder build compiles host/sa2_present_rec.c and host/sa2_stream.h as
HEAD has them (copied into private/generated/s8/cri-dsp-native/recorder/), so an A and a B built
while the working tree's recorder is being edited record with the same writer.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s8"))
import perf_game as PG  # noqa: E402

GEN = ROOT / "private" / "generated"
OUT = GEN / "s8" / "cri-dsp-native"
RUNS = OUT / "runs"
LIFTED = GEN / "s8" / "pc-double" / "linux-obj"
KERNEL_FILE = "sa2_cri_dsp.c"
KERNEL_WRAPS = ("sub_842500", "sub_843400", "sub_843460", "sub_8418b0")
PROCMON = GEN / "s8" / "serial-study" / "procmon.py"


def frozen_recorder() -> Path:
    """host/sa2_present_rec.c and host/sa2_stream.h as HEAD has them, in a directory of their own
    (the recorder includes sa2_stream.h from its own directory, the runtime's headers by -I)."""
    d = OUT / "recorder"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("sa2_present_rec.c", "sa2_stream.h"):
        text = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:host/{name}"],
                              capture_output=True, check=True).stdout
        p = d / name
        if not p.is_file() or p.read_bytes() != text:
            p.write_bytes(text)
    return d / "sa2_present_rec.c"


def cmd_build(a) -> int:
    load = PG.load_link

    def link_without_kernels():
        link = load()
        if a.kernels == "off":
            link.RUNTIME_C = [s for s in link.RUNTIME_C if s != KERNEL_FILE]
            link.PRODUCTION_WRAPS = [w for w in link.PRODUCTION_WRAPS if w not in KERNEL_WRAPS]
        elif KERNEL_FILE not in link.RUNTIME_C:
            raise SystemExit(f"--kernels on, but tools/s4/link_s4.py does not compile {KERNEL_FILE}")
        return link

    PG.load_link = link_without_kernels
    if a.variant == "rec":
        PG.RECORDER = frozen_recorder()
    ns = argparse.Namespace(variant=a.variant, scene="metalharbor", name=a.name,
                            lifted=a.lifted, rebuild=a.rebuild, shims=None)
    rc = PG.cmd_build(ns)
    info_p = PG.PG / "builds" / f"{a.name}.json"
    if rc == 0 and info_p.is_file():
        info = json.loads(info_p.read_text())
        info["cri_dsp_kernels"] = a.kernels
        info["recorder_source"] = str(PG.RECORDER)
        info_p.write_text(json.dumps(info, indent=1) + "\n")
    return rc


def hasher() -> Path:
    exe = OUT / "bin" / "stream_audio_hash"
    src = HERE / "stream_audio_hash.c"
    if not exe.is_file() or exe.stat().st_mtime < src.stat().st_mtime:
        exe.parent.mkdir(parents=True, exist_ok=True)
        sys.path[:0] = [str(ROOT / "tools" / "spike")]
        import spikelib as S  # noqa: PLC0415
        subprocess.run([str(S.ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-Wall", "-Werror",
                        str(src), "-o", str(exe)], check=True)
    return exe


def cmd_run(a) -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    info = json.loads((PG.PG / "builds" / f"{a.build}.json").read_text())
    procs = []
    env_args = list(a.env or [])
    stream_json = RUNS / f"{a.label}.stream.json"
    if a.rec:
        fifo = RUNS / f"{a.label}.stream.fifo"
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(fifo)
        stream_json.unlink(missing_ok=True)
        procs.append(("stream", subprocess.Popen([str(hasher()), str(fifo), str(stream_json)])))
        env_args.append(f"SA2_REC_TRACE={fifo}")
    if a.procmon:
        mon = RUNS / f"{a.label}.procmon.jsonl"
        procs.append(("procmon", subprocess.Popen([sys.executable, str(PROCMON), info["binary"],
                                                   str(mon), "--wait-s", "7200"])))
    cmd = [str(ROOT / "tools" / "slot.sh"), sys.executable, "-u", str(ROOT / "tools/s8/perf_game.py"),
           "run", "--build", a.build, "--label", os.path.relpath(RUNS / a.label, PG.PG / "runs")]
    cmd += ["--rec"] if a.rec else []
    cmd += ["--instrument"] if a.instrument else []
    for e in env_args:
        cmd += ["--env", e]
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    for name, p in procs:
        try:
            p.wait(timeout=120 if name == "stream" else 30)
        except subprocess.TimeoutExpired:
            p.kill()
    res = {"label": a.label, "build": a.build, "rc": rc, "wall_seconds": round(time.time() - t0, 1),
           "kernels": info.get("cri_dsp_kernels")}
    rj = RUNS / a.label / "result.json"
    if rj.is_file():
        res["perf_game"] = json.loads(rj.read_text())
    if a.rec and stream_json.is_file():
        res["stream"] = json.loads(stream_json.read_text())
    (RUNS / f"{a.label}.ab.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "stream"}, indent=1))
    return rc


def cmd_compare(a) -> int:
    sa, sb = (json.loads((RUNS / f"{x}.stream.json").read_text()) for x in (a.a, a.b))
    keys = ["records", "records_digest", "payload_bytes", "kinds", "xa_submit_pcm",
            "ds_unlock_pcm", "audio_ops", "ended", "problems"]
    diff = {k: [sa.get(k), sb.get(k)] for k in keys if sa.get(k) != sb.get(k)}
    out = {"a": a.a, "b": a.b, "equal": not diff, "differ": diff,
           "xa_submit_pcm": sa["xa_submit_pcm"], "records": sa["records"],
           "audio_records": sa["kinds"]["audio"]["count"]}
    print(json.dumps(out, indent=1))
    return 0 if not diff else 1


def main(argv=None) -> int:
    global OUT, RUNS
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT,
                    help="the runs', recorder's and hasher's directory (default %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--kernels", choices=("on", "off"), required=True)
    b.add_argument("--variant", choices=("plain", "rec"), default="plain")
    b.add_argument("--name", required=True)
    b.add_argument("--lifted", default=str(LIFTED))
    b.add_argument("--rebuild", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--build", required=True)
    r.add_argument("--label", required=True)
    r.add_argument("--rec", action="store_true")
    r.add_argument("--instrument", action="store_true")
    r.add_argument("--procmon", action="store_true")
    r.add_argument("--env", action="append")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    a = ap.parse_args(argv)
    OUT = a.out.resolve()
    RUNS = OUT / "runs"
    return {"build": cmd_build, "run": cmd_run, "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
