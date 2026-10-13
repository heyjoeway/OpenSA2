"""Read commits from a git repository and turn them into agent wall-hours.

This is the one place the wall-hour rule from `Documentation/stages.json`
is implemented. `metrics.py` (budgets, rate), `gate_report.py` and
`check_durable_records_fresh.py` (the slice rule, design section 4.4) all
import it, so a change here moves every number derived from the git log at
once, and the rule is never re-typed in three places with three drifts.

Public API (kept stable on purpose; other scripts import exactly this):

    commits(repo: Path, rev_range: str = "HEAD") -> list[Commit]
        Every commit reachable in `rev_range` (any `git log` revision
        expression, such as "HEAD", "abc123..HEAD" or "main"), oldest
        first by author date. Each `Commit` has `hash` (full sha1),
        `author_date` (a timezone-aware `datetime`) and `subject`.

    wall_hours(commits: Sequence[Commit]) -> float
        The wall-hour rule: commits are taken in time order; the first
        contributes 0.25 h; every later commit contributes the gap to the
        previous commit when that gap is at most 2.0 h, otherwise 0.25 h
        (the first commit of a new session). An empty sequence is 0.0.

    Stage boundaries (applied by metrics.budget_info, stated here because
    it is part of the rule): a stage with its own start_commit is charged
    from that commit inclusive. A stage whose start is derived from the
    previous gate's commit is charged as wall_hours over the range that
    includes the gate commit, minus SESSION_START_HOURS for the gate
    commit itself, so the gap between the gate and the stage's first
    commit is charged to the new stage (a gap over SESSION_GAP_HOURS
    still collapses to SESSION_START_HOURS). Budgets are agent
    wall-hours: the main thread's commit spacing plus the summed
    parallel units below.

Author date is used rather than commit date because a rebase rewrites the
commit date but keeps the author date, and the rule measures when the work
happened, not when history was tidied. Commits are sorted by author date
for the same reason: `git log` orders by commit date or topology, and a
rebased range can put an older piece of work after a newer one.

    session_gaps(commits: Sequence[Commit]) -> list[tuple[Commit, Commit, float]]
        Every pair of consecutive commits, in time order, whose gap exceeds
        SESSION_GAP_HOURS, with the gap in hours: the gaps `wall_hours`
        charges SESSION_START_HOURS each, whatever they spanned. The rule
        cannot tell an idle gap from a working one, so the user asked on
        2026-09-24 that every gate review report how many there are, the
        wall-hours they span and what they were charged, with the method
        left as it is (`Documentation/decisions/2026-09-24-S4-110h-review.md`);
        `gate_report.py` and `tools/s4/estimate_actuals.py` print it from
        here. Nothing in it changes `wall_hours`.

    load_units(root: Path) -> list[dict]
        The parallel-unit ledger `Documentation/agent-units.json` written by
        `tools/units.py` (empty list when absent). Each unit has `stage`,
        `started`, `ended` (ISO 8601) and `hours`.

    unit_hours(units, stage: str) -> tuple[float, int]
        Sum of the recorded units' hours for `stage`, and their count. The
        budget adds this to the main thread's commit-spacing hours: units
        are summed, not maximised, because budgets are in agent wall-hours
        (design 4.1) and three units of ten minutes are thirty agent-minutes.
        Required by the user at G0 before the first S1 commit; the method
        chosen (sum of unit durations from the harness transcripts, not a
        concurrency multiplier on main-thread spacing) is recorded in
        `Documentation/stages.json` and `tools/units.py`. A unit's `hours`
        is its first-to-last window (the G0 method), and this sum is that
        reading.

    unit_active_hours(units, stage: str) -> tuple[float, int]
    unit_gaps(units, stage: str) -> dict
        Units split at the idle gaps (the user's ruling of 2026-10-05,
        `Documentation/decisions/2026-10-05-S5-user-rulings-3.md`: "split
        units at idle gaps, with the same stated threshold as the main
        thread; report both readings"). `tools/units.py` writes each unit's
        `windows` and `gaps` with the main thread's own rule
        (`tools/sessions.py` windows_and_gaps over the unit's records inside
        its recorded window), in the session ledger's shapes, and the unit
        ledger states the threshold as `idle_gap_hours`. `unit_active_hours`
        is the active reading: each unit's first-to-last hours less its split
        gaps, which is its windows' length to the ledger's rounding, never more
        than the first-to-last reading and less by exactly the split gaps; a
        unit with no windows is charged its hours (a stage counted by commit
        spacing; the unit audit fails an unsplit unit of a stage counted at
        active time). `unit_gaps` counts the gaps longer than the threshold inside a
        stage's units, bridged and split, as `transcript_gaps` does for
        sessions. `load_units` checks the windows a unit carries as
        `check_sessions` checks a session's, and that they run from the
        unit's first record to its last.

The transcript count (the user's ruling of 2026-09-29,
`Documentation/decisions/2026-09-29-S5-budget-400.md`): from S5 on, the main
thread is counted from its own session transcripts, the way units are, so a
long gap between commits is no longer charged as if the main thread had
worked through it or had not worked at all. The stages it applies to carry
`"main_thread": "transcripts"` in stages.json (metrics.TRANSCRIPTS_FROM pins
the first); every other stage keeps the commit spacing above, unchanged. Its
parts live here, beside the rule they replace, so the session gap is this
module's constant and not a second typed copy.

What it charges is active time (the user's ruling of 2026-10-04,
`Documentation/decisions/2026-10-04-S5-user-rulings.md`: "split sessions at
idle gaps, with the threshold stated, and report both readings at every
review"): each session's windows, split at every gap longer than
SESSION_GAP_HOURS that the session's own work did not end. The threshold is
this constant; `Documentation/stages.json` states it as `idle_gap_hours`
(IDLE_GAP_FIELD), which metrics.load_stages holds equal to it, and the
session ledger records it under the same name, which `load_ledger` holds
equal to it, so a change to the constant cannot move a figure without both
files saying so. The first-to-last reading, each session from its first
counted record to its last, idle gaps included, is reported beside it
(`first_to_last_windows`); until that ruling it was the binding count.

    instant(value: str) -> datetime
        An ISO 8601 stamp ("Z" accepted) as an aware datetime; a stamp with
        no offset is refused. Transcript stamps, ledger windows and commit
        dates are all read through it.

    long_gaps(stamps) -> list[tuple[datetime, datetime]]
        Every gap longer than SESSION_GAP_HOURS between consecutive stamps,
        in time order, as (last stamp before it, first stamp after it).

    active_windows(stamps, bridged=()) -> list[tuple[datetime, datetime]]
        One session's counted timestamps as windows, each running from its
        first stamp to its last (a unit is counted first to last the same
        way). A gap longer than SESSION_GAP_HOURS ends a window unless the
        stamp that ends the gap is in `bridged`: `tools/sessions.py` puts it
        there when the session's own work ended the wait (a task it launched
        reporting back, a message from one of its agents, its own scheduled
        prompt), because a unit waiting on its own work is charged the wait
        and so is the main thread (the ruling: "so long gaps are no longer
        charged as idle"). A gap a person ended, or nothing the session
        launched, counts nothing: no session ran, or the machine was off, or
        the session waited on the user.

    load_ledger(root: Path, rev: str | None = None, required: bool = False) -> dict
    load_sessions(root, rev=None, required=False) -> list[dict]
        The main-session ledger `Documentation/agent-sessions.json` written
        by `tools/sessions.py`, from the working tree or, with `rev`, as
        committed at that revision (metrics reads a closed stage's ledger at
        its gate commit, so a later recording cannot move a figure a gate
        already quoted). `load_ledger` returns {"sessions": [...],
        "recorded_at": datetime | None}; `load_sessions` the list alone.
        `recorded_at` is the instant `tools/sessions.py record` read the
        transcripts at: the ledger holds every main session's time up to it
        and nothing after it. Absent: an empty ledger, or a ValueError when
        `required`. Each session has `id`, `source`, `windows` ([start, end]
        ISO 8601 pairs in time order) and `gaps` (every gap longer than the
        session gap: [start, end, what ended it, "bridged" | "split"]). A
        malformed ledger is a ValueError naming what is wrong
        (`check_sessions`, which `tools/sessions.py audit` runs too).

    windows(session) -> list[tuple[datetime, datetime]]
    clip(windows, start, end) / subtract(windows, covered) / span(windows)
        A session's windows as instants, the parts inside [start, end), the
        parts no window of `covered` holds, and their total length as a
        timedelta (exact to the microsecond). metrics.counted_windows charges
        a stage with them, each session's windows clipped to the stage and
        the sessions summed, concurrent ones included, for the reason units
        are summed: two sessions running at once are two agents' hours; and
        tools/sessions.py audits the ledger with them.

    first_to_last_windows(windows) -> list[tuple[datetime, datetime]]
        The first-to-last reading of one session: a single window from its
        first window's start to its last window's end, the split gaps
        between them included. Reported beside the binding windows at every
        review (metrics.counted_windows(first_to_last=True)); never the
        budget's verdict.

    check_idle_gap(document) -> None
        ValueError when a session ledger states an `idle_gap_hours` other
        than SESSION_GAP_HOURS. A ledger without the field reads as recorded
        at LEGACY_IDLE_GAP_HOURS, 2.0, the literal every such ledger was split
        at: the constant has been 2.0 since it was introduced (development_snapshot),
        before the first ledger (development_snapshot). It is a literal and not the
        constant so that a later change of the constant fails those ledgers
        rather than reading them as split at the new threshold.

    transcript_gaps(sessions, start, end) -> dict
        The gaps longer than the session gap inside [start, end): how many
        were bridged (counted) and split (not counted), and the hours of
        each, so every review shows what the split left out.

    recorded_to(sessions) -> datetime | None
        The newest window end in the ledger.

Where the harness keeps the transcripts, and which are units and which the
main thread's, is `tools/harness.py`, which both recorders import.

Also here, because every gate needs it and it belongs beside the git calls:

    project_root(explicit: Path | None = None) -> Path
        `explicit` resolved when given, else `git rev-parse --show-toplevel`
        from the current working directory. The gates locate the project
        from cwd rather than from `__file__` so the tests can run the real
        scripts against throwaway repositories under tmp_path.

No CLI. `python -c "from gitlog import commits, wall_hours; ..."` from
`Documentation/scripts` is enough for a one-off query.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Collection, Sequence

# From Documentation/stages.json, "wall_hour_rule". The session gap is also the
# transcript count's idle-gap threshold, stated in stages.json and in the session
# ledger under IDLE_GAP_FIELD (the module docstring, "The transcript count").
SESSION_GAP_HOURS = 2.0
SESSION_START_HOURS = 0.25
IDLE_GAP_FIELD = "idle_gap_hours"
# The threshold every session ledger without IDLE_GAP_FIELD was split at (check_idle_gap).
LEGACY_IDLE_GAP_HOURS = 2.0


@dataclass(frozen=True)
class Commit:
    hash: str
    author_date: datetime
    subject: str


def project_root(explicit: Path | None = None) -> Path:
    """The repository to check: `explicit`, else the git toplevel of cwd."""
    if explicit is not None:
        root = explicit.resolve()
        if not (root / ".git").exists():
            raise SystemExit(f"not a git repository: {root}")
        return root
    result = subprocess.run(("git", "rev-parse", "--show-toplevel"),
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit("not inside a git repository (pass --root PATH): "
                         + result.stderr.strip())
    return Path(result.stdout.strip()).resolve()


def commits(repo: Path, rev_range: str = "HEAD") -> list[Commit]:
    """Commits in `rev_range`, oldest first by author date."""
    result = subprocess.run(
        ("git", "-C", str(repo), "log", "--format=%H%x00%aI%x00%s", rev_range),
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git log {rev_range} in {repo} failed: "
                           f"{result.stderr.strip()}")
    parsed = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        sha, date, subject = line.split("\0", 2)
        stamp = datetime.fromisoformat(date)
        if stamp.tzinfo is None:
            raise RuntimeError(f"commit {sha[:12]} has a naive author date {date!r}")
        parsed.append(Commit(sha, stamp, subject))
    # git prints newest first; stable sort keeps git's order for equal stamps.
    parsed.reverse()
    parsed.sort(key=lambda commit: commit.author_date)
    return parsed


def wall_hours(commits: Sequence[Commit]) -> float:
    """Sum of wall-hours over `commits` per the rule in the module docstring."""
    ordered = sorted(commits, key=lambda commit: commit.author_date)
    total = 0.0
    previous: Commit | None = None
    for commit in ordered:
        if previous is None:
            total += SESSION_START_HOURS
        else:
            gap = (commit.author_date - previous.author_date).total_seconds() / 3600.0
            total += gap if 0.0 <= gap <= SESSION_GAP_HOURS else SESSION_START_HOURS
        previous = commit
    return total


def session_gaps(commits: Sequence[Commit]) -> list[tuple[Commit, Commit, float]]:
    """The gaps `wall_hours` charges as new sessions: (earlier, later, hours) for every
    pair of consecutive commits, in time order, more than SESSION_GAP_HOURS apart."""
    ordered = sorted(commits, key=lambda commit: commit.author_date)
    out = []
    for previous, commit in zip(ordered, ordered[1:]):
        gap = (commit.author_date - previous.author_date).total_seconds() / 3600.0
        if gap > SESSION_GAP_HOURS:
            out.append((previous, commit, gap))
    return out


UNITS_FILE = "Documentation/agent-units.json"


def load_units(root: Path) -> list[dict]:
    """The parallel-unit ledger written by tools/units.py; [] when absent. A unit that
    carries windows is checked as a session's are (`check_units`)."""
    import json

    path = root / UNITS_FILE
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    units = data.get("units")
    if not isinstance(units, list):
        raise ValueError(f"{UNITS_FILE}: 'units' must be a list")
    for unit in units:
        for field in ("id", "stage", "started", "ended", "hours"):
            if field not in unit:
                raise ValueError(f"{UNITS_FILE}: unit missing {field!r}: {unit}")
    check_units(data)
    return units


