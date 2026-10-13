#!/usr/bin/env python3
"""Play host 2's registered Metal Harbor replay under the Vulkan validation layer, and write its receipt.

The user's ruling of 2026-10-04 (Documentation/decisions/2026-10-04-S5-user-rulings-2.md) has host 2's
debug runs load the validation layers built from the approved sources, because Vulkan misuse a desktop
driver tolerates is what breaks on phones. Documentation/receipts/s5-host2-r1-v1.json showed the shader
loads and the synthetic draws quiet under the layer; this tool runs the game's own draws: the registered
Metal Harbor stream (the one tools/s5/compare_hosts.py and tools/s5/host_compare.py hold), complete.

`run` loads the layer the way host/d3d9gpu/test_d3d9gpu.sh step 5 does: every inherited VK_* variable
cleared (host/d3d9gpu/test/r1_receipt.py vk-env records them and fails on a layer settings file), the
pinned layer chosen by tools/s5/validation_tools.py, SDL_gpu's debug mode (SA2_D3D9GPU_DEBUG=1,
SDL_ASSERT=abort), and the player run under host/d3d9gpu/test/validation_run.py, whose observer layer
proves the selected library is the one mapped into the live process. The player writes each frame's
hash (--frames-out to a link to /dev/null keeps the times file and drops the pixels), so the run is also
held to the registered DXVK reference frame for frame. `--control fp16` is the positive control: the
same harness with a translator fault (a test run) that makes shader modules the layer rejects, so a
zero count is shown to be a count that can be nonzero. The run records the player's build record by
its sha256 beside the player's.

`--no-frames` runs the play path alone (no readback), as host/d3d9gpu/test_d3d9gpu.sh step 5 does: the
readback adds copies and layout transitions of its own, so each path is validated.

`receipt` reads the retained runs, launching nothing, and writes the receipt: the commands, the player,
SDL, layer, observer and ICD identities, the stream, the frames presented and their equality with the
reference, and every validation message by its VUID (the layer reports errors and warnings), with SDL's
own error, warning and assertion lines. HOST2_VALIDATION_MH_OK only when:

  * both complete runs are quiet: no validation error and no validation warning, no SDL "ERROR: " or
    "WARNING: " line and no assertion line, each count read at line start and as a loose substring
    and the two readings agreeing;
  * every leg (the control too) is held to its own record: the observer's live observations of the
    selected library (one per layer startup line, each the whole of validation_run.py's LAYER_STARTUP,
    naming /dev/stderr), the settings file exactly validation_run.py's LAYER_SETTINGS at the path it
    writes it with VK_LAYER_SETTINGS_PATH its directory, no VK_* variable that could set a validation
    feature, the device's fault switches the command's, and the registered Metal Harbor stream
    (compare_hosts.py's sha256) both given to the player and read by it;
  * every leg's inputs are what they were and what is on disk now, as validation_run.py's audit_r1
    holds them: the selected layer is validation_tools.py's selection now, the ICD is the NVIDIA one
    at its current sha256, and the observer passes validation_run.py's probe_identity and is the
    record's (the retained copy of its files held to it); the layer manifest's defaults are read only
    once the file is the selected manifest's sha256;
  * the player is the one its build record names, every one of the build record's sources is this
    tree's, the build record is the one each run recorded, the player's own report names the binary
    on disk, and the SDL library is the one each run loaded;
  * the control counted errors, all of them its fault's VUID, in the same harness, player, layer and
    stream.

A missing or malformed input (a file that is not there or not JSON, a record without a field the
receipt reads) refuses ("refused: ...") rather than writing a receipt.

  source tools/env.sh && .venv/bin/python tools/s5/host2_validation_mh.py run --players PLAYERS --out RUN
  source tools/env.sh && .venv/bin/python tools/s5/host2_validation_mh.py run --players PLAYERS --out PLAY --no-frames
  source tools/env.sh && .venv/bin/python tools/s5/host2_validation_mh.py run --players PLAYERS --out CONTROL \\
      --control fp16 --max-frames 300
  source tools/env.sh && .venv/bin/python tools/s5/host2_validation_mh.py receipt --run RUN --play-run PLAY \\
      --control-run CONTROL --out Documentation/receipts/s5-host2-validation-mh-v1.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import compare_hosts as ch  # noqa: E402
import host_compare as hc  # noqa: E402
import play_report  # noqa: E402
import validation_tools as vt  # noqa: E402

SELF = "tools/s5/host2_validation_mh.py"
SCHEMA = "sa2-s5-host2-validation-mh/v1"
PY = ROOT / ".venv" / "bin" / "python"
VALIDATION_RUN = "host/d3d9gpu/test/validation_run.py"
# validation_run.py's own settings text, startup line, ICD paths and observer audit (probe_identity)
_spec = importlib.util.spec_from_file_location("sa2_validation_run", ROOT / VALIDATION_RUN)
vr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vr)
PROBE_BUILD = "host/d3d9gpu/test/validation_probe/build_validation_probe.sh"
R1_RECEIPT = "host/d3d9gpu/test/r1_receipt.py"
PLAYER_ENV = "host/player/player_env.sh"
LAYER = "VK_LAYER_KHRONOS_validation"
PROBE = "VK_LAYER_SA2_validation_probe"
NVIDIA_ICD = vr.ICDS["nvidia"]
# The files validation_run.py's probe_identity verifies, by the observer record's sha256 field.
PROBE_FILES = {"validation_probe.json": "manifest_sha256", "libVkLayer_sa2_validation_probe.so": "library_sha256",
               "validation_probe_build.json": "build_record_sha256"}
# Each control's translator fault and the VUIDs that fault, and only that fault, makes the layer report.
CONTROLS = {"fp16": {"VUID-VkShaderModuleCreateInfo-pCode-08740"}}
R1_RECEIPT_DOC = "Documentation/receipts/s5-host2-r1-v1.json"
# What produced the run and the receipt, held to the tree by Documentation/scripts/check_receipt_pins.py.
# The player is held by its build record (its sha256 recorded at run time, every source held to the tree
# at receipt time, and every source pinned in the receipt's inputs.pipeline).
PIPELINE = (SELF, VALIDATION_RUN, PROBE_BUILD, "host/d3d9gpu/test/validation_probe/validation_probe.c",
            "host/d3d9gpu/test/validation_probe/validation_probe.json.in", R1_RECEIPT, PLAYER_ENV,
            "tools/s5/validation_tools.py", "tools/s5/validation-sources-350.json", "tools/s5/compare_hosts.py",
            "tools/s5/host_compare.py", "tools/s5/play_report.py", "tools/pins.json")
# The layer's text format (khronos_validation.message_format_json = false): a header line per message,
# then the call, the spec's sentence and the objects on their own lines.
MESSAGE = re.compile(r"^Validation (Error|Warning|Performance Warning|Information): \[ ([^\]]+?) \]"
                     r"(?: \| MessageID = (0x[0-9a-fA-F]+))?")
CALL = re.compile(r"^(vk\w+)\(\)")
SPEC = "The Vulkan spec states: "
SDL_LINE = re.compile(r"^(ERROR|WARNING): ")
ASSERT = re.compile(r"Assertion failure|SDL_assert")
LOGGING = "Validation Layer Info - Logging"
# The only VK_* variables a leg's producer may see: the loader's selection and validation_run.py's own.
# Anything else (VK_LAYER_<setting>, VK_KHRONOS_VALIDATION_<setting>) could set a validation feature.
VK_ENV = {"VK_DRIVER_FILES", "VK_ADD_LAYER_PATH", "VK_INSTANCE_LAYERS", "VK_LAYER_DUPLICATE_MESSAGE_LIMIT",
          "VK_LAYER_SETTINGS_PATH", "VK_LOADER_LAYERS_DISABLE"}
DEFAULT_KEYS = ("validate_core", "check_image_layout", "check_command_buffer", "check_object_in_use", "check_query",
                "check_shaders", "unique_handles", "object_lifetime", "stateless_param", "thread_safety",
                "validate_sync", "gpuav_enable", "printf_enable", "validate_best_practices", "disables", "enables")
FAULT_SWITCHES = ("SA2_D3D9GPU_FAULT", "SA2_D3D9GPU_SHADER_FAULT", "SA2_D3D9GPU_TEST_RUN")


def sha256(path: Path) -> str:
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def digest(path: Path) -> str | None:
    """A file's sha256, or None when it is not there (an identity that fails rather than refuses)."""
    return sha256(path) if Path(path).is_file() else None


