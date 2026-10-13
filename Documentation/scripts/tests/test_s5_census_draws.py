"""C4 original-byte reconciliation, with authored streams and no GPU or source build."""
from __future__ import annotations

import copy
import ast
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "host/d3d9gpu/test"))
import check_census_draws as CD
import test_s5_compare_hosts as W

ENCODED = {"DrawPrimitive": {"POINTLIST": 1}, "DrawIndexedPrimitive": {"TRIANGLELIST": 1},
           "DrawPrimitiveUP": {"POINTLIST": 1}, "DrawIndexedPrimitiveUP": {"TRIANGLELIST": 1}}


def write_draw_case(directory: Path, *, mixed=False, tail=False, zero=False):
    """Write literal original records and independently specified player/device answers."""
    directory.mkdir(parents=True, exist_ok=True)
    records = [W.device()]
    if mixed:
        records += [W.up(1)]  # no declaration: one refused TRIANGLESTRIP
    records += [*W.decl(), W.clear()]
    if mixed:
        records += [W.dp(1, 0)]  # one empty POINTLIST
    if not zero:
        records += [W.dp(1, 1), W.dip(4, 3, 1), W.up(1, ptype=1), W.dipup(1)]
    records += W.present(1)
    if tail:
        records += [W.dp(1, 1)]
    records += [(5, b"")]
    stream = W.stream(records, directory / "original.sa2s", header=W.account_header(True)).absolute()
    total = (0 if zero else 4) + 2 * mixed + tail
    encoded = copy.deepcopy({} if zero else ENCODED)
    if tail:
        encoded.setdefault("DrawPrimitive", {}).setdefault("POINTLIST", 0)
        encoded["DrawPrimitive"]["POINTLIST"] += 1
    refused = {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}} if mixed else {}
    empty = {"DrawPrimitive": {"POINTLIST": 1}} if mixed else {}
    records_count = {"call": 5 + (0 if zero else 4) + 2 * mixed + tail, "d3dx": 0, "frame": 1,
                     "note": 0, "end": 1, "unknown": 0, "seq_gaps": 0, "after_end": 0}
    uploads = dict(up_vertex_bytes=0 if zero else 64, up_index_bytes=0 if zero else 6,
                   up_arena_bytes=0 if zero else 118, up_pad_bytes=0 if zero else 48,
                   up_frame_peak_bytes=0 if zero else 118, up_arena_uploads=0 if zero else 2,
                   up_draw_uploaded_bytes=0 if zero else 70)
    device = {"complete": True, "failure": None, "fault": None, "fault_error": None,
              "library": {"sha256": "b" * 64}, "not_implemented": {"count": 0},
              "present": {"presents": 1, "failures": 0}, "uploads": uploads,
              "draws": {"drawn": (0 if zero else 4) + tail, "accepted": (0 if zero else 4) + tail,
                        "total": total, "fault_uncounted": 0, "encoded": encoded,
                        "unmapped_count": 0, "backend_failed_count": 0, "suppressed_count": 0,
                        "unmapped": {}, "backend_failed": {}, "fault_suppressed": {}, "failures": [],
                        "refused": refused, "empty": empty, "per_frame": [[1, 1, total - tail]],
                        "after_last_present": int(tail)}}
    size = stream.stat().st_size
    player = {"draws": total, "stage": 3, "complete": True, "pass": True, "exit_status": 0,
              "stop": "end of stream", "errors": 0, "readback_failures": 0, "present_failures": 0,
              "presents": 1, "frame_records": 1, "presents_without_frame_record": 0,
              "stream": str(stream), "stream_sha256": hashlib.sha256(stream.read_bytes()).hexdigest(),
              "stream_bytes": size, "stream_file_bytes_read": size, "records": records_count,
              "stream_records_read_to_end": True, "stream_sha256_after_stop": False,
              "stream_problems": {"count": 0}, "host_identity": {"d3d9_host": "sdlgpu"},
              "files": {"player": {"sha256": "b" * 64}},
              "options": {"max_frames": 0, "allow_truncated": False, "stats_only": False, "allow_notes": False},
              "methods": [{"type": "Device", "method": m, "handled": 1 + (mixed if m == "DrawPrimitiveUP" else 0),
                           **({"failed": 1} if mixed and m == "DrawPrimitiveUP" else {})} for m in W.CH.DRAW_METHODS]}
    pp, dp = directory / "report.json", directory / "d3d9gpu.json"
    pp.write_text(json.dumps(player)); dp.write_text(json.dumps(device))
    return stream, pp, dp


