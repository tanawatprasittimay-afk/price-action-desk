"""Closed-bar signal research for USD-quoted linear FX and metal contracts.

A signal uses only its own candle and earlier candles. Prices in a plan are
midpoint reference levels. The simulator checks midpoint OHLC then models
bid/ask spread and adverse execution costs. Broker stop triggers may instead
use side-specific bid/ask quotes. Swap, financing and variable spread are
outside this fixed-cost model. Sizing returns base units and broker lots.
"""

from decimal import Decimal, ROUND_DOWN, localcontext
from math import isfinite

WARMUP = 35
INDICATORS = ("rsi", "macd", "signal", "histogram", "atr", "ema_fast", "ema_slow")


def _number(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError, TypeError) as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not isfinite(number) or abs(number) > 1e30 or (number and abs(number) < 1e-30):
        raise ValueError(f"{label} is outside the supported numeric range")
    return number


def _bars(candles):
    if not isinstance(candles, (list, tuple)):
        raise ValueError("candles must be a sequence of closed OHLCV bars")
    bars = []
    for item in candles:
        if not isinstance(item, dict):
            raise ValueError("each candle must contain time and OHLCV fields")
        stamp = item.get("time")
        if type(stamp) is not int or not 0 <= stamp <= 253402300799:
            raise ValueError("time must be Unix seconds")
        if bars and stamp <= bars[-1]["time"]:
            raise ValueError("candle times must increase strictly")
        bar = {k: _number(item.get(k), k) for k in ("open", "high", "low", "close", "volume")}
        if not 0 < bar["low"] <= min(bar["open"], bar["close"]):
            raise ValueError("low must be positive and at or below both open and close")
        if bar["high"] < max(bar["open"], bar["close"]) or bar["volume"] < 0:
            raise ValueError("invalid high or volume")
        bars.append({**bar, "time": stamp})
    return bars


def _average(values, period, alpha):
    """Seed with an SMA of the first period observations, then smooth."""
    result = []
    count = 0
    total = 0.0
    average = None
    for value in values:
        if value is not None:
            if count < period:
                total += value
                count += 1
                if count == period:
                    average = total / period
            else:
                average += alpha * (value - average)
        result.append(average)
    return result


def _indicators(bars):
    closes = [bar["close"] for bar in bars]
    fast = _average(closes, 12, 2 / 13)
    slow = _average(closes, 26, 2 / 27)
    macd = [None if s is None else f - s for f, s in zip(fast, slow)]
    signal = _average(macd, 9, 2 / 10)
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gains = _average([None] + [max(x, 0) for x in changes], 14, 1 / 14)
    losses = _average([None] + [max(-x, 0) for x in changes], 14, 1 / 14)
    ranges = []
    for index, bar in enumerate(bars):
        previous = closes[max(0, index - 1)]
        ranges.append(max(bar["high"], previous) - min(bar["low"], previous))
    atr = _average(ranges, 14, 1 / 14)
    for i in range(len(bars)):
        rsi = None
        if gains[i] is not None:
            movement = gains[i] + losses[i]
            rsi = 100 * gains[i] / movement if movement else 50.0
        yield {
            "rsi": rsi, "macd": macd[i], "signal": signal[i],
            "histogram": None if signal[i] is None else macd[i] - signal[i],
            "atr": atr[i], "ema_fast": fast[i], "ema_slow": slow[i],
        }


def _pattern(bar, previous):
    o, h, low, c = (bar[key] for key in ("open", "high", "low", "close"))
    spread, body = h - low, abs(c - o)
    if spread == 0:
        return "Flat bar", "neutral"
    if body <= 0.1 * spread:
        return "Doji", "neutral"
    if previous:
        po, pc = previous["open"], previous["close"]
        if c > o and pc < po and o <= pc and c >= po and body > po - pc:
            return "Bullish engulfing", "bullish"
        if c < o and pc > po and o >= pc and c <= po and body > pc - po:
            return "Bearish engulfing", "bearish"
    lower_wick = min(o, c) - low
    upper_wick = h - max(o, c)
    if lower_wick >= 2 * body and upper_wick <= body and c >= low + 0.65 * spread:
        return "Bullish pin bar", "bullish"
    if upper_wick >= 2 * body and lower_wick <= body and c <= low + 0.35 * spread:
        return "Bearish pin bar", "bearish"
    if previous:
        if h <= previous["high"] and low >= previous["low"]:
            return "Inside bar", "neutral"
        if c > previous["high"] and c > o and body >= 0.5 * spread:
            return "Bullish breakout", "bullish"
        if c < previous["low"] and c < o and body >= 0.5 * spread:
            return "Bearish breakout", "bearish"
    return ("Up candle" if c > o else "Down candle"), "neutral"


