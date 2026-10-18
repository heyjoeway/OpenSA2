#!/usr/bin/env python3
"""variants: a Mod Loader code patch as a variant of the lifted function it patches, chosen by a
tweak.

Subtitle: `variants.py build SPEC... --objects DIR --out OUT` lifts every module a tweak's patch
reaches again, from the patched bytes, under a symbol of its own (`sub_<va>__tw_<tweak>`),
compiles it as DIR's lifted objects were compiled, and writes OUT/sa2_tweak_variants.c, which tells
runtime/sa2_tweaks.c the tweaks the game links (sa2_tweak_patches_linked) and wraps each patched
module: the variant when the tweak is on (sa2_tweak_patch_on), the lifted original (`__real_`)
otherwise. With no SPEC, `build` takes every shipped spec, tools/s8/variants/tweaks/*.json (none
is fine: the file then links no tweak). `variants.py check` runs the checks alone, for specs (with
none, every tracked spec and fixture) or for every Patch entry of a Mod Loader Codes.lst.

A spec in tools/s8/variants/tweaks/ is a shipped tweak: runtime/sa2_tweaks.h registers it in
SA2_TWEAK_LIST and SA2_TWEAK_PATCH_LIST, with the spec's report ("Add a code-patch tweak"). Any
other spec, such as the tool's fixtures in tools/s8/variants/fixtures/, names no registered tweak.

    source tools/env.sh
    .venv/bin/python tools/s8/variants/variants.py check
    .venv/bin/python tools/s8/variants/variants.py check --codes <Codes.lst> [--patch NAME]
    .venv/bin/python tools/s8/variants/variants.py build [SPEC...] \\
        --objects private/generated/s8/pc-double/linux-obj --out <OUT under private/generated>
    tools/slot.sh .venv/bin/python tools/s7/release_build.py ... --variants <OUT>
    tools/slot.sh .venv/bin/python tools/s8/perf_f80.py arm64-link --objects <DIR> --out <DIR2> \\
        --variants <OUT built for that DIR>

OpenSA2 never runs the guest's code bytes, so a patch to them changes nothing until the lifted
function is lifted again from the patched bytes. README.md beside this file has the design, the
checks and the limits; every refusal names the tweak, the address and the reason.
"""
from __future__ import annotations

import argparse
import ast
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import dataclasses
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _d in ("tools/s3/whole_image", "tools/s3/arms", "tools/s4", "tools/s2/corpus_run", "tools/spike",
           "tools/s7", "tools/s8"):
    if str(ROOT / _d) not in sys.path:
        sys.path.append(str(ROOT / _d))

# v2: the generated C names its tweaks with sa2_tweak_patches_linked and asks sa2_tweak_patch_on
# (runtime/sa2_tweaks.h), and each tweak records whether it is a shipped one; v1's C does not link.
SCHEMA = "sa2-s8-variants/v2"
SELF = "tools/s8/variants/variants.py"
# JSON under a name that does not end in .json: tools/s4/m7_inputs.py reads every *.json under
# private/generated as a possible game-run record.
RECORD = "variants.rec"
C_FILE = "sa2_tweak_variants.c"
TAG = "next+allarms"
IMAGE_BASE = 0x00400000
RUNTIME = ROOT / "runtime"
LINK_S4 = ROOT / "tools" / "s4" / "link_s4.py"
EDGES_C = Path("private/generated/s4/link") / f"mb_edges.{TAG}.c"
# The edge kinds of tools/s4/mb_edges.py (runtime/remill_runtime.h SA2_MB_EDGE_*).
EDGE_JMP, EDGE_JCC, EDGE_FALL, EDGE_CALL = 1, 2, 3, 4
EDGE_NAMES = {EDGE_JMP: "jmp", EDGE_JCC: "jcc-taken", EDGE_FALL: "fall-through",
              EDGE_CALL: "call-return"}
EDGE_ROW = re.compile(r"\{0x([0-9a-f]+)u, 0x([0-9a-f]+)u, (\d+), (\d+),")
DECLARE_SUB = re.compile(r"^declare[^\n]*@(sub_[0-9a-f]+)\(", re.M)
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
REPORT = re.compile(r"[ -!#-\[\]-~]{1,60}")            # printable ASCII, no quote or backslash
MAX_TWEAKS = 32                                       # runtime/sa2_tweaks.c: one bit per tweak


@functools.cache
def tweak_sources():
    """tools/s4/tweak_sources.py of this checkout: SA2_TWEAK_LIST, SA2_TWEAK_PATCH_LIST, SPECS."""
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location("variants_tweak_sources", ROOT / "tools/s4/tweak_sources.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def registrations(runtime: Path = RUNTIME) -> tuple[frozenset[str], dict[str, str]]:
    """(the names of the tweaks with a runtime file, {name: report} of the code-patch tweaks), as
    runtime/sa2_tweaks.h's two lists say."""
    TS = tweak_sources()
    patch = {r["name"]: r["report"] for r in TS.patches(runtime)}
    return frozenset(r["name"] for r in TS.rows(runtime) if r["name"] not in patch), patch


# The shipped specs, each a tweak runtime/sa2_tweaks.h registers, and the tool's test fixtures.
SPECS_DIR = ROOT / tweak_sources().SPECS
FIXTURES_DIR = ROOT / tweak_sources().FIXTURES


def shipped_specs() -> list[Path]:
    """Every shipped spec, tools/s8/variants/tweaks/*.json, in name order (none is fine)."""
    return sorted(SPECS_DIR.glob("*.json"))


def fixture_specs() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json"))


class Refused(Exception):
    """A tweak the tool will not build, with the reason; the command exits 1 naming it."""


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path: Path) -> str:
    path = Path(os.path.abspath(path))
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


