#!/usr/bin/env python3
"""周防大島町議会の会議録PDFを収集するスクリプト。

一覧ページ (list18-56.html) から各会期の詳細ページを辿り、
詳細ページ内のPDFリンクをすべて data/pdfs/ にダウンロードする。

差分検出:
- 取得済みPDFと各詳細ページの更新日を data/index.json に記録する。
- 一覧ページ上の更新日が前回と同じ詳細ページはアクセス自体をスキップし、
  新規・更新されたページのPDFのうち未取得のものだけダウンロードする。

使い方:
    python3 scripts/fetch_minutes.py

役場サーバーへの配慮として、全HTTPリクエストの間に2秒のsleepを入れている。
"""

import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BASE_URL = "https://www.town.suo-oshima.lg.jp"
LIST_URL = f"{BASE_URL}/site/gikai/list18-56.html"
USER_AGENT = (
    "SuoOshimaGijirokuCollector/1.0 "
    "(+https://github.com/datamonsters-jp/minna-no-gijiroku)"
)
REQUEST_INTERVAL_SEC = 2.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PDF_DIR = DATA_DIR / "pdfs"
INDEX_PATH = DATA_DIR / "index.json"

# 一覧ページの1エントリ:
# <li><span class="article_date">2026年6月10日更新</span>
#     <span class="article_title"><a href="/site/gikai/15586.html">タイトル</a></span></li>
LIST_ENTRY_RE = re.compile(
    r'<span class="article_date">([^<]*)</span>\s*'
    r'<span class="article_title"><a href="(/site/gikai/\d+\.html)">([^<]*)</a>'
)

_last_request_at = 0.0


def polite_get(url: str) -> bytes:
    """2秒間隔とUser-Agentを守ってGETする。"""
    global _last_request_at
    wait = REQUEST_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp:
        body = resp.read()
    _last_request_at = time.monotonic()
    return body


class LinkExtractor(HTMLParser):
    """<a href>とそのリンクテキストを集める。"""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, text)
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._current_href = href
                self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href is not None:
            text = "".join(self._current_text).strip()
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


def find_session_pages(list_html: str) -> list[dict]:
    """一覧ページから各会期の詳細ページ (URL, タイトル, 更新日) を抽出する。"""
    pages = []
    seen = set()
    for updated, href, title in LIST_ENTRY_RE.findall(list_html):
        url = urljoin(BASE_URL, href)
        if url in seen:
            continue
        seen.add(url)
        pages.append({"url": url, "title": title.strip(), "updated": updated.strip()})
    return pages


def build_pdf_filename(link_text: str, page_title: str, pdf_url: str) -> str:
    """PDFリンクのテキストから分かりやすいファイル名を組み立てる。

    例: 「令和8年第1回定例会（第2日） [PDFファイル／1.16MB]」
        → 「令和8年_第1回定例会_2日目.pdf」
    """
    # 「[PDFファイル／1.3MB]」のようなサイズ表記を除去
    text = re.sub(r"[\[［(（]\s*PDF[^\]）)]*[\]）)]?", "", link_text)
    text = unicodedata.normalize("NFKC", text).strip()

    m = re.match(
        r"(令和|平成)(\d+|元)年\s*第(\d+)回\s*(定例会|臨時会)\s*[(（](.+?)[)）]",
        text,
    )
    if m:
        era, year, session_no, session_type, part = m.groups()
        part = part.strip()
        day = re.fullmatch(r"第(\d+)日", part)
        part_label = f"{day.group(1)}日目" if day else part
        name = f"{era}{year}年_第{session_no}回{session_type}_{part_label}"
    else:
        # 想定外の表記はリンクテキスト（空ならページタイトル+添付ID）をそのまま使う
        fallback = text or page_title
        attachment_id = Path(pdf_url).stem
        name = f"{fallback}_{attachment_id}" if fallback else attachment_id

    # ファイル名に使えない文字を除去
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return f"{name}.pdf"


def load_index() -> dict:
    if INDEX_PATH.exists():
        with INDEX_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {"pages": {}, "pdfs": {}}


def save_index(index: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(INDEX_PATH)


def now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def main() -> int:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    index = load_index()

    print(f"一覧ページを取得中: {LIST_URL}")
    list_html = polite_get(LIST_URL).decode("utf-8", errors="replace")
    pages = find_session_pages(list_html)
    print(f"詳細ページ {len(pages)} 件を検出")

    new_count = 0
    for page in pages:
        page_url, page_title, updated = page["url"], page["title"], page["updated"]

        # 前回チェック時から更新日が変わっていないページはスキップ
        prev = index["pages"].get(page_url)
        if prev and prev.get("updated") == updated:
            continue

        print(f"確認中: {page_title} ({updated})")
        page_html = polite_get(page_url).decode("utf-8", errors="replace")
        extractor = LinkExtractor()
        extractor.feed(page_html)
        pdf_links = [
            (urljoin(page_url, href), text)
            for href, text in extractor.links
            if href.lower().endswith(".pdf")
        ]

        used_names = {entry["filename"] for entry in index["pdfs"].values()}
        for pdf_url, link_text in pdf_links:
            if pdf_url in index["pdfs"]:
                continue  # 取得済み

            filename = build_pdf_filename(link_text, page_title, pdf_url)
            if filename in used_names:
                # 同名衝突時は添付IDを付けて区別する
                filename = f"{Path(filename).stem}_{Path(pdf_url).stem}.pdf"
            used_names.add(filename)

            print(f"  ダウンロード: {filename}  <-  {pdf_url}")
            pdf_bytes = polite_get(pdf_url)
            (PDF_DIR / filename).write_bytes(pdf_bytes)

            index["pdfs"][pdf_url] = {
                "filename": filename,
                "link_text": link_text,
                "page_url": page_url,
                "page_title": page_title,
                "size_bytes": len(pdf_bytes),
                "downloaded_at": now_utc(),
            }
            save_index(index)  # 中断されても取得済み分の記録が残るよう都度保存
            new_count += 1

        # ページ内の全PDFを処理し終えてから更新日を記録する
        index["pages"][page_url] = {
            "title": page_title,
            "updated": updated,
            "checked_at": now_utc(),
        }
        save_index(index)

    print(f"完了: 新規 {new_count} 件、累計 {len(index['pdfs'])} 件 (保存先: {PDF_DIR})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
