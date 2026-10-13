#!/usr/bin/env python3
"""Copy a generated contract, changing only its expected DXVK config digest."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    path = path.resolve(strict=True)
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}


def generated_header(contract):
    text = "#pragma once\nnamespace sa2_host_expected {\n"
    for name, value in contract.items():
        kind = "char" if isinstance(value, str) else "int"
        suffix = "[]" if isinstance(value, str) else ""
        text += f"inline constexpr {kind} {name}{suffix} = {json.dumps(value)};\n"
    return text + "}\n"


def prepare(args):
    out = args.out.resolve()
    if out.exists():
        raise ValueError(f"refused existing test build directory: {out}")
    header = args.bin / "expected/sa2_host_expectations.h"
    receipt = args.bin / "expected/expectations.json"
    record = json.loads(receipt.read_text())
    contract = record["contract"]
    names = {"sdl_sha256", "dxvk_sha256", "dxvk_config_sha256", "sdl_version", "sdl_revision"}
    if set(contract) != names or header.read_text() != generated_header(contract):
        raise ValueError("the production header is not the exact generated contract")
    if sha(header) != record["header"]["sha256"]:
        raise ValueError("the production generated header differs from its receipt")
    pins = json.loads((args.repo / "tools/pins.json").read_text())["tools"]
    # The SDL pin of the configuration the build is (generate_expectations.py's
    # sdl3_configuration): the patched SDL's tools.sdl3, the stock one's tools.sdl3-stock; DXVK's
    # is tools.dxvk in both (the stock prefix holds the same bytes).
    sdl_entry = {"patched": "sdl3", "stock": "sdl3-stock"}.get(record.get("sdl3_configuration"))
    if sdl_entry is None:
        raise ValueError(f"the production contract names no SDL3 configuration: {record.get('sdl3_configuration')!r}")
    for key, name in (("sdl_sha256", sdl_entry), ("dxvk_sha256", "dxvk")):
        if contract[key] != pins[name]["library_sha256"]:
            raise ValueError(f"the production {name} expectation differs from its pin")
    good_config = args.player_dir / "dxvk.conf"
    if contract["dxvk_config_sha256"] != sha(good_config):
        raise ValueError("the production config expectation differs from dxvk.conf")
    old_line = "d3d9.floatEmulation = Strict\n"
    new_line = "d3d9.floatEmulation = True\n"
    good = good_config.read_bytes()
    bad = args.config.read_bytes()
    if good.count(old_line.encode()) != 1 or bad != good.replace(old_line.encode(), new_line.encode(), 1):
        raise ValueError("the bad config must change only floatEmulation from Strict to True")
    old_sha, new_sha = contract["dxvk_config_sha256"], sha(args.config)
    old = f'inline constexpr char dxvk_config_sha256[] = "{old_sha}";\n'
    new = f'inline constexpr char dxvk_config_sha256[] = "{new_sha}";\n'
    before = header.read_text()
    if old_sha == new_sha or before.count(old) != 1:
        raise ValueError("the exact old config digest must occur once and change")
    after = before.replace(old, new, 1)
    changed = dict(contract, dxvk_config_sha256=new_sha)
    if after != generated_header(changed) or after.replace(new, old, 1) != before:
        raise ValueError("the copied header changed more than its config digest")
    paths = [args.player_dir / name for name in (
        "player.cpp", "input.cpp", "input.h", "mixer.cpp", "mixer.h", "stream.cpp", "stream.h", "sha256.cpp",
        "sha256.h", "texcodec.cpp", "texcodec.h", "names.h", "sdl_mark.h", "build.sh", "generate_expectations.py",
        "dxvk.conf")]
    paths += [args.stream_header, header, receipt, args.config, args.repo / "tools/pins.json",
              args.repo / "tools/s5/play_report.py", args.repo / "tools/s5/frames_to_video.py", Path(__file__)]
    sources = [identity(path) for path in paths]
    (out / "original").mkdir(parents=True)
    (out / "expected").mkdir()
    original = out / "original/sa2_host_expectations.h"
    target = out / "expected/sa2_host_expectations.h"
    original.write_bytes(header.read_bytes())
    (out / "original/expectations.json").write_bytes(receipt.read_bytes())
    target.write_text(after)
    manifest = {
        "scope": "Test-only config contract. Unchanged production source; no startup bypass or run proof.",
        "sources": sources,
        "original_header": identity(original), "changed_header": identity(target),
        "change": {"field": "dxvk_config_sha256", "old": old_sha, "new": new_sha,
                   "old_line": old, "new_line": new, "count": 1},
        "original_contract": contract, "changed_contract": changed,
    }
    (out / "contract.json").write_text(json.dumps(manifest, indent=2) + "\n")


def verify(args):
    out = args.out.resolve(strict=True)
    record = json.loads((out / "contract.json").read_text())
    files = record["sources"] + [record["original_header"], record["changed_header"]]
    changed = [row["path"] for row in files if identity(Path(row["path"])) != row]
    if changed:
        raise ValueError(f"test build inputs changed: {changed}")
    before = Path(record["original_header"]["path"]).read_text()
    after = Path(record["changed_header"]["path"]).read_text()
    change = record["change"]
    if before.count(change["old_line"]) != 1 or after != before.replace(change["old_line"], change["new_line"], 1):
        raise ValueError("the copied header is no longer an exact one-digest change")
    result = {"pass": True, "scope": "File identities only; no player or DXVK outcome inferred.", "files": files}
    (out / "identities-after.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    create = sub.add_parser("prepare")
    for name in ("repo", "player-dir", "bin", "stream-header", "config", "out"):
        create.add_argument("--" + name, type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args) if args.mode == "prepare" else verify(args)
    except (KeyError, OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"refused: {exc}\n")
