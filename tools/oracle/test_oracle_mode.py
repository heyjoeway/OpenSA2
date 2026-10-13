"""tools/oracle/oracle_mode.py on synthetic captures: the mode a capture's own
files show, the strict reading of the vanilla log, and the refusal to compare
two captures produced by different modes. Every value is made up: no byte
here is the executable's or a real run's (AGENTS.md rule 9)."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oracle_mode as OM  # noqa: E402

KEEP = [(0x00401810, 5), (0x00867AE0, 5), (0x00867B00, 5), (0x00867B10, 5)]
KEPT = sorted(f"0x{va:08x}+{n}" for va, n in KEEP)
HEADER = "va,length,loader_bytes,file_bytes,action"


def profile(skip=False, mods=("vanilla_probe", "tick_probe")):
    return {"Graphics": {"SkipIntro": skip}, "EnabledMods": list(mods)}


def capture(tmp: Path, name: str, mods: list[str], rows: list[str] | None = None, *,
            skip=False, args=(), recorded=None, enabled=None, order=None, sha="a" * 64,
            defines=(), collected=True) -> Path:
    d = tmp / name
    d.mkdir()
    built = [{"mod_id": m, "source": f"/x/harness/replay/native_{m}.c", "source_sha256": sha,
              "defines": list(defines) if m == "vanilla_probe" else []} for m in mods]
    for b in built:
        if b["mod_id"] == "vanilla_probe":
            b["source"] = "/x/harness/replay/native_vanilla_probe.c"
    rec = {"schema": "sa2-oracle-capture/v1",
           "inputs": {"built_probes": built, "enabled_mods": list(enabled or mods),
                      "modloader_profile": profile(skip, order or mods), "game_args": list(args)},
           "session": ({"oracle": recorded} if recorded is not None else {})}
    if rows is not None:
        (d / "sa2-vanilla.csv").write_text("\n".join([HEADER, *rows]) + "\n")
        if collected:                    # the artifact row the runner writes for it
            rec["artifacts"] = [{"file": "sa2-vanilla.csv", "check": {"sha256": hashlib.sha256(
                (d / "sa2-vanilla.csv").read_bytes()).hexdigest()}}]
    (d / "receipt.json").write_text(json.dumps(rec))
    return d


def log(iat="iat-kept", kept=KEEP, tick1=True, extra=()):
    rows = [f"0x{va:08x},{n},e9a1a2a3a4,a1b2c3d4e5,kept" for va, n in kept]
    rows += ["0x00426740,5,e9b1b2b3b4,b2c3d4e5f6,restored",
             "0x0042df60,5,e9c1c2c3c4,c3d4e5f6a7,restored"]
    if iat:
        rows += [f"0x00873104,4,f1e2d3c4,c4d3e2f1,{iat}"]
    if tick1:
        rows += ["0x00426740,5,,,tick1-intact", "0x0042df60,5,,,tick1-intact"]
    return rows + list(extra)


V = ["vanilla_probe", "tick_probe"]


def test_no_vanilla_probe_is_the_loader_mode(tmp_path):
    c = capture(tmp_path, "v7a", ["import_probe", "tick_probe"])
    m = OM.mode_of(c)
    assert (m["mode"], m["config"]["skip_intro"], m["config"]["game_args"]) == ("loader", False, [])


def test_the_vanilla_mode_is_what_its_log_shows(tmp_path):
    c = capture(tmp_path, "v3b", V, log(), sha="b" * 64)
    m = OM.mode_of(c)
    assert (m["mode"], m["kept"], m["iat"], m["probe_sha256"]) == ("vanilla", KEPT, "kept", "b" * 64)


def test_kept_ranges_are_normalised_whatever_order_and_case_the_log_uses(tmp_path):
    rows = [f"0X{va:08X},{n},E9A1A2A3A4,A1B2C3D4E5,kept" for va, n in reversed(KEEP)] \
        + log(kept=[])
    assert OM.mode_of(capture(tmp_path, "c", V, rows))["kept"] == KEPT


@pytest.mark.parametrize("iat, tick1, want", [
    ("iat-restored", True, "restored"), ("iat-kept", True, "kept"),
    (None, True, "none"), (None, False, "not-logged")])
def test_the_import_slots_state_is_read_from_the_rows(tmp_path, iat, tick1, want):
    assert OM.mode_of(capture(tmp_path, "c", V, log(iat, tick1=tick1)))["iat"] == want


@pytest.mark.parametrize("rows, why", [
    (log(extra=["0x00000000,0,,,error: the executable could not be opened, twice"]), "leaves the code"),
    (log(extra=["0x00426740,5,e9,a1,protect-failed"]), "leaves the code"),
    (log(extra=["0x00426740,5,e9,a1,Verify-Failed"]), "leaves the code"),
    (log(extra=["0x00426740,5,e9,a1, restored"]), "leaves the code"),
    (log(extra=["0x00426740,5,e9,a1,tick1-rew"]), "leaves the code"),
    (log(extra=["0x00426740,5,e9"]), "five fields"),
    (log(extra=["0x0042zz40,5,e9,a1,restored"]), "not a range"),
    (log(extra=["0x00426740,5,e9,a1,restored"]), "repeats"),
    (log(iat=None, extra=["0x00873104,4,a,b,iat-kept", "0x00873108,4,a,b,iat-restored"]),
     "both kept and restored"),
    (log(tick1=False, extra=["0x00426740,5,,,tick1-intact"]), "second look"),
    ([], "records no range"),
])
def test_a_log_that_leaves_the_code_unknown_is_refused(tmp_path, rows, why):
    with pytest.raises(OM.ModeError, match=why):
        OM.mode_of(capture(tmp_path, "c", V, rows))


def test_a_log_with_another_header_or_a_bom_is_refused(tmp_path):
    c = capture(tmp_path, "c", V, log())
    (c / "sa2-vanilla.csv").write_text("﻿" + (c / "sa2-vanilla.csv").read_text())
    with pytest.raises(OM.ModeError, match="header"):
        OM.mode_of(c)


@pytest.mark.parametrize("kw, why", [
    ({"mods": ["tick_probe", "vanilla_probe"]}, "not first"),
    ({"mods": V, "enabled": ["mirror", "vanilla_probe", "tick_probe"]}, "initialised mirror first"),
    ({"mods": V, "order": ["tick_probe", "vanilla_probe"]}, "initialised tick_probe first"),
    ({"mods": []}, "no built probe"),
])
def test_a_vanilla_probe_that_did_not_run_first_or_no_probe_list_is_refused(tmp_path, kw, why):
    mods = kw.pop("mods")
    with pytest.raises(OM.ModeError, match=why):
        OM.mode_of(capture(tmp_path, "c", mods, log(), **kw))


def test_a_vanilla_probe_without_its_log_and_a_log_without_its_probe_are_refused(tmp_path):
    with pytest.raises(OM.ModeError, match="not collected"):
        OM.mode_of(capture(tmp_path, "a", V))
    with pytest.raises(OM.ModeError, match="names no vanilla probe"):
        OM.mode_of(capture(tmp_path, "b", ["tick_probe"], log()))


def test_slot_rows_without_the_tick1_second_look_are_refused(tmp_path):
    with pytest.raises(OM.ModeError, match="second look never ran"):
        OM.mode_of(capture(tmp_path, "c", V, log("iat-kept", tick1=False)))


def test_a_log_that_is_not_the_one_the_runner_collected_is_refused(tmp_path):
    c = capture(tmp_path, "c", V, log())
    rec = json.loads((c / "receipt.json").read_text())
    rec["artifacts"] = [{"file": "sa2-vanilla.csv", "check": {"sha256": "e" * 64}}]
    (c / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(OM.ModeError, match="not the file the runner collected"):
        OM.mode_of(c)


def test_another_loader_setting_is_another_mode(tmp_path):
    a = capture(tmp_path, "a", V, log())
    b = capture(tmp_path, "b", V, log())
    rec = json.loads((b / "receipt.json").read_text())
    rec["inputs"]["modloader_profile"]["Graphics"]["EnablePauseOnInactive"] = True
    (b / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(OM.ModeError, match="different oracle modes"):
        OM.require_same([a, b])


def test_a_comparison_refuses_a_receipt_whose_recorded_mode_disagrees(tmp_path):
    a = capture(tmp_path, "a", V, log())
    wrong = {**OM.mode_of(a), "iat": "restored"}
    b = capture(tmp_path, "b", V, log(), recorded=wrong)
    with pytest.raises(OM.ModeError, match="records oracle mode"):
        OM.require_same([a, b])


def test_a_receipt_without_a_mod_loader_profile_is_refused(tmp_path):
    c = capture(tmp_path, "c", ["tick_probe"])
    rec = json.loads((c / "receipt.json").read_text())
    del rec["inputs"]["modloader_profile"]
    (c / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(OM.ModeError, match="no Mod Loader profile"):
        OM.mode_of(c)


@pytest.mark.parametrize("b", [
    dict(mods=["tick_probe"], rows=None),                         # loader against vanilla
    dict(mods=V, rows=log("iat-restored")),                       # slots restored against kept
    dict(mods=V, rows=log(kept=KEEP[1:])),                        # one range fewer kept
    dict(mods=V, rows=log(kept=[(0x00401810, 7)] + KEEP[1:])),    # a longer kept range
    dict(mods=V, rows=log(iat=None, tick1=False)),                # a probe that did not log slots
    dict(mods=V, rows=log(), skip=True),                          # the intro skipped
    dict(mods=V, rows=log(), args=["--level", "metalharbor"]),    # the test spawn
])
def test_captures_of_different_modes_are_not_compared(tmp_path, b):
    a = capture(tmp_path, "a", V, log())
    other = capture(tmp_path, "b", b.pop("mods"), b.pop("rows"), **b)
    with pytest.raises(OM.ModeError, match="different oracle modes"):
        OM.require_same([a, other], "the recording and its replay")


def test_captures_of_one_mode_are_compared_and_nothing_is_not(tmp_path):
    a = capture(tmp_path, "a", V, log(), sha="a" * 64)
    b = capture(tmp_path, "b", V + ["import_probe"], log(), sha="c" * 64)
    assert OM.require_same([a, b])["mode"] == "vanilla"
    with pytest.raises(OM.ModeError, match="no capture"):
        OM.require_same([])


def test_a_recorded_mode_is_checked_against_the_files(tmp_path):
    probe = OM.mode_of(capture(tmp_path, "probe", V, log()))
    ok = {**probe, "kept": list(reversed(KEPT))}
    assert OM.recorded_matches(capture(tmp_path, "ok", V, log(), recorded=ok))["iat"] == "kept"
    assert OM.recorded_matches(capture(tmp_path, "old", V, log()))["mode"] == "vanilla"
    with pytest.raises(OM.ModeError, match="records oracle mode"):
        OM.recorded_matches(capture(tmp_path, "bad", V, log(), recorded={**ok, "iat": "restored"}))
    with pytest.raises(OM.ModeError, match="not a recorded mode"):
        OM.recorded_matches(capture(tmp_path, "junk", V, log(), recorded="vanilla"))


def tree(tmp_path, restore_iat=0) -> Path:
    root = tmp_path / "root"
    probe = root / OM.VANILLA_PROBE
    probe.parent.mkdir(parents=True)
    keep = ",\n".join(f"    {{0x{va:08X}u, 0x{va + n:08X}u}}" for va, n in KEEP)
    probe.write_text(f"#ifndef RESTORE_IAT\n#define RESTORE_IAT {restore_iat}\n#endif\n"
                     f"static const uint32_t KEEP[][2] = {{\n{keep}\n}};\n")
    return root


def test_the_vanilla_oracle_as_it_stands_is_the_trees_probe_with_its_defaults(tmp_path):
    root = tree(tmp_path)
    now = hashlib.sha256((root / OM.VANILLA_PROBE).read_bytes()).hexdigest()
    assert OM.vanilla_problems(capture(tmp_path, "ok", V, log(), sha=now), root=root) == []
    old = OM.vanilla_problems(capture(tmp_path, "old", V, log(), sha="d" * 64), root=root)
    assert any("not the one in the tree" in p for p in old)
    exp = OM.vanilla_problems(capture(tmp_path, "exp", V, log(kept=KEEP[1:]), sha=now,
                                      defines=["KEEP_MASK=0xE"]), root=root)
    assert any("an experiment" in p for p in exp) and any("KEEP table" in p for p in exp)
    slots = OM.vanilla_problems(capture(tmp_path, "slots", V, log("iat-restored"), sha=now),
                                root=root)
    assert any("default leaves them kept" in p for p in slots)
    loader = OM.vanilla_problems(capture(tmp_path, "loader", ["tick_probe"]), root=root)
    assert loader == ["loader was taken by the loader oracle, not the vanilla one"]


def test_the_vanilla_oracle_as_it_stands_reads_only_a_log_the_runner_collected(tmp_path):
    """The mode reads a log against the receipt's sha256 when there is one; the
    oracle as it stands requires that there is (the verify-profile review's
    first LOW finding, 2026-09-23)."""
    root = tree(tmp_path)
    now = hashlib.sha256((root / OM.VANILLA_PROBE).read_bytes()).hexdigest()
    assert OM.vanilla_problems(capture(tmp_path, "ok", V, log(), sha=now), root=root) == []
    bare = capture(tmp_path, "bare", V, log(), sha=now, collected=False)
    assert OM.mode_of(bare)["mode"] == "vanilla"
    assert OM.vanilla_problems(bare, root=root) == [
        "bare's receipt records no sha256 for sa2-vanilla.csv, so nothing shows the log is the "
        "one the runner collected"]


def test_the_digest_masks_sources_are_parsed_verified_and_moded(tmp_path):
    root = tmp_path
    caps = []
    for n in ("snap-a", "snap-b"):
        c = capture(tmp_path, n, ["snapshot_probe"])
        (c / "sa2-snapshots.bin").write_bytes(n.encode())
        caps.append(c)
    lines = [f" * native {i}: {c.relative_to(root)}/sa2-snapshots.bin sha256 "
             f"{hashlib.sha256(c.name.encode()).hexdigest()}" for i, c in enumerate(caps)]
    header = tmp_path / "td.h"
    header.write_text("/* GENERATED\n" + "\n".join(lines) + "\n */\n")
    assert OM.digest_mask_sources(header, root) == caps
    assert OM.digest_mask_mode(header, root)["mode"] == "loader"
    (caps[0] / "sa2-snapshots.bin").write_bytes(b"moved")
    with pytest.raises(OM.ModeError, match="no longer hashes"):
        OM.digest_mask_sources(header, root)
    header.write_text("/*\n * native A: a path with spaces/x.bin sha256 " + "0" * 64 + "\n */\n")
    with pytest.raises(OM.ModeError, match="does not parse"):
        OM.digest_mask_sources(header, root)


def test_the_exclusion_record_is_skipped_only_when_it_is_one(tmp_path):
    """The mask review's N1: the "excluded words:" label alone took a capture out of the
    mode check. Only an exclusion record with no receipt beside it is skipped now."""
    root = tmp_path
    c = capture(tmp_path, "snap-a", ["snapshot_probe"])
    (c / "sa2-snapshots.bin").write_bytes(b"snap-a")
    rec = tmp_path / "exclusions.json"
    rec.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": []}))

    def header(*lines):
        h = tmp_path / "td.h"
        h.write_text("/* GENERATED\n" + "\n".join(lines) + "\n */\n")
        return h
    snap = (f" * native 0: {c.relative_to(root)}/sa2-snapshots.bin sha256 "
            f"{hashlib.sha256(b'snap-a').hexdigest()}")
    good = f" * excluded words: exclusions.json sha256 {OM.sha256(rec)}"
    assert OM.digest_mask_sources(header(snap, good), root) == [c]
    disguised = (f" * excluded words: {c.relative_to(root)}/sa2-snapshots.bin sha256 "
                 f"{hashlib.sha256(b'snap-a').hexdigest()}")
    with pytest.raises(OM.ModeError, match="is cited as the excluded words and is not an "
                                           "exclusion record"):
        OM.digest_mask_sources(header(snap, disguised), root)
    # Each half of the test alone (the mask review, N2): a real record beside a receipt,
    # and a file outside any capture with no schema.
    beside = c / "exclusions.json"
    beside.write_text(rec.read_text())
    with pytest.raises(OM.ModeError, match="is not an exclusion record"):
        OM.digest_mask_sources(header(snap, f" * excluded words: {beside.relative_to(root)}"
                                            f" sha256 {OM.sha256(beside)}"), root)
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"words": []}))
    with pytest.raises(OM.ModeError, match="is not an exclusion record"):
        OM.digest_mask_sources(header(snap, f" * excluded words: bare.json sha256"
                                            f" {OM.sha256(bare)}"), root)
    # The record is hashed like any source (N4), and a header naming only the record
    # names no capture (N5).
    h = header(snap, good)
    rec.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": [1]}))
    with pytest.raises(OM.ModeError, match="no longer hashes"):
        OM.digest_mask_sources(h, root)
    with pytest.raises(OM.ModeError, match="names no source capture"):
        OM.digest_mask_sources(header(f" * excluded words: exclusions.json sha256"
                                      f" {OM.sha256(rec)}"), root)


# ------------------------------------------------------------- word-log masks

def logcap(tmp: Path, name: str, *, sha="a" * 64, args=(), defines=("TICK_LIMIT=12",),
           digest_h="1" * 64, cwd="/g/game", idle=120.0) -> Path:
    """A loader-mode capture holding a (made-up) word log its receipt records, built
    with one tick probe whose recorded headers include a mask header, with every
    configuration fact the runner records."""
    c = capture(tmp, name, ["tick_probe"], sha=sha, args=args)
    log = c / "sa2-word-log.bin"
    log.write_bytes(name.encode() * 16)
    rec = json.loads((c / "receipt.json").read_text())
    rec["inputs"]["built_probes"][0].update(
        defines=list(defines), dll_sha256=name,
        headers_sha256={"tick_probe_core.h": "2" * 64, "tick_digest.h": digest_h})
    rec["inputs"].update(executable_sha256="e" * 64, installation="/g/install")
    rec["session"].update(idle_before_run={"seconds": idle},
                          saves={"pin": {"digest": "p" * 64}, "matches_pin": True},
                          game_environment={"HOME": "/h"}, device_profile={"sha256": "d" * 64},
                          winedebug=None, standard_handles="stdin an empty pipe")
    rec["artifacts"] = [{"file": log.name, "check": {"sha256": OM.sha256(log)}}]
    rec.update(cwd=cwd, name=name, created_utc=f"2026-09-25T00:00:00+00:00 ({name})")
    (c / "receipt.json").write_text(json.dumps(rec))
    return c


def edit_receipt(c: Path, **fields) -> None:
    rec = json.loads((c / "receipt.json").read_text())
    rec.update(fields)
    (c / "receipt.json").write_text(json.dumps(rec))


def mask_body(words) -> str:
    """td_base and td_mask in tick_digest.h's shape, selecting `words`."""
    blocks: dict[int, list[int]] = {}
    for a in words:
        blocks.setdefault(a & ~0xFFFF, []).append(a)
    out = [f"#define TD_BLOCKS {len(blocks)}u", "static const uint32_t td_base[TD_BLOCKS] = {"]
    out += [f"    0x{b:08x}u," for b in sorted(blocks)]
    out += ["};", "static const uint32_t td_mask[TD_BLOCKS][512] = {"]
    for b in sorted(blocks):
        m = [0] * 512
        for a in blocks[b]:
            w = (a - b) // 4
            m[w // 32] |= 1 << (w % 32)
        out.append("    {" + ",".join(f"0x{x:x}u" if x else "0" for x in m) + "},")
    return "\n".join(out + ["};", ""])


def mask_header(tmp: Path, sources: list[Path], held: list[Path], *, identity=True,
                build: str | None = None, at: Path | None = None, method="word logs",
                exclusions=False, receipt: dict | None = None, receipt_line=True,
                words=(0x01000000, 0x01000004), ident: Path | None = None) -> Path:
    """A word-log header as snapshot_fields.py --word-logs writes one, with its mask
    receipt beside it; `receipt` overrides the receipt's fields."""
    tree_ident = tmp / "harness" / "replay" / "masks" / "identity-words.json"
    tree_ident.parent.mkdir(parents=True, exist_ok=True)
    tree_ident.write_text(json.dumps({"schema": "sa2-identity-words/v1", "words": []}))
    ident = ident or tree_ident
    first = (sources + held)[0]
    digest = build or OM.build_digest(OM.build_of(first))
    lines = ([f" * Method: {method}. Ticks: 1 to 12."] if method else []) + [
        f" * Probe build: {digest}"]
    lines += [f" * source {k + 1}: {c / 'sa2-word-log.bin'} sha256 "
              f"{OM.sha256(c / 'sa2-word-log.bin')}" for k, c in enumerate(sources)]
    lines += [f" * held out: {c / 'sa2-word-log.bin'} sha256 {OM.sha256(c / 'sa2-word-log.bin')}"
              for c in held]
    if identity:
        lines.append(f" * identity words: {ident} sha256 {OM.sha256(ident)}")
    if exclusions:
        ex = tmp / OM.EXCLUSIONS_REQUIRED["boot"]         # the boot mask's own record
        ex.parent.mkdir(parents=True, exist_ok=True)
        ex.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": []}))
        lines.append(f" * excluded words: {ex} sha256 {OM.sha256(ex)}")
    h = at or tmp / "boot.h"
    h.parent.mkdir(parents=True, exist_ok=True)
    rec_path = h.with_suffix(".json")
    if receipt_line:
        lines.append(f" * Receipt: {rec_path}")
    h.write_text("/* GENERATED\n" + "\n".join(lines) + "\n */\n" + mask_body(words))
    rec = {"schema": "sa2-digest-mask/v1", "emitted": {"sha256": OM.sha256(h)}, "problems": [],
           "held_out_check": {"differing_selected_words": 0},
           "sources": [{"sha256": OM.sha256(c / "sa2-word-log.bin"), "cold": k == 0}
                       for k, c in enumerate(sources)],
           "held_out": ({"sha256": OM.sha256(held[0] / "sa2-word-log.bin")} if held else None),
           "build": digest, "counts": {"selected": len(words)}}
    rec.update(receipt or {})
    rec_path.write_text(json.dumps(rec))
    return h


def test_a_word_log_mask_is_three_sources_a_held_out_capture_and_one_build(tmp_path):
    caps = [logcap(tmp_path, n, digest_h=n * 32) for n in ("s1", "s2", "s3", "h1")]
    h = mask_header(tmp_path, caps[:3], caps[3:])
    assert OM.digest_mask_sources(h, tmp_path) == caps
    p = OM.digest_mask_provenance(h, tmp_path, by_receipt=True)
    assert (p["method"], p["sources"], p["held_out"]) == ("word logs", caps[:3], caps[3:])
    # the recorded tick_digest.h differs in every capture: a mask is the host's
    # input, so it is not another build
    assert p["build"]["probes"][0]["headers"] == {"tick_probe_core.h": "2" * 64}


@pytest.mark.parametrize("variant, where", [
    ({"sha": "b" * 64}, "probes[0].source_sha256"),
    ({"defines": ("TICK_LIMIT=13",)}, "probes[0].defines[0]"),
    ({"args": ("--level", "metalharbor")}, "configuration.oracle.config.game_args"),
    ({"cwd": "/g/other"}, "configuration.cwd"),
])
def test_a_source_from_another_probe_build_is_refused(tmp_path, variant, where):
    caps = [logcap(tmp_path, n) for n in ("s1", "s2")] + [logcap(tmp_path, "s3", **variant)]
    held = [logcap(tmp_path, "h1")]
    with pytest.raises(OM.ModeError, match=r"are not one probe build and configuration: s1 and "
                                           rf"s3 differ at {re.escape(where)}: "):
        OM.digest_mask_sources(mask_header(tmp_path, caps, held), tmp_path)


def test_a_two_source_word_log_mask_is_refused(tmp_path):
    caps = [logcap(tmp_path, n) for n in ("s1", "s2", "h1")]
    with pytest.raises(OM.ModeError, match="chosen from 2 word logs; a mask needs at least 3"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:2], caps[2:]), tmp_path)


