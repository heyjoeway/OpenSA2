"""Authored compiler/runtime boundaries; the separate patch owner checks actual pixels."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "tools/s5/sdl3-patches/test/run_border_clamp.sh"


@pytest.mark.parametrize("compile_status", [47, 0])
def test_owned_temp_and_complete_compiler_failure(tmp_path, compile_status):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/python").symlink_to(sys.executable)
    install = repo / "private/s5-tools/install"
    (install / "bin").mkdir(parents=True)
    (install / "lib").mkdir()
    (install / "lib/libSDL3.so.0").write_bytes(b"authored library placeholder")
    glslang = install / "bin/glslang"
    glslang.write_text("#!/bin/sh\nexit 0\n")
    glslang.chmod(0o755)
    runtime = f"#!{sys.executable}\n" + '''
import json, os
print(json.dumps({"runtime_tmp": os.environ.get("TMPDIR"), "caller_marker": os.environ.get("SA2_TEST_CALLER_MARKER")}))
raise SystemExit(61)
'''
    compiler = repo / "compiler"
    compiler.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
target = Path(sys.argv[sys.argv.index("-o") + 1])
(target.parent / "compiler-observed.json").write_text(json.dumps(dict(os.environ)))
(target.parent / "compiler-observed-argv.json").write_text(json.dumps(sys.argv))
print("authored compiler stdout")
print("authored compiler stderr\\n" + "z" * 10000, file=sys.stderr)
if {compile_status}:
    raise SystemExit({compile_status})
target.write_text({runtime!r})
target.chmod(0o755)
''')
    compiler.chmod(0o755)
    (repo / "tools/pins.json").write_text(json.dumps({"tools": {"gcc": {"path": str(compiler)}}}))
    out = tmp_path / "out"
    result = subprocess.run(
        ["bash", str(RUNNER), str(install), str(out), "lvp"], capture_output=True, text=True,
        env={**os.environ, "SA2_REPO": str(repo), "TMPDIR": "/absent-authored-caller-temp",
             "TMP": "/absent-authored-caller-temp", "TEMP": "/absent-authored-caller-temp",
             "SA2_TEST_CALLER_MARKER": "must be stripped"})
    observed = json.loads((out / "compiler-observed.json").read_text())
    assert observed["TMPDIR"] == str(out / "tmp")
    assert Path(observed["TMPDIR"]).is_dir()
    assert not {"TMP", "TEMP", "SA2_TEST_CALLER_MARKER"} & observed.keys()
    argv = json.loads((out / "compiler.argv.json").read_text())
    expected = [str(compiler), "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wno-missing-field-initializers", "-o", str(out / "border_clamp"),
                str(RUNNER.parent / "border_clamp.c"), "-I" + str(install / "include"),
                "-L" + str(install / "lib"), "-lSDL3"]
    assert json.loads((out / "compiler-observed-argv.json").read_text()) == expected
    assert argv == ["env", "-i", f"HOME={os.environ['HOME']}", "PATH=/usr/bin:/bin",
                    f"TMPDIR={out / 'tmp'}", *expected]
    assert (out / "compiler.exit").read_text() == f"{compile_status}\n"
    assert (out / "compiler.stdout").read_text() == "authored compiler stdout\n"
    assert (out / "compiler.stderr").read_text() == "authored compiler stderr\n" + "z" * 10000 + "\n"
    assert "BORDER_CLAMP_OK" not in result.stdout
    if compile_status:
        assert result.returncode == 47
        assert result.stdout == "authored compiler stdout\n"
        assert result.stderr == (out / "compiler.stderr").read_text()
        assert not (out / "result.jsonl").exists()
    else:
        assert result.returncode == 1
        assert json.loads((out / "result.jsonl").read_text()) == {
            "runtime_tmp": str(out / "tmp"), "caller_marker": None}
        assert "BORDER_CLAMP_FAIL border_clamp exited 61" in result.stdout
