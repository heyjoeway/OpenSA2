"""tools/s5/sdl3_patch_receipt.py, the SDL patch's receipt writer: it writes only when every run it
is given holds and every leg is there, each leg given once, and each kind of run that does not
hold stops it with its own reason.

The evidence here is written by the test in the formats the runs print (the builder's
configuration and hash lines, run_border_clamp.sh's JSON lines, the player's report and times
file, d3d12_compile.sh's lines, the dxvk-ab documents and their reports), held to this
repository's tools/pins.json; the registered references are a registry of the test's own, in the
platform-startup receipt's shape (the writer's REGISTRY, replaced for the test). Nothing runs a
GPU. Each case below is one way the evidence can fail to hold; together they remove each of the
writer's checks from under some case (the second reviews' W1 to W7 among them, and the twelve
sub-checks I4's third confirm found no case held: B1 to B8, B9b, B10, B11 and B13).

The A/B controls also refuse the same player on both sides and byte-identical player copies
at different build paths. These call the real writer and require that no receipt is written.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PINS = json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import sdl3_patch_receipt as W  # noqa: E402

FIELDS = "frame\tseq\tvirtual_us\tguest_frames\tpolls\tbgrx_hash\textra\n"
ROWS = ["0\t1\t2\t3\t4\taaaa\tz\n", "1\t2\t3\t4\t5\tbbbb\tz\n"]
BOOT_STREAM = "b" * 64
SYNTH_STREAM = "5" * 64


def revision() -> str:
    with tarfile.open(ROOT / PINS["sdl3"]["path"]) as tar:
        text = tar.extractfile(f"SDL3-{PINS['sdl3']['version']}/REVISION.txt").read().decode().strip()
    return f"SDL-{text} ({PINS['sdl3']['vendor_info']})"


def sha(path: Path) -> str:
    return W.sha(path)


def build_log(config: str, prefix: Path, jobs: int) -> str:
    d, g = PINS["dxvk"], PINS["glslang"]
    if config == "patched":
        rows = [f"dxvk library_sha256 {d['library_sha256']} x", f"sdl3 library_sha256 {PINS['sdl3']['library_sha256']} x",
                f"glslang binary_sha256 {g['binary_sha256']} x", f"dxvk dxbc_spirv_archive_sha256 {d['dxbc_spirv_archive_sha256']} x"]
    else:
        rows = [f"sdl3-stock library_sha256 {PINS['sdl3-stock']['library_sha256']} x",
                f"dxvk library_sha256 (in the stock prefix) {d['library_sha256']} x",
                f"glslang binary_sha256 (in the stock prefix) {g['binary_sha256']} x",
                f"dxvk dxbc_spirv_archive_sha256 (in the stock prefix) {d['dxbc_spirv_archive_sha256']} x"]
    return "\n".join([f"== configuration: {config}, prefix {prefix}", f"== {jobs} parallel jobs",
                      "== sha256 of the built files the pins name (a library's soname link followed)",
                      *rows, "== what was built, by the pin gate", f"BUILD_OK ({config})"]) + "\n"


def sdl_library(prefix: Path, pin: str) -> str:
    return json.dumps({"sdl_library": {"prefix": str(prefix), "sha256": pin}})


def a3_out(device: str, prefix: Path) -> str:
    rows = [json.dumps({"test": f"t{i}", "ok": True}) for i in range(25)]
    rows.insert(17, json.dumps({"test": "properties.default", "ok": True,
                                "detail": f"border 1 (present 1), clip_and_clamp 1 (present 1), device {device}"}))
    record = {"devices": 6, "pipelines": 16, "clip_and_clamp_pipelines": 4, "samplers": 10,
              "layers_inserted": {"VK_LAYER_KHRONOS_validation": 6, "VK_LAYER_SA2_vkcap": 6}, "problems": []}
    return "\n".join([json.dumps({"sdl_revision": revision(), "sdl_version": 3004016}), *rows,
                      sdl_library(prefix, PINS["sdl3"]["library_sha256"]),
                      json.dumps({"validation_layer": "/x", "validation_errors": 0}), json.dumps({"vk_record": record}),
                      "VK_RECORD_OK", "BORDER_CLAMP_OK"]) + "\n"


def player_run(base: Path, prefix: Path, stream: str) -> Path:
    base.mkdir(parents=True)
    (base / "report.json").write_text(json.dumps({
        "pass": True, "complete": True, "exit_status": 0, "stream_sha256": stream,
        "files": {"libSDL3": {"sha256": PINS["sdl3"]["library_sha256"], "real_path": str(prefix / "lib/libSDL3.so.0.4.16")},
                  "libdxvk_d3d9": {"sha256": PINS["dxvk"]["library_sha256"],
                                   "real_path": str(prefix / "lib/x86_64-linux-gnu/libdxvk_d3d9.so.0")}},
        "host_identity": {"sdl_file_verified": True, "dxvk_file_verified": True, "sdl_revision": revision()}}))
    (base / "frames.y4m.times.tsv").write_text(FIELDS + "".join(ROWS))
    return base


def ab_doc(base: Path, e: Path, stream: str, stage: int) -> Path:
    stock = e / "stock-prefix"
    for side in ("before", "after"):
        (base / side).mkdir(parents=True)
        (base / side / "report.json").write_text(json.dumps({"files": {"libSDL3": {
            "sha256": PINS["sdl3-stock"]["library_sha256"], "real_path": str(stock / "lib/libSDL3.so.0.4.16")}}}))
    players = {side: {"build": str(e / f"{side}-build"), "sha256": sha(e / f"{side}-build" / "sa2_player")}
               for side in ("before", "after")}
    (base / "dxvk-ab.json").write_text(json.dumps({
        "verdict": "PASS", "pass": True, "stream": {"sha256": stream}, "stage": stage, "players": players,
        "host_sources_differing": ["x"], "before_provenance": {"pass": True}, "after_provenance": {"pass": True}}))
    return base / "dxvk-ab.json"


@pytest.fixture
def evidence(tmp_path, monkeypatch) -> Path:
    e = tmp_path / "e"
    patched, stock = e / "patched-prefix", e / "stock-prefix"
    for d in (patched, stock, e / "before-build", e / "after-build"):
        d.mkdir(parents=True)
    (e / "before-build" / "sa2_player").write_bytes(b"\x7fELF before")
    (e / "after-build" / "sa2_player").write_bytes(b"\x7fELF after")
    files = {
        "patched-1.log": build_log("patched", patched, 8), "patched-2.log": build_log("patched", patched, 4),
        "stock-1.log": build_log("stock", stock, 8),
        "lvp.out": a3_out("llvmpipe (LLVM 19.1.7, 256 bits)", patched), "nv.out": a3_out("NVIDIA GeForce RTX 5090", patched),
        "stock-detect.out": "\n".join([json.dumps({"test": "properties.stock_absent", "ok": True,
                                                   "detail": "border 0 (present 0), clip_and_clamp 0 (present 0)"}),
                                       sdl_library(stock, PINS["sdl3-stock"]["library_sha256"]), "BORDER_CLAMP_OK"]) + "\n",
        "a5.out": json.dumps({"file": "src/gpu/SDL_gpu.c", "stock_diagnostic_lines": 0, "patched_diagnostic_lines": 0,
                              "patched_only": 0}) + "\nD3D12_COMPILE_OK\n",
        "skip.log": ("SDL_VENDOR_INFO 'x'\nsdl3: FAIL sha256(libSDL3.so.0 -> libSDL3.so.0.4.16) reports "
                     f"'{'0' * 64}', pinned '{PINS['sdl3']['library_sha256']}'\ntool pins FAIL: sdl3\n"
                     "build_host_deps.sh: the install does not match tools/pins.json. If ...\n"),
        "system.log": ("build_host_deps.sh: --sdl3=system: /k/libSDL3.so.0 is /k/libSDL3.so.0.4.16, under /b or another "
                       "private/s5-tools: one of this project's own SDL3 builds, not a system SDL3\n"),
        "system-other.log": ("build_host_deps.sh: --sdl3=system: /o/libSDL3.so.0.4.16 holds the SDL patch's marks "
                             "(sa2-recomp): one of this project's own SDL3 builds, not a system SDL3\n"),
    }
    for name, text in files.items():
        (e / name).write_text(text)
    boot, mh = player_run(e / "boot", patched, BOOT_STREAM), player_run(e / "mh", patched, "7" * 64)
    registry = {"registered_picture_checks": []}
    for stage, other in (("boot", "other"), ("metalharbor", "mh-other")):
        ref = e / f"{stage}.ref.tsv"
        ref.write_text(FIELDS + "".join(r.replace("\tz", f"\t{other}") for r in ROWS))
        registry["registered_picture_checks"].append({
            "stage": stage, "frame_count": len(ROWS), "fields_compared": list(W.TIMES_FIELDS), "pass": True,
            "expected_times": {"path": f"private/{stage}.ref.tsv", "sha256": sha(ref)}})
    (e / "registry.json").write_text(json.dumps(registry))
    monkeypatch.setattr(W, "REGISTRY", e / "registry.json")
    ab = [ab_doc(e / "ab-boot", e, BOOT_STREAM, 3)] + [ab_doc(e / f"ab-synth{s}", e, SYNTH_STREAM, s) for s in range(4)]
    doc = {"installs": {"patched": str(patched), "stock": str(stock)},
           "builds": {"patched": [str(e / "patched-1.log"), str(e / "patched-2.log")], "stock": [str(e / "stock-1.log")]},
           "refusals": {"skip_patch": str(e / "skip.log"), "system_kept_aside": str(e / "system.log"),
                        "system_other_checkout": str(e / "system-other.log")},
           "a3": {"lavapipe": str(e / "lvp.out"), "nvidia": str(e / "nv.out"), "stock_detect": str(e / "stock-detect.out")},
           "a4": {"boot": {"run": str(boot), "reference": str(e / "boot.ref.tsv")},
                  "metalharbor": {"run": str(mh), "reference": str(e / "metalharbor.ref.tsv")}},
           "a5": str(e / "a5.out"), "ab": [str(p) for p in ab]}
    (e / "evidence.json").write_text(json.dumps(doc))
    return e


def write(e: Path, capsys) -> tuple[int, str]:
    rc = W.main(["--evidence", str(e / "evidence.json"), "--out", str(e / "r.json")])
    return rc, capsys.readouterr().err


def test_the_receipt_is_written_from_runs_that_hold(evidence, capsys, monkeypatch):
    # the caller's git variables do not reach the writer's git (an inherited GIT_DIR read nothing)
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    rc, err = write(evidence, capsys)
    assert rc == 0, err
    doc = json.loads((evidence / "r.json").read_text())
    assert doc["schema"] == "sa2-recomp-s5-sdl3-patch/v1" and doc["ok"] is True
    assert len(doc["code_head_before"]) == 40
    assert doc["checks"]["builds"]["patched"]["reproduced"] == 2
    # two builds of one source print one log: told apart by their paths, not their bytes
    (evidence / "patched-2.log").write_text((evidence / "patched-1.log").read_text())
    rc, err = write(evidence, capsys)
    assert rc == 0, err
    assert {k: (v["rows"], v["rows_differing"]) for k, v in doc["checks"]["a4_dxvk_references"].items()} == {
        "boot": (2, 0), "metalharbor": (2, 0)}
    assert doc["checks"]["a3_behaviour"]["nvidia"]["device"] == "NVIDIA GeForce RTX 5090"
    assert doc["checks"]["a3_behaviour"]["lavapipe"]["device"] == "llvmpipe (LLVM 19.1.7, 256 bits)"
    assert [(x["stream"]["sha256"], x["stage"]) for x in doc["checks"]["equal_sdl_ab"]] == [
        (BOOT_STREAM, 3), *[(SYNTH_STREAM, s) for s in range(4)]]
    assert set(doc["checks"]["refusals"]) == {"skip_patch", "system_kept_aside", "system_other_checkout"}
    assert doc["inputs"]["installs"] == {"patched": str((evidence / "patched-prefix").resolve()),
                                         "stock": str((evidence / "stock-prefix").resolve())}
    assert doc["inputs"]["registered_references"]["sha256"] == sha(evidence / "registry.json")
    assert doc["inputs"]["sdl3"]["vendor_info"] == PINS["sdl3"]["vendor_info"]
    assert "tools/s5/sdl3_patch_receipt.py" in doc["inputs"]["pipeline"]


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, (path, old)
    path.write_text(text.replace(old, new))


def evidence_change(change):
    """A change to evidence.json itself."""
    def apply(e: Path) -> None:
        doc = json.loads((e / "evidence.json").read_text())
        change(doc, e)
        (e / "evidence.json").write_text(json.dumps(doc))
    return apply


def report_change(run: str, change):
    def apply(e: Path) -> None:
        doc = json.loads((e / run / "report.json").read_text())
        change(doc)
        (e / run / "report.json").write_text(json.dumps(doc))
    return apply


def times_older(e: Path) -> None:
    """An evidence file older than the pipeline files: written before their last edit."""
    os.utime(e / "a5.out", (1, 1))


def other_players(e: Path) -> None:
    doc = json.loads((e / "ab-synth2" / "dxvk-ab.json").read_text())
    (e / "after-build-2").mkdir()
    (e / "after-build-2" / "sa2_player").write_bytes(b"\x7fELF another after")
    doc["players"]["after"] = {"build": str(e / "after-build-2"), "sha256": sha(e / "after-build-2" / "sa2_player")}
    (e / "ab-synth2" / "dxvk-ab.json").write_text(json.dumps(doc))


def registry_change(stage: str, change):
    """A change to one of the registry's own registered checks."""
    def apply(e: Path) -> None:
        doc = json.loads((e / "registry.json").read_text())
        change(next(c for c in doc["registered_picture_checks"] if c["stage"] == stage))
        (e / "registry.json").write_text(json.dumps(doc))
    return apply


