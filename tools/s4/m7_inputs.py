#!/usr/bin/env python3
"""Regenerate M7's two inputs from the tree they measure, with the full denominator.

Subtitle: `source tools/env.sh && .venv/bin/python tools/s4/m7_inputs.py`

M7 is defined in `Documentation/SA2_RECOMP_DESIGN.md` section 8 as "d3d9/d3dx9
methods implemented / methods called by the binary", from an analyzer call-site
scan. `Documentation/scripts/metrics.py` reads two flat lists of names and
intersects them: `called` in `Documentation/receipts/d3d9-methods.json` and
`implemented` in `private/generated/device-status.json`. This tool writes both,
and it is the one command that clears `check_m7_inputs.py` when that gate says
M7 is stale.

Why it was rewritten (the user's ruling of 2026-10-04)
------------------------------------------------------

M7 read 58/67 from 2026-09-21 until S5's 50 % review, and the review answered
the S5 kill criterion against that row. All nine "called but not implemented"
methods had been implemented four hours after the inputs were last regenerated;
nothing re-ran the tools, and no gate compared the runtime they had read with the
runtime in the tree.
The user ruled (`Documentation/decisions/2026-10-04-S5-user-rulings.md`): "no
split. The surface did not grow; M7 was stale. Regenerate M7 with the full
denominator (recorded runs plus static call sites) and add a staleness check
like run reach's, so M7 cannot silently go stale again." The gate is
`Documentation/scripts/check_m7_inputs.py`; it calls `derive()` below, so the
gate and this tool cannot disagree about what the inputs imply.

The denominator: `called`
-------------------------

The union of six sets, each kept with its provenance in the receipt:

1. **The static scan** (`tools/s4/d3d9_methods.py` Layer B). A site the dataflow
   attributed to a join of interfaces ("IDirect3DPixelShader9|IDirect3DVertexShader9")
   counts under each, because either would have to answer for the site to work;
   `IDirect3DBaseTexture9`, a type the runtime never instantiates, counts under
   the three texture interfaces for the same reason.
2. **The renderer census supplement** (`Documentation/m7-census-supplement.json`):
   methods a person read off unreached renderer call sites the scan cannot
   attribute (tail jumps, method pointers loaded in another block). It is hand
   written, so it is a declared input outside `Documentation/receipts/`, every
   row is re-decoded in the pinned executable by the gate, and it is retired
   when the scan attributes its rows (M7 plan, phase 2).
3. **The reach run's dynamic surface** (`reach.run_json` in
   `Documentation/receipts/s4-run-reach-v1.json`, held fresh by
   `check_run_reach.py`).
4. **Every registered recorded run**: the methods and D3DX entries the
   presentation recorder counted (`counts.json`, schema
   `sa2-present-rec-counts/v1`, `calls > 0`) in each run of
   `Documentation/receipts/m7-recorded-runs.json`. That registry is generated
   here and only grows. A run enters it when (a) a disk census at regeneration
   finds a recorder `counts.json` beside a guest run record (`run.json` with the
   probe's `winmain` section and the device's own table), (b) a receipt under
   `Documentation/receipts/` names it, or (c) `host/d3d9gpu/state_census.sh`
   replays its stream. The gate re-discovers all three on every push. A name a
   registered run reached stays counted when that run is re-recorded with fewer
   methods or deleted (`--retire-missing`): `kept_from_earlier_counts` holds it
   with the runs that changed, so no regeneration can shrink the denominator
   silently, and the gate fails any name or run that HEAD counts and the tree
   does not. `d3d9!Direct3DCreate9` is M4's import, not a device method, and is
   left out.
5. **Every d3dx9_42 import** the scan lists.
6. **The census of every guest run** (the user's ruling of 2026-10-09, item 1:
   "Count the device's per-method table from every game run, with or without the
   recorder"): every method the device's own table counted a call of in any guest
   run record harness/crt_probe.c wrote under private/generated, recorder or not,
   read by a bounded census (update_index: a cached index of the tree, so a push
   stats it and reads only what moved) into an append-only run index, and counted
   in the registry's `device_table_census`, which keeps every name an earlier
   census counted.

`called_by_the_run_but_missed_by_the_scan` is (3 ∪ 4 ∪ 6) − (1), less the d3dx9
entries the scan found an import call site for. The supplement is left out of it
on purpose, so a name a person attributed can never shrink what the scan is shown
to miss.

Every name in `called` must be a named row of the device table or a d3dx9
import. A name that is neither stops this tool and fails the gate: it is naming
drift between the recorder, the scan and the device.

The numerator: `implemented`
----------------------------

What the recording device answers, as the probe printed it from the runtime's
own table (`sa2_d3d9_method_implemented`), in the reach run: the same run M4's
numerator is tied to, so it is fresh whenever `check_run_reach.py` is green.
`tools/s4/device_status.py` flattens it and records the run's sha256 and the
runtime digest; the d3dx9 side is the reach run's import table. The old "most
capable import table" glob is gone: its purpose, "re-running an old probe cannot
walk the metric backwards", is met better by the tie to the fresh reach run,
and a real fall is now visible instead of masked.

It still means "the call returns to the guest". It does not mean the answer is
what a GPU or native D3DX would give, nor that the recorder or either player
handles the call; `private/generated/s4/device-status.json` says so.

The user's second ruling of 2026-10-04
(`Documentation/decisions/2026-10-04-S5-user-rulings-2.md`) defines M7's numerator
as three-way: "M7's numerator: a method counts only when the device answers it,
the recorder captures it, and both players handle it. Methods no recorded run reaches are
exercised by a synthetic stream per method, so the check can fail." This tool
measures it into a block of its own, `three_way_numerator` (the section "the
three-way numerator" below): from two receipts of pins, a current recording of the
game with both hosts' replays of it (`Documentation/receipts/s5-m7-production-v1.json`,
written by `--production`) and one authored stream per method no recorded run reaches
(`Documentation/receipts/s5-m7-synthetic-v1.json`, written by
`tools/s5/m7_synthetic.py`), each method's verdict derived here with one predicate.
The block reads measured, with `held_methods` and `not_held` splitting `called`, only
while both receipts describe the files on disk and every source those files were made
from is the tree's; otherwise it reads not measured (`measured` false, `not_held` null)
with the reason, and `implemented` stays the row's numerator, an upper bound. G5's M7
criterion reads that block, and the gate derives it again from the raw evidence at
every push, so it cannot be set by hand.

The reach run must have been measured on the tree's runtime: this tool refuses
when the reach receipt's `manifest.runtime_sha256` is not the tree's runtime
digest, and names the reach re-run that clears it.

The ceiling and the misses: instrument health (the user's ruling of 2026-10-09)
------------------------------------------------------------------------------

The user read design 8's "methods called by the binary" as "the methods the scan
proves plus what runs observe, which is the union I ruled on 2026-10-04, not every
method the binary could call", with the could-call space handled by the closed
world the device enforces at run time, and dropped "ceiling zero" and "missed
zero" as G5 pass conditions. The M7 row prints both. Every ceiling site is listed
(address, receiver class, code region) in the private scan receipt by
tools/s4/d3d9_methods.py; the tracked receipt carries the list's length and the
sha256 of its canonical form (`ceiling_sites`, ceiling_site_record), because
Documentation/scripts/check_boundary.py refuses a tracked file with 100 or more
guest addresses. A regeneration in which the ceiling or the misses grew against
HEAD's receipt is refused unless `--growth-cause` says why (`instrument_growth`),
and the gate fails such a receipt the same way; a fall is accepted and is the
next bar.

The scan's provenance
---------------------

The scan does not record which `d3d9_methods.py` produced it, so this tool
records it only when it ran the scan itself (`--rescan`, 7 to 13 minutes, no
game run). Without `--rescan` it carries the recorded tool digest forward while
the scan file is byte-identical; a scan it did not run and cannot vouch for
stops it. The scan's vtable layout is held as a digest of what
`d3d9_methods.parse_vtables()` reads, at the runtime file the scan recorded,
so a handler landing in `runtime/sa2_d3d9.c` does not force a rescan and a slot
or name change does.

Who runs it
-----------

The M7 plan owns this tool and the gate. Regenerating M7's inputs is the main
thread's, with this one command, at any integration whose `m7 inputs` gate
reads stale: a runtime change (re-run the reach first), a receipt that names a
recorder run, a guest run on disk that reached a method outside `called`, the
census script naming another stream, or the M7 plan's later phases. If the M7
row then moves, the kill review `design-S5-d3d9-surface` is answered again in
the same commit (`check_kill_criteria.py` holds an open review that names a row
to that row), and its `projection` is carried forward unchanged.

The three-way block reads measured only while every source its evidence was made from is
the tree's, so a push that moves one (either player, the second host, the recorder, the
stream format, the runtime, play_report.py, the fixture's sources) makes the evidence again
first, with one command: `source tools/env.sh && .venv/bin/python tools/s5/m7_synthetic.py
refresh` (REFRESH). It rebuilds both players, re-runs the synthetic cases, replays the
current Metal Harbor recording in both hosts and rewrites both evidence receipts; when the
recorder, the stream format or the runtime moved, it prints the re-recording it needs and
runs nothing. Then this tool regenerates.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/m7_inputs.py                  # regenerate
  .venv/bin/python tools/s4/m7_inputs.py --rescan         # re-run the scan first
  .venv/bin/python tools/s4/m7_inputs.py --retire-missing # keep a deleted run's names
  .venv/bin/python tools/s4/m7_inputs.py --index          # build or update the run index only
  .venv/bin/python tools/s4/m7_inputs.py --growth-cause "TEXT"  # regenerate, recording why the
                                                          # ceiling or the misses grew
  .venv/bin/python tools/s4/m7_inputs.py --production --record DIR --dxvk DIR --sdlgpu DIR \
      --census FILE [--out PATH]   # write the three-way numerator's production receipt
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))

SCAN = "private/generated/s4/receipts/d3d9-methods.json"
DEVICE_STATUS = "private/generated/s4/device-status.json"
STATUS_OUT = "private/generated/device-status.json"
RECEIPT = "Documentation/receipts/d3d9-methods.json"
REGISTRY = "Documentation/receipts/m7-recorded-runs.json"
SUPPLEMENT = "Documentation/m7-census-supplement.json"
REACH = "Documentation/receipts/s4-run-reach-v1.json"
RECEIPTS_DIR = "Documentation/receipts"
D3D9_C = "runtime/sa2_d3d9.c"
IMPORTS_C = "runtime/sa2_imports.c"
CENSUS_SCRIPT = "host/d3d9gpu/state_census.sh"
FUNCTION_MAP = "private/generated/function-map.json"
PINS = "tools/pins.json"
EXE_PIN = "sonic2app-unpacked.exe"
SCAN_TOOL = "tools/s4/d3d9_methods.py"
PIPELINE = ("tools/s4/m7_inputs.py", "tools/s4/device_status.py", SCAN_TOOL)
# The two files this tool writes under Documentation/receipts/; run discovery skips them,
# or the registry would discover itself.
OWN_RECEIPTS = (RECEIPT, REGISTRY)

COUNTS_SCHEMA = "sa2-present-rec-counts/v1"
RECORDER = "host/sa2_present_rec.c"
NOT_A_METHOD = "Direct3DCreate9"            # M4's import, counted there
D3DX = "d3dx9_42"
BASE_TEXTURE = "IDirect3DBaseTexture9"
TEXTURES = ("IDirect3DTexture9", "IDirect3DCubeTexture9", "IDirect3DVolumeTexture9")
COMMAND = "source tools/env.sh && .venv/bin/python tools/s4/m7_inputs.py"
# The one command that makes the three-way numerator's evidence again at the tree's sources.
REFRESH = "source tools/env.sh && .venv/bin/python tools/s5/m7_synthetic.py refresh"
REACH_COMMAND = "source tools/env.sh && .venv/bin/python tools/s4/reach_receipt.py --name reach --replay boot --write"
THREE_WAY_RULING = "Documentation/decisions/2026-10-04-S5-user-rulings-2.md"
THREE_WAY_RULED = ("M7's numerator: a method counts only when the device answers it, the "
                   "recorder captures it, and both players handle it.")


class Refused(Exception):
    """The inputs cannot be regenerated as they stand; the message says what to do."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ------------------------------------------------------------------ the runtime's own tables