def substitute_player_original(directory: Path, *, geometry=False):
    """Coherently change a player's path/hash, retaining its census-held original."""
    player = directory / "report.json"
    report = json.loads(player.read_text())
    data = bytearray(Path(report["stream"]).read_bytes())
    if geometry:
        pos = struct.unpack_from("<I", data, 12)[0]
        found = 0
        while pos < len(data):
            kind, size, _ = struct.unpack_from("<III", data, pos)
            if kind == 1 and struct.unpack_from("<HH", data, pos + 12) == (1, 81):
                assert struct.unpack_from("<I", data, pos + 32)[0] == 0
                struct.pack_into("<I", data, pos + 32, 7)  # actual DrawPrimitive.StartVertex
                found += 1
            pos += size
        assert found == 1
    replacement = (directory / "replacement.sa2s").absolute()
    replacement.write_bytes(data)
    report.update(stream=str(replacement), stream_sha256=hashlib.sha256(data).hexdigest())
    player.write_text(json.dumps(report))
    return replacement


def load_case(directory, **kw):
    stream, pp, dp = write_draw_case(directory, **kw)
    return stream, W.CH.stream_account(stream), json.loads(pp.read_text()), json.loads(dp.read_text())


def run_audit(stream, account, player, device):
    return CD.audit(account, player, device, stream_path=str(stream),
                    stream_sha256=hashlib.sha256(stream.read_bytes()).hexdigest(), stream_bytes=stream.stat().st_size)


@pytest.mark.parametrize("mixed,tail", [(False, False), (True, False), (False, True), (True, True)])
def test_literal_four_draw_classes_and_refused_empty_tail_are_reconciled(tmp_path, mixed, tail):
    stream, account, player, device = load_case(tmp_path, mixed=mixed, tail=tail)
    assert account["draws"] == {"total": 4 + 2 * mixed + tail,
        "skipped_no_shader_route": device["draws"]["encoded"],
        "refused": {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}} if mixed else {},
        "empty": {"DrawPrimitive": {"POINTLIST": 1}} if mixed else {},
        "per_frame": [[1, 1, 4 + 2 * mixed]], "after_last_present": int(tail)}
    assert account["up_vertex_bytes"] == 64 and account["up_index_bytes"] == 6
    assert account["consumed_records"] == player["records"]
    result = run_audit(stream, account, player, device)
    assert result["pass"], result["failures"]
    assert result["original_draw_total"] == 4 + 2 * mixed + tail
    assert CD.check_paths(stream, tmp_path / "report.json", tmp_path / "d3d9gpu.json")["pass"]


def set_value(obj, dotted, value):
    parts = dotted.split(".")
    for part in parts[:-1]:
        obj = obj[part]
    if value == "ABSENT":
        obj.pop(parts[-1])
    else:
        obj[parts[-1]] = value


BAD_FIELDS = [
    ("device", "draws.total", 7), ("player", "draws", 7), ("player", "draws", "ABSENT"),
    ("device", "draws.drawn", 0), ("device", "draws.accepted", 3),
    ("device", "draws.fault_uncounted", 1), ("device", "draws.fault_uncounted", "ABSENT"),
    ("device", "draws.unmapped_count", 1), ("device", "draws.backend_failed_count", 1),
    ("device", "draws.suppressed_count", 1), ("device", "draws.failures", ["lost draw"]),
    ("device", "draws.encoded.DrawPrimitive.POINTLIST", 0),
    ("device", "draws.encoded.DrawPrimitive.POINTLIST", True),
    ("device", "draws.refused", {}), ("device", "draws.empty", {}),
    ("device", "draws.per_frame", [[1, 1, 5]]), ("device", "draws.per_frame", []),
    ("device", "draws.after_last_present", 1), ("device", "draws.after_last_present", "ABSENT"),
    ("player", "stage", 2), ("player", "options.max_frames", 1),
    ("player", "options.max_frames", False), ("player", "stream", "other.sa2s"),
    ("player", "stream_sha256", "0" * 64), ("player", "stop", "--max-frames"),
    ("player", "records.end", 0), ("player", "records.call", 10),
    ("player", "records.after_end", 1), ("player", "records.seq_gaps", 1),
    ("player", "records.unknown", 1), ("player", "stream_records_read_to_end", False),
    ("player", "stream_sha256_after_stop", True), ("player", "stream_bytes", 0),
    ("player", "stream_file_bytes_read", 0), ("player", "frame_records", 0),
    ("player", "presents", 0), ("player", "presents_without_frame_record", 1),
    ("device", "present.presents", 0), ("device", "present.failures", 1),
    ("device", "failure", "bad"), ("device", "fault", "draw-account"),
    ("device", "fault_error", "bad"), ("device", "fault_error", "ABSENT"),
    ("device", "complete", False), ("device", "not_implemented.count", 1),
    ("device", "library.sha256", "c" * 64), ("player", "host_identity.d3d9_host", "dxvk"),
] + [("player", k, 1) for k in ("exit_status", "errors", "readback_failures", "present_failures", "stream_problems.count")] + [
    ("player", k, False) for k in ("complete", "pass")] + [
    ("player", "options." + k, True) for k in ("allow_truncated", "stats_only", "allow_notes")] + [
    ("device", "uploads." + k, "ABSENT") for k in ("up_vertex_bytes", "up_index_bytes", "up_arena_bytes",
        "up_pad_bytes", "up_frame_peak_bytes", "up_arena_uploads", "up_draw_uploaded_bytes")]


