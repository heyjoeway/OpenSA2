#!/usr/bin/env python3
"""Compile and run a bounded SA2 Mod Loader probe in the private game copy.

The runner turns the repeated compile/profile-switch/run/capture/restore sequence
into one fail-closed command. It never writes the owned Steam installation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Iterator, Mapping, Sequence, TextIO

from sa2_private_work import resolve_private_work


EXPECTED_EXE_SHA256 = "c97b116fbd1d64108cbc8ec403b4a59ba6f815159b9a0ad896668d6c19c1bfe9"
EXPECTED_ZIG_SHA256 = "2858dc89dbbfdd08cceda1b841e7fd0a793a1a67b49f150bc3d0d1de44ed7f51"
SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SAFE_CAPTURE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
LOCK_TIMEOUT_ENVIRONMENT_VARIABLE = "SA2_NATIVE_PROBE_LOCK_TIMEOUT_SECONDS"
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
MAX_LOCK_TIMEOUT_SECONDS = 300.0


class NativeProbeLockTimeout(RuntimeError):
    """Raised when another native probe holds the private-work lease."""


def lock_timeout_seconds(value: str) -> float:
    """Parse and bound a lock wait supplied at the command/config boundary."""
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_LOCK_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must be between 0 and {MAX_LOCK_TIMEOUT_SECONDS:g} seconds"
        )
    return seconds


def lock_record(mod_id: str, source: Path) -> dict[str, object]:
    return {
        "schema": "sa2-native-probe-lock/v1",
        "owner_pid": os.getpid(),
        "hostname": socket.gethostname(),
        "start_time_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join([sys.executable, *sys.argv]),
        "probe": {"mod_id": mod_id, "source": str(source)},
    }


def _lock_holder_description(lock_file: TextIO) -> str:
    try:
        lock_file.seek(0)
        raw = lock_file.read().strip()
        if not raw:
            return "no owner metadata"
        record = json.loads(raw)
        return json.dumps(record, sort_keys=True, separators=(",", ":"))
    except (OSError, ValueError, TypeError):
        return "unreadable owner metadata"


@contextmanager
def native_probe_lease(
    lock_path: Path,
    wait_seconds: float,
    record: Mapping[str, object],
) -> Iterator[None]:
    """Hold the private native-probe lease until the protected work finishes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    holder = _lock_holder_description(lock_file)
                    raise NativeProbeLockTimeout(
                        f"Timed out after {wait_seconds:g}s waiting for native probe "
                        f"lock {lock_path}; current holder: {holder}"
                    )
                time.sleep(min(0.1, remaining))

        lock_file.seek(0)
        lock_file.truncate()
        json.dump(dict(record), lock_file, indent=2, sort_keys=True)
        lock_file.write("\n")
        lock_file.flush()
        os.fsync(lock_file.fileno())
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path, check=True,
        capture_output=True, text=True,
    )
    return Path(result.stdout.strip()).resolve()


def beneath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def profile(enabled_mods: Sequence[str]) -> dict[str, object]:
    return {
        "SettingsVersion": 3,
        "Graphics": {
            "ScreenMode": 0,
            "HorizontalResolution": 640,
            "VerticalResolution": 480,
            "SkipIntro": True,
        },
        "DebugSettings": {
            "EnableDebugFile": True,
            "EnableDebugCrashLog": True,
        },
        "TestSpawn": {},
        "Patches": {},
        "EnabledMods": list(enabled_mods),
    }


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"Missing {label}: {path}")