ROW = re.compile(r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*([A-Za-z0-9_|\s]+?)\s*,\s*\"([A-Za-z0-9_]+)\"\s*,"
                 r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")
FLAG_WORDS = (("DF_OUT", "out"), ("DF_CAP", "cap"), ("DF_VOID", "void"))


def _macro(text: str, name: str) -> str:
    m = re.search(r"#define\s+" + name + r"\s*\\\n((?:.*\\\n)*.*)\n", text)
    if not m:
        raise Refused(f"{D3D9_C}: no #define {name}")
    return m.group(1).replace("\\\n", "\n")


def flag_string(expr: str) -> str:
    """The probe's spelling of a row's flags (sa2_d3d9_method_flags): out, cap, void."""
    words = {w.strip() for w in expr.split("|")}
    unknown = words - {"0"} - {name for name, _ in FLAG_WORDS}
    if unknown:
        raise Refused(f"{D3D9_C}: a row's flags name {sorted(unknown)}, which this parser "
                      "does not know; teach flag_string() the new flag")
    return ",".join(word for name, word in FLAG_WORDS if name in words)


def parse_device_rows(text: str) -> dict[str, dict[int, dict]]:
    """{interface: {slot: {method, argc, flags, implemented}}} for every g_types entry of
    `runtime/sa2_d3d9.c`, read statically. `implemented` is a non-NULL handler, which is
    what `sa2_d3d9_method_implemented` answers (`r && r->fn`)."""
    macros = {n: _macro(text, n) for n in ("RESOURCE_ROWS", "BASETEXTURE_ROWS")}
    tables: dict[str, dict[int, dict]] = {}
    for m in re.finditer(r"static const d3d_row (ROWS_[A-Z0-9_]+)\[\]\s*=\s*\{(.*?)\n\};",
                         text, re.S):
        body = m.group(2)
        for name, expansion in macros.items():
            body = body.replace(name, expansion)
        rows: dict[int, dict] = {}
        for r in ROW.finditer(body):
            slot = int(r.group(1))
            if slot in rows:
                raise Refused(f"{D3D9_C}: {m.group(1)} names slot {slot} twice")
            rows[slot] = {"method": r.group(4), "argc": int(r.group(2)),
                          "flags": flag_string(r.group(3)),
                          "implemented": r.group(5) != "NULL"}
        tables[m.group(1)] = rows
    order = re.search(r"static d3d_type g_types\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if not order:
        raise Refused(f"{D3D9_C}: no g_types[]")
    out: dict[str, dict[int, dict]] = {}
    for m in re.finditer(r"\{\s*\"([A-Za-z0-9_]+)\"\s*,\s*(ROWS_[A-Z0-9_]+)", order.group(1)):
        if m.group(2) not in tables:
            raise Refused(f"{D3D9_C}: g_types names {m.group(2)}, which was not parsed")
        out[m.group(1)] = tables[m.group(2)]
    if not out:
        raise Refused(f"{D3D9_C}: g_types[] lists no interface")
    return out


IMPORT_ROW = re.compile(r"\{\s*\"d3dx9_42\.dll\"\s*,\s*\"([A-Za-z0-9_]+)\"\s*,\s*(\d+)\s*,"
                        r"\s*([A-Z0-9_]+)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")


def parse_d3dx9_rows(text: str) -> dict[str, bool]:
    """{entry: implemented} for the d3dx9_42.dll rows of `runtime/sa2_imports.c`. An entry
    with no row is refused by name, so only a row with a handler is implemented."""
    rows: dict[str, bool] = {}
    for m in IMPORT_ROW.finditer(text):
        rows[m.group(1)] = rows.get(m.group(1), False) or m.group(4) != "NULL"
    return rows


def device_table_rows(table: list[dict]) -> dict[str, dict[int, dict]]:
    """The probe's printed table in the shape parse_device_rows returns."""
    out: dict[str, dict[int, dict]] = {}
    for r in table:
        out.setdefault(r["interface"], {})[int(r["slot"])] = {
            "method": r["method"], "argc": int(r["argc"]), "flags": r.get("flags") or "",
            "implemented": bool(r["implemented"])}
    return out


def cross_check(printed: dict[str, dict[int, dict]], parsed: dict[str, dict[int, dict]],
                printed_d3dx9: dict[str, bool],
                parsed_d3dx9: dict[str, bool]) -> list[tuple[str, str, object, object]]:
    """Where the numerator a probe printed and the tree's own tables disagree, as
    (name, field, the numerator's value, the tree's value). Independent of every hash: a
    numerator from another runtime is found here even when its digests were refreshed by
    hand. A d3dx9 entry with no row in the tree is refused by name, so it reads False."""
    out: list[tuple[str, str, object, object]] = []
    for iface in sorted(set(printed) | set(parsed)):
        a, b = printed.get(iface), parsed.get(iface)
        if a is None or b is None:
            out.append((iface, "table", a is not None, b is not None))
            continue
        for slot in sorted(set(a) | set(b)):
            x, y = a.get(slot), b.get(slot)
            if x is None or y is None:
                out.append((f"{iface} slot {slot}", "row", x is not None, y is not None))
                continue
            for key in ("method", "argc", "flags", "implemented"):
                if x[key] != y[key]:
                    out.append((name_of(iface, y["method"]), key, x[key], y[key]))
    for name in sorted(set(printed_d3dx9) | set(parsed_d3dx9)):
        x, y = printed_d3dx9.get(name, False), parsed_d3dx9.get(name, False)
        if x != y:
            out.append((f"{D3DX}!{name}", "implemented", x, y))
    return out


def describe(diff: tuple[str, str, object, object]) -> str:
    name, key, numerator, tree = diff
    source = IMPORTS_C if name.startswith(f"{D3DX}!") else D3D9_C
    if key in ("table", "row"):
        return (f"{name}: the {key} is {'present' if numerator else 'absent'} in the numerator "
                f"and {'present' if tree else 'absent'} in {source}")
    return f"{name}: the numerator says {key}={numerator!r} and {source} says {tree!r}"


def layout_digest(d3d9_c_text: str) -> str:
    """sha256 of the vtable layout `d3d9_methods.parse_vtables()` reads (interface ->
    slot -> name and argc, handlers excluded): what the scan attributed against."""
    import d3d9_methods  # noqa: E402 (iced_x86; imported only when needed)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sa2_d3d9.c"
        p.write_text(d3d9_c_text, encoding="utf-8")
        ifaces, _ = d3d9_methods.parse_vtables(p)
    return sha256_bytes(canonical({i: {str(s): v for s, v in sorted(rows.items())}
                                   for i, rows in sorted(ifaces.items())}).encode())


def text_at_sha(root: Path, rel: str, sha: str) -> str | None:
    """The content `rel` had when its sha256 was `sha`: the working tree, or the newest
    commit that held it (read-only git). None when no commit did."""
    path = root / rel
    if path.is_file() and sha256_file(path) == sha:
        return path.read_text(encoding="utf-8")
    try:
        log = subprocess.run(["git", "log", "--format=%H", "--", rel], cwd=root,
                             capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    for commit in log:
        blob = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=root,
                              capture_output=True, check=False).stdout
        if blob and sha256_bytes(blob) == sha:
            return blob.decode("utf-8")
    return None


# ------------------------------------------------------------------ recorded runs

def is_recorder_counts(doc) -> bool:
    return isinstance(doc, dict) and doc.get("schema") == COUNTS_SCHEMA \
        and doc.get("writer") == RECORDER


def counts_methods(doc: dict) -> set[str]:
    """Every method and D3DX entry a recorder `counts.json` says the run called."""
    names: set[str] = set()
    for m in doc.get("methods") or []:
        if int(m.get("calls") or 0) > 0:
            names.add(f"{m['interface']}::{m['method']}")
    for e in doc.get("entries") or []:
        if int(e.get("calls") or 0) > 0 and e.get("name") != NOT_A_METHOD:
            names.add(f"{D3DX}!{e['name']}")
    return names


def is_guest_run(run_doc) -> bool:
    """A run record the probe wrote for a run of the guest: it carries the WinMain section
    and the device's own table. A recorder test's synthetic record carries neither."""
    return isinstance(run_doc, dict) and "winmain" in run_doc \
        and isinstance((run_doc.get("d3d9") or {}).get("implemented"), list)


PATH_PREFIX = "private/generated/"
# A path ends at whitespace, a quote, a separator, `|` or `=` (an environment string such as
# "A=private/generated/x|B=private/generated/y"), or a backslash (a JSON escape).
PATH_BODY = re.compile(r"private/generated/[^\s\"'`,;()\[\]{}<>|=\\]+")


def cited_paths(text: str) -> set[str]:
    """Every private/generated path `text` names. The match restarts at every occurrence of
    the prefix, so a path joined to another without a separator is found too."""
    found: set[str] = set()
    start = text.find(PATH_PREFIX)
    while start >= 0:
        m = PATH_BODY.match(text, start)
        if m:
            found.add(m.group(0))
        start = text.find(PATH_PREFIX, start + 1)
    return found


def run_dir_named_by(root: Path, cited: str) -> str | None:
    """The recorder run a path names: the directory itself, its counts.json, or any file
    beside a counts.json. None when the path names no recorder run."""
    cited = cited.rstrip(".:/")
    p = root / cited
    if p.is_dir():
        d = p
    elif p.is_file():
        d = p.parent
    else:
        return None
    counts = d / "counts.json"
    if not counts.is_file():
        return None
    try:
        if not is_recorder_counts(json.loads(counts.read_text())):
            return None
    except (OSError, ValueError):
        return None
    return d.relative_to(root).as_posix()


def receipt_named_runs(root: Path, receipts: Path | None = None) -> dict[str, set[str]]:
    """{run dir: {receipt}} for every recorder run a receipt under Documentation/receipts/
    names, outside this tool's own two files. On disk, not in git: a receipt the commit
    under construction adds counts before it is committed."""
    receipts = receipts or root / RECEIPTS_DIR
    out: dict[str, set[str]] = {}
    for path in sorted(receipts.rglob("*.json")):
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        if rel in OWN_RECEIPTS:
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue        # check_receipt_pins.py fails an unreadable receipt
        # The raw text, not the parsed strings: a path in a JSON string has no escapes, and
        # the two mask receipts are 5 MB each.
        for cited in sorted(cited_paths(text)):
            run = run_dir_named_by(root, cited)
            if run is not None:
                out.setdefault(run, set()).add(rel)
    return out


CENSUS_TABLE = re.compile(r"^declare -A STREAM=\((.*?)^\)", re.M | re.S)
CENSUS_ENTRY = re.compile(r'^\s*\[(\w+)\]=("?)\$REPO/(private/generated/[^\s"]+?\.sa2s)\2\s*$')
CENSUS_DEFAULT = re.compile(r'^STREAMS=\$\{STREAMS:-([A-Za-z0-9_ ]+)\}\s*$', re.M)


def census_streams(root: Path) -> dict[str, str]:
    """{name: stream} the R2b state census replays (host/d3d9gpu/state_census.sh): every
    entry of its `declare -A STREAM=(...)` table, which must name exactly the streams of its
    STREAMS default. An entry this parser cannot read is refused by its text, never skipped,
    so a stream written in a new form cannot drop out of M7's denominator unseen."""
    path = root / CENSUS_SCRIPT
    if not path.is_file():
        raise Refused(f"{CENSUS_SCRIPT} is absent, so the streams host 2 is checked against "
                      "cannot be read")
    text = path.read_text()
    table = CENSUS_TABLE.search(text)
    if not table:
        raise Refused(f"{CENSUS_SCRIPT} has no `declare -A STREAM=(...)` table; teach "
                      "census_streams() its new form")
    found: dict[str, str] = {}
    for line in table.group(1).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = CENSUS_ENTRY.match(line)
        if not m:
            raise Refused(f"{CENSUS_SCRIPT}: the STREAM entry {line.strip()!r} is in a form "
                          "census_streams() cannot read; teach it the form")
        if m.group(1) in found:
            raise Refused(f"{CENSUS_SCRIPT}: the STREAM table names {m.group(1)} twice")
        found[m.group(1)] = m.group(3)
    default = CENSUS_DEFAULT.search(text)
    if not default:
        raise Refused(f"{CENSUS_SCRIPT} has no `STREAMS=${{STREAMS:-...}}` default; teach "
                      "census_streams() its new form")
    names = default.group(1).split()
    if sorted(names) != sorted(found):
        raise Refused(f"{CENSUS_SCRIPT} replays {names} by default and its STREAM table names "
                      f"{sorted(found)}: a census stream would be left out of M7's denominator")
    return found


STATUS_LINE = re.compile(r"closed: (\d+) records, (\d+) bytes(?:, (\d+) frames)?")


def stream_run(root: Path, stream: str, candidates: list[str]) -> str | None:
    """The recorder run that wrote `stream`: the run directory it sits in, else the one
    candidate whose counts record the stream's byte size and record count."""
    p = root / stream
    beside = run_dir_named_by(root, stream)
    if beside is not None:
        return beside
    status = Path(str(p) + ".status")
    if not p.is_file() or not status.is_file():
        return None
    m = STATUS_LINE.search(status.read_text())
    if not m:
        return None
    records, size = int(m.group(1)), int(m.group(2))
    frames = int(m.group(3)) if m.group(3) is not None else None
    if size != p.stat().st_size:
        return None
    hits = []
    for run in candidates:
        try:
            doc = json.loads((root / run / "counts.json").read_text())
        except (OSError, ValueError):
            continue
        kinds = doc.get("stream_records") or {}
        if doc.get("stream_bytes") == size and sum(kinds.values()) == records \
                and (frames is None or kinds.get("frame") == frames):
            hits.append(run)
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ the census of every guest run
#
# The user's ruling of 2026-10-09 (Documentation/decisions/2026-10-09-S5-user-rulings.md, item 1):
# "Count the device's per-method table from every game run, with or without the recorder, so a
# new method grows the list and turns M7 red at the next push." A guest run record is the JSON
# harness/crt_probe.c writes for a run of the game: it starts with PROBE_HEADER and carries the
# WinMain section and the device's own table (is_guest_run). That table (`d3d9.implemented`) has
# one row per named method with the device's own count of its calls, so it names every method
# the run called, with or without the recorder. A file in that record's shape without the
# probe's header (a test's planted fixture) is not the game's and is not counted here; the
# recorder census (disk_runs) keeps its own rule, as before.
#
# Discovery is bounded. A walk of private/generated lists about two million directories and 1.2
# million JSON files, 1.5 to 2.5 minutes on a loaded machine (measured 2026-10-09), so no push
# walks it. The index (INDEX_DIR) caches every directory's mtime and every JSON file's size and
# mtime, over the walk disk_runs() used to make (a directory holding `.git` is a checkout and is
# not entered; `vfsroot` is the guest's own files; nothing below a symbolic link but the children
# of private/generated). An update stats every cached entry (about three seconds for two
# million), lists only the directories whose mtime moved, walks only directories it has not
# seen, and reads only JSON files that are new or whose size or mtime moved. A run written as a
# new file moves its directory's mtime, and a run rewritten in place moves its file's size or
# mtime, so the update reads it either way; a guest run record on disk cannot be left out without
# the update seeing it change. An index that is absent fails the gate, never rebuilt behind it:
# `--index` builds it once, a full walk. A guest run record is a file named *.json (every launcher
# in the tree names it so); a name holding a tab or a newline is reported, not skipped.
#
# What the update reads goes into an append-only run index (INDEX_DIR/runs.json): a record keeps
# every name its table ever reached at its path, and one deleted or rewritten keeps them, marked
# gone, so a name a run reached never leaves the census. A launcher that rewrites one path run
# after run can add each run as soon as it is written (index_run_record), before a later run at
# that path replaces it; between two updates, a run replaced at the same path is otherwise read
# only as its last version. A runs.json that is gone while its tree.tsv is there is refused, never
# started again empty behind the gate: the names of runs deleted since would go with it.
#
# What the census does not count, stated so the records match the instrument (CENSUS_EXCLUDES):
# a directory holding `.git` and everything below it (a checkout: a unit's clone or a
# falsification overlay, whose runs were made from that checkout's sources and are its own
# gate's); `vfsroot`; anything reached through a symbolic link below private/generated's own
# children; a file directly in private/generated; a record whose name does not end in .json; a
# run.json in the record's shape without the probe's first bytes (a test's planted fixture); and,
# between two updates, every version but the last of a record rewritten at one path, until its
# launcher calls index_run_record.
#
# The census recognises a record by bytes and keys copied from the probe, and the probe is not in
# the runtime digest, so nothing else would show a probe that began writing another shape: its
# runs would read as `n` and be left out without a word. probe_control_problems() holds the
# reading to the writer at every push: harness/crt_probe.c's record must begin with PROBE_HEADER
# and write the WinMain section and the device table with {interface, method, calls} rows, and
# the reach run and each production recording's run record must read as guest run records.

INDEX_DIR = "private/generated/m7-run-index"
INDEX_SCHEMA = "sa2-recomp-m7-run-index/v1"
TREE_PREFIX = "# sa2-recomp-m7-run-index tree/"
# The first bytes of harness/crt_probe.c's run record (`fprintf(out, "{\n \"va\": \"0x%08x\"...`).
PROBE_HEADER = b'{\n "va": "0x'
# The functions whose reading of a JSON file the tree caches as its verdict. The tree records
# the reading it was made by (reader_id), so a cache made by another reading is never trusted:
# a verdict is kept only while the file's size and mtime hold, and a run cached as `n` under a
# reading that had drifted from the probe would otherwise never be read again once the reading
# was brought back to the probe.
READER_FUNCTIONS = ("file_verdict", "is_guest_run", "device_table_names", "is_recorder_counts",
                    "counts_methods")


def reader_id() -> str:
    """A digest of what the census reads a file by: PROBE_HEADER and the source of
    READER_FUNCTIONS as they stand (a function with no source, such as a test's stand-in, by
    its code object)."""
    import inspect
    h = hashlib.sha256(PROBE_HEADER)
    for name in READER_FUNCTIONS:
        f = globals().get(name)
        try:
            text = inspect.getsource(f)
        except (OSError, TypeError):
            code = getattr(f, "__code__", None)
            text = repr((name, getattr(f, "__qualname__", None),
                         code.co_code if code is not None else None,
                         code.co_consts if code is not None else None))
        h.update(b"\0" + name.encode() + b"\0" + text.encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def tree_header() -> str:
    return (f"{TREE_PREFIX}v2 reader {reader_id()}: D|X mtime_ns dir; F size mtime_ns verdict "
            f"file\n")


class ReaderMoved(Exception):
    """The tree's cached verdicts were read by another reading of the probe's record."""
SKIP_DIRS = ("vfsroot",)
INDEX_COMMAND = COMMAND + " --index"
# A probe record that does not parse is a run still writing it while this young; older, it is a
# run that stopped mid-write, whose table cannot be counted.
WRITING_SECONDS = 900
# An update waits this long for another's lock on the index (a build takes minutes); the gate
# waits GATE_LOCK_WAIT_SECONDS, so a hung update fails a push with its holder named instead of
# stalling it.
LOCK_WAIT_SECONDS = 1800
GATE_LOCK_WAIT_SECONDS = 120
PROBE_WRITER = "harness/crt_probe.c"
CENSUS_EXCLUDES = (
    "a directory holding .git and everything below it (a checkout: a unit's clone or a "
    "falsification overlay, whose runs were made from that checkout's sources), vfsroot (the "
    "guest's own files), anything reached through a symbolic link below private/generated's own "
    "children, a file directly in private/generated, a record whose name does not end in .json, a "
    "run.json in the record's shape without the probe's first bytes (a test's planted fixture), "
    "and, between two updates, every version but the last of a record rewritten at one path "
    "unless its launcher indexes each run as it writes it (index_run_record)")
# Inode times come from the kernel's coarse clock, so a directory or file changed again within
# one tick of being stat'ed keeps the mtime the cache recorded (the racy case git's index has).
# An entry stat'ed this soon after its mtime is cached as racy (mtime 0) and looked at again by
# the next update.
RACY_NS = 2_000_000_000
# File verdicts: g a guest run record, its table read; u a guest run record whose table cannot be
# read; t a probe record that is not whole JSON; p one still being written; q a run.json in the
# guest record's shape without the probe's header (the recorder census's rule); c a recorder
# counts.json; n anything else.
UNREADABLE = {"u": "its device table has a row that is not {interface, method, calls}",
              "t": "it is not whole JSON (a run that stopped while writing it)"}
# What to do with a record the census cannot count. A live session's record (under
# LIVE_SESSIONS) is the user's own play and is preserved before anything else is done with it.
LIVE_SESSIONS = "private/generated/s5/input/"
UNREADABLE_REMEDY = ("Re-run each. A record under " + LIVE_SESSIONS + " is a live session's (the "
                     "user's own play): copy it into private/evidence/ first and never only delete "
                     "it. Then move the record out of private/generated, or remove it with "
                     ".venv/bin/python tools/rmtree.py <its literal path>.")


@dataclasses.dataclass
class Census:
    recorder_runs: list[str]            # disk_runs(): a recorder counts.json beside a guest run.json
    device_runs: dict[str, set[str]]    # every guest run record on disk, and the names it reached
    indexed: dict[str, set[str]]        # every guest run record the run index holds, gone ones too
    unreadable: dict[str, str]          # what the census cannot count, and why
    stats: dict                         # what the update did


def device_table_names(doc: dict) -> set[str] | None:
    """The methods a guest run record's device table says the run called (`calls` > 0), or None
    when a row is not {interface, method, calls} with an integer count."""
    rows = (doc.get("d3d9") or {}).get("implemented")
    if not isinstance(rows, list):
        return None
    out: set[str] = set()
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("interface"), str) \
                or not isinstance(r.get("method"), str) or type(r.get("calls")) is not int:
            return None
        if r["calls"] > 0:
            out.add(name_of(r["interface"], r["method"]))
    return out


def file_verdict(path: str, name: str, mtime_ns: int) -> tuple[str, set[str] | None]:
    """(verdict, the names a guest run record's table reached) for one JSON file: only a file
    that starts with PROBE_HEADER, or is named run.json or counts.json, is read past its head."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOATIME", 0) | getattr(os, "O_NONBLOCK", 0))
    except PermissionError:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return "n", None
    except OSError:
        return "n", None
    try:
        # Most JSON files are read for their first bytes alone; no readahead past them.
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_RANDOM)
        head = os.read(fd, len(PROBE_HEADER))
        probe = head == PROBE_HEADER
        if not probe and name not in ("run.json", "counts.json"):
            return "n", None
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_SEQUENTIAL)
        chunks = [head]
        while True:
            more = os.read(fd, 1 << 20)
            if not more:
                break
            chunks.append(more)
        data = b"".join(chunks)
    except OSError:
        return "n", None
    finally:
        os.close(fd)
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        if probe:
            young = dt.datetime.now(dt.timezone.utc).timestamp() - mtime_ns / 1e9 < WRITING_SECONDS
            return ("p" if young else "t"), None
        return "n", None
    if not probe:
        if name == "counts.json":
            return ("c" if is_recorder_counts(doc) else "n"), None
        return ("q" if name == "run.json" and is_guest_run(doc) else "n"), None
    if not is_guest_run(doc):
        return "n", None
    names = device_table_names(doc)
    return ("u", None) if names is None else ("g", names)


# What probe_control_problems read, keyed by real path, size and mtime: the gate's falsifications
# read the same unchanged files through every overlay.
_CONTROL_VERDICTS: dict[tuple, tuple] = {}
VERDICT_SAYS = {"n": "not a guest run record (its first bytes are not the probe's header, or it "
                     "carries no WinMain section or device table)",
                "q": "a run.json in the guest record's shape without the probe's first bytes",
                "c": "a recorder counts file", "p": "a record still being written", **UNREADABLE}


def c_string_literals(text: str) -> list[tuple[int, str]]:
    """(offset, value) of every string literal in C source `text`, its escapes read; comments
    and character literals ('"') are skipped, and adjacent literals are separate items."""
    out: list[tuple[int, str]] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif text[i] in "'\"":
            q, j = text[i], i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            if q == '"':
                body = text[i + 1:j]
                out.append((i, body.encode("latin-1", "backslashreplace").decode("unicode_escape")))
            i = j + 1
        else:
            i += 1
    return out


def probe_writer_problems(root: Path) -> list[str]:
    """Why harness/crt_probe.c's run-record writer no longer writes what the census reads: the
    record's first bytes (the first fprintf to the record file once it is opened, up to its first
    conversion, must be PROBE_HEADER), the WinMain section, and the device table with one
    {interface, method, calls} row per method (is_guest_run, device_table_names). A probe that
    wrote another shape would leave every game run since out of the census without a word."""
    rel = PROBE_WRITER
    try:
        real = os.path.realpath(root / rel)
        st = os.stat(real)
        key = ("writer", real, st.st_size, st.st_mtime_ns)
        if key in _CONTROL_VERDICTS:
            return list(_CONTROL_VERDICTS[key])
        text = Path(real).read_text(encoding="utf-8")
    except OSError as e:
        return [f"{rel} cannot be read ({e}), so nothing shows the census of every game run still "
                f"reads the probe's run record"]
    out = _probe_writer_problems(rel, text)
    _CONTROL_VERDICTS[key] = tuple(out)
    return out


def _probe_writer_problems(rel: str, text: str) -> list[str]:
    out: list[str] = []
    lits = c_string_literals(text)
    at = {k: v for k, v in lits}
    opened = re.search(r"\bout\s*=\s*fopen\s*\(\s*json_path\b", text)
    first = re.compile(r"\bfprintf\s*\(\s*out\s*,\s*(?=\")").search(text, opened.end()) if opened else None
    if first is None or first.end() not in at:
        out.append(f"{rel}: the census cannot find where the probe opens its run record "
                   f"(out = fopen(json_path, ...)) and writes the record's first line, so it cannot "
                   f"show that a game run written now is recognised (m7_inputs.PROBE_HEADER). Bring "
                   f"probe_writer_problems in tools/s4/m7_inputs.py to the writer, then: {COMMAND}")
    else:
        head = at[first.end()].split("%", 1)[0]
        # Any write to the record before that fprintf would come before its first bytes.
        between = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text[opened.end():first.start()])
        early = [m.group(1) for m in re.finditer(
            r"\b(fputs|fputc|putc|fwrite|fprintf|vfprintf|jstr|fputws|fputwc|putwc)\s*\(([^;]*)\)\s*;",
            between) if re.search(r"\bout\b", m.group(2))]
        if early:
            out.append(f"{rel} writes to its run record ({', '.join(early)}) before the fprintf "
                       f"the census reads as the record's first line, so the record's first bytes "
                       f"are not that line's and the census of every game run "
                       f"({PROBE_HEADER.decode()!r}, m7_inputs.PROBE_HEADER) may not recognise a "
                       f"game run written now. Bring probe_writer_problems in tools/s4/m7_inputs.py "
                       f"to the writer, then: {COMMAND}")
        if head.encode("utf-8") != PROBE_HEADER:
            out.append(f"{rel} begins its run record with {head!r} and the census of every game "
                       f"run recognises a record by {PROBE_HEADER.decode()!r} (m7_inputs.PROBE_HEADER), "
                       f"so a game run written now would not be counted. Bring PROBE_HEADER to the "
                       f"writer, then: {COMMAND}")
    template = "".join(v for _, v in lits)
    d3d9 = template.find(' "d3d9": {')
    impl = template.find('"implemented": [', d3d9) if d3d9 >= 0 else -1
    row = template[impl:impl + 400] if impl >= 0 else ""
    missing = [what for what, ok in (
        ("the WinMain section (\"winmain\": {)", ' "winmain": {' in template),
        ("the device's section (\"d3d9\": {)", d3d9 >= 0),
        ("the device's table (\"implemented\": [, after \"d3d9\")", impl >= 0),
        ("the table rows' interface, method and calls columns",
         impl >= 0 and all(k in row for k in ('{"interface": ', '"method": ', '"calls": %u')))) if not ok]
    if missing:
        out.append(f"{rel} no longer writes {'; '.join(missing)} where the census of every game run "
                   f"reads them (m7_inputs.is_guest_run, device_table_names), so a game run written "
                   f"now would not be counted. Bring the census to the writer, then: {COMMAND}")
    return out


def control_records(root: Path) -> list[str]:
    """The run records the census must recognise at every push: the reach run, and the run
    record of each production recording the three-way evidence names (both written by the probe
    for a run of the game)."""
    rels: list[str] = []
    try:
        reach = json.loads((root / REACH).read_text())
        run_json = (reach.get("reach") or {}).get("run_json")
        if isinstance(run_json, str):
            rels.append(run_json)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        blocks = production_blocks(json.loads((root / PRODUCTION).read_text())) or []
    except (OSError, ValueError, AttributeError):
        blocks = []
    for b in blocks:
        d = (b.get("recording") or {}).get("dir") if isinstance(b.get("recording"), dict) else None
        if isinstance(d, str) and f"{d}/run.json" not in rels:
            rels.append(f"{d}/run.json")
    return rels


def probe_control_problems(root: Path) -> list[str]:
    """Why the census could miss a game run without a word: the probe's writer drifted from
    the census's reading (probe_writer_problems), or a run record the probe wrote for a run of
    the game (control_records) does not read as a guest run record with a method called. A
    control record that is absent is left to the checks that own it."""
    out = probe_writer_problems(root)
    for rel in control_records(root):
        p = root / rel
        try:
            real = os.path.realpath(p)
            st = os.stat(real)
        except OSError:
            continue
        key = (real, st.st_size, st.st_mtime_ns)
        if key not in _CONTROL_VERDICTS:
            _CONTROL_VERDICTS[key] = file_verdict(real, p.name, st.st_mtime_ns)
        verdict, names = _CONTROL_VERDICTS[key]
        if verdict != "g" or not names:
            says = VERDICT_SAYS.get(verdict, verdict) if verdict != "g" else \
                "a guest run record whose device table counted no call"
            out.append(f"the census of every game run does not recognise {rel}, a run record the "
                       f"probe wrote for a run of the game: it reads as {says}. tools/s4/m7_inputs.py's "
                       f"reading (PROBE_HEADER, is_guest_run, device_table_names) has drifted from what "
                       f"{PROBE_WRITER} writes, so a game run could go uncounted. Bring the census to "
                       f"the writer, then: {COMMAND}")
    return out


class _Tree:
    """The index's cache of private/generated, keyed by the path below it ("s4/run")."""

    def __init__(self, root: Path, index_dir: str) -> None:
        base = root / "private" / "generated"
        self.index_real = str((root / index_dir).resolve())
        self.tops: dict[str, str] = {}
        if base.is_dir():
            for child in sorted(base.iterdir()):
                real = child.resolve()
                if real.is_dir() and str(real) != self.index_real:
                    self.tops[child.name] = str(real)
        self.dirs: dict[str, tuple[str, int]] = {}            # key: (D or X, mtime_ns)
        self.files: dict[str, tuple[int, int, str]] = {}      # key: (size, mtime_ns, verdict)
        self.found: dict[str, tuple[int, int, set[str]]] = {}  # g files read in this update
        self.unnamable: list[str] = []
        self.listed = self.read = 0

    def real(self, key: str) -> str | None:
        top, _, rest = key.partition("/")
        base = self.tops.get(top)
        return None if base is None else (base + "/" + rest if rest else base)

    def load(self, path: Path) -> bool:
        try:
            fh = open(path, encoding="utf-8")
        except FileNotFoundError:
            return False
        dirs, files = self.dirs, self.files
        header = tree_header()
        with fh:
            first = fh.readline()
            if first != header:
                if first.startswith(TREE_PREFIX):
                    raise ReaderMoved(
                        f"{path} was made by another reading of the probe's run record "
                        f"({first.strip()[len(TREE_PREFIX):].split(':', 1)[0]}; this one is "
                        f"{header.strip()[len(TREE_PREFIX):].split(':', 1)[0]}), so a file it "
                        f"cached as not a game run may be one under this reading and would never "
                        f"be read again. Build it again, which reads every file anew: "
                        f"{INDEX_COMMAND}")
                raise Refused(f"{path} is not a run index tree ({header.strip()}); build it "
                              f"again: {INDEX_COMMAND}")
            try:
                for line in fh:
                    p = line.rstrip("\n").split("\t")
                    if p[0] == "F" and len(p) == 5:
                        files[p[4]] = (int(p[1]), int(p[2]), p[3])
                    elif p[0] in ("D", "X") and len(p) == 3:
                        dirs[p[2]] = (p[0], int(p[1]))
                    else:
                        raise ValueError(f"a line reads {line[:80]!r}")
            except (ValueError, UnicodeDecodeError) as e:
                raise Refused(f"{path} is not a whole run index tree ({e}); build it again: "
                              f"{INDEX_COMMAND}") from e
        return True

    def lines(self):
        yield tree_header()
        for d, (k, m) in self.dirs.items():
            yield f"{k}\t{m}\t{d}\n"
        for f, (s, m, v) in self.files.items():
            yield f"F\t{s}\t{m}\t{v}\t{f}\n"

    def _stat(self, key: str):
        """The directory's stat: a child of private/generated through its link, any other
        without following its last component. None when it is gone."""
        real = self.real(key)
        if real is None:
            return None
        try:
            return os.stat(real) if "/" not in key else os.lstat(real)
        except OSError:
            return None

    def _drop_under(self, key: str) -> None:
        pre = key + "/"
        for d in [d for d in self.dirs if d.startswith(pre)]:
            del self.dirs[d]
        for f in [f for f in self.files if f.startswith(pre)]:
            del self.files[f]

    def _note_file(self, key: str, real: str, size: int, mtime: int) -> None:
        old = self.files.get(key)
        if old is not None and old[0] == size and old[1] == mtime and old[2] != "p":
            return
        verdict, names = file_verdict(real, key.rsplit("/", 1)[-1], mtime)
        self.read += 1
        self.files[key] = (size, mtime if time.time_ns() - mtime > RACY_NS else 0, verdict)
        if verdict == "g":
            self.found[key] = (size, mtime, names)

    def scan(self, key: str) -> list[str]:
        """List one directory, its mtime taken first so that an entry added while it is listed
        moves it again; returns its subdirectories the cache has not seen."""
        st = self._stat(key)
        # Only a directory the cache held as walked can have cached entries below it to drop.
        walked = self.dirs.get(key, ("",))[0] == "D"
        try:
            if st is None or not stat.S_ISDIR(st.st_mode):
                raise OSError
            entries = list(os.scandir(self.real(key)))
        except OSError:
            self.dirs.pop(key, None)
            if walked:
                self._drop_under(key)
            return []
        self.listed += 1
        if any(e.name == ".git" for e in entries):
            if walked:
                self._drop_under(key)
            self.dirs[key] = ("X", st.st_mtime_ns if time.time_ns() - st.st_mtime_ns > RACY_NS else 0)
            return []
        self.dirs[key] = ("D", st.st_mtime_ns if time.time_ns() - st.st_mtime_ns > RACY_NS else 0)
        new = []
        for e in entries:
            name = e.name
            if "\t" in name or "\n" in name:
                self.unnamable.append(f"{key}/{name!r}")
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    child = key + "/" + name
                    if name not in SKIP_DIRS and e.path != self.index_real and child not in self.dirs:
                        new.append(child)
                elif name.endswith(".json") and e.is_file(follow_symlinks=False):
                    s = e.stat(follow_symlinks=False)
                    self._note_file(key + "/" + name, e.path, s.st_size, s.st_mtime_ns)
            except OSError:
                continue
        return new

    def walk(self, keys: list[str]) -> None:
        stack = list(keys)
        while stack:
            stack.extend(self.scan(stack.pop()))

    def update(self) -> None:
        """Bring the cache to the disk: stat every cached directory and file, re-list each
        directory whose mtime moved, walk each directory not seen, re-read each file whose size
        or mtime moved."""
        moved = []
        for key, (_, mtime) in list(self.dirs.items()):
            st = self._stat(key)
            if st is None:
                self.dirs.pop(key, None)            # its cached files are gone with it
            elif not stat.S_ISDIR(st.st_mode):      # a link or a file now: what it held may resolve
                self.dirs.pop(key, None)
                self._drop_under(key)
            elif st.st_mtime_ns != mtime:
                moved.append(key)
        for key, (size, mtime, verdict) in list(self.files.items()):
            real = self.real(key)
            try:
                st = os.lstat(real) if real is not None else None
            except OSError:
                st = None
            if st is None or not stat.S_ISREG(st.st_mode):
                del self.files[key]
            elif st.st_size != size or st.st_mtime_ns != mtime or verdict == "p":
                self._note_file(key, real, st.st_size, st.st_mtime_ns)
        for key in moved:
            if key in self.dirs:
                self.walk(self.scan(key))
        self.walk([t for t in self.tops if t not in self.dirs])
        # A cached directory whose parent the cache no longer holds as walked (it was dropped as
        # gone or as a checkout while this one's own path still resolved) is not walked.
        for d in [d for d in self.dirs if "/" in d and self.dirs.get(d.rsplit("/", 1)[0], ("",))[0] != "D"]:
            if d in self.dirs:
                del self.dirs[d]
                self._drop_under(d)
        for f in [f for f in self.files if self.dirs.get(f.rsplit("/", 1)[0], ("",))[0] != "D"]:
            del self.files[f]


class _IndexLock:
    """An exclusive lock on the index for one update (fcntl), waited for up to `wait` seconds.
    The holder writes who it is into the lock file, so one that waited too long names it."""

    def __init__(self, idx: Path, wait: float = LOCK_WAIT_SECONDS, by: str = "") -> None:
        self.path = idx / "lock"
        self.fh = None
        self.wait = wait
        self.by = by

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    self.fh.close()
                    try:
                        holder = self.path.read_text(encoding="utf-8").strip()
                    except OSError:
                        holder = ""
                    raise Refused(f"the run index {self.path.parent} stayed locked by another update "
                                  f"for {self.wait:g} s (held by {holder or 'an update that did not say'}); "
                                  f"wait for it, or stop it if it is hung, then run again")
                time.sleep(min(1.0, max(self.wait / 10, 0.05)))
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"pid {os.getpid()} {self.by or 'an update'} since "
                      f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n")
        self.fh.flush()
        return self

    def __exit__(self, *exc) -> None:
        import fcntl
        self.fh.seek(0)
        self.fh.truncate()          # no holder named once it is released
        self.fh.flush()
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _write_atomic(path: Path, text) -> None:
    """Write `text` (a string, or an iterable of strings) beside `path` and move it into place."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        if isinstance(text, str):
            fh.write(text)
        else:
            fh.writelines(text)
    os.replace(tmp, path)


RUNS_GONE = ("the run index {where} is gone while its tree is there, so the names of guest runs "
             "deleted since it was written would be lost without a word (the census only grows). "
             "Restore it; or rebuild it, which keeps on disk only the runs still there while the "
             "registry keeps every name it counted (kept_from_earlier_census): " + INDEX_COMMAND)


def _load_runs(path: Path) -> dict:
    if not path.is_file():
        return {"schema": INDEX_SCHEMA, "runs": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"{path} is not JSON ({e}): the names gone runs reached would be lost. Restore "
                      f"it; or move it aside and run {INDEX_COMMAND}, and the registry keeps every "
                      f"name it counted (kept_from_earlier_census)") from e
    runs = doc.get("runs") if isinstance(doc, dict) else None
    if doc.get("schema") != INDEX_SCHEMA or not isinstance(runs, dict) or not all(
            isinstance(e, dict) and isinstance(e.get("names"), list) for e in runs.values()):
        raise Refused(f"{path} is not a run index ({INDEX_SCHEMA}, runs of names); restore it")
    return doc


def _runs_text(doc: dict) -> str:
    doc = {**doc, "schema": INDEX_SCHEMA, "note": (
        "Every guest run record (harness/crt_probe.c's JSON) under private/generated that "
        "tools/s4/m7_inputs.py's census has read, with every name its device table ever reached "
        "(calls > 0) at that path. Append-only: a record deleted or rewritten keeps its names and "
        "is marked gone. M7's denominator counts every name here (the user's ruling of 2026-10-09, "
        "item 1). Private: it names private paths.")}
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"


def _record(runs: dict, rel: str, size: int, mtime: int, names: set[str], by: str, now: str) -> None:
    e = runs.setdefault(rel, {"names": [], "first_indexed_utc": now, "indexed_by": by})
    e["names"] = sorted(set(e["names"]) | set(names))
    e.update(size=size, mtime_ns=mtime, last_indexed_utc=now, gone=False)


def update_index(root: Path, *, build: bool = False, by: str = "", write: bool = True,
                 index_dir: str | None = None, lock_wait: float = LOCK_WAIT_SECONDS,
                 rebuild_runs: bool = False) -> Census:
    """The census of every guest run under `root`'s private/generated, with the index brought up
    to date (and written back when `write`). Raises Refused when the index is absent, or was made
    by another reading of the probe's record (reader_id), and `build` is not given: building it
    is a full walk, never a push's. Raises Refused when its run index (runs.json) is gone while
    its tree is there, unless `rebuild_runs` (only --index gives it): a run index started again
    empty would lose the names of runs deleted since without a word, so neither a push nor a
    regeneration starts it again."""
    index_dir = index_dir or INDEX_DIR
    idx = root / index_dir
    started = time.monotonic()
    if not build and not (idx / "tree.tsv").is_file():
        raise Refused(f"the run index {index_dir}/tree.tsv is absent, so the census of every guest "
                      f"run cannot be brought up to date at a push's cost. Build it once (a full "
                      f"walk of private/generated, several minutes): {INDEX_COMMAND}")
    with _IndexLock(idx, lock_wait, by):
        if (idx / "tree.tsv").is_file() and not (idx / "runs.json").is_file() and not rebuild_runs:
            raise Refused(RUNS_GONE.format(where=f"{index_dir}/runs.json"))
        tree = _Tree(root, index_dir)
        try:
            had = tree.load(idx / "tree.tsv")
        except ReaderMoved as e:
            if not build:
                raise Refused(str(e)) from e
            tree = _Tree(root, index_dir)       # every file is read again under this reading
            had = False
        except Refused:
            if not build:
                raise
            tree = _Tree(root, index_dir)       # a build walks the whole tree again
            had = False
        runs_doc = _load_runs(idx / "runs.json")
        tree.update()
        runs = runs_doc["runs"]
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for key, (size, mtime, names) in tree.found.items():
            _record(runs, "private/generated/" + key, size, mtime, names, by, now)
        live = {}
        for key, (size, mtime, verdict) in tree.files.items():
            if verdict != "g":
                continue
            rel = "private/generated/" + key
            if rel not in runs:     # the run index lost an entry the tree holds: read it again
                v, names = file_verdict(tree.real(key), key.rsplit("/", 1)[-1], mtime)
                if v != "g":
                    tree.files[key] = (size, mtime, v)
                    continue
                _record(runs, rel, size, mtime, names, by, now)
            live[rel] = set(runs[rel]["names"])
        for rel, e in runs.items():
            e["gone"] = rel not in live
        if write:
            _write_atomic(idx / "runs.json", _runs_text(runs_doc))
            _write_atomic(idx / "tree.tsv", tree.lines())
    files = tree.files
    recorder = sorted("private/generated/" + d for d, (k, _) in tree.dirs.items() if k == "D"
                      and (files.get(d + "/counts.json") or (0, 0, ""))[2] == "c"
                      and (files.get(d + "/run.json") or (0, 0, ""))[2] in ("g", "q", "u"))
    unreadable = {"private/generated/" + f: UNREADABLE[v] for f, (_, _, v) in files.items()
                  if v in UNREADABLE}
    unreadable.update({"private/generated/" + n: "its name holds a tab or a newline, which the "
                       "index cannot hold" for n in tree.unnamable})
    return Census(recorder_runs=recorder, device_runs=live,
                  indexed={rel: set(e["names"]) for rel, e in runs.items()}, unreadable=unreadable,
                  stats={"built": not had, "directories": len(tree.dirs), "json_files": len(files),
                         "listed": tree.listed, "read": tree.read, "runs_on_disk": len(live),
                         "runs_indexed": len(runs), "seconds": round(time.monotonic() - started, 1)})


def index_run_record(root: Path, path: Path, *, by: str, index_dir: str | None = None,
                     wait: float = 120) -> bool:
    """Add one run record to the run index as soon as it is written (for a launcher that reuses
    one path run after run, such as tools/s4/loop_run.py), so a later run at the same path cannot
    replace it unread. True when it is a guest run record. The tree cache is left to the next
    update, which finds the same file and the same names."""
    path = Path(path)
    real = path.resolve()
    # The record's path as the census keys it: below private/generated by the name it was
    # written under (a child of private/generated may be a link), else by its real path.
    base = root / "private" / "generated"
    try:
        rel = "private/generated/" + Path(os.path.abspath(path)).relative_to(os.path.abspath(base)).as_posix()
    except ValueError:
        try:
            rel = "private/generated/" + real.relative_to(base.resolve()).as_posix()
        except ValueError:
            return False
    st = real.stat()
    verdict, names = file_verdict(str(real), real.name, st.st_mtime_ns)
    if verdict != "g":
        return False
    idx = root / (index_dir or INDEX_DIR)
    with _IndexLock(idx, wait, by):
        if (idx / "tree.tsv").is_file() and not (idx / "runs.json").is_file():
            raise Refused(RUNS_GONE.format(where=f"{index_dir or INDEX_DIR}/runs.json"))
        doc = _load_runs(idx / "runs.json")
        _record(doc["runs"], rel, st.st_size, st.st_mtime_ns, names, by,
                dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        _write_atomic(idx / "runs.json", _runs_text(doc))
    return True


def disk_runs(root: Path, census: Census | None = None) -> list[str]:
    """Every recorder run under private/generated beside a guest run record: a recorder
    counts.json beside a run.json with the probe's winmain section and the device's own table,
    read from the census (update_index) rather than a walk.

    A directory holding a `.git` entry is a checkout of the repository, or a falsification
    overlay of one, under some unit's scratch directory: its runs, planted or copied, are
    that copy's and not this tree's, so the census does not enter it. A planted run outside
    any checkout is still found, and turns the gate red until it is deleted."""
    census = census if census is not None else update_index(root, build=True, by="disk_runs")
    return list(census.recorder_runs)


def disk_census(root: Path, census: Census | None = None) -> dict[str, set[str]]:
    """{run: the names its counts say it reached} for every recorder run disk_runs() finds."""
    out: dict[str, set[str]] = {}
    for run in disk_runs(root, census):
        try:
            out[run] = counts_methods(json.loads((root / run / "counts.json").read_text()))
        except (OSError, ValueError):
            continue
    return out


def run_counts(root: Path, run: str) -> tuple[str, set[str]] | None:
    p = root / run / "counts.json"
    if not p.is_file():
        return None
    doc = json.loads(p.read_text())
    if not is_recorder_counts(doc):
        raise Refused(f"{run}/counts.json is not a recorder counts file ({COUNTS_SCHEMA})")
    return sha256_file(p), counts_methods(doc)


def device_census_section(previous: dict | None, device: dict[str, set[str]] | None) -> dict:
    """The registry's record of the census of every guest run's device table: how many guest
    run records the run index holds, each name they reached with how many reach it, and every
    name an earlier census counted that the index no longer holds (an index rebuilt or lost),
    kept, so the census never shrinks. `device` None carries the previous record forward."""
    prev = (previous or {}).get("device_table_census") or {}
    if device is None:
        return prev
    reached: dict[str, int] = {}
    for names in device.values():
        for n in names:
            reached[n] = reached.get(n, 0) + 1
    before = set(prev.get("reached") or {}) | set(prev.get("kept_from_earlier_census") or [])
    return {
        "rule": ("every guest run record harness/crt_probe.c wrote under private/generated (its "
                 "header, the WinMain section and the device's own table), with or without the "
                 "recorder: the methods its device table counted a call of (calls > 0, the "
                 "device's implemented methods and the ones it refuses alike), read by "
                 "tools/s4/m7_inputs.py's census into the append-only run index. Not counted: "
                 + CENSUS_EXCLUDES),
        "index": f"{INDEX_DIR}/runs.json",
        "runs_indexed": len(device),
        "reached": dict(sorted(reached.items())),
        "kept_from_earlier_census": sorted(before - set(reached)),
    }


def census_names(registry: dict | None) -> set[str]:
    """Every name the census of every guest run's device table counts (the registry's record)."""
    dev = (registry or {}).get("device_table_census") or {}
    return set(dev.get("reached") or {}) | set(dev.get("kept_from_earlier_census") or [])


def build_registry(root: Path, previous: dict | None, *, disk: list[str],
                   retire_missing: bool, device: dict[str, set[str]] | None = None) -> dict:
    """The registry: every run previously registered, every run found now, and the names
    only a deleted run reached, kept; and the census of every guest run's device table
    (`device`, the run index's runs and their names). Raises Refused when a registered run is
    gone and `retire_missing` was not given."""
    named = receipt_named_runs(root)
    previous = previous or {}
    prev_runs = {r["run"]: r for r in previous.get("runs", [])}
    retired = {r["run"]: r for r in previous.get("retired", [])}
    candidates = sorted(set(prev_runs) | set(disk) | set(named))
    census = {}
    for name, stream in census_streams(root).items():
        run = stream_run(root, stream, candidates)
        if run is None:
            raise Refused(f"the census stream {name} ({stream}) maps to no recorder run: "
                          "neither a counts.json beside it nor exactly one run whose counts "
                          "record its size and record count")
        census.setdefault(run, set()).add(name)
    runs, missing, rerecorded = [], [], []
    reached: dict[str, int] = {}
    present_names: set[str] = set()
    for run in sorted(set(candidates) | set(census)):
        got = run_counts(root, run)
        if got is None:
            if run in prev_runs:
                missing.append(run)
                continue
            raise Refused(f"{run} was found and has no counts.json")
        sha, names = got
        if run in prev_runs and prev_runs[run].get("counts_sha256") != sha:
            rerecorded.append(run)
        sources = (["disk"] if run in disk else []) \
            + [f"receipt:{r}" for r in sorted(named.get(run, ()))] \
            + [f"census:{n}" for n in sorted(census.get(run, ()))]
        runs.append({"run": run, "counts_sha256": sha, "methods": len(names),
                     "sources": sources or ["registered earlier"]})
        retired.pop(run, None)
        present_names |= names
        for n in names:
            reached[n] = reached.get(n, 0) + 1
    if missing and not retire_missing:
        raise Refused(
            "registered runs are gone: " + ", ".join(missing) + ". A run stays counted once "
            "registered; restore it, or re-run with --retire-missing to keep every name only "
            "it reached")
    kept: dict[str, dict] = {n: dict(v) for n, v in
                             (previous.get("kept_from_earlier_counts") or {}).items()}
    for name in previous.get("kept_from_retired_runs") or []:   # the field's first name
        kept.setdefault(name, {"re_recorded": [], "retired": [],
                               "because": "kept from a retired run before the field was renamed"})
    # Every name the registry counted and no run reaches now stays counted, whatever moved
    # it: a re-recorded run that reaches fewer methods as much as a deleted one.
    dropped = sorted(set(previous.get("reached", {})) - present_names - set(kept))
    if dropped and not (rerecorded or missing):
        raise Refused(
            "the registry counted " + ", ".join(dropped) + " as reached, and no registered run "
            "was re-recorded or retired since: its `reached` was edited, or the rule that "
            "counts a run's names changed. Either changes M7's denominator, which is the "
            "user's to rule on (AGENTS.md rule 10)")
    for name in dropped:
        kept[name] = {"re_recorded": sorted(rerecorded), "retired": sorted(missing),
                      "because": "no registered run reaches it now; one of these runs reached "
                                 "it before it was re-recorded or retired, and a name M7 "
                                 "counted stays counted"}
    for run in missing:
        old = prev_runs[run]
        retired[run] = {"run": run, "counts_sha256": old["counts_sha256"],
                        "methods": old["methods"],
                        "retired_because": "its counts.json was absent at a regeneration "
                                           "with --retire-missing"}
    kept = {n: v for n, v in kept.items() if n not in present_names}
    return {
        "schema": "sa2-recomp-m7-recorded-runs/v1",
        "id": "m7-recorded-runs",
        "generated_by": "tools/s4/m7_inputs.py",
        "command": COMMAND,
        "note": ("Every recorded run M7's denominator counts (the user's ruling of 2026-10-04: "
                 "\"recorded runs plus static call sites\"). Generated; it only grows. A run is "
                 "registered when a disk census at a regeneration finds a recorder counts.json "
                 "beside a guest run record, when a receipt under Documentation/receipts/ names "
                 "it, or when host/d3d9gpu/state_census.sh replays its stream. A name a "
                 "registered run reached stays counted when that run is re-recorded or retired "
                 "(kept_from_earlier_counts names the runs that changed). check_m7_inputs.py "
                 "re-discovers all three sources on every push, fails any guest run on disk "
                 "that reached a name M7 does not count, holds every counts.json to its sha256, "
                 "and fails any run or name the registry and receipt at HEAD count and the "
                 "tree does not. device_table_census counts every guest run record with or "
                 "without the recorder (the user's ruling of 2026-10-09, item 1: \"Count the "
                 "device's per-method table from every game run\"), from the run index the "
                 "census keeps; it too only grows."),
        "rule": {"counts_schema": COUNTS_SCHEMA, "writer": RECORDER,
                 "guest_run": "run.json beside it with the probe's winmain section and the "
                              "device's implemented table",
                 "counted": "methods[] and entries[] with calls > 0; d3d9!Direct3DCreate9 is "
                            "M4's import and is not counted"},
        "runs": runs,
        "reached": dict(sorted(reached.items())),
        "kept_from_earlier_counts": dict(sorted(kept.items())),
        "retired": [retired[k] for k in sorted(retired)],
        "device_table_census": device_census_section(previous, device),
    }


def registry_names(registry: dict) -> set[str]:
    return set(registry.get("reached", {})) | set(registry.get("kept_from_earlier_counts", {}))


# ------------------------------------------------------------------ the census supplement

def supplement_names(supplement: dict) -> set[str]:
    return {f"{r['interface']}::{r['method']}" for r in supplement.get("rows", [])}


# ------------------------------------------------------------------ the three-way numerator
#
# The user's numerator (THREE_WAY_RULING): "a method counts only when the device answers it,
# the recorder captures it, and both players handle it. Methods no recorded run reaches are
# exercised by a synthetic stream per method, so the check can fail." Two receipts carry the
# evidence, as pins (paths and sha256s) and no verdicts; the verdicts are derived here:
#
# * PRODUCTION (written by this tool's --production): one current recording of the game
#   (its recorder counts.json and present-run.json) and both hosts' replays of its stream
#   (each run directory's report.json, check.json, exit and checked-by file, the second
#   host's d3d9gpu.json), with the frame census the second host's draw account is held to,
#   and the two players' build records;
# * SYNTHETIC (written by tools/s5/m7_synthetic.py): per target method, a positive and an
#   omit stream of tools/s5/tests/rec_harness_m7.c, each replayed in both players.
#
# A method is held when the device answers it and the production evidence holds it
# (production_problems), or, for a method no registered recorded run reaches (neither the
# production recording's recorder nor any run of the registry's `reached` counted a call of
# it), when its synthetic case does (case_verdict): its positive stream passes the predicate
# below and its omit stream fails that same predicate where removing the call must make it
# fail. A method a recorded run reaches is held by the production recording alone, so a
# synthetic case never stands in for a real call a host mishandled or never replayed: one
# that another registered run reaches and the production recording does not is not held until
# a production recording that reaches it is replayed in both hosts. The predicate,
# per run of the recorder and per host's replay:
# the player exited 0 with a complete passing report at stage 3, not stats-only, read to the
# END record of the recorded stream; nothing not handled or skipped by stage, no error;
# play_report.py's check passed (the checker the tree holds), its allow-list problems none;
# the method's category sum in the host's report equals the recorder's count of it, with
# nothing not handled or skipped, and no failed host answer outside play_report.py's two
# scene-order rules (BeginScene inside a scene, EndScene with no scene open: the rule's count
# all of the method's failures); for the second host the device's report complete, nothing
# not implemented, and compare_hosts.py's draw account clean against the census; for an
# authored stream every pixel the harness predicts. The recorder: its counts file whole (the
# stream closed, no note), the method counted and entered as often, and an authored stream
# read back by tools/s5/stream_dump.py and equal to those counts. Ignored by design counts
# as handled: a call the player plans as no effect on the picture is handled by planning it
# so (READINGS).
#
# The evidence is current when every file it pins hashes as pinned, and every source the
# recording, the fixture and both players were made from is the tree's (their records' own
# sources, the recorder and stream format the recording names, the runtime digest it was
# measured on, and the checker that wrote each check.json). Otherwise the block reads not
# measured and says why, so a push that moves a pinned source is never refused here; the
# evidence is then made again (the M7 plan's re-replay) and this tool re-run.

PRODUCTION = "Documentation/receipts/s5-m7-production-v1.json"
SYNTHETIC = "Documentation/receipts/s5-m7-synthetic-v1.json"
PRODUCTION_SCHEMA = "sa2-recomp-m7-production/v1"
SYNTHETIC_SCHEMA = "sa2-recomp-m7-synthetic/v1"
PRODUCTION_ID = "s5-m7-production-v1"
SYNTHETIC_WRITER = "tools/s5/m7_synthetic.py"
PLAY_REPORT = "tools/s5/play_report.py"
CHECKED_BY = "check.play_report.sha256"
HOSTS = ("dxvk", "sdlgpu")
HOST_FILES = {"dxvk": ("report.json", "check.json", "exit", CHECKED_BY),
              "sdlgpu": ("report.json", "check.json", "exit", CHECKED_BY, "d3d9gpu.json")}
PLAYER_RECORDS = {"dxvk": "sa2_player.json", "sdlgpu": "sa2_player_gpu.json"}
PLAYER_BINARIES = {"dxvk": "sa2_player", "sdlgpu": "sa2_player_gpu"}
# host/player/names.h type_name(): the report's name of each interface, by its type number.
TYPE_SHORT = ("IDirect3D9", "Device", "Texture", "CubeTexture", "VolumeTexture", "Surface", "Volume",
              "VertexBuffer", "IndexBuffer", "VertexDeclaration", "VertexShader", "PixelShader",
              "StateBlock", "SwapChain", "Query", "ID3DXConstantTable")
# The methods whose omit stream must fail on both hosts' pixels, not only on the recorder's
# count: each changes what the harness's frame shows (tools/s5/tests/rec_harness_m7.c),
# SetStreamSourceFreq as the game calls it (stream 0 INDEXEDDATA|2, stream 1 INSTANCEDATA|1:
# two instances of one indexed quad). The others (releases, queries, the Save calls, the
# constant-table queries, a Reset to the creation parameters) have no effect on the picture,
# so their falsifier is the count (NO_EFFECT_READING).
PICTURE_EFFECT = frozenset({
    "IDirect3DDevice9::CreateIndexBuffer", "IDirect3DIndexBuffer9::Lock", "IDirect3DIndexBuffer9::Unlock",
    "IDirect3DDevice9::DrawIndexedPrimitive", "IDirect3DDevice9::SetStreamSourceFreq",
    "IDirect3DDevice9::SetVertexShaderConstantB", "IDirect3DDevice9::SetVertexShaderConstantI",
    "IDirect3DDevice9::SetPixelShaderConstantB", "IDirect3DDevice9::SetPixelShaderConstantI",
    "ID3DXConstantTable::SetBool", "ID3DXConstantTable::SetInt", "ID3DXConstantTable::SetFloat",
    "ID3DXConstantTable::SetVector", "ID3DXConstantTable::SetMatrix",
})
# A target whose case must also carry runs of other scenarios that both players must refuse by
# name, because its positive stream alone cannot show the player checks anything: a Reset to
# the creation parameters is no host call, so a player that ignored every Reset would pass it.
# {target: {variant: the report's name the refusal must carry}}.
REFUSED_VARIANTS = {"IDirect3DDevice9::Reset": {"other-pp": "Device::Reset"}}
# What the synthetic evidence must pin for the block to read measured: the fixture's sources
# (tools/s5/m7_synthetic.py COMPILE_SOURCES and the files they include), the tools that made
# its generated inputs, and in each player's build record its replay sources.
SYNTHETIC_SOURCES = ("tools/s5/tests/rec_harness_m7.c", "tools/s5/tests/rec_harness.c", "runtime/sa2_d3d9.c",
                     "runtime/sa2_d3d9.h", "runtime/sa2_guest_call.c", "host/sa2_present_rec.c",
                     "host/sa2_stream.h", "Documentation/scripts/tests/test_s5_stream.py")
SYNTHETIC_GENERATORS = ("host/d3d9gpu/test/sm3_asm.py", "tools/s5/objcheck.py", "tools/s5/m7_synthetic.py")
PLAYER_SOURCES = {"dxvk": ("host/player/player.cpp", "host/player/stream.cpp"),
                  "sdlgpu": ("host/player/player.cpp", "host/player/stream.cpp", "host/d3d9gpu/d9g_device.cpp")}
NO_EFFECT_READING = (
    "a method with no effect on the picture is held on the count: its omit stream fails only because the "
    "recorder counted no call of it, so that both players handle it rests on the positive stream, where each "
    "host's category sum (handled plus ignored_by_design) equals the recorder's count with nothing not handled")
READINGS = (
    "ignored_by_design counts as handled: a call a player plans as having no effect on the picture "
    "(host/player/player.cpp call_plan, P_NO_EFFECT and P_CHECK) is handled by that plan; a call it does "
    "not plan is not_handled and holds nothing",
    "the scene-order allowance: a host answer that failed where the guest's succeeded is allowed only under "
    "tools/s5/play_report.py's two rules (BeginScene inside a scene, EndScene with no scene open), and only "
    "when the rule's count is all of that method's failures",
    "the recorder captures a call when the stream holds its record, read back whole with its arguments; a "
    "pointer payload no player reads has no recorder rule (ID3DXConstantTable::GetConstantByName's name, the "
    "D3DXSave* paths), so for those the record is the call and its answer",
    "a method a recorded run reaches is held by the production recording alone; a synthetic case holds only a "
    "method no recorded run reaches: neither the production recording nor any run of "
    "Documentation/receipts/m7-recorded-runs.json `reached` (the runs that reach it now; a name kept from "
    "earlier counts is one no registered run reaches now) counted a call of it, so a method another registered "
    "run reaches and the production recording does not is not held until a production recording that reaches it "
    "is replayed in both hosts. The synthetic streams are made through the real recording device and recorder "
    "on tools/s5/tests/rec_harness.c's stand-in runtime (its guest memory, function table and CPU state), not "
    "the lifted game",
    NO_EFFECT_READING,
)

_CACHE: dict[tuple, object] = {}


def cached_sha256(path: Path) -> str | None:
    """sha256 of a file, or None when it is absent. Hashed every time: the evidence files
    are small (the 12 GB recorded stream is never hashed here), and a cache keyed by a stat
    can miss a rewrite inside one timestamp tick."""
    try:
        return sha256_file(path) if path.is_file() else None
    except OSError:
        return None


def cached_json(path: Path):
    """A JSON file's content, parsed once per content (keyed by its sha256), or None."""
    sha = cached_sha256(path)
    if sha is None:
        return None
    key = ("json", sha)
    if key not in _CACHE:
        try:
            _CACHE[key] = json.loads(path.read_bytes())
        except (OSError, ValueError, UnicodeDecodeError):
            _CACHE[key] = None
    return copy_json(_CACHE[key])


def copy_json(doc):
    return json.loads(json.dumps(doc)) if doc is not None else None


def cached_scan(stream: Path, counts: Path) -> list[str]:
    """tools/s5/stream_dump.py's problems with an authored stream, and its differences from
    the recorder's own counts (compare_counts), once per content of the two."""
    key = ("scan", cached_sha256(stream), cached_sha256(counts))
    if None in key:
        return [f"{stream.name} or {counts.name} is absent, so the stream cannot be read back"]
    if key not in _CACHE:
        sys.path.insert(0, str(ROOT / "tools" / "s5"))
        import stream_dump  # noqa: E402
        try:
            summary = stream_dump.scan(stream)
            doc = json.loads(counts.read_text())
            _CACHE[key] = [str(p) for p in summary.get("problems") or []] + \
                stream_dump.compare_counts(summary, doc)
        except (stream_dump.StreamError, OSError, ValueError) as e:
            _CACHE[key] = [f"the stream does not read back: {e}"]
    return list(_CACHE[key])


def _get(d, dotted: str):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def recorder_rows(counts: dict | None) -> dict[str, dict]:
    """{M7 name: the recorder's row} for every method and D3DX entry a counts file names,
    with the report's key for the row: (short type, slot) or ("d3dx9_42", entry)."""
    out: dict[str, dict] = {}
    for m in (counts or {}).get("methods") or []:
        t = m.get("type")
        short = TYPE_SHORT[t] if isinstance(t, int) and 0 <= t < len(TYPE_SHORT) else None
        out[name_of(m.get("interface"), m.get("method"))] = {
            "calls": m.get("calls"), "entered": m.get("entered"), "key": (short, m.get("slot"), m.get("method"))}
    for e in (counts or {}).get("entries") or []:
        if e.get("name") == NOT_A_METHOD:
            continue
        out[f"{D3DX}!{e.get('name')}"] = {"calls": e.get("calls"), "entered": e.get("entered"),
                                          "key": (D3DX, e.get("entry"), e.get("name"))}
    return out


def report_row(report: dict, key: tuple) -> dict | None:
    """The player's row for a recorder row's key: by D3DX entry number, or by interface and
    slot. Not by the method's name: the player names every constant-table slot past Release
    "ConstantTableMethod" (host/player/names.h) and plans it by its slot."""
    kind, n, _ = key
    for m in report.get("methods") or []:
        if kind == D3DX:
            if m.get("type") == D3DX and m.get("entry") == n:
                return m
        elif m.get("type") == kind and m.get("slot") == n and "entry" not in m:
            return m
    return None


def _play_report():
    sys.path.insert(0, str(ROOT / "tools" / "s5"))
    import play_report  # noqa: E402
    return play_report


def _draw_account(census: dict, device: dict) -> list[str]:
    sys.path.insert(0, str(ROOT / "tools" / "s5"))
    import compare_hosts  # noqa: E402
    failures, _ = compare_hosts.r2_draw_account({"census": census, "sdl": {"device": device}, "fault": None})
    return failures


def recorder_problems(run: dict) -> list[tuple[str, str]]:
    """(clause, sentence) for a recorder run that is not whole: its counts file and, for an
    authored stream, the stream read back against it."""
    f: list[tuple[str, str]] = []
    c = run.get("counts")
    if not is_recorder_counts(c):
        return [("recorder", f"{run.get('dir')}: no recorder counts file ({COUNTS_SCHEMA})")]
    for key, want in (("counting", True), ("stream", "closed"), ("stream_records.note", 0)):
        if _get(c, key) != want:
            f.append(("recorder", f"the recorder's {key} is {_get(c, key)!r}, not {want!r}"))
    noted = {k: v for k, v in (c.get("notes") or {}).items() if v}
    if noted:
        f.append(("recorder", f"the recorder noted {noted}"))
    if "harness_exit" in run and run["harness_exit"] != 0:
        f.append(("recorder", f"the fixture that recorded it exited {run['harness_exit']}"))
    for p in run.get("scan") or []:
        f.append(("stream", f"the stream read back: {p}"))
    return f


def host_problems(host: str, h: dict, *, stream_sha256: str, census: dict | None, checker: str | None,
                  pixels: bool) -> list[tuple[str, str]]:
    """(clause, sentence) for one host's replay that is not clean, the clause suffixed /host."""
    f: list[tuple[str, str]] = []

    def bad(clause: str, text: str) -> None:
        f.append((f"{clause}/{host}", f"{host}: {text}"))
    rep = h.get("report")
    if not isinstance(rep, dict):
        bad("run", "no report")
        return f
    if h.get("exit") != 0:
        bad("run", f"the player exited {h.get('exit')}")
    for key, want in (("complete", True), ("pass", True), ("exit_status", 0), ("stage", 3),
                      ("options.stats_only", False), ("stream_records_read_to_end", True), ("records.end", 1),
                      ("stop", "end of stream"), ("errors", 0), ("calls.not_handled", 0),
                      ("calls.skipped_by_stage", 0), ("stream_sha256", stream_sha256),
                      ("host_identity.d3d9_host", host)):
        got = _get(rep, key)
        if got != want or type(got) is not type(want):
            bad("run", f"{key} is {got!r}, not {want!r}")
    for p in _play_report().allow_list_problems(rep):
        bad("run", p)
    chk = h.get("check")
    if not isinstance(chk, dict) or chk.get("pass") is not True:
        bad("check", f"tools/s5/play_report.py check: {(chk or {}).get('failures', ['no verdict'])[:3]}")
    else:
        for key in ("stream_sha256", "stage", "presents"):
            if chk.get(key) != rep.get(key):
                bad("check", f"the check's {key} {chk.get(key)!r} is not the report's {rep.get(key)!r}")
    if checker is None or h.get("checked_by") != checker:
        bad("check", f"its check.json was written by play_report.py {h.get('checked_by')}, not {checker}")
    if pixels:
        exp = rep.get("expectations") or {}
        if not exp.get("file") or not exp.get("selected") or exp.get("fail") != 0 \
                or exp.get("pass") != exp.get("selected"):
            bad("pixels", f"the harness's pixels: {exp}")
    if host == "sdlgpu":
        dev = h.get("device")
        if not isinstance(dev, dict) or dev.get("complete") is not True or dev.get("failure") is not None \
                or _get(dev, "not_implemented.count") != 0:
            bad("device", f"the device's report: complete {_get(dev, 'complete')}, failure "
                          f"{_get(dev, 'failure')}, not implemented {_get(dev, 'not_implemented.count')}")
        elif not isinstance(census, dict):
            bad("draws", "no frame census to hold the draw account to")
        else:
            for key, want in (("stream_sha256", stream_sha256), ("stage", 3), ("frames", rep.get("presents"))):
                if census.get(key) != want:
                    bad("draws", f"the census's {key} is {census.get(key)!r}, not {want!r}")
            for p in _draw_account(census, dev):
                bad("draws", f"draw account: {p}")
    return f


def method_problems(name: str, rows: dict[str, dict], hosts: dict[str, dict]) -> list[tuple[str, str]]:
    """(clause, sentence) for `name` in one recorder run and its replays: the recorder counted
    and entered it, and each host's category sum is that count, with nothing not handled,
    skipped, or failed outside the scene-order allowance. A run that holds no call of `name`
    fails one clause of its own, `count`, so an omit stream's falsifier can require exactly
    that and nothing else the recorder says."""
    row = rows.get(name)
    calls = (row or {}).get("calls")
    if not row or not isinstance(calls, int) or calls <= 0:
        return [("count", f"the recorder counted no call of {name}")]
    f: list[tuple[str, str]] = []
    if row.get("entered") != calls:
        f.append(("recorder", f"the recorder counted {calls} calls of {name} and {row.get('entered')} entered"))
    allowed = {method: rule for rule, method in _play_report().ALLOWED_RULES.items()}
    for host in HOSTS:
        rep = (hosts.get(host) or {}).get("report") or {}
        r = report_row(rep, row["key"])
        if r is None:
            f.append((f"method/{host}", f"{host}: the report has no row for {name}"))
            continue
        total = sum(int(r.get(k) or 0) for k in ("handled", "ignored_by_design", "skipped_by_stage", "not_handled"))
        if total != calls:
            f.append((f"method/{host}", f"{host}: {name} counts {total} calls by category, the recorder {calls}"))
        for k in ("not_handled", "skipped_by_stage"):
            if r.get(k):
                f.append((f"method/{host}", f"{host}: {name} {k} {r.get(k)}"))
        failed, differs = int(r.get("failed") or 0), int(r.get("hr_differs_from_guest") or 0)
        if failed or differs:
            rule = allowed.get((row["key"][0], row["key"][2]))
            n = _get(rep, f"allowed_host_failures.rules")
            n = n.get(rule) if isinstance(n, dict) and rule else None
            if rule is None or n != failed or differs > failed:
                f.append((f"method/{host}", f"{host}: {name} failed {failed} and differed from the guest "
                                            f"{differs} times, outside the scene-order allowance"))
    return f


def run_problems(run: dict, *, pixels: bool) -> list[tuple[str, str]]:
    """Every whole-run problem of one recorder run and both hosts' replays of its stream."""
    f = recorder_problems(run)
    for host in HOSTS:
        f += host_problems(host, (run.get("hosts") or {}).get(host) or {}, stream_sha256=run.get("stream_sha256"),
                           census=run.get("census"), checker=run.get("checker"), pixels=pixels)
    return f


def case_problems(run: dict, name: str, *, pixels: bool = True) -> list[tuple[str, str]]:
    """The shared per-method predicate on one run: empty when the run holds `name`."""
    return run_problems(run, pixels=pixels) + method_problems(name, recorder_rows(run.get("counts")),
                                                              run.get("hosts") or {})


CLAUSE_ORDER = ("count", "recorder", "stream", "method", "device", "draws", "pixels", "check", "run")


def refused_variant_problems(run: dict, target: str, name: str) -> list[str]:
    """Why a variant run does not show both players refusing `target` by name: the run itself
    whole (recorder, stream, pixels, the second host's device and draws), the recorder counting
    the call, and in each host's report every call of it not handled, nothing else not handled
    or failed, and the not-handled values naming it (`name`, the report's own spelling)."""
    out = [t for c, t in run_problems(run, pixels=True)
           if c.split("/")[0] not in ("run", "check")]
    rows = recorder_rows(run.get("counts"))
    row = rows.get(target)
    calls = (row or {}).get("calls")
    if not row or not isinstance(calls, int) or calls <= 0:
        return out + [f"the recorder counted no call of {target}"]
    for host in HOSTS:
        h = (run.get("hosts") or {}).get(host) or {}
        rep = h.get("report") or {}
        r = report_row(rep, row["key"]) or {}
        if r.get("not_handled") != calls or _get(rep, "calls.not_handled") != calls:
            out.append(f"{host}: {target} not handled {r.get('not_handled')!r} of {calls} calls, "
                       f"{_get(rep, 'calls.not_handled')!r} not handled in all")
        if h.get("exit") == 0 or rep.get("pass") is not False:
            out.append(f"{host}: the player exited {h.get('exit')!r} and passed {rep.get('pass')!r}")
        if int(_get(rep, "host_failures.count") or 0) or int(rep.get("errors") or 0):
            out.append(f"{host}: {_get(rep, 'host_failures.count')} host failures and {rep.get('errors')} errors")
        first = _get(rep, "not_handled_values.first") or []
        if not any(f"{name}:" in str(v) for v in first):
            out.append(f"{host}: no not-handled value names {name} ({str(first)[:120]})")
    return out


def case_verdict(case: dict, target: str) -> dict:
    """A synthetic case's verdict: held when the positive run passes the predicate and the
    omit run fails it on the recorder's count and, for a method with an effect on the
    picture, on both hosts' pixels, and nowhere that removing the call should not touch; and,
    for a target REFUSED_VARIANTS names, when each such variant's run is refused by both
    players by name."""
    if case.get("problems"):
        return {"held": False, "why": "; ".join(case["problems"][:3]), "positive": [], "omit": []}
    pos = case_problems(case["positive"], target)
    omit = case_problems(case["omit"], target)
    effect = target in PICTURE_EFFECT
    required = {"count"} | ({f"pixels/{h}" for h in HOSTS} if effect else set())
    consequence = required | ({f"{c}/{h}" for c in ("run", "check") for h in HOSTS} if effect else set())
    failed = {c for c, _ in omit}
    why = ""
    if pos:
        # The most specific clauses first, so the reason names the cause (an unmapped draw, a
        # count) before the exit status it led to.
        lead = sorted(pos, key=lambda ct: CLAUSE_ORDER.index(ct[0].split("/")[0])
                      if ct[0].split("/")[0] in CLAUSE_ORDER else len(CLAUSE_ORDER))
        why = "the positive stream: " + "; ".join(t for _, t in lead[:3])
    elif not required <= failed:
        why = (f"the omit stream does not fail where it must: {sorted(required - failed)} pass without "
               f"{target}")
    elif not failed <= consequence:
        why = (f"the omit stream fails where leaving {target} out should not reach: "
               + "; ".join(t for c, t in omit if c not in consequence)[:300])
    else:
        for variant, name in sorted(REFUSED_VARIANTS.get(target, {}).items()):
            run = (case.get("variants") or {}).get(variant)
            problems = refused_variant_problems(run, target, name) if run is not None \
                else [f"the case has no {variant} run"]
            if problems:
                why = f"the {variant} stream is not refused by both players: " + "; ".join(problems[:3])
                break
    return {"held": not why, "why": why, "positive": [t for _, t in pos], "omit": sorted(failed)}


def in_tree(rel_path, problems: list[str], what: str) -> str:
    """`rel_path` as given, with a problem when it is not a path in the tree (absolute, or
    climbing out with ..). The evidence pins its files by their path in the tree, so the gate
    can put a changed copy of any of them in an overlay of the tree to show it can fail; a file
    pinned elsewhere could be read and never be changed so."""
    parts = Path(str(rel_path)).parts
    if Path(str(rel_path)).is_absolute() or ".." in parts:
        problems.append(f"{what} {rel_path} is not a path in the tree (the evidence pins files by their path in it)")
    return str(rel_path)


def host_pins(entry: dict, host: str) -> tuple[str, dict]:
    """(the directory, {file: sha256}) a receipt entry pins for one host's replay: its own
    `dir`, else the entry's directory/<host>; its `files`, else the host entry itself."""
    h = (entry.get("hosts") or {}).get(host) or {}
    hfiles = h.get("files") if "files" in h else h
    return str(h.get("dir") or (Path(str(entry.get("dir"))) / host).as_posix()), \
        (hfiles if isinstance(hfiles, dict) else {})


def _run_from_dir(root: Path, entry: dict, problems: list[str], *, stream: Path | None,
                  counts_name: str) -> dict:
    """One recorder run and both hosts' replays, read from the files `entry` pins; each pinned
    file that is absent or hashes otherwise is a problem."""
    d = root / in_tree(entry.get("dir"), problems, "the run directory")
    files = entry.get("files") or {}
    for name, want in sorted(files.items()):
        if cached_sha256(d / name) != want:
            problems.append(f"{entry.get('dir')}/{name} is absent or not the file the receipt pins")
    run = {"dir": entry.get("dir"), "counts": cached_json(d / counts_name), "hosts": {}}
    for host in HOSTS:
        hdir, hfiles = host_pins(entry, host)
        hd = root / in_tree(hdir, problems, f"the {host} directory")
        for name in HOST_FILES[host]:
            if name not in (hfiles or {}):
                problems.append(f"the receipt pins no {host} {name} for {entry.get('dir')}")
            elif cached_sha256(hd / name) != hfiles[name]:
                problems.append(f"{hd.relative_to(root) if hd.is_relative_to(root) else hd}/{name} is absent or "
                                f"not the file the receipt pins")
        exit_text = (hd / "exit").read_text().strip() if (hd / "exit").is_file() else ""
        checked = (hd / CHECKED_BY).read_text().strip() if (hd / CHECKED_BY).is_file() else None
        run["hosts"][host] = {"report": cached_json(hd / "report.json"), "check": cached_json(hd / "check.json"),
                              "exit": int(exit_text) if exit_text.lstrip("-").isdigit() else None,
                              "checked_by": checked,
                              "device": cached_json(hd / "d3d9gpu.json") if host == "sdlgpu" else None}
    if stream is not None:
        run["scan"] = cached_scan(stream, d / counts_name)
    return run


def counts_json_dirs(root: Path, dirs: list[str]) -> list[str]:
    """Each directory a synthetic receipt cites that holds a file named counts.json, which
    run_dir_named_by would register as a recorded run of the game."""
    out = set()
    for rel_dir in dirs:
        d = root / rel_dir
        for p in [d, *[q for q in d.rglob("*") if q.is_dir()]] if d.is_dir() else []:
            if (p / "counts.json").exists():
                out.add(p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p))
    return sorted(out)


