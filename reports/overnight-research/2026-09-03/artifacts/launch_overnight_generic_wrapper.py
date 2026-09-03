from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.crypto_backtest.run_overnight_multi_asset_research_v2 import main


if __name__ == "__main__":
    raise SystemExit(main())