def not_a_directory(config: str, absent: bool):
    """The configuration's prefix gone (absent) or a file where it was; the logs still name it."""
    def apply(e: Path) -> None:
        prefix = e / f"{config}-prefix"
        prefix.rmdir()
        if not absent:
            prefix.write_text("")
    return apply


CASES = [
    # installs
    ("installs absent", evidence_change(lambda d, e: d.pop("installs")), "names no installs"),
    ("installs one prefix", evidence_change(lambda d, e: d["installs"].update(stock=d["installs"]["patched"])),
     "the two configurations name one prefix"),
    # (B10: each prefix a directory)
    ("installs patched prefix a file", not_a_directory("patched", absent=False), "installs: the patched prefix"),
    ("installs stock prefix absent", not_a_directory("stock", absent=True), "installs: the stock prefix"),
    # builds (W7: the stock build's hashes)
    ("a patched hash", lambda e: edit(e / "patched-2.log", PINS["sdl3"]["library_sha256"], "1" * 64), "builds: "),
    ("a stock hash", lambda e: edit(e / "stock-1.log", PINS["sdl3-stock"]["library_sha256"], "1" * 64),
     "stock-1.log printed"),
    ("stock not BUILD_OK", lambda e: edit(e / "stock-1.log", "BUILD_OK (stock)", "build_host_deps.sh: failed"),
     "does not end BUILD_OK (stock)"),
    ("one patched log", evidence_change(lambda d, e: d["builds"].update(patched=d["builds"]["patched"][:1])),
     "1 patched build log(s): at least 2 are needed"),
    ("one patched log twice", evidence_change(lambda d, e: d["builds"].update(patched=[d["builds"]["patched"][0]] * 2)),
     "the patched build logs: one file is given more than once"),
    ("two copies of one A3 run", lambda e: (e / "nv.out").write_text((e / "lvp.out").read_text()),
     "the A3 runs: two of the files hold the same bytes"),
    ("two copies of one A/B", lambda e: (e / "ab-synth1/dxvk-ab.json").write_text((e / "ab-synth0/dxvk-ab.json").read_text()),
     "the dxvk-ab documents: two of the files hold the same bytes"),
    ("another prefix", lambda e: edit(e / "patched-1.log", f"prefix {e / 'patched-prefix'}", "prefix /elsewhere"),
     "built into /elsewhere, not the patched prefix"),
    # refusals (W5: the mutant applied nothing)
    ("the mutant applied", lambda e: edit(e / "skip.log", "SDL_VENDOR_INFO 'x'\n", "applied 0001-x.patch (0): 7 files changed\n"),
     "the mutant applied the patch"),
    ("the mutant passed", lambda e: edit(e / "skip.log", "sdl3: FAIL", "sdl3: OK"), "did not stop at the final pin check"),
    # (B9b: the log ends at the stop; B13: the stop names this pin)
    ("the mutant's log does not end at its stop",
     lambda e: edit(e / "skip.log", "build_host_deps.sh: the install does not match tools/pins.json. If ...\n", ""),
     "did not stop at the final pin check"),
    ("the mutant stopped on another pin",
     lambda e: edit(e / "skip.log", f"pinned '{PINS['sdl3']['library_sha256']}'", f"pinned '{'4' * 64}'"),
     "did not stop at the final pin check"),
    ("system built", lambda e: (e / "system.log").write_text("BUILD_OK (system SDL3, not pinned)\n"), "did not refuse"),
    ("other checkout absent", evidence_change(lambda d, e: d["refusals"].pop("system_other_checkout")),
     "names no system_other_checkout log"),
    ("one system log twice", evidence_change(lambda d, e: d["refusals"].update(system_other_checkout=str(e / "system.log"))),
     "the two --sdl3=system refusals: one file is given more than once"),
    # A3 (W3, W4)
    ("a3 row", lambda e: edit(e / "nv.out", '"t3", "ok": true', '"t3", "ok": false'), "A3 nvidia"),
    # (B3: the BORDER_CLAMP_OK ending; B4: the row count)
    ("a3 not BORDER_CLAMP_OK", lambda e: edit(e / "nv.out", "VK_RECORD_OK\nBORDER_CLAMP_OK\n", "VK_RECORD_OK\n"),
     "does not end BORDER_CLAMP_OK"),
    ("a3 a row missing", lambda e: edit(e / "nv.out", '{"test": "t3", "ok": true}\n', ""), "25 rows, 0 not ok"),
    ("a3 a row more", lambda e: edit(e / "lvp.out", '{"test": "t3", "ok": true}\n',
                                     '{"test": "t3", "ok": true}\n{"test": "t25", "ok": true}\n'), "27 rows, 0 not ok"),
    ("a3 validation", lambda e: edit(e / "lvp.out", '"validation_errors": 0', '"validation_errors": 2'), "A3 lavapipe"),
    ("a3 revision", lambda e: edit(e / "lvp.out", PINS["sdl3"]["vendor_info"], "sa2-recomp sdl3-patches 000000000000"),
     "SDL revision"),
    ("a3 record", lambda e: edit(e / "nv.out", '"problems": []', '"problems": ["x"]'), "no clean Vulkan record"),
    ("a3 record unread", lambda e: edit(e / "lvp.out", "VK_RECORD_OK\n", ""), "no clean Vulkan record"),
    ("a3 nvidia on lavapipe", lambda e: edit(e / "nv.out", "device NVIDIA GeForce RTX 5090", "device llvmpipe"),
     "not run on the nvidia device"),
    ("a3 one file for both", evidence_change(lambda d, e: d["a3"].update(nvidia=d["a3"]["lavapipe"])),
     "the A3 runs: one file is given more than once"),
    ("a3 another prefix", lambda e: edit(e / "lvp.out", str(e / "patched-prefix"), "/elsewhere"), "SDL prefix /elsewhere"),
    ("a3 stock detection", lambda e: edit(e / "stock-detect.out", '"ok": true', '"ok": false'), "A3 stock detection"),
    ("a3 stock library", lambda e: edit(e / "stock-detect.out", PINS["sdl3-stock"]["library_sha256"], "2" * 64),
     "A3 stock detection"),
    # A4 (W2: the SDL hash; W6: the row count)
    ("a4 SDL", report_change("boot", lambda r: r["files"]["libSDL3"].update(sha256=PINS["sdl3-stock"]["library_sha256"])),
     "not the patched pin"),
    ("a4 SDL elsewhere", report_change("mh", lambda r: r["files"]["libSDL3"].update(real_path="/elsewhere/libSDL3.so.0")),
     "from /elsewhere/libSDL3.so.0"),
    ("a4 DXVK", report_change("boot", lambda r: r["files"]["libdxvk_d3d9"].update(sha256="3" * 64)), "the run loaded DXVK"),
    # (B5: DXVK's prefix; B6: DXVK's file verified; B7: complete)
    ("a4 DXVK elsewhere", report_change("mh", lambda r: r["files"]["libdxvk_d3d9"].update(
        real_path="/elsewhere/libdxvk_d3d9.so.0")), "from /elsewhere/libdxvk_d3d9.so.0, not the pin"),
    ("a4 DXVK unverified", report_change("mh", lambda r: r["host_identity"].update(dxvk_file_verified=False)),
     "did not verify its SDL and DXVK files"),
    ("a4 incomplete", report_change("boot", lambda r: r.update(complete=False)), "pass True, complete False, exit 0"),
    ("a4 failed", report_change("mh", lambda r: r.update(**{"pass": False, "exit_status": 4})), "the run did not pass"),
    ("a4 unverified", report_change("boot", lambda r: r["host_identity"].update(sdl_file_verified=False)),
     "did not verify its SDL and DXVK files"),
    ("a4 revision", report_change("boot", lambda r: r["host_identity"].update(sdl_revision="SDL-x")),
     "the run's SDL revision 'SDL-x'"),
    ("a4 a row differs", lambda e: edit(e / "boot/frames.y4m.times.tsv", "bbbb", "cccc"), "1 differing (first 1)"),
    ("a4 truncated", lambda e: edit(e / "mh/frames.y4m.times.tsv", ROWS[1], ""), "1 rows against 2 (registered 2)"),
    ("a4 no metalharbor", evidence_change(lambda d, e: d["a4"].pop("metalharbor")),
     "A4 runs ['boot'], not the registered stages ['boot', 'metalharbor']"),
    ("a4 no runs", evidence_change(lambda d, e: d.update(a4={})), "names no A4 runs"),
    ("a4 its own reference", evidence_change(lambda d, e: d["a4"]["boot"].update(
        reference=str(e / "boot" / "frames.y4m.times.tsv"))), "the reference is the run's own times file"),
    ("a4 another reference", lambda e: edit(e / "boot.ref.tsv", "\tother", "\tanother"), "is not the registered one"),
    # (B8: the registry's own checks, each passed and over the six fields)
    ("registry check not passed", registry_change("metalharbor", lambda c: c.update(**{"pass": False})),
     "the registered check 'metalharbor' is not a passed check of"),
    ("registry check other fields", registry_change("boot", lambda c: c.update(fields_compared=list(W.TIMES_FIELDS[:-1]))),
     "the registered check 'boot' is not a passed check of"),
    # A5
    ("a5", lambda e: edit(e / "a5.out", '"patched_only": 0', '"patched_only": 1'), "A5"),
    # (B11: at least one file row)
    ("a5 no file row", lambda e: (e / "a5.out").write_text("D3D12_COMPILE_OK\n"), "['D3D12_COMPILE_OK'] []"),
    # the A/Bs
    ("ab fails", lambda e: edit(e / "ab-boot/dxvk-ab.json", '"PASS"', '"FAIL"'), "reads FAIL"),
    # (B1: the pass flag beside the verdict; B2: the before side's provenance)
    ("ab pass flag", lambda e: edit(e / "ab-synth2/dxvk-ab.json", '"verdict": "PASS", "pass": true',
                                    '"verdict": "PASS", "pass": false'), "reads PASS"),
    ("ab before provenance", lambda e: edit(e / "ab-boot/dxvk-ab.json", '"before_provenance": {"pass": true}',
                                            '"before_provenance": {"pass": false}'), "a side's provenance did not pass"),
    ("ab provenance", lambda e: edit(e / "ab-synth1/dxvk-ab.json", '"after_provenance": {"pass": true}',
                                      '"after_provenance": {"pass": false}'), "a side's provenance did not pass"),
    ("ab patched SDL", lambda e: edit(e / "ab-synth0/before/report.json", PINS["sdl3-stock"]["library_sha256"],
                                      PINS["sdl3"]["library_sha256"]), "not the stock pin from"),
    ("ab SDL elsewhere", lambda e: edit(e / "ab-synth3/after/report.json", str(e / "stock-prefix"), "/elsewhere"),
     "not the stock pin from"),
    ("ab none", evidence_change(lambda d, e: d.update(ab=[])), "names no dxvk-ab documents"),
    ("ab absent", evidence_change(lambda d, e: d.pop("ab")), "names no dxvk-ab documents"),
    ("ab four", evidence_change(lambda d, e: d.update(ab=d["ab"][:4])), "4 dxvk-ab documents"),
    ("ab one five times", evidence_change(lambda d, e: d.update(ab=[d["ab"][0]] * 5)),
     "the dxvk-ab documents: one file is given more than once"),
    ("ab boot stage", lambda e: edit(e / "ab-boot/dxvk-ab.json", '"stage": 3', '"stage": 2'),
     "not the A4 boot run's stream"),
    ("ab another player", lambda e: (e / "after-build" / "sa2_player").write_bytes(b"\x7fELF rebuilt"),
     "the after player"),
    ("ab two pairs", other_players, "compare 2 pairs of players"),
    # the pipeline (W1)
    ("evidence older than the pipeline", times_older, "edited after the oldest evidence file"),
]