# ---------------------------------------------------------------- the model

@dataclass(frozen=True)
class Patch:
    va: int
    old: bytes
    new: bytes

    @property
    def end(self) -> int:
        return self.va + len(self.new)

    def doc(self) -> dict:
        return {"va": f"0x{self.va:08x}", "old": self.old.hex(), "new": self.new.hex()}


@dataclass(frozen=True)
class Tweak:
    name: str
    report: str
    functions: tuple[int, ...]
    patches: tuple[Patch, ...]
    source: str = ""
    why: str = ""
    spec: str = ""
    spec_sha256: str = ""
    shipped: bool = False                # its spec is in tools/s8/variants/tweaks/: a registered tweak

    @property
    def cname(self) -> str:
        return self.name.replace("-", "_")


@dataclass(frozen=True)
class Module:
    """A lifted module: remill's trace from `entry` over the bytes [lo, hi), seeded with
    --entry_address when `anchor` is set (S3's arms and the anchored extras)."""
    entry: int
    lo: int
    hi: int
    population: str                      # map | arms | extra: tools/s7/cross_build.py's names
    anchor: int | None = None

    @property
    def symbol(self) -> str:
        return f"sub_{self.entry:x}"

    def variant(self, t: Tweak) -> str:
        return f"sub_{self.entry:x}__tw_{t.cname}"


@dataclass
class World:
    """What the checks read: the image as mapped, its executable sections, every lifted module
    the link holds, the missing-block edge table, and the names already in use."""
    image: bytes
    code: tuple[tuple[int, int], ...]
    modules: tuple[Module, ...]
    edges: dict[int, tuple[int, int]]            # target -> (kind, allow)
    taken: frozenset[str]                        # symbols the runtime already wraps
    builtin: frozenset[str]                      # the tweaks with a runtime file (runtime/tweaks/)
    registered: dict[str, str] = dataclasses.field(default_factory=dict)  # code-patch: name -> report
    base: int = IMAGE_BASE

    @functools.cached_property
    def entries(self) -> frozenset[int]:
        return frozenset(m.entry for m in self.modules)

    @functools.cached_property
    def maps(self) -> dict[int, Module]:
        return {m.entry: m for m in self.modules if m.population == "map"}


def production_wraps() -> list[str]:
    """tools/s4/link_s4.py's PRODUCTION_WRAPS, read as a literal (arm64_link.py reads it so), and the
    plain tweaks' wraps (runtime/tweaks/*.c TWEAK_WRAPS), which link_s4.py adds after it."""
    for node in ast.parse(LINK_S4.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PRODUCTION_WRAPS"
                                                for t in node.targets):
            listed = list(ast.literal_eval(node.value))
            return listed + tweak_sources().wraps(RUNTIME, listed)
    raise Refused(f"{rel(LINK_S4)} defines no PRODUCTION_WRAPS literal")


def load_world() -> World:
    """The checkout's lifted image, as tools/s7/cross_build.py's populations name it."""
    import common as C  # noqa: PLC0415
    import pefile  # noqa: PLC0415
    pe = pefile.PE(str(C.EXE), fast_load=True)
    code = tuple((C.IMAGE_BASE + s.VirtualAddress, C.IMAGE_BASE + s.VirtualAddress + s.Misc_VirtualSize)
                 for s in pe.sections if s.Characteristics & 0x20000000)
    built = C.read_jsonl(C.BUILD / "next.jsonl")
    mods = [Module(int(va, 16), int(va, 16), int(r["end"], 16), "map")
            for va, r in C.read_jsonl(C.LIFT / "next.jsonl").items()
            if r.get("lifted") and (built.get(va) or {}).get("compiled")]
    for r in C.read_jsonl(C.GEN / "s3" / "arms" / "arms.next.jsonl").values():
        if r.get("compiled"):
            lo, hi = (int(x, 16) for x in r["anchor"])
            mods.append(Module(int(r["entry"], 16), lo, hi, "arms", lo))
    state = json.loads((C.GEN / "s4" / "link" / f"extra_functions.{TAG}.json").read_text())
    for r in state["rows"]:
        if r.get("compiled") and not r.get("rejected"):
            va = int(r["va"], 16)
            anchor = int(r["anchor"], 16) if r.get("anchor") else None
            mods.append(Module(va, anchor if anchor is not None else va, int(r["end"], 16), "extra",
                               anchor))
    edges = {int(t, 16): (int(k), int(a))
             for t, _s, k, a in EDGE_ROW.findall((ROOT / EDGES_C).read_text())}
    builtin, registered = registrations()
    return World(image=C.memory_image(), code=code, modules=tuple(mods), edges=edges,
                 taken=frozenset(production_wraps()), builtin=builtin, registered=registered)


def read_spec(path: Path) -> Tweak:
    """A tweak spec (README.md, "The spec"): a JSON object with exactly these keys."""
    path = Path(path)
    d = json.loads(path.read_text())
    keys = {"name", "report", "functions", "patches", "source", "why"}
    if not isinstance(d, dict) or set(d) != keys:
        raise Refused(f"{rel(path)}: a spec has exactly the keys {sorted(keys)}; this one has "
                      f"{sorted(d) if isinstance(d, dict) else type(d).__name__}")
    patches = []
    for p in d["patches"]:
        if set(p) != {"va", "old", "new"}:
            raise Refused(f"{rel(path)}: a patch has exactly va, old and new: {p}")
        patches.append(Patch(int(p["va"], 16), bytes.fromhex(p["old"]), bytes.fromhex(p["new"])))
    shipped = path.resolve().parent == SPECS_DIR.resolve()
    return Tweak(name=d["name"], report=d["report"], functions=tuple(int(f, 16) for f in d["functions"]),
                 patches=tuple(patches), source=d["source"], why=d["why"], spec=rel(path),
                 spec_sha256=sha256(path), shipped=shipped)


