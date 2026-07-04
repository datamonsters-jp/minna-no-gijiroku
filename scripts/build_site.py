#!/usr/bin/env python3
"""data/summaries/ の要約JSONから静的サイトを docs/ に生成する。

デザインは design_handoff_gijiroku/ のClaudeDesignハンドオフに準拠
（クリーム地 × オレンジ × 緑、Zen Maru Gothic見出し）。
本文の文字サイズのみ、高齢の住民の読みやすさを優先して
ハンドオフ（13〜14px）より大きい17〜18pxを採用している。

使い方:
  python3 scripts/build_site.py            # docs/ にビルド
  python3 -m http.server -d docs 8080     # ローカルプレビュー

依存: 標準ライブラリのみ。
"""

from __future__ import annotations

import html
import json
import re
import shutil
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUMMARY_DIR = PROJECT_ROOT / "data" / "summaries"
INDEX_PATH = PROJECT_ROOT / "data" / "index.json"
OUT_DIR = PROJECT_ROOT / "docs"

SITE_TITLE = "みんなの議事録"
SITE_SUB = "周防大島町"
BASE_URL = "https://datamonsters-jp.github.io/minna-no-gijiroku"
SITE_DESC = (
    "周防大島町議会の会議録をAIがわかりやすいことばに要約。"
    "議題・一般質問・議案の結果を紹介する非公式サイトです。"
)
ASSETS_SRC = Path(__file__).resolve().parent.parent / "assets_src"
PAGES_SRC = Path(__file__).resolve().parent.parent / "pages_src"
BUDGET_DIR = Path(__file__).resolve().parent.parent / "data" / "budget"
POPULATION_PATH = Path(__file__).resolve().parent.parent / "data" / "population" / "monthly.json"
COUNCIL_PATH = Path(__file__).resolve().parent.parent / "data" / "council.json"
KOUHOU_BACKNUMBER_URL = "https://www.town.suo-oshima.lg.jp/soshiki/2/1572.html"
DISCLAIMER = (
    "本サイトは公式の会議録PDFをAIが要約した非公式サイトです。"
    "正確な内容は必ず原文の会議録をご確認ください。"
)

ERA_BASE = {"令和": 2018, "平成": 1988}
THEME_COLORS = ["#E8873C", "#4A8C5C", "#6B5BA8", "#C06828", "#D4A843"]


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


# ---------------------------------------------------------------- データ読み込み

def parse_stem(stem: str):
    """『令和8年_第1回定例会_2日目』→ (西暦, 回, 種別, 日目)"""
    m = re.match(r"(令和|平成)(\d+|元)年_第(\d+)回(定例会|臨時会)_(\d+)日目", stem)
    if not m:
        return None
    era, y, num, kind, day = m.groups()
    year = ERA_BASE[era] + (1 if y == "元" else int(y))
    return year, int(num), kind, int(day)


def session_slug(year: int, num: int, kind: str) -> str:
    k = "teirei" if kind == "定例会" else "rinji"
    return f"y{year}-{k}-{num}"


def load_sessions():
    """要約JSONを会期ごとにまとめる。返り値は新しい順のリスト。"""
    pdf_urls = {}
    if INDEX_PATH.exists():
        idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        for url, entry in idx["pdfs"].items():
            pdf_urls[entry["filename"]] = {"pdf": url, "page": entry["page_url"]}

    sessions = {}
    for path in sorted(SUMMARY_DIR.glob("*.json")):
        parsed = parse_stem(path.stem)
        if not parsed:
            print(f"  スキップ（ファイル名を解釈できません）: {path.name}")
            continue
        year, num, kind, day = parsed
        data = json.loads(path.read_text(encoding="utf-8"))
        key = (year, num, kind)
        era_year = year - ERA_BASE["令和"]
        sessions.setdefault(key, {
            "slug": session_slug(year, num, kind),
            "title": f"令和{'元' if era_year == 1 else era_year}年 第{num}回{kind}",
            "year": year,
            "kind": kind,
            "days": [],
        })
        pdf_name = f"{path.stem}.pdf"
        sessions[key]["days"].append({
            "day": day,
            "data": data,
            "pdf_url": pdf_urls.get(pdf_name, {}).get("pdf"),
            "page_url": pdf_urls.get(pdf_name, {}).get("page"),
        })

    result = []
    for sess in sessions.values():
        sess["days"].sort(key=lambda d: d["day"])
        dates = [d["data"].get("開催日") for d in sess["days"] if d["data"].get("開催日")]
        sess["start"], sess["end"] = (min(dates), max(dates)) if dates else (None, None)
        kw = []
        for d in sess["days"]:
            kw.extend(d["data"].get("頻出キーワード") or [])
        seen = set()
        sess["keywords"] = [k for k in kw if not (k in seen or seen.add(k))][:6]
        sess["all_keywords"] = kw
        sess["n_questions"] = sum(len(d["data"].get("一般質問") or []) for d in sess["days"])
        result.append(sess)
    result.sort(key=lambda s: (s["start"] or "", s["year"]), reverse=True)
    return result


def normalize_member(name: str) -> str:
    """『新田健介議員（9番）』→『新田健介』"""
    name = re.sub(r"[（(].*?[)）]", "", name)
    name = re.sub(r"(議員|君|氏)$", "", name.strip())
    return re.sub(r"[\s　]+", "", name)


def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    m = re.match(r"(\d+)-(\d+)-(\d+)", iso)
    return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日" if m else iso


def date_box(iso: str | None, alt: int) -> str:
    """カード左端の日付ボックス（オレンジ/緑交互）"""
    m = re.match(r"\d+-(\d+)-(\d+)", iso or "")
    mo, dy = (int(m.group(1)), int(m.group(2))) if m else ("", "")
    cls = "datebox orange" if alt % 2 == 0 else "datebox green"
    return f'<div class="{cls}"><span class="d-mo">{mo}月</span><span class="d-dy">{dy}</span></div>'


# ---------------------------------------------------------------- HTML部品

