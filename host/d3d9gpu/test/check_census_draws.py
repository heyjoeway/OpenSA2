#!/usr/bin/env python3
"""Reconcile one default C4 SDL replay with the complete original stream's draw account."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools/s5"))
import compare_hosts as CH  # noqa: E402

SCHEMA = "sa2-s5-census-draws/v1"


def audit(account: dict, player: dict, device: dict, *, stream_path: str,
          stream_sha256: str, stream_bytes: int) -> dict:
    """Pure checks on an independently decoded original stream and one SDL player's reports."""
    failures = []
    if not all(isinstance(x, dict) for x in (account, player, device)):
        return {"schema": SCHEMA, "pass": False, "failures": ["account and reports must be objects"]}
    cd, dd = account.get("draws"), device.get("draws")
    if not isinstance(cd, dict) or not isinstance(dd, dict):
        return {"schema": SCHEMA, "pass": False, "failures": ["draw account and device draws must be objects"]}
    frames = account.get("frames")
    if not CH.exact_count(frames) or frames == 0:
        return {"schema": SCHEMA, "pass": False, "failures": ["original stream has no exact positive frame count"]}

    def count(label, value, wanted):
        if not CH.exact_count(value) or not CH.exact_count(wanted) or value != wanted:
            failures.append(f"{label}: {value!r}, expected exact {wanted!r}")

    # This composition needs no other host's evidence and admits no suppression fault.
    outcome_failures, detail = CH.r2_draw_account({"scope": "r2-draws-full", "census": account,
                                                  "sdl": {"device": device}})
    failures.extend(outcome_failures)
    for label, value in (("player", player.get("draws")), ("device", dd.get("total"))):
        count(label + " draw total", value, cd.get("total"))
    count("fault_uncounted", dd.get("fault_uncounted"), 0)
    for cls in ("refused", "empty"):
        wanted = CH.count_map(cd.get(cls), "original " + cls, failures)
        got = CH.count_map(dd.get(cls), "device " + cls, failures)
        if got != wanted:
            failures.append(f"{cls} draws differ from original stream")
    original_frames = CH.frame_counts(cd.get("per_frame"), frames, "original per_frame", failures)
    device_frames = CH.frame_counts(dd.get("per_frame"), frames, "device per_frame", failures)
    if device_frames != original_frames:
        failures.append("per-frame draws differ from original stream")
    count("after-last-Present", dd.get("after_last_present"), cd.get("after_last_present"))
    count("device per-frame plus tail total", dd.get("total"),
          sum(device_frames.values()) + dd["after_last_present"] if CH.exact_count(dd.get("after_last_present")) else None)
    accepted_frames = CH.frame_counts(account.get("accepted_per_frame"), frames, "original accepted_per_frame", failures)
    accepted_tail = account.get("accepted_after_last_present")
    expected = CH.count_map(cd.get("skipped_no_shader_route"), "original accepted", failures)
    count("original accepted frame plus tail total",
          sum(accepted_frames.values()) + accepted_tail if CH.exact_count(accepted_tail) else None, sum(expected.values()))
    if any(n > original_frames.get(frame, 0) for frame, n in accepted_frames.items()):
        failures.append("original accepted frame count exceeds all draws in that frame")
    refused = CH.count_map(cd.get("refused"), "original refused", failures)
    methods = player.get("methods")
    rows = {}
    if not isinstance(methods, list) or any(not isinstance(row, dict) for row in methods):
        failures.append("player methods must be a list of rows")
    else:
        for row in methods:
            method = row.get("method")
            if row.get("type") == "Device" and method in CH.DRAW_METHODS:
                if method in rows:
                    failures.append(f"duplicate player draw method {method}")
                rows[method] = row
        for method in CH.DRAW_METHODS:
            wanted = sum(n for (name, _), n in refused.items() if name == method)
            count("player " + method + " failures", rows.get(method, {}).get("failed", 0), wanted)
    for key, source in (("up_vertex_bytes", "up_vertex_bytes"), ("up_index_bytes", "up_index_bytes"),
                        ("up_arena_bytes", "up_arena_bytes"), ("up_pad_bytes", "up_pad_bytes"),
                        ("up_frame_peak_bytes", "up_frame_peak_bytes"), ("up_arena_uploads", "up_draws"),
                        ("up_draw_uploaded_bytes", "up_draw_uploaded_bytes")):
        count("uploads." + key, CH.get(device, "uploads." + key), account.get(source))

    for label, got, wanted in (("player stage", player.get("stage"), 3),
                               ("player max_frames", CH.get(player, "options.max_frames"), 0),
                               ("player presents", player.get("presents"), frames),
                               ("player frame_records", player.get("frame_records"), frames),
                               ("player presents_without_frame_record", player.get("presents_without_frame_record"), 0),
                               ("device presents", CH.get(device, "present.presents"), frames),
                               ("device Present failures", CH.get(device, "present.failures"), 0),
                               ("consumed bytes", account.get("consumed_bytes"), stream_bytes),
                               ("player stream_bytes", player.get("stream_bytes"), stream_bytes),
                               ("player stream_file_bytes_read", player.get("stream_file_bytes_read"), stream_bytes),
                               ("player exit", player.get("exit_status"), 0), ("player errors", player.get("errors"), 0),
                               ("player readback failures", player.get("readback_failures"), 0),
                               ("player Present failures", player.get("present_failures"), 0),
                               ("player stream problems", CH.get(player, "stream_problems.count"), 0),
                               ("device not implemented", CH.get(device, "not_implemented.count"), 0)):
        count(label, got, wanted)
    records, reported = account.get("consumed_records"), player.get("records")
    if not isinstance(records, dict) or not isinstance(reported, dict) or set(records) != set(reported):
        failures.append("player consumed record fields differ from original stream")
    else:
        for key, wanted in records.items():
            count("player records." + key, reported[key], wanted)
    for key, wanted in (("end", 1), ("frame", frames), ("unknown", 0), ("seq_gaps", 0), ("after_end", 0)):
        count("original records." + key, records.get(key) if isinstance(records, dict) else None, wanted)
    for path, wanted in (("complete", True), ("pass", True), ("stream_records_read_to_end", True),
                         ("stream_sha256_after_stop", False), ("options.allow_truncated", False),
                         ("options.stats_only", False), ("options.allow_notes", False)):
        if CH.get(player, path) is not wanted:
            failures.append(f"player {path} must be {wanted}")
    if player.get("stop") != "end of stream":
        failures.append("player did not stop at genuine END")
    if player.get("stream") != stream_path or player.get("stream_sha256") != stream_sha256:
        failures.append("player original stream path/hash differs")
    if CH.get(player, "host_identity.d3d9_host") != "sdlgpu":
        failures.append("player is not the SDL GPU host")
    if device.get("complete") is not True:
        failures.append("device is not complete")
    for key in ("failure", "fault", "fault_error"):
        if key not in device or device[key] is not None:
            failures.append(f"device {key} must be present and null")
    library, binary = CH.get(device, "library.sha256"), CH.get(player, "files.player.sha256")
    if not isinstance(library, str) or len(library) != 64 or library != binary:
        failures.append("device and player binary hashes differ or are absent")
    return {"schema": SCHEMA, "scope": "default-full-geometry", "pass": not failures, "failures": failures,
            "frames": frames, "draws": detail, "original_draw_total": cd.get("total")}


