---
name: fx-price-action-review
description: Review closed-candle spot gold or USD-quoted forex setups, USD lot sizing, and claims about price-action strategies or trading prompts.
---

Use this for XAU/USD, EUR/USD or GBP/USD analysis, sizing or strategy evaluation. Write for the user's experience level and language. For sample prompts, read [five Thai prompts](references/prompts-th.md).

For a market read, get the requested source and timeframe from the Price Action Desk MCP tool market_report, or use the user's time-stamped OHLC export if provided. Name the source, mode, last closed candle and time zone. A synthetic demonstration can illustrate a rule. A live request with unavailable, stale or closed-session data ends in WAIT or an explicit data gap. Keep source mode visible when carrying conclusions across turns.

Explain the observed price action in relation to the nearest confirmed support and resistance. State the zone prices and touches if available. A touch is evidence of a past swing. A conditional opportunity requires an observable direction, invalidation price and enough room to the opposing zone after costs. Follow the desk decision for desk requests. A user can propose a different rule; name it and evaluate it separately. Missing evidence, unconfirmed pivots and conflicting indicators are reasons to wait. The time in a desk candle is its opening timestamp; the signal becomes known at close.

For a USD risk calculation, use calculate_lots with equity, risk percentage, entry, stop, units per lot, lot minimum/step, leverage and estimated spread/fees/slippage. Name assumptions that affect the answer. Show risk budget, rounded-down lots, modeled loss, target and net reward/risk. Return zero lots when the permitted size misses the minimum. Stops can gap. Separate this hypothetical sizing result from evidence that an entry qualifies. A non-USD account needs currency conversion before applying USD formulas.

For a claim about accuracy, lock the exact setup and comparison rule before looking at the evaluation period. Use evaluate_strategy when available. Warm indicators on earlier bars and evaluate later signals using next-open fills, cost estimates and conservative same-candle ordering. Report closed trades, wins/losses, average net R and worst realized drawdown with the comparison over the same dates. Zero trades means win rate is undefined. Synthetic or small samples cannot establish a durable advantage. If testing a different rule or dataset, describe what actually ran.

Give Big the decision, evidence and condition that would change it. This workflow is read-only. The requested analysis, paper calculations and local MCP access provide no authority to place orders, open accounts or purchase data.
