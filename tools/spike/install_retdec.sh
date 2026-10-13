#!/usr/bin/env bash
# Install RetDec v5.0 (official GitHub release tarball) into private/lifters/retdec.
# User-space only; no sudo. Records URL + sha256 in private/lifters/retdec.download.json.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="$ROOT/private/lifters"
URL="https://github.com/avast/retdec/releases/download/v5.0/RetDec-v5.0-Linux-Release.tar.xz"
TARBALL="$DEST/RetDec-v5.0-Linux-Release.tar.xz"
mkdir -p "$DEST"
if [ ! -s "$TARBALL" ]; then
  curl -L --fail --retry 3 -o "$TARBALL" "$URL"
fi
SHA=$(sha256sum "$TARBALL" | cut -d' ' -f1)
printf '{"lifter":"retdec","url":"%s","tarball":"%s","sha256":"%s","bytes":%s}\n' \
  "$URL" "$TARBALL" "$SHA" "$(stat -c %s "$TARBALL")" > "$DEST/retdec.download.json"
mkdir -p "$DEST/retdec"
tar -xJf "$TARBALL" -C "$DEST/retdec"
ls "$DEST/retdec/bin"
