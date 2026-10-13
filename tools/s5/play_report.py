#!/usr/bin/env python3
"""Hold one run of the DXVK player to its report, as a second layer over the player's own verdict.

Subtitle: `host/player/sa2_player --report R` writes its verdict ("pass",
"failures", "exit_status") beside the raw counts it came from. This tool
recomputes the verdict from the raw counts alone, requires the player's verdict
and its exit status to agree with it, and turns a run that died (a signal, or a
report the player never finished) into a failure that names where it stopped
and how many Presents it had replayed, as closely as the report can say. It
holds the player to what this file says, not only to what the report says: the
allow-list's two rule names, each tied to the one method and the one host answer
it may excuse, the arithmetic of the host answers that failed, and (with
--dxvk-log) the literal DXVK option set. With --frames-out it also holds the
frames file and its times file to the report (count, bytes, and the digest of
every frame's fingerprint), and with --dxvk-log it reads DXVK's own log for the
run into the verdict: the DXVK version, the device and driver, the config file
DXVK found, and the options it applied, which must be exactly
PINNED_DXVK_OPTIONS. The log must be the one the run read: its first bytes must
hash to what the report pins (the player removes an earlier run's log when it
starts, and DXVK only appends).

tools/s5/frames_to_video.py encode runs this same check on the report of the
run whose frames it encodes, so no video rests on the player's "pass" alone.

Usage:
  .venv/bin/python tools/s5/play_report.py check --report OUT/stage3.json --exit 0 \\
      [--frames-out OUT/frames.y4m] [--dxvk-log OUT/stage3.dxvk] [--out OUT/stage3.check.json]

Prints one verdict line, then one line per failure; exit 0 when the run passes,
1 when it does not, 2 on bad usage or a refused --out. play_stream.sh and
test_synth.sh run it after every stage; Documentation/scripts/tests/test_s5_play.py
holds it to forged reports without a GPU.

refuse_output() is the output-path rule the player applies (AGENTS.md rule 9),
shared with tools/s5/frames_to_video.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "sa2-d3d9-stream/v1"
VERSION = 1
# A stream with audio (host/sa2_stream.h, "Versions"): version 2, its own schema.
SCHEMA_AUDIO = "sa2-d3d9-stream/v2"
VERSION_AUDIO = 2
SCHEMAS = {VERSION: SCHEMA, VERSION_AUDIO: SCHEMA_AUDIO}
EXPECTED_STOPS = ("end of stream", "--max-frames")
# The scene-order rules host/player/player.cpp allows a host failure under
# (device_call, BeginScene and EndScene), each with the one method it covers.
# Either excuses one host answer only, D3DERR_INVALIDCALL. A report naming any
# other rule, a rule counting more failures than its method's row has, or an
# allowed call on another method or with another answer fails the check.
ALLOWED_RULES = {
    "scene order: BeginScene inside a scene": ("Device", "BeginScene"),
    "scene order: EndScene with no scene open": ("Device", "EndScene"),
}
ALLOWED_HOST_HR = "0x8876086c"
# How player.cpp count() lists an allowed call: "seq N Type::Method: host 0x..,
# guest 0x.. (rule)"; the report keeps the first 20.
ALLOWED_CALL = re.compile(r"seq \d+ (\w+)::(\w+): host (0x[0-9a-f]{8}), guest 0x[0-9a-f]{8} \((.*)\)")
ALLOWED_CALLS_KEPT = 20
# DXVK's effective configuration for every capture: host/player/dxvk.conf's
# options, written out here so that a run pointed at another file, or a
# changed dxvk.conf, fails the check (test_s5_play.py holds the tracked file
# to this set).
PINNED_DXVK_OPTIONS = sorted([
    "d3d9.floatEmulation = Strict",
    "d3d9.maxFrameLatency = 0",
    "d3d9.maxFrameRate = 0",
    "d3d9.presentInterval = -1",
    "d3d9.shaderModel = 3",
    "d3d9.useFP16 = False",
    "dxvk.tearFree = Auto",
])


def refuse_output(path: str | Path) -> str | None:
    """Why writing `path` is refused, or None when it is allowed: inside a git
    working tree (the nearest ancestor holding .git, a directory or a
    worktree's file), only its private/ may hold outputs. Symlinks are resolved
    first, so a worktree's private/ link counts as the main tree's private/.
    host/player/player.cpp applies the same rule (refuse_output)."""
    resolved = Path(path).expanduser().absolute().resolve()
    for d in resolved.parents:
        if (d / ".git").exists():
            if resolved.is_relative_to(d / "private"):
                return None
            return (f"{path} resolves to {resolved}, inside the working tree {d} and outside its private/ "
                    f"(AGENTS.md rule 9: game frames are captures)")
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def bgrx_digest(times: Path) -> tuple[int, int]:
    """(rows, digest) of a player times file: FNV-1a 64 over each row's
    bgrx_hash, 8 bytes little-endian (host/player/player.cpp, digest_step).
    ValueError, naming the row, for a file without the column or a row whose
    hash is not a 64-bit number."""
    d, n = 0xcbf29ce484222325, 0
    with times.open() as f:
        names = f.readline().rstrip("\n").split("\t")
        if "bgrx_hash" not in names:
            raise ValueError(f"{times}: no bgrx_hash column in {names}")
        col = names.index("bgrx_hash")
        for row, line in enumerate(f, start=2):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            try:
                v = int(fields[col], 0)
            except (IndexError, ValueError):
                v = -1
            if not 0 <= v < 1 << 64:
                raise ValueError(f"{times}: line {row} has no 64-bit bgrx_hash: {line.rstrip()[:80]!r}")
            for i in range(8):
                d ^= (v >> (8 * i)) & 0xFF
                d = (d * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
            n += 1
    return n, d


def times_problem(times: Path) -> str | None:
    """Why a times file cannot be read as the player writes it, or None."""
    try:
        bgrx_digest(times)
    except ValueError as exc:
        return str(exc)
    return None


def conf_options(path: Path) -> list[str]:
    """`key = value` lines of a dxvk.conf, comments and blanks dropped."""
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out.append(f"{k.strip()} = {v.strip()}")
    return sorted(out)


def dxvk_log(directory: Path, name: str | None = None) -> dict:
    """What DXVK logged at info about itself, the device and its configuration:
    from the log called `name` in `directory` (the one the report names), or
    else from the first *_d3d9.log there."""
    logs = [p for p in ([directory / name] if name else sorted(directory.glob("*_d3d9.log"))) if p.is_file()]
    info: dict = {"files": [str(p) for p in logs], "effective_configuration": [], "found_devices": []}
    if not logs:
        return info
    in_config = False
    for raw in logs[0].read_text(errors="replace").splitlines():
        line = raw.rstrip()
        body = line[len("info:  "):] if line.startswith("info:  ") else None
        if body is None:
            in_config = False
            continue
        if in_config and body.startswith("  ") and "=" in body:
            k, v = body.strip().split("=", 1)
            info["effective_configuration"].append(f"{k.strip()} = {v.strip()}")
            continue
        in_config = False
        if body.startswith("DXVK: v"):
            info.setdefault("dxvk_version", body[len("DXVK: "):])
        elif body.startswith("Found config file: "):
            info["config_file"] = body[len("Found config file: "):]
        elif body.startswith("Effective configuration:"):
            in_config = True
        elif body.startswith("Found device: "):
            info["found_devices"].append(body[len("Found device: "):])
        elif body.strip().startswith("Driver") and ":" in body:
            info.setdefault("driver", body.split(":", 1)[1].strip())
    info["effective_configuration"].sort()
    return info


def log_not_the_runs(log: Path, pinned: dict) -> str | None:
    """Why `log` is not the DXVK log the run read, or None: its first
    pinned["log_bytes_read"] bytes must hash to pinned["log_sha256"]."""
    n, sha = pinned.get("log_bytes_read"), pinned.get("log_sha256")
    if not isinstance(n, int) or not isinstance(sha, str):
        return "the report pins no DXVK log (DXVK_LOG_PATH unset, or no log when the run ended), so none can be held to it"
    with log.open("rb") as f:
        head = f.read(n)
    if len(head) != n or hashlib.sha256(head).hexdigest() != sha:
        return f"{log} is not the log this run read: its first {n} bytes do not hash to {sha[:16]}..."
    return None


def not_handled_names(rep: dict) -> list[str]:
    """Each method or entry the report counts not handled, with its count."""
    return [f"{m.get('type')}::{m.get('method')} {m.get('not_handled')}"
            for m in rep.get("methods", []) if m.get("not_handled")]


def presents_at_death(rep: dict | None) -> str:
    """How many Presents a player that died had made, as closely as its report
    says. A final report, or one written before the replay began (phases start,
    Direct3DCreate9, CreateDevice: no Present comes before the device), is
    exact. A report written during the replay counts K Presents, and the player
    rewrites it after every Nth (options.report_every), so the run had made at
    least K and at most K + N; with N = 0 it is not rewritten during the replay
    and only K is known."""
    k = (rep or {}).get("presents")
    if not isinstance(k, int):
        return "an unknown number of presents"
    if rep.get("complete") is True or rep.get("phase") != "replay":
        return f"{k} presents"
    every = (rep.get("options") or {}).get("report_every")
    if not isinstance(every, int):
        return f"at least {k} presents (the report does not say how often it was rewritten)"
    if every == 0:
        return f"at least {k} presents (--report-every 0: the report is not rewritten during the replay)"
    how = "after every Present" if every == 1 else f"every {every} Presents"
    return f"{k} to {k + every} presents (the report is rewritten {how})"


def classes(rep: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    """The failure classes the player's exit status is built from (2, 3, 4, 1),
    recomputed from the report's raw counts. A count the report lacks fails."""
    opts = rep.get("options", {})
    records = rep.get("records", {})
    stop = rep.get("stop")
    stats_only = bool(opts.get("stats_only"))
    basic = [f for f in rep.get("failures", []) if f.startswith(("i/o:", "usage:"))]
    stream: list[str] = []
    version = rep.get("stream_version")
    if version not in SCHEMAS:
        stream.append(f"stream version {version}, not {VERSION} or {VERSION_AUDIO}")
    elif rep.get("stream_schema") != SCHEMAS[version]:
        stream.append(f"schema {rep.get('stream_schema')!r}, not {SCHEMAS[version]!r}")
    if rep.get("stream_problems", {}).get("count", 1):
        stream.append(f"{rep.get('stream_problems', {}).get('count')} stream problem(s): "
                      f"{rep.get('stream_problems', {}).get('first', [])[:3]}")
    for key in ("seq_gaps", "after_end", "unknown"):
        if records.get(key, 1):
            stream.append(f"records.{key} is {records.get(key)}")
    if rep.get("presents_without_frame_record", 1):
        stream.append(f"{rep.get('presents_without_frame_record')} Present(s) without a FRAME record")
    if stop == "end of stream" and records.get("end") != 1 and not opts.get("allow_truncated"):
        stream.append(f"{records.get('end')} END record(s) at the end of the stream")
    if records.get("note", 1) and not opts.get("allow_notes"):
        stream.append(f"{records.get('note')} NOTE record(s), and no --allow-notes: "
                      f"{(rep.get('notes') or {}).get('first', [])[:3]}")
    replay: list[str] = []
    if not rep.get("presents"):
        replay.append("no Present was replayed" if not stats_only else "the stream holds no Present")
    if rep.get("errors", 1):
        replay.append(f"{rep.get('errors')} error(s): {rep.get('first_errors', [])[:3]}")
    if rep.get("calls", {}).get("not_handled", 1):
        replay.append(f"{rep.get('calls', {}).get('not_handled')} call(s) not handled: {not_handled_names(rep)[:6]}"
                      f"; first values {rep.get('not_handled_values', {}).get('first', [])[:3]}")
    # A device the player could not make, and in a stats read (which makes
    # none) a CreateDevice it could not honour: player.cpp device_failure.
    dev = rep.get("device", {})
    if dev.get("failure") is not None or (not stats_only and dev.get("created") is not True):
        replay.append(f"no device: {dev.get('failure')}")
    if not stats_only:
        if rep.get("host_failures", {}).get("count", 1):
            replay.append(f"{rep.get('host_failures', {}).get('count')} host failure(s): "
                          f"{rep.get('host_failures', {}).get('first', [])[:3]}")
        for key in ("readback_failures", "present_failures"):
            if rep.get(key, 1):
                replay.append(f"{key} is {rep.get(key)}")
        if stop not in EXPECTED_STOPS:
            replay.append(f"the run stopped: {stop}")
        fo = rep.get("frames_out")
        if fo and fo.get("frames") != rep.get("presents"):
            replay.append(f"{fo.get('frames')} frame(s) written for {rep.get('presents')} Present(s)")
    elif stop not in EXPECTED_STOPS:
        replay.append(f"the read stopped: {stop}")
    # Sound, when the run asked for it (--audio, --audio-wav): the mixer's problems, a
    # device that did not open, and the report's account of the WAV it wrote.
    if opts.get("audio") or opts.get("audio_wav"):
        au = rep.get("audio")
        if not isinstance(au, dict):
            replay.append("the run asked for sound and the report has no audio account")
        else:
            if (au.get("problems") or {}).get("count", 1):
                replay.append(f"audio: {(au.get('problems') or {}).get('count')} problem(s): "
                              f"{(au.get('problems') or {}).get('first', [])[:3]}")
            dev = au.get("device")
            if opts.get("audio") and not (isinstance(dev, dict) and dev.get("open") is True
                                          and dev.get("failure") is None):
                replay.append(f"audio: no playback device: {(dev or {}).get('failure')}")
    if stop == "--max-frames" and rep.get("presents") != opts.get("max_frames"):
        replay.append(f"--max-frames {opts.get('max_frames')} but {rep.get('presents')} presents")
    exp = rep.get("expectations", {})
    expect: list[str] = []
    if exp.get("fail", 1):
        expect.append(f"{exp.get('fail')} expectation(s) failed")
    if exp.get("file") and not exp.get("selected"):
        expect.append(f"{exp.get('file')} selected no expectation")
    return basic, stream, replay, expect


def allow_list_problems(rep: dict) -> list[str]:
    """The allow-list held to ALLOWED_RULES, each rule to its own method, and
    the host answers that failed held to their sum.

    Each rule may excuse only its method's failures, so a rule's count cannot
    exceed the failed answers on that method's row, and every allowed call the
    report lists must be on that method with host answer ALLOWED_HOST_HR. Every
    failed answer is counted once on its method's row, and once as a host
    failure, a failure like the guest's, an allowed call or a failed Present
    (player.cpp count() and do_present)."""
    problems = []
    allowed = rep.get("allowed_host_failures")
    rules = allowed.get("rules") if isinstance(allowed, dict) else None
    if not isinstance(rules, dict):
        return ["allow-list: the report has no allowed_host_failures.rules"]
    unknown = sorted(set(rules) - set(ALLOWED_RULES))
    if unknown:
        problems.append(f"allow-list: the report allows under {unknown}, which are not the player's rules "
                        f"{sorted(ALLOWED_RULES)}")
    failed_on = {(m.get("type"), m.get("method")): int(m.get("failed", 0)) for m in rep.get("methods", [])}
    for rule, n in sorted(rules.items()):
        if rule in ALLOWED_RULES and isinstance(n, int) and n > failed_on.get(ALLOWED_RULES[rule], 0):
            problems.append(f"allow-list: {rule!r} allows {n} failure(s); {'::'.join(ALLOWED_RULES[rule])} failed "
                            f"{failed_on.get(ALLOWED_RULES[rule], 0)} time(s)")
    calls = allowed.get("calls")
    listed = calls if isinstance(calls, list) else []
    total = sum(n for n in rules.values() if isinstance(n, int))
    if len(listed) != min(total, ALLOWED_CALLS_KEPT):
        problems.append(f"allow-list: {len(listed)} allowed call(s) listed for {total} allowed")
    for call in listed:
        m = ALLOWED_CALL.fullmatch(call) if isinstance(call, str) else None
        if not m or ALLOWED_RULES.get(m.group(4)) != (m.group(1), m.group(2)) or m.group(3) != ALLOWED_HOST_HR:
            problems.append(f"allow-list: {call!r} is not a {ALLOWED_HOST_HR} answer to the one method its rule covers")
    hf = rep.get("host_failures", {})
    failed = sum(int(m.get("failed", 0)) for m in rep.get("methods", []))
    parts = {"host_failures.count": hf.get("count"), "host_failures.failed_like_guest": hf.get("failed_like_guest"),
             "allowed": sum(rules.values()) if all(isinstance(v, int) for v in rules.values()) else None,
             "present_failures": rep.get("present_failures")}
    if any(not isinstance(v, int) for v in parts.values()):
        problems.append(f"allow-list: counts missing from the report: {parts}")
    elif failed != sum(parts.values()):
        problems.append(f"allow-list: the methods' failed answers sum to {failed}; {parts} sum to "
                        f"{sum(parts.values())}")
    return problems


def check(args) -> dict:
    report_path = Path(args.report)
    status = args.exit
    failures: list[str] = []
    verdict: dict = {"tool": "tools/s5/play_report.py check", "report": str(report_path), "exit_status": status}
    signal = status - 128 if status >= 128 else (-status if status < 0 else 0)
    try:
        rep = json.loads(report_path.read_text())
    except FileNotFoundError:
        rep = None
        failures.append(f"no report: the player wrote none (exit {status})")
    except ValueError as exc:
        rep = None
        failures.append(f"the report is not JSON: {exc}")
    if signal:
        phase = (rep or {}).get("phase", "unknown")
        if phase in ("Direct3DCreate9", "CreateDevice"):
            failures.append(f"device creation aborted: signal {signal} in phase {phase}")
        else:
            failures.append(f"the player died: signal {signal} in phase {phase} after {presents_at_death(rep)}")
    if rep is None:
        verdict.update(pass_=False, failures=failures)
        return verdict
    if rep.get("complete") is not True:
        # A report written before it was final holds the run's inputs and its
        # counts so far, not its outcome: no verdict is recomputed from it.
        failures.append(f"the player did not finish its report: phase {rep.get('phase')}, last written at "
                        f"{rep.get('presents')} presents")
        verdict.update(stream=rep.get("stream"), stage=rep.get("stage"), presents=rep.get("presents"),
                       phase=rep.get("phase"), pass_=False, failures=failures)
        return verdict

    basic, stream, replay, expect = classes(rep)
    want_exit = 2 if basic else 3 if stream else 4 if replay else 1 if expect else 0
    failures += basic + [f"stream: {s}" for s in stream] + [f"replay: {s}" for s in replay] + \
        [f"expect: {s}" for s in expect]
    if (rep.get("pass") is True) != (want_exit == 0):
        failures.append(f"the report says pass={rep.get('pass')}; its counts say exit {want_exit}")
    if rep.get("exit_status") != status and not signal:
        failures.append(f"the report says exit {rep.get('exit_status')}; the process exited {status}")
    if status != want_exit and not signal:
        failures.append(f"the process exited {status}; the report's counts say {want_exit}")
    failures += allow_list_problems(rep)

    # The frames file and its times, held to the run that wrote them.
    if args.frames_out:
        frames = Path(args.frames_out)
        times = Path(str(frames) + ".times.tsv")
        fo = rep.get("frames_out") or {}
        if not fo:
            failures.append("frames: the report has no frames_out record")
        elif not (frames.exists() and times.exists()):
            failures.append(f"frames: {frames} or its times file is missing")
        elif (bad := times_problem(times)) is not None:
            failures.append(f"frames: {bad}")
        else:
            rows, digest = bgrx_digest(times)
            if Path(fo.get("path", "")).resolve() != frames.resolve():
                failures.append(f"frames: the report wrote {fo.get('path')}, not {frames}")
            if fo.get("y4m_bytes") != frames.stat().st_size:
                failures.append(f"frames: {frames.stat().st_size} bytes; the report says {fo.get('y4m_bytes')}")
            if not (rows == fo.get("times_rows") == fo.get("frames")):
                failures.append(f"frames: {rows} times rows; the report says {fo.get('times_rows')} rows and "
                                f"{fo.get('frames')} frames")
            if f"0x{digest:016x}" != fo.get("bgrx_digest"):
                failures.append(f"frames: digest 0x{digest:016x}; the report says {fo.get('bgrx_digest')}")
            verdict["frames"] = {"rows": rows, "bgrx_digest": f"0x{digest:016x}", "bytes": frames.stat().st_size}

    # DXVK's own account of the run, held to the run and to the pinned option set.
    if args.dxvk_log:
        pinned = rep.get("dxvk") or {}
        log = dxvk_log(Path(args.dxvk_log), Path(pinned["log"]).name if pinned.get("log") else None)
        verdict["dxvk"] = log
        conf = rep.get("files", {}).get("dxvk_conf") or {}
        if not log["files"]:
            failures.append(f"dxvk: no DXVK log in {args.dxvk_log}")
        elif "dxvk_version" not in log:
            failures.append("dxvk: the log has no version line (was DXVK_LOG_LEVEL below info?)")
        elif rep.get("device", {}).get("created"):
            if (why := log_not_the_runs(Path(log["files"][0]), pinned)) is not None:
                failures.append(f"dxvk: {why}")
            real = conf.get("real_path")
            if log["effective_configuration"] != PINNED_DXVK_OPTIONS:
                failures.append(f"dxvk: DXVK applied {log['effective_configuration']}; the pinned options are "
                                f"{PINNED_DXVK_OPTIONS}")
            if not real or Path(log.get("config_file", "")).resolve() != Path(real).resolve():
                failures.append(f"dxvk: DXVK read {log.get('config_file')}; the report pins {real}")
            elif conf.get("sha256") != sha256_file(Path(real)):
                failures.append("dxvk: the config file changed after the run pinned it")
            elif conf_options(Path(real)) != PINNED_DXVK_OPTIONS:
                failures.append(f"dxvk: {real} holds {conf_options(Path(real))}; the pinned options are "
                                f"{PINNED_DXVK_OPTIONS}")
    verdict["stream"] = rep.get("stream")
    verdict["stream_sha256"] = rep.get("stream_sha256")
    verdict["stage"] = rep.get("stage")
    verdict["stats_only"] = bool(rep.get("options", {}).get("stats_only"))
    verdict["presents"] = rep.get("presents")
    verdict["notes"] = rep.get("notes")
    verdict["allowed_host_failures"] = rep.get("allowed_host_failures")
    verdict["live_desktop_session"] = rep.get("host", {}).get("live_desktop_session")
    verdict["live_desktop_session_basis"] = rep.get("host", {}).get("live_desktop_session_basis")
    verdict["pass_"] = not failures
    verdict["failures"] = failures
    return verdict


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="hold one player run to its report")
    c.add_argument("--report", required=True)
    c.add_argument("--exit", type=int, required=True, help="the player's exit status (128+N for signal N)")
    c.add_argument("--frames-out", help="the run's --frames-out .y4m")
    c.add_argument("--dxvk-log", help="the run's DXVK_LOG_PATH directory")
    c.add_argument("--out", help="write the verdict here as JSON")
    args = parser.parse_args(argv)
    if args.out and (why := refuse_output(args.out)):
        print(f"refused: {why}", file=sys.stderr)
        return 2
    verdict = check(args)
    verdict["pass"] = verdict.pop("pass_")
    if args.out:
        Path(args.out).write_text(json.dumps(verdict, indent=2) + "\n")
    stage = "stats only" if verdict.get("stats_only") else f"stage {verdict.get('stage')}"
    print(f"check {args.report}: {'PASS' if verdict['pass'] else 'FAIL'} ({stage}, exit {args.exit}, "
          f"presents {verdict.get('presents')})")
    for f in verdict["failures"]:
        print(f"  failure: {f}")
    notes = verdict.get("notes") or {}
    if notes.get("count"):
        print(f"  notes: {notes.get('count')} (allowed: {notes.get('allowed')}); first {notes.get('first', [])[:3]}")
    dx = verdict.get("dxvk") or {}
    if dx.get("dxvk_version"):
        print(f"  dxvk {dx.get('dxvk_version')}; driver {dx.get('driver')}; options {dx.get('effective_configuration')}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
