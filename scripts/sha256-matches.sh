#!/usr/bin/env bash
# Usage: sha256-matches.sh <file> <expected-hex>. Exits 0 when the file's SHA-256 is the expected one.
# Compares the printed digest rather than using -c: macOS's BSD sha256sum has no -c.
set -euo pipefail

if command -v shasum >/dev/null; then
  actual=$(shasum -a 256 "$1" | cut -d' ' -f1)
else
  actual=$(sha256sum "$1" | cut -d' ' -f1)
fi
[[ "$actual" == "$2" ]]
