"""Draws installer/isg-kamera.ico (so no binary drawing is kept in the repository): an orange shield and a white hard hat
on a dark background. Same colors as the panel (background #111318, accent #ef4b1c).

  python installer/make_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # drawn large and scaled down so the edges are smooth
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((32, 32, S - 32, S - 32), radius=224, fill=(17, 19, 24))

# shield
top, bottom, left, right = 190, 860, 250, S - 250
mid = S // 2
edge = [(left, top + 70), (mid, top), (right, top + 70)]
for i in range(41):  # bottom edge: bulging outward from both sides to the tip (quarter ellipses)
    t = math.pi * i / 40
    x = mid + (right - mid) * math.cos(t)
    y = 520 + (bottom - 520) * math.sin(t)
    edge.append((x, y))
d.polygon(edge, fill=(239, 75, 28))

# hard hat: dome, brim and top ridge
white = (255, 255, 255)
d.pieslice((330, 330, S - 330, 690), 180, 360, fill=white)
d.rounded_rectangle((300, 498, S - 300, 548), radius=25, fill=white)
d.rounded_rectangle((mid - 26, 330, mid + 26, 470), radius=20, fill=(239, 75, 28))

out = Path(__file__).with_name("isg-kamera.ico")
img.resize((256, 256), Image.LANCZOS).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
