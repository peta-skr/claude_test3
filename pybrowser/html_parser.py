"""A hand-written HTML parser producing a DOM tree.

This is a forgiving parser in the spirit of real browsers: it inserts
implicit ``<html>``/``<head>``/``<body>`` elements, auto-closes elements
that cannot nest, understands void (self-closing) elements, decodes a
useful subset of character references, and never raises on malformed
input.
"""

from __future__ import annotations

from typing import Dict, List

from .dom import Element, Node, Text

# Elements that never have children (no closing tag).
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# The document skeleton, in the order the tree builder expects it.
HEAD_TAGS = {"base", "link", "meta", "title", "style", "script"}

# Elements whose content is raw text (no nested markup): a "<" inside them is
# literal.  script/style keep entities verbatim; textarea/title decode them.
RAW_TEXT_ELEMENTS = {"script", "style", "textarea", "title"}

# Opening one of these implicitly closes an already-open element of the same
# tag (the "implied end tag" behaviour that lets ``<p>a<p>b`` produce two
# sibling paragraphs rather than nested ones).
IMPLIED_END = {
    "p": {"p"},
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "tr": {"tr"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "option": {"option"},
}

# A small but practical set of named character references.
ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    "nbsp": " ", "copy": "©", "reg": "®", "trade": "™",
    "mdash": "—", "ndash": "–", "hellip": "…",
    "laquo": "«", "raquo": "»", "middot": "·",
    "times": "×", "divide": "÷", "deg": "°",
    "larr": "←", "rarr": "→", "uarr": "↑", "darr": "↓",
    "bull": "•", "dagger": "†", "euro": "€", "pound": "£",
    "yen": "¥", "cent": "¢", "sect": "§", "para": "¶",
    "ldquo": '"', "rdquo": '"', "lsquo": "'", "rsquo": "'",
    "hyphen": "-", "minus": "-",
    "frac12": "1/2", "frac14": "1/4", "frac34": "3/4",
}


