# 記憶のノート（Memory Diary）

忘れやすい人のための日記アプリ。出来事を雑に短く打ち込むと SQLite に**逐語で保存**し、
一日の終わりにまとめたり、過去の出来事を質問で思い出したりできる。
AIは「保存された記録だけ」を根拠に答えるよう固定されており、創作（ハルシネーション）を抑える。

## 構成（study_planner と同じ土台）
- Flask + SQLite + Jinja2、gunicorn で Railway デプロイ
- LLM は Anthropic Claude API（`app.py` の `MODEL` 定数で切替）

## 主要ファイル
| ファイル | 役割 |
|----------|------|
| app.py | ルーティング・DB操作・LLM呼び出し |
| database.py | DB接続・init_db（entries テーブル） |
| templates/index.html | メイン画面（記録／まとめ／質問） |
| templates/login.html | パスワード認証画面 |

## 環境変数
| 変数 | 必須 | 説明 |
|------|------|------|
| `ANTHROPIC_API_KEY` | ✅ | Claude APIキー（console.anthropic.com で発行） |
| `APP_PASSWORD` | 任意 | 設定するとログイン必須になる（医療・人間関係の情報を守るため推奨） |
| `SECRET_KEY` | 任意 | Flask セッション署名鍵（本番ではランダム値を設定） |
| `DB_PATH` | 任意 | SQLite の保存先パス |

## ローカルで動かす
```
pip install -r requirements.txt
set ANTHROPIC_API_KEY=<あなたのAPIキー>   # PowerShell: $env:ANTHROPIC_API_KEY="<あなたのAPIキー>"
python app.py                       # http://127.0.0.1:5001
```

## Railway へデプロイ
1. このフォルダを Git リポジトリにして GitHub に push（study_planner と同じ手順）
2. Railway で New Project → Deploy from GitHub repo
3. Variables に `ANTHROPIC_API_KEY` と `APP_PASSWORD`、`SECRET_KEY` を設定
4. 生成された公開URLをスマホで開き、「ホーム画面に追加」でアプリのように使える

## 注意
- これは記憶の補助。AIの回答は「根拠」欄の記録原文と照らして確認する運用にする。
- SQLite はファイル1個（`memory_diary.db`）。定期的にバックアップ／エクスポートすること。
