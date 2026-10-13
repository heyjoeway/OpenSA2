#!/usr/bin/env python3
"""Gate: a clean-checkout reproduction of the run reach exists and succeeded.

Two halves of one question, split by what they cost.

`tools/s4/reach_reproduce.py` is the expensive half: it takes only the tracked
files at HEAD into a fresh directory, regenerates every build input the tree is
supposed to determine, rebuilds, runs the guest and compares the reach against
`s4-run-reach-v1.json`. It is a full relink and a driven run, and it requires a
clean working tree, so it cannot run on every push -- and a gate that cannot
run is one people learn to skip.

This is the cheap half, and it runs in the suite. It reads the reproduction's
receipt and requires that one exists and that it **matched**. It deliberately
does *not* fail merely because the receipt is behind HEAD: that is what makes
it affordable per push, and the currency requirement lives where it belongs,
in G4's exit criteria, which run this script with `--current`.

So: every push must be able to point at a successful reproduction. The gate
review must point at a *current* one.

**One exception, on every push: a receipt rewritten in the commit under
construction** (its bytes differ from HEAD's, staged or not) must be current
for that commit, by the whole `--current` test below plus its falsifications:
it names HEAD, and the export digest is the working tree's. Nothing else can
ever make it current, because the commit that carries it is the only commit
it can be current on. Until 2026-09-26 the closing order (every edit, then
`metrics.py --write`, then `reach_reproduce.py --write`, then `ship.py`) was
only written down, and all sixteen receipts committed by then fail
`--current`'s digest: METRICS.md or other receipts were regenerated after the
export (review-gq-reach finding 1). The failure then surfaced at the gate
review, a stage later, where the only remedy is another lifted run and
another commit. `ship.py` runs the gates after `metrics.py --write` and
`git add -A`, so this refuses the push itself.

What "current" means (`--current`)
----------------------------------
Two halves, and both must hold.

* **The build inputs.** The receipt's input manifest (`generated`, `tracked`)
  equals the tree's, from `tools/s4/reach_receipt.py manifest()`.
* **The tree.** The reproduction exported a tree, and the tree being gated is
  that tree. `reach_reproduce.py` records HEAD *before* the commit that
  carries its receipt, so the receipt names HEAD only while it is
  uncommitted, and the gate report of such a tree is stamped DIRTY. Until
  2026-09-26 this required `commit == HEAD`, which a committed receipt can
  never meet, so G4's report could not be generated at a clean commit. It now
  accepts exactly two states, and nothing looser:

  - **uncommitted**: the receipt names HEAD (it was written on HEAD and not
    committed yet);
  - **committed by HEAD**: the receipt names HEAD's parent, HEAD is the
    commit that last wrote the receipt (`git log -1 -- <receipt>`), and the
    working tree is clean, so what is gated is HEAD's tree. "Clean" is read
    with explicit flags (`--untracked-files=all --ignore-submodules=none`),
    because `git status` follows the user's configuration and
    `status.showUntrackedFiles=no` hides a file the export held and the commit
    does not; and the files an export would hold must be exactly HEAD's
    (`git ls-tree -r HEAD`).

  In both, every index entry must be a plain one (`git ls-files -v` tag `H`):
  an entry marked assume-unchanged or skip-worktree reads clean whatever its
  bytes, and `git add -A` leaves it out, so the tree the digest reads would
  not be the commit's.

  In both, the export digest is recomputed over the tree as it stands now
  (`reach_reproduce.export_listing` and `export_digest`, the tool's own
  functions) and must equal the recorded one: nothing may have moved between
  the export and the commit, including a METRICS.md that `ship.py`
  regenerated after the export. A receipt written before the digest left out
  its own receipt (no `digest_excludes`) is read back with the receipt's bytes
  as they stood before the write, which is the commit the receipt names; one
  whose `digest_excludes` lists anything but its own path is refused.

Why any of this exists: on 2026-09-21 the driven run fell from 369 WinMain
iterations to 1 because a generated build input lost a reviewed dispatch
exception that had never existed anywhere in the tree, and all 23 gates
passed.

Usage: .venv/bin/python Documentation/scripts/check_reach_reproduction.py [--current]
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))

RECEIPT_REL = "Documentation/receipts/s4-reach-reproduction-v1.json"
RECEIPT = ROOT / RECEIPT_REL
REACH_REL = "Documentation/receipts/s4-run-reach-v1.json"
REACH = ROOT / REACH_REL

RAN = 0
CURRENT_RAN = 0


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True)


def git_text(root: Path, *args: str) -> str:
    return git(root, *args).stdout.decode(errors="replace").strip()


def receipt_pending(root: Path) -> bool:
    """True when the receipt is rewritten in the commit under construction: its
    working or staged bytes differ from HEAD's. Read from the bytes, not from
    `git status`, so neither the user's status configuration nor an index flag
    can hide the rewrite; anything git cannot answer counts as pending, which
    fails closed (the push is then held to the whole --current test)."""
    at_head = git(root, "show", f"HEAD:{RECEIPT_REL}")
    staged = git(root, "show", f":{RECEIPT_REL}")
    work = root / RECEIPT_REL
    now = work.read_bytes() if work.is_file() else None
    head = at_head.stdout if at_head.returncode == 0 else None
    index = staged.stdout if staged.returncode == 0 else None
    if head is None and git(root, "rev-parse", "--verify", "-q", "HEAD").returncode:
        return True
    return now != head or index != head


def index_flag_problems(root: Path) -> list[str]:
    """An index entry marked assume-unchanged or skip-worktree (or unmerged)
    reads clean whatever its bytes, and `git add -A` leaves it out: the tree the
    digest reads is then not the tree a commit holds. Every entry must be plain."""
    flags = git(root, "ls-files", "-v", "-z")
    if flags.returncode:
        return ["git ls-files -v cannot be read, so the index cannot be shown plain"]
    odd = [e[2:] for e in flags.stdout.decode(errors="replace").split("\0")
           if e and not e.startswith("H ")]
    if odd:
        return [f"the index marks {len(odd)} file(s) git status does not compare "
                f"(assume-unchanged, skip-worktree or unmerged: {odd[:3]}), so neither a clean "
                "status nor the digest shows the tree a commit holds; clear the flags "
                "(`git update-index --no-assume-unchanged --no-skip-worktree`)"]
    return []


def tree_problems(doc: dict, root: Path, pending: bool = False) -> list[str]:
    """--current's second half: the tree being gated is the tree the
    reproduction exported (see the module docstring), [] when it is.
    `pending`: the receipt is rewritten in the commit under construction, so
    only the uncommitted state (it names HEAD) can be right."""
    import reach_reproduce as RR                        # noqa: PLC0415

    res = doc.get("result") or {}
    commit, want = doc.get("commit"), res.get("export_digest")
    if not commit or not want:
        return ["the receipt records no commit or no export digest, so the tree it "
                "reproduced cannot be compared with the tree being gated"]
    out = []
    if res.get("head") != commit:
        out.append(f"the receipt names commit {str(commit)[:12]} and its result records head "
                   f"{str(res.get('head'))[:12]}; one of them is not the export's")
    head = git_text(root, "rev-parse", "--verify", "-q", "HEAD")
    if not head:
        return out + ["HEAD cannot be read, so the reproduction cannot be tied to a commit"]
    parent = git_text(root, "rev-parse", "--verify", "-q", "HEAD^")
    if pending and commit != head:
        return out + [
            f"the receipt is rewritten in the commit under construction and names "
            f"{str(commit)[:12]}, not HEAD ({head[:12]}): it is not a reproduction of the tree "
            "being committed, and no gate review could accept it. Run `metrics.py --write`, "
            "then `tools/s4/reach_reproduce.py --write`, then ship, with no edit between"]
    flagged = index_flag_problems(root)
    if flagged:
        return out + flagged
    if pending:
        # The digest reads the working tree and the commit holds the index. With
        # `ship.py --no-add`, or a subset staged by hand, the two differ, the push
        # passes, and the committed receipt fails --current at the clean commit.
        unstaged = git(root, "diff", "--name-only", "-z", "--ignore-submodules=none")
        untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard")
        left = sorted({f for f in (unstaged.stdout + b"\0" + untracked.stdout)
                       .decode(errors="replace").split("\0") if f})
        if unstaged.returncode or untracked.returncode or left:
            out.append(f"the receipt is rewritten in the commit under construction and "
                       f"{len(left)} file(s) are not staged as they stand ({left[:3]}): the "
                       "digest reads the working tree and the commit holds the index, so the "
                       "commit would not be the tree reproduced; stage everything "
                       "(`git add -A`, as ship.py does without --no-add)")
    if commit == head:
        base = head                          # written on HEAD, not committed yet
    elif parent and commit == parent:
        last = git_text(root, "log", "-1", "--format=%H", "--", RECEIPT_REL)
        if last != head:
            return out + [
                f"the reproduction was taken at {commit[:12]}, HEAD's parent, and HEAD "
                f"({head[:12]}) is not the commit that wrote its receipt (last written by "
                f"{last[:12] or 'no commit'}); a reproduction is current on the commit that "
                "carries it and on no later one"]
        # Explicit flags: `git status` follows the user's configuration, and with
        # status.showUntrackedFiles=no a file the export held and the commit
        # does not reads clean.
        status = git(root, "status", "--porcelain", "--untracked-files=all",
                     "--ignore-submodules=none")
        if status.returncode:
            return out + ["git status cannot be read, so a clean tree cannot be shown"]
        if status.stdout.strip():
            return out + [
                f"the reproduction was committed by HEAD ({head[:12]}) and the working tree "
                "has changes, so the tree being gated is not HEAD's; a gate review needs "
                "the commit alone"]
        committed = git(root, "ls-tree", "-r", "-z", "--name-only", "HEAD")
        held = {f for f in committed.stdout.decode(errors="replace").split("\0") if f}
        if committed.returncode or set(RR.export_listing(root)) != held:
            return out + ["the files an export would hold are not HEAD's files, so the tree "
                          "the digest reads is not the commit"]
        base = parent
    else:
        return out + [f"the reproduction was taken at {commit[:12]} and HEAD is {head[:12]}, "
                      "which is neither that commit nor the commit that wrote the receipt "
                      "on it"]
    excludes = res.get("digest_excludes")
    files = RR.export_listing(root)
    if excludes is None:
        # Written before the digest left out its own receipt: the export held
        # the receipt as it stood at `base`, before the write.
        before = git(root, "show", f"{base}:{RECEIPT_REL}")
        digest = RR.export_digest(root, files, exclude=(),
                                  substitute={RECEIPT_REL: before.stdout if before.returncode == 0
                                              else None})
    elif list(excludes) != [RECEIPT_REL]:
        return out + [f"the export digest leaves out {excludes!r}; only the receipt itself "
                      f"({RECEIPT_REL}) may be left out, so the rest of the tree is compared"]
    else:
        digest = RR.export_digest(root, files, exclude=(RECEIPT_REL,))
    if digest != want:
        out.append(f"the tree being gated is not the tree the reproduction exported: its "
                   f"digest is {digest[:12]} and the receipt's {str(want)[:12]}. Something "
                   "moved between the export and the commit (METRICS.md regenerated after "
                   "the export, or an edit made after it); run `metrics.py --write`, then "
                   "`tools/s4/reach_reproduce.py --write`, then commit nothing else with it")
    return out


def problems(path: Path | None = None, current: bool = False,
             live: dict | None = None, root: Path | None = None,
             pending: bool | None = None) -> list[str]:
    """What is wrong with the receipt at `path` (default: the tree's). `current`:
    the gate review's test. `pending` (default: computed for the tree's own
    receipt, off for any other path): the receipt is rewritten in the commit
    under construction, which holds it to the gate review's test on every push."""
    out: list[str] = []
    root = ROOT if root is None else root
    p = path or root / RECEIPT_REL
    if pending is None:
        pending = path is None and not current and p.exists() and receipt_pending(root)
    reach = root / REACH_REL
    if not p.exists():
        return [f"{RECEIPT_REL} does not exist. Run "
                "`.venv/bin/python tools/s4/reach_reproduce.py --write` from a clean "
                "tree: until it has succeeded once, nothing has shown that the "
                "tracked files determine the build."]
    try:
        doc = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [f"the reproduction receipt is not JSON: {e}"]
    if doc.get("id") != "s4-reach-reproduction-v1":
        out.append(f"receipt id is {doc.get('id')!r}, expected "
                   "'s4-reach-reproduction-v1'")

    res = doc.get("result") or {}
    if res.get("match") is not True:
        out.append("the last clean-checkout reproduction did not match: recorded "
                   f"{res.get('recorded')!r}, reproduced {res.get('reproduced')!r}. "
                   "The tracked tree does not determine the build.")
    if reach.exists():
        want = json.loads(reach.read_text()).get("reach", {})
        got = res.get("reproduced") or {}
        for k in ("iterations", "import_calls", "frames_presented"):
            if k in got and want.get(k) != got.get(k):
                out.append(f"the reproduction reproduced {k}={got[k]} and the reach "
                           f"receipt now records {want.get(k)}; one of them is stale")

    if current or pending:
        if live is None:
            try:
                import reach_receipt                    # noqa: PLC0415
                live = reach_receipt.manifest()
            except Exception as e:                      # noqa: BLE001
                return out + [f"the manifest could not be computed: {e!r}"]
        rec = doc.get("manifest") or {}
        for section in ("generated", "tracked"):
            if rec.get(section) != live.get(section):
                out.append(f"the reproduction is not current: its {section!r} inputs "
                           "differ from the tree's. A gate review needs a "
                           "reproduction of the state being gated.")
        out.extend(tree_problems(doc, root, pending=pending and not current))
    return out


