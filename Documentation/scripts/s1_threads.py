#!/usr/bin/env python3
"""S1 receipt 3: thread creation sites, their start routines, and the sync surface (design 3.5).

    .venv/bin/python Documentation/scripts/s1_threads.py \
        --out Documentation/receipts/s1-threads-v1.json

CreateThread is imported; `_beginthread` / `_beginthreadex` are statically
linked CRT functions that call it. So the script finds every `call
[CreateThread]` (direct or through a `jmp [slot]` thunk), takes the function
around each one as a wrapper (map entry, or nearest preceding direct-call
target without the map), and lists the wrapper's direct callers as the
real thread-creation sites. At every site the start routine is the third
push counting back from the call (CreateThread and _beginthreadex both take
it as the third argument, and cdecl pushes right to left) when that push
is an immediate inside a code section; otherwise it is unresolved. The
sync surface is every imported symbol matching SYNC_PATTERN, with its call
sites; `lock`-prefixed instructions are counted as the inline atomic
surface.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s1_sweep as sw  # noqa: E402

SYNC_PATTERN = re.compile(
    r"^(Create(Event|Mutex|Semaphore|WaitableTimer)(Ex)?[AW]?|Open(Event|Mutex|Semaphore)[AW]?"
    r"|WaitFor(Single|Multiple)Objects?(Ex)?|SignalObjectAndWait|(Set|Reset|Pulse)Event"
    r"|Release(Mutex|Semaphore)|(Enter|Leave|Initialize|Delete|TryEnter)CriticalSection(AndSpinCount)?"
    r"|Interlocked\w+|Sleep(Ex)?|SwitchToThread|Tls(Alloc|Free|GetValue|SetValue)"
    r"|(Create|Suspend|Resume|Exit|Terminate|Open)Thread|(Get|Set)Thread\w+|GetCurrentThread(Id)?"
    r"|GetExitCodeThread|timeSetEvent|timeKillEvent|_beginthread(ex)?|_endthread(ex)?)$")
THREAD_CREATORS = ("CreateThread",)
START_ROUTINE_ARG = 3  # third argument of CreateThread and of _beginthreadex


def start_routine(image: sw.Image, call: int) -> int | None:
    """The immediate pushed as the start routine before `call`, when it is a code address."""
    pos = image.at(call)
    if pos is None:
        return None
    pushes = image.pushed_arguments(pos)
    if len(pushes) < START_ROUTINE_ARG:
        return None
    imm = pushes[-START_ROUTINE_ARG]
    return imm if imm is not None and image.in_code(imm) else None


def start_regions(fmap: sw.FunctionMap, sr: int | None) -> dict:
    """Region of a start routine, plus the neighbouring mapped function's region when the map has no
    function at that address (a routine reached only through the pointer passed here)."""
    if sr is None:
        return {"start_region": None, "start_neighbour_region": None}
    region = fmap.region_of(sr)
    return {"start_region": region, "start_neighbour_region": fmap.neighbour_region(sr) if region == "unmapped" else None}


def thread_sites(image: sw.Image, fmap: sw.FunctionMap) -> dict:
    """CreateThread call sites, the wrappers around them, and the wrappers' callers."""
    direct, wrappers = [], {}
    for name in THREAD_CREATORS:
        for call in image.import_call_sites(name):
            f = fmap.enclosing(image, call)
            sr = start_routine(image, call)
            direct.append({"call": call, "import": name, "function": f["entry"], "region": f["region"],
                           "start_routine": sr, **start_regions(fmap, sr)})
            if f["entry"] is None:
                continue
            w = wrappers.setdefault(f["entry"], {"entry": f["entry"], "region": f["region"],
                                                 "createthread_calls": [], "callers": []})
            w["createthread_calls"].append(call)
    for w in wrappers.values():
        for call in sorted(image.direct_calls.get(w["entry"], [])):
            f = fmap.enclosing(image, call)
            sr = start_routine(image, call)
            w["callers"].append({"call": call, "function": f["entry"], "region": f["region"], "start_routine": sr,
                                 **start_regions(fmap, sr)})
    return {"direct": direct, "wrappers": sorted(wrappers.values(), key=lambda w: w["entry"])}


def lock_prefixed(image: sw.Image) -> list[int]:
    return [ins.ip for ins in image.instructions if ins.has_lock_prefix]


