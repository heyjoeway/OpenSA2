#!/usr/bin/env python3
"""The shipped executable's code-section overlay: the generated table the runtime reads.

Subtitle: `.venv/bin/python tools/s7/text_readset.py` writes
`private/generated/s7/exe-overlay/sa2_exe_overlay_table.c` and its manifest; `--check` builds
them again and fails when the files on disk say anything else.

The user's DRM ruling of 2026-10-11 (`Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md`)
ships no unwrap step: the runtime loads the player's shipped `sonic2app.exe` as-is and overlays
into guest `.text` the bytes it reads there, which the release build carries. This tool makes
what the build carries, in the layout `runtime/sa2_exe_overlay.h` defines:

* **The runs**: the bytes of the unpacked executable's `.text` that the read-set of
  `tools/s7/drm_unwrap.py` names -- each jump table's ranges (`table_ranges` over its
  `jump_tables()`, over the view `measure` builds) and each emulated-instruction site's bytes
  (`emulated_sites()`) -- clipped to `.text`, merged where they overlap or touch, and sorted.
  The read-set is held to the one the ruling's receipt pins: it must equal, range for range,
  the private companion whose digest `Documentation/receipts/s7-drm-unwrap-v1.json` holds, and a
  table with an arm no near dword holds is refused, as `measure` refuses it. A range's part
  outside `.text` must lie in a DATA section's mapped bytes, which the runtime maps as-is and
  verifies by hash, so it needs no run; any other part is refused.
* **The digests**: the unpacked executable's sha256, the read-set's (`readset_digest`, of the
  run list) and the runs' bytes'.
* **The shipped executable's sections, keyed by VA**: each one's kind (TEXT for `.text`, DATA
  for every section the unpacked file has at the same VA with the same entry, UNMAPPED for the
  rest -- SteamStub's `.bind`, which must lie past the unpacked image), its mapped length, for a
  DATA section the sha256 of its mapped bytes, and the unpacked file's name for it, which the
  guest's section list carries. A DATA section whose mapped bytes differ from the unpacked
  file's is refused here: the premise of the overlay is that only `.text` differs.
* **The header fields the loader holds the shipped file to** (`HEADER_AT`, the list
  runtime/sa2_exe_overlay.c `FIELDS` reads), as each file holds them. The manifest lists every
  header byte the loader verifies (`header_verified`), as file offsets: a run of the shipped
  executable under the poison tier reports the header bytes the guest read
  (sa2_exe_overlay_report `header_read`) and those of them outside this list
  (`header_unverified`).

`--omit emulated_sites` or `--omit jump_tables` writes a negative-control table without that
half of the read-set (with `--out` somewhere other than the release path, which refuses one).
The table records what it omits, and the runtime reports it.

What it writes carries code bytes of the game, so it lives under `private/generated` only and is
never tracked (AGENTS.md rule 9). The manifest beside it holds counts, addresses and hashes only,
and is named `.manifest` rather than `.json`: tools/s4/m7_inputs.py reads every `.json` under
private/generated as a possible game-run record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

SCHEMA = "sa2-s7-exe-overlay/v2"
GENERATED_BY = "tools/s7/text_readset.py"
TABLE_VERSION = 2                     # runtime/sa2_exe_overlay.h SA2_EXE_OVERLAY_TABLE_VERSION
SHIPPED = "private/exe/sonic2app.exe"
UNPACKED = "private/exe/sonic2app-unpacked.exe"
PINS = "tools/pins.json"
RECEIPT = "Documentation/receipts/s7-drm-unwrap-v1.json"
OUT_DIR = "private/generated/s7/exe-overlay"
TABLE = f"{OUT_DIR}/sa2_exe_overlay_table.c"
LAYOUT_HEADER = "runtime/sa2_exe_overlay.h"
EXEC = 0x20000000
KINDS = {"TEXT": 1, "DATA": 2, "UNMAPPED": 3}   # runtime/sa2_exe_overlay.h SA2_EXE_SEC_*
OMITTABLE = ("emulated_sites", "jump_tables")
SOURCES = {"jump_tables": "jump_table", "emulated_sites": "emulated_site"}

# The header fields the loader holds the shipped file to: runtime/sa2_exe_overlay.c FIELDS,
# entry for entry -- (name, where its offset is from, offset, size). "coff" is the COFF header
# after the PE signature, "opt" the optional header. The runtime reads the first seven and the
# two directories (guest_memory.c pe_parse, sa2_imports_bind, the dialog resource lookup); the
# guest's C runtime start-up reads the directory count and the CLR entry (its managed-app check).
HEADER_AT = (
    ("e_lfanew", "file", 0x3C, 4),
    ("sections_count", "coff", 2, 2),
    ("optional_header_size", "coff", 16, 2),
    ("magic", "opt", 0, 2),
    ("image_base", "opt", 28, 4),
    ("size_of_image", "opt", 56, 4),
    ("size_of_headers", "opt", 60, 4),
    ("rva_count", "opt", 92, 4),
    ("import_directory.rva", "opt", 104, 4),
    ("import_directory.size", "opt", 108, 4),
    ("resource_directory.rva", "opt", 112, 4),
    ("resource_directory.size", "opt", 116, 4),
    ("clr_directory.rva", "opt", 208, 4),
    ("clr_directory.size", "opt", 212, 4),
)
OPT_READ = 216                        # runtime/sa2_exe_overlay.c OPT_READ
# Of each 40-byte section entry, what the loader verifies: runtime/sa2_exe_overlay.c
# ENTRY_VERIFIED. The name and the raw pointer are not held; the bytes the pointer leads to are.
ENTRY_VERIFIED = ((8, 12, "virtual_size,va,raw_size"), (36, 4, "characteristics"))
SCALARS = tuple(n for n, *_ in HEADER_AT if "." not in n)
PAIRS = ("import_directory", "resource_directory", "clr_directory")


class Refusal(Exception):
    """An input the table cannot be made from, by name."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_path(table: Path) -> Path:
    return table.with_name(table.name + ".manifest")


