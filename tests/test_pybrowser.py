"""Unit tests for the pybrowser engine.

Run with::

    python -m unittest discover -s tests

No third-party test runner is required.
"""

import struct
import unittest

from pybrowser.browser import CHROME_HEIGHT, Browser, Tab
from pybrowser.css import (CSSParser, ClassSelector, DescendantSelector,
                           IdSelector, TagSelector, default_rules,
                           extract_stylesheets, style)
from pybrowser.dom import Element, Text
from pybrowser.fonts import Font, glyph_rows
from pybrowser.html_parser import decode_entities, parse_html
from pybrowser.image import Bitmap, decode_png
from pybrowser.layout import DocumentLayout, TableLayout, collect_links
from pybrowser.paint import DrawImage, DrawLine, DrawText
from pybrowser.raster import Canvas, parse_color
from pybrowser.url import URL


def render(html, width=480):
    root = parse_html(html)
    rules = default_rules() + CSSParser(extract_stylesheets(root)).parse()
    style(root, rules)
    doc = DocumentLayout(root, width)
    doc.layout()
    return root, doc


class TestHTMLParser(unittest.TestCase):
    def test_basic_tree(self):
        root = parse_html("<p>hello</p>")
        self.assertEqual(root.tag, "html")
        # html > body > p > text
        p = next(n for n in root if isinstance(n, Element) and n.tag == "p")
        self.assertIsInstance(p.children[0], Text)
        self.assertEqual(p.children[0].text, "hello")

    def test_attributes(self):
        root = parse_html('<a href="http://x" class="a b">y</a>')
        a = next(n for n in root if isinstance(n, Element) and n.tag == "a")
        self.assertEqual(a.attributes["href"], "http://x")
        self.assertEqual(a.attributes["class"], "a b")

    def test_void_element(self):
        root = parse_html("<p>a<br>b</p>")
        p = next(n for n in root if isinstance(n, Element) and n.tag == "p")
        tags = [c.tag for c in p.children if isinstance(c, Element)]
        self.assertIn("br", tags)
        # <br> must not swallow following text.
        texts = [c.text for c in p.children if isinstance(c, Text)]
        self.assertEqual(texts, ["a", "b"])

    def test_implied_paragraph_close(self):
        root = parse_html("<p>a<p>b")
        body = next(n for n in root if isinstance(n, Element) and n.tag == "body")
        ps = [c for c in body.children if isinstance(c, Element) and c.tag == "p"]
        self.assertEqual(len(ps), 2)  # siblings, not nested

    def test_implicit_html_head_body(self):
        root = parse_html("<title>t</title><p>x")
        tags = {n.tag for n in root if isinstance(n, Element)}
        self.assertTrue({"html", "head", "body", "title", "p"}.issubset(tags))

    def test_entities(self):
        self.assertEqual(decode_entities("a&amp;b&lt;c&#65;&#x42;"), "a&b<cAB")
        self.assertEqual(decode_entities("x&mdash;y"), "x—y")


class TestCSS(unittest.TestCase):
    def test_specificity_order(self):
        self.assertLess(TagSelector("p").priority, ClassSelector("c").priority)
        self.assertLess(ClassSelector("c").priority, IdSelector("i").priority)

    def test_cascade_and_inheritance(self):
        html = ("<style>p{color:red} #a p{color:green}</style>"
                "<div id=a><p><b>hi</b></p></div>")
        root, _ = render(html)
        p = next(n for n in root if isinstance(n, Element) and n.tag == "p")
        b = next(n for n in root if isinstance(n, Element) and n.tag == "b")
        self.assertEqual(p.style["color"], "green")  # id selector wins
        self.assertEqual(b.style["color"], "green")  # inherited by child

    def test_inline_style_wins(self):
        html = "<style>p{color:red}</style><p style='color:blue'>x</p>"
        root, _ = render(html)
        p = next(n for n in root if isinstance(n, Element) and n.tag == "p")
        self.assertEqual(p.style["color"], "blue")

    def test_class_and_descendant_selectors(self):
        cls = ClassSelector("note")
        el = Element("p", {"class": "note extra"})
        self.assertTrue(cls.matches(el))
        outer = Element("div", {"id": "x"})
        inner = Element("span", {}, outer)
        outer.children.append(inner)
        sel = DescendantSelector(IdSelector("x"), TagSelector("span"))
        self.assertTrue(sel.matches(inner))

    def test_malformed_css_does_not_crash(self):
        rules = CSSParser("p{color:red;;} garbage @media{} q{").parse()
        self.assertTrue(any(isinstance(s, TagSelector) for s, _ in rules))