def load_case(root: Path, entry: dict, checker: str | None = None) -> dict:
    """One synthetic case of a receipt ({target, family, positive, omit}) read from disk.
    `checker` is the play_report.py every check.json must have been written by (load_synthetic
    sets it to the one the receipt's runs agree on, which the tree must hold)."""
    problems: list[str] = []
    case: dict = {"target": entry.get("target"), "problems": problems, "variants": {}}

    def one(m: dict, mode: str, variant: str) -> dict:
        d = root / str(m.get("dir"))
        run = _run_from_dir(root, m, problems, stream=d / "stream.sa2s", counts_name="rec.counts.json")
        for name in ("stream.sa2s", "rec.counts.json", "answers.json", "expect.tsv", "census.json", "harness.exit"):
            if name not in (m.get("files") or {}):
                problems.append(f"the receipt pins no {name} for {m.get('dir')}")
        run["stream_sha256"] = (m.get("files") or {}).get("stream.sa2s")
        run["census"] = cached_json(d / "census.json")
        run["checker"] = checker
        exit_text = (d / "harness.exit").read_text().strip() if (d / "harness.exit").is_file() else ""
        run["harness_exit"] = int(exit_text) if exit_text.lstrip("-").isdigit() else None
        answers = cached_json(d / "answers.json") or {}
        if answers.get("target") != entry.get("target") or answers.get("mode") != mode \
                or (answers.get("variant") or "") != variant:
            problems.append(f"{m.get('dir')}/answers.json is the harness's {answers.get('target')!r} "
                            f"{answers.get('mode')!r} {answers.get('variant')!r}, not {entry.get('target')!r} "
                            f"{mode} {variant!r}")
        return run

    for mode in ("positive", "omit"):
        case[mode] = one(entry.get(mode) or {}, mode, "")
    variants = entry.get("variants") or {}
    for variant, m in sorted(variants.items()):
        case["variants"][variant] = one(m or {}, "positive", variant)
    dirs = [str((entry.get(m) or {}).get("dir")) for m in ("positive", "omit")] \
        + [str((m or {}).get("dir")) for m in variants.values()]
    for d in counts_json_dirs(root, dirs):
        problems.append(f"{d} holds a counts.json: a synthetic case would be a recorded run")
    return case


