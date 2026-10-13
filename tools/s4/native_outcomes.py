#!/usr/bin/env python3
"""What the native game gets where the runtime refuses, read from traced native boots.

Subtitle: pin the Wine-traced native boots, find in each the lines that show
what the game's own calls got -- DirectInput, the WMI scan, XAudio2, DirectDraw's
monitor enumeration, XInput, DirectSound, D3DX's constant tables -- and which
imports the game calls before the Mod Loader's Init, where no import log can
see; write them to one tracked receipt, and recompute it on demand.

    .venv/bin/python tools/s4/native_outcomes.py            # print what the logs show
    .venv/bin/python tools/s4/native_outcomes.py --write    # write the tracked receipt
    .venv/bin/python tools/s4/native_outcomes.py --check    # recompute it and compare

Why this exists
---------------
On 2026-09-23 the user ruled that "a refusal counts as implemented only if it
reproduces the native outcome"
(Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md). A ruling
about native outcomes needs them as a record a gate can read.
`Documentation/refusal-reasons.json` names, for every refusal, the outcome in
this receipt it reproduces or does not;
`Documentation/scripts/check_refused_presentation.py` holds the two together;
and `tools/s4/import_status.py` adds this receipt's pre-Init imports to M4's
replay-reachable set.

What an outcome is
------------------
An outcome is a short sequence of trace lines, each matched by a rule in this
file, in order, on the game's own threads, and each showing one step: that the
game made the call, and what it did next with the answer. Wine's `+channel`
traces print arguments, not return values, so success is shown the way the
guest itself shows it -- by what it calls through the result. The game's
DirectInput8Create succeeded because the game calls CreateDevice on the
interface it got back: the guard at 0x00864d42 tests that pointer, and on a
failed call 0x00864d3c has already cleared it.

A step is cited -- its first match recorded as line number and exact text --
or counted, with bounds. A count of zero shows an absence only in a log whose
channel was traced, so a zero-count step on an untraced channel is refused. A
step's `class:channel:function` is matched as a shell pattern, so one count can
cover a family of functions: `*:d3dx:ID3DXConstantTableImpl_*` is every line
Wine's d3dx9 prints from any method of a constant table. A counted step may
name its count (`counted_as`), and a later step's bounds may use it, so "one
line per call" is a check rather than two numbers a reader compares.

An outcome may also name, per log, the level and game state its capture's stage
trace ends in (`reaches`), when what it shows is an absence through gameplay:
the trace is pinned by the sha256 its capture's receipt records, and a run that
ends elsewhere is refused.

Each outcome also carries two things a gate reads:

* its **answer class**, `success` or `failure`: what the native call returned,
  as the game's next step shows it. A refusal answers failure, so
  `Documentation/scripts/check_refused_presentation.py` accepts a refusal's
  claim to reproduce a `success` outcome only while every switch that replaces
  the refusal with a device is on in the replay configuration;
* the **device-profile records** that carry its answer on a capture
  (`profile_records`, record kinds of runtime/sa2_devprofile.h's grammar,
  checked against `tools/s4/device_profile.py` on every run). `host_dependent`
  is whether there are any, derived rather than typed: before 2026-09-23 it was
  set by hand, read by nothing, and wrong for the XAudio2 stream, whose
  mastering voice's details the profile records.

Threads
-------
The game's main thread is the one that loads the executable (`+loaddll`) or,
in a log traced without `+loaddll`, the one that calls DirectInput8Create with
the executable's module handle 00400000, or, in a log traced with neither, the
one thread that calls D3DXGetShaderConstantTable, and only if exactly one does.
The last rule stands on two checks: the import's one call in the executable is
at 0x00867225, in the routine at 0x008671b0 (searched for and decoded on every
run, outcome d3dx-constant-table's `call_sites`), and in
every log that traces `+d3dx` beside a thread found by the first two rules,
every D3DXGetShaderConstantTable line is on that thread. The XAudio2 streaming thread is the
one other thread that calls the source voice the main thread created. Those
two are the only threads a step is matched on, except the counts over every
thread that show an absence. The other thread ids are Wine's -- its own 64-bit
processes, and internals such as FAudio's mixing thread, which calls the
engine's callback wrappers and never the game -- or the game's own threads
that no outcome needs: in native-outcomes-v1b, 01ac is the game's DirectSound
thread, with thousands of IDirectSoundBuffer Lock, GetStatus and
GetCurrentPosition calls. None of them is cited.

Before the Mod Loader's Init
----------------------------
The native import probe counts from the Mod Loader's Init, which is when the
loader loads the enabled mods. So a log's Init boundary is the main thread's
first `+loaddll` line loading a DLL of a mod its capture's receipt enabled, and
an import is reached before Init when its line on the main thread precedes that
boundary. Each such import is also decoded at its call site in the executable
-- the instruction must reference the import's own slot, directly or through a
`jmp [slot]` thunk -- so a trace line is attributed to the game's call and not
to one of Wine's internal calls through the same entry point.

What the receipt leaves out
---------------------------
Host inventory: no Win32_PNPEntity DeviceID, and no device, display or HID
name, and no USB VID:PID. The WMI scan's lines that carry DeviceIDs are never
cited. A cited line with a GUID that is not one of the classes and interfaces
the image itself holds has it replaced by `{redacted}`, and a DirectInput
`product: VID:PID` by `product: <redacted>`; every evidence item carries the
sha256 of the exact line, so the check still binds it to the log. The host's
HID device list is recorded as its count and sha256, and the enumerated
game-controller device as its dwDevType only. Writing refuses a receipt in
which any string matches a device ID, a host HID device, the VID:PID of a host
HID device, or a GUID outside that list.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import functools
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

RECEIPT_REL = "Documentation/receipts/s4-native-outcomes-v1.json"
RECEIPT = ROOT / RECEIPT_REL
SELF = "tools/s4/native_outcomes.py"
DECISION = "Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md"
CAPTURES = "private/generated/oracle/captures"
LOG_NAME = "native-run.log"
EXE_NAME = "sonic2app-unpacked.exe"
SCHEMA = "sa2-recomp-receipt/v1"
RECEIPT_ID = "s4-native-outcomes-v1"

# The traced boots, by the name each capture's receipt gives itself. The first
# two were taken for this receipt with the runner recording WINEDEBUG and the
# host's HID devices; the third is the `+dinput` trace the M4 decision cites,
# which predates both fields. The last two are vanilla-oracle boots traced with
# `+d3dx` (the first with `+loaddll`, the second run into the City Escape
# attract demo's gameplay), taken for D3DXGetShaderConstantTable.
LOGS = ("native-outcomes-v1a", "native-outcomes-v1b", "dinput-trace-v1a",
        "native-outcomes-v1c", "native-outcomes-v1d")

LINE = re.compile(r"([0-9a-f]{4}):(trace|fixme|warn|err):([A-Za-z0-9_]+):([A-Za-z0-9_]+) (.*)\Z")
GUID = re.compile(r"\{([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}")
WIDE = re.compile(r'L"((?:[^"\\]|\\.)*)"')
REDACTED = "{redacted}"

# The classes and interfaces the executable holds, at the addresses its call
# sites push. These are the only GUIDs a cited line keeps; each is read back
# from the image on every run, so a typo here is a failure, not a new allowance.
IMAGE_GUIDS = {
    "CLSID_WbemLocator": (0x008FFF88, "00000000-0000-0000-0000-000000000000"),
    "IID_IWbemLocator": (0x008FFF98, "00000000-0000-0000-0000-000000000000"),
    "CLSID_XAudio2": (0x00902200, "00000000-0000-0000-0000-000000000000"),
    "IID_IXAudio2": (0x00902210, "00000000-0000-0000-0000-000000000000"),
    "IID_IDirectInput8A": (0x0087355C, "00000000-0000-0000-0000-000000000000"),
    "GUID_SysKeyboard": (0x0087360C, "00000000-0000-0000-0000-000000000000"),
    "GUID_SysMouse": (0x008735FC, "00000000-0000-0000-0000-000000000000"),
    "IID_IDirectDraw7": (0x0087354C, "00000000-0000-0000-0000-000000000000"),
}
KEPT_GUIDS = frozenset(g for _, g in IMAGE_GUIDS.values())

# Host inventory, by shape: the pieces of a Plug and Play device ID. A string
# in the receipt that matches is refused, whatever else it says.
DEVICE_ID = re.compile(r"(?i)\b(?:vid|pid|ven|dev)_[0-9a-f]{4}|\bsubsys_[0-9a-f]{8}"
                       r"|default_monitor|\broot\\+wine\\+|\b(?:hid|usb|pci|winebus)\\+[a-z0-9_]")
# DirectInput's joystick cache prints a device's USB vendor and product ids.
PRODUCT = re.compile(r"\bproduct: [0-9a-fA-F]{4}:[0-9a-fA-F]{4}\b")

STAGE_TRACE = "sa2-stage-trace.csv"
STAGE_LAST = ("tick", "current_level", "game_state", "ingame_frames")
IN_GAME = 0x10        # game_state of a level being played (tools/s4/demo_run.py reads it so)

MAIN = "main"
ANY = "any"           # every thread: only for counts, where zero on all is the stronger claim
STREAM = "xaudio2-stream"
TICKS = "ticks"       # a count bound equal to the capture's tick count

# The answer classes (module docstring). The gate reads them; nothing else may
# stand in an outcome's `answer`.
SUCCESS, FAILURE = "success", "failure"
ANSWERS = (SUCCESS, FAILURE)


class Refused(Exception):
    """A log does not show what a rule says it shows."""


# ---------------------------------------------------------------- the logs

@dataclass
class Log:
    key: str
    rel: str
    sha256: str
    lines: list[str]
    by_fn: dict[tuple[str, str], list[int]]      # (thread, "class:channel:function") -> line numbers
    by_tid: dict[str, list[int]]                  # thread -> its prefixed line numbers, in order
    pos: dict[int, int]                           # line number -> index in by_tid[its thread]
    traced: frozenset[str]                        # channels with at least one `trace:` line

    def rest(self, n: int) -> str:
        m = LINE.match(self.lines[n - 1])
        return m.group(5) if m else ""

    def tid(self, n: int) -> str:
        return self.lines[n - 1][:4]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@functools.lru_cache(maxsize=8)
def _parse(path: str, size: int, mtime: int) -> tuple[str, tuple[str, ...]]:
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise Refused(f"{path} is not UTF-8 ({e}), so a cited line's text is not its bytes")
    if "\r" in text:
        raise Refused(f"{path} has carriage returns; a cited line would not be the line")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return hashlib.sha256(data).hexdigest(), tuple(lines)


def read_log(key: str, root: Path = ROOT) -> Log:
    rel = f"{CAPTURES}/{key}/{LOG_NAME}"
    path = root / rel
    if not path.is_file():
        raise Refused(f"{rel} is missing")
    st = path.stat()
    digest, lines = _parse(str(path), st.st_size, st.st_mtime_ns)
    by_fn: dict[tuple[str, str], list[int]] = {}
    by_tid: dict[str, list[int]] = {}
    pos: dict[int, int] = {}
    traced = set()
    for n, ln in enumerate(lines, 1):
        m = LINE.match(ln)
        if not m:
            continue
        tid, cls, chan, fn = m.group(1), m.group(2), m.group(3), m.group(4)
        by_fn.setdefault((tid, f"{cls}:{chan}:{fn}"), []).append(n)
        seq = by_tid.setdefault(tid, [])
        pos[n] = len(seq)
        seq.append(n)
        if cls == "trace":
            traced.add(chan)
    return Log(key, rel, digest, list(lines), by_fn, by_tid, pos, frozenset(traced))


def capture_receipt(key: str, root: Path = ROOT) -> tuple[str, dict, str]:
    rel = f"{CAPTURES}/{key}/receipt.json"
    path = root / rel
    if not path.is_file():
        raise Refused(f"{rel} is missing")
    return rel, json.loads(path.read_text()), sha256_file(path)


def ticks_of(receipt: dict) -> int | None:
    """The capture's tick count, or None when it has no checked tick capture;
    a step bounded by TICKS is then refused (bound)."""
    for a in receipt.get("artifacts", []):
        if a.get("file") == "sa2-tick-capture.csv" and (a.get("check") or {}).get("ok"):
            return int(a["check"]["rows"])
    return None


def stage_trace(key: str, receipt: dict, root: Path = ROOT) -> dict | None:
    """Where the capture's stage trace ends: its rows, the rows in a level being
    played, and the last row's level and state; None when the capture has no
    checked stage trace. The file is pinned by the sha256 its receipt records."""
    art = next((a for a in receipt.get("artifacts", []) if a.get("file") == STAGE_TRACE
                and (a.get("check") or {}).get("ok")), None)
    if art is None:
        return None
    rel = f"{CAPTURES}/{key}/{STAGE_TRACE}"
    path = root / rel
    if not path.is_file():
        raise Refused(f"{rel} is missing")
    digest = sha256_file(path)
    if digest != art["check"].get("sha256"):
        raise Refused(f"{rel} does not hash to the sha256 its capture's receipt records")
    rows = list(csv.DictReader(path.read_text().splitlines()))
    if not rows or len(rows) != art["check"].get("rows"):
        raise Refused(f"{rel} has {len(rows)} rows, not the {art['check'].get('rows')} its "
                      "capture's receipt checked")
    return {"file": STAGE_TRACE, "sha256": digest, "rows": len(rows),
            "in_game_rows": sum(int(r["game_state"], 16) == IN_GAME for r in rows),
            "last": {k: rows[-1][k] for k in STAGE_LAST}}


def d3dx_callers(log: Log) -> set[str]:
    """The threads with a D3DXGetShaderConstantTable line."""
    return {tid for (tid, fn) in log.by_fn if fn == "trace:d3dx:D3DXGetShaderConstantTable"}


def main_thread(log: Log) -> str:
    """The game's main thread, by the rule in the module docstring."""
    exe = [n for (tid, fn), ns in log.by_fn.items() if fn == "trace:loaddll:build_module"
           for n in ns if f"\\\\{EXE_NAME}\" at 00400000" in log.lines[n - 1]]
    if exe:
        return log.tid(min(exe))
    di = [n for (tid, fn), ns in log.by_fn.items() if fn == "trace:dinput:DirectInput8Create"
          for n in ns if log.rest(n).startswith("hinst 00400000,")]
    if di:
        return log.tid(min(di))
    ct = d3dx_callers(log)
    if len(ct) != 1:
        raise Refused(f"{log.rel}: neither the executable's load nor a DirectInput8Create "
                      f"with its module handle is traced, and {len(ct)} threads call "
                      "D3DXGetShaderConstantTable, not one, so no thread is the game's")
    return ct.pop()