def decode_entities(text: str) -> str:
    """Replace ``&name;`` and ``&#123;`` / ``&#x1F;`` references."""
    if "&" not in text:
        return text
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        semi = text.find(";", i, i + 12)
        if semi == -1:
            out.append(ch)
            i += 1
            continue
        body = text[i + 1:semi]
        if body.startswith("#"):
            try:
                code = int(body[2:], 16) if body[1:2] in ("x", "X") else int(body[1:])
                out.append(chr(code))
                i = semi + 1
                continue
            except (ValueError, OverflowError):
                pass
        elif body in ENTITIES:
            out.append(ENTITIES[body])
            i = semi + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class HTMLParser:
    """Parse a string of HTML into a DOM tree rooted at an ``<html>`` element."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.unfinished: List[Element] = []

    def parse(self) -> Element:
        text_buffer: List[str] = []
        i = 0
        n = len(self.body)
        lower = self.body.lower()
        while i < n:
            c = self.body[i]
            if c != "<":
                text_buffer.append(c)
                i += 1
                continue
            gt = self.body.find(">", i)
            if gt == -1:
                text_buffer.append(c)
                i += 1
                continue
            if text_buffer:
                self.add_text("".join(text_buffer))
                text_buffer = []
            tag_source = self.body[i + 1:gt]
            if tag_source.startswith("!--"):
                end = self.body.find("-->", i)
                i = (end + 3) if end != -1 else gt + 1
                continue
            if tag_source.startswith("!"):
                i = gt + 1  # <!doctype ...> and similar declarations
                continue
            self.add_tag(tag_source)
            i = gt + 1
            # Raw-text elements (script/style/textarea/title) take their
            # content literally -- a "<" inside them is not a tag.
            open_el = self.unfinished[-1] if self.unfinished else None
            if (open_el is not None and open_el.tag in RAW_TEXT_ELEMENTS
                    and not tag_source.startswith("/")
                    and not tag_source.endswith("/")):
                tag = open_el.tag
                close = "</" + tag
                cidx = lower.find(close, i)
                raw = self.body[i:cidx] if cidx != -1 else self.body[i:]
                i = cidx if cidx != -1 else n
                if raw:
                    text = raw if tag in ("script", "style") else decode_entities(raw)
                    open_el.children.append(Text(text, open_el))
        if text_buffer:
            self.add_text("".join(text_buffer))
        return self.finish()

    # -- tree construction helpers -------------------------------------

    def add_text(self, text: str) -> None:
        if text.isspace():
            # Collapse whitespace-only runs between block tags but keep a
            # single space so inline text stays separated.
            if not self.unfinished:
                return
        parent = self.unfinished[-1] if self.unfinished else None
        if parent is None:
            return
        node = Text(decode_entities(text), parent)
        parent.children.append(node)

    def add_tag(self, tag_source: str) -> None:
        tag, attributes = self._parse_tag(tag_source)
        if tag.startswith("/"):
            self._close_tag(tag[1:])
            return
        self._implicit_tags(tag)
        # Auto-close an open element that this tag is not allowed to nest in.
        if tag in IMPLIED_END and self.unfinished:
            if self.unfinished[-1].tag in IMPLIED_END[tag]:
                node = self.unfinished.pop()
                if self.unfinished:
                    self.unfinished[-1].children.append(node)
        if tag in VOID_ELEMENTS or tag_source.endswith("/"):
            parent = self.unfinished[-1] if self.unfinished else None
            self.unfinished_append_void(Element(tag, attributes, parent))
        else:
            parent = self.unfinished[-1] if self.unfinished else None
            self.unfinished.append(Element(tag, attributes, parent))

    def unfinished_append_void(self, node: Element) -> None:
        if self.unfinished:
            self.unfinished[-1].children.append(node)
        else:
            self.unfinished.append(node)

    def _close_tag(self, tag: str) -> None:
        if len(self.unfinished) <= 1:
            # Never pop the root; ignore stray closing tags.
            if len(self.unfinished) == 1 and self.unfinished[0].tag == tag:
                return
            return
        # Auto-close mis-nested tags up to the matching open element.
        for depth in range(len(self.unfinished) - 1, -1, -1):
            if self.unfinished[depth].tag == tag:
                while len(self.unfinished) - 1 > depth:
                    node = self.unfinished.pop()
                    self.unfinished[-1].children.append(node)
                node = self.unfinished.pop()
                if self.unfinished:
                    self.unfinished[-1].children.append(node)
                return
        # Unmatched close tag: ignore.

    def _implicit_tags(self, tag: str) -> None:
        """Insert html/head/body when the document omits them."""
        while True:
            open_tags = [node.tag for node in self.unfinished]
            if not open_tags and tag != "html":
                self.unfinished.append(Element("html", {}, None))
            elif open_tags == ["html"] and tag not in ("head", "body", "/html"):
                if tag in HEAD_TAGS:
                    self.unfinished.append(
                        Element("head", {}, self.unfinished[-1])
                    )
                else:
                    self.unfinished.append(
                        Element("body", {}, self.unfinished[-1])
                    )
            elif (
                open_tags == ["html", "head"]
                and tag not in ("/head",) + tuple(HEAD_TAGS)
            ):
                node = self.unfinished.pop()
                self.unfinished[-1].children.append(node)
            else:
                break

    def _parse_tag(self, text: str) -> tuple[str, Dict[str, str]]:
        text = text.strip()
        if text.endswith("/"):
            text = text[:-1].strip()
        parts = self._split_attrs(text)
        if not parts:
            return "", {}
        tag = parts[0].lower()
        attributes: Dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                value = value.strip()
                if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                    value = value[1:-1]
                attributes[key.lower()] = decode_entities(value)
            elif part:
                attributes[part.lower()] = ""
        return tag, attributes

    @staticmethod
    def _split_attrs(text: str) -> List[str]:
        """Split a tag body on whitespace, respecting quoted attribute values."""
        parts: List[str] = []
        current: List[str] = []
        quote = ""
        for ch in text:
            if quote:
                current.append(ch)
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
                current.append(ch)
            elif ch.isspace():
                if current:
                    parts.append("".join(current))
                    current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current))
        return parts

    def finish(self) -> Element:
        if not self.unfinished:
            self.unfinished.append(Element("html", {}, None))
        while len(self.unfinished) > 1:
            node = self.unfinished.pop()
            self.unfinished[-1].children.append(node)
        return self.unfinished[0]


def parse_html(source: str) -> Element:
    """Convenience wrapper: parse ``source`` and return the root element."""
    return HTMLParser(source).parse()
