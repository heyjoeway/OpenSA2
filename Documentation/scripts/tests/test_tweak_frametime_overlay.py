"""The host tweak frametime-overlay (host/player/tweaks/frametime-overlay.cpp): a box at the window's
top left with FPS, MS (mean and longest) and LATE, drawn over the swapchain only.

  * the meter: its numbers from presents given by hand, against literal lines (the window of
    about a second, a refresh at most every 0.25 s, a missed sample left out of MAX, a stall longer
    than the window, LATE "--" without a schedule);
  * the box's pixels: each line rendered and read back glyph by glyph with the font the file
    defines, and the scale it is drawn at on a target of each size;
  * its place (place, touch_controls): inside the window's safe area, and clear of the touch
    controls while they are drawn, on an Android-shaped 2400x1080 window with the default touch
    layout (the box under LT, never over LT or RT), with a cutout's inset, upright, and with
    controls that leave it no room;
  * drawing (on a Vulkan device, no window, as test_touch_overlay): with the tweak off nothing is
    drawn, the target unchanged to the byte; on, the box is the only change and reads as the
    numbers of the presents the schedule (pace.h) counted, which this test computes itself; with
    the touch controls drawn on a 2400x1080 target the box is drawn below LT, the only change;
  * release: on a Vulkan device in SDL's debug mode with the Khronos validation layer
    (tools/s5/validation_tools.py's pinned one), a device destroyed after release() reports no
    object left, one destroyed without it reports the box's texture and buffer; after release()
    the box draws afresh on a second device;
  * the player (host/d3d9gpu/build.sh's sa2_player_gpu, built from this tree unless
    SA2_PLAYER_GPU names one): the synthetic stream played live in a play session (--interactive,
    --input-path, --pace and --pace-log, --overlay-out) with SA2_TWEAKS=frametime-overlay. Each
    frame-N-swapchain.png holds the box, which reads as the numbers --pace-log's rows give for
    the presents before N, and frame-N-game.png does not: the two pictures differ inside the box
    alone. The frames file (read back before Present) is the same, frame for frame, as a run
    without the tweak, and the run says the tweak's lines.

  .venv/bin/python -m pytest Documentation/scripts/tests/test_tweak_frametime_overlay.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_player_pads_touch as PT  # noqa: E402

PLAYER = ROOT / "host/player"
SOURCE = PLAYER / "tweaks/frametime-overlay.cpp"
GXX = shutil.which(os.environ.get("CXX", "g++"))
INSTALL = Path(os.environ.get("SA2_S5_INSTALL") or ROOT / "private/s5-tools/install").resolve()

COLUMNS, LINES, GLYPH_W, GLYPH_H, CELL_W, CELL_H, PAD = 18, 3, 5, 7, 6, 9, 2
BOX_W, BOX_H = 2 * PAD + COLUMNS * CELL_W - 1, 2 * PAD + LINES * CELL_H - 2
WINDOW_S, REFRESH_S = 1.0, 0.25


def font() -> dict[tuple[int, ...], str]:
    """The file's glyphs, rows to character."""
    text = SOURCE.read_text()
    glyphs = {tuple(int(v, 16) for v in rows.split(",")): c
              for c, rows in re.findall(r"\{'(.)', \{([0-9xA-F, ]+)\}\}", text)}
    assert len(glyphs) == 22, "every glyph distinct, and the font has the 22 the lines use"
    return glyphs