@pytest.mark.parametrize("kw, why", [
    ({"held": 0}, "names 0 held-out captures, not one"),
    ({"identity": False}, "cites no identity record"),
    ({"build": "0" * 64}, "its Probe build line 000000000000 does not name its sources' build"),
])
def test_a_word_log_mask_without_its_held_out_capture_record_or_build_is_refused(tmp_path, kw, why):
    caps = [logcap(tmp_path, n) for n in ("s1", "s2", "s3", "h1")]
    held = caps[3:] if kw.get("held", 1) else []
    h = mask_header(tmp_path, caps[:3], held, identity=kw.get("identity", True),
                    build=kw.get("build"))
    with pytest.raises(OM.ModeError, match=re.escape(why)):
        OM.digest_mask_sources(h, tmp_path)


def test_a_mask_mixing_word_logs_with_snapshots_is_refused(tmp_path):
    caps = [logcap(tmp_path, n) for n in ("s1", "s2", "s3", "h1")]
    snap = capture(tmp_path, "snap-a", ["snapshot_probe"])
    (snap / "sa2-snapshots.bin").write_bytes(b"snap-a")
    h = mask_header(tmp_path, caps[:3], caps[3:])
    h.write_text(h.read_text().replace(" */", f" * native A: {snap / 'sa2-snapshots.bin'} sha256 "
                                              f"{OM.sha256(snap / 'sa2-snapshots.bin')}\n */"))
    with pytest.raises(OM.ModeError, match="says Method: word logs and cites "
                                           "sa2-snapshots.bin, which is not a word log"):
        OM.digest_mask_sources(h, tmp_path)