# ------------------------------------------------------------------ the images


def drm():
    """tools/s7/drm_unwrap.py, the read-set's one definition."""
    sys.path.insert(0, str(ROOT / "tools" / "s7"))
    import drm_unwrap  # noqa: PLC0415
    return drm_unwrap


def layout(data: bytes) -> dict:
    """The PE fields the runtime reads (drm_unwrap.pe_layout), each section with its mapped
    length (drm_unwrap.file_bytes): what runtime/guest_memory.c's map_pe copies."""
    D = drm()
    lay = D.pe_layout(data)
    for s in lay["sections"]:
        s["mapped"] = D.file_bytes(data, s)
    return lay


def section_bytes(data: bytes, s: dict) -> bytes:
    return data[s["raw_pointer"]:s["raw_pointer"] + s["mapped"]]


def at_base(where: str, nt: int) -> int:
    return {"file": 0, "coff": nt + 4, "opt": nt + 24}[where]


def header_values(data: bytes) -> dict:
    """HEADER_AT read from the file as the loader reads it: the scalars, and each directory as
    [rva, size]."""
    nt = struct.unpack_from("<I", data, 0x3C)[0]
    if nt + 24 + OPT_READ > len(data):
        raise Refusal("e_lfanew points past the file")
    raw = {name: struct.unpack_from("<H" if size == 2 else "<I", data, at_base(where, nt) + off)[0]
           for name, where, off, size in HEADER_AT}
    out = {k: raw[k] for k in SCALARS}
    out.update({k: [raw[f"{k}.rva"], raw[f"{k}.size"]] for k in PAIRS})
    return out