def mod_init(log: Log, main: str, receipt: dict) -> int | None:
    """The line of the Mod Loader's Init: the main thread's first load of an enabled mod."""
    if "loaddll" not in log.traced:
        return None
    mods = receipt.get("inputs", {}).get("enabled_mods") or []
    if not mods:
        raise Refused(f"{log.rel}'s receipt enables no mod, so the log has no Init boundary")
    hits = [n for n in log.by_fn.get((main, "trace:loaddll:build_module"), [])
            if any(f"\\\\mods\\\\{m}\\\\" in log.lines[n - 1] for m in mods)]
    if not hits:
        raise Refused(f"{log.rel}: no enabled mod ({', '.join(mods)}) is loaded on the main "
                      "thread, so the log never reaches the Mod Loader's Init")
    return min(hits)


def hid_digest(devices: list[str]) -> str:
    """The sha256 of a HID device list as the runner records it, order-free."""
    return hashlib.sha256(json.dumps(sorted(devices), separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the image

@functools.lru_cache(maxsize=2)
def image(path: str):
    import pefile
    pe = pefile.PE(path, fast_load=True)
    return pe, pe.OPTIONAL_HEADER.ImageBase


def exe_path(root: Path = ROOT) -> Path:
    return root / "private" / "exe" / EXE_NAME


def read_va(va: int, n: int, root: Path = ROOT) -> bytes:
    pe, base = image(str(exe_path(root)))
    return pe.get_data(va - base, n)


def wide_at(va: int, root: Path = ROOT) -> str:
    raw = read_va(va, 256, root)
    units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw) - 1, 2)]
    s = "".join(map(chr, units[:units.index(0)])) if 0 in units else ""
    if not s or not all(0x20 <= ord(c) < 0x7F for c in s):
        raise Refused(f"no printable wide string at {va:#010x}")
    return s


def guid_at(va: int, root: Path = ROOT) -> str:
    import uuid
    return str(uuid.UUID(bytes_le=read_va(va, 16, root)))


def decode_at(va: int, root: Path = ROOT):
    from iced_x86 import Decoder
    return next(iter(Decoder(32, read_va(va, 16, root), ip=va)))


def slot_of_site(va: int, root: Path = ROOT) -> int | None:
    """The import slot the instruction at `va` references: `call [slot]`,
    `mov reg,[slot]`, or a `call` to a `jmp [slot]` thunk."""
    from iced_x86 import Mnemonic, OpKind, Register

    def mem(ins) -> int | None:
        for k in range(ins.op_count):
            if (ins.op_kind(k) == OpKind.MEMORY and ins.memory_base == Register.NONE
                    and ins.memory_index == Register.NONE):
                return ins.memory_displacement
        return None

    ins = decode_at(va, root)
    if ins.mnemonic in (Mnemonic.CALL, Mnemonic.MOV) and mem(ins) is not None:
        return mem(ins)
    if ins.mnemonic == Mnemonic.CALL and ins.op0_kind == OpKind.NEAR_BRANCH32:
        thunk = decode_at(ins.near_branch_target, root)
        if thunk.mnemonic == Mnemonic.JMP:
            return mem(thunk)
    return None


def push_imm_at(va: int, root: Path = ROOT) -> int | None:
    from iced_x86 import Mnemonic, OpKind
    ins = decode_at(va, root)
    if ins.mnemonic == Mnemonic.PUSH and ins.op0_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
        return ins.immediate(0) & 0xFFFFFFFF
    return None


@functools.lru_cache(maxsize=4)
def import_calls(imp: str, root: Path = ROOT) -> tuple[int, ...]:
    """Every call of `imp` in the executable's code, found by search rather than
    listed: each place the code section holds the import's slot address is
    decoded, and a `call [slot]` there is a call; a `jmp [slot]` is a thunk, and
    every near call whose target is the thunk is a call. Any other instruction
    that holds the slot is refused, since it would be a use this search does
    not follow."""
    import native_imports as NI
    from iced_x86 import Mnemonic, OpKind, Register
    pe, base = image(str(exe_path(root)))
    slot = {v: k for k, v in NI.slots(exe_path(root)).items()}[imp]
    text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
    tva, code = base + text.VirtualAddress, text.get_data()
    calls, thunks = [], []
    for m in re.finditer(re.escape(slot.to_bytes(4, "little")), code):
        va = tva + m.start() - 2          # FF 15 / FF 25 and a disp32
        ins = decode_at(va, root)
        if not (ins.mnemonic in (Mnemonic.CALL, Mnemonic.JMP) and ins.op0_kind == OpKind.MEMORY
                and ins.memory_base == Register.NONE and ins.memory_index == Register.NONE
                and ins.memory_displacement == slot):
            raise Refused(f"{imp}'s slot {slot:#010x} is held at {tva + m.start():#010x} by "
                          "something other than a call or jmp through it")
        (thunks if ins.mnemonic == Mnemonic.JMP else calls).append(va)
    for m in re.finditer(b"\xe8", code):
        at = m.start()
        if at + 5 > len(code):
            break
        target = (tva + at + 5 + int.from_bytes(code[at + 1:at + 5], "little", signed=True)) \
            & 0xFFFFFFFF
        if target in thunks and decode_at(tva + at, root).mnemonic == Mnemonic.CALL:
            calls.append(tva + at)
    return tuple(sorted(calls))