def check_units(document: dict) -> None:
    """ValueError on the first unit whose windows are malformed (the module docstring,
    `unit_windows`): its windows and gaps as `check_sessions` holds a session's, its
    windows running from its first record (`started`) to its last (`ended`), and, once
    any unit carries windows, the ledger stating the threshold they were split at."""
    units = document.get("units") or []
    split = [unit for unit in units if "windows" in unit or "gaps" in unit]
    if split:
        check_idle_gap(document, where=UNITS_FILE, required=True)
    for unit in split:
        name = f"{unit['id']} from {unit['started']}"
        for field in ("windows", "gaps"):
            if field not in unit:
                raise ValueError(f"{UNITS_FILE}: {name}: carries "
                                 f"{'gaps' if field == 'windows' else 'windows'} but no "
                                 f"{field!r}")
        spans = check_split(unit, name, UNITS_FILE)
        try:
            first, last = instant(str(unit["started"])), instant(str(unit["ended"]))
        except ValueError as error:
            raise ValueError(f"{UNITS_FILE}: {name}: {error}") from None
        if not spans or spans[0][0] != first or spans[-1][1] != last:
            raise ValueError(f"{UNITS_FILE}: {name}: its windows do not run from its first "
                             "record to its last (started .. ended), so the two readings "
                             "would not be of one unit")


