"""The browser application layer: tabs, history, and window chrome.

A :class:`Tab` owns one page's pipeline (fetch -> parse -> style -> layout
-> paint) plus its own back/forward history and scroll position.  A
:class:`Browser` owns several tabs and paints a Chrome-like top bar (tab
strip + toolbar + omnibox) above the active tab's content.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .css import CSSParser, default_rules, extract_stylesheets, style
from .dom import Element, Node, Text
from .fonts import Font
from .html_parser import parse_html
from .layout import DocumentLayout, collect_links
from .paint import paint_visible
from .raster import Canvas
from .url import URL, URLError

# Chrome geometry.
TABBAR_HEIGHT = 30
TOOLBAR_HEIGHT = 36
CHROME_HEIGHT = TABBAR_HEIGHT + TOOLBAR_HEIGHT
SCROLL_STEP = 60

# Chrome colours.
C_CHROME_BG = (222, 225, 230)
C_TAB_ACTIVE = (255, 255, 255)
C_TAB_INACTIVE = (200, 204, 210)
C_TAB_TEXT = (32, 33, 36)
C_OMNIBOX_BG = (255, 255, 255)
C_OMNIBOX_BORDER = (170, 175, 182)
C_BUTTON = (95, 99, 104)
C_BUTTON_DISABLED = (190, 193, 198)


class Tab:
    """A single browsing context with its own history and scroll offset."""

    def __init__(self, width: int = 800, session=None) -> None:
        from .session import Session

        self.width = width
        self.session = session if session is not None else Session()
        self.url: Optional[URL] = None
        self.root: Optional[Node] = None
        self.document: Optional[DocumentLayout] = None
        self.scroll = 0
        self.title = "New Tab"
        self.status = ""
        self.links: List[Tuple[str, str]] = []
        self.history: List[URL] = []
        self.future: List[URL] = []
        self.javascript_enabled = True
        self.js_console: List[str] = []

    # -- navigation ----------------------------------------------------

    def load(self, url, record: bool = True) -> None:
        if isinstance(url, str):
            url = URL(url)
        try:
            _headers, body = url.request(session=self.session)
            self.status = ""
        except Exception as exc:  # noqa: BLE001 - a bad page must never crash the UI
            body = _error_page(url, exc)
            self.status = f"Error: {exc}"

        if record and self.url is not None:
            self.history.append(self.url)
            self.future.clear()
        self.url = url
        self._render_pipeline(body)

    def _render_pipeline(self, body: str) -> None:
        self.root = parse_html(body)
        if self.javascript_enabled:
            self._run_scripts(self.root)
        rules = default_rules() + CSSParser(extract_stylesheets(self.root)).parse()
        style(self.root, rules)
        self._load_images(self.root)
        self.document = DocumentLayout(self.root, self.width)
        self.document.layout()
        self.links = collect_links(self.root)
        self.title = _document_title(self.root) or (
            self.url.host if self.url and self.url.scheme in ("http", "https")
            else str(self.url))
        self.scroll = 0

    def _run_scripts(self, root: Node) -> None:
        """Execute inline and external ``<script>`` elements in order."""
        from .js import Interpreter
        from .js_dom import Document, _Namespace

        scripts = [n for n in root
                   if isinstance(n, Element) and n.tag == "script"]
        if not scripts:
            return
        document = Document(root, None)
        window = _Namespace({"document": document})
        interp = Interpreter({"document": document, "window": window})
        self._install_window_globals(interp, window)
        for node in scripts:
            code = self._script_source(node)
            if not code.strip():
                continue
            try:
                interp.run(code)
            except Exception as exc:  # noqa: BLE001 - one bad script must not kill the page
                self.js_console.append(f"[script error] {type(exc).__name__}: {exc}")
        self.js_console.extend(interp.console_output)

    def _install_window_globals(self, interp, window) -> None:
        from .js import UNDEFINED, _bi, invoke, to_str
        g = interp.global_scope
        g.declare("alert", _bi(lambda *a: self.js_console.append(
            "[alert] " + " ".join(to_str(x) for x in a)) or UNDEFINED))
        # No event loop: timers fire immediately.
        g.declare("setTimeout", _bi(lambda fn=None, *_: (
            invoke(fn, []) if fn is not None else UNDEFINED)))
        g.declare("setInterval", _bi(lambda *a: UNDEFINED))

    @staticmethod
    def _script_source(node: Element) -> str:
        src = node.attributes.get("src", "").strip()
        if src:
            return ""  # external scripts are skipped in this offline engine
        return "".join(c.text for c in node.children if isinstance(c, Text))

    def _load_images(self, root: Node) -> None:
        """Fetch and decode every ``<img src>`` before layout runs."""
        from .image import decode_image

        cache: dict = {}
        for node in root:
            if not (isinstance(node, Element) and node.tag == "img"):
                continue
            src = node.attributes.get("src", "").strip()
            if not src:
                node.image = None
                continue
            if src in cache:
                node.image = cache[src]
                continue
            try:
                target = self.url.resolve(src) if self.url else URL(src)
                headers, data = target.request_bytes(timeout=15, session=self.session)
                bitmap = decode_image(data, headers.get("content-type", ""))
            except Exception:  # noqa: BLE001 - a broken image must not break layout
                bitmap = None
            cache[src] = bitmap
            node.image = bitmap

    def hit_test(self, x: int, y: int):
        """Return the link href at content-area coords ``(x, y)``, or None.

        ``y`` is relative to the top of the visible content viewport, so the
        current scroll offset is added to reach document coordinates.
        """
        if not self.document:
            return None
        doc_y = y + self.scroll
        for cmd in self.document.display_list:
            href = getattr(cmd, "href", None)
            if href is None:
                continue
            if cmd.left <= x <= cmd.right and cmd.top <= doc_y <= cmd.bottom:
                return href
        return None

    def go_back(self) -> bool:
        if not self.history:
            return False
        if self.url is not None:
            self.future.append(self.url)
        target = self.history.pop()
        self.url = None  # avoid re-recording
        self.load(target, record=False)
        self.url = target
        return True

    def go_forward(self) -> bool:
        if not self.future:
            return False
        if self.url is not None:
            self.history.append(self.url)
        target = self.future.pop()
        self.url = None
        self.load(target, record=False)
        self.url = target
        return True

    def reload(self) -> None:
        if self.url is not None:
            self.load(self.url, record=False)

    def follow_link(self, index: int) -> bool:
        if 0 <= index < len(self.links):
            href = self.links[index][0]
            target = self.url.resolve(href) if self.url else URL(href)
            self.load(target)
            return True
        return False

    # -- scrolling -----------------------------------------------------

    @property
    def content_height(self) -> int:
        return int(self.document.height) if self.document else 0

    def scroll_by(self, dy: int, viewport_h: int) -> None:
        max_scroll = max(0, self.content_height - viewport_h)
        self.scroll = max(0, min(self.scroll + dy, max_scroll))

    # -- painting ------------------------------------------------------

    def paint(self, canvas: Canvas) -> None:
        if self.document is not None:
            paint_visible(self.document.display_list, self.scroll, canvas)

    def render_text(self, width_chars: int = 100) -> str:
        """Flatten the page to readable plain text (for the terminal UI)."""
        if not self.document:
            return ""
        from .paint import DrawText

        rows: dict = {}
        for cmd in self.document.display_list:
            if isinstance(cmd, DrawText):
                key = cmd.top // 4  # bucket nearby baselines onto one line
                rows.setdefault(key, []).append((cmd.left, cmd.text))
        lines = []
        for key in sorted(rows):
            words = sorted(rows[key], key=lambda t: t[0])
            # Indent proportional to the leftmost word; join words with a
            # single space (pixel widths vary by font, so column-perfect
            # reconstruction reads worse than clean spacing).
            indent = min(8, words[0][0] // 12)
            line = " " * indent + " ".join(text for _, text in words)
            lines.append(line.rstrip())
        return "\n".join(lines)


class Browser:
    """Owns a set of tabs and renders the surrounding window chrome."""

    def __init__(self, width: int = 800, height: int = 600) -> None:
        from .session import Session

        self.width = width
        self.height = height
        self.tabs: List[Tab] = []
        self.active = 0
        self.session = Session()  # cookies + cache shared by all tabs

    @property
    def content_height(self) -> int:
        return self.height - CHROME_HEIGHT

    @property
    def tab(self) -> Tab:
        if not self.tabs:
            self.new_tab("about:home")
        return self.tabs[self.active]

    def new_tab(self, url: str = "about:blank") -> Tab:
        tab = Tab(width=self.width, session=self.session)
        tab.load(url)
        self.tabs.append(tab)
        self.active = len(self.tabs) - 1
        return tab

    def close_tab(self, index: Optional[int] = None) -> None:
        if index is None:
            index = self.active
        if 0 <= index < len(self.tabs):
            self.tabs.pop(index)
            self.active = max(0, min(self.active, len(self.tabs) - 1))

    def switch_tab(self, index: int) -> None:
        if 0 <= index < len(self.tabs):
            self.active = index

    def navigate(self, url: str) -> None:
        self.tab.load(url)

    def scroll_by(self, dy: int) -> None:
        self.tab.scroll_by(dy, self.content_height)

    def link_at(self, x: int, y: int):
        """Link href at full-window screen coords ``(x, y)`` (chrome-aware)."""
        if y < CHROME_HEIGHT:
            return None
        return self.tab.hit_test(x, y - CHROME_HEIGHT)

    def click(self, x: int, y: int) -> bool:
        """Follow the link under a screen-space click; True if navigated."""
        href = self.link_at(x, y)
        if href is None:
            return False
        target = self.tab.url.resolve(href) if self.tab.url else href
        self.tab.load(target)
        return True

    # -- screenshot ----------------------------------------------------

    def screenshot(self) -> Canvas:
        """Render chrome + active tab content into a single canvas."""
        canvas = Canvas(self.width, self.height, (255, 255, 255))
        self._paint_chrome(canvas)
        content = Canvas(self.width, self.content_height, (255, 255, 255))
        self.tab.paint(content)
        canvas.blit(content, 0, CHROME_HEIGHT)
        return canvas

    def _paint_chrome(self, canvas: Canvas) -> None:
        canvas.fill_rect(0, 0, self.width, CHROME_HEIGHT, C_CHROME_BG)
        self._paint_tabstrip(canvas)
        self._paint_toolbar(canvas)

    def _paint_tabstrip(self, canvas: Canvas) -> None:
        font = Font(13)
        x = 4
        tab_w = 150
        for i, tab in enumerate(self.tabs):
            active = i == self.active
            color = C_TAB_ACTIVE if active else C_TAB_INACTIVE
            canvas.fill_rect(x, 4, tab_w, TABBAR_HEIGHT - 4, color)
            canvas.draw_rect_outline(x, 4, tab_w, TABBAR_HEIGHT - 4,
                                     (175, 178, 184))
            title = tab.title or "New Tab"
            max_chars = (tab_w - 26) // font.char_width
            if len(title) > max_chars:
                title = title[:max(0, max_chars - 1)] + "…" if max_chars > 1 else ""
            canvas.draw_text(x + 8, 11, title, font, C_TAB_TEXT)
            # Close glyph.
            canvas.draw_text(x + tab_w - 14, 11, "x", font, (120, 123, 128))
            x += tab_w + 3
            if x + tab_w > self.width:
                break
        # "+" new-tab affordance.
        canvas.draw_text(x + 4, 10, "+", Font(15), C_BUTTON)

    def _paint_toolbar(self, canvas: Canvas) -> None:
        y = TABBAR_HEIGHT
        font = Font(15)
        tab = self.tab
        # Navigation buttons: back, forward, reload.
        back_color = C_BUTTON if tab.history else C_BUTTON_DISABLED
        fwd_color = C_BUTTON if tab.future else C_BUTTON_DISABLED
        canvas.draw_text(12, y + 10, "<", font, back_color)
        canvas.draw_text(34, y + 10, ">", font, fwd_color)
        canvas.draw_text(56, y + 10, "R", Font(13), C_BUTTON)
        # Omnibox.
        box_x = 80
        box_w = self.width - box_x - 12
        canvas.fill_rect(box_x, y + 6, box_w, TOOLBAR_HEIGHT - 12, C_OMNIBOX_BG)
        canvas.draw_rect_outline(box_x, y + 6, box_w, TOOLBAR_HEIGHT - 12,
                                 C_OMNIBOX_BORDER)
        url_text = str(tab.url) if tab.url else ""
        obfont = Font(13)
        max_chars = (box_w - 16) // obfont.char_width
        if len(url_text) > max_chars:
            url_text = url_text[:max(0, max_chars - 1)] + "…"
        # Small "lock" hint for https.
        prefix_x = box_x + 8
        if tab.url and tab.url.scheme == "https":
            canvas.draw_text(prefix_x, y + 12, "*", obfont, (26, 137, 23))
            prefix_x += obfont.char_width + 2
        canvas.draw_text(prefix_x, y + 12, url_text, obfont, (32, 33, 36))


def _document_title(root: Node) -> Optional[str]:
    for node in root:
        if isinstance(node, Element) and node.tag == "title":
            text = "".join(c.text for c in node if isinstance(c, Text)).strip()
            if text:
                return text
    return None


def _error_page(url, exc: Exception) -> str:
    return (
        "<!doctype html><html><head><title>Problem loading page</title></head>"
        "<body><h1>This page isn't working</h1>"
        f"<p>pybrowser could not load <b>{url}</b>.</p>"
        f"<p>Reason: {type(exc).__name__}: {exc}</p></body></html>"
    )
