#!/usr/bin/env python3
"""Turn the DXVK player's frame dump into an MP4 at the recorded frame times.

Subtitle: the player (`host/player`) writes one frame per guest Present, read
back through GetRenderTargetData and converted to YUV4MPEG2 4:2:0
(`--frames-out X.y4m`), and a sidecar `X.y4m.times.tsv` holding each frame's
virtual time. This tool resamples those frames onto a constant output rate by
virtual time (a frame is shown from its own virtual time until the next one's),
has the host's VLC 3.0.23 encode the result (x264 and an MP4 muxer, nothing
downloaded), and checks the MP4 it wrote by reading its sample table back.

Nothing here decodes a pixel in Python (the host has no numpy): resampling is
copying whole y4m frames by byte offset, and the colour conversion is the
player's.

Usage:
  .venv/bin/python tools/s5/frames_to_video.py probe \\
      --dir private/generated/s5/video/probe
  .venv/bin/python tools/s5/frames_to_video.py encode \\
      --frames private/generated/s5/play/boot/frames.y4m \\
      --report private/generated/s5/play/boot/stage3.json \\
      [--dxvk-log private/generated/s5/play/boot/stage3.dxvk] \\
      [--audio-wav private/generated/s5/play/boot/sound.wav] \\
      --out private/generated/s5/video/boot.mp4
  .venv/bin/python tools/s5/frames_to_video.py info private/generated/s5/video/boot.mp4
  .venv/bin/python tools/s5/frames_to_video.py compare A.y4m.times.tsv B.y4m.times.tsv

`probe` writes 60 synthetic 1280x720 frames, and a 9-frame stream shorter than
x264's default lookahead, and encodes each from a file (the path `encode`
uses). Each MP4 must hold an avc1 track of 1280x720 with every frame, tagged
BT.709 limited range with centred chroma, and VLC must decode it back to frames
whose mean luma follows the synthetic ramp. A slow fade then checks the decode
check itself: it must pass the true frame mapping and fail the same mapping
slipped by one frame. It also records, as findings and not requirements, what a
real pipe and raw BGRA through VLC's rawvid demuxer give.

`encode` needs --report, the report of the player run that wrote --frames: it
refuses frames whose count differs from their times file's, and frames that are
not the ones that run wrote. The report must say the run finished and passed,
and its frames_out record (path, byte count, frame count, and a digest of every
frame's bgrx_hash) must match the files. The report's own "pass" is not taken
for the verdict: `encode` runs tools/s5/play_report.py's check on it, with the
frames and the run's DXVK log (--dxvk-log, default the directory the report
names), and refuses the frames on any failure, as play_stream.sh refuses to make
a video of a stage whose check failed. A times file whose rows are not the
player's (a missing column, a field that is not a number) is refused like any
other input (exit 2). It stages the resampled stream in
`<out>.resampled.y4m` (deleted afterwards unless --keep-staged), decodes the MP4
back into `<out>.decoded.i420` (deleted after the check), and writes
`<out>.json` beside the MP4 with the inputs, the command, the frame mapping and
the MP4's own numbers. `compare` holds two plays of one stream to each other
frame by frame, by the fingerprint the player writes for every frame
(`bgrx_hash`) and by the frames' virtual times; it exits 1 on any difference.

With --audio-wav W.wav (the player's --audio-wav output and its W.wav.json beside
it), the video carries the sound. The WAV is bound to the frames' run by the stream
both played: its .json's stream_sha256 must be the report's, and its mix must have
had no problem. It is aligned to the video in a staged copy, `<out>.audio.wav`: its
sample 0 is the first frame's virtual time (the .json's origin_us names the WAV's
own first sample's), and it lasts exactly the video's frames at its rate, cut or
padded with silence (align_wav). VLC encodes it alone to AAC (mp4a, 192 kb/s,
stereo, 48 kHz) in `<out>.audio.m4a` (vlc_audio_cmd; this host has no ffmpeg, and
VLC is the encoder already), and merge_audio adds that track to the video's MP4 box
by box, the video's samples untouched; the staged files are deleted afterwards
unless --keep-staged. The receipt records the alignment, the merge and the MP4's
sound track (mp4_info "audio"), and the encode passes only when the track is there,
AAC at 48 kHz in two channels, within AUDIO_SLACK_S of the video's duration, and
every sample of both tracks reads back through the merged file's tables as it was
(track_samples).

Every output is game video or holds game frames, so `encode` and `probe` refuse
(exit 2, before anything is written) any output path inside a git working tree
and outside its private/ (AGENTS.md rule 9), by the player's own rule
(play_report.refuse_output).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from play_report import check as play_check  # noqa: E402  the second layer over a run's report
from play_report import refuse_output  # noqa: E402  the player's output-path rule

CVLC = "/usr/bin/cvlc"
Y4M_MAGIC = b"YUV4MPEG2 "
FRAME_TAG = b"FRAME\n"

# What VLC 3.0.23 does here, as measured on this host (probe.json keeps it):
#
#  * With x264's defaults (B-frames, a 40-frame lookahead, frame threads) the
#    frames still inside x264 at the input's EOF are lost: a 60-frame input gave
#    an MP4 with a moov and 0 samples with the transcode in the decoder thread,
#    59 samples with `--sout-transcode-threads`, and a 9-frame input gave 0
#    samples either way.
#  * With no B-frames, no lookahead and one x264 thread (X264_LOW_LATENCY), x264
#    hands every frame back at once, and 1, 9 and 60 frames each came out whole
#    but for the last frame of the input, with and without transcode threads.
#    So one padding frame, a copy of the last, is appended; the MP4 must then
#    hold exactly the frames meant, and `encode` and `probe` both check it.
#  * The x264 `options` value is a VLC option chain, so several x264 options go
#    in braces, comma-separated. An MP4 encoded with colorprim, transfer,
#    colormatrix and range appended to `options=threads=1:sync-lookahead=0`
#    carried no video signal type in its SPS, and the braced spelling carried
#    BT.709 limited range (`info` parses the SPS): everything after the first
#    colon was dropped, most likely as part of the value of `threads`.
#  * `chromaloc=1` goes the same way and reaches the SPS: its VUI then carries
#    chroma_loc_info with type 1 for both fields (centred chroma, what the
#    player's C420jpeg frames are: host/player/texcodec.h, bgra_to_i420 averages
#    each 2x2 block), where without it a decoder assumes type 0, half a chroma
#    sample to the left.
#
# VLC's own y4m reader (rawvid) refuses every header spelling tried with
# "invalid or no framerate specified"; libavformat's yuv4mpegpipe demuxer is
# what reads the stream, from a file or from stdin, so no demuxer is forced.
# The staged y4m also says XCOLORRANGE=LIMITED, which that demuxer reads.
X264_LOW_LATENCY = ("x264{preset=medium,crf=18,bframes=0,lookahead=0,options={threads=1,sync-lookahead=0,"
                    "colorprim=bt709,transfer=bt709,colormatrix=bt709,range=tv,chromaloc=1}}")
TRANSCODE_THREADS = "--sout-transcode-threads=2"
PAD_FRAMES = 1
# The decode check's tolerance, in luma levels of an 80x80 block's mean (see
# verify_mp4): the boot's largest difference at crf 18 was 0.71.
DECODE_THRESHOLD = 2.0
# Sound (--audio-wav): what VLC encodes it to, and how far the MP4's sound track may
# end from its video, in seconds: two AAC frames of 1024 samples at 48 kHz, the
# encoder's priming and its last partial frame.
AUDIO_TRANSCODE = "acodec=mp4a,ab=192,channels=2,samplerate=48000"
AUDIO_RATE = 48000
AUDIO_SLACK_S = 2 * 1024 / AUDIO_RATE


# --------------------------------------------------------------------- y4m

class Y4M:
    """A YUV4MPEG2 file with 4:2:0 or 4:4:4 planes and no per-frame parameters."""

    def __init__(self, path: Path):
        self.path = path
        with path.open("rb") as f:
            line = f.readline(4096)
        if not line.startswith(Y4M_MAGIC) or not line.endswith(b"\n"):
            raise SystemExit(f"{path}: not a YUV4MPEG2 file")
        self.header = line
        self.width = self.height = 0
        self.fps = (60, 1)
        self.chroma = "420jpeg"
        try:
            for token in line[len(Y4M_MAGIC):].split():
                tag, value = chr(token[0]), token[1:].decode()
                if tag == "W":
                    self.width = int(value)
                elif tag == "H":
                    self.height = int(value)
                elif tag == "F":
                    num, den = value.split(":")
                    self.fps = (int(num), int(den))
                elif tag == "C":
                    self.chroma = value
        except ValueError as exc:
            raise SystemExit(f"{path}: the YUV4MPEG2 header does not parse: {exc}") from None
        if self.chroma.startswith("420"):
            plane = self.width * self.height + 2 * ((self.width + 1) // 2) * ((self.height + 1) // 2)
        elif self.chroma.startswith("444"):
            plane = 3 * self.width * self.height
        else:
            raise SystemExit(f"{path}: chroma {self.chroma} is not handled")
        self.frame_bytes = len(FRAME_TAG) + plane
        size = path.stat().st_size
        self.count = (size - len(self.header)) // self.frame_bytes
        self.trailing = (size - len(self.header)) % self.frame_bytes

    def offset(self, i: int) -> int:
        return len(self.header) + i * self.frame_bytes


def y4m_header(width: int, height: int, fps: tuple[int, int], chroma: str = "420jpeg") -> bytes:
    """The player's frames are BT.709 limited range (host/player/texcodec.h,
    bgra_to_i420); the header says the range, and x264 tags the rest."""
    return (f"YUV4MPEG2 W{width} H{height} F{fps[0]}:{fps[1]} Ip A1:1 C{chroma} XCOLORRANGE=LIMITED\n").encode()


# ------------------------------------------------------------------- times

TIMES_COLUMNS = ("frame", "virtual_us", "bgrx_hash")     # of the player's, what this tool reads


def read_times(path: Path) -> list[dict]:
    """The player's sidecar: a header row, then one row per frame written, each
    field an integer (bgrx_hash is 0x...). SystemExit, naming the line, for a
    file that is not that: no rows, a column missing, a row of another length,
    or a field that is not a number."""
    rows = []
    with path.open() as f:
        names = f.readline().rstrip("\n").split("\t")
        missing = [c for c in TIMES_COLUMNS if c not in names]
        if missing:
            raise SystemExit(f"{path}: no {', '.join(missing)} column in {names}")
        for line_no, line in enumerate(f, start=2):
            if not line.strip():
                continue
            values = line.rstrip("\n").split("\t")
            try:
                if len(values) != len(names):
                    raise ValueError(f"{len(values)} fields for {len(names)} columns")
                rows.append({k: int(v, 0) for k, v in zip(names, values)})
            except ValueError as exc:
                raise SystemExit(f"{path}: line {line_no} is not a row of the player's: {exc}") from None
    if not rows:
        raise SystemExit(f"{path}: no rows")
    return rows


def resample(times_us: list[int], fps: tuple[int, int]) -> list[int]:
    """Input frame index for every output frame at a constant rate.

    Output frame k covers [t0 + k/fps, t0 + (k+1)/fps) and carries the last input
    frame whose virtual time is at or before the middle of that interval. Two
    frames presented at one virtual time show only the later, as a display would;
    a gap of n periods shows a frame n times.

    Sampling at the middle postpones drift; it does not prevent it. The runtime's
    frame period is 16,666 us and 60/1's is 16,666.67 us, so the output falls
    0.67 us a frame behind the input: after about 12,500 output frames (P/2 over
    the difference) one input frame is skipped, and one more every 25,000 after
    (Metal Harbor presents 15,709 frames). The receipt counts every input frame
    not shown (`input_frames_not_shown`); the video is for watching, and the
    frame-by-frame reference is the player's y4m and its times file, one frame
    per Present, which no resampling touches.
    """
    if not times_us:
        return []
    period = 1_000_000 * fps[1] / fps[0]
    t0 = times_us[0]
    end = times_us[-1] - t0 + period          # the last frame is shown for one period
    out: list[int] = []
    i = 0
    k = 0
    while k * period < end - period / 2:
        t = k * period + period / 2
        while i + 1 < len(times_us) and times_us[i + 1] - t0 <= t:
            i += 1
        out.append(i)
        k += 1
    return out


# --------------------------------------------------------------------- mp4

class _Bits:
    """An H.264 RBSP bit reader: u(n), ue(v), se(v) (ITU-T H.264 7.2, 9.1)."""

    def __init__(self, rbsp: bytes):
        self.data = rbsp
        self.pos = 0

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("bad Exp-Golomb code")
        return (1 << zeros) - 1 + self.u(zeros)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def avc_sps_info(sps: bytes) -> dict:
    """Profile, size, and the VUI's video signal type and chroma location from
    one SPS NAL unit.

    The fields a decoder reads to choose range, matrix and chroma siting:
    without video_signal_type_present_flag the first two are left to each
    player's guess, and without chroma_loc_info_present_flag the siting is
    type 0 (ITU-T H.264 E.2.1)."""
    rbsp, zeros = bytearray(), 0
    for b in sps[1:]:                                   # drop the NAL header, then 0x000003 escapes
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        rbsp.append(b)
        zeros = zeros + 1 if b == 0 else 0
    r = _Bits(bytes(rbsp))
    info: dict = {"profile_idc": r.u(8)}
    r.u(8)
    info["level_idc"] = r.u(8)
    r.ue()
    chroma = 1
    if info["profile_idc"] in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = r.ue()
        if chroma == 3:
            r.u(1)
        r.ue(); r.ue(); r.u(1)
        if r.u(1):                                      # seq_scaling_matrix_present_flag
            for i in range(8 if chroma != 3 else 12):
                if r.u(1):
                    last = nxt = 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt:
                            nxt = (last + r.se() + 256) % 256
                        last = nxt or last
    r.ue()                                              # log2_max_frame_num_minus4
    poc = r.ue()
    if poc == 0:
        r.ue()
    elif poc == 1:
        r.u(1); r.se(); r.se()
        for _ in range(r.ue()):
            r.se()
    r.ue(); r.u(1)
    width_mbs, height_units = r.ue() + 1, r.ue() + 1
    frame_mbs_only = r.u(1)
    if not frame_mbs_only:
        r.u(1)
    r.u(1)
    crop = [0, 0, 0, 0]
    if r.u(1):
        crop = [r.ue(), r.ue(), r.ue(), r.ue()]
    info["coded_size"] = [width_mbs * 16, height_units * 16 * (2 - frame_mbs_only)]
    info["crop"] = crop
    info["vui"] = bool(r.u(1))
    info["video_signal_type_present"] = False
    if info["vui"]:
        if r.u(1):                                      # aspect_ratio_info_present_flag
            if r.u(8) == 255:
                r.u(16); r.u(16)
        if r.u(1):                                      # overscan_info_present_flag
            r.u(1)
        if r.u(1):                                      # video_signal_type_present_flag
            info["video_signal_type_present"] = True
            info["video_format"] = r.u(3)
            info["video_full_range_flag"] = r.u(1)
            if r.u(1):                                  # colour_description_present_flag
                info["colour_primaries"] = r.u(8)
                info["transfer_characteristics"] = r.u(8)
                info["matrix_coefficients"] = r.u(8)
        info["chroma_loc_info_present"] = bool(r.u(1))  # chroma_loc_info_present_flag
        if info["chroma_loc_info_present"]:
            info["chroma_sample_loc_type_top_field"] = r.ue()
            info["chroma_sample_loc_type_bottom_field"] = r.ue()
    return info


def bt709_limited(sps: dict) -> bool:
    """The stream says what the player's y4m is: BT.709 primaries, transfer and
    matrix (1, 1, 1 in H.264 Table E-3 to E-5), limited range."""
    return (sps.get("video_signal_type_present", False) and sps.get("video_full_range_flag") == 0
            and sps.get("colour_primaries") == 1 and sps.get("transfer_characteristics") == 1
            and sps.get("matrix_coefficients") == 1)


def chroma_centred(sps: dict) -> bool:
    """The stream says the player's chroma siting: type 1, centred between the
    four luma samples of each 2x2 block, in both fields (H.264 Figure E-1)."""
    return (sps.get("chroma_loc_info_present", False) and sps.get("chroma_sample_loc_type_top_field") == 1
            and sps.get("chroma_sample_loc_type_bottom_field") == 1)


def colour_ok(sps: dict) -> bool:
    return bt709_limited(sps) and chroma_centred(sps)


def _boxes(data: bytes, start: int, end: int):
    off = start
    while off + 8 <= end:
        size, kind = struct.unpack(">I4s", data[off:off + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            header = 16
        elif size == 0:
            size = end - off
        if size < header or off + size > end:
            return
        yield kind.decode("latin-1"), off + header, off + size
        off += size


def mp4_info(path: Path) -> dict:
    """Codec, size, sample count and duration of the first video track, and of the first
    sound track when there is one ("audio": codec, channels, sample rate)."""
    data = path.read_bytes()
    info: dict = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    moov = next((b for b in _boxes(data, 0, len(data)) if b[0] == "moov"), None)
    if not moov:
        info["error"] = "no moov box"
        return info
    for kind, s, e in _boxes(data, moov[1], moov[2]):
        if kind != "trak":
            continue
        track: dict = {}
        stack = [(s, e)]
        while stack:
            bs, be = stack.pop()
            for k2, s2, e2 in _boxes(data, bs, be):
                if k2 in ("mdia", "minf", "stbl", "edts", "dinf"):
                    stack.append((s2, e2))
                elif k2 == "hdlr":
                    track["handler"] = data[s2 + 8:s2 + 12].decode("latin-1")
                elif k2 == "mdhd":
                    if data[s2] == 1:
                        timescale, duration = struct.unpack(">IQ", data[s2 + 20:s2 + 32])
                    else:
                        timescale, duration = struct.unpack(">II", data[s2 + 12:s2 + 20])
                    track["timescale"] = timescale
                    track["duration"] = duration
                elif k2 == "stsd":
                    entry = data[s2 + 8:e2]
                    if len(entry) >= 36 and track.get("handler") == "soun":
                        track["codec"] = entry[4:8].decode("latin-1")
                        track["channels"] = struct.unpack(">H", entry[24:26])[0]
                        track["sample_rate"] = struct.unpack(">I", entry[32:36])[0] >> 16
                    elif len(entry) >= 8 + 78:
                        track["codec"] = entry[4:8].decode("latin-1")
                        track["width"], track["height"] = struct.unpack(">HH", entry[8 + 24:8 + 28])
                    at = entry.find(b"avcC")
                    if at >= 4 and len(entry) >= at + 12 and entry[at + 9] & 0x1F:
                        slen = struct.unpack(">H", entry[at + 10:at + 12])[0]
                        try:
                            track["sps"] = avc_sps_info(entry[at + 12:at + 12 + slen])
                        except (IndexError, ValueError) as exc:
                            track["sps"] = {"error": f"SPS did not parse: {exc}"}
                elif k2 == "stsz":
                    track["samples"] = struct.unpack(">I", data[s2 + 8:s2 + 12])[0]
                elif k2 == "stss":
                    n_sync = struct.unpack(">I", data[s2 + 4:s2 + 8])[0]
                    track["sync_samples"] = n_sync
                    track["sync_first"] = list(struct.unpack(f">{min(n_sync, 4)}I",
                                                             data[s2 + 8:s2 + 8 + 4 * min(n_sync, 4)]))
                elif k2 == "stts":
                    n = struct.unpack(">I", data[s2 + 4:s2 + 8])[0]
                    deltas: dict = {}
                    for j in range(n):
                        count, delta = struct.unpack(">II", data[s2 + 8 + 8 * j:s2 + 16 + 8 * j])
                        deltas[delta] = deltas.get(delta, 0) + count
                    track["sample_deltas"] = deltas
        if track.get("timescale"):
            track["duration_s"] = track["duration"] / track["timescale"]
        if track.get("handler") == "vide" and "video" not in info:
            info["video"] = track
        elif track.get("handler") == "soun" and "audio" not in info:
            info["audio"] = track
    if "video" not in info:
        info["error"] = "no video track"
    return info


def _find_path(data: bytes, start: int, end: int, path: list[str]) -> list[tuple[int, int, int]] | None:
    """[(box start, payload start, box end)] for each box along `path`, or None."""
    chain = []
    for name in path:
        for kind, s, e in _boxes(data, start, end):
            if kind == name and (name != "trak" or _is_video_trak(data, s, e)):
                if struct.unpack(">I", data[s - 8:s - 4])[0] != e - (s - 8):
                    return None                # a 64-bit box size here: not what VLC writes
                chain.append((s - 8, s, e))
                start, end = s, e
                break
        else:
            return None
    return chain


def _is_video_trak(data: bytes, s: int, e: int) -> bool:
    mdia = next((b for b in _boxes(data, s, e) if b[0] == "mdia"), None)
    if not mdia:
        return False
    hdlr = next((b for b in _boxes(data, mdia[1], mdia[2]) if b[0] == "hdlr"), None)
    return bool(hdlr) and data[hdlr[1] + 8:hdlr[1] + 12] == b"vide"


def fix_first_duration(path: Path) -> dict:
    """Give the first sample the frame period VLC's MP4 muxer left at 0.

    Every MP4 VLC 3.0.23 wrote here has `stts` [(1, 0), (n-1, 1500)]: the first
    sample lasts 0 ticks, so samples 1 and 2 share time 0, a player shows one of
    them (VLC's raw decode of the boot dropped sample 1 and so ran one frame early
    for the whole video), and the track is a frame shorter than its samples. The
    stream fed to VLC is constant-rate by construction (one frame per output
    period), so the table becomes [(1, d), (n-1, d)], the same size, and the
    track, media, movie and edit durations each gain the missing period."""
    data = bytearray(path.read_bytes())
    moov = next(((k, s, e) for k, s, e in _boxes(data, 0, len(data)) if k == "moov"), None)
    if not moov:
        return {"changed": False, "why": "no moov"}
    chain = _find_path(data, moov[1], moov[2], ["trak", "mdia", "minf", "stbl"])
    if not chain:
        return {"changed": False, "why": "no video stbl"}
    trak, mdia, _, stbl = chain
    stts = next(((s, e) for k, s, e in _boxes(data, stbl[1], stbl[2]) if k == "stts"), None)
    n = struct.unpack(">I", data[stts[0] + 4:stts[0] + 8])[0]
    entries = [struct.unpack(">II", data[stts[0] + 8 + 8 * i:stts[0] + 16 + 8 * i]) for i in range(n)]
    if n != 2 or entries[0] != (1, 0) or entries[1][1] == 0:
        return {"changed": False, "why": f"stts is {entries[:4]}, not [(1, 0), (n-1, d)]"}
    d = entries[1][1]
    data[stts[0] + 8:stts[0] + 16] = struct.pack(">II", 1, d)

    def bump(box_payload: int, v0_at: int, v1_at: int, delta: int) -> None:
        v1 = data[box_payload] == 1
        at, fmt = (box_payload + v1_at, ">Q") if v1 else (box_payload + v0_at, ">I")
        width = struct.calcsize(fmt)
        data[at:at + width] = struct.pack(fmt, struct.unpack(fmt, data[at:at + width])[0] + delta)

    mdhd = next(s for k, s, e in _boxes(data, mdia[1], mdia[2]) if k == "mdhd")
    mvhd = next(s for k, s, e in _boxes(data, moov[1], moov[2]) if k == "mvhd")
    tkhd = next(s for k, s, e in _boxes(data, trak[1], trak[2]) if k == "tkhd")
    media_ts = struct.unpack(">I", data[mdhd + (20 if data[mdhd] == 1 else 12):][:4])[0]
    movie_ts = struct.unpack(">I", data[mvhd + (20 if data[mvhd] == 1 else 12):][:4])[0]
    movie_delta = d * movie_ts // media_ts
    bump(mdhd, 16, 24, d)
    bump(mvhd, 16, 24, movie_delta)
    bump(tkhd, 20, 28, movie_delta)
    edts = next(((s, e) for k, s, e in _boxes(data, trak[1], trak[2]) if k == "edts"), None)
    if edts:
        elst = next((s for k, s, e in _boxes(data, edts[0], edts[1]) if k == "elst"), None)
        if elst is not None:
            bump(elst, 8, 8, movie_delta)
    path.write_bytes(bytes(data))
    return {"changed": True, "stts_before": entries, "period": d, "media_timescale": media_ts,
            "movie_timescale": movie_ts}


def mark_first_sync(path: Path) -> dict:
    """Add sample 1 to the video track's sync-sample table when it is an IDR frame.

    VLC 3.0.23's MP4 muxer leaves x264's first frame (always an IDR) out of `stss`
    while it lists the later keyframes: the boot's MP4 listed samples 251, 391, ...
    and players that start at the first sync sample, VLC among them, skipped the
    first 250 frames (1,348 samples decoded to 1,098 frames). The first sample's
    NAL units are read to confirm the IDR before the table is touched; the moov's
    boxes grow by 4 bytes, and when the moov precedes the mdat every chunk offset
    moves by the same 4."""
    data = bytearray(path.read_bytes())
    moov = next(((k, s, e) for k, s, e in _boxes(data, 0, len(data)) if k == "moov"), None)
    mdat = next(((k, s, e) for k, s, e in _boxes(data, 0, len(data)) if k == "mdat"), None)
    if not moov or not mdat:
        return {"changed": False, "why": "no moov or mdat"}
    chain = _find_path(data, moov[1], moov[2], ["trak", "mdia", "minf", "stbl"])
    if not chain:
        return {"changed": False, "why": "no video stbl"}
    stbl = chain[-1]
    boxes = {k: (s, e) for k, s, e in _boxes(data, stbl[1], stbl[2])}
    if "stss" not in boxes:
        return {"changed": False, "why": "no stss: every sample is already a sync sample"}
    ss, se = boxes["stss"]
    n = struct.unpack(">I", data[ss + 4:ss + 8])[0]
    entries = list(struct.unpack(f">{n}I", data[ss + 8:ss + 8 + 4 * n]))
    if entries and entries[0] == 1:
        return {"changed": False, "why": "sample 1 is already a sync sample"}
    co_name = "stco" if "stco" in boxes else "co64"
    cs, ce = boxes[co_name]
    first_chunk = (struct.unpack(">I", data[cs + 8:cs + 12])[0] if co_name == "stco"
                   else struct.unpack(">Q", data[cs + 8:cs + 16])[0])
    zs = boxes["stsz"][0]
    size0 = struct.unpack(">I", data[zs + 4:zs + 8])[0] or struct.unpack(">I", data[zs + 12:zs + 16])[0]
    nal_types, p = [], first_chunk
    while p + 4 <= first_chunk + size0:
        length = struct.unpack(">I", data[p:p + 4])[0]
        nal_types.append(data[p + 4] & 0x1F)
        p += 4 + length
    if 5 not in nal_types:
        return {"changed": False, "why": f"sample 1 holds NAL types {nal_types}, no IDR (5)"}
    new_stss = (struct.pack(">I4s", 8 + 8 + 4 * (n + 1), b"stss") + bytes(data[ss:ss + 4]) +
                struct.pack(f">I{n + 1}I", n + 1, 1, *entries))
    moov_before_mdat = moov[2] <= mdat[1]
    new = data[:ss - 8] + new_stss + data[se:]
    grow = len(new_stss) - (se - (ss - 8))
    for hs, _, _ in [(moov[1] - 8, 0, 0)] + chain:        # every ancestor of stss grows
        size = struct.unpack(">I", new[hs:hs + 4])[0]
        new[hs:hs + 4] = struct.pack(">I", size + grow)
    if moov_before_mdat:                                  # the mdat moved by `grow`
        cs2 = cs + (grow if cs > ss else 0)
        count = struct.unpack(">I", new[cs2 + 4:cs2 + 8])[0]
        width = 4 if co_name == "stco" else 8
        fmt = ">I" if width == 4 else ">Q"
        for i in range(count):
            at = cs2 + 8 + width * i
            new[at:at + width] = struct.pack(fmt, struct.unpack(fmt, new[at:at + width])[0] + grow)
    path.write_bytes(bytes(new))
    return {"changed": True, "first_sample_nal_types": nal_types, "stss_before": entries[:8],
            "moov_before_mdat": moov_before_mdat, "bytes_added": grow}


# --------------------------------------------------------------------- vlc

def child_peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0


def vlc_version() -> str:
    try:
        out = subprocess.run([CVLC, "--version"], capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines()[0] if out.stdout else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def vlc_encode_cmd(src: str, dst: Path, extra: list[str]) -> list[str]:
    sout = (f"#transcode{{vcodec=h264,venc={X264_LOW_LATENCY},acodec=none}}"
            f":std{{access=file,mux=mp4,dst={dst}}}")
    return [CVLC, "-I", "dummy", "--no-audio", TRANSCODE_THREADS, *extra, src,
            "--sout", sout, "--play-and-exit"]


def vlc_audio_cmd(wav: Path, dst: Path) -> list[str]:
    """VLC's command to encode a WAV alone to an AAC track in an MP4. The sound is not
    encoded beside the video in one VLC run: with the WAV as the y4m's input slave the
    host's VLC (3.0.24 on 2026-10-02) kept 4 of 10 video frames, and with the y4m as the
    WAV's slave it wrote an empty sound track (measured on this host that day), so the
    two are encoded apart and merge_audio puts the sound track into the video's MP4."""
    sout = f"#transcode{{{AUDIO_TRANSCODE}}}:std{{access=file,mux=mp4,dst={dst}}}"
    return [CVLC, "-I", "dummy", "--no-video", str(wav), "--sout", sout, "--play-and-exit"]


