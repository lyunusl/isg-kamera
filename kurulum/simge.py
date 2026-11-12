"""kurulum/isg-kamera.ico dosyasını çizer (depoda ikili çizim tutulmasın diye): koyu zemin üstünde turuncu kalkan
ve beyaz baret. Panelin renkleriyle aynıdır (--ray #111318, --vurgu #ef4b1c).

  python kurulum/simge.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # büyük çizilip küçültülür: kenarlar yumuşak olsun
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((32, 32, S - 32, S - 32), radius=224, fill=(17, 19, 24))

# kalkan
ust, alt, sol, sag = 190, 860, 250, S - 250
orta = S // 2
kenar = [(sol, ust + 70), (orta, ust), (sag, ust + 70)]
for i in range(41):  # alt kenar: iki yandan uca dışa doğru bombeli (çeyrek elips)
    t = math.pi * i / 40
    x = orta + (sag - orta) * math.cos(t)
    y = 520 + (alt - 520) * math.sin(t)
    kenar.append((x, y))
d.polygon(kenar, fill=(239, 75, 28))

# baret: kubbe, siper ve tepe şeridi
beyaz = (255, 255, 255)
d.pieslice((330, 330, S - 330, 690), 180, 360, fill=beyaz)
d.rounded_rectangle((300, 498, S - 300, 548), radius=25, fill=beyaz)
d.rounded_rectangle((orta - 26, 330, orta + 26, 470), radius=20, fill=(239, 75, 28))

out = Path(__file__).with_name("isg-kamera.ico")
img.resize((256, 256), Image.LANCZOS).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
