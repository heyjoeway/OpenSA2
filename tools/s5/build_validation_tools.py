#!/usr/bin/env python3
"""Build approved supplemental sources in a fresh retained run. Never fetch or delete."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.dont_write_bytecode = True
from validation_tools import (ValidationRefused, require, sha, read_json, source_manifest,
    stable_base, owned_path, inspect_source, verify_pin, selected_layer, manifest_identity,
    record_file, protected_inputs, protected_checkouts, ROLE_PATHS, ALIASES, role_matches)
from fetch_validation_sources import require_approval

def run(argv, env, log=None):
    if log is None:
        return subprocess.check_output([str(x) for x in argv], env=env, text=True,
                                       stderr=subprocess.STDOUT).strip()
    with log.open("x") as output:
        subprocess.run([str(x) for x in argv], env=env, text=True, stdout=output,
                       stderr=subprocess.STDOUT, check=True)


def config_dir(prefix, package):
    found = list(prefix.rglob(package + "Config.cmake"))
    require(len(found) == 1, f"expected one installed {package} config, found {found}")
    require(found[0].resolve(strict=True).is_relative_to(prefix), f"escaped package config: {found[0]}")
    return found[0].parent


def cache_values(cache):
    values = {}
    for line in cache.read_text().splitlines():
        match = re.match(r"([^:#=]+):[^=]+=(.*)$", line)
        if match:
            values[match[1]] = match[2]
    return values


def inventory(prefix):
    rows = []
    for path in sorted(prefix.rglob("*")):
        if path.is_dir():
            require(not path.is_symlink(), f"installed directory is a symlink: {path}")
            continue
        item = record_file(path)
        require(Path(item["realpath"]).is_relative_to(prefix), f"installed symlink escapes prefix: {path}")
        item["relative_path"] = str(path.relative_to(prefix))
        if path.is_symlink():
            item["link"] = os.readlink(path)
        rows.append(item)
    require(rows, "empty install")
    return rows



def stage_files(prefix, manifest):
    files = {}
    for raw in manifest.read_text().splitlines():
        path = Path(raw)
        require(path.is_absolute() and path.is_relative_to(prefix), f"install escaped exact prefix: {raw}")
        relative = str(path.relative_to(prefix))
        from validation_tools import installed_file
        owned = installed_file(prefix, relative)
        files[relative] = sha(owned)
    require(files, "CMake stage installed no files")
    return files


def discover_roles(name, files):
    roles = {}
    for role, semantic in ROLE_PATHS[name].items():
        found = [path for path in files if role_matches(path, semantic)]
        require(len(found) == 1, f"missing/ambiguous consumed role {name}.{role}: {found}")
        roles[role] = found[0]
    return roles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--propose-pins", action="store_true", help="explicit bootstrap; never inferred from missing pins")
    parser.add_argument("--compare", type=Path, help="prior receipt; compare sources/toolchain and layer/manifest bytes")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.run), "invalid literal run name")
    manifest = require_approval(root, args.approval)
    base = stable_base(root)
    fetch_path = owned_path(root, "private/s5-tools/validation-350/fetch-receipt.json")
    fetched = read_json(fetch_path)
    require(fetched.get("schema") == "sa2-validation-350-fetch-v1", "wrong fetch receipt schema")
    require(fetched.get("source_manifest_sha256") == sha(root / "tools/s5/validation-sources-350.json") and fetched.get("approval_sha256") == sha(args.approval), "fetch manifest/approval changed")
    for relative in ("tools/s5/validation_tools.py", "tools/s5/fetch_validation_sources.py", "tools/s5/validation-sources-350.json"):
        require(fetched.get("input_files", {}).get(relative) == sha(root / relative), f"accepted fetch input changed: {relative}")
    sources = owned_path(root, "private/s5-tools/validation-350/sources")
    rows = [inspect_source(sources / row["name"], row) for row in manifest["sources"]]
    require(rows == fetched["sources"], "source proof differs from accepted fetch receipt")
    known_good = sources / "Vulkan-ValidationLayers/scripts/known_good.json"
    require(sha(known_good) == fetched["upstream_known_good_sha256"], "known_good bytes changed")
    pins = read_json(root / "tools/pins.json")
    adopted = None
    if not args.propose_pins:
        for row in manifest["sources"]:
            require(row["key"] in pins["tools"], "missing supplemental pin; explicit --propose-pins required")
            verify_pin(root, row["key"], pins["tools"][row["key"]], sources_only=False)
        adopted = selected_layer(root)
    before = protected_inputs(root, pins)
    sources_before = protected_checkouts(root, pins)
    paths = [root / "tools/s5/validation_tools.py", root / "tools/s5/fetch_validation_sources.py", Path(__file__).resolve(), root / "tools/s5/validation-sources-350.json", fetch_path, args.approval]
    if args.compare:
        paths.append(args.compare)
    paths = [path.resolve(strict=True) for path in paths]
    input_files = {str(path): sha(path) for path in paths}
    run_relative = "private/s5-tools/validation-350/runs/" + args.run
    run_root = owned_path(root, run_relative, must_exist=False)
    require(not run_root.exists(), "fresh run directory already exists")
    run_root.mkdir(parents=True, exist_ok=False)
    prefix = run_root / "install"
    prefix.mkdir()
    cc = Path(pins["tools"]["gcc"]["path"])
    cxx, cmake, ninja = cc.parent / "g++", Path(pins["tools"]["cmake"]["path"]), Path(pins["tools"]["ninja"]["path"])
    python = root / pins["tools"]["python"]["path"]
    env = {"HOME": os.environ["HOME"], "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin", "CC": str(cc), "CXX": str(cxx), "PYTHONDONTWRITEBYTECODE": "1"}
    run([root / ".venv/bin/python", root / "Documentation/scripts/check_pins.py", "--root", root, "--only", "cmake", "--only", "ninja", "--only", "gcc", "--only", "python"], env, run_root / "toolchain-pin-check.log")
    require(run([cxx, "-dumpfullversion"], env) == pins["tools"]["gcc"]["version"], "g++ version differs from gcc pin")
    toolchain = [{**record_file(p), "version_line": run([p, "--version"], env).splitlines()[0]} for p in (cc, cxx, cmake, ninja, python)]
    for item in toolchain:
        print(f"TOOL {item['path']} {item['version_line']} sha256 {item['sha256']}", flush=True)
    for row in rows:
        print(f"SOURCE {row['name']} commit {row['commit']} tree {row['tree']} SOURCE_DATE_EPOCH {row['source_date_epoch']}", flush=True)
    jobs = min(os.cpu_count() or 1, 8)
    flags = f"-ffile-prefix-map={sources}=/sa2-validation-sources -ffile-prefix-map={run_root}=/sa2-validation-build"
    common = ["-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}", "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={prefix}", "-DCMAKE_INSTALL_LIBDIR=lib", f"-DCMAKE_PREFIX_PATH={prefix}", f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}", f"-DCMAKE_C_FLAGS={flags}", f"-DCMAKE_CXX_FLAGS={flags}", f"-DPython3_EXECUTABLE={python}", "-DCMAKE_INSTALL_RPATH=", "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF", "-DFETCHCONTENT_FULLY_DISCONNECTED=ON", "-DFETCHCONTENT_UPDATES_DISCONNECTED=ON", "-DUPDATE_DEPS=OFF", "-DBUILD_TESTS=OFF"]
    stages = []

    def build(name, options, packages=(), capped_jobs=jobs):
        source = next(r for r in rows if r["name"] == name)
        directory = run_root / "build" / name
        deps = {package: config_dir(prefix, package) for package in packages}
        options = options + [f"-D{package}_DIR={path}" for package, path in deps.items()]
        configure = [cmake, "--fresh", "-S", sources / name, "-B", directory, *common, *options]
        compile_install = [ninja, "-C", directory, f"-j{capped_jobs}", "install"]
        stage_env = {**env, "SOURCE_DATE_EPOCH": str(source["source_date_epoch"])}
        print(f"BUILD_START {name} jobs {capped_jobs}", flush=True)
        run(configure, stage_env, run_root / f"{name}-configure.log")
        cache_path = directory / "CMakeCache.txt"
        cache = cache_values(cache_path)
        for option in common + options:
            if option.startswith("-D") and "=" in option:
                key, value = option[2:].split("=", 1)
                require(cache.get(key) == value, f"requested option {key} changed in {name}")
        resolved_tools = {}
        for key in ("CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "CMAKE_AR", "CMAKE_RANLIB", "CMAKE_LINKER", "CMAKE_NM", "CMAKE_OBJCOPY", "CMAKE_OBJDUMP", "CMAKE_STRIP", "_Python3_EXECUTABLE", "PYTHON_EXECUTABLE"):
            if cache.get(key):
                executable = Path(cache[key])
                require(executable.is_absolute() and executable.is_file(), f"invalid resolved configure tool {key}")
                resolved_tools[key] = {**record_file(executable), "version_line": run([executable, "--version"], env).splitlines()[0]}
        bundled_phmap = None
        if name == "Vulkan-ValidationLayers":
            require("_Python3_EXECUTABLE" in resolved_tools and Path(resolved_tools["_Python3_EXECUTABLE"]["realpath"]) == python.resolve(strict=True), "VVL used another Python interpreter")
            require((directory / "layers/external/parallel_hashmap/CMakeFiles").is_dir() and "layers/external/parallel_hashmap/CMakeFiles" in (directory / "build.ninja").read_text(), "VVL did not configure owned bundled phmap")
            bundled_phmap = str(sources / name / "layers/external/parallel_hashmap")
        run(compile_install, stage_env, run_root / f"{name}-build.log")
        install_manifest = directory / "install_manifest.txt"
        files = stage_files(prefix, install_manifest)
        roles = discover_roles(name, files)
        for old in stages:
            require(old["installed_files"] == {p: sha(prefix / p) for p in old["installed_files"]}, "later install changed earlier installed bytes")
        stages.append({"name": name, "jobs": capped_jobs, "source_date_epoch": source["source_date_epoch"], "configure": [str(x) for x in configure], "build": [str(x) for x in compile_install], "package_dirs": {key: str(path) for key, path in deps.items()}, "cache_sha256": sha(cache_path), "install_manifest_sha256": sha(install_manifest), "installed_files": files, "roles": roles, "resolved_tools": resolved_tools, "bundled_phmap": bundled_phmap, "configure_log_sha256": sha(run_root / f"{name}-configure.log"), "build_log_sha256": sha(run_root / f"{name}-build.log")})
        print(f"BUILD_FINISH {name}", flush=True)

    build("Vulkan-Headers", ["-DVULKAN_HEADERS_ENABLE_TESTS=OFF", "-DVULKAN_HEADERS_ENABLE_INSTALL=ON"])
    header = prefix / "include/vulkan/vulkan_core.h"
    require(re.search(r"^#define\s+VK_HEADER_VERSION\s+350\s*$", header.read_text(), re.M), "installed headers are not revision 350")
    build("Vulkan-Utility-Libraries", ["-DVUL_ENABLE_INSTALL=ON", "-DVUL_CODEGEN=OFF"], ["VulkanHeaders"])
    build("SPIRV-Headers", ["-DSPIRV_HEADERS_ENABLE_TESTS=OFF", "-DSPIRV_HEADERS_ENABLE_INSTALL=ON"])
    build("SPIRV-Tools", [f"-DSPIRV-Headers_SOURCE_DIR={sources / 'SPIRV-Headers'}", "-DSPIRV_SKIP_TESTS=ON", "-DSPIRV_WERROR=OFF", "-DSPIRV_SKIP_EXECUTABLES=OFF", "-DBUILD_SHARED_LIBS=OFF"])
    build("Vulkan-ValidationLayers", ["-DCMAKE_DISABLE_FIND_PACKAGE_phmap=ON", "-DBUILD_WERROR=OFF", "-DUSE_MIMALLOC=OFF", "-DVVL_ENABLE_TRACY=OFF", "-DVVL_CODEGEN=OFF", f"-DVULKAN_HEADERS_INSTALL_DIR={prefix}", f"-DVULKAN_UTILITY_LIBRARIES_INSTALL_DIR={prefix}", f"-DSPIRV_HEADERS_INSTALL_DIR={prefix}", f"-DSPIRV_TOOLS_INSTALL_DIR={prefix}"], ["VulkanHeaders", "VulkanUtilityLibraries", "SPIRV-Headers", "SPIRV-Tools", "SPIRV-Tools-opt"], min(jobs, 6))
    require(rows == [inspect_source(sources / row["name"], row) for row in manifest["sources"]], "build changed source bytes/modes/identity")
    require(before == protected_inputs(root, pins), "old archives/outputs/pins/config changed")
    require(sources_before == protected_checkouts(root, pins), "old raw source checkouts or submodules changed")
    require(input_files == {path: sha(Path(path)) for path in input_files}, "build input scripts/records changed")
    proposed, measured = {}, {}
    prefix_relative = run_relative + "/install"
    for row, stage in zip(rows, stages, strict=True):
        key = row["key"]
        proposed[key] = {"version": row["ref"], "commit": row["commit"], "tree": row["tree"], "url": row["url"], "path": f"private/s5-tools/validation-350/sources/{row['name']}", "prefix": prefix_relative, "installed_files": stage["installed_files"], "roles": stage["roles"]}
        for field, relative in ALIASES.get(row["name"], {}).items():
            require(relative in stage["installed_files"], f"required alias not installed by owning stage: {relative}")
            item = record_file(prefix / relative)
            proposed[key][field] = prefix_relative + "/" + relative
            proposed[key][field + "_sha256"] = item["sha256"]
            measured[key + "." + field] = item
            print(f"OUTPUT {key} {field} {item['path']} sha256 {item['sha256']}", flush=True)
        verify_pin(root, key, proposed[key], sources_only=False)
    library = prefix / ALIASES["Vulkan-ValidationLayers"]["library"]
    layer_manifest = prefix / ALIASES["Vulkan-ValidationLayers"]["manifest"]
    layer_identity = manifest_identity(prefix, layer_manifest, library, sha(library), {"LD_LIBRARY_PATH": str(library.parent), "VK_ADD_LAYER_PATH": str(layer_manifest.parent)})
    if adopted:
        require(sha(library) == adopted["library_sha256"] and sha(layer_manifest) == adopted["manifest_sha256"], "normal rebuild differs from adopted layer outputs")
    if args.compare:
        prior = read_json(args.compare)
        require(prior["sources"] == rows and prior["toolchain"] == toolchain, "repeat source/toolchain differs")
        require([x["resolved_tools"] for x in prior["stages"]] == [x["resolved_tools"] for x in stages], "repeat resolved configure tools differ")
        for field in ("library", "manifest"):
            key = "validation-350-vulkan-validation-layers." + field
            require(prior["measured_outputs"][key]["sha256"] == measured[key]["sha256"], "repeat layer/manifest bytes differ")
    receipt = {"schema": "sa2-validation-350-build-v1", "status": "BUILD_ONLY_NO_GPU_VERDICT", "proposal_mode": args.propose_pins, "sources": rows, "input_files": input_files, "toolchain": toolchain, "build_environment": env, "protected_inputs_before": before, "protected_inputs_after": protected_inputs(root, pins), "protected_source_checkouts_before": sources_before, "protected_source_checkouts_after": protected_checkouts(root, pins), "stages": stages, "install_prefix": str(prefix), "installed_files": inventory(prefix), "measured_outputs": measured, "upstream_known_good_sha256": sha(known_good), "layer_manifest_identity": layer_identity}
    for name, data in (("build-receipt.json", receipt), ("proposed-pins.json", proposed)):
        destination = run_root / name
        with destination.open("x") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
        print(f"RECORD {destination} sha256 {sha(destination)}", flush=True)
    print("BUILD_OK BUILD_ONLY_NO_GPU_VERDICT", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValidationRefused, OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        print(f"BUILD_FAIL {error}", file=sys.stderr)
        sys.exit(1)
