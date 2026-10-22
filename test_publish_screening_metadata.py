import copy
import unittest
from datetime import datetime, timezone, timedelta
from publish_screening_metadata import FORMAL, build

BASE=datetime(2026,10,23,0,0,tzinfo=timezone.utc)
def iso(minutes):
    return (BASE+timedelta(minutes=minutes)).isoformat()

def sample():
    pairs=[]
    for pair in FORMAL:
        frames={tf:{"status":"OK","candle_closed_utc":iso(-5 if tf=="5m" else -10 if tf=="10m" else -30)}
                for tf in ("5m","10m","15m","1h","4h")}
        pairs.append({"pair":pair,"role":"FORMAL_12","source":"GMO","market_status":"OPEN","frames":frames,
                      "three_methods":{"overall":"売り方向","environment":"トレンド継続","score":3}})
    return {"generated_utc":iso(0),"pair_set_version":"2026-10-21","pairs":pairs}

class MetadataTests(unittest.TestCase):
    def test_all_twelve(self):
        result=build(sample())
        self.assertEqual(result["formal_pair_count"],12)
        self.assertEqual(len(result["pairs"]),12)
        self.assertEqual(len(result["top_candidates"]),3)
        self.assertTrue(all(row["screening_ready"] for row in result["pairs"]))
    def test_reject_stale_5m(self):
        data=sample()
        data["pairs"][0]["frames"]["5m"]["candle_closed_utc"]=iso(-50)
        self.assertFalse(build(data)["pairs"][0]["screening_ready"])
    def test_reject_falsely_closed_yahoo(self):
        data=sample()
        data["pairs"][0]["source"]="YAHOO"
        data["pairs"][0]["market_status"]="UNKNOWN"
        data["pairs"][0]["frames"]["5m"]["status"]="MARKET_CLOSED"
        self.assertFalse(build(data)["pairs"][0]["screening_ready"])
    def test_allow_real_closed(self):
        data=sample()
        data["pairs"][0]["market_status"]="CLOSED"
        data["pairs"][0]["frames"]["5m"]["status"]="MARKET_CLOSED"
        self.assertTrue(build(data)["pairs"][0]["screening_ready"])
    def test_usdcad_synthetic(self):
        data=sample()
        item=data["pairs"][7]
        item["source"]="GMO_SYNTH_CLOSE_ONLY"
        item["frames"]={}
        item["synthetic_screening"]={"no_true_high_low_atr_adx":True,"time_aligned_bars":99,
             "last_1h_candle_open_utc":iso(-90)}
        out=build(data)["pairs"][7]
        self.assertTrue(out["screening_ready"])
        self.assertEqual(out["screening_mode"],"CLOSE_ONLY")
    def test_reject_stale_synthetic(self):
        data=sample()
        item=data["pairs"][7]
        item["source"]="GMO_SYNTH_CLOSE_ONLY"
        item["frames"]={}
        item["synthetic_screening"]={"no_true_high_low_atr_adx":True,"time_aligned_bars":99,
             "last_1h_candle_open_utc":iso(-220)}
        self.assertFalse(build(data)["pairs"][7]["screening_ready"])
    def test_reject_missing_pair(self):
        data=sample()
        data["pairs"].pop()
        with self.assertRaises(ValueError): build(data)
    def test_reject_future_candle(self):
        data=sample()
        data["pairs"][0]["frames"]["5m"]["candle_closed_utc"]=iso(10)
        self.assertFalse(build(data)["pairs"][0]["screening_ready"])

if __name__=="__main__": unittest.main()
