#!/usr/bin/env python3
"""Root-only explicit approved source fetch. Parallel units must not invoke this entrypoint."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True

from validation_tools import ValidationRefused, require, sha, read_json, source_manifest, stable_base, owned_path, inspect_source, git


def require_approval(root, path):
    manifest = source_manifest(root)
    approval = read_json(path)
    require(approval.get("approved") is True and approval.get("approved_by") == "user", "no recorded direct user approval")
    require(approval.get("scope") == manifest["scope"] and approval.get("source_manifest_sha256") == sha(root / "tools/s5/validation-sources-350.json"), "approval scope/manifest differs")
    require(isinstance(approval.get("user_quote"), str) and approval["user_quote"].strip(), "approval lacks exact direct user quote")
    base = stable_base(root)
    require(path.resolve(strict=True) == base / "approval.json" and not path.is_symlink(), "approval must be exact stable approval.json")
    return manifest


def root_fetch_git(checkout, *args):
    env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"}
    argv = ["/usr/bin/git", "--no-replace-objects", "--no-lazy-fetch", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(checkout), *args]
    return subprocess.check_output(argv, env=env, stderr=subprocess.PIPE).decode().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--approval", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    manifest = require_approval(root, args.approval)
    base = stable_base(root)
    inputs = {str(p.relative_to(root)): sha(p) for p in (root / "tools/s5/validation-sources-350.json", root / "tools/s5/validation_tools.py", Path(__file__).resolve())}
    sources = owned_path(root, "private/s5-tools/validation-350/sources", must_exist=False)
    sources.mkdir(exist_ok=True)
    rows = []
    for row in manifest["sources"]:
        checkout = owned_path(root, "private/s5-tools/validation-350/sources/" + row["name"], must_exist=False)
        if not checkout.exists():
            checkout.mkdir()
            root_fetch_git(checkout, "init", "--quiet")
            root_fetch_git(checkout, "remote", "add", "origin", row["url"])
            root_fetch_git(checkout, "fetch", "--no-tags", "--depth=1", "origin", row["ref"])
            require(git(checkout, "rev-parse", "FETCH_HEAD^{commit}") == row["commit"], "upstream ref moved")
            root_fetch_git(checkout, "-c", "advice.detachedHead=false", "checkout", "--detach", "FETCH_HEAD")
        value = inspect_source(checkout, row)
        rows.append(value)
        print(f"SOURCE {row['name']} commit {value['commit']} tree {value['tree']}", flush=True)
    known_good = sources / "Vulkan-ValidationLayers/scripts/known_good.json"
    upstream = read_json(known_good)["repos"]
    require([(r["name"], r["url"], r["commit"]) for r in upstream[:4]] == [(r["name"], r["url"], r["ref"]) for r in manifest["sources"][:4]], "upstream mandatory dependencies differ")
    require(all(r.get("build_platforms") == ["windows"] or "tests" in r.get("optional", []) for r in upstream[4:]), "additional mandatory source needs separate approval")
    require(inputs == {p: sha(root / p) for p in inputs}, "fetcher/helper/manifest changed")
    receipt = {"schema": "sa2-validation-350-fetch-v1", "sources": rows, "source_manifest_sha256": sha(root / "tools/s5/validation-sources-350.json"), "approval_sha256": sha(args.approval), "input_files": inputs, "upstream_known_good_sha256": sha(known_good)}
    destination = base / "fetch-receipt.json"
    require(not destination.is_symlink(), "fetch receipt is a link")
    data = json.dumps(receipt, indent=2) + "\n"
    if destination.exists():
        require(destination.read_text() == data, "existing fetch receipt differs; no overwrite")
    else:
        with destination.open("x") as stream:
            stream.write(data)
    print(f"FETCH_OK {destination} sha256 {sha(destination)}")


if __name__ == "__main__":
    try:
        main()
    except (ValidationRefused, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"FETCH_FAIL {error}", file=sys.stderr)
        sys.exit(1)
