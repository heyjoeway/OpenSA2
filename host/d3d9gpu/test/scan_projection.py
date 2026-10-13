#!/usr/bin/env python3
"""scan_projection.py: the link between the state check's tuples and the stream censuses the
brief names (private/generated/s5/next/D/scan/{boot,menu,story,mh}.txt, made by scan.cpp from the
recorded streams), PLAN.md section 12.2 with the critic's M9.

Each pipeline input in a device keys file (host/d3d9gpu d9g_device.cpp write_keys_file) is
projected onto scan.cpp's PKey: (vs, ps, declaration, primitive type, stride, UP), the 48 render
states scan.cpp lists (read from scan.cpp's own pk_states, never typed here), the four render
target formats and the depth-stencil format by scan's rule (0 when ZENABLE and STENCILENABLE
are both 0). Each sampler record with a texture bound is projected onto scan's SKey (sampler
states 1-11). Then, per stream:

  * every census tuple (parsed from the .txt) must be the projection of at least one checked
    dump tuple; one that is not is UNCHECKED, and fails (M9: never explained away);
  * every dump tuple must project onto a census tuple (else the device saw a draw state the
    census did not: EXTRA, which fails too);
  * the counts are the census's "pipeline keys" and "sampler keys", read from the file;
  * what the projection's keys leave out but the facts read is held to the census's own records
    (the R2b checker-logic re-review's finding 8): each input's declaration elements to the
    census's "== decls" entry of its declaration; each bound render target's format and extent,
    and whether it is a texture's level, to the backbuffer the device was made with ("pp" in
    "== misc") or a target "== set render target" names; each bound depth-stencil surface's
    format and extent to the auto depth-stencil ("pp") or a CreateDepthStencilSurface ("== misc");
    each sampler record's texture format, per stage, to "== bound texture formats at draws", its
    mip level count to the census's texture creations of that format (a file texture's count is
    not in the census: counted unchecked, never passed), and a cube texture to a cube creation.

  scan_projection.py --dumps NAME=KEYS.json ... --scan DIR [--out RESULT.json]

SCAN_PROJECTION_OK only when every stream's projection equals its census, tuple for tuple.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def pk_states(scan_cpp: str) -> list[int]:
    m = re.search(r"static const int pk_states\[\] = \{([^}]*)\};", scan_cpp)
    if not m:
        raise SystemExit("scan.cpp: no pk_states")
    return [int(x) for x in m.group(1).split(",")]


def parse_census(txt: str) -> dict:
    """The census's tuples and counts, as scan.cpp printed them."""
    head = re.search(r"vs (\d+) ps (\d+) decls (\d+) pipeline keys (\d+) sampler keys (\d+)", txt)
    if not head:
        raise SystemExit("census: no counts line")
    pkeys, skeys = set(), set()
    section = None
    for line in txt.splitlines():
        if line.startswith("== sampler keys"):
            section = "s"
            continue
        if line.startswith("== pipeline keys"):
            section = "p"
            continue
        if line.startswith("=="):
            section = None
            continue
        if section == "s" and line.strip():
            vals = line.split()
            skeys.add(tuple(int(v, 16) for v in vals[:11]))
        elif section == "p" and line.strip():
            left, states, right = line.split("|")
            m = re.match(r"\s*vs(-?\d+) ps(-?\d+) d(-?\d+) p(\d+) s(\d+) up(\d+)\s*$", left)
            ids = tuple(int(x) for x in m.groups())
            st = tuple((int(k), int(v, 16)) for k, v in (kv.split("=") for kv in states.split()))
            tail = [int(x) for x in right.split()]
            pkeys.add((ids, st, tuple(tail[:5])))
    return {"vs": int(head.group(1)), "ps": int(head.group(2)), "decls": int(head.group(3)),
            "pipeline_keys": int(head.group(4)), "sampler_keys": int(head.group(5)), "pkeys": pkeys, "skeys": skeys}


