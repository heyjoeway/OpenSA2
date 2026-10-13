#!/usr/bin/env python3
"""The guest-poll rule's acceptance, and the NEW FILE run that keeps it honest.

Subtitle: the runs the rule must leave alone -- both registered replays, the
reach run and the stage boot -- copied as they stood before the rule
(`baseline`), re-run on the build that carries it under build names of this
tool's own, and held to the copies: every kept file byte for byte, every
word-log tick hash (with a one-tick-shift control), and the run record byte for
byte once the rule's one line is taken out and the copy's write overlay put
back. Then the NEW FILE script on the boot's configuration, once with the rule
and once with its break (SA2_POLL_HANDOFF=0). `receipt` writes the acceptance
receipt from the six results. `regression` runs the NEW FILE script with the
rule again and writes Documentation/receipts/s5-newfile-wait-v1.json, which
Documentation/scripts/check_poll_handoff.py holds to the tree on every push.

Why the word logs, and not the gates
------------------------------------
The rule was accepted as conformance (design 3.5, "the same sync semantics") on
the condition that it fires at no poll of the registered runs and moves nothing
there (the 2026-10-02 review, S1 and S2b). The gates judge masked readings:
under the experiment's `always` rule the boot's tick capture and every masked
column stayed equal while every one of its word-log hashes moved
(agents-journals/s5/2026-10-02-newfile-hang.md). So the acceptance evidence is
this comparison of every word of `.dataa` at every tick, not run_all_gates.py.
`word_log_compare` also runs the one-tick-shift control that
tools/s4/wordlog.py runs on a capture: the same comparison a tick off must
differ, or it could not have seen the logs shifted a tick.

Why a baseline copy
-------------------
Re-registering the replays on the rule's build (tools/s4/tick_replay.py, and the
reach and stage-boot owners) rewrites the files these runs are compared with. A
comparison made after that is the fixed build against itself: a determinism
check, not the neutrality the risk register cites (the 2026-10-02 instrument
review, S3). So the files are copied first (`baseline`), each held to the pin
its receipt records, and every comparison reads the copies. `baseline` refuses a
registered record that already carries the rule's counters, and `receipt`
refuses a result compared with any baseline but the one it names. The order at
the fix's commit: apply the fix, `baseline` (once, before anything
re-registers), the six runs, `receipt`, and only then the owners re-register.

What the record comparison allows
---------------------------------
harness/crt_probe.c prints `threads.poll_handoff` in every run that polls, so a
re-run's record has that one line more than the pre-rule copy, and its write
overlay (`files.write_root`) is this tool's own. With that line taken out and
the copy's overlay path put back, the record must hash to the copy's hash, which
is the registered pin where a receipt records one (Metal Harbor's
run_json_sha256, the stage boot's run_sha256) (`record_bytes`); and leaf for
leaf nothing else may differ (`record_compare`).

What "reaches the main menu" is read from
-----------------------------------------
The game mode stays 0x0E from the title to the main menu, so the stage trace
cannot say which screen is up. The word log can: CurrentMenu at 0x01D7BB10
(the Mod Loader's
private/predecessor-tools/sa2-mod-loader/SA2ModLoader/include/SA2Variables.h)
holds Menus_FileSelect (9) on the file select and Menus_Main (1) on the main
menu (SA2Enums.h beside it), and the experiment's frames showed the main menu
from the tick it reads 1. A run reaches the main menu when the word reads 1 at
some tick; the hang is the break's run, which never does.

Usage (one game run at a time; every run builds unless --no-build):
  source tools/env.sh
  .venv/bin/python tools/s5/poll_handoff_check.py baseline
  .venv/bin/python tools/s5/poll_handoff_check.py run boot
  .venv/bin/python tools/s5/poll_handoff_check.py run metalharbor
  .venv/bin/python tools/s5/poll_handoff_check.py run reach
  .venv/bin/python tools/s5/poll_handoff_check.py run stage-boot
  .venv/bin/python tools/s5/poll_handoff_check.py run newfile --no-build
  .venv/bin/python tools/s5/poll_handoff_check.py run newfile-break --no-build
  .venv/bin/python tools/s5/poll_handoff_check.py receipt \\
      --out Documentation/receipts/s5-poll-handoff-v1.json
  .venv/bin/python tools/s5/poll_handoff_check.py regression --write

`baseline` copies into --baseline (default OUT/baseline) and writes its
baseline.json. Each run writes OUT/<run>/ (--out-dir moves OUT): the run record,
the matrix row, the files it is held to, and result.json, its comparison and
verdict. The boot and both NEW FILE runs share one build (the tick hook and its
wrap, as the registered boot's), so the NEW FILE runs may take --no-build after
`boot`. `receipt` reads the six results, holds each to the tree as it is now
(every input's sha256) and to the one baseline, and exits 1 unless every verdict
holds. `regression` writes its run under OUT/regression/<time>/ and, with
--write, the receipt; it exits 1 unless the run reaches the main menu through
the rule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import device_profile as DP  # noqa: E402
import reach_receipt as RR  # noqa: E402
import rebuild_image as RI  # noqa: E402
import save_state as SS  # noqa: E402
import tick_replay as TR  # noqa: E402
import wordlog as WL  # noqa: E402

OUT = ROOT / "private" / "generated" / "s5" / "poll-handoff"
RUNDIR = TR.RUNDIR
LINKDIR = ROOT / "private" / "generated" / "s4" / "link"
STAGE_BOOT_RECEIPT = ROOT / "Documentation" / "receipts" / "s4-stage-boot-v1.json"
REGRESSION_RECEIPT = ROOT / "Documentation" / "receipts" / "s5-newfile-wait-v1.json"
BOOT_BUILD = f"pollfix-replay.{RR.TAG}"
STAGE_BUILD = "pollfix-metalharbor"
REACH_BUILD = "pollfix-reach"
STAGE_BOOT_BUILD = "pollfix-stageboot"
STAGE_BOOT_STAGE = "metalharbor"
RUNS = ("boot", "metalharbor", "reach", "stage-boot", "newfile", "newfile-break")
# The runs the rule must leave alone, and the records each stands for as
# Documentation/scripts/check_poll_handoff.py names them: the gate holds every
# one of those records to no hand-off.
SILENT = {"boot": "replay:boot", "metalharbor": "replay:metalharbor", "reach": "reach",
          "stage-boot": f"stage-boot:{STAGE_BOOT_STAGE}"}
# The rule's own settings, never inherited from a shell: a stray SA2_POLL_HANDOFF=0
# would turn the rule off in a run meant to show it on.
RULE_VARS = ("SA2_POLL_HANDOFF", "SA2_THREADS_BREAK")

# The NEW FILE script (runtime/sa2_dinput.h SA2_INPUT_DI_SCRIPT): Enter skips the
# opening event, opens the file select at the title, and confirms NEW FILE.
NEWFILE_SCRIPT = ("# NEW FILE: Enter skips the opening event (600), opens file select at the "
                  "title (1000), confirms NEW FILE (1300)\n"
                  "600 605 0x1C\n1000 1005 0x1C\n1300 1305 0x1C\n")
NEWFILE_POLLS = 2100
NEWFILE_ALARM = {"newfile": 600, "newfile-break": 150}
CURRENT_MENU = 0x01D7BB10          # SA2Variables.h CurrentMenu
MENUS_MAIN, MENUS_FILE_SELECT = 1, 9
ASYNDO_START = "0x008653d0"        # the AsynDo thread's start routine

# A word log's fields that are the host's clock or the reader's own path, never the
# guest's (tools/s4/wordlog.py info).
LOG_CLOCK = ("clk_init", "clk_end", "clock_hz", "ref_init", "ref_end", "ref_hz", "path")
# The run record's leaves a re-run may differ in: its own write overlay.
ALLOWED_LEAVES = (("files", "write_root"),)
POLL_LEAVES = ("threads", "poll_handoff")
# The rule's one line in a run record (harness/crt_probe.c), and the overlay's path.
POLL_LINE = b'"poll_handoff": {'
WRITE_ROOT = re.compile(rb'"write_root": "((?:[^"\\]|\\.)*)"')

# The registered files each baseline run copies, and where its pin is read from.
BASELINE_SCHEMA = "sa2-poll-handoff-baseline/v1"
SOURCES = ("Documentation/replays.json", "Documentation/receipts/s4-tick-replay-v1.json",
           "Documentation/receipts/s4-run-reach-v1.json",
           "Documentation/receipts/s4-stage-boot-v1.json")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p: Path) -> str:
    p = Path(p)
    return str(p.relative_to(ROOT)) if p.is_absolute() and p.is_relative_to(ROOT) else str(p)


def entry_of(name: str) -> dict:
    """Registered replay `name`'s registry entry (Documentation/replays.json)."""
    reg = json.loads(TR.REGISTRY.read_text())
    return next(e for e in reg["replays"] if e["name"] == name)


