#!/usr/bin/env python3
"""Which oracle produced a native capture, read from the capture's own evidence.

Subtitle: one answer for every tool that compares native captures with each
other or with the lifted build, and the refusal to compare across two.

    .venv/bin/python tools/oracle/oracle_mode.py CAPTURE_DIR [CAPTURE_DIR ...]

Why this exists
---------------
The live oracle runs in two modes.
- **loader**: the SA2 Mod Loader's process as it is, with its code patches in
  place.
- **vanilla**: `harness/replay/native_vanilla_probe.c` loads first and writes
  the executable's bytes back over the loader's patches, except the ranges it
  keeps.

The two differ wherever the loader's code does, and the boot replay's tick-4
divergence was one such place (`agents-journals/s4/2026-09-23-vanilla-oracle.md`).
A comparison across modes measures the modes, not the game. The user directed
on 2026-09-23 that "every capture receipt records which oracle mode produced
it, with a check refusing comparisons across modes". The runner writes this
module's answer into each receipt as `session.oracle`. Every comparison
recomputes it from the files and refuses a receipt that disagrees.

What a mode is
--------------
A mode is not a flag someone passed. It is what the capture shows, and two
captures are comparable only when every part of it is equal:

- **mode** is `vanilla` when the vanilla probe is the first mod the loader
  initialised, and `loader` when no probe names it and no vanilla log exists.
  Anything else is refused: a vanilla probe that is not first, or a vanilla
  log with no vanilla probe named. An empty or unrecognisable probe list is
  refused too, never read as `loader`.
- **kept** is the ranges the log says stayed the loader's, each with its
  address and length.
- **iat** is the import slots the loader redirected: `restored`, `kept`, or
  `none`, or `not-logged` for a probe from before slots were compared. A
  `not-logged` capture is never comparable with a newer one. That is
  deliberate, because the log cannot prove the slots were the same.
- **config** is what the loader writes after the mods' Init, where no Init-time
  probe can see it: the intro skip (`Graphics.SkipIntro`) and the test
  spawn's arguments. Captures that differ here ran different game code, even
  under the vanilla probe (the review of this module, 2026-09-23, found a real
  pair: `boot-vanilla-v3a` with the intro skipped, `boot-vanilla-v3b` without).
- **probe_sha256** is kept for provenance. It is not compared: the same code
  under a probe whose comments changed is the same mode.

The log is read by one strict parser, shared with `vanilla_problems`:
- the exact header;
- five fields per row;
- only the actions a correct run writes;
- no duplicate rows;
- at least one range;
- tick-1 rows that name exactly the restored ranges, once the probe writes
  them.

Anything else leaves the code unknown, and the capture is refused.

`vanilla_problems` is a stricter question: whether a capture was taken by the
vanilla oracle as it stands in the tree. That means the probe source at the
tree's hash, built with no experiment defines, keeping exactly the source's
KEEP ranges and treating import slots the way the source's default does, and
a log the receipt shows the runner collected: the mode reads the log against
the receipt's sha256 when the receipt has one, and this requires that it
does (the verify-profile review, 2026-09-23). The recorder and new
registrations ask it. Comparisons ask `require_same`.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VANILLA_PROBE = "harness/replay/native_vanilla_probe.c"
VANILLA_MOD = "vanilla_probe"
VANILLA_LOG = "sa2-vanilla.csv"
VANILLA_HEADER = "va,length,loader_bytes,file_bytes,action"
RANGE_ACTIONS = ("kept", "restored")
SLOT_ACTIONS = ("iat-kept", "iat-restored")
TICK1_ACTIONS = ("tick1-intact",)
GOOD_ACTIONS = RANGE_ACTIONS + SLOT_ACTIONS + TICK1_ACTIONS
DIGEST = ROOT / "harness" / "replay" / "tick_digest.h"


class ModeError(SystemExit):
    """A capture whose mode cannot be established, or two that differ."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _receipt(capture: Path) -> dict:
    p = capture / "receipt.json"
    if not p.is_file():
        raise ModeError(f"{capture.name}: no receipt.json, so its oracle mode is unknown")
    return json.loads(p.read_text())


def _is_vanilla(probe: dict) -> bool:
    name = re.split(r"[\\/]", str(probe.get("source", "")))[-1]
    return name == Path(VANILLA_PROBE).name or probe.get("mod_id") == VANILLA_MOD


