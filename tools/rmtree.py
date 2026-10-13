#!/usr/bin/env python3
"""Delete scratch by literal path, inside the private tree or a session scratchpad only.

Why this exists
---------------

A Bash command that runs `rm` on a path built from a shell variable or a glob
(`rm -rf $S`, `rm -rf $X/$v/out`, `rm -f $S/*`, `rm $B/$d/$l`), whatever its
options, can raise a permission prompt in the harness, bypass mode included,
because the path could expand to anything. An unattended unit then waits on the
prompt until a person sees it;
the waits inside S5's recorded units are listed, with the commands that raised
them, in `agents-journals/s5/2026-10-05-units-idle-gaps.md`. A line in every
brief did not stop it: one came from a unit briefed against it. The user's ruling of
2026-10-05 (`Documentation/decisions/2026-10-05-S5-user-rulings-3.md`, item 2)
asked for the cause to be fixed. `tools/hooks/block_variable_rm.py` refuses the
command shape before it can prompt and names this tool; this tool is the delete
that needs no prompt because it cannot reach outside the places scratch lives.

What it removes, and what it refuses
------------------------------------

Each argument is a path, taken literally: this tool expands nothing. A path is
removed only when all of these hold, and otherwise the whole call is refused
before anything is removed:

- it lies inside an allowed root, lexically (made absolute, `..` folded, nothing
  resolved): the repository's `private/` (at least two levels down, and not under
  one of PROTECTED, the inputs no command regenerates: the executable, the
  installation, the Proton prefix, the fixture corpus, the evidence packets, the
  native captures and the predecessor's tools), or a session scratchpad,
  `<tmp>/claude-<uid>/<key>/<session>/scratchpad/...`, below the scratchpad
  directory itself;
- where it really is, its parent with every symlink resolved and its own name
  (the entry the delete reaches; a link at the end is unlinked, not followed),
  passes the same test against the roots resolved: inside one of them, two levels
  down in `private/`, and not under PROTECTED. A unit's clone links its own
  `private/exe`, `private/fixtures` or the whole `private/` to the main tree's,
  so a path that is scratch by its spelling can be the main tree's executable or
  its whole `private/generated` by where it leads; the spelling alone decides
  nothing;
- its resolved parent still lies inside the root its spelling names, also
  resolved: a link on the way cannot carry the delete into another root;
- it is not the root itself.

A directory is removed with `shutil.rmtree`, which unlinks the symlinks inside it
and never follows them; a file is unlinked; a path that is itself a symlink is
unlinked as a link, and its target is left alone. A path that does not exist is
reported and passes, so running the same command twice changes nothing. Every
removal prints a line naming what it removed.

Usage:
  .venv/bin/python tools/rmtree.py <path> [<path> ...]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# private/'s top-level entries no command regenerates (AGENTS.md, "Locations").
PROTECTED = frozenset({"exe", "install", "proton-prefix", "fixtures", "evidence-packets",
                       "predecessor-native-run", "predecessor-tools"})


@dataclass(frozen=True)
class Root:
    """An allowed root: where scratch may be removed, and how deep a path must lie."""
    path: Path
    kind: str           # "private" or "scratchpad"


def absolute(path: Path) -> Path:
    """`path` made absolute with `..` and `.` folded, no symlink resolved."""
    return Path(os.path.normpath(os.path.abspath(path)))


def temp_root() -> Path:
    """The harness's temporary directory, `<tmp>/claude-<uid>`."""
    return Path(tempfile.gettempdir()) / f"claude-{os.getuid()}"


def root_of(path: Path, private: Path, temp: Path) -> Root | None:
    """The allowed root `path` lies in, or None (the module docstring): `private` is the
    repository's `private/` and `temp` the harness's temporary directory, both spelled as
    `path` is (all lexical, or all resolved)."""
    if private in path.parents:
        parts = path.relative_to(private).parts
        if len(parts) >= 2 and parts[0] not in PROTECTED:
            return Root(private, "private")
        return None
    if temp in path.parents:
        parts = path.relative_to(temp).parts
        if len(parts) >= 4 and parts[2] == "scratchpad":
            return Root(temp.joinpath(*parts[:3]), "scratchpad")
    return None


def refusal(given: str, repository: Path, temp: Path) -> str | None:
    """Why `given` may not be removed, or None when it may (the module docstring)."""
    if not given:
        return "an empty path"
    path = absolute(Path(given))
    allowed = (f"{absolute(repository / 'private')}/<dir>/..., not under "
               f"{', '.join(sorted(PROTECTED))}; or {absolute(temp)}/<key>/<session>/scratchpad/...")
    root = root_of(path, absolute(repository / "private"), absolute(temp))
    if root is None:
        return f"{given}: outside the allowed roots ({allowed})"
    try:
        parent = path.parent.resolve(strict=False)
        anchor = root.path.resolve(strict=False)
        private = (repository / "private").resolve(strict=False)
        real_temp = temp.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        return f"{given}: its parent cannot be resolved ({error})"
    real = parent / path.name
    if root_of(real, private, real_temp) is None:
        return (f"{given}: it leads to {real} once its symlinks are resolved, which is outside "
                f"the allowed roots ({allowed}): a symlink on the way leads out of them or into "
                "a protected input")
    if parent != anchor and anchor not in parent.parents:
        return (f"{given}: its parent resolves to {parent}, outside {anchor}: a symlink on the "
                "way leads out of the allowed root")
    return None


def remove(paths: list[str], repository: Path = ROOT, temp: Path | None = None,
           out=sys.stdout) -> int:
    """Remove every path, or none when any is refused; 0 on success, 2 on a refusal."""
    temp = temp_root() if temp is None else temp
    refused = [reason for reason in (refusal(given, repository, temp) for given in paths)
               if reason is not None]
    if not paths:
        refused = ["no path given"]
    if refused:
        for reason in refused:
            print(f"RMTREE_REFUSED {reason}", file=sys.stderr)
        print("RMTREE_REFUSED nothing was removed", file=sys.stderr)
        return 2
    for given in paths:
        path = absolute(Path(given))
        if path.is_symlink():
            target = os.readlink(path)
            path.unlink()
            print(f"RMTREE_REMOVED symlink {path} (its target {target} left as it is)", file=out)
        elif path.is_dir():
            shutil.rmtree(path)
            print(f"RMTREE_REMOVED directory {path}", file=out)
        elif path.exists():
            path.unlink()
            print(f"RMTREE_REMOVED file {path}", file=out)
        else:
            print(f"RMTREE_ABSENT {path}: nothing to remove", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    return remove([arg for arg in args if arg != "--"])


if __name__ == "__main__":
    raise SystemExit(main())
