package com.sa2recomp.app;

import org.json.JSONObject;

/**
 * What the app says about Vulkan, as plain functions of what it read, so a test holds each text to
 * literal versions (Documentation/scripts/tests/test_android_vulkan.py) without a phone.
 *
 * The renderer (host/d3d9gpu) needs Vulkan 1.1. On a driver that offers 1.1 or 1.2 it makes its device
 * at 1.1 and lowers its shaders to SPIR-V 1.3 (its Vulkan 1.1 profile); on 1.3 or later it keeps its 1.3
 * profile. It decides from the driver's own apiVersion, which the system's declared Vulkan version
 * (android.hardware.vulkan.version, which the manufacturer sets) can understate, so nothing here guesses
 * the profile from the declared version: the crash report names the profile only from the player's own
 * record (d3d9gpu.runrec, sdl_gpu's "vk11" member), and only a version below 1.1 is warned about.
 *
 * A version is android.hardware.vulkan.version's value, encoded as VK_MAKE_VERSION; -1 when the system
 * declares none.
 */
final class VulkanSupport {
    private VulkanSupport() {}

    /** True when the declared version is 1.1 or later, which the renderer needs. */
    static boolean atLeast11(int version) {
        if (version < 0) return false;
        int major = version >>> 22, minor = (version >>> 12) & 0x3ff;
        return major > 1 || (major == 1 && minor >= 1);
    }

    /** "major.minor", or "none". */
    static String shortText(int version) {
        return version < 0 ? "none" : (version >>> 22) + "." + ((version >>> 12) & 0x3ff);
    }

    /** The reports' "vulkan:" line: the declared version and level (-1 none), warned only below 1.1. */
    static String declaredText(int version, int level) {
        if (version < 0) return "none declared (the renderer needs Vulkan 1.1)";
        return (version >>> 22) + "." + ((version >>> 12) & 0x3ff) + "." + (version & 0xfff)
               + (level >= 0 ? ", level " + level : "") + (atLeast11(version) ? "" : " (the renderer needs 1.1)");
    }

    /** The setup screen's warning for a system that declares no Vulkan 1.1; empty otherwise. */
    static String setupNote(int version) {
        if (atLeast11(version)) return "";
        return "\n\nNote: this phone's graphics driver "
               + (version < 0 ? "declares no Vulkan support" : "reports Vulkan " + shortText(version))
               + ". OpenSA2's renderer needs Vulkan 1.1 or later, so the game will probably not start on this phone.";
    }

    /**
     * The crash report's "gpu device:" line's account of the renderer's Vulkan profile, from the player's
     * sdl_gpu record: empty on the 1.3 profile (the record has no "vk11" member, so the line reads as it
     * did before the 1.1 profile existed); "; Vulkan 1.1 profile" otherwise, with the device's own
     * version and the switch that forced it (debug.sa2.vk11=1) where the record names them.
     */
    static String profileText(JSONObject sdlGpu) {
        JSONObject vk11 = sdlGpu == null ? null : sdlGpu.optJSONObject("vk11");
        if (vk11 == null) return "";
        String device = vk11.isNull("device_api_version") ? "" : vk11.optString("device_api_version", "");
        String forced = vk11.isNull("forced") ? "" : vk11.optString("forced", "");
        String detail = (device.isEmpty() ? "" : "the device reports Vulkan " + device)
                        + (!device.isEmpty() && !forced.isEmpty() ? "; " : "")
                        + (forced.isEmpty() ? "" : "forced by " + forced);
        return "; Vulkan 1.1 profile" + (detail.isEmpty() ? "" : " (" + detail + ")");
    }
}
