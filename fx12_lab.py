#!/usr/bin/env python3
"""FX12 laboratory: GMO-first 12 pairs + AUD/NZD comparison, official-event context.
Read-only research. No orders. Output intended for private CI artifact, not public Pages.
"""
import argparse
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from analyzer import PAIRS, SYMBOLS, fetch as yahoo_fetch, indicators
from dual_source_trial import api_get, parse_gmo_candles
from fx12_events import collect_events

UTC = timezone.utc
JST = ZoneInfo("Asia/Tokyo")
EXTRA = "AUD/NZD"
HELPERS = ("CAD/JPY",)  # internal helper only; NZD/JPY and CHF/JPY are formal targets
SYNTH = {"USD/CAD": ("USD/JPY", "CAD/JPY"),
         "EUR/AUD": ("EUR/JPY", "AUD/JPY"),
         "EUR/NZD": ("EUR/JPY", "NZD/JPY")}
FRAMES = {"5m": ("5min", 5, 90), "10m": ("10min", 10, 90),
          "15m": ("15min", 15, 90), "1h": ("1hour", 60, 90),
          "4h": ("4hour", 240, 80)}
MAX_AGE_OPEN_MIN = {"5m": 20, "10m": 35, "15m": 45, "1h": 125, "4h": 420}


def iso(d):
    return d.astimezone(UTC).isoformat()


def jst(d):
    return d.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def market_clock(now):
    local = now.astimezone(JST)
    hour = local.hour
    if local.weekday() == 6 or (local.weekday() == 5 and hour >= 8) or (local.weekday() == 0 and hour < 7):
        phase = "週末・流動性低下／休場に注意"
    elif 8 <= hour < 10:
        phase = "東京初動"
    elif 10 <= hour < 15:
        phase = "東京日中"
    elif 15 <= hour < 19:
        phase = "欧州移行・ロンドン初動候補"
    elif 19 <= hour < 21:
        phase = "欧州中盤"
    elif 21 <= hour < 24:
        phase = "NY市場・米指標警戒"
    elif hour < 2:
        phase = "ロンドン終盤・欧米重複後半"
    else:
        phase = "流動性低下時間帯"
    london = now.astimezone(ZoneInfo("Europe/London"))
    newyork = now.astimezone(ZoneInfo("America/New_York"))
    return {"jst":jst(now), "phase_reference":phase,
            "london_local":london.strftime("%Y-%m-%d %H:%M %Z"),
            "new_york_local":newyork.strftime("%Y-%m-%d %H:%M %Z"),
            "note":"時間帯だけで売買方向・市場の営業状況は断定しない"}


def number(x, digits=6):
    return round(float(x), digits) if pd.notna(x) and math.isfinite(float(x)) else None


def gmo_dates(now, interval):
    d = (now.astimezone(JST) - timedelta(hours=6)).date()
    if interval == "4h":
        return [str(d.year), str(d.year - 1)]
    return [(d - timedelta(days=i)).strftime("%Y%m%d")
            for i in range(16 if interval == "1h" else 7)]


def fetch_gmo(session, pair, interval, now):
    name, mins, required = FRAMES[interval]
    candles, errors = [], []
    for day in gmo_dates(now, interval):
        try:
            candles.extend(api_get(session, "/v1/klines", {
                "symbol": pair.replace("/", "_"), "priceType": "BID",
                "interval": name, "date": day}))
        except (ValueError, requests.RequestException) as exc:
            errors.append(type(exc).__name__)
        df = parse_gmo_candles(candles, mins, now)
        if len(df) >= required + 5:
            break
        time.sleep(0.10)
    return parse_gmo_candles(candles, mins, now), errors