def _player_pins(root: Path, records: dict, problems: list[str], where: str) -> tuple[dict, dict]:
    """({path: sha256} of every source both players' build records name, {host: binary sha256}),
    each record read from the file the receipt pins. A record that does not name the player's
    own replay sources (PLAYER_SOURCES) is a problem: its pins could never go stale."""
    tree: dict[str, str] = {}
    binaries: dict[str, str] = {}
    for host, name in PLAYER_RECORDS.items():
        ref = (records or {}).get(name) or {}
        path = root / in_tree(ref.get("path"), problems, f"{where}: the build record")
        if cached_sha256(path) != ref.get("sha256"):
            problems.append(f"{where}: the build record {ref.get('path')} is absent or not the one it pins")
            continue
        doc = cached_json(path) or {}
        sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
        missing = [s for s in PLAYER_SOURCES[host] if not isinstance(sources.get(s), str)]
        if missing:
            problems.append(f"{where}: the build record {ref.get('path')} pins no {', '.join(missing)}")
        for p, s in sources.items():
            tree[p] = s
        binaries[host] = (doc.get("binaries") or {}).get(PLAYER_BINARIES[host])
    return tree, binaries


def _players_ran(run: dict, binaries: dict, problems: list[str], where: str) -> None:
    for host in HOSTS:
        rep = (run["hosts"].get(host) or {}).get("report") or {}
        if _get(rep, "files.player.sha256") != binaries.get(host) or binaries.get(host) is None:
            problems.append(f"{where}: the {host} player that ran ({_get(rep, 'files.player.sha256')}) is not "
                            f"the build record's {PLAYER_BINARIES[host]}")
        if host == "sdlgpu":
            dev = (run["hosts"].get(host) or {}).get("device") or {}
            if _get(dev, "library.sha256") != binaries.get(host):
                problems.append(f"{where}: the second host's device ran from {_get(dev, 'library.sha256')}, not "
                                f"the build record's sa2_player_gpu")