def _confirmed_levels(bars, index, pivots, atr):
    """Count distinct pivot bars, confirmed after two later closed bars.

    last_confirmed_time is the *open timestamp* of the confirming closed bar,
    consistent with the candle timestamps returned to the client.
    """
    if index >= 4:
        window = bars[index - 4:index + 1]
        pivot = window[2]
        neighbors = window[:2] + window[3:]
        if pivot["low"] < min(b["low"] for b in neighbors):
            pivots.append((index - 2, pivot["low"], bars[index]["time"]))
        if pivot["high"] > max(b["high"] for b in neighbors):
            pivots.append((index - 2, pivot["high"], bars[index]["time"]))
    pivots[:] = [point for point in pivots if point[0] > index - 160]
    if not atr or not pivots:
        return {"support": [], "resistance": [], "at_price": []}
    # Bound a zone's full width. Adjacent pivots cannot form an unbounded chain.
    tolerance = 0.4 * atr
    clusters = []
    for _, price, stamp in sorted(pivots, key=lambda point: point[1]):
        if not clusters or price - clusters[-1]["low"] > tolerance:
            clusters.append({"low": price, "high": price, "confirmed_bars": set()})
        zone = clusters[-1]
        zone["high"] = price
        zone["confirmed_bars"].add(stamp)
    close = bars[index]["close"]
    for zone in clusters:
        # An outside candle can mark both a swing high and low. Count it once
        # if those two prices belong to the same volatility-adjusted zone.
        confirmed_bars = zone.pop("confirmed_bars")
        zone["touches"] = len(confirmed_bars)
        zone["last_confirmed_time"] = max(confirmed_bars)
        zone["center"] = (zone["low"] + zone["high"]) / 2
        # Give a single swing an explicit band, including a small volatility buffer.
        zone["low"] -= 0.05 * atr
        zone["high"] += 0.05 * atr
    return {
        "support": [z for z in reversed(clusters) if z["high"] < close][:3],
        "resistance": [z for z in clusters if z["low"] > close][:3],
        "at_price": [z for z in clusters if z["low"] <= close <= z["high"]],
    }


def _net_target(entry, stop, side, fee, friction, reward_risk=2.0):
    entry_fill = entry * (1 + side * friction)
    stop_fill = stop * (1 - side * friction)
    risk = side * (entry_fill - stop_fill) + fee * (entry_fill + stop_fill)
    target_fill = (reward_risk * risk + (side + fee) * entry_fill) / (side - fee)
    return target_fill / (1 - side * friction)


