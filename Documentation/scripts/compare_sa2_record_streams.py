#!/usr/bin/env python3
"""Omochao Split: find the first difference in normalized CSV records."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import struct
import sys
from typing import Any, Sequence


SCHEMA = "sa2-omochao-split/v1"
WORD = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)\Z")


class StreamError(ValueError):
    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__(report["error"])
        self.report = report


@dataclass(frozen=True)
class Record:
    identity: tuple[str, ...]
    words: dict[str, int]
    line: int


@dataclass(frozen=True)
class Stream:
    path: Path
    fields: tuple[str, ...]
    compared_fields: tuple[str, ...]
    records: tuple[Record, ...]


def invalid(error: str, **details: Any) -> StreamError:
    return StreamError({"schema": SCHEMA, "status": "invalid", "error": error,
                        **details})


def identity_object(fields: Sequence[str], values: Sequence[str]) -> dict[str, str]:
    return dict(zip(fields, values, strict=True))


def parse_word(value: str, path: Path, line: int, field: str) -> int:
    token = value.strip()
    if not WORD.fullmatch(token):
        raise invalid("invalid-uint32", path=str(path), line=line,
                      field=field, value=value)
    base = 16 if token.lower().startswith("0x") else 10
    word = int(token, base)
    if word > 0xFFFFFFFF:
        raise invalid("invalid-uint32", path=str(path), line=line,
                      field=field, value=value)
    return word


def load_stream(path: Path, identity_fields: Sequence[str]) -> Stream:
    try:
        stream = path.open(newline="", encoding="utf-8")
    except OSError as error:
        raise invalid("unreadable-input", path=str(path), message=str(error)) from error

    with stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            raise invalid("missing-header", path=str(path)) from None
        if not header or any(not field for field in header):
            raise invalid("invalid-header", path=str(path))
        duplicate_fields = sorted({field for field in header if header.count(field) > 1})
        if duplicate_fields:
            raise invalid("duplicate-field", path=str(path), fields=duplicate_fields)
        for field in identity_fields:
            if field not in header:
                raise invalid("missing-identity-field", path=str(path), field=field)

        compared_fields = tuple(field for field in header if field not in identity_fields)
        if not compared_fields:
            raise invalid("missing-compared-field", path=str(path))
        positions = {field: index for index, field in enumerate(header)}
        records: list[Record] = []
        seen: dict[tuple[str, ...], int] = {}
        for row in reader:
            line = reader.line_num
            if len(row) != len(header):
                raise invalid("row-width-mismatch", path=str(path), line=line,
                              expected=len(header), actual=len(row))
            identity = tuple(row[positions[field]].strip()
                             for field in identity_fields)
            for field, value in zip(identity_fields, identity, strict=True):
                if not value:
                    raise invalid("missing-identity-value", path=str(path),
                                  line=line, field=field)
            if identity in seen:
                raise invalid(
                    "duplicate-identity", path=str(path),
                    identity=identity_object(identity_fields, identity),
                    first_line=seen[identity], duplicate_line=line,
                )
            seen[identity] = line
            words = {
                field: parse_word(row[positions[field]], path, line, field)
                for field in compared_fields
            }
            records.append(Record(identity, words, line))

    if not records:
        raise invalid("empty-stream", path=str(path))
    return Stream(path, tuple(header), compared_fields, tuple(records))


def location(stream: Stream, record: Record) -> dict[str, Any]:
    return {"path": str(stream.path), "line": record.line}


def value_report(stream: Stream, record: Record, field: str) -> dict[str, Any]:
    word = record.words[field]
    return {
        **location(stream, record),
        "field": field,
        "uint32": word,
        "hex": f"0x{word:08X}",
        "float32": float32_text(word),
    }


def float32_text(word: int) -> str:
    value = struct.unpack("<f", struct.pack("<I", word))[0]
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "-inf" if value < 0 else "inf"
    return format(value, ".9g")


def compare(left: Stream, right: Stream,
            identity_fields: Sequence[str]) -> tuple[int, dict[str, Any]]:
    if left.fields != right.fields:
        raise invalid("schema-mismatch", left_fields=list(left.fields),
                      right_fields=list(right.fields))

    left_by_identity = {record.identity: record for record in left.records}
    right_by_identity = {record.identity: record for record in right.records}
    left_keys = set(left_by_identity)
    right_keys = set(right_by_identity)
    if left_keys != right_keys:
        raise invalid(
            "identity-set-mismatch",
            missing_from_left=[identity_object(identity_fields, identity)
                               for identity in sorted(right_keys - left_keys)],
            missing_from_right=[identity_object(identity_fields, identity)
                                for identity in sorted(left_keys - right_keys)],
        )

    for index, (left_record, right_record) in enumerate(
            zip(left.records, right.records, strict=True)):
        if left_record.identity != right_record.identity:
            raise invalid(
                "reordered-identity", record_index=index,
                left={**location(left, left_record),
                      "identity": identity_object(
                          identity_fields, left_record.identity)},
                right={**location(right, right_record),
                       "identity": identity_object(
                           identity_fields, right_record.identity)},
            )
        for field in left.compared_fields:
            if left_record.words[field] != right_record.words[field]:
                return 1, {
                    "schema": SCHEMA,
                    "status": "different",
                    "record_index": index,
                    "identity": identity_object(
                        identity_fields, left_record.identity),
                    "field": field,
                    "left": value_report(left, left_record, field),
                    "right": value_report(right, right_record, field),
                }

    return 0, {
        "schema": SCHEMA,
        "status": "identical",
        "records": len(left.records),
        "compared_fields": len(left.compared_fields),
        "identity_fields": list(identity_fields),
    }


def text_report(report: dict[str, Any]) -> str:
    status = report["status"]
    if status == "identical":
        return (
            f"OMOCHAO_SPLIT_IDENTICAL records={report['records']} "
            f"compared_fields={report['compared_fields']} identity="
            f"{','.join(report['identity_fields'])}"
        )
    if status == "different":
        identity = ",".join(
            f"{field}={value}" for field, value in report["identity"].items()
        )
        left = report["left"]
        right = report["right"]
        return "\n".join((
            f"OMOCHAO_SPLIT_DIFFERENT record={report['record_index']} "
            f"identity={identity} field={report['field']}",
            f"left {left['path']}:{left['line']}:{left['field']} "
            f"uint32={left['uint32']} hex={left['hex']} "
            f"float32={left['float32']}",
            f"right {right['path']}:{right['line']}:{right['field']} "
            f"uint32={right['uint32']} hex={right['hex']} "
            f"float32={right['float32']}",
        ))
    details = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return f"OMOCHAO_SPLIT_INVALID {details}"


def emit(report: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        target = sys.stderr if report["status"] == "invalid" else sys.stdout
        print(text_report(report), file=target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--identity", action="append", required=True, metavar="FIELD",
        help="CSV field in the compound record identity; repeat in key order",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)",
    )
    parser.add_argument("left", type=Path, help="Reference normalized CSV")
    parser.add_argument("right", type=Path, help="Comparison normalized CSV")
    args = parser.parse_args()
    if len(args.identity) != len(set(args.identity)):
        parser.error("--identity fields must be unique")
    return args


def main() -> int:
    args = parse_args()
    try:
        left = load_stream(args.left, args.identity)
        right = load_stream(args.right, args.identity)
        code, report = compare(left, right, args.identity)
    except StreamError as error:
        emit(error.report, args.format)
        return 2
    emit(report, args.format)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
