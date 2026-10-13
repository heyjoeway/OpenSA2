"""The DirectSound device's backing stores and object slots, handed on after a final
Release (runtime/sa2_dsound.c, "the backing stores" and obj_new), held by their unit probe.

A story-mode session stopped itself after 29 minutes on 2026-10-04 with "dsound: the
runtime arena has no room for 131072 bytes of sound-buffer backing store": every buffer
CRI made for a stream took a fresh block of the runtime arena at its first Lock and kept
it past its final Release, so 79 stream buffers and one of 65 536 bytes held 10 420 224 of
the arena's 10 485 760 bytes with four of them alive
(private/generated/s5/input/metalharbor/20261004T195443-3633836-71a2e769/run.json).

harness/dsound_store_probe.c is built with the pinned zig from the production
runtime/sa2_dsound.c and runtime/guest_memory.c, so the arena and its allocator are the
real ones. Each test holds a literal expectation and names the break that moves it:

* **the arena holds what is alive (A1):** CRI's stream pattern, a thousand streams with
  four alive, runs to its end on five 131 072-byte blocks and the 65 536-byte one; under
  SA2_DSOUND_BREAK=noreuse, the device as it was, it stops on the 80th stream with the
  session's own note, objects and bytes;
* **slots go round oldest first (A2):** two thousand buffers one at a time take each of
  the 511 buffer slots once and then the oldest released one each time; noreuse stops at
  the 511th;
* **no block goes to two live buffers (A3):** a seeded mix of creations, duplicates before
  and after the first Lock, Locks left open and final Releases, audited after every step
  from the device's own records; SA2_DSOUND_BREAK=sharefree (a store given up while a
  duplicate holds it) is stopped by name by the device's guard, and sharefree-noguard
  (neither the count nor the guard) is caught by the probe's audit;
* **the addresses are the call sequence's (A4):** a literal sequence names every object
  and store, including a 65 536-byte buffer that is handed a fresh block while a free
  one of 131 072 bytes waits (a block goes only to a buffer of exactly its length);
  the stream loop runs the same twice, which is a determinism check and not a
  regression test: the device before reuse was deterministic too;
* **a use after free stops by name (A5):** a Lock, a DuplicateSoundBuffer, or an Unlock
  through a released buffer once its store is another buffer's with a Lock open, which
  the recorder, stream_dump and the mixer would read as closing that buffer's Lock; and
  **a store left locked is never handed on (A6);**
* **a call after the final Release (A7):** six hundred Releases past zero on one dead
  buffer leave the slot ring in order (the Release that changes nothing, m_Release),
  and a buffer any call reached after its final Release, revived by an AddRef or not,
  never has its slot handed on (obj_new's `stale`);
* **a store's bytes at handout (A8):** a reused block that is not all zero is counted
  (stores_reused_dirty), and none is in CRI's stream pattern, which clears a store
  before its final Release.

Without the pinned zig the build fails rather than skips: a skip would pass with the
arena unheld."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
PROBE = ROOT / "harness" / "dsound_store_probe.c"
RUNTIME = ROOT / "runtime"
# -O2, as tools/s4/link_s4.py compiles the runtime (OPT).
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
SETTINGS = ("SA2_DSOUND_BREAK", "SA2_DSOUND_DEVICE", "SA2_DSOUND_STRICT_CAPS",
            "SA2_DSOUND_WRITE_LEAD_US", "SA2_DSOUND_PRIMARY_MS", "SA2_GUEST_POISON")

ARENA_NOTE = "dsound: the runtime arena has no room for 131072 bytes of sound-buffer backing store"


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    """The built probe: a function (*args, brk=None) -> its JSON."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("dsound-store-probe")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    exe = d / "probe"
    built = subprocess.run([str(ZIG), "cc", *CC, str(PROBE), str(RUNTIME / "sa2_dsound.c"),
                            str(RUNTIME / "guest_memory.c"), "-o", str(exe)],
                           capture_output=True, text=True, env=env)
    assert built.returncode == 0, built.stderr
    clean = {k: v for k, v in os.environ.items() if k not in SETTINGS}

    def run(*args: str, brk: str | None = None) -> dict:
        env_run = dict(clean, **({"SA2_DSOUND_BREAK": brk} if brk else {}))
        p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=120,
                           env=env_run)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)
    return run