def header_verified(h: dict) -> list[list]:
    """Every header byte the loader verifies, as [file offset, length, what] sorted: the MZ and
    PE signatures, HEADER_AT, and ENTRY_VERIFIED of every section entry."""
    nt = h["e_lfanew"]
    out = [[0, 2, "MZ"], [nt, 4, "PE signature"]]
    out += [[at_base(where, nt) + off, size, name] for name, where, off, size in HEADER_AT]
    sec = nt + 24 + h["optional_header_size"]
    out += [[sec + 40 * i + off, size, f"section {i} {what}"]
            for i in range(h["sections_count"]) for off, size, what in ENTRY_VERIFIED]
    return sorted(out)


def text_section(lay: dict) -> dict:
    """The section the unpacked file names `.text`, which must be executable and the only one."""
    found = [s for s in lay["sections"] if s["name"] == ".text"]
    if len(found) != 1 or not found[0]["characteristics"] & EXEC:
        raise Refusal(f"the unpacked executable has {len(found)} executable `.text` sections; "
                      "the overlay is for exactly one")
    return found[0]


def entry(s: dict) -> tuple:
    return (s["va"], s["virtual_size"], s["raw_size"], s["characteristics"])


def section_plan(shipped: bytes, ls: dict, unpacked: bytes, lu: dict) -> list[dict]:
    """The shipped file's sections in header order, each with its kind, its mapped length, the
    unpacked file's name for it and, for DATA, the sha256 of its mapped bytes."""
    text = text_section(lu)
    by_va = {s["va"]: s for s in lu["sections"]}
    end = lu["image_base"] + lu["size_of_image"]
    out = []
    for s in ls["sections"]:
        u = by_va.get(s["va"])
        row = {"name": s["name"], "guest_name": "", "va": s["va"],
               "virtual_size": s["virtual_size"], "raw_size": s["raw_size"],
               "characteristics": s["characteristics"], "mapped": s["mapped"], "sha256": ""}
        if u is not None and entry(u) == entry(s):
            row["guest_name"] = u["name"]
            if u is text:
                if s["mapped"] != u["mapped"]:
                    raise Refusal(f"the shipped .text at {s['va']:#010x} maps {s['mapped']:#x} "
                                  f"bytes, the unpacked one {u['mapped']:#x}")
                row["kind"] = "TEXT"
            else:
                a, b = section_bytes(shipped, s), section_bytes(unpacked, u)
                if a != b:
                    raise Refusal(f"section {s['name']} at {s['va']:#010x} differs between the "
                                  "shipped and the unpacked executable; the overlay covers .text "
                                  "only")
                row.update(kind="DATA", sha256=sha256(a))
        elif u is None and s["va"] >= end:
            row["kind"] = "UNMAPPED"
        else:
            raise Refusal(f"the shipped section {s['name']} at {s['va']:#010x} is neither the "
                          "unpacked file's section at that VA nor past the unpacked image")
        out.append(row)
    if sum(r["kind"] == "TEXT" for r in out) != 1:
        raise Refusal("the shipped executable has no section with the unpacked .text's entry")
    shipped_va = {s["va"] for s in ls["sections"]}
    lost = [s["name"] for s in lu["sections"] if s["va"] not in shipped_va]
    if lost:
        raise Refusal(f"unpacked sections the shipped executable has not: {', '.join(lost)}")
    return out


# ------------------------------------------------------------------ the read-set


