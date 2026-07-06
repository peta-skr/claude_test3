"""Generate the demo pages and screenshots shown in the README.

Usage::

    python examples/demo.py            # writes PNGs into docs/

Everything here uses only pybrowser -- no external tools produce the images.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, ROOT)  # run without installing the package

from pybrowser.browser import Browser  # noqa: E402

DEMO_PAGE = """<!doctype html>
<html>
<head>
  <title>pybrowser demo</title>
  <style>
    body { background: #ffffff; margin: 16px; color: #202124; }
    h1 { color: #1a73e8; }
    h2 { color: #188038; }
    .card {
      background: #f1f6ff; border: 2px solid #1a73e8;
      padding: 12px; margin-top: 12px; margin-bottom: 12px;
    }
    .warn { background: #fff4e5; border: 2px solid #f9a825; padding: 10px; }
    a { color: #1a0dab; }
    .muted { color: #5f6368; }
    blockquote { color: #444; }
  </style>
</head>
<body>
  <h1>Hello from pybrowser</h1>
  <p>This whole page &mdash; parsing, styling, layout and every pixel below
     &mdash; is produced by a browser written <b>from scratch</b> on the
     Python standard library. <i>No engines, no frameworks.</i></p>

  <h2>What it does</h2>
  <ul>
    <li>Fetches pages over its own HTTP/HTTPS stack</li>
    <li>Parses HTML into a DOM and CSS into a cascade</li>
    <li>Lays out block &amp; inline boxes with word wrapping</li>
    <li>Rasterizes to a real PNG with a built-in bitmap font</li>
  </ul>

  <div class="card">
    <p>Styled boxes work: backgrounds, borders and padding are honoured.
       Follow a <a href="about:version">link</a> to navigate.</p>
  </div>

  <div class="warn">
    <p class="muted">Colors: <span style="color:#d93025">red</span>,
       <span style="color:#188038">green</span>,
       <span style="color:#1a73e8">blue</span> and inline
       <b>bold</b> / <i>italic</i> runs.</p>
  </div>

  <blockquote><p>&ldquo;The best way to understand a browser is to
     build one.&rdquo;</p></blockquote>
</body>
</html>"""


def main() -> None:
    os.makedirs(DOCS, exist_ok=True)

    # 1. Full browser window (chrome + content) with two tabs.
    browser = Browser(width=760, height=560)
    browser.new_tab("about:home")
    data_url = "data:text/html," + DEMO_PAGE.replace("\n", "").replace("#", "%23")
    browser.new_tab(data_url)
    browser.tabs[1].title = "pybrowser demo"
    browser.switch_tab(1)
    window = browser.screenshot()
    window.save_png(os.path.join(DOCS, "screenshot-window.png"))
    print("wrote docs/screenshot-window.png",
          f"[{window.width}x{window.height}]")

    # 2. Full-page content render (no chrome), tall capture.
    tab = browser.tabs[1]
    from pybrowser.raster import Canvas

    page = Canvas(760, int(tab.content_height) + 16, (255, 255, 255))
    tab.paint(page)
    page.save_png(os.path.join(DOCS, "screenshot-page.png"))
    print("wrote docs/screenshot-page.png", f"[{page.width}x{page.height}]")

    # 3. The built-in about:home start page.
    home = Browser(width=760, height=420)
    home.new_tab("about:home")
    home.screenshot().save_png(os.path.join(DOCS, "screenshot-home.png"))
    print("wrote docs/screenshot-home.png")


if __name__ == "__main__":
    main()
