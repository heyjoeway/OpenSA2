"""The boot replay's field selection, from native evidence only.

`tools/s4/snapshot_fields.py` chooses the words the tick capture digests. The
property that matters is that the lifted run has no say in the choice: a field
list selected for agreement proves nothing. These tests build synthetic
snapshot files and hold each class, the per-tick instability exclusion, and
the header it emits.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import snapshot_fields as SF  # noqa: E402

BASE = 0x00917000
WORDS = 8


def snapfile(path: Path, per_tick: dict[int, list[int]], regions=((0x02800000, 0x1000),)) -> Path:
    out = b""
    for tick, words in sorted(per_tick.items()):
        body = struct.pack(f"<{len(words)}I", *words)
        out += b"SA2S" + struct.pack("<III", tick, BASE, len(body)) + body
    out += b"SA2R" + struct.pack("<III", max(per_tick), len(regions), 0)
    out += b"".join(struct.pack("<II", b, s) for b, s in regions)
    path.write_bytes(out)
    return path


def blockfile(path: Path, per_tick: dict[int, list[int]], base: int = BASE) -> Path:
    out = b""
    for tick, words in sorted(per_tick.items()):
        body = struct.pack(f"<{len(words)}I", *words)
        out += b"SA2B" + struct.pack("<III", tick, base, len(body)) + body
    path.write_bytes(out)
    return path


# word 0 constant; 1 varying and repeatable; 2 varies differently in the two
# runs; 3 varying and holds an address in a committed region; 4..7 constant.
A = {100: [7, 1, 5, 0x02800010, 0, 0, 0, 0], 200: [7, 2, 6, 0x02800020, 0, 0, 0, 0]}
B = {100: [7, 1, 5, 0x02800010, 0, 0, 0, 0], 200: [7, 2, 9, 0x02800020, 0, 0, 0, 0]}


def test_the_native_classes(tmp_path):
    a, regions, base = SF.parse(snapfile(tmp_path / "a.bin", A))
    b, _, _ = SF.parse(snapfile(tmp_path / "b.bin", B))
    cls = SF.classify(a, b, regions, base)
    assert cls == {"varying": [BASE + 4], "unrepeatable": [BASE + 8],
                   "pointer_like": [BASE + 12]}


def test_the_lifted_run_does_not_choose_the_fields(tmp_path):
    """Whatever the lifted file holds, the candidates are the same; only the
    comparison moves."""
    a, regions, base = SF.parse(snapfile(tmp_path / "a.bin", A))
    b, _, _ = SF.parse(snapfile(tmp_path / "b.bin", B))
    agree, _, _ = SF.parse(snapfile(tmp_path / "l1.bin", A))
    differ, _, _ = SF.parse(snapfile(tmp_path / "l2.bin",
                                     {t: [w ^ 1 for w in v] for t, v in A.items()}))
    cls = SF.classify(a, b, regions, base)
    assert SF.compare_lifted(a, agree, base, cls["varying"])["differ"] == 0
    assert SF.compare_lifted(a, differ, base, cls["varying"])["differ"] == 1
    assert cls == SF.classify(a, b, regions, base)


def test_a_word_that_differs_between_runs_between_samples_is_dropped(tmp_path):
    """Word 1 agrees at the sampled ticks and differs at tick 150 in one run."""
    r1 = blockfile(tmp_path / "r1.bin", {150: [7, 1, 0, 0, 0, 0, 0, 0]})
    r2 = blockfile(tmp_path / "r2.bin", {150: [7, 4, 0, 0, 0, 0, 0, 0]})
    words, rep = SF.unstable([r1, r2])
    assert words == {BASE + 4} and rep["first_unstable_tick_by_block"] == {f"{BASE:#010x}": 150}


def test_block_files_skip_the_region_map(tmp_path):
    p = blockfile(tmp_path / "r.bin", {1: [0] * WORDS})
    p.write_bytes(p.read_bytes() + b"SA2R" + struct.pack("<III", 1, 1, 0) + struct.pack("<II", 1, 2))
    assert list(SF.parse_blocks(p)) == [(1, BASE)]


@pytest.mark.parametrize("bad", [b"SA2X" + b"\0" * 12, b"SA2S" + struct.pack("<III", 1, BASE, 64)])
def test_a_damaged_file_is_refused(tmp_path, bad):
    p = tmp_path / "bad.bin"
    p.write_bytes(bad)
    with pytest.raises(SystemExit):
        SF.parse(p)


def test_the_emitted_header_selects_exactly_the_words(tmp_path):
    out = tmp_path / "tick_digest.h"
    words = [0x01750004, 0x0175fffc, 0x01af18c0]
    rep = SF.write_mask(words, [" * native A: x"], out)
    text = out.read_text()
    assert rep == {"blocks": 2, "words": 3, "path": str(out)}
    assert "0x01750000u," in text and "0x01af0000u," in text
    rows = [r for r in text.splitlines() if r.startswith("    {")]
    first = [int(x.rstrip("u"), 0) if x != "0" else 0 for x in rows[0].strip(" {},").split(",")]
    assert first[0] == 1 << 1 and first[511] == 1 << 31


def test_a_word_that_holds_an_address_at_some_early_tick_is_dropped(tmp_path):
    """Two runs agree, but word 1 holds an address in a committed region at tick
    1 and a small integer afterwards: the 100-tick samples would call it an
    integer, and a lifted heap would make it a false divergence."""
    ticks = {1: [7, 0x02800010, 0, 0, 0, 0, 0, 0], 4: [7, 5, 0, 0, 0, 0, 0, 0]}
    region = b"SA2R" + struct.pack("<III", 4, 1, 0) + struct.pack("<II", 0x02800000, 0x1000)
    runs = []
    for name in ("r1.bin", "r2.bin"):
        p = blockfile(tmp_path / name, ticks)
        p.write_bytes(p.read_bytes() + region)
        runs.append(p)
    words, rep = SF.unstable(runs, {BASE + 4, BASE + 8})
    assert words == {BASE + 4} and rep["pointer_like_at_some_tick"] == 1
    assert SF.unstable(runs)[0] == set()        # no candidates given, no pointer scan


def exclusion_record(path: Path, *words) -> Path:
    import json
    path.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": list(words)}))
    return path


EVIDENCE = {"disagree": "r1 vs r2 in d_00910000 from tick 200",
            "agree_without": "r1..r9 agree with the word left out"}


def test_an_exclusion_record_drops_a_candidate_it_names(tmp_path, capsys):
    """Word 1 (BASE + 4) is the one varying, repeatable candidate; the record drops it."""
    rec = exclusion_record(tmp_path / "x.json", dict(EVIDENCE, address=f"{BASE + 4:#010x}"))
    assert SF.exclusions(rec) == {BASE + 4}
    a, b = snapfile(tmp_path / "a.bin", A), snapfile(tmp_path / "b.bin", B)
    assert SF.main([str(a), str(b), "--exclude-words", str(rec)]) == 0
    assert '"excluded_by_plain_repeats": 1' in capsys.readouterr().out.replace("\n", "")


def test_an_exclusion_that_drops_nothing_is_refused(tmp_path):
    """The mask review's W2: the first derivation named two wrong addresses and dropped
    nothing, silently. A word outside the candidates now stops the run by name."""
    rec = exclusion_record(tmp_path / "x.json", dict(EVIDENCE, address=f"{BASE + 16:#010x}"))
    a, b = snapfile(tmp_path / "a.bin", A), snapfile(tmp_path / "b.bin", B)
    with pytest.raises(SystemExit, match=f"{BASE + 16:#010x} are not among the candidates"):
        SF.main([str(a), str(b), "--exclude-words", str(rec)])


@pytest.mark.parametrize("word", [
    dict(EVIDENCE, address=BASE + 4),                   # an integer, not a 0x string
    dict(EVIDENCE, address=f"{BASE + 5:#010x}"),        # not aligned
    dict(EVIDENCE, address=f"{BASE + 4:08x}"),          # hex without its 0x
    {"address": f"{BASE + 4:#010x}", "disagree": "r1 vs r2"},   # no agree_without
    {"address": f"{BASE + 4:#010x}", "agree_without": "r1..r9"},  # no disagree
    f"{BASE + 4:#010x}",                                # not a record at all
])
def test_an_exclusion_without_an_address_and_its_evidence_is_refused(tmp_path, word):
    rec = exclusion_record(tmp_path / "x.json", word)
    with pytest.raises(SystemExit, match="is not an aligned address with 'disagree' and "
                                         "'agree_without' evidence"):
        SF.exclusions(rec)


# --------------------------------------------------------------- the strict mask
#
# `--word-logs` on synthetic captures: word logs written here in the format of
# harness/replay/word_log_core.h (read by the real reader, tools/s4/wordlog.c), each
# beside a receipt and the game's named row. Every value is made up (AGENTS.md
# rule 9): the keyframe is zeros but for the words the tests set.

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402

sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import oracle_mode as OM  # noqa: E402
import save_state as SS  # noqa: E402
import wordlog as WL  # noqa: E402

SECTION, SIZE = 0x00917000, 0x01D59598
TICKS = 12
HEAP = (0x02800000, 0x10000)                  # a committed region: values in it are addresses
OWN = [(0xB0000000, 0x0C000000), (0xFD000000, SIZE)]   # the probe's shadow and buffer (SA2O)
FRAME = 0x0174B038                            # tick_fields.h frame_count: the shift control
W_VAR = 0x01000000        # varies, every run alike: selected
W_ONCE_LATE = 0x01000010  # changes once, at tick 5: varies over ticks 1..N, selected
W_TICK1 = 0x01000020      # changes at tick 1 only: one value over ticks 1..N, not selected
W_UNSTABLE = 0x01000030   # one source differs at tick 7: dropped
W_POINTER = 0x01000040    # holds an address in HEAP at tick 3: dropped
W_OWN_DATA = 0x01000050   # a value inside the probe's own block: data, selected
W_HANDLE = 0x01755220     # XECI entry 0 +0x218, an identity word: dropped
W_EXCLUDED = 0x01000060   # in the exclusion record: dropped
W_STILL = 0x01000070      # never changes
W_APART = 0x01000080      # one value throughout each run, another in s3: apart, not unstable


def base_ticks() -> dict[int, dict[int, int]]:
    out = {}
    for t in range(1, TICKS + 1):
        w = {FRAME: 100 + t, W_VAR: t * 3, W_UNSTABLE: t, W_POINTER: 7 if t != 3 else HEAP[0] + 8,
             W_OWN_DATA: OWN[0][0] + 0x40 + t, W_HANDLE: 0x230 if t >= 4 else 0,
             W_EXCLUDED: t & 1}
        if t >= 5:
            w[W_ONCE_LATE] = 9
        if t >= 1:
            w[W_TICK1] = 5
            w[W_APART] = 5
        out[t] = w
    return out


def section_hash(state: dict[int, int]) -> int:
    """word_log_core.h wl_hash (FNV-1a 64 over the section's 32-bit words) of a
    section that is zero but for `state` (va -> value), each run of zeros at once."""
    prime, mod = 1099511628211, 1 << 64
    h, at = 14695981039346656037, 0
    for i, v in sorted(((va - SECTION) // 4, v) for va, v in state.items()):
        h = h * pow(prime, i - at, mod) % mod
        h = (h ^ v) * prime % mod
        at = i + 1
    return h * pow(prime, SIZE // 4 - at, mod) % mod


def write_log(path: Path, per_tick: dict[int, dict[int, int]], *, own=OWN, clock=0,
              regions: list | None = None) -> Path:
    """A version-2 word log: header, SA2O, the Init map, the keyframe, one SA2D per
    tick holding the words that changed, the end map and SA2E. `clock` moves every
    clock reading, so each run's log is its own bytes (the mask reviews' C1: a
    fixture that hard-linked one log as three runs passed the 3-source rule with
    two); the keyframe's zeros are skipped with a seek, so the file stays sparse."""
    regions = [HEAP] + list(OWN) if regions is None else regions
    rmap = struct.pack("<III", 0, len(regions), 0) + b"".join(struct.pack("<II", *r)
                                                                for r in regions)
    out = bytearray(b"SA2F" + struct.pack("<8I3Q", 2, SECTION, SIZE, 192 << 20, 1, TICKS, 16, 0,
                                          10_000_000, 1_000 + clock, 5_000 + clock))
    if own is not None:
        out += b"SA2O" + struct.pack("<I", len(own)) + b"".join(struct.pack("<II", *o) for o in own)
    out += b"SA2R" + rmap
    out += b"SA2K" + struct.pack("<III", 0, SECTION, SIZE)
    head = bytes(out)
    out = bytearray()
    state: dict[int, int] = {}
    clk = 5_000 + clock
    for t in range(1, TICKS + 1):
        changed = {va: v for va, v in per_tick.get(t, {}).items() if state.get(va, 0) != v}
        state.update(changed)
        runs: list[tuple[int, list[int]]] = []
        for i in sorted((va - SECTION) // 4 for va in changed):
            if runs and i == runs[-1][0] + len(runs[-1][1]):
                runs[-1][1].append(changed[SECTION + 4 * i])
            else:
                runs.append((i, [changed[SECTION + 4 * i]]))
        out += b"SA2D" + struct.pack("<I", t)
        for first, vals in runs:
            out += struct.pack(f"<II{len(vals)}I", first, len(vals), *vals)
        clk += 16_000
        out += struct.pack("<III", 0xFFFFFFFF, len(runs), len(changed))
        out += struct.pack("<QQQ", clk, clk + 10, clk + 200)
    if b'"SA2V"' in WL.CORE.read_bytes():     # U1's fold: the end-of-run check, then required
        out += b"SA2V" + struct.pack("<3IQ", TICKS, 0, 0, section_hash(state))
    out += b"SA2R" + struct.pack("<I", TICKS) + rmap[4:]
    at = len(out)
    out += b"SA2E" + struct.pack("<IIII", TICKS, 0, 1, 0)
    out += struct.pack("<QQQ", 2_000 + clock, clk + 999, len(head) + SIZE + at)
    with path.open("wb") as f:
        f.write(head)
        f.seek(SIZE, 1)                       # the keyframe: zeros, left as a hole
        f.write(bytes(out))
    return path


CLOCKS = iter(range(1, 1000))


def capture_dir(root: Path, name: str, per_tick, *, idle=120.0, sha="c" * 64,
                defines=("TICK_LIMIT=12",), args=(), own=OWN, clock: int | None = None,
                same_log_as: Path | None = None, regions: list | None = None) -> Path:
    """A capture directory holding its own run's log (a clock no other capture here
    has, unless `clock` repeats one), or with `same_log_as` a hard link of another
    capture's log: the same run's bytes under another name."""
    d = root / name
    d.mkdir(parents=True)
    if same_log_as is not None:
        os.link(same_log_as / "sa2-word-log.bin", d / "sa2-word-log.bin")
        log = d / "sa2-word-log.bin"
    else:
        log = write_log(d / "sa2-word-log.bin", per_tick, own=own,
                        clock=1_000_000 * (next(CLOCKS) if clock is None else clock),
                        regions=regions)
    values = WL.field_values(log)
    names = [n for n, _, _ in WL.named_fields()]
    row = d / "sa2-tick-capture.csv"
    row.write_text(
        "\n".join([",".join(["tick", *names])] + [",".join([str(t), *values[t]])
                                                   for t in sorted(values)]) + "\n")
    probes = [{"mod_id": "tick_probe", "source": "/x/harness/replay/native_tick_probe.c",
               "source_sha256": sha, "defines": list(defines), "dll_sha256": name * 2,
               "headers_sha256": {"tick_probe_core.h": "1" * 64, "tick_digest.h": name}}]
    rec = {"schema": "sa2-oracle-capture/v1", "name": name,
           "created_utc": f"2026-09-25T00:00:00+00:00 ({name})",
           "cwd": "/g/game",
           "inputs": {"built_probes": probes, "enabled_mods": ["tick_probe"],
                      "executable_sha256": "e" * 64, "game_args": list(args),
                      "installation": "/g/install",
                      "modloader_profile": {"Graphics": {"SkipIntro": False},
                                            "EnabledMods": ["tick_probe"]}},
           "session": {"idle_before_run": {"seconds": idle}, "game_environment": {"HOME": "/h"},
                       "saves": {"pin": {"digest": "p" * 64}, "matches_pin": True},
                       "device_profile": {"sha256": "d" * 64}, "winedebug": None,
                       "standard_handles": "stdin an empty pipe"},
           "artifacts": [{"file": "sa2-word-log.bin", "check": {"sha256": WL.sha256(log)}},
                         {"file": row.name, "check": {"sha256": WL.sha256(row)}}]}
    (d / "receipt.json").write_text(json.dumps(rec))
    return d


@pytest.fixture
def saves_ok(monkeypatch):
    """The save pin is save_state's own check, tested there; here every capture
    started from it unless a test says otherwise."""
    monkeypatch.setattr(SS, "native_start_problems", lambda capture, root=None: [])


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Three sources and a held-out capture of one build, one of them cold; and the
    variants the refusals need. Four logs of 31 MB, removed when the module ends."""
    root = tmp_path_factory.mktemp("wordlogs")
    unstable = base_ticks()
    unstable[7][W_UNSTABLE] = 99
    for t in unstable:
        unstable[t][W_APART] = 6
    moved = base_ticks()
    moved[9][W_VAR] = 1234
    s1 = capture_dir(root, "s1", base_ticks(), idle=3.0, clock=500)
    out = {"root": root, "s1": s1,
           "s2": capture_dir(root, "s2", base_ticks(), idle=150.0),
           "s3": capture_dir(root, "s3", unstable, idle=4.0),
           "held": capture_dir(root, "held", base_ticks(), idle=3.0),
           "held_moved": capture_dir(root, "held-moved", moved, idle=3.0),
           "other_build": capture_dir(root, "other-build", base_ticks(), sha="f" * 64),
           "other_args": capture_dir(root, "other-args", base_ticks(), args=("--level", "x")),
           "no_own": capture_dir(root, "no-own", base_ticks(), own=None),
           # One run under another name: s1's log hard-linked, its receipt renamed.
           "s1_again": capture_dir(root, "s1-again", None, idle=150.0, same_log_as=s1),
           # Another run's bytes, but the clock s1's probe read at Init.
           "s1_clock": capture_dir(root, "s1-clock", moved, idle=150.0, clock=500)}
    rec1 = json.loads((s1 / "receipt.json").read_text())
    again = json.loads((out["s1_again"] / "receipt.json").read_text())
    again["created_utc"] = rec1["created_utc"]
    (out["s1_again"] / "receipt.json").write_text(json.dumps(again))
    os.symlink(s1, root / "s1-link")
    out["s1_link"] = root / "s1-link"
    rec = root / "x.json"
    rec.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": [
        dict(EVIDENCE, address=f"{W_EXCLUDED:#010x}")]}))
    out["exclusions"] = rec
    yield out
    shutil.rmtree(root, ignore_errors=True)


def strict(runs, *extra: str, sources=("s1", "s2", "s3"), held="held") -> list[str]:
    argv = ["--word-logs", *[str(runs[s]) for s in sources]]
    if held:
        argv += ["--held-out", str(runs[held])]
    return argv + ["--exclude-words", str(runs["exclusions"]), *extra]


def test_the_strict_mask_is_the_rule_word_by_word(runs, saves_ok, tmp_path):
    header, receipt = tmp_path / "boot.h", tmp_path / "boot.json"
    assert SF.main(strict(runs, "--emit", str(header), "--receipt", str(receipt))) == 0
    assert SF.read_mask(header) == {FRAME, W_VAR, W_ONCE_LATE, W_OWN_DATA}
    rep = json.loads(receipt.read_text())
    # W_APART differs between s1 and s3 and varies in neither: not unstable (the
    # mask-rule review, N2)
    assert rep["counts"] == {"varying": 8, "unstable": 1, "pointer_like": 1, "identity": 1,
                             "excluded": 1, "selected": 4, "blocks": 2}
    assert [s["idle_before_run_seconds"] for s in rep["sources"]] == [3.0, 150.0, 4.0]
    assert [s["cold"] for s in rep["sources"]] == [False, True, False]
    assert rep["held_out_check"]["differing_selected_words"] == 0
    assert all(v["differing"] == 0 for v in rep["digest_agreement"].values())
    # every pair's differences (N6), the pointer rule's two decisions (W3), and
    # whether the runs took two paths through an excluded word (evidence N4)
    assert rep["pair_differences"]["s1 ~ s3"] == {"words": 2, "varying_words": 1}
    assert rep["pair_differences"]["s1 ~ held"] == {"words": 0, "varying_words": 0}
    assert rep["pointer_rule"]["image_values_count_as_pointers"] is False
    assert rep["pointer_rule"]["stable_words_dropped"] == 1
    assert rep["exclusion_exercised"] is False
    assert f" * Receipt: {receipt}" in header.read_text()
    # The header reads back as a word-log mask: its sources, its held-out capture and
    # its records, each hashed, and one build.
    prov = OM.digest_mask_provenance(header, SF.ROOT)    # the record is cited from the tree
    assert prov["method"] == "word logs"
    assert prov["sources"] == [runs["s1"], runs["s2"], runs["s3"]]
    assert prov["held_out"] == [runs["held"]]
    assert set(prov["records"]) == {"identity words", "excluded words"}
    assert prov["receipt"] == receipt


def test_the_mask_is_the_host_s_digest_of_those_words(runs, saves_ok, tmp_path):
    """The digest column the reader computes under the emitted header for the block
    of the three selected test words equals a reference computed here from those
    words alone."""
    header, receipt = tmp_path / "boot.h", tmp_path / "boot.json"
    SF.main(strict(runs, "--emit", str(header), "--receipt", str(receipt)))
    head, rows = WL.read_capture_text(WL.capture_csv(runs["s1"] / "sa2-word-log.bin", header))
    col = head.index("d_01000000") - 1
    assert head[1 + col + 1:] == ["d_01740000"]
    per_tick = base_ticks()
    state: dict[int, int] = {}
    for t in range(1, TICKS + 1):
        state.update(per_tick[t])
        h = 2166136261
        for w in (W_VAR, W_ONCE_LATE, W_OWN_DATA):
            h = ((h ^ state.get(w, 0)) * 16777619) & 0xFFFFFFFF
        assert int(rows[t][col], 16) == h


def test_a_held_out_capture_that_differs_on_a_selected_word_refuses_the_mask(runs, saves_ok,
                                                                            tmp_path):
    header = tmp_path / "boot.h"
    with pytest.raises(SystemExit, match="REFUSED"):
        SF.main(strict(runs, "--emit", str(header), "--receipt", str(tmp_path / "r.json"),
                       held="held_moved"))
    assert not header.exists() and not (tmp_path / "r.json").exists()
    SF.main(strict(runs, "--dry-run", "--out", str(tmp_path), held="held_moved"))
    rep = json.loads((tmp_path / "word-log-mask.json").read_text())
    assert rep["problems"] == ["the held-out capture held-moved differs from the sources on 1 "
                               f"selected words, first {W_VAR:#010x} at tick 9: the mask does "
                               "not repeat"]
    word = rep["held_out_check"]["words"][0]          # the sources' value, then the held-out's
    assert (word["a"], word["b"]) == (27, 1234)


def test_a_two_source_mask_is_refused(runs, saves_ok, tmp_path):
    """The mutation the plan requires: two runs that happen to agree choose nothing."""
    with pytest.raises(SystemExit, match="REFUSED"):
        SF.main(strict(runs, "--emit", str(tmp_path / "m.h"), "--receipt", str(tmp_path / "r.json"),
                       sources=("s1", "s2")))
    SF.main(strict(runs, "--dry-run", "--out", str(tmp_path), sources=("s1", "s2")))
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert problems == ["2 source runs; a mask needs at least 3, so that no two runs that happen "
                        "to agree choose it"]


@pytest.mark.parametrize("variant, where", [
    ("other_build", "probes[0].source_sha256"),
    ("other_args", "configuration.oracle.config.game_args"),
])
def test_a_source_from_another_probe_build_or_configuration_is_refused(runs, saves_ok, tmp_path,
                                                                        variant, where):
    """The other mutation the plan requires. A mask header that differs (the recorded
    tick_digest.h differs in every capture here) is not another build."""
    with pytest.raises(SystemExit, match="REFUSED"):
        SF.main(strict(runs, "--emit", str(tmp_path / "m.h"), "--receipt", str(tmp_path / "r.json"),
                       sources=("s1", "s2", variant)))
    SF.main(strict(runs, "--dry-run", "--out", str(tmp_path), sources=("s1", "s2", variant)))
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert len(problems) == 1 and f"another probe build or configuration" in problems[0] \
        and f" {where}: " in problems[0]


def test_a_mask_with_no_cold_source_is_refused(runs, saves_ok, tmp_path):
    SF.main(strict(runs, "--dry-run", "--out", str(tmp_path), sources=("s1", "s3", "held"),
                   held="s2"))
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert len(problems) == 1 and problems[0].startswith(
        "no source run's receipt records 90 s or more idle before it")


def test_a_log_without_the_probe_s_own_blocks_is_refused(runs, saves_ok, tmp_path):
    """Without SA2O the maps' pointer test would take the probe's blocks for the
    game's memory (the reader review, N3): W_OWN_DATA would read as a pointer."""
    SF.main(strict(runs, "--dry-run", "--out", str(tmp_path), sources=("s1", "s2", "no_own")))
    rep = json.loads((tmp_path / "word-log-mask.json").read_text())
    # U1's fold refuses it in the capture check too, in its own words: this tool's
    # refusal is asked for by name, and every problem is the one capture's
    assert ("no-own: the log names 0 blocks of the probe's own (SA2O), not its shadow and buffer, "
            "so its maps' pointer test would count the probe's memory as the game's (the reader "
            "review, N3)") in rep["problems"]
    assert all(p.startswith("no-own: ") for p in rep["problems"])
    # W_POINTER, which every source's maps flag; W_OWN_DATA is flagged by no-own's
    # maps alone, so under the every-source rule it is compared (the U6 masks review,
    # W2), and this refusal is what keeps the run out
    assert rep["counts"]["pointer_like"] == 1
    assert rep["pointer_rule"]["stable_words_by_sources_flagging"] == {"1": 1, "3": 1}


def test_a_source_that_did_not_start_from_the_pinned_saves_is_refused(runs, monkeypatch, tmp_path):
    monkeypatch.setattr(SS, "native_start_problems",
                        lambda c, root=None: ["started elsewhere"] if Path(c).name == "s2" else [])
    SF.main(strict(runs, "--dry-run", "--out", str(tmp_path)))
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert problems == ["s2: started elsewhere"]


def test_an_exclusion_that_moves_in_no_source_is_refused(runs, saves_ok, tmp_path):
    rec = tmp_path / "x.json"
    rec.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": [
        dict(EVIDENCE, address=f"{W_STILL:#010x}")]}))
    argv = strict(runs, "--dry-run", "--out", str(tmp_path))
    argv[argv.index("--exclude-words") + 1] = str(rec)
    SF.main(argv)
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert len(problems) == 1 and f"{W_STILL:#010x} vary in no source" in problems[0]


