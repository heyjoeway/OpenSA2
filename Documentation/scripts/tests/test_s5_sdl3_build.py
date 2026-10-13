"""The SDL3 build's own rules, held without a GPU: the project's patches applied strictly, the
mark of an altered SDL, the configurations, and the players' isolation from other SDL3s.

The user's second rulings of 2026-10-04 (Documentation/decisions/2026-10-04-S5-user-rulings-2.md)
keep the SDL patch in this repository (patches/sdl3), applied by every build, never submitted to
SDL. tools/s5/build_host_deps.sh applies it through tools/s5/sdl3_patches.py, which this file
holds:

  * the tracked patch set is the pinned one, its identity is tools.sdl3.vendor_info, and its
    header names the pinned commit and says it is not from the SDL project;
  * applied to a fresh extraction of the pinned tarball it changes every file it names, and the
    result reads the patch's properties;
  * it refuses, each with its own message: a hunk whose context was altered by one line (no
    fuzz), a hunk that would land at an offset, a patch already applied, an apply that changes
    nothing, a patch naming no file, a patch that is not the pinned bytes, a patch the pins do
    not name, a vendor_info not derived from the patches, a header without its claims (the
    tarball's sha256 among them), a tree that is not the pinned commit (its .git-hash or its
    REVISION.txt), a .rej or .orig file left in the tree, and a GNU patch that is not the pinned
    version, held whole (tools.patch); and GNU patch runs in the C locale with none of the
    caller's environment, since the offset check reads its messages;
  * the patch-set rule has two implementations, the builder's (sdl3_patches.checked_patches,
    which test/extract_refs.py also calls) and the pin gate's (check_pins.sdl3_patches): each
    variant of the set (a link, the pins out of file-name order, a header claim missing, other
    bytes, an unpinned file, a vendor_info not derived) is refused by both, so they cannot drift;
  * the startup line both players print (host/player/sdl_mark.h) names a patched SDL as patched
    and a stock one as stock, word for word;
  * host/player/generate_expectations.py takes the configuration the install is: the patched
    SDL's revision with its vendor string, the stock one's without, and refuses an install whose
    library no pin names, or whose revision header's vendor string is not its configuration's;
    under this repository's own pin entries, both hosts' contracts build on both
    configurations, the stock prefix's DXVK held to tools.dxvk's hash; it refuses a vendor string
    the revision does not carry, two pins naming one library, and a pin the contract needs and
    the pins lack (by name), and holds each configuration's header to its own entry's version;
  * test_synth.sh's isolated bad-config player (host/player/test_bad_config_contract.py
    prepare) holds a contract to the SDL pin of the configuration it names: a patched contract
    to tools.sdl3, a stock one to tools.sdl3-stock, and a contract labelled with the other
    configuration, or with none, is refused;
  * the state check's SDL side (host/d3d9gpu/test/extract_refs.py): a patched install's spans are
    cut from the tarball with the pinned patches applied, the patch's own spans among them; a
    stock install's from the bare tarball; another patch set's mark is refused;
  * the players' link rule (DT_RPATH, host/player/build.sh and host/d3d9gpu/build.sh): a library
    on LD_LIBRARY_PATH does not displace the one the rpath names, and the DT_RUNPATH a plain
    -rpath gives would (the control); every -rpath in host/'s scripts, in any spelling, follows
    -Wl,--disable-new-dtags; and each build script's own check, cut from it as it is, covers
    all its SDL-linking binaries and refuses DT_RUNPATH, even beside the correct DT_RPATH,
    and an rpath elsewhere;
  * each build script's repository is its own checkout unless SA2_REPO names another, and a
    default OUT that resolves outside it (a clone whose private/ is a link to another tree's,
    whose play/bin is that tree's shared player builds) is refused before anything is written;
    host/player/player_env.sh likewise defaults to its own checkout, and unsets SDL3_DYNAMIC_API
    (SDL's dynamic API, which would route every SDL call past the verified library);
  * the player itself refuses to start with SDL3_DYNAMIC_API set, before any SDL call on either of
    its start paths (the live stream's and the file's), read from host/player/player.cpp;
  * build_host_deps.sh refuses an unknown configuration before it does anything.

The GPU half (the patched library's behaviour on lavapipe, with the validation layer, and the
stock build reporting neither capability) is test_s5_sdl3_patch.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PATCHES = ROOT / "patches" / "sdl3"
PINS = json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]
TOOL = ROOT / "tools" / "s5" / "sdl3_patches.py"
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "host" / "player"))
sys.path.insert(0, str(ROOT / "host" / "d3d9gpu" / "test"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_pins as CP  # noqa: E402
import extract_refs as ER  # noqa: E402
import generate_expectations as GE  # noqa: E402
import sdl3_patches  # noqa: E402
import test_bad_config_contract as BCC  # noqa: E402

PATCH_NAME = "0001-gpu-clamp-to-border-and-depth-clamp-with-clip.patch"
SENTINEL = b"SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN"
GXX = shutil.which("g++")
GCC = shutil.which("gcc")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


@pytest.fixture(scope="module")
def tarball_tree(tmp_path_factory) -> Path:
    """A fresh extraction of the pinned SDL tarball (a missing tarball fails, it does not skip)."""
    archive = ROOT / PINS["sdl3"]["path"]
    assert archive.is_file(), f"no pinned SDL tarball at {archive}"
    assert sha(archive.read_bytes()) == PINS["sdl3"]["sha256"]
    out = tmp_path_factory.mktemp("sdl-pristine")
    with tarfile.open(archive) as tar:
        tar.extractall(out, filter="data")
    return out / f"SDL3-{PINS['sdl3']['version']}"


@pytest.fixture
def tree(tarball_tree, tmp_path) -> Path:
    copy = tmp_path / tarball_tree.name
    shutil.copytree(tarball_tree, copy, symlinks=True)
    return copy


@pytest.fixture
def root(tmp_path) -> Path:
    """A repository root holding this repository's pins and patches, for one test to break."""
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "pins.json", r / "tools" / "pins.json")
    shutil.copytree(PATCHES, r / "patches" / "sdl3")
    return r


def set_sdl3(root: Path, **changes) -> None:
    doc = json.loads((root / "tools" / "pins.json").read_text())
    doc["tools"]["sdl3"].update(changes)
    (root / "tools" / "pins.json").write_text(json.dumps(doc))


def refused(r: subprocess.CompletedProcess, words: str) -> None:
    assert r.returncode == 1, r.stdout + r.stderr
    assert words in r.stderr, r.stderr


# ------------------------------------------------------------------ the tracked patch set


def test_the_tracked_patches_are_the_pinned_ones_with_their_claims():
    r = tool("check", "--root", str(ROOT))
    assert r.returncode == 0, r.stderr
    digest = sha((PATCHES / PATCH_NAME).read_bytes())
    assert r.stdout.splitlines() == [f"{digest}  patches/sdl3/{PATCH_NAME}",
                                     f"VENDOR_INFO {PINS['sdl3']['vendor_info']}"]
    assert PINS["sdl3"]["patches"] == [{"path": f"patches/sdl3/{PATCH_NAME}", "sha256": digest}]
    assert PINS["sdl3"]["vendor_info"] == "sa2-recomp sdl3-patches " + sha(f"{digest}\n".encode())[:12]
    head = sdl3_patches.header((PATCHES / PATCH_NAME).read_text())
    for words in ("NOT FROM THE SDL PROJECT", PINS["sdl3"]["commit"], PINS["sdl3"]["sha256"],
                  "border addressing and depth clamp", "not submitted to SDL"):
        assert words in " ".join(head.split()), words
    assert sorted(p.name for p in PATCHES.glob("*.patch")) == [PATCH_NAME]


