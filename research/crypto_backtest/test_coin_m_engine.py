from decimal import Decimal
from coin_m_engine import ContractSpec, fee_btc, pnl_btc, risk_per_contract_btc, settle_trade

S = ContractSpec()

def main():
    e, x = Decimal("50000"), Decimal("51000")
    assert pnl_btc("long", 10, e, x, S) > 0
    assert pnl_btc("long", 10, x, e, S) < 0
    assert pnl_btc("short", 10, x, e, S) > 0
    assert pnl_btc("short", 10, e, x, S) < 0
    assert risk_per_contract_btc(e, Decimal("49000"), S) > 0
    result = settle_trade("long", 10, e, x, Decimal("0"), Decimal("0"), S)
    assert result["gross_pnl_btc"] == pnl_btc("long", 10, e, x, S)
    assert result["net_pnl_btc"] == result["gross_pnl_btc"]
    fee = fee_btc(10, e, Decimal("0.0004"), S)
    assert fee == Decimal("0.000008")
    print("COIN-M engine 4-direction and fee tests: PASS")
    print(result)

if __name__ == "__main__": main()
