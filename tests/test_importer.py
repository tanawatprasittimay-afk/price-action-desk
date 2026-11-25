import unittest
from datetime import datetime, timezone
from tradelens.importer import read_export


class ExportTest(unittest.TestCase):
    def data(self, n=50):
        start = 1654041600
        return [[start + i * 3600, 1901, 1905, 1900, 1903] for i in range(n)]

    def test_unclosed_last_and_sort(self):
        bars = self.data()
        raw = "time,open,high,low,close\n" + "\n".join(",".join(map(str, r)) for r in reversed(bars))
        response = read_export("XAUUSD", "1h", "UTC", raw, bars[-1][0] + 1)
        self.assertEqual(len(response["candles"]), 49)
        self.assertEqual(response["as_of"], bars[-1][0])
        with self.assertRaisesRegex(ValueError, "กรอบเวลา"):
            read_export("XAUUSD", "15m", "UTC", raw, bars[-1][0]+3600)

    def test_mt5_date_tab_tick_volume(self):
        rows = ["<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"]
        for ts, *prices in self.data():
            stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y.%m.%d\t%H:%M:%S")
            rows.append("\t".join([stamp, *map(str, prices), "42", "0", "12"]))
        market = read_export("XAUUSD", "1h", "Asia/Bangkok", "\n".join(rows))
        self.assertEqual(market["candles"][0]["volume"], 42.0)
        self.assertEqual(market["candles"][0]["time"], self.data()[0][0] - 7*3600)