def page(title: str, body: str, root: str = ".", active: str = "",
         desc: str = SITE_DESC, path: str = "") -> str:
    nav_home = ' class="active"' if active == "home" else ""
    nav_search = ' class="active"' if active == "search" else ""
    full_title = (f"{title} | {SITE_TITLE} {SITE_SUB}" if title
                  else f"{SITE_TITLE} {SITE_SUB} — 町の議論を、みんなの手に。")
    url = f"{BASE_URL}/{path}"
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_TITLE} {SITE_SUB}">
<meta property="og:title" content="{esc(full_title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{BASE_URL}/assets/ogp.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="ja_JP">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(full_title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{BASE_URL}/assets/ogp.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Zen+Maru+Gothic:wght@500;700&family=Noto+Sans+JP:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{root}/assets/style.css">
</head>
<body>
<header class="site-header">
  <a class="brand" href="{root}/index.html">
    <span class="logo">議</span>
    <span class="brand-text"><span class="brand-title">{SITE_TITLE}</span><span class="brand-sub">{SITE_SUB}</span></span>
  </a>
  <nav><a href="{root}/index.html"{nav_home}>ホーム</a><a href="{root}/search.html"{nav_search}>一般質問をさがす</a><a href="{root}/budget.html">予算のながれ</a></nav>
</header>
<main>
{body}
</main>
<footer class="site-footer">
  <div class="footer-inner">
    <p class="footer-brand"><span class="logo">議</span> {SITE_TITLE} <span class="footer-sub">{SITE_SUB}</span></p>
    <p class="disclaimer">{esc(DISCLAIMER)}</p>
    <p class="source">原文の会議録: <a href="https://www.town.suo-oshima.lg.jp/site/gikai/list18-56.html">周防大島町議会 会議録一覧</a></p>
  </div>
</footer>
</body>
</html>"""


def keyword_tags(keywords) -> str:
    return "".join(f'<span class="tag">{esc(k)}</span>' for k in keywords)


def session_card(sess, alt: int, root=".") -> str:
    date = fmt_date(sess["start"])
    if sess["end"] and sess["end"] != sess["start"]:
        date += f" 〜 {fmt_date(sess['end'])}"
    meta = f"全{len(sess['days'])}日"
    if sess["n_questions"]:
        meta += f" ・ 一般質問 {sess['n_questions']}件"
    kind_cls = "t-teirei" if sess["kind"] == "定例会" else "t-rinji"
    return f"""<a class="card session-card" href="{root}/kaigi/{sess['slug']}.html">
  {date_box(sess['start'], alt)}
  <div class="session-card-body">
    <p class="card-tags"><span class="tag {kind_cls}">{esc(sess['kind'])}</span>{keyword_tags(sess['keywords'][:4])}</p>
    <h3>{esc(sess['title'])}</h3>
    <p class="card-meta">{esc(date)} ・ {esc(meta)}</p>
  </div>
  <span class="chevron">›</span>
