"""Exercise tools/s4/address_taken.py on a synthetic image with two code sections."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import unittest

import pefile


TOOL_PATH = Path(__file__).resolve().parents[1] / "s4" / "address_taken.py"
SPEC = importlib.util.spec_from_file_location("address_taken_under_test", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
address_taken = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(address_taken)

BASE = 0x00400000
ALIGN = 0x1000
FILE_ALIGN = 0x200

# .text holds the one map function: it stores the PSFD00 function's address
# the way 0x007F926F stores 0x00872650 in the 2012 executable.
#   0x401000  mov dword ptr [0x00403000], 0x00402010   C7 05 disp32 imm32
#   0x40100a  ret
TEXT = (b"\xC7\x05" + struct.pack("<II", 0x00403000, 0x00402010) + b"\xC3").ljust(0x20, b"\xCC")
# PSFD00 holds two functions no map lists, each after int3 padding:
#   0x402010  push ebp; mov ebp, esp; pop ebp; ret     reached by the immediate
#   0x402020  xor eax, eax; ret                         reached by .data's pointer
PSFD00 = (b"\xCC" * 0x10 + b"\x55\x8B\xEC\x5D\xC3").ljust(0x20, b"\xCC") + b"\x33\xC0\xC3"
# .data stores the second PSFD00 function's address at 0x403004.
DATA = struct.pack("<II", 0, 0x00402020)
# Read-only, marked as holding code and not as executable.
CODE_ONLY = b"\xC3"

TEXT_FN = (0x00401000, 11)
IMM_TARGET = 0x00402010
DATA_TARGET = 0x00402020


def reloc_block(page: int, offsets: list[int]) -> bytes:
    entries = [(3 << 12) | o for o in offsets]                  # IMAGE_REL_BASED_HIGHLOW
    if len(entries) % 2:
        entries.append(0)                                       # IMAGE_REL_BASED_ABSOLUTE pad
    return struct.pack("<II", page, 8 + 2 * len(entries)) + struct.pack(f"<{len(entries)}H",
                                                                        *entries)


def synthetic_pe(psfd00: bytes = PSFD00, data: bytes = DATA, data_relocs: tuple[int, ...] = (0x004,)) -> bytes:
    """A PE32 with .text and PSFD00 executable, .data, a code-only section and
    .reloc naming the disp32 and imm32 of .text's store and .data's pointer."""
    relocs = reloc_block(0x1000, [0x002, 0x006]) + reloc_block(0x3000, list(data_relocs))
    sections = [(b".text", 0x60000020, TEXT), (b"PSFD00", 0x60000020, psfd00),
                (b".data", 0xC0000040, data), (b".codeon", 0x40000020, CODE_ONLY),
                (b".reloc", 0x42000040, relocs)]
    headers = 0x200
    table, body = b"", b""
    raw = headers
    for i, (name, chars, data) in enumerate(sections):
        size = -(-len(data) // FILE_ALIGN) * FILE_ALIGN
        table += struct.pack("<8sIIIIIIHHI", name, len(data), ALIGN * (i + 1), size, raw,
                             0, 0, 0, 0, chars)
        body += data.ljust(size, b"\0")
        raw += size
    dirs = [(0, 0)] * 16
    dirs[5] = (ALIGN * len(sections), len(relocs))              # IMAGE_DIRECTORY_ENTRY_BASERELOC
    optional = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII",
                           0x10B, 0, 0, 0x200, 0x200, 0, 0x1000, 0x1000, 0x3000, BASE,
                           ALIGN, FILE_ALIGN, 4, 0, 0, 0, 4, 0, 0,
                           ALIGN * (len(sections) + 1), headers, 0, 2, 0,
                           0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    optional += b"".join(struct.pack("<II", *d) for d in dirs)
    coff = struct.pack("<HHIIIHH", 0x14C, len(sections), 0, 0, 0, len(optional), 0x0102)
    dos = b"MZ".ljust(0x3C, b"\0") + struct.pack("<I", 0x40)
    head = (dos + b"PE\0\0" + coff + optional + table).ljust(headers, b"\0")
    return head + body


class AddressTakenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pe = pefile.PE(data=synthetic_pe(), fast_load=True)
        self.img = bytes(self.pe.get_memory_mapped_image())
        self.relocs = address_taken.relocations(self.pe)

    def test_sections_split_on_execute_or_code(self) -> None:
        code, data = address_taken.executable_sections(self.pe)
        self.assertEqual(code, [(0x401000, 0x20), (0x402000, 0x23), (0x404000, 1)])
        self.assertEqual(data, [(0x403000, 8), (0x405000, 0x18)])

    def test_relocations_are_the_image_list(self) -> None:
        self.assertEqual(self.relocs, [0x401002, 0x401006, 0x403004])

    def test_targets_in_a_second_code_section_are_admitted(self) -> None:
        code, data = address_taken.executable_sections(self.pe)
        res = address_taken.analyse(self.img, self.relocs, [TEXT_FN], [], code, data)
        self.assertEqual(res["targets"], {
            IMM_TARGET: {"imm": [(0x401006, 0x401000)]},
            DATA_TARGET: {"data": [(0x403004, 0x403004)]},
        })
        self.assertEqual(res["verdict"], {IMM_TARGET: "admitted", DATA_TARGET: "admitted"})
        self.assertEqual(res["boundary"], {IMM_TARGET: "int3 padding",
                                           DATA_TARGET: "int3 padding"})

    def test_a_function_at_a_section_start_is_admitted(self) -> None:
        # PSFD00's first byte is a function, stored in .data: the bytes before it
        # are .text's zero fill, so nothing in them ends a function.
        psfd00 = b"\x33\xC0\xC3".ljust(0x20, b"\xCC")              # 0x402000  xor eax, eax; ret
        pe = pefile.PE(data=synthetic_pe(psfd00, struct.pack("<II", 0x00402000, 0), (0x000,)),
                       fast_load=True)
        code, data = address_taken.executable_sections(pe)
        res = address_taken.analyse(bytes(pe.get_memory_mapped_image()), address_taken.relocations(pe),
                                    [TEXT_FN], [], code, data)
        self.assertEqual(res["verdict"][0x00402000], "admitted")
        self.assertEqual(res["boundary"][0x00402000], "a section start")

    def test_text_alone_misses_them(self) -> None:
        # What the tool saw before every executable section counted as code.
        code, data = address_taken.executable_sections(self.pe)
        res = address_taken.analyse(self.img, self.relocs, [TEXT_FN], [], code[:1], data)
        self.assertEqual(res["targets"], {})


if __name__ == "__main__":
    unittest.main()