def debugstr_w(s: str) -> str:
    """A string as Wine's debugstr_w prints it, for the characters these literals use."""
    return 'L"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- the rules

@dataclass(frozen=True)
class Step:
    shows: str               # what the matched line shows, in plain words
    fn: str                  # "class:channel:function"
    rest: str = ""           # regex over the text after the function; see template()
    thread: str = MAIN
    at: str = "after"        # after | prev | next | count
    count: tuple | None = None  # bounds on matching lines on `thread` over the whole log;
                                # given, they are checked and the count is recorded
    counted_as: str = ""     # a name later steps' bounds may use for this step's count


@dataclass(frozen=True)
class Outcome:
    id: str
    imports: tuple[str, ...]  # dll!name as the runtime's import table names them
    outcome: str
    answer: str               # SUCCESS or FAILURE: the class of what native returned
    profile: tuple[str, ...]  # device-profile record kinds that carry the answer on a capture
    what: str
    runs: tuple[tuple[str, tuple[Step, ...]], ...]
    # Every call of the one import in the executable, when the outcome's claims
    # rest on there being no other: import_calls must find exactly these.
    sites: tuple[int, ...] = ()
    # (log, current_level, game_state) its stage trace's last row must hold.
    reaches: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class PreInit:
    imp: str
    sites: tuple[int, ...]    # instructions that reference the import's slot
    log: str
    steps: tuple[Step, ...]
    attribution: str
    literals: tuple[tuple[int, int], ...] = ()   # (push site, wide string pushed)


def template(pattern: str, ctx: dict[str, str], root: Path) -> str:
    """`<<GUID:NAME>>` a class or interface from IMAGE_GUIDS, `<<WSTR:0xVA>>` the
    image's wide literal at VA as Wine prints it, `<<name>>` an earlier capture."""
    def sub(m: re.Match) -> str:
        kind, arg = m.group(1), m.group(2)
        if kind == "GUID:":
            return re.escape("{" + IMAGE_GUIDS[arg][1] + "}")
        if kind == "WSTR:":
            return re.escape(debugstr_w(wide_at(int(arg, 16), root)))
        if arg not in ctx:
            raise Refused(f"rule uses <<{arg}>> before any step captured it")
        return re.escape(ctx[arg])
    return re.sub(r"<<([A-Z]+:)?([^>]+)>>", sub, pattern)


GUID_ANY = r"\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}"
NOT_SYSTEM = r"\{(?!6f1d2b6[01]-d5a0-11cf-bfc7-444553540000\})[0-9a-f-]{36}\}"

# DirectInput --------------------------------------------------------------

DI8_CREATE = Step(
    "the game's DirectInput8Create with its own module handle, DIRECTINPUT_VERSION 0x800 "
    "and IID_IDirectInput8A (0x0087355c), pushed at 0x00864d14-0x00864d24 and called at "
    "0x00864d33",
    "trace:dinput:DirectInput8Create",
    r"hinst 00400000, version 0x800, iid <<GUID:IID_IDirectInput8A>>, out \w+, outer 00000000\.")
DI8_THROUGH = Step(
    "CreateDevice(GUID_SysKeyboard) through the interface it got back (0x00863781): the "
    "device set-up runs only past the pointer test at 0x00864d42, and a failed call has "
    "cleared the pointer at 0x00864d3c",
    "trace:dinput:dinput8_a_CreateDevice",
    r"impl (?P<di>\w+), guid <<GUID:GUID_SysKeyboard>>, out \w+, outer 00000000\.")

# The WMI scan in IsXInputDevice (0x00864680) -------------------------------

WBEM_CREATE = Step(
    "CoCreateInstance(CLSID_WbemLocator, NULL, CLSCTX_INPROC_SERVER, IID_IWbemLocator) at "
    "0x008646db, the classes pushed from 0x008fff88 and 0x008fff98",
    "trace:ole:CoCreateInstance",
    r"<<GUID:CLSID_WbemLocator>>, 00000000, 0x1, <<GUID:IID_IWbemLocator>>, \w+\.")
WBEM_COINIT = Step(
    "on the game thread's line just before it, CoInitialize(NULL) at 0x008646bc, which Wine "
    "implements as CoInitializeEx(NULL, COINIT_APARTMENTTHREADED)",
    "trace:ole:CoInitializeEx", r"00000000, 0x2", at="prev")
WBEM_CONNECT = Step(
    "ConnectServer on the locator it got back (0x0086474a), reached only past 0x008646e3 and "
    "0x008646ed, which send a failed or null creation to the cleanup at 0x0086490d",
    "trace:wbemprox:wbem_locator_ConnectServer",
    r"(?P<locator>\w+), <<WSTR:0x0089F068>>, \(null\), \(null\), \(null\), 0, \(null\), "
    r"00000000, \w+\)")
WBEM_BLANKET = Step(
    "CoSetProxyBlanket(services, RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE, NULL, "
    "RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE, NULL, EOAC_NONE) at 0x0086476b",
    "trace:ole:CoSetProxyBlanket", r"(?P<services>\w+), 10, 0, 00000000, 3, 3, 00000000, 0\.")
WBEM_ENUM = Step(
    "CreateInstanceEnum(Win32_PNPEntity) on those services (0x00864787)",
    "trace:wbemprox:wbem_services_CreateInstanceEnum",
    r"<<services>>, <<WSTR:0x0089F088>>, 0, 00000000, \w+")
WBEM_ROWS = Step(
    "how many Win32_PNPEntity entities the host has; the entities themselves are host "
    "inventory and are never cited",
    "trace:wbemprox:fill_pnpentity", r"created (?P<wmi_pnp>\d+) rows")
WBEM_NEXT = Step(
    "Next(10000 ms, 20) on the enumerator, pushed at 0x008647a9 and 0x008647ab",
    "trace:wbemprox:enum_class_object_Next", r"(?P<enum>\w+), 10000, 20, \w+, \w+")
WBEM_GET = Step(
    "Get(DeviceID) once for every entity (0x008647e7)",
    "trace:wbemprox:class_object_Get", r"\w+, <<WSTR:0x0089F0A8>>, 0, \w+, 00000000, 00000000",
    count=("<<wmi_pnp>>", "<<wmi_pnp>>"))
WBEM_RELEASE = Step(
    "the cleanup releasing the enumerator (0x00864938)",
    "trace:wbemprox:enum_class_object_Release", r"destroying <<enum>>")
WBEM_RELEASE_LOC = Step(
    "the locator (0x0086494c)", "trace:wbemprox:wbem_locator_Release", r"destroying <<locator>>")
WBEM_RELEASE_SVC = Step(
    "and the services (0x00864960)", "trace:wbemprox:wbem_services_Release",
    r"destroying <<services>>")
WBEM_UNINIT = Step(
    "then, on the game thread's next line, CoUninitialize at 0x0086496c",
    "trace:ole:CoUninitialize", r"", at="next")
WBEM_ALLOC = Step(
    "SysAllocString of the namespace literal pushed at 0x008646f9 (Wine's SysAllocString is "
    "SysAllocStringLen of the string's length)",
    "trace:ole:SysAllocStringLen", r"<<WSTR:0x0089F068>>")
WBEM_ALLOC_CLASS = Step(
    "and of the class name pushed at 0x0086470c, on the game thread's next line",
    "trace:ole:SysAllocStringLen", r"<<WSTR:0x0089F088>>", at="next")
WBEM_ALLOC_PROP = Step(
    "and of the property name pushed at 0x0086471f, on the next",
    "trace:ole:SysAllocStringLen", r"<<WSTR:0x0089F0A8>>", at="next")

# XAudio2 ---------------------------------------------------------------------

XA2_COINIT = Step(
    "CoInitializeEx(NULL, COINIT_MULTITHREADED) at 0x007841d4, both arguments pushed as 0",
    "trace:ole:CoInitializeEx", r"00000000, 0")
XA2_CREATE = Step(
    "CoCreateInstance(CLSID_XAudio2 2.6, NULL, CLSCTX_INPROC_SERVER, IID_IXAudio2) at "
    "0x00784103, the classes pushed from 0x00902200 and 0x00902210",
    "trace:ole:CoCreateInstance",
    r"<<GUID:CLSID_XAudio2>>, 00000000, 0x1, <<GUID:IID_IXAudio2>>, \w+\.")
XA2_CREATED = Step(
    "Wine serving the class from xaudio2_6.dll",
    "trace:xaudio2:XAudio2CF_CreateInstance", r"Created XAudio version 26: (?P<engine>\w+)")
XA2_INIT = Step(
    "Initialize(0, XAUDIO2_DEFAULT_PROCESSOR) through the engine (0x0078411b), reached only "
    "past the jl at 0x0078410b",
    "trace:xaudio2:IXAudio2Impl_Initialize", r"<<engine>>, 0, 0xffffffff")

# DirectDraw ------------------------------------------------------------------

DD_LOADS = Step(
    "ddraw.dll loading on the game thread, which LoadLibraryA at 0x00865d4a asks for",
    "trace:loaddll:build_module", r'Loaded L"C:\\\\windows\\\\system32\\\\ddraw\.dll" at \w+: builtin')
DD_ENUM = Step(
    "DirectDrawEnumerateExA(callback 0x00865c20, context, DDENUM_ATTACHEDSECONDARYDEVICES) "
    "at 0x00865d96, so the je at 0x00865d54 was not taken",
    "trace:ddraw:DirectDrawEnumerateExA", r"callback 00865C20, context \w+, flags 0x1\.")