class TestURL(unittest.TestCase):
    def test_http_parse(self):
        u = URL("http://example.com:8080/a/b?c=d")
        self.assertEqual(u.scheme, "http")
        self.assertEqual(u.host, "example.com")
        self.assertEqual(u.port, 8080)
        self.assertEqual(u.path, "/a/b?c=d")

    def test_https_default_port(self):
        self.assertEqual(URL("https://x.test/").port, 443)

    def test_data_scheme(self):
        headers, body = URL("data:text/html,<h1>Hi</h1>").request()
        self.assertEqual(body, "<h1>Hi</h1>")
        self.assertEqual(headers["content-type"], "text/html")

    def test_about_scheme(self):
        headers, body = URL("about:version").request()
        self.assertIn("pybrowser", body)

    def test_resolve_relative(self):
        base = URL("http://example.com/dir/page.html")
        self.assertEqual(str(base.resolve("other.html")),
                         "http://example.com/dir/other.html")
        self.assertEqual(str(base.resolve("/root.html")),
                         "http://example.com/root.html")
        self.assertEqual(str(base.resolve("http://other/x")),
                         "http://other/x")


class TestFontsAndRaster(unittest.TestCase):
    def test_font_metrics(self):
        f = Font(16)
        self.assertGreater(f.char_width, 0)
        self.assertEqual(f.measure("abc"), 3 * f.char_width)
        self.assertGreater(Font(32).char_width, Font(16).char_width)

    def test_glyph_rows_shape(self):
        rows = glyph_rows("A")
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(isinstance(r, int) for r in rows))

    def test_parse_color(self):
        self.assertEqual(parse_color("#ff0000"), (255, 0, 0))
        self.assertEqual(parse_color("#0f0"), (0, 255, 0))
        self.assertEqual(parse_color("red"), (255, 0, 0))
        self.assertEqual(parse_color("rgb(1,2,3)"), (1, 2, 3))
        self.assertEqual(parse_color("nonsense", (7, 7, 7)), (7, 7, 7))

    def test_fill_and_read_pixel(self):
        c = Canvas(4, 4, (255, 255, 255))
        c.fill_rect(1, 1, 2, 2, (10, 20, 30))
        i = (1 * 4 + 1) * 3
        self.assertEqual(tuple(c.pixels[i:i + 3]), (10, 20, 30))

    def test_png_is_valid(self):
        c = Canvas(8, 5, (255, 255, 255))
        c.draw_text(0, 0, "Hi", Font(8), (0, 0, 0))
        png = c.to_png_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h, depth, ctype = struct.unpack(">IIBB", png[16:26])
        self.assertEqual((w, h, depth, ctype), (8, 5, 8, 2))
        # IEND terminates the file.
        self.assertEqual(png[-12:], b"\x00\x00\x00\x00IEND\xaeB`\x82")

    def test_blit(self):
        dst = Canvas(6, 6, (255, 255, 255))
        src = Canvas(2, 2, (9, 9, 9))
        dst.blit(src, 1, 1)
        i = (1 * 6 + 1) * 3
        self.assertEqual(tuple(dst.pixels[i:i + 3]), (9, 9, 9))