def inputs_list() -> list[str]:
    """What the runs are made from, pinned: tick_replay's own lists for the boot's and
    Metal Harbor's lifted runs (the hooks Metal Harbor links among them) and for the
    word-log reader the compared hashes come from (the 2026-10-02 instrument review,
    N2), the registry the runs are configured from, and this tool."""
    out: list[str] = []
    for name in ("boot", "metalharbor"):
        out += TR.lifted_pipeline(entry_of(name))
    out += TR.WORD_COMPARE + ["tools/s4/tick_replay.py", "tools/s4/stage_run.py",
                              "Documentation/replays.json", "tools/s5/poll_handoff_check.py"]
    return list(dict.fromkeys(out))


def tree_inputs() -> dict:
    return {"pipeline": {f: sha256(ROOT / f) for f in inputs_list()},
            "runtime_sha256": TR.runtime_digest()}


def runtime_pins() -> dict[str, str]:
    """Each runtime source the runtime digest is made from (tools/s4/tick_replay.py
    RUNTIME_GLOBS), pinned by path. Documentation/scripts/check_receipt_pins.py reads
    a receipt's `inputs.pipeline`, not the digest beside it, so without these an edit
    to the rule's runtime (runtime/sa2_threads.c, sa2_threads.h, sa2_poll_handoff.c)
    would leave the acceptance naming a runtime no commit has, with every gate
    passing (the 2026-10-02 confirm review's SHOULD-FIX). The runs' own inputs carry
    the digest, which `receipt` holds to the tree's."""
    return {str(p.relative_to(ROOT)): sha256(p)
            for pattern in TR.RUNTIME_GLOBS for p in sorted(ROOT.glob(pattern))}


def provenance() -> dict:
    """The commit the tree stands on and whether it differs from it: the runs of
    2026-10-02 ran on a copy of an uncommitted tree, and `commit` alone said
    otherwise (the 2026-10-02 instrument review, N7). `checkout` is whether ROOT is
    the repository's own working tree rather than a copy inside it."""
    def git(*args: str) -> str | None:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None
    top = git("rev-parse", "--show-toplevel")
    return {"commit": git("rev-parse", "HEAD") or "",
            "uncommitted_changes": bool(git("status", "--porcelain")) if top else None,
            "checkout": bool(top) and Path(top).resolve() == ROOT.resolve()}


# ------------------------------------------------------------------ the record

def leaves(obj, prefix=()):
    if isinstance(obj, dict) and obj:
        for k, v in obj.items():
            yield from leaves(v, prefix + (k,))
    elif isinstance(obj, list) and obj:
        for i, v in enumerate(obj):
            yield from leaves(v, prefix + (i,))
    else:
        yield prefix, obj


def leaf_name(path: tuple) -> str:
    return "".join(f"[{k}]" if isinstance(k, int) else (f".{k}" if i else str(k))
                   for i, k in enumerate(path))


def record_compare(mine: dict, theirs: dict) -> dict:
    """The run's whole record against the registered run's, leaf for leaf (equal in
    type and value), but for the write overlay and, while the registered record
    predates the rule, the rule's own counters, which only the re-run has."""
    a, b = dict(leaves(mine)), dict(leaves(theirs))
    registered_has_rule = any(k[:2] == POLL_LEAVES for k in b)
    differing = sorted((k for k in set(a) | set(b)
                        if k not in a or k not in b
                        or not (type(a[k]) is type(b[k]) and a[k] == b[k])), key=leaf_name)
    allowed = [k for k in differing if k in ALLOWED_LEAVES
               or (not registered_has_rule and k[:2] == POLL_LEAVES and k not in b)]
    bad = [k for k in differing if k not in allowed]
    return {"leaves": len(b), "allowed": [leaf_name(k) for k in allowed],
            "differing": [leaf_name(k) for k in bad[:20]], "differing_count": len(bad),
            "first": {leaf_name(k): [a.get(k), b.get(k)] for k in bad[:5]},
            "registered_has_rule": registered_has_rule, "ok": not bad}