def test_the_identity_record_names_the_heap_words_and_cri_s_file_table():
    words = SF.identity_words(SF.IDENTITY)
    assert words[0x01A557C0].startswith("heap") and words[0x01AF19F0].startswith("heap")
    # the in-use word is the game's own flag, compared by its bits (the mask evidence
    # review, C1): tick_digest.h compares entry 0's, 0x01755008
    assert 0x01755008 not in words and 0x01755008 in SF.read_mask(SF.DIGEST_H)
    assert words[0x01755008 + 0x218] == "handle: XECI file table entry 0 +0x218"
    assert words[0x01755008 + 79 * 0x2D8 + 0x27C] == "handle: XECI file table entry 79 +0x27c"
    assert len(words) == 2 + 80 * 4


@pytest.mark.parametrize("doc, why", [
    ({"schema": "x", "words": []}, "not an identity record"),
    ({"schema": SF.IDENTITY_SCHEMA, "words": [{"address": "0x01000001", "kind": "heap",
                                               "evidence": "e"}]}, "not an aligned word"),
    ({"schema": SF.IDENTITY_SCHEMA, "words": [{"address": "0x01000000", "kind": "heap"}]},
     "needs an address, a kind and its evidence"),
    ({"schema": SF.IDENTITY_SCHEMA, "words": [{"address": "0x01000000", "kind": "heap",
                                               "evidence": "e"}] * 2}, "named twice"),
    ({"schema": SF.IDENTITY_SCHEMA, "tables": [{"name": "t", "base": "0x01000000", "stride": "0x8",
                                                "count": 2, "offsets": {"0x8": "handle"},
                                                "evidence": "e"}]}, "offset past its stride"),
    ({"schema": SF.IDENTITY_SCHEMA}, "names no word"),
])
def test_a_malformed_identity_record_is_refused(tmp_path, doc, why):
    p = tmp_path / "id.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match=why):
        SF.identity_words(p)


