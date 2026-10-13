#!/usr/bin/env python3
"""Gate: every refusal has a recorded reason, and says whether it reproduces native.

What this gate is for
---------------------
Design 3.5 classifies every import of the shipped executable into OS services,
presentation, Steam and DRM. For two of those classes the design says what the
runtime must do, and says it in one sentence each:

* **presentation** -- stage S4's work list: "presentation stubs as no-ops that
  record calls";
* **Steam** -- section 3.5: "`steam_api`, `SteamMetricsSDK`: no-op stubs
  returning success".

So a presentation or Steam import that *refuses* -- traps for want of a stub,
or answers with failure by policy -- is a nonconformance with the design, not
a design choice, unless somebody wrote down why. This gate is that "unless".

Why it exists as a gate and not as a code review
------------------------------------------------
Because the last one was found by its damage rather than by reading. The
DirectSound device returned failure instead of succeeding and recording, which
disabled CRI's *file system* five call sites away; the guest stopped in
`adxf_GetPtStat` and the refusal took a slice to find. Nothing was wrong with
the reasoning that produced the refusal -- what was missing was any way to
enumerate refusals and ask, of each one, whether the design already said what
it should do.

One refusal being wrong is a bug. No way to list them is the defect, because
the next one is found the same expensive way.

What counts as a reason
-----------------------
A reason says why the refusal exists and carries a falsifier: the observation
that would make the reason wrong and the refusal a defect. "Not implemented
yet" is not a reason, and this gate rejects it by name along with its usual
disguises, because a reasons file that accepts one is a queue with a nicer
label.

The native outcome (the user's ruling of 2026-09-23)
----------------------------------------------------
"A refusal counts as implemented only if it reproduces the native outcome"
(`Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md`). So every
entry carries `native: {outcome, reproduces, switches?, requires_unset?}`,
`tools/s4/import_status.py` counts an import only when its entry's
`reproduces` is true, and this gate holds each part to something that can
fail:

* `outcome` is an outcome id in `Documentation/receipts/s4-native-outcomes-v1.json`,
  and that outcome names this entry's import -- or it is null, which says no
  traced native boot measured the import, and then `reproduces` must be false:
  a refusal is not shown to reproduce an outcome nobody measured;
* the receipt recomputes from its traced native logs (`tools/s4/native_outcomes.py
  --check`): every log exists and hashes to its pin, and every cited line is
  the line;
* `reproduces` is a boolean, and it may be true only when:
  - every switch the entry names is on in the configuration the retained
    replays run -- the reach receipt's environment -- because a device that
    is off in the replays is a refusal the replays run;
  - the outcome's answer class (the receipt's `answer`) is `failure`, or the
    entry names at least one switch. A refusal answers failure, so when native
    answers success the refusal reproduces it only while a switch replaces it
    with a device that succeeds. Before 2026-09-23's review the switch list was
    the only guard, so an entry that claimed `reproduces: true` and deleted its
    switches passed, and M4 counted it;
  - when native answers success, the reach run called the import and its
    runtime never refused it. A switch's name is the entry's own word, so the
    rule above alone was met by any switch that happened to be on (the gate
    fold's verification, N1). The reach receipt records every row of the run's
    import table with its calls and its refusals (`sa2_import_refused` counts
    them, `tools/s4/reach_receipt.py refusals` copies them), and a success is
    claimed only against a row with calls above zero and no refusal. **The
    counts are not taken as written**: the gate recomputes them with
    `reach_receipt.refusals` from the table the receipt names, which must hash
    to the receipt's sha256, and fails when the two differ. **A missing row is
    not a zero**: the table dumps the import-address-table rows only
    (`harness/crt_probe.c --dump-imports`), so an import resolved through
    GetProcAddress cannot claim a success until something records its counts.
    A receipt that records no counts refuses every success claim, and so does
    one whose table is not on disk as recorded. The table lives under
    `private/`, which is never committed, so **on a checkout without
    `private/` every success claim fails**: the gate fails closed rather than
    read the receipt's copy as evidence. (The traced native logs the outcomes
    are recomputed from live there too, so that checkout fails this gate
    regardless.);
  - no variable the entry lists in `requires_unset` is set in that
    configuration. The XInput entries list SA2_XINPUT_PAD, which fabricates a
    connected pad the native game never had;
  - the entry's kind is not `no-stub` or `null-impl`. Such a refusal is a
    trap: the guest's call stops the run by name and gets no answer, and no
    answer is no outcome. The rules above judge the configuration an answer
    comes from, so a null-impl entry that claimed to reproduce, with its switch
    on and a count showing its import called and never refused, passed every
    one of them (the fold review of the gate push, 2026-09-23).

What no gate here can see is a refusal whose failure is a different failure
from native's -- the runtime answering E_FAIL where native answers 1167. The
marker's code is often a variable, so it is not compared; an entry whose
outcome answers failure says in its reason which code native gets and why the
runtime's is the same.

A refusal that does not reproduce is recorded, not excused: the gate accepts
`reproduces: false` with a reason, and M4 leaves the import out of its
implemented count. The summary line counts both.

Policy refusals, and the marker regime
--------------------------------------
The runtime marks a refusal by policy with `sa2_import_refused("DLL", "Name",
code)`, and `tools/s4/refusal_audit.py` reads the calls out of `runtime/*.c`.
**Once any marker exists**, a marker with no entry and a policy entry with no
marker both fail this gate. **Before the first marker, neither can be checked**:
a policy refusal is then only its entry, and deleting a policy entry makes its
refusal invisible. The summary line says which regime ran (`marker_checks=on`
or `off`), so a pass under the weaker one is never read as a pass under the
stronger.

Falsification
-------------
Every pass mutates the record -- the reasons file, the native-outcomes
receipt, the reach receipt (beside an import table written for the case), or
the runtime's markers -- in a temporary copy, and requires the gate
to fail on each mutation **for the reason the mutation targets, with a problem
the unmutated record does not already have**. Without that subtraction a case
passed whenever its phrase was among the live record's own problems: the
harness reported every case caught while the gate itself failed with four
problems (2026-09-23 review). So the gate is shown failing before it is shown
passing on the same run.

Each mutation's target is found in the data, never named: a case that names
its target stops constructing its condition the day ordinary work removes the
target, and then passes the gate while claiming to test it. Where ordinary work
is *expected* to remove every target, the case is built synthetically instead:
the claim-with-a-switch-off case names a switch no configuration sets
(`SA2_FALSIFY_SWITCH_OFF`), because turning every device on in the replay
configuration is the goal, and a case that needs an entry whose switch is off
would then fail the gate for ever; and the trap-claim case declares the first
entry null-impl, because the work list ends when no trap entry is left. A case
whose condition cannot arise in the data is **inapplicable**, not failed, and
the summary line names it: a policy refusal for an import with no stub needs
an import slot with no stub, and the work list ends when there is none. A
check nobody has watched fail is a check nobody has watched.

Usage: .venv/bin/python Documentation/scripts/check_refused_presentation.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import native_outcomes as NO  # noqa: E402
import reach_receipt as RR  # noqa: E402
from refusal_audit import KINDS, MARKER, REASONS, audit, key  # noqa: E402

# The classes whose behaviour design 3.5 fixes. `os-service` is deliberately
# absent: the design does not say an OS service may be a no-op, and most of
# them cannot be -- a `CreateFileA` that succeeds and does nothing is worse
# than one that traps. Those refusals are a work list, not a conformance
# question; `tools/s4/refusal_audit.py` lists them. An os-service entry that
# exists is still held to everything below, native block included.
GATED = ("presentation", "steam")

SCHEMA = "sa2-recomp-refusal-reasons/v2"
REQUIRED = ("dll", "name", "class", "kind", "recorded", "why",
            "falsifier", "owner_stage")
OUTCOMES = NO.RECEIPT
REACH = ROOT / "Documentation" / "receipts" / "s4-run-reach-v1.json"
SWITCH = re.compile(r"SA2_[A-Z0-9_]+\Z")
# A switch name no replay configuration sets, for the synthetic falsification
# of the switch rule (module docstring, "Falsification").
OFF_SWITCH = "SA2_FALSIFY_SWITCH_OFF"

# A reason that is a queue entry wearing a reason's clothes. Matched against a
# lower-cased `why`; each of these has been written by somebody, in some
# repository, meaning "later".
NON_REASONS = ("not implemented", "not yet implemented", "todo", "tbd",
               "later", "future work", "no time", "out of scope for now",
               "will be done", "placeholder", "stub pending", "unimplemented")

MIN_WHY = 80          # characters. A one-line reason is a label.
MIN_FALSIFIER = 30

# The kinds whose refusal is a trap rather than an answer: the guest's call
# stops the run by name. Only a `policy` refusal answers anything.
TRAPS = ("no-stub", "null-impl")


def replay_environment(reach: Path | None = None) -> dict[str, str]:
    """The environment the retained replays run: the reach receipt's manifest."""
    return dict(json.loads((reach or REACH).read_text())["manifest"]["environment"])


