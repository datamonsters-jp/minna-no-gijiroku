#!/usr/bin/env python3
"""会議録PDFをテキスト抽出し、Claude Batch APIで構造化JSONに要約するパイプライン。

Batch API（通常料金の50%オフ）を使った2段階処理:
  ステージ1: 各PDFをチャンク分割し、チャンクごとの抽出リクエストをバッチ投入
  ステージ2: 複数チャンクのPDFは部分結果の統合リクエストをバッチ投入
  完了したものから data/summaries/<PDF名>.json に保存

差分・再開:
  - data/summaries/ に既にあるPDFはスキップ（差分処理）
  - 投入済みバッチIDは data/batch_state.json に記録され、中断しても
    再実行すれば結果待ちから再開する（バッチ結果は29日間取得可能）

対象範囲:
  - デフォルトは令和（2019年5月以降）の会議録のみ
  - 過去分は --era 平成 または --era all で後から追加実行できる

使い方:
  export ANTHROPIC_API_KEY=sk-ant-...
  .venv/bin/python scripts/summarize_minutes.py [--era 令和|平成|all]
      [--limit N] [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000
CHUNK_CHARS = 60_000          # 1チャンクの最大文字数（ページ境界で分割）
MAX_REQUESTS_PER_BATCH = 100  # 1バッチあたりのリクエスト数上限
MAX_BATCH_BYTES = 100_000_000 # 1バッチあたりの推定ボディサイズ上限（API上限256MB）
POLL_INTERVAL_SEC = 60

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = PROJECT_ROOT / "data" / "pdfs"
SUMMARY_DIR = PROJECT_ROOT / "data" / "summaries"
STATE_PATH = PROJECT_ROOT / "data" / "batch_state.json"

JSON_SPEC = """\
{
  "会期名": "例: 令和8年第1回定例会（第1日）",
  "開催日": "YYYY-MM-DD（不明なら null）",
  "議題要約": [
    {
      "議題": "議題名",
      "要約": "中学生にも分かる平易な日本語で3〜5文。専門用語はかみくだいて説明する。"
    }
  ],
  "一般質問": [
    {
      "質問者": "議員名",
      "テーマ": "質問のテーマ",
      "質問要旨": "質問の要点（平易な日本語で2〜3文）",
      "答弁要旨": "町長・執行部の答弁の要点（平易な日本語で2〜3文）"
    }
  ],
  "議案結果": [
    {
      "議案番号": "例: 議案第25号（番号がなければ件名のみ）",
      "件名": "議案の件名",
      "結果": "可決 / 否決 / 承認 / 同意 / 継続審査 / 撤回 / 不明 のいずれか",
      "備考": "全会一致・賛成多数・反対討論の有無など（なければ null）"
    }
  ],
  "頻出キーワード": ["人口減少", "観光", "防災", "..."]
}"""

EXTRACT_SYSTEM = """あなたは地方議会の会議録を住民向けに分かりやすく整理する編集者です。
周防大島町議会の会議録テキストから情報を抽出し、指定されたJSON形式だけを出力してください。
JSON以外の文章やコードフェンス（```）は一切出力しないでください。
会議録に存在しない情報を創作してはいけません。該当がない項目は空配列またはnullにしてください。"""


# ---------------------------------------------------------------- PDF処理

def extract_pdf_text(pdf_path: Path) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(f"[p.{i}]\n{text}")
    return "\n\n".join(pages)


def split_into_chunks(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """ページ境界（[p.N] マーカー）を優先してチャンク分割する。"""
    if len(text) <= max_chars:
        return [text]
    pages = re.split(r"\n\n(?=\[p\.\d+\])", text)
    chunks, current = [], ""
    for page in pages:
        if current and len(current) + len(page) > max_chars:
            chunks.append(current)
            current = page
        else:
            current = f"{current}\n\n{page}" if current else page
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------- プロンプト

def chunk_prompt(filename: str, chunk: str, part: int, total: int) -> str:
    note = ""
    if total > 1:
        note = (
            f"\n注意: これは全{total}分割中の{part}番目の部分です。"
            "この部分に含まれる情報だけを抽出してください（後で統合します）。"
        )
    return f"""以下は周防大島町議会の会議録「{filename}」のテキストです。{note}

次のJSON形式で情報を抽出してください:

{JSON_SPEC}

--- 会議録テキストここから ---
{chunk}
--- 会議録テキストここまで ---"""


def merge_prompt(filename: str, partials: list[dict]) -> str:
    return f"""以下は会議録「{filename}」をチャンク分割して抽出した部分結果のリストです。
これらを統合して、重複を除いた1つのJSONにまとめてください。
- 「議題要約」「一般質問」「議案結果」は同一項目を統合し、要約は情報を失わないようまとめ直す
- 「頻出キーワード」は全体で重要なもの上位10件程度に絞る
- 出力形式は部分結果と同じ（前述のJSON形式）

{JSON_SPEC}

--- 部分結果 ---
{json.dumps(partials, ensure_ascii=False, indent=1)}"""


def build_params(user_prompt: str) -> dict:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": EXTRACT_SYSTEM,
        "messages": [{"role": "user", "content": user_prompt}],
    }


def parse_json_response(raw: str) -> dict:
    """モデル出力からJSONを取り出す。コードフェンス付きにも耐える。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"JSONが見つかりません: {raw[:200]}...")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------- バッチ操作

def submit_batches(client: Anthropic, requests: list[dict], label: str) -> list[str]:
    """リクエスト数とサイズの上限を守って複数バッチに分けて投入する。"""
    batch_ids = []
    group, group_bytes = [], 0
    def flush():
        nonlocal group, group_bytes
        if not group:
            return
        batch = client.messages.batches.create(requests=group)
        batch_ids.append(batch.id)
        print(f"  {label}バッチ投入: {batch.id}（{len(group)}リクエスト）")
        group, group_bytes = [], 0
    for req in requests:
        size = len(json.dumps(req))  # ensure_ascii=Trueで送信時サイズを概算
        if group and (len(group) >= MAX_REQUESTS_PER_BATCH
                      or group_bytes + size > MAX_BATCH_BYTES):
            flush()
        group.append(req)
        group_bytes += size
    flush()
    return batch_ids


def wait_for_batches(client: Anthropic, batch_ids: list[str], label: str) -> None:
    pending = set(batch_ids)
    while pending:
        for bid in sorted(pending):
            batch = client.messages.batches.retrieve(bid)
            if batch.processing_status == "ended":
                pending.discard(bid)
                c = batch.request_counts
                print(f"  {label}バッチ完了: {bid}"
                      f"（成功 {c.succeeded} / 失敗 {c.errored} / 期限切れ {c.expired}）")
        if pending:
            print(f"  {label}バッチ {len(pending)} 件を待機中... "
                  f"({datetime.now().strftime('%H:%M:%S')})")
            time.sleep(POLL_INTERVAL_SEC)


def collect_results(client: Anthropic, batch_ids: list[str]) -> dict:
    """custom_id → 抽出済みdict（またはエラー文字列）のマップを返す。"""
    results = {}
    for bid in batch_ids:
        for entry in client.messages.batches.results(bid):
            if entry.result.type == "succeeded":
                msg = entry.result.message
                text = "".join(b.text for b in msg.content if b.type == "text")
                try:
                    results[entry.custom_id] = parse_json_response(text)
                except (ValueError, json.JSONDecodeError) as e:
                    results[entry.custom_id] = f"ERROR: JSONパース失敗: {e}"
            else:
                results[entry.custom_id] = f"ERROR: {entry.result.type}"
    return results


# ---------------------------------------------------------------- 状態管理

def load_state() -> dict | None:
    if STATE_PATH.exists():
        with STATE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_PATH)


def clear_state() -> None:
    STATE_PATH.unlink(missing_ok=True)


# ---------------------------------------------------------------- メイン処理

def find_targets(era: str, limit: int | None, force: bool) -> list[Path]:
    pdfs = sorted(p for p in PDF_DIR.glob("*.pdf") if not p.stem.endswith("目次"))
    if era != "all":
        pdfs = [p for p in pdfs if p.stem.startswith(era)]
    targets = []
    for p in pdfs:
        if not force and (SUMMARY_DIR / f"{p.stem}.json").exists():
            continue
        targets.append(p)
    if limit:
        targets = targets[:limit]
    return targets


def save_summary(stem: str, summary: dict, chunks: int, chars: int) -> None:
    summary["_meta"] = {
        "source_pdf": f"{stem}.pdf",
        "model": MODEL,
        "chunks": chunks,
        "chars": chars,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch": True,
    }
    out_path = SUMMARY_DIR / f"{stem}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="会議録PDFをBatch APIで構造化JSONに要約する")
    parser.add_argument("--era", choices=["令和", "平成", "all"], default="令和",
                        help="対象の年号（デフォルト: 令和）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="対象一覧・文字数・チャンク数の表示のみ（API呼び出しなし）")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    state = load_state()
    if state and not args.dry_run:
        print(f"未完了のバッチ状態を検出（{STATE_PATH.name}）。結果待ちから再開します。")
    else:
        state = None

    # ---- 対象決定とテキスト抽出（再開時も状態ファイルの対象リストを使う）
    if state:
        pdf_map = state["pdfs"]  # pid -> {"stem":..., "chunks": n, "chars": n}
        targets = [PDF_DIR / f"{v['stem']}.pdf" for v in pdf_map.values()]
    else:
        targets = find_targets(args.era, args.limit, args.force)

    if not targets:
        print("対象のPDFはありません（すべて処理済み）。")
        return 0

    print(f"対象: {len(targets)} 本（era={args.era}）")

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("エラー: 環境変数 ANTHROPIC_API_KEY が設定されていません。", file=sys.stderr)
        return 1

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    # ---- ステージ1: チャンク抽出リクエストの作成・投入
    client = None if args.dry_run else Anthropic()

    if state:
        pdf_map = state["pdfs"]
        chunk_batch_ids = state["chunk_batches"]
        texts = None  # 再開時はテキスト不要（結果はAPIから取得）
    else:
        pdf_map, requests, texts = {}, [], {}
        total_chars = 0
        for idx, pdf_path in enumerate(targets):
            pid = f"p{idx:04d}"
            text = extract_pdf_text(pdf_path)
            chunks = split_into_chunks(text)
            pdf_map[pid] = {"stem": pdf_path.stem, "chunks": len(chunks),
                            "chars": len(text)}
            total_chars += len(text)
            print(f"  {pdf_path.stem}: {len(text):,}字 / {len(chunks)}チャンク")
            for i, chunk in enumerate(chunks, start=1):
                requests.append({
                    "custom_id": f"{pid}_c{i}of{len(chunks)}",
                    "params": build_params(
                        chunk_prompt(pdf_path.name, chunk, i, len(chunks))),
                })
        print(f"\n合計: {total_chars:,}字 / チャンクリクエスト {len(requests)} 件")

        if args.dry_run:
            return 0

        chunk_batch_ids = submit_batches(client, requests, "抽出")
        state = {"era": args.era, "pdfs": pdf_map,
                 "chunk_batches": chunk_batch_ids, "merge_batches": None}
        save_state(state)

    # ---- ステージ1の完了待ちと結果回収
    wait_for_batches(client, chunk_batch_ids, "抽出")
    chunk_results = collect_results(client, chunk_batch_ids)

    failed, merge_requests = [], []
    single_done = 0
    for pid, info in pdf_map.items():
        n = info["chunks"]
        parts = [chunk_results.get(f"{pid}_c{i}of{n}") for i in range(1, n + 1)]
        if any(p is None or isinstance(p, str) for p in parts):
            errs = [p for p in parts if isinstance(p, str)]
            print(f"  失敗: {info['stem']} ({errs[:1] or ['結果なし']})", file=sys.stderr)
            failed.append(info["stem"])
        elif n == 1:
            save_summary(info["stem"], parts[0], n, info["chars"])
            single_done += 1
        else:
            merge_requests.append({
                "custom_id": f"{pid}_merge",
                "params": build_params(
                    merge_prompt(f"{info['stem']}.pdf", parts)),
            })
    print(f"ステージ1完了: 単一チャンク {single_done} 本保存 / "
          f"統合待ち {len(merge_requests)} 本 / 失敗 {len(failed)} 本")

    # ---- ステージ2: 統合リクエストの投入・回収
    merge_done = 0
    if merge_requests:
        if state.get("merge_batches"):
            merge_batch_ids = state["merge_batches"]
        else:
            merge_batch_ids = submit_batches(client, merge_requests, "統合")
            state["merge_batches"] = merge_batch_ids
            save_state(state)

        wait_for_batches(client, merge_batch_ids, "統合")
        merge_results = collect_results(client, merge_batch_ids)

        for pid, info in pdf_map.items():
            if info["chunks"] == 1 or info["stem"] in failed:
                continue
            merged = merge_results.get(f"{pid}_merge")
            if merged is None or isinstance(merged, str):
                print(f"  統合失敗: {info['stem']} ({merged})", file=sys.stderr)
                failed.append(info["stem"])
            else:
                save_summary(info["stem"], merged, info["chunks"], info["chars"])
                merge_done += 1

    clear_state()
    print(f"\n完了: 保存 {single_done + merge_done} 本 / 失敗 {len(failed)} 本")
    if failed:
        print("失敗分は再実行すれば自動的に再処理されます:", ", ".join(failed[:5]),
              "..." if len(failed) > 5 else "")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
