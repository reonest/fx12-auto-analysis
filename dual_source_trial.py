#!/usr/bin/env python3
"""GMO-first / Yahoo-fallback FX12 screening trial. Read-only and non-publishing.

GMO Coin foreign-exchange Public API, NOT GMO Click Securities.
This script never calls Private API endpoints or places orders.
It intentionally outputs source/quality/signal diagnostics, not quote prices.
The existing analyzer.py, site.py, main branch and Pages remain unchanged.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from analyzer import PAIRS, SYMBOLS, fetch as yahoo_fetch, summary as screen_summary

BASE = "https://forex-api.coin.z.com/public"
JST = timezone(timedelta(hours=9))
MIN_BARS = 80
ALLOWED = ("/v1/ticker", "/v1/klines")


def api_get(session, path, params=None):
    if path not in ALLOWED:
        raise ValueError("Only anonymous foreign-exchange public endpoints allowed")
    response = session.get(BASE + path, params=params, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != 0:
        raise ValueError("Invalid or unsuccessful GMO response")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("Missing GMO data array")
    return data


def gmo_dates(now, count):
    # Official GMO FX day rollover is 06:00 JST, NOT 00:00.
    start = (now.astimezone(JST) - timedelta(hours=6)).date()
    return [(start - timedelta(days=i)).strftime("%Y%m%d") for i in range(count)]


def parse_gmo_candles(candles, interval_minutes, now):
    rows = []
    for item in candles:
        try:
            stamp = pd.to_datetime(int(item["openTime"]), unit="ms", utc=True)
            end = stamp + pd.Timedelta(minutes=interval_minutes)
            if end.to_pydatetime() > now:
                continue
            prices = [float(item[k]) for k in ("open", "high", "low", "close")]
            op, hi, lo, cl = prices
            if (not all(pd.notna(p) and p > 0 for p in prices) or
                    not (lo <= min(op, cl) <= max(op, cl) <= hi)):
                continue
            rows.append((stamp, *prices))
        except (KeyError, ValueError, OverflowError, TypeError):
            continue
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    out = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    return out.drop_duplicates("time", keep="last").set_index("time").sort_index()


def get_gmo_history(session, pair, interval, days, now):
    interval_minutes = {"5min": 5, "1hour": 60}[interval]
    candles = []
    errors = []
    for day in gmo_dates(now, days):
        try:
            candles.extend(api_get(session, "/v1/klines", {
                "symbol": pair.replace("/", "_"),
                "priceType": "BID",
                "interval": interval,
                "date": day}))
        except (ValueError, requests.RequestException) as exc:
            errors.append(str(exc)[:90])
        time.sleep(0.10)
    return parse_gmo_candles(candles, interval_minutes, now), errors


def bar_quality(df, minutes, now, feed_status):
    if len(df) < MIN_BARS:
        return "INSUFFICIENT_BARS"
    last_end = df.index[-1].to_pydatetime() + timedelta(minutes=minutes)
    age_minutes = (now - last_end).total_seconds() / 60
    if age_minutes < -1:
        return "CLOCK_ANOMALY"
    if feed_status == "OPEN" and age_minutes > (20 if minutes == 5 else 120):
        return "STALE"
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        return "INVALID_HISTORY"
    return "OK"


def choose_source(gmo_listed, five_quality, hour_quality):
    if not gmo_listed:
        return "YAHOO", "GMO_NOT_LISTED"
    if five_quality != "OK":
        return "YAHOO", "GMO_5M_" + five_quality
    if hour_quality != "OK":
        return "YAHOO", "GMO_1H_" + hour_quality
    return "GMO", "GMO_5M_AND_1H_VALID"


def trial_one(pair, ticker, session, now):
    symbol = pair.replace("/", "_")
    listed = symbol in ticker
    status = ticker.get(symbol, {}).get("status", "UNKNOWN")
    five = hourly = None
    q5 = qh = "NOT_LISTED"
    if listed:
        five, _ = get_gmo_history(session, pair, "5min", 3, now)
        hourly, _ = get_gmo_history(session, pair, "1hour", 12, now)
        q5 = bar_quality(five, 5, now, status)
        qh = bar_quality(hourly, 60, now, status)
    selected, reason = choose_source(listed, q5, qh)
    if selected == "YAHOO":
        try:
            five = yahoo_fetch(SYMBOLS[pair], "5m", "7d", session)
            hourly = yahoo_fetch(SYMBOLS[pair], "60m", "1mo", session)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            return {"pair": pair, "source_used": "NONE",
                    "quality": "UNAVAILABLE", "fallback_reason": reason,
                    "error_type": type(exc).__name__}
    try:
        score = screen_summary(pair, five, hourly)
        return {"pair": pair, "source_used": selected, "quality": "RESEARCH_ONLY",
                "market_status": status, "gmo_5m_quality": q5, "gmo_1h_quality": qh,
                "fallback_reason": reason if selected == "YAHOO" else None,
                "trend_1h": score["trend_1h"], "screening_score": score["screening_score"],
                "five_min_closed_utc": (five.index[-1] + pd.Timedelta(minutes=5)).isoformat(),
                "hour_closed_utc": (hourly.index[-1] + pd.Timedelta(hours=1)).isoformat()}
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return {"pair": pair, "source_used": selected,
                "quality": "UNAVAILABLE", "error_type": type(exc).__name__}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pairs", type=int, default=3,
                        help="Default samples 3 pairs; use 12 to test all pairs")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    selected = PAIRS[:max(1, min(12, args.max_pairs))]
    # Include MXN/JPY in the initial three-pair pilot.
    if args.max_pairs == 3:
        selected = ["USD/JPY", "EUR/USD", "MXN/JPY"]
    with requests.Session() as session:
        session.headers.update({"Accept": "application/json",
                                "User-Agent": "fx12-research/1.0"})
        try:
            ticker_raw = api_get(session, "/v1/ticker")
            ticker = {x.get("symbol"): x for x in ticker_raw if isinstance(x, dict)}
            ticker_status = "OK"
        except (requests.RequestException, ValueError) as exc:
            ticker = {}
            ticker_status = "UNAVAILABLE_" + type(exc).__name__
        rows = [trial_one(pair, ticker, session, now) for pair in selected]
    result = {"generated_utc": now.isoformat(),
              "mode": "GMO_PRIMARY_YAHOO_FALLBACK_READ_ONLY_TRIAL",
              "gmo_provider": "GMO Coin FX, not GMO Click Securities",
              "gmo_ticker_status": ticker_status,
              "tested_pairs": len(rows),
              "results": rows,
              "note": "No quote-price redistribution. Not real-time broker execution prices."}
    # Output only for CI logs; never commit GMO candles / prices to docs/ or main.
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
