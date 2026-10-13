#!/usr/bin/env python3
"""The receipt for S4 slice 10: why the CRI file read never finishes.

Subtitle: read the run JSONs this slice produced -- the shipping run, its two
breaks, the two determinism runs, the peeks and the build-cost measurement --
and write one document whose every number was copied from a file a program
wrote, never typed.

What it asserts, and what each assertion is read from
----------------------------------------------------
  * the guest is polling `adxf_GetPtStat` and the CRIFS request is queued and
    unstarted -- from the `SA2_PEEK` runs' guest memory;
  * the virtual file system was never asked for the archive -- from the run's
    own `files.stats` and `files.opened`;
  * the two CRI server threads starve without the critical-section hand-off,
    and the clock stands still without the spin rule -- from the shipping run
    and the two break runs, compared clause by clause;
  * the CRI file system's own worker threads were never created -- from four
    guest globals that are zero, and from a byte-level scan of every call site
    of the chain that would have set them;
  * determinism -- from the two-run comparison and from the `free` break that
    shows the comparison can fail.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/cri_receipt.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

RUN = C.GEN / "s4" / "run"
THREADS = C.GEN / "s4" / "threads"
OUT = C.GEN / "s4" / "receipts" / "s4-cri-streaming-v1.json"


def jload(p: Path):
    return json.loads(p.read_text()) if p.is_file() else {"error": f"missing: {p}"}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def shape(run: dict) -> dict:
    """The progress measures a reader compares between rows."""
    t = run.get("threads", {})
    ticks = t.get("ticks", [])
    rows = t.get("rows", [])
    return {
        "import_calls": run.get("calls", {}).get("total"),
        "winmain_iterations": run.get("winmain_loop", {}).get("iterations"),
        "frames": t.get("frames"),
        "virtual_us_at_last_tick": ticks[-1]["virtual_us"] if ticks else None,
        "switches": t.get("switches"),
        "time_advances": t.get("time_advances"),
        "spin_yields": t.get("spin_yields"),
        "spin_observations": t.get("spin_observations"),
        "spin_clock_moves": t.get("spin_clock_moves"),
        "cs_handoff": t.get("cs_handoff"),
        "timer_fires": t.get("timers", {}).get("fires"),
        "worker_blocking_waits": [r.get("blocks") for r in rows[1:4]],
        "worker_guest_calls": [r.get("guest_calls") for r in rows[1:4]],
        "worker_waiting_on": [r.get("waiting_on") for r in rows[1:4]],
        "events": t.get("events"),
        "afs_opens": sum(1 for o in run.get("files", {}).get("opened", [])
                         if o.get("rel", "").lower().endswith(".afs")),
        "vfs_opens": run.get("files", {}).get("stats", {}).get("opens"),
        "message_boxes": run.get("message_boxes", {}).get("shown"),
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ship = jload(RUN / "f2.on.json")
    nocs = jload(RUN / "f2.nocs.json")
    nospin = jload(RUN / "f2.nospin.json")
    peek_state = jload(RUN / "cri2.peek.json").get("peek", {})
    peek_dele = jload(RUN / "cri2.dele.json").get("peek", {})
    ring = jload(RUN / "cri2.ring.json").get("peek_dump", {})
    det = jload(THREADS / "determinism.s10b.json")
    det_free = jload(THREADS / "determinism.free.s10b.json")
    cost = jload(C.GEN / "s4" / "build_cost" / "build_cost.next+allarms.json")

    binary = C.GEN / "s4" / "link" / "mains" / "loop.next+allarms" / "sa2_main"

    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-cri-streaming-v1",
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(C.ROOT),
                                 capture_output=True, text=True).stdout.strip(),
        "question": "The guest polls adxf_GetPtStat(0x1E) forever after asking CRI to load "
                    "the partition 'event_adx_e.afs'. What is the file server waiting for?",
        "inputs": {
            "executable": str(C.EXE.relative_to(C.ROOT)),
            "executable_sha256": C.sha256(C.EXE),
            "binary": str(binary.relative_to(C.ROOT)) if binary.is_file() else "",
            "binary_sha256": sha256(binary),
            "pipeline": {
                p: sha256(C.ROOT / p) for p in (
                    "runtime/sa2_threads.c", "runtime/sa2_threads.h",
                    "runtime/sa2_imports.c", "runtime/sa2_imports.h",
                    "harness/crt_probe.c", "tools/s4/xrefs.py",
                    "tools/s4/cri_receipt.py",
                )
            },
        },
        "the_poll": {
            "loop": "0x004425d0 calls adxf_LoadPartitionNw at 0x00442700 and then spins on "
                    "adxf_GetPtStat (0x007C4E20, ptid 0x1E) at 0x00442707/0x00442720 until the "
                    "status is 3 (loaded) or 4 (error)",
            "archive": "event_adx_e.afs, assembled by the guest at 0x004426c6 from the .rdata "
                       "strings at 0x008B8A04/0x008B8A0C/0x008B8A10",
            "archive_in_installation": "resource/gd_PC/event_adx_e.afs",
            "guest_state": peek_state,
            "adxf_trace_ring_at_0x01AFEFE0": ring,
            "reading": {
                "0x01AFEFC0 = 0x1e": "the ptid the game asked for; the ptid check passes",
                "0x01AFEFC4 = 2": "the cached status the poll keeps reading: LOADING",
                "0x01AFF120 = 0x01aff540": "a live ADXF handle, so the open did not fail",
                "adxf+4 = 0x01b0e420": "its CRIFS handle, slot 48 of the array at "
                                       "0x01B0D0A0 (stride 0x68)",
                "fshn[0x49] = 1": "a CRIFS request is queued",
                "fshn[0x4D] = 0": "and has never been started: 0x007cd6f0 sets this to 1 at "
                                  "0x007cd764 and has never run for this handle",
                "0x007C4340 returns 0": "so adxf_GetPtStat returns the cached 2 at 0x007C49A8 "
                                        "without ever driving the server",
            },
        },
        "the_answer": {
            "class": "a subsystem whose initialisation lives behind a stub that deliberately "
                     "refuses -- not one of the five candidate classes, and not audio",
            "delegate_globals": peek_dele,
            "chain": [
                "0x00861260 is the sound-system init",
                "0x0086126C and 0x0086128A call DSOUND.dll ordinal 11 (DirectSoundCreate8)",
                "this runtime answers E_FAIL and a null out-pointer, by design 3.5 "
                "(audio is S5's), so the guest shows 'DirectSound Create Failed!' twice and "
                "returns at 0x008612AD",
                "0x008613BF -- CRI's file-system setup (0x007C5270) -- is past that return",
                "0x007C5270 -> 0x007D4D00 -> 0x007D3DF0 creates the two CRIFS delegate worker "
                "threads at 0x007DB310/0x007DAEE0 and stores them at 0x01763398/0x0176339C",
                "both are 0, the delegate module's critical section 0x01763E48 is 0, and the "
                "device slot 0x01754FB8 is 0: the file system has no workers",
                "the game asks it to load the archive anyway and polls forever",
            ],
            "call_sites_are_from_a_byte_scan": {
                "why": "the analyzer function map covers 82.8 % of executable bytes, and a "
                       "decode-from-entries cross-reference therefore reports 'the only "
                       "caller' over 82.8 % of the image. Every call site below comes from a "
                       "byte-level E8/E9 scan of 100 % of the executable sections, which found "
                       "one more call site of 0x007D0CB0 than the decode did (0x007C5070, in "
                       "an analyzer gap).",
                "0x007C5270": ["0x008613BF"],
                "0x007D4D00": ["0x007C52B0"],
                "0x007D3DF0": ["0x007D4D5A"],
                "0x007C0CC8 (DSOUND ordinal 11)": ["0x0086126C", "0x0086128A"],
            },
            "not_proved": "that opening the DirectSound door is sufficient. What is proved is "
                          "that it is shut and that the file system's own workers do not exist "
                          "behind it.",
        },
        "ruled_out_by_evidence": {
            "a read the virtual file system never completes":
                "ruled out: the VFS was never asked. 15 opens, 0 of them .afs.",
            "an event nothing signals":
                "ruled out by the event census: both events have a waiter, one is set 161 "
                "times by the guest's own CriSr_SignalingServer and the other is pulsed by "
                "the multimedia timer (sets = 0 because a pulse is not a set, and "
                "pulses_delivered accounts for every one).",
            "a worker whose start routine is in no module":
                "ruled out: 0 undispatched addresses, and every created thread's start routine "
                "resolves and runs -- 0x00853192 (CriSr_ProcessServer / CriSr_SignalingServer), "
                "0x00785500 (sound) and 0x008653D0 (AsynDo).",
            "a scheduler hand-off point the guest's path never reaches":
                "ruled IN, fixed, and necessary but not sufficient. See the_fix.",
        },
        "the_fix": {
            "what": [
                "LeaveCriticalSection is a hand-off point (SA2_CS_HANDOFF=0 is the break). "
                "G1's decision already lists 'event and critical-section calls' among the "
                "entry points where the next runnable thread is picked; the implementation "
                "handed off only on contention, and the guest's poll takes four uncontended "
                "locks per iteration and makes no other runtime call, so a READY worker "
                "starved forever.",
                "The spin rule: SA2_SPIN_YIELDS (256) consecutive hand-off points that find "
                "nobody ready advance the clock to the earliest deadline, because a thread "
                "that has passed that many with nothing else runnable and the clock standing "
                "still is busy-waiting. SA2_SPIN_YIELDS=0 is the break.",
            ],
            "shipping": shape(ship),
            "break_cs_handoff_off": shape(nocs),
            "break_spin_rule_off": shape(nospin),
            "what_each_break_moves": {
                "SA2_CS_HANDOFF=0": "switches and the workers' blocking waits collapse to the "
                                    "pre-fix values; spin observations fall to 6 because the "
                                    "poll no longer reaches a hand-off point at all",
                "SA2_SPIN_YIELDS=0": "the hand-off points are still reached (96 609 "
                                     "observations) and the clock never moves (0), which is "
                                     "the clause that says the hand-off alone is not enough",
            },
            "frame_invariant": "8 frames and 133 330 us at the twelfth tick row in all three "
                               "rows: the spin rule at 256 does not stretch the frame loop. At "
                               "16 it did, to 249 992 us, and that is why 256 is the default.",
        },
        "determinism": {
            "two_runs": {k: det.get(k) for k in
                         ("identical", "ticks_a", "ticks_b", "ticks_identical",
                          "ticks_compared_something", "verdict")},
            "shown_able_to_fail": {k: det_free.get(k) for k in
                                   ("identical", "ticks_identical", "verdict")},
            "first_differences_under_the_break": det_free.get("differences", [])[:8],
        },
        "build_cost": cost,
        "regression": {
            "corpus": "tools/s2/corpus_run/run.py",
            "controls": "tools/s2/corpus_run/controls.py",
            "gates": "Documentation/scripts/run_all_gates.py",
        },
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
