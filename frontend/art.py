"""Original, procedurally generated artwork for the anime themes (no external images).

Each function returns a CSS url("data:image/svg+xml,...") value. Output is deterministic.
"""
import random
from urllib.parse import quote


def _url(svg):
    return 'url("data:image/svg+xml,%s")' % quote(svg.strip(), safe="")


def petals(w=520, h=520, seed=7):
    r = random.Random(seed)
    petal = "M0 0C-9-5-9-17-2.6-21.5L0-17.5L2.6-21.5C9-17 9-5 0 0Z"
    out = []
    for _ in range(11):
        x, y, a, s = r.uniform(10, w - 10), r.uniform(10, h - 10), r.uniform(0, 360), r.uniform(.55, 1.15)
        c = r.choice(['#ffb3cf', '#ffc9dc', '#ff9dc2', '#ffd9e6'])
        out.append("<path d='%s' transform='translate(%.0f %.0f) rotate(%.0f) scale(%.2f)' fill='%s' fill-opacity='.78'/>" % (petal, x, y, a, s, c))
    for _ in range(4):  # full five-petal blossoms
        x, y, s = r.uniform(30, w - 30), r.uniform(30, h - 30), r.uniform(.9, 1.5)
        g = ''.join("<path d='%s' transform='rotate(%d)' fill='#ffc2d9' fill-opacity='.85'/>" % (petal, 72 * i) for i in range(5))
        out.append("<g transform='translate(%.0f %.0f) scale(%.2f) rotate(%.0f)'>%s<circle r='2.4' fill='#ff7fae'/></g>" % (x, y, s, r.uniform(0, 72), g))
    return _url("<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' viewBox='0 0 %d %d'>%s</svg>" % (w, h, w, h, ''.join(out)))


def sparkles(w=560, h=560, seed=11):
    r = random.Random(seed)
    star = "M0-10Q1.4-1.4 10 0Q1.4 1.4 0 10Q-1.4 1.4-10 0Q-1.4-1.4 0-10Z"
    out = []
    for _ in range(13):
        x, y, s = r.uniform(12, w - 12), r.uniform(12, h - 12), r.uniform(.45, 1.7)
        c = r.choice(['#ffffff', '#ffb8de', '#d3bfff', '#ffe3f2'])
        out.append("<path d='%s' transform='translate(%.0f %.0f) scale(%.2f)' fill='%s' fill-opacity='%.2f'/>" % (star, x, y, s, c, r.uniform(.5, .95)))
    for _ in range(26):
        out.append("<circle cx='%.0f' cy='%.0f' r='%.1f' fill='#ffffff' fill-opacity='%.2f'/>" % (r.uniform(0, w), r.uniform(0, h), r.uniform(.6, 1.6), r.uniform(.25, .7)))
    return _url("<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' viewBox='0 0 %d %d'>%s</svg>" % (w, h, w, h, ''.join(out)))


def skyline(w=1280, h=300, seed=3, far=False):
    r = random.Random(seed + (50 if far else 0))
    base = '#13235a' if far else '#0a1536'
    out, x = [], 0
    while x < w:
        bw = r.randint(34, 96)
        bh = r.randint(60, 150) if far else r.randint(80, 250)
        top = h - bh
        out.append("<rect x='%d' y='%d' width='%d' height='%d' fill='%s'/>" % (x, top, bw, bh, base))
        if r.random() < .3:  # antenna
            out.append("<rect x='%d' y='%d' width='2' height='%d' fill='%s'/>" % (x + bw // 2, top - r.randint(14, 36), 36, base))
        if not far:
            for wy in range(top + 10, h - 8, 14):
                for wx in range(x + 8, x + bw - 8, 12):
                    if r.random() < .17:
                        c = r.choice(['#ffd166', '#6ad7ff', '#ffb347', '#a6e1ff'])
                        out.append("<rect x='%d' y='%d' width='4' height='6' fill='%s' fill-opacity='%.2f'/>" % (wx, wy, c, r.uniform(.7, 1)))
        x += bw + r.randint(0, 6)
    return _url("<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' viewBox='0 0 %d %d' preserveAspectRatio='xMidYMax slice'>%s</svg>" % (w, h, w, h, ''.join(out)))


def render(text):
    return (text.replace('{{PETALS}}', petals()).replace('{{SPARKLES}}', sparkles())
            .replace('{{SKYLINE}}', skyline()).replace('{{SKYLINE_FAR}}', skyline(far=True)))


if __name__ == '__main__':
    for f in (petals, sparkles, skyline):
        print(f.__name__, len(f()))
