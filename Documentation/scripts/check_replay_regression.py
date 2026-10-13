#!/usr/bin/env python3
"""Gate: every registered replay keeps being run and compared at every stage, and
none that met M5 or reached its goal reads worse.

Why this exists
---------------
The user, 2026-09-26 (`Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md`):
"Every registered replay stays in the gates for all later stages as a
regression check." Design 7.3: "A function or replay that has passed never
silently stops being run. The ledger's coverage section lists every passing
item; a drop is a gate failure."

Before this gate, half of that was built for replays and half was not.
`check_replay_results.py` holds the tick-replay receipt to the tree on every
push, at every stage: it names every registered replay, its inputs are the
tree's, and its comparisons recompute. So a replay whose build moved has to be
run again. But nothing compared what the run said with what it had said. M5 and
M6 are judged by `stage_exit.py`'s predicates, and only G4's criteria in
`Documentation/gate-criteria.json` run them, so after G4 a replay that met M5
could read `bug` at tick 4 and every gate would pass. A replay could also leave
the registry on a one-line `retired` reason. Nothing in `run_all_gates.py`,
`check_replay_results.py`, `check_stage_results.py`, `stage_exit.py` or
`tick_replay.py` is scoped to S4 or to the active stage; the only stage scope
is that the M5 and M6 checks live under G4 alone.

What it checks
--------------
The baseline is tracked, `Documentation/receipts/replay-baseline-v1.json`: per
replay, its M5 reading as `metrics.py` renders it (`stage_exit.m5_part` once
that exists, so the two cannot drift apart), the ticks it judged, the words it
compared (`result.mask.words`: the mask less identity and residue words),
whether the reading met M5 then, and for a stage replay whether its lifted run
reached the goal. A replay is **ratcheted** once its baseline reading met M5 or
reached its goal. On every pass:

1. **Coverage.** Every registered replay (`Documentation/replays.json`) is in
   the baseline, and every replay any committed version of the baseline ever
   held is still registered, or retired in `replays.json` with a `decision`
   that is the user's (below). A replay registered and not yet in the baseline
   fails until `--refresh` records it.
2. **Currency.** The tick-replay receipt describes the tree: it covers exactly
   the registered replays, under this registry, with each entry's inputs equal
   to the tree's now (`tick_replay.inputs`) and its M5 record derived from its
   own result (`tick_replay.m5_record`). METRICS.md's M5 row carries the same
   reading, and the stage-boot receipt holds a goal verdict for each stage
   replay. `check_replay_results.py` recomputes the comparisons themselves; this
   gate does not, so it costs a second, and it refuses readings it cannot tie
   to the tree rather than comparing them.
3. **Regression.** A ratcheted replay that now reads worse fails: it no longer
   meets M5 (`stage_exit.m5`, over its own part of the row, and the identity
   rule held for it, `tick_replay.identity_problems`), it compares less (fewer
   ticks judged, on the tick capture or the stage trace, a shorter native
   capture, or fewer words compared: a smaller mask or a wider residue class),
   or its goal is no longer reached. "Compares less" holds for every ratcheted
   replay, not only one that met M5: Metal Harbor reaches its goal and reads
   unmet, and its judged window is exactly what the user asked to have
   reported (review of 2026-09-26, F3). Each of those is the criterion met over
   less, or not met, so recording one takes the user's decision.
4. **Staleness.** A ratcheted replay whose reading changed any other way, and
   any replay that newly meets M5 or newly reaches its goal, fails until
   `--refresh` records it, so a gain is held rather than left to be lost again.
   A labelled residue divergence that moved, even earlier, is such a change and
   not a regression: M5 still holds, over as many ticks, and design 7.4 records
   a residue rather than fixing it. The refresh prints the move and the commit
   carries it. A replay that met nothing and still meets nothing may move
   either way; the gate prints a `note:` line for it, which `run_all_gates.py`
   relays.
5. **History.** Across every committed version of the baseline and the working
   one, a ratcheted replay's reading never got worse without an `accepted`
   record naming the move and a decision of the user's. A baseline edited by
   hand cannot turn a regression into a new baseline.

A decision of the user's, for this gate, is a file under
`Documentation/decisions/` whose Date paragraph says it was made or adopted by
the user (every one of theirs does) and that says, word for word (whitespace
folded), the one thing it authorises:

- "Retires replay `<name>`", to let a replay leave the registry;
- "Accepts replay `<name>` reading `<its M5 part>` (meets|does not meet M5), judged
  over <n> ticks[, its stage trace over <n>][, <n> words compared], <goal>", to
  record one worse reading: the part exactly as METRICS.md's M5 row writes it,
  whether it meets M5 (the identity rule can fail under an unchanged part), then
  what it was compared over. `--refresh` prints the sentence when it refuses.

Each is a sentence of its own, in a paragraph of the file, and the Date
paragraph opens a sentence with "Made by the user" or "Status: adopted by the
user": "not yet adopted by the user" or "never retires replay `boot`" authorise
nothing.

So a decision is written for exactly that move. An old decision that merely
names the replay authorises nothing (two of the user's decisions name `boot`,
and before 2026-09-26's review either would have retired it or accepted any
regression of it, again and again); a decision naming one worse reading does
not cover another. AGENTS.md rule 10 reserves loosening a criterion to the
user; retiring a replay, or accepting that it reads worse, loosens one. The text
test is the mechanical part of that rule, not the whole of it.

`--refresh`
-----------
Rewrites the baseline from the current readings, and in
`Documentation/fidelity-ledger.json` this suite's marker, its checker pin and
its claim's baseline pin, as `check_controls.py --refresh` does. It refuses to
run on a receipt that is not current, to record a ratcheted replay reading worse
unless `--accept-regression DECISION` names a decision of the user's that
accepts exactly that reading, and to drop a replay that left the registry
without one that retires it. A gain needs no reason.

Output ends with `marker_sha256=` over the ratcheted readings as compared, so
the ledger's pin tells "the replays still hold" from "the replays were not run".

    check_replay_regression.py                          compare, exit 1 on a drop
    check_replay_regression.py --refresh                rewrite the baseline
    --accept-regression Documentation/decisions/X.md    let --refresh record a drop
    --root PATH                                         repository (default: git toplevel)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from gitlog import project_root

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parents[1] / "tools" / "s4"))

BASELINE_REL = "Documentation/receipts/replay-baseline-v1.json"
REGISTRY_REL = "Documentation/replays.json"
TICK_REPLAY_REL = "Documentation/receipts/s4-tick-replay-v1.json"
STAGE_BOOT_REL = "Documentation/receipts/s4-stage-boot-v1.json"
RESIDUES_REL = "Documentation/receipts/residues"
METRICS_REL = "METRICS.md"
LEDGER_REL = "Documentation/fidelity-ledger.json"
SUITE_ID = "replay-regression"
CLAIM_ID = "replay-regression-baseline"
SCHEMA = "sa2-recomp-replay-baseline/v1"
DECISIONS_PREFIX = "Documentation/decisions/"
# The phrase opens a sentence or clause of the Date paragraph ("Made by the user, in their
# words below", "recorded by the agent; made by the user", "**Status: adopted by the user"): "not yet adopted by the user", a
# proposal awaiting the user's ruling, is no decision of theirs, nor "the user's agent".
USER_DECISION = re.compile(r"(?:^|[.!?;]\s+|\*\*Status:\s*)(?:made|adopted) by the user\b(?!['’])",
                           re.IGNORECASE)
# The Date paragraph: from the line that starts `Date:` to the first blank line.
DATE_PARAGRAPH = re.compile(r"^Date:.*?(?:\n[ \t]*\n|\Z)", re.M | re.S)
GOALS = {True: "reaches its goal", False: "does not reach its goal", None: "no goal"}

# The fields a reading is made of, and the ones a stored `accepted` move must
# name exactly.
READING = ("m5", "ticks", "first_divergent_tick", "field", "class", "judged_ticks",
           "trace_judged_ticks", "compared_words", "m5_met", "goal_complete")


class Unrunnable(Exception):
    """The inputs cannot be read: the gate is missing, not passing."""


# ------------------------------------------------------------------ readings

def render(rec: dict) -> str:
    """One replay's part of METRICS.md's M5 row, as metrics.py m5_replays renders it:
    stage_exit.m5_part once it exists (RC), so the two cannot drift apart."""
    import stage_exit as SE                                    # noqa: PLC0415
    if hasattr(SE, "m5_part"):
        return SE.m5_part(rec)
    if rec.get("first_divergent_tick") is None:
        return f"{rec['name']} no divergence/{rec['ticks']}"
    return f"{rec['name']} {rec['first_divergent_tick']}/{rec['ticks']} ({rec['class']}: {rec['field']})"


def part_meets_m5(part: str, residues: Path) -> bool:
    """G4's M5 predicate (`stage_exit.m5`) over this one replay's part of the row."""
    import stage_exit as SE                                    # noqa: PLC0415

    with tempfile.TemporaryDirectory() as td:
        row = Path(td) / "METRICS.md"
        row.write_text(f"| M5 replays | {part} | harness level 2/3 | measured |\n")
        return SE.m5(row, residues).met


def retires(name: str) -> str:
    """What a decision must say to let replay `name` leave the registry."""
    return f"Retires replay `{name}`"


def accepts(name: str, r: dict) -> str:
    """What a decision must say to let replay `name` be recorded reading `r`: its
    M5 part exactly as the row writes it, then what it was compared over, so one
    decision names one reading and covers no other."""
    # Whether it meets M5 is named too: the identity rule can fail under an unchanged
    # part, and a sentence written for the met reading must not accept that one.
    over = [f"`{r.get('m5')}` ({'meets' if r.get('m5_met') is True else 'does not meet'} M5)",
            f"judged over {r.get('judged_ticks')} ticks"]
    if r.get("trace_judged_ticks") is not None:
        over.append(f"its stage trace over {r['trace_judged_ticks']}")
    if r.get("compared_words") is not None:
        over.append(f"{r['compared_words']} words compared")
    over.append(GOALS[r.get("goal_complete")])
    return f"Accepts replay `{name}` reading " + ", ".join(over)


def says(text: str, sentence: str) -> bool:
    """`text` holds `sentence` as a sentence of its own: whitespace folded within a
    paragraph, opening the paragraph (after any list or quote marker or `**`) or
    following `.`, `!`, `?` or `:`, and ending there (`.`, `;`, `**` or the paragraph's
    end). Only its first letter may be lower case, so a replay's name and reading are
    matched exactly, and "never retires replay `boot`" or "Retires replay `boot` once
    S6 lands" says nothing."""
    first = sentence[:1]
    pat = re.compile(r"(?:^(?:[-*>]\s+)*(?:\*\*)?|[.!?:]\s+(?:\*\*)?)"
                     + f"[{re.escape(first.upper() + first.lower())}]" + re.escape(sentence[1:])
                     + r"(?=\*\*|[.;]|$)")
    # What a reader does not see as the record's text says nothing: a fenced block
    # (an example of the sentence) or an HTML comment.
    text = re.sub(r"<!--.*?-->", "", re.sub(r"(?ms)^```.*?^```[ \t]*$", "", text), flags=re.S)
    return any(pat.search(" ".join(p.split())) for p in re.split(r"\n[ \t]*\n", text))


