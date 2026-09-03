"""CLI for overnight multi-asset research v2."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.crypto_backtest.overnight_multi_asset_research_v2 import WARNING_FLAGS, run_overnight_research, sha256_file

TARGET_TEST = Path("tests/unit/test_overnight_multi_asset_research_v2.py")
SNAPSHOT_RELATIVE_PATHS = (
    Path("research/crypto_backtest/overnight_multi_asset_research_v2.py"),
    Path("research/crypto_backtest/run_overnight_multi_asset_research_v2.py"),
    Path("tests/unit/test_overnight_multi_asset_research_v2.py"),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def snapshot_sources(output_dir: Path, root: Path) -> list[dict[str, str]]:
    verification_dir = output_dir / "verification_artifacts"
    verification_dir.mkdir(parents=True, exist_ok=True)
    snapshots: list[dict[str, str]] = []
    for relative_path in SNAPSHOT_RELATIVE_PATHS:
        source_path = root / relative_path
        snapshot_path = verification_dir / relative_path
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, snapshot_path)
        snapshots.append(
            {
                "relative_path": relative_path.as_posix(),
                "source_path": str(source_path),
                "snapshot_path": str(snapshot_path),
            }
        )
    return snapshots


def build_command_text(args: list[str]) -> str:
    return subprocess.list2cmdline(args)


def summarize_output(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no_output"


def run_preflight_pytest(root: Path, output_dir: Path) -> dict[str, Any]:
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "pytest", str(TARGET_TEST), "-q"]
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    combined_output = completed.stdout
    if completed.stderr:
        combined_output = combined_output + ("\n" if combined_output and not combined_output.endswith("\n") else "") + completed.stderr
    (artifacts_dir / "pytest.log").write_text(combined_output.rstrip() + "\n", encoding="utf-8")
    return {
        "command": build_command_text(command),
        "exit_code": completed.returncode,
        "result": summarize_output(combined_output),
    }


def build_run_command(manifest: Path, universe: Path, output_dir: Path, summary_path: Path) -> str:
    root = repo_root()
    script_path = Path(__file__).resolve().relative_to(root)
    return build_command_text(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest),
            "--universe",
            str(universe),
            "--output",
            str(output_dir),
            "--summary",
            str(summary_path),
        ]
    )


def write_verification_bundle(
    output_dir: Path,
    manifest: Path,
    universe: Path,
    pytest_result: dict[str, Any],
    run_result: dict[str, Any],
    snapshots: list[dict[str, str]],
) -> None:
    verification_lines = [
        "warning_flags=" + ";".join(WARNING_FLAGS),
        "data_manifest_sha256=" + sha256_file(manifest),
        "universe_manifest_sha256=" + sha256_file(universe),
        "BASELINE_COMMAND: " + str(pytest_result["command"]),
        "BASELINE_EXIT_CODE: " + str(pytest_result["exit_code"]),
        "BASELINE_RESULT: " + str(pytest_result["result"]),
        "MODIFIED_COMMAND: " + str(run_result["command"]),
        "MODIFIED_EXIT_CODE: " + str(run_result["exit_code"]),
        "MODIFIED_RESULT: " + str(run_result["result"]),
        "ROLLBACK_COMMAND: bash ROLLBACK.sh <isolated-copy-root>",
        "ROLLBACK_RESULT: restores verification_artifacts snapshots into the supplied isolated copy root",
        "ARTIFACT_LOGS: artifacts/pytest.log; artifacts/run_cli.log",
        "LIMITS: paper-only;local-model-only;fixed-bybit-15m-cache;1000-usdt;no-private-api;no-live-trading;no-credential-reads;no-auto-promotion",
    ]
    verification_lines.extend(
        "SNAPSHOT: " + snapshot["relative_path"] + " <= " + snapshot["snapshot_path"]
        for snapshot in snapshots
    )
    (output_dir / "VERIFICATION.txt").write_text("\n".join(verification_lines) + "\n", encoding="utf-8")

    rollback_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'root=${1:?isolated copy root required}',
        'script_dir="$(cd "$(dirname "$0")" && pwd)"',
    ]
    for relative_path in SNAPSHOT_RELATIVE_PATHS:
        posix_relative = relative_path.as_posix()
        rollback_lines.append(f'mkdir -p "$root/{relative_path.parent.as_posix()}"')
        rollback_lines.append(
            f'cp "$script_dir/verification_artifacts/{posix_relative}" "$root/{posix_relative}"'
        )
    rollback_lines.append('echo restored')
    (output_dir / "ROLLBACK.sh").write_text("\n".join(rollback_lines) + "\n", encoding="utf-8")


def write_running_summary(summary_path: Path, output_dir: Path, manifest: Path, universe: Path) -> None:
    import pandas as pd

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 夜间多资产量化研究 v2 晨报",
        "",
        f"- 启动时间：{pd.Timestamp.utcnow().tz_convert('Asia/Shanghai')}",
        "- 状态：running",
        f"- 输出目录：`{output_dir}`",
        f"- 数据清单：`{manifest}`",
        f"- 月度宇宙：`{universe}`",
        "- 模式：paper-only / 本地模型 / 固定 Bybit 15m 缓存 / 1000 USDT / 不自动晋级",
        "",
        "## 进度",
        "",
        "1. 已启动统一多资产执行器。",
        "2. 正在完成四策略族 Development/Validation/Holdout 与成本压力计算。",
        "3. 完成后将覆盖写入完整晨报、注册表、权益曲线和验证工件。",
        "",
    ]
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def write_failed_summary(
    summary_path: Path,
    output_dir: Path,
    manifest: Path,
    universe: Path,
    pytest_result: dict[str, Any],
) -> None:
    import pandas as pd

    lines = [
        "# 夜间多资产量化研究 v2 晨报",
        "",
        f"- 启动时间：{pd.Timestamp.utcnow().tz_convert('Asia/Shanghai')}",
        f"- 完成时间：{pd.Timestamp.utcnow().tz_convert('Asia/Shanghai')}",
        "- 状态：pytest_failed",
        f"- 输出目录：`{output_dir}`",
        f"- 数据清单：`{manifest}`",
        f"- 月度宇宙：`{universe}`",
        "- 模式：paper-only / 本地模型 / 固定 Bybit 15m 缓存 / 1000 USDT / 不自动晋级",
        "",
        "## 失败原因",
        "",
        f"1. 定向测试命令失败：`{pytest_result['command']}`。",
        f"2. 退出码：`{pytest_result['exit_code']}`。",
        f"3. 摘要：`{pytest_result['result']}`。",
        "4. 已保留 `VERIFICATION.txt`、`ROLLBACK.sh` 与 `artifacts/pytest.log` 供审计。",
        "",
    ]
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("reports/crypto-backtest/strategy-discovery-sprint-v1/dataset-repair-v1-final2/normalized_data_manifest.json"),
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=Path("reports/crypto-backtest/strategy-discovery-sprint-v1/universe_manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/overnight-research/2026-09-03"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("reports/overnight-research/2026-09-03/morning-summary.md"),
    )
    args = parser.parse_args()
    write_running_summary(args.summary, args.output, args.manifest, args.universe)
    root = repo_root()
    snapshots = snapshot_sources(args.output, root)
    pytest_result = run_preflight_pytest(root, args.output)
    run_command = build_run_command(args.manifest, args.universe, args.output, args.summary)
    if pytest_result["exit_code"] != 0:
        run_result = {
            "command": run_command,
            "exit_code": pytest_result["exit_code"],
            "result": "skipped_due_to_pytest_failure",
        }
        write_verification_bundle(args.output, args.manifest, args.universe, pytest_result, run_result, snapshots)
        write_failed_summary(args.summary, args.output, args.manifest, args.universe, pytest_result)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "status": "pytest_failed",
                    "pytest_exit_code": pytest_result["exit_code"],
                },
                ensure_ascii=False,
            )
        )
        return int(pytest_result["exit_code"])

    result = run_overnight_research(args.manifest, args.universe, args.output, args.summary)
    run_summary = {
        "output": str(args.output),
        "candidates": len(result["candidate_registry"]),
        "rejections": len(result["rejection_registry"]),
        "families": {family: row["param_id"] for family, row in result["chosen_rows"].items()},
    }
    artifacts_dir = args.output / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    run_summary_text = json.dumps(run_summary, ensure_ascii=False)
    (artifacts_dir / "run_cli.log").write_text(run_summary_text + "\n", encoding="utf-8")
    run_result = {
        "command": run_command,
        "exit_code": 0,
        "result": run_summary_text,
    }
    write_verification_bundle(args.output, args.manifest, args.universe, pytest_result, run_result, snapshots)
    print(run_summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
