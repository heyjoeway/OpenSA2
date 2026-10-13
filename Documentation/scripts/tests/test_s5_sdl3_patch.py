"""The local SDL patch (border addressing and depth clamp), held on lavapipe, offscreen.

The patch (patches/sdl3/, the user's ruling of 2026-10-04 in
Documentation/decisions/2026-10-04-S5-user-rulings-2.md) is checked by
tools/s5/sdl3-patches/test/border_clamp.c, built here against an SDL install and run on lavapipe
(VK_DRIVER_FILES names its ICD; a missing ICD fails, it does not skip). Every check compares
pixels with literal expected values:

  * the two device capabilities, true on a default device, clip-and-clamp false when the device
    is created with depth clamping off, and both true through SDL_GPUVulkanOptions at 1.3 with
    an application depth-clip struct in its feature list;
  * clamp-to-border with each of the three border colours, NEAREST and LINEAR, and the default
    colour; CLAMP_TO_EDGE as the control that must not read a border colour;
  * depth clamp beside depth clip, through a depth bias and through a shader-written depth,
    against clip only; depth clip still on with the clamp (a quad beyond the far plane clipped)
    against clamp only;
  * refusals: an out-of-range border colour or address mode, and the clamp property on a device
    without it, in debug mode (one SDL assertion each) and in the backend without debug mode.

The full check runs with the Khronos validation layer (private/s5-tools/install-shader-tools)
and the project's capture layer (host/d3d9gpu/test/vkcap, built here into a temporary
directory); tools/s5/sdl3-patches/test/check_vk_record.py proves both layers were inserted into
every device and holds what the patch asked of Vulkan to the specification's rules, the half the
pixels cannot see (a depth-clip structure without its extension draws the same).

The library is marked as modified with the exact identity of the tracked patch bytes:
SDL_GetRevision() must read "SDL-<the pinned tarball's REVISION.txt> (sa2-recomp sdl3-patches
<id>)", <id> derived from patches/sdl3/*.patch as the patch header states, so a library built
from any other patch set fails.

Both configurations the ruling names are exercised: the patched library (tools/pins.json
sdl3.library, the shared install) and the stock configuration's own prefix (tools.sdl3-stock,
built by tools/s5/build_host_deps.sh --sdl3=stock), whose stock-API draws must give the same
bytes and which must report neither capability: the run-time detection a stock build takes its
shader fallback by (host 2's own detection, and the fallback it then owes, are held by
Documentation/scripts/tests/test_s5_sdl3_detect.py).

The quiet window: the shared install is replaced by tools/s5/build_host_deps.sh (the I4 landing),
and nothing may run against it meanwhile: not this test, not R1's GPU script or its C7, C9 and
C10 DXVK runs, not the input-latency plan's compare_hosts.py dxvk-ab or its end-to-end session,
and not a ship whose gates build or run the players. This test holds its half: each library's
sha256 must equal its pin before anything runs, which a half-written or replaced install does
not. SA2_SDL3_PREFIX names another patched build for a mutant run (the pin guard is then off and
the exact identity still holds), and is refused unless SA2_SDL3_MUTANT_RUN=1 is set beside it, so
an inherited value cannot weaken a gate run unseen; the gates set neither. The repository is
always this file's own: SA2_REPO is not read (an inherited one would point the check at another
tree), and each subprocess is given this repository as SA2_REPO.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEST = ROOT / "tools" / "s5" / "sdl3-patches" / "test"
PATCHES = ROOT / "patches" / "sdl3"
VKCAP_BUILD = ROOT / "host" / "d3d9gpu" / "test" / "vkcap" / "build_vkcap.sh"
VALIDATION = ROOT / "private" / "s5-tools" / "install-shader-tools"
LVP = Path("/usr/share/vulkan/icd.d/lvp_icd.json")

EXPECTED = [
    "neutral.clamp_to_edge", "neutral.clip_only_depth",
    "border.nearest.transparent_black", "border.nearest.opaque_black", "border.nearest.opaque_white",
    "border.linear.transparent_black", "border.linear.opaque_black", "border.linear.opaque_white",
    "border.default_color", "border.control_clamp_to_edge",
    "clamp.depth_bias.clip_only", "clamp.depth_bias.clip_and_clamp",
    "clamp.frag_depth.clip_only", "clamp.frag_depth.clip_and_clamp",
    "clip.far_plane.clip_and_clamp", "clip.far_plane.clip_only", "clip.far_plane.clamp_only",
    "properties.default", "properties.depth_clamping_off", "properties.vulkan13_options",
    "validation.debug.border_color_out_of_range", "validation.debug.address_mode_out_of_range",
    "validation.debug.clamp_without_capability", "validation.debug.valid_border_sampler",
    "validation.backend.clamp_without_capability", "validation.backend.border_color_out_of_range",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sdl3_pin() -> dict:
    return json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]["sdl3"]


def patch_identity() -> str:
    """The vendor identity of the tracked patch set, as the patch header defines it: the first 12
    hex digits of the sha256 over each patch's sha256 (64 lowercase hex digits and a newline), in
    order."""
    files = sorted(PATCHES.glob("*.patch"))
    assert files, f"no patch under {PATCHES}"
    pinned = sdl3_pin().get("patches")
    if pinned is not None:
        assert [p["path"] for p in pinned] == [str(f.relative_to(ROOT)) for f in files], (pinned, files)
        assert [p["sha256"] for p in pinned] == [sha256(f) for f in files], "a patch does not hash to its pin"
    line = "".join(sha256(f) + "\n" for f in files)
    return "sa2-recomp sdl3-patches " + hashlib.sha256(line.encode()).hexdigest()[:12]


def expected_revision() -> str:
    with tarfile.open(ROOT / sdl3_pin()["path"]) as tar:
        version = tar.extractfile(f"SDL3-{sdl3_pin()['version']}/REVISION.txt").read().decode().strip()
    return f"SDL-{version} ({patch_identity()})"


def checked_prefix(prefix: Path, pinned_sha: str | None) -> Path:
    lib = prefix / "lib" / "libSDL3.so.0"
    assert lib.exists(), f"no SDL library at {lib}"
    if pinned_sha is not None:
        assert sha256(lib.resolve()) == pinned_sha, (
            f"{lib} is not the pinned build (mid-rebuild, or the pin moved): nothing runs against it")
    return prefix


def patched_prefix() -> Path:
    override = os.environ.get("SA2_SDL3_PREFIX")
    if override:
        assert os.environ.get("SA2_SDL3_MUTANT_RUN") == "1", (
            f"SA2_SDL3_PREFIX={override} is set: it turns the pin guard off and is for a mutant run only "
            "(SA2_SDL3_MUTANT_RUN=1 beside it); a gate run sets neither")
        print(f"note: SA2_SDL3_PREFIX={override}: a mutant run, the pin guard is off")
        return checked_prefix(Path(override), None)
    pin = sdl3_pin()
    assert pin.get("patches"), "tools/pins.json pins no SDL patch: the shared install is stock SDL"
    return checked_prefix(ROOT / Path(pin["library"]).parent.parent, pin["library_sha256"])


def stock_prefix() -> Path:
    stock = json.loads((ROOT / "tools" / "pins.json").read_text())["tools"].get("sdl3-stock")
    assert stock, "tools/pins.json pins no sdl3-stock: the stock configuration cannot be exercised"
    prefix = ROOT / stock["prefix"]
    assert str(stock["prefix"]).startswith("private/s5-tools/"), stock
    assert stock["library"] == f"{stock['prefix']}/lib/libSDL3.so.0", stock
    return checked_prefix(prefix, stock["library_sha256"])


def run(prefix, out, *extra):
    assert LVP.exists(), f"the lavapipe ICD {LVP} is missing: the SDL patch's check needs it (it fails, it does not skip)"
    r = subprocess.run(["bash", str(TEST / "run_border_clamp.sh"), str(prefix), str(out), "lvp", *extra],
                       capture_output=True, text=True, env={**os.environ, "SA2_REPO": str(ROOT)})
    rows = [json.loads(l) for l in r.stdout.splitlines() if l.startswith('{"test"')]
    return r, rows


@pytest.fixture(scope="module")
def vkcap(tmp_path_factory):
    out = tmp_path_factory.mktemp("vkcap")
    r = subprocess.run(["bash", str(VKCAP_BUILD), str(out)], capture_output=True, text=True,
                       env={**os.environ, "SA2_REPO": str(ROOT)})
    assert r.returncode == 0 and "VKCAP_BUILD_OK" in r.stdout, r.stdout + r.stderr
    return out / "vkcap"


def test_the_patched_library_passes_every_literal_check(tmp_path, vkcap):
    assert (VALIDATION / "share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json").exists(), \
        f"no validation layer under {VALIDATION}: the check runs with it (it fails, it does not skip)"
    r, rows = run(patched_prefix(), tmp_path, "--capture", vkcap, "--validation", VALIDATION)
    assert [row["test"] for row in rows] == EXPECTED, r.stdout[-3000:]
    failed = [row for row in rows if not row["ok"]]
    assert not failed, failed
    lines = r.stdout.splitlines()
    assert r.returncode == 0 and lines[-1] == "BORDER_CLAMP_OK", r.stdout[-3000:]
    assert '{"validation_layer": "%s", "validation_errors": 0}' % VALIDATION.resolve() in lines
    assert "VK_RECORD_OK" in lines
    record = json.loads(next(l for l in lines if l.startswith('{"vk_record"')))["vk_record"]
    assert record["layers_inserted"] == {"VK_LAYER_SA2_vkcap": record["devices"],
                                         "VK_LAYER_KHRONOS_validation": record["devices"]}
    assert record["devices"] == 6 and record["clip_and_clamp_pipelines"] == 4 and record["problems"] == []
    head = json.loads(lines[0])
    assert head["sdl_revision"] == expected_revision(), head


def test_stock_api_draws_are_unchanged_by_the_patch(tmp_path):
    _, patched = run(patched_prefix(), tmp_path / "patched", "--neutral-only")
    _, stock = run(stock_prefix(), tmp_path / "stock", "--neutral-only")
    assert [r["test"] for r in patched] == ["neutral.clamp_to_edge", "neutral.clip_only_depth"]
    assert all(r["ok"] for r in patched)
    assert patched == stock


def test_stock_sdl_reports_neither_capability(tmp_path):
    r, stock = run(stock_prefix(), tmp_path / "stock", "--stock-detect")
    assert r.returncode == 0 and [(x["test"], x["ok"]) for x in stock] == [("properties.stock_absent", True)], r.stdout
    assert stock[0]["detail"] == "border 0 (present 0), clip_and_clamp 0 (present 0)"
    r, patched = run(patched_prefix(), tmp_path / "patched", "--stock-detect")
    assert r.returncode == 1 and [(x["test"], x["ok"]) for x in patched] == [("properties.stock_absent", False)]
    assert patched[0]["detail"] == "border 1 (present 1), clip_and_clamp 1 (present 1)"
