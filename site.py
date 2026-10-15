"""Render FX screening dashboard with per-pair candle freshness and JST timestamps."""
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
SRC = Path('fx12_output/fx12_screening.json')
SITE = Path('docs')
SITE.mkdir(exist_ok=True)
DATA = json.loads(SRC.read_text(encoding='utf-8')) if SRC.exists() else {'generated_utc': None, 'results': []}


def parse_utc(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def jst(value):
    d = parse_utc(value)
    return d.astimezone(JST).strftime('%m/%d %H:%M') if d else '未取得'


def candle_freshness(open_utc, interval_minutes, reference):
    """Yahoo timestamps are candle *start* times; calculate closed-bar age at generation."""
    opened = parse_utc(open_utc)
    if not opened or not reference:
        return ('不明', 'unknown', None)
    closed = opened + timedelta(minutes=interval_minutes)
    age = (reference - closed).total_seconds() / 60
    if age < -1:
        return ('時刻異常', 'stale', closed)
    age = max(0, round(age))
    # Allow a modest feed delay. These are UI monitoring thresholds, not trading rules.
    limit = 15 if interval_minutes == 5 else 90
    if age > limit:
        return (f'遅延 {age}分', 'stale', closed)
    return (f'{age}分前', 'fresh', closed)


def cell(value):
    return '<td>' + html.escape(str(value)) + '</td>'

reference = parse_utc(DATA.get('generated_utc'))
rows = []
counts = {'fresh': 0, 'stale': 0, 'unknown': 0}
for item in sorted(DATA.get('results', []), key=lambda x: (x.get('status') != 'OK', -x.get('screening_score', -1))):
    ok = item.get('status') == 'OK'
    five, five_class, five_end = candle_freshness(item.get('five_min_closed_utc'), 5, reference)
    hour, hour_class, hour_end = candle_freshness(item.get('hour_closed_utc'), 60, reference)
    if not ok:
        freshness, cls = '取得失敗', 'unknown'
    elif five_class == 'stale' or hour_class == 'stale':
        freshness, cls = '要注意', 'stale'
    elif five_class == 'fresh' and hour_class == 'fresh':
        freshness, cls = '正常', 'fresh'
    else:
        freshness, cls = '未確認', 'unknown'
    counts[cls] += 1
    values = [item.get('pair', ''), item.get('status', ''), freshness,
              item.get('trend_1h', '—'), item.get('screening_score', '—'),
              item.get('close_5m', '—'), item.get('atr14_5m', '—'),
              item.get('rsi14_5m', '—'), item.get('macd_hist_5m', '—'),
              five_end.astimezone(JST).strftime('%m/%d %H:%M') if five_end else '—', five,
              hour_end.astimezone(JST).strftime('%m/%d %H:%M') if hour_end else '—', hour,
              item.get('reason', '')]
    rows.append('<tr class="' + cls + '">' + ''.join(cell(v) for v in values) + '</tr>')

run_jst = jst(DATA.get('generated_utc'))
run_utc = html.escape(str(DATA.get('generated_utc') or '未更新'))
summary = f'鮮度正常 {counts["fresh"]} / 遅延・異常 {counts["stale"]} / 未確認 {counts["unknown"]}（全{len(DATA.get("results", []))}通貨）'
page = '''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FX 12通貨・自動分析</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,"Noto Sans JP",sans-serif;margin:0;background:#101825;color:#eaf0f7}main{max-width:1200px;margin:auto;padding:18px}h1{font-size:1.5rem}.info{background:#1c2b3e;padding:16px;border-radius:12px;margin:14px 0}.info strong{font-size:1.15rem}table{border-collapse:collapse;min-width:1500px;width:100%;font-size:13px}th,td{padding:10px;border-bottom:1px solid #364457;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#23364d}.scroll{overflow:auto;border-radius:12px}.stale{color:#ffd18a}.unknown{color:#f5a6a6}small{color:#bdcad8;line-height:1.6}a{color:#8fceff}.status{margin-top:9px;font-weight:600}
</style></head><body><main><h1>FX 12通貨・自動分析</h1><div class="info"><strong>最終分析実行（日本時間）：RUN_JST JST</strong><div class="status">SUMMARY</div><small>実行時刻（UTC）：RUN_UTC</small><p><small>足時刻はローソク足の「確定時刻」を表示。Yahoo Financeが返す開始時刻に5分または60分を加算しています。鮮度は分析実行時点を基準に計算。5分足15分超・1時間足90分超を遅延扱い（暫定的な監視基準）。休場中も遅延表示になります。</small></p></div><div class="scroll"><table><thead><tr><th>通貨</th><th>取得</th><th>鮮度</th><th>1時間トレンド</th><th>簡易スコア</th><th>5分終値</th><th>ATR14</th><th>RSI14</th><th>MACDヒスト</th><th>5分足確定(JST)</th><th>5分足経過</th><th>1時間足確定(JST)</th><th>1時間足経過</th><th>取得エラー</th></tr></thead><tbody>ROWS</tbody></table></div><p><small>データ元：Yahoo Finance非公式エンドポイント。取得OKはデータの鮮度を保証しません。表示された鮮度はページを開いた現在時刻ではなく最終分析実行時点の判定です。SBI FXトレードのBid/Ask・実勢スプレッド・約定条件ではありません。一次選考専用で、発注シグナルではありません。</small></p><p><a href="fx12_screening.json">JSONデータ</a> · <a href="fx12_screening.csv">CSVデータ</a></p></main></body></html>'''
SITE.joinpath('index.html').write_text(page.replace('RUN_JST', html.escape(run_jst)).replace('RUN_UTC', run_utc).replace('SUMMARY', html.escape(summary)).replace('ROWS', ''.join(rows)), encoding='utf-8')
if SRC.exists():
    SITE.joinpath('fx12_screening.json').write_bytes(SRC.read_bytes())
    csv = Path('fx12_output/fx12_screening.csv')
    if csv.exists():
        SITE.joinpath('fx12_screening.csv').write_bytes(csv.read_bytes())