def read_log(path: Path) -> dict:
    """The vanilla probe's log, strictly: what it kept, restored, did to the
    import slots and saw at tick 1. Refuses anything a correct run does not
    write."""
    where = f"{path.parent.name}/{path.name}"
    if not path.is_file():
        raise ModeError(f"{where}: the vanilla probe ran but its log was not collected, so "
                        "which code ran is not recorded")
    lines = path.read_text(errors="replace").splitlines()
    if not lines or lines[0] != VANILLA_HEADER:
        raise ModeError(f"{where}: does not begin with the probe's header {VANILLA_HEADER!r}")
    kept, restored, slots, tick1, seen = set(), set(), {}, set(), set()
    for n, line in enumerate(lines[1:], 2):
        fields = line.split(",", 4)          # an error row's words may hold commas
        if len(fields) != 5:
            raise ModeError(f"{where}: line {n} is not a row of five fields: {line!r}")
        va_s, len_s, _, _, action = fields
        if action not in GOOD_ACTIONS:
            raise ModeError(f"{where}: line {n} records {action!r}, which leaves the code "
                            f"that ran unknown")
        try:
            va, length = int(va_s, 16), int(len_s)
        except ValueError:
            raise ModeError(f"{where}: line {n} names {va_s},{len_s}, which is not a range")
        if (va, action) in seen:
            raise ModeError(f"{where}: line {n} repeats {va_s} {action}")
        seen.add((va, action))
        if action == "kept":
            kept.add((va, length))
        elif action == "restored":
            restored.add((va, length))
        elif action in SLOT_ACTIONS:
            slots[va] = action
        else:
            tick1.add(va)
    if not kept and not restored:
        raise ModeError(f"{where}: records no range, but the loader always patches code; the "
                        "probe did not compare the sections")
    if slots and not tick1:
        raise ModeError(f"{where}: a probe that compares import slots also checks the restored "
                        "ranges at tick 1, and this log has no tick-1 rows: the second look "
                        "never ran")
    if tick1 and tick1 != {va for va, _ in restored}:
        raise ModeError(f"{where}: its tick-1 rows name {len(tick1)} range(s) and it restored "
                        f"{len(restored)}; the second look did not cover what was restored")
    return {"kept": kept, "restored": restored, "slots": slots, "tick1": tick1}


def config_of(rec: dict, where: str) -> dict:
    """What the loader was told, which it acts on after the mods' Init where no
    Init-time probe can see: the whole Mod Loader profile except the mod list
    (the load order is judged on its own), and the game's arguments, which
    carry the test spawn. The intro skip is the setting found to matter; the
    pause on focus loss, the languages and the resolution are acted on too
    (the P1 review, 2026-09-23), so none is left out. A receipt with no Mod
    Loader profile is refused."""
    inputs = rec.get("inputs") or {}
    profile = inputs.get("modloader_profile")
    if not isinstance(profile, dict):
        raise ModeError(f"{where}: the receipt has no Mod Loader profile, so what the loader "
                        "wrote after the mods' Init is unknown")
    skip = (profile.get("Graphics") or {}).get("SkipIntro")
    if not isinstance(skip, bool):
        raise ModeError(f"{where}: the Mod Loader profile does not say whether the intro was "
                        "skipped")
    rest = {k: v for k, v in profile.items() if k != "EnabledMods"}
    return {"skip_intro": skip, "game_args": [str(a) for a in inputs.get("game_args") or []],
            "profile_sha256": hashlib.sha256(json.dumps(rest, sort_keys=True).encode()).hexdigest()}


def mode_of(capture: Path) -> dict:
    """The capture's oracle mode, from its receipt and its vanilla log."""
    return mode_from(_receipt(Path(capture)), Path(capture))


def mode_from(rec: dict, capture: Path) -> dict:
    """The mode a receipt and the capture directory beside it show; the runner
    calls this before it writes the receipt, to record the answer in it."""
    capture = Path(capture)
    where = capture.name
    inputs = rec.get("inputs") or {}
    built = inputs.get("built_probes")
    if not isinstance(built, list) or not built:
        raise ModeError(f"{where}: the receipt lists no built probe, so neither mode can be "
                        "established")
    config = config_of(rec, where)
    at = [i for i, b in enumerate(built) if _is_vanilla(b)]
    has_log = (capture / VANILLA_LOG).exists()
    if not at:
        if has_log:
            raise ModeError(f"{where}: holds {VANILLA_LOG} but its receipt names no vanilla probe")
        return {"mode": "loader", "kept": [], "iat": "loader", "config": config,
                "probe_sha256": None}
    if at != [0]:
        raise ModeError(f"{where}: the vanilla probe is built at position {at}, not first")
    mod = built[0].get("mod_id")
    enabled = inputs.get("enabled_mods") or []
    order = ((inputs.get("modloader_profile") or {}).get("EnabledMods")) or []
    for lst in (enabled, order):
        if not lst or lst[0] != mod:
            raise ModeError(f"{where}: the loader initialised {(lst or ['nothing'])[0]} first, "
                            "not the vanilla probe, so another mod's Init ran on the loader's "
                            "code")
    log = read_log(capture / VANILLA_LOG)
    art = next((a for a in rec.get("artifacts") or [] if a.get("file") == VANILLA_LOG), None)
    want = ((art or {}).get("check") or {}).get("sha256")
    if want and sha256(capture / VANILLA_LOG) != want:
        raise ModeError(f"{where}: {VANILLA_LOG} is not the file the runner collected (its "
                        f"receipt records {want[:12]})")
    states = set(log["slots"].values())
    if len(states) > 1:
        raise ModeError(f"{where}: import slots are both kept and restored, which one build of "
                        "the probe cannot do")
    iat = ("restored" if states == {"iat-restored"} else "kept" if states == {"iat-kept"}
           else "none" if log["tick1"] else "not-logged")
    return {"mode": "vanilla",
            "kept": sorted(f"0x{va:08x}+{n}" for va, n in log["kept"]),
            "iat": iat, "config": config,
            "probe_sha256": built[0].get("source_sha256")}