@pytest.mark.parametrize("role,path,value", BAD_FIELDS, ids=[f"{r}.{p}={v}" for r,p,v in BAD_FIELDS])
def test_audit_refuses_wrong_missing_or_nonexact_facts(tmp_path, role, path, value):
    stream, account, player, device = load_case(tmp_path, mixed=True)
    set_value(player if role == "player" else device, path, value)
    result = run_audit(stream, account, player, device)
    assert not result["pass"], (role, path, value)


@pytest.mark.parametrize("method", W.CH.DRAW_METHODS)
def test_each_player_draw_method_failure_is_held_to_independent_refusals(tmp_path, method):
    stream, account, player, device = load_case(tmp_path, mixed=True)
    next(row for row in player["methods"] if row["method"] == method)["failed"] = 2
    assert not run_audit(stream, account, player, device)["pass"]


def test_refused_empty_coherent_device_swap_and_duplicate_method_are_refused(tmp_path):
    stream, account, player, device = load_case(tmp_path, mixed=True)
    device["draws"]["refused"], device["draws"]["empty"] = device["draws"]["empty"], device["draws"]["refused"]
    assert not run_audit(stream, account, player, device)["pass"]


@pytest.mark.parametrize("field", ["drawn", "total", "fault_uncounted", "accepted", "unmapped_count",
    "backend_failed_count", "suppressed_count", "encoded", "unmapped", "backend_failed", "fault_suppressed",
    "refused", "empty", "failures", "per_frame", "after_last_present"])
def test_missing_outcome_schema_fields_are_refused(tmp_path, field):
    stream, account, player, device = load_case(tmp_path)
    device["draws"].pop(field)
    assert not run_audit(stream, account, player, device)["pass"]


def test_coherent_unmapped_backend_failed_and_suppressed_outcomes_are_refused(tmp_path):
    for cls, count in (("unmapped", "unmapped_count"), ("backend_failed", "backend_failed_count"),
                       ("fault_suppressed", "suppressed_count")):
        stream, account, player, device = load_case(tmp_path / cls)
        device["draws"][cls] = device["draws"]["encoded"]
        device["draws"].update(encoded={}, drawn=0)
        device["draws"][count] = 4
        assert not run_audit(stream, account, player, device)["pass"]


def test_missing_refused_player_method_and_unknown_outcome_field_are_refused(tmp_path):
    stream, account, player, device = load_case(tmp_path, mixed=True)
    player["methods"] = [row for row in player["methods"] if row["method"] != "DrawPrimitiveUP"]
    assert not run_audit(stream, account, player, device)["pass"]
    stream, account, player, device = load_case(tmp_path / "unknown")
    device["draws"]["skipped"] = 0
    assert not run_audit(stream, account, player, device)["pass"]
    stream, account, player, device = load_case(tmp_path / "duplicate")
    player["methods"].append(dict(player["methods"][0]))
    assert not run_audit(stream, account, player, device)["pass"]


def test_perframe_tail_coherent_reallocation_and_zero_encoded_are_refused(tmp_path):
    stream, account, player, device = load_case(tmp_path, tail=True)
    device["draws"].update(per_frame=[[1, 1, 5]], after_last_present=0)
    assert not run_audit(stream, account, player, device)["pass"]
    stream, account, player, device = load_case(tmp_path / "zero", zero=True)
    assert not run_audit(stream, account, player, device)["pass"]


