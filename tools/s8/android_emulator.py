#!/usr/bin/env python3
"""android_emulator: one headless Android emulator for the project, set up, started, used and
stopped by command.

Subtitle: the Android step's test device on this machine (no phone is attached to it). Google's
x86_64 system image for API 35 (google_apis) runs under KVM, and it translates arm64 code
(ro.product.cpu.abilist lists arm64-v8a; the image's native bridge runs arm64 apps, and its
binfmt_misc entry runs an arm64 executable launched from `adb shell`), so the arm64 game and the
arm64 APK both run on it. Translated code is slower than an arm64 phone: a speed measured here
is a floor, not the phone's number.

Everything lives under private/: the SDK in private/android-sdk (ANDROID_SDK_ROOT), the AVD
`sa2` in private/android-avd (ANDROID_AVD_HOME), and this tool's state, lock and emulator log
in private/android-avd/. The adb keys stay in ~/.android, which the adb server and the emulator
both read, so `adb` is authorised on the emulator without a prompt.

Commands (each prints JSON on stdout unless it runs a command for you):

  setup     install the emulator and the system image with sdkmanager (idempotent) and create
            the AVD (16 GB data partition, 3072 MB RAM, 4 cores, a hardware keyboard).
  start     boot the AVD headless (-no-window -no-audio -no-snapshot -no-boot-anim, -gpu
            swiftshader_indirect by default, -memory 3072) on console port 5554, so its adb
            serial is emulator-5554, and wait until sys.boot_completed is 1. The emulator runs
            through tools/slot.sh, so it holds one of the machine's three heavy-job slots for
            as long as it runs (and waits for one, and for 8 GB free, before it boots). One
            emulator at a time: a second `start` while one runs reports it and boots nothing,
            and `start` refuses when any other emulator (a qemu-system-* of the Android
            emulator) is running on the machine.
  stop      `adb emu kill`, then the emulator's whole process group if it has not exited in
            60 s. The slot is released when it exits.

  Leases: the emulator is shared by the units that use it. `start --client NAME` takes (or
  refreshes) NAME's lease, for --lease-minutes (120); `stop --client NAME` releases NAME's lease
  and stops the emulator only when no other unexpired lease remains, or with --force. Without
  --client both use the client `default`. `status` lists the leases. A unit that wants a cold
  boot while others hold leases must agree it with them (or use --force knowing it ends their
  runs).
  status    whether the emulator runs and has booted, its serial, pid, ABIs and native bridge.
  shell     `adb -s <serial> shell <args...>` (exit status passed through).
  adb       `adb -s <serial> <args...>` (push, pull, install, logcat, forward, ...).
  root      restart adbd as root (the image is userdebug), so `shell` runs in the permissive su
            domain: the shell domain may not mkfifo under /data/local/tmp, for one.
  env       the environment lines (export ...) for using the SDK and the AVD by hand.

A unit using the emulator (android-app) calls this tool, never the emulator binary directly, so
the lock and the slot hold. Its exit status is 0 on success; `start` exits 1 when the boot did
not complete within --wait seconds (the emulator is then stopped), 2 when it may not start.

Usage:
  .venv/bin/python tools/s8/android_emulator.py setup
  .venv/bin/python tools/s8/android_emulator.py start [--gpu swiftshader_indirect|host|guest]
      [--memory 3072] [--cores 4] [--wait 900] [--wipe-data]
  .venv/bin/python tools/s8/android_emulator.py status
  .venv/bin/python tools/s8/android_emulator.py shell getprop ro.product.cpu.abilist
  .venv/bin/python tools/s8/android_emulator.py adb push file /data/local/tmp/
  .venv/bin/python tools/s8/android_emulator.py stop
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "AGENTS.md").is_file() and (p / "tools" / "pins.json").is_file():
            return p
    raise SystemExit(f"no repository above {__file__}")


ROOT = _root()
SDK = ROOT / "private" / "android-sdk"
AVD_HOME = ROOT / "private" / "android-avd"
AVD = "sa2"
IMAGE = "system-images;android-35;google_apis;x86_64"
PACKAGES = ("emulator", "platform-tools", IMAGE)
PORT = 5554
SERIAL = f"emulator-{PORT}"
STATE = AVD_HOME / "emulator.state"       # JSON: pid (the process group), serial, argv, started
LOCK = AVD_HOME / "emulator.lock"         # flock around start and stop
LEASES = AVD_HOME / "emulator.leases"     # JSON {client: expiry (epoch seconds)}
LEASE_MINUTES = 120
LOG = AVD_HOME / "emulator.log"
SLOT = ROOT / "tools" / "slot.sh"
JAVA_HOME = ROOT / "private" / "predecessor-tools" / "temurin21" / "jdk-21.0.12.1+1"
# The AVD's hardware, written into its config.ini at setup (keys the emulator reads).
AVD_CONFIG = {"disk.dataPartition.size": "16G", "hw.ramSize": "3072", "hw.cpu.ncore": "4",
              "hw.keyboard": "yes", "hw.gpu.enabled": "yes",
              "hw.gpu.mode": "swiftshader_indirect", "hw.audioInput": "no",
              "hw.audioOutput": "no", "fastboot.forceColdBoot": "yes",
              "fastboot.forceFastBoot": "no", "hw.initialOrientation": "landscape",
              "hw.lcd.width": "1280", "hw.lcd.height": "720", "hw.lcd.density": "240",
              "showDeviceFrame": "no"}
# Keys the 2026 avdmanager writes with template placeholders (`<build>`, `<temp>`) that the
# emulator would take literally; setup drops them and the emulator derives them.
AVD_PLACEHOLDER_KEYS = ("avd.id", "avd.name", "disk.dataPartition.path")


def adb_path() -> Path:
    return SDK / "platform-tools" / "adb"


def emulator_path() -> Path:
    return SDK / "emulator" / "emulator"


def env() -> dict[str, str]:
    e = dict(os.environ)
    e["ANDROID_SDK_ROOT"] = str(SDK)
    e["ANDROID_HOME"] = str(SDK)
    e["ANDROID_AVD_HOME"] = str(AVD_HOME)
    if JAVA_HOME.is_dir():
        e["JAVA_HOME"] = str(JAVA_HOME)
        e["PATH"] = f"{JAVA_HOME / 'bin'}:{e.get('PATH', '')}"
    return e


def adb(*args: str, timeout: float = 120, check: bool = False,
        serial: str | None = SERIAL) -> subprocess.CompletedProcess:
    cmd = [str(adb_path())] + (["-s", serial] if serial else []) + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env())
    if check and p.returncode:
        raise SystemExit(f"adb {' '.join(args)[:200]} exited {p.returncode}: "
                         f"{(p.stderr or p.stdout)[-800:]}")
    return p


def getprop(name: str) -> str:
    try:
        p = adb("shell", "getprop", name, timeout=20)
    except subprocess.TimeoutExpired:
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def read_state() -> dict | None:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def emulator_processes() -> list[tuple[int, str]]:
    """Every Android emulator process on the machine (qemu-system-* from an emulator
    directory), as (pid, command line)."""
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        exe = cmd[0].decode(errors="replace") if cmd else ""
        if Path(exe).name.startswith("qemu-system-") and "emulator" in exe:
            out.append((int(d.name), " ".join(c.decode(errors="replace") for c in cmd if c)))
    return out


class Locked:
    """flock on LOCK for the duration of a start or a stop."""

    def __enter__(self):
        AVD_HOME.mkdir(parents=True, exist_ok=True)
        self.fh = open(LOCK, "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


# ------------------------------------------------------------------ setup

def avd_config_text(old: str) -> str:
    """An AVD config.ini with AVD_CONFIG's keys set (each once, last) and avdmanager's
    placeholder values (AVD_PLACEHOLDER_KEYS holding a `<...>`) dropped; every other line kept."""
    def key(line: str) -> str:
        return line.split("=", 1)[0].strip()
    lines = [l for l in old.splitlines()
             if key(l) not in AVD_CONFIG and not (key(l) in AVD_PLACEHOLDER_KEYS and "<" in l)]
    lines += [f"{k}={v}" for k, v in AVD_CONFIG.items()]
    return "\n".join(lines) + "\n"


def setup(args: argparse.Namespace) -> int:
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise SystemExit("/dev/kvm is not readable and writable by this user: the x86_64 image "
                         "needs KVM (add the user to the kvm group)")
    sdkm = SDK / "cmdline-tools" / "latest" / "bin" / "sdkmanager"
    if not sdkm.is_file():
        raise SystemExit(f"no sdkmanager at {sdkm}: run private/android-sdk/install.sh first")
    need = [p for p in PACKAGES if not (SDK / p.replace(";", "/")).is_dir()]
    if need:
        p = subprocess.run([str(sdkm), f"--sdk_root={SDK}", *need], env=env(), text=True,
                           capture_output=True, input="y\n" * 8)
        if p.returncode:
            raise SystemExit(f"sdkmanager {need} exited {p.returncode}:\n{p.stdout[-2000:]}"
                             f"{p.stderr[-2000:]}")
    AVD_HOME.mkdir(parents=True, exist_ok=True)
    avd_dir = AVD_HOME / f"{AVD}.avd"
    if not (avd_dir / "config.ini").is_file():
        avdm = SDK / "cmdline-tools" / "latest" / "bin" / "avdmanager"
        p = subprocess.run([str(avdm), "create", "avd", "-n", AVD, "-k", IMAGE, "-p",
                            str(avd_dir), "--force"], env=env(), text=True,
                           capture_output=True, input="no\n")
        if p.returncode or not (avd_dir / "config.ini").is_file():
            raise SystemExit(f"avdmanager create avd exited {p.returncode}:\n{p.stdout[-2000:]}"
                             f"{p.stderr[-2000:]}")
    # The AVD's own ini (ANDROID_AVD_HOME/<name>.ini) names its directory; written here in case
    # avdmanager put it elsewhere.
    (AVD_HOME / f"{AVD}.ini").write_text(
        f"avd.ini.encoding=UTF-8\npath={avd_dir}\npath.rel=avd/{AVD}.avd\ntarget=android-35\n")
    cfg = avd_dir / "config.ini"
    cfg.write_text(avd_config_text(cfg.read_text()))
    out = {"sdk": str(SDK), "avd_home": str(AVD_HOME), "avd": AVD, "image": IMAGE,
           "installed": list(PACKAGES), "config": AVD_CONFIG}
    print(json.dumps(out, indent=1))
    return 0


# ------------------------------------------------------------------ start, stop, status

def booted() -> bool:
    return getprop("sys.boot_completed") == "1"


def opt(args, name: str, default):
    return getattr(args, name, default) if args is not None else default


def read_leases() -> dict[str, float]:
    """The clients holding the emulator, expired leases left out."""
    try:
        got = json.loads(LEASES.read_text())
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {c: float(e) for c, e in got.items() if float(e) > now}


def write_leases(leases: dict[str, float]) -> None:
    if leases:
        LEASES.write_text(json.dumps(leases, sort_keys=True) + "\n")
    else:
        LEASES.unlink(missing_ok=True)


def status_dict() -> dict:
    st = read_state() or {}
    pid = st.get("pid")
    running = bool(pid) and group_alive(pid)
    d = {"running": running, "serial": SERIAL, "pid": pid if running else None,
         "started": st.get("started") if running else None, "log": str(LOG),
         "emulator_processes": [p for p, _ in emulator_processes()],
         "leases": {c: time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(e))
                    for c, e in read_leases().items()} if running else {}}
    if running:
        devs = adb("devices", serial=None, timeout=20).stdout
        d["adb_state"] = next((l.split()[1] for l in devs.splitlines()
                               if l.startswith(SERIAL)), "absent")
        d["booted"] = d["adb_state"] == "device" and booted()
        if d["booted"]:
            d["abilist"] = getprop("ro.product.cpu.abilist")
            d["native_bridge"] = getprop("ro.dalvik.vm.native.bridge")
            d["sdk"] = getprop("ro.build.version.sdk")
            d["release"] = getprop("ro.build.version.release")
    else:
        d["booted"] = False
    return d


def start(args: argparse.Namespace) -> int:
    if not (AVD_HOME / f"{AVD}.avd" / "config.ini").is_file():
        raise SystemExit("no AVD: run `android_emulator.py setup` first")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise SystemExit("/dev/kvm is not usable by this user")
    client = opt(args, "client", "default")
    lease = time.time() + 60 * opt(args, "lease_minutes", LEASE_MINUTES)
    with Locked():
        st = status_dict()
        if st["running"]:
            write_leases(dict(read_leases(), **{client: lease}))
            print(json.dumps(dict(st, note="already running; nothing started; lease taken",
                                  client=client), indent=1))
            return 0 if st["booted"] else wait_boot(args.wait)
        write_leases({client: lease})       # a fresh emulator: no earlier lease applies
        others = emulator_processes()
        if others:
            print(json.dumps({"refused": "another Android emulator is running on this machine",
                              "processes": others}, indent=1))
            return 2
        argv = [str(SLOT), str(emulator_path()), "-avd", AVD, "-no-window", "-no-audio",
                "-no-snapshot", "-no-boot-anim", "-port", str(PORT), "-gpu", args.gpu,
                "-memory", str(args.memory), "-cores", str(args.cores), "-accel", "on",
                "-netdelay", "none", "-netspeed", "full"]
        if args.wipe_data:
            argv.append("-wipe-data")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG, "w")
        adb("start-server", serial=None, timeout=60)
        p = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env=env(), start_new_session=True, cwd=str(ROOT))
        STATE.write_text(json.dumps({"pid": p.pid, "serial": SERIAL, "argv": argv,
                                     "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
    rc = wait_boot(args.wait)
    if rc:
        print(f"the emulator did not boot in {args.wait} s; stopping it (log {LOG})",
              file=sys.stderr)
        stop(argparse.Namespace(client=client, force=True))
    return rc


def wait_boot(wait: float) -> int:
    t0 = time.time()
    st = read_state() or {}
    while time.time() - t0 < wait:
        if not st.get("pid") or not group_alive(st["pid"]):
            print(json.dumps({"booted": False, "why": "the emulator exited", "log": str(LOG),
                              "log_tail": LOG.read_text(errors="replace")[-1500:]}, indent=1))
            return 1
        devs = adb("devices", serial=None, timeout=20).stdout
        if any(l.startswith(SERIAL) and l.split()[1:2] == ["device"] for l in devs.splitlines()) \
                and booted():
            d = status_dict()
            d["boot_seconds"] = round(time.time() - t0, 1)
            print(json.dumps(d, indent=1))
            return 0
        time.sleep(3)
    return 1


def stop(args: argparse.Namespace) -> int:
    client = opt(args, "client", "default")
    with Locked():
        st = read_state() or {}
        pid = st.get("pid")
        if not pid or not group_alive(pid):
            print(json.dumps({"running": False, "note": "nothing to stop"}))
            STATE.unlink(missing_ok=True)
            LEASES.unlink(missing_ok=True)
            return 0
        leases = read_leases()
        leases.pop(client, None)
        if leases and not opt(args, "force", False):
            write_leases(leases)
            print(json.dumps({"running": True, "released": client, "kept_for": sorted(leases),
                              "note": "other clients hold the emulator; it keeps running "
                                      "(stop --force stops it anyway)"}))
            return 0
        try:
            adb("emu", "kill", timeout=30)
        except subprocess.TimeoutExpired:
            pass
        t0 = time.time()
        while group_alive(pid) and time.time() - t0 < 60:
            time.sleep(1)
        how = "adb emu kill"
        if group_alive(pid):
            os.killpg(pid, signal.SIGTERM)
            how = "SIGTERM to its process group"
            t1 = time.time()
            while group_alive(pid) and time.time() - t1 < 20:
                time.sleep(1)
            if group_alive(pid):
                os.killpg(pid, signal.SIGKILL)
                how = "SIGKILL to its process group"
        STATE.unlink(missing_ok=True)
        LEASES.unlink(missing_ok=True)
        print(json.dumps({"running": False, "stopped_by": how,
                          "seconds": round(time.time() - t0, 1)}))
    return 0


def status(args: argparse.Namespace) -> int:
    print(json.dumps(status_dict(), indent=1))
    return 0


def passthrough(args: argparse.Namespace) -> int:
    rest = list(args.rest)
    if rest and rest[0] == "--":
        rest = rest[1:]
    cmd = [str(adb_path()), "-s", SERIAL] + (["shell"] if args.cmd == "shell" else []) + rest
    return subprocess.call(cmd, env=env())


def ensure_root() -> bool:
    """adbd as root (`adb root`; the google_apis image is userdebug), so `adb shell` runs in the
    permissive su domain. Needed for what the shell domain's SELinux policy refuses, such as
    mkfifo under /data/local/tmp (an app may make FIFOs in its own files directory). True when
    the shell is root afterwards."""
    if adb("shell", "id -u", timeout=20).stdout.strip() == "0":
        return True
    adb("root", timeout=60)
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            p = adb("shell", "id -u", timeout=10)
        except subprocess.TimeoutExpired:
            continue
        if p.returncode == 0 and p.stdout.strip() == "0":
            return True
        time.sleep(1)
    return False


def root(args: argparse.Namespace) -> int:
    ok = ensure_root()
    print(json.dumps({"root": ok}))
    return 0 if ok else 1


def print_env(args: argparse.Namespace) -> int:
    e = env()
    for k in ("ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_AVD_HOME", "JAVA_HOME"):
        if k in e:
            print(f"export {k}={e[k]}")
    print(f"export ANDROID_SERIAL={SERIAL}")
    print(f"export PATH={SDK / 'platform-tools'}:{SDK / 'emulator'}:$PATH")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    s = sub.add_parser("start")
    s.add_argument("--gpu", default="swiftshader_indirect",
                   help="swiftshader_indirect (default, no host GPU needed), host, guest")
    s.add_argument("--memory", type=int, default=3072)
    s.add_argument("--cores", type=int, default=4)
    s.add_argument("--wait", type=float, default=900, help="seconds to wait for the boot")
    s.add_argument("--wipe-data", action="store_true")
    s.add_argument("--client", default="default",
                   help="who holds the emulator (a unit's name); stop releases only its lease")
    s.add_argument("--lease-minutes", type=float, default=LEASE_MINUTES)
    sp = sub.add_parser("stop")
    sp.add_argument("--client", default="default")
    sp.add_argument("--force", action="store_true",
                    help="stop even while other clients hold leases")
    sub.add_parser("status")
    sub.add_parser("env")
    sub.add_parser("root")
    for name in ("shell", "adb"):
        p = sub.add_parser(name)
        p.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    return {"setup": setup, "start": start, "stop": stop, "status": status, "env": print_env,
            "shell": passthrough, "adb": passthrough, "root": root}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
