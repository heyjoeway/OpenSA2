"""Source-free conformance controls; Git objects are Python-authored fixture bytes.

No test fetches or configures/builds a source. Fixture policy substitution only names
these tiny authored commits and never changes the production manifest or policy.
"""
import ast
import hashlib
import importlib.util
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools/s5"))
import validation_tools as v
import fetch_validation_sources as fetcher
import build_validation_tools as builder


@pytest.fixture
def owned():
    base = Path(os.environ.get("SA2_VALIDATION_TEST_ARTIFACTS", str(REPO / "private/generated/s5/validation-helper-controls")))
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="fixture-", dir=base))


def repository(base, name, files, extra_config=""):
    """Write tiny fixture objects/index as test bytes, never through a mutating Git command."""
    checkout = base / name
    checkout.mkdir()
    metadata = checkout / ".git"
    (metadata / "objects").mkdir(parents=True)
    (metadata / "refs").mkdir()
    (metadata / "info").mkdir()
    (metadata / "config").write_text("[core]\n repositoryformatversion = 0\n bare = false\n filemode = true\n" + extra_config)

    def obj(kind, body):
        data = kind.encode() + b" " + str(len(body)).encode() + b"\0" + body
        oid = hashlib.sha1(data).hexdigest()
        directory = metadata / "objects" / oid[:2]
        directory.mkdir(exist_ok=True)
        (directory / oid[2:]).write_bytes(zlib.compress(data))
        return bytes.fromhex(oid)

    hierarchy = {}
    indexed = []
    for relative, content, mode in sorted(files):
        destination = checkout / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if mode == 0o120000:
            destination.symlink_to(os.fsdecode(content))
        elif mode == 0o160000:
            destination.mkdir()
        else:
            destination.write_bytes(content)
            destination.chmod(mode & 0o777)
        oid = bytes.fromhex(content.decode() if content else "1" * 40) if mode == 0o160000 else obj("blob", content)
        current = hierarchy
        parts = relative.split("/")
        for parent in parts[:-1]:
            current = current.setdefault(parent, {})
        current[parts[-1]] = (mode, oid)
        observed = destination.lstat()
        values = [int(observed.st_ctime), observed.st_ctime_ns % 10**9,
                  int(observed.st_mtime), observed.st_mtime_ns % 10**9,
                  observed.st_dev, observed.st_ino, mode, observed.st_uid, observed.st_gid, observed.st_size]
        entry = struct.pack(">10I", *[x & 0xffffffff for x in values]) + oid + struct.pack(">H", len(os.fsencode(relative)))
        entry += os.fsencode(relative) + b"\0"
        entry += b"\0" * (-len(entry) % 8)
        indexed.append(entry)

    def tree(node):
        body = bytearray()
        for name, value in sorted(node.items()):
            mode, oid = (0o40000, tree(value)) if isinstance(value, dict) else value
            body.extend(f"{mode:o} ".encode() + os.fsencode(name) + b"\0" + oid)
        return obj("tree", bytes(body))

    tree_id = tree(hierarchy)
    commit = obj("commit", b"tree " + tree_id.hex().encode() + b"\nauthor Fixture <fixture@example.invalid> 12345 +0000\ncommitter Fixture <fixture@example.invalid> 12345 +0000\n\nfixture\n")
    (metadata / "HEAD").write_text(commit.hex() + "\n")
    index = b"DIRC" + struct.pack(">II", 2, len(indexed)) + b"".join(indexed)
    (metadata / "index").write_bytes(index + hashlib.sha1(index).digest())
    return checkout, {"name": name, "commit": commit.hex()}



def install(root, files):
    prefix = root / "private/s5-tools/validation-350/runs/fixture/install"
    prefix.mkdir(parents=True)
    for relative, data in files.items():
        path = prefix / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return prefix


def layer_manifest(name="libVkLayer_khronos_validation.so", api="1.4.350"):
    return json.dumps({"layer": {"name": "VK_LAYER_KHRONOS_validation", "api_version": api, "library_path": name}}).encode()


