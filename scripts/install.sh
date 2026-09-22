#!/bin/sh
# AdLife installer for Linux and macOS.
#
# Installs the published package with uv's tool facility. The contract:
#   * no privilege escalation (never sudo), no shell startup files edited;
#   * uv is a requirement, not something this script installs for you — when it is
#     missing, the official installation URL is printed and the script exits, so no
#     second remote installer is ever chained from here;
#   * ADLIFE_VERSION selects a package version (default: the latest release);
#   * ADLIFE_INSTALL_DIR is reserved for verified binary-release mode and is unused
#     while installation goes through the package index;
#   * both installers fail clearly when the package has not yet been published.
#
# Usage:  curl -fsSL <installer-url> | sh
#         ADLIFE_VERSION=0.1.0 sh scripts/install.sh

set -eu

PACKAGE="adlife-sim"
VERSION="$(printenv ADLIFE_VERSION 2>/dev/null || true)"

case "$(uname -s)" in
  Linux|Darwin) ;;
  *)
    echo "AdLife supports this installer on Linux and macOS." >&2
    exit 2
    ;;
esac

case "$(uname -m)" in
  x86_64|amd64|arm64|aarch64) ;;
  *)
    echo "Unsupported processor architecture." >&2
    exit 2
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/ and rerun." >&2
  exit 2
fi

SPEC="$PACKAGE"
if [ -n "$VERSION" ]; then
  SPEC="$PACKAGE==$VERSION"
fi

if ! uv tool install --upgrade "$SPEC"; then
  echo "Installation failed. If the package has not been published to a package" >&2
  echo "index yet, install from source instead:" >&2
  echo "  git clone https://github.com/mahanfatehian/adlife-sim.git" >&2
  echo "  cd adlife-sim && uv sync" >&2
  exit 1
fi

command -v adlife >/dev/null 2>&1
adlife --version
adlife doctor --offline

# ---------------------------------------------------------------------------
# Future binary-release mode (dormant). When verified frozen artifacts exist,
# this branch activates behind ADLIFE_INSTALL_DIR and must:
#   * download the selected archive to a mktemp -d directory with a cleanup trap;
#   * verify the archive against the release's SHA256SUMS BEFORE extraction;
#   * extract and atomically replace only the exact adlife executable inside
#     ADLIFE_INSTALL_DIR (write to a temp name, then rename over the target);
#   * never touch anything else in the target directory.
# ---------------------------------------------------------------------------