def readset_ranges(unpacked: bytes, lu: dict, ls: dict, tables: list[dict], relocated: set[int],
                   sites: list[dict]) -> list[tuple[int, int, str]]:
    """Every byte range of the read-set, before clipping: (lo, hi, source). drm_unwrap.measure's
    own functions over the view it builds -- both files' extents, the unpacked file's bytes --
    and its refusal of a table with an arm no dword near the table holds."""
    D = drm()
    lo = min(lu["image_base"], ls["image_base"])
    hi = max(lu["image_base"] + lu["size_of_image"], ls["image_base"] + ls["size_of_image"],
             *(s["va"] + D.section_extent(s) for s in lu["sections"] + ls["sections"]))
    view = D.mapped(unpacked, lu, lo, hi)

    def dword(x: int) -> int:
        return struct.unpack_from("<I", view, x - lo)[0] if lo <= x <= hi - 4 else -1

    def byte(x: int) -> int:
        return view[x - lo] if lo <= x < hi else D.PAD

    starts = sorted({t["table"] for t in tables}
                    | {t["index_table"] for t in tables if t["index_table"] is not None})
    out, unheld = [], []
    for t in tables:
        rs, _exact, missing = D.table_ranges(t, dword, relocated, starts, byte)
        if missing:
            unheld.append(f"{t['dispatch']:#010x}")
        out += [(x, y, "jump_table") for x, y in rs]
    if unheld:
        raise Refusal("jump tables with an arm no dword near the table holds (drm_unwrap.measure "
                      "refuses them too): " + ", ".join(unheld))
    out += [(s["ip"], s["next_ip"], "emulated_site") for s in sites]
    return out


def companion_ranges(companion: dict) -> list[tuple[int, int, str]]:
    """The read-set as drm_unwrap.measure's companion holds it, in readset_ranges' form."""
    out = []
    for t in companion["jump_tables"]:
        if t.get("arms_in_no_near_dword"):
            raise Refusal(f"the companion's jump table {t['dispatch']} has an arm no near dword "
                          "holds")
        out += [(int(x, 16), int(y, 16), "jump_table") for x, y, *_ in t["ranges"]]
    out += [(int(s["ip"], 16), int(s["ip"], 16) + s["length"], "emulated_site")
            for s in companion["emulated_sites"]]
    return out


def same_readset(ours: list[tuple[int, int, str]], companion: dict) -> None:
    """Refuse unless the read-set computed here is the companion's, range for range."""
    theirs = companion_ranges(companion)
    a, b = sorted(ours), sorted(theirs)
    if a != b:
        only_ours, only_theirs = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        first = (only_ours or only_theirs or [None])[0]
        raise Refusal(f"the read-set computed here ({len(a)} ranges) is not the one the receipt's "
                      f"companion holds ({len(b)}): {len(only_ours)} only here, {len(only_theirs)} "
                      f"only there, first {first}")


def merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted, disjoint, non-adjacent [lo, hi) ranges covering exactly the input's bytes."""
    out: list[list[int]] = []
    for x, y in sorted(r for r in ranges if r[1] > r[0]):
        if out and x <= out[-1][1]:
            out[-1][1] = max(out[-1][1], y)
        else:
            out.append([x, y])
    return [(x, y) for x, y in out]


def clip(ranges: list[tuple[int, int, str]], lo: int, hi: int,
         data: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], dict]:
    """The parts of the ranges inside [lo, hi), and how many ranges reach outside it, by source.
    A part outside must lie inside one of `data`, the DATA sections' mapped [va, end); any other
    part is refused, since nothing would verify the bytes it reads."""
    inside, outside = [], {}
    for x, y, src in ranges:
        a, b = max(x, lo), min(y, hi)
        if a < b:
            inside.append((a, b))
        for p, q in ((x, min(y, lo)), (max(x, hi), y)):
            if p < q and not any(d <= p and q <= e for d, e in data):
                raise Refusal(f"the {src} range {x:#010x}..{y:#010x} reads {p:#010x}..{q:#010x}, "
                              "outside .text and outside every DATA section's mapped bytes")
        if x < lo or y > hi:
            outside[src] = outside.get(src, 0) + 1
    return inside, outside


def readset_digest(text: tuple[int, int], runs: list[tuple[int, int]]) -> str:
    """The read-set's sha256: the `.text` extent and the run list, as canonical JSON."""
    doc = {"text": [text[0], text[1]], "runs": [[x, y - x] for x, y in runs]}
    return sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())


# ------------------------------------------------------------------ the table


