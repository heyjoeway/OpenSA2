"""The player's render-thread sampler (host/player/prof.h) and its report (tools/s8/player_prof.py).

host/player/test_prof.cpp burns about twice as much CPU in burn_call (zone "call", through its
leaf burn_leaf) as in burn_stream (zone "stream") with SA2_PLAYER_PROF set, marks 30 Presents
and stops; the report must name both functions from the binary's own symbols (matched by GNU
build-id), split them about 2:1, walk from burn_leaf to burn_call by frame pointers, and count
the 30 frames. A run killed before stop keeps its samples; a refused output and an unset
variable write nothing; the sampler costs the sampled thread under 2 % at 1000 Hz. The report's
rules sort the player's functions by name without their parameter types.

  .venv/bin/python -m pytest Documentation/scripts/tests/test_player_prof.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GXX = shutil.which(os.environ.get("CXX", "g++"))

spec = importlib.util.spec_from_file_location("player_prof", ROOT / "tools" / "s8" / "player_prof.py")
PP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PP)


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not GXX:
        pytest.skip("no g++")
    out = tmp_path_factory.mktemp("prof") / "test_prof"
    subprocess.run([GXX, "-std=c++17", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-fno-omit-frame-pointer",
                    "-Wl,--build-id", f"-I{ROOT / 'host' / 'player'}", "-o", str(out),
                    str(ROOT / "host" / "player" / "test_prof.cpp")], check=True)
    return out


def run(exe: Path, *args: str, hz: str | None = "1000") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    if hz is not None:
        env["SA2_PLAYER_PROF"] = hz
    return subprocess.run([str(exe), *args], env=env, capture_output=True, text=True, timeout=120)


def by_name(entries: list[dict], name: str) -> float:
    return sum(e["share"] for e in entries if name in e.get("function", e.get("name", "")))


def test_split_names_and_walk(exe, tmp_path):
    p = run(exe, "run", str(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "player-prof: sampling the render thread at 1000 Hz into " + str(tmp_path) in p.stdout
    r = PP.analyse(tmp_path, tmp_path / "no-store", [], 30, None)
    assert r["complete"] is True
    assert r["frames"] == 29                       # 30 Presents: 29 frame intervals
    assert r["samples"] >= 100                     # 1.8 s of CPU at the kernel tick's rate or more
    call, stream = by_name(r["top_self"], "burn_leaf"), by_name(r["top_self"], "burn_stream")
    assert 0.55 <= call <= 0.78, r["top_self"]
    assert 0.22 <= stream <= 0.45, r["top_self"]
    # burn_leaf's samples walk to burn_call by its frame pointer: burn_call is inclusive of them
    assert by_name(r["top_inclusive"], "burn_call") >= call - 0.02, r["top_inclusive"]
    zones = {z["name"]: z["share"] for z in r["zones"]}
    assert 0.55 <= zones["call"] <= 0.78 and 0.22 <= zones["stream"] <= 0.45, zones
    test_mod = next(m for m in r["modules"] if m["name"] == "test_prof")
    assert test_mod["build_id"] == PP.read_elf(exe)["build_id"]
    assert "symtab" in test_mod["symbols"]


def test_killed_run_keeps_its_samples(exe, tmp_path):
    p = run(exe, "killed", str(tmp_path))
    assert p.returncode == -9
    r = PP.analyse(tmp_path, tmp_path / "no-store", [], 10, None)
    assert r["complete"] is False
    assert r["samples"] >= 100
    assert by_name(r["top_self"], "burn_leaf") > 0.5


def test_refused_and_unset_write_nothing(exe, tmp_path):
    p = run(exe, "refused", str(tmp_path))
    assert p.returncode == 0
    assert p.stdout.startswith("player-prof: refused: " + str(tmp_path) + "/player-prof.samples is not allowed here")
    p = run(exe, "unset", str(tmp_path), hz=None)
    assert p.returncode == 0 and p.stdout == "[]\n"
    assert list(tmp_path.iterdir()) == []
    p = run(exe, "run", str(tmp_path), hz="0x10")
    assert "SA2_PLAYER_PROF=0x10 is not 1 to 20000 Hz; off" in p.stdout


def test_overhead_under_two_percent(exe, tmp_path):
    def cpu(hz):
        return min(float(run(exe, "cost", str(tmp_path), hz=hz).stdout.split()[1]) for _ in range(3))
    off, on = cpu(None), cpu("1000")
    assert on <= off * 1.02, (off, on)
    assert len(list(tmp_path.glob("player-prof-*.samples"))) == 3      # the sampled runs wrote here


def test_rules_read_names_not_parameter_types():
    assert PP.category_of("Sa2Mixer::apply_ds(Sa2Record const&, Sa2AudioRow const*)") == "mixer"
    assert PP.category_of("Sa2StreamReader::fill(void*, unsigned long, bool*)") == "stream read/parse"
    assert PP.category_of("(anonymous namespace)::blocks_shani(unsigned int*, unsigned char const*, unsigned long)") \
        == "stream hash (file replays only)"
    assert PP.category_of("d9g::Device::execute_draw(char const*, unsigned int)") == "d3d9 state translation (d9g)"
    assert PP.category_of("std::vector<unsigned char, std::allocator<unsigned char> >::_M_fill_assign(unsigned long)") is None
    chain = [("libc", "libc.so.6", "memcpy"), ("player", "libmain.so", "std::vector<int>::_M_realloc_insert(int)"),
             ("player", "libmain.so", "Sa2Mixer::on_audio(Sa2Record const&)")]
    assert PP.owner_category(chain, {}) == "mixer"
    assert PP.owner_category([("libc", "libc.so", "ioctl"), ("driver", "vulkan.adreno.so", "x")], {}) == "GPU driver"
    assert PP.owner_category([("libc", "libc.so", "ioctl")], {}) == "C library, no caller found"
