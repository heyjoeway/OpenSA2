#!/usr/bin/env python3
"""Compute the spike's wall-clock numbers from tools/spike/timeline.jsonl.

The timeline is append-only, one JSON object per line: {"ts","lifter","event",
"detail"} with ts in UTC ISO 8601 (...Z). The spike start is the first line.
Per lifter this prints, and `summary()` returns, minutes from the spike start
to: the lifter's first event, its install outcome (installed / unpacked /
install_failed), its first_compilable_output, and its ten_functions_done, plus
the lifter's own span (first to last event). The receipt copies these numbers
(tools/spike/write_receipt.py); nobody types them.

Two clocks for "time to first compilable output". The shared clock
(`time_to_first_compilable_output_minutes`, column first_compilable) counts
from the spike's first event, so for lifters tried one after another it
includes the queue time spent on the lifters before it. The lifter's own
clock (`own_first_compilable_minutes`, column own_first_comp) counts from that
lifter's own first timeline event, which is the number the 60-minute install
box is about. The event that ends either clock is first_compilable_output, or
pass_first_compiles for the remill-pc24pass driver (whose "install" is the
pass itself).

Usage: .venv/bin/python tools/spike/timeline_summary.py [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

TIMELINE = Path(__file__).resolve().parent / "timeline.jsonl"
INSTALL_EVENTS = ("installed", "unpacked", "install_failed", "headless_done")
COMPILABLE_EVENTS = ("first_compilable_output", "pass_first_compiles")


def parse(path: Path = TIMELINE) -> list[dict]:
    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        e["t"] = dt.datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        events.append(e)
    if not events:
        raise SystemExit(f"{path}: empty timeline")
    if any(b["t"] < a["t"] for a, b in zip(events, events[1:])):
        raise SystemExit(f"{path}: timestamps are not monotonic")
    return events


def minutes(start: dt.datetime, t: dt.datetime | None) -> float | None:
    return None if t is None else round((t - start).total_seconds() / 60, 1)


def summary(path: Path = TIMELINE) -> dict:
    events = parse(path)
    start = events[0]["t"]
    last = events[-1]["t"]
    lifters: dict[str, dict] = {}
    for e in events:
        if e["lifter"] == "spike":
            continue
        L = lifters.setdefault(e["lifter"], {"first_event": e["t"], "last_event": e["t"], "events": 0,
                                             "install_event": None, "install_at": None,
                                             "first_compilable_output_at": None, "ten_functions_done_at": None})
        L["last_event"] = e["t"]
        L["events"] += 1
        if e["event"] in INSTALL_EVENTS and L["install_event"] is None:
            L["install_event"], L["install_at"] = e["event"], e["t"]
        if e["event"] in COMPILABLE_EVENTS and L["first_compilable_output_at"] is None:
            L["first_compilable_output_at"] = e["t"]
        if e["event"] == "ten_functions_done" and L["ten_functions_done_at"] is None:
            L["ten_functions_done_at"] = e["t"]
    out = {"spike_start": events[0]["ts"], "last_event": events[-1]["ts"],
           "spike_span_minutes": minutes(start, last), "event_count": len(events), "lifters": {}}
    for name, L in lifters.items():
        out["lifters"][name] = {
            "first_event_minutes": minutes(start, L["first_event"]),
            "install_event": L["install_event"],
            "install_minutes": minutes(start, L["install_at"]),
            "time_to_first_compilable_output_minutes": minutes(start, L["first_compilable_output_at"]),
            "own_first_compilable_minutes": minutes(L["first_event"], L["first_compilable_output_at"]),
            "ten_functions_done_minutes": minutes(start, L["ten_functions_done_at"]),
            "own_span_minutes": minutes(L["first_event"], L["last_event"]),
            "events": L["events"],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    s = summary()
    if args.json:
        print(json.dumps(s, indent=2))
        return
    print(f"spike start {s['spike_start']}  last event {s['last_event']}  span {s['spike_span_minutes']} min  ({s['event_count']} events)")
    print(f"{'lifter':18} {'first':>6} {'install':>8} {'first_compilable':>16} {'own_first_comp':>14} {'ten_done':>9} {'own_span':>9}  install_event")
    for name, L in s["lifters"].items():
        f = lambda v: "-" if v is None else f"{v:.1f}"
        print(f"{name:18} {f(L['first_event_minutes']):>6} {f(L['install_minutes']):>8} "
              f"{f(L['time_to_first_compilable_output_minutes']):>16} {f(L['own_first_compilable_minutes']):>14} "
              f"{f(L['ten_functions_done_minutes']):>9} {f(L['own_span_minutes']):>9}  {L['install_event'] or '-'}")
    print("(minutes from spike start, except own_first_comp = first compilable output minus the lifter's own first event; '-' = not reached)")


if __name__ == "__main__":
    main()