def comparable(a: dict, b: dict, *, config: bool = True) -> bool:
    keys = ("mode", "kept", "iat") + (("config",) if config else ())
    return all(a.get(k) == b.get(k) for k in keys)


def describe(m: dict) -> str:
    if "refused" in m:
        return f"refused ({m['refused']})"
    cfg = m.get("config") or {}
    tail = (f"; intro {'skipped' if cfg.get('skip_intro') else 'played'}"
            + (f"; args {' '.join(cfg['game_args'])}" if cfg.get("game_args") else ""))
    if m["mode"] == "loader":
        return "loader" + tail
    return f"vanilla (kept {', '.join(m['kept']) or 'none'}; import slots {m['iat']}{tail})"


def require_same(captures: list[Path], what: str = "these captures", *,
                 config: bool = True) -> dict:
    """The one mode `captures` share; refused when any two differ, or when
    there is nothing to compare."""
    if not captures:
        raise ModeError(f"{what}: no capture to establish a mode from")
    modes = [(Path(c), recorded_matches(Path(c))) for c in captures]
    first = modes[0][1]
    if any(not comparable(m, first, config=config) for _, m in modes):
        lines = "; ".join(f"{c.name}: {describe(m)}" for c, m in modes)
        raise ModeError(f"{what} were produced by different oracle modes, so comparing them "
                        f"measures the modes and not the game: {lines}")
    return first


def normalise(recorded: object) -> dict:
    if not isinstance(recorded, dict) or not all(k in recorded for k in ("mode", "kept", "iat",
                                                                           "config")):
        raise ModeError(f"session.oracle is not a recorded mode: {recorded!r}")
    return {**recorded, "kept": sorted(recorded["kept"])}


def recorded_matches(capture: Path) -> dict:
    """The recorded mode, refused when the receipt lacks it (from the runner that
    writes it on) or when the files show another."""
    capture = Path(capture)
    rec = _receipt(capture)
    now = mode_of(capture)
    if "oracle" not in (rec.get("session") or {}):
        return now                      # captures from before the field; the files stand alone
    recorded = normalise(rec["session"]["oracle"])
    if not comparable(recorded, now):
        raise ModeError(f"{capture.name}: the receipt records oracle mode {describe(recorded)} "
                        f"but its files show {describe(now)}")
    return now


def keep_table(root: Path = ROOT) -> set[tuple[int, int]]:
    """The probe source's KEEP table as (address, length) pairs, read from the
    tree so a changed table cannot leave this checking the old one."""
    path = root / VANILLA_PROBE
    code = re.sub(r"/\*.*?\*/", " ", path.read_text(), flags=re.S)
    table = re.search(r"\bKEEP\s*\[\s*\]\s*\[\s*2\s*\]\s*=\s*\{(.*?)\}\s*;", code, re.S)
    if not table:
        raise ModeError(f"{VANILLA_PROBE} declares no KEEP table this reads")
    pair = r"\{\s*(0x[0-9A-Fa-f]+)[uU]?\s*,\s*(0x[0-9A-Fa-f]+)[uU]?\s*\}"
    return {(int(a, 16), int(b, 16) - int(a, 16)) for a, b in re.findall(pair, table.group(1))}


def default_restore_iat(root: Path = ROOT) -> int:
    m = re.search(r"#ifndef RESTORE_IAT\s*\n\s*#define RESTORE_IAT\s+(\d+)",
                  (root / VANILLA_PROBE).read_text())
    if not m:
        raise ModeError(f"{VANILLA_PROBE} declares no RESTORE_IAT default this reads")
    return int(m.group(1))


def vanilla_problems(capture: Path, *, root: Path = ROOT) -> list[str]:
    """Why a capture is not one the vanilla oracle as it stands in the tree took,
    or [] when it is."""
    capture = Path(capture)
    try:
        m = recorded_matches(capture)
    except ModeError as e:
        return [str(e)]
    if m["mode"] != "vanilla":
        return [f"{capture.name} was taken by the {m['mode']} oracle, not the vanilla one"]
    out = []
    rec = _receipt(capture)
    probe = (rec.get("inputs") or {}).get("built_probes")[0]
    art = next((a for a in rec.get("artifacts") or [] if a.get("file") == VANILLA_LOG), None)
    if not ((art or {}).get("check") or {}).get("sha256"):
        out.append(f"{capture.name}'s receipt records no sha256 for {VANILLA_LOG}, so nothing "
                   "shows the log is the one the runner collected")
    now = sha256(root / VANILLA_PROBE)
    if probe.get("source_sha256") != now:
        out.append(f"{capture.name} was taken with a {Path(VANILLA_PROBE).name} that is not the "
                   f"one in the tree ({str(probe.get('source_sha256'))[:12]} against {now[:12]}); "
                   "re-capture it")
    if probe.get("defines"):
        out.append(f"{capture.name}'s vanilla probe was built with {probe['defines']}, an "
                   "experiment, not the oracle")
    want = sorted(f"0x{va:08x}+{n}" for va, n in keep_table(root))
    if m["kept"] != want:
        out.append(f"{capture.name} keeps {', '.join(m['kept']) or 'nothing'} and the probe's "
                   f"KEEP table is {', '.join(want)}")
    iat_want = "restored" if default_restore_iat(root) else "kept"
    if m["iat"] not in (iat_want, "none"):
        out.append(f"{capture.name} left the import slots {m['iat']}; the probe's default "
                   f"leaves them {iat_want}")
    return out