@pytest.mark.parametrize("name,change,words", CASES, ids=[c[0] for c in CASES])
def test_a_run_that_does_not_hold_stops_the_receipt(evidence, capsys, name, change, words):
    change(evidence)
    rc, err = write(evidence, capsys)
    assert rc == 1 and words in err, err
    assert not (evidence / "r.json").exists()


def same_player_both_sides(e: Path) -> None:
    for path in json.loads((e / "evidence.json").read_text())["ab"]:
        doc = json.loads(Path(path).read_text())
        doc["players"]["before"] = dict(doc["players"]["after"])
        Path(path).write_text(json.dumps(doc))


def test_an_ab_whose_before_is_its_after_stops_the_receipt(evidence, capsys):
    same_player_both_sides(evidence)
    rc, err = write(evidence, capsys)
    assert rc == 1 and "before and after name byte-identical players" in err, err
    assert not (evidence / "r.json").exists()


def test_identical_player_copies_at_distinct_build_paths_stop_the_receipt(evidence, capsys):
    before = evidence / "before-build" / "sa2_player"
    after = evidence / "after-build" / "sa2_player"
    after.write_bytes(before.read_bytes())
    for path in json.loads((evidence / "evidence.json").read_text())["ab"]:
        doc = json.loads(Path(path).read_text())
        doc["players"]["after"]["sha256"] = sha(after)
        Path(path).write_text(json.dumps(doc))
    rc, err = write(evidence, capsys)
    assert rc == 1 and "before and after name byte-identical players" in err, err
    assert not (evidence / "r.json").exists()


@pytest.mark.parametrize("git", [None, "#!/bin/sh\nexit 128\n", "#!/bin/sh\necho not-a-commit\n"])
def test_a_git_that_cannot_say_the_head_stops_the_receipt(evidence, capsys, monkeypatch, git):
    """No git on PATH, a git that fails, and one that answers no commit: each stops the receipt
    (an empty code_head_before is never written)."""
    bin_dir = evidence / "bin"
    bin_dir.mkdir()
    if git is not None:
        (bin_dir / "git").write_text(git)
        (bin_dir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    rc, err = write(evidence, capsys)
    assert rc == 1 and "pipeline: " in err, err
    assert not (evidence / "r.json").exists()
