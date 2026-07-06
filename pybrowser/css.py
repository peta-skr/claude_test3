"""A small CSS engine: parser, selector matching, cascade and inheritance.

Supported selectors: type (``p``), class (``.note``), id (``#main``),
the universal selector (``*``), and descendant combinators
(``div p .x``).  Declarations are parsed into ``property: value`` pairs and
applied in cascade order (specificity, then source order), with a set of
inherited properties propagated down the tree.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .dom import Element, Node, Text

# Properties whose computed value is inherited by child elements.
INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-weight": "normal",
    "font-style": "normal",
    "color": "black",
    "text-align": "left",
    "line-height": "1.25",
    "white-space": "normal",
    # Not inherited in real CSS, but propagating it lets ``a { text-decoration:
    # none }`` reach the anchor's text nodes, which is what authors expect.
    "text-decoration": "",
}


class Selector:
    """Base selector type; subclasses implement matching and specificity."""

    priority: Tuple[int, int, int] = (0, 0, 0)

    def matches(self, node: Node) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class TagSelector(Selector):
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.priority = (0, 0, 0) if tag == "*" else (0, 0, 1)

    def matches(self, node: Node) -> bool:
        if self.tag == "*":
            return isinstance(node, Element)
        return isinstance(node, Element) and node.tag == self.tag

    def __repr__(self) -> str:
        return self.tag


class ClassSelector(Selector):
    def __init__(self, cls: str) -> None:
        self.cls = cls
        self.priority = (0, 1, 0)

    def matches(self, node: Node) -> bool:
        if not isinstance(node, Element):
            return False
        classes = node.attributes.get("class", "").split()
        return self.cls in classes

    def __repr__(self) -> str:
        return f".{self.cls}"


class IdSelector(Selector):
    def __init__(self, ident: str) -> None:
        self.ident = ident
        self.priority = (1, 0, 0)

    def matches(self, node: Node) -> bool:
        return isinstance(node, Element) and node.attributes.get("id") == self.ident

    def __repr__(self) -> str:
        return f"#{self.ident}"


class DescendantSelector(Selector):
    """``ancestor descendant`` -- match ``descendant`` inside ``ancestor``."""

    def __init__(self, ancestor: Selector, descendant: Selector) -> None:
        self.ancestor = ancestor
        self.descendant = descendant
        a = ancestor.priority
        d = descendant.priority
        self.priority = (a[0] + d[0], a[1] + d[1], a[2] + d[2])

    def matches(self, node: Node) -> bool:
        if not self.descendant.matches(node):
            return False
        parent = node.parent
        while parent:
            if self.ancestor.matches(parent):
                return True
            parent = parent.parent
        return False

    def __repr__(self) -> str:
        return f"{self.ancestor!r} {self.descendant!r}"


def _parse_simple_selector(text: str) -> Optional[Selector]:
    text = text.strip()
    if not text:
        return None
    if text == "*":
        return TagSelector("*")
    if text.startswith("."):
        return ClassSelector(text[1:])
    if text.startswith("#"):
        return IdSelector(text[1:])
    # Compound like ``p.note`` -> treat leading tag then class/id via descendant?
    # Keep it simple: split on '.'/'#' and AND them via a compound wrapper.
    return _parse_compound_selector(text)


class CompoundSelector(Selector):
    """Several simple selectors that must all match the same element."""

    def __init__(self, parts: List[Selector]) -> None:
        self.parts = parts
        p = (0, 0, 0)
        for part in parts:
            p = (p[0] + part.priority[0], p[1] + part.priority[1], p[2] + part.priority[2])
        self.priority = p

    def matches(self, node: Node) -> bool:
        return all(part.matches(node) for part in self.parts)

    def __repr__(self) -> str:
        return "".join(repr(p) for p in self.parts)


def _parse_compound_selector(text: str) -> Optional[Selector]:
    parts: List[Selector] = []
    token = ""
    kind = "tag"

    def flush() -> None:
        nonlocal token, kind
        if not token:
            return
        if kind == "tag":
            parts.append(TagSelector(token.lower()))
        elif kind == "class":
            parts.append(ClassSelector(token))
        elif kind == "id":
            parts.append(IdSelector(token))
        token = ""

    for ch in text:
        if ch == ".":
            flush()
            kind = "class"
        elif ch == "#":
            flush()
            kind = "id"
        else:
            token += ch
    flush()
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return CompoundSelector(parts)


class CSSParser:
    """Parse a CSS stylesheet string into ``(selector, declarations)`` rules."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0

    def parse(self) -> List[Tuple[Selector, Dict[str, str]]]:
        rules: List[Tuple[Selector, Dict[str, str]]] = []
        self._strip_comments()
        while self.i < len(self.text):
            self._whitespace()
            if self.i >= len(self.text):
                break
            if self.text[self.i] == "@":
                self._skip_at_rule()
                continue
            try:
                selectors = self._selectors()
                self._literal("{")
                body = self._declarations()
                self._literal("}")
            except _ParseError:
                # Skip to the next rule so one bad rule can't kill the sheet.
                if not self._skip_to("}"):
                    break
                continue
            for selector in selectors:
                rules.append((selector, body))
        return rules

    def _strip_comments(self) -> None:
        out: List[str] = []
        i = 0
        text = self.text
        while i < len(text):
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                i = len(text) if end == -1 else end + 2
            else:
                out.append(text[i])
                i += 1
        self.text = "".join(out)

    def _whitespace(self) -> None:
        while self.i < len(self.text) and self.text[self.i].isspace():
            self.i += 1

    def _literal(self, ch: str) -> None:
        if self.i >= len(self.text) or self.text[self.i] != ch:
            raise _ParseError(f"expected {ch!r}")
        self.i += 1

    def _skip_to(self, ch: str) -> bool:
        while self.i < len(self.text):
            if self.text[self.i] == ch:
                self.i += 1
                return True
            self.i += 1
        return False

    def _skip_at_rule(self) -> None:
        # Handle ``@media {...}`` blocks and ``@import ...;`` statements simply.
        depth = 0
        while self.i < len(self.text):
            c = self.text[self.i]
            self.i += 1
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return
            elif c == ";" and depth == 0:
                return

    def _selectors(self) -> List[Selector]:
        start = self.i
        while self.i < len(self.text) and self.text[self.i] != "{":
            self.i += 1
        selector_text = self.text[start:self.i]
        result: List[Selector] = []
        for group in selector_text.split(","):
            selector = self._compile_selector(group.strip())
            if selector is not None:
                result.append(selector)
        if not result:
            raise _ParseError("no valid selector")
        return result

    @staticmethod
    def _compile_selector(text: str) -> Optional[Selector]:
        tokens = text.split()
        if not tokens:
            return None
        selector = _parse_simple_selector(tokens[0])
        if selector is None:
            return None
        for token in tokens[1:]:
            descendant = _parse_simple_selector(token)
            if descendant is None:
                return None
            selector = DescendantSelector(selector, descendant)
        return selector

    def _declarations(self) -> Dict[str, str]:
        body: Dict[str, str] = {}
        while self.i < len(self.text) and self.text[self.i] != "}":
            self._whitespace()
            if self.i < len(self.text) and self.text[self.i] == "}":
                break
            prop_start = self.i
            while self.i < len(self.text) and self.text[self.i] not in ":;}":
                self.i += 1
            if self.i >= len(self.text) or self.text[self.i] != ":":
                # Malformed declaration; skip to ';' or '}'.
                while self.i < len(self.text) and self.text[self.i] not in ";}":
                    self.i += 1
                if self.i < len(self.text) and self.text[self.i] == ";":
                    self.i += 1
                continue
            prop = self.text[prop_start:self.i].strip().lower()
            self.i += 1  # skip ':'
            val_start = self.i
            while self.i < len(self.text) and self.text[self.i] not in ";}":
                self.i += 1
            value = self.text[val_start:self.i].strip()
            if self.i < len(self.text) and self.text[self.i] == ";":
                self.i += 1
            if prop and value:
                body[prop] = value
        return body


