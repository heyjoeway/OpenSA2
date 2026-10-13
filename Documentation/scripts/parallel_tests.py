#!/usr/bin/env python3
"""Run Documentation/scripts/tests across worker processes; one summary, pytest's.

The user's decision of 2026-10-11 (Documentation/decisions/2026-10-11-S7-gate-speed.md)
keeps every gate and makes the pass faster. Run serially, the "script tests" suite was
one pytest process and most of a ship's gate time. This runs the same tests, every one,
across worker processes, with no package beyond pytest itself.

How a run goes
--------------
1. **Collect.** One `pytest --collect-only` process collects the whole directory, as
   the serial run would; its node ids are the test set.
2. **Split.** Each test file is a job. Jobs start longest first, by the seconds the
   timing record gives (private/generated/script-tests/timings.rec, never tracked,
   rewritten after every run; a file it lacks counts as UNKNOWN_SECONDS, and among
   those the largest file starts first). The tests
   `Documentation/parallel-tests-serial.json` lists are held out of the parallel part.
   A job's peak, for the memory rule below, is the timing record's too, and
   UNKNOWN_MIB without one: a machine without the record admits a heavy held-out
   file too early, so the record is kept (it is rewritten, never removed).
3. **Run.** As many jobs at once as `worker_count` gives: the lesser of the CPUs and
   what memory allows, with RESERVE_MIB kept free. A job starts only when its
   recorded peak, with RESERVE_MIB kept free, fits in the memory available beside
   what the running jobs may still grow to (`need_mib`). A job whose peak and the
   reserve together exceed the machine runs alone, once its peak fits, as the serial
   gate ran it. A job that cannot start for MEMORY_WAIT_SECONDS with nothing running
   is not started, and the run fails naming it. Each job is its own
   `python -m pytest` process with its own cache directory and logs under the run
   directory, and its own `--basetemp` and TMPDIR in the run's temporary directory
   (`temp_dir`), on disk, never /tmp. A recorder plugin writes each report's
   category, as pytest's own terminal counts it, with each failure's text.
4. **Serial tail.** The held-out tests run after the parallel part, in one process
   with nothing else running, admitted by the same memory rule. They are never
   skipped.
5. **Check and summarise.** The run fails on any failed or erroring test; on any job
   that dies, exits with a status its reports do not explain, or writes no record;
   and unless the node ids that ran (reported their setup) are exactly the node ids
   collected, each once. The summary is pytest's: its failures, then its line.

Usage:
    .venv/bin/python Documentation/scripts/parallel_tests.py [--jobs N|auto] [--run-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
TESTS = "Documentation/scripts/tests"
SERIAL_LIST = "Documentation/parallel-tests-serial.json"
SERIAL_SCHEMA = "sa2-parallel-tests-serial/v1"
# Not *.json: the M7 census reads every *.json under private/generated as a possible
# run record (tools/s4/m7_inputs.py), so nothing this writes there is named so.
TIMINGS = "private/generated/script-tests/timings.rec"
TIMINGS_SCHEMA = "sa2-script-tests-timings/v3"
SPEC_ENV = "SA2_PARALLEL_TESTS_SPEC"
# A file the timing record lacks is assumed this slow (seconds) and this large (MiB).
UNKNOWN_SECONDS = 120.0
UNKNOWN_MIB = 2048
# Memory kept free for everything else on the machine (MiB): 8 GB, after a 31 GB
# machine ran out of memory on 2026-10-11 with several units' jobs side by side.
RESERVE_MIB = 8192
# A file's peak is the largest of its last few runs'.
PEAK_HISTORY = 8
# The per-worker peak the worker count is sized by: this quantile of the jobs' peaks.
# A larger job is held back by the admission rule in `run_jobs`.
WORKER_PEAK_QUANTILE = 0.9
# Where the jobs' basetemps and TMPDIRs go (`temp_dir`): on disk and outside the
# repository. Not under the run directory itself: the tests take a path outside the
# tree to be outside it, as the serial run's /tmp was (a tool refuses an output inside
# the tree, or names it relative to the tree), and they plant run records there that
# the M7 census would read under private/generated. Not /tmp, a RAM-backed tmpfs here.
TEMP_ROOT = Path("/var/tmp")
# A run's temporary directory that a failed run kept is removed this many days on.
TEMP_KEEP_DAYS = 3
# A job that memory keeps from starting this long, with none of this run's jobs
# running, is not started; the run fails naming it rather than wait unattended.
MEMORY_WAIT_SECONDS = 3600


# ------------------------------------------------------------------ the recorder
#
# Loaded into each worker by `-p sa2_parallel_recorder`, a module in the run
# directory that imports this file by path (`write_shim`), so the tests run under
# `python -m pytest` as the serial gate ran them.

class Recorder:
    def __init__(self, config, spec: dict):
        self.config = config
        self.spec = spec
        self.keep = set(spec["keep"]) if spec.get("keep") is not None else None
        self.collected: list[str] = []
        self.reports: list[dict] = []
        self.counts: dict[str, int] = {}             # the tests' reports
        self.collect_counts: dict[str, int] = {}     # the collection's (errors, skipped modules)
        self.order: list[str] = []
        self.failures: list[dict] = []
        self.warnings = {"config": 0, "collect": 0, "runtest": 0}

    def count(self, category: str, counts: dict[str, int]) -> None:
        if category:
            if category not in self.order:
                self.order.append(category)
            counts[category] = counts.get(category, 0) + 1

    def pytest_collection_modifyitems(self, session, config, items):
        if self.keep is not None:
            items[:] = [i for i in items if i.nodeid in self.keep]
        self.collected = [i.nodeid for i in items]

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            category = "error" if report.failed else "skipped"
            self.count(category, self.collect_counts)
            entry = {"nodeid": report.nodeid, "when": "collect", "category": category,
                     "duration": 0.0}
            self.reports.append(entry)
            if report.failed:
                self.failures.append(dict(entry, text=str(report.longreprtext)))

    def pytest_runtest_logreport(self, report):
        res = self.config.hook.pytest_report_teststatus(report=report, config=self.config)
        category = res[0] if res else ""
        if isinstance(category, tuple):
            category = category[0]
        if getattr(report, "count_towards_summary", True):
            self.count(category, self.counts)
        entry = {"nodeid": report.nodeid, "when": report.when, "category": category,
                 "duration": float(report.duration)}
        self.reports.append(entry)
        if report.failed:
            text = str(report.longreprtext)
            for title, content in report.sections:
                text += f"\n{'-' * 20} {title} {'-' * 20}\n{content}"
            self.failures.append(dict(entry, text=text))

    def pytest_warning_recorded(self, warning_message, when, nodeid, location):
        self.warnings[when] = self.warnings.get(when, 0) + 1

    def pytest_sessionfinish(self, session, exitstatus):
        out = {"job": self.spec.get("job"), "collected": self.collected,
               "reports": self.reports, "counts": self.counts,
               "collect_counts": self.collect_counts, "order": self.order,
               "failures": self.failures, "warnings": self.warnings,
               "exitstatus": int(exitstatus)}
        path = Path(self.spec["record"])
        part = path.with_suffix(".part")
        part.write_text(json.dumps(out))
        part.replace(path)


def pytest_configure(config):
    """The recorder's entry when this file is loaded as a pytest plugin."""
    spec = json.loads(Path(os.environ[SPEC_ENV]).read_text())
    config.pluginmanager.register(Recorder(config, spec), "sa2-parallel-recorder")


