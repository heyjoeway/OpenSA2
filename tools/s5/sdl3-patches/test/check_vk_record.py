#!/usr/bin/env python3
"""Checks what the SDL patch asked of Vulkan during one border_clamp run, from the capture
layer's records, and that every layer the run asked for was really loaded.

Subtitle: the API half of the SDL patch's behaviour check (A3). The pixels show the patch draws
right; they cannot show it asks Vulkan correctly, because a desktop driver honours a depth-clip
structure whose extension was never enabled, and the validation layer of this version does not
flag it either (review of 2026-10-04: stateless_validation_helper.cpp checks the structures'
flags only). So the records are held to the rules the Vulkan specification states:

  * a device that chains VkPhysicalDeviceDepthClipEnableFeaturesEXT enables
    VK_EXT_depth_clip_enable, and a device that enables the extension chains the feature with
    depthClipEnable and enables depthClamp (the patch enables it only where it uses it);
  * no structure type appears twice in a device's pNext chain;
  * a pipeline that chains VkPipelineRasterizationDepthClipStateCreateInfoEXT is created on a
    device with the extension and the feature, and asks for clip and clamp together; a pipeline
    that clamps runs on a device with depthClamp;
  * the run drew at least one clip-and-clamp pipeline, and created clamp-to-border samplers in
    all three border colours;
  * every layer named in VK_INSTANCE_LAYERS was inserted into every device the run created (the
    loader's own VK_LOADER_DEBUG=layer lines), and its manifest exists: the loader ignores a
    layer it cannot find, so a run that merely asked for validation proves nothing;
  * the capture layer wrote every record it counted, and no write failed.

  check_vk_record.py --records DIR --loader-log FILE --layers L1:L2 [--manifest PATH ...]

Prints one JSON line and VK_RECORD_OK or VK_RECORD_FAIL; exits 0 only for OK.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

DEPTH_CLIP_EXT = "VK_EXT_depth_clip_enable"
CLAMP_TO_BORDER = 3  # VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_BORDER
BORDER_COLORS = {0: "transparent_black", 2: "opaque_black", 4: "opaque_white"}  # VK_BORDER_COLOR_FLOAT_*


def read_jsonl(path, problems):
    rows = []
    if not path.exists():
        return rows
    for n, line in enumerate(path.read_text().splitlines(), 1):
        try:
            rows.append(json.loads(line))
        except ValueError:
            problems.append(f"{path.name}:{n} is not one JSON record")
    return rows


def check(records, loader_log, layers, manifests):
    problems = []
    devices = read_jsonl(records / "devices.jsonl", problems)
    pipelines = read_jsonl(records / "pipelines.jsonl", problems)
    samplers = read_jsonl(records / "samplers.jsonl", problems)
    log = loader_log.read_text(errors="replace") if loader_log.exists() else ""
    if not log:
        problems.append(f"no loader log at {loader_log}")

    # Layers: each requested layer inserted into every device, and present on disk.
    for m in manifests:
        if not Path(m).is_file():
            problems.append(f"layer manifest {m} is missing")
    created = [d for d in devices if d.get("result") == 0]
    inserted = Counter(re.findall(r'Inserted device layer "([^"]+)"', log))
    for layer in layers:
        if inserted[layer] != len(devices) or not devices:
            problems.append(f"layer {layer} was inserted into {inserted[layer]} devices; the run created {len(devices)}")

    # The capture layer's own accounting.
    reports = re.findall(r"vkcap: load=(\S+) records=(\d+) write_failures=(\d+)", log)
    loads = {r["load"] for r in devices + pipelines + samplers}
    if not reports:
        problems.append("the capture layer reported nothing (not loaded, or the process died)")
    if any(int(f) for _, _, f in reports):
        problems.append(f"the capture layer failed {sum(int(f) for _, _, f in reports)} writes")
    if not loads <= {l for l, _, _ in reports}:
        problems.append("a load wrote records but never reported its count")
    modules = len(list(records.glob("module-*.spv")))
    if sum(int(n) for _, n, _ in reports) != len(devices) + len(pipelines) + len(samplers) + modules:
        problems.append(f"the capture layer counted {sum(int(n) for _, n, _ in reports)} records; "
                        f"{len(devices) + len(pipelines) + len(samplers) + modules} are on disk")

    # Devices.
    by_key = {}
    for d in devices:
        key = (d["load"], d["seq"])
        if key in by_key:
            problems.append(f"device {key} recorded twice")
        by_key[key] = d
        dup = [t for t, n in Counter(d["pnext"]).items() if n > 1]
        if dup:
            problems.append(f"device {key} chains sType {dup} more than once")
        has_ext = DEPTH_CLIP_EXT in d["extensions"]
        if d["depth_clip_enable"] is not None and not has_ext:
            problems.append(f"device {key} chains the depth-clip feature without enabling {DEPTH_CLIP_EXT}")
        if has_ext and (d["depth_clip_enable"] != 1 or d["depth_clamp"] != 1):
            problems.append(f"device {key} enables {DEPTH_CLIP_EXT} without depthClipEnable "
                            f"({d['depth_clip_enable']}) and depthClamp ({d['depth_clamp']})")

    # Pipelines.
    both = 0
    for p in pipelines:
        dev = by_key.get((p["load"], p["device"]))
        r = p.get("raster")
        if dev is None:
            problems.append(f"pipeline {p['load']}/{p['seq']} names no recorded device")
            continue
        if not r:
            continue
        if r["depth_clip"] is not None:
            if DEPTH_CLIP_EXT not in dev["extensions"] or dev["depth_clip_enable"] != 1:
                problems.append(f"pipeline {p['load']}/{p['seq']} chains depth-clip state on device "
                                f"{p['device']}, which did not enable the extension and its feature")
            if (r["depth_clamp"], r["depth_clip"]) != (1, 1):
                problems.append(f"pipeline {p['load']}/{p['seq']} chains depth-clip state with "
                                f"clamp {r['depth_clamp']} and clip {r['depth_clip']}, not both")
            both += p["result"] == 0 and r["depth_clamp"] == 1 and r["depth_clip"] == 1
        if r["depth_clamp"] == 1 and dev["depth_clamp"] != 1:
            problems.append(f"pipeline {p['load']}/{p['seq']} clamps depth on a device without depthClamp")
    if not both:
        problems.append("no pipeline was created with depth clip and depth clamp together")

    # Samplers: clamp-to-border in each standard colour.
    seen = {s["border_color"] for s in samplers if s.get("result") == 0 and CLAMP_TO_BORDER in s["address"]}
    for c, name in BORDER_COLORS.items():
        if c not in seen:
            problems.append(f"no clamp-to-border sampler with border colour {name}")
    for s in samplers:
        if s.get("pnext"):
            problems.append("a sampler chains a structure (a custom border colour needs an extension the patch never enables)")

    summary = {"devices": len(devices), "created": len(created), "pipelines": len(pipelines),
               "clip_and_clamp_pipelines": both, "samplers": len(samplers), "modules": modules,
               "loads": len(loads), "layers_inserted": dict(inserted), "problems": problems}
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--records", required=True, type=Path)
    ap.add_argument("--loader-log", required=True, type=Path)
    ap.add_argument("--layers", required=True, help="the VK_INSTANCE_LAYERS value the run used")
    ap.add_argument("--manifest", action="append", default=[])
    a = ap.parse_args(argv)
    s = check(a.records, a.loader_log, [l for l in a.layers.split(":") if l], a.manifest)
    print(json.dumps({"vk_record": s}))
    ok = not s["problems"]
    print("VK_RECORD_OK" if ok else "VK_RECORD_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
