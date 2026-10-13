#!/usr/bin/env python3
"""Split selected Ghidra SA2 function exports into private analysis files."""
import argparse
from pathlib import Path
import re


BEGIN = re.compile(r"SA2_(?:NATIVE|CAMERA)_BEGIN entry=0x([0-9A-Fa-f]+) name=(SA2_[A-Za-z0-9_]+)")
END = re.compile(r"SA2_(?:NATIVE|CAMERA)_END entry=0x([0-9A-Fa-f]+)")


def split(log: Path, private_output: Path) -> None:
    private_output.mkdir(parents=True, exist_ok=True)
    active = None
    buffer = []
    for line in log.read_text(errors="replace").splitlines():
        match = BEGIN.search(line)
        if match:
            if active is not None:
                raise ValueError(f"Nested Ghidra export before {active[1]} ended")
            active = match.groups()
            buffer = []
            continue
        match = END.search(line)
        if match:
            if active is None or match.group(1).lower() != active[0].lower():
                raise ValueError("Ghidra export end does not match start")
            text = "\n".join(buffer).strip() + "\n"
            if active[1] + "(" not in text:
                raise ValueError(f"No function body found for {active[1]}")
            path = private_output / f"{active[0]}-{active[1]}.c"
            path.write_text(text)
            print(path, len(buffer), "lines")
            active = None
            buffer = []
            continue
        if active is not None:
            marker = re.search(r"ExportSA2(?:Native|Camera)Functions\.java> ", line)
            if marker:
                line = line[marker.end():]
            buffer.append(line)
    if active is not None:
        raise ValueError(f"Unclosed Ghidra export {active[1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ghidra_log", type=Path)
    parser.add_argument("private_output", type=Path)
    args = parser.parse_args()
    split(args.ghidra_log, args.private_output)


if __name__ == "__main__":
    main()
