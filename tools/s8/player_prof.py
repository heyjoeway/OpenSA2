#!/usr/bin/env python3
"""player_prof: where the player's render thread spends its CPU, from its own sampler.

Subtitle: reads the files host/player/prof.h writes when the player runs with
SA2_PLAYER_PROF=<Hz> (player-prof-<pid>-<tid>.samples, .frames, .modules.json), names every
sampled address from an unstripped copy of its module kept by GNU build-id, and prints the render
thread's CPU per frame, its split by what the code does (stream read and parse, the d3d9 device's
state translation, uploads, the d9g-to-SDL encoding, SDL's GPU layer, the GPU driver, the mixer,
the touch overlay, input), by the player's own zones, the slow frames against the fast ones, and
the top functions, self and inclusive.

  .venv/bin/python tools/s8/player_prof.py report DIR [--store S] [--elf F ...] [--top 30] [--json OUT]
      DIR holds one run's files (the newest run's when it holds several); --pid picks another.
  .venv/bin/python tools/s8/player_prof.py keep FILE ... [--store S]
      copies each ELF to S/<build-id>/<name>, the store `report` reads (a Linux player is found by
      its own path when it is still there; keep one that will be rebuilt).
  .venv/bin/python tools/s8/player_prof.py keep-android BUILD [--store S]
      BUILD is tools/s8/android_app.py's --out: relinks its libmain.so without the strip, by the
      link command the build logged (app/arm64-v8a/gen/link-libmain.log), into the store, and
      refuses the copy unless its build-id is the stripped libmain.so's; libSDL3.so's unstripped
      build output is kept too when the SDL build left one.
  .venv/bin/python tools/s8/player_prof.py pull-libs DIR [--adb ADB] [--serial S]
      pulls each system and vendor library the run's module map names (the GPU driver, libgsl,
      libvulkan, libc...) from the phone into DIR/libs, for `report --elf-dir DIR/libs`.
  .venv/bin/python tools/s8/player_prof.py sdl-counter [--out DIR]
      builds tools/s8/player_prof_sdl.c, an LD_PRELOAD library that counts the player's SDL GPU
      calls per frame by kind and the redundant ones (Linux; see that file).

Addresses: a sample's PC, then on arm64 its link register, and on x86-64 the word at its stack
pointer when the PC is in a C library (a leaf's return address), then the frame-pointer walk; a
return address is looked up one byte back (the call instruction). arm64 return addresses lose
their pointer-authentication bits (48- or 39-bit user addresses). Each sample stands for the CPU
time since the one before it (the thread's CPU clock in the sample).

The split: a sample goes to the category of its innermost address outside the C libraries (libc,
libm, libc++, libstdc++, the loader, the vDSO): SDL3's module is "SDL GPU", a Vulkan driver's
(vulkan.*, libvulkan*, libnvidia*, libgsl, libGLX/EGL, radeon, adreno, mesa) "GPU driver", and the
player's own code by its function's name (CATEGORIES). Without an unstripped copy a module is
named but its functions are not.

Output (stdout; --json writes the same as JSON): PLAYER_PROF_OK on success.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
STORE = ROOT / "private" / "generated" / "s8" / "player-prof" / "symbols"
NDK_BIN = ROOT / "private/android-sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/linux-x86_64/bin"

# prof.h's layouts.
HEAD = struct.Struct("<8s4I4I3Q4Q2Q2IQ")             # SampleHead, 128 bytes
SAMPLE = struct.Struct("<6QIBBH25Q")                  # Sample, 256 bytes
FHEAD = struct.Struct("<8s4I2Q3Q")                    # FrameHead, 64 bytes
FROW = struct.Struct("<2I2Q")                         # FrameRow, 24 bytes
assert HEAD.size == 128 and SAMPLE.size == 256 and FHEAD.size == 64 and FROW.size == 24
ZONES = ("other", "stream", "call", "d3dx", "present", "audio", "idle")
EM_X86_64, EM_AARCH64 = 62, 183

# Module classes, by file name.
DRIVER = re.compile(r"vulkan|nvidia|libgsl|adreno|radeon|amdgpu|intel|mesa|libGLX|libEGL|libgallium|libdrm|"
                    r"libllvm|libLLVM|gfxstream|swiftshader|libvk", re.I)
SDL = re.compile(r"libSDL3")
LIBC = re.compile(r"(^|/)(libc[.-]|libc\.so|libm[.-]|libm\.so|libstdc\+\+|libc\+\+|ld-linux|linker64|libdl|"
                  r"linux-vdso|\[vdso\]|libpthread|libgcc_s|libclang_rt)")

# The player's own code, by demangled function name; the first that matches names the category.
CATEGORIES = [
    ("stream hash (file replays only)", re.compile(r"sha256|Sha256|SHA256|blocks_shani|blocks_portable|hash_rest")),
    ("stream read/parse", re.compile(r"Sa2StreamReader|check_record|Sa2Record|ZSTD_|zstd|HUF_|FSE_")),
    ("mixer", re.compile(r"Sa2Mixer|mixer|audio_block|Sa2Wav|finish_audio|Player::.*audio")),
    ("touch overlay", re.compile(r"TouchOverlay|touch_overlay|sa2_touch|overlay")),
    ("input and window events", re.compile(r"pump_events|service_pads|KeySender|sa2_input|on_idle|idle_thunk|"
                                           r"service_thunk|send_pads")),
    ("reports and logs", re.compile(r"write_report|report_json|read_dxvk_log")),
    ("uploads (buffers, textures, UP draws)", re.compile(
        r"SdlGpu::(upload|write|ring|stage|copy|update|fill)|d9g::.*([Ll]ock|[Uu]pload|write_level|update_|"
        r"stage|ring)|texcodec|sa2_tex|decode_|dxt|png_|inflate|crc32|adler")),
    ("d9g-to-SDL encoding (SdlGpu)", re.compile(r"SdlGpu|d9g::sdl|sampler_object|pipeline_object|program_objects|"
                                                 r"render_pass")),
    ("shader translation", re.compile(r"d9g::shader|dxbc|dxvk::|spv|D9gShader|translate_shader")),
    ("d3d9 state translation (d9g)", re.compile(r"d9g::|Direct3D|IDirect3D")),
    ("player dispatch and checks", re.compile(r"Player::|sa2_player|main$")),
]
ORDER = ["stream read/parse", "stream hash (file replays only)", "player dispatch and checks",
         "d3d9 state translation (d9g)", "uploads (buffers, textures, UP draws)", "d9g-to-SDL encoding (SdlGpu)",
         "shader translation", "SDL GPU", "GPU driver", "mixer", "touch overlay", "input and window events",
         "reports and logs", "other (player)", "C library, no caller found", "other module", "unknown address"]


# ---------------------------------------------------------------- ELF

def read_elf(path: Path) -> dict:
    """An ELF64 file's machine, GNU build-id and function symbols (.symtab, else .dynsym)."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError(f"{path}: not a little-endian ELF64 file")
    (e_type, e_machine, _v, _entry, e_phoff, e_shoff, _flags, _ehsize, e_phentsize, e_phnum, e_shentsize,
     e_shnum, e_shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
    secs = [struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + i * e_shentsize) for i in range(e_shnum)]
    build_id = ""
    for (_n, typ, _f, _a, off, size, *_rest) in secs:
        if typ != 7:                                  # SHT_NOTE
            continue
        at, end = off, off + size
        while at + 12 <= end:
            namesz, descsz, ntype = struct.unpack_from("<III", data, at)
            name = data[at + 12:at + 12 + namesz]
            desc_at = at + 12 + ((namesz + 3) & ~3)
            if ntype == 3 and name[:3] == b"GNU":
                build_id = data[desc_at:desc_at + descsz].hex()
                break
            at = desc_at + ((descsz + 3) & ~3)
        if build_id:
            break
    syms: list[tuple[int, int, str]] = []
    kinds = []
    for want in (2, 11):                              # SHT_SYMTAB, SHT_DYNSYM
        for (_n, typ, _f, _a, off, size, link, _info, _al, entsize) in secs:
            if typ != want or not entsize:
                continue
            stroff = secs[link][4]
            for k in range(size // entsize):
                st_name, st_info, _other, st_shndx, st_value, st_size = struct.unpack_from("<IBBHQQ", data, off + k * entsize)
                if st_shndx == 0 or not st_value or (st_info & 0xF) not in (2, 10):   # FUNC, IFUNC
                    continue
                end_ = data.index(b"\0", stroff + st_name)
                syms.append((st_value, st_size, data[stroff + st_name:end_].decode("utf-8", "replace")))
            kinds.append("symtab" if want == 2 else "dynsym")
        if syms and want == 2:
            break
    syms.sort()
    h, any_x = 1469598103934665603, False
    for i in range(e_phnum):
        p_type, p_flags, p_offset, _va, _pa, p_filesz, _memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", data, e_phoff + i * e_phentsize)
        if p_type == 1 and p_flags & 1 and p_flags & 4:          # PT_LOAD, PF_X, PF_R
            h = text_hash(h, data[p_offset:p_offset + p_filesz])
            any_x = True
    return {"machine": e_machine, "type": e_type, "build_id": build_id, "symbols": syms, "kind": kinds[0] if kinds else "",
            "text_hash": f"{h:016x}" if any_x else ""}


def text_hash(h: int, seg: bytes) -> int:
    """prof.h's text_hash: 64-bit little-endian words, FNV-1a's prime, the length mixed in first."""
    m, prime = (1 << 64) - 1, 1099511628211
    n = len(seg)
    h ^= n
    whole = n - n % 8
    for (w,) in struct.iter_unpack("<Q", seg[:whole]):
        h = ((h ^ w) * prime) & m
    tail = int.from_bytes(seg[whole:], "little")
    return ((h ^ tail) * prime) & m


def identity(e: dict) -> str:
    """A store directory's name: the GNU build-id, else text-<the executable segments' hash>."""
    return e["build_id"] or (f"text-{e['text_hash']}" if e["text_hash"] else "")


def demangle(names: list[str]) -> dict[str, str]:
    mangled = sorted({n for n in names if n.startswith("_Z")})
    out = {n: n for n in names}
    tool = shutil.which("c++filt") or (str(NDK_BIN / "llvm-cxxfilt") if (NDK_BIN / "llvm-cxxfilt").is_file() else None)
    if not mangled or not tool:
        return out
    p = subprocess.run([tool], input="\n".join(mangled) + "\n", capture_output=True, text=True)
    lines = p.stdout.splitlines()
    if p.returncode == 0 and len(lines) == len(mangled):
        out.update(zip(mangled, lines))
    return out


# ---------------------------------------------------------------- the run's files

def find_run(directory: Path, pid: int | None) -> Path:
    samples = sorted(directory.glob("player-prof-*.samples"), key=lambda p: p.stat().st_mtime)
    if pid is not None:
        samples = [p for p in samples if p.name.startswith(f"player-prof-{pid}-")]
    if not samples:
        raise SystemExit(f"no player-prof-*.samples in {directory}")
    return samples[-1].with_suffix("")


def read_samples(base: Path) -> tuple[dict, list[tuple]]:
    raw = base.with_suffix(".samples").read_bytes()
    h = HEAD.unpack_from(raw, 0)
    if h[0] != b"SA2PPRF1":
        raise SystemExit(f"{base}.samples: not a player-prof samples file")
    keys = ("magic", "version", "head_bytes", "record_bytes", "hz", "pid", "tid", "machine", "depth", "capacity",
            "count", "dropped", "t0_mono_ns", "t0_cpu_ns", "t1_mono_ns", "t1_cpu_ns", "stack_lo", "stack_hi",
            "complete", "frames_at_stop", "reserved")
    head = dict(zip(keys, h))
    head.pop("magic")
    if head["record_bytes"] != SAMPLE.size or head["head_bytes"] != HEAD.size:
        raise SystemExit(f"{base}.samples: record {head['record_bytes']} B, head {head['head_bytes']} B: another version")
    n = min(head["count"], head["capacity"], (len(raw) - HEAD.size) // SAMPLE.size)
    rows = [SAMPLE.unpack_from(raw, HEAD.size + i * SAMPLE.size) for i in range(n)]
    # a killed run's file is its full mapped size: the samples taken are the ones with a clock
    rows = [r for r in rows if r[4]]
    return head, rows


def read_frames(base: Path) -> list[tuple]:
    path = base.with_suffix(".frames")
    if not path.is_file():
        return []
    raw = path.read_bytes()
    h = FHEAD.unpack_from(raw, 0)
    if h[0] != b"SA2PFRM1":
        return []
    n = min(h[6], (len(raw) - FHEAD.size) // FROW.size)
    return [FROW.unpack_from(raw, FHEAD.size + i * FROW.size) for i in range(n)]


class Module:
    def __init__(self, m: dict):
        self.name = m["name"]
        self.base = os.path.basename(self.name) or self.name
        self.bias, self.lo, self.hi = m["bias"], m["exec_lo"], m["exec_hi"]
        self.build_id = m.get("build_id", "")
        self.ident = self.build_id or (f"text-{m['text_hash']}" if m.get("text_hash") else "")
        self.cls = ("sdl" if SDL.search(self.base) else "libc" if LIBC.search(self.name)
                    else "driver" if DRIVER.search(self.base) else "other")
        self.segments = m.get("segments", [])
        self.syms: list[tuple[int, int, str]] = []
        self.starts: list[int] = []
        self.source = ""
        self.file: Path | None = None
        self._bytes: bytes | None = None

    def load(self, store: Path, extra: dict[str, Path]) -> None:
        cands = []
        if self.ident:
            cands += [extra[self.ident]] if self.ident in extra else []
            d = store / self.ident
            if d.is_dir():
                cands += sorted(d.iterdir())
        cands.append(Path(self.name))
        for c in cands:
            try:
                if not c.is_file():
                    continue
                e = read_elf(c)
            except (OSError, ValueError, struct.error):
                continue
            if self.ident and identity(e) != self.ident:
                continue
            self.syms = e["symbols"]
            self.starts = [s[0] for s in self.syms]
            self.source = f"{c} ({e['kind']}, {len(self.syms)} functions)"
            self.file = c
            return
        if self.build_id and Path(self.name).is_file():
            self.file = Path(self.name)        # no symbols, but its code (the call check below)

    def code(self, vaddr: int, n: int) -> bytes | None:
        """The module file's n bytes at a virtual address in a loaded segment, or None."""
        if self.file is None:
            return None
        if self._bytes is None:
            try:
                self._bytes = self.file.read_bytes()
            except OSError:
                self.file = None
                return None
        for seg in self.segments:
            if seg["vaddr"] <= vaddr and vaddr + n <= seg["vaddr"] + seg["memsz"]:
                at = vaddr - seg["vaddr"] + seg["offset"]
                return self._bytes[at:at + n] if at + n <= len(self._bytes) else None
        return None

    def follows_call(self, vaddr: int) -> bool:
        """Whether an x86-64 address follows a call instruction: E8 rel32, or FF /2 (indirect,
        through a register or memory, 2 to 7 bytes)."""
        b = self.code(vaddr - 7, 7)
        if b is None:
            return False
        if b[2] == 0xE8:
            return True
        for length in (2, 3, 4, 6, 7):
            op, modrm = b[7 - length], b[7 - length + 1]
            if op == 0xFF and (modrm >> 3) & 7 == 2:
                return True
            if length >= 3 and b[7 - length] in (0x41, 0x42, 0x43) and b[7 - length + 1] == 0xFF \
                    and (b[7 - length + 2] >> 3) & 7 == 2:
                return True
        return False

    def near(self, vaddr: int) -> str:
        i = bisect.bisect_right(self.starts, vaddr) - 1
        return self.syms[i][2] if i >= 0 else "its start"

    def func(self, vaddr: int) -> str | None:
        i = bisect.bisect_right(self.starts, vaddr) - 1
        if i < 0:
            return None
        start, size, name = self.syms[i]
        if size and vaddr >= start + size:
            # inside a gap: an unnamed (local, stripped) function after `name`
            return None
        return name


class Resolver:
    def __init__(self, modules: list[Module], machine: int, players: set[str]):
        self.mods = sorted(modules, key=lambda m: m.lo)
        self.los = [m.lo for m in self.mods]
        self.machine = machine
        self.players = players
        self.cache: dict[int, tuple[str, str, str] | None] = {}

    def module(self, addr: int) -> Module | None:
        i = bisect.bisect_right(self.los, addr) - 1
        if i >= 0 and self.mods[i].lo <= addr < self.mods[i].hi:
            return self.mods[i]
        return None

    def resolve(self, addr: int, is_return: bool) -> tuple[str, str, str] | None:
        """(module class, module name, function) of an address, or None outside every module."""
        key = addr * 2 + is_return
        if key in self.cache:
            return self.cache[key]
        cands = [addr]
        if self.machine == EM_AARCH64:
            cands += [addr & ((1 << 48) - 1), addr & ((1 << 39) - 1)]
        got = None
        for a in cands:
            look = a - 1 if is_return else a
            m = self.module(look)
            if m is None:
                continue
            f = m.func(look - m.bias)
            cls = "player" if m.base in self.players else m.cls
            # a module with no symbols is one name; an address between a module's symbols (a
            # local function a dynsym-only module does not name) is named after the one before it
            got = (cls, m.base, f if f else f"[{m.base}]" if not m.syms else f"[{m.base}] after {m.near(look - m.bias)}")
            break
        self.cache[key] = got
        return got


def bare_name(func: str) -> str:
    """A demangled function's qualified name without its parameter list (whose types would match
    other categories' rules: Sa2Mixer::apply_ds(Sa2Record const&, ...))."""
    f = func.replace("(anonymous namespace)", "{anon}")
    cut = f.find("(")
    return f if cut <= 0 else f[:cut]


def category_of(func: str) -> str | None:
    bare = bare_name(func)
    for name, rx in CATEGORIES:
        if rx.search(bare):
            return name
    return None


def owner_category(chain: list, dm: dict[str, str]) -> str:
    """The category of a sample's walk: its innermost SDL or driver frame, or player frame whose
    name a rule matches; the C libraries and other libraries (libpng, libz) are passed through,
    as are the player's helpers no rule names (std:: containers, anonymous helpers)."""
    seen_player = seen_other = False
    for c in chain:
        if c is None:
            continue
        if c[0] == "sdl":
            return "SDL GPU"
        if c[0] == "driver":
            return "GPU driver"
        if c[0] == "player":
            seen_player = True
            cat = category_of(dm.get(c[2], c[2]))
            if cat:
                return cat
        elif c[0] != "libc":
            seen_other = True
    return "other (player)" if seen_player else "other module" if seen_other else "C library, no caller found"


def analyse(directory: Path, store: Path, elfs: list[Path], top: int, pid: int | None) -> dict:
    base = find_run(directory, pid)
    head, rows = read_samples(base)
    frames = read_frames(base)
    mods_doc = json.loads(base.with_suffix(".modules.json").read_text())
    extra = {}
    for e in elfs:
        try:
            extra[identity(read_elf(e))] = e
        except (OSError, ValueError):
            pass
    modules = [Module(m) for m in mods_doc["modules"] if m["exec_hi"] > m["exec_lo"]]
    for m in modules:
        m.load(store, extra)
    # the player's own code: the app's libmain.so, a Linux player, or a Linux program that includes
    # prof.h (the first module dl_iterate_phdr names on glibc is the program itself)
    players = {m.base for m in modules if m.base in ("libmain.so", "sa2_player_gpu", "sa2_player")}
    if head["machine"] == EM_X86_64 and modules and modules[0].cls == "other":
        players.add(modules[0].base)
    rs = Resolver(modules, head["machine"], players)

    # weights: each sample is one timer expiry plus the ones its tick covered (si_overrun), scaled
    # so that they sum to the thread's CPU over the samples. (A sample's own CPU clock is no
    # weight: under a translator, as on the emulator, signals arrive in bursts.)
    cpu_span = (rows[-1][4] - head["t0_cpu_ns"]) if rows else 0
    ticks = sum(1 + r[9] for r in rows) or 1
    per_tick = cpu_span / ticks if cpu_span > 0 else 1e9 / max(head["hz"], 1)
    cat_w = collections.Counter()
    zone_w = collections.Counter()
    self_w = collections.Counter()
    incl_w = collections.Counter()
    called_from = {"SDL GPU": collections.Counter(), "GPU driver": collections.Counter()}
    by_frame_cat: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    resolved_depth = collections.Counter()
    total = 0
    names: set[str] = set()
    chains = []
    for r in rows:
        pc, lr, _sp, _fp, cpu, _mono, frame, zone, depth, _over = r[:10]
        rets = r[10:10 + depth]
        w = (1 + _over) * per_tick
        total += w
        leaf = rs.resolve(pc, False)
        chain = [leaf]
        if head["machine"] == EM_AARCH64:
            chain.append(rs.resolve(lr, True))
        else:
            # x86-64: the word at the stack pointer is the leaf's return address while the leaf
            # has pushed nothing; it is taken when it follows a call instruction in its module
            m = rs.module(lr - 1)
            if m is not None and m.follows_call(lr - m.bias):
                chain.append(rs.resolve(lr, True))
        chain += [rs.resolve(a, True) for a in rets]
        chains.append((w, frame, zone, chain))
        for c in chain:
            if c:
                names.add(c[2])
    dm = demangle(sorted(names))
    for w, frame, zone, chain in chains:
        zone_w[ZONES[zone] if zone < len(ZONES) else f"zone {zone}"] += w
        leaf = chain[0]
        if leaf is None:
            cat = "unknown address"
            self_w[("?", "?")] += w
        else:
            self_w[(leaf[1], dm.get(leaf[2], leaf[2]))] += w
            cat = owner_category(chain, dm)
            if cat == "C library, no caller found":       # named by what the player was doing
                cat = f"C library, no caller found (zone {ZONES[zone] if zone < len(ZONES) else zone})"
            if cat in called_from:
                caller = next((c for c in chain if c and c[0] == "player"), None)
                called_from[cat][dm.get(caller[2], caller[2]) if caller else "(no player frame in the walk)"] += w
        cat_w[cat] += w
        by_frame_cat[frame][cat] += w
        seen = set()
        known = 0
        for c in chain:
            if c is None:
                continue
            known += 1
            k = (c[1], dm.get(c[2], c[2]))
            if k not in seen:
                seen.add(k)
                incl_w[k] += w
        resolved_depth[min(known, 10)] += 1

    # frames: the render thread's CPU and wall time per Present
    per = []
    for a, b in zip(frames, frames[1:]):
        per.append((b[0], (b[3] - a[3]) / 1e6, (b[2] - a[2]) / 1e6))
    n_frames = len(per)
    cpu_ms = sum(p[1] for p in per)
    wall_ms = sum(p[2] for p in per)

    def pct(values, q):
        if not values:
            return None
        v = sorted(values)
        return round(v[min(len(v) - 1, int(q * len(v)))], 3)

    # slow frames against the rest (a frame's own wall time over 1/50 s)
    slow = {f for f, _c, wl in per if wl > 20.0}
    fast = {f for f, _c, wl in per if wl <= 20.0}

    def split_of(frameset):
        c = collections.Counter()
        for f in frameset:
            c.update(by_frame_cat.get(f, {}))
        return c

    s_slow, s_fast = split_of(slow), split_of(fast)
    # windows of ~5 s: fps, CPU per frame, the thread's share of a core
    windows = []
    if frames:
        start = frames[0][2]
        cur = []
        for row in frames:
            cur.append(row)
            if row[2] - start >= 5e9 and len(cur) > 1:
                dt, dc = (cur[-1][2] - cur[0][2]) / 1e9, (cur[-1][3] - cur[0][3]) / 1e9
                windows.append({"from_frame": cur[0][0], "to_frame": cur[-1][0], "seconds": round(dt, 2),
                                "fps": round((len(cur) - 1) / dt, 1), "cpu_ms_per_frame": round(dc * 1e3 / (len(cur) - 1), 3),
                                "core_share": round(dc / dt, 3)})
                start, cur = row[2], [row]

    cpu_total_ms = (head["t1_cpu_ns"] - head["t0_cpu_ns"]) / 1e6 if head["complete"] else (rows[-1][4] - head["t0_cpu_ns"]) / 1e6 if rows else 0
    ms_per_frame = cpu_ms / n_frames if n_frames else None

    def table(counter, scale_frames):
        out = []
        for k, w in counter.most_common():
            out.append({"name": k, "share": round(w / total, 4) if total else 0,
                        "ms_per_frame": round(w / 1e6 / scale_frames, 3) if scale_frames else None})
        return out

    sampled_frames = len({c[1] for c in chains}) or 1
    frames_for_split = n_frames or sampled_frames
    res = {
        "schema": "sa2-player-prof-report/v1",
        "run": str(base),
        "machine": {EM_X86_64: "x86_64", EM_AARCH64: "aarch64"}.get(head["machine"], head["machine"]),
        "hz_asked": head["hz"],
        "samples": len(rows), "samples_dropped": head["dropped"], "complete": bool(head["complete"]),
        "hz_reached": round(len(rows) / (cpu_total_ms / 1e3), 1) if cpu_total_ms else None,
        "thread_cpu_ms": round(cpu_total_ms, 1),
        "frames": n_frames,
        "cpu_ms_per_frame": round(ms_per_frame, 3) if ms_per_frame else None,
        "wall_ms_per_frame": round(wall_ms / n_frames, 3) if n_frames else None,
        "core_share": round(cpu_ms / wall_ms, 3) if wall_ms else None,
        "cpu_ms_per_frame_p50_p90_p99": [pct([p[1] for p in per], q) for q in (0.5, 0.9, 0.99)],
        "wall_ms_per_frame_p50_p90_p99": [pct([p[2] for p in per], q) for q in (0.5, 0.9, 0.99)],
        "split": sorted(table(cat_w, frames_for_split), key=lambda e: ORDER.index(e["name"]) if e["name"] in ORDER else 99),
        "zones": table(zone_w, frames_for_split),
        "sdl_called_from": {k: table(v, frames_for_split)[:8] for k, v in called_from.items() if v},
        "slow_frames": {"count": len(slow), "threshold_wall_ms": 20.0,
                        "cpu_ms_per_frame": round(sum(p[1] for p in per if p[0] in slow) / len(slow), 3) if slow else None,
                        "split_ms_per_frame": {k: round(v / 1e6 / len(slow), 3) for k, v in s_slow.most_common()} if slow else {}},
        "fast_frames": {"count": len(fast),
                        "cpu_ms_per_frame": round(sum(p[1] for p in per if p[0] in fast) / len(fast), 3) if fast else None,
                        "split_ms_per_frame": {k: round(v / 1e6 / len(fast), 3) for k, v in s_fast.most_common()} if fast else {}},
        "windows": windows,
        "top_self": [{"module": m, "function": f, "share": round(w / total, 4), "ms_per_frame": round(w / 1e6 / frames_for_split, 3)}
                     for (m, f), w in self_w.most_common(top)],
        "top_inclusive": [{"module": m, "function": f, "share": round(w / total, 4),
                           "ms_per_frame": round(w / 1e6 / frames_for_split, 3)}
                          for (m, f), w in incl_w.most_common(top)],
        "walk_depth": {str(k): v for k, v in sorted(resolved_depth.items())},
        "modules": [{"name": m.base, "class": "player" if m.base in players else m.cls, "build_id": m.ident,
                     "symbols": m.source or "none (name only)"} for m in modules
                    if any(c and c[1] == m.base for _w, _f, _z, ch in chains[:5000] for c in ch[:1])],
    }
    return res


def print_report(r: dict) -> None:
    print(f"run {r['run']} ({r['machine']}): {r['samples']} samples ({r['samples_dropped']} dropped), "
          f"{r['hz_asked']} Hz asked, {r['hz_reached']} Hz reached, {r['thread_cpu_ms']} ms of the render thread's CPU"
          f"{'' if r['complete'] else ' (no stop: a killed run)'}")
    print(f"frames {r['frames']}: render thread {r['cpu_ms_per_frame']} ms CPU a frame (p50/p90/p99 "
          f"{r['cpu_ms_per_frame_p50_p90_p99']}), {r['wall_ms_per_frame']} ms wall a frame, {r['core_share']} of a core")
    print("\nsplit (ms a frame, share of the thread's CPU):")
    for e in r["split"]:
        print(f"  {e['name']:44s} {e['ms_per_frame']:8.3f}  {100 * e['share']:5.1f} %")
    print("\nzones:")
    for e in r["zones"]:
        print(f"  {e['name']:44s} {e['ms_per_frame']:8.3f}  {100 * e['share']:5.1f} %")
    for k, v in r["sdl_called_from"].items():
        print(f"\n{k}, by the player function that called it:")
        for e in v:
            print(f"  {e['name'][:90]:90s} {e['ms_per_frame']:8.3f}  {100 * e['share']:5.1f} %")
    for key in ("slow_frames", "fast_frames"):
        s = r[key]
        if s["count"]:
            top = ", ".join(f"{k} {v}" for k, v in list(s["split_ms_per_frame"].items())[:6])
            print(f"\n{key.replace('_', ' ')}: {s['count']}, {s['cpu_ms_per_frame']} ms CPU a frame; {top}")
    if r["windows"]:
        print("\nwindows (fps, CPU ms a frame, core share):")
        for w in r["windows"]:
            print(f"  frames {w['from_frame']:6d}-{w['to_frame']:6d} {w['seconds']:5.1f} s  {w['fps']:6.1f} fps  "
                  f"{w['cpu_ms_per_frame']:7.3f} ms  {w['core_share']:.2f}")
    for key, title in (("top_self", "top functions, self"), ("top_inclusive", "top functions, inclusive")):
        print(f"\n{title} (ms a frame, share):")
        for e in r[key]:
            print(f"  {e['ms_per_frame']:8.3f} {100 * e['share']:5.1f} %  {e['module'][:22]:22s} {e['function'][:110]}")
    print("\nwalk depth (resolved addresses a sample: samples):", r["walk_depth"])
    print("modules sampled:")
    for m in r["modules"]:
        print(f"  {m['name']:28s} {m['class']:7s} {m['build_id'][:16]:16s} {m['symbols']}")


# ---------------------------------------------------------------- keeping symbols

def keep(files: list[Path], store: Path) -> list[str]:
    kept = []
    for f in files:
        e = read_elf(f)
        if not identity(e):
            raise SystemExit(f"{f}: no GNU build-id and no executable segment, so a report cannot match it")
        d = store / identity(e)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f.name
        shutil.copyfile(f, dest)
        kept.append(f"{dest} ({e['kind']}, {len(e['symbols'])} functions)")
    return kept


def keep_android(build: Path, store: Path) -> list[str]:
    b = build / "app" / "arm64-v8a"
    log = b / "gen" / "link-libmain.log"
    stripped = b / "lib" / "libmain.so"
    if not log.is_file() or not stripped.is_file():
        raise SystemExit(f"no {log} or {stripped}: build the player phase first")
    want = identity(read_elf(stripped))
    cmd = shlex.split(log.read_text().split("\n", 1)[0])
    out_i = cmd.index("-o") + 1
    tmp = store / "tmp-libmain.so"
    store.mkdir(parents=True, exist_ok=True)
    cmd[out_i] = str(tmp)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"the relink failed: {p.stderr[-2000:]}")
    got = read_elf(tmp)
    if identity(got) != want:
        tmp.unlink()
        raise SystemExit(f"the relinked libmain.so is {identity(got)}, the APK's {want}: not kept")
    d = store / want
    d.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, d / "libmain.so")
    kept = [f"{d / 'libmain.so'} ({got['kind']}, {len(got['symbols'])} functions)"]
    sdl = b / "lib" / "libSDL3.so"
    if sdl.is_file():
        sdl_id = identity(read_elf(sdl))
        for c in sorted((b / "sdl3").rglob("libSDL3.so*")) if (b / "sdl3").is_dir() else []:
            try:
                e = read_elf(c)
            except (OSError, ValueError):
                continue
            if identity(e) == sdl_id and e["kind"] == "symtab":
                kept += keep([c], store)
                break
        else:
            kept += keep([sdl], store)
    return kept


def pull_libs(directory: Path, adb: str, serial: str | None) -> list[str]:
    base = find_run(directory, None)
    mods = json.loads(base.with_suffix(".modules.json").read_text())["modules"]
    out = directory / "libs"
    out.mkdir(exist_ok=True)
    got = []
    for m in mods:
        name = m["name"]
        if not name.startswith(("/system/", "/vendor/", "/apex/", "/odm/", "/product/")) or "!" in name:
            continue
        if not (DRIVER.search(os.path.basename(name)) or LIBC.search(name)):
            continue                                    # the driver's and the C libraries' only
        dest = out / os.path.basename(name)
        if dest.exists():
            continue
        cmd = [adb, *(["-s", serial] if serial else []), "pull", name, str(dest)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        got.append(f"{name}: {'ok' if p.returncode == 0 else (p.stderr or p.stdout).strip()[-200:]}")
    return got


def sdl_counter(out: Path) -> Path:
    src = ROOT / "tools" / "s8" / "player_prof_sdl.c"
    inc = ROOT / "private" / "s5-tools" / "install" / "include"
    out.mkdir(parents=True, exist_ok=True)
    lib = out / "player_prof_sdl.so"
    # SDL3's own version node: the player's references are SDL_*@SDL3_0.0.0
    script = out / "player_prof_sdl.map"
    script.write_text("SDL3_0.0.0 {\n  global: SDL_*;\n  local: *;\n};\n")
    subprocess.run(["gcc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-fPIC", "-shared", f"-I{inc}", "-o", str(lib),
                    str(src), f"-Wl,--version-script={script}", "-ldl"], check=True)
    return lib


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("dir", type=Path)
    r.add_argument("--store", type=Path, default=STORE)
    r.add_argument("--elf", type=Path, action="append", default=[],
                   help="an unstripped or dynsym ELF a module is symbolized from, matched by build-id (repeatable)")
    r.add_argument("--elf-dir", type=Path, action="append", default=[],
                   help="every file in DIR as --elf (the phone's pulled driver and system libraries)")
    r.add_argument("--top", type=int, default=30)
    r.add_argument("--pid", type=int)
    r.add_argument("--json", type=Path)
    k = sub.add_parser("keep")
    k.add_argument("files", type=Path, nargs="+")
    k.add_argument("--store", type=Path, default=STORE)
    ka = sub.add_parser("keep-android")
    ka.add_argument("build", type=Path)
    ka.add_argument("--store", type=Path, default=STORE)
    pl = sub.add_parser("pull-libs")
    pl.add_argument("dir", type=Path)
    pl.add_argument("--adb", default=str(ROOT / "private/android-sdk/platform-tools/adb"))
    pl.add_argument("--serial")
    sc = sub.add_parser("sdl-counter")
    sc.add_argument("--out", type=Path, default=ROOT / "private" / "generated" / "s8" / "player-prof" / "sdl-counter")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        elfs = list(a.elf) + [f for d in a.elf_dir for f in sorted(d.rglob("*")) if f.is_file()]
        res = analyse(a.dir, a.store, elfs, a.top, a.pid)
        sdl_json = a.dir / "sdl-gpu-calls.json"
        if sdl_json.is_file():
            res["sdl_gpu_calls"] = json.loads(sdl_json.read_text())
        print_report(res)
        if a.json:
            a.json.write_text(json.dumps(res, indent=1) + "\n")
        print("PLAYER_PROF_OK", res["run"])
    elif a.cmd == "keep":
        for line in keep(a.files, a.store):
            print("kept", line)
        print("PLAYER_PROF_OK keep")
    elif a.cmd == "keep-android":
        for line in keep_android(a.build, a.store):
            print("kept", line)
        print("PLAYER_PROF_OK keep-android")
    elif a.cmd == "pull-libs":
        for line in pull_libs(a.dir, a.adb, a.serial):
            print("pull", line)
        print("PLAYER_PROF_OK pull-libs")
    elif a.cmd == "sdl-counter":
        print("PLAYER_PROF_OK", sdl_counter(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