</a>"""


# ---------------------------------------------------------------- 各ページ

def fmt_signed(v: int) -> str:
    return ("＋" if v > 0 else "−" if v < 0 else "±") + f"{abs(v):,}"


def population_line_chart(rows) -> str:
    """人口（左軸・実線）と世帯数（右軸・破線）の折れ線SVG。"""
    W, H, mL, mR, mT, mB = 720, 300, 68, 68, 30, 44
    pw, ph = W - mL - mR, H - mT - mB
    n = len(rows)
    pops = [r["人口"] for r in rows]
    hhs = [r["世帯"] for r in rows]

    def scale(vals):
        lo = (min(vals) // 100) * 100
        hi = -((-max(vals)) // 100) * 100
        if lo == hi:
            hi += 100
        return lo, hi

    plo, phi = scale(pops)
    hlo, hhi = scale(hhs)
    x = lambda i: mL + pw * i / (n - 1)
    yp = lambda v: mT + ph * (1 - (v - plo) / (phi - plo))
    yh = lambda v: mT + ph * (1 - (v - hlo) / (hhi - hlo))

    parts = []
    # 左軸グリッドと目盛り
    for v in range(plo, phi + 1, 100):
        parts.append(f'<line x1="{mL}" y1="{yp(v):.1f}" x2="{W - mR}" y2="{yp(v):.1f}" stroke="#F0E8DC" stroke-width="1"/>')
        parts.append(f'<text x="{mL - 8}" y="{yp(v):.1f}" font-size="12" fill="#8C7B66" text-anchor="end" dominant-baseline="middle">{v:,}</text>')
    # 右軸目盛り
    for v in range(hlo, hhi + 1, 100):
        parts.append(f'<text x="{W - mR + 8}" y="{yh(v):.1f}" font-size="12" fill="#8C7B66" dominant-baseline="middle">{v:,}</text>')
    # 月ラベル（年の変わり目に年も表示）
    prev_year = None
    for i, r in enumerate(rows):
        yy, mm = r["基準日"][:4], int(r["基準日"][5:7])
        parts.append(f'<text x="{x(i):.1f}" y="{H - mB + 18}" font-size="12" fill="#6B5D4F" text-anchor="middle">{mm}月</text>')
        if yy != prev_year:
            parts.append(f'<text x="{x(i):.1f}" y="{H - mB + 34}" font-size="11" fill="#A89880" text-anchor="middle">{yy}年</text>')
            prev_year = yy
    # 折れ線
    pop_pts = " ".join(f"{x(i):.1f},{yp(v):.1f}" for i, v in enumerate(pops))
    hh_pts = " ".join(f"{x(i):.1f},{yh(v):.1f}" for i, v in enumerate(hhs))
    parts.append(f'<polyline points="{hh_pts}" fill="none" stroke="#7AA5CC" stroke-width="2.5" stroke-dasharray="6 4"/>')
    parts.append(f'<polyline points="{pop_pts}" fill="none" stroke="#1F3557" stroke-width="3"/>')
    for i, v in enumerate(pops):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{yp(v):.1f}" r="3.5" fill="#1F3557"/>')
    # 凡例
    parts.append(f'<line x1="{mL}" y1="14" x2="{mL + 26}" y2="14" stroke="#1F3557" stroke-width="3"/>'
                 f'<text x="{mL + 32}" y="14" font-size="13" fill="#2C2418" dominant-baseline="middle">人口（左軸）</text>')
    parts.append(f'<line x1="{mL + 140}" y1="14" x2="{mL + 166}" y2="14" stroke="#7AA5CC" stroke-width="2.5" stroke-dasharray="6 4"/>'
                 f'<text x="{mL + 172}" y="14" font-size="13" fill="#2C2418" dominant-baseline="middle">世帯数（右軸）</text>')
    return f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="人口と世帯数の推移">{"".join(parts)}</svg>'


def population_bar_chart(rows) -> str:
    """月ごとの自然増減・社会増減の積み上げ棒SVG（減=赤系、増=青系）。"""
    W, H, mL, mR, mT, mB = 720, 310, 52, 16, 30, 60
    pw, ph = W - mL - mR, H - mT - mB
    n = len(rows)
    stack_lo = min(min(0, r["自然増減"]) + min(0, r["社会増減"]) for r in rows)
    stack_hi = max(max(0, r["自然増減"]) + max(0, r["社会増減"]) for r in rows)
    lo = (stack_lo // 10) * 10 - 10
    hi = -((-stack_hi) // 10) * 10 + 10
    y = lambda v: mT + ph * (hi - v) / (hi - lo)
    bw = pw / n * 0.55

    C = {"nat-": "#B0392E", "soc-": "#E4756A", "nat+": "#1E4A7A", "soc+": "#7AA5CC"}
    parts = []
    for v in range(lo, hi + 1, 20):
        parts.append(f'<line x1="{mL}" y1="{y(v):.1f}" x2="{W - mR}" y2="{y(v):.1f}" stroke="#F0E8DC"/>')
        parts.append(f'<text x="{mL - 6}" y="{y(v):.1f}" font-size="12" fill="#8C7B66" text-anchor="end" dominant-baseline="middle">{fmt_signed(v) if v else "0"}</text>')
    prev_year = None
    for i, r in enumerate(rows):
        cx = mL + pw * (i + 0.5) / n
        up = down = 0
        for key, val in (("nat", r["自然増減"]), ("soc", r["社会増減"])):
            if val == 0:
                continue
            if val > 0:
                y0, y1 = y(up + val), y(up)
                up += val
                color = C[key + "+"]
            else:
                y0, y1 = y(down), y(down + val)
                down += val
                color = C[key + "-"]
            parts.append(f'<rect x="{cx - bw / 2:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 1):.1f}" fill="{color}" rx="1.5"/>')
        total = r["自然増減"] + r["社会増減"]
        ty = y(down) + 14 if total < 0 else y(up) - 6
        parts.append(f'<text x="{cx:.1f}" y="{ty:.1f}" font-size="11.5" font-weight="bold" fill="{"#B0392E" if total < 0 else "#1E4A7A"}" text-anchor="middle">{fmt_signed(total)}</text>')
        yy, mm = r["基準日"][:4], int(r["基準日"][5:7])
        parts.append(f'<text x="{cx:.1f}" y="{H - mB + 32}" font-size="12" fill="#6B5D4F" text-anchor="middle">{mm}月</text>')
        if yy != prev_year:
            parts.append(f'<text x="{cx:.1f}" y="{H - mB + 48}" font-size="11" fill="#A89880" text-anchor="middle">{yy}年</text>')
            prev_year = yy
    # ゼロ線を強調
    parts.append(f'<line x1="{mL}" y1="{y(0):.1f}" x2="{W - mR}" y2="{y(0):.1f}" stroke="#6B5D4F" stroke-width="1.5"/>')
    # 凡例
    parts.append(f'<rect x="{mL}" y="8" width="13" height="13" fill="#B0392E" rx="3"/>'
                 f'<text x="{mL + 19}" y="15" font-size="13" fill="#2C2418" dominant-baseline="middle">自然増減（出生−死亡）</text>')
    parts.append(f'<rect x="{mL + 190}" y="8" width="13" height="13" fill="#E4756A" rx="3"/>'
                 f'<text x="{mL + 209}" y="15" font-size="13" fill="#2C2418" dominant-baseline="middle">社会増減（転入−転出）</text>')
    parts.append(f'<text x="{mL + 390}" y="15" font-size="12" fill="#8C7B66" dominant-baseline="middle">※赤=減った月・青=増えた月</text>')
    return f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="毎月の人口増減の内訳">{"".join(parts)}</svg>'


def build_population_section() -> str:
    if not POPULATION_PATH.exists():
        return ""
    data = json.loads(POPULATION_PATH.read_text(encoding="utf-8"))
    rows = data["monthly"]
    if not rows:
        return ""
    latest = rows[-1]
    delta = sum(r["人口増減"] for r in rows)
    base = latest["人口"] - delta
    pct_s = f"{delta / base * 100:+.1f}%".replace("+", "＋").replace("-", "−")
    natural = sum(r["自然増減"] for r in rows)
    social = sum(r["社会増減"] for r in rows)
    births = sum(r["出生"] for r in rows)
    deaths = sum(r["死亡"] for r in rows)
    move_in = sum(r["転入"] for r in rows)
    move_out = sum(r["転出"] for r in rows)
    ref = latest["基準日"]
    ref_label = f"{int(ref[5:7])}月{int(ref[8:10])}日"
    period = (f"{rows[0]['基準日'][:4]}年{int(rows[0]['基準日'][5:7])}月〜"
              f"{ref[:4]}年{int(ref[5:7])}月")
    src_pdf = latest.get("_pdf_url", KOUHOU_BACKNUMBER_URL)

    return f"""<section>
  <h2>周防大島町の今</h2>
  <p class="section-lead">町の「人口」「お金」を、議会の話題とあわせてどうぞ。</p>
  <div class="stats pop-stats">
    <div class="stat-card"><p class="stat-label">現在の人口（{esc(ref_label)}時点）</p>
      <p class="stat-num navy">{latest['人口']:,}<span class="stat-unit">人</span></p>
      <p class="stat-sub">世帯数 {latest['世帯']:,}戸</p></div>
    <div class="stat-card"><p class="stat-label">この1年の増減</p>
      <p class="stat-num red">{fmt_signed(delta)}<span class="stat-unit">人</span></p>
      <p class="stat-sub">{esc(pct_s)}</p></div>
    <div class="stat-card"><p class="stat-label">自然増減（出生−死亡）</p>
      <p class="stat-num red">{fmt_signed(natural)}<span class="stat-unit">人</span></p>
      <p class="stat-sub">出生{births}・死亡{deaths}</p></div>
    <div class="stat-card"><p class="stat-label">社会増減（転入−転出）</p>
      <p class="stat-num {'red' if social < 0 else 'navy'}">{fmt_signed(social)}<span class="stat-unit">人</span></p>
      <p class="stat-sub">転入{move_in}・転出{move_out}（ほぼ拮抗）</p></div>
  </div>
  <p class="pop-source">出典: <a href="{esc(src_pdf)}">広報すおう大島 {esc(latest['掲載号'])}「人のうごき」(PDF)</a></p>
  <h3>人口・世帯数の推移（{esc(period)}）</h3>
  <div class="panel chart-scroll">{population_line_chart(rows)}</div>
  <h3>毎月なぜ減っているのか（増減の内訳）</h3>
  <p class="section-lead">棒がゼロ線より下に伸びるほど、その月に人口が減ったことを表します。大半は出生より死亡が多い「自然減」です。</p>
  <div class="panel chart-scroll">{population_bar_chart(rows)}</div>
  <p class="pop-source">出典: <a href="{esc(KOUHOU_BACKNUMBER_URL)}">広報すおう大島 各号（バックナンバー一覧）</a>の「人のうごき」から集計</p>
  <a class="card budget-card" href="budget.html">
    <div class="budget-icon">¥</div>
    <div class="session-card-body">
      <h3>令和8年度当初予算 お金の流れ</h3>
      <p class="card-meta">総額171億5,000万円が「どこから入って、何に使われるのか」を1枚の図で。各項目のやさしい解説つき</p>
    </div>
    <span class="chevron">›</span>
  </a>
