# Current development status

OpenSA2 is an experimental port with Windows, Android, and Linux downloads. It has reached live gameplay on desktop and Android devices. It has not completed a full-game compatibility pass.

This status reflects development on 2026-10-16. The [prereleases](downloads.md) use tested development snapshots. A complete fresh-checkout game build remains unfinished.

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

- In the Windows and Linux 0.1.0-alpha.2 packages, every story movie stays black and silent and never ends, starting with the Prison Island movie after Green Forest. Sofdec's B-picture slice loop (`0x00872650`) sits in the executable section PSFD00, which the address-taken scan did not cover, so the movie decoder's thread ended on a function table miss while it held the movie library's lock. The function is lifted in the Android APK 0.1.0-alpha.2.1, where the movies play, and the scan now covers every executable section. Earlier builds stopped at the same event on a missing block in the CRT `_CItan` x87 fallback (`0x00859AC8`), fixed on 2026-10-15.
- The renderer needs Vulkan 1.3. A device whose driver offers an older version, or lacks a feature the renderer asks for, stops before the first frame. The Android setup screen warns when the system reports an older Vulkan version.
- Broad stage, boss, event, and game-mode testing is unfinished. Additional missing blocks can exist outside the exercised paths.
- Widescreen rendering is in development. The viewport and main HUD have been adjusted, but several menus and overlays still need work, including the boss gauge, stage map, title cards, and story-select art.
- The portable renderer currently depends on the project's SDL3 patches. Equivalent behavior with stock SDL3 is unfinished.
- The faster floating-point mode used by desktop development builds needs wider gameplay comparisons. A native x87 mode remains available for fidelity work.
- Android support is experimental. The tested phones do not represent every Android GPU or device, and sustained performance depends on temperature and workload.
- A complete public source-build bootstrap is unfinished. The build scripts still require local generated inputs and pinned dependencies.

## Work still needed

The immediate game-path work is smoke tests across stages and events, including the Dark story's movies and the real-time events that embed movies. Wider testing must then cover long sessions, saves, controllers, and rendering behavior on more hardware. Public build setup and dependency documentation also need work before the checkout can offer a complete build guide.

There are no release dates or platform-support promises. Check this page when reporting an issue, then include the steps and text diagnostics needed to reproduce it. On Android, the setup screen's **Report on GitHub** button copies a crash report and opens the bug-report form.