def record_bytes(mine: bytes, theirs: bytes) -> dict:
    """The run's record as bytes against the registered record's: the rule's one
    line taken out (there must be exactly one) and the registered write overlay put
    back in place of the run's own; what is left must hash to the registered
    record's hash. This is the form the registered pins are in, so it shows the
    rule's line is the only change the rule made to the record (the 2026-10-02
    instrument review, S2)."""
    problems = []
    lines = mine.split(b"\n")
    rule = [i for i, line in enumerate(lines) if line.lstrip().startswith(POLL_LINE)]
    if len(rule) != 1:
        problems.append(f"the record has {len(rule)} lines of the rule's counters, not one")
    stripped = b"\n".join(line for i, line in enumerate(lines) if i not in rule)
    ours, reg = WRITE_ROOT.findall(stripped), WRITE_ROOT.findall(theirs)
    if len(ours) != 1 or len(reg) != 1:
        problems.append(f"files.write_root appears {len(ours)} times in the run's record and "
                        f"{len(reg)} in the registered one, not once each")
    else:
        stripped = WRITE_ROOT.sub(lambda m: b'"write_root": "' + reg[0] + b'"', stripped,
                                  count=1)
    got, want = hashlib.sha256(stripped).hexdigest(), hashlib.sha256(theirs).hexdigest()
    if not problems and got != want:
        problems.append("without the rule's line and with the registered overlay path, the "
                        "record does not hash to the registered record's")
    return {"rule_lines": len(rule), "sha256_without_rule": got, "registered_sha256": want,
            "problems": problems, "equal": not problems}


def poll_record(rec: dict) -> dict | None:
    return (rec.get("threads") or {}).get("poll_handoff")


def asyndo_threads(rec: dict) -> dict:
    """The AsynDo threads' rows: how many, and which were left READY with no slice."""
    rows = [r for r in (rec.get("threads") or {}).get("rows") or []
            if str(r.get("start_va", "")).lower() == ASYNDO_START]
    starved = [r.get("index") for r in rows if r.get("state") == "ready" and not r.get("slices")]
    return {"count": len(rows), "finished": sum(r.get("state") == "finished" for r in rows),
            "ready_with_no_slice": starved}


# ------------------------------------------------------------------ the word log

def word_log_compare(mine: Path | None, theirs: Path) -> dict:
    """Every tick's words by their hash (tools/s4/wordlog.py `hashes`: FNV-1a over
    the whole section the log rebuilds at that tick, the keyframe as tick 0), the
    log's own fields but the host's clock and its path, and the one-tick-shift
    control: the run's hashes against the registered ones a tick off must differ
    at some tick each way, or this comparison could not have seen the two logs
    shifted a tick. The word-log files never match byte for byte, since they hold
    the host's clock readings."""
    a = WL.hashes(mine) if mine else {}
    b = WL.hashes(theirs)
    differing = sorted(t for t in set(a) | set(b) if a.get(t) != b.get(t))
    shifted = {d: sum(1 for t in a if t + d in b and a[t] != b[t + d]) for d in (-1, 1)}
    ia = {k: v for k, v in (WL.info(mine) if mine else {}).items() if k not in LOG_CLOCK}
    ib = {k: v for k, v in WL.info(theirs).items() if k not in LOG_CLOCK}
    info_differing = sorted(k for k in set(ia) | set(ib) if ia.get(k) != ib.get(k))
    problems = []
    if not a:
        problems.append("the run wrote no word log")
    elif differing or len(a) != len(b):
        problems.append(f"the word log differs from the registered run's at {len(differing)} "
                        "ticks")
    if a and (not shifted[-1] or not shifted[1]):
        problems.append("the tick hashes are equal a tick off, so this comparison could not "
                        "have seen the two logs shifted a tick")
    if info_differing:
        problems.append(f"the word log's own fields differ: {info_differing}")
    return {"ticks_mine": len(a), "ticks_registered": len(b), "ticks_differing": len(differing),
            "first_differing": differing[:5],
            "shift_control_ticks_differing": {"registered_tick_before": shifted[-1],
                                              "registered_tick_after": shifted[1]},
            "fields_compared": len(ib), "fields_differing": info_differing,
            "problems": problems, "ok": not problems}


# ------------------------------------------------------------------ the baseline

def baseline_sources() -> dict[str, dict[str, tuple[str, str | None, str]]]:
    """For each run the rule must leave alone, each registered file it is held to:
    (path, the pin its receipt records or None, where that pin is read)."""
    tick = json.loads(TR.RECEIPT.read_text())
    reach = json.loads(RR.OUT.read_text())["reach"]
    stage = next(s for s in json.loads(STAGE_BOOT_RECEIPT.read_text())["stages"]
                 if s.get("stage") == STAGE_BOOT_STAGE)
    lifted = {r["name"]: r["lifted"] for r in tick["replays"]}
    t, b, m = "s4-tick-replay-v1.json", lifted["boot"], lifted["metalharbor"]
    out: dict[str, dict[str, tuple[str, str | None, str]]] = {
        "boot": {"tick capture": (b["capture"], b["sha256"], f"{t} boot lifted.sha256"),
                 "word log": (b["words"]["path"], b["words"]["sha256"],
                              f"{t} boot lifted.words.sha256"),
                 "run record": (b["run_json"], b.get("run_json_sha256"),
                                f"{t} boot lifted.run_json_sha256" if b.get("run_json_sha256")
                                else f"{t} pins no boot run record; its reach and, once "
                                     "registered with the rule, its counters are held")},
        "metalharbor": {"tick capture": (m["capture"], m["sha256"],
                                         f"{t} metalharbor lifted.sha256"),
                        "word log": (m["words"]["path"], m["words"]["sha256"],
                                     f"{t} metalharbor lifted.words.sha256"),
                        "run record": (m["run_json"], m["run_json_sha256"],
                                       f"{t} metalharbor lifted.run_json_sha256")},
        "reach": {"import table": (reach["import_table"]["path"], reach["import_table"]["sha256"],
                                   "s4-run-reach-v1.json reach.import_table.sha256"),
                  "run record": (reach["run_json"], None,
                                 "s4-run-reach-v1.json pins no record; its reach fields are "
                                 "held instead")},
        "stage-boot": {"tick capture": (stage["ticks_capture"], stage["ticks_sha256"],
                                        "s4-stage-boot-v1.json stages[metalharbor].ticks_sha256"),
                       "stage trace": (stage["trace"], stage["trace_sha256"],
                                       "s4-stage-boot-v1.json stages[metalharbor].trace_sha256"),
                       "run record": (stage["run"], stage["run_sha256"],
                                      "s4-stage-boot-v1.json stages[metalharbor].run_sha256")},
    }
    for key, what in (("stage_trace", "stage trace"), ("goal_latch", "goal latch"),
                      ("input_applied", "input applied"), ("import_table", "import table")):
        out["metalharbor"][what] = (m[key]["path"], m[key]["sha256"],
                                    f"{t} metalharbor lifted.{key}.sha256")
    return out