def drop_session_truncated_higher_bars(higher, five):
    """Remove longer-frame bars nominally ending after the last traded 5m close.

    During weekend closure a broker can publish a nominally closed 4h bar
    containing only an hour of actual session trading; exclude such tail bars.
    This is a conservative quality filter, not reconstruction of tick coverage.
    """
    if higher.empty or five.empty:
        return higher, 0
    session_end = five.index[-1] + pd.Timedelta(minutes=5)
    allowed = higher.index + pd.Timedelta(minutes=240) <= session_end
    dropped = int((~allowed).sum())
    return higher.loc[allowed], dropped


def resample_complete(df, minutes, now):
    if len(df) < 2:
        return df.iloc[:0].copy()
    diffs = df.index.to_series().diff().dropna().dt.total_seconds().div(60)
    step = int(diffs.mode().iloc[0])
    if (minutes in (10, 15) and step != 5) or (minutes == 240 and step != 60):
        return df.iloc[:0].copy()
    group = df.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    out = group.agg({"open": "first", "high": "max",
                     "low": "min", "close": "last"}).dropna()
    out = out[group["close"].count() == minutes // step]
    return out[out.index + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)]


def yahoo_frames(session, pair, now):
    sym = pair.replace("/", "") + "=X"
    five = yahoo_fetch(sym, "5m", "7d", session)
    hour = yahoo_fetch(sym, "60m", "1mo", session)
    five = five[five.index + pd.Timedelta(minutes=5) <= pd.Timestamp(now)]
    hour = hour[hour.index + pd.Timedelta(hours=1) <= pd.Timestamp(now)]
    return {"5m": five, "10m": resample_complete(five, 10, now),
            "15m": resample_complete(five, 15, now), "1h": hour,
            "4h": resample_complete(hour, 240, now)}


def frame_quality(df, label, now, market_open):
    if len(df) < FRAMES[label][2]:
        return "INSUFFICIENT_BARS"
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        return "INVALID_TIME"
    end = df.index[-1].to_pydatetime() + timedelta(minutes=FRAMES[label][1])
    age = (now - end).total_seconds() / 60
    if age < -1:
        return "FUTURE_CANDLE"
    if market_open and age > MAX_AGE_OPEN_MIN[label]:
        return "STALE"
    if not market_open and age > 7 * 24 * 60:
        return "MARKET_CLOSED_TOO_OLD"
    return "OK" if market_open else "MARKET_CLOSED"


