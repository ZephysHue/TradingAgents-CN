# V2 Structural Re-Expansion Baseline v0.1

## Frozen design

- BTCUSD_PERP COIN-M inverse, BTC numeraire, 100 USD face value, integer contracts, 1% risk, single position.
- Confirmed 5-bar swings only; Impulse >=1 ATR, 3-48 bars; Pullback >=0.1 ATR, <75% impulse, <=32 bars; completed-close re-expansion, next-bar entry.
- 4H completed EMA20/EMA50 background only; daily risk controls disabled; funding excluded.

## Result

- Rating: **REJECT**
- Scenario A Price-R expectancy: -0.033383; PF 0.927424
- Scenario D All-in-R expectancy: -0.257397; PF 0.648894
- Scenario D BTC max drawdown: -99.73%

## Audit

```json
{
  "market": "BTCUSD_PERP",
  "data_market_key": "cm",
  "structural_breakouts": 13206,
  "signals": 3501,
  "discarded_open_position": 3029,
  "skipped_wide_stop": 687,
  "skipped_background": 4677,
  "simultaneous_breakouts_discarded": 1312,
  "future_leakage_violations": 0,
  "confirmed_swing_only": true,
  "next_bar_entry": true,
  "raw_trigger_fill_separated": true,
  "single_position": true,
  "same_impulse_single_trade": true,
  "daily_risk_control": "disabled"
}
```

No parameter optimization, filter search, or V2.0.2 variation was run.