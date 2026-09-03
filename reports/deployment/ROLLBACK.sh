#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${1:?source repository path required}"
TARGET_ROOT="${2:?target copy path required}"

for file in docker-compose.yml scripts/mongo-init.js scripts/create_default_admin.py; do
  mkdir -p "$(dirname "$TARGET_ROOT/$file")"
  git -C "$SOURCE_ROOT" show "HEAD:$file" > "$TARGET_ROOT/$file"
done
printf 'rollback restored baseline files in %s\n' "$TARGET_ROOT"
