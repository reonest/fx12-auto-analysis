# FX 12通貨・iPhone自動更新ダッシュボード（試作）

## 概要
GitHub Actionsで約15分ごとにYahoo Financeの非公式チャートAPIへアクセスし、12通貨の5分足ATR14/RSI14/MACDと1時間足MA5/25/75を計算。GitHub Pagesの公開Web画面に結果を保存します。Macは起動不要です。

**重要：未デプロイ・Yahooへのライブアクセス未検証。** 更新は厳密な15分保証ではありません。Yahooのアクセス制限・欠損・遅延やGitHub Actionsの混雑・停止で更新できない場合があります。公開リポジトリのGitHub Pagesでは分析結果も公開されます。認証情報、口座情報、注文情報は絶対にアップロードしないでください。

## 初回設定（Macのブラウザのみでも可能）
1. GitHubアカウントを作成し、**Public** リポジトリ `fx12-dashboard` を作成。
2. このZIPの `analyzer.py`, `site.py`, `requirements.txt`, `.github/workflows/update.yml` をフォルダ構成を維持してリポジトリにアップロード。ZIPをそのままアップロードしても展開されません。
3. GitHubの `Settings > Actions > General > Workflow permissions` で `Read and write permissions` を選び保存（選べない場合は権限設定を確認）。
4. `Actions` タブで `FX 12-pair refresh` を選択し、`Run workflow` を手動実行。成功したら `docs/index.html` が生成されます。
5. `Settings > Pages` の `Build and deployment` を `Deploy from a branch`、`main`、`/docs` にして保存。
6. 表示されたPages URLをiPhoneのSafariで開き、共有 > ホーム画面に追加。
7. iPhoneでURLを開いて最終更新時刻と各通貨の取得状態を確認。画面は開くたびに最新の公開結果を読み込みます。

## 費用
公開リポジトリのGitHub Actions・GitHub Pages無料枠を想定していますが、プラン・利用規約・無料枠の最新条件はGitHubで確認してください。GitHub Pagesは公開表示なので、取引情報は載せないでください。

## 対象通貨
USD/JPY、EUR/JPY、GBP/JPY、AUD/JPY、EUR/USD、GBP/USD、AUD/USD、USD/CAD、EUR/GBP、EUR/AUD、GBP/AUD、MXN/JPY。AUD/NZDは対象外。

## 分析範囲と制約
一次選考用の簡易スコアのみ。15分足の価格構造・ADX・経済指標・相関・SBI実勢Bid/Ask・スプレッド・IFD-OCO期限は未実装。10分足は不要という運用の正式変更はまだしていません。FXプロジェクトの正式ルールと照合するまで発注判断に使わないでください。

## 重要な運用上の注意
GitHub Actionsのscheduleは混雑で遅れることがあり、リポジトリの状態によって自動無効化される場合があります。取得不能な通貨はUNAVAILABLEと表示し、値を推測しません。スクショなしで完全放置のIFD-OCOを確定するものではありません。
