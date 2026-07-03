#!/usr/bin/env python3
"""OGPカード画像（1200×630）をサイトのデザインに合わせて生成する。

出力: assets_src/ogp.png（build_site.py が docs/assets/ にコピーする）
使い方: .venv/bin/python scripts/make_ogp.py
依存: Pillow、macOSのヒラギノ丸ゴフォント
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
FONT_PATH = "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc"

# サイトのデザイントークン
ORANGE = (232, 135, 60)
ORANGE_DARKER = (192, 104, 40)
GREEN_DARK = (61, 122, 80)
TEXT = (44, 36, 24)
TEXT_2 = (107, 93, 79)
GRAD_TOP = (255, 248, 240)     # #FFF8F0
GRAD_MID = (254, 243, 226)     # #FEF3E2
GRAD_BOTTOM = (240, 247, 237)  # #F0F7ED

OUT = Path(__file__).resolve().parent.parent / "assets_src" / "ogp.png"


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main():
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # ヒーローと同じ縦方向グラデーション（上40%まで TOP→MID、以降 MID→BOTTOM）
    for y in range(H):
        t = y / H
        color = lerp(GRAD_TOP, GRAD_MID, t / 0.4) if t < 0.4 \
            else lerp(GRAD_MID, GRAD_BOTTOM, (t - 0.4) / 0.6)
        draw.line([(0, y), (W, y)], fill=color)

    # 装飾の半透明円（ヒーローの浮遊円）
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse([950, -120, 1330, 260], fill=(245, 221, 191, 130))
    odraw.ellipse([820, 440, 1100, 720], fill=(217, 232, 210, 140))
    odraw.ellipse([-90, 470, 130, 690], fill=(245, 221, 191, 110))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    f_eyebrow = ImageFont.truetype(FONT_PATH, 34)
    f_title = ImageFont.truetype(FONT_PATH, 88)
    f_sub = ImageFont.truetype(FONT_PATH, 40)
    f_note = ImageFont.truetype(FONT_PATH, 28)
    f_logo = ImageFont.truetype(FONT_PATH, 64)

    x = 90
    # ロゴ（オレンジ角丸+「議」）とサイト名
    draw.rounded_rectangle([x, 80, x + 104, 184], radius=28, fill=ORANGE)
    draw.text((x + 52, 128), "議", font=f_logo, fill="white", anchor="mm")
    draw.text((x + 130, 108), "みんなの議事録", font=f_sub, fill=TEXT)
    draw.text((x + 132, 158), "周防大島町", font=f_eyebrow, fill=TEXT_2)

    # メインコピー
    draw.text((x, 250), "町の議論を、", font=f_title, fill=TEXT)
    draw.text((x, 360), "みんなの手に。", font=f_title, fill=TEXT)

    # サブコピー
    draw.text((x, 500), "議会の会議録を、AIがわかりやすいことばに要約", font=f_sub, fill=TEXT_2)

    # 下部の注記（緑のアクセント点付き）
    draw.ellipse([x, 574, x + 14, 588], fill=GREEN_DARK)
    draw.text((x + 26, 566), "非公式サイト ・ 正確な内容は原文の会議録をご確認ください",
              font=f_note, fill=TEXT_2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"生成完了: {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()