def test_the_pinned_commit_is_the_tarball_s_own(tarball_tree):
    assert (tarball_tree / ".git-hash").read_text().strip() == PINS["sdl3"]["commit"]
    r = tool("source", "--root", str(ROOT), "--tree", str(tarball_tree))
    assert r.returncode == 0 and r.stdout.strip() == (
        f"SDL3-3.4.16 is commit {PINS['sdl3']['commit']} (release-3.4.16-0-gfa2c02bb6)"), r.stderr


def test_applied_to_the_pinned_tarball_it_changes_every_file_it_names(tree):
    named = sdl3_patches.named_files((PATCHES / PATCH_NAME).read_text())
    before = {n: sha((tree / n).read_bytes()) for n in named}
    r = tool("apply", "--root", str(ROOT), "--tree", str(tree))
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-2:] == [f"SDL3_PATCHES_APPLIED 1 {PINS['sdl3']['vendor_info']}",
                                         f"VENDOR_INFO {PINS['sdl3']['vendor_info']}"]
    assert len(named) == 7 and all(sha((tree / n).read_bytes()) != before[n] for n in named)
    assert SENTINEL in (tree / "include/SDL3/SDL_gpu.h").read_bytes()
    assert not [p for p in tree.rglob("*") if p.suffix in (".rej", ".orig")]


# ------------------------------------------------------------------ strict application


@pytest.mark.parametrize("line", [
    # the hunk's outermost context line, which GNU patch's default fuzz would let it skip
    "#define SDL_GPU_BLENDFACTOR_MAX_ENUM_VALUE          (SDL_GPU_BLENDFACTOR_SRC_ALPHA_SATURATE + 1)",
    # the context line next to the change, which no fuzz skips
    "#define SDL_GPU_PRESENTMODE_MAX_ENUM_VALUE          (SDL_GPU_PRESENTMODE_MAILBOX + 1)",
])
def test_a_hunk_whose_context_changed_by_one_line_is_refused(tree, root, line):
    """No fuzz: the tree's line a hunk's context names, altered, stops the apply with the
    patch's name, before anything is written (a dry run first)."""
    target = tree / "src/gpu/SDL_sysgpu.h"
    text = target.read_text()
    assert text.count(line) == 1 and f"\n {line}\n" in (PATCHES / PATCH_NAME).read_text()
    target.write_text(text.replace(line, line + " /* altered */"))
    snapshot = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file() and "gpu" in p.parts}
    r = tool("apply", "--root", str(root), "--tree", str(tree))
    refused(r, f"{PATCH_NAME} does not apply cleanly to {tree} (GNU patch, --fuzz=0")
    assert ("No fuzz is allowed: the dry run refused it, so it changed nothing; any patch before it stays "
            "applied, and nothing may be built from this tree") in r.stderr
    assert "Hunk #" in r.stderr and "FAILED at" in r.stderr, r.stderr
    assert "Can't create temporary file" not in r.stderr and "No space left on device" not in r.stderr, r.stderr
    assert {p: p.read_bytes() for p in snapshot} == snapshot


def test_a_hunk_that_would_land_at_an_offset_is_refused(tree, root):
    target = tree / "src/gpu/SDL_sysgpu.h"
    target.write_text("\n" + target.read_text())
    refused(tool("apply", "--root", str(root), "--tree", str(tree)),
            f"{PATCH_NAME} applies to {tree} only at an offset or with fuzz")


def test_a_second_apply_is_refused(tree, root):
    assert tool("apply", "--root", str(root), "--tree", str(tree)).returncode == 0
    r = tool("apply", "--root", str(root), "--tree", str(tree))
    refused(r, "does not apply cleanly")
    assert "Reversed (or previously applied) patch detected" in r.stderr


def test_an_apply_that_changes_nothing_is_refused(tree, root):
    """A hunk that removes a line and adds it back applies, and leaves its file as it was."""
    line = "#define SDL_GPU_PRESENTMODE_MAX_ENUM_VALUE          (SDL_GPU_PRESENTMODE_MAILBOX + 1)"
    text = (tree / "src/gpu/SDL_sysgpu.h").read_text().split("\n")
    i = text.index(line)
    hunk = "\n".join([f"@@ -{i + 1},1 +{i + 1},1 @@", f"-{line}", f"+{line}"])
    patch = (f"sa2-recomp local patch: NOT FROM THE SDL PROJECT, commit {PINS['sdl3']['commit']}, "
             f"tarball {PINS['sdl3']['sha256']}\n\ndiff --git a/src/gpu/SDL_sysgpu.h b/src/gpu/SDL_sysgpu.h\n"
             f"--- a/src/gpu/SDL_sysgpu.h\n+++ b/src/gpu/SDL_sysgpu.h\n{hunk}\n")
    (root / "patches/sdl3" / PATCH_NAME).write_text(patch)
    digest = sha(patch.encode())
    set_sdl3(root, patches=[{"path": f"patches/sdl3/{PATCH_NAME}", "sha256": digest}],
             vendor_info="sa2-recomp sdl3-patches " + sha(f"{digest}\n".encode())[:12])
    refused(tool("apply", "--root", str(root), "--tree", str(tree)),
            f"{PATCH_NAME} left ['src/gpu/SDL_sysgpu.h'] unchanged: an apply that changes nothing is refused")


def test_a_patch_naming_no_file_is_refused(tree, root):
    patch = f"NOT FROM THE SDL PROJECT {PINS['sdl3']['commit']} {PINS['sdl3']['sha256']}\nno diff here\n"
    (root / "patches/sdl3" / PATCH_NAME).write_text(patch)
    digest = sha(patch.encode())
    set_sdl3(root, patches=[{"path": f"patches/sdl3/{PATCH_NAME}", "sha256": digest}],
             vendor_info="sa2-recomp sdl3-patches " + sha(f"{digest}\n".encode())[:12])
    refused(tool("apply", "--root", str(root), "--tree", str(tree)), f"{PATCH_NAME} names no file")


def set_pin(root: Path, name: str, entry: dict) -> None:
    doc = json.loads((root / "tools" / "pins.json").read_text())
    doc["tools"][name] = entry
    (root / "tools" / "pins.json").write_text(json.dumps(doc))


def test_gnu_patch_is_the_pinned_one(tree, root):
    """Its messages are what the offset check reads, so its version is pinned (tools.patch)."""
    set_pin(root, "patch", dict(PINS["patch"], version="GNU patch 2.7"))
    refused(tool("apply", "--root", str(root), "--tree", str(tree)),
            f"{PINS['patch']['path']} --version reports 'GNU patch 2.8', not the pinned 'GNU patch 2.7'")
    doc = json.loads((root / "tools" / "pins.json").read_text())
    del doc["tools"]["patch"]
    (root / "tools" / "pins.json").write_text(json.dumps(doc))
    refused(tool("apply", "--root", str(root), "--tree", str(tree)), "tools/pins.json pins no patch")
    assert (tree / "include/SDL3/SDL_gpu.h").read_bytes().count(SENTINEL) == 0


