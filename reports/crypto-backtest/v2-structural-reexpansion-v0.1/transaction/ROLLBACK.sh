#!/usr/bin/env python3
"""Restore a test target from the verified V2 artifact."""
import argparse
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--target", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).with_name("MODIFIED_FILE.py")
shutil.copy2(source, args.target)
print(f"RESTORED {args.target} FROM {source}")
