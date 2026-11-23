"""Derive a light-mode stylesheet from the dark base stylesheet.

Every colour-bearing declaration of base.css is copied under #app[data-mode="light"]
with its lightness flipped (hue / saturation / alpha kept), so all ~200 components get
a consistent light variant without maintaining two hand-written stylesheets.
Shadows and filters are skipped on purpose: refine.css sets light-appropriate ones.
"""
import colorsys, re
from cssparse import split_rules

MODE = '#app[data-mode="light"]'
PROPS = re.compile(r'^(color|background(-color|-image)?|border(-[a-z]+)*|outline(-color)?|fill|stroke|'
                   r'accent-color|caret-color|column-rule|text-decoration-color|--[\w-]+)$')
HEX = re.compile(r'#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b')
RGB = re.compile(r'rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*(?:[,/]\s*([\d.%]+)\s*)?\)')
NAMED = {'white': '#000000', 'black': '#ffffff'}


def _flip(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    l = 1 - l
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return round(r2 * 255), round(g2 * 255), round(b2 * 255)


def _hex(m):
    h = m.group(1)
    if len(h) in (3, 4):
        h = ''.join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = h[6:8] if len(h) == 8 else ''
    return '#%02x%02x%02x%s' % (*_flip(r, g, b), a)


def _rgb(m):
    r, g, b = (float(m.group(i)) for i in (1, 2, 3))
    r2, g2, b2 = _flip(r, g, b)
    a = m.group(4)
    return 'rgba(%d,%d,%d,%s)' % (r2, g2, b2, a) if a else 'rgb(%d,%d,%d)' % (r2, g2, b2)


def flip_value(v):
    if 'url(' in v:
        return v
    v = HEX.sub(_hex, v)
    v = RGB.sub(_rgb, v)
    return re.sub(r'\b(white|black)\b', lambda m: NAMED[m.group(1)], v)


def _split_top(s, sep=','):
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch in '([':
            depth += 1
        elif ch in ')]':
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur); cur = ''
        else:
            cur += ch
    out.append(cur)
    return out


def _selector(sel):
    sel = sel.strip()
    if sel.startswith(':root') or sel == '#app':
        return MODE + sel.replace(':root', '').replace('#app', '', 1)
    if sel.startswith('#app'):
        sel = re.sub(r'^#app(\[data-theme\])?', '', sel)
        return MODE + sel
    if re.match(r'^(html|body|#root|\*)', sel):
        return None
    return MODE + ' ' + sel


def _decls(body):
    out = []
    for d in _split_top(body, ';'):
        if ':' not in d:
            continue
        k, v = d.split(':', 1)
        k, v = k.strip(), v.strip()
        if not PROPS.match(k):
            continue
        nv = flip_value(v)
        if nv != v:
            out.append('%s:%s' % (k, nv))
    return ';'.join(out)


def generate(css):
    out = []
    for pre, body in split_rules(css):
        if pre.startswith('@keyframes') or pre.startswith('@font-face') or pre.startswith('@import'):
            continue
        if pre.startswith('@media'):
            inner = []
            for p, b in split_rules(body):
                sels = [s for s in (_selector(x) for x in _split_top(p)) if s]
                d = _decls(b)
                if sels and d:
                    inner.append('%s{%s}' % (','.join(sels), d))
            if inner:
                out.append('%s{%s}' % (pre, ''.join(inner)))
            continue
        sels = [s for s in (_selector(x) for x in _split_top(pre)) if s]
        d = _decls(body)
        if sels and d:
            out.append('%s{%s}' % (','.join(sels), d))
    return '\n'.join(out)


if __name__ == '__main__':
    import sys
    print(generate(open(sys.argv[1], encoding='utf-8').read()))