def test_a_mask_header_reads_back_word_for_word(tmp_path):
    """read_mask against write_mask, over the entries written as a plain 0 (the
    parser that skipped them read two wrong addresses) and the block's last word."""
    words = [0x00917000, 0x0091001c, 0x0175fffc, 0x01755254, 0x02670594]
    SF.write_mask(words, [" * test"], tmp_path / "m.h")
    assert SF.read_mask(tmp_path / "m.h") == set(words)
    assert SF.read_mask(SF.DIGEST_H) and len(SF.read_mask(SF.DIGEST_H)) == int(
        re.search(r"Words: (\d+);", SF.DIGEST_H.read_text()).group(1))


def test_the_snapshot_method_no_longer_writes_a_mask(tmp_path):
    """The user's mask rule (2026-09-24): a mask is chosen from runs configured like the
    comparison runs, and a snapshot run loads a probe they do not."""
    a, b = snapfile(tmp_path / "a.bin", A), snapfile(tmp_path / "b.bin", B)
    with pytest.raises(SystemExit, match="configured exactly like the comparison runs"):
        SF.main([str(a), str(b), "--emit"])
    assert SF.main([str(a), str(b)]) == 0                # its reading of old evidence stays


def test_a_replay_s_mask_and_receipt_have_one_place_each():
    assert SF.mask_header("metalharbor") == SF.ROOT / "harness/replay/masks/metalharbor.h"
    assert SF.mask_receipt("boot") == SF.ROOT / "Documentation/receipts/s4-mask-boot-v1.json"
    with pytest.raises(SystemExit, match="is not a replay name"):
        SF.mask_header("../tick_digest")