def recording_pins(made: dict) -> dict[str, str | None]:
    """{path: sha256} of the sources a recording was made from that the evidence holds to the
    tree, from its present-run.json `current` (`made`): the recorder and the stream format.
    The runtime it ran on is held beside them by its digest (`runtime_sha256`)."""
    return {RECORDER: made.get("recorder_sha256"), "host/sa2_stream.h": made.get("stream_h_sha256")}


def recording_moved(root: Path, made: dict, runtime_digest: str | None) -> list[tuple[str, str | None, str | None]]:
    """(what, the recording's sha256, the tree's) for each source a recording was made from that
    the tree no longer holds: the stale_pins rule for the recording's own pins, so a recording
    this names is one the production evidence would read as stale."""
    out = [(p, sha, cached_sha256(root / p)) for p, sha in recording_pins(made).items()
           if sha is None or cached_sha256(root / p) != sha]
    want = made.get("runtime_sha256")
    if want is None or want != runtime_digest:
        out.append(("the runtime digest", want, runtime_digest))
    return out


def production_blocks(doc: dict) -> list[dict] | None:
    """The production runs a receipt names, each block {recording, census, hosts, players}: its
    `runs` list (the contract of 2026-10-09 with tools/s5/m7_synthetic.py: several production
    runs, each block shaped as the single-run receipt production_receipt() writes), or the one
    run the receipt's top level is, in the form before the list. None when `runs` is there and is
    not a nonempty list of objects."""
    if "runs" in doc:
        runs = doc.get("runs")
        if not isinstance(runs, list) or not runs or not all(isinstance(r, dict) for r in runs):
            return None
        return [{**r, "players": r.get("players") or doc.get("players")} for r in runs]
    return [{k: doc.get(k) for k in ("recording", "census", "hosts", "players")}]