def _decision(bar, pattern, bias, indicators, count, levels, fee, friction):
    if count < WARMUP:
        return "WAIT", None, [f"สะสมข้อมูล {count}/{WARMUP} แท่งปิดเพื่อเริ่มประเมิน"]
    if bias == "neutral":
        return "WAIT", None, [f"{pattern} ยังไม่ให้สัญญาณ PA ฝั่งซื้อหรือขายตามกฎ"]
    bullish = bias == "bullish"
    rsi = indicators["rsi"]
    trend = indicators["ema_fast"] - indicators["ema_slow"]
    momentum = indicators["histogram"]
    rsi_ok = 50 <= rsi < 70 if bullish else 30 < rsi <= 50
    trend_ok = trend > 0 if bullish else trend < 0
    momentum_ok = momentum > 0 if bullish else momentum < 0
    rsi_range = "50 ถึงต่ำกว่า 70" if bullish else "มากกว่า 30 ถึง 50"
    direction = "ซื้อ" if bullish else "ขาย"
    reasons = [
        f"พบ {pattern} ในแท่งปิด ให้ทิศทางฝั่ง{direction}",
        f"EMA12 {'สูงกว่า' if trend > 0 else 'ต่ำกว่า' if trend < 0 else 'เท่ากับ'} EMA26",
        f"MACD histogram {'เป็นบวก' if momentum > 0 else 'เป็นลบ' if momentum < 0 else 'เท่ากับศูนย์'}",
        f"RSI {rsi:.2f} {'อยู่ใน' if rsi_ok else 'อยู่นอก'}ช่วง {rsi_range}",
    ]
    if not all((rsi_ok, trend_ok, momentum_ok)):
        reasons.append(f"รอให้แนวโน้ม MACD และ RSI ยืนยันทิศทางฝั่ง{direction}พร้อมกัน")
        return "WAIT", None, reasons
    entry, atr = bar["close"], indicators["atr"]
    if not levels["support"] or not levels["resistance"]:
        return "WAIT", None, reasons + ["ยังไม่มีแนวรับและแนวต้านที่ยืนยันครบทั้งสองด้าน"]
    if levels.get("at_price"):
        return "WAIT", None, reasons + ["ราคาปิดยังอยู่ในโซนสวิง รอให้พ้นโซนก่อนประเมินทางเข้า"]
    support, resistance = levels["support"][0], levels["resistance"][0]
    if bullish:
        stop = min(entry - atr, min(bar["low"], support["low"]) - 0.1 * atr)
    else:
        stop = max(entry + atr, max(bar["high"], resistance["high"]) + 0.1 * atr)
    side = 1 if bullish else -1
    target = _net_target(entry, stop, side, fee, friction)
    if min(stop, target) <= 0 or entry == stop:
        return "WAIT", None, reasons + ["ช่วงราคากว้างเกินกว่าจะวางจุดหยุดขาดทุนและเป้าหมายได้"]
    obstacle = resistance["low"] if bullish else support["high"]
    if side * (obstacle - target) <= 0:
        return "WAIT", None, reasons + ["ระยะถึงโซนฝั่งตรงข้ามไม่พอสำหรับเป้าหมาย 2R หลังหักต้นทุน"]
    plan = {
        "entry": entry, "stop": stop, "target": target, "rr": 2.0,
        "support": support, "resistance": resistance,
    }
    reasons.append("มีพื้นที่ก่อนถึงโซนฝั่งตรงข้ามสำหรับเป้าหมายสุทธิ 2 เท่าของความเสี่ยง")
    return ("BUY" if bullish else "SELL"), plan, reasons


def _history(bars, fee, friction):
    pivots = []
    for i, (bar, indicators) in enumerate(zip(bars, _indicators(bars))):
        pattern, bias = _pattern(bar, bars[i - 1] if i else None)
        levels = _confirmed_levels(bars, i, pivots, indicators["atr"])
        signal, plan, reasons = _decision(bar, pattern, bias, indicators, i + 1, levels, fee, friction)
        yield {
            **indicators, "macd_signal": indicators["signal"], "signal": signal,
            "time": bar["time"], "pattern": pattern, "bias": bias, "plan": plan,
            "reasons": reasons, "levels": levels,
        }


def analyze(candles, *, fee_bps=0, slippage_bps=0.25, spread_bps=2):
    """Evaluate a strictly chronological sequence of already-closed bars."""
    bars = _bars(candles)
    fee, friction = _cost_rates(fee_bps, slippage_bps, spread_bps)
    history = list(_history(bars, fee, friction))
    if not history:
        return {
            "time": None, "signal": "WAIT", "pattern": "No data", "bias": "neutral",
            "price": None, "indicators": dict.fromkeys(INDICATORS),
            "reasons": ["ยังไม่มีแท่งปิด"], "plan": None, "history": [],
            "levels": {"support": [], "resistance": [], "at_price": []},
        }
    last = history[-1]
    indicators = {key: last["macd_signal" if key == "signal" else key] for key in INDICATORS}
    return {
        **{key: last[key] for key in ("signal", "time", "pattern", "bias", "reasons", "plan", "levels")},
        "price": bars[-1]["close"], "indicators": indicators, "history": history,
    }