# ------------------------------------------------ one run counts once (C1, W1)

def dry(runs, tmp_path, **kw) -> dict:
    SF.main(strict(runs, "--dry-run", "--out", str(tmp_path), *kw.pop("extra", ()), **kw))
    return json.loads((tmp_path / "word-log-mask.json").read_text())


@pytest.mark.parametrize("sources, held, want", [
    # s1's log again, under a name and receipt of its own: one run as two sources
    (("s1", "s2", "s1_again"), "held",
     ["s1 and s1-again share a log sha256", "s1 and s1-again share a receipt created_utc",
      "s1 and s1-again share a clock at Init (ref_init, clk_init)"]),
    # the held-out capture is a source's own run
    (("s1", "s2", "s3"), "s1_again",
     ["s1 and s1-again share a log sha256", "s1 and s1-again share a receipt created_utc",
      "s1 and s1-again share a clock at Init (ref_init, clk_init)"]),
    # a symlink to s1's directory: its receipt names s1
    (("s1", "s2", "s3"), "s1_link",
     ["s1 and s1-link share a directory", "s1 and s1-link share a log sha256",
      "s1 and s1-link share a receipt created_utc",
      "s1 and s1-link share a clock at Init (ref_init, clk_init)",
      "s1-link: its receipt names the run 's1'; a renamed copy of a capture is not another run"]),
    # other bytes, but the clock s1's probe read at Init: a .bin and its .zst look so
    (("s1", "s2", "s3"), "s1_clock",
     ["s1 and s1-clock share a clock at Init (ref_init, clk_init)"]),
])
def test_one_run_counts_once(runs, saves_ok, tmp_path, sources, held, want):
    """The mask reviews' C1 and W1: a capture copied, linked or renamed is one run, as a
    source twice or as a source and the held-out capture."""
    rep = dry(runs, tmp_path, sources=sources, held=held)
    got = [p.replace(": they are one run, which would count twice", "")
           for p in rep["problems"] if "held-out capture" not in p]
    assert got == want
    with pytest.raises(SystemExit, match="REFUSED"):
        SF.main(strict(runs, "--emit", str(tmp_path / "m.h"), "--receipt",
                       str(tmp_path / "r.json"), sources=sources, held=held))
    assert not (tmp_path / "m.h").exists()