def assembled(owned, monkeypatch):
    root = owned / "root"
    tools = root / "tools/s5"
    tools.mkdir(parents=True)
    for name in ("validation_tools.py", "fetch_validation_sources.py", "build_validation_tools.py"):
        (tools / name).write_bytes((REPO / "tools/s5" / name).read_bytes())
    source_dir = root / "private/s5-tools/validation-350/sources"
    source_dir.mkdir(parents=True)
    rows, proofs = [], []
    policy = json.loads(json.dumps(v.SOURCE_POLICY))
    for row in policy:
        files = [("source.cpp", b"int answer = 1;\n", 0o100644)]
        if row["name"] == "Vulkan-ValidationLayers":
            known = {"repos": [{"name": x["name"], "url": x["url"], "commit": x["ref"]} for x in policy[:4]]}
            files += [("scripts/known_good.json", json.dumps(known).encode(), 0o100644)]
        checkout, identity = repository(source_dir, row["name"], files)
        row["commit"] = identity["commit"]
        proof = v.inspect_source(checkout, row)
        rows.append(row)
        proofs.append(proof)
    manifest = {"schema": "sa2-validation-350-sources-v1", "scope": "validation-350-five-sources", "sources": rows}
    manifest_path = tools / "validation-sources-350.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(v, "SOURCE_POLICY", tuple(rows))
    monkeypatch.setattr(v, "SOURCE_MANIFEST_SHA256", v.sha(manifest_path))
    prefix_relative = "private/s5-tools/validation-350/runs/fixture/install"
    files = {
        "include/vulkan/vulkan_core.h": b"#define VK_HEADER_VERSION 350\n",
        "share/cmake/VulkanHeaders/VulkanHeadersConfig.cmake": b"fixture package\n",
        "include/vulkan/utility/vk_safe_struct.hpp": b"fixture header\n",
        "lib/libVulkanLayerSettings.a": b"fixture archive\n",
        "lib/libVulkanSafeStruct.a": b"fixture archive\n",
        "lib/cmake/VulkanUtilityLibraries/VulkanUtilityLibrariesConfig.cmake": b"fixture package\n",
        "include/spirv/unified1/spirv.h": b"fixture header\n",
        "share/cmake/SPIRV-Headers/SPIRV-HeadersConfig.cmake": b"fixture package\n",
        "lib/libSPIRV-Tools.a": b"fixture archive\n",
        "lib/libSPIRV-Tools-opt.a": b"fixture archive\n",
        "lib/cmake/SPIRV-Tools/SPIRV-ToolsConfig.cmake": b"fixture package\n",
        "lib/cmake/SPIRV-Tools-opt/SPIRV-Tools-optConfig.cmake": b"fixture package\n",
        "lib/libVkLayer_khronos_validation.so": b"fixture mapped file, never a compiled layer\n",
        "share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json": layer_manifest(),
        **{path: b"fixture executable\n" for path in v.ALIASES["SPIRV-Tools"].values()},
    }
    prefix = install(root, files)
    entries = {}
    for row, proof in zip(rows, proofs, strict=True):
        roles = builder.discover_roles(row["name"], files)
        selected = set(roles.values()) | set(v.ALIASES.get(row["name"], {}).values())
        entry = {"path": f"private/s5-tools/validation-350/sources/{row['name']}", "commit": row["commit"], "tree": proof["tree"], "version": row["ref"], "prefix": prefix_relative, "installed_files": {name: v.sha(prefix / name) for name in selected}, "roles": roles}
        for field, name in v.ALIASES.get(row["name"], {}).items():
            entry[field] = prefix_relative + "/" + name
            entry[field + "_sha256"] = v.sha(prefix / name)
        entries[row["key"]] = entry
    (root / "tools/pins.json").write_text(json.dumps({"tools": entries}))
    return root, prefix, rows, proofs, entries


def test_source_raw_positive_and_tree_mode_controls(owned):
    checkout, expected = repository(owned, "source", [("nested/source.cpp", b"source\n", 0o100644), ("run.sh", b"#!/bin/sh\n", 0o100755)])
    proof = v.inspect_source(checkout, expected)
    assert proof["tracked_file_count"] == 2 and proof["source_date_epoch"] == 12345
    with pytest.raises(v.ValidationRefused, match="tree differs"):
        v.inspect_source(checkout, {**expected, "tree": "f" * 40})
    (checkout / "run.sh").chmod(0o644)
    with pytest.raises(v.ValidationRefused, match="executable mode"):
        v.inspect_source(checkout, expected)