# What the reach run did with each import it dumped: {refusal_audit.key:
# {"calls": n, "refused": m}}.
Counts = dict[tuple[str, str], dict[str, int]]


def _count(n) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError(n)
    return n


def _parse_counts(raw: dict, what: str) -> tuple[Counts, list[str]]:
    """`raw` ({"DLL!name": {"calls": n, "refused": m}}) keyed by refusal_audit.key,
    and every entry that is not one, as a sentence. A malformed entry is
    reported and left out, so the import it names has no count and a success
    claim for it is refused; it is never read as zero. Two spellings of one
    import are summed, so neither's refusals are lost."""
    counts: Counts = {}
    out: list[str] = []
    for k, v in raw.items():
        if not isinstance(k, str) or "!" not in k:
            out.append(f"{what} has the key {k!r}, which names no DLL!import, so its counts "
                       "belong to no import")
            continue
        try:
            c = {f: _count(v[f]) for f in ("calls", "refused")}
        except (TypeError, KeyError, ValueError):
            out.append(f"{what} for {k} is {v!r}, not {{\"calls\": n, \"refused\": m}} with "
                       "whole non-negative counts")
            continue
        mine = key(*k.split("!", 1))
        if mine in counts:
            c = {f: counts[mine][f] + c[f] for f in c}
        counts[mine] = c
    return counts, out


