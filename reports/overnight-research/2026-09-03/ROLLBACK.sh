#!/usr/bin/env bash
set -euo pipefail
root=${1:?isolated copy root required}
script_dir="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$root/research/crypto_backtest"
cp "$script_dir/verification_artifacts/research/crypto_backtest/overnight_multi_asset_research_v2.py" "$root/research/crypto_backtest/overnight_multi_asset_research_v2.py"
mkdir -p "$root/research/crypto_backtest"
cp "$script_dir/verification_artifacts/research/crypto_backtest/run_overnight_multi_asset_research_v2.py" "$root/research/crypto_backtest/run_overnight_multi_asset_research_v2.py"
mkdir -p "$root/tests/unit"
cp "$script_dir/verification_artifacts/tests/unit/test_overnight_multi_asset_research_v2.py" "$root/tests/unit/test_overnight_multi_asset_research_v2.py"
echo restored
