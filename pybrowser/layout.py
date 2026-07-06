"""A block + inline layout engine implementing a useful slice of CSS flow.

The layout tree mirrors the DOM but only for rendered content.  Two layout
modes exist, chosen per element:

* **block**  -- children are stacked vertically (``<div>``, ``<p>``, ...)
* **inline** -- text and inline elements flow into wrapped lines

Boxes carry margins, padding and (optional) borders.  The engine produces a
flat display list of :mod:`pybrowser.paint` commands.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .dom import Element, Node, Text
from .fonts import Font
from .paint import DrawLine, DrawRect, DrawRectOutline, DrawText, PaintCommand
from .raster import RGB, parse_color

BLOCK_ELEMENTS = {
    "html", "body", "article", "section", "nav", "aside", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "footer", "address", "p", "hr", "pre",
    "blockquote", "ol", "ul", "li", "dl", "dt", "dd", "figure", "figcaption",
    "main", "div", "table", "form", "fieldset", "legend", "details", "summary",
}


def _px(style: dict, prop: str, default: float = 0.0) -> float:
    value = style.get(prop)
    if not value:
        return default
    value = value.strip()
    if value.endswith("px"):
        value = value[:-2]
    try:
        return float(value)
    except ValueError:
        return default


def _font_for(node: Node) -> Font:
    size = _px(node.style, "font-size", 16.0)
    weight = node.style.get("font-weight", "normal")
    slant = node.style.get("font-style", "normal")
    return Font(size, weight, slant)


def _border(style: dict) -> Tuple[int, Optional[RGB]]:
    """Return (thickness, color) parsed from ``border`` / ``border-*``."""
    shorthand = style.get("border")
    color = None
    width = 0.0
    if shorthand:
        for token in shorthand.split():
            low = token.lower()
            if low.endswith("px") and low[:-2].replace(".", "").isdigit():
                width = float(low[:-2])
            elif low in ("solid", "dashed", "dotted", "double", "none"):
                if low != "none":
                    width = width or 1.0
            else:
                color = parse_color(token, (0, 0, 0))
    if "border-width" in style:
        width = _px(style, "border-width", width)
    if "border-color" in style:
        color = parse_color(style["border-color"], (0, 0, 0))
    if width and color is None:
        color = (0, 0, 0)
    return int(width), color


class LayoutBox:
    """Common geometry for both block and inline boxes."""

    def __init__(self, node: Node, parent: Optional["LayoutBox"],
                 previous: Optional["LayoutBox"]) -> None:
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children: List["LayoutBox"] = []
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0
        self.ml = self.mr = self.mt = self.mb = 0.0
        self.pl = self.pr = self.pt = self.pb = 0.0
        self.bw = 0
        self.bcolor: Optional[RGB] = None
        self.display_list: List[PaintCommand] = []

    def content_x(self) -> float:
        return self.x + self.pl + self.bw

    def content_width(self) -> float:
        return max(0.0, self.width - self.pl - self.pr - 2 * self.bw)

    def content_top(self) -> float:
        return self.y + self.pt + self.bw

    def _read_box(self) -> None:
        s = self.node.style if isinstance(self.node, Element) else {}
        self.mt = _px(s, "margin-top", 0)
        self.mb = _px(s, "margin-bottom", 0)
        self.ml = _px(s, "margin-left", 0)
        self.mr = _px(s, "margin-right", 0)
        self.pt = _px(s, "padding-top", 0)
        self.pb = _px(s, "padding-bottom", 0)
        self.pl = _px(s, "padding-left", 0)
        self.pr = _px(s, "padding-right", 0)
        self.bw, self.bcolor = _border(s)

    def background_commands(self) -> List[PaintCommand]:
        """Backgrounds/borders, returned so they paint *behind* the content."""
        cmds: List[PaintCommand] = []
        s = self.node.style if isinstance(self.node, Element) else {}
        bg = s.get("background-color") or s.get("background")
        if bg:
            color = parse_color(bg.split()[0], None)
            if color is not None and color != (255, 255, 255):
                cmds.append(
                    DrawRect(int(self.x), int(self.y), int(self.x + self.width),
                             int(self.y + self.height), color))
        if self.bw and self.bcolor:
            cmds.append(
                DrawRectOutline(int(self.x), int(self.y),
                                int(self.x + self.width),
                                int(self.y + self.height), self.bcolor, self.bw))
        return cmds


class DocumentLayout(LayoutBox):
    """The root of the layout tree; establishes the viewport width."""

    def __init__(self, node: Node, width: int, hstep: int = 8, vstep: int = 8):
        super().__init__(node, None, None)
        self.viewport_width = width
        self.hstep = hstep
        self.vstep = vstep

    def layout(self) -> None:
        child = BlockLayout(self.node, self, None)
        self.width = self.viewport_width
        self.x = 0
        self.y = 0
        self.children = [child]
        child.layout()
        self.height = child.y + child.height + child.mb
        self.display_list = []
        _collect(child, self.display_list)


class BlockLayout(LayoutBox):
    def layout(self) -> None:
        self._read_box()

        # Horizontal geometry from the parent's content box.
        self.x = self.parent.content_x() + self.ml
        self.width = self.parent.content_width() - self.ml - self.mr

        # Vertical start: below the previous sibling (margins, no collapsing).
        if self.previous is not None:
            self.y = (self.previous.y + self.previous.height
                      + self.previous.mb + self.mt)
        else:
            self.y = self.parent.content_top() + self.mt

        if self._mode() == "block":
            self._layout_block()
        else:
            self._layout_inline()

    def _mode(self) -> str:
        node = self.node
        if isinstance(node, Text):
            return "inline"
        if node.style.get("display") == "none":
            return "block"  # zero children below
        for child in node.children:
            if isinstance(child, Element) and (
                child.tag in BLOCK_ELEMENTS
                or child.style.get("display") == "block"
            ):
                return "block"
        if node.children:
            return "inline"
        return "block"

    def _layout_block(self) -> None:
        previous: Optional[BlockLayout] = None
        for child in self.node.children:
            if isinstance(child, Element) and child.style.get("display") == "none":
                continue
            if isinstance(child, Text) and child.text.isspace():
                continue
            box = BlockLayout(child, self, previous)
            self.children.append(box)
            box.layout()
            previous = box

        # List item marker.
        self._maybe_marker()

        content_bottom = self.content_top()
        if self.children:
            last = self.children[-1]
            content_bottom = last.y + last.height + last.mb
        self.height = (content_bottom - self.y) + self.pb + self.bw

    def _layout_inline(self) -> None:
        self.cursor_x = self.content_x()
        self.cursor_y = self.content_top()
        self.line: List[Tuple[float, str, Font, RGB, bool]] = []
        self.line_start_x = self.content_x()

        self._maybe_marker(inline=True)
        self._recurse_inline(self.node)
        self._flush_line()

        self.height = (self.cursor_y - self.y) + self.pb + self.bw

    # -- inline helpers ------------------------------------------------

    def _recurse_inline(self, node: Node) -> None:
        if isinstance(node, Text):
            self._text(node)
            return
        if isinstance(node, Element):
            if node.style.get("display") == "none":
                return
            if node.tag == "br":
                self._flush_line()
                return
        for child in node.children:
            self._recurse_inline(child)

    def _text(self, node: Text) -> None:
        font = _font_for(node)
        color = parse_color(node.style.get("color", "black"), (0, 0, 0))
        is_link = _inside_link(node)
        white_space = node.style.get("white-space", "normal")

        if white_space == "pre":
            for i, raw_line in enumerate(node.text.split("\n")):
                if i > 0:
                    self._flush_line()
                if raw_line:
                    self._place_word(raw_line, font, color, is_link, space_after=False)
            return

        words = node.text.split()
        for word in words:
            self._place_word(word, font, color, is_link, space_after=True)

    def _place_word(self, word: str, font: Font, color: RGB, is_link: bool,
                    space_after: bool) -> None:
        w = font.measure(word)
        max_x = self.content_x() + self.content_width()
        if self.cursor_x + w > max_x and self.line:
            self._flush_line()
        self.line.append((self.cursor_x, word, font, color, is_link))
        self.cursor_x += w
        if space_after:
            self.cursor_x += font.char_width  # one space between words

    def _flush_line(self) -> None:
        if not self.line:
            self.cursor_x = self.content_x()
            return
        line_height = max(font.line_height for _, _, font, _, _ in self.line)
        align = self.node.style.get("text-align", "left")
        # Right/center alignment: shift the whole line.
        line_width = (self.line[-1][0]
                      + self.line[-1][2].measure(self.line[-1][1])
                      - self.content_x())
        offset = 0.0
        avail = self.content_width()
        if align == "center":
            offset = max(0.0, (avail - line_width) / 2)
        elif align == "right":
            offset = max(0.0, avail - line_width)

        for x, word, font, color, is_link in self.line:
            px = int(x + offset)
            py = int(self.cursor_y)
            self.display_list.append(DrawText(px, py, word, font, color))
            if is_link:
                underline_y = py + font.ascent + 1
                self.display_list.append(
                    DrawLine(px, underline_y, px + font.measure(word),
                             underline_y, color, 1))
        self.cursor_y += line_height
        self.cursor_x = self.content_x()
        self.line = []

    def _maybe_marker(self, inline: bool = False) -> None:
        if not isinstance(self.node, Element) or self.node.tag != "li":
            return
        font = _font_for(self.node)
        color = parse_color(self.node.style.get("color", "black"), (0, 0, 0))
        marker_x = int(self.x - font.char_width * 2)
        if marker_x < 0:
            marker_x = int(self.content_x())
        marker_y = int(self.content_top()) if not inline else int(self.cursor_y)
        self.display_list.append(DrawText(marker_x, marker_y, "•", font, color))


def _collect(box: LayoutBox, out: List[PaintCommand]) -> None:
    """Depth-first flatten of every box's paint commands into one list.

    A box's background/border is emitted before its own content and before
    its children, so painting proceeds back-to-front.
    """
    out.extend(box.background_commands())
    out.extend(box.display_list)
    for child in box.children:
        _collect(child, out)


def _inside_link(node: Node) -> bool:
    cur = node.parent
    while cur is not None:
        if isinstance(cur, Element) and cur.tag == "a":
            return True
        cur = cur.parent
    return False


def collect_links(root: Node) -> List[Tuple[str, str]]:
    """Return (href, text) for every ``<a href>`` in the document."""
    links: List[Tuple[str, str]] = []
    for node in root:
        if isinstance(node, Element) and node.tag == "a" and "href" in node.attributes:
            text = "".join(
                c.text for c in node if isinstance(c, Text)
            ).strip()
            links.append((node.attributes["href"], text))
    return links
