#!/usr/bin/env python3
"""Gate: the guest-poll rule ran in every gated run, handed off in none of the
runs it was accepted on, left every registered replay with the counters its
registration recorded, and still ends the NEW FILE wait on the tree as it is.

Subtitle: read the run records the tick-replay, reach and stage-boot receipts
name, the acceptance receipt's list of runs the rule must leave alone, and the
NEW FILE receipt with the build manifest it was measured on; hold each to the
promises of the 2026-10-02 NEW FILE fix (runtime/sa2_threads.h
`sa2_sched_poll`, runtime/sa2_poll_handoff.c).

What this gate is for
---------------------
The rule is a hand-off point at a guest function, 0x00427EE0, and a build has it
only if its link wrapped that function: tools/s4/link_s4.py passes
-Wl,-wrap=sub_427ee0 in every link, and runtime/sa2_poll_handoff.c makes a link
without the flag fail. The registered runs never reach a pure-spin load, so on
them a build without the rule, or with a rule that no longer fires, looks like a
good one. Four checks, and each is shown failing on every pass:

1. **Present.** Every record read -- each registered replay's lifted run and
   baseline run, the reach run, each stage-boot run -- has
   `threads.poll_handoff` (harness/crt_probe.c prints it whenever a wrapped poll
   ran), with the rule on and polls above zero. Each of those runs loads through
   the AsynDo wait loops, so a record with no polls ran no wrapper: a link
   without it, or every call of 0x00427EE0 bypassing the wrap.
2. **Registered.** Each registered replay's record carries the counters its
   registration recorded (tools/s4/tick_replay.py `poll_counters`, in the
   tick-replay receipt) and the reach that receipt records for it, so the record
   read is the registered one and its counters are pinned where the record is not
   (the boot's has no pin of its own); a registration that records no reach
   fails. The stage boot's record must hash to its run_sha256, and the reach
   run's must read the reach its receipt records.
3. **Silent where accepted.** The records of the runs the acceptance receipt
   (Documentation/receipts/s5-poll-handoff-v1.json, `silent`) shows the rule
   leaving alone -- at least ACCEPTED_SILENT: the boot, Metal Harbor, the reach
   run and the Metal Harbor stage boot -- hand off at no poll, and each of
   those runs must have a gated record that was read: one dropped from its
   receipt or de-registered fails rather than passing unseen. The rule was
   accepted as conformance on that evidence; a hand-off in one of them moves an
   interleaving the acceptance measured unmoved, which is the scheduler-policy
   class Documentation/S4_CARRY_FORWARD.md item 8 reserves to the user. A replay
   registered later through a load the rule's hand-offs end -- any path from the
   title through the file select to the menu -- is held to check 2, not this
   one: without the rule it hangs, so there is no unmoved interleaving to
   protect, and failing it here would send the user a question nobody has to
   ask (the 2026-10-02 reviews, S1 of each).
4. **Working, now.** The NEW FILE receipt
   (Documentation/receipts/s5-newfile-wait-v1.json) shows the NEW FILE script
   reaching the main menu through hand-offs that each ran another thread, its
   run record still reads those counters, and the receipt is current: its build
   manifest -- the reach run's (tools/s4/reach_receipt.py `manifest`: the
   executable, the lifted and whole-image objects, the edge table, the fold
   code), the runtime digest and the run's own pipeline -- equals the tree's
   (tools/s5/poll_handoff_check.py `regression_manifest`). No registered run
   reaches the hand-off path, so this is the only check that sees the wrapper's
   register read, the wait loop's dispatched call or the quiet test regress; a
   change to any of them makes the receipt stale until the run is repeated, a
   24-second run on the boot's build (the 2026-10-02 review, S2).

It never runs the guest. Re-running what it reads, in this order:

    source tools/env.sh
    .venv/bin/python tools/s4/reach_receipt.py --replay boot --write
    .venv/bin/python tools/s4/tick_replay.py --receipt
    .venv/bin/python tools/s4/stage_boot.py --write
    .venv/bin/python tools/s5/poll_handoff_check.py regression --write

Usage: .venv/bin/python Documentation/scripts/check_poll_handoff.py
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECEIPTS = Path("Documentation") / "receipts"
PATHS = {"registry": Path("Documentation") / "replays.json",
         "tick": RECEIPTS / "s4-tick-replay-v1.json",
         "reach": RECEIPTS / "s4-run-reach-v1.json",
         "stage_boot": RECEIPTS / "s4-stage-boot-v1.json",
         "acceptance": RECEIPTS / "s5-poll-handoff-v1.json",
         "regression": RECEIPTS / "s5-newfile-wait-v1.json"}
# The records the rule was accepted on (the acceptance receipt's runs, 2026-10-02).
# The receipt may name more; naming fewer would loosen check 3, which is the user's.
ACCEPTED_SILENT = ("replay:boot", "replay:metalharbor", "reach", "stage-boot:metalharbor")
REACH_KEYS = ("iterations", "import_calls", "frames_presented", "stop")
RERUN = ".venv/bin/python tools/s5/poll_handoff_check.py regression --write"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reach_of(rec: dict) -> dict:
    """tools/s4/tick_replay.py reach_of: the reach fields of a run record."""
    return {"iterations": (rec.get("pump") or {}).get("iterations", 0),
            "import_calls": (rec.get("calls") or {}).get("total", 0),
            "frames_presented": ((rec.get("d3d9") or {}).get("frames") or {}).get("present", 0),
            "stop": (rec.get("trap") or {}).get("note", "")[:300]}


def counters(rec: dict) -> dict | None:
    ph = (rec.get("threads") or {}).get("poll_handoff")
    return ph if isinstance(ph, dict) else None


def gated_records(docs: dict) -> tuple[list[dict], list[str]]:
    """Every run record the gate reads, with what its receipt says of it, and every
    registered replay the tick-replay receipt holds no run of."""
    items: list[dict] = []
    problems: list[str] = []
    held = {rp.get("name"): rp for rp in docs["tick"].get("replays") or []}
    for entry in docs["registry"].get("replays") or []:
        name = entry.get("name")
        if name not in held:
            problems.append(f"replay {name} is registered and the tick-replay receipt holds no "
                            "run of it")
            continue
        lifted = held[name].get("lifted") or {}
        runs = [("lifted run", lifted)]
        if isinstance(lifted.get("baseline"), dict):
            runs.append(("baseline run", lifted["baseline"]))
        for what, r in runs:
            items.append({"id": f"replay:{name}", "who": f"{name} {what}", "path": r.get("run_json"),
                          "pin": r.get("run_json_sha256"), "registered": True,
                          "has_counters": "poll_handoff" in r, "counters": r.get("poll_handoff"),
                          "reach": r.get("reach")})
    reach = docs["reach"].get("reach") or {}
    items.append({"id": "reach", "who": "the reach run", "path": reach.get("run_json"),
                  "pin": None, "registered": False,
                  "reach": {k: reach.get(k) for k in REACH_KEYS}})
    for s in docs["stage_boot"].get("stages") or []:
        items.append({"id": f"stage-boot:{s.get('stage')}", "who": f"the {s.get('stage')} stage boot",
                      "path": s.get("run"), "pin": s.get("run_sha256"), "registered": False,
                      "reach": None})
    return items, problems


def record_problems(rec: dict, item: dict, silent: set[str]) -> list[str]:
    """Checks 1 to 3 on one record; [] when it holds."""
    who = f"{item['who']} ({item['path']})"
    ph = counters(rec)
    if ph is None:
        return [f"{who}: its record has no threads.poll_handoff, so its build ran no wrapped poll "
                "of 0x00427EE0: a link without -Wl,-wrap=sub_427ee0 and runtime/sa2_poll_handoff.c, "
                "every call of the guest function bypassing the wrap, or a record older than the "
                "rule"]
    out = []
    if ph.get("rule") != 1:
        out.append(f"{who}: the guest-poll rule was {ph.get('rule')!r} (SA2_POLL_HANDOFF), not on")
    polls = ph.get("polls")
    if not isinstance(polls, int) or polls <= 0:
        out.append(f"{who}: {polls!r} wrapped polls ran; the run loads through the AsynDo wait "
                   "loops, so its build did not run the wrapper")
    if item["registered"]:
        if not item.get("has_counters"):
            out.append(f"{who}: its registration recorded no guest-poll counters; re-register it "
                       "(tools/s4/tick_replay.py --receipt)")
        elif ph != item.get("counters"):
            out.append(f"{who}: its counters {ph} are not the ones its registration recorded "
                       f"{item.get('counters')}")
        # The reach is what ties the record read to the registration (the boot's record
        # has no pin of its own), so a registration that records none cannot pass.
        if item.get("reach") is None:
            out.append(f"{who}: its registration recorded no reach, so nothing shows the record "
                       "read is the registered one; re-register it (tools/s4/tick_replay.py "
                       "--receipt)")
    if item.get("reach") is not None and reach_of(rec) != item["reach"]:
        out.append(f"{who}: it reads reach {reach_of(rec)} and its receipt records "
                   f"{item['reach']}, so it is not the record the receipt describes")
    if item["id"] in silent and ph.get("hand_offs") != 0:
        out.append(f"{who}: the guest-poll rule handed off {ph.get('hand_offs')!r} times in a run "
                   "it was accepted as leaving alone (Documentation/receipts/s5-poll-handoff-v1.json "
                   "`silent`): its interleaving moved, which is the scheduler-policy class "
                   "Documentation/S4_CARRY_FORWARD.md item 8 reserves to the user")
    return out


def acceptance_problems(doc: dict | None) -> tuple[set[str], list[str]]:
    """The records the acceptance receipt holds silent, and why it cannot be read so."""
    if doc is None:
        return set(ACCEPTED_SILENT), [f"{PATHS['acceptance']} is missing, so nothing records the "
                                      "runs the rule was accepted on"]
    out = []
    if doc.get("id") != "s5-poll-handoff-v1" or doc.get("ok") is not True:
        out.append(f"{PATHS['acceptance']} is not an acceptance that holds: id {doc.get('id')!r}, "
                   f"ok {doc.get('ok')!r}")
    silent = set(doc.get("silent") or [])
    missing = sorted(set(ACCEPTED_SILENT) - silent)
    if missing:
        out.append(f"{PATHS['acceptance']} holds {missing} silent no longer; the rule was "
                   "accepted on them, and dropping one is the user's (AGENTS.md rule 10)")
    return silent | set(ACCEPTED_SILENT), out


def flat(obj, prefix=""):
    if isinstance(obj, dict) and obj:
        for k, v in obj.items():
            yield from flat(v, f"{prefix}.{k}" if prefix else str(k))
    else:
        yield prefix, obj


def regression_problems(doc: dict | None, live: dict | None, root: Path) -> tuple[list[str], dict]:
    """Check 4: the NEW FILE receipt holds, its record agrees, and it is current."""
    if doc is None:
        return [f"{PATHS['regression']} is missing: nothing shows the rule still ends the NEW "
                f"FILE wait. Run {RERUN}"], {}
    out = []
    run = doc.get("run") or {}
    ph = run.get("poll_handoff") or {}
    menu = run.get("current_menu") or {}
    if doc.get("id") != "s5-newfile-wait-v1" or doc.get("ok") is not True or run.get("ok") is not True:
        out.append(f"{PATHS['regression']} does not hold: ok {doc.get('ok')!r}, run problems "
                   f"{run.get('problems')}")
    if menu.get("main_menu_at") is None:
        out.append("the NEW FILE run never reached the main menu (CurrentMenu never read 1)")
    if ph.get("rule") != 1 or not ph.get("hand_offs") or ph.get("switched") != ph.get("hand_offs"):
        out.append(f"the NEW FILE run's counters do not show the rule handing off, each hand-off "
                   f"running another thread: {ph}")
    if (run.get("asyndo") or {}).get("ready_with_no_slice"):
        out.append(f"the NEW FILE run left AsynDo threads READY with no slice: {run['asyndo']}")
    if "sub_427ee0" not in ((run.get("build") or {}).get("wrapped") or []):
        out.append(f"the NEW FILE run's link did not wrap sub_427ee0: {run.get('build')}")
    rec_path = run.get("run_json")
    try:
        rec = json.loads((root / str(rec_path)).read_text())
        if counters(rec) != ph:
            out.append(f"the NEW FILE run's record {rec_path} reads {counters(rec)}, not the "
                       "receipt's counters")
    except (OSError, json.JSONDecodeError, TypeError) as e:
        out.append(f"the NEW FILE run's record {rec_path!r} cannot be read: {e}")
    stale = []
    if live is None:
        out.append("the tree's build manifest could not be computed, so the NEW FILE receipt "
                   "cannot be shown current")
    else:
        was, now = dict(flat(doc.get("manifest") or {})), dict(flat(live))
        stale = sorted(k for k in set(was) | set(now) if was.get(k) != now.get(k))
        if stale:
            out.append(f"the NEW FILE receipt is stale: {stale[:6]}"
                       f"{' and more' if len(stale) > 6 else ''} moved since it ran, so it "
                       f"describes a build that no longer exists. Run {RERUN}")
    return out, {"hand_offs": ph.get("hand_offs"), "main_menu_at": menu.get("main_menu_at"),
                 "stale": len(stale)}


def live_manifest() -> dict | None:
    sys.path.insert(0, str(ROOT / "tools" / "s5"))
    try:
        import poll_handoff_check as PH  # noqa: E402
        return PH.regression_manifest()
    except (Exception, SystemExit):          # noqa: BLE001 -- the check reports it
        return None


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def problems(root: Path = ROOT, live: dict | None = None,
             compute_live: bool = True) -> tuple[list[str], dict]:
    """Every problem under `root`, and what was read."""
    docs = {k: read_json(root / p) for k, p in PATHS.items()}
    out: list[str] = []
    for k in ("registry", "tick", "reach", "stage_boot"):
        if docs[k] is None:
            out.append(f"{PATHS[k]} cannot be read")
            docs[k] = {}
    silent, bad = acceptance_problems(docs["acceptance"])
    out += bad
    items, bad = gated_records(docs)
    out += bad
    read = 0
    read_ids: set[str] = set()
    for item in items:
        if not item["path"]:
            out.append(f"{item['who']}: its receipt names no run record")
            continue
        try:
            raw = (root / item["path"]).read_bytes()
            rec = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            out.append(f"{item['who']}: its record {item['path']} cannot be read: {e}")
            continue
        read += 1
        read_ids.add(item["id"])
        if item["pin"] and sha256(raw) != item["pin"]:
            out.append(f"{item['who']} ({item['path']}): the record does not hash to its pin")
        out += record_problems(rec, item, silent)
    # Check 3 holds a run silent through its record: a run held silent whose record was
    # not read (dropped from its receipt, or de-registered) would otherwise pass unseen.
    for sid in sorted(silent - read_ids):
        out.append(f"{sid}: held silent (Documentation/receipts/s5-poll-handoff-v1.json "
                   f"`silent`, ACCEPTED_SILENT) and no gated record of it was read, so nothing "
                   "shows it handed off at no poll")
    if compute_live and live is None:
        live = live_manifest()
    more, seen = regression_problems(docs["regression"], live, root)
    out += more
    return out, {"records": read, "silent": sorted(i["id"] for i in items if i["id"] in silent),
                 "newfile": seen}


# ------------------------------------------------------------------ falsification

def good_tree() -> dict:
    """A tree that passes: two replays the rule was accepted on, one registered later
    whose loads the rule's hand-offs end, the reach run and a stage boot."""
    def rec(polls, hand_offs=0, iterations=1300):
        return {"pump": {"iterations": iterations}, "calls": {"total": 5},
                "d3d9": {"frames": {"present": 9}}, "trap": {"note": "exit"},
                "threads": {"poll_handoff": {"rule": 1, "polls": polls, "not_done": polls // 2,
                                             "quiet": hand_offs, "hand_offs": hand_offs,
                                             "switched": hand_offs, "first_hand_off": {
                                                 "poll": 0, "import_call": 0, "frame": 0}}}}
    records = {"run/boot.json": rec(72), "run/mh.json": rec(130, iterations=15645),
               "run/mh.base.json": rec(130, iterations=15645),
               "run/garden.json": rec(300, hand_offs=190), "run/reach.json": rec(72),
               "run/stage.json": rec(40), "run/newfile.json": rec(298, hand_offs=190)}
    manifest = {"build": {"runtime_sha256": "1" * 64, "pipeline": {"tools/x.py": "2" * 64}},
                "runtime_sha256": "1" * 64, "pipeline": {"tools/s5/poll_handoff_check.py": "3" * 64}}

    def lifted(path, base=None):
        r = records[path]
        d = {"run_json": path, "poll_handoff": copy.deepcopy(r["threads"]["poll_handoff"]),
             "reach": reach_of(r)}
        if base:
            d["baseline"] = lifted(base)
        return d
    files = {
        "records": records,
        "registry": {"replays": [{"name": "boot"}, {"name": "metalharbor"}, {"name": "garden"}]},
        "tick": {"replays": [{"name": "boot", "lifted": lifted("run/boot.json")},
                             {"name": "metalharbor",
                              "lifted": lifted("run/mh.json", "run/mh.base.json")},
                             {"name": "garden", "lifted": lifted("run/garden.json")}]},
        "reach": {"reach": dict(reach_of(records["run/reach.json"]), run_json="run/reach.json")},
        "stage_boot": {"stages": [{"stage": "metalharbor", "run": "run/stage.json"}]},
        "acceptance": {"id": "s5-poll-handoff-v1", "ok": True, "silent": list(ACCEPTED_SILENT)},
        "regression": {"id": "s5-newfile-wait-v1", "ok": True, "manifest": manifest,
                       "run": {"ok": True, "run_json": "run/newfile.json",
                               "poll_handoff": copy.deepcopy(
                                   records["run/newfile.json"]["threads"]["poll_handoff"]),
                               "current_menu": {"main_menu_at": 1362},
                               "asyndo": {"ready_with_no_slice": []},
                               "build": {"wrapped": ["sub_427ee0", "sub_433ee0"]}}},
        "live": copy.deepcopy(manifest),
    }
    return files


