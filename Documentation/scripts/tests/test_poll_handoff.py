"""The guest-poll rule (runtime/sa2_threads.h sa2_sched_poll), its wrapper
(runtime/sa2_poll_handoff.c), the links that must carry it, and the gate that
holds the registered runs to it.

harness/poll_handoff_probe.c is built with the pinned zig from the production
runtime files, linked as production links them (-Wl,-wrap=sub_427ee0), and each
scenario is held to literal counts. The same scenarios under SA2_POLL_HANDOFF=0
and SA2_THREADS_BREAK=pollimports must break the expectations they exist for:
the first brings the hang back, the second hands off on the drawing path (the
review's S2a). Without the pinned zig the build fails rather than skips, since a
skip would pass the gate with the rule unheld."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_poll_handoff as G  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
PROBE = ROOT / "harness" / "poll_handoff_probe.c"
RUNTIME = ROOT / "runtime"
# -O2, as tools/s4/link_s4.py compiles the runtime (OPT).
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]


def zig_env(local: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(local),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The probe's guest object, and a function (wrap, wrapper) -> the link's
    CompletedProcess and binary: with the -wrap flag or without it, with the
    runtime's wrapper or without it."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("poll-handoff-probe")
    env = zig_env(d / "zc")
    guest = d / "guest.o"
    done = subprocess.run([str(ZIG), "cc", *CC, "-DSA2_PROBE_GUEST", "-c", str(PROBE), "-o", str(guest)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    made: dict = {}

    def link(wrap: bool = True, wrapper: bool = True):
        key = (wrap, wrapper)
        if key not in made:
            exe = d / f"probe-{int(wrap)}{int(wrapper)}"
            cmd = [str(ZIG), "cc", *CC, str(PROBE), str(guest), str(RUNTIME / "sa2_threads.c")]
            cmd += [str(RUNTIME / "sa2_poll_handoff.c")] if wrapper else []
            cmd += ["-Wl,-wrap=sub_427ee0"] if wrap else []
            made[key] = (subprocess.run(cmd + ["-o", str(exe)], capture_output=True, text=True,
                                        env=env), exe)
        return made[key]
    return link


def probe(built, scenario: str, **env) -> subprocess.CompletedProcess:
    done, exe = built()
    assert done.returncode == 0, done.stderr
    clean = {k: v for k, v in os.environ.items()
             if k not in ("SA2_POLL_HANDOFF", "SA2_THREADS_BREAK", "SA2_SPIN_YIELDS", "SA2_FRAME_US")}
    return subprocess.run([str(exe), scenario], capture_output=True, text=True, timeout=60,
                          env=dict(clean, **env))


def run(built, scenario: str, **env) -> dict:
    p = probe(built, scenario, **env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def stats(**kw) -> dict:
    s = {"rule": 1, "polls": 0, "not_done": 0, "quiet": 0, "hand_offs": 0, "switched": 0,
         "first_poll": 0, "first_import_call": 0, "first_frame": 0}
    s.update(kw)
    return s


# ------------------------------------------------------------------ the rule on

def test_a_pure_spin_hands_off_at_its_second_poll_and_the_worker_ends_the_wait(built):
    got = run(built, "spin")
    # Poll 1 follows CreateThread and ResumeThread (import calls 1 and 2), and a
    # thread's first poll is never quiet; poll 2 is, and its hand-off runs the
    # worker, which sets the byte before the poll reads it.
    assert (got["done"], got["polls"], got["worker"]) == (True, 2, "finished")
    assert got["stats"] == stats(polls=2, not_done=2, quiet=1, hand_offs=1, switched=1,
                                 first_poll=2, first_import_call=2)
    assert got["virtual_us"] == 0 and got["spin_observations"] == 0


def test_a_sleeping_worker_is_reached_by_the_spin_rule_after_its_threshold(built):
    got = run(built, "sleeper")
    # Poll 2 runs the worker into its 50 ms sleep; polls 3 to 258 find nobody
    # ready, and the 256th such observation moves the clock to the sleeper's
    # deadline (SA2_SPIN_YIELDS), so poll 258's hand-off runs it to the end.
    assert (got["done"], got["polls"], got["worker"]) == (True, 258, "finished")
    assert got["stats"] == stats(polls=258, not_done=258, quiet=257, hand_offs=257, switched=2,
                                 first_poll=2, first_import_call=2)
    assert (got["spin_observations"], got["spin_clock_moves"], got["virtual_us"]) == (256, 1, 50000)


def test_a_poll_on_the_drawing_path_is_never_quiet(built):
    got = run(built, "draw")
    # A frame boundary between polls moves the frame count, so the rule never
    # fires; the worker runs inside the frame waits, as the boot's loads do.
    assert (got["done"], got["polls"], got["frames"], got["worker"]) == (True, 4, 3, "finished")
    assert got["stats"] == stats(polls=4, not_done=3)


def test_an_import_call_between_polls_is_a_runtime_entry(built):
    got = run(built, "import")
    # Not a pure spin, so the rule leaves it alone, even though nothing in it hands
    # off: the rule claims pure spins and nothing else.
    assert (got["done"], got["polls"], got["worker"]) == (False, 64, "ready")
    assert got["stats"] == stats(polls=64, not_done=64)


@pytest.mark.parametrize("scenario", ["done", "noop"])
def test_a_poll_that_finds_the_operation_done_never_hands_off(built, scenario):
    got = run(built, scenario)
    assert (got["done"], got["polls"], got["worker"]) == (True, 2, "none")
    assert got["stats"] == stats(polls=2)


def test_a_poll_that_cannot_read_its_operation_never_hands_off(built):
    got = run(built, "unreadable")
    # Two polls with nothing between them and a worker ready: a readable "not
    # done" would hand off at the second. Unreadable, neither is a wait, so the
    # worker never runs, and the trap the lifted read meets lands where it did.
    assert (got["polls"], got["worker"]) == (2, "ready")
    assert got["stats"] == stats(polls=2)


@pytest.mark.parametrize("value", ["on", "true", "yes", "2", "01"])
def test_a_setting_other_than_0_or_1_refuses_the_run(built, value):
    p = probe(built, "spin", SA2_POLL_HANDOFF=value)
    # strtol read every one of these as a number, and `on` as 0: the rule off with
    # nothing to say so but the record's `rule` (review N4).
    assert p.returncode == 3 and p.stdout == ""
    assert (f"SA2_POLL_HANDOFF={value}: 1 is the guest-poll rule and 0 its break, and no other "
            "value is read") in p.stderr
    assert "trap 1 at 0x00427ee0" in p.stderr      # SA2_TRAP_CONTROL_LEFT


def test_one_is_the_rule_and_an_empty_setting_is_the_default(built):
    assert run(built, "spin", SA2_POLL_HANDOFF="1")["stats"]["hand_offs"] == 1
    assert run(built, "spin", SA2_POLL_HANDOFF="")["stats"]["hand_offs"] == 1


# ------------------------------------------------------------------ the breaks

@pytest.mark.parametrize("scenario", ["spin", "sleeper"])
def test_the_break_brings_the_hang_back(built, scenario):
    got = run(built, scenario, SA2_POLL_HANDOFF="0")
    # Every poll after the first is quiet and none hands off: the worker never
    # gets a slice and the wait ends only at the probe's bound.
    assert (got["done"], got["polls"], got["worker"]) == (False, 1000, "ready")
    assert got["stats"] == stats(rule=0, polls=1000, not_done=1000, quiet=999)


def test_the_break_moves_nothing_where_the_rule_never_fires(built):
    assert run(built, "draw", SA2_POLL_HANDOFF="0")["stats"] == stats(rule=0, polls=4, not_done=3)


def test_a_marker_of_imports_alone_hands_off_on_the_drawing_path(built):
    good, broken = run(built, "draw"), run(built, "draw", SA2_THREADS_BREAK="pollimports")
    assert good["stats"]["hand_offs"] == 0
    # Polls 2 and 3 follow a frame boundary and no import call: quiet to a marker
    # that counts imports alone. On the game that is the "always" pattern, which
    # moved every word hash of the boot (DIAGNOSIS.md section 5).
    assert broken["stats"] == stats(polls=4, not_done=3, quiet=2, hand_offs=2,
                                    first_poll=2, first_import_call=2, first_frame=1)


# ------------------------------------------------------------------ the links

def test_a_link_that_compiles_the_wrapper_and_omits_the_flag_fails(built):
    done, _ = built(wrap=False)
    assert done.returncode != 0
    assert "undefined symbol: __real_sub_427ee0" in done.stderr


def test_a_link_that_passes_the_flag_without_the_wrapper_fails(built):
    done, _ = built(wrapper=False)
    assert done.returncode != 0
    assert "undefined symbol: __wrap_sub_427ee0" in done.stderr


def link_s4_phase_link(monkeypatch, tmp_path, wrap):
    """link_s4.phase_link's command, the phase itself run with link_all.run standing
    in for the linker and a build directory of the test's own."""
    import link_s4 as LS
    ran = []
    monkeypatch.setattr(LS, "OUT", tmp_path / "s4" / "link")
    monkeypatch.setattr(LS.C, "BUILD", tmp_path / "build")
    (tmp_path / "build" / "tag").mkdir(parents=True, exist_ok=True)
    (tmp_path / "build" / "tag" / "ir_00427EE0.o").write_bytes(b"")
    monkeypatch.setattr(LS.L, "run", lambda cmd, **kw: ran.append(cmd) or {
        "rc": 0, "seconds": 0, "peak_child_rss_mb": 0, "stderr_tail": ""})
    state = {"compile_ok": True}
    LS.phase_link(state, "poll-test", "tag", [], wrap=wrap)
    return ran, state


