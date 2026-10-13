"""Tests for Documentation/scripts/check_reach_reproduction.py --current and the
export digest tools/s4/reach_reproduce.py records.

Each test builds a throwaway git repository holding copies of the checker and
of the tool it reads the digest rules from, with a stand-in
`tools/s4/reach_receipt.py` whose manifest never moves, so what varies is only
the commit and the tree. The receipt is written the way `reach_reproduce.py
--write` writes it, by this file's own reading of the digest rule
(`reference_digest`), not by the tool's function, so the test and the code
under test do not share the rule they are checking. The checker then runs as
a subprocess, as `gate_report.py --gate G4` runs it.

The defect these hold closed (2026-09-26, understand-g4exit.md blocker B): the
tool records HEAD before the commit that carries its receipt, and --current
required `commit == HEAD`, so a reproduction passed only while its receipt was
uncommitted, and G4's report could only be generated on a DIRTY tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parents[1]
RECEIPT = "Documentation/receipts/s4-reach-reproduction-v1.json"
REACH_RECEIPT = "Documentation/receipts/s4-run-reach-v1.json"
REACH = {"iterations": 1300, "import_calls": 1186059, "frames_presented": 1335}
MANIFEST = {"pipeline": {"tools/s4/reach_receipt.py": "0" * 64},
            "generated": {"edges": "1" * 64}, "tracked": {"runtime/a.c": "2" * 64}}
STUB = '''import json
from pathlib import Path


def manifest():
    return json.loads((Path(__file__).resolve().parents[2] / "private" / "manifest.json")
                      .read_text())
'''


def env() -> dict[str, str]:
    e = dict(os.environ)
    e.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
              "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
              "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    return e


def git(repo: Path, *args: str) -> str:
    return subprocess.run(("git", "-C", str(repo)) + args, env=env(), capture_output=True,
                          text=True, check=True).stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def listing(repo: Path) -> list[str]:
    out = git(repo, "ls-files", "-z") + "\0" + git(repo, "ls-files", "-z", "--others",
                                                    "--exclude-standard")
    return sorted({f for f in out.split("\0") if f})


def reference_digest(repo: Path, exclude: tuple[str, ...] = (RECEIPT,)) -> str:
    """The export digest as reach_reproduce.py's docstring states it: sha256 over
    each listed file's path and content sha256, less `exclude`."""
    h = hashlib.sha256()
    for rel in listing(repo):
        if rel in exclude or not (repo / rel).is_file():
            continue
        h.update(rel.encode() + b"\0" + hashlib.sha256((repo / rel).read_bytes()).digest())
    return h.hexdigest()


def write_receipt(repo: Path, legacy: bool = False, excludes: tuple[str, ...] = (RECEIPT,)) -> str:
    """The receipt `reach_reproduce.py --write` writes, on the tree as it stands.
    `legacy`: as the tool wrote it before 2026-09-26, the digest holding the
    receipt's own bytes as they stood before the write, and no digest_excludes."""
    head = git(repo, "rev-parse", "HEAD")
    digest = reference_digest(repo, exclude=() if legacy else excludes)
    result = {"head": head, "uncommitted": bool(git(repo, "status", "--porcelain")),
              "export_digest": digest, "exported_files": len(listing(repo)),
              "recorded": REACH, "reproduced": REACH, "match": True}
    if not legacy:
        result["digest_excludes"] = list(excludes)
    write(repo, RECEIPT, json.dumps({
        "schema": "sa2-recomp-receipt/v1", "id": "s4-reach-reproduction-v1",
        "generated_by": "tools/s4/reach_reproduce.py", "commit": head,
        "inputs": {"pipeline": MANIFEST["pipeline"]}, "manifest": MANIFEST,
        "result": result}, indent=2, sort_keys=True) + "\n")
    return head


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    for rel in ("Documentation/scripts/check_reach_reproduction.py", "tools/s4/reach_reproduce.py",
                "tools/s3/whole_image/common.py"):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, r / rel)
    write(r, "tools/s4/reach_receipt.py", STUB)
    write(r, "private/manifest.json", json.dumps(MANIFEST))
    write(r, ".gitignore", "/private\n__pycache__/\n")
    write(r, REACH_RECEIPT, json.dumps({"reach": REACH}))
    write(r, "METRICS.md", "# Metrics\n\nM1 12683/12683\n")
    write(r, "runtime/a.c", "int a;\n")
    commit(r, "P0")
    return r


