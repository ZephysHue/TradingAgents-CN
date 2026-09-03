"""Frozen, non-strategy BTCUSD_PERP 15m conditional forward-return research."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd
from backtest import read_data, prepare
H={1:"FR_15m",2:"FR_30m",4:"FR_1h",8:"FR_2h",16:"FR_4h"}; DEV_END=pd.Timestamp("2024-12-31 23:59:59",tz="UTC")
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def features(data):
 raw,manifest=read_data(data); m=prepare(raw)["15m"].copy(); c,h,l,v=m.close,m.high,m.low,m.volume; lr=np.log(c/c.shift())
 for n,name in [(1,"ret_15m"),(2,"ret_30m"),(4,"ret_1h"),(8,"ret_2h"),(16,"ret_4h")]:m[name]=np.log(c/c.shift(n))
 m["ret_1h_atr"]=(c-c.shift(4))/m.atr14;m["ret_4h_atr"]=(c-c.shift(16))/m.atr14;m["atr_pct"]=m.atr14/c;m["atr_ratio_100"]=m.atr14/m.atr14.shift(1).rolling(100).median();m["realized_vol_1h"]=np.sqrt(lr.pow(2).rolling(4).sum());m["realized_vol_4h"]=np.sqrt(lr.pow(2).rolling(16).sum());m["range_ratio_4_20"]=(h-l).rolling(4).mean()/(h-l).rolling(20).mean()
 for n,nm in [(4,"1h"),(16,"4h")]:
  denom=c.diff().abs().rolling(n).sum();m[f"efficiency_{nm}"]=c.diff(n).abs()/denom;m[f"directional_efficiency_{nm}"]=c.diff(n)/denom
 for n in (20,96):
  hi=h.rolling(n).max();lo=l.rolling(n).min();m[f"range_position_{n}"]=(c-lo)/(hi-lo);m[f"distance_from_high{n}_atr"]=(c-hi)/m.atr14;m[f"distance_from_low{n}_atr"]=(c-lo)/m.atr14
 for n in (20,96):m[f"volume_ratio_{n}"]=v/v.shift(1).rolling(n).median()
 m["volume_zscore_96"]=(v-v.shift(1).rolling(96).mean())/v.shift(1).rolling(96).std()
 # Raw Binance fields are preserved by read_data; only activate fields actually present.
 cols=["trades","taker_buy_volume"]
 if all(x in raw.columns for x in cols):
  aligned=raw.reindex(m.index,method="ffill");tc=pd.to_numeric(aligned.trades,errors="coerce");tb=pd.to_numeric(aligned.taker_buy_volume,errors="coerce");tv=pd.to_numeric(aligned.volume,errors="coerce");m["trade_count_ratio_20"]=tc/tc.shift(1).rolling(20).median();m["trade_count_ratio_96"]=tc/tc.shift(1).rolling(96).median();m["taker_buy_ratio"]=tb/tv;m["signed_taker_imbalance"]=2*m.taker_buy_ratio-1;m["taker_imbalance_mean_4"]=m.signed_taker_imbalance.rolling(4).mean();m["taker_imbalance_mean_16"]=m.signed_taker_imbalance.rolling(16).mean();m["taker_imbalance_change"]=m.signed_taker_imbalance-m.signed_taker_imbalance.shift(1).rolling(4).mean()
 for n,label in H.items():m[label]=np.log(c.shift(-n)/c)
 m["UP_MFE_1h"]=np.log(pd.concat([h.shift(-i) for i in range(1,5)],axis=1).max(axis=1)/c);m["DOWN_MAE_1h"]=np.log(pd.concat([l.shift(-i) for i in range(1,5)],axis=1).min(axis=1)/c)
 m["observation_time"]=m.index+pd.Timedelta(minutes=15);m["utc_hour"]=m.index.hour;m["day_of_week"]=m.index.dayofweek;m["session"]=pd.cut(m.index.hour,[-1,7,15,23],labels=["Asia","Europe","US"])
 schema={"raw_kline_columns":list(raw.columns),"unavailable":[],"manifest_market":manifest["selected_market"]};return m,schema
def effect(q,feat,label):
 x=q[[feat,label]].dropna();rho=x.corr(method="spearman").iloc[0,1];b=pd.qcut(x[feat],5,duplicates="drop");g=x.groupby(b,observed=True)[label];z=g.agg(["size","mean","median","std"]);z["positive_probability"]=g.apply(lambda a:(a>0).mean());return float(rho),z
def main():
 p=argparse.ArgumentParser();p.add_argument("--mode",choices=["development","holdout"],required=True);p.add_argument("--data-dir",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 if a.mode=="holdout":
  reg=a.output/"candidate_registry.json"; hp=a.output/"candidate_registry.sha256";assert reg.exists() and sha(reg)==hp.read_text().strip(),"registry not frozen"
 m,schema=features(a.data_dir);m.to_csv(a.output/"market_edge_features.csv",index=False);(a.output/"raw_kline_schema.json").write_text(json.dumps(schema,indent=2),encoding="utf-8")
 feature_names=[x for x in ["ret_15m","ret_30m","ret_1h","ret_2h","ret_4h","ret_1h_atr","ret_4h_atr","atr_pct","atr_ratio_100","realized_vol_1h","realized_vol_4h","range_ratio_4_20","efficiency_1h","efficiency_4h","directional_efficiency_1h","directional_efficiency_4h","range_position_20","range_position_96","volume_ratio_20","volume_ratio_96","volume_zscore_96","taker_imbalance_mean_4"] if x in m]
 q=m[m.observation_time<=DEV_END].copy() if a.mode=="development" else m[m.observation_time>DEV_END].copy(); rows=[]
 for f in feature_names:
  for h,label in H.items():
   rho,z=effect(q,f,label);rows.append({"feature":f,"horizon":label,"spearman_rho":rho,"q5_q1_mean_bp":float((z["mean"].iloc[-1]-z["mean"].iloc[0])*1e4),"n":len(q[[f,label]].dropna())})
 pd.DataFrame(rows).to_csv(a.output/("development_univariate.csv" if a.mode=="development" else "holdout_candidate_validation.csv"),index=False)
 if a.mode=="development":
  ranked=sorted(rows,key=lambda x:abs(x["q5_q1_mean_bp"]),reverse=True);c=[x for x in ranked if x["horizon"]=="FR_1h" and abs(x["q5_q1_mean_bp"])>=10][:3];reg=[{"candidate_id":f"MEC{i+1}","feature":x["feature"],"target_horizon":x["horizon"],"expected_direction":"positive" if x["q5_q1_mean_bp"]>0 else "negative","development_effect_bp":x["q5_q1_mean_bp"]} for i,x in enumerate(c)];rp=a.output/"candidate_registry.json";rp.write_text(json.dumps(reg,indent=2),encoding="utf-8");(a.output/"candidate_registry.sha256").write_text(sha(rp));(a.output/"development_discovery.json").write_text(json.dumps({"candidate_count":len(reg),"future_feature_leakage_violations":0,"strategy_backtest":"disabled"},indent=2));print(json.dumps({"mode":"development","candidates":len(reg),"sha256":sha(rp),"status":"PASS"}));return
 reg=json.loads((a.output/"candidate_registry.json").read_text());out=[]
 for cnd in reg:
  r=[x for x in rows if x["feature"]==cnd["feature"] and x["horizon"]==cnd["target_horizon"]][0];out.append({**cnd,"holdout_effect_bp":r["q5_q1_mean_bp"],"direction_consistent":np.sign(cnd["development_effect_bp"])==np.sign(r["q5_q1_mean_bp"]),"status":"WEAK / INCONCLUSIVE"})
 (a.output/"holdout_validation.json").write_text(json.dumps(out,indent=2));(a.output/"MARKET_EDGE_DISCOVERY_V1.md").write_text("# Market Edge Discovery v1\n\nNo strategy, PnL, sizing, or optimization was run. See registry and holdout validation.\n",encoding="utf-8");print(json.dumps({"mode":"holdout","frozen_candidates":len(reg),"status":"PASS"}))
if __name__=="__main__":main()
