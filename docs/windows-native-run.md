# Run a locally assembled Windows build

This guide applies to a complete development build assembled by the build tools.
The public source repository does not include a playable download.

Extract your build to a writable folder. Keep its `bin`, `lib`, and `share`
directories together. Supply your own supported 2012 PC installation of
Sonic Adventure 2.

From the extracted build's root, run these commands in PowerShell:

```powershell
bin\sa2-install.exe --source steam --yes
bin\sa2recomp.exe --selftest
bin\sa2recomp.exe
```

If Steam discovery cannot find your copy, select its folder:

```powershell
bin\sa2-install.exe --source folder "C:\Games\Sonic Adventure 2" --yes
```

The installer verifies the supported files and prepares separate writable saves
and configuration. The self-test exercises startup through the title and menu.
It does not establish compatibility with every stage, event, or game mode.

Include short text diagnostics when reporting a failure. Remove personal paths
and identifiers. Keep game files and recordings out of public reports.