def build(shipped: bytes, unpacked: bytes, tables: list[dict], relocated: set[int],
          sites: list[dict], omit: tuple[str, ...] = (), inputs: dict | None = None,
          companion: dict | None = None) -> tuple[str, dict]:
    """The C table and its manifest, from the two files' bytes and the read-set; with
    `companion`, the read-set must be that companion's."""
    bad = sorted(set(omit) - set(OMITTABLE))
    if bad:
        raise Refusal(f"--omit {bad}: only {list(OMITTABLE)} can be omitted")
    ls, lu = layout(shipped), layout(unpacked)
    plan = section_plan(shipped, ls, unpacked, lu)
    text = text_section(lu)
    t_lo, t_hi = text["va"], text["va"] + text["mapped"]
    full = readset_ranges(unpacked, lu, ls, tables, relocated, sites)
    if companion is not None:
        same_readset(full, companion)
    dropped = {SOURCES[o] for o in omit}
    raw = [r for r in full if r[2] not in dropped]
    data = [(r["va"], r["va"] + r["mapped"]) for r in plan if r["kind"] == "DATA"]
    inside, outside = clip(raw, t_lo, t_hi, data)
    runs = merge(inside)
    tbytes = section_bytes(unpacked, text)
    blob = b"".join(tbytes[x - t_lo:y - t_lo] for x, y in runs)
    digests = {"unpacked_sha256": sha256(unpacked), "shipped_sha256": sha256(shipped),
               "readset_sha256": readset_digest((t_lo, t_hi), runs), "bytes_sha256": sha256(blob)}
    hs, hu = header_values(shipped), header_values(unpacked)
    c_text = render_c(digests, omit, hs, hu, plan, (t_lo, t_hi), runs, blob)
    manifest = {
        "schema": SCHEMA, "generated_by": GENERATED_BY, "table_version": TABLE_VERSION,
        "inputs": inputs or {}, "omitted": list(omit), **digests,
        "text": {"va": f"{t_lo:#010x}", "end": f"{t_hi:#010x}"},
        "readset": {"jump_tables": len(tables) if "jump_tables" not in omit else 0,
                    "emulated_sites": len(sites) if "emulated_sites" not in omit else 0,
                    "ranges": len(raw), "ranges_outside_text": outside,
                    "runs": len(runs), "bytes": len(blob),
                    "companion_checked": companion is not None},
        "sections": [{k: (f"{v:#010x}" if k == "va" else v) for k, v in r.items()} for r in plan],
        "header": {"shipped": hs, "unpacked": hu},
        "header_verified": [[f"{o:#05x}", n, what] for o, n, what in header_verified(hs)],
        "table_sha256": sha256(c_text.encode()),
    }
    return c_text, manifest


def c_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def c_name(s: str) -> str:
    return c_string("".join(c if " " <= c <= "~" else "?" for c in s)[:8])


def c_header(h: dict) -> str:
    return ("{" + ", ".join([f".{k} = {h[k]:#x}" for k in SCALARS]
                            + [f".{k} = {{{h[k][0]:#x}, {h[k][1]:#x}}}" for k in PAIRS]) + "}")