def unit_active_hours(units: Sequence[dict], stage: str) -> tuple[float, int]:
    """(summed hours, count) of the recorded units attributed to `stage` on the active
    reading: each unit's `hours` (first to last, as the ledger rounds it) less its split
    gaps. `check_units` holds a unit's windows to run from its first record to its last
    with its split gaps the spaces between them, so this is the windows' length to that
    rounding, and the first-to-last sum less this is exactly the split gaps' hours. A unit
    with no windows is charged its hours (the module docstring, `unit_active_hours`)."""
    chosen = [unit for unit in units if unit["stage"] == stage]
    split = sum(span([(instant(low), instant(high))]).total_seconds()
                for unit in chosen for low, high, _closer, kind in unit.get("gaps") or []
                if kind == GAP_SPLIT)
    return sum(float(unit["hours"]) for unit in chosen) - split / 3600.0, len(chosen)


def unit_gaps(units: Sequence[dict], stage: str) -> dict[str, float | int]:
    """The gaps longer than the threshold inside `stage`'s units, as `transcript_gaps`
    counts a session's: {"bridged": n, "bridged_hours": h, "split": n, "split_hours": h}.
    Units are charged by their stage label, not clipped to the stage's instants."""
    return transcript_gaps([{"gaps": unit.get("gaps") or []} for unit in units
                            if unit["stage"] == stage], None, None)


