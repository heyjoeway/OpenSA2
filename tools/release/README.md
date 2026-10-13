# Package a tested build

These tools package an existing local runtime. They do not translate or compile the game. Keep runtime inputs, packaging output, privacy terms, and Android signing keys outside the tracked source tree.

Run the checks with:

```bash
python3 -m unittest discover -s tools/release -p 'test_*.py'
```

The Windows packer copies the tested runtime without changing its binaries. It adds the OpenSA2 entry point, instructions, licenses, and a file manifest, and omits development debug files.

```bash
python3 tools/release/package_windows.py \
  --release /path/to/tested/windows-x86_64 \
  --source-root . \
  --output /path/to/output/OpenSA2-windows-x86_64.zip
```

The Linux and Android packers preserve machine code. They remove debug data from copied executables and can replace validated assertion filename strings with public diagnostic paths. Their verification compares the allocated ELF sections and permits only those exact diagnostic changes. Linux libraries whose full-file hashes form part of the player's contract must stay unchanged.

Consult each packer's `--help` for toolchain, input, output, and signing arguments. Android signing material belongs in a separate private directory. Back up the release key and its password securely; future APK updates must use the same key.

Read each completed package back before upload:

```bash
python3 tools/release/check_package.py /path/to/output/package.zip \
  --deny-file /path/to/private/identity-terms.json \
  --forbidden-hashes /path/to/private/install-hashes.json \
  --report /path/to/private/package-check.json
```

The private terms file has the shape `{"terms": ["private identifier", "private path"]}`. The checker also accepts APK and DEB files. It checks archive paths, duplicate ZIP members, private terms in UTF-8 and UTF-16LE, original executable filenames, development files, and original-game hashes. An executable/tool pins file limits the forbidden set to its `executables` section; dependency and patch hashes are not game-file hashes.

These checks establish package integrity and source separation. They do not prove gameplay compatibility. Exercise the packaged installer and launcher, and test the final signed Android APK, before publishing. Release notes must state the tested scope and remaining limitations.
