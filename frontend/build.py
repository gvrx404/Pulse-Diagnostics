#!/usr/bin/env python3
"""Bundle App.jsx + React + CSS into ../ui/index.html (single offline file).
Needs Node.js. Run from anywhere:  python frontend/build.py

CSS pipeline: base.css -> derived light-mode rules (lightgen) -> refine.css -> themes.css (+ generated art) -> polish.css"""
import os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import art, lightgen
sh = sys.platform.startswith("win")
subprocess.check_call(["npm", "i", "--no-save", "--silent", "react@18.3.1", "react-dom@18.3.1", "esbuild@0.25.12"], cwd=here, shell=sh)
subprocess.check_call(["npx", "esbuild", "App.jsx", "--bundle", "--minify", "--format=iife", "--loader:.jsx=jsx",
                       "--define:process.env.NODE_ENV=\"production\"", "--outfile=bundle.js"], cwd=here, shell=sh)
js = open(os.path.join(here, "bundle.js"), encoding="utf-8").read().replace("</script", "<\\/script")
read = lambda n: open(os.path.join(here, n), encoding="utf-8").read()
base = read("base.css")
css = "\n".join([base, lightgen.generate(base), read("refine.css"), art.render(read("themes.css")), read("polish.css")])
out = os.path.join(here, "..", "ui"); os.makedirs(out, exist_ok=True)
html = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Pulse Diagnostics</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><style>' + css + '</style></head>'
        '<body><div id="root"></div>'
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>'
        '<script>' + read("qt_bridge.js").replace("</script", "<\\/script") + '</script>'
        '<script>' + js + '</script></body></html>')
open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)
print("built ui/index.html", len(html) // 1024, "KB")
