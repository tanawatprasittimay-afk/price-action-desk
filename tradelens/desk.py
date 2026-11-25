"""Shared public interface used by both the browser and local MCP client."""

from math import isfinite

from .engine import analyze, backtest, plan_target, position_size
from .importer import SYMBOLS


def build_desk(market):
    bars = market["candles"]
    analysis = analyze(bars)
    if market["mode"] == "live" and (market["stale"] or market.get("session_closed")):
        reason = market.get("warning") or "ตลาดปิดหรือข้อมูลล่าช้า กรุณารอราคาชุดใหม่"
        analysis.update(signal="WAIT", plan=None, reasons=[reason, *analysis["reasons"]])
    return {"market": market, "analysis": analysis, "backtest": backtest(bars)}


def size_plan(values):
    symbol = values.get("symbol", "XAUUSD")
    if symbol not in SYMBOLS:
        raise ValueError("ไม่รองรับสินทรัพย์ที่เลือก")
    needed = ("equity", "risk_pct", "entry", "stop", "reward_risk")
    if any(k not in values for k in needed):
        raise ValueError("กรอกทุน ความเสี่ยง ราคาเข้า จุดตัดขาดทุน และระยะเป้าหมายให้ครบ")
    defaults = dict(contract_size=100 if symbol == "XAUUSD" else 100000, lot_step=0.01,
                    min_lot=0.01, leverage=1, fee_bps=0, slippage_bps=0.25, spread_bps=2)
    args = {k: values.get(k, default) for k, default in defaults.items()}
    try:
        rr = float(values["reward_risk"])
        if not isfinite(rr) or not 0 < rr <= 100 or isinstance(values["reward_risk"], bool):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError("อัตรากำไรต่อความเสี่ยงต้องมากกว่า 0 และไม่เกิน 100") from None
    costs = {k: args[k] for k in ("fee_bps", "slippage_bps", "spread_bps")}
    target = plan_target(values["entry"], values["stop"], rr, **costs)
    result = position_size(*(values[k] for k in needed[:4]), target=target, **args)
    return dict(result, target=target, reward_risk=rr)