def parse_resources(txt: str) -> dict:
    """The census's records of what the keys files' facts read beyond the projection: the
    declarations, the targets, the depth-stencil surfaces, the bound texture formats per stage
    and the texture creations."""
    sections: dict[str, list[str]] = {}
    cur = None
    for line in txt.splitlines():
        m = re.match(r"== (.+?)(?: \(\d+\))?$", line)
        if m:
            cur = m.group(1)
            sections.setdefault(cur, [])
            continue
        if cur is not None and line.strip():
            sections[cur].append(line.strip())
    decls = {}
    for line in sections.get("decls", []):
        m = re.match(r"decl(\d+):(.*)END$", line)
        if not m:
            raise SystemExit(f"census: a declaration line it cannot read: {line!r}")
        decls[int(m.group(1))] = [[int(x) for x in e] for e in
                                  re.findall(r"\[s(\d+) o(\d+) t(\d+) m(\d+) u(\d+) i(\d+)\]", m.group(2))]
    misc = sections.get("misc", [])
    pp = next((re.search(r"pp bb=(\d+)x(\d+) fmt=(\d+) count=\d+ ms=(\d+) q=(\d+) .*autods=(\d+) dsfmt=(\d+)", l)
               for l in misc if " pp bb=" in f" {l}"), None)
    if not pp:
        raise SystemExit("census: no device creation (pp) line in == misc")
    bb_w, bb_h, bb_fmt, ms, q, autods, dsfmt = (int(x) for x in pp.groups())
    rts = {(0, bb_fmt, bb_w, bb_h, False)}            # the backbuffer is RT0 from the device's creation
    rt_unknown = []
    for line in sections.get("set render target", []):
        m = re.search(r"rt(\d) (backbuffer|texlevel|other|null) fmt=(\d+) (\d+)x(\d+)$", line)
        if not m:
            raise SystemExit(f"census: a set-render-target line it cannot read: {line!r}")
        i, what, f, w, h = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))
        if what == "backbuffer":
            rts.add((i, bb_fmt, bb_w, bb_h, False))
        elif what == "texlevel":
            rts.add((i, f, w, h, True))
        elif what == "other":
            rt_unknown.append(line)
    dss = {(dsfmt, bb_w, bb_h)} if autods else set()
    for line in misc:
        m = re.search(r"CreateDepthStencilSurface (\d+)x(\d+) fmt=(\d+) ms=(\d+)", line)
        if m:
            dss.add((int(m.group(3)), int(m.group(1)), int(m.group(2))))
    bound = set()
    for line in sections.get("bound texture formats at draws", []):
        m = re.search(r"stage(\d+) fmt=(\d+) ", line)
        if not m:
            raise SystemExit(f"census: a bound-texture line it cannot read: {line!r}")
        bound.add((int(m.group(1)), int(m.group(2))))
    levels: dict[int, set[int]] = {}
    file_fmts, cube_created = set(), False
    for line in sections.get("texture creation", []):
        m = re.search(r"(?:d3dx|dev)create fmt=(\d+) usage=\S+ pool=\d+ levels=(\d+) (\d+)x(\d+)$", line)
        if m:
            f, lv, w, h = (int(x) for x in m.groups())
            if lv in (0, 0xFFFFFFFF):   # the full chain: down to 1x1
                lv = max(w, h).bit_length()
            levels.setdefault(f, set()).add(lv)
            continue
        m = re.search(r"file cont=\d+ kind=(\d+) fmt=(\d+) ", line)
        if not m:
            raise SystemExit(f"census: a texture-creation line it cannot read: {line!r}")
        file_fmts.add(int(m.group(2)))
        cube_created |= int(m.group(1)) != 0
    return {"decls": decls, "rts": rts, "rt_unknown": rt_unknown, "dss": dss, "ms": (ms, q), "bound": bound,
            "levels": levels, "file_fmts": file_fmts, "cube_created": cube_created}


def scan_stage(stage: int) -> int | None:
    """The census's stage of the device's sampler stage: 0-15 the same; 17-20 (the vertex
    samplers) scan.cpp's 16-19; 16 (the displacement map) none."""
    return stage if stage < 16 else 16 + stage - 17 if 17 <= stage <= 20 else None


def resources_check(dump: dict, res: dict) -> tuple[list[str], dict]:
    """The keys file's inputs and samplers held to the census's own records (see the module's
    docstring); problems fail, and the counts of what was compared go into the result."""
    problems: list[str] = []
    n = {"declarations": 0, "render_targets": 0, "depth_stencils": 0, "sampler_stage_formats": 0, "sampler_levels": 0,
         "sampler_levels_unchecked_file_textures": 0}
    for rec in dump["inputs"]:
        if rec["decl"]:
            want = res["decls"].get(rec["decl"] - 1)
            if want is None:
                problems.append(f"input {rec['id']}: declaration {rec['decl']} is not in the census's decls")
            elif [list(e) for e in rec["elems"]] != want:
                problems.append(f"input {rec['id']}: declaration {rec['decl']}'s elements {rec['elems']} are not the "
                                f"census's decl{rec['decl'] - 1} {want}")
            n["declarations"] += 1
        for i, rt in enumerate(rec["rt"]):
            fmt, w, h, ms, q, _level, bound, texture = rt
            if not bound:
                continue
            n["render_targets"] += 1
            if (i, fmt, w, h, bool(texture)) not in res["rts"] or (ms, q) != res["ms"]:
                problems.append(f"input {rec['id']}: render target {i} {rt} is not a target the census sets")
        fmt, w, h, ms, q, _level, bound, texture = rec["ds"]
        if bound:
            n["depth_stencils"] += 1
            if (fmt, w, h) not in res["dss"] or texture or (ms, q) != res["ms"]:
                problems.append(f"input {rec['id']}: depth-stencil surface {rec['ds']} is not one the census creates")
    for s in dump["samplers"]:
        if not s["tex"]:
            continue
        for st in range(21):
            if (s["stages"] >> st) & 1:
                n["sampler_stage_formats"] += 1
                if (scan_stage(st), s["fmt"]) not in res["bound"]:
                    problems.append(f"sampler {s['id']}: format {s['fmt']} at stage {st} is not a format the census binds there")
        if s["levels"] in res["levels"].get(s["fmt"], set()):
            n["sampler_levels"] += 1
        elif s["fmt"] in res["file_fmts"]:
            n["sampler_levels_unchecked_file_textures"] += 1
        else:
            problems.append(f"sampler {s['id']}: {s['levels']} mip levels of format {s['fmt']}, which no texture the census "
                            f"creates has")
        if s["cube"] and not res["cube_created"]:
            problems.append(f"sampler {s['id']}: a cube texture, and the census creates none")
    if res["rt_unknown"]:
        problems.append(f"the census sets render targets whose extent it does not record: {res['rt_unknown']}")
    return problems, n


