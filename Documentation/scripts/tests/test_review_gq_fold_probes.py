"""Review of fold:gq, probes. Each test states what the gate should do and fails on
the fold as delivered. Copied into a tree's Documentation/scripts/tests/ to run."""
from __future__ import annotations

import json
import subprocess
import sys

from test_replay_regression import (MET_BOOT, USER, RR, commit, git as rgit, reading,  # noqa: F401
                                    refresh, registry, repo, run, write)
import test_reach_reproduction as TR

NOT_YET = ("Date: 2026-10-01. Stage S5. **Status: Proposed, for the user's ruling; not yet "
           "adopted by the user.**\n\n")


def _retire_boot(repo, capsys, text):
    write(repo, RR.REGISTRY_REL, registry("boot", "metalharbor"))
    mh = reading("metalharbor", tick=1, judged=121, ticks=15585, goal=True, trace=121)
    assert refresh(repo, {"boot": MET_BOOT, "metalharbor": mh}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/d.md", text)
    write(repo, RR.REGISTRY_REL, registry(
        "metalharbor", retired=[{"name": "boot", "decision": "Documentation/decisions/d.md"}]))
    return run(repo, {"metalharbor": mh}, capsys=capsys)


def test_probe_a_proposal_not_yet_adopted_by_the_user_retires_nothing(repo, capsys):
    code, out = _retire_boot(repo, capsys, NOT_YET + "Retires replay `boot`.\n")
    assert code == 1 and "DROPPED boot" in out, out


def test_probe_a_negated_sentence_retires_nothing(repo, capsys):
    code, out = _retire_boot(repo, capsys,
                             USER + "Nothing in this ruling retires replay `boot`; it stays.\n")
    assert code == 1 and "DROPPED boot" in out, out


def test_probe_an_acceptance_names_whether_the_reading_meets_m5(repo, capsys):
    """Same M5 part, same window, same words: only the identity rule failed. The
    sentence written for the met reading must not accept the unmet one."""
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/user.md", USER + RR.accepts("boot", MET_BOOT) + ".\n")
    code, out = refresh(repo, {"boot": dict(MET_BOOT, m5_met=False)}, capsys,
                        "--accept-regression", "Documentation/decisions/user.md")
    assert code == 1, out


def _reach_repo(tmp_path):
    """test_reach_reproduction.py's `repo` fixture body."""
    import shutil
    r = tmp_path / "repo"
    r.mkdir()
    TR.git(r, "init", "-q", "-b", "main")
    for rel in ("Documentation/scripts/check_reach_reproduction.py", "tools/s4/reach_reproduce.py",
                "tools/s3/whole_image/common.py"):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TR.REPO / rel, r / rel)
    TR.write(r, "tools/s4/reach_receipt.py", TR.STUB)
    TR.write(r, "private/manifest.json", json.dumps(TR.MANIFEST))
    TR.write(r, ".gitignore", "/private\n__pycache__/\n")
    TR.write(r, TR.REACH_RECEIPT, json.dumps({"reach": TR.REACH}))
    TR.write(r, "METRICS.md", "# Metrics\n\nM1 12683/12683\n")
    TR.write(r, "runtime/a.c", "int a;\n")
    TR.commit(r, "P0")
    return r


def test_probe_push_refuses_when_the_commit_would_not_hold_the_exported_tree(tmp_path):
    """ship.py --no-add (or anything staged by hand): the digest reads the working
    tree, the commit holds the index. The push must refuse, because the committed
    receipt then fails --current at the clean commit."""
    r = _reach_repo(tmp_path)
    TR.write(r, "runtime/a.c", "int a = 30;\n")      # an edit the export holds
    TR.write_receipt(r)
    TR.git(r, "add", TR.RECEIPT)                        # staged: the receipt only
    push = TR.check_push(r)
    TR.git(r, "commit", "-q", "-m", "C")
    TR.git(r, "stash", "-q")                            # the commit alone
    review = TR.check(r)
    assert review.returncode == 1, review.stdout        # the gate review refuses it...
    assert push.returncode == 1, push.stdout            # ...so the push must have


def test_probe_a_sentence_in_a_fence_or_comment_retires_nothing(repo, capsys):
    code, out = _retire_boot(repo, capsys, USER + "An example, not a ruling:\n\n```\nRetires "
                             "replay `boot`.\n```\n\n<!--\nRetires replay `boot`.\n-->\n")
    assert code == 1 and "DROPPED boot" in out, out


def test_probe_the_s_sentence_as_written_still_retires(repo, capsys):
    """The fix refuses nothing a real decision would write."""
    code, out = _retire_boot(repo, capsys, USER + "> retire boot\n\nRetires replay `boot`.\n")
    assert code == 0, out
