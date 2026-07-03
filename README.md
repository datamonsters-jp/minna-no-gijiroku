# みんなの議事録 周防大島

周防大島町議会の会議録（公式PDF）をAIで要約し、住民向けにわかりやすく公開する**非公式**サイトです。

> ⚠️ 本サイトは公式の会議録PDFをAIが要約した非公式サイトです。
> 正確な内容は必ず[原文の会議録](https://www.town.suo-oshima.lg.jp/site/gikai/list18-56.html)をご確認ください。

## 構成

| パス | 内容 |
|---|---|
| `scripts/fetch_minutes.py` | 町サイトから会議録PDFを収集（差分取得・2秒間隔のアクセス制限つき） |
| `scripts/summarize_minutes.py` | PDFをテキスト抽出し、Claude Batch APIで構造化JSONに要約 |
| `scripts/build_site.py` | 要約JSONから静的サイトを `docs/` に生成 |
| `data/summaries/` | 会議録ごとの要約JSON |
| `docs/` | 生成された静的サイト（GitHub Pages公開対象） |

会議録PDF本体は著作権に配慮しリポジトリに含めていません（`fetch_minutes.py` で取得できます）。
サイト内のPDFリンクはすべて町公式サーバーへの直リンクです。

## ビルド方法

```bash
# 1. PDF収集（初回は全件、以降は新規分のみ）
python3 scripts/fetch_minutes.py

# 2. 要約（要 pdfplumber / anthropic、APIキーは環境変数から）
python3 -m venv .venv && .venv/bin/pip install pdfplumber anthropic
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python scripts/summarize_minutes.py --era 令和

# 3. サイト生成とプレビュー
python3 scripts/build_site.py
python3 -m http.server -d docs 8080
```

## クレジット

- 会議録の原文: [周防大島町議会](https://www.town.suo-oshima.lg.jp/site/gikai/list18-56.html)
- 要約モデル: Claude (Anthropic)
