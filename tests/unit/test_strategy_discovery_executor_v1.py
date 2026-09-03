import pandas as pd
from research.crypto_backtest.strategy_discovery_executor_v1 import _fill, metrics, passes, classify, run_s3

def test_directional_fill_and_fees_are_literal():
    assert _fill(100,"long",True)==100.02
    assert _fill(100,"long",False)==99.98
    assert _fill(100,"short",True)==99.98
    assert _fill(100,"short",False)==100.02

def test_gate_chain_and_high_win_negative_expectancy():
    m={"trade_count":150,"win_rate":51,"net_expectancy_bps":1,"profit_factor":1.06}
    assert passes(m,"development")
    assert classify({"trade_count":301,"win_rate":60,"net_expectancy_bps":-1,"profit_factor":2,"max_drawdown_pct":-1}) == "high_win_rate_negative_expectancy"

def test_s3_tie_break_and_four_hour_hold():
    idx=pd.date_range("2026-06-01",periods=31*4,freq="15min",tz="UTC")
    def f(mult): return pd.DataFrame({"timestamp":idx,"open":100*mult,"high":100,"low":100,"close":100*mult,"volume":1})
    frames={s:f(1) for s in ["AAA","BBB","CCC","DDD","EEE"]}
    members=[{"tier":"hot","symbol":s} for s in frames]
    trades, life=run_s3(frames,members,"development")
    assert trades and {x["side"] for x in trades} == {"long","short"}
    assert all(x["holding_bars"]==4 for x in trades)
    assert any(x["event"]=="filled_entry" for x in life)