def main() -> None:
    script = Path(__file__).resolve()
    project = git_root(script.parent)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Tracked C probe source inside this repository")
    parser.add_argument("--mod-id", required=True,
                        help="Private Mod Loader directory identifier")
    parser.add_argument("--capture-dir", type=Path, required=True,
                        help="New private output directory below work/sa2-unity-hello-world")
    parser.add_argument("--capture", action="append", required=True,
                        help="Output basename written below the private game's mods directory")
    parser.add_argument("--enable-mod", action="append", default=[],
                        help="Additional existing private mod to enable before the probe")
    parser.add_argument("--define", action="append", default=[],
                        help="Compiler definition, NAME or NAME=VALUE")
    parser.add_argument("--timeout", type=int, default=25,
                        help="Bounded native-run time in seconds (5-120)")
    parser.add_argument(
        "--lock-timeout",
        type=lock_timeout_seconds,
        default=os.environ.get(
            LOCK_TIMEOUT_ENVIRONMENT_VARIABLE,
            str(DEFAULT_LOCK_TIMEOUT_SECONDS),
        ),
        help=(
            "Seconds to wait for the private native-probe lease (0-300; "
            f"default {DEFAULT_LOCK_TIMEOUT_SECONDS:g}, override with "
            f"{LOCK_TIMEOUT_ENVIRONMENT_VARIABLE})"
        ),
    )
    parser.add_argument("game_args", nargs=argparse.REMAINDER,
                        help="Arguments after -- passed to sonic2app-unpacked.exe")
    args = parser.parse_args()

    if not SAFE_ID.fullmatch(args.mod_id):
        raise SystemExit("--mod-id must match [a-z][a-z0-9_]{1,63}")
    if not 5 <= args.timeout <= 120:
        raise SystemExit("--timeout must be between 5 and 120 seconds")
    if len(set(args.capture)) != len(args.capture):
        raise SystemExit("Duplicate --capture basename")
    for name in args.capture:
        if not SAFE_CAPTURE.fullmatch(name) or Path(name).name != name:
            raise SystemExit(f"Unsafe capture basename: {name!r}")
    for mod in args.enable_mod:
        if not SAFE_ID.fullmatch(mod):
            raise SystemExit(f"Unsafe --enable-mod identifier: {mod!r}")
    for define in args.define:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:=[A-Za-z0-9_.+-]+)?", define):
            raise SystemExit(f"Unsafe compiler definition: {define!r}")

    source = args.source.expanduser().resolve()
    if not beneath(source, project) or source.suffix.lower() != ".c":
        raise SystemExit("Probe source must be a tracked .c file inside the repository")
    require_file(source, "probe source")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(source.relative_to(project))],
        cwd=project, capture_output=True,
    )
    if tracked.returncode != 0:
        raise SystemExit(f"Probe source is not tracked by Git: {source}")

    work = resolve_private_work(project)
    capture_dir = args.capture_dir.expanduser().resolve()
    if not beneath(capture_dir, work.resolve()) or beneath(capture_dir, project):
        raise SystemExit("Capture directory must be in the private work tree outside Git")

    lock_path = work / "locks/native-probe.lock"
    try:
        with native_probe_lease(
            lock_path,
            args.lock_timeout,
            lock_record(args.mod_id, source.relative_to(project)),
        ):
            run_locked_probe(args, project, work, source, capture_dir)
    except NativeProbeLockTimeout as error:
        raise SystemExit(str(error)) from None