def take_baseline(dest: Path) -> dict:
    """Copy every registered file the runs are held to into `dest`, after holding
    each to its pin, and write dest/baseline.json. Refused, with nothing copied,
    when a file is missing or not its pin, or when a run record already carries the
    rule's counters (the registration is then the rule's own, and comparing with it
    would compare the fixed build with itself)."""
    manifest = dest / "baseline.json"
    if manifest.exists():
        raise SystemExit(f"{rel(manifest)} exists: a baseline is taken once, before anything "
                         "re-registers")
    problems: list[str] = []
    runs: dict = {}
    sources = baseline_sources()
    for run, files in sources.items():
        entry: dict = {"files": {}}
        for what, (src, pin, pinned_by) in files.items():
            path = ROOT / src
            if not path.is_file():
                problems.append(f"{run}: the registered {what} {src} is missing")
                continue
            got = sha256(path)
            if pin is not None and got != pin:
                problems.append(f"{run}: the registered {what} {src} is not its pin ({pinned_by})")
            entry["files"][what] = {"from": src, "copy": rel(dest / run / Path(src).name),
                                    "sha256": got, "pin": pin, "pinned_by": pinned_by}
            if what == "run record":
                rec = json.loads(path.read_text())
                entry["has_rule"] = poll_record(rec) is not None
                entry["reach"] = TR.reach_of(rec)
                if entry["has_rule"]:
                    problems.append(f"{run}: the registered run record {src} already carries "
                                    "threads.poll_handoff: that registration is the rule's own")
        runs[run] = entry
    reach = json.loads(RR.OUT.read_text())["reach"]
    want = {k: reach.get(k) for k in TR.REACH_KEYS}
    if runs.get("reach", {}).get("reach") != want:
        problems.append(f"reach: the registered record's reach {runs.get('reach', {}).get('reach')} "
                        f"is not the reach receipt's {want}")
    if problems:
        raise SystemExit("baseline refused, nothing copied:\n  " + "\n  ".join(problems))
    for run, entry in runs.items():
        for what, f in entry["files"].items():
            copy = ROOT / f["copy"]
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / f["from"], copy)
            if sha256(copy) != f["sha256"]:
                raise SystemExit(f"{run}: the copy of {f['from']} does not hash to the original")
    doc = {"schema": BASELINE_SCHEMA, **provenance(),
           "taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "receipts": {s: sha256(ROOT / s) for s in SOURCES},
           "reach_device_profile_of_replay": reach.get("device_profile_of_replay"),
           "runs": runs}
    manifest.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def load_baseline(dest: Path) -> tuple[dict, str]:
    """The baseline's manifest and its sha256, with every copy held to its hash."""
    manifest = dest / "baseline.json"
    if not manifest.is_file():
        raise SystemExit(f"no baseline at {rel(manifest)}: take it first, before anything "
                         "re-registers (poll_handoff_check.py baseline)")
    doc = json.loads(manifest.read_text())
    if doc.get("schema") != BASELINE_SCHEMA:
        raise SystemExit(f"{rel(manifest)} is not a {BASELINE_SCHEMA} manifest")
    bad = [f["copy"] for e in doc["runs"].values() for f in e["files"].values()
           if not (ROOT / f["copy"]).is_file() or sha256(ROOT / f["copy"]) != f["sha256"]]
    if bad:
        raise SystemExit(f"the baseline's copies are missing or changed: {bad}")
    return doc, sha256(manifest)


def baseline_problems(base: dict) -> list[str]:
    """Why a baseline cannot stand for the registration before the rule."""
    return [f"{run}: its baseline record already carries the rule's counters"
            for run, e in base["runs"].items() if e.get("has_rule") is not False]


# ------------------------------------------------------------------ the runs

def clean_env() -> dict:
    return {k: v for k, v in RR.clean_environ().items() if k not in RULE_VARS}


def guards() -> None:
    stale, other = RI.staged_not_rebuilt(), RI.fold_code_problems()
    if stale or other:
        raise SystemExit(f"the link would read objects that are not the rebuild's: "
                         f"{stale[:3]} {other}")


def loop_run(cmd: list, out: Path, prefix: str, label: str, timeout: int) -> dict:
    """Run loop_run's command, then move its run record and matrix into `out`."""
    run_json, matrix = RUNDIR / f"{prefix}.{label}.json", RUNDIR / f"{prefix}.matrix.json"
    for f in (run_json, matrix):
        f.unlink(missing_ok=True)
    t0 = time.time()
    with open(out / "loop_run.log", "w") as log:
        log.write("$ " + " ".join(str(c) for c in cmd) + "\n")
        log.flush()
        rc = subprocess.run([str(c) for c in cmd], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                            env=clean_env(), timeout=timeout).returncode
    seconds = round(time.time() - t0, 1)
    for src, dst in ((run_json, out / "run.json"), (matrix, out / "matrix.json")):
        if src.is_file():
            shutil.move(str(src), str(dst))
    return {"rc": rc, "seconds": seconds}


