#!/usr/bin/env bash
#
# anygarden-machine bootstrap installer (#550).
#
# Creates a self-owned venv under ~/.anygarden/machine/, installs
# anygarden-machine from PyPI into it, then runs `anygarden-machine
# bootstrap` to write the install manifest, a launcher shim on PATH, and
# the systemd user unit. This self-owned layout is what makes
# `anygarden-machine update` deterministic — no pip/uv/pipx detection.
#
# Usage:
#   ./install.sh                 # install/upgrade to the latest release
#   ANYGARDEN_MACHINE_VERSION=0.12.0 ./install.sh   # pin a version
#   curl -fsSL <raw-url>/install.sh | bash
#
set -euo pipefail

INSTALL_ROOT="${ANYGARDEN_MACHINE_HOME:-$HOME/.anygarden/machine}"
VENV_DIR="$INSTALL_ROOT/venv"
PACKAGE="anygarden-machine"
VERSION="${ANYGARDEN_MACHINE_VERSION:-}"

log() { printf '  %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; exit 1; }

# 1. Locate a Python 3 interpreter with venv support.
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || err "python3 not found (set \$PYTHON to override)"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || err "python >= 3.10 required"

echo "Installing anygarden-machine into $VENV_DIR"

# 2. Create the owned venv (python -m venv guarantees pip is present, so
#    the recorded "venv-pip" update method always works).
if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "creating venv"
  "$PYTHON" -m venv "$VENV_DIR"
fi

# 3. Install (or upgrade to) the requested version from PyPI.
SPEC="$PACKAGE"
[ -n "$VERSION" ] && SPEC="$PACKAGE==$VERSION"
log "installing $SPEC"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade "$SPEC"

# 4. Record the self-owned layout: manifest + launcher shim + systemd unit.
log "writing manifest, launcher shim, and systemd unit"
"$VENV_DIR/bin/anygarden-machine" bootstrap

cat <<'EOF'

Done. Next steps:
  1. Register this machine:
       anygarden-machine register --server wss://<host>/ws/machine --name <name>
  2. Enable the service:
       systemctl --user daemon-reload
       systemctl --user enable --now anygarden-machine

Update later with:  anygarden-machine update
EOF