def replay_refusals(reach: Path | None = None) -> tuple[Counts | str | None, list[str]]:
    """What the reach run did with each import, as claim_problems takes it, and
    every way the receipt's record of it is wrong, as sentences.

    The receipt's `reach.import_table.refused` is what tools/s4/reach_receipt.py
    refusals() made of the run's import table. It is held to that table, never
    taken as written. The first value is:

    * None when the receipt records no counts, or a record that is not a
      mapping. A receipt from before the marker counted says nothing about
      which refusals a device replaced;
    * a sentence when the table the receipt names is not on disk as recorded:
      absent (a checkout without private/, or a run in progress), rewritten since
      (another sha256), or without a `refused_calls` column. Counts that nothing
      can recompute are only the receipt's word, so claim_problems refuses every
      success claim with this sentence. The gate fails closed without private/;
    * otherwise the table's own counts, `reach_receipt.refusals` of it, and a
      problem when the receipt's differ. The claims are judged on the table, so
      an edited receipt can neither pass a claim nor go unreported.

    The import table's path is read against the repository root, as
    tools/s4/import_status.py reach_table reads it. No overlay or copied receipt
    moves private/.
    """
    doc = json.loads((reach or REACH).read_text())
    table = (doc.get("reach") or {}).get("import_table") or {}
    got = table.get("refused")
    if got is None:
        return None, []
    what = "the reach receipt's refusal record (reach.import_table.refused)"
    if not isinstance(got, dict):
        return None, [f"{what} is {type(got).__name__}, not a mapping of DLL!import to its "
                      "calls and refusals, so it records no counts"]
    _, out = _parse_counts(got, what)
    rel = table.get("path")
    path = ROOT / str(rel) if rel else None
    unchecked = (f"the reach receipt's refusal counts cannot be recomputed: the import table "
                 f"it names ({rel!r})")
    if path is None or not path.is_file():
        return (f"{unchecked} is not on disk (a checkout without private/, or a run that has "
                "not finished), and counts nothing can recompute are the receipt's word, not "
                "evidence"), out
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != table.get("sha256"):
        return (f"{unchecked} hashes to {digest[:12]} and the receipt records "
                f"{str(table.get('sha256'))[:12]}, so it is not the table the counts were "
                "written from"), out
    real = RR.refusals(path)
    if real is None:
        return (f"{unchecked} has no refused_calls column"), out + [
            f"{what} holds counts, and the import table it names ({rel}) has no "
            "refused_calls column they could have been written from"]
    if got != real:
        moved = sorted(k for k in set(got) | set(real) if got.get(k) != real.get(k))
        out.append(f"{what} is not what tools/s4/reach_receipt.py refusals() makes of the "
                   f"import table it names ({rel}): {len(moved)} import(s) differ, first "
                   f"{moved[0]} (receipt {got.get(moved[0])!r}, table {real.get(moved[0])!r})")
    counts, bad = _parse_counts(real, f"the import table {rel}'s counts")
    return counts, out + (bad if got != real else [])


def _names(v) -> bool:
    return isinstance(v, list) and all(isinstance(s, str) and SWITCH.match(s) for s in v)


def success_count_problem(who: str, oid, mine: tuple[str, str],
                          refused: Counts | str | None) -> str | None:
    """Why the reach run's counts do not show a claimed success, or None when
    the run called the import and never refused it."""
    head = f"{who} claims to reproduce outcome {oid}, which answers success natively, but "
    if refused is None:
        return head + ("the reach receipt records no refusal counts, so nothing shows the "
                       "replay configuration replaced the refusal")
    if isinstance(refused, str):
        return head + refused
    got = refused.get(mine)
    if got is None:
        return head + ("the reach run's import table has no row for it, and a count nobody "
                       "recorded is not a count of zero: harness/crt_probe.c --dump-imports "
                       "dumps the import-address-table rows only, so an import resolved "
                       "through GetProcAddress, or one the table does not hold, has no count")
    if got["refused"]:
        return head + (f"the reach run's runtime refused it {got['refused']} time(s): a switch "
                       "that is on is not a device that answered")
    if not got["calls"]:
        return head + ("the reach run never called it, so no answer of the runtime's shows "
                       "the refusal replaced")
    return None


