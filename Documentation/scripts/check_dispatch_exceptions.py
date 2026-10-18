#!/usr/bin/env python3
"""Gate: every reviewed dispatch exception is tracked, reasoned, and in force.

A named dispatch exception says an edge may dispatch although it is not a
`jmp`. That is a statement about what the guest is allowed to execute, and
design rule 14 requires every such reinterpretation to carry a recorded
reason.

Until 2026-09-21 the reasons were `--allow-target VA=reason` arguments, which
survived only inside the generated `mb_edges.<tag>.c` that the next
regeneration overwrites. One regeneration dropped the exception for
0x00826f60 -- `allow=1` with a reason became `allow=0` with none -- and the
driven run fell from 369 WinMain iterations to 1 with every gate passing. The
reason was eventually recovered out of a surviving object file's
`.rodata.str1.1`, which is not a place a record should have to be recovered
from.

Four things are checked:

1. every entry in `Documentation/dispatch-exceptions.json` has an address, a
   reason and a falsifier, and the reason is not a placeholder;
2. the generated table, when it exists, carries **exactly** the tracked
   entries as its reviewed exceptions -- an entry in the file and not in the
   table means the table is stale, and one in the table and not in the file
   means somebody passed an argument that will not survive;
3. every edge the generated table refuses (an edge into a target whose row is
   a non-`jmp` with no exception, which traps if taken) is named in the file's
   `refused` groups, by target and source, with the same kind, under a reason
   -- and every named edge is still one the table refuses. Issue #16 was such
   an edge that nobody had named: a `jne` from FUN_007059a0 into FUN_007059af,
   its own body, which stopped the game at Pyramid Cave's title card. The
   check was per target until its review found 0x008294d8 `jne 0082CCC5h`
   hidden behind 0x008294a7's named `je` into the same target;
4. the falsifications below fail the gate.

Usage: .venv/bin/python Documentation/scripts/check_dispatch_exceptions.py
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECL = ROOT / "Documentation" / "dispatch-exceptions.json"
GEN = ROOT / "private" / "generated" / "s4" / "link"

REQUIRED = ("va", "reviewed", "why", "evidence", "falsifier", "decoded")

EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"

# Every field of `decoded`, and how to derive it from the executable. They are
# checked rather than read because schema v1's prose said the reaching
# instruction was int3 padding when it is `push esp` -- and mb_edges' own
# generated comment on the same row said `push esp` while the hand-written
# reason beside it did not. A described byte that can contradict the byte is
# not evidence.
DECODED_FIELDS = ("source_va", "source_byte", "source_mnemonic",
                  "source_flow_control", "target_bytes", "target_mnemonic",
                  "target_flow_control", "first_padding_va",
                  "first_padding_byte")
NON_REASONS = ("todo", "tbd", "later", "not sure", "unknown", "temporary",
               "for now", "placeholder", "workaround")
MIN_WHY = 40


def decode_at(target: int) -> dict:
    """Decode the exception's target and the instruction that falls into it.

    The reaching instruction is found by decoding backwards the only way a
    linear decode can: from a little way above the target, taking the decode
    that lands exactly on it. That is the same linear decode `mb_edges.py`
    does, which is the point -- this must agree with the tool that produced
    the edge, not with a cleverer disassembler that would disagree with both.
    """
    if not EXE.is_file():
        raise SystemExit(f"{EXE.relative_to(ROOT)} is absent, so the decoded "
                         "evidence cannot be checked. A skip is not a pass.")
    import pefile                                        # noqa: PLC0415
    from iced_x86 import Decoder, FlowControl, Formatter, FormatterSyntax  # noqa: PLC0415

    pe = pefile.PE(str(EXE), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    window_lo = target - 0x20
    data = None
    for sec in pe.sections:
        lo = base + sec.VirtualAddress
        if lo <= window_lo and target < lo + sec.Misc_VirtualSize:
            raw = bytes(sec.get_data())
            data = raw[window_lo - lo:window_lo - lo + 0x40]
            break
    if data is None:
        raise SystemExit(f"{target:#010x} is in no section of the executable")

    names = {v: k for k, v in vars(FlowControl).items() if isinstance(v, int)}
    fmt = Formatter(FormatterSyntax.NASM)
    got: dict[str, str] = {}
    prev = None
    for ins in Decoder(32, data, ip=window_lo):
        if ins.ip == target:
            got["target_mnemonic"] = fmt.format(ins)
            got["target_flow_control"] = names.get(ins.flow_control, "?")
            off = ins.ip - window_lo
            got["target_bytes"] = " ".join(f"{b:02x}" for b in data[off:off + ins.len])
            if prev is not None:
                poff = prev.ip - window_lo
                got["source_va"] = f"{prev.ip:#010x}"
                got["source_byte"] = f"{data[poff]:#04x}"
                got["source_mnemonic"] = fmt.format(prev)
                got["source_flow_control"] = names.get(prev.flow_control, "?")
            nxt = ins.ip + ins.len
            got["first_padding_va"] = f"{nxt:#010x}"
            got["first_padding_byte"] = f"{data[nxt - window_lo]:#04x}"
            break
        prev = ins
    if "target_mnemonic" not in got:
        raise SystemExit(f"a linear decode from {window_lo:#010x} never lands on "
                         f"{target:#010x}, so the edge's own source cannot be named")
    return got


def problems(decl_path: Path | None = None, gen_dir: Path | None = None) -> list[str]:
    out: list[str] = []
    gen_root = gen_dir or GEN
    path = decl_path or DECL
    if not path.exists():
        return [f"{path.relative_to(ROOT)} does not exist. Without it every "
                "reviewed exception is a command-line argument again."]
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"the record is not JSON: {e}"]
    if doc.get("schema") != "sa2-recomp-dispatch-exceptions/v2":
        out.append(f"schema is {doc.get('schema')!r}, expected "
                   "'sa2-recomp-dispatch-exceptions/v2'")

    seen: set[tuple[str, str]] = set()
    by_tag: dict[str, set[str]] = {}
    for e in doc.get("exceptions", []):
        va = str(e.get("va", "?"))
        for field in REQUIRED:
            if not str(e.get(field, "")).strip():
                out.append(f"{va} has no {field}")
        try:
            norm = f"0x{int(va, 0):08x}"
        except ValueError:
            out.append(f"{va!r} is not an address")
            continue
        tag = e.get("tag", "")
        if (tag, norm) in seen:
            out.append(f"{norm} is listed twice for tag {tag!r}")
        seen.add((tag, norm))
        by_tag.setdefault(tag, set()).add(norm)
        why = str(e.get("why", "")).strip()
        low = why.lower()
        for phrase in NON_REASONS:
            if phrase in low:
                out.append(f"{norm}: {phrase!r} is not a reason for letting the "
                           "guest execute an edge")
                break
        if why and len(why) < MIN_WHY:
            out.append(f"{norm}: the reason is {len(why)} characters, which is a "
                       "label rather than a reason")

    # The refused groups: a reason each, and every edge named once, in no exception. An edge is
    # (target, source): the runtime looks rows up by target, so every edge into a refused target
    # traps, and a reason for one of them is not a reason for another.
    refused: dict[str, dict[tuple[str, str], str]] = {}
    for g in doc.get("refused", []):
        name = str(g.get("group", "")).strip() or "?"
        tag = g.get("tag", "")
        why = str(g.get("why", "")).strip()
        if not str(g.get("group", "")).strip():
            out.append("a refused group has no name")
        if len(why) < MIN_WHY:
            out.append(f"refused group {name!r}: the reason is {len(why)} characters, which "
                       "is a label rather than a reason")
        for phrase in NON_REASONS:
            if phrase in why.lower():
                out.append(f"refused group {name!r}: {phrase!r} is not a reason for leaving "
                           "an edge trapping")
                break
        if not g.get("rows"):
            out.append(f"refused group {name!r} names no rows")
        for r in g.get("rows", []):
            try:
                norm = f"0x{int(str(r.get('va')), 0):08x}"
                src = f"0x{int(str(r.get('source')), 0):08x}"
            except ValueError:
                out.append(f"refused group {name!r}: {r!r} has no address or source")
                continue
            if not str(r.get("kind", "")).strip():
                out.append(f"refused group {name!r}: {norm} has no kind")
            if (norm, src) in refused.setdefault(tag, {}):
                out.append(f"{norm} from {src} is refused twice for tag {tag!r}")
            refused[tag][(norm, src)] = str(r.get("kind", ""))
            if norm in by_tag.get(tag, set()):
                out.append(f"{norm} is both a reviewed exception and refused for tag {tag!r}")

    # The decoded facts must match the executable, byte for byte.
    for e in doc.get("exceptions", []):
        va = str(e.get("va", "?"))
        dec = e.get("decoded")
        if not isinstance(dec, dict):
            continue                     # already reported as a missing field
        for f in DECODED_FIELDS:
            if not str(dec.get(f, "")).strip():
                out.append(f"{va}: decoded.{f} is missing")
        try:
            got = decode_at(int(va, 0))
        except ValueError:
            continue
        except SystemExit as err:
            out.append(str(err))
            break
        for f in DECODED_FIELDS:
            if f in got and str(dec.get(f, "")).lower() != str(got[f]).lower():
                out.append(
                    f"{va}: decoded.{f} says {dec.get(f)!r} and the executable says "
                    f"{got[f]!r}. The record describes a byte that is not there.")

    # The generated table must agree, for every tag either side names.
    #
    # **Iterating over the file's tags alone was not enough**, and the gate's
    # own falsification caught it: dropping the only entry leaves no tags, so
    # nothing was compared and a table full of untracked exceptions passed.
    # A check that only looks where the record points cannot see what the
    # record has stopped pointing at.
    tags = set(by_tag) | set(refused) | {p.name[len("mb_edges."):-len(".json")]
                          for p in gen_root.glob("mb_edges.*.json")}
    for tag in sorted(t for t in tags if t):
        addrs = by_tag.get(tag, set())
        gen = gen_root / f"mb_edges.{tag}.json"
        if not gen.is_file():
            continue                      # nothing generated for this tag yet
        gdoc = json.loads(gen.read_text())
        got = set((gdoc.get("reviewed_exceptions") or {}))
        # In force means the generated row carries allow=1. A tracked exception
        # whose row became a plain `jmp` does nothing and says it does: the
        # summary line printed in_force from the tracked count until the
        # 2026-09-23 review found 0x00826F60 in exactly that state.
        allowed = {f"0x{int(a, 0):08x}" for a in (gdoc.get("named_exception_targets") or [])}
        for idle in sorted(addrs - allowed):
            out.append(f"{idle} is tracked for tag {tag} but its generated row is not an "
                       "allowed edge -- it is a plain jmp now, or absent -- so the exception "
                       f"is not in force; move it to `retired` in {DECL.name} with a reason")
        for miss in sorted(addrs - got):
            out.append(f"{miss} is tracked for tag {tag} but the generated table "
                       "does not carry it; regenerate mb_edges before pushing")
        for extra in sorted(got - addrs):
            out.append(f"{extra} is a reviewed exception in the generated table for "
                       f"tag {tag} and is not tracked in {DECL.name}; it came from a "
                       "--allow-target argument and will not survive the next "
                       "regeneration")
        # Every edge that traps if taken is named, by target and source, with its kind.
        if "refused" not in gdoc:
            out.append(f"the generated table for tag {tag} lists no `refused` rows: it was "
                       "written by an mb_edges.py older than the record's refused groups; "
                       "regenerate it")
            continue
        named = refused.get(tag, {})
        traps = {(f"0x{int(r['target'], 0):08x}", f"0x{int(r['source'], 0):08x}"): r["kind"]
                 for r in gdoc["refused"]}
        for va, src in sorted(e for e in set(traps) - set(named) if e[0] not in addrs):
            out.append(f"{va} traps if reached ({traps[va, src]} from {src}) and {path.name} "
                       f"names it in no exception and no refused group for tag {tag}: decide "
                       "which, and record the reason")
        for va, src in sorted(set(named) - set(traps)):
            out.append(f"{va} from {src} is refused for tag {tag} in {path.name} but the "
                       "generated table does not refuse that edge (it dispatches now, or no "
                       "longer reaches it from there): drop it from `refused`")
        for va, src in sorted(set(named) & set(traps)):
            if named[va, src] != traps[va, src]:
                out.append(f"{va} from {src} is refused as {named[va, src]}, and the generated "
                           f"table reaches it from there by {traps[va, src]}: the edge changed, "
                           "so its reason needs a fresh look")
    return out


# How many falsifications ran, set by falsifications() from the cases it
# actually built. It was a literal in the summary line, which is the same
# defect this project catalogues as "a counter incremented before the thing it
# counts": adding a case without touching the literal would have printed a
# number that was simply wrong, and a gate that misreports its own rigour is
# worse than one that does not report it.
RAN = 0


def falsifications() -> list[str]:
    if not DECL.exists():
        return ["the record does not exist, so no mutation can be shown to fail"]
    doc = json.loads(DECL.read_text())
    if not doc.get("exceptions"):
        return ["the record has no entries, so a dropped entry cannot be "
                "constructed; the falsifications cannot run"]

    def mutate(name, f):
        d = copy.deepcopy(doc)
        f(d)
        return name, d

    cases = [
        mutate("an entry dropped", lambda d: d["exceptions"].pop(0)),
        mutate("a reason emptied",
               lambda d: d["exceptions"][0].__setitem__("why", "")),
        mutate("a reason replaced by a placeholder",
               lambda d: d["exceptions"][0].__setitem__(
                   "why", "temporary workaround, revisit later")),
        mutate("a falsifier dropped",
               lambda d: d["exceptions"][0].__setitem__("falsifier", "")),
        mutate("an address that is not an address",
               lambda d: d["exceptions"][0].__setitem__("va", "the thunk")),
        mutate("a duplicate entry",
               lambda d: d["exceptions"].append(copy.deepcopy(d["exceptions"][0]))),
        mutate("the schema changed",
               lambda d: d.__setitem__("schema", "something-else/v1")),
        # The one that actually happened. Schema v1's prose called the
        # reaching instruction int3 padding; it is `push esp`. If this
        # mutation passes, the decoded block is decoration.
        mutate("the original wrong claim: the reaching byte described as int3",
               lambda d: d["exceptions"][0]["decoded"].update(
                   {"source_byte": "0xcc", "source_mnemonic": "int3",
                    "source_flow_control": "INTERRUPT"})),
        mutate("a decoded field emptied",
               lambda d: d["exceptions"][0]["decoded"].__setitem__(
                   "target_bytes", "")),
        mutate("the padding moved above the thunk",
               lambda d: d["exceptions"][0]["decoded"].__setitem__(
                   "first_padding_va", "0x00826f5f")),
    ]
    if doc.get("refused"):
        cases += [
            # Issue #16's shape: a row the table refuses that no record names.
            mutate("a refused row dropped from the record",
                   lambda d: d["refused"][0]["rows"].pop(0)),
            # The edge the per-target census hid: a second source into a target another row
            # names. If dropping it passes, the gate is per target again.
            mutate("a second source into a refused target dropped from the record",
                   lambda d: [g["rows"].remove(r) for g in d["refused"] for r in list(g["rows"])
                              if (r["va"], r["source"]) == ("0x0082ccc5", "0x008294d8")]),
            mutate("a refused edge named twice",
                   lambda d: d["refused"][0]["rows"].append(dict(d["refused"][0]["rows"][0]))),
            mutate("a refused group's reason emptied",
                   lambda d: d["refused"][0].__setitem__("why", "")),
            mutate("a refused row's edge kind changed",
                   lambda d: d["refused"][0]["rows"][0].__setitem__("kind", "call-return")),
            mutate("an address both allowed and refused",
                   lambda d: d["refused"][0]["rows"].append(
                       {"va": d["exceptions"][0]["va"], "source": "0x00401000",
                        "kind": "jcc-taken"})),
        ]
    global RAN
    RAN = len(cases)

    survived = []
    with tempfile.TemporaryDirectory() as td:
        for name, d in cases:
            p = Path(td) / "exceptions.json"
            p.write_text(json.dumps(d))
            if not problems(p):
                survived.append(name)
        # The generated side: a tracked exception whose row no longer needs it.
        gens = sorted(GEN.glob("mb_edges.*.json"))
        if gens:
            gd = Path(td) / "gen"
            gd.mkdir()
            for g in gens:
                doc_g = json.loads(g.read_text())
                tracked = {str(e["va"]).lower() for e in doc.get("exceptions", [])}
                doc_g["named_exception_targets"] = [
                    a for a in (doc_g.get("named_exception_targets") or [])
                    if f"0x{int(a, 0):08x}" not in tracked]
                (gd / g.name).write_text(json.dumps(doc_g))
            RAN += 1
            if not problems(None, gd):
                survived.append("a tracked exception whose generated row is no longer allowed")
            # And a row the table refuses that the record has never heard of.
            for g in gens:
                doc_g = json.loads(g.read_text())
                doc_g["refused"] = list(doc_g.get("refused") or []) + [
                    {"target": "0x00401001", "source": "0x00401000", "kind": "jcc-taken"}]
                (gd / g.name).write_text(json.dumps(doc_g))
            RAN += 1
            if not problems(None, gd):
                survived.append("a trapping row in the generated table that no record names")
            # And a second source into a target the record already names from another.
            for g in gens:
                doc_g = json.loads(g.read_text())
                rows = list(doc_g.get("refused") or [])
                if rows:
                    rows.append(dict(rows[0], source="0x00401000"))
                doc_g["refused"] = rows
                (gd / g.name).write_text(json.dumps(doc_g))
            RAN += 1
            if not problems(None, gd):
                survived.append("a second trapping source into a named target")
    return [f"falsification survived: {s} -- the gate accepted a record it "
            "should have rejected" for s in survived]


def main() -> int:
    failed = falsifications()
    if failed:
        for f in failed:
            print(f"  {f}")
        print(f"DISPATCH_EXCEPTIONS_FAILED falsifications={len(failed)}")
        return 1
    probs = problems()
    if probs:
        for p in probs:
            print(f"  {p}")
        print(f"DISPATCH_EXCEPTIONS_FAILED problems={len(probs)}")
        return 1
    doc = json.loads(DECL.read_text())
    n = len(doc["exceptions"])
    allowed = set()
    for g in GEN.glob("mb_edges.*.json"):
        allowed |= {f"0x{int(a, 0):08x}" for a in
                    (json.loads(g.read_text()).get("named_exception_targets") or [])}
    live = sum(1 for e in doc["exceptions"] if f"0x{int(e['va'], 0):08x}" in allowed)
    refused = sum(len(g.get("rows", [])) for g in doc.get("refused", []))
    print(f"DISPATCH_EXCEPTIONS_OK tracked={n} in_force={live} retired="
          f"{len(doc.get('retired', []))} untracked=0 refused_named={refused} "
          f"refused_unnamed=0 falsifications={RAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