@pytest.mark.parametrize("target", ["../outside.cpp", ".git/HEAD", "untracked.cpp"])
def test_source_link_counterexamples(owned, target):
    (owned / "outside.cpp").write_bytes(b"external\n")
    checkout, row = repository(owned, "source", [("alias.cpp", os.fsencode(target), 0o120000)])
    if target == "untracked.cpp":
        (checkout / target).write_bytes(b"untracked\n")
    with pytest.raises(v.ValidationRefused):
        v.inspect_source(checkout, row)


def test_tracked_interior_link_holds_terminal_bytes(owned):
    checkout, row = repository(owned, "source", [("source.cpp", b"source\n", 0o100644), ("alias.cpp", b"source.cpp", 0o120000)])
    proof = v.inspect_source(checkout, row)
    assert any(x.get("terminal_target") == "source.cpp" for x in proof["worktree_files"])
    (checkout / "source.cpp").write_bytes(b"mutate\n")
    with pytest.raises(v.ValidationRefused):
        v.inspect_source(checkout, row)


def test_filter_concealed_same_size_bytes_are_rejected_without_filter(owned):
    canonical = owned / "canonical.cpp"
    canonical.write_bytes(b"int answer = 1;\n")
    marker = owned / "filter-executed"
    script = owned / "filter.py"
    script.write_text(f"from pathlib import Path\nimport sys\nPath({str(marker)!r}).write_text('ran')\nsys.stdout.buffer.write(Path({str(canonical)!r}).read_bytes())\n")
    config = f"[filter \"hide\"]\n clean = {sys.executable} {script}\n required = true\n"
    checkout, row = repository(owned, "filtered", [(".gitattributes", b"source.cpp filter=hide\n", 0o100644), ("source.cpp", canonical.read_bytes(), 0o100644)], config)
    (checkout / "source.cpp").write_bytes(b"int answer = 9;\n")
    with pytest.raises(v.ValidationRefused, match="raw source bytes"):
        v.inspect_source(checkout, row)
    assert not marker.exists(), "raw proof executed the configured clean filter"


@pytest.mark.parametrize("kind", ["extra", "ignored", "missing", "gitlink", "hidden-index"])
def test_source_inventory_refuses(kind, owned):
    files = [("source.cpp", b"source\n", 0o100644)] if kind != "gitlink" else [("dep", b"1" * 40, 0o160000)]
    checkout, row = repository(owned, "source", files)
    if kind in ("extra", "ignored"):
        (checkout / "extra.cpp").write_bytes(b"extra\n")
        if kind == "ignored":
            (checkout / ".git/info/exclude").write_text("extra.cpp\n")
    elif kind == "missing":
        (checkout / "source.cpp").rename(checkout / ".git/missing-copy")
    elif kind == "hidden-index":
        data = bytearray((checkout / ".git/index").read_bytes())
        data[72] |= 0x80
        data[-20:] = hashlib.sha1(data[:-20]).digest()
        (checkout / ".git/index").write_bytes(data)
    with pytest.raises(v.ValidationRefused):
        v.inspect_source(checkout, row)


def test_readonly_git_excludes_lazy_fetch_and_external_config(owned, monkeypatch):
    checkout, row = repository(owned, "source", [("source.cpp", b"source\n", 0o100644)])
    real = subprocess.check_output
    seen = []
    def observe(argv, **kwargs):
        assert "--no-lazy-fetch" in argv and "--no-replace-objects" in argv
        assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert "core.fsmonitor=false" in argv and "core.hooksPath=/dev/null" in argv
        seen.append(argv)
        return real(argv, **kwargs)
    monkeypatch.setattr(v.subprocess, "check_output", observe)
    v.inspect_source(checkout, row)
    assert seen and all("status" not in x and "fetch" not in x for x in seen)
    with pytest.raises(v.ValidationRefused, match="allowed read-only"):
        v.git_bytes(checkout, "status", "--porcelain")