def test_a_comparison_run_is_held_to_the_mask_s_configuration(tmp_path):
    caps = [logcap(tmp_path, n) for n in ("s1", "s2", "s3", "h1")]
    h = mask_header(tmp_path, caps[:3], caps[3:])
    alike = logcap(tmp_path, "replay-a", digest_h="9" * 64)
    assert OM.require_mask_configuration(h, [alike], tmp_path) == OM.build_of(caps[0])
    other = logcap(tmp_path, "replay-b", args=("--level", "metalharbor"))
    with pytest.raises(OM.ModeError, match="replay-b is not configured like boot.h's sources: "
                                           "configuration.oracle.config.game_args"):
        OM.require_mask_configuration(h, [alike, other], tmp_path)
    snap = capture(tmp_path, "snap-a", ["snapshot_probe"])
    (snap / "sa2-snapshots.bin").write_bytes(b"snap-a")
    rec = json.loads((snap / "receipt.json").read_text())
    rec["artifacts"] = [{"file": "sa2-snapshots.bin",
                         "check": {"sha256": OM.sha256(snap / "sa2-snapshots.bin")}}]
    (snap / "receipt.json").write_text(json.dumps(rec))
    old = tmp_path / "td.h"
    old.write_text(f"/*\n * native A: {snap / 'sa2-snapshots.bin'} sha256 "
                   f"{OM.sha256(snap / 'sa2-snapshots.bin')}\n */\n")
    with pytest.raises(OM.ModeError, match="chosen from snapshot runs"):
        OM.require_mask_configuration(old, [alike], tmp_path)


