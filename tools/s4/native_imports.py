#!/usr/bin/env python3
"""The imports a native capture called, named from the executable.

Subtitle: read harness/replay/native_import_probe.c's per-slot call counts,
join each slot address to its import through the unpacked executable's own
import directory, and refuse a log that does not cover every slot exactly.

    .venv/bin/python tools/s4/native_imports.py CAPTURE_DIR     # print the called set

`tools/s4/import_status.py` reads it for M4's replay-reachable set, and
`Documentation/scripts/check_replay_results.py` recomputes it. `replay_profile`
holds the replay's device set, which decides what the native game calls before
the Mod Loader's Init, where no import log sees.

A replay registered with `"oracle": "vanilla"` counts its imports only from
runs that restored the game's own code first: `replay_calls` refuses an import
log whose capture `tools/oracle/oracle_mode.py vanilla_problems` refuses (the
vanilla probe first in the build list and the loader's orders, at the tree's
hash with no experiment's defines, its log the one the runner collected, the
kept ranges exactly the probe's, the import slots as its default treats them,
and the tick-1 rows naming exactly the restored ranges), asked through
`tools/s4/tick_replay.py native_vanilla_problems`. The user adopted the vanilla
oracle on 2026-09-23 (`Documentation/decisions/2026-09-23-S4-vanilla-oracle.md`),
and each replay moves to it when it is re-captured; an entry without the flag
is the Mod Loader's until then, and is read as before.

What makes a log unusable, each a refusal rather than a smaller set:
  * a header other than the probe's, or a `refused` row (the probe could not
    patch, and says why);
  * a slot the executable does not have, a slot twice, or a slot missing:
    the probe walks the same descriptors, so any of these means the log is
    not from this executable or was cut;
  * a slot whose thunk had been replaced by the time the file was written
    (`ours` 0): its count stopped when something re-pointed it, so a zero
    there proves nothing;
  * rows counted at different ticks.
"""

from __future__ import annotations

import csv
import functools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
LOG = "sa2-import-calls.csv"
HEADER = ["slot", "calls", "ours", "tick"]
PINS = ROOT / "tools" / "pins.json"