# ---------------------------------------------------------------- the checks

def apply(image: bytes, base: int, patches) -> bytes:
    out = bytearray(image)
    for p in patches:
        out[p.va - base:p.end - base] = p.new
    return bytes(out)


def decode(img: bytes, base: int, ip: int):
    from iced_x86 import Decoder  # noqa: PLC0415
    return Decoder(32, img[ip - base:ip - base + 16], ip=ip).decode()


def trace(img: bytes, base: int, m: Module) -> tuple[dict, set[int]]:
    """remill's recursive descent over the module's bytes from its entry
    (tools/s3/arms/cost.py walk_detail, the copy S3 validated against remill's markers): the
    live instructions by address, and the exits."""
    from cost import walk_detail  # noqa: PLC0415
    if base != IMAGE_BASE:
        raise Refused(f"the image base is 0x{base:08x}; cost.walk_detail reads 0x{IMAGE_BASE:08x}")
    seen, exits = walk_detail(img, m.entry, m.lo, m.hi)
    return {ip: decode(img, base, ip) for ip in seen}, exits


def leaving(insns: dict, m: Module) -> set[tuple[int, int]]:
    """(target, kind) for every edge a live instruction takes out of [lo, hi), classified as
    tools/s4/mb_edges.py decode_edges classifies them."""
    from iced_x86 import FlowControl as F  # noqa: PLC0415
    out = set()
    inside = lambda a: m.lo <= a < m.hi  # noqa: E731
    for ins in insns.values():
        fc, nxt = ins.flow_control, ins.next_ip
        if fc == F.UNCONDITIONAL_BRANCH:
            if not inside(ins.near_branch_target):
                out.add((ins.near_branch_target, EDGE_JMP))
        elif fc == F.CONDITIONAL_BRANCH:
            if not inside(ins.near_branch_target):
                out.add((ins.near_branch_target, EDGE_JCC))
            if not inside(nxt):
                out.add((nxt, EDGE_FALL))
        elif fc in (F.CALL, F.INDIRECT_CALL):
            if not inside(nxt):
                out.add((nxt, EDGE_CALL))
        elif fc not in (F.RETURN, F.INDIRECT_BRANCH, F.INTERRUPT, F.EXCEPTION):
            if not inside(nxt):
                out.add((nxt, EDGE_FALL))
    return out


def covered(insns: dict, addrs: set[int]) -> set[int]:
    return {a for ins in insns.values() for a in range(ins.ip, ins.next_ip) if a in addrs}


def edge_text(t: int, k: int) -> str:
    return f"{EDGE_NAMES.get(k, k)} to 0x{t:08x}"