def unit_hours(units: Sequence[dict], stage: str) -> tuple[float, int]:
    """(summed hours, count) of the recorded units attributed to `stage`."""
    chosen = [unit for unit in units if unit["stage"] == stage]
    return sum(float(unit["hours"]) for unit in chosen), len(chosen)


SESSIONS_FILE = "Documentation/agent-sessions.json"
# What a gap longer than the session gap became: kept inside its window (the
# session's own work ended it) or the end of one window and the start of the next.
GAP_BRIDGED = "bridged"
GAP_SPLIT = "split"


def long_gaps(stamps: Sequence[datetime]) -> list[tuple[datetime, datetime]]:
    """(last stamp before, first stamp after) for every gap longer than
    SESSION_GAP_HOURS between consecutive stamps, in time order. A gap of exactly
    the session gap is not one, as `wall_hours` charges such a gap in full."""
    gap = timedelta(hours=SESSION_GAP_HOURS)
    ordered = sorted(stamps)
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if b - a > gap]


def active_windows(stamps: Sequence[datetime],
                   bridged: Collection[datetime] = ()) -> list[tuple[datetime, datetime]]:
    """One session's counted stamps as (first, last) windows.

    A gap longer than SESSION_GAP_HOURS ends the window unless the stamp that ends
    the gap is in `bridged` (the module docstring). A gap of exactly the session
    gap stays inside its window; compared as a timedelta so the boundary is exact to
    the microsecond the stamps carry.
    """
    gap = timedelta(hours=SESSION_GAP_HOURS)
    windows: list[tuple[datetime, datetime]] = []
    for stamp in sorted(stamps):
        if windows and (stamp - windows[-1][1] <= gap or stamp in bridged):
            windows[-1] = (windows[-1][0], stamp)
        else:
            windows.append((stamp, stamp))
    return windows