def claim_problems(doc: dict, rec: dict, env: dict[str, str],
                   refused: Counts | str | None) -> list[str]:
    """Every way an entry's native block says more than the native-outcomes
    receipt `rec`, the replay configuration `env` and the reach run's counts
    `refused` (replay_refusals: the counts, a sentence saying why they cannot
    be recomputed, or None) show, as sentences. Reads nothing: M4's criterion
    runs it on the files its clause gate hands it."""
    out: list[str] = []
    by_id = {o.get("id"): o for o in rec.get("outcomes", []) if isinstance(o, dict)}
    for r in doc.get("reasons", []):
        who = f"{r.get('dll', '?')}!{r.get('name', '?')}"
        nat = r.get("native")
        if not isinstance(nat, dict):
            out.append(f"{who} has no native block, so nothing says whether its answer "
                       "reproduces the native outcome")
            continue
        rep = nat.get("reproduces")
        if not isinstance(rep, bool):
            out.append(f"{who}: native.reproduces is {rep!r}, not true or false")
        if rep is True and r.get("kind") in TRAPS:
            out.append(f"{who} is a {r.get('kind')} refusal, which traps when the guest calls "
                       "it and answers nothing, and claims to reproduce the native outcome; a "
                       "trap reproduces no outcome")
        oid, answer = nat.get("outcome"), None
        mine = key(str(r.get("dll", "")), str(r.get("name", "")))
        if oid is None:
            if rep is not False:
                out.append(f"{who} names no native outcome (outcome null), and says reproduces "
                           f"{rep!r}; an entry without a receipt outcome may only say false "
                           "(null: the native-outcomes receipt holds none for it, whatever "
                           "else its why cites)")
        elif oid not in by_id:
            out.append(f"{who} names native outcome {oid!r}, which "
                       f"{NO.RECEIPT_REL} does not have")
        else:
            names = by_id[oid].get("imports", [])
            if mine not in {key(*i.split("!", 1)) for i in names if "!" in i}:
                out.append(f"{who}: outcome {oid} is about {', '.join(names)}, not this import")
            answer = by_id[oid].get("answer")
            if answer not in NO.ANSWERS:
                out.append(f"{who}: outcome {oid}'s answer class is {answer!r}, not one of "
                           f"{', '.join(NO.ANSWERS)}, so whether a refusal can reproduce it "
                           "is unknown")
        sw, unset = nat.get("switches", []), nat.get("requires_unset", [])
        if not _names(sw):
            out.append(f"{who}: native.switches is {sw!r}, not a list of SA2_* switch names")
        if not _names(unset):
            out.append(f"{who}: native.requires_unset is {unset!r}, not a list of SA2_* names")
        if rep is not True or not _names(sw) or not _names(unset):
            continue
        off = [s for s in sw if env.get(s) != "1"]
        if off:
            out.append(f"{who} claims to reproduce the native outcome, but {', '.join(off)} "
                       "is not on in the replay configuration (the reach receipt's "
                       "environment), so the replays run the refusal")
        if answer == NO.SUCCESS and not sw:
            out.append(f"{who} claims to reproduce outcome {oid}, which answers success "
                       "natively, and names no switch: a refusal answers failure, so it "
                       "reproduces a success only while a switch that replaces it with a "
                       "device is on in the replay configuration")
        # A switch's name is the entry's own word, so any switch that is on
        # would satisfy the rule above (the gate fold's verification, N1). What
        # the runtime did is not: a success is reproduced only if the reach run
        # called the import and never refused it.
        why = success_count_problem(who, oid, mine, refused) if answer == NO.SUCCESS else None
        if why:
            out.append(why)
        live = [s for s in unset if s in env]
        if live:
            out.append(f"{who} claims to reproduce the native outcome, but {', '.join(live)} "
                       "is set in the replay configuration, and the entry's answer holds "
                       "only while it is unset (native.requires_unset)")
    return out


def claim_problems_in(reasons: str | Path, outcomes: str | Path, reach: str | Path) -> list[str]:
    """`claim_problems` of the three files at exactly these paths, with every
    way the reach receipt's refusal record is wrong (replay_refusals). G4's M4
    criterion calls it in the clause gate's overlay, whose mutated files are
    the ones named here, so it reads no module-level root for them; the one
    path it resolves against the root is the import table the reach receipt
    names, under private/, which no overlay mutates."""
    try:
        doc = json.loads(Path(reasons).read_text())
        rec = json.loads(Path(outcomes).read_text())
        env = replay_environment(Path(reach))
        refused, bad = replay_refusals(Path(reach))
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as e:
        return [f"the reasons, the native outcomes or the replay configuration cannot be "
                f"read: {e}"]
    return bad + claim_problems(doc, rec, env, refused)


def native_problems(doc: dict, outcomes: Path, env: dict[str, str],
                    refused: Counts | str | None) -> list[str]:
    """Every way the entries' native blocks fail the ruling, as sentences."""
    try:
        rec = NO.load(outcomes)
    except SystemExit as e:
        return [f"the native-outcomes receipt cannot be read: {e}"]
    return ([f"native outcomes: {p}" for p in NO.check(outcomes)]
            + claim_problems(doc, rec, env, refused))


def problems(reasons_path: Path | None = None, outcomes: Path | None = None,
             sources: list[Path] | None = None, reach: Path | None = None,
             src: Path | None = None) -> list[str]:
    """Every way the record fails the rule, as a list of sentences. `src` is the
    runtime source whose import tables the audit reads (default the tree's)."""
    out: list[str] = []
    path = reasons_path or REASONS
    if not path.exists():
        return [f"the reasons file {path} does not exist"]

    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"the reasons file is not JSON: {e}"]
    if doc.get("schema") != SCHEMA:
        out.append(f"schema is {doc.get('schema')!r}, expected {SCHEMA!r}")

    seen: set[tuple[str, str]] = set()
    for r in doc.get("reasons", []):
        who = f"{r.get('dll', '?')}!{r.get('name', '?')}"
        k = key(str(r.get("dll", "")), str(r.get("name", "")))
        if k in seen:
            out.append(f"{who} has two entries; one of them is dead text")
        seen.add(k)
        for field in REQUIRED:
            if not str(r.get(field, "")).strip():
                out.append(f"{who} has no {field}")
        if r.get("kind") not in KINDS:
            out.append(f"{who}: kind {r.get('kind')!r} is not one of {', '.join(KINDS)}")
        why = str(r.get("why", "")).strip()
        low = why.lower()
        for phrase in NON_REASONS:
            if phrase in low:
                out.append(f"{who}: {phrase!r} is a queue entry, not a reason. "
                           "A reason says why the refusal exists.")
                break
        if why and len(why) < MIN_WHY:
            out.append(f"{who}: the reason is {len(why)} characters; a reason "
                       f"shorter than {MIN_WHY} is a label")
        fals = str(r.get("falsifier", "")).strip()
        if fals and len(fals) < MIN_FALSIFIER:
            out.append(f"{who}: the falsifier is {len(fals)} characters; name "
                       "the observation, not the topic")

    try:
        env = replay_environment(reach or REACH)
        refused, bad = replay_refusals(reach or REACH)
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as e:
        return out + [f"the replay configuration cannot be read from the reach receipt: {e}"]
    out += bad
    out += native_problems(doc, outcomes or OUTCOMES, env, refused)

    try:
        a = audit(path, sources, src)
    except SystemExit as e:
        return out + [f"the audit refused to run: {e}"]

    for row in a["refused"]:
        reason = row.get("reason")
        if reason and row["surface"] == "iat" and reason.get("class") != row["class"]:
            out.append(f"{row['dll']}!{row['name']}: the entry says class "
                       f"{reason.get('class')!r} and the census says {row['class']!r}")
        if row["class"] in GATED and not reason:
            out.append(f"{row['dll']}!{row['name']} ({row['class']}, "
                       f"{row['kind']}) refuses with no recorded reason. Design "
                       "3.5 says this class is a no-op that records its calls.")
    for row in a["markers_without_entry"]:
        m = row["markers"][0]
        out.append(f"{row['dll']}!{row['name']} is marked refused at {m['file']}:{m['line']} "
                   "and has no entry, so nothing records why it refuses or whether that "
                   "reproduces the native outcome")
    for s in a["policy_without_marker"]:
        out.append(f"{s} is a policy entry with no {MARKER} marker in runtime/*.c: either "
                   "the runtime no longer refuses it and the entry is stale, or the refusal "
                   "is unmarked")
    unmarked = set(a["policy_without_marker"])
    for s in a["stale_reasons"]:
        if s not in unmarked:
            out.append(f"{s} has a recorded reason but does not refuse; the reason "
                       "is stale and would excuse a future refusal nobody reviewed")
    return out