def ratcheted(r: dict | None) -> bool:
    return bool(r) and (r.get("m5_met") is True or r.get("goal_complete") is True)


def reading_of(r: dict) -> dict:
    return {k: r.get(k) for k in READING}


def worse(base: dict, now: dict) -> list[str]:
    """How `now` reads worse than a ratcheted `base`, [] when it does not."""
    out = []
    if base.get("m5_met") is True and now.get("m5_met") is not True:
        out.append(f"no longer meets M5 (was `{base.get('m5')}`, now `{now.get('m5')}`)")
    if ratcheted(base):                    # compared over less: M5 met or a goal reached
        if base.get("compared_words") is not None and \
                (now.get("compared_words") or 0) < base["compared_words"]:
            out.append(f"compared {now.get('compared_words')} words, was {base['compared_words']}")
        if (now.get("judged_ticks") or 0) < (base.get("judged_ticks") or 0):
            out.append(f"judged {now.get('judged_ticks')} ticks, was {base.get('judged_ticks')}")
        if base.get("trace_judged_ticks") is not None and \
                (now.get("trace_judged_ticks") or 0) < base["trace_judged_ticks"]:
            out.append(f"its stage trace judged {now.get('trace_judged_ticks')} ticks, was "
                       f"{base['trace_judged_ticks']}")
        if (now.get("ticks") or 0) < (base.get("ticks") or 0):
            out.append(f"its native capture holds {now.get('ticks')} ticks, was {base.get('ticks')}")
    if base.get("goal_complete") is True and now.get("goal_complete") is not True:
        out.append(f"its lifted run no longer reaches the goal ({GOALS[now.get('goal_complete')]})")
    return out


