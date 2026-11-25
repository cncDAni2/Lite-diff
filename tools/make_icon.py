"""A LiteDiff ikonjának előállítása (litediff/web/litediff.ico és .png).

Két egymás melletti, lekerekített panel: balra piros (törölt) sorok, jobbra zöld (új) sorok,
középen egy kis világoskék villám – „könnyű” diff.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "litediff" / "web"
S = 1024  # rajzolási méret, a végén kicsinyítjük


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # háttér: sötét, lekerekített négyzet
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=200, fill=(32, 36, 44, 255))
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=200, outline=(70, 130, 200, 255), width=18)

    # két panel
    panel_y0, panel_y1 = 210, 830
    left = (140, panel_y0, 480, panel_y1)
    right = (544, panel_y0, 884, panel_y1)
    for box in (left, right):
        d.rounded_rectangle(box, radius=48, fill=(24, 26, 31, 255))

    # sorok a panelekben
    def lines(box, rows):
        x0, y0, x1, _ = box
        y = y0 + 60
        for kind, length in rows:
            color = {"del": (229, 83, 83, 255), "add": (82, 200, 120, 255), "ctx": (110, 118, 130, 255)}[kind]
            if kind != "ctx":
                d.rounded_rectangle((x0 + 22, y - 30, x1 - 22, y + 30), radius=18,
                                    fill=tuple(int(24 * 0.7 + c * 0.3) for c in color[:3]) + (255,))
            d.rounded_rectangle((x0 + 48, y - 14, x0 + 48 + (x1 - x0 - 96) * length, y + 14), radius=14, fill=color)
            y += 92

    lines(left, [("ctx", .8), ("del", .65), ("del", .9), ("ctx", .55), ("ctx", .75), ("del", .5)])
    lines(right, [("ctx", .8), ("add", .9), ("add", .7), ("ctx", .55), ("ctx", .75), ("add", .85)])

    # villám a két panel között, felül
    bolt = [(560, 70), (420, 330), (505, 330), (450, 560), (620, 270), (530, 270), (600, 70)]
    bolt = [(x, y + 260) for x, y in bolt]
    d.polygon(bolt, fill=(255, 205, 60, 255), outline=(32, 36, 44, 255), width=22)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "litediff.png")
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.save(OUT / "litediff.ico", sizes=sizes)
    print("kész:", OUT / "litediff.ico")


if __name__ == "__main__":
    main()