def load(path: Path) -> dict:
    """A retained JSON file; a malformed one refuses, naming it, as a missing one does."""
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"refused: {rel(path)} is malformed (not JSON: {e.msg} at line {e.lineno}); "
                         "every input is a retained file") from None


def rel(path: Path | str) -> str:
    p = Path(path).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def lexical(value: str) -> str:
    """A command word with this checkout's prefix taken off, its links left as written."""
    return value[len(str(ROOT)) + 1:] if value.startswith(str(ROOT) + "/") else "." if value == str(ROOT) else value


def registered_stream() -> tuple[Path, str]:
    """The registered Metal Harbor stream: the held recording's, by host_compare.py's pinned hash."""
    recording = ROOT / hc.RECORDING
    if sha256(recording) != hc.RECORDING_SHA256:
        raise SystemExit("refused: the held Metal Harbor recording is not host_compare.py's")
    ready = json.loads(recording.read_text())["ready"]
    if ready["stream_sha256"] != ch.METAL_HARBOR["stream_sha256"]:
        raise SystemExit("refused: the recording's stream is not compare_hosts.py's registered Metal Harbor")
    return ROOT / ready["stream"], ready["stream_sha256"]


def pipeline_now() -> dict:
    return {p: sha256(ROOT / p) for p in PIPELINE}


def build_record_of(player: Path) -> Path:
    return player.parent / (player.name + ".json")


