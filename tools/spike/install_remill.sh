#!/usr/bin/env bash
# Fetch remill's last release with binary assets (v5.0.7, official GitHub
# release, ubuntu-20.04 .deb packages) into private/lifters and unpack the
# .deb files in user space with `ar`/`tar` (no dpkg, no sudo).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="$ROOT/private/lifters"
URL="https://github.com/lifting-bits/remill/releases/download/v5.0.7/remill_ubuntu-20.04_packages.zip"
ZIP="$DEST/remill_ubuntu-20.04_packages.zip"
mkdir -p "$DEST"
if [ ! -s "$ZIP" ]; then
  curl -L --fail --retry 3 -o "$ZIP" "$URL"
fi
SHA=$(sha256sum "$ZIP" | cut -d' ' -f1)
printf '{"lifter":"remill","url":"%s","tarball":"%s","sha256":"%s","bytes":%s}\n' \
  "$URL" "$ZIP" "$SHA" "$(stat -c %s "$ZIP")" > "$DEST/remill.download.json"
mkdir -p "$DEST/remill-pkg" "$DEST/remill"
( cd "$DEST/remill-pkg" && unzip -o -q "$ZIP" && ls -la )
for deb in $(find "$DEST/remill-pkg" -name '*.deb'); do
  echo "== $deb"
  ( cd "$DEST/remill" && ar x "$deb" && for d in data.tar.*; do tar -xf "$d"; done && rm -f data.tar.* control.tar.* debian-binary )
done
find "$DEST/remill" -maxdepth 4 -type d | head -40