def pairs(n: int, objs: int, stores: list[int]) -> list[list[str]]:
    return [[f"0x{objs + 0x40 * i:08x}", f"0x{stores[i]:08x}"] for i in range(n)]


# ------------------------------------------------------------------- A1

def test_a_thousand_streams_hold_only_the_blocks_alive_at_once(probe):
    got = probe("stream", "1000", "4", "131072")
    # what the session saw first, then the device's own counts
    assert (got["trapped"], got["note"], got["iterations"], got["arena_used"]) == (
        False, "", 1000, 65536 + 5 * 131072)
    assert {k: got[k] for k in ("retired", "max_live", "distinct_stores", "violations",
                                "guest_bytes_allocated", "buffers", "objects_built",
                                "stores_reused", "objects_reused", "stores_kept_locked",
                                "calls_on_released", "stores_reused_dirty")} == {
        "retired": 996, "max_live": 5, "distinct_stores": 5, "violations": 0,
        # the 65 536-byte block, then five of 131 072: the most streams alive at once
        "guest_bytes_allocated": 65536 + 5 * 131072,
        "buffers": 1002, "objects_built": 1003, "stores_reused": 995,
        # every slot once (the device, the primary, 510 buffers), then 491 handed on
        "objects_reused": 1003 - 512, "stores_kept_locked": 0, "calls_on_released": 0,
        # CRI clears a store whole before its final Release (0x007C7E3F)
        "stores_reused_dirty": 0}
    # the first five streams where the boot replay's record has them, then the oldest
    # block back: 0x03310000, freed by the first stream's final Release
    assert got["first"] == pairs(8, 0x032500C0, [0x03310000, 0x03330000, 0x03350000, 0x03370000,
                                                 0x03390000, 0x03310000, 0x03330000, 0x03350000])


def test_without_reuse_the_stream_loop_stops_where_the_session_did(probe):
    got = probe("stream", "1000", "4", "131072", brk="noreuse")
    # run.json of the 2026-10-04 session: objects_built 83, 82 sound_buffers, 10 420 224
    # bytes, the same note, on the 80th stream
    assert {k: got[k] for k in ("trapped", "note", "iterations", "guest_bytes_allocated",
                                "arena_used", "buffers", "objects_built", "distinct_stores")} == {
        "trapped": True, "note": ARENA_NOTE, "iterations": 79, "guest_bytes_allocated": 10420224,
        "arena_used": 10420224, "buffers": 82, "objects_built": 83, "distinct_stores": 79}


def test_the_stream_loop_runs_the_same_twice(probe):
    # A determinism check: it passes on the device before reuse too.
    assert probe("stream", "1000", "4", "131072") == probe("stream", "1000", "4", "131072")


# ------------------------------------------------------------------- A2

def test_object_slots_go_round_oldest_released_first(probe):
    got = probe("slots", "2000")
    assert (got["trapped"], got["note"], got["made"]) == (False, "", 2000)
    assert {k: got[k] for k in ("distinct_addresses", "fresh", "fifo_violations",
                                "objects_reused", "arena_used", "stores_reused")} == {
        "distinct_addresses": 511, "fresh": 511, "fifo_violations": 0, "objects_reused": 1489,
        "arena_used": 64, "stores_reused": 1999}


def test_without_reuse_the_slots_run_out(probe):
    got = probe("slots", "2000", brk="noreuse")
    assert (got["trapped"], got["made"], got["note"]) == (
        True, 511, "dsound: the fabricated-object pool of 512 is exhausted with 1 of its objects "
                   "alive, 0 released ones withheld after a call reached them, and no released "
                   "buffer's slot to hand on")


# ------------------------------------------------------------------- A3

