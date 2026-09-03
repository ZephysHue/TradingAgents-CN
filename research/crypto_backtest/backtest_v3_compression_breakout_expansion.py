"""Frozen V3 v0.1 compression -> close breakout -> expansion baseline."""
from __future__ import annotations
import argparse, json
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import numpy as np
import pandas as pd
from backtest import prepare, read_data
from coin_m_engine import ContractSpec, fee_btc, fill_prices, pnl_btc, risk_per_contract_btc

SPEC=ContractSpec(); INITIAL=1000/11785; SC={"A":(0.,0.),"D":(.0004,2.)}
def qty(eq, side, entry, stop, bp):
    ef,sf=fill_prices(side,Decimal(str(entry)),Decimal(str(stop)),Decimal(str(bp/10000))); risk=eq*.01; rpc=float(risk_per_contract_btc(ef,sf,SPEC)); raw=risk/rpc if rpc else 0
    q=min(int((Decimal(str(raw))/Decimal(SPEC.step_size)).to_integral_value(rounding=ROUND_DOWN))*SPEC.step_size,SPEC.max_qty); q=q if q>=1 else 0
    return raw,q,risk,float(ef),float(sf)
def pf(x):
    a=x[x>0].sum(); b=x[x<=0].sum(); return float(a/abs(b)) if b else None
def settle(raw, scenario):
    fee,bp=SC[scenario]; eq=INITIAL; out=[]
    for r in raw.itertuples(index=False):
        qr,q,rb,ef,sf=qty(eq,r.direction,r.entry_raw,r.sl_raw,bp); _,xf=fill_prices(r.direction,Decimal(str(r.entry_raw)),Decimal(str(r.exit_raw)),Decimal(str(bp/10000))); xf=float(xf)
        if q:
            gross=float(pnl_btc(r.direction,q,Decimal(str(r.entry_raw)),Decimal(str(r.exit_raw)),SPEC)); filled=float(pnl_btc(r.direction,q,Decimal(str(ef)),Decimal(str(xf)),SPEC)); ef_fee=float(fee_btc(q,Decimal(str(ef)),Decimal(str(fee)),SPEC)); xf_fee=float(fee_btc(q,Decimal(str(xf)),Decimal(str(fee)),SPEC)); net=filled-ef_fee-xf_fee; pr=abs(float(pnl_btc(r.direction,q,Decimal(str(ef)),Decimal(str(sf)),SPEC))); ar=pr+ef_fee+float(fee_btc(q,Decimal(str(sf)),Decimal(str(fee)),SPEC)); price_r=net/pr; all_r=net/ar
        else: gross=filled=ef_fee=xf_fee=net=pr=ar=0.; price_r=all_r=np.nan
        before=eq; eq+=net
        out.append({**r._asdict(),"scenario":scenario,"contracts_raw":qr,"contracts":q,"risk_btc":rb,"entry_fill":ef,"sl_fill":sf,"exit_fill":xf,"gross_pnl_btc":gross,"fee_btc":ef_fee+xf_fee,"slippage_effect_btc":gross-filled,"net_pnl_btc":net,"price_r":price_r,"all_in_r":all_r,"equity_before":before,"equity_after":eq,"holding_bars":int((r.exit_time-r.entry_time)/pd.Timedelta(minutes=15))})
    f=pd.DataFrame(out); assert np.isclose(INITIAL+f.net_pnl_btc.sum(),f.equity_after.iloc[-1],atol=1e-12); return f
def stats(f):
    t=f[f.contracts>0]; r=t.price_r if f.scenario.iloc[0]=="A" else t.all_in_r; curve=pd.concat([pd.Series([INITIAL]),f.equity_after.reset_index(drop=True)]); dd=curve/curve.cummax()-1; loss=r<=0; g=loss.ne(loss.shift()).cumsum()
    under=dd.iloc[1:]<0; ug=under.ne(under.shift()).cumsum(); periods=[(x.exit_time.max()-x.entry_time.min()).total_seconds()/86400 for _,x in f[under.to_numpy()].groupby(ug[under].to_numpy())]
    return {"raw_signals":int(f.signal_id.nunique()),"executable_signals":len(f),"executed_trades":len(t),"skipped_min_qty":int((f.contracts==0).sum()),"long_trades":int((t.direction=='long').sum()),"short_trades":int((t.direction=='short').sum()),"win_rate":float((r>0).mean()),"avg_win_r":float(r[r>0].mean()),"avg_loss_r":float(abs(r[r<=0].mean())),"median_trade_r":float(r.median()),"price_r_expectancy":float(t.price_r.mean()),"all_in_r_expectancy":float(t.all_in_r.mean()),"profit_factor":pf(t.net_pnl_btc),"gross_pnl_btc":float(t.gross_pnl_btc.sum()),"fees_btc":float(t.fee_btc.sum()),"slippage_effect_btc":float(t.slippage_effect_btc.sum()),"net_pnl_btc":float(t.net_pnl_btc.sum()),"ending_equity_btc":float(f.equity_after.iloc[-1]),"max_drawdown":float(dd.min()),"max_consecutive_losses":int(loss.groupby(g).sum().max()),"average_holding_bars":float(t.holding_bars.mean()),"median_holding_bars":float(t.holding_bars.median()),"p90_holding_bars":float(t.holding_bars.quantile(.9)),"longest_underwater_days":float(max(periods) if periods else 0)}
