#!/usr/bin/env python3
"""Generate the installer's hash table (host/install's table source) from the install-hashes receipt.

Subtitle: `.venv/bin/python tools/s7/install_table.py` writes
`private/generated/s7/install/sa2_install_table.cpp` (generated, never tracked) and a one-screen
summary beside it; `--check` regenerates in memory and fails when the file on disk differs.

What goes in
------------
* **Every file of `Documentation/receipts/install-hashes-v1.json`** (`tools/hash_install.py`'s
  receipt of the clean Steam tree): path, size, SHA-256. The table carries the receipt's `id` and
  the SHA-256 of the receipt file itself, so a config or a store written under another table is
  detected rather than trusted.
* **Required or known.** `resource/**` and the executable are required: the runtime reads them.
  The rest (`Launcher.exe`, `steam_api.dll`, `SteamMetricsSDK.dll`) are known: the runtime never
  reads them, and the installer checks them only to report a modified install.
* **The executable's per-section check**, in place of its whole-file hash (the user's DRM ruling,
  `Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md`: "The installer verifies the
  shipped executable's data sections and the header fields the runtime reads, never the whole
  file"). The expected values come from the shipped executable (`private/exe/sonic2app.exe`,
  held to its pin in `tools/pins.json` and to the receipt's whole-file hash). Each section is
  compared with the unpacked executable at the same VA, the way `tools/s7/drm_unwrap.py`
  compares them:
  - a section whose loaded bytes equal the unpacked file's is hashed (the bytes the loader in
    `runtime/guest_memory.c` copies: at most the virtual size, from the raw pointer);
  - the code section that differs is not hashed: the release build carries the code bytes the
    runtime reads, and overlays them;
  - a section the unpacked file does not have (SteamStub's `.bind`) is not hashed: nothing the
    lifted program runs reads it;
  - any other differing section is an error: the runtime would read a byte nobody checked.
  The hashed set is held to the DRM receipt's `regions` with no differing byte
  (`Documentation/receipts/s7-drm-unwrap-v1.json`), and that receipt's input hashes to the two
  executables used here, so the two measurements cannot drift apart silently. There is no table
  without this check: the executable never keeps its whole-file hash.

* **The shipped files under `resource/` the receipt does not hash** (its `excluded_under_resource`:
  `resource/gd_PC/StageSelectConfig.ini`, excluded as user config by `tools/hash_install.py`'s
  `.ini` rule). The game must never read one, since nothing verifies it. `unread_by_the_game`
  measures that in the unpacked executable, the code the lifted program is: every string in it
  that names the file's base name (ASCII or UTF-16, case-blind) must name another folder. The
  executable names `StageSelectConfig.ini` only as `.\\Config\\StageSelectConfig.ini`, a user
  file the game reads from `Config/` (the overlay first), never the shipped copy under
  `resource/gd_PC/`, so the table leaves it out and the summary says why. A bare base name, or a
  folder that `resource/gd_PC/` ends with, is refused: the file would have to be hashed. The
  launcher's selftest holds the same at run time: an opened installation file the table does not
  hold fails "launched" by name (host/launch/sa2_launch.cpp, check_run).

What never goes in: a byte of any shipped file. The output carries paths, sizes, hashes and
section-table fields only, and it lives under `private/generated` anyway.

Usage:
  .venv/bin/python tools/s7/install_table.py [--receipt R] [--exe E] [--reference U]
      [--pins tools/pins.json|none] [--drm-receipt D|none] [--out-dir DIR] [--check]

`none` for the pins or the DRM receipt is for synthetic executables (the tests); the summary
says which holds were applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = "Documentation/receipts/install-hashes-v1.json"
SHIPPED = "private/exe/sonic2app.exe"
UNPACKED = "private/exe/sonic2app-unpacked.exe"
PINS = "tools/pins.json"
DRM_RECEIPT = "Documentation/receipts/s7-drm-unwrap-v1.json"
RULING = "Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md"
OUT_DIR = "private/generated/s7/install"
TABLE = "sa2_install_table.cpp"
SUMMARY = "sa2_install_table.txt"      # not .json: the M7 census reads every .json under private/generated
GENERATED_BY = "tools/s7/install_table.py"
EXE_ENTRY = "sonic2app.exe"
REQUIRED_TREE = "resource/"
MEM_EXECUTE = 0x20000000


class TableError(Exception):
    pass


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pe_layout(data: bytes) -> dict:
    """The header fields the runtime reads (tools/s7/drm_unwrap.py's pe_layout, the same offsets)."""
    if data[:2] != b"MZ":
        raise TableError("no MZ header")
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[lfanew:lfanew + 4] != b"PE\0\0":
        raise TableError("no PE signature")
    nsec, = struct.unpack_from("<H", data, lfanew + 6)
    opt_size, = struct.unpack_from("<H", data, lfanew + 20)
    opt = lfanew + 24
    magic, = struct.unpack_from("<H", data, opt)
    if magic != 0x10B:
        raise TableError(f"optional header magic {magic:#x}, not PE32")
    base, = struct.unpack_from("<I", data, opt + 28)
    size_of_image, size_of_headers = struct.unpack_from("<II", data, opt + 56)
    dirs = opt + 96
    imports = struct.unpack_from("<II", data, dirs + 8)
    resources = struct.unpack_from("<II", data, dirs + 16)
    sec_off = opt + opt_size
    sections = []
    for i in range(nsec):
        s = sec_off + 40 * i
        name = data[s:s + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, raw, rawptr = struct.unpack_from("<IIII", data, s + 8)
        chars, = struct.unpack_from("<I", data, s + 36)
        sections.append({"name": name, "va": va, "virtual_size": vsize, "raw_size": raw,
                         "raw_pointer": rawptr, "characteristics": chars})
    return {"e_lfanew": lfanew, "sections_count": nsec, "optional_header_size": opt_size,
            "magic": magic, "image_base": base, "size_of_image": size_of_image,
            "size_of_headers": size_of_headers, "import": list(imports),
            "resource": list(resources), "sections": sections}


def loaded(data: bytes, s: dict) -> bytes:
    """The bytes runtime/guest_memory.c copies for a section: its raw data, clipped to the file
    and to the virtual size when that is nonzero."""
    n = s["raw_size"]
    if s["raw_pointer"] >= len(data):
        n = 0
    elif s["raw_pointer"] + n > len(data):
        n = len(data) - s["raw_pointer"]
    if s["virtual_size"] and n > s["virtual_size"]:
        n = s["virtual_size"]
    return data[s["raw_pointer"]:s["raw_pointer"] + n]


def exe_check(shipped: bytes, reference: bytes) -> dict:
    la, lb = pe_layout(shipped), pe_layout(reference)
    by_va = {s["va"]: s for s in lb["sections"]}
    code_va = next((s["va"] for s in la["sections"] if s["characteristics"] & MEM_EXECUTE), None)
    out = []
    for s in la["sections"]:
        mine = loaded(shipped, s)
        ref = by_va.get(s["va"])
        row = {"name": s["name"], "va": s["va"], "virtual_size": s["virtual_size"],
               "raw_size": s["raw_size"], "characteristics": s["characteristics"]}
        if ref is not None and ref["virtual_size"] == s["virtual_size"] and loaded(reference, ref) == mine:
            row.update(hashed=True, hashed_bytes=len(mine), sha256=sha256_bytes(mine),
                       why_not_hashed="")
        elif ref is None:
            row.update(hashed=False, hashed_bytes=0, sha256="0" * 64,
                       why_not_hashed="added by the wrapper: the unpacked image has no section "
                                      "here, and nothing the lifted program runs reads it")
        elif s["va"] == code_va:
            row.update(hashed=False, hashed_bytes=0, sha256="0" * 64,
                       why_not_hashed="the code section: the release build carries the code "
                                      "bytes the runtime reads and overlays them")
        else:
            raise TableError(f"section {s['name']} at {s['va']:#x} differs from the unpacked "
                             f"file and is not the code section: the runtime would read a byte "
                             f"this check does not cover")
        out.append(row)
    return {"e_lfanew": la["e_lfanew"], "sections_count": la["sections_count"],
            "optional_header_size": la["optional_header_size"], "magic": la["magic"],
            "image_base": la["image_base"], "size_of_image": la["size_of_image"],
            "size_of_headers": la["size_of_headers"], "import": la["import"],
            "resource": la["resource"], "sections": out}


def hold_to_drm_receipt(check: dict, drm: dict, exe_sha: str, ref_sha: str) -> None:
    """The DRM receipt measured these two files, and the hashed set is exactly its equal
    regions (headers excluded)."""
    inputs = drm["inputs"]
    if inputs["shipped"]["sha256"] != exe_sha or inputs["unpacked"]["sha256"] != ref_sha:
        raise TableError("the DRM receipt's inputs are not the executables used here (its shipped "
                         f"{inputs['shipped']['sha256'][:16]}, unpacked {inputs['unpacked']['sha256'][:16]}; "
                         f"here {exe_sha[:16]}, {ref_sha[:16]})")
    base = check["image_base"]
    equal = {int(r["va"], 16) for r in drm["regions"]
             if r["differing"] == 0 and r["shipped"] and r["shipped"] != ["(headers)"]}
    hashed = {base + s["va"] for s in check["sections"] if s["hashed"]}
    if equal != hashed:
        raise TableError(f"the hashed sections {sorted(map(hex, hashed))} are not the DRM "
                         f"receipt's equal regions {sorted(map(hex, equal))}")


def named_strings(data: bytes, name: str) -> list[str]:
    """Every run of printable ASCII characters in `data`, one byte each or UTF-16LE, that holds
    `name` (case-blind), as `strings` reads them, in file order and each once."""
    out: list[str] = []
    low = data.lower()
    for enc, width in (("ascii", 1), ("utf-16-le", 2)):
        def printable(i: int) -> bool:
            b = data[i:i + width]
            return len(b) == width and 32 <= b[0] < 127 and (width == 1 or b[1] == 0)
        needle = name.lower().encode(enc)
        at = low.find(needle)
        while at >= 0:
            start, end = at, at + len(needle)
            while start >= width and printable(start - width):
                start -= width
            while printable(end):
                end += width
            text = data[start:end].decode(enc)
            if text not in out:
                out.append(text)
            at = low.find(needle, at + 1)
    return out


# A folder a string names is "another folder" only when it is a plain relative path of these
# characters: no placeholder ('%s', '$VAR', '{0}') that could be filled with the shipped folder at
# run time, no drive or root, no '..'.
PLAIN_FOLDER = re.compile(r"[a-z0-9_ .-]+(/[a-z0-9_ .-]+)*")


def unread_by_the_game(path: str, executable: bytes) -> str:
    """Why the game never reads the shipped `path`, measured in `executable`: every string naming
    its base name names another folder. Refused (TableError) when a string is the bare name; when
    its folder is not a plain relative one (PLAIN_FOLDER: a placeholder, an absolute path, a '..');
    when its folder and `path`'s are related (either ends with the other, component-wise), so a
    longer path ending in the shipped folder is caught as well as a suffix of it; or when the
    executable names it nowhere at all (a name built at run time is then not ruled out)."""
    folder, _, base = path.lower().rpartition("/")
    seen = named_strings(executable, base)
    if not seen:
        raise TableError(f"{path} is not hashed and the executable names {base} nowhere, so a name "
                         "built at run time is not ruled out; hash it")

    def related(a: str, b: str) -> bool:
        return a == b or a.endswith("/" + b) or b.endswith("/" + a)

    for text in seen:
        t = text.replace("\\", "/").lower()
        t = t[2:] if t.startswith("./") else t
        if not t.endswith(base):
            continue
        where = t[:-len(base)].rstrip("/")
        parts = where.split("/")
        if where == "" or not PLAIN_FOLDER.fullmatch(where) or ".." in parts or \
                any(p in ("", ".") for p in parts) or related(folder, where):
            raise TableError(f"{path} is not hashed, and the executable names it as {text!r}, "
                             "which can reach the shipped copy; hash it")
    return "the executable names it only as " + ", ".join(f'"{t}"' for t in seen)


def c_bytes(hexstr: str) -> str:
    b = bytes.fromhex(hexstr)
    return "{" + ",".join(f"0x{x:02x}" for x in b) + "}"


def c_str(s: str) -> str:
    out = []
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif 0x20 <= ord(ch) < 0x7F:
            out.append(ch)
        else:
            out.extend(f"\\x{x:02x}\"\"" for x in ch.encode("utf-8"))
    return '"' + "".join(out) + '"'


def build(receipt_path: Path, exe: Path, reference: Path, pins: Path | None,
          drm_receipt: Path | None, rel_receipt: str) -> tuple[str, str]:
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw)
    rid = receipt.get("id")
    files = receipt.get("files")
    if not isinstance(rid, str) or not isinstance(files, list) or not files:
        raise TableError(f"{rel_receipt}: no id or no files")
    if receipt.get("modded"):
        raise TableError(f"{rel_receipt} was taken from a modded tree")
    paths = [f["path"] for f in files]
    folded = {}
    for p in paths:
        if folded.setdefault(p.lower(), p) != p:
            raise TableError(f"{p} and {folded[p.lower()]} differ only in case")
    exe_rows = [f for f in files if f["path"] == EXE_ENTRY]
    if len(exe_rows) != 1:
        raise TableError(f"{rel_receipt}: {len(exe_rows)} entries named {EXE_ENTRY}")
    shipped = exe.read_bytes()
    ref_bytes = reference.read_bytes()
    whole, ref_sha = sha256_bytes(shipped), sha256_bytes(ref_bytes)
    if whole != exe_rows[0]["sha256"] or len(shipped) != exe_rows[0]["bytes"]:
        raise TableError(f"{exe} is not the receipt's {EXE_ENTRY}")
    if pins is not None:
        pin = json.loads(pins.read_text())["executables"]
        if whole != pin["sonic2app.exe"]["sha256"]:
            raise TableError(f"{exe} does not match its pin in {pins}")
        if ref_sha != pin["sonic2app-unpacked.exe"]["sha256"]:
            raise TableError(f"{reference} does not match its pin in {pins}")
    check = exe_check(shipped, ref_bytes)
    if drm_receipt is not None:
        hold_to_drm_receipt(check, json.loads(drm_receipt.read_text()), whole, ref_sha)
    unhashed = []
    for row in receipt.get("excluded_under_resource") or []:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise TableError(f"{rel_receipt}: an excluded_under_resource row with no path: {row!r}")
        if row["path"] in paths:
            raise TableError(f"{rel_receipt} both hashes and excludes {row['path']}")
        unhashed.append((row["path"], row.get("reason"), unread_by_the_game(row["path"], ref_bytes)))

    rsha = sha256_bytes(raw)
    lines = [f"// Generated by {GENERATED_BY} from {rel_receipt} (sha256 {rsha}).",
             "// Do not edit. Never tracked: it lives under private/generated (AGENTS.md rule 9).",
             '#include "sa2_install.h"', "", "namespace {", ""]
    lines.append("const Sa2ExeSection kExeSections[] = {")
    for s in check["sections"]:
        lines.append(f"    {{{c_str(s['name'])}, 0x{s['va']:x}u, 0x{s['virtual_size']:x}u, "
                     f"0x{s['raw_size']:x}u, 0x{s['characteristics']:x}u, "
                     f"{'true' if s['hashed'] else 'false'}, {s['hashed_bytes']}u, "
                     f"{c_bytes(s['sha256'])}, {c_str(s['why_not_hashed'])}}},")
    lines.append("};")
    c = check
    lines.append(f"const Sa2ExeCheck kExe = {{0x{c['e_lfanew']:x}u, {c['sections_count']}, "
                 f"0x{c['optional_header_size']:x}, 0x{c['magic']:x}, 0x{c['image_base']:x}u, "
                 f"0x{c['size_of_image']:x}u, 0x{c['size_of_headers']:x}u, "
                 f"0x{c['import'][0]:x}u, 0x{c['import'][1]:x}u, "
                 f"0x{c['resource'][0]:x}u, 0x{c['resource'][1]:x}u, kExeSections, "
                 f"{len(c['sections'])}, {c_str(RULING)}}};")
    lines.append("")
    lines.append("const Sa2InstallEntry kEntries[] = {")
    required = 0
    for f in sorted(files, key=lambda f: f["path"]):
        req = f["path"].startswith(REQUIRED_TREE) or f["path"] == EXE_ENTRY
        required += req
        is_exe = f["path"] == EXE_ENTRY
        digest = "0" * 64 if is_exe else f["sha256"]
        lines.append(f"    {{{c_str(f['path'])}, {int(f['bytes'])}ull, {c_bytes(digest)}, "
                     f"{'true' if req else 'false'}, {'&kExe' if is_exe else 'nullptr'}}},")
    lines.append("};")
    lines += ["", f"const Sa2InstallTable kTable = {{{c_str(rid)}, {c_str(rsha)}, kEntries, "
                  f"{len(files)}, {c_str(GENERATED_BY)}, {required}}};", "", "}  // namespace", "",
              "const Sa2InstallTable& sa2_install_table() { return kTable; }", ""]
    src = "\n".join(lines)
    summary = [f"table {rid} from {rel_receipt} sha256 {rsha}",
               f"entries {len(files)} required {required} known {len(files) - required}",
               f"source sha256 {sha256_bytes(src.encode())}"]
    summary.append(f"executable: per-section check of {RULING}; held to the pins: "
                   f"{'yes' if pins is not None else 'no'}; held to the DRM receipt: "
                   f"{'yes' if drm_receipt is not None else 'no'}")
    for s in check["sections"]:
        summary.append(f"  {s['name']:<8} va {s['va']:#010x} "
                       + (f"hashed {s['hashed_bytes']} bytes {s['sha256'][:16]}"
                          if s["hashed"] else f"not hashed: {s['why_not_hashed']}"))
    summary.append(f"shipped under resource/ and not hashed: {len(unhashed)}")
    for path, reason, why in unhashed:
        summary.append(f"  {path} ({reason}): unread by the game: {why}")
    return src, "\n".join(summary) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--receipt", type=Path, default=ROOT / RECEIPT)
    ap.add_argument("--exe", type=Path, default=ROOT / SHIPPED, help="the shipped executable")
    ap.add_argument("--reference", type=Path, default=ROOT / UNPACKED)
    ap.add_argument("--pins", default=str(ROOT / PINS), help="tools/pins.json, or 'none'")
    ap.add_argument("--drm-receipt", default=str(ROOT / DRM_RECEIPT), help="or 'none'")
    ap.add_argument("--out-dir", type=Path, default=ROOT / OUT_DIR)
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory; fail when the table on disk differs")
    a = ap.parse_args()
    rp = a.receipt.resolve()
    rel = rp.relative_to(ROOT).as_posix() if rp.is_relative_to(ROOT) else rp.name
    exe = a.exe
    pins = None if a.pins == "none" else Path(a.pins)
    drm = None if a.drm_receipt == "none" else Path(a.drm_receipt)
    try:
        src, summary = build(rp, exe, a.reference, pins, drm, rel)
    except (TableError, OSError, KeyError, ValueError) as e:
        print(f"INSTALL_TABLE_FAIL {e}", file=sys.stderr)
        return 1
    out = a.out_dir / TABLE
    if a.check:
        if not out.is_file() or out.read_text() != src:
            print(f"INSTALL_TABLE_STALE {out}")
            return 1
        print(f"INSTALL_TABLE_CURRENT {out}")
        return 0
    a.out_dir.mkdir(parents=True, exist_ok=True)
    if not out.is_file() or out.read_text() != src:
        out.write_text(src)
    (a.out_dir / SUMMARY).write_text(summary)
    print(f"INSTALL_TABLE_OK {out}")
    sys.stdout.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