def project(dump: dict, states: list[int]) -> tuple[set, set, list[str]]:
    order = dump["pipeline_render_states"]
    extra_order = dump["census_extra_render_states"]
    missing = [s for s in states if s not in order and s not in extra_order]
    if missing:
        raise SystemExit(f"the keys file records no value of census render states {missing} (d9g_state.h kCensusExtraRenderStates)")
    pk, problems = set(), []
    for rec in dump["inputs"]:
        rs = dict(zip(order, rec["rs"]))
        rs.update(dict(zip(extra_order, rec["extra"])))
        # scan.cpp counts DrawPrimitive and DrawPrimitiveUP calls (slots 81 and 83) only
        if rec["method"] not in (0, 2):
            problems.append(f"input {rec['id']}: an indexed draw (method {rec['method']}), which the census does not count")
            continue
        ids = (rec["vs"] - 1, rec["ps"] - 1, rec["decl"] - 1, rec["prim"], rec["stride0"], 1 if rec["method"] == 2 else 0)
        st = tuple((s, rs[s]) for s in states)
        rtf = tuple(t[0] if t[6] else 0 for t in rec["rt"])
        ds = rec["ds"]
        dsf = ds[0] if ds[6] and (rs.get(7, 0) or rs.get(52, 0)) else 0
        pk.add((ids, st, rtf + (dsf,)))
    sk = {tuple(r["ss"][1:12]) for r in dump["samplers"] if r["tex"]}
    return pk, sk, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dumps", nargs="+", required=True, help="NAME=KEYS.json (NAME the census file's stem)")
    ap.add_argument("--scan", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    scan = Path(a.scan)
    scan_cpp = (scan / "scan.cpp").read_text()
    states = pk_states(scan_cpp)
    result = {"schema": "sa2-d3d9gpu-scan-projection/v1", "scan_cpp_sha256": hashlib.sha256(scan_cpp.encode()).hexdigest(),
              "pk_states": states, "streams": {}}
    ok = True
    for spec in a.dumps:
        name, path = spec.split("=", 1)
        dump = json.loads(Path(path).read_text())
        census_txt = (scan / f"{name}.txt").read_text()
        census = parse_census(census_txt)
        pk, sk, problems = project(dump, states)
        res_problems, compared = resources_check(dump, parse_resources(census_txt))
        problems += res_problems
        unchecked = sorted(census["pkeys"] - pk, key=repr)
        extra = sorted(pk - census["pkeys"], key=repr)
        s_unchecked = sorted(census["skeys"] - sk)
        s_extra = sorted(sk - census["skeys"])
        stream_ok = (not unchecked and not extra and not s_unchecked and not s_extra and not problems and
                     len(pk) == census["pipeline_keys"] and len(sk) == census["sampler_keys"] and dump.get("complete"))
        ok &= stream_ok
        result["streams"][name] = {
            "keys_file": path, "keys_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "census_file": str(scan / f"{name}.txt"), "census_sha256": hashlib.sha256(census_txt.encode()).hexdigest(),
            "complete": dump.get("complete"),
            "census_pipeline_keys": census["pipeline_keys"], "projected_pipeline_keys": len(pk),
            "census_sampler_keys": census["sampler_keys"], "projected_sampler_keys": len(sk),
            "dump_inputs": len(dump["inputs"]), "dump_samplers_with_texture": sum(1 for r in dump["samplers"] if r["tex"]),
            "unchecked_census_pipeline_tuples": [repr(t) for t in unchecked],
            "extra_dump_pipeline_tuples": [repr(t) for t in extra],
            "unchecked_census_sampler_tuples": [list(t) for t in s_unchecked],
            "extra_dump_sampler_tuples": [list(t) for t in s_extra],
            "resources_compared": compared, "problems": problems, "pass": stream_ok,
        }
        print(f"{name}: census {census['pipeline_keys']} pipeline / {census['sampler_keys']} sampler keys; projected "
              f"{len(pk)} / {len(sk)}; unchecked {len(unchecked)} / {len(s_unchecked)}; extra {len(extra)} / {len(s_extra)}; "
              f"resources compared {compared}; {len(problems)} problems; {'pass' if stream_ok else 'FAIL'}")
        for pr in problems[:10]:
            print(f"   {pr}")
    result["pass"] = ok
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1) + "\n")
    print("SCAN_PROJECTION_OK" if ok else "SCAN_PROJECTION_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
