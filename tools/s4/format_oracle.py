#!/usr/bin/env python3
"""USER32!wvsprintfA: the runtime's formatter against the oracle's own user32.

Subtitle: one command from the case table to a receipt that says, case by
case, whether `runtime/sa2_format.c` writes what the oracle's user32 writes.

    .venv/bin/python tools/s4/format_oracle.py            # run both, compare, print
    .venv/bin/python tools/s4/format_oracle.py --write    # and write the receipt

Why the oracle and not the documentation
----------------------------------------
wsprintf's documentation is imprecise at the edges and printf's semantics are
not wsprintf's: the width does not count a `#` prefix, the precision counts the
sign, a `%` followed by something that is not a conversion pads to its width.
The user32 the oracle ran is Proton's, so its behaviour is observable and is
what a native capture was taken under. `harness/format_cases.h` is one table
of cases; `harness/format_oracle.c` is a 32-bit Windows console program that
hands each to that user32, and `harness/format_probe.c` hands each to the
runtime's formatter over stubbed guest memory.

Where the oracle is defective -- it writes bytes it did not compute, or past
the limit the documentation sets -- the runtime follows the documentation, and
`DEFECTS` below names each case, why, and what the documented output is. The
tool fails if the runtime differs from the oracle anywhere else, if it differs
from the documentation there, or if the oracle stops showing a named defect.

Where it runs
-------------
In `private/generated/format-oracle/compat`, a prefix of its own that the
wrapper sets up itself (`getcompatpath`, which runs Proton's prefix setup)
and then uses with `runinprefix`, as the probe runner uses the oracle's. It is
neither the Steam installation's prefix nor the oracle's
(`private/proton-prefix`). The program opens no window and reads no input; the
receipt records the desktop session it ran on all the same.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "private" / "generated" / "format-oracle"
COMPAT = OUT / "compat"
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
PROTON = ROOT / "private" / "predecessor-tools" / "proton-private-wrapper" / "proton"
USER32 = PROTON.parent / "files" / "lib" / "wine" / "i386-windows" / "user32.dll"
STEAM_CLIENT = Path.home() / ".local/share/Steam"
RECEIPT = ROOT / "Documentation" / "receipts" / "s4-wsprintf-oracle-v1.json"
PIPELINE = (
    "tools/s4/format_oracle.py",
    "harness/format_cases.h",
    "harness/format_oracle.c",
    "harness/format_probe.c",
    "runtime/sa2_format.c",
    "runtime/sa2_format.h",
    "runtime/sa2_codepage.c",
    "runtime/sa2_codepage.h",
)
TIMEOUT_S = 180
# Longer outputs are kept as a length, a sha256, a head and a tail: the long
# cases' text would otherwise be a run check_boundary.py reads as embedded bytes.
TEXT_MAX = 200

DEFECTS = {
    "prefix-dec": {
        "reason": "`#` on a decimal conversion. The oracle adds two characters to a field's "
                  "count for `#` whether or not it writes a 0x, so on a decimal it copies "
                  "two bytes past the digits it formatted -- a NUL and whatever followed it "
                  "in its buffer. The documentation gives `#` to hexadecimal conversions "
                  "only.",
        "documented": "5|5",
        "documented_ret": 3,
    },
    "limit-prefix": {
        "reason": "a `#` prefix in the last three characters of a full buffer: the same "
                  "defect. The oracle clamps the digits to the room left and then writes the "
                  "two-character prefix as well, so its count passes the limit, and from "
                  "there the rest of the call writes past the 1024 bytes the documentation "
                  "sets (1068 bytes, with 40 characters of text after the field, in the "
                  "review's measurement).",
        "documented": "Q." * 510 + "0xa",
        "documented_ret": 1024,
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def zig(args: list[str], cache: Path) -> None:
    if not ZIG.is_file():
        raise SystemExit(f"the pinned zig is missing: {ZIG}")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(cache / "zc"),
               ZIG_GLOBAL_CACHE_DIR=str(ROOT / "private" / "generated" / "zig-cache-tests"))
    done = subprocess.run([str(ZIG), "cc", *args], capture_output=True, text=True, env=env)
    if done.returncode:
        raise SystemExit(f"zig cc failed:\n{done.stderr}")


def build_probe(out_dir: Path) -> Path:
    """harness/format_probe.c over the runtime's formatter, for this host."""
    exe = out_dir / "format_probe"
    zig(["-target", "x86_64-linux-musl", "-O1", "-Wall", "-Wextra", "-Werror",
         "-I", str(ROOT / "runtime"),
         str(ROOT / "harness" / "format_probe.c"),
         str(ROOT / "runtime" / "sa2_format.c"),
         str(ROOT / "runtime" / "sa2_codepage.c"), "-o", str(exe)], out_dir)
    return exe


