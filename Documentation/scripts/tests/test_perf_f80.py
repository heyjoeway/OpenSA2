"""tools/s8/perf_f80.py's `deps`: the inputs of an f80 build's pass and shims, which its summary
records and tools/s7/release_build.py's f80_build checks. For each mode, `perf_f80.py deps` names
the pass module with every module of this checkout it imports at module level, and the shims
source with every file it includes, each at its sha256; every other import of a checkout module
in those files, at any level, is one the pass never runs (IMPORTS_THE_PASS_NEVER_RUNS), so a new
one fails here; and release_build's manifest sources (F80_SOURCE_GLOBS) hold every input.

`perf_f80.py deps` runs as its own process, as release_build runs it, so its sys.path stays its own.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PERF_F80 = ROOT / "tools" / "s8" / "perf_f80.py"
DEPS = {
    ("double", False): ["tools/s8/f80_double.py", "tools/s7/f80_legalize.py",
                        "tools/s8/f80_shims_double.c", "tools/s7/f80_shims.c"],
    ("exact", False): ["tools/s7/f80_legalize.py", "tools/s7/f80_shims.c"],
    ("double", True): ["tools/s8/f80_double.py", "tools/s7/f80_legalize.py", "tools/s8/perf_memfast.py",
                       "tools/s2/corpus_run/state_local.py", "tools/s8/f80_shims_double.c",
                       "tools/s7/f80_shims.c"],
}
# Imports of a checkout module inside a function the pass never calls: f80_double's fold check
# (cross_build's populations) and f80_legalize's selftest oracle (the PC24 pass's helpers).
IMPORTS_THE_PASS_NEVER_RUNS = {("tools/s8/f80_double.py", "cross_build"),
                               ("tools/s7/f80_legalize.py", "pc24pass")}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def perf_f80_deps(mode: str, memfast: bool = False) -> dict[str, str]:
    p = subprocess.run([sys.executable, str(PERF_F80), "deps", "--mode", mode]
                       + (["--memfast"] if memfast else []), capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def checkout_modules(name: str) -> list[str]:
    top = name.split(".")[0]
    return sorted(str(p.relative_to(ROOT)) for pat in (f"{top}.py", f"{top}/__init__.py")
                  for p in (ROOT / "tools").rglob(pat) if "__pycache__" not in p.parts)


@pytest.mark.parametrize("mode, memfast", list(DEPS))
def test_deps_name_the_pass_and_shims_inputs_at_their_sha256(mode, memfast):
    got = perf_f80_deps(mode, memfast)
    assert list(got) == DEPS[(mode, memfast)]
    assert got == {p: sha(ROOT / p) for p in DEPS[(mode, memfast)]}


@pytest.mark.parametrize("mode, memfast", list(DEPS))
def test_every_checkout_import_and_include_of_an_input_is_an_input(mode, memfast):
    inputs = DEPS[(mode, memfast)]
    for f in inputs:
        text = (ROOT / f).read_text()
        if f.endswith(".py"):
            for n in ast.walk(ast.parse(text)):
                names = ([a.name for a in n.names] if isinstance(n, ast.Import) else
                         [n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else [])
                for name in names:
                    found = checkout_modules(name)
                    assert not found or (f, name) in IMPORTS_THE_PASS_NEVER_RUNS or \
                        set(found) & set(inputs), f"{f} imports {name} ({found}), not among {inputs}"
        else:
            for line in text.splitlines():
                if re.match(r"\s*#\s*include", line):
                    m = re.match(r'\s*#\s*include\s*([<"])([^>"]+)[>"]', line)
                    assert m, f"{f}: {line!r}"
                    if m[1] == '"':
                        inc = str(((ROOT / f).parent / m[2]).resolve().relative_to(ROOT))
                        assert inc in inputs, f"{f} includes {inc}, not among {inputs}"
                    else:
                        assert not ((ROOT / f).parent / m[2]).exists(), \
                            f"{f} includes <{m[2]}>, a file beside it: by quote, so deps record it"


def test_the_release_manifest_sources_hold_every_input():
    spec = importlib.util.spec_from_file_location("release_build_deps_under_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    for mode in rb.PERF_F80_MODES:
        assert set(perf_f80_deps(mode)) <= set(rb.F80_SOURCE_GLOBS), mode
    assert rb.f80_deps("double") == perf_f80_deps("double")
