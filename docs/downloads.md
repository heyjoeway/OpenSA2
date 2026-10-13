# Download and play OpenSA2

Get the builds from [GitHub Releases](https://github.com/kittycache9/OpenSA2/releases/tag/v0.1.0-alpha.1). The first release is an experimental prerelease. It has reached gameplay, but it has not completed a full-game compatibility pass. A known missing block can stop an event after a stage. Read the [limitations](status.md) before testing.

You need your own supported, unmodified **2012 PC release of Sonic Adventure 2**. The downloads include recompiled game logic and the port's runtime. They include no original game executable or assets. The installer verifies the files you supply and prepares a separate installation.

| Platform | Download | Requirements |
| --- | --- | --- |
| Windows, x86-64 | [Windows ZIP](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.1/OpenSA2-v0.1.0-alpha.1-windows-x86_64.zip) | Windows 10 or later and a Vulkan or Direct3D 12 graphics driver. |
| Android, arm64 | [Android APK](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.1/OpenSA2-v0.1.0-alpha.1-android-arm64.apk) | Android 10 or later, a 64-bit ARM device, and compatible Vulkan graphics. Device support is experimental. |
| Linux, x86-64 | [Linux DEB](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.1/OpenSA2-v0.1.0-alpha.1-linux-x86_64.deb) | Ubuntu 24.04, Debian 13, or a compatible distribution, with a working Vulkan driver. |

## Windows

1. Extract the entire ZIP to a writable folder. Keep its `bin` and `share` folders together.
2. Run `OpenSA2.cmd`.
3. Let the installer find your Steam copy, or select the game folder or a ZIP made from that folder.

The Windows build is unsigned. Windows may ask you to confirm that you want to run the downloaded program. Saves, settings, and logs use `%LOCALAPPDATA%\sa2recomp`.

## Android

1. Copy your own PC game installation into a ZIP. The archive must include `sonic2app.exe` and the game files.
2. Download the APK on your device and install it. Android may require permission for your browser or file manager to install apps.
3. Open OpenSA2 and choose your game ZIP in the setup screen.

The app verifies and imports the files into its own storage. It supports touch controls and external controllers. Keep the game ZIP until setup has completed.

This release uses a new release signing key. If you previously installed a development APK, Android may reject an update because the signing key differs. Back up that app's saves before uninstalling the development version. Public builds will keep the release key for updates.

## Linux

Download the DEB, then install it from that directory:

```bash
sudo apt install './OpenSA2-v0.1.0-alpha.1-linux-x86_64.deb'
```

Open OpenSA2 from your application menu, or run `opensa2`. The first start finds a Steam copy or offers a game-folder picker. A controller is optional.

Saves, settings, and logs use the port's per-user `sa2recomp` directories under your XDG data and configuration locations. The package does not write into your Steam installation.

## Source and checksums

The release provides `SHA256SUMS` for the downloadable files. It also provides the project source snapshot matching the desktop builds. Android's project source is at the release tag. The [developer guide](getting-started.md) explains the local build inputs and the current limits of the public build setup.

The desktop packages use an earlier tested development snapshot than Android. Later Android performance and display changes are not all included in the desktop runtime. Report the platform and release version with any bug.
