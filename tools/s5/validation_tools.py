#!/usr/bin/env python3
"""Read-only identity proofs for the supplemental S5 validation instrument."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

class ValidationRefused(Exception):
    """A required source, installed-file or process identity proof failed."""

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValidationRefused(message)


def git_process(checkout, args):
    env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1"}
    argv = ["/usr/bin/git", "--no-replace-objects", "--no-lazy-fetch", "-c", "core.fsmonitor=false",
            "-c", "core.untrackedCache=false", "-c", "core.hooksPath=/dev/null",
            "-C", str(checkout), *args]
    return subprocess.check_output(argv, env=env, stderr=subprocess.PIPE)


def git_bytes(checkout, *args):
    # Only raw object and index reads are allowed here. Status and hash-object --path
    # may invoke a configured filter, and are deliberately not used.
    allowed = args in [("rev-parse", "HEAD"), ("rev-parse", "HEAD^{tree}"),
                       ("rev-parse", "FETCH_HEAD^{commit}"), ("rev-parse", "--show-object-format"),
                       ("ls-files", "-v", "-z"), ("ls-files", "--stage", "-z")]
    if len(args) == 3 and args[0] == "cat-file" and args[1] in ("tree", "commit"):
        allowed = len(args[2]) == 40 and all(c in "0123456789abcdef" for c in args[2])
    require(allowed, f"not an allowed read-only Git query: {args}")
    return git_process(checkout, args)


def git(checkout, *args):
    return git_bytes(checkout, *args).decode("utf-8").strip()


def object_id(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()


def authoritative_tree(checkout, tree, allow_submodules=False):
    files = {}
    directories = set()
    submodules = {}
    pending = [(tree, "")]
    while pending:
        oid, parent = pending.pop()
        data = git_bytes(checkout, "cat-file", "tree", oid)
        require(object_id("tree", data) == oid, f"corrupt authoritative tree object: {oid}")
        offset = 0
        while offset < len(data):
            end = data.index(b"\0", offset)
            mode, name = data[offset:end].split(b" ", 1)
            require(name and b"/" not in name and name not in (b".", b"..", b".git"), "unsafe tree entry name")
            child = data[end + 1:end + 21]
            require(len(child) == 20, "truncated tree object")
            offset = end + 21
            relative = parent + os.fsdecode(name)
            require(relative not in files and relative not in directories and relative not in submodules,
                    f"duplicate tree entry: {relative}")
            if mode == b"40000":
                directories.add(relative)
                pending.append((child.hex(), relative + "/"))
            else:
                if mode == b"160000" and allow_submodules:
                    submodules[relative] = child.hex()
                else:
                    require(mode in (b"100644", b"100755", b"120000"), f"nonregular or gitlink source entry: {relative}")
                    files[relative] = (mode.decode(), child.hex())
    return files, directories, submodules


def raw_regular_blob(path, expected_mode):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), f"nonregular source file: {path}")
    require(before.st_mode & 0o111 == int(expected_mode, 8) & 0o111, f"source executable mode differs: {path}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                f"source file changed while opening: {path}")
        digest = hashlib.sha1(b"blob " + str(opened.st_size).encode() + b"\0")
        length = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            length += len(chunk)
        after = os.fstat(stream.fileno())
        require(length == opened.st_size and (opened.st_size, opened.st_mtime_ns, opened.st_mode) ==
                (after.st_size, after.st_mtime_ns, after.st_mode), f"source file changed while hashing: {path}")
    return digest.hexdigest()


def raw_worktree(checkout, files, directories, submodules):
    observed = set()
    observed_submodules = set()
    inventory = []
    pending = [checkout]
    while pending:
        directory = pending.pop()
        for item in os.scandir(directory):
            path = Path(item.path)
            relative = str(path.relative_to(checkout))
            if relative == ".git":
                continue
            mode = item.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                if relative in submodules:
                    observed_submodules.add(relative)
                    continue
                require(relative in directories, f"extra source directory: {relative}")
                pending.append(path)
                continue
            require(relative in files, f"extra source file: {relative}")
            expected_mode, expected_blob = files[relative]
            if expected_mode == "120000":
                require(stat.S_ISLNK(mode), f"source symlink changed type: {relative}")
                target = path.resolve(strict=True)
                require(target.is_relative_to(checkout) and ".git" not in target.relative_to(checkout).parts,
                        f"source symlink escapes authoritative checkout: {relative}")
                target_relative = str(target.relative_to(checkout))
                require(target_relative in files or target_relative in directories,
                        f"source symlink selects untracked bytes: {relative}")
                if target_relative in files:
                    target_mode, target_blob = files[target_relative]
                    require(target_mode in ("100644", "100755") and target.is_file(),
                            f"source symlink terminal target is not a tracked regular file: {relative}")
                    require(raw_regular_blob(target, target_mode) == target_blob,
                            f"source symlink terminal bytes differ from HEAD: {relative}")
                actual_blob = object_id("blob", os.fsencode(os.readlink(path)))
            else:
                actual_blob = raw_regular_blob(path, expected_mode)
            require(actual_blob == expected_blob, f"raw source bytes differ from HEAD: {relative}")
            entry = {"path": relative, "mode": expected_mode, "git_blob": actual_blob}
            if expected_mode == "120000":
                entry.update({"link_target": os.readlink(path), "terminal_target": target_relative})
            inventory.append(entry)
            observed.add(relative)
    require(observed == set(files), f"missing source files: {sorted(set(files) - observed)[:3]}")
    require(observed_submodules == set(submodules), "missing source submodule directories")
    entries = "".join(f"{name}\0{mode}\0{oid}\0" for name, (mode, oid) in sorted(files.items()))
    return hashlib.sha256(os.fsencode(entries)).hexdigest(), sorted(inventory, key=lambda entry: entry["path"])


def _inspect_source(checkout, row, allow_submodules=False, family_root=None):
    family_root = family_root or checkout
    require(checkout.resolve(strict=True) == checkout, f"source checkout has a symlink ancestor: {checkout}")
    metadata = checkout / ".git"
    require(not metadata.is_symlink(), "source Git metadata is a symlink")
    if metadata.is_file() and allow_submodules and checkout != family_root:
        content = metadata.read_text().strip()
        require(content.startswith("gitdir: ") and "\n" not in content, "invalid submodule Git metadata file")
        reached = (checkout / content[8:]).resolve(strict=True)
        require(reached.is_dir() and reached.is_relative_to(family_root / ".git"), "submodule Git metadata escapes source family")
    else:
        require(metadata.is_dir(), "source Git metadata is not independent")
    require(git(checkout, "rev-parse", "--show-object-format") == "sha1", "source object format is not SHA-1")
    require(git(checkout, "rev-parse", "HEAD") == row["commit"], f"wrong commit: {checkout}")
    commit = git_bytes(checkout, "cat-file", "commit", row["commit"])
    require(object_id("commit", commit) == row["commit"], "corrupt authoritative commit object")
    headers = commit.split(b"\n\n", 1)[0].splitlines()
    tree = next(line[5:].decode("ascii") for line in headers if line.startswith(b"tree "))
    require(git(checkout, "rev-parse", "HEAD^{tree}") == tree, "HEAD tree resolution differs")
    epoch = int(next(line for line in headers if line.startswith(b"committer ")).rsplit(b" ", 2)[1])
    require("tree" not in row or row["tree"] == tree, "source tree differs from pin")
    files, directories, submodules = authoritative_tree(checkout, tree, allow_submodules)
    tags = git_bytes(checkout, "ls-files", "-v", "-z").split(b"\0")[:-1]
    require(all(x.startswith(b"H ") for x in tags),
            f"source index hides files: {checkout}")
    indexed = {}
    for entry in git_bytes(checkout, "ls-files", "--stage", "-z").split(b"\0")[:-1]:
        metadata, name = entry.split(b"\t", 1)
        mode, blob, stage = metadata.split(b" ")
        require(stage == b"0" and os.fsdecode(name) not in indexed, "unmerged or duplicate source index entry")
        indexed[os.fsdecode(name)] = (mode.decode("ascii"), blob.decode("ascii"))
    require(indexed == {**files, **{name: ("160000", oid) for name, oid in submodules.items()}},
            "source index differs from authoritative HEAD tree")
    digest, inventory = raw_worktree(checkout, files, directories, submodules)
    nested = []
    for name, commit in sorted(submodules.items()):
        child = checkout / name
        if (child / ".git").exists():
            value = _inspect_source(child, {"name": name, "commit": commit}, True, family_root)
            nested.append({"state": "initialized", **value})
        else:
            require(not any(child.iterdir()), f"uninitialized submodule has untracked source bytes: {name}")
            nested.append({"name": name, "path": str(child), "commit": commit, "state": "uninitialized-empty"})
    return {**row, "path": str(checkout), "tree": tree, "source_date_epoch": epoch,
            "raw_worktree_manifest_sha256": digest, "tracked_file_count": len(files),
            "worktree_files": inventory, "submodules": nested}



SOURCE_MANIFEST_SHA256 = "9786c8756ff2b84f84304c566deecfee3b97ebffa06c4dfc174b2e4693be3799"
SOURCE_POLICY = (
    {"name": "Vulkan-Headers", "key": "validation-350-vulkan-headers", "url": "https://github.com/KhronosGroup/Vulkan-Headers.git", "ref": "v1.4.350", "commit": "8864cdc896bbc2a9b6eb36b3218fc9ef57908d77"},
    {"name": "Vulkan-Utility-Libraries", "key": "validation-350-vulkan-utility-libraries", "url": "https://github.com/KhronosGroup/Vulkan-Utility-Libraries.git", "ref": "a96bd4933c6adfc21329d15e9999791a0153bbf3", "commit": "a96bd4933c6adfc21329d15e9999791a0153bbf3"},
    {"name": "SPIRV-Headers", "key": "validation-350-spirv-headers", "url": "https://github.com/KhronosGroup/SPIRV-Headers.git", "ref": "ad9184e76a66b1001c29db9b0a3e87f646c64de0", "commit": "ad9184e76a66b1001c29db9b0a3e87f646c64de0"},
    {"name": "SPIRV-Tools", "key": "validation-350-spirv-tools", "url": "https://github.com/KhronosGroup/SPIRV-Tools.git", "ref": "0539c81f69a3daeb706fd3477dca61435b475156", "commit": "0539c81f69a3daeb706fd3477dca61435b475156"},
    {"name": "Vulkan-ValidationLayers", "key": "validation-350-vulkan-validation-layers", "url": "https://github.com/KhronosGroup/Vulkan-ValidationLayers.git", "ref": "vulkan-sdk-1.4.350.1", "commit": "b9d4f9ead8d97c1bb0d174ea07d8aed8273818a5"},
)
SELECTED_KEY = "validation-350-vulkan-validation-layers"
ROLE_PATHS = {
    "Vulkan-Headers": {"vulkan_header": "include/vulkan/vulkan_core.h", "package_config": "VulkanHeadersConfig.cmake"},
    "Vulkan-Utility-Libraries": {"utility_header": "include/vulkan/utility/vk_safe_struct.hpp", "layer_settings_library": "lib/libVulkanLayerSettings.a", "safe_struct_library": "lib/libVulkanSafeStruct.a", "package_config": "VulkanUtilityLibrariesConfig.cmake"},
    "SPIRV-Headers": {"spirv_header": "include/spirv/unified1/spirv.h", "package_config": "SPIRV-HeadersConfig.cmake"},
    "SPIRV-Tools": {"spirv_tools_library": "lib/libSPIRV-Tools.a", "optimizer_library": "lib/libSPIRV-Tools-opt.a", "package_config": "SPIRV-ToolsConfig.cmake", "optimizer_package_config": "SPIRV-Tools-optConfig.cmake"},
    "Vulkan-ValidationLayers": {},
}
ALIASES = {
    "Vulkan-ValidationLayers": {"library": "lib/libVkLayer_khronos_validation.so", "manifest": "share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json"},
    "SPIRV-Tools": {"binary": "bin/spirv-val", "disassembler": "bin/spirv-dis", "assembler": "bin/spirv-as", "optimizer": "bin/spirv-opt"},
}
PROTECTED_TOOLS = ("spirv-tools", "spirv-cross", "sdl-shadercross", "vulkan-validation-layers", "sdl3", "sdl3-stock", "dxvk")
PROTECTED_SOURCE_TOOLS = ("spirv-headers", "spirv-tools", "spirv-cross", "sdl-shadercross", "vulkan-utility-libraries", "robin-hood-hashing", "vulkan-validation-layers", "dxvk")


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"duplicate JSON key: {key}")
            value[key] = item
        return value
    return json.loads(path.read_text(), object_pairs_hook=unique)


def source_manifest(root):
    path = root / "tools/s5/validation-sources-350.json"
    require(sha(path) == SOURCE_MANIFEST_SHA256, "source manifest bytes changed")
    value = read_json(path)
    require(value.get("schema") == "sa2-validation-350-sources-v1" and value.get("sources") == list(SOURCE_POLICY), "source manifest policy changed")
    return value


def stable_base(root):
    anchor = (root / "private/s5-tools").resolve(strict=True)
    require(anchor.is_dir(), "s5-tools anchor is not a directory")
    base = anchor / "validation-350"
    if base.exists() or base.is_symlink():
        require(not base.is_symlink() and base.is_dir(), "stable validation directory is linked/nonregular")
    return base


def owned_path(root, relative, *, must_exist=True, allow_file_link=False):
    require(isinstance(relative, str) and relative and "\\" not in relative, "invalid owned path")
    parts = Path(relative).parts
    require(parts[:3] == ("private", "s5-tools", "validation-350") and all(p not in (".", "..") for p in parts), "path is outside stable validation layout")
    require(str(Path(relative)) == relative, "owned path is not a literal canonical relative name")
    base = stable_base(root)
    reached = base
    for index, name in enumerate(parts[3:]):
        reached = reached / name
        terminal = index == len(parts[3:]) - 1
        require(not reached.is_symlink() or (terminal and allow_file_link), f"linked owned path component: {reached}")
    if must_exist:
        require(reached.exists(), f"missing owned file/directory: {reached}")
    return reached


def inspect_source(checkout, expected):
    try:
        require(isinstance(expected, dict) and re.fullmatch(r"[0-9a-f]{40}", expected.get("commit", "")), "missing/invalid source commit")
        if "tree" in expected:
            require(re.fullmatch(r"[0-9a-f]{40}", expected["tree"]), "invalid source tree")
        return _inspect_source(Path(checkout), expected)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.CalledProcessError) as error:
        raise ValidationRefused(f"source proof failed: {error}") from error


def installed_file(prefix, relative):
    require(isinstance(relative, str) and relative and str(Path(relative)) == relative and not Path(relative).is_absolute() and "\\" not in relative and all(p not in (".", "..") for p in Path(relative).parts), "invalid install-relative path")
    path = prefix
    for i, component in enumerate(Path(relative).parts):
        path = path / component
        require(not path.is_symlink() or i == len(Path(relative).parts) - 1, "installed directory link refused")
    actual = path.resolve(strict=True)
    require(actual.is_relative_to(prefix) and actual.is_file(), "installed file escapes prefix or is nonregular")
    return path


def role_matches(path, expected):
    if "/" in expected:
        return path == expected
    parts = Path(path).parts
    return parts[-1] == expected and (parts[:2] == ("lib", "cmake") or parts[:2] == ("share", "cmake")) and len(parts) == 4


def verify_pin(root, key, entry, *, sources_only):
    try:
        root = Path(root)
        require(isinstance(entry, dict) and entry.get("unpinned") is not True, "supplemental validation key cannot be unpinned")
        manifest = source_manifest(root)
        candidates = [row for row in manifest["sources"] if row["key"] == key]
        require(len(candidates) == 1, f"unregistered validation key: {key}")
        row = candidates[0]
        source_relative = f"private/s5-tools/validation-350/sources/{row['name']}"
        require(entry.get("path") == source_relative, "source path differs from keyed stable path")
        require(entry.get("commit") == row["commit"] and entry.get("version") == row["ref"], "source commit/version differs from approved policy")
        require(isinstance(entry.get("tree"), str) and re.fullmatch(r"[0-9a-f]{40}", entry["tree"]), "required source tree missing/invalid")
        proof = inspect_source(owned_path(root, source_relative), {**row, "tree": entry["tree"]})
        if sources_only:
            return f"commit {proof['commit']} tree {proof['tree']} raw {proof['raw_worktree_manifest_sha256']}"
        prefix_relative = entry.get("prefix")
        require(isinstance(prefix_relative, str) and re.fullmatch(r"private/s5-tools/validation-350/runs/[a-z0-9][a-z0-9-]{0,63}/install", prefix_relative), "required stable run prefix missing/invalid")
        prefix = owned_path(root, prefix_relative)
        require(prefix.is_dir(), "install prefix is not a directory")
        files = entry.get("installed_files")
        require(isinstance(files, dict) and bool(files), "required nonempty installed_files missing")
        for relative, digest in files.items():
            require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "invalid installed-file digest")
            require(sha(installed_file(prefix, relative)) == digest, f"installed bytes differ: {relative}")
        roles = entry.get("roles")
        require(isinstance(roles, dict), "required roles map missing")
        for role, semantic in ROLE_PATHS[row["name"]].items():
            relative = roles.get(role)
            require(isinstance(relative, str) and relative in files and role_matches(relative, semantic), f"required semantic installed role missing: {role}")
        if row["name"] == "Vulkan-Headers":
            header = installed_file(prefix, roles["vulkan_header"])
            require(re.search(r"^#define\s+VK_HEADER_VERSION\s+350\s*$", header.read_text(), re.M), "installed Vulkan header revision differs from 350")
        for field, relative in ALIASES.get(row["name"], {}).items():
            require(entry.get(field) == prefix_relative + "/" + relative, f"required exact {field} alias missing/invalid")
            require(relative in files and entry.get(field + "_sha256") == files[relative], f"required {field} alias digest/map mismatch")
        if row["name"] == "Vulkan-ValidationLayers":
            library = installed_file(prefix, ALIASES[row["name"]]["library"])
            layer_manifest = installed_file(prefix, ALIASES[row["name"]]["manifest"])
            manifest_identity(prefix, layer_manifest, library, entry["library_sha256"], {"LD_LIBRARY_PATH": str(library.parent)})
        return f"commit {proof['commit']} tree {proof['tree']} installed {len(files)} files"
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValidationRefused(f"validation pin proof failed: {error}") from error


def manifest_identity(prefix, manifest, library, library_sha256, loader_environment):
    try:
        prefix, manifest, library = Path(prefix), Path(manifest), Path(library)
        require(prefix.resolve(strict=True) == prefix and prefix.is_dir(), "install prefix has linked ancestors")
        owned_library = installed_file(prefix, str(library.relative_to(prefix)))
        owned_manifest = installed_file(prefix, str(manifest.relative_to(prefix)))
        actual = owned_library.resolve(strict=True)
        layer = read_json(owned_manifest)["layer"]
        require(layer.get("name") == "VK_LAYER_KHRONOS_validation" and layer.get("api_version") == "1.4.350", "wrong layer identity/API")
        name = layer.get("library_path")
        require(isinstance(name, str) and name and "\0" not in name and "\\" not in name, "invalid Linux layer library path")
        if "/" in name:
            selected = Path(name) if Path(name).is_absolute() else owned_manifest.parent / name
            resolution = "absolute" if Path(name).is_absolute() else "manifest-relative"
        else:
            require(name == library.name and loader_environment.get("LD_LIBRARY_PATH") == str(library.parent), "bare layer SONAME needs one exact owned LD_LIBRARY_PATH")
            selected = library.parent / name
            resolution = "bare-soname-with-exact-LD_LIBRARY_PATH"
        require(selected.resolve(strict=True) == actual and selected.parent.resolve(strict=True).is_relative_to(prefix), "manifest selects a different layer library")
        require(sha(actual) == library_sha256, "manifest layer bytes differ from selected pin")
        return {"api_version": "1.4.350", "library_path": name, "resolution": resolution, "resolved_library": str(actual), "resolved_library_sha256": library_sha256, "required_loader_environment": dict(loader_environment)}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValidationRefused(f"manifest proof failed: {error}") from error


def selected_layer(root):
    try:
        root = Path(root)
        entry = read_json(root / "tools/pins.json")["tools"][SELECTED_KEY]
        verify_pin(root, SELECTED_KEY, entry, sources_only=False)
        prefix = owned_path(root, entry["prefix"])
        library = installed_file(prefix, ALIASES["Vulkan-ValidationLayers"]["library"])
        manifest = installed_file(prefix, ALIASES["Vulkan-ValidationLayers"]["manifest"])
        environment = {"LD_LIBRARY_PATH": str(library.parent), "VK_ADD_LAYER_PATH": str(manifest.parent)}
        identity = manifest_identity(prefix, manifest, library, entry["library_sha256"], environment)
        actual = library.resolve(strict=True)
        status = actual.stat()
        expected = {"realpath": str(actual), "device": status.st_dev, "inode": status.st_ino, "sha256": entry["library_sha256"], "api_version": identity["api_version"]}
        return {"key": SELECTED_KEY, "prefix": str(prefix), "library": str(library), "manifest": str(manifest), "library_sha256": entry["library_sha256"], "manifest_sha256": entry["manifest_sha256"], "api_version": identity["api_version"], "loader_environment": environment, "manifest_identity": identity, "expected_mapping": expected}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValidationRefused(f"selected layer proof failed: {error}") from error


def verify_loaded_layer(observed, selected):
    try:
        require(selected.get("key") == SELECTED_KEY, "wrong selected layer key")
        expected = selected["expected_mapping"]
        require(isinstance(observed, dict) and type(observed.get("pid")) is int and observed["pid"] > 0, "missing live layer process")
        for field in ("realpath", "device", "inode", "sha256", "api_version"):
            require(observed.get(field) == expected[field] and type(observed.get(field)) is type(expected[field]), f"observed loaded layer differs: {field}")
        path = Path(expected["realpath"])
        status = path.stat()
        require(path.resolve(strict=True) == path and (status.st_dev, status.st_ino) == (expected["device"], expected["inode"]) and sha(path) == expected["sha256"], "selected library identity changed")
        rows = Path(f"/proc/{observed['pid']}/maps").read_text().splitlines()
        matching = []
        for line in rows:
            fields = line.split(None, 5)
            if len(fields) != 6:
                continue
            major, minor = [int(x, 16) for x in fields[3].split(":")]
            if (os.makedev(major, minor), int(fields[4]), fields[5]) == (expected["device"], expected["inode"], expected["realpath"]):
                matching.append(line)
        require(matching, "actual live process has no selected layer file mapping")
        return {**observed, "verified": True, "matched_map_lines": matching, "selected_key": selected["key"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValidationRefused(f"loaded layer proof failed: {error}") from error


def record_file(path):
    actual = path.resolve(strict=True)
    require(actual.is_file(), f"protected file is nonregular: {path}")
    return {"path": str(path), "realpath": str(actual), "sha256": sha(actual), "bytes": actual.stat().st_size}


def protected_archives(value, trail=""):
    """Only archive/patch path+sha pairs inside the selected old tool entries."""
    rows = []
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            require(isinstance(value["path"], str) and isinstance(value["sha256"], str), "invalid protected source path/hash pair")
            rows.append((trail + "path", value["path"], value["sha256"]))
        for name, child in value.items():
            if isinstance(child, (dict, list)):
                rows.extend(protected_archives(child, trail + name + "."))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(protected_archives(child, trail + str(index) + "."))
    return rows


def protected_inputs(root, pins):
    rows = [record_file(root / "tools/pins.json"), record_file(root / "host/player/dxvk.conf")]
    for key in PROTECTED_TOOLS:
        entry = pins["tools"][key]
        pairs = protected_archives(entry)
        pairs += [(field[:-7], entry[field[:-7]], digest) for field, digest in entry.items() if field.endswith("_sha256") and isinstance(digest, str) and isinstance(entry.get(field[:-7]), str)]
        for field, relative, digest in pairs:
            item = record_file(root / relative)
            require(item["sha256"] == digest, f"existing {key}.{field} differs from pin")
            rows.append({"tool": key, "field": field, **item})
    return rows


def protected_checkouts(root, pins):
    rows = []
    for key in PROTECTED_SOURCE_TOOLS:
        entry = pins["tools"][key]
        value = _inspect_source((root / entry["path"]).resolve(strict=True), {"name": key, "commit": entry["commit"], **({"tree": entry["tree"]} if "tree" in entry else {})}, allow_submodules=True)
        rows.append(value)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["selected-layer"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--shell", action="store_true")
    args = parser.parse_args()
    proof = selected_layer(args.root)
    if not args.shell:
        print(json.dumps(proof, indent=2))
        return
    exports = {"SA2_VALIDATION_KEY": proof["key"], "SA2_VALIDATION_PREFIX": proof["prefix"], "SA2_VALIDATION_LIBRARY": proof["library"], "SA2_VALIDATION_MANIFEST": proof["manifest"], "SA2_VALIDATION_LIBRARY_SHA256": proof["library_sha256"], "SA2_VALIDATION_MANIFEST_SHA256": proof["manifest_sha256"], "SA2_VALIDATION_API_VERSION": proof["api_version"], **proof["loader_environment"]}
    for name, value in exports.items():
        print(f"export {name}={shlex.quote(value)}")


if __name__ == "__main__":
    try:
        main()
    except ValidationRefused as error:
        print(f"VALIDATION_FAIL {error}", file=sys.stderr)
        sys.exit(1)
