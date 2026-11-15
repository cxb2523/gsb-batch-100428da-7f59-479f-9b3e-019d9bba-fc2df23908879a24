#!/usr/bin/env sh
# Uninstall ProxyRotate, reversing everything install.sh does.
set -eu

say() { printf '==> %s\n' "$1"; }
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

# --- remove uv-installed tool -------------------------------------------------

if command -v uv >/dev/null 2>&1; then
    if uv tool list 2>/dev/null | grep -q '^proxyrotate'; then
        say "removing uv tool: uv tool uninstall proxyrotate"
        uv tool uninstall proxyrotate
    fi
fi

# --- remove pipx-installed tool -----------------------------------------------

if command -v pipx >/dev/null 2>&1; then
    if pipx list --short 2>/dev/null | grep -q '^proxyrotate'; then
        say "removing pipx tool: pipx uninstall proxyrotate"
        pipx uninstall proxyrotate || true
    fi
fi

# --- remove venv + symlink fallback -------------------------------------------

VENV="$HOME/.local/proxyrotate-venv"
if [ -d "$VENV" ]; then
    say "removing venv: $VENV"
    rm -rf "$VENV"
fi

BIN="$HOME/.local/bin/proxyrotate"
if [ -L "$BIN" ] || [ -f "$BIN" ]; then
    say "removing symlink/binary: $BIN"
    rm -f "$BIN"
fi

# --- report -------------------------------------------------------------------

if command -v proxyrotate >/dev/null 2>&1; then
    die "proxyrotate still found on PATH after uninstall; remove it manually"
fi

say "uninstalled. note: config at ~/.config/proxyrotate/ was not removed"