def check_paths(stream: Path, player: Path, device: Path, *, identity: dict | None = None) -> dict:
    """Read original bytes and reports anew. Saved audit files and markers are never inputs."""
    try:
        stream, player, device = Path(stream).absolute(), Path(player).absolute(), Path(device).absolute()
        raw_player, raw_device = player.read_bytes(), device.read_bytes()
        report = json.loads(raw_player)
        if identity is not None:
            if (not isinstance(identity, dict) or identity.get("path") != str(stream)
                    or not Path(identity["path"]).is_absolute()
                    or not isinstance(identity.get("sha256"), str) or len(identity["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in identity["sha256"])
                    or not CH.exact_count(identity.get("presents")) or identity["presents"] == 0
                    or identity.get("read_to_end") is not True):
                raise ValueError("held census stream identity requires an exact absolute path/hash and full Present/END account")
            if (not isinstance(report, dict) or report.get("stream") != identity["path"]
                    or report.get("stream_sha256") != identity["sha256"]):
                raise ValueError("player original path/hash differs from held census stream identity")
        before = CH.sha256(stream)
        if identity is not None and before != identity["sha256"]:
            raise ValueError("original file hash differs from held census stream identity")
        account = CH.stream_account(stream)
        result = audit(account, report, json.loads(raw_device), stream_path=str(stream),
                       stream_sha256=before, stream_bytes=stream.stat().st_size)
        if identity is not None and account["frames"] != identity["presents"]:
            result["failures"].append("original Present count differs from held census stream identity")
            result["pass"] = False
        if CH.sha256(stream) != before or player.read_bytes() != raw_player or device.read_bytes() != raw_device:
            result["failures"].append("an audit input changed while checked")
            result["pass"] = False
        result["inputs"] = {"stream": {"path": str(stream), "sha256": before},
                            "player": {"path": str(player), "sha256": hashlib.sha256(raw_player).hexdigest()},
                            "device": {"path": str(device), "sha256": hashlib.sha256(raw_device).hexdigest()}}
        if identity is not None:
            result["held_stream_identity"] = identity
        return result
    except (OSError, ValueError, TypeError, CH.stream_dump.StreamError) as error:
        return {"schema": SCHEMA, "scope": "default-full-geometry", "pass": False,
                "failures": [f"cannot audit original stream and reports: {error}"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", required=True, type=Path)
    parser.add_argument("--player", required=True, type=Path)
    parser.add_argument("--device", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    result = check_paths(args.stream, args.player, args.device)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print("CENSUS_DRAWS_OK" if result["pass"] else "CENSUS_DRAWS_FAIL", "; ".join(result["failures"]))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
