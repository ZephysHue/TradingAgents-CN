#!/usr/bin/env bash
set -euo pipefail
target="${1:?pass a copy of the workspace as the only argument}"
test -d "$target/.git"
echo "This rollback is intentionally copy-only: no source files in the active workspace are changed."
rm -rf "$target/reports/crypto-backtest/bybit-linear-v1-frozen-signal"
rm -rf "$target/research/crypto_backtest/data/bybit-linear-v1"
rm -f "$target/app/services/crypto/bybit_client.py"
rm -f "$target/tests/unit/test_bybit_crypto_data.py"
rm -f "$target/research/crypto_backtest/run_bybit_multisymbol_v1.py"
echo "Rollback complete for $target. Existing pre-Bybit edits to shared crypto routes and UI are preserved."