@pytest.mark.parametrize("bad", ["missing-stream", "missing-player", "missing-device", "json", "root", "truncated", "no-end", "after-end"])
def test_cli_returns_json_and_nonzero_for_missing_malformed_or_incomplete_inputs(tmp_path, bad):
    stream, player, device = write_draw_case(tmp_path)
    if bad.startswith("missing-"):
        stream, player, device = (tmp_path / "absent", player, device) if bad == "missing-stream" else (
            stream, tmp_path / "absent", device) if bad == "missing-player" else (stream, player, tmp_path / "absent")
    elif bad in ("json", "root"):
        player.write_text("{" if bad == "json" else "[]")
    elif bad == "after-end":
        stream.write_bytes(stream.read_bytes() + b"x")
    else:
        stream.write_bytes(stream.read_bytes()[:-1 if bad == "truncated" else -12])
    out = tmp_path / "audit.json"
    rc = subprocess.run([sys.executable, str(ROOT / "host/d3d9gpu/test/check_census_draws.py"),
        "--stream", str(stream), "--player", str(player), "--device", str(device), "--out", str(out)],
        capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert rc.returncode == 1, rc.stderr
    assert not json.loads(out.read_text())["pass"]
    assert "CENSUS_DRAWS_FAIL" in rc.stdout


def test_check_paths_refuses_inputs_changed_during_audit(tmp_path, monkeypatch):
    stream, player, device = write_draw_case(tmp_path)
    original = CD.CH.stream_account
    def change(path):
        account = original(path)
        device.write_text(device.read_text() + " ")
        return account
    monkeypatch.setattr(CD.CH, "stream_account", change)
    got = CD.check_paths(stream, player, device)
    assert not got["pass"] and "an audit input changed while checked" in got["failures"]


def held_identity(stream):
    return {"path": str(stream), "sha256": hashlib.sha256(stream.read_bytes()).hexdigest(),
            "presents": 1, "read_to_end": True}


@pytest.mark.parametrize("geometry", [False, True], ids=["copied-path", "changed-geometry-and-hash"])
def test_held_original_rejects_coherent_player_substitution_before_decode(tmp_path, monkeypatch, geometry):
    stream, player, device = write_draw_case(tmp_path)
    identity = held_identity(stream)
    replacement = substitute_player_original(tmp_path, geometry=geometry)
    assert CD.CH.stream_account(replacement)["draws"] == CD.CH.stream_account(stream)["draws"]
    assert (replacement.read_bytes() != stream.read_bytes()) is geometry
    def forbidden(path):
        pytest.fail("a player original-identity mismatch must be rejected before decoding")
    monkeypatch.setattr(CD.CH, "stream_account", forbidden)
    result = CD.check_paths(stream, player, device, identity=identity)
    assert not result["pass"] and "player original path/hash differs" in result["failures"][0]


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("sha256", "g" * 64), ("sha256", "ABSENT"),
    ("path", "original.sa2s"), ("presents", 2), ("presents", True), ("presents", 0), ("presents", "ABSENT"),
    ("read_to_end", False), ("read_to_end", "ABSENT")])
def test_held_original_identity_has_exact_path_hash_present_and_end_fields(tmp_path, field, value):
    stream, player, device = write_draw_case(tmp_path)
    identity = held_identity(stream)
    assert CD.check_paths(stream, player, device, identity=identity)["pass"]
    set_value(identity, field, value)
    assert not CD.check_paths(stream, player, device, identity=identity)["pass"]


def test_changed_held_original_file_is_refused_before_decode(tmp_path, monkeypatch):
    stream, player, device = write_draw_case(tmp_path)
    identity = held_identity(stream)
    replacement = substitute_player_original(tmp_path, geometry=True)
    stream.write_bytes(replacement.read_bytes())
    report = json.loads(player.read_text()); report.update(stream=str(stream), stream_sha256=identity["sha256"])
    player.write_text(json.dumps(report))
    def forbidden(path):
        pytest.fail("held original hash mismatch must be rejected before decoding")
    monkeypatch.setattr(CD.CH, "stream_account", forbidden)
    result = CD.check_paths(stream, player, device, identity=identity)
    assert not result["pass"] and "original file hash differs" in result["failures"][0]