</section>"""


def build_top(sessions) -> str:
    latest = sessions[0]
    total_q = sum(s["n_questions"] for s in sessions)
    last_update = fmt_date(max(s["end"] or "" for s in sessions))

    # 頻出キーワードの集計から「よく議論されているテーマ」を作る（実データ由来）
    counter = Counter()
    for s in sessions:
        counter.update(set(s["all_keywords"]))
    themes = counter.most_common(5)
    max_count = themes[0][1] if themes else 1
    theme_rows = "".join(f"""<div class="theme-row">
  <span class="theme-label">{esc(name)}</span>
  <span class="theme-count">{count}会期</span>
  <div class="theme-bar-bg"><div class="theme-bar" style="width:{count / max_count * 100:.0f}%;background:{THEME_COLORS[i % 5]}"></div></div>
</div>""" for i, (name, count) in enumerate(themes))

    parts = [f"""<section class="hero">
  <div class="hero-inner">
    <p class="hero-eyebrow">● 周防大島町議会</p>
    <h1>町の議論を、<br>みんなの手に。</h1>
    <p class="hero-lead">周防大島町議会の会議録を、わかりやすいことばでお届けします。<br>町で何が話し合われ、何が決まったのかを、どなたでも気軽に。</p>
    <p class="hero-cta">
      <a class="btn-primary" href="kaigi/{latest['slug']}.html">最新の議会を読む →</a>
      <a class="btn-secondary" href="search.html">一般質問をさがす</a>
    </p>
  </div>
</section>
<section class="stats">
  <div class="stat-card"><p class="stat-label">公開済みの会期</p><p class="stat-num orange">{len(sessions)}<span class="stat-unit">会期</span></p></div>
  <div class="stat-card"><p class="stat-label">直近の会議</p><p class="stat-num small">{esc(latest['title'])}</p></div>
  <div class="stat-card"><p class="stat-label">一般質問</p><p class="stat-num green">{total_q}<span class="stat-unit">件</span></p></div>
  <div class="stat-card"><p class="stat-label">最終更新</p><p class="stat-num small">{esc(last_update)}</p></div>
</section>
<section>
  <h2>よく議論されているテーマ</h2>
  <p class="section-lead">各会期の頻出キーワードから集計しています。</p>
  <div class="panel">{theme_rows}</div>
</section>
{build_population_section()}
<section>
  <h2>これまでの議会</h2>"""]
    current_year = None
    for i, sess in enumerate(sessions):
        y = (sess["start"] or str(sess["year"]))[:4]
        if y != current_year:
            parts.append(f'<h3 class="year-heading">{esc(y)}年</h3>')
            current_year = y
        parts.append(session_card(sess, i))
    parts.append("</section>")
    return page("", "\n".join(parts), active="home")


def build_session_page(sess) -> str:
    parts = [f"""<p class="breadcrumb"><a href="../index.html">ホーム</a> › {esc(sess['title'])}</p>
<div class="detail-header">
<p class="card-tags"><span class="tag {'t-teirei' if sess['kind'] == '定例会' else 't-rinji'}">{esc(sess['kind'])}</span>{keyword_tags(sess['keywords'])}</p>
<h1>{esc(sess['title'])}</h1>
<p class="session-date">📅 {esc(fmt_date(sess['start']))}{' 〜 ' + esc(fmt_date(sess['end'])) if sess['end'] != sess['start'] else ''} ・ 全{len(sess['days'])}日</p>
</div>"""]

    for d in sess["days"]:
        data = d["data"]
        parts.append(f"""<section class="day">
<h2><span class="day-badge">{d['day']}日目</span> <span class="day-date">{esc(fmt_date(data.get('開催日')))}</span></h2>""")
        links = []
        if d["pdf_url"]:
            links.append(f'<a class="btn-primary btn-sm" href="{esc(d["pdf_url"])}">📄 原文の会議録PDFを読む</a>')
        if d["page_url"]:
            links.append(f'<a class="btn-secondary btn-sm" href="{esc(d["page_url"])}">町サイトの掲載ページ</a>')
        if links:
            parts.append(f'<p class="links">{" ".join(links)}</p>')

        gidai = data.get("議題要約") or []
        if gidai:
            parts.append("<h3>この日の議題</h3>")
            for g in gidai:
                parts.append(f"""<details class="topic">