def test_every_link_s4_link_wraps_the_poll(monkeypatch, tmp_path):
    import link_s4 as LS
    # The poll's wrap first; the runtime's other production wraps (runtime/sa2_cri_dsp.c's
    # kernels) after it, and a hook's after those.
    assert LS.PRODUCTION_WRAPS[0] == "sub_427ee0" and "sa2_poll_handoff.c" in LS.RUNTIME_C
    ran, state = link_s4_phase_link(monkeypatch, tmp_path, [])
    assert len(ran) == 1 and "-Wl,-wrap=sub_427ee0" in ran[0]
    assert state["link"]["wrapped"] == LS.PRODUCTION_WRAPS
    ran, state = link_s4_phase_link(monkeypatch, tmp_path, ["sub_433ee0"])
    assert [a for a in ran[0] if a.startswith("-Wl,-wrap=")] == [
        f"-Wl,-wrap={w}" for w in LS.PRODUCTION_WRAPS + ["sub_433ee0"]]
    assert state["link"]["wrapped"] == LS.PRODUCTION_WRAPS + ["sub_433ee0"]


def test_a_hook_may_not_wrap_the_runtimes_poll(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="PRODUCTION_WRAPS"):
        link_s4_phase_link(monkeypatch, tmp_path, ["sub_427ee0"])