PROVENANCE = re.compile(r"^\s*\*\s*[^:]+:\s+(\S+)\s+sha256\s+([0-9a-f]{64})\s*$")

# ------------------------------------------------------------ the probe build

# A word-log mask is chosen from runs configured exactly like the comparison runs
# (the user's mask rule, Documentation/decisions/2026-09-24-S4-halt-220h.md): the
# same probes built from the same sources, headers and defines, and the same run
# configuration. `build_of` is that identity, read from a capture's receipt, and
# `require_same_build` refuses captures whose identities differ, naming the first
# difference.
WORD_LOGS = ("sa2-word-log.bin", "sa2-word-log.bin.zst")
MIN_MASK_SOURCES = 3
# At least one source run must follow this long idle (the judge's plan): a run
# after a pause can take a path warm runs do not, as the boot's tick-369 second
# path did. The one constant: tools/s4/snapshot_fields.py reads it from here.
COLD_SECONDS = 90
RECORDS = {"excluded words": "sa2-digest-exclusions/v1",
           "identity words": "sa2-identity-words/v1"}
HELD_OUT = "held out"
BUILD_LINE = re.compile(r"^\s*\*\s*Probe build:\s*([0-9a-f]{64})\b", re.M)
METHOD_LINE = re.compile(r"^\s*\*\s*Method:\s*([^.\n]+)\.", re.M)
RECEIPT_LINE = re.compile(r"^\s*\*\s*Receipt:\s*(\S+)\s*$", re.M)
MASK_SCHEMA = "sa2-digest-mask/v1"
# Where the per-replay word-log masks live, and the one identity record every one
# of them cites (the comparer reads the same record, so a mask cut with another
# would leave out words the comparer does not compare by identity).
MASKS_DIR = "harness/replay/masks"
IDENTITY_RECORD = f"{MASKS_DIR}/identity-words.json"
# A replay whose plain native repeats disagree on words no run shows apart must
# cite its exclusion record, so its receipt names each run's path through them.
# The boot's word-log mask cites its own record, beside it: the snapshot mask's
# two words (harness/replay/tick_digest_exclusions.json, which tick_digest.h
# cites and which stays as it was), each retired for the probe build whose
# plain runs all take one path through it, so a mask of that build compares
# them (tools/s4/snapshot_fields.py retirements; the U6 masks review, W1).
EXCLUSIONS_REQUIRED = {"boot": "harness/replay/masks/boot-exclusions.json"}


def idle_seconds(rec: dict) -> float | None:
    """How long the oracle sat idle before a run, from its receipt: the runner's
    `session.idle_before_run.seconds` (tools/oracle/run_oracle_probe.py
    idle_before_run), or a plain `session.idle_before_run_seconds`. None when
    neither is a number, which no rule counts as cold."""
    session = rec.get("session") or {}
    nested = session.get("idle_before_run")
    for v in ((nested.get("seconds") if isinstance(nested, dict) else None),
              session.get("idle_before_run_seconds")):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def read_mask_words(header: Path) -> set[int]:
    """Every word a mask header selects: td_base's blocks and td_mask's bits,
    each entry read whether it is written 0 or 0x...u (the parser that skipped
    the plain 0 entries read two wrong addresses; the recording-session
    journal). tools/s4/snapshot_fields.py read_mask is this."""
    text = re.sub(r"/\*.*?\*/", " ", Path(header).read_text(), flags=re.S)
    base_body = re.search(r"td_base\[TD_BLOCKS\]\s*=\s*\{(.*?)\};", text, re.S)
    mask_body = re.search(r"td_mask\[TD_BLOCKS\]\[512\]\s*=\s*\{(.*)\};", text, re.S)
    count = re.search(r"#define TD_BLOCKS (\d+)u", text)
    if not (base_body and mask_body and count):
        raise ModeError(f"{header}: not a mask header (TD_BLOCKS, td_base, td_mask)")
    bases = [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]+)u", base_body.group(1))]
    rows = re.findall(r"\{([^{}]*)\}", mask_body.group(1))
    if len(bases) != int(count.group(1)) or len(rows) != len(bases):
        raise ModeError(f"{header}: {count.group(1)} blocks declared, {len(bases)} bases and "
                        f"{len(rows)} mask rows")
    out: set[int] = set()
    for b, row in zip(bases, rows):
        cells = [c.strip() for c in row.split(",") if c.strip()]
        if len(cells) != 512:
            raise ModeError(f"{header}: block {b:#010x}'s row has {len(cells)} entries, not 512")
        for k, c in enumerate(cells):
            v = int(c.rstrip("uU"), 0)
            for i in range(32):
                if v >> i & 1:
                    out.add(b + 4 * (32 * k + i))
    return out