<summary>{esc(g.get('議題'))}</summary>
<p>{esc(g.get('要約'))}</p>
</details>""")

        questions = data.get("一般質問") or []
        if questions:
            parts.append("<h3>一般質問</h3>")
            for q in questions:
                member = q.get("質問者") or ""
                initial = normalize_member(member)[:1] or "議"
                parts.append(f"""<div class="question">
<p class="q-header"><span class="avatar">{esc(initial)}</span>
<span><span class="q-member">{esc(member)}</span><br>
<span class="q-theme">{esc(q.get('テーマ'))}</span></span></p>
<div class="q-body">
<p class="q-label">質問</p>
<p>{esc(q.get('質問要旨'))}</p>
<div class="answer">
<p class="q-label a-label">答弁</p>
<p>{esc(q.get('答弁要旨'))}</p>
</div>
</div>
</div>""")

        gian = data.get("議案結果") or []
        if gian:
            parts.append("""<h3>議案の結果</h3>
<table class="gian"><thead><tr><th>議案</th><th>件名</th><th>結果</th></tr></thead><tbody>""")
            for g in gian:
                result = g.get("結果") or "不明"
                cls = {"可決": "ok", "承認": "ok", "同意": "ok",
                       "否決": "ng", "不明": "na"}.get(result, "na")
                note = f'<br><small>{esc(g.get("備考"))}</small>' if g.get("備考") else ""
                parts.append(f"<tr><td>{esc(g.get('議案番号'))}</td>"
                             f"<td>{esc(g.get('件名'))}{note}</td>"
                             f'<td class="result {cls}">{esc(result)}</td></tr>')
            parts.append("</tbody></table>")
        parts.append("</section>")

    n_gidai = sum(len(d["data"].get("議題要約") or []) for d in sess["days"])
    desc = (f"{sess['title']}（{fmt_date(sess['start'])}開会）の要約。"
            f"議題{n_gidai}件・一般質問{sess['n_questions']}件と議案の結果を"
            "わかりやすいことばで紹介します。")
    return page(sess["title"], "\n".join(parts), root="..",
                desc=desc, path=f"kaigi/{sess['slug']}.html")


def build_search(sessions) -> tuple[str, list]:
    questions = []
    for sess in sessions:
        for d in sess["days"]:
            for q in d["data"].get("一般質問") or []:
                questions.append({
                    "member": normalize_member(q.get("質問者") or ""),
                    "member_raw": q.get("質問者") or "",
                    "theme": q.get("テーマ") or "",
                    "question": q.get("質問要旨") or "",
                    "answer": q.get("答弁要旨") or "",
                    "session": sess["title"],
                    "date": d["data"].get("開催日") or "",
                    "url": f"kaigi/{sess['slug']}.html",
                })
    members = sorted({q["member"] for q in questions if q["member"]})
    options = "".join(f'<option value="{esc(m)}">{esc(m)}</option>' for m in members)

    council_html = ""
    if COUNCIL_PATH.exists():
        council = json.loads(COUNCIL_PATH.read_text(encoding="utf-8"))
        chips = "".join(
            f'<button type="button" class="member-chip" data-name="{esc(m["氏名"])}">'
            f'{esc(m["氏名"])}</button>'
            for m in council["議員"])
        council_html = f"""<div class="panel council-box">
  <h2 class="council-title">周防大島町議会の構成</h2>
  <p>周防大島町議会の議員は<strong>定数14人</strong>（{esc(council['meta']['as_of'])}）。
  現在の議員の任期は<strong>{esc(council['任期'])}</strong>です。
  町全体からえらばれた14人が、条例や予算の審議・一般質問などを通じて町政をチェックしています。</p>
  <p class="council-hint">議員の名前を押すと、その議員の一般質問に絞り込めます。</p>
  <div class="member-chips">{chips}</div>
  <p class="pop-source">出典: <a href="{esc(council['meta']['source_url'])}">{esc(council['meta']['source_name'])}</a></p>
</div>"""

    body = f"""<h1>一般質問をさがす</h1>
<p class="section-lead">議員の名前やキーワード（例: 防災、観光、人口減少）で、これまでの一般質問を絞り込めます。</p>
{council_html}
<div class="panel search-controls">
  <label>議員でしぼる
    <select id="member"><option value="">すべての議員</option>{options}</select>
  </label>
  <label>キーワード
    <input type="search" id="keyword" placeholder="🔍 例: 防災">
  </label>
</div>
<p id="count" class="search-count"></p>
<div id="results"></div>
<script>
const fmtDate = iso => {{
  const m = iso.match(/(\\d+)-(\\d+)-(\\d+)/);
  return m ? `${{+m[1]}}年${{+m[2]}}月${{+m[3]}}日` : iso;
}};
let QUESTIONS = [];
const escapeHtml = s => s.replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
function render() {{
  const member = document.getElementById("member").value;
  const kw = document.getElementById("keyword").value.trim();
  const hits = QUESTIONS.filter(q =>
    (!member || q.member === member) &&
    (!kw || (q.theme + q.question + q.answer + q.member_raw).includes(kw))
  );
  document.getElementById("count").textContent = `${{hits.length}}件みつかりました`;
  document.getElementById("results").innerHTML = hits.map(q => `
    <div class="question">
      <p class="q-header"><span class="avatar">${{escapeHtml(q.member.slice(0, 1) || "議")}}</span>
      <span><span class="q-member">${{escapeHtml(q.member_raw)}}</span><br>
      <span class="q-theme">${{escapeHtml(q.theme)}}</span></span></p>
      <p class="q-session"><a href="${{q.url}}">${{escapeHtml(q.session)}}</a> ・ ${{fmtDate(q.date)}}</p>
      <div class="q-body">
        <p class="q-label">質問</p><p>${{escapeHtml(q.question)}}</p>
        <div class="answer"><p class="q-label a-label">答弁</p><p>${{escapeHtml(q.answer)}}</p></div>
      </div>
    </div>`).join("") || '<p>該当する質問が見つかりませんでした。</p>';
}}
fetch("data/questions.json").then(r => r.json()).then(data => {{
  QUESTIONS = data;
  render();
  setupChips();
}});
document.getElementById("member").addEventListener("change", render);
document.getElementById("keyword").addEventListener("input", render);