def instant(value: str) -> datetime:
    """An ISO 8601 stamp as a timezone-aware datetime ("Z" accepted); a stamp with
    no offset is refused, because the ledger's windows and the stages' commit
    instants are compared across machines and a naive time has no fixed meaning."""
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"{value!r} carries no UTC offset")
    return stamp


def load_ledger(root: Path, rev: str | None = None, required: bool = False) -> dict:
    """The main-session ledger written by tools/sessions.py, the working tree's or as
    committed at `rev`, as {"sessions": [...], "recorded_at": datetime | None}.
    Absent: no sessions unless `required`, when it is a ValueError, so an open stage
    counted from transcripts cannot read a missing ledger as no time.

    Checked as it is read (`check_sessions`; `check_idle_gap`, the threshold it was
    recorded at; and the recording point parses as an instant with an offset), so a
    hand-edited ledger fails here rather than being summed.
    """
    import json

    if rev is None:
        path = root / SESSIONS_FILE
        text = path.read_text() if path.is_file() else None
    else:
        shown = subprocess.run(("git", "-C", str(root), "show", f"{rev}:{SESSIONS_FILE}"),
                               capture_output=True, text=True, check=False)
        text = shown.stdout if shown.returncode == 0 else None
    if text is None:
        if required:
            where = "the working tree" if rev is None else f"commit {rev}"
            raise ValueError(f"{SESSIONS_FILE}: absent at {where}, and a stage counted from "
                             "the session transcripts reads it (tools/sessions.py record "
                             "writes it)")
        return {"sessions": [], "recorded_at": None}
    data = json.loads(text)
    sessions = data.get("sessions") if isinstance(data, dict) else None
    check_sessions(sessions)
    check_idle_gap(data)
    point = data.get("recorded_at")
    if point is not None:
        if not isinstance(point, str):
            raise ValueError(f"{SESSIONS_FILE}: recorded_at {point!r} is not a stamp")
        try:
            point = instant(point)
        except ValueError as error:
            raise ValueError(f"{SESSIONS_FILE}: recorded_at: {error}") from None
    return {"sessions": sessions, "recorded_at": point}


