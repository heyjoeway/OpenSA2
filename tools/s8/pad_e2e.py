#!/usr/bin/env python3
"""Controllers end to end on Linux: the lifted game reaches its main menu on gamepad records alone.

Subtitle: `source tools/env.sh; tools/slot.sh .venv/bin/python tools/s8/pad_e2e.py` runs the boot
build on a live input FIFO this process holds and writes, the way the launcher and play_live hold
theirs, once per run below, and says whether the game's controller path took the pad: present
from boot, connected after the title, unplugged, and the keyboard alone.

What it shows
-------------
Contract (A) of the 2026-10-12 push (runtime/sa2_dinput.h, "The host gamepads"): the live input
source carries gamepad records (SA2P) beside keyboard records (SA2K), and the runtime's XInput
ordinals answer a connected slot from the latest of them. The game makes its pad objects once, at
its boot scan (0x00864E29), for the slots answered connected, and each pad object then calls
XInputGetCapabilities and XInputGetState on every frame (0x00863D40).

**A pad connected after boot** ("Pads that connect later" in runtime/sa2_dinput.h): in a session
whose sender sent a gamepad record before the boot scan, the runtime answers that scan and each
new pad object's first poll connected for all four slots, so the game makes all four pad objects,
and answers the rescan (0x00864A27) connected for every slot that has one, so the game's
"controllers changed" dialog (0x0040A8F0 state 4, whose default "yes" runs Launcher.exe -r through
ShellExecuteW, which this runtime does not stand in for) never comes. Every other call answers the
records. The runs `hotplug` and `unplug` measure it.

**A pad object alone does not drive the game.** Which device a player reads is the player's
InputWay (0x0174B5FC + 4 * player, read from Config/Keyboard.cfg's <Player InputWay=...> at
0x004056DE): the controller fill (0x00425700, called by UpdateControllers 0x0077E780) takes the
next pad for a player only when its InputWay is not 0 and the pad's last poll found it connected,
and the keyboard otherwise. The game sets InputWay from the pads itself when its command line is
exactly "-r" (0x00401BD1, lstrcmpA, sets 0x01933F6C): right after the input manager's start-up,
0x00425AE0 counts the pad objects whose first poll found them connected (0x00864380) and gives
player 1 InputWay 1 for one (when both are 0), both players 1 for two or more, and 0 to both for
none. So a live session that wants its pads passes SA2_GUEST_CMDLINE=-r (runtime/sa2_imports.c),
and with pads that connect later the count is four in every session that sends a gamepad record,
so both players' InputWay is 1 (summary.json's input_way_changes) and player 1's prompts show the
pad's buttons even while the keyboard drives. The runs, all with -r unless named -noflag, keys or
nosender:

  pad         slot 0 connected from before the game starts; START held at ticks 600, 1000 and A
              at 1300 (the NEW FILE keyboard script's ticks, tools/s5/poll_handoff_check.py
              NEWFILE_SCRIPT, as pad buttons): passes when CurrentMenu (0x01D7BB10) reads
              Menus_FileSelect (9) and then Menus_Main (1);
  idle        the pad connected and at rest: the control, CurrentMenu never reaches 9 or 1;
  keys-nopad  Enter on the keyboard records at the same ticks and no pad (one gamepad record,
              every slot disconnected): the keyboard drives player 1 when no pad is connected;
  hotplug     no pad at boot; slot 0 connects at tick 2000, past the title, and presses START at
              2100 and 2500 and A at 2800: passes when File Select and then the main menu come
              after tick 2000, and the run ends at its SIGTERM (no trap);
  keys-late   the keyboard alone at hotplug's ticks: the control for hotplug's schedule;
  unplug      slot 0 connected at boot and unplugged at tick 400, Enter on the keyboard at the
              NEW FILE ticks: passes when the keyboard takes player 1 back and reaches the main
              menu with no dialog (no trap);
  pad-noflag  no command line: the pad pressed as in `pad`, InputWay 0 from Keyboard.cfg: the
              game ignores the pad (recorded, not a verdict: what a session without -r gets);
  keys        no command line; Enter as in keys-nopad beside a connected pad at rest: the keyboard
              drives player 1 (recorded, not a verdict);
  keys-pad    Enter beside a connected pad at rest: player 1 reads the pad, so the keyboard's
              Enter is not player 1's (recorded, not a verdict);
  nosender    no command line, no gamepad record ever and SA2_INPUT_PAD_WAIT_MS=0, the launcher
              selftest's input, and no input at all: passes when the run ends cleanly at the
              title; with --same-as DIR (DIR another --out, e.g. the same run on the build before
              a runtime change), when its capture equals DIR's row for row up to its stop tick
              less 20 too (and, with --keep-words in both, every word of .dataa at those ticks),
              which says a session with no gamepad answers the game as before. idle, the other
              run with no input after boot, is compared too: its capture's digests leave out
              InputWay and the heap's pointers, which its word log does not.

Each run is the boot build `Documentation/scripts/fast_check.py` links from the tree (its
`fast-boot`, copied here first so a concurrent rebuild cannot replace it mid-run; `--binary` names
another) with the registered boot replay's environment (its device profile, the recording devices)
and the NEW FILE run's budgets, SA2_INPUT_DI_SOURCE this process's FIFO, SA2_INPUT_DI_LOG,
SA2_REPLAY_TICKS (read live: the writer keys its presses to the tick rows as they are flushed) and
SA2_REPLAY_WORDS (CurrentMenu is read from the word log afterwards, tools/s4/wordlog.py). The run
ends with SIGTERM at its stop tick (1800, or 3300 for the late runs), which the runtime turns into
the host's WM_QUIT at its next keyboard poll, so the guest leaves through its own exit and the
probe writes its record.

Outputs under --out (default private/generated/s8/input/e2e): <run>/run.json, stderr.txt,
input.log, lifted.csv, and summary.json, which holds every run's verdict, CurrentMenu's changes, the
XInput counters of the run record, the gamepad line of the input log and InputWay's changes (tick
0 lists its value at the first tick). The word logs are removed after they are read (they are
large) unless --keep-words. Exit 0 when every verdict of the runs asked for holds (pad,
idle, keys-nopad, hotplug, keys-late, unplug, nosender), 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / "s4")]
import reach_receipt as RR  # noqa: E402  clean_environ, ENV
import wordlog as WL  # noqa: E402

FAST_BOOT = ROOT / "private/generated/fastcheck/s4/link/mains/fast-boot/sa2_main"
EXE = ROOT / "private/exe/sonic2app-unpacked.exe"
OUT = ROOT / "private/generated/s8/input/e2e"
CURRENT_MENU = 0x01D7BB10                 # SA2Variables.h CurrentMenu
INPUT_WAY = 0x0174B5FC                    # players 1 and 2's InputWay (0x00425AE0)
MENUS_MAIN, MENUS_FILE_SELECT = 1, 9
KEY_MAGIC, PAD_MAGIC = 0x4B324153, 0x50324153
START, A = 0x0010, 0x1000                 # XINPUT_GAMEPAD_START, XINPUT_GAMEPAD_A
DIK_RETURN = 0x1C
HOLD = 8                                  # ticks a press is held
EARLY = (600, 1000, 1300)                 # the NEW FILE script's ticks
EARLY_STOP = 1800
HOTPLUG_TICK = 2000                       # past the title, which is up by tick 600
LATE = (2100, 2500, 2800)
LATE_STOP = 3300
UNPLUG_TICK = 400
TRAP_MARKS = ("TRAP", "trap:", "unimplemented import")


@dataclass(frozen=True)
class Run:
    cmdline: str                          # SA2_GUEST_CMDLINE, "" for none
    boot_pad: bool                        # slot 0 connected in the record sent before the game
    presses: str | None                   # "pad" (START, START, A), "keys" (Enter x3) or None
    ticks: tuple[int, int, int] = EARLY
    stop: int = EARLY_STOP
    connect_at: int | None = None         # slot 0 connects at this tick
    disconnect_at: int | None = None      # slot 0 is unplugged at this tick
    pad_record: bool = True               # False: no gamepad record ever, as the selftest
    wait_ms: str | None = None            # SA2_INPUT_PAD_WAIT_MS, unset for the runtime's default


RUNS = {
    "pad": Run("-r", True, "pad"),
    "idle": Run("-r", True, None),
    "keys-nopad": Run("-r", False, "keys"),
    "hotplug": Run("-r", False, "pad", LATE, LATE_STOP, connect_at=HOTPLUG_TICK),
    "keys-late": Run("-r", False, "keys", LATE, LATE_STOP),
    "unplug": Run("-r", True, "keys", disconnect_at=UNPLUG_TICK),
    "pad-noflag": Run("", True, "pad"),
    "keys": Run("", True, "keys"),
    "keys-pad": Run("-r", True, "keys"),
    "nosender": Run("", False, None, pad_record=False, wait_ms="0"),
}
MENU_VERDICTS = ("pad", "keys-nopad", "hotplug", "keys-late", "unplug")
IDLE_VERDICTS = ("idle", "nosender")
SAME_MARGIN = 20                          # rows before the stop tick compared by --same-as


class Writer:
    """The FIFO's one writer: keyboard and gamepad records, each kind on its own seq counter
    from 0 (contract A), as host/player/input.cpp writes them."""

    def __init__(self, fd: int) -> None:
        self.fd, self.seq = fd, {KEY_MAGIC: 0, PAD_MAGIC: 0}

    def _put(self, magic: int, body: bytes) -> None:
        rec = struct.pack("<II", magic, self.seq[magic]) + body
        if os.write(self.fd, rec) != len(rec):
            raise SystemExit("pad_e2e: a short write to the FIFO")
        self.seq[magic] += 1

    def keys(self, held: tuple[int, ...]) -> None:
        k = bytearray(256)
        for d in held:
            k[d] = 0x80
        self._put(KEY_MAGIC, bytes(k))

    def pad(self, connected: bool, buttons: int = 0) -> None:
        slot0 = struct.pack("<BBHBBhhhhBB", 1 if connected else 0, 0,
                            buttons if connected else 0, 0, 0, 0, 0, 0, 0, 0, 0)
        self._put(PAD_MAGIC, slot0 + bytes(48))


def schedule(run: Run) -> list[tuple[int, str, int]]:
    """(tick, what, value), in tick order: what is "pad" (buttons), "keys" (a DIK held, 0 for
    none), "connect" or "disconnect" (slot 0)."""
    ev: list[tuple[int, str, int]] = []
    if run.connect_at is not None:
        ev.append((run.connect_at, "connect", 0))
    if run.disconnect_at is not None:
        ev.append((run.disconnect_at, "disconnect", 0))
    for i, t in enumerate(run.ticks if run.presses else ()):
        if run.presses == "pad":
            b = A if i == 2 else START
            ev += [(t, "pad", b), (t + HOLD, "pad", 0)]
        else:
            ev += [(t, "keys", DIK_RETURN), (t + HOLD, "keys", 0)]
    return sorted(ev, key=lambda e: e[0])


def boot_env(polls: int) -> dict[str, str]:
    row = json.loads((ROOT / "private/generated/s4/run/replay-boot.matrix.json").read_text())["rows"][0]
    env = dict(RR.clean_environ())
    env.update(row["env"])
    env.update({"SA2_INSTALL": "private/install", "SA2_PUMP_POLLS": str(polls),
                "SA2_IMPORT_BUDGET": str(max(8_000_000, polls * 5000)),
                "SA2_VIRTUAL_BUDGET_MS": str(max(30000, polls * 20)),
                "SDL_AUDIO_DRIVER": "dummy"})
    for k in ("SA2_INPUT_DI_SCRIPT", "SA2_INPUT_PAD_WAIT_MS", "SA2_XINPUT_PAD", "SA2_GUEST_CMDLINE",
              "SA2_INPUT_PAD_HOTPLUG"):
        env.pop(k, None)
    return env


def run_one(kind: str, binary: Path, out: Path, keep_words: bool = False) -> dict:
    run = RUNS[kind]
    d = out / kind
    if d.exists():
        subprocess.run([sys.executable, str(ROOT / "tools/rmtree.py"), str(d)], check=True)
    (d / "vfsroot").mkdir(parents=True)
    fifo, ticks, words = d / "input.fifo", d / "lifted.csv", d / "words.bin"
    os.mkfifo(fifo, 0o600)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)      # the holder, as the launcher's
    w = Writer(fd)
    connected = run.boot_pad
    if run.pad_record:
        w.pad(connected)                               # the pads, before the boot scan
    env = boot_env(run.stop + 600)                     # the host's bound, past the stop tick
    if run.cmdline:
        env["SA2_GUEST_CMDLINE"] = run.cmdline
    if run.wait_ms is not None:
        env["SA2_INPUT_PAD_WAIT_MS"] = run.wait_ms
    env.update({"SA2_VFS_WRITE_ROOT": str(d / "vfsroot"), "SA2_REPLAY_TICKS": str(ticks),
                "SA2_REPLAY_WORDS": str(words), "SA2_INPUT_DI_SOURCE": str(fifo),
                "SA2_INPUT_DI_LOG": str(d / "input.log")})
    argv = [str(binary), str(EXE), "--json", str(d / "run.json"), "--timeout", "900"]
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=open(d / "stderr.txt", "w"), start_new_session=True)
    events = schedule(run)
    sent: list[dict] = []
    seen, buf, stopped = 0, b"", None
    while proc.poll() is None and time.monotonic() - t0 < 900:
        if ticks.is_file():
            with open(ticks, "rb") as f:
                f.seek(len(buf))
                buf += f.read()
            seen = max(0, buf.count(b"\n") - 1)
        while events and events[0][0] <= seen:
            t, what, v = events.pop(0)
            if what in ("connect", "disconnect"):
                connected = what == "connect"
                w.pad(connected)
            elif what == "pad":
                w.pad(connected, v)
            else:
                w.keys((v,) if v else ())
            sent.append({"at_tick": seen, "planned": t, "what": what, "value": hex(v)})
        if stopped is None and seen >= run.stop:
            stopped = seen
            proc.send_signal(signal.SIGTERM)
        time.sleep(0.002)
    try:
        proc.wait(timeout=300)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    os.close(fd)
    seconds = round(time.monotonic() - t0, 1)
    rec = json.loads((d / "run.json").read_text()) if (d / "run.json").is_file() else {}
    menus: list[dict] = []
    input_way: list[dict] = []
    if words.is_file():
        try:
            menus = [{"tick": t, "from": old, "to": new}
                     for t, va, old, new in WL.changes(words, CURRENT_MENU, CURRENT_MENU + 4)
                     if va == CURRENT_MENU and t > 0]
            input_way = [{"tick": t, "player": 1 + (va - INPUT_WAY) // 4, "from": old, "to": new}
                         for t, va, old, new in WL.changes(words, INPUT_WAY, INPUT_WAY + 8)]
        finally:
            if not keep_words:
                words.unlink()
    log_tail = (d / "input.log").read_text().splitlines()[-1:] if (d / "input.log").is_file() else []
    stderr = (d / "stderr.txt").read_text()
    after = run.connect_at or 0
    found = {"file_select_at": next((m["tick"] for m in menus
                                     if m["to"] == MENUS_FILE_SELECT and m["tick"] > after), None),
             "main_menu_at": next((m["tick"] for m in menus
                                   if m["to"] == MENUS_MAIN and m["tick"] > after), None)}
    return {"run": kind, "cmdline": run.cmdline, "boot_pad": run.boot_pad, "presses": run.presses,
            "press_ticks": list(run.ticks), "connect_at": run.connect_at,
            "disconnect_at": run.disconnect_at, "stop_tick": run.stop,
            "rc": proc.returncode, "seconds": seconds, "ticks": seen,
            "sigterm_at_tick": stopped, "sent": sent, "current_menu_changes": menus, **found,
            "input_way_changes": input_way,
            "trapped": any(m in stderr for m in TRAP_MARKS),
            "stderr_hotplug": [ln for ln in stderr.splitlines() if "sa2 dinput:" in ln],
            "stderr": stderr[-2000:], "input_log_last": log_tail,
            "record_dinput_input": ((rec.get("dinput") or {}).get("host_input") if rec else None),
            "record_iface_xinput": find_xinput(rec)}


def find_xinput(rec: object) -> object:
    """Whatever the run record says about XInput, wherever it says it."""
    if isinstance(rec, dict):
        hit = {k: v for k, v in rec.items() if "xinput" in k.lower() or k in ("pad_absent", "pad_present")}
        if hit:
            return hit
        for v in rec.values():
            r = find_xinput(v)
            if r:
                return r
    return None


def same_capture(a: Path, b: Path, upto: int) -> dict:
    """The tick rows 1..upto of two runs' captures, compared whole, and, when both kept their
    word logs (--keep-words), every word of `.dataa` at those ticks."""
    def rows(p: Path) -> dict[str, str]:
        lines = p.read_text().splitlines()[1:] if p.is_file() else []
        return {ln.split(",", 1)[0]: ln for ln in lines if ln.split(",", 1)[0].isdigit()
                and int(ln.split(",", 1)[0]) <= upto}
    ra, rb = rows(a), rows(b)
    diff = sorted((int(t) for t in set(ra) | set(rb) if ra.get(t) != rb.get(t)))
    got = {"against": str(b), "rows": len(ra), "rows_other": len(rb), "upto": upto,
           "equal": bool(ra) and len(ra) == len(rb) == upto and not diff,
           "first_diff_tick": diff[0] if diff else None, "words_compared": False}
    wa, wb = a.parent / "words.bin", b.parent / "words.bin"
    if wa.is_file() and wb.is_file():
        words, meta = WL.diff(wa, wb, ticks=list(range(1, upto + 1)))
        got.update({"words_compared": True, "words_differing": len(words),
                    "words_first": [{k: (hex(v) if k in ("va", "a", "b") else v)
                                     for k, v in w.items()} for w in words[:8]],
                    "words_compared_through": meta["compared_through"]})
        got["equal"] = got["equal"] and not words
    return got


def verdict(kind: str, r: dict) -> bool:
    ends = r["rc"] == 0 and not r["trapped"] and r["sigterm_at_tick"] is not None
    if "same_as" in r and not r["same_as"]["equal"]:
        return False
    if kind in IDLE_VERDICTS:
        return ends and r["file_select_at"] is None and r["main_menu_at"] is None
    return bool(ends and r["file_select_at"] and r["main_menu_at"]
                and r["file_select_at"] < r["main_menu_at"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--binary", type=Path, default=None,
                    help="the boot build to run (default: a copy of fast_check's fast-boot)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--runs", default=",".join(RUNS))
    ap.add_argument("--same-as", type=Path, default=None,
                    help="another --out of the same runs: each run with no input after boot "
                         "(nosender, idle), which is deterministic, must have that one's capture up "
                         "to its stop tick less 20")
    ap.add_argument("--keep-words", action="store_true",
                    help="keep each run's word log (words.bin, large), which --same-as then "
                         "compares word for word as well")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    binary = a.binary
    if binary is None:
        if not FAST_BOOT.is_file():
            raise SystemExit(f"pad_e2e: no {FAST_BOOT}: run Documentation/scripts/fast_check.py on "
                             "a runtime change first, or pass --binary")
        binary = a.out / "sa2_main"
        shutil.copy2(FAST_BOOT, binary)
    for k in a.runs.split(","):
        if k not in RUNS:
            raise SystemExit(f"pad_e2e: no run {k!r}; the runs are {', '.join(RUNS)}")
    results = {k: run_one(k, binary, a.out, a.keep_words) for k in a.runs.split(",")}
    if a.same_as:
        for k, r in results.items():
            if not schedule(RUNS[k]):           # no input after boot: a deterministic run
                r["same_as"] = same_capture(a.out / k / "lifted.csv", a.same_as / k / "lifted.csv",
                                            RUNS[k].stop - SAME_MARGIN)
    verdicts = {k: verdict(k, r) for k, r in results.items()
                if k in MENU_VERDICTS or k in IDLE_VERDICTS}
    summary = {"binary": str(binary), "verdicts": verdicts, "runs": results}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    for k, r in results.items():
        print(f"{k}: rc {r['rc']}, {r['ticks']} ticks in {r['seconds']} s, trapped {r['trapped']}, "
              f"CurrentMenu {[(m['tick'], m['to']) for m in r['current_menu_changes']]}, "
              f"InputWay {[(c['tick'], c['player'], c['to']) for c in r['input_way_changes']]}, "
              + (f"same as {r['same_as']['against']}: {r['same_as']['equal']} (rows "
                 f"{r['same_as']['rows']}, first row diff {r['same_as']['first_diff_tick']}, words "
                 f"{r['same_as'].get('words_differing', 'not compared')}), "
                 if "same_as" in r else "") + f"verdict {verdicts.get(k)}")
    if not verdicts:
        print("PAD_E2E_RECORDED (no run asked for has a verdict)")
        return 0
    ok = all(verdicts.values())
    print("PAD_E2E_OK" if ok else "PAD_E2E_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
