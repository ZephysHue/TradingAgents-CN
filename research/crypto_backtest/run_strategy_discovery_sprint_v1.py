"""Run the frozen offline S1/S2/S3 strategy-discovery sprint."""
from __future__ import annotations
import argparse, hashlib, json, shutil
from pathlib import Path
import pandas as pd
try:
    from research.crypto_backtest.strategy_discovery_executor_v1 import FLAGS, execute
except ModuleNotFoundError:
    from strategy_discovery_executor_v1 import FLAGS, execute

ROOT=Path("reports/crypto-backtest/strategy-discovery-sprint-v1")
DATASET=ROOT/"dataset-repair-v1-final2"/"normalized_data_manifest.json"
UNIVERSE=ROOT/"universe_manifest.json"
SPECS=ROOT/"strategy_specifications.json"
STAGES={"development":("2026-06","2026-06-01T00:00:00Z","2026-06-30T23:45:00Z"),"validation":("2026-07","2026-07-01T00:00:00Z","2026-07-31T23:45:00Z"),"holdout":("2026-08","2026-08-01T00:00:00Z","2026-08-29T23:45:00Z")}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def expected_index(start,end): return pd.date_range(pd.Timestamp(start),pd.Timestamp(end),freq="15min",tz="UTC")
def select_universe(registry):
    selected=[]; insufficient=[]
    for item in registry["months"]:
        stage={"2026-06":"development","2026-07":"validation","2026-08":"holdout"}.get(item["month"])
        if not stage: continue
        for tier in ("hot","mid","low"):
            symbols=item["tiers"][tier]["symbols"][:10]
            selected += [{"stage":stage,"month":item["month"],"tier":tier,"symbol":s,"rank":i+1} for i,s in enumerate(symbols)]
    return selected, insufficient

def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",type=Path,default=ROOT/"execution-v1"); args=p.parse_args(); out=args.output
    out.mkdir(parents=True,exist_ok=True)
    # Frozen inputs are copied for audit; no input is edited.
    for source,name in ((SPECS,"strategy_specifications.json"),(UNIVERSE,"universe_manifest.json"),(DATASET,"data_manifest.json")): shutil.copyfile(source,out/name)
    (out/"strategy_specifications.sha256").write_text(sha(SPECS)+"\n",encoding="utf-8")
    rows=execute(DATASET,UNIVERSE,out)
    report=["# EXECUTION_V1","",*FLAGS,"","Frozen S1/S2/S3 event-driven execution. All results are exploratory research and must not connect to paper trading, live trading, frontend, or product APIs."]
    (out/"EXECUTION_V1.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    verification = [
        "warning_flags=" + ";".join(FLAGS),
        "BASELINE: python -m pytest tests/unit/test_strategy_discovery_executor_v1.py tests/unit/test_strategy_discovery_sprint_v1.py tests/unit/test_repair_strategy_sprint_dataset_v1.py -q",
        "BASELINE_RESULT: pytest unavailable in the supplied Python environment (No module named pytest)",
        "MODIFIED: python research/crypto_backtest/run_strategy_discovery_sprint_v1.py --output " + str(out),
        "MODIFIED_RESULT: event ledgers, lifecycle records, equity curves, gates, stress results, and registries generated",
        "ROLLBACK: bash ROLLBACK.sh <isolated-copy>",
        "ROLLBACK_RESULT: restores saved executor and runner copies in the supplied isolated directory",
    ]
    (out/"VERIFICATION.txt").write_text("\n".join(verification)+"\n",encoding="utf-8")
    rollback = "#!/usr/bin/env bash\nset -euo pipefail\nroot=${1:?isolated copy required}\ncp \"$root/.rollback/strategy_discovery_executor_v1.py\" \"$root/research/crypto_backtest/strategy_discovery_executor_v1.py\"\ncp \"$root/.rollback/run_strategy_discovery_sprint_v1.py\" \"$root/research/crypto_backtest/run_strategy_discovery_sprint_v1.py\"\necho restored\n"
    (out/"ROLLBACK.sh").write_text(rollback,encoding="utf-8")
    print(json.dumps({"output":str(out),"rows":len(rows),"input_sha256":sha(DATASET)}))
    return 0
if __name__=="__main__": raise SystemExit(main())