def write_tree(root: Path, files: dict, repin: bool = True) -> dict:
    """Write `files` under `root`; with `repin`, the pinned records' pins are the
    hashes of the records as written (the Metal Harbor runs and the stage boot)."""
    raw = {p: (json.dumps(r, sort_keys=True).encode() if isinstance(r, dict) else r)
           for p, r in files["records"].items()}
    if repin:
        mh = next(rp for rp in files["tick"]["replays"] if rp["name"] == "metalharbor")["lifted"]
        for d in (mh, mh.get("baseline") or {}):
            if d.get("run_json") in raw:
                d["run_json_sha256"] = sha256(raw[d["run_json"]])
        for s in files["stage_boot"]["stages"]:
            if s.get("run") in raw:
                s["run_sha256"] = sha256(raw[s["run"]])
    for p, data in raw.items():
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_bytes(data)
    for k, rel in PATHS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if files.get(k) is None:
            (root / rel).unlink(missing_ok=True)
        else:
            (root / rel).write_text(json.dumps(files[k]))
    return files["live"]


def _ph(files, path):
    return files["records"][path]["threads"]["poll_handoff"]


def _tick(files, name):
    return next(rp for rp in files["tick"]["replays"] if rp["name"] == name)["lifted"]


# (name, mutation, a fragment the failure must name, re-pin after the mutation)
CASES = [
    ("the wrapper's object absent", lambda f: f["records"]["run/boot.json"]["threads"].pop(
        "poll_handoff"), "has no threads.poll_handoff", True),
    ("the rule switched off", lambda f: _ph(f, "run/boot.json").update(rule=0),
     "not on", True),
    ("no polls", lambda f: _ph(f, "run/mh.base.json").update(polls=0),
     "0 wrapped polls ran", True),
    ("the registration recorded no counters", lambda f: _tick(f, "boot").pop("poll_handoff"),
     "recorded no guest-poll counters", True),
    ("counters other than the registration's", lambda f: _ph(f, "run/boot.json").update(
        polls=71), "not the ones its registration recorded", True),
    ("a record that is not the registered one", lambda f: f["records"]["run/boot.json"][
        "pump"].update(iterations=1299), "not the record the receipt describes", True),
    ("a pinned record changed", lambda f: f["records"]["run/mh.json"]["calls"].update(total=6),
     "does not hash to its pin", False),
    ("a hand-off in the stage boot", lambda f: _ph(f, "run/stage.json").update(
        hand_offs=1, quiet=1, switched=1), "handed off 1 times", True),
    ("a hand-off in the reach run", lambda f: _ph(f, "run/reach.json").update(
        hand_offs=2, quiet=2, switched=2), "handed off 2 times", True),
    ("a hand-off in a registered replay accepted silent", lambda f: (
        _ph(f, "run/boot.json").update(hand_offs=1, quiet=1, switched=1),
        _tick(f, "boot")["poll_handoff"].update(hand_offs=1, quiet=1, switched=1)),
     "handed off 1 times", True),
    ("the acceptance no longer naming the boot", lambda f: f["acceptance"]["silent"].remove(
        "replay:boot"), "silent no longer", True),
    ("a run held silent whose record was not read: the stage boot dropped from its receipt",
     lambda f: f["stage_boot"]["stages"].clear(), "no gated record of it was read", True),
    ("a replay held silent and de-registered", lambda f: f["registry"]["replays"].remove(
        {"name": "boot"}), "no gated record of it was read", True),
    ("a registration that records no reach", lambda f: _tick(f, "boot").pop("reach"),
     "recorded no reach", True),
    ("the acceptance missing", lambda f: f.update(acceptance=None), "is missing", True),
    ("a registered replay with no run", lambda f: f["registry"]["replays"].append(
        {"name": "chao"}), "holds no run of it", True),
    ("the NEW FILE receipt stale", lambda f: f["live"]["build"]["pipeline"].update(
        {"tools/x.py": "4" * 64}), "is stale", True),
    ("the NEW FILE run short of the main menu", lambda f: f["regression"]["run"][
        "current_menu"].update(main_menu_at=None), "never reached the main menu", True),
    ("the NEW FILE run with no hand-off", lambda f: f["regression"]["run"]["poll_handoff"].update(
        hand_offs=0), "do not show the rule handing off", True),
    ("the NEW FILE record not the receipt's", lambda f: _ph(f, "run/newfile.json").update(
        polls=1), "not the receipt's counters", True),
    ("the NEW FILE receipt missing", lambda f: f.update(regression=None), "is missing", True),
]


