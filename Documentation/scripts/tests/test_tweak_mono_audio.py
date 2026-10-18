"""The mono-audio host tweak (host/player/tweaks/mono-audio.cpp; runtime/sa2_tweaks.h
SA2_TWEAK_HOST_LIST, home "player"): in play, both channels of the player's sound are (L + R) / 2.

The tweak acts on the mixer's block in player.cpp's audio_block, before the block is written to
--audio-wav and queued to the device. Two levels hold it, both on literal version 2 streams made
with test_s5_audio.py's builders (a stream with CreateDevice, Presents and a DirectSound buffer):

  * the fold, with the system g++ as host/player/build.sh builds the player's objects: the mixer,
    the host's reader and the tweak's file, linked with a driver that does what audio_block does
    with each block (the fold, then the WAV). A tone panned hard left plays at half its level in
    both channels with the tweak on, and two different channels as the exact float (L + R) / 2;
    the fold does not take a limited full-scale mix past full scale; the tweak's own line is
    printed once. Off is the mixer to the byte: with the value unset, naming a game tweak only,
    out of play and in a replay, the driver's WAV is the WAV the plain renderer (sa2_audio_render,
    the same mixer with no tweak) writes, byte for byte.
  * the player itself (host 2's, built by host/d3d9gpu/build.sh unless SA2_PLAYER_GPU names one):
    the hard-left stream played live in play (--interactive with --video-fd and --input-path, as
    the launcher's session runs it) with --audio-wav. With SA2_TWEAKS=mono-audio its WAV has equal
    channels and its stderr the tweak's lines; with SA2_TWEAKS unset its WAV is the hard-panned
    tone. The same stream replayed from its file with SA2_TWEAKS=mono-audio (out of play) writes
    the hard-panned tone and says the value is ignored. Its window opens for a moment (the
    interactive mode refuses a hidden one), so it needs a display; SDL_AUDIO_DRIVER=dummy.

  .venv/bin/python -m pytest Documentation/scripts/tests/test_tweak_mono_audio.py
"""
from __future__ import annotations

import importlib.util
import math
import os
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLAYER = ROOT / "host" / "player"
_spec = importlib.util.spec_from_file_location("s5_audio", Path(__file__).with_name("test_s5_audio.py"))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

GXX = shutil.which(os.environ.get("CXX", "g++"))
FLAGS = ["-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", f"-I{ROOT / 'host'}"]
SOURCES = [PLAYER / "mixer.cpp", PLAYER / "stream.cpp", PLAYER / "sha256.cpp"]
LINE = "sa2 host tweaks: mono audio: "
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")