def check_idle_gap(document: dict, where: str = SESSIONS_FILE, required: bool = False) -> None:
    """ValueError when a ledger states an idle-gap threshold other than
    SESSION_GAP_HOURS: its windows were split at that threshold, and summing them
    with a budget stated at this one would report a count at a threshold neither
    file names. Absent, a session ledger was recorded at LEGACY_IDLE_GAP_HOURS (the
    module docstring, `check_idle_gap`); with `required` (the unit ledger, whose
    windows were all written after the threshold was stated) absence is refused."""
    if required and isinstance(document, dict) and IDLE_GAP_FIELD not in document:
        raise ValueError(f"{where}: no {IDLE_GAP_FIELD}, so the threshold its windows were "
                         "split at is unstated")
    value = (document.get(IDLE_GAP_FIELD, LEGACY_IDLE_GAP_HOURS) if isinstance(document, dict)
             else None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: {IDLE_GAP_FIELD} {value!r} is not a number of hours")
    if float(value) != SESSION_GAP_HOURS:
        raise ValueError(f"{where}: {IDLE_GAP_FIELD} is {value:g} h, but the session "
                         f"gap is {SESSION_GAP_HOURS:g} h (gitlog.SESSION_GAP_HOURS): its windows "
                         "were split at another threshold than the one the budget states")


def load_sessions(root: Path, rev: str | None = None, required: bool = False) -> list[dict]:
    """`load_ledger`'s sessions alone."""
    return load_ledger(root, rev, required)["sessions"]


def _pairs(name: str, field: str, items, width: int,
           where: str = SESSIONS_FILE) -> list[tuple[datetime, datetime]]:
    """`field`'s items as (start, end) instants, each a list of `width` strings whose
    first two are stamps with the start not after the end."""
    if not isinstance(items, list):
        raise ValueError(f"{where}: {name}: {field!r} must be a list")
    out = []
    for item in items:
        if not (isinstance(item, list) and len(item) == width
                and all(isinstance(s, str) for s in item)):
            raise ValueError(f"{where}: {name}: {field} item {item!r} is not "
                             f"a list of {width} strings beginning [start, end]")
        try:
            start, end = instant(item[0]), instant(item[1])
        except ValueError as error:
            raise ValueError(f"{where}: {name}: {field} item {item!r}: "
                             f"{error}") from None
        if end < start:
            raise ValueError(f"{where}: {name}: {field} item {item!r} ends "
                             "before it starts")
        out.append((start, end))
    return out


def check_split(entry: dict, name: str, where: str = SESSIONS_FILE) -> list[tuple[datetime,
                                                                                  datetime]]:
    """One ledger entry's windows and gaps, a session's or a unit's (`check_sessions`):
    the windows in time order, none overlapping the one before; every split gap the space
    between two consecutive windows, those spaces all listed; every bridged gap inside a
    window. Returns the windows as instants."""
    windows = _pairs(name, "windows", entry["windows"], 2, where)
    for (_, previous_end), (start, _) in zip(windows, windows[1:]):
        if start < previous_end:
            raise ValueError(f"{where}: {name}: a window starting "
                             f"{start.isoformat()} starts before the window ahead of it "
                             "ends")
    gaps = _pairs(name, "gaps", entry["gaps"], 4, where)
    kinds = [item[3] for item in entry["gaps"]]
    if any(kind not in (GAP_BRIDGED, GAP_SPLIT) for kind in kinds):
        raise ValueError(f"{where}: {name}: a gap is neither "
                         f"{GAP_BRIDGED!r} nor {GAP_SPLIT!r}")
    split = [gap for gap, kind in zip(gaps, kinds) if kind == GAP_SPLIT]
    spaces = [(a[1], b[0]) for a, b in zip(windows, windows[1:])]
    if split != spaces:
        raise ValueError(f"{where}: {name}: its split gaps are not the "
                         "spaces between its windows")
    for (low, high), kind in zip(gaps, kinds):
        if kind == GAP_BRIDGED and not any(a <= low and high <= b for a, b in windows):
            raise ValueError(f"{where}: {name}: a bridged gap from "
                             f"{low.isoformat()} lies outside every window")
    return windows


def check_sessions(sessions) -> None:
    """ValueError on the first malformed session: every session has its fields; its
    windows are [start, end] stamps with the start not after the end, in time order,
    none overlapping the one before; and its gaps agree with its windows (every
    split gap is the space between two consecutive windows, those spaces are all
    listed, and every bridged gap lies inside a window). tools/sessions.py audit
    reads the same."""
    if not isinstance(sessions, list):
        raise ValueError(f"{SESSIONS_FILE}: 'sessions' must be a list")
    seen: set[str] = set()
    for session in sessions:
        if not isinstance(session, dict):
            raise ValueError(f"{SESSIONS_FILE}: a session is not an object: {session!r}")
        for field in ("id", "source", "windows", "gaps", "hours"):
            if field not in session:
                raise ValueError(f"{SESSIONS_FILE}: session missing {field!r}: {session.get('id')}")
        if session["id"] in seen:
            # Two entries for one session would charge it twice.
            raise ValueError(f"{SESSIONS_FILE}: session {session['id']} is listed twice")
        seen.add(session["id"])
        check_split(session, session["id"])


Window = tuple[datetime, datetime]


def windows(session: dict | None) -> list[Window]:
    """A ledger session's windows as instants ([] for None)."""
    return [(instant(a), instant(b)) for a, b in (session or {}).get("windows", [])]


def first_to_last_windows(spans: Sequence[Window]) -> list[Window]:
    """The first-to-last reading, reported beside the binding split windows: one window
    from the first window's start to the last window's end, split gaps included.
    Singleton windows still supply its endpoints."""
    return [(spans[0][0], spans[-1][1])] if spans else []


def clip(spans: Sequence[Window], start: datetime | None, end: datetime | None) -> list[Window]:
    """The parts of `spans` inside [start, end) (either bound None: unbounded), empty
    parts dropped."""
    out = []
    for low, high in spans:
        if start is not None:
            low = max(low, start)
        if end is not None:
            high = min(high, end)
        if high > low:
            out.append((low, high))
    return out


def subtract(spans: Sequence[Window], covered: Sequence[Window]) -> list[Window]:
    """The parts of `spans` no window of `covered` holds."""
    out = []
    for window in spans:
        pieces = [window]
        for cover_start, cover_end in covered:
            remaining = []
            for low, high in pieces:
                if cover_end <= low or cover_start >= high:
                    remaining.append((low, high))
                    continue
                if low < cover_start:
                    remaining.append((low, cover_start))
                if cover_end < high:
                    remaining.append((cover_end, high))
            pieces = remaining
        out += [(low, high) for low, high in pieces if high > low]
    return out


def span(spans: Sequence[Window]) -> timedelta:
    """The total length of `spans`, exact to the microsecond, so a sum of floats
    cannot turn a covered session into a sliver of uncovered time."""
    return sum((high - low for low, high in spans), timedelta())


def transcript_gaps(sessions: Sequence[dict], start: datetime | None,
                    end: datetime | None) -> dict[str, float | int]:
    """The gaps longer than the session gap inside [start, end) (either bound None:
    unbounded), each clipped to it:
    {"bridged": count, "bridged_hours": h, "split": count, "split_hours": h}."""
    out: dict[str, float | int] = {GAP_BRIDGED: 0, f"{GAP_BRIDGED}_hours": 0.0,
                                   GAP_SPLIT: 0, f"{GAP_SPLIT}_hours": 0.0}
    for session in sessions:
        for low, high, _closer, kind in session["gaps"]:
            inside = span(clip([(instant(low), instant(high))], start, end)).total_seconds()
            if inside > 0.0:
                out[kind] += 1
                out[f"{kind}_hours"] += inside / 3600.0
    return out


def recorded_to(sessions: Sequence[dict]) -> datetime | None:
    """The newest window end in the ledger, None when it holds no window."""
    ends = [instant(session["windows"][-1][1]) for session in sessions if session["windows"]]
    return max(ends) if ends else None
