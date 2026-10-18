# Download and play OpenSA2

Get the builds from [GitHub Releases](https://github.com/kittycache9/OpenSA2/releases/tag/v0.1.0-alpha.3.1). The current release is an experimental prerelease. It has reached gameplay, but it has not completed a full-game compatibility pass. Read the [limitations](status.md) before testing.

You need your own supported, unmodified **2012 PC release of Sonic Adventure 2**. That is the version Steam sells and installs today. "2012" names the release, not an old build: do not look for an older copy. Install the game through Steam, remove any mods, and use its folder as it is. The installer checks every file it reads against the known hashes, so a modified, repacked, or downloaded copy fails the check.

The downloads include recompiled game logic and the port's runtime. They include no original game executable or assets. The installer verifies the files you supply and prepares a separate installation.

| Platform | Download | Requirements |
| --- | --- | --- |
| Windows, x86-64 | [Windows ZIP](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.3.1/OpenSA2-v0.1.0-alpha.3.1-windows-x86_64.zip) | Windows 10 or later and a Vulkan or Direct3D 12 graphics driver. |
| Android, arm64 | [Android APK](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.3.1/OpenSA2-v0.1.0-alpha.3.1-android-arm64.apk) | Android 10 or later, a 64-bit ARM device, and Vulkan 1.1 or later graphics. Device support is experimental. |
| Linux, x86-64 | [Linux DEB](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.3.1/OpenSA2-v0.1.0-alpha.3.1-linux-x86_64.deb) | Ubuntu 24.04, Debian 13, or a compatible distribution, with a working Vulkan driver. |

## Windows

1. Extract the entire ZIP to a writable folder. Keep its `bin` and `share` folders together.
2. Run `OpenSA2.cmd`.
3. Let the installer find your Steam copy, or select the game folder or a ZIP made from that folder.

The Windows build is unsigned. Windows may ask you to confirm that you want to run the downloaded program. Saves, settings, and logs use `%LOCALAPPDATA%\sa2recomp`.

## Android

1. On your computer, open the game's folder. In Steam, right-click Sonic Adventure 2 and select **Manage** > **Browse local files**.
2. Put everything in that folder into a ZIP. The archive must include `sonic2app.exe` and the `resource` folder.
3. Copy the ZIP to your device.
4. Download the APK on your device and install it. Android may require permission for your browser or file manager to install apps.
5. Open OpenSA2, tap **Choose game ZIP**, and select your ZIP.

The app copies the ZIP, then shows "Checking the game ZIP" while it verifies and imports the files into its own storage. This can take a few minutes, and the screen can look stuck. Keep the app open until the game starts. You can then delete the ZIP. If a file fails the check, the setup screen names it.

The app supports touch controls and external controllers. The touch controls appear when you touch the screen.

### Game settings on Android

Tap **Game settings** on the setup screen. Changes apply the next time the game starts. App updates keep your settings.

- **Orientation** is Landscape by default. **Portrait** lets you hold the phone upright: the game is drawn at 4:3 across the screen, in the middle of its upper half, and the touch controls sit in the space below the picture. **Picture position** moves the picture anywhere from the top of the screen to its middle. **Auto-rotate** follows the phone when its own auto-rotate is on. The game is then drawn at 4:3 so that it fits either way.
- **Full-width picture** draws the game at the screen's shape. Turn it off to keep the game's own 16:9 picture, with bars at the sides of a wider screen.
- **Aspect ratio** is the screen's shape by default. You can choose 21:9, 16:9, or 4:3 instead.
- **Resolution** is the largest picture the screen holds. Fewer lines can run faster on a slower phone.
- **Low-FPS speed fix (experimental)** keeps stages at their normal speed when the phone cannot draw 60 frames per second. **30 FPS** and **20 FPS** run two or three game ticks for each drawn frame, and **Auto** chooses one, two, or three as the game runs. Menus, cutscenes, and movies still slow down on a slow phone. It is off by default. See [Low-FPS speed fix](#low-fps-speed-fix-experimental) for what to expect.
- **HUD at 4:3 positions** keeps the score, time, and rings near the middle of a wide picture. It is on by default.
- **Touch controls** sets the size and opacity of the on-screen controls. At 0% opacity the controls are hidden but still work. **Move the controls** lets you drag each control to a new place, and **Change what the touch buttons do** changes the game button each one sends.
- **Controller** changes the game button that each controller button sends. Buttons are named by position: the bottom face button is A, as on an Xbox controller.
- **Tweaks** are optional changes to the game, each off by default. Turn on **Remove cutscene black bars** to remove the black bars that most in-engine cutscenes draw at the sides of a wide picture. See [Tweaks](#tweaks) for the cutscenes that keep their bars.

**Reset all settings** restores the defaults.

### Report a crash on Android

If the game stops, OpenSA2 returns to its setup screen and says so. Tap **Report on GitHub**. The app copies a crash report and opens the bug-report form with your device filled in. Paste the report into the **Crash report** box, then fill in the steps and what you expected. **Copy report** copies the whole report, **Copy summary** copies a shorter version that fits in a forum comment, and **Share report** sends it to another app. **Share OpenSA2** at the bottom of the screen sends a friend the download link. If the installer refuses your game files, the same buttons copy its output.

The report contains the app version, the device model, the GPU, the Android version, and the game's last log lines. It contains no game files or account details. Read it before you post it. The app also saves the last report as `Android/data/com.sa2recomp.app/files/crash-report.txt`, which a computer can read over USB.

This release uses a new release signing key. If you previously installed a development APK, Android may reject an update because the signing key differs. Back up that app's saves before uninstalling the development version. Public builds will keep the release key for updates.

## Linux

Download the DEB, then install it from that directory:

```bash
sudo apt install './OpenSA2-v0.1.0-alpha.3.1-linux-x86_64.deb'
```

Open OpenSA2 from your application menu, or run `opensa2`. The first start finds a Steam copy or offers a game-folder picker. A controller is optional.

Saves, settings, and logs use the port's per-user `sa2recomp` directories under your XDG data and configuration locations. The package does not write into your Steam installation.

## Tweaks

Tweaks are optional changes to how the game looks or plays. Each one is off until you turn it on, and replays and the launcher's self-test never use them. Tweaks need a release newer than v0.1.0-alpha.3.1.

| Name | Change |
| --- | --- |
| `2p-layout-in-1p` | Thirteen one-player stages load their leftover two-player object layout, with other rings, items, and enemies. Hard Mode, Pumpkin Hill, and Iron Gate keep their own layout. These layouts leave out most upgrades and Chao key boxes, and some switches and animals, so a mission may not be finishable. Clears, ranks, and emblems still save, but an upgrade that the layout leaves out cannot be collected or saved. |
| `all-events-skippable` | Start skips every cutscene, including the Hero story ending and the Last story finale, which the game otherwise makes you watch. |
| `analog-fix` | A gamepad stick reaches full speed at about two thirds of a push instead of before half, so half a push walks and a full push still runs. The keyboard plays as before. |
| `chao-key-always` | After every stage you clear in one-player play, you go to Chao World as if you had found the Chao Key. Your save may record the key as found. |
| `chao-stat-x5` | Chao gain up to five times the stat points from Chaos Drives, small animals, and fruit, like the Mod Loader's "Chao Stat Multiplier" code. An animal or a drive gives at most 100 points to a stat, so a stat never loses points. The extra points are saved with your Chao and stay after you turn the tweak off. |
| `drives-to-minimals` | Destroyed enemies leave a small animal instead of a Chaos Drive, which changes what you can collect for your Chao. |
| `event-bars-all` | Draws black bars at the sides of every in-engine cutscene on a screen wider than 4:3, so each one looks as it does on a 4:3 screen. Movies are unchanged. It cannot be combined with `no-event-bars`: with both set, neither applies. |
| `fast-stage-load` | Starts a stage as soon as its files are loaded. The game no longer waits at the end of the loading screen, so the loading screen and its tip can end sooner. Chao World loads as usual. |
| `gold-beetle-stays` | A Gold Beetle that appears no longer flies away when its time runs out; it stays until you destroy it. This raises scores, so the scores and ranks the game saves are higher than in the original game. |
| `hide-hud` | Hides the score, time, rings, and lives counters during play, and the whole kart race display, for a clean picture. Button prompts and the radar stay, and the game plays the same. |
| `hide-radar` | Treasure hunts (Knuckles and Rouge) show no emerald radar and no "!" over a piece, and play no radar beep. You find the pieces by eye and with the hint monitors. Your save is not changed. |
| `infinite-lives` | Dying costs no life, so the lives never run out and there is no game over. 1-ups still add lives, and your save keeps the count you actually have. |
| `metal-bounce-fix` | Gives Metal Sonic's bounce attack a model he has, so the attack no longer crashes the game. The crash happens only when a cheat gives Metal Sonic all upgrades, so this changes nothing in normal play. |
| `no-2p-specials` | In 2-player battles, neither player earns special attacks, from collecting rings or after losing a life. 1-player games and save data are unchanged. |
| `no-event-bars` | Removes the black bars that most in-engine cutscenes draw at the sides of a screen wider than 4:3. The scene then fills the width of the screen. |
| `no-lives-bonus` | Collecting 100 rings (or 200, 300, and so on) no longer gives an extra life or plays the 1-up jingle. 1-up boxes and other extra lives still count, and your save keeps the lives you earned. |
| `no-loading-tips` | Hides Omochao's tips and the animated Now Loading ring while the game loads. Most loads show a black screen instead. A load over a picture already on screen, such as a stage's title card, keeps that picture. |
| `no-powerup-music` | The speed shoes and invincibility item boxes no longer play their own tune, so the stage music keeps playing. The power-ups still work as before. |
| `quiet-omochao` | Omochao, the hint robot, no longer appears in the stages, so it gives no hints. The Chao Garden is unchanged. |
| `radar-all-pieces` | In treasure hunts (Knuckles and Rouge), the radar shows how near every piece still to find is, not only the first one. This makes hunts easier. Your save is not changed. |
| `ring-loss-cap` | When you get hit, you lose at most 20 rings instead of all of them. The game scatters at most 20 rings on a hit, so you can pick every lost ring back up. Getting back to 100 rings this way gives no second extra life. Scores and ranks you earn with it on are higher than normal and are saved as usual. |
| `rival-upgrades` | Shadow, Rouge, and Mech Tails get the upgrades Sonic, Knuckles, and Eggman have found: Shadow can use the Bounce Bracelet and Magic Gloves, Rouge never runs out of air underwater, and Mech Tails gets the Protective Armor's extra health. Your save is not changed. |
| `skip-intro` | Starts the game at the title screen, skipping the company logos and the opening cutscene. Loading tips still appear. The opening still plays if you leave the title screen idle. |
| `walker-cannon-fix` | Fixes a crash when a Chao Walker that has the Mech's cannon upgrade fires its cannon. Only a cheat gives a Chao Walker that upgrade, so without one this tweak changes nothing. |

With `no-event-bars` on, five in-engine cutscenes keep their bars: the opening one (event 361) and events 21, 205, 206, and 360. These scenes are framed for 4:3. The opening draws its title cards, flashes, backdrops, and logo glow over the middle 4:3 part of the picture only, so without the bars those effects would stop at a hard edge. SA2 Render Fix keeps the bars for the same five cutscenes for this reason. Movies also keep their bars, because the video itself is 4:3.

On Android, use the **Tweaks** card in **Game settings**. On Windows and Linux, add a line to the release's `share/game.env` file:

```
SA2_TWEAKS=no-event-bars
```

On Windows the file is `share\game.env` in the extracted folder. For the Linux package it is `/usr/lib/opensa2/share/game.env`, which you edit as root; a package update replaces it. Separate several tweak names with commas. Setting `SA2_TWEAKS` in a shell has no effect, because the launcher passes only `game.env`'s settings to the game.

The launcher prints the tweaks it passes, and each session's game log starts with a line that names the active tweaks. A name that is not a tweak is reported by name in that line and ignored. Some tweaks change the player or the launcher instead of the game. The game's line names them as host tweaks, and the player says which of its tweaks are on in its own log, `player.err` (on Android, the session report). The launcher says which of its own are on in `launcher.log`.

## Source and checksums

The release provides `SHA256SUMS` for the downloadable files. The project source for all three packages is at the release tag. The [developer guide](getting-started.md) explains the local build inputs and the current limits of the public build setup.

All three packages include the math fixes from 2026-10-15. Report the platform and release version with any bug. Android shows the version in the app's title.

## Low-FPS speed fix (experimental)

The game advances one tick for each frame it draws, so on a device that cannot draw 60 frames per second the whole game slows down. The low-FPS speed fix runs two or three game ticks for each drawn frame in stages, so a stage keeps its normal speed at 30 or 20 frames per second. **Auto** chooses one, two, or three ticks as the game runs, from how long the game takes and how fast frames reach the screen. When drawing, not the game, holds the frame rate down, Auto keeps the higher count and tries the lower one again after 30 seconds, then less often. The fix needs a release newer than v0.1.0-alpha.3.1.

What to expect:

- Only stages change. Menus, the pause menu, cutscenes, and movies still draw one frame per tick, so they slow down on a slow device as before. The fade into and out of a stage also counts drawn frames, so it takes longer.
- A button press counts once, even when a frame runs several ticks.
- Sound effects from the ticks of one frame can start together, up to about 33 ms early at 20 frames per second.
- The level-up message window is drawn once for each tick, over itself, so it can look more opaque.
- In the 100-ring mission, the game checks for 100 rings once per drawn frame, so the mission can end up to two ticks after the 100th ring. If you lose rings in those ticks, collect 100 again.
- Replays and tests never use the fix.

On Android, choose it under **Low-FPS speed fix (experimental)** in **Game settings**. On Windows and Linux, add one line to the release's `share/game.env` file:

```
SA2_PLAY_FRAMESKIP=2
```

Use `2` for 30 frames per second, `3` for 20 frames per second, or `auto`. On Windows the file is `share\game.env` in the extracted folder. For the Linux package it is `/usr/lib/opensa2/share/game.env`, which you edit as root; a package update replaces it. Setting `SA2_PLAY_FRAMESKIP` in a shell has no effect, because the launcher passes only `game.env`'s settings to the game.

Each session's game log starts with a line that names the setting, or says why a value was not used. During stage play it adds a line every 10 seconds: the frames drawn at each tick count, frame times, and the stage timer's progress against the wall clock, where 100% means the stage ran at its normal speed.
