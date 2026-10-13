#!/usr/bin/env python3
"""check_params.py: the device parameters the second host's state check takes
(test/state_check.cpp --params), each read from a recorded source (the critic's M7):

  * DXVK's device features, and whether DXVK's format table has D24S8 and D16S8, from DXVK's own
    log of a run on the same machine (its "Enabled features" and per-extension sections, and the
    remap lines D3D9VkFormatTable's constructor logs for a format it lacks:
    "D3D9: VK_FORMAT_D24_UNORM_S8_UINT -> ..." and "D3D9: VK_FORMAT_D16_UNORM_S8_UINT -> ...").
    The table is made with the adapter, before the device: a log that reaches "Creating device"
    has made it, so the absence of a remap line there is the format's support, never a truncated
    log's silence (the review's checker-logic finding 11). The log's DXVK configuration is
    recorded, and a built-in (per-application) configuration refuses: the check's shims answer
    every option with its default. So does an effective configuration that sets an option the
    extracted code reads (the extraction's manifest, --extract-manifest: dxvk_conf.modelled_options;
    without one, extract_refs.py's span table, which the extraction holds equal to it; the R2b
    re-reviews' extraction NIT 8 and checker-logic NIT 13);
  * the vendor ID and the point size range, from the Vulkan property query the second host's
    device report carries ("sdl_gpu"), held to the same device name DXVK's log names; the vendor
    ID is also held to the vendor of the driver DXVK's log names ("Driver : NVIDIA ..."), where
    that name is one this tool knows (the review's N9). DXVK's log prints no point size range,
    so POINTSIZE_MAX's value has one source: the defaults check holds d9g's rule
    (pointSizeRange[1]), not the value, and the parameters say so;
  * the second host's own D24S8 choice, from its report;
  * the SDL patch's two capabilities as the second host used them (patches/sdl3; its report's
    sdl_gpu.sampler_clamp_to_border and depth_clip_and_clamp), and a test run's override of them
    (sdl_gpu.capability_override), which the state check maps with and holds the keys files to.
    Where the SDL is the patched one, the depth clip and clamp it detected is held to what DXVK's
    log reports for the same driver: VK_EXT_depth_clip_enable's depthClipEnable and the core
    depthClamp, both enabled (sdl-patch PLAN.md section 3, the cross-check). A stock SDL detects
    neither, and the parameters say so.

  check_params.py --dxvk-log LOG --device-report D3D9GPU.json [--extract-manifest MANIFEST.json] --out PARAMS.json

A feature the state check needs that the log does not give refuses (exit 2).

  check_params.py --axis NAME --out PARAMS.json

writes one of the sweep's device axes instead (nvidia-like, amd-like, intel-like): parameter sets
that drive DXVK's device-dependent branches in the GPU-free sweep. They are inputs to a sweep,
labelled as such, and never a claim about any machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# DxvkDeviceFeatures paths the extracted DXVK code reads (ref_dxvk.cpp dxvk_features_read), and
# where DXVK's log reports each.
FEATURES = {
    "core.features.depthBounds": ("Enabled features", "depthBounds"),
    "extAttachmentFeedbackLoopLayout.attachmentFeedbackLoopLayout": ("VK_EXT_attachment_feedback_loop_layout", "attachmentFeedbackLoopLayout"),
    "extBorderColorSwizzle.borderColorSwizzle": ("VK_EXT_border_color_swizzle", "borderColorSwizzle"),
    "extBorderColorSwizzle.borderColorSwizzleFromImage": ("VK_EXT_border_color_swizzle", "borderColorSwizzleFromImage"),
    "extCustomBorderColor.customBorderColorWithoutFormat": ("VK_EXT_custom_border_color", "customBorderColorWithoutFormat"),
    "extCustomBorderColor.customBorderColors": ("VK_EXT_custom_border_color", "customBorderColors"),
    "extDepthBiasControl.depthBiasControl": ("VK_EXT_depth_bias_control", "depthBiasControl"),
    "extDepthBiasControl.depthBiasExact": ("VK_EXT_depth_bias_control", "depthBiasExact"),
    "extDepthBiasControl.floatRepresentation": ("VK_EXT_depth_bias_control", "floatRepresentation"),
    "extDepthBiasControl.leastRepresentableValueForceUnormRepresentation":
        ("VK_EXT_depth_bias_control", "leastRepresentableValueForceUnormRepresentation"),
    "extExtendedDynamicState3.extendedDynamicState3AlphaToCoverageEnable":
        ("VK_EXT_extended_dynamic_state3", "extendedDynamicState3AlphaToCoverageEnable"),
    "extExtendedDynamicState3.extendedDynamicState3DepthClipEnable":
        ("VK_EXT_extended_dynamic_state3", "extendedDynamicState3DepthClipEnable"),
    "extExtendedDynamicState3.extendedDynamicState3RasterizationSamples":
        ("VK_EXT_extended_dynamic_state3", "extendedDynamicState3RasterizationSamples"),
    "extExtendedDynamicState3.extendedDynamicState3SampleMask":
        ("VK_EXT_extended_dynamic_state3", "extendedDynamicState3SampleMask"),
    "extNonSeamlessCubeMap.nonSeamlessCubeMap": ("VK_EXT_non_seamless_cube_map", "nonSeamlessCubeMap"),
}


class Refusal(Exception):
    pass


# The PCI vendor IDs of the drivers DXVK's log can name (the driver name's first word).
DRIVER_VENDORS = {"NVIDIA": 0x10DE, "AMD": 0x1002, "radv": 0x1002, "RADV": 0x1002, "Intel": 0x8086, "ANV": 0x8086,
                  "llvmpipe": 0x10005}


def vendor_cross_check(driver: str | None, vendor_id: int) -> str:
    word = (driver or "").split(" ")[0]
    if word not in DRIVER_VENDORS:
        return f"not cross-checked: DXVK's log names driver {driver!r}, which this tool does not map to a vendor"
    if DRIVER_VENDORS[word] != vendor_id:
        raise Refusal(f"the device's query says vendor {vendor_id:#x}; DXVK's log names driver {driver!r} "
                      f"({DRIVER_VENDORS[word]:#x})")
    return f"DXVK's log names driver {driver!r}, vendor {vendor_id:#x}, as the device's query says"


def parse_dxvk_log(text: str) -> dict:
    sections: dict[str, dict[str, int]] = {}
    cur = None
    device = None
    for line in text.splitlines():
        m = re.match(r"info:  (Enabled features|VK_[A-Z0-9_a-z]+):\s*$", line)
        if m:
            cur = m.group(1)
            sections.setdefault(cur, {})
            continue
        m = re.match(r"info:    (\w+)\s+:\s+([01])\s*$", line)
        if m and cur:
            sections[cur][m.group(1)] = int(m.group(2))
            continue
        m = re.match(r"info:  (\S.*):\s*$", line)
        if m and cur is None and device is None and m.group(1) not in ("Creating device", "Queues", "Memory",
                                                                          "Enabled extensions", "Extension providers",
                                                                          "Enabled instance extensions"):
            pass
        if not line.startswith("info:    "):
            cur = None if not re.match(r"info:  (Enabled features|VK_)", line) else cur
    m = re.search(r"info:  Creating device:\ninfo:  (.+):\n", text)
    if m:
        device = m.group(1)
    m = re.search(r"info:  Creating device:\ninfo:  .+:\ninfo:    Driver\s*:\s*(.+)\n", text)
    driver = m.group(1).strip() if m else None
    # the format table's remap lines, which precede the device's creation
    head = text.split("info:  Creating device:", 1)[0] if "info:  Creating device:" in text else None
    config = {}
    m = re.search(r"info:  Effective configuration:\n((?:info:    \S+ = .*\n)*)", text)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line[len("info:    "):].partition(" = ")
            config[k] = v
    return {"sections": sections, "device": device, "driver": driver,
            "format_table_made": head is not None,
            "d24s8_remapped": head is not None and "D3D9: VK_FORMAT_D24_UNORM_S8_UINT -> " in head,
            "d16s8_remapped": head is not None and "D3D9: VK_FORMAT_D16_UNORM_S8_UINT -> " in head,
            "builtin_config": re.findall(r"info:  Found built-in config:.*", text),
            "effective_configuration": config,
            "version": (re.search(r"info:  DXVK: (\S+)", text) or [None, None])[1]}


def from_sources(dxvk_log: Path, report: Path, manifest: Path | None) -> dict:
    text = dxvk_log.read_text(errors="replace")
    log = parse_dxvk_log(text)
    if manifest is not None:
        modelled, modelled_from = json.loads(manifest.read_text())["dxvk_conf"]["modelled_options"], str(manifest)
    else:   # the extractor's span table, which its extraction holds to the options the spans read
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import extract_refs
        modelled, modelled_from = extract_refs.modelled_options(), "test/extract_refs.py modelled_options()"
    if not modelled:
        raise Refusal(f"{modelled_from}: names no modelled DXVK option")
    set_modelled = sorted(k for k in log["effective_configuration"] if k in modelled)
    if set_modelled:
        raise Refusal(f"{dxvk_log}: DXVK's effective configuration sets {set_modelled}, which the state check's shims "
                      "answer with getOption's default")
    if log["version"] != "v3.1.1":
        raise Refusal(f"{dxvk_log}: DXVK {log['version']}, not v3.1.1")
    if not log["format_table_made"]:
        raise Refusal(f"{dxvk_log}: the log does not reach the device's creation, so its format table's remap lines say nothing")
    if log["builtin_config"]:
        raise Refusal(f"{dxvk_log}: DXVK applied a built-in configuration ({log['builtin_config'][0]}), which the state "
                      "check's shims do not model")
    feats = {}
    for path, (section, name) in FEATURES.items():
        v = log["sections"].get(section, {}).get(name)
        if v is None:
            raise Refusal(f"{dxvk_log}: no {section} {name} (DxvkDeviceFeatures {path})")
        feats[path] = bool(v)
    rep = json.loads(report.read_text())
    sg = rep["sdl_gpu"]
    if not sg.get("vk_query_ok"):
        raise Refusal(f"{report}: the device's Vulkan property query did not run ({sg.get('vk_query')})")
    if log["device"] != sg["device_name"]:
        raise Refusal(f"DXVK's device {log['device']!r} is not the second host's {sg['device_name']!r}")
    fill = log["sections"].get("Enabled features", {}).get("fillModeNonSolid")
    if fill is None:
        raise Refusal(f"{dxvk_log}: no fillModeNonSolid")
    vendor_check = vendor_cross_check(log["driver"], sg["vk_vendor_id"])
    clamp_check = clip_and_clamp_cross_check(log, sg, dxvk_log)
    return {
        "schema": "sa2-d3d9gpu-check-params/v1",
        "source": f"DXVK log {dxvk_log} (sha256 {hashlib.sha256(text.encode()).hexdigest()}); device report {report}",
        "axis": None,
        "device_name": sg["device_name"],
        "dxvk_effective_configuration": log["effective_configuration"],
        "dxvk_modelled_options": {"from": modelled_from, "options": modelled,
                                  "set_by_the_effective_configuration": set_modelled},
        "checks": {"vendor_id": vendor_check,
                   "point_size_max": "one source: the second host's Vulkan query (DXVK's log prints no pointSizeRange); "
                                     "both sides' POINTSIZE_MAX default is this value, so the defaults check holds the rule",
                   "depth_clip_and_clamp": clamp_check},
        "dxvk": {"vendor_id": sg["vk_vendor_id"], "point_size_max": sg["vk_point_size_max"],
                 "d24s8_supported": not log["d24s8_remapped"], "d16s8_supported": not log["d16s8_remapped"],
                 "features": feats},
        "sdl": {"supports_fill_mode_non_solid": bool(fill)},
        "device": {"d24s8_supported": sg["depth_format"] == "D24_UNORM_S8_UINT",
                   "point_size_max": sg["vk_point_size_max"],
                   "sampler_clamp_to_border": bool(sg["sampler_clamp_to_border"]),
                   "depth_clip_and_clamp": bool(sg["depth_clip_and_clamp"]),
                   "capability_override": sg["capability_override"]},
    }


def clip_and_clamp_cross_check(log: dict, sg: dict, dxvk_log: Path) -> str:
    """The device's detected depth clip and clamp against DXVK's log of the same driver."""
    for key in ("sdl_capabilities_present", "detected", "sampler_clamp_to_border", "depth_clip_and_clamp",
                "capability_override"):
        if key not in sg:
            raise Refusal(f"the device report's sdl_gpu has no {key} (a report from before the SDL patch's phase B)")
    detected = sg["detected"]["depth_clip_and_clamp"]
    if sg["capability_override"] is None and (sg["sampler_clamp_to_border"] != sg["detected"]["sampler_clamp_to_border"] or
                                              sg["depth_clip_and_clamp"] != detected):
        raise Refusal("the device used capabilities other than the ones it detected, under no override")
    if not sg["sdl_capabilities_present"]:
        if detected or sg["detected"]["sampler_clamp_to_border"]:
            raise Refusal("a stock SDL's device reports a capability of the patch's")
        return "not cross-checked: a stock SDL3 reports neither of the patch's capabilities"
    clip = log["sections"].get("VK_EXT_depth_clip_enable", {}).get("depthClipEnable")
    clamp = log["sections"].get("Enabled features", {}).get("depthClamp")
    if clip is None or clamp is None:
        raise Refusal(f"{dxvk_log}: no VK_EXT_depth_clip_enable depthClipEnable or Enabled features depthClamp")
    dxvk = bool(clip) and bool(clamp)
    if detected != dxvk:
        raise Refusal(f"the device detected depth clip and clamp {detected}; DXVK's log of the same driver reports "
                      f"depthClipEnable {clip} and depthClamp {clamp}")
    return (f"the device detected depth clip and clamp {detected}, as DXVK's log reports for the same driver "
            f"(depthClipEnable {clip}, depthClamp {clamp})")


AXES = {
    # every feature on, NVIDIA's vendor ID, D24S8 and no D16S8: the branch set of the machine DXVK
    # runs on here
    "nvidia-like": (0x10de, True, False, {k: True for k in FEATURES} | {"extDepthBiasControl.floatRepresentation": False,
                                                                        "extBorderColorSwizzle.borderColorSwizzleFromImage": False}),
    # AMD's vendor ID (DXVK's ATOC and depth-bias rules), float depth bias, no custom border colours
    "amd-like": (0x1002, True, True, {k: True for k in FEATURES} | {"extCustomBorderColor.customBorderColors": False,
                                                                    "extCustomBorderColor.customBorderColorWithoutFormat": False,
                                                                    "extNonSeamlessCubeMap.nonSeamlessCubeMap": False}),
    # Intel's vendor ID, no D24S8 nor D16S8 (DXVK's D32 fallbacks), no depth-bias control, no feedback
    # loops, no extended dynamic state 3
    "intel-like": (0x8086, False, False, {k: False for k in FEATURES} | {"core.features.depthBounds": True}),
}


# The SDL patch's capabilities per axis (the second host's side): both on the NVIDIA-like and
# AMD-like axes, neither on the Intel-like one, so the sweep maps BORDER and the depth clamp both
# ways: the patch's, and the shader route's fallback.
AXIS_CAPABILITIES = {"nvidia-like": True, "amd-like": True, "intel-like": False}


def axis(name: str) -> dict:
    vendor, d24, d16, feats = AXES[name]
    caps = AXIS_CAPABILITIES[name]
    return {
        "schema": "sa2-d3d9gpu-check-params/v1",
        "source": f"sweep axis {name}: a parameter set for the GPU-free sweep, not a claim about any device",
        "axis": name,
        "device_name": None,
        "dxvk": {"vendor_id": vendor, "point_size_max": 64.0, "d24s8_supported": d24, "d16s8_supported": d16,
                 "features": feats},
        "sdl": {"supports_fill_mode_non_solid": True},
        "device": {"d24s8_supported": d24, "point_size_max": 64.0, "sampler_clamp_to_border": caps,
                   "depth_clip_and_clamp": caps, "capability_override": None},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dxvk-log")
    ap.add_argument("--device-report")
    ap.add_argument("--extract-manifest", help="the extraction's extract_manifest.json (its dxvk_conf.modelled_options; "
                                               "default: extract_refs.py's span table, which the extraction holds to it)")
    ap.add_argument("--axis", choices=sorted(AXES))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        if a.axis:
            p = axis(a.axis)
        else:
            if not a.dxvk_log or not a.device_report:
                ap.error("--dxvk-log and --device-report, or --axis")
            p = from_sources(Path(a.dxvk_log), Path(a.device_report),
                             Path(a.extract_manifest) if a.extract_manifest else None)
    except Refusal as e:
        print(f"CHECK_PARAMS_REFUSED: {e}", file=sys.stderr)
        return 2
    Path(a.out).write_text(json.dumps(p, indent=1) + "\n")
    print(f"CHECK_PARAMS_OK {p['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