def mask_includes(replay: Path) -> list[str]:
    """Every native probe's include of a mask, directly or through a header it
    includes: tick_digest.h, or any header under masks/ (the mask evidence review,
    N1: the runner records only the headers beside the probe, so a probe including
    masks/boot.h would change with the mask and its build would not)."""
    out = []
    for probe in sorted(replay.glob("native_*.c")):
        seen, todo = set(), [probe]
        while todo:
            code = re.sub(r"/\*.*?\*/", " ", todo.pop().read_text(), flags=re.S)
            for name in re.findall(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', code, re.M):
                if name in OM.MASK_HEADERS or name.replace("\\", "/").startswith("masks/"):
                    out.append(f"{probe.name} includes {name}")
                if name not in seen and (replay / name).is_file():
                    seen.add(name)
                    todo.append(replay / name)
    return out


def test_no_native_probe_reaches_a_mask_header(tmp_path):
    """What leaving MASK_HEADERS out of the build rests on: no native probe includes a
    mask, so a mask change is not a configuration change."""
    assert mask_includes(OM.ROOT / "harness" / "replay") == []
    (tmp_path / "native_x.c").write_text('#include "x_core.h"\n')
    (tmp_path / "x_core.h").write_text('#include "masks/boot.h"\n')
    assert mask_includes(tmp_path) == ["native_x.c includes masks/boot.h"]


# --------------------------------------------- the mask reviews' round-2 folds

def four(tmp: Path) -> list[Path]:
    return [logcap(tmp, n) for n in ("s1", "s2", "s3", "h1")]


def test_one_run_counts_once_when_a_header_is_read(tmp_path):
    """The mask reviews' C1/W1 on the reading side: a source or the held-out capture
    that is another source's run, by its log's bytes, its directory, its receipt's
    creation time or its receipt's name."""
    caps = four(tmp_path)
    ok = mask_header(tmp_path, caps[:3], caps[3:])
    assert OM.digest_mask_sources(ok, tmp_path) == caps
    # the held-out log is source 1's bytes
    (caps[3] / "sa2-word-log.bin").write_bytes((caps[0] / "sa2-word-log.bin").read_bytes())
    rec = json.loads((caps[3] / "receipt.json").read_text())
    edit_receipt(caps[3], artifacts=[{"file": "sa2-word-log.bin",
                                      "check": {"sha256": OM.sha256(caps[0] / "sa2-word-log.bin")}}])
    with pytest.raises(OM.ModeError, match="share a log sha256"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], caps[3:]), tmp_path)
    # source 1's directory again, through a link
    os_link = tmp_path / "s1-link"
    os_link.symlink_to(caps[0])
    with pytest.raises(OM.ModeError, match="share a directory"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], [os_link]), tmp_path)
    # another run's bytes, but source 1's receipt creation time
    h2 = logcap(tmp_path, "h2")
    edit_receipt(h2, created_utc=json.loads((caps[0] / "receipt.json").read_text())["created_utc"])
    with pytest.raises(OM.ModeError, match="share a receipt created_utc"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], [h2]), tmp_path)
    # a renamed copy: its receipt still names the run it was copied from
    h3 = logcap(tmp_path, "h3")
    edit_receipt(h3, name="s1")
    with pytest.raises(OM.ModeError, match="h3's receipt names the run 's1'"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], [h3]), tmp_path)
    assert rec["name"] == "h1"