DD_DEFAULT = Step(
    "the default interface reported first",
    "trace:ddraw:DirectDrawEnumerateExA", r"Default interface: DirectDraw HAL")
DD_SECONDARY = Step(
    "the secondary devices, counted; each GUID is host inventory and is redacted",
    "trace:ddraw:ddraw_enumerate_secondary_devices", r"Interface \d+: " + GUID_ANY,
    count=(1, None))
DD_END = Step("the end of the enumeration", "trace:ddraw:DirectDrawEnumerateExA",
              r"End of enumeration")
DD_CREATE = Step(
    "DirectDrawCreate(the device the callback kept, &pDD, NULL) at 0x00865db8",
    "trace:ddraw:DirectDrawCreate", r"driver_guid " + GUID_ANY + r", ddraw \w+, outer 00000000\.")
DD_QI = Step(
    "QueryInterface(IID_IDirectDraw7) on the object it got back (0x00865dcd), whose pointer "
    "0x00865dba dereferences with no test of the HRESULT",
    "trace:ddraw:ddraw1_QueryInterface", r"iface \w+, riid <<GUID:IID_IDirectDraw7>>, object \w+\.")
DD_VIDMEM = Step(
    "GetAvailableVidMem through IDirectDraw7",
    "trace:ddraw:ddraw7_GetAvailableVidMem", r"iface \w+, caps \w+, total \w+, free 00000000\.")

# XInput ----------------------------------------------------------------------

XI_FIRST = Step(
    "XInputGetCapabilities(0, XINPUT_FLAG_GAMEPAD, &caps) at 0x00864e29, in the scan the "
    "input manager runs once DirectInput has succeeded (Wine's XInputGetCapabilities is "
    "XInputGetCapabilitiesEx(1, ...))",
    "trace:xinput:XInputGetCapabilitiesEx", r"unk 1, index 0, flags 0x1, capabilities \w+\.")

DSOUND_CREATE = Step(
    "DirectSoundCreate8(NULL, 0x01a51638, NULL), the out-pointer the game's own global",
    "trace:dsound:DirectSoundCreate8", r"\(\(null\),01A51638,00000000\)")

# D3DX ------------------------------------------------------------------------

D3DX_SITE = 0x00867225   # call 0x007a4aca, the thunk jmp [slot], in the routine at 0x008671b0


def d3dx_table(target: str, version: str, first: bool) -> tuple[Step, ...]:
    """One D3DXGetShaderConstantTable call and what Wine does with it, line by
    adjacent line on the game thread: forwarded to the Ex function, the CTAB
    comment found, and the table's header naming its creator, constants and
    target."""
    code = f"{target[:2]}_code"
    return (
        Step(f"the game's D3DXGetShaderConstantTable (0x{D3DX_SITE:08x}) for a {target} shader, "
             "which Wine forwards to D3DXGetShaderConstantTableEx"
             + ("; every such call on the thread counted" if first else ""),
             "trace:d3dx:D3DXGetShaderConstantTable",
             rf"\((?P<{code}>\w+), (?P<out>\w+)\): Forwarded to D3DXGetShaderConstantTableEx",
             **({"count": (1, None), "counted_as": "calls"} if first else {})),
        Step("on the game thread's next line, the Ex function with no flags on the same "
             "bytecode and out-pointer", "trace:d3dx:D3DXGetShaderConstantTableEx",
             rf"byte_code <<{code}>>, flags 0, constant_table <<out>>\.", at="next"),
        Step("then a search of that bytecode for the CTAB comment (fourcc 0x42415443)",
             "trace:d3dx:D3DXFindShaderComment",
             rf"byte_code <<{code}>>, fourcc 0x42415443, data \w+, size \w+\.", at="next"),
        Step("which finds it", "trace:d3dx:D3DXFindShaderComment",
             r"Returning comment data at \w+ with size \d+", at="next"),
        Step(f"and the table's header: its creator, constant count and target {target}",
             "trace:d3dx:D3DXGetShaderConstantTableEx",
             rf'Creator "[^"]+", Version {version}, Constants \d+, Target "{target}"\.',
             at="next"),
    )


D3DX_STEPS = d3dx_table("vs_3_0", "0xfffe0300", True) + d3dx_table("ps_3_0", "0xffff0300", False) + (
    Step("a table header for every call on the game thread: each found its shader's CTAB "
         "comment", "trace:d3dx:D3DXGetShaderConstantTableEx",
         r'Creator "[^"]+", Version 0x[0-9a-f]+, Constants \d+, Target "[pv]s_\d_\d"\.',
         at="count", count=("<<calls>>", "<<calls>>")),
    Step("no warn, err or fixme line from D3DXGetShaderConstantTable or its Ex function on any "
         "thread", "[!t]*:d3dx:D3DXGetShaderConstantTable*", r".*", thread=ANY, at="count",
         count=(0, 0)),
    Step("no line from any ID3DXConstantTable method on any thread: Wine's d3dx9 prints each "
         "method under its own ID3DXConstantTableImpl_ name, and none is called on any table, "
         "not even Release", "*:d3dx:ID3DXConstantTableImpl_*", r".*", thread=ANY, at="count",
         count=(0, 0)),
)