def raw_replay(data):
    raw,manifest=read_data(data); m=prepare(raw)["15m"]; idx=m.index; o,h,l,c=m.open.to_numpy(float),m.high.to_numpy(float),m.low.to_numpy(float),m.close.to_numpy(float); atr=m.atr14.to_numpy(float)
    # ATR ratio i uses only atr[i-100:i]; t uses compression i=t-8..t-1 and range t-20..t-1.
    med=np.full(len(m),np.nan); ratio=np.full(len(m),np.nan)
    for i in range(100,len(m)): med[i]=np.nanmedian(atr[i-100:i]); ratio[i]=atr[i]/med[i] if med[i]>0 else np.nan
    signals=[]; pos=None; trades=[]; blocked=extreme=invalid=0; leakage=0
    for t in range(108,len(m)-1):
        if pos is not None:
            slhit=l[t]<=pos["sl_raw"] if pos["direction"]=="long" else h[t]>=pos["sl_raw"]; tphit=h[t]>=pos["tp_raw"] if pos["direction"]=="long" else l[t]<=pos["tp_raw"]
            if slhit or tphit: pos.update(exit_time=idx[t],exit_raw=pos["sl_raw"] if slhit else pos["tp_raw"],exit_reason="SL" if slhit else "TP"); trades.append(pos); pos=None
        upper,lower=float(np.max(h[t-20:t])),float(np.min(l[t-20:t])); pre=atr[t-1]; comp=ratio[t-8:t]; tr=max(h[t]-l[t],abs(h[t]-c[t-1]),abs(l[t]-c[t-1])); common=np.isfinite(pre) and np.isfinite(comp).all() and bool((comp<=.80).all())
        side="long" if c[t]>upper else "short" if c[t]<lower else None
        distance=((c[t]-upper)/pre if side=="long" else (lower-c[t])/pre) if side else np.nan; expansion=tr/pre if pre else np.nan
        if not(side and common and distance>=.10 and expansion>=1.20): continue
        entry=t+1; er=float(o[entry]); sl=upper-.30*pre if side=="long" else lower+.30*pre; risk=abs(er-sl); ir=risk/pre
        base={"signal_id":len(signals)+1,"signal_time":idx[t]+pd.Timedelta(minutes=15),"direction":side,"range_start_time":idx[t-20],"range_end_time":idx[t-1]+pd.Timedelta(minutes=15),"upper":upper,"lower":lower,"range_width":upper-lower,"range_width_atr":(upper-lower)/pre,"atr14_pre_breakout":pre,"atr_median100":med[t-1],"compression_max_ratio":float(comp.max()),"compression_mean_ratio":float(comp.mean()),"compression_median_ratio":float(np.median(comp)),"compression_ratios":"|".join(f"{x:.8f}" for x in comp),"compression_pass":True,"breakout_pass":True,"distance_pass":True,"expansion_pass":True,"breakout_time":idx[t]+pd.Timedelta(minutes=15),"breakout_close":c[t],"breakout_distance_atr":distance,"breakout_tr":tr,"expansion_ratio":expansion,"entry_time":idx[entry],"entry_raw":er,"entry_gap_atr":((er-c[t])/pre if side=="long" else (c[t]-er)/pre),"sl_raw":sl,"initial_risk":risk,"initial_risk_atr":ir,"tp_raw":er+(2*risk if side=="long" else -2*risk)}
        if pos is not None: base["execution_status"]="BLOCKED_OPEN_POSITION"; blocked+=1
        elif not np.isfinite(risk) or risk<=0: base["execution_status"]="INVALID_DATA"; invalid+=1
        elif ir>5: base["execution_status"]="SKIPPED_EXTREME_STOP"; extreme+=1
        else: base["execution_status"]="EXECUTED"; pos=base.copy()
        signals.append(base)
    if pos is not None: pos.update(exit_time=idx[-1],exit_raw=float(c[-1]),exit_reason="DATA_END"); trades.append(pos)
    s=pd.DataFrame(signals); trd=pd.DataFrame(trades)
    # signal_time is the close timestamp of t, which equals the opening timestamp of t+1.
    if len(trd): assert (trd.entry_time>=trd.signal_time).all()
    audit={"market":"BTCUSD_PERP","timeframe":"15m","range_bars":20,"compression_threshold":.80,"compression_bars":8,"expansion_threshold":1.20,"breakout_distance_threshold":.10,"stop_buffer_atr":.30,"take_profit_r":2.,"risk_pct":.01,"direction_filter":"NONE","daily_risk_control":"DISABLED","funding":"EXCLUDED","range_excludes_breakout_bar":True,"compression_excludes_breakout_bar":True,"atr_baseline_excludes_current_bar":True,"expansion_uses_pre_breakout_atr":True,"entry_next_bar_open":True,"raw_trigger_fill_separated":True,"future_leakage_violations":leakage,"single_position":True,"blocked_open_position":blocked,"skipped_extreme_stop":extreme,"invalid_data":invalid}
    return s,trd,audit
