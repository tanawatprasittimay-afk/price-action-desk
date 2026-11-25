# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0"]
# ///
"""Read-only local stdio tools. Start with: uv run mcp_server.py"""

from mcp.server.fastmcp import FastMCP

from tradelens.desk import build_desk, size_plan
from tradelens.evaluation import evaluate
from tradelens.market import load_market

server = FastMCP("Price Action Desk")


@server.tool()
def market_report(symbol: str = "XAUUSD", interval: str = "1h", mode: str = "demo") -> dict:
    """Closed OHLC snapshot, confirmed zones, rule decision and historical results. Symbols XAUUSD/EURUSD/GBPUSD; intervals 15m/1h/4h/1d. Set live explicitly to fetch external prices. Demo is synthetic."""
    result = build_desk(load_market(symbol, interval, mode))
    bars = result["market"].pop("candles")
    history = result["analysis"].pop("history")
    result["recent_bars"] = [
        {**bars[i], "pattern": history[i]["pattern"], "rsi": history[i]["rsi"],
         "macd": history[i]["macd"], "macd_signal": history[i]["macd_signal"],
         "levels_before_bar": history[i - 1]["levels"] if i else None}
        for i in range(max(0, len(bars) - 8), len(bars))
    ]
    result["backtest"].pop("results", None)
    return result


@server.tool()
def evaluate_strategy(symbol: str = "EURUSD", interval: str = "1h", mode: str = "demo") -> dict:
    """Compare frozen PA rules and EMA20/50 on the last 30% of closed bars. The first 70% only warms indicators. Results are simulated in risk units; report sample size and source."""
    market = load_market(symbol, interval, mode)
    report = evaluate(market["candles"])
    for name in ("production", "baseline"):
        report[name].pop("results", None)
    return {"market": {k: v for k, v in market.items() if k != "candles"}, **report}


@server.tool()
def calculate_lots(symbol: str, equity: float, risk_pct: float, entry: float, stop: float,
                   reward_risk: float = 2, leverage: float = 1, contract_size: float | None = None,
                   min_lot: float = 0.01, lot_step: float = 0.01, spread_bps: float = 2,
                   fee_bps: float = 0, slippage_bps: float = 0.25) -> dict:
    """Size a hypothetical USD-account trade using explicit costs, units per lot, and margin."""
    return size_plan({k: v for k, v in locals().items() if v is not None})


if __name__ == "__main__":
    server.run(transport="stdio")
