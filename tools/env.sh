# source tools/env.sh
# Puts the pinned, user-space toolchain on PATH. Nothing here is installed
# system-wide; every binary lives under private/predecessor-tools (a symlink to
# the predecessor's private work tree) or the repository's .venv.
_SA2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_SA2_TOOLS="$_SA2_ROOT/private/predecessor-tools"
export JAVA_HOME="$_SA2_TOOLS/temurin21/jdk-21.0.12.1+1"
export GHIDRA_INSTALL_DIR="$_SA2_TOOLS/ghidra/ghidra_12.1.3_PUBLIC"
export SA2_ZIG="$_SA2_TOOLS/zig/zig-x86_64-linux-0.15.2/zig"
export SA2_EXE_UNPACKED="$_SA2_ROOT/private/exe/sonic2app-unpacked.exe"
export SA2_EXE_SHIPPED="$_SA2_ROOT/private/exe/sonic2app.exe"
export SA2_INSTALL="$_SA2_ROOT/private/install"
export SA2_PROTON_PREFIX="$_SA2_ROOT/private/proton-prefix"
export PATH="$_SA2_ROOT/.venv/bin:$JAVA_HOME/bin:$GHIDRA_INSTALL_DIR/support:$(dirname "$SA2_ZIG"):$PATH"
unset _SA2_ROOT _SA2_TOOLS