def test_a_row_its_receipt_does_not_record_is_refused(runs, saves_ok, tmp_path):
    """The mask evidence review's N6: the log is held to the capture's own row, the
    one its receipt records, not a row put beside it later."""
    d = runs["root"] / "wrong-row"
    if not d.exists():
        shutil.copytree(runs["s2"], d, copy_function=os.link)
        rec = json.loads((d / "receipt.json").read_text())
        rec.update(name="wrong-row", created_utc="2026-09-25T01:00:00+00:00 (wrong-row)")
        for art in rec["artifacts"]:          # the runner collected another row than this one
            if art["file"] == "sa2-tick-capture.csv":
                art["check"]["sha256"] = "0" * 64
        (d / "receipt.json").unlink()
        (d / "receipt.json").write_text(json.dumps(rec))
    runs["wrong_row"] = d
    rep = dry(runs, tmp_path, sources=("s1", "s3", "wrong_row"))
    assert [p for p in rep["problems"] if p.startswith("wrong-row")] == [
        "wrong-row: its receipt does not record sa2-tick-capture.csv at "
        f"{WL.sha256(d / 'sa2-tick-capture.csv')[:12]}, so the row the log is held to may be "
        "another run's (the mask evidence review, N6)"]


def test_an_exclusion_apart_between_runs_but_moving_in_none_is_refused(runs, saves_ok,
                                                                       tmp_path):
    """The mask-rule review's N2: W_APART differs between s1 and s3 and varies in
    neither, so excluding it drops nothing; it used to read as 'unstable' and pass."""
    rec = tmp_path / "x.json"
    rec.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": [
        dict(EVIDENCE, address=f"{W_APART:#010x}")]}))
    argv = strict(runs, "--dry-run", "--out", str(tmp_path))
    argv[argv.index("--exclude-words") + 1] = str(rec)
    SF.main(argv)
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert problems == [f"{rec}: {W_APART:#010x} vary in no source, so excluding them drops "
                        "nothing; the record names only words the runs move"]


