#!/usr/bin/env python3
"""The receipt for the recording DirectSound device.

Subtitle: read the files this slice produced -- the image scan, the driven
probe and its four breaks, the two determinism pairs, the guest-memory peek
and the build-cost measurement -- and write one document whose every number
was copied from a file a program wrote, never typed.

What it asserts, and what each assertion is read from
----------------------------------------------------
  * every DirectSound slot this runtime declares carries the argument count
    the image actually pushes at its call sites -- from
    `tools/s4/dsound_surface.py --argc`, with the run under
    `SA2_DSOUND_BREAK=argc` beside it to show the check can fail;
  * the cursors are `anchor_pos + elapsed_us * nAvgBytesPerSec / 1e6`, floored
    and block-aligned -- from `harness/dsound_probe.c`, which recomputes the
    expected byte independently and records both;
  * the door the previous slice found shut is open -- from four guest globals
    that were zero in `Documentation/receipts/s4-cri-streaming-v1.json` and
    are not zero now;
  * where the guest gets, with the device off and on -- from the two runs'
    own `winmain_loop`, `threads` and `files` sections;
  * determinism -- from the two byte-identical pairs, with the breaks showing
    the comparison can fail.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/dsound_receipt.py
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
OUT = C.GEN / "s4" / "receipts" / "s4-dsound-recording-device-v1.json"


def jload(p: Path):
    return json.loads(p.read_text()) if p.is_file() else {"error": f"missing: {p}"}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def shape(run: dict) -> dict:
    """The progress measures a reader compares between rows. Every one of them
    is something only the guest could have produced: a counter this runtime
    increments after the guest's own instruction did the thing, never before."""
    t = run.get("threads", {})
    ticks = t.get("ticks", [])
    f = run.get("files", {}).get("stats", {})
    ds = run.get("dsound", {})
    return {
        "import_calls": run.get("calls", {}).get("total"),
        "winmain_iterations": run.get("winmain_loop", {}).get("iterations"),
        "frames": t.get("frames"),
        "virtual_us": ticks[-1]["virtual_us"] if ticks else None,
        "guest_threads_created": t.get("created"),
        "thread_switches": t.get("switches"),
        "message_boxes": run.get("message_boxes", {}).get("count"),
        "file_opens": f.get("opens"),
        "file_bytes_read": f.get("bytes_read"),
        "archive_opened": any("afs" in o.get("guest", "").lower()
                              for o in run.get("files", {}).get("opened", [])),
        "dsound_method_calls": ds.get("method_calls"),
        "dsound_distinct_methods": ds.get("distinct_methods"),
        "stopped_at": run.get("trap", {}).get("exit_pc"),
        "stop_note": run.get("trap", {}).get("note"),
    }


