#!/usr/bin/env python3
"""Write the SDL patch's receipt (Documentation/receipts/s5-sdl3-patch-v1.json) from the runs'
own outputs: the evidence for the project's SDL3 patch (patches/sdl3) and the rebuild that
lands it, the checks A2 to A5 of the plan for ruling 3 and the equal-SDL A/B of the DXVK path.

  sdl3_patch_receipt.py --evidence EVIDENCE.json --out RECEIPT.json

EVIDENCE.json names the runs (paths absolute, or relative to the repository):

  {"installs": {"patched": PREFIX, "stock": PREFIX},
   "builds": {"patched": [LOG, LOG, ...], "stock": [LOG, ...]},
   "refusals": {"skip_patch": LOG, "system_kept_aside": LOG, "system_other_checkout": LOG},
   "a3": {"lavapipe": OUT, "nvidia": OUT, "stock_detect": OUT},
   "a4": {"boot": {"run": DIR, "reference": TSV}, "metalharbor": {"run": DIR, "reference": TSV}},
   "a5": OUT,
   "ab": [DXVK_AB_JSON x 5]}

Every leg is required, and every check below must hold, or nothing is written (exit 1, each
failure on stderr). No leg may be given twice: the files of one kind are distinct paths, and,
where two genuine runs always print differently (the A3 runs name their devices, the dxvk-ab
documents their stages and times, the refusals their libraries), distinct bytes, so one log or
document cannot count as two runs. Two builds of one source print byte-identical logs (the
builder prints no time or host state), so build logs are told apart by their paths alone.

  installs  the two prefixes the runs used, each a directory, two different ones;
  builds    at least two patched logs and one stock log; each starts "== configuration: <it>,
            prefix <P>", P the installs' prefix for it, ends "BUILD_OK (<it>)", and the sha256
            it prints for each pinned file equals tools/pins.json now (sdl3, dxvk, glslang; the
            stock prefix's SDL to sdl3-stock, its DXVK, glslang and archive to the dxvk and
            glslang pins), so the patched builds made one library (A2, "reproduced": how many);
  refusals  the builder with its apply step removed stopped at its final pin check on the sdl3
            library, having applied nothing (the plan's mutant "the builder skips the patch");
            --sdl3=system refused an SDL3 kept aside under this tree's private/s5-tools, and,
            in a second log, one built by another checkout;
  a3        lavapipe and NVIDIA, each a full check (border_clamp.c through run_border_clamp.sh
            with the capture and validation layers) on the device its leg names (the
            properties.default row's "device ..."), on the patched prefix's library (its
            sdl_library line: the prefix, and the sdl3 pin's sha256): it ends BORDER_CLAMP_OK
            with 26 rows, every row ok, no validation error, VK_RECORD_OK, and SDL's revision
            "SDL-<REVISION.txt> (<vendor_info>)"; the stock prefix's library (the sdl3-stock
            pin's) reads neither capability;
  a4        exactly the stages Documentation/receipts/s5-sdl-player-startup-v1.json registers
            (boot and Metal Harbor): each DXVK player run passed (pass, complete, exit status 0),
            verified its files, loaded the pinned patched SDL and DXVK from the patched prefix
            and reports the patched revision; its reference is the registered file (its sha256)
            and not the run's own; and its times file equals the reference in every row, as many
            rows as the registry's frame count, in the six fields tools/s5/compare_hosts.py's
            check h holds (TIMES_FIELDS);
  a5        the D3D12 compile ends D3D12_COMPILE_OK with no diagnostic the patch adds;
  ab        test_d3d9gpu.sh step 7's five A/Bs: the A4 boot run's stream at stage 3, and one
            other stream (the synthetic one) at stages 0 to 3; each reads PASS with both
            provenance checks passed, both its players ran on the stock prefix's library (the
            sdl3-stock pin's), and all five compare one before player with one after player,
            each the build file the document names, with different binary bytes on the two sides.

The receipt holds hashes, counts and verdicts, never the stream's bytes (AGENTS.md rule 9). Its
inputs.pipeline hashes the repository files that made the evidence; each must be older than
the oldest evidence file, so a file edited after any run fails here rather than reaching a
receipt (the check is over all the evidence at once: a pipeline file newer than the oldest run
may have been used by it in an older version). tools/pins.json is checked by content above, not
by age; check_pins.py, which the builder runs last, is not in it: every hash it would hold is
checked here against the pins themselves.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "sa2-recomp-s5-sdl3-patch/v1"
TIMES_FIELDS = ("frame", "seq", "virtual_us", "guest_frames", "polls", "bgrx_hash")
A3_ROWS = 26
# The registered DXVK references the A4 runs are held to (stage, expected times file and its
# sha256, frame count): the platform-startup receipt's registered picture checks.
REGISTRY = ROOT / "Documentation" / "receipts" / "s5-sdl-player-startup-v1.json"
# A3's device per leg, as border_clamp.c's properties.default row names it.
A3_DEVICES = {"lavapipe": "device llvmpipe", "nvidia": "device NVIDIA"}
# test_d3d9gpu.sh step 7's A/Bs: the boot stream at stage 3, the synthetic stream at these.
AB_SYNTH_STAGES = (0, 1, 2, 3)
PIPELINE = (
    "patches/sdl3/0001-gpu-clamp-to-border-and-depth-clamp-with-clip.patch",
    "tools/s5/sdl3_patches.py", "tools/s5/build_host_deps.sh",
    "tools/s5/sdl3-patches/test/border_clamp.c", "tools/s5/sdl3-patches/test/run_border_clamp.sh",
    "tools/s5/sdl3-patches/test/check_vk_record.py", "tools/s5/sdl3-patches/test/d3d12_compile.sh",
    "tools/s5/sdl3-patches/test/quad.vert", "tools/s5/sdl3-patches/test/textured.frag",
    "tools/s5/sdl3-patches/test/solid.frag", "tools/s5/sdl3-patches/test/solid_depth.frag",
    "host/d3d9gpu/test/vkcap/vkcap.c", "host/d3d9gpu/test/vkcap/build_vkcap.sh",
    "host/d3d9gpu/test/vkcap/vkcap.json.in",
    "host/player/player.cpp", "host/player/sdl_mark.h", "host/player/generate_expectations.py",
    "host/player/build.sh", "host/player/player_env.sh", "host/player/run_player.sh", "host/player/dxvk.conf",
    "tools/s5/compare_hosts.py", "tools/s5/sdl3_patch_receipt.py",
)


class Failed(Exception):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def where(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def shown(path: Path) -> str:
    """A path as the receipt records it: repository-relative where it can be, a file under the
    private tree as private/... even where private/ is a link (a worktree's or a clone's)."""
    real = path.resolve()
    for base, prefix in ((ROOT.resolve(), ""), ((ROOT / "private").resolve(), "private/")):
        if real.is_relative_to(base):
            return prefix + str(real.relative_to(base))
    return str(path)


def evidence(path: Path) -> dict:
    return {"path": shown(path), "sha256": sha(path)}


def lines(path: Path) -> list[str]:
    if not path.is_file():
        raise Failed(f"no evidence file {path}")
    return path.read_text(errors="replace").splitlines()


def leg(ev: dict, key: str, kind: type, what: str):
    """ev[key], which must be there and of `kind`."""
    value = ev.get(key)
    if not isinstance(value, kind) or not value:
        raise Failed(f"the evidence names no {what} ({key})")
    return value


def distinct(paths: list[Path], what: str, by_bytes: bool = True) -> None:
    """No file given twice, by path, or (by_bytes) by bytes: one run cannot stand for two."""
    reals = [p.resolve() for p in paths]
    if len(set(reals)) != len(reals):
        raise Failed(f"{what}: one file is given more than once")
    digests = [sha(p) for p in paths]
    if by_bytes and len(set(digests)) != len(digests):
        raise Failed(f"{what}: two of the files hold the same bytes")


def under(path: str | None, prefix: Path) -> bool:
    return bool(path) and Path(path).resolve().is_relative_to(prefix)


def installs(ev: dict) -> dict[str, Path]:
    given = leg(ev, "installs", dict, "installs")
    out = {}
    for config in ("patched", "stock"):
        p = where(leg(given, config, str, f"{config} prefix"))
        if not p.is_dir():
            raise Failed(f"installs: the {config} prefix {p} is not a directory")
        out[config] = p.resolve()
    if out["patched"] == out["stock"]:
        raise Failed(f"installs: the two configurations name one prefix {out['patched']}")
    return out


def printed_hashes(log: list[str]) -> dict[str, str]:
    """The builder's "<label> <sha256> <path>" lines after "== sha256 of the built files"."""
    out = {}
    if "== sha256 of the built files the pins name (a library's soname link followed)" not in log:
        raise Failed("the build log prints no built files' sha256")
    at = log.index("== sha256 of the built files the pins name (a library's soname link followed)")
    for line in log[at + 1:]:
        if line.startswith("=="):
            break
        m = re.fullmatch(r"(.+?) ([0-9a-f]{64}) \S.*", line)
        if not m:
            raise Failed(f"unreadable build hash line {line!r}")
        out[m.group(1)] = m.group(2)
    return out