class _ParseError(Exception):
    pass


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


def _expand_shorthands(decls: Dict[str, str]) -> Dict[str, str]:
    """Expand the handful of shorthands the layout engine understands."""
    out = dict(decls)
    if "font" in out:
        # e.g. "italic bold 16px" -> pick out the pieces we care about.
        for token in out["font"].split():
            low = token.lower()
            if low in ("italic", "oblique"):
                out.setdefault("font-style", "italic")
            elif low in ("bold", "bolder"):
                out.setdefault("font-weight", "bold")
            elif low.endswith("px") and low[:-2].isdigit():
                out.setdefault("font-size", low)
    return out


def cascade_priority(rule: Tuple[Selector, Dict[str, str]]) -> Tuple[int, int, int]:
    selector, _ = rule
    return selector.priority


def style(
    node: Node,
    rules: List[Tuple[Selector, Dict[str, str]]],
    parent: Optional[Node] = None,
) -> None:
    """Compute ``node.style`` from ``rules`` and recurse over children."""
    node.style = {}

    # 1. Inherited properties come from the parent (or the initial value).
    for prop, default in INHERITED_PROPERTIES.items():
        if parent is not None and prop in parent.style:
            node.style[prop] = parent.style[prop]
        else:
            node.style[prop] = default

    if isinstance(node, Element):
        # 2. Author rules, applied in ascending specificity/source order.
        for selector, body in sorted(rules, key=cascade_priority):
            if selector.matches(node):
                for prop, value in _expand_shorthands(body).items():
                    node.style[prop] = value

        # 3. Inline ``style="..."`` attribute wins over selector rules.
        inline = node.attributes.get("style")
        if inline:
            pairs = CSSParser("*{" + inline + "}")._declarations_from_inline()
            for prop, value in _expand_shorthands(pairs).items():
                node.style[prop] = value

    # 4. Resolve percentage/relative font sizes against the parent.
    _resolve_font_size(node, parent)

    for child in node.children:
        style(child, rules, node)