OUTCOMES: tuple[Outcome, ...] = (
    Outcome(
        "dinput8-create", ("DINPUT8.dll!DirectInput8Create",), "succeeds", SUCCESS,
        ("dinput8_create",),
        "DirectInput8Create succeeds: the game calls through the interface it returns, in "
        "both traced boots that trace DirectInput.",
        (("native-outcomes-v1a", (DI8_CREATE, DI8_THROUGH)),
         ("dinput-trace-v1a", (DI8_CREATE, DI8_THROUGH)))),
    Outcome(
        "dinput8-devices", ("DINPUT8.dll!DirectInput8Create",), "keyboard-and-mouse-polled",
        SUCCESS, ("dinput8_syskeyboard", "dinput8_sysmouse"),
        "The system keyboard and mouse are created, given their data formats and cooperative "
        "level, acquired once each, and read with GetDeviceState at least once per tick. "
        "Acquire is called exactly twice in the run, so no GetDeviceState failed: a failed "
        "poll retries Acquire (0x00863894, 0x008633b0). Whether the two system devices exist "
        "is part of the capture's device profile.",
        (("native-outcomes-v1a", (
            Step("CreateDevice(GUID_SysKeyboard) (0x00863781)", "trace:dinput:dinput8_a_CreateDevice",
                 r"impl (?P<di>\w+), guid <<GUID:GUID_SysKeyboard>>, out \w+, outer 00000000\."),
            Step("SetDataFormat(c_dfDIKeyboard, 0x00875d6c) on the keyboard (0x0086379e)",
                 "trace:dinput:dinput_device_a_SetDataFormat", r"impl (?P<kbd>\w+), format 00875D6C\."),
            Step("SetCooperativeLevel(hwnd, DISCL_FOREGROUND | DISCL_NONEXCLUSIVE) (0x008637b2)",
                 "trace:dinput:dinput_device_a_SetCooperativeLevel",
                 r"impl <<kbd>>, window \w+, flags 0x6\."),
            Step("Acquire (0x008637c4)", "trace:dinput:dinput_device_a_Acquire", r"impl <<kbd>>\."),
            Step("GetDeviceState(256) on the keyboard (0x00863810 at init, 0x00863885 per poll), "
                 "at least once per tick", "trace:dinput:dinput_device_a_GetDeviceState",
                 r"impl <<kbd>>, count 0x100, data \w+\.", count=(TICKS, None)),
            Step("CreateDevice(GUID_SysMouse) on the same interface (0x00863208)",
                 "trace:dinput:dinput8_a_CreateDevice",
                 r"impl <<di>>, guid <<GUID:GUID_SysMouse>>, out \w+, outer 00000000\."),
            Step("SetDataFormat(c_dfDIMouse, 0x00875f74) on the mouse (0x00863222)",
                 "trace:dinput:dinput_device_a_SetDataFormat", r"impl (?P<mouse>\w+), format 00875F74\."),
            Step("SetCooperativeLevel(hwnd, DISCL_FOREGROUND | DISCL_NONEXCLUSIVE) (0x00863236)",
                 "trace:dinput:dinput_device_a_SetCooperativeLevel",
                 r"impl <<mouse>>, window \w+, flags 0x6\."),
            Step("Acquire (0x00863244)", "trace:dinput:dinput_device_a_Acquire", r"impl <<mouse>>\."),
            Step("GetDeviceState(16) on the mouse (0x00863282 at init, 0x008633a1 per poll), at "
                 "least once per tick", "trace:dinput:dinput_device_a_GetDeviceState",
                 r"impl <<mouse>>, count 0x10, data \w+\.", count=(TICKS, None)),
            Step("Acquire exactly twice in the whole run, so no poll failed",
                 "trace:dinput:dinput_device_a_Acquire", r"impl \w+\.", at="count", count=(2, 2)),
        )),)),
    Outcome(
        "dinput8-enumeration", ("DINPUT8.dll!DirectInput8Create",), "one-device-created-and-released",
        SUCCESS, ("dinput8_gamectrl", "dinput8_enum"),
        "EnumDevices(DI8DEVCLASS_GAMECTRL, DIEDFL_ATTACHEDONLY) enumerates one device on this "
        "host, the user's keyboard-and-mouse receiver, which Wine classes as a HID "
        "supplemental device. The callback (0x00864c90) creates it, asks IsXInputDevice "
        "(0x00864680), gets false, and unacquires and releases it. Which devices enumerate is "
        "the capture's device profile.",
        (("native-outcomes-v1a", (
            Step("EnumDevices(DI8DEVCLASS_GAMECTRL, callback 0x00864c90, DIEDFL_ATTACHEDONLY) "
                 "(0x00864e19)", "trace:dinput:dinput8_a_EnumDevices",
                 r"impl (?P<di>\w+), type 0x4, callback 00864C90, ref \w+, flags 0x1\."),
            # The registry cache's "Created instance ... path L\"\"" line is not the
            # device: load_registry_product_instances emits one for every cached
            # instance, attached or not. The device the enumeration reports is the
            # one Wine opened, whose dwDevType and "Reusing instance" line (the
            # instance with its path) are adjacent on the game thread (2026-09-23
            # review, v1a:14776-14777).
            Step("the device Wine opened for the enumeration, by its dwDevType",
                 "trace:dinput:hid_joystick_device_try_open",
                 r"detected device type (?P<dev_type>0x[0-9a-f]+)"),
            Step("on the game thread's next line, that device's cache entry, with its instance "
                 "GUID; its VID:PID, instance GUID and path are host inventory and are "
                 "redacted", "trace:dinput:insert_cache_entry",
                 r"Reusing instance product: [0-9a-f]{4}:[0-9a-f]{4}, instance: "
                 r"(?P<instance>" + GUID_ANY + r"), path L\"(?:[^\"\\]|\\.)+\", joy_id \d+",
                 at="next"),
            Step("the callback's CreateDevice(lpddi->guidInstance) (0x00864cae) on that device",
                 "trace:dinput:dinput8_a_CreateDevice",
                 r"impl <<di>>, guid <<instance>>, out \w+, outer 00000000\."),
            Step("exactly one CreateDevice on a device other than the system keyboard and mouse "
                 "in the whole run", "trace:dinput:dinput8_a_CreateDevice",
                 r"impl <<di>>, guid " + NOT_SYSTEM + r", out \w+, outer 00000000\.",
                 at="count", count=(1, 1)),
            Step("IsXInputDevice answered false, so the callback unacquires the device (0x00864cce)",
                 "trace:dinput:dinput_device_a_Unacquire", r"impl (?P<dev>\w+)\."),
            Step("and releases it (0x00864cda)", "trace:dinput:dinput_device_a_Release",
                 r"impl <<dev>>\."),
        )),)),
    Outcome(
        "wbem-scan",
        ("ole32.dll!CoInitialize", "ole32.dll!CoCreateInstance", "OLEAUT32.dll!Ordinal#2",
         "ole32.dll!CoSetProxyBlanket", "OLEAUT32.dll!Ordinal#6", "ole32.dll!CoUninitialize"),
        # wmi_coinit is not here: the profile records it for information only, and the
        # runtime's CoInitialize answers from its own apartment depth (main thread's
        # contract of 2026-09-23; native's call succeeded, since its CoUninitialize at
        # v1a:15240 is reachable only past the setge at 0x008646d6).
        "succeeds", SUCCESS,
        ("dinput8_gamectrl", "wmi_locator", "wmi_connect", "wmi_blanket", "wmi_enum",
         "wmi_next", "wmi_pnp", "wmi_entities"),
        "IsXInputDevice (0x00864680), called by the enumeration callback for the one enumerated "
        "device, reaches WMI: the locator is created, connected to root\\cimv2, given its proxy "
        "blanket, and asked for every Win32_PNPEntity entity's DeviceID. No DeviceID pairs IG_ "
        "with the enumerated device's VID/PID, so it answers false and the callback releases "
        "the device (outcome dinput8-enumeration). SysFreeString is not traced by Wine; the "
        "enumerator's release shows the path through 0x008648ee, which frees the three "
        "strings. The entities are the host's, so this whole path is the capture's device "
        "profile.",
        (("native-outcomes-v1a", (
            WBEM_CREATE, WBEM_COINIT, WBEM_ALLOC, WBEM_ALLOC_CLASS, WBEM_ALLOC_PROP, WBEM_CONNECT,
            WBEM_BLANKET, WBEM_ENUM, WBEM_ROWS, WBEM_NEXT, WBEM_GET, WBEM_RELEASE,
            WBEM_RELEASE_LOC, WBEM_RELEASE_SVC, WBEM_UNINIT)),)),
    Outcome(
        "cocreateinstance", ("ole32.dll!CoCreateInstance",), "succeeds", SUCCESS,
        ("dinput8_gamectrl", "wmi_locator", "xaudio2_master"),
        "The game asks CoCreateInstance for two classes and gets both: the WMI locator from "
        "IsXInputDevice, then the XAudio2 2.6 engine. Each is shown by the first call through "
        "the object it returned. The locator is asked for only because a "
        "game-controller-class device enumerated, which is the capture's device profile, and "
        "the profile records the engine's mastering voice.",
        (("native-outcomes-v1a", (WBEM_CREATE, WBEM_CONNECT, XA2_CREATE, XA2_CREATED,
                                  XA2_INIT)),)),
    Outcome(
        "xaudio2-stream", ("ole32.dll!CoInitializeEx", "ole32.dll!CoCreateInstance"),
        "succeeds-and-streams", SUCCESS, ("xaudio2_master",),
        "The XAudio2 2.6 engine is created and initialised, gets a 48000 Hz mastering voice "
        "and one source voice with no callback, and the source voice is started; a thread of "
        "the game's own then polls GetState and submits buffers to that voice for the rest of "
        "the run. DirectSound serves the game's other sounds at the same time "
        "(outcome dsound-create). The mastering voice's details are the host's speakers: the "
        "game reads them with GetVoiceDetails (0x0078421d) into 0x01af0ee0 and chooses its "
        "output matrix by the channel count (0x007843a6), so the capture's device profile "
        "records them.",
        (("native-outcomes-v1a", (
            XA2_COINIT, XA2_CREATE, XA2_CREATED, XA2_INIT,
            Step("CreateMasteringVoice(&0x01af0edc, 0 channels, 48000 Hz, 0, device 0, NULL) "
                 "at 0x00784208", "trace:xaudio2:IXAudio2Impl_CreateMasteringVoice",
                 r"\(<<engine>>\)->\(01AF0EDC, 0, 48000, 0x0, 00000000\)"),
            Step("CreateSourceVoice with no callback, sends or effects",
                 "trace:xaudio2:IXAudio2Impl_CreateSourceVoice",
                 r"\(<<engine>>\)->\(\w+, \w+, 0x0, 2\.000000, 00000000, 00000000, 00000000\)"),
            Step("the source voice it created", "trace:xaudio2:IXAudio2Impl_CreateSourceVoice",
                 r"Created source voice: (?P<voice>\w+)", at="next"),
            Step("SetOutputMatrix on it", "trace:xaudio2:XA2SRC_SetOutputMatrix", r"<<voice>>, .*"),
            Step("the first SubmitSourceBuffer, on the game thread",
                 "trace:xaudio2:XA2SRC_SubmitSourceBuffer", r"<<voice>>, \w+, 00000000"),
            Step("Start", "trace:xaudio2:XA2SRC_Start", r"<<voice>>, 0x0, 0x0"),
            Step("GetState on the voice from the game's streaming thread, counted",
                 "trace:xaudio2:XA2SRC_GetState", r"<<voice>>, \w+, 0x0", thread=STREAM,
                 count=(1, None)),
            Step("SubmitSourceBuffer on the voice from the same thread, counted",
                 "trace:xaudio2:XA2SRC_SubmitSourceBuffer", r"<<voice>>, \w+, 00000000",
                 thread=STREAM, count=(1, None)),
        )),)),
    Outcome(
        "ddraw-enumeration",
        ("KERNEL32.dll!LoadLibraryA", "KERNEL32.dll!GetProcAddress",
         "DDRAW!DirectDrawEnumerateExA", "DDRAW!DirectDrawCreate"),
        "loads", SUCCESS,
        ("ddraw_device", "ddraw_enum", "ddraw_create", "ddraw_query", "ddraw_vidmem"),
        "ddraw.dll is present and loads; DirectDrawEnumerateExA reports the default interface "
        "and this host's secondary devices to the guest's callback, and DirectDrawCreate on "
        "the device it kept returns an object the guest queries for IDirectDraw7, in both "
        "traced boots that trace DirectDraw. The je at 0x00865d54, which skips all of it when "
        "the module is absent, is not taken. The devices are the capture's device profile.",
        tuple((key, (DD_LOADS, DD_ENUM, DD_DEFAULT, DD_SECONDARY, DD_END, DD_CREATE, DD_QI,
                     DD_VIDMEM)) for key in ("native-outcomes-v1a", "native-outcomes-v1b"))),
    Outcome(
        "xinput-not-connected",
        ("XINPUT1_3.dll!Ordinal#4", "XINPUT1_3.dll!Ordinal#2", "XINPUT1_3.dll!Ordinal#3"),
        "not-connected", FAILURE, ("xinput_caps",),
        "Every XInputGetCapabilities call answers 'not connected'. XInputGetState "
        "(0x00863d8c, 0x00863e19) and XInputSetState (0x0086396b, 0x008639c2) are called only "
        "from methods of the pad object (vtable 0x008fffe0, slots 7, 5, 17 and 16, and nothing "
        "else references those methods), which the game builds only at 0x00864e53, past the "
        "test at 0x00864e2e and the jne at 0x00864e30, when the start-up scan's "
        "XInputGetCapabilities returns 0; the constructor at 0x00863e70 that also writes "
        "that vtable is referenced by nothing. The traced run calls neither on any thread, so "
        "none of the start-up scan's calls returned 0. The rescan's 0 answer "
        "(0x00864a2c/0x00864a2e) builds only a 12-byte object with vtable 0x008fff54 "
        "(0x00864a44), whose methods call neither, so this absence does not show the rescan's "
        "answers; the capture's device profile records every slot's answer (xinput_caps), "
        "and its readers accept only 1167. No pad is attached on this host.",
        (("native-outcomes-v1a", (
            XI_FIRST,
            Step("XInputGetCapabilities for slots 0-3 on the game thread, counted",
                 "trace:xinput:XInputGetCapabilitiesEx",
                 r"unk 1, index [0-3], flags 0x1, capabilities \w+\.", at="count", count=(4, None)),
            Step("no XInputGetState on any thread", "trace:xinput:XInputGetState", r".*",
                 thread=ANY, at="count", count=(0, 0)),
            Step("no XInputSetState on any thread", "trace:xinput:XInputSetState", r".*",
                 thread=ANY, at="count", count=(0, 0)),
        )),)),
    Outcome(
        "dsound-create", ("DSOUND.dll!Ordinal#11",), "succeeds", SUCCESS, (),
        "DirectSoundCreate8 succeeds, and the game sets DSSCL_PRIORITY and plays a buffer "
        "through the object it got back, alongside the XAudio2 stream (outcome xaudio2-stream).",
        (("native-outcomes-v1b", (
            DSOUND_CREATE,
            Step("SetCooperativeLevel(hwnd, DSSCL_PRIORITY) on the object it got back",
                 "trace:dsound:IDirectSound8Impl_SetCooperativeLevel",
                 r"\(\w+,\w+,DSSCL_PRIORITY\)"),
            Step("Play on a secondary buffer, counted", "trace:dsound:IDirectSoundBufferImpl_Play",
                 r".*", count=(1, None)),
        )),)),
    Outcome(
        "d3dx-constant-table", ("d3dx9_42.dll!D3DXGetShaderConstantTable",),
        "succeeds-no-method-called", SUCCESS, (),
        "D3DXGetShaderConstantTable succeeds every time, and the game calls nothing on the "
        "tables it gets. The import's one call is at 0x00867225, in the routine at 0x008671b0, "
        "which its two callers (0x0086632c, 0x0086638c) hand the address of a slot they have "
        "zeroed, and store what the slot then holds at [object+50h] (0x00866338, 0x00866398) "
        "without a test. "
        "Wine forwards every call to D3DXGetShaderConstantTableEx, which finds the shader's "
        "CTAB comment and prints the table's header (creator, constant count, target: vs_3_0 "
        "and ps_3_0 shaders of the HLSL compiler), one header per call and no warning. No "
        "ID3DXConstantTable method is called on any thread: Wine's d3dx9 prints each method "
        "under its ID3DXConstantTableImpl_ name, and neither log has one -- no "
        "GetConstantByName, no Set*, not even Release -- in native-outcomes-v1c to the tick "
        "probe's limit (logs[].ticks), and in native-outcomes-v1d through boot, the title and "
        "on into the City Escape attract demo's gameplay, its stage trace ending in level "
        "0x000d in game state 0x0010 (logs[].stage_trace, and this outcome's reaches). That "
        "Wine's methods all print their names is a property of the "
        "Wine build the captures ran, which this receipt does not pin. The two methods that "
        "use a stored table, 0x00867240 and 0x00867290, read it from [object+50h] and call "
        "through it with no null test. The answer is reflection over the game's own shader "
        "bytecode, not the host's devices, so no device-profile record carries it.",
        tuple((key, D3DX_STEPS) for key in ("native-outcomes-v1c", "native-outcomes-v1d")),
        sites=(D3DX_SITE,),
        reaches=(("native-outcomes-v1d", "0x000d", "0x0010"),)),
)