def configuration_prefix(text: list[str], log: Path, config: str) -> Path:
    """The prefix the log's first line names for its configuration."""
    m = re.fullmatch(r"== configuration: (\S+), prefix (.+)", text[0]) if text else None
    if m is None or m.group(1) != config:
        raise Failed(f"{shown(log)} does not start '== configuration: {config}, prefix ...': {text[:1]}")
    return Path(m.group(2)).resolve()


def check_builds(ev: dict, tools: dict, prefixes: dict[str, Path]) -> dict:
    want = {"patched": {"dxvk library_sha256": tools["dxvk"]["library_sha256"],
                        "sdl3 library_sha256": tools["sdl3"]["library_sha256"],
                        "glslang binary_sha256": tools["glslang"]["binary_sha256"],
                        "dxvk dxbc_spirv_archive_sha256": tools["dxvk"]["dxbc_spirv_archive_sha256"]},
            "stock": {"sdl3-stock library_sha256": tools["sdl3-stock"]["library_sha256"],
                      "dxvk library_sha256 (in the stock prefix)": tools["dxvk"]["library_sha256"],
                      "glslang binary_sha256 (in the stock prefix)": tools["glslang"]["binary_sha256"],
                      "dxvk dxbc_spirv_archive_sha256 (in the stock prefix)": tools["dxvk"]["dxbc_spirv_archive_sha256"]}}
    given = leg(ev, "builds", dict, "builds")
    out = {}
    for config, least in (("patched", 2), ("stock", 1)):
        logs = [where(p) for p in leg(given, config, list, f"{config} build logs")]
        if len(logs) < least:
            raise Failed(f"{len(logs)} {config} build log(s): at least {least} are needed")
        for log in logs:
            lines(log)
        distinct(logs, f"the {config} build logs", by_bytes=False)
        rows = []
        for log in logs:
            text = lines(log)
            if configuration_prefix(text, log, config) != prefixes[config]:
                raise Failed(f"{shown(log)} built into {text[0].split('prefix ', 1)[-1]}, not the {config} prefix "
                             f"{prefixes[config]} the evidence's installs name")
            if text[-1:] != [f"BUILD_OK ({config})"]:
                raise Failed(f"{shown(log)} does not end BUILD_OK ({config}): {text[-1:]}")
            got = printed_hashes(text)
            if got != want[config]:
                raise Failed(f"{shown(log)} printed {got}, not the pins' {want[config]}")
            rows.append({**evidence(log), "printed": got})
        out[config] = {"builds": rows, "one_result": True, "pinned": want[config]}
    out["patched"]["reproduced"] = len(out["patched"]["builds"])
    return out