class TestLayout(unittest.TestCase):
    def test_word_wrap_produces_multiple_lines(self):
        words = " ".join(["word"] * 60)
        _, doc = render(f"<body><p>{words}</p></body>", width=300)
        texts = [c for c in doc.display_list if isinstance(c, DrawText)]
        tops = {c.top for c in texts}
        self.assertGreater(len(tops), 1)

    def test_block_stacks_vertically(self):
        _, doc = render("<body><p>one</p><p>two</p></body>", width=400)
        texts = sorted((c for c in doc.display_list if isinstance(c, DrawText)),
                       key=lambda c: c.top)
        self.assertEqual(texts[0].text, "one")
        self.assertLess(texts[0].top, texts[-1].top)

    def test_heading_is_larger(self):
        _, doc = render("<body><h1>Big</h1><p>small</p></body>")
        big = next(c for c in doc.display_list
                   if isinstance(c, DrawText) and c.text == "Big")
        small = next(c for c in doc.display_list
                     if isinstance(c, DrawText) and c.text == "small")
        self.assertGreater(big.font.size, small.font.size)

    def test_display_none_hidden(self):
        _, doc = render("<body><p>shown</p><p style='display:none'>hidden</p></body>")
        texts = [c.text for c in doc.display_list if isinstance(c, DrawText)]
        self.assertIn("shown", texts)
        self.assertNotIn("hidden", texts)

    def test_collect_links(self):
        root, _ = render("<body><a href='/x'>X</a><a href='/y'>Y</a></body>")
        links = collect_links(root)
        self.assertEqual(links, [("/x", "X"), ("/y", "Y")])


class TestBrowser(unittest.TestCase):
    def test_tab_loads_title(self):
        tab = Tab()
        tab.load("data:text/html,<title>Doc</title><p>hi</p>")
        self.assertEqual(tab.title, "Doc")

    def test_history_back_forward(self):
        tab = Tab()
        tab.load("about:home")
        tab.load("about:version")
        self.assertTrue(tab.go_back())
        self.assertEqual(str(tab.url), "about:home")
        self.assertTrue(tab.go_forward())
        self.assertEqual(str(tab.url), "about:version")
        self.assertFalse(tab.go_forward())

    def test_follow_link(self):
        tab = Tab()
        tab.load("data:text/html,<a href='about:version'>v</a>")
        self.assertTrue(tab.follow_link(0))
        self.assertIn("version", str(tab.url))

    def test_error_page_does_not_raise(self):
        tab = Tab()
        tab.load("file:///no/such/path/at/all.html")
        self.assertIn("working", tab.title.lower() + tab.render_text().lower())

    def test_screenshot_dimensions_and_chrome(self):
        b = Browser(320, 240)
        b.new_tab("about:home")
        canvas = b.screenshot()
        self.assertEqual((canvas.width, canvas.height), (320, 240))
        # The chrome bar is not white at the top-left corner.
        self.assertNotEqual(tuple(canvas.pixels[0:3]), (255, 255, 255))
        self.assertGreater(CHROME_HEIGHT, 0)

    def test_tab_management(self):
        b = Browser()
        b.new_tab("about:home")
        b.new_tab("about:version")
        self.assertEqual(len(b.tabs), 2)
        b.switch_tab(0)
        self.assertEqual(b.active, 0)
        b.close_tab(0)
        self.assertEqual(len(b.tabs), 1)


class TestImages(unittest.TestCase):
    def _make_png(self, w=6, h=4, color=(200, 30, 40)):
        c = Canvas(w, h, (255, 255, 255))
        c.fill_rect(1, 1, w - 2, h - 2, color)
        return c

    def test_png_roundtrip(self):
        c = self._make_png()
        bm = decode_png(c.to_png_bytes())
        self.assertIsInstance(bm, Bitmap)
        self.assertEqual((bm.width, bm.height), (6, 4))
        # Interior pixel matches the fill colour.
        self.assertEqual(bm.pixel(2, 2)[:3], (200, 30, 40))

    def test_decode_rejects_non_png(self):
        self.assertIsNone(decode_png(b"not a png at all"))

    def test_draw_image_alpha_composites(self):
        # A half-transparent red over white -> pinkish.
        rgb = bytes([255, 0, 0])
        bm = Bitmap(1, 1, rgb, alpha=bytes([128]))
        canvas = Canvas(2, 2, (255, 255, 255))
        canvas.draw_image(bm, 0, 0, 2, 2)
        r, g, b = canvas.pixels[0:3]
        self.assertTrue(r > g and r > b and g > 0)  # blended, not pure red

    def test_img_element_renders(self):
        c = self._make_png(8, 6, (10, 20, 250))
        import base64
        data_url = "data:image/png;base64," + base64.b64encode(
            c.to_png_bytes()).decode()
        tab = Tab(width=300)
        tab.load(f"data:text/html,<body><img src='{data_url}' width='40'></body>")
        imgs = [cmd for cmd in tab.document.display_list
                if isinstance(cmd, DrawImage)]
        self.assertEqual(len(imgs), 1)
        self.assertEqual(imgs[0].dw, 40)          # honoured the width attr
        self.assertEqual(imgs[0].dh, 30)          # kept 8:6 aspect ratio

    def test_broken_image_placeholder(self):
        tab = Tab(width=300)
        tab.load("data:text/html,<body><img src='nope.png' alt='X' "
                 "width='60' height='20'></body>")
        # No DrawImage, but the alt text is painted as a placeholder.
        self.assertFalse(any(isinstance(c, DrawImage)
                             for c in tab.document.display_list))
        self.assertTrue(any(isinstance(c, DrawText) and c.text == "X"
                            for c in tab.document.display_list))