def build_oracle_driver() -> Path:
    exe = OUT / "format_oracle.exe"
    zig(["-target", "x86-windows-gnu", "-O2", "-Wall", "-Wextra", "-Werror",
         "-o", str(exe), str(ROOT / "harness" / "format_oracle.c"), "-luser32"], OUT)
    described = subprocess.run(["file", str(exe)], capture_output=True, text=True).stdout
    if "PE32" not in described or not ("80386" in described or "i386" in described):
        raise SystemExit(f"the oracle driver is not a 32-bit PE: {described.strip()}")
    return exe


def proton(*verb: str) -> subprocess.CompletedProcess:
    if not PROTON.is_file():
        raise SystemExit(f"the Proton wrapper is missing: {PROTON}")
    command = ["timeout", "--signal=TERM", "--kill-after=5s", f"{TIMEOUT_S}s", "env",
               f"STEAM_COMPAT_DATA_PATH={COMPAT}",
               f"STEAM_COMPAT_CLIENT_INSTALL_PATH={STEAM_CLIENT}",
               str(PROTON), *verb]
    return subprocess.run(command, cwd=OUT, capture_output=True, text=True)


def ensure_prefix() -> None:
    if (COMPAT / "pfx").is_dir():
        return
    COMPAT.mkdir(parents=True, exist_ok=True)
    done = proton("getcompatpath", "C:\\")
    if done.returncode or not (COMPAT / "pfx").is_dir():
        raise SystemExit(f"Proton did not set up {COMPAT}:\n{done.stderr[-2000:]}")


def lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def run_oracle(exe: Path) -> list[dict]:
    done = proton("runinprefix", "./" + exe.name)
    if done.returncode:
        raise SystemExit(f"the oracle driver exited {done.returncode}:\n{done.stderr[-2000:]}")
    return lines(done.stdout)


def run_probe(exe: Path, *args: str) -> list[dict]:
    done = subprocess.run([str(exe), *args], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"{exe.name} exited {done.returncode}:\n{done.stderr}")
    return lines(done.stdout)


def record(ret: int, out: bytes) -> dict:
    """What a call did, as the receipt keeps it: the return value, how many bytes
    it wrote, and those bytes as Latin-1 text or, when long, a digest of them."""
    doc = {"ret": ret, "touched": len(out)}
    if len(out) <= TEXT_MAX:
        doc["out"] = out.decode("latin-1")
    else:
        doc.update(out_len=len(out), out_sha256=hashlib.sha256(out).hexdigest(),
                   out_head=out[:40].decode("latin-1"), out_tail=out[-40:].decode("latin-1"))
    return doc


def observed(row: dict) -> dict:
    if "fault" in row:
        return {"fault": row["fault"]}
    return record(row["ret"], bytes.fromhex(row["out"]))


def documented(name: str) -> dict:
    d = DEFECTS[name]
    return record(d["documented_ret"], d["documented"].encode("latin-1") + b"\0")


def expected(case: dict) -> dict:
    """What the runtime must produce for a receipt case."""
    return case.get("documented") or case["oracle"]


def mismatches(receipt_cases: list[dict], runtime_rows: list[dict]) -> list[str]:
    """Every way the runtime's rows fail the receipt's cases, in order."""
    problems = []
    names = [c["name"] for c in receipt_cases]
    got = [r["name"] for r in runtime_rows]
    if names != got:
        return [f"the cases differ: the receipt has {names}, the runtime ran {got}"]
    for case, row in zip(receipt_cases, runtime_rows):
        want, have = expected(case), observed(row)
        if have != want:
            problems.append(f"{case['name']}: expected {want}, the runtime produced {have}")
    return problems


