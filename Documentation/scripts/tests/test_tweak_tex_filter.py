"""The host tweak tex-filter (host/player/tweaks/tex-filter.h): its report in each player and shape.

Subtitle: the tweak's change is the sdl3-gpu device's sampler description, which host/d3d9gpu's own
unit test holds (test/test_d3d9gpu.cpp test_tex_filter, run by host/d3d9gpu/build.sh: off DXVK's
default sampler; on anisotropy 16 and the bias less a quarter on a mip-mapped linear stage, the bias
alone on a point min filter, one level or no mip filter as off). This file holds what the player
says: the host's reader (host/player/tweaks.cpp) and the tweak's file compiled as the player's
main calls them, once with SA2_PLAYER_GPU (sa2_player_gpu) and once without (sa2_player, the
dxvk-native player, which the tweak leaves alone by name). Off, and out of play or in a replay, the
tweak says nothing of its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLAYER = ROOT / "host" / "player"
CXX = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"]

# player.cpp's main, as far as the tweaks go: argv[1] the reason it is no play session ("-" in play).
DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "tweaks.h"
#include "tweaks/tex-filter.h"
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const sa2tw::Reading tweaks = sa2tw::read(std::getenv("SA2_TWEAKS"),
                                              sa2tw::off(std::strcmp(argv[1], "-") ? argv[1] : nullptr), "player");
    sa2tw::g_on = tweaks.on;
    if (!tweaks.line.empty()) std::fprintf(stderr, "%s\n", tweaks.line.c_str());
    sa2tw::tex_filter::start();
    return 0;
}
"""

GPU_LINE = ("sa2 host tweaks: texture filter 16x: mip bias -0.25, and anisotropy 16x where the min filter is "
            "above point, on each texture with mip levels sampled with a mip filter (DXVK's d3d9.samplerLodBias "
            "and d3d9.samplerAnisotropy rule); textures with one level, and stages with no mip filter, are left alone")
DXVK_LINE = ("sa2 host tweaks: texture filter 16x: the dxvk-native player (sa2_player) is left alone: its DXVK "
             "configuration is a pinned file it verifies; the tweak acts in the sdl3-gpu player (sa2_player_gpu)")


@pytest.fixture(scope="module")
def players(tmp_path_factory) -> dict:
    if shutil.which("g++") is None:
        pytest.fail("g++ is not installed, and the player's tweaks are built with it (host/player/build.sh)")
    d = tmp_path_factory.mktemp("tex-filter")
    (d / "main.cpp").write_text(DRIVER)
    out = {}
    for name, defines in (("gpu", ["-DSA2_PLAYER_GPU=1"]), ("dxvk", [])):
        out[name] = d / name
        done = subprocess.run([*CXX, *defines, "-I", str(PLAYER), str(d / "main.cpp"), str(PLAYER / "tweaks.cpp"),
                               str(PLAYER / "tweaks" / "tex-filter.cpp"), "-o", str(out[name])],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, why: str = "-", **env: str) -> list[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    done = subprocess.run([str(exe), why], capture_output=True, text=True, env={**base, **env}, check=True)
    assert done.stdout == ""
    return done.stderr.splitlines()


def test_on_in_play_the_gpu_player_says_what_it_changes_and_leaves_alone(players):
    assert run(players["gpu"], SA2_TWEAKS="tex-filter") == [
        "sa2 host tweaks (player): texture filter 16x (SA2_TWEAKS=tex-filter)", GPU_LINE]
    assert run(players["gpu"], SA2_TWEAKS="no-event-bars, tex-filter") == [
        "sa2 host tweaks (player): texture filter 16x (SA2_TWEAKS=no-event-bars, tex-filter)", GPU_LINE]


def test_on_in_play_the_dxvk_player_says_it_is_left_alone(players):
    assert run(players["dxvk"], SA2_TWEAKS="tex-filter") == [
        "sa2 host tweaks (player): texture filter 16x (SA2_TWEAKS=tex-filter)", DXVK_LINE]


@pytest.mark.parametrize("player", ["gpu", "dxvk"])
def test_off_the_tweak_says_nothing(players, player):
    assert run(players[player]) == []
    assert run(players[player], SA2_TWEAKS="no-event-bars") == [
        "sa2 host tweaks (player): none on (SA2_TWEAKS=no-event-bars)"]


@pytest.mark.parametrize("player", ["gpu", "dxvk"])
def test_out_of_play_and_in_a_replay_it_is_off(players, player):
    assert run(players[player], "outside play (a stream replay)", SA2_TWEAKS="tex-filter") == [
        "sa2 host tweaks (player): SA2_TWEAKS=tex-filter ignored outside play (a stream replay)"]
    assert run(players[player], SA2_TWEAKS="tex-filter", SA2_REPLAY_TICKS="1") == [
        "sa2 host tweaks (player): SA2_TWEAKS=tex-filter ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)"]


def test_the_switch_and_the_summary_promise_no_more_than_the_sampler_rule():
    """The rule is DXVK's: every mip-mapped, mip-filtered stage, 2D or 3D, and anisotropy only on a
    device with samplerAnisotropy (some phones' GPUs lack it; d9g_gpu_sdl.cpp drops it and keeps the
    bias). So neither the Android switch nor the registry's summary may promise 16x on every device,
    limit it to the 3D world, or say the HUD and menus are untouched: a mip-mapped HUD texture drawn
    smaller changes."""
    java = (ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java").read_text()
    line = next(l for l in java.splitlines() if 'new Tweak("tex-filter",' in l)
    description = line.split('", "')[-1]
    assert "16x anisotropic filtering where the GPU supports it" in description
    assert "Most of the HUD and menus look the same" in description
    assert "unchanged" not in description and "3D world's textures" not in description
    registry = (ROOT / "runtime/sa2_tweaks.h").read_text()
    row = next(l for l in registry.splitlines() if '"tex-filter"' in l)
    assert "(where the device has samplerAnisotropy)" in row and "on mip-mapped, mip-filtered textures" in row
    assert "3D world" not in row