def render_c(digests: dict, omit: tuple[str, ...], hs: dict, hu: dict, plan: list[dict],
             text: tuple[int, int], runs: list[tuple[int, int]], blob: bytes) -> str:
    lines = [
        f"/* GENERATED by {GENERATED_BY} -- do not edit, do not commit.",
        " * The shipped executable's code-section overlay (runtime/sa2_exe_overlay.h): code bytes",
        " * of the game, so it lives under private/generated only (AGENTS.md rule 9).",
        f" * unpacked {digests['unpacked_sha256']}",
        f" * shipped  {digests['shipped_sha256']}",
        f" * read-set {digests['readset_sha256']}  runs {len(runs)}  bytes {len(blob)}",
        f" * omitted  {','.join(omit) or '(nothing)'}",
        " */",
        '#include "sa2_exe_overlay.h"',
        "",
        f"static const uint8_t overlay_bytes[{max(len(blob), 1)}] = {{",
    ]
    for i in range(0, len(blob), 16):
        lines.append("    " + ", ".join(f"0x{b:02x}" for b in blob[i:i + 16]) + ",")
    if not blob:
        lines.append("    0,                        /* no runs: byte_count is 0 */")
    lines.append("};")
    lines.append("")
    lines.append(f"static const sa2_exe_overlay_run_t overlay_runs[{max(len(runs), 1)}] = {{")
    off = 0
    for x, y in runs:
        lines.append(f"    {{.va = {x:#010x}, .len = {y - x}, .offset = {off}}},")
        off += y - x
    if not runs:
        lines.append("    {.va = 0, .len = 0, .offset = 0},   /* no runs: run_count is 0 */")
    lines.append("};")
    lines.append("")
    lines.append(f"static const sa2_exe_overlay_section_t overlay_sections[{len(plan)}] = {{")
    for r in plan:
        lines.append(f"    {{.name = {c_name(r['name'])}, .guest_name = {c_name(r['guest_name'])}, "
                     f".va = {r['va']:#010x}, .virtual_size = {r['virtual_size']:#x}, "
                     f".raw_size = {r['raw_size']:#x}, .characteristics = "
                     f"{r['characteristics']:#010x}, .mapped = {r['mapped']:#x}, "
                     f".kind = SA2_EXE_SEC_{r['kind']}, .sha256 = {c_string(r['sha256'])}}},")
    lines.append("};")
    lines += [
        "",
        "const sa2_exe_overlay_table_t sa2_exe_overlay_generated = {",
        f"    .version = {TABLE_VERSION},",
        f"    .generated_by = {c_string(GENERATED_BY)},",
        f"    .unpacked_sha256 = {c_string(digests['unpacked_sha256'])},",
        f"    .shipped_sha256 = {c_string(digests['shipped_sha256'])},",
        f"    .readset_sha256 = {c_string(digests['readset_sha256'])},",
        f"    .bytes_sha256 = {c_string(digests['bytes_sha256'])},",
        f"    .omitted = {c_string(','.join(omit))},",
        f"    .shipped = {c_header(hs)},",
        f"    .unpacked = {c_header(hu)},",
        f"    .sections = overlay_sections, .section_count = {len(plan)},",
        f"    .text_va = {text[0]:#010x}, .text_mapped = {text[1] - text[0]:#x},",
        f"    .runs = overlay_runs, .run_count = {len(runs)},",
        f"    .bytes = overlay_bytes, .byte_count = {len(blob)},",
        "};",
        "",
        "/* Linking this table makes it the shipped-executable loader's (sa2_exe_overlay.h). */",
        "__attribute__((constructor)) static void sa2_exe_overlay_register(void) {",
        "    (void)sa2_exe_overlay_install(&sa2_exe_overlay_generated);",
        "}",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ the command


def receipt_companion(root: Path) -> tuple[dict, dict]:
    """The read-set the ruling's receipt pins: its companion, held to the receipt's digest of it,
    and what the manifest records of the receipt."""
    D = drm()
    rpath = root / RECEIPT
    if not rpath.is_file():
        raise Refusal(f"{RECEIPT} is absent")
    receipt = json.loads(rpath.read_text())
    held = (receipt.get("companion") or {})
    cpath = root / str(held.get("path", D.COMPANION))
    if not cpath.is_file():
        raise Refusal(f"the receipt's companion {held.get('path')} is absent; run "
                      "tools/s7/drm_unwrap.py")
    companion = json.loads(cpath.read_text())
    if D.digest(companion) != held.get("sha256"):
        raise Refusal(f"{held.get('path')} is not the companion {RECEIPT} pins")
    return companion, {"path": RECEIPT, "sha256": sha256(rpath.read_bytes()),
                       "companion": {"path": held["path"], "sha256": held["sha256"]},
                       "verdict": receipt.get("verdict"),
                       "tools": receipt.get("inputs", {}).get("tools", {})}


def pinned(root: Path) -> tuple[bytes, bytes, dict]:
    pins = json.loads((root / PINS).read_text())["executables"]
    shipped, unpacked = (root / SHIPPED).read_bytes(), (root / UNPACKED).read_bytes()
    if sha256(shipped) != pins["sonic2app.exe"]["sha256"] \
            or sha256(unpacked) != pins["sonic2app-unpacked.exe"]["sha256"]:
        raise Refusal("an executable does not match its pin in tools/pins.json")
    D = drm()
    sys.path.insert(0, str(root / "tools" / "s3" / "whole_image"))
    import common as C  # noqa: PLC0415
    # Each tool as it ran: this file, the layout header, and the read-set's census inputs --
    # drm_unwrap.TOOLS outside runtime/ (the runtime files there define what it reads, not the
    # table) and the lift tree's index the jump-table census walks.
    tools = {GENERATED_BY: Path(__file__), LAYOUT_HEADER: root / LAYOUT_HEADER}
    tools.update({t: root / t for t in D.TOOLS if not t.startswith("runtime/")})
    lift = C.LIFT / "next.jsonl"
    inputs = {"shipped": {"path": SHIPPED, "sha256": sha256(shipped)},
              "unpacked": {"path": UNPACKED, "sha256": sha256(unpacked)},
              "pins": {"path": PINS, "sha256": sha256((root / PINS).read_bytes())},
              "lift_index": {"path": str(lift.relative_to(C.ROOT)),
                             "sha256": sha256(lift.read_bytes())},
              "tools": {t: sha256(p.read_bytes()) for t, p in sorted(tools.items())}}
    return shipped, unpacked, inputs


def generate(root: Path, omit: tuple[str, ...]) -> tuple[str, dict]:
    shipped, unpacked, inputs = pinned(root)
    companion, inputs["drm_unwrap_receipt"] = receipt_companion(root)
    D = drm()
    got = D.jump_tables()
    if not (isinstance(got, tuple) and len(got) == 2):
        raise Refusal("tools/s7/drm_unwrap.py jump_tables() no longer returns (tables, relocated)")
    tables, relocated = got
    return build(shipped, unpacked, tables, relocated, D.emulated_sites(), omit, inputs,
                 companion)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=None, help=f"the table to write (default {TABLE})")
    ap.add_argument("--omit", action="append", default=[], choices=OMITTABLE,
                    help="a negative control: leave this half of the read-set out")
    ap.add_argument("--check", action="store_true",
                    help="build again and fail if the files on disk differ")
    args = ap.parse_args()
    out = (args.out if args.out is not None else ROOT / TABLE).resolve()
    if args.omit and out == (ROOT / TABLE).resolve():
        print(f"a table with --omit is a negative control; write it with --out, not to {TABLE}")
        return 2
    try:
        c_text, manifest = generate(ROOT, tuple(sorted(set(args.omit))))
    except Refusal as e:
        print(f"TEXT_READSET_REFUSED {e}")
        return 1
    mpath = manifest_path(out)
    m_text = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    if args.check:
        problems = [f"{p} is absent or differs from a fresh build"
                    for p, want in ((out, c_text), (mpath, m_text))
                    if not p.is_file() or p.read_text() != want]
        for p in problems:
            print(p)
        if not problems:
            print(f"{out} matches; {manifest['readset']['runs']} runs, "
                  f"{manifest['readset']['bytes']} bytes")
        return 1 if problems else 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(c_text)
    mpath.write_text(m_text)
    r = manifest["readset"]
    print(f"wrote {out}: {r['runs']} runs, {r['bytes']} bytes of .text from {r['ranges']} ranges "
          f"({r['jump_tables']} jump tables, {r['emulated_sites']} emulated sites; outside .text "
          f"{r['ranges_outside_text'] or 'none'}); read-set {manifest['readset_sha256'][:16]}; "
          f"omitted {manifest['omitted'] or 'nothing'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