def _cost_rates(fee_bps, slippage_bps, spread_bps):
    rates = [_number(v, label) for v, label in (
        (fee_bps, "fee_bps"), (slippage_bps, "slippage_bps"), (spread_bps, "spread_bps")
    )]
    if not all(0 <= rate <= 1000 for rate in rates):
        raise ValueError("cost inputs must be between 0 and 1000 basis points")
    fee, slip, spread = rates
    return fee / 10000, (slip + spread / 2) / 10000


def plan_target(entry, stop, reward_risk, *, fee_bps=0, slippage_bps=0.25, spread_bps=2):
    """Midpoint target that yields the desired net reward per dollar of planned risk."""
    entry = _number(entry, "entry")
    stop = _number(stop, "stop")
    ratio = _number(reward_risk, "reward_risk")
    if min(entry, stop, ratio) <= 0 or entry == stop:
        raise ValueError("entry, stop and reward_risk must be positive and entry must differ from stop")
    fee, friction = _cost_rates(fee_bps, slippage_bps, spread_bps)
    side = 1 if entry > stop else -1
    target = _net_target(entry, stop, side, fee, friction, ratio)
    if target <= 0 or side * (target - entry) <= 0:
        raise ValueError("no positive reachable target at the requested reward/risk")
    return target


def position_size(equity, risk_pct, entry, stop, *, fee_bps=0, slippage_bps=0.25,
                  spread_bps=2, contract_size=1, lot_step=0.01, min_lot=0.01, leverage=1, target=None):
    """USD account, USD quote, user-supplied linear contract and broker limits.

    Spread is the full bid/ask width in bps of mid. The estimate includes
    commission on each fill. Gaps beyond the stop can cause a larger loss.
    """
    values = {
        "equity": equity, "risk_pct": risk_pct, "entry": entry, "stop": stop,
        "contract_size": contract_size, "lot_step": lot_step, "min_lot": min_lot, "leverage": leverage,
    }
    values = {name: _number(value, name) for name, value in values.items()}
    for name, value in values.items():
        if value <= 0:
            raise ValueError(f"{name} must be positive")
    fee, friction = (Decimal(str(v)) for v in _cost_rates(fee_bps, slippage_bps, spread_bps))
    with localcontext() as context:
        context.prec = 160
        equity, pct, entry, stop, contract, step, minimum, leverage = (Decimal(str(v)) for v in values.values())
        if pct > 100 or entry == stop or leverage < 1:
            raise ValueError("risk_pct must be at most 100, entry must differ from stop and leverage must be >= 1")
        side = 1 if entry > stop else -1
        entry_fill = entry * (1 + side * friction)
        stop_fill = stop * (1 - side * friction)
        risk_per_unit = side * (entry_fill - stop_fill) + fee * (entry_fill + stop_fill)
        risk_budget = equity * pct / 100
        by_risk = risk_budget / (risk_per_unit * contract)
        margin_per_lot = entry_fill * contract / leverage
        by_capital = equity / (margin_per_lot + fee * entry_fill * contract)
        available = min(by_risk, by_capital)
        # Some brokers have a minimum larger than the volume increment.
        lots = Decimal(0)
        if available >= minimum:
            increments = ((available - minimum) / step).to_integral_value(rounding=ROUND_DOWN)
            lots = minimum + increments * step
        units = lots * contract
        reward = rr = None
        if target is not None:
            target = _number(target, "target")
            if target <= 0:
                raise ValueError("target must be positive")
            target = Decimal(str(target))
            if side * (target - entry) <= 0:
                raise ValueError("target must be on the opposite side of entry from stop")
            exit_fill = target * (1 - side * friction)
            net = side * (exit_fill - entry_fill) - fee * (entry_fill + exit_fill)
            reward, rr = float(units * net), float(net / risk_per_unit)
        return {
            "estimated_reward": reward, "net_rr": rr,
            "units": float(units), "lots": float(lots), "risk_budget": float(risk_budget),
            "estimated_loss": float(units * risk_per_unit), "notional": float(units * entry),
            "risk_per_unit": float(risk_per_unit), "limited_by_capital": by_capital < by_risk,
            "margin": float(lots * margin_per_lot), "contract_size": float(contract),
            "lot_step": float(step), "min_lot": float(minimum), "leverage": float(leverage),
        }