# How many falsifications ran, and which could not arise in the data, set by
# falsifications() from the cases it actually built. The count was a literal
# in the summary line, which is the same defect this project catalogues as "a
# counter incremented before the thing it counts": adding a case without
# touching the literal would have printed a number that was simply wrong, and
# a gate that misreports its own rigour is worse than one that does not
# report it.
RAN = 0
INAPPLICABLE: list[str] = []


def _marker_source(path: Path, marks: list[tuple[str, str]]) -> Path:
    body = [f"uint32_t {MARKER}(const char *dll, const char *name, uint32_t code);"]
    body += [f'static uint32_t refuse_{i}(void) {{ return {MARKER}("{d}", "{n}", 0x80004005u); }}'
             for i, (d, n) in enumerate(marks)]
    path.write_text("\n".join(body) + "\n")
    return path


def caught(got: list[str], base: list[str], expect: tuple[str, ...]) -> bool:
    """Whether a mutation's problems include one the unmutated record does not
    already have, carrying one of the phrases its case targets."""
    before = set(base)
    return any(e in p for p in got if p not in before for e in expect)


def falsifications(src: Path | None = None, sources: list[Path] | None = None) -> list[str]:
    """Mutate the record and require the gate to fail, for its reason, on each.
    `src` and `sources` are the runtime the gate is held to, as in `problems`."""
    global RAN, INAPPLICABLE
    RAN, INAPPLICABLE = 0, []
    doc = json.loads(REASONS.read_text())
    entries = doc.get("reasons", [])
    if not entries:
        return ["the reasons file is empty, so no mutation of it can be shown "
                "to fail; the falsifications cannot run"]
    try:
        live = audit(None, sources, src)
    except SystemExit as e:
        return [f"the audit refused to run, so no falsification can be built: {e}"]
    env = replay_environment()
    outcomes = json.loads(OUTCOMES.read_text()) if OUTCOMES.is_file() else {}
    answers = {o.get("id"): o.get("answer") for o in outcomes.get("outcomes", [])}
    keys = {key(str(r.get("dll", "")), str(r.get("name", ""))) for r in entries}
    order = sorted(range(len(entries)),
                   key=lambda i: (str(entries[i].get("dll")), str(entries[i].get("name"))))
    first = order[0]
    missing: list[str] = []

    def find(what: str, found):
        if found is None:
            missing.append(what)
        return found

    def optional(what: str, found):
        if found is None:
            INAPPLICABLE.append(what)
        return found

    def nat(i: int) -> dict:
        return entries[i].get("native") or {}

    # Every target below is found in the data. "A reason deleted" needs an entry
    # whose refusal the audit derives without it: a no-stub or null-impl entry
    # always, and a policy entry only once markers exist (see the docstring).
    derivable = find("entry whose refusal is derived without it", next(
        (i for i in order if entries[i].get("kind") in ("no-stub", "null-impl")
         or (live["marker_checks"] and entries[i].get("kind") == "policy")), None))
    refused_iat = {key(r["dll"], r["name"]) for r in live["refused"]
                   if r["surface"] == "iat" and r.get("reason")}
    iat_entry = find("entry for a refused import slot", next(
        (i for i in order if key(str(entries[i].get("dll", "")), str(entries[i].get("name", "")))
         in refused_iat), None))
    nostub = optional("nostub (no import slot lacks a stub, so no policy entry can name one)",
                      next((r for r in live["refused"] if r["kind"] == "no-stub"), None))
    answered = find("import slot with a stub that does not refuse and has no entry", next(
        (r for r in live["rows"] if r["surface"] == "iat" and not r["refused"]
         and key(r["dll"], r["name"]) not in keys), None))
    cited = find("entry whose native outcome has evidence", next(
        (i for i in order if any(o.get("id") == nat(i).get("outcome") and o.get("evidence")
                                 for o in outcomes.get("outcomes", []))), None))
    # The class rule, built from the data: an entry whose outcome answers
    # success, preferring one that does not reproduce, claims to with no switch.
    succeeding = [i for i in order if answers.get(nat(i).get("outcome")) == NO.SUCCESS]
    success_entry = optional(
        "success-claim (no entry names an outcome that answers success)",
        next((i for i in succeeding if nat(i).get("reproduces") is not True),
             succeeding[0] if succeeding else None))
    # And the receipt side of it: an entry that reproduces a failure with no
    # switch, whose outcome is flipped to success in the receipt.
    failure_entry = optional(
        "failure-flip (no entry reproduces a failure outcome without a switch)",
        next((i for i in order if nat(i).get("reproduces") is True and not nat(i).get("switches")
              and answers.get(nat(i).get("outcome")) == NO.FAILURE), None))
    # An SA2_* name, since requires_unset takes nothing else: the first sorted
    # key could be DXVK_HUD, whose claim the gate refuses for its shape.
    set_var = find("variable the replay configuration sets",
                   next((k for k in sorted(env) if SWITCH.match(k)), None))
    if env.get(OFF_SWITCH) == "1":
        missing.append(f"switch name that the replay configuration leaves off ({OFF_SWITCH} "
                       "is on)")
    policy = [(str(r["dll"]), str(r["name"])) for r in entries if r.get("kind") == "policy"]
    find("policy entry", policy or None)

    def mutate(d: dict, i: int | None, f) -> dict:
        d = copy.deepcopy(d)
        f(d, i)
        return d

    # (name, reasons, outcomes receipt, markers, reach receipt, expected phrases);
    # None leaves that part of the record live.
    cases: list[tuple[str, dict | None, dict | None, list | None, dict | None,
                      tuple[str, ...]]] = []

    def case(name, expect, reasons=None, receipt=None, marks=None, reach=None):
        cases.append((name, reasons, receipt, marks, reach, expect))

    def claim(d: dict, i: int, **native) -> None:
        d["reasons"][i]["native"] = {**(d["reasons"][i].get("native") or {}), **native}

    if derivable is not None:
        case("a reason deleted", ("no recorded reason", "no reasons-file entry", "has no entry"),
             reasons=mutate(doc, derivable, lambda d, i: d["reasons"].pop(i)))
    case("a reason emptied", ("has no why",),
         reasons=mutate(doc, first, lambda d, i: d["reasons"][i].__setitem__("why", "")))
    case("a reason replaced by a queue entry", ("is a queue entry",),
         reasons=mutate(doc, first, lambda d, i: d["reasons"][i].__setitem__(
             "why", "Not implemented yet; it is on the S5 work list and will be picked up "
                    "when the renderer lands.")))
    case("a falsifier deleted", ("has no falsifier",),
         reasons=mutate(doc, first, lambda d, i: d["reasons"][i].__setitem__("falsifier", "")))
    if answered is not None:
        case(f"a no-stub reason for {answered['dll']}!{answered['name']}, which has a stub "
             "and does not refuse", ("does not refuse",),
             reasons=mutate(doc, first, lambda d, i: d["reasons"].append(
                 {**d["reasons"][i], "dll": answered["dll"], "name": answered["name"],
                  "kind": "no-stub"})))
    if nostub is not None:
        case(f"a policy refusal declared for {nostub['dll']}!{nostub['name']}, which has no "
             "stub at all", ("has no stub at all",),
             reasons=mutate(doc, first, lambda d, i: d["reasons"].append(
                 {**d["reasons"][i], "dll": nostub["dll"], "name": nostub["name"],
                  "kind": "policy"})))
    case("the schema changed", ("schema is",),
         reasons=mutate(doc, None, lambda d, i: d.__setitem__("schema", "something-else/v1")))
    case("a duplicate entry", ("has two entries",),
         reasons=mutate(doc, first, lambda d, i: d["reasons"].append(
             copy.deepcopy(d["reasons"][i]))))
    case("a kind outside the three", ("is not one of",),
         reasons=mutate(doc, first, lambda d, i: d["reasons"][i].__setitem__("kind", "declared")))
    if iat_entry is not None:
        case("an entry's class that is not the census's", ("the census says",),
             reasons=mutate(doc, iat_entry, lambda d, i: d["reasons"][i].__setitem__(
                 "class", "steam" if d["reasons"][i]["class"] != "steam" else "presentation")))
    case("a native block deleted", ("has no native block",),
         reasons=mutate(doc, first, lambda d, i: d["reasons"][i].pop("native", None)))
    case("an outcome id that does not exist", ("does not have",),
         reasons=mutate(doc, first, lambda d, i: claim(d, i, outcome="no-such-outcome")))
    case("reproduces set to a non-boolean", ("not true or false",),
         reasons=mutate(doc, first, lambda d, i: claim(d, i, reproduces="true")))
    case("a claim to reproduce with no receipt outcome", ("names no native outcome",),
         reasons=mutate(doc, first, lambda d, i: claim(d, i, outcome=None, reproduces=True)))
    # Synthetic, like the switch-off case below: the work list ends when no
    # no-stub or null-impl entry is left (the DirectDraw push removes the two
    # there are), so the first entry is declared a trap instead of one being
    # found.
    case("an entry declared a trap (null-impl) that claims to reproduce",
         ("a trap reproduces no outcome",),
         reasons=mutate(doc, first, lambda d, i: (
             d["reasons"][i].__setitem__("kind", "null-impl"), claim(d, i, reproduces=True))))
    case(f"a claim to reproduce whose switch ({OFF_SWITCH}) is off in the replay "
         "configuration", ("is not on in the replay configuration",),
         reasons=mutate(doc, first, lambda d, i: claim(d, i, reproduces=True,
                                                       switches=[OFF_SWITCH])))
    case("switches that are not a list of switch names", ("native.switches is",),
         reasons=mutate(doc, first, lambda d, i: claim(d, i, switches="SA2_X")))
    case("requires_unset that is not a list of names", ("native.requires_unset is",),
         reasons=mutate(doc, first, lambda d, i: claim(d, i, requires_unset="SA2_X")))
    if set_var is not None:
        case(f"a claim to reproduce that requires {set_var} unset, which the replay "
             "configuration sets", ("is set in the replay configuration",),
             reasons=mutate(doc, first, lambda d, i: claim(
                 d, i, reproduces=True, switches=[], requires_unset=[set_var])))
    # The refusal-count cases run on import tables written here, each with the
    # counts reach_receipt.refusals makes of it: the live table is under
    # private/ and may be absent or mid-run, and a case that needs it would
    # then test the absence instead of its own rule.
    reach_doc = json.loads(REACH.read_text())
    tables = tempfile.TemporaryDirectory()

    def row(dll: str, name: str, calls: int, refused: int) -> dict:
        return {"dll": dll, "name": name, "calls": calls, "refused_calls": refused}

    stand_in = row("NOT_AN_IMPORT.dll", "Nothing", 1, 0)

    def reach_on(rows: list[dict], edit=None) -> dict:
        p = Path(tables.name) / f"t{len(list(Path(tables.name).iterdir()))}.imports.table.json"
        p.write_text(json.dumps({"rows": rows}))
        d = copy.deepcopy(reach_doc)
        t = d.setdefault("reach", {}).setdefault("import_table", {})
        t.update(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                 refused=RR.refusals(p))
        if edit is not None:
            edit(t)
        return d

    # Whatever the success rules find, the record of the counts is held to its
    # own shape: a count that is not one and a key that names no import are
    # reported, not dropped or raised.
    case("a refusal count that is not a count (null)",
         ("with whole non-negative counts",),
         reach=reach_on([stand_in], lambda t: t["refused"].__setitem__(
             "NOT_AN_IMPORT.dll!Nothing", {"calls": None, "refused": 0})))
    case("a refusal record keyed by a name with no DLL", ("which names no DLL!import",),
         reach=reach_on([stand_in], lambda t: t["refused"].__setitem__(
             "Nothing", {"calls": 1, "refused": 0})))
    if success_entry is not None:
        e = entries[success_entry]
        soid = nat(success_entry).get("outcome")
        case(f"{e['dll']}!{e['name']} claiming to reproduce outcome "
             f"{soid}, which answers success, with its switches "
             "deleted", ("and names no switch",),
             reasons=mutate(doc, success_entry, lambda d, i: (
                 claim(d, i, reproduces=True), d["reasons"][i]["native"].pop("switches", None))))
        # The same claim with its switches kept, against reach runs that do
        # not show the import answered: the switch names alone must not carry
        # it (the gate fold's verification, N1).
        claimed = mutate(doc, success_entry, lambda d, i: claim(d, i, reproduces=True))
        who_key = f"{e['dll']}!{e['name']}"
        case(f"{who_key} claiming to reproduce outcome {soid}, "
             "which answers success, while the reach run's runtime refused it",
             ("a switch that is on is not a device that answered",), reasons=claimed,
             reach=reach_on([row(e["dll"], e["name"], 1, 1)]))
        case(f"{who_key} claiming to reproduce outcome {soid} while the reach run's table has "
             "no row for it (a dynamic import, or none)", ("has no row for it",),
             reasons=claimed, reach=reach_on([stand_in]))
        case(f"{who_key} claiming to reproduce outcome {soid} while the reach run never "
             "called it", ("never called it",),
             reasons=claimed, reach=reach_on([row(e["dll"], e["name"], 0, 0)]))
        case(f"{who_key} claiming to reproduce outcome {soid} while the reach receipt "
             "records no refusal counts", ("records no refusal counts",),
             reasons=claimed, reach=reach_on([row(e["dll"], e["name"], 1, 0)],
                                             lambda t: t.pop("refused")))
        # The receipt's counts are recomputed from its table, not trusted: an
        # emptied record beside a table that refused the claimed import.
        case(f"the reach receipt's refusal counts emptied while its import table records a "
             f"refusal of {who_key}, which claims to reproduce outcome {soid}",
             ("is not what tools/s4/reach_receipt.py refusals() makes",), reasons=claimed,
             reach=reach_on([row(e["dll"], e["name"], 1, 1)],
                            lambda t: t.__setitem__("refused", {})))
        case(f"{who_key} claiming to reproduce outcome {soid} while the import table the "
             "reach receipt names is not on disk", ("is not on disk",), reasons=claimed,
             reach=reach_on([row(e["dll"], e["name"], 1, 0)], lambda t: t.__setitem__(
                 "path", str(Path(tables.name) / "absent.imports.table.json"))))
        case(f"{who_key} claiming to reproduce outcome {soid} while the import table the "
             "reach receipt names is not the one it hashed", ("is not the table the counts",),
             reasons=claimed, reach=reach_on([row(e["dll"], e["name"], 1, 0)],
                                             lambda t: t.__setitem__("sha256", "0" * 64)))
    if failure_entry is not None:
        foid = nat(failure_entry).get("outcome")

        def to_success(d, i):
            next(o for o in d["outcomes"] if o["id"] == foid)["answer"] = NO.SUCCESS

        case(f"outcome {foid} answering success in the receipt, while "
             f"{entries[failure_entry]['dll']}!{entries[failure_entry]['name']} reproduces it "
             "with no switch", ("and names no switch",),
             receipt=mutate(outcomes, None, to_success))
    if cited is not None:
        oid = nat(cited)["outcome"]

        def edit_line(d, i):
            o = next(o for o in d["outcomes"] if o["id"] == oid)
            o["evidence"][0]["text"] += " (edited)"

        def drop_import(d, i):
            o = next(o for o in d["outcomes"] if o["id"] == oid)
            mine = key(str(entries[cited]["dll"]), str(entries[cited]["name"]))
            o["imports"] = [x for x in o["imports"] if key(*x.split("!", 1)) != mine]

        def bad_answer(d, i):
            next(o for o in d["outcomes"] if o["id"] == oid)["answer"] = "maybe"

        case(f"an evidence line of outcome {oid} whose text no longer matches its log",
             ("not the cited",), receipt=mutate(outcomes, None, edit_line))
        case(f"outcome {oid} no longer naming the import that cites it", ("is about",),
             receipt=mutate(outcomes, None, drop_import))
        case(f"outcome {oid} with an answer class outside the two", ("answer class is",),
             receipt=mutate(outcomes, None, bad_answer))
    if policy and answered is not None:
        case(f"a marker for {answered['dll']}!{answered['name']}, which has no entry",
             ("is marked refused",), marks=policy + [(answered["dll"], answered["name"])])
        case(f"a policy entry for {policy[0][0]}!{policy[0][1]} with no marker",
             ("with no " + MARKER + " marker",), marks=policy[1:] + [(answered["dll"],
                                                                      answered["name"])])

    RAN = len(cases)

    survived = []
    with tables, tempfile.TemporaryDirectory() as td:
        # The unmutated record's own problems, once for the runtime's markers and
        # once for markers on exactly the policy entries, which is the record the
        # marker cases mutate.
        base_live = problems(None, None, sources, None, src)
        base_marked = (problems(None, None, [_marker_source(Path(td) / "policy.c", policy)],
                                None, src) if policy else [])
        for n, (name, reasons, receipt, marks, reach, expect) in enumerate(cases):
            rp = rc = rr = None
            if reasons is not None:
                rp = Path(td) / f"reasons-{n}.json"
                rp.write_text(json.dumps(reasons))
            if receipt is not None:
                rc = Path(td) / f"outcomes-{n}.json"
                rc.write_text(NO.render(receipt))
            if reach is not None:
                rr = Path(td) / f"reach-{n}.json"
                rr.write_text(json.dumps(reach))
            srcs = sources if marks is None else [_marker_source(Path(td) / f"markers-{n}.c",
                                                                 marks)]
            got = problems(rp, rc, srcs, rr, src)
            base = base_live if marks is None else base_marked
            if not caught(got, base, expect):
                new = [p for p in got if p not in set(base)]
                survived.append(f"{name} (it added {len(new)} problem(s) the unmutated record "
                                f"does not have, first: {new[0][:120] if new else 'none'})")
    return ([f"a falsification could not be built: the data has no {m}" for m in missing]
            + [f"falsification survived: {s} -- the gate passed a record it should have "
               "rejected for that reason, so it is not checking what it claims"
               for s in survived])