def features(df):
    out = indicators(df)
    high, low, close = out.high, out.low, out.close
    change_up, change_down = high.diff(), -low.diff()
    pos = change_up.where((change_up > change_down) & (change_up > 0), 0)
    neg = change_down.where((change_down > change_up) & (change_down > 0), 0)
    prior = close.shift(1)
    tr = pd.concat([high-low, (high-prior).abs(), (low-prior).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    plus = 100 * pos.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr.replace(0, np.nan)
    minus = 100 * neg.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus-minus).abs() / (plus+minus).replace(0, np.nan)
    out["adx14"] = dx.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    out["plus_di14"] = plus
    out["minus_di14"] = minus
    mid, sd = close.rolling(20).mean(), close.rolling(20).std(ddof=0)
    out["bb_upper"] = mid + 2 * sd
    out["bb_lower"] = mid - 2 * sd
    out["bb_width_pct"] = 400 * sd / mid
    out["high20_prev"] = high.shift(1).rolling(20).max()
    out["low20_prev"] = low.shift(1).rolling(20).min()
    return out


def summarize(df, frame, source, now, market_open):
    quality = frame_quality(df, frame, now, market_open)
    if len(df) < 90:
        return {"status": quality, "source": source, "bar_count": len(df)}
    row = features(df).iloc[-1]
    end = df.index[-1].to_pydatetime() + timedelta(minutes=FRAMES[frame][1])
    output = {"status": quality, "source": source, "bar_count": len(df),
              "candle_open_utc": iso(df.index[-1].to_pydatetime()),
              "candle_closed_utc": iso(end), "candle_closed_jst": jst(end)}
    for key in ("close", "atr14", "rsi14", "macd", "macd_signal", "macd_hist",
                "adx14", "plus_di14", "minus_di14", "bb_upper", "bb_lower",
                "bb_width_pct", "high20_prev", "low20_prev", "ma5", "ma25", "ma75"):
        output[key] = number(row[key], 7 if key.startswith("macd") else 6)
    return output


def scenarios(row):
    frames = row["frames"]
    if not all(frames.get(k, {}).get("status") in ("OK", "MARKET_CLOSED")
               for k in ("5m", "10m", "1h")):
        return {"environment": "判定保留", "overall": "中立",
                "trend_breakout": "判定保留", "range_reversal": "判定保留", "score": 0}
    h, t = frames["1h"], frames["10m"]
    close, ma25, ma75 = h.get("close"), h.get("ma25"), h.get("ma75")
    up = all(v is not None for v in (close, ma25, ma75)) and close > ma25 > ma75
    down = all(v is not None for v in (close, ma25, ma75)) and close < ma25 < ma75
    direction = "買い方向" if up else "売り方向" if down else "中立"
    adx, macd = t.get("adx14"), t.get("macd_hist")
    bullish = t.get("close") is not None and t.get("high20_prev") is not None and t["close"] > t["high20_prev"]
    bearish = t.get("close") is not None and t.get("low20_prev") is not None and t["close"] < t["low20_prev"]
    breakout = bullish or bearish
    aligned = (up and macd is not None and macd > 0) or (down and macd is not None and macd < 0)
    environment = ("トレンド発生・加速" if breakout and (adx or 0) >= 20 else
                   "トレンド継続" if (up or down) and (adx or 0) >= 25 else
                   "レンジ・横ばい" if adx is not None and adx < 20 else
                   "トレンド失速・移行期" if (up or down) else "判定保留")
    a = "条件付き候補" if breakout and adx is not None and adx >= 20 else "未成立"
    b = "条件付き候補" if environment == "レンジ・横ばい" and t.get("rsi14") is not None and (t["rsi14"] < 35 or t["rsi14"] > 65) else "未成立"
    return {"environment": environment, "overall": direction, "trend_breakout": a,
            "range_reversal": b, "score": (2 if up or down else 0)+(1 if aligned else 0)+(1 if a=="条件付き候補" else 0),
            "experimental_not_expected_win_probability": True}


def synthetic_checks(pair, gmo_raw):
    if pair not in SYNTH:
        return None
    left, right = SYNTH[pair]
    result = {"pair": pair, "formula": left + " / " + right,
              "high_low_atr_adx_calculation_allowed": False}
    for tf in ("5m", "10m", "15m", "1h"):
        a, b = gmo_raw.get(left, {}).get(tf), gmo_raw.get(right, {}).get(tf)
        if a is None or b is None:
            result[tf] = {"status": "MISSING_GMO_INPUT"}
            continue
        aligned = pd.concat([a.close.rename("a"), b.close.rename("b")], axis=1, join="inner").dropna()
        aligned = aligned[aligned.b > 0]
        if len(aligned) < 90:
            result[tf] = {"status": "INSUFFICIENT_ALIGNED_CLOSE", "count": len(aligned)}
            continue
        ratio = aligned.a/aligned.b
        ema12 = ratio.ewm(span=12, adjust=False, min_periods=26).mean()
        ema26 = ratio.ewm(span=26, adjust=False, min_periods=26).mean()
        macd = ema12-ema26
        signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
        result[tf] = {"status": "OK", "count": len(aligned),
                      "last_open_utc": iso(ratio.index[-1].to_pydatetime()),
                      "close_only_ratio": number(ratio.iloc[-1]),
                      "macd_hist_close_only": number((macd-signal).iloc[-1], 7)}
    return result


def correlations(rows, raw):
    # Research metric only: pairwise 1-hour returns with matching timestamps.
    series = {}
    for pair in rows:
        frames = raw.get(pair, {})
        if "1h" in frames and len(frames["1h"]) >= 65:
            series[pair] = frames["1h"].close.pct_change()
    output = []
    for i, a in enumerate(PAIRS):
        for b in PAIRS[i+1:]:
            if a not in series or b not in series:
                continue
            aligned = pd.concat([series[a], series[b]], axis=1, join="inner").dropna().tail(120)
            if len(aligned) < 60:
                continue
            coefficient = aligned.iloc[:,0].corr(aligned.iloc[:,1])
            if pd.notna(coefficient):
                output.append({"pair_a": a, "pair_b": b,
                               "returns_bars": len(aligned), "correlation": number(coefficient, 3)})
    return sorted(output, key=lambda x: -abs(x["correlation"]))[:30]


def eight_currency_strength(gmo_raw, now):
    """24h relative performance from a common 1h close timestamp across seven GMO JPY crosses.

    Normalizes 8 log returns to an equal-weight eight-currency basket.
    This is a relative observation, NOT tradeable prices and NOT a live quote.
    """
    currencies = ("USD", "JPY", "EUR", "GBP", "AUD", "NZD", "CAD", "CHF")
    cross = {ccy: gmo_raw.get(ccy + "/JPY", {}).get("1h")
             for ccy in currencies if ccy != "JPY"}
    if any(df is None or len(df)<30 for df in cross.values()):
        return {"status": "INSUFFICIENT_GMO_JPY_CROSSES", "currencies": list(currencies)}
    shared = None
    for df in cross.values():
        shared = df.index if shared is None else shared.intersection(df.index)
    shared = shared[shared + pd.Timedelta(hours=1) <= pd.Timestamp(now)]
    if len(shared) < 25:
        return {"status": "NOT_TIME_ALIGNED", "currencies": list(currencies)}
    end = shared[-1]
    start = end - pd.Timedelta(hours=24)
    if start not in shared:
        return {"status": "NO_EXACT_24H_COMMON_REFERENCE", "currencies": list(currencies),
                "latest_open_utc": iso(end.to_pydatetime())}
    raw_changes = {"JPY": 0.0}
    for ccy, df in cross.items():
        a = float(df.loc[start, "close"])
        b = float(df.loc[end, "close"])
        if a <= 0 or b <= 0:
            return {"status":"INVALID_CROSS_RATE"}
        raw_changes[ccy] = math.log(b/a)
    average = sum(raw_changes.values()) / len(currencies)
    strength = [{"currency": ccy,
                 "relative_to_equal_weight_basket_pct": round(100*(math.exp(raw_changes[ccy]-average)-1),4)}
                for ccy in currencies]
    strength.sort(key=lambda item:-item["relative_to_equal_weight_basket_pct"])
    last_close=end.to_pydatetime()+timedelta(hours=1)
    return {"status":"OK_HISTORICAL_RELATIVE_STRENGTH", "currency_count":len(strength),
            "basket":"USD,JPY,EUR,GBP,AUD,NZD,CAD,CHF equal-weight normalization",
            "data_source":"GMO Coin FX JPY crosses 1hour Bid close, all same timestamp",
            "reference_hours":24, "from_close_utc":iso(start.to_pydatetime()+timedelta(hours=1)),
            "to_close_utc":iso(last_close),
            "to_close_jst":jst(last_close),
            "age_minutes_at_generation":round((now-last_close).total_seconds()/60),
            "ranked":strength, "not_a_probability_or_trade_signal":True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="trial_output/fx12_ai_context.json")
    parser.add_argument("--with-news", action="store_true")
    parser.add_argument("--all-pairs", action="store_true", help="Formal 12 + research AUD/NZD + helper CAD/JPY")
    args = parser.parse_args()
    now = datetime.now(UTC)
    pairs = PAIRS + [EXTRA]
    available = list(dict.fromkeys(pairs + list(HELPERS))) if args.all_pairs else ["USD/JPY", "EUR/USD", "EUR/NZD", EXTRA, "CAD/JPY"]
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "fx12-lab/0.1"})
    try:
        ticker = api_get(session, "/v1/ticker")
        ticker = {i["symbol"]: i for i in ticker if isinstance(i, dict) and "symbol" in i}
        ticker_status = "OK"
    except (requests.RequestException, ValueError, KeyError) as exc:
        ticker, ticker_status = {}, "FAILED_" + type(exc).__name__
    raw, sources, info, gmo_raw = {}, {}, {}, {}
    for pair in available:
        symbol = pair.replace("/", "_")
        listed = symbol in ticker
        open_now = ticker.get(symbol, {}).get("status") == "OPEN"
        gmo = {}
        errors = []
        if listed:
            for frame in FRAMES:
                gmo[frame], err = fetch_gmo(session, pair, frame, now)
                if err: errors += [frame + ":" + ",".join(err[:2])]
            if not open_now and "5m" in gmo and "4h" in gmo:
                gmo["4h"], dropped = drop_session_truncated_higher_bars(gmo["4h"], gmo["5m"])
                if dropped:
                    errors.append("SESSION_TRUNCATED_4H_REMOVED:" + str(dropped))
        good = listed and all(frame_quality(gmo[x],x,now,open_now) in ("OK","MARKET_CLOSED") for x in FRAMES)
        source, data = "GMO", gmo
        if not good:
            source = "YAHOO"
            errors.append("GMO_INSUFFICIENT_OR_NOT_LISTED")
            try:
                data = yahoo_frames(session,pair,now)
            except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
                data, source = {}, "UNAVAILABLE"
                errors.append("YAHOO_" + type(exc).__name__)
        raw[pair], sources[pair] = data, source
        if good: gmo_raw[pair] = gmo
        info[pair] = {"source": source, "gmo_listed": listed,
                      "market": ticker.get(symbol, {}).get("status", "UNKNOWN"),
                      "notes": errors}
        print("FETCH",pair,source,"pairs",[(x,len(v)) for x,v in data.items()])
    events = collect_events(session, now) if args.with_news else {
        "generated_utc": iso(now), "events": [], "news": [], "sources": [],
        "calendar_coverage": "NOT_FETCHED", "critical_note": "Must verify at analysis time"}
    rows = []
    for pair in pairs:
        meta = info.get(pair, {"source":"NOT_TESTED", "market":"UNKNOWN", "notes":["PILOT_SUBSET"]})
        data = raw.get(pair, {})
        result = {"pair": pair, "role":"RESEARCH_ONLY" if pair==EXTRA else "FORMAL_12",
                  "source":meta["source"], "market_status":meta["market"],
                  "data_warnings":meta["notes"], "frames":{}}
        for frame in FRAMES:
            result["frames"][frame] = (summarize(data[frame],frame,meta["source"],now,meta["market"]=="OPEN")
                                      if frame in data else {"status":"NOT_AVAILABLE"})
        result["three_methods"]=scenarios(result)
        result["synthetic_close_check"]=synthetic_checks(pair,gmo_raw)
        ccys=pair.split("/")
        result["upcoming_events"]=[e for e in events["events"] if any(c in e.get("currencies",[]) for c in ccys)][:10]
        result["news"]=[n for n in events["news"] if any(c in n.get("currencies",[]) for c in ccys)][:10]
        event_near = []
        for e in result["upcoming_events"]:
            if e.get("importance_hint") != "S":
                continue
            try:
                moment=datetime.fromisoformat(e["scheduled_utc"])
                if 0 <= (moment-now).total_seconds() <= 7200:
                    event_near.append(e["name"])
            except (TypeError,ValueError,KeyError):
                pass
        result["event_risk"]={
            "s_events_within_2h":event_near,
            "calendar_complete":False,
            "must_recheck_official_and_major_calendar_before_order":True}
        result["requires_SBI_screenshot_before_order"]=True
        rows.append(result)
    ranking=sorted((x for x in rows if x["role"]=="FORMAL_12"),
                   key=lambda x:(-x["three_methods"]["score"],x["pair"]))
    # Record unexecuted research signals separately from real and user-defined virtual trades.
    signals=[]
    for r in rows:
        if r["role"] != "FORMAL_12":
            continue
        methods=r["three_methods"]
        for name in ("trend_breakout", "range_reversal"):
            if methods.get(name) != "条件付き候補":
                continue
            signals.append({
                "category":"UNEXECUTED_RESEARCH_SIGNAL", "pair":r["pair"],
                "method":name, "market_status":r["market_status"],
                "generated_utc":iso(now),
                "source":r["source"],
                "last_10m_close_utc":r["frames"]["10m"].get("candle_closed_utc"),
                "direction_reference":methods.get("overall"),
                "environment_reference":methods.get("environment"),
                "entry_order_placed":False, "entry_price_confirmed":False,
                "take_profit_confirmed":False, "stop_loss_confirmed":False,
                "actual_pnl_jpy":None, "outcome":"UNVERIFIED",
                "cannot_infer_fill_sequence_from_ohlc":True})
    signals_path=Path(args.output).parent/"fx12_unexecuted_signals.json"
    signals_path.parent.mkdir(parents=True,exist_ok=True)
    signals_path.write_text(json.dumps({"generated_utc":iso(now),
        "signals":signals,"not_real_trades":True,
        "history_note":"Per-run snapshots only; longitudinal evaluation requires retained runs."
        },ensure_ascii=False,indent=2),encoding="utf-8")
    context={"generated_utc":iso(now), "generated_jst":jst(now),
             "market_time_analysis":market_clock(now),
             "status":"RESEARCH_NOT_ORDER",
             "data_plan":"GMO_COIN_FX_FIRST_YAHOO_FALLBACK", "gmo_ticker_status":ticker_status,
             "formal_pairs":12, "formal_pair_names":list(PAIRS), "pair_set_version":"2026-10-21",
             "research_pair":EXTRA, "extra_helper_pairs":list(HELPERS),
             "eight_currency_strength":eight_currency_strength(gmo_raw,now),
             "pairs":rows,"top_candidates":[{"pair":r["pair"],
               "score_experimental":r["three_methods"]["score"],
               "market_status":r["market_status"], "not_an_order_signal":True,
               "direction":r["three_methods"]["overall"],
               "environment":r["three_methods"]["environment"],
               "source":r["source"]} for r in ranking if r["three_methods"]["score"]>0][:3],
             "hourly_return_correlations":correlations(available,raw),
             "unexecuted_research_signals":signals,
             "currency_strength_note":"Use synchronized GMO JPY crosses incl NZD/JPY and CHF/JPY; mark missing if unavailable.",
             "events_and_news":events,
             "news_alert_policy":"Never mark old/undated headlines as fresh/S-grade. RSS alone not enough for S-grade.",
             "triggers_for_future_instruction":["分析して","FX分析","分析","ふ"],
             "order_inputs_confirmed":False, "orders_placed":False,
             "limitations":["SBI spread/BidAsk/order details require screenshot",
                            "market close must not be mistaken for live data",
                            "non-official news and missing events must be rechecked by GPT",
                            "synthetic closes do not yield real OHLC, ATR, or stop prices"]}
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(context,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print("RESULT",out,"GMO",sum(r["source"]=="GMO" for r in rows),
          "YAHOO",sum(r["source"]=="YAHOO" for r in rows),
          "EVENT_FEEDS_OK",sum(s["status"]=="OK" for s in events["sources"]),
          "EVENT_FEEDS_TOTAL",len(events["sources"]))
    return context


if __name__=="__main__":
    main()
