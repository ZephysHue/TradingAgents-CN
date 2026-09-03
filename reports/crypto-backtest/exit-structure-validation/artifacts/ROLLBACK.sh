#!/usr/bin/env python3
"""Restore a test target from the preserved source and verify SHA256."""
import argparse
import hashlib
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--target", type=Path, required=True)
parser.add_argument("--source", type=Path, required=True)
args = parser.parse_args()
expected = hashlib.sha256(args.source.read_bytes()).hexdigest()
args.target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(args.source, args.target)
actual = hashlib.sha256(args.target.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit("ROLLBACK hash mismatch")
print(f"ROLLBACK PASS target={args.target} sha256={actual}")