def changed(base: dict, now: dict) -> list[str]:
    """The reading fields that differ, by name."""
    return [k for k in READING if base.get(k) != now.get(k)]


def current_readings(root: Path) -> tuple[dict[str, dict], list[str]]:
    """Every registered replay's reading now, and why any of them cannot be tied
    to the tree (check 2). Raises Unrunnable when an input cannot be read."""
    import tick_replay as T                                    # noqa: PLC0415
    import stage_exit as SE                                    # noqa: PLC0415

    def load(rel: str) -> dict:
        try:
            return json.loads((root / rel).read_text())
        except (OSError, ValueError) as e:
            raise Unrunnable(f"{rel} cannot be read: {e}") from e

    registry, receipt, stage_boot = load(REGISTRY_REL), load(TICK_REPLAY_REL), load(STAGE_BOOT_REL)
    try:
        metrics_text = (root / METRICS_REL).read_text()
    except OSError as e:
        raise Unrunnable(f"{METRICS_REL} cannot be read: {e}") from e
    entries = {e["name"]: e for e in registry.get("replays") or []}
    docs = {d.get("name"): d for d in receipt.get("replays") or []}
    m5 = {r.get("name"): r for r in receipt.get("m5") or []}
    goals = {v.get("replay"): v for v in stage_boot.get("replays") or []}
    row = {p.strip().split(" ", 1)[0]: p.strip()
           for p in SE.row(metrics_text, "M5 replays").split(";") if p.strip()}

    stale: list[str] = []
    rerun = "re-run tools/s4/tick_replay.py --receipt"
    if receipt.get("registry_sha256") != T.sha256(root / REGISTRY_REL):
        stale.append(f"{TICK_REPLAY_REL} was written under another {REGISTRY_REL}; {rerun}")
    if sorted(docs) != sorted(entries):
        stale.append(f"{TICK_REPLAY_REL} holds {sorted(docs)} and the registry registers "
                     f"{sorted(entries)}; a registered replay that is not run is not compared")
    try:
        ident = T.identity_problems(receipt, registry, root)
    except SystemExit as e:
        ident = [f"the identity rule cannot be checked: {e}"]      # unmeets every replay
    readings: dict[str, dict] = {}
    for name, entry in entries.items():
        doc = docs.get(name)
        if doc is None:
            continue
        try:
            now = T.inputs(entry, root)
        except SystemExit as e:
            stale.append(f"{name}: its inputs cannot be computed: {e}")
            continue
        if now != (doc.get("inputs") or {}):
            stale.append(f"{name}: its recorded inputs are not the tree's; {rerun}")
        try:
            rec = T.m5_record(doc)
        except (KeyError, TypeError) as e:
            stale.append(f"{name}: its recorded result has no M5 reading: {e!r}")
            continue
        if m5.get(name) != rec:
            stale.append(f"{name}: the receipt's m5 record is not derived from its result")
        part = render(rec)
        if row.get(name) != part:
            stale.append(f"{name}: METRICS.md's M5 row reads {row.get(name)!r} and the receipt "
                         f"{part!r}; regenerate METRICS.md with metrics.py --write")
        result = doc.get("result") or {}
        goal = None
        if T.is_stage(entry):
            verdict = goals.get(name)
            if verdict is None:
                stale.append(f"{name}: {STAGE_BOOT_REL} holds no goal verdict for this stage "
                             "replay; re-run tools/s4/stage_boot.py --write")
            else:
                goal = verdict.get("complete") is True
        own = [p for p in ident if p.startswith(f"{name}: ")]
        loose = [p for p in ident if not any(p.startswith(f"{n}: ") for n in entries)]
        readings[name] = {
            "m5": part, "ticks": rec.get("ticks"),
            "first_divergent_tick": rec.get("first_divergent_tick"),
            "field": rec.get("field"), "class": rec.get("class"),
            "judged_ticks": result.get("judged_ticks"),
            "trace_judged_ticks": (result.get("trace") or {}).get("judged_ticks"),
            "compared_words": (result.get("mask") or {}).get("words"),
            "m5_met": part_meets_m5(part, root / RESIDUES_REL) and not own and not loose,
            "goal_complete": goal,
        }
    return readings, stale


