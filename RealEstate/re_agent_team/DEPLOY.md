# 常時利用（おすすめ構成）

このプロジェクトは `render.yaml` を使って Render に常時デプロイできます。  
最も簡単で「使いたいときにすぐ使える」構成です。

## 1. 事前準備

- GitHub にこのフォルダを push
- Render アカウントを作成

## 2. Render でデプロイ

1. Render ダッシュボードで **New +** → **Blueprint**
2. リポジトリを選択
3. `render.yaml` を読み込んで作成
4. 環境変数を設定
   - `REINFOLIB_API_KEY` (必須)
   - `ORS_API_KEY` (任意)

`render.yaml` で以下は自動設定されます。
- 永続ディスク `/var/data`（DBと出力保存）
- `DB_PATH=/var/data/realestate.db`
- ヘルスチェック `/healthz`

## 3. 動作確認

- デプロイ完了後、発行URLを開く
- `https://<your-app>.onrender.com/healthz` が `{"status":"ok"}` を返せばOK

## 4. 共有方法

- 発行URLをそのまま共有すれば、他の人も同じ画面を使えます
- DBが永続化されるため、分析結果や収集データも保持されます

## 5. 運用メモ

- Render 側で再起動しても、`/var/data` のDBは残ります
- 定期データ収集はアプリ内スケジューラで動作します（Webプロセス常駐時）
- APIキーを変更したら Render の Environment を更新後に再デプロイしてください
