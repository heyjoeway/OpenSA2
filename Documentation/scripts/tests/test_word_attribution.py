"""tools/s4/word_attribution.py: the words compared, the words found differing,
and the owner each is attributed to, on synthetic snapshots with literal
answers."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import word_attribution as WA  # noqa: E402

BASE = 0x01760000


def snapshot(path: Path, base: int, ticks: dict[int, dict[int, int]], size: int = 0x10000) -> Path:
    """An "SA2S" file: one whole-range record per tick, `ticks[t]` the words set."""
    out = bytearray()
    for t, words in ticks.items():
        data = bytearray(size)
        for addr, v in words.items():
            struct.pack_into("<I", data, addr - base, v)
        out += b"SA2S" + struct.pack("<III", t, base, size) + data
    path.write_bytes(bytes(out))
    return path


def header(path: Path, block: int, words: list[int]) -> Path:
    mask = [0] * 512
    for w in words:
        i = (w - block) // 4
        mask[i // 32] |= 1 << (i % 32)
    path.write_text(
        "#define TD_BLOCKS 1u\n"
        f"static const uint32_t td_base[TD_BLOCKS] = {{\n    {block:#010x}u,\n}};\n"
        "static const uint32_t td_mask[TD_BLOCKS][512] = {\n    {"
        + ",".join(f"{m:#x}u" if m else "0" for m in mask) + "},\n};\n")
    return path


def test_the_digest_words_are_read_with_their_zero_mask_words(tmp_path):
    # The first hand attribution read only the non-zero mask words and put
    # every word at the wrong address; the zeros are positions too.
    h = header(tmp_path / "td.h", BASE, [BASE + 0xAD08, BASE + 0xAF08, BASE + 0x3E6C])
    assert WA.digest_words(BASE, h) == [BASE + 0x3E6C, BASE + 0xAD08, BASE + 0xAF08]


def test_a_block_with_no_digest_column_is_refused(tmp_path):
    h = header(tmp_path / "td.h", BASE, [BASE])
    with pytest.raises(SystemExit, match="is not a digest block"):
        WA.digest_words(0x01770000, h)


def test_only_compared_words_that_differ_are_reported(tmp_path):
    a = snapshot(tmp_path / "a.bin", BASE, {300: {}, 400: {BASE + 0xAD08: 1, BASE + 0x10: 7}})
    b = snapshot(tmp_path / "b.bin", BASE, {300: {}, 400: {BASE + 0x10: 9}})
    ta, ga = WA.load(a)
    tb, gb = WA.load(b)
    assert ta == tb == [300, 400]
    d = WA.differing(ga, gb, [300, 400], [BASE + 0xAD08, BASE + 0xAF08])
    assert list(d) == [BASE + 0xAD08]           # 0x10 differs but is not compared
    assert d[BASE + 0xAD08] == {"first_tick": 400, "a": 1, "b": 0, "ticks": [400]}


def test_the_nearest_relocated_base_at_or_below_owns_the_word():
    targets = {BASE + 0xAC7C: [0x007FBCB5], BASE + 0xAD17: [0x007FBCE4], BASE + 0xB000: [0x1]}
    funcs = [{"address": "0x007fbc90", "size": 0x100, "name": "FUN_007fbc90", "region": "cri"}]
    of = WA.function_index(funcs)
    got = WA.attribute(BASE + 0xAD88, targets, of, 0x400)
    assert got == {"base": f"{BASE + 0xAD17:#010x}", "offset": "0x71",
                   "sites": [{"site": "0x007fbce4", "function": "FUN_007fbc90", "region": "cri"}]}
    # Beyond the reach, or with nothing below, it is unattributed, not guessed.
    assert WA.attribute(BASE + 0xAD88, targets, of, 0x10)["base"] is None
    assert WA.attribute(BASE + 0x10, targets, of, 0x400) == {"base": None, "sites": []}


def test_a_site_outside_every_function_has_no_owner():
    of = WA.function_index([{"address": "0x00401000", "size": 16, "name": "f", "region": "game"}])
    assert of(0x00401010) is None
    assert of(0x0040100f)["name"] == "f"
