# Early Failure Exit Validation

## Frozen protocol

- Fixed 1,858 BASELINE entry signals; no new filter, TP, stop, sizing, fee/slippage, or COIN-M settlement change.
- F1/F2/F3 only inspect completed bar 1/2/4 respectively; a negative 0R close exits at the next 15m open.
- Original SL/TP receive priority before an early-failure check. Scenario A is 0 fee / 0 slippage; D is 0.04% taker fee / 2bp adverse slippage.

## Results

```csv
model,scenario,description,signals,trades,early_failure_exits,original_sl,tp_2r,skipped_min_qty,win_rate,expectancy_price_r,expectancy_all_in_r,profit_factor,net_pnl_btc,max_drawdown,max_losing_streak,longest_underwater_days,avg_win_r,avg_loss_r,median_trade_r
F0,A,Original BASELINE,1858,1858,0,1212,646,0,0.34768568353067814,0.04346386086471876,0.04346386086471876,1.0597262420077656,0.07234547817399753,-0.355712867547442,15,646.6979166666666,2.0011700518369158,1.0,-1.0
F0,D,Original BASELINE,1858,1858,0,1212,646,0,0.3471474703982777,-0.15329446713300476,-0.12605299189956812,0.7813478370022217,-0.08039182798960612,-0.9519941620654144,15,2170.7395833333335,1.5161548551923938,0.9992797531314852,-0.9999999999999952
F1,A,First complete 15m close negative; exit next open,1858,1858,989,497,372,0,0.20129171151776104,0.014973519216832359,0.014973519216832359,1.0263350756238943,0.016462882557947162,-0.29315943736066474,22,1165.375,1.9941553814201265,0.48382298783440214,-0.20974216187129727
F1,D,First complete 15m close negative; exit next open,1858,1855,989,497,372,3,0.19838274932614555,-0.18199540295607008,-0.1504203089188733,0.6647645661159085,-0.08180784759459418,-0.9644622476467186,22,2170.7395833333335,1.5152270213401733,0.5626316186265593,-0.3453279171235473
F2,A,Second complete 15m close negative; exit next open,1858,1858,887,561,410,0,0.22120559741657697,0.008712696914097559,0.008712696914097559,1.0056352251910294,0.0036646556770643258,-0.23766646453108153,21,830.3854166666666,2.0007388322651045,0.5570943118137973,-0.29026453144493003
F2,D,Second complete 15m close negative; exit next open,1858,1853,887,561,410,5,0.21910415542363734,-0.1890012074733852,-0.1579940609579519,0.6675241124375174,-0.08213216239539152,-0.9682842975741139,21,2170.7395833333335,1.495837394517155,0.622026936509364,-0.4092954801722938
F3,A,Fourth complete 15m close negative; exit next open,1858,1858,753,641,464,0,0.24973089343379978,0.029932193964697665,0.029932193964697665,1.0510255458724638,0.04460734401093751,-0.20270763856641238,25,342.0833333333333,1.9989743664586497,0.6254735219873783,-0.3380219941527465
F3,D,Fourth complete 15m close negative; exit next open,1858,1857,753,641,464,1,0.24878836833602586,-0.16787088050393947,-0.1396724950078192,0.7251501668973436,-0.08103128058768763,-0.9563861498093564,25,2170.7395833333335,1.4828627976567068,0.6770282693526298,-0.4479473751308108

```

## Attribution

```csv
model,early_failure_exits,baseline_original_sl,baseline_tp_2r,saved_loss_r,sacrificed_winner_r,actual_early_exit_r_total,net_early_exit_contribution_r,total_delta_a_r,false_early_exit_count,false_early_exit_pct_of_early,false_early_exit_pct_of_all_f0_winners,f0_mfe_r_p10,f0_mfe_r_p25,f0_mfe_r_p50,f0_mfe_r_p75,f0_mfe_r_p90
F1,989,715,274,545.7686843608781,598.703739142651,-220.97696375626458,-52.93505478177292,-52.93505478177292,274,0.2770475227502528,0.4241486068111455,0.03258469831195087,0.15473066798878768,0.6049082174733352,2.038975549231965,2.455215724926456
F2,887,651,236,457.66815913947494,522.2358217597291,-245.09777709186937,-64.56766262025417,-64.56766262025418,236,0.266065388951522,0.3653250773993808,0.037873990378765876,0.17017146856671578,0.5913384337377158,2.0298649575290826,2.364060386743717
F3,753,571,182,381.24498309120736,406.38682019144653,-230.91008965040527,-25.141837100239172,-25.141837100239172,182,0.24169986719787517,0.28173374613003094,0.053565034239421445,0.1897455132749065,0.5837845221862495,1.917201179067019,2.310063266959115

```

## Decision rule

Candidate requires all preregistered criteria: D expectancy +0.02R, D PF higher than F0 by 0.02 and >1, no DD deterioration, both halves improve, majority years improve, result remains positive after removing top ten D-delta trades, false-exit rate <=25% of early exits and <=15% of F0 winners.

### F1: Reject

- d_expectancy_improvement_gt_002=False; d_profit_factor_improves_and_gt_1=False; max_drawdown_not_worse=False; both_halves_improve=False; majority_years_improve=False; not_tail_dependent_after_removing_top10_delta_trades=False; false_early_exit_pct_of_early_lte_25=False; false_early_exit_pct_of_all_f0_winners_lte_15=False

### F2: Reject

- d_expectancy_improvement_gt_002=False; d_profit_factor_improves_and_gt_1=False; max_drawdown_not_worse=False; both_halves_improve=False; majority_years_improve=False; not_tail_dependent_after_removing_top10_delta_trades=False; false_early_exit_pct_of_early_lte_25=False; false_early_exit_pct_of_all_f0_winners_lte_15=False

### F3: Reject

- d_expectancy_improvement_gt_002=False; d_profit_factor_improves_and_gt_1=False; max_drawdown_not_worse=False; both_halves_improve=False; majority_years_improve=False; not_tail_dependent_after_removing_top10_delta_trades=False; false_early_exit_pct_of_early_lte_25=True; false_early_exit_pct_of_all_f0_winners_lte_15=False
