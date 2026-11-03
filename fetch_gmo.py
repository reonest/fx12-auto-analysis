#!/usr/bin/env python3
"""GMOコイン 外国為替FX 公開APIから12ペア分のデータを取得し、data/ に保存する。

出力:
  data/latest.json            … 更新時刻・市場状態・Bid/Ask・指標の要約(小さい。まずこれを読む)
  data/klines/<PAIR>_<足>.json … ローソク足(BID、直近 N 本)

外部ライブラリ不要(Python 3.9+ 標準ライブラリのみ)。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://forex-api.coin.z.com/public"
JST = timezone(timedelta(hours=9))
OUT = "data"

# GMOで直接取れるペア(CAD_JPYは換算用)
DIRECT = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CHF_JPY", "NZD_JPY",
          "EUR_USD", "GBP_USD", "AUD_USD", "CAD_JPY"]
# GMOにないペアは円クロスから換算(通貨強弱を見る参考値)
CONVERTED = {"USD_CAD": ("USD_JPY", "CAD_JPY"),
             "EUR_AUD": ("EUR_JPY", "AUD_JPY"),
             "EUR_NZD": ("EUR_JPY", "NZD_JPY")}
# 足ごとに、何日分さかのぼって取るか / 何本残すか
INTERVALS = {"5min": (1, 120), "15min": (2, 120), "1hour": (6, 120)}


def get(path, retries=2):
    url = BASE + path
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fx12-fetch"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode())
            if body.get("status") == 0:
                return body.get("data")
            return None  # 未来日付など、APIがエラーを返したとき
        except Exception as e:  # 通信エラーは少し待って再試行
            if i == retries:
                print(f"WARN {path}: {e}", file=sys.stderr)
                return None
            time.sleep(1.5)


def klines(symbol, interval, days, keep):
    bars = {}
    today = datetime.now(JST).date()
    for d in range(days, -1, -1):  # 古い日付→今日(GMOの日付は朝6時切り替え)
        date = (today - timedelta(days=d)).strftime("%Y%m%d")
        data = get(f"/v1/klines?symbol={symbol}&priceType=BID&interval={interval}&date={date}")
        for b in data or []:
            bars[int(b["openTime"])] = b
        time.sleep(0.1)
    out = []
    for t in sorted(bars)[-keep:]:
        b = bars[t]
        out.append({"t": datetime.fromtimestamp(t / 1000, JST).strftime("%m-%d %H:%M"),
                    "o": float(b["open"]), "h": float(b["high"]),
                    "l": float(b["low"]), "c": float(b["close"])})
    return out


def ema(vals, n):
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"]))
           for p, b in zip(bars, bars[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def boll(closes, n=20, k=2):
    if len(closes) < n:
        return None
    w = closes[-n:]
    m = sum(w) / n
    sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
    return {"mid": m, "up": m + k * sd, "dn": m - k * sd}


def summarize(bars, digits):
    if not bars:
        return None
    c = [b["c"] for b in bars]
    r = lambda x: None if x is None else round(x, digits + 1)
    bb = boll(c)
    return {"last_bar": bars[-1]["t"], "close": c[-1],
            "ema20": r(ema(c, 20)), "ema50": r(ema(c, 50)), "atr14": r(atr(bars)),
            "bb20": {k: r(v) for k, v in bb.items()} if bb else None,
            "high20": max(b["h"] for b in bars[-20:]),
            "low20": min(b["l"] for b in bars[-20:])}


def digits_of(pair):
    return 3 if pair.endswith("JPY") else 5


def main():
    os.makedirs(f"{OUT}/klines", exist_ok=True)
    now = datetime.now(JST)
    status = (get("/v1/status") or {}).get("status", "UNKNOWN")
    ticker = {t["symbol"]: t for t in (get("/v1/ticker") or [])}

    result = {"updated_at_jst": now.strftime("%Y-%m-%d %H:%M:%S"),
              "updated_at_unix": int(now.timestamp()),
              "market_status": status,
              "source": "GMOコイン外国為替FX 公開API(BID)。SBIの実勢ではない。注文価格はSBIで決める",
              "pairs": {}}

    if status != "OPEN":
        print(f"market_status={status}: 価格は更新しない")
    raw = {}
    for sym in DIRECT:
        t = ticker.get(sym, {})
        entry = {"bid": t.get("bid"), "ask": t.get("ask"), "ticker_time": t.get("timestamp")}
        if status == "OPEN":
            for iv, (days, keep) in INTERVALS.items():
                bars = klines(sym, iv, days, keep)
                raw[(sym, iv)] = bars
                with open(f"{OUT}/klines/{sym}_{iv}.json", "w") as f:
                    json.dump(bars, f, separators=(",", ":"))
                entry[iv] = summarize(bars, digits_of(sym))
        result["pairs"][sym] = entry

    # 換算ペア(終値ベースの概算。高安は正確ではない)
    for pair, (a, b) in CONVERTED.items():
        ta, tb = ticker.get(a, {}), ticker.get(b, {})
        entry = {"converted_from": f"{a} / {b}", "note": "参考値(通貨強弱用)。直接の売買判断には使わない"}
        try:
            entry["mid"] = round((float(ta["bid"]) + float(ta["ask"])) / (float(tb["bid"]) + float(tb["ask"])), 5)
        except (KeyError, TypeError, ValueError):
            entry["mid"] = None
        for iv in INTERVALS:
            ba = {x["t"]: x["c"] for x in raw.get((a, iv), [])}
            bb_ = {x["t"]: x["c"] for x in raw.get((b, iv), [])}
            closes = [round(ba[t] / bb_[t], 5) for t in ba if t in bb_]
            if closes:
                entry[iv] = {"close": closes[-1], "ema20": round(ema(closes, 20), 5) if ema(closes, 20) else None,
                             "change_20bars": round(closes[-1] - closes[-21], 5) if len(closes) > 20 else None}
        result["pairs"][pair] = entry

    with open(f"{OUT}/latest.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("saved", result["updated_at_jst"], status)


if __name__ == "__main__":
    main()