@pytest.mark.parametrize("form", ["bare", "relative", "absolute"])
def test_manifest_exact_library_positive_and_same_basename_counterexample(owned, form):
    prefix = install(owned, {"lib/libVkLayer_khronos_validation.so": b"held bytes\n", "share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json": b"{}"})
    library = prefix / "lib/libVkLayer_khronos_validation.so"
    manifest = prefix / "share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json"
    name = {"bare": library.name, "relative": "../../../lib/" + library.name, "absolute": str(library)}[form]
    manifest.write_bytes(layer_manifest(name))
    assert v.manifest_identity(prefix, manifest, library, v.sha(library), {"LD_LIBRARY_PATH": str(library.parent)})["resolved_library"] == str(library)
    other = prefix / "other" / library.name
    other.parent.mkdir()
    other.write_bytes(library.read_bytes())
    manifest.write_bytes(layer_manifest(str(other) if form != "relative" else "../../../other/" + library.name))
    with pytest.raises(v.ValidationRefused, match="different layer library"):
        v.manifest_identity(prefix, manifest, library, v.sha(library), {"LD_LIBRARY_PATH": str(library.parent)})


def test_manifest_soname_env_and_api_controls(owned):
    prefix = install(owned, {"lib/libVkLayer_khronos_validation.so": b"held\n", "share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json": layer_manifest()})
    library = prefix / "lib/libVkLayer_khronos_validation.so"
    manifest = prefix / "share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json"
    with pytest.raises(v.ValidationRefused, match="exact owned"):
        v.manifest_identity(prefix, manifest, library, v.sha(library), {"LD_LIBRARY_PATH": str(library.parent) + ":/tmp"})
    manifest.write_bytes(layer_manifest(api="1.4.309"))
    with pytest.raises(v.ValidationRefused, match="identity/API"):
        v.manifest_identity(prefix, manifest, library, v.sha(library), {"LD_LIBRARY_PATH": str(library.parent)})


def test_all_five_real_pin_proofs_and_installed_controls(owned, monkeypatch):
    root, prefix, rows, proofs, entries = assembled(owned, monkeypatch)
    for key, entry in entries.items():
        assert "commit " in v.verify_pin(root, key, entry, sources_only=True)
        assert "installed " in v.verify_pin(root, key, entry, sources_only=False)
        with pytest.raises(v.ValidationRefused, match="nonempty"):
            v.verify_pin(root, key, {**entry, "installed_files": {}}, sources_only=False)
        with pytest.raises(v.ValidationRefused, match="unpinned"):
            v.verify_pin(root, key, {**entry, "unpinned": True}, sources_only=True)
    entry = entries[v.SELECTED_KEY]
    with pytest.raises(v.ValidationRefused, match="alias"):
        v.verify_pin(root, v.SELECTED_KEY, {**entry, "manifest_sha256": "f" * 64}, sources_only=False)
    path = prefix / "lib/libVkLayer_khronos_validation.so"
    path.write_bytes(b"changed output\n")
    with pytest.raises(v.ValidationRefused, match="installed bytes"):
        v.verify_pin(root, v.SELECTED_KEY, entry, sources_only=False)


def test_selected_fixed_key_manifest_parent_and_live_mapping(owned, monkeypatch):
    root, prefix, rows, proofs, entries = assembled(owned, monkeypatch)
    proof = v.selected_layer(root)
    assert proof["key"] == v.SELECTED_KEY
    assert proof["loader_environment"]["VK_ADD_LAYER_PATH"] == str(Path(proof["manifest"]).parent)
    observed = {"pid": os.getpid(), **proof["expected_mapping"]}
    with pytest.raises(v.ValidationRefused, match="no selected layer file mapping"):
        v.verify_loaded_layer(observed, proof)
    with Path(proof["library"]).open("rb") as stream:
        mapped = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            assert v.verify_loaded_layer(observed, proof)["verified"] is True
            for field, bad in (("inode", observed["inode"] + 1), ("sha256", "f" * 64), ("api_version", "1.4.309"), ("pid", 999999999)):
                with pytest.raises(v.ValidationRefused):
                    v.verify_loaded_layer({**observed, field: bad}, proof)
        finally:
            mapped.close()
    old = root / "tools/pins.json"
    old.write_text(json.dumps({"tools": {"vulkan-validation-layers": entries[v.SELECTED_KEY]}}))
    with pytest.raises(v.ValidationRefused):
        v.selected_layer(root)


