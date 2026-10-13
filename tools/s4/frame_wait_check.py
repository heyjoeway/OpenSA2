#!/usr/bin/env python3
"""Where the frame's wait sits, shown by the word it moves.

Subtitle: the boot replay's lifted run with the d3d9 device's frame wait where it
is (BeginScene) and under SA2_D3D9_BREAK=presentwait (every wait at Present, as
before 2026-09-26), each held to every registered native boot on one word:
Sofdec's first playback handle's served flag.

    source tools/env.sh
    .venv/bin/python tools/s4/frame_wait_check.py [--json OUT]

Why this exists
---------------
The device takes each frame's wait at BeginScene because the adopted oracle waits
there: the Mod Loader's frame limiter runs in its BeginScene wrapper, at the
frame's start (runtime/sa2_d3d9.c, `frame_wait`, which says what is and is not
measured about the native Present). Where the wait sits decides whether CRI's 60 Hz server threads run
after a frame's logic or before the next frame's. The word 0x01d0c6b8 is field
+0x88 of Sofdec's first playback handle (the handle array at 0x01d0c630). The
handle server at 0x007eb8a0 sets it when it first serves the handle, on ADXM's MW
idle thread, which ADXM's vsync thread resumes at 60 Hz. The boot creates the
handle between BeginScene and Present. With the wait at Present the lifted run
served it inside that tick; every native boot serves it in the next.

A break nobody runs is prose, so this runs it.

What it checks (each can fail, and the run fails if any does)
-------------------------------------------------------------
  * control_waits: the control run's own record takes no wait at a Present that
    followed a BeginScene: every frame boundary the scheduler crossed is one of
    the two counted waits, every BeginScene waits, and the Presents that wait
    are at least one (the boot's first Present comes before any BeginScene, so
    the fallback runs) and at most P - B + 1. The bound is this boot's call
    pattern, not a law: the Presents that wait are P minus the Presents a
    BeginScene came before, which is P - B plus the BeginScenes no Present
    follows alone; the boot has exactly one (its last BeginScene, which no
    Present follows, or a frame with two scenes). A run that ends after a
    Present leaves one wait of slack; a boot with two such BeginScenes fails
    here and the bound must be re-derived, not widened;
  * break_waits: the break run's record takes none at BeginScene and one at every
    Present, and every frame boundary is one of them;
  * covers_native: each lifted log holds every tick each native log holds, and
    each native log changes the word on one of them, so the two checks below
    compare the tick the word moves on and are never empty;
  * control_follows_native: the control's word equals each native boot's on every
    tick from 1 that both logs hold;
  * break_moves_it: the break's word differs from each native boot's on some tick,
    so the break moves what the check above reads.

The native side is the boot entry of Documentation/replays.json: its capture's
word log and every repeat's. Nothing here writes a tracked file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tick_replay as TR  # noqa: E402
import wordlog as WL  # noqa: E402

WORD = 0x01D0C6B8
BREAK = "presentwait"


def series(log: Path) -> tuple[dict[int, int], int]:
    """The word's value after each tick that changes it, and the log's last tick."""
    changes = {t: new for (t, va, _old, new) in WL.changes(log, WORD, WORD + 4) if va == WORD}
    info = WL.info(log)
    return changes, int(info.get("last_tick") or info.get("ticks"))


def values(changes: dict[int, int], upto: int) -> list[int]:
    out, cur = [], changes.get(0, 0)
    for t in range(0, upto + 1):
        cur = changes.get(t, cur)
        out.append(cur)
    return out


def covers(lifted: tuple[dict[int, int], int], native: tuple[dict[int, int], int]) -> bool:
    """The lifted log holds every native tick, and the native changes the word on one."""
    return lifted[1] >= native[1] and any(1 <= t <= native[1] for t in native[0])


def differing(a: tuple[dict[int, int], int], b: tuple[dict[int, int], int]) -> list[int]:
    upto = min(a[1], b[1])
    va, vb = values(a[0], upto), values(b[0], upto)
    return [t for t in range(1, upto + 1) if va[t] != vb[t]]


def waits(run: dict) -> dict:
    rec = json.loads((TR.ROOT / run["run_json"]).read_text())
    f = rec["d3d9"]["frames"]
    out = {k: f.get(k) for k in ("present", "begin_scene", "waits_at_begin_scene",
                                  "waits_at_present")}
    out["frame_boundaries"] = (rec.get("threads") or {}).get("frames")
    return out


def counted(w: dict) -> bool:
    """Every frame boundary the scheduler crossed is one of the two counted waits."""
    return (None not in w.values()
            and w["frame_boundaries"] == w["waits_at_begin_scene"] + w["waits_at_present"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, default=None, help="also write the result here")
    a = ap.parse_args()

    entry = next(e for e in json.loads(TR.REGISTRY.read_text())["replays"] if e["name"] == "boot")
    sources = [entry["native"]] + list(entry["native"].get("repeats", []))
    missing = [s.get("capture") for s in sources if not s.get("word_log")]
    if missing:
        raise SystemExit(f"FRAME_WAIT refused: registered boot(s) without a word log: {missing}")
    natives = [s["word_log"]["file"] for s in sources]
    stale = [s["word_log"]["file"] for s in sources
             if TR.sha256(TR.ROOT / s["word_log"]["file"]) != s["word_log"]["sha256"]]
    if stale:
        raise SystemExit(f"FRAME_WAIT refused: word log(s) not the registered bytes: {stale}")
    profile = TR.native_device_profile(entry)
    control = TR.run_lifted("frame-wait-control", {}, device_profile=profile, words=True)
    broken = TR.run_lifted(f"frame-wait-{BREAK}", {"SA2_D3D9_BREAK": BREAK},
                           device_profile=profile, words=True)

    c_log, b_log = TR.ROOT / control["words"]["path"], TR.ROOT / broken["words"]["path"]
    c_ser, b_ser = series(c_log), series(b_log)
    rows = []
    for n in natives:
        n_ser = series(TR.ROOT / n)
        rows.append({"native": n, "native_last_tick": n_ser[1],
                     "native_changes": sorted(n_ser[0]),
                     "ticks_compared": {"control": min(c_ser[1], n_ser[1]),
                                        "break": min(b_ser[1], n_ser[1])},
                     "covered": covers(c_ser, n_ser) and covers(b_ser, n_ser),
                     "control_differs_on": differing(c_ser, n_ser)[:10],
                     "break_differs_on": differing(b_ser, n_ser)[:10]})
    cw, bw = waits(control), waits(broken)
    checks = {
        "control_waits": (counted(cw) and cw["waits_at_begin_scene"] == cw["begin_scene"]
                          and 1 <= cw["waits_at_present"]
                          <= cw["present"] - cw["begin_scene"] + 1),
        "break_waits": (counted(bw) and bw["waits_at_begin_scene"] == 0
                        and bw["waits_at_present"] == bw["present"]),
        "covers_native": bool(rows) and all(r["covered"] for r in rows),
        "control_follows_native": bool(rows) and all(not r["control_differs_on"] for r in rows),
        "break_moves_it": bool(rows) and all(r["break_differs_on"] for r in rows),
    }
    result = {"word": f"{WORD:#010x}", "break": BREAK, "natives": len(rows),
              "control": {"capture_sha256": control["sha256"], "waits": cw,
                          "changes": {str(k): v for k, v in sorted(c_ser[0].items())}},
              "broken": {"capture_sha256": broken["sha256"], "waits": bw,
                         "changes": {str(k): v for k, v in sorted(b_ser[0].items())}},
              "rows": rows, "checks": checks, "ok": all(checks.values())}
    for k, v in checks.items():
        print(f"FRAME_WAIT {k}: {'ok' if v else 'FAILED'}")
    print(f"FRAME_WAIT word {WORD:#010x}: control sets it at "
          f"{sorted(c_ser[0])}, the break at {sorted(b_ser[0])}, over {len(rows)} native boots")
    if a.json:
        a.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
