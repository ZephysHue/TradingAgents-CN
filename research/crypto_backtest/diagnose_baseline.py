"""BASELINE diagnostics; deliberately does not optimize or alter signals."""
from __future__ import annotations
import argparse, json, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from backtest import KLINE_COLUMNS, indicators, read_data, prepare, Params, run_strategy

def stats(x: pd.DataFrame, net="net"):
    if x.empty: return {"trades":0}
    p=x[net]; w=p>0; loss=abs(p[~w].sum())
    eq=x["equity"]; dd=eq/eq.cummax()-1
    return {"trades":len(x),"win_rate":float(w.mean()),"expectancy_r":float(x.pnl_r.mean()),"profit_factor":float(p[w].sum()/loss) if loss else None,"max_drawdown":float(dd.min()),"final_equity":float(eq.iloc[-1]),"net_pnl":float(p.sum())}

def scenario(t, fee, slip):
    x=t.copy(); direction=np.where(x.direction.eq("long"),1,-1)
    raw=np.where(x.exit_reason.eq("SL"),x.sl,x.tp)
    entry_raw=x.entry/(1+slip/10000*direction)
    entry_exec=entry_raw*(1+slip/10000*direction)
    # Entry and exit have opposite adverse directions: long pays more on entry
    # and receives less on exit; short receives less on entry and pays more on exit.
    exit_exec=raw*(1-slip/10000*direction)
    gross=(exit_exec-entry_exec)*x.position_size*direction
    fee_cost=(abs(entry_raw*x.position_size)+abs(exit_exec*x.position_size))*fee
    x["gross_raw_pnl"]=(raw-entry_raw)*x.position_size*direction
    x["slippage_cost"]=(abs(entry_exec-entry_raw)+abs(exit_exec-raw))*x.position_size
    x["fee_cost"]=fee_cost; x["net"]=gross-fee_cost
    x["pnl_r"]=x.net/x.risk_usd
    x["equity"]=1000+x.net.cumsum()
    return x

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--trades",type=Path,default=Path("reports/crypto-backtest/BASELINE_trades.csv")); ap.add_argument("--output",type=Path,default=Path("reports/crypto-backtest/diagnostics")); args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    t=pd.read_csv(args.trades,parse_dates=["entry_time","exit_time"]); t=t.sort_values("entry_time").reset_index(drop=True)
    scenarios={k:scenario(t,f,s) for k,f,s in [("A_0fee_0slip",0,0),("B_004fee_0slip",.0004,0),("C_0fee_2bp",0,2),("D_004fee_2bp",.0004,2)]}
    summary={k:stats(v) for k,v in scenarios.items()}
    detail=scenarios["D_004fee_2bp"]
    detail.to_csv(args.output/"BASELINE_diagnostic_trades.csv",index=False)
    groups={"long_only":detail[detail.direction=="long"],"short_only":detail[detail.direction=="short"]}
    years={str(y):g for y,g in detail.groupby(detail.entry_time.dt.year)}
    grouped={"long_only":stats(groups["long_only"]),"short_only":stats(groups["short_only"]),"by_year":{y:stats(g) for y,g in years.items()}}
    # Trend distance is computed against the stored 4H EMA values; ATR14_4H is not in the old trade log.
    grouped["trend_distance_without_atr"]={"note":"BASELINE CSV lacks ATR14_4H, so requested normalized distance buckets are not fabricated."}
    # Consecutive losses.
    loss=detail.pnl_r<0; runs=loss.ne(loss.shift()).cumsum(); streaks=[]
    for _,g in detail[loss].groupby(runs):
        if len(g)>=4: streaks.append({"start":g.entry_time.iloc[0].isoformat(),"end":g.exit_time.iloc[-1].isoformat(),"count":len(g),"long":int((g.direction=="long").sum()),"short":int((g.direction=="short").sum()),"mean_ema_distance_raw":float((abs(g.h4_ema20-g.h4_ema50)).mean())})
    grouped["loss_streaks_ge4"]=streaks
    # Duration.
    detail["holding_hours"]=(detail.exit_time-detail.entry_time).dt.total_seconds()/3600
    grouped["duration"]={"winning":stats(detail[detail.pnl_r>0]),"losing":stats(detail[detail.pnl_r<=0]),"winning_avg_hours":float(detail.loc[detail.pnl_r>0,"holding_hours"].mean()),"winning_median_hours":float(detail.loc[detail.pnl_r>0,"holding_hours"].median()),"losing_avg_hours":float(detail.loc[detail.pnl_r<=0,"holding_hours"].mean()),"losing_median_hours":float(detail.loc[detail.pnl_r<=0,"holding_hours"].median())}
    # CM contract formula diagnostic. Official exchangeInfo currently reports contractSize=100 USD.
    sample=[]
    for _,r in t.sample(min(5,len(t)),random_state=20260830).iterrows():
        contracts=r.risk_usd/(100*abs(1/r.entry-1/r.sl)*r.sl); inv_pnl=contracts*100*(1/r.entry-1/r.exit)*r.exit*(1 if r.direction=="long" else -1); sample.append({"entry":r.entry,"sl":r.sl,"exit":r.exit,"risk_usd":r.risk_usd,"contracts_for_1R":contracts,"inverse_gross_pnl_usd":inv_pnl,"linear_logged_gross_pnl":r.gross_pnl})
    grouped["coin_m"]={"contract_size_usd":100,"position_sizing":"contracts = equity*risk_pct / (contract_size*abs(1/entry-1/SL)*SL)","inverse_pnl":"contracts*contract_size*(1/entry-1/exit)*exit*direction (USD-equivalent)","samples":sample,"verdict":"COIN-M inverse PnL is converted from BTC settlement to USD at exit price."}
    grouped["funding"]={"status":"unavailable","cost":None,"note":"历史交易记录未包含 Funding，未假设为0。"}
    (args.output/"diagnostic_summary.json").write_text(json.dumps({"cost_scenarios":summary,**grouped},ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"output":str(args.output),"cost_scenarios":summary,"loss_streaks_ge4":len(streaks)},ensure_ascii=False,default=str))
if __name__=="__main__": main()
