import json, html
from pathlib import Path
from datetime import datetime, timezone

src=Path('fx12_output/fx12_screening.json')
site=Path('docs'); site.mkdir(exist_ok=True)
if src.exists():
    data=json.loads(src.read_text(encoding='utf-8'))
else:
    data={'generated_utc':None,'results':[]}
rows=[]
for x in sorted(data['results'],key=lambda a: (a.get('status')!='OK',-a.get('screening_score',-1))):
    ok=x.get('status')=='OK'
    cells=[x.get('pair',''),x.get('status',''),x.get('trend_1h','—'),x.get('screening_score','—'),x.get('close_5m','—'),x.get('atr14_5m','—'),x.get('rsi14_5m','—'),x.get('macd_hist_5m','—'),x.get('five_min_closed_utc','—'),x.get('reason','')]
    rows.append('<tr class="'+('ok' if ok else 'bad')+'">'+''.join('<td>'+html.escape(str(c))+'</td>' for c in cells)+'</tr>')
updated=html.escape(str(data.get('generated_utc') or '未更新'))
page='''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FX 12通貨・自動分析</title><style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:0;background:#101825;color:#eaf0f7}main{max-width:1150px;margin:auto;padding:18px}h1{font-size:1.5rem}.info{background:#1c2b3e;padding:14px;border-radius:12px;margin:14px 0}table{border-collapse:collapse;min-width:1050px;width:100%;font-size:13px}th,td{padding:10px;border-bottom:1px solid #364457;text-align:left}th{position:sticky;top:0;background:#23364d}.scroll{overflow:auto;border-radius:12px}.bad{color:#f5a6a6}small{color:#bdcad8}a{color:#8fceff}</style><main><h1>FX 12通貨・自動分析</h1><div class="info">最終実行（UTC）：UPDATED<br><small>表示は取得できた確定足のみ。順位スコアは期待利益・勝率ではありません。未取得・遅延を含む可能性があります。</small></div><div class="scroll"><table><thead><tr><th>通貨</th><th>状態</th><th>1時間トレンド</th><th>簡易スコア</th><th>5分終値</th><th>ATR14</th><th>RSI14</th><th>MACDヒスト</th><th>5分足開始(UTC)</th><th>取得エラー</th></tr></thead><tbody>ROWS</tbody></table></div><p><small>データ元：Yahoo Finance非公式エンドポイント。SBI FXトレードのBid/Ask・実勢スプレッド・約定条件ではありません。投資助言や発注シグナルではなく一次選考用です。クラウドの実行は遅延・停止することがあります。</small></p><p><a href="fx12_screening.json">JSONデータ</a> · <a href="fx12_screening.csv">CSVデータ</a></p></main></html>'''
(site/'index.html').write_text(page.replace('UPDATED',updated).replace('ROWS',''.join(rows)),encoding='utf-8')
if src.exists():
    (site/'fx12_screening.json').write_bytes(src.read_bytes())
    (site/'fx12_screening.csv').write_bytes(Path('fx12_output/fx12_screening.csv').read_bytes())
