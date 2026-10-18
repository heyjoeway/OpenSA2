# Code-patch tweaks

A Mod Loader `Patch` that writes the game's code bytes does nothing in this port, because the
lifted game never runs those bytes. `variants.py` makes such a patch a tweak: it lifts each module
the patch reaches a second time, from the patched bytes, under a symbol of its own, and generates
the C that picks one of the two at run time from `SA2_TWEAKS`.

A code-patch tweak is a tweak like any other (`runtime/sa2_tweaks.h`): a line in `SA2_TWEAK_LIST`,
a line in the Android settings (`GameSettings.TWEAKS`), a row in the player's guide. Where a plain
tweak has its runtime file, `runtime/tweaks/<name>.c`, a code-patch tweak has its spec,
`tweaks/<name>.json` here, and a line in `SA2_TWEAK_PATCH_LIST` ("Add a code-patch tweak" in the
header). `Documentation/scripts/tests/test_tweaks.py` holds the spec to both lists and to the
Android line.

`fixtures/` holds the tool's test fixtures. A fixture is no tweak: it is in no list and no settings
screen, and its name, which begins `test-`, is one no tweak has. `test-beetle-stays` (Mod Loader's
"Gold Beetle Never Disappears", a `jle` made a `jmp` inside 0x00505FA0) exercises the whole path on
a real lifted module that no tweak wraps. Mod Loader's "Disable Loading Tips" (a `ret` at
0x00428010) was the first example; the tweak `no-loading-tips` wraps that function, so a variant of
it would be refused.

## Commands

```bash
source tools/env.sh
# The checks alone: every tracked spec and fixture, the specs named, or every Patch entry of a Codes.lst.
.venv/bin/python tools/s8/variants/variants.py check
.venv/bin/python tools/s8/variants/variants.py check --codes <Codes.lst> [--patch "<entry name>"]
# The variants, for the lifted objects a game links (an f80 build: perf_f80.py, android_game.py):
# with no spec, every shipped one (none is fine); or the specs named, a fixture among them.
.venv/bin/python tools/s8/variants/variants.py build \
    --objects private/generated/s8/pc-double/linux-obj --out <dir under private/generated>
.venv/bin/python tools/s8/variants/variants.py build tools/s8/variants/fixtures/test-beetle-stays.json \
    --objects private/generated/s8/pc-double/linux-obj --out <dir under private/generated>
# Linked in:
tools/slot.sh .venv/bin/python tools/s7/release_build.py --f80-objects <the same objects> \
    --variants <dir> --out <dir> --work <dir>
tools/slot.sh .venv/bin/python tools/s8/perf_f80.py arm64-link --objects <aarch64 objects> \
    --out <dir> --replay boot --variants <dir built for those objects>
```

`check --codes` sorts every Patch entry into `variant` (the checks pass, and it names the modules),
`data` (every write is outside the code sections: a guest-memory poke, not a variant), `refused`
(with the reason) and `unread` (an operation the reader does not take).

## The spec

One JSON file per tweak in `tweaks/`, with exactly these keys:

| Key | What it holds |
| --- | --- |
| `name` | The `SA2_TWEAKS` spelling: lowercase words joined by hyphens. A shipped spec's file is `<name>.json`. |
| `report` | What the start-up line says when the tweak is on, at most 60 characters: for a shipped spec, its `SA2_TWEAK_LIST` report. |
| `functions` | The map functions the patch is in (`"0x00428010"`). Every patch lies inside one of them. |
| `patches` | `{"va", "old", "new"}`: the address and the bytes, in hex. `old` must be the image's bytes. |
| `source` | Where the patch comes from: the Codes.lst entry and its lines. |
| `why` | What the patched code does, read from the image. |

## The checks

`check` and `build` refuse a tweak, naming it, the address and the reason, when:

0. its name is a plain tweak's; a spec in `tweaks/` is not registered in both lists of
   `runtime/sa2_tweaks.h`, or its report is not the list's; or a spec elsewhere (a fixture) has the
   name of a registered tweak;
1. a patch is outside the code sections (a data write), its `old` bytes are not the image's, it is
   outside the functions the spec names, or two patches overlap;
2. a changed byte is in no lifted module's trace (remill's recursive descent from the module's
   entry, `tools/s3/arms/cost.py` `walk_detail`): data inside the code section, such as a jump table,
   or code no trace reaches;
