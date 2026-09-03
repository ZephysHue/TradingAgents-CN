"""Deterministic market regime snapshot; LLM prose cannot alter this output."""
from __future__ import annotations

import pandas as pd


def classify_regime(close: pd.Series, *, trend_threshold: float = 0.01, volatility_threshold: float = 0.03) -> dict:
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) < 25:
        return {"deterministic_label": "unknown", "confidence": 0.0, "features": {}}
    returns = close.pct_change().dropna()
    trend = float(close.iloc[-1] / close.iloc[-25] - 1)
    volatility = float(returns.tail(24).std(ddof=0))
    if volatility >= volatility_threshold:
        label = "high_volatility"
    elif trend >= trend_threshold:
        label = "trend_up"
    elif trend <= -trend_threshold:
        label = "trend_down"
    else:
        label = "range"
    confidence = min(1.0, max(abs(trend) / max(trend_threshold, 1e-12), volatility / max(volatility_threshold, 1e-12)))
    return {"schema_version": "market-context/v1", "deterministic_label": label, "confidence": confidence, "features": {"trend_strength": trend, "realized_volatility": volatility}}
