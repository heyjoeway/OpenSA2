# Third-party notices

OpenSA2's original project code is licensed under GPL-3.0-only. Third-party code and data retain their own notices and terms. This file records the dependencies and data used by the source repository. It does not grant rights to Sonic Adventure 2, its executable, or its assets.

## SDL3

The project uses SDL 3.4.16. The local patch at `patches/sdl3/0001-gpu-clamp-to-border-and-depth-clamp-with-clip.patch` changes sampler border addressing and depth clamp. It is an altered version prepared for this project and is not from the SDL project. The patch and the build's SDL vendor revision mark the changes.

The full SDL copyright and zlib license notice is preserved in [licenses/SDL3.txt](licenses/SDL3.txt).

## DXVK

The project uses DXVK 3.1.1 at commit `b1a1c99ab52b687cf950d62c88bc2fa316b41663`. Parts of the Direct3D state implementation follow DXVK's behavior and cite the upstream files. `host/d3d9gpu/test/ref_dxvk.cpp` contains transcriptions identified by `D9G_TRANSCRIBED` tags and preserves DXVK's notice. Build scripts also compile upstream DXVK units and generate extracted reference and shader-lowering code into ignored build output.

The full DXVK copyright and zlib/libpng license notice is preserved in [licenses/DXVK.txt](licenses/DXVK.txt). Keep the upstream copyright names and all existing file-level attribution when redistributing this source.

The shader translator links dxbc-spirv from DXVK's pinned submodule during a build. Its MIT notice is preserved in [licenses/dxbc-spirv.txt](licenses/dxbc-spirv.txt). Upstream DXVK and dxbc-spirv source trees are fetched separately and are not vendored in this repository.

## Remill

The runtime implements the `__remill_*` interface and uses Remill 5.0.7 at commit `c0f90b9e4a251615288ed4b65ead81cea617da8c`. `runtime/remill_runtime.cpp` and `runtime/sa2_emulate.cpp` include Remill's external x86 `State.h`. The installed upstream headers carry `Copyright (c) 2017 Trail of Bits, Inc.` and Apache-2.0 notices. The project does not vendor those headers.

The exact pinned upstream license is preserved in [licenses/Remill-Apache-2.0.txt](licenses/Remill-Apache-2.0.txt).

## Wine, Unicode, CLDR, and code-page data

`runtime/sa2_chardata.h` and `runtime/sa2_localedata.h` transform Wine's NLS data from the oracle's Proton installation. The generated headers and their generators identify and hash every NLS input. They contain character properties, case mappings, locale data, and code-page conversion tables. These tables are platform data, rather than game assets.

Wine's compilation and related implementation are attributed to the Wine project authors under LGPL-2.1-or-later. The installed Proton distribution's Wine attribution is preserved in [licenses/Proton-Wine-attribution.txt](licenses/Proton-Wine-attribution.txt). Wine's copyright and license statement is preserved in [licenses/Wine-LICENSE.txt](licenses/Wine-LICENSE.txt), and the LGPL 2.1 text is preserved in [licenses/Wine-LGPL-2.1.txt](licenses/Wine-LGPL-2.1.txt). This attribution applies to the Wine-derived material and does not replace the license notices of unrelated files.

The generators identify Unicode Consortium and CLDR data, and Microsoft's published code-page 1252 and 936 mappings, as upstream data origins. Unicode's data notice is preserved in [licenses/Unicode.txt](licenses/Unicode.txt). A CLDR release 48 notice is preserved in [licenses/CLDR-release48.txt](licenses/CLDR-release48.txt). These are notice references for the identified upstream data origins, not a claim that the pinned Proton binary was built from the current Wine branch or CLDR release 48. The original Unicode, CLDR, and Microsoft mapping source files are not packaged in this repository.

Upstream references: [Wine's data generator](https://github.com/wine-mirror/wine/blob/master/tools/make_unicode), [Unicode terms and licenses](https://www.unicode.org/copyright.html), [Microsoft's best-fit mapping catalogue](https://www.unicode.org/Public/MAPPINGS/VENDORS/MICSFT/WindowsBestFit/readme.txt), and [Microsoft's supported code-page data description](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-ucoderef/d167d756-0c58-4564-a0de-77c647d367fa).

## Community names

The analyzer can read the X-Hax SA2 Mod Loader headers as an external community name source. The headers are not vendored. Consult the [upstream repository](https://github.com/X-Hax/sa2-mod-loader) for its terms. Do not include those headers as part of this source distribution.

## Generated output and binary distributions

The source repository does not distribute the game's executable, assets, captures, lifted output, or generated executable overlay bytes. The handwritten native kernels in `runtime/sa2_ninja_math.c` and `runtime/sa2_cri_dsp.c` implement the original game's behavior; this project license does not assert ownership of the original game or third-party rights.

Binary distributions need a notice set for the components they actually contain. Depending on the target, builds use SDL, DXVK, dxbc-spirv, Remill, libpng, zlib, Zstandard, musl, MinGW-w64, LLVM runtimes, and other platform components. This source notice file is not a complete notice set for every prebuilt distribution. Those distributions must retain their component-specific notices and altered-source marks.