def c4_pipeline_inventory(root):
    """Execute the real receipt collector only, without the write entrypoint's Git queries."""
    source = (ROOT / "host/d3d9gpu/test/r1_receipt.py").read_text()
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "write")
    first = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "pipeline" for t in n.targets))
    nodes = function.body[first:first + 2]
    assert isinstance(nodes[1], ast.For) and nodes[1].target.id == "extra"
    environment = {"REPO": root, "sha256_file": lambda p: hashlib.sha256(p.read_bytes()).hexdigest()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<actual-C4-receipt-collector>", "exec"), environment)
    return environment["pipeline"], [n.value for n in nodes[1].iter.elts]


@pytest.mark.parametrize("dependency", ["tools/s5/stream_dump.py", "tools/s5/play_report.py",
                                        "host/player/build_record.py"])
def test_c4_receipt_source_inventory_moves_when_each_external_audit_dependency_changes(tmp_path, dependency):
    real, extras = c4_pipeline_inventory(ROOT)
    required = ["host/d3d9gpu/test/check_census_draws.py", "tools/s5/compare_hosts.py",
                "tools/s5/stream_dump.py", "tools/s5/play_report.py", "host/player/build_record.py"]
    for path in required:
        assert real[path] == hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    for relative in set(extras + required):
        p = tmp_path / relative; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("authored input " + relative + "\n")
    before, _ = c4_pipeline_inventory(tmp_path)
    changed = tmp_path / dependency
    changed.write_bytes(changed.read_bytes().replace(b"authored", b"modified", 1))  # same length, raw bytes change
    after, _ = c4_pipeline_inventory(tmp_path)
    assert set(after) == set(before)
    assert {key for key in before if before[key] != after[key]} == {dependency}


def census_replay_fragment():
    """Extract only the existing replay checks, after player execution and before DXVK/GPU work."""
    source = (ROOT / "host/d3d9gpu/state_census.sh").read_text()
    start = source.index('  "$PY" - "$R/$s/d3d9gpu.json"')
    end = source.index("\ndone", start)
    return source[start:end]


def run_shell_check(directory, *, caps="", fragment=None):
    stream, player, device = write_draw_case(directory)
    d = json.loads(device.read_text())
    keys = {"complete": True, "map_faults": 0, "device": {"capability_override": caps or None}}
    keypath = directory / "d3d9gpu.keys.json"
    keypath.write_text(json.dumps(keys))
    d["sdl_gpu"] = {"capability_override": caps or None, "sampler_clamp_to_border": not caps,
        "depth_clip_and_clamp": not caps, "detected": {"sampler_clamp_to_border": True, "depth_clip_and_clamp": True}}
    d["pipeline"] = {"keys_sha256": hashlib.sha256(keypath.read_bytes()).hexdigest(), "keys_refused": None,
        "draws_mapped": 4, "distinct_inputs": 1, "distinct_keys": 1, "distinct_state_keys": 1,
        "distinct_samplers": 1, "unmapped": {"inputs": 0}, "patch": {"border_samplers": 0, "depth_clamp_keys": 0},
        "fallback_owed": {"border_samplers": 0, "depth_clamp_keys": 0}, "recomputes": 4}
    device.write_text(json.dumps(d))
    script = 'set -uo pipefail\nfail=0\nbad() { echo "FAIL $*"; fail=1; }\n'
    script += 'declare -A STREAM=([s]="$ORIGINAL")\ns=s; rc=0; crc=0\n' + (fragment or census_replay_fragment())
    script += '\nexit "$fail"\n'
    return script, {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "R": str(directory.parent), "ORIGINAL": str(stream),
        "CAPS_OFF": caps, "PY": sys.executable, "HERE": str(ROOT / "host/d3d9gpu")}, device


def test_shell_default_calls_real_audit_and_cannot_accept_wrong_or_missing_reports(tmp_path):
    script, env, device = run_shell_check(tmp_path / "s")
    good = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert good.returncode == 0 and "CENSUS_DRAWS_OK" in good.stdout, good.stdout + good.stderr
    d = json.loads(device.read_text()); d["draws"]["total"] = 3; device.write_text(json.dumps(d))
    assert subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode == 1
    env["ORIGINAL"] = str(tmp_path / "missing")
    assert subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode == 1


def test_shell_capability_off_keeps_zero_draws_and_does_not_claim_geometry(tmp_path):
    script, env, device = run_shell_check(tmp_path / "s", caps="border,depth-clamp")
    assert subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode == 1
    d = json.loads(device.read_text()); d["draws"]["drawn"] = 0; device.write_text(json.dumps(d))
    env["ORIGINAL"] = str(tmp_path / "missing")  # this leg is the original state/fallback scope
    got = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert got.returncode == 0 and "CENSUS_DRAWS_OK" not in got.stdout, got.stdout + got.stderr
