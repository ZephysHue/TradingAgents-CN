#!/usr/bin/env bash
set -euo pipefail
target="${1:?pass a copy of the workspace as the only argument}"
test -d "$target/.git"
rm -rf "$target/reports/crypto-backtest/multitier-mechanism-discovery-v1"
rm -rf "$target/research/crypto_backtest/data/multitier-mechanism-v1"
rm -f "$target/research/crypto_backtest/census_multitier_universe_v1.py"
rm -f "$target/tests/unit/test_multitier_census_v1.py"
echo "Census rollback complete for $target"
