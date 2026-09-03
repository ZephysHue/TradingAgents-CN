"""Auditable BTC-base settlement engine for Binance BTCUSD_PERP."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


@dataclass(frozen=True)
class ContractSpec:
    contract_size_usd: Decimal = Decimal("100")
    min_qty: int = 1
    max_qty: int = 60000
    step_size: int = 1


def round_quantity(raw: Decimal, spec: ContractSpec) -> int:
    qty = int((raw / Decimal(spec.step_size)).to_integral_value(rounding=ROUND_DOWN)) * spec.step_size
    return min(qty, spec.max_qty)


def pnl_btc(side: str, contracts: int, entry_fill: Decimal, exit_fill: Decimal, spec: ContractSpec = ContractSpec()) -> Decimal:
    if side not in {"long", "short"} or contracts < 0:
        raise ValueError("invalid side or contracts")
    direction = Decimal(1 if side == "long" else -1)
    return Decimal(contracts) * spec.contract_size_usd * (Decimal(1) / entry_fill - Decimal(1) / exit_fill) * direction


def fee_btc(contracts: int, price: Decimal, fee_rate: Decimal, spec: ContractSpec = ContractSpec()) -> Decimal:
    return Decimal(contracts) * spec.contract_size_usd / price * fee_rate


def fill_prices(side: str, entry_raw: Decimal, exit_raw: Decimal, slippage: Decimal) -> tuple[Decimal, Decimal]:
    direction = Decimal(1 if side == "long" else -1)
    return entry_raw * (1 + direction * slippage), exit_raw * (1 - direction * slippage)


def settle_trade(side: str, contracts: int, entry_raw: Decimal, exit_raw: Decimal, fee_rate: Decimal, slippage: Decimal, spec: ContractSpec = ContractSpec()) -> dict[str, Decimal]:
    entry_fill, exit_fill = fill_prices(side, entry_raw, exit_raw, slippage)
    gross_btc = pnl_btc(side, contracts, entry_fill, exit_fill, spec)
    entry_fee_btc = fee_btc(contracts, entry_fill, fee_rate, spec)
    exit_fee_btc = fee_btc(contracts, exit_fill, fee_rate, spec)
    return {"entry_fill": entry_fill, "exit_fill": exit_fill, "gross_pnl_btc": gross_btc, "entry_fee_btc": entry_fee_btc, "exit_fee_btc": exit_fee_btc, "fee_btc": entry_fee_btc + exit_fee_btc, "net_pnl_btc": gross_btc - entry_fee_btc - exit_fee_btc}


def risk_per_contract_btc(entry_fill: Decimal, stop_fill: Decimal, spec: ContractSpec = ContractSpec()) -> Decimal:
    return spec.contract_size_usd * abs(Decimal(1) / entry_fill - Decimal(1) / stop_fill)
