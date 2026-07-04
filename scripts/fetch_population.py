#!/usr/bin/env python3
"""広報すおう大島から「人のうごき」（人口の動き）を抽出する。

処理:
  1. 広報ページから号（年月）→PDF版URLの対応を取得
     - 最新年: https://www.town.suo-oshima.lg.jp/soshiki/2/1617.html
     - バックナンバー: /soshiki/2/1572.html からの年別ページ
  2. 直近 --num 号（デフォルト12）のPDFを data/kouhou_pdfs/ にダウンロード（差分取得）
  3. 「人のうごき」欄を正規表現で抽出し、検算のうえ
     data/population/monthly.json に保存（基準日の昇順）

使い方:
  .venv/bin/python scripts/fetch_population.py [--num 12]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pdfplumber

BASE_URL = "https://www.town.suo-oshima.lg.jp"
CURRENT_PAGE = f"{BASE_URL}/soshiki/2/1617.html"   # 最新号+当年のバックナンバー
INDEX_PAGE = f"{BASE_URL}/soshiki/2/1572.html"     # 年別バックナンバー一覧
USER_AGENT = (
    "SuoOshimaGijirokuCollector/1.0 "
    "(+https://github.com/datamonsters-jp/minna-no-gijiroku)"
)
REQUEST_INTERVAL_SEC = 2.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = PROJECT_ROOT / "data" / "kouhou_pdfs"
OUT_PATH = PROJECT_ROOT / "data" / "population" / "monthly.json"

_last_request_at = 0.0


def polite_get(url: str) -> bytes:
    global _last_request_at
    wait = REQUEST_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp:
        body = resp.read()
    _last_request_at = time.monotonic()
    return body


# ---------------------------------------------------------------- 号の一覧取得

def parse_issue_links(html: str, year: int) -> dict:
    """『N月号 … PDF版リンク』の並びから {(year, month): pdf_url} を作る。"""
    issues = {}
    month = None
    for m in re.finditer(
            r'(\d+)月号|href="(/uploaded/attachment/\d+\.pdf)">PDF版', html):
        if m.group(1):
            month = int(m.group(1))
        elif month is not None:
            issues.setdefault((year, month), BASE_URL + m.group(2))
    return issues


def collect_issues(num: int) -> list:
    """新しい順に num 号ぶんの (year, month, pdf_url) を返す。"""
    html = polite_get(CURRENT_PAGE).decode("utf-8", errors="replace")
    m = re.search(r"広報すおう大島（(\d{4})年）", html)
    current_year = int(m.group(1)) if m else time.localtime().tm_year
    issues = parse_issue_links(html, current_year)

    # 足りなければバックナンバーの年別ページを新しい年から辿る
    index_html = polite_get(INDEX_PAGE).decode("utf-8", errors="replace")
    year_pages = re.findall(
        r'href="(/soshiki/2/\d+\.html)">広報すおう大島（(\d{4})年）', index_html)
    year_pages.sort(key=lambda t: -int(t[1]))
    for path, year_s in year_pages:
        if len(issues) >= num + 2:   # 余裕をもって打ち切り
            break
        year = int(year_s)
        if year >= current_year:
            continue
        page = polite_get(BASE_URL + path).decode("utf-8", errors="replace")
        issues.update(parse_issue_links(page, year))

    ordered = sorted(issues.items(), key=lambda kv: kv[0], reverse=True)[:num]
    return [(y, mo, url) for (y, mo), url in ordered]


# ---------------------------------------------------------------- 抽出

NUM = r"([\d,]+)"
DELTA = r"（\s*([-+±▲△]?\s*\d+)\s*）"


def parse_delta(s: str) -> int:
    s = s.replace(" ", "").replace("　", "")
    if s.startswith(("±",)):
        return 0
    if s.startswith(("▲", "△")):
        return -int(s[1:])
    return int(s)


def extract_population(pdf_path: Path, pub_year: int, pub_month: int) -> dict:
    with pdfplumber.open(pdf_path) as pdf:
        text = ""
        for page in pdf.pages:
            t = page.extract_text() or ""
            if "人のうごき" in t:
                text = t
                break
    if not text:
        raise ValueError("「人のうごき」のページが見つかりません")
    # PDF由来の制御文字（\x07など）が数値の前に混入することがあるため除去
    text = re.sub(r"[\x00-\x09\x0b-\x1f]", "", text)

    def find(pattern, cast=int, required=True):
        m = re.search(pattern, text)
        if not m:
            if required:
                raise ValueError(f"抽出失敗: {pattern}")
            return None
        return cast(m.group(1).replace(",", ""))

    # 基準日「（５月１日現在）」→ 掲載月の前月として年を推定
    m = re.search(r"人のうごき（([０-９\d]+)月([０-９\d]+)日現在）", text)
    if not m:
        raise ValueError("基準日が見つかりません")
    z2h = str.maketrans("０１２３４５６７８９", "0123456789")
    ref_month = int(m.group(1).translate(z2h))
    ref_day = int(m.group(2).translate(z2h))
    ref_year = pub_year if ref_month <= pub_month else pub_year - 1

    pop_m = re.search(r"人口\s*" + NUM + r"人\s*" + DELTA, text)
    male_m = re.search(r"男\s*" + NUM + r"人\s*" + DELTA, text)
    female_m = re.search(r"女\s*" + NUM + r"人\s*" + DELTA, text)
    house_m = re.search(r"世帯\s*" + NUM + r"[戸世帯]*\s*" + DELTA, text)
    if not all((pop_m, male_m, female_m, house_m)):
        raise ValueError("人口・男・女・世帯のいずれかが抽出できません")

    rec = {
        "掲載号": f"{pub_year}年{pub_month}月号",
        "基準日": f"{ref_year}-{ref_month:02d}-{ref_day:02d}",
        "人口": int(pop_m.group(1).replace(",", "")),
        "人口増減": parse_delta(pop_m.group(2)),
        "男": int(male_m.group(1).replace(",", "")),
        "男増減": parse_delta(male_m.group(2)),
        "女": int(female_m.group(1).replace(",", "")),
        "女増減": parse_delta(female_m.group(2)),
        "世帯": int(house_m.group(1).replace(",", "")),
        "世帯増減": parse_delta(house_m.group(2)),
        "出生": find(r"出生\s*(\d+)人"),
        "死亡": find(r"死亡\s*(\d+)人"),
        "転入": find(r"転入\s*(\d+)人"),
        "転出": find(r"転出\s*(\d+)人"),
    }

    # 検算: 出生-死亡+転入-転出 = 人口増減、男+女 = 人口
    natural = rec["出生"] - rec["死亡"]
    social = rec["転入"] - rec["転出"]
    rec["自然増減"] = natural
    rec["社会増減"] = social
    checks = []
    if natural + social != rec["人口増減"]:
        checks.append(f"増減不一致（自然{natural}+社会{social}≠{rec['人口増減']}）")
    if rec["男"] + rec["女"] != rec["人口"]:
        checks.append(f"男女計不一致（{rec['男']}+{rec['女']}≠{rec['人口']}）")
    if checks:
        rec["_検算注意"] = " / ".join(checks)
    return rec


# ---------------------------------------------------------------- メイン

def main() -> int:
    parser = argparse.ArgumentParser(description="広報から人口の動きを抽出")
    parser.add_argument("--num", type=int, default=12, help="取得する号数（新しい順）")
    args = parser.parse_args()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("広報の号一覧を取得中...")
    issues = collect_issues(args.num)
    print(f"対象: {len(issues)} 号 "
          f"({issues[-1][0]}年{issues[-1][1]}月号 〜 {issues[0][0]}年{issues[0][1]}月号)")

    records, failed = [], []
    for year, month, url in issues:
        pdf_path = PDF_DIR / f"{year}-{month:02d}.pdf"
        if not pdf_path.exists():
            print(f"  ダウンロード: {year}年{month}月号 <- {url}")
            pdf_path.write_bytes(polite_get(url))
        try:
            rec = extract_population(pdf_path, year, month)
            rec["_pdf_url"] = url
            records.append(rec)
            note = f" ⚠ {rec['_検算注意']}" if "_検算注意" in rec else ""
            print(f"  {rec['掲載号']}: 人口{rec['人口']:,}人"
                  f"（{rec['人口増減']:+d}） 世帯{rec['世帯']:,}{note}")
        except ValueError as e:
            print(f"  失敗: {year}年{month}月号: {e}", file=sys.stderr)
            failed.append(f"{year}年{month}月号")

    records.sort(key=lambda r: r["基準日"])
    out = {
        "meta": {
            "source": "広報すおう大島「人のうごき」",
            "note": "基準日時点の住民基本台帳ベース。増減は前月比。",
            "generated_by": "scripts/fetch_population.py",
        },
        "monthly": records,
    }
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n保存: {OUT_PATH.relative_to(PROJECT_ROOT)}（{len(records)}か月分）")
    if failed:
        print(f"失敗: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