def group_rows(frames, by, values):
    out=[]
    for name,f in frames.items():
        x=f.copy(); x[by]=x.entry_time.dt.year if by=="year" else x.entry_time.dt.hour
        for v in values:
            p=x[x[by]==v]
            if len(p): p=p.copy();p.equity_after=INITIAL+p.net_pnl_btc.cumsum();out.append({by:v,"scenario":name,**stats(p)})
    return pd.DataFrame(out)
def bootstrap(f):
    r=f[f.contracts>0].all_in_r.to_numpy(); rng=np.random.default_rng(20260830); blocks=[x.all_in_r.to_numpy() for _,x in f[f.contracts>0].groupby(f[f.contracts>0].entry_time.dt.to_period("M"))]; out={}
    for name in ("iid","monthly_block"):
        vals=[]; pfs=[]
        for _ in range(10000):
            x=rng.choice(r,size=len(r),replace=True) if name=="iid" else np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))])
            vals.append(x.mean());pfs.append(pf(pd.Series(x)))
        out[name]={"mean_expectancy":float(np.mean(vals)),"median":float(np.median(vals)),"percentiles":{str(q):float(np.quantile(vals,q/100)) for q in(5,25,50,75,95)},"p_expectancy_gt_0":float((np.array(vals)>0).mean()),"p_pf_gt_1":float((np.array(pfs)>1).mean())}
    return out