@functools.lru_cache(maxsize=4)
def slots(exe: Path = EXE) -> dict[int, str]:
    """Every import-address-table slot's address to `dll!name`, from the file.

    The logs are joined across sides by name, so a name held by two slots would
    merge their counts; this executable has none, and that is checked rather
    than assumed (2026-09-23 review)."""
    import pefile
    pe = pefile.PE(str(exe), fast_load=True)
    pe.parse_data_directories([pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    out = {}
    for d in pe.DIRECTORY_ENTRY_IMPORT:
        for imp in d.imports:
            # An import by ordinal is `Ordinal#N`, as the runtime's table names
            # it. pefile fills `name` for OLEAUT32's ordinals from its own
            # lookup table, which is not what the file says: SysAllocString
            # here is ordinal 2 in the executable (2026-09-22, caught by the
            # slot-by-slot comparison with the lifted table).
            name = f"Ordinal#{imp.ordinal}" if imp.import_by_ordinal else imp.name.decode()
            out[imp.address] = f"{d.dll.decode()}!{name}"
    if len(set(out.values())) != len(out):
        raise SystemExit(f"{exe}: two import slots share a name, so a join by name would "
                         "merge them")
    return out


def read(path: Path, table: dict[int, str] | None = None) -> dict[str, int]:
    """`dll!name` -> calls for every slot, or SystemExit naming what is wrong."""
    return read_at(path, table)[0]


def read_at(path: Path, table: dict[int, str] | None = None) -> tuple[dict[str, int], int]:
    """`read`, and the tick the counts were taken at (the same on every row)."""
    table = slots() if table is None else table
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != HEADER:
        raise SystemExit(f"{path}: header is {rows[0] if rows else None}, not {HEADER}")
    body = rows[1:]
    if body and body[0] and body[0][0] == "refused":
        raise SystemExit(f"{path}: the probe did not patch: {' '.join(sum(body[1:], []))}")
    out, seen, ticks = {}, set(), set()
    for r in body:
        if len(r) != 4:
            raise SystemExit(f"{path}: row {r} is not slot,calls,ours,tick")
        va = int(r[0], 16)
        if va not in table:
            raise SystemExit(f"{path}: slot {r[0]} is not one of the executable's")
        if va in seen:
            raise SystemExit(f"{path}: slot {r[0]} appears twice")
        if r[2] != "1":
            raise SystemExit(f"{path}: slot {r[0]} ({table[va]}) no longer held the probe's "
                             "thunk when the log was written, so its count is not a count")
        seen.add(va)
        ticks.add(int(r[3]))
        out[table[va]] = int(r[1])
    missing = sorted(set(table) - seen)
    if missing:
        raise SystemExit(f"{path}: {len(missing)} slot(s) absent, first {hex(missing[0])} "
                         f"({table[missing[0]]})")
    if len(ticks) != 1:
        raise SystemExit(f"{path}: the rows were counted at ticks {sorted(ticks)}, not one")
    return out, ticks.pop()


def called(path: Path, table: dict[int, str] | None = None) -> list[str]:
    return sorted(k for k, v in read(path, table).items() if v)


PROBE = ROOT / "harness" / "replay" / "native_import_probe.c"


def sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay_calls(entry: dict, table: dict[int, str] | None = None,
                 root: Path = ROOT, probe: Path = PROBE) -> list[str]:
    """The imports a registered replay's native run called, or SystemExit.

    `entry` is a `Documentation/replays.json` replay. Its `native.imports` names
    an import log and at least one repeat, each with the run's receipt. For
    each log:

      * it hashes to its pin, and its run collected it;
      * the tick capture beside it -- hashed here, not read from the receipt,
        which is not pinned -- is the replay's registered capture, which is the
        evidence that counting changed nothing the replay compares. For a
        replay with a `word_policy`, which is compared from its word logs under
        its own digest mask, that is the capture the run's word log gives under
        that mask (`word_captures`): the run is one of the replay's registered
        native runs and its log gives the registered capture's text, where the
        stored file's digest columns follow whichever mask the tree had when
        the run was taken;
      * its counts were taken at the replay's last tick, so the log covers the
        ticks the replay judges and no fewer;
      * it ran the pinned executable, and the probe now in the tree;
      * for a replay registered with `"oracle": "vanilla"`, its capture passes
        `tools/oracle/oracle_mode.py vanilla_problems` (asked through
        `tools/s4/tick_replay.py native_vanilla_problems`), the one check every
        tool that takes a vanilla capture shares, so the imports it counts are
        the game's own code's and not the Mod Loader's replacements'.

    What the game calls before Init depends on the capture's devices, and is
    `replay_profile`'s question, not this log's.

    Every repeat must call the same set. Counts are not compared: a spin on a
    critical section runs as often as the scheduler lets it, and the message
    count moves with the desktop (38 and 3 in the first two runs).
    """
    name = entry.get("name")
    imp = (entry.get("native") or {}).get("imports")
    if not imp:
        raise SystemExit(f"replay {name} registers no native import log, so the imports it "
                         "reaches are only the lifted build's")
    logs = [imp, *imp.get("repeats", [])]
    if len(logs) < 2:
        raise SystemExit(f"replay {name}'s import log has no repeat, so its set is not shown "
                         "to be the run's rather than one run's")
    tick_name = Path(entry["native"]["capture"]).name
    registered = root / entry["native"]["capture"]
    if not registered.is_file():
        raise SystemExit(f"replay {name}: its registered capture {entry['native']['capture']} "
                         "is missing, so the ticks an import log must cover are unknown")
    with registered.open(newline="") as f:
        length = sum(1 for _ in csv.reader(f)) - 1
    exe_pin = json.loads(PINS.read_text())["executables"]["sonic2app-unpacked.exe"]["sha256"]
    oracle = entry.get("oracle")
    if oracle not in (None, "vanilla"):
        raise SystemExit(f"replay {name}: oracle {oracle!r} is not a registry flag this tool "
                         "reads; the one flag is \"vanilla\", and its absence is the Mod "
                         "Loader's oracle")
    vanilla = None
    if oracle == "vanilla":
        import tick_replay                    # here: only a vanilla entry needs the replay tool
        vanilla = tick_replay.native_vanilla_problems
    words = word_captures(entry, root) if "word_policy" in entry else None
    sets = []
    for log in logs:
        path = root / log["log"]
        if not path.is_file() or sha256(path) != log.get("sha256"):
            raise SystemExit(f"replay {name}: import log {log['log']} is missing or no longer "
                             "hashes to its pin")
        rec = json.loads((root / log["receipt"]).read_text())
        arts = {a.get("file"): a for a in rec.get("artifacts", [])}
        got = (arts.get(Path(log["log"]).name, {}).get("check") or {}).get("sha256")
        if got != log["sha256"]:
            raise SystemExit(f"replay {name}: {log['receipt']} did not collect {log['log']}")
        if vanilla is not None:
            problems = vanilla((root / log["receipt"]).parent)
            if problems:
                raise SystemExit(f"replay {name} is registered with the vanilla oracle, and the "
                                 f"run that wrote {log['log']} is not a vanilla capture: "
                                 + "; ".join(problems))
        if words is not None:
            run, want = words
            got = run.get(path.parent.resolve())
            if got is None:
                raise SystemExit(f"replay {name}: the run that wrote {log['log']} is not one of "
                                 "its registered native runs, so nothing holds what it captured "
                                 "to the registered capture")
            if got != want:
                raise SystemExit(f"replay {name}: the run that wrote {log['log']} captured ticks "
                                 "whose word log gives another capture than the registered one "
                                 "under the replay's own digest mask, so the count may have "
                                 "perturbed the game")
        else:
            beside = path.parent / tick_name
            ticks = sha256(beside) if beside.is_file() else None
            if ticks != entry["native"]["sha256"]:
                raise SystemExit(f"replay {name}: the run that wrote {log['log']} captured "
                                 f"ticks hashing to {ticks}, not the registered capture's "
                                 f"{entry['native']['sha256']}, so the count may have perturbed "
                                 "the game")
        if (rec.get("inputs") or {}).get("executable_sha256") != exe_pin:
            raise SystemExit(f"replay {name}: {log['receipt']} did not run the executable "
                             "tools/pins.json pins")
        built = [b for b in rec.get("inputs", {}).get("built_probes", [])
                 if Path(b.get("source", "")).name == probe.name]
        if len(built) != 1 or built[0].get("source_sha256") != sha256(probe):
            raise SystemExit(f"replay {name}: {log['log']} was not written by the "
                             f"{PROBE.relative_to(ROOT)} now in the tree")
        counts, at = read_at(path, table)
        if at != length:
            raise SystemExit(f"replay {name}: {log['log']} was counted at tick {at}, and the "
                             f"replay judges {length} ticks")
        sets.append(sorted(k for k, v in counts.items() if v))
    if any(s != sets[0] for s in sets[1:]):
        diff = sorted(set().union(*sets) - set(sets[0]).intersection(*sets))
        raise SystemExit(f"replay {name}: the import logs' repeats call different sets "
                         f"({len(diff)} differ, first {diff[:3]})")
    return sets[0]


def word_captures(entry: dict, root: Path = ROOT) -> tuple[dict[Path, str], str]:
    """For a replay with a `word_policy`: the tick capture each registered
    native run's word log gives under the replay's own digest mask less its
    identity words (tools/s4/tick_replay.py `mask_for`, `effective_mask`), by
    the run's directory, and the registered capture's. The logs are held to
    their pins and receipts by tick_replay.native_log, the rule the comparer
    reads them under. A stage replay's native runs differ in the stored
    captures' digest columns, which the tree's boot mask built, wherever that
    mask holds words the stage's own mask leaves out (CRI's streaming during a
    load, among them)."""
    import tempfile
    import tick_replay as T               # here: only a word-policy entry needs its mask
    import wordlog as WL
    native = entry.get("native") or {}
    caps = [native, *native.get("repeats", [])]
    spec = T.identity_spec(entry, root)
    out: dict[Path, str] = {}
    with tempfile.TemporaryDirectory() as td:
        mask, _ = T.effective_mask(entry, spec, Path(td), root)
        for cap in caps:
            try:
                text = WL.capture_csv(T.native_log(entry, cap, root), mask, native=True)
            except SystemExit as e:
                raise SystemExit(f"replay {entry.get('name')}: {cap.get('capture')}: {e}") from None
            out[(root / str(cap["capture"])).parent.resolve()] = text
    return out, out[(root / str(native["capture"])).parent.resolve()]


TICK_REPLAY = "Documentation/receipts/s4-tick-replay-v1.json"
OUTCOMES = "Documentation/receipts/s4-native-outcomes-v1.json"
RECAPTURE = ("The captures registered today predate the device profile: re-capture this "
             "replay's native runs with harness/replay/native_device_profile.c loaded, so each "
             "receipt records session.device_profile, and re-register the replay from those runs.")


def replay_receipts(entry: dict) -> list[str]:
    """Every oracle receipt a registered replay names, in order and once each: its
    capture's, the capture's repeats', its import log's and the log's repeats'."""
    nat = entry.get("native") or {}
    imp = nat.get("imports") or {}
    out: list[str] = []
    for r in ([nat.get("receipt")] + [x.get("receipt") for x in nat.get("repeats", [])]
              + [imp.get("receipt")] + [x.get("receipt") for x in imp.get("repeats", [])]):
        if r and r not in out:
            out.append(r)
    return out


def replay_profile(entry: dict, root: Path = ROOT, outcomes: dict | None = None) -> dict:
    """The device set a registered replay ran with, on both sides, or SystemExit.

    The user's directive of 2026-09-23: "The device set is a recorded property of
    each capture: the runner records it in the receipt, the lifted replay reads the
    same profile back ... A capture whose profile the runtime cannot reproduce is
    refused, not approximated." So, for every native run the replay names:

      * `tools/s4/device_profile.py capture_profile` accepts its capture: the
        receipt records `session.device_profile`, the `sa2-device-profile.txt`
        beside it hashes to that sha256, parses with the runtime's grammar into
        the counts the receipt kept, holds only answers the runtime can
        reproduce, and was written by the profile probe now in the tree;
      * every run recorded the same profile;
      * the lifted run that stands for the replay -- its record in
        `Documentation/receipts/s4-tick-replay-v1.json` -- used that same profile
        (`lifted.device_profile.sha256`; a run on the default profile, which
        the record keeps as the string "default", is a different device set);
      * the pre-Init reach M4 takes from `Documentation/receipts/s4-native-outcomes-v1.json`
        was traced on this host: every receipt records the host's HID devices,
        and they hash to the list the traced boots recorded, and the profile
        enumerates as many game-controller-class devices as the traced boots,
        because the WMI scan in that reach runs once per enumerated device. The
        count is the parsed profile's, which its sha256 pins, not the receipt's.

    This copied capture_profile's first two checks until the 2026-09-23 review
    and took the controller count from the receipt, which nothing pins; it now
    asks capture_profile, so the replay tools and M4 refuse a capture for the
    same reasons.

    **Why the evdev check this replaced was not the question.** It required the
    runner's `host_game_controllers` to be empty, and the runner asks evdev
    (`/proc/bus/input/devices`), which calls a device a controller by its joystick
    handlers and buttons. Wine's DirectInput does not ask evdev: it enumerates HID
    devices through winebus, and on this host it hands the game's joystick scan the
    user's keyboard receiver, which evdev reports as no controller at all. So the
    check could not fail here while the game's scan ran WMI through three imports
    neither log saw (`agents-journals/s4/2026-09-23-native-outcomes.md`).
    """
    import device_profile as DP
    import native_outcomes as NO
    name = entry.get("name")
    recs = replay_receipts(entry)
    if not recs:
        raise SystemExit(f"replay {name} names no native receipt, so its device set is unknown")
    seen: dict[str, tuple[dict, list | None]] = {}
    for rel in recs:
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"replay {name}: {rel} is missing")
        if path.name != "receipt.json":
            raise SystemExit(f"replay {name}: {rel} is not a capture's receipt.json, so no "
                             "capture directory holds the profile it records")
        session = json.loads(path.read_text()).get("session") or {}
        if not session.get("device_profile"):
            raise SystemExit(f"replay {name}: {rel} records no device profile "
                             "(session.device_profile), so the devices its native run saw -- "
                             "which decide what the game calls before the Mod Loader's Init -- "
                             f"are not a recorded fact. {RECAPTURE}")
        try:
            prof = DP.capture_profile(path.parent, root)
        except DP.Refusal as e:
            raise SystemExit(f"replay {name}: {e}") from None
        seen[rel] = (prof, session.get("host_hid_devices"))
    shas = sorted({prof["sha256"] for prof, _ in seen.values()})
    if len(shas) != 1:
        raise SystemExit(f"replay {name}: its native runs recorded different device profiles "
                         f"({', '.join(s[:12] for s in shas)}), so they are not one device set")
    sha, counts = seen[recs[0]][0]["sha256"], seen[recs[0]][0]["counts"]

    record = root / TICK_REPLAY
    lifted = None
    if record.is_file():
        lifted = next((d.get("lifted") for d in json.loads(record.read_text()).get("replays", [])
                       if d.get("name") == name), None)
    # tools/s4/tick_replay.py records what device_profile.check_run returned: the
    # string "default", or {path, sha256}.
    dp = (lifted or {}).get("device_profile")
    used = dp.get("sha256") if isinstance(dp, dict) else dp
    if used != sha:
        raise SystemExit(f"replay {name}: the lifted run that stands for it ({TICK_REPLAY}) used "
                         f"device profile {used!r}, not its native capture's {sha[:12]}; the "
                         "replay reproduces a device set only when both sides ran the same one")

    outcomes = outcomes if outcomes is not None else NO.load(root / OUTCOMES)
    host = (outcomes.get("host") or {}).get("hid_devices")
    if not isinstance(host, dict) or not host.get("sha256"):
        raise SystemExit(f"{OUTCOMES} records no host HID devices, so no replay can be shown to "
                         "share the host its pre-Init reach was traced on")
    for rel, (_, hid) in seen.items():
        if not isinstance(hid, list):
            raise SystemExit(f"replay {name}: {rel} does not record the host's HID devices, so "
                             f"the pre-Init reach traced in {OUTCOMES} cannot be shown to be this "
                             f"host's. {RECAPTURE}")
        if NO.hid_digest(hid) != host["sha256"]:
            raise SystemExit(f"replay {name}: {rel} ran with other HID devices than the traced "
                             f"boots of {OUTCOMES} ({len(hid)} against {host.get('count')}), so "
                             "their pre-Init reach is not this replay's; trace a boot with this "
                             "host's devices and re-run tools/s4/native_outcomes.py --write")
    traced = (outcomes.get("devices") or {}).get("gamectrl")
    if counts.get("gamectrl") != traced:
        raise SystemExit(f"replay {name}: its device profile enumerates {counts.get('gamectrl')} "
                         f"game-controller-class device(s) and the traced boots {traced}; the "
                         "WMI scan in the pre-Init reach runs once per enumerated device, so "
                         "that reach is not this replay's")
    return {"sha256": sha, "counts": counts, "receipts": recs}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.split("\n\n")[2])
        return 2
    p = Path(argv[0])
    log = p / LOG if p.is_dir() else p
    counts = read(log)
    hit = sorted(k for k, v in counts.items() if v)
    print(f"{log}: {len(hit)} of {len(counts)} import slots called")
    for k in hit:
        print(f"  {counts[k]:>10}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
