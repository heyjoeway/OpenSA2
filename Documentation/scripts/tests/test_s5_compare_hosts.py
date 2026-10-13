"""tools/s5/compare_hosts.py and its census, held without a GPU or a window.

The comparison of the second host (host/d3d9gpu on SDL_gpu) with DXVK Native is a
measuring instrument (AGENTS.md rule 5): what it says about R2a rests on its checks
failing when they should. Here every check a-h is fed forged reports, device reports,
times files and censuses built in this file, and each forgery fails its one check, by its
named reason, and no other; the controls' bookkeeping is held to what PLAN.md asks (a
control that does not move its check, moves it for another reason than its fault's or by
more than the fault's exact effect, whose run crashed, or a rule the stream never
exercises, is not a pass); the two hosts are two binaries (sa2_player, sa2_player_gpu),
each run by its own command, and check a fails one file on both hosts or a field of the
player that loaded the second host's library, a field of its own left out (absent is not
null) and a key set at the top, under files or under host_identity that is not the other
report's, check b a device whose own account of its file is not the player that ran; the DXVK-path comparison (dxvk-ab) ignores wall time, the
named allowlist, two named log lines, each side's own checkout's dxvk.conf path and its own
install's DXVK library path (any other path, and the library's bytes, still compared) and
nothing else, holds the identity fields the two binaries changed per side (the after the
DXVK player alone, a before's expected library its own build's, the after's end_request a
run's without --end-fd and a before's that or none of it, every type held), runs each side under its
own checkout's run-time files, names what its two sides differ in, and refuses one player
on both sides, an after whose DXVK path is the commit's (by dxvk-unchanged's own rule, so
the two never disagree on one pair), an after that is not the DXVK player alone, a
compare_hosts.py edited since it started and a before build not held to its commit (a
group or an exact record, its pins and its checkout's run-time files the commit's);
dxvk-unchanged says UNCHANGED only when the DXVK player's exact sources and its run-time
files are the commit's and the commit holds no file an include could find first (before a
file the build read, or a header in the compiler's system directories, by the search list
the build recorded, without which, or with another compiler now, both commands refuse) and
no symbolic link under an include directory; a build is
run only when its four records tie its binaries to the tree it pins (every source they name
pinned, no binary in two records, each player's record exact, naming its translation units
and build script and every file its depfiles name, none through a symbolic link in its
checkout, and its contract its host's),
and each run's player (by the role this tool gave it), its device's file and its
play_report.py verdict to the build and the tree, and a build's record (an exact one made
from a real compiler's depfile here, with the compiler's search list in the build's
environment) is refused when a source moved while it built or a compile read a file through
a symbolic link in the checkout; `run`
refuses a directory an earlier run left files in and a compare_hosts.py edited since it
started; `check` recomputes a run only on
the build, the files and the play_report.py it recorded, with the controls and reference it
recorded, and carries forward the compare_hosts.py that ran it; the clear matrix (check i)
fails on any line, library, GPU or driver not the players'; the receipt holds nothing outside
its schema, records the clear matrix, and is refused for a run whose controls did not all
behave, whose build was not held to the tree, that ran no matrix, that another
compare_hosts.py ran than the one it pins, or whose comparable frames, with every other
run's, are all black, and an --also run of another pipeline or other binaries is refused as
the main run is; it is written once, under the id it is given. The census (host/d3d9gpu/census.cpp, compiled here against the pinned
headers) reads streams written here: the player's stage gates, DXVK's draw classes for all
four draw methods, the clear rule's lineage, a swap chain's Present, and render target 0's
reset of the viewport, and counts no draw whose data is short or missing. The GPU end of
the instrument runs in host/d3d9gpu/test_d3d9gpu.sh.
"""
from __future__ import annotations

import argparse
import copy
import functools
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import compare_hosts as CH  # noqa: E402

INSTALL = ROOT / "private" / "s5-tools" / "install"
GXX = shutil.which(os.environ.get("CXX", "g++"))
# The bytes a forged build's binaries hold, and their hashes, which the forged reports name.
BYTES = {"sa2_player": b"forged player", "sa2_player_gpu": b"forged gpu player", "sa2_frame_census": b"forged census",
         "d3d9gpu_clear_matrix": b"forged matrix", "test_d3d9gpu": b"forged cpu model",
         "test_texcodec": b"forged codec test", "dxvk_smoke": b"forged smoke", "d3d9gpu_state_check": b"forged state"}
PLAYER_SHA = hashlib.sha256(BYTES["sa2_player"]).hexdigest()           # the DXVK host's player
GPU_SHA = hashlib.sha256(BYTES["sa2_player_gpu"]).hexdigest()          # the second host's player
GPU_PATH = "/b/sa2_player_gpu"
# The players' contracts (host/player/generate_expectations.py --host dxvk / sdlgpu).
DXVK_CONTRACT = {"sdl_sha256": "1" * 64, "dxvk_sha256": "d" * 64, "dxvk_config_sha256": "e" * 64,
                 "sdl_version": 3004016, "sdl_revision": "SDL-release"}
GPU_CONTRACT = {"sdl_sha256": "1" * 64, "sdl_version": 3004016, "sdl_revision": "SDL-release"}
# The play_report.py that wrote every forged run's check.json: the tree's.
CHECKER = hashlib.sha256((ROOT / CH.PLAY_REPORT).read_bytes()).hexdigest()
BEGIN = "scene order: BeginScene inside a scene"


# ------------------------------------------------------------------ forged evidence
def census(**over) -> dict:
    c = {"tool": "host/d3d9gpu sa2_frame_census", "stream": "s.sa2s", "stream_sha256": "a" * 64, "stage": 3,
         "frames": 4,
         "draws": {"total": 6, "skipped_no_shader_route": {"DrawPrimitiveUP": {"TRIANGLESTRIP": 5}},
                   "refused": {"DrawPrimitiveUP": {"TRIANGLEFAN": 1}}, "empty": {},
                   "per_frame": [[1, 1, 3], [2, 3, 0], [4, 4, 3]], "after_last_present": 0},
         "up_vertex_bytes": 320, "up_index_bytes": 0,
         "draw_free_frames": [2, 3], "draw_free_frames_back_buffer_drawn_earlier": [],
         "draw_free_frames_never_colour_cleared": [], "frames_made_by_clears_alone": 2,
         "device": {"made": True, "back_buffer": [2, 2, 21], "auto_depth": True},
         "clears": {"records": 3, "host_calls": 3, "back_buffer_full_colour": 3, "back_buffer_partial_colour": 0,
                    "past_edge": 0}}
    c.update(over)
    return c


def player_report(host: str) -> dict:
    return {
        "complete": True, "pass": True, "exit_status": 0, "stop": "end of stream", "errors": 0,
        "readback_failures": 0, "present_failures": 0, "records": {"end": 1}, "presents": 4, "stage": 3,
        "stream_sha256": "a" * 64, "draws": 6,
        "calls": {"handled": 30, "ignored_by_design": 2, "skipped_by_stage": 0, "not_handled": 0},
        "host_failures": {"count": 0, "first": [], "failed_like_guest": 0},
        "not_handled_values": {"count": 0, "first": []},
        "textures": {"dds": 1, "png": 0, "placeholder_binds": 0, "bytes_uploaded": 4096},
        "allowed_host_failures": {"rules": {BEGIN: 1}, "calls": [f"seq 7 Device::BeginScene: host 0x8876086c, "
                                                                f"guest 0x00000000 ({BEGIN})"]},
        "targets": {"queries_held_to_bindings": 0, "get_back_buffer": 0},
        "recorder_descriptions": {"implicit_objects": "desc"},
        "methods": [
            {"type": "Device", "method": "BeginScene", "slot": 41, "handled": 5, "failed": 1,
             "first_fail_hr": "0x8876086c", "first_fail_seq": 7, "hr_differs_from_guest": 1,
             "first_host_hr": "0x8876086c", "guest_eax": "0x00000000"},
            {"type": "Device", "method": "DrawPrimitiveUP", "slot": 83, "handled": 6, "failed": 1,
             "first_fail_hr": "0x8876086c", "first_fail_seq": 9, "hr_differs_from_guest": 1},
            {"type": "Device", "method": "Clear", "slot": 43, "handled": 4}],
        "device": {"created": True, "x87_cw_before": "037f", "x87_cw_after": "037f", "behavior": "0x00000042"},
        "host_identity": {"sdl_file_verified": True, "dxvk_file_verified": host == "dxvk",
                          "config_file_verified": host == "dxvk",
                          "expected_dxvk_sha256": "d" * 64 if host == "dxvk" else None,
                          "expected_config_sha256": "e" * 64 if host == "dxvk" else None, "d3d9_host": host},
        "files": {"player": {"sha256": PLAYER_SHA, "real_path": "/b/sa2_player"} if host == "dxvk" else
                  {"sha256": GPU_SHA, "real_path": GPU_PATH},
                  "libdxvk_d3d9": {"sha256": "d" * 64} if host == "dxvk" else None,
                  "dxvk_conf": {"sha256": "e" * 64} if host == "dxvk" else None},
        "adapter": {"description": "NVIDIA GeForce RTX 5090 (vendor 10de device 2b85)", "driver": "nvd3dum.dll"},
        "options": {"hidden": False},
        "frames_out": {"bgrx_digest": "0x0"},
        # host/player/player.cpp end_request_json: a run given no --end-fd
        "end_request": copy.deepcopy(CH.END_REQUEST_UNUSED),
    }


OWN = "c" * 64          # a before build's own expectation of the second host's library (development_snapshot's contract)


def development_snapshot_report() -> dict:
    """The DXVK run's report of development_snapshot's one player (--d3d9 dxvk): the second host's fields
    with DXVK's answers, its expected library its own build's."""
    rep = player_report("dxvk")
    rep["host_identity"].update(d3d9gpu_file_verified=False, expected_d3d9gpu_sha256=OWN)
    rep["files"]["libsa2_d3d9gpu"] = None
    return rep


def device_report(**over) -> dict:
    d = {"schema": "sa2-d3d9gpu-report/v1", "complete": True, "failure": None, "fault": None, "fault_error": None,
         "library": {"path": GPU_PATH, "sha256": GPU_SHA},
         "sdl_gpu": {"driver": "vulkan", "device_name": "NVIDIA GeForce RTX 5090", "driver_name": "NVIDIA",
                     "driver_version": "595.91.7.0", "driver_info": "595.91.07", "debug_mode": False},
         "fpu": {"unchanged": True},
         "present": {"mode": "window", "presents": 4, "frames_without_swapchain_texture": 0, "failures": 0},
         "draws": {"drawn": 0, "total": 6, "fault_uncounted": 0,
                   "skipped_no_shader_route": {"DrawPrimitiveUP": {"TRIANGLESTRIP": 5}},
                   "refused": {"DrawPrimitiveUP": {"TRIANGLEFAN": 1}}, "empty": {},
                   "per_frame": [[1, 1, 3], [2, 3, 0], [4, 4, 3]], "after_last_present": 0},
         "clears": {"calls": 3, "past_edge": 0}, "uploads": {"up_vertex_bytes": 320, "up_index_bytes": 0},
         "objects": {"created": {"texture": 1}, "live_at_destroy": {}},
         "methods": [{"type": "Device", "method": "DrawPrimitiveUP", "slot": 83, "calls": 6, "failed": 1},
                     {"type": "Device", "method": "BeginScene", "slot": 41, "calls": 5, "failed": 1}],
         "not_implemented": {"count": 0, "methods": {}, "value_count": 0, "values": []}}
    d.update(over)
    return d


def times(hashes: list[str]) -> list[dict]:
    return [{"frame": str(i + 1), "seq": str(10 * (i + 1)), "virtual_us": str(16666 * (i + 1)),
             "guest_frames": str(i + 2), "polls": "3", "wall_us": str(1000 * i + 7), "bgrx_hash": h}
            for i, h in enumerate(hashes)]


def evidence() -> dict:
    log = {"device_name": "NVIDIA GeForce RTX 5090", "driver": "NVIDIA 595.91.7",
           "found_devices": ["NVIDIA GeForce RTX 5090 (NVIDIA 595.91.7)"]}
    dx = {"dir": "d", "report": player_report("dxvk"), "check": {"pass": True}, "checked_by": CHECKER, "exit": 0,
          "times": times(["0x1", "0xb", "0xb", "0x4"]), "device": None, "log": log}
    sd = {"dir": "s", "report": player_report("sdlgpu"), "check": {"pass": True}, "checked_by": CHECKER, "exit": 0,
          "times": times(["0x9", "0xb", "0xb", "0x9"]), "device": device_report(),
          "log": {"device_name": None, "driver": None, "found_devices": []}}
    return {"census": census(), "dxvk": dx, "sdl": sd, "hidden": False, "reference": None, "scope": "r2a-clears"}


def verdicts(ev: dict) -> dict:
    return {c["check"]: c for c in CH.evaluate(ev)}


# DXVK's log of a device made on the forged runs' GPU and driver.
DXVK_LOG = "info:  Creating device:\ninfo:  NVIDIA GeForce RTX 5090:\ninfo:    Driver   : NVIDIA 595.91.7\n"
# Two cases of the clear matrix as d3d9gpu_clear_matrix prints them (host/d3d9gpu/test/clear_matrix.h).
MATRIX_TSV = "full\t00000000,00000000,00000000\tb9eae8ae3ce98325\nrect_list\t00000000,00000000\t48883ab156957f25\n"


def write_matrix(out_dir: Path, build: Path) -> Path:
    """out_dir/matrix as run_matrix leaves it, every host's lines DXVK's, run by `build`'s
    binaries on the forged reports' libraries."""
    d = out_dir / "matrix"
    d.mkdir(parents=True, exist_ok=True)
    for host in CH.MATRIX_HOSTS:
        (d / f"{host}.tsv").write_text(MATRIX_TSV)
    (d / "run.json").write_text(json.dumps({
        "exit": dict.fromkeys(CH.MATRIX_HOSTS, 0),
        "binaries": {b: hashlib.sha256((build / b).read_bytes()).hexdigest() for b in CH.MATRIX_BINARIES},
        "dxvk_library": {"path": "/i/libdxvk_d3d9.so.0", "sha256": "d" * 64}}))
    (d / "d3d9gpu.json").write_text(json.dumps(device_report(library={"path": GPU_PATH, "sha256": GPU_SHA})))
    (d / "dxvk-log").mkdir(exist_ok=True)
    (d / "dxvk-log" / "d3d9gpu_clear_matrix_d3d9.log").write_text(DXVK_LOG)
    return out_dir


def test_the_forged_runs_pass_every_check():
    v = verdicts(evidence())
    assert sorted(v) == list("abcdefg")
    assert {k: c["pass"] for k, c in v.items()} == dict.fromkeys("abcdefg", True)
    e = v["e"]["detail"]
    assert (e["comparable_frames"], e["comparable"], e["mismatched"]) == ([[2, 3]], 2, 0)
    assert e["informational_frames_with_draws_equal"] == 0


def put(ev: dict, path: str, value) -> None:
    *head, last = path.split(".")
    cur = ev
    for part in head:
        cur = cur[part]
    cur[last] = value


def shift_frame(ev: dict) -> None:
    ev["sdl"]["device"]["draws"]["per_frame"] = [[1, 1, 2], [2, 2, 1], [3, 3, 0], [4, 4, 3]]


def refused_two(ev: dict) -> None:
    for d in (ev["census"]["draws"], ev["sdl"]["device"]["draws"]):
        d["refused"] = {"DrawPrimitiveUP": {"TRIANGLEFAN": 2}}


def begin_scene_answered(ev: dict, host: str = "sdl", row: bool = True, allowed: bool = True) -> None:
    """BeginScene inside a scene answered D3D_OK, as the scene-answer control's run shows it:
    not failed and not differing from the guest on the report's row (`row`), not under the
    rule in the allowed failures (`allowed`), not failed on the device's row."""
    if row:
        for m in ev[host]["report"]["methods"]:
            if m["method"] == "BeginScene":
                for k in ("failed", "first_fail_hr", "first_fail_seq", "hr_differs_from_guest", "first_host_hr",
                          "guest_eax"):
                    m.pop(k)
    if allowed:
        a = ev[host]["report"]["allowed_host_failures"]
        a["rules"].pop(BEGIN)
        a["calls"] = [c for c in a["calls"] if not c.endswith(f"({BEGIN})")]
    for m in (ev[host].get("device") or {}).get("methods", []):
        if m["method"] == "BeginScene":
            m["failed"] = 0


@pytest.mark.parametrize("check,change,needle", [
    ("a", lambda ev: put(ev, "sdl.exit", 4), "sdlgpu: the player exited 4"),
    ("a", lambda ev: put(ev, "sdl.check", {"pass": False, "failures": ["x"]}), "sdlgpu: tools/s5/play_report.py check"),
    ("a", lambda ev: put(ev, "sdl.report.host_identity.d3d9_host", "dxvk"), "sdlgpu: host_identity.d3d9_host is 'dxvk'"),
    ("a", lambda ev: put(ev, "sdl.report.host_identity.sdl_file_verified", False),
     "sdlgpu: host_identity.sdl_file_verified is False, not True"),
    ("a", lambda ev: put(ev, "dxvk.report.stream_sha256", "b" * 64), "dxvk: stream_sha256 is"),
    ("a", lambda ev: put(ev, "dxvk.report.host_identity.config_file_verified", False),
     "dxvk: host_identity.config_file_verified is False"),
    # the second host pins no DXVK file (check_s5_exit.py renderer_loaded), and DXVK's run
    # verifies no second-host library
    ("a", lambda ev: put(ev, "sdl.report.files.libdxvk_d3d9", {"sha256": "d" * 64}),
     "sdlgpu: files.libdxvk_d3d9 is {'sha256':"),
    ("a", lambda ev: put(ev, "sdl.report.files.dxvk_conf", {"sha256": "e" * 64}), "sdlgpu: files.dxvk_conf is"),
    ("a", lambda ev: put(ev, "sdl.report.host_identity.dxvk_file_verified", True),
     "sdlgpu: host_identity.dxvk_file_verified is True, not False"),
    # ... nor expects one (the GPU player's contract has no DXVK key)
    ("a", lambda ev: put(ev, "sdl.report.host_identity.expected_dxvk_sha256", "d" * 64),
     f"sdlgpu: host_identity.expected_dxvk_sha256 is '{'d' * 64}', not None"),
    ("a", lambda ev: put(ev, "sdl.report.host_identity.expected_config_sha256", "e" * 64),
     f"sdlgpu: host_identity.expected_config_sha256 is '{'e' * 64}', not None"),
    # ... each of them written, null: a report that leaves one out is not one that wrote it
    # null (the review's NIT: get() read an absent key as None)
    ("a", lambda ev: ev["sdl"]["report"]["files"].pop("libdxvk_d3d9"),
     "sdlgpu: files.libdxvk_d3d9 is absent, not None"),
    ("a", lambda ev: ev["sdl"]["report"]["files"].pop("dxvk_conf"), "sdlgpu: files.dxvk_conf is absent, not None"),
    ("a", lambda ev: ev["sdl"]["report"]["host_identity"].pop("expected_dxvk_sha256"),
     "sdlgpu: host_identity.expected_dxvk_sha256 is absent, not None"),
    ("a", lambda ev: ev["sdl"]["report"]["host_identity"].pop("dxvk_file_verified"),
     "sdlgpu: host_identity.dxvk_file_verified is absent, not False"),
    # the second host's player writes the DXVK player's report fields: one key set at the
    # top, under files and under host_identity
    ("a", lambda ev: put(ev, "sdl.report.frames_written", 4),
     "the reports' keys at the top differ: dxvk alone [], sdlgpu alone ['frames_written']"),
    ("a", lambda ev: put(ev, "sdl.report.files.libsdl3", None),
     "the reports' keys under files differ: dxvk alone [], sdlgpu alone ['libsdl3']"),
    ("a", lambda ev: put(ev, "dxvk.report.host_identity.wsi", "SDL3"),
     "the reports' keys under host_identity differ: dxvk alone ['wsi'], sdlgpu alone []"),
    # a field of the one player that loaded the second host's library, on either host, with
    # any value (its presence: the report is not one of the two binaries')
    ("a", lambda ev: put(ev, "dxvk.report.files.libsa2_d3d9gpu", None),
     "dxvk: the report has files.libsa2_d3d9gpu (None), a field of the player that loaded the second host's "
     "library, not of sa2_player"),
    ("a", lambda ev: put(ev, "dxvk.report.host_identity.d3d9gpu_file_verified", False),
     "dxvk: the report has host_identity.d3d9gpu_file_verified (False)"),
    ("a", lambda ev: put(ev, "sdl.report.host_identity.d3d9gpu_file_verified", True),
     "sdlgpu: the report has host_identity.d3d9gpu_file_verified (True), a field of the player that loaded the "
     "second host's library, not of sa2_player_gpu"),
    ("a", lambda ev: put(ev, "sdl.report.host_identity.expected_d3d9gpu_sha256", GPU_SHA),
     "sdlgpu: the report has host_identity.expected_d3d9gpu_sha256"),
    # one binary on both hosts (renderer_loaded passes it; the device ran from it too)
    ("a", lambda ev: (put(ev, "sdl.report.files.player", dict(ev["dxvk"]["report"]["files"]["player"])),
                      put(ev, "sdl.device.library", {"path": "/b/sa2_player", "sha256": PLAYER_SHA})),
     f"both hosts ran one player file ({PLAYER_SHA}): the second host is its own binary"),
    ("b", lambda ev: put(ev, "sdl.device.fault", "clear-colour"), "fault is 'clear-colour', not None"),
    ("b", lambda ev: put(ev, "sdl.device.draws.drawn", 1), "draws.drawn is 1, not 0"),
    # the device's own account of its file: the player that ran, its hash and its path
    ("b", lambda ev: put(ev, "sdl.device.library.sha256", "d" * 64),
     f"the device's library {'d' * 64} ({GPU_PATH}) is not the player that ran ({GPU_SHA}, {GPU_PATH})"),
    ("b", lambda ev: put(ev, "sdl.device.library.path", "/elsewhere/sa2_player_gpu"),
     f"the device's library {GPU_SHA} (/elsewhere/sa2_player_gpu) is not the player that ran"),
    ("b", lambda ev: put(ev, "sdl.device.library", None), "the device's library None (None) is not the player"),
    ("b", lambda ev: put(ev, "sdl.device.not_implemented.count", 2), "not_implemented.count is 2, not 0"),
    ("b", lambda ev: put(ev, "sdl.device.present.frames_without_swapchain_texture", 3),
     "3 Present(s) found no swapchain texture in a window not hidden"),
    ("b", lambda ev: ev["sdl"]["device"]["methods"].append(
        {"type": "Device", "method": "GetRenderTargetData", "slot": 32, "calls": 4, "failed": 1}),
     "Device::GetRenderTargetData failed 1 time(s) in the device; the player's row shows 0"),
    ("b", lambda ev: put(ev, "sdl.device.objects.live_at_destroy", {"texture": 1}), "left alive"),
    ("b", lambda ev: put(ev, "sdl.device.fpu.unchanged", False), "fpu.unchanged is False"),
    ("b", lambda ev: put(ev, "sdl.device.complete", False), "complete is False, not True"),
    ("b", lambda ev: put(ev, "sdl.device.sdl_gpu.debug_mode", True), "sdl_gpu.debug_mode is True"),
    ("c", shift_frame, "per-frame draws differ from frame 1: device 2, census 3"),
    ("c", lambda ev: put(ev, "sdl.device.draws.after_last_present", 1), "draws after the last Present"),
    ("c", lambda ev: put(ev, "sdl.device.draws.skipped_no_shader_route", {"DrawPrimitiveUP": {"TRIANGLEFAN": 5}}),
     "skipped_no_shader_route: device"),
    ("c", lambda ev: put(ev, "sdl.device.draws.total", 5), "draw totals differ"),
    ("c", lambda ev: put(ev, "sdl.device.uploads.up_vertex_bytes", 321), "UP arena up_vertex_bytes: device 321"),
    ("c", refused_two, "the draw rows' failures (1) are not the device's refused draws (2)"),
    ("d", begin_scene_answered, "Device::BeginScene: dxvk"),
    ("d", lambda ev: put(ev, "sdl.report.device.x87_cw_after", "027f"), "device.x87_cw_after: dxvk '037f'; sdlgpu '027f'"),
    ("d", lambda ev: put(ev, "sdl.report.textures.bytes_uploaded", 4092), "textures: dxvk"),
    ("e", lambda ev: ev["sdl"]["times"][2].update(bgrx_hash="0xc"), "frame 3: bgrx_hash 0xc (sdlgpu) != 0xb (dxvk)"),
    ("e", lambda ev: ev["sdl"]["times"][0].update(seq="11"), "1 row(s) differ outside bgrx_hash and wall_us"),
    ("e", lambda ev: ev["sdl"]["times"].pop(), "times rows: dxvk 4, sdlgpu 3"),
    ("e", lambda ev: ev["dxvk"]["times"][1].update(frame="3"), "dxvk: times row 2 is frame '3', not 2"),
    ("f", lambda ev: put(ev, "census.draw_free_frames_back_buffer_drawn_earlier", [2]),
     "draw-free frame 2 shows a back buffer a draw reached"),
    ("f", lambda ev: put(ev, "sdl.device.clears.calls", 2), "Clear calls: the census sends 3, the device received 2"),
    ("g", lambda ev: put(ev, "sdl.device.sdl_gpu.device_name", "llvmpipe"), "is not SDL_gpu's 'llvmpipe'"),
    ("g", lambda ev: put(ev, "sdl.device.sdl_gpu.driver_info", "595.91.08"), "DXVK's driver 'NVIDIA 595.91.7'"),
])
def test_each_forgery_fails_its_one_check_by_name(check, change, needle):
    ev = evidence()
    change(ev)
    v = verdicts(ev)
    failing = sorted(k for k, c in v.items() if not c["pass"])
    assert failing == [check], (failing, {k: v[k]["failures"] for k in failing})
    assert any(needle in f for f in v[check]["failures"]), v[check]["failures"]


def test_a_missing_device_report_fails_the_device_check_first():
    ev = evidence()
    ev["sdl"]["device"] = None
    v = verdicts(ev)
    assert v["b"]["failures"] == ["the device wrote no report (SA2_D3D9GPU_REPORT)"]
    assert not v["c"]["pass"] and not v["g"]["pass"] and v["e"]["pass"]


def test_placeholder_textures_fail_the_runs_at_stage_3_only():
    ev = evidence()
    for host in ("dxvk", "sdl"):
        ev[host]["report"]["textures"]["placeholder_binds"] = 46
    a = verdicts(ev)["a"]
    assert not a["pass"] and "sdlgpu: textures.placeholder_binds is 46, not 0" in a["failures"]
    ev["census"]["stage"] = 1
    for host in ("dxvk", "sdl"):
        ev[host]["report"]["stage"] = 1
    assert verdicts(ev)["a"]["pass"] is True


def test_an_empty_comparable_set_fails_instead_of_passing_vacuously():
    ev = evidence()
    ev["census"]["draw_free_frames"] = []
    v = verdicts(ev)
    assert v["e"]["pass"] is False
    assert v["e"]["failures"] == ["no comparable frame: the census finds no frame without a draw call"]


def test_a_hidden_window_excuses_frames_without_a_swapchain_texture():
    ev = evidence()
    ev["sdl"]["device"]["present"]["frames_without_swapchain_texture"] = 3
    ev["hidden"] = True
    assert verdicts(ev)["b"]["pass"] is True


def test_the_driver_is_compared_without_leading_zeros():
    assert CH.normalise_version("595.91.07") == "595.91.7"
    assert CH.normalise_version("25.0.7") == "25.0.7"
    assert CH.normalise_version("1.2.x") == "1.2.x"


def test_the_reference_holds_the_fresh_dxvk_run_in_six_fields():
    ev = evidence()
    ev["reference"] = {"path": "ref.tsv", "sha256": "e" * 64, "rows": copy.deepcopy(ev["dxvk"]["times"])}
    for row in ev["reference"]["rows"]:
        row["wall_us"] = "999999"
    assert verdicts(ev)["h"]["pass"] is True
    ev["reference"]["rows"][2]["polls"] = "4"
    h = verdicts(ev)["h"]
    assert h["pass"] is False
    assert h["failures"] == ["frame 3: ['polls'] differ from the reference: the driver or the environment drifted"]


# ------------------------------------------------------------------ the clear matrix (check i)
def with_matrix(tmp_path: Path, change=None) -> dict:
    ev = evidence()
    write_matrix(tmp_path, forged_build(tmp_path / "bin"))
    if change:
        change(tmp_path / "matrix")
    ev["matrix"] = CH.load_matrix(tmp_path)
    return ev


def test_the_clear_matrix_equal_on_every_host_passes(tmp_path):
    v = verdicts(with_matrix(tmp_path))
    assert {k: c["pass"] for k, c in v.items()} == dict.fromkeys("abcdefgi", True)
    assert (v["i"]["detail"]["cases"], v["i"]["detail"]["names"]) == (2, ["full", "rect_list"])
    assert CH.load_matrix(tmp_path / "none") is None and "i" not in verdicts(evidence())


def matrix_meta(change):
    def edit(d: Path):
        meta = json.loads((d / "run.json").read_text())
        change(meta)
        (d / "run.json").write_text(json.dumps(meta))
    return edit


def matrix_device(change):
    def edit(d: Path):
        dev = json.loads((d / "d3d9gpu.json").read_text())
        change(dev)
        (d / "d3d9gpu.json").write_text(json.dumps(dev))
    return edit


@pytest.mark.parametrize("change,needle", [
    (lambda d: (d / "sdlgpu.tsv").write_text(MATRIX_TSV.replace("48883ab156957f25", "48883ab156957f26")),
     "sdlgpu: line 2 is 'rect_list\\t00000000,00000000\\t48883ab156957f26', DXVK's"),
    (lambda d: (d / "cpu.tsv").write_text(MATRIX_TSV.splitlines()[0] + "\n"), "cpu: line 2 is '<none>', DXVK's"),
    (lambda d: (d / "cpu.tsv").unlink(), "cpu: line 1 is '<none>'"),
    (lambda d: [(d / f"{h}.tsv").write_text("") for h in CH.MATRIX_HOSTS], "dxvk: the clear matrix printed no case"),
    (matrix_meta(lambda m: m["exit"].update(sdlgpu=3)), "sdlgpu: the clear matrix exited 3"),
    (matrix_meta(lambda m: m["exit"].pop("cpu")), "cpu: the clear matrix exited None"),
    (matrix_meta(lambda m: m["dxvk_library"].update(sha256="7" * 64)),
     f"dxvk: the matrix ran DXVK's library {'7' * 64}, not the DXVK run's ({'d' * 64})"),
    (matrix_device(lambda dev: dev["library"].update(sha256="7" * 64)),
     f"sdlgpu: the matrix ran the player {'7' * 64}, not the second host's run's ({GPU_SHA})"),
    # the SDL_gpu leg on the DXVK host's player (the one-binary build's shape)
    (matrix_device(lambda dev: dev["library"].update(sha256=PLAYER_SHA)),
     f"sdlgpu: the matrix ran the player {PLAYER_SHA}, not the second host's run's ({GPU_SHA})"),
    (lambda d: (d / "d3d9gpu.json").unlink(), "sdlgpu: the matrix ran the player None"),
    (matrix_device(lambda dev: dev.update(fault="clear-colour")), "fault 'clear-colour'"),
    (matrix_device(lambda dev: dev.update(complete=False)), "the matrix's device report is complete False"),
    # the matrix on another GPU or driver than the players (the review's finding 8)
    (lambda d: (d / "dxvk-log" / "d3d9gpu_clear_matrix_d3d9.log").write_text(DXVK_LOG.replace("RTX 5090", "RTX 4090")),
     "dxvk: the matrix ran on device_name 'NVIDIA GeForce RTX 4090', the DXVK run on 'NVIDIA GeForce RTX 5090'"),
    (lambda d: (d / "dxvk-log" / "d3d9gpu_clear_matrix_d3d9.log").unlink(), "dxvk: the matrix ran on device_name None"),
    (matrix_device(lambda dev: dev["sdl_gpu"].update(driver_info="595.91.08")),
     "sdlgpu: the matrix ran on driver_info '595.91.08', the second host's run on '595.91.07'"),
])
def test_each_matrix_forgery_fails_the_matrix_check_by_name(tmp_path, change, needle):
    v = verdicts(with_matrix(tmp_path, change))
    failing = sorted(k for k, c in v.items() if not c["pass"])
    assert failing == ["i"], (failing, {k: v[k]["failures"] for k in failing})
    assert any(needle in f for f in v["i"]["failures"]), v["i"]["failures"]


def test_a_matrix_run_by_other_binaries_than_the_build_s_is_named(tmp_path):
    build = forged_build(tmp_path / "bin")
    write_matrix(tmp_path, build)
    matrix_meta(lambda m: m["binaries"].update(test_d3d9gpu="7" * 64))(tmp_path / "matrix")
    prov = CH.provenance(build, CH.pipeline(), matrix=CH.load_matrix(tmp_path))
    assert prov["failures"] == [f"{CH.rel(tmp_path / 'matrix')}: the matrix's test_d3d9gpu ({'7' * 64}) is not the "
                                f"build's ({hashlib.sha256(BYTES['test_d3d9gpu']).hexdigest()})"]
    # its SDL_gpu leg is the second host's player (C6): another file there is named too
    write_matrix(tmp_path, build)
    matrix_meta(lambda m: m["binaries"].update(sa2_player_gpu=PLAYER_SHA))(tmp_path / "matrix")
    prov = CH.provenance(build, CH.pipeline(), matrix=CH.load_matrix(tmp_path))
    assert prov["failures"] == [f"{CH.rel(tmp_path / 'matrix')}: the matrix's sa2_player_gpu ({PLAYER_SHA}) is not "
                                f"the build's ({GPU_SHA})"]
    matrix_meta(lambda m: m["binaries"].pop("sa2_player_gpu"))(tmp_path / "matrix")
    prov = CH.provenance(build, CH.pipeline(), matrix=CH.load_matrix(tmp_path))
    assert prov["failures"] == [f"{CH.rel(tmp_path / 'matrix')}: the matrix's sa2_player_gpu (None) is not "
                                f"the build's ({GPU_SHA})"]


# ------------------------------------------------------------------ controls
def control(name: str) -> dict:
    return next(c for c in CH.CONTROLS if c["name"] == name)


def under(fault: str, change=None) -> dict:
    """evidence() with the device under `fault`, and `change` applied."""
    ev = evidence()
    ev["sdl"]["device"]["fault"] = fault
    if change:
        change(ev)
    return ev


def both_comparable_changed(ev: dict) -> None:
    ev["sdl"]["times"][1]["bgrx_hash"] = "0xc"
    ev["sdl"]["times"][2]["bgrx_hash"] = "0xc"


def one_left_uncounted(ev: dict) -> None:
    """count-draw as the device does it: the first skipped draw (frame 1's) uncounted."""
    d = ev["sdl"]["device"]["draws"]
    d.update(total=5, fault_uncounted=1, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 4}},
             per_frame=[[1, 1, 2], [2, 3, 0], [4, 4, 3]])


def test_a_control_that_moves_its_check_is_as_required():
    out = CH.control_outcome(control("clear-colour"), under("clear-colour", both_comparable_changed))
    assert (out["got"], out["as_required"], out["outcome"], out["collateral"]) == ("fail", True, "as required", [])
    assert (out["must_differ"], out["differ"]) == ([[2, 3]], [[2, 3]])


@pytest.mark.parametrize("name,change", [
    ("clear-colour", both_comparable_changed),
    ("count-draw", one_left_uncounted),
    ("scene-answer", begin_scene_answered),
    ("present-scribble", None),
    ("present-scribble+clear-noop", both_comparable_changed),
])
def test_each_control_moved_by_its_fault_alone_is_as_required(name, change):
    c = control(name)
    out = CH.control_outcome(c, under(c["faults"], change))
    assert (out["got"], out["outcome"], out["collateral"]) == (c["want"], "as required", [])


def crashed(ev: dict) -> None:
    """The review's p1: the faulted run died (exit 139) with no frame read back."""
    ev["sdl"]["exit"] = 139
    ev["sdl"]["times"] = []
    ev["sdl"]["check"] = {"pass": False, "failures": ["no END"]}


@pytest.mark.parametrize("name", [c["name"] for c in CH.CONTROLS])
def test_a_control_whose_run_crashed_is_not_as_required(name):
    """Its target fails (no rows, no hashes, no draws) but not for the fault's reason."""
    c = control(name)
    out = CH.control_outcome(c, under(c["faults"], crashed))
    assert (out["as_required"], out["outcome"]) == (False, "NOT as required")
    assert "a runs: sdlgpu: the player exited 139" in out["collateral"]


@pytest.mark.parametrize("name,change,needle", [
    # a row lost or shifted is not a clear colour
    ("clear-colour", lambda ev: (both_comparable_changed(ev), ev["sdl"]["times"][0].update(seq="11")),
     "e: 1 row(s) differ outside bgrx_hash and wall_us, first at row 1 (not a comparable frame's hash)"),
    ("present-scribble+clear-noop", lambda ev: (both_comparable_changed(ev), ev["sdl"]["times"].pop()),
     "e: times rows: dxvk 4, sdlgpu 3, census frames 4 (not a comparable frame's hash)"),
    # count-draw must drop exactly the draw its report says it dropped
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.device.draws.total", 4)),
     "c: draw totals differ: {'census': 6, 'dxvk report': 6, 'sdlgpu report': 6, 'device': 5} (with the 1 draw(s) "
     "left uncounted put back)"),
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.device.draws.fault_uncounted", 0)),
     "b device: draws.fault_uncounted is 0, not 1"),
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.report.draws", 5)), "d answers: draws: dxvk 6"),
    # both players' reports off the census by one more draw: not the device's dropped one
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.report.draws", 7), put(ev, "dxvk.report.draws", 7)),
     "c: draw totals differ: {'census': 6, 'dxvk report': 7, 'sdlgpu report': 7, 'device': 6}"),
    # ... and nothing else in c (the confirm's p5: these were excused while the totals held)
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.device.uploads.up_vertex_bytes", 384)),
     "c: UP arena up_vertex_bytes: device 384, census 320 (with the 1 draw(s) left uncounted put back)"),
    ("count-draw", lambda ev: (one_left_uncounted(ev), ev["sdl"]["device"]["draws"]["refused"]["DrawPrimitiveUP"].update(
        POINTLIST=1)), "c: refused: device {'DrawPrimitiveUP': {'TRIANGLEFAN': 1, 'POINTLIST': 1}}; census"),
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.device.draws.empty", {"DrawPrimitive": {"POINTLIST": 1}})),
     "c: empty: device {'DrawPrimitive': {'POINTLIST': 1}}; census {}"),
    # one class short by two and another over by one is not one draw left uncounted
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.device.draws.skipped_no_shader_route",
                                                           {"DrawPrimitiveUP": {"TRIANGLESTRIP": 3, "TRIANGLEFAN": 1}})),
     "c: the device's skipped draws are short of the census's by {'DrawPrimitiveUP TRIANGLEFAN': -1, "
     "'DrawPrimitiveUP TRIANGLESTRIP': 2}, not by the 1 draw(s) it left uncounted in one class"),
    # two draws left out of one class and one frame are not the one it left uncounted
    ("count-draw", lambda ev: (one_left_uncounted(ev), ev["sdl"]["device"]["draws"].update(
        total=4, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 3}}, per_frame=[[1, 1, 1], [2, 3, 0],
                                                                                              [4, 4, 3]])),
     "c: the device's skipped draws are short of the census's by {'DrawPrimitiveUP TRIANGLESTRIP': 2}, not by the 1"),
    ("count-draw", lambda ev: (one_left_uncounted(ev), ev["sdl"]["device"]["draws"].update(
        total=4, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 3}}, per_frame=[[1, 1, 1], [2, 3, 0],
                                                                                              [4, 4, 3]])),
     "c: the device's draws per frame are short of the census's by {1: 2}, not by the 1 draw(s)"),
    # nor is a draw moved from frame 4 to frame 2 besides the one left out of frame 1
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.device.draws.per_frame",
                                                           [[1, 1, 2], [2, 2, 1], [3, 3, 0], [4, 4, 2]])),
     "c: the device's draws per frame are short of the census's by {1: 1, 2: -1, 4: 1}, not by the 1 draw(s)"),
    ("count-draw", lambda ev: (one_left_uncounted(ev), put(ev, "sdl.device.draws.after_last_present", 1)),
     "c: the device's draws per frame are short of the census's by {1: 1, 'after the last Present': -1}"),
    # scene-answer may change BeginScene's answers under the rule and nothing else in d
    ("scene-answer", lambda ev: (begin_scene_answered(ev), put(ev, "sdl.report.textures.bytes_uploaded", 4092)),
     "d: textures: dxvk"),
    ("scene-answer", lambda ev: (begin_scene_answered(ev), put(ev, "sdl.report.calls.handled", 31)), "d: calls: dxvk"),
    ("scene-answer", lambda ev: (begin_scene_answered(ev), ev["sdl"]["report"]["methods"][2].update(handled=3)),
     "d: Device::Clear: dxvk"),
    # the confirm's p5: another rule's allowed failure on the control's run was excused
    ("scene-answer", lambda ev: (begin_scene_answered(ev), ev["sdl"]["report"]["allowed_host_failures"].update(
        rules={"scene order: EndScene with no scene open": 1},
        calls=["seq 9 Device::EndScene: host 0x8876086c, guest 0x00000000 (scene order: EndScene with no scene open)"])),
     "d: allowed_host_failures: dxvk {'rules': {}, 'calls': []}; sdlgpu {'rules': {'scene order: EndScene"),
    # the answers changed on the row and the rule still listed, or the reverse
    ("scene-answer", lambda ev: begin_scene_answered(ev, allowed=False),
     f"d: allowed_host_failures: dxvk {{'rules': {{}}, 'calls': []}}; sdlgpu {{'rules': {{'{BEGIN}': 1}}"),
    ("scene-answer", lambda ev: begin_scene_answered(ev, row=False), "d: Device::BeginScene: dxvk"),
    # BeginScene's row moved beyond its answers under the rule
    ("scene-answer", lambda ev: (begin_scene_answered(ev), ev["sdl"]["report"]["methods"][0].update(handled=4)),
     "d: Device::BeginScene: dxvk {'type': 'Device', 'method': 'BeginScene', 'slot': 41, 'handled': 5}; sdlgpu"),
    # the device's report is excused its fault's name, not a failure
    ("present-scribble", lambda ev: put(ev, "sdl.device.failure", "lost"), "b device: failure is 'lost', not None"),
])
def test_a_control_must_move_its_check_for_its_named_reason_alone(name, change, needle):
    c = control(name)
    out = CH.control_outcome(c, under(c["faults"], change))
    assert out["outcome"] == "NOT as required"
    assert any(x.startswith(needle) for x in out["collateral"]), out["collateral"]


def test_a_control_that_moves_only_some_frames_is_not_as_required():
    ev = under("clear-colour", lambda ev: ev["sdl"]["times"][1].update(bgrx_hash="0xc"))
    out = CH.control_outcome(control("clear-colour"), ev)
    assert (out["got"], out["as_required"], out["outcome"]) == ("fail", False, "NOT as required")


def test_a_control_that_leaves_its_check_passing_fails_the_controls():
    out = CH.control_outcome(control("clear-colour"), under("clear-colour"))
    assert (out["got"], out["as_required"]) == ("pass", False)


def test_a_must_pass_control_that_fails_is_not_as_required():
    ev = under("present-scribble", lambda ev: ev["sdl"]["times"][1].update(bgrx_hash="0xc"))
    out = CH.control_outcome(control("present-scribble"), ev)
    assert (out["want"], out["got"], out["as_required"]) == ("pass", "fail", False)
    assert CH.control_outcome(control("present-scribble"), under("present-scribble"))["as_required"] is True


def test_a_rule_the_stream_never_exercises_is_not_a_pass():
    def no_rule(ev):
        for host in ("dxvk", "sdl"):
            begin_scene_answered(ev, host)
            ev[host]["report"]["allowed_host_failures"] = {"rules": {}, "calls": []}
    out = CH.control_outcome(control("scene-answer"), under("scene-answer", no_rule))
    assert (out["exercised"], out["as_required"], out["outcome"]) == (False, False, "not exercised")
    assert CH.control_outcome(control("scene-answer"), under("scene-answer", begin_scene_answered))["outcome"] == \
        "as required"


def test_count_draw_is_not_exercised_without_a_real_draw():
    def no_draws(ev):
        ev["census"]["draws"]["skipped_no_shader_route"] = {}
    assert CH.control_outcome(control("count-draw"), under("count-draw", no_draws))["outcome"] == "not exercised"


def every_draw_skipped(ev: dict) -> None:
    """The census and the device count no refused or empty draw (the refused fan skipped
    instead): every draw is skipped, so the stream's first draw is the one count-draw leaves
    uncounted (host/d3d9gpu/d9g_device.cpp Device::count_draw)."""
    for d in (ev["census"]["draws"], ev["sdl"]["device"]["draws"]):
        d.update(refused={}, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 5, "TRIANGLEFAN": 1}})
    for host in ("dxvk", "sdl"):
        for k in ("failed", "first_fail_hr", "first_fail_seq", "hr_differs_from_guest"):
            ev[host]["report"]["methods"][1].pop(k)
    ev["sdl"]["device"]["methods"][0]["failed"] = 0


def only_after_the_last_present(ev: dict) -> None:
    """Every draw the census counts skipped, and after the last Present: one strip."""
    ev["census"]["draws"].update(total=1, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}},
                                 refused={}, per_frame=[[1, 4, 0]], after_last_present=1)
    ev["sdl"]["device"]["draws"].update(total=1, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}},
                                        refused={}, per_frame=[[1, 4, 0]], after_last_present=1)
    for host in ("dxvk", "sdl"):
        ev[host]["report"]["draws"] = 1
        for k in ("failed", "first_fail_hr", "first_fail_seq", "hr_differs_from_guest"):
            ev[host]["report"]["methods"][1].pop(k)
    ev["sdl"]["device"]["methods"][0]["failed"] = 0


def refused_three(ev: dict) -> None:
    """The census and the device count three refused fans, two of them in frame 1: frame 1
    may hold only refused draws, so the first skipped draw may be frame 4's."""
    for d in (ev["census"]["draws"], ev["sdl"]["device"]["draws"]):
        d.update(skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 3}},
                 refused={"DrawPrimitiveUP": {"TRIANGLEFAN": 3}}, per_frame=[[1, 1, 2], [2, 3, 0], [4, 4, 4]])
    for host in ("dxvk", "sdl"):
        ev[host]["report"]["methods"][1]["failed"] = 3


def empty_first(ev: dict) -> None:
    """Frame 1's one draw empty, every other skipped (no refused draw): the first skipped draw
    is frame 4's."""
    for d in (ev["census"]["draws"], ev["sdl"]["device"]["draws"]):
        d.update(skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 5}}, refused={},
                 empty={"DrawPrimitive": {"POINTLIST": 1}}, per_frame=[[1, 1, 1], [2, 3, 0], [4, 4, 5]])
    for host in ("dxvk", "sdl"):
        for k in ("failed", "first_fail_hr", "first_fail_seq", "hr_differs_from_guest"):
            ev[host]["report"]["methods"][1].pop(k)
    ev["sdl"]["device"]["methods"][0]["failed"] = 0


STRIPS_4 = {"DrawPrimitiveUP": {"TRIANGLESTRIP": 4, "TRIANGLEFAN": 1}}


@pytest.mark.parametrize("base,device,needle", [
    # the fault leaves out the stream's first skipped draw: frame 1's where every draw is skipped ...
    (every_draw_skipped, dict(total=5, skipped_no_shader_route=STRIPS_4, per_frame=[[1, 1, 2], [2, 3, 0], [4, 4, 3]]),
     None),
    # ... not frame 4's (the review's P1) ...
    (every_draw_skipped, dict(total=5, skipped_no_shader_route=STRIPS_4, per_frame=[[1, 1, 3], [2, 3, 0], [4, 4, 2]]),
     "c: the device is short of the census in frame 4, but the first skipped draw, the one the fault leaves out, is "
     "in frame 1 or earlier: the census's draws to there outnumber the 0 it refused or found empty"),
    # ... nor where frame 1's three draws outnumber the one refused ...
    (lambda ev: None, dict(total=5, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 4}},
                           per_frame=[[1, 1, 3], [2, 3, 0], [4, 4, 2]]),
     "c: the device is short of the census in frame 4, but the first skipped draw, the one the fault leaves out, is "
     "in frame 1 or earlier: the census's draws to there outnumber the 1 it refused or found empty"),
    # ... though where frame 1's two may both be refused it can be frame 4's, or frame 1's ...
    (refused_three, dict(total=5, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 2}},
                         per_frame=[[1, 1, 2], [2, 3, 0], [4, 4, 3]]), None),
    (refused_three, dict(total=5, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 2}},
                         per_frame=[[1, 1, 1], [2, 3, 0], [4, 4, 4]]), None),
    # ... and an empty draw counts as a refused one does
    (empty_first, dict(total=5, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 4}},
                       per_frame=[[1, 1, 1], [2, 3, 0], [4, 4, 4]]), None),
    # after the last Present where no draw comes before it ...
    (only_after_the_last_present, dict(total=0, skipped_no_shader_route={}, after_last_present=0), None),
    # ... not where frame 1 has the first (the review's P2)
    (lambda ev: (every_draw_skipped(ev), ev["census"]["draws"].update(total=7, after_last_present=1,
                 skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 6, "TRIANGLEFAN": 1}}),
                 put(ev, "dxvk.report.draws", 7), put(ev, "sdl.report.draws", 7)),
     dict(total=6, after_last_present=0),
     "c: the device is short of the census after the last Present, but the first skipped draw, the one the fault "
     "leaves out, is in frame 1 or earlier"),
    # a count missing on the device's side is not 0 (the review's missing fields) ...
    (only_after_the_last_present, dict(total=0, skipped_no_shader_route={}, after_last_present=None),
     "c: the device's draws per frame are short of the census's by {'after the last Present': 'census 1, device "
     "None'}, not by the 1 draw(s)"),
    (lambda ev: put(ev, "census.draws.per_frame", [[1, 1, 1], [2, 3, 0], [4, 4, 5]]),
     dict(total=5, skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 4}}, per_frame=[[2, 3, 0], [4, 4, 5]]),
     "c: the device's draws per frame are short of the census's by {1: 'census 1, device None'}, not by the 1"),
    # ... and JSON's true is no count
    (lambda ev: None, dict(total=5, fault_uncounted=True, per_frame=[[1, 1, 2], [2, 3, 0], [4, 4, 3]],
                           skipped_no_shader_route={"DrawPrimitiveUP": {"TRIANGLESTRIP": 4}}),
     "c: the device's skipped draws are short of the census's by {'DrawPrimitiveUP TRIANGLESTRIP': 1}, not by the "
     "True draw(s)"),
], ids=["first-frame", "frame-4", "frame-4-past-the-refused", "frame-4-within-the-refused",
        "frame-1-within-the-refused", "frame-4-past-an-empty-draw", "after-last-present",
        "after-last-present-with-frame-1", "after-last-missing", "frame-missing", "uncounted-true"])
def test_count_draw_puts_back_the_draw_the_fault_left_out_and_no_other(base, device, needle):
    """The draw count-draw leaves uncounted is the stream's first skipped draw, so the frame it
    is put back in is no later than the first by which the census's draws outnumber those it
    refused or found empty (where it counts none, its first frame with a draw, or after the
    last Present when none comes before); every count the device leaves out or writes as
    other than a whole number is a difference, never 0."""
    def change(ev):
        base(ev)
        ev["sdl"]["device"]["draws"].update(fault_uncounted=1)
        ev["sdl"]["device"]["draws"].update(device)
    out = CH.control_outcome(control("count-draw"), under("count-draw", change))
    if needle is None:
        assert (out["got"], out["outcome"], out["collateral"]) == ("fail", "as required", [])
        return
    assert out["outcome"] == "NOT as required"
    assert any(x.startswith(needle) for x in out["collateral"]), out["collateral"]


OK, INVALID, LOST = "0x00000000", "0x8876086c", "0x88760868"     # D3D_OK, D3DERR_INVALIDCALL, _DEVICELOST
END = "scene order: EndScene with no scene open"


def allowed_call(seq: int, host: str, guest: str, rule: str = BEGIN, method: str = "BeginScene") -> str:
    """One allowed call as host/player/player.cpp Player::count lists it."""
    return f"seq {seq} Device::{method}: host {host}, guest {guest} ({rule})"


def fails(n: int, seq: int, hr: str = INVALID) -> dict:
    return {"failed": n, "first_fail_hr": hr, "first_fail_seq": seq}


def differs(n: int, host: str, guest: str) -> dict:
    return {"hr_differs_from_guest": n, "first_host_hr": host, "guest_eax": guest}


def scene_answer_on(dxvk_row: dict, control_row: dict, calls: list[str], rules: dict | None = None):
    """scene-answer's evidence: BeginScene's row on DXVK's run (`dxvk_row`) and on the
    control's (`control_row`), and the allowed calls DXVK's report lists under `rules` (by
    default each under the rule); the control's run lists them less the rule's, which it
    answered D3D_OK."""
    def change(ev):
        for host, row in (("dxvk", dxvk_row), ("sdl", control_row)):
            ev[host]["report"]["methods"][0] = {"type": "Device", "method": "BeginScene", "slot": 41, "handled": 5,
                                                **row}
        rs = rules or {BEGIN: len(calls)}
        ev["dxvk"]["report"]["allowed_host_failures"] = {"rules": rs, "calls": calls}
        ev["sdl"]["report"]["allowed_host_failures"] = {"rules": {k: v for k, v in rs.items() if k != BEGIN},
                                                        "calls": [c for c in calls if not c.endswith(f"({BEGIN})")]}
        ev["sdl"]["device"]["methods"][1]["failed"] = 0
    return change


@pytest.mark.parametrize("dxvk_row,control_row,calls,rules,needle", [
    # the guest failed the rule's BeginScene too: answered D3D_OK it differs, first (the review's P3) ...
    pytest.param(fails(1, 7), differs(1, OK, INVALID), [allowed_call(7, INVALID, INVALID)], None, None,
                 id="guest-failed-too"),
    # ... and a control that does not show the new difference is not as required (P4)
    pytest.param(fails(1, 7), {}, [allowed_call(7, INVALID, INVALID)], None, "d: Device::BeginScene: dxvk {'type': "
                 "'Device', 'method': 'BeginScene', 'slot': 41, 'handled': 5, 'hr_differs_from_guest': 1, "
                 "'first_host_hr': '0x00000000', 'guest_eax': '0x8876086c'}", id="guest-failed-too-not-shown"),
    # the first differing answer the fault leaves is the lowest seq's
    pytest.param({**fails(2, 7), **differs(1, INVALID, LOST)}, differs(2, OK, INVALID),
                 [allowed_call(7, INVALID, INVALID), allowed_call(12, INVALID, LOST)], None, None, id="lowest-seq"),
    # an answer outside the rule differed first in DXVK's report: it stays first
    pytest.param({**fails(1, 7), **differs(2, OK, LOST)}, differs(1, OK, LOST), [allowed_call(7, INVALID, OK)], None,
                 None, id="first-differing-outside-the-rule"),
    # the real boot run's shape: another rule listed, a BeginScene failed like the guest's
    # (seq 3) before the rule's; the control keeps both (the review's tests) ...
    pytest.param({**fails(2, 3), **differs(1, INVALID, OK)}, fails(1, 3),
                 [allowed_call(5, INVALID, OK, END, "EndScene"), allowed_call(7, INVALID, OK)], {END: 1, BEGIN: 1},
                 None, id="another-rule-and-a-failure-kept"),
    # ... and a control whose fault reached that other BeginScene too is not as required
    pytest.param({**fails(2, 3), **differs(1, INVALID, OK)}, {},
                 [allowed_call(5, INVALID, OK, END, "EndScene"), allowed_call(7, INVALID, OK)], {END: 1, BEGIN: 1},
                 "d: Device::BeginScene: dxvk {'type': 'Device', 'method': 'BeginScene', 'slot': 41, 'handled': 5, "
                 "'failed': 1, 'first_fail_hr': '0x8876086c', 'first_fail_seq': 3}", id="another-beginscene-reached"),
    # fail closed, each saying why: the allowed calls cut at the player's 20 ...
    pytest.param({**fails(1, 7), **differs(1, INVALID, OK)}, {},
                 [allowed_call(7, INVALID, OK)] + [allowed_call(9 + i, INVALID, OK, END, "EndScene")
                                                   for i in range(19)],
                 {BEGIN: 1, END: 20}, f"d: DXVK's report lists 20 of its 21 allowed call(s): the allowed calls left "
                 f"once the fault answers '{BEGIN}' cannot be derived", id="list-cut"),
    # ... a call under the rule not a BeginScene answer ...
    pytest.param({**fails(1, 7), **differs(1, INVALID, OK)}, {}, [allowed_call(7, INVALID, OK, BEGIN, "EndScene")],
                 None, f"d: DXVK's report lists 0 of its 1 call(s) under '{BEGIN}' as BeginScene answers: the fault's "
                 f"answers cannot be derived call by call", id="call-not-read"),
    # ... more under the rule than listed ...
    pytest.param({**fails(2, 7), **differs(2, INVALID, OK)}, {}, [allowed_call(7, INVALID, OK)], {BEGIN: 2},
                 f"d: DXVK's report lists 1 of its 2 call(s) under '{BEGIN}' as BeginScene answers",
                 id="call-unlisted"),
    # ... a row counting fewer failures, or differing answers, than the rule's calls make ...
    pytest.param(differs(1, INVALID, OK), {}, [allowed_call(7, INVALID, OK)], None,
                 f"counts fewer failures than its 1 call(s) under '{BEGIN}'", id="row-fewer-failures"),
    pytest.param(fails(1, 7), {}, [allowed_call(7, INVALID, OK)], None,
                 f"counts fewer answers differing from the guest's than its 1 call(s) under '{BEGIN}' that differ",
                 id="row-fewer-differing"),
    # ... a first failure that was the rule's, with another left ...
    pytest.param({**fails(2, 7), **differs(1, INVALID, OK)}, fails(1, 9), [allowed_call(7, INVALID, OK)], None,
                 f"d: DXVK's first failed BeginScene (seq 7) is one '{BEGIN}' allowed: the first failure the fault "
                 f"leaves is not in its report", id="first-failure-the-rule-s"),
    # ... a first differing answer that may have been the rule's, with another left ...
    pytest.param({**fails(1, 7), **differs(2, INVALID, OK)}, differs(1, INVALID, OK), [allowed_call(7, INVALID, OK)],
                 None, f"d: DXVK's first BeginScene answer differing from the guest's, with '{BEGIN}' answered D3D_OK, "
                 f"is not in its report (1 differing outside the rule, 0 under it)", id="first-differing-unknown"),
    # ... or answers outside the rule and the fault's both differing, in an order not reported
    pytest.param({**fails(1, 7), **differs(1, OK, LOST)}, differs(2, OK, LOST), [allowed_call(7, INVALID, INVALID)],
                 None, "is not in its report (1 differing outside the rule, 1 under it)", id="order-unknown"),
])
def test_scene_answer_holds_dxvk_s_report_answered_d3d_ok_call_by_call(dxvk_row, control_row, calls, rules, needle):
    """The control's run against DXVK's report as Player::count would have kept it with every
    BeginScene the rule allowed answered D3D_OK (answered_ok): as required where the run
    shows exactly that, and not as required, saying why, where it does not or the report
    cannot give it."""
    out = CH.control_outcome(control("scene-answer"),
                             under("scene-answer", scene_answer_on(dxvk_row, control_row, calls, rules)))
    if needle is None:
        assert (out["got"], out["outcome"], out["collateral"]) == ("fail", "as required", [])
        return
    assert out["outcome"] == "NOT as required"
    assert any(needle in x for x in out["collateral"]), out["collateral"]


def test_the_scribble_cannot_show_before_the_first_present():
    def frame_one(ev):
        ev["census"]["draw_free_frames"] = [1]
        ev["census"]["draws"]["per_frame"] = [[1, 1, 0], [2, 4, 2]]
        ev["sdl"]["times"][0]["bgrx_hash"] = "0x1"
    out = CH.control_outcome(control("present-scribble+clear-noop"), under("present-scribble,clear-noop", frame_one))
    assert (out["exercised"], out["as_required"]) == (False, False)


def test_a_control_run_under_another_fault_is_not_as_required():
    out = CH.control_outcome(control("clear-colour"), under("count-draw", lambda ev: put(ev, "sdl.device.draws.total", 5)))
    assert out["as_required"] is False
    assert out["failures"][0] == "the run's device report names fault 'count-draw', not 'clear-colour'"


def test_the_verdict_with_controls_needs_every_control(tmp_path):
    (tmp_path / "census.json").write_text(json.dumps(census()))
    stream = tmp_path / "s.sa2s"
    stream.write_bytes(b"SA2D3D9S")
    build = forged_build(tmp_path / "bin")
    args = argparse.Namespace(scope="r2a-clears", build=str(build), stream=str(stream), hidden=False, reference=None)
    ev = evidence()
    good = [{"as_required": True}, {"as_required": True}]
    doc = CH.assemble(tmp_path, args, census(), ev["dxvk"], ev["sdl"], good, "t")
    assert (doc["verdict"], doc["verdict_with_controls"]) == ("PASS", "PASS")
    assert doc["inputs"]["provenance"]["pass"] is True and doc["inputs"]["provenance"]["runs_held"] == 2
    doc = CH.assemble(tmp_path, args, census(), ev["dxvk"], ev["sdl"], good + [{"as_required": False}], "t")
    assert (doc["verdict"], doc["verdict_with_controls"]) == ("PASS", "FAIL")
    doc = CH.assemble(tmp_path, args, census(), ev["dxvk"], ev["sdl"], None, "t")
    assert doc["verdict_with_controls"] == "NOT_RUN"
    # everything the build records name is pinned (the review's finding 6)
    assert {"tools/s5/compare_hosts.py", "host/d3d9gpu/d9g_clear.cpp", "tools/pins.json",
            "host/dxvk_smoke.cpp"} <= set(doc["inputs"]["pipeline"])
    # Every check passing is not a PASS when a run's player is not the build's (the device's
    # own account of its file agreeing with the run's, so check b passes).
    ev["sdl"]["report"]["files"]["player"]["sha256"] = ev["sdl"]["device"]["library"]["sha256"] = "0" * 64
    doc = CH.assemble(tmp_path, args, census(), ev["dxvk"], ev["sdl"], good, "t")
    assert all(c["pass"] for c in doc["checks"]) and (doc["verdict"], doc["verdict_with_controls"]) == ("FAIL", "FAIL")
    assert doc["inputs"]["provenance"]["failures"] == [
        f"s: the player that ran ({'0' * 64}) is not the build's sa2_player_gpu ({GPU_SHA})",
        f"s: the device ran from {'0' * 64}, not the build's sa2_player_gpu ({GPU_SHA})"]


# ------------------------------------------------------------------ the build tied to the tree
# What the two build scripts read (host/player/build.sh, host/d3d9gpu/build.sh), as this
# file knows it: a forged build's group records name every pinned file under these.
TREE_SOURCES = ("host/d3d9gpu/", "host/player/", "host/sa2_stream.h", "host/dxvk_smoke.cpp", "tools/pins.json")
# A forged player's exact sources: its translation units, a few headers its compiles read
# (each named by its forged depfile), its build script and its pins (CH.REQUIRED_SOURCES among them).
TUS = tuple(f"host/player/{tu}.cpp" for tu in CH.PLAYER_TUS)
DXVK_EXACT = (*TUS, "host/player/sha256.h", "host/sa2_stream.h", "host/player/build.sh", "tools/pins.json")
GPU_EXACT = (*TUS, "host/d3d9gpu/d9g_entry.cpp", "host/d3d9gpu/d9g.h", "host/sa2_stream.h", "host/d3d9gpu/build.sh",
             "tools/pins.json")
# What a forged player's compiles read (its depfile): its exact sources but the build script and pins.
COMPILED = lambda names: [n for n in names if not n.endswith(("build.sh", "pins.json"))]  # noqa: E731


@functools.cache
def compiler() -> dict:
    """A record's compiler as build_record.py writes it, this machine's g++: its command,
    --version line and include search list."""
    if not GXX:
        pytest.fail("no g++")
    return CH.build_record.compiler_line(GXX)


def write_contract(build: Path, where: str, contract: dict) -> str:
    (build / where).mkdir(parents=True, exist_ok=True)
    (build / where / "expectations.json").write_text(json.dumps({"contract": contract}))
    return hashlib.sha256((build / where / "expectations.json").read_bytes()).hexdigest()


def forged_build(build: Path, pins: dict | None = None) -> Path:
    """A build whose records match the tree as it is: its binaries are BYTES (which the
    forged reports name); each player's exact record (sa2-build-record/v2) naming it alone,
    a few of the tree's files and its contract; and the two group records, whose sources are
    every pinned file under TREE_SOURCES with the hash it has now."""
    pins = CH.pipeline() if pins is None else pins
    build.mkdir(parents=True, exist_ok=True)
    for name, data in BYTES.items():
        (build / name).write_bytes(data)
    sources = {k: v for k, v in pins.items() if k.startswith(TREE_SOURCES)}
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731

    def depfile(binary: str, names) -> dict:
        d = build / "obj" / binary / "forged.d"
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(f"{d.with_suffix('.o')}: " + " \\\n ".join(str(ROOT / n) for n in COMPILED(names)) + "\n")
        return {f"obj/{binary}/forged.d": sha(d)}
    exact = lambda binary, names, where, contract: {  # noqa: E731
        "schema": "sa2-build-record/v2", "tree": str(ROOT), "binaries": {binary: sha(build / binary)},
        "sources": {k: pins[k] for k in names}, "generated": {f"{where}/expectations.json": write_contract(
            build, where, contract)}, "external": {}, "depfiles": depfile(binary, names),
        "compiler": copy.deepcopy(compiler())}
    records = {
        "sa2_player.json": exact("sa2_player", DXVK_EXACT, "expected", DXVK_CONTRACT),
        "sa2_player_gpu.json": exact("sa2_player_gpu", GPU_EXACT, "expected-gpu", GPU_CONTRACT),
        "player_tools.json": {"schema": "sa2-build-record/v1", "binaries": {b: sha(build / b) for b in (
            "test_texcodec", "dxvk_smoke")}, "sources": {k: v for k, v in sources.items()
                                                       if not k.startswith("host/d3d9gpu/")}},
        "d3d9gpu_tools.json": {"schema": "sa2-build-record/v1", "binaries": {b: sha(build / b) for b in (
            "sa2_frame_census", "d3d9gpu_clear_matrix", "test_d3d9gpu", "d3d9gpu_state_check")},
            "sources": {k: v for k, v in sources.items() if k.startswith("host/d3d9gpu/")}}}
    for name, rec in records.items():
        (build / name).write_text(json.dumps(rec))
    return build


def edit_record(build: Path, name: str, change) -> None:
    rec = json.loads((build / name).read_text())
    change(rec)
    (build / name).write_text(json.dumps(rec))


def test_a_build_whose_records_match_the_tree_passes(tmp_path):
    ev = evidence()
    prov = CH.provenance(forged_build(tmp_path), CH.pipeline(), [ev["dxvk"]], [ev["sdl"]])
    assert (prov["pass"], prov["failures"], prov["runs_held"]) == (True, [], 2)
    assert sorted(prov["records"]) == ["d3d9gpu_tools.json", "player_tools.json", "sa2_player.json",
                                       "sa2_player_gpu.json"]


def other_source(rec):
    rec["sources"]["host/d3d9gpu/d9g_format.cpp"] = "5" * 64


def other_binary(build):
    (build / "sa2_player_gpu").write_bytes(b"rebuilt after the record")


def pop_everywhere(path: str):
    def edit(build: Path):
        for name in CH.BUILD_RECORDS:
            edit_record(build, name, lambda r: r["sources"].pop(path, None))
    return edit


def rewrite_contract(where: str, record: str, contract: dict):
    """The build's contract rewritten, its record's hash of it rewritten to match."""
    def edit(build: Path):
        h = write_contract(build, where, contract)
        edit_record(build, record, lambda r: r["generated"].update({f"{where}/expectations.json": h}))
    return edit


@pytest.mark.parametrize("change,needle", [
    # the review's finding 1: a binary built from an edited d9g_format.cpp, the tree restored
    (lambda b: edit_record(b, "d3d9gpu_tools.json", other_source),
     "d3d9gpu_tools.json: source host/d3d9gpu/d9g_format.cpp is "),
    (lambda b: edit_record(b, "sa2_player_gpu.json", lambda r: r["sources"].update(
        {"host/d3d9gpu/d9g_entry.cpp": "5" * 64})), "sa2_player_gpu.json: source host/d3d9gpu/d9g_entry.cpp is "),
    (lambda b: (b / "sa2_player.json").unlink(), "sa2_player.json: no build record in"),
    (lambda b: (b / "sa2_player_gpu.json").unlink(),
     "sa2_player_gpu.json: no build record in"),
    (lambda b: (b / "d3d9gpu_tools.json").unlink(), "d3d9gpu_tools.json: no build record in"),
    (other_binary, "sa2_player_gpu.json: sa2_player_gpu is "),
    (lambda b: edit_record(b, "player_tools.json", lambda r: r["sources"].pop("host/player/test_synth.sh")),
     "host/player/test_synth.sh: pinned, and no build record names it"),
    (lambda b: write_contract(b, "expected", {**DXVK_CONTRACT, "sdl_version": 1}),
     "sa2_player.json: generated expected/expectations.json is "),
    (lambda b: write_contract(b, "expected-gpu", {**GPU_CONTRACT, "sdl_version": 1}),
     "sa2_player_gpu.json: generated expected-gpu/expectations.json is "),
    # a source the receipt would not pin (the review's finding 6: tools/pins.json was one)
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["sources"].update({"tools/s5/other.py": "6" * 64})),
     "sa2_player.json: source tools/s5/other.py is not pinned by the pipeline"),
    (pop_everywhere("tools/pins.json"), "tools/pins.json: pinned, and no build record names it"),
    (pop_everywhere("host/dxvk_smoke.cpp"), "host/dxvk_smoke.cpp: pinned, and no build record names it"),
    (pop_everywhere("host/sa2_stream.h"), "host/sa2_stream.h: pinned, and no build record names it"),
    (pop_everywhere("host/d3d9gpu/d9g_clear.cpp"), "host/d3d9gpu/d9g_clear.cpp: pinned, and no build record names it"),
    # an exact record's other inputs: an external header, a depfile, as they were
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["external"].update({str(b / "x.h"): "7" * 64})),
     "sa2_player.json: external "),
    (lambda b: edit_record(b, "sa2_player_gpu.json", lambda r: r["depfiles"].update({"obj/x.d": "7" * 64})),
     "sa2_player_gpu.json: depfile obj/x.d is None, " + "7" * 64),
    # one record per binary: the GPU player in a group record too
    (lambda b: edit_record(b, "d3d9gpu_tools.json", lambda r: r["binaries"].update(sa2_player_gpu=GPU_SHA)),
     "sa2_player_gpu: named by 2 records (sa2_player_gpu.json, d3d9gpu_tools.json), not one"),
    # each player's record is its exact record, of it alone
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r.update(schema="sa2-build-record/v1")),
     "sa2_player.json: sa2-build-record/v1 of ['sa2_player'], not sa2_player's exact record"),
    (lambda b: edit_record(b, "sa2_player_gpu.json", lambda r: r["binaries"].update(test_texcodec=hashlib.sha256(
        BYTES["test_texcodec"]).hexdigest())), "sa2_player_gpu.json: sa2-build-record/v2 of ['sa2_player_gpu', "),
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["generated"].clear()),
     "sa2_player.json: its generated inputs do not name expected/expectations.json"),
    # the DXVK player built from a second-host source, or expecting the second host's library
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["sources"].update(
        {"host/d3d9gpu/d9g_entry.cpp": CH.pipeline()["host/d3d9gpu/d9g_entry.cpp"]})),
     "sa2_player.json: the DXVK player was built from the second host's ['host/d3d9gpu/d9g_entry.cpp']"),
    (rewrite_contract("expected", "sa2_player.json", {**DXVK_CONTRACT, "d3d9gpu_sha256": GPU_SHA}),
     "sa2_player's contract (expected/expectations.json) is ['sdl_sha256', 'dxvk_sha256', 'dxvk_config_sha256', "
     "'sdl_version', 'sdl_revision', 'd3d9gpu_sha256'], not"),
    # ... and the GPU player expecting DXVK's
    (rewrite_contract("expected-gpu", "sa2_player_gpu.json", DXVK_CONTRACT),
     "sa2_player_gpu's contract (expected-gpu/expectations.json) is ['sdl_sha256', 'dxvk_sha256'"),
    (lambda b: (b / "expected-gpu" / "expectations.json").unlink(), "sa2_player_gpu's contract (expected-gpu/"
     "expectations.json) is None, not"),
    # an exact record that left out what made its player (the review's finding 2: a checkout
    # under OUT recorded player.cpp as generated): its translation units, the stream header,
    # its build script, d9g's entry point ...
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["sources"].pop("host/player/player.cpp")),
     "sa2_player.json: names none of ['host/player/player.cpp'] among its sources (REQUIRED_SOURCES)"),
    (lambda b: edit_record(b, "sa2_player.json", lambda r: [r["sources"].pop(k) for k in (
        "host/player/build.sh", "host/sa2_stream.h")]),
     "sa2_player.json: names none of ['host/sa2_stream.h', 'host/player/build.sh'] among its sources"),
    (lambda b: edit_record(b, "sa2_player_gpu.json", lambda r: r["sources"].pop("host/d3d9gpu/d9g_entry.cpp")),
     "sa2_player_gpu.json: names none of ['host/d3d9gpu/d9g_entry.cpp'] among its sources"),
    (lambda b: edit_record(b, "sa2_player_gpu.json", lambda r: r["sources"].pop("host/player/mixer.cpp")),
     "sa2_player_gpu.json: names none of ['host/player/mixer.cpp'] among its sources"),
    # ... or any file its depfiles name, read again (the review's NIT 3)
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["sources"].pop("host/player/sha256.h")),
     f"sa2_player.json: depfile obj/sa2_player/forged.d names {ROOT / 'host/player/sha256.h'}, which the record "
     f"does not hold (sources host/player/sha256.h)"),
    (lambda b: edit_record(b, "sa2_player_gpu.json", lambda r: r["sources"].pop("host/d3d9gpu/d9g.h")),
     f"sa2_player_gpu.json: depfile obj/sa2_player_gpu/forged.d names {ROOT / 'host/d3d9gpu/d9g.h'}, which"),
    # ... as the kind its place makes it: a tree file the record calls generated is not a source
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["generated"].update(
        {"host/player/sha256.h": r["sources"].pop("host/player/sha256.h")})),
     f"sa2_player.json: depfile obj/sa2_player/forged.d names {ROOT / 'host/player/sha256.h'}, which the record "
     f"does not hold (sources host/player/sha256.h)"),
    (lambda b: (b / "obj" / "sa2_player" / "forged.d").write_text(f"x.o: {b / 'gen.h'}\n")
     or edit_record(b, "sa2_player.json", lambda r: r["depfiles"].update({"obj/sa2_player/forged.d": CH.sha256(
         b / "obj" / "sa2_player" / "forged.d")})),
     "sa2_player.json: depfile obj/sa2_player/forged.d names "),
    (lambda b: (b / "obj" / "sa2_player" / "forged.d").write_text("x.o: host/player/player.cpp\n")
     or edit_record(b, "sa2_player.json", lambda r: r["depfiles"].update({"obj/sa2_player/forged.d": CH.sha256(
         b / "obj" / "sa2_player" / "forged.d")})),
     "sa2_player.json: depfile obj/sa2_player/forged.d names host/player/player.cpp, a relative path"),
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["depfiles"].clear()),
     "sa2_player.json: names no depfile"),
    (lambda b: edit_record(b, "sa2_player_gpu.json", lambda r: r.pop("tree")),
     "sa2_player_gpu.json: names no checkout (tree)"),
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r.update(tree=str(b / "tree"))),
     "sa2_player.json: its checkout "),
])
def test_a_build_not_made_from_the_tree_is_named(tmp_path, change, needle):
    build = forged_build(tmp_path)
    change(build)
    prov = CH.provenance(build, CH.pipeline())
    assert prov["pass"] is False
    assert any(f.startswith(needle) for f in prov["failures"]), prov["failures"]


@pytest.mark.parametrize("dxvk_change,gpu_change,needle", [
    # each run held to the binary of the role this tool gave it, not to its own d3d9_host
    (lambda r: put(r, "report.files.player.sha256", GPU_SHA), None,
     f"d: the player that ran ({GPU_SHA}) is not the build's sa2_player ({PLAYER_SHA})"),
    (None, lambda r: put(r, "report.files.player.sha256", PLAYER_SHA),
     f"s: the player that ran ({PLAYER_SHA}) is not the build's sa2_player_gpu ({GPU_SHA})"),
    (None, lambda r: put(r, "report.host_identity.d3d9_host", "dxvk"), None),
    # the device's own account of the file it ran from
    (None, lambda r: put(r, "device.library.sha256", "2" * 64),
     f"s: the device ran from {'2' * 64}, not the build's sa2_player_gpu ({GPU_SHA})"),
    (None, lambda r: r.update(device=None), f"s: the device ran from None, not the build's sa2_player_gpu"),
])
def test_each_run_is_held_to_the_player_of_its_role(tmp_path, dxvk_change, gpu_change, needle):
    ev = evidence()
    for change, run in ((dxvk_change, ev["dxvk"]), (gpu_change, ev["sdl"])):
        if change:
            change(run)
    prov = CH.provenance(forged_build(tmp_path), CH.pipeline(), [ev["dxvk"]], [ev["sdl"]])
    assert len(prov["failures"]) == (needle is not None) and all(f.startswith(needle) for f in prov["failures"]), \
        prov["failures"]


def recorded_commands(monkeypatch) -> list[tuple[list[str], dict]]:
    """subprocess.run as compare_hosts.py calls it, each call recorded and answered exit 0."""
    calls: list[tuple[list[str], dict]] = []

    def run(cmd, **kw):
        calls.append(([str(c) for c in cmd], dict(kw.get("env") or {})))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(CH.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("sdlgpu", [False, True])
def test_each_host_runs_its_own_binary(monkeypatch, tmp_path, sdlgpu):
    """The role is the tool's: the DXVK run is run_player.sh (the build's sa2_player), the
    second host's run the build's sa2_player_gpu itself under player_env.sh, with its device
    report and fault and no library to name (no --d3d9 switch, no SA2_D3D9GPU)."""
    calls = recorded_commands(monkeypatch)
    monkeypatch.setenv("SA2_D3D9GPU", "/stale/libsa2_d3d9gpu.so")
    build = tmp_path / "bin"
    CH.run_player(build, tmp_path / "s.sa2s", 3, tmp_path / "run", sdlgpu=sdlgpu, faults="count-draw" if sdlgpu else "")
    cmd, env = calls[0]
    if sdlgpu:
        assert cmd[:5] == ["bash", "-c", 'source "$0" && exec "$@"', str(CH.PLAYER_ENV), str(build / "sa2_player_gpu")]
        assert (env["SA2_D3D9GPU_REPORT"], env["SA2_D3D9GPU_FAULT"]) == (str(tmp_path / "run" / "d3d9gpu.json"),
                                                                        "count-draw")
    else:
        assert cmd[:2] == ["bash", str(CH.RUN_PLAYER)] and "SA2_D3D9GPU_REPORT" not in env
    assert "--d3d9" not in cmd and "SA2_D3D9GPU" not in env and cmd[cmd.index("--stage") + 1] == "3"
    assert env["OUT"] == str(build)
    if not sdlgpu:
        # dxvk-ab's before: run under its own checkout's run_player.sh (and so its player_env.sh)
        CH.run_player(build, tmp_path / "s.sa2s", 3, tmp_path / "before", sdlgpu=False, checkout=tmp_path / "clone")
        assert calls[-2][0][:2] == ["bash", str(tmp_path / "clone" / "host" / "player" / "run_player.sh")]


def test_the_matrix_s_sdl_gpu_leg_is_the_second_host_s_player(monkeypatch, tmp_path):
    calls = recorded_commands(monkeypatch)
    build = tmp_path / "bin"
    CH.run_matrix(build, tmp_path, hidden=True)
    sdl = next(c for c in calls if str(build / "sa2_player_gpu") in c[0])
    assert sdl[0][3:] == [str(CH.PLAYER_ENV), str(build / "sa2_player_gpu"), "--clear-matrix", "--hidden"]
    assert sdl[1]["SA2_D3D9GPU_REPORT"] == str(tmp_path / "matrix" / "d3d9gpu.json")
    dxvk = next(c for c in calls if str(build / "d3d9gpu_clear_matrix") in c[0])
    assert "$SA2_DXVK_D3D9" in dxvk[0][2] and "SA2_D3D9GPU_REPORT" not in dxvk[1]
    assert [c[0] for c in calls if str(build / "test_d3d9gpu") in c[0]] == [[str(build / "test_d3d9gpu"),
                                                                             "--clear-matrix"]]
    assert len(calls) == 3


def test_run_without_a_build_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        CH.main(["run", "--scope", "r2a-clears", "--stream", str(tmp_path / "none.sa2s"), "--out", str(tmp_path / "out")])
    assert exc.value.code == 2
    assert "the following arguments are required: --build" in capsys.readouterr().err
    assert not (tmp_path / "out").exists() and not hasattr(CH, "DEFAULT_BUILD")


def test_run_refuses_a_directory_an_earlier_run_left_files_in(tmp_path, capsys):
    out = tmp_path / "out"
    (out / "control-clear-colour").mkdir(parents=True)
    assert CH.main(["run", "--scope", "r2a-clears", "--stream", str(tmp_path / "s.sa2s"), "--build", str(forged_build(tmp_path / "bin")),
                    "--out", str(out)]) == 2
    assert "exists and is not empty" in capsys.readouterr().err and not (out / "census.json").exists()


def test_run_and_check_refuse_a_compare_hosts_py_edited_since_it_started(monkeypatch, tmp_path, capsys):
    """The pins would name code that did not run (the review's finding 2)."""
    run, build = forged_run_dir(monkeypatch, tmp_path)
    before = (run / "compare.json").read_text()
    monkeypatch.setattr(CH, "RAN_BY", "1" * 64)
    out = tmp_path / "out"
    assert CH.main(["run", "--scope", "r2a-clears", "--stream", str(tmp_path / "s.sa2s"), "--build", str(build), "--out", str(out)]) == 2
    assert "edited since it started" in capsys.readouterr().err and not out.exists()
    assert CH.main(["check", "--run", str(run)]) == 2
    assert "edited since it started" in capsys.readouterr().err and (run / "compare.json").read_text() == before


def test_a_run_whose_check_json_another_play_report_wrote_is_named(tmp_path):
    ev = evidence()
    ev["sdl"]["checked_by"] = "7" * 64
    prov = CH.provenance(forged_build(tmp_path), CH.pipeline(), [ev["dxvk"]], [ev["sdl"]])
    assert prov["failures"] == [f"s: its check.json was written by play_report.py {'7' * 64}, not the pinned {CHECKER}"]


def test_run_refuses_a_build_without_records_before_running_anything(tmp_path, capsys):
    bare = tmp_path / "bin"
    bare.mkdir()
    out = tmp_path / "out"
    assert CH.main(["run", "--scope", "r2a-clears", "--stream", str(tmp_path / "none.sa2s"), "--build", str(bare), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "is not the tree this comparison pins" in err and "sa2_player.json: no build record" in err
    assert not out.exists()
    assert CH.main(["dxvk-ab", "--before", str(bare), "--before-commit", "HEAD", "--after", str(bare),
                    "--stream", str(tmp_path / "none.sa2s"), "--out", str(tmp_path / "ab")]) == 2
    assert not (tmp_path / "ab").exists()


# `run`, `check` and `dxvk-ab` end to end on forged runs: the census, the matrix and the
# players replaced, everything else as they run.
def fake_player(changes: dict):
    """run_player's stand-in: evidence()'s run for the host, the device under the control's
    fault, and changes[run_dir.name](run) applied."""
    def run_player(build, stream, stage, run_dir, *, sdlgpu, faults="", hidden=False, allow_notes=False, max_frames=0):
        run = copy.deepcopy(evidence()["sdl" if sdlgpu else "dxvk"])
        run["dir"] = CH.rel(run_dir)
        if sdlgpu:
            run["device"]["fault"] = faults or None
        changes.get(run_dir.name, lambda r: None)(run)
        return run
    return run_player


def forged_run_command(monkeypatch, tmp_path: Path, changes: dict) -> tuple[Path, Path]:
    build = forged_build(tmp_path / "bin")
    stream = tmp_path / "s.sa2s"
    stream.write_bytes(b"SA2D3D9S")

    def run_census(build, stream, stage, out_dir):
        (out_dir / "census.json").write_text(json.dumps(census()))
        return census()
    monkeypatch.setattr(CH, "run_census", run_census)
    monkeypatch.setattr(CH, "run_matrix", lambda build, out_dir, hidden=False: CH.load_matrix(write_matrix(out_dir, build)))
    monkeypatch.setattr(CH, "run_player", fake_player(changes))
    return build, stream


def other_player(run):
    run["report"]["files"]["player"]["sha256"] = "0" * 64


def test_run_holds_every_control_run_to_the_build(monkeypatch, tmp_path):
    build, stream = forged_run_command(monkeypatch, tmp_path, {"control-count-draw": other_player})
    out = tmp_path / "out"
    assert CH.main(["run", "--scope", "r2a-clears", "--stream", str(stream), "--build", str(build), "--out", str(out), "--controls"]) == 1
    doc = json.loads((out / "compare.json").read_text())
    assert all(c["pass"] for c in doc["checks"]) and [c["check"] for c in doc["checks"]] == list("abcdefgi")
    prov = doc["inputs"]["provenance"]
    assert (prov["runs_held"], doc["verdict"]) == (2 + len(CH.CONTROLS), "FAIL")
    assert prov["failures"] == [f"{CH.rel(out / 'control-count-draw')}: the player that ran ({'0' * 64}) is not "
                                f"the build's sa2_player_gpu ({GPU_SHA})"]


def forged_run_dir(monkeypatch, tmp_path: Path, changes: dict | None = None, controls: bool = False,
                   recorded_controls: bool | None = None, reference: Path | None = None) -> tuple[Path, Path]:
    """A run directory as `run` leaves it, and its build: with `controls`, every control's
    run moved by its fault alone (as required); compare.json says the controls ran when
    `recorded_controls` (default: `controls`)."""
    build, stream = forged_run_command(monkeypatch, tmp_path, {})
    run = tmp_path / "run"
    run.mkdir()
    dirs = [("dxvk", evidence()["dxvk"]), ("sdlgpu", evidence()["sdl"])]
    if controls:
        moves = {"clear-colour": both_comparable_changed, "count-draw": one_left_uncounted,
                 "scene-answer": begin_scene_answered, "present-scribble+clear-noop": both_comparable_changed}
        for c in CH.CONTROLS:
            ev = under(c["faults"], moves.get(c["name"]))
            ev["sdl"]["dir"] = CH.rel(run / f"control-{c['name']}")
            dirs.append((f"control-{c['name']}", ev["sdl"]))
    for name, ev in dirs:
        (changes or {}).get(name, lambda r: None)(ev)
        d = run / name
        (d / "dxvk-log").mkdir(parents=True)
        (d / "exit").write_text(f"{ev['exit']}\n")
        (d / "report.json").write_text(json.dumps(ev["report"]))
        (d / "check.json").write_text(json.dumps(ev["check"]))
        (d / CH.CHECKED_BY).write_text(f"{ev['checked_by']}\n")
        cols = list(ev["times"][0])
        (d / "frames.y4m.times.tsv").write_text("\t".join(cols) + "\n" + "".join(
            "\t".join(r[c] for c in cols) + "\n" for r in ev["times"]))
        if ev["device"] is not None:
            (d / "d3d9gpu.json").write_text(json.dumps(ev["device"]))
    (run / "dxvk" / "dxvk-log" / "sa2_player_d3d9.log").write_text(DXVK_LOG)
    (run / "census.json").write_text(json.dumps(census()))
    write_matrix(run, build)
    args = argparse.Namespace(scope="r2a-clears", build=str(build), stream=str(stream), hidden=False,
                              reference=str(reference) if reference else None)
    dxvk, sdl = CH.load_run(run / "dxvk"), CH.load_run(run / "sdlgpu")
    outcomes, control_runs = None, []
    if controls:
        control_runs = [CH.load_run(run / f"control-{c['name']}") for c in CH.CONTROLS]
        outcomes = [CH.control_outcome(c, {"census": census(), "dxvk": dxvk, "sdl": r, "hidden": False})
                    for c, r in zip(CH.CONTROLS, control_runs)]
    if not (controls if recorded_controls is None else recorded_controls):
        outcomes = None
    doc = CH.assemble(run, args, census(), dxvk, sdl, outcomes, "t", control_runs=control_runs,
                      matrix=CH.load_matrix(run))
    (run / "compare.json").write_text(json.dumps(doc))
    return run, build


def test_check_recomputes_a_run_on_the_build_it_recorded(monkeypatch, tmp_path, capsys):
    run, build = forged_run_dir(monkeypatch, tmp_path)
    assert CH.main(["check", "--run", str(run)]) == 0
    doc = json.loads((run / "compare.json").read_text())
    assert (doc["verdict"], [c["check"] for c in doc["checks"]]) == ("PASS", list("abcdefgi"))
    # the build rebuilt since the run (its records still the tree's): refused, nothing rewritten
    doc["inputs"]["files"]["sa2_player"] = "0" * 64
    (run / "compare.json").write_text(json.dumps(doc))
    before = (run / "compare.json").read_text()
    assert CH.main(["check", "--run", str(run)]) == 2
    assert "is not the one the run recorded" in capsys.readouterr().err
    assert (run / "compare.json").read_text() == before
    doc["inputs"]["files"] = {}
    (run / "compare.json").write_text(json.dumps(doc))
    assert CH.main(["check", "--run", str(run)]) == 2
    doc["inputs"].pop("build")
    (run / "compare.json").write_text(json.dumps(doc))
    assert CH.main(["check", "--run", str(run)]) == 2
    assert "no compare.json naming its build" in capsys.readouterr().err


def test_check_refuses_a_build_no_longer_the_tree_s(monkeypatch, tmp_path, capsys):
    run, build = forged_run_dir(monkeypatch, tmp_path)
    edit_record(build, "d3d9gpu_tools.json", other_source)
    before = (run / "compare.json").read_text()
    assert CH.main(["check", "--run", str(run)]) == 2
    assert "is not the tree this comparison pins" in capsys.readouterr().err
    assert (run / "compare.json").read_text() == before


def test_check_refuses_a_verdict_another_play_report_wrote(monkeypatch, tmp_path, capsys):
    run, build = forged_run_dir(monkeypatch, tmp_path, {"sdlgpu": lambda ev: ev.update(checked_by="7" * 64)})
    before = (run / "compare.json").read_text()
    assert CH.main(["check", "--run", str(run)]) == 2
    err = capsys.readouterr().err
    assert "written by another play_report.py than the one this check pins" in err
    assert f"{CH.rel(run / 'sdlgpu')}: its check.json was written by play_report.py {'7' * 64}" in err
    assert (run / "compare.json").read_text() == before
    (run / "sdlgpu" / CH.CHECKED_BY).unlink()          # a run from before the checker was recorded
    assert CH.main(["check", "--run", str(run)]) == 2


def test_check_holds_the_controls_the_run_recorded(monkeypatch, tmp_path):
    run, build = forged_run_dir(monkeypatch, tmp_path, controls=True)
    assert CH.main(["check", "--run", str(run)]) == 0
    doc = json.loads((run / "compare.json").read_text())
    assert doc["verdict_with_controls"] == "PASS"
    assert [c["outcome"] for c in doc["controls"]["results"]] == ["as required"] * len(CH.CONTROLS)
    assert doc["inputs"]["provenance"]["runs_held"] == 2 + len(CH.CONTROLS)


def test_check_keys_the_controls_on_the_run_s_record_not_on_its_directories(monkeypatch, tmp_path):
    """Control directories a run did not record running (the review's finding 1) are not
    its controls."""
    run, build = forged_run_dir(monkeypatch, tmp_path, controls=True, recorded_controls=False)
    assert CH.main(["check", "--run", str(run)]) == 0
    doc = json.loads((run / "compare.json").read_text())
    assert (doc["controls"], doc["verdict_with_controls"]) == ({"run": False}, "NOT_RUN")


@pytest.mark.parametrize("change,needle", [
    (lambda run: (run / "sdlgpu" / "d3d9gpu.json").write_text("{}"), "sdlgpu/d3d9gpu.json"),
    (lambda run: (run / "census.json").write_text(json.dumps(census(frames=5))), "census.json"),
    (lambda run: (run / "control-count-draw" / "frames.y4m.times.tsv").unlink(),
     "control-count-draw/frames.y4m.times.tsv"),
    (lambda run: (run / "matrix" / "cpu.tsv").write_text(""), "matrix/cpu.tsv"),
    (lambda run: (run / "control-clear-colour" / "d3d9gpu.json").unlink(), "control-clear-colour/d3d9gpu.json"),
    # what check a reads of each run: its exit status and play_report.py's verdict
    (lambda run: (run / "sdlgpu" / "exit").write_text("4\n"), "sdlgpu/exit"),
    (lambda run: (run / "dxvk" / "check.json").write_text(json.dumps({"pass": False, "failures": ["x"]})),
     "dxvk/check.json"),
    # the DXVK logs checks g and i read the GPU and driver from: edited, or another added
    # (with no name in the report the first in name order is read)
    (lambda run: (run / "dxvk" / "dxvk-log" / "sa2_player_d3d9.log").write_text(DXVK_LOG.replace("RTX 5090", "RTX 4090")),
     "dxvk/dxvk-log/sa2_player_d3d9.log"),
    (lambda run: (run / "dxvk" / "dxvk-log" / "a_d3d9.log").write_text(DXVK_LOG), "dxvk/dxvk-log/a_d3d9.log"),
    (lambda run: (run / "matrix" / "dxvk-log" / "d3d9gpu_clear_matrix_d3d9.log").write_text(DXVK_LOG + "info:  x\n"),
     "matrix/dxvk-log/d3d9gpu_clear_matrix_d3d9.log"),
])
def test_check_refuses_files_the_run_did_not_write(monkeypatch, tmp_path, capsys, change, needle):
    """A file in the run directory rewritten, added or removed since the run (the review's
    finding 8): refused, nothing rewritten."""
    run, build = forged_run_dir(monkeypatch, tmp_path, controls=True)
    change(run)
    before = (run / "compare.json").read_text()
    assert CH.main(["check", "--run", str(run)]) == 2
    err = capsys.readouterr().err
    assert "are not the ones the run recorded" in err and needle in err, err
    assert (run / "compare.json").read_text() == before


def test_check_holds_the_reference_the_run_held(monkeypatch, tmp_path):
    ref = tmp_path / "ref.tsv"
    rows = evidence()["dxvk"]["times"]
    ref.write_text("\t".join(rows[0]) + "\n" + "".join("\t".join(r.values()) + "\n" for r in rows))
    run, build = forged_run_dir(monkeypatch, tmp_path, reference=ref)
    assert "h" in [c["check"] for c in json.loads((run / "compare.json").read_text())["checks"]]
    assert CH.main(["check", "--run", str(run)]) == 0
    h = next(c for c in json.loads((run / "compare.json").read_text())["checks"] if c["check"] == "h")
    assert (h["pass"], h["detail"]["reference"]) == (True, CH.rel(ref))


def test_check_carries_the_compare_hosts_py_that_ran_forward(monkeypatch, tmp_path, capsys):
    """`check` under another compare_hosts.py pins it, keeps the one that ran the players
    (inputs.ran_by), and the receipt refuses the two differing (the review's finding 2)."""
    run, build = forged_run_dir(monkeypatch, tmp_path, controls=True)
    doc = json.loads((run / "compare.json").read_text())
    doc["inputs"]["ran_by"] = "0" * 64
    (run / "compare.json").write_text(json.dumps(doc))
    assert CH.main(["check", "--run", str(run)]) == 0
    assert "no receipt takes this run" in capsys.readouterr().out
    doc = json.loads((run / "compare.json").read_text())
    assert (doc["inputs"]["ran_by"], doc["inputs"]["pipeline"][CH.SELF]) == ("0" * 64, CH.RAN_BY)
    out = tmp_path / "r.json"
    assert receipt("--run", str(run), "--out", str(out)) == 2
    assert f"its runs were made by {CH.SELF} {'0' * 64}, and it pins {CH.RAN_BY}" in capsys.readouterr().err
    assert not out.exists()
    doc["inputs"].pop("ran_by")         # a compare.json from before ran_by was recorded
    (run / "compare.json").write_text(json.dumps(doc))
    assert CH.main(["check", "--run", str(run)]) == 2
    assert "names no compare_hosts.py that ran it" in capsys.readouterr().err


def ab_player(sha: dict, log: dict | None = None, report: dict | None = None, conf: dict | None = None,
              lib: dict | None = None):
    """run_player's stand-in for dxvk-ab: DXVK's run under `checkout`'s run-time files, the
    player that ran named by sha[name] (the build's own sa2_player when absent), the
    dxvk.conf it read conf[name] (its checkout's when absent) in its report and in DXVK's
    log, the DXVK library it loaded lib[name] (its checkout's install's when absent, as
    player_env.sh names it, and as that resolves) in its report, DXVK_LOG plus log[name], its
    report report[name]() (player_report("dxvk") when absent). The runs' checkouts are
    recorded in its `checkouts`."""
    def run_player(build, stream, stage, run_dir, *, sdlgpu, faults="", hidden=False, allow_notes=False,
                   checkout=None):
        assert not sdlgpu and checkout is not None
        run_player.checkouts[run_dir.name] = Path(checkout)
        rep = (report or {}).get(run_dir.name, lambda: player_report("dxvk"))()
        rep["files"]["player"]["sha256"] = sha.get(run_dir.name, CH.sha256(Path(build) / "sa2_player"))
        cfg = (conf or {}).get(run_dir.name, str(Path(checkout) / CH.DXVK_CONF))
        rep["files"]["dxvk_conf"].update(path=cfg, real_path=cfg)
        library = (lib or {}).get(run_dir.name, str(Path(checkout) / CH.DXVK_LIBRARY))
        rep["files"]["libdxvk_d3d9"].update(path=library, real_path=os.path.realpath(library))
        rep["env"] = {"DXVK_CONFIG_FILE": cfg, "DXVK_WSI_DRIVER": "SDL3", "SA2_DXVK_D3D9": library}
        (run_dir / "dxvk-log").mkdir(parents=True, exist_ok=True)
        path = run_dir / "dxvk-log" / "sa2_player_d3d9.log"
        if (log or {}).get(run_dir.name, "") is not None:      # None: the run left no log
            path.write_text(f"info:  Found config file: {cfg}\n" + DXVK_LOG
                            + (log or {}).get(run_dir.name, "info:    Budget: 30.40 GiB\n"))
        rep["dxvk"] = {"log": str(path), "config_file": cfg}
        return {"dir": CH.rel(run_dir), "report": rep, "times": times(["0x1", "0x2"]), "exit": 0,
                "check": {"pass": True}, "checked_by": CHECKER, "device": None, "log": {}}
    run_player.checkouts = {}
    return run_player


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def before_commit(monkeypatch, tmp_path: Path, tree: bool = False) -> tuple[str, Path, Path]:
    """A repository (GIT_REPO) with one commit of BEFORE_SOURCES (with `tree`, this tree's
    own files), its clone's player built into `before` with the record
    host/d3d9gpu/build_before.sh writes (its pins the clone's, its checkout the clone, whose
    run_player.sh, player_env.sh and dxvk.conf it runs under): (commit, before, clone)."""
    repo = tmp_path / "repo"
    (repo / "host" / "player").mkdir(parents=True)
    (repo / "tools").mkdir()
    if tree:
        shutil.copytree(ROOT / "host" / "player", repo / "host" / "player", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        for f in ("sa2_stream.h", "dxvk_smoke.cpp"):
            shutil.copyfile(ROOT / "host" / f, repo / "host" / f)
        shutil.copyfile(ROOT / "tools" / "pins.json", repo / "tools" / "pins.json")
    else:
        (repo / "host" / "player" / "player.cpp").write_text("int main() {}\n")
        (repo / "host" / "player" / "build.sh").write_text("# the unchanged player's build\n")
        for f in ("run_player.sh", "player_env.sh", "dxvk.conf"):
            (repo / "host" / "player" / f).write_text(f"# the unchanged player's {f}\n")
        (repo / "host" / "sa2_stream.h").write_text("// stream\n")
        (repo / "host" / "dxvk_smoke.cpp").write_text("// smoke\n")
        (repo / "tools" / "pins.json").write_text("{}\n")
    (repo / "host" / "other.cpp").write_text("// not a player source\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "before")
    monkeypatch.setattr(CH, "GIT_REPO", repo)
    before = tmp_path / "before"
    (before / "expected").mkdir(parents=True)
    (before / "sa2_player").write_bytes(b"the unchanged player")
    (before / "expected" / "expectations.h").write_text("// generated\n")
    record_before(repo, before)
    return git(repo, "rev-parse", "HEAD"), before, repo


def record_before(repo: Path, before: Path) -> None:
    """The before build's record, of the clone as it is (build_before.sh's closing call)."""
    sys.path.insert(0, str(ROOT / "host" / "player"))
    import build_record as BR
    srcs = [repo / "host" / "player", repo / "host" / "sa2_stream.h", repo / "host" / "dxvk_smoke.cpp",
            repo / "tools" / "pins.json"]
    (before / "sa2_player.json").write_text(json.dumps(BR.record(repo, before, ["sa2_player"], srcs,
                                                                   [before / "expected"])))


def run_ab(tmp_path: Path, before: Path, commit: str, after: Path | None = None) -> tuple[int, dict | None]:
    after = after or forged_build(tmp_path / "after")
    out = tmp_path / "ab"
    rc = CH.main(["dxvk-ab", "--before", str(before), "--before-commit", commit, "--after", str(after),
                  "--stream", str(tmp_path / "s.sa2s"), "--out", str(out)])
    doc = json.loads((out / "dxvk-ab.json").read_text()) if (out / "dxvk-ab.json").exists() else None
    return rc, doc


@pytest.mark.parametrize("ran,needle", [
    ({}, None),
    ({"before": "0" * 64}, "before: the player that ran is not "),
    ({"after": "0" * 64}, f"after: {{after}}: the player that ran ({'0' * 64}) is not the build's sa2_player "
                          f"({PLAYER_SHA})"),
])
def test_dxvk_ab_holds_both_players_to_their_builds(monkeypatch, tmp_path, ran, needle):
    commit, before, repo = before_commit(monkeypatch, tmp_path)
    monkeypatch.setattr(CH, "run_player", ab_player(ran))
    rc, doc = run_ab(tmp_path, before, commit)
    if needle is None:
        assert (rc, doc["verdict"], doc["failures"]) == (0, "PASS", [])
        assert (doc["before_provenance"]["commit"], doc["before_provenance"]["sources_held"]) == (commit, 8)
        # each side ran under its own checkout's run-time files, the before's its clone's
        assert {n: r["checkout"] for n, r in doc["runtime"].items()} == {"before": str(repo.resolve()),
                                                                        "after": str(ROOT)}
        assert doc["runtime"]["before"]["host/player/player_env.sh"] == CH.sha256(repo / "host/player/player_env.sh")
        assert set(doc["runtime"]["after"]) == {"checkout", *CH.RUNTIME, CH.DXVK_CONF}
        # each side's own dxvk.conf, in four report fields and one log line, reads as OWN_CONF
        assert doc["own_conf"]["fields"] == dict.fromkeys(("before", "after"), [
            "dxvk.config_file", "env.DXVK_CONFIG_FILE", "files.dxvk_conf.path", "files.dxvk_conf.real_path"])
        # and its own install's DXVK library, in three, as OWN_LIBRARY
        assert doc["own_library"]["fields"] == dict.fromkeys(("before", "after"), LIBRARY_FIELDS_NAMED)
        assert doc["own_library"]["paths"] == {"before": CH.own_library(repo.resolve()), "after": CH.own_library(ROOT)}
        assert not [x for x in doc["dxvk_log_lines_differing"] if "config file" in x]
        return
    assert (rc, doc["verdict"]) == (1, "FAIL")
    needle = needle.format(after=CH.rel(tmp_path / "ab" / "after"))
    assert any(f.startswith(needle) for f in doc["failures"]), doc["failures"]


def test_dxvk_ab_refuses_one_player_on_both_sides(monkeypatch, tmp_path, capsys):
    """The review's p3: --before = --after passed, comparing nothing."""
    after = forged_build(tmp_path / "after")
    commit, _, _ = before_commit(monkeypatch, tmp_path)
    monkeypatch.setattr(CH, "run_player", ab_player({}))
    rc, doc = run_ab(tmp_path, after, commit, after)
    assert (rc, doc) == (2, None)
    assert "before and after are the same player" in capsys.readouterr().err


def test_dxvk_ab_refuses_two_builds_of_one_tree(monkeypatch, tmp_path, capsys):
    """The confirm's p3: a before built from the after's own sources passed (two builds of
    one tree differ in their binaries' hashes). Refused, before anything runs, by
    dxvk-unchanged's own rule (dxvk_path); one source changed in the before's commit, the
    A/B runs and names that source."""
    commit, before, repo = before_commit(monkeypatch, tmp_path, tree=True)
    monkeypatch.setattr(CH, "run_player", ab_player({}))
    rc, doc = run_ab(tmp_path, before, commit)
    err = capsys.readouterr().err
    assert (rc, doc) == (2, None) and not (tmp_path / "ab").exists()
    assert f"refused: the after's DXVK path is commit {commit[:12]}'s (all {len(DXVK_EXACT)} sources of its " \
           f"sa2_player.json and its 2 run-time files the commit's, no file of the commit shadowing an include)" in err
    assert "compare_hosts.py dxvk-unchanged says UNCHANGED, by the same rule" in err
    with (repo / "host" / "player" / "player.cpp").open("a") as f:
        f.write("// the player as it was\n")
    git(repo, "commit", "-q", "-am", "an earlier player")
    record_before(repo, before)
    rc, doc = run_ab(tmp_path, before, git(repo, "rev-parse", "HEAD"))
    assert (rc, doc["verdict"]) == (0, "PASS"), doc["failures"]
    # dxvk-ab.json names the change the A/B covered (the review's NIT 4)
    assert doc["host_sources_differing"] == ["host/player/player.cpp"]
    assert "1 differ: ['host/player/player.cpp']" in capsys.readouterr().out


def appended(path: str, text: str):
    def change(repo: Path):
        with (repo / path).open("a") as f:
            f.write(text)
    return change


SHADOW = "host/player/sa2_stream.h"
# How dxvk-unchanged names a symbolic link the commit holds under an include directory.
LINK = ("at the commit, a symbolic link (mode 120000) under an include directory, through which an include could "
        "find what the commit does not hold")


@pytest.mark.parametrize("change,differing", [
    (None, []),
    # the review's dead end: what the before's build and run read beside the player's
    # sources, which both sides of the A/B shared
    (appended("tools/pins.json", "\n"), ["tools/pins.json"]),
    (appended("host/player/player_env.sh", "# a comment\n"), ["host/player/player_env.sh"]),
    (appended("host/player/run_player.sh", "# a comment\n"), ["host/player/run_player.sh"]),
    (appended("host/player/player.cpp", "// a comment\n"), ["host/player/player.cpp"]),
    # a file of the commit a quoted include of the stream header would have found first
    (lambda r: (r / SHADOW).write_text("// shadow\n"),
     [f"{SHADOW}: at the commit, where an include could find it before what this build read"]),
    # and one ahead of a system header (the confirm's S1)
    (lambda r: (r / "host/cerrno").write_text("#error shadowed: host/cerrno\n"),
     lambda: [f"host/cerrno: at the commit, where an include could find it before the compiler's system header "
              f"{compiler_opens('cerrno')}"]),
    # and a link under an include directory (the review's SHOULD-FIX 1)
    (lambda r: os.symlink("../elsewhere", r / "host/SDL3"), lambda: [f"host/SDL3: {LINK}"]),
])
def test_dxvk_unchanged_and_dxvk_ab_agree_on_one_pair(monkeypatch, tmp_path, capsys, change, differing):
    """The review's dead end: dxvk-unchanged said CHANGED (tools/pins.json, a run-time file)
    while dxvk-ab refused the same pair as built from the same sources, so step 7 could not
    pass. One rule says both now (dxvk_path), and dxvk-ab's sides differ in exactly what it
    holds (the before's pins and run-time files its clone's): CHANGED always has an A/B to
    run, which names the same items; UNCHANGED, and the A/B is refused."""
    commit, before, repo = before_commit(monkeypatch, tmp_path, tree=True)
    if change:
        commit = commit_change(repo, change)
        record_before(repo, before)
    after = forged_build(tmp_path / "after")
    rc, udoc = run_unchanged(tmp_path, commit, after)
    player = ab_player({})
    monkeypatch.setattr(CH, "run_player", player)
    arc, adoc = run_ab(tmp_path, before, commit, after)
    differing = differing() if callable(differing) else differing
    if not differing:
        assert (rc, udoc["verdict"], arc, adoc) == (0, "UNCHANGED", 2, None)
        assert "says UNCHANGED, by the same rule" in capsys.readouterr().err
        return
    assert (rc, udoc["verdict"], udoc["differing"]) == (1, "CHANGED", differing)
    assert (arc, adoc["verdict"], adoc["host_sources_differing"]) == (0, "PASS", differing), adoc["failures"]
    # the before ran under its clone's run-time files, which are the commit's
    assert player.checkouts == {"before": repo.resolve(), "after": CH.ROOT}
    assert adoc["runtime"]["before"]["host/player/player_env.sh"] == CH.blob_sha(commit, "host/player/player_env.sh")


@pytest.mark.parametrize("before_conf,line,needle", [
    # the before naming the after's dxvk.conf (not its own) differs, in the report and the log
    ("after", False, "report env.DXVK_CONFIG_FILE: before "),
    (None, True, "DXVK's log line 1 differs outside DXVK_AB_LOG_ALLOWED"),
])
def test_dxvk_ab_takes_out_each_side_s_own_dxvk_conf_and_no_other(monkeypatch, tmp_path, before_conf, line, needle):
    commit, before, _ = before_commit(monkeypatch, tmp_path)
    conf = {"before": str(CH.ROOT / CH.DXVK_CONF)} if before_conf else None
    player = ab_player({}, conf=conf)
    if line:
        def run_player(*a, **kw):
            run = player(*a, **kw)
            log = Path(run["report"]["dxvk"]["log"])
            if a[3].name == "before":
                log.write_text(log.read_text().replace("Found config file: ", "Found config file: /elsewhere"))
            return run
        run_player.checkouts = player.checkouts
        monkeypatch.setattr(CH, "run_player", run_player)
    else:
        monkeypatch.setattr(CH, "run_player", player)
    rc, doc = run_ab(tmp_path, before, commit)
    assert (rc, doc["verdict"]) == (1, "FAIL")
    assert any(f.startswith(needle) for f in doc["failures"]), doc["failures"]


# The report fields that name the DXVK library a run loaded (player_env.sh's SA2_DXVK_D3D9).
LIBRARY_FIELDS_NAMED = ["env.SA2_DXVK_D3D9", "files.libdxvk_d3d9.path", "files.libdxvk_d3d9.real_path"]


def without_end_request() -> dict:
    """The DXVK player's report as development_snapshot's player wrote it: no end_request block."""
    rep = player_report("dxvk")
    rep.pop("end_request")
    return rep


@pytest.mark.parametrize("lib,end_request,needle", [
    # test_d3d9gpu.sh step 7 against development_snapshot: its player writes no end_request, the current one
    # writes a run's without --end-fd, and each side names its own checkout's library
    (None, None, None),
    # the before naming the after's install (not its clone's) differs in the three fields
    ({"before": str(ROOT / CH.DXVK_LIBRARY)}, None, "report env.SA2_DXVK_D3D9: before "),
    # and so does an after naming the before's clone, or any other install
    ("before-clone", None, "report files.libdxvk_d3d9.path: before "),
    ({"after": "/elsewhere/libdxvk_d3d9.so.0"}, None, "report env.SA2_DXVK_D3D9: before "),
    # an after that wrote an end request (it was given none), or wrote none of the block
    (None, lambda r: r["end_request"].update(requested=True, by="the caller's request"),
     "after: end_request is {'fd': False, 'requested': True"),
    (None, lambda r: r.pop("end_request"), "after: end_request is '<absent>', not {"),
])
def test_dxvk_ab_holds_development_snapshot_s_pair_by_its_end_request_and_own_libraries(monkeypatch, tmp_path, lib,
                                                                           end_request, needle):
    commit, before, repo = before_commit(monkeypatch, tmp_path)
    if lib == "before-clone":
        lib = {"after": str(repo.resolve() / CH.DXVK_LIBRARY)}

    def after_report():
        rep = player_report("dxvk")
        if end_request:
            end_request(rep)
        return rep
    monkeypatch.setattr(CH, "run_player", ab_player({}, lib=lib, report={"before": without_end_request,
                                                                          "after": after_report}))
    rc, doc = run_ab(tmp_path, before, commit)
    if needle is None:
        assert (rc, doc["verdict"], doc["failures"]) == (0, "PASS", [])
        assert doc["split_fields_differing"] == ["end_request"]
        assert doc["split_fields"]["end_request"] == {"before": CH.ABSENT, "after": CH.END_REQUEST_UNUSED}
        assert doc["split_rule"]["end_request"] == {"after": [CH.END_REQUEST_UNUSED],
                                                    "before": [CH.ABSENT, CH.END_REQUEST_UNUSED]}
        assert doc["own_library"]["fields"] == dict.fromkeys(("before", "after"), LIBRARY_FIELDS_NAMED)
        assert doc["report_fields_differing_outside_allowlist"] == []
        return
    assert (rc, doc["verdict"]) == (1, "FAIL")
    assert any(f.startswith(needle) for f in doc["failures"]), doc["failures"]


def test_dxvk_ab_refuses_a_compare_hosts_py_edited_since_it_started(monkeypatch, tmp_path, capsys):
    """Its pins (dxvk-ab.json's pipeline) would name code that did not run."""
    commit, before, _ = before_commit(monkeypatch, tmp_path)
    monkeypatch.setattr(CH, "run_player", ab_player({}))
    monkeypatch.setattr(CH, "RAN_BY", "1" * 64)
    rc, doc = run_ab(tmp_path, before, commit)
    assert (rc, doc) == (2, None) and not (tmp_path / "ab").exists()
    assert "edited since it started" in capsys.readouterr().err


def record_change(change):
    def edit(before: Path, repo: Path):
        rec = json.loads((before / "sa2_player.json").read_text())
        change(rec, before, repo)
        (before / "sa2_player.json").write_text(json.dumps(rec))
    return edit


@pytest.mark.parametrize("change,needle", [
    # no record: a build taken on trust
    (lambda before, repo: (before / "sa2_player.json").unlink(), "{before}: no build record"),
    # a source edited in the clone before it built (the record says so)
    (record_change(lambda r, b, repo: r["sources"].update({"host/player/player.cpp": "5" * 64})),
     "{before}: source host/player/player.cpp is " + "5" * 64 + " in its record"),
    # a source the commit holds and the record does not name
    (record_change(lambda r, b, repo: r["sources"].pop("host/dxvk_smoke.cpp")),
     "{before}: source host/dxvk_smoke.cpp is None in its record"),
    # a file in the clone the commit does not hold
    (record_change(lambda r, b, repo: r["sources"].update({"host/player/extra.cpp": "7" * 64})),
     "{before}: its record names host/player/extra.cpp, which commit"),
    # the player rebuilt since its record
    (lambda before, repo: (before / "sa2_player").write_bytes(b"rebuilt"), "{before}: sa2_player is "),
    (lambda before, repo: (before / "expected" / "expectations.h").write_text("// edited\n"),
     "{before}: generated expected/expectations.h is "),
    # pins read from the repository the clone was made from, not the commit's (build_before.sh
    # before the fold), and pins not recorded at all
    (record_change(lambda r, b, repo: r["sources"].update({str(ROOT / "tools/pins.json"): CH.sha256(
        ROOT / "tools/pins.json")})), "{before}: its record names " + str(ROOT / "tools/pins.json") + ", outside"),
    (record_change(lambda r, b, repo: r["sources"].pop("tools/pins.json")),
     "{before}: source tools/pins.json is None in its record"),
    # a run-time file of its checkout edited since (the before runs under them), or none at all
    (lambda before, repo: (repo / "host/player/player_env.sh").write_text("# edited in the clone\n"),
     "{before}: its checkout's host/player/player_env.sh is "),
    (lambda before, repo: (repo / "host/player/dxvk.conf").unlink(), "{before}: its checkout's host/player/dxvk.conf "
     "is None, "),
    (record_change(lambda r, b, repo: r.pop("tree")), "{before}: its record names no checkout (tree)"),
])
def test_dxvk_ab_refuses_a_before_build_not_held_to_its_commit(monkeypatch, tmp_path, capsys, change, needle):
    commit, before, repo = before_commit(monkeypatch, tmp_path)
    change(before, repo)
    monkeypatch.setattr(CH, "run_player", ab_player({}))
    rc, doc = run_ab(tmp_path, before, commit)
    err = capsys.readouterr().err
    assert (rc, doc) == (2, None) and "is not held to commit" in err
    assert needle.format(before=CH.rel(before)) in err, err


def test_dxvk_ab_refuses_a_commit_that_is_not_the_before_build_s(monkeypatch, tmp_path, capsys):
    commit, before, repo = before_commit(monkeypatch, tmp_path)
    (repo / "host" / "player" / "player.cpp").write_text("int main() { return 1; }\n")
    git(repo, "commit", "-q", "-am", "the switch")
    monkeypatch.setattr(CH, "run_player", ab_player({}))
    assert run_ab(tmp_path, before, "HEAD")[0] == 2
    assert f"{CH.rel(before)}: source host/player/player.cpp is " in capsys.readouterr().err
    assert run_ab(tmp_path, before, "no-such-commit")[0] == 2
    assert "--before-commit 'no-such-commit' is no commit" in capsys.readouterr().err
    assert run_ab(tmp_path, before, commit)[0] == 0


@pytest.mark.parametrize("log,ok", [
    ({"before": "info:    Budget: 30.40 GiB\n", "after": "info:    Budget: 30.37 GiB\n"}, True),
    ({"before": "info:  Vulkan: Found vkGetInstanceProcAddr in libvulkan.so @ 0x7f19cd57b180\n",
      "after": "info:  Vulkan: Found vkGetInstanceProcAddr in libvulkan.so @ 0x7fc703dc6180\n"}, True),
    ({"after": "info:    Budget: 30.40 GiB\nwarn:  D3D9DeviceEx::Clear: unsupported\n"}, False),
    ({"after": "info:    Budget: 30.40 GiB\ninfo:  Memory Heap[0]: \n"}, False),
    ({"after": "info:  Vulkan: Found vkGetInstanceProcAddr in libvulkan.so @ 0x7fc703dc6180\n"}, False),
])
def test_dxvk_ab_fails_a_dxvk_log_line_outside_the_allowlist(monkeypatch, tmp_path, log, ok):
    """The review's finding 4: differing log lines were listed and never failed."""
    commit, before, _ = before_commit(monkeypatch, tmp_path)
    monkeypatch.setattr(CH, "run_player", ab_player({}, log))
    rc, doc = run_ab(tmp_path, before, commit)
    assert (rc == 0, doc["verdict"] == "PASS") == (ok, ok), doc["failures"]
    assert ok or any(f.startswith("DXVK's log line ") and "differs outside DXVK_AB_LOG_ALLOWED" in f
                     for f in doc["failures"])
    assert len(doc["dxvk_log_lines_differing"]) >= 1


def test_dxvk_ab_fails_a_run_that_left_no_dxvk_log(monkeypatch, tmp_path):
    commit, before, _ = before_commit(monkeypatch, tmp_path)
    monkeypatch.setattr(CH, "run_player", ab_player({}, {"after": None}))
    rc, doc = run_ab(tmp_path, before, commit)
    assert (rc, doc["verdict"]) == (1, "FAIL")
    assert any(f.startswith("after: no DXVK log (") for f in doc["failures"]), doc["failures"]


def with_own_contract(before: Path, repo: Path, contract: dict | None) -> None:
    """The before build's expectations as development_snapshot's build wrote them (expected/expectations.json,
    with the second host's library in its contract), its record re-made over them."""
    (before / "expected" / "expectations.json").write_text(json.dumps({"contract": contract}))
    record_before(repo, before)


@pytest.mark.parametrize("own,expected,ok", [
    (OWN, OWN, True),             # development_snapshot's: its own build's library
    (OWN, GPU_SHA, False),        # another build's
    (None, OWN, False),           # a contract with no such key: the field may not be there
])
def test_dxvk_ab_holds_development_snapshot_s_before_to_its_own_build(monkeypatch, tmp_path, own, expected, ok):
    """End to end: the before is development_snapshot's one player run as the DXVK host (its report has
    the second host's fields with DXVK's answers), the after the DXVK player alone. The
    split fields differ, each named in dxvk-ab.json with both sides' values; the before's
    expected library is held to its own build's contract, never to the after's build."""
    commit, before, repo = before_commit(monkeypatch, tmp_path)
    with_own_contract(before, repo, {**DXVK_CONTRACT, **({"d3d9gpu_sha256": own} if own else {})})

    def before_report():
        rep = development_snapshot_report()
        rep["host_identity"]["expected_d3d9gpu_sha256"] = expected
        return rep
    monkeypatch.setattr(CH, "run_player", ab_player({}, report={"before": before_report}))
    rc, doc = run_ab(tmp_path, before, commit)
    assert (rc == 0, doc["verdict"] == "PASS") == (ok, ok), doc["failures"]
    assert doc["split_fields_differing"] == ["files.libsa2_d3d9gpu", "host_identity.d3d9gpu_file_verified",
                                             "host_identity.expected_d3d9gpu_sha256"]
    assert doc["split_fields"]["host_identity.expected_d3d9gpu_sha256"] == {"before": expected, "after": CH.ABSENT}
    assert doc["report_fields_differing_outside_allowlist"] == []
    assert ok or doc["failures"] == [f"before: host_identity.expected_d3d9gpu_sha256 is '{expected}', not '<absent>'"
                                     + (f" or '{own}'" if own else "")]


@pytest.mark.parametrize("change,needle", [
    # the after's sa2_player.json is the DXVK player's exact record ...
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r.update(schema="sa2-build-record/v1")),
     "sa2_player.json: sa2-build-record/v1 of ['sa2_player'], not sa2_player's exact record"),
    # ... built from nothing of the second host's ...
    (lambda b: edit_record(b, "sa2_player.json", lambda r: r["sources"].update(
        {"host/d3d9gpu/d9g_entry.cpp": CH.pipeline()["host/d3d9gpu/d9g_entry.cpp"]})),
     "sa2_player.json: the DXVK player was built from the second host's"),
    # ... and expecting DXVK's files alone
    (rewrite_contract("expected", "sa2_player.json", {**DXVK_CONTRACT, "d3d9gpu_sha256": GPU_SHA}),
     "sa2_player's contract (expected/expectations.json) is"),
])
def test_dxvk_ab_refuses_an_after_that_is_not_the_dxvk_player_alone(monkeypatch, tmp_path, capsys, change, needle):
    commit, before, _ = before_commit(monkeypatch, tmp_path)
    after = forged_build(tmp_path / "after")
    change(after)
    monkeypatch.setattr(CH, "run_player", ab_player({}))
    rc, doc = run_ab(tmp_path, before, commit, after)
    err = capsys.readouterr().err
    assert (rc, doc) == (2, None) and "is not the tree this comparison pins" in err and needle in err, err


def exact_before(repo: Path, before: Path, change=None) -> None:
    """The before's record as a commit's own host/player/build.sh writes it after the two
    binaries: the player's exact record (v2), its sources the clone's files it read, its
    pins the clone's (build_before.sh builds with SA2_REPO the clone)."""
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    rec = {"schema": "sa2-build-record/v2", "tree": str(repo.resolve()),
           "binaries": {"sa2_player": sha(before / "sa2_player")},
           "sources": {p: sha(repo / p) for p in ("host/player/player.cpp", "host/player/build.sh",
                                                   "host/sa2_stream.h", "tools/pins.json")},
           "generated": {"expected/expectations.h": sha(before / "expected" / "expectations.h")},
           "external": {}, "depfiles": {}}
    if change:
        change(rec)
    (before / "sa2_player.json").write_text(json.dumps(rec))


@pytest.mark.parametrize("change,needle", [
    (None, None),
    # a source edited in the clone before it built
    (lambda r: r["sources"].update({"host/player/player.cpp": "5" * 64}),
     "source host/player/player.cpp is " + "5" * 64 + " in its record"),
    # a file the compile read that the commit does not hold
    (lambda r: r["sources"].update({"host/player/extra.h": "7" * 64}), "its record names host/player/extra.h, which"),
    # an exact record without the player's own source or its build script
    (lambda r: r["sources"].pop("host/player/build.sh"), "its exact record does not name host/player/build.sh"),
    (lambda r: r["sources"].pop("host/player/player.cpp"), "its exact record does not name host/player/player.cpp"),
    # a source read from outside its checkout (the pins of the repository it was cloned from)
    (lambda r: r["sources"].update({str(ROOT / "tools" / "pins.json"): CH.sha256(ROOT / "tools" / "pins.json")}),
     f"its record names {ROOT / 'tools' / 'pins.json'}, outside its checkout"),
    # no checkout, whose run-time files it would run under
    (lambda r: r.pop("tree"), "its record names no checkout (tree)"),
])
def test_before_provenance_holds_an_exact_record_to_its_commit(monkeypatch, tmp_path, change, needle):
    commit, before, repo = before_commit(monkeypatch, tmp_path)
    exact_before(repo, before, change)
    prov = CH.before_provenance(before, commit)
    if needle is None:
        assert (prov["pass"], prov["sources_held"], prov["record_schema"]) == (True, 4, "sa2-build-record/v2")
        assert (prov["checkout"], sorted(prov["runtime"])) == (str(repo.resolve()), sorted((*CH.RUNTIME, CH.DXVK_CONF)))
        return
    assert prov["pass"] is False and any(needle in f for f in prov["failures"]), prov["failures"]


# ------------------------------------------------------------------ dxvk-unchanged
def unchanged_commit(monkeypatch, tmp_path: Path) -> tuple[str, Path]:
    """A repository (GIT_REPO) whose one commit holds this tree's DXVK player as the forged
    after build's record names it: every file directly in host/ and host/player, and
    tools/pins.json, with the tree's bytes."""
    repo = tmp_path / "repo"
    for d in CH.INCLUDE_DIRS:
        (repo / d).mkdir(parents=True, exist_ok=True)
        for f in (ROOT / d).iterdir():
            if f.is_file():
                shutil.copyfile(f, repo / d / f.name)
    (repo / "tools").mkdir()
    shutil.copyfile(ROOT / "tools" / "pins.json", repo / "tools" / "pins.json")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "the DXVK player")
    monkeypatch.setattr(CH, "GIT_REPO", repo)
    return git(repo, "rev-parse", "HEAD"), repo


def run_unchanged(tmp_path: Path, commit: str, after: Path | None = None) -> tuple[int, dict | None]:
    after = after or forged_build(tmp_path / "after")
    out = tmp_path / "u"
    rc = CH.main(["dxvk-unchanged", "--after", str(after), "--before-commit", commit, "--out", str(out)])
    doc = json.loads((out / "dxvk-unchanged.json").read_text()) if (out / "dxvk-unchanged.json").exists() else None
    return rc, doc


def executable(path: Path) -> None:
    """A script written at `path`, mode 0755 (git: 100755)."""
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)


def commit_change(repo: Path, change) -> str:
    change(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a change")
    return git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("change,differing", [
    (None, None),
    # one source the DXVK player's compile read
    (lambda r: (r / "host/player/sha256.h").write_text("// another\n"), ["host/player/sha256.h"]),
    # its pins
    (lambda r: (r / "tools/pins.json").write_text("{}\n"), ["tools/pins.json"]),
    # the run-time files each side of dxvk-ab runs under, its own checkout's
    (lambda r: (r / "host/player/player_env.sh").write_text("# another\n"), ["host/player/player_env.sh"]),
    (lambda r: (r / "host/player/run_player.sh").unlink(), ["host/player/run_player.sh"]),
    # a file of the commit where an include of a file this build read could find it first:
    # a quoted include of the stream header from host/player, an angle include of the
    # install's SDL3/SDL.h searching -I host/ (at any depth: the reviews' NITs 2 and 6) ...
    (lambda r: (r / "host/player/sa2_stream.h").write_text("//\n"),
     ["host/player/sa2_stream.h: at the commit, where an include could find it before what this build read"]),
    (lambda r: (r / "host/SDL3").mkdir() or (r / "host/SDL3/SDL.h").write_text("//\n"),
     ["host/SDL3/SDL.h: at the commit, where an include could find it before what this build read"]),
    # ... or a system header, which no -MMD depfile names (the confirm's S1: with host/cerrno
    # the commit's player does not build, and the build's record could not show it) ...
    (lambda r: (r / "host/cerrno").write_text("#error shadowed: host/cerrno\n"),
     lambda: [f"host/cerrno: at the commit, where an include could find it before the compiler's system header "
              f"{compiler_opens('cerrno')}"]),
    # ... and a symbolic link under an include directory, to a directory or a file, which
    # git holds as a link and through which an include finds what no commit holds (the
    # review's SHOULD-FIX 1: listed by name only, a link matched no candidate)
    (lambda r: os.symlink("../elsewhere/SDL3", r / "host/SDL3"), [f"host/SDL3: {LINK}"]),
    (lambda r: os.symlink("../sa2_stream.h", r / "host/player/stream_link.h"), [f"host/player/stream_link.h: {LINK}"]),
    # ... and nothing for a file no include could name (one the build does not read, added or
    # removed: development_snapshot lacked build_record.py)
    (lambda r: (r / "host/player/extra.h").write_text("//\n"), None),
    (lambda r: (r / "host/sa2_present_rec.c").unlink(), None),
    (lambda r: (r / "host/player/build_record.py").unlink(), None),
    # ... an executable file is a file (mode 100755), not an entry an include finds through
    (lambda r: executable(r / "host/player/extra.sh"), None),
])
def test_dxvk_unchanged_holds_the_dxvk_player_to_the_commit(monkeypatch, tmp_path, change, differing):
    """UNCHANGED (exit 0) only when every source of the after's exact record, run_player.sh
    and player_env.sh are the commit's and the commit holds no file an include could find
    before what the build read; CHANGED (exit 1) names each that is not."""
    commit, repo = unchanged_commit(monkeypatch, tmp_path)
    if change:
        commit = commit_change(repo, change)
    after = forged_build(tmp_path / "after")
    sdl = tmp_path / "install" / "include" / "SDL3" / "SDL.h"       # an install header the build read
    sdl.parent.mkdir(parents=True)
    sdl.write_text("// SDL\n")
    edit_record(after, "sa2_player.json", lambda r: r["external"].update({str(sdl): CH.sha256(sdl)}))
    rc, doc = run_unchanged(tmp_path, commit, after)
    if differing is None:
        assert (rc, doc["verdict"], doc["differing"], doc["shadowing"]) == (0, "UNCHANGED", [], [])
        assert sorted(doc["sources"]) == sorted(DXVK_EXACT) and sorted(doc["runtime"]) == [
            "host/player/player_env.sh", "host/player/run_player.sh"]
        assert doc["not_held"] == list(CH.UNCHANGED_NOT_HELD) and doc["before_commit"] == commit
        # the directories it looked in for system headers, the compiler's own
        assert doc["system_include_dirs"] and os.path.dirname(compiler_opens("cerrno")) in doc["system_include_dirs"]
        return
    differing = differing() if callable(differing) else differing
    assert (rc, doc["verdict"], doc["differing"]) == (1, "CHANGED", differing)


def test_dxvk_unchanged_sees_a_link_through_which_the_commit_s_player_reads_another_header(monkeypatch, tmp_path):
    """The review's SHOULD-FIX 1, half (a): a commit holding host/SDL3 as a link to a
    directory outside the repository, where an SDL3/SDL.h stops the compile. The compiler
    opens that header through the link ahead of the install's (g++ -H, -I host first, as
    host/player/build.sh orders it); dxvk-unchanged says CHANGED, naming the link."""
    if not GXX:
        pytest.fail("no g++")
    commit, repo = unchanged_commit(monkeypatch, tmp_path)
    elsewhere = tmp_path / "elsewhere" / "SDL3"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SDL.h").write_text("#error read through host/SDL3\n")
    install = tmp_path / "install" / "include" / "SDL3"
    install.mkdir(parents=True)
    (install / "SDL.h").write_text("// the install's SDL\n")
    commit = commit_change(repo, lambda r: os.symlink(elsewhere, r / "host/SDL3"))
    p = subprocess.run([GXX, "-x", "c++", "-E", "-H", "-I", str(repo / "host"), "-I", str(install.parent), "-o",
                        os.devnull, "-"], input="#include <SDL3/SDL.h>\n", capture_output=True, text=True)
    assert p.returncode != 0 and f". {repo}/host/SDL3/SDL.h" in p.stderr, p.stderr
    after = forged_build(tmp_path / "after")
    edit_record(after, "sa2_player.json", lambda r: r["external"].update({str(install / "SDL.h"): CH.sha256(
        install / "SDL.h")}))
    rc, doc = run_unchanged(tmp_path, commit, after)
    assert (rc, doc["verdict"], doc["shadowing"], doc["differing"]) == (1, "CHANGED", ["host/SDL3"],
                                                                         [f"host/SDL3: {LINK}"])


def compiler_opens(name: str) -> str:
    """The file the compiler itself opens for `#include <name>` (g++ -H, not its search list):
    the system header an include of that name finds when nothing ahead of it shadows it."""
    if not GXX:
        pytest.fail("no g++")
    p = subprocess.run([GXX, "-x", "c++", "-E", "-H", "-o", os.devnull, "-"], input=f"#include <{name}>\n",
                       capture_output=True, text=True, check=True)
    return next(line[2:] for line in p.stderr.splitlines() if line.startswith(". "))


@pytest.mark.parametrize("path,name", [
    ("host/cerrno", "cerrno"),
    # at any depth under -I host/ ...
    ("host/sys/types.h", "sys/types.h"),
    # ... and in host/player, which a quoted include from the player's own files searches first
    ("host/player/stdio.h", "stdio.h"),
])
def test_dxvk_unchanged_sees_a_file_of_the_commit_ahead_of_a_system_header(monkeypatch, tmp_path, path, name):
    """The confirm's S1: the player's -MMD depfiles name no system header, so no tail of a
    file the build read names host/cerrno; -I host/ is searched before the compiler's own
    directories, and with that file the commit's player does not even build. CHANGED, by the
    compiler's own search list, naming the header the compiler would otherwise open."""
    commit, repo = unchanged_commit(monkeypatch, tmp_path)

    def plant(r: Path):
        (r / path).parent.mkdir(parents=True, exist_ok=True)
        (r / path).write_text(f"#error shadowed: {path}\n")
    commit = commit_change(repo, plant)
    rc, doc = run_unchanged(tmp_path, commit)
    assert (rc, doc["verdict"], doc["shadowing"]) == (1, "CHANGED", [path])
    assert doc["differing"] == [f"{path}: at the commit, where an include could find it before the compiler's "
                                f"system header {compiler_opens(name)}"]


def test_dxvk_unchanged_holds_a_source_named_like_a_system_header_by_its_hash(monkeypatch, tmp_path):
    """A file the build read is held by its hash wherever it sits, never as shadowing: a
    host/player/stdio.h the record names, the commit's bytes, leaves the DXVK path the
    commit's (UNCHANGED); with other bytes at the commit, CHANGED by the source rule alone."""
    commit, repo = unchanged_commit(monkeypatch, tmp_path)
    own = "host/player/stdio.h"
    commit = commit_change(repo, lambda r: (r / own).write_text("// the player's own stdio.h\n"))
    pins = CH.pipeline()
    h = CH.sha256(repo / own)
    monkeypatch.setattr(CH, "pipeline", lambda: dict(pins, **{own: h}))
    after = forged_build(tmp_path / "after")
    edit_record(after, "sa2_player.json", lambda r: r["sources"].update({own: h}))
    rc, doc = run_unchanged(tmp_path, commit, after)
    assert (rc, doc["verdict"], doc["shadowing"], doc["differing"]) == (0, "UNCHANGED", [], [])
    other = commit_change(repo, lambda r: (r / own).write_text("// another stdio.h\n"))
    shutil.rmtree(tmp_path / "u")
    rc, doc = run_unchanged(tmp_path, other, after)
    assert (rc, doc["verdict"], doc["shadowing"], doc["differing"]) == (1, "CHANGED", [], [own])


@pytest.mark.parametrize("change,why", [
    (lambda c: None, "names no compiler"),
    # no search list recorded, an empty one, or not a list
    (lambda c: {k: v for k, v in c.items() if k != "search"}, "records no include search list"),
    (lambda c: dict(c, search=[]), "records no include search list"),
    (lambda c: dict(c, search="/usr/include"), "records no include search list"),
    (lambda c: dict(c, search=["", "/usr/include"]), "records no include search list"),
    # a compiler that cannot be run now
    (lambda c: dict(c, command="/nonexistent/g++"), "cannot be run"),
    # another compiler now than the one whose search list the build recorded (the review's
    # NIT: g++ resolving elsewhere between the build and the check)
    (lambda c: dict(c, version="g++ (another) 13.1.0"), "is another compiler now"),
    (lambda c: dict(c, command="true"), "is another compiler now"),
    (lambda c: dict(c, command="false"), "is another compiler now"),
])
def test_dxvk_unchanged_and_dxvk_ab_refuse_a_compiler_they_cannot_hold(monkeypatch, tmp_path, capsys, change, why):
    """Without the build's search list, or with another compiler now than the one it is of,
    a file of the commit ahead of a system header cannot be seen: dxvk-unchanged refuses
    (exit 2, no document) rather than say UNCHANGED, and dxvk-ab refuses before anything
    runs, by the same rule."""
    commit, repo = unchanged_commit(monkeypatch, tmp_path)
    after = forged_build(tmp_path / "after")
    edit_record(after, "sa2_player.json", lambda r: r.update(compiler=change(r["compiler"])))
    assert run_unchanged(tmp_path, commit, after) == (2, None)
    err = capsys.readouterr().err
    assert "refused: sa2_player.json's compiler (" in err and why in err, err
    commit, before, repo = before_commit(monkeypatch, tmp_path / "ab-pair", tree=True)
    commit = commit_change(repo, appended("host/player/player.cpp", "// an earlier player\n"))
    record_before(repo, before)
    player = ab_player({})
    monkeypatch.setattr(CH, "run_player", player)
    assert run_ab(tmp_path, before, commit, after) == (2, None)
    err = capsys.readouterr().err
    assert "refused: sa2_player.json's compiler (" in err and why in err and player.checkouts == {}, err


def test_dxvk_unchanged_and_dxvk_ab_refuse_a_commit_whose_tree_git_cannot_list(monkeypatch, tmp_path, capsys):
    """git ls-tree failing is no tree with nothing in it: refused, not UNCHANGED; and dxvk-ab,
    which asks the same rule (dxvk_path) of a before held to its commit, refuses before
    anything runs (the confirm's residual 4: held through dxvk-unchanged alone). Only the
    listing of the include directories fails, so before_provenance's own listing of the
    before's sources still holds the pair."""
    commit, repo = unchanged_commit(monkeypatch, tmp_path)
    git_bytes = CH.git_bytes
    dirs = tuple(d + "/" for d in CH.INCLUDE_DIRS)
    monkeypatch.setattr(CH, "git_bytes", lambda *a: None if a[0] == "ls-tree" and a[-len(dirs):] == dirs
                        else git_bytes(*a))
    after = forged_build(tmp_path / "after")
    assert run_unchanged(tmp_path, commit, after) == (2, None)
    assert f"refused: git cannot list commit {commit[:12]}'s host/player, host (ls-tree)" in capsys.readouterr().err
    commit, before, repo = before_commit(monkeypatch, tmp_path / "ab-pair", tree=True)
    commit = commit_change(repo, appended("host/player/player.cpp", "// an earlier player\n"))
    record_before(repo, before)
    assert CH.before_provenance(before, commit)["pass"]
    player = ab_player({})
    monkeypatch.setattr(CH, "run_player", player)
    assert run_ab(tmp_path, before, commit, after) == (2, None) and not (tmp_path / "ab").exists()
    err = capsys.readouterr().err
    assert f"refused: git cannot list commit {commit[:12]}'s host/player, host (ls-tree)" in err, err
    assert player.checkouts == {}


def test_dxvk_unchanged_searches_the_directories_the_build_s_compiler_searched(monkeypatch, tmp_path):
    """The search list is the build's (build_record.py, in the build's environment), not the
    checker's: a directory CPLUS_INCLUDE_PATH added at build time alone is searched, so a
    commit's file ahead of a header there reads as CHANGED (the review's NIT: the checker's
    environment is not the build's)."""
    commit, repo = unchanged_commit(monkeypatch, tmp_path)
    extra = tmp_path / "build-env-include"
    extra.mkdir()
    (extra / "only_at_build_time.h").write_text("// a header the build's environment added\n")
    monkeypatch.setenv("CPLUS_INCLUDE_PATH", str(extra))
    built = CH.build_record.compiler_line(GXX)
    monkeypatch.delenv("CPLUS_INCLUDE_PATH")
    assert str(extra) in built["search"] and str(extra) not in compiler()["search"]
    after = forged_build(tmp_path / "after")
    edit_record(after, "sa2_player.json", lambda r: r.update(compiler=built))
    commit = commit_change(repo, lambda r: (r / "host/only_at_build_time.h").write_text("#error shadowed\n"))
    rc, doc = run_unchanged(tmp_path, commit, after)
    assert (rc, doc["verdict"], doc["differing"]) == (1, "CHANGED", [
        f"host/only_at_build_time.h: at the commit, where an include could find it before the compiler's system "
        f"header {extra}/only_at_build_time.h"])
    assert doc["system_include_dirs"] == built["search"]


@pytest.mark.parametrize("change,needle", [
    # an after not held to the tree, or not the DXVK player alone: provenance refuses it
    (lambda b, r: edit_record(b, "sa2_player.json", lambda x: x["sources"].update({"host/player/sha256.h": "5" * 64})),
     "sa2_player.json: source host/player/sha256.h is "),
    (lambda b, r: edit_record(b, "sa2_player.json", lambda x: x.update(schema="sa2-build-record/v1")),
     "not sa2_player's exact record"),
    (lambda b, r: edit_record(b, "sa2_player.json", lambda x: x["sources"].update(
        {"host/d3d9gpu/d9g_entry.cpp": CH.pipeline()["host/d3d9gpu/d9g_entry.cpp"]})),
     "the DXVK player was built from the second host's"),
    (lambda b, r: (b / "sa2_player_gpu.json").unlink(), "sa2_player_gpu.json: no build record"),
    # a source outside the tree, which no commit holds
    (lambda b, r: edit_record(b, "sa2_player.json", lambda x: x["sources"].update({"/elsewhere/x.h": "5" * 64})),
     "sa2_player.json: source /elsewhere/x.h is not pinned"),
    # a record that does not name what made the player: UNCHANGED would hold nothing (the
    # review's finding 2 and NIT 3)
    (lambda b, r: edit_record(b, "sa2_player.json", lambda x: x["sources"].pop("host/player/player.cpp")),
     "sa2_player.json: names none of ['host/player/player.cpp']"),
    (lambda b, r: edit_record(b, "sa2_player.json", lambda x: x["sources"].pop("host/player/sha256.h")),
     "which the record does not hold (sources host/player/sha256.h)"),
])
def test_dxvk_unchanged_refuses_a_build_it_cannot_hold(monkeypatch, tmp_path, capsys, change, needle):
    commit, repo = unchanged_commit(monkeypatch, tmp_path)
    after = forged_build(tmp_path / "after")
    change(after, repo)
    rc, doc = run_unchanged(tmp_path, commit, after)
    err = capsys.readouterr().err
    assert (rc, doc) == (2, None) and needle in err, err


def test_dxvk_unchanged_refuses_no_commit_and_an_edited_self(monkeypatch, tmp_path, capsys):
    commit, _ = unchanged_commit(monkeypatch, tmp_path)
    assert run_unchanged(tmp_path, "no-such-commit") == (2, None)
    assert "--before-commit 'no-such-commit' is no commit" in capsys.readouterr().err
    monkeypatch.setattr(CH, "RAN_BY", "1" * 64)
    assert run_unchanged(tmp_path, commit) == (2, None)
    assert "edited since it started" in capsys.readouterr().err


def test_dxvk_unchanged_refuses_a_source_outside_the_tree(monkeypatch, tmp_path, capsys):
    """A record naming a source by an absolute path (SA2_STREAM_H_DIR elsewhere) cannot be
    held to a commit: refused, even when the pipeline would pin it."""
    commit, _ = unchanged_commit(monkeypatch, tmp_path)
    after = forged_build(tmp_path / "after")
    pins = CH.pipeline()
    outside = str(tmp_path / "elsewhere.h")
    monkeypatch.setattr(CH, "pipeline", lambda: dict(pins, **{outside: "5" * 64}))
    edit_record(after, "sa2_player.json", lambda x: x["sources"].update({outside: "5" * 64}))
    assert run_unchanged(tmp_path, commit, after) == (2, None)
    assert f"refused: sa2_player.json names sources outside the tree, which no commit holds: ['{outside}']" in \
        capsys.readouterr().err


def test_the_build_record_names_what_the_build_read(tmp_path):
    """host/player/build_record.py, which both build scripts end with: binaries by name,
    sources by path in the checkout (a directory stands for its files, __pycache__ left
    out), generated inputs by directory and name; a missing source stops the build."""
    sys.path.insert(0, str(ROOT / "host" / "player"))
    import build_record as BR
    tree, out = tmp_path / "tree", tmp_path / "out"
    (tree / "host" / "d" / "__pycache__").mkdir(parents=True)
    (tree / "host" / "d" / "a.cpp").write_text("a")
    (tree / "host" / "d" / "__pycache__" / "x.pyc").write_text("x")
    (tree / "host" / "s.h").write_text("s")
    (out / "gen").mkdir(parents=True)
    (out / "gen" / "g.h").write_text("g")
    (out / "lib.so").write_bytes(b"lib")
    outside = tmp_path / "pins.json"
    outside.write_text("{}")
    rec = BR.record(tree, out, ["lib.so"], [tree / "host" / "d", tree / "host" / "s.h", outside], [out / "gen"])
    h = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    assert rec["binaries"] == {"lib.so": h(b"lib")}
    assert rec["sources"] == {"host/d/a.cpp": h(b"a"), "host/s.h": h(b"s"), str(outside): h(b"{}")}
    assert rec["generated"] == {"gen/g.h": h(b"g")}
    with pytest.raises(SystemExit, match="no source"):
        BR.record(tree, out, ["lib.so"], [tree / "host" / "gone.cpp"], [])


def test_the_build_record_refuses_sources_that_moved_while_the_build_ran(tmp_path):
    """build_record.py --start hashes the sources before the build compiles (and removes an
    earlier record); the closing call refuses, with no record, when one changed, appeared
    or went since, and when no --start ran."""
    sys.path.insert(0, str(ROOT / "host" / "player"))
    import build_record as BR
    tree, out = tmp_path / "tree", tmp_path / "out"
    (tree / "host" / "d").mkdir(parents=True)
    (tree / "host" / "d" / "a.cpp").write_text("a")
    out.mkdir()
    (out / "lib.so").write_bytes(b"lib")
    rec = out / "lib.json"
    start = ["--start", "--out", str(rec), "--tree", str(tree), "--sources", str(tree / "host" / "d")]
    close = ["--out", str(rec), "--tree", str(tree), "--bin-dir", str(out), "--binaries", "lib.so",
             "--sources", str(tree / "host" / "d")]
    rec.write_text("an earlier build's record")
    BR.main(start)
    assert not rec.exists() and json.loads((out / "lib.json.start").read_text()) == {
        "host/d/a.cpp": hashlib.sha256(b"a").hexdigest()}
    (tree / "host" / "d" / "a.cpp").write_text("edited mid-build")
    with pytest.raises(SystemExit, match="the sources changed while the build ran") as exc:
        BR.main(close)
    assert f"host/d/a.cpp: {hashlib.sha256(b'a').hexdigest()} at the start, " in str(exc.value.code)
    assert not rec.exists() and not (out / "lib.json.start").exists()
    with pytest.raises(SystemExit, match="no .*lib.json.start"):
        BR.main(close)
    assert not rec.exists()
    BR.main(start)
    (tree / "host" / "d" / "b.cpp").write_text("b")
    with pytest.raises(SystemExit, match="host/d/b.cpp: absent at the start"):
        BR.main(close)
    BR.main(start)
    BR.main(close)
    assert json.loads(rec.read_text())["sources"] == {"host/d/a.cpp": hashlib.sha256(b"edited mid-build").hexdigest(),
                                                       "host/d/b.cpp": hashlib.sha256(b"b").hexdigest()}
    assert not (out / "lib.json.start").exists()
    # The command line exits non-zero, as both build scripts need it to under set -e.
    BR.main(start)
    (tree / "host" / "d" / "a.cpp").write_text("edited again")
    r = subprocess.run([sys.executable, str(ROOT / "host" / "player" / "build_record.py"), *close],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "the sources changed while the build ran" in r.stderr and not rec.exists()


def test_both_build_scripts_hash_their_sources_before_they_compile():
    """Each build script starts each of its records before its first compile and closes it
    after its last; each player's record is closed from its compiles' depfiles (exact), each
    group record over the sources its start hashed; the four are CH.BUILD_RECORDS."""
    records = {"host/player/build.sh": ("sa2_player.json", "player_tools.json"),
               "host/d3d9gpu/build.sh": ("sa2_player_gpu.json", "d3d9gpu_tools.json")}
    assert sorted(r for rs in records.values() for r in rs) == sorted(CH.BUILD_RECORDS)
    for script, (player, group) in records.items():
        text = (ROOT / script).read_text()
        first_compile, last_compile = text.index("$CXX "), text.rindex("$CXX ")
        for record in (player, group):
            start = text.index(f'build_record.py" --start --out "$OUT/{record}"')
            close = text.index(f'build_record.py" --out "$OUT/{record}"')
            assert start < first_compile and last_compile < close, (script, record)
        close = text.index(f'build_record.py" --out "$OUT/{player}"')
        assert "--deps " in text[close:text.index("\n\"$PY\"", close)], script
        # the group record is closed over the sources its start hashed
        close = text.index(f'build_record.py" --out "$OUT/{group}"')
        source_list = "TOOL_SOURCES" if script == "host/d3d9gpu/build.sh" else "SOURCES"
        assert f'--sources "${{{source_list}[@]}}"' in text[close:text.index("\necho", close)], script
    # the DXVK player's build reads nothing of the second host's (outside its comments and
    # its check that the binary holds none of its names)
    text = (ROOT / "host/player/build.sh").read_text()
    text = text[:text.index("<<'EOF_NAMES'")] + text[text.index("\nEOF_NAMES\n"):]
    assert "d3d9gpu" not in "".join(line for line in text.splitlines(True) if not line.lstrip().startswith("#"))


def test_an_exact_record_is_what_the_compiler_read(tmp_path):
    """build_record.py's exact record (v2), from a real g++ -MMD: each path the depfile names
    classified (the build's own output generated, the tree's sources, the rest external with
    the compiler), the --sources files beside them, the depfile itself; refused when the
    compile read a file the start did not hash, or one that moved since."""
    if not GXX:
        pytest.fail("no g++")
    sys.path.insert(0, str(ROOT / "host" / "player"))
    import build_record as BR
    tree, out, ext = tmp_path / "tree", tmp_path / "tree" / "private" / "out", tmp_path / "install"
    (tree / "src").mkdir(parents=True)
    (tree / "private" / "inc").mkdir(parents=True)
    out.mkdir()
    ext.mkdir()
    (tree / "src" / "a b.h").write_text("#define A 1\n")       # an escaped space in the depfile
    (tree / "src" / "a.cpp").write_text('#include "a b.h"\n#include "gen.h"\n#include "ext.h"\n#include "priv.h"\n'
                                        "int main() { return A + G + E + P; }\n")
    (tree / "build.sh").write_text("# the build\n")
    (out / "gen.h").write_text("#define G 2\n")
    (ext / "ext.h").write_text("#define E 3\n")
    (tree / "private" / "inc" / "priv.h").write_text("#define P 4\n")
    dep = out / "a.d"
    subprocess.run([GXX, "-I", str(out), "-I", str(ext), "-I", str(tree / "private" / "inc"), "-MMD", "-MF", str(dep),
                    "-c", str(tree / "src" / "a.cpp"), "-o", str(out / "a.o")], check=True)
    subprocess.run([GXX, "-o", str(out / "a"), str(out / "a.o")], check=True)
    rec_path = out / "a.json"
    start = ["--start", "--out", str(rec_path), "--tree", str(tree), "--sources", str(tree / "src"),
             str(tree / "build.sh")]
    close = ["--out", str(rec_path), "--tree", str(tree), "--bin-dir", str(out), "--binaries", "a", "--deps", str(dep),
             "--compiler", GXX, "--sources", str(tree / "build.sh")]
    BR.main(start)
    BR.main(close)
    rec = json.loads(rec_path.read_text())
    h = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
    assert rec["schema"] == "sa2-build-record/v2"
    assert rec["sources"] == {"build.sh": h(tree / "build.sh"), "src/a b.h": h(tree / "src" / "a b.h"),
                              "src/a.cpp": h(tree / "src" / "a.cpp")}
    assert rec["generated"] == {"gen.h": h(out / "gen.h")}
    # outside the tree, and inside its private/: external, never a source
    assert rec["external"] == {str(ext / "ext.h"): h(ext / "ext.h"),
                               str(tree / "private" / "inc" / "priv.h"): h(tree / "private" / "inc" / "priv.h")}
    assert rec["depfiles"] == {"a.d": h(dep)} and rec["binaries"] == {"a": h(out / "a")}
    # the compiler as the build named it, verbatim (flags it carries included: the review's NIT 4),
    # with its own search list: the directory of the header `#include <cstdio>` opens among it
    assert rec["compiler"]["command"] == GXX and rec["compiler"]["version"].startswith("g++")
    assert BR.compiler_line(f"{GXX} -O0 -fno-inline")["command"] == f"{GXX} -O0 -fno-inline"
    assert os.path.dirname(compiler_opens("cstdio")) in rec["compiler"]["search"]
    # a compiler that gives no search list, or an empty one, makes no record
    fake = tmp_path / "fake-cxx"
    fake.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo "fake 1"; exit 0; fi\n'
                    'printf "#include <...> search starts here:\\nEnd of search list.\\n" >&2\n')
    fake.chmod(0o755)
    for cxx in ("true", str(fake)):
        assert BR.search_list(cxx) is None
        with pytest.raises(SystemExit, match="gives no include search list"):
            BR.compiler_line(cxx)
    # a compile that read what the start did not hash
    BR.main(["--start", "--out", str(rec_path), "--tree", str(tree), "--sources", str(tree / "build.sh")])
    with pytest.raises(SystemExit, match="the compile read src/a.cpp, which the build did not hash at its start"):
        BR.main(close)
    # a source that moved while the build ran
    BR.main(start)
    (tree / "src" / "a b.h").write_text("#define A 5\n")
    with pytest.raises(SystemExit, match="src/a b.h: .* at the start, .* at the end"):
        BR.main(close)
    assert not rec_path.exists()
    # an exact record names files, not directories
    BR.main(start)
    with pytest.raises(SystemExit, match="an exact record names files"):
        BR.main([*close[:-1], str(tree / "src")])
    # a checkout under the build's OUT: its sources would read as generated (the review's
    # finding 2: player.cpp recorded as generated, the record's sources build.sh alone)
    BR.main(start)
    with pytest.raises(SystemExit, match="the checkout .* is under the build's .*, where its sources would be "
                                         "recorded as generated"):
        BR.main([*close[:close.index("--bin-dir") + 1], str(tmp_path), *close[close.index("--bin-dir") + 2:]])


def test_an_exact_record_refuses_a_file_read_through_a_link_in_the_checkout(tmp_path):
    """The review's SHOULD-FIX 1, half (b): a header read through a symbolic link in the
    checkout (a directory link out of it, or a file link) was resolved, then recorded as
    external, which nothing holds. build_record.py refuses it, from a real g++ -MMD, and so
    do the --sources it names; a link in the checkout's private/ (a clone's link to the
    repository's) is not one, and depfiles_unrecorded names such a path in a record."""
    if not GXX:
        pytest.fail("no g++")
    sys.path.insert(0, str(ROOT / "host" / "player"))
    import build_record as BR
    tree, ext = tmp_path / "tree", tmp_path / "elsewhere"
    out = tree / "private" / "out"
    (tree / "src").mkdir(parents=True)
    (ext / "SDL3").mkdir(parents=True)
    (tree / "private").mkdir(exist_ok=True)
    out.mkdir()
    (ext / "SDL3" / "SDL.h").write_text("#define S 1\n")
    (ext / "inc").mkdir()
    (ext / "inc" / "priv.h").write_text("#define P 2\n")
    (tree / "src" / "own.h").write_text("#define O 3\n")
    os.symlink(ext / "SDL3", tree / "src" / "SDL3")              # a directory link out of the checkout
    os.symlink("own.h", tree / "src" / "linked.h")               # a file link inside it
    os.symlink(ext / "inc", tree / "private" / "inc")            # a link in its private/: allowed
    (tree / "build.sh").write_text("# the build\n")
    os.symlink("build.sh", tree / "build_link.sh")
    h = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731

    def build(src: str) -> Path:
        (tree / "src" / "a.cpp").write_text(src + "int main() { return 0; }\n")
        dep = out / "a.d"
        subprocess.run([GXX, "-I", str(tree / "src"), "-I", str(tree / "private" / "inc"), "-MMD", "-MF", str(dep),
                        "-c", str(tree / "src" / "a.cpp"), "-o", str(out / "a.o")], check=True)
        subprocess.run([GXX, "-o", str(out / "a"), str(out / "a.o")], check=True)
        return dep
    rec_path = out / "a.json"
    start = ["--start", "--out", str(rec_path), "--tree", str(tree), "--sources", str(tree / "src"),
             str(tree / "build.sh")]

    def close(dep: Path, *sources: Path) -> list[str]:
        return ["--out", str(rec_path), "--tree", str(tree), "--bin-dir", str(out), "--binaries", "a", "--deps",
                str(dep), "--compiler", GXX, "--sources", *map(str, sources or (tree / "build.sh",))]
    for src, link in (("#include <SDL3/SDL.h>\n", "src/SDL3"), ('#include "linked.h"\n', "src/linked.h")):
        dep = build(src)
        BR.main(start)
        with pytest.raises(SystemExit, match=f"refused, the compile read .* through the checkout's symbolic link "
                                             f"{link}: "):
            BR.main(close(dep))
        assert not rec_path.exists()
    dep = build('#include "own.h"\n#include "priv.h"\n')
    BR.main(start)
    with pytest.raises(SystemExit, match="refused, the build names .*build_link.sh through the checkout's symbolic "
                                         "link build_link.sh"):
        BR.main(close(dep, tree / "build_link.sh"))
    BR.main(start)
    BR.main(close(dep))
    rec = json.loads(rec_path.read_text())
    assert rec["external"] == {str(ext / "inc" / "priv.h"): h(ext / "inc" / "priv.h")}
    assert sorted(rec["sources"]) == ["build.sh", "src/a.cpp", "src/own.h"]
    # tree_links follows the path as the system does: `..` from where the path has led (a
    # link's target, or a directory), and on from where a link leads, a link above the
    # checkout into it included
    assert BR.tree_links(tree, tree / "src" / "SDL3" / ".." / "inc" / "priv.h") == ["src/SDL3"]
    assert BR.tree_links(tree, tree / "src" / ".." / "src" / "own.h") == []
    (tree / "src" / "sub").mkdir()
    assert BR.tree_links(tree, tree / "src" / "sub" / ".." / "linked.h") == ["src/linked.h"]
    os.symlink(tree / "src", ext / "tree_view")
    assert BR.tree_links(tree, ext / "tree_view" / "linked.h") == ["src/linked.h"]
    assert BR.tree_links(tree, ext / "tree_view" / "own.h") == []
    assert BR.tree_links(tree, tree / "private" / "inc" / "priv.h") == []
    # a record whose depfile names a path through such a link (a record build_record.py did
    # not write, or a link made since): depfiles_unrecorded names it
    (out / "b.d").write_text(f"b.o: {tree / 'src' / 'SDL3' / 'SDL.h'}\n")
    forged = dict(rec, external={**rec["external"], str(ext / "SDL3" / "SDL.h"): h(ext / "SDL3" / "SDL.h")},
                  depfiles={"a.d": h(out / "a.d"), "b.d": h(out / "b.d")})
    assert CH.depfiles_unrecorded(out, forged) == [
        f"depfile b.d names {tree / 'src' / 'SDL3' / 'SDL.h'}, read through the checkout's symbolic link src/SDL3, "
        f"which no record can hold"]
    assert CH.depfiles_unrecorded(out, rec) == []


@pytest.mark.parametrize("text,want", [
    ("o.o: a.c b.h \\\n c.h\n", ["a.c", "b.h", "c.h"]),
    ("o.o: a\\ b.h c$$d.h\n", ["a b.h", "c$d.h"]),
    ("o.o: a.c\nb.h:\n", ["a.c"]),
])
def test_a_depfile_is_read_as_make_reads_it(text, want):
    sys.path.insert(0, str(ROOT / "host" / "player"))
    import build_record as BR
    assert BR.depfile_paths(text) == want


def test_an_all_black_frame_is_named_by_its_hash():
    # boot's comparable frames (1280 x 720), every one of them black on both hosts
    assert CH.black_hash(1280, 720) == "0x4bd87a07a4536325"
    ev = evidence()
    black = CH.black_hash(2, 2)
    for host in ("dxvk", "sdl"):
        ev[host]["times"][1]["bgrx_hash"] = black
    e = verdicts(ev)["e"]
    assert e["pass"] is True
    assert e["detail"]["comparable_hashes"] == [{"bgrx_hash": black, "frames": 1, "black": True},
                                                {"bgrx_hash": "0xb", "frames": 1, "black": False}]
    assert e["detail"]["comparable_not_black"] == 1


# ------------------------------------------------------------------ dxvk-ab
def ab_run(report: dict, rows: list[dict]) -> dict:
    return {"report": report, "times": rows, "exit": 0, "check": {"pass": True}}


def test_dxvk_ab_ignores_wall_time_and_the_allowlist_only():
    before = player_report("dxvk")
    before["host_identity"].pop("d3d9_host")            # a player from before the switch
    after = copy.deepcopy(player_report("dxvk"))
    after.update(wall_s=9.9, t_start_monotonic_s=123.4, window={"log": ["x"]}, dxvk={"log": "/a", "log_sha256": "f"})
    ta, tb = times(["0x1", "0x2"]), times(["0x1", "0x2"])
    tb[1]["wall_us"] = "555555"
    res = CH.compare_dxvk_ab(ab_run(before, ta), ab_run(after, tb), before_expected_d3d9gpu=None)
    assert res["pass"] is True, res["failures"]
    assert res["split_fields_differing"] == ["host_identity.d3d9_host"]
    assert "host_identity.d3d9_host" not in res["report_fields_differing_inside_allowlist"]
    assert res["times_columns_compared"] == ["bgrx_hash", "frame", "guest_frames", "polls", "seq", "virtual_us"]


def test_dxvk_ab_takes_development_snapshot_s_player_as_the_before_by_its_split_fields():
    """The before of this change: development_snapshot's player wrote the second host's three fields with
    DXVK's answers, which the after (the DXVK player alone) does not write. They differ as
    split fields, each held to its side's values, and none is in the free allowlist."""
    before, after = development_snapshot_report(), player_report("dxvk")
    res = CH.compare_dxvk_ab(ab_run(before, times(["0x1"])), ab_run(after, times(["0x1"])), before_expected_d3d9gpu=OWN)
    assert (res["pass"], res["failures"]) == (True, [])
    assert res["split_fields_differing"] == ["files.libsa2_d3d9gpu", "host_identity.d3d9gpu_file_verified",
                                             "host_identity.expected_d3d9gpu_sha256"]
    assert res["report_fields_differing_inside_allowlist"] == [] == res["report_fields_differing_outside_allowlist"]
    assert res["split_fields"]["host_identity.d3d9_host"] == {"before": "dxvk", "after": "dxvk"}
    assert res["split_fields"]["host_identity.expected_d3d9gpu_sha256"] == {"before": OWN, "after": CH.ABSENT}
    assert not [k for k in CH.DXVK_AB_SPLIT if CH.allowed(k)] and not [p for p in CH.DXVK_AB_ALLOWED
                                                                       if "d3d9gpu" in p or "d3d9_host" in p]


@pytest.mark.parametrize("change,needle", [
    (lambda a, ta: ta[1].update(bgrx_hash="0x3"), "times: 1 row(s) differ outside wall_us, first at row 2"),
    (lambda a, ta: ta.pop(), "times rows: before 2, after 1"),
    (lambda a, ta: a.update(draws=7), "report draws: before 6, after 7"),
    (lambda a, ta: a["textures"].pop("png"), "report textures.png: before 0, after '<absent>'"),
    (lambda a, ta: a["host_identity"].update(dxvk_file_verified=False), "report host_identity.dxvk_file_verified"),
    # the split fields, fixed on the after side: the DXVK player names its host and writes
    # none of the second host's library fields, with any value (the review's finding 4)
    (lambda a, ta: a["host_identity"].update(d3d9_host="sdlgpu"),
     "after: host_identity.d3d9_host is 'sdlgpu', not 'dxvk'"),
    (lambda a, ta: a["host_identity"].pop("d3d9_host"), "after: host_identity.d3d9_host is '<absent>', not 'dxvk'"),
    (lambda a, ta: a["host_identity"].update(d3d9gpu_file_verified=False),
     "after: host_identity.d3d9gpu_file_verified is False, not '<absent>'"),
    (lambda a, ta: a["host_identity"].update(expected_d3d9gpu_sha256=OWN),
     f"after: host_identity.expected_d3d9gpu_sha256 is '{OWN}', not '<absent>'"),
    (lambda a, ta: a["files"].update(libsa2_d3d9gpu=None), "after: files.libsa2_d3d9gpu is None, not '<absent>'"),
    (lambda a, ta: a["files"].update(libsa2_d3d9gpu={"sha256": GPU_SHA}), "after: files.libsa2_d3d9gpu is {'sha256'"),
])
def test_dxvk_ab_fails_any_other_difference(change, needle):
    before, after = player_report("dxvk"), player_report("dxvk")
    ta, tb = times(["0x1", "0x2"]), times(["0x1", "0x2"])
    change(after, tb)
    res = CH.compare_dxvk_ab(ab_run(before, ta), ab_run(after, tb), before_expected_d3d9gpu=None)
    assert res["pass"] is False
    assert any(needle in f for f in res["failures"]), res["failures"]


@pytest.mark.parametrize("change,own,want", [
    (lambda r: r["host_identity"].update(d3d9_host="sdlgpu"), OWN,
     "before: host_identity.d3d9_host is 'sdlgpu', not '<absent>' or 'dxvk'"),
    (lambda r: r["host_identity"].update(d3d9gpu_file_verified=True), OWN,
     "before: host_identity.d3d9gpu_file_verified is True, not '<absent>' or False"),
    # (0 is not False: JSON's types are held)
    (lambda r: r["host_identity"].update(d3d9gpu_file_verified=0), OWN,
     "before: host_identity.d3d9gpu_file_verified is 0, not '<absent>' or False"),
    (lambda r: r["files"].update(libsa2_d3d9gpu={"sha256": GPU_SHA}), OWN,
     "before: files.libsa2_d3d9gpu is {'sha256': "),
    # the before's expected library is its own build's, not the after's (the coupling removed)
    (lambda r: r["host_identity"].update(expected_d3d9gpu_sha256=GPU_SHA), OWN,
     f"before: host_identity.expected_d3d9gpu_sha256 is '{GPU_SHA}', not '<absent>' or '{OWN}'"),
    # ... and a before whose own contract has none may not carry one
    (lambda r: None, None, f"before: host_identity.expected_d3d9gpu_sha256 is '{OWN}', not '<absent>'"),
])
def test_dxvk_ab_holds_the_before_side_s_split_fields_to_its_values(change, own, want):
    """A before player that writes the second host's fields gives DXVK's answers in them,
    its expected library its own contract's: a split field's presence on one side is held,
    not excused."""
    before, after = development_snapshot_report(), player_report("dxvk")
    change(before)
    res = CH.compare_dxvk_ab(ab_run(before, times(["0x1", "0x2"])), ab_run(after, times(["0x1", "0x2"])),
                             before_expected_d3d9gpu=own)
    assert len(res["failures"]) == 1 and res["failures"][0].startswith(want), res["failures"]


# dxvk-ab's sides as test_d3d9gpu.sh step 7 runs them against development_snapshot: the before from its clone.
BEFORE_CHECKOUT, AFTER_CHECKOUT = Path("/c/before-clone"), Path("/c/tree")
OWN_LIBRARIES = {"before": CH.own_library(BEFORE_CHECKOUT), "after": CH.own_library(AFTER_CHECKOUT)}


def side_report(checkout: Path, *, end_request: bool = True, lib: str | None = None) -> dict:
    """The DXVK player's report of a run under `checkout`'s player_env.sh: its own install's
    DXVK library (or `lib`) in the three fields that name it, and the end_request block a run
    without --end-fd writes (none of it with end_request false: development_snapshot's player)."""
    rep = player_report("dxvk") if end_request else without_end_request()
    path = lib or str(checkout / CH.DXVK_LIBRARY)
    rep["env"] = {"SA2_DXVK_D3D9": path, "DXVK_WSI_DRIVER": "SDL3", "DXVK_LOG_LEVEL": "info"}
    rep["files"]["libdxvk_d3d9"].update(path=path, real_path=path)
    return rep


def ab_pair(before: dict, after: dict, own_libraries: dict | None = OWN_LIBRARIES) -> dict:
    return CH.compare_dxvk_ab(ab_run(before, times(["0x1", "0x2"])), ab_run(after, times(["0x1", "0x2"])),
                              before_expected_d3d9gpu=None, own_libraries=own_libraries)


def test_dxvk_ab_takes_development_snapshot_s_player_as_the_before():
    """test_d3d9gpu.sh step 7's pair: the before (development_snapshot's player, run from its clone) writes
    no end_request and names its clone's install's DXVK library; the after writes end_request
    as a run without --end-fd does and names this tree's. Both rules held, nothing else
    differs and the pair passes; a before that writes the block writes the after's."""
    res = ab_pair(side_report(BEFORE_CHECKOUT, end_request=False), side_report(AFTER_CHECKOUT))
    assert (res["pass"], res["failures"]) == (True, [])
    assert res["split_fields_differing"] == ["end_request"]
    assert res["split_fields"]["end_request"] == {"before": CH.ABSENT, "after": CH.END_REQUEST_UNUSED}
    assert res["own_library"]["fields"] == dict.fromkeys(("before", "after"), LIBRARY_FIELDS_NAMED)
    assert res["report_fields_differing_outside_allowlist"] == [] == res["report_fields_differing_inside_allowlist"]
    res = ab_pair(side_report(BEFORE_CHECKOUT), side_report(AFTER_CHECKOUT))
    assert (res["pass"], res["split_fields_differing"]) == (True, [])
    # the block as the player writes it (host/player/player.cpp end_request_json, no --end-fd)
    assert CH.END_REQUEST_UNUSED == json.loads('{"fd": false, "requested": false, "by": null, "after_frame": null, '
                                               '"at_s": null, "written": false, "closes_after": 0, "error": null}')


@pytest.mark.parametrize("removed,needles", [
    # without the end_request split rule, each of the block's eight fields differs
    ("end_request", [f"report end_request.{k}: before '<absent>', after {v!r}"
                     for k, v in CH.END_REQUEST_UNUSED.items()]),
    # without each side's own library, the three fields that name it differ
    ("own_library", [f"report {k}: before '{BEFORE_CHECKOUT / CH.DXVK_LIBRARY}', after '{AFTER_CHECKOUT / CH.DXVK_LIBRARY}'"
                     for k in LIBRARY_FIELDS_NAMED]),
])
def test_dxvk_ab_fails_development_snapshot_s_pair_without_each_rule(monkeypatch, removed, needles):
    """Each rule is what passes the pair: removed, the pair fails on exactly its fields."""
    libraries = OWN_LIBRARIES
    if removed == "end_request":
        monkeypatch.setattr(CH, "DXVK_AB_SPLIT", {k: v for k, v in CH.DXVK_AB_SPLIT.items() if k != "end_request"})
    else:
        libraries = None
    res = ab_pair(side_report(BEFORE_CHECKOUT, end_request=False), side_report(AFTER_CHECKOUT), libraries)
    assert res["pass"] is False
    assert sorted(res["failures"]) == sorted(needles), res["failures"]


@pytest.mark.parametrize("side,change", [
    ("after", lambda r: r.pop("end_request")),
    ("after", lambda r: r.update(end_request=None)),
    ("after", lambda r: r["end_request"].update(fd=True)),
    ("after", lambda r: r["end_request"].update(requested=True, by="the window's close", after_frame=12, at_s=1.5,
                                                written=True)),
    ("after", lambda r: r["end_request"].update(closes_after=1)),
    ("after", lambda r: r["end_request"].update(error="write: short")),
    ("after", lambda r: r["end_request"].pop("error")),
    ("after", lambda r: r["end_request"].update(extra=None)),
    # JSON's types are held inside the block (False is not 0, 0 is not False or 0.0)
    ("after", lambda r: r["end_request"].update(closes_after=False)),
    ("after", lambda r: r["end_request"].update(written=0)),
    ("before", lambda r: r["end_request"].update(closes_after=0.0)),
    ("before", lambda r: r["end_request"].update(requested=True)),
    ("before", lambda r: r.update(end_request={})),
    ("before", lambda r: r.update(end_request=None)),
])
def test_dxvk_ab_holds_end_request_on_each_side(side, change):
    reports = {"before": side_report(BEFORE_CHECKOUT), "after": side_report(AFTER_CHECKOUT)}
    change(reports[side])
    res = ab_pair(reports["before"], reports["after"])
    assert res["pass"] is False
    assert len(res["failures"]) == 1 and res["failures"][0].startswith(f"{side}: end_request is "), res["failures"]


@pytest.mark.parametrize("side,lib", [
    ("before", str(AFTER_CHECKOUT / CH.DXVK_LIBRARY)),          # the after's install, not its clone's
    ("after", str(BEFORE_CHECKOUT / CH.DXVK_LIBRARY)),          # the before's clone's, not this tree's
    ("before", "/elsewhere/libdxvk_d3d9.so.0"),
    ("after", "/elsewhere/libdxvk_d3d9.so.0"),
    ("after", str(AFTER_CHECKOUT / "private/s5-tools/install/lib/libdxvk_d3d9.so.0")),
])
def test_dxvk_ab_holds_each_side_s_library_to_its_own_install(side, lib):
    """Each side's library path is held to its own install, never ignored: any other path
    differs in each field that names it."""
    reports = {"before": side_report(BEFORE_CHECKOUT), "after": side_report(AFTER_CHECKOUT)}
    reports[side] = side_report(BEFORE_CHECKOUT if side == "before" else AFTER_CHECKOUT, lib=lib)
    res = ab_pair(reports["before"], reports["after"])
    assert res["pass"] is False
    assert sorted(f.split(":")[0] for f in res["failures"]) == [f"report {k}" for k in LIBRARY_FIELDS_NAMED]
    assert res["own_library"]["fields"][side] == []


def perturbed(value):
    """A value of a report leaf that is not `value`."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    if value is None:
        return "x"
    return [0] if isinstance(value, list) else {"x": 0}


def leaf_edits(d, prefix=""):
    """(flattened key, setter, deleter) for every leaf of `d`, keyed as CH.flatten keys it."""
    items = d.items() if isinstance(d, dict) else enumerate(d)
    for k, v in list(items):
        key = (f"{prefix}.{k}" if prefix else str(k)) if isinstance(d, dict) else f"{prefix}[{k}]"
        if isinstance(v, (dict, list)) and v:
            yield from leaf_edits(v, key)
        else:
            yield key, functools.partial(d.__setitem__, k, perturbed(v)), \
                (functools.partial(d.pop, k) if isinstance(d, dict) else None)


@pytest.mark.parametrize("side", ["before", "after"])
def test_dxvk_ab_fails_a_difference_in_every_other_report_field(side):
    """With both rules in place, every leaf of the report outside DXVK_AB_ALLOWED, the split
    fields and the fields naming the side's own library, changed or removed on either side,
    fails the pair, by its name (the library's bytes among them)."""
    def pair():
        return {"before": side_report(BEFORE_CHECKOUT, end_request=False), "after": side_report(AFTER_CHECKOUT)}
    keys = [k for k, _, _ in leaf_edits(pair()[side])
            if not CH.allowed(k) and CH.split_key(k) is None and k not in LIBRARY_FIELDS_NAMED]
    assert "files.libdxvk_d3d9.sha256" in keys and "env.DXVK_LOG_LEVEL" in keys and len(keys) > 50
    for i, key in enumerate(keys):
        for how in (1, 2):
            reports = pair()
            edit = next(e for e in leaf_edits(reports[side]) if e[0] == key)
            if how == 2 and edit[2] is None:
                continue
            edit[how]()
            res = ab_pair(reports["before"], reports["after"])
            assert res["pass"] is False and any(f.startswith(f"report {key}:") for f in res["failures"]), \
                (key, how, res["failures"])


def test_flatten_names_every_leaf():
    assert CH.flatten({"a": {"b": [1, {"c": None}], "e": {}}, "f": []}) == \
        {"a.b[0]": 1, "a.b[1].c": None, "a.e": {}, "f": []}
    assert CH.allowed("window.flags_at_present[3]") and CH.allowed("host.window_end.hidden")
    assert not CH.allowed("windows") and not CH.allowed("draws")


def test_dxvk_s_device_is_read_from_its_log(tmp_path):
    (tmp_path / "sa2_player_d3d9.log").write_text(
        "info:  Found device: NVIDIA GeForce RTX 5090 (NVIDIA 595.91.7)\n"
        "info:  Found device: llvmpipe (LLVM 19.1.7, 256 bits) (llvmpipe 0.0.1)\n"
        "info:  Creating device:\ninfo:  NVIDIA GeForce RTX 5090:\ninfo:    Driver   : NVIDIA 595.91.7\n"
        "info:  Queues:\n")
    d = CH.dxvk_device(tmp_path, "sa2_player_d3d9.log")
    assert d == {"device_name": "NVIDIA GeForce RTX 5090", "driver": "NVIDIA 595.91.7",
                 "found_devices": ["NVIDIA GeForce RTX 5090 (NVIDIA 595.91.7)",
                                   "llvmpipe (LLVM 19.1.7, 256 bits) (llvmpipe 0.0.1)"]}


# ------------------------------------------------------------------ receipt
def compare_doc(tmp_path, controls_ok: bool = True, black: bool = False, no_matrix: bool = False) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "census.json").write_text(json.dumps(census()))
    stream = tmp_path / "s.sa2s"
    stream.write_bytes(b"SA2D3D9S")
    build = forged_build(tmp_path / "bin")
    args = argparse.Namespace(scope="r2a-clears", build=str(build), stream=str(stream), hidden=False, reference=None)
    ev = evidence()
    if black:
        for host in ("dxvk", "sdl"):
            for fr in (1, 2):
                ev[host]["times"][fr]["bgrx_hash"] = CH.black_hash(2, 2)
    ev["sdl"]["device"]["fault"] = "present-scribble"
    ctl = [CH.control_outcome(control("present-scribble"), ev)]
    assert ctl[0]["outcome"] == "as required"
    ev["sdl"]["device"]["fault"] = None
    ctl[0]["as_required"] = controls_ok
    return CH.assemble(tmp_path, args, census(), ev["dxvk"], ev["sdl"], ctl, "t",
                       matrix=None if no_matrix else CH.load_matrix(write_matrix(tmp_path, build)))


def test_the_receipt_holds_its_schema_and_no_more(tmp_path):
    rec = CH.build_receipt(compare_doc(tmp_path), "private/x", receipt_id=RID)
    assert set(rec) == CH.RECEIPT_KEYS
    assert (rec["schema"], rec["id"]) == ("sa2-s5-host2/v2", RID)
    assert rec["marker"] == "R2A_CLEARS_OK" and rec["acceptance_scope"] == "r2a-clears"
    assert rec["answer"] == "yes" and rec["verdict"] == "PASS"
    assert rec["comparable_frames"] == [[2, 3]] and rec["counts"]["comparable"] == 2
    assert rec["counts"]["comparable_hashes"] == [{"bgrx_hash": "0xb", "frames": 2, "black": False}]
    assert set(rec["runs"]) == {"dxvk", "sdlgpu"}
    assert "live_desktop_session_basis" in rec["runs"]["sdlgpu"] and "dir" not in rec["runs"]["sdlgpu"]
    pins = rec["inputs"]["pipeline"]
    assert all(len(v) == 64 for v in pins.values()) and "host/player/player.cpp" in pins
    assert rec["inputs"]["ran_by"] == pins[CH.SELF] == CH.RAN_BY
    assert sorted(rec["inputs"]["build_records"]) == sorted(CH.BUILD_RECORDS)
    assert rec["inputs"]["binaries"]["sa2_player.json"] == {"sa2_player": PLAYER_SHA}
    assert rec["inputs"]["binaries"]["sa2_player_gpu.json"] == {"sa2_player_gpu": GPU_SHA}
    assert rec["inputs"]["files"] == {"sa2_player": PLAYER_SHA, "sa2_player_gpu": GPU_SHA,
                                      "sa2_frame_census": hashlib.sha256(BYTES["sa2_frame_census"]).hexdigest(),
                                      "libdxvk_d3d9": "d" * 64, "libSDL3": None, "dxvk_conf": "e" * 64}
    assert "SDL_GPUVulkanOptions" in rec["scope"] and "other_runs" not in rec["scope"]
    # what the comparable frames cannot show, and the clear matrix that holds it
    assert "a frame cleared whole in one colour exercises the colour" in rec["scope"]
    assert rec["clear_matrix"] == {
        "pass": True, "failures": [], "cases": 2, "names": ["full", "rect_list"],
        "outputs": dict.fromkeys(CH.MATRIX_HOSTS, hashlib.sha256(MATRIX_TSV.encode()).hexdigest()),
        "dxvk_library": "d" * 64, "library": GPU_SHA,
        "binaries": {b: hashlib.sha256(BYTES[b]).hexdigest() for b in CH.MATRIX_BINARIES}}
    assert len(json.dumps(rec)) < 200_000


RID = "s5-host2-test-v1"


def receipt(*args: str) -> int:
    """compare_hosts.py receipt under a test id, the --out it names removed first (a receipt
    is written once: an existing --out is refused, test_the_receipt_is_written_once)."""
    a = list(args)
    Path(a[a.index("--out") + 1]).unlink(missing_ok=True)
    return CH.main(["receipt", "--id", RID, *a])


def test_the_receipt_is_written_once_under_the_id_it_is_given(tmp_path, capsys):
    """The review's finding 4: the receipt wrote R2a's id whatever ran, and over an existing
    file. Its id is the caller's (no default), of a receipt id's form; an existing --out,
    and an id not of that form, are refused before anything is written."""
    run = write_doc(tmp_path / "run", compare_doc(tmp_path / "run"))
    out = tmp_path / "r.json"
    assert CH.main(["receipt", "--id", "s5-host2-r2b-v1", "--run", str(run), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["id"] == "s5-host2-r2b-v1"
    before = out.read_bytes()
    assert CH.main(["receipt", "--id", "s5-host2-r2b-v2", "--run", str(run), "--out", str(out)]) == 2
    assert f"refused: {out} exists" in capsys.readouterr().err and out.read_bytes() == before
    for bad in ("", "host2", "s5-host2-r2b", "s5-Host2-v1", "../s5-x-v1"):
        assert CH.main(["receipt", "--id", bad, "--run", str(run), "--out", str(tmp_path / "n.json")]) == 2, bad
        assert "is not a receipt id" in capsys.readouterr().err and not (tmp_path / "n.json").exists()
    with pytest.raises(SystemExit):
        CH.main(["receipt", "--run", str(run), "--out", str(tmp_path / "n.json")])


def write_doc(d: Path, doc: dict) -> Path:
    d.mkdir(exist_ok=True)
    (d / "compare.json").write_text(json.dumps(doc))
    return d


def test_the_receipt_is_refused_for_a_run_whose_controls_did_not_all_behave(tmp_path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    write_doc(run, compare_doc(run, controls_ok=False))
    out = tmp_path / "r.json"
    assert receipt("--run", str(run), "--out", str(out)) == 1
    assert not out.exists()
    assert "verdict with controls is FAIL" in capsys.readouterr().err
    write_doc(run, compare_doc(run, controls_ok=True))
    assert receipt("--run", str(run), "--out", str(out)) == 0
    assert json.loads(out.read_text())["verdict"] == "PASS"
    # Another stream's run joins the receipt only when the same pipeline and binaries made it,
    # and its failure fails the receipt.
    other = tmp_path / "other"
    other.mkdir()
    doc = compare_doc(other)
    doc["verdict"] = "FAIL"
    write_doc(other, doc)
    assert receipt("--run", str(run), "--out", str(out), "--also", str(other)) == 0
    rec = json.loads(out.read_text())
    assert (rec["verdict"], rec["answer"], len(rec["other_runs"])) == ("FAIL", "no", 1)
    assert "rests on the controls of this receipt's run" in rec["scope"]
    doc["inputs"]["pipeline"]["host/d3d9gpu/d9g_clear.cpp"] = "0" * 64
    write_doc(other, doc)
    assert receipt("--run", str(run), "--out", str(out), "--also", str(other)) == 2
    assert "was compared by another pipeline" in capsys.readouterr().err
    # an --also run whose runs another compare_hosts.py made than the one it pins
    doc = compare_doc(other)
    doc["inputs"]["ran_by"] = "0" * 64
    write_doc(other, doc)
    assert receipt("--run", str(run), "--out", str(out), "--also", str(other)) == 2
    assert "its runs were made by" in capsys.readouterr().err
    # each of the three binaries, the two players as well as the census
    for name in ("sa2_frame_census", "sa2_player", "sa2_player_gpu"):
        doc = compare_doc(other)
        doc["inputs"]["files"][name] = "0" * 64
        write_doc(other, doc)
        assert receipt("--run", str(run), "--out", str(out), "--also", str(other)) == 2, name
        assert "ran other binaries" in capsys.readouterr().err


def test_an_other_run_whose_own_control_misbehaved_fails_the_receipt(tmp_path):
    run, other = write_doc(tmp_path / "run", compare_doc(tmp_path / "run")), tmp_path / "other"
    doc = compare_doc(other, controls_ok=False)
    doc["controls"]["results"][0]["outcome"] = "NOT as required"
    write_doc(other, doc)
    out = tmp_path / "r.json"
    assert receipt("--run", str(run), "--out", str(out), "--also", str(other)) == 0
    rec = json.loads(out.read_text())
    assert (rec["verdict"], rec["other_runs"][0]["verdict"]) == ("FAIL", "FAIL")
    assert rec["other_runs"][0]["controls"][0]["outcome"] == "NOT as required"


def test_the_receipt_is_refused_for_a_build_not_held_to_the_tree(tmp_path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    doc = compare_doc(run)
    doc["inputs"]["provenance"] = {"pass": False, "failures": ["d3d9gpu_tools.json: source x"]}
    write_doc(run, doc)
    assert receipt("--run", str(run), "--out", str(tmp_path / "r.json")) == 2
    assert "its build was not held to the tree it pins" in capsys.readouterr().err
    doc["inputs"].pop("provenance")      # a compare.json from before the build was held
    write_doc(run, doc)
    assert receipt("--run", str(run), "--out", str(tmp_path / "r.json")) == 2


def test_the_receipt_is_refused_for_a_run_without_the_clear_matrix(tmp_path, capsys):
    run = write_doc(tmp_path / "run", compare_doc(tmp_path / "run", no_matrix=True))
    out = tmp_path / "r.json"
    assert receipt("--run", str(run), "--out", str(out)) == 2
    assert not out.exists() and "ran no clear matrix (check i)" in capsys.readouterr().err


def test_a_missing_or_foreign_also_run_is_refused_as_the_main_run_is(tmp_path, capsys):
    run = write_doc(tmp_path / "run", compare_doc(tmp_path / "run"))
    out = tmp_path / "r.json"
    assert receipt("--run", str(tmp_path / "nothing"), "--out", str(out)) == 2
    assert receipt("--run", str(run), "--out", str(out), "--also", str(tmp_path / "nothing")) == 2
    other = write_doc(tmp_path / "other", {"schema": "sa2-s5-dxvk-ab/v1"})
    assert receipt("--run", str(run), "--out", str(out), "--also", str(other)) == 2
    assert capsys.readouterr().err.count(f"no compare.json of schema {CH.SCHEMA}") == 3 and not out.exists()


def test_a_receipt_whose_every_comparable_frame_is_black_is_refused(tmp_path, capsys):
    run = write_doc(tmp_path / "run", compare_doc(tmp_path / "run", black=True))
    out = tmp_path / "r.json"
    assert receipt("--run", str(run), "--out", str(out)) == 1
    err = capsys.readouterr().err
    assert not out.exists() and "every comparable frame of every run is black" in err
    assert "colour" in err and "channel" not in err
    # another run whose comparable frames are not black (the synthetic stream's) carries it
    other = write_doc(tmp_path / "other", compare_doc(tmp_path / "other"))
    assert receipt("--run", str(run), "--out", str(out), "--also", str(other)) == 0
    rec = json.loads(out.read_text())
    assert (rec["counts"]["comparable_not_black"], rec["other_runs"][0]["counts"]["comparable_not_black"]) == (0, 2)


# ------------------------------------------------------------------ the census
HEADER = {"interfaces": ["IDirect3D9", "IDirect3DDevice9", "IDirect3DTexture9"] + [f"type{i}" for i in range(3, 13)] + ["IDirect3DSwapChain9"], "schema": "sa2-d3d9-stream/v1", "writer": "test_s5_compare_hosts.py",
          "methods": [[0, 16, "CreateDevice", 6], [1, 17, "Present", 4], [1, 23, "CreateTexture", 8],
                      [1, 37, "SetRenderTarget", 2], [1, 39, "SetDepthStencilSurface", 1], [1, 43, "Clear", 6],
                      [1, 47, "SetViewport", 1], [1, 57, "SetRenderState", 2], [1, 75, "SetScissorRect", 1],
                      [1, 81, "DrawPrimitive", 3], [1, 82, "DrawIndexedPrimitive", 6],
                      [1, 83, "DrawPrimitiveUP", 4], [1, 84, "DrawIndexedPrimitiveUP", 8],
                      [1, 86, "CreateVertexDeclaration", 2],
                      [1, 87, "SetVertexDeclaration", 1], [2, 18, "GetSurfaceLevel", 2], [13, 3, "Present", 5]]}
SYNTH_INTERFACES = ["IDirect3D9", "Device", "Texture", "CubeTexture", "VolumeTexture", "Surface", "Volume",
                    "VertexBuffer", "IndexBuffer", "VertexDeclaration", "VertexShader", "PixelShader",
                    "StateBlock", "SwapChain", "Query", "ID3DXConstantTable"]
D3D9, DEV, BB, DS, DECL, TEX, LVL = 0x03200000, 0x03200010, 0x03200020, 0x03200030, 0x03200040, 0x03200050, 0x03200060
IN, OUT, DESC = 1, 2, 5


def pad4(n: int) -> int:
    return (n + 3) & ~3


def blobs(items) -> bytes:
    out = struct.pack("<I", len(items))
    for role, arg, data in items:
        out += struct.pack("<HHI", role, arg, len(data)) + data + b"\0" * (pad4(len(data)) - len(data))
    return out


def call(t, slot, args, items=(), this=DEV) -> tuple[int, bytes]:
    return (1, struct.pack("<HHIII", t, slot, this, 0, len(args)) + struct.pack(f"<{len(args)}I", *args)
            + struct.pack("<I", 0) + blobs(list(items)))


def stream(records, path: Path, *, header=None) -> Path:
    head = json.dumps(HEADER if header is None else header).encode()
    hb = 16 + pad4(len(head))
    out = bytearray(b"SA2D3D9S" + struct.pack("<II", 1, hb) + head + b" " * (hb - 16 - len(head)))
    for i, (kind, payload) in enumerate(records):
        out += struct.pack("<III", kind, 12 + len(payload), i) + payload
    path.write_bytes(bytes(out))
    return path


def device(auto_ds: bool = True):
    pp = struct.pack("<14I", 1280, 720, 21, 1, 0, 0, 1, 0, 1, int(auto_ds), 75, 0, 0, 0)
    return call(0, 16, [0, 1, 0, 0x40, 0x03004000, 0x03004100], this=D3D9,
                items=[(IN, 4, pp), (OUT, 5, struct.pack("<I", DEV)), (DESC, 0xFFFF, struct.pack("<3I", DEV, BB, DS))])


def present(n: int):
    return [call(1, 17, [0, 0, 0, 0]), (3, struct.pack("<IIII", 16666 * n, 0, n, 0))]


def clear(flags=1, count=0, rects=None, colour=0xFF000000):
    items = [(IN, 1, b"".join(struct.pack("<4i", *r) for r in rects))] if rects else []
    return call(1, 43, [count, 0x0300F000 if rects else 0, flags, colour, 0x3F800000, 0], items=items)


def viewport(x, y, w, h):
    return call(1, 47, [0x0300E000], items=[(IN, 0, struct.pack("<4I2f", x, y, w, h, 0.0, 1.0))])


def decl():
    elems = struct.pack("<HHBBBB", 0, 0, 2, 0, 0, 0) + struct.pack("<HHBBBB", 0xFF, 0, 17, 0, 0, 0)
    return [call(1, 86, [0x0300D000, 0x0300D100], items=[(IN, 0, elems), (OUT, 1, struct.pack("<I", DECL))]),
            call(1, 87, [DECL])]


def up(prims, ptype=5, stride=16):
    n = {1: prims, 2: 2 * prims, 3: prims + 1, 4: 3 * prims, 5: prims + 2, 6: prims + 2}.get(ptype, 0)
    return call(1, 83, [ptype, prims, 0x0300C000, stride], items=[(IN, 2, b"\0" * (n * stride))])


def dp(ptype, prims):
    """DrawPrimitive (Type, StartVertex, PrimitiveCount)."""
    return call(1, 81, [ptype, 0, prims])


def dip(ptype, vertices, prims):
    """DrawIndexedPrimitive (Type, BaseVertexIndex, MinVertexIndex, NumVertices, StartIndex, primCount)."""
    return call(1, 82, [ptype, 0, 0, vertices, 0, prims])


def dipup(prims, ptype=4, min_v=0, vertices=3, fmt=101, stride=16, cut=(0, 0), omit=()):
    """DrawIndexedPrimitiveUP (Type, MinVertexIndex, NumVertices, PrimitiveCount, pIndexData,
    IndexDataFormat, pVertexStreamZeroData, VertexStreamZeroStride), its indices and vertices
    (`cut` bytes short of what the arguments name, indices first; the IN blobs numbered in
    `omit` left out)."""
    n = {1: prims, 2: 2 * prims, 3: prims + 1, 4: 3 * prims, 5: prims + 2, 6: prims + 2}.get(ptype, 0)
    isz = 2 if fmt == 101 else 4
    items = [(IN, 4, b"\0" * (n * isz - cut[0])), (IN, 6, b"\0" * ((min_v + vertices) * stride - cut[1]))]
    return call(1, 84, [ptype, min_v, vertices, prims, 0x0300A000, fmt, 0x0300A100, stride],
                items=[it for it in items if it[1] not in omit])


def account_header(synthetic: bool) -> dict:
    head = copy.deepcopy(HEADER)
    if synthetic:
        head["interfaces"] = list(SYNTH_INTERFACES)
    return head


def test_stream_account_fixture_names_are_the_actual_synthetic_producer_contract():
    names = (ROOT / "host/player/names.h").read_text().split("n[T_COUNT] = {", 1)[1].split("};", 1)[0]
    assert re.findall(r'"([^"]+)"', names) == SYNTH_INTERFACES
    producer = (ROOT / "host/player/synth_stream.cpp").read_text()
    assert 'for (uint32_t t = 0; t < 16; t++)' in producer
    assert 'names::type_name(t)' in producer


@pytest.mark.parametrize("synthetic", [False, True], ids=["recorder", "synthetic"])
@pytest.mark.parametrize("swapchain", [False, True], ids=["device-present", "swapchain-present"])
def test_stream_account_names_count_literal_draws_clears_and_presents(tmp_path, synthetic, swapchain):
    end_frame = ([call(13, 3, [0, 0, 0, 0, 0]), present(1)[1]] if swapchain else present(1))
    s = stream([device(), *decl(), clear(), dp(1, 1), dip(4, 3, 1), up(1, ptype=1), dipup(1),
                *end_frame, (5, b"")], tmp_path / "s.sa2s", header=account_header(synthetic))
    original = s.read_bytes()
    account = CH.stream_account(s)
    assert account["frames"] == 1
    assert account["clears"] == {"host_calls": 1}
    assert account["draws"] == {"total": 4, "skipped_no_shader_route": {
        "DrawPrimitive": {"POINTLIST": 1}, "DrawIndexedPrimitive": {"TRIANGLELIST": 1},
        "DrawPrimitiveUP": {"POINTLIST": 1}, "DrawIndexedPrimitiveUP": {"TRIANGLELIST": 1}},
        "refused": {}, "empty": {}, "per_frame": [[1, 1, 4]], "after_last_present": 0}
    assert account["accepted_per_frame"] == [[1, 1, 4]]
    assert account["first_accepted"] == {"method": "DrawPrimitive", "primitive": "POINTLIST", "frame": 1, "seq": 4}
    assert {key: account[key] for key in ("up_vertex_bytes", "up_index_bytes", "up_arena_bytes", "up_pad_bytes",
                                        "up_draw_uploaded_bytes", "up_draws")} == {
        "up_vertex_bytes": 64, "up_index_bytes": 6, "up_arena_bytes": 118, "up_pad_bytes": 48,
        "up_draw_uploaded_bytes": 70, "up_draws": 2}
    assert account["consumed_records"] == {"call": 9, "d3dx": 0, "frame": 1, "note": 0,
                                           "end": 1, "unknown": 0, "seq_gaps": 0, "after_end": 0}
    assert account["consumed_bytes"] == len(original) and s.read_bytes() == original
    partial = CH.stream_account(s, max_frames=1)
    assert partial["draws"] == account["draws"]
    assert partial["consumed_records"]["end"] == 0
    assert partial["consumed_bytes"] == len(original) - 12


@pytest.mark.parametrize("synthetic", [False, True], ids=["recorder", "synthetic"])
@pytest.mark.parametrize("fault,reason", [
    ("schema", "schema"), ("interfaces", "interfaces are not a list of names"),
    ("method-row", "method row"), ("missing-present", "argc differs"), ("argc", "argc differs"),
    ("orphan-frame", "FRAME has no preceding Present"), ("seq", "framing fails"),
    ("truncated", "truncated record"), ("tail", "tail after END"), ("missing-end", "logical END")])
def test_stream_account_names_preserve_malformed_stream_rejection(tmp_path, synthetic, fault, reason):
    head = account_header(synthetic)
    records = [device(), *decl(), up(1), *present(1), (5, b"")]
    if fault == "schema":
        head["schema"] = "sa2-d3d9-stream/v9"
    elif fault == "interfaces":
        head["interfaces"][1] = None
    elif fault == "method-row":
        head["methods"][0][1] = "16"
    elif fault == "missing-present":
        head["methods"] = [row for row in head["methods"] if row[:2] != [1, 17]]
    elif fault == "argc":
        next(row for row in head["methods"] if row[:2] == [1, 17])[3] = 3
    elif fault == "orphan-frame":
        del records[-3]
    elif fault == "missing-end":
        records.pop()
    s = stream(records, tmp_path / "bad.sa2s", header=head)
    raw = bytearray(s.read_bytes())
    if fault == "seq":
        struct.pack_into("<I", raw, struct.unpack_from("<I", raw, 12)[0] + 8, 99)
    elif fault == "truncated":
        raw = raw[:-1]
    elif fault == "tail":
        raw += struct.pack("<III", 5, 12, len(records))
    s.write_bytes(raw)
    with pytest.raises((ValueError, CH.stream_dump.StreamError), match=reason):
        CH.stream_account(s)


@pytest.mark.parametrize("name", ["device", "DeviceX", "IDirect3DDevice9X", "swapchain", "SwapChainX",
                                  "IDirect3DSwapChain9X"])
def test_stream_account_names_do_not_accept_near_aliases(tmp_path, name):
    head = account_header(True)
    swapchain = "wap" in name
    head["interfaces"][13 if swapchain else 1] = name
    end_frame = ([call(13, 3, [0, 0, 0, 0, 0]), present(1)[1]] if swapchain else present(1))
    s = stream([device(), *decl(), up(1), *end_frame, (5, b"")], tmp_path / "bad.sa2s", header=head)
    with pytest.raises(ValueError, match="FRAME has no preceding Present"):
        CH.stream_account(s)


@pytest.mark.parametrize("synthetic", [False, True], ids=["recorder", "synthetic"])
def test_stream_account_names_preserve_refused_empty_and_short_draws(tmp_path, synthetic):
    s = stream([device(), up(1), *decl(), dp(1, 0), dip(4, 0, 1), dipup(1, cut=(1, 0)),
                dipup(1, omit=(6,)), up(1), *present(1), (5, b"")], tmp_path / "s.sa2s",
               header=account_header(synthetic))
    account = CH.stream_account(s)
    assert account["draws"] == {"total": 4, "skipped_no_shader_route": {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}},
        "refused": {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}},
        "empty": {"DrawPrimitive": {"POINTLIST": 1}, "DrawIndexedPrimitive": {"TRIANGLELIST": 1}},
        "per_frame": [[1, 1, 4]], "after_last_present": 0}
    assert account["accepted_per_frame"] == [[1, 1, 1]]
    assert account["up_draws"] == 1 and account["up_vertex_bytes"] == 48


@pytest.mark.parametrize("synthetic", [False, True], ids=["recorder", "synthetic"])
def test_stream_account_names_preserve_unmapped_fvf_rejection(tmp_path, synthetic):
    head = account_header(synthetic)
    head["methods"].append([1, 89, "SetFVF", 1])
    s = stream([device(), call(1, 89, [2]), *present(1), (5, b"")], tmp_path / "bad.sa2s", header=head)
    with pytest.raises(ValueError, match="nonzero FVF"):
        CH.stream_account(s)


@pytest.fixture(scope="session")
def census_bin(tmp_path_factory) -> Path:
    if not GXX:
        pytest.fail("no g++: the census cannot be built")
    if not (INSTALL / "include" / "dxvk" / "d3d9.h").is_file():
        pytest.fail(f"no pinned DXVK Native headers under {INSTALL}: run private/s5-tools/build.sh")
    out = tmp_path_factory.mktemp("census") / "sa2_frame_census"
    d, p = ROOT / "host" / "d3d9gpu", ROOT / "host" / "player"
    cmd = [GXX, "-std=c++17", "-O1", "-I", str(d), "-I", str(p), "-I", str(ROOT / "host"),
           "-I", str(INSTALL / "include" / "dxvk"), "-I", str(INSTALL / "include"), "-o", str(out),
           str(d / "census.cpp"), str(d / "d9g_clear.cpp"), str(d / "d9g_format.cpp"), str(p / "stream.cpp"),
           str(p / "sha256.cpp"), "-lzstd"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return out


def run_census(census_bin: Path, tmp_path: Path, records, stage: int = 3) -> dict:
    s = stream(records + [(5, b"")], tmp_path / "s.sa2s")
    out = tmp_path / "census.json"
    r = subprocess.run([str(census_bin), str(s), "--stage", str(stage), "--out", str(out), "--tsv",
                        str(tmp_path / "census.tsv")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    c = json.loads(out.read_text())
    assert c["stream_sha256"] == hashlib.sha256(s.read_bytes()).hexdigest()
    return c


def test_the_census_classifies_draws_as_dxvk_and_counts_what_the_player_sends(census_bin, tmp_path):
    recs = [device(), up(2)]                    # no declaration bound: refused
    recs += decl()
    recs += [up(2), up(0), up(0, ptype=4), up(3, ptype=6), up(2, stride=0)]
    recs += present(1)
    c = run_census(census_bin, tmp_path, recs)
    # a LIST of no primitives has no vertices: the player sends nothing; a STRIP of none
    # sends two vertices' worth and DXVK answers D3D_OK with nothing to draw (empty)
    assert c["draws"]["total"] == 5
    assert c["draws"]["refused"] == {"DrawPrimitiveUP": {"TRIANGLESTRIP": 2}}
    assert c["draws"]["empty"] == {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}}
    assert c["draws"]["skipped_no_shader_route"] == {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1, "TRIANGLEFAN": 1}}
    assert c["up_vertex_bytes"] == 4 * 16 + 5 * 16
    assert c["draws"]["per_frame"] == [[1, 1, 5]] and c["frames"] == 1
    # at stage 0 the player sends no draw at all
    assert run_census(census_bin, tmp_path, recs, stage=0)["draws"]["total"] == 0


def test_the_census_lineage_follows_full_colour_clears_of_the_back_buffer(census_bin, tmp_path):
    recs = [device()] + decl()
    recs += [clear(), up(2)] + present(1)                    # 1: drawn
    recs += present(2)                                         # 2: no draw, drawn earlier
    recs += [clear(3)] + present(3)                            # 3: cleared whole: clean
    recs += [up(2)] + present(4)                               # 4: drawn
    recs += [viewport(0, 0, 64, 48), clear()] + present(5)     # 5: a partial clear does not clean
    recs += [viewport(0, 0, 1280, 720), clear(2)] + present(6)  # 6: depth only does not clean
    recs += [clear(1, 1, [(-1, -1, 2000, 2000)])] + present(7)  # 7: a covering rectangle: whole
    c = run_census(census_bin, tmp_path, recs)
    assert c["draw_free_frames"] == [2, 3, 5, 6, 7]
    assert c["draw_free_frames_back_buffer_drawn_earlier"] == [2, 5, 6]
    assert c["frames_made_by_clears_alone"] == 2
    assert c["clears"]["back_buffer_full_colour"] == 3 and c["clears"]["back_buffer_partial_colour"] == 1
    rows = (tmp_path / "census.tsv").read_text().splitlines()
    assert rows[0].split("\t")[-2:] == ["dirty", "ever_cleared"] and len(rows) == 8


def test_the_census_takes_the_player_s_stage_and_device_rules_for_clears(census_bin, tmp_path):
    base = [device(auto_ds=False)] + decl() + [up(2)] + present(1)
    # ZBUFFER with no depth-stencil: DXVK refuses the whole Clear, colour too
    c = run_census(census_bin, tmp_path, base + [clear(3)] + present(2))
    assert c["draw_free_frames_back_buffer_drawn_earlier"] == [2]
    # Count 1 with pRects NULL is not handled by the player: no host call
    c = run_census(census_bin, tmp_path, base + [call(1, 43, [1, 0, 1, 0, 0x3F800000, 0])] + present(2))
    assert c["draw_free_frames_back_buffer_drawn_earlier"] == [2] and c["clears"]["host_calls"] == 0
    # stage 0 replays no SetViewport: the Clear stays whole
    c = run_census(census_bin, tmp_path, [device()] + [viewport(0, 0, 10, 10), clear()] + present(1), stage=0)
    assert c["clears"]["back_buffer_full_colour"] == 1
    c = run_census(census_bin, tmp_path, [device()] + [viewport(0, 0, 10, 10), clear()] + present(1), stage=1)
    assert c["clears"]["back_buffer_full_colour"] == 0 and c["clears"]["back_buffer_partial_colour"] == 1


def test_the_census_resets_the_viewport_with_render_target_0(census_bin, tmp_path):
    recs = [device()] + decl() + [up(2)] + present(1)
    recs += [call(1, 23, [512, 512, 1, 1, 21, 0, 0x0300B000, 0], items=[(OUT, 6, struct.pack("<I", TEX))]),
             call(2, 18, [0, 0x0300B100], this=TEX, items=[(OUT, 1, struct.pack("<I", LVL))]),
             viewport(0, 0, 64, 48),
             call(1, 37, [0, LVL]), clear(),                  # the level, whole (its own size)
             call(1, 37, [0, BB]), clear()]                   # back: the viewport is the back buffer's again
    recs += present(2)
    c = run_census(census_bin, tmp_path, recs)
    assert c["draw_free_frames"] == [2] and c["draw_free_frames_back_buffer_drawn_earlier"] == []
    # below stage 3 SetRenderTarget is not replayed: the small viewport stays, the clear is partial
    c = run_census(census_bin, tmp_path, recs, stage=2)
    assert c["draw_free_frames_back_buffer_drawn_earlier"] == [2]


def test_a_swap_chain_present_ends_a_frame(census_bin, tmp_path):
    recs = [device()] + decl() + [up(2), call(13, 3, [0, 0, 0, 0, 0], this=0x03200990),
                                  (3, struct.pack("<IIII", 16666, 0, 1, 0)), up(1)]
    c = run_census(census_bin, tmp_path, recs)
    assert c["frames"] == 1 and c["draws"]["per_frame"] == [[1, 1, 1]] and c["draws"]["after_last_present"] == 1


def test_the_census_classifies_the_other_three_draw_methods_as_dxvk(census_bin, tmp_path):
    """DrawPrimitive (81), DrawIndexedPrimitive (82) and DrawIndexedPrimitiveUP (84), which
    no registered stream reaches (the review's finding 5), against DXVK 3.1.1's
    d3d9_device.cpp: no declaration refused, and for the UP form a stride of 0; no primitives
    (or, indexed, no vertices) empty; the UP arena's vertex bytes (MinVertexIndex +
    NumVertices) x stride and index bytes the primitives' vertex count x the index size."""
    recs = [device(), dp(4, 2), dip(4, 6, 2), dipup(2)]          # no declaration bound: refused
    recs += decl()
    recs += [dp(4, 2), dp(4, 0),                                # skipped; empty
             dip(4, 6, 2), dip(4, 0, 2), dip(4, 6, 0),          # skipped; no vertices, no primitives: empty
             dipup(2, ptype=4, min_v=1, vertices=5),            # 16-bit list: 6 vertices of 16, 6 indices of 2
             dipup(1, ptype=5, vertices=3, fmt=102, stride=12),  # 32-bit strip: 3 vertices of 12, 3 indices of 4
             dipup(2, stride=0),                                # stride 0: refused
             dipup(0), dipup(2, vertices=0)]                    # no primitives, no vertices: empty
    recs += present(1)
    c = run_census(census_bin, tmp_path, recs)
    assert c["draws"]["total"] == 13
    assert c["draws"]["refused"] == {"DrawPrimitive": {"TRIANGLELIST": 1}, "DrawIndexedPrimitive": {"TRIANGLELIST": 1},
                                     "DrawIndexedPrimitiveUP": {"TRIANGLELIST": 2}}
    assert c["draws"]["empty"] == {"DrawPrimitive": {"TRIANGLELIST": 1}, "DrawIndexedPrimitive": {"TRIANGLELIST": 2},
                                   "DrawIndexedPrimitiveUP": {"TRIANGLELIST": 2}}
    assert c["draws"]["skipped_no_shader_route"] == {
        "DrawPrimitive": {"TRIANGLELIST": 1}, "DrawIndexedPrimitive": {"TRIANGLELIST": 1},
        "DrawIndexedPrimitiveUP": {"TRIANGLELIST": 1, "TRIANGLESTRIP": 1}}
    assert (c["up_vertex_bytes"], c["up_index_bytes"]) == (6 * 16 + 3 * 12, 6 * 2 + 3 * 4)
    assert c["draws"]["per_frame"] == [[1, 1, 13]]
    # below stage 1 the player sends none of them
    assert run_census(census_bin, tmp_path, recs, stage=0)["draws"]["total"] == 0


@pytest.mark.parametrize("cut,omit", [((1, 0), ()), ((0, 1), ()), ((0, 0), (4,)), ((0, 0), (6,))])
def test_the_census_counts_no_draw_whose_data_is_short(census_bin, tmp_path, cut, omit):
    """DrawIndexedPrimitiveUP with fewer index (or vertex) bytes than its arguments name, or
    none: the player refuses the record (host/player/player.cpp in_blob) and makes no host
    call, so the census counts no draw and no arena bytes for it."""
    recs = [device()] + decl() + [dipup(2, ptype=4, min_v=1, vertices=5), dipup(1, ptype=5, cut=cut, omit=omit)]
    recs += present(1)
    c = run_census(census_bin, tmp_path, recs)
    assert c["draws"]["total"] == 1
    assert c["draws"]["skipped_no_shader_route"] == {"DrawIndexedPrimitiveUP": {"TRIANGLELIST": 1}}
    assert (c["up_vertex_bytes"], c["up_index_bytes"]) == (6 * 16, 6 * 2)
    assert c["draws"]["per_frame"] == [[1, 1, 1]]

# ------------------------------------------------------------------ R2 actual geometry account, literal CPU controls

def r2_evidence(partial=False):
    ev = evidence()
    ev['scope'] = 'r2-draws-partial' if partial else 'r2-draws-full'
    ev['census']['stream_sha256'] = CH.BOOT_STREAM_SHA256
    ev['max_frames'] = 1 if partial else 0
    for row in ev['dxvk']['times']:
        row['bgrx_hash'] = f"0x{int(row['bgrx_hash'], 16):016x}"
    ev['sdl']['times'] = copy.deepcopy(ev['dxvk']['times'])
    accepted = {'DrawPrimitiveUP': {'TRIANGLESTRIP': 3 if partial else 5}}
    expected = {'up_vertex_bytes': 192 if partial else 320, 'up_index_bytes': 0,
                'up_arena_bytes': 192 if partial else 320, 'up_pad_bytes': 0,
                'up_frame_peak_bytes': 192, 'up_draw_uploaded_bytes': 192 if partial else 320,
                'up_draws': 3 if partial else 5}
    ev['census'].update(expected)
    ev['census'].update(accepted_per_frame=[[1, 1, 3]] if partial else [[1, 1, 3], [2, 3, 0], [4, 4, 2]],
        accepted_after_last_present=0, consumed_records={'call': 10 if partial else 30, 'd3dx': 1 if partial else 2,
        'frame': 1 if partial else 4, 'note': 0, 'end': 0 if partial else 1, 'unknown': 0, 'seq_gaps': 0, 'after_end': 0},
        consumed_bytes=1000 if partial else 4096,
        first_accepted={'method': 'DrawPrimitiveUP', 'primitive': 'TRIANGLESTRIP', 'frame': 1, 'seq': 4})
    if partial:
        ev['census'].update(frames=1, draw_free_frames=[], draw_free_frames_never_colour_cleared=[])
        ev['census']['draws'].update(total=3, skipped_no_shader_route=accepted, refused={}, per_frame=[[1, 1, 3]])
        for host in ('dxvk', 'sdl'):
            ev[host]['times'] = ev[host]['times'][:1]
            ev[host]['report'].update(presents=1, draws=3)
            ev[host]['report']['methods'][1].update(handled=3, failed=0)
        ev['sdl']['device']['present']['presents'] = 1
        ev['sdl']['device']['methods'][0].update(calls=3, failed=0)
    for host in ('dxvk', 'sdl'):
        rep = ev[host]['report']
        rep.update(stream_sha256=CH.BOOT_STREAM_SHA256, stream_records_read_to_end=not partial, stream_sha256_after_stop=partial,
                   stream_bytes=1000 if partial else 4096, stop='--max-frames' if partial else 'end of stream')
        rep['records'] = copy.deepcopy(ev['census']['consumed_records'])
        rep['options'].update(max_frames=1 if partial else 0, allow_truncated=False, stats_only=False)
    draws = copy.deepcopy(ev['census']['draws'])
    draws.pop('skipped_no_shader_route')
    draws.update(drawn=3 if partial else 5, accepted=3 if partial else 5, encoded=accepted,
                 unmapped_count=0, backend_failed_count=0, suppressed_count=0, fault_uncounted=0,
                 unmapped={}, backend_failed={}, fault_suppressed={}, failures=[])
    ev['sdl']['device']['draws'] = draws
    ev['sdl']['device']['uploads'] = expected | {'up_arena_uploads': 3 if partial else 5}
    return ev


@pytest.mark.parametrize('partial', [False, True])
def test_r2_geometry_literal_baselines(partial):
    ev = r2_evidence(partial)
    assert all(c['pass'] for c in CH.evaluate(ev)), CH.evaluate(ev)
    assert CH.check_c(ev)['detail'] == {
        'accepted': 3 if partial else 5, 'encoded': 3 if partial else 5, 'unmapped': 0, 'backend_failed': 0,
        'suppressed': 0, 'totals': {'census': 3 if partial else 6, 'dxvk report': 3 if partial else 6,
                                   'sdlgpu report': 3 if partial else 6, 'device': 3 if partial else 6},
        'refused': 0 if partial else 1}
    assert CH.check_e(ev)['detail']['hash_scope'] == 'all completed frames'
    assert CH.check_e(ev)['detail']['comparable_frames'] == ([[1, 1]] if partial else [[1, 4]])


@pytest.mark.parametrize('path,value', [
    ('draws.drawn', True), ('draws.accepted', 5.0), ('draws.total', 6.0), ('draws.fault_uncounted', False),
    ('draws.unmapped_count', False), ('draws.backend_failed_count', False), ('draws.suppressed_count', False),
    ('draws.encoded.DrawPrimitiveUP.TRIANGLESTRIP', True),
    ('draws.encoded.DrawPrimitiveUP.TRIANGLESTRIP', 4), ('draws.encoded', {'Other': {'TRIANGLESTRIP': 1}}),
    ('draws.refused.DrawPrimitiveUP.TRIANGLEFAN', 1.0), ('draws.empty', {'DrawPrimitive': {'POINTLIST': 0}}),
    ('draws.per_frame', [[1, 1, 3], [3, 4, 3]]), ('draws.per_frame', [[True, 1, 3], [2, 3, 0], [4, 4, 3]]),
    ('draws.after_last_present', False), ('draws.failures', ['real backend error']),
    ('uploads.up_arena_bytes', 319), ('uploads.up_pad_bytes', False), ('uploads.up_frame_peak_bytes', 320),
    ('uploads.up_arena_uploads', 4), ('uploads.up_draw_uploaded_bytes', 319),
])
def test_r2_real_accounting_mutants_fail(path, value):
    ev = r2_evidence()
    put(ev, 'sdl.device.' + path, value)
    assert CH.check_c(ev)['pass'] is False, (path, value)


@pytest.mark.parametrize('field', ['drawn', 'total', 'fault_uncounted', 'accepted', 'unmapped_count',
    'backend_failed_count', 'suppressed_count', 'encoded', 'unmapped', 'backend_failed', 'fault_suppressed',
    'refused', 'empty', 'failures', 'per_frame', 'after_last_present'])
def test_r2_missing_account_fields_fail(field):
    ev = r2_evidence()
    del ev['sdl']['device']['draws'][field]
    assert CH.check_c(ev)['pass'] is False


@pytest.mark.parametrize('partial,path,value', [
    (False, 'stop', '--max-frames'), (False, 'records.end', 0), (False, 'records.call', 29),
    (False, 'records.end', True), (False, 'stream_records_read_to_end', False),
    (False, 'stream_sha256_after_stop', True), (False, 'stream_bytes', 4095),
    (False, 'options.allow_truncated', True), (False, 'options.stats_only', True),
    (True, 'stop', 'end of stream'), (True, 'records.end', 1), (True, 'records.call', 11),
    (True, 'stream_records_read_to_end', True), (True, 'stream_sha256_after_stop', False),
    (True, 'options.max_frames', 2), (True, 'options.max_frames', True),
])
def test_r2_full_and_partial_stop_mutants_fail(partial, path, value):
    ev = r2_evidence(partial)
    put(ev, 'sdl.report.' + path, value)
    assert CH.check_a(ev)['pass'] is False


def test_r2_geometry_frame_hash_mismatch_fails_while_legacy_scope_has_only_clear_frames():
    ev = r2_evidence()
    ev['sdl']['times'][0]['bgrx_hash'] = '0x0000000000000009'
    assert CH.check_e(ev)['pass'] is False
    assert CH.check_e(dict(ev, scope='r2a-clears'))['pass'] is True


def suppress_geometry(ev, change_pixels=True):
    d = ev['sdl']['device']
    d['fault'] = 'geometry-noop'
    d['draws'].update(drawn=0, encoded={}, suppressed_count=d['draws']['accepted'],
                      fault_suppressed=copy.deepcopy(d['draws']['encoded']))
    # update evaluates values before update; retain the accepted outcomes explicitly
    d['draws']['fault_suppressed'] = copy.deepcopy(ev['census']['draws']['skipped_no_shader_route'])
    d['uploads'].update(up_arena_uploads=0, up_draw_uploaded_bytes=0)
    if change_pixels:
        ev['sdl']['times'][0]['bgrx_hash'] = '0x0000000000000009'


@pytest.mark.parametrize('partial', [False, True])
def test_r2_suppression_must_change_completed_geometry_pixels(partial):
    control = CH.R2_CONTROLS[0]
    ev = r2_evidence(partial)
    suppress_geometry(ev)
    assert CH.control_outcome(control, ev)['as_required'] is True
    assert CH.check_b(ev)['pass'] is False  # normal strict verdict rejects the fault
    assert CH.check_c(ev)['pass'] is False  # normal strict verdict rejects suppression
    ev = r2_evidence(partial)
    suppress_geometry(ev, change_pixels=False)
    assert CH.control_outcome(control, ev)['as_required'] is False


def test_r2_reported_backend_failure_preserves_answers_but_fails_device():
    ev = r2_evidence()
    ev['sdl']['device']['failure'] = 'DrawPrimitiveUP: upload failed'
    assert CH.check_b(ev)['pass'] is False
    assert CH.check_d(ev)['pass'] is True


def r2_count_drop(ev):
    d = ev['sdl']['device']
    d['fault'] = 'count-draw'
    d['draws'].update(total=5, accepted=4, drawn=4, fault_uncounted=1,
        encoded={'DrawPrimitiveUP': {'TRIANGLESTRIP': 4}}, per_frame=[[1, 1, 2], [2, 3, 0], [4, 4, 3]])


def test_r2_accounting_control_restores_only_exact_first_accepted_class_and_frame():
    ev = r2_evidence()
    r2_count_drop(ev)
    assert CH.control_outcome(CH.R2_CONTROLS[1], ev)['as_required'] is True
    ev['sdl']['device']['draws']['per_frame'] = [[1, 1, 3], [2, 3, 0], [4, 4, 2]]
    assert CH.control_outcome(CH.R2_CONTROLS[1], ev)['as_required'] is False
    ev = r2_evidence()
    r2_count_drop(ev)
    ev['sdl']['device']['draws']['encoded'] = {'DrawPrimitiveUP': {'TRIANGLESTRIP': 3, 'POINTLIST': 1}}
    assert CH.control_outcome(CH.R2_CONTROLS[1], ev)['as_required'] is False


def test_r2_original_stream_up_alignment_padding_and_partial_records_are_literal(tmp_path):
    # Decl FLOAT3 extends to byte 12. Three POINTLIST vertices at stride 4 need 20 allocated bytes,
    # of which 12 are caller bytes. The next block starts at byte 64 and contains 16 vertex + 6 index bytes.
    s = stream([device(), *decl(), up(3, ptype=1, stride=4), dipup(1, min_v=1, vertices=3, stride=4),
                *present(1), up(1, ptype=1, stride=16), *present(2), (5, b'')], tmp_path / 'original.sa2s')
    original = s.read_bytes()
    account = CH.stream_account(s)
    assert account['draws'] == {'total': 3,
        'skipped_no_shader_route': {'DrawPrimitiveUP': {'POINTLIST': 2}, 'DrawIndexedPrimitiveUP': {'TRIANGLELIST': 1}},
        'refused': {}, 'empty': {}, 'per_frame': [[1, 1, 2], [2, 2, 1]], 'after_last_present': 0}
    assert {k: account[k] for k in ('up_vertex_bytes', 'up_index_bytes', 'up_arena_bytes', 'up_pad_bytes',
            'up_frame_peak_bytes', 'up_draw_uploaded_bytes', 'up_draws')} == {
        'up_vertex_bytes': 44, 'up_index_bytes': 6, 'up_arena_bytes': 110, 'up_pad_bytes': 60,
        'up_frame_peak_bytes': 94, 'up_draw_uploaded_bytes': 66, 'up_draws': 3}
    partial = CH.stream_account(s, max_frames=1)
    assert partial['consumed_records'] == {'call': 6, 'd3dx': 0, 'frame': 1, 'note': 0, 'end': 0,
                                           'unknown': 0, 'seq_gaps': 0, 'after_end': 0}
    assert partial['draws']['total'] == 2 and partial['frames'] == 1
    assert partial['up_arena_bytes'] == 94 and partial['up_draw_uploaded_bytes'] == 50
    assert partial['consumed_bytes'] < len(original) == account['consumed_bytes']
    assert s.read_bytes() == original
    with pytest.raises(ValueError, match='bound did not stop'):
        CH.stream_account(s, max_frames=3)


def marker_doc(partial=False):
    ev = r2_evidence(partial)
    g = r2_evidence(partial); suppress_geometry(g)
    c = r2_evidence(); r2_count_drop(c)
    controls = [CH.control_outcome(CH.R2_CONTROLS[0], g), CH.control_outcome(CH.R2_CONTROLS[1], c)]
    checks = CH.evaluate(ev) + [{"check": "i", "pass": True, "failures": []}]
    if not partial:
        checks += [{"check": "h", "pass": True, "failures": [], "detail": {"sha256": CH.BOOT_REFERENCE_SHA256, "rows": 4}}]
    inputs = {"pipeline": {CH.SELF: CH.RAN_BY}, "ran_by": CH.RAN_BY,
        "files": {"sa2_player": PLAYER_SHA, "sa2_player_gpu": GPU_SHA, "sa2_frame_census": "c" * 64},
        "provenance": {"pass": True, "failures": [], "records": dict.fromkeys(CH.BUILD_RECORDS, "b" * 64),
            "binaries": {"sa2_player.json": {"sa2_player": PLAYER_SHA}, "sa2_player_gpu.json": {"sa2_player_gpu": GPU_SHA},
                "player_tools.json": {}, "d3d9gpu_tools.json": {"sa2_frame_census": "c" * 64}}}}
    return {"verdict_with_controls": "PASS", "acceptance_scope": ev["scope"], "checks": checks, "inputs": inputs,
            "max_frames": ev["max_frames"], "stage": 3,
            "stream": {"sha256": CH.BOOT_STREAM_SHA256, "bytes": 4096},
            "census": {"frames": ev["census"]["frames"], "draws": ev["census"]["draws"]["total"],
                       "consumed_records": ev["census"]["consumed_records"], "consumed_bytes": ev["census"]["consumed_bytes"]},
            "runs": {"dxvk": CH.summarise(ev["dxvk"]), "sdlgpu": CH.summarise(ev["sdl"])},
            "controls": {"run": True, "results": controls}}


def test_r2_marker_cannot_promote_legacy_or_partial_to_full():
    doc = marker_doc()
    assert CH.acceptance_marker(doc) == "R2_DRAW_OK"
    doc["acceptance_scope"] = "r2a-clears"
    assert CH.acceptance_marker(doc) == "R2A_CLEARS_OK"
    doc = marker_doc(True)
    assert CH.acceptance_marker(doc) == "R2_PARTIAL_GEOMETRY_OK"
    doc["acceptance_scope"] = "r2-draws-full"
    assert CH.acceptance_marker(doc) == "R2_FAIL"
    doc = marker_doc()
    doc["stream"]["sha256"] = "a" * 64
    for run in doc["runs"].values():
        run["stream_sha256"] = "a" * 64
    assert CH.acceptance_marker(doc) == "R2_STREAM_GEOMETRY_OK"
    doc["acceptance_scope"] = "r2-draws-partial"
    assert CH.acceptance_marker(doc) == "R2_FAIL"
    doc = marker_doc()
    doc["controls"]["results"][0]["as_required"] = False
    assert CH.acceptance_marker(doc) == "R2_FAIL"


def test_r2_partial_rejects_synthetic_or_untyped_scope():
    ev = r2_evidence(True)
    ev["census"]["stream_sha256"] = "a" * 64
    assert CH.check_a(ev)["pass"] is False
    ev = r2_evidence(True)
    ev["scope"] = None
    assert CH.check_a(ev)["pass"] is False


@pytest.mark.parametrize("key", ["failure", "fault", "fault_error"])
def test_r2_device_null_fields_must_be_present(key):
    ev = r2_evidence()
    del ev["sdl"]["device"][key]
    assert CH.check_b(ev)["pass"] is False

@pytest.mark.parametrize('dtype, extent', [(0, 4), (1, 8), (2, 12), (3, 16), (4, 4), (5, 4),
    (6, 4), (7, 8), (8, 4), (9, 4), (10, 8), (11, 4), (12, 8), (13, 4), (14, 4), (15, 4), (16, 8)])
def test_r2_up_declaration_extent_is_literal_for_every_type(tmp_path, dtype, extent):
    elems = struct.pack('<HHBBBB', 0, 0, dtype, 0, 0, 0) + struct.pack('<HHBBBB', 255, 0, 17, 0, 0, 0)
    create = call(1, 86, [0x0300D000, 0x0300D100], items=[(IN, 0, elems), (OUT, 1, struct.pack('<I', DECL))])
    s = stream([device(), create, call(1, 87, [DECL]), up(1, ptype=1, stride=4), *present(1), (5, b'')], tmp_path / 's.sa2s')
    account = CH.stream_account(s)
    assert account['up_vertex_bytes'] == 4
    assert account['up_arena_bytes'] == extent
    assert account['up_pad_bytes'] == extent - 4
    assert account['up_draw_uploaded_bytes'] == extent


@pytest.mark.parametrize('field,value', [('bgrx_hash', ''), ('bgrx_hash', '0x1'), ('seq', None), ('polls', 'false')])
def test_r2_times_missing_or_invalid_columns_fail(field, value):
    ev = r2_evidence()
    for host in ('dxvk', 'sdl'):
        ev[host]['times'][0][field] = value
    assert CH.check_e(ev)['pass'] is False


@pytest.mark.parametrize('sdlgpu', [False, True])
def test_r2_bounded_runner_passes_original_stream_and_only_existing_max_frames(monkeypatch, tmp_path, sdlgpu):
    calls = recorded_commands(monkeypatch)
    original = tmp_path / 'original.sa2s'
    CH.run_player(tmp_path / 'bin', original, 3, tmp_path / 'run', sdlgpu=sdlgpu, max_frames=1)
    cmd, _ = calls[0]
    assert str(original) in cmd
    assert cmd[cmd.index('--max-frames') + 1] == '1'
    assert cmd[cmd.index('--stage') + 1] == '3'
    assert '--allow-truncated' not in cmd


def test_r2a_clears_accepts_new_empty_outcome_schema_without_promoting_geometry():
    ev = r2_evidence()
    ev['scope'] = 'r2a-clears'
    ev['census']['draws'].update(total=1, skipped_no_shader_route={}, per_frame=[[1, 1, 1], [2, 4, 0]])
    d = ev['sdl']['device']['draws']
    d.update(total=1, accepted=0, drawn=0, encoded={}, per_frame=[[1, 1, 1], [2, 4, 0]])
    ev['census']['up_vertex_bytes'] = ev['sdl']['device']['uploads']['up_vertex_bytes'] = 0
    for host in ('dxvk', 'sdl'):
        ev[host]['report']['draws'] = 1
    assert CH.check_c(ev)['pass'] is True
    assert CH.check_b(ev)['pass'] is True
    assert CH.check_c(dict(ev, scope='r2-draws-full'))['pass'] is False
    del d['encoded']
    assert CH.check_c(ev)['pass'] is False


@pytest.mark.parametrize('key', ['skipped_no_shader_route', 'made_up_outcome'])
def test_r2_unknown_or_legacy_draw_outcomes_cannot_coexist_with_encoded(key):
    ev = r2_evidence()
    ev['sdl']['device']['draws'][key] = {'DrawPrimitiveUP': {'TRIANGLESTRIP': 5}}
    assert CH.check_c(ev)['pass'] is False


def test_r2_suppression_changed_only_refused_frame_cannot_prove_geometry():
    ev = r2_evidence()
    frames = [[1, 1, 3], [2, 2, 1], [3, 3, 0], [4, 4, 2]]
    ev['census']['draws']['per_frame'] = frames
    ev['sdl']['device']['draws']['per_frame'] = copy.deepcopy(frames)
    ev['census']['draw_free_frames'] = [3]
    assert all(c['pass'] for c in CH.evaluate(ev))
    suppress_geometry(ev, change_pixels=False)
    ev['sdl']['times'][1]['bgrx_hash'] = '0x0000000000000009'
    outcome = CH.control_outcome(CH.R2_CONTROLS[0], ev)
    assert outcome['as_required'] is False
    assert 'geometry-noop changed no completed geometry-bearing frame' in outcome['collateral']


@pytest.mark.parametrize('field,value', [('accepted_per_frame', None), ('accepted_per_frame', [[1, 4, True]]),
    ('accepted_per_frame', [[1, 4, 0]]), ('accepted_after_last_present', None), ('accepted_after_last_present', False)])
def test_r2_independent_accepted_frame_account_is_required(field, value):
    ev = r2_evidence()
    ev['census'][field] = value
    assert CH.check_c(ev)['pass'] is False


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('path,value', [('stage', 0), ('census.consumed_records', None), ('census.consumed_bytes', None),
    ('census.consumed_bytes', 999), ('runs.sdlgpu.records.end', True), ('runs.sdlgpu.stop', 'other'),
    ('runs.sdlgpu.stream_records_read_to_end', None), ('runs.sdlgpu.stream_sha256_after_stop', None),
    ('runs.sdlgpu.stream_bytes', 999), ('runs.sdlgpu.stage', 0), ('runs.sdlgpu.placeholder_binds', 1),
    ('runs.sdlgpu.allow_truncated', True), ('runs.sdlgpu.stats_only', True)])
def test_r2_marker_holds_serialized_original_scope_facts(partial, path, value):
    doc = marker_doc(partial)
    put(doc, path, value)
    assert CH.acceptance_marker(doc) == 'R2_FAIL'


def test_r2_full_boot_marker_requires_registered_reference_content():
    doc = marker_doc()
    h = next(c for c in doc['checks'] if c['check'] == 'h')
    h['detail']['sha256'] = 'a' * 64
    assert CH.acceptance_marker(doc) == 'R2_FAIL'
    ev = r2_evidence()
    ev['reference'] = {'path': 'custom.tsv', 'sha256': 'a' * 64, 'rows': ev['dxvk']['times']}
    assert CH.check_h(ev)['pass'] is False


@pytest.mark.parametrize('path,value', [('inputs.provenance.pass', False), ('inputs.provenance.records', {}),
    ('inputs.files.sa2_player_gpu', 'f' * 64), ('runs.sdlgpu.player.sha256', 'f' * 64),
    ('inputs.ran_by', 'f' * 64), ('inputs.pipeline', {})])
def test_r2_marker_holds_exact_player_and_tree_provenance(path, value):
    doc = marker_doc()
    put(doc, path, value)
    assert CH.acceptance_marker(doc) == 'R2_FAIL'


def test_r2_receipt_recomputes_actual_geometry_artifacts_before_writing(monkeypatch, tmp_path):
    doc = marker_doc()
    run, output = tmp_path / 'run', tmp_path / 'receipt.json'
    run.mkdir()
    (run / 'compare.json').write_text(json.dumps(doc))
    monkeypatch.setattr(CH, 'receipt_refusal', lambda *args: None)
    # This isolated validator-refusal test supplies no physical artifact graph.
    # Actual graph/publication refusal is exercised by publication_fixture below.
    monkeypatch.setattr(CH, 'receipt_inputs', lambda *args: {})
    seen = []
    def refused_check(args):
        seen.append(args.run)
        return 2
    monkeypatch.setattr(CH, 'cmd_check', refused_check)
    assert CH.main(['receipt', '--id', RID, '--run', str(run), '--out', str(output)]) == 2
    assert seen == [str(run)]
    assert not output.exists()


@pytest.mark.parametrize("name", CH.RUN_OUTPUTS)
def test_r2_geometry_control_output_changes_are_part_of_saved_evidence(tmp_path, name):
    control = tmp_path / "control-geometry-noop"
    control.mkdir()
    path = control / name
    path.write_bytes(b"original measured output")
    original = CH.recorded_outputs(tmp_path)
    assert set(original) == {"control-geometry-noop/" + name}
    path.write_bytes(b"replacement measured output")
    assert CH.recorded_outputs(tmp_path) != original

# ------------------------------------------------------------------ R3 registered-boot A/A, authored original bytes only

def aa_pair(monkeypatch, tmp_path):
    """A tiny literal original/reference replaces registered SHA policy only in this fixture.

    Real pipeline, depfile, original decoder, controls, provenance and frame validators run.
    No compiler, executable or GPU is launched; binaries/build records are authored stand-ins.
    """
    monkeypatch.setattr(sys.modules[__name__], 'compiler', lambda: {
        'command': '/authored/compiler', 'version': 'authored compiler record', 'search': ['/usr/include']})
    build = forged_build(tmp_path / 'build')
    original = stream([device(), *decl(), clear(), up(1), *present(1), (5, b'')], tmp_path/'original.sa2s')
    account = CH.stream_account(original)
    assert account['draws']['total'] == 1 and account['up_vertex_bytes'] == 48
    assert account['consumed_records']['end'] == 1 and account['frames'] == 1
    monkeypatch.setattr(CH, 'BOOT_STREAM_SHA256', CH.sha256(original))
    rows = [{'frame':'1','seq':'6','virtual_us':'16666','guest_frames':'1','polls':'0',
             'wall_us':'7','bgrx_hash':'0x000000000000000b'}]
    def tsv(path, table):
        path.write_text('\t'.join(rows[0])+'\n'+''.join('\t'.join(r[k] for k in rows[0])+'\n' for r in table))
    reference=tmp_path/'reference.tsv';tsv(reference,rows)
    monkeypatch.setattr(CH,'BOOT_REFERENCE_SHA256',CH.sha256(reference))
    monkeypatch.setattr(CH,'REFERENCES',{(CH.BOOT_STREAM_SHA256,3):str(reference)})
    c=census(frames=1,draw_free_frames=[],device={'made':True,'back_buffer':[1280,720,21],'auto_depth':True})
    c.update(account);c.update(stream=str(original),stream_sha256=CH.sha256(original),stage=3)
    c['clears'].update(records=1,back_buffer_full_colour=1,back_buffer_partial_colour=0,past_edge=0)
    c['draws']['refused']={};c['draw_free_frames_back_buffer_drawn_earlier']=[]
    def run(path, index):
        path.mkdir();(path/'census.json').write_text(json.dumps(c));write_matrix(path,build)
        ev=r2_evidence();ev['census']=copy.deepcopy(c)
        ev['dxvk']['times']=copy.deepcopy(rows);ev['sdl']['times']=copy.deepcopy(rows)
        for h,key in [('dxvk','dxvk'),('sdlgpu','sdl')]:
            rep=ev[key]['report'];rep.update(presents=1,draws=1,stream_bytes=original.stat().st_size,
                stream_sha256=CH.sha256(original),records=account['consumed_records'],t_start_monotonic_s=100.0+index,
                stream_records_read_to_end=True,stream_sha256_after_stop=False)
            rep['methods'][1].update(handled=1,failed=0)
            rep['frames_out']={'path':str(path/h/'frames.y4m'),'times':str(path/h/'frames.y4m.times.tsv'),'bgrx_digest':'0xb'}
            # Literal field names emitted by the actual player, independent of AA_ENV_KEYS.
            rep['env']=dict.fromkeys(('DXVK_WSI_DRIVER','DXVK_CONFIG_FILE','DXVK_CONFIG','DXVK_LOG_PATH',
                'DXVK_LOG_LEVEL','DXVK_SHADER_CACHE','DXVK_SHADER_CACHE_PATH','DXVK_HUD','DXVK_FILTER_DEVICE_NAME',
                'DXVK_FILTER_DEVICE_UUID','DXVK_FORCE_WINDOWED','SDL_NO_SIGNAL_HANDLERS','SDL_VIDEO_DRIVER',
                'VK_LOADER_LAYERS_DISABLE','VK_LOADER_LAYERS_ENABLE','VK_INSTANCE_LAYERS','VK_DRIVER_FILES',
                'VK_ICD_FILENAMES','SA2_DXVK_D3D9','SA2_PLAY_OUT'),'')
            rep['env'].update(DXVK_LOG_PATH=str(path/h/'dxvk-log'),SA2_PLAY_OUT=str(path/'play-out'),
                DXVK_SHADER_CACHE_PATH=str(path/'play-out/dxvk-cache'),VK_DRIVER_FILES='/usr/share/vulkan/icd.d/nvidia_icd.json')
            # Literal options emitted by player.cpp's report writer at lines 3450-3454.
            rep['options'].update(pace=False,speed=1,vsync=False,hidden=False,max_frames=0,
                allow_truncated=False,allow_notes=False,stats_only=False,report_every=100)
            rep['host']={'sdl_video_driver':'wayland'}
            rep['device'].update(failure=None,back_buffer=[1280,720,21],auto_depth=75,interval='IMMEDIATE',
                guest_behavior='0x00000040',guest_multisample=0,guest_device='0x03200010',
                guest_back_buffer='0x03200020',guest_depth='0x03200030')
        dev=ev['sdl']['device'];dev['present']['presents']=1;dev['methods'][0].update(calls=1,failed=0)
        dev['draws']=copy.deepcopy(c['draws']);dev['draws'].pop('skipped_no_shader_route')
        dev['draws'].update(drawn=1,accepted=1,encoded=account['draws']['skipped_no_shader_route'],unmapped_count=0,
            backend_failed_count=0,suppressed_count=0,fault_uncounted=0,unmapped={},backend_failed={},fault_suppressed={},failures=[])
        dev['uploads']={k:account[k] for k in ('up_vertex_bytes','up_index_bytes','up_arena_bytes','up_pad_bytes',
            'up_frame_peak_bytes','up_draw_uploaded_bytes')};dev['uploads']['up_arena_uploads']=1
        dev['clears']['calls']=1
        dev['sdl_gpu'].update(vk_query_ok=True,vk_vendor_id=0x10de,vk_query='vkGetPhysicalDeviceProperties',
            vulkan_options={'api_version':'1.3','features':['robustBufferAccess'],'extensions':[]},
            depth_format='D24_UNORM_S8_UINT',shader_formats='spirv',vk_device_id=0x2b85,
            sdl_revision='authored SDL revision',sdl_patched=True,sdl_capabilities_present=True,
            detected={'sampler_clamp_to_border':True,'depth_clip_and_clamp':True},capability_override=None,
            sampler_clamp_to_border=True,depth_clip_and_clamp=True,fallback_owed={'border':False,'depth_clamp':False})
        dev['env']=dict.fromkeys(('SA2_D3D9GPU_REPORT','SA2_D3D9GPU_FAULT','SA2_D3D9GPU_PRESENT','SA2_D3D9GPU_DEBUG',
            'SA2_D3D9GPU_CAPS_OFF','SA2_D3D9GPU_TEST_RUN','SA2_D3D9GPU_SHADER_DUMP','SA2_D3D9GPU_SHADER_FAULT',
            'SDL_VIDEO_DRIVER','VK_DRIVER_FILES','VK_ICD_FILENAMES','VK_LOADER_LAYERS_DISABLE','VK_LOADER_LAYERS_ENABLE',
            'VK_INSTANCE_LAYERS','SDL_ASSERT'),'');dev['env']['SA2_D3D9GPU_REPORT']=str(path/'sdlgpu/d3d9gpu.json')
        keys=path/'sdlgpu/d3d9gpu.keys.json';keys.parent.mkdir();keys.write_text('authored shader key artifact\n')
        dev['pipeline']={'keys_file':str(keys),'keys_sha256':CH.sha256(keys),'device_params':True}
        variants=[('dxvk',ev['dxvk']),('sdlgpu',ev['sdl'])]
        cr=[];outcomes=[]
        for control in CH.R2_CONTROLS:
            faulted=copy.deepcopy(ev)
            if control['name']=='geometry-noop':suppress_geometry(faulted)
            else:r2_count_drop(faulted)
            # One accepted original call, so the count fault drops exactly that one call.
            if control['name']!='geometry-noop':
                faulted['sdl']['device']['draws'].update(total=0,accepted=0,drawn=0,
                    encoded={},per_frame=[[1,1,0]],fault_uncounted=1)
            variants.append(('control-'+control['name'],faulted['sdl']))
        for name,data in variants:
            d=path/name;(d/'dxvk-log').mkdir(parents=True,exist_ok=True)
            (d/'report.json').write_text(json.dumps(data['report']));(d/'exit').write_text('0\n')
            (d/'check.json').write_text(json.dumps(data['check']));(d/CH.CHECKED_BY).write_text(CHECKER+'\n')
            tsv(d/'frames.y4m.times.tsv',data['times'])
            if data['device'] is not None:(d/'d3d9gpu.json').write_text(json.dumps(data['device']))
        (path/'dxvk/dxvk-log/sa2_player_d3d9.log').write_text(DXVK_LOG)
        dx,sd=CH.load_run(path/'dxvk'),CH.load_run(path/'sdlgpu')
        for control in CH.R2_CONTROLS:
            r=CH.load_run(path/('control-'+control['name']));cr.append(r)
            outcomes.append(CH.control_outcome(control,{'census':c,'dxvk':dx,'sdl':r,'hidden':False,
                'scope':'r2-draws-full','max_frames':0}))
        args=argparse.Namespace(build=str(build),stream=str(original),hidden=False,reference=str(reference),
            scope='r2-draws-full',max_frames=0)
        doc=CH.assemble(path,args,c,dx,sd,outcomes,'authored',control_runs=cr,matrix=CH.load_matrix(path))
        assert doc['marker']=='R2_DRAW_OK',doc
        (path/'compare.json').write_text(json.dumps(doc))
        return path
    return run(tmp_path/'a',0),run(tmp_path/'b',10)


def aa_refresh(path):
    doc=json.loads((path/'compare.json').read_text());doc['outputs']=CH.recorded_outputs(path)
    (path/'compare.json').write_text(json.dumps(doc))


def aa_put(doc, path, value):
    names=path.split('.')
    for key in names[:-1]:doc=doc[int(key)] if isinstance(doc,list) else doc[key]
    doc[names[-1]]=value


def aa_json(path, change):
    doc=json.loads(path.read_text());change(doc);path.write_text(json.dumps(doc))


def test_r3_aa_equal_full_runs_recheck_read_only_and_write_separate_receipt(monkeypatch,tmp_path):
    a,b=aa_publication_fixture(monkeypatch,tmp_path)
    before={str(p):CH.sha256(p) for d in (a,b) for p in d.rglob('*') if p.is_file()}
    result=CH.check_aa(a,b)
    assert result['verdict']=='PASS',result
    assert set(result['hosts'])=={'dxvk','sdlgpu'} and all(h['pass'] for h in result['hosts'].values())
    assert before=={str(p):CH.sha256(p) for d in (a,b) for p in d.rglob('*') if p.is_file()}
    out=tmp_path/'aa.json'
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==0
    assert json.loads(out.read_text())['verdict']=='PASS'
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2


def aa_tsv(path, change):
    rows=CH.read_times(path);change(rows)
    path.write_text('\t'.join(('frame','seq','virtual_us','guest_frames','polls','wall_us','bgrx_hash'))+'\n'+
        ''.join('\t'.join(row[k] for k in ('frame','seq','virtual_us','guest_frames','polls','wall_us','bgrx_hash'))+'\n' for row in rows))


@pytest.mark.parametrize('hosts',[('sdlgpu',),('dxvk','sdlgpu')])
@pytest.mark.parametrize('field,value',[('frame','2'),('seq','7'),('virtual_us','16667'),('guest_frames','2'),
    ('polls','1'),('bgrx_hash','0x000000000000000c')])
def test_r3_aa_every_literal_column_is_held_for_each_role_even_coherent_b(monkeypatch,tmp_path,hosts,field,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    for host in hosts:aa_tsv(b/host/'frames.y4m.times.tsv',lambda rows: rows[0].update({field:value}))
    aa_refresh(b)
    result=CH.check_aa(a,b)
    assert result['verdict']=='FAIL',result
    for host in hosts:
        assert result['hosts'][host]['pass'] is False,result
        assert result['hosts'][host]['mismatches']==[{'row':1,'fields':[field]}],result


@pytest.mark.parametrize('shape',['empty','extra','reordered','malformed'])
def test_r3_aa_ordered_row_inventory_is_not_a_saved_pass(monkeypatch,tmp_path,shape):
    a,b=aa_pair(monkeypatch,tmp_path);p=b/'sdlgpu/frames.y4m.times.tsv'
    if shape=='malformed':p.write_text('frame\tseq\n1\t6\n')
    else:
        def change(rows):
            if shape=='empty':rows.clear()
            else:
                rows.append(dict(rows[0],frame='2',seq='7',virtual_us='33332',guest_frames='2'))
                if shape=='reordered':rows.reverse()
        aa_tsv(p,change)
    aa_refresh(b);result=CH.check_aa(a,b)
    assert result['verdict']=='FAIL',result
    if shape!='malformed':assert result['hosts']['sdlgpu']['pass'] is False,result


def test_r3_aa_wall_time_is_the_only_excluded_times_field(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path)
    aa_tsv(b/'sdlgpu/frames.y4m.times.tsv',lambda rows: rows[0].update(wall_us='999999'))
    aa_refresh(b);result=CH.check_aa(a,b)
    assert result['verdict']=='PASS',result
    assert result['hosts']['sdlgpu']['columns']==['frame','seq','virtual_us','guest_frames','polls','bgrx_hash']


@pytest.mark.parametrize('path,value',[('stop','max frames'),('complete',False),('records.end',0),
    ('stream_records_read_to_end',False),('stream_sha256_after_stop',True),('options.max_frames',1),
    ('stage',2),('readback_failures',1),('stream_sha256','f'*64),('stream_bytes',1),
    ('methods.1.failed',1),('textures.placeholder_binds',1),('options.allow_truncated',True)])
def test_r3_aa_full_original_player_end_and_scope_are_recomputed(monkeypatch,tmp_path,path,value):
    a,b=aa_publication_fixture(monkeypatch,tmp_path)
    aa_json(b/'sdlgpu/report.json',lambda d:aa_put(d,path,value));aa_refresh(b)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    out=tmp_path/'refused.json'
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==1
    assert not out.exists()


@pytest.mark.parametrize('path,value',[('acceptance_scope','r2a-clears'),('acceptance_scope','r2-draws-partial'),
    ('max_frames',1),('stage',2),('inputs.ran_by','f'*64),('inputs.pipeline',{}),
    ('inputs.files.sa2_player_gpu','f'*64),('stream.sha256','f'*64)])
def test_r3_aa_saved_pass_cannot_replace_provenance_source_or_full_scope(monkeypatch,tmp_path,path,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    aa_json(b/'compare.json',lambda d:aa_put(d,path,value))
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result


@pytest.mark.parametrize('target,path,value',[
    ('report','env.DXVK_CONFIG','different non-output setting'),('report','env.VK_DRIVER_FILES','other ICD'),
    ('report','options.hidden',True),('report','device.back_buffer',[640,480,21]),
    ('report','device.guest_behavior','0x00000000'),('report','device.interval','DEFAULT'),
    ('device','env.SA2_D3D9GPU_CAPS_OFF','border,depth_clamp'),('device','env.SA2_D3D9GPU_DEBUG','1'),
    ('device','env.SA2_D3D9GPU_SHADER_FAULT','fault'),('device','sdl_gpu.driver_version','different'),
    ('device','sdl_gpu.vulkan_options.features',['different']),('device','sdl_gpu.detected.depth_clip_and_clamp',False),
    ('device','pipeline.device_params',False)])
def test_r3_aa_nonoutput_player_device_and_driver_settings_cannot_drift(monkeypatch,tmp_path,target,path,value):
    a,b=aa_pair(monkeypatch,tmp_path);name='report.json' if target=='report' else 'd3d9gpu.json'
    aa_json(b/'sdlgpu'/name,lambda d:aa_put(d,path,value));aa_refresh(b)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert any('settings differ' in f for f in result['failures']),result


@pytest.mark.parametrize('target,path',[
    ('report','env.DXVK_CONFIG'),('report','device'),('report','device.interval'),
    ('device','env.SA2_D3D9GPU_DEBUG'),('device','sdl_gpu.vulkan_options'),
    ('device','sdl_gpu.vulkan_options.features'),('device','sdl_gpu.driver_version')])
def test_r3_aa_equal_incomplete_settings_are_not_measured_settings(monkeypatch,tmp_path,target,path):
    a,b=aa_pair(monkeypatch,tmp_path);name='report.json' if target=='report' else 'd3d9gpu.json'
    def omit(d):
        names=path.split('.');obj=d
        for key in names[:-1]:obj=obj[key]
        obj.pop(names[-1])
    for run in (a,b):aa_json(run/'sdlgpu'/name,omit);aa_refresh(run)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result


@pytest.mark.parametrize('field,value',[('vk_query_ok',False),('vk_query_ok',None),('vk_vendor_id',True),
    ('vk_vendor_id',0x1002),('vk_query','display string only')])
def test_r3_aa_coherent_nvidia_display_strings_without_actual_query_refuse(monkeypatch,tmp_path,field,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        aa_json(run/'sdlgpu/d3d9gpu.json',lambda d:aa_put(d,'sdl_gpu.'+field,value));aa_refresh(run)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert any('no measured NVIDIA' in f for f in result['failures']),result


@pytest.mark.parametrize('value',[True,False,None,0,-1,float('nan'),float('inf')])
def test_r3_aa_actual_start_is_finite_positive_and_not_boolean(monkeypatch,tmp_path,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    aa_json(b/'sdlgpu/report.json',lambda d:d.update(t_start_monotonic_s=value));aa_refresh(b)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert any('actual start time' in f for f in result['failures']),result


def test_r3_aa_copied_start_does_not_show_separate_executions(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path)
    aa_json(b/'sdlgpu/report.json',lambda d:d.update(t_start_monotonic_s=100.0));aa_refresh(b)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert any('same execution start' in f for f in result['failures']),result


@pytest.mark.parametrize('same',['literal','directory-link','directory-child'])
def test_r3_aa_same_resolved_directory_is_not_a_repeat(monkeypatch,tmp_path,same):
    a,b=aa_pair(monkeypatch,tmp_path)
    if same=='directory-link':
        b=tmp_path/'alias';b.symlink_to(a,target_is_directory=True)
    elif same=='directory-child':b=a/'..'/'a'
    else:b=a
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert result['failures']==['A/A requires separate physical run directories']


@pytest.mark.parametrize('artifact',['sdlgpu/frames.y4m.times.tsv','sdlgpu/d3d9gpu.keys.json',
    'sdlgpu/report.json','dxvk/frames.y4m.times.tsv'])
@pytest.mark.parametrize('kind',['hardlink','symlink'])
def test_r3_aa_distinct_directory_names_cannot_share_physical_artifacts(monkeypatch,tmp_path,artifact,kind):
    a,b=aa_pair(monkeypatch,tmp_path);dest=b/artifact
    if kind=='hardlink':os.replace(dest,b/(artifact+'.old'));os.link(a/artifact,dest)
    else:os.replace(dest,b/(artifact+'.old'));dest.symlink_to(a/artifact)
    aa_refresh(b);result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert any('share physical' in f for f in result['failures']),result


def test_r3_aa_cross_role_hardlinked_table_is_not_an_independent_repeat(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path);p=b/'sdlgpu/frames.y4m.times.tsv'
    os.replace(p,p.with_name('retained.times'));os.link(a/'dxvk/frames.y4m.times.tsv',p);aa_refresh(b)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert any('share physical output' in f for f in result['failures']),result


@pytest.mark.parametrize('name',['compare.json','sdlgpu/report.json','sdlgpu/d3d9gpu.json'])
def test_r3_aa_external_nonobject_json_refuses_without_crash(monkeypatch,tmp_path,name):
    a,b=aa_pair(monkeypatch,tmp_path);(b/name).write_text('[]')
    if name!='compare.json':aa_refresh(b)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result


def test_r3_aa_nonobject_inputs_refuses_without_crash(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path);aa_json(b/'compare.json',lambda d:d.update(inputs=[]))
    assert CH.check_aa(a,b)['verdict']=='FAIL'


@pytest.mark.parametrize('target',['original','binary','raw-report','shader-keys'])
def test_r3_aa_exact_original_build_and_raw_artifact_bytes_are_bound(monkeypatch,tmp_path,target):
    a,b=aa_pair(monkeypatch,tmp_path)
    p={'original':tmp_path/'original.sa2s','binary':tmp_path/'build/sa2_player_gpu',
       'raw-report':b/'sdlgpu/report.json','shader-keys':b/'sdlgpu/d3d9gpu.keys.json'}[target]
    with p.open('ab') as file:file.write(b'changed bytes')
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result


@pytest.mark.parametrize('where',['a','b','existing','missing-parent'])
def test_r3_aa_cli_never_overwrites_input_evidence_or_existing_output(monkeypatch,tmp_path,where):
    a,b=aa_publication_fixture(monkeypatch,tmp_path)
    out={'a':a/'new-aa.json','b':b/'new-aa.json','existing':tmp_path/'existing.json',
         'missing-parent':tmp_path/'absent/new-aa.json'}[where]
    if where=='existing':out.write_text('held output')
    before={str(p):p.read_bytes() for d in (a,b) for p in d.rglob('*') if p.is_file()}
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2
    assert before=={str(p):p.read_bytes() for d in (a,b) for p in d.rglob('*') if p.is_file()}
    if where=='existing':assert out.read_text()=='held output'
    else:assert not out.exists()


@pytest.mark.parametrize('timing',['during-read','after-recheck'])
def test_r3_aa_artifact_drift_during_verification_cannot_publish_pass(monkeypatch,tmp_path,timing):
    a,b=aa_pair(monkeypatch,tmp_path)
    if timing=='after-recheck':
        real=CH.checked_run
        def move(path,*args,**kwargs):
            result=real(path,*args,**kwargs)
            if path==b:
                with (a/'sdlgpu/frames.y4m.times.tsv').open('a') as file:file.write('\n')
            return result
        monkeypatch.setattr(CH,'checked_run',move)
    else:
        real=CH.load_run;once=[]
        def move(path,*args,**kwargs):
            result=real(path,*args,**kwargs)
            if path==b/'sdlgpu' and not once:
                once.append(True)
                with (path/'frames.y4m.times.tsv').open('a') as file:file.write('\n')
            return result
        monkeypatch.setattr(CH,'load_run',move)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert any('artifacts changed' in f for f in result['failures']),result


@pytest.mark.parametrize('mode',['pass','fail','raw-refusal','runner-note'])
def test_r3_aa_extraction_keeps_ordinary_check_wrapper_bytes(monkeypatch,tmp_path,capsys,mode):
    # The exact retained original CLI and wrapper are a literal output oracle below.
    old=LEGACY_CMD_CHECK
    space=dict(vars(CH));exec(compile(old+'\n\n'+LEGACY_MAIN,'retained-check-cli','exec'),space)
    monkeypatch.setattr(sys.modules[__name__],'compiler',lambda:{'command':'/authored/compiler',
        'version':'authored compiler record','search':['/usr/include']})
    run,build=forged_run_dir(monkeypatch,tmp_path,controls=True)
    if mode=='fail':
        aa_json(run/'sdlgpu/d3d9gpu.json',lambda d:aa_put(d,'present.failures',1));aa_refresh(run)
    if mode=='raw-refusal':(run/'sdlgpu/exit').write_text('4\n')
    if mode=='runner-note':aa_json(run/'compare.json',lambda d:aa_put(d,'inputs.ran_by','0'*64))
    monkeypatch.setattr(CH,'now',lambda:'held-time');space['now']=CH.now
    before=(run/'compare.json').read_bytes()
    capsys.readouterr();old_status=space['main'](['check','--run',str(run)]);old_console=capsys.readouterr();old_bytes=(run/'compare.json').read_bytes()
    (run/'compare.json').write_bytes(before)
    current_status=CH.main(['check','--run',str(run)]);current_console=capsys.readouterr();current_bytes=(run/'compare.json').read_bytes()
    assert (current_status,current_console.out,current_console.err,current_bytes)==(old_status,old_console.out,old_console.err,old_bytes)


# Exact retained ordinary wrapper; the equality control executes this literal.
LEGACY_CMD_CHECK = 'def cmd_check(args: argparse.Namespace) -> int:\n    out_dir = Path(args.run)\n    old = read_json(out_dir / "compare.json") or {}\n    scope, bound = old.get("acceptance_scope"), old.get("max_frames")\n    if scope not in SCOPES or type(bound) is not int or bound < 0 or ((scope == "r2-draws-partial") != (bound > 0)):\n        print("refused: no explicit valid scope and bound in compare.json; rerun it", file=sys.stderr)\n        return 2\n    census = read_json(out_dir / "census.json")\n    if census is None:\n        print(f"refused: no census.json in {out_dir}", file=sys.stderr)\n        return 2\n    if not get(old, "inputs.build"):\n        print(f"refused: no compare.json naming its build in {out_dir}", file=sys.stderr)\n        return 2\n    # The compare_hosts.py that ran the players and the matrix: carried forward, never\n    # replaced by the one checking (the receipt refuses a run whose two differ).\n    ran_by = get(old, "inputs.ran_by")\n    if not ran_by:\n        print(f"refused: the compare.json in {out_dir} names no compare_hosts.py that ran it (inputs.ran_by): "\n              f"rerun the comparison", file=sys.stderr)\n        return 2\n    build = (ROOT / old["inputs"]["build"]).resolve()\n    # The reference the run held (check h), unless --reference names another.\n    held = next((get(c, "detail.reference") for c in old.get("checks") or [] if c.get("check") == "h"), None)\n    ns = argparse.Namespace(stream=str(ROOT / old.get("stream", {}).get("path", census["stream"])),\n                            hidden=old.get("hidden", False), build=str(build), reference=args.reference or held,\n                            scope=old.get("acceptance_scope"), max_frames=old.get("max_frames"))\n    # Recomputed from the files the run left, against the build that made them: the build\n    # must still be the one the run recorded, and still match the tree.\n    pins = pipeline()\n    if not refuse_changed_self(pins):\n        return 2\n    then = {k: v for k, v in (old.get("inputs", {}).get("files") or {}).items() if k in BINARIES}\n    now_ = {k: sha256(build / k) if (build / k).is_file() else None for k in then}\n    if not then or then != now_:\n        print(f"refused: the build in {rel(build)} is not the one the run recorded ({then} then, {now_} now)",\n              file=sys.stderr)\n        return 2\n    if not refuse_unbuilt(build, pins):\n        return 2\n    # ... and the files it reads must be the ones the run wrote.\n    recorded, present = old.get("outputs") or {}, recorded_outputs(out_dir)\n    if not recorded or recorded != present:\n        moved = sorted(k for k in set(recorded) | set(present) if recorded.get(k) != present.get(k))\n        print(f"refused: the files in {out_dir} are not the ones the run recorded (outputs): "\n              f"{[(k, recorded.get(k, \'absent\'), present.get(k, \'absent\')) for k in moved[:6]]}", file=sys.stderr)\n        return 2\n    if scope != "r2a-clears":\n        original = Path(ns.stream)\n        if not original.is_file() or sha256(original) != get(old, "stream.sha256"):\n            print("refused: the original stream no longer matches the recorded hash", file=sys.stderr)\n            return 2\n        try:\n            account = stream_account(original, max_frames=bound)\n        except (OSError, ValueError, stream_dump.StreamError) as exc:\n            print(f"refused: original-stream account: {exc}", file=sys.stderr)\n            return 2\n        if (any(census.get(k) != v for k, v in account.items() if k != "clears")\n                or get(census, "clears.host_calls") != get(account, "clears.host_calls")):\n            print("refused: the saved census differs from the original consumed records and UP layout", file=sys.stderr)\n            return 2\n    dxvk, sdl = load_run(out_dir / "dxvk"), load_run(out_dir / "sdlgpu")\n    # The controls the run ran, by its own record (not by which directories exist).\n    controls, control_runs = None, []\n    if get(old, "controls.run") is True:\n        controls = []\n        for c in controls_for(ns.scope):\n            run = load_run(out_dir / f"control-{c[\'name\']}")\n            control_runs.append(run)\n            controls.append(control_outcome(c, {"census": census, "dxvk": dxvk, "sdl": run, "hidden": ns.hidden,\n                                              "scope": ns.scope, "max_frames": ns.max_frames}))\n    # Each run\'s verdict (check.json) is play_report.py\'s at run time: reused only when that\n    # is the play_report.py this check pins.\n    if (stale := checker_failures([dxvk, sdl, *control_runs], pins)):\n        print("refused: a run\'s check.json was written by another play_report.py than the one this check pins "\n              "(rerun the comparison):", file=sys.stderr)\n        for f in stale[:8]:\n            print(f"  {f}", file=sys.stderr)\n        return 2\n    doc = assemble(out_dir, ns, census, dxvk, sdl, controls, old.get("started", now()), pins, control_runs,\n                   load_matrix(out_dir), ran_by=ran_by)\n    (out_dir / "compare.json").write_text(json.dumps(doc, indent=1) + "\\n")\n    print_summary(doc)\n    if ran_by != pins.get(SELF):\n        print(f"  note: the runs were made by {SELF} {ran_by}, checked by {pins.get(SELF)}: no receipt takes this "\n              f"run (rerun the comparison)")\n    final = doc["verdict_with_controls"] if controls is not None else doc["verdict"]\n    return 0 if final == "PASS" else 1'


def test_r3_aa_actual_pipeline_source_drift_after_recording_refuses(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path)
    # This is an authored source copy under the private candidate ROOT, restored exactly.
    source=ROOT/'host/player/build_record.py';held=source.read_bytes()
    try:
        source.write_bytes(held+b'\n# author-created source drift control\n')
        result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    finally:source.write_bytes(held)


def test_r3_aa_actual_original_source_drift_after_read_refuses(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path);original=tmp_path/'original.sa2s'
    real=CH.checked_run
    def move(path,*args,**kwargs):
        result=real(path,*args,**kwargs)
        if path==b:
            with original.open('ab') as file:file.write(b'after both rechecks')
        return result
    monkeypatch.setattr(CH,'checked_run',move)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    assert 'original source drift during A/A verification' in result['failures'],result


LEGACY_MAIN = 'def main(argv: list[str]) -> int:\n    p = argparse.ArgumentParser(description=__doc__.split("\\n")[0])\n    sub = p.add_subparsers(dest="cmd", required=True)\n    r = sub.add_parser("run")\n    r.add_argument("--stream", required=True)\n    r.add_argument("--stage", type=int, default=3)\n    r.add_argument("--out")\n    r.add_argument("--build", required=True, help="the build to run (host/player/build.sh\'s OUT); no default")\n    r.add_argument("--controls", action="store_true")\n    r.add_argument("--scope", choices=SCOPES, default="r2-draws-full")\n    r.add_argument("--max-frames", type=int, default=0)\n    r.add_argument("--hidden", action="store_true")\n    r.add_argument("--reference")\n    r.add_argument("--allow-notes", action="store_true")\n    c = sub.add_parser("check")\n    c.add_argument("--run", required=True)\n    c.add_argument("--reference")\n    a = sub.add_parser("dxvk-ab")\n    a.add_argument("--before", required=True)\n    a.add_argument("--before-commit", required=True, help="the commit the before build was built from (a clone)")\n    a.add_argument("--after", required=True)\n    a.add_argument("--stream", required=True)\n    a.add_argument("--stage", type=int, default=3)\n    a.add_argument("--out")\n    a.add_argument("--allow-notes", action="store_true")\n    a.add_argument("--hidden", action="store_true")\n    u = sub.add_parser("dxvk-unchanged")\n    u.add_argument("--after", required=True, help="this tree\'s build (both build scripts\' OUT)")\n    u.add_argument("--before-commit", required=True, help="the commit whose DXVK player this build\'s is held to")\n    u.add_argument("--out")\n    rc = sub.add_parser("receipt")\n    rc.add_argument("--run", required=True)\n    rc.add_argument("--id", required=True, help="the receipt\'s id (s5-...-vN): never a default")\n    rc.add_argument("--out", required=True)\n    rc.add_argument("--also", action="append", help="another stream\'s run (no controls) by the same pipeline")\n    args = p.parse_args(argv)\n    if args.cmd == "run" and ((args.scope == "r2-draws-partial") != (args.max_frames > 0) or args.max_frames < 0):\n        print("usage: partial scope requires --max-frames N > 0; other scopes require no bound", file=sys.stderr)\n        return 2\n    if args.cmd in ("run", "dxvk-ab") and not 0 <= args.stage <= 3:\n        print("usage: --stage 0..3", file=sys.stderr)\n        return 2\n    return {"run": cmd_run, "check": cmd_check, "dxvk-ab": cmd_dxvk_ab, "dxvk-unchanged": cmd_dxvk_unchanged,\n            "receipt": cmd_receipt}[args.cmd](args)'


@pytest.mark.parametrize('target',['binary','record','reference','generated','depfile'])
def test_r3_aa_actual_late_build_and_reference_drift_refuses(monkeypatch,tmp_path,target):
    a,b=aa_pair(monkeypatch,tmp_path);real=CH.checked_run
    path={'binary':tmp_path/'build/sa2_player_gpu','record':tmp_path/'build/sa2_player_gpu.json',
        'reference':tmp_path/'reference.tsv','generated':tmp_path/'build/expected-gpu/expectations.json',
        'depfile':tmp_path/'build/obj/sa2_player_gpu/forged.d'}[target]
    def drift(run,*args,**kwargs):
        result=real(run,*args,**kwargs)
        if run==b:
            with path.open('ab') as file:file.write(b'\n')
        return result
    monkeypatch.setattr(CH,'checked_run',drift)
    result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
    expected='registered reference drift' if target=='reference' else 'build inputs or outputs drift'
    assert any(expected in f for f in result['failures']),result


def test_r3_aa_huge_json_start_refuses_without_numeric_overflow(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path)
    aa_json(b/'sdlgpu/report.json',lambda d:d.update(t_start_monotonic_s=10**1000));aa_refresh(b)
    assert CH.check_aa(a,b)['verdict']=='FAIL'


def test_r3_aa_actual_late_pipeline_source_drift_refuses(monkeypatch,tmp_path):
    a,b=aa_pair(monkeypatch,tmp_path);real=CH.checked_run
    source=ROOT/'host/player/build_record.py';held=source.read_bytes()
    def drift(path,*args,**kwargs):
        result=real(path,*args,**kwargs)
        if path==b:source.write_bytes(held+b'\n# late authored source drift\n')
        return result
    monkeypatch.setattr(CH,'checked_run',drift)
    try:
        result=CH.check_aa(a,b);assert result['verdict']=='FAIL',result
        assert 'pipeline source drift during A/A verification' in result['failures'],result
    finally:source.write_bytes(held)


@pytest.mark.parametrize('field,value',[('api_version',None),('api_version',''),('features',None),
    ('features','robustBufferAccess'),('extensions',None)])
def test_r3_aa_equal_absent_or_untyped_measured_vulkan_options_refuse(monkeypatch,tmp_path,field,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):aa_json(run/'sdlgpu/d3d9gpu.json',lambda d:aa_put(d,'sdl_gpu.vulkan_options.'+field,value));aa_refresh(run)
    assert CH.check_aa(a,b)['verdict']=='FAIL'


@pytest.mark.parametrize('target',['control-device','control-checker','matrix','stored-control-pass'])
def test_r3_aa_independent_controls_and_matrix_are_recomputed_from_raw_evidence(monkeypatch,tmp_path,target):
    a,b=aa_pair(monkeypatch,tmp_path)
    if target=='control-device':
        aa_json(b/'control-geometry-noop/d3d9gpu.json',lambda d:aa_put(d,'draws.drawn',1))
    elif target=='control-checker':(b/'control-geometry-noop'/CH.CHECKED_BY).write_text('f'*64+'\n')
    elif target=='matrix':(b/'matrix/cpu.tsv').write_text('missing matrix\n')
    else:
        aa_json(b/'control-count-draw/d3d9gpu.json',lambda d:aa_put(d,'draws.fault_uncounted',0))
        aa_json(b/'compare.json',lambda d:d.update(verdict='PASS',verdict_with_controls='PASS',marker='R2_DRAW_OK'))
    aa_refresh(b)
    assert CH.check_aa(a,b)['verdict']=='FAIL'


@pytest.mark.parametrize('field',['pace','speed','vsync','hidden','max_frames','allow_truncated',
    'allow_notes','stats_only','report_every'])
def test_r3_aa_fold_coherent_missing_authoritative_player_option_refuses(monkeypatch,tmp_path,field):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        for host in ('dxvk','sdlgpu'):
            aa_json(run/host/'report.json',lambda d:d['options'].pop(field))
        aa_refresh(run)
    result=CH.check_aa(a,b)
    (tmp_path/'actual-audit.json').write_text(json.dumps(result,indent=1)+'\n')
    assert result['verdict']=='FAIL',result
    assert any('incomplete or invalid player options' in f for f in result['failures']),result


@pytest.mark.parametrize('field',['pace','vsync','hidden','allow_truncated','allow_notes','stats_only'])
@pytest.mark.parametrize('value',[0,1,None,'false',float('nan')])
def test_r3_aa_fold_authoritative_boolean_options_are_exact_booleans(monkeypatch,tmp_path,field,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        for host in ('dxvk','sdlgpu'):aa_json(run/host/'report.json',lambda d:aa_put(d,'options.'+field,value))
        aa_refresh(run)
    assert CH.check_aa(a,b)['verdict']=='FAIL'


@pytest.mark.parametrize('field',['max_frames','report_every'])
@pytest.mark.parametrize('value',[False,True,-1,2**32,0.0,None,float('nan'),float('inf')])
def test_r3_aa_fold_authoritative_count_options_are_uint32(monkeypatch,tmp_path,field,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        for host in ('dxvk','sdlgpu'):aa_json(run/host/'report.json',lambda d:aa_put(d,'options.'+field,value))
        aa_refresh(run)
    assert CH.check_aa(a,b)['verdict']=='FAIL'


@pytest.mark.parametrize('value',[True,False,None,'1',0,-1,float('nan'),float('inf'),-float('inf'),10**1000])
def test_r3_aa_fold_speed_is_finite_positive_json_number(monkeypatch,tmp_path,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        for host in ('dxvk','sdlgpu'):aa_json(run/host/'report.json',lambda d:aa_put(d,'options.speed',value))
        aa_refresh(run)
    assert CH.check_aa(a,b)['verdict']=='FAIL'


@pytest.mark.parametrize('field,value',[('pace',True),('vsync',True),('speed',2.0),('report_every',0),
    ('new_actual_option',{'kind':'authored','value':1})])
def test_r3_aa_fold_every_actual_option_including_extras_remains_compared(monkeypatch,tmp_path,field,value):
    a,b=aa_pair(monkeypatch,tmp_path)
    for host in ('dxvk','sdlgpu'):aa_json(b/host/'report.json',lambda d:d['options'].update({field:value}))
    aa_refresh(b)
    assert CH.check_aa(a,b)['verdict']=='FAIL'


@pytest.mark.parametrize('speed',[1,1.0,0.25,2])
def test_r3_aa_fold_valid_producer_options_and_matching_extras_pass(monkeypatch,tmp_path,speed):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        for host in ('dxvk','sdlgpu'):
            aa_json(run/host/'report.json',lambda d:d['options'].update(speed=speed,report_every=0,
                new_actual_option={'kind':'authored','value':1}))
        aa_refresh(run)
    assert CH.check_aa(a,b)['verdict']=='PASS'


@pytest.mark.parametrize('vsync,interval',[(False,'IMMEDIATE'),(True,'ONE')])
def test_r3_aa_fold_recorded_vsync_and_created_interval_consistent_pairs_pass(monkeypatch,tmp_path,vsync,interval):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        for host in ('dxvk','sdlgpu'):
            aa_json(run/host/'report.json',lambda d:(d['options'].update(vsync=vsync),d['device'].update(interval=interval)))
        aa_refresh(run)
    result=CH.check_aa(a,b)
    (tmp_path/'actual-vsync-audit.json').write_text(json.dumps(result,indent=1)+'\n')
    assert result['verdict']=='PASS',result


@pytest.mark.parametrize('vsync,interval',[(False,'ONE'),(True,'IMMEDIATE')])
def test_r3_aa_fold_recorded_vsync_and_created_interval_contradictions_refuse(monkeypatch,tmp_path,vsync,interval):
    a,b=aa_pair(monkeypatch,tmp_path)
    for run in (a,b):
        for host in ('dxvk','sdlgpu'):
            aa_json(run/host/'report.json',lambda d:(d['options'].update(vsync=vsync),d['device'].update(interval=interval)))
        aa_refresh(run)
    result=CH.check_aa(a,b)
    (tmp_path/'actual-vsync-audit.json').write_text(json.dumps(result,indent=1)+'\n')
    assert result['verdict']=='FAIL',result
    assert any('vsync option contradicts the created device interval' in f for f in result['failures']),result

# R4 registered full original MH. Tiny original/reference substitutions below are
# explicit CPU policy fixtures; they do not establish the actual 15,709-frame run.
def r4_run(monkeypatch,tmp_path):
    held_boot=CH.BOOT_STREAM_SHA256
    a,b=aa_pair(monkeypatch,tmp_path)
    fixture=tmp_path
    build = fixture/'build'
    library=fixture/'literal-libSDL3.so';library.write_bytes(b'authored SDL runtime library')
    dxvk=fixture/'literal-dxvk.so';dxvk.write_bytes(b'authored DXVK runtime library')
    config=fixture/'literal-dxvk.conf';config.write_bytes(b'authored DXVK config')
    for run in (a,b):
        for role in ('dxvk','sdlgpu','control-geometry-noop','control-count-draw'):
            path=run/role/'report.json';report=json.loads(path.read_text())
            binary=build/('sa2_player' if role=='dxvk' else 'sa2_player_gpu')
            report['files']['player']['real_path']=str(binary)
            report['files']['libSDL3']={'real_path':str(library),'path':str(library),'sha256':CH.sha256(library)}
            report['host_identity']['expected_sdl_sha256']=CH.sha256(library)
            if role=='dxvk':
                report['files']['libdxvk_d3d9']={'real_path':str(dxvk),'path':str(dxvk),'sha256':CH.sha256(dxvk)}
                report['files']['dxvk_conf']={'real_path':str(config),'path':str(config),'sha256':CH.sha256(config)}
                report['host_identity'].update(expected_dxvk_sha256=CH.sha256(dxvk),expected_config_sha256=CH.sha256(config))
            path.write_text(json.dumps(report))
            device=run/role/'d3d9gpu.json'
            if device.exists():
                d=json.loads(device.read_text());d['library']['path']=str(binary);device.write_text(json.dumps(d))
    header = tmp_path/'literal-external.h';header.write_bytes(b'authored external header')
    record = build/'sa2_player_gpu.json';rec=json.loads(record.read_text())
    depname=next(iter(rec['depfiles']));dep=build/depname
    dep.write_text(dep.read_text().rstrip()+' '+str(header)+'\n')
    rec['depfiles'][depname]=CH.sha256(dep);rec['external']={str(header):CH.sha256(header)}
    record.write_text(json.dumps(rec))
    for run in (a,b):
        matrix=run/'matrix/run.json';m=json.loads(matrix.read_text())
        m['dxvk_library']={'path':str(dxvk),'sha256':CH.sha256(dxvk)};matrix.write_text(json.dumps(m))
        saved=json.loads((run/'compare.json').read_text())
        c=json.loads((run/'census.json').read_text());dx=CH.load_run(run/'dxvk');sd=CH.load_run(run/'sdlgpu')
        controls=[CH.load_run(run/('control-'+control['name'])) for control in CH.R2_CONTROLS]
        outcomes=[CH.control_outcome(control,{'census':c,'dxvk':dx,'sdl':data,'hidden':False,
            'scope':'r2-draws-full','max_frames':0}) for control,data in zip(CH.R2_CONTROLS,controls)]
        args=argparse.Namespace(build=str(build),stream=str(CH.ROOT/saved['stream']['path']),hidden=False,
            reference=None,scope='r2-draws-full',max_frames=0)
        doc=CH.assemble(run,args,c,dx,sd,outcomes,'authored-final-physical',control_runs=controls,matrix=CH.load_matrix(run))
        assert doc['marker']=='R2_DRAW_OK',doc
        (run/'compare.json').write_text(json.dumps(doc))
    assert CH.check_aa(a,b)['verdict']=='PASS'
    original=tmp_path/'original.sa2s';reference=tmp_path/'reference.tsv'
    policy=dict(CH.METAL_HARBOR,stream_sha256=CH.sha256(original),reference=str(reference),
                reference_sha256=CH.sha256(reference),frames=1)
    monkeypatch.setattr(CH,'METAL_HARBOR',policy)
    monkeypatch.setattr(CH,'BOOT_STREAM_SHA256',held_boot)
    for run in (a,b):
        status,doc=CH.checked_run(run)
        assert status==0 and doc['marker']=='R4_METALHARBOR_OK',doc
        (run/'compare.json').write_text(json.dumps(doc))
    return a


def r4_checked(run):
    status,doc=CH.checked_run(run)
    return status,doc,CH.acceptance_marker(doc) if doc else None


def test_r4_authority_is_exact_adopted_original_and_reference():
    assert CH.METAL_HARBOR=={'name':'metalharbor','stage':3,'frames':15709,'marker':'R4_METALHARBOR_OK',
        'stream_sha256':'f7f6b4e18ed1a0a117fa2701e12c0092b86d3887ec4b3e82a1d40cdfecee2e53',
        'reference':'private/generated/s5/pushC/fresh-picture/frames.y4m.times.tsv',
        'reference_sha256':'f4864c3d5d0b210b60ead83da1086d9bf30618199228228b6b603927921e024b'}


def test_r4_positive_actual_recheck_and_owned_receipt_bind_registration(monkeypatch,tmp_path):
    run=r4_run(monkeypatch,tmp_path)
    status,doc,marker=r4_checked(run)
    assert (status,marker)==(0,'R4_METALHARBOR_OK')
    assert doc['census']['frames']==1 and doc['census']['consumed_records']['end']==1
    assert CH.receipt_refusal(doc,str(run)) is None
    output=tmp_path/'owned-r4.json'
    assert CH.main(['receipt','--run',str(run),'--id','s5-host2-r4-v1','--out',str(output)])==0
    receipt=json.loads(output.read_text())
    assert receipt['marker']=='R4_METALHARBOR_OK' and receipt['verdict']=='PASS'
    assert receipt['inputs']['registered_replay']==CH.METAL_HARBOR
    assert receipt['counts']['frames']==1
    assert 'registered original Metal Harbor' in receipt['question']


@pytest.mark.parametrize('field,value',[('bgrx_hash','0x000000000000000c'),('seq','7'),
    ('virtual_us','16667'),('guest_frames','2'),('polls','1'),('frame','2')])
def test_r4_coherent_both_host_row_corruption_cannot_replace_held_reference(monkeypatch,tmp_path,field,value):
    run=r4_run(monkeypatch,tmp_path)
    for host in ('dxvk','sdlgpu'):aa_tsv(run/host/'frames.y4m.times.tsv',lambda rows:rows[0].update({field:value}))
    aa_tsv(run/'control-count-draw/frames.y4m.times.tsv',lambda rows:rows[0].update({field:value}))
    if field!='bgrx_hash':aa_tsv(run/'control-geometry-noop/frames.y4m.times.tsv',lambda rows:rows[0].update({field:value}))
    aa_refresh(run);status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL',doc
    h=next(c for c in doc['checks'] if c['check']=='h')
    assert not h['pass'] and any('differ from the reference' in f for f in h['failures'])
    if field!='frame':assert next(c for c in doc['checks'] if c['check']=='e')['pass']


def test_r4_arbitrary_coherent_custom_reference_cannot_replace_registered_bytes(monkeypatch,tmp_path):
    run=r4_run(monkeypatch,tmp_path)
    for host in ('dxvk','sdlgpu'):
        aa_tsv(run/host/'frames.y4m.times.tsv',lambda rows:rows[0].update(bgrx_hash='0x000000000000000c'))
    aa_tsv(run/'control-count-draw/frames.y4m.times.tsv',lambda rows:rows[0].update(bgrx_hash='0x000000000000000c'))
    custom=tmp_path/'custom.tsv';custom.write_bytes((run/'dxvk/frames.y4m.times.tsv').read_bytes())
    aa_json(run/'compare.json',lambda d:next(c for c in d['checks'] if c['check']=='h')['detail'].update(reference=str(custom)))
    aa_refresh(run);status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL'
    assert next(c for c in doc['checks'] if c['check']=='e')['pass']
    assert any('custom reference cannot replace' in f for c in doc['checks'] for f in c['failures'])


def test_r4_missing_reference_is_a_failed_named_check(monkeypatch,tmp_path):
    run=r4_run(monkeypatch,tmp_path)
    reference=tmp_path/'reference.tsv';reference.rename(tmp_path/'retained-reference.tsv')
    status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL'
    assert any(c['check']=='h' and not c['pass'] for c in doc['checks'])


@pytest.mark.parametrize('fact,value',[('frames',0),('frames',2),('frames',True),('stage',2)])
def test_r4_named_denominator_and_stage_cannot_be_loosened(monkeypatch,tmp_path,fact,value):
    run=r4_run(monkeypatch,tmp_path)
    aa_json(run/'census.json',lambda d:d.update({fact:value}));aa_refresh(run)
    status,doc,marker=r4_checked(run)
    assert status!=0 and marker!='R4_METALHARBOR_OK'


@pytest.mark.parametrize('field,value',[('stop','--max-frames'),('complete',False),('records.end',0),
    ('stream_records_read_to_end',False),('stream_sha256_after_stop',True),('options.max_frames',1),
    ('options.allow_truncated',True),('options.stats_only',True),('errors',1),('readback_failures',1),
    ('present_failures',1),('calls.not_handled',1),('host_failures.count',1),('textures.placeholder_binds',1),
    ('device.created',False),('device.failure','failed')])
def test_r4_original_completion_and_errors_cannot_be_saved_as_registered_pass(monkeypatch,tmp_path,field,value):
    run=r4_run(monkeypatch,tmp_path)
    for host in ('dxvk','sdlgpu'):aa_json(run/host/'report.json',lambda d:aa_put(d,field,value))
    aa_refresh(run);status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL',doc


@pytest.mark.parametrize('field',['pace','speed','vsync','hidden','max_frames','allow_truncated',
    'allow_notes','stats_only','report_every'])
def test_r4_coherent_missing_player_option_refuses_registration(monkeypatch,tmp_path,field):
    run=r4_run(monkeypatch,tmp_path)
    for host in ('dxvk','sdlgpu'):aa_json(run/host/'report.json',lambda d:d['options'].pop(field))
    aa_refresh(run);status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL'


@pytest.mark.parametrize('field,value',[('vsync',1),('report_every',True),('report_every',2**32),
    ('speed',True),('speed',float('nan')),('speed',10**1000)])
def test_r4_invalid_player_types_refuse_registration(monkeypatch,tmp_path,field,value):
    run=r4_run(monkeypatch,tmp_path)
    for host in ('dxvk','sdlgpu'):aa_json(run/host/'report.json',lambda d:aa_put(d,'options.'+field,value))
    aa_refresh(run);status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL'


@pytest.mark.parametrize('vsync,interval,want',[(False,'IMMEDIATE',True),(True,'ONE',True),
    (False,'ONE',False),(True,'IMMEDIATE',False)])
def test_r4_current_vsync_and_created_interval_contract(monkeypatch,tmp_path,vsync,interval,want):
    run=r4_run(monkeypatch,tmp_path)
    for host in ('dxvk','sdlgpu'):
        aa_json(run/host/'report.json',lambda d:(d['options'].update(vsync=vsync),d['device'].update(interval=interval)))
    aa_refresh(run);status,doc,marker=r4_checked(run)
    assert (marker=='R4_METALHARBOR_OK') is want,doc


@pytest.mark.parametrize('target,field,value',[('report.json','options.pace',True),
    ('report.json','env.DXVK_CONFIG','different'),('report.json','device.guest_multisample',4),
    ('d3d9gpu.json','sdl_gpu.vk_query_ok',False),('d3d9gpu.json','sdl_gpu.vk_vendor_id',True),
    ('d3d9gpu.json','sdl_gpu.vk_vendor_id',0x1002),('d3d9gpu.json','sdl_gpu.vk_query','display-only'),
    ('d3d9gpu.json','sdl_gpu.vulkan_options.api_version','')])
def test_r4_settings_and_queried_nvidia_identity_refuse_drift(monkeypatch,tmp_path,target,field,value):
    run=r4_run(monkeypatch,tmp_path)
    aa_json(run/'sdlgpu'/target,lambda d:aa_put(d,field,value));aa_refresh(run)
    status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL',doc


@pytest.mark.parametrize('field',['vk_query','vulkan_options','sdl_revision','fallback_owed'])
def test_r4_incomplete_measured_gpu_settings_refuse(monkeypatch,tmp_path,field):
    run=r4_run(monkeypatch,tmp_path)
    aa_json(run/'sdlgpu/d3d9gpu.json',lambda d:d['sdl_gpu'].pop(field));aa_refresh(run)
    status,doc,marker=r4_checked(run)
    assert status==1 and marker=='R2_FAIL'


@pytest.mark.parametrize('target',['missing-END','tail','binary','control','suppression','missing-control'])
def test_r4_original_provenance_and_omission_evidence_are_rechecked(monkeypatch,tmp_path,target):
    run=r4_run(monkeypatch,tmp_path)
    if target=='missing-END':
        p=tmp_path/'original.sa2s';p.write_bytes(p.read_bytes()[:-16])
    elif target=='tail':
        with (tmp_path/'original.sa2s').open('ab') as file:file.write(b'after END')
    elif target=='binary':
        with (tmp_path/'build/sa2_player_gpu').open('ab') as file:file.write(b'changed executable')
    elif target=='control':
        aa_json(run/'control-count-draw/d3d9gpu.json',lambda d:aa_put(d,'draws.fault_uncounted',0));aa_refresh(run)
    elif target=='suppression':
        aa_json(run/'sdlgpu/d3d9gpu.json',lambda d:aa_put(d,'draws.suppressed_count',1));aa_refresh(run)
    else:
        p=run/'control-count-draw/report.json';p.rename(p.with_name('retained-report.json'));aa_refresh(run)
    status,doc,marker=r4_checked(run)
    assert status!=0 and marker!='R4_METALHARBOR_OK'


def test_r4_saved_pass_receipt_still_rechecks_actual_reference_bytes(monkeypatch,tmp_path):
    run=r4_run(monkeypatch,tmp_path)
    with (tmp_path/'reference.tsv').open('ab') as file:file.write(b'\n')
    output=tmp_path/'must-not-publish.json'
    assert CH.main(['receipt','--run',str(run),'--id','s5-host2-r4-v1','--out',str(output)])!=0
    assert not output.exists()


@pytest.mark.parametrize('kind',['absent-h','wrong-reference','wrong-count','missing-settings','wrong-query',
    'wrong-options','partial','bound','wrong-stage','missing-END'])
def test_r4_serialized_marker_and_receipt_share_fail_closed_registration(monkeypatch,tmp_path,kind):
    run=r4_run(monkeypatch,tmp_path);doc=json.loads((run/'compare.json').read_text())
    h=next(c for c in doc['checks'] if c['check']=='h');g=next(c for c in doc['checks'] if c['check']=='g')
    if kind=='absent-h':doc['checks'].remove(h)
    elif kind=='wrong-reference':h['detail']['sha256']='f'*64
    elif kind=='wrong-count':h['detail']['rows']=True
    elif kind=='missing-settings':h['detail'].pop('player_settings')
    elif kind=='wrong-query':g['detail']['sdl_gpu']['vk_query_ok']=False
    elif kind=='wrong-options':h['detail']['player_settings']['sdlgpu']['options'].pop('vsync')
    elif kind=='partial':doc['acceptance_scope']='r2-draws-partial'
    elif kind=='bound':doc['max_frames']=1
    elif kind=='wrong-stage':doc['stage']=2
    else:doc['census']['consumed_records']['end']=0
    assert CH.acceptance_marker(doc)=='R2_FAIL',doc
    assert CH.receipt_refusal(doc,str(run)) is not None


@pytest.mark.parametrize('stream_hash',['a'*64,'08de946ebb102ff45a68f6307fab6f526598c20d092443218e92b8bac0c42ea3'])
def test_r4_other_full_stream_identities_keep_generic_marker(monkeypatch,tmp_path,stream_hash):
    run=r4_run(monkeypatch,tmp_path);doc=json.loads((run/'compare.json').read_text())
    doc['stream']['sha256']=stream_hash
    for rep in doc['runs'].values():rep['stream_sha256']=stream_hash
    assert CH.acceptance_marker(doc)=='R2_STREAM_GEOMETRY_OK'


def test_r4_reference_selection_uses_one_exact_registration(monkeypatch,tmp_path):
    run=r4_run(monkeypatch,tmp_path)
    selected=CH.reference_for({'stream_sha256':CH.METAL_HARBOR['stream_sha256'],'stage':3},None)
    assert selected['sha256']==CH.METAL_HARBOR['reference_sha256'] and len(selected['rows'])==1
    assert CH.reference_for({'stream_sha256':'a'*64,'stage':3},None) is None
    assert CH.reference_for({'stream_sha256':CH.METAL_HARBOR['stream_sha256'],'stage':2},None) is None


@pytest.mark.parametrize('mutation',['reordered','duplicate','short','invalid-seq','missing-hash'])
def test_r4_literal_multiframe_row_boundary_refuses_corruption(monkeypatch,tmp_path,mutation):
    # Pure row-boundary fixture: two authored rows and forged census count.
    # No full replay/marker is obtained from these isolated e/h calls.
    run=r4_run(monkeypatch,tmp_path)
    first=dict(CH.load_run(run/'dxvk')['times'][0])
    second=dict(first,frame='2',seq='8',virtual_us='33332',guest_frames='2',wall_us='12',
                bgrx_hash='0x000000000000000c')
    reference=tmp_path/'two-row-reference.tsv'
    reference.write_text('\t'.join(first)+'\n'+''.join('\t'.join(r[k] for k in first)+'\n' for r in (first,second)))
    monkeypatch.setattr(CH,'METAL_HARBOR',dict(CH.METAL_HARBOR,frames=2,
        reference=str(reference),reference_sha256=CH.sha256(reference)))
    census_doc=json.loads((run/'census.json').read_text());census_doc['frames']=2
    dx,sd=CH.load_run(run/'dxvk'),CH.load_run(run/'sdlgpu')
    dx['times']=copy.deepcopy([first,second]);sd['times']=copy.deepcopy([first,second])
    ev={'census':census_doc,'dxvk':dx,'sdl':sd,'scope':'r2-draws-full','max_frames':0,
        'reference':CH.reference_for(census_doc,None)}
    assert CH.check_e(ev)['pass'] and CH.check_h(ev)['pass']
    changed=copy.deepcopy([first,second])
    if mutation=='reordered':changed.reverse()
    elif mutation=='duplicate':changed[1]=dict(first)
    elif mutation=='short':changed.pop()
    elif mutation=='invalid-seq':changed[1]['seq']='invalid'
    else:changed[1].pop('bgrx_hash')
    dx['times']=copy.deepcopy(changed);sd['times']=copy.deepcopy(changed)
    assert not CH.check_e(ev)['pass'] and not CH.check_h(ev)['pass']


def publication_fixture(monkeypatch, tmp_path):
    """Actual production validator/writer on literal CPU specimens.

    Original four-frame stream/reference policy, compiler, binaries, runtime
    library metadata and reports are authored stand-ins. No real compiler/GPU.
    All late mutations happen in a dedicated copy, never the repository root.
    """
    original_root = ROOT
    fixture = tmp_path / "publication-root"
    fixture.mkdir()
    for name in ["AGENTS.md", *CH.pipeline()]:
        destination = fixture / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original_root / name, destination)
    monkeypatch.setattr(CH, "ROOT", fixture)
    monkeypatch.setitem(globals(), "ROOT", fixture)
    monkeypatch.setattr(sys.modules[__name__], "compiler", lambda: {"command": "literal-forged-cxx", "version": "literal-forged-cxx 1", "include_search": []})
    T = sys.modules[__name__]
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    put_json = lambda p, d: p.write_text(json.dumps(d, indent=1) + "\n")
    load = lambda p: json.loads(p.read_text())
    runtime = fixture / "runtime"
    runtime.mkdir()
    for name in ("libSDL3", "libdxvk_d3d9", "dxvk_conf"):
        (runtime / name).write_bytes(b"authored runtime identity specimen")
    monkeypatch.setattr(CH, "BOOT_STREAM_SHA256", CH.BOOT_STREAM_SHA256)
    monkeypatch.setattr(CH, "BOOT_REFERENCE_SHA256", CH.BOOT_REFERENCE_SHA256)
    monkeypatch.setattr(CH, "REFERENCES", CH.REFERENCES)
    stream=T.stream([T.device(),*T.decl(),T.clear(),T.up(2),T.up(2),T.up(2),*T.present(1),T.clear(),*T.present(2),T.clear(),*T.present(3),T.up(2),T.up(2),T.call(1,87,[0]),T.up(2,ptype=6),*T.present(4),(5,b'')],fixture/'boot.sa2s',header=T.account_header(False))
    account=CH.stream_account(stream)
    assert account['draws']=={'total':6,'skipped_no_shader_route':{'DrawPrimitiveUP':{'TRIANGLESTRIP':5}},'refused':{'DrawPrimitiveUP':{'TRIANGLEFAN':1}},'empty':{},'per_frame':[[1,1,3],[2,3,0],[4,4,3]],'after_last_present':0}, account
    assert account['accepted_per_frame']==[[1,1,3],[2,3,0],[4,4,2]]
    CH.BOOT_STREAM_SHA256=sha(stream)
    # Pin substitutions are test-only module state; the production source is copied unchanged.
    rows=[{'frame':str(i),'seq':str((7,10,13,19)[i-1]),'virtual_us':str((i-1)*16666),'guest_frames':str(i-1),'polls':'0','wall_us':str(i*79),'bgrx_hash':h} for i,h in enumerate(['0x0000000000000001','0x000000000000000b','0x000000000000000b','0x0000000000000004'],1)]
    def write_times(path,rs):
     cols=['frame','seq','virtual_us','guest_frames','polls','wall_us','bgrx_hash']; path.write_text('\t'.join(cols)+'\n'+''.join('\t'.join(r[c] for c in cols)+'\n' for r in rs))
    reference=fixture/'reference.tsv'; write_times(reference,rows); CH.BOOT_REFERENCE_SHA256=sha(reference); CH.REFERENCES={(CH.BOOT_STREAM_SHA256,3):str(reference)}
    ev=T.r2_evidence(); clears=ev['census']['clears']; ev['census'].update(account); ev['census']['clears']=clears|account['clears']; ev['census'].update(stream=str(stream),stream_sha256=sha(stream)); ev['census']['device']['back_buffer']=[1280,720,21]
    for role in ('dxvk','sdl'):
     ev[role]['times']=copy.deepcopy(rows)
     ev[role]['report'].update(stream_sha256=sha(stream),stream_bytes=account['consumed_bytes'],records=account['consumed_records'])
    build=T.forged_build(fixture/'bin')
    external = fixture / 'private/header.h'
    external.parent.mkdir()
    external.write_text('authored external header')
    record_path = build / 'sa2_player.json'
    record = load(record_path)
    depfile = build / next(iter(record['depfiles']))
    depfile.write_text(depfile.read_text().rstrip() + ' ' + str(external) + '\n')
    record['depfiles'][str(depfile.relative_to(build))] = sha(depfile)
    record['external'][str(external)] = sha(external)
    put_json(record_path, record)
    env={'DXVK_WSI_DRIVER':'SDL3','DXVK_CONFIG_FILE':'same/dxvk.conf','DXVK_CONFIG':'','DXVK_LOG_PATH':'','DXVK_LOG_LEVEL':'info','DXVK_SHADER_CACHE':'0','DXVK_SHADER_CACHE_PATH':'','DXVK_HUD':'','DXVK_FILTER_DEVICE_NAME':'','DXVK_FILTER_DEVICE_UUID':'','DXVK_FORCE_WINDOWED':'','SDL_NO_SIGNAL_HANDLERS':'1','SDL_VIDEO_DRIVER':'','VK_LOADER_LAYERS_DISABLE':'~implicit~','VK_LOADER_LAYERS_ENABLE':'','VK_INSTANCE_LAYERS':'','VK_DRIVER_FILES':'same/nvidia.json','VK_ICD_FILENAMES':'','SA2_DXVK_D3D9':'same/libdxvk.so','SA2_PLAY_OUT':''}
    device_env={'SA2_D3D9GPU_REPORT':'','SA2_D3D9GPU_FAULT':'','SA2_D3D9GPU_PRESENT':'','SA2_D3D9GPU_DEBUG':'','SA2_D3D9GPU_CAPS_OFF':'','SA2_D3D9GPU_TEST_RUN':'','SA2_D3D9GPU_SHADER_DUMP':'','SA2_D3D9GPU_SHADER_FAULT':'','SDL_VIDEO_DRIVER':'','VK_DRIVER_FILES':'same/nvidia.json','VK_ICD_FILENAMES':'','VK_LOADER_LAYERS_DISABLE':'~implicit~','VK_LOADER_LAYERS_ENABLE':'','VK_INSTANCE_LAYERS':'','SDL_ASSERT':''}
    def write_role(run,role,evidence,start):
     d=run/role; (d/'dxvk-log').mkdir(parents=True)
     rep=copy.deepcopy(evidence['report']); rep.update(t_start_monotonic_s=start,host={'sdl_video_driver':'wayland','live_desktop_session':True},env=copy.deepcopy(env))
     
     for key,item in rep['files'].items():
      if isinstance(item,dict):
       actual=build/('sa2_player' if role=='dxvk' else 'sa2_player_gpu') if key=='player' else runtime/key
       item['real_path']=str(actual);item['path']=str(actual)
     rep['files']['libSDL3']={'path':str(runtime/'libSDL3'),'real_path':str(runtime/'libSDL3'),'sha256':'1'*64}; rep['host_identity'].update(sdl_revision='SDL-release',sdl_version=3004016,expected_sdl_sha256='1'*64)
     rep['options'].update(pace=False,speed=1,vsync=False,allow_notes=False,report_every=100)
     rep['device'].update(failure=None,back_buffer=[1280,720,21],auto_depth=75,interval='IMMEDIATE',guest_behavior='0x00000040',guest_multisample=0,guest_device=f'0x{T.DEV:08x}',guest_back_buffer=f'0x{T.BB:08x}',guest_depth=f'0x{T.DS:08x}')
     rep['frames_out'].update(path=str(d/'frames.y4m'),times=str(d/'frames.y4m.times.tsv'))
     rep['env'].update(DXVK_LOG_PATH=str(d/'dxvk-log'),DXVK_SHADER_CACHE_PATH=str(run/'play-out/dxvk-cache'),SA2_PLAY_OUT=str(run/'play-out'))
     put_json(d/'report.json',rep); put_json(d/'check.json',{'pass':True}); (d/CH.CHECKED_BY).write_text(T.CHECKER+'\n'); (d/'exit').write_text('0\n'); write_times(d/'frames.y4m.times.tsv',evidence['times'])
     if evidence['device'] is not None:
      dev=copy.deepcopy(evidence['device']); dev['library']['path']=str(build/'sa2_player_gpu'); dev['sdl_gpu'].update(vk_query_ok=True,vk_query='vkGetPhysicalDeviceProperties',vk_vendor_id=4318,vk_device_id=11141,shader_formats='spirv',depth_format='D24_UNORM_S8_UINT',vulkan_options={'api_version':'1.3','features':['robustBufferAccess','shaderInt16'],'extensions':[]},sdl_revision='SDL-release',sdl_patched=True,sdl_capabilities_present=True,detected={'sampler_clamp_to_border':True,'depth_clip_and_clamp':True},capability_override=None,sampler_clamp_to_border=True,depth_clip_and_clamp=True,fallback_owed={'border':False,'depth_clamp':False})
      keys=d/'d3d9gpu.keys.json'; put_json(keys,{'literal_review_shader_keys':['a']}); dev['pipeline']={'keys_file':str(keys),'keys_sha256':sha(keys),'compiled_formats':['spirv']}; dev['env']=copy.deepcopy(device_env); dev['env']['SA2_D3D9GPU_REPORT']=str(d/'d3d9gpu.json'); put_json(d/'d3d9gpu.json',dev)
     if role=='dxvk': (d/'dxvk-log/sa2_player_d3d9.log').write_text(T.DXVK_LOG)
    def refresh(run):
     cd=load(run/'census.json'); dx=CH.load_run(run/'dxvk'); sd=CH.load_run(run/'sdlgpu'); controls=[CH.load_run(run/f"control-{c['name']}") for c in CH.R2_CONTROLS]
     outcomes=[CH.control_outcome(c,{'census':cd,'dxvk':dx,'sdl':r,'hidden':False,'scope':'r2-draws-full','max_frames':0}) for c,r in zip(CH.R2_CONTROLS,controls)]
     args=argparse.Namespace(stream=cd['stream'],build=str(build),hidden=False,scope='r2-draws-full',max_frames=0,reference=str(run/'reference.tsv'))
     doc=CH.assemble(run,args,cd,dx,sd,outcomes,'literal-review-start',CH.pipeline(),controls,CH.load_matrix(run),ran_by=CH.RAN_BY); put_json(run/'compare.json',doc); return doc
    def make_run(name,start):
     run=fixture/name; run.mkdir(); (run/'original.sa2s').write_bytes(stream.read_bytes()); (run/'reference.tsv').write_bytes(reference.read_bytes()); cd=copy.deepcopy(ev['census']); cd['stream']=str(run/'original.sa2s'); put_json(run/'census.json',cd); T.write_matrix(run,build)
     write_role(run,'dxvk',ev['dxvk'],start); write_role(run,'sdlgpu',ev['sdl'],start+1)
     noop=copy.deepcopy(ev); T.suppress_geometry(noop); write_role(run,'control-geometry-noop',noop['sdl'],start+2)
     count=copy.deepcopy(ev); T.r2_count_drop(count); write_role(run,'control-count-draw',count['sdl'],start+3)
     return run,refresh(run)
    a,da=make_run('a',100); b,db=make_run('b',200)
    assert da["marker"] == db["marker"] == "R2_DRAW_OK"
    return fixture, build, a, b


PUBLICATION_TARGETS = ('original', 'reference', 'binary', 'source', 'build_record', 'depfile',
                       'generated', 'external', 'raw_report', 'times', 'pipeline', 'control', 'matrix')


def publication_target(root, build, run, target):
    return {
        'original': run / 'original.sa2s', 'reference': run / 'reference.tsv',
        'binary': build / 'sa2_player_gpu', 'source': root / 'host/player/player.cpp',
        'build_record': build / 'sa2_player.json',
        'depfile': build / next(iter(CH.read_json(build / 'sa2_player.json')['depfiles'])),
        'generated': build / 'expected/expectations.json', 'external': root / 'private/header.h',
        'raw_report': run / 'dxvk/report.json', 'times': run / 'dxvk/frames.y4m.times.tsv',
        'pipeline': root / 'tools/s5/play_report.py', 'control': run / 'control-count-draw/d3d9gpu.json',
        'matrix': run / 'matrix/dxvk.tsv',
    }[target]


def same_size_change(path):
    data = path.read_bytes()
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])


@pytest.mark.parametrize('extra', [False, True], ids=['main', 'also'])
@pytest.mark.parametrize('target', PUBLICATION_TARGETS)
def test_receipt_holds_actual_inputs_after_validator_returns(monkeypatch, tmp_path, target, extra):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    selected = other if extra else main
    changed = publication_target(root, build, selected, target)
    real_check = CH.cmd_check
    def check_then_change(args):
        status = real_check(args)
        if status == 0 and Path(args.run) == selected:
            same_size_change(changed)
        return status
    monkeypatch.setattr(CH, 'cmd_check', check_then_change)
    out = tmp_path / 'receipt.json'
    args = ['receipt', '--id', RID, '--run', str(main), '--out', str(out)] + (['--also', str(other)] if extra else [])
    assert CH.main(args) == 1
    assert not out.exists()
    assert not list(tmp_path.glob('.receipt.json.*'))


@pytest.mark.parametrize('point', ['build', 'serialization', 'prepare', 'open', 'published', 'cleanup'])
@pytest.mark.parametrize('target', ['original', 'reference', 'binary'])
def test_receipt_holds_inputs_through_publication(monkeypatch, tmp_path, point, target):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    changed = publication_target(root, build, other, target)
    if point == 'build':
        real = CH.build_receipt
        def wrapped(*a, **kw):
            result = real(*a, **kw); same_size_change(changed); return result
        monkeypatch.setattr(CH, 'build_receipt', wrapped)
    elif point == 'serialization':
        real = CH.json.dumps
        def wrapped(doc, *a, **kw):
            result = real(doc, *a, **kw)
            if isinstance(doc, dict) and doc.get('schema') == CH.RECEIPT_SCHEMA:
                same_size_change(changed)
            return result
        monkeypatch.setattr(CH.json, 'dumps', wrapped)
    elif point == 'prepare':
        real = CH.tempfile.mkstemp
        def wrapped(*a, **kw):
            result = real(*a, **kw); same_size_change(changed); return result
        monkeypatch.setattr(CH.tempfile, 'mkstemp', wrapped)
    elif point == 'open':
        real = CH.os.fdopen
        def wrapped(*a, **kw):
            result = real(*a, **kw); same_size_change(changed); return result
        monkeypatch.setattr(CH.os, 'fdopen', wrapped)
    elif point == 'cleanup':
        real = Path.unlink
        def wrapped(path, *a, **kw):
            result = real(path, *a, **kw)
            if path.name.startswith('.receipt.json.') and (tmp_path / 'receipt.json').exists():
                same_size_change(changed)
            return result
        monkeypatch.setattr(Path, 'unlink', wrapped)
    else:
        real = CH.os.link
        def wrapped(*a, **kw):
            result = real(*a, **kw); same_size_change(changed); return result
        monkeypatch.setattr(CH.os, 'link', wrapped)
    out = tmp_path / 'receipt.json'
    assert CH.main(['receipt', '--id', RID, '--run', str(main), '--also', str(other), '--out', str(out)]) == 1
    assert not out.exists()
    assert not list(tmp_path.glob('.receipt.json.*'))


@pytest.mark.parametrize('change', ['compare', 'attestation', 'new_log', 'new_source', 'new_optional_report', 'main_during_also'])
def test_receipt_publication_identity_and_graph_membership(monkeypatch, tmp_path, change):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    real = CH.cmd_check
    def wrapped(args):
        result = real(args)
        if result == 0 and Path(args.run) == other:
            if change == 'compare':
                path = other / 'compare.json'; doc = CH.read_json(path); doc['finished'] = 'valid-looking replacement PASS'; path.write_text(json.dumps(doc))
            elif change == 'attestation':
                del args.checked_document_sha256
            elif change == 'new_log':
                (other / 'dxvk/dxvk-log/new_d3d9.log').write_text('new input')
            elif change == 'new_source':
                (root / 'host/player/new-source.h').write_text('new include candidate')
            elif change == 'new_optional_report':
                (other / 'dxvk/d3d9gpu.json').write_text('{}')
            else:
                same_size_change(main / 'original.sa2s')
        return result
    monkeypatch.setattr(CH, 'cmd_check', wrapped)
    out = tmp_path / 'receipt.json'
    assert CH.main(['receipt', '--id', RID, '--run', str(main), '--also', str(other), '--out', str(out)]) == 1
    assert not out.exists()


@pytest.mark.parametrize('change', ['provisional', 'competitor', 'replacement'])
def test_receipt_publication_payload_and_owned_inode(monkeypatch, tmp_path, change):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    out = tmp_path / 'receipt.json'
    real = CH.os.link
    def wrapped(source, destination):
        if change == 'provisional':
            same_size_change(Path(source))
            return real(source, destination)
        if change == 'competitor':
            out.write_text('unrelated competitor')
            return real(source, destination)
        result = real(source, destination)
        out.unlink()
        out.write_text('unrelated replacement')
        same_size_change(main / 'original.sa2s')
        return result
    monkeypatch.setattr(CH.os, 'link', wrapped)
    assert CH.main(['receipt', '--id', RID, '--run', str(main), '--out', str(out)]) == 1
    if change == 'provisional':
        assert not out.exists()
    else:
        assert out.read_text() == ('unrelated competitor' if change == 'competitor' else 'unrelated replacement')
    assert not list(tmp_path.glob('.receipt.json.*'))


def test_receipt_actual_full_positive_with_also(monkeypatch, tmp_path):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    out = tmp_path / 'receipt.json'
    assert CH.main(['receipt', '--id', RID, '--run', str(main), '--also', str(other), '--out', str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc['marker'] == 'R2_DRAW_OK' and doc['verdict'] == 'PASS'
    assert len(doc['other_runs']) == 1
    assert not list(tmp_path.glob('.receipt.json.*'))


@pytest.mark.parametrize('change', ['also_before_check', 'identical_inode_replacement', 'writer_replacement', 'provisional_replacement', 'output_during_last_rewalk'])
def test_receipt_publication_remaining_observation_boundaries(monkeypatch, tmp_path, change):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    out = tmp_path / 'receipt.json'
    foreign = None
    if change in ('also_before_check', 'identical_inode_replacement'):
        real = CH.cmd_check
        def wrapped(args):
            result = real(args)
            if result == 0 and Path(args.run) == main:
                path = other / 'original.sa2s' if change == 'also_before_check' else main / 'original.sa2s'
                if change == 'also_before_check':
                    same_size_change(path)
                else:
                    new = path.with_suffix('.replacement'); new.write_bytes(path.read_bytes()); new.replace(path)
            return result
        monkeypatch.setattr(CH, 'cmd_check', wrapped)
    elif change == 'writer_replacement':
        real = Path.write_text
        def wrapped(path, text, *a, **kw):
            if path == main / 'compare.json':
                doc = json.loads(text); doc['finished'] = 'replaced at actual writer'; text = json.dumps(doc)
            return real(path, text, *a, **kw)
        monkeypatch.setattr(Path, 'write_text', wrapped)
    elif change == 'provisional_replacement':
        real = CH.receipt_inputs_unchanged
        def wrapped(held):
            result = real(held)
            provisionals = list(tmp_path.glob('.receipt.json.*'))
            if provisionals and not out.exists():
                path = provisionals[0]
                new = path.with_suffix('.foreign'); new.write_text('unrelated provisional competitor'); new.replace(path)
            return result
        monkeypatch.setattr(CH, 'receipt_inputs_unchanged', wrapped)
    else:
        real = CH.receipt_inputs_unchanged
        def wrapped(held):
            result = real(held)
            if out.exists():
                same_size_change(out)
            return result
        monkeypatch.setattr(CH, 'receipt_inputs_unchanged', wrapped)
    assert CH.main(['receipt', '--id', RID, '--run', str(main), '--also', str(other), '--out', str(out)]) == 1
    if change == 'provisional_replacement':
        # The competitor's pathname/inode must survive; it is not this writer's.
        assert out.read_text() == 'unrelated provisional competitor'
        assert len(list(tmp_path.glob('.receipt.json.*'))) == 1
    else:
        assert not out.exists()
        assert not list(tmp_path.glob('.receipt.json.*'))


def test_receipt_exclusive_publication_preserves_preparation_competitor(monkeypatch, tmp_path):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    out = tmp_path / 'receipt.json'
    real = CH.tempfile.mkstemp
    def wrapped(*a, **kw):
        result = real(*a, **kw)
        out.write_text('unrelated preparation competitor')
        return result
    monkeypatch.setattr(CH.tempfile, 'mkstemp', wrapped)
    assert CH.main(['receipt', '--id', RID, '--run', str(main), '--out', str(out)]) == 1
    assert out.read_text() == 'unrelated preparation competitor'
    assert not list(tmp_path.glob('.receipt.json.*'))


@pytest.mark.parametrize('target, value', [('report', 1), ('report', [1]), ('files', [1]), ('files', None), ('device', [1]), ('external', [1]), ('sources', 1), ('binaries', None)])
def test_receipt_input_inventory_refuses_malformed_shapes(monkeypatch, tmp_path, target, value):
    root, build, main, other = publication_fixture(monkeypatch, tmp_path)
    if target in ('report', 'files'):
        path = main / 'dxvk/report.json'
        doc = CH.read_json(path)
        if target == 'report':
            doc = value
        else:
            doc['files'] = value
    elif target == 'device':
        path, doc = main / 'sdlgpu/d3d9gpu.json', value
    else:
        path = build / 'sa2_player.json'; doc = CH.read_json(path); doc[target] = value
    path.write_text(json.dumps(doc))
    out = tmp_path / 'receipt.json'
    assert CH.main(['receipt', '--id', RID, '--run', str(main), '--out', str(out)]) == 1
    assert not out.exists()


# Additional confirmed AA publication boundary; existing 68 tests above stay exact.
def aa_publication_fixture(monkeypatch, tmp_path):
    original_root = ROOT
    fixture = tmp_path/'aa-root';fixture.mkdir()
    for name in ['AGENTS.md', *CH.pipeline()]:
        target = fixture/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original_root/name,target)
    monkeypatch.setattr(CH,'ROOT',fixture)
    monkeypatch.setitem(globals(),'ROOT',fixture)
    a,b = aa_pair(monkeypatch,fixture)
    build = fixture/'build'
    library=fixture/'literal-libSDL3.so';library.write_bytes(b'authored SDL runtime library')
    dxvk=fixture/'literal-dxvk.so';dxvk.write_bytes(b'authored DXVK runtime library')
    config=fixture/'literal-dxvk.conf';config.write_bytes(b'authored DXVK config')
    for run in (a,b):
        for role in ('dxvk','sdlgpu','control-geometry-noop','control-count-draw'):
            path=run/role/'report.json';report=json.loads(path.read_text())
            binary=build/('sa2_player' if role=='dxvk' else 'sa2_player_gpu')
            report['files']['player']['real_path']=str(binary)
            report['files']['libSDL3']={'real_path':str(library),'path':str(library),'sha256':CH.sha256(library)}
            report['host_identity']['expected_sdl_sha256']=CH.sha256(library)
            if role=='dxvk':
                report['files']['libdxvk_d3d9']={'real_path':str(dxvk),'path':str(dxvk),'sha256':CH.sha256(dxvk)}
                report['files']['dxvk_conf']={'real_path':str(config),'path':str(config),'sha256':CH.sha256(config)}
                report['host_identity'].update(expected_dxvk_sha256=CH.sha256(dxvk),expected_config_sha256=CH.sha256(config))
            path.write_text(json.dumps(report))
            device=run/role/'d3d9gpu.json'
            if device.exists():
                d=json.loads(device.read_text());d['library']['path']=str(binary);device.write_text(json.dumps(d))
    header = tmp_path/'literal-external.h';header.write_bytes(b'authored external header')
    record = build/'sa2_player_gpu.json';rec=json.loads(record.read_text())
    depname=next(iter(rec['depfiles']));dep=build/depname
    dep.write_text(dep.read_text().rstrip()+' '+str(header)+'\n')
    rec['depfiles'][depname]=CH.sha256(dep);rec['external']={str(header):CH.sha256(header)}
    record.write_text(json.dumps(rec))
    for run in (a,b):
        matrix=run/'matrix/run.json';m=json.loads(matrix.read_text())
        m['dxvk_library']={'path':str(dxvk),'sha256':CH.sha256(dxvk)};matrix.write_text(json.dumps(m))
        saved=json.loads((run/'compare.json').read_text())
        c=json.loads((run/'census.json').read_text());dx=CH.load_run(run/'dxvk');sd=CH.load_run(run/'sdlgpu')
        controls=[CH.load_run(run/('control-'+control['name'])) for control in CH.R2_CONTROLS]
        outcomes=[CH.control_outcome(control,{'census':c,'dxvk':dx,'sdl':data,'hidden':False,
            'scope':'r2-draws-full','max_frames':0}) for control,data in zip(CH.R2_CONTROLS,controls)]
        args=argparse.Namespace(build=str(build),stream=str(CH.ROOT/saved['stream']['path']),hidden=False,
            reference=None,scope='r2-draws-full',max_frames=0)
        doc=CH.assemble(run,args,c,dx,sd,outcomes,'authored-final-physical',control_runs=controls,matrix=CH.load_matrix(run))
        assert doc['marker']=='R2_DRAW_OK',doc
        (run/'compare.json').write_text(json.dumps(doc))
    assert CH.check_aa(a,b)['verdict']=='PASS'
    return a,b


def aa_publication_target(run, name):
    doc=json.loads((run/'compare.json').read_text());build=CH.ROOT/doc['inputs']['build']
    record=json.loads((build/'sa2_player_gpu.json').read_text())
    return {
        'original':CH.ROOT/doc['stream']['path'],
        'reference':CH.ROOT/next(c['detail']['reference'] for c in doc['checks'] if c['check']=='h'),
        'player':build/'sa2_player_gpu', 'record':build/'sa2_player_gpu.json',
        'generated':build/next(iter(record['generated'])),
        'depfile':build/next(iter(record['depfiles'])),
        'external':Path(next(iter(record['external']))),
        'library':Path(json.loads((run/'sdlgpu/report.json').read_text())['files']['libSDL3']['real_path']),
        'raw':run/'sdlgpu/report.json','tsv':run/'sdlgpu/frames.y4m.times.tsv',
        'census':run/'census.json','matrix':run/'matrix/d3d9gpu.json',
        'control':run/'control-count-draw/report.json','compare':run/'compare.json',
        'keys':run/'sdlgpu/d3d9gpu.keys.json',
        'pipeline':CH.ROOT/'host/player/input.cpp',
        'optional':run/'dxvk/d3d9gpu.json',
        'newlog':run/'dxvk/dxvk-log/new_d3d9.log',
        'newsource':CH.ROOT/'host/player/new-publication-input.cpp',
    }[name]


@pytest.mark.parametrize('side',['a','b'])
@pytest.mark.parametrize('name',['original','reference','player','record','generated','depfile','external','library',
    'raw','tsv','census','matrix','control','compare','keys','pipeline','optional','newlog','newsource'])
def test_aa_publication_holds_both_graphs_after_real_validator(monkeypatch,tmp_path,side,name):
    a,b=aa_publication_fixture(monkeypatch,tmp_path);run=a if side=='a' else b
    target=aa_publication_target(run,name);out=tmp_path/'receipt.json';real=CH.check_aa;fired=[]
    def actual_then_mutate(*args):
        doc=real(*args);assert doc['verdict']=='PASS',doc
        if target.exists():same_size_change(target)
        else:target.write_bytes(b'authored late input')
        fired.append(True);return doc
    monkeypatch.setattr(CH,'check_aa',actual_then_mutate)
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2
    assert fired==[True] and not out.exists()


@pytest.mark.parametrize('point',['first-check','second-check'])
def test_aa_publication_holds_both_graphs_before_first_validator(monkeypatch,tmp_path,point):
    a,b=aa_publication_fixture(monkeypatch,tmp_path);library=aa_publication_target(b,'library')
    real=CH.checked_run;seen=[];out=tmp_path/'receipt.json'
    def actual_then_mutate(*args,**kwargs):
        result=real(*args,**kwargs);assert result[0]==0
        seen.append(True)
        if len(seen)==(1 if point=='first-check' else 2):same_size_change(library)
        return result
    monkeypatch.setattr(CH,'checked_run',actual_then_mutate)
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2
    assert len(seen)==2 and not out.exists()


@pytest.mark.parametrize('name',['reference','original','player','compare'])
def test_aa_publication_holds_inputs_through_actual_serialization(monkeypatch,tmp_path,name):
    a,b=aa_publication_fixture(monkeypatch,tmp_path);target=aa_publication_target(b,name)
    real=CH.json.dumps;fired=[];out=tmp_path/'receipt.json'
    def serialize_then_mutate(doc,*args,**kwargs):
        text=real(doc,*args,**kwargs)
        if isinstance(doc,dict) and doc.get('schema')=='sa2-s5-host2-aa/v1' and not fired:
            same_size_change(target);fired.append(True)
        return text
    monkeypatch.setattr(CH.json,'dumps',serialize_then_mutate)
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2
    assert fired==[True] and not out.exists()


@pytest.mark.parametrize('name',['reference','player','raw'])
def test_aa_publication_holds_inputs_through_success_cleanup(monkeypatch,tmp_path,name):
    a,b=aa_publication_fixture(monkeypatch,tmp_path);target=aa_publication_target(a,name)
    real=Path.unlink;out=tmp_path/'receipt.json';fired=[]
    def unlink_then_mutate(path,*args,**kwargs):
        result=real(path,*args,**kwargs)
        if path.name.startswith('.receipt.json.') and not fired:
            same_size_change(target);fired.append(True)
        return result
    monkeypatch.setattr(Path,'unlink',unlink_then_mutate)
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2
    assert fired==[True] and not out.exists()


@pytest.mark.parametrize('label',['a','b'])
def test_aa_publication_binds_real_nonwriting_document_identity(monkeypatch,tmp_path,label):
    a,b=aa_publication_fixture(monkeypatch,tmp_path);real=CH.check_aa;out=tmp_path/'receipt.json'
    def actual_then_altered(*args):
        doc=real(*args);assert doc['verdict']=='PASS'
        doc['runs'][label]['compare_sha256']='f'*64;return doc
    monkeypatch.setattr(CH,'check_aa',actual_then_altered)
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2
    assert not out.exists()


def test_aa_publication_keeps_exact_public_schema_and_inputs(monkeypatch,tmp_path,capsys):
    a,b=aa_publication_fixture(monkeypatch,tmp_path);expected=CH.check_aa(a,b)
    before={str(p):CH.sha256(p) for run in (a,b) for p in run.rglob('*') if p.is_file()}
    out=tmp_path/'receipt.json';capsys.readouterr()
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==0
    result=json.loads(out.read_text());result.pop('written');expected.pop('written');expected['id']='s5-boot-aa-v1'
    assert result==expected
    stdout,stderr=capsys.readouterr();assert stdout==f'BOOT_AA_OK {out}\n' and stderr==''
    assert before=={str(p):CH.sha256(p) for run in (a,b) for p in run.rglob('*') if p.is_file()}


@pytest.mark.parametrize('shape',[1,[1]])
def test_aa_publication_refuses_malformed_inventory_without_output(monkeypatch,tmp_path,shape):
    a,b=aa_publication_fixture(monkeypatch,tmp_path);(b/'compare.json').write_text(json.dumps(shape))
    out=tmp_path/'receipt.json'
    assert CH.main(['aa','--a',str(a),'--b',str(b),'--id','s5-boot-aa-v1','--out',str(out)])==2
    assert not out.exists()


@pytest.mark.parametrize('name',['original','reference','player'])
def test_r4_publication_holds_registered_claim_after_real_validator(monkeypatch,tmp_path,name):
    run=r4_run(monkeypatch,tmp_path);doc=json.loads((run/'compare.json').read_text())
    build=CH.ROOT/doc['inputs']['build']
    target={'original':CH.ROOT/doc['stream']['path'],'reference':Path(CH.METAL_HARBOR['reference']),
            'player':build/'sa2_player_gpu'}[name]
    out=tmp_path/'registered-receipt.json';real=CH.cmd_check;fired=[]
    def actual_then_mutate(args):
        status=real(args);assert status==0
        assert json.loads((run/'compare.json').read_text())['marker']=='R4_METALHARBOR_OK'
        same_size_change(target);fired.append(True);return status
    monkeypatch.setattr(CH,'cmd_check',actual_then_mutate)
    assert CH.main(['receipt','--run',str(run),'--id','s5-host2-r4-v1','--out',str(out)])==1
    assert fired==[True] and not out.exists()


# Local publisher behavior; no player, GPU, compiler or build fixture is used.
def receipt_publisher_descriptor_capture(monkeypatch):
    creator = []
    real = CH.tempfile.mkstemp

    def create(*args, **kwargs):
        fd, name = real(*args, **kwargs)
        observed = os.fstat(fd)
        creator.append((fd, (observed.st_dev, observed.st_ino), Path(name)))
        return fd, name

    monkeypatch.setattr(CH.tempfile, "mkstemp", create)
    return creator


def receipt_publisher_descriptor_open(fd):
    import errno
    try:
        os.fstat(fd)
        return True
    except OSError as exc:
        if exc.errno != errno.EBADF:
            raise
        return False


def test_receipt_publisher_descriptor_success(monkeypatch, tmp_path):
    creator = receipt_publisher_descriptor_capture(monkeypatch)
    out = tmp_path / "receipt.json"
    text = '{"authored_local_writer":true}\n'
    assert CH.publish_receipt(out, text, []) is True
    assert out.read_bytes() == text.encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["receipt.json"]
    assert len(creator) == 1
    assert not receipt_publisher_descriptor_open(creator[0][0])


def test_receipt_publisher_descriptor_preserves_cleanup_replacement(monkeypatch, tmp_path):
    creator = receipt_publisher_descriptor_capture(monkeypatch)
    out = tmp_path / "receipt.json"
    foreign = b"another local test writer owns this file\n"
    real_unlink = Path.unlink
    observations = []

    def cleanup(path, *args, **kwargs):
        answer = real_unlink(path, *args, **kwargs)
        if creator and path == creator[0][2]:
            fd, owned, _ = creator[0]
            open_during_cleanup = receipt_publisher_descriptor_open(fd)
            real_unlink(out)
            out.write_bytes(foreign)
            replacement = out.lstat()
            observations.append((open_during_cleanup, owned,
                                 (replacement.st_dev, replacement.st_ino)))
        return answer

    monkeypatch.setattr(Path, "unlink", cleanup)
    assert CH.publish_receipt(out, '{"authored_local_writer":true}\n', []) is False
    assert len(observations) == 1
    assert out.exists(), "rollback removed another test writer's replacement"
    assert out.read_bytes() == foreign
    # An open creator keeps its identity allocated during the ownership checks.
    assert observations[0][0] is True
    assert observations[0][1] != observations[0][2]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["receipt.json"]
    assert not receipt_publisher_descriptor_open(creator[0][0])


@pytest.mark.parametrize("point", ["before_link", "after_cleanup", "collision"])
def test_receipt_publisher_descriptor_refusal_cleanup(monkeypatch, tmp_path, point):
    creator = receipt_publisher_descriptor_capture(monkeypatch)
    out = tmp_path / "receipt.json"
    foreign = b"preexisting local test writer\n"
    observations = []
    if point == "collision":
        out.write_bytes(foreign)
    else:
        def unchanged(held):
            # Explicit local boundary provider; no receipt graph is fabricated.
            observations.append(receipt_publisher_descriptor_open(creator[0][0]))
            return point == "after_cleanup" and len(observations) == 1
        monkeypatch.setattr(CH, "receipt_inputs_unchanged", unchanged)
    assert CH.publish_receipt(out, '{"authored_local_writer":true}\n', []) is False
    assert len(creator) == 1
    assert not receipt_publisher_descriptor_open(creator[0][0])
    assert all(observations)
    if point == "collision":
        assert out.read_bytes() == foreign
        assert sorted(p.name for p in tmp_path.iterdir()) == ["receipt.json"]
    else:
        assert not out.exists()
        assert not list(tmp_path.iterdir())


def test_receipt_publisher_descriptor_closes_on_cleanup_exception(monkeypatch, tmp_path):
    creator = receipt_publisher_descriptor_capture(monkeypatch)
    out = tmp_path / "receipt.json"
    real_unlink = Path.unlink
    observed = []

    def cleanup(path, *args, **kwargs):
        if creator and path == creator[0][2]:
            observed.append(receipt_publisher_descriptor_open(creator[0][0]))
            raise OSError("authored provisional cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cleanup)
    with pytest.raises(OSError, match="authored provisional cleanup failure"):
        CH.publish_receipt(out, '{"authored_local_writer":true}\n', [])
    assert observed and all(observed)
    assert not receipt_publisher_descriptor_open(creator[0][0])
    assert not out.exists()
    # The deliberately unremovable owned temporary is retained as failure evidence.
    assert list(tmp_path.iterdir()) == [creator[0][2]]
