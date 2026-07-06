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
from .paint import (DrawImage, DrawLine, DrawRect, DrawRectOutline, DrawText,
                    PaintCommand)
from .raster import RGB, parse_color

# Elements that flow inline but are laid out as a single atomic box.
_ATOMIC_INLINE = {"img", "input", "button", "textarea", "select"}
_FORM_WIDGETS = {"input", "button", "textarea", "select"}

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


def _length(value: str, percent_basis: float):
    """Parse a CSS length (``px`` or ``%``) into pixels, or None."""
    if not value:
        return None
    value = value.strip().lower()
    try:
        if value.endswith("px"):
            return float(value[:-2])
        if value.endswith("%"):
            return percent_basis * float(value[:-1]) / 100.0
        return float(value)
    except ValueError:
        return None


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
        self._apply_explicit_width()

        # Vertical start: below the previous sibling (margins, no collapsing).
        if self.previous is not None:
            self.y = (self.previous.y + self.previous.height
                      + self.previous.mb + self.mt)
        else:
            self.y = self.parent.content_top() + self.mt

        self._layout_contents()

    def _layout_contents(self) -> None:
        """Lay out this box's children/content given its geometry is set."""
        if self._mode() == "block":
            self._layout_block()
        else:
            self._layout_inline()
        # An explicit height only grows the box (content is never clipped).
        s = self.node.style if isinstance(self.node, Element) else {}
        if "height" in s:
            explicit_h = _length(s["height"], 0)
            if explicit_h is not None:
                self.height = max(self.height, explicit_h)

    def _apply_explicit_width(self) -> None:
        """Honour ``width`` and ``margin:auto`` centering on block boxes."""
        s = self.node.style if isinstance(self.node, Element) else {}
        avail = self.parent.content_width()
        raw = s.get("width")
        if raw:
            explicit = _length(raw, avail)
            if explicit is not None:
                explicit = min(explicit, avail - self.ml - self.mr)
                # margin:auto (or margin-left/right:auto) centers the box.
                if (s.get("margin") in ("auto", "0 auto")
                        or s.get("margin-left") == "auto"
                        or s.get("margin-right") == "auto"):
                    self.x = self.parent.content_x() + max(0, (avail - explicit) // 2)
                self.width = max(0, explicit)

    def _mode(self) -> str:
        node = self.node
        if isinstance(node, Text):
            return "inline"
        if isinstance(node, Element) and node.tag in _ATOMIC_INLINE:
            return "inline"  # replaced/widget elements flow like inline content
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
        # <hr> is an empty block that paints a horizontal rule.
        if isinstance(self.node, Element) and self.node.tag == "hr":
            y = int(self.content_top())
            x0 = int(self.content_x())
            x1 = int(self.content_x() + self.content_width())
            self.display_list.append(DrawLine(x0, y, x1, y, (170, 170, 170), 2))
            self.height = 2 + self.pb + self.bw
            return

        previous = None
        for child in self.node.children:
            if isinstance(child, Element) and child.style.get("display") == "none":
                continue
            if isinstance(child, Text) and child.text.isspace():
                continue
            if isinstance(child, Element) and (
                child.tag == "table" or child.style.get("display") == "table"):
                box = TableLayout(child, self, previous)
            else:
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
        self.line: List[dict] = []

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
            if node.tag == "img":
                self._image(node)
                return
            if node.tag in _FORM_WIDGETS:
                self._widget(node)
                return
        for child in node.children:
            self._recurse_inline(child)

    def _text(self, node: Text) -> None:
        font = _font_for(node)
        color = parse_color(node.style.get("color", "black"), (0, 0, 0))
        href = _link_href(node)
        decoration = node.style.get("text-decoration", "")
        underline = ("underline" in decoration
                     or (href is not None and "none" not in decoration))
        white_space = node.style.get("white-space", "normal")

        if white_space == "pre":
            for i, raw_line in enumerate(node.text.split("\n")):
                if i > 0:
                    self._flush_line()
                if raw_line:
                    self._place_word(raw_line, font, color, href, underline,
                                     space_after=False)
            return

        for word in node.text.split():
            self._place_word(word, font, color, href, underline, space_after=True)

    def _place_word(self, word: str, font: Font, color: RGB, href, underline: bool,
                    space_after: bool) -> None:
        w = font.measure(word)
        max_x = self.content_x() + self.content_width()
        if self.cursor_x + w > max_x and self.line:
            self._flush_line()
        self.line.append({"kind": "text", "x": self.cursor_x, "text": word,
                          "font": font, "color": color, "href": href,
                          "underline": underline, "w": w, "h": font.line_height})
        self.cursor_x += w
        if space_after:
            self.cursor_x += font.char_width  # one space between words

    def _image(self, node: Element) -> None:
        bitmap = getattr(node, "image", None)
        attr_w = _attr_px(node, "width")
        attr_h = _attr_px(node, "height")
        if bitmap is not None:
            w = attr_w or bitmap.width
            h = attr_h or bitmap.height
            if attr_w and not attr_h:      # preserve aspect ratio
                h = max(1, bitmap.height * attr_w // bitmap.width)
            elif attr_h and not attr_w:
                w = max(1, bitmap.width * attr_h // bitmap.height)
        else:
            w = attr_w or 120
            h = attr_h or 32
        # Clamp to the available content width.
        avail = int(self.content_width())
        if w > avail and avail > 0:
            if bitmap is not None:
                h = max(1, h * avail // w)
            w = avail
        max_x = self.content_x() + self.content_width()
        if self.cursor_x + w > max_x and self.line:
            self._flush_line()
        self.line.append({"kind": "image", "x": self.cursor_x, "w": w, "h": h,
                          "bitmap": bitmap, "href": _link_href(node),
                          "alt": node.attributes.get("alt", "")})
        self.cursor_x += w + 2

    def _widget(self, node: Element) -> None:
        """Lay out a form control (input/button/textarea/select) as one box."""
        font = _font_for(node)
        tag = node.tag
        typ = node.attributes.get("type", "text").lower() if tag == "input" else tag
        spec: dict = {"kind": "widget", "font": font, "node": node}

        if tag == "input" and typ in ("checkbox", "radio"):
            spec.update(subtype=typ, w=14, h=14,
                        checked="checked" in node.attributes)
        elif (tag == "input" and typ in ("submit", "reset", "button")) or tag == "button":
            label = (node.attributes.get("value")
                     or _element_text(node) or typ.capitalize())
            spec.update(subtype="button", label=label,
                        w=font.measure(label) + 16, h=font.line_height + 6)
        elif tag == "textarea":
            cols = _attr_int(node, "cols", 24)
            rows = _attr_int(node, "rows", 2)
            spec.update(subtype="textarea", text=_element_text(node),
                        w=cols * font.char_width + 10,
                        h=rows * font.line_height + 8)
        elif tag == "select":
            options = [c for c in node.children
                       if isinstance(c, Element) and c.tag == "option"]
            selected = next((o for o in options if "selected" in o.attributes),
                            options[0] if options else None)
            label = _element_text(selected) if selected is not None else ""
            spec.update(subtype="select", label=label,
                        w=font.measure(label) + 26, h=font.line_height + 6)
        else:  # text-like input
            value = node.attributes.get("value", "")
            placeholder = node.attributes.get("placeholder", "")
            size = _attr_int(node, "size", 20)
            if typ == "password":
                display, is_placeholder = "•" * len(value), False
            elif value:
                display, is_placeholder = value, False
            else:
                display, is_placeholder = placeholder, True
            spec.update(subtype="text", text=display, placeholder=is_placeholder,
                        w=size * font.char_width + 10, h=font.line_height + 6)

        w = min(int(spec["w"]), int(self.content_width()) or int(spec["w"]))
        spec["w"] = w
        max_x = self.content_x() + self.content_width()
        if self.cursor_x + w > max_x and self.line:
            self._flush_line()
        spec["x"] = self.cursor_x
        self.line.append(spec)
        self.cursor_x += w + 2

    def _paint_widget_item(self, px: int, py: int, item: dict) -> None:
        w, h = int(item["w"]), int(item["h"])
        font = item["font"]
        subtype = item["subtype"]
        if subtype in ("checkbox", "radio"):
            self.display_list.append(DrawRect(px, py, px + w, py + h, (255, 255, 255)))
            self.display_list.append(
                DrawRectOutline(px, py, px + w, py + h, (120, 120, 120), 1))
            if item.get("checked"):
                self.display_list.append(
                    DrawRect(px + 3, py + 3, px + w - 3, py + h - 3, (40, 110, 220)))
        elif subtype == "button":
            self.display_list.append(DrawRect(px, py, px + w, py + h, (225, 227, 231)))
            self.display_list.append(
                DrawRectOutline(px, py, px + w, py + h, (150, 153, 158), 1))
            self.display_list.append(
                DrawText(px + 8, py + 3, item["label"], font, (32, 33, 36)))
        else:  # text, textarea, select
            self.display_list.append(DrawRect(px, py, px + w, py + h, (255, 255, 255)))
            self.display_list.append(
                DrawRectOutline(px, py, px + w, py + h, (150, 153, 158), 1))
            text = item.get("text") or item.get("label", "")
            color = (150, 150, 150) if item.get("placeholder") else (32, 33, 36)
            max_chars = max(0, (w - 8) // font.char_width)
            self.display_list.append(
                DrawText(px + 4, py + 3, text[:max_chars], font, color))
            if subtype == "select":
                ay = py + h // 2
                self.display_list.append(
                    DrawText(px + w - 12, py + 3, "▼", Font(11), (90, 90, 90)))

    def _flush_line(self) -> None:
        if not self.line:
            self.cursor_x = self.content_x()
            return
        line_height = max(item["h"] for item in self.line)
        align = self.node.style.get("text-align", "left")
        last = self.line[-1]
        line_width = last["x"] + last["w"] - self.content_x()
        offset = 0.0
        avail = self.content_width()
        if align == "center":
            offset = max(0.0, (avail - line_width) / 2)
        elif align == "right":
            offset = max(0.0, avail - line_width)

        for item in self.line:
            px = int(item["x"] + offset)
            py = int(self.cursor_y)
            if item["kind"] == "text":
                font, color = item["font"], item["color"]
                self.display_list.append(
                    DrawText(px, py, item["text"], font, color, item["href"]))
                if item.get("underline"):
                    uy = py + font.ascent + 1
                    self.display_list.append(
                        DrawLine(px, uy, px + item["w"], uy, color, 1))
            elif item["kind"] == "widget":
                self._paint_widget_item(px, py, item)
            else:  # image
                self._paint_image_item(px, py, item)
        self.cursor_y += line_height
        self.cursor_x = self.content_x()
        self.line = []

    def _paint_image_item(self, px: int, py: int, item: dict) -> None:
        if item["bitmap"] is not None:
            self.display_list.append(
                DrawImage(px, py, item["bitmap"], item["w"], item["h"],
                          item["href"]))
        else:
            # Broken/unsupported image: a bordered placeholder with alt text.
            self.display_list.append(
                DrawRectOutline(px, py, px + item["w"], py + item["h"],
                                (150, 150, 150), 1))
            alt = item["alt"] or "[image]"
            font = Font(13)
            max_chars = max(1, (item["w"] - 6) // font.char_width)
            self.display_list.append(
                DrawText(px + 3, py + max(2, (item["h"] - font.line_height) // 2),
                         alt[:max_chars], font, (110, 110, 110), item["href"]))

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


class TableLayout(LayoutBox):
    """A simple auto-layout table: rows of cells arranged in a grid.

    Column widths are derived from each column's preferred (single-line)
    content width, scaled to fit the available width.  Each cell is laid out
    as its own block, the row height is the tallest cell, and cell borders
    come from the user-agent stylesheet.
    """

    CELL_SPACING = 0  # collapsed borders

    def layout(self) -> None:
        self._read_box()
        # A table always draws an outer border; set it before positioning so
        # the border thickness is included in the content box.
        if not self.bw:
            self.bw, self.bcolor = 1, (150, 150, 150)
        self.x = self.parent.content_x() + self.ml
        self.width = self.parent.content_width() - self.ml - self.mr
        if self.previous is not None:
            self.y = (self.previous.y + self.previous.height
                      + self.previous.mb + self.mt)
        else:
            self.y = self.parent.content_top() + self.mt

        rows = self._collect_rows()
        if not rows:
            self.height = 0
            return
        ncols = max(len(r) for r in rows)
        avail = int(self.content_width())
        col_widths = self._column_widths(rows, ncols, avail)

        cursor_y = self.content_top()
        for cells in rows:
            row_boxes = []
            cursor_x = self.content_x()
            for col, cell in enumerate(cells):
                cw = col_widths[col]
                box = BlockLayout(cell, self, None)
                box._read_box()
                box.x = cursor_x
                box.width = cw
                box.y = cursor_y
                box._layout_contents()
                row_boxes.append((box, cw))
                cursor_x += cw + self.CELL_SPACING
            row_height = max((b.height for b, _ in row_boxes), default=0)
            # Stretch every cell to the row height so borders line up.
            for box, cw in row_boxes:
                box.height = row_height
                self.children.append(box)
            cursor_y += row_height + self.CELL_SPACING

        self.height = (cursor_y - self.y) + self.pb + self.bw

    def _collect_rows(self):
        rows = []
        for node in self.node:
            if isinstance(node, Element) and node.tag == "tr":
                cells = [c for c in node.children
                         if isinstance(c, Element) and c.tag in ("td", "th")]
                if cells:
                    rows.append(cells)
        return rows

    def _column_widths(self, rows, ncols, avail):
        prefs = [1.0] * ncols
        for cells in rows:
            for col, cell in enumerate(cells):
                prefs[col] = max(prefs[col], _preferred_width(cell) + 14)
        total = sum(prefs)
        if total <= avail:
            # Distribute the slack so the table fills the available width.
            extra = (avail - total) / ncols
            widths = [int(p + extra) for p in prefs]
        else:
            scale = avail / total
            widths = [max(24, int(p * scale)) for p in prefs]
        # Fix rounding so the columns exactly span the available width.
        drift = avail - sum(widths)
        if widths:
            widths[-1] += drift
        return widths


def _preferred_width(node: Node) -> float:
    """Rough single-line content width of an element (for column sizing)."""
    total = 0.0
    for n in node:
        if isinstance(n, Text):
            font = _font_for(n)
            for word in n.text.split():
                total += font.measure(word) + font.char_width
        elif isinstance(n, Element) and n.tag == "img":
            total += (_attr_px(n, "width") or 120) + 2
    return total


def _collect(box: LayoutBox, out: List[PaintCommand]) -> None:
    """Depth-first flatten of every box's paint commands into one list.

    A box's background/border is emitted before its own content and before
    its children, so painting proceeds back-to-front.
    """
    out.extend(box.background_commands())
    out.extend(box.display_list)
    for child in box.children:
        _collect(child, out)


def _link_href(node: Node):
    """The href of the nearest ancestor ``<a href>``, or ``None``."""
    cur = node.parent
    while cur is not None:
        if (isinstance(cur, Element) and cur.tag == "a"
                and "href" in cur.attributes):
            return cur.attributes["href"]
        cur = cur.parent
    return None


def _element_text(node) -> str:
    """Concatenated text of an element's descendants (for labels/options)."""
    if node is None:
        return ""
    return "".join(n.text for n in node if isinstance(n, Text)).strip()


def _attr_int(node: Element, name: str, default: int) -> int:
    value = node.attributes.get(name, "").strip()
    return int(value) if value.isdigit() else default


def _attr_px(node: Element, name: str):
    """Read a pixel dimension from an HTML attribute (``width="120"``)."""
    value = node.attributes.get(name, "").strip()
    if value.endswith("px"):
        value = value[:-2]
    if value.isdigit():
        return int(value)
    return None


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