def plan_tweak(w: World, t: Tweak) -> dict:
    """Every check that needs no lift (README.md, "The checks"), in order; the modules the patch
    reaches and what changes in each. Raises Refused on the first failure."""
    from emulated import FORMS  # noqa: PLC0415
    from iced_x86 import Code, FlowControl  # noqa: PLC0415

    def no(why: str) -> Refused:
        return Refused(f"tweak {t.name}: {why}")

    if not NAME.fullmatch(t.name) or len(t.name) > 40:
        raise no("a tweak name is lowercase words joined by hyphens, at most 40 characters")
    if t.name in w.builtin:
        raise no(f"runtime/sa2_tweaks.h already has a tweak named {t.name}, with its own runtime file "
                 f"(runtime/tweaks/{t.name}.c)")
    if not REPORT.fullmatch(t.report):
        raise no(f"the report {t.report!r} is not 1 to 60 printable characters without quotes")
    if t.shipped and t.name not in w.registered:
        raise no("its spec is a shipped tweak's (tools/s8/variants/tweaks/), and runtime/sa2_tweaks.h "
                 "does not register it: add its line to SA2_TWEAK_LIST and to SA2_TWEAK_PATCH_LIST "
                 "(\"Add a code-patch tweak\")")
    if t.shipped and w.registered[t.name] != t.report:
        raise no(f"the report {t.report!r} is not the one SA2_TWEAK_LIST gives it, "
                 f"{w.registered[t.name]!r}")
    if not t.shipped and t.name in w.registered:
        raise no(f"runtime/sa2_tweaks.h registers {t.name} as a code-patch tweak, whose spec is "
                 f"tools/s8/variants/tweaks/{t.name}.json, not {t.spec or 'this one'}")
    if not t.patches:
        raise no("the spec has no patch")
    if not t.functions:
        raise no("the spec names no function")
    declared = []
    for f in t.functions:
        if f not in w.maps:
            raise no(f"0x{f:08x} is not a function the lifted image starts a map module at")
        declared.append(w.maps[f])
    spans = sorted((p.va, p.end) for p in t.patches)
    for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        if b0 < a1:
            raise no(f"the patches at 0x{a0:08x} and 0x{b0:08x} overlap")
    for p in t.patches:
        if not p.new or len(p.old) != len(p.new):
            raise no(f"the patch at 0x{p.va:08x} has {len(p.old)} old bytes and {len(p.new)} new; "
                     "they are the same length, at least one")
        if not any(lo <= p.va and p.end <= hi for lo, hi in w.code):
            raise no(f"0x{p.va:08x}..0x{p.end:08x} is not in a code section: a data write, which is "
                     "a guest-memory poke, not a variant")
        have = w.image[p.va - w.base:p.end - w.base]
        if have != p.old:
            raise no(f"the patch at 0x{p.va:08x} expects the bytes {p.old.hex(' ')}, and the image "
                     f"holds {have.hex(' ')}: it was written for another executable")
        if not any(f.lo <= p.va and p.end <= f.hi for f in declared):
            raise no(f"the patch at 0x{p.va:08x}..0x{p.end:08x} is outside the functions the spec "
                     f"names ({', '.join(f'0x{f.entry:08x}..0x{f.hi:08x}' for f in declared)})")
    changed = {p.va + k for p in t.patches for k in range(len(p.new)) if p.new[k] != p.old[k]}
    if not changed:
        raise no("its patches change no byte")
    patched = apply(w.image, w.base, t.patches)
    lo_all, hi_all = min(changed), max(changed) + 1
    reached, seen_bytes = [], set()
    for m in sorted(w.modules, key=lambda m: (m.entry, m.population)):
        if m.hi <= lo_all or m.lo >= hi_all:
            continue
        before, _ = trace(w.image, w.base, m)
        after, exits = trace(patched, w.base, m)
        hit = covered(before, changed) | covered(after, changed)
        if hit:
            reached.append((m, before, after, exits))
            seen_bytes |= hit
    stray = sorted(changed - seen_bytes)
    if stray:
        raise no(f"no lifted module's trace runs the byte{'s' if len(stray) > 1 else ''} at "
                 f"{', '.join(f'0x{a:08x}' for a in stray[:4])}: data inside the code section (a jump "
                 "table, say) or code no trace reaches, which a variant cannot change; a guest-memory "
                 "poke can")
    rows = []
    for m, before, after, exits in reached:
        where = f"the {m.population} module 0x{m.entry:08x} (0x{m.lo:08x}..0x{m.hi:08x})"
        if m.symbol in w.taken:
            raise no(f"{where} is one the runtime or a tweak's file already wraps (tools/s4/link_s4.py "
                     "PRODUCTION_WRAPS, runtime/tweaks/*.c); a second __wrap_ cannot be linked")
        bad = sorted(ip for ip, ins in after.items() if ins.code == Code.INVALID)
        if bad:
            raise no(f"{where}: the patched bytes decode to no instruction at 0x{bad[0]:08x}")
        old_e, new_e = leaving(before, m), leaving(after, m)
        for tgt, kind in sorted(new_e - old_e):
            row = w.edges.get(tgt)
            if tgt not in w.entries:
                raise no(f"{where}: the patched code leaves by {edge_text(tgt, kind)}, where no "
                         "lifted module starts; the run would trap there")
            if row is None or not (row[0] == EDGE_JMP or row[1]):
                held = "no row" if row is None else f"the row {EDGE_NAMES.get(row[0], row[0])}, allow {row[1]}"
                raise no(f"{where}: the patched code leaves by {edge_text(tgt, kind)}, and the edge "
                         f"table ({EDGES_C}) has {held} for it, so the runtime would trap there "
                         "rather than dispatch; that edge needs a reviewed dispatch exception first")
        calls = sorted({ins.near_branch_target for ins in after.values()
                        if ins.flow_control == FlowControl.CALL})
        orphan = [c for c in calls if c not in w.entries]
        if orphan:
            raise no(f"{where}: the patched code calls 0x{orphan[0]:08x}, where no lifted module "
                     "starts; the variant would not link")
        for ins in after.values():
            if ins.code in FORMS and ins.next_ip not in w.entries:
                raise no(f"{where}: the patched code reaches {FORMS[ins.code]} at 0x{ins.ip:08x}, "
                         f"which remill cannot lift, and no lifted module continues at "
                         f"0x{ins.next_ip:08x}")
        rows.append({"population": m.population, "entry": f"0x{m.entry:08x}",
                     "range": [f"0x{m.lo:08x}", f"0x{m.hi:08x}"],
                     "anchor": f"0x{m.anchor:08x}" if m.anchor is not None else None,
                     "symbol": m.symbol, "variant": m.variant(t),
                     "live_before": len(before), "live_after": len(after),
                     "pc_markers_expected": len(after) + len(exits),
                     "edges_added": [edge_text(*e) for e in sorted(new_e - old_e)],
                     "edges_removed": [edge_text(*e) for e in sorted(old_e - new_e)]})
    return {"tweak": t.name, "changed_bytes": len(changed), "modules": rows}


def plan_all(w: World, tweaks: list[Tweak]) -> list[dict]:
    """plan_tweak for each, and what only the set can break: names and modules shared."""
    listed = len(w.builtin) + len(w.registered)
    extra = sum(1 for t in tweaks if t.name not in w.registered)
    if listed + extra > MAX_TWEAKS:
        raise Refused(f"the {listed} tweaks of runtime/sa2_tweaks.h and {extra} unregistered ones (test "
                      f"fixtures) are more than the {MAX_TWEAKS} runtime/sa2_tweaks.c has bits for")
    names = [t.name for t in tweaks]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise Refused(f"two specs name the tweak {dup[0]}")
    plans, owner = [], {}
    for t in tweaks:
        p = plan_tweak(w, t)
        for row in p["modules"]:
            if row["symbol"] in owner:
                raise Refused(f"tweak {t.name}: the {row['population']} module {row['entry']} is "
                              f"patched by tweak {owner[row['symbol']]} too; a variant holding both "
                              "patches is not built (README.md, limits)")
            owner[row["symbol"]] = t.name
        plans.append(p)
    return plans


# ---------------------------------------------------------------- lifting and compiling

