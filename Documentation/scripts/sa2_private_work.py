#!/usr/bin/env python3
"""Tails Workshop: locate SA2 Emerald's private owned-install work tree.

The canonical Git repository can move independently of the proprietary research
tree.  Callers resolve the latter through SA2_PRIVATE_WORK, a repository-local
ignored pointer file, or the historical wrapper layout, in that order.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


ENVIRONMENT_VARIABLE = "SA2_PRIVATE_WORK"
POINTER_FILE = ".sa2-private-work"
WORK_BASENAME = Path("work/sa2-unity-hello-world")


def git_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path, check=True,
        capture_output=True, text=True,
    )
    return Path(result.stdout.strip()).resolve()


def beneath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validated(candidate: Path, project: Path, source: str) -> Path:
    resolved = candidate.expanduser().resolve()
    if beneath(resolved, project):
        raise RuntimeError(
            f"Private work tree from {source} must be outside Git: {resolved}"
        )
    if not resolved.is_dir():
        raise RuntimeError(
            f"Private work tree from {source} does not exist: {resolved}"
        )
    return resolved


def resolve_private_work(project: Path | None = None) -> Path:
    """Return the validated private work tree or raise with setup guidance."""
    root = (project or git_root(Path(__file__).parent)).resolve()
    configured = os.environ.get(ENVIRONMENT_VARIABLE)
    if configured:
        return _validated(Path(configured), root, ENVIRONMENT_VARIABLE)

    pointer = root / POINTER_FILE
    if pointer.is_file():
        value = pointer.read_text().strip()
        if not value or "\n" in value or "\r" in value:
            raise RuntimeError(f"{pointer} must contain exactly one non-empty path")
        return _validated(Path(value), root, str(pointer))

    historical = root.parents[1] / WORK_BASENAME
    if historical.is_dir():
        return _validated(historical, root, "historical repository layout")

    raise RuntimeError(
        "SA2 private work tree is not configured. Set SA2_PRIVATE_WORK or run "
        f"python3 Documentation/scripts/sa2_private_work.py --init /absolute/path/to/{WORK_BASENAME}"
    )


def write_pointer(project: Path, private_work: Path) -> Path:
    root = project.resolve()
    resolved = _validated(private_work, root, "--init")
    pointer = root / POINTER_FILE
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(str(resolved) + "\n")
    os.replace(temporary, pointer)
    return pointer


def main() -> None:
    try:
        project = git_root(Path(__file__).parent)
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--init", type=Path,
                            help="Write the ignored repository-local pointer")
        args = parser.parse_args()
        if args.init is not None:
            pointer = write_pointer(project, args.init)
            print(
                "SA2_PRIVATE_WORK_CONFIGURED "
                f"pointer={pointer} work={resolve_private_work(project)}"
            )
            return
        print(resolve_private_work(project))
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"SA2_PRIVATE_WORK_ERROR {error}") from None


if __name__ == "__main__":
    main()