def write_shim(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sa2_parallel_recorder.py").write_text(
        "import importlib.util as _u\n"
        f"_s = _u.spec_from_file_location('_sa2_parallel_tests', {str(HERE)!r})\n"
        "_m = _u.module_from_spec(_s)\n"
        "_s.loader.exec_module(_m)\n"
        "pytest_configure = _m.pytest_configure\n")


def pytest_process(d: Path, temp: Path, shim: Path, spec: dict,
                   args: list[str]) -> tuple[list[str], dict]:
    """The argv and environment of one pytest process whose logs and cache live in `d`
    and whose basetemp and TMPDIR live in `temp`."""
    d.mkdir(parents=True)
    (temp / "t").mkdir(parents=True)
    (d / "spec.rec").write_text(json.dumps(dict(spec, record=str(d / "record.rec"))))
    argv = [sys.executable, "-m", "pytest", *args, "-q", "-p", "sa2_parallel_recorder",
            "--basetemp", str(temp / "b"), "-o", f"cache_dir={d / 'c'}"]
    env = dict(os.environ, TMPDIR=str(temp / "t"), **{SPEC_ENV: str(d / "spec.rec")})
    env["PYTHONPATH"] = os.pathsep.join([str(shim)] + ([env["PYTHONPATH"]]
                                                       if env.get("PYTHONPATH") else []))
    (d / "argv.rec").write_text(json.dumps(argv))
    return argv, env


