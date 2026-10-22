#!/usr/bin/env python3
"""Publish a price-free screening summary for ChatGPT. No GMO quotes/OHLC."""
import json
import sys
from pathlib import Path

FORMAL = ["USD/JPY","EUR/JPY","GBP/JPY","AUD/JPY",
          "EUR/USD","GBP/USD","AUD/USD","USD/CAD",
          "CHF/JPY","NZD/JPY","EUR/AUD","EUR/NZD"]
FRAMES = ("5m","10m","15m","1h","4h")
VALID = {"OK","MARKET_CLOSED"}

def build(source):
    pairs = [p for p in source.get("pairs",[]) if p.get("role")=="FORMAL_12"]
    by_name = {p["pair"]:p for p in pairs}
    if len(by_name)!=12 or set(by_name)!=set(FORMAL):
        raise ValueError("Incorrect formal 12-pair set")
    output = []
    for pair in FORMAL:
        p=by_name[pair]
        candles=p.get("frames",{})
        times={frame:{"status":candles.get(frame,{}).get("status","NOT_AVAILABLE"),
                      "closed_utc":candles.get(frame,{}).get("candle_closed_utc")}
               for frame in FRAMES}
        methods=p.get("three_methods",{})
        quality = (all(times[t]["status"] in VALID and times[t]["closed_utc"] for t in ("5m","10m","1h"))
                   or (pair=="USD/CAD" and bool(p.get("synthetic_screening"))))
        output.append({"pair":pair,"source":p.get("source"),"market_status":p.get("market_status"),
                       "frames":times,"screening_ready":quality,
                       "screening_mode":"CLOSE_ONLY" if p.get("synthetic_screening") else "DIRECT_OHLC",
                       "direction":methods.get("overall","中立"),
                       "environment":methods.get("environment","判定保留"),
                       "score_experimental":methods.get("score",0),
                       "trend_breakout":methods.get("trend_breakout"),
                       "range_reversal":methods.get("range_reversal"),
                       "warnings":p.get("data_warnings",[])})
    ranking=sorted((p for p in output if p["screening_ready"]),
                   key=lambda p:(-p["score_experimental"],FORMAL.index(p["pair"])))
    return {"generated_utc":source.get("generated_utc"),
            "pair_set_version":source.get("pair_set_version"),
            "mode":"GMO_PRIMARY_YAHOO_FALLBACK_METADATA_ONLY",
            "public_summary_only":True,"contains_broker_quotes":False,
            "formal_pair_count":len(output),"pairs":output,
            "top_candidates":[{"pair":p["pair"],"direction":p["direction"],
                               "environment":p["environment"],"source":p["source"],
                               "score_experimental":p["score_experimental"]}
                              for p in ranking if p["score_experimental"]>0][:3],
            "source_counts":{s:sum(p["source"]==s for p in output)
                             for s in ("GMO","GMO_SYNTH_CLOSE_ONLY","YAHOO","UNAVAILABLE")},
            "note":"Ranking metadata only; current SBI Bid/Ask and news must be checked before orders."}

def main():
    src=Path(sys.argv[1]); dst=Path(sys.argv[2])
    data=build(json.loads(src.read_text(encoding="utf-8")))
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Published metadata for", data["formal_pair_count"], "formal pairs")

if __name__=="__main__":
    main()
