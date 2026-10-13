"""Documentation/scripts/ship_subset.py: what it agrees to ship. The worktree,
gates and push are exercised by every use; these pin the refusals that keep a
subset exactly what was named."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))

import ship_subset as SS  # noqa: E402


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.py").write_text("x\n")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "cap.csv").write_text("y\n")
    return tmp_path


def test_named_files_ship_as_repository_paths(tree):
    assert SS.plan(["a/one.py"], [], tree) == ["a/one.py"]


@pytest.mark.parametrize("paths, deletions, why", [
    (["a"], [], "is not a file"),
    (["a/missing.py"], [], "is not a file"),
    (["private/cap.csv"], [], "never committed"),
    (["../elsewhere.py"], [], "outside the repository"),
    ([], ["a/one.py"], "but it exists in the tree"),
    (["a/one.py", "a/one.py"], [], "named twice"),
])
def test_anything_but_a_named_file_is_refused(tree, paths, deletions, why):
    with pytest.raises(SystemExit, match=why):
        SS.plan(paths, deletions, tree)


def test_identical_files_take_the_main_trees_time_and_changed_ones_keep_theirs(tmp_path, monkeypatch):
    import os
    main, wt = tmp_path / "main", tmp_path / "wt"
    for d in (main, wt):
        (d / "t").mkdir(parents=True)
    (main / "t" / "same.c").write_text("int x;\n")
    (wt / "t" / "same.c").write_text("int x;\n")
    (main / "t" / "new.c").write_text("int y;\n")
    (wt / "t" / "new.c").write_text("int z;\n")
    os.utime(main / "t" / "same.c", (1_000_000, 1_000_000))
    os.utime(main / "t" / "new.c", (1_000_000, 1_000_000))
    monkeypatch.setattr(SS, "git", lambda *a, **k: "t/same.c\0t/new.c\0")
    assert SS.same_mtimes(wt, main) == 1
    assert (wt / "t" / "same.c").stat().st_mtime == 1_000_000
    assert (wt / "t" / "new.c").stat().st_mtime != 1_000_000


def test_a_main_tree_file_that_differs_is_made_newer_than_the_worktree_built(tmp_path, monkeypatch):
    import os
    main, wt = tmp_path / "main", tmp_path / "wt"
    for d in (main, wt):
        (d / "r").mkdir(parents=True)
    (main / "r" / "a.c").write_text("int a = 2;\n")      # the main tree moved on
    (wt / "r" / "a.c").write_text("int a = 1;\n")        # the worktree built this
    (main / "r" / "b.c").write_text("int b;\n")
    (wt / "r" / "b.c").write_text("int b;\n")
    for f in (main / "r" / "a.c", main / "r" / "b.c"):
        os.utime(f, (1_000_000, 1_000_000))
    monkeypatch.setattr(SS, "git", lambda *a, **k: "r/a.c\0r/b.c\0")
    assert SS.touch_differing(wt, main) == 1
    assert (main / "r" / "a.c").stat().st_mtime > 1_000_000
    assert (main / "r" / "b.c").stat().st_mtime == 1_000_000


def test_a_deletion_is_a_file_the_tree_no_longer_has(tree):
    assert SS.plan([], ["a/gone.py"], tree) == ["a/gone.py"]


def test_the_session_ledger_the_ship_recorded_is_copied_back(tmp_path):
    """Review F3 of the transcript count's fold: ship.py records the session ledger in the
    worktree, so after the reset the main tree's copy is older than the commit's and reads
    as a change reverting it; it is copied back, unless the main tree's copy changed while
    the ship ran."""
    main, wt = tmp_path / "main", tmp_path / "wt"
    for d in (main, wt):
        (d / "Documentation").mkdir(parents=True)
    ledger = "Documentation/agent-sessions.json"
    (main / ledger).write_text('{"recorded_at": "before"}\n')
    before = SS.ledger_bytes(main)
    (wt / ledger).write_text('{"recorded_at": "the ship"}\n')
    assert SS.copy_ledger_back(wt, before, main) == "copied back"
    assert (main / ledger).read_text() == '{"recorded_at": "the ship"}\n'

    (main / ledger).write_text('{"recorded_at": "recorded meanwhile"}\n')
    (wt / ledger).write_text('{"recorded_at": "the next ship"}\n')
    assert "changed in the main tree while the ship ran" in SS.copy_ledger_back(wt, before, main)
    assert (main / ledger).read_text() == '{"recorded_at": "recorded meanwhile"}\n'