def _merge_pins(tree: dict, more: dict) -> None:
    """Pins of several runs as one: a source two runs pin at different sha256s is pinned at None,
    which no tree holds, so the evidence reads stale (stale_pins) rather than keep either."""
    for path, sha in more.items():
        tree[path] = sha if tree.get(path, sha) == sha else None


def load_production(root: Path, rel_path: str = PRODUCTION) -> dict:
    """The production evidence, read from the files its receipt pins: {problems, receipt, runs,
    run (the first), pins}. A problem means the receipt does not describe the files on disk; a
    stale pin means they were made from sources the tree no longer holds. The receipt may name
    several production runs (production_blocks); each is read and pinned as the single run was."""
    out: dict = {"path": rel_path, "problems": [], "pins": {}, "run": None, "runs": []}
    path = root / rel_path
    doc = cached_json(path)
    if doc is None:
        out["problems"].append(f"{rel_path} is absent (written by {COMMAND} --production)")
        return out
    out["sha256"] = cached_sha256(path)
    if doc.get("schema") != PRODUCTION_SCHEMA:
        out["problems"].append(f"{rel_path}: schema {doc.get('schema')!r}, not {PRODUCTION_SCHEMA!r}")
        return out
    blocks = production_blocks(doc)
    if blocks is None:
        out["problems"].append(f"{rel_path}: `runs` is not a nonempty list of production runs")
        return out
    p = out["problems"]
    tree: dict = {}
    digests, runs, dirs = [], [], set()
    for k, block in enumerate(blocks):
        where = rel_path if len(blocks) == 1 else f"{rel_path} run {k + 1} ({block.get('name') or '?'})"
        q: list[str] = []
        rec = block.get("recording") or {}
        if rec.get("dir") in dirs:
            q.append(f"{rec.get('dir')} is named by two production runs")
        dirs.add(rec.get("dir"))
        run = _run_from_dir(root, {"dir": rec.get("dir"), "files": rec.get("files"), "hosts": block.get("hosts")}, q,
                            stream=None, counts_name="counts.json")
        run["stream_sha256"] = rec.get("stream_sha256")
        run["name"] = block.get("name")
        cref = block.get("census") or {}
        in_tree(cref.get("path"), q, "the census")
        if cached_sha256(root / str(cref.get("path"))) != cref.get("sha256"):
            q.append(f"the census {cref.get('path')} is absent or not the one the receipt pins")
        run["census"] = cached_json(root / str(cref.get("path")))
        for name in ("counts.json", "present-run.json"):
            if name not in (rec.get("files") or {}):
                q.append(f"the receipt pins no recording {name}")
        present = cached_json(root / str(rec.get("dir")) / "present-run.json") or {}
        run["present"] = present
        made = present.get("current") or {}
        pins, binaries = _player_pins(root, (block.get("players") or {}).get("records"), q, where)
        _players_ran(run, binaries, q, where)
        pins.update(recording_pins(made))
        checkers = {h["checked_by"] for h in run["hosts"].values()}
        pins[PLAY_REPORT] = checkers.pop() if len(checkers) == 1 else None
        run["checker"] = pins[PLAY_REPORT]
        _merge_pins(tree, pins)
        digests.append(made.get("runtime_sha256"))
        p.extend(q if len(blocks) == 1 else [f"{where}: {t}" for t in q])
        runs.append(run)
    out.update(receipt=doc, run=runs[0], runs=runs,
               pins={"tree": tree, "runtime_digest": digests[0], "runtime_digests": digests})
    return out


def load_synthetic(root: Path, rel_path: str = SYNTHETIC) -> dict:
    """The synthetic evidence, read from the files its receipt pins: {problems, cases, pins}."""
    out: dict = {"path": rel_path, "problems": [], "pins": {}, "cases": {}}
    path = root / rel_path
    doc = cached_json(path)
    if doc is None:
        out["problems"].append(f"{rel_path} is absent (written by {SYNTHETIC_WRITER})")
        return out
    out["sha256"] = cached_sha256(path)
    if doc.get("schema") != SYNTHETIC_SCHEMA or doc.get("generated_by") != SYNTHETIC_WRITER:
        out["problems"].append(f"{rel_path}: schema {doc.get('schema')!r} by {doc.get('generated_by')!r}, "
                               f"not {SYNTHETIC_SCHEMA!r} by {SYNTHETIC_WRITER}")
        return out
    p = out["problems"]
    harness = doc.get("harness") or {}
    binary = harness.get("binary") or {}
    in_tree(binary.get("path"), p, "the fixture")
    if cached_sha256(root / str(binary.get("path"))) != binary.get("sha256"):
        p.append(f"the fixture {binary.get('path')} is absent or not the one the receipt pins")
    tree, binaries = _player_pins(root, (doc.get("players") or {}).get("records"), p, rel_path)
    sources = harness.get("sources") if isinstance(harness.get("sources"), dict) else {}
    tools = harness.get("tools") if isinstance(harness.get("tools"), dict) else {}
    # Pins only go stale if they are there: the fixture's own sources and its generators,
    # each by a sha256, or the block could read measured over a fixture nothing holds.
    missing = [s for s in SYNTHETIC_SOURCES if not isinstance(sources.get(s), str)]
    if missing:
        p.append(f"{rel_path}: the fixture's record pins no {', '.join(missing)}")
    if sorted(tools) != sorted(SYNTHETIC_GENERATORS) or not all(isinstance(v, str) for v in tools.values()):
        p.append(f"{rel_path}: the fixture's record pins the tools {sorted(tools)}, not {list(SYNTHETIC_GENERATORS)}")
    for t, s in {**sources, **tools}.items():
        tree[t] = s
    checkers: set = set()
    seen: set[str] = set()
    for entry in doc.get("cases") or []:
        t = entry.get("target")
        if t in seen:
            p.append(f"{rel_path} holds two cases of {t}")
        seen.add(t)
        case = load_case(root, entry)
        runs = [(mode, case[mode]) for mode in ("positive", "omit")] + sorted(case["variants"].items())
        for mode, run in runs:
            _players_ran(run, binaries, case["problems"], f"{t} {mode}")
            checkers |= {h["checked_by"] for h in run["hosts"].values()}
        out["cases"][t] = case
    if checkers:
        # Every case's checks by one play_report.py, which the tree must hold.
        tree[PLAY_REPORT] = checkers.pop() if len(checkers) == 1 else None
    for case in out["cases"].values():
        for run in [case["positive"], case["omit"], *case["variants"].values()]:
            run["checker"] = tree.get(PLAY_REPORT)
    out.update(receipt=doc, pins={"tree": tree})
    return out


def load_three_way(root: Path, production: str = PRODUCTION, synthetic: str = SYNTHETIC) -> dict:
    """What three_way_numerator() reads, from disk: both receipts' evidence, and the tree's
    sha256 of every source the evidence pins, with the tree's runtime digest."""
    prod = load_production(root, production)
    syn = load_synthetic(root, synthetic)
    paths = set(prod["pins"].get("tree") or {}) | set(syn["pins"].get("tree") or {})
    return {"production": prod, "synthetic": syn,
            "tree": {p: cached_sha256(root / p) for p in sorted(paths) if isinstance(p, str)},
            "runtime_digest": tree_runtime_digest(root)}


def tree_runtime_digest(root: Path) -> str | None:
    """The tree's runtime digest (tools/s4/reach_receipt.py), which a recording names as the
    runtime it was measured on."""
    import reach_receipt  # noqa: E402
    try:
        return reach_receipt.runtime_digest(root)
    except (OSError, ValueError, Refused):
        return None


def stale_pins(ev: dict) -> list[str]:
    """Each source an evidence receipt's files were made from that the tree no longer holds."""
    out = []
    for which in ("production", "synthetic"):
        for path, sha in sorted((ev[which]["pins"].get("tree") or {}).items()):
            if sha is None or ev["tree"].get(path) != sha:
                out.append(f"{which}: {path}")
        pins = ev[which]["pins"]
        wants = pins.get("runtime_digests") or [pins.get("runtime_digest")]
        # A digest neither side can name is no match: a recording that names no runtime, or a
        # tree whose digest cannot be computed, holds nothing current. Every production run's
        # recording must have been made on the tree's runtime.
        if which == "production" and pins and any(w is None or w != ev.get("runtime_digest") for w in wants):
            out.append(f"{which}: the runtime digest")
    return out


def present_run_problems(run: dict) -> list[tuple[str, str]]:
    """The recording's own record (tools/s5/present_run.py's present-run.json): the run was
    whole, its stream read back whole by tools/s5/stream_dump.py and equal to the recorder's
    counts when it was made (the 12 GB stream is not read again here), and it is the stream
    both hosts replayed."""
    pr = run.get("present") or {}
    f: list[tuple[str, str]] = []
    for key, want in (("ok", True), ("checks.counts.ok", True), ("checks.stream.ok", True),
                      ("checks.stream.problems", []), ("checks.stream.counts_mismatch", []),
                      ("checks.stream.end", True), ("ready.stream_sha256", run.get("stream_sha256"))):
        if _get(pr, key) != want:
            f.append(("recorder", f"the recording's present-run.json {key} is {_get(pr, key)!r}, not {want!r}"))
    return f


def production_problems(ev: dict) -> dict[str, list[tuple[str, str]]]:
    """{M7 name: its problems} for every method a production run's recorder counted: empty, so
    held, when any listed run whose recorder counts it holds it (the contract of 2026-10-09: "a
    method is held by production when any listed run's recorder counts it and both hosts' rows
    for that run pass the existing per-host predicate"); otherwise the first such run's problems,
    named by its run when the receipt lists several."""
    runs = ev.get("runs") or ([ev["run"]] if ev.get("run") is not None else [])
    out: dict[str, list[tuple[str, str]]] = {}
    for k, run in enumerate(runs):
        whole = present_run_problems(run) + run_problems(run, pixels=False)
        rows = recorder_rows(run.get("counts"))
        tag = "" if len(runs) == 1 else f"production run {k + 1} ({run.get('name') or run.get('dir')}): "
        for name, row in rows.items():
            if not (isinstance(row.get("calls"), int) and row["calls"] > 0):
                continue
            mine = [(c, tag + t) for c, t in whole + method_problems(name, rows, run["hosts"])]
            if name not in out or (out[name] and not mine):
                out[name] = mine
    return out


def runs_reaching(registry: dict | None, name: str) -> int:
    """How many registered recorded runs reach `name` now (the registry's `reached`), which
    makes it a method no synthetic case may hold (the ruling's "Methods no recorded run
    reaches are exercised by a synthetic stream"). A name kept from earlier counts is one no
    registered run reaches now, so it is not counted here."""
    n = ((registry or {}).get("reached") or {}).get(name)
    return n if isinstance(n, int) and n > 0 else 0


def three_way_numerator(inp: "Inputs", called: set[str] | None = None,
                        implemented: set[str] | None = None) -> dict:
    """The numerator the user ruled on 2026-10-04 (THREE_WAY_RULING), derived from the
    evidence load_three_way() read (inp.three_way). Measured only when both receipts are
    present, describe the files on disk, and were made from the tree's sources; then
    `held_methods` and `not_held` split `called`, and `why_not_held` says why for each method
    not held."""
    base = {"ruling": THREE_WAY_RULING, "ruled": THREE_WAY_RULED}
    ev = inp.three_way
    if ev is None:
        return {**base, "measured": False, "not_held": None,
                "why_not_measured": "the evidence was not read (load_three_way)"}
    reasons = [f"{which}: {p}" for which in ("production", "synthetic") for p in ev[which]["problems"]]
    stale = stale_pins(ev)
    if stale:
        reasons.append("the evidence was made from sources the tree no longer holds, so it describes other "
                       f"builds; make it again ({REFRESH}) and regenerate (" + ", ".join(stale) + ")")
    if reasons:
        return {**base, "measured": False, "not_held": None, "why_not_measured": "; ".join(reasons)}
    if called is None or implemented is None:
        called, implemented = called_and_implemented(inp)
    prod = production_problems(ev["production"])
    cases = {t: case_verdict(c, t) for t, c in sorted(ev["synthetic"]["cases"].items())}
    held, why, by = [], {}, {"production": [], "synthetic": []}
    for name in sorted(called):
        p, s = prod.get(name), cases.get(name)
        if name not in implemented:
            why[name] = "the recording device does not answer it"
        elif p == []:
            held.append(name)
            by["production"].append(name)
        elif p is not None:
            # The recording reaches it (its recorder counted a call): the ruling's synthetic
            # stream is for a method no recorded run reaches, so a passing case does not
            # stand in for a real call a host mishandled.
            why[name] = "the production evidence: " + "; ".join(t for _, t in p[:2]) \
                + ("; its synthetic case passes, and a method the recording reaches is held by the "
                   "recording alone" if s is not None and s["held"] else "")
        elif (n := runs_reaching(inp.registry, name)):
            # Another recorded run reaches it: the game calls it, and no replay of that call
            # by both hosts is in the evidence, so an authored stream does not stand in for it.
            why[name] = (f"{n} registered recorded run{'s' if n != 1 else ''} ({REGISTRY} `reached`) reach"
                         f"{'' if n != 1 else 'es'} it and the production recording does not; a synthetic case "
                         f"holds only a method no recorded run reaches"
                         + ("; its synthetic case passes" if s is not None and s["held"] else ""))
        elif s is not None and s["held"]:
            held.append(name)
            by["synthetic"].append(name)
        elif s is not None:
            why[name] = f"its synthetic case: {s['why']}"
        else:
            why[name] = "no recorded run reaches it and no synthetic case exercises it"
    return {
        **base,
        "measured": True,
        "held_methods": held,
        "not_held": sorted(set(called) - set(held)),
        "why_not_held": why,
        "held_by": by,
        "evidence": {which: {"receipt": ev[which]["path"], "sha256": ev[which].get("sha256")}
                     for which in ("production", "synthetic")},
        "readings": list(READINGS),
    }


def production_receipt(root: Path, record: str, dxvk: str, sdlgpu: str, census: str,
                       out: str = PRODUCTION) -> dict:
    """The production receipt for one recording (`record`: its directory, holding the
    recorder's counts.json and present-run.json) and both hosts' replays of its stream (each
    a compare_hosts.py run directory), with the frame census of that stream. Pins only;
    load_production() and three_way_numerator() derive what it holds."""
    def sha(rel_path: str) -> str:
        path = root / rel_path
        if not path.is_file():
            raise Refused(f"{rel_path} is absent")
        return sha256_file(path)
    rec_files = {n: sha(f"{record}/{n}") for n in ("counts.json", "present-run.json")}
    present = json.loads((root / record / "present-run.json").read_text())
    stream_sha = _get(present, "ready.stream_sha256")
    hosts, builds = {}, set()
    for host, d in (("dxvk", dxvk), ("sdlgpu", sdlgpu)):
        hosts[host] = {"dir": d, "files": {n: sha(f"{d}/{n}") for n in HOST_FILES[host]}}
        rep = json.loads((root / d / "report.json").read_text())
        player = _get(rep, "files.player.real_path")
        if not player:
            raise Refused(f"{d}/report.json names no player file")
        builds.add(Path(player).resolve().parent)
    if len(builds) != 1:
        raise Refused(f"the two hosts ran players from {sorted(map(str, builds))}, not one build")
    build = builds.pop()
    build_rel = build.relative_to(root.resolve()).as_posix() if build.is_relative_to(root.resolve()) else str(build)
    records = {}
    for host, name in PLAYER_RECORDS.items():
        doc = json.loads((build / name).read_text())
        records[name] = {"path": f"{build_rel}/{name}", "sha256": sha256_file(build / name),
                         "binaries": doc.get("binaries")}
    pipeline = {t: sha(t) for t in ("tools/s4/m7_inputs.py", PLAY_REPORT, "tools/s5/compare_hosts.py")}
    return {
        "schema": PRODUCTION_SCHEMA,
        "id": PRODUCTION_ID,
        "generated_by": "tools/s4/m7_inputs.py",
        "command": (f"{COMMAND} --production --record {record} --dxvk {dxvk} --sdlgpu {sdlgpu} "
                    f"--census {census} --out {out}"),
        "written": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": ("The three-way numerator's production evidence (the user's ruling of 2026-10-04: \"a method "
                 "counts only when the device answers it, the recorder captures it, and both players handle "
                 "it\"): one current recording of the game and both hosts' replays of its stream, by path and "
                 "sha256. Pins only: tools/s4/m7_inputs.py derives which methods it holds, and "
                 "Documentation/scripts/check_m7_inputs.py holds that derivation at every push; it reads not "
                 "measured once any source the recording or either player was made from is not the tree's."),
        "inputs": {"pipeline": pipeline},
        "recording": {"dir": record, "files": rec_files, "stream_sha256": stream_sha},
        "census": {"path": census, "sha256": sha(census)},
        "hosts": hosts,
        "players": {"build": build_rel, "records": records},
    }


# ------------------------------------------------------------------ derive

@dataclasses.dataclass
class Inputs:
    scan: dict                      # private/generated/s4/receipts/d3d9-methods.json
    table: list[dict]               # the probe's printed device table (device-status table)
    dynamic: list[dict]             # the reach run's dynamic surface
    d3dx9_rows: list[dict]          # the reach run's import table, d3dx9 rows
    registry: dict                  # Documentation/receipts/m7-recorded-runs.json
    supplement: dict                # Documentation/m7-census-supplement.json
    three_way: dict | None = None   # load_three_way(): the two evidence receipts, read