def check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "Documentation/scripts/check_reach_reproduction.py",
                           "--current"], cwd=repo, env=env(), capture_output=True, text=True)


def ok(done: subprocess.CompletedProcess) -> bool:
    return done.returncode == 0 and "current=yes" in done.stdout


# accepted -------------------------------------------------------------------------------

def test_a_receipt_committed_by_head_is_current(repo: Path):
    """The G4 flow: edit, reproduce, commit the receipt with the edit. HEAD's parent
    is the commit the receipt names, and HEAD wrote it."""
    write(repo, "runtime/a.c", "int a = 1;\n")
    parent = write_receipt(repo)
    commit(repo, "C: the edit and its reproduction")
    assert git(repo, "rev-parse", "HEAD^") == parent
    done = check(repo)
    assert ok(done), done.stdout + done.stderr
    # no receipt stood at P0, so the other-rule case does not apply
    assert "current_falsifications=4" in done.stdout


def test_a_second_reproduction_runs_every_current_falsification(repo: Path):
    write_receipt(repo)
    commit(repo, "P1: a first reproduction")
    write(repo, "runtime/a.c", "int a = 7;\n")
    write_receipt(repo)
    commit(repo, "C")
    done = check(repo)
    assert ok(done), done.stdout + done.stderr
    assert "current_falsifications=5" in done.stdout


def test_a_legacy_receipt_committed_by_head_is_current(repo: Path):
    """A receipt written before the digest left out its own receipt is read back with
    the receipt's bytes at the commit it names, before the write."""
    write_receipt(repo, legacy=True)                   # the first one, over no receipt
    commit(repo, "P1: a first reproduction")
    write(repo, "runtime/a.c", "int a = 2;\n")
    write_receipt(repo, legacy=True)                   # over P1's receipt
    commit(repo, "C: the edit and its reproduction")
    done = check(repo)
    assert ok(done), done.stdout + done.stderr


def test_an_uncommitted_receipt_is_current(repo: Path):
    write(repo, "runtime/a.c", "int a = 3;\n")
    write_receipt(repo)
    done = check(repo)
    assert ok(done), done.stdout + done.stderr


# refused --------------------------------------------------------------------------------

def test_a_commit_after_the_one_that_wrote_the_receipt_is_not_current(repo: Path):
    write_receipt(repo)
    commit(repo, "C")
    write(repo, "runtime/a.c", "int a = 4;\n")
    commit(repo, "D: a later change")
    done = check(repo)
    assert done.returncode == 1
    assert "which is neither that commit nor the commit that wrote the receipt" in done.stdout


def test_head_must_be_the_commit_that_wrote_the_receipt(repo: Path):
    """HEAD's parent is the commit the receipt names, and the tree is the export's,
    but the receipt came in through a merge's other parent: HEAD did not write it."""
    parent = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    write_receipt(repo)
    commit(repo, "W: the receipt, on a side branch")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "M", "side")
    assert git(repo, "rev-parse", "HEAD^") == parent
    done = check(repo)
    assert done.returncode == 1
    assert "is not the commit that wrote its receipt" in done.stdout


def test_metrics_regenerated_after_the_export_is_not_current(repo: Path):
    """development_snapshot's case: ship.py rewrote METRICS.md between the export and the commit."""
    write(repo, "runtime/a.c", "int a = 5;\n")
    write_receipt(repo)
    write(repo, "METRICS.md", "# Metrics\n\nM1 12683/12683 (regenerated)\n")
    commit(repo, "C")
    done = check(repo)
    assert done.returncode == 1
    assert "is not the tree the reproduction exported" in done.stdout


def test_an_uncommitted_receipt_with_a_later_edit_is_not_current(repo: Path):
    write_receipt(repo)
    write(repo, "runtime/a.c", "int a = 6;\n")
    done = check(repo)
    assert done.returncode == 1
    assert "is not the tree the reproduction exported" in done.stdout


def test_a_dirty_tree_is_not_current_for_a_committed_receipt(repo: Path):
    """A change the digest cannot see (a file's mode) still leaves the gated tree
    other than HEAD's, so a committed receipt needs a clean tree."""
    write_receipt(repo)
    commit(repo, "C")
    (repo / "runtime" / "a.c").chmod(0o755)
    assert git(repo, "status", "--porcelain")
    done = check(repo)
    assert done.returncode == 1
    assert "the working tree has changes" in done.stdout