def analyse(image: sw.Image, fmap: sw.FunctionMap) -> dict:
    ts = thread_sites(image, fmap)
    hx = lambda v: None if v is None else hex(v)  # noqa: E731

    def site_row(s: dict) -> dict:
        return {"call": hex(s["call"]), "region": s["region"], "function": hx(s["function"]),
                "start_routine": hx(s["start_routine"]), "start_region": s["start_region"],
                "start_neighbour_region": s["start_neighbour_region"]}

    all_starts = [s["start_routine"] for s in ts["direct"] if s["start_routine"] is not None]
    user_sites = [c for w in ts["wrappers"] for c in w["callers"]]
    user_starts = [c["start_routine"] for c in user_sites if c["start_routine"] is not None]
    sync = []
    for name in image.imports_matching(SYNC_PATTERN):
        sites = image.import_call_sites(name)
        sync.append({"import": name, "dll": image.import_dll[name], "call_sites": sw.counted(fmap, sites),
                     "other_references": len([u for u in image.import_uses(name) if u[1] == "other"])})
    locks = lock_prefixed(image)
    return {
        "CreateThread_call_sites": sw.counted(fmap, [s["call"] for s in ts["direct"]]),
        "CreateThread_sites": [{**site_row(s), "import": s["import"]} for s in ts["direct"]],
        "wrappers": [{"entry": hex(w["entry"]), "region": w["region"],
                      "createthread_calls": [hex(c) for c in w["createthread_calls"]],
                      "callers": sw.counted(fmap, [c["call"] for c in w["callers"]]),
                      "caller_sites": [site_row(c) for c in w["callers"]]} for w in ts["wrappers"]],
        "thread_creation_sites_through_wrappers": sw.counted(fmap, [c["call"] for c in user_sites]),
        "start_routines_resolved": sw.counted(fmap, sorted(set(user_starts))),
        "start_routines_unmapped_by_neighbour_region": {r: sum(1 for c in user_sites if c["start_neighbour_region"] == r)
                                                        for r in sorted({c["start_neighbour_region"] for c in user_sites
                                                                         if c["start_neighbour_region"]})},
        "start_routines_unresolved_sites": sw.counted(fmap, [c["call"] for c in user_sites if c["start_routine"] is None]),
        "wrapper_internal_start_routines": sw.counted(fmap, sorted(set(all_starts))),
        "sync_imports": sync,
        "sync_imports_absent": sorted(n for n in ("CreateSemaphoreA", "CreateSemaphoreW", "WaitForMultipleObjects",
                                                  "CreateMutexA", "InterlockedCompareExchange", "InterlockedExchangeAdd",
                                                  "TryEnterCriticalSection", "SwitchToThread")
                                      if n not in image.import_dll),
        "lock_prefixed_instructions": sw.counted(fmap, locks),
        "lock_prefixed_sites": [hex(a) for a in locks[:20]],
    }


METHOD = (
    "Linear iced-x86 sweep of every executable section. CreateThread call sites are `call [IAT slot]` "
    "and `call thunk` where the thunk is a `jmp [IAT slot]`. The function around each site (map entry, "
    "or the nearest preceding direct-call target without the map) is a wrapper (`_beginthread` / "
    "`_beginthreadex`-shaped when in the CRT); the wrapper's direct `call` sites are the thread-creation "
    "sites. At each call the pushes since the previous call, return or branch are collected in stack "
    "order and the third argument (CreateThread's lpStartAddress, _beginthreadex's start_address) is "
    "taken when it is an immediate inside a code section. Sync imports are every import name matching "
    "SYNC_PATTERN (events, mutexes, semaphores, waits, critical sections, Interlocked*, Sleep, Tls*, "
    "thread control); their call sites are counted the same way, and `other_references` counts loads "
    "of the slot that are neither call nor jmp (function-pointer captures). `lock`-prefixed instructions "
    "are the inline atomic surface. Regions come from the function map when present."
)

LIMITS = [
    "A start routine passed in a register or loaded from memory is unresolved; the site is still counted.",
    "Only one wrapper level is followed: a game-side helper that itself wraps _beginthreadex shows as one "
    "site with an unresolved or helper-internal start routine, and its own callers are not expanded.",
    "Calls to CreateThread through a function pointer captured from the IAT (`other_references`) are not "
    "call sites.",
    "Without the function map the enclosing function is the nearest preceding direct-call target.",
    "Whether a start routine belongs to the CRI audio path or the game is read from its region only; "
    "no dynamic evidence. A start routine the map has no function for (reached only through the pointer) "
    "is 'unmapped' and the region of the nearest mapped function before it is given as the neighbour region.",
]


def main(argv=None) -> int:
    args = sw.base_parser(__doc__.splitlines()[0]).parse_args(argv)
    image = sw.Image(args.exe)
    fmap = sw.FunctionMap(args.function_map)
    outputs = analyse(image, fmap)
    doc = sw.receipt(image, fmap, "s1-threads-v1",
                     "Where are threads created, what do they run, and what synchronisation surface does the "
                     "runtime need? (design 3.5)", METHOD, outputs, LIMITS)
    sw.write_receipt(args.out, doc)
    o = outputs
    print(f"s1-threads: {sw.format_counts('CreateThread call sites', o['CreateThread_call_sites'])}: "
          + ", ".join(f"{s['call']} in {s['function']} ({s['region']}) start {s['start_routine']}" for s in o["CreateThread_sites"]))
    for w in o["wrappers"]:
        print(f"s1-threads: wrapper {w['entry']} ({w['region']}) {sw.format_counts('callers', w['callers'])}: "
              + ", ".join(f"{c['call']} ({c['region']}) start {c['start_routine']} ({c['start_region']}"
                          + (f", neighbour {c['start_neighbour_region']})" if c['start_neighbour_region'] else ")")
                          for c in w["caller_sites"]))
    print(f"s1-threads: {sw.format_counts('thread creation sites through wrappers', o['thread_creation_sites_through_wrappers'])}; "
          f"{sw.format_counts('distinct start routines resolved', o['start_routines_resolved'])}; "
          f"{sw.format_counts('sites with unresolved start routine', o['start_routines_unresolved_sites'])}; "
          f"unmapped start routines by neighbour region {o['start_routines_unmapped_by_neighbour_region']}")
    print("s1-threads: sync imports: " + ", ".join(f"{s['import']} {s['call_sites']['total']}" for s in o["sync_imports"]))
    print(f"s1-threads: sync imports absent: {o['sync_imports_absent']}")
    print(f"s1-threads: {sw.format_counts('lock-prefixed instructions', o['lock_prefixed_instructions'])} at {o['lock_prefixed_sites']}")
    print(f"s1-threads: receipt {sw.rel(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