def check_refusals(ev: dict, tools: dict) -> dict:
    given = leg(ev, "refusals", dict, "refusals")
    skip = where(leg(given, "skip_patch", str, "skip-patch mutant's log"))
    text = lines(skip)
    if any(l.startswith("applied 0001-") or l.startswith("SDL3_PATCHES_APPLIED") for l in text):
        raise Failed(f"{shown(skip)}: the mutant applied the patch")
    stop = [l for l in text if l.startswith("sdl3: FAIL sha256(libSDL3.so.0 -> libSDL3.so.0.4.16) reports ")]
    if len(stop) != 1 or f"pinned '{tools['sdl3']['library_sha256']}'" not in stop[0] \
            or "the install does not match tools/pins.json" not in text[-1]:
        raise Failed(f"{shown(skip)} did not stop at the final pin check on the sdl3 library")
    out = {"skip_patch": {**evidence(skip), "caught_by": stop[0]}}
    systems = [where(leg(given, key, str, f"{key} log")) for key in ("system_kept_aside", "system_other_checkout")]
    for path in systems:
        lines(path)
    distinct(systems, "the two --sdl3=system refusals")
    for key, path in zip(("system_kept_aside", "system_other_checkout"), systems):
        stext = lines(path)
        refusal = [l for l in stext if "one of this project's own SDL3 builds" in l and "not a system SDL3" in l]
        if not refusal or any(l.startswith("BUILD_OK") for l in stext):
            raise Failed(f"{shown(path)}: --sdl3=system did not refuse one of this project's own SDL3 builds ({key})")
        out[key] = {**evidence(path), "refusal": refusal[0]}
    return out


