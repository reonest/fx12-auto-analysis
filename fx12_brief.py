#!/usr/bin/env python3
"""Render a compact GPT briefing from the FX12 laboratory JSON.
Always prints original publication time and observation age. No auto S-grade.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def as_jst(value):
    if not value:
        return "未確認"
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    except (ValueError, TypeError, AttributeError):
        return "未確認"


def make_brief(data):
    lines = [
        "# FX12試験分析コンテキスト（発注指示ではない）",
        f"データ生成: {data.get('generated_jst', '不明')} / GMO API状態: {data.get('gmo_ticker_status', '不明')}",
        f"市場時間帯参考: {data.get('market_time_analysis', {}).get('phase_reference', '未取得')}",
        "**休場中のスコアは過去の確定足による研究結果。現在の新規売買推奨ではありません。**",
        "**正式12通貨（2026-10-21版） + AUD/NZD比較研究。CHF/JPY・NZD/JPYは正式監視、CAD/JPYのみ補助。**",
        "**注文前にSBI FXのBid/Ask・実勢スプレッド・損切り・数量・注文条件のスクショ確認が必要。**",
        "",
        "## 上位候補（スコアは検証中、勝率ではない）",
    ]
    for row in data.get("top_candidates", []):
        lines.append(
            f"- {row['pair']} | 方向: {row['direction']} | 環境: {row['environment']} "
            f"| データ: {row['source']} | 試験点: {row['score_experimental']}"
        )
    strength = data.get("eight_currency_strength", {})
    lines.extend(["", "## 主要8通貨の強弱（同一時刻のGMO円ペアから試験計算）",
                  f"判定: {strength.get('status','未確認')}, 基準時刻: {strength.get('to_close_jst','未確認')}"])
    for entry in strength.get("ranked", []):
        lines.append(f"- {entry['currency']}: 基準バスケット相対変化 {entry['relative_to_equal_weight_basket_pct']:+.4f}%")
    lines.extend(["", "## 正式12通貨の取得・確定足時刻（JST）"])
    for row in data.get("pairs", []):
        tf = row.get("frames", {})
        five = tf.get("5m", {})
        ten = tf.get("10m", {})
        hour = tf.get("1h", {})
        lines.append(
            f"- {row['pair']} [{row['role']}]: {row['source']} / {row['market_status']}; "
            f"5m={five.get('status')}@{as_jst(five.get('candle_closed_utc'))}; "
            f"10m={ten.get('status')}@{as_jst(ten.get('candle_closed_utc'))}; "
            f"1h={hour.get('status')}@{as_jst(hour.get('candle_closed_utc'))}; "
            f"3方式={row.get('three_methods')}"
        )
    econ = data.get("events_and_news", {})
    lines.extend(["", "## 未執行・研究用シグナル（実注文ではない）"])
    for signal in data.get("unexecuted_research_signals", []):
        lines.append(f"- {signal['pair']} | {signal['method']} | {signal['direction_reference']} "
                     f"| 10m確定 {as_jst(signal.get('last_10m_close_utc'))} "
                     f"| 結果未検証・約定なし")
    lines.extend(["", "## 公式予定指標（日本時間）",
                  "**公開カレンダーは一部のみ。0件でも「重要指標なし」と断定しない。**"])
    for event in econ.get("events", [])[:30]:
        lines.append(
            f"- {event.get('scheduled_jst', '時刻未確認')} | {event.get('name')} "
            f"| {','.join(event.get('currencies', []))} | 重要度参考={event.get('importance_hint')} "
            f"| {event.get('url')}"
        )
    lines.extend(["", "## 公式RSSニュース（古い記事を速報扱いしない）",
                  "**S級判定は常に未実施。RSS収集だけではS級速報を通知しない。**"])
    for n in econ.get("news", [])[:25]:
        lines.append(
            f"- 発表={n.get('published_jst') or '未確認'} | 発表から={n.get('publication_age_minutes')}分 "
            f"| 種別={n.get('freshness')} | {n.get('source')} | {n.get('title')} "
            f"| {n.get('url')}"
        )
    lines.extend(["", "## データ取得状況・障害"])
    for s in econ.get("sources", []):
        lines.append(f"- {s.get('id')}: {s.get('status')} "
                     f"件数={s.get('records', '未取得')} 理由={s.get('error','')}")
    lines.extend(["", "## GPTが分析時に行う追加確認",
                  "1. このファイルの生成時刻と各通貨の5m・1h確定時刻を現在の日本時間と照合。週末休場は明示。",
                  "2. BLS/BEA/FRB/日銀/ECBに加えFXStreet、みんかぶFX、GMO外貨、楽天証券等の現在の予定/結果を調査。一次情報を優先。",
                  "3. 予定時刻、記事公表時刻、最初に確認された時刻、現在時刻を別に扱い、古い記事を新着S級速報と呼ばない。",
                  "4. 全12通貨比較、通常総合・別分析A・別分析B、時間帯・相関・リスクを比較し1～3候補を提示。",
                  "5. SBIの必要なチャートとBid/Askを依頼。スクショ確認前に確定IFD-OCO数値を作らない。",
                  "",
                  "参照用サイトの自動収集には許諾・API認証の検証が必要。取得失敗は未確認とする。",
                  "実際の発注や通知の常時実行は行っていない。"])
    return "\n".join(lines) + "\n"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("json")
    p.add_argument("--out", default="trial_output/fx12_brief.md")
    a=p.parse_args()
    raw=json.loads(Path(a.json).read_text(encoding="utf-8"))
    out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(make_brief(raw), encoding="utf-8")
    print("BRIEF",out,len(raw.get("pairs",[])),"pairs")


if __name__=="__main__":
    main()