def test_a_hashed_source_its_receipt_records_otherwise_is_refused(tmp_path):
    """The mask evidence review's W1: without by_receipt the file was hashed and its
    receipt's own record of it never read."""
    caps = four(tmp_path)
    h = mask_header(tmp_path, caps[:3], caps[3:])
    edit_receipt(caps[1], artifacts=[{"file": "sa2-word-log.bin", "check": {"sha256": "0" * 64}}])
    with pytest.raises(OM.ModeError, match="but its capture's receipt records 000000000000"):
        OM.digest_mask_sources(h, tmp_path)


def test_a_header_with_no_cold_source_is_refused(tmp_path):
    """The mask-rule review's W1: the reader holds the idle rule too."""
    caps = [logcap(tmp_path, n, idle=3.0) for n in ("s1", "s2", "s3")] + [logcap(tmp_path, "h1")]
    with pytest.raises(OM.ModeError, match="no source run's receipt records 90 s or more idle"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], caps[3:]), tmp_path)
    assert OM.idle_seconds({"session": {"idle_before_run": {"seconds": 91}}}) == 91.0
    assert OM.idle_seconds({"session": {"idle_before_run_seconds": 90}}) == 90.0
    assert OM.idle_seconds({"session": {"idle_before_run": {"seconds": None}}}) is None