def fs_type(path: Path) -> str:
    """The file system type of the mount `path` is on, from /proc/mounts."""
    best, kind = "", ""
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            fields = line.split()
            point = fields[1].replace("\\040", " ")
            if (str(path) == point or str(path).startswith(point.rstrip("/") + "/")) \
                    and len(point) >= len(best):
                best, kind = point, fields[2]
    except (OSError, IndexError):
        pass
    return kind


def temp_dir(root: Path, run_dir: Path, temp_root: Path) -> Path:
    """This run's temporary directory, new, under `temp_root`; linked from the run
    directory as `temp`. Refused inside the repository or on a tmpfs."""
    temp_root = temp_root.resolve()
    if temp_root == root or root in temp_root.parents:
        raise SystemExit(f"parallel tests: {temp_root} is inside {root}; the tests take "
                         f"their temporary paths to be outside the tree (--temp-root)")
    if fs_type(temp_root) in ("tmpfs", "ramfs"):
        raise SystemExit(f"parallel tests: {temp_root} is on a {fs_type(temp_root)}, in "
                         f"memory; give a directory on disk (--temp-root)")
    parent = temp_root / f"sa2-script-tests-{os.getuid()}"
    parent.mkdir(mode=0o700, exist_ok=True)
    old = time.time() - TEMP_KEEP_DAYS * 86400
    for p in parent.iterdir():               # what failed runs kept, once it is old
        try:
            if p.is_dir() and not p.is_symlink() and p.stat().st_mtime < old:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass
    temp = Path(tempfile.mkdtemp(prefix=f"{run_dir.name}-", dir=parent))
    (run_dir / "temp").symlink_to(temp, target_is_directory=True)
    return temp


def read_record(d: Path) -> dict | None:
    try:
        return json.loads((d / "record.rec").read_text())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ jobs

class Job:
    def __init__(self, files: list[str], keep: list[str], seconds: float, mib: int, label: str):
        self.files, self.keep, self.seconds, self.mib, self.label = files, keep, seconds, mib, label
        self.index = 0
        self.proc: subprocess.Popen | None = None
        self.dir: Path | None = None
        self.started = 0.0
        self.elapsed = 0.0
        self.status: int | None = None
        self.peak_mib = 0
        self.record: dict | None = None