def falsifications() -> list[str]:
    global RAN
    if not RECEIPT.exists():
        RAN = 0
        return []          # nothing recorded yet; `problems` says so plainly
    doc = json.loads(RECEIPT.read_text())

    def mutate(name, f):
        d = copy.deepcopy(doc)
        f(d)
        return name, d

    cases = [
        mutate("the reproduction recorded as not matching",
               lambda d: d["result"].__setitem__("match", False)),
        mutate("the match flag removed",
               lambda d: d["result"].pop("match", None)),
        mutate("a reproduced number changed",
               lambda d: d["result"]["reproduced"].__setitem__("iterations", 1)),
        mutate("the receipt id changed",
               lambda d: d.__setitem__("id", "something-else")),
    ]
    RAN = len(cases)
    survived = []
    with tempfile.TemporaryDirectory() as td:
        for name, d in cases:
            q = Path(td) / "r.json"
            q.write_text(json.dumps(d))
            if not problems(q):
                survived.append(name)
    return [f"falsification survived: {s} -- the gate accepted a receipt it "
            "should have rejected" for s in survived]


def current_falsifications(live: dict, root: Path | None = None,
                           path: Path | None = None) -> list[str]:
    """--current's own falsifications, run only once the record passes --current:
    each edits the recorded currency, and each must add a problem the unedited
    record does not have, or the currency half is not looking at it."""
    global CURRENT_RAN
    root = ROOT if root is None else root
    doc = json.loads((path or root / RECEIPT_REL).read_text())

    def mutate(name, f):
        d = copy.deepcopy(doc)
        f(d)
        return name, d

    cases = [
        mutate("the export digest changed",
               lambda d: d["result"].__setitem__("export_digest", "0" * 64)),
        mutate("the reproduction recorded at a commit that is neither HEAD nor its parent",
               lambda d: (d.__setitem__("commit", "0" * 40),
                          d["result"].__setitem__("head", "0" * 40))),
        mutate("the receipt's commit and its result's head disagree",
               lambda d: d["result"].__setitem__("head", "0" * 40)),
        mutate("the digest leaves out another file than its own receipt",
               lambda d: d["result"].__setitem__(
                   "digest_excludes", list(d["result"].get("digest_excludes") or [])
                   + ["METRICS.md"])),
    ]
    # Read by the other digest rule. The two readings differ only when a receipt
    # stood at the commit the receipt names; the first reproduction there ever
    # was reads the same by both, so the case is not applicable to it.
    if git(root, "cat-file", "-e", f"{doc.get('commit')}:{RECEIPT_REL}").returncode == 0:
        cases.append(mutate(
            "the digest read by the other rule (holding, or leaving out, its own receipt)",
            lambda d: d["result"].pop("digest_excludes", None)
            if d["result"].get("digest_excludes") is not None
            else d["result"].__setitem__("digest_excludes", [RECEIPT_REL])))
    CURRENT_RAN = len(cases)
    survived = []
    with tempfile.TemporaryDirectory() as td:
        q = Path(td) / "r.json"
        for name, d in cases:
            q.write_text(json.dumps(d))
            if not problems(q, current=True, live=live, root=root):
                survived.append(name)
    return [f"falsification survived: {s} -- --current accepted a receipt it should "
            "have rejected" for s in survived]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", action="store_true",
                    help="also require the reproduction to be of the state being "
                         "gated; G4's exit criteria pass this")
    a = ap.parse_args()
    failed = falsifications()
    if failed:
        for f in failed:
            print(f"  {f}")
        print(f"REACH_REPRODUCTION_FAILED falsifications={len(failed)}")
        return 1
    # A receipt rewritten in the commit under construction is held to the gate
    # review's test on every push (module docstring).
    pending = not a.current and receipt_pending(ROOT)
    live = None
    if a.current or pending:
        try:
            import reach_receipt                        # noqa: PLC0415
            live = reach_receipt.manifest()
        except Exception as e:                          # noqa: BLE001
            print(f"  the manifest could not be computed: {e!r}")
            print("REACH_REPRODUCTION_FAILED problems=1")
            return 1
    probs = problems(current=a.current, live=live, pending=pending)
    if probs:
        for p in probs:
            print(f"  {p}")
        if pending:
            print("  (the receipt is rewritten in this commit, so this push is held to the "
                  "gate review's --current test)")
        print(f"REACH_REPRODUCTION_FAILED problems={len(probs)}")
        return 1
    if a.current or pending:
        failed = current_falsifications(live)
        if failed:
            for f in failed:
                print(f"  {f}")
            print(f"REACH_REPRODUCTION_FAILED current_falsifications={len(failed)}")
            return 1
    doc = json.loads(RECEIPT.read_text())
    r = doc["result"]["reproduced"]
    print(f"REACH_REPRODUCTION_OK iterations={r['iterations']} "
          f"import_calls={r['import_calls']} frames={r['frames_presented']} "
          f"at={doc['commit'][:12]} "
          f"current={'yes' if a.current else 'yes-rewritten-now' if pending else 'not-checked'} "
          f"falsifications={RAN}"
          + (f" current_falsifications={CURRENT_RAN}" if a.current or pending else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
