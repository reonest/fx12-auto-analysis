#!/usr/bin/env python3
"""FX12 3型スコアリング（第1段階・試作）
A=標準(順張り押し目) B=トレンド/ブレイク C=バンド(レンジ逆張り)
データ: GMOコイン外国為替FX 公開API (BID)。出来高なし。
"""
import json, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
import numpy as np, pandas as pd
from fx12_continuation import signal_at

J = timezone(timedelta(hours=9))
API = "https://forex-api.coin.z.com/public/v1"
DIRECT = ["USD_JPY","EUR_JPY","GBP_JPY","AUD_JPY","EUR_USD","GBP_USD","AUD_USD","CHF_JPY","NZD_JPY","CAD_JPY"]
FORMAL = ["USD_JPY","EUR_JPY","GBP_JPY","AUD_JPY","EUR_USD","GBP_USD","AUD_USD","USD_CAD","CHF_JPY","NZD_JPY","EUR_AUD","EUR_NZD"]
SYN = {"USD_CAD":("USD_JPY","CAD_JPY"),"EUR_AUD":("EUR_JPY","AUD_JPY"),"EUR_NZD":("EUR_JPY","NZD_JPY")}

# ---- 実行時設定（毎回更新） ----
CFG = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
EVENTS = [datetime.fromisoformat(e) for e in CFG.get("events", [])]          # JST ISO
EXPOSURE = CFG.get("exposure", {})   # 例 {"USD":-1,"JPY":1,"GBP":1}  +1=買い方向に保有

def get(path):
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(API + path, timeout=15))
        except Exception:
            time.sleep(1)
    return {}

def kl(sym, iv, dates):
    rows = []
    for d in dates:
        rows += get(f"/klines?symbol={sym}&priceType=BID&interval={iv}&date={d}").get("data", [])
    if not rows: return None
    df = pd.DataFrame(rows).astype(float)
    df["t"] = pd.to_datetime(df.openTime, unit="ms", utc=True).dt.tz_convert(J)
    return df.drop_duplicates("t").set_index("t")[["open","high","low","close"]].sort_index()

def tradedays(now, n):
    d, out = now, []
    while len(out) < n:
        dd = d - timedelta(hours=6)          # 日付は6:00切替
        if dd.weekday() < 5 or (dd.weekday() == 5 and d.hour < 7):
            s = dd.strftime("%Y%m%d")
            if s not in out: out.append(s)
        d -= timedelta(days=1)
    return sorted(out)