def test_the_digest_may_leave_out_only_its_own_receipt(repo: Path):
    write_receipt(repo, excludes=(RECEIPT, "METRICS.md"))
    commit(repo, "C")
    done = check(repo)
    assert done.returncode == 1
    assert "only the receipt itself" in done.stdout


def test_the_receipt_and_its_result_must_name_one_commit(repo: Path):
    write_receipt(repo)
    doc = json.loads((repo / RECEIPT).read_text())
    doc["result"]["head"] = "f" * 40
    write(repo, RECEIPT, json.dumps(doc))
    commit(repo, "C")
    done = check(repo)
    assert done.returncode == 1
    assert "one of them is not the export's" in done.stdout


# the tool's digest ------------------------------------------------------------------------

def tool(repo: Path):
    sys.path.insert(0, str(REPO / "tools" / "s3" / "whole_image"))
    sys.path.insert(0, str(REPO / "tools" / "s4"))
    import reach_reproduce as RR
    return RR


def test_the_tool_s_digest_is_the_stated_rule_and_leaves_out_only_its_receipt(repo: Path):
    RR = tool(repo)
    assert RR.DIGEST_EXCLUDES == (RECEIPT,)
    write_receipt(repo)
    before = RR.export_digest(repo, RR.export_listing(repo))
    assert before == reference_digest(repo)
    write(repo, RECEIPT, "{}\n")                       # the receipt rewritten: no change
    assert RR.export_digest(repo, RR.export_listing(repo)) == before
    write(repo, "notes.md", "an untracked file a `git add -A` would commit\n")
    assert RR.export_digest(repo, RR.export_listing(repo)) != before


def test_write_is_refused_while_metrics_would_be_regenerated(repo: Path):
    RR = tool(repo)
    fake = repo / "Documentation" / "scripts" / "metrics.py"
    fake.write_text("import sys\nsys.exit(0)\n")
    assert RR.metrics_refusal(repo, sys.executable) is None
    fake.write_text("import sys\nprint('METRICS.md differs from the regenerated metrics')\n"
                    "sys.exit(1)\n")
    why = RR.metrics_refusal(repo, sys.executable)
    assert why is not None and "metrics.py --write" in why


# review of 2026-09-26: a clean status that is not HEAD's tree -----------------------------

def test_hidden_untracked_file_is_not_a_clean_tree(repo: Path):
    """status.showUntrackedFiles=no: an untracked source file rides in the export,
    the commit leaves it out, and a plain `git status` reads clean."""
    write(repo, "runtime/extra.c", "int needed_by_the_build;\n")
    write(repo, "runtime/a.c", "int a = 11;\n")
    parent = write_receipt(repo)
    git(repo, "add", "runtime/a.c", RECEIPT)
    git(repo, "commit", "-q", "-m", "C: the receipt without runtime/extra.c")
    git(repo, "config", "status.showUntrackedFiles", "no")
    assert git(repo, "rev-parse", "HEAD^") == parent
    assert git(repo, "status", "--porcelain") == ""
    done = check(repo)
    assert not ok(done) and "the working tree has changes" in done.stdout, done.stdout


def test_a_skip_worktree_file_is_not_head_s_tree(repo: Path):
    """A tracked file marked skip-worktree is edited before the export; `git add -A`
    and `git status` pass over it, so HEAD keeps the old bytes and the digest reads
    the new ones."""
    git(repo, "update-index", "--skip-worktree", "runtime/a.c")
    write(repo, "runtime/a.c", "int a = 12; /* only in the export */\n")
    write_receipt(repo)
    commit(repo, "C")
    assert git(repo, "show", "HEAD:runtime/a.c") == "int a;"
    done = check(repo)
    assert not ok(done) and "skip-worktree" in done.stdout, done.stdout


def test_an_assume_unchanged_file_is_not_head_s_tree(repo: Path):
    write_receipt(repo)
    commit(repo, "C")
    git(repo, "update-index", "--assume-unchanged", "runtime/a.c")
    done = check(repo)
    assert not ok(done) and "assume-unchanged" in done.stdout, done.stdout


# review of 2026-09-26: the closing order, enforced on the push ----------------------------

def check_push(repo: Path) -> subprocess.CompletedProcess:
    """The per-push gate, as run_all_gates.py runs it (no --current)."""
    return subprocess.run([sys.executable, "Documentation/scripts/check_reach_reproduction.py"],
                          cwd=repo, env=env(), capture_output=True, text=True)


