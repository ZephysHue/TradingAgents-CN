# V1 Entry-Time Predictive Structure Diagnostic

## Protocol

- Frozen 1,858 Corrected BASELINE entries; labels are E0 TP=WIN and E0 SL=LOSS.
- Features use only data completed before Entry Open. Source timestamps are audited in `feature_source_audit.csv`.
- Development ranking is frozen before the Feature Stability Holdout is read. This is not strict independent OOS because the broader V1 history was previously researched.

## Conclusion

No stable entry-time predictive structure was identified.

## Top development-ranked features and holdout validation

```json
[
  {
    "feature": "atr_ratio_15m",
    "status": "Reject",
    "development_effect": 0.1770848159157074,
    "holdout_effect": 0.023250806556439588,
    "holdout_direction": "WIN > LOSS",
    "redundant_with": [],
    "market_structure_explanation": "Completed ATR relative to its prior 100-bar median.",
    "checks": {
      "development_non_very_small": false,
      "holdout_direction_consistent": true,
      "holdout_effect_retained": false,
      "quantile_relation_continuous_in_both": false,
      "time_not_unstable": true,
      "no_severe_long_short_conflict": true,
      "not_highly_redundant": true
    }
  },
  {
    "feature": "pullback_time_ratio",
    "status": "Reject",
    "development_effect": -0.12898838986602654,
    "holdout_effect": -0.02410178813392241,
    "holdout_direction": "WIN < LOSS",
    "redundant_with": [],
    "market_structure_explanation": "Pullback duration divided by impulse duration.",
    "checks": {
      "development_non_very_small": false,
      "holdout_direction_consistent": true,
      "holdout_effect_retained": false,
      "quantile_relation_continuous_in_both": false,
      "time_not_unstable": true,
      "no_severe_long_short_conflict": true,
      "not_highly_redundant": true
    }
  },
  {
    "feature": "ema_distance_15m",
    "status": "Reject",
    "development_effect": 0.09885241488714913,
    "holdout_effect": 0.05022408910185842,
    "holdout_direction": "WIN > LOSS",
    "redundant_with": [],
    "market_structure_explanation": "Direction-standardized EMA20/EMA50 distance on completed 15m data.",
    "checks": {
      "development_non_very_small": false,
      "holdout_direction_consistent": true,
      "holdout_effect_retained": false,
      "quantile_relation_continuous_in_both": false,
      "time_not_unstable": true,
      "no_severe_long_short_conflict": true,
      "not_highly_redundant": true
    }
  }
]
```