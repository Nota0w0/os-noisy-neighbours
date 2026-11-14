#!/usr/bin/env python3
"""Crop the demo screenshots (1600x1260, demo/screenshots/) into slide-sized pieces.
usage: python3 demo/crop_slides.py demo/screenshots demo/screenshots/slides   (needs Pillow)"""
import sys, pathlib
from PIL import Image, ImageDraw, ImageFont

SRC, OUT = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
X0, X1 = 62, 1538                       # card x range 70-1530 plus margin
BOX = {'tiles': (196, 356), 'charts': (358, 1024), 'throughput': (378, 602), 'cpumap': (1027, 1218)}
BG = (245, 244, 241)
FONT = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 26)

def crop(name, part):
    y0, y1 = BOX[part]
    return Image.open(SRC / f'{name}.png').convert('RGB').crop((X0, y0, X1, y1))

def stack(items, out, label_w=250):
    """items: [(label, image)] stacked vertically, label column on the left"""
    w = label_w + max(im.width for _, im in items); h = sum(im.height for _, im in items) + 12 * (len(items) - 1)
    canvas = Image.new('RGB', (w, h), BG); d = ImageDraw.Draw(canvas); y = 0
    for label, im in items:
        canvas.paste(im, (label_w, y))
        lines = label.split('\n'); lh = 34; ty = y + (im.height - lh * len(lines)) // 2
        for i, line in enumerate(lines):
            d.text((24, ty + i * lh), line, font=FONT, fill=(30, 30, 30))
        y += im.height + 12
    canvas.save(OUT / out, optimize=True)

shots = sorted(p.stem for p in SRC.glob('[0-9][0-9]-*.png'))
for s in shots:                          # every screenshot: tiles / charts / CPU map
    for part in ('tiles', 'charts', 'cpumap'):
        crop(s, part).save(OUT / f'{s}_{part}.png', optimize=True)

# slide 1: the effect (tiles + throughput, default n = 2)
a, b = crop('03-default-n2', 'tiles'), crop('03-default-n2', 'throughput')
im = Image.new('RGB', (a.width, a.height + b.height), BG); im.paste(a, (0, 0)); im.paste(b, (0, a.height))
im.save(OUT / 'A-effect_default-n2_tiles+throughput.png', optimize=True)
# slide 2: the cause (CPU map, n = 1 vs n = 2)
stack([('n = 1\nno overlap', crop('02-default-n1', 'cpumap')), ('n = 2\nCPU 2 shared', crop('03-default-n2', 'cpumap'))],
      'B-cause_cpumap_n1-vs-n2.png')
# slide 3: the three policies at n = 8 (tiles)
stack([('default\nn = 8', crop('05-default-n8', 'tiles')), ('dedicated\nn = 8', crop('08-dedicated-n8', 'tiles')),
       ('float\nn = 8', crop('10-float-n8', 'tiles'))], 'C-policies-n8_tiles.png')
# slide 4: timeline (default): n = 1 -> 2 (collapse) -> 4, then n = 4 -> 8
crop('04-default-n4', 'throughput').save(OUT / 'D1-timeline_default-n1-n2-n4_throughput.png', optimize=True)
crop('05-default-n8', 'throughput').save(OUT / 'D2-timeline_default-n4-n8_throughput.png', optimize=True)
print(len(list(OUT.glob('*.png'))), 'files in', OUT)
