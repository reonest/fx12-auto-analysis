#!/usr/bin/env python3
"""Read-only probe for GMO Coin's FOREIGN EXCHANGE public API.

This is NOT GMO Click Securities, not the crypto API and not an order client.
It does not publish quote prices into GitHub Pages or change Yahoo screening.
Run manually via the workflow before considering production integration.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from analyzer import PAIRS

BASE_URL = "https://forex-api.coin.z.com/public"
JST = timezone(timedelta(hours=9))
TIMEFRAMES = {"5min": 5, "10min": 10, "15min": 15, "1hour": 60}


def trading_date(now):
    """GMO's minute-candle date changes at 06:00 JST, not midnight."""
    return (now.astimezone(JST) - timedelta(hours=6)).strftime("%Y%m%d")


def get_public(session, endpoint, params=None):
    if endpoint not in ("/v1/ticker", "/v1/klines", "/v1/status", "/v1/symbols"):
        raise ValueError("Endpoint is not on the public read-only allowlist")
    response = session.get(BASE_URL + endpoint, params=params, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != 0:
        raise ValueError("GMO public API returned nonzero/invalid status")
    return payload.get("data")


def validate_quote(item):
    try:
        bid, ask = Decimal(item["bid"]), Decimal(item["ask"])
        if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid:
            raise ValueError("Invalid Bid/Ask")
        time_value = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        if time_value.tzinfo is None:
            raise ValueError("Timestamp has no timezone")
        return time_value.astimezone(timezone.utc)
    except (KeyError, InvalidOperation, TypeError, AttributeError) as exc:
        raise ValueError("Invalid quote record") from exc


def quote_coverage(data, now):
    if not isinstance(data, list):
        raise ValueError("Invalid ticker data")
    by_symbol = {x.get("symbol"): x for x in data if isinstance(x, dict)}
    result = []
    for pair in PAIRS:
        symbol = pair.replace("/", "_")
        item = by_symbol.get(symbol)
        if item is None:
            result.append({"pair": pair, "status": "NOT_LISTED"})
            continue
        try:
            stamp = validate_quote(item)
            age = round((now - stamp).total_seconds())
            state = item.get("status")
            if age < -60:
                health = "CLOCK_ERROR"
            elif state != "OPEN":
                health = "MARKET_CLOSED"
            elif age > 300:
                health = "STALE"
            else:
                health = "OK"
            result.append({"pair": pair, "status": health, "feed_status": state,
                           "quote_time_utc": stamp.isoformat(), "quote_age_seconds": age})
        except ValueError:
            result.append({"pair": pair, "status": "INVALID_QUOTE"})
    return result


def candle_check(data, interval_minutes, now):
    """Inspect only closed, complete OHLC bars; NEVER output bar prices."""
    if not isinstance(data, list):
        raise ValueError("Invalid kline data")
    valid = []
    for item in data:
        try:
            stamp = datetime.fromtimestamp(int(item["openTime"]) / 1000, timezone.utc)
            op, high, low, close = [Decimal(item[k]) for k in ("open", "high", "low", "close")]
            if not all(x.is_finite() for x in (op, high, low, close)):
                continue
            if low <= min(op, close) <= max(op, close) <= high and low > 0:
                end = stamp + timedelta(minutes=interval_minutes)
                if end <= now:
                    valid.append(end)
        except (KeyError, ValueError, InvalidOperation, OverflowError, TypeError):
            continue
    valid = sorted(set(valid))
    return {"closed_count": len(valid),
            "last_closed_utc": valid[-1].isoformat() if valid else None,
            "status": "OK" if valid else "NO_CLOSED_CANDLES"}


def main():
    parser = argparse.ArgumentParser(description="Manual GMO Coin FX Public API probe (read-only)")
    parser.add_argument("--out", default="fx12_output/gmo_fx_probe.json")
    parser.add_argument("--pairs", nargs="*", default=["USD/JPY", "EUR/GBP", "MXN/JPY"])
    args = parser.parse_args()
    selected = [pair for pair in args.pairs if pair in PAIRS]
    now = datetime.now(timezone.utc)
    report = {
        "generated_utc": now.isoformat(),
        "source": "GMO Coin FOREIGN EXCHANGE Public API (not GMO Click Securities)",
        "mode": "READ_ONLY_MANUAL_PROBE",
        "live_trading": False,
        "public_redistribution_approved": False,
        "quote_prices_exported": False,
        "gmo_date_jst_6am_cutover": trading_date(now),
        "ticker": [],
        "klines": [],
    }
    with requests.Session() as session:
        session.headers.update({"Accept": "application/json", "User-Agent": "fx12-research/1.0"})
        try:
            report["ticker"] = quote_coverage(get_public(session, "/v1/ticker"), now)
            report["ticker_fetch_status"] = "OK"
        except (requests.RequestException, ValueError) as exc:
            report["ticker_fetch_status"] = "UNAVAILABLE"
            report["ticker_error"] = str(exc)[:150]
        listed = {x["pair"] for x in report["ticker"] if x["status"] != "NOT_LISTED"}
        for pair in selected:
            if pair not in listed:
                report["klines"].append({"pair": pair, "status": "NOT_LISTED_OR_TICKER_UNAVAILABLE"})
                continue
            for interval in ("5min", "10min", "15min"):
                entry = {"pair": pair, "interval": interval, "price_type": "BID"}
                try:
                    data = get_public(session, "/v1/klines", {
                        "symbol": pair.replace("/", "_"), "priceType": "BID",
                        "interval": interval, "date": trading_date(now)})
                    entry.update(candle_check(data, TIMEFRAMES[interval], now))
                except (requests.RequestException, ValueError) as exc:
                    entry.update({"status": "UNAVAILABLE", "error": str(exc)[:150]})
                report["klines"].append(entry)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GMO FX probe:", report.get("ticker_fetch_status"),
          "| ticker pairs:", len(report["ticker"]),
          "| candle checks:", len(report["klines"]))
    print("No quotes published and no orders submitted. Report:", path)


if __name__ == "__main__":
    main()