def name_of(interface: str, method: str) -> str:
    return f"{interface}::{method}"


def expand(interface: str) -> list[str]:
    out = []
    for i in str(interface).split("|"):
        out.extend(TEXTURES if i == BASE_TEXTURE else (i,))
    return out


def denominator_sets(inp: Inputs) -> dict[str, set[str]]:
    """The denominator's sets, by provenance, and `called`, their union."""
    scan = inp.scan
    static: set[str] = set()
    ambiguous: set[str] = set()
    for m in scan["layer_b_attributed"]["methods"]:
        ifaces = expand(m["interface"])
        for i in ifaces:
            static.add(name_of(i, m["method"]))
            if len(ifaces) > 1:
                ambiguous.add(name_of(i, m["method"]))
    reach = {name_of(r["interface"], r["method"]) for r in inp.dynamic}
    recorded = registry_names(inp.registry)
    guest_runs = census_names(inp.registry)
    census = supplement_names(inp.supplement)
    d3dx9 = {f"{D3DX}!{i['name']}" for i in scan["d3dx9_42"]["imports"]}
    # The scan finds a d3dx9 entry by its import call sites, not by Layer B.
    d3dx9_sited = {f"{D3DX}!{i['name']}" for i in scan["d3dx9_42"]["imports"]
                   if int(i.get("call_sites") or 0) > 0}
    return {"static": static, "ambiguous": ambiguous, "reach": reach, "recorded": recorded,
            "guest_runs": guest_runs, "census": census, "d3dx9": d3dx9, "d3dx9_sited": d3dx9_sited,
            "called": static | census | reach | recorded | guest_runs | d3dx9}


def numerator_sets(inp: Inputs) -> tuple[set[str], set[str]]:
    """(every name the device table or the d3dx9 imports know, the ones the device answers)."""
    rows = device_table_rows(inp.table)
    known = {name_of(i, r["method"]) for i, slots in rows.items() for r in slots.values()}
    known |= {f"{D3DX}!{r['name']}" for r in inp.d3dx9_rows}
    implemented = {name_of(i, r["method"]) for i, slots in rows.items()
                   for r in slots.values() if r["implemented"]}
    implemented |= {f"{D3DX}!{r['name']}" for r in inp.d3dx9_rows if r.get("implemented")}
    return known, implemented


def called_and_implemented(inp: Inputs) -> tuple[set[str], set[str]]:
    return denominator_sets(inp)["called"], numerator_sets(inp)[1]


RULES = ("R1_constant_function_table_global", "R2_this_of_rtti_confirmed_vtable_member",
         "R3_static_or_embedded_image_object")


def sound_rule_outs(scan: dict, ceiling) -> dict | None:
    """The scan's sound rule-outs (tools/s4/d3d9_methods.py `rule_outs`), checked, or None
    for a scan made before they existed.

    A sound rule never rules out a proven d3d9 call, so a scan is refused when a rule
    fired on an attributed site: when its own control list says so, and independently
    of that list, when a removed site is one of the attributed call sites the receipt
    lists (`layer_b_attributed.methods[*].call_sites`). So is one whose arithmetic does
    not add up. The post-rule-out figure is recorded beside the ceiling, never in its
    place: `unattributed_that_could_still_be_d3d9` stays the pre-rule-out ceiling,
    because that is the number G5[4]'s kill threshold reads and lowering it is a
    loosening reserved to the user (AGENTS rule 10)."""
    ro = scan.get("sound_rule_outs")
    if ro is None:
        return None
    # Each rule's control list must be there, and be a list of call sites, before anything
    # reads it: an absent one would read as empty, and a control hit is the only signal for a
    # rule firing on an attributed site. An int, a string, an object or a list of anything but
    # strings is refused as incomplete here, never iterated.
    try:
        controls = [ro[r]["control_sites_ruled_out"] for r in RULES]
    except (KeyError, TypeError) as e:
        raise Refused(f"the scan's sound_rule_outs block is incomplete ({e!r}). Re-run the "
                      "scan: " + COMMAND + " --rescan") from e
    malformed = [f"{r}'s control_sites_ruled_out is {c!r}" for r, c in zip(RULES, controls)
                 if type(c) is not list or not all(type(s) is str for s in c)]
    if malformed:
        raise Refused("the scan's sound_rule_outs block is incomplete (" + "; ".join(malformed)
                      + ", not a list of call sites). Re-run the scan: " + COMMAND + " --rescan")
    hits = sorted(s for c in controls for s in c)
    if hits:
        raise Refused("the scan's sound rule-outs ruled out attributed d3d9 call sites (a rule "
                      "is unsound): " + ", ".join(hits) + ". Re-run the scan: " + COMMAND
                      + " --rescan")
    try:
        before = ro["ceiling_before_rules"]
        removed = [ro[r]["removed"] for r in RULES]
        lists = [ro[r]["sites"] for r in RULES]
        after = ro["ceiling_after_sound_rule_outs"]
        steps = [ro["ceiling_after_R1"], ro["ceiling_after_R2"], ro["ceiling_after_R3"]]
    except (KeyError, TypeError) as e:
        raise Refused(f"the scan's sound_rule_outs block is incomplete ({e!r}). Re-run the "
                      "scan: " + COMMAND + " --rescan") from e
    # Every count an integer and every site list a list of call sites, before any arithmetic
    # or iteration reads them: a malformed field is refused here by name, never raised as a
    # TypeError from the sums below (wave-1 review minor).
    malformed = [f"{r}'s sites is {s!r}" for r, s in zip(RULES, lists)
                 if type(s) is not list or not all(type(v) is str for v in s)]
    malformed += [f"{name} is {v!r}" for name, v in
                  [*((f"{r}'s removed", n) for r, n in zip(RULES, removed)),
                   ("ceiling_before_rules", before), ("ceiling_after_sound_rule_outs", after),
                   *((f"ceiling_after_R{k}", s) for k, s in enumerate(steps, 1))]
                  if type(v) is not int]
    rs = ro.get("removed_sites")
    if rs is not None and (type(rs) is not list or not all(type(v) is str for v in rs)):
        malformed.append(f"removed_sites is {rs!r}")
    if malformed:
        raise Refused("the scan's sound_rule_outs block is incomplete (" + "; ".join(malformed)
                      + ", not an integer or a list of call sites). Re-run the scan: " + COMMAND
                      + " --rescan")
    every = [s for sites in lists for s in sites]
    try:
        attributed = {int(v, 16) for m in scan["layer_b_attributed"]["methods"]
                      for v in m["call_sites"]}
        ruled = {int(v, 16) for v in every + list(ro.get("removed_sites") or [])}
    except (KeyError, TypeError, ValueError) as e:
        raise Refused(f"the scan's attributed call sites or removed sites cannot be read "
                      f"({e!r}). Re-run the scan: " + COMMAND + " --rescan") from e
    # The method list is what the removed sites are held against, so its distinct sites must
    # be exactly the attributed call sites the scan counts: an emptied, shortened or padded
    # list, or a scan that counts none (call_sites absent or not an integer), is refused. This
    # holds the list to the same scan's own count; a scan edited consistently in both is not
    # caught here.
    counted = scan["layer_b_attributed"].get("call_sites")
    if type(counted) is not int or len(attributed) != counted:
        raise Refused(f"the scan's attributed methods list {len(attributed)} distinct call "
                      f"sites and it counts {counted!r}. Re-run the scan: " + COMMAND
                      + " --rescan")
    proven = sorted(ruled & attributed)
    if proven:
        raise Refused("the scan's sound rule-outs removed attributed d3d9 call sites (a rule "
                      "is unsound, and its control did not say so): "
                      + ", ".join(hex(v) for v in proven) + ". Re-run the scan: " + COMMAND
                      + " --rescan")
    wrong = []
    if before != ceiling:
        wrong.append(f"its ceiling_before_rules {before} is not the census ceiling {ceiling}")
    if any(n != len(sites) for n, sites in zip(removed, lists)):
        wrong.append("a rule's removed count is not the length of its site list")
    if len(set(every)) != len(every):
        wrong.append("a site is removed by more than one rule")
    if sorted(set(every)) != sorted(ro.get("removed_sites") or []):
        wrong.append("removed_sites is not the union of the rules' sites")
    running = before
    for n, step in zip(removed, steps):
        running -= n
        if step != running:
            wrong.append(f"a ceiling_after step reads {step}, not {running}")
            break
    if after != before - sum(removed):
        wrong.append(f"ceiling_after_sound_rule_outs {after} is not {before} - {sum(removed)}")
    if wrong:
        raise Refused("the scan's sound rule-outs do not add up: " + "; ".join(wrong)
                      + ". Re-run the scan: " + COMMAND + " --rescan")
    r1 = ro["R1_constant_function_table_global"]
    objects, must = r1.get("objects") or {}, ro.get("must_analysis") or {}
    unconverged = must.get("functions_whose_analysis_did_not_converge") or () \
        if isinstance(must, dict) else None
    if not isinstance(objects, dict) or not all(isinstance(o, dict) or o is None
                                                for o in objects.values()) \
            or not isinstance(unconverged, (list, tuple)):
        raise Refused("the scan's sound_rule_outs block is incomplete (R1's objects is not a map "
                      "of objects, or must_analysis is not a map with a list of functions). "
                      "Re-run the scan: " + COMMAND + " --rescan")
    return {
        "ceiling_before_rules": before,
        "R1_removed": removed[0], "R2_removed": removed[1], "R3_removed": removed[2],
        "ceiling_after_sound_rule_outs": after,
        "assumption": ro.get("assumption"),
        "R1_globals": r1.get("globals"),
        "R1_enclosing_object_escapes": {
            g: (o or {}).get("object_address_escapes")
            for g, o in sorted(objects.items())},
        "functions_whose_must_analysis_did_not_converge": len(unconverged),
        "note": ("sound rule-outs rule these ceiling sites out as d3d9 COM vtable calls under "
                 "the stated assumption and holes; each rule's soundness, holes and site list "
                 "are in the scan's "
                 "sound_rule_outs. This figure does not feed the kill threshold, which reads "
                 "unattributed_that_could_still_be_d3d9; moving the threshold to it is a "
                 "loosening reserved to the user (AGENTS rule 10). R1 rests on a stated "
                 "hole, counted in R1_enclosing_object_escapes: a pointer to the object "
                 "that holds the global, reaching a write the scan does not follow."),
    }


CEILING_SITE_KEYS = ("va", "function", "disp", "receiver", "region", "ruled_out_by")
CEILING_SITES_FIELD = "layer_a_shape_census.ceiling_sites"
GROWTH_RULING = "Documentation/decisions/2026-10-09-S5-user-rulings.md"


def ceiling_site_record(scan: dict) -> tuple[dict | None, list[str]]:
    """(the tracked receipt's record of the scan's ceiling-site list, the problems with that
    list). The list itself stays in the private scan receipt (CEILING_SITES_FIELD): thousands of
    guest addresses in a tracked file would fail Documentation/scripts/check_boundary.py. The
    record carries its length, the sha256 of its canonical form (its rows in address order, each
    with exactly CEILING_SITE_KEYS, as canonical JSON), and its counts by receiver class and by
    code region, which hold no address. The list must be one row per ceiling site: its length is
    the ceiling (`unattributed_that_could_still_be_d3d9`). None, with a problem, for a scan made
    before the list existed."""
    census_ = scan.get("layer_a_shape_census") or {}
    rows = census_.get("ceiling_sites")
    if rows is None:
        return None, [f"the scan {SCAN} lists no ceiling sites ({CEILING_SITES_FIELD}): it was made "
                      f"before every site was listed. Re-run the scan: {COMMAND} --rescan"]
    problems: list[str] = []
    if not isinstance(rows, list) or not all(
            isinstance(r, dict) and set(r) == set(CEILING_SITE_KEYS)
            and all(isinstance(r[k], str) for k in CEILING_SITE_KEYS if k != "ruled_out_by")
            and (r["ruled_out_by"] is None or isinstance(r["ruled_out_by"], str)) for r in rows):
        return None, [f"the scan's {CEILING_SITES_FIELD} is not a list of {{{', '.join(CEILING_SITE_KEYS)}}} "
                      f"rows. Re-run the scan: {COMMAND} --rescan"]
    try:
        keyed = sorted(rows, key=lambda r: int(r["va"], 16))
    except ValueError:
        return None, [f"the scan's {CEILING_SITES_FIELD} names an address that is not hex. Re-run the scan: "
                      f"{COMMAND} --rescan"]
    if len({int(r["va"], 16) for r in rows}) != len(rows):
        problems.append(f"the scan's {CEILING_SITES_FIELD} lists a site twice")
    ceiling = census_.get("unattributed_that_could_still_be_d3d9")
    if len(rows) != ceiling:
        problems.append(f"the scan's {CEILING_SITES_FIELD} lists {len(rows)} sites and its ceiling is "
                        f"{ceiling!r}: every site the ceiling counts is listed, once")
    if problems:
        return None, [p + f". Re-run the scan: {COMMAND} --rescan" for p in problems]

    def counts(key: str) -> dict:
        out: dict[str, int] = {}
        for r in rows:
            k = "none" if r[key] is None else str(r[key])      # a site no sound rule removes
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items()))
    return {"listed_in": SCAN, "field": CEILING_SITES_FIELD, "columns": list(CEILING_SITE_KEYS),
            "count": len(rows),
            "sha256": sha256_bytes(canonical([{k: r[k] for k in CEILING_SITE_KEYS} for r in keyed]).encode()),
            "by_receiver": counts("receiver"), "by_region": counts("region"),
            "by_sound_rule": counts("ruled_out_by")}, []


def instrument_growth(head: dict | None, receipt: dict) -> dict | None:
    """How the scan's ceiling and the misses grew against HEAD's receipt, or None when neither
    did (the user's ruling of 2026-10-09, item 1: "guard both against regression as instrument
    health"). The ceiling grows when it is above HEAD's; the misses grow when they name a method
    HEAD's do not. A fall is not growth, and the receipt that records it is the next bar."""
    if not isinstance(head, dict):
        return None
    h_ceiling = head.get("unattributed_that_could_still_be_d3d9")
    ceiling = receipt.get("unattributed_that_could_still_be_d3d9")
    h_missed = head.get("called_by_the_run_but_missed_by_the_scan")
    missed = receipt.get("called_by_the_run_but_missed_by_the_scan")
    grew_ceiling = type(h_ceiling) is int and type(ceiling) is int and ceiling > h_ceiling
    added = sorted(set(missed) - set(h_missed)) \
        if isinstance(h_missed, list) and isinstance(missed, list) else []
    if not grew_ceiling and not added:
        return None
    return {"against_head": {"ceiling": h_ceiling, "missed": sorted(h_missed or [])},
            "ceiling": ceiling, "missed_added": added}


def growth_problems(head: dict | None, receipt: dict) -> list[str]:
    """Why the receipt's instrument_growth does not name the growth against HEAD: none when
    nothing grew; otherwise a record whose figures are exactly instrument_growth()'s and whose
    cause is stated."""
    grew = instrument_growth(head, receipt)
    if grew is None:
        return []
    what = []
    if type(grew["ceiling"]) is int and type(grew["against_head"]["ceiling"]) is int \
            and grew["ceiling"] > grew["against_head"]["ceiling"]:
        what.append(f"the scan's ceiling grew from {grew['against_head']['ceiling']} to {grew['ceiling']}")
    if grew["missed_added"]:
        what.append(f"the scan now misses {', '.join(grew['missed_added'])}, which a driven run called")
    rec = receipt.get("instrument_growth")
    if isinstance(rec, dict) and isinstance(rec.get("cause"), str) and rec["cause"].strip() \
            and {k: rec.get(k) for k in grew} == grew:
        return []
    return [f"{RECEIPT}: against HEAD's receipt " + " and ".join(what) + ". The ceiling and the misses "
            f"are instrument health ({GROWTH_RULING}, item 1): a scan or a census in which either grew "
            f"fails until the receipt names the cause. Regenerate with the cause stated: {COMMAND} "
            f"--growth-cause \"<what moved them>\""]


def derive(inp: Inputs) -> tuple[dict, dict]:
    """(the denominator's sets, the numerator's) from the inputs, with no I/O: the tool
    writes what this returns, and the gate compares what is written with it."""
    scan = inp.scan
    sets = denominator_sets(inp)
    static, ambiguous, reach, recorded = sets["static"], sets["ambiguous"], sets["reach"], sets["recorded"]
    census, d3dx9, d3dx9_sited, called = sets["census"], sets["d3dx9"], sets["d3dx9_sited"], sets["called"]
    known, implemented = numerator_sets(inp)

    census_ = scan["layer_a_shape_census"]
    ceiling = census_.get("unattributed_that_could_still_be_d3d9")
    rule_outs = sound_rule_outs(scan, ceiling)
    denominator = {
        "called": sorted(called),
        "from_the_static_scan": sorted(static),
        "from_the_census_supplement": sorted(census),
        "from_the_reach_run": sorted(reach),
        "from_the_recorded_runs": sorted(recorded),
        "from_the_guest_run_census": sorted(sets["guest_runs"]),
        "d3dx9_imports": sorted(d3dx9),
        "called_by_the_run_but_missed_by_the_scan":
            sorted((reach | recorded | sets["guest_runs"]) - static - d3dx9_sited),
        "attributed_to_more_than_one_interface": sorted(ambiguous),
        "named_by_neither_the_device_table_nor_the_d3dx9_imports": sorted(called - known),
        "vtable_shaped_call_sites": census_["vtable_shaped_call_sites"],
        "unattributed_vtable_call_sites": census_["unattributed_vtable_call_sites"],
        "unattributed_that_could_still_be_d3d9": ceiling,
        "unattributed_ruled_out_by_displacement":
            census_.get("unattributed_displacement_past_every_d3d9_vtable"),
        "ceiling_after_sound_rule_outs": rule_outs and rule_outs["ceiling_after_sound_rule_outs"],
        "sound_rule_outs": rule_outs,
        "ceiling_sites": ceiling_site_record(scan)[0],
        "what_the_ceiling_and_the_misses_mean": (
            f"{census_['unattributed_vtable_call_sites']} of "
            f"{census_['vtable_shaped_call_sites']} vtable-shaped call sites are unattributed, "
            f"of which at most {ceiling} could be d3d9 calls (the rest have a displacement past "
            "the widest d3d9 vtable and cannot be), each listed in the private scan receipt "
            "(ceiling_sites). The user ruled on 2026-10-09 "
            f"({GROWTH_RULING}, item 1) that the methods called by the binary are the methods "
            "the scan proves plus what runs observe, the union this denominator counts, not "
            "every method the binary could call; the could-call space is handled by enforcing "
            "the closed world at run time. So the ceiling and the methods a driven run called "
            "that the scan missed are not G5 pass conditions: the M7 row prints both, and "
            "Documentation/scripts/check_m7_inputs.py fails a regeneration in which either grew "
            "against HEAD's receipt unless instrument_growth names the cause."),
        "three_way_numerator": three_way_numerator(inp, called, implemented),
    }
    numerator = {"implemented": sorted(implemented), "named_methods": len(inp.table),
                 "refuses": sum(1 for r in inp.table if not r["implemented"])}
    return denominator, numerator


