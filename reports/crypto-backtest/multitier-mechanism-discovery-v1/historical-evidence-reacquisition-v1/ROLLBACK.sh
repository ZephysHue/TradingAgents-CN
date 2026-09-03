#!/usr/bin/env bash
set -euo pipefail

target="${1:?usage: ROLLBACK.sh <copied-audit-output-directory>}"
case "$target" in
  *"historical-evidence-reacquisition-v1"*) ;;
  *) echo "target must be a copied historical-evidence-reacquisition-v1 directory" >&2; exit 2 ;;
esac

test -f "$target/MODIFIED_FILE"
test -f "$target/VERIFICATION.txt"
rm -rf -- "$target"
echo "ROLLBACK_OK: removed copied audit output directory"