def test_no_block_goes_to_two_live_buffers(probe):
    got = probe("audit", "1", "5000")
    assert (got["trapped"], got["note"], got["steps"], got["violations"]) == (False, "", 5000, 0)
    assert {k: got[k] for k in ("made", "duplicates", "released", "released_with_lock_open",
                                "stores_reused", "objects_reused", "stores_kept_locked",
                                "calls_on_released")} == {
        "made": 615,
        "duplicates": 241, "released": 809, "released_with_lock_open": 71, "stores_reused": 406,
        "objects_reused": 345, "stores_kept_locked": 64, "calls_on_released": 0}


@pytest.mark.parametrize("seed", ["7", "1234567"])
def test_no_block_goes_to_two_live_buffers_on_other_seeds(probe, seed):
    got = probe("audit", seed, "5000")
    assert (got["trapped"], got["steps"], got["violations"]) == (False, 5000, 0)
    assert got["stores_reused"] and got["objects_reused"] and got["stores_kept_locked"]


def test_a_store_given_up_while_a_duplicate_holds_it_is_stopped_by_the_guard(probe):
    got = probe("audit", "1", "5000", brk="sharefree")
    assert (got["trapped"], got["steps"], got["note"]) == (
        True, 25, "dsound: the block at 0x03300000 (4800 bytes) is about to go to buffer "
                  "0x03250200, and buffer 0x03250280, which has not had its final Release, "
                  "holds 0x03300000 (4800 bytes): two live buffers would share one store")


def test_without_the_count_and_the_guard_the_audit_sees_two_live_buffers_share(probe):
    got = probe("audit", "1", "5000", brk="sharefree-noguard")
    assert got["trapped"] is False and got["violations"] > 0
    assert got["first_violation"] == ("step 25: live buffers 0x03250200 (family 7) and 0x03250280 "
                                      "(family 8) hold 0x03300000+4800 and 0x03300000+4800")


# ------------------------------------------------------------------- A4

FIXED = ["0x03250000",                        # the device
         "0x03250040", "0x03300000",          # A, 131 072
         "0x03250080", "0x03320000",          # B, 65 536
         "0x032500c0", "0x03300000",          # C: A's block, A released
         "0x03250100", "0x03330000",          # D: fresh
         "0x03250140", "0x03330000",          # E, D's duplicate: D's block
         "0x03250180", "0x03350000",          # F, D released, E holding it: fresh
         "0x032501c0", "0x03330000",          # G, E released: D's block
         "0x03250200", "0x03370000"]          # H, 65 536, G released: not D's 131 072, fresh


def test_the_fixed_sequence_names_its_addresses(probe):
    got = probe("fixed")
    assert (got["trapped"], got["addresses"], got["arena_used"]) == (
        False, FIXED, 3 * 131072 + 2 * 65536)
    assert got["stores_reused"] == 2


def test_the_fixed_sequence_without_reuse_is_the_old_bump(probe):
    got = probe("fixed", brk="noreuse")
    assert (got["trapped"], got["addresses"]) == (
        False, ["0x03250000", "0x03250040", "0x03300000", "0x03250080", "0x03320000",
                "0x032500c0", "0x03330000", "0x03250100", "0x03350000", "0x03250140",
                "0x03350000", "0x03250180", "0x03370000", "0x032501c0", "0x03390000",
                "0x03250200", "0x033b0000"])


def test_the_fixed_sequence_with_the_store_given_up_early_stops_at_f(probe):
    got = probe("fixed", brk="sharefree")
    assert (got["trapped"], got["addresses"][-1]) == (True, "0x03250180")
    assert got["note"] == ("dsound: the block at 0x03330000 (131072 bytes) is about to go to buffer "
                           "0x03250180, and buffer 0x03250140, which has not had its final Release, "
                           "holds 0x03330000 (131072 bytes): two live buffers would share one store")


# ------------------------------------------------------------------- A5, A6