// 議会構成の名前チップ: 押すとその議員で絞り込み。「﨑/崎」の表記ゆれも吸収
function chipVariants(name) {{
  return [name, name.replace(/﨑/g, "崎"), name.replace(/崎/g, "﨑")];
}}
function setupChips() {{
  const select = document.getElementById("member");
  const optionValues = [...select.options].map(o => o.value);
  document.querySelectorAll(".member-chip").forEach(chip => {{
    const match = chipVariants(chip.dataset.name).find(v => optionValues.includes(v));
    if (!match) {{
      chip.classList.add("no-q");
      chip.title = "掲載期間内の一般質問はありません";
      return;
    }}
    chip.addEventListener("click", () => {{
      select.value = match;
      document.getElementById("keyword").value = "";
      render();
      document.getElementById("count").scrollIntoView({{ behavior: "smooth", block: "start" }});
    }});
  }});
}}
</script>"""
    desc = ("周防大島町議会の一般質問を議員別・キーワード別に検索できます。"
            "質問と答弁の要旨をわかりやすいことばで紹介します。")
    return page("一般質問をさがす", body, active="search",
                desc=desc, path="search.html"), questions


STYLE = """/* みんなの議事録 周防大島 — design_handoff_gijiroku 準拠 */
:root {
  --orange: #E8873C;
  --orange-dark: #D47830;
  --orange-darker: #C06828;
  --orange-bg: #FFF3E8;
  --green: #4A8C5C;
  --green-dark: #3D7A50;
  --green-bg: #EDF5F0;
  --purple: #6B5BA8;
  --purple-bg: #F0EAFF;
  --text: #2C2418;
  --text-2: #6B5D4F;
  --muted: #8C7B66;
  --faint: #A89880;
  --bg: #FEFCF7;
  --card-border: #F0E8DC;
  --divider: #E8DFD0;
  --hover-bg: #F8F4EE;
  --footer-bg: #2C2418;
  --shadow: 0 2px 16px rgba(44,36,24,0.06);
  --shadow-hover: 0 4px 20px rgba(44,36,24,0.08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Noto Sans JP", "Hiragino Sans", Meiryo, sans-serif;
  font-size: 17px;
  line-height: 1.9;
  color: var(--text);
  background: var(--bg);
}
main { max-width: 46rem; margin: 0 auto; padding: 0 1.2rem 3.5rem; }
a { color: var(--orange-darker); }
h1, h2, h3, .stat-num, .brand-title, .logo {
  font-family: "Zen Maru Gothic", "Noto Sans JP", sans-serif;
}

/* ---------- header ---------- */
.site-header {
  position: sticky; top: 0; z-index: 10;
  background: rgba(254,252,247,0.85); backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--divider);
  padding: 0.6rem 1.2rem;
  display: flex; flex-wrap: wrap; gap: 0.4rem 1.5rem;
  align-items: center; justify-content: space-between;
}
.brand { display: flex; align-items: center; gap: 0.6rem; text-decoration: none; }
.logo {
  display: inline-flex; align-items: center; justify-content: center;
  width: 38px; height: 38px; border-radius: 11px;
  background: var(--orange); color: #fff; font-weight: 700; font-size: 1.15rem;
}
.brand-text { display: flex; flex-direction: column; line-height: 1.25; }
.brand-title { color: var(--text); font-weight: 700; font-size: 1.1rem; }
.brand-sub { color: var(--muted); font-size: 0.8rem; }
.site-header nav a {
  color: var(--text-2); text-decoration: none; margin-left: 1.2rem;
  font-size: 1rem; font-weight: 500;
}
.site-header nav a.active, .site-header nav a:hover { color: var(--orange); }

