# Download and play OpenSA2

Get the builds from [GitHub Releases](https://github.com/kittycache9/OpenSA2/releases/tag/v0.1.0-alpha.2). The current release is an experimental prerelease. It has reached gameplay, but it has not completed a full-game compatibility pass. All three packages were updated to 0.1.0-alpha.2.1 on 2026-10-17, which plays the story movies. Read the [limitations](status.md) before testing.

You need your own supported, unmodified **2012 PC release of Sonic Adventure 2**. That is the version Steam sells and installs today. "2012" names the release, not an old build: do not look for an older copy. Install the game through Steam, remove any mods, and use its folder as it is. The installer checks every file it reads against the known hashes, so a modified, repacked, or downloaded copy fails the check.

The downloads include recompiled game logic and the port's runtime. They include no original game executable or assets. The installer verifies the files you supply and prepares a separate installation.

| Platform | Download | Requirements |
| --- | --- | --- |
| Windows, x86-64 | [Windows ZIP](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.2/OpenSA2-v0.1.0-alpha.2-windows-x86_64.zip) | Windows 10 or later and a Vulkan or Direct3D 12 graphics driver. |
| Android, arm64 | [Android APK](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.2/OpenSA2-v0.1.0-alpha.2-android-arm64.apk) | Android 10 or later, a 64-bit ARM device, and compatible Vulkan graphics. Device support is experimental. |
| Linux, x86-64 | [Linux DEB](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.2/OpenSA2-v0.1.0-alpha.2-linux-x86_64.deb) | Ubuntu 24.04, Debian 13, or a compatible distribution, with a working Vulkan driver. |

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

### Report a crash on Android

If the game stops, OpenSA2 returns to its setup screen and says so. Tap **Report on GitHub**. The app copies a crash report and opens the bug-report form with your device filled in. Paste the report into the **Crash report** box, then fill in the steps and what you expected. **Copy report** copies the whole report, **Copy summary** copies a shorter version that fits in a forum comment, and **Share report** sends it to another app. **Share OpenSA2** at the bottom of the screen sends a friend the download link. If the installer refuses your game files, the same buttons copy its output.

The report contains the app version, the device model, the GPU, the Android version, and the game's last log lines. It contains no game files or account details. Read it before you post it. The app also saves the last report as `Android/data/com.sa2recomp.app/files/crash-report.txt`, which a computer can read over USB.

This release uses a new release signing key. If you previously installed a development APK, Android may reject an update because the signing key differs. Back up that app's saves before uninstalling the development version. Public builds will keep the release key for updates.

## Linux

Download the DEB, then install it from that directory:

```bash
sudo apt install './OpenSA2-v0.1.0-alpha.2-linux-x86_64.deb'
```

Open OpenSA2 from your application menu, or run `opensa2`. The first start finds a Steam copy or offers a game-folder picker. A controller is optional.

Saves, settings, and logs use the port's per-user `sa2recomp` directories under your XDG data and configuration locations. The package does not write into your Steam installation.

## Source and checksums

The release provides `SHA256SUMS` for the downloadable files. The project source for all three packages is at the release tag. The [developer guide](getting-started.md) explains the local build inputs and the current limits of the public build setup.

All three packages include the math fixes from 2026-10-15. Report the platform and release version with any bug. Android shows the version in the app's title.