def scale_for(w: int, h: int) -> int:
    """frametime-overlay.cpp scale_for, as its comment says it: from the short side."""
    s = max(1, (min(w, h) + 300) // 540)
    while s and not (BOX_W * s + 8 * s <= w and BOX_H * s + 8 * s <= h):
        s -= 1
    return s


def read_box(rgb: bytes, w: int, h: int, channels: int = 3, at: tuple[int, int, int] | None = None) -> list[str]:
    """The box's lines read from a w x h picture (RGB rows, or BGRA with channels=4), the box at
    `at` (x, y, scale), by default the top left corner of a window with no touch controls."""
    glyphs = font()
    bx, by, s = at or (4 * scale_for(w, h), 4 * scale_for(w, h), scale_for(w, h))
    assert s, f"no box fits {w}x{h}"

    def ink(nx: int, ny: int) -> bool:
        x, y = bx + nx * s + s // 2, by + ny * s + s // 2
        i = (y * w + x) * channels
        return sum(rgb[i:i + 3]) > 3 * 160

    out = []
    for line in range(LINES):
        chars = ""
        for col in range(COLUMNS):
            x0, y0 = PAD + col * CELL_W, PAD + line * CELL_H
            rows = tuple(sum(0x10 >> x for x in range(GLYPH_W) if ink(x0 + x, y0 + y)) for y in range(GLYPH_H))
            chars += glyphs.get(rows, "?")
        out.append(chars.rstrip())
    return out


def box_rect(w: int, h: int) -> tuple[int, int, int, int]:
    s = scale_for(w, h)
    return 4 * s, 4 * s, BOX_W * s, BOX_H * s


def five(v: float | None) -> str:
    if v is None:
        return "   --"
    v = min(max(v, 0.0), 99999.0)
    return f"{v:5.1f}" if v < 999.95 else f"{v:5.0f}"


def meter(samples: list[tuple[int, float, int]], paced: bool = True) -> list[list[str]]:
    """The box's lines after each sample (count, at, late), by the rules the file's comment gives."""
    window, refreshed, lines, out = [], None, ["FPS    --", "MS    -- MAX    --", "LATE --"], []
    for count, at, late in samples:
        if window and count <= window[-1][0]:
            out.append(lines)
            continue
        window.append((count, at))
        while len(window) > 1 and at - window[1][1] >= WINDOW_S:
            window.pop(0)
        if refreshed is None or at - refreshed >= REFRESH_S:
            refreshed = at
            (c0, a0), (c1, a1) = window[0], window[-1]
            n, span = c1 - c0, a1 - a0
            gaps = [b[1] - a[1] for a, b in zip(window, window[1:]) if b[0] == a[0] + 1]
            lines = [f"FPS {five(n / span if n and span > 0 else None)}",
                     f"MS {five(span * 1e3 / n if n and span > 0 else None)} MAX {five(max(gaps) * 1e3 if gaps else None)}",
                     f"LATE {late if paced else '--'}"]
        out.append(lines)
    return out


DRIVER = r"""
#include "tweaks/frametime-overlay.h"
#include "pace.h"
#include "texcodec.h"
#include "touch_overlay.h"
#include "tweaks.h"

#include <SDL3/SDL.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

using namespace sa2_frametime;

struct Target {
    SDL_GPUDevice* dev;
    SDL_GPUTexture* tex;
    SDL_GPUTransferBuffer* down;
    uint32_t w, h;
};

// Touch controls with no device (they draw nothing), w x h, for a WxH frame (0: none known); with
// `active`, as after a finger came down on no control, so they are drawn.
static sa2_touch::Overlay* controls(uint32_t w, uint32_t h, uint32_t fw, uint32_t fh, bool active) {
    sa2_touch::Overlay* o = sa2_touch::create(nullptr, nullptr);
    sa2_touch::set_size(o, w, h);
    sa2_touch::set_frame(o, fw, fh);
    if (active) {
        SDL_Event e{};
        e.type = SDL_EVENT_FINGER_DOWN;
        e.tfinger.touchID = 7;
        e.tfinger.fingerID = 1;
        e.tfinger.x = 0.75f;
        e.tfinger.y = 0.5f;
        sa2_touch::handle_event(o, e);
    }
    return o;
}

// The target and its readback buffer on a Vulkan device (SDL's debug mode with `debug`).
static bool make_target(Target& t, bool debug) {
    t.dev = SDL_CreateGPUDevice(SDL_GPU_SHADERFORMAT_SPIRV, debug, "vulkan");
    if (!t.dev) { std::printf("FAIL SDL_CreateGPUDevice: %s\n", SDL_GetError()); return false; }
    SDL_GPUTextureCreateInfo ti{};
    ti.type = SDL_GPU_TEXTURETYPE_2D;
    ti.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
    ti.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
    ti.width = t.w;
    ti.height = t.h;
    ti.layer_count_or_depth = 1;
    ti.num_levels = 1;
    ti.sample_count = SDL_GPU_SAMPLECOUNT_1;
    t.tex = SDL_CreateGPUTexture(t.dev, &ti);
    SDL_GPUTransferBufferCreateInfo bi{SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, t.w * t.h * 4, 0};
    t.down = SDL_CreateGPUTransferBuffer(t.dev, &bi);
    if (!t.tex || !t.down) { std::printf("FAIL target: %s\n", SDL_GetError()); return false; }
    return true;
}

static void drop_target(Target& t) {
    SDL_ReleaseGPUTexture(t.dev, t.tex);
    SDL_ReleaseGPUTransferBuffer(t.dev, t.down);
    SDL_DestroyGPUDevice(t.dev);
    t = Target{nullptr, nullptr, nullptr, t.w, t.h};
}

static const sa2_touch::Overlay* g_touch = nullptr;   // the controls the box keeps clear of

// One Present's worth: the target cleared to a colour, the box drawn for `frame`, and (when `px`)
// the target read back as BGRA rows.
static bool present(Target& t, uint32_t frame, const Sa2Pace* pace, std::vector<uint8_t>* px) {
    SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(t.dev);
    if (!cb) return false;
    SDL_GPUColorTargetInfo ct{};
    ct.texture = t.tex;
    ct.load_op = SDL_GPU_LOADOP_CLEAR;
    ct.store_op = SDL_GPU_STOREOP_STORE;
    ct.clear_color = SDL_FColor{0.0f, 0.25f, 0.5f, 1.0f};
    SDL_EndGPURenderPass(SDL_BeginGPURenderPass(cb, &ct, 1, nullptr));
    draw(t.dev, cb, t.tex, t.w, t.h, frame, pace, g_touch, nullptr);
    if (px) {
        SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
        SDL_GPUTextureRegion from{};
        from.texture = t.tex;
        from.w = t.w;
        from.h = t.h;
        from.d = 1;
        SDL_GPUTextureTransferInfo to{t.down, 0, 0, 0};
        SDL_DownloadFromGPUTexture(cp, &from, &to);
        SDL_EndGPUCopyPass(cp);
    }
    SDL_GPUFence* f = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
    if (!f) return false;
    SDL_WaitForGPUFences(t.dev, true, &f, 1);
    SDL_ReleaseGPUFence(t.dev, f);
    if (!px) return true;
    const uint8_t* p = static_cast<const uint8_t*>(SDL_MapGPUTransferBuffer(t.dev, t.down, false));
    if (!p) return false;
    px->assign(p, p + size_t(t.w) * t.h * 4);
    SDL_UnmapGPUTransferBuffer(t.dev, t.down);
    return true;
}

int main(int argc, char** argv) {
    const std::string mode = argv[1];
    if (mode == "meter") {               // stdin: "count at late paced" per line
        Meter m;
        unsigned long long count, late;
        double at;
        int paced;
        while (std::scanf("%llu %lf %llu %d", &count, &at, &late, &paced) == 4) {
            const bool changed = m.add(count, at, late, paced != 0);
            std::printf("%d|%s|%s|%s\n", changed, m.lines()[0].c_str(), m.lines()[1].c_str(), m.lines()[2].c_str());
        }
        return 0;
    }
    if (mode == "render") {              // argv[2..4] the lines, argv[5] the RGBA file
        const std::vector<uint8_t> px = render({argv[2], argv[3], argv[4]});
        FILE* f = std::fopen(argv[5], "wb");
        std::fwrite(px.data(), 1, px.size(), f);
        std::fclose(f);
        std::printf("%u %u\n", kBoxW, kBoxH);
        return 0;
    }
    if (mode == "scale") {
        std::printf("%u\n", scale_for(uint32_t(std::atoi(argv[2])), uint32_t(std::atoi(argv[3]))));
        return 0;
    }
    if (mode == "place") {               // W H SX SY SW SH TOUCH FW FH: the controls' boxes, then the spot
        const uint32_t w = uint32_t(std::atoi(argv[2])), h = uint32_t(std::atoi(argv[3]));
        const Rect safe{float(std::atof(argv[4])), float(std::atof(argv[5])), float(std::atof(argv[6])),
                        float(std::atof(argv[7]))};
        sa2_touch::Overlay* o = controls(w, h, uint32_t(std::atoi(argv[9])), uint32_t(std::atoi(argv[10])),
                                         std::atoi(argv[8]) != 0);
        const std::vector<Rect> boxes = touch_controls(o, w, h);
        for (const Rect& r : boxes) std::printf("CONTROL %.3f %.3f %.3f %.3f\n", r.x, r.y, r.w, r.h);
        Spot at;
        if (place(safe, boxes, &at)) std::printf("SPOT %u %u %u\n", at.x, at.y, at.scale);
        else std::printf("NONE\n");
        sa2_touch::destroy(o);
        return 0;
    }
    if (!SDL_Init(SDL_INIT_VIDEO)) { std::printf("FAIL SDL_Init: %s\n", SDL_GetError()); return 1; }
    Target t{nullptr, nullptr, nullptr, uint32_t(std::atoi(argv[2])), uint32_t(std::atoi(argv[3]))};
    const std::string out = argv[4];
    std::vector<uint8_t> px;
    auto save = [&](const char* name, const std::vector<uint8_t>& px) {
        FILE* f = std::fopen((out + "/" + name).c_str(), "wb");
        std::fwrite(px.data(), 1, px.size(), f);
        std::fclose(f);
    };
    if (mode == "leak") {                // W H OUT RELEASE: a device in debug mode destroyed after the box
        const bool rel = std::atoi(argv[5]) != 0;
        sa2tw::g_on = sa2tw::SA2_TWEAK_FRAMETIME_OVERLAY.mask;
        if (!make_target(t, true)) return 1;
        for (uint32_t k = 1; k <= 3; k++)
            if (!present(t, k, nullptr, nullptr)) { std::printf("FAIL present: %s\n", SDL_GetError()); return 1; }
        if (rel) release();
        std::fprintf(stderr, "DESTROY_DEVICE\n");
        drop_target(t);
        std::fprintf(stderr, "DEVICE_DESTROYED\n");
        if (rel) {                       // afresh on a second device
            if (!make_target(t, true)) return 1;
            if (!present(t, 4, nullptr, &px)) { std::printf("FAIL present: %s\n", SDL_GetError()); return 1; }
            save("again.bgra", px);
            release();
            drop_target(t);
        }
        std::printf("LEAK_OK\n");
        return 0;
    }
    // "gpu W H OUT [touch]": off, then on over a schedule of presents; OUT gets off.bgra, on.bgra and
    // the presents' times (presents.tsv: count, at, due, late so far). With "touch", the touch
    // controls are drawn (as after a finger came down) for the box to keep clear of.
    if (!make_target(t, false)) return 1;
    sa2_touch::Overlay* touch = argc > 5 ? controls(t.w, t.h, 0, 0, true) : nullptr;
    g_touch = touch;
    Sa2Pace pace(1.0, -1.0);             // a file replay's schedule: it never moves
    setup(true, true);                   // off: says nothing
    if (!present(t, 1, &pace, &px)) { std::printf("FAIL present: %s\n", SDL_GetError()); return 1; }
    save("off.bgra", px);
    sa2tw::g_on = sa2tw::SA2_TWEAK_FRAMETIME_OVERLAY.mask;
    setup(true, true);
    FILE* times = std::fopen((out + "/presents.tsv").c_str(), "w");
    // 90 presents at 60 Hz from t = 10 s, the 30th 40 ms late and the 31st 20 ms late (then back
    // on time); the box drawn for frame k + 1 after the k-th present, as the player draws it.
    const uint32_t n = 90;
    for (uint32_t k = 1; k <= n; k++) {
        const uint64_t vus = uint64_t(k - 1) * 16667;
        const double ready = 10.0 + double(k - 1) * 16667e-6;
        const Sa2Pace::Slot slot = pace.schedule(vus, ready);
        const double at = slot.due + (k == 30 ? 0.040 : k == 31 ? 0.020 : 0.0005);
        pace.presented(vus, slot, at);
        std::fprintf(times, "%u\t%.9f\t%.9f\t%llu\n", k, at, slot.due, (unsigned long long)pace.over_frame());
        if (!present(t, k + 1, &pace, k == n ? &px : nullptr)) { std::printf("FAIL present: %s\n", SDL_GetError()); return 1; }
    }
    std::fclose(times);
    save("on.bgra", px);
    release();
    drop_target(t);
    sa2_touch::destroy(touch);
    std::printf("GPU_OK\n");
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("frametime")
    (d / "driver.cpp").write_text(DRIVER)
    exe = d / "driver"
    done = subprocess.run([GXX, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter", f"-I{PLAYER}",
                           f"-I{INSTALL / 'include'}", "-o", str(exe), str(d / "driver.cpp"), str(SOURCE),
                           str(PLAYER / "tweaks.cpp"), str(PLAYER / "texcodec.cpp"), str(PLAYER / "touch_overlay.cpp"),
                           f"-L{INSTALL / 'lib'}",
                           "-Wl,--disable-new-dtags", f"-Wl,-rpath,{INSTALL / 'lib'}", "-lSDL3", "-lpng"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return exe


def run_meter(exe: Path, samples: list[tuple[int, float, int, int]]) -> list[tuple[int, list[str]]]:
    stdin = "".join(f"{c} {a!r} {late} {p}\n" for c, a, late, p in samples)
    out = subprocess.run([str(exe), "meter"], input=stdin, capture_output=True, text=True, check=True).stdout
    return [(int(row.split("|")[0]), row.split("|")[1:]) for row in out.splitlines()]


def test_the_meter_against_literal_lines(driver):
    got = run_meter(driver, [
        (1, 10.0, 0, 1),       # one present: no rate yet
        (1, 10.1, 0, 1),       # no new present
        (2, 10.02, 0, 1),      # within 0.25 s of the last refresh: the numbers stay
        (16, 10.25, 1, 1),     # 15 presents in 0.25 s; 2 to 16 is a missed run, left out of MAX
        (17, 11.3, 1, 1),      # a 1.05 s stall: the window keeps the present before it
    ])
    assert got == [
        (1, ["FPS    --", "MS    -- MAX    --", "LATE 0"]),
        (0, ["FPS    --", "MS    -- MAX    --", "LATE 0"]),
        (0, ["FPS    --", "MS    -- MAX    --", "LATE 0"]),
        (1, ["FPS  60.0", "MS  16.7 MAX  20.0", "LATE 1"]),
        (1, ["FPS   1.0", "MS  1050 MAX  1050", "LATE 1"]),
    ]


def test_without_a_schedule_late_is_not_counted(driver):
    assert run_meter(driver, [(1, 2.0, 0, 0), (31, 2.5, 7, 0)]) == [
        (0, ["FPS    --", "MS    -- MAX    --", "LATE --"]),   # the lines it starts with
        (1, ["FPS  60.0", "MS  16.7 MAX    --", "LATE --"]),
    ]


def test_the_meter_matches_its_rules_over_a_long_run(driver):
    """Three seconds at 60 Hz with a hitch every 50 presents: the C++ meter and this file's reading
    of its comment give the same lines after every present."""
    samples, at, late = [], 5.0, 0
    for k in range(1, 181):
        hitch = k % 50 == 0
        at += 0.050 if hitch else 1 / 60
        late += hitch
        samples.append((k, round(at, 9), late, 1))
    got = run_meter(driver, samples)
    assert [lines for _, lines in got] == meter([s[:3] for s in samples])
    assert got[-1][1][2] == "LATE 3"


@pytest.mark.parametrize("lines", [["FPS  59.9", "MS  16.7 MAX  33.4", "LATE 12345"],
                                   ["FPS    --", "MS    -- MAX    --", "LATE --"],
                                   ["FPS 999.9", "MS  1050 MAX 99999", "LATE 0"]])
def test_the_box_reads_back_as_its_lines(driver, tmp_path, lines):
    out = subprocess.run([str(driver), "render", *lines, str(tmp_path / "box.rgba")], capture_output=True, text=True,
                         check=True).stdout.split()
    assert (int(out[0]), int(out[1])) == (BOX_W, BOX_H)
    rgba = (tmp_path / "box.rgba").read_bytes()
    assert len(rgba) == BOX_W * BOX_H * 4
    # Read at scale 1 from the box's own pixels, placed as the player places it on a target.
    w, h = 640, 360
    assert scale_for(w, h) == 1
    canvas = bytearray(w * h * 3)
    for y in range(BOX_H):
        for x in range(BOX_W):
            i, j = (y * BOX_W + x) * 4, ((4 + y) * w + 4 + x) * 3
            canvas[j:j + 3] = rgba[i:i + 3]
    assert read_box(bytes(canvas), w, h) == lines


@pytest.mark.parametrize("w,h,want", [(1280, 720, 1), (1920, 1080, 2), (2560, 1440, 3), (3840, 2160, 4),
                                      (2400, 1080, 2), (1080, 2400, 2), (720, 1560, 1), (1440, 3120, 3),
                                      (119, 37, 1), (118, 37, 0), (300, 36, 0)])
def test_the_scale_on_each_target(driver, w, h, want):
    got = int(subprocess.run([str(driver), "scale", str(w), str(h)], capture_output=True, text=True,
                             check=True).stdout)
    assert got == scale_for(w, h) == want


def run_place(exe: Path, w: int, h: int, safe: tuple[float, float, float, float], touch: bool,
              frame: tuple[int, int] = (0, 0)) -> tuple[list[tuple[float, ...]], tuple[int, int, int] | None]:
    """touch_controls and place on a w x h target: the controls' boxes and the spot (x, y, scale)."""
    out = subprocess.run([str(exe), "place", str(w), str(h), *map(str, safe), str(int(touch)), *map(str, frame)],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    boxes = [tuple(float(v) for v in row.split()[1:]) for row in out if row.startswith("CONTROL ")]
    spot = next((tuple(int(v) for v in row.split()[1:]) for row in out if row.startswith("SPOT ")), None)
    assert spot or out[-1] == "NONE", out
    return boxes, spot


def meets(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def spot_rect(spot: tuple[int, int, int]) -> tuple[int, int, int, int]:
    x, y, s = spot
    return x, y, BOX_W * s, BOX_H * s


def assert_clear(spot, boxes, safe):
    """The box inside the safe area by its margin, and its margin clear of every control."""
    x, y, bw, bh = spot_rect(spot)
    m = 4 * spot[2]
    assert safe[0] <= x - m and x + bw + m <= safe[0] + safe[2] and safe[1] <= y - m and y + bh + m <= safe[1] + safe[3]
    for b in boxes:
        assert not meets((x - m, y - m, bw + 2 * m, bh + 2 * m), b), f"box {spot} meets control {b}"


# touch_overlay.cpp layout() on a 2400x1080 window at scale 1, no frame: s = 1080, m = 0.04 s,
# LT and RT 0.09 s by 0.04 s half sizes, m from the top and the sides.
LT_2400 = (43.2, 43.2, 194.4, 86.4)
RT_2400 = (2400 - 43.2 - 194.4, 43.2, 194.4, 86.4)


def test_on_an_android_window_the_box_goes_under_lt_never_over_the_controls(driver):
    full = (0, 0, 2400, 1080)
    boxes, spot = run_place(driver, 2400, 1080, full, touch=True)
    assert len(boxes) == 8, "the stick and the seven buttons"
    lt, rt = boxes[6], boxes[7]
    assert lt == pytest.approx(LT_2400, abs=1e-3) and rt == pytest.approx(RT_2400, abs=1e-3)
    # The top left corner (8, 8) meets LT: the box goes 8 below LT's bottom (129.6), at scale 2.
    assert spot == (8, 138, 2)
    assert not meets(spot_rect(spot), lt) and not meets(spot_rect(spot), rt)
    assert_clear(spot, boxes, full)
    # Before a finger comes down the controls are not drawn: the corner.
    assert run_place(driver, 2400, 1080, full, touch=False) == ([], (8, 8, 2))


def test_the_box_stays_inside_the_safe_area(driver):
    # A camera hole on the left short edge (Sa2Activity lays out into it): the safe area starts 100 in.
    cutout = (100, 0, 2300, 1080)
    boxes, spot = run_place(driver, 2400, 1080, cutout, touch=True)
    assert spot == (108, 138, 2)
    assert_clear(spot, boxes, cutout)
    assert run_place(driver, 2400, 1080, cutout, touch=False)[1] == (108, 8, 2)
    # Upright, a status bar's 120 rows on top; the scale from the short side (1080: 2, not 5).
    upright = (0, 120, 1080, 2280)
    assert run_place(driver, 1080, 2400, upright, touch=False)[1] == (8, 128, 2)
    boxes, spot = run_place(driver, 1080, 2400, upright, touch=True)
    assert spot == (8, 138, 2)                             # LT at 43..130 is above the safe top
    assert_clear(spot, boxes, upright)


def test_upright_with_the_controls_under_the_picture_the_corner_is_free(driver):
    # A 16:9 frame across a 1080x2400 window leaves the band under it to the controls.
    boxes, spot = run_place(driver, 1080, 2400, (0, 0, 1080, 2400), touch=True, frame=(1280, 720))
    assert min(b[1] for b in boxes) > 1200, "every control in the band below the picture"
    assert spot == (8, 8, 2)


def test_no_room_clear_of_the_controls_is_no_box(driver):
    # A safe area 300x120 from row 20: LT fills its top left and right, and under it is no room.
    assert run_place(driver, 2400, 1080, (0, 20, 300, 120), touch=True)[1] is None
    assert run_place(driver, 2400, 1080, (0, 20, 300, 120), touch=False)[1] == (4, 24, 1)


def gpu_run(driver, tmp_path, w, h, touch=False):
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: SDL's video subsystem, which the test's device needs, finds none")
    env = dict(os.environ, VK_LOADER_LAYERS_DISABLE="~implicit~", SDL_AUDIO_DRIVER="dummy")
    r = subprocess.run([str(driver), "gpu", str(w), str(h), str(tmp_path), *(["touch"] if touch else [])],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0 and "GPU_OK" in r.stdout, r.stdout + r.stderr
    return r


def assert_box_alone(off: bytes, on: bytes, w: int, h: int, rect: tuple[int, int, int, int]):
    b, g, r_, a = off[:4]                                  # BGRA of the clear colour (0, 0.25, 0.5)
    assert abs(b - 127.5) < 1 and abs(g - 63.75) < 1 and r_ == 0 and a == 255
    assert off == off[:4] * (len(off) // 4), "off draws nothing: every pixel the clear colour"
    x0, y0, bw, bh = rect
    for y in range(h):                                     # on: only the box's rectangle changes
        row = slice(y * w * 4, (y + 1) * w * 4)
        if not y0 <= y < y0 + bh:
            assert on[row] == off[row], f"row {y} changed outside the box"
        else:
            assert on[row][:x0 * 4] == off[row][:x0 * 4] and on[row][(x0 + bw) * 4:] == off[row][(x0 + bw) * 4:]
            assert all(on[row][x * 4:x * 4 + 3] != off[row][x * 4:x * 4 + 3] for x in (x0, x0 + bw - 1)), \
                f"row {y}: the box's edges are drawn"


def schedule_box(tmp_path: Path) -> list[str]:
    """The box after the 90th present: its numbers are the presents' (count, at, late so far)."""
    presents = [line.split("\t") for line in (tmp_path / "presents.tsv").read_text().splitlines()]
    samples = [(int(k), float(at), int(late)) for k, at, _, late in presents]
    assert samples[-1][2] == 2 and samples[28][2] == 0 and samples[29][2] == 1   # the 30th and 31st late
    want = meter(samples)[-1]
    assert want[0].startswith("FPS ") and want[2] == "LATE 2"
    return want


def test_drawn_off_nothing_and_on_the_box_alone(driver, tmp_path):
    w, h = 1920, 1080
    r = gpu_run(driver, tmp_path, w, h)
    off, on = (tmp_path / "off.bgra").read_bytes(), (tmp_path / "on.bgra").read_bytes()
    assert box_rect(w, h) == (8, 8, BOX_W * 2, BOX_H * 2)
    assert_box_alone(off, on, w, h, box_rect(w, h))
    assert read_box(on, w, h, channels=4) == schedule_box(tmp_path)
    assert r.stderr.splitlines() == [
        "sa2 host tweaks: frame-time overlay on: a box at the window's top left: FPS and MS (mean and longest) over "
        "the last second, LATE the frames presented more than 16.7 ms after their time (the report's pace "
        "over_frame); on the swapchain only, never in the frames file or the snaps",
        "sa2 host tweaks: frame-time overlay on: the box at 8,8, scale 2, in the 1920x1080 window (safe area 0,0 "
        "1920x1080)"], "off says nothing; on says what it shows and, once, where the box is"


def test_drawn_with_the_touch_controls_on_an_android_window_the_box_is_under_lt(driver, tmp_path):
    w, h = 2400, 1080
    r = gpu_run(driver, tmp_path, w, h, touch=True)
    off, on = (tmp_path / "off.bgra").read_bytes(), (tmp_path / "on.bgra").read_bytes()
    rect = spot_rect((8, 138, 2))
    assert not meets(rect, LT_2400) and not meets(rect, RT_2400)
    assert_box_alone(off, on, w, h, rect)                  # the controls (no device) draw nothing
    assert read_box(on, w, h, channels=4, at=(8, 138, 2)) == schedule_box(tmp_path)
    assert "the box at 8,138, scale 2, in the 2400x1080 window (safe area 0,0 2400x1080; clear of the touch " \
           "controls)" in r.stderr


def test_release_leaves_nothing_on_the_device(driver, tmp_path):
    """On a device in SDL's debug mode with the pinned Khronos validation layer: destroyed without
    release(), the device reports the box's objects left; after release(), none, and the box draws
    afresh on a second device."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: SDL's video subsystem, which the test's device needs, finds none")
    sys.path.insert(0, str(ROOT / "tools/s5"))
    import validation_tools                                   # noqa: E402
    try:
        layer = validation_tools.selected_layer(ROOT)
    except validation_tools.ValidationRefused as e:
        pytest.fail(f"the Khronos validation layer this test needs is not here (tools/s5/build_validation_tools.py): {e}")
    env = dict(os.environ, VK_LOADER_LAYERS_DISABLE="~implicit~", SDL_AUDIO_DRIVER="dummy", **layer["loader_environment"])
    w, h = 640, 360
    said = {}
    for rel in (0, 1):
        out = tmp_path / f"r{rel}"
        out.mkdir()
        r = subprocess.run([str(driver), "leak", str(w), str(h), str(out), str(rel)], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=120, env=env)
        log = r.stdout
        assert r.returncode == 0 and "LEAK_OK" in log, log
        assert "Validation Error" not in log.split("DESTROY_DEVICE")[0], "the layer saw an error before the end"
        said[rel] = log.split("DESTROY_DEVICE")[1].split("DEVICE_DESTROYED")[0]
    # The layer is loaded and counts what is left: without release() the box's texture and its
    # upload buffer (VkImage, VkBuffer) outlive the device.
    assert "VUID-vkDestroyDevice-device-05137" in said[0] and "VkBuffer" in said[0] and "VkImage" in said[0], said[0]
    assert said[1].strip() == "", said[1]
    # The second device's box: drawn in full (every glyph one of the font's), its meter carried on
    # (no schedule, so LATE "--"; FPS from the presents so far, which the devices' making spaces).
    lines = read_box((tmp_path / "r1/again.bgra").read_bytes(), w, h, channels=4)
    assert re.fullmatch(r"FPS +(--|[0-9.]+)", lines[0]) and re.fullmatch(r"MS +(--|[0-9.]+) MAX +(--|[0-9.]+)", lines[1])
    assert lines[2] == "LATE --"


# ------------------------------------------------------------------ the player, end to end

def png(path: Path) -> tuple[int, int, bytes]:
    return PT.read_png_rgb(path)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: the interactive mode opens a window")
    t = tmp_path_factory.mktemp("frametime_player")
    player = PT.build_player(t / "bin")
    stream = PT.build_synth(t)
    return {"on": play(player, stream, t / "on", {"SA2_TWEAKS": "frametime-overlay"}),
            "off": play(player, stream, t / "off", {})}


def play(player: Path, stream: Path, out: Path, env: dict) -> dict:
    """One live play session of the synthetic stream (as test_player_pads_touch's, without
    synthetic input, so the tweak may be on), fed in two parts PT.FEED_GAP_S apart, with --pace,
    --pace-log, --overlay-out and --frames-out, and `env` in the player's environment."""
    out.mkdir(parents=True)
    (out / "shots").mkdir()
    fifo = out / "input.fifo"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)  # the game's end of the input: drained, unread
    video_r, video_w = os.pipe()
    cancel_r, cancel_w = os.pipe()
    argv = [str(player), "--interactive", "--video-fd", str(video_r), "--cancel-fd", str(cancel_r),
            "--input-path", str(fifo), "--allow-notes", "--stage", "3", "--pace", "--pace-log", str(out / "pace.tsv"),
            "--overlay-out", str(out / "shots"), "--frames-out", str(out / "frames.y4m"),
            "--report", str(out / "report.json")]
    penv = {k: v for k, v in os.environ.items() if k != "SA2_TWEAKS" and not k.startswith("SA2_TOUCH_")}
    penv.update(VK_LOADER_LAYERS_DISABLE="~implicit~", SDL_AUDIO_DRIVER="dummy", **env)
    with (out / "player.out").open("wb") as so:
        proc = subprocess.Popen(argv, pass_fds=(video_r, cancel_r), stdout=so, stderr=subprocess.STDOUT, env=penv,
                                cwd=out)
    os.close(video_r)
    os.close(cancel_r)
    data = stream.read_bytes()

    def feed():
        try:
            with os.fdopen(video_w, "wb") as f:
                f.write(data[:len(data) // 2])
                f.flush()
                time.sleep(PT.FEED_GAP_S)
                f.write(data[len(data) // 2:])
        except BrokenPipeError:
            pass

    feeder = threading.Thread(target=feed)
    feeder.start()
    deadline = time.monotonic() + 60
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            os.read(reader, 65536)
        except BlockingIOError:
            pass
        time.sleep(0.002)
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    feeder.join()
    os.close(cancel_w)
    os.close(reader)
    times = (out / "frames.y4m.times.tsv").read_text().splitlines()
    (out / "frames.y4m").unlink(missing_ok=True)
    return {"exit": proc.returncode, "report": json.loads((out / "report.json").read_text()), "times": times,
            "out": out}


def test_the_play_session_draws_the_box_on_the_swapchain_alone(runs):
    r = runs["on"]
    log = (r["out"] / "player.out").read_text()
    assert r["exit"] == 0 and r["report"]["pass"] is True, log[-3000:]
    assert "sa2 host tweaks (player): frame-time overlay on (SA2_TWEAKS=frametime-overlay)" in log
    assert "sa2 host tweaks: frame-time overlay on: a box at the window's top left" in log
    assert "LATE the frames presented more than 16.7 ms after their time" in log
    w, h, _ = png(sorted(r["out"].glob("shots/frame-*-swapchain.png"))[0])
    s = scale_for(w, h)                                    # no touch controls drawn: the corner
    assert f"frame-time overlay on: the box at {4 * s},{4 * s}, scale {s}, in the {w}x{h} window (safe area 0,0 " \
           f"{w}x{h})" in log
    # pace.tsv: frame, virtual_us, ready_s, due_s, present_s, moved_ms, queue_ms; the k-th row is
    # the k-th paced present, and the box drawn during Present N counts the presents up to N - 1.
    rows = [line.split("\t") for line in (r["out"] / "pace.tsv").read_text().splitlines()[1:]]
    samples, late = [], 0
    for k, row in enumerate(rows, 1):
        assert int(row[0]) == k
        late += float(row[4]) - float(row[3]) > 1 / 60
        samples.append((k, float(row[4]), late))
    assert late == r["report"]["pace"]["late"]["over_frame"] and late >= 1, "the feed's gap makes a late frame"
    shots = sorted(r["out"].glob("shots/frame-*-swapchain.png"), key=lambda p: int(p.name.split("-")[1]))
    assert len(shots) >= 4, "the overlay shots were written"
    # The window's overlay is drawn from the first Present after the pads are set up, which is the
    # first shot's: the box's first sample is the present before it.
    first = int(shots[0].name.split("-")[1])
    for sw in shots:
        n = int(sw.name.split("-")[1])
        w, h, swap = png(sw)
        _, _, game = png(sw.with_name(f"frame-{n}-game.png"))
        x0, y0, bw, bh = box_rect(w, h)
        for y in range(h):                                 # the pictures differ in the box alone
            a, b = swap[y * w * 3:(y + 1) * w * 3], game[y * w * 3:(y + 1) * w * 3]
            if y0 <= y < y0 + bh:
                assert a[:x0 * 3] == b[:x0 * 3] and a[(x0 + bw) * 3:] == b[(x0 + bw) * 3:], f"frame {n} row {y}"
            else:
                assert a == b, f"frame {n}: row {y} differs outside the box"
        assert read_box(game, w, h) != read_box(swap, w, h)
        fed = samples[max(0, first - 2):n - 1]
        want = meter(fed)[-1] if fed else ["FPS    --", "MS    -- MAX    --", "LATE --"]
        assert read_box(swap, w, h) == want, f"frame {n}"


def test_the_frames_file_never_holds_the_box(runs):
    on, off = runs["on"], runs["off"]
    assert off["exit"] == 0 and len(on["times"]) > 1 and len(on["times"]) == len(off["times"])
    col = on["times"][0].split("\t").index("bgrx_hash")
    assert [r.split("\t")[col] for r in on["times"][1:]] == [r.split("\t")[col] for r in off["times"][1:]]
    log = (off["out"] / "player.out").read_text()
    assert "sa2 host tweaks" not in log, "with SA2_TWEAKS unset the player says nothing of the tweaks"
