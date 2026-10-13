# Current development status

OpenSA2 is a source preview of an experimental port. It has reached live gameplay on desktop and Android development builds. It has not completed a full-game compatibility pass.

This status reflects development on 2026-10-13. The repository does not provide a playable download or a complete fresh-checkout game build.

## Platforms

| Platform | What has been exercised | What the result means |
| --- | --- | --- |
| Linux, x86-64 | Native gameplay, sound, input, installation, startup, and renderer comparisons. | An established development path with incomplete game coverage. |
| Windows, x86-64 | Native installation, startup, and live gameplay. | Tested on native Windows as well as under Wine during development. Wider hardware coverage is unfinished. |
| Android, arm64 | Touch gameplay on a Galaxy S25 Edge and a Pixel 9 Pro. External-controller input is implemented. | Experimental device testing. GPU behavior, temperature, and the game path can affect results. |
| Linux, arm64 | Cross builds and registered replays under QEMU. | No general native-hardware support claim. |
| macOS | Shader-conversion and portability work remain. | Future work with no supported build. |

## What is implemented

The runtime can boot the original game logic and reach stages. Graphics, video, and decoded audio pass through the player. Desktop keyboard and controller input are implemented, including controller hot-plug. Android has touch controls and external controller input.

Metal Harbor has been played to the goal ring on the desktop renderers. City Escape has run on the tested Android devices. Those results do not establish compatibility for every stage, character, boss, story event, or mode.

The desktop installer supports Steam discovery, a selected game folder, and a ZIP made from that folder. It checks the supported files and prepares a separate local installation. The launcher verifies that installation, starts the game and player, and handles session shutdown.

## Known limitations

- An event after a stage can stop at a missing block in the CRT `_CItan` x87 fallback. The observed missing target is `0x00859AC8`. The sibling math paths and the affected event need further checks.
- Broad stage, boss, event, and game-mode testing is unfinished. Additional missing blocks can exist outside the exercised paths.
- Widescreen rendering is in development. The viewport and main HUD have been adjusted, but several menus and overlays still need work, including the boss gauge, stage map, title cards, and story-select art.
- The portable renderer currently depends on the project's SDL3 patches. Equivalent behavior with stock SDL3 is unfinished.
- The faster floating-point mode used by desktop development builds needs wider gameplay comparisons. A native x87 mode remains available for fidelity work.
- Android support is experimental. The tested phones do not represent every Android GPU or device, and sustained performance depends on temperature and workload.
- A complete public source-build bootstrap is unfinished. The build scripts still require local generated inputs and pinned dependencies.

## Work still needed

The immediate game-path work is the `_CItan` missing block and smoke tests across stages and events. Wider testing must then cover long sessions, saves, controllers, and rendering behavior on more hardware. Public build setup and dependency documentation also need work before the checkout can offer a complete build guide.

There are no release dates or platform-support promises. Check this page when reporting an issue, then include the steps and text diagnostics needed to reproduce it.