# ---- 指標 ----
def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([df.high-df.low, (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()
def rsi(c, n=14):
    d = c.diff(); g = d.clip(lower=0).rolling(n).mean(); l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1+g/l)
def macd(c):
    m = c.ewm(span=12).mean() - c.ewm(span=26).mean(); s = m.ewm(span=9).mean(); return m, s, m-s
def boll(c, n=20, k=2):
    m = c.rolling(n).mean(); sd = c.rolling(n).std(); return m, m+k*sd, m-k*sd
def keltner(df, n=20, k=1.5):
    m = df.close.ewm(span=n).mean(); a = atr(df, n); return m+k*a, m-k*a
def adx(df, n=14):
    up = df.high.diff(); dn = -df.low.diff()
    pdm = np.where((up>dn)&(up>0), up, 0.0); ndm = np.where((dn>up)&(dn>0), dn, 0.0)
    a = atr(df, n)
    pdi = 100*pd.Series(pdm, df.index).rolling(n).mean()/a; ndi = 100*pd.Series(ndm, df.index).rolling(n).mean()/a
    dx = 100*(pdi-ndi).abs()/(pdi+ndi); return dx.rolling(n).mean(), pdi, ndi
def ichimoku(df):
    tk = (df.high.rolling(9).max()+df.low.rolling(9).min())/2
    kj = (df.high.rolling(26).max()+df.low.rolling(26).min())/2
    sa = ((tk+kj)/2).shift(26); sb = ((df.high.rolling(52).max()+df.low.rolling(52).min())/2).shift(26)
    return tk, kj, sa, sb

def session(now):
    h = now.hour + now.minute/60
    if 8 <= h < 15: return "TOKYO"
    if 15 <= h < 21.5: return "LONDON"
    if h >= 21.5 or h < 2: return "NY"
    return "LATE"

def round_levels(p, jpy):
    step = 0.5 if jpy else 0.005
    b = np.floor(p/step)*step
    return [b-step, b, b+step, b+2*step]

# ---- データ取得 ----
def load(now):
    d5 = tradedays(now, 3); d1h = tradedays(now, 8); yr = [str(now.year)]
    if now.month == 1: yr = [str(now.year-1)] + yr
    data = {}
    for s in DIRECT:
        data[s] = {"m5": kl(s,"5min",d5[-2:]), "m15": kl(s,"15min",d5), "h1": kl(s,"1hour",d1h),
                   "h4": kl(s,"4hour",yr), "d1": kl(s,"1day",yr)}
    for s,(a,b) in SYN.items():   # 換算：終値比のみ（高安は近似）
        data[s] = {}
        for tf in ["m5","m15","h1","h4","d1"]:
            A, B = data[a][tf], data[b][tf]
            if A is None or B is None: data[s][tf] = None; continue
            idx = A.index.intersection(B.index); c = A.close[idx]/B.close[idx]
            data[s][tf] = pd.DataFrame({"open":(A.open[idx]/B.open[idx]),"high":np.maximum(c, A.open[idx]/B.open[idx]),
                                        "low":np.minimum(c, A.open[idx]/B.open[idx]),"close":c})
    tick = {d["symbol"]: d for d in get("/ticker").get("data", [])}
    return data, tick

def strength(data):
    X = {"USD":"USD_JPY","EUR":"EUR_JPY","GBP":"GBP_JPY","AUD":"AUD_JPY","NZD":"NZD_JPY","CAD":"CAD_JPY","CHF":"CHF_JPY"}
    out = {}
    for w,n in [("24h",24),("6h",6)]:
        r = {k:(data[v]["h1"].close.iloc[-1]/data[v]["h1"].close.iloc[-1-n]-1)*100 for k,v in X.items()}; r["JPY"] = 0.0
        m = np.mean(list(r.values())); out[w] = {k:v-m for k,v in r.items()}
    return out

# ---- スコア ----
def score_pair(sym, D, tick, st, now):
    syn = sym in SYN; jpy = sym.endswith("JPY")
    h1, h4, m5, m15, d1 = D["h1"], D["h4"], D["m5"], D["m15"], D["d1"]
    c1 = h1.close; px = c1.iloc[-1]
    a1 = atr(h1).iloc[-1]; a5 = atr(m5).iloc[-1]
    ma5, ma20, ma25, ma75 = [c1.rolling(n).mean() for n in (5,20,25,75)]
    h4ma25, h4ma75 = h4.close.rolling(25).mean(), h4.close.rolling(75).mean()
    tk, kj, sa, sb = ichimoku(h1); ctop = max(sa.iloc[-1], sb.iloc[-1]); cbot = min(sa.iloc[-1], sb.iloc[-1])
    ad, pdi, ndi = adx(h1)
    # トレンド方向
    up1 = ma5.iloc[-1] > ma25.iloc[-1] > ma75.iloc[-1] and ma25.iloc[-1] > ma25.iloc[-4]
    dn1 = ma5.iloc[-1] < ma25.iloc[-1] < ma75.iloc[-1] and ma25.iloc[-1] < ma25.iloc[-4]
    up4 = h4ma25.iloc[-1] > h4ma75.iloc[-1]; dn4 = not up4
    trend = 1 if up1 else -1 if dn1 else 0
    base, quote = sym.split("_")
    sdiff = {w: st[w][base]-st[w][quote] for w in st}
    sess = session(now)
    # 節目
    pd_ = d1.iloc[-2] if d1.index[-1].date() == (now - timedelta(hours=6)).date() or len(d1) > 1 else d1.iloc[-1]
    P = (pd_.high+pd_.low+pd_.close)/3
    levels = sorted(set([pd_.high, pd_.low, P, 2*P-pd_.low, 2*P-pd_.high] + round_levels(px, jpy)))
    def next_level(direction):
        c = [l for l in levels if (l > px + 0.1*a1 if direction>0 else l < px - 0.1*a1)]
        return (min(c) if direction>0 else max(c)) if c else None
    # 東京レンジ
    td = (now - timedelta(hours=6)).date()
    tok = m5[(m5.index.date == td) & (m5.index.hour >= 9) & (m5.index.hour < 15)]
    tH, tL = (tok.high.max(), tok.low.min()) if len(tok) else (None, None)

    res = {}
    # ---------- A ----------
    if trend != 0:
        d = trend; s = 0; why = []
        above_cloud = px > ctop if d>0 else px < cbot
        kj_ok = (kj.iloc[-1] >= kj.iloc[-4]) if d>0 else (kj.iloc[-1] <= kj.iloc[-4])
        s += 10 + (8 if (up4 if d>0 else dn4) else 0) + (4 if above_cloud else 0) + (3 if kj_ok else 0); why.append("上位足一致" if (up4 if d>0 else dn4) else "4h不一致")
        dist = abs(px - ma25.iloc[-1]) / a1
        s += 20 if 0.3 <= dist <= 1.0 and (px-ma25.iloc[-1])*d > 0 else 10 if dist < 0.3 else 5 if dist <= 1.5 else 0
        why.append(f"MA25乖離{dist:.1f}ATR")
        m5ma = m5.close.rolling(20).mean(); _,_,h5 = macd(m5.close)
        rev = ((m5.close.iloc[-1]-m5ma.iloc[-1])*d > 0) + ((h5.iloc[-1]-h5.iloc[-2])*d > 0) + ((m5.close.iloc[-1]-m5.open.iloc[-1])*d > 0)
        rev = int(rev); s += [0,7,14,20][rev]; why.append(f"5m反転{rev}/3")
        sd = sdiff["24h"]*d; s += 15 if sd > 0.4 else 10 if sd > 0.15 else 5 if sd > 0 else 0
        nl = next_level(d); room = abs(nl-px)/a1 if nl else 2
        s += 10 if room >= 1.5 else 5 if room >= 0.8 else 0
        s += 10 if sess in ("LONDON","NY") else 3
        res["A"] = (s, d, why)
    # ---------- B ----------
    for d in (1, -1):
        s = 0; why = []
        dc = (h1.high.iloc[-21:-1].max() if d>0 else h1.low.iloc[-21:-1].min())
        brk = (c1.iloc[-1] > dc) if d>0 else (c1.iloc[-1] < dc)
        tokb = tH is not None and sess != "TOKYO" and ((m15.close.iloc[-1] > tH) if d>0 else (m15.close.iloc[-1] < tL))
        if not (brk or tokb): continue
        s += 20; why.append("ドンチャン抜け" if brk else "東京レンジ抜け")
        _, bu, bl = boll(c1); ku, kl_ = keltner(h1)
        sq = ((bu < ku) & (bl > kl_)).iloc[-12:-1].any(); expand = (bu-bl).iloc[-1] > (bu-bl).iloc[-3]
        s += 15 if sq and expand else 7 if expand else 0; why.append("スクイーズ放れ" if sq and expand else "")
        body = abs(c1.iloc[-1]-h1.open.iloc[-1])/a1; ratio = atr(h1,5).iloc[-1]/atr(h1,50).iloc[-1]
        s += (8 if body >= 0.8 else 4 if body >= 0.4 else 0) + (7 if ratio >= 1.2 else 3 if ratio >= 1.0 else 0)
        s += 15 if (up4 and d>0) or (dn4 and d<0) else 0
        s += 15 if sess in ("LONDON","NY") else 5
        nl = next_level(d); room = abs(nl-px)/a1 if nl else 3
        s += 10 if room >= 2 else 5 if room >= 1 else 0
        s += 10 if ad.iloc[-1] > ad.iloc[-4] else 0
        if s > res.get("B", (0,))[0]: res["B"] = (s, d, [w for w in why if w])
    # ---------- B / 継続トレンド（既存ブレイクBを維持して追加判定） ----------
    # 確定した5分足のみ。1h・15mは完成足のみで方向一致を判定する。
    # B継続は新規の成行を自動承認しない。SBIレート・構造SL・RR・指標確認が必須。
    cont = None
    try:
        closed_m5 = m5[m5.index + pd.Timedelta(minutes=5) <= pd.Timestamp(now)]
        if len(closed_m5) >= 240:
            last_closed = closed_m5.index[-1] + pd.Timedelta(minutes=5)
            cont = signal_at(closed_m5, last_closed)
    except (ValueError, KeyError, IndexError):
        pass
    if cont is not None:
        d = cont['direction']
        _, upper5, lower5 = boll(m5.close)
        inside = cont['inside_2sigma']
        # 70点は「候補」を示すだけ。成行可否は別途判断。
        score_cont = 60 + (10 if inside else 0)
        why_cont = ['B継続:1h/15m一致', '5m MACD方向/ヒスト拡大',
                    '5m ATR14上昇', '5m BB幅拡大',
                    '±2σ内' if inside else '±2σ外:追いかけ注意']
        if score_cont > res.get('B',(-1,))[0]:
            res['B'] = (score_cont,d,why_cont)
    # ---------- C ----------
    mid, bu, bl = boll(c1)
    flat = abs(ma25.iloc[-1]-ma25.iloc[-6]) / a1 < 0.3
    for d in (1, -1):   # d=+1 下限から買い, -1 上限から売り
        touch = (h1.low.iloc[-3:] <= bl.iloc[-3:]).any() if d>0 else (h1.high.iloc[-3:] >= bu.iloc[-3:]).any()
        if not touch: continue
        s = 0; why = ["±2σ到達"]
        inside = cbot <= px <= ctop or (ctop-cbot)/a1 < 0.5
        s += (8 if flat else 0) + (9 if ad.iloc[-1] < 20 else 4 if ad.iloc[-1] < 25 else 0) + (8 if inside else 0)
        wick = (min(m15.open.iloc[-1], m15.close.iloc[-1]) - m15.low.iloc[-1]) if d>0 else (m15.high.iloc[-1]-max(m15.open.iloc[-1], m15.close.iloc[-1]))
        nearlvl = any(abs(l-px) < 0.3*a1 for l in levels)
        s += (10 if wick > 0.3*atr(m15).iloc[-1] else 0) + (10 if nearlvl else 0); why.append("節目一致" if nearlvl else "")
        s += 15 if (not up4 and not dn4) or (up4 and d>0) or (dn4 and d<0) else 0
        s += 15 if sess in ("TOKYO","LATE") else 5
        s += 15
        r = rsi(c1).iloc[-1]; pb = (px-bl.iloc[-1])/(bu.iloc[-1]-bl.iloc[-1])
        s += 10 if (r < 30 or pb < 0.05) and d>0 or (r > 70 or pb > 0.95) and d<0 else 4 if (r < 40 and d>0) or (r > 60 and d<0) else 0
        if s > res.get("C", (0,))[0]: res["C"] = (s, d, [w for w in why if w])
    # ---------- 共通減点 ----------
    pen = []; tk_ = tick.get(sym)
    spread = (float(tk_["ask"])-float(tk_["bid"])) if tk_ else None
    for k,(s,d,why) in list(res.items()):
        p = 0
        if any(timedelta(minutes=-30) <= (now - e) <= timedelta(minutes=10) for e in EVENTS) or any(timedelta(0) <= (e - now) <= timedelta(minutes=30) for e in EVENTS):
            p += 15; why = why + ["指標接近-15"]
        if spread and spread/a1 > 0.10: p += 10; why = why + ["スプレッド-10"]
        dirs = {base: d, quote: -d}
        if any(EXPOSURE.get(c, 0) * v > 0 for c,v in dirs.items()): p += 10; why = why + ["相関重複-10"]
        nl_against = next_level(d)
        if nl_against and abs(nl_against-px) < 0.5*a1: p += 10; why = why + ["節目近い-10"]
        res[k] = (max(0, s-p), d, why)
    # 目安価格（Aは押し目指値、Bは逆指値、Cは指値）
    return {"sym": sym, "syn": syn, "px": px, "atr1h": a1, "atr5": a5, "adx": ad.iloc[-1], "rsi": rsi(c1).iloc[-1],
            "res": res, "continuation": cont, "levels": levels, "tokyo": (tH, tL), "ma25": ma25.iloc[-1], "kijun": kj.iloc[-1]}

def fmt(p, sym): return f"{p:.3f}" if sym.endswith("JPY") else f"{p:.5f}"

def main():
    now = datetime.now(J)
    data, tick = load(now)
    st = strength(data)
    out = []
    for s in FORMAL:
        try:
            out.append(score_pair(s, data[s], tick, st, now))
        except Exception as e:
            out.append({"sym": s, "err": str(e)})
    rows = []
    for o in out:
        if "err" in o: continue
        for k,(sc,d,why) in o["res"].items():
            if o["syn"] and k in ("B","C"): why = why + ["換算・参考"]
            rows.append((sc, o["sym"], k, d, why, o))
    rows.sort(key=lambda r: -r[0])
    best = {}
    for r in rows:
        best.setdefault(r[1], r)
    print("時刻", now.strftime("%m/%d %H:%M"), "セッション", session(now))
    print("強弱24h", sorted(((k, round(float(v),2)) for k,v in st["24h"].items()), key=lambda x:-x[1]))
    print("強弱6h ", sorted(((k, round(float(v),2)) for k,v in st["6h"].items()), key=lambda x:-x[1]))
    for sc,sym,k,d,why,o in sorted(best.values(), key=lambda r:-r[0]):
        a = o["atr1h"]
        if k == "A": ent = o["px"] - d*0.3*a
        elif k == "B": ent = o["px"] + d*0.1*a
        else: ent = o["px"]
        sl = ent - d*1.0*a; tp = ent + d*(2.0 if k=="B" else 1.5)*a
        print(f"{sc:3d} {sym} {k}型 {'買' if d>0 else '売'} 現{fmt(o['px'],sym)} ATR{fmt(a,sym)} ADX{o['adx']:.0f} RSI{o['rsi']:.0f} | 目安 入{fmt(ent,sym)} 損{fmt(sl,sym)} 利{fmt(tp,sym)} | {', '.join(why)}")
    for o in out:
        if o["sym"] not in best and "err" not in o:
            print(f"  - {o['sym']} 該当型なし 現{fmt(o['px'],o['sym'])} ADX{o['adx']:.0f} RSI{o['rsi']:.0f}")
        if "err" in o: print("ERR", o["sym"], o["err"])
    json.dump([{k:v for k,v in o.items() if k in ("sym","px","res","adx","rsi","continuation")} for o in out], open("last_scores.json","w"), default=str, ensure_ascii=False)

if __name__ == "__main__":
    main()