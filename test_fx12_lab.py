"""Offline regressions for multi-timeframe OHLC and time-sensitive news."""
import json
import unittest
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

from fx12_lab import (EXTRA, FRAMES, PAIRS, resample_complete, frame_quality,
                      features, synthetic_checks, scenarios, summarize)
from fx12_events import parse_rss, parse_bea_json, parse_bls_ics, SOURCES

UTC = timezone.utc


class NewsTimeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 2, 20, tzinfo=UTC)
        self.feed = {"id": "frb_policy", "ccy": ["USD"], "url": "https://www.federalreserve.gov/feeds/press_monetary.xml"}

    def test_old_article_never_breaking_even_if_fetched_now(self):
        rss = b"""<rss><channel><item><title>Rate announcement</title>
        <link>https://www.federalreserve.gov/a</link>
        <pubDate>Fri, 16 Oct 2026 00:00:00 GMT</pubDate></item></channel></rss>"""
        item = parse_rss(rss, self.feed, self.now)[0]
        self.assertEqual(item["freshness"], "ARCHIVE_NOT_BREAKING")
        self.assertFalse(item["breaking_eligible"])
        self.assertEqual(item["s_grade"], "NOT_EVALUATED")
        self.assertGreater(item["publication_age_minutes"], 120)

    def test_undated_article_never_breaking(self):
        rss = b"""<rss><channel><item><title>Rate announcement</title>
        <link>https://www.federalreserve.gov/b</link></item></channel></rss>"""
        item = parse_rss(rss, self.feed, self.now)[0]
        self.assertEqual(item["freshness"], "PUBLICATION_TIME_UNKNOWN")
        self.assertIsNone(item["published_utc"])
        self.assertFalse(item["breaking_eligible"])

    def test_recent_article_not_automatically_s_grade(self):
        rss = b"""<rss><channel><item><title>Rate announcement</title>
        <link>https://www.federalreserve.gov/c</link>
        <pubDate>Sat, 17 Oct 2026 02:15:00 GMT</pubDate></item></channel></rss>"""
        item = parse_rss(rss, self.feed, self.now)[0]
        self.assertTrue(item["breaking_eligible"])
        self.assertEqual(item["s_grade"], "NOT_EVALUATED")
        self.assertEqual(item["publication_age_minutes"], 5)

    def test_rss_relative_link_keeps_original_publication_time(self):
        rss = b"""<rss><channel><item><title>Policy update</title>
        <link>/newsevents/pressreleases/monetary20261015a.htm</link>
        <pubDate>Thu, 15 Oct 2026 02:15:00 GMT</pubDate></item></channel></rss>"""
        item = parse_rss(rss, self.feed, self.now)[0]
        self.assertEqual(item["url"], "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm")
        self.assertFalse(item["breaking_eligible"])
        self.assertEqual(item["freshness"], "ARCHIVE_NOT_BREAKING")

    def test_bea_dst_correct(self):
        src = {"id": "bea", "ccy": ["USD"], "url": "https://apps.bea.gov/API/signup/release_dates.json"}
        data = {"Gross Domestic Product": {"release_dates": ["2026-10-20T12:30:00+00:00"]}}
        item = parse_bea_json(data, src, self.now)[0]
        self.assertEqual(item["scheduled_jst"], "2026-10-20 21:30 JST")

    def test_bls_tz_and_floating_time(self):
        src = {"id": "bls", "ccy": ["USD"], "url": "https://www.bls.gov/schedule/news_release/bls.ics"}
        raw = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one
DTSTART;TZID=America/New_York:20261020T083000
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
UID:two
DTSTART:20261020T083000
SUMMARY:Undated timezone release
END:VEVENT
END:VCALENDAR
"""
        events = parse_bls_ics(raw, src, self.now)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["scheduled_jst"], "2026-10-20 21:30 JST")


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 2, 20, tzinfo=UTC)

    def series(self):
        idx = pd.date_range(start="2026-10-16T08:00:00Z", periods=120, freq="5min")
        prices = np.linspace(150, 151.2, 120)
        return pd.DataFrame({"open": prices, "high": prices+0.03,
                             "low": prices-0.03, "close": prices+0.01}, index=idx)

    def test_ten_fifteen_have_complete_candles(self):
        data = self.series()
        ten = resample_complete(data, 10, self.now)
        self.assertEqual(len(ten), 60)
        fifteen = resample_complete(data, 15, self.now)
        self.assertEqual(len(fifteen), 40)
        missing = data.drop(data.index[3])
        ten2 = resample_complete(missing, 10, self.now)
        self.assertEqual(len(ten2), 59)

    def test_open_stale_vs_weekend_closed(self):
        data=self.series()
        self.assertEqual(frame_quality(data,"5m",self.now,True),"STALE")
        self.assertEqual(frame_quality(data,"5m",self.now,False),"MARKET_CLOSED")

    def test_indicator_not_synthetic_ohlc(self):
        data = self.series()
        feats = features(data)
        self.assertIsNotNone(feats["adx14"].iloc[-1])
        self.assertIsNotNone(feats["bb_upper"].iloc[-1])
        synthetic = synthetic_checks("GBP/AUD", {
            "GBP/JPY": {"5m": data},
            "AUD/JPY": {"5m": data}})
        self.assertFalse(synthetic["high_low_atr_adx_calculation_allowed"])
        self.assertEqual(synthetic["5m"]["status"], "OK")
        self.assertEqual(synthetic["10m"]["status"], "MISSING_GMO_INPUT")

    def test_fixed_twelve_and_additional_research(self):
        self.assertEqual(len(PAIRS), 12)
        self.assertNotIn(EXTRA, PAIRS)
        self.assertEqual(EXTRA, "AUD/NZD")


if __name__ == "__main__":
    unittest.main()