def keep_words(words: Path) -> Path | None:
    if not words.is_file():
        return None
    kept = words.with_name(words.name + ".zst")
    subprocess.run(["zstd", "-3", "-q", "-f", "--rm", str(words), "-o", str(kept)], check=True)
    return kept


def build_state(build: str) -> dict:
    st = json.loads((LINKDIR / f"main.{build}.json").read_text())
    link = st.get("link") or {}
    return {"name": build, "sha256": link.get("sha256"), "wrapped": link.get("wrapped"),
            "production_wrapped": link.get("production_wrapped"),
            "compile_ok": st.get("compile_ok"), "link_rc": link.get("rc")}


def matrix_sha(out: Path) -> str | None:
    f = out / "matrix.json"
    return json.loads(f.read_text()).get("sha256") if f.is_file() else None


def run_boot(out: Path, build: bool, base: dict, base_sha: str) -> dict:
    """tick_replay.run_lifted's command for the boot, with this tool's build name,
    outputs and write overlay."""
    profile = TR.native_device_profile(entry_of("boot"))
    ticks, words = out / "lifted.csv", out / TR.LIFTED_WORDS
    spec = "|".join(["ticks"] + [f"{k}={v}" for k, v in RR.ENV.items()]
                    + [f"SA2_REPLAY_TICKS={rel(ticks)}", f"SA2_REPLAY_WORDS={rel(words)}"]
                    + [f"{k}={v}" for k, v in RR.profile_env(profile).items()]
                    + [f"SA2_VFS_WRITE_ROOT={rel(out / 'vfsroot')}"])
    prefix = "pollfix-boot"
    cmd = [TR.PY, ROOT / "tools" / "s4" / "loop_run.py", "--tag", RR.TAG, "--main", RR.MAIN,
           "--name", BOOT_BUILD, "--hook", TR.HOOK, "--wrap", TR.WRAP, "--out-prefix", prefix,
           "--run", spec] + [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    if not build:
        cmd.append("--no-build")
    saves = SS.reset_lifted([str(c) for c in cmd], ROOT)
    SS.require_empty(saves)
    ran = loop_run(cmd, out, prefix, "ticks", 3600)
    return held("boot", out, ran, saves, profile, {"tick capture": ticks}, keep_words(words),
                base, base_sha, BOOT_BUILD)


def run_metalharbor(out: Path, build: bool, base: dict, base_sha: str) -> dict:
    """tick_replay.run_stage_lifted's observed run, through stage_run.command, with
    this tool's build name, outputs and write overlay."""
    import stage_run as SR
    entry = entry_of("metalharbor")
    script = ROOT / entry["input"]["script"]
    if sha256(script) != entry["input"]["sha256"]:
        raise SystemExit("the recording does not hash to its pin")
    profile = TR.native_device_profile(entry)
    polls = TR.stage_poll_budget(entry)
    alarm = max(600, polls // 5)
    words = out / TR.LIFTED_WORDS
    extra = {"SA2_STAGE_TRACE": rel(out / "stage-trace.csv"), "SA2_REPLAY_WORDS": rel(words),
             "SA2_VFS_WRITE_ROOT": rel(out / "vfsroot")}
    cmd, _, ticks = SR.command(entry["stage"]["name"], polls, label="ticks", input_script=script,
                               extra_env=extra, timeout=alarm, device_profile=profile,
                               name=STAGE_BUILD, out_dir=out, observe=True)
    cmd += ["--probe-arg=--dump-imports", f"--probe-arg={rel(out / 'imports.table.json')}"]
    if not build:
        cmd.append("--no-build")
    saves = SS.reset_lifted([str(c) for c in cmd], ROOT)
    SS.require_empty(saves)
    ran = loop_run(cmd, out, STAGE_BUILD, "ticks", alarm + 900)
    files = {"tick capture": ticks, "stage trace": out / "stage-trace.csv",
             "goal latch": out / "goal-latch.csv", "input applied": out / "input-applied.csv",
             "import table": out / "imports.table.json"}
    return held("metalharbor", out, ran, saves, profile, files, keep_words(words), base,
                base_sha, STAGE_BUILD)


def run_reach(out: Path, build: bool, base: dict, base_sha: str) -> dict:
    """reach_receipt.run's command on the registered replay's device profile the
    reach receipt was measured on, with this tool's build name, import-table dump
    and write overlay."""
    replay = base.get("reach_device_profile_of_replay")
    profile = TR.native_device_profile(entry_of(replay)) if replay else DP.DEFAULT
    spec = "|".join(["a"] + [f"{k}={v}" for k, v in RR.ENV.items()]
                    + [f"{k}={v}" for k, v in RR.profile_env(profile).items()]
                    + [f"SA2_VFS_WRITE_ROOT={rel(out / 'vfsroot')}"])
    cmd = [TR.PY, ROOT / "tools" / "s4" / "loop_run.py", "--tag", RR.TAG, "--main", RR.MAIN,
           "--name", REACH_BUILD, "--out-prefix", REACH_BUILD, "--run", spec]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--dump-imports", f"--probe-arg={rel(out / 'imports.table.json')}"]
    if not build:
        cmd.append("--no-build")
    saves = SS.reset_lifted([str(c) for c in cmd], ROOT)
    SS.require_empty(saves)
    ran = loop_run(cmd, out, REACH_BUILD, "a", 3600)
    return held("reach", out, ran, saves, profile, {"import table": out / "imports.table.json"},
                None, base, base_sha, REACH_BUILD)


def run_stage_boot(out: Path, build: bool, base: dict, base_sha: str) -> dict:
    """tools/s4/stage_boot.py's run of the stage (stage_run.command with the stage
    trace on), with this tool's build name, outputs and write overlay."""
    import stage_run as SR
    trace = out / "stage-trace.csv"
    cmd, _, ticks = SR.command(STAGE_BOOT_STAGE,
                               extra_env={"SA2_STAGE_TRACE": rel(trace),
                                          "SA2_VFS_WRITE_ROOT": rel(out / "vfsroot")},
                               name=STAGE_BOOT_BUILD, out_dir=out)
    if not build:
        cmd.append("--no-build")
    saves = SS.reset_lifted([str(c) for c in cmd], ROOT)
    SS.require_empty(saves)
    ran = loop_run(cmd, out, STAGE_BOOT_BUILD, "a", 3600)
    return held("stage-boot", out, ran, saves, DP.DEFAULT,
                {"tick capture": ticks, "stage trace": trace}, None, base, base_sha,
                STAGE_BOOT_BUILD)


def held(name: str, out: Path, ran: dict, saves: dict, profile, files: dict[str, Path],
         kept: Path | None, base: dict, base_sha: str, build: str) -> dict:
    """The re-run held to the baseline's copies: each kept file equal to its copy
    (which is the registered file and, where a receipt records one, its pin), every
    word-log tick hash equal, the run record equal byte for byte but for the rule's
    line and the overlay path (`record_bytes`) and leaf for leaf but for the overlay
    and the rule's own counters (`record_compare`), and the rule shown running and
    handing off at no poll."""
    entry = base["runs"][name]
    rec = json.loads((out / "run.json").read_text()) if (out / "run.json").is_file() else {}
    res: dict = {"run": name, "build": build_state(build), "loop_run": ran, "saves": saves,
                 "inputs": tree_inputs(), "baseline_sha256": base_sha,
                 "silent_record": SILENT[name], "matrix_sha256": matrix_sha(out)}
    problems = []
    if not rec:
        res.update(problems=["the run wrote no record"], ok=False)
        return res
    try:
        res["device_profile"] = DP.check_run(rec, profile)
    except DP.Refusal as e:
        problems.append(f"the run is not the one asked for: {e}")
    res["reach"] = TR.reach_of(rec)
    compared = {}
    for what, mine in files.items():
        copy = entry["files"][what]
        got = sha256(mine) if Path(mine).is_file() else None
        compared[what] = {"mine": got, "registered": copy["sha256"], "pin": copy["pin"],
                          "equal": got is not None and got == copy["sha256"]}
        if not compared[what]["equal"]:
            problems.append(f"the {what} differs from the registered run's")
    res["files"] = compared
    if "word log" in entry["files"]:
        res["word_log"] = word_log_compare(kept, ROOT / entry["files"]["word log"]["copy"])
        problems += res["word_log"]["problems"]
    theirs = ROOT / entry["files"]["run record"]["copy"]
    res["record"] = record_compare(rec, json.loads(theirs.read_text()))
    if not res["record"]["ok"]:
        problems.append(f"the run record differs from the registered run's at "
                        f"{res['record']['differing_count']} leaves: {res['record']['differing'][:5]}")
    if res["record"]["registered_has_rule"]:
        problems.append("the registered record it was compared with already carries the rule")
    res["record_bytes"] = record_bytes((out / "run.json").read_bytes(), theirs.read_bytes())
    problems += [f"the run record's bytes: {p}" for p in res["record_bytes"]["problems"]]
    ph = poll_record(rec)
    res["poll_handoff"] = ph
    if not ph or ph.get("rule") != 1 or not ph.get("polls"):
        problems.append(f"the record does not show the wrapped poll running with the rule on: {ph}")
    elif ph.get("hand_offs") or ph.get("quiet"):
        problems.append(f"the rule found {ph.get('quiet')} quiet polls and handed off "
                        f"{ph.get('hand_offs')} times in a run it must leave alone")
    if "sub_427ee0" not in (res["build"].get("wrapped") or []):
        problems.append(f"the build's link did not wrap sub_427ee0: {res['build'].get('wrapped')}")
    if res["matrix_sha256"] != res["build"].get("sha256"):
        problems.append("the binary that ran is not the build's link")
    res.update(problems=problems, ok=not problems)
    return res


def run_newfile(out: Path, build: bool, rule_on: bool) -> dict:
    """The NEW FILE script on the boot's configuration (its build, device profile and
    pinned saves) with a longer poll budget, the host keyboard reading the script,
    and the rule on or broken (SA2_POLL_HANDOFF=0)."""
    name = "newfile" if rule_on else "newfile-break"
    profile = TR.native_device_profile(entry_of("boot"))
    (out / "script.txt").write_text(NEWFILE_SCRIPT)
    ticks, words, trace = out / "lifted.csv", out / "words.bin", out / "stage-trace.csv"
    env = dict(RR.ENV, SA2_PUMP_POLLS=str(NEWFILE_POLLS),
               SA2_IMPORT_BUDGET=str(max(8_000_000, NEWFILE_POLLS * 5000)),
               SA2_VIRTUAL_BUDGET_MS=str(max(30000, NEWFILE_POLLS * 20)))
    env.update(RR.profile_env(profile))
    env.update({"SA2_VFS_WRITE_ROOT": rel(out / "vfsroot"), "SA2_REPLAY_TICKS": rel(ticks),
                "SA2_STAGE_TRACE": rel(trace), "SA2_REPLAY_WORDS": rel(words),
                "SA2_INPUT_DI_SCRIPT": rel(out / "script.txt"),
                "SA2_INPUT_DI_LOG": rel(out / "input.log")})
    if not rule_on:
        env["SA2_POLL_HANDOFF"] = "0"
    prefix = f"pollfix-{name}"
    spec = "|".join([name] + [f"{k}={v}" for k, v in env.items()])
    alarm = NEWFILE_ALARM[name]
    cmd = [TR.PY, ROOT / "tools" / "s4" / "loop_run.py", "--tag", RR.TAG, "--main", RR.MAIN,
           "--name", BOOT_BUILD, "--hook", TR.HOOK, "--wrap", TR.WRAP, "--out-prefix", prefix,
           "--timeout", "3600", "--run", spec]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--timeout", f"--probe-arg={alarm}"]
    if not build:
        cmd.append("--no-build")
    saves = SS.reset_lifted([str(c) for c in cmd], ROOT)
    SS.require_empty(saves)
    ran = loop_run(cmd, out, prefix, name, 4000)
    rec = json.loads((out / "run.json").read_text()) if (out / "run.json").is_file() else {}
    kept = keep_words(words)
    res: dict = {"run": name, "build": build_state(BOOT_BUILD), "loop_run": ran, "saves": saves,
                 "inputs": tree_inputs(), "polls": NEWFILE_POLLS, "alarm_s": alarm,
                 "script_sha256": sha256(out / "script.txt"), "matrix_sha256": matrix_sha(out),
                 "run_json": rel(out / "run.json")}
    if not rec or not kept:
        res.update(problems=["the run wrote no record or no word log"], ok=False)
        return res
    pump = rec.get("pump") or {}
    trace_rows = max(0, len(trace.read_text().splitlines()) - 1) if trace.is_file() else 0
    changes = [c for c in WL.changes(kept, CURRENT_MENU, CURRENT_MENU + 4) if c[1] == CURRENT_MENU]
    menus = [{"tick": t, "from": old, "to": new} for t, _, old, new in changes]
    res.update(reach=TR.reach_of(rec), stage_trace_rows=trace_rows,
               pump={k: pump.get(k) for k in ("iterations", "quit_by_host", "quit_by_guest")},
               trap_note=(rec.get("trap") or {}).get("note", "")[:200],
               current_menu={"va": f"0x{CURRENT_MENU:08x}", "changes": menus,
                             "file_select_at": next((m["tick"] for m in menus
                                                     if m["to"] == MENUS_FILE_SELECT), None),
                             "main_menu_at": next((m["tick"] for m in menus
                                                   if m["to"] == MENUS_MAIN), None)},
               poll_handoff=poll_record(rec), asyndo=asyndo_threads(rec),
               input=(rec.get("dinput") or {}).get("host_input"))
    problems = newfile_problems(res, rule_on)
    if "sub_427ee0" not in (res["build"].get("wrapped") or []):
        problems.append(f"the build's link did not wrap sub_427ee0: {res['build'].get('wrapped')}")
    if res["matrix_sha256"] != res["build"].get("sha256"):
        problems.append("the binary that ran is not the build's link")
    res.update(problems=problems, ok=not problems)
    return res


def newfile_problems(res: dict, rule_on: bool) -> list[str]:
    """Why a NEW FILE run's reading is not what its rule setting must give: with the
    rule, the main menu reached through hand-offs that each ran another thread, the
    poll budget lasted, and no AsynDo worker left READY with no slice; with the
    break, the file select and no main menu, the run cut short, every quiet poll
    left alone, and a worker left READY with no slice."""
    ph, pump, menu = res.get("poll_handoff") or {}, res.get("pump") or {}, res["current_menu"]
    problems = []
    if menu.get("file_select_at") is None:
        problems.append("the script never reached the file select (CurrentMenu never read 9)")
    if rule_on:
        if menu.get("main_menu_at") is None:
            problems.append("the main menu was never reached (CurrentMenu never read 1)")
        if not pump.get("quit_by_host") or pump.get("iterations") != NEWFILE_POLLS:
            problems.append(f"the run did not last its poll budget: {pump}")
        if ph.get("rule") != 1 or not ph.get("hand_offs") or ph.get("switched") != ph.get("hand_offs"):
            problems.append(f"the rule did not hand off, or a hand-off ran nobody: {ph}")
        if res["asyndo"]["ready_with_no_slice"]:
            problems.append(f"AsynDo threads left READY with no slice: {res['asyndo']}")
    else:
        if menu.get("main_menu_at") is not None:
            problems.append(f"the break's run reached the main menu at tick {menu['main_menu_at']}")
        if pump.get("quit_by_host"):
            problems.append("the break's run lasted its poll budget: the hang did not come back")
        if ph.get("rule") != 0 or ph.get("hand_offs") or not ph.get("quiet"):
            problems.append(f"the break's counters are not the rule off at a pure spin: {ph}")
        if not res["asyndo"]["ready_with_no_slice"]:
            problems.append(f"no AsynDo thread was left READY with no slice: {res['asyndo']}")
    return problems


def first_equal_rows(a: Path, b: Path) -> int:
    """How many leading tick-capture rows two runs share, header excluded."""
    ra, rb = a.read_text().splitlines()[1:], b.read_text().splitlines()[1:]
    n = 0
    for x, y in zip(ra, rb):
        if x != y:
            break
        n += 1
    return n


# ------------------------------------------------------------------ the receipts

def receipt(out_dir: Path, base_dir: Path) -> dict:
    """The acceptance receipt from the six results: each run on the tree's inputs as
    they are now, each silent run compared with this one baseline, which stands for
    the registration before the rule; the break's run the rule's run up to the hang;
    the boot and NEW FILE runs on one binary."""
    base, base_sha = load_baseline(base_dir)
    problems = baseline_problems(base)
    results = {}
    now = tree_inputs()
    for name in RUNS:
        f = out_dir / name / "result.json"
        if not f.is_file():
            problems.append(f"{name}: no result at {rel(f)}")
            continue
        r = json.loads(f.read_text())
        results[name] = r
        if r.get("inputs") != now:
            was = r.get("inputs") or {}
            moved = sorted(k for k in set(was.get("pipeline") or {}) | set(now["pipeline"])
                           if (was.get("pipeline") or {}).get(k) != now["pipeline"].get(k))
            if was.get("runtime_sha256") != now["runtime_sha256"]:
                moved.append("runtime_sha256")
            problems.append(f"{name}: run on other inputs than the tree's now: {moved}")
        problems += [f"{name}: {p}" for p in r.get("problems") or []]
        if not r.get("ok"):
            problems.append(f"{name}: its verdict does not hold")
        if name in SILENT:
            if r.get("baseline_sha256") != base_sha:
                problems.append(f"{name}: compared with another baseline than "
                                f"{rel(base_dir / 'baseline.json')}")
            if (r.get("record") or {}).get("registered_has_rule") is not False:
                problems.append(f"{name}: its comparison does not show a registered record "
                                "without the rule")
    if "newfile" in results and "newfile-break" in results:
        a, b = out_dir / "newfile" / "lifted.csv", out_dir / "newfile-break" / "lifted.csv"
        shared = first_equal_rows(a, b) if a.is_file() and b.is_file() else None
        hang = results["newfile-break"].get("stage_trace_rows")
        results["newfile-break"]["capture_rows_shared_with_the_rule_on"] = shared
        if shared is None or hang is None or shared < hang:
            problems.append(f"the break's run is not the rule's run up to the hang: {shared} "
                            f"leading rows shared, the hang at {hang}")
    builds = {r["build"]["sha256"] for n, r in results.items()
              if n in ("boot", "newfile", "newfile-break")}
    if len(builds) > 1:
        problems.append(f"the boot and NEW FILE runs ran different binaries: {sorted(builds)}")
    return {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s5-poll-handoff-v1",
        "generated_by": "tools/s5/poll_handoff_check.py",
        **provenance(),
        "question": "Does the guest-poll rule end the NEW FILE wait, does its break bring the "
                    "hang back, and does the build that carries it reproduce the registered "
                    "runs as they stood before it, word for word and record for record, with "
                    "the rule handing off at none of their polls?",
        "inputs": {**now, "pipeline": {**now["pipeline"], **runtime_pins()}},
        "baseline": {"path": rel(base_dir / "baseline.json"), "sha256": base_sha,
                     "commit": base.get("commit"), "receipts": base.get("receipts")},
        # The records Documentation/scripts/check_poll_handoff.py holds to no hand-off.
        "silent": sorted(SILENT[n] for n in SILENT
                         if (results.get(n) or {}).get("ok") is True),
        "runs": results,
        "problems": problems,
        "ok": not problems,
    }


def regression_inputs() -> list[str]:
    """What the NEW FILE run is made from and read with, pinned in its receipt: the
    boot's lifted pipeline (its build and hook), the word-log reader CurrentMenu is
    read with, the registry, tick_replay's helpers and this tool."""
    out = TR.lifted_pipeline(entry_of("boot")) + TR.WORD_COMPARE + [
        "tools/s4/tick_replay.py", "Documentation/replays.json", "tools/s5/poll_handoff_check.py"]
    return list(dict.fromkeys(out))


def regression_manifest() -> dict:
    """What Documentation/scripts/check_poll_handoff.py holds the NEW FILE receipt
    to on every push: the reach run's build manifest (tools/s4/reach_receipt.py
    `manifest`: the executable, the lifted and whole-image objects, the edge table,
    the fold code and the runtime), this run's own pipeline, and the runtime digest.
    Any of it moving means the run describes a build that no longer exists, and
    only the run can say whether the wait still ends (the 2026-10-02 review, S2)."""
    return {"build": RR.manifest(), "runtime_sha256": TR.runtime_digest(),
            "pipeline": {f: sha256(ROOT / f) for f in regression_inputs()}}


def regression_doc(res: dict, manifest: dict) -> dict:
    return {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s5-newfile-wait-v1",
        "generated_by": "tools/s5/poll_handoff_check.py",
        "command": ".venv/bin/python tools/s5/poll_handoff_check.py regression --write",
        **provenance(),
        "question": "On the tree as it is, does the NEW FILE script still reach the main menu, "
                    "through the guest-poll rule's hand-offs?",
        "inputs": {"pipeline": dict(sorted({**manifest["build"]["pipeline"],
                                            **manifest["pipeline"]}.items()))},
        "manifest": manifest,
        "run": res,
        "ok": bool(res.get("ok")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--baseline", type=Path, default=OUT / "baseline")
    r = sub.add_parser("run")
    r.add_argument("name", choices=RUNS)
    r.add_argument("--no-build", action="store_true")
    r.add_argument("--out-dir", type=Path, default=OUT)
    r.add_argument("--baseline", type=Path, default=OUT / "baseline")
    c = sub.add_parser("receipt")
    c.add_argument("--out-dir", type=Path, default=OUT)
    c.add_argument("--baseline", type=Path, default=OUT / "baseline")
    c.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("regression")
    g.add_argument("--out-dir", type=Path, default=OUT)
    g.add_argument("--write", action="store_true")
    a = ap.parse_args()

    def under_root(p: Path) -> Path:
        return p if p.is_absolute() else ROOT / p
    if a.cmd == "baseline":
        doc = take_baseline(under_root(a.baseline))
        print(json.dumps({r: {"has_rule": e["has_rule"], "files": sorted(e["files"])}
                          for r, e in doc["runs"].items()}, indent=1))
        return 0
    if a.cmd == "receipt":
        doc = receipt(under_root(a.out_dir), under_root(a.baseline))
        a.out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(json.dumps({"ok": doc["ok"], "problems": doc["problems"], "silent": doc["silent"]},
                         indent=1))
        return 0 if doc["ok"] else 1
    guards()
    if a.cmd == "regression":
        before = regression_manifest()
        out = under_root(a.out_dir) / "regression" / time.strftime("%Y%m%dT%H%M%S")
        out.mkdir(parents=True)
        (out / "vfsroot").mkdir()
        res = run_newfile(out, build=True, rule_on=True)
        (out / "result.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str)
                                         + "\n")
        if regression_manifest() != before:
            raise SystemExit("an input moved while the NEW FILE run ran; nothing was recorded")
        doc = regression_doc(res, before)
        if a.write:
            REGRESSION_RECEIPT.write_text(json.dumps(doc, indent=1, sort_keys=True,
                                                     default=str) + "\n")
        print(json.dumps({"ok": doc["ok"], "problems": res.get("problems"),
                          "poll_handoff": res.get("poll_handoff"),
                          "current_menu": res.get("current_menu"), "run": rel(out),
                          "written": rel(REGRESSION_RECEIPT) if a.write else None}, indent=1,
                         default=str))
        return 0 if doc["ok"] else 1
    out = under_root(a.out_dir) / a.name
    if out.exists():
        raise SystemExit(f"{rel(out)} exists: a run directory is used once")
    base = base_sha = None
    if a.name in SILENT:
        base, base_sha = load_baseline(under_root(a.baseline))
        problems = baseline_problems(base)
        if problems:
            raise SystemExit("; ".join(problems))
    out.mkdir(parents=True)
    (out / "vfsroot").mkdir()
    build = not a.no_build
    if a.name == "boot":
        res = run_boot(out, build, base, base_sha)
    elif a.name == "metalharbor":
        res = run_metalharbor(out, build, base, base_sha)
    elif a.name == "reach":
        res = run_reach(out, build, base, base_sha)
    elif a.name == "stage-boot":
        res = run_stage_boot(out, build, base, base_sha)
    else:
        res = run_newfile(out, build, rule_on=(a.name == "newfile"))
    (out / "result.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: res.get(k) for k in ("run", "ok", "problems", "loop_run", "poll_handoff",
                                              "word_log", "record_bytes", "current_menu")},
                     indent=1, default=str))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
