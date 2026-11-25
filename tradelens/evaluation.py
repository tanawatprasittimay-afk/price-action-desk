"""Locked, time-ordered holdout comparison of two predefined trading rules."""

from itertools import islice

from .engine import _average, _bars, _cost_rates, _history, _net_target, _simulate


def _baseline(bars, analysis, fee, friction):
    """Fresh 20/50 crossover at close, 1 ATR stop, 2R net target."""
    closes = [bar["close"] for bar in bars]
    fast = _average(closes, 20, 2 / 21)
    slow = _average(closes, 50, 2 / 51)
    previous = None
    for i, bar in enumerate(bars):
        current = fast[i] - slow[i] if slow[i] is not None else None
        side = 0
        if previous is not None and current is not None:
            if previous <= 0 < current:
                side = 1
            elif previous >= 0 > current:
                side = -1
        previous = current
        atr = analysis[i]["atr"]
        if side and atr and atr > 0:
            entry = bar["close"]
            stop = entry - side * atr
            target = _net_target(entry, stop, side, fee, friction)
            if min(stop, target) > 0 and side * (target - entry) > 0:
                yield {
                    "time": bar["time"], "signal": "BUY" if side > 0 else "SELL",
                    "plan": {"entry": entry, "stop": stop, "target": target},
                }
                continue
        yield {"time": bar["time"], "signal": "WAIT", "plan": None}


def evaluate(candles, *, fee_bps=0, slippage_bps=0.25, spread_bps=2):
    """Warm indicators on the first floor(70%) bars, then simulate from flat.

    Boundaries are the opening timestamps of the first/last included closed
    candle. Ignore even the final signal in the prefix. Each strategy starts
    flat and without a pending order. The EMA baseline preserves indicator
    state across the split, so a genuine crossover on the first test bar counts.
    """
    bars = _bars(candles)
    split = len(bars) * 7 // 10
    if split < 50:
        raise ValueError("ต้องใช้แท่งปิดอย่างน้อย 72 แท่ง เพื่ออุ่น EMA50 ก่อนช่วงทดสอบ")
    fee, friction = _cost_rates(fee_bps, slippage_bps, spread_bps)
    production = list(_history(bars, fee, friction))
    baseline = _baseline(bars, production, fee, friction)
    holdout = bars[split:]

    def run(setups):
        return _simulate(holdout, islice(setups, split, None), fee, friction, slippage_bps, spread_bps)

    return {
        "period": {
            "bars": len(bars), "prefix_bars": split, "holdout_bars": len(holdout),
            "prefix_start": bars[0]["time"], "prefix_end": bars[split - 1]["time"],
            "holdout_start": holdout[0]["time"], "holdout_end": holdout[-1]["time"],
        },
        "production": run(production),
        "baseline": run(baseline),
        "baseline_rules": "EMA20 ตัด EMA50 เมื่อปิดแท่ง หยุดขาดทุนห่าง 1 ATR14 เป้าสุทธิ 2R",
        "limitations": [
            "ใช้ช่วงท้าย 30% เพียงช่วงเดียว เก็บช่วงต้นไว้เริ่มต้นค่าอินดิเคเตอร์ ทั้งสองระบบเริ่มช่วงทดสอบโดยไม่มีสถานะ",
            "signals นับจังหวะเข้าเกณฑ์ทั้งหมด trades นับเฉพาะรายการที่ปิดแล้ว ผลชนะและผลตอบแทนเฉลี่ยใช้รายการที่ปิดเท่านั้น",
            "การเปรียบเทียบรวมทั้งวิธีเลือกจังหวะและวางจุดตัดขาดทุน ผลต่างจึงไม่ได้ชี้ว่าปัจจัยใดให้ผลเพียงลำพัง",
            "ผลขึ้นกับแหล่งราคาและเวลาแท่งของชุดข้อมูล กราฟทดลองและราคากลางอาจต่างจาก bid/ask ที่โบรกเกอร์ใช้",
            "ใช้สเปรดและต้นทุนคงที่ คำนวณผลตามแท่งราคา ไม่รวม swap และ drawdown ระหว่างถือสถานะ",
            "จำนวนดีลน้อยและข้อมูลตลาดเพียงช่วงเดียวจำกัดการสรุปผลสำหรับอนาคต",
        ],
    }