def lift_folded(image: bytes, m: Module, lldir: Path) -> tuple[str, int | None]:
    """The module lifted from `image` exactly as its own tool lifted it (lift_extra.lift_one: the
    remill command, the PC24 pass with both helpers), then folded (internalize.rewrite): the
    folded IR and the PC markers remill emitted in the module's define."""
    import internalize  # noqa: PLC0415
    import lift_extra as LE  # noqa: PLC0415
    from cost import pc_stores  # noqa: PLC0415
    lldir.mkdir(parents=True, exist_ok=True)
    try:
        rec = LE.lift_one(m.entry, m.hi, image, lldir, m.anchor)
    except RuntimeError as e:
        raise Refused(f"0x{m.entry:08x}: {e}") from None
    if not rec["lifted"]:
        raise Refused(f"remill did not lift 0x{m.entry:08x}: {rec.get('error_head')}")
    markers = pc_stores(lldir / f"sub_{m.entry:08X}.ll", m.symbol)
    text, _st = internalize.rewrite((lldir / f"sub_{m.entry:08X}.pc24.ll").read_text(errors="replace"))
    return text, markers


def rename(text: str, m: Module, symbol: str) -> str:
    """Every reference to the module's own symbol made the variant's (its define, and a call to
    itself); the module then defines nothing the original defines."""
    out = re.sub(rf"@{re.escape(m.symbol)}(?![0-9A-Za-z_.$])", "@" + symbol, text)
    defs = re.findall(rf"^define [^\n]*@{re.escape(symbol)}\(", out, re.M)
    if len(defs) != 1:
        raise Refused(f"0x{m.entry:08x}: the folded IR defines {symbol} {len(defs)} times after the "
                      "rename, not once")
    return out


class Objects:
    """A legalised build of the lifted image (tools/s8/perf_f80.py build, tools/s7/cross_build.py,
    tools/s8/android_game.py compile): its records, its pass, its compile commands."""

    def __init__(self, d: Path):
        self.dir = Path(os.path.abspath(d))
        sp, lp = self.dir / "summary.json", self.dir / "build.jsonl"
        if not (sp.is_file() and lp.is_file()):
            raise Refused(f"{rel(self.dir)} holds no lifted-object build (summary.json, build.jsonl)")
        self.summary = json.loads(sp.read_text())
        self.records = {}
        for line in lp.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                self.records[(r["population"], int(r["va"], 16))] = r
        s = self.summary
        self.target = s.get("target")
        self.mode = s.get("f80_mode") if s.get("legalized") else None
        if s.get("memfast"):
            raise Refused(f"{rel(self.dir)} is a memfast measuring build; variants are built for "
                          "product builds only")
        if self.mode not in ("double", "exact"):
            raise Refused(f"{rel(self.dir)} is not a legalised double or exact build "
                          f"(legalized {s.get('legalized')}, f80_mode {s.get('f80_mode')})")
        pass_rel = s.get("pass") or ("tools/s8/f80_double.py" if self.mode == "double"
                                     else "tools/s7/f80_legalize.py")
        if sha256(ROOT / pass_rel) != s.get("pass_sha256"):
            raise Refused(f"{rel(self.dir)} was legalised by another {pass_rel} "
                          f"({s.get('pass_sha256')}); a variant would be legalised by this "
                          "checkout's, so the build must be made again first")

    def identity(self) -> dict:
        return {"dir": rel(self.dir), "target": self.target, "f80_mode": self.mode,
                "summary_sha256": sha256(self.dir / "summary.json"),
                "build_jsonl_sha256": sha256(self.dir / "build.jsonl")}

    def command(self, pop: str, ir: Path, obj: Path) -> list[str]:
        fill = {"{ir}": str(ir), "{object}": str(obj)}
        if self.summary.get("commands"):
            return [fill.get(x, x) for x in self.summary["commands"][pop]]
        import cross_build as XB  # noqa: PLC0415
        return XB.compile_command(pop, self.target, ir, obj)

    def legalize(self, text: str) -> str:
        if self.mode == "double":
            import f80_double as D  # noqa: PLC0415
            return D.legalize(text)[0]
        import f80_legalize as F  # noqa: PLC0415
        return F.legalize(text)[0]

    def compile(self, text: str, pop: str, obj: Path, what: str) -> None:
        ir = obj.with_suffix(".f80.ll")
        obj.parent.mkdir(parents=True, exist_ok=True)
        try:
            ir.write_text(self.legalize(text))
        except Exception as e:  # noqa: BLE001  the pass refuses by exception; named here
            raise Refused(f"{what}: the {self.mode} pass refused it: {str(e)[:300]}") from None
        if obj.exists():
            obj.unlink()
        p = subprocess.run(self.command(pop, ir, obj), capture_output=True, text=True,
                           cwd=str(obj.parent), timeout=1200)
        if p.returncode or not obj.is_file():
            raise Refused(f"{what}: the compile failed: {(p.stderr + p.stdout)[-600:]}")


def defined_globals(obj: Path) -> list[str]:
    p = subprocess.run(["nm", "-g", "--defined-only", str(obj)], capture_output=True, text=True)
    if p.returncode:
        raise Refused(f"nm {obj}: {p.stderr.strip()[:200]}")
    return sorted(line.split()[-1] for line in p.stdout.splitlines() if line.strip())


