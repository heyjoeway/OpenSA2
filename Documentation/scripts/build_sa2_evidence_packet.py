#!/usr/bin/env python3
"""Build a private address-driven Ghidra evidence packet for owned SA2 code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from sa2_private_work import resolve_private_work


EXPECTED_EXE_SHA256 = "c97b116fbd1d64108cbc8ec403b4a59ba6f815159b9a0ad896668d6c19c1bfe9"
EXPECTED_GHIDRA_ARCHIVE_SHA256 = "93a5d11a9ad510622acaaf908c556a7b9b764d338e78a7567f3689bf5081fd54"
EXPECTED_JDK_ARCHIVE_SHA256 = "ce79869e1307ed8ee1e2baa86a412b1eb5b75d10a01006d788a6f968bcfaee94"
TARGET_PATTERN = re.compile(r"^(?:0x)?([0-9A-Fa-f]{6,8})=([A-Za-z_][A-Za-z0-9_]*)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def beneath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def git_root(path: Path) -> Path:
    """Resolve the current repository root instead of assuming a wrapper layout."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def main() -> None:
    project = Path(__file__).resolve().parents[2]
    repository = git_root(project)
    if repository != project:
        raise SystemExit(
            f"Evidence exporter must live in the Unity repository root: "
            f"expected {project}, got {repository}"
        )
    work = resolve_private_work(project)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True,
                        help="Function as 0xADDRESS=StableName; repeat as needed")
    parser.add_argument("--output", type=Path, required=True,
                        help="Private output directory below work/sa2-unity-hello-world")
    parser.add_argument("--receipt", type=Path,
                        help="Optional tracked, hash-only JSON receipt")
    parser.add_argument("--exe", type=Path,
                        default=work / "LocalAssets/SA2/source/sonic2app-unpacked.exe")
    parser.add_argument("--program", default="sonic2app-unpacked.exe")
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if not beneath(output, work.resolve()):
        raise SystemExit(f"Refusing proprietary output outside ignored work tree: {output}")
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing evidence packet: {output}")
    if beneath(output, repository):
        raise SystemExit(f"Refusing proprietary output inside Git repository: {output}")
    parsed_targets = []
    seen_addresses = set()
    for target in args.target:
        match = TARGET_PATTERN.fullmatch(target)
        if not match:
            raise SystemExit(f"Invalid --target {target!r}; expected 0xADDRESS=StableName")
        address = match.group(1).upper().zfill(8)
        if address in seen_addresses:
            raise SystemExit(f"Duplicate target address: 0x{address}")
        seen_addresses.add(address)
        parsed_targets.append(f"{address}={match.group(2)}")

    executable = args.exe.expanduser().resolve()
    executable_hash = sha256(executable)
    if executable_hash != EXPECTED_EXE_SHA256:
        raise SystemExit(f"Owned unpacked executable hash mismatch: {executable_hash}")

    ghidra = work / "tools/ghidra/ghidra_12.1.3_PUBLIC"
    analyze = ghidra / "support/analyzeHeadless"
    project_dir = work / "tools/ghidra/project"
    project_file = project_dir / "SA2Native.gpr"
    script = project / "Documentation/scripts/ExportSA2EvidencePacket.java"
    java_home = work / "tools/temurin21/jdk-21.0.12.1+1"
    ghidra_archive = work / "tools/ghidra/ghidra_12.1.3_PUBLIC_20260817.zip"
    jdk_archive = work / "tools/temurin21/temurin21.tar.gz"
    for required in (analyze, project_file, script, java_home / "bin/java",
                     ghidra_archive, jdk_archive):
        if not required.exists():
            raise SystemExit(f"Required evidence tool is missing: {required}")
    if sha256(ghidra_archive) != EXPECTED_GHIDRA_ARCHIVE_SHA256:
        raise SystemExit("Pinned Ghidra archive hash mismatch")
    if sha256(jdk_archive) != EXPECTED_JDK_ARCHIVE_SHA256:
        raise SystemExit("Pinned JDK archive hash mismatch")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    script_log = temporary / "ghidra-script.log"
    ghidra_log = temporary / "ghidra.log"
    request = {
        "proprietary_output": True,
        "redistribute": False,
        "executable": str(executable),
        "executable_sha256": executable_hash,
        "ghidra": str(ghidra),
        "project": str(project_file),
        "program": args.program,
        "exporter": str(script),
        "exporter_sha256": sha256(script),
        "targets": [
            {"address": "0x" + item.split("=", 1)[0], "name": item.split("=", 1)[1]}
            for item in parsed_targets
        ],
    }
    (temporary / "request.json").write_text(json.dumps(request, indent=2) + "\n")

    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(java_home)
    environment["PATH"] = str(java_home / "bin") + os.pathsep + environment.get("PATH", "")
    environment["XDG_CONFIG_HOME"] = str(work / "xdg-config")
    command = [
        str(analyze), str(project_dir), "SA2Native",
        "-process", args.program,
        "-noanalysis", "-readOnly",
        "-scriptPath", str(script.parent),
        "-scriptlog", str(script_log),
        "-log", str(ghidra_log),
        "-postScript", script.name, str(temporary), md5(executable), *parsed_targets,
    ]
    try:
        subprocess.run(command, check=True, env=environment)

        expected = []
        for item in parsed_targets:
            address, name = item.split("=", 1)
            stem = f"{address}-{name}"
            expected.extend(temporary / f"{stem}{suffix}"
                            for suffix in (".c", ".asm.txt", ".json"))
        missing = [str(path) for path in expected if not path.is_file() or path.stat().st_size == 0]
        if missing:
            raise SystemExit("Ghidra did not produce complete evidence: " + ", ".join(missing))
        expected_md5 = md5(executable)
        for item in parsed_targets:
            address, name = item.split("=", 1)
            metadata = json.loads((temporary / f"{address}-{name}.json").read_text())
            required_metadata = {
                "program": args.program,
                "entry": "0x" + address.lower(),
                "requested_label": name,
                "executable_md5": expected_md5,
                "image_base": "0x00400000",
                "language_id": "x86:LE:32:default",
                "compiler_spec_id": "windows",
            }
            for key, expected_value in required_metadata.items():
                actual_value = str(metadata.get(key, ""))
                if key in {"entry", "executable_md5", "image_base"}:
                    actual_value = actual_value.lower()
                if actual_value != expected_value:
                    raise SystemExit(f"Ghidra metadata mismatch for {name}.{key}: "
                                     f"expected {expected_value}, got {actual_value}")

        artifacts = []
        for path in sorted(expected):
            artifacts.append({"file": path.name, "sha256": sha256(path), "size": path.stat().st_size})
        receipt = {
            "schema": "sa2-native-evidence-receipt/v1",
            "complete": True,
            "source": {"sha256": executable_hash, "size": executable.stat().st_size},
            "program": args.program,
            "request_sha256": sha256(temporary / "request.json"),
            "tools": {
                "ghidra_version": "12.1.3",
                "ghidra_archive_sha256": sha256(ghidra_archive),
                "jdk_archive_sha256": sha256(jdk_archive),
                "launcher_sha256": sha256(Path(__file__).resolve()),
                "exporter_sha256": sha256(script),
            },
            "analysis": {
                "image_base": "0x00400000",
                "language_id": "x86:LE:32:default",
                "compiler_spec_id": "windows",
            },
            "targets": [
                {"address": "0x" + item.split("=", 1)[0], "label": item.split("=", 1)[1]}
                for item in parsed_targets
            ],
            "artifacts": artifacts,
        }
        temporary.rename(output)
        if args.receipt:
            receipt_path = args.receipt.expanduser().resolve()
            receipt_dir = (project / "Documentation/native-evidence-receipts").resolve()
            if receipt_path.parent != receipt_dir or receipt_path.suffix != ".json":
                raise SystemExit(f"Receipt must be a JSON file directly inside {receipt_dir}")
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            contents = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            if receipt_path.exists() and receipt_path.read_text() != contents:
                raise SystemExit(f"Refusing to replace a different receipt: {receipt_path}")
            receipt_temp = receipt_path.with_suffix(".json.tmp")
            receipt_temp.write_text(contents)
            os.replace(receipt_temp, receipt_path)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(f"SA2_EVIDENCE_PACKET_OK targets={len(parsed_targets)} output={output}")


if __name__ == "__main__":
    main()