# ------------------------------------------------------------------ decisions

def decision_problems(root: Path, path: object, name: str, sentence: str) -> list[str]:
    """Why `path` is not a decision of the user's (so its Date paragraph says) that
    says `sentence` (`retires` or `accepts`, filled in) about replay `name`."""
    if not isinstance(path, str) or not path.startswith(DECISIONS_PREFIX) \
            or not path.endswith(".md") or ".." in Path(path).parts:
        return [f"{path!r} is not a decision record under {DECISIONS_PREFIX}"]
    p = root / path
    if not p.is_file():
        return [f"{path} does not exist"]
    text = p.read_text(errors="replace")
    out = []
    date = DATE_PARAGRAPH.search(text)
    if not date or not USER_DECISION.search(" ".join(date.group(0).split())):
        out.append(f"{path} does not say in its Date paragraph that it was made or adopted by "
                   "the user (AGENTS.md rule 10: loosening a criterion is the user's)")
    if not says(text, sentence):
        out.append(f"{path} does not say \"{sentence}\"; a decision is written for exactly the "
                   "move it allows, and one that merely names the replay allows nothing")
    return out


# ------------------------------------------------------------------ baseline and history

def load_baseline(text: str) -> dict[str, dict]:
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA \
            or not isinstance(doc.get("replays"), list):
        raise ValueError(f"not a {SCHEMA} document")
    out = {}
    for r in doc["replays"]:
        if not isinstance(r, dict) or not isinstance(r.get("name"), str):
            raise ValueError("a replays[] entry has no name")
        out[r["name"]] = r
    return out


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)