def test_an_empty_selection_is_refused(runs, saves_ok, tmp_path):
    """The mask-rule review's N3: a header of TD_BLOCKS 0u compares nothing."""
    rec = tmp_path / "x.json"
    rec.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": [
        dict(EVIDENCE, address=f"{w:#010x}") for w in (FRAME, W_VAR, W_ONCE_LATE, W_OWN_DATA,
                                                       W_EXCLUDED)]}))
    argv = strict(runs, "--dry-run", "--out", str(tmp_path))
    argv[argv.index("--exclude-words") + 1] = str(rec)
    SF.main(argv)
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert problems == ["the rule selects no word, and an empty mask compares nothing"]


def test_the_boot_mask_needs_its_exclusion_record_and_its_comparison(runs, saves_ok, tmp_path,
                                                                    monkeypatch):
    """The mask-rule review's W4 (the tick-369 paths are recorded only with the record)
    and the mask evidence review's W5 (what the new mask stops comparing is recorded
    only with --compare). Here the boot has no mask of its own yet, so its mask is the
    one chosen before word logs (the tree has harness/replay/masks/boot.h since U6:
    test_a_replay_s_own_mask_is_the_one_to_compare_with holds that case)."""
    monkeypatch.setattr(SF, "MASKS", tmp_path / "masks")
    with pytest.raises(SystemExit, match="REFUSED"):
        SF.main(strict(runs, "--replay", "boot", "--emit", str(tmp_path / "boot.h"),
                       "--receipt", str(tmp_path / "boot.json"), "--out", str(tmp_path)))
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert problems == [
        "the boot mask is chosen with --exclude-words harness/replay/masks/boot-exclusions.json, "
        "so its receipt names each run's path through the words plain repeats disagree on (the "
        "mask-rule review, W4)",
        "the boot replay has a mask already (harness/replay/tick_digest.h): pass --compare with "
        "it, so the receipt records every word the new mask stops comparing (the mask evidence "
        "review, W5)"]
    assert not (tmp_path / "boot.h").exists()


def test_a_replay_s_own_mask_is_the_one_to_compare_with(runs, saves_ok, tmp_path, monkeypatch):
    """Once a replay has its own mask (harness/replay/masks/<replay>.h), a new one is
    compared with it, not with the mask chosen before word logs."""
    masks = tmp_path / "masks"
    masks.mkdir()
    (masks / "boot.h").write_text("/* the boot's own mask */\n")
    monkeypatch.setattr(SF, "MASKS", masks)
    with pytest.raises(SystemExit, match="REFUSED"):
        SF.main(strict(runs, "--replay", "boot", "--emit", str(tmp_path / "boot.h"),
                       "--receipt", str(tmp_path / "boot.json"), "--out", str(tmp_path),
                       "--exclude-words", str(SF.ROOT / "harness/replay/masks/boot-exclusions.json"),
                       "--compare", str(SF.ROOT / "harness/replay/tick_digest.h")))
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert (f"the boot replay has a mask already ({masks / 'boot.h'}): pass --compare with it, "
            "so the receipt records every word the new mask stops comparing (the mask evidence "
            "review, W5)") in problems, problems
    assert not (tmp_path / "boot.h").exists()


def test_a_word_log_mask_is_not_written_over_the_tree_s_boot_mask(runs, saves_ok, tmp_path,
                                                                   monkeypatch):
    """The U4 fold review's W1: emitting over harness/replay/tick_digest.h would escape
    the boot's exclusion record and comparison; it is refused, and nothing is written."""
    over = tmp_path / "tick_digest.h"
    over.write_text("/* the mask before word logs */\n")
    monkeypatch.setitem(SF.CURRENT_MASKS, "boot", over)
    with pytest.raises(SystemExit, match="REFUSED"):
        SF.main(strict(runs, "--emit", str(over), "--receipt", str(tmp_path / "r.json"),
                       "--out", str(tmp_path)))
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert any("is a mask chosen before word logs" in p for p in problems), problems
    assert over.read_text() == "/* the mask before word logs */\n"


def test_another_identity_record_is_for_a_dry_run_only(runs, saves_ok, tmp_path):
    """The mask-rule review's N5: the comparer reads the tree's record."""
    other = tmp_path / "id.json"
    other.write_text(SF.IDENTITY.read_text())
    with pytest.raises(SystemExit) as e:
        SF.main(strict(runs, "--identity-words", str(other), "--emit", str(tmp_path / "m.h"),
                       "--receipt", str(tmp_path / "r.json")))
    assert e.value.code == 2 and not (tmp_path / "m.h").exists()
    assert dry(runs, tmp_path, extra=("--identity-words", str(other)))["problems"] == []


def test_a_dropped_word_the_rule_does_not_explain_refuses_the_mask(runs, saves_ok, tmp_path,
                                                                   monkeypatch):
    """The mask evidence review's W5: a word the older mask compares that leaves the
    new one for no reason the rule gives is a defect of the tool, not a result."""
    old = tmp_path / "old.h"
    SF.write_mask([W_VAR], [" * old"], old)
    real = SF.compare_masks

    def unexplained(*a, **k):
        out = real(*a, **k)
        out["dropped_by_reason"]["not explained (a defect of this tool)"] = {
            "words": 1, "top_blocks": {}, "examples": [f"{W_STILL:#010x}"]}
        return out
    monkeypatch.setattr(SF, "compare_masks", unexplained)
    rep = dry(runs, tmp_path, extra=("--compare", str(old)))
    assert rep["problems"] == [f"1 words {old} compares leave the mask for no reason the rule "
                               f"gives, first ['{W_STILL:#010x}']"]


# ------------------------------------------------- the U6 fold: pointers and retirements

W_LOOKS = 0x01000090      # data whose values one source's maps happen to hold
EXTRA = (0x03000000, 0x10000)


def looks_ticks() -> dict[int, dict[int, int]]:
    out = base_ticks()
    for t in out:
        out[t][W_LOOKS] = EXTRA[0] + 4 * t
    return out


