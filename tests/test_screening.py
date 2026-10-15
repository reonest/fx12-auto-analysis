"""Regression tests for supplemental FX screening fields (no network calls)."""\n
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import analyzer

class ScreeningTests(unittest.TestCase):
    def frames(self, kind):
        times5 = pd.date_range("2026-10-01", periods=100, freq="5min", tz="UTC")
        times1 = pd.date_range("2026-09-01", periods=100, freq="1h", tz="UTC")
        def make(times, values):
            a = np.asarray(values, dtype=float)
            return pd.DataFrame({"open":a, "high":a+0.02, "low":a-0.02, "close":a}, index=times)
        if kind == "up":
            v5 = np.linspace(1, 2, 100)
            v5[-1] = 2.5
            v1 = np.linspace(1, 2, 100)
        elif kind == "down":
            v5 = np.linspace(2, 1, 100)
            v5[-1] = 0.5
            v1 = np.linspace(2, 1, 100)
        else:
            v5 = np.full(100, 1.5)
            v1 = np.full(100, 1.5)
        return make(times5,v5), make(times1,v1)

    def test_up_breakout(self):
        f,h=self.frames("up")
        with patch("analyzer.pd.Timestamp.now", return_value=pd.Timestamp("2026-10-20",tz="UTC")):
            result=analyzer.summary("USD/JPY",f,h)
        self.assertEqual(result["status"],"OK")
        self.assertEqual(result["ma_alignment_1h"],"上昇")
        self.assertEqual(result["breakout_5m"],"上抜け")
        self.assertEqual(result["previous_20_high_5m"],round(float(f.iloc[-21:-1].high.max()),6))
        self.assertEqual(result["screening_score"],3)

    def test_down_breakout(self):
        f,h=self.frames("down")
        with patch("analyzer.pd.Timestamp.now", return_value=pd.Timestamp("2026-10-20",tz="UTC")):
            result=analyzer.summary("USD/JPY",f,h)
        self.assertEqual(result["ma_alignment_1h"],"下降")
        self.assertEqual(result["breakout_5m"],"下抜け")
        self.assertEqual(result["previous_20_low_5m"],round(float(f.iloc[-21:-1].low.min()),6))
        self.assertEqual(result["screening_score"],3)

    def test_no_breakout(self):
        f,h=self.frames("flat")
        with patch("analyzer.pd.Timestamp.now", return_value=pd.Timestamp("2026-10-20",tz="UTC")):
            result=analyzer.summary("USD/JPY",f,h)
        self.assertEqual(result["breakout_5m"],"なし")
        self.assertEqual(result["ma_alignment_1h"],"混在")
        self.assertEqual(result["screening_score"],0)

if __name__=="__main__":
    unittest.main()