def test_the_build_cost_link_reads_the_same_wraps():
    import build_cost as BC
    import link_s4 as LS
    assert BC.PRODUCTION_WRAPS == LS.PRODUCTION_WRAPS
    assert "sa2_poll_handoff.c" in BC.S4_ADDED_C


# ------------------------------------------------------------------ the gate

def gate(tmp_path: Path, mutate=None, repin: bool = True) -> tuple[list[str], dict]:
    """The gate on a tree of its own: two replays the rule was accepted on, a third
    registered later whose loads the rule ends (190 hand-offs, recorded at its
    registration), the reach run, a stage boot and the NEW FILE receipt."""
    files = G.good_tree()
    if mutate:
        mutate(files)
    live = G.write_tree(tmp_path, files, repin=repin)
    return G.problems(tmp_path, live)


def test_the_gate_reads_every_gated_record_and_holds_the_accepted_ones_silent(tmp_path):
    found, seen = gate(tmp_path)
    assert found == []
    assert seen == {"records": 6, "silent": ["reach", "replay:boot", "replay:metalharbor",
                                             "replay:metalharbor", "stage-boot:metalharbor"],
                    "newfile": {"hand_offs": 190, "main_menu_at": 1362, "stale": 0}}


def test_a_later_replay_may_hand_off_and_an_accepted_one_may_not(tmp_path):
    # garden hands off 190 times and passes: its registration recorded them. The
    # same counters in the boot, registered the same way, fail check 3.
    def boot_like_garden(f):
        moved = dict(G._ph(f, "run/garden.json"))
        G._ph(f, "run/boot.json").update(moved)
        G._tick(f, "boot")["poll_handoff"].update(moved)
    found, _ = gate(tmp_path, boot_like_garden)
    assert len(found) == 1 and found[0].startswith("boot lifted run (run/boot.json): the "
                                                   "guest-poll rule handed off 190 times")


