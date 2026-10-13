# OpenSA2

[![Source checks](https://github.com/kittycache9/OpenSA2/actions/workflows/source-checks.yml/badge.svg)](https://github.com/kittycache9/OpenSA2/actions/workflows/source-checks.yml)

**Sonic Adventure 2, recompiled for native platforms.**

OpenSA2 is an experimental native port of the 2012 PC release of *Sonic Adventure 2*. It translates the game's x86 machine code into native code at build time, then supplies a new runtime for graphics, audio, input, and operating-system services.

The aim is to preserve the game's original logic while making that logic run on new platforms. Linux and Windows builds have reached live gameplay. Android builds have run on real devices with touch input, and external-controller support is implemented. Work continues on game coverage and platform behavior.

[Get started](docs/getting-started.md) · [Architecture](docs/architecture.md) · [Current status](docs/status.md) · [Contribute](CONTRIBUTING.md)

## Source preview

This repository contains the project's source and tools. It includes no game executable, game assets, translated game code, or playable download. **The source cannot build a playable game on its own.** You need your own supported copy of the 2012 PC game, and the complete build needs generated inputs that this checkout does not include.

The public build setup is still in development. Start with the [developer guide](docs/getting-started.md) to run checks that use synthetic files and find the existing build entry points.

## What runs today

| Platform | Current state |
| --- | --- |
| Linux, x86-64 | Tested native gameplay and sound. Keyboard and controller input are implemented. |
| Windows, x86-64 | Tested development builds on native Windows, including installation and live gameplay. |
| Android, arm64 | Experimental builds tested on a Galaxy S25 Edge and a Pixel 9 Pro. Touch and external controllers are implemented. |
| Linux, arm64 | Cross-build and replay work under QEMU. General native-device support is unverified. |
| macOS | Future work. No supported build or compatibility promise. |

These are development results, not a claim of full-game parity. A known missing block in the CRT `_CItan` fallback can stop an event after a stage. Broad stage, boss, and event coverage remains unfinished. See the [status page](docs/status.md) before testing.

## How the port works

The build analyzes a supported executable, translates its instructions through Remill and LLVM, and compiles the result for the target CPU. The runtime keeps the game's guest addresses and provides the services that the original Windows program expects.

The game sends graphics and audio to a separate SDL3 player. The default renderer uses SDL3's GPU API. Linux also has a DXVK Native renderer used as a reference during development. Keyboard, controller, and touch input return through the game's own input interfaces.

This split lets the runtime and renderer be checked independently. Function tests compare machine state, replays compare game state over time, and renderer tests compare recorded calls and output. Passing a tested path does not prove that every path in the game works.

Read the [architecture guide](docs/architecture.md) for the code layout and current design.

## Help finish it

Useful contributions include reproducible bug reports, synthetic tests, build setup improvements, and fixes for untested game paths. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Share source changes and text diagnostics. Keep game files and captures that contain game data out of issues and pull requests.

## Credits and license

The project builds on [Remill](https://github.com/lifting-bits/remill), [LLVM](https://llvm.org/), [Ghidra](https://github.com/NationalSecurityAgency/ghidra), [SDL](https://github.com/libsdl-org/SDL), [DXVK Native](https://github.com/doitsujin/dxvk), and [dxbc-spirv](https://github.com/doitsujin/dxbc-spirv). Community research from the [SA2 Mod Loader](https://github.com/X-Hax/sa2-mod-loader) helps identify the original game's routines. [Unleashed Recompiled](https://github.com/hedge-dev/UnleashedRecomp) is a reference for the static-recompilation approach.

Original project code is licensed under [GPL-3.0-only](LICENSE). Third-party dependencies retain their own licenses and notices. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

OpenSA2 is an independent project by [kittycache](https://github.com/kittycache9). It is not affiliated with SEGA or Sonic Team. *Sonic Adventure 2* and its game assets belong to their respective rights holders.
