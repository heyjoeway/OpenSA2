#!/usr/bin/env python3
"""Package OpenSA2 Android from tested native libraries without changing machine code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sections(data: bytes) -> dict[str, dict]:
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("An input is not a little-endian ELF64 binary")
    offset = struct.unpack_from("<Q", data, 40)[0]
    stride, count, strings = struct.unpack_from("<HHH", data, 58)
    rows = [struct.unpack_from("<IIQQQQIIQQ", data, offset + stride * n) for n in range(count)]
    string_row = rows[strings]
    names = data[string_row[4]:string_row[4] + string_row[5]]
    result = {}
    for row in rows:
        name = names[row[0]:].split(b"\0", 1)[0].decode()
        result[name] = {"type": row[1], "flags": row[2], "address": row[3],
                        "offset": row[4], "size": row[5]}
    return result


def alloc_hashes(data: bytes) -> dict[str, dict]:
    result = {}
    for name, section in sections(data).items():
        if section["flags"] & 2:
            result[name] = {"address": section["address"], "size": section["size"],
                            "flags": section["flags"], "type": section["type"],
                            "sha256": None if section["type"] == 8 else digest(
                                data[section["offset"]:section["offset"] + section["size"]])}
    return result


def verify_allocated(before: bytes, after: bytes, allowed: set[str]) -> dict:
    original, packaged = alloc_hashes(before), alloc_hashes(after)
    if original.keys() != packaged.keys():
        raise ValueError("Allocated ELF sections changed")
    changed = []
    for name in original:
        old, new = original[name], packaged[name]
        if old != new:
            changed.append(name)
            if name not in allowed or old["flags"] & 4:
                raise ValueError(f"Forbidden ELF section change: {name}")
            if {k: v for k, v in old.items() if k != "sha256"} != {
                k: v for k, v in new.items() if k != "sha256"
            }:
                raise ValueError(f"ELF section layout changed: {name}")
    return {"changed_allocated_sections": changed, "before": original, "after": packaged,
            "executable_sections_identical": True}


def sanitize_diagnostics(data: bytes, private_prefix: Path, public_prefix: str) -> tuple[bytes, list[dict]]:
    old = str(private_prefix).encode()
    public = public_prefix.encode()
    if len(public) > len(old):
        raise ValueError("Public diagnostic prefix is longer than the original prefix")
    public += b"-" * (len(old) - len(public))
    section = sections(data)[".rodata"]
    lo, hi = section["offset"], section["offset"] + section["size"]
    patched = bytearray(data)
    changes = []
    for match in re.finditer(rb"[\x20-\x7e]{5,}\0", data[lo:hi]):
        original = match.group()[:-1]
        if old not in original:
            continue
        text = original.decode()
        if text.startswith(str(private_prefix) + "/"):
            source = Path(text)
            if not source.is_file() or source.suffix not in {".h", ".cpp"}:
                raise ValueError(f"Ambiguous source path: {text}")
            source_text = source.read_text()
            if "assert" not in source_text and "dxbc_spv_unreachable" not in source_text:
                raise ValueError(f"Source path has no assertion diagnostic: {text}")
            if "__FILE__" in source_text:
                raise ValueError(f"Explicit __FILE__ use requires manual review: {text}")
            category = "assertion source filename"
        elif text.startswith("void dxbc_spv::ir::forEachPhiOperand(") and "[Proc = (lambda at " in text:
            category = "assertion function name"
        else:
            raise ValueError(f"Ambiguous private string: {text}")
        replacement = original.replace(old, public)
        start = lo + match.start()
        patched[start:start + len(original)] = replacement
        changes.append({"offset": start, "bytes": len(original), "kind": category,
                        "public_string": replacement.decode()})
    if bytes(patched).count(old):
        raise ValueError("The native host still contains a private source path")
    return bytes(patched), changes


def run(command: list[str | Path], env: dict[str, str]) -> str:
    result = subprocess.run([str(v) for v in command], env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed: {result.stderr[-3000:]}")
    return result.stdout + result.stderr


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", "--source-apk", dest="source_apk", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--private-prefix", type=Path, required=True)
    parser.add_argument("--public-prefix", default="/build/OpenSA2-release-1")
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--java", "--java-home", dest="java_home", type=Path, required=True)
    parser.add_argument("--strip", dest="strip_tool", type=Path, required=True)
    parser.add_argument("--sdl-java", type=Path, required=True)
    parser.add_argument("--keystore", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--output", "--out", dest="out", type=Path, required=True,
                        help="Fresh output directory for the APK and verification files")
    parser.add_argument("--version", default="0.1.0-alpha.1")
    parser.add_argument("--version-code", type=int, default=10001)
    parser.add_argument("--deny-file", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", args.version):
        parser.error("Version must be a numeric semantic version with an optional prerelease suffix")
    if not 1 <= args.version_code <= 2_100_000_000:
        parser.error("Version code must be between 1 and 2100000000")
    inputs = (args.source_root, args.private_prefix, args.sdk, args.java_home,
              args.sdl_java, args.source_apk.parent)
    for source in inputs:
        if args.out.resolve().is_relative_to(source.resolve()):
            raise ValueError("Output must be outside all input trees")
        for secret in (args.keystore, args.password_file):
            if secret.parent.resolve().is_relative_to(source.resolve()):
                raise ValueError("Signing directory must be outside all input trees")
    for secret in (args.keystore, args.password_file):
        if secret.parent.resolve().is_relative_to(args.out.resolve()) or args.out.resolve().is_relative_to(
            secret.parent.resolve()
        ):
            raise ValueError("Signing files and output must use separate directory trees")
        if secret.is_symlink() or secret.parent.is_symlink():
            raise ValueError("Signing files and their parent directories must not be symlinks")
        if secret.exists() and secret.stat().st_mode & 0o077:
            raise ValueError("Existing signing files must have private permissions")
        if secret.parent.exists() and secret.parent.stat().st_mode & 0o077:
            raise ValueError("Existing signing directories must have private permissions")
    if args.out.exists():
        raise ValueError("Output directory already exists; use a fresh output path")
    args.out.mkdir(parents=True)
    env = dict(os.environ)
    env["PATH"] = f"{args.java_home / 'bin'}:{env.get('PATH', '')}"
    bt = args.sdk / "build-tools/36.0.0"
    android_jar = args.sdk / "platforms/android-36/android.jar"
    members = {}
    with zipfile.ZipFile(args.source_apk) as archive:
        for name in archive.namelist():
            if name.startswith("lib/") or name == "assets/game.env":
                members[name] = archive.read(name)
    input_native_hashes = {n: digest(data) for n, data in members.items() if n.startswith("lib/")}
    game_name, host_name = "lib/arm64-v8a/libsa2game.so", "lib/arm64-v8a/libmain.so"
    if set(members) != {game_name, host_name, "lib/arm64-v8a/libSDL3.so",
                        "lib/arm64-v8a/libsa2install.so", "assets/game.env"}:
        raise ValueError("Unexpected input library list")
    native = args.out / "native"
    native.mkdir()
    game = native / "libsa2game.so"
    game.write_bytes(members[game_name])
    run([args.strip_tool, "--strip-debug", game], env)
    stripped_game = game.read_bytes()
    host, substitutions = sanitize_diagnostics(members[host_name], args.private_prefix, args.public_prefix)
    proof = {"game": verify_allocated(members[game_name], stripped_game, set()),
             "host": verify_allocated(members[host_name], host, {".rodata"}),
             "diagnostic_changes": substitutions}
    members[game_name], members[host_name] = stripped_game, host
    if any(name.startswith(".debug") for name in sections(stripped_game)):
        raise ValueError("The game retains debug sections")
    java_src, classes, dex, resources, flat = [args.out / n for n in
                                            ("java", "classes", "dex", "res", "flat")]
    for directory in (java_src, classes, dex, flat):
        directory.mkdir()
    for root in (args.sdl_java, args.source_root / "host/android/java"):
        for source in root.rglob("*.java"):
            destination = java_src / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source.read_text().replace("SA2 Recompiled", "OpenSA2"))
    shutil.copytree(args.source_root / "host/android/res", resources)
    strings = resources / "values/strings.xml"
    strings.write_text(strings.read_text().replace("SA2 Recompiled", "OpenSA2"))
    manifest = args.out / "AndroidManifest.xml"
    manifest.write_text((args.source_root / "host/android/AndroidManifest.xml").read_text())
    run([args.java_home / "bin/javac", "--release", "11", "-g:none", "-nowarn", "-encoding", "UTF-8",
         "-classpath", android_jar, "-d", classes, *sorted(java_src.rglob("*.java"))], env)
    run([bt / "d8", "--release", "--min-api", "29", "--lib", android_jar,
         "--output", dex, *sorted(classes.rglob("*.class"))], env)
    for resource in sorted(p for p in resources.rglob("*") if p.is_file()):
        run([bt / "aapt2", "compile", "-o", flat, resource], env)
    unsigned = args.out / "unsigned.apk"
    run([bt / "aapt2", "link", "-o", unsigned, "-I", android_jar, "--manifest", manifest,
         "--min-sdk-version", "29", "--target-sdk-version", "35", "--version-code", str(args.version_code),
         "--version-name", args.version, *sorted(flat.glob("*.flat"))], env)
    with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(dex / "classes.dex", "classes.dex")
        for name, data in members.items():
            archive.writestr(name, data)
        for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            archive.write(args.source_root / name, "assets/" + name)
        for license_file in sorted((args.source_root / "licenses").rglob("*")):
            if license_file.is_file():
                archive.write(license_file, "assets/licenses/" + str(license_file.relative_to(args.source_root / "licenses")))
        archive.writestr("assets/SOURCE.txt", "OpenSA2 corresponding source and modified dependency instructions:\n"
                        "https://github.com/kittycache9/OpenSA2\n")
    aligned = args.out / "aligned.apk"
    run([bt / "zipalign", "-f", "-P", "16", "4", unsigned, aligned], env)
    keystore, password = args.keystore, args.password_file
    for parent in {keystore.parent, password.parent}:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not keystore.exists():
        if password.exists():
            raise ValueError("Signing password exists without its key")
        with password.open("x") as handle:
            handle.write(secrets.token_urlsafe(48) + "\n")
        password.chmod(0o600)
        run([args.java_home / "bin/keytool", "-genkeypair", "-keystore", keystore, "-storetype", "PKCS12",
             "-storepass:file", password, "-keypass:file", password, "-alias", "OpenSA2", "-keyalg", "RSA",
             "-keysize", "4096", "-validity", "10000", "-dname", "CN=kittycache,OU=OpenSA2,O=kittycache9"], env)
        keystore.chmod(0o600)
    if not password.is_file() or keystore.is_symlink() or password.is_symlink():
        raise ValueError("Invalid signing files")
    apk = args.out / f"OpenSA2-{args.version}-android-arm64.apk"
    run([bt / "apksigner", "sign", "--ks", keystore, "--ks-pass", f"file:{password}",
         "--ks-key-alias", "OpenSA2", "--min-sdk-version", "29",
         "--v4-signing-enabled", "false", "--out", apk, aligned], env)
    certificate = run([bt / "apksigner", "verify", "--verbose", "--print-certs", apk], env)
    badging = run([bt / "aapt", "dump", "badging", apk], env)
    if "application-debuggable" in badging or "application-label:'OpenSA2'" not in badging:
        raise ValueError("APK metadata verification failed")
    run([bt / "zipalign", "-c", "-P", "16", "4", apk], env)
    denied = [str(args.private_prefix)]
    if args.deny_file:
        denied += json.loads(args.deny_file.read_text())["terms"]
    with zipfile.ZipFile(apk) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            for term in denied:
                if term.lower().encode() in data.lower() or term.encode("utf-16le") in data:
                    raise ValueError(f"APK privacy check failed in {name}")
    receipt = {"version": args.version, "package": "com.sa2recomp.app", "app_label": "OpenSA2",
               "min_sdk": 29, "target_sdk": 35, "abi": "arm64-v8a", "debuggable": False,
               "input_apk_sha256": digest(args.source_apk.read_bytes()),
               "output_apk": apk.name, "output_apk_sha256": digest(apk.read_bytes()),
               "output_bytes": apk.stat().st_size,
               "input_native_hashes": input_native_hashes,
               "packaged_native_hashes": {n: digest(data) for n, data in members.items() if n.startswith("lib/")},
               "allocated_section_proof": proof, "certificate": certificate,
               "badging": badging, "privacy_check": "pass"}
    (args.out / "verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"apk": str(apk), "sha256": receipt["output_apk_sha256"], "bytes": apk.stat().st_size}))


if __name__ == "__main__":
    main()