@pytest.mark.parametrize("how,note", [
    ("lock", "dsound: buffer 0x03250040 is locked after its final release; its store 0x03300000 "
             "went back then and this runtime hands it to no one twice"),
    ("dup", "IDirectSound8::DuplicateSoundBuffer from 0x00401000: buffer 0x03250040 has had its "
            "final release, and a duplicate of it would share a store that went back then"),
])
def test_a_use_after_the_final_release_stops_the_run_by_name(probe, how, note):
    got = probe("uaf", how)
    assert (got["trapped"], got["note"], got["answer"]) == (True, note, "0x00000000")


def test_an_unlock_through_a_released_buffer_stops_the_run_by_name(probe):
    got = probe("staleunlock")
    # B is handed A's block and locks it; the Unlock through A is the one call after A's
    # final Release, and it stops there, before the instruments could read it as B's
    assert {k: got[k] for k in ("trapped", "note", "a", "b", "answer", "stores_reused",
                                "calls_on_released")} == {
        "trapped": True,
        "note": "IDirectSoundBuffer8::Unlock from 0x00401000: buffer 0x03250040 has had its "
                "final release, and its store 0x03300000 may be another buffer's by now, "
                "whose open Lock this Unlock would close",
        "a": ["0x03250040", "0x03300000"], "b": ["0x03250080", "0x03300000"],
        "answer": "0x00000000", "stores_reused": 1, "calls_on_released": 1}


def test_a_store_left_locked_is_never_handed_on(probe):
    got = probe("kept")
    # A's block stays A's; B's goes free at B's final Release and C takes it
    assert (got["a"], got["b"], got["c"]) == (
        ["0x03250040", "0x03300000"], ["0x03250080", "0x033012c0"], ["0x032500c0", "0x033012c0"])
    assert (got["stores_kept_locked"], got["stores_reused"]) == (1, 1)


# ------------------------------------------------------------------- A7

def test_releases_past_zero_leave_the_slot_ring_in_order(probe):
    got = probe("pastzero")
    # every Release past zero answers 0; the two buffers made once the pool is full take
    # the oldest released slots no call reached after their release: x1's, then x2's
    assert {k: got[k] for k in ("trapped", "note", "x0", "final_release", "past_zero",
                                "past_zero_answer_max", "made", "x1", "x2", "y1", "y2",
                                "objects_reused", "calls_on_released")} == {
        "trapped": False, "note": "", "x0": "0x03250040", "final_release": 0, "past_zero": 600,
        "past_zero_answer_max": 0, "made": 510, "x1": "0x03250080", "x2": "0x032500c0",
        "y1": "0x03250080", "y2": "0x032500c0", "objects_reused": 2, "calls_on_released": 600}


def test_a_buffer_called_after_its_final_release_never_has_its_slot_handed_on(probe):
    got = probe("revive")
    assert {k: got[k] for k in ("trapped", "note", "a", "answers", "b", "c", "made",
                                "a_slot_handed_on", "calls_on_released", "objects_reused")} == {
        "trapped": False, "note": "",
        "a": ["0x03250040", "0x03300000"],
        # the final Release, one past zero, the AddRef that revives it, the Release back
        "answers": [0, 0, 1, 0],
        # A's store went free once, at the first: B has it and C a fresh one
        "b": ["0x03250080", "0x03300000"], "c": ["0x032500c0", "0x03320000"],
        "made": 1100, "a_slot_handed_on": 0,
        # Release, AddRef, Release, QueryInterface, Release after the final one
        "calls_on_released": 5,
        # 508 fresh slots of the 1100, then every released one but A's
        "objects_reused": 1100 - 508}


# ------------------------------------------------------------------- A8

def test_a_store_handed_on_dirty_is_counted(probe):
    got = probe("dirty")
    assert {k: got[k] for k in ("trapped", "a", "b", "b_word", "c", "c_word",
                                "stores_reused", "stores_reused_dirty")} == {
        "trapped": False, "a": "0x03300000",
        # B is handed A's block as A left it; C is handed it after B cleared it
        "b": "0x03300000", "b_word": "0xdeadbeef", "c": "0x03300000", "c_word": "0x00000000",
        "stores_reused": 2, "stores_reused_dirty": 1}