def build_module(w: World, t: Tweak, row: dict, m: Module, objs: Objects, out: Path) -> dict:
    """One module's variant: the identity proof first, then the variant itself."""
    rec = objs.records.get((m.population, m.entry))
    where = f"tweak {t.name}: the {m.population} module 0x{m.entry:08x}"
    if rec is None or not rec.get("compiled"):
        raise Refused(f"{where} has no compiled record in {rel(objs.dir)}/build.jsonl")
    work = out / "work" / t.name / f"{m.population}_{m.entry:08X}"
    # The identity proof: this checkout's lift, fold, pass and compile of the unpatched bytes
    # are the build's IR and object, byte for byte, so the variant differs from the module it
    # stands beside by the patch alone.
    text0, _ = lift_folded(w.image, m, work / "orig")
    ir0 = hashlib.sha256(text0.encode()).hexdigest()
    if ir0 != rec.get("ir_sha256"):
        raise Refused(f"{where}: this checkout's lift and fold of the unpatched bytes give IR "
                      f"{ir0[:16]}, not the {str(rec.get('ir_sha256'))[:16]} the build compiled "
                      f"({rec.get('ir')}); the variant would not be made as the original was")
    obj0 = work / "orig" / f"ir_{m.entry:08X}.o"
    objs.compile(text0, m.population, obj0, f"{where} (unpatched)")
    ref = objs.dir / rec["object"]
    if obj0.read_bytes() != ref.read_bytes():
        raise Refused(f"{where}: the unpatched bytes compile to {sha256(obj0)[:16]}, not the build's "
                      f"{rel(ref)} ({sha256(ref)[:16]}); the variant would not be compiled as the "
                      "original was")
    patched = apply(w.image, w.base, t.patches)
    text1, markers = lift_folded(patched, m, work / "patched")
    if markers != row["pc_markers_expected"]:
        raise Refused(f"{where}: remill's trace of the patched bytes has {markers} PC markers and "
                      f"this tool's walk {row['pc_markers_expected']} (instructions + exits): the "
                      "lifter followed the patched control flow differently than the checks did")
    symbol = m.variant(t)
    text1 = rename(text1, m, symbol)
    have = {f"sub_{e:x}" for e in w.entries}
    orphan = sorted(set(DECLARE_SUB.findall(text1)) - have)
    if orphan:
        raise Refused(f"{where}: the variant declares {orphan[:3]}, which no lifted module defines")
    (work / "patched" / f"{symbol}.ll").write_text(text1)
    obj = out / "obj" / f"ir_{m.entry:08X}__tw_{t.cname}.o"
    objs.compile(text1, m.population, obj, where)
    defs = defined_globals(obj)
    if defs != [symbol]:
        raise Refused(f"{where}: the variant object defines {defs}, not exactly {symbol}")
    census = None
    shims = objs.dir / "f80_shims.o"
    if shims.is_file():
        import f80_double as D  # noqa: PLC0415
        import f80_legalize as F  # noqa: PLC0415
        names = (D.shim_names if objs.mode == "double" else F.shim_names)(shims)
        cen = F.census([obj], names, 1)
        census = {k: cen[k] for k in ("ok", "classes", "disallowed")}
        if not cen["ok"]:
            raise Refused(f"{where}: the variant object fails the f80 census: {cen['disallowed']}")
    return dict(row, object=str(obj.relative_to(out)), object_sha256=sha256(obj), ir_sha256=hashlib.sha256(
        text1.encode()).hexdigest(), pc_markers=markers, census=census,
        identity={"ir_sha256": ir0, "object": rel(ref), "object_sha256": sha256(ref)})


def c_source(tweaks: list[Tweak], built: list[dict], objs: dict) -> str:
    """The generated C: the tweaks the game links (sa2_tweak_patches_linked, runtime/sa2_tweaks.h)
    and one wrap per patched module, which asks sa2_tweak_patch_on for its tweak's row."""
    out = ["/* GENERATED by tools/s8/variants/variants.py -- do not edit, never committed.",
           " *",
           f" * The code-patch tweaks for the lifted objects of {objs['dir']} ({objs['target']},",
           f" * f80 {objs['f80_mode']}). sa2_tweak_patches_linked names them to runtime/sa2_tweaks.c,",
           " * and each wraps the lifted modules its patch reaches: with the tweak on, the module's",
           " * variant (lifted from the patched bytes) runs; off, and in every replay, the wrap only",
           " * calls `__real_`.",
           " */",
           "#include <stddef.h>",
           "#include <stdint.h>",
           "#include <stdio.h>",
           "",
           '#include "sa2_tweaks.h"',
           ""]
    if tweaks:
        out += [f"static const sa2_tweak_patch g_patches[{len(tweaks)}] = {{"]
        out += [f'    {{"{t.name}", "{t.report}"}},' for t in tweaks]
        out += ["};", ""]
    out += ["const sa2_tweak_patch *sa2_tweak_patches_linked(unsigned *count) {",
            f"    *count = {len(tweaks)}u;",
            f"    return {'g_patches' if tweaks else 'NULL'};",
            "}"]
    k = 0
    for i, (t, b) in enumerate(zip(tweaks, built)):
        kind = "registered in runtime/sa2_tweaks.h" if t.shipped else "a test fixture, in no list"
        out += ["", f"/* {t.name} ({kind}): {t.source.replace('*/', '* /')}", f" * {t.spec} */"]
        for row in b["modules"]:
            sym, var = row["symbol"], row["variant"]
            out += [f"void *__real_{sym}(void *state, uint32_t pc, void *memory);",
                    f"void *{var}(void *state, uint32_t pc, void *memory);",
                    f"static int g_said_{k};",
                    "",
                    f"void *__wrap_{sym}(void *state, uint32_t pc, void *memory) {{",
                    f"    if (!sa2_tweak_patch_on({i}u)) return __real_{sym}(state, pc, memory);",
                    f"    if (!__atomic_exchange_n(&g_said_{k}, 1, __ATOMIC_RELAXED))",
                    f'        fputs("sa2 tweaks: {t.report}: the patched lift of {row["entry"]} runs\\n", stderr);',
                    f"    return {var}(state, pc, memory);",
                    "}"]
            k += 1
    return "\n".join(out) + "\n"