def test_a_word_is_dropped_as_pointer_like_only_when_every_source_s_maps_flag_it(saves_ok,
                                                                                 tmp_path):
    """The U6 masks review's W2: flags united over the sources dropped a stable
    word whenever one run's maps held its value. Now one source's maps holding it
    leave it compared, and every source's drop it."""
    mapped = [HEAP, EXTRA] + list(OWN)

    def choose(tag, regions_by_run):
        dirs = [capture_dir(tmp_path, f"{tag}{k}", looks_ticks(), idle=150.0 if k == 0 else 3.0,
                            regions=r) for k, r in enumerate(regions_by_run)]
        header, receipt = tmp_path / f"{tag}.h", tmp_path / f"{tag}.json"
        assert SF.main(["--word-logs", *map(str, dirs[:3]), "--held-out", str(dirs[3]),
                        "--emit", str(header), "--receipt", str(receipt)]) == 0
        return SF.read_mask(header), json.loads(receipt.read_text())["pointer_rule"]

    some, rule = choose("some", [None, mapped, None, None])
    assert W_LOOKS in some and W_POINTER not in some
    assert rule["dropped_when_flagged_by"] == "every source, for a value outside the image"
    assert rule["sources"] == 3
    assert rule["stable_words_by_sources_flagging"] == {"1": 1, "3": 1}
    assert rule["stable_words_flagged_by_some_sources_compared"] == 1
    every, rule = choose("every", [mapped, mapped, mapped, mapped])
    assert W_LOOKS not in every and W_POINTER not in every
    assert some - every == {W_LOOKS} and rule["stable_words_dropped"] == 2
    two, rule = choose("two", [mapped, mapped, None, None])      # all but one: still compared
    assert W_LOOKS in two and rule["stable_words_by_sources_flagging"] == {"2": 1, "3": 1}


W_IMAGE_DATA = 0x010000a0   # data whose values lie in the executable image's range
W_IMAGE_THEN_HEAP = 0x010000b0   # an image value, then an address in HEAP at tick 9
IMAGE_REGION = (SF.IMAGE[0], SF.IMAGE[1] - SF.IMAGE[0])   # the image, as the maps list it


def image_ticks() -> dict[int, dict[int, int]]:
    out = base_ticks()
    for t in out:
        out[t][W_IMAGE_DATA] = 0x00989680 if t >= 6 else 0      # Wine's QPF, from tick 6
        out[t][W_IMAGE_THEN_HEAP] = HEAP[0] + 0x20 if t == 9 else 0x00401000 + 4 * t
    return out


def test_a_word_every_source_holds_alike_is_not_dropped_for_an_image_value(saves_ok, tmp_path):
    """The G4 interrogation, Reviewer B's finding 1: the pointer test counted a value
    inside the executable's image as an address, so a word every source held alike
    (Sofdec's timer unit, 10,000,000 in the image's range) was compared by nothing.
    The image is cut out of every source's maps: such a word is compared, a word
    that also holds an address the host chose is still dropped, and the receipt
    counts the words the image alone flagged."""
    mapped = [HEAP, IMAGE_REGION] + list(OWN)
    dirs = [capture_dir(tmp_path, f"img{k}", image_ticks(), idle=150.0 if k == 0 else 3.0,
                        regions=mapped) for k in range(4)]
    header, receipt = tmp_path / "img.h", tmp_path / "img.json"
    assert SF.main(["--word-logs", *map(str, dirs[:3]), "--held-out", str(dirs[3]),
                    "--emit", str(header), "--receipt", str(receipt)]) == 0
    mask, rep = SF.read_mask(header), json.loads(receipt.read_text())
    assert W_IMAGE_DATA in mask
    assert W_IMAGE_THEN_HEAP not in mask and W_POINTER not in mask
    rule = rep["pointer_rule"]
    assert rule["image_values_count_as_pointers"] is False
    assert rule["dropped_when_flagged_by"] == "every source, for a value outside the image"
    assert rule["stable_words_flagged_by_every_source_for_image_values_alone_compared"] == 1
    assert rule["of_them_flagged_by_some_sources_for_a_host_value_too"] == 0
    assert rule["stable_words_dropped"] == 2
    # Both dropped words hold data on the ticks their address is not there (the
    # review of the G4 round-one fold, Reviewer B's finding 1): W_POINTER holds 7,
    # and W_IMAGE_THEN_HEAP image values, which the cut test does not count.
    assert rule["stable_words_dropped_holding_data_at_some_tick"] == 2
    # Every source flags all three, since the maps hold the image and the heap alike.
    assert rule["stable_words_by_sources_flagging"] == {"3": 3}
    # The rule as it stood (an image value an address) would have dropped the word:
    # the three sources' maps flag it for its image value alone.
    WL = SF._modules()[0]
    for d in dirs[:3]:
        log = d / "sa2-word-log.bin"
        assert W_IMAGE_DATA in WL.pointer_words(log)
        assert W_IMAGE_DATA not in WL.pointer_words(log, cut=SF.IMAGE)
        assert W_IMAGE_THEN_HEAP in WL.pointer_words(log, cut=SF.IMAGE)


def test_a_re_emitted_mask_names_the_words_the_image_rule_added(saves_ok, tmp_path):
    """The receipt's comparison with the mask it replaces gives each added word its
    reason: the image rule's words under their own."""
    mapped = [HEAP, IMAGE_REGION] + list(OWN)
    dirs = [capture_dir(tmp_path, f"re{k}", image_ticks(), idle=150.0 if k == 0 else 3.0,
                        regions=mapped) for k in range(4)]
    old = tmp_path / "old.h"
    SF.write_mask(sorted({FRAME, W_VAR, W_ONCE_LATE}), [" * the mask before"], old)
    header, receipt = tmp_path / "re.h", tmp_path / "re.json"
    assert SF.main(["--word-logs", *map(str, dirs[:3]), "--held-out", str(dirs[3]),
                    "--compare", str(old), "--emit", str(header),
                    "--receipt", str(receipt)]) == 0
    got = json.loads(receipt.read_text())["compared_with"]
    why = ("flagged by every source, by at least one for image values alone, compared since "
           "2026-09-27")
    assert got["added_by_reason"][why]["examples"] == [f"{W_IMAGE_DATA:#010x}"]
    assert got["dropped"] == 0


W_IMAGE_SOME_HOST = 0x010000c0   # image values, and at tick 9 one in EXTRA, which one source maps


