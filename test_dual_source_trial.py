"""Offline tests: no network, no accounts, no orders."""
import unittest
from datetime import datetime, timedelta, timezone
import pandas as pd
from dual_source_trial import (gmo_dates, parse_gmo_candles,
                               choose_source, bar_quality)


class DualSourceTrialTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 1, 30, tzinfo=timezone.utc)

    def test_six_am_jst_date_cutover(self):
        before = datetime(2026, 10, 16, 20, 30, tzinfo=timezone.utc)  # 05:30 JST Oct 17
        after = datetime(2026, 10, 16, 21, 30, tzinfo=timezone.utc)  # 06:30 JST Oct 17
        self.assertEqual(gmo_dates(before, 1)[0], "20261016")
        self.assertEqual(gmo_dates(after, 1)[0], "20261017")

    def test_latest_unfinished_bar_excluded(self):
        opened = self.now - timedelta(minutes=3)
        completed = self.now - timedelta(minutes=10)
        def bar(t):
            return {"openTime": str(int(t.timestamp() * 1000)),
                    "open": "100", "high": "101", "low": "99", "close": "100.5"}
        df = parse_gmo_candles([bar(opened), bar(completed)], 5, self.now)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.index[-1].to_pydatetime(), completed)

    def test_reject_invalid_ohlc(self):
        old = self.now - timedelta(minutes=10)
        df = parse_gmo_candles([{"openTime": str(int(old.timestamp()*1000)),
                                 "open": "105", "high": "102",
                                 "low": "100", "close": "101"}], 5, self.now)
        self.assertTrue(df.empty)

    def test_missing_gmo_uses_yahoo(self):
        self.assertEqual(choose_source(False, "OK", "OK")[0], "YAHOO")
        self.assertEqual(choose_source(True, "STALE", "OK")[0], "YAHOO")
        self.assertEqual(choose_source(True, "OK", "OK")[0], "GMO")

    def test_open_stale_rejected_closed_retained_for_research(self):
        times = pd.date_range(end=self.now-timedelta(hours=5), periods=100, freq="5min", tz="UTC")
        df = pd.DataFrame({"close": [100.0]*len(times)}, index=times)
        self.assertEqual(bar_quality(df, 5, self.now, "OPEN"), "STALE")
        self.assertEqual(bar_quality(df, 5, self.now, "CLOSE"), "OK")


if __name__ == "__main__":
    unittest.main()