def test_gnu_patch_runs_in_the_c_locale_with_nothing_of_the_caller_s_environment(tree, root, tmp_path):
    """A translated message would hide an offset from the check (LANGUAGE, LC_ALL), and
    POSIXLY_CORRECT or a PATCH_* variable changes what patch does: none reaches it. A stand-in
    for the pinned patch records the environment of every call."""
    record = tmp_path / "calls"
    record.mkdir()
    stand_in = tmp_path / "patch"
    stand_in.write_text(f"#!{sys.executable}\n" +
                        "import os, sys, tempfile\nfrom pathlib import Path\n" +
                        f"record = Path({str(record)!r})\n" +
                        "(record / f'call.{os.getpid()}').write_text(''.join(f'{k}={v}\\n' for k, v in os.environ.items()))\n" +
                        "if sys.argv[1] == '--version':\n    print('GNU patch 2.8')\nelse:\n" +
                        f"    base = {['-p1', '--fuzz=0', '--forward', '--batch', '--no-backup-if-mismatch', '-d', str(tree), '-i', str(root / 'patches/sdl3' / PATCH_NAME)]!r}\n" +
                        "    assert sys.argv[1:] in (base, base + ['--dry-run'])\n" +
                        "    with tempfile.TemporaryFile(dir=os.environ['TMPDIR']) as held:\n" +
                        "        held.write(b'owned temp'); held.flush()\n")
    stand_in.chmod(0o755)
    set_pin(root, "patch", dict(PINS["patch"], path=str(stand_in)))
    env = dict(os.environ, LANGUAGE="de", LC_ALL="de_DE.UTF-8", LANG="de_DE.UTF-8", POSIXLY_CORRECT="1",
               PATCH_VERSION_CONTROL="numbered", VERSION_CONTROL="numbered", SIMPLE_BACKUP_SUFFIX=".x")
    r = subprocess.run([sys.executable, str(TOOL), "apply", "--root", str(root), "--tree", str(tree)],
                       capture_output=True, text=True, env=env)
    # the stand-in changes nothing, so the apply is refused after it ran: --version, dry run, apply
    refused(r, "an apply that changes nothing is refused")
    calls = sorted(record.iterdir())
    assert len(calls) == 3
    for call in calls:
        seen = dict(line.split("=", 1) for line in call.read_text().splitlines() if "=" in line)
        seen.pop("PWD", None), seen.pop("SHLVL", None), seen.pop("_", None)   # the shell's own
        child_tmp = seen.pop("TMPDIR", None)
        assert seen == {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, seen
        if child_tmp is not None:
            assert Path(child_tmp).parent == tree.resolve().parent
            assert Path(child_tmp).name.startswith("sa2-gnu-patch-")
    owned = [dict(line.split("=", 1) for line in call.read_text().splitlines() if "=" in line).get("TMPDIR")
             for call in calls]
    assert owned.count(None) == 1  # --version retains the original environment
    assert len(set(value for value in owned if value is not None)) == 1  # dry-run and apply share storage
    assert not list(tree.parent.glob("sa2-gnu-patch-*"))


# ------------------------------------------------------------------ the pinned set


def test_a_patch_that_is_not_the_pinned_bytes_is_refused(tree, root):
    p = root / "patches/sdl3" / PATCH_NAME
    p.write_bytes(p.read_bytes().replace(b"Border addressing.", b"Border  addressing."))
    r = tool("apply", "--root", str(root), "--tree", str(tree))
    refused(r, f"patches/sdl3/{PATCH_NAME} hashes to {sha(p.read_bytes())}, not its pin")
    assert "it is not the pinned patch" in r.stderr
    assert (tree / "include/SDL3/SDL_gpu.h").read_bytes().count(SENTINEL) == 0


def test_a_patch_the_pins_do_not_name_is_refused(root):
    shutil.copy2(root / "patches/sdl3" / PATCH_NAME, root / "patches/sdl3/0002-extra.patch")
    refused(tool("check", "--root", str(root)), "the patches under patches/sdl3/ are ")


def test_a_vendor_info_not_derived_from_the_patches_is_refused(root):
    set_sdl3(root, vendor_info="sa2-recomp sdl3-patches 000000000000")
    refused(tool("check", "--root", str(root)),
            f"tools.sdl3.vendor_info is 'sa2-recomp sdl3-patches 000000000000'; the pinned patches' identity "
            f"is {PINS['sdl3']['vendor_info']!r}")


@pytest.mark.parametrize("words", ["NOT FROM THE SDL PROJECT", "fa2c02bb6e21974a89ea9824bc53c9932abe5f9c",
                                   PINS["sdl3"]["sha256"]])
def test_a_header_without_its_claims_is_refused(root, words):
    p = root / "patches/sdl3" / PATCH_NAME
    text = p.read_text()
    head = sdl3_patches.header(text)
    p.write_text(head.replace(words, "x" * len(words)) + text[len(head):])
    digest = sha(p.read_bytes())
    set_sdl3(root, patches=[{"path": f"patches/sdl3/{PATCH_NAME}", "sha256": digest}],
             vendor_info="sa2-recomp sdl3-patches " + sha(f"{digest}\n".encode())[:12])
    refused(tool("check", "--root", str(root)), f"{PATCH_NAME}'s header does not name")


def test_a_tree_that_is_not_the_pinned_commit_is_refused(tree, root):
    (tree / ".git-hash").write_text("0" * 40 + "\n")
    refused(tool("apply", "--root", str(root), "--tree", str(tree)),
            f"{tree}/.git-hash reads '{'0' * 40}', not the pinned commit")


def test_a_tree_whose_revision_is_not_the_pinned_commit_s_is_refused(tree, root):
    """The .git-hash right and REVISION.txt naming another commit: refused by both checks of the
    tree (source, and apply before it applies anything)."""
    (tree / "REVISION.txt").write_text("release-3.4.16-0-g000000000\n")
    want = f"{tree}/REVISION.txt reads 'release-3.4.16-0-g000000000', not release-3.4.16-0-gfa2c02bb6"
    refused(tool("source", "--root", str(root), "--tree", str(tree)), want)
    refused(tool("apply", "--root", str(root), "--tree", str(tree)), want)
    assert (tree / "include/SDL3/SDL_gpu.h").read_bytes().count(SENTINEL) == 0


def test_a_rej_or_orig_file_left_in_the_tree_is_refused(tree, root):
    """A .rej or .orig file anywhere in the tree after the apply is refused, whoever left it."""
    (tree / "src" / "stray.c.orig").write_text("left behind\n")
    refused(tool("apply", "--root", str(root), "--tree", str(tree)),
            f"{PATCH_NAME} left ['src/stray.c.orig'] in {tree}")


def test_gnu_patch_s_version_is_held_whole(tree, root, tmp_path):
    """The pinned "GNU patch 2.8" is not "GNU patch 2.8.1": the version is matched whole, as
    check_pins.py reads a --version line, not as a substring."""
    stand_in = tmp_path / "patch"
    stand_in.write_text('#!/bin/sh\n[ "$1" = --version ] && echo "GNU patch 2.8.1"\nexit 0\n')
    stand_in.chmod(0o755)
    set_pin(root, "patch", dict(PINS["patch"], path=str(stand_in)))
    refused(tool("apply", "--root", str(root), "--tree", str(tree)),
            f"{stand_in} --version reports 'GNU patch 2.8.1', not the pinned 'GNU patch 2.8'")


def repin(root: Path, names: list[str]) -> None:
    """tools.sdl3.patches as `names` (in that order), each at its file's sha256, and the
    vendor_info of that order: only what the variant breaks is wrong."""
    rows = [{"path": f"patches/sdl3/{n}", "sha256": sha((root / "patches/sdl3" / n).read_bytes())} for n in names]
    set_sdl3(root, patches=rows, vendor_info=sdl3_patches.identity([r["sha256"] for r in rows]))


def variant_link(root: Path) -> None:
    p = root / "patches/sdl3" / PATCH_NAME
    (root / "elsewhere.patch").write_bytes(p.read_bytes())
    p.unlink()
    p.symlink_to(root / "elsewhere.patch")


def variant_order(root: Path) -> None:
    shutil.copy2(root / "patches/sdl3" / PATCH_NAME, root / "patches/sdl3" / "0002-second.patch")
    repin(root, ["0002-second.patch", PATCH_NAME])


def variant_claim(root: Path) -> None:
    p = root / "patches/sdl3" / PATCH_NAME
    text = p.read_text()
    head = sdl3_patches.header(text)
    p.write_text(head.replace(PINS["sdl3"]["sha256"], "0" * 64) + text[len(head):])
    repin(root, [PATCH_NAME])


def variant_bytes(root: Path) -> None:
    p = root / "patches/sdl3" / PATCH_NAME
    p.write_bytes(p.read_bytes().replace(b"Border addressing.", b"Border  addressing."))


def variant_unpinned(root: Path) -> None:
    shutil.copy2(root / "patches/sdl3" / PATCH_NAME, root / "patches/sdl3" / "0002-unpinned.patch")


def variant_vendor(root: Path) -> None:
    set_sdl3(root, vendor_info="sa2-recomp sdl3-patches 000000000000")


@pytest.mark.parametrize("variant,builder,gate", [
    (variant_link, f"patches/sdl3/{PATCH_NAME} is not a regular file", f"patch patches/sdl3/{PATCH_NAME} is not a regular file"),
    (variant_order, f"the patches under patches/sdl3/ are ['patches/sdl3/{PATCH_NAME}', 'patches/sdl3/0002-second.patch'], "
                    f"the pins name ['patches/sdl3/0002-second.patch', 'patches/sdl3/{PATCH_NAME}']",
     f"the patches under patches/sdl3/ are ['patches/sdl3/{PATCH_NAME}', 'patches/sdl3/0002-second.patch'], "
     f"the pins name ['patches/sdl3/0002-second.patch', 'patches/sdl3/{PATCH_NAME}']"),
    (variant_claim, f"{PATCH_NAME}'s header does not name the pinned tarball's sha256",
     f"{PATCH_NAME}'s header does not name '{PINS['sdl3']['sha256']}'"),
    (variant_bytes, "it is not the pinned patch", "not the pinned patch"),
    (variant_unpinned, "the patches under patches/sdl3/ are ", "the patches under patches/sdl3/ are "),
    (variant_vendor, "tools.sdl3.vendor_info is 'sa2-recomp sdl3-patches 000000000000'",
     "vendor_info 'sa2-recomp sdl3-patches 000000000000' is not the pinned patches' identity"),
])
def test_the_builder_and_the_pin_gate_refuse_one_patch_set(root, variant, builder, gate):
    """One rule, two implementations (the second review's finding 3): each variant of the patch
    set is refused by the builder's check (sdl3_patches.checked_patches, also extract_refs.py's)
    and by the pin gate's (check_pins.sdl3_patches), each with its own words; the unbroken set
    passes both (the control)."""
    pin = json.loads((root / "tools" / "pins.json").read_text())["tools"]["sdl3"]
    assert sdl3_patches.checked_patches(root, pin)[1] == PINS["sdl3"]["vendor_info"]
    assert CP.sdl3_patches(root, pin).endswith(f"identity {PINS['sdl3']['vendor_info']!r}")
    variant(root)
    pin = json.loads((root / "tools" / "pins.json").read_text())["tools"]["sdl3"]
    with pytest.raises(sdl3_patches.PatchError) as built:
        sdl3_patches.checked_patches(root, pin)
    assert builder in str(built.value), str(built.value)
    with pytest.raises(CP.Mismatch) as held:
        CP.sdl3_patches(root, pin)
    assert gate in str(held.value), str(held.value)


# ------------------------------------------------------------------ the mark of an altered SDL


@pytest.mark.skipif(GXX is None, reason="no g++")
def test_the_startup_line_names_a_patched_sdl_as_patched(tmp_path):
    src = tmp_path / "mark.cpp"
    src.write_text('#include "sdl_mark.h"\n#include <cstdio>\nint main(int, char** argv) {\n'
                   '    for (int i = 1; argv[i]; i++) std::printf("%s\\n", sa2_sdl_mark_line(argv[i]).c_str());\n}\n')
    exe = tmp_path / "mark"
    r = subprocess.run([GXX, "-std=c++17", "-Wall", "-Werror", "-I", str(ROOT / "host" / "player"), str(src), "-o",
                        str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    patched = f"SDL-release-3.4.16-0-gfa2c02bb6 ({PINS['sdl3']['vendor_info']})"
    out = subprocess.run([str(exe), patched, "SDL-release-3.4.16-0-gfa2c02bb6"], capture_output=True, text=True)
    assert out.stdout.splitlines() == [
        f"SDL3: {patched}: patched by sa2-recomp (patches/sdl3), not the SDL project's release",
        "SDL3: SDL-release-3.4.16-0-gfa2c02bb6: stock"]
    assert '#include "sdl_mark.h"' in (ROOT / "host/player/player.cpp").read_text()
    assert 'std::printf("%s\\n", sa2_sdl_mark_line(sdl_revision_).c_str());' in \
        (ROOT / "host/player/player.cpp").read_text()


# ------------------------------------------------------------------ the configurations' contracts


REVISION_H = '''#ifndef SDL_revision_h_
#define SDL_revision_h_
{vendor}
#if defined(SDL_VENDOR_INFO)
#define SDL_REVISION "SDL-release-3.4.16-0-gfa2c02bb6 (" SDL_VENDOR_INFO ")"
#else
#define SDL_REVISION "SDL-release-3.4.16-0-gfa2c02bb6"
#endif
#endif
'''


def fake_install(base: Path, vendor: str | None, library: bytes) -> Path:
    inc = base / "include" / "SDL3"
    inc.mkdir(parents=True)
    (inc / "SDL_version.h").write_text("#define SDL_MAJOR_VERSION   3\n#define SDL_MINOR_VERSION   4\n"
                                       "#define SDL_MICRO_VERSION   16\n")
    (inc / "SDL_revision.h").write_text(REVISION_H.format(vendor=f'#define SDL_VENDOR_INFO "{vendor}"' if vendor else ""))
    lib = base / "lib"
    lib.mkdir()
    (lib / "libSDL3.so.0.4.16").write_bytes(library)
    (lib / "libSDL3.so.0").symlink_to("libSDL3.so.0.4.16")
    return base


@pytest.fixture
def configurations(tmp_path) -> Path:
    """A repository whose pins name a patched and a stock install, each with its own SDL."""
    repo = tmp_path / "repo"
    s5 = repo / "private" / "s5-tools"
    vendor = PINS["sdl3"]["vendor_info"]
    patched = fake_install(s5 / "install", vendor, b"patched SDL")
    stock = fake_install(s5 / "install-sdl3-stock", None, b"stock SDL")
    (repo / "tools").mkdir()
    pins = {"sdl3": {"version": "3.4.16", "vendor_info": vendor, "library": "private/s5-tools/install/lib/libSDL3.so.0",
                     "library_sha256": sha(b"patched SDL")},
            "sdl3-stock": {"version": "3.4.16", "library": "private/s5-tools/install-sdl3-stock/lib/libSDL3.so.0",
                           "library_sha256": sha(b"stock SDL")}}
    (repo / "tools" / "pins.json").write_text(json.dumps({"tools": pins}))
    assert patched.is_dir() and stock.is_dir()
    return repo


def test_each_configuration_s_contract_is_its_own_sdl(configurations, tmp_path):
    s5 = configurations / "private" / "s5-tools"
    patched = GE.generate(configurations, s5 / "install", None, tmp_path / "p", "sdlgpu")
    stock = GE.generate(configurations, s5 / "install-sdl3-stock", None, tmp_path / "s", "sdlgpu")
    assert patched["sdl3_configuration"] == "patched" and stock["sdl3_configuration"] == "stock"
    assert patched["contract"] == {"sdl_sha256": sha(b"patched SDL"), "sdl_version": 3004016,
                                   "sdl_revision": f"SDL-release-3.4.16-0-gfa2c02bb6 ({PINS['sdl3']['vendor_info']})"}
    assert stock["contract"] == {"sdl_sha256": sha(b"stock SDL"), "sdl_version": 3004016,
                                 "sdl_revision": "SDL-release-3.4.16-0-gfa2c02bb6"}


def test_an_install_no_pin_names_or_with_another_mark_is_refused(configurations, tmp_path):
    s5 = configurations / "private" / "s5-tools"
    system = fake_install(tmp_path / "system", None, b"a system SDL")
    with pytest.raises(ValueError, match="selected SDL prefix is not the pinned input"):
        GE.generate(configurations, system, None, tmp_path / "x", "sdlgpu")
    # an unmarked patched library, and a stock one carrying the mark
    (s5 / "install/include/SDL3/SDL_revision.h").write_text(REVISION_H.format(vendor=""))
    with pytest.raises(ValueError, match="the patched configuration's SDL revision header defines SDL_VENDOR_INFO none"):
        GE.generate(configurations, s5 / "install", None, tmp_path / "y", "sdlgpu")
    (s5 / "install-sdl3-stock/include/SDL3/SDL_revision.h").write_text(
        REVISION_H.format(vendor='#define SDL_VENDOR_INFO "sa2-recomp sdl3-patches 000000000000"'))
    with pytest.raises(ValueError, match="the stock configuration's SDL revision header defines SDL_VENDOR_INFO"):
        GE.generate(configurations, s5 / "install-sdl3-stock", None, tmp_path / "z", "sdlgpu")


def test_a_vendor_string_the_revision_does_not_carry_is_refused(configurations, tmp_path):
    """SDL_VENDOR_INFO defined while SDL_REVISION does not append it: the revision the library
    reports would not be the one the contract expects."""
    s5 = configurations / "private" / "s5-tools"
    (s5 / "install/include/SDL3/SDL_revision.h").write_text(
        f'#define SDL_VENDOR_INFO "{PINS["sdl3"]["vendor_info"]}"\n'
        '#define SDL_REVISION "SDL-release-3.4.16-0-gfa2c02bb6"\n')
    with pytest.raises(ValueError, match="does not append SDL_VENDOR_INFO to its revision"):
        GE.generate(configurations, s5 / "install", None, tmp_path / "g", "sdlgpu")


def test_two_pins_naming_one_library_are_refused_as_both(configurations, tmp_path):
    s5 = configurations / "private" / "s5-tools"
    doc = json.loads((configurations / "tools" / "pins.json").read_text())
    doc["tools"]["sdl3-stock"]["library"] = doc["tools"]["sdl3"]["library"]
    (configurations / "tools" / "pins.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="is both the patched configuration's .* and the stock one's"):
        GE.generate(configurations, s5 / "install", None, tmp_path / "g", "sdlgpu")


def test_each_configuration_s_header_is_held_to_its_own_entry_s_version(configurations, tmp_path):
    s5 = configurations / "private" / "s5-tools"
    doc = json.loads((configurations / "tools" / "pins.json").read_text())
    doc["tools"]["sdl3"]["version"] = "3.4.15"          # the patched entry's, not the stock one's
    (configurations / "tools" / "pins.json").write_text(json.dumps(doc))
    assert GE.generate(configurations, s5 / "install-sdl3-stock", None, tmp_path / "s", "sdlgpu")[
        "sdl3_configuration"] == "stock"
    with pytest.raises(ValueError, match=r"SDL header version 3.4.16 disagrees with tools/pins.json \(sdl3.version 3.4.15\)"):
        GE.generate(configurations, s5 / "install", None, tmp_path / "p", "sdlgpu")
    doc["tools"]["sdl3"]["version"], doc["tools"]["sdl3-stock"]["version"] = "3.4.16", "3.4.17"
    (configurations / "tools" / "pins.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=r"\(sdl3-stock.version 3.4.17\)"):
        GE.generate(configurations, s5 / "install-sdl3-stock", None, tmp_path / "t", "sdlgpu")


@pytest.fixture
def real_shaped(tmp_path) -> Path:
    """A repository whose sdl3, sdl3-stock and dxvk pins are this repository's own entries, every
    key as tools/pins.json has it, with only the three library hashes replaced by the fake
    installs' (each prefix holding an SDL, and DXVK at the path its pin names): the players'
    contracts read the real schema, so a key the generator expects and the pins lack fails here."""
    repo = tmp_path / "repo"
    s5 = repo / "private" / "s5-tools"
    fake_install(s5 / "install", PINS["sdl3"]["vendor_info"], b"patched SDL")
    fake_install(s5 / "install-sdl3-stock", None, b"stock SDL")
    for library in (PINS["dxvk"]["library"], PINS["sdl3-stock"]["dxvk_library"]):
        (repo / library).parent.mkdir(parents=True, exist_ok=True)
        (repo / library).write_bytes(b"DXVK")
    pins = {name: json.loads(json.dumps(PINS[name])) for name in ("sdl3", "sdl3-stock", "dxvk")}
    pins["sdl3"]["library_sha256"] = sha(b"patched SDL")
    pins["sdl3-stock"]["library_sha256"] = sha(b"stock SDL")
    pins["dxvk"]["library_sha256"] = sha(b"DXVK")
    (repo / "tools").mkdir()
    (repo / "tools" / "pins.json").write_text(json.dumps({"tools": pins}))
    (repo / "dxvk.conf").write_text("# test\n")
    return repo


def test_both_hosts_contracts_build_on_both_configurations_under_the_real_pin_schema(real_shaped, tmp_path):
    """The DXVK host on the stock prefix: its DXVK (tools.sdl3-stock.dxvk_library) is held to
    tools.dxvk.library_sha256, as check_pins.py holds it. The I4 review's blocker: the stock
    DXVK contract read a tools.sdl3-stock.dxvk_library_sha256 the pins do not have."""
    s5 = real_shaped / "private" / "s5-tools"
    conf = real_shaped / "dxvk.conf"
    got = {}
    for install in ("install", "install-sdl3-stock"):
        for host, config in (("dxvk", conf), ("sdlgpu", None)):
            got[install, host] = GE.generate(real_shaped, s5 / install, config, tmp_path / f"{install}-{host}", host)
    for host in ("dxvk", "sdlgpu"):
        assert got["install", host]["sdl3_configuration"] == "patched"
        assert got["install-sdl3-stock", host]["sdl3_configuration"] == "stock"
    assert got["install-sdl3-stock", "dxvk"]["contract"] == {
        "sdl_sha256": sha(b"stock SDL"), "dxvk_sha256": sha(b"DXVK"), "dxvk_config_sha256": sha(b"# test\n"),
        "sdl_version": 3004016, "sdl_revision": "SDL-release-3.4.16-0-gfa2c02bb6"}
    assert got["install-sdl3-stock", "dxvk"]["libraries"]["dxvk"]["path"] == str(
        (real_shaped / PINS["sdl3-stock"]["dxvk_library"]).resolve())
    assert got["install", "dxvk"]["contract"]["dxvk_sha256"] == sha(b"DXVK")
    assert got["install", "dxvk"]["libraries"]["dxvk"]["path"] == str((real_shaped / PINS["dxvk"]["library"]).resolve())


def test_the_stock_dxvk_contract_refuses_other_bytes_and_a_hash_of_the_stock_entry_s_own(real_shaped, tmp_path):
    s5 = real_shaped / "private" / "s5-tools"
    conf = real_shaped / "dxvk.conf"
    stock_dxvk = real_shaped / PINS["sdl3-stock"]["dxvk_library"]
    stock_dxvk.write_bytes(b"DXVK rebuilt")
    with pytest.raises(ValueError, match=r"^dxvk library disagrees with tools/pins.json \(dxvk.library_sha256\)$"):
        GE.generate(real_shaped, s5 / "install-sdl3-stock", conf, tmp_path / "a", "dxvk")
    stock_dxvk.write_bytes(b"DXVK")
    doc = json.loads((real_shaped / "tools" / "pins.json").read_text())
    doc["tools"]["sdl3-stock"]["dxvk_library_sha256"] = sha(b"DXVK")
    (real_shaped / "tools" / "pins.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="tools.sdl3-stock pins dxvk_library_sha256"):
        GE.generate(real_shaped, s5 / "install-sdl3-stock", conf, tmp_path / "b", "dxvk")


def test_a_pin_the_contract_needs_and_the_pins_lack_is_refused_by_name(real_shaped, tmp_path):
    """Not a KeyError traceback (the shape of the first review's blocker): the missing pin named."""
    s5 = real_shaped / "private" / "s5-tools"
    doc = json.loads((real_shaped / "tools" / "pins.json").read_text())
    del doc["tools"]["sdl3-stock"]["dxvk_library"]
    (real_shaped / "tools" / "pins.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=r"^tools/pins.json pins no sdl3-stock.dxvk_library, which the contract needs$"):
        GE.generate(real_shaped, s5 / "install-sdl3-stock", real_shaped / "dxvk.conf", tmp_path / "a", "dxvk")


# ------------------------------------------------------------------ the bad-config player's contract


def bad_config_args(tmp_path: Path, configuration: str | None, sdl_entry: str) -> argparse.Namespace:
    """test_bad_config_contract.py prepare's arguments: a build whose generated contract names
    `configuration` (none: no sdl3_configuration) and holds `sdl_entry`'s library hash, in a
    repository whose sdl3, sdl3-stock and dxvk pins are this repository's own."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "s5").symlink_to(ROOT / "tools" / "s5")
    (repo / "tools" / "pins.json").write_text(json.dumps({"tools": {k: PINS[k] for k in ("sdl3", "sdl3-stock", "dxvk")}}))
    player = ROOT / "host" / "player"
    conf = (player / "dxvk.conf").read_bytes()
    contract = {"sdl_sha256": PINS[sdl_entry]["library_sha256"], "dxvk_sha256": PINS["dxvk"]["library_sha256"],
                "dxvk_config_sha256": sha(conf), "sdl_version": 3004016,
                "sdl_revision": "SDL-release-3.4.16-0-gfa2c02bb6"}
    expected = tmp_path / "bin" / "expected"
    expected.mkdir(parents=True)
    header = BCC.generated_header(contract)
    (expected / "sa2_host_expectations.h").write_text(header)
    record = {"contract": contract, "header": {"sha256": sha(header.encode())},
              **({"sdl3_configuration": configuration} if configuration else {})}
    (expected / "expectations.json").write_text(json.dumps(record))
    bad = tmp_path / "neg-float.conf"
    bad.write_bytes(conf.replace(b"d3d9.floatEmulation = Strict\n", b"d3d9.floatEmulation = True\n", 1))
    return argparse.Namespace(repo=repo, player_dir=player, bin=tmp_path / "bin",
                              stream_header=ROOT / "host" / "sa2_stream.h", config=bad, out=tmp_path / "out")


@pytest.mark.parametrize("configuration,sdl_entry,refusal", [
    ("patched", "sdl3", None),
    ("stock", "sdl3-stock", None),
    # mislabelled: the stock label on the patched library's hash, and the converse
    ("stock", "sdl3", "the production sdl3-stock expectation differs from its pin"),
    ("patched", "sdl3-stock", "the production sdl3 expectation differs from its pin"),
    (None, "sdl3", "the production contract names no SDL3 configuration: None"),
])
def test_the_bad_config_player_holds_its_contract_to_its_configuration_s_sdl_pin(tmp_path, configuration, sdl_entry,
                                                                                 refusal):
    args = bad_config_args(tmp_path, configuration, sdl_entry)
    if refusal is None:
        BCC.prepare(args)
        manifest = json.loads((args.out / "contract.json").read_text())
        assert manifest["original_contract"]["sdl_sha256"] == PINS[sdl_entry]["library_sha256"]
        assert manifest["change"]["field"] == "dxvk_config_sha256"
    else:
        with pytest.raises(ValueError, match=re.escape(refusal)):
            BCC.prepare(args)
        assert not args.out.exists()


# ------------------------------------------------------------------ the state check's SDL side


# The spans only the patched SDL has (host/d3d9gpu/test/extract_refs.py sdl_spans).
PATCHED_ONLY = {"SDLToVK_SamplerBorderColor", "SDL_gpu.CHECK_SAMPLERADDRESSMODE_ENUM_INVALID",
                "VULKAN_CreateGraphicsPipeline.clamp_local", "VULKAN_CreateSampler.border_local"}


def revision_include(base: Path, vendor: str | None) -> Path:
    base.mkdir(parents=True)
    (base / "SDL_revision.h").write_text(REVISION_H.format(vendor=f'#define SDL_VENDOR_INFO "{vendor}"' if vendor else ""))
    return base


def pinned_tarball():
    return ER.SdlTarball(ROOT / PINS["sdl3"]["path"], PINS["sdl3"]["sha256"])


def test_the_state_check_cuts_the_patched_sdl_s_spans_for_a_patched_install(tmp_path):
    """extract_refs.py, which the d3d9gpu build runs: an install whose revision header carries the
    pinned vendor string is the patched configuration, its spans cut from the tarball with the
    pinned patches applied (the patch's own lines among them); a stock install's from the bare
    tarball, without the patch's spans."""
    tarball = pinned_tarball()
    configuration, spans, vendor = ER.sdl_configuration(revision_include(tmp_path / "p", PINS["sdl3"]["vendor_info"]),
                                                        tarball, PINS["sdl3"], ROOT)
    assert (configuration, vendor) == ("patched", PINS["sdl3"]["vendor_info"])
    assert tarball.patches == PINS["sdl3"]["patches"]
    body, manifest = ER.render(spans, tarball.read, {}, lambda name: name)
    assert PATCHED_ONLY <= {m["name"] for m in manifest}
    for line in (ER.PATCHED_CLAMP_LOCAL, ER.PATCHED_BORDER_LOCAL, ER.PATCHED_MAX_ENUM_END):
        assert line in body, line
    # every span of a file the patch writes was cut from the patched bytes
    named = {ER.SDL_ROOT + n for n in sdl3_patches.named_files((PATCHES / PATCH_NAME).read_text())}
    assert set(tarball.patched) == named
    stock = pinned_tarball()
    for m in manifest:
        if m["source"] in named:
            assert tarball.read(m["source"]) != stock.read(m["source"]), m["source"]

    configuration, stock_spans, vendor = ER.sdl_configuration(revision_include(tmp_path / "s", None), stock,
                                                              PINS["sdl3"], ROOT)
    assert (configuration, vendor, stock.patches, stock.patched) == ("stock", None, [], {})
    _, stock_manifest = ER.render(stock_spans, stock.read, {}, lambda name: name)
    assert not PATCHED_ONLY & {m["name"] for m in stock_manifest}
    # the control: the patched spans are not in the bare tarball
    with pytest.raises(ER.Refusal):
        ER.render(spans, stock.read, {}, lambda name: name)


def test_the_state_check_refuses_an_install_marked_with_another_patch_set(tmp_path):
    with pytest.raises(ER.Refusal, match="not tools.sdl3.vendor_info"):
        ER.sdl_configuration(revision_include(tmp_path / "x", "sa2-recomp sdl3-patches 000000000000"),
                             pinned_tarball(), PINS["sdl3"], ROOT)


# ------------------------------------------------------------------ isolation


@pytest.mark.skipif(GCC is None, reason="no gcc")
def test_the_players_rpath_is_not_displaced_by_ld_library_path(tmp_path):
    """The players' link rule, -Wl,--disable-new-dtags -Wl,-rpath,<prefix>/lib (DT_RPATH): the
    loader takes the prefix's libSDL3.so.0 before LD_LIBRARY_PATH's. A plain -rpath (DT_RUNPATH)
    is the control: there LD_LIBRARY_PATH's wins, so the check can fail."""
    for name in ("private", "elsewhere"):
        d = tmp_path / name
        d.mkdir()
        (d / "lib.c").write_text(f'const char *which_sdl(void) {{ return "{name}"; }}\n')
        r = subprocess.run([GCC, "-shared", "-fPIC", "-Wl,-soname,libSDL3.so.0", "-o", str(d / "libSDL3.so.0"),
                            str(d / "lib.c")], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    (tmp_path / "main.c").write_text('#include <stdio.h>\nconst char *which_sdl(void);\n'
                                     'int main(void) { puts(which_sdl()); return 0; }\n')
    env = dict(os.environ, LD_LIBRARY_PATH=str(tmp_path / "elsewhere"))
    seen = {}
    for rule, flags in (("rpath", ["-Wl,--disable-new-dtags"]), ("runpath", ["-Wl,--enable-new-dtags"])):
        exe = tmp_path / f"main-{rule}"
        r = subprocess.run([GCC, str(tmp_path / "main.c"), "-o", str(exe), f"-L{tmp_path / 'private'}", *flags,
                            f"-Wl,-rpath,{tmp_path / 'private'}", "-l:libSDL3.so.0"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        seen[rule] = subprocess.run([str(exe)], capture_output=True, text=True, env=env).stdout.strip()
    assert seen == {"rpath": "private", "runpath": "elsewhere"}
    for script in ("host/player/build.sh", "host/d3d9gpu/build.sh"):
        text = (ROOT / script).read_text()
        assert '-Wl,--disable-new-dtags -Wl,-rpath,"$I/lib" -lSDL3' in text, script
        assert 'does not load SDL3 from $I/lib by DT_RPATH alone' in text, script
    # and every SDL-linking build the players' scripts make (test_synth.sh's isolated bad-config
    # player among them) gives DT_RPATH: every -rpath, in any spelling (-Wl,-rpath=,
    # -Xlinker -rpath, --rpath), is "-Wl,--disable-new-dtags -Wl,-rpath,", and nothing enables
    # the new tags or sets a path by -R
    scripts = sorted((ROOT / "host").rglob("*.sh"))
    assert len([s for s in scripts if "-rpath" in s.read_text()]) >= 3
    for script in scripts:
        for n, line in enumerate(script.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert "--enable-new-dtags" not in line and "-Wl,-R" not in line, (script, n, line)
            for m in re.finditer(r"-{1,2}rpath", line):
                assert m.group() == "-rpath" and line[:m.start()].endswith("-Wl,--disable-new-dtags -Wl,") \
                    and line[m.end():m.end() + 1] == ",", (script, n, line)


# Each build script's link-rule check (readelf after the link): the binaries it covers.
LINK_RULE = {"host/player/build.sh": ["sa2_player", "dxvk_smoke"],
             "host/d3d9gpu/build.sh": ["sa2_player_gpu", "d3d9gpu_clear_matrix", "d3d9gpu_draw_matrix"]}


def link_rule_check(script: str) -> str:
    """The script's link-rule loop, cut from it as it is."""
    text = (ROOT / script).read_text()
    at = text.index('by DT_RPATH alone"; exit 2')
    start = text.rindex("\nfor bin in ", 0, at) + 1
    return text[start:text.index("\ndone\n", at) + len("\ndone\n")]


@pytest.mark.skipif(GCC is None, reason="no gcc")
def test_each_build_script_s_link_rule_check_refuses_runpath_and_another_rpath(tmp_path):
    """Each build script's own check, run by bash on stand-in binaries: it covers its script's
    SDL-linking binaries, passes when all load SDL3 from $I/lib by DT_RPATH, and exits 2
    when any one has DT_RUNPATH or an rpath elsewhere."""
    install, other = tmp_path / "install" / "lib", tmp_path / "other" / "lib"
    for d in (install, other):
        d.mkdir(parents=True)
    (tmp_path / "lib.c").write_text("int which_sdl(void) { return 1; }\n")
    r = subprocess.run([GCC, "-shared", "-fPIC", "-Wl,-soname,libSDL3.so.0", "-o", str(install / "libSDL3.so.0"),
                        str(tmp_path / "lib.c")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (tmp_path / "main.c").write_text("int which_sdl(void);\nint main(void) { return which_sdl() - 1; }\n")
    exe = {}
    for name, flags in (("rpath", ["-Wl,--disable-new-dtags", f"-Wl,-rpath,{install}"]),
                        ("runpath", ["-Wl,--enable-new-dtags", f"-Wl,-rpath,{install}"]),
                        ("elsewhere", ["-Wl,--disable-new-dtags", f"-Wl,-rpath,{other}"])):
        exe[name] = tmp_path / f"main-{name}"
        r = subprocess.run([GCC, str(tmp_path / "main.c"), "-o", str(exe[name]), f"-L{install}", *flags,
                            "-l:libSDL3.so.0"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    # A real ELF with both tags isolates the RUNPATH refusal from the missing-RPATH refusal.
    both = bytearray(exe["rpath"].read_bytes())
    assert both[:6] == b"\x7fELF\x02\x01", "this fixture requires little-endian ELF64"
    phoff = struct.unpack_from("<Q", both, 32)[0]
    entsize, count = struct.unpack_from("<HH", both, 54)
    dynamic = [struct.unpack_from("<IIQQQQQQ", both, phoff + i * entsize) for i in range(count)]
    segment = next(row for row in dynamic if row[0] == 2)
    slots = list(range(segment[2], segment[2] + segment[5], 16))
    rpath = next(struct.unpack_from("<QQ", both, at)[1] for at in slots
                 if struct.unpack_from("<Q", both, at)[0] == 15)
    null = next(at for at in slots if struct.unpack_from("<Q", both, at)[0] == 0)
    assert null + 16 in slots and struct.unpack_from("<Q", both, null + 16)[0] == 0
    struct.pack_into("<QQ", both, null, 29, rpath)
    exe["both-tags"] = tmp_path / "main-both-tags"
    exe["both-tags"].write_bytes(both)
    tags = subprocess.run(["readelf", "-d", str(exe["both-tags"])], capture_output=True, text=True, check=True).stdout
    assert f"Library rpath: [{install}]" in tags and "(RUNPATH)" in tags
    for script, names in LINK_RULE.items():
        block = link_rule_check(script)
        assert block.startswith(f"for bin in {' '.join(names)}; do\n"), (script, block)
        cases = [(None, None)] + [(bad, at) for bad in ("runpath", "elsewhere", "both-tags") for at in range(len(names))]
        for bad, at in cases:
            out = tmp_path / f"out-{Path(script).parent.name}-{bad}-{at}"
            out.mkdir()
            for i, name in enumerate(names):
                shutil.copy2(exe[bad if i == at else "rpath"], out / name)
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block], capture_output=True, text=True,
                               env=dict(os.environ, OUT=str(out), I=str(tmp_path / "install")), timeout=60)
            if bad is None:
                assert r.returncode == 0, (script, r.stdout + r.stderr)
            else:
                assert r.returncode == 2 and r.stdout.strip() == (
                    f"{names[at]} does not load SDL3 from {tmp_path / 'install'}/lib by DT_RPATH alone"), \
                    (script, bad, at, r.stdout + r.stderr)


def fake_checkout(base: Path, script: str, private: Path | None) -> Path:
    """A checkout holding `script` and an empty tools/pins.json; its private/ a link to `private`,
    or, with None, a directory of its own."""
    target = base / script
    target.parent.mkdir(parents=True)
    shutil.copy2(ROOT / script, target)
    (base / "tools").mkdir()
    (base / "tools" / "pins.json").write_text("{}")
    if private is None:
        (base / "private" / "generated").mkdir(parents=True)
    else:
        (base / "private").symlink_to(private)
    return target


@pytest.mark.parametrize("script", ["host/player/build.sh", "host/d3d9gpu/build.sh"])
def test_a_build_script_s_default_out_stays_inside_its_own_checkout(tmp_path, script):
    """The confirm's R3 and the second review's finding 5: with SA2_REPO and OUT unset, the
    repository is the script's own checkout, and a default OUT that resolves outside it is refused
    (exit 2) before anything is written. With OUT named, or a private/ of its own, the build goes
    past that point and stops at its next check (no stream header or SDL3 here)."""
    shared = tmp_path / "shared-private"
    (shared / "generated").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_") and k not in ("OUT", "PYTHON")}
    linked = tmp_path / "linked"
    target = fake_checkout(linked, script, shared)
    r = subprocess.run(["bash", str(target)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2 and r.stdout.strip() == (
        f"the default OUT {linked}/private/generated/s5/play/bin resolves to {shared}/generated/s5/play/bin, "
        f"outside this checkout {linked} (a linked private/): set OUT"), r.stdout + r.stderr
    named = subprocess.run(["bash", str(target)], capture_output=True, text=True, timeout=60,
                           env=dict(env, OUT=str(tmp_path / "out")))
    own = tmp_path / "own"
    alone = subprocess.run(["bash", str(fake_checkout(own, script, None))], capture_output=True, text=True, env=env,
                           timeout=60)
    for run, checkout in ((named, linked), (alone, own)):
        assert run.returncode == 2 and "outside this checkout" not in run.stdout, run.stdout + run.stderr
        assert run.stdout.startswith(("no sa2_stream.h in ", f"no SDL3 under {checkout}/private/s5-tools/install")), \
            run.stdout
    assert not (shared / "generated" / "s5").exists() and not (tmp_path / "out").exists()


def test_player_env_defaults_to_its_own_checkout_and_unsets_the_dynamic_api(tmp_path):
    checkout = tmp_path / "checkout"
    target = checkout / "host" / "player" / "player_env.sh"
    target.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "host" / "player" / "player_env.sh", target)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SA2_", "DXVK_"))}
    env["SDL3_DYNAMIC_API"] = str(tmp_path / "other" / "libSDL3.so.0")
    r = subprocess.run(["bash", "-c", 'source "$0" && printf "%s|%s|%s\n" "${SDL3_DYNAMIC_API-unset}" "$SA2_REPO" '
                        '"$SA2_DXVK_D3D9"', str(target)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == (f"unset|{checkout}|{checkout}/private/s5-tools/install/lib/x86_64-linux-gnu/"
                                "libdxvk_d3d9.so.0")


# player.cpp's refusal, as it reads in the source.
DYNAMIC_API_REFUSAL = ('    if (getenv("SDL3_DYNAMIC_API"))\n'
                       '        return device_failure("SDL3_DYNAMIC_API is set: ')
SDL_CALL = re.compile(r"\bSDL_\w+\s*\(")
LEXEME = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.S)


def blanked(source: str) -> str:
    """The source with its comments and the insides of its string and character literals made
    spaces, every offset kept, so a call is only ever read where it is code."""
    def blank(m: re.Match) -> str:
        s = m.group()
        if s.startswith("/"):
            return re.sub(r"[^\n]", " ", s)
        return s[0] + " " * (len(s) - 2) + s[-1]
    return LEXEME.sub(blank, source)


def player_methods(code: str) -> dict[str, tuple[int, int]]:
    """Each Player:: definition's body as (start, end) offsets: a definition at column 0, up to the
    first "}" alone on its line."""
    return {m.group(1): (m.start(2), m.end(2)) for m in re.finditer(
        r"^[A-Za-z][\w:<>*&, ]* Player::(\w+)\([^)]*\)[^;{\n]*\{\n(.*?)^\}$", code, re.M | re.S)}


def closing_brace(code: str, at: int) -> int:
    """The offset of the "}" that closes the "{" at `at`."""
    depth = 0
    for i in range(at, len(code)):
        depth += {"{": 1, "}": -1}.get(code[i], 0)
        if depth == 0:
            return i
    raise AssertionError(f"no closing brace for the one at {at}")


def test_the_player_refuses_sdl_s_dynamic_api_before_any_sdl_call_on_both_start_paths():
    """I4's third confirm (its A11): player.cpp without its SDL3_DYNAMIC_API refusal passed every
    gate test; only test_synth.sh held it. Read from the source, like the sdl_mark.h include above:
    validate_host_files() refuses SDL3_DYNAMIC_API, returning device_failure, before any SDL call
    of its own; and each of run()'s two start paths calls validate_host_files() before any SDL
    call. An SDL call is an SDL_ function called in code, or a call of a Player method that makes
    one, itself or through another. Each path is read in run()'s text: the live stream's is
    everything before its validate_host_files() inside `if (o_.explicit_live()) {`; the file's is
    everything before that if, then everything after its else block up to its own
    validate_host_files(). A statement that returns ends the path, so it is not read."""
    source = (ROOT / "host/player/player.cpp").read_text()
    code = blanked(source)
    methods = player_methods(code)
    calls = {name: set(re.findall(r"\b(\w+)\s*\(", code[a:b])) & set(methods) for name, (a, b) in methods.items()}
    sdl = {name for name, (a, b) in methods.items() if SDL_CALL.search(code[a:b])}
    while grown := {name for name in methods if calls[name] & sdl} - sdl:
        sdl |= grown
    assert {"setup_sdl", "setup_window", "setup_audio", "validate_host_files", "run"} <= sdl, sorted(sdl)

    def sdl_calls(text: str) -> list[str]:
        text = re.sub(r"\breturn\b[^;]*;", "", text)
        return SDL_CALL.findall(text) + [f"{m}(" for m in re.findall(r"\b(\w+)\s*\(", text) if m in sdl]

    assert source.count(DYNAMIC_API_REFUSAL) == 1, "player.cpp does not refuse SDL3_DYNAMIC_API as it did"
    refusal = source.index(DYNAMIC_API_REFUSAL)
    start, end = methods["validate_host_files"]
    assert start <= refusal < end, "the SDL3_DYNAMIC_API refusal is not in validate_host_files()"
    assert sdl_calls(code[start:refusal]) == [], "validate_host_files() makes an SDL call before its refusal"

    start, end = methods["run"]
    body = code[start:end]
    starts = [m.start() for m in re.finditer(r"\bvalidate_host_files\s*\(\s*\)", body)]
    assert len(starts) == 2, f"run() calls validate_host_files() {len(starts)} times, not once on each start path"
    live_if = body.index("if (o_.explicit_live()) {")
    live_end = closing_brace(body, body.index("{", live_if))
    else_open = re.compile(r"\s*else\s*\{").match(body, live_end + 1)
    assert else_open, "run()'s live start is not followed by the file's (`} else {`)"
    file_from = closing_brace(body, else_open.end() - 1)
    assert live_if < starts[0] < live_end and file_from < starts[1], (
        "run()'s two validate_host_files() calls are not one in the live start and one after the file's open")
    paths = {"live": body[:starts[0]], "file": body[:live_if] + body[else_open.start():starts[1]]}
    assert {name: sdl_calls(text) for name, text in paths.items()} == {"live": [], "file": []}


def test_the_builder_refuses_an_unknown_configuration_before_it_does_anything():
    r = subprocess.run(["bash", str(ROOT / "tools/s5/build_host_deps.sh"), "--sdl3=upstream"], capture_output=True,
                       text=True)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.splitlines() == ["build_host_deps.sh: unknown argument: --sdl3=upstream",
                                     "usage: tools/s5/build_host_deps.sh [--fetch] [--sdl3=patched|stock|system]"]


def test_patch_application_owns_storage_when_the_caller_tmpdir_is_a_file(tree, root, tmp_path):
    """An unusable caller temporary directory cannot redirect the two patch application calls."""
    bad_tmp = tmp_path / "caller-tmp-is-a-file"
    bad_tmp.write_text("caller state\n")
    r = subprocess.run([sys.executable, str(TOOL), "apply", "--root", str(root), "--tree", str(tree)],
                       capture_output=True, text=True, env=dict(os.environ, TMPDIR=str(bad_tmp)))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SDL3_PATCHES_APPLIED 1 " in r.stdout
    assert (tree / "include/SDL3/SDL_gpu.h").read_bytes().count(SENTINEL) > 0
    assert bad_tmp.read_text() == "caller state\n"
    assert not list(tree.parent.glob("sa2-gnu-patch-*"))
