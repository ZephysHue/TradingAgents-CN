#!/usr/bin/env bash
set -euo pipefail

target="${1:?usage: ROLLBACK.sh <copied-output-directory>}"
case "$target" in
  *"strategy-discovery-sprint-v1"*) ;;
  *) echo "target must be a copied strategy-discovery-sprint-v1 directory" >&2; exit 2 ;;
esac

test -f "$target/MODIFIED_FILE"
test -f "$target/VERIFICATION.txt"
rm -rf -- "$target"
echo "ROLLBACK_OK: removed copied sprint output directory"