3. a module the patch reaches (a map function, an S3 arm, a lifted extra) is already wrapped
   (`tools/s4/link_s4.py` `PRODUCTION_WRAPS`, or a tweak's `runtime/tweaks/<name>.c`), or another
   tweak in the same build patches it;
4. in a module's patched trace: an instruction does not decode; an edge leaves the module to an
   address where no module starts, or one the missing-block edge table would not dispatch (an
   edge to an entry the table dispatches, by a `jmp` row or a reviewed exception, is accepted and
   recorded as `edges_added`); a call goes where no module starts; or an instruction remill cannot
   lift has no lifted continuation.

`build` then makes each module's variant:

1. **The identity proof.** The unpatched bytes go through this checkout's lift
   (`tools/s4/lift_extra.py` `lift_one`, the remill command and the PC24 pass every population
   uses), fold (`internalize.rewrite`), f80 pass and the objects' own compile command. The IR must
   hash to the objects' `build.jsonl` record and the object must equal the build's byte for byte.
   The variant is made by the same steps, so it differs from the module beside it by the patch
   alone.
2. **The variant.** The same lift of the patched bytes. remill's PC markers in the module must
   equal the checked walk's count (instructions and exits), so the lifter followed the patched
   control flow as the checks did. Every reference to `sub_<va>` becomes `sub_<va>__tw_<tweak>`.
   The object must define exactly that symbol, declare only symbols lifted modules define, and
   pass the f80 census.

It writes `obj/ir_<VA>__tw_<tweak>.o`, `sa2_tweak_variants.c` and `variants.rec` (JSON; not
`.json`, since `tools/s4/m7_inputs.py` reads every `*.json` under `private/generated` as a run
record).

## At run time

`sa2_tweak_variants.c` defines `sa2_tweak_patches_linked` (`runtime/sa2_tweaks.h`), which names the
tweaks the game links to `runtime/sa2_tweaks.c`, and one `__wrap_sub_<va>` per module. A registered
tweak keeps its `SA2_TWEAK_LIST` bit and report; a fixture takes a bit after the list's. A link
passes `-wrap=sub_<va>`, so every reference to the module reaches the wrap: direct calls from lifted
code, the function table and the extras' registry. With the tweak off, the wrap only calls
`__real_sub_<va>`. With it on (`sa2_tweak_patch_on`), the wrap calls the variant and prints one line
the first time: `sa2 tweaks: <report>: the patched lift of 0x<va> runs`. `SA2_TWEAKS` is read once
at the start and ignored in a replay, so a replay always runs the lifted originals. The function
table and the edge table do not change.

A game linked without a registered code-patch tweak (no `--variants`, or variants built without its
spec) still knows its name: the start-up line says the game was built without it, and leaves it off.
The release's README.txt lists only the code-patch tweaks the game links.

`release_build.py --variants`, `arm64_link.py --variants` and `android_game.py link --variants`
call `linked`. It refuses variants built for other lifted objects than the link's, a file that
changed since the build, a shipped tweak no longer registered or whose spec changed, a fixture whose
name a tweak has taken, or, when a variant adds an edge, an edge table that changed since; the links
refuse a wrap they already have.

## Limits

- One tweak per module. Two tweaks that patch the same module would need a variant holding both,
  which is not built.
- A patch whose patched code leaves its module by an edge the runtime would not dispatch is refused.
  Allowing it needs a reviewed dispatch exception (`Documentation/dispatch-exceptions.json`) first.
- An indirect jump or call that a patch adds is not checked: the runtime resolves it through the
  function table, and a target with no lifted module traps by name.
- A write into data inside a code section, such as a jump table, is refused. A guest-memory poke
  could apply it, since the lifted code reads that data from guest memory.
- Variants are built for a legalised lifted-object build only (the double or exact f80 mode). The
  native-x87 staged objects and the Windows game's link (`tools/s7/win_link.py`) do not take them
  yet.
- A function the runtime or a tweak's file already wraps cannot have a variant until its wrap calls
  the variant itself.
- The Codes.lst reader takes `write8`, `write16`, `write32` (with `xN`), `writenop` and `writejump`.
  It names any other operation. `Code` entries run every frame and are not patches.
