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


def _logo_data_url() -> str:
    """Draw a small logo with pybrowser's own rasterizer and inline it."""
    import base64

    from pybrowser.fonts import Font
    from pybrowser.raster import Canvas

    c = Canvas(96, 64, (250, 250, 250))
    c.fill_rect(0, 0, 96, 64, (26, 115, 232))
    c.fill_rect(8, 8, 80, 48, (255, 255, 255))
    c.fill_rect(16, 16, 26, 32, (234, 67, 53))
    c.fill_rect(46, 16, 26, 32, (52, 168, 83))
    c.draw_text(14, 50, "pyb", Font(11, "bold"), (26, 115, 232))
    return "data:image/png;base64," + base64.b64encode(c.to_png_bytes()).decode()


FEATURES_PAGE = """<!doctype html>
<html>
<head>
  <title>pybrowser features</title>
  <style>
    body {{ margin: 16px; color: #202124; }}
    h1 {{ color: #1a73e8; }}
    h2 {{ color: #188038; }}
    table {{ width: 100%; margin-top: 10px; }}
    th {{ background: #e8f0fe; }}
    td {{ background: #ffffff; }}
    .num {{ text-align: right; }}
    .banner {{ background: #f1f6ff; border: 2px solid #1a73e8; padding: 10px; }}
  </style>
</head>
<body>
  <h1>Now with images &amp; tables</h1>
  <div class="banner">
    <p><img src="{logo}" width="64"> Inline images decode through
       pybrowser's own PNG decoder and composite with alpha.</p>
  </div>
  <h2>A real table</h2>
  <table>
    <tr><th>Feature</th><th>Module</th><th>Lines</th></tr>
    <tr><td>PNG decoder</td><td>image.py</td><td class="num">~180</td></tr>
    <tr><td>Table layout</td><td>layout.py</td><td class="num">~90</td></tr>
    <tr><td>Hit testing</td><td>browser.py</td><td class="num">~30</td></tr>
  </table>
  <hr>
  <p>Plus <code>width</code>/<code>margin:auto</code> centering,
     <code>text-decoration</code>, and clickable-link hit testing.</p>
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

    # 4. The features showcase: images + a table, in a full browser window.
    features_html = FEATURES_PAGE.format(logo=_logo_data_url())
    feat = Browser(width=760, height=560)
    feat.new_tab("about:blank")
    feat.tab.load("data:text/html," + features_html.replace("#", "%23"))
    feat.tab.title = "pybrowser features"
    feat.screenshot().save_png(os.path.join(DOCS, "screenshot-features.png"))
    print("wrote docs/screenshot-features.png")

    # 5. JavaScript-generated content.
    _render_page(JS_PAGE, "screenshot-js.png", 620)
    # 6. Form controls.
    _render_page(FORM_PAGE, "screenshot-forms.png", 560)
    # 7. A baseline JPEG decoded from scratch.
    _render_page(JPEG_PAGE.format(photo=_jpeg_data_url()), "screenshot-jpeg.png", 440)


def _render_page(html: str, name: str, width: int) -> None:
    from pybrowser.browser import Tab
    from pybrowser.raster import Canvas

    tab = Tab(width=width)
    tab.load("data:text/html," + html.replace("#", "%23"))
    page = Canvas(width, int(tab.content_height) + 12, (255, 255, 255))
    tab.paint(page)
    page.save_png(os.path.join(DOCS, name))
    print(f"wrote docs/{name}", f"[{page.width}x{page.height}]")


def _jpeg_data_url() -> str:
    import base64

    from pybrowser.fonts import Font
    from pybrowser.jpeg import encode_canvas
    from pybrowser.raster import Canvas

    c = Canvas(140, 90, (250, 250, 250))
    for y in range(90):
        for x in range(140):
            c.set_pixel(x, y, (110 + x, min(255, 150 + y // 2), 220))
    c.fill_rect(20, 44, 34, 34, (200, 60, 50))
    c.fill_rect(82, 34, 40, 44, (40, 150, 80))
    c.draw_text(8, 8, "JPEG", Font(14, "bold"), (20, 20, 20))
    return "data:image/jpeg;base64," + base64.b64encode(
        encode_canvas(c, quality=85)).decode()


JS_PAGE = """<!doctype html><html><head><title>JS</title><style>
body{margin:14px} h1{color:#1a73e8} li{color:#188038} .hot{color:#d93025;font-weight:bold}
</style></head><body>
<h1 id="title">Loading...</h1>
<ul id="list"></ul>
<p id="out"></p>
<script>
  document.getElementById("title").textContent = "Generated by JavaScript";
  var list = document.getElementById("list");
  var items = ["parse", "style", "layout", "paint"];
  for (var i = 0; i < items.length; i++) {
    var li = document.createElement("li");
    li.textContent = (i + 1) + ". " + items[i].toUpperCase();
    list.appendChild(li);
  }
  function sum(n){ var s = 0; for (var k = 1; k <= n; k++) s += k; return s; }
  var out = document.getElementById("out");
  out.textContent = "sum(1..100) = " + sum(100);
  out.setAttribute("class", "hot");
</script>
</body></html>"""

FORM_PAGE = """<!doctype html><html><head><title>Form</title><style>
body{margin:14px} h1{color:#1a73e8} p{margin-top:8px}
</style></head><body>
<h1>Sign up</h1>
<p>Name: <input type="text" value="Ada Lovelace" size="18"></p>
<p>Email: <input type="text" placeholder="you@example.com" size="18"></p>
<p>Password: <input type="password" value="secret"></p>
<p><input type="checkbox" checked> Remember me
   <input type="checkbox"> Send news</p>
<p>Plan: <input type="radio" checked> Free <input type="radio"> Pro</p>
<p>Country:
<select><option>Japan</option><option selected>United Kingdom</option></select></p>
<p>Bio:<br><textarea rows="3" cols="30">Countess of Lovelace.</textarea></p>
<p><button>Cancel</button> <input type="submit" value="Create account"></p>
</body></html>"""

JPEG_PAGE = """<!doctype html><html><head><title>JPEG</title><style>
body{{margin:14px}} h1{{color:#1a73e8}}
</style></head><body>
<h1>JPEG decoding</h1>
<p>A real baseline JPEG, decoded from scratch and scaled 2x:</p>
<img src="{photo}" width="280">
</body></html>"""


if __name__ == "__main__":
    main()
