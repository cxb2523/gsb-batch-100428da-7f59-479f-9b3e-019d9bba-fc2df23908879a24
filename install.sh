#!/usr/bin/env sh
# Install ProxyRotate so the `proxyrotate` command is available on $PATH.
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

say() { printf '==> %s\n' "$1"; }
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

# --- sanity checks -----------------------------------------------------------

[ -f pyproject.toml ] || die "run this script from the ProxyRotate project root"

command -v python3 >/dev/null 2>&1 || die "python3 not found"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11+ required (found $(python3 --version 2>&1))"

if ! command -v sing-box >/dev/null 2>&1; then
    say "warning: sing-box not found on PATH"
    say "         install it or pass --sing-box / set sing_box in the config"
fi

# --- install -----------------------------------------------------------------

install_with_uv() {
    command -v uv >/dev/null 2>&1 || return 1
    say "installing with: uv tool install . --force"
    uv tool install . --force
}

install_with_pipx() {
    command -v pipx >/dev/null 2>&1 || return 1
    say "installing with: pipx install . --force"
    pipx install . --force
}

install_with_venv() {
    say "installing into ~/.local/proxyrotate-venv"
    python3 -m venv "$HOME/.local/proxyrotate-venv"
    "$HOME/.local/proxyrotate-venv/bin/pip" install --upgrade pip >/dev/null
    "$HOME/.local/proxyrotate-venv/bin/pip" install .
    mkdir -p "$HOME/.local/bin"
    ln -sf "$HOME/.local/proxyrotate-venv/bin/proxyrotate" "$HOME/.local/bin/proxyrotate"
}

if install_with_uv; then :
elif install_with_pipx; then :
else
    install_with_venv
fi

# --- make sure it's reachable ------------------------------------------------

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
        say "note: $HOME/.local/bin is not on your PATH"
        say "      add this to your shell profile:"
        say "        export PATH=\"\$HOME/.local/bin:\$PATH\""
        ;;
esac

# --- verify ------------------------------------------------------------------

BIN="$(command -v proxyrotate 2>/dev/null \
    || echo "$HOME/.local/bin/proxyrotate")"

if [ ! -x "$BIN" ]; then
    die "proxyrotate not found after install; check the messages above"
fi

"$BIN" --version

cat <<EOF

Installed. Usage examples:

  proxyrotate --every 30 opencode
  proxyrotate --help

Proxy list goes in: ~/.config/proxyrotate/proxies.txt
EOF