def falsifications() -> tuple[list[str], int]:
    """The control tree must pass (a later replay that hands off included, which
    check 3 must leave to check 2), and each case in CASES must fail naming its
    fragment. Returns what did not behave, and how many cases ran."""
    out = []
    with tempfile.TemporaryDirectory(prefix="poll-gate-") as td:
        root = Path(td) / "control"
        live = write_tree(root, good_tree())
        found, _ = problems(root, live)
        if found:
            out.append(f"the control tree fails: {found}")
        for i, (name, mutate, needle, repin) in enumerate(CASES):
            root = Path(td) / f"case{i}"
            files = good_tree()
            if not repin:
                write_tree(Path(td) / f"pins{i}", files)      # the pins of the unchanged tree
            mutate(files)
            live = write_tree(root, files, repin=repin)
            found, _ = problems(root, live)
            if not any(needle in p for p in found):
                out.append(f"falsification '{name}' did not fail naming {needle!r}: {found}")
    return out, len(CASES)


def main() -> int:
    bad, cases = falsifications()
    found, seen = problems()
    for p in found:
        print(f"FAIL: {p}")
    for p in bad:
        print(f"FAIL (falsification): {p}")
    if found or bad:
        return 1
    print(f"OK: {seen['records']} gated run records ran the guest-poll wrapper; "
          f"{len(seen['silent'])} of them, the runs it was accepted on, saw no hand-off; the "
          f"NEW FILE run reaches the main menu at tick {seen['newfile']['main_menu_at']} through "
          f"{seen['newfile']['hand_offs']} hand-offs on the tree as it is; "
          f"{cases} of {cases} falsifications fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
