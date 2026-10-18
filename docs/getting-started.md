# Start working on OpenSA2

This guide sets up the source preview and runs installer and launcher checks without game files. It also identifies the build tools for developers who already have the required local inputs.

To play the experimental prebuilt version, follow the [download and setup guide](downloads.md). A complete source build needs your own supported 2012 PC installation of *Sonic Adventure 2*, an analyzed executable, translated modules, and built host dependencies. The public checkout does not yet provide a complete bootstrap for those inputs.

## Set up the checkout

Use a Linux development machine with Bash, GCC, a C++17 compiler available as `g++`, Binutils, and Bubblewrap. The sandbox cases use `bwrap` and require working unprivileged user namespaces. Python 3.13 matches the current development environment. Install your distribution's Python virtual-environment package if `python3 -m venv` reports that `ensurepip` is missing. The commands below run from the repository root.

1. Clone the repository.

   ```bash
   git clone https://github.com/kittycache9/OpenSA2.git
   cd OpenSA2
   ```

2. Create a Python environment.

   ```bash
   python3 -m venv .venv
   ```

3. Install the Python dependencies.

   ```bash
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. Create the ignored workspace used by the build-tool imports.

   ```bash
   mkdir -p private/generated
   ```

5. Check the public source.

   ```bash
   python3 tools/public_check.py
   ```

6. Run the synthetic installer and launcher tests.

   ```bash
   .venv/bin/python tools/test_source.py
   ```

The tests build the installer and launcher in temporary directories. They use small synthetic files, hand-built PE images, and stand-ins for the game and player. The command runs the source-only component cases. Comparisons against a local game installation, SDL3 dialog integration, and Windows cross-compilation cases remain in the test modules but are deselected here because they require additional local inputs. A passing result does not run the game or prove gameplay compatibility.

## Find the code for your change

Use the [architecture guide](architecture.md) to choose a starting point. The main areas are:

| Area | Source |
| --- | --- |
| Installation, file verification, and Steam discovery | `host/install/` |
| First launch, session control, and the stream relay | `host/launch/` |
| Graphics, sound playback, and input | `host/player/` and `host/d3d9gpu/` |
| Android setup and native session control | `host/android/` |
| Guest memory, Windows services, and guest devices | `runtime/` |
| Translation and compilation tools | `tools/s2/`, `tools/s3/`, and `tools/spike/` |

## Add a tweak

A tweak is an optional change to the game's behavior that a player turns on with the `SA2_TWEAKS` setting. A tweak adds its own file and lines and changes no existing line, so several tweaks can be written at the same time in separate branches:

1. Add one line for the tweak to `SA2_TWEAK_LIST` in `runtime/sa2_tweaks.h`.
2. Put its wraps in `runtime/tweaks/<name>.c`. A `TWEAK_WRAPS:` line in the file's header comment names the translated functions that the file wraps. The link tools find the file and its wraps without further changes.
3. Add one line for its switch to `TWEAKS` in `host/android/java/com/sa2recomp/app/GameSettings.java`.
4. Add a row to the [Tweaks](downloads.md#tweaks) table.

`Documentation/scripts/tests/test_tweaks.py` checks that the list, the files, and the Android lines agree. The header comment in `runtime/sa2_tweaks.h` gives the rules that every tweak follows.

## Understand the full build prerequisites

The game build is a developer workflow, with a Linux build host used for desktop and Android targets. The existing scripts expect a local toolchain and generated files under the ignored `private/` directory.

Before running a full build, prepare these inputs:

- A supported shipped `sonic2app.exe` and the matching unpacked executable used for translation.
- A Ghidra function map, translated LLVM modules, jump-table modules, extra blocks, and generated dispatch tables.
- The pinned compiler and translation tools, including Remill, LLVM-compatible Clang, and Zig for cross builds.
- SDL3 and the renderer dependencies, built with the expected project patches and configuration.
- The supported installation's file-verification table.
- For Android, the SDK, NDK, and Java tools expected by the Android builder.

`tools/env.sh` names the expected local layout. It adds existing tools to `PATH`; it does not install them or generate the game image. Consult each script's module documentation and `--help` where available before using it.

The current build entry points are:

| Task | Entry point | Prerequisite |
| --- | --- | --- |
| Translate mapped game functions | `tools/s3/whole_image/lift_all.py` | The analyzed executable, function map, Remill, and helper modules. |
| Rebuild the translated image after a translation change | `tools/s4/rebuild_image.py` | Existing mapped modules, jump-table modules, and extra-block inputs. |
| Build host dependencies | `tools/s5/build_host_deps.sh` | The pinned source archives, build tools, and platform headers. |
| Assemble a desktop build | `tools/s7/release_build.py` | The generated game image, host libraries, and verification inputs. |
| Compile and link the Android game | `tools/s8/android_game.py` | The translated image and Android toolchain. |
| Build the Android app | `tools/s8/android_app.py` | The Android game, renderer dependencies, and Android toolchain. |

These tools do not form a fresh-checkout build command yet. Keep generated code, binaries, and game files local. Do not commit them.

## Test a locally assembled desktop build

These commands apply only after you have assembled a complete development build. Run them from that build's root directory, which contains `bin/`, `share/`, and a build manifest.

On Linux, install your supported Steam copy and start the launcher:

```bash
bin/sa2-install --source steam --yes
bin/sa2recomp
```

On Windows, use PowerShell:

```powershell
bin\sa2-install.exe --source steam --yes
bin\sa2recomp.exe
```

If Steam discovery cannot find your copy, select the folder that contains `sonic2app.exe`:

```bash
bin/sa2-install --source folder "/path/to/Sonic Adventure 2" --yes
```

The installer also accepts `--source zip` for a ZIP made from your own game folder. It verifies the required files and prepares a separate local installation. Save and configuration writes use the port's own writable directory.

The launcher has a scripted title-to-menu check:

```bash
bin/sa2recomp --selftest
```

This check covers installation and startup. It does not check every stage or event. Read [current limitations](status.md) before a longer session.
