"""The pinned shader tools the R1 instruments run, each held to its pin before it runs.

Subtitle: tools/pins.json pins each file of private/s5-tools/install-shader-tools a check runs (the
spirv-tools entry's spirv-val, spirv-dis, spirv-as and spirv-opt; spirv-cross's command line;
sdl-shadercross's binary) and the shared libraries a tool loads (shadercross: SDL_shadercross's and
SPIRV-Cross's libraries and the install's SDL3). A file that does not hash to its pin is refused, never
run: an instrument whose tool moved under it measures with something no record names. The tools run
with LD_LIBRARY_PATH removed (tool_env), so each loads the libraries its rpath names, the pinned ones.
"""
import hashlib
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIELDS = {"spirv-val": ("spirv-tools", "binary"), "spirv-dis": ("spirv-tools", "disassembler"),
          "spirv-as": ("spirv-tools", "assembler"), "spirv-opt": ("spirv-tools", "optimizer"),
          "spirv-cross": ("spirv-cross", "binary"), "shadercross": ("sdl-shadercross", "binary")}
# the shared libraries each tool loads from the installs (ldd: shadercross's rpath names them; the others
# are linked statically against their own project)
LIBRARIES = {"shadercross": (("sdl-shadercross", "library"), ("spirv-cross", "library"), ("sdl3", "library"))}


class ToolRefused(Exception):
    pass


def _held(repo, key, field, name):
    entry = json.loads((Path(repo) / "tools" / "pins.json").read_text())["tools"].get(key, {})
    if field not in entry or f"{field}_sha256" not in entry:
        raise ToolRefused(f"tools/pins.json pins no {field} of {key} ({name})")
    path = Path(repo) / entry[field]
    if not path.is_file():
        raise ToolRefused(f"{name}: {entry[field]} is missing")
    sha = hashlib.sha256(path.resolve().read_bytes()).hexdigest()
    if sha != entry[f"{field}_sha256"]:
        raise ToolRefused(f"{name}: {entry[field]} hashes to {sha}, not its pin {entry[field + '_sha256']}")
    return path


def libraries(name, repo=REPO):
    """{pins key: path} of the libraries tool `name` loads, each held to its pin."""
    return {lkey: _held(repo, lkey, lfield, f"{name}'s {lkey} {lfield}") for lkey, lfield in LIBRARIES.get(name, ())}


def pinned(name, repo=REPO):
    """The path of the pinned tool `name`, once its file and every library it loads hash to their pins."""
    key, field = FIELDS[name]
    path = _held(repo, key, field, name)
    libraries(name, repo)
    return path


def tool_env():
    """The environment a pinned tool runs in: this one without LD_LIBRARY_PATH."""
    return {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
