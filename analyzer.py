#!/usr/bin/env python3
"""Yahoo Finance 12-pair FX screening (unofficial, research only).
Usage: pip install requests pandas numpy; python yahoo_fx12_analyzer.py
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

# Formal twelve, effective 2026-10-21. Keep screen order aligned with SBI 4-up charts.
FORMAL_PAIRSET_VERSION = "2026-10-21"
PAIRS = ["USD/JPY","EUR/JPY","GBP/JPY","AUD/JPY","EUR/USD","GBP/USD","AUD/USD","USD/CAD","CHF/JPY","NZD/JPY","EUR/AUD","EUR/NZD"]
SYMBOLS = {pair: pair.replace('/', '') + '=X' for pair in PAIRS}


def fetch(symbol, interval, period, session):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    resp = session.get(url, params={"interval": interval, "range": period}, timeout=18)
    resp.raise_for_status()
    body = resp.json().get('chart', {})
    if body.get('error'):
        raise ValueError(str(body['error']))
    results = body.get('result') or []
    if not results:
        raise ValueError('No Yahoo Finance data')
    result = results[0]
    timestamps = result.get('timestamp') or []
    quotes = result['indicators']['quote'][0]
    df = pd.DataFrame({k: quotes.get(k, []) for k in ['open', 'high', 'low', 'close']}, index=pd.to_datetime(timestamps, unit='s', utc=True))
    df = df.apply(pd.to_numeric, errors='coerce').dropna()
    df = df[~df.index.duplicated(keep='last')].sort_index()
    if df.empty:
        raise ValueError('No complete OHLC bars')
    return df


def indicators(df):
    out = df.copy()
    c, h, l = out.close, out.high, out.low
    prev = c.shift(1)
    tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    out['atr14'] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    change = c.diff()
    gain = change.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    out['rsi14'] = (100 - 100/(1+rs)).where(loss.ne(0), 100)
    out.loc[(gain == 0) & (loss == 0), 'rsi14'] = 50
    macd = c.ewm(span=12, adjust=False, min_periods=26).mean() - c.ewm(span=26, adjust=False, min_periods=26).mean()
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    out['macd'] = macd
    out['macd_signal'] = signal
    out['macd_hist'] = macd - signal
    for n in (5, 25, 75):
        out[f'ma{n}'] = c.rolling(n).mean()
    return out


def summary(pair, five, hourly):
    # Only fully closed candles; current live candle may be incomplete.
    five = five[five.index + pd.Timedelta(minutes=5) <= pd.Timestamp.now(tz='UTC')]
    hourly = hourly[hourly.index + pd.Timedelta(hours=1) <= pd.Timestamp.now(tz='UTC')]
    if len(five) < 80 or len(hourly) < 80:
        raise ValueError(f'Insufficient closed candles (5m={len(five)}, 1h={len(hourly)})')
    f, h = indicators(five), indicators(hourly)
    a, b = f.iloc[-1], h.iloc[-1]
    if pd.isna(a.atr14) or pd.isna(b.ma75):
        raise ValueError('Indicators not ready')
    direction = '上昇' if b.close > b.ma25 > b.ma75 else '下降' if b.close < b.ma25 < b.ma75 else '混在'
    momentum = '上向き' if a.macd_hist > 0 else '下向き'
    atr_pct = float(a.atr14 / a.close * 100)
    # Supplementary signals: do not change the legacy screening score.
    # A rising market can be classified as '混在' by the legacy MA25/MA75 rule.
    ma_alignment_1h = ('上昇' if b.ma5 > b.ma25 > b.ma75 else
                       '下降' if b.ma5 < b.ma25 < b.ma75 else '混在')
    previous_20 = f.iloc[-21:-1]
    high_20 = float(previous_20.high.max())
    low_20 = float(previous_20.low.min())
    breakout_5m = ('上抜け' if a.close > high_20 else
                   '下抜け' if a.close < low_20 else 'なし')
    score = (2 if direction != '混在' else 0) + (1 if (direction == '上昇' and momentum == '上向き') or (direction == '下降' and momentum == '下向き') else 0)
    return {
        'pair': pair, 'yahoo_symbol': SYMBOLS[pair], 'status': 'OK',
        'five_min_closed_utc': f.index[-1].isoformat(), 'hour_closed_utc': h.index[-1].isoformat(),
        'close_5m': round(float(a.close), 6), 'close_1h': round(float(b.close), 6),
        'atr14_5m': round(float(a.atr14), 6), 'atr_pct_5m': round(atr_pct, 4),
        'rsi14_5m': round(float(a.rsi14), 2),
        'macd_hist_5m': round(float(a.macd_hist), 7),
        'ma5_1h': round(float(b.ma5), 6), 'ma25_1h': round(float(b.ma25), 6),
        'ma75_1h': round(float(b.ma75), 6),
        'trend_1h': direction, 'momentum_5m': momentum, 'screening_score': score,
        'ma_alignment_1h': ma_alignment_1h,
        'previous_20_high_5m': round(high_20, 6),
        'previous_20_low_5m': round(low_20, 6),
        'breakout_5m': breakout_5m,
        'note': '候補選考のみ。SBI実勢Bid/Ask、注文仕様、指標、SL/TP未検証。'
    }


def main():
    parser = argparse.ArgumentParser(description='Yahoo Finance FX 12-pair screening')
    parser.add_argument('--out', default='fx12_output', help='output directory')
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 FX-research/1.0', 'Accept': 'application/json'})
    results = []
    for pair in PAIRS:
        try:
            five = fetch(SYMBOLS[pair], '5m', '7d', session)
            hourly = fetch(SYMBOLS[pair], '60m', '1mo', session)
            results.append(summary(pair, five, hourly))
            print(f'OK  {pair}')
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            results.append({'pair': pair, 'yahoo_symbol': SYMBOLS[pair], 'status': 'UNAVAILABLE', 'reason': str(exc)[:180]})
            print(f'NG  {pair}: {exc}')
        time.sleep(0.25)
    df = pd.DataFrame(results)
    df['rank_score'] = pd.to_numeric(df.get('screening_score'), errors='coerce')
    df = df.sort_values('rank_score', ascending=False, na_position='last').drop(columns='rank_score')
    df.to_csv(output/'fx12_screening.csv', index=False, encoding='utf-8-sig')
    with (output/'fx12_screening.json').open('w', encoding='utf-8') as fh:
        json.dump({'generated_utc': datetime.now(timezone.utc).isoformat(), 'data_source': 'Yahoo Finance unofficial chart endpoint', 'pair_set_version': FORMAL_PAIRSET_VERSION, 'formal_pairs': PAIRS, 'results': results}, fh, ensure_ascii=False, indent=2)
    print(f'\nSaved: {output / "fx12_screening.csv"}')
    print(f'Success: {sum(x["status"] == "OK" for x in results)}/12')
    print('Research only. No live broker spread, no order execution.')

if __name__ == '__main__':
    main()