def test_the_method_line_decides_how_a_header_is_read(tmp_path):
    """The mask reviews' W2/W3: a header in masks/ is a word-log mask, and one that says
    Method: word logs cites only word logs; the file names decide nothing."""
    snap = capture(tmp_path, "snap-a", ["snapshot_probe"])
    (snap / "sa2-snapshots.bin").write_bytes(b"snap-a")
    line = (f" * native A: {snap / 'sa2-snapshots.bin'} sha256 "
            f"{OM.sha256(snap / 'sa2-snapshots.bin')}")
    masks = tmp_path / "harness" / "replay" / "masks"
    masks.mkdir(parents=True)
    (masks / "boot.h").write_text(f"/*\n{line}\n */\n")
    with pytest.raises(OM.ModeError, match="sits in harness/replay/masks/"):
        OM.digest_mask_sources(masks / "boot.h", tmp_path)
    caps = four(tmp_path)
    csv = caps[1] / "sa2-tick-capture.csv"
    csv.write_text("tick\n")
    h = tmp_path / "td.h"
    h.write_text(f"/*\n * Method: word logs.\n * source 1: {csv} sha256 {OM.sha256(csv)}\n */\n")
    with pytest.raises(OM.ModeError, match="cites sa2-tick-capture.csv, which is not a word log"):
        OM.digest_mask_sources(h, tmp_path)
    with pytest.raises(OM.ModeError, match="cites word logs, and its Method line says None"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], caps[3:], method=None), tmp_path)