PRE_INIT: tuple[PreInit, ...] = (
    PreInit("DINPUT8.dll!DirectInput8Create", (0x00864D33,), "native-outcomes-v1a", (DI8_CREATE,),
            "Wine traces the export under its own name, with the arguments the game pushes."),
    PreInit("ole32.dll!CoInitialize", (0x008646BC,), "native-outcomes-v1a", (WBEM_CREATE, WBEM_COINIT),
            "Wine's CoInitialize(NULL) is CoInitializeEx(NULL, COINIT_APARTMENTTHREADED), and the "
            "game thread's line just before the WMI locator's creation is that call; 0x008646bc "
            "is the only ole32 call between the function's entry and 0x008646db."),
    PreInit("ole32.dll!CoCreateInstance", (0x008646DB, 0x00784103), "native-outcomes-v1a",
            (WBEM_CREATE, XA2_CREATE),
            "Wine traces the call under its own name, with the classes the game pushes."),
    PreInit("OLEAUT32.dll!Ordinal#2", (0x008646F3,), "native-outcomes-v1a",
            (WBEM_ALLOC, WBEM_ALLOC_CLASS, WBEM_ALLOC_PROP),
            "Wine's SysAllocString is SysAllocStringLen(str, lstrlenW(str)); the three strings "
            "are the image's own literals, pushed at the three sites decoded beside them and "
            "passed to the function loaded from the slot at 0x008646f3.",
            ((0x008646F9, 0x0089F068), (0x0086470C, 0x0089F088), (0x0086471F, 0x0089F0A8))),
    PreInit("ole32.dll!CoSetProxyBlanket", (0x0086476B,), "native-outcomes-v1a", (WBEM_BLANKET,),
            "Wine traces the call under its own name, with the literals the game pushes."),
    PreInit("OLEAUT32.dll!Ordinal#6", (0x008648F2,), "native-outcomes-v1a",
            (WBEM_BLANKET, WBEM_ENUM, WBEM_NEXT, WBEM_RELEASE),
            "Wine does not trace SysFreeString. The enumerator exists only once "
            "CreateInstanceEnum (0x00864787) has written it, every path from that call to the "
            "enumerator's release at 0x00864938 passes 0x008648ee, and the first call there "
            "(0x008648f9) is SysFreeString, loaded from the slot at 0x008648f2; the jumps to "
            "0x0086490d that skip it all precede the call."),
    PreInit("ole32.dll!CoUninitialize", (0x0086496C,), "native-outcomes-v1a",
            (WBEM_BLANKET, WBEM_RELEASE_SVC, WBEM_UNINIT),
            "Wine traces the call under its own name, on the game thread's line right after the "
            "cleanup's last release."),
    PreInit("XINPUT1_3.dll!Ordinal#4", (0x00864E29,), "native-outcomes-v1a", (XI_FIRST,),
            "Wine's XInputGetCapabilities is XInputGetCapabilitiesEx(1, index, flags, caps)."),
    PreInit("ole32.dll!CoInitializeEx", (0x007841D4,), "native-outcomes-v1a", (XA2_COINIT,),
            "Wine traces the call under its own name, with the two zeros the game pushes."),
    PreInit("DSOUND.dll!Ordinal#11", (0x0086126C, 0x0086128A), "native-outcomes-v1b",
            (DSOUND_CREATE,),
            "Wine traces DSOUND's ordinal 11 as DirectSoundCreate8, with the game's out-pointer."),
)


# ---------------------------------------------------------------- matching

def redact(text: str) -> str:
    """A line with every GUID outside KEPT_GUIDS, every wide literal shaped like a
    device ID, and every DirectInput `product: VID:PID`, replaced."""
    text = GUID.sub(lambda m: m.group(0) if m.group(1).lower() in KEPT_GUIDS else REDACTED, text)
    text = PRODUCT.sub("product: <redacted>", text)
    return WIDE.sub(lambda m: 'L"<redacted>"' if DEVICE_ID.search(m.group(1)) else m.group(0),
                    text)


