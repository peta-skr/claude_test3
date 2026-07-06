"""DOM node types produced by the HTML parser.

The DOM (Document Object Model) is a tree of nodes. Every node knows its
parent and its children, which lets the layout engine walk the tree in
document order.
"""

from __future__ import annotations

from typing import Dict, List, Optional


class Node:
    """Base class for every node in the document tree."""

    def __init__(self) -> None:
        self.parent: Optional["Node"] = None
        self.children: List["Node"] = []
        # Computed style, filled in by the CSS engine (css.style()).
        self.style: Dict[str, str] = {}

    def __iter__(self):
        """Depth-first pre-order traversal over this node and its subtree."""
        yield self
        for child in self.children:
            yield from child


class Text(Node):
    """A run of character data, e.g. the "Hello" in ``<p>Hello</p>``."""

    def __init__(self, text: str, parent: Optional[Node] = None) -> None:
        super().__init__()
        self.text = text
        self.parent = parent

    def __repr__(self) -> str:
        return f"Text({self.text!r})"


class Element(Node):
    """An HTML element such as ``<p>`` or ``<a href="...">``."""

    def __init__(
        self,
        tag: str,
        attributes: Optional[Dict[str, str]] = None,
        parent: Optional[Node] = None,
    ) -> None:
        super().__init__()
        self.tag = tag
        self.attributes = attributes or {}
        self.parent = parent

    def __repr__(self) -> str:
        attrs = "".join(f' {k}="{v}"' for k, v in self.attributes.items())
        return f"<{self.tag}{attrs}>"


def tree_to_string(node: Node, indent: int = 0) -> str:
    """Return a readable, indented dump of a node tree (used in tests/debug)."""
    lines = [" " * indent + repr(node)]
    for child in node.children:
        lines.append(tree_to_string(child, indent + 2))
    return "\n".join(lines)