def run_locked_probe(
    args: argparse.Namespace,
    project: Path,
    work: Path,
    source: Path,
    capture_dir: Path,
) -> None:
    """Run one complete private-game transaction while its lease is held."""
    game = work / "native-run/Sonic Adventure 2"
    owned_game = Path.home() / ".local/share/Steam/steamapps/common/Sonic Adventure 2"
    if capture_dir.exists():
        raise SystemExit(f"Refusing to overwrite capture directory: {capture_dir}")

    executable = game / "sonic2app-unpacked.exe"
    zig = work / "tools/zig/zig-x86_64-linux-0.15.2/zig"
    proton = Path.home() / ".local/share/Steam/steamapps/common/Proton - Experimental/proton"
    profiles_root = game / "mods/.modloader"
    profiles_dir = profiles_root / "profiles"
    profiles_index = profiles_dir / "Profiles.json"
    for required, label in (
        (executable, "private unpacked executable"),
        (zig, "pinned Zig compiler"),
        (proton, "Proton launcher"),
        (profiles_index, "Mod Loader profile index"),
    ):
        require_file(required, label)
    if game.resolve() == owned_game.resolve() or beneath(game.resolve(), owned_game.resolve()):
        raise SystemExit("Refusing to run or modify the owned Steam installation")
    if sha256(executable) != EXPECTED_EXE_SHA256:
        raise SystemExit("Private executable hash mismatch")
    if sha256(zig) != EXPECTED_ZIG_SHA256:
        raise SystemExit("Pinned Zig compiler hash mismatch")

    mod_dir = game / "mods" / args.mod_id
    if mod_dir.exists() or mod_dir.is_symlink():
        raise SystemExit(f"Refusing to replace existing private mod directory: {mod_dir}")
    build_dir = work / "native-probe-build" / args.mod_id
    build_dir.mkdir(parents=True, exist_ok=True)
    dll = build_dir / f"{args.mod_id}.dll"
    implib = build_dir / f"{args.mod_id}.lib"
    local_cache = work / "zig-cache" / f"probe-{args.mod_id}-local"
    global_cache = work / "zig-cache/global"
    local_cache.mkdir(parents=True, exist_ok=True)
    global_cache.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["ZIG_LOCAL_CACHE_DIR"] = str(local_cache)
    environment["ZIG_GLOBAL_CACHE_DIR"] = str(global_cache)
    compile_command = [
        str(zig), "cc", "-target", "x86-windows-gnu", "-shared", "-O2",
        "-Wall", "-Wextra", "-Werror", f"-Wl,--out-implib,{implib}",
    ]
    compile_command.extend(f"-D{define}" for define in args.define)
    compile_command.extend(["-o", str(dll), str(source)])
    subprocess.run(compile_command, cwd=project, env=environment, check=True)
    file_result = subprocess.run(
        ["file", str(dll)], check=True, capture_output=True, text=True,
    ).stdout.strip()
    if "PE32" not in file_result or not (
        "Intel 80386" in file_result or "Intel i386" in file_result
    ):
        raise SystemExit(f"Unexpected probe DLL format: {file_result}")
    exports = subprocess.run(
        ["objdump", "-p", str(dll)], check=True, capture_output=True, text=True,
    ).stdout
    for symbol in ("SA2ModInfo", "Init", "OnFrame"):
        if symbol not in exports:
            raise SystemExit(f"Probe DLL does not export {symbol}")

    sources = [game / "mods" / name for name in args.capture]
    for output in sources:
        if not beneath(output.resolve(), (game / "mods").resolve()):
            raise SystemExit(f"Capture escapes private mods directory: {output}")
        if output.exists() or output.is_symlink():
            raise SystemExit(f"Refusing to replace existing capture output: {output}")

    profiles_dir.mkdir(parents=True, exist_ok=True)
    temporary_profile = profiles_dir / f"OpenSA2Probe-{args.mod_id}.json"
    if temporary_profile.exists() or temporary_profile.is_symlink():
        raise SystemExit(f"Refusing to replace existing temporary profile: {temporary_profile}")
    enabled_mods = [*args.enable_mod, args.mod_id]
    original_index = profiles_index.read_bytes()
    temporary_index = {
        "ProfileIndex": 0,
        "ProfilesList": [{
            "Name": f"OpenSA2Probe-{args.mod_id}",
            "Filename": temporary_profile.name,
        }],
    }

    game_args = list(args.game_args)
    if game_args and game_args[0] == "--":
        game_args = game_args[1:]
    command = [
        "timeout", "--signal=TERM", "--kill-after=5s", f"{args.timeout}s",
        "env",
        f"STEAM_COMPAT_DATA_PATH={work / 'proton-prefix'}",
        f"STEAM_COMPAT_CLIENT_INSTALL_PATH={Path.home() / '.local/share/Steam'}",
        str(proton), "runinprefix", "./sonic2app-unpacked.exe", *game_args,
    ]
    run_output = ""
    return_code = -1
    index_mutated = False
    restoration_error: OSError | None = None
    try:
        mod_dir.mkdir()
        shutil.copy2(dll, mod_dir / dll.name)
        (mod_dir / "mod.ini").write_text(
            "Name=OpenSA2 Native Probe\n"
            "Author=Local parity analysis\n"
            "Description=Bounded evidence probe generated by run_sa2_native_probe.py\n"
            "Version=1.0\n"
            f"DLLFile={dll.name}\n"
        )
        temporary_profile.write_text(
            json.dumps(profile(enabled_mods), separators=(",", ":"))
        )
        index_mutated = True
        profiles_index.write_text(json.dumps(temporary_index, separators=(",", ":")))
        completed = subprocess.run(
            command, cwd=game, capture_output=True, text=True,
        )
        return_code = completed.returncode
        run_output = completed.stdout + completed.stderr
    finally:
        try:
            if index_mutated:
                profiles_index.write_bytes(original_index)
        except OSError as error:
            restoration_error = error
        finally:
            temporary_profile.unlink(missing_ok=True)
            shutil.rmtree(mod_dir, ignore_errors=True)

    profile_restored = profiles_index.read_bytes() == original_index
    cleanup_complete = not temporary_profile.exists() and not mod_dir.exists()
    if restoration_error is not None or not profile_restored or not cleanup_complete:
        raise SystemExit(
            "Private profile/mod restoration failed; refusing to report probe result: "
            f"profile_restored={profile_restored} cleanup_complete={cleanup_complete} "
            f"error={restoration_error}"
        )

    missing = [str(path) for path in sources if not path.is_file() or path.stat().st_size == 0]
    if return_code not in (0, 124) or missing:
        capture_dir.mkdir(parents=True)
        (capture_dir / "native-run.log").write_text(run_output)
        failure = {
            "schema": "sa2-native-probe-failure/v1",
            "return_code": return_code,
            "missing_captures": missing,
            "profile_restored": profile_restored,
            "source": str(source.relative_to(project)),
            "source_sha256": sha256(source),
            "dll_sha256": sha256(dll),
            "enabled_mods": enabled_mods,
            "game_args": game_args,
        }
        (capture_dir / "failure.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n"
        )
        raise SystemExit(
            f"Native probe did not complete; diagnostics preserved at {capture_dir}"
        )

    capture_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_capture = Path(tempfile.mkdtemp(
        prefix=capture_dir.name + ".tmp-", dir=capture_dir.parent,
    ))
    try:
        artifacts = []
        for source_output in sources:
            destination = temporary_capture / source_output.name
            shutil.copy2(source_output, destination)
            artifacts.append({
                "file": destination.name,
                "size": destination.stat().st_size,
                "sha256": sha256(destination),
            })
        (temporary_capture / "native-run.log").write_text(run_output)
        manifest = {
            "schema": "sa2-native-probe-run/v1",
            "private": True,
            "redistribute": False,
            "source": str(source.relative_to(project)),
            "source_sha256": sha256(source),
            "executable_sha256": sha256(executable),
            "zig_sha256": sha256(zig),
            "mod_id": args.mod_id,
            "enabled_mods": enabled_mods,
            "defines": args.define,
            "timeout_seconds": args.timeout,
            "return_code": return_code,
            "game_args": game_args,
            "dll": {"size": dll.stat().st_size, "sha256": sha256(dll)},
            "artifacts": artifacts,
            "profile_restored": profile_restored,
        }
        (temporary_capture / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        temporary_capture.rename(capture_dir)
        # Delete game-directory outputs only after the complete capture and
        # manifest have been durably finalized at their unique destination.
        for source_output in sources:
            source_output.unlink()
    except BaseException:
        shutil.rmtree(temporary_capture, ignore_errors=True)
        raise

    print(
        f"SA2_NATIVE_PROBE_OK mod={args.mod_id} exit={return_code} "
        f"captures={len(artifacts)} output={capture_dir}"
    )


if __name__ == "__main__":
    main()
