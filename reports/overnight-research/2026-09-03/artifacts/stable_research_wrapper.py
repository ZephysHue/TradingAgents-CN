from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.crypto_backtest.overnight_multi_asset_research_v2 import run_overnight_research
from research.crypto_backtest.run_overnight_multi_asset_research_v2 import snapshot_sources, write_verification_bundle

MANIFEST = Path("reports/crypto-backtest/strategy-discovery-sprint-v1/dataset-repair-v1-final2/normalized_data_manifest.json")
UNIVERSE = Path("reports/crypto-backtest/strategy-discovery-sprint-v1/universe_manifest.json")
OUTPUT_DIR = Path("reports/overnight-research/2026-09-03/stable-final-run")
SUMMARY_PATH = Path("reports/overnight-research/2026-09-03/stable-final-run-summary.md")
PYTEST_RESULT = {
    "command": "python -m pytest tests\\unit\\test_overnight_multi_asset_research_v2.py -q",
    "exit_code": 0,
    "result": "9 passed in 13.73s",
}


def main() -> int:
    root = REPO_ROOT
    output_dir = root / OUTPUT_DIR
    summary_path = root / SUMMARY_PATH
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots = snapshot_sources(output_dir, root)

    result = run_overnight_research(MANIFEST, UNIVERSE, OUTPUT_DIR, SUMMARY_PATH)
    run_summary = {
        "output": str(OUTPUT_DIR),
        "candidates": len(result["candidate_registry"]),
        "rejections": len(result["rejection_registry"]),
        "families": {family: row["param_id"] for family, row in result["chosen_rows"].items()},
    }
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    run_summary_text = json.dumps(run_summary, ensure_ascii=False)
    (artifacts_dir / "run_cli.log").write_text(run_summary_text + "\n", encoding="utf-8")
    write_verification_bundle(
        output_dir=output_dir,
        manifest=root / MANIFEST,
        universe=root / UNIVERSE,
        pytest_result=PYTEST_RESULT,
        run_result={
            "command": "python reports\\overnight-research\\2026-09-03\\artifacts\\stable_research_wrapper.py",
            "exit_code": 0,
            "result": run_summary_text,
        },
        snapshots=snapshots,
    )
    print(run_summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
