#!/usr/bin/env python3
"""Gate: M7's inputs describe the tree they claim to measure.

What this gate is for
---------------------
M7 read "58/67" from 2026-09-21 until S5's 50 % review, and the review answered
the S5 kill criterion `design-S5-d3d9-surface` against that row. Every one of the
nine methods it counted as called and not implemented had been implemented at
`development_snapshot`, four hours after M7's inputs were last regenerated (`development_snapshot`). The
numerator was still read from a control probe of `runtime/sa2_d3d9.c` at
`a631a4b4...`, the denominator from a 25-method run and a scan nobody re-ran,
and **every gate passed**, because no gate compared M7's inputs with anything. The user ruled
(`Documentation/decisions/2026-10-04-S5-user-rulings.md`): "no split. The
surface did not grow; M7 was stale. Regenerate M7 with the full denominator
(recorded runs plus static call sites) and add a staleness check like run
reach's, so M7 cannot silently go stale again."

This is that check, in the manner of `check_run_reach.py`: seconds, no game
run, and falsifications on every pass. It fails when any input
`tools/s4/m7_inputs.py` recorded in `Documentation/receipts/d3d9-methods.json`
no longer matches the tree:

* **pipeline**: the three tools' sha256 (a tool edit without a regeneration);
* **scan**: the scan file, the executable it read (and `tools/pins.json`'s pin),
  the function map, the tool that made it, and the vtable layout it attributed
  against, as `d3d9_methods.parse_vtables()` reads `runtime/sa2_d3d9.c` now;
* **numerator**: the reach run was measured on the tree's runtime (the reach
  receipt's `manifest.runtime_sha256`; a runtime change is cleared by re-running
  the reach first), the device status is that run's and records that run's
  runtime, and the run file is unchanged. The device status is re-derived from
  the run by `tools/s4/device_status.py build()` and compared whole, so a table
  row or a dynamic-surface name edited by hand is found even with its digests
  refreshed;
* **the numerator against the source**, independent of every hash: each named
  device row's method, argument count, flags and handler as the probe printed
  them, against a static parse of `runtime/sa2_d3d9.c`; each d3dx9 entry's
  implemented flag against `runtime/sa2_imports.c`. Run on the sources of
  `development_snapshot`, the commit that published 58/67, it names exactly the nine methods
  (`test_m7_inputs.py`);
* **d3dx9 table**: the reach receipt's import table, path and sha256;
* **recorded runs**: every counts file in `Documentation/receipts/m7-recorded-runs.json`
  is unchanged and present, its method count and the registry's union recount;
  every recorder run a receipt under `Documentation/receipts/` names (a path
  anywhere in its text, joined to another by `|` or `=` or not), and every stream
  `host/d3d9gpu/state_census.sh` replays (every entry of its STREAM table, which
  must match its STREAMS default), is registered; and every guest run a disk
  census of `private/generated` finds reached no name outside `called`.
  Discovery can only add failures, so it is a strengthening;
* **the census of every game run** (the user's ruling of 2026-10-09, item 1): every
  guest run record harness/crt_probe.c wrote, with or without the recorder, gone
  ones included, reached no name outside `called` through the device's own table,
  and none is a record the census cannot read. The census is bounded
  (`m7_inputs.update_index`: the run index caches private/generated's directories
  and JSON files, so a push stats them and reads only what moved, seconds, not the
  minutes a walk takes); an absent index fails here rather than being rebuilt, and
  the census's own falsifications (DISCOVERY_CASES) show each way a run reaches the
  disk is found. This gate writes the index back: it is a private cache and the
  append-only record of every run it reads. The census's reading is held to the
  probe (`m7_inputs.probe_control_problems`): harness/crt_probe.c's record must
  begin with the header the census matches and write the WinMain section and the
  device table's interface, method and calls columns, and the reach run and each
  production recording's run record must read as guest run records with a method
  called, so a probe that began writing another shape fails here instead of
  leaving every game run since uncounted. What the census does not count is
  `m7_inputs.CENSUS_EXCLUDES`. The gate waits at most
  `m7_inputs.GATE_LOCK_WAIT_SECONDS` for another update's lock, naming its holder;
* **instrument health** (the same ruling): the scan lists every ceiling site
  (its count is the ceiling), and against HEAD's receipt neither the ceiling nor
  the misses grew unless the receipt's `instrument_growth` names the cause;
* **it only grows**: against the receipt, registry and supplement at `HEAD`
  (`git show`), every run HEAD registered is registered or retired, every name
  HEAD's `called` holds is still in it, every name HEAD's census of every guest
  run counted is still counted, and a supplement row leaves only when
  the scan attributes its method. A name leaves M7's denominator only by the
  user's ruling (AGENTS.md rule 10);
* **census supplement**: every row of `Documentation/m7-census-supplement.json`
  re-decoded in the pinned executable (inside the renderer module this gate
  states, `mov r,[base+D]` at the load, nothing writing `r` or branching before
  the site, `call r`/`jmp r` as the row says, D = 4 x the named method's slot in
  `runtime/sa2_d3d9.c`), and the module the file declares is the one this gate
  holds it to;
* **the three-way numerator's evidence**: the two receipts it is derived from
  (`Documentation/receipts/s5-m7-production-v1.json`, a current recording of the
  game and both hosts' replays of it; `Documentation/receipts/s5-m7-synthetic-v1.json`,
  one authored stream per method no recorded run reaches, with its omit falsifier)
  are the ones M7 was regenerated from (`inputs.three_way`, path and sha256, or
  absent at both);
* **derived equality**: `called` and its provenance lists, `missed`, the
  ceiling fields, the `three_way_numerator` block and
  `private/generated/device-status.json` equal what `m7_inputs.derive()`
  computes from the inputs on disk (a hand-edited receipt or a partial
  regeneration), and every name in `called` is a device-table row or a d3dx9
  import (naming drift). The three-way block is derived again from the raw
  evidence: every file the two receipts pin is hashed and the small ones read
  again (the reports, checks, counts, census and device reports; each authored
  stream read back with `tools/s5/stream_dump.py`; the 12 GB recorded stream is
  held through its own record, not read), every source they were made from is
  compared with the tree's, and each method's verdict recomputed with the shared
  predicate (`m7_inputs.case_problems`). A difference is named: which method the
  receipt holds that its evidence does not, or the other way, or that the
  evidence derives not measured and why. Evidence made from sources the tree no
  longer holds derives not measured, never a refusal: the regeneration then
  writes it so, with the reason.

Every failure names the one command that clears it. A full rescan belongs to
`tools/s4/m7_inputs.py --rescan`, not here.

Who clears it
-------------
The M7 plan owns this gate and `tools/s4/m7_inputs.py`. Regenerating M7's inputs
is the main thread's, at any integration where this gate reads stale: a runtime
change (the reach first), a receipt naming a recorder run, a new guest run on
disk that reached a method outside `called`, or the M7 plan's later phases. A
regeneration that moves the M7 row also re-answers the kill review
`design-S5-d3d9-surface` in the same commit (`check_kill_criteria.py`), keeping
its `projection`.

Re-running it
-------------

    source tools/env.sh
    .venv/bin/python tools/s4/m7_inputs.py

Falsification runs on every pass: each case writes a mutated copy of the tree
(symlinks for everything it does not change), or hands `problems()` another
HEAD or disk census, and must make `problems()` fail with its own message.

Usage: .venv/bin/python Documentation/scripts/check_m7_inputs.py
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))

import m7_inputs as M  # noqa: E402

COMMAND = M.COMMAND
ZERO = "0" * 64
# The renderer module the census supplement's sites must lie in (the M7 plan, section 2.1):
# held here, not read from the supplement, so the supplement cannot widen its own bounds.
RENDERER_MODULE = ("0x00865BA0", "0x0086A000")
# The files whose HEAD copy the tree may only grow from.
HEAD_FILES = (M.RECEIPT, M.REGISTRY, M.SUPPLEMENT)
# How many falsifications ran, and which, counted from the cases built (check_run_reach.py's RAN).
RAN = 0
CASES: list[str] = []

_SHA: dict[tuple, str] = {}
_STATUS: dict[tuple, dict] = {}


def sha(path: Path) -> str:
    """sha256 of a file, cached by its resolved path and stat within this process: the
    falsification copies of the tree share every file they do not change."""
    real = path.resolve()
    st = real.stat()
    key = (str(real), st.st_size, st.st_mtime_ns)
    if key not in _SHA:
        _SHA[key] = M.sha256_file(real)
    return _SHA[key]


def stale(what: str, was, now) -> str:
    return (f"M7 is stale: {what} was {was!r} when M7 was regenerated and is {now!r} now. "
            f"Run: {COMMAND}")


def load(root: Path, rel: str, out: list[str]):
    path = root / rel
    if not path.is_file():
        out.append(f"{rel} is absent. Run: {COMMAND}")
        return None
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        out.append(f"{rel} is not JSON ({e}). Run: {COMMAND}")
        return None


def head_docs(root: Path) -> dict[str, dict | None]:
    """{path: its JSON at HEAD, or None} for HEAD_FILES, read with `git show` (read-only).
    None for a file HEAD does not hold, and for a root that is not its own repository's top."""
    out: dict[str, dict | None] = {rel: None for rel in HEAD_FILES}
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                             capture_output=True, text=True, check=False)
    except OSError:
        return out
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        return out
    for rel in HEAD_FILES:
        blob = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root, capture_output=True,
                              check=False)
        if blob.returncode == 0:
            try:
                out[rel] = json.loads(blob.stdout)
            except ValueError:
                out[rel] = None
    return out