def revision_wanted(tools: dict) -> str:
    with tarfile.open(where(tools["sdl3"]["path"])) as tar:
        member = tar.extractfile(f"SDL3-{tools['sdl3']['version']}/REVISION.txt")
        revision = member.read().decode().strip() if member else ""
    return f"SDL-{revision} ({tools['sdl3']['vendor_info']})"


def sdl_library(text: list[str], prefix: Path, pin: str) -> list[str]:
    """run_border_clamp.sh's sdl_library line against the prefix and library pin: problems."""
    found = [json.loads(l)["sdl_library"] for l in text if l.startswith('{"sdl_library"')]
    if len(found) != 1:
        return [f"{len(found)} sdl_library lines"]
    problems = []
    if Path(found[0].get("prefix") or "/nonexistent").resolve() != prefix:
        problems.append(f"SDL prefix {found[0].get('prefix')}, not {prefix}")
    if found[0].get("sha256") != pin:
        problems.append(f"SDL library {found[0].get('sha256')}, not the pin {pin}")
    return problems


def check_a3(ev: dict, tools: dict, prefixes: dict[str, Path]) -> dict:
    given = leg(ev, "a3", dict, "A3 runs")
    out = {}
    revision = revision_wanted(tools)
    paths = {name: where(leg(given, name, str, f"A3 {name} run")) for name in ("lavapipe", "nvidia", "stock_detect")}
    for path in paths.values():
        lines(path)
    distinct(list(paths.values()), "the A3 runs")
    for name in ("lavapipe", "nvidia"):
        path = paths[name]
        text = lines(path)
        rows = [json.loads(l) for l in text if l.startswith('{"test"')]
        heads = [json.loads(l) for l in text if l.startswith('{"sdl_revision"')]
        head = heads[0] if len(heads) == 1 else {}
        validation = [json.loads(l) for l in text if l.startswith('{"validation_layer"')]
        record = [json.loads(l)["vk_record"] for l in text if l.startswith('{"vk_record"')]
        default = [r for r in rows if r.get("test") == "properties.default"]
        problems = sdl_library(text, prefixes["patched"], tools["sdl3"]["library_sha256"])
        if text[-1:] != ["BORDER_CLAMP_OK"]:
            problems.append("does not end BORDER_CLAMP_OK")
        if len(rows) != A3_ROWS or not all(r.get("ok") is True for r in rows):
            problems.append(f"{len(rows)} rows, {sum(1 for r in rows if r.get('ok') is not True)} not ok")
        if len(default) != 1 or A3_DEVICES[name] not in str(default[0].get("detail")):
            problems.append(f"not run on the {name} device ('{A3_DEVICES[name]}' in properties.default): "
                            f"{[d.get('detail') for d in default]}")
        if len(validation) != 1 or validation[0]["validation_errors"] != 0:
            problems.append(f"validation {validation}")
        if "VK_RECORD_OK" not in text or len(record) != 1 or record[0].get("problems") != []:
            problems.append("no clean Vulkan record")
        if head.get("sdl_revision") != revision:
            problems.append(f"SDL revision {head.get('sdl_revision')!r}, not {revision!r}")
        if problems:
            raise Failed(f"A3 {name} ({shown(path)}): {'; '.join(problems)}")
        out[name] = {**evidence(path), "rows": len(rows), "all_ok": True, "validation_errors": 0,
                     "device": default[0]["detail"].split(A3_DEVICES[name][:len("device ")], 1)[1],
                     "vk_record": {k: record[0].get(k) for k in ("devices", "pipelines", "clip_and_clamp_pipelines",
                                                                   "samplers", "layers_inserted")},
                     "sdl_revision": revision}
    path = paths["stock_detect"]
    text = lines(path)
    rows = [json.loads(l) for l in text if l.startswith('{"test"')]
    problems = sdl_library(text, prefixes["stock"], tools["sdl3-stock"]["library_sha256"])
    if [(r["test"], r["ok"]) for r in rows] != [("properties.stock_absent", True)] or text[-1:] != ["BORDER_CLAMP_OK"]:
        problems.append(f"rows {rows}")
    if problems:
        raise Failed(f"A3 stock detection ({shown(path)}): {'; '.join(problems)}")
    out["stock_detect"] = {**evidence(path), "detail": rows[0].get("detail")}
    return out


