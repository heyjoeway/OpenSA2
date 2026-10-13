#!/usr/bin/env python3
"""perf_memfast: the guest-memory fast path for the lifted functions State localization left alone.

Subtitle: `memfast(text)` rewrites a folded module whose lifted functions still call remill's
one-to-eight-byte guest-memory intrinsics so that every such call is an inline load or store
through `sa2_guest_fast_base`, with the out-of-line checked path for everything else: the fast
path tools/s2/corpus_run/state_local.py gives the functions it localizes (its `_fast_helper` and
`_fast_call`, used here unchanged), given to the rest. A measurement for tools/s8/perf_f80.py
`build --memfast`; nothing in the registered pipeline calls it.

Why: in the double mode (tools/s8/f80_double.py) a fifth of Metal Harbor's gameplay time is in
`__remill_read_memory_32` and its siblings, called from hundreds of lifted functions the local
pass did not select (tools/s8/perf_game.py's profile names their callers).

The rules are state_local's for its fast path, for the same reasons (its module docstring, "The
guest-memory fast path"): all or none in a module, so -O2's inliner never mixes a function whose
guest accesses are token-ordered `readnone` calls with one whose accesses are real loads and
stores; a module with a ten-byte, sixteen-byte or port access, a guest-memory intrinsic used
other than as a call's callee, a call it cannot read, or a name it would add already in use is
left as it came, and counted. In a function State localization did not rewrite, State is the
`noalias` parameter, so a guest access through the base cannot alias it, and every bracket point
(a lifted callee, the runtime's entry points) is declared with no memory attribute, so no guest
access moves across one. The base is loaded once at each lifted function's entry: the runtime
never changes it while lifted code runs (runtime/guest_memory.h, "the inlined fast path").
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s2" / "corpus_run"))
import state_local as SL  # noqa: E402

DEFINE = re.compile(r"^define (?:internal )?ptr @(sub_[0-9a-fA-F]+(?:\.\d+)?)"
                    r"\(ptr noalias %state, i32 %program_counter, ptr noalias %memory\) ?(?:#\d+ )?\{$")
BASE = f"  %sl.base = load ptr, ptr @{SL.FAST_BASE}, align 8"


def memfast(text: str) -> tuple[str, dict]:
    """(the module, what was done): every non-localized lifted function's guest accesses
    through the fast path, or the module unchanged with `kept` saying why."""
    st = {"functions": 0, "calls": 0, "kept": None}
    if "@__remill_read_memory_" not in text and "@__remill_write_memory_" not in text:
        return text, st
    if "%sl.base" in text or "__sa2_fast_" in text or "%state.host" in text:
        st["kept"] = "localized or already through the fast path"
        return text, st
    lines = text.split("\n")
    out: list[str] = []
    used: set[str] = set()
    in_fn = False
    for i, line in enumerate(lines):
        if line.startswith("define "):
            m = DEFINE.match(line)
            in_fn = bool(m)
            out.append(line)
            if m:
                st["functions"] += 1
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if nxt.endswith(":") and not nxt.startswith(" "):
                    out.append(nxt)              # an entry label: the load goes after it
                    lines[i + 1] = None          # type: ignore[call-overload]
                out.append(BASE)
            continue
        if line is None:
            continue
        if line == "}":
            in_fn = False
            out.append(line)
            continue
        if "@__remill_" in line and SL._MEMORY_OR_PORT_REF.search(line):
            if line.startswith("declare"):
                out.append(line)
                continue
            names = SL._MEMORY_OR_PORT_REF.findall(line)
            if not in_fn:
                st["kept"] = f"a guest-memory reference outside a lifted function: {line.strip()[:120]}"
                return text, st
            if any(n not in SL.FAST_ACCESS for n in names):
                st["kept"] = f"an access the fast path does not replace: {names[0]}"
                return text, st
            c = SL.parse_call(line) if SL._CALL_HEAD.match(line) else None
            if c is None or c.callee not in SL.FAST_ACCESS:
                st["kept"] = f"a guest-memory intrinsic not called: {line.strip()[:120]}"
                return text, st
            new = SL._fast_call(line, c)
            if new is None:
                st["kept"] = f"a call the fast path cannot read: {line.strip()[:120]}"
                return text, st
            out.append(new)
            used.add(c.callee)
            st["calls"] += 1
            continue
        out.append(line)
    if not used:
        return text, {"functions": 0, "calls": 0, "kept": None}
    tail = [SL._fast_helper(callee) for callee in SL.FAST_ACCESS if callee in used]
    tail += [f"declare dso_local ptr @{SL.SLOW_HALVES[kind]}(i32, i32) cold nounwind"
             for kind in ("read", "write")
             if any(SL.FAST_ACCESS[callee][0] == kind for callee in used)]
    tail.append(f"@{SL.FAST_BASE} = external dso_local global ptr, align 8")
    result = "\n".join(out)
    if not SL._EXPECT_DECLARE.search(result):
        tail.append("declare i1 @llvm.expect.i1(i1, i1)")
    result += "\n" + "\n\n".join(tail) + "\n"
    # Unused intrinsic declarations stay: a declaration orders nothing.
    left = [l for l in result.split("\n") if not l.startswith("declare")
            and SL._MEMORY_OR_PORT_REF.search(l)]
    if left:
        st["kept"] = f"an intrinsic reference survived: {left[0].strip()[:120]}"
        return text, {"functions": 0, "calls": 0, "kept": st["kept"]}
    return result, st
