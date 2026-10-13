#!/usr/bin/env python3
"""tools/rmtree.py: the delete by literal path that needs no permission prompt.

Every case builds its own repository and harness temporary directory under
tmp_path and passes them to `rmtree.remove`, so nothing here can reach the real
private/ tree or a real scratchpad. The one subprocess case is a refusal, which
removes nothing.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[3] / "tools" / "rmtree.py"


@pytest.fixture
def rmtree(monkeypatch):
    spec = importlib.util.spec_from_file_location("rmtree_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "rmtree_under_test", module)   # dataclasses look here
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    scratch = repo / "private" / "generated" / "unit" / "scratch"
    (scratch / "deep").mkdir(parents=True)
    (scratch / "deep" / "a.o").write_text("x")
    exe = repo / "private" / "exe"
    exe.mkdir(parents=True)
    (exe / "sonic2app.exe").write_text("game")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    escape = repo / "private" / "generated" / "unit" / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    (scratch / "link-out").symlink_to(outside, target_is_directory=True)
    temp = tmp_path / "tmp" / "claude-1000"
    pad = temp / "-workspace-OpenSA2" / "session" / "scratchpad"
    (pad / "work").mkdir(parents=True)
    tasks = temp / "-workspace-OpenSA2" / "session" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "a1.output").write_text("{}")
    return {"repo": repo, "scratch": scratch, "exe": exe, "outside": outside, "escape": escape,
            "temp": temp, "pad": pad, "tasks": tasks}


def remove(rmtree, tree, *paths: Path | str) -> tuple[int, str]:
    out = io.StringIO()
    code = rmtree.remove([str(p) for p in paths], tree["repo"], tree["temp"], out=out)
    return code, out.getvalue()


def test_a_directory_under_private_is_removed_and_named(rmtree, tree) -> None:
    code, out = remove(rmtree, tree, tree["scratch"])
    assert code == 0
    assert not tree["scratch"].exists()
    assert out == f"RMTREE_REMOVED directory {tree['scratch']}\n"
    # rmtree unlinked the link inside and did not follow it.
    assert (tree["outside"] / "keep.txt").read_text() == "keep"


def test_a_scratchpad_path_is_removed_and_the_harness_s_own_files_are_not(rmtree, tree,
                                                                         capsys) -> None:
    code, out = remove(rmtree, tree, tree["pad"] / "work")
    assert code == 0 and not (tree["pad"] / "work").exists()
    for refused in (tree["pad"], tree["tasks"] / "a1.output", tree["temp"]):
        assert remove(rmtree, tree, refused)[0] == 2
        assert refused.exists()
    assert "outside the allowed roots" in capsys.readouterr().err


@pytest.mark.parametrize("relative", ["outside/keep.txt", "repo/private",
                                      "repo/private/generated", "repo/private/exe/sonic2app.exe",
                                      "repo/private/generated/../exe/sonic2app.exe",
                                      "repo/private/generated/unit/../../../outside",
                                      "repo/Documentation"])
def test_paths_outside_the_roots_are_refused(rmtree, tree, tmp_path: Path, relative: str,
                                             capsys) -> None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    code, out = remove(rmtree, tree, target)
    assert code == 2 and out == ""
    assert target.exists() == existed
    assert "RMTREE_REFUSED nothing was removed" in capsys.readouterr().err


def test_a_path_through_a_symlink_out_of_the_root_is_refused(rmtree, tree, capsys) -> None:
    code, _ = remove(rmtree, tree, tree["escape"] / "keep.txt")
    assert code == 2
    assert (tree["outside"] / "keep.txt").read_text() == "keep"
    assert "a symlink on the way leads out of" in capsys.readouterr().err


def clone_in(tree, name: str) -> Path:
    """A unit's clone inside the repository's private/generated, as units make them."""
    clone = tree["repo"] / "private" / "generated" / "s5" / name / "clone"
    clone.mkdir(parents=True)
    return clone


def test_a_clone_s_link_to_a_protected_input_is_refused(rmtree, tree, capsys) -> None:
    # The clone's private/exe is a link to the repository's: by its spelling the path is
    # scratch four levels down private/generated, by where it leads the shipped executable.
    clone = clone_in(tree, "unit")
    (clone / "private").mkdir()
    (clone / "private" / "exe").symlink_to(tree["exe"], target_is_directory=True)
    target = clone / "private" / "exe" / "sonic2app.exe"
    code, out = remove(rmtree, tree, target)
    assert (code, out) == (2, "")
    assert (tree["exe"] / "sonic2app.exe").read_text() == "game"
    err = capsys.readouterr().err
    assert f"it leads to {tree['exe'] / 'sonic2app.exe'} once its symlinks are resolved" in err


@pytest.mark.parametrize("relative", ["generated", "exe", "exe/sonic2app.exe", "install"])
def test_a_clone_whose_private_is_a_link_cannot_reach_the_repository_s_private(
        rmtree, tree, relative: str) -> None:
    # i1-final-review/clone/private is a link to the whole private/: through it the
    # repository's private/generated is one level down, and its inputs are protected.
    clone = clone_in(tree, "review")
    (clone / "private").symlink_to(tree["repo"] / "private", target_is_directory=True)
    (tree["repo"] / "private" / "install").mkdir()
    code, out = remove(rmtree, tree, clone / "private" / relative)
    assert (code, out) == (2, "")
    assert (tree["repo"] / "private" / "generated" / "unit" / "scratch" / "deep" / "a.o").exists()
    assert (tree["exe"] / "sonic2app.exe").exists()
    assert (tree["repo"] / "private" / "install").is_dir()


def test_a_repository_whose_own_private_is_a_link_removes_its_scratch(rmtree, tmp_path,
                                                                      tree) -> None:
    # Run from such a clone, its private/ is where the link leads, so its scratch there
    # is its own; the protected inputs stay protected by where they are.
    clone = clone_in(tree, "linked")
    (clone / "private").symlink_to(tree["repo"] / "private", target_is_directory=True)
    out = io.StringIO()
    scratch = clone / "private" / "generated" / "unit" / "scratch"
    assert rmtree.remove([str(scratch)], clone, tree["temp"], out=out) == 0
    assert not tree["scratch"].exists()
    assert rmtree.remove([str(clone / "private" / "exe" / "sonic2app.exe")], clone,
                         tree["temp"], out=out) == 2
    assert (tree["exe"] / "sonic2app.exe").exists()


def test_a_link_from_one_root_into_another_is_refused(rmtree, tree) -> None:
    (tree["scratch"] / "to-pad").symlink_to(tree["pad"], target_is_directory=True)
    (tree["pad"] / "work" / "keep").write_text("keep")
    assert remove(rmtree, tree, tree["scratch"] / "to-pad" / "work")[0] == 2
    assert (tree["pad"] / "work" / "keep").read_text() == "keep"


def test_a_symlink_itself_is_unlinked_and_its_target_left(rmtree, tree) -> None:
    code, out = remove(rmtree, tree, tree["escape"])
    assert code == 0 and not tree["escape"].is_symlink()
    assert out.startswith(f"RMTREE_REMOVED symlink {tree['escape']}")
    assert (tree["outside"] / "keep.txt").read_text() == "keep"


def test_one_refusal_removes_nothing(rmtree, tree) -> None:
    code, out = remove(rmtree, tree, tree["scratch"], tree["outside"])
    assert code == 2 and out == ""
    assert tree["scratch"].is_dir() and tree["outside"].is_dir()


def test_an_absent_path_passes_so_the_command_can_be_run_twice(rmtree, tree) -> None:
    assert remove(rmtree, tree, tree["scratch"])[0] == 0
    code, out = remove(rmtree, tree, tree["scratch"])
    assert code == 0 and out == f"RMTREE_ABSENT {tree['scratch']}: nothing to remove\n"


def test_the_command_line_refuses_and_says_so(tmp_path: Path) -> None:
    keep = tmp_path / "keep.txt"
    keep.write_text("keep")
    result = subprocess.run([sys.executable, str(TOOL), str(keep)], capture_output=True,
                            text=True, check=False, timeout=60)
    assert result.returncode == 2 and keep.exists()
    assert "RMTREE_REFUSED" in result.stderr
    empty = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True,
                           check=False, timeout=60)
    assert empty.returncode == 2 and "no path given" in empty.stderr