def times(path: Path) -> list[dict]:
    with path.open() as handle:
        return [{k: r[k] for k in TIMES_FIELDS} for r in csv.DictReader(handle, delimiter="\t")]


def registered() -> dict[str, dict]:
    """The registry's checks by stage: expected times (path, sha256) and frame count."""
    doc = json.loads(REGISTRY.read_text())
    out = {}
    for c in doc.get("registered_picture_checks") or []:
        if c.get("pass") is not True or list(c.get("fields_compared") or []) != list(TIMES_FIELDS):
            raise Failed(f"{shown(REGISTRY)}: the registered check {c.get('stage')!r} is not a passed check of "
                         f"{list(TIMES_FIELDS)}")
        out[c["stage"]] = {"path": c["expected_times"]["path"], "sha256": c["expected_times"]["sha256"],
                           "frames": c["frame_count"]}
    if not out:
        raise Failed(f"{shown(REGISTRY)} registers no reference")
    return out


def check_a4(ev: dict, tools: dict, prefixes: dict[str, Path]) -> dict:
    given = leg(ev, "a4", dict, "A4 runs")
    refs = registered()
    if set(given) != set(refs):
        raise Failed(f"A4 runs {sorted(given)}, not the registered stages {sorted(refs)} ({shown(REGISTRY)})")
    revision = revision_wanted(tools)
    out = {}
    for name in sorted(refs):
        run, ref = where(given[name]["run"]), where(given[name]["reference"])
        report_path, times_path = run / "report.json", run / "frames.y4m.times.tsv"
        for p in (report_path, times_path, ref):
            lines(p)
        report = json.loads(report_path.read_text())
        files, ident = report.get("files") or {}, report.get("host_identity") or {}
        sdl, dxvk = files.get("libSDL3") or {}, files.get("libdxvk_d3d9") or {}
        problems = []
        if ref.resolve() == times_path.resolve():
            problems.append("the reference is the run's own times file")
        if sha(ref) != refs[name]["sha256"]:
            problems.append(f"the reference {shown(ref)} is not the registered one ({refs[name]['path']}, "
                            f"sha256 {refs[name]['sha256']})")
        if (report.get("pass"), report.get("complete"), report.get("exit_status")) != (True, True, 0):
            problems.append(f"the run did not pass: pass {report.get('pass')}, complete {report.get('complete')}, "
                            f"exit {report.get('exit_status')}")
        if (ident.get("sdl_file_verified"), ident.get("dxvk_file_verified")) != (True, True):
            problems.append("the run did not verify its SDL and DXVK files")
        if sdl.get("sha256") != tools["sdl3"]["library_sha256"] or not under(sdl.get("real_path"), prefixes["patched"]):
            problems.append(f"the run loaded SDL {sdl.get('sha256')} from {sdl.get('real_path')}, not the patched "
                            f"pin from {prefixes['patched']}")
        if dxvk.get("sha256") != tools["dxvk"]["library_sha256"] or not under(dxvk.get("real_path"), prefixes["patched"]):
            problems.append(f"the run loaded DXVK {dxvk.get('sha256')} from {dxvk.get('real_path')}, not the pin "
                            f"from {prefixes['patched']}")
        if ident.get("sdl_revision") != revision:
            problems.append(f"the run's SDL revision {ident.get('sdl_revision')!r}, not {revision!r}")
        mine, theirs = times(times_path), times(ref)
        differ = [i for i, (a, b) in enumerate(zip(mine, theirs)) if a != b]
        if not (len(mine) == len(theirs) == refs[name]["frames"]) or differ:
            problems.append(f"{len(mine)} rows against {len(theirs)} (registered {refs[name]['frames']}), "
                            f"{len(differ)} differing" + (f" (first {differ[0]})" if differ else ""))
        if problems:
            raise Failed(f"A4 {name} ({shown(run)}): {'; '.join(problems)}")
        out[name] = {"report": evidence(report_path), "times": evidence(times_path),
                     "reference": evidence(ref), "registered": refs[name]["path"], "rows": len(mine),
                     "rows_differing": 0, "fields_compared": list(TIMES_FIELDS),
                     "stream_sha256": report.get("stream_sha256"), "libSDL3_sha256": sdl["sha256"],
                     "libdxvk_sha256": dxvk["sha256"], "sdl_revision": revision}
    return out