def build(specs: list[Path], objects: Path, out: Path) -> int:
    out = Path(os.path.abspath(out))
    gen = ROOT / "private" / "generated"
    if not out.is_relative_to(gen):
        raise Refused(f"--out {out} is not under {rel(gen)}: the variants hold lifted code")
    specs = list(specs) or shipped_specs()
    w = load_world()
    tweaks = [read_spec(p) for p in specs]
    plans = plan_all(w, tweaks)
    objs = Objects(objects)
    out.mkdir(parents=True, exist_ok=True)
    mods = {(m.population, m.entry): m for m in w.modules}
    built = []
    for t, plan in zip(tweaks, plans):
        rows = [build_module(w, t, row, mods[(row["population"], int(row["entry"], 16))], objs, out)
                for row in plan["modules"]]
        built.append(dict(plan, modules=rows))
        for row in rows:
            print(f"  {t.name}: {row['population']} {row['entry']} -> {row['variant']} "
                  f"({row['live_after']} instructions, identity proven against {row['identity']['object']})",
                  flush=True)
    ident = objs.identity()
    c = out / C_FILE
    c.write_text(c_source(tweaks, built, ident))
    record = {
        "schema": SCHEMA, "generated_by": SELF, "tool_sha256": sha256(Path(__file__)),
        "objects": ident, "edge_table_sha256": sha256(ROOT / EDGES_C),
        "tweaks": [{"name": t.name, "report": t.report, "source": t.source, "why": t.why,
                    "spec": t.spec, "spec_sha256": t.spec_sha256, "shipped": t.shipped,
                    "functions": [f"0x{f:08x}" for f in t.functions],
                    "patches": [p.doc() for p in t.patches], **b}
                   for t, b in zip(tweaks, built)],
        "c": {"path": C_FILE, "sha256": sha256(c)},
        "wraps": [row["symbol"] for b in built for row in b["modules"]],
        "link_objects": [row["object"] for b in built for row in b["modules"]],
    }
    (out / RECORD).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"VARIANTS_OK {rel(out / RECORD)}: {len(tweaks)} tweak(s), {len(record['wraps'])} "
          f"variant(s) for {ident['dir']} ({ident['target']}, f80 {ident['f80_mode']})")
    return 0


def linked(out: Path, objects: Path) -> dict:
    """What a link adds for a built OUT (release_build.py and arm64_link.py call this): its C, its
    objects and its wraps, after checking that OUT was built for exactly `objects` and that no
    file moved since. Refuses by name otherwise."""
    out = Path(os.path.abspath(out))
    rp = out / RECORD
    if not rp.is_file():
        raise SystemExit(f"variants: {rel(out)} holds no {RECORD}; build it with {SELF} build")
    rec = json.loads(rp.read_text())
    if rec.get("schema") != SCHEMA:
        raise SystemExit(f"variants: {rel(rp)} has schema {rec.get('schema')!r}, not {SCHEMA}")
    want = rec["objects"]
    objects = Path(os.path.abspath(objects))
    have = {"summary_sha256": sha256(objects / "summary.json"),
            "build_jsonl_sha256": sha256(objects / "build.jsonl")}
    if {k: want.get(k) for k in have} != have:
        raise SystemExit(f"variants: {rel(out)} was built for the lifted objects {want.get('dir')}, "
                         f"and this link takes {rel(objects)}, a different build; build the variants "
                         f"for it: {SELF} build ... --objects {rel(objects)}")
    # A variant that adds an edge out of its module was checked against the edge table of its
    # build; a table regenerated since may no longer dispatch that edge.
    if any(row["edges_added"] for t in rec["tweaks"] for row in t["modules"]):
        table = ROOT / EDGES_C
        if not table.is_file() or sha256(table) != rec.get("edge_table_sha256"):
            raise SystemExit(f"variants: {rel(out)} adds edges the edge table {EDGES_C} was checked "
                             "to dispatch, and that table has changed since; build the variants again")
    files = [(out / rec["c"]["path"], rec["c"]["sha256"])]
    files += [(out / row["object"], row["object_sha256"]) for t in rec["tweaks"] for row in t["modules"]]
    for path, digest in files:
        if not path.is_file() or sha256(path) != digest:
            raise SystemExit(f"variants: {rel(path)} is not the file {rel(rp)} records; build again")
    # The registrations the build was checked against: a shipped tweak is still registered with its
    # spec unchanged, and a fixture names no tweak runtime/sa2_tweaks.h has.
    builtin, registered = registrations()
    for t in rec["tweaks"]:
        spec = ROOT / t["spec"]
        if t.get("shipped") and (t["name"] not in registered or not spec.is_file()
                             or sha256(spec) != t["spec_sha256"]):
            raise SystemExit(f"variants: {rel(out)} links the code-patch tweak {t['name']}, and since it "
                             f"was built runtime/sa2_tweaks.h no longer registers it or {t['spec']} has "
                             "changed; build the variants again")
        if not t.get("shipped") and (t["name"] in registered or t["name"] in builtin):
            raise SystemExit(f"variants: {rel(out)} links {t['name']} from {t['spec']}, which is not "
                             "its shipped spec, and runtime/sa2_tweaks.h has a tweak of that name; build "
                             "the variants again from tools/s8/variants/tweaks/")
    note = {"record": rel(rp), "record_sha256": sha256(rp), "objects": want,
            "tweaks": [{"name": t["name"], "report": t["report"], "spec": t["spec"],
                        "spec_sha256": t["spec_sha256"], "shipped": bool(t.get("shipped")),
                        "variants": [row["variant"] for row in t["modules"]]} for t in rec["tweaks"]],
            "c_sha256": rec["c"]["sha256"], "wraps": rec["wraps"]}
    return {"c": out / rec["c"]["path"], "objects": [out / o for o in rec["link_objects"]],
            "wraps": list(rec["wraps"]), "note": note}


# ---------------------------------------------------------------- a Mod Loader Codes.lst

WRITE = {"write8": 1, "write16": 2, "write32": 4}