# A 1 kHz tone at 48 kHz, one cycle of 48 frames, 16-bit, amplitude 16384 (half scale): left the
# tone, right silent. As floats each sample is v / 32768, and its half v / 65536, both exact.
TONE = [round(16384 * math.sin(2 * math.pi * k / 48)) for k in range(48)]
HARD_LEFT = b"".join(struct.pack("<hh", v, 0) for v in TONE)
# Two different channels: left the tone, right a third of it in the opposite phase plus an offset.
TWO = [(v, -v // 3 + 1000) for v in TONE]
TWO_CH = b"".join(struct.pack("<hh", l, r) for l, r in TWO)
# Full scale in both channels; and full scale left, a quarter of it in the opposite phase right. The
# two played at once are past full scale on the left.
FULL = b"".join(struct.pack("<hh", 32767, 32767) for _ in range(48))
SKEW = b"".join(struct.pack("<hh", 32767, -8192 * 3) for _ in range(48))
N = 960                        # samples: 20 ms at 48 kHz, 20 laps of the buffer


def stream(data: bytes) -> bytes:
    """Create, fill and play a looping 48 kHz 16-bit stereo buffer of `data`; Presents at 10 and
    20 ms. The mix: N samples, frame n the buffer's frame n % its length."""
    return A.make(A.start() + A.ds_buffer(data=data) + A.present(A.T0 + 10_000, 2)
                  + A.present(A.T0 + 20_000, 3) + [A.END])


def loud() -> bytes:
    """FULL and SKEW at once (the second at its own address, with its own store): the mix is
    about 2 on the left, past full scale, and 0.25 on the right, before the limiter."""
    def moved(p: bytes) -> bytes:
        return (p.replace(struct.pack("<I", A.BUF), struct.pack("<I", A.BUF2))
                .replace(struct.pack("<I", A.MEM), struct.pack("<I", A.MEM + 0x1000)))
    second = [(k, moved(p)) for k, p in A.ds_buffer(data=SKEW)]
    return A.make(A.start() + A.ds_buffer(data=FULL) + second + A.present(A.T0 + 10_000, 2) + [A.END])


def frames_of(data: bytes) -> list[tuple[float, float]]:
    return [(l / 32768, r / 32768) for l, r in struct.iter_unpack("<hh", data)]


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


# What audio_block does with each block, in its order: the fold (the tweak's one call), then the
# WAV. The reader as main reads it: argv[3] is the reason this is no play session, or "-" in play.
DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "player/mixer.h"
#include "player/tweaks.h"
#include "player/tweaks/mono-audio.h"
int main(int argc, char** argv) {
    if (argc != 5) return 2;
    const sa2tw::Reading t = sa2tw::read(std::getenv("SA2_TWEAKS"), sa2tw::off(std::strcmp(argv[3], "-") ? argv[3] : nullptr),
                                         "player");
    sa2tw::g_on = t.on;
    if (!t.line.empty()) std::fprintf(stderr, "%s\n", t.line.c_str());
    Sa2StreamReader rd;
    std::string err;
    if (rd.open(argv[1], &err) != Sa2StreamReader::OPEN_OK) { std::fprintf(stderr, "%s\n", err.c_str()); return 3; }
    Sa2Mixer mx;
    mx.set_limiter(std::strcmp(argv[4], "limit") == 0);
    Sa2Wav wav;
    if (!wav.open(argv[2], &err)) return 2;
    Sa2Record r;
    int got;
    auto block = [&] {
        sa2tw::mono_audio(mx);
        wav.write(mx.block().data(), mx.block().size() / 2);
        mx.clear_block();
    };
    while ((got = rd.next(&r, &err)) > 0) {
        if (r.kind == SA2S_AUDIO) mx.on_audio(r, rd);
        else if (r.kind == SA2S_FRAME) { mx.on_frame(r.virtual_us); block(); }
    }
    block();
    if (!wav.close() || got < 0) return 3;
    std::printf("%llu %llu\n", (unsigned long long)mx.samples(), (unsigned long long)mx.problems());
    return 0;
}
"""


@pytest.fixture(scope="module")
def exes(tmp_path_factory) -> dict:
    if not GXX:
        pytest.fail("g++ is not on PATH: the player's objects are built with it (host/player/build.sh)")
    d = tmp_path_factory.mktemp("mono")
    (d / "driver.cpp").write_text(DRIVER)
    out = {"driver": d / "driver", "render": d / "sa2_audio_render", "dir": d}
    for cmd in ([GXX, *FLAGS, "-o", str(out["driver"]), str(d / "driver.cpp"), *map(str, SOURCES),
                 str(PLAYER / "tweaks.cpp"), str(PLAYER / "tweaks" / "mono-audio.cpp"), "-lzstd"],
                [GXX, *FLAGS, "-DSA2_MIXER_MAIN", "-o", str(out["render"]), *map(str, SOURCES), "-lzstd"]):
        done = subprocess.run(cmd, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return out


def drive(exes: dict, tmp_path: Path, data: bytes, env: dict, not_play: str = "-", limit: bool = False,
          name: str = "s") -> tuple[bytes, list, str]:
    """The driver on the stream `data` with `env` over the test's own (no SA2_ variable of the
    caller's): the WAV's bytes, its frames and stderr."""
    src, wav = tmp_path / f"{name}.sa2s", tmp_path / f"{name}.wav"
    src.write_bytes(data)
    e = {k: v for k, v in os.environ.items() if k not in VARS}
    e.update(env)
    done = subprocess.run([str(exes["driver"]), str(src), str(wav), not_play, "limit" if limit else "-"],
                          capture_output=True, text=True, env=e, timeout=60)
    assert done.returncode == 0, done.stderr
    samples, problems = map(int, done.stdout.split())
    assert problems == 0, done.stderr
    return wav.read_bytes(), A.read_wav(wav)[1], done.stderr


def plain(exes: dict, tmp_path: Path, data: bytes, limit: bool = False) -> bytes:
    """The same stream's WAV from the renderer, the mixer with no tweak."""
    src, wav = tmp_path / "plain.sa2s", tmp_path / "plain.wav"
    src.write_bytes(data)
    done = subprocess.run([str(exes["render"]), str(src), "--wav", str(wav), *(["--limit"] if limit else [])],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return wav.read_bytes()


def test_a_tone_panned_hard_left_plays_at_half_in_both_channels(exes, tmp_path):
    _, off, err_off = drive(exes, tmp_path, stream(HARD_LEFT), {}, name="off")
    _, on, err_on = drive(exes, tmp_path, stream(HARD_LEFT), {"SA2_TWEAKS": "mono-audio"}, name="on")
    tone = frames_of(HARD_LEFT)
    assert off == [tone[n % 48] for n in range(N)]
    assert [r for _, r in off] == [0.0] * N
    assert on == [(tone[n % 48][0] / 2, tone[n % 48][0] / 2) for n in range(N)]
    assert max(l for l, _ in on) == 0.25
    assert err_off == ""
    assert err_on.splitlines() == [
        "sa2 host tweaks (player): mono audio (SA2_TWEAKS=mono-audio)",
        LINE + "both channels play (L + R) / 2, after the limiter, in the speakers and --audio-wav; "
               "the report's audio levels are the mix before it"]


def test_two_channels_become_their_exact_float_mean(exes, tmp_path):
    _, on, _ = drive(exes, tmp_path, stream(TWO_CH), {"SA2_TWEAKS": "no-event-bars, mono-audio"})
    want = [f32(f32(l / 32768 * 0.5) + f32(r / 32768 * 0.5)) for l, r in TWO]
    assert on == [(want[n % 48], want[n % 48]) for n in range(N)]
    assert len(set(want)) > 20                                   # not a constant


def test_the_fold_keeps_a_limited_full_scale_mix_at_full_scale(exes, tmp_path):
    """The live mixer's limiter holds a mix past full scale under its ceiling (0.98), and the fold
    is after it: the mean of two channels so held, never past the louder."""
    _, off, _ = drive(exes, tmp_path, loud(), {}, limit=True, name="off")
    _, on, _ = drive(exes, tmp_path, loud(), {"SA2_TWEAKS": "mono-audio"}, limit=True, name="on")
    assert len(on) == len(off) == 480
    assert on == [(f32(f32(0.5 * l) + f32(0.5 * r)),) * 2 for l, r in off]
    assert max(l for l, _ in off) == f32(0.98) and 0.1 < max(r for _, r in off) < 0.15
    assert max(l for l, _ in on) <= f32(0.98) and min(l for l, _ in on) > 0.5


@pytest.mark.parametrize("env, not_play, said", [
    ({}, "-", ""),
    ({"SA2_TWEAKS": ""}, "-", ""),
    ({"SA2_TWEAKS": "no-event-bars"}, "-", "sa2 host tweaks (player): none on (SA2_TWEAKS=no-event-bars)\n"),
    ({"SA2_TWEAKS": "mono-audio"}, "outside play",
     "sa2 host tweaks (player): SA2_TWEAKS=mono-audio ignored outside play\n"),
    ({"SA2_TWEAKS": "mono-audio", "SA2_REPLAY_TICKS": "100"}, "-",
     "sa2 host tweaks (player): SA2_TWEAKS=mono-audio ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS "
     "is set)\n"),
])
def test_off_is_the_mixers_wav_to_the_byte(exes, tmp_path, env, not_play, said):
    wav, _, err = drive(exes, tmp_path, stream(HARD_LEFT), env, not_play)
    assert wav == plain(exes, tmp_path, stream(HARD_LEFT))
    assert err == said


def test_the_player_folds_in_audio_block_before_both_readers():
    """The call is audio_block's first statement: before the WAV write and SDL's queue, which
    read the block after it, and in the one function every block passes through."""
    src = (PLAYER / "player.cpp").read_text()
    body = src[src.index("void Player::audio_block() {\n"):]
    assert body.splitlines()[1] == "    sa2tw::mono_audio(*mixer_);"
    assert src.count("sa2tw::mono_audio(") == 1
    assert src.count("SDL_PutAudioStreamData(") == 2 and src.count("wav_.write(") == 1
    body = body[:body.index("\n}\n")]
    assert "SDL_PutAudioStreamData(" in body and "wav_.write(" in body


# ---------------------------------------------------------------- the player itself

def build_player(out: Path) -> Path:
    """SA2_PLAYER_GPU, or the second host's player built from this tree into `out`."""
    if os.environ.get("SA2_PLAYER_GPU"):
        player = Path(os.environ["SA2_PLAYER_GPU"])
        assert player.is_file(), f"SA2_PLAYER_GPU names no file: {player}"
        return player
    r = subprocess.run(["bash", str(ROOT / "host/d3d9gpu/build.sh")], env=dict(os.environ, OUT=str(out)),
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0 and "D3D9GPU_BUILD_OK" in r.stdout, (r.stdout + r.stderr)[-4000:]
    return out / "sa2_player_gpu"


def player_env(tweaks: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in VARS}
    env.update(VK_LOADER_LAYERS_DISABLE="~implicit~", SDL_AUDIO_DRIVER="dummy")
    if tweaks is not None:
        env["SA2_TWEAKS"] = tweaks
    return env


def play_live(player: Path, data: bytes, out: Path, tweaks: str | None) -> dict:
    """The stream played live in play: --interactive, the stream on --video-fd, --input-path a
    FIFO this test drains, --audio-wav. The exit, the WAV's frames and the player's stderr."""
    out.mkdir(parents=True)
    fifo = out / "input.fifo"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    video_r, video_w = os.pipe()
    cancel_r, cancel_w = os.pipe()                       # never written: no cancel
    argv = [str(player), "--interactive", "--video-fd", str(video_r), "--cancel-fd", str(cancel_r),
            "--input-path", str(fifo), "--allow-notes", "--stage", "3", "--audio-wav", str(out / "sound.wav"),
            "--report", str(out / "report.json")]
    with (out / "player.out").open("wb") as so, (out / "player.err").open("wb") as se:
        proc = subprocess.Popen(argv, pass_fds=(video_r, cancel_r), stdout=so, stderr=se,
                                env=player_env(tweaks), cwd=out)
    os.close(video_r)
    os.close(cancel_r)

    def feed():
        try:
            with os.fdopen(video_w, "wb") as f:
                f.write(data)
        except BrokenPipeError:              # the player stopped early: its report says why
            pass

    feeder = threading.Thread(target=feed)
    feeder.start()
    deadline = time.monotonic() + 60
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            os.read(reader, 65536)
        except BlockingIOError:
            pass
        time.sleep(0.005)
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    feeder.join()
    os.close(cancel_w)
    os.close(reader)
    return {"exit": proc.returncode, "frames": A.read_wav(out / "sound.wav")[1],
            "err": (out / "player.err").read_text(), "out": (out / "player.out").read_text()}


def replay(player: Path, data: bytes, out: Path, tweaks: str) -> dict:
    """The stream replayed from its file (out of play), --stats-only with --audio-wav."""
    out.mkdir(parents=True)
    (out / "s.sa2s").write_bytes(data)
    done = subprocess.run([str(player), str(out / "s.sa2s"), "--stats-only", "--audio-wav", str(out / "sound.wav"),
                           "--report", str(out / "report.json")], capture_output=True, text=True,
                          env=player_env(tweaks), cwd=out, timeout=60)
    return {"exit": done.returncode, "frames": A.read_wav(out / "sound.wav")[1], "err": done.stderr}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: the interactive mode opens a window")
    t = tmp_path_factory.mktemp("mono_player")
    player = build_player(t / "bin")
    data = stream(HARD_LEFT)
    return {"on": play_live(player, data, t / "on", "mono-audio"), "off": play_live(player, data, t / "off", None),
            "replay": replay(player, data, t / "replay", "mono-audio")}


def test_the_player_in_play_folds_its_wav_and_says_so(runs):
    on, off = runs["on"], runs["off"]
    assert on["exit"] == 0 and off["exit"] == 0, (on["err"] + on["out"])[-3000:]
    tone = frames_of(HARD_LEFT)
    # The live mixer's limiter holds a half-scale tone at unity, so the WAV is the tone itself.
    assert off["frames"] == [tone[n % 48] for n in range(len(off["frames"]))] and len(off["frames"]) == N
    assert on["frames"] == [(l / 2, l / 2) for l, _ in off["frames"]]
    assert "sa2 host tweaks (player): mono audio (SA2_TWEAKS=mono-audio)" in on["err"].splitlines()
    assert sum(line.startswith(LINE) for line in on["err"].splitlines()) == 1
    assert "sa2 host tweaks" not in off["err"]


def test_the_player_out_of_play_leaves_the_sound_alone(runs):
    r = runs["replay"]
    assert r["exit"] == 0, r["err"][-3000:]
    tone = frames_of(HARD_LEFT)
    assert r["frames"] == [tone[n % 48] for n in range(N)]
    assert ("sa2 host tweaks (player): SA2_TWEAKS=mono-audio ignored outside play (play is --interactive with "
            "--input-path)") in r["err"].splitlines()
    assert LINE not in r["err"]