def run(args) -> int:
    players = Path(args.players).resolve(strict=True)
    player = players / ch.PLAYERS["sdlgpu"]
    build_record = build_record_of(player)
    if not build_record.is_file():
        raise SystemExit(f"refused: no build record {rel(build_record)} beside the player")
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f"refused: {rel(out)} exists; every run is a fresh directory")
    stream, stream_sha = registered_stream()
    out.mkdir(parents=True)
    with (out / "setup.log").open("w") as log:
        subprocess.run([str(PY), str(ROOT / R1_RECEIPT), "vk-env", "--out", str(out / "vk-env.json")],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(["bash", str(ROOT / PROBE_BUILD), str(out / "validation-probe")],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    selected = vt.selected_layer(ROOT)
    # Nothing inherited reaches the player that could select a layer, a driver, a device mode or a
    # library: every VK_*, SDL_*, DXVK_* and SA2_* variable but the two player_env.sh reads is dropped.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VK_", "SA2_", "DXVK_", "SDL_")) and k not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "OUT")
           or k in ("SA2_S5_INSTALL", "SA2_REPO")}
    env.update({
        "OUT": str(players), "SA2_PLAY_OUT": str(out / "play-out"), "SA2_DXVK_LOG_PATH": str(out / "dxvk-log"),
        "SDL_AUDIO_DRIVER": "dummy", "SA2_D3D9GPU_DEBUG": "1", "SDL_ASSERT": "abort",
        "SA2_D3D9GPU_REPORT": str(out / "d3d9gpu.json"), "VK_DRIVER_FILES": NVIDIA_ICD,
        "VK_ADD_LAYER_PATH": selected["loader_environment"]["VK_ADD_LAYER_PATH"], "VK_INSTANCE_LAYERS": LAYER,
        "LD_LIBRARY_PATH": selected["loader_environment"]["LD_LIBRARY_PATH"], "SDL_LOGGING": "gpu=info",
        "VK_LAYER_DUPLICATE_MESSAGE_LIMIT": "1000000",
        "SA2_VALIDATION_KEY": selected["key"], "SA2_VALIDATION_PREFIX": selected["prefix"],
        "SA2_VALIDATION_LIBRARY": selected["library"], "SA2_VALIDATION_MANIFEST": selected["manifest"],
        "SA2_VALIDATION_LIBRARY_SHA256": selected["library_sha256"],
        "SA2_VALIDATION_MANIFEST_SHA256": selected["manifest_sha256"],
        "SA2_VALIDATION_API_VERSION": selected["api_version"]})
    if args.control:
        env.update({"SA2_D3D9GPU_TEST_RUN": "1", "SA2_D3D9GPU_SHADER_FAULT": args.control})
    player_args = [str(stream), "--stage", str(ch.METAL_HARBOR["stage"]), "--report", str(out / "report.json")]
    if not args.no_frames:
        frames = out / "frames.y4m"
        frames.symlink_to(os.devnull)
        player_args += ["--frames-out", str(frames)]
    if args.max_frames:
        player_args += ["--max-frames", str(args.max_frames)]
    argv = ["bash", "-c", 'source "$1" && shift && exec "$@"', "sa2-validation-mh", str(ROOT / PLAYER_ENV),
            str(PY), str(ROOT / VALIDATION_RUN), "--root", str(ROOT), "run",
            "--probe-dir", str(out / "validation-probe"), "--record", str(out / "validation.json"),
            "--", str(player), *player_args]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    (out / "command.json").write_text(json.dumps({
        "argv": argv, "started": started, "control": args.control, "max_frames": args.max_frames,
        "readback": not args.no_frames,
        "stream_sha256": stream_sha, "player_sha256": sha256(player),
        "build_record": {"path": str(build_record), "sha256": sha256(build_record)}, "pipeline": pipeline_now(),
        "env": {k: v for k, v in sorted(env.items()) if k.startswith(("VK_", "SA2_", "SDL_", "LD_"))
                or k == "OUT"}}, indent=1) + "\n")
    with (out / "run.txt").open("wb") as f:
        p = subprocess.run(argv, env=env, stdout=f, stderr=subprocess.STDOUT)
    (out / "exit").write_text(f"{p.returncode}\n")
    c = subprocess.run([str(PY), str(ROOT / ch.PLAY_REPORT), "check", "--report", str(out / "report.json"),
                        "--exit", str(p.returncode), "--out", str(out / "check.json")], capture_output=True, text=True)
    (out / "check.txt").write_text(c.stdout + c.stderr)
    summary = scan(out)
    print(f"{rel(out)}: exit {p.returncode}; validation errors {summary['errors']}, warnings "
          f"{summary['warnings']}; SDL error lines {len(summary['sdl']['ERROR'])}, warning lines "
          f"{len(summary['sdl']['WARNING'])}")
    return 0


def scan(out: Path) -> dict:
    """Every layer message in the run's combined output (the player's stdout, then the stderr
    validation_run.py collected), by VUID, with its MessageID, the call and the spec's sentence."""
    text = (out / "run.txt").read_text(errors="replace").splitlines()
    by: dict[str, dict] = {}
    sdl: dict[str, list[str]] = {"ERROR": [], "WARNING": []}
    asserts = []
    for i, line in enumerate(text):
        if m := MESSAGE.match(line):
            key = f"{m.group(1)}|{m.group(2)}"
            row = by.setdefault(key, {"severity": m.group(1), "id": m.group(2), "message_ids": [], "count": 0,
                                      "calls": [], "spec": None})
            row["count"] += 1
            if m.group(3) and m.group(3) not in row["message_ids"]:
                row["message_ids"].append(m.group(3))
            for follow in text[i + 1:i + 8]:
                if MESSAGE.match(follow):
                    break
                if (c := CALL.match(follow)) and c.group(1) not in row["calls"]:
                    row["calls"].append(c.group(1))
                if row["spec"] is None and SPEC in follow:
                    row["spec"] = follow.split(SPEC, 1)[1][:400]
        elif m := SDL_LINE.match(line):
            sdl[m.group(1)].append(line[:300])
        if ASSERT.search(line):
            asserts.append(line[:300])
    errors = sum(r["count"] for r in by.values() if r["severity"] == "Error")
    warnings = sum(r["count"] for r in by.values() if r["severity"] != "Error")
    return {"messages": dict(sorted(by.items())), "errors": errors, "warnings": warnings, "sdl": sdl,
            "sdl_asserts": asserts,
            "sdl_validation_enabled": sum(1 for line in text if line.startswith("Validation layers enabled")),
            "sdl_validation_not_found": sum(1 for line in text if "Validation layers not found" in line),
            "layer_logging_lines": sum(1 for line in text if line.startswith(LOGGING)),
            # the whole line, naming the destination validation_run.py's settings file sets (/dev/stderr)
            "layer_startup_lines": sum(1 for line in text if line == vr.LAYER_STARTUP),
            # test_d3d9gpu.sh's own count (grep -c "Validation Error"), any VUID anywhere and SDL's prefixes
            # anywhere, so a message split or prefixed by another writer on the same pipe is not missed by
            # the line-start parse
            "loose": {"validation_error_lines": sum(1 for line in text if "Validation Error" in line),
                      "validation_warning_lines": sum(1 for line in text if re.search(r"Validation (Warning|Performance Warning|Information)", line)),
                      "vuid_lines": sum(1 for line in text if "VUID-" in line),
                      "sdl_error_lines": sum(1 for line in text if "ERROR: " in line),
                      "sdl_warning_lines": sum(1 for line in text if "WARNING: " in line),
                      "layer_logging_lines": sum(1 for line in text if LOGGING in line)}}


def map_row_shows(row, observed: dict) -> bool:
    """One /proc/<pid>/maps line naming the observed file: its device, inode and path."""
    try:
        fields = row.split(None, 5)
        major, minor = (int(v, 16) for v in fields[3].split(":"))
        return (os.makedev(major, minor), int(fields[4]), fields[5]) == (
            observed["device"], observed["inode"], observed["realpath"])
    except (AttributeError, IndexError, KeyError, ValueError):
        return False


def observation_problems(record: dict) -> list[str]:
    """validation_run.py's own audit of a live observation (its audit_r1), applied to this record: each
    observation is the producer process's, verified, of the selected key, at the selected library's
    mapped identity, with the process's map lines to show that file mapped."""
    selected = record.get("selected") or {}
    expected = selected.get("expected_mapping") or {}
    observations = record.get("observations") or []
    if not observations:
        return ["no verified live observation of the selected layer"]
    if not expected or expected.get("realpath") != selected.get("library") \
            or expected.get("sha256") != selected.get("library_sha256"):
        return ["the record's expected mapping is not the selected library"]
    p = []
    for i, o in enumerate(observations):
        if not (o.get("verified") is True and o.get("selected_key") == selected.get("key")
                and type(o.get("pid")) is int and o.get("pid") == record.get("pid")):
            p.append(f"observation {i}: no verified live observation of the selected layer in the producer")
        if wrong := sorted(k for k, v in expected.items() if o.get(k) != v):
            p.append(f"observation {i}: the live library differs from the selected one in {', '.join(wrong)}")
        rows = o.get("matched_map_lines")
        if not isinstance(rows, list) or not rows or not all(map_row_shows(r, o) for r in rows):
            p.append(f"observation {i}: the process's map lines do not show the observed library mapped")
    return p


def observer_problems(held: dict) -> list[str]:
    """validation_run.py's probe_identity, as its audit_r1 applies it, on the observer the run loaded (the
    run's --probe-dir): its manifest selects its library, both are its build record's outputs, the build's
    sources are this tree's and its compiler is unchanged, and the record's observer is what that returns.
    The run's retained copy of those files is held to the verified ones (read in place, they are the same)."""
    argv, recorded = held["command"]["argv"], held["record"].get("observer") or {}
    directory = Path(recorded["manifest"]).parent
    p = []
    if "--probe-dir" not in argv[:-1] or directory != Path(argv[argv.index("--probe-dir") + 1]).resolve():
        p.append(f"the observer record names {directory}, not the run's --probe-dir")
    try:
        probe = vr.probe_identity(directory, ROOT)
    except vr.ObservationRefused as e:
        return p + [f"the observer the run loaded fails validation_run.py's probe_identity: {e}"]
    if probe != recorded:
        p.append("the observer record is not what probe_identity verifies now, in "
                 + ", ".join(sorted(k for k in set(probe) | set(recorded) if probe.get(k) != recorded.get(k))))
    retained = held["dir"] / "validation-probe"
    if wrong := sorted(n for n, k in PROBE_FILES.items() if digest(retained / n) != probe[k]):
        p.append(f"the retained observer's {', '.join(wrong)} differ from the observer the run loaded")
    return p


def held_run(out: Path) -> dict:
    """One retained run, read and held to itself: its command, the validation record, the reports."""
    out = Path(out).resolve(strict=True)
    command, record, report, device, check = (load(out / n) for n in
                                              ("command.json", "validation.json", "report.json", "d3d9gpu.json", "check.json"))
    try:
        exit_status = int((out / "exit").read_text())
    except ValueError:
        raise SystemExit(f"refused: {rel(out / 'exit')} is malformed (not an exit status); "
                         "every input is a retained file") from None
    stderr = out / "validation.stderr"
    # validation_run.py writes its settings file at the record's stem + "-settings" (audit_r1 holds it there):
    # the record must name that file, and the retained one beside this run is the one read
    argv = command["argv"]
    recorded = Path(argv[argv.index("--record") + 1]) if "--record" in argv[:-1] else None
    settings_file = Path(record["settings_file"])
    settings = out / settings_file.parent.name / settings_file.name
    problems = []
    if recorded is None or settings_file != recorded.parent / (recorded.stem + "-settings") / "vk_layer_settings.txt":
        problems.append(f"the record names settings file {settings_file}, not validation_run.py's for {recorded}")
    if (record.get("environment") or {}).get("VK_LAYER_SETTINGS_PATH") != str(settings_file.parent):
        problems.append(f"the producer's VK_LAYER_SETTINGS_PATH is "
                        f"{(record.get('environment') or {}).get('VK_LAYER_SETTINGS_PATH')}, not the settings "
                        f"file's directory {settings_file.parent}")
    if record.get("stderr_sha256") != sha256(stderr) or record.get("stderr_bytes") != stderr.stat().st_size:
        problems.append("the retained stderr differs from what validation_run.py collected")
    if not (out / "run.txt").read_bytes().endswith(stderr.read_bytes()):
        problems.append("the combined output omits the collected stderr")
    if record.get("command") != command["argv"][command["argv"].index("--") + 1:]:
        problems.append("the validation record names another producer command")
    if record.get("producer_sha256") != command["player_sha256"]:
        problems.append("the validation record names another player")
    if report.get("stream_sha256") != command["stream_sha256"]:
        problems.append("the player read another stream")
    names = (get_env(device, "VK_INSTANCE_LAYERS") or "").split(":")
    if names != [PROBE, LAYER]:
        problems.append(f"the device's environment selects {names}, not the observer then the validation layer")
    problems += observation_problems(record)
    return {"dir": out, "command": command, "record": record, "report": report, "device": device, "check": check,
            "exit": exit_status, "scan": scan(out), "problems": problems, "settings": settings,
            "vk_env": load(out / "vk-env.json")}


def get_env(device: dict, key: str):
    return (device.get("env") or {}).get(key)


def frames(out: Path, report: dict) -> dict:
    """The run's times file against the registered DXVK reference, field by field (compare_hosts.py
    TIMES_FIELDS), and its rows and digest against the player's own report."""
    times = out / "frames.y4m.times.tsv"
    rows, digest_ = play_report.bgrx_digest(times)
    reference = ROOT / ch.METAL_HARBOR["reference"]
    got, want = ch.read_times(times), ch.read_times(reference)
    mismatched = [i for i, (a, b) in enumerate(zip(got, want))
                  if any(a.get(k) != b.get(k) for k in ch.TIMES_FIELDS)]
    fo = report.get("frames_out") or {}
    return {"times": {"path": rel(times), "sha256": sha256(times)}, "rows": rows,
            "bgrx_digest": f"0x{digest_:016x}",
            "report_agrees": rows == fo.get("times_rows") == fo.get("frames") and f"0x{digest_:016x}" == fo.get("bgrx_digest"),
            "reference": {"path": ch.METAL_HARBOR["reference"], "sha256": sha256(reference), "rows": len(want)},
            "reference_sha256_registered": sha256(reference) == ch.METAL_HARBOR["reference_sha256"],
            "fields": list(ch.TIMES_FIELDS), "rows_compared": min(len(got), len(want)),
            "rows_differing": len(mismatched), "first_differing_frame": got[mismatched[0]].get("frame") if mismatched else None,
            "equal": len(got) == len(want) and not mismatched}


def summary(held: dict) -> dict:
    report, device, record = held["report"], held["device"], held["record"]
    gpu = device.get("sdl_gpu") or {}
    return {
        "dir": rel(held["dir"]), "started": held["command"]["started"], "exit": held["exit"],
        "complete": report.get("complete"), "pass": report.get("pass"), "stop": report.get("stop"),
        "presents": report.get("presents"), "frame_records": report.get("frame_records"),
        "draws": report.get("draws"), "records": report.get("records"), "errors": report.get("errors"),
        "wall_s": report.get("wall_s"), "validation_run_seconds": record.get("seconds"),
        "play_report_check": {"pass": held["check"].get("pass"), "failures": held["check"].get("failures")},
        "device": {"complete": device.get("complete"), "debug_mode": gpu.get("debug_mode"),
                   "device_name": gpu.get("device_name"), "driver_info": gpu.get("driver_info"),
                   "not_implemented": (device.get("not_implemented") or {}).get("count"),
                   "VK_INSTANCE_LAYERS": get_env(device, "VK_INSTANCE_LAYERS"),
                   **{k: get_env(device, k) for k in FAULT_SWITCHES},
                   "sdl_patched": gpu.get("sdl_patched"), "capability_override": gpu.get("capability_override"),
                   "present": {k: (device.get("present") or {}).get(k) for k in
                               ("mode", "swapchain_mode", "presents", "presented", "frames_without_swapchain_texture", "failures")},
                   "draws": {k: (device.get("draws") or {}).get(k) for k in
                             ("total", "accepted", "drawn", "unmapped_count", "backend_failed_count", "suppressed_count")},
                   "readbacks": device.get("readbacks")},
        "window": {k: (report.get("window") or {}).get(k) for k in ("frames_with_flags", "input_events", "idle")},
        "host": {k: (report.get("host") or {}).get(k) for k in ("sdl_video_driver", "live_desktop_session",
                                                                "live_desktop_session_basis", "focus_or_occlusion")},
        "validation_record": {"path": rel(held["dir"] / "validation.json"), "sha256": sha256(held["dir"] / "validation.json"),
                              "pass": record.get("pass"), "producer_exit": record.get("producer_exit"),
                              "observations": len(record.get("observations") or []),
                              "observed_instances": [o.get("instance") for o in record.get("observations") or []],
                              "stderr_sha256": record.get("stderr_sha256"), "stderr_bytes": record.get("stderr_bytes"),
                              "settings_sha256": record.get("settings_sha256")},
        "vk_env": {"inherited_cleared": sorted(held["vk_env"].get("inherited") or {}),
                   "settings_files_present": held["vk_env"].get("settings_files_present")},
        "problems": held["problems"],
    }


def leg_failures(held: dict, label: str, control: str | None) -> list[str]:
    """What every leg, the control included, is held to: its own record and output, the registered stream,
    the layer's and SDL's lines read two ways, the observer's observations against the layer's whole startup
    lines, validation_run.py's settings text and an environment that set no validation feature, the NVIDIA
    ICD as it is now, the observer through probe_identity, and the device's fault switches."""
    record, command, s = held["record"], held["command"], held["scan"]
    f = [f"{label}: {p}" for p in held["problems"]]
    if command.get("control") != control:
        f.append(f"{label}: run with control {command.get('control')}, not {control}")
    registered = ch.METAL_HARBOR["stream_sha256"]
    if command.get("stream_sha256") != registered or held["report"].get("stream_sha256") != registered:
        f.append(f"{label}: the run was given stream {command.get('stream_sha256')} and the player read "
                 f"{held['report'].get('stream_sha256')}, not the registered Metal Harbor {registered}")
    if record.get("pass") is not True:
        f.append(f"{label}: validation_run.py refused the run: {record.get('failure')}")
    if not s["sdl_validation_enabled"] or s["sdl_validation_not_found"]:
        f.append(f"{label}: SDL's log does not show the validation layer enabled")
    observed = len(record.get("observations") or [])
    if not s["layer_logging_lines"] or s["layer_logging_lines"] != observed:
        f.append(f"{label}: the layer said where it logs {s['layer_logging_lines']} times for {observed} observed "
                 "instances")
    if s["layer_startup_lines"] != s["layer_logging_lines"]:
        f.append(f"{label}: {s['layer_logging_lines'] - s['layer_startup_lines']} of the layer's "
                 f"{s['layer_logging_lines']} logging lines are not {vr.LAYER_STARTUP!r}")
    if (held["device"].get("sdl_gpu") or {}).get("debug_mode") is not True:
        f.append(f"{label}: SDL_gpu's debug mode was not on")
    if held["vk_env"].get("settings_files_present"):
        f.append(f"{label}: a layer settings file outside the run could filter the layer")
    if digest(held["settings"]) != record.get("settings_sha256"):
        f.append(f"{label}: the retained layer settings file is not the one validation_run.py wrote")
    elif held["settings"].read_text() != vr.LAYER_SETTINGS:
        f.append(f"{label}: the layer settings file is not validation_run.py's LAYER_SETTINGS")
    icd = {"VK_DRIVER_FILES": {"path": NVIDIA_ICD, "sha256": digest(NVIDIA_ICD)}}
    if record.get("icd_inputs") != icd:
        f.append(f"{label}: the run's ICD was {record.get('icd_inputs')}, not {icd} (the ICD on disk now)")
    drivers = {"command": (command.get("env") or {}).get("VK_DRIVER_FILES"),
               "producer": (record.get("environment") or {}).get("VK_DRIVER_FILES"),
               "device": get_env(held["device"], "VK_DRIVER_FILES")}
    if set(drivers.values()) != {NVIDIA_ICD}:
        f.append(f"{label}: VK_DRIVER_FILES was {drivers}, not {NVIDIA_ICD}")
    f += [f"{label}: {p}" for p in observer_problems(held)]
    vk = sorted({k for env in (command.get("env") or {}, record.get("environment") or {}) for k in env
                 if k.startswith("VK_")} - VK_ENV)
    if vk:
        f.append(f"{label}: the producer saw {vk}, which could set a validation feature")
    want = {"SA2_D3D9GPU_FAULT": "", "SA2_D3D9GPU_SHADER_FAULT": control or "",
            "SA2_D3D9GPU_TEST_RUN": "1" if control else ""}
    got = {k: get_env(held["device"], k) for k in FAULT_SWITCHES}
    if got != want:
        f.append(f"{label}: the device's fault switches were {got}, not {want}")
    loose = s["loose"]
    if (loose["validation_error_lines"] != s["errors"] or loose["validation_warning_lines"] != s["warnings"]
            or (loose["vuid_lines"] and not s["errors"] + s["warnings"])
            or loose["sdl_error_lines"] != len(s["sdl"]["ERROR"]) or loose["sdl_warning_lines"] != len(s["sdl"]["WARNING"])
            or loose["layer_logging_lines"] != s["layer_logging_lines"]):
        f.append(f"{label}: the loose count {loose} disagrees with the parsed messages (errors {s['errors']}, warnings "
                 f"{s['warnings']}, SDL error lines {len(s['sdl']['ERROR'])}, SDL warning lines "
                 f"{len(s['sdl']['WARNING'])}, logging lines {s['layer_logging_lines']})")
    return f


def complete_run_failures(held: dict, label: str, readback: bool) -> list[str]:
    """Why one retained run is not a complete, quiet debug run of the registered stream under the layer.
    Quiet is no validation error or warning and no SDL error, warning or assertion line."""
    record, report, command = held["record"], held["report"], held["command"]
    f = leg_failures(held, label, None)
    if command.get("max_frames") or command.get("readback") is not readback:
        f.append(f"{label}: a bounded run or the other readback mode, not this leg")
    if held["exit"] != 0 or report.get("complete") is not True or report.get("pass") is not True:
        f.append(f"{label}: the player exited {held['exit']} with complete={report.get('complete')} pass={report.get('pass')}")
    if report.get("stop") != "end of stream" or report.get("presents") != ch.METAL_HARBOR["frames"]:
        f.append(f"{label}: the player stopped at {report.get('stop')} after {report.get('presents')} presents, "
                 f"not the stream's end after {ch.METAL_HARBOR['frames']}")
    if held["check"].get("pass") is not True:
        f.append(f"{label}: play_report.py check failed the run")
    present, drawn = held["device"].get("present") or {}, held["device"].get("draws") or {}
    if (present.get("mode") != "window" or present.get("presented") != ch.METAL_HARBOR["frames"]
            or present.get("frames_without_swapchain_texture") != 0 or present.get("failures") != 0):
        f.append(f"{label}: not every frame was presented to the swapchain: {present}")
    if drawn.get("drawn") != drawn.get("total") or not drawn.get("total"):
        f.append(f"{label}: the device drew {drawn.get('drawn')} of {drawn.get('total')} draws")
    s = held["scan"]
    if s["errors"] or s["warnings"] or s["sdl"]["ERROR"] or s["sdl"]["WARNING"] or s["sdl_asserts"]:
        f.append(f"{label}: {s['errors']} validation errors, {s['warnings']} validation warnings, "
                 f"{len(s['sdl']['ERROR'])} SDL error lines, {len(s['sdl']['WARNING'])} SDL warning lines, "
                 f"{len(s['sdl_asserts'])} SDL assertion lines")
    return f


def identities(legs: dict[str, dict]) -> tuple[dict, dict, dict, list[str]]:
    """The player, its build record and SDL, held across every leg and to what is on disk now: the build
    record names the binary that ran and every one of its sources is this tree's, each run recorded this
    build record, each player report names this binary, and each run loaded the SDL library on disk."""
    main = legs["readback"]
    producer = main["record"]["producer_sha256"]
    player = Path(main["record"]["command"][0])
    build_record = build_record_of(player)
    built = load(build_record)
    record_now, player_now = sha256(build_record), digest(player)
    sources = built.get("sources") or {}
    differing = sorted(k for k, v in sources.items() if digest(ROOT / k) != v)
    binary = (built.get("binaries") or {}).get(player.name)
    libsdl = (main["report"].get("files") or {}).get("libSDL3") or {}
    sdl_now = digest(libsdl["real_path"]) if libsdl.get("real_path") else None
    f = []
    if binary != producer:
        f.append(f"player: its build record {rel(build_record)} names binary {binary}, not the {producer} that ran")
    if not sources or differing:
        f.append(f"player: {len(differing)} of its build record's {len(sources)} sources differ from this tree: "
                 + ", ".join(differing[:12]) + (" ..." if len(differing) > 12 else ""))
    if player_now != producer:
        f.append(f"player: the binary at {rel(player)} is {player_now}, not the {producer} that ran")
    if not libsdl.get("sha256") or sdl_now != libsdl.get("sha256"):
        f.append(f"sdl3: {libsdl.get('real_path')} is {sdl_now} now, not the {libsdl.get('sha256')} the run loaded")
    for label, h in legs.items():
        files = h["report"].get("files") or {}
        mine = files.get("player") or {}
        if Path(h["record"]["command"][0]) != player or h["record"].get("producer_sha256") != producer:
            f.append(f"{label}: another player than the readback run's")
        if (not mine.get("real_path") or Path(mine["real_path"]).resolve() != player.resolve()
                or mine.get("sha256") != producer or mine.get("sha256") != player_now):
            f.append(f"{label}: the player's report names {mine.get('real_path')} at {mine.get('sha256')}, not "
                     f"{rel(player)} at {player_now}")
        if h["command"].get("build_record") != {"path": str(build_record), "sha256": record_now}:
            f.append(f"{label}: the run recorded build record {h['command'].get('build_record')}, not "
                     f"{rel(build_record)} at {record_now}")
        theirs = files.get("libSDL3") or {}
        if (theirs.get("real_path"), theirs.get("sha256")) != (libsdl.get("real_path"), libsdl.get("sha256")):
            f.append(f"{label}: another SDL library than the readback run's")
    player_doc = {"path": rel(player), "sha256": producer, "sha256_now": player_now,
                  "build_record": {"path": rel(build_record), "sha256": record_now},
                  "build_record_binary_sha256": binary, "build_record_agrees": binary == producer,
                  "build_sources": len(sources), "build_sources_equal_to_this_tree": len(sources) - len(differing),
                  "build_sources_differing": differing,
                  "report_player_sha256": ((main["report"].get("files") or {}).get("player") or {}).get("sha256")}
    sdl_doc = {"path": rel(libsdl.get("real_path", "")), "sha256": libsdl.get("sha256"), "sha256_now": sdl_now}
    return player_doc, sdl_doc, sources, f


def validation(held: dict, classes: dict) -> dict:
    messages = held["scan"]["messages"]
    return {"errors": held["scan"]["errors"], "warnings": held["scan"]["warnings"], "by_id": messages,
            "classification": {k: classes[k] for k in sorted(classes) if k in messages},
            "unclassified_errors": sorted(k for k, v in messages.items() if v["severity"] == "Error" and k not in classes),
            "sdl_error_lines": held["scan"]["sdl"]["ERROR"], "sdl_warning_lines": held["scan"]["sdl"]["WARNING"],
            "sdl_asserts": held["scan"]["sdl_asserts"],
            "sdl_validation_enabled_lines": held["scan"]["sdl_validation_enabled"],
            "layer_logging_lines": held["scan"]["layer_logging_lines"],
            "layer_startup_lines": held["scan"]["layer_startup_lines"], "loose": held["scan"]["loose"]}


def tool_line(held: dict, players: Path) -> str:
    c = held["command"]
    return (f"source tools/env.sh && .venv/bin/python {SELF} run --players {rel(players)} --out {rel(held['dir'])}"
            + ("" if c.get("readback") else " --no-frames") + (f" --control {c['control']}" if c.get("control") else "")
            + (f" --max-frames {c['max_frames']}" if c.get("max_frames") else ""))


def receipt(args) -> int:
    main = held_run(Path(args.run))
    play = held_run(Path(args.play_run)) if args.play_run else None
    control = held_run(Path(args.control_run)) if args.control_run else None
    record, report = main["record"], main["report"]
    runs = {"readback": main} | ({"play": play} if play else {})
    legs = runs | ({"control": control} if control else {})
    player_doc, sdl_doc, sources, failures = identities(legs)
    players = Path(record["command"][0]).parent
    stream_path, stream_sha = registered_stream()
    fr = frames(main["dir"], report)
    classes = json.loads(Path(args.classes).read_text()) if args.classes else {}
    failures += complete_run_failures(main, "readback", True)
    if not (fr["equal"] and fr["report_agrees"] and fr["reference_sha256_registered"]):
        failures.append("readback: the frames under the layer are not the registered reference's")
    if play is None:
        failures.append("no play-path run (run --no-frames), the leg host/d3d9gpu/test_d3d9gpu.sh step 5 runs")
    else:
        failures += complete_run_failures(play, "play", False)
        for name, a, b in (("layer", play["record"]["selected"], record["selected"]),
                           ("stream", play["report"].get("stream_sha256"), report.get("stream_sha256"))):
            if a != b:
                failures.append(f"play: another {name} than the readback run's")
    if control is None:
        failures.append("no positive control was run")
    else:
        name = control["command"].get("control")
        failures += leg_failures(control, "control", name)
        if name not in CONTROLS:
            failures.append("control: not a named control")
        error_ids = {v["id"] for v in control["scan"]["messages"].values() if v["severity"] == "Error"}
        if control["scan"]["errors"] == 0:
            failures.append("control: no validation error was counted, so a zero count would prove nothing")
        elif error_ids != CONTROLS.get(name):
            failures.append(f"control: its errors are {sorted(error_ids)}, not its fault's {sorted(CONTROLS.get(name) or ())}")
        for name, a, b in (("layer", control["record"]["selected"], record["selected"]),
                           ("stream", control["report"].get("stream_sha256"), report.get("stream_sha256"))):
            if a != b:
                failures.append(f"control: another {name} than the runs it controls")
        drawn = draw_time_control(record)
        if not drawn["same_layer"] or not drawn["validation_errors"]:
            failures.append("control: R1's draw-time control is not this layer's, or counted nothing")
    pipeline = pipeline_now()
    try:
        layer_now = vt.selected_layer(ROOT)
    except vt.ValidationRefused as e:
        layer_now = None
        failures.append(f"layer: the pinned validation layer does not prove itself now: {e}")
    for label, held in legs.items():
        if held["command"].get("pipeline") != pipeline:
            failures.append(f"{rel(held['dir'])} was made by other code than this receipt pins: "
                            + ", ".join(sorted(k for k in set(pipeline) | set(held["command"].get("pipeline") or {})
                                               if (held["command"].get("pipeline") or {}).get(k) != pipeline.get(k))))
        # audit_r1's own hold: the run's selected layer is validation_tools.py's selection now
        if layer_now is not None and held["record"].get("selected") != layer_now:
            failures.append(f"{label}: the run's selected layer is not the pinned layer on disk now, in "
                            + ", ".join(sorted(k for k in set(layer_now) | set(held["record"].get("selected") or {})
                                               if (held["record"].get("selected") or {}).get(k) != layer_now.get(k))))
    manifest = Path(record["selected"]["manifest"])
    if digest(manifest) != record["selected"]["manifest_sha256"]:
        defaults = {k: None for k in DEFAULT_KEYS}
        failures.append(f"layer: the manifest at {rel(manifest)} is {digest(manifest)}, not the selected "
                        f"{record['selected']['manifest_sha256']}; its defaults are not read")
    else:
        defaults = layer_defaults(manifest)
    marker = "HOST2_VALIDATION_MH_OK" if not failures else "HOST2_VALIDATION_MH_FAIL"
    on = ", ".join(k for k, v in defaults.items() if v is True)
    off = ", ".join(k for k, v in defaults.items() if v is False)
    doc = {
        "schema": SCHEMA, "generated_by": SELF,
        "inputs": {"pipeline": dict(sorted((sources | pipeline).items())),
                   "pipeline_note": f"this tool and what it runs, then every source of the player's build record "
                                    f"{player_doc['build_record']['path']} (sha256 {player_doc['build_record']['sha256']}, "
                                    "the one each run recorded) at the hash that record gives it",
                   "run": "the run directories are private (the player reads the game's draws): only hashes, counts "
                          "and the layer's own words are here"},
        "ruling": "Documentation/decisions/2026-10-04-S5-user-rulings-2.md: host 2's debug runs load the Vulkan "
                  "validation layers built from the approved sources, since Vulkan misuse a desktop driver "
                  "tolerates is what breaks on phones",
        "scope": "host 2 (sa2_player_gpu on SDL_gpu's Vulkan backend, NVIDIA) playing the registered Metal Harbor "
                 "stream to its end under the pinned Khronos validation layer at its compiled defaults: each run's "
                 "settings file is validation_run.py's LAYER_SETTINGS, which sets only where and how messages are "
                 "reported, and no VK_* variable sets a feature, "
                 f"so the layer's built-in values applied (on: {on}; off: {off}). The manifest also lists a "
                 f"legacy 'disables' default ({', '.join(defaults.get('disables') or []) or 'none'}): that is a "
                 "vkconfig preset, which the layer reads only when a settings file or the environment sets "
                 "'disables', and it was not applied, so thread safety was on (VVL 350 layers/layer_options.cpp: "
                 "the legacy list is read only when set, lines 999-1004; with no legacy list the per-feature "
                 "settings apply over the compiled defaults, lines 1282-1314). SDL_gpu's "
                 "debug mode on, windowed on the live desktop session so the swapchain is acquired and presented; "
                 "twice: each frame read back for its hash (the comparison runs' readback path, held to the registered "
                 "reference) and the play path alone (no readback, as host/d3d9gpu/test_d3d9gpu.sh step 5 runs it). "
                 "One driver (NVIDIA); lavapipe and Metal are carried to S7 by the user's ruling of 2026-10-10.",
        "command": {label: {"argv": [lexical(a) for a in h["command"]["argv"]],
                            "env": {k: lexical(v) for k, v in h["command"]["env"].items()},
                            "producer_env": {k: ":".join(lexical(x) for x in v.split(":"))
                                             for k, v in sorted((h["record"].get("environment") or {}).items())},
                            "tool": tool_line(h, players)}
                    for label, h in legs.items()},
        "player": player_doc,
        "sdl3": sdl_doc,
        "validation_layer": {k: record["selected"][k] for k in ("key", "library_sha256", "manifest_sha256", "api_version")}
                            | {"library": rel(record["selected"]["library"]), "manifest": rel(record["selected"]["manifest"])},
        "observer": {label: {"library": rel(h["record"]["observer"]["library"]),
                             **{k: h["record"]["observer"][k] for k in PROBE_FILES.values()},
                             "probe_identity_now": not observer_problems(h),
                             "retained_in_place": (h["dir"] / "validation-probe").resolve()
                             == Path(h["record"]["observer"]["manifest"]).parent}
                     for label, h in legs.items()},
        "layer_settings": {label: vt_settings(h) for label, h in legs.items()},
        "layer_defaults": defaults,
        "icd": {label: {k: {"path": v["path"], "sha256": v["sha256"], "sha256_now": digest(v["path"])}
                        for k, v in (h["record"].get("icd_inputs") or {}).items()} for label, h in legs.items()},
        "stream": {"path": rel(stream_path), "sha256": report.get("stream_sha256"), "bytes": report.get("stream_bytes"),
                   "registered_sha256": ch.METAL_HARBOR["stream_sha256"],
                   "by_leg": {label: {"command": h["command"].get("stream_sha256"), "report": h["report"].get("stream_sha256")}
                              for label, h in legs.items()},
                   "registered": stream_sha == ch.METAL_HARBOR["stream_sha256"] and all(
                       h["command"].get("stream_sha256") == h["report"].get("stream_sha256") == stream_sha
                       for h in legs.values()),
                   "recording": {"path": hc.RECORDING, "sha256": hc.RECORDING_SHA256}},
        "runs": {label: summary(h) | {"rate": rate(h["report"]), "validation": validation(h, classes)}
                 for label, h in runs.items()},
        "frames": fr,
        "validation_errors": sum(h["scan"]["errors"] for h in runs.values()),
        "validation_warnings": sum(h["scan"]["warnings"] for h in runs.values()),
        "control": None if control is None else {
            "what": f"SA2_D3D9GPU_SHADER_FAULT={control['command']['control']} in a test run (SA2_D3D9GPU_TEST_RUN=1), "
                    f"the same harness, player, layer and stream, --max-frames {control['command']['max_frames']}: the "
                    "translator's fault makes shader modules the layer rejects, so the count can be nonzero; every "
                    "error it counts must be its fault's VUID",
            "expected_error_ids": sorted(CONTROLS.get(control["command"].get("control")) or ()),
            "run": summary(control), "errors": control["scan"]["errors"], "warnings": control["scan"]["warnings"],
            "loose": control["scan"]["loose"], "layer_logging_lines": control["scan"]["layer_logging_lines"],
            "layer_startup_lines": control["scan"]["layer_startup_lines"],
            "by_id": {k: {f: v[f] for f in ("severity", "id", "count", "calls")} for k, v in control["scan"]["messages"].items()},
            "draw_time": draw_time_control(record)},
        "failures": failures,
        "marker": marker,
    }
    Path(args.out).write_text(json.dumps(doc, indent=1) + "\n")
    print(f"{marker}: {rel(Path(args.out))}; validation errors {doc['validation_errors']}, warnings "
          f"{doc['validation_warnings']}; presents " + ", ".join(f"{k} {h['report'].get('presents')}" for k, h in runs.items())
          + f"; frames equal {fr['equal']}" + "".join(f"\n  {f}" for f in failures))
    return 0 if marker.endswith("_OK") else 1


def rate(report: dict) -> dict:
    """The run's pace under the layer beside host 2's registered run of the same stream without it
    (Documentation/receipts/s5-host-compare-v1.json's sdl3-gpu report, another player build, also
    windowed and reading each frame back)."""
    held = json.loads((ROOT / "Documentation/receipts/s5-host-compare-v1.json").read_text())
    host = next(h for h in held["hosts"] if h["host"] == "sdl3-gpu")
    path = ROOT / host["report"]["path"]
    base = json.loads(path.read_text()) if path.is_file() and sha256(path) == host["report"]["sha256"] else {}
    wall, base_wall = report.get("wall_s"), base.get("wall_s")
    return {"wall_s": wall, "presents_per_s": report["presents"] / wall if wall else None,
            "draws_per_s": report["draws"] / wall if wall else None,
            "without_layer": {"report": host["report"], "player_sha256": host["renderer"]["sha256"],
                              "wall_s": base_wall, "presents": base.get("presents"), "draws": base.get("draws"),
                              "reads_back": base.get("frames_out") is not None},
            "reads_back": report.get("frames_out") is not None,
            "slowdown": wall / base_wall if wall and base_wall else None}


def draw_time_control(record: dict) -> dict:
    """The control this tool's own cannot give: fp16's errors come at shader creation, and no named
    fault misuses Vulkan at a draw (modes_skipped and border_off were tried and are quiet). R1's
    modes-base control, the same validation_run.py, layer and settings on NVIDIA, is a draw-time error
    the harness counted."""
    doc = json.loads((ROOT / R1_RECEIPT_DOC).read_text())
    job = doc["validation_layer_observations"]["jobs"]["nvidia/modes-base"]
    return {"receipt": {"path": R1_RECEIPT_DOC, "sha256": sha256(ROOT / R1_RECEIPT_DOC)}, "job": "nvidia/modes-base",
            "validation_errors": job["validation_errors"],
            "same_layer": doc["validation_layer"]["library_sha256"] == record["selected"]["library_sha256"]
            and doc["validation_layer"]["manifest_sha256"] == record["selected"]["manifest_sha256"]}


def layer_defaults(path: Path) -> dict:
    """Each validation feature's default as the layer's manifest lists it (the caller holds the file to the
    selected manifest's sha256 first). No leg sets a feature (leg_failures holds the settings file to
    validation_run.py's text and the producer's VK_* variables to that), so the layer ran at its compiled
    values, which the per-feature defaults here describe. The legacy 'disables' default (thread safety off)
    is a vkconfig preset the layer applies only when 'disables' is set, which no leg does (VVL 350
    layers/layer_options.cpp lines 999-1004 and 1282-1314)."""
    manifest = load(path)
    found = {}
    def walk(rows):
        for row in rows:
            if row.get("key") in DEFAULT_KEYS:
                found[row["key"]] = row.get("default")
            walk(row.get("settings") or [])
    walk(manifest["layer"].get("features", {}).get("settings") or [])
    return {k: found.get(k) for k in DEFAULT_KEYS}


def vt_settings(held: dict) -> dict:
    text = held["settings"].read_text()
    return {"sha256": held["record"].get("settings_sha256"), "sha256_now": digest(held["settings"]),
            "validation_run_text": text == vr.LAYER_SETTINGS, "text": text.splitlines(),
            "VK_LAYER_SETTINGS_PATH": lexical((held["record"].get("environment") or {}).get("VK_LAYER_SETTINGS_PATH", ""))}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run", help="play the registered Metal Harbor stream on host 2 under the validation layer")
    r.add_argument("--players", required=True, help="a build's OUT holding sa2_player_gpu (read and executed only)")
    r.add_argument("--out", required=True, help="a fresh run directory under private/")
    r.add_argument("--max-frames", type=int, default=0)
    r.add_argument("--control", choices=sorted(CONTROLS), help="the positive control's translator fault (a test run)")
    r.add_argument("--no-frames", action="store_true",
                   help="no --frames-out: the play path alone, as host/d3d9gpu/test_d3d9gpu.sh step 5 runs it")
    w = sub.add_parser("receipt", help="write the receipt from retained runs; launches nothing")
    w.add_argument("--run", required=True)
    w.add_argument("--control-run")
    w.add_argument("--play-run", help="the same stream without readback (run --no-frames)")
    w.add_argument("--classes", help="JSON {severity|VUID: classification} for each error the run reported")
    w.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        return run(args) if args.mode == "run" else receipt(args)
    except FileNotFoundError as e:
        raise SystemExit(f"refused: {rel(e.filename) if e.filename else e} does not exist; "
                         "every input is a retained file") from None
    except KeyError as e:
        raise SystemExit(f"refused: a retained record is malformed (it has no {e.args[0]!r}); "
                         "every input is a retained file") from None
    except json.JSONDecodeError as e:
        raise SystemExit(f"refused: a retained record is malformed (not JSON: {e.msg} at line {e.lineno}); "
                         "every input is a retained file") from None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
