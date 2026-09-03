#!/usr/bin/env python3
import argparse, shutil
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--target",type=Path,required=True); a=p.parse_args()
s=Path(__file__).with_name("MODIFIED_FILE.py"); shutil.copy2(s,a.target); print(f"RESTORED {a.target} FROM {s}")