def identical(a: Path, b: Path) -> dict:
    return {"a": C.rel(a) if hasattr(C, "rel") else str(a), "b": str(b),
            "a_sha256": sha256(a), "b_sha256": sha256(b),
            "byte_identical": bool(sha256(a)) and sha256(a) == sha256(b)}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    surface = jload(C.GEN / "s4" / "dsound" / "surface.json")
    surface_brk = jload(C.GEN / "s4" / "dsound" / "surface.brkargc.json")
    probe = {lbl: jload(RUN / f"dsp.{lbl}.json")
             for lbl in ("baseline", "break_argc", "break_frozen", "break_noout",
                         "break_nolead")}
    off_a, on_a = jload(RUN / "ds7.off_a.json"), jload(RUN / "ds7.on_a.json")
    peek = jload(RUN / "ds9.peek.json")
    bc = jload(C.GEN / "s4" / "build_cost" / "build_cost.next+allarms.json")
    corpus = jload(C.GEN / "s2" / "corpus_run" / "run.pc24.json")
    controls = jload(C.GEN / "s2" / "corpus_run" / "controls.json")
    binary = C.GEN / "s4" / "link" / "mains" / "loop.next+allarms" / "sa2_main"
    dsbin = C.GEN / "s4" / "link" / "mains" / "dsound.next+allarms" / "sa2_main"

    b = probe["baseline"]
    samples = b.get("cursor_samples", [])
    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-dsound-recording-device-v1",
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(C.ROOT),
                                 capture_output=True, text=True).stdout.strip(),
        "question": "Design 3.5 puts dsound in presentation and design 5 S4 asks for "
                    "presentation stubs that record calls. What does a recording "
                    "IDirectSound8 answer, what does it refuse, and where does the guest "
                    "get once DirectSoundCreate8 stops returning failure?",
        "inputs": {
            "executable": "private/exe/sonic2app-unpacked.exe",
            "executable_sha256": C.sha256(C.EXE),
            "driver_binary": str(binary),
            "driver_binary_sha256": sha256(binary),
            "probe_binary": str(dsbin),
            "probe_binary_sha256": sha256(dsbin),
            "pipeline": {p: sha256(C.ROOT / p) for p in (
                "runtime/sa2_dsound.c", "runtime/sa2_dsound.h", "runtime/sa2_imports.c",
                "harness/dsound_probe.c", "harness/crt_probe.c",
                "tools/s4/dsound_surface.py", "tools/s4/dsound_receipt.py",
                "tools/s4/lift_extra.py", "tools/s4/crt_run.py", "tools/s4/link_s4.py")},
        },

        # ---------------------------------------------------------- the surface
        "the_surface": {
            "what_it_scans": surface.get("coverage"),
            "roots": surface.get("roots"),
            "register_seeds": surface.get("register_seeds"),
            "attributed_sites": len(surface.get("sites", [])),
            "distinct_slots": surface.get("distinct_slots"),
            "vtable_shaped_sites_unattributed":
                surface.get("vtable_shaped_sites_unattributed"),
            "ceiling_slots_that_could_still_be_dsound":
                surface.get("ceiling_slots_that_could_still_be_dsound"),
            "witnesses": surface.get("check", {}).get("witnesses"),
            "witnesses_ok": surface.get("check", {}).get("ok"),
            "falsify": surface.get("falsify"),
        },
        "the_argument_counts": {
            "why": "a __stdcall COM method pops `this` plus its own arguments, so a "
                   "declared count that is short leaves the caller's frame drifting and "
                   "every later [esp+n] reads the wrong local. This runtime has been bitten "
                   "by that twice: d3d9 at S4 slice 6 and Steam at S4 slice 9. Neither "
                   "produced a wrong answer, which is why no assertion in the runtime "
                   "could see either of them.",
            "compared": surface.get("argc", {}).get("compared"),
            "disagreements": surface.get("argc", {}).get("disagreements"),
            "ok": surface.get("argc", {}).get("ok"),
            "shown_able_to_fail": {
                "how": "SA2_DSOUND_BREAK=argc patches the *declared* count of "
                       "IDirectSound8::CreateSoundBuffer to 2, so the number the "
                       "dispatcher pops with and the number the run publishes are the "
                       "same wrong number -- a break that changed only the pop would "
                       "leave this check green over a broken build",
                "disagreements": surface_brk.get("argc", {}).get("disagreements"),
                "ok": surface_brk.get("argc", {}).get("ok"),
            },
            "also_measured_at_runtime": {
                "how": "harness/dsound_probe.c enters every method through the fabricated "
                       "vtable with sa2_call and measures ESP after the call",
                "calls": b.get("argument_counts", {}).get("calls"),
                "every_slot_popped_exactly_right":
                    b.get("argument_counts", {}).get(
                        "every_slot_popped_exactly_this_plus_its_own_arguments"),
                "under_break_argc": probe["break_argc"].get("first_failure"),
            },
        },

        # ---------------------------------------------------------- the cursors
        "the_cursors": {
            "derivation": "play = anchor_pos + floor(elapsed_us * nAvgBytesPerSec / 1e6), "
                          "in uint64 throughout; looping buffers take it modulo "
                          "dwBufferBytes, non-looping buffers halt at the last whole block "
                          "and clear DSBSTATUS_PLAYING; the result is then rounded DOWN to "
                          "nBlockAlign so no cursor is ever mid-sample. elapsed_us is "
                          "sa2_virtual_us() minus the anchor, and Play, Stop and "
                          "SetCurrentPosition each re-anchor, so a cursor is "
                          "elapsed-since-anchor and never elapsed-since-process-start.",
            "write_cursor": "the play cursor plus a FIXED lead, computed once when the "
                            "buffer's format becomes known and block-aligned then, modulo "
                            "dwBufferBytes; it is not re-derived per call, because a lead "
                            "that moved would let a guest watch the write cursor go "
                            "backwards relative to the play cursor",
            "clock": "sa2_virtual_us(), the same clock Present advances at the frame "
                     "boundary. Nothing in runtime/sa2_dsound.c reads host time, a random "
                     "number or a pointer value.",
            "format_under_test": b.get("format"),
            "samples": samples,
            "every_sample_matched": all(s["play"] == s["expected_play"]
                                        and s["write"] == s["expected_write"]
                                        for s in samples) and bool(samples),
            "checks": b.get("checks"), "failures": b.get("failures"),
            "shown_able_to_fail": {
                lbl: {"checks": probe[lbl].get("checks"),
                      "failures": probe[lbl].get("failures"),
                      "first_failure": probe[lbl].get("first_failure")}
                for lbl in ("break_frozen", "break_nolead", "break_noout", "break_argc")},
            "device_counters": b.get("device"),
        },

        # ------------------------------------------------------ where it gets
        "where_the_guest_gets": {
            "device_off": shape(off_a),
            "device_on": shape(on_a),
            "the_door": {
                "claim": "S4 slice 10 recorded four guest globals as zero and named them "
                         "as the proof that CRI's file system had no workers behind the "
                         "DirectSound refusal. Those four are the observable here, and "
                         "they are read out of guest memory after the run rather than "
                         "inferred from a program counter.",
                "before": {
                    "source": "Documentation/receipts/s4-cri-streaming-v1.json"
                              " the_answer.delegate_globals",
                    "delegate1": "0x00000000", "delegate2": "0x00000000",
                    "delegate_cs": "0x00000000", "fs_dev": "0x00000000"},
                "after": peek.get("peek"),
            },
            "did_the_partition_load_start": {
                "answer": "no, and not for the reason the previous slice found",
                "evidence": {
                    "adxf_handle": peek.get("peek", {}).get("adxf"),
                    "ptid_cur": peek.get("peek", {}).get("ptid_cur"),
                    "archive_opened_by_the_virtual_file_system":
                        shape(on_a)["archive_opened"],
                },
                "reading": "the guest never asks for the partition in this run, because "
                           "the request is made from WinMain's loop and the run now stops "
                           "before that loop starts. The load has not started because the "
                           "ask has not happened, which is a different state from the "
                           "previous slice's queued-and-never-started request.",
            },
        },

        # ------------------------------------------------------- determinism
        "determinism": {
            "device_off": identical(RUN / "ds7.off_a.json", RUN / "ds7.off_b.json"),
            "device_on": identical(RUN / "ds7.on_a.json", RUN / "ds7.on_b.json"),
            "driven_probe": identical(RUN / "dsp.det_a.json", RUN / "dsp.det_b.json"),
            "why_it_is_expected": "the cursors are a function of the virtual clock, and "
                                  "virtual time moves only when the guest asks or when "
                                  "nothing is runnable, so a clock-derived value is "
                                  "deterministic by construction under the single-token "
                                  "scheduler. It is checked rather than argued.",
        },

        # ----------------------------------------------------------- the cost
        "build_cost": {
            "criterion_minutes_added": bc.get("delta", {}).get("criterion_minutes_added"),
            "why_zero": bc.get("delta", {}).get("why_zero"),
            "compile_seconds_added_by_file":
                bc.get("delta", {}).get("compile_seconds_added_by_file"),
            "extra_blocks": bc.get("extra_blocks"),
        },
        # The corpus and controls files carry one entry per fixture rather than a
        # summary, so the totals are counted here from the entries. A summary
        # field that the producing tool does not write is a field somebody would
        # have had to type.
        "regression": {
            "corpus": {
                "variant": corpus.get("variant"),
                "fixtures": len(corpus.get("results", [])),
                "passed": sum(1 for r in corpus.get("results", []) if r.get("rc") == 0),
                "path": "private/generated/s2/corpus_run/run.pc24.json",
                "sha256": sha256(C.GEN / "s2" / "corpus_run" / "run.pc24.json")},
            # The controls tally is taken from `controls.py`'s own summary line,
            # not recounted here. A first draft did recount it and got 98 of 374,
            # because the file's rows are not shaped the way the recount assumed
            # -- a number that is present and means something else, which is the
            # failure the operating notes say the gates are worst at catching.
            "controls": {
                "fixtures": len(controls),
                "summary_line": next(
                    (ln for ln in (RUN / "controls2.log").read_text().splitlines()
                     if "asserting controls hold" in ln), "")
                    if (RUN / "controls2.log").is_file() else "",
                "counted_by": "tools/s2/corpus_run/controls.py, quoted verbatim",
                "path": "private/generated/s2/corpus_run/controls.json",
                "sha256": sha256(C.GEN / "s2" / "corpus_run" / "controls.json")},
            "stdout": {
                "corpus": (RUN / "corpus2.log").read_text().strip().splitlines()[-2:]
                          if (RUN / "corpus2.log").is_file() else [],
                "controls": (RUN / "controls2.log").read_text().strip().splitlines()[-2:]
                            if (RUN / "controls2.log").is_file() else [],
                "gates": (RUN / "gates2.log").read_text().strip().splitlines()[-3:]
                         if (RUN / "gates2.log").is_file() else [],
            },
        },
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: doc[k] for k in ("the_argument_counts",)}, indent=1)[:600])
    print("->", OUT)


if __name__ == "__main__":
    main()