def test_a_record_must_carry_the_counters_its_registration_recorded(tmp_path):
    found, _ = gate(tmp_path, lambda f: G._ph(f, "run/garden.json").update(polls=299))
    assert len(found) == 1 and "garden lifted run (run/garden.json): its counters" in found[0]
    assert "are not the ones its registration recorded" in found[0]


@pytest.mark.parametrize("change,needle", [
    (lambda r: r["threads"].pop("poll_handoff"), "has no threads.poll_handoff"),
    (lambda r: r["threads"]["poll_handoff"].update(polls=0), "0 wrapped polls ran"),
    (lambda r: r["threads"]["poll_handoff"].update(rule=0), "was 0 (SA2_POLL_HANDOFF), not on"),
])
def test_the_gate_fails_a_record_without_the_wrapper(tmp_path, change, needle):
    found, _ = gate(tmp_path, lambda f: change(f["records"]["run/stage.json"]))
    assert len(found) == 1 and found[0].startswith(
        "the metalharbor stage boot (run/stage.json): ") and needle in found[0]


def test_a_stale_new_file_receipt_names_what_moved(tmp_path):
    found, seen = gate(tmp_path, lambda f: f["live"]["pipeline"].update(
        {"tools/s5/poll_handoff_check.py": "9" * 64}))
    assert seen["newfile"]["stale"] == 1 and len(found) == 1
    assert found[0].startswith("the NEW FILE receipt is stale: "
                               "['pipeline.tools/s5/poll_handoff_check.py'] moved since it ran")


@pytest.mark.parametrize("mutate,ids", [
    (lambda f: f["stage_boot"]["stages"].clear(), ["stage-boot:metalharbor"]),
    (lambda f: f["registry"]["replays"].remove({"name": "boot"}), ["replay:boot"]),
    (lambda f: f["acceptance"]["silent"].append("replay:chao"), ["replay:chao"]),
])
def test_a_run_held_silent_with_no_record_read_fails(tmp_path, mutate, ids):
    """An id the acceptance holds silent must match a record that was read: dropped
    from its receipt, de-registered, or never gated, it would otherwise pass check 3
    unseen (the 2026-10-02 confirm review, NIT 1)."""
    found, _ = gate(tmp_path, mutate)
    assert found == [f"{i}: held silent (Documentation/receipts/s5-poll-handoff-v1.json "
                     "`silent`, ACCEPTED_SILENT) and no gated record of it was read, so nothing "
                     "shows it handed off at no poll" for i in ids]


@pytest.mark.parametrize("name,path,run", [
    ("boot", "run/boot.json", "lifted run"),
    ("garden", "run/garden.json", "lifted run")])
def test_a_registration_with_no_reach_fails(tmp_path, name, path, run):
    """The registration's reach ties the record read to it; one that records none
    skipped that half of check 2 and passed with any record (the 2026-10-02 confirm
    review, NIT 2)."""
    found, _ = gate(tmp_path, lambda f: G._tick(f, name).pop("reach"))
    assert found == [f"{name} {run} ({path}): its registration recorded no reach, so nothing "
                     "shows the record read is the registered one; re-register it "
                     "(tools/s4/tick_replay.py --receipt)"]


def test_the_gates_own_falsifications_fail():
    bad, cases = G.falsifications()
    assert bad == [] and cases == 21
