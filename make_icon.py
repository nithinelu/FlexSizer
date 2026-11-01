"""Draw the FlexSizer icon (app.ico): navy tile, three rising bars in the licence-type colours.

Run: py make_icon.py app   (writes app.ico and app.png)
"""
import sys
from PIL import Image, ImageDraw

S = 1024                      # drawn large, then downsampled for clean edges
NAVY, CYAN = "#0B2A4A", "#049FD9"
BARS = ["#049FD9", "#14A38B", "#7B5CD6"]


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, r = 40, 200
    d.rounded_rectangle((pad, pad, S - pad, S - pad), radius=r, fill=NAVY)
    # Baseline and three rising rounded bars.
    left, base, w, gap = 236, 790, 150, 52
    heights = [250, 400, 560]
    for i, (h, col) in enumerate(zip(heights, BARS)):
        x = left + i * (w + gap)
        d.rounded_rectangle((x, base - h, x + w, base), radius=34, fill=col)
    d.rounded_rectangle((210, base + 30, S - 210, base + 62), radius=16, fill="#FFFFFF")
    # Upward accent arrow over the bars.
    d.line([(236, 470), (440, 330), (560, 400), (790, 205)], fill="#FFFFFF", width=44, joint="curve")
    d.polygon([(820, 180), (700, 200), (790, 290)], fill="#FFFFFF")
    return img


big = draw()
out = sys.argv[1]
big.resize((256, 256), Image.LANCZOS).save(out + ".ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
                                                                   (48, 48), (64, 64), (128, 128), (256, 256)])
big.resize((256, 256), Image.LANCZOS).save(out + ".png")