# --------------------------------------------------------------------- sound

def read_wav(path: Path) -> dict:
    """The player's --audio-wav file: its format fields, the offset and length of its
    samples. SystemExit for a file that is not a WAVE_FORMAT_IEEE_FLOAT stereo 32-bit
    48 kHz WAV, the one host/player/mixer.cpp writes."""
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SystemExit(f"{path}: not a RIFF WAVE file")
    fmt, at, size, p = None, None, None, 12
    while p + 8 <= len(data):
        cid, n = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", data, p + 8)
        elif cid == b"data":
            at, size = p + 8, min(n, len(data) - p - 8)
            break
        p += 8 + n + (n & 1)
    if fmt is None or at is None:
        raise SystemExit(f"{path}: no fmt or data chunk")
    tag, channels, rate, _bps, block, bits = fmt
    if (tag, channels, rate, block, bits) != (3, 2, AUDIO_RATE, 8, 32):
        raise SystemExit(f"{path}: format tag {tag}, {channels} channels, {rate} Hz, {bits} bits; the "
                         f"player writes float32 stereo at {AUDIO_RATE} Hz")
    return {"data_offset": at, "frames": size // 8, "bytes": len(data)}


def audio_offset(first_frame_us: int, origin_us: int, rate: int = AUDIO_RATE) -> int:
    """The WAV sample at the video's start: the first frame's virtual time, counted in
    samples from the WAV's own first sample's (negative: the video starts before the
    sound does), to the nearest sample."""
    num = (first_frame_us - origin_us) * rate
    return (num + 500_000) // 1_000_000 if num >= 0 else -((-num + 500_000) // 1_000_000)


def align_wav(src: Path, side: dict, first_frame_us: int, output_frames: int, fps: tuple[int, int],
              dst: Path) -> dict:
    """`src` written to `dst` with sample 0 at `first_frame_us` and exactly the video's
    length, output_frames * fps[1] / fps[0] seconds of samples: cut where the WAV has
    more, silence where it has less."""
    w = read_wav(src)
    offset = audio_offset(first_frame_us, side["origin_us"])
    want = (output_frames * fps[1] * AUDIO_RATE + fps[0] // 2) // fps[0]
    data = src.read_bytes()
    with dst.open("wb") as out:
        out.write(b"RIFF" + struct.pack("<I", 50 + 8 * want) + b"WAVEfmt "
                  + struct.pack("<IHHIIHHH", 18, 3, 2, AUDIO_RATE, AUDIO_RATE * 8, 8, 32, 0)
                  + b"fact" + struct.pack("<II", 4, want) + b"data" + struct.pack("<I", 8 * want))
        lead = max(0, -offset)
        out.write(b"\0" * 8 * min(lead, want))
        first = max(0, offset)
        take = max(0, min(w["frames"] - first, want - min(lead, want)))
        if take:
            out.write(data[w["data_offset"] + 8 * first:w["data_offset"] + 8 * (first + take)])
        out.write(b"\0" * 8 * (want - min(lead, want) - take))
    return {"offset_samples": offset, "samples": want, "from_wav": take, "silence_before": min(lead, want),
            "silence_after": want - min(lead, want) - take, "wav_frames": w["frames"]}


def _child(data: bytes, start: int, end: int, kind: str):
    """The first child box of kind `kind` in [start, end): (box start, payload start, end)."""
    for k, s, e in _boxes(data, start, end):
        if k == kind:
            return s - 8, s, e
    return None


def _trak_of(data: bytes, handler: str):
    """(box start, payload start, end) of the first trak whose hdlr is `handler`, and the
    moov's."""
    moov = _child(data, 0, len(data), "moov")
    if moov is None:
        raise ValueError("no moov box")
    for k, s, e in _boxes(data, moov[1], moov[2]):
        if k != "trak":
            continue
        mdia = _child(data, s, e, "mdia")
        hdlr = _child(data, mdia[1], mdia[2], "hdlr") if mdia else None
        if hdlr and data[hdlr[1] + 8:hdlr[1] + 12] == handler.encode():
            return (s - 8, s, e), moov
    raise ValueError(f"no {handler} track")


def _stbl(data: bytes, trak) -> tuple[int, int, int]:
    mdia = _child(data, trak[1], trak[2], "mdia")
    minf = _child(data, mdia[1], mdia[2], "minf")
    return _child(data, minf[1], minf[2], "stbl")


def track_samples(data: bytes, handler: str) -> list[bytes]:
    """Every sample of the first `handler` track, read through its sample tables (stsz,
    stsc, stco or co64): what a player would read."""
    trak, _ = _trak_of(data, handler)
    stbl = _stbl(data, trak)
    stsz, stsc = _child(data, stbl[1], stbl[2], "stsz"), _child(data, stbl[1], stbl[2], "stsc")
    co = _child(data, stbl[1], stbl[2], "stco") or _child(data, stbl[1], stbl[2], "co64")
    fixed, count = struct.unpack(">II", data[stsz[1] + 4:stsz[1] + 12])
    sizes = [fixed] * count if fixed else list(struct.unpack(f">{count}I", data[stsz[1] + 12:stsz[1] + 12 + 4 * count]))
    wide = data[co[0] + 4:co[0] + 8] == b"co64"
    n_chunks = struct.unpack(">I", data[co[1] + 4:co[1] + 8])[0]
    offsets = list(struct.unpack(f">{n_chunks}{'Q' if wide else 'I'}", data[co[1] + 8:co[1] + 8 + (8 if wide else 4) * n_chunks]))
    n_runs = struct.unpack(">I", data[stsc[1] + 4:stsc[1] + 8])[0]
    runs = [struct.unpack(">III", data[stsc[1] + 8 + 12 * j:stsc[1] + 20 + 12 * j]) for j in range(n_runs)]
    out, k = [], 0
    for c in range(n_chunks):
        per = next((r[1] for r in reversed(runs) if r[0] <= c + 1), 0)
        at = offsets[c]
        for _ in range(per):
            if k >= len(sizes):
                break
            out.append(data[at:at + sizes[k]])
            at += sizes[k]
            k += 1
    return out


def _shift_chunks(buf: bytearray, data: bytes, stbl, base: int, by: int, rebase: int | None = None) -> None:
    """Add `by` to every chunk offset of the stbl at `stbl` (positions in `data`, written
    into `buf` at the same positions less `base`); with `rebase`, subtract it first."""
    co = _child(data, stbl[1], stbl[2], "stco") or _child(data, stbl[1], stbl[2], "co64")
    wide = data[co[0] + 4:co[0] + 8] == b"co64"
    n = struct.unpack(">I", data[co[1] + 4:co[1] + 8])[0]
    fmt, w = (">Q", 8) if wide else (">I", 4)
    for j in range(n):
        at = co[1] + 8 + w * j
        v = struct.unpack(fmt, data[at:at + w])[0] - (rebase or 0) + by
        if not wide and not 0 <= v < 1 << 32:
            raise ValueError("a chunk offset past 4 GiB in a 32-bit table")
        struct.pack_into(fmt, buf, at - base, v)


def _movie_timescale(data: bytes, moov) -> tuple[int, int, int]:
    """The mvhd's (payload start, timescale, version)."""
    mvhd = _child(data, moov[1], moov[2], "mvhd")
    version = data[mvhd[1]]
    ts = struct.unpack(">I", data[mvhd[1] + (20 if version else 12):mvhd[1] + (24 if version else 16)])[0]
    return mvhd[1], ts, version


def merge_audio(video: Path, audio: Path, dst: Path) -> dict:
    """The video MP4 with the first sound track of `audio` added: the video's boxes and
    samples untouched, its chunk offsets moved by the bytes the new track adds to the
    moov, the sound's samples in a second mdat after the video's, its track renumbered to
    the movie's next track id, its durations (tkhd, elst) rescaled to the video's movie
    timescale, and the movie's duration the longer track's. Both files are VLC's layout
    (ftyp, moov, wide, mdat); ValueError for anything else."""
    v, a = video.read_bytes(), audio.read_bytes()
    vtop = list(_boxes(v, 0, len(v)))
    kinds = [k for k, _s, _e in vtop]
    if "moov" not in kinds or "mdat" not in kinds or kinds.index("moov") > kinds.index("mdat"):
        raise ValueError(f"{video}: boxes {kinds}, not a moov before the mdat")
    vtrak, vmoov = _trak_of(v, "vide")
    atrak, amoov = _trak_of(a, "soun")
    _, v_ts, v_ver = _movie_timescale(v, vmoov)
    _, a_ts, _ = _movie_timescale(a, amoov)
    trak = bytearray(a[atrak[0]:atrak[2]])
    # the sound's track id, its tkhd duration and its edit list in the video's timescale
    tkhd = _child(a, atrak[1], atrak[2], "tkhd")
    mv_payload = _child(v, vmoov[1], vmoov[2], "mvhd")[1]
    next_id_at = _child(v, vmoov[1], vmoov[2], "mvhd")[2] - 4
    next_id = struct.unpack(">I", v[next_id_at:next_id_at + 4])[0]
    t_ver = a[tkhd[1]]
    struct.pack_into(">I", trak, tkhd[1] + (20 if t_ver else 12) - atrak[0], next_id)
    dur_at = tkhd[1] + (28 if t_ver else 20)
    a_dur = struct.unpack(">Q" if t_ver else ">I", a[dur_at:dur_at + (8 if t_ver else 4)])[0]
    a_dur_v = (a_dur * v_ts + a_ts // 2) // a_ts
    struct.pack_into(">Q" if t_ver else ">I", trak, dur_at - atrak[0], a_dur_v)
    edts = _child(a, atrak[1], atrak[2], "edts")
    elst = _child(a, edts[1], edts[2], "elst") if edts else None
    if elst:
        e_ver, n = a[elst[1]], struct.unpack(">I", a[elst[1] + 4:elst[1] + 8])[0]
        w = 8 if e_ver else 4
        for j in range(n):
            at = elst[1] + 8 + j * (2 * w + 4)
            seg = struct.unpack(">Q" if e_ver else ">I", a[at:at + w])[0]
            struct.pack_into(">Q" if e_ver else ">I", trak, at - atrak[0], (seg * v_ts + a_ts // 2) // a_ts)
    # the new moov: the video's, the sound's trak after the video's
    grow = len(trak)
    moov = bytearray(v[vmoov[0]:vmoov[2]])
    _shift_chunks(moov, v, _stbl(v, vtrak), vmoov[0], grow)
    mv_dur_at = mv_payload + (24 if v_ver else 16)
    v_mdur = struct.unpack(">Q" if v_ver else ">I", v[mv_dur_at:mv_dur_at + (8 if v_ver else 4)])[0]
    struct.pack_into(">Q" if v_ver else ">I", moov, mv_dur_at - vmoov[0], max(v_mdur, a_dur_v))
    struct.pack_into(">I", moov, next_id_at - vmoov[0], next_id + 1)
    at = vtrak[2] - vmoov[0]
    moov[at:at] = trak
    struct.pack_into(">I", moov, 0, len(moov))
    # the sound's samples: its whole mdat payload, in a second mdat after the video's;
    # its chunk offsets are patched where its trak now sits in the new moov
    amdat = _child(a, 0, len(a), "mdat")
    new_mdat = struct.pack(">I", 8 + amdat[2] - amdat[1]) + b"mdat" + a[amdat[1]:amdat[2]]
    payload_at = len(v) + grow + 8
    trak_in_moov = vtrak[2] - vmoov[0]
    _shift_chunks(moov, a, _stbl(a, atrak), atrak[0] - trak_in_moov, payload_at, rebase=amdat[1])
    out = v[:vmoov[0]] + bytes(moov) + v[vmoov[2]:] + new_mdat
    dst.write_bytes(out)
    return {"moov_grew": grow, "sound_mdat_bytes": len(new_mdat), "track_id": next_id,
            "sound_duration": [a_dur, a_ts], "movie_timescale": v_ts}


def audio_binding(wav: Path, report_path: Path) -> dict:
    """The WAV, its .json and the report of the run whose frames are encoded: the .json
    must say the WAV was written whole by a mix with no problem, name as many samples as
    the WAV holds, and name the stream the report's run played (stream_sha256)."""
    side_path = Path(str(wav) + ".json")
    refusals = []
    try:
        side = json.loads(side_path.read_text())
        w = read_wav(wav)
    except (OSError, ValueError, SystemExit) as exc:
        return {"refusals": [f"--audio-wav: {exc}"], "sidecar": str(side_path)}
    try:
        rep = json.loads(report_path.read_text())
    except (OSError, ValueError) as exc:
        return {"refusals": [f"--audio-wav: the report does not read: {exc}"], "sidecar": str(side_path)}
    if side.get("ok") is not True:
        refusals.append(f"{side_path} says the WAV was not written whole")
    if side.get("audio_problems") != 0:
        refusals.append(f"{side_path} says the mixer had {side.get('audio_problems')} problem(s) making it")
    if side.get("frames") != w["frames"]:
        refusals.append(f"{side_path} names {side.get('frames')} samples; {wav} holds {w['frames']}")
    if not isinstance(side.get("origin_us"), int):
        refusals.append(f"{side_path} names no origin_us")
    if not side.get("stream_sha256") or side.get("stream_sha256") != rep.get("stream_sha256"):
        refusals.append(f"the WAV is the sound of stream {side.get('stream_sha256')}; the frames' run played "
                        f"{rep.get('stream_sha256')}")
    return {"refusals": refusals, "sidecar": str(side_path), "side": side, "wav_frames": w["frames"],
            "wav_sha256": sha256_file(wav)}


def run_vlc(cmd: list[str], stdin_path: Path | None = None, feed=None,
            timeout: int = 3600) -> dict:
    """Run cvlc with stdin from a file, from `feed` (an iterator of bytes), or none."""
    t0 = time.monotonic()
    handle = stdin_path.open("rb") if stdin_path else None
    stdin = subprocess.PIPE if feed is not None else (handle or subprocess.DEVNULL)
    proc = subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    # stderr is drained on its own thread, so VLC can never block on a full
    # stderr pipe while this thread blocks writing its stdin.
    chunks: list[bytes] = []
    drain = threading.Thread(target=lambda: chunks.append(proc.stderr.read()), daemon=True)
    drain.start()
    if feed is not None:
        try:
            for chunk in feed:
                proc.stdin.write(chunk)
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    drain.join(timeout=60)
    if handle:
        handle.close()
    err = b"".join(chunks)
    lines = [line for line in err.decode("utf-8", "replace").splitlines() if line.strip()]
    return {"cmd": cmd, "returncode": proc.returncode,
            "wall_s": round(time.monotonic() - t0, 3), "stderr_tail": lines[-6:]}


def decode_to_y4m(mp4: Path, dst: Path) -> dict:
    """Decode an MP4 back to y4m through VLC's yuv video output (plays in real time)."""
    if dst.exists():
        dst.unlink()
    cmd = [CVLC, "-I", "dummy", "--no-audio", "--vout", "yuv", "--yuv-file", str(dst),
           "--yuv-yuv4mpeg2", "--yuv-chroma", "I420", "--no-drop-late-frames",
           "--no-skip-frames", "--no-video-title-show", "--no-osd", "--no-spu",
           "--play-and-exit", str(mp4)]
    result = run_vlc(cmd, timeout=1800)
    result["frames"] = Y4M(dst).count if dst.exists() and dst.stat().st_size else 0
    return result


def mean_luma(y4m: Y4M, i: int, sample_rows: int = 45) -> float:
    """Mean of Y over every 16th row of frame i (enough for a ramp check)."""
    total = n = 0
    with y4m.path.open("rb") as f:
        base = y4m.offset(i) + len(FRAME_TAG)
        step = max(1, y4m.height // sample_rows)
        for row in range(0, y4m.height, step):
            f.seek(base + row * y4m.width)
            data = f.read(y4m.width)
            total += sum(data)
            n += len(data)
    return total / n if n else 0.0


# -------------------------------------------------------------------- probe

def synthetic_frames(width: int, height: int, count: int):
    """Frame k: a vertical luma ramp lifted by 3*k and a white box moving right.

    Mean luma rises by about 3 per frame, so a decode can be checked frame by frame."""
    for k in range(count):
        lift = 3 * k
        plane = bytearray(b"".join(bytes([min(255, 16 + (y * 128) // height + lift)]) * width
                                   for y in range(height)))
        bx = (k * (width - 128)) // max(1, count - 1)
        for y in range(height // 2 - 64, height // 2 + 64):
            plane[y * width + bx:y * width + bx + 128] = b"\xeb" * 128
        yield bytes(plane)


def bgra_from_luma(plane: bytes) -> bytes:
    out = bytearray(len(plane) * 4)
    out[0::4] = plane
    out[1::4] = plane
    out[2::4] = plane
    out[3::4] = b"\xff" * len(plane)
    return bytes(out)


def check_mp4(d: Path, name: str, n: int, w: int, h: int, encode: dict, mp4: Path) -> dict:
    entry: dict = {"encode": encode}
    if not (mp4.exists() and mp4.stat().st_size):
        entry["pass"] = False
        return entry
    entry["mp4_fixes"] = {"first_duration": fix_first_duration(mp4), "first_sync": mark_first_sync(mp4)}
    entry["mp4"] = mp4_info(mp4)
    dec = d / f"probe-{name}.decoded.y4m"
    entry["decode"] = decode_to_y4m(mp4, dec)
    if entry["decode"]["frames"]:
        y = Y4M(dec)
        lumas = [round(mean_luma(y, i), 2) for i in range(y.count)]
        steps = [round(b - a, 2) for a, b in zip(lumas, lumas[1:])]
        entry["decode"]["mean_luma_first_last"] = [lumas[0], lumas[-1]]
        entry["decode"]["luma_steps_min_max"] = [min(steps), max(steps)] if steps else []
        # VLC's yuv output can show a picture twice (a 9-sample MP4 decoded to 10
        # frames, one step of 0); every other step must be the ramp's, and there
        # must be exactly n-1 of them.
        rising = [s for s in steps if s != 0.0]
        entry["decode"]["distinct_frames"] = len(rising) + 1
        entry["decode"]["ramp_ok"] = len(rising) == n - 1 and all(1.0 <= s <= 5.0 for s in rising)
    if dec.exists():
        dec.unlink()   # a check input of 80 MB; the numbers above are what is kept
    video = entry["mp4"].get("video", {})
    entry["bt709_limited"] = bt709_limited(video.get("sps", {}))
    entry["chroma_centred"] = chroma_centred(video.get("sps", {}))
    entry["pass"] = (video.get("codec") == "avc1" and video.get("samples") == n
                     and video.get("width") == w and video.get("height") == h
                     and entry["decode"].get("frames", 0) in (n, n + 1)
                     and entry["decode"].get("ramp_ok", False) and entry["bt709_limited"]
                     and entry["chroma_centred"])
    return entry


def decode_check_control(d: Path, w: int, h: int, n: int = 30) -> dict:
    """The decode check held to a case it must catch and one it must pass.

    A fade of one luma level a frame, n frames, is encoded the way `encode`
    encodes; verify_mp4 must pass it against the true mapping and fail it against
    the same mapping slipped by one frame. One level is under DECODE_THRESHOLD,
    so only the neighbour test can see that slip."""
    chroma = b"\x80" * (2 * ((w + 1) // 2) * ((h + 1) // 2))
    src, mp4 = d / "control-fade.y4m", d / "control-fade.mp4"
    with src.open("wb") as fy:
        fy.write(y4m_header(w, h, (60, 1)))
        for k in list(range(n)) + [n - 1] * PAD_FRAMES:
            fy.write(FRAME_TAG + bytes([100 + k]) * (w * h) + chroma)
    mp4.unlink(missing_ok=True)
    encode = run_vlc(vlc_encode_cmd(str(src), mp4, []), timeout=600)
    out: dict = {"frames": n, "encode_returncode": encode["returncode"]}
    if not (mp4.exists() and mp4.stat().st_size):
        out["pass"] = False
        return out
    fix_first_duration(mp4)
    mark_first_sync(mp4)
    y = Y4M(src)
    true_map = list(range(n))
    slipped = [min(k + 1, n - 1) for k in range(n)]
    out["true_mapping"] = verify_mp4(mp4, y, true_map)
    out["slipped_mapping"] = verify_mp4(mp4, y, slipped)
    out["pass"] = out["true_mapping"]["pass"] is True and out["slipped_mapping"]["pass"] is False
    src.unlink()
    mp4.unlink()
    return out


def cmd_probe(args) -> int:
    d = Path(args.dir)
    if why := refuse_output(d / "probe.json"):
        print(f"refused: {why}", file=sys.stderr)
        return 2
    d.mkdir(parents=True, exist_ok=True)
    w, h, n = 1280, 720, args.frames
    chroma = b"\x80" * (2 * ((w + 1) // 2) * ((h + 1) // 2))
    y4m_path, raw_path, short_path = d / "probe.y4m", d / "probe.bgra", d / "probe-short.y4m"
    short = 9                   # a stream shorter than x264's default lookahead
    planes = list(synthetic_frames(w, h, n))
    with y4m_path.open("wb") as fy, raw_path.open("wb") as fr, short_path.open("wb") as fs:
        fy.write(y4m_header(w, h, (60, 1)))
        fs.write(y4m_header(w, h, (60, 1)))
        for plane in planes + planes[-1:] * PAD_FRAMES:        # see PAD_FRAMES
            fy.write(FRAME_TAG + plane + chroma)
            fr.write(bgra_from_luma(plane))
        for plane in planes[:short] + planes[short - 1:short] * PAD_FRAMES:
            fs.write(FRAME_TAG + plane + chroma)
    report: dict = {"frames_meant": n, "short_frames_meant": short, "pad_frames": PAD_FRAMES,
                    "size": [w, h], "x264": X264_LOW_LATENCY,
                    "inputs": {"y4m": str(y4m_path), "y4m_bytes": y4m_path.stat().st_size,
                               "bgra": str(raw_path), "bgra_bytes": raw_path.stat().st_size},
                    "vlc": vlc_version(), "runs": {}}
    def pipe_of(path: Path):
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                yield chunk

    # name: (source, extra args, stdin, frames meant, required). `encode` stages
    # its stream in a file (see cmd_encode), so the file runs are the required
    # ones; a real pipe and raw BGRA are recorded as findings.
    runs = {
        "y4m_file": (str(y4m_path), [], None, n, True),
        "y4m_file_short": (str(short_path), [], None, short, True),
        "y4m_pipe": ("-", [], ("feed", y4m_path), n, False),
        "bgra_rawvid_stdin": ("-", ["--demux", "rawvid", "--rawvid-fps", "60",
                                    "--rawvid-width", str(w), "--rawvid-height", str(h),
                                    "--rawvid-chroma", "RV32"], ("file", raw_path), n, False),
    }
    ok = True
    for name, (src, extra, stdin, meant, required) in runs.items():
        mp4 = d / f"probe-{name}.mp4"
        if mp4.exists():
            mp4.unlink()
        cmd = vlc_encode_cmd(src, mp4, extra)
        if stdin and stdin[0] == "feed":
            encode = run_vlc(cmd, feed=pipe_of(stdin[1]), timeout=600)
        else:
            encode = run_vlc(cmd, stdin_path=stdin[1] if stdin else None, timeout=600)
        entry = check_mp4(d, name, meant, w, h, encode, mp4)
        entry["required"] = required
        if required:
            ok = ok and entry["pass"]
        report["runs"][name] = entry
    raw_path.unlink()          # 225 MB, only an input to the finding above
    report["decode_check_control"] = decode_check_control(d, w, h)
    ok = ok and report["decode_check_control"]["pass"]
    report["child_peak_rss_mb"] = round(child_peak_rss_mb(), 1)
    report["pass"] = ok
    (d / "probe.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, entry in report["runs"].items():
        video = entry.get("mp4", {}).get("video", {})
        dec = entry.get("decode", {})
        print(f"{name}: pass={entry['pass']} required={entry['required']} "
              f"codec={video.get('codec')} {video.get('width')}x{video.get('height')} "
              f"samples={video.get('samples')} duration_s={video.get('duration_s')} "
              f"decoded={dec.get('frames')} luma={dec.get('mean_luma_first_last')} "
              f"steps={dec.get('luma_steps_min_max')} bt709_limited={entry.get('bt709_limited')} "
              f"chroma_centred={entry.get('chroma_centred')}")
    ctl = report["decode_check_control"]
    print(f"decode check control: pass={ctl['pass']} true mapping "
          f"{ctl.get('true_mapping', {}).get('pass')} (largest {ctl.get('true_mapping', {}).get('largest_difference')}), "
          f"slipped by one {ctl.get('slipped_mapping', {}).get('pass')} "
          f"(neighbour misses {ctl.get('slipped_mapping', {}).get('frames_nearer_a_neighbour')})")
    print(f"probe pass={ok} child_peak_rss_mb={report['child_peak_rss_mb']}")
    return 0 if ok else 1


# ------------------------------------------------------------------- encode

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def bgrx_digest(rows: list[dict]) -> int:
    """FNV-1a 64 over every frame's bgrx_hash, 8 bytes each, little-endian, in
    frame order: the digest the player's report records for its frames file
    (host/player/player.cpp, digest_step)."""
    d = 0xcbf29ce484222325
    for r in rows:
        v = r["bgrx_hash"]
        for i in range(8):
            d ^= (v >> (8 * i)) & 0xFF
            d = (d * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return d


def report_binding(report_path: Path, frames: Path, times_path: Path, y: "Y4M", rows: list[dict],
                   dxvk_log: str | None = None) -> dict:
    """Whether these frames are the ones the report's run wrote, and passed.

    The player truncates its frames file and times file when a run starts and
    records, at the end, how many frames it wrote, how many bytes, and a digest
    of every frame's fingerprint. A frames file left by an earlier run, cut short,
    or paired with another run's report fails one of these.

    "Passed" is not the report's word for it. The second layer
    (tools/s5/play_report.py check) runs on the report as play_stream.sh runs it
    on a stage: the verdict recomputed from the counts, the allow-list, these
    frames, and DXVK's options from the run's own log (`dxvk_log`, or else the
    directory the report names), which the player's verdict leaves out. Any
    failure of it is a refusal."""
    refusals: list[str] = []
    try:
        rep = json.loads(report_path.read_text())
    except (OSError, ValueError) as exc:
        return {"report": str(report_path), "refusals": [f"{report_path}: {exc}"]}
    fo = rep.get("frames_out") or {}
    if rep.get("complete") is not True:
        refusals.append(f"{report_path}: the run did not finish (phase {rep.get('phase')})")
    if rep.get("pass") is not True:
        refusals.append(f"{report_path}: the run did not pass: {rep.get('failures')}")
    if not fo:
        refusals.append(f"{report_path}: the run wrote no frames file")
    else:
        try:
            same = Path(fo.get("path", "")).resolve() == frames.resolve() and \
                Path(fo.get("times", "")).resolve() == times_path.resolve()
        except OSError:
            same = False
        if not same:
            refusals.append(f"{report_path} wrote {fo.get('path')}, not {frames}")
        size = frames.stat().st_size
        if fo.get("y4m_bytes") != size:
            refusals.append(f"{frames} is {size} bytes; its run wrote {fo.get('y4m_bytes')}")
        if not (fo.get("frames") == fo.get("times_rows") == y.count == len(rows) == rep.get("presents")):
            refusals.append(f"frames: report {fo.get('frames')}, times rows {fo.get('times_rows')}, presents "
                            f"{rep.get('presents')}; the file holds {y.count} and its times {len(rows)}")
        digest = f"0x{bgrx_digest(rows):016x}"
        if fo.get("bgrx_digest") != digest:
            refusals.append(f"{times_path}: frame digest {digest}; its run recorded {fo.get('bgrx_digest')}")
    named = (rep.get("dxvk") or {}).get("log")
    log_dir = dxvk_log or (str(Path(named).parent) if named else None)
    if log_dir is None:
        refusals.append(f"{report_path}: the report names no DXVK log to hold the run's options to (pass --dxvk-log)")
    check = play_check(argparse.Namespace(report=str(report_path), exit=0, frames_out=str(frames), dxvk_log=log_dir))
    refusals += [f"the check: {f}" for f in check["failures"]]
    return {"report": str(report_path), "report_pass": rep.get("pass"), "stream": rep.get("stream"),
            "stream_sha256": rep.get("stream_sha256"), "stage": rep.get("stage"),
            "check": {"tool": "tools/s5/play_report.py check", "exit": 0, "dxvk_log": log_dir, "pass": check["pass_"],
                      "failures": check["failures"]},
            "refusals": refusals}


def encode_outputs(out: Path) -> list[Path]:
    """Every file `encode` writes: the MP4, its receipt, the staged stream, the
    decode check's raw frames and the staged sound."""
    return [out, Path(str(out) + ".json"), out.with_name(out.name + ".resampled.y4m"),
            out.with_name(out.name + ".decoded.i420"), out.with_name(out.name + ".audio.wav"),
            out.with_name(out.name + ".audio.m4a"), out.with_name(out.name + ".merged.mp4")]


def cmd_encode(args) -> int:
    frames = Path(args.frames)
    times_path = Path(args.times) if args.times else Path(str(frames) + ".times.tsv")
    out = Path(args.out)
    refusals = [why for p in encode_outputs(out) if (why := refuse_output(p))]
    if refusals:                      # before anything is read or written
        for r in refusals:
            print(f"refused: {r}", file=sys.stderr)
        return 2
    fps = tuple(int(x) for x in args.fps.split("/")) if "/" in args.fps else (int(args.fps), 1)
    try:                              # an empty or foreign file is refused like any other
        rows = read_times(times_path)
        y = Y4M(frames)
    except (SystemExit, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if y.trailing:
        refusals.append(f"{frames} ends with {y.trailing} bytes that are not a whole frame")
    if y.count != len(rows):
        refusals.append(f"{frames} holds {y.count} frames and {times_path} times {len(rows)}")
    if y.count == 0:
        refusals.append(f"{frames} holds no frame")
    binding = report_binding(Path(args.report), frames, times_path, y, rows, args.dxvk_log)
    refusals += binding["refusals"]
    audio_wav = getattr(args, "audio_wav", None)
    sound = audio_binding(Path(audio_wav), Path(args.report)) if audio_wav else None
    if sound:
        refusals += sound["refusals"]
    if refusals:
        for r in refusals:
            print(f"refused: {r}", file=sys.stderr)
        return 2
    count = y.count
    out.parent.mkdir(parents=True, exist_ok=True)
    mapping = resample([r["virtual_us"] for r in rows], fps)
    shown = len(set(mapping))

    # The resampled stream goes to a file and VLC reads the file. A pipe is not
    # enough: on the 1,335-frame boot, VLC's rawvid demuxer refused the stream and
    # consumed its first bytes, and every demuxer tried after it failed with
    # "cannot seek (to offset 0)" / "cannot peek" (the 8-frame synthetic stream
    # happened to get through the same pipe). A file is seekable every time.
    staged = out.with_name(out.name + ".resampled.y4m")
    with frames.open("rb") as f, staged.open("wb") as s:
        s.write(y4m_header(y.width, y.height, fps, y.chroma))
        for i in mapping + mapping[-1:] * PAD_FRAMES:
            f.seek(y.offset(i))
            s.write(f.read(y.frame_bytes))
    staged_audio = out.with_name(out.name + ".audio.wav") if sound else None
    if sound:
        sound["aligned"] = align_wav(Path(audio_wav), sound["side"], rows[0]["virtual_us"],
                                     len(mapping), fps, staged_audio)
    if out.exists():
        out.unlink()
    result = run_vlc(vlc_encode_cmd(str(staged), out, []), timeout=args.timeout)
    if not args.keep_staged:
        staged.unlink()
    made = out.exists() and out.stat().st_size > 0
    fixes = {"first_duration": fix_first_duration(out) if made else {"changed": False},
             "first_sync": mark_first_sync(out) if made else {"changed": False}}
    if sound and made:
        # the sound encoded alone, then its track merged into the video's MP4
        m4a = out.with_name(out.name + ".audio.m4a")
        merged = out.with_name(out.name + ".merged.mp4")
        for f in (m4a, merged):
            if f.exists():
                f.unlink()
        sound["encode"] = run_vlc(vlc_audio_cmd(staged_audio, m4a), timeout=args.timeout)
        try:
            sound["merge"] = merge_audio(out, m4a, merged)
            sound["samples_intact"] = (track_samples(merged.read_bytes(), "soun")
                                       == track_samples(m4a.read_bytes(), "soun"))
            sound["video_intact"] = (track_samples(merged.read_bytes(), "vide")
                                     == track_samples(out.read_bytes(), "vide"))
            merged.replace(out)
        except (OSError, ValueError, TypeError, IndexError, struct.error) as exc:
            sound["merge"] = {"error": f"{type(exc).__name__}: {exc}"}
            sound["samples_intact"] = sound["video_intact"] = False
        if not args.keep_staged:
            for f in (staged_audio, m4a):
                if f.exists():
                    f.unlink()
    info = mp4_info(out) if made else {"error": "no output"}
    video = info.get("video", {})
    verify = verify_mp4(out, y, mapping) if made and not args.no_verify else {"pass": None}
    sps = video.get("sps", {})
    colour = colour_ok(sps)
    audio_ok = True
    if sound:
        track = info.get("audio") or {}
        gap = (abs(track["duration_s"] - len(mapping) * fps[1] / fps[0])
               if track.get("duration_s") is not None else None)
        audio_ok = (track.get("codec") == "mp4a" and track.get("channels") == 2
                    and track.get("sample_rate") == AUDIO_RATE and gap is not None and gap <= AUDIO_SLACK_S
                    and sound.get("samples_intact") is True and sound.get("video_intact") is True)
        sound = {"wav": audio_wav, "wav_sha256": sound["wav_sha256"], "sidecar": sound["sidecar"],
                 "origin_us": sound["side"].get("origin_us"), "first_frame_us": rows[0]["virtual_us"],
                 "aligned": sound["aligned"], "encoder": "vlc " + AUDIO_TRANSCODE,
                 "encode": sound.get("encode"), "merge": sound.get("merge"),
                 "samples_intact": sound.get("samples_intact"), "video_intact": sound.get("video_intact"),
                 "track": track, "duration_gap_s": gap, "slack_s": AUDIO_SLACK_S, "pass": audio_ok}
    receipt = {
        "tool": "tools/s5/frames_to_video.py encode",
        "inputs": {"frames": str(frames), "frames_bytes": frames.stat().st_size,
                   "frames_sha256": sha256_file(frames) if args.hash else None,
                   "times": str(times_path), "times_sha256": sha256_file(times_path),
                   "bgrx_digest": f"0x{bgrx_digest(rows):016x}"},
        "report_binding": binding,
        "size": [y.width, y.height], "fps": f"{fps[0]}/{fps[1]}",
        "frames_in_file": y.count, "frames_timed": len(rows), "frames_used": count,
        "virtual_us_first_last": [rows[0]["virtual_us"], rows[count - 1]["virtual_us"]],
        "output_frames": len(mapping), "pad_frames": PAD_FRAMES,
        "input_frames_shown": shown, "input_frames_not_shown": count - shown,
        "colour": {"y4m": "BT.709 limited range (XCOLORRANGE=LIMITED), chroma centred (C420jpeg)",
                   "sps_bt709_limited": bt709_limited(sps), "sps_chroma_centred": chroma_centred(sps)},
        "vlc": vlc_version(), "x264": X264_LOW_LATENCY, "encode": result, "mp4_fixes": fixes, "mp4": info,
        "decode_check": verify,
        "audio": sound,
        "child_peak_rss_mb": round(child_peak_rss_mb(), 1),
        "pass": (video.get("codec") == "avc1" and video.get("samples") == len(mapping)
                 and y.count == len(rows) == count
                 # every sample one output period long, the first included
                 and set(video.get("sample_deltas", {})) == {video.get("timescale", 0) * fps[1] // fps[0]}
                 and ("sync_first" not in video or video["sync_first"][:1] == [1])
                 and colour
                 and not binding["refusals"]
                 and verify["pass"] is not False
                 and audio_ok),
    }
    Path(str(out) + ".json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"frames {count} written and timed; output {len(mapping)} at {fps[0]}/{fps[1]}; "
          f"shown {shown}, not shown {count - shown}; bound to {binding['report']}")
    print(f"mp4 {out}: codec={video.get('codec')} {video.get('width')}x{video.get('height')} "
          f"samples={video.get('samples')} sample_deltas={video.get('sample_deltas')} "
          f"sync_first={video.get('sync_first')} duration_s={video.get('duration_s')} bytes={info.get('bytes')} "
          f"bt709_limited={bt709_limited(sps)} chroma_centred={chroma_centred(sps)}")
    if sound:
        tr = sound["track"]
        print(f"sound {audio_wav}: from sample {sound['aligned']['offset_samples']}, "
              f"{sound['aligned']['samples']} samples ({sound['aligned']['silence_before']} silent before, "
              f"{sound['aligned']['silence_after']} after); track codec={tr.get('codec')} "
              f"channels={tr.get('channels')} rate={tr.get('sample_rate')} duration_s={tr.get('duration_s')} "
              f"(gap {sound['duration_gap_s']}, slack {AUDIO_SLACK_S:.4f}): {'pass' if audio_ok else 'FAIL'}")
    if verify["pass"] is not None:
        print(f"decode check: {verify['decoded_frames']} of {verify['samples']} frames decoded; largest "
              f"block-mean luma difference {verify['largest_difference']} (threshold {verify['threshold']}); "
              f"frames over it {verify['frames_over_threshold']}; frames nearer a neighbour "
              f"{verify['frames_nearer_a_neighbour']} (noise {verify['noise_l2']})")
    print(f"pass={receipt['pass']}")
    return 0 if receipt["pass"] else 1


def _block_sig(fh, base: int, w: int, h: int, block: int = 80, step: int = 4) -> list[float]:
    """Mean luma of each block x block square of the Y plane at `base` (every
    step-th pixel of every step-th row): 144 numbers for 1280x720."""
    bw, bh = (w + block - 1) // block, (h + block - 1) // block
    sums = [0] * (bw * bh)
    counts = [0] * (bw * bh)
    for row in range(0, h, step):
        fh.seek(base + row * w)
        line = fh.read(w)
        base_idx = (row // block) * bw
        for bx in range(bw):
            seg = line[bx * block:min(w, (bx + 1) * block):step]
            sums[base_idx + bx] += sum(seg)
            counts[base_idx + bx] += len(seg)
    return [s / c for s, c in zip(sums, counts)]


def compare_decoded(dec: list[list[float]], src, mapping: list[int], y, threshold: float) -> dict:
    """verify_mp4's two tests on block signatures, with no file and no VLC:
    dec[k] is decoded frame k's, src(i) input frame i's, mapping[k] the input
    frame output frame k shows, and y.count the input frames. Returns the largest
    block difference ("worst"), the frames over `threshold` ("over"), the frames
    nearer an input neighbour than their own ("nearer"), and the codec's noise,
    the median distance to the nearest of the three candidates, from which the
    neighbour test's gate is made."""
    def l2(a: list[float], b: list[float]) -> float:
        return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5

    worst, over, nearer, noise = 0.0, [], [], 0.0
    n = len(dec)
    if not n:
        return {"worst": worst, "over": over, "nearer": nearer, "noise": noise}
    best = []
    for k in range(n):
        m = mapping[k]
        cands = [c for c in (m - 1, m, m + 1) if 0 <= c < y.count]
        best.append(min(l2(dec[k], src(c)) for c in cands))
    noise = sorted(best)[len(best) // 2]
    gate = max(3.0 * noise, 0.5)
    for k in range(n):
        m = mapping[k]
        a, b = dec[k], src(m)
        diff = max(abs(p - q) for p, q in zip(a, b))
        worst = max(worst, diff)
        if diff > threshold:
            over.append(k)
        to_self = l2(a, b)
        for nb in (m - 1, m + 1):
            if 0 <= nb < y.count and l2(src(nb), b) > gate and l2(a, src(nb)) <= to_self:
                nearer.append(k)
                break
    return {"worst": worst, "over": over, "nearer": nearer, "noise": noise}


def verify_mp4(mp4: Path, y: "Y4M", mapping: list[int], threshold: float = DECODE_THRESHOLD) -> dict:
    """Decode the MP4 back to raw I420 through VLC's stream output (no display clock,
    unlike the yuv video output, which dropped pictures) and hold every decoded frame
    to the player's frame at the same output position, by the mean luma of each 80x80
    block. Two tests (compare_decoded), both required:

      * the largest block difference from the mapped frame stays within `threshold`;
      * the decoded frame is nearer (L2 over the blocks) its mapped frame than either
        input neighbour, wherever that neighbour differs from the mapped frame by more
        than three times the codec's own error (the median, over all frames, of the
        distance to the nearest of the three candidates), so a one-frame slip inside
        a fade too slow for the threshold still fails.

    The raw file is deleted afterwards."""
    raw = mp4.with_name(mp4.name + ".decoded.i420")
    raw.unlink(missing_ok=True)
    w, h = y.width, y.height
    fb = w * h + 2 * ((w + 1) // 2) * ((h + 1) // 2)
    result = run_vlc([CVLC, "-I", "dummy", "--no-audio", str(mp4), "--sout",
                      f"#transcode{{vcodec=I420,acodec=none}}:std{{access=file,mux=dummy,dst={raw}}}",
                      TRANSCODE_THREADS, "--play-and-exit"], timeout=3600)
    decoded = raw.stat().st_size // fb if raw.exists() else 0
    n = min(decoded, len(mapping))
    got = compare_decoded([], None, mapping, y, threshold)
    if n:
        src_sig: dict[int, list[float]] = {}
        with raw.open("rb") as d, y.path.open("rb") as s:
            def src(i: int) -> list[float]:
                if i not in src_sig:
                    src_sig[i] = _block_sig(s, y.offset(i) + len(FRAME_TAG), w, h)
                return src_sig[i]

            dec = [_block_sig(d, k * fb, w, h) for k in range(n)]
            got = compare_decoded(dec, src, mapping, y, threshold)
    raw.unlink(missing_ok=True)
    worst, over, nearer, noise = got["worst"], got["over"], got["nearer"], got["noise"]
    return {"decoded_frames": decoded, "samples": len(mapping), "largest_difference": round(worst, 2),
            "threshold": threshold, "frames_over_threshold": len(over), "first_over": over[:10],
            "noise_l2": round(noise, 3), "frames_nearer_a_neighbour": len(nearer), "first_nearer": nearer[:10],
            "vlc_returncode": result["returncode"], "wall_s": result["wall_s"],
            "pass": decoded == len(mapping) and not over and not nearer}


def cmd_info(args) -> int:
    print(json.dumps(mp4_info(Path(args.mp4)), indent=2))
    return 0


def cmd_compare(args) -> int:
    """Two plays of one stream, frame by frame, by the player's bgrx_hash column."""
    a, b = read_times(Path(args.a)), read_times(Path(args.b))
    n = min(len(a), len(b))
    same = sum(1 for i in range(n) if a[i].get("bgrx_hash") == b[i].get("bgrx_hash"))
    differ = [a[i]["frame"] for i in range(n) if a[i].get("bgrx_hash") != b[i].get("bgrx_hash")]
    times_equal = all(a[i]["virtual_us"] == b[i]["virtual_us"] for i in range(n))
    print(f"frames {len(a)} and {len(b)}; compared {n}: identical {same}, different {len(differ)}"
          f"{' (first ' + str(differ[:10]) + ')' if differ else ''}; virtual times equal: {times_equal}")
    return 0 if same == n and len(a) == len(b) and times_equal else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="encode synthetic frames and check the MP4s")
    p.add_argument("--dir", required=True)
    p.add_argument("--frames", type=int, default=60)
    p.set_defaults(func=cmd_probe)
    e = sub.add_parser("encode", help="player frames (.y4m) + times -> MP4 at the recorded times")
    e.add_argument("--frames", required=True, help="the player's --frames-out .y4m")
    e.add_argument("--times", help="default: <frames>.times.tsv")
    e.add_argument("--report", required=True,
                   help="the player's --report of the run that wrote --frames: the frames are refused unless that "
                        "run finished, wrote exactly these files, and passes tools/s5/play_report.py's check (frames "
                        "bound to no run are never encoded)")
    e.add_argument("--dxvk-log",
                   help="the run's DXVK_LOG_PATH directory, for the check to hold DXVK's options (default: the "
                        "directory of the log the report names)")
    e.add_argument("--fps", default="60", help="output rate, N or N/D (default 60)")
    e.add_argument("--audio-wav", help="the player's --audio-wav of the same stream: the video carries it")
    e.add_argument("--out", required=True)
    e.add_argument("--hash", action="store_true", help="hash the frames file into the receipt")
    e.add_argument("--keep-staged", action="store_true",
                   help="keep <out>.resampled.y4m, the stream VLC encoded")
    e.add_argument("--no-verify", action="store_true",
                   help="skip decoding the MP4 back and comparing every frame")
    e.add_argument("--timeout", type=int, default=7200)
    e.set_defaults(func=cmd_encode)
    i = sub.add_parser("info", help="print an MP4's video track numbers")
    i.add_argument("mp4")
    i.set_defaults(func=cmd_info)
    c = sub.add_parser("compare", help="two plays' .times.tsv, frame by frame, by bgrx_hash")
    c.add_argument("a")
    c.add_argument("b")
    c.set_defaults(func=cmd_compare)
    args = parser.parse_args(argv)
    if args.cmd in ("probe", "encode") and not shutil.which(CVLC):
        raise SystemExit(f"{CVLC} is not installed")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