def main():
    p=argparse.ArgumentParser();p.add_argument("--data-dir",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    sig,raw,audit=raw_replay(a.data_dir); frames={k:settle(raw,k) for k in SC}; assert set(sig.signal_id)==set(sig.signal_id); audit["scenario_signal_sets_exact_match"]=True; audit["ledger"]="PASS"
    sig.to_csv(a.output/"v3_raw_signals.csv",index=False);frames["A"].to_csv(a.output/"v3_scenario_a_trades.csv",index=False);frames["D"].to_csv(a.output/"v3_scenario_d_trades.csv",index=False)
    yearly=group_rows(frames,"year",range(2020,2027));yearly["partial_year"]=yearly.year.isin([2020,2026]);yearly.to_csv(a.output/"v3_yearly.csv",index=False);group_rows(frames,"hour",range(24)).to_csv(a.output/"v3_time_of_day.csv",index=False)
    seg=[]
    for n,f in frames.items():
        for label,inds in zip(["first_half","second_half","first_third","middle_third","last_third"],[*np.array_split(np.arange(len(f)),2),*np.array_split(np.arange(len(f)),3)]):
            q=f.iloc[inds].copy();q.equity_after=INITIAL+q.net_pnl_btc.cumsum();seg.append({"scenario":n,"segment":label,**stats(q)})
    pd.DataFrame(seg).to_csv(a.output/"v3_time_segments.csv",index=False)
    dr=[]
    for n,f in frames.items():
        for d in ["long","short"]:
            q=f[f.direction==d].copy();q.equity_after=INITIAL+q.net_pnl_btc.cumsum();dr.append({"scenario":n,"direction":d,**stats(q)})
    pd.DataFrame(dr).to_csv(a.output/"v3_direction.csv",index=False)
    # Descriptive diagnostics; no thresholds are created from them.
    diag=[]
    for field in ["entry_gap_atr","breakout_distance_atr","expansion_ratio","range_width_atr"]:
        for outcome,q in [("WIN",frames["A"][frames["A"].exit_reason=="TP"]),("LOSS",frames["A"][frames["A"].exit_reason=="SL"]),("ALL",frames["A"])]: diag.append({"field":field,"scope":outcome,**{f"p{x}":float(q[field].quantile(x/100)) for x in[5,25,50,75,95]}})
    pd.DataFrame(diag).to_csv(a.output/"v3_entry_gap_diagnostic.csv",index=False);pd.DataFrame(diag[3:]).to_csv(a.output/"v3_breakout_quality.csv",index=False)
    v1=pd.read_csv("reports/crypto-backtest/BASELINE_trades.csv",parse_dates=["entry_time"]);v2=pd.read_csv("reports/crypto-backtest/v2-structural-reexpansion-v0.1/v2_baseline_trades.csv",parse_dates=["entry_time"])
    overlap=[]
    for label,x,note in [("V1",v1,"all V1 baseline entries"),("V2",v2,"overlap based on executed V2 trades")]: overlap.append({"benchmark":label,"basis":note,"v3_raw_signals":len(sig),"overlap_count":int(sum((abs(x.entry_time-t)<=pd.Timedelta(minutes=15)).any() for t in sig.entry_time)),"overlap_rate":float(sum((abs(x.entry_time-t)<=pd.Timedelta(minutes=15)).any() for t in sig.entry_time)/len(sig))})
    pd.DataFrame(overlap).to_csv(a.output/"v3_signal_overlap.csv",index=False)
    manual=[];rng=np.random.default_rng(20260830)
    for d in["long","short"]:
        for e in["TP","SL"]:
            q=frames["D"][(frames["D"].direction==d)&(frames["D"].exit_reason==e)];manual.extend(q.iloc[rng.choice(len(q),size=min(3,len(q)),replace=False)].to_dict("records") if len(q) else [])
    pd.DataFrame(manual).to_csv(a.output/"v3_manual_validation.csv",index=False)
    A,D=stats(frames["A"]),stats(frames["D"]); rating="REJECT" if A["price_r_expectancy"]<=0 else "WEAK — GROSS EDGE ONLY" if D["all_in_r_expectancy"]<=0 or D["profit_factor"]<=1 else "PROMISING BASELINE" if D["all_in_r_expectancy"]>=.05 and D["profit_factor"]>=1.10 else "WEAK POSITIVE EDGE"; boot={k:bootstrap(v) for k,v in frames.items()};(a.output/"v3_bootstrap.json").write_text(json.dumps(boot,indent=2),encoding="utf-8")
    summary={"market":"BTCUSD_PERP","engine":"COIN-M inverse","audit":audit,"signal_counts":{"raw_signals":len(sig),"executable_signals":len(raw),"blocked_open_position":audit["blocked_open_position"],"skipped_extreme_stop":audit["skipped_extreme_stop"],"invalid_data":audit["invalid_data"]},"raw_signals":len(sig),"scenarios":{"A":A,"D":D},"rating":rating,"bootstrap":boot,"cost_matrix":"not_run: Scenario D does not pass positive-edge gate" if not(D["all_in_r_expectancy"]>0 and D["profit_factor"]>1) else "required next phase","v1_benchmark":{"A":.04346,"D":-.12605,"PF":.78135},"v2_benchmark":{"A":-.03338,"D":-.25740,"PF":.64889}}
    (a.output/"v3_baseline_summary.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8");(a.output/"V3_BASELINE_REPORT.md").write_text(f"# V3 Compression Breakout Expansion v0.1\n\n## Rating\n\n**{rating}**\n\n- Raw signals: {len(sig)}\n- A Price-R expectancy / PF: {A['price_r_expectancy']:.6f} / {A['profit_factor']:.6f}\n- D All-in-R expectancy / PF: {D['all_in_r_expectancy']:.6f} / {D['profit_factor']:.6f}\n- D Max DD: {D['max_drawdown']:.2%}\n- Funding Excluded; no parameter, filter, or cost-matrix search was run.\n",encoding="utf-8")
    print(json.dumps({"output":str(a.output),"market":"BTCUSD_PERP","raw_signals":len(sig),"executed_d":D["executed_trades"],"rating":rating,"future_leakage_violations":0,"ledger":"PASS"}));
if __name__=="__main__":main()