def derived_status(root: Path, run: Path) -> dict:
    """tools/s4/device_status.py's build() of `run` in the tree at `root`, cached by what it
    reads: the run, the reach receipt and runtime/sa2_d3d9.c."""
    import device_status  # noqa: E402
    key = tuple((str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns)
                for p in (run, root / M.REACH, root / M.D3D9_C))
    if key not in _STATUS:
        _STATUS[key] = device_status.build(run, root)
    return _STATUS[key]


# ------------------------------------------------------------------ the census supplement

def decode_supplement(root: Path, supplement: dict,
                      layout: dict[str, dict[int, dict]]) -> list[str]:
    """Re-decode every supplement row in the pinned executable; one sentence per defect."""
    import pefile  # noqa: E402
    from iced_x86 import (Decoder, FlowControl, InstructionInfoFactory, Mnemonic,  # noqa: E402
                          OpAccess, OpKind, Register, RegisterExt)
    out: list[str] = []
    exe = root / "private" / "exe" / M.EXE_PIN
    if not exe.is_file():
        return [f"{exe.relative_to(root)} is absent, so the census supplement cannot be "
                "re-decoded"]
    module = supplement.get("renderer_module") or {}
    if (module.get("start"), module.get("end")) != RENDERER_MODULE:
        out.append(f"the census supplement declares the renderer module as "
                   f"{module.get('start')}..{module.get('end')}; this gate holds its sites to "
                   f"{RENDERER_MODULE[0]}..{RENDERER_MODULE[1]}, and the supplement cannot "
                   f"move its own bounds")
    lo, hi = (int(v, 16) for v in RENDERER_MODULE)
    pe = pefile.PE(str(exe), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    writes = (OpAccess.WRITE, OpAccess.READ_WRITE, OpAccess.COND_WRITE,
              OpAccess.READ_COND_WRITE)
    info = InstructionInfoFactory()
    for row in supplement.get("rows") or []:
        tag = f"census supplement row {row.get('va')} ({row.get('interface')}::{row.get('method')})"
        try:
            va, load_va = int(row["va"], 16), int(row["load_va"], 16)
            disp = int(row["displacement"], 16)
        except (KeyError, TypeError, ValueError):
            out.append(f"{tag}: va, load_va and displacement must be hex numbers")
            continue
        if not (lo <= load_va < va < hi):
            out.append(f"{tag}: the site and its load are not inside the renderer module "
                       f"{RENDERER_MODULE[0]}..{RENDERER_MODULE[1]} in that order")
            continue
        slot = next((s for s, r in (layout.get(row.get("interface")) or {}).items()
                     if r["method"] == row.get("method")), None)
        if slot is None:
            out.append(f"{tag}: runtime/sa2_d3d9.c names no such method")
            continue
        if disp != 4 * slot:
            out.append(f"{tag}: displacement {row['displacement']} is not 4 x slot {slot}")
        data = pe.get_data(load_va - base, va - load_va + 16)
        ins = []
        for i in Decoder(32, data, ip=load_va):
            ins.append(i)
            if i.ip >= va:
                break
        if not ins or ins[-1].ip != va:
            out.append(f"{tag}: a decode from the load does not land on the site")
            continue
        first, site = ins[0], ins[-1]
        if first.mnemonic != Mnemonic.MOV or first.op0_kind != OpKind.REGISTER \
                or first.op1_kind != OpKind.MEMORY or first.memory_base == Register.NONE \
                or first.memory_index != Register.NONE or not RegisterExt.is_gpr32(first.op0_register):
            out.append(f"{tag}: the load is not mov r32,[base+D]")
            continue
        reg = first.op0_register
        if (first.memory_displacement & 0xFFFFFFFF) != disp:
            out.append(f"{tag}: the load reads displacement {hex(first.memory_displacement)}, "
                       f"not {row['displacement']}")
        for i in ins[1:-1]:
            if i.flow_control != FlowControl.NEXT:
                out.append(f"{tag}: control flow between the load and the site at {hex(i.ip)}")
                break
            if any(RegisterExt.full_register32(u.register) == reg and u.access in writes
                   for u in info.info(i).used_registers()):
                out.append(f"{tag}: {hex(i.ip)} writes the loaded register before the site")
                break
        want = Mnemonic.JMP if row.get("shape") == "jmp" else Mnemonic.CALL
        if row.get("shape") not in ("call", "jmp") or site.mnemonic != want \
                or site.op0_kind != OpKind.REGISTER or site.op0_register != reg:
            out.append(f"{tag}: the site is not `{row.get('shape')} r` through the loaded register")
    return out


# ------------------------------------------------------------------ the gate

def grows_from_head(head: dict[str, dict | None], receipt: dict, registry: dict | None,
                    supplement: dict | None) -> list[str]:
    """What the tree dropped that HEAD counted: a registered run, a name in `called`, or a
    supplement row whose method the scan does not attribute."""
    out: list[str] = []
    hr = head.get(M.REGISTRY)
    if hr is not None and registry is not None:
        now = {r.get("run") for r in (registry.get("runs") or []) + (registry.get("retired") or [])}
        before = {r.get("run") for r in (hr.get("runs") or []) + (hr.get("retired") or [])}
        for run in sorted(before - now, key=str):
            out.append(f"the registry at HEAD counts the run {run}, which is neither registered "
                       f"nor retired now. A run leaves the registry only through {COMMAND} "
                       f"--retire-missing, which keeps every name only it reached")
    if hr is not None and registry is not None:
        lost = sorted(M.census_names(hr) - M.census_names(registry))
        if lost:
            out.append(f"the census of every guest run at HEAD counts {', '.join(lost)}, and the "
                       f"tree's registry does not (device_table_census). A name the census counted "
                       f"stays counted (kept_from_earlier_census); restore it. Run: {COMMAND}")
    hc = head.get(M.RECEIPT)
    if hc is not None:
        lost = sorted(set(hc.get("called") or []) - set(receipt.get("called") or []))
        if lost:
            out.append(f"M7's denominator at HEAD counts {', '.join(lost)}, and the tree's does "
                       f"not. A name leaves M7's denominator only by the user's ruling "
                       f"(AGENTS.md rule 10); restore it")
    hs = head.get(M.SUPPLEMENT)
    if hs is not None and supplement is not None:
        def key(r: dict) -> tuple:
            return r.get("va"), r.get("interface"), r.get("method")
        rows_now = {key(r) for r in supplement.get("rows") or []}
        scanned = set(receipt.get("from_the_static_scan") or [])
        for r in hs.get("rows") or []:
            if key(r) not in rows_now and f"{r.get('interface')}::{r.get('method')}" not in scanned:
                out.append(f"the census supplement at HEAD holds the row {r.get('va')} "
                           f"({r.get('interface')}::{r.get('method')}), and the tree's does not "
                           f"while the scan does not attribute that method. A row is retired "
                           f"only when the scan attributes it; restore it")
    return out


def problems(root: Path | None = None, *, head: dict[str, dict | None] | None = None,
             disk: dict[str, set[str]] | None = None, census: "M.Census | None" = None) -> list[str]:
    """Every reason Documentation/receipts/d3d9-methods.json does not describe the tree at
    `root`, as a sentence. `head` is the receipt, registry and supplement at HEAD (default:
    read with git), `disk` the recorder runs on disk with the names each reached and `census`
    every guest run record with the names its device table reached (default: the run index of
    `root`, brought up to date)."""
    root = root or ROOT
    out: list[str] = []
    receipt = load(root, M.RECEIPT, out)
    if receipt is None:
        return out
    ins = receipt.get("inputs")
    if not isinstance(ins, dict):
        return [f"{M.RECEIPT} records no inputs, so nothing ties it to the tree (it was written "
                f"before the 2026-10-04 regeneration). Run: {COMMAND}"]
    head = head_docs(root) if head is None else head
    if census is None:
        try:
            # Under a reading the probe's writer has drifted from (which the probe control below
            # fails), the index is read but not written back: a file cached as not a game run
            # under that reading would not be read again once the reading was brought back.
            census = M.update_index(root, by="check_m7_inputs.py", lock_wait=M.GATE_LOCK_WAIT_SECONDS,
                                    write=not M.probe_control_problems(root))
        except M.Refused as e:
            out.append(str(e))
            census = M.Census([], {}, {}, {}, {})
    disk = M.disk_census(root, census) if disk is None else disk

    # pipeline
    for rel in M.PIPELINE:
        recorded = (ins.get("pipeline") or {}).get(rel)
        now = sha(root / rel) if (root / rel).is_file() else None
        if recorded != now:
            out.append(stale(f"inputs.pipeline {rel}", recorded, now))

    # scan
    sc = ins.get("scan") or {}
    scan = load(root, M.SCAN, out)
    if scan is not None:
        if sc.get("path") != M.SCAN:
            out.append(stale("inputs.scan.path", sc.get("path"), M.SCAN))
        if sc.get("sha256") != sha(root / M.SCAN):
            out.append(stale("inputs.scan.sha256", sc.get("sha256"), sha(root / M.SCAN)))
        sins = scan.get("inputs") or {}
        pins = load(root, M.PINS, out) or {}
        pin = ((pins.get("executables") or {}).get(M.EXE_PIN) or {}).get("sha256")
        exe = root / "private" / "exe" / M.EXE_PIN
        exe_now = sha(exe) if exe.is_file() else None
        for what, value in (("the scan's own executable", (sins.get("executable") or {})
                             .get("sha256")),
                            ("inputs.scan.exe_sha256", sc.get("exe_sha256")),
                            ("the executable on disk", exe_now)):
            if value != pin:
                out.append(f"M7's scan does not read the pinned executable: {what} is "
                           f"{value!r} and tools/pins.json pins {pin!r}. Run: {COMMAND} --rescan")
        fmap = root / M.FUNCTION_MAP
        fmap_now = sha(fmap) if fmap.is_file() else None
        for what, value in (("the scan's own function map", (sins.get("function_map") or {})
                             .get("sha256")),
                            ("inputs.scan.function_map_sha256", sc.get("function_map_sha256"))):
            if value != fmap_now:
                out.append(f"M7's scan is stale: {what} is {value!r} and "
                           f"{M.FUNCTION_MAP} is {fmap_now!r}. Run: {COMMAND} --rescan")
        tool_now = sha(root / M.SCAN_TOOL)
        if sc.get("tool_sha256") != tool_now:
            out.append(f"M7's scan is stale: inputs.scan.tool_sha256 is "
                       f"{sc.get('tool_sha256')!r} and {M.SCAN_TOOL} is {tool_now!r}. "
                       f"Run: {COMMAND} --rescan")
        read_from = (sins.get("vtable_tables") or {}).get("sha256")
        if sc.get("layout_read_from_sha256") != read_from:
            out.append(stale("inputs.scan.layout_read_from_sha256",
                             sc.get("layout_read_from_sha256"), read_from))
        digest_now = M.layout_digest((root / M.D3D9_C).read_text(encoding="utf-8"))
        if sc.get("layout_digest") != digest_now:
            out.append(stale("inputs.scan.layout_digest (the current layout)",
                             sc.get("layout_digest"), digest_now))
        out.extend(M.ceiling_site_record(scan)[1])
        then = M.text_at_sha(root, M.D3D9_C, read_from or "")
        if then is None:
            out.append(f"M7's scan read {M.D3D9_C} at {read_from!r}, which neither the tree "
                       f"nor any commit holds. Run: {COMMAND} --rescan")
        else:
            digest_then = M.layout_digest(then)
            if sc.get("layout_digest") != digest_then:
                out.append(stale("inputs.scan.layout_digest (the layout the scan read)",
                                 sc.get("layout_digest"), digest_then))
            if digest_now != digest_then:
                out.append(f"M7's scan is stale: the vtable layout in {M.D3D9_C} (slots, names, "
                           f"argument counts) is not the one the scan attributed against. "
                           f"Run: {COMMAND} --rescan")

    # numerator: the reach run, measured on the tree's runtime
    num = ins.get("numerator") or {}
    status = load(root, M.DEVICE_STATUS, out)
    reach = load(root, M.REACH, out) or {}
    run_json = (reach.get("reach") or {}).get("run_json")
    import reach_receipt  # noqa: E402
    digest = reach_receipt.runtime_digest(root)
    run_digest = (reach.get("manifest") or {}).get("runtime_sha256")
    if run_digest != digest:
        out.append(f"M7's numerator is read from the reach run, which was measured on runtime "
                   f"{run_digest!r}; the tree's runtime digest is {digest!r}. Re-run the reach "
                   f"first ({M.REACH_COMMAND}), then: {COMMAND}")
    if status is not None:
        if (num.get("device_status") or {}).get("sha256") != sha(root / M.DEVICE_STATUS):
            out.append(stale("inputs.numerator.device_status.sha256",
                             (num.get("device_status") or {}).get("sha256"),
                             sha(root / M.DEVICE_STATUS)))
        sinputs = status.get("inputs") or {}
        probe = (sinputs.get("probe_run") or {})
        if probe.get("path") != run_json or (num.get("run_json") or {}).get("path") != run_json:
            out.append(f"M7's numerator is not the reach run: the device status was written from "
                       f"{probe.get('path')!r}, the receipt records "
                       f"{(num.get('run_json') or {}).get('path')!r}, and the reach receipt's run "
                       f"is {run_json!r}. Run: {COMMAND}")
        if run_json and (root / run_json).is_file():
            now = sha(root / run_json)
            for what, value in (("the device status's probe_run.sha256", probe.get("sha256")),
                                ("inputs.numerator.run_json.sha256",
                                 (num.get("run_json") or {}).get("sha256"))):
                if value != now:
                    out.append(stale(what, value, now))
        else:
            out.append(f"the reach run {run_json!r} is absent, so M7's numerator has no source. "
                       f"Re-run the reach ({M.REACH_COMMAND}), then: {COMMAND}")
        for what, value in (("inputs.numerator.runtime_digest", num.get("runtime_digest")),
                            ("the device status's run_runtime_sha256",
                             sinputs.get("run_runtime_sha256"))):
            if value != run_digest:
                out.append(stale(what + " (the runtime the reach run was measured on)", value,
                                 run_digest))
        d3d9_now = sha(root / M.D3D9_C)
        if num.get("d3d9_c_sha256") != d3d9_now:
            out.append(stale("inputs.numerator.d3d9_c_sha256", num.get("d3d9_c_sha256"), d3d9_now))

    # d3dx9 table
    table = (reach.get("reach") or {}).get("import_table") or {}
    want = {"path": table.get("path"), "sha256": table.get("sha256")}
    if ins.get("d3dx9_table") != want:
        out.append(stale("inputs.d3dx9_table (the reach run's import table)",
                         ins.get("d3dx9_table"), want))
    elif not table.get("path") or not (root / table["path"]).is_file() \
            or sha(root / table["path"]) != table.get("sha256"):
        out.append(f"the reach run's import table {table.get('path')!r} is absent or not the one "
                   f"the reach receipt records. Re-run the reach ({M.REACH_COMMAND}), then: "
                   + COMMAND)

    # recorded runs
    registry = load(root, M.REGISTRY, out)
    called = set(receipt.get("called") or [])
    if registry is not None:
        if (ins.get("registry") or {}).get("sha256") != sha(root / M.REGISTRY):
            out.append(stale("inputs.registry.sha256", (ins.get("registry") or {}).get("sha256"),
                             sha(root / M.REGISTRY)))
        if not isinstance(registry.get("kept_from_earlier_counts"), dict):
            out.append(f"{M.REGISTRY} keeps no `kept_from_earlier_counts` map, so a name a "
                       f"re-recorded or retired run reached could leave M7 unseen. Run: {COMMAND}")
        reached: dict[str, int] = {}
        registered = set()
        for r in registry.get("runs") or []:
            registered.add(r.get("run"))
            counts = root / str(r.get("run")) / "counts.json"
            if not counts.is_file():
                out.append(f"the registered run {r.get('run')} has no counts.json: restore it, or "
                           f"keep its names with: {COMMAND} --retire-missing")
                continue
            if sha(counts) != r.get("counts_sha256"):
                out.append(stale(f"the counts of the registered run {r.get('run')}",
                                 r.get("counts_sha256"), sha(counts)))
                continue
            names = M.counts_methods(json.loads(counts.read_text()))
            if len(names) != r.get("methods"):
                out.append(f"the registry says {r.get('run')} reached {r.get('methods')} methods "
                           f"and its counts.json says {len(names)}. Run: {COMMAND}")
            for n in names:
                reached[n] = reached.get(n, 0) + 1
        if reached != registry.get("reached"):
            out.append(f"the registry's `reached` is not the union its runs' counts recount to. "
                       f"Run: {COMMAND}")
        for run, receipts in sorted(M.receipt_named_runs(root).items()):
            if run not in registered:
                out.append(f"{sorted(receipts)[0]} names the recorded run {run}, which M7's "
                           f"denominator does not count. Run: {COMMAND}")
        try:
            streams = M.census_streams(root)
        except M.Refused as e:
            out.append(f"{e}. Then: {COMMAND}")
            streams = {}
        for name, stream in sorted(streams.items()):
            run = M.stream_run(root, stream, sorted(registered))
            if run is None or run not in registered:
                out.append(f"the state census replays {name} ({stream}), whose run M7's "
                           f"denominator does not count. Run: {COMMAND}")
    for run, names in sorted(disk.items()):
        outside = sorted(names - called)
        if outside:
            out.append(f"the guest run {run} on disk reached {', '.join(outside)}, which M7's "
                       f"denominator does not count. Run: {COMMAND}")
    # The census of every guest run's device table, with or without the recorder (the user's
    # ruling of 2026-10-09, item 1), gone runs included: a new method grows the list.
    for run, names in sorted(census.indexed.items()):
        outside = sorted(names - called)
        if outside:
            out.append(f"the guest run record {run} (the census of every game run, its device "
                       f"table) reached {', '.join(outside)}, which M7's denominator does not "
                       f"count. Run: {COMMAND}")
    for run, why in sorted(census.unreadable.items()):
        out.append(f"the guest run record {run} cannot be counted by the census of every game "
                   f"run: {why}. {M.UNREADABLE_REMEDY} Then: {COMMAND}")
    # The census's reading held to the probe that writes the records (a probe writing another
    # shape would leave every game run since out of the census without a word).
    out.extend(M.probe_control_problems(root))

    # census supplement
    supplement = load(root, M.SUPPLEMENT, out)
    try:
        layout = M.parse_device_rows((root / M.D3D9_C).read_text(encoding="utf-8"))
    except M.Refused as e:
        out.append(str(e))
        layout = {}
    if supplement is not None:
        if (ins.get("census_supplement") or {}).get("sha256") != sha(root / M.SUPPLEMENT):
            out.append(stale("inputs.census_supplement.sha256",
                             (ins.get("census_supplement") or {}).get("sha256"),
                             sha(root / M.SUPPLEMENT)))
        out.extend(decode_supplement(root, supplement, layout))

    # the three-way numerator's evidence: the receipts M7 was regenerated from
    recorded_three = ins.get("three_way")
    now_three = M.evidence_inputs(root)
    if recorded_three is None:
        if any(v["sha256"] for v in now_three.values()):
            out.append(f"M7 is stale: the three-way numerator's evidence "
                       f"({', '.join(v['path'] for v in now_three.values() if v['sha256'])}) is on disk "
                       f"and the receipt records none. Run: {COMMAND}")
    else:
        for which, v in now_three.items():
            was = (recorded_three.get(which) or {})
            if was.get("path") != v["path"] or was.get("sha256") != v["sha256"]:
                out.append(stale(f"inputs.three_way.{which} ({v['path']})", was.get("sha256"), v["sha256"]))

    # it only grows
    out.extend(grows_from_head(head, receipt, registry, supplement))

    # instrument health: the scan's ceiling and the misses do not grow against HEAD unrecorded
    out.extend(M.growth_problems(head.get(M.RECEIPT), receipt))

    # the numerator against the source, independent of every hash
    if status is not None and layout:
        d3dx9_rows = []
        if table.get("path") and (root / table["path"]).is_file():
            d3dx9_rows = [r for r in json.loads((root / table["path"]).read_text()).get("rows", [])
                          if M.D3DX in str(r.get("dll", "")).lower()]
        try:
            parsed_d3dx9 = M.parse_d3dx9_rows((root / M.IMPORTS_C).read_text(encoding="utf-8"))
            for problem in M.cross_check(
                    M.device_table_rows(status.get("table") or []), layout,
                    {r["name"]: bool(r.get("implemented")) for r in d3dx9_rows}, parsed_d3dx9):
                out.append(f"M7's numerator is not the tree's runtime: {M.describe(problem)}. "
                           f"Re-run the reach ({M.REACH_COMMAND}), then: {COMMAND}")
        except M.Refused as e:
            out.append(str(e))

    # derived equality
    if not out:
        try:
            inp, _ = M.load_inputs(root)
            denominator, numerator = M.derive(inp)
            rebuilt = derived_status(root, root / run_json)
        except (M.Refused, KeyError, TypeError, SystemExit) as e:
            return out + [f"M7's inputs cannot be derived: {e!r}. Run: {COMMAND}"]
        written = {k: v for k, v in status.items() if k != "written"}
        if written != rebuilt:
            differ = sorted(k for k in set(written) | set(rebuilt)
                            if written.get(k) != rebuilt.get(k))
            out.append(f"{M.DEVICE_STATUS} is not what tools/s4/device_status.py derives from "
                       f"the reach run now (it differs in {', '.join(differ)}). Run: {COMMAND}")
        for key, value in denominator.items():
            if key == "three_way_numerator":
                out.extend(three_way_difference(receipt.get(key), value))
            elif receipt.get(key) != value:
                out.append(f"{M.RECEIPT} `{key}` is not what its inputs derive (a hand edit or a "
                           f"partial regeneration). Run: {COMMAND}")
        unknown = denominator["named_by_neither_the_device_table_nor_the_d3dx9_imports"]
        if unknown:
            out.append(f"names in M7's denominator that are neither a device-table row nor a "
                       f"d3dx9 import (naming drift): {', '.join(unknown)}")
        flat = load(root, M.STATUS_OUT, out)
        if flat is not None:
            for key, value in numerator.items():
                if flat.get(key) != value:
                    out.append(f"{M.STATUS_OUT} `{key}` is not what the reach run's tables "
                               f"derive. Run: {COMMAND}")
    return out


def three_way_difference(written, derived: dict) -> list[str]:
    """Where the receipt's `three_way_numerator` differs from what its evidence derives,
    named: the measured state, then each method held on one side only, else the block."""
    if written == derived:
        return []
    lead = f"{M.RECEIPT} `three_way_numerator` is not what its evidence derives"
    written = written if isinstance(written, dict) else {}
    if written.get("measured") != derived.get("measured"):
        return [f"{lead}: the receipt reads measured {written.get('measured')!r} and the evidence derives "
                f"measured {derived.get('measured')!r}"
                + (f" ({str(derived.get('why_not_measured'))[:300]})" if derived.get("why_not_measured") else "")
                + (f". Make the evidence again ({M.REFRESH}), then run: {COMMAND}" if derived.get("measured") is not True
                   else f". Run: {COMMAND}")]
    if derived.get("measured") is True:
        held_w, held_d = set(written.get("held_methods") or []), set(derived.get("held_methods") or [])
        out = [f"{lead}: the receipt holds {n}, which its evidence does not "
               f"({(derived.get('why_not_held') or {}).get(n, '')[:200]}). Run: {COMMAND}"
               for n in sorted(held_w - held_d)]
        out += [f"{lead}: its evidence holds {n}, which the receipt does not. Run: {COMMAND}"
                for n in sorted(held_d - held_w)]
        if out:
            return out
    return [f"{lead} (a hand edit or a partial regeneration). Run: {COMMAND}"]


# ------------------------------------------------------------------ falsification

def overlay(root: Path, dest: Path, changes: dict[str, bytes | None]) -> Path:
    """A copy of `root` at `dest` made of symlinks, except the paths in `changes`, which are
    written (bytes) or left out (None)."""
    def build(src: Path, dst: Path, rels: dict[str, bytes | None], prefix: str) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        heads = {}
        for rel, data in rels.items():
            head, _, tail = rel.partition("/")
            heads.setdefault(head, {})[tail] = data
        names = (set(os.listdir(src)) if src.is_dir() else set()) | set(heads)
        for name in sorted(names):
            s, d = src / name, dst / name
            if name not in heads:
                os.symlink(s, d)
                continue
            sub = heads[name]
            if "" in sub:                        # the file itself
                if sub[""] is not None:
                    d.write_bytes(sub[""])
                continue
            build(s, d, sub, prefix + name + "/")
    build(root, dest, changes, "")
    return dest


def falsifications() -> list[str]:
    global RAN, CASES
    try:
        receipt = json.loads((ROOT / M.RECEIPT).read_text())
        status = json.loads((ROOT / M.DEVICE_STATUS).read_text())
        reach = json.loads((ROOT / M.REACH).read_text())
        registry = json.loads((ROOT / M.REGISTRY).read_text())
        supplement = json.loads((ROOT / M.SUPPLEMENT).read_text())
        flat = json.loads((ROOT / M.STATUS_OUT).read_text())
    except (OSError, ValueError) as e:
        return [f"M7's inputs cannot be read, so no mutation of them can be shown to fail: {e}"]
    if not isinstance(receipt.get("inputs"), dict):
        return ["the receipt records no inputs, so no mutation of them can be shown to fail"]
    # Every case but those about HEAD or the disk runs as if HEAD were the tree and no guest
    # run on disk reached anything, so the case's own mutation is the only difference.
    here = {M.RECEIPT: receipt, M.REGISTRY: registry, M.SUPPLEMENT: supplement}

    def doc(obj) -> bytes:
        return (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()

    def edit(document: dict, f) -> dict:
        d = copy.deepcopy(document)
        f(d)
        return d

    def set_path(d: dict, keys: tuple, value) -> None:
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = value

    cases: list[tuple] = []     # (name, changes, expect, head, disk, census)

    def case(name: str, changes: dict, expect: str, head: dict | None = None,
             disk: dict | None = None, census: "M.Census | None" = None) -> None:
        cases.append((name, changes, expect, head or here, disk or {},
                      census or M.Census([], {}, {}, {}, {})))

    def receipt_case(name: str, keys: tuple, value=ZERO, expect: str = "",
                     head: dict | None = None) -> None:
        case(name, {M.RECEIPT: doc(edit(receipt, lambda d: set_path(d, keys, value)))}, expect,
             head=head)

    for rel in M.PIPELINE:
        receipt_case(f"a pipeline tool changed ({rel})", ("inputs", "pipeline", rel),
                     expect=rel)
    for key in ("sha256", "exe_sha256", "function_map_sha256", "tool_sha256", "layout_digest",
                "layout_read_from_sha256"):
        receipt_case(f"the scan's {key} changed", ("inputs", "scan", key), expect=key)
    receipt_case("the device status changed", ("inputs", "numerator", "device_status", "sha256"),
                 expect="device_status.sha256")
    receipt_case("the reach run file changed", ("inputs", "numerator", "run_json", "sha256"),
                 expect="run_json.sha256")
    receipt_case("the runtime moved and the numerator did not",
                 ("inputs", "numerator", "runtime_digest"), expect="runtime_digest")
    receipt_case("runtime/sa2_d3d9.c moved and the numerator did not",
                 ("inputs", "numerator", "d3d9_c_sha256"), expect="d3d9_c_sha256")
    receipt_case("the d3dx9 table changed", ("inputs", "d3dx9_table", "sha256"),
                 expect="d3dx9_table")
    receipt_case("the registry changed", ("inputs", "registry", "sha256"),
                 expect="registry.sha256")
    receipt_case("the census supplement changed", ("inputs", "census_supplement", "sha256"),
                 expect="census_supplement.sha256")
    real_block = receipt.get("three_way_numerator") or {}
    forged = dict(real_block, measured=True, not_held=[])
    if forged == real_block:
        # The tree's block already reads met (every called method held), so setting it met
        # changes nothing: the hand edit is a met block the evidence does not give, one held
        # method taken out while nothing is said not held.
        forged["held_methods"] = list(real_block.get("held_methods") or [])[1:]
    receipt_case("the three-way numerator was set measured by hand",
                 ("three_way_numerator",), forged, expect="`three_way_numerator`")
    three_way_cases(receipt, case, doc, edit)

    # A source file changed and its sha256 in the receipt refreshed by hand, so that only the
    # check reading inside that file can see it.
    def inner_case(name: str, rel: str, document: dict, keys: tuple, value, recorded: tuple,
                   expect: str) -> None:
        changed = doc(edit(document, lambda d: set_path(d, keys, value)))
        if rel == M.DEVICE_STATUS:      # device_status.py writes it unsorted, indent 1
            changed = (json.dumps(edit(document, lambda d: set_path(d, keys, value)), indent=1)
                       + "\n").encode()
        rc = edit(receipt, lambda d: set_path(d, recorded, M.sha256_bytes(changed))) \
            if recorded else receipt
        case(name, {rel: changed, M.RECEIPT: doc(rc)}, expect)

    scan = json.loads((ROOT / M.SCAN).read_text())
    sha_key = ("inputs", "scan", "sha256")
    inner_case("the scan read another executable", M.SCAN, scan,
               ("inputs", "executable", "sha256"), ZERO, sha_key, "the scan's own executable")
    inner_case("the scan read another function map", M.SCAN, scan,
               ("inputs", "function_map", "sha256"), ZERO, sha_key, "the scan's own function map")
    inner_case("the scan read another runtime layout", M.SCAN, scan,
               ("inputs", "vtable_tables", "sha256"), ZERO, sha_key, "layout_read_from_sha256")
    ds_key = ("inputs", "numerator", "device_status", "sha256")
    inner_case("the device status records another run's runtime", M.DEVICE_STATUS, status,
               ("inputs", "run_runtime_sha256"), ZERO, ds_key, "run_runtime_sha256")
    inner_case("the device status was written from another copy of the run", M.DEVICE_STATUS,
               status, ("inputs", "probe_run", "sha256"), ZERO, ds_key, "probe_run.sha256")
    inner_case("the device status was written from another run", M.DEVICE_STATUS, status,
               ("inputs", "probe_run", "path"), "private/generated/s4/run/d3d9break.control.json",
               ds_key, "not the reach run")
    surface = (status.get("run") or {}).get("dynamic_surface") or []
    if surface:
        inner_case("a name was dropped from the device status's dynamic surface", M.DEVICE_STATUS,
                   status, ("run", "dynamic_surface"), surface[1:], ds_key,
                   "derives from the reach run now (it differs in run)")
    inner_case("the reach run was measured on another runtime", M.REACH, reach,
               ("manifest", "runtime_sha256"), ZERO, (), "Re-run the reach first")
    if registry.get("runs"):
        reg_key = ("inputs", "registry", "sha256")
        inner_case("a registered run's method count was edited", M.REGISTRY, registry,
                   ("runs", 0, "methods"), registry["runs"][0]["methods"] + 1, reg_key,
                   "methods and its counts.json says")
    # HEAD left out, so that the derived equality, which runs only on an otherwise clean tree,
    # is what must see it (the HEAD case below holds the other half).
    receipt_case("a name was removed from called", ("called",),
                 receipt["called"][1:], expect="`called`", head={**here, M.RECEIPT: None})

    runs = registry.get("runs") or []
    census_run = next((r for r in runs if any(s.startswith("census:menu") for s in r["sources"])),
                      None)
    receipt_run = next((r for r in runs if any(s.startswith("receipt:") for s in r["sources"])),
                       None)
    if runs:
        case("a registered run was re-recorded",
             {M.REGISTRY: doc(edit(registry, lambda d: d["runs"][0].__setitem__(
                 "counts_sha256", ZERO)))}, "counts of the registered run")
        case("the registry's union lost a name",
             {M.REGISTRY: doc(edit(registry, lambda d: d["reached"].pop(
                 sorted(d["reached"])[0])))}, "`reached`")
        case("a registered run's counts were deleted",
             {f"{runs[0]['run']}/counts.json": None}, "--retire-missing")
        case("the registry lost its kept map",
             {M.REGISTRY: doc(edit(registry, lambda d: d.pop("kept_from_earlier_counts")))},
             "kept_from_earlier_counts")
    for label, victim, expect in (("a census stream's run was dropped from the registry",
                                   census_run, "state census"),
                                  ("a receipt-named run was dropped from the registry",
                                   receipt_run, "does not count")):
        if victim is not None:
            case(label, {M.REGISTRY: doc(edit(registry, lambda d, v=victim: d.__setitem__(
                "runs", [r for r in d["runs"] if r["run"] != v["run"]])))}, expect)
    # A receipt naming a recorder run nobody registered. The planted run is a copy of a real
    # counts file in a directory of its own.
    if runs:
        planted = "private/generated/m7-falsification-run"
        counts = (ROOT / runs[0]["run"] / "counts.json").read_bytes()
        case("a receipt names a run M7 does not count", {
            f"{planted}/counts.json": counts,
            f"{M.RECEIPTS_DIR}/zz-m7-falsification.json": doc({"run": f"{planted}/counts.json"}),
        }, "does not count")
        case("a receipt names a run M7 does not count inside a |-joined string", {
            f"{planted}/counts.json": counts,
            f"{M.RECEIPTS_DIR}/zz-m7-falsification.json": doc({"env": (
                f"SA2_LIFTED={M.REGISTRY}|SA2_REC_COUNTS={planted}/counts.json")}),
        }, f"names the recorded run {planted}")
    # The census script's stream table, read whole.
    census_text = (ROOT / M.CENSUS_SCRIPT).read_text()
    menu_line = next((line for line in census_text.splitlines()
                      if line.strip().startswith("[menu]=$REPO/")), None)
    if menu_line is not None and census_run is not None:
        quoted = menu_line.replace("=$REPO/", '="$REPO/', 1) + '"'
        case("a census stream written quoted, its run dropped from the registry", {
            M.CENSUS_SCRIPT: census_text.replace(menu_line, quoted, 1).encode(),
            M.REGISTRY: doc(edit(registry, lambda d: d.__setitem__(
                "runs", [r for r in d["runs"] if r["run"] != census_run["run"]]))),
        }, "state census replays menu")
        case("a census stream written in a form the parser cannot read", {
            M.CENSUS_SCRIPT: census_text.replace(
                menu_line, menu_line.replace("=$REPO/", "=${MENU:-$REPO/", 1) + "}", 1).encode(),
        }, "cannot read")
        case("the census table leaves out a stream its default replays", {
            M.CENSUS_SCRIPT: census_text.replace(menu_line + "\n", "", 1).encode(),
        }, "a census stream would be left out")

    # It only grows: HEAD held more than the tree.
    case("HEAD registered a run the tree neither registers nor retires", {}, "registry at HEAD",
         head={**here, M.REGISTRY: edit(registry, lambda d: d["runs"].append(
             {"run": "private/generated/m7-falsification-head-run"}))})
    case("HEAD's denominator held a name the tree's does not", {}, "denominator at HEAD",
         head={**here, M.RECEIPT: edit(receipt, lambda d: d["called"].append(
             "IDirect3DDevice9::NotCountedByM7"))})
    case("HEAD's supplement held a row the scan does not attribute, and the tree dropped it", {},
         "supplement at HEAD",
         head={**here, M.SUPPLEMENT: edit(supplement, lambda d: d["rows"].append(
             {"va": "0x00865BB0", "interface": "IDirect3DDevice9", "method": "NotCountedByM7"}))})
    # A guest run on disk reached a name M7 does not count.
    case("a guest run on disk reached a name M7 does not count", {}, "on disk reached",
         disk={"private/generated/m7-falsification-disk-run": {"IDirect3DDevice9::NotCountedByM7"}})
    # The census of every game run (the user's ruling of 2026-10-09, item 1): a guest run without
    # the recorder, on disk or gone, reached a name M7 does not count; and one it cannot read.
    case("a guest run record without the recorder reached a name M7 does not count", {},
         "the census of every game run, its device table",
         census=M.Census([], {}, {"private/generated/m7-falsification-run.json":
                                  {"IDirect3DDevice9::NotCountedByM7"}}, {}, {}))
    case("a guest run record the census cannot read", {}, "cannot be counted by the census",
         census=M.Census([], {}, {}, {"private/generated/m7-falsification-run.json":
                                      M.UNREADABLE["t"]}, {}))
    case("HEAD's census of every guest run counted a name the tree's does not", {},
         "census of every guest run at HEAD",
         head={**here, M.REGISTRY: edit(registry, lambda d: d.setdefault(
             "device_table_census", {}).setdefault("kept_from_earlier_census", []).append(
             "IDirect3DDevice9::NotCountedByM7"))})
    # Instrument health (the user's ruling of 2026-10-09, item 1): the ceiling and the misses do
    # not grow against HEAD unless the receipt names the cause; a fall is accepted. Every case is
    # built whatever the tree holds, so none can drop out of the count unseen.
    ceiling = receipt.get("unattributed_that_could_still_be_d3d9")
    ceiling = ceiling if type(ceiling) is int else 0
    missed = receipt.get("called_by_the_run_but_missed_by_the_scan")
    missed = missed if isinstance(missed, list) else []
    case("the scan's ceiling grew against HEAD and no record names the cause", {},
         "the scan's ceiling grew from",
         head={**here, M.RECEIPT: edit(receipt, lambda d: d.__setitem__(
             "unattributed_that_could_still_be_d3d9", ceiling - 1))})
    grown = edit(receipt, lambda d: d.__setitem__("instrument_growth", {
        "against_head": {"ceiling": ceiling - 2, "missed": sorted(missed)}, "ceiling": ceiling,
        "missed_added": [], "cause": "a record of another growth"}))
    case("a growth record names other figures than the growth against HEAD",
         {M.RECEIPT: doc(grown)}, "the scan's ceiling grew from",
         head={**here, M.RECEIPT: edit(receipt, lambda d: d.__setitem__(
             "unattributed_that_could_still_be_d3d9", ceiling - 1))})
    case("the misses grew against HEAD and no record names the cause",
         {M.RECEIPT: doc(edit(receipt, lambda d: d.__setitem__(
             "called_by_the_run_but_missed_by_the_scan", sorted(missed + ["IDirect3DDevice9::NotCountedByM7"]))))},
         "the scan now misses IDirect3DDevice9::NotCountedByM7")
    # Every ceiling site listed: the private list one short of the ceiling, the scan's sha256
    # refreshed by hand (a stand-in list of the ceiling's length when the scan predates the
    # list); and the tracked record of the list edited (or forged, when the receipt has none).
    scan_doc = json.loads((ROOT / M.SCAN).read_text())
    scan_ceiling = (scan_doc.get("layer_a_shape_census") or {}).get("unattributed_that_could_still_be_d3d9")
    sites = (scan_doc.get("layer_a_shape_census") or {}).get("ceiling_sites")
    if not isinstance(sites, list) or not sites:
        sites = [{"va": f"0x{0x00401000 + 4 * k:08X}", "function": "0x00401000", "disp": "0x0",
                  "receiver": "unknown", "region": "game", "ruled_out_by": None}
                 for k in range(scan_ceiling if type(scan_ceiling) is int and scan_ceiling > 0 else 1)]
    short = edit(scan_doc, lambda d: d.setdefault("layer_a_shape_census", {}).__setitem__(
        "ceiling_sites", sites[1:]))
    short_bytes = doc(short)
    case("the scan lists one ceiling site fewer than its ceiling", {
        M.SCAN: short_bytes,
        M.RECEIPT: doc(edit(receipt, lambda d: d["inputs"].setdefault("scan", {}).__setitem__(
            "sha256", M.sha256_bytes(short_bytes))))}, "every site the ceiling counts is listed")
    case("the tracked record of the ceiling-site list was edited",
         {M.RECEIPT: doc(edit(receipt, lambda d: d.__setitem__("ceiling_sites", {
             **(d.get("ceiling_sites") if isinstance(d.get("ceiling_sites"), dict) else {"count": ceiling}),
             "sha256": ZERO})))}, "`ceiling_sites`")
    # The census's reading held to the probe (the 2026-10-09 review): the reach run's record
    # written with a key before "va", so its first bytes are not the header the census matches;
    # the probe's writer changed to begin the record otherwise; and its device table no longer
    # writing each method's calls.
    reach_run = (reach.get("reach") or {}).get("run_json")
    if isinstance(reach_run, str) and (ROOT / reach_run).is_file():
        data = (ROOT / reach_run).read_bytes()
        case("the reach run's record gained a key before \"va\", which the census does not recognise",
             {reach_run: b'{\n "written_by": "crt_probe",' + data[1:]}, "does not recognise")
    else:
        case("the reach run's record gained a key before \"va\", which the census does not recognise",
             {}, "does not recognise")
    probe_text = (ROOT / M.PROBE_WRITER).read_text(encoding="utf-8")
    case("the probe's writer begins its run record with another first line",
         {M.PROBE_WRITER: probe_text.replace('fprintf(out, "{\\n \\"va\\"',
                                             'fprintf(out, "{\\n \\"probe\\": 1, \\"va\\"', 1).encode()},
         f"{M.PROBE_WRITER} begins its run record with")
    case("the probe's device table no longer writes each method's calls",
         {M.PROBE_WRITER: probe_text.replace('\\"calls\\": %u, \\"flags\\": ',
                                             '\\"count\\": %u, \\"flags\\": ', 1).encode()},
         "the table rows' interface, method and calls columns")

    # The source against the numerator, with every hash refreshed by hand.
    d3d9_text = (ROOT / M.D3D9_C).read_text(encoding="utf-8")
    rows = M.parse_device_rows(d3d9_text)
    refused = next(((i, s, r) for i, slots in sorted(rows.items()) for s, r in sorted(slots.items())
                    if not r["implemented"]), None)
    if refused is not None:
        iface, slot, row = refused
        needle = f'"{row["method"]}", NULL}}'
        if needle in d3d9_text:
            # The first refused row of the first table that refuses one gains a handler.
            mutated = d3d9_text.replace(needle, f'"{row["method"]}", m_zero}}', 1)
            case("a refused method was implemented since, and every digest refreshed",
                 refreshed(mutated.encode(), M.D3D9_C, receipt, status, reach),
                 "says implemented")
    imports_text = (ROOT / M.IMPORTS_C).read_text(encoding="utf-8")
    m = M.IMPORT_ROW.search(imports_text)
    if m:
        mutated = imports_text[:m.start(4)] + "NULL" + imports_text[m.end(4):]
        case("a d3dx9 entry lost its handler, and every digest refreshed",
             refreshed(mutated.encode(), M.IMPORTS_C, receipt, status, reach),
             "sa2_imports.c")
    renamed = d3d9_text.replace('"SetRenderState"', '"SetRenderStatus"', 1)
    if renamed != d3d9_text:
        case("a slot was renamed in the layout", {M.D3D9_C: renamed.encode()}, "layout")
    case("reach.run_json points elsewhere",
         {M.REACH: doc(edit(reach, lambda d: d["reach"].__setitem__(
             "run_json", "private/generated/s4/run/d3d9break.control.json")))},
         "not the reach run")
    case("a name was added to implemented",
         {M.STATUS_OUT: doc(edit(flat, lambda d: d["implemented"].append(
             "IDirect3DDevice9::NotAMethod")))}, M.STATUS_OUT)
    srows = supplement.get("rows") or []
    if srows:
        case("a census row names a method the device table does not",
             {M.SUPPLEMENT: doc(edit(supplement, lambda d: d["rows"][0].__setitem__(
                 "method", "SetVertexShaderConstantQ")))}, "no such method")
        case("a census row's displacement is one slot off",
             {M.SUPPLEMENT: doc(edit(supplement, lambda d: d["rows"][-1].__setitem__(
                 "displacement", hex(int(d["rows"][-1]["displacement"], 16) + 4))))},
             "displacement")
        case("a census row's register is overwritten before the site",
             {M.SUPPLEMENT: doc(edit(supplement, lambda d: d["rows"][0].__setitem__(
                 "load_va", "0x00866F21")))}, "writes the loaded register")
        case("a census row lies outside the renderer module",
             {M.SUPPLEMENT: doc(edit(supplement, lambda d: d["rows"][0].update(
                 va="0x00401010", load_va="0x00401000")))}, "not inside the renderer module")
        # Widened to take in a row moved out of the renderer: the row is still held to the
        # gate's module, not the one the file declares.
        case("the census supplement widened its renderer module around a row outside it",
             {M.SUPPLEMENT: doc(edit(supplement, lambda d: (
                 d.__setitem__("renderer_module", {"start": "0x00401000", "end": "0x00900000"}),
                 d["rows"][0].update(va="0x00401010", load_va="0x00401000"))))},
             f"not inside the renderer module {RENDERER_MODULE[0]}..{RENDERER_MODULE[1]}")
    RAN = len(cases)
    CASES = [c[0] for c in cases]

    survived = []
    with tempfile.TemporaryDirectory() as td:
        for k, (name, changes, expect, head, disk, census) in enumerate(cases):
            found = problems(overlay(ROOT, Path(td) / f"case{k}", changes), head=head, disk=disk,
                             census=census)
            if not found or not any(expect in p for p in found):
                survived.append(name if found else f"{name} (no problem at all)")
    survived += discovery_falsifications()
    RAN += len(DISCOVERY_CASES)
    CASES += list(DISCOVERY_CASES)
    return [f"falsification survived: {s} -- the gate accepted M7 inputs it should have called "
            "stale" for s in survived]


# The bounded discovery's own falsifications: on a small tree of its own, the run index is built,
# then each way a guest run reaches the disk is made, and the next update must find it. A run the
# update did not find is a census that could miss a game run silently.
DISCOVERY_CASES = (
    "a run written into a directory the index holds",
    "a run written into a directory the index has never seen, three levels down",
    "a JSON file the index holds rewritten in place as a run, its directory unchanged",
    "a checkout's .git removed, so its runs become the tree's",
    "a run written into a directory the index stat'ed in the same clock tick",
    "a run deleted after it was indexed keeps its names",
)


def _plant(path: Path, method: str) -> None:
    """A guest run record whose table counts a call of `method` on a row the device does not
    implement: what a new method looks like (the closed world counts a trapped call), so a
    census that read implemented rows alone would find none of these."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"interface": "IDirect3DDevice9", "method": method, "slot": 0, "argc": 0,
             "implemented": False, "calls": 1, "flags": ""},
            {"interface": "IDirect3DDevice9", "method": "QueryInterface", "slot": 1, "argc": 2,
             "implemented": True, "calls": 0, "flags": ""}]
    path.write_text(M.PROBE_HEADER.decode() + '00000000", "winmain": {}, "d3d9": {"implemented": '
                    + json.dumps(rows) + "}}\n")


def discovery_falsifications() -> list[str]:
    """Each DISCOVERY_CASES case that the census's update does not find, by name."""
    survived = []
    old = 1_000_000_000

    def age(*paths: Path) -> None:
        for path in paths:
            os.utime(path, ns=(old, old))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # A `.git` at the top first: wherever TMPDIR is, a census of the real tree treats this
        # small tree as a checkout and never counts the runs planted in it.
        (root / ".git").mkdir()
        g = root / "private" / "generated"
        run = g / "s4" / "run"
        _plant(run / "first.json", "Present")
        (run / "notes.json").write_text('{"not": "a run"}\n')
        _plant(g / "s5" / "clone" / "private" / "generated" / "x" / "run.json", "SetFVF")
        (g / "s5" / "clone" / ".git").mkdir(parents=True)

        def update() -> "M.Census":
            return M.update_index(root, by="check_m7_inputs.py falsification")

        def found(c: "M.Census", rel: str, method: str) -> bool:
            return f"IDirect3DDevice9::{method}" in c.device_runs.get(f"private/generated/{rel}", set())
        try:
            age(run / "first.json", run / "notes.json", run, g / "s5" / "clone", g / "s4", g / "s5", g)
            M.update_index(root, build=True, by="check_m7_inputs.py falsification")
            # a new file in a directory the index holds, unchanged since it was stat'ed
            _plant(run / "second.json", "Clear")
            if not found(update(), "s4/run/second.json", "Clear"):
                survived.append(DISCOVERY_CASES[0])
            # a directory three levels below one the index holds
            _plant(g / "s4" / "a" / "b" / "c" / "run.json", "Reset")
            if not found(update(), "s4/a/b/c/run.json", "Reset"):
                survived.append(DISCOVERY_CASES[1])
            # a file rewritten in place, its directory's mtime the one the index recorded
            age(run)
            update()
            _plant(run / "notes.json", "EndScene")
            age(run)
            if not found(update(), "s4/run/notes.json", "EndScene"):
                survived.append(DISCOVERY_CASES[2])
            # a checkout whose .git is removed
            (g / "s5" / "clone" / ".git").rmdir()
            if not found(update(), "s5/clone/private/generated/x/run.json", "SetFVF"):
                survived.append(DISCOVERY_CASES[3])
            # a run landing in a directory within the clock tick it was stat'ed in: its mtime
            # is put back to the value the update read
            tick = time.time_ns()
            os.utime(run, ns=(tick, tick))
            update()
            _plant(run / "tick.json", "BeginScene")
            os.utime(run, ns=(tick, tick))
            if not found(update(), "s4/run/tick.json", "BeginScene"):
                survived.append(DISCOVERY_CASES[4])
            # a run deleted after it was indexed: gone, and its names kept
            (run / "first.json").unlink()
            c = update()
            if "private/generated/s4/run/first.json" in c.device_runs or \
                    c.indexed.get("private/generated/s4/run/first.json") != {"IDirect3DDevice9::Present"}:
                survived.append(DISCOVERY_CASES[5])
        except (M.Refused, OSError) as e:
            survived.append(f"the census's own falsifications could not run ({e})")
    return survived


def three_way_cases(receipt: dict, case, doc, edit) -> None:
    """The three-way numerator's falsifications. Each changes the raw evidence and refreshes,
    by hand, every digest a reader would refresh (the evidence receipt's pin of the changed
    file, and M7's receipt's pin of the evidence receipt), so that only the derivation from
    the raw files can see it. Built when the tree's receipt records the evidence it was
    regenerated from, and, past the first, when that evidence reads measured on disk: present,
    every pinned file as pinned, and made from the tree's sources (load_three_way). Evidence
    that does not is problems()'s to name ("derives measured False"), so none of its files is
    read here and a gate run over deleted evidence reports instead of raising. The same holds
    for one synthetic case: a case whose own files are not the ones pinned (load_case's
    problems, which leave the block measured and the case not held) is never the one a
    falsification is built from, so problems() names it ("the receipt holds ..., which its
    evidence does not"). A read that fails anyway is named as a falsification that could not
    be built, never a traceback."""
    ins = (receipt.get("inputs") or {}).get("three_way")
    if not isinstance(ins, dict):
        return
    case("an evidence receipt changed after the regeneration",
         {M.RECEIPT: doc(edit(receipt, lambda d: d["inputs"]["three_way"]["production"].__setitem__(
             "sha256", ZERO)))}, "inputs.three_way.production")
    three = receipt.get("three_way_numerator") or {}
    if three.get("measured") is not True:
        return
    ev = M.load_three_way(ROOT)
    if ev["production"]["problems"] or ev["synthetic"]["problems"] or M.stale_pins(ev):
        return
    try:
        _three_way_cases(receipt, three, ev, case, doc)
    except (OSError, ValueError, KeyError, TypeError) as e:
        case(f"the three-way evidence could not be read to build its falsifications ({e})", {},
             "the three-way evidence could not be read")


def _three_way_cases(receipt: dict, three: dict, ev: dict, case, doc) -> None:
    prod, syn = ev["production"]["receipt"], ev["synthetic"]["receipt"]

    def repinned(prod_doc: dict | None = None, syn_doc: dict | None = None) -> dict:
        """The evidence receipts given, and M7's receipt pinned to them."""
        out, rc = {}, copy.deepcopy(receipt)
        for which, rel, d in (("production", M.PRODUCTION, prod_doc), ("synthetic", M.SYNTHETIC, syn_doc)):
            if d is not None:
                out[rel] = doc(d)
                rc["inputs"]["three_way"][which]["sha256"] = M.sha256_bytes(out[rel])
        out[M.RECEIPT] = doc(rc)
        return out

    by = three.get("held_by") or {}
    targets = {e.get("target") for e in syn.get("cases") or []}
    # A host's report lost the row of a method the recording holds (one with no synthetic case,
    # so that only the production derivation decides it).
    held_prod = by.get("production") or []
    runs = ev["production"].get("runs") or [ev["production"]["run"]]
    rows = M.recorder_rows(runs[0].get("counts"))
    name = next((n for n in held_prod if n in rows and n not in targets), None)
    if name is not None:
        # The row goes from the dxvk report of every production run whose recorder counted the
        # method, since any one of them holding it holds it.
        p = copy.deepcopy(prod)
        blocks = p["runs"] if isinstance(p.get("runs"), list) else [p]
        changes: dict = {}
        for block, run in zip(blocks, runs):
            mine = M.recorder_rows(run.get("counts"))
            if name not in mine:
                continue
            hdir, _ = M.host_pins({"dir": (block.get("recording") or {}).get("dir"), "hosts": block.get("hosts")},
                                  "dxvk")
            rel = f"{hdir}/report.json"
            report = json.loads((ROOT / rel).read_text())
            kind, n, _ = mine[name]["key"]
            report["methods"] = [m for m in report.get("methods") or []
                                 if not (m.get("type") == kind and (m.get("entry") if kind == M.D3DX else m.get("slot")) == n)]
            changes[rel] = (json.dumps(report, indent=1) + "\n").encode()
            h = block["hosts"]["dxvk"]
            (h["files"] if "files" in h else h)["report.json"] = M.sha256_bytes(changes[rel])
        case("a host's report lost the row of a method the recording holds",
             {**changes, **repinned(prod_doc=p)}, f"the receipt holds {name}")
    # A falsifier that passes: a synthetic omit run made of its positive run's files, from a
    # case the receipt holds whose files are the ones pinned (a case that is not is problems()'s).
    held_syn = by.get("synthetic") or []
    loaded = ev["synthetic"]["cases"]
    entry = next((e for e in syn.get("cases") or [] if e.get("target") in held_syn
                  and isinstance(loaded.get(e.get("target")), dict) and not loaded[e["target"]]["problems"]), None)
    if entry is not None:
        changes: dict = {}
        s = copy.deepcopy(syn)
        e = next(x for x in s["cases"] if x["target"] == entry["target"])
        pos, omit = entry["positive"], entry["omit"]
        for name_ in ("stream.sa2s", "rec.counts.json", "census.json", "harness.exit"):
            changes[f"{omit['dir']}/{name_}"] = (ROOT / pos["dir"] / name_).read_bytes()
            e["omit"]["files"][name_] = pos["files"][name_]
        for h in M.HOSTS:
            pdir, pfiles = M.host_pins(pos, h)
            odir, _ = M.host_pins(omit, h)
            ohost = e["omit"].setdefault("hosts", {}).setdefault(h, {"dir": odir})
            ohost.setdefault("files", {})
            for name_, sha in pfiles.items():
                changes[f"{odir}/{name_}"] = (ROOT / pdir / name_).read_bytes()
                ohost["files"][name_] = sha
        case("a synthetic omit run passes the predicate its positive run passes",
             {**changes, **repinned(syn_doc=s)}, f"the receipt holds {entry['target']}")
        # A synthetic case directory holding a counts.json is a recorded run of the game.
        case("a synthetic case directory holds a counts.json",
             {f"{pos['dir']}/counts.json": (ROOT / pos["dir"] / "rec.counts.json").read_bytes()},
             "does not count")
    # A pin of the production evidence changed.
    p = copy.deepcopy(prod)
    (p["runs"][-1] if isinstance(p.get("runs"), list) else p)["recording"]["files"]["counts.json"] = ZERO
    case("a pin of the production evidence changed", repinned(prod_doc=p), "derives measured False")
    # A pin dropped: the synthetic receipt no longer pins a source the fixture was built from,
    # so no change to that source could ever make it stale.
    s = copy.deepcopy(syn)
    s["harness"]["sources"].pop("runtime/sa2_d3d9.c", None)
    case("the synthetic evidence dropped the pin of a source the fixture was built from",
         repinned(syn_doc=s), "derives measured False")
    # A source the evidence was made from moved in the tree: one both players were built from,
    # and one only the fixture was.
    for rel, label in (("host/player/player.cpp", "a source both players were built from moved"),
                       ("tools/s5/tests/rec_harness_m7.c", "a source the synthetic fixture was built from moved")):
        if (ROOT / rel).is_file():
            case(label, {rel: (ROOT / rel).read_bytes() + b"\n// moved\n"}, "derives measured False")


def refreshed(source: bytes, rel: str, receipt: dict, status: dict, reach: dict) -> dict:
    """Changes that replace `rel` with `source` and refresh, by hand, every digest the gate
    holds the runtime to, so that only the source-against-numerator comparison can see it."""
    import reach_receipt  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        view = overlay(ROOT, Path(td) / "v", {rel: source})
        digest = reach_receipt.runtime_digest(view)
        d3d9_sha = M.sha256_file(view / M.D3D9_C)
    st = copy.deepcopy(status)
    st["inputs"]["run_runtime_sha256"] = digest
    st["inputs"]["d3d9_c_at_write"]["sha256"] = d3d9_sha
    st_bytes = (json.dumps(st, indent=1) + "\n").encode()
    rc = copy.deepcopy(receipt)
    rc["inputs"]["numerator"]["runtime_digest"] = digest
    rc["inputs"]["numerator"]["d3d9_c_sha256"] = d3d9_sha
    rc["inputs"]["numerator"]["device_status"]["sha256"] = M.sha256_bytes(st_bytes)
    rr = copy.deepcopy(reach)
    rr["manifest"]["runtime_sha256"] = digest
    return {rel: source, M.DEVICE_STATUS: st_bytes,
            M.RECEIPT: (json.dumps(rc, indent=1, sort_keys=True) + "\n").encode(),
            M.REACH: (json.dumps(rr, indent=1) + "\n").encode()}


def main() -> int:
    failed = falsifications()
    if failed:
        for f in failed:
            print(f"  {f}")
        print(f"M7_INPUTS_FAILED falsifications={len(failed)}")
        return 1
    try:
        census = M.update_index(ROOT, by="check_m7_inputs.py", lock_wait=M.GATE_LOCK_WAIT_SECONDS,
                                write=not M.probe_control_problems(ROOT))
    except M.Refused as e:
        print(f"  {e}")
        print("M7_INPUTS_FAILED problems=1")
        return 1
    disk = M.disk_census(ROOT, census)
    found = problems(disk=disk, census=census)
    if found:
        for p in found:
            print(f"  {p}")
        print(f"M7_INPUTS_FAILED problems={len(found)}")
        return 1
    receipt = json.loads((ROOT / M.RECEIPT).read_text())
    flat = json.loads((ROOT / M.STATUS_OUT).read_text())
    registry = json.loads((ROOT / M.REGISTRY).read_text())
    called = set(receipt["called"])
    three_way = receipt.get("three_way_numerator") or {}
    print(f"M7_INPUTS_OK called={len(called)} implemented={len(called & set(flat['implemented']))} "
          f"runs={len(registry.get('runs') or [])} disk_runs={len(disk)} "
          f"guest_runs={census.stats.get('runs_on_disk')} guest_runs_indexed={census.stats.get('runs_indexed')} "
          f"index_seconds={census.stats.get('seconds')} "
          f"missed={len(receipt['called_by_the_run_but_missed_by_the_scan'])} "
          f"ceiling={receipt['unattributed_that_could_still_be_d3d9']} "
          f"ceiling_sites={(receipt.get('ceiling_sites') or {}).get('count')} "
          f"three_way={'measured held=%d/%d' % (len(three_way.get('held_methods') or []), len(called)) if three_way.get('measured') is True else 'not-measured'} "
          f"stale=0 falsifications={RAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