def versions(root: Path) -> tuple[list[tuple[str, dict[str, dict]]], list[str]]:
    """Every committed version of the baseline, oldest first, then the working one
    when it differs from HEAD's; and a note per committed version that does not
    parse (skipped: the ratchet is carried across it, not reset by it)."""
    log = git(root, "log", "--format=%H", "--", BASELINE_REL)
    if log.returncode:
        raise Unrunnable(f"git log -- {BASELINE_REL} failed: {log.stderr.strip()}")
    out, notes = [], []
    for commit in reversed(log.stdout.split()):
        shown = git(root, "show", f"{commit}:{BASELINE_REL}")
        if shown.returncode:
            continue                                       # the commit that deleted it
        try:
            out.append((commit[:12], load_baseline(shown.stdout)))
        except (ValueError, TypeError) as e:
            notes.append(f"note: the baseline at {commit[:12]} does not parse ({e}); the "
                         "ratchet is carried across it")
    work = root / BASELINE_REL
    if work.is_file():
        text = work.read_text()
        at_head = git(root, "show", f"HEAD:{BASELINE_REL}")
        if at_head.returncode or at_head.stdout != text:
            try:
                out.append(("working tree", load_baseline(text)))
            except (ValueError, TypeError) as e:
                raise Unrunnable(f"{BASELINE_REL} does not parse: {e}") from e
    return out, notes