def judge(oracle_rows: list[dict], runtime_rows: list[dict]) -> tuple[list[dict], list[str]]:
    problems = []
    if [r["name"] for r in oracle_rows] != [r["name"] for r in runtime_rows]:
        raise SystemExit("the two drivers ran different case lists")
    missing = sorted(set(DEFECTS) - {r["name"] for r in oracle_rows})
    if missing:
        problems.append(f"named defects with no case: {missing}")
    cases = []
    for o, r in zip(oracle_rows, runtime_rows):
        case = {"name": o["name"], "oracle": observed(o)}
        if o["name"] in DEFECTS:
            case["defect"] = DEFECTS[o["name"]]["reason"]
            case["documented"] = documented(o["name"])
            if case["oracle"] == case["documented"]:
                problems.append(f"{o['name']}: the oracle no longer shows the named defect")
        have = observed(r)
        if have != expected(case):
            problems.append(f"{o['name']}: expected {expected(case)}, the runtime produced {have}")
        cases.append(case)
    return cases, problems


def judge_faults(rows: list[dict]) -> list[str]:
    problems = []
    for r in rows:
        if r["status"] == 0 or r["fault"] != r["expect"]:
            problems.append(f"{r['fault_case']}: status {r['status']}, named {r['fault']:#x}, "
                            f"expected {r['expect']:#x}")
    straddle = [r for r in rows if r["fault_case"] == "out-straddles"]
    if not straddle or straddle[0]["written"] != "abcdef":
        problems.append("out-straddles: the bytes before the fault were not written as USER32 "
                        "writes them")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help=f"write {RECEIPT.relative_to(ROOT)}")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    driver = build_oracle_driver()
    probe = build_probe(OUT)
    ensure_prefix()
    oracle_rows = run_oracle(driver)
    runtime_rows = run_probe(probe)
    fault_rows = run_probe(probe, "--faults")
    cases, problems = judge(oracle_rows, runtime_rows)
    problems += judge_faults(fault_rows)

    agree = sum(1 for c in cases if "defect" not in c)
    print(f"{len(cases)} cases: the runtime writes what the oracle's user32 writes in {agree}, "
          f"and what the documentation says in the {len(cases) - agree} where the oracle "
          f"is defective ({', '.join(sorted(DEFECTS))}); {len(fault_rows)} refusals checked")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        raise SystemExit(f"{len(problems)} problem(s); the receipt was not written")
    if not args.write:
        return

    version = (USER32.resolve().parents[4] / "version")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                          text=True, check=True).stdout.strip()
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-wsprintf-oracle-v1",
        "question": "Does the runtime's USER32!wvsprintfA write what the oracle's user32 writes, "
                    "case by case, and where it does not, is that the oracle's defect?",
        "generated_by": "tools/s4/format_oracle.py",
        "commit": head,
        "command": ".venv/bin/python tools/s4/format_oracle.py --write",
        "inputs": {
            "pipeline": {p: sha256(ROOT / p) for p in PIPELINE},
            "oracle": {
                "user32": "Proton's i386 user32.dll, the one the oracle's prefix loads",
                "user32_sha256": sha256(USER32),
                "proton_version": version.read_text().strip() if version.is_file() else None,
                "driver_sha256": sha256(driver),
            },
        },
        "ran_on_live_desktop_session": bool(os.environ.get("DISPLAY")
                                            or os.environ.get("WAYLAND_DISPLAY")),
        "session": {
            "display": os.environ.get("DISPLAY"),
            "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
            "xdg_session_type": os.environ.get("XDG_SESSION_TYPE"),
            "opens_a_window": False,
            "reads_input": False,
            "prefix": str(COMPAT.relative_to(ROOT)),
        },
        "cases": cases,
        "refusals": [{"case": r["fault_case"], "named": r["fault"], "expected": r["expect"]}
                     for r in fault_rows],
        "summary": {
            "cases": len(cases),
            "runtime_writes_what_the_oracle_writes": agree,
            "runtime_follows_the_documentation_where_the_oracle_is_defective": sorted(DEFECTS),
            "refusals_naming_the_right_address": len(fault_rows),
        },
    }
    RECEIPT.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"wrote {RECEIPT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