def test_push_holds_a_rewritten_receipt_to_the_current_test(repo: Path):
    """The order followed: every edit, then the reproduction, then the commit with
    nothing else. The push passes with --current's falsifications run, and the
    commit then passes --current at a clean HEAD."""
    write(repo, "runtime/a.c", "int a = 22;\n")
    write_receipt(repo)
    git(repo, "add", "-A")                              # as ship.py stages before the gates
    done = check_push(repo)
    assert done.returncode == 0, done.stdout
    # no receipt stood at P0, so the other-rule case does not apply: four run
    assert "current=yes-rewritten-now" in done.stdout and "current_falsifications=4" in done.stdout
    commit(repo, "C")
    done = check_push(repo)
    assert done.returncode == 0 and "current=not-checked" in done.stdout, done.stdout
    assert ok(check(repo))


def test_push_refuses_a_receipt_whose_tree_moved_after_the_export(repo: Path):
    """development_snapshot's and development_snapshot's case: METRICS.md regenerated after the export."""
    write(repo, "runtime/a.c", "int a = 21;\n")
    write_receipt(repo)
    write(repo, "METRICS.md", "# Metrics\n\nregenerated after the export\n")
    git(repo, "add", "-A")
    done = check_push(repo)
    assert done.returncode == 1 and "is not the tree the reproduction exported" in done.stdout
    assert "rewritten in this commit" in done.stdout


def test_push_refuses_an_unstaged_rewrite_too(repo: Path):
    """Pending is read from the bytes: a rewrite not yet staged counts."""
    write_receipt(repo)
    write(repo, "runtime/a.c", "int a = 24;\n")
    done = check_push(repo)
    assert done.returncode == 1 and "is not the tree the reproduction exported" in done.stdout


def test_push_refuses_an_old_receipt_committed_later(repo: Path):
    write_receipt(repo)
    commit(repo, "P1: a receipt")
    write(repo, "runtime/a.c", "int a = 23;\n")
    commit(repo, "P2")
    doc = json.loads((repo / RECEIPT).read_text())
    doc["note"] = "touched"
    write(repo, RECEIPT, json.dumps(doc))                # names P1, rewritten now
    done = check_push(repo)
    assert done.returncode == 1 and "not HEAD" in done.stdout, done.stdout


def test_push_refuses_a_rewritten_receipt_over_a_flagged_index(repo: Path):
    git(repo, "update-index", "--skip-worktree", "runtime/a.c")
    write(repo, "runtime/a.c", "int a = 25; /* the commit will not hold this */\n")
    write_receipt(repo)
    done = check_push(repo)
    assert done.returncode == 1 and "skip-worktree" in done.stdout, done.stdout


def test_push_does_not_hold_a_receipt_it_does_not_rewrite(repo: Path):
    """The cheap half is unchanged for every other push: a committed receipt behind
    HEAD passes, as it must, or no push could follow the one that carried it."""
    write_receipt(repo)
    commit(repo, "C")
    write(repo, "runtime/a.c", "int a = 26;\n")
    commit(repo, "D")
    write(repo, "runtime/a.c", "int a = 27;\n")
    done = check_push(repo)
    assert done.returncode == 0 and "current=not-checked" in done.stdout, done.stdout


def test_push_sees_a_rewrite_git_status_is_told_to_hide(repo: Path):
    """The receipt itself marked assume-unchanged: `git status` reads it clean,
    and the push still holds the rewrite, because pending is read from bytes."""
    write_receipt(repo)
    commit(repo, "C")
    git(repo, "update-index", "--assume-unchanged", RECEIPT)
    write(repo, "runtime/a.c", "int a = 28;\n")
    commit(repo, "D")
    doc = json.loads((repo / RECEIPT).read_text())
    doc["note"] = "rewritten behind an index flag"
    write(repo, RECEIPT, json.dumps(doc))
    assert git(repo, "status", "--porcelain") == ""
    done = check_push(repo)
    assert done.returncode == 1 and "rewritten in this commit" in done.stdout, done.stdout


def test_push_refuses_a_rewritten_receipt_whose_generated_inputs_moved(repo: Path):
    """The manifest half too: a generated input regenerated after the reproduction."""
    write_receipt(repo)
    moved = dict(MANIFEST, generated={"edges": "9" * 64})
    write(repo, "private/manifest.json", json.dumps(moved))
    done = check_push(repo)
    assert done.returncode == 1 and "its 'generated' inputs differ" in done.stdout, done.stdout