class TestTables(unittest.TestCase):
    def test_table_produces_grid(self):
        html = ("<body><table>"
                "<tr><th>A</th><th>B</th></tr>"
                "<tr><td>1</td><td>2</td></tr>"
                "</table></body>")
        root, doc = render(html, width=400)
        table = _find_table(doc)
        self.assertIsNotNone(table)
        # 4 cells laid out as boxes.
        self.assertEqual(len(table.children), 4)
        # Header cells sit on one row, data cells below.
        tops = sorted({int(c.y) for c in table.children})
        self.assertEqual(len(tops), 2)
        # Two columns side by side (distinct x on the first row).
        first_row = [c for c in table.children if int(c.y) == tops[0]]
        self.assertEqual(len({int(c.x) for c in first_row}), 2)

    def test_table_cells_span_width(self):
        html = "<body><table><tr><td>x</td><td>y</td></tr></table></body>"
        _, doc = render(html, width=300)
        table = _find_table(doc)
        right = max(c.x + c.width for c in table.children)
        left = min(c.x for c in table.children)
        self.assertGreater(right - left, 150)  # fills much of the width


class TestNewCSS(unittest.TestCase):
    def test_explicit_width_and_margin_auto(self):
        html = ("<body><div style='width:100px;margin:0 auto'>"
                "<p>hi</p></div></body>")
        _, doc = render(html, width=400)
        div = _find_by_tag(doc, "div")
        self.assertIsNotNone(div)
        self.assertEqual(int(div.width), 100)
        self.assertGreater(div.x, 100)  # centered, not flush left

    def test_text_decoration_none_removes_link_underline(self):
        html = ("<style>a{text-decoration:none}</style>"
                "<body><p><a href='#'>x</a></p></body>")
        _, doc = render(html)
        self.assertFalse(any(isinstance(c, DrawLine)
                             for c in doc.display_list))

    def test_hr_draws_a_line(self):
        _, doc = render("<body><p>a</p><hr><p>b</p></body>")
        self.assertTrue(any(isinstance(c, DrawLine) for c in doc.display_list))


class TestHitTest(unittest.TestCase):
    def test_hit_test_returns_href(self):
        tab = Tab(width=400)
        tab.load("data:text/html,<body><p><a href='about:version'>go</a></p></body>")
        cmd = next(c for c in tab.document.display_list
                   if getattr(c, "href", None))
        cx = (cmd.left + cmd.right) // 2
        cy = (cmd.top + cmd.bottom) // 2
        self.assertEqual(tab.hit_test(cx, cy), "about:version")
        self.assertIsNone(tab.hit_test(9999, 9999))

    def test_browser_click_navigates(self):
        b = Browser(400, 300)
        b.new_tab("data:text/html,<body><a href='about:version'>v</a></body>")
        cmd = next(c for c in b.tab.document.display_list
                   if getattr(c, "href", None))
        cx = (cmd.left + cmd.right) // 2
        cy = (cmd.top + cmd.bottom) // 2 + CHROME_HEIGHT
        self.assertTrue(b.click(cx, cy))
        self.assertIn("version", str(b.tab.url))


def _find_table(doc):
    def walk(box):
        if isinstance(box, TableLayout):
            return box
        for child in box.children:
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(doc)


def _find_by_tag(doc, tag):
    def walk(box):
        node = getattr(box, "node", None)
        if isinstance(node, Element) and node.tag == tag:
            return box
        for child in box.children:
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(doc)


if __name__ == "__main__":
    unittest.main()