# The headers beside a probe that are the host's input and not the game's: a mask.
# No native probe includes one since the word log moved the digests to the host
# (harness/replay/tick_probe_core.h wants a mask only from an includer that asks
# for the digest columns, and no native probe does: test_oracle_mode.py holds it).
MASK_HEADERS = ("tick_digest.h",)


def probe_build(rec: dict, where: str) -> list[dict]:
    """The probes a receipt records, in load order, as they bear on the game:
    each probe's mod id, source file name and sha256, defines, and the sha256 of
    every header the runner recorded beside it but a mask. Not compared: the
    DLL's own hash (a build of the same inputs differs in its PE timestamp; the
    word-log probe review, N7), the source's directory (a scratch tree builds
    the same bytes), and a mask header, which is the host's input, so a new
    mask is not a new configuration. Every other header beside the probe is
    compared, included or not: that is stricter than the headers the probe
    includes, and reads the receipt alone, never the disk."""
    built = (rec.get("inputs") or {}).get("built_probes")
    if not isinstance(built, list) or not built:
        raise ModeError(f"{where}: the receipt lists no built probe, so its build is unknown")
    out = []
    for k, b in enumerate(built):
        at = f"{where}: built_probes[{k}]"
        entry = {"mod_id": _recorded(b, "mod_id", at),
                 "source": re.split(r"[\\/]", str(_recorded(b, "source", at)))[-1],
                 "source_sha256": _recorded(b, "source_sha256", at),
                 "defines": sorted(str(d) for d in _recorded(b, "defines", at) or []),
                 "headers": {h: v for h, v in sorted((_recorded(b, "headers_sha256", at)
                                                      or {}).items())
                             if h not in MASK_HEADERS}}
        # Recorded by the runner only once it does (the mask reviews' W5/W4, a request to
        # U2): the compiler and its arguments, and the DLL's hash with its PE timestamp,
        # checksum and CodeView record zeroed. A receipt that records one is compared
        # on it, and one that does not differs from it.
        for key in UNTIL_RECORDED_PROBE:
            if key in b:
                entry[key] = b[key]
        out.append(entry)
    return out


# Build facts no runner records yet. Once U2's runner writes them they move into
# the required reads; until then a mask receipt lists them as not recorded.
UNTIL_RECORDED_PROBE = ("compile", "dll_sha256_normalised")
UNTIL_RECORDED_SESSION = ("proton",)


def _recorded(d: object, key: str, where: str):
    """d[key], refused when the receipt does not record it at all: a fact missing
    from two receipts is not a fact they share (the mask review's W5). A key the
    receipt records as null is compared as null."""
    if not isinstance(d, dict) or key not in d:
        raise ModeError(f"{where}: the receipt does not record {key}, so two runs cannot be "
                        "shown configured alike on it")
    return d[key]


def run_configuration(rec: dict, capture: Path) -> dict:
    """What a run was configured with besides its probes: the executable, the
    installation and the directory the game ran in (CRI's file table holds
    paths from it), the loader's mod order, the oracle mode with the loader's
    configuration (`mode_from`), the save pin it started from, the game's
    environment, the device profile, WINEDEBUG and the game's standard handles
    (both can move timing or handle numbering; the mask evidence review, W4).
    None of these is a time or a count, so two runs configured alike record
    them alike; a receipt that does not record one is refused."""
    where = Path(capture).name
    inputs = _recorded(rec, "inputs", where)
    session = _recorded(rec, "session", where)
    mode = mode_from(rec, Path(capture))
    saves = _recorded(session, "saves", f"{where}: session")
    out = {"executable_sha256": _recorded(inputs, "executable_sha256", f"{where}: inputs"),
           "installation": _recorded(inputs, "installation", f"{where}: inputs"),
           "cwd": _recorded(rec, "cwd", where),
           "enabled_mods": list(_recorded(inputs, "enabled_mods", f"{where}: inputs") or []),
           "oracle": {k: mode[k] for k in ("mode", "kept", "iat", "config")},
           "saves_pin": _recorded(_recorded(saves, "pin", f"{where}: session.saves"), "digest",
                                  f"{where}: session.saves.pin"),
           "saves_matched_pin": _recorded(saves, "matches_pin", f"{where}: session.saves"),
           "game_environment": _recorded(session, "game_environment", f"{where}: session"),
           "device_profile": _recorded(_recorded(session, "device_profile",
                                                 f"{where}: session"), "sha256",
                                       f"{where}: session.device_profile"),
           "winedebug": _recorded(session, "winedebug", f"{where}: session"),
           "standard_handles": _recorded(session, "standard_handles", f"{where}: session")}
    for key in UNTIL_RECORDED_SESSION:
        if key in session:
            out[key] = session[key]
    return out