/* ---------- hero ---------- */
.hero {
  margin: 0 -1.2rem;
  background: linear-gradient(165deg, #FFF8F0 0%, #FEF3E2 40%, #F0F7ED 100%);
  position: relative; overflow: hidden;
}
.hero::before, .hero::after {
  content: ""; position: absolute; border-radius: 50%; opacity: 0.5;
  animation: floaty 7s ease-in-out infinite;
}
.hero::before { width: 220px; height: 220px; background: #F5DDBF; right: -60px; top: -40px; }
.hero::after { width: 140px; height: 140px; background: #D9E8D2; right: 18%; bottom: -50px; animation-delay: 2.5s; }
@keyframes floaty { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-14px); } }
.hero-inner { max-width: 46rem; margin: 0 auto; padding: 2.8rem 1.2rem 3.4rem; position: relative; z-index: 1; }
.hero-eyebrow { color: var(--green-dark); font-weight: 600; font-size: 0.95rem; margin: 0 0 0.4rem; }
.hero h1 { font-size: 2.1rem; line-height: 1.5; margin: 0 0 0.8rem; }
.hero-lead { color: var(--text-2); margin: 0 0 1.6rem; }
.hero-cta { display: flex; flex-wrap: wrap; gap: 0.8rem; margin: 0; }
.btn-primary {
  display: inline-block; background: var(--orange); color: #fff;
  padding: 0.7rem 1.5rem; border-radius: 12px; text-decoration: none;
  font-weight: 700; box-shadow: 0 2px 12px rgba(232,135,60,0.3);
  transition: all 0.2s;
}
.btn-primary:hover { background: var(--orange-dark); transform: translateY(-1px); box-shadow: 0 4px 16px rgba(232,135,60,0.4); }
.btn-secondary {
  display: inline-block; background: #fff; color: var(--text);
  border: 1px solid var(--divider);
  padding: 0.7rem 1.5rem; border-radius: 12px; text-decoration: none;
  font-weight: 600; transition: all 0.2s;
}
.btn-secondary:hover { border-color: var(--orange); color: var(--orange-darker); transform: translateY(-1px); }
.btn-sm { padding: 0.5rem 1.1rem; font-size: 0.98rem; }

/* ---------- stats ---------- */
.stats {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
  gap: 0.8rem; margin-top: -2rem; position: relative; z-index: 2;
}
.stat-card {
  background: #fff; border: 1px solid var(--card-border); border-radius: 16px;
  padding: 1rem 1.2rem; box-shadow: var(--shadow);
}
.stat-label { color: var(--muted); font-size: 0.88rem; margin: 0 0 0.2rem; }
.stat-num { font-size: 2rem; font-weight: 700; margin: 0; line-height: 1.4; }
.stat-num.small { font-size: 1.15rem; }
.stat-num.orange { color: var(--orange); }
.stat-num.green { color: var(--green); }
.stat-unit { font-size: 0.95rem; color: var(--muted); font-weight: 500; margin-left: 0.2rem; }

/* ---------- sections ---------- */
h2 { font-size: 1.4rem; margin: 2.6rem 0 0.4rem; }
h3 { font-size: 1.12rem; margin: 1.8rem 0 0.8rem; }
.section-lead { color: var(--muted); font-size: 0.95rem; margin: 0 0 1rem; }
.year-heading { color: var(--muted); font-size: 1rem; margin: 1.8rem 0 0.7rem; }

.panel {
  background: #fff; border: 1px solid var(--card-border); border-radius: 16px;
  padding: 1.3rem 1.4rem; box-shadow: var(--shadow);
}
.theme-row { margin-bottom: 1rem; }
.theme-row:last-child { margin-bottom: 0; }
.theme-label { font-weight: 600; }
.theme-count { color: var(--muted); font-size: 0.9rem; margin-left: 0.6rem; }
.theme-bar-bg { background: var(--hover-bg); border-radius: 5px; height: 10px; margin-top: 0.3rem; }
.theme-bar { height: 10px; border-radius: 5px; }

/* ---------- cards ---------- */
.card {
  display: flex; align-items: center; gap: 1rem;
  background: #fff; border: 1px solid var(--card-border); border-radius: 14px;
  padding: 1rem 1.2rem; margin-bottom: 0.8rem;
  text-decoration: none; color: var(--text);
  box-shadow: var(--shadow); transition: all 0.2s;
}
.card:hover { box-shadow: var(--shadow-hover); border-color: rgba(232,135,60,0.35); transform: translateY(-1px); }
.session-card h3 { margin: 0.1rem 0; font-size: 1.12rem; }
.session-card-body { flex: 1; min-width: 0; }
.card-tags { margin: 0 0 0.2rem; display: flex; flex-wrap: wrap; gap: 0.35rem; }
.card-meta { color: var(--muted); font-size: 0.9rem; margin: 0; }
.chevron { color: #C4B8A8; font-size: 1.6rem; }

/* ---------- 周防大島町の今 ---------- */
.pop-stats { margin-top: 0.6rem; }
.stat-num.navy { color: #1F3557; }
.stat-num.red { color: #B0392E; }
.stat-sub { color: var(--muted); font-size: 0.88rem; margin: 0; }
.pop-source { color: var(--muted); font-size: 0.85rem; margin: 0.5rem 0 0; }
.pop-source a { color: var(--orange-darker); }
.chart-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.chart-scroll svg { min-width: 560px; width: 100%; height: auto; display: block; }

.budget-card { border-width: 2px; border-color: var(--orange); margin-top: 1.6rem; }
.budget-icon {
  flex: none; width: 56px; height: 56px; border-radius: 12px;
  background: var(--orange); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 1.7rem; font-weight: 700;
  font-family: "Zen Maru Gothic", sans-serif;
}

.datebox {
  flex: none; width: 56px; height: 56px; border-radius: 12px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  line-height: 1.2;
}
.datebox.orange { background: var(--orange-bg); color: var(--orange-darker); }
.datebox.green { background: var(--green-bg); color: var(--green-dark); }
.d-mo { font-size: 0.75rem; font-weight: 600; }
.d-dy { font-size: 1.35rem; font-weight: 700; font-family: "Zen Maru Gothic", sans-serif; }

.tag {
  background: var(--orange-bg); color: var(--orange-darker);
  border-radius: 20px; padding: 0.02rem 0.7rem; font-size: 0.85rem; font-weight: 600;
  white-space: nowrap;
}
.tag.t-teirei { background: var(--green-bg); color: var(--green-dark); }
.tag.t-rinji { background: var(--purple-bg); color: var(--purple); }

/* ---------- detail ---------- */
.breadcrumb { font-size: 0.92rem; color: var(--muted); margin-top: 1.2rem; }
.detail-header h1 { font-size: 1.7rem; margin: 0.3rem 0; }
.session-date { color: var(--text-2); margin: 0; }
.day { border-top: 2px solid var(--divider); margin-top: 2.6rem; padding-top: 1rem; }
.day-badge {
  background: var(--orange); color: #fff; border-radius: 10px;
  padding: 0.1rem 0.8rem; font-size: 1.05rem;
}
.day-date { font-size: 1rem; color: var(--muted); font-weight: 500; margin-left: 0.5rem; }
.links { display: flex; flex-wrap: wrap; gap: 0.7rem; }

details.topic {
  background: #fff; border: 1px solid var(--card-border); border-radius: 14px;
  margin-bottom: 0.7rem; padding: 0.4rem 1.1rem;
  box-shadow: var(--shadow);
}
details.topic summary { cursor: pointer; font-weight: 600; padding: 0.5rem 0; }
details.topic summary:hover { color: var(--orange-darker); }
details.topic p { margin: 0.3rem 0 0.9rem; color: var(--text-2); }

.question {
  background: #fff; border: 1px solid var(--card-border); border-radius: 14px;
  padding: 1.1rem 1.2rem; margin-bottom: 1rem; box-shadow: var(--shadow);
}
.q-header { display: flex; gap: 0.7rem; align-items: center; margin: 0 0 0.6rem; line-height: 1.5; }
.avatar {
  flex: none; width: 40px; height: 40px; border-radius: 50%;
  background: var(--orange-bg); color: var(--orange-darker);
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 700; font-family: "Zen Maru Gothic", sans-serif;
}
.q-member { font-weight: 700; }
.q-theme { font-size: 0.95rem; color: var(--text-2); font-weight: 600; }
.q-session { font-size: 0.9rem; color: var(--muted); margin: 0 0 0.5rem; }
.q-body p { margin: 0.15rem 0 0.6rem; }
.q-label {
  font-size: 0.82rem; font-weight: 700; color: var(--orange-darker);
  background: var(--orange-bg); display: inline-block;
  border-radius: 6px; padding: 0 0.6rem; margin: 0;
}
.answer {
  border-left: 3px solid var(--green); background: #FAFCF9;
  border-radius: 0 10px 10px 0; padding: 0.6rem 0.9rem; margin-top: 0.5rem;
}
.a-label { color: var(--green-dark); background: var(--green-bg); }

table.gian { width: 100%; border-collapse: separate; border-spacing: 0; background: #fff;
  font-size: 0.95rem; border: 1px solid var(--card-border); border-radius: 14px; overflow: hidden; }
table.gian th, table.gian td { border-bottom: 1px solid var(--card-border); padding: 0.55rem 0.7rem; text-align: left; vertical-align: top; }
table.gian tr:last-child td { border-bottom: none; }
table.gian th { background: var(--hover-bg); color: var(--text-2); font-size: 0.88rem; }
table.gian small { color: var(--muted); }
td.result { white-space: nowrap; font-weight: 700; }
td.result.ok { color: var(--green-dark); }
td.result.ng { color: #A32626; }
td.result.na { color: var(--muted); }

/* ---------- search ---------- */
.search-controls { display: flex; flex-wrap: wrap; gap: 1rem; margin: 1.2rem 0; }
.search-controls label { display: flex; flex-direction: column; font-weight: 600; flex: 1 1 14rem; font-size: 0.95rem; }
.search-controls select, .search-controls input {
  font-size: 1.05rem; padding: 0.6rem 0.8rem; border: 1px solid var(--divider);
  border-radius: 10px; margin-top: 0.3rem; background: var(--bg); color: var(--text);
}
.search-controls select:focus, .search-controls input:focus { outline: 2px solid var(--orange); border-color: var(--orange); }
.search-count { color: var(--muted); }

.council-box { margin: 1.2rem 0 1.6rem; }
.council-title { font-size: 1.15rem; margin: 0 0 0.5rem; border: none; padding: 0; }
.council-box p { margin: 0 0 0.5rem; }
.council-hint { color: var(--muted); font-size: 0.9rem; }
.member-chips { display: flex; flex-wrap: wrap; gap: 0.45rem; margin: 0.4rem 0 0.6rem; }
.member-chip {
  font: inherit; font-size: 0.95rem; font-weight: 600;
  background: var(--orange-bg); color: var(--orange-darker);
  border: 1px solid transparent; border-radius: 20px;
  padding: 0.15rem 0.9rem; cursor: pointer; transition: all 0.15s;
}
.member-chip:hover { border-color: var(--orange); transform: translateY(-1px); }
.member-chip.no-q {
  background: var(--hover-bg); color: var(--faint); cursor: default;
}
.member-chip.no-q:hover { border-color: transparent; transform: none; }

/* ---------- footer ---------- */
.site-footer { background: var(--footer-bg); margin-top: 4rem; padding: 2rem 1.2rem 2.5rem; }
.footer-inner { max-width: 46rem; margin: 0 auto; }
.footer-brand { color: #F0E8DC; font-weight: 700; display: flex; align-items: center; gap: 0.6rem; margin: 0 0 1rem; }
.footer-sub { color: #A89880; font-weight: 400; font-size: 0.85rem; }
.site-footer p { color: #C4B8A8; font-size: 0.95rem; margin: 0 0 0.6rem; }
.site-footer a { color: #F0C89A; }
.disclaimer { color: #F0E8DC !important; font-weight: 600; }

@media (max-width: 600px) {
  body { font-size: 16.5px; }
  .hero h1 { font-size: 1.65rem; }
  .stats { margin-top: -1.4rem; }
  .stat-num { font-size: 1.6rem; }
  table.gian { font-size: 0.88rem; }
  .site-header nav a { margin-left: 0.9rem; }
}
"""


def main() -> int:
    sessions = load_sessions()
    if not sessions:
        print("data/summaries/ に要約JSONがありません。")
        return 1

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "kaigi").mkdir(parents=True)
    (OUT_DIR / "assets").mkdir()
    (OUT_DIR / "data").mkdir()

    (OUT_DIR / "assets" / "style.css").write_text(STYLE, encoding="utf-8")
    if ASSETS_SRC.exists():
        for asset in ASSETS_SRC.iterdir():
            if asset.is_file() and not asset.name.startswith("."):
                shutil.copy2(asset, OUT_DIR / "assets" / asset.name)
    # 手書きの単体ページ（予算サンキー図など）と予算データをコピー
    if PAGES_SRC.exists():
        for page_file in PAGES_SRC.glob("*.html"):
            shutil.copy2(page_file, OUT_DIR / page_file.name)
    if BUDGET_DIR.exists():
        (OUT_DIR / "data" / "budget").mkdir(parents=True, exist_ok=True)
        for jf in BUDGET_DIR.glob("*.json"):
            shutil.copy2(jf, OUT_DIR / "data" / "budget" / jf.name)
    (OUT_DIR / "index.html").write_text(build_top(sessions), encoding="utf-8")

    for sess in sessions:
        (OUT_DIR / "kaigi" / f"{sess['slug']}.html").write_text(
            build_session_page(sess), encoding="utf-8")

    search_html, questions = build_search(sessions)
    (OUT_DIR / "search.html").write_text(search_html, encoding="utf-8")
    (OUT_DIR / "data" / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / ".nojekyll").write_text("")

    print(f"ビルド完了: 会期 {len(sessions)} / 一般質問 {len(questions)} 件")
    print(f"出力先: {OUT_DIR}")
    print("プレビュー: python3 -m http.server -d docs 8080")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
