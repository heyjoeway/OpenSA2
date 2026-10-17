#!/usr/bin/env python3
"""Run a debug producer with a verified live validation-library checkpoint."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import re
import selectors
import socket
import subprocess
import sys
import time

PROBE_NAME = "VK_LAYER_SA2_validation_probe"
LAYER_NAME = "VK_LAYER_KHRONOS_validation"
LAYER_STARTUP = "Validation Layer Info - Logging validation error to /dev/stderr"
LAYER_SETTINGS = (
    "khronos_validation.debug_action = VK_DBG_LAYER_ACTION_LOG_MSG\n"
    "khronos_validation.log_filename = /dev/stderr\n"
    "khronos_validation.report_flags = error,warn\n"
    "khronos_validation.enable_message_limit = false\n"
    "khronos_validation.message_format_json = false\n"
    # the layer's shader cache keys on the module, not the target environment: a module once passed
    # under a 1.3 instance would pass unchecked under a 1.1 one (the vk11 profile's legs and controls)
    "khronos_validation.check_shaders_caching = false\n")
ICDS = {"nvidia": "/usr/share/vulkan/icd.d/nvidia_icd.json",
        "lvp": "/usr/share/vulkan/icd.d/lvp_icd.json"}
CONTROL_VUIDS = {
    "load-control": {"VUID-VkShaderModuleCreateInfo-pCode-08737",
                     "VUID-RuntimeSpirv-uniformAndStorageBuffer8BitAccess-06329",
                     "VUID-RuntimeSpirv-uniformAndStorageBuffer16BitAccess-06332",
                     "VUID-VkShaderModuleCreateInfo-pCode-08740"},
    "modes-base": {"VUID-vkCmdDraw-None-06479"},
    # the 1.3 profile's SPIR-V 1.6 modules on SDL_gpu's Vulkan 1.1 device: the version, and the
    # capabilities (PhysicalStorageBufferAddresses, VulkanMemoryModel, demote, ...) no 1.1 feature enables
    "load-vk11-control": {"VUID-VkShaderModuleCreateInfo-pCode-08737",
                          "VUID-VkShaderModuleCreateInfo-pCode-08740"},
}


class ObservationRefused(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise ObservationRefused(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selection_environment(selected):
    return {"SA2_VALIDATION_KEY": selected["key"], "SA2_VALIDATION_PREFIX": selected["prefix"],
            "SA2_VALIDATION_LIBRARY": selected["library"], "SA2_VALIDATION_MANIFEST": selected["manifest"],
            "SA2_VALIDATION_LIBRARY_SHA256": selected["library_sha256"],
            "SA2_VALIDATION_MANIFEST_SHA256": selected["manifest_sha256"],
            "SA2_VALIDATION_API_VERSION": selected["api_version"]}


def check_live_record(observed, pid):
    require(type(observed) is dict and observed.get("schema") == "sa2-validation-live/v1",
            "malformed observer record")
    require(type(observed.get("pid")) is int and observed["pid"] == pid,
            "observer belongs to another process")
    instance = observed.get("instance")
    require(type(instance) is str and re.fullmatch(r"0x[0-9a-fA-F]+", instance) is not None and
            0 < int(instance, 16) < 2**64, "observer has no concrete live instance")


def callback_errors(raw):
    errors = {}
    for line in raw.decode("utf-8").splitlines():
        if re.search(r"Validation Error|^\s*ERROR\b.*VUID-|^err:\s+VUID-", line):
            identity = re.search(r"\bVUID-[\w-]+", line)
            name = identity.group() if identity else "unknown-validation-error"
            errors[name] = errors.get(name, 0) + 1
    return errors


def load_helper(root):
    path = root / "tools/s5/validation_tools.py"
    spec = importlib.util.spec_from_file_location("sa2_validation_tools_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe_identity(directory, root=None):
    directory = directory.resolve(strict=True)
    manifest = directory / "validation_probe.json"
    library = directory / "libVkLayer_sa2_validation_probe.so"
    data = json.loads(manifest.read_text())
    require(data["layer"]["name"] == PROBE_NAME, "wrong observer layer name")
    require(Path(data["layer"]["library_path"]).resolve(strict=True) == library.resolve(strict=True),
            "observer manifest selects another library")
    build_path = directory / "validation_probe_build.json"
    build = json.loads(build_path.read_text())
    require(build.get("schema") == "sa2-validation-probe-build/v1" and
            build.get("outputs") == {manifest.name: sha(manifest), library.name: sha(library)},
            "observer differs from its build record")
    if root is not None:
        expected = {"host/d3d9gpu/test/validation_probe/" + name for name in
                    ("validation_probe.c", "validation_probe.json.in", "build_validation_probe.sh")}
        require(set(build.get("sources", {})) == expected and build.get("headers"),
                "observer build has missing or different source roles")
        for path, digest in {**build["sources"], **build["headers"]}.items():
            require(sha(root / path) == digest, "observer build used other sources: " + path)
        require(sha(build["compiler"]["path"]) == build["compiler"]["sha256"], "observer compiler changed")
    return {"manifest": str(manifest), "manifest_sha256": sha(manifest),
            "library": str(library), "library_sha256": sha(library),
            "build_record": str(build_path), "build_record_sha256": sha(build_path)}


def observed_command(argv, *, selected, verify, probe, record_path, environment,
                     stdout=None, stderr_sink=None, cwd=None):
    """Keep the checkpoint child alive until its actual mapping has been checked."""
    record_path = Path(record_path)
    require(not record_path.exists(), "observation record already exists")
    require(argv and all(type(v) is str for v in argv), "missing producer command")
    require(selected["key"] == "validation-350-vulkan-validation-layers", "wrong selected validation key")
    env = dict(environment)
    require(not [key for key in env if key.startswith("VK_LAYER_") and key not in
                 ("VK_LAYER_SETTINGS_PATH", "VK_LAYER_DUPLICATE_MESSAGE_LIMIT")],
            "unexpected inherited validation settings")
    names = env.get("VK_INSTANCE_LAYERS", "").split(":")
    require(names.count(LAYER_NAME) == 1 and PROBE_NAME not in names,
            "debug job must select the validation layer exactly once")
    names.insert(names.index(LAYER_NAME), PROBE_NAME)
    env["VK_INSTANCE_LAYERS"] = ":".join(names)
    paths = env.get("VK_ADD_LAYER_PATH", "").split(":")
    selected_parent = str(Path(selected["manifest"]).parent)
    require(paths.count(selected_parent) == 1, "debug job selects another validation manifest directory")
    probe_parent = str(Path(probe["manifest"]).parent)
    require(probe_parent not in paths, "observer directory already selected")
    paths.insert(0, probe_parent)
    env["VK_ADD_LAYER_PATH"] = ":".join(paths)
    require(env.get("LD_LIBRARY_PATH") == selected["loader_environment"]["LD_LIBRARY_PATH"],
            "debug job uses another validation library directory")
    require(sha(selected["manifest"]) == selected["manifest_sha256"] and
            sha(selected["library"]) == selected["library_sha256"], "selected layer changed before launch")
    env.update(selection_environment(selected))
    require(env.get("VK_LOADER_LAYERS_DISABLE", "~implicit~") == "~implicit~" and
            "VK_LOADER_LAYERS_ENABLE" not in env and "VK_LAYER_PATH" not in env,
            "unexpected Vulkan loader layer policy")
    env["VK_LOADER_LAYERS_DISABLE"] = "~implicit~"
    icd_inputs = {key: {"path": env[key], "sha256": sha(env[key])} for key in
                  ("VK_DRIVER_FILES", "VK_ICD_FILENAMES") if key in env}
    record_path.parent.mkdir(parents=True, exist_ok=True)
    settings = record_path.parent / (record_path.stem + "-settings")
    require(not settings.exists(), "validation settings directory already exists")
    settings.mkdir()
    setting_file = settings / "vk_layer_settings.txt"
    setting_file.write_text(LAYER_SETTINGS)
    env["VK_LAYER_SETTINGS_PATH"] = str(settings)
    env["VK_LAYER_DUPLICATE_MESSAGE_LIMIT"] = "1000000"
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    env["SA2_VALIDATION_OBSERVER_FD"] = str(child.fileno())
    record = {"schema": "sa2-validation-run/v1", "command": argv, "selected": selected,
              "producer_sha256": sha(argv[0]),
              "observer": probe, "observations": [], "pass": False, "producer_exit": None,
              "settings_file": str(setting_file), "settings_sha256": sha(setting_file),
              "environment": {k: v for k, v in env.items() if k.startswith(("VK_", "SA2_VALIDATION_")) or
                              k in ("LD_LIBRARY_PATH", "VKCAP_OUT", "SDL_LOGGING", "SDL_VIDEO_DRIVER")},
              "icd_inputs": icd_inputs,
              "started_monotonic": time.monotonic()}
    stderr = bytearray()
    failure = None
    process = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(argv, stdout=stdout, stderr=subprocess.PIPE, env=env,
                                   pass_fds=(child.fileno(),), cwd=cwd)
        record["pid"] = process.pid
        child.close()
        parent.setblocking(False)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(parent, selectors.EVENT_READ, "checkpoint")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            for key, _ in selector.select(0.25):
                if key.data == "stderr":
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    stderr.extend(chunk)
                    if stderr_sink is not None:
                        stderr_sink.write(chunk)
                        stderr_sink.flush()
                    continue
                packet, _, flags, _ = parent.recvmsg(32768)
                if not packet:
                    selector.unregister(parent)
                    continue
                try:
                    require(not (flags & socket.MSG_TRUNC), "truncated observer packet")
                    observed = json.loads(packet)
                    check_live_record(observed, process.pid)
                    verified = verify(observed, selected)
                    record["observations"].append(verified)
                    parent.send(b"Y")
                except Exception as error:
                    failure = str(error)
                    try:
                        parent.send(b"N")
                    except OSError:
                        pass
            if process.poll() is not None and not selector.get_map():
                break
        record["producer_exit"] = process.wait()
        require(failure is None, failure or "observer rejected")
        require(record["observations"], "producer supplied no live instance observation")
        require(sha(selected["manifest"]) == selected["manifest_sha256"] and
                sha(selected["library"]) == selected["library_sha256"], "selected layer changed during producer")
        require(sha(probe["manifest"]) == probe["manifest_sha256"] and
                sha(probe["library"]) == probe["library_sha256"], "observer changed during producer")
        require(all(sha(value["path"]) == value["sha256"] for value in icd_inputs.values()),
                "selected ICD changed during producer")
        record["pass"] = True
    except BaseException as error:
        record["failure"] = str(error) if isinstance(error, Exception) else type(error).__name__ + ": " + str(error)
        if process is not None:
            if process.poll() is None:
                process.kill()
            record["producer_exit"] = process.wait()
            if process.stderr:
                tail = process.stderr.read() or b""
                stderr.extend(tail)
        if not isinstance(error, Exception):
            raise
    finally:
        selector.close()
        parent.close()
        child.close()
        if process is not None and process.stderr is not None:
            process.stderr.close()
        record["stderr_sha256"] = hashlib.sha256(stderr).hexdigest()
        record["stderr_bytes"] = len(stderr)
        record["seconds"] = time.monotonic() - record["started_monotonic"]
        record_path.write_text(json.dumps(record, indent=2) + "\n")
        record_path.with_suffix(".stderr").write_bytes(stderr)
    return record, bytes(stderr)


R1_JOBS = {"load": "load", "load-control": "load", "load-vk11": "load", "load-vk11-control": "load", "null-sdl": "probe-null",
           "null-control": "probe-null", "border": "border", "modes": "modes", "modes-base": "modes"}


def accepted_build_inputs(root, selected):
    """Bind adopted output pins to the accepted fetch and no-fetch build receipts."""
    helper = load_helper(root)
    manifest_path = root / "tools/s5/validation-sources-350.json"
    manifest = json.loads(manifest_path.read_text())
    base = (root / "private/s5-tools").resolve(strict=True) / "validation-350"
    fetch_path, approval_path = base / "fetch-receipt.json", base / "approval.json"
    build_path = Path(selected["prefix"]).parent / "build-receipt.json"
    fetched, build = json.loads(fetch_path.read_text()), json.loads(build_path.read_text())
    require(fetched.get("schema") == "sa2-validation-350-fetch-v1" and
            fetched.get("source_manifest_sha256") == sha(manifest_path) and
            fetched.get("approval_sha256") == sha(approval_path), "accepted fetch identity differs")
    require(build.get("schema") == "sa2-validation-350-build-v1" and
            build.get("status") == "BUILD_ONLY_NO_GPU_VERDICT" and
            build.get("install_prefix") == selected["prefix"], "accepted build identity differs")
    source_paths = ("tools/s5/validation_tools.py", "tools/s5/fetch_validation_sources.py",
                    "tools/s5/validation-sources-350.json")
    for relative in source_paths:
        require(fetched.get("input_files", {}).get(relative) == sha(root / relative),
                "accepted fetch used other inputs: " + relative)
    required = [root / relative for relative in source_paths] + [root / "tools/s5/build_validation_tools.py",
                                                               fetch_path, approval_path]
    for path in required:
        require(build.get("input_files", {}).get(str(path.resolve(strict=True))) == sha(path),
                "accepted build used other inputs: " + str(path))
    for name, digest in build["input_files"].items():
        require(Path(name).is_absolute() and sha(name) == digest, "accepted build input changed: " + name)
    pins = json.loads((root / "tools/pins.json").read_text())["tools"]
    rows = []
    for row in manifest["sources"]:
        helper.verify_pin(root, row["key"], pins[row["key"]], sources_only=False)
        rows.append(helper.inspect_source(base / "sources" / row["name"], row))
    require(rows == fetched.get("sources") == build.get("sources"), "accepted source proof differs")
    for role in ("library", "manifest"):
        value = build.get("measured_outputs", {}).get(selected["key"] + "." + role, {})
        path = Path(selected[role])
        require(value.get("path") == str(path) and value.get("realpath") == str(path.resolve(strict=True)) and
                value.get("sha256") == selected[role + "_sha256"] and value.get("bytes") == path.stat().st_size,
                "accepted build measured another " + role)
    return {"fetch_receipt": {"path": str(fetch_path), "sha256": sha(fetch_path)},
            "build_receipt": {"path": str(build_path), "sha256": sha(build_path)},
            "approval": {"path": str(approval_path), "sha256": sha(approval_path)},
            "source_manifest_sha256": sha(manifest_path), "build_input_files": build["input_files"]}


def audit_r1(run, selected, shaderc, root):
    """Reconcile retained checkpoint records for every R1 debug command."""
    run, shaderc = Path(run), Path(shaderc)
    probe = probe_identity(run / "validation-probe", root)
    records = {}
    expected_paths = {run / driver / "validation" / (job + ".json")
                      for driver in ("nvidia", "lvp") for job in R1_JOBS}
    require(set(run.glob("*/validation/*.json")) == expected_paths,
            "missing or extra R1 live observation records")
    for path in sorted(expected_paths):
        record = json.loads(path.read_text())
        job, driver = path.stem, path.parent.parent.name
        command = record.get("command")
        require(record.get("schema") == "sa2-validation-run/v1" and record.get("pass") is True,
                "R1 live observation failed: " + str(path))
        require(record.get("selected") == selected and record.get("observer") == probe,
                "R1 selected layer or observer differs: " + str(path))
        require(type(command) is list and len(command) >= 2 and command[:2] == [str(shaderc), R1_JOBS[job]] and
                record.get("producer_sha256") == sha(shaderc), "R1 observation names another producer")
        require(type(record.get("producer_exit")) is int and record["producer_exit"] in
                ((0, 1) if job in ("load-control", "load-vk11-control", "modes-base") else (0,)), "R1 producer exited unexpectedly")
        stderr = path.with_suffix(".stderr")
        require(record.get("stderr_sha256") == sha(stderr) and record.get("stderr_bytes") == stderr.stat().st_size,
                "R1 retained stderr differs from collected bytes")
        raw_stderr = stderr.read_bytes()
        checked = run / driver / (job + ".txt")
        require(checked.read_bytes().endswith(raw_stderr), "R1 checked log omits collected callback bytes")
        errors = callback_errors(raw_stderr)
        if job in CONTROL_VUIDS:
            require(set(errors) == CONTROL_VUIDS[job], "R1 control has missing or unrelated validation errors")
        else:
            require(not errors, "R1 positive retained stderr contains a validation error")
        settings = Path(record.get("settings_file", ""))
        require(settings.parent == path.parent / (job + "-settings") and
                record.get("settings_sha256") == sha(settings) and settings.read_text() == LAYER_SETTINGS and
                record.get("environment", {}).get("VK_LAYER_SETTINGS_PATH") == str(settings.parent),
                "R1 owned layer settings changed")
        require(LAYER_STARTUP in (run / driver / (job + ".txt")).read_text().splitlines(),
                "R1 unfiltered layer-owned callback was not initialized")
        environment = record.get("environment", {})
        fd = environment.get("SA2_VALIDATION_OBSERVER_FD")
        require(type(fd) is str and re.fullmatch(r"[0-9]+", fd) is not None,
                "R1 has no observer descriptor identity")
        paths = [str(Path(probe["manifest"]).parent), str(Path(selected["manifest"]).parent)]
        captured = job in ("load", "load-vk11")   # test_shaders.sh's capture layer rides on both loads
        if captured: paths.append(str(run / "vkcap/vkcap"))
        expected_environment = {
            **selection_environment(selected), "SA2_VALIDATION_OBSERVER_FD": fd,
            "VK_INSTANCE_LAYERS": ":".join([PROBE_NAME, LAYER_NAME] +
                (["VK_LAYER_SA2_vkcap"] if captured else [])),
            "VK_ADD_LAYER_PATH": ":".join(paths), "LD_LIBRARY_PATH": selected["loader_environment"]["LD_LIBRARY_PATH"],
            "VK_LAYER_SETTINGS_PATH": str(settings.parent), "VK_LAYER_DUPLICATE_MESSAGE_LIMIT": "1000000",
            "VK_LOADER_LAYERS_DISABLE": "~implicit~", "VK_DRIVER_FILES": ICDS[driver],
            "SDL_LOGGING": "gpu=info", "SDL_VIDEO_DRIVER": "offscreen",
        }
        if captured: expected_environment["VKCAP_OUT"] = str(run / driver / (job + "-capture"))
        require(environment == expected_environment, "R1 retained role environment differs")
        require(record.get("icd_inputs") == {"VK_DRIVER_FILES": {"path": ICDS[driver], "sha256": sha(ICDS[driver])}},
                "R1 selected ICD identity differs")
        names = environment.get("VK_INSTANCE_LAYERS", "").split(":")
        require(names[:2] == [PROBE_NAME, LAYER_NAME] and len(set(names)) == len(names),
                "R1 observer and validation layer order differs")
        require(names == [PROBE_NAME, LAYER_NAME] + (["VK_LAYER_SA2_vkcap"] if captured else []),
                "R1 observation selected another explicit layer")
        observations = record.get("observations")
        require(type(observations) is list and observations, "R1 has no actual live observations")
        require(type(record.get("pid")) is int and record["pid"] > 0, "R1 producer has no process identity")
        for observed in observations:
            check_live_record(observed, record["pid"])
            require(type(observed) is dict and type(observed.get("pid")) is int and observed["pid"] == record["pid"] and
                    observed.get("verified") is True and observed.get("selected_key") == selected["key"],
                    "R1 observation belongs to another process or selection")
            for field, value in selected["expected_mapping"].items():
                require(type(observed.get(field)) is type(value) and observed[field] == value,
                        "R1 loaded identity differs: " + field)
            rows = observed.get("matched_map_lines")
            require(type(rows) is list and rows, "R1 omitted its live mapped-file evidence")
            for row in rows:
                require(type(row) is str, "R1 malformed live mapping row")
                fields = row.split(None, 5)
                require(len(fields) == 6, "R1 malformed live mapping row")
                major, minor = [int(v, 16) for v in fields[3].split(":")]
                require((os.makedev(major, minor), int(fields[4]), fields[5]) ==
                        (observed["device"], observed["inode"], observed["realpath"]),
                        "R1 retained mapping does not match loaded identity")
        records[driver + "/" + job] = {"record_sha256": sha(path), "producer_exit": record["producer_exit"],
                                      "observations": observations, "stderr_sha256": sha(stderr),
                                      "validation_errors": errors, "environment": environment,
                                      "icd_inputs": record["icd_inputs"]}
    return {"selected": selected, "observer": probe, "jobs": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="mode", required=True)
    command = sub.add_parser("run")
    command.add_argument("--probe-dir", required=True, type=Path)
    command.add_argument("--record", required=True, type=Path)
    command.add_argument("producer", nargs=argparse.REMAINDER)
    audit = sub.add_parser("audit-r1")
    audit.add_argument("--run", required=True, type=Path)
    audit.add_argument("--shaderc", required=True, type=Path)
    args = parser.parse_args()
    helper = load_helper(args.root.resolve(strict=True))
    try:
        selected = helper.selected_layer(args.root)
        if args.mode == "audit-r1":
            audit_r1(args.run, selected, args.shaderc, args.root)
            print("R1_LIVE_VALIDATION_OK")
            return 0
        argv = args.producer[1:] if args.producer[:1] == ["--"] else args.producer
        probe = probe_identity(args.probe_dir, args.root)
        record, stderr = observed_command(argv, selected=selected, verify=helper.verify_loaded_layer, probe=probe,
                                          record_path=args.record, environment=os.environ)
        # The child has exited. Emit each callback byte once, as a contiguous suffix
        # of the shell's checked log; stdout cannot interleave with it now.
        sys.stderr.buffer.write(stderr)
        sys.stderr.buffer.flush()
        if not record["pass"]:
            raise ObservationRefused(record.get("failure", "missing observation"))
        return record["producer_exit"] if record["producer_exit"] >= 0 else 128 - record["producer_exit"]
    except Exception as error:
        print("VALIDATION_OBSERVATION_FAIL " + str(error), file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
