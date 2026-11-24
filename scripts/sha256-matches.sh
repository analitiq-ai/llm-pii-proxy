#!/usr/bin/env bash
# Usage: sha256-matches.sh <file> <expected-hex>
# Exits 0 on a match, 1 on a mismatch, 2 when the digest can't be computed: a tool failure must
# not read as a tampered file. Compares the printed digest because macOS's BSD sha256sum has no -c.
set -uo pipefail

if command -v shasum >/dev/null; then
  tool=(shasum -a 256)
else
  tool=(sha256sum)
fi
if ! output=$("${tool[@]}" "$1"); then
  echo "${tool[0]} failed on $1" >&2
  exit 2
fi
actual=${output%% *}
if [[ ! "$actual" =~ ^[0-9a-f]{64}$ ]]; then
  echo "${tool[0]} printed no digest for $1" >&2
  exit 2
fi
[[ "$actual" == "$2" ]]
