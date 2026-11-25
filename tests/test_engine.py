import random
import unittest

from tradelens.engine import analyze, backtest, plan_target, position_size


def bar(t, o, h, low, c):
    return {"time": t, "open": o, "high": h, "low": low, "close": c, "volume": 20}


def buy_fixture():
    """Artificial series with a confirmed overhead pivot and bullish final bar."""
    rng = random.Random(10)
    rows = []
    close = 100
    for i in range(46):
        opening = close
        close += rng.uniform(-0.45, 0.45)
        high = max(opening, close) + rng.uniform(0.01, 0.17)
        low = min(opening, close) - rng.uniform(0.01, 0.17)
        rows.append(bar(i * 60, opening, high + (12 if i == 6 else 0), low, close))
    return rows


def reflect(rows):
    return [bar(b["time"], 200-b["open"], 200-b["low"], 200-b["high"], 200-b["close"]) for b in rows]


class Indicators(unittest.TestCase):
    def test_wilder_example_seed_and_next_value(self):
        prices = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                  46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
        rows = [bar(i, p, p, p, p) for i, p in enumerate(prices)]
        history = analyze(rows)["history"]
        self.assertIsNone(history[13]["rsi"])
        self.assertAlmostEqual(history[14]["rsi"], 70.4641, places=4)
        self.assertAlmostEqual(history[15]["rsi"], 66.2496, places=4)

    def test_linear_ema_macd_and_gap_adjusted_atr(self):
        rows = [bar(i, i + 1, i + 1, i + 1, i + 1) for i in range(40)]
        history = analyze(rows)["history"]
        self.assertIsNone(history[24]["macd"])
        self.assertIsNone(history[32]["macd_signal"])
        self.assertEqual(history[33]["macd"], 7.0)
        self.assertEqual(history[33]["macd_signal"], 7.0)
        self.assertEqual(history[33]["histogram"], 0.0)
        self.assertEqual(history[14]["rsi"], 100)
        self.assertAlmostEqual(history[13]["atr"], 13 / 14)
        rows[14] = bar(14, 20, 20, 19, 19)
        self.assertAlmostEqual(analyze(rows[:15])["indicators"]["atr"], (13 / 14 * 13 + 6) / 14)

    def test_no_data_and_flat_market(self):
        self.assertEqual(analyze([])["signal"], "WAIT")
        self.assertIsNone(backtest([])["win_rate"])
        decision = analyze([bar(i, 5, 5, 5, 5) for i in range(50)])
        self.assertEqual(decision["pattern"], "Flat bar")
        self.assertEqual(decision["indicators"]["rsi"], 50)
        self.assertEqual(decision["signal"], "WAIT")

    def test_invalid_price_and_order(self):
        valid = bar(0, 2, 3, 1, 2)
        for rows in ([valid, valid], [dict(valid, close=float("nan"))],
                     [dict(valid, low=2.5)], [dict(valid, volume=-1)], [dict(valid, time=0.5)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                analyze(rows)

    def test_candle_shapes(self):
        first = bar(1, 15, 16, 12, 13)
        cases = [
            (bar(2, 12, 17, 11, 16), "Bullish engulfing"),
            (bar(2, 18, 18.1, 14, 17.5), "Bullish pin bar"),
            (bar(2, 14, 15, 12.5, 13.5), "Inside bar"),
            (bar(2, 14.2, 15, 13, 14.1), "Doji"),
        ]
        for last, wanted in cases:
            self.assertEqual(analyze([first, last])["pattern"], wanted)


class TradeRules(unittest.TestCase):
    def test_latest_analysis_matches_past_prefixes_and_mirrors_direction(self):
        for rows, side in ((buy_fixture(), "BUY"), (reflect(buy_fixture()), "SELL")):
            analysis = analyze(rows)
            self.assertEqual(analysis["signal"], side)
            self.assertEqual(analysis["plan"]["rr"], 2)
            for n in range(1, len(rows) + 1):
                self.assertEqual(analysis["history"][n - 1], analyze(rows[:n])["history"][-1])

    def test_pivot_requires_two_future_bars_to_confirm(self):
        rows = [bar(i, 10, 11, 9, 10) for i in range(19)]
        rows[16]["high"] = 15
        self.assertEqual(analyze(rows[:18])["levels"]["resistance"], [])
        zone = analyze(rows)["levels"]["resistance"][0]
        self.assertEqual(zone["center"], 15)
        self.assertEqual(zone["last_confirmed_time"], 18)

    def test_no_resistance_or_insufficient_space_blocks_buy(self):
        rows = buy_fixture()
        rows[6]["high"] -= 12
        self.assertEqual(analyze(rows)["levels"]["resistance"], [])
        self.assertEqual(analyze(rows)["signal"], "WAIT")
        rows[6]["high"] = 104
        self.assertIn("2R", " ".join(analyze(rows)["reasons"]))
        self.assertIsNone(analyze(rows)["plan"])

    def test_pending_entries_and_adverse_entry_gap(self):
        rows = buy_fixture()
        p = analyze(rows)["plan"]["entry"]
        stats = backtest(rows)
        self.assertEqual((stats["trades"], stats["unresolved"], stats["win_rate"]), (0, 1, None))
        next_bar = bar(9999, p + 0.1, p + 0.1, p + 0.1, p + 0.1)
        stats = backtest(rows + [next_bar])
        self.assertEqual((stats["trades"], stats["unresolved"], stats["skipped"]), (0, 0, 1))

    def test_stop_before_target_when_both_hit_long_and_short(self):
        for rows in (buy_fixture(), reflect(buy_fixture())):
            plan = analyze(rows)["plan"]
            p = plan["entry"]
            low, high = sorted([plan["target"], plan["stop"]])
            stats = backtest(rows + [bar(9999, p, high + 0.1, low - 0.1, p)])
            self.assertEqual(stats["trades"], 1)
            self.assertEqual(stats["results"][0]["exit_reason"], "stop")
            self.assertEqual(stats["results"][0]["entry_time"], 9999)
            self.assertAlmostEqual(stats["net_r"], -1)
            self.assertAlmostEqual(stats["max_drawdown_r"], 1)

    def test_stop_gap_can_lose_more_than_one_r(self):
        rows = buy_fixture()
        plan = analyze(rows)["plan"]
        p = plan["entry"]
        gap = plan["stop"] - 1
        stats = backtest(rows + [bar(9999, p, p, p, p), bar(10000, gap, gap, gap, gap)])
        self.assertEqual(stats["results"][0]["exit_reason"], "gap_stop")
        self.assertLess(stats["net_r"], -1)
        self.assertGreater(stats["max_drawdown_r"], 1)

    def test_planned_target_is_two_r_after_both_sides_costs(self):
        costs = dict(fee_bps=0.4, slippage_bps=0.5, spread_bps=2.4)
        for rows in (buy_fixture(), reflect(buy_fixture())):
            plan = analyze(rows, **costs)["plan"]
            p, target = plan["entry"], plan["target"]
            stats = backtest(rows + [bar(9999, p, max(p, target), min(p, target), p)], **costs)
            self.assertEqual(stats["wins"], 1)
            self.assertAlmostEqual(stats["net_r"], 2)
            size = position_size(10000, 1, p, plan["stop"], target=target, **costs)
            self.assertAlmostEqual(size["net_rr"], 2)
            self.assertAlmostEqual(size["estimated_reward"], size["estimated_loss"] * 2)


class Sizing(unittest.TestCase):
    def test_fx_standard_lot_and_gold_contract(self):
        costs = dict(fee_bps=0, slippage_bps=0, spread_bps=0)
        fx = position_size(10000, 1, 1.08, 1.075, contract_size=100000, leverage=30, **costs)
        self.assertEqual(fx["lots"], 0.2)
        self.assertEqual(fx["units"], 20000)
        self.assertEqual(fx["estimated_loss"], 100)
        self.assertAlmostEqual(fx["margin"], 720)
        gold = position_size(10000, 1, 2700, 2710, contract_size=100, leverage=30, **costs)
        self.assertEqual(gold["lots"], 0.1)
        self.assertEqual(gold["units"], 10)
        self.assertEqual(gold["estimated_loss"], 100)

    def test_low_equity_cannot_round_up_to_min_lot(self):
        quote = position_size(500, 0.1, 1.2, 1.19, contract_size=100000, leverage=30)
        self.assertEqual((quote["lots"], quote["units"], quote["estimated_loss"]), (0, 0, 0))

    def test_capital_and_risk_limits_with_fees(self):
        size = position_size(1000, 10, 100, 99, contract_size=0.1, lot_step=1, min_lot=1, fee_bps=10)
        self.assertTrue(size["limited_by_capital"])
        self.assertLessEqual(size["margin"], 1000)
        self.assertLessEqual(size["estimated_loss"], 100)
        self.assertGreater(size["risk_per_unit"], 1)

    def test_editable_rr_target_and_position_size_agree_long_short(self):
        costs = dict(fee_bps=0.2, slippage_bps=0.4, spread_bps=2.1)
        for entry, stop, ratio in ((1.1, 1.075, 1.5), (2700, 2720, 3)):
            target = plan_target(entry, stop, ratio, **costs)
            sized = position_size(10000, 1, entry, stop, target=target, **costs)
            self.assertAlmostEqual(sized["net_rr"], ratio)
        with self.assertRaises(ValueError):
            plan_target(1.1, 2.3, 2.0)

    def test_input_bounds_and_target_side(self):
        for args in (dict(leverage=0.5), dict(spread_bps=-1), dict(target=8),
                     dict(contract_size=float("inf")), dict(min_lot=0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                position_size(100, 1, 10, 9, **args)
        with self.assertRaises(ValueError):
            position_size(100, 1, 10, 10)


if __name__ == "__main__":
    unittest.main()