def build_of(capture: Path) -> dict:
    """`{"probes": probe_build, "configuration": run_configuration}` of a capture."""
    rec = _receipt(Path(capture))
    return {"probes": probe_build(rec, Path(capture).name),
            "configuration": run_configuration(rec, Path(capture))}


def build_digest(build: dict) -> str:
    return hashlib.sha256(json.dumps(build, sort_keys=True).encode()).hexdigest()


def first_difference(a: object, b: object, at: str = "") -> str | None:
    """Where two builds first differ, as a path and both values; None if equal."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            d = first_difference(a.get(k), b.get(k), f"{at}.{k}" if at else str(k))
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, f"{at}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{at}: {json.dumps(a)[:80]} against {json.dumps(b)[:80]}"


def require_same_build(captures: list[Path], what: str = "these captures", *,
                       root: Path = ROOT) -> dict:
    """The one build and configuration `captures` share; refused when any two
    differ, by the first difference, or when there is nothing to compare."""
    if not captures:
        raise ModeError(f"{what}: no capture to establish a build from")
    builds = [build_of(Path(c)) for c in captures]
    for c, b in zip(captures[1:], builds[1:]):
        d = first_difference(builds[0], b)
        if d:
            raise ModeError(f"{what} are not one probe build and configuration: "
                            f"{Path(captures[0]).name} and {Path(c).name} differ at {d}")
    return builds[0]


def digest_mask_provenance(header: Path = DIGEST, root: Path = ROOT, *,
                           by_receipt: bool = False) -> dict:
    """What a digest mask header says it was chosen from, each file's hash
    verified: {"method": "word logs" or "snapshots", "sources": capture dirs,
    "held_out": capture dirs, "records": {label: path}, "build": the sources'
    shared build or None, "receipt": the mask receipt's path or None}.

    A word-log header (its `Method: word logs.` line, or any header under
    harness/replay/masks/, or one citing a word log) is held to the mask rule
    each time it is read: at least MIN_MASK_SOURCES source runs and one
    held-out capture, each a different run (its own directory, log hash,
    receipt name and creation time); one of the sources at least COLD_SECONDS
    idle before it; every one of them one probe build and configuration, the
    one its "Probe build:" line names; the tree's identity record, and for a
    replay in EXCLUSIONS_REQUIRED its exclusion record; and its mask receipt
    (the "Receipt:" line), which must record this header's sha256, no problem,
    a held-out capture that reproduced, a cold source, the same build, sources
    and held-out logs, and the header's own word count, which is not zero.

    What reading cannot show: the receipt is the tool's word for how the words
    were chosen, so a header and receipt edited together by hand would pass.
    Only choosing again from the cited logs (tools/s4/snapshot_fields.py
    --word-logs) shows the words are the rule's.

    A snapshot header (the method before word logs, no Method line) is read as
    it always was."""
    text = header.read_text()
    lines = [ln for ln in text.splitlines() if " sha256 " in ln and ln.lstrip().startswith("*")]
    if not lines:
        raise ModeError(f"{header.name}: names no source capture")
    sources: list[Path] = []
    held: list[Path] = []
    cited: dict[Path, str] = {}
    records: dict[str, Path] = {}
    for ln in lines:
        m = PROVENANCE.match(ln)
        if not m:
            raise ModeError(f"{header.name}: a provenance line does not parse: {ln.strip()!r}")
        path = Path(m.group(1))
        path = path if path.is_absolute() else root / path
        label = ln.lstrip(" *").split(":", 1)[0].strip()
        record = label in RECORDS
        recorded = None
        if not record and (path.parent / "receipt.json").is_file():
            art = next((a for a in _receipt(path.parent).get("artifacts") or []
                        if a.get("file") == path.name), None)
            recorded = ((art or {}).get("check") or {}).get("sha256")
        if by_receipt and not record and (path.parent / "receipt.json").is_file():
            if not path.is_file() or recorded != m.group(2):
                raise ModeError(f"{header.name}: {m.group(1)} is missing, or its capture's "
                                f"receipt records {str(recorded)[:12]} for it, not "
                                f"{m.group(2)[:12]}")
        elif not path.is_file() or sha256(path) != m.group(2):
            raise ModeError(f"{header.name}: {m.group(1)} is missing or no longer hashes to "
                            f"{m.group(2)[:12]}")
        elif recorded is not None and recorded != m.group(2):
            # Hashed, and its receipt says the runner collected other bytes (the mask
            # evidence review, W1: this branch never compared the two).
            raise ModeError(f"{header.name}: {m.group(1)} hashes to the header's "
                            f"{m.group(2)[:12]}, but its capture's receipt records "
                            f"{recorded[:12]}")
        if record:
            # A record (tools/s4/snapshot_fields.py --exclude-words, --identity-words) is an
            # input of the choice, verified above, and not a capture: it has no mode of its
            # own. Only a file that is one skips the mode check, so the label cannot take a
            # capture out of it.
            try:
                schema = json.loads(path.read_text()).get("schema")
            except (ValueError, AttributeError):
                schema = None
            if schema != RECORDS[label] or (path.parent / "receipt.json").exists():
                kind = "an exclusion" if label == "excluded words" else "an identity"
                raise ModeError(f"{header.name}: {m.group(1)} is cited as the {label} and is "
                                f"not {kind} record")
            if label in records:
                raise ModeError(f"{header.name}: cites two {label} records")
            records[label] = path
            continue
        if not (path.parent / "receipt.json").is_file():
            raise ModeError(f"{header.name}: {m.group(1)} is not in a capture with a receipt")
        (held if label == HELD_OUT else sources).append(path)
        cited[path] = m.group(2)
    if not sources:
        raise ModeError(f"{header.name}: names no source capture")
    method_m = METHOD_LINE.search(text)
    method = method_m.group(1).strip() if method_m else None
    under_masks = header.resolve().parent == (root / MASKS_DIR).resolve()
    logs = [p for p in sources + held if p.name in WORD_LOGS]
    if method is None and not under_masks and not logs:
        if held:
            raise ModeError(f"{header.name}: names a held-out capture, which only a word-log "
                            "mask has, and no word log")
        return {"method": "snapshots", "sources": [p.parent for p in sources], "held_out": [],
                "records": records, "build": None, "receipt": None}
    # The method is what the header says, never what its file names suggest (the mask
    # reviews' W2/W3): a header under masks/ is a word-log mask, and one that says so
    # cites nothing else.
    if method != "word logs":
        raise ModeError(f"{header.name}: "
                        + ("sits in harness/replay/masks/, where every mask is chosen from word "
                           "logs," if under_masks else "cites word logs,")
                        + f" and its Method line says {method!r}, not 'word logs'")
    if len(logs) != len(sources) + len(held):
        other = next(p for p in sources + held if p.name not in WORD_LOGS)
        raise ModeError(f"{header.name}: says Method: word logs and cites {other.name}, which "
                        "is not a word log; a mask is chosen from one kind of observation")
    if len(sources) < MIN_MASK_SOURCES:
        raise ModeError(f"{header.name}: chosen from {len(sources)} word logs; a mask needs at "
                        f"least {MIN_MASK_SOURCES} source runs")
    if len(held) != 1:
        raise ModeError(f"{header.name}: names {len(held)} held-out captures, not one; the "
                        "held-out capture is what shows the mask repeats")
    if "identity words" not in records:
        raise ModeError(f"{header.name}: cites no identity record, so nothing shows the words "
                        "compared by identity were left out of the mask")
    if records["identity words"].resolve() != (root / IDENTITY_RECORD).resolve():
        raise ModeError(f"{header.name}: cites {records['identity words']} as its identity "
                        f"record, not the tree's {IDENTITY_RECORD}, which the comparer reads")
    # The tree's boot mask before word logs is the boot's too: a word-log header
    # written over it is held to the boot's records (the U4 fold review, W1).
    replay = header.stem if under_masks else (
        "boot" if header.resolve() == (root / "harness" / "replay" / "tick_digest.h").resolve()
        else None)
    if replay in EXCLUSIONS_REQUIRED:
        want = (root / EXCLUSIONS_REQUIRED[replay]).resolve()
        if "excluded words" not in records or records["excluded words"].resolve() != want:
            raise ModeError(f"{header.name}: the {replay} mask cites no exclusion record "
                            f"{EXCLUSIONS_REQUIRED[replay]}, so its receipt names no run's path "
                            "through the words plain repeats disagree on")
    # One run counts once (the mask reviews' C1/W1): by its directory, its log's hash,
    # its receipt's name and its receipt's creation time.
    runs = sources + held
    dirs = [p.parent.resolve() for p in runs]
    receipts = [_receipt(p.parent) for p in runs]
    for what, keys in (("directory", dirs), ("log sha256", [cited[p] for p in runs]),
                       ("receipt created_utc", [r.get("created_utc") for r in receipts])):
        if None in keys or len(set(keys)) != len(keys):
            raise ModeError(f"{header.name}: two of its source and held-out captures share a "
                            f"{what} (or one records none), so one run would count twice")
    for p, r in zip(runs, receipts):
        if r.get("name") != p.parent.name:
            raise ModeError(f"{header.name}: {p.parent.name}'s receipt names the run "
                            f"{r.get('name')!r}; a renamed copy of a capture is not another run")
    idle = [idle_seconds(r) for r in receipts[:len(sources)]]
    if not any(v is not None and v >= COLD_SECONDS for v in idle):
        raise ModeError(f"{header.name}: no source run's receipt records {COLD_SECONDS} s or "
                        f"more idle before it ({idle})")
    build = require_same_build([p.parent for p in runs],
                               f"{header.name}'s source and held-out captures", root=root)
    claimed = BUILD_LINE.search(text)
    if not claimed or claimed.group(1) != build_digest(build):
        raise ModeError(f"{header.name}: its Probe build line "
                        f"{claimed.group(1)[:12] if claimed else 'is missing and'} does not name "
                        f"its sources' build ({build_digest(build)[:12]})")
    receipt = mask_receipt_problems(header, root, build_digest(build),
                                    [cited[p] for p in sources], cited[held[0]])
    return {"method": "word logs", "sources": [p.parent for p in sources],
            "held_out": [p.parent for p in held], "records": records, "build": build,
            "receipt": receipt}


def mask_receipt_problems(header: Path, root: Path, build: str, source_shas: list[str],
                          held_sha: str) -> Path:
    """The mask receipt a word-log header names on its "Receipt:" line, held to
    the header (the mask evidence review, W2): it records this header's sha256,
    no problem, a held-out capture equal on every selected word, a cold source,
    the build, the sources' and the held-out log's hashes, and a selected-word
    count equal to the header's, which is not zero. Returns its path."""
    m = RECEIPT_LINE.search(header.read_text())
    if not m:
        raise ModeError(f"{header.name}: names no Receipt, so nothing records how its words were "
                        "chosen and that its held-out capture reproduced them")
    path = Path(m.group(1))
    path = path if path.is_absolute() else root / path
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ModeError(f"{header.name}: its receipt {m.group(1)} does not read: {e}")
    words = read_mask_words(header)
    held_check = rec.get("held_out_check") or {}
    wrong = [what for what, ok in (
        ("its schema", rec.get("schema") == MASK_SCHEMA),
        ("this header's sha256", (rec.get("emitted") or {}).get("sha256") == sha256(header)),
        ("no problem", rec.get("problems") == []),
        ("a held-out capture that reproduced",
         held_check.get("differing_selected_words") == 0),
        ("a cold source", any(s.get("cold") is True for s in rec.get("sources") or [])),
        ("the header's build", rec.get("build") == build),
        ("the header's source logs", [s.get("sha256") for s in rec.get("sources") or []]
         == source_shas),
        ("the header's held-out log", (rec.get("held_out") or {}).get("sha256") == held_sha),
        ("the header's word count", (rec.get("counts") or {}).get("selected") == len(words)),
        ("a mask that selects a word", len(words) > 0)) if not ok]
    if wrong:
        raise ModeError(f"{header.name}: its receipt {m.group(1)} does not record "
                        f"{', '.join(wrong)}")
    return path


def digest_mask_sources(header: Path = DIGEST, root: Path = ROOT, *,
                        by_receipt: bool = False) -> list[Path]:
    """The native captures a digest mask header was chosen from (and, for a
    word-log mask, its held-out capture), each file's hash verified. Every
    provenance line must parse, and every source must be a capture with a
    receipt. A word-log mask is held to the whole rule as it is read
    (`digest_mask_provenance`).

    `by_receipt` checks a capture's file against the sha256 its receipt records
    for it (the runner's, written when the capture was taken) instead of hashing
    the file again. The mode check asks only which captures the mask names, and
    the full hash of the snapshot runs is about 12 GB on every replay check (the
    mask review, W1). The full hash runs where M5 is published:
    `tools/s4/tick_replay.py --receipt` calls this without `by_receipt`, so a
    source changed on disk after its capture was taken stops that push. The
    records are small and are always hashed."""
    p = digest_mask_provenance(header, root, by_receipt=by_receipt)
    return p["sources"] + p["held_out"]


def require_mask_configuration(header: Path, captures: list[Path], root: Path = ROOT) -> dict:
    """Refuse `captures` (a replay's native side) unless each is the probe build
    and configuration a word-log mask was chosen from: the user's mask rule read
    from the comparison's side. A snapshot mask has no such build and is refused
    here, since its runs loaded a probe the comparison runs do not."""
    p = digest_mask_provenance(header, root, by_receipt=True)
    if p["build"] is None:
        raise ModeError(f"{header.name} was chosen from snapshot runs, a probe the comparison "
                        "runs do not load, so no comparison run is configured like its sources "
                        "(Documentation/decisions/2026-09-24-S4-halt-220h.md, the mask rule)")
    for c in captures:
        d = first_difference(p["build"], build_of(Path(c)))
        if d:
            raise ModeError(f"{Path(c).name} is not configured like {header.name}'s sources: "
                            f"{d}")
    return p["build"]


def digest_mask_mode(header: Path = DIGEST, root: Path = ROOT) -> dict:
    """The one oracle mode the digest mask was chosen from: its mode, kept ranges
    and import slots, which a capture it is applied to must share. The
    configuration is not asked here: for the snapshot mask (tick_digest.h) it
    need not agree, since its runs were boot runs; a word-log mask holds its
    captures to its whole build and configuration in
    `require_mask_configuration`, which a replay asks as well."""
    return require_same(digest_mask_sources(header, root), f"{header.name}'s source captures",
                        config=False)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        raise SystemExit(__doc__.split("\n\n")[1])
    for c in args:
        print(f"ORACLE_MODE {Path(c).name}: {describe(recorded_matches(Path(c)))}")
    if len(args) > 1:
        require_same([Path(c) for c in args])
        print("ORACLE_MODE_SAME")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