def test_a_header_citing_another_identity_record_is_refused(tmp_path):
    """The mask-rule review's N5: the comparer reads the tree's record."""
    caps = four(tmp_path)
    other = tmp_path / "id.json"
    other.write_text(json.dumps({"schema": "sa2-identity-words/v1", "words": []}))
    with pytest.raises(OM.ModeError, match="not the tree's harness/replay/masks/identity-words"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], caps[3:], ident=other), tmp_path)


def test_the_boot_mask_cites_its_exclusion_record(tmp_path):
    """The mask-rule review's W4: masks/boot.h without the exclusion record names no
    run's path through the tick-369 words."""
    caps = four(tmp_path)
    at = tmp_path / "harness" / "replay" / "masks" / "boot.h"
    with pytest.raises(OM.ModeError, match="the boot mask cites no exclusion record"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], caps[3:], at=at), tmp_path)
    ok = mask_header(tmp_path, caps[:3], caps[3:], at=at, exclusions=True)
    assert OM.digest_mask_sources(ok, tmp_path) == caps
    # A word-log header written over the tree's tick_digest.h is the boot's too
    # (the U4 fold review, W1).
    over = tmp_path / "harness" / "replay" / "tick_digest.h"
    with pytest.raises(OM.ModeError, match="the boot mask cites no exclusion record"):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], caps[3:], at=over), tmp_path)