def unaccepted_moves(root: Path, seq: list[tuple[str, dict[str, dict]]]) -> list[tuple]:
    """Each move of a ratcheted replay to a worse reading, between consecutive
    versions that hold it, that no `accepted` record at or after it covers with a
    decision of the user's: (name, from version, to version, from, to, why)."""
    out = []
    last: dict[str, tuple[str, dict]] = {}
    for k, (label, doc) in enumerate(seq):
        for name, entry in doc.items():
            if name in last and ratcheted(last[name][1]):
                prev_label, prev = last[name]
                why = worse(prev, entry)
                if why:
                    move = (reading_of(prev), reading_of(entry))
                    covered = any(
                        (a.get("from"), a.get("to")) == move
                        and not decision_problems(root, a.get("decision"), name,
                                                  accepts(name, move[1]))
                        for _, later in seq[k:] for a in (later.get(name) or {}).get("accepted") or []
                        if isinstance(a, dict))
                    if not covered:
                        out.append((name, prev_label, label, move[0], move[1], why))
            last[name] = (label, entry)
    return out


# ------------------------------------------------------------------ the gate

def problems(root: Path, readings: dict[str, dict], stale: list[str],
             seq: list[tuple[str, dict[str, dict]]], registry: dict,
             baseline: dict[str, dict] | None) -> tuple[list[str], list[str]]:
    """(failures, notes) for checks 1 and 3 to 5; `stale` is check 2's."""
    failures = list(stale)
    notes: list[str] = []
    if baseline is None:
        return failures + [f"MISSING no baseline at {BASELINE_REL}; write one with --refresh "
                           "and commit it"], notes
    registered = [e["name"] for e in registry.get("replays") or []]
    retired = {r.get("name"): r for r in registry.get("retired") or [] if isinstance(r, dict)}
    for name in registered:
        if name not in baseline:
            failures.append(f"NOT IN BASELINE {name}: registered, and {BASELINE_REL} does not "
                            "hold it; run --refresh so every later stage compares it")
    ever: dict[str, str] = {}
    for label, doc in seq:
        for name in doc:
            ever.setdefault(name, label)
    for name, first in sorted(ever.items()):
        if name in registered:
            continue
        why = decision_problems(root, (retired.get(name) or {}).get("decision"), name,
                                retires(name))
        if why:
            failures.append(f"DROPPED {name}: in the baseline since {first}, no longer registered, "
                            f"and not retired on a decision of the user's that says "
                            f"\"{retires(name)}\" ({'; '.join(why)}); "
                            "design 7.3: a replay never silently stops being run")
    for name, frm, to, _, _, why in unaccepted_moves(root, seq):
        failures.append(f"HISTORY {name}: the baseline recorded it reading worse from {frm} to "
                        f"{to} ({'; '.join(why)}) with no accepted record naming a decision of "
                        "the user's")
    for name in registered:
        base, now = baseline.get(name), readings.get(name)
        if base is None or now is None:
            continue                              # NOT IN BASELINE or stale, said above
        moved = changed(base, now)
        if ratcheted(base):
            why = worse(base, now)
            if why:
                failures.append(f"REGRESSION {name}: {'; '.join(why)}")
            elif moved:
                failures.append(f"BASELINE STALE {name}: reads `{now['m5']}` "
                                f"({GOALS[now['goal_complete']]}), and the baseline records "
                                f"`{base.get('m5')}` ({GOALS[base.get('goal_complete')]}); "
                                "rerun with --refresh and commit the baseline")
        elif ratcheted(now):
            gained = " and ".join(w for w, on in (("meets M5", now["m5_met"]),
                                                  ("reaches its goal", now["goal_complete"])) if on)
            failures.append(f"BASELINE STALE {name}: {gained} now (`{now['m5']}`), and the "
                            "baseline does not record it; rerun with --refresh and commit it")
        elif moved:
            notes.append(f"note: {name} meets neither M5 nor a goal, and reads `{now['m5']}` "
                         f"against the baseline's `{base.get('m5')}` ({', '.join(moved)})")
    return failures, notes