def _open_trade(bar, setup, fee, slip):
    side = 1 if setup["signal"] == "BUY" else -1
    stop, target = setup["plan"]["stop"], setup["plan"]["target"]
    opening = bar["open"]
    entry = opening * (1 + side * slip)
    if not all(min(stop, target) < p < max(stop, target) for p in (opening, entry)):
        return None
    stop_fill = stop * (1 - side * slip)
    risk = side * (entry - stop_fill) + fee * (entry + stop_fill)
    target_fill = target * (1 - side * slip)
    reward = side * (target_fill - entry) - fee * (entry + target_fill)
    if reward + 1e-12 * max(abs(entry), abs(target)) < 2 * risk:
        return None
    return {
        "side": setup["signal"], "signal_time": setup["time"], "entry_time": bar["time"],
        "entry": entry, "stop": stop, "target": target, "risk": risk,
    }


def _exit_trade(trade, bar, fee, slip):
    bullish = trade["side"] == "BUY"
    side = 1 if bullish else -1
    stop, target = trade["stop"], trade["target"]
    at_open = bar["open"]
    # On a gap through a stop, the opening price is the next available price.
    # A gap through a target receives at most the target; the model assumes
    # adverse slippage on both exits. Ties within one candle use the stop.
    if side * (at_open - stop) <= 0:
        exit_price, reason = at_open, "gap_stop"
    elif side * (at_open - target) >= 0:
        exit_price, reason = target, "target"
    elif (bar["low"] <= stop if bullish else bar["high"] >= stop):
        exit_price, reason = stop, "stop"
    elif (bar["high"] >= target if bullish else bar["low"] <= target):
        exit_price, reason = target, "target"
    else:
        return None
    exit_fill = exit_price * (1 - side * slip)
    net = side * (exit_fill - trade["entry"]) - fee * (exit_fill + trade["entry"])
    return {
        **{k: v for k, v in trade.items() if k != "risk"},
        "exit_time": bar["time"], "exit_reason": reason, "exit": exit_fill,
        "r": net / trade["risk"],
    }


def backtest(candles, *, fee_bps=0, slippage_bps=0.25, spread_bps=2):
    """One position at a time, fixed signal levels, next-open entry, no forced exit.

    R uses estimated cost-inclusive risk from the actual entry to the planned
    stop. Drawdown is peak-to-trough realized R, without compounding.
    """
    bars = _bars(candles)
    fee, slip = _cost_rates(fee_bps, slippage_bps, spread_bps)
    return _simulate(bars, _history(bars, fee, slip), fee, slip, slippage_bps, spread_bps)


def _simulate(bars, setups, fee, slip, slippage_bps, spread_bps):
    active = pending = None
    signals = skipped = wins = 0
    net_r = high_water = max_drawdown = 0.0
    results = []
    for bar, setup in zip(bars, setups):
        if pending is not None:
            active = _open_trade(bar, pending, fee, slip)
            if active is None:
                skipped += 1
            pending = None
        if active is not None:
            result = _exit_trade(active, bar, fee, slip)
            if result is not None:
                results.append(result)
                wins += int(result["r"] > 0)
                net_r += result["r"]
                high_water = max(high_water, net_r)
                max_drawdown = max(max_drawdown, high_water - net_r)
                active = None
        if setup["signal"] != "WAIT":
            signals += 1
            if active is None:
                pending = setup
            else:
                skipped += 1
    trades = len(results)
    breakeven = sum(row["r"] == 0 for row in results)
    return {
        "signals": signals, "trades": trades, "wins": wins, "breakeven": breakeven,
        "losses": trades - wins - breakeven, "skipped": skipped,
        "unresolved": int(active is not None or pending is not None),
        "win_rate": 100 * wins / trades if trades else None,
        "net_r": net_r, "avg_r": net_r / trades if trades else None,
        "max_drawdown_r": max_drawdown, "fee_bps": fee * 10000,
        "slippage_bps": float(slippage_bps), "spread_bps": float(spread_bps), "results": results,
    }