def test_stable_path_confinement_with_resolved_anchor(owned):
    root = owned / "root"
    (root / "private").mkdir(parents=True)
    anchor = owned / "mirror-anchor"
    anchor.mkdir()
    (root / "private/s5-tools").symlink_to(anchor)
    base = anchor / "validation-350"
    base.mkdir()
    assert v.stable_base(root) == base
    (base / "sources").symlink_to(owned)
    with pytest.raises(v.ValidationRefused, match="linked owned"):
        v.owned_path(root, "private/s5-tools/validation-350/sources/source", must_exist=False)
    with pytest.raises(v.ValidationRefused):
        v.owned_path(root, "private/generated/source", must_exist=False)


def test_old_archive_and_raw_checkout_preservation_controls(owned, monkeypatch):
    root = owned / "root"
    (root / "tools").mkdir(parents=True)
    (root / "host/player").mkdir(parents=True)
    (root / "host/player/dxvk.conf").write_bytes(b"held config\n")
    archive = root / "archive.tar.gz"
    archive.write_bytes(b"approved archive\n")
    source, row = repository(root, "old-source", [("source.cpp", b"source\n", 0o100644)])
    pins = {"tools": {"sdl3": {"path": "archive.tar.gz", "sha256": v.sha(archive)}, "dxvk": {"path": "old-source", "commit": row["commit"]}}}
    (root / "tools/pins.json").write_text(json.dumps(pins))
    monkeypatch.setattr(v, "PROTECTED_TOOLS", ("sdl3",))
    monkeypatch.setattr(v, "PROTECTED_SOURCE_TOOLS", ("dxvk",))
    before = v.protected_inputs(root, pins)
    assert any(x.get("field") == "path" and x["sha256"] == v.sha(archive) for x in before)
    raw_before = v.protected_checkouts(root, pins)
    assert raw_before[0]["worktree_files"][0]["path"] == "source.cpp"
    archive.write_bytes(b"changed archive\n")
    with pytest.raises(v.ValidationRefused, match="differs from pin"):
        v.protected_inputs(root, pins)
    (source / "source.cpp").write_bytes(b"mutate\n")
    with pytest.raises(v.ValidationRefused, match="raw source bytes"):
        v.protected_checkouts(root, pins)


def test_old_initialized_submodule_raw_bytes_are_held(owned):
    temporary, child_row = repository(owned, "authored-child", [("sub.cpp", b"held\n", 0o100644)])
    parent, parent_row = repository(owned, "parent", [("dependency", child_row["commit"].encode(), 0o160000)])
    child = parent / "dependency"
    (temporary / "sub.cpp").rename(child / "sub.cpp")
    modules = parent / ".git/modules"
    modules.mkdir()
    (temporary / ".git").rename(modules / "dependency")
    (child / ".git").write_text("gitdir: ../.git/modules/dependency\n")
    proof = v._inspect_source(parent, parent_row, allow_submodules=True)
    assert proof["submodules"][0]["state"] == "initialized"
    (child / "sub.cpp").write_bytes(b"edit\n")
    with pytest.raises(v.ValidationRefused, match="raw source bytes"):
        v._inspect_source(parent, parent_row, allow_submodules=True)