@pytest.mark.parametrize("kw, why", [
    ({"receipt_line": False}, "names no Receipt"),
    ({"receipt": {"emitted": {"sha256": "0" * 64}}}, "this header's sha256"),
    ({"receipt": {"problems": ["x"]}}, "no problem"),
    ({"receipt": {"held_out_check": {"differing_selected_words": 19}}},
     "a held-out capture that reproduced"),
    ({"receipt": {"sources": []}}, "a cold source"),
    ({"receipt": {"build": "0" * 64}}, "the header's build"),
    ({"receipt": {"held_out": {"sha256": "0" * 64}}}, "the header's held-out log"),
    ({"receipt": {"counts": {"selected": 7}}}, "the header's word count"),
    ({"words": (), "receipt": {"counts": {"selected": 0}}}, "a mask that selects a word"),
    # the fold review's W2: the receipt's schema, and its sources held to the header's
    ({"receipt": {"schema": "x"}}, "its schema"),
    ({"receipt": {"sources": [{"sha256": "0" * 64, "cold": True}] * 3}},
     "the header's source logs"),
])
def test_the_mask_receipt_is_held_to_the_header(tmp_path, kw, why):
    """The mask evidence review's W2 and the mask-rule review's N3: the header's words
    and the receipt that says how they were chosen are read together."""
    caps = four(tmp_path)
    with pytest.raises(OM.ModeError, match=re.escape(why)):
        OM.digest_mask_sources(mask_header(tmp_path, caps[:3], caps[3:], **kw), tmp_path)


def test_a_configuration_fact_a_receipt_does_not_record_is_refused(tmp_path):
    """The mask reviews' W5/W4: a fact missing from two receipts is not a fact they
    share, and WINEDEBUG and the standard handles are compared."""
    a, b = logcap(tmp_path, "a"), logcap(tmp_path, "b")
    rec = json.loads((b / "receipt.json").read_text())
    rec["session"]["winedebug"] = "+seh"
    (b / "receipt.json").write_text(json.dumps(rec))
    assert OM.first_difference(OM.build_of(a), OM.build_of(b)).startswith(
        "configuration.winedebug: null against")
    # the standard handles and the Proton build are compared too (the U4 fold
    # review, W2), each on its own
    for key in ("standard_handles", "proton"):
        one = json.loads(json.dumps(rec))
        one["session"]["winedebug"] = json.loads((a / "receipt.json").read_text())[
            "session"].get("winedebug")
        one["session"][key] = "another"
        (b / "receipt.json").write_text(json.dumps(one))
        assert OM.first_difference(OM.build_of(a), OM.build_of(b)).startswith(
            f"configuration.{key}"), key
    del rec["session"]["winedebug"]
    (b / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(OM.ModeError, match="does not record winedebug"):
        OM.build_of(b)
    del rec["inputs"]["built_probes"][0]["defines"]
    rec["session"]["winedebug"] = None
    (b / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(OM.ModeError, match=r"built_probes\[0\]: the receipt does not record "
                                           "defines"):
        OM.build_of(b)
