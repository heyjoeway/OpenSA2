#!/usr/bin/env python3
"""Wait for background work to finish, without the wait outliving the work.

Subtitle: `.venv/bin/python tools/waitfor.py --pid 1234` or `--file done.json`

Five wait loops in one session outlived the work they watched, the longest by
seventeen hours and forty-five minutes. Every one of them was some form of

    until ! pgrep -f "some_tool.py --flag"; do sleep 10; done

and every one waited forever, because **the shell running that loop has the
pattern in its own command line**, so `pgrep -f` matches the waiter. The work
had finished; the waiter had not.

Three of the five used the `[s]ome_tool.py` bracket trick specifically to
avoid this. It does not help. The trick works because a shell glob does not
expand `[s]ome_tool.py` into itself, so the pattern in the *process list*
differs from the pattern in the *argument*. But `pgrep -f` matches against
the waiting shell's own `-c` argument, where the literal `[s]` is still
sitting. The fix that looks right, and is right when typed at a prompt, is
wrong here.

So this tool exists, and the rule is not advisory:

* it waits on **a PID or a sentinel file**, never on a pattern;
* `--pattern` is accepted and **refused** when it matches this process's own
  command line, which is the case that has actually happened -- the
  alternative, silently rewriting it, would leave the author believing a
  pattern wait is fine;
* it is **bounded**. The default timeout is under the harness's tool timeout,
  so the wait returns and can simply be run again rather than being
  backgrounded and orphaned. An unbounded wait is what turns a hang into five
  hangs.

Exit codes: 0 the thing finished, 1 the deadline passed (not an error -- run
it again), 2 the wait was refused as unsound.

Usage:
  tools/waitfor.py --pid 1234 [--timeout 450] [--interval 5]
  tools/waitfor.py --file path/to/sentinel [--nonempty]
  tools/waitfor.py --self-test
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT = 450.0   # under the harness tool timeout, deliberately
DEFAULT_INTERVAL = 5.0


def own_command_line() -> str:
    try:
        return Path("/proc/self/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace")
    except OSError:
        return " ".join(sys.argv)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def refuse(message: str) -> int:
    print(f"waitfor: refused: {message}", file=sys.stderr)
    return 2


def self_test() -> int:
    """Show the refusal firing, and a bounded wait returning rather than hanging."""
    ok = True

    mine = own_command_line()
    hit = "waitfor.py" in mine
    print(f"  own command line contains 'waitfor.py': {hit}  "
          f"(this is why a pattern wait cannot be trusted)")
    ok &= hit

    start = time.time()
    rc = wait_for_file(Path("/nonexistent/sentinel/never-appears"), False, 2.0, 0.2)
    elapsed = time.time() - start
    bounded = rc == 1 and elapsed < 5.0
    print(f"  a file that never appears -> exit {rc} after {elapsed:.1f}s "
          f"(bounded: {bounded})")
    ok &= bounded

    rc = wait_for_pid(os.getpid(), 1.0, 0.2)
    print(f"  waiting on our own live pid -> exit {rc} (expected 1, the deadline)")
    ok &= rc == 1

    rc = wait_for_pid(2 ** 22, 5.0, 0.2)
    print(f"  waiting on a pid that does not exist -> exit {rc} (expected 0, immediate)")
    ok &= rc == 0

    print("waitfor self-test OK" if ok else "waitfor self-test FAILED")
    return 0 if ok else 1


def wait_for_pid(pid: int, timeout: float, interval: float) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return 0
        time.sleep(min(interval, max(0.0, deadline - time.time())))
    return 1


def wait_for_file(path: Path, nonempty: bool, timeout: float, interval: float) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and (not nonempty or path.stat().st_size > 0):
            return 0
        time.sleep(min(interval, max(0.0, deadline - time.time())))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--pattern", help="refused; wait on a PID or a sentinel file")
    parser.add_argument("--nonempty", action="store_true",
                        help="with --file, also require it to be non-empty")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if args.pattern:
        if args.pattern.strip("[]") and args.pattern.replace("[", "").replace("]", "") \
                in own_command_line().replace("[", "").replace("]", ""):
            return refuse(
                f"the pattern {args.pattern!r} matches this process's own command line, so a "
                f"pattern wait on it can never end. This is the defect that left five waiters "
                f"running in one session, the longest for 17h45m. Wait on --pid or --file. "
                f"(The [b]racket trick does not help: pgrep -f reads the un-globbed argument.)")
        return refuse(f"--pattern is not supported. Wait on --pid or --file; a pattern wait "
                      f"cannot distinguish the work from the waiter.")

    if bool(args.pid) == bool(args.file):
        return refuse("give exactly one of --pid or --file")

    if args.timeout > 500:
        return refuse(f"--timeout {args.timeout} exceeds the harness tool timeout, so this "
                      f"wait would be backgrounded and orphaned. Use a shorter one and run "
                      f"it again; a bounded wait that returns is the point.")

    if args.pid:
        rc = wait_for_pid(args.pid, args.timeout, args.interval)
        print(f"waitfor: pid {args.pid} "
              + ("finished" if rc == 0 else f"still running after {args.timeout:.0f}s"))
        return rc

    rc = wait_for_file(args.file, args.nonempty, args.timeout, args.interval)
    print(f"waitfor: {args.file} "
          + ("appeared" if rc == 0 else f"absent after {args.timeout:.0f}s"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