@pytest.mark.parametrize("existing_install", [False, True])
def test_pending_approval_refuses_fetch_and_build_without_stable_writes(owned, existing_install):
    root = owned / "root"
    tools = root / "tools/s5"
    tools.mkdir(parents=True)
    (tools / "validation-sources-350.json").write_bytes((REPO / "tools/s5/validation-sources-350.json").read_bytes())
    stable = root / "private/s5-tools/validation-350"
    if existing_install:
        stable.mkdir(parents=True)
        (stable / "held-output.bin").write_bytes(b"existing installed bytes\n")
    pending = owned / "pending.json"
    pending.write_text(json.dumps({"approved": False}))

    def snapshot():
        return {str(path.relative_to(root)): (path.lstat().st_mode,
                os.readlink(path) if path.is_symlink() else None if path.is_dir() else path.read_bytes())
                for path in root.rglob("*")}

    before = snapshot()
    for script, extra in (("fetch_validation_sources.py", []), ("build_validation_tools.py", ["--run", "fixture", "--propose-pins"])):
        process = subprocess.run([sys.executable, str(REPO / "tools/s5" / script), "--root", str(root), "--approval", str(pending), *extra], env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
        assert process.returncode == 1 and "no recorded direct user approval" in process.stderr
        assert snapshot() == before
    assert stable.exists() == existing_install


def test_normal_missing_pins_is_not_implicit_proposal(owned, monkeypatch):
    root, prefix, rows, proofs, entries = assembled(owned, monkeypatch)
    approval = root / "private/s5-tools/validation-350/approval.json"
    approval.write_text(json.dumps({"approved": True, "approved_by": "user", "scope": "validation-350-five-sources", "source_manifest_sha256": v.sha(root / "tools/s5/validation-sources-350.json"), "user_quote": "fixture approval only; no network/build"}))
    inputs = {relative: v.sha(root / relative) for relative in ("tools/s5/validation_tools.py", "tools/s5/fetch_validation_sources.py", "tools/s5/validation-sources-350.json")}
    fetched = {"schema": "sa2-validation-350-fetch-v1", "sources": proofs, "source_manifest_sha256": v.sha(root / "tools/s5/validation-sources-350.json"), "approval_sha256": v.sha(approval), "input_files": inputs, "upstream_known_good_sha256": v.sha(root / "private/s5-tools/validation-350/sources/Vulkan-ValidationLayers/scripts/known_good.json")}
    (approval.parent / "fetch-receipt.json").write_text(json.dumps(fetched))
    (root / "tools/pins.json").write_text('{"tools": {}}')
    monkeypatch.setattr(sys, "argv", ["build_validation_tools.py", "--root", str(root), "--approval", str(approval), "--run", "fresh"])
    with pytest.raises(v.ValidationRefused, match="explicit --propose-pins required"):
        builder.main()
    assert not (approval.parent / "runs/fresh").exists()


def test_stage_manifest_roles_are_nonempty_and_semantic(owned):
    prefix = install(owned, {"include/vulkan/vulkan_core.h": b"#define VK_HEADER_VERSION 350\n", "share/cmake/VulkanHeaders/VulkanHeadersConfig.cmake": b"fixture config\n"})
    manifest = owned / "install_manifest.txt"
    manifest.write_text("\n".join(str(prefix / p) for p in ("include/vulkan/vulkan_core.h", "share/cmake/VulkanHeaders/VulkanHeadersConfig.cmake")))
    files = builder.stage_files(prefix, manifest)
    assert builder.discover_roles("Vulkan-Headers", files) == {"vulkan_header": "include/vulkan/vulkan_core.h", "package_config": "share/cmake/VulkanHeaders/VulkanHeadersConfig.cmake"}
    manifest.write_text("")
    with pytest.raises(v.ValidationRefused, match="installed no files"):
        builder.stage_files(prefix, manifest)
    with pytest.raises(v.ValidationRefused, match="missing/ambiguous"):
        builder.discover_roles("Vulkan-Headers", {"unrelated.h": "0" * 64})


def test_helper_has_no_fetch_build_or_gate_import_and_manifest_is_exact():
    source = (REPO / "tools/s5/validation_tools.py").read_text()
    tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imports & {"fetch_validation_sources", "build_validation_tools", "check_pins"}
    assert "approval" not in source
    assert hashlib.sha256((REPO / "tools/s5/validation-sources-350.json").read_bytes()).hexdigest() == "9786c8756ff2b84f84304c566deecfee3b97ebffa06c4dfc174b2e4693be3799"


def test_nested_old_alsa_archive_pair_is_held(owned, monkeypatch):
    root = owned / "root"
    (root / "tools").mkdir(parents=True)
    (root / "host/player").mkdir(parents=True)
    (root / "host/player/dxvk.conf").write_text("held config\n")
    archive = root / "alsa-headers.deb"
    archive.write_bytes(b"approved ALSA archive\n")
    pins = {"tools": {"sdl3": {"alsa_headers": {"path": "alsa-headers.deb", "sha256": v.sha(archive), "bytes": archive.stat().st_size}}}}
    (root / "tools/pins.json").write_text(json.dumps(pins))
    monkeypatch.setattr(v, "PROTECTED_TOOLS", ("sdl3",))
    before = v.protected_inputs(root, pins)
    assert any(row.get("field") == "alsa_headers.path" and row["sha256"] == v.sha(archive) for row in before)
    archive.write_bytes(b"changed ALSA archive\n")
    with pytest.raises(v.ValidationRefused, match="alsa_headers.path differs"):
        v.protected_inputs(root, pins)