def codes_entries(text: str) -> list[tuple[str, list[list[str]]]]:
    """Every `Patch "<name>"` entry of a Codes.lst with its operation lines (Code entries run each
    frame and are not patches; Author, Category, Description and comments are skipped)."""
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        head = line.split(None, 1)
        if head[0] in ("Patch", "Code"):
            cur = (head[1].strip().strip('"'), []) if head[0] == "Patch" else None
            if cur:
                out.append(cur)
        elif cur and head[0] not in ("Author", "Category", "Description", "Required"):
            cur[1].append(line.split())
    return out


def codes_writes(ops: list[list[str]]) -> dict[int, int]:
    """The bytes a Patch entry writes, {address: byte}, later writes winning; raises ValueError
    naming an operation this reader does not take."""
    num = lambda s: int(s, 16) if s.lower().startswith("0x") else int(s)  # noqa: E731
    out: dict[int, int] = {}
    for op in ops:
        name, args = op[0], op[1:]
        rep = 1
        if args and re.fullmatch(r"x\d+", args[-1]):
            rep, args = int(args[-1][1:]), args[:-1]
        if name in WRITE and len(args) == 2:
            n, at = WRITE[name], int(args[0], 16)
            data = (num(args[1]) & ((1 << (8 * n)) - 1)).to_bytes(n, "little") * rep
        elif name == "writenop" and len(args) == 2 and rep == 1:
            at, data = int(args[0], 16), b"\x90" * num(args[1])
        elif name == "writejump" and len(args) == 2 and rep == 1:
            at = int(args[0], 16)
            data = b"\xe9" + ((num(args[1]) - (at + 5)) & 0xFFFFFFFF).to_bytes(4, "little")
        else:
            raise ValueError(f"the operation {' '.join(op)!r}")
        for k, b in enumerate(data):
            out[at + k] = b
    return out


def codes_tweak(w: World, title: str, writes: dict[int, int]) -> Tweak:
    """The code writes of a Patch entry as a tweak, its old bytes read from the image."""
    runs, cur = [], []
    for a in sorted(writes):
        if cur and a != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(a)
    if cur:
        runs.append(cur)
    patches = tuple(Patch(r[0], w.image[r[0] - w.base:r[-1] + 1 - w.base], bytes(writes[a] for a in r))
                    for r in runs)
    funcs = sorted({m.entry for p in patches for m in w.maps.values() if m.lo <= p.va < m.hi})
    name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "patch"
    return Tweak(name=name, report=name[:60], functions=tuple(funcs), patches=patches,
                 source=f'Codes.lst Patch "{title}"')


def check(specs: list[Path], codes: Path | None, only: list[str]) -> int:
    w = load_world()
    if codes is None:
        # With no spec, the shipped ones as one set and each fixture on its own (a fixture may share
        # a module with a shipped tweak; it is never linked beside it in a release).
        sets = [[read_spec(p) for p in specs]] if specs else (
            [[read_spec(p) for p in shipped_specs()]] + [[read_spec(p)] for p in fixture_specs()])
        n = 0
        for tweaks in sets:
            for p in plan_all(w, tweaks):
                mods = ", ".join(f"{r['population']} {r['entry']}" for r in p["modules"])
                print(f"ok       {p['tweak']}: {p['changed_bytes']} byte(s) in {mods}")
                n += 1
        print(f"VARIANTS_CHECK_OK {n} tweak(s)")
        return 0
    counts: dict[str, int] = {}
    for title, ops in codes_entries(Path(codes).read_text(errors="replace")):
        if only and title not in only:
            continue
        try:
            writes = codes_writes(ops)
        except ValueError as e:
            verdict, text = "unread", f"{e} is not one this reader takes"
        else:
            in_code = {a: b for a, b in writes.items() if any(lo <= a < hi for lo, hi in w.code)}
            data = len(writes) - len(in_code)
            pre = f"{data} byte(s) of data poke, then " if data and in_code else ""
            if not in_code:
                verdict, text = "data", f"{data} byte(s) outside the code sections: a guest-memory poke"
            else:
                try:
                    p = plan_tweak(w, codes_tweak(w, title, in_code))
                    verdict = "variant"
                    text = pre + f"{p['changed_bytes']} byte(s) changed in " + ", ".join(
                        f"{r['population']} {r['entry']}" + (f" (+{', '.join(r['edges_added'])})"
                                                             if r["edges_added"] else "")
                        for r in p["modules"])
                except Refused as e:
                    verdict, text = "refused", pre + str(e).split(": ", 1)[1]
        counts[verdict] = counts.get(verdict, 0) + 1
        print(f"{verdict:8} \"{title}\": {text}")
    print("VARIANTS_CODES " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="the checks alone, for specs (default every tracked spec and "
                       "fixture) or a Codes.lst's Patch entries")
    c.add_argument("specs", nargs="*", type=Path)
    c.add_argument("--codes", type=Path, default=None, help="a Mod Loader Codes.lst")
    c.add_argument("--patch", action="append", default=[], help="with --codes: only this entry")
    b = sub.add_parser("build", help="lift, compile and wrap the variants of the specs' tweaks")
    b.add_argument("specs", nargs="*", type=Path,
                   help="default every shipped spec, tools/s8/variants/tweaks/*.json (none is fine)")
    b.add_argument("--objects", type=Path, required=True,
                   help="the legalised lifted-object build the variants are linked with")
    b.add_argument("--out", type=Path, required=True, help="a directory under private/generated")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            if a.specs and a.codes:
                raise Refused("check takes specs or --codes, not both")
            return check(a.specs, a.codes, a.patch)
        return build(a.specs, a.objects, a.out)
    except Refused as e:
        print(f"VARIANTS_REFUSED {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
