#!/usr/bin/env bash
#
# install.sh — install (or remove) a-term as a launchable desktop app.
#
# Per-user install by default (no root needed):
#   ./install.sh
#
# System-wide install for all users (needs root):
#   sudo ./install.sh --system
#
# Remove what a previous run installed:
#   ./install.sh --uninstall            (add --system if it was a system install)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/a-term.py"
DESKTOP_SRC="$SCRIPT_DIR/a-term.desktop"

MODE="user"
ACTION="install"

for arg in "$@"; do
    case "$arg" in
        --system)    MODE="system" ;;
        --user)      MODE="user" ;;
        --uninstall) ACTION="uninstall" ;;
        -h|--help)
            sed -n '2,13p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "install.sh: unknown option '$arg' (try --help)" >&2
            exit 2
            ;;
    esac
done

if [ "$MODE" = "system" ]; then
    BIN_DIR="/usr/local/bin"
    APP_DIR="/usr/share/applications"
    if [ "$(id -u)" -ne 0 ]; then
        echo "install.sh: --system needs root; re-run with sudo." >&2
        exit 1
    fi
else
    BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
    APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
fi

BIN_TARGET="$BIN_DIR/a-term"
DESKTOP_TARGET="$APP_DIR/a-term.desktop"

# ---- uninstall --------------------------------------------------------------
if [ "$ACTION" = "uninstall" ]; then
    rm -fv "$BIN_TARGET" "$DESKTOP_TARGET"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APP_DIR" 2>/dev/null || true
    fi
    echo "a-term removed."
    exit 0
fi

# ---- install ----------------------------------------------------------------
if [ ! -f "$SOURCE" ]; then
    echo "install.sh: cannot find a-term.py next to this script." >&2
    exit 1
fi

# Dependency checks (warn, don't block — the app itself errors clearly too).
if command -v python3 >/dev/null 2>&1; then
    if ! python3 -c 'import serial' >/dev/null 2>&1; then
        echo "warning: python module 'pyserial' not found."
        echo "         install it with: pip install --user pyserial"
    fi
    if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
        echo "warning: python 'tkinter' not found (needed for the GUI)."
        echo "         Debian/Ubuntu: sudo apt install python3-tk"
        echo "         Fedora:        sudo dnf install python3-tkinter"
    fi
else
    echo "warning: python3 not found on PATH."
fi

mkdir -p "$BIN_DIR" "$APP_DIR"

install -m 0755 "$SOURCE" "$BIN_TARGET"
echo "installed $BIN_TARGET"

# Write the desktop file with an absolute Exec so it works regardless of PATH.
sed "s|^Exec=.*|Exec=$BIN_TARGET --gui|" "$DESKTOP_SRC" > "$DESKTOP_TARGET"
chmod 0644 "$DESKTOP_TARGET"
echo "installed $DESKTOP_TARGET"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

# Nudge if the per-user bin dir isn't on PATH (only matters for CLI use).
if [ "$MODE" = "user" ] && ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
    echo
    echo "note: $BIN_DIR is not on your PATH."
    echo "      Add this to your shell profile to run 'a-term' from a terminal:"
    echo "        export PATH=\"$BIN_DIR:\$PATH\""
    echo "      (The desktop launcher works either way.)"
fi

echo
echo "Done. Launch a-term from your application menu, or run: a-term --gui"
echo "You also need read/write on the serial device (usually the 'dialout' group)."