def node_file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def load_timings(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return doc.get("files", {}) if doc.get("schema") == TIMINGS_SCHEMA else {}


def load_serial(root: Path) -> list[dict]:
    path = root / SERIAL_LIST
    if not path.is_file():
        return []
    doc = json.loads(path.read_text())
    if doc.get("schema") != SERIAL_SCHEMA:
        raise SystemExit(f"{SERIAL_LIST}: schema is {doc.get('schema')!r}, not {SERIAL_SCHEMA!r}")
    out = doc.get("serial", [])
    for item in out:
        if not str(item.get("test", "")).strip() or not str(item.get("reason", "")).strip():
            raise SystemExit(f"{SERIAL_LIST}: every entry names a test and why it runs "
                             f"serially: {item!r}")
    return out


def held_out(collected: list[str], serial: list[dict]) -> tuple[list[str], list[str]]:
    """The collected node ids the serial list names (a node id, or a file for all of
    its tests), and the list's entries that match nothing collected."""
    names = [str(s["test"]) for s in serial]
    held = [n for n in collected if n in names or node_file(n) in names]
    stale = [s for s in names if not any(n == s or node_file(n) == s for n in collected)]
    return held, stale


def plan(collected: list[str], timings: dict, root: Path) -> list[Job]:
    """One job per test file, longest first by the timing record; among files it does
    not time, the largest file first."""
    by_file: dict[str, list[str]] = {}
    for n in collected:
        by_file.setdefault(node_file(n), []).append(n)
    jobs = []
    for f, ns in by_file.items():
        rec = timings.get(f) or {}
        jobs.append(Job([f], ns, float(rec.get("seconds", UNKNOWN_SECONDS)),
                        int(rec.get("peak_mib", UNKNOWN_MIB)), f))

    def size(f: str) -> int:
        try:
            return (root / f).stat().st_size
        except OSError:
            return 0
    jobs.sort(key=lambda j: (-j.seconds, -size(j.files[0])))
    return jobs


def worker_count(requested: str, jobs: list[Job], cpus: int | None = None,
                 available: int | None = None, reserve: int = RESERVE_MIB) -> tuple[int, str]:
    """How many jobs run at once, and why: `requested` when it is a number, and for
    `auto` the lesser of the CPUs and what memory allows: the memory available now
    less `reserve`, at the WORKER_PEAK_QUANTILE quantile of the jobs' recorded peaks."""
    cpus = cpus or os.cpu_count() or 1
    if requested != "auto":
        n = max(1, int(requested))
        return n, f"{n} worker(s), as asked"
    peaks = sorted(max(1, j.mib) for j in jobs) or [UNKNOWN_MIB]
    per = peaks[min(len(peaks) - 1, int(WORKER_PEAK_QUANTILE * len(peaks)))]
    avail = mem_available_mib() if available is None else available
    by_memory = max(1, (avail - reserve) // per)
    n = max(1, min(cpus, by_memory, max(1, len(jobs))))
    return n, (f"{n} worker(s): the lesser of {cpus} CPU(s) and the {by_memory} memory allows "
               f"at {per} MiB per worker ({avail} MiB available, {reserve} MiB kept free)")


def meminfo_mib(key: str) -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(f"{key}:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 1 << 30


def mem_available_mib() -> int:
    return meminfo_mib("MemAvailable")


def need_mib(peak: int, alone: bool, reserve: int, total: int) -> int:
    """The memory a job of this recorded peak needs available before it starts (beside
    what the running jobs may still grow to): its peak and the reserve. A job whose peak
    and reserve exceed the whole machine can never keep the reserve; it waits to run
    alone and needs only its peak, as the serial gate ran it."""
    need = peak + reserve
    if need > total:
        return peak if alone else 1 << 40
    return need


def rss_mib(pid: int) -> int:
    """Resident set of a process and its descendants, MiB."""
    total = 0
    todo = [pid]
    while todo:
        p = todo.pop()
        try:
            for line in Path(f"/proc/{p}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
            for t in Path(f"/proc/{p}/task").iterdir():
                todo += [int(k) for k in (t / "children").read_text().split()]
        except (OSError, ValueError):
            continue
    return total // 1024


def start(job: Job, run_dir: Path, temp: Path, root: Path, shim: Path) -> None:
    job.dir = run_dir / f"j{job.index:03d}"
    argv, env = pytest_process(job.dir, temp / job.dir.name, shim,
                               {"job": job.index, "keep": job.keep}, job.files)
    job.started = time.monotonic()
    with (job.dir / "stdout").open("wb") as out, (job.dir / "stderr").open("wb") as err:
        job.proc = subprocess.Popen(argv, cwd=root, env=env, stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL, start_new_session=True)


def reap(job: Job) -> bool:
    """True when the job's process has ended; its status, peak and record taken."""
    pid, status, usage = os.wait4(job.proc.pid, os.WNOHANG)
    if pid == 0:
        return False
    job.elapsed = time.monotonic() - job.started
    job.status = os.waitstatus_to_exitcode(status)
    job.peak_mib = int(usage.ru_maxrss) // 1024
    job.proc.returncode = job.status
    job.record = read_record(job.dir)
    return True


def run_jobs(jobs: list[Job], width: int, run_dir: Path, temp: Path, root: Path,
             shim: Path, reserve: int = RESERVE_MIB) -> None:
    """Start `jobs` in order, at most `width` at once, each once `need_mib` fits.
    A job that cannot start for MEMORY_WAIT_SECONDS while none runs is left unstarted
    with every job after it; `check` then fails the run on them."""
    pending = list(jobs)
    running: list[Job] = []
    total = meminfo_mib("MemTotal")
    said, idle_since = 0.0, None
    try:
        while pending or running:
            for job in list(running):
                if reap(job):
                    running.remove(job)
            while pending and len(running) < width:
                job = pending[0]
                avail = mem_available_mib()
                grow = sum(max(0, j.mib - rss_mib(j.proc.pid)) for j in running)
                need = need_mib(job.mib, not running, reserve, total)
                if avail - grow < need:
                    if not running:
                        idle_since = idle_since or time.monotonic()
                        if time.monotonic() - idle_since > MEMORY_WAIT_SECONDS:
                            print(f"parallel tests: {len(pending)} job(s) not started: "
                                  f"{job.label} needs {need} MiB available and "
                                  f"{avail} MiB was, for {MEMORY_WAIT_SECONDS} s",
                                  flush=True)
                            pending.clear()
                        elif time.monotonic() - said > 60:
                            said = time.monotonic()
                            print(f"parallel tests: waiting for memory: {job.label} needs "
                                  f"{need} MiB available, {avail} MiB is", flush=True)
                    break
                idle_since = None
                pending.pop(0)
                start(job, run_dir, temp, root, shim)
                running.append(job)
            time.sleep(0.2)
    finally:
        for job in running:                 # stopped: no worker outlives the run
            try:
                os.killpg(job.proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(job.proc.pid, 0)
            except OSError:
                pass


# ------------------------------------------------------------------ the check

def check(collected: list[str], jobs: list[Job], collect_record: dict | None) -> list[str]:
    """Why the run cannot be trusted beyond its test results: dead or unexplained
    workers, and node ids that ran not exactly once each as collected."""
    problems: list[str] = []
    if collect_record is None:
        problems.append("the collection wrote no record")
    elif collect_record.get("exitstatus") != 0:
        problems.append(f"the collection exited {collect_record.get('exitstatus')}")
    if not collected:
        problems.append("the collection collected no tests")
    ran: dict[str, int] = {}
    for job in jobs:
        rec = job.record
        if job.status is None:
            problems.append(f"job {job.label} never ran")
            continue
        if rec is None:
            problems.append(f"job {job.label} exited {job.status} and wrote no record "
                            f"(a worker that died); its output is in {job.dir}")
            continue
        failed = rec.get("failures", [])
        if job.status not in (0, 1):
            problems.append(f"job {job.label} exited {job.status}, which pytest's reports "
                            f"do not explain; its output is in {job.dir}")
        elif (job.status == 1) != bool(failed):
            problems.append(f"job {job.label} exited {job.status} with {len(failed)} "
                            f"failure(s) recorded")
        if sorted(rec.get("collected", [])) != sorted(job.keep):
            lost = sorted(set(job.keep) - set(rec.get("collected", [])))
            problems.append(f"job {job.label} collected {len(rec.get('collected', []))} of its "
                            f"{len(job.keep)} node ids; not collected: {lost[:5]}")
        # Run, not only collected: every test that runs reports its setup, a skipped
        # one included; a session ended early (pytest.exit) leaves the rest without.
        for n in dict.fromkeys(r["nodeid"] for r in rec.get("reports", [])
                               if r.get("when") == "setup"):
            ran[n] = ran.get(n, 0) + 1
    want = set(collected)
    missing = [n for n in collected if n not in ran]
    doubled = [n for n, c in ran.items() if c > 1]
    added = [n for n in ran if n not in want]
    if missing:
        problems.append(f"{len(missing)} collected node id(s) did not run: {missing[:5]}")
    if doubled:
        problems.append(f"{len(doubled)} node id(s) ran more than once: {doubled[:5]}")
    if added:
        problems.append(f"{len(added)} node id(s) ran that the collection did not "
                        f"collect: {added[:5]}")
    return problems


# ------------------------------------------------------------------ the summary

def summary_line(counts: dict[str, int], order: list[str], seconds: float) -> str:
    """pytest's last line for these counts: its known categories in its order,
    any other in the order first seen, its plurals and its duration format."""
    from _pytest.terminal import KNOWN_TYPES, format_session_duration, pluralize
    keys = list(KNOWN_TYPES) + [k for k in order if k not in KNOWN_TYPES]
    parts = ["%d %s" % pluralize(counts[k], k) for k in keys if counts.get(k)]
    return f"{', '.join(parts) or 'no tests ran'} in {format_session_duration(seconds)}"


def tally(collect_record: dict | None, jobs: list[Job]) -> tuple[dict[str, int], list[str]]:
    """The counts pytest's line is built from: the collection's own reports (its
    errors and skipped modules) and its config and collect warnings once, from the
    collection run, since every job collects its files again; every job's tests'
    reports and warnings."""
    counts: dict[str, int] = {}
    order: list[str] = []
    parts = [(collect_record or {}).get("collect_counts", {})] + \
        [j.record.get("counts", {}) for j in jobs if j.record]
    for rec in [collect_record or {}] + [j.record for j in jobs if j.record]:
        order += [k for k in rec.get("order", []) if k not in order]
    for part in parts:
        for k, v in part.items():
            counts[k] = counts.get(k, 0) + v
    w = (collect_record or {}).get("warnings") or {}
    warned = int(w.get("config", 0)) + int(w.get("collect", 0)) + sum(
        int((j.record.get("warnings") or {}).get("runtest", 0)) for j in jobs if j.record)
    if warned:
        counts["warnings"] = warned
    return counts, order


def report(collect_record: dict | None, jobs: list[Job], problems: list[str],
           seconds: float) -> tuple[str, dict[str, int]]:
    """Failures, the run's problems and pytest's line; the line and the counts."""
    counts, order = tally(collect_record, jobs)
    # A job collects its files again: a collection error the collection run reported
    # already is printed once, from it; one only a job met is printed from the job.
    failures = list((collect_record or {}).get("failures", []))
    known = {f["nodeid"] for f in failures}
    failures += [f for j in jobs if j.record for f in j.record.get("failures", [])
                 if f["when"] != "collect" or f["nodeid"] not in known]
    if failures:
        print("=" * 30 + " FAILURES " + "=" * 30)
        for f in failures:
            print(f"{'_' * 20} {f['nodeid']} ({f['when']}) {'_' * 20}")
            print(f["text"])
        print("=" * 25 + " short test summary info " + "=" * 25)
        for f in failures:
            kind = "ERROR" if f["category"] == "error" else "FAILED"
            last = (f["text"].strip().splitlines() or [""])[-1][:200]
            print(f"{kind} {f['nodeid']} - {last}")
    if problems:
        print("=" * 25 + " parallel run problems " + "=" * 25)
        for p in problems:
            print(f"PROBLEM {p}")
        counts["error"] = counts.get("error", 0) + len(problems)
    line = summary_line(counts, order, seconds)
    print(line, flush=True)
    return line, counts


def write_timings(path: Path, jobs: list[Job], old: dict) -> None:
    """Per file: its tests' seconds (setup, call and teardown), and its peak, the
    largest of its last PEAK_HISTORY runs' by a job that ran that file alone."""
    files = {f: dict(v) for f, v in old.items()}
    seconds: dict[str, float] = {}
    for job in jobs:
        if not job.record:
            continue
        if len(job.files) == 1:
            entry = files.setdefault(job.files[0], {})
            entry["peaks"] = (list(entry.get("peaks") or []) + [job.peak_mib])[-PEAK_HISTORY:]
            entry["peak_mib"] = max(entry["peaks"])
        for r in job.record.get("reports", []):
            if r["when"] in ("setup", "call", "teardown"):
                f = node_file(r["nodeid"])
                seconds[f] = seconds.get(f, 0.0) + r["duration"]
    for f, s in seconds.items():
        files.setdefault(f, {})["seconds"] = round(s, 2)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(f".{os.getpid()}.part")
        part.write_text(json.dumps({"schema": TIMINGS_SCHEMA, "files": files}, indent=1,
                                   sort_keys=True))
        part.replace(path)
    except OSError as e:
        print(f"warning: the timing record {path} could not be written ({e})", file=sys.stderr)


def collect(root: Path, tests: str, run_dir: Path, temp: Path,
            shim: Path) -> tuple[list[str], dict | None]:
    d = run_dir / "collect"
    argv, env = pytest_process(d, temp / d.name, shim, {"job": "collect", "keep": None},
                               [tests, "--collect-only"])
    with (d / "stdout").open("wb") as out, (d / "stderr").open("wb") as err:
        subprocess.run(argv, cwd=root, env=env, stdout=out, stderr=err,
                       stdin=subprocess.DEVNULL, check=False)
    rec = read_record(d)
    return (list(rec.get("collected", [])) if rec else []), rec


def drop_temp(run_dir: Path, temp: Path) -> None:
    """After a run that passed: the run's temporary directory, every job's basetemp and
    TMPDIR, is removed; each job's argv, spec, record, output and cache stay with the
    gate's logs. A failed run keeps all of it (`temp_dir` removes it once it is old)."""
    shutil.rmtree(temp, ignore_errors=True)
    (run_dir / "temp").unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)

    def jobs_arg(text: str) -> str:
        if text != "auto" and not (text.isdigit() and int(text) > 0):
            raise argparse.ArgumentTypeError(f"{text!r} is neither a positive number nor auto")
        return text

    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--tests", default=TESTS)
    parser.add_argument("--jobs", default="auto", type=jobs_arg,
                        help="jobs at once: a number, or auto (the default): the lesser of "
                             "the CPUs and what memory allows")
    parser.add_argument("--run-dir", type=Path, default=None,
                        help="where each worker's cache and logs go, and a link to its "
                             "basetemp and TMPDIR (default: a new directory under "
                             "private/generated/script-tests)")
    parser.add_argument("--temp-root", type=Path, default=TEMP_ROOT,
                        help=f"on disk, outside the repository: where the workers' basetemps "
                             f"and TMPDIRs go (default: {TEMP_ROOT})")
    parser.add_argument("--timings", type=Path, default=None)
    parser.add_argument("--reserve-mib", type=int, default=RESERVE_MIB,
                        help=f"memory kept free, MiB (default: {RESERVE_MIB}; this tool's "
                             f"own tests lower it so they run on a busy machine)")
    parser.add_argument("--keep-temp", action="store_true",
                        help="keep the jobs' temporary directories after a run that passed")
    parser.add_argument("--record", type=Path, default=None,
                        help="write the run's line, counts, problems and job times here")
    args = parser.parse_args(argv)

    root = (args.root or Path.cwd()).resolve()
    if args.run_dir is None:
        base = root / "private" / "generated" / "script-tests" / "runs"
        base.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    else:
        run_dir = args.run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
    temp = temp_dir(root, run_dir, args.temp_root)
    print(f"parallel tests: {run_dir}; temporary files in {temp}", flush=True)
    started = time.monotonic()
    shim = run_dir / "plugin"
    write_shim(shim)

    def stop(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)   # a stopped run stops its workers
    try:
        return run(args, root, run_dir, temp, shim, started)
    finally:
        signal.signal(signal.SIGTERM, previous)


def run(args: argparse.Namespace, root: Path, run_dir: Path, temp: Path, shim: Path,
        started: float) -> int:
    timings_path = args.timings or (root / TIMINGS)
    collected, collect_record = collect(root, args.tests, run_dir, temp, shim)
    problems: list[str] = []
    held, stale = held_out(collected, load_serial(root))
    for s in stale:
        problems.append(f"{SERIAL_LIST} names {s!r}, which nothing collected matches")
    timings = load_timings(timings_path)
    apart = set(held)
    jobs = plan([n for n in collected if n not in apart], timings, root)
    tail_files = list(dict.fromkeys(node_file(n) for n in held))
    tail = [Job(tail_files, held, 0.0, max(int((timings.get(f) or {}).get(
        "peak_mib", UNKNOWN_MIB)) for f in tail_files), "serial tail")] if held else []
    for i, job in enumerate(jobs + tail):
        job.index = i
    width, why = worker_count(args.jobs, jobs, reserve=args.reserve_mib)
    print(f"parallel tests: {len(jobs)} job(s), {why}; {len(held)} test(s) after them, "
          f"alone", flush=True)
    run_jobs(jobs, width, run_dir, temp, root, shim, args.reserve_mib)
    run_jobs(tail, 1, run_dir, temp, root, shim, args.reserve_mib)
    every = jobs + tail
    problems += check(collected, every, collect_record)
    seconds = time.monotonic() - started
    line, counts = report(collect_record, every, problems, seconds)
    write_timings(timings_path, every, timings)
    if args.record:
        args.record.write_text(json.dumps({
            "mode": "parallel", "jobs": width, "line": line, "counts": counts,
            "collected": len(collected), "problems": problems,
            "job_times": [{"label": j.label, "seconds": round(j.elapsed, 2),
                           "started": round(j.started - started, 2) if j.started else None,
                           "peak_mib": j.peak_mib, "status": j.status} for j in every],
            "seconds": round(seconds, 2)}, indent=1))
    bad = problems or any(counts.get(k) for k in ("failed", "error", "subtests failed"))
    if not bad and not args.keep_temp:
        drop_temp(run_dir, temp)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