def check_a5(ev: dict) -> dict:
    path = where(leg(ev, "a5", str, "A5 run"))
    text = lines(path)
    files = [json.loads(l) for l in text if l.startswith('{"file"')]
    if text[-1:] != ["D3D12_COMPILE_OK"] or not files or any(f["patched_only"] != 0 for f in files):
        raise Failed(f"A5 ({shown(path)}): {text[-1:]} {files}")
    return {**evidence(path), "files": files}


def check_ab(ev: dict, tools: dict, prefixes: dict[str, Path], boot_stream: str | None) -> list[dict]:
    paths = [where(p) for p in leg(ev, "ab", list, "dxvk-ab documents")]
    if len(paths) != 1 + len(AB_SYNTH_STAGES):
        raise Failed(f"{len(paths)} dxvk-ab documents: test_d3d9gpu.sh step 7 runs {1 + len(AB_SYNTH_STAGES)} (the boot "
                     f"stream at stage 3, the synthetic one at stages {list(AB_SYNTH_STAGES)})")
    for path in paths:
        lines(path)
    distinct(paths, "the dxvk-ab documents")
    docs = [json.loads(p.read_text()) for p in paths]
    out, players = [], set()
    for path, doc in zip(paths, docs):
        if doc.get("verdict") != "PASS" or doc.get("pass") is not True:
            raise Failed(f"dxvk-ab {shown(path)} reads {doc.get('verdict')}: {doc.get('failures', [])[:5]}")
        if (doc.get("before_provenance") or {}).get("pass") is not True or \
                (doc.get("after_provenance") or {}).get("pass") is not True:
            raise Failed(f"dxvk-ab {shown(path)}: a side's provenance did not pass")
        sdl = {}
        for side in ("before", "after"):
            lib = (json.loads((path.parent / side / "report.json").read_text()).get("files") or {}).get("libSDL3") or {}
            if lib.get("sha256") != tools["sdl3-stock"]["library_sha256"] or not under(lib.get("real_path"), prefixes["stock"]):
                raise Failed(f"dxvk-ab {shown(path)}: the {side} side ran on SDL {lib.get('sha256')} from "
                             f"{lib.get('real_path')}, not the stock pin from {prefixes['stock']}")
            sdl[side] = lib["sha256"]
        pair = doc.get("players") or {}
        for side in ("before", "after"):
            p = pair.get(side) or {}
            binary = Path(str(p.get("build"))) / "sa2_player"
            if not binary.is_file() or sha(binary) != p.get("sha256"):
                raise Failed(f"dxvk-ab {shown(path)}: the {side} player {binary} is not the one it names ({p.get('sha256')})")
        if pair["before"]["sha256"] == pair["after"]["sha256"]:
            raise Failed(f"dxvk-ab {shown(path)}: before and after name byte-identical players")
        players.add(json.dumps(pair, sort_keys=True))
        out.append({**evidence(path), "verdict": "PASS", "stream": doc.get("stream"), "stage": doc.get("stage"),
                    "before": pair.get("before"), "after": pair.get("after"),
                    "sides_sdl_sha256": tools["sdl3-stock"]["library_sha256"],
                    "host_sources_differing": doc.get("host_sources_differing")})
    if len(players) != 1:
        raise Failed(f"the dxvk-ab documents compare {len(players)} pairs of players, not one before and one after")
    runs = sorted(((d.get("stream") or {}).get("sha256"), d.get("stage")) for d in docs)
    boot = [r for r in runs if r == (boot_stream, 3)]
    synth = {s for s, _ in runs if s != boot_stream}
    if len(boot) != 1 or len(synth) != 1 or sorted(st for s, st in runs if s != boot_stream) != list(AB_SYNTH_STAGES):
        raise Failed(f"the dxvk-ab documents run {runs}: not the A4 boot run's stream ({boot_stream}) at stage 3 and "
                     f"one other stream at stages {list(AB_SYNTH_STAGES)}")
    return out


def evidence_files(ev: dict) -> list[Path]:
    paths = [*ev["builds"]["patched"], *ev["builds"]["stock"], *ev["refusals"].values(), *ev["a3"].values(), ev["a5"],
             *ev["ab"]]
    for name in ev["a4"].values():
        paths += [str(Path(name["run"]) / "frames.y4m.times.tsv"), str(Path(name["run"]) / "report.json")]
    return [where(p) for p in paths]