def _resolve_font_size(node: Node, parent: Optional[Node]) -> None:
    value = node.style.get("font-size", "16px")
    parent_px = 16.0
    if parent is not None:
        parent_px = _px(parent.style.get("font-size", "16px"), 16.0)
    if value.endswith("%"):
        try:
            node.style["font-size"] = f"{parent_px * float(value[:-1]) / 100:.1f}px"
        except ValueError:
            node.style["font-size"] = f"{parent_px:.1f}px"
    elif value.endswith("em"):
        try:
            node.style["font-size"] = f"{parent_px * float(value[:-2]):.1f}px"
        except ValueError:
            node.style["font-size"] = f"{parent_px:.1f}px"


def _px(value: str, default: float) -> float:
    value = value.strip()
    if value.endswith("px"):
        value = value[:-2]
    try:
        return float(value)
    except ValueError:
        return default


# A small helper so the inline-style path can reuse the declaration parser.
def _declarations_from_inline(self: "CSSParser") -> Dict[str, str]:  # noqa: N805
    self.i = self.text.index("{") + 1
    return self._declarations()


CSSParser._declarations_from_inline = _declarations_from_inline  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The default (user-agent) stylesheet
# ---------------------------------------------------------------------------

DEFAULT_STYLE_SHEET = """
html { display: block; }
body { display: block; margin: 8px; }
div, section, article, header, footer, nav, main, aside, figure { display: block; }
p { display: block; margin-top: 12px; margin-bottom: 12px; }
h1 { display: block; font-size: 32px; font-weight: bold; margin-top: 18px; margin-bottom: 18px; }
h2 { display: block; font-size: 24px; font-weight: bold; margin-top: 16px; margin-bottom: 16px; }
h3 { display: block; font-size: 20px; font-weight: bold; margin-top: 14px; margin-bottom: 14px; }
h4 { display: block; font-size: 17px; font-weight: bold; margin-top: 12px; margin-bottom: 12px; }
h5, h6 { display: block; font-weight: bold; margin-top: 10px; margin-bottom: 10px; }
ul, ol { display: block; margin-top: 8px; margin-bottom: 8px; padding-left: 30px; }
li { display: block; margin-top: 2px; margin-bottom: 2px; }
blockquote { display: block; margin-left: 30px; margin-top: 10px; margin-bottom: 10px; }
pre { display: block; white-space: pre; margin-top: 10px; margin-bottom: 10px; }
hr { display: block; margin-top: 8px; margin-bottom: 8px; }
b, strong { font-weight: bold; }
i, em { font-style: italic; }
a { color: #1a0dab; }
small { font-size: 13px; }
big { font-size: 20px; }
title, head, script, style, meta, link { display: none; }
table { display: table; margin-top: 8px; margin-bottom: 8px; }
td, th { padding-top: 4px; padding-bottom: 4px; padding-left: 6px; padding-right: 6px; border: 1px solid #bbbbbb; }
th { font-weight: bold; }
""".strip()


def default_rules() -> List[Tuple[Selector, Dict[str, str]]]:
    return CSSParser(DEFAULT_STYLE_SHEET).parse()


def extract_stylesheets(root: Node) -> str:
    """Collect the text of every ``<style>`` element in the document."""
    chunks: List[str] = []
    for node in root:
        if isinstance(node, Element) and node.tag == "style":
            for child in node.children:
                if isinstance(child, Text):
                    chunks.append(child.text)
    return "\n".join(chunks)
