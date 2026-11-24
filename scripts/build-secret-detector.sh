#!/usr/bin/env bash
# Builds .tools/gitleaks-sidecar with a pinned, checksum-verified Go toolchain (research R7).
# Go isn't installable through uv, so the toolchain lives in .tools/ (gitignored) instead of on the system.
set -euo pipefail

GO_VERSION=1.27.4
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/.tools"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)        PLATFORM=linux-amd64;  SHA256=7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840 ;;
  Linux-aarch64)       PLATFORM=linux-arm64;  SHA256=3392582988a273a87ab5043ee1b2c9c17a2c3c00580bf9326c47a2e0aaaeac92 ;;
  Darwin-x86_64)       PLATFORM=darwin-amd64; SHA256=2b799377704ef12db39bd254995d5c348c9cc8d86562ca817724390e56c38f43 ;;
  Darwin-arm64)        PLATFORM=darwin-arm64; SHA256=91a44a2b8a716600ad94a4f143e156116620db8828086a6e3602291be9bd0bb4 ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

ARCHIVE="$TOOLS/go$GO_VERSION.$PLATFORM.tar.gz"
mkdir -p "$TOOLS"

# Returns only match (0) or mismatch (1). A digest that can't be computed stops the build instead of
# being reported as a mismatch, which is how #6 was misread.
verify() {
  local status=0
  "$ROOT/scripts/sha256-matches.sh" "$ARCHIVE" "$SHA256" || status=$?
  if ((status > 1)); then
    echo "checksum tool failed on $ARCHIVE" >&2
    exit 1
  fi
  return "$status"
}

if [[ ! -f "$ARCHIVE" ]] || ! verify; then
  curl -fsSL -o "$ARCHIVE.part" "https://go.dev/dl/go$GO_VERSION.$PLATFORM.tar.gz"
  mv "$ARCHIVE.part" "$ARCHIVE"
  verify || { echo "checksum mismatch for $ARCHIVE" >&2; rm -f "$ARCHIVE"; exit 1; }
  rm -rf "$TOOLS/go"
fi

if [[ ! -x "$TOOLS/go/bin/go" ]]; then
  tar -C "$TOOLS" -xzf "$ARCHIVE"
fi

cd "$ROOT/sidecar/gitleaks"
env GOTOOLCHAIN=local GOFLAGS=-mod=readonly \
  GOPATH="$TOOLS/gopath" GOCACHE="$TOOLS/gocache" GOMODCACHE="$TOOLS/gopath/pkg/mod" \
  "$TOOLS/go/bin/go" build -trimpath -o "$TOOLS/gitleaks-sidecar" .
echo "built $TOOLS/gitleaks-sidecar"
