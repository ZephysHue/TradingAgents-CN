#!/usr/bin/env python3
"""Restore a target file from a verified source copy; executable as: python ROLLBACK.sh."""
import argparse
import hashlib
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--target", type=Path, required=True)
parser.add_argument("--source", type=Path, required=True)
args = parser.parse_args()
source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
args.target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(args.source, args.target)
target_hash = hashlib.sha256(args.target.read_bytes()).hexdigest()
if target_hash != source_hash:
    raise SystemExit("ROLLBACK hash mismatch")
print(f"ROLLBACK PASS target={args.target} sha256={target_hash}")