def compared(readings: dict[str, dict]) -> dict:
    """What the marker is taken over: each replay's ratcheted reading, and only that,
    so an unratcheted replay moving does not move the ledger's pin."""
    return {name: {"m5": r["m5"] if r["m5_met"] else "unmet",
                   "judged": ([r["judged_ticks"], r["trace_judged_ticks"], r.get("compared_words")]
                              if ratcheted(r) else None),
                   "goal": GOALS[r["goal_complete"]]}
            for name, r in sorted(readings.items())}


def marker(readings: dict[str, dict]) -> str:
    canonical = json.dumps(compared(readings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ refresh

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh(root: Path, readings: dict[str, dict], stale: list[str], registry: dict,
            seq: list[tuple[str, dict[str, dict]]], baseline: dict[str, dict] | None,
            decision: str | None) -> int:
    if stale:
        print("replay regression REFUSED: --refresh records readings, and these cannot be tied "
              "to the tree:", file=sys.stderr)
        for line in stale:
            print(f"  {line}", file=sys.stderr)
        return 1
    ledger_path = root / LEDGER_REL
    try:
        ledger = json.loads(ledger_path.read_text())
    except (OSError, ValueError) as e:
        print(f"replay regression REFUSED: {LEDGER_REL} cannot be read: {e}", file=sys.stderr)
        return 1
    suite = next((s for s in ledger.get("suites") or [] if s.get("id") == SUITE_ID), None)
    claim = next((c for c in ledger.get("claims") or [] if c.get("id") == CLAIM_ID), None)
    if suite is None or claim is None:
        print(f"replay regression REFUSED: {LEDGER_REL} registers no suite {SUITE_ID!r} with "
              f"claim {CLAIM_ID!r}; design 7.1 registers every level's suites there",
              file=sys.stderr)
        return 1
    previous = baseline or {}
    registered = [e["name"] for e in registry.get("replays") or []]
    retired = {r.get("name"): r for r in registry.get("retired") or [] if isinstance(r, dict)}
    refusals, moves = [], []
    for name in sorted(set(previous) - set(registered)):
        why = decision_problems(root, (retired.get(name) or {}).get("decision"), name,
                                retires(name))
        if why:
            refusals.append(f"{name} left the registry and is not retired on a decision of the "
                            f"user's that says \"{retires(name)}\": {'; '.join(why)}")
        else:
            moves.append(f"  retired: {name} ({retired[name]['decision']})")
    entries = {}
    for name in registered:
        now = readings.get(name)
        if now is None:
            refusals.append(f"{name} is registered and has no reading")
            continue
        entry = {"name": name, **reading_of(now),
                 "accepted": list((previous.get(name) or {}).get("accepted") or [])}
        entries[name] = entry
        base = previous.get(name)
        if base is None:
            moves.append(f"  recorded: {name} `{now['m5']}` ({GOALS[now['goal_complete']]})")
        elif changed(base, now):
            moves.append(f"  moved: {name} `{base.get('m5')}` -> `{now['m5']}` "
                         f"[{', '.join(changed(base, now))}]"
                         + (f" ({'; '.join(worse(base, now))})" if ratcheted(base) and
                            worse(base, now) else ""))
    # Every worse move the new baseline would make, and any made in history that
    # no accepted record covers yet: each needs the user's decision.
    new_seq = seq + [("refresh", entries)]
    for name, frm, to, a, b, why in unaccepted_moves(root, new_seq):
        problems_ = (decision_problems(root, decision, name, accepts(name, b))
                     if decision else ["no decision given"])
        if problems_:
            refusals.append(f"{name} would be recorded reading worse ({frm} -> {to}: "
                            f"{'; '.join(why)}); pass --accept-regression with a decision of the "
                            f"user's that says \"{accepts(name, b)}\" ({'; '.join(problems_)})")
        else:
            entries[name]["accepted"].append({"decision": decision, "from": a, "to": b})
            moves.append(f"  accepted: {name} {frm} -> {to} ({decision})")
    if refusals:
        print("replay regression REFUSED:", file=sys.stderr)
        for line in refusals:
            print(f"  {line}", file=sys.stderr)
        return 1
    receipt = json.loads((root / TICK_REPLAY_REL).read_text())
    out = root / BASELINE_REL
    out.write_text(json.dumps({
        "schema": SCHEMA,
        "id": "replay-baseline-v1",
        "question": ("Which registered replays must every later stage keep running and "
                     "comparing, and which of them met M5 or reached their goal, so that a "
                     "later reading that is worse fails a gate (design 7.3; the user, "
                     "2026-09-26)?"),
        "note": ("Names, tick numbers, field names and classes only, as METRICS.md's M5 row "
                 "and the stage-boot receipt state them: nothing derived from the shipped "
                 "executable's bytes. `accepted` records a move to a worse reading, the "
                 "user's decision that allowed it, and the two readings."),
        "authority": "observed",
        "written_by": "Documentation/scripts/check_replay_regression.py --refresh",
        "taken_from": {TICK_REPLAY_REL: {"sha256": sha256_file(root / TICK_REPLAY_REL),
                                         "commit": receipt.get("commit")},
                       STAGE_BOOT_REL: {"sha256": sha256_file(root / STAGE_BOOT_REL)}},
        "replays": [entries[n] for n in sorted(entries)],
    }, indent=2, sort_keys=True) + "\n")
    suite["marker"] = {"kind": "sha256", "value": marker(readings)}
    for pin in list(suite.get("files") or []) + list(claim.get("sources") or []):
        if isinstance(pin, dict) and (root / str(pin.get("path"))).is_file():
            pin["sha256"] = sha256_file(root / pin["path"])
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    for line in moves:
        print(line)
    met = sum(1 for r in readings.values() if r["m5_met"])
    goals = sum(1 for r in readings.values() if r["goal_complete"])
    print(f"replay baseline written: {len(entries)} replays, {met} meeting M5, {goals} reaching "
          f"their goal -> {BASELINE_REL}; {LEDGER_REL} suite {SUITE_ID} re-pinned")
    print(f"marker_sha256={marker(readings)}")
    return 0


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None,
         now: Callable[[Path], tuple[dict[str, dict], list[str]]] = current_readings) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--refresh", action="store_true",
                        help="rewrite the baseline and this suite's ledger pins from this run")
    parser.add_argument("--accept-regression", default=None, metavar="DECISION",
                        help="let --refresh record a ratcheted replay reading worse, on this "
                             "decision of the user's (a Documentation/decisions/*.md that says "
                             "\"Accepts replay `<name>` reading ...\" for exactly that reading)")
    args = parser.parse_args(argv)
    root = project_root(args.root)
    try:
        registry = json.loads((root / REGISTRY_REL).read_text())
        readings, stale = now(root)
        seq, history_notes = versions(root)
        text = (root / BASELINE_REL).read_text() if (root / BASELINE_REL).is_file() else None
        baseline = load_baseline(text) if text is not None else None
    except (Unrunnable, OSError, ValueError) as why:
        print(f"replay regression MISSING: {why}", file=sys.stderr)
        return 1
    if args.refresh:
        return refresh(root, readings, stale, registry, seq, baseline, args.accept_regression)
    failures, notes = problems(root, readings, stale, seq, registry, baseline)
    for line in history_notes + notes:
        print(line)
    for line in failures:
        print(f"  {line}")
    if failures:
        sys.stdout.flush()
        print(f"replay regression FAIL: {len(failures)} problem(s) over "
              f"{len(registry.get('replays') or [])} registered replays", file=sys.stderr)
        return 1
    met = sum(1 for r in readings.values() if r["m5_met"])
    goals = sum(1 for r in readings.values() if r["goal_complete"])
    print(f"replay regression OK: {len(readings)} registered replays run and compared, "
          f"{met} meeting M5 and {goals} reaching their goal as the baseline records, "
          f"0 regressions; {len(seq)} baseline version(s) checked")
    print(f"marker_sha256={marker(readings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