def test_a_word_some_sources_flag_for_a_host_value_too_is_counted_apart(saves_ok, tmp_path):
    """The review of the G4 round-one fold, Reviewer A's finding 2: a word every
    source flags, one of them for a value outside the image too, is compared under
    the "every source" rule, not for image values alone, and the receipt counts
    it apart from the words the image alone flagged."""
    ticks = image_ticks()
    for t in ticks:
        ticks[t][W_IMAGE_SOME_HOST] = EXTRA[0] + 0x40 if t == 9 else 0x00402000 + 4 * t
    base = [HEAP, IMAGE_REGION] + list(OWN)
    regions = [base + [EXTRA], base, base, base]              # EXTRA in the first source alone
    dirs = [capture_dir(tmp_path, f"sh{k}", ticks, idle=150.0 if k == 0 else 3.0,
                        regions=regions[k]) for k in range(4)]
    old = tmp_path / "old.h"
    SF.write_mask(sorted({FRAME, W_VAR, W_ONCE_LATE}), [" * the mask before"], old)
    header, receipt = tmp_path / "sh.h", tmp_path / "sh.json"
    assert SF.main(["--word-logs", *map(str, dirs[:3]), "--held-out", str(dirs[3]),
                    "--compare", str(old), "--emit", str(header),
                    "--receipt", str(receipt)]) == 0
    mask, rep = SF.read_mask(header), json.loads(receipt.read_text())
    assert W_IMAGE_SOME_HOST in mask and W_IMAGE_DATA in mask
    rule = rep["pointer_rule"]
    assert rule["stable_words_flagged_by_every_source_for_image_values_alone_compared"] == 2
    assert rule["of_them_flagged_by_some_sources_for_a_host_value_too"] == 1
    why = ("flagged by every source, by at least one for image values alone, compared since "
           "2026-09-27")
    assert rep["compared_with"]["added_by_reason"][why]["words"] == 2


def retired_record(path: Path, build: str, runs: list[Path], word: int = W_EXCLUDED) -> Path:
    path.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": [
        dict(EVIDENCE, address=f"{word:#010x}",
             retired={"build": build, "runs": [str(r) for r in runs], "why": "one path"})]}))
    return path


def test_a_word_retired_for_the_runs_build_is_compared(runs, saves_ok, tmp_path):
    """The U6 masks review's W1: a record word retired for the sources' build is
    compared, with every run's path through it recorded; retired for another
    build, the record still drops it."""
    build = OM.build_digest(OM.build_of(runs["s1"]))
    rec = retired_record(tmp_path / "x.json", build, [runs["s1"], runs["held"]])
    header, receipt = tmp_path / "boot.h", tmp_path / "boot.json"
    argv = ["--word-logs", *[str(runs[k]) for k in ("s1", "s2", "s3")], "--held-out",
            str(runs["held"]), "--exclude-words", str(rec)]
    assert SF.main(argv + ["--emit", str(header), "--receipt", str(receipt)]) == 0
    assert SF.read_mask(header) == {FRAME, W_VAR, W_ONCE_LATE, W_OWN_DATA, W_EXCLUDED}
    rep = json.loads(receipt.read_text())
    assert rep["exclusion_record"] == {f"{W_EXCLUDED:#010x}": "retired for this build: compared"}
    got = rep["exclusion_retired"][f"{W_EXCLUDED:#010x}"]
    assert got["runs_read"] == 4 and len(got["paths"]) == 1
    other = retired_record(tmp_path / "y.json", "0" * 64, [runs["s1"]])
    assert SF.main(argv[:-1] + [str(other), "--emit", str(tmp_path / "o.h"), "--receipt",
                                str(tmp_path / "o.json")]) == 0
    assert W_EXCLUDED not in SF.read_mask(tmp_path / "o.h")


def test_a_retired_word_a_named_run_takes_another_path_through_refuses_the_mask(runs, saves_ok,
                                                                                tmp_path):
    """The retirement's premise, held on every choice: a run it names that takes a
    second path through the word refuses the mask."""
    second = base_ticks()
    for t in second:
        second[t][W_EXCLUDED] = 1 - (t & 1)
    other = capture_dir(tmp_path, "second-path", second, idle=3.0)
    build = OM.build_digest(OM.build_of(runs["s1"]))
    rec = retired_record(tmp_path / "x.json", build, [runs["s1"], other])
    SF.main(["--word-logs", *[str(runs[k]) for k in ("s1", "s2", "s3")], "--held-out",
             str(runs["held"]), "--exclude-words", str(rec), "--dry-run", "--out",
             str(tmp_path)])
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert len(problems) == 1 and f"{W_EXCLUDED:#010x} is retired for build {build[:12]}" in \
        problems[0] and "these take 2" in problems[0]


@pytest.mark.parametrize("retired", [
    {"build": "f" * 63, "runs": ["a"], "why": "w"},          # not 64 hex digits
    {"build": "f" * 64, "runs": [], "why": "w"},             # names no run
    {"build": "f" * 64, "runs": ["a"], "why": " "},          # says not why
])
def test_a_retirement_that_does_not_say_all_three_is_refused(tmp_path, retired):
    rec = tmp_path / "x.json"
    rec.write_text(json.dumps({"schema": "sa2-digest-exclusions/v1", "words": [
        dict(EVIDENCE, address=f"{W_EXCLUDED:#010x}", retired=retired)]}))
    with pytest.raises(SystemExit, match="retirement .* must name a build"):
        SF.retirements(rec, "f" * 64)


def test_a_retirement_naming_a_run_of_another_build_refuses_the_mask(runs, saves_ok, tmp_path):
    """W1's premise: a retirement rests on runs of its own build and configuration
    only; a named run of another (the stack control's STACK_PAD) refuses the mask."""
    pad = capture_dir(tmp_path, "pad", base_ticks(), idle=3.0,
                      defines=("TICK_LIMIT=12", "STACK_PAD=8192"))
    build = OM.build_digest(OM.build_of(runs["s1"]))
    rec = retired_record(tmp_path / "x.json", build, [runs["s1"], pad])
    SF.main(["--word-logs", *[str(runs[k]) for k in ("s1", "s2", "s3")], "--held-out",
             str(runs["held"]), "--exclude-words", str(rec), "--dry-run", "--out",
             str(tmp_path)])
    problems = json.loads((tmp_path / "word-log-mask.json").read_text())["problems"]
    assert len(problems) == 1 and "which is not the mask's build and configuration" in \
        problems[0], problems


def test_a_retired_word_the_held_out_run_takes_another_path_through_refuses_the_mask(
        runs, saves_ok, tmp_path):
    """The U6 fold review's SF7: every run of the mask is read through a retired
    word, the held-out capture among them, whether or not the retirement names
    it. Here it names s1 alone, and the held-out run takes a second path."""
    second = base_ticks()
    for t in second:
        second[t][W_EXCLUDED] = 1 - (t & 1)
    held = capture_dir(tmp_path, "held-second-path", second, idle=3.0)
    build = OM.build_digest(OM.build_of(runs["s1"]))
    rec = retired_record(tmp_path / "x.json", build, [runs["s1"]])
    SF.main(["--word-logs", *[str(runs[k]) for k in ("s1", "s2", "s3")], "--held-out",
             str(held), "--exclude-words", str(rec), "--dry-run", "--out", str(tmp_path)])
    doc = json.loads((tmp_path / "word-log-mask.json").read_text())
    retired = [p for p in doc["problems"]
               if f"{W_EXCLUDED:#010x} is retired for build {build[:12]}" in p]
    assert len(retired) == 1 and "these take 2" in retired[0], doc["problems"]
    got = doc["exclusion_retired"][f"{W_EXCLUDED:#010x}"]
    assert got["runs_named"] == 1 and got["runs_read"] == 4
    assert got["path_by_run"]["held-second-path"] != got["path_by_run"]["s1"]