# ------------------------------------------------------------------ reading the tree

def read_json(root: Path, rel: str):
    path = root / rel
    if not path.is_file():
        raise Refused(f"{rel} is absent")
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        raise Refused(f"{rel} is not JSON: {e}") from e


def reach_tie(root: Path) -> dict:
    """What the reach receipt says M7's numerator is read from."""
    reach = read_json(root, REACH)
    r = reach.get("reach") or {}
    table = r.get("import_table") or {}
    if not r.get("run_json") or not table.get("path") or not table.get("sha256"):
        raise Refused(f"{REACH} names no run_json or import table; re-run "
                      "tools/s4/reach_receipt.py --name reach --replay boot --write")
    return {"run_json": r["run_json"], "import_table": {"path": table["path"],
                                                        "sha256": table["sha256"]},
            "runtime_sha256": (reach.get("manifest") or {}).get("runtime_sha256")}


def load_inputs(root: Path, registry: dict | None = None, supplement: dict | None = None,
                evidence: tuple[str, str] = (PRODUCTION, SYNTHETIC)) -> tuple[Inputs, dict]:
    """(Inputs, the files they came from with their sha256s), read from the tree. `evidence`
    names the three-way numerator's two receipts (production, synthetic), by path."""
    scan = read_json(root, SCAN)
    status = read_json(root, DEVICE_STATUS)
    tie = reach_tie(root)
    d3dx9_table = read_json(root, tie["import_table"]["path"])
    registry = registry if registry is not None else read_json(root, REGISTRY)
    supplement = supplement if supplement is not None else read_json(root, SUPPLEMENT)
    rows = [r for r in d3dx9_table.get("rows", []) if D3DX in str(r.get("dll", "")).lower()]
    inp = Inputs(scan=scan, table=status["table"], dynamic=status["run"]["dynamic_surface"],
                 d3dx9_rows=rows, registry=registry, supplement=supplement,
                 three_way=load_three_way(root, *evidence))
    return inp, {"status": status, "tie": tie}


def evidence_inputs(root: Path, evidence: tuple[str, str] = (PRODUCTION, SYNTHETIC)) -> dict:
    """The receipt's record of the three-way evidence it read: each receipt's path and sha256
    (None when absent), held by the gate like every other input."""
    return {which: {"path": rel, "sha256": cached_sha256(root / rel)}
            for which, rel in zip(("production", "synthetic"), evidence)}


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")


def scan_provenance(root: Path, scan: dict, previous: dict | None, ran_tool_sha: str | None) -> dict:
    """The scan's own record, the tool that made it, and the layout it read."""
    sha = sha256_file(root / SCAN)
    ins = scan.get("inputs") or {}
    if ran_tool_sha is not None:
        tool_sha = ran_tool_sha
    else:
        old = ((previous or {}).get("inputs") or {}).get("scan") or {}
        if old.get("sha256") != sha or not old.get("tool_sha256"):
            raise Refused(f"{SCAN} was not produced by this tool's --rescan, and it does not "
                          "record which tools/s4/d3d9_methods.py made it, so nothing vouches "
                          "for the scan. Run: " + COMMAND + " --rescan")
        tool_sha = old["tool_sha256"]
    vt = ins.get("vtable_tables") or {}
    text = text_at_sha(root, D3D9_C, vt.get("sha256", ""))
    if text is None:
        raise Refused(f"the scan read {D3D9_C} at sha256 {vt.get('sha256')!r}, which neither the "
                      "working tree nor any commit holds; its layout cannot be compared")
    return {"path": SCAN, "sha256": sha, "generated_utc": scan.get("generated_utc"),
            "tool_sha256": tool_sha,
            "exe_sha256": (ins.get("executable") or {}).get("sha256"),
            "function_map_sha256": (ins.get("function_map") or {}).get("sha256"),
            "layout_read_from_sha256": vt.get("sha256"),
            "layout_digest": layout_digest(text),
            "fixpoint_reached": scan["layer_b_attributed"].get("fixpoint_reached"),
            # a fixpoint over truncated local analyses is not one; the scan now requires
            # both lists empty for fixpoint_reached, and the counts stand beside it
            "functions_whose_last_analysis_hit_the_block_visit_limit": len(
                scan["layer_b_attributed"].get(
                    "functions_whose_last_analysis_hit_the_block_visit_limit") or ()),
            "functions_skipped_for_size": len(
                scan["layer_b_attributed"].get("functions_skipped_for_size") or ())}


def rescan(root: Path) -> str:
    """Run the scan; return the tool's sha256, which must not move while it runs."""
    tool = root / SCAN_TOOL
    before = sha256_file(tool)
    print(f"re-running the scan ({SCAN_TOOL}; 7 to 13 minutes, no game run)")
    done = subprocess.run([sys.executable, str(tool)], cwd=root, check=False)
    if done.returncode != 0:
        raise Refused(f"{SCAN_TOOL} exited {done.returncode}")
    if sha256_file(tool) != before:
        raise Refused(f"{SCAN_TOOL} changed while it ran; run --rescan again")
    return before


def head_json(root: Path, rel: str):
    """`rel` as HEAD holds it (read-only `git show`), or None when HEAD does not hold it or
    `root` is not its own repository's top."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True,
                             text=True, check=False)
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        blob = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root, capture_output=True, check=False)
    except OSError:
        return None
    if blob.returncode != 0:
        return None
    try:
        return json.loads(blob.stdout)
    except ValueError:
        return None


def growth_record(head: dict | None, receipt: dict, previous: dict | None, cause: str | None) -> dict | None:
    """The receipt's instrument_growth: None when nothing grew against HEAD; the growth with
    `cause` when one is given; the previous receipt's record when it names exactly this growth;
    otherwise Refused, naming the growth and the flag that records its cause."""
    grew = instrument_growth(head, receipt)
    if grew is None:
        return None
    if cause is not None and cause.strip():
        return {**grew, "cause": cause.strip(),
                "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    old = (previous or {}).get("instrument_growth")
    if isinstance(old, dict) and {k: old.get(k) for k in grew} == grew and isinstance(old.get("cause"), str) \
            and old["cause"].strip():
        return old
    raise Refused("; ".join(growth_problems(head, receipt)))


def regenerate(root: Path, *, ran_tool_sha: str | None, retire_missing: bool,
               growth_cause: str | None = None) -> dict:
    import device_status  # noqa: E402
    import reach_receipt  # noqa: E402

    previous = None
    if (root / RECEIPT).is_file():
        try:
            previous = json.loads((root / RECEIPT).read_text())
        except ValueError:
            previous = None
    old_registry = json.loads((root / REGISTRY).read_text()) \
        if (root / REGISTRY).is_file() else None

    tie = reach_tie(root)
    digest = reach_receipt.runtime_digest(root)
    if tie["runtime_sha256"] != digest:
        raise Refused(f"the reach run was measured on runtime {tie['runtime_sha256']!r} and the "
                      f"tree's runtime digest is {digest!r}, so M7's numerator would describe "
                      f"another runtime. Re-run the reach first: {REACH_COMMAND}; then: {COMMAND}")
    device_status.write(root / tie["run_json"], root / DEVICE_STATUS, root)

    # The census's reading is held to the probe before the index is brought up to date: a file
    # read under a reading that has drifted from the probe would be cached by its size and mtime
    # and not read again once the reading was brought back.
    held = probe_control_problems(root)
    if held:
        raise Refused("; ".join(held))
    print("registering recorded runs and the census of every guest run (the run index)")
    census = update_index(root, build=True, by="tools/s4/m7_inputs.py")
    print(f"  run index: {census.stats}")
    if census.unreadable:
        raise Refused("guest run records on disk whose device table the census cannot count: "
                      + "; ".join(f"{r} ({why})" for r, why in sorted(census.unreadable.items())[:8])
                      + ". " + UNREADABLE_REMEDY + " Then: " + COMMAND)
    registry = build_registry(root, old_registry, disk=census.recorder_runs,
                              retire_missing=retire_missing, device=census.indexed)
    registry_text = json.dumps(registry, indent=1, sort_keys=True) + "\n"

    # Nothing tracked is written until every check below has passed.
    inp, extra = load_inputs(root, registry=registry)
    status = extra["status"]
    denominator, numerator = derive(inp)
    _, site_problems = ceiling_site_record(inp.scan)
    if site_problems:
        raise Refused("; ".join(site_problems))
    if denominator["named_by_neither_the_device_table_nor_the_d3dx9_imports"]:
        raise Refused("names in the denominator that are neither a device-table row nor a "
                      "d3dx9 import (naming drift between recorder, scan and device): "
                      + ", ".join(denominator["named_by_neither_the_device_table_nor_the_d3dx9_"
                                              "imports"]))
    drift = cross_check(device_table_rows(inp.table),
                        parse_device_rows((root / D3D9_C).read_text(encoding="utf-8")),
                        {r["name"]: bool(r.get("implemented")) for r in inp.d3dx9_rows},
                        parse_d3dx9_rows((root / IMPORTS_C).read_text(encoding="utf-8")))
    if drift:
        raise Refused("the reach run's device table is not the tree's runtime; re-run the "
                      "reach (tools/s4/reach_receipt.py --name reach --replay boot --write): "
                      + "; ".join(describe(d) for d in drift[:5]))

    inputs = {
        "pipeline": {rel: sha256_file(root / rel) for rel in PIPELINE},
        "scan": scan_provenance(root, inp.scan, previous, ran_tool_sha),
        "numerator": {
            "device_status": {"path": DEVICE_STATUS, "sha256": sha256_file(root / DEVICE_STATUS)},
            "run_json": {"path": tie["run_json"], "sha256": status["inputs"]["probe_run"]["sha256"]},
            "runtime_digest": status["inputs"]["run_runtime_sha256"],
            "d3d9_c_sha256": status["inputs"]["d3d9_c_at_write"]["sha256"],
        },
        "d3dx9_table": tie["import_table"],
        "registry": {"path": REGISTRY, "sha256": sha256_bytes(registry_text.encode())},
        "census_supplement": {"path": SUPPLEMENT, "sha256": sha256_file(root / SUPPLEMENT)},
        "three_way": evidence_inputs(root),
    }
    if inputs["numerator"]["runtime_digest"] != digest:
        raise Refused(f"the device status records the run's runtime as "
                      f"{inputs['numerator']['runtime_digest']!r}, not the tree's {digest!r}. "
                      f"Re-run the reach first: {REACH_COMMAND}; then: {COMMAND}")

    receipt = {
        "schema": "sa2-recomp-measurement/v1",
        "id": "m7-inputs",
        "generated_by": "tools/s4/m7_inputs.py",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "command": COMMAND,
        "note": ("M7's denominator, regenerated by tools/s4/m7_inputs.py with the full "
                 "denominator the user ruled on 2026-10-04: the static scan, the census "
                 "supplement, the reach run, every registered recorded run, the census of every "
                 "guest run's device table (the user's ruling of 2026-10-09, item 1) and the "
                 "d3dx9 imports. Documentation/scripts/check_m7_inputs.py holds every input to "
                 "the tree on every push. The user's reading of design 8's \"methods called by "
                 "the binary\" (2026-10-09): the methods the scan proves plus what runs observe, "
                 "this union, not every method the binary could call. The scan's ceiling and "
                 "its misses are instrument health, printed on the M7 row and guarded against "
                 "growth (instrument_growth). The numerator metrics.py reads is the three-way "
                 "numerator the user ruled (three_way_numerator below) when that block reads "
                 "measured, and otherwise the device's alone, an upper bound on it."),
        "source": SCAN,
        "inputs": inputs,
        **denominator,
    }
    growth = growth_record(head_json(root, RECEIPT), receipt, previous, growth_cause)
    if growth is not None:
        receipt["instrument_growth"] = growth
    (root / REGISTRY).write_text(registry_text)
    write_json(root / RECEIPT, receipt)
    write_json(root / STATUS_OUT, {
        "note": ("M7's numerator, regenerated by tools/s4/m7_inputs.py from the reach run's "
                 "device table (private/generated/s4/device-status.json) and its import table. "
                 "`implemented` names every method the recording no-op device answers so that "
                 "the call returns to the guest; it does not mean the answer is what a GPU would "
                 "give, nor that the recorder or either player handles the call. The user's "
                 "second ruling of 2026-10-04 counts a method only when the device answers it, "
                 "the recorder captures it and both players handle it, so this is an upper "
                 "bound on that numerator, which Documentation/receipts/d3d9-methods.json "
                 "three_way_numerator measures from its evidence receipts."),
        "source": DEVICE_STATUS,
        "run_json": tie["run_json"],
        "runtime_digest": inputs["numerator"]["runtime_digest"],
        **numerator,
    })
    return receipt


def summary(receipt: dict, numerator_implemented: list[str]) -> str:
    called = set(receipt["called"])
    done = len(called & set(numerator_implemented))
    return (f"M7 inputs: called {len(called)} (static {len(receipt['from_the_static_scan'])}, "
            f"supplement {len(receipt['from_the_census_supplement'])}, "
            f"reach {len(receipt['from_the_reach_run'])}, "
            f"recorded runs {len(receipt['from_the_recorded_runs'])}, "
            f"guest run census {len(receipt.get('from_the_guest_run_census') or [])}, "
            f"d3dx9 {len(receipt['d3dx9_imports'])}), implemented-and-called {done}; "
            f"scan missed {len(receipt['called_by_the_run_but_missed_by_the_scan'])} a run "
            f"called; ceiling {receipt['unattributed_that_could_still_be_d3d9']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rescan", action="store_true",
                    help="re-run tools/s4/d3d9_methods.py first and vouch for its output")
    ap.add_argument("--retire-missing", action="store_true",
                    help="keep the names a deleted registered run reached, and retire it")
    ap.add_argument("--production", action="store_true",
                    help="write the three-way numerator's production receipt (with --record, --dxvk, "
                         "--sdlgpu, --census, --out) instead of regenerating")
    ap.add_argument("--record", help="--production: the recording's directory (counts.json, present-run.json)")
    ap.add_argument("--dxvk", help="--production: the DXVK host's run directory of that stream")
    ap.add_argument("--sdlgpu", help="--production: the second host's run directory of that stream")
    ap.add_argument("--census", help="--production: the frame census of that stream (census.json)")
    ap.add_argument("--out", default=PRODUCTION, help="--production: where to write it")
    ap.add_argument("--index", action="store_true",
                    help="build or bring up to date the run index of every guest run under "
                         "private/generated (the census's cache; the first build is a full walk, "
                         "several minutes) and stop")
    ap.add_argument("--growth-cause", metavar="TEXT",
                    help="record why the scan's ceiling or the misses grew against HEAD's receipt "
                         "(instrument_growth); the gate fails such growth until it is recorded")
    args = ap.parse_args(argv)
    if args.index:
        try:
            census = update_index(ROOT, build=True, by="tools/s4/m7_inputs.py --index",
                                  rebuild_runs=True)
        except Refused as e:
            print(f"M7_INPUTS_REFUSED {e}", file=sys.stderr)
            return 1
        print(f"run index {INDEX_DIR}: {census.stats}")
        for rel, why in sorted(census.unreadable.items()):
            print(f"  cannot count {rel}: {why}")
        return 0
    if args.production:
        if not all((args.record, args.dxvk, args.sdlgpu, args.census)):
            ap.error("--production needs --record, --dxvk, --sdlgpu and --census")
        try:
            doc = production_receipt(ROOT, args.record, args.dxvk, args.sdlgpu, args.census, out=args.out)
        except Refused as e:
            print(f"M7_INPUTS_REFUSED {e}", file=sys.stderr)
            return 1
        write_json(Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out, doc)
        print(f"-> {args.out}")
        return 0
    try:
        ran = rescan(ROOT) if args.rescan else None
        receipt = regenerate(ROOT, ran_tool_sha=ran, retire_missing=args.retire_missing,
                             growth_cause=args.growth_cause)
    except Refused as e:
        print(f"M7_INPUTS_REFUSED {e}", file=sys.stderr)
        return 1
    status = json.loads((ROOT / STATUS_OUT).read_text())
    print(summary(receipt, status["implemented"]))
    three = receipt["three_way_numerator"]
    print("three-way numerator: " + (f"measured, {len(three['held_methods'])} of {len(receipt['called'])} held"
                                     if three.get("measured") is True
                                     else f"not measured: {str(three.get('why_not_measured'))[:400]}"))
    print(f"-> {RECEIPT}, {REGISTRY}, {STATUS_OUT}")
    for line in row_moved(ROOT):
        print(line)
    # The device's closed world traps every method off `called` (runtime/sa2_d3d9.c; the
    # user's ruling of 2026-10-09, item 1), so the list it holds is rewritten with the
    # receipt. A changed header is a changed runtime: every run is then rerun.
    import m7_device_list  # noqa: E402  (tools/s4, beside this file)
    before = m7_device_list.HEADER.read_text() if m7_device_list.HEADER.is_file() else None
    if m7_device_list.main([]) != 0:
        return 1
    if m7_device_list.HEADER.read_text() != before:
        print(f"M7_INPUTS the closed world's list moved: {m7_device_list.rel(m7_device_list.HEADER)} "
              "was rewritten, so the runtime changed and every registered run is stale")
    return 0


KILL_LEDGER = "Documentation/kill-criteria.json"
KILL_REVIEW = "design-S5-d3d9-surface"


def row_moved(root: Path) -> list[str]:
    """What else the commit carrying this regeneration must change when it moves the M7 row:
    the row is regenerated into METRICS.md, the open kill review answered against it is
    answered again (check_kill_criteria.py), and a row that gained or lost a clause (the
    three-way block changing state) needs G5's M7 criterion's METRICS.md shape repinned
    (check_criterion_clauses.py names the digest). Printed, never applied: those records are
    the main thread's. Empty when the row reads as METRICS.md does."""
    try:
        sys.path.insert(0, str(root / "Documentation" / "scripts"))
        import metrics  # noqa: E402
        new = metrics.m7_device(root).value
        old = metrics.parse_table((root / metrics.METRICS_FILE).read_text(encoding="utf-8")).get("M7 device")
    except Exception as e:      # noqa: BLE001 (an aid; the gates hold the records themselves)
        return [f"M7_ROW_UNKNOWN the new M7 row could not be computed ({e}); run metrics.py --write"]
    if new == old:
        return []
    out = [f"M7_ROW_MOVED {old!r} -> {new!r}",
           "  in the same commit: regenerate METRICS.md (ship.py does), and answer "
           f"{KILL_REVIEW} in {KILL_LEDGER} again against the new row (review.answered_against.value; its "
           "projection is carried forward unchanged)"]
    if (M7_DEVICE_ONLY_MARK in (old or "")) != (M7_DEVICE_ONLY_MARK in new):
        out.append("  the row gained or lost its device-only clause (the three-way block changed state), so "
                   "G5's M7 criterion's METRICS.md evidence_shape moves too: check_criterion_clauses.py names "
                   "the digest to pin")
    if M7_DEVICE_ONLY_MARK in new and M7_DEVICE_ONLY_MARK not in (old or ""):
        out.append(f"  the three-way block now reads not measured; to keep it measured instead, make its evidence "
                   f"again first ({REFRESH}) and regenerate")
    return out


M7_DEVICE_ONLY_MARK = "numerator is the device's alone"


if __name__ == "__main__":
    raise SystemExit(main())
