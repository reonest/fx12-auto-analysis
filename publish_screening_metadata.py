#!/usr/bin/env python3
"""Publish a price-free screening summary for ChatGPT. No GMO quotes/OHLC."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

FORMAL = ["USD/JPY","EUR/JPY","GBP/JPY","AUD/JPY",
          "EUR/USD","GBP/USD","AUD/USD","USD/CAD",
          "CHF/JPY","NZD/JPY","EUR/AUD","EUR/NZD"]
FRAMES = ("5m","10m","15m","1h","4h")
VALID = {"OK","MARKET_CLOSED"}
MAX_AGE = {"5m":20,"10m":35,"15m":45,"1h":125,"4h":420}

def parse_time(value):
    if not value:
        return None
    try:
        moment=datetime.fromisoformat(value.replace("Z","+00:00"))
        return moment.astimezone(timezone.utc) if moment.tzinfo else None
    except (TypeError,ValueError,AttributeError):
        return None

def valid_bar(bar, frame, generation, declared_market):
    closed=parse_time(bar.get("closed_utc"))
    if not closed or not generation:
        return False
    age=(generation-closed).total_seconds()/60
    if age < -1:
        return False
    if bar.get("status")=="OK":
        return age <= MAX_AGE[frame]
    if bar.get("status")=="MARKET_CLOSED" and declared_market=="CLOSED":
        return age <= 7*24*60
    return False


def build(source):
    pairs = [p for p in source.get("pairs",[]) if p.get("role")=="FORMAL_12"]
    by_name = {p["pair"]:p for p in pairs}
    if len(by_name)!=12 or set(by_name)!=set(FORMAL):
        raise ValueError("Incorrect formal 12-pair set")
    generation=parse_time(source.get("generated_utc"))
    if not generation:
        raise ValueError("Missing or invalid generation timestamp")
    output = []
    for pair in FORMAL:
        p=by_name[pair]
        candles=p.get("frames",{})
        times={frame:{"status":candles.get(frame,{}).get("status","NOT_AVAILABLE"),
                      "closed_utc":candles.get(frame,{}).get("candle_closed_utc")}
               for frame in FRAMES}
        methods=p.get("three_methods",{})
        direct_quality=all(valid_bar(times[t],t,generation,p.get("market_status")) for t in ("5m","10m","1h"))
        synth=p.get("synthetic_screening") if pair=="USD/CAD" else None
        synthetic_last=parse_time(synth.get("last_1h_candle_open_utc")) if isinstance(synth,dict) else None
        synthetic_fresh=(bool(synth and synth.get("no_true_high_low_atr_adx") and
                              synth.get("time_aligned_bars",0)>=90 and synthetic_last and
                              -1 <= (generation-synthetic_last-timedelta(hours=1)).total_seconds()/60 <= 125))
        quality=direct_quality or bool(synthetic_fresh)
        output.append({"pair":pair,"source":p.get("source"),"market_status":p.get("market_status"),
                       "frames":times,"screening_ready":quality,
                       "screening_mode":"CLOSE_ONLY" if synthetic_fresh else "DIRECT_OHLC",
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
