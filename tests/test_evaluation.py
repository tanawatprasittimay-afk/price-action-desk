import unittest

from tradelens.engine import analyze, backtest
from tradelens.evaluation import evaluate
from tests.test_engine import bar, buy_fixture


def flat_market():
    return [bar(t, 100, 100.2, 99.8, 100) for t in range(100)]


def single_cross(cross_index):
    rows = flat_market()
    for i in range(cross_index, len(rows)):
        rows[i] = bar(i, 101, 101.2, 100.8, 101)
    rows[cross_index] = bar(cross_index, 100, 101.2, 99.8, 101)
    return rows


class HoldoutEvaluation(unittest.TestCase):
    def test_split_size_and_insufficient_warmup(self):
        with self.assertRaises(ValueError):
            evaluate(flat_market()[:71])
        report = evaluate(flat_market()[:72])
        self.assertEqual(report["period"], {
            "bars": 72, "prefix_bars": 50, "holdout_bars": 22,
            "prefix_start": 0, "prefix_end": 49, "holdout_start": 50, "holdout_end": 71,
        })
        self.assertIsNone(report["baseline"]["win_rate"])

    def test_final_prefix_cross_does_not_open_a_holdout_position(self):
        result = evaluate(single_cross(69))["baseline"]
        self.assertEqual((result["signals"], result["trades"], result["unresolved"]), (0, 0, 0))

    def test_cross_on_first_holdout_bar_can_enter_only_the_following_bar(self):
        rows = single_cross(70)
        rows[71] = bar(71, 101, 110, 100.99, 101)
        result = evaluate(rows)["baseline"]
        self.assertEqual(result["signals"], 1)
        trade = result["results"][0]
        self.assertEqual((trade["signal_time"], trade["entry_time"], trade["exit_time"]), (70, 71, 71))
        self.assertEqual(trade["exit_reason"], "target")
        self.assertAlmostEqual(result["net_r"], 2)

    def test_short_cross_uses_same_stop_first_policy_and_costs(self):
        rows = single_cross(70)
        rows = [bar(i, 200-r["open"], 200-r["low"], 200-r["high"], 200-r["close"]) for i, r in enumerate(rows)]
        rows[71] = bar(71, 99, 115, 80, 99)
        report = evaluate(rows, spread_bps=3, fee_bps=0.5, slippage_bps=0.3)
        result = report["baseline"]
        self.assertEqual(result["results"][0]["side"], "SELL")
        self.assertEqual(result["results"][0]["exit_reason"], "stop")
        self.assertEqual((result["losses"], result["max_drawdown_r"]), (1, 1))
        self.assertEqual(report["production"]["spread_bps"], result["spread_bps"])

    def test_last_crossover_is_unresolved_without_a_future_fill(self):
        result = evaluate(single_cross(99))["baseline"]
        self.assertEqual((result["unresolved"], result["trades"], result["signals"]), (1, 0, 1))
        self.assertIsNone(result["avg_r"])
        self.assertIsNone(result["win_rate"])

    def test_production_strategy_matches_existing_backtest_for_a_heldout_trade(self):
        prefix = [bar(i, 100, 100, 100, 100) for i in range(27)]
        setup = [dict(row, time=i + 27) for i, row in enumerate(buy_fixture())]
        data = prefix + setup
        plan = analyze(data)["plan"]
        self.assertIsNotNone(plan)
        entry, target = plan["entry"], plan["target"]
        data.append(bar(len(data), entry, target, entry, entry))
        while len(data) < 100:
            data.append(bar(len(data), entry, entry, entry, entry))
        result = evaluate(data)["production"]
        reference = backtest(data)
        heldout = [t for t in reference["results"] if t["signal_time"] >= 70]
        self.assertEqual(len(heldout), 1)
        self.assertEqual(result["results"], heldout)
        self.assertAlmostEqual(result["net_r"], 2)


if __name__ == "__main__":
    unittest.main()