def evidence_item(log: Log, n: int, shows: str) -> dict:
    raw = log.lines[n - 1]
    item = {"log": log.rel, "line": n, "text": redact(raw), "shows": shows,
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
    if item["text"] != raw:
        item["redacted"] = True
    return item


def bound(b, ctx: dict[str, str], ticks: int | None) -> int | None:
    if b is None or isinstance(b, int):
        return b
    if b == TICKS:
        if ticks is None:
            raise Refused("the capture records no checked tick capture, so 'every tick' has "
                          "no count to be held to")
        return ticks
    m = re.fullmatch(r"<<(\w+)>>", str(b))
    if m and m.group(1) in ctx:
        return int(ctx[m.group(1)])
    raise Refused(f"count bound {b!r} names nothing captured")


def threads_for(step: Step, log: Log, roles: dict[str, str]) -> list[str]:
    if step.thread == ANY:
        return sorted(log.by_tid)
    if step.thread in roles:
        return [roles[step.thread]]
    raise Refused(f"{log.rel}: no thread plays the role {step.thread!r}")


def resolve_stream(log: Log, main: str, ctx: dict[str, str]) -> str:
    """The one thread other than main that calls the source voice main created."""
    voice = ctx.get("voice")
    if not voice:
        raise Refused(f"{log.rel}: the streaming thread is asked for before the voice is known")
    tids = sorted({tid for (tid, fn), ns in log.by_fn.items() if tid != main
                   and fn == "trace:xaudio2:XA2SRC_GetState"
                   and any(log.rest(n).startswith(voice + ",") for n in ns)})
    if len(tids) != 1:
        raise Refused(f"{log.rel}: {len(tids)} threads other than the game's main thread poll "
                      f"the source voice {voice}, not one: {tids}")
    return tids[0]


def run_steps(log: Log, steps: tuple[Step, ...], main: str, ticks: int | None,
              root: Path = ROOT) -> tuple[list[dict], list[dict], dict[str, str], dict[str, str]]:
    """Match `steps` in order. Returns (evidence, counts, captures, thread roles)."""
    ctx: dict[str, str] = {}
    roles = {MAIN: main}
    evidence: list[dict] = []
    counts: list[dict] = []
    cursor, last = 0, 0
    for step in steps:
        chan = step.fn.split(":")[1]
        if chan not in log.traced:
            raise Refused(f"{log.rel}: the {chan} channel is not traced, so '{step.shows}' "
                          "cannot be shown or ruled out from it")
        if step.thread == STREAM and STREAM not in roles:
            roles[STREAM] = resolve_stream(log, main, ctx)
        rx = re.compile(template(step.rest, ctx, root) + r"\Z")
        tids = set(threads_for(step, log, roles))
        hits = sorted(n for (t, fn), ns in log.by_fn.items()
                      if t in tids and fnmatch.fnmatchcase(fn, step.fn)
                      for n in ns if rx.match(log.rest(n)))
        if step.counted_as:
            ctx[step.counted_as] = str(len(hits))
        if step.at == "count":
            pass
        elif step.at in ("prev", "next"):
            seq = log.by_tid[log.tid(last)] if last else []
            i = log.pos.get(last, -1) + (1 if step.at == "next" else -1)
            n = seq[i] if last and 0 <= i < len(seq) else None
            if n is None or n not in hits:
                where = "after" if step.at == "next" else "before"
                raise Refused(f"{log.rel}: the line {where} line {last} on its thread is not "
                              f"{step.fn} matching {step.rest!r} ({step.shows})")
            m = rx.match(log.rest(n))
            ctx.update({k: v for k, v in m.groupdict().items() if v is not None})
            evidence.append(evidence_item(log, n, step.shows))
            last = n
            cursor = max(cursor, n)
        else:
            after = [n for n in hits if n > cursor]
            if not after:
                raise Refused(f"{log.rel}: no {step.fn} line matching {step.rest!r} after line "
                              f"{cursor} on {', '.join(sorted(tids))} ({step.shows})")
            n = after[0]
            m = rx.match(log.rest(n))
            ctx.update({k: v for k, v in m.groupdict().items() if v is not None})
            evidence.append(evidence_item(log, n, step.shows))
            last = cursor = n
        # Bounds hold over the whole log on the step's threads, not only after the cursor.
        lo, hi = (bound(b, ctx, ticks) for b in (step.count or (1, None)))
        if (lo is not None and len(hits) < lo) or (hi is not None and len(hits) > hi):
            raise Refused(f"{log.rel}: {len(hits)} {step.fn} lines match {step.rest!r} on "
                          f"{step.thread}, outside [{lo}, {hi}] ({step.shows})")
        if step.count is not None:
            counts.append({"log": log.rel, "fn": step.fn, "rule": step.rest,
                           "thread": step.thread, "matches": len(hits), "bounds": [lo, hi],
                           "shows": step.shows})
    return evidence, counts, ctx, roles


# ---------------------------------------------------------------- the receipt

def exe_pin(root: Path = ROOT) -> str:
    return json.loads((root / "tools" / "pins.json").read_text())["executables"][EXE_NAME]["sha256"]


def import_names(root: Path = ROOT) -> tuple[set[str], set[str]]:
    """IAT imports as the runtime's table names them, and the runtime's dynamic names."""
    import native_imports as NI
    import refusal_audit as RA
    iat = set(NI.slots(exe_path(root)).values())
    dyn = {f"{r['dll']}!{r['name']}" for r in
           RA.table((root / "runtime" / "sa2_imports.c").read_text().split("\n"),
                    "static const dyn_row g_dyn_table[]")}
    return iat, dyn


def profile_record_kinds() -> frozenset[str]:
    """The device profile's record kinds, from the reader that implements
    runtime/sa2_devprofile.h's grammar; `end` and `error` carry no answer."""
    import device_profile as DP
    return frozenset(DP.RECORDS) - {"end", "error"}


def verify_image(root: Path = ROOT) -> None:
    for name, (va, want) in IMAGE_GUIDS.items():
        got = guid_at(va, root)
        if got != want:
            raise Refused(f"{name} at {va:#010x} is {got} in the image, not {want}")


def build(root: Path = ROOT) -> dict:
    """The receipt, recomputed from the logs, their receipts and the image."""
    pin = exe_pin(root)
    if sha256_file(exe_path(root)) != pin:
        raise Refused(f"private/exe/{EXE_NAME} does not hash to its pin in tools/pins.json")
    verify_image(root)
    iat, dyn = import_names(root)

    logs, parsed, meta, stages = [], {}, {}, {}
    hid_lists = {}
    for key in LOGS:
        log = read_log(key, root)
        rrel, rec, rsha = capture_receipt(key, root)
        if (rec.get("inputs") or {}).get("executable_sha256") != pin:
            raise Refused(f"{rrel} ran an executable other than the one tools/pins.json pins")
        session = rec.get("session") or {}
        main = main_thread(log)
        if "d3dx" in log.traced and d3dx_callers(log) - {main}:
            raise Refused(f"{log.rel}: D3DXGetShaderConstantTable is called on "
                          f"{sorted(d3dx_callers(log) - {main})} as well as the main thread "
                          f"{main}, so the thread that calls it is not the game's by that alone")
        init = mod_init(log, main, rec)
        ticks = ticks_of(rec)
        stage = stage_trace(key, rec, root)
        wd = session.get("winedebug")
        if wd is not None:
            named = {c.lstrip("+") for c in wd.split(",") if c.startswith("+")}
            extra = sorted(log.traced - named)
            if extra:
                raise Refused(f"{log.rel} traces {extra}, which its receipt's WINEDEBUG "
                              f"{wd!r} does not name: the log is not that run's")
        hid = session.get("host_hid_devices")
        if hid is not None:
            hid_lists[key] = list(hid)
        parsed[key] = log
        meta[key] = (main, init, ticks)
        stages[key] = stage
        logs.append({
            "key": key, "log": log.rel, "sha256": log.sha256, "lines": len(log.lines),
            "receipt": rrel, "receipt_sha256": rsha,
            "executable_sha256": rec["inputs"]["executable_sha256"],
            "winedebug": wd if wd is not None else "not recorded",
            "trace_channels": sorted(log.traced),
            "main_thread": main, "ticks": ticks if ticks is not None else "not recorded",
            "stage_trace": stage if stage is not None else "not recorded",
            "mod_init": None if init is None else evidence_item(
                log, init, "the Mod Loader's Init: an enabled mod's DLL loading on the main "
                           "thread"),
            "live_desktop_session": session.get("live_desktop_session"),
            "host_hid_devices": ({"count": len(hid), "sha256": hid_digest(hid)}
                                 if hid is not None else "not recorded"),
        })

    outcomes, workers = [], {}
    captured: dict[str, dict[str, str]] = {}
    records = profile_record_kinds()
    for oc in OUTCOMES:
        for imp in oc.imports:
            if imp not in iat and imp not in dyn:
                raise Refused(f"outcome {oc.id} names {imp}, which is neither an import slot of "
                              "the executable nor a name in the runtime's dynamic table")
        if oc.answer not in ANSWERS:
            raise Refused(f"outcome {oc.id}'s answer class is {oc.answer!r}, not one of "
                          f"{', '.join(ANSWERS)}")
        unknown = [r for r in oc.profile if r not in records]
        if unknown or len(set(oc.profile)) != len(oc.profile):
            raise Refused(f"outcome {oc.id} names device-profile records {list(oc.profile)}; "
                          f"{unknown or 'one twice'} is not one record kind of the grammar "
                          "tools/s4/device_profile.py reads")
        ev, cn = [], []
        for key, steps in oc.runs:
            main, _, ticks = meta[key]
            e, c, ctx, roles = run_steps(parsed[key], steps, main, ticks, root)
            ev += e
            cn += c
            captured.setdefault(oc.id, {}).update(ctx)
            for role, tid in roles.items():
                if role != MAIN:
                    workers.setdefault(key, {})[role] = tid
        entry = {"id": oc.id, "imports": list(oc.imports), "outcome": oc.outcome,
                 "answer": oc.answer, "profile_records": list(oc.profile),
                 "host_dependent": bool(oc.profile), "what": oc.what,
                 "evidence": ev, "counts": cn}
        if oc.sites:
            (imp,) = oc.imports
            found = import_calls(imp, root)
            if found != tuple(sorted(oc.sites)):
                raise Refused(f"outcome {oc.id} rests on {imp} being called only at "
                              f"{', '.join(f'{s:#010x}' for s in oc.sites)}, and the executable "
                              f"calls it at {', '.join(f'{s:#010x}' for s in found) or 'none'}")
            entry["call_sites"] = [f"{s:#010x}" for s in found]
        for key, level, state in oc.reaches:
            got = stages.get(key)
            if key not in dict(oc.runs) or got is None:
                raise Refused(f"outcome {oc.id} holds {key} to its stage trace, and "
                              f"{'the outcome does not run on it' if got else 'it has none'}")
            last = got["last"]
            if (last["current_level"], last["game_state"]) != (level, state):
                raise Refused(f"outcome {oc.id}: {key}'s stage trace ends in level "
                              f"{last['current_level']}, state {last['game_state']}, not level "
                              f"{level} in state {state}")
            entry.setdefault("reaches", []).append(
                {"log": parsed[key].rel, "current_level": level, "game_state": state,
                 "stage_trace_sha256": got["sha256"]})
        outcomes.append(entry)
    for entry in logs:
        entry["workers"] = workers.get(entry["key"], {})

    import native_imports as NI
    slots = {v: k for k, v in NI.slots(exe_path(root)).items()}
    pre = []
    for p in PRE_INIT:
        if p.imp not in iat:
            raise Refused(f"pre-Init import {p.imp} is not an import slot of the executable")
        for site in p.sites:
            got = slot_of_site(site, root)
            if got != slots[p.imp]:
                raise Refused(f"{site:#010x} references slot {got and hex(got)}, not {p.imp}'s "
                              f"{slots[p.imp]:#010x}")
        for site, va in p.literals:
            if push_imm_at(site, root) != va:
                raise Refused(f"{site:#010x} does not push {va:#010x}")
        main, init, ticks = meta[p.log]
        ev, _, _, _ = run_steps(parsed[p.log], p.steps, main, ticks, root)
        if init is None or any(e["line"] >= init for e in ev):
            raise Refused(f"{p.imp}'s evidence in {parsed[p.log].rel} is not before the Mod "
                          f"Loader's Init (line {init})")
        pre.append({"import": p.imp, "call_sites": [f"{s:#010x}" for s in p.sites],
                    "literals": [{"push": f"{s:#010x}", "string": wide_at(va, root)}
                                 for s, va in p.literals],
                    "attribution": p.attribution, "evidence": ev})
    pre.sort(key=lambda r: r["import"])

    if len({hid_digest(v) for v in hid_lists.values()}) > 1:
        raise Refused(f"the traced boots recorded different HID devices ({sorted(hid_lists)}), "
                      "so they are not one host's answers")
    hid = next(iter(hid_lists.values()), None)
    enum = captured["dinput8-enumeration"]

    def counted(oid: str, fn: str) -> int:
        return next(c["matches"] for o in outcomes if o["id"] == oid for c in o["counts"]
                    if c["fn"] == fn and c["log"].startswith(f"{CAPTURES}/native-outcomes-v1a/"))
    doc = {
        "schema": SCHEMA,
        "id": RECEIPT_ID,
        "question": ("What does the native game get from the imports the runtime refuses, and "
                     "which imports does it call before the Mod Loader's Init?"),
        "generated_by": SELF,
        "decision": DECISION,
        "inputs": {
            "pipeline": {SELF: sha256_file(root / SELF)},
            "executable": {"file": EXE_NAME, "sha256": pin, "pinned_by": "tools/pins.json"},
        },
        "logs": logs,
        "host": {"hid_devices": ({"count": len(hid), "sha256": hid_digest(hid)}
                                 if hid is not None else "not recorded"),
                 "recorded_by": sorted(hid_lists)},
        "devices": {
            "from": "native-outcomes-v1a",
            "gamectrl": counted("dinput8-enumeration", "trace:dinput:dinput8_a_CreateDevice"),
            "enumerated": [{"dev_type": enum["dev_type"]}],
            "xinput_connected": 0,
            "wmi_pnp": int(captured["wbem-scan"]["wmi_pnp"]),
            "ddraw": 1 + counted("ddraw-enumeration",
                                 "trace:ddraw:ddraw_enumerate_secondary_devices"),
            "note": ("gamectrl counts the game thread's CreateDevice calls on a device other "
                     "than the system keyboard and mouse (outcome dinput8-enumeration, bounded "
                     "to one); enumerated is the device Wine opened for the enumeration, its "
                     "dwDevType read from the line just before its cache entry, and the "
                     "callback created that device; its VID:PID is host inventory and is "
                     "not recorded; xinput_connected is 0 because outcome "
                     "xinput-not-connected holds; ddraw is the default interface plus the "
                     "secondary devices counted in outcome ddraw-enumeration."),
        },
        "outcomes": outcomes,
        "native_pre_init": {
            "boundary": ("the main thread's first +loaddll line loading an enabled mod's DLL, "
                         "per log (logs[].mod_init)"),
            "imports": pre,
        },
    }
    leaks = inventory_leaks(doc, [d for v in hid_lists.values() for d in v])
    if leaks:
        raise Refused("the receipt would carry host inventory: " + "; ".join(leaks[:5]))
    return doc


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def inventory_leaks(doc: dict, hid_devices: list[str]) -> list[str]:
    """Every string in `doc` that carries host inventory, by the rules in the docstring."""
    names, ids = set(), set()
    for d in hid_devices:
        ident, _, name = d.partition(" ")
        names.update(x for x in (ident, name.strip()) if len(x) >= 4)
        # The runner records a HID device as the kernel's BUS:VENDOR:PRODUCT, each
        # zero-padded hex; the VID:PID is the last four digits of the other two.
        parts = ident.split(":")
        if len(parts) == 3 and all(re.fullmatch(r"[0-9a-fA-F]{4,}", p) for p in parts[1:]):
            ids.add(f"{parts[1][-4:]}:{parts[2][-4:]}".lower())
    vidpid = {v: re.compile(rf"(?<![0-9a-f]){re.escape(v)}(?![0-9a-f])") for v in sorted(ids)}
    out = []
    for s in strings(doc):
        for m in GUID.finditer(s):
            if m.group(1).lower() not in KEPT_GUIDS:
                out.append(f"GUID {m.group(0)} in {s[:80]!r}")
        if DEVICE_ID.search(s):
            out.append(f"a device ID in {s[:80]!r}")
        out += [f"host HID device {n!r} in {s[:80]!r}" for n in names if n in s]
        low = s.lower()
        out += [f"host HID VID:PID {v!r} in {s[:80]!r}" for v, rx in vidpid.items()
                if rx.search(low)]
    return out


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- readers

def load(path: Path = RECEIPT) -> dict:
    """The tracked receipt, or SystemExit naming what is wrong with it."""
    if not path.is_file():
        raise SystemExit(f"{path} is absent; run {SELF} --write")
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not JSON: {e}")
    if doc.get("schema") != SCHEMA or doc.get("id") != RECEIPT_ID:
        raise SystemExit(f"{path} is not a {RECEIPT_ID} receipt")
    for k in ("logs", "outcomes", "native_pre_init", "host", "devices"):
        if k not in doc:
            raise SystemExit(f"{path} has no {k}")
    return doc


def verify_evidence(doc: dict, root: Path = ROOT) -> list[str]:
    """Every log the receipt cites exists and hashes to its pin, and every cited line
    is the line: its sha256 is the log line's, and its text is that line redacted."""
    out = []
    pins = {e["log"]: e["sha256"] for e in doc.get("logs", [])}
    items = [e for o in doc.get("outcomes", []) for e in o.get("evidence", [])]
    items += [e for p in (doc.get("native_pre_init") or {}).get("imports", [])
              for e in p.get("evidence", [])]
    items += [e["mod_init"] for e in doc.get("logs", []) if e.get("mod_init")]
    for it in items:
        rel = it.get("log")
        if rel not in pins:
            out.append(f"evidence cites {rel}, which the receipt does not pin")
            continue
        path = root / rel
        if not path.is_file():
            out.append(f"{rel} is missing")
            continue
        try:
            st = path.stat()
            digest, lines = _parse(str(path), st.st_size, st.st_mtime_ns)
        except Refused as e:
            out.append(str(e))
            continue
        if digest != pins[rel]:
            out.append(f"{rel} no longer hashes to its pin {pins[rel][:12]}")
            continue
        n = it.get("line")
        if not isinstance(n, int) or not 1 <= n <= len(lines):
            out.append(f"{rel}: line {n!r} is not in the log")
            continue
        raw = lines[n - 1]
        if redact(raw) != it.get("text"):
            out.append(f"{rel}:{n} reads {redact(raw)[:90]!r}, not the cited {str(it.get('text'))[:90]!r}")
        elif "sha256" in it and hashlib.sha256(raw.encode("utf-8")).hexdigest() != it["sha256"]:
            out.append(f"{rel}:{n} does not hash to the cited line's sha256")
    return out


@functools.lru_cache(maxsize=16)
def _check(content_sha: str, path: str, root: str) -> tuple[str, ...]:
    p, r = Path(path), Path(root)
    try:
        doc = load(p)
    except SystemExit as e:
        return (str(e),)
    out = verify_evidence(doc, r)
    try:
        fresh = build(r)
    except Refused as e:
        return tuple(out + [f"the receipt cannot be recomputed: {e}"])
    if fresh != doc:
        diff = sorted(k for k in set(fresh) | set(doc) if fresh.get(k) != doc.get(k))
        out.append(f"{Path(path).name} is not what {SELF} computes from the logs now "
                   f"(differs in {', '.join(diff)}); re-run {SELF} --write")
    hid = []
    for key in LOGS:
        try:
            hid += (capture_receipt(key, r)[1].get("session") or {}).get("host_hid_devices") or []
        except Refused:
            pass
    out += [f"host inventory in the receipt: {x}" for x in inventory_leaks(doc, hid)[:5]]
    return tuple(out)


def check(path: Path = RECEIPT, root: Path = ROOT) -> list[str]:
    """Problems with the receipt at `path`, recomputed from the logs; [] when it holds."""
    if not path.is_file():
        return [f"{path} is absent; run {SELF} --write"]
    return list(_check(sha256_file(path), str(path), str(root)))


def pre_init_imports(doc: dict) -> list[str]:
    return sorted(p["import"] for p in doc["native_pre_init"]["imports"])


def outcome(doc: dict, oid: str) -> dict | None:
    return next((o for o in doc.get("outcomes", []) if o.get("id") == oid), None)


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help=f"write {RECEIPT_REL}")
    g.add_argument("--check", action="store_true", help=f"recompute {RECEIPT_REL} and compare")
    a = ap.parse_args(argv)
    if a.check:
        probs = check()
        for p in probs:
            print(f"  {p}")
        print(f"NATIVE_OUTCOMES_{'FAILED' if probs else 'OK'} {RECEIPT_REL}"
              + (f" problems={len(probs)}" if probs else ""))
        return 1 if probs else 0
    try:
        doc = build()
    except Refused as e:
        print(f"NATIVE_OUTCOMES_FAILED {e}")
        return 1
    for o in doc["outcomes"]:
        print(f"  {o['id']:<22} {o['outcome']:<34} {o['answer']:<8} evidence "
              f"{len(o['evidence']):>2}  {' '.join(o['profile_records']) or '-'}")
    print(f"  pre-Init: {', '.join(pre_init_imports(doc))}")
    if a.write:
        RECEIPT.write_text(render(doc))
        print(f"NATIVE_OUTCOMES wrote {RECEIPT_REL}")
    print(f"NATIVE_OUTCOMES outcomes={len(doc['outcomes'])} "
          f"pre_init={len(doc['native_pre_init']['imports'])} logs={len(doc['logs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
