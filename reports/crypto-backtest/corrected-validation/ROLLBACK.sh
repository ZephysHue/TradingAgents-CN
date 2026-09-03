#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -f "$ROOT/corrected_validation.json.snapshot" ]; then
  cp "$ROOT/corrected_validation.json.snapshot" "$ROOT/corrected_validation.json"
fi
printf '%s\n' 'Generated validation report restored from snapshot; active corrected baseline was not reverted.'