def main() -> int:
    failed = falsifications()
    probs = problems()
    try:
        a = audit()
    except SystemExit:
        a = None
    regime = ("regime unknown: the audit refused to run" if a is None else
              f"markers={a['counts']['markers']} marker_checks=on" if a["marker_checks"] else
              f"markers=0 marker_checks=off(no {MARKER} call in runtime/*.c; policy "
              "refusals are declared, not derived)")
    inapplicable = (f"inapplicable={len(INAPPLICABLE)}({'; '.join(INAPPLICABLE)})"
                    if INAPPLICABLE else "inapplicable=0")
    if failed or probs:
        for f in failed:
            print(f"  {f}")
        for p in probs:
            if p not in failed:
                print(f"  {p}")
        gated = "?" if a is None else len([r for r in a["refused"] if r["class"] in GATED])
        print(f"REFUSED_PRESENTATION_FAILED falsifications={len(failed)} problems={len(probs)} "
              f"gated_refusals={gated} {regime}")
        return 1

    gated = [r for r in a["refused"] if r["class"] in GATED]
    entries = json.loads(REASONS.read_text()).get("reasons", [])
    rep = sum(1 for r in entries if (r.get("native") or {}).get("reproduces") is True)
    nonrep = sum(1 for r in entries if (r.get("native") or {}).get("reproduces") is False)
    no_receipt_outcome = sum(1 for r in entries if isinstance(r.get("native"), dict)
                             and r["native"].get("outcome") is None)
    excused = [r for r in gated if r.get("reason")]
    print(f"REFUSED_PRESENTATION_OK classes={'+'.join(GATED)} "
          f"census={a['counts']['census']} refused={a['counts']['refused']} "
          f"gated_refusals={len(gated)} excused={len(excused)} unexcused=0 "
          f"entries={len(entries)} reproducing={rep} non_reproducing={nonrep} "
          f"no_receipt_outcome={no_receipt_outcome} {regime} falsifications={RAN} {inapplicable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