def pipeline(ev: dict) -> dict:
    """The repository files that made the evidence, each no newer than the oldest evidence file."""
    files = evidence_files(ev)
    oldest = min(files, key=lambda p: p.stat().st_mtime)
    newer = [p for p in PIPELINE if (ROOT / p).stat().st_mtime > oldest.stat().st_mtime
             and p != "tools/s5/sdl3_patch_receipt.py"]
    if newer:
        raise Failed(f"edited after the oldest evidence file ({shown(oldest)}) was written, so a run may have used an "
                     f"older version of them: {newer}; make the evidence again after the last edit")
    return {p: sha(ROOT / p) for p in PIPELINE}


def code_head() -> str:
    """This repository's HEAD, git run with none of the caller's GIT_* variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, env=env)
    head = r.stdout.strip()
    if r.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise Failed(f"git rev-parse HEAD in {ROOT} exited {r.returncode}: {(r.stdout + r.stderr).strip()[:200]}")
    return head


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--evidence", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    ev = json.loads(args.evidence.read_text())
    pins_path = ROOT / "tools" / "pins.json"
    tools = json.loads(pins_path.read_text())["tools"]
    checks, failures, prefixes = {}, [], {}
    errors = (Failed, OSError, KeyError, ValueError, TypeError, AttributeError)
    try:
        prefixes = installs(ev)
    except errors as error:
        failures.append(f"installs: {error}")
    if prefixes:
        for name, run in (("builds", lambda: check_builds(ev, tools, prefixes)),
                          ("refusals", lambda: check_refusals(ev, tools)),
                          ("a3_behaviour", lambda: check_a3(ev, tools, prefixes)),
                          ("a4_dxvk_references", lambda: check_a4(ev, tools, prefixes)),
                          ("a5_d3d12_compile", lambda: check_a5(ev)),
                          ("equal_sdl_ab", lambda: check_ab(ev, tools, prefixes,
                                                            (checks.get("a4_dxvk_references") or {}).get("boot", {})
                                                            .get("stream_sha256")))):
            try:
                checks[name] = run()
            except errors as error:
                failures.append(f"{name}: {error}")
    pipe, head = None, None
    if not failures:
        try:
            pipe = pipeline(ev)
            head = code_head()
        except (Failed, OSError) as error:
            failures.append(f"pipeline: {error}")
    if failures:
        for f in failures:
            print(f"sdl3_patch_receipt.py: {f}", file=sys.stderr)
        return 1
    receipt = {
        "schema": SCHEMA,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "code_head_before": head,
        "tree_state": "the evidence files and this receipt's pipeline are those of the commit that adds this receipt",
        "question": "Does the project's SDL3 patch (patches/sdl3) build reproducibly and strictly, behave as its "
                    "checks require on lavapipe and NVIDIA with the validation layer, compile for D3D12, hold the "
                    "DXVK player on the patched SDL to the registered boot and Metal Harbor references, and leave "
                    "the DXVK host's frames unchanged on an equal (stock) SDL?",
        "ok": True,
        "inputs": {
            "pipeline": pipe,
            "toolchain_pins": {"path": "tools/pins.json", "sha256": sha(pins_path)},
            "registered_references": evidence(REGISTRY),
            "sdl3": {k: tools["sdl3"][k] for k in ("version", "commit", "sha256", "patches", "vendor_info",
                                                   "library_sha256")},
            "sdl3_stock_library_sha256": tools["sdl3-stock"]["library_sha256"],
            "installs": {k: shown(v) for k, v in prefixes.items()},
        },
        "checks": checks,
        "scope": ("The runs used the scratch installs named in inputs.installs, built by the pipeline's "
                  "tools/s5/build_host_deps.sh from the pinned sources; the files the pins name there hash to the "
                  "pins above. The shared install (private/s5-tools/install) is held to the same pins by "
                  "check_pins.py at every push. Host 2 does not draw yet, so the stock configuration's replay gate, "
                  "host 2's own detection and its frame equality with host 1 are not in this receipt "
                  "(patches/sdl3/README.md lists them as owed)."),
        "generated_by": "tools/s5/sdl3_patch_receipt.py",
    }
    args.out.write_text(json.dumps(receipt, indent=1) + "\n")
    print(f"SDL3_PATCH_RECEIPT_OK {shown(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
