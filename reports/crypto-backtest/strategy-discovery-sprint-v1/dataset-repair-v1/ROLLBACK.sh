#!/usr/bin/env bash
set -euo pipefail

target="${1:?usage: ROLLBACK.sh <dataset-repair-copy>}"
case "$target" in
  *"dataset-repair-v1"*) ;;
  *) echo "refusing target outside dataset-repair-v1" >&2; exit 2 ;;
esac
rm -rf -- "$target"
echo "removed: $target"
