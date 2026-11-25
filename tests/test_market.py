from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from tradelens import market
from tradelens.evaluation import evaluate


END = 1_735_689_600
STEP = 900


def row(start):
    stamp = datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    return dict(datetime=stamp, open='100', high='110', low='90', close='106')


def snapshot():
    return dict(as_of=END, source_time=END + 4, fetched_at=END + 5, public_trial=True,
                source='Twelve Data (EUR/USD public trial)', excluded_weekend_bars=0, candles=[])


class MarketTests(unittest.TestCase):
    def setUp(self):
        market._cache.clear()

    def tearDown(self):
        market._cache.clear()

    def test_only_closed_candles_from_requested_spot_symbol(self):
        rows = [row(END - STEP), row(END)]
        with patch.dict(market.os.environ, {'TWELVE_DATA_API_KEY': ''}), patch.object(market.time, 'time', return_value=END + 100), patch.object(market, '_get_series', return_value=(rows, END + 1)) as fetch:
            data = market._fetch('EURUSD', '15m')
            self.assertEqual(fetch.call_args.args[:2], ('EUR/USD', '15min'))
            self.assertEqual(fetch.call_args.args[-1], 'demo')
            self.assertTrue(data['public_trial'])
            self.assertFalse(data['has_volume'])
            self.assertEqual([c['time'] for c in data['candles']], [END - STEP])
            self.assertEqual(data['as_of'], END)

    def test_no_demo_substitution_for_gold_or_sterling_missing_key(self):
        with patch.dict(market.os.environ, {'TWELVE_DATA_API_KEY': ''}), patch.object(market, '_get_series', side_effect=AssertionError('unexpected call')):
            for symbol in ('XAUUSD', 'GBPUSD'):
                with self.assertRaisesRegex(market.MarketUnavailable, 'TWELVE_DATA_API_KEY'):
                    market.load_market(symbol, '1h')

    def test_malformed_or_duplicate_candles_rejected(self):
        first = row(END - STEP)
        for rows in ([first, first], [dict(first, low='111')], [dict(first, open='NaN')], [row(END + 1)], [{}], []):
            with self.subTest(rows=rows), self.assertRaises(market.MarketUnavailable):
                market._parse_candles(rows, STEP, END + 2 * STEP)

    def test_utc_aggregation_excludes_incomplete_buckets_and_uses_extremes(self):
        rows = [row(END + i * 3600) for i in range(48) if i != 32]
        rows[5].update(high='145')
        rows[23].update(close='93')
        result = market._parse_candles(rows, 3600, END + 48 * 3600)
        self.assertEqual(market._aggregate_utc(result, 86400), [dict(time=END, open=100, high=145, low=90, close=93, volume=0)])
        complete_4h = market._aggregate_utc(result, 14400)
        self.assertEqual(len(complete_4h), 11)
        self.assertNotIn(END + 32 * 3600, [c['time'] for c in complete_4h])

    def test_demo_is_explicit_offline_and_independently_mutable(self):
        with patch.object(market, '_get_series', side_effect=AssertionError('unexpected call')):
            first = market.load_market('XAUUSD', '4h', 'demo')
            second = market.load_market('XAUUSD', '4h', 'demo')
            self.assertEqual(first, second)
            self.assertEqual(first['status'], 'demo')
            first['candles'][0]['close'] = -10
            self.assertGreaterEqual(second['candles'][0]['close'], 0)
            for args in [('BTCUSD', '1h', 'live'), ('EURUSD', '1m', 'live'), ('XAUUSD', '1h', 'test')]:
                with self.assertRaises(ValueError):
                    market.load_market(*args)

    def test_first_failure_no_fallback_and_cooldown(self):
        with patch.object(market, '_fetch', side_effect=market.MarketUnavailable('offline')) as fetch:
            for _ in range(2):
                with self.assertRaisesRegex(market.MarketUnavailable, 'offline'):
                    market.load_market('XAUUSD', '15m', 'live')
            self.assertEqual(fetch.call_count, 1)

    def test_last_good_snapshot_stale_after_outage_and_recovers(self):
        attempts = [snapshot(), market.MarketUnavailable('offline'), snapshot()]
        with patch.object(market, '_fetch', side_effect=attempts), patch.object(market.time, 'time', return_value=END + 5), patch.object(market.time, 'monotonic', return_value=0) as clock:
            self.assertFalse(market.load_market('EURUSD', '15m')['stale'])
            clock.return_value = 900
            stale = market.load_market('EURUSD', '15m')
            self.assertTrue(stale['stale'])
            self.assertIn('offline', stale['warning'])
            self.assertIn('Twelve Data', stale['source'])
            clock.return_value = 961
            self.assertFalse(market.load_market('EURUSD', '15m')['stale'])

    def test_delayed_feed_and_clock_skew_are_stale(self):
        for as_of, source_time in [(END - STEP, END + 2), (END, END + 240)]:
            market._cache.clear()
            data = dict(snapshot(), as_of=as_of, source_time=source_time)
            with patch.object(market, '_fetch', return_value=data), patch.object(market.time, 'time', return_value=END + 5):
                self.assertTrue(market.load_market('EURUSD', '15m')['stale'])

    def test_simultaneous_callers_share_one_fetch(self):
        with patch.object(market.time, 'time', return_value=END + 5), patch.object(market, '_fetch', return_value=snapshot()) as fetch, ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: market.load_market('EURUSD', '15m'), range(8)))
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(all(item['status'] == 'fresh' for item in results))

    def test_weekly_overlap_half_open_boundaries_and_dst(self):
        def overlaps(start, seconds, symbol='EURUSD'):
            timestamp = int(datetime.fromisoformat(start + '+00:00').timestamp())
            return market._overlaps_weekend(symbol, timestamp, seconds)

        # New York jumps from EST to EDT on this weekend. Gold reopens an hour later.
        self.assertFalse(overlaps('2026-03-06T20:59', 3600))  # ends exactly at Friday 16:59 ET
        self.assertTrue(overlaps('2026-03-06T21:00', 3600))
        self.assertTrue(overlaps('2026-03-07T12:00', 900))
        self.assertTrue(overlaps('2026-03-08T21:00', 900))
        self.assertFalse(overlaps('2026-03-08T21:05', 3600))
        self.assertTrue(overlaps('2026-03-08T21:05', 3600, 'XAUUSD'))
        self.assertFalse(overlaps('2026-03-08T22:05', 3600, 'XAUUSD'))
        self.assertTrue(overlaps('2026-03-06T20:00', 14400))
        self.assertTrue(overlaps('2026-03-06T00:00', 86400))
        self.assertTrue(overlaps('2026-03-08T00:00', 86400))
        self.assertFalse(overlaps('2026-03-09T00:00', 86400))

    def test_weekend_ema_trade_disappears_through_real_feed_path(self):
        cross = 117
        saturday = int(datetime(2026, 6, 13, 12, tzinfo=timezone.utc).timestamp())
        start = saturday - cross * 3600
        end = start + 150 * 3600
        rows = []
        for index in range(150):
            value = 100 if index < cross else 101
            item = row(start + index * 3600)
            item.update(open=value, high=value + 0.2, low=value - 0.2, close=value)
            rows.append(item)
        rows[cross].update(open=100, low=99.8)
        rows[cross + 1].update(high=110, low=100.99)
        original = market._parse_candles(rows, 3600, end)
        self.assertEqual(evaluate(original)['baseline']['results'][0]['entry_time'], saturday + 3600)
        for key in ('', 'private-test-key'):
            with patch.dict(market.os.environ, {'TWELVE_DATA_API_KEY': key}), patch.object(market.time, 'time', return_value=end), patch.object(market, '_get_series', return_value=(rows, end)):
                snapshot = market._fetch('EURUSD', '1h')
                self.assertGreater(snapshot['excluded_weekend_bars'], 0)
                self.assertFalse(any(market._overlaps_weekend('EURUSD', b['time'], 3600) for b in snapshot['candles']))
                self.assertEqual(evaluate(snapshot['candles'])['baseline']['results'], [])

    def test_weekend_gap_preserved_in_utc_4h_and_daily_chart(self):
        monday = int(datetime(2026, 6, 8, tzinfo=timezone.utc).timestamp())
        end = monday + 8 * 86400
        rows = [row(stamp) for stamp in range(monday, end, 3600)]
        for interval, excluded in [('4h', 13), ('1d', 3)]:
            with self.subTest(interval=interval), patch.dict(market.os.environ, {'TWELVE_DATA_API_KEY': ''}), patch.object(market.time, 'time', return_value=end), patch.object(market, '_get_series', return_value=(rows, end)):
                snapshot = market._fetch('EURUSD', interval)
                seconds = market.INTERVALS[interval]
                self.assertEqual(snapshot['excluded_weekend_bars'], excluded)
                times = [b['time'] for b in snapshot['candles']]
                self.assertTrue(any(b - a > seconds for a, b in zip(times, times[1:])))
                self.assertFalse(any(market._overlaps_weekend('EURUSD', t, seconds) for t in times))

    def test_session_break_weekend_and_summer_time(self):
        def utc(value):
            return int(datetime.fromisoformat(value + '+00:00').timestamp())
        self.assertIsNotNone(market._session_warning('EURUSD', utc('2026-06-12T21:00')))
        self.assertIsNotNone(market._session_warning('XAUUSD', utc('2026-11-22T22:10')))
        self.assertIsNone(market._session_warning('EURUSD', utc('2026-11-22T22:10')))
        self.assertIsNone(market._session_warning('XAUUSD', utc('2026-11-22T23:05')))


if __name__ == '__main__':
    unittest.main()
